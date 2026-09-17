"""邮箱凭据加解密。

配置纪律：INVOICING_FERNET_KEY 必须显式配置（44 字符 urlsafe base64 / 32 字节）。
未配置或非法时——历史实现会退化为「进程启动时随机生成密钥」，密文入库后
重启即全体失效（静默数据损失）。现在 encrypt/decrypt 直接拒绝并给出修复指引，
自检（ops/checks.check_fernet_key）负责在启动时暴露存量密文的失效状态。
"""
import logging

from cryptography.fernet import Fernet, InvalidToken

from invoicing.config import settings

logger = logging.getLogger(__name__)

_FERNET_FIX_HINT = (
    "INVOICING_FERNET_KEY 未配置或非法（需 44 字符 urlsafe base64 / 32 字节）。"
    '生成：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"，'
    "写入 backend/.env 后重启服务。"
)


def _configured_fernet() -> Fernet | None:
    """配置合法时返回 Fernet 实例；否则 None（不抛异常，交调用方决定行为）。

    每次调用现构造：调用频率为每次邮箱登录一次，成本可忽略；换来配置热更新
    与测试 monkeypatch 的即时生效。"""
    key = settings.fernet_key
    if len(key) != 44:
        return None
    try:
        return Fernet(key.encode())
    except Exception:
        return None


def fernet_configured() -> bool:
    """当前 FERNET_KEY 是否可用于凭据加解密（自检与 API 守卫共用）。"""
    return _configured_fernet() is not None


def encrypt_secret(plain: str) -> str:
    f = _configured_fernet()
    if f is None:
        raise RuntimeError(f"凭据加密被拒绝（拒绝落库重启即失效的密文）：{_FERNET_FIX_HINT}")
    return f.encrypt(plain.encode()).decode()


def decrypt_secret(cipher: str) -> str:
    f = _configured_fernet()
    if f is None:
        raise RuntimeError(f"凭据解密被拒绝（密钥未配置）：{_FERNET_FIX_HINT}")
    try:
        return f.decrypt(cipher.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "凭据解密失败：密文与当前 INVOICING_FERNET_KEY 不匹配——通常是 key 已更换，"
            "或密文产生于 key 未配置时期（临时随机密钥）。请用 scripts/reencrypt_secrets.py "
            "从旧 key 迁移，或重新录入邮箱凭据。"
        ) from exc
