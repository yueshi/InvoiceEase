"""从备份恢复（运维兜底）。**必须先停服**。

用法（backend 目录下）：
    uv run python scripts/restore_backup.py data/backups/invoiceease-backup-YYYYMMDD-HHMMSS.tar.gz --yes

步骤：解包 → 校验 → 当前 db 备份为 <db>.pre-restore → 替换 db → 清空并解包 originals
→ 提示执行 alembic upgrade head 与重启。不加 --yes 只预览计划。
"""
import argparse
import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from invoicing.config import settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("backup", help="备份 tar.gz 路径")
    ap.add_argument("--yes", action="store_true", help="确认执行（缺省只预览）")
    args = ap.parse_args()

    src = Path(args.backup)
    if not src.exists():
        print(f"备份不存在: {src}")
        return 1
    db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    originals = Path(settings.storage_root)

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        with tarfile.open(src) as tf:
            tf.extractall(work)
        meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
        print(f"备份时间: {meta['created_at']}  alembic: {meta.get('alembic_version')}")
        print(f"表计数: {meta.get('table_counts')}")
        if not args.yes:
            print("（预览模式，加 --yes 执行）")
            return 0

        if db_path.exists():
            shutil.copy2(db_path, db_path.with_suffix(db_path.suffix + ".pre-restore"))
            print(f"当前库已留存: {db_path}.pre-restore")
        shutil.copy2(work / "invoicing.db", db_path)
        if (work / "originals.tar").exists():
            if originals.exists():
                shutil.rmtree(originals)
            with tarfile.open(work / "originals.tar") as tf:
                tf.extractall(originals.parent)
        print("恢复完成。后续：① uv run alembic upgrade head ② 重启 uvicorn / arq worker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
