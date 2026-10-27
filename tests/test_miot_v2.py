"""Tests for Xiaomi MIoT Spec-v2 messages."""

from dataclasses import replace

import pytest

from custom_components.miot_ble.profile import EntityKind, PropertySpec, ValueType
from custom_components.miot_ble.profiles import SOOCARE_MAGIC_TOUCH_M14
from custom_components.miot_ble.protocol import miot_v2


def test_get_message_and_property_response_round_trip() -> None:
    properties = (SOOCARE_MAGIC_TOUCH_M14.property(3, 1),)
    request = miot_v2.build_get(0x1234, properties)
    assert request == bytes.fromhex("092034120201030100")
    response = bytes.fromhex("0e2034120301030100000001102a")
    transaction_id, values = miot_v2.parse_properties(response)
    assert transaction_id == 0x1234
    assert values[0].key == (3, 1)
    assert values[0].value_type is ValueType.UINT8
    assert values[0].value == 42


def test_set_message_and_response() -> None:
    heating = SOOCARE_MAGIC_TOUCH_M14.property(2, 3)
    assert miot_v2.build_set(7, heating, True) == bytes.fromhex("0c2007000001020300010001")
    response = bytes.fromhex("0b20070001010203000000")
    assert miot_v2.parse_set_response(response) == (7, ((2, 3, 0),))


def test_notification_message_round_trip() -> None:
    payload = bytes.fromhex("0c2001000401020100010001")
    transaction_id, values = miot_v2.parse_properties(payload)
    assert transaction_id == 1
    assert values[0].key == (2, 1)
    assert values[0].value is True


@pytest.mark.parametrize("payload", [b"", bytes.fromhex("0620000003ff")])
def test_invalid_spec_v2_payload_is_rejected(payload: bytes) -> None:
    with pytest.raises(ValueError):
        miot_v2.parse_properties(payload)


@pytest.mark.parametrize("transaction_id", [-1, 0x10000])
def test_message_rejects_invalid_transaction_id(transaction_id: int) -> None:
    with pytest.raises(ValueError, match="Transaction ID"):
        miot_v2.build_message(transaction_id, 0)


def test_message_rejects_oversized_packet() -> None:
    with pytest.raises(ValueError, match="too large"):
        miot_v2.build_message(0, 0, bytes(0x1000))


def test_get_requires_properties() -> None:
    with pytest.raises(ValueError, match="between one and 255"):
        miot_v2.build_get(0, ())


def test_set_rejects_read_only_property() -> None:
    prop = replace(SOOCARE_MAGIC_TOUCH_M14.property(3, 1), writable=False)
    with pytest.raises(ValueError, match="not writable"):
        miot_v2.build_set(0, prop, 50)


@pytest.mark.parametrize(
    "payload",
    [
        bytes.fromhex("07200000050000"),
        bytes.fromhex("0b2000000301ff0000000000"),
        bytes.fromhex("0c200000030101000000000000"),
        bytes.fromhex("0c20000003010100010002"),
        bytes.fromhex("0b200000030101000f00"),
        bytes.fromhex("0d2000000301010000110200"),
        bytes.fromhex("0c2000000301010001100200ff"),
        bytes.fromhex("0b2000000300ff"),
    ],
)
def test_property_parser_rejects_malformed_responses(payload: bytes) -> None:
    with pytest.raises(ValueError):
        miot_v2.parse_properties(payload)


def test_property_parser_skips_failed_get_results() -> None:
    payload = bytes.fromhex("0b20010003010301000100")
    transaction_id, values = miot_v2.parse_properties(payload)
    assert transaction_id == 1
    assert values == ()


def test_property_parser_rejects_invalid_boolean() -> None:
    with pytest.raises(ValueError, match="boolean"):
        miot_v2.parse_properties(bytes.fromhex("0c2001000401020100010002"))


@pytest.mark.parametrize("payload", [b"", bytes.fromhex("0b200000010000")])
def test_set_response_rejects_invalid_header(payload: bytes) -> None:
    with pytest.raises(ValueError):
        miot_v2.parse_set_response(payload)


def test_set_response_rejects_truncated_or_trailing_results() -> None:
    with pytest.raises(ValueError, match="truncated"):
        miot_v2.parse_set_response(bytes.fromhex("07200000010101"))
    with pytest.raises(ValueError, match="trailing"):
        miot_v2.parse_set_response(bytes.fromhex("072000000100ff"))


@pytest.mark.parametrize("payload", [b"", bytes.fromhex("0000010000")])
def test_transaction_validation_rejects_mismatch(payload: bytes) -> None:
    with pytest.raises(ValueError):
        miot_v2.validate_transaction(payload, 0, 0)


def test_profile_value_type_encoding_supports_signed_values() -> None:
    prop = PropertySpec(
        name="offset",
        siid=1,
        piid=1,
        value_type=ValueType.INT16,
        entity_kind=EntityKind.NUMBER,
        writable=True,
    )
    assert miot_v2.build_set(2, prop, -1).endswith(b"\xff\xff")
