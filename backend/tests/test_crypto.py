#!/usr/bin/env python3
"""
Tests for the crypto.py encryption service.
Covers: master key loading, validation, encrypt/decrypt round-trip, IV uniqueness,
error handling for missing/invalid keys, and edge cases.
"""
import os
import sys
import base64
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "services"))


@pytest.fixture(autouse=True)
def _reset_master_key():
    """Reset the module-level master key singleton before each test."""
    import crypto as cr
    cr._master_key = None
    yield
    cr._master_key = None


def _set_test_key(monkeypatch):
    """Set a valid 32-byte base64-encoded test key."""
    key = os.urandom(32)
    b64_key = base64.b64encode(key).decode()
    monkeypatch.setenv("CHAINWATCH_MASTER_KEY", b64_key)
    return key


class TestLoadMasterKey:

    def test_valid_base64_key(self, monkeypatch):
        raw_key = os.urandom(32)
        b64_key = base64.b64encode(raw_key).decode()
        monkeypatch.setenv("CHAINWATCH_MASTER_KEY", b64_key)
        import crypto as cr
        key = cr._load_master_key()
        assert key == raw_key
        assert len(key) == 32

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("CHAINWATCH_MASTER_KEY", raising=False)
        import crypto as cr
        with pytest.raises(ValueError, match="CHAINWATCH_MASTER_KEY"):
            cr._load_master_key()

    def test_short_key_raises(self, monkeypatch):
        short_key = base64.b64encode(b"short").decode()
        monkeypatch.setenv("CHAINWATCH_MASTER_KEY", short_key)
        import crypto as cr
        with pytest.raises(ValueError, match="32 bytes"):
            cr._load_master_key()

    def test_long_key_raises(self, monkeypatch):
        long_key = base64.b64encode(b"a" * 64).decode()
        monkeypatch.setenv("CHAINWATCH_MASTER_KEY", long_key)
        import crypto as cr
        with pytest.raises(ValueError, match="32 bytes"):
            cr._load_master_key()

    def test_raw_32_byte_string_fallback(self, monkeypatch):
        """A raw 32-byte string that fails base64 decode and is exactly 32 bytes."""
        # Use a string that is NOT valid base64 and is exactly 32 bytes
        raw = "!" * 32  # "!" is not a valid base64 character
        monkeypatch.setenv("CHAINWATCH_MASTER_KEY", raw)
        import crypto as cr
        key = cr._load_master_key()
        assert key == raw.encode("utf-8")
        assert len(key) == 32

    def test_invalid_base64_falls_back_to_raw_but_wrong_length(self, monkeypatch):
        """Invalid base64 that falls back to raw but is NOT 32 bytes → raises."""
        raw = "!" * 20  # Not valid base64, only 20 bytes
        monkeypatch.setenv("CHAINWATCH_MASTER_KEY", raw)
        import crypto as cr
        with pytest.raises(ValueError, match="32 bytes"):
            cr._load_master_key()


class TestGetMasterKey:

    def test_caches_key(self, monkeypatch):
        _set_test_key(monkeypatch)
        import crypto as cr
        key1 = cr._get_master_key()
        key2 = cr._get_master_key()
        assert key1 is key2

    def test_returns_32_bytes(self, monkeypatch):
        _set_test_key(monkeypatch)
        import crypto as cr
        key = cr._get_master_key()
        assert isinstance(key, bytes)
        assert len(key) == 32


class TestEncryptDecrypt:

    def test_round_trip(self, monkeypatch):
        _set_test_key(monkeypatch)
        import crypto as cr
        plaintext = "my-secret-api-key-12345"
        ct_b64, iv_b64 = cr.encrypt_secret(plaintext)
        decrypted = cr.decrypt_secret(ct_b64, iv_b64)
        assert decrypted == plaintext

    def test_ciphertext_is_base64(self, monkeypatch):
        _set_test_key(monkeypatch)
        import crypto as cr
        ct_b64, iv_b64 = cr.encrypt_secret("test")
        # Should not raise
        base64.b64decode(ct_b64)
        base64.b64decode(iv_b64)

    def test_iv_is_12_bytes(self, monkeypatch):
        _set_test_key(monkeypatch)
        import crypto as cr
        _, iv_b64 = cr.encrypt_secret("test")
        iv = base64.b64decode(iv_b64)
        assert len(iv) == 12

    def test_same_plaintext_different_ciphertext(self, monkeypatch):
        """Encrypting the same plaintext twice should yield different ciphertexts (different IVs)."""
        _set_test_key(monkeypatch)
        import crypto as cr
        ct1, _ = cr.encrypt_secret("same plaintext")
        ct2, _ = cr.encrypt_secret("same plaintext")
        assert ct1 != ct2

    def test_same_plaintext_different_ivs(self, monkeypatch):
        """Encrypting the same plaintext twice should yield different IVs."""
        _set_test_key(monkeypatch)
        import crypto as cr
        _, iv1 = cr.encrypt_secret("same plaintext")
        _, iv2 = cr.encrypt_secret("same plaintext")
        assert iv1 != iv2

    def test_empty_string(self, monkeypatch):
        """Encrypting an empty string should work."""
        _set_test_key(monkeypatch)
        import crypto as cr
        ct_b64, iv_b64 = cr.encrypt_secret("")
        decrypted = cr.decrypt_secret(ct_b64, iv_b64)
        assert decrypted == ""

    def test_unicode_plaintext(self, monkeypatch):
        """Unicode characters should round-trip correctly."""
        _set_test_key(monkeypatch)
        import crypto as cr
        plaintext = "🔑密钥-ключ-مفتاح"
        ct_b64, iv_b64 = cr.encrypt_secret(plaintext)
        decrypted = cr.decrypt_secret(ct_b64, iv_b64)
        assert decrypted == plaintext

    def test_long_plaintext(self, monkeypatch):
        """Large plaintext should encrypt/decrypt correctly."""
        _set_test_key(monkeypatch)
        import crypto as cr
        plaintext = "A" * 10000
        ct_b64, iv_b64 = cr.encrypt_secret(plaintext)
        decrypted = cr.decrypt_secret(ct_b64, iv_b64)
        assert decrypted == plaintext

    def test_wrong_key_fails_decryption(self, monkeypatch):
        """Decrypting with a different key should fail."""
        import crypto as cr

        # Encrypt with key 1
        key1 = os.urandom(32)
        monkeypatch.setenv("CHAINWATCH_MASTER_KEY", base64.b64encode(key1).decode())
        ct_b64, iv_b64 = cr.encrypt_secret("secret")

        # Reset and use key 2
        cr._master_key = None
        key2 = os.urandom(32)
        monkeypatch.setenv("CHAINWATCH_MASTER_KEY", base64.b64encode(key2).decode())

        with pytest.raises(Exception):  # InvalidTag
            cr.decrypt_secret(ct_b64, iv_b64)

    def test_tampered_ciphertext_fails(self, monkeypatch):
        """Tampered ciphertext should fail authentication."""
        _set_test_key(monkeypatch)
        import crypto as cr
        ct_b64, iv_b64 = cr.encrypt_secret("secret")
        ct = bytearray(base64.b64decode(ct_b64))
        ct[0] ^= 0xFF  # Flip bits
        tampered_ct = base64.b64encode(bytes(ct)).decode()

        with pytest.raises(Exception):  # InvalidTag
            cr.decrypt_secret(tampered_ct, iv_b64)

    def test_returns_tuple_of_two_strings(self, monkeypatch):
        _set_test_key(monkeypatch)
        import crypto as cr
        result = cr.encrypt_secret("test")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)


class TestEncryptOutputFormat:

    def test_ciphertext_not_equal_to_plaintext(self, monkeypatch):
        _set_test_key(monkeypatch)
        import crypto as cr
        ct_b64, _ = cr.encrypt_secret("hello world")
        ct = base64.b64decode(ct_b64)
        assert ct != b"hello world"

    def test_ciphertext_longer_than_plaintext(self, monkeypatch):
        """AES-GCM adds a 16-byte auth tag."""
        _set_test_key(monkeypatch)
        import crypto as cr
        ct_b64, _ = cr.encrypt_secret("a")
        ct = base64.b64decode(ct_b64)
        # 1 byte plaintext + 16 byte tag = 17 bytes minimum
        assert len(ct) > 1
