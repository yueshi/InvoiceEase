"""scripts/reencrypt_secrets.py：Fernet key 轮换迁移脚本。

端到端：旧 key 加密的密文 → 脚本迁移 → 新 key 可解密。子进程真实执行（同
test_ops_backup 的 restore 用例模式），env 全部指向 tmp_path，不碰真实库。
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from invoicing.db import Base
from invoicing.models import Mailbox  # noqa: F401  # 顶部 import 注册模型（conftest create_all 时机）

_OLD_KEY = "5HbICFEKCvcZCcRiNcUcC8i6vNRLPBFWwYUS6z4hkyA="
_NEW_KEY = "cZgtiPVTynDwwW2jxBUMpBv47QDU4Rmbx_SZSNEpNww="  # conftest 的测试 key，作为「当前 key」
_SCRIPT = Path(__file__).resolve().parent.parent / "backend" / "scripts" / "reencrypt_secrets.py"


def _make_db(tmp_path: Path) -> Path:
    engine = create_engine(f"sqlite:///{tmp_path}/iso.db")
    Base.metadata.create_all(engine)
    old = Fernet(_OLD_KEY)
    with Session(engine) as s:
        s.add(
            Mailbox(
                name="迁移邮箱",
                mailbox_type="imap",
                password_encrypted=old.encrypt("imap-pass-1".encode()).decode(),
                smtp_password_encrypted=old.encrypt("smtp-pass-1".encode()).decode(),
            )
        )
        s.add(Mailbox(name="无密文邮箱", mailbox_type="imap"))
        s.commit()
    engine.dispose()
    return tmp_path / "iso.db"


def _run(db_path: Path, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["INVOICING_DATABASE_URL"] = f"sqlite:///{db_path}"
    env["INVOICING_FERNET_KEY"] = _NEW_KEY
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *extra],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def _password_plain(db_path: Path, key: str) -> str:
    conn = sqlite3.connect(db_path)
    cipher = conn.execute("SELECT password_encrypted FROM mailboxes WHERE name='迁移邮箱'").fetchone()[0]
    conn.close()
    return Fernet(key.encode()).decrypt(cipher.encode()).decode()


def test_reencrypt_roundtrip(tmp_path):
    db = _make_db(tmp_path)

    preview = _run(db, "--from-key", _OLD_KEY)
    assert preview.returncode == 0
    assert "预览" in preview.stdout
    assert _password_plain(db, _OLD_KEY) == "imap-pass-1"  # 预览未写库，仍是旧 key 密文

    applied = _run(db, "--from-key", _OLD_KEY, "--yes")
    assert applied.returncode == 0, applied.stderr
    assert "完成: 1 个邮箱" in applied.stdout
    assert _password_plain(db, _NEW_KEY) == "imap-pass-1"  # 新 key 可解密
    with pytest.raises(Exception):
        _password_plain(db, _OLD_KEY)  # 旧 key 已解不开


def test_reencrypt_reports_mismatch_without_touching_rows(tmp_path):
    """密文与 --from-key 不匹配 → 报失败清单、退出码 1、不写坏数据。"""
    db = _make_db(tmp_path)
    res = _run(db, "--from-key", _NEW_KEY, "--yes")  # 拿新 key 当旧 key：必然解不开
    assert res.returncode == 1
    assert "迁移邮箱" in res.stdout  # 失败清单点名邮箱
    assert _password_plain(db, _OLD_KEY) == "imap-pass-1"  # 原密文原样保留
