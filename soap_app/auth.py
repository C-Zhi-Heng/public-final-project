"""Password hashing/verification only — no DB access, so database.py stays the sole owner of sqlite3 connections."""

import hashlib
import hmac
import os

_ITERATIONS = 200_000  # PBKDF2 iteration count; slows brute-force attempts against a stolen DB copy


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    """Returns (hash, salt); generates a fresh random salt if one isn't supplied (i.e. on account creation)."""
    if salt is None:
        salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return digest, salt


def verify_password(password: str, salt: bytes, expected_hash: bytes) -> bool:
    """Recomputes the hash with the stored salt and compares in constant time (avoids timing attacks)."""
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return hmac.compare_digest(candidate, expected_hash)
