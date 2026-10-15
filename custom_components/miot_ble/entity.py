"""Shared Xiaomi MIoT BLE entity implementation."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MiotBleCoordinator
from .profile import PropertySpec


class MiotBleEntity(CoordinatorEntity[MiotBleCoordinator]):
    """Represent one MIoT property in Home Assistant."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MiotBleCoordinator, prop: PropertySpec) -> None:
        super().__init__(coordinator)
        self.property_spec = prop
        address = coordinator.address.replace(":", "").lower()
        self._attr_unique_id = f"{address}_{prop.siid}_{prop.piid}"
        self._attr_name = prop.name.replace("_", " ").title()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            manufacturer="Xiaomi",
            model=coordinator.profile.name,
            name=coordinator.entry.title,
        )

    @property
    def property_value(self) -> int | bool | None:
        """Return the last value received for this entity's property."""
        return (self.coordinator.data or {}).get(self.property_spec.key)
