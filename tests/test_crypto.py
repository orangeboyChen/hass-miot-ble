"""Tests for Xiaomi Standard Auth cryptography."""

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from custom_components.miot_ble.protocol.crypto import MeshSessionKeys, SessionKeys, _nonce

TOKEN = bytes.fromhex("00112233445566778899aabb")
APP_RANDOM = bytes(range(16))
DEVICE_RANDOM = bytes(range(16, 32))


def test_session_key_derivation_and_proofs() -> None:
    keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    assert len(keys.device_key) == len(keys.app_key) == 16
    assert len(keys.device_iv) == len(keys.app_iv) == 4
    proof = keys.expected_device_proof(APP_RANDOM, DEVICE_RANDOM)
    assert keys.verify_device_proof(proof, APP_RANDOM, DEVICE_RANDOM)
    assert not keys.verify_device_proof(bytes(32), APP_RANDOM, DEVICE_RANDOM)
    assert len(keys.app_proof(APP_RANDOM, DEVICE_RANDOM)) == 32


def test_directional_encryption_round_trips() -> None:
    keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    plaintext = bytes.fromhex("0c20b1000001020100011001")
    app_ciphertext = keys.encrypt_from_app(0, plaintext)
    device_ciphertext = keys.encrypt_from_device(7, plaintext)
    assert app_ciphertext != plaintext
    assert device_ciphertext != app_ciphertext
    assert keys.decrypt_from_app(0, app_ciphertext) == plaintext
    assert keys.decrypt_from_device(7, device_ciphertext) == plaintext


@pytest.mark.parametrize(
    ("token", "app_random", "device_random", "message"),
    [
        (b"", APP_RANDOM, DEVICE_RANDOM, "Token"),
        (TOKEN, b"short", DEVICE_RANDOM, "16 bytes"),
        (TOKEN, APP_RANDOM, b"short", "16 bytes"),
    ],
)
def test_key_derivation_rejects_invalid_inputs(
    token: bytes, app_random: bytes, device_random: bytes, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        SessionKeys.derive(token, app_random, device_random)


def test_nonce_validation() -> None:
    assert _nonce(bytes(4), 1) == bytes(8) + b"\x01\x00\x00\x00"
    with pytest.raises(ValueError, match="four bytes"):
        _nonce(bytes(3), 0)
    with pytest.raises(ValueError, match="32-bit"):
        _nonce(bytes(4), -1)


def test_mesh_session_keys_and_directional_encryption() -> None:
    app_private_key = ec.derive_private_key(1, ec.SECP256R1())
    device_private_key = ec.derive_private_key(2, ec.SECP256R1())
    device_public_key = device_private_key.public_key().public_numbers()
    encoded_public_key = device_public_key.x.to_bytes(32, "big") + device_public_key.y.to_bytes(
        32, "big"
    )
    keys = MeshSessionKeys.from_private_key(app_private_key, encoded_public_key, bytes(32))
    assert len(keys.device_key) == len(keys.app_key) == 16
    assert len(keys.device_iv) == len(keys.app_iv) == 4
    assert len(keys.login_proof(encoded_public_key)) == 8
    encrypted = keys.encrypt_from_app(7, b"property")
    assert encrypted[:2] == b"\x07\x00"
    device_ciphertext = keys.encrypt_from_device(7, b"property")
    assert keys.decrypt_from_device(7, device_ciphertext) == b"property"


@pytest.mark.parametrize("shared_secret,gatt_ltmk", [(b"short", bytes(32)), (bytes(32), b"short")])
def test_mesh_session_keys_reject_invalid_inputs(shared_secret: bytes, gatt_ltmk: bytes) -> None:
    with pytest.raises(ValueError, match="invalid lengths"):
        MeshSessionKeys.from_private_key(
            ec.derive_private_key(1, ec.SECP256R1()), shared_secret, gatt_ltmk
        )
