"""AES-256-GCM token encryption.

Byte-for-byte compatible with the original NestJS EncryptionService so tokens
encrypted by either implementation can be decrypted by the other. Wire format is
base64(iv[12] || tag[16] || ciphertext), keyed by TOKEN_ENCRYPTION_KEY (64 hex chars).
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.settings import settings

_IV_LEN = 12
_TAG_LEN = 16


class EncryptionService:
    def __init__(self) -> None:
        self._key: bytes | None = None

    def _get_key(self) -> bytes:
        if self._key is None:
            hex_key = settings.token_encryption_key
            if not hex_key or len(hex_key) != 64:
                msg = "TOKEN_ENCRYPTION_KEY must be 64 hex chars (32 bytes)"
                raise ValueError(msg)
            self._key = bytes.fromhex(hex_key)
        return self._key

    def encrypt(self, plaintext: str) -> str:
        iv = os.urandom(_IV_LEN)
        # cryptography returns ciphertext || tag; Node stores iv || tag || ciphertext.
        ct_and_tag = AESGCM(self._get_key()).encrypt(iv, plaintext.encode("utf-8"), None)
        ciphertext, tag = ct_and_tag[:-_TAG_LEN], ct_and_tag[-_TAG_LEN:]
        return base64.b64encode(iv + tag + ciphertext).decode("ascii")

    def decrypt(self, payload: str) -> str:
        buf = base64.b64decode(payload)
        iv = buf[:_IV_LEN]
        tag = buf[_IV_LEN : _IV_LEN + _TAG_LEN]
        ciphertext = buf[_IV_LEN + _TAG_LEN :]
        plaintext = AESGCM(self._get_key()).decrypt(iv, ciphertext + tag, None)
        return plaintext.decode("utf-8")
