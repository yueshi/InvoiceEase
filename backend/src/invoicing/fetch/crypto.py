from cryptography.fernet import Fernet

from invoicing.config import settings

_fernet = Fernet(settings.fernet_key.encode() if len(settings.fernet_key) == 44 else Fernet.generate_key())


def encrypt_secret(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt_secret(cipher: str) -> str:
    return _fernet.decrypt(cipher.encode()).decode()
