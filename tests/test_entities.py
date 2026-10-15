"""Tests for dynamically generated MIoT BLE entities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTime

from custom_components.miot_ble.binary_sensor import MiotBleBinarySensor
from custom_components.miot_ble.binary_sensor import async_setup_entry as setup_binary_sensor
from custom_components.miot_ble.entity import MiotBleEntity
from custom_components.miot_ble.number import MiotBleNumber
from custom_components.miot_ble.number import async_setup_entry as setup_number
from custom_components.miot_ble.profile import EntityKind, PropertySpec, ValueType
from custom_components.miot_ble.profiles import MUXNW_DRYER_M73
from custom_components.miot_ble.select import MiotBleSelect
from custom_components.miot_ble.select import async_setup_entry as setup_select
from custom_components.miot_ble.sensor import MiotBleSensor
from custom_components.miot_ble.sensor import async_setup_entry as setup_sensor
from custom_components.miot_ble.switch import MiotBleSwitch
from custom_components.miot_ble.switch import async_setup_entry as setup_switch


def make_coordinator(data=None, profile=MUXNW_DRYER_M73):
    """Create the coordinator attributes consumed by entities."""
    return SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF",
        profile=profile,
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

    mapped = PropertySpec(
        "mode",
        1,
        2,
        ValueType.UINT8,
        EntityKind.SELECT,
        value_map=((0, "Off"), (2, "Auto")),
    )
    coordinator.data = {(1, 2): 2}
    entity = MiotBleSelect(coordinator, mapped)
    assert entity.options == ["Off", "Auto"]
    assert entity.current_option == "Auto"


def test_sensor_entity() -> None:
    coordinator = make_coordinator({(2, 4): 120})
    entity = MiotBleSensor(coordinator, MUXNW_DRYER_M73.property(2, 4))
    assert entity.native_value == 120
    assert entity.device_class is SensorDeviceClass.DURATION
    assert entity.native_unit_of_measurement is UnitOfTime.SECONDS
    assert entity.state_class is SensorStateClass.MEASUREMENT


@pytest.mark.asyncio
async def test_number_and_binary_sensor_entities() -> None:
    number = PropertySpec(
        "target",
        3,
        1,
        ValueType.INT16,
        EntityKind.NUMBER,
        writable=True,
        minimum=-10,
        maximum=10,
        step=2,
        unit="°C",
    )
    active = PropertySpec("active", 3, 2, ValueType.BOOL, EntityKind.BINARY_SENSOR)
    coordinator = make_coordinator({number.key: 4, active.key: 1})

    number_entity = MiotBleNumber(coordinator, number)
    assert number_entity.native_value == 4
    assert number_entity.native_min_value == -10
    assert number_entity.native_max_value == 10
    assert number_entity.native_step == 2
    await number_entity.async_set_native_value(6.0)
    assert coordinator.async_set_property.await_args.args[1] == 6
    with pytest.raises(ValueError, match="integer"):
        await number_entity.async_set_native_value(1.5)

    binary_entity = MiotBleBinarySensor(coordinator, active)
    assert isinstance(binary_entity, BinarySensorEntity)
    assert binary_entity.is_on is True
    coordinator.data = {}
    assert binary_entity.is_on is None

    unbounded = PropertySpec("target", 3, 3, ValueType.INT16, EntityKind.NUMBER)
    with pytest.raises(ValueError, match="minimum and maximum"):
        MiotBleNumber(coordinator, unbounded)


@pytest.mark.asyncio
async def test_platform_setup_functions() -> None:
    coordinator = make_coordinator()
    entry = SimpleNamespace(runtime_data=coordinator)
    entities: list[object] = []
    await setup_switch(None, entry, entities.extend)
    await setup_select(None, entry, entities.extend)
    await setup_sensor(None, entry, entities.extend)
    await setup_binary_sensor(None, entry, entities.extend)
    await setup_number(None, entry, entities.extend)
    assert [type(entity) for entity in entities] == [
        MiotBleSwitch,
        MiotBleSwitch,
        MiotBleSelect,
        MiotBleSensor,
    ]

    number = PropertySpec(
        "target",
        3,
        1,
        ValueType.INT16,
        EntityKind.NUMBER,
        writable=True,
        minimum=-10,
        maximum=10,
    )
    active = PropertySpec("active", 3, 2, ValueType.BOOL, EntityKind.BINARY_SENSOR)
    profile = SimpleNamespace(name="Generic", properties=(number, active))
    entry.runtime_data = make_coordinator(profile=profile)
    generated: list[object] = []
    await setup_number(None, entry, generated.extend)
    await setup_binary_sensor(None, entry, generated.extend)
    assert [type(entity) for entity in generated] == [MiotBleNumber, MiotBleBinarySensor]
