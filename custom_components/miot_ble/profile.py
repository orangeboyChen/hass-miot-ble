"""Declarative device profile types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class ValueType(IntEnum):
    """MIoT value types used by the encrypted BLE property protocol."""

    BOOL = 0x00
    UINT8 = 0x10
    UINT16 = 0x30

    @property
    def byte_width(self) -> int:
        """Return the encoded value width."""
        return 2 if self is ValueType.UINT16 else 1


class EntityKind(StrEnum):
    """Home Assistant entity representation requested by a property."""

    SENSOR = "sensor"
    SWITCH = "switch"
    SELECT = "select"


@dataclass(frozen=True, slots=True)
class PropertySpec:
    """Describe one MIoT property exposed by a device."""

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

    def __post_init__(self) -> None:
        """Validate profile metadata at import time."""
        if not self.name:
            raise ValueError("Property name must not be empty")
        if not 1 <= self.siid <= 255 or not 1 <= self.piid <= 255:
            raise ValueError("SIID and PIID must be between 1 and 255")
        if not (self.readable or self.writable or self.notifiable):
            raise ValueError("Property must expose at least one access mode")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Property minimum must not exceed maximum")
        if self.step is not None and self.step <= 0:
            raise ValueError("Property step must be positive")

    @property
    def key(self) -> tuple[int, int]:
        """Return the MIoT property identifier."""
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

        width_maximum = (1 << (8 * self.value_type.byte_width)) - 1
        if not 0 <= normalized <= width_maximum:
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
        return normalized


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Describe a Xiaomi BLE model and its MIoT properties."""

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
        """Return a property by its MIoT identifier."""
        try:
            return next(prop for prop in self.properties if prop.key == (siid, piid))
        except StopIteration as error:
            raise KeyError(f"Unknown property {siid}/{piid} for {self.model}") from error
