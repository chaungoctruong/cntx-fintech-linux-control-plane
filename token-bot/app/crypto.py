import base64
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


NONCE_LEN = 12


class BotCipher:
    def __init__(self, master_key: bytes):
        if len(master_key) not in (16, 24, 32):
            raise ValueError("master key phải là 16/24/32 bytes (đã decode base64)")
        self._aead = AESGCM(master_key)

    def encrypt(self, plaintext: bytes, aad: bytes = b"") -> bytes:
        nonce = os.urandom(NONCE_LEN)
        return nonce + self._aead.encrypt(nonce, plaintext, aad)

    def decrypt(self, blob: bytes, aad: bytes = b"") -> bytes:
        if len(blob) < NONCE_LEN + 16:
            raise ValueError("ciphertext quá ngắn")
        return self._aead.decrypt(blob[:NONCE_LEN], blob[NONCE_LEN:], aad)


def generate_master_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def generate_secret(nbytes: int = 48) -> str:
    return secrets.token_urlsafe(nbytes)
