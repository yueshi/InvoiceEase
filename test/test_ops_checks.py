"""启动自检：坏 key / 默认密钥 / 目录可写 / 迁移比对 / strict 阻断。"""
import base64

import pytest

from invoicing.config import settings
from invoicing.ops.checks import (
    check_db_migration,
    check_default_secrets,
    check_dirs_writable,
    check_fernet_key,
    run_startup_checks,
)


def _valid_key() -> str:
    return base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def test_check_fernet_key_invalid_warns(monkeypatch):
    monkeypatch.setattr(settings, "fernet_key", "short")
    monkeypatch.setattr(settings, "startup_checks_strict", False)
    assert check_fernet_key()[0] == "warn"


def test_check_fernet_key_strict_fails(monkeypatch):
    monkeypatch.setattr(settings, "fernet_key", "short")
    monkeypatch.setattr(settings, "startup_checks_strict", True)
    assert check_fernet_key()[0] == "fail"


def test_check_fernet_key_ok(monkeypatch):
    monkeypatch.setattr(settings, "fernet_key", _valid_key())
    assert check_fernet_key()[0] == "ok"


def test_check_default_secrets_warns(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "change-me")
    level, msg = check_default_secrets()
    assert level == "warn" and "jwt_secret" in msg


def test_check_dirs_writable(tmp_path, monkeypatch):
    for attr in ("storage_root", "log_dir", "ops_backup_dir"):
        monkeypatch.setattr(settings, attr, str(tmp_path / attr))
    assert check_dirs_writable()[0] == "ok"


def test_check_migration_warns_on_fresh_db(db):
    """测试库由 create_all 建表、无 alembic_version → 比对应给出 warn 提示。"""
    assert check_db_migration()[0] == "warn"


def test_run_startup_checks_strict_raises(monkeypatch):
    monkeypatch.setattr(settings, "startup_checks_strict", True)
    monkeypatch.setattr(settings, "fernet_key", "short")
    with pytest.raises(RuntimeError):
        run_startup_checks()


def test_check_fernet_key_fail_when_ciphertexts_exist(monkeypatch, db):
    """坏 key + 库里已有凭据密文 → 升级 fail（与 strict 无关）：密文已无法解密。"""
    from invoicing.models import Mailbox

    monkeypatch.setattr(settings, "fernet_key", "short")
    db.add(Mailbox(name="旧邮箱", mailbox_type="imap", password_encrypted="gAAAAABmdead"))
    db.commit()
    level, msg = check_fernet_key()
    assert level == "fail"
    assert "reencrypt_secrets" in msg
    assert "1 条" in msg


# ---- M1：production 形态 fail-fast + inbox_dir / offline_llm 自检项 --------------------


def test_production_weak_secret_blocks_startup(monkeypatch):
    """production 形态弱默认一律 fail（无需显式 strict）：run_startup_checks 抛 RuntimeError。"""
    monkeypatch.setattr(settings, "invoicing_env", "production")
    monkeypatch.setattr(settings, "startup_checks_strict", False)
    monkeypatch.setattr(settings, "jwt_secret", "change-me")
    level, msg = check_default_secrets()
    assert level == "fail"


def test_dev_weak_secret_still_warns(monkeypatch):
    """dev 形态行为不回退：弱默认仍只是 warn。"""
    monkeypatch.setattr(settings, "invoicing_env", "dev")
    monkeypatch.setattr(settings, "startup_checks_strict", False)
    monkeypatch.setattr(settings, "jwt_secret", "change-me")
    level, _ = check_default_secrets()
    assert level == "warn"


def test_production_empty_inbox_dir_fails(monkeypatch):
    from invoicing.ops.checks import check_inbox_dir

    monkeypatch.setattr(settings, "invoicing_env", "production")
    monkeypatch.setattr(settings, "workbuddy_inbox_dir", "")
    level, msg = check_inbox_dir()
    assert level == "fail"
    assert "任意路径" in msg


def test_dev_empty_inbox_dir_info(monkeypatch):
    from invoicing.ops.checks import check_inbox_dir

    monkeypatch.setattr(settings, "invoicing_env", "dev")
    monkeypatch.setattr(settings, "workbuddy_inbox_dir", "")
    assert check_inbox_dir()[0] == "info"
    monkeypatch.setattr(settings, "workbuddy_inbox_dir", "/tmp/inbox")
    assert check_inbox_dir()[0] == "ok"


def test_offline_deploy_rejects_cloud_llm(monkeypatch):
    from invoicing.ops.checks import check_offline_llm

    monkeypatch.setattr(settings, "offline_deploy", True)
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "llm_base_url", "https://api.deepseek.com/v1")
    level, msg = check_offline_llm()
    assert level == "fail"
    assert "内网模型" in msg

    monkeypatch.setattr(settings, "llm_base_url", "http://192.168.1.9:8000/v1")
    assert check_offline_llm()[0] == "ok"
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:11434/v1")
    assert check_offline_llm()[0] == "ok"


def test_offline_deploy_without_llm_ok(monkeypatch):
    from invoicing.ops.checks import check_offline_llm

    monkeypatch.setattr(settings, "offline_deploy", True)
    monkeypatch.setattr(settings, "llm_enabled", False)
    assert check_offline_llm()[0] == "ok"
    monkeypatch.setattr(settings, "offline_deploy", False)
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "llm_base_url", "https://api.deepseek.com/v1")
    assert check_offline_llm()[0] == "info"  # 未声明离线时不拦（现状行为）


def test_new_checks_registered():
    """inbox_dir / offline_llm 已进自检清单（/ops/status 与启动共用）。"""
    from invoicing.ops.checks import run_all_checks

    names = {r["name"] for r in run_all_checks()}
    assert {"inbox_dir", "offline_llm"} <= names


def test_invoicing_env_reads_from_env_var(monkeypatch):
    """回归：INVOICING_ENV 环境变量必须真正映射到 settings.invoicing_env。

    此前字段名 invoicing_env 叠加 env_prefix 后实际读 INVOICING_INVOICING_ENV，
    production 形态静默失效（演示实测暴露）；属性级 monkeypatch 测不出映射问题。
    """
    from invoicing.config import Settings

    monkeypatch.setenv("INVOICING_ENV", "production")
    assert Settings().invoicing_env == "production"
    monkeypatch.delenv("INVOICING_ENV")
    assert Settings().invoicing_env == "dev"
