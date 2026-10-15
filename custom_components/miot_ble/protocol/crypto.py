"""Xiaomi Standard Auth cryptographic helpers."""

from __future__ import annotations

import hmac as stdlib_hmac
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

LOGIN_INFO = b"mible-login-info"
AES_TAG_LENGTH = 4


def _hmac_sha256(key: bytes, data: bytes) -> bytes:
    digest = hmac.HMAC(key, hashes.SHA256())
    digest.update(data)
    return digest.finalize()


def _nonce(iv: bytes, counter: int) -> bytes:
    if len(iv) != 4:
        raise ValueError("A Xiaomi BLE session IV must contain four bytes")
    if not 0 <= counter <= 0xFFFFFFFF:
        raise ValueError("AES counter must fit in an unsigned 32-bit integer")
    return iv + bytes(4) + struct.pack("<I", counter)


@dataclass(frozen=True, slots=True)
class SessionKeys:
    """Directional keys and IVs negotiated by Xiaomi Standard Auth."""

    device_key: bytes
    app_key: bytes
    device_iv: bytes
    app_iv: bytes

    @classmethod
    def derive(cls, token: bytes, app_random: bytes, device_random: bytes) -> SessionKeys:
        """Derive session keys from a bound-device token and handshake randoms."""
        if not token:
            raise ValueError("Token must not be empty")
        if len(app_random) != 16 or len(device_random) != 16:
            raise ValueError("Handshake random values must contain 16 bytes")
        material = HKDF(
            algorithm=hashes.SHA256(),
            length=64,
            salt=app_random + device_random,
            info=LOGIN_INFO,
        ).derive(token)
        return cls(
            device_key=material[0:16],
            app_key=material[16:32],
            device_iv=material[32:36],
            app_iv=material[36:40],
        )

    def app_proof(self, app_random: bytes, device_random: bytes) -> bytes:
        """Build the proof sent by the app during login."""
        return _hmac_sha256(self.app_key, app_random + device_random)

    def expected_device_proof(self, app_random: bytes, device_random: bytes) -> bytes:
        """Build the expected proof sent by the device during login."""
        return _hmac_sha256(self.device_key, device_random + app_random)

    def verify_device_proof(
        self, proof: bytes, app_random: bytes, device_random: bytes
    ) -> bool:
        """Verify the device proof without exposing a timing side channel."""
        expected = self.expected_device_proof(app_random, device_random)
        return stdlib_hmac.compare_digest(proof, expected)

    def encrypt_from_app(self, counter: int, plaintext: bytes) -> bytes:
        """Encrypt one app-to-device payload."""
        return AESCCM(self.app_key, tag_length=AES_TAG_LENGTH).encrypt(
            _nonce(self.app_iv, counter), plaintext, None
        )

    def decrypt_from_app(self, counter: int, ciphertext: bytes) -> bytes:
        """Decrypt one app-to-device payload for protocol diagnostics."""
        return AESCCM(self.app_key, tag_length=AES_TAG_LENGTH).decrypt(
            _nonce(self.app_iv, counter), ciphertext, None
        )

    def encrypt_from_device(self, counter: int, plaintext: bytes) -> bytes:
        """Encrypt one device-to-app payload for protocol diagnostics."""
        return AESCCM(self.device_key, tag_length=AES_TAG_LENGTH).encrypt(
            _nonce(self.device_iv, counter), plaintext, None
        )

    def decrypt_from_device(self, counter: int, ciphertext: bytes) -> bytes:
        """Decrypt one device-to-app payload."""
        return AESCCM(self.device_key, tag_length=AES_TAG_LENGTH).decrypt(
            _nonce(self.device_iv, counter), ciphertext, None
        )
