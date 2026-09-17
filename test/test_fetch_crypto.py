"""fetch/crypto.py：Fernet 凭据加密的配置纪律。

核心约束：未配置合法 INVOICING_FERNET_KEY 时，encrypt/decrypt 必须显式拒绝——
否则进程用临时随机密钥加密入库，重启后密文全体失效（静默数据损失）。"""
import pytest

from invoicing.config import settings
from invoicing.fetch.crypto import decrypt_secret, encrypt_secret, fernet_configured

_VALID_KEY = "cZgtiPVTynDwwW2jxBUMpBv47QDU4Rmbx_SZSNEpNww="
_OTHER_KEY = "5HbICFEKCvcZCcRiNcUcC8i6vNRLPBFWwYUS6z4hkyA="


def test_roundtrip_with_configured_key():
    cipher = encrypt_secret("imap-password-123")
    assert cipher != "imap-password-123"
    assert decrypt_secret(cipher) == "imap-password-123"


def test_encrypt_refuses_when_key_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "fernet_key", "placeholder")
    with pytest.raises(RuntimeError, match="INVOICING_FERNET_KEY"):
        encrypt_secret("secret")


def test_decrypt_refuses_when_key_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "fernet_key", "placeholder")
    with pytest.raises(RuntimeError, match="INVOICING_FERNET_KEY"):
        decrypt_secret("gAAAAABmfake")


def test_fernet_configured_flag(monkeypatch):
    monkeypatch.setattr(settings, "fernet_key", _VALID_KEY)
    assert fernet_configured() is True
    monkeypatch.setattr(settings, "fernet_key", "placeholder")
    assert fernet_configured() is False
    # 44 字符但不是合法 urlsafe base64：同样视为未配置，且不能在 import 时崩
    monkeypatch.setattr(settings, "fernet_key", "x" * 44)
    assert fernet_configured() is False


def test_decrypt_invalid_token_gives_migration_hint(monkeypatch):
    """密文与当前 key 不匹配 → 报错必须指向迁移脚本，而不是裸 InvalidToken。"""
    monkeypatch.setattr(settings, "fernet_key", _OTHER_KEY)
    cipher = encrypt_secret("old-secret")
    monkeypatch.setattr(settings, "fernet_key", _VALID_KEY)
    with pytest.raises(RuntimeError, match="reencrypt_secrets"):
        decrypt_secret(cipher)
