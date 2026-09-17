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
