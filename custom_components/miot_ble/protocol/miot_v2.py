"""MIoT Spec-v2 message encoding used by Xiaomi BLE Mesh devices."""

from __future__ import annotations

from ..profile import PropertySpec, ValueType
from .tlv import PropertyValue

_V2_TYPES = {
    ValueType.BOOL: 0,
    ValueType.UINT8: 1,
    ValueType.INT8: 2,
    ValueType.UINT16: 3,
    ValueType.INT16: 4,
    ValueType.UINT32: 5,
    ValueType.INT32: 6,
    ValueType.UINT64: 7,
    ValueType.INT64: 8,
}
_TYPE_VALUES = {value: key for key, value in _V2_TYPES.items()}


def build_message(transaction_id: int, opcode: int, body: bytes = b"") -> bytes:
    """Build one MIoT Spec-v2 packet."""
    length = 5 + len(body)
    if not 0 <= transaction_id <= 0xFFFF:
        raise ValueError("Transaction ID must fit in an unsigned 16-bit integer")
    if length > 0x0FFF:
        raise ValueError("MIoT Spec-v2 packet is too large")
    return (
        (0x2000 | length).to_bytes(2, "little")
        + transaction_id.to_bytes(2, "little")
        + bytes((opcode,))
        + body
    )


def build_get(transaction_id: int, properties: tuple[PropertySpec, ...]) -> bytes:
    """Build a GetProperty request."""
    if not properties or len(properties) > 0xFF:
        raise ValueError("GetProperty requires between one and 255 properties")
    body = bytes((len(properties),)) + b"".join(
        bytes((prop.siid,)) + prop.piid.to_bytes(2, "little") for prop in properties
    )
    return build_message(transaction_id, 2, body)


def build_set(transaction_id: int, prop: PropertySpec, value: int | bool) -> bytes:
    """Build a SetProperty request."""
    if not prop.writable:
        raise ValueError(f"{prop.name} is not writable")
    normalized = prop.validate_value(value)
    encoded = normalized.to_bytes(
        prop.value_type.byte_width, "little", signed=prop.value_type.signed
    )
    type_length = (_V2_TYPES[prop.value_type] << 12) | len(encoded)
    body = (
        bytes((1, prop.siid))
        + prop.piid.to_bytes(2, "little")
        + type_length.to_bytes(2, "little")
        + encoded
    )
    return build_message(transaction_id, 0, body)


def parse_properties(payload: bytes) -> tuple[int, tuple[PropertyValue, ...]]:
    """Parse GetPropertyResp or PropertyChanged payloads."""
    if len(payload) < 6:
        raise ValueError("MIoT Spec-v2 packet is too short")
    header = int.from_bytes(payload[:2], "little")
    if header >> 12 != 2 or header & 0x0FFF != len(payload):
        raise ValueError("MIoT Spec-v2 packet header is invalid")
    opcode = payload[4]
    body = payload[5:]
    if opcode not in (3, 4) or not body:
        raise ValueError("MIoT Spec-v2 packet does not contain property values")
    count = body[0]
    offset = 1
    values: list[PropertyValue] = []
    for _ in range(count):
        if opcode == 3:
            if offset + 5 > len(body):
                raise ValueError("MIoT Spec-v2 property result is truncated")
            siid = body[offset]
            piid = int.from_bytes(body[offset + 1 : offset + 3], "little")
            status = int.from_bytes(body[offset + 3 : offset + 5], "little")
            offset += 5
            if status:
                continue
        else:
            if offset + 3 > len(body):
                raise ValueError("MIoT Spec-v2 property notification is truncated")
            siid = body[offset]
            piid = int.from_bytes(body[offset + 1 : offset + 3], "little")
            offset += 3
        if offset + 2 > len(body):
            raise ValueError("MIoT Spec-v2 property type is truncated")
        type_length = int.from_bytes(body[offset : offset + 2], "little")
        value_type_raw = type_length >> 12
        value_length = type_length & 0x0FFF
        offset += 2
        end = offset + value_length
        if end > len(body):
            raise ValueError("MIoT Spec-v2 property value is truncated")
        try:
            value_type = _TYPE_VALUES[value_type_raw]
        except KeyError as error:
            raise ValueError(f"Unsupported MIoT Spec-v2 value type: {value_type_raw}") from error
        if value_length != value_type.byte_width:
            raise ValueError("MIoT Spec-v2 property value has an invalid length")
        raw_value = int.from_bytes(body[offset:end], "little", signed=value_type.signed)
        if value_type is ValueType.BOOL:
            if raw_value not in (0, 1):
                raise ValueError("MIoT Spec-v2 boolean property is invalid")
            value: int | bool = bool(raw_value)
        else:
            value = raw_value
        values.append(PropertyValue(siid, piid, value_type, value))
        offset = end
    if offset != len(body):
        raise ValueError("MIoT Spec-v2 packet contains trailing data")
    return int.from_bytes(payload[2:4], "little"), tuple(values)


def validate_transaction(payload: bytes, transaction_id: int, opcode: int) -> None:
    """Validate a response packet's transaction and opcode."""
    if len(payload) < 5:
        raise ValueError("MIoT Spec-v2 response is truncated")
    if int.from_bytes(payload[2:4], "little") != transaction_id or payload[4] != opcode:
        raise ValueError("MIoT Spec-v2 response does not match its request")


def parse_set_response(payload: bytes) -> tuple[int, tuple[tuple[int, int, int], ...]]:
    """Parse SetPropertyResp status entries."""
    if len(payload) < 6:
        raise ValueError("MIoT Spec-v2 set response is truncated")
    header = int.from_bytes(payload[:2], "little")
    if header >> 12 != 2 or header & 0x0FFF != len(payload) or payload[4] != 1:
        raise ValueError("MIoT Spec-v2 set response header is invalid")
    count = payload[5]
    offset = 6
    results: list[tuple[int, int, int]] = []
    for _ in range(count):
        if offset + 5 > len(payload):
            raise ValueError("MIoT Spec-v2 set result is truncated")
        siid = payload[offset]
        piid = int.from_bytes(payload[offset + 1 : offset + 3], "little")
        code = int.from_bytes(payload[offset + 3 : offset + 5], "little")
        results.append((siid, piid, code))
        offset += 5
    if offset != len(payload):
        raise ValueError("MIoT Spec-v2 set response contains trailing data")
    return int.from_bytes(payload[2:4], "little"), tuple(results)
