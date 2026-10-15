"""Config flow for Xiaomi MIoT BLE."""

from __future__ import annotations

import logging
import re
from typing import Any, override

import voluptuous as vol
from bleak import BleakClient
from bleak_retry_connector import establish_connection
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_MODEL, CONF_NAME
from homeassistant.helpers import config_validation as cv

from .const import CONF_TOKEN, DOMAIN, UUID_MI_SERVICE
from .profiles import get_profile, iter_profiles
from .protocol import AuthenticationError, MiotBleSession

_LOGGER = logging.getLogger(__name__)
_TOKEN_PATTERN = re.compile(r"[0-9a-fA-F]{24}")


def async_discovered_service_info(hass: Any) -> Any:
    """Return connectable discoveries without importing Bluetooth during module load."""
    from homeassistant.components import bluetooth

    return bluetooth.async_discovered_service_info(hass, connectable=True)


def async_ble_device_from_address(hass: Any, address: str) -> Any:
    """Resolve a BLE device without importing Bluetooth during module load."""
    from homeassistant.components import bluetooth

    return bluetooth.async_ble_device_from_address(hass, address, connectable=True)


def normalize_token(value: str) -> str:
    """Normalize and validate a 12-byte Xiaomi BLE token."""
    token = value.strip().lower()
    if not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError("A Xiaomi BLE token must contain 24 hexadecimal characters")
    return token


def _supports_miot_service(info: BluetoothServiceInfoBleak) -> bool:
    return UUID_MI_SERVICE in info.service_uuids or UUID_MI_SERVICE in info.service_data


def _discovered_devices(hass: Any) -> dict[str, str]:
    devices: dict[str, str] = {}
    for info in async_discovered_service_info(hass):
        if _supports_miot_service(info):
            devices[info.address] = info.name or info.address
    return devices


async def async_validate_connection(
    hass: Any,
    address: str,
    token: bytes,
    model: str,
) -> None:
    """Connect and authenticate before saving a config entry."""
    ble_device = async_ble_device_from_address(hass, address)
    if ble_device is None:
        raise ConnectionError(f"Bluetooth device {address} is not currently available")
    client: BleakClient | None = None
    try:
        client = await establish_connection(BleakClient, ble_device, address)
        session = MiotBleSession(client, token, get_profile(model))
        await session.start()
        await session.login()
    finally:
        if client is not None and client.is_connected:
            await client.disconnect()


class MiotBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a Xiaomi MIoT BLE device."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_address: str | None = None
        self._discovered_name: str | None = None

    @override
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a connectable FE95 Bluetooth discovery."""
        if not _supports_miot_service(discovery_info):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovered_address = discovery_info.address
        self._discovered_name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": self._discovered_name}
        return await self.async_step_user()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the model, address, and bound-device token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = str(user_input[CONF_ADDRESS]).strip()
            model = str(user_input[CONF_MODEL])
            try:
                token = normalize_token(str(user_input[CONF_TOKEN]))
            except ValueError:
                errors[CONF_TOKEN] = "invalid_token"
            else:
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                try:
                    await async_validate_connection(
                        self.hass,
                        address,
                        bytes.fromhex(token),
                        model,
                    )
                except AuthenticationError:
                    errors["base"] = "invalid_auth"
                except Exception as error:
                    _LOGGER.debug("Unable to connect to %s: %s", address, error)
                    errors["base"] = "cannot_connect"
                else:
                    profile = get_profile(model)
                    name = str(user_input.get(CONF_NAME) or self._discovered_name or profile.name)
                    return self.async_create_entry(
                        title=name,
                        data={
                            CONF_ADDRESS: address,
                            CONF_MODEL: model,
                            CONF_NAME: name,
                            CONF_TOKEN: token,
                        },
                    )

        values = user_input or {}
        devices = _discovered_devices(self.hass)
        if self._discovered_address:
            devices.setdefault(
                self._discovered_address,
                self._discovered_name or self._discovered_address,
            )
        address_default = self._discovered_address or values.get(CONF_ADDRESS, "")
        address_validator: Any = vol.In(devices) if devices else cv.string
        models = {profile.model: profile.name for profile in iter_profiles()}
        model_default = values.get(CONF_MODEL, next(iter(models)))
        name_default = values.get(CONF_NAME, self._discovered_name or "")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS, default=address_default): address_validator,
                    vol.Required(CONF_MODEL, default=model_default): vol.In(models),
                    vol.Required(CONF_TOKEN, default=values.get(CONF_TOKEN, "")): cv.string,
                    vol.Optional(CONF_NAME, default=name_default): cv.string,
                }
            ),
            errors=errors,
        )
