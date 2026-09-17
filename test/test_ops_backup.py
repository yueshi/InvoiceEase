"""备份：创建/内容/轮转/恢复 roundtrip。"""
# 顶部 import：与仓库测试惯例一致。
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from decimal import Decimal
from pathlib import Path

from invoicing.config import settings
from invoicing.models import Invoice
from invoicing.ops.backup import create_backup, list_backups, rotate_backups


def _seed_originals(tmp_path, monkeypatch):
    root = tmp_path / "originals"
    (root / "tenant-default").mkdir(parents=True)
    (root / "tenant-default" / "a.xml").write_text("<inv/>", encoding="utf-8")
    (root / "tenant-default" / "b.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(settings, "storage_root", str(root))
    monkeypatch.setattr(settings, "ops_backup_dir", str(tmp_path / "backups"))
    return root


def _seed_invoice(db) -> Invoice:
    """seed 一条可识别发票：后续备份/恢复断言统一以 invoice_number=SEED0001 为准。"""
    inv = Invoice(file_url="seed-inv.xml", file_type="XML",
                  invoice_number="SEED0001", total_amount=Decimal("100.00"))
    db.add(inv)
    db.commit()
    return inv


def test_create_backup_roundtrip(db, tmp_path, monkeypatch):
    _seed_originals(tmp_path, monkeypatch)
    _seed_invoice(db)
    tar_path = create_backup()
    assert tar_path.exists()
    items = list_backups()
    assert len(items) == 1 and items[0]["meta"] is not None

    # 解包校验：db 快照可打开且恰含 seed 发票、originals 完整、meta 有表计数
    work = tmp_path / "unpacked"
    work.mkdir()
    with tarfile.open(tar_path) as tf:
        tf.extractall(work)
    assert (work / "originals.tar").exists()
    with tarfile.open(work / "originals.tar") as tf:
        names = tf.getnames()
    assert any(n.endswith("a.xml") for n in names)
    conn = sqlite3.connect(str(work / "invoicing.db"))
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1, "快照应恰含 1 条 seed 发票"
    row = conn.execute(
        "SELECT invoice_number FROM invoices WHERE invoice_number='SEED0001'").fetchone()
    assert row is not None, "快照应含备份前的 seed 发票"
    conn.close()
    assert "table_counts" in json.loads((work / "meta.json").read_text(encoding="utf-8"))


def test_rotate_keeps_latest(db, tmp_path, monkeypatch):
    _seed_originals(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "ops_backup_retention", 2)
    for _ in range(3):
        create_backup()
        time.sleep(1.1)  # 文件名秒级时间戳，错开
    out = tmp_path / "backups"
    assert len(list(out.glob("invoiceease-backup-*.tar.gz"))) == 2
    assert rotate_backups(out) == 0  # 已在 create_backup 内轮转过


def test_create_backup_non_sqlite_raises(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql://x/y")
    try:
        create_backup()
        assert False, "应抛 RuntimeError"
    except RuntimeError as e:
        assert "SQLite" in str(e)


def test_restore_roundtrip(db, tmp_path, monkeypatch):
    """restore_backup.py 自动化：seed→备份→变更→恢复，数据/库/原件均还原。

    全程隔离在 tmp_path：只碰临时 db 副本，绝不碰 invoicing_test.db 与 ./data。
    """
    _seed_originals(tmp_path, monkeypatch)
    _seed_invoice(db)

    # 隔离 db：把已 seed 的测试库复制一份到 tmp_path，restore 只动这份副本
    src_db = Path(settings.database_url.removeprefix("sqlite:///"))
    iso_db = tmp_path / "iso.db"
    shutil.copy2(src_db, iso_db)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{iso_db}")

    tar_path = create_backup()
    assert tar_path.exists()

    # 模拟恢复前状态漂移：库里注入 junk 表、原件目录被污染
    con = sqlite3.connect(str(iso_db))
    con.execute("CREATE TABLE junk_after_backup (id INTEGER)")
    con.commit()
    con.close()
    originals = Path(settings.storage_root)
    (originals / "MARKER-current-state.txt").write_text("should be wiped")

    # 用脚本真实执行恢复（子进程 + 环境指向临时目录）
    script = Path(__file__).resolve().parent.parent / "backend" / "scripts" / "restore_backup.py"
    env = dict(os.environ)
    env["INVOICING_DATABASE_URL"] = f"sqlite:///{iso_db}"
    env["INVOICING_STORAGE_ROOT"] = str(originals)
    env["INVOICING_OPS_BACKUP_DIR"] = str(tmp_path / "backups")
    r = subprocess.run(
        [sys.executable, str(script), str(tar_path), "--yes"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"restore 退出码非 0：\nstdout={r.stdout}\nstderr={r.stderr}"

    # 恢复后断言：seed 发票仍在、junk 表被快照替换、pre-restore 留存、原件重解包
    con = sqlite3.connect(str(iso_db))
    row = con.execute(
        "SELECT invoice_number FROM invoices WHERE invoice_number='SEED0001'").fetchone()
    junk_gone = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='junk_after_backup'"
    ).fetchone()[0] == 0
    con.close()
    assert row is not None, "恢复后 seed 发票应仍在"
    assert junk_gone, "恢复后注入的 junk 表应被快照替换掉"
    assert iso_db.with_suffix(".db.pre-restore").exists(), "恢复前应留存 <db>.pre-restore"
    assert not (originals / "MARKER-current-state.txt").exists(), "原件目录应被重解包"
    assert (originals / "tenant-default" / "a.xml").exists()
    assert (originals / "tenant-default" / "b.pdf").exists()
