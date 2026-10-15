"""Tests for the MIoT property TLV codec."""

import pytest

from custom_components.miot_ble.profile import EntityKind, PropertySpec, ValueType
from custom_components.miot_ble.profiles import MUXNW_DRYER_M73
from custom_components.miot_ble.protocol.tlv import build_set, build_subscribe, parse_properties


def test_build_set_for_m73_properties() -> None:
    power = MUXNW_DRYER_M73.property(2, 1)
    timer = MUXNW_DRYER_M73.property(2, 3)
    assert build_set(0xB1, power, 1).hex() == "0c20b1000001020100011001"
    assert build_set(0xB2, timer, 6).hex() == "0c20b2000001020300011006"


def test_build_set_supports_bool_and_uint16() -> None:
    boolean = PropertySpec(
        "enabled", 1, 2, ValueType.BOOL, EntityKind.SWITCH, writable=True
    )
    uint16 = PropertySpec(
        "duration", 1, 3, ValueType.UINT16, EntityKind.SENSOR, writable=True
    )
    assert build_set(1, boolean, True).hex() == "0c2001000001010200010001"
    assert build_set(2, uint16, 0x1234).hex() == "0d200200000101030002303412"


def test_build_set_rejects_read_only_property() -> None:
    with pytest.raises(ValueError, match="not writable"):
        build_set(1, MUXNW_DRYER_M73.property(2, 4), 1)


def test_build_subscribe() -> None:
    properties = MUXNW_DRYER_M73.properties[:2]
    assert build_subscribe(0xB3, properties).hex() == "0c20b3000202020100020200"
    with pytest.raises(ValueError, match="at least one"):
        build_subscribe(1, ())


def test_build_subscribe_rejects_write_only_property() -> None:
    prop = PropertySpec(
        "write_only",
        1,
        1,
        ValueType.UINT8,
        EntityKind.SENSOR,
        readable=False,
        writable=True,
    )
    with pytest.raises(ValueError, match="without read or notify"):
        build_subscribe(1, (prop,))


def test_parse_notification() -> None:
    payload = bytes.fromhex("0c20b4000401020400011001")
    values = parse_properties(payload)
    assert len(values) == 1
    assert values[0].key == (2, 4)
    assert values[0].value == 1


def test_parse_snapshot() -> None:
    payload = bytes.fromhex("1720b50003020201000000011001020200000002307856")
    values = parse_properties(payload)
    assert [(value.key, value.value) for value in values] == [((2, 1), 1), ((2, 2), 0x5678)]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "too short"),
        (bytes.fromhex("07200100040100"), "truncated"),
        (bytes.fromhex("062101000400"), "header"),
        (bytes.fromhex("062001009900"), "operation"),
        (bytes.fromhex("062001000001"), "does not contain"),
        (bytes.fromhex("07200100040200"), "exactly one"),
        (bytes.fromhex("0820010004010203"), "truncated"),
        (bytes.fromhex("0c2001000401020100021001"), "truncated"),
        (bytes.fromhex("0c2001000401020100019901"), "Unsupported"),
        (bytes.fromhex("0d200100040102010002100100"), "length does not match"),
        (bytes.fromhex("0c2001000401020100011001ff"), "length"),
        (bytes.fromhex("0d2001000401020100011001ff"), "trailing"),
        (bytes.fromhex("0c2001000401020100010002"), "boolean"),
        (bytes.fromhex("072001000301ff"), "truncated"),
    ],
)
def test_parse_rejects_invalid_payloads(payload: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_properties(payload)


def test_transaction_and_subscription_limits() -> None:
    prop = MUXNW_DRYER_M73.property(2, 1)
    with pytest.raises(ValueError, match="Transaction"):
        build_set(256, prop, 1)
    with pytest.raises(ValueError, match="255"):
        build_subscribe(1, (prop,) * 256)
