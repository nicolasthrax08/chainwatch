"""
ChainWatch Encryption Helpers
AES-256-GCM encryption for sensitive per-user secrets (Alpaca API keys).

Master key is read from the CHAINWATCH_MASTER_KEY environment variable.
Expected format: a raw 32-byte value encoded in base64.

Usage:
    from services.crypto import encrypt_secret, decrypt_secret

    ciphertext_b64, iv_b64 = encrypt_secret("my-secret-value")
    plaintext = decrypt_secret(ciphertext_b64, iv_b64)
"""

import os
import base64
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Master key loading & validation
# ---------------------------------------------------------------------------

def _load_master_key() -> bytes:
    """
    Load and validate the master encryption key from CHAINWATCH_MASTER_KEY.

    The env var must be a base64-encoded 32-byte key.
    Alternatively, a raw 32-byte string is accepted for development convenience.

    Raises ValueError if the key is missing or not exactly 32 bytes after decoding.
    """
    raw = os.environ.get("CHAINWATCH_MASTER_KEY")
    if not raw:
        raise ValueError(
            "CHAINWATCH_MASTER_KEY environment variable is not set. "
            "Generate one with: python -c \"import os,base64; print(base64.b64encode(os.urandom(32)).decode())\""
        )

    # Try base64 decode first
    try:
        key_bytes = base64.b64decode(raw, validate=True)
    except Exception:
        # Fall back to raw bytes
        key_bytes = raw.encode("utf-8")

    if len(key_bytes) != 32:
        raise ValueError(
            f"CHAINWATCH_MASTER_KEY must decode to exactly 32 bytes, "
            f"got {len(key_bytes)} bytes. "
            f"Generate a correct key with: python -c \"import os,base64; print(base64.b64encode(os.urandom(32)).decode())\""
        )

    return key_bytes


# Module-level singleton – validated once on first use
_master_key: bytes | None = None


def _get_master_key() -> bytes:
    """Return the cached master key, loading and validating on first call."""
    global _master_key
    if _master_key is None:
        _master_key = _load_master_key()
    return _master_key


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encrypt_secret(plaintext: str) -> tuple[str, str]:
    """
    Encrypt *plaintext* with AES-256-GCM using the master key.

    Returns:
        (ciphertext_b64, iv_b64) — both base64-encoded strings safe for TEXT columns.

    A fresh 12-byte IV (nonce) is generated for every call, so encrypting the
    same plaintext twice yields different ciphertexts.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    key = _get_master_key()
    iv = os.urandom(12)  # 96-bit nonce, standard for AES-GCM

    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)

    return base64.b64encode(ciphertext).decode("ascii"), base64.b64encode(iv).decode("ascii")


def decrypt_secret(ciphertext_b64: str, iv_b64: str) -> str:
    """
    Decrypt a base64-encoded *ciphertext_b64* using the base64-encoded *iv_b64*.

    Returns the original plaintext string.

    Raises cryptography.exceptions.InvalidTag if the ciphertext or key is wrong.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    key = _get_master_key()
    iv = base64.b64decode(iv_b64)
    ciphertext = base64.b64decode(ciphertext_b64)

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)

    return plaintext.decode("utf-8")
