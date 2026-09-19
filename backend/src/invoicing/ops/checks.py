"""启动自检与深度自检（运维兜底）：同一套检查函数，lifespan 启动跑一次，
/ops/status 随时重跑。level: ok / warn / fail / info。"""
import logging
import shutil
from pathlib import Path

from invoicing.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_SECRETS = {"jwt_secret": "change-me", "mcp_token": "change-me", "admin_password": "admin123"}


def _strict() -> bool:
    """阻断判定：显式 strict 或 production 形态（弱配置一律拒绝启动）。"""
    return settings.startup_checks_strict or settings.invoicing_env == "production"


def check_dirs_writable() -> tuple[str, str]:
    for attr in ("storage_root", "log_dir", "ops_backup_dir"):
        d = Path(getattr(settings, attr))
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write-probe"
            probe.write_text("ok")
            probe.unlink()
        except Exception as exc:
            return "fail", f"目录不可写 {d}（{type(exc).__name__}）"
    return "ok", "存储/日志/备份目录可写"


def check_fernet_key() -> tuple[str, str]:
    from sqlalchemy import or_

    from invoicing.db import SessionLocal
    from invoicing.fetch.crypto import fernet_configured
    from invoicing.models import Mailbox

    if fernet_configured():
        return "ok", "fernet_key 有效"
    # 未配置/非法：查库里是否已有凭据密文——有则这些密文已无法解密（或重启即失效），
    # 升级为 fail（无论 strict）；无则只是「无法新增凭据」，维持 warn（开发环境友好）。
    ciphertext_hint = "凭据加密已拒绝，邮箱登录不可用"
    try:
        with SessionLocal() as db:
            n = (
                db.query(Mailbox)
                .filter(
                    or_(
                        Mailbox.password_encrypted.isnot(None),
                        Mailbox.smtp_password_encrypted.isnot(None),
                        Mailbox.agently_token_encrypted.isnot(None),
                    )
                )
                .count()
            )
        if n:
            return "fail", (
                f"fernet_key 未配置/非法：库中已有 {n} 条邮箱凭据密文无法解密——"
                "用 scripts/reencrypt_secrets.py 从旧 key 迁移，或重新录入凭据"
            )
    except Exception:
        logger.exception("fernet_key 自检查询密文失败，退回 warn 评估")
    msg = f"fernet_key 未配置/非法（需 44 字符 urlsafe base64 / 32 字节）：{ciphertext_hint}"
    return ("fail" if _strict() else "warn"), msg


def check_default_secrets() -> tuple[str, str]:
    hits = [k for k, v in _DEFAULT_SECRETS.items() if getattr(settings, k) == v]
    if not hits:
        return "ok", "安全配置已覆盖默认值"
    msg = f"仍为默认值: {','.join(hits)}（生产必须覆盖）"
    return ("fail" if _strict() else "warn"), msg


def check_db_migration() -> tuple[str, str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from invoicing.db import SessionLocal

    try:
        head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    except Exception:
        return "warn", "无法读取迁移脚本（alembic.ini 不在当前目录），跳过版本比对"
    try:
        with SessionLocal() as db:
            row = db.execute(text("SELECT version_num FROM alembic_version")).fetchone()
    except Exception:
        return "warn", "数据库无 alembic_version 表：请执行 alembic upgrade head"
    current = row[0] if row else None
    if current != head:
        return "warn", f"迁移落后（db={current} head={head}）：请执行 alembic upgrade head"
    return "ok", "迁移版本一致"


def check_disk_free(min_percent: int = 10) -> tuple[str, str]:
    anchor = Path(settings.storage_root).resolve().anchor or "/"
    usage = shutil.disk_usage(anchor)
    pct = 100 * usage.free / usage.total if usage.total else 100.0
    if pct < min_percent:
        return "warn", f"磁盘剩余 {pct:.1f}%（<{min_percent}%）"
    return "ok", f"磁盘剩余 {pct:.1f}%"


def check_engines() -> tuple[str, str]:
    parts = [f"LLM={'on' if settings.llm_enabled else 'off'}"]
    try:
        from invoicing.parse.ocr import get_ocr_provider

        parts.append(f"OCR={'on' if get_ocr_provider() else 'off'}")
    except Exception:
        parts.append("OCR=?")
    return "info", "能力现状： " + " ".join(parts)


def check_inbox_dir() -> tuple[str, str]:
    """MCP 文件级工具的路径信任边界（mcp/extract.py：非空才约束目录）。

    生产为空 = Agent 可读服务器任意路径，必须配置；开发环境维持宽松。
    """
    if settings.workbuddy_inbox_dir:
        return "ok", "workbuddy_inbox_dir 已配置"
    if settings.invoicing_env == "production":
        return "fail", "workbuddy_inbox_dir 未配置：MCP 文件级工具可读服务器任意路径，生产必须配置"
    return "info", "workbuddy_inbox_dir 未配置（开发环境允许；生产必须配置）"


def _llm_host_is_private(host: str) -> bool:
    import ipaddress

    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or host.endswith(".local") or host.endswith(".internal")
    return ip.is_private or ip.is_loopback


def check_offline_llm() -> tuple[str, str]:
    """离线部署（FRD：数据不出企业内网）声明与云端 LLM 端点互斥（R6）。"""
    if not settings.offline_deploy:
        return "info", "offline_deploy 未启用"
    if not settings.llm_enabled:
        return "ok", "离线部署：LLM 未启用"
    from urllib.parse import urlparse

    host = (urlparse(settings.llm_base_url).hostname or "").lower()
    if _llm_host_is_private(host):
        return "ok", f"离线部署：LLM 端点在内网（{host}）"
    return "fail", f"离线部署禁止云端 LLM（当前端点 {host}）——换内网模型或置 llm_enabled=false"


_CHECKS = [
    ("dirs_writable", check_dirs_writable),
    ("fernet_key", check_fernet_key),
    ("default_secrets", check_default_secrets),
    ("db_migration", check_db_migration),
    ("disk_free", check_disk_free),
    ("engines", check_engines),
    ("inbox_dir", check_inbox_dir),
    ("offline_llm", check_offline_llm),
]


def run_all_checks() -> list[dict]:
    return [{"name": name, "level": level, "message": msg}
            for name, fn in _CHECKS for level, msg in [fn()]]


def run_startup_checks() -> list[dict]:
    """启动时执行：打日志 + fail 项写 critical 告警；strict 且有 fail 抛 RuntimeError。"""
    from datetime import timedelta

    from invoicing.db import SessionLocal
    from invoicing.ops.alerts import record_alert

    results = run_all_checks()
    for r in results:
        log = {"fail": logger.error, "warn": logger.warning}.get(r["level"], logger.info)
        log("启动自检[%s] %s: %s", r["level"], r["name"], r["message"])
        if r["level"] == "fail":
            try:
                with SessionLocal() as db:
                    record_alert(db, f"startup.{r['name']}", "critical",
                                 f"{r['name']}: {r['message']}", cooldown=timedelta(hours=12))
            except Exception:
                logger.exception("自检告警写入失败")
    if _strict() and any(r["level"] == "fail" for r in results):
        raise RuntimeError("启动自检未通过（strict：startup_checks_strict=true 或 production 形态）：见自检日志")
    return results
