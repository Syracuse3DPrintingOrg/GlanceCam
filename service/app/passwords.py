"""Hash the optional settings password at rest.

The settings password is never stored in plaintext: it is kept as a salted
PBKDF2-HMAC-SHA256 hash and verified by re-hashing the submitted value, so a
leaked settings.json (or backup) does not expose the actual secret.

The format is self-describing so verification needs no extra state:

    pbkdf2$<iterations>$<salt_hex>$<hash_hex>

``looks_hashed`` lets callers tell a stored hash from a plaintext value, so a
value that is already hashed is never double-hashed on a re-save.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets as _secrets

# PBKDF2 iteration count. A sensible interactive-login default that keeps
# verification well under a few milliseconds.
_ITERATIONS = 200_000
_PREFIX = "pbkdf2$"


def looks_hashed(value: str) -> bool:
    """True when value is one of our stored hashes (not a plaintext value)."""
    return isinstance(value, str) and value.startswith(_PREFIX)


def hash_secret(plain: str) -> str:
    """Return a salted PBKDF2 hash string for a plaintext secret.

    An empty input returns '' so an unset password stays unset (not a hash of
    the empty string)."""
    if not plain:
        return ""
    salt = _secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, _ITERATIONS)
    return f"{_PREFIX}{_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_secret(plain: str, stored: str) -> bool:
    """Constant-time check of a plaintext against a stored value.

    Handles our hash format and, for resilience, a legacy plaintext value.
    Returns False for empty inputs.
    """
    if not plain or not stored:
        return False
    if not looks_hashed(stored):
        # Plaintext on disk (should not happen, but never crash on it):
        # compare directly, still in constant time.
        return hmac.compare_digest(plain, stored)
    try:
        _, iter_s, salt_hex, hash_hex = stored.split("$")
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations,
                             dklen=len(expected))
    return hmac.compare_digest(dk, expected)
