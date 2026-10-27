"""Xiaomi Standard Auth cryptographic helpers."""

from __future__ import annotations

import hmac as stdlib_hmac
import struct
import zlib
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.asymmetric import ec
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

    def verify_device_proof(self, proof: bytes, app_random: bytes, device_random: bytes) -> bool:
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


@dataclass(frozen=True, slots=True)
class MeshSessionKeys:
    """Directional keys negotiated by Xiaomi's security-chip login."""

    device_key: bytes
    app_key: bytes
    device_iv: bytes
    app_iv: bytes

    @classmethod
    def from_private_key(
        cls,
        private_key: ec.EllipticCurvePrivateKey,
        device_public_key: bytes,
        gatt_ltmk: bytes,
    ) -> MeshSessionKeys:
        """Derive Mesh session keys from the device public key and cloud key."""
        if len(device_public_key) != 64 or len(gatt_ltmk) != 32:
            raise ValueError("Mesh login inputs have invalid lengths")
        try:
            public_key = ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256R1(), b"\x04" + device_public_key
            )
        except ValueError as error:
            raise ValueError("Mesh device public key is invalid") from error
        shared_secret = private_key.exchange(ec.ECDH(), public_key)
        material = HKDF(
            algorithm=hashes.SHA256(),
            length=64,
            salt=b"miot-mesh-login-salt",
            info=b"miot-mesh-login-info",
        ).derive(shared_secret + gatt_ltmk)
        return cls(material[0:16], material[16:32], material[32:36], material[36:40])

    def login_proof(self, device_public_key: bytes) -> bytes:
        """Build the encrypted checksum used to prove possession of the LTMK."""
        if len(device_public_key) != 64:
            raise ValueError("Mesh device public key must contain 64 bytes")
        checksum = zlib.crc32(device_public_key).to_bytes(4, "little")
        return AESCCM(self.app_key, tag_length=4).encrypt(bytes(range(0x10, 0x1C)), checksum, None)

    @staticmethod
    def _nonce(iv: bytes, counter: int, roll: int) -> bytes:
        if len(iv) != 4:
            raise ValueError("A Xiaomi Mesh session IV must contain four bytes")
        if not 0 <= counter <= 0xFFFF or not 0 <= roll <= 0xFFFF:
            raise ValueError("Mesh counters must fit in unsigned 16-bit integers")
        return iv + bytes(4) + struct.pack("<HH", counter, roll)

    def encrypt_from_app(self, counter: int, plaintext: bytes, roll: int = 0) -> bytes:
        """Encrypt one Mesh app-to-device payload, including its counter."""
        ciphertext = AESCCM(self.app_key, tag_length=4).encrypt(
            self._nonce(self.app_iv, counter, roll), plaintext, None
        )
        return struct.pack("<H", counter) + ciphertext

    def decrypt_from_device(self, counter: int, ciphertext: bytes, roll: int = 0) -> bytes:
        """Decrypt one Mesh device-to-app payload."""
        return AESCCM(self.device_key, tag_length=4).decrypt(
            self._nonce(self.device_iv, counter, roll), ciphertext, None
        )

    def encrypt_from_device(self, counter: int, plaintext: bytes, roll: int = 0) -> bytes:
        """Encrypt one Mesh device-to-app payload for protocol tests."""
        return AESCCM(self.device_key, tag_length=4).encrypt(
            self._nonce(self.device_iv, counter, roll), plaintext, None
        )


@dataclass(frozen=True, slots=True)
class MeshLoginMaterial:
    """Compatibility helper for callers that only need a login cipher."""

    key: bytes
    nonce: bytes

    @classmethod
    def derive(cls, shared_secret: bytes, gatt_ltmk: bytes) -> MeshLoginMaterial:
        """Derive the app-direction login key from Mesh inputs."""
        if len(shared_secret) != 32 or len(gatt_ltmk) != 32:
            raise ValueError("Mesh login inputs must contain 32 bytes")
        material = HKDF(
            algorithm=hashes.SHA256(),
            length=64,
            salt=b"miot-mesh-login-salt",
            info=b"miot-mesh-login-info",
        ).derive(shared_secret + gatt_ltmk)
        return cls(material[16:32], bytes(range(16, 28)))

    def encrypt(self, payload: bytes) -> bytes:
        """Encrypt one login payload with the compatibility nonce."""
        return AESCCM(self.key, tag_length=4).encrypt(self.nonce, payload, None)
