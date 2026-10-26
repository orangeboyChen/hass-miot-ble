"""Device profile for the Xiaomi Smart Neck Massager M14."""

from ..profile import DeviceProfile, EntityKind, PropertySpec, StateSensorSpec, ValueType

_MODES = (
    (0, "Vitality"),
    (1, "Knead"),
    (2, "Unpack"),
)

_DEVICE_STATES = (
    (0, "Vitality"),
    (1, "Knead"),
    (2, "Unpack"),
    (3, "Scene"),
    (4, "Standby"),
    (5, "Charging"),
    (6, "Shutdown"),
)

PROFILE = DeviceProfile(
    model="soocare.magic_touch.m14",
    name="Xiaomi Smart Neck Massager M14",
    spec_type="urn:miot-spec-v2:device:massager:0000A083:soocare-m14:1:0000C81F",
    auth_type=5,
    properties=(
        PropertySpec(
            name="battery_level",
            siid=3,
            piid=1,
            value_type=ValueType.UINT8,
            entity_kind=EntityKind.SENSOR,
            readable=True,
            notifiable=True,
            minimum=0,
            maximum=100,
            unit="%",
        ),
        PropertySpec(
            name="mode",
            siid=2,
            piid=2,
            value_type=ValueType.UINT8,
            entity_kind=EntityKind.SELECT,
            writable=True,
            value_map=_MODES,
        ),
        PropertySpec(
            name="heating",
            siid=2,
            piid=3,
            value_type=ValueType.BOOL,
            entity_kind=EntityKind.SWITCH,
            writable=True,
            notifiable=True,
        ),
        PropertySpec(
            name="power",
            siid=2,
            piid=1,
            value_type=ValueType.BOOL,
            entity_kind=EntityKind.SWITCH,
            writable=True,
        ),
        PropertySpec(
            name="device_state",
            siid=4,
            piid=1,
            value_type=ValueType.UINT8,
            entity_kind=EntityKind.SENSOR,
            readable=True,
            notifiable=True,
            minimum=0,
            maximum=6,
        ),
    ),
    state_sensors=(
        StateSensorSpec(
            name="device_status",
            source=(4, 1),
            value_map=_DEVICE_STATES,
        ),
    ),
)
