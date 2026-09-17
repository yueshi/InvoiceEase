"""备份（运维兜底）：SQLite 一致性快照 + 原件目录打包 + meta，保留轮转。

产物：{ops_backup_dir}/invoiceease-backup-YYYYMMDD-HHMMSS.tar.gz（内含
invoicing.db / originals.tar / meta.json）+ 同名 .meta.json sidecar（列表页免解包读）。
文件系统即事实源，不落表（设计 §3）。恢复见 backend/scripts/restore_backup.py。
"""
import json
import logging
import re
import shutil
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from invoicing.config import settings
from invoicing.models.fields import utcnow

logger = logging.getLogger(__name__)

BACKUP_NAME_RE = re.compile(r"^invoiceease-backup-\d{8}-\d{6}\.tar\.gz$")


def _db_file_path() -> Path | None:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        return Path(url.removeprefix("sqlite:///"))
    return None  # PG 备份属 Plan D 生产对齐，v1 明确不支持


def _collect_meta(db_snapshot: Path) -> dict:
    conn = sqlite3.connect(str(db_snapshot))
    try:
        def count(table: str) -> int | None:
            try:
                return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                return None

        try:
            version_row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            version = version_row[0] if version_row else None
        except sqlite3.OperationalError:
            version = None
        return {
            "created_at": utcnow().isoformat(),
            "alembic_version": version,
            "table_counts": {"invoices": count("invoices"),
                             "bank_receipts": count("bank_receipts"),
                             "expense_claims": count("expense_claims"),
                             "users": count("users")},
        }
    finally:
        conn.close()


def create_backup(target_dir: str | None = None) -> Path:
    out_dir = Path(target_dir or settings.ops_backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = _db_file_path()
    if db_path is None:
        raise RuntimeError("仅支持 SQLite 数据库备份（当前 database_url 非 sqlite）")
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    base = f"invoiceease-backup-{stamp}"
    work = out_dir / f".{base}.work"
    work.mkdir()
    try:
        snap = work / "invoicing.db"
        src = sqlite3.connect(str(db_path))
        # 业务持锁时（如夜间长事务）等待而非立即 transient 失败
        src.execute("PRAGMA busy_timeout=5000")
        try:
            dst = sqlite3.connect(str(snap))
            try:
                with dst:
                    src.backup(dst)  # 一致性快照，不阻塞业务写入
            finally:
                dst.close()
        finally:
            src.close()
        originals = Path(settings.storage_root)
        if originals.exists():
            with tarfile.open(work / "originals.tar", "w") as tf:
                tf.add(originals, arcname="originals")
        meta = _collect_meta(snap)
        (work / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tar_path = out_dir / f"{base}.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            for p in sorted(work.iterdir()):
                tf.add(p, arcname=p.name)
        (out_dir / f"{base}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        rotate_backups(out_dir)
        logger.info("备份完成 %s（invoices=%s）", tar_path.name, meta["table_counts"]["invoices"])
        return tar_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


def rotate_backups(out_dir: Path, retention: int | None = None) -> int:
    keep = retention if retention is not None else settings.ops_backup_retention
    tars = sorted(out_dir.glob("invoiceease-backup-*.tar.gz"))
    removed = 0
    for old in tars[:-keep] if len(tars) > keep else []:
        old.unlink(missing_ok=True)
        Path(str(old).removesuffix(".tar.gz") + ".meta.json").unlink(missing_ok=True)
        removed += 1
    return removed


def list_backups(out_dir: str | None = None) -> list[dict]:
    d = Path(out_dir or settings.ops_backup_dir)
    items = []
    for tar in sorted(d.glob("invoiceease-backup-*.tar.gz"), reverse=True):
        meta_path = Path(str(tar).removesuffix(".tar.gz") + ".meta.json")
        meta = None
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                # sidecar 损坏/缺失：降级 meta=None，列表照常返回，不抛异常
                logger.warning("备份 sidecar 解析失败，降级 meta=None: %s", meta_path.name,
                               exc_info=True)
        stat = tar.stat()
        items.append({
            "name": tar.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "meta": meta,
        })
    return items
