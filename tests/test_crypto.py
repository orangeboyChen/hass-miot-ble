"""Tests for Xiaomi Standard Auth cryptography."""

import pytest

from custom_components.miot_ble.protocol.crypto import SessionKeys, _nonce

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
