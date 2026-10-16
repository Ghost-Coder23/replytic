"""Encryption of secrets (Facebook access tokens) at rest."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _fernet():
    key = settings.TOKEN_ENCRYPTION_KEY
    if not key:
        # Development fallback only (production requires TOKEN_ENCRYPTION_KEY).
        digest = hashlib.sha256(f"replytic-tokens:{settings.SECRET_KEY}".encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(value: str) -> str:
    """Return plaintext, or '' when the value can't be decrypted (e.g. key changed)."""
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


class EncryptedTextField(models.TextField):
    """Stores ciphertext in the database; Python code sees plaintext."""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt(value) if value else ""

    def from_db_value(self, value, expression, connection):
        return decrypt(value) if value else ""
