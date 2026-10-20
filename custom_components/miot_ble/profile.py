"""Declarative device profile types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class ValueType(IntEnum):
    """Miot value types used by the encrypted BLE property protocol."""

    BOOL = 0x00
    UINT8 = 0x10
    INT8 = 0x20
    UINT16 = 0x30
    INT16 = 0x40
    UINT32 = 0x50
    INT32 = 0x60
    UINT64 = 0x70
    INT64 = 0x80

    @property
    def byte_width(self) -> int:
        """Return the encoded value width."""
        return {
            ValueType.BOOL: 1,
            ValueType.UINT8: 1,
            ValueType.INT8: 1,
            ValueType.UINT16: 2,
            ValueType.INT16: 2,
            ValueType.UINT32: 4,
            ValueType.INT32: 4,
            ValueType.UINT64: 8,
            ValueType.INT64: 8,
        }[self]

    @property
    def signed(self) -> bool:
        """Return whether the integer wire type is signed."""
        return self in {
            ValueType.INT8,
            ValueType.INT16,
            ValueType.INT32,
            ValueType.INT64,
        }


class EntityKind(StrEnum):
    """Home Assistant entity representation requested by a property."""

    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    NUMBER = "number"
    SWITCH = "switch"
    SELECT = "select"


@dataclass(frozen=True, slots=True)
class PropertySpec:
    """Describe one Miot property exposed by a device."""

    name: str
    siid: int
    piid: int
    value_type: ValueType
    entity_kind: EntityKind
    readable: bool = True
    writable: bool = False
    notifiable: bool = False
    minimum: int | None = None
    maximum: int | None = None
    step: int | None = None
    unit: str | None = None
    value_map: tuple[tuple[int, str], ...] = ()
    value_multiplier: int = 1

    def __post_init__(self) -> None:
        """Validate profile metadata at import time."""
        if not self.name:
            raise ValueError("Property name must not be empty")
        if not 1 <= self.siid <= 0xFF:
            raise ValueError("SIID must be between 1 and 255")
        if not 1 <= self.piid <= 0xFFFF:
            raise ValueError("PIID must be between 1 and 65535")
        if not (self.readable or self.writable or self.notifiable):
            raise ValueError("Property must expose at least one access mode")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Property minimum must not exceed maximum")
        if self.step is not None and self.step <= 0:
            raise ValueError("Property step must be positive")
        if self.value_multiplier <= 0:
            raise ValueError("Property value multiplier must be positive")
        values = [value for value, _ in self.value_map]
        labels = [label for _, label in self.value_map]
        if len(values) != len(set(values)) or len(labels) != len(set(labels)):
            raise ValueError("Property value map must contain unique values and labels")
        if any(not label for label in labels):
            raise ValueError("Property value map labels must not be empty")

    @property
    def key(self) -> tuple[int, int]:
        """Return the Miot property identifier."""
        return self.siid, self.piid

    def validate_value(self, value: int | bool) -> int:
        """Validate and normalize a value before encoding it."""
        if self.value_type is ValueType.BOOL:
            if not isinstance(value, bool):
                raise TypeError(f"{self.name} requires a boolean value")
            normalized = int(value)
        else:
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{self.name} requires an integer value")
            normalized = value

        bits = 8 * self.value_type.byte_width
        width_minimum = -(1 << (bits - 1)) if self.value_type.signed else 0
        width_maximum = (1 << (bits - 1)) - 1 if self.value_type.signed else (1 << bits) - 1
        if not width_minimum <= normalized <= width_maximum:
            raise ValueError(f"{self.name} is outside its wire type range")
        if self.minimum is not None and normalized < self.minimum:
            raise ValueError(f"{self.name} is below its minimum")
        if self.maximum is not None and normalized > self.maximum:
            raise ValueError(f"{self.name} is above its maximum")
        if (
            self.step is not None
            and self.minimum is not None
            and (normalized - self.minimum) % self.step
        ):
            raise ValueError(f"{self.name} does not match its configured step")
        if self.value_map and normalized not in {item[0] for item in self.value_map}:
            raise ValueError(f"{self.name} is not in its configured value map")
        return normalized


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Describe a Xiaomi BLE model and its Miot properties."""

    model: str
    name: str
    spec_type: str
    auth_type: int
    properties: tuple[PropertySpec, ...]

    def __post_init__(self) -> None:
        """Reject incomplete or ambiguous profiles."""
        if not self.model or not self.name or not self.spec_type:
            raise ValueError("Profile identity fields must not be empty")
        if self.auth_type < 0:
            raise ValueError("Authentication type must not be negative")
        keys = [prop.key for prop in self.properties]
        if len(keys) != len(set(keys)):
            raise ValueError("Profile contains duplicate SIID/PIID pairs")

    def property(self, siid: int, piid: int) -> PropertySpec:
        """Return a property by its Miot identifier."""
        try:
            return next(prop for prop in self.properties if prop.key == (siid, piid))
        except StopIteration as error:
            raise KeyError(f"Unknown property {siid}/{piid} for {self.model}") from error
