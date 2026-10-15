"""Device profile for the Muxiang M73 shoe dryer."""

from ..profile import DeviceProfile, EntityKind, PropertySpec, ValueType

PROFILE = DeviceProfile(
    model="muxnw.dryer.m73",
    name="Muxiang M73 Shoe Dryer",
    spec_type="urn:miot-spec-v2:device:clothes-dryer:0000A06D:muxnw-m73:1",
    auth_type=4,
    properties=(
        PropertySpec(
            name="power",
            siid=2,
            piid=1,
            value_type=ValueType.UINT8,
            entity_kind=EntityKind.SWITCH,
            writable=True,
            notifiable=True,
            minimum=0,
            maximum=1,
        ),
        PropertySpec(
            name="uv",
            siid=2,
            piid=2,
            value_type=ValueType.UINT8,
            entity_kind=EntityKind.SWITCH,
            writable=True,
            notifiable=True,
            minimum=0,
            maximum=1,
        ),
        PropertySpec(
            name="timer",
            siid=2,
            piid=3,
            value_type=ValueType.UINT8,
            entity_kind=EntityKind.SELECT,
            writable=True,
            notifiable=True,
            minimum=1,
            maximum=6,
            step=1,
            unit="h",
        ),
        PropertySpec(
            name="remaining_time",
            siid=2,
            piid=4,
            value_type=ValueType.UINT16,
            entity_kind=EntityKind.SENSOR,
            readable=False,
            notifiable=True,
            minimum=0,
            maximum=21600,
            unit="s",
        ),
    ),
)
