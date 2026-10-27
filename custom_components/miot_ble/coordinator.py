"""Connection and update coordinator for Xiaomi Miot BLE."""

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
        self._manually_disconnected = False
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
            _LOGGER.warning("Xiaomi BLE device %s disconnected unexpectedly", self.address)
            session = self._session
            self._client = None
            self._session = None
            if session is not None:
                session.cancel_background_tasks()
            self.async_update_listeners()
            if self._manually_disconnected:
                return
            self.hass.async_create_task(
                self.async_refresh(),
                name=f"{DOMAIN}_reconnect_{self.address}",
            )

    @property
    def connected(self) -> bool:
        """Return whether the authenticated property channel is usable."""
        return bool(
            not self._manually_disconnected
            and self._client is not None
            and self._client.is_connected
            and self._session is not None
            and self._session.authenticated
            and self._session.receiver_running
        )

    @property
    def manually_disconnected(self) -> bool:
        """Return whether automatic reconnection is paused by the user."""
        return self._manually_disconnected

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
        if self._manually_disconnected:
            raise ConnectionError("Bluetooth connection is manually disconnected")
        if self.connected:
            assert self._session is not None
            self._session.raise_if_receiver_failed()
            return self._session
        async with self._connection_lock:
            if self.connected:
                assert self._session is not None
                self._session.raise_if_receiver_failed()
                return self._session
            if self._session is not None or self._client is not None:
                await self._drop_connection()
            ble_device = async_ble_device_from_address(self.hass, self.address)
            if ble_device is None:
                _LOGGER.warning(
                    "Xiaomi BLE device %s is not available to a Home Assistant "
                    "Bluetooth adapter or proxy",
                    self.address,
                )
                raise ConnectionError(
                    f"{self.address} is not in range of a Home Assistant Bluetooth adapter or proxy"
                )
            try:
                client = await establish_connection(
                    BleakClient,
                    ble_device,
                    self.address,
                    disconnected_callback=self._disconnected,
                )
            except Exception as error:
                _LOGGER.warning(
                    "Failed to establish a GATT connection to Xiaomi BLE device %s: %s",
                    self.address,
                    error,
                    exc_info=True,
                )
                raise
            session = MiotBleSession(client, self._token, self.profile)
            self._client = client
            self._session = session
            stage = "starting the Miot protocol session"
            try:
                await session.start()
                stage = "authenticating with the device"
                await session.login()
                stage = "subscribing to property notifications"
                await session.subscribe_properties(
                    self._properties,
                    lambda values: self._handle_property_values(session, values),
                )
                self.async_update_listeners()
            except Exception as error:
                _LOGGER.warning(
                    "Xiaomi BLE device %s failed while %s: %s",
                    self.address,
                    stage,
                    error,
                    exc_info=True,
                )
                if session is self._session:
                    self._session = None
                    self._client = None
                await session.async_close()
                await client.disconnect()
                raise
            return session

    async def _async_update_data(self) -> PropertyData:
        if self._manually_disconnected:
            return dict(self.data or {})
        try:
            session = await self._ensure_session()
            return {key: result.value for key, result in session.property_values.items()}
        except AuthenticationError as error:
            _LOGGER.warning(
                "Xiaomi BLE device %s rejected authentication: %s",
                self.address,
                error,
                exc_info=True,
            )
            await self._drop_connection()
            raise ConfigEntryAuthFailed("The device rejected its BLE token") from error
        except Exception as error:
            _LOGGER.warning(
                "Xiaomi BLE update failed for %s: %s",
                self.address,
                error,
                exc_info=True,
            )
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
                except Exception as error:
                    _LOGGER.warning(
                        "Xiaomi BLE write failed for %s (attempt %d/2): %s",
                        self.address,
                        attempt + 1,
                        error,
                        exc_info=True,
                    )
                    await self._drop_connection()
                    if attempt:
                        raise

    async def async_connect(self) -> None:
        """Resume automatic connection management and connect now."""
        self._manually_disconnected = False
        try:
            session = await self._ensure_session()
        except Exception:
            self.async_update_listeners()
            raise
        self.async_set_updated_data(
            {key: result.value for key, result in session.property_values.items()}
        )

    async def async_disconnect(self) -> None:
        """Disconnect and pause automatic reconnection."""
        self._manually_disconnected = True
        await self._drop_connection()

    async def _drop_connection(self) -> None:
        client = self._client
        session = self._session
        self._client = None
        self._session = None
        self.async_update_listeners()
        if session is not None:
            await session.async_close()
        if client is not None and client.is_connected:
            await client.disconnect()

    async def async_shutdown(self) -> None:
        """Stop polling and disconnect from the device."""
        await super().async_shutdown()
        await self._drop_connection()
