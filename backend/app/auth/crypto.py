"""Fernet encryption for OAuth refresh tokens at rest (module 2).

Key comes from FERNET_KEY in the environment; rotate by re-encrypting rows.
"""
from cryptography.fernet import Fernet

from app.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().fernet_key
    if not key:
        raise RuntimeError("FERNET_KEY is not set — refuse to store tokens in plaintext.")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt_token(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode()
