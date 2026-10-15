"""MIoT property TLV encoder and decoder."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from ..profile import PropertySpec, ValueType

MIOT_CHANNEL = 0x20


class Operation(IntEnum):
    """MIoT BLE property operations."""

    SET = 0x00
    READ = 0x01
    SUBSCRIBE = 0x02
    SNAPSHOT = 0x03
    NOTIFY = 0x04
    OPEN = 0xF0


@dataclass(frozen=True, slots=True)
class PropertyValue:
    """A decoded MIoT property value."""

    siid: int
    piid: int
    value_type: ValueType
    value: int | bool

    @property
    def key(self) -> tuple[int, int]:
        """Return the MIoT property identifier."""
        return self.siid, self.piid


def _header(transaction_id: int, body: bytes) -> bytes:
    if not 0 <= transaction_id <= 0xFF:
        raise ValueError("Transaction ID must fit in one byte")
    total_length = len(body) + 4
    if total_length > 0xFF:
        raise ValueError("MIoT property payload is too large")
    return bytes((total_length, MIOT_CHANNEL, transaction_id, 0)) + body


def _encode_value(value_type: ValueType, value: int) -> bytes:
    return value.to_bytes(value_type.byte_width, "little", signed=value_type.signed)


def build_set(transaction_id: int, prop: PropertySpec, value: int | bool) -> bytes:
    """Build a request that writes one MIoT property."""
    if not prop.writable:
        raise ValueError(f"{prop.name} is not writable")
    normalized = prop.validate_value(value)
    encoded = _encode_value(prop.value_type, normalized)
    body = (
        bytes(
            (
                Operation.SET,
                1,
                prop.siid,
                prop.piid,
                0,
                len(encoded),
                prop.value_type,
            )
        )
        + encoded
    )
    return _header(transaction_id, body)


def build_subscribe(transaction_id: int, properties: tuple[PropertySpec, ...]) -> bytes:
    """Build a subscription request for readable or notifiable properties."""
    if not properties:
        raise ValueError("A subscription must contain at least one property")
    if len(properties) > 0xFF:
        raise ValueError("A subscription cannot contain more than 255 properties")
    if any(not (prop.readable or prop.notifiable) for prop in properties):
        raise ValueError("Subscription contains a property without read or notify access")
    pairs = b"".join(bytes((prop.siid, prop.piid, 0)) for prop in properties)
    return _header(transaction_id, bytes((Operation.SUBSCRIBE, len(properties))) + pairs)


def _decode_value(value_type_raw: int, raw: bytes) -> tuple[ValueType, int | bool]:
    try:
        value_type = ValueType(value_type_raw)
    except ValueError as error:
        raise ValueError(f"Unsupported MIoT value type: 0x{value_type_raw:02x}") from error
    if len(raw) != value_type.byte_width:
        raise ValueError("MIoT value length does not match its type")
    value = int.from_bytes(raw, "little", signed=value_type.signed)
    if value_type is ValueType.BOOL:
        if value not in (0, 1):
            raise ValueError("MIoT boolean value must be zero or one")
        return value_type, bool(value)
    return value_type, value


def _validate_payload(payload: bytes) -> Operation:
    if len(payload) < 6:
        raise ValueError("MIoT property payload is too short")
    if payload[0] != len(payload):
        raise ValueError("MIoT property payload length is inconsistent")
    if payload[1] != MIOT_CHANNEL or payload[3] != 0:
        raise ValueError("MIoT property payload header is invalid")
    try:
        return Operation(payload[4])
    except ValueError as error:
        raise ValueError(f"Unsupported MIoT operation: 0x{payload[4]:02x}") from error


def _parse_entry(payload: bytes, offset: int, snapshot: bool) -> tuple[PropertyValue, int]:
    metadata_size = 7 if snapshot else 5
    if offset + metadata_size > len(payload):
        raise ValueError("MIoT property entry is truncated")
    siid, piid = payload[offset], payload[offset + 1]
    length_offset = offset + 5 if snapshot else offset + 3
    value_length = payload[length_offset]
    type_offset = length_offset + 1
    value_offset = type_offset + 1
    end = value_offset + value_length
    if end > len(payload):
        raise ValueError("MIoT property value is truncated")
    value_type, value = _decode_value(payload[type_offset], payload[value_offset:end])
    return PropertyValue(siid, piid, value_type, value), end


def parse_properties(payload: bytes) -> tuple[PropertyValue, ...]:
    """Decode property values from a snapshot or notification payload."""
    operation = _validate_payload(payload)
    if operation is Operation.SNAPSHOT:
        expected_count = payload[5]
        offset = 6
        values: list[PropertyValue] = []
        for _ in range(expected_count):
            value, offset = _parse_entry(payload, offset, snapshot=True)
            values.append(value)
        if offset != len(payload):
            raise ValueError("MIoT snapshot contains trailing data")
        return tuple(values)
    if operation is Operation.NOTIFY:
        expected_count = payload[5]
        offset = 6
        values: list[PropertyValue] = []
        for _ in range(expected_count):
            value, offset = _parse_entry(payload, offset, snapshot=False)
            values.append(value)
        if offset != len(payload):
            raise ValueError("MIoT notification contains trailing data")
        return tuple(values)
    raise ValueError(f"MIoT operation {operation.name} does not contain property values")
