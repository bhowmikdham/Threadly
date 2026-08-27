"""Google tokens are encrypted at rest with Fernet (symmetric, key in env)."""
from cryptography.fernet import Fernet

from . import config

_fernet: Fernet | None = None


def _get() -> Fernet:
    global _fernet
    if _fernet is None:
        if not config.FERNET_KEY:
            raise RuntimeError("THREADLY_FERNET_KEY is not set; cannot store Google tokens")
        _fernet = Fernet(config.FERNET_KEY.encode())
    return _fernet


def encrypt(value: str) -> str:
    return _get().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _get().decrypt(value.encode()).decode()
