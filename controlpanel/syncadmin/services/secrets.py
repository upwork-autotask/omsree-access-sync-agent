"""Encrypt secrets (DB/SMTP passwords, tokens) at rest in the local SQLite DB.

Uses Fernet with a key kept in STATE_DIR/secret.key (outside source control). This
is not a hardware vault, but it keeps plaintext passwords out of the DB file and any
backups of it. The key file should be readable only by the agent's Windows user.
"""

from __future__ import annotations

from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken

_PREFIX = "enc:"
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key_file = settings.STATE_DIR / "secret.key"
        if key_file.exists():
            key = key_file.read_bytes()
        else:
            key = Fernet.generate_key()
            key_file.write_bytes(key)
        _fernet = Fernet(key)
    return _fernet


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    if plaintext.startswith(_PREFIX):  # already encrypted
        return plaintext
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt(stored: str) -> str:
    if not stored:
        return ""
    if not stored.startswith(_PREFIX):  # legacy/plaintext value
        return stored
    try:
        return _get_fernet().decrypt(stored[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
