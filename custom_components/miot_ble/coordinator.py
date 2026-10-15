"""Connection and update coordinator for Xiaomi MIoT BLE."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from bleak import BleakClient
from bleak_retry_connector import establish_connection
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_MODEL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_TOKEN, DEFAULT_UPDATE_INTERVAL, DOMAIN
from .profile import DeviceProfile, PropertySpec
from .profiles import get_profile
from .protocol import AuthenticationError, MiotBleSession, PropertyValue

_LOGGER = logging.getLogger(__name__)

type PropertyData = dict[tuple[int, int], int | bool]


def async_ble_device_from_address(hass: HomeAssistant, address: str):
    """Resolve a BLE device after Home Assistant loaded Bluetooth dependencies."""
    from homeassistant.components import bluetooth

    return bluetooth.async_ble_device_from_address(hass, address, connectable=True)


class MiotBleCoordinator(DataUpdateCoordinator[PropertyData]):
    """Manage one authenticated BLE connection and its property cache."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS]
        self.profile: DeviceProfile = get_profile(entry.data[CONF_MODEL])
        self._token = bytes.fromhex(entry.data[CONF_TOKEN])
        self._client: BleakClient | None = None
        self._session: MiotBleSession | None = None
        self._connection_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.address}",
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
            always_update=False,
        )

    def _disconnected(self, client: BleakClient) -> None:
        if client is self._client:
            session = self._session
            self._client = None
            self._session = None
            if session is not None:
                session.cancel_background_tasks()
            self.hass.async_create_task(
                self.async_refresh(),
                name=f"{DOMAIN}_reconnect_{self.address}",
            )

    @property
    def _properties(self) -> tuple[PropertySpec, ...]:
        """Return properties that can produce local state."""
        return tuple(prop for prop in self.profile.properties if prop.readable or prop.notifiable)

    def _handle_property_values(
        self,
        session: MiotBleSession,
        values: tuple[PropertyValue, ...],
    ) -> None:
        """Publish property notifications from the active BLE session."""
        if session is not self._session:
            return
        data = dict(self.data or {})
        data.update((value.key, value.value) for value in values)
        self.async_set_updated_data(data)

    async def _ensure_session(self) -> MiotBleSession:
        if self._session is not None and self._client is not None and self._client.is_connected:
            self._session.raise_if_receiver_failed()
            return self._session
        async with self._connection_lock:
            if self._session is not None and self._client is not None and self._client.is_connected:
                self._session.raise_if_receiver_failed()
                return self._session
            ble_device = async_ble_device_from_address(self.hass, self.address)
            if ble_device is None:
                raise ConnectionError(
                    f"{self.address} is not in range of a Home Assistant Bluetooth adapter or proxy"
                )
            client = await establish_connection(
                BleakClient,
                ble_device,
                self.address,
                disconnected_callback=self._disconnected,
            )
            session = MiotBleSession(client, self._token, self.profile)
            self._client = client
            self._session = session
            try:
                await session.start()
                await session.login()
                await session.subscribe_properties(
                    self._properties,
                    lambda values: self._handle_property_values(session, values),
                )
            except Exception:
                if session is self._session:
                    self._session = None
                    self._client = None
                await session.async_close()
                await client.disconnect()
                raise
            return session

    async def _async_update_data(self) -> PropertyData:
        try:
            session = await self._ensure_session()
            return {key: result.value for key, result in session.property_values.items()}
        except AuthenticationError as error:
            await self._drop_connection()
            raise ConfigEntryAuthFailed("The device rejected its BLE token") from error
        except Exception as error:
            await self._drop_connection()
            raise UpdateFailed(str(error)) from error

    async def async_set_property(self, prop: PropertySpec, value: int | bool) -> None:
        """Write one property and update the coordinator cache."""
        async with self._write_lock:
            for attempt in range(2):
                try:
                    session = await self._ensure_session()
                    changed = await session.set_property(prop, value)
                    data = dict(self.data or {})
                    data[changed.key] = changed.value
                    self.async_set_updated_data(data)
                    return
                except Exception:
                    await self._drop_connection()
                    if attempt:
                        raise

    async def _drop_connection(self) -> None:
        client = self._client
        session = self._session
        self._client = None
        self._session = None
        if session is not None:
            await session.async_close()
        if client is not None and client.is_connected:
            await client.disconnect()

    async def async_shutdown(self) -> None:
        """Stop polling and disconnect from the device."""
        await super().async_shutdown()
        await self._drop_connection()
