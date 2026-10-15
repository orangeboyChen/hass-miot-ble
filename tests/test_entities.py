"""Tests for dynamically generated MIoT BLE entities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTime

from custom_components.miot_ble.entity import MiotBleEntity
from custom_components.miot_ble.profile import EntityKind, PropertySpec, ValueType
from custom_components.miot_ble.profiles import MUXNW_DRYER_M73
from custom_components.miot_ble.select import MiotBleSelect
from custom_components.miot_ble.select import async_setup_entry as setup_select
from custom_components.miot_ble.sensor import MiotBleSensor
from custom_components.miot_ble.sensor import async_setup_entry as setup_sensor
from custom_components.miot_ble.switch import MiotBleSwitch
from custom_components.miot_ble.switch import async_setup_entry as setup_switch


def make_coordinator(data=None):
    """Create the coordinator attributes consumed by entities."""
    return SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF",
        profile=MUXNW_DRYER_M73,
        entry=SimpleNamespace(title="Dryer"),
        data=data or {},
        last_update_success=True,
        async_set_property=AsyncMock(),
    )


def test_base_entity_metadata_and_value() -> None:
    coordinator = make_coordinator({(2, 1): 1})
    entity = MiotBleEntity(coordinator, MUXNW_DRYER_M73.property(2, 1))
    assert entity.unique_id == "aabbccddeeff_2_1"
    assert entity.name == "Power"
    assert entity.property_value == 1
    assert entity.device_info["manufacturer"] == "Xiaomi"


@pytest.mark.asyncio
async def test_switch_entity() -> None:
    coordinator = make_coordinator({(2, 1): 1})
    entity = MiotBleSwitch(coordinator, MUXNW_DRYER_M73.property(2, 1))
    assert entity.is_on is True
    await entity.async_turn_off()
    await entity.async_turn_on()
    assert coordinator.async_set_property.await_args_list[0].args[1] == 0
    assert coordinator.async_set_property.await_args_list[1].args[1] == 1
    coordinator.data = {}
    assert entity.is_on is None


@pytest.mark.asyncio
async def test_select_entity() -> None:
    coordinator = make_coordinator({(2, 3): 3})
    entity = MiotBleSelect(coordinator, MUXNW_DRYER_M73.property(2, 3))
    assert entity.options == ["1 h", "2 h", "3 h", "4 h", "5 h", "6 h"]
    assert entity.current_option == "3 h"
    await entity.async_select_option("6 h")
    assert coordinator.async_set_property.await_args.args[1] == 6
    with pytest.raises(ValueError, match="Unsupported"):
        await entity.async_select_option("7 h")
    coordinator.data = {(2, 3): 99}
    assert entity.current_option is None

    unbounded = PropertySpec("mode", 1, 1, ValueType.UINT8, EntityKind.SELECT)
    with pytest.raises(ValueError, match="minimum and maximum"):
        MiotBleSelect(coordinator, unbounded)


def test_sensor_entity() -> None:
    coordinator = make_coordinator({(2, 4): 120})
    entity = MiotBleSensor(coordinator, MUXNW_DRYER_M73.property(2, 4))
    assert entity.native_value == 120
    assert entity.device_class is SensorDeviceClass.DURATION
    assert entity.native_unit_of_measurement is UnitOfTime.SECONDS
    assert entity.state_class is SensorStateClass.MEASUREMENT


@pytest.mark.asyncio
async def test_platform_setup_functions() -> None:
    coordinator = make_coordinator()
    entry = SimpleNamespace(runtime_data=coordinator)
    entities: list[object] = []
    await setup_switch(None, entry, entities.extend)
    await setup_select(None, entry, entities.extend)
    await setup_sensor(None, entry, entities.extend)
    assert [type(entity) for entity in entities] == [
        MiotBleSwitch,
        MiotBleSwitch,
        MiotBleSelect,
        MiotBleSensor,
    ]
