"""备份：创建/内容/轮转/恢复 roundtrip。"""
# 顶部 import：与仓库测试惯例一致。
import json
import sqlite3
import tarfile
import time

from invoicing.config import settings
from invoicing.ops.backup import create_backup, list_backups, rotate_backups


def _seed_originals(tmp_path, monkeypatch):
    root = tmp_path / "originals"
    (root / "tenant-default").mkdir(parents=True)
    (root / "tenant-default" / "a.xml").write_text("<inv/>", encoding="utf-8")
    (root / "tenant-default" / "b.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(settings, "storage_root", str(root))
    monkeypatch.setattr(settings, "ops_backup_dir", str(tmp_path / "backups"))
    return root


def test_create_backup_roundtrip(db, tmp_path, monkeypatch):
    _seed_originals(tmp_path, monkeypatch)
    tar_path = create_backup()
    assert tar_path.exists()
    items = list_backups()
    assert len(items) == 1 and items[0]["meta"] is not None

    # 解包校验：db 快照可打开、originals 完整、meta 有表计数
    work = tmp_path / "unpacked"
    work.mkdir()
    with tarfile.open(tar_path) as tf:
        tf.extractall(work)
    assert (work / "originals.tar").exists()
    with tarfile.open(work / "originals.tar") as tf:
        names = tf.getnames()
    assert any(n.endswith("a.xml") for n in names)
    conn = sqlite3.connect(str(work / "invoicing.db"))
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] >= 0
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
