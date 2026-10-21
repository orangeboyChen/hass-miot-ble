"""Tests for declarative device profiles."""

import pytest

from custom_components.miot_ble.profile import (
    DeviceProfile,
    EntityKind,
    PropertySpec,
    StateSensorSpec,
    ValueType,
)
from custom_components.miot_ble.profiles import (
    MUXNW_DRYER_M73,
    XIAOMI_MAGIC_TOUCH_YB01,
    XIAOMI_MAGIC_TOUCH_YB11,
    get_profile,
    iter_profiles,
    profile_for_product_id,
)


def test_m73_profile_matches_plugin_bundle() -> None:
    profile = get_profile("muxnw.dryer.m73")
    assert profile is MUXNW_DRYER_M73
    assert profile.auth_type == 4
    assert [prop.key for prop in profile.properties] == [(2, 1), (2, 2), (2, 3), (2, 4)]
    assert profile.property(2, 3).maximum == 6
    assert profile.property(2, 4).value_type is ValueType.UINT16
    assert list(iter_profiles()) == [
        profile,
        XIAOMI_MAGIC_TOUCH_YB01,
        XIAOMI_MAGIC_TOUCH_YB11,
    ]


def test_yb01_profile_matches_plugin_bundle() -> None:
    profile = get_profile("xiaomi.magic_touch.yb01")
    assert profile is XIAOMI_MAGIC_TOUCH_YB01
    assert profile.auth_type == 4
    assert [prop.key for prop in profile.properties] == [
        (3, 1),
        (4, 1),
        (4, 2),
        (4, 3),
        (4, 4),
        (4, 5),
        (4, 6),
        (4, 7),
        (4, 10),
    ]
    assert profile.property(3, 1).value_multiplier == 5
    assert profile.property(4, 3).value_map == ()
    assert profile.property(4, 3).minimum == 10
    assert profile.property(4, 3).step == 5
    assert profile.property(4, 10).maximum == 2
    assert profile.state_sensors == (
        StateSensorSpec(
            "device_status",
            (4, 10),
            ((0, "Standby"), (1, "Massaging"), (2, "Charging")),
        ),
    )


def test_yb11_profile_matches_plugin_bundle() -> None:
    profile = get_profile("xiaomi.magic_touch.yb11")
    assert profile is XIAOMI_MAGIC_TOUCH_YB11
    assert profile.auth_type == 4
    assert [prop.key for prop in profile.properties] == [
        (3, 1003),
        (4, 1),
        (4, 2),
        (4, 3),
        (4, 4),
        (4, 5),
        (4, 8),
        (4, 11),
        (4, 13),
        (4, 14),
        (4, 15),
    ]
    assert profile.property(3, 1003).value_multiplier == 5
    assert profile.property(4, 3).entity_kind is EntityKind.NUMBER
    assert profile.property(4, 8).maximum == 4
    assert profile.state_sensors == (
        StateSensorSpec(
            "device_status",
            (4, 8),
            (
                (0, "Standby"),
                (1, "Massaging"),
                (2, "Charging"),
                (3, "Fully charged"),
                (4, "Resetting"),
            ),
        ),
    )


def test_unknown_profile_and_property_are_rejected() -> None:
    with pytest.raises(KeyError, match="Unsupported"):
        get_profile("missing.model")
    with pytest.raises(KeyError, match="9/9"):
        MUXNW_DRYER_M73.property(9, 9)


def test_product_id_matches_only_supported_profiles() -> None:
    assert profile_for_product_id(0x46C6) is XIAOMI_MAGIC_TOUCH_YB01
    assert profile_for_product_id(0x5A5B) is MUXNW_DRYER_M73
    assert profile_for_product_id(0x8FD3) is XIAOMI_MAGIC_TOUCH_YB11
    assert profile_for_product_id(0x0000) is None


def test_property_validates_values() -> None:
    timer = MUXNW_DRYER_M73.property(2, 3)
    assert timer.validate_value(6) == 6
    with pytest.raises(TypeError):
        timer.validate_value(True)
    with pytest.raises(ValueError, match="below"):
        timer.validate_value(0)
    with pytest.raises(ValueError, match="above"):
        timer.validate_value(7)

    boolean = PropertySpec("enabled", 1, 1, ValueType.BOOL, EntityKind.SWITCH)
    assert boolean.validate_value(True) == 1
    with pytest.raises(TypeError):
        boolean.validate_value(1)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "", "siid": 1, "piid": 1}, "name"),
        ({"name": "x", "siid": 0, "piid": 1}, "SIID"),
        ({"name": "x", "siid": 1, "piid": 1, "readable": False}, "access"),
        ({"name": "x", "siid": 1, "piid": 1, "minimum": 2, "maximum": 1}, "minimum"),
        ({"name": "x", "siid": 1, "piid": 1, "step": 0}, "step"),
        ({"name": "x", "siid": 1, "piid": 65536}, "PIID"),
        ({"name": "x", "siid": 1, "piid": 1, "value_multiplier": 0}, "multiplier"),
    ],
)
def test_property_rejects_invalid_metadata(kwargs: dict[str, object], message: str) -> None:
    defaults: dict[str, object] = {
        "value_type": ValueType.UINT8,
        "entity_kind": EntityKind.SENSOR,
    }
    with pytest.raises(ValueError, match=message):
        PropertySpec(**defaults, **kwargs)  # type: ignore[arg-type]


def test_property_rejects_wire_range_and_step_mismatch() -> None:
    wide = PropertySpec("wide", 1, 1, ValueType.UINT8, EntityKind.SENSOR)
    with pytest.raises(ValueError, match="wire type"):
        wide.validate_value(256)
    stepped = PropertySpec(
        "stepped", 1, 2, ValueType.UINT8, EntityKind.SELECT, minimum=1, maximum=5, step=2
    )
    with pytest.raises(ValueError, match="step"):
        stepped.validate_value(2)


def test_signed_integer_and_value_map_validation() -> None:
    signed = PropertySpec("offset", 1, 1, ValueType.INT8, EntityKind.NUMBER)
    assert signed.validate_value(-128) == -128
    with pytest.raises(ValueError, match="wire type"):
        signed.validate_value(-129)

    mode = PropertySpec(
        "mode",
        1,
        2,
        ValueType.UINT8,
        EntityKind.SELECT,
        value_map=((0, "Off"), (2, "Auto")),
    )
    assert mode.validate_value(2) == 2
    with pytest.raises(ValueError, match="value map"):
        mode.validate_value(1)


@pytest.mark.parametrize(
    "value_map",
    [
        ((1, "One"), (1, "Two")),
        ((1, "Same"), (2, "Same")),
        ((1, ""),),
    ],
)
def test_property_rejects_invalid_value_map(value_map: tuple[tuple[int, str], ...]) -> None:
    with pytest.raises(ValueError, match="value map"):
        PropertySpec(
            "mode",
            1,
            1,
            ValueType.UINT8,
            EntityKind.SELECT,
            value_map=value_map,
        )


def test_device_profile_rejects_invalid_metadata() -> None:
    prop = PropertySpec("value", 1, 1, ValueType.UINT8, EntityKind.SENSOR)
    with pytest.raises(ValueError, match="identity"):
        DeviceProfile("", "Name", "type", 4, (prop,))
    with pytest.raises(ValueError, match="negative"):
        DeviceProfile("model", "Name", "type", -1, (prop,))
    with pytest.raises(ValueError, match="duplicate"):
        DeviceProfile("model", "Name", "type", 4, (prop, prop))


@pytest.mark.parametrize(
    "value_map",
    [
        (),
        ((1, "One"), (1, "Two")),
        ((1, "Same"), (2, "Same")),
        ((1, ""),),
    ],
)
def test_state_sensor_rejects_invalid_value_map(
    value_map: tuple[tuple[int, str], ...],
) -> None:
    with pytest.raises(ValueError, match="value map"):
        StateSensorSpec("status", (1, 1), value_map)


def test_device_profile_rejects_invalid_state_sensors() -> None:
    prop = PropertySpec("value", 1, 1, ValueType.UINT8, EntityKind.SENSOR)
    sensor = StateSensorSpec("status", prop.key, ((0, "Off"), (1, "On")))
    with pytest.raises(ValueError, match="duplicate state sensor"):
        DeviceProfile("model", "Name", "type", 4, (prop,), (sensor, sensor))
    with pytest.raises(ValueError, match="source"):
        DeviceProfile(
            "model",
            "Name",
            "type",
            4,
            (prop,),
            (StateSensorSpec("status", (9, 9), ((0, "Off"),)),),
        )
    with pytest.raises(ValueError, match="name"):
        StateSensorSpec("", prop.key, ((0, "Off"),))
