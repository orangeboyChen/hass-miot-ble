"""Tests for integration constants."""

import pytest

from custom_components.miot_ble.const import UUID_MI_SERVICE, expand_uuid


def test_expand_uuid() -> None:
    assert UUID_MI_SERVICE == "0000fe95-0000-1000-8000-00805f9b34fb"
    assert expand_uuid("ABCD").startswith("0000abcd-")


@pytest.mark.parametrize("value", ["123", "12345", "xyz!"])
def test_expand_uuid_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        expand_uuid(value)
