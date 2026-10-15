"""Config flow for Xiaomi MIoT BLE."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, override

import aiohttp
import voluptuous as vol
from bleak import BleakClient
from bleak_retry_connector import establish_connection
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_MODEL, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .cloud import (
    XiaomiCloudAuthenticationError,
    XiaomiCloudCaptchaError,
    XiaomiCloudCaptchaRequiredError,
    XiaomiCloudClient,
    XiaomiCloudError,
    XiaomiCloudVerificationError,
    XiaomiCloudVerificationRequiredError,
)
from .const import CONF_TOKEN, DOMAIN, UUID_MI_SERVICE
from .profiles import get_profile, iter_profiles
from .protocol import AuthenticationError, MiotBleSession

_LOGGER = logging.getLogger(__name__)
_TOKEN_PATTERN = re.compile(r"[0-9a-fA-F]{24}")
_XIAOMI_BLE_DOMAIN = "xiaomi_ble"
_CONF_BINDKEY = "bindkey"


class CloudDeviceNotFoundError(Exception):
    """Raised when Xiaomi Cloud does not return the selected BLE device."""


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


def find_existing_token(hass: Any, address: str) -> str | None:
    """Return a legacy bindkey stored by Home Assistant's Xiaomi BLE integration."""
    normalized_address = address.replace(":", "").lower()
    for entry in hass.config_entries.async_entries(_XIAOMI_BLE_DOMAIN):
        entry_address = str(entry.unique_id or entry.data.get(CONF_ADDRESS, ""))
        if entry_address.replace(":", "").lower() != normalized_address:
            continue
        try:
            return normalize_token(str(entry.data.get(_CONF_BINDKEY, "")))
        except ValueError:
            continue
    return None


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


async def async_fetch_cloud_token(client: XiaomiCloudClient, address: str) -> str:
    """Fetch and validate a BLE key using an authenticated cloud client."""
    from xiaomi_ble import (
        XiaomiCloudException,
        XiaomiCloudInvalidAuthenticationException,
    )

    try:
        token = await client.async_get_device_token(address)
    except XiaomiCloudInvalidAuthenticationException as error:
        raise XiaomiCloudAuthenticationError from error
    except XiaomiCloudException as error:
        raise XiaomiCloudError("Unable to query Xiaomi Cloud") from error
    if token is None:
        raise CloudDeviceNotFoundError
    return normalize_token(token)


class MiotBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a Xiaomi MIoT BLE device."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_address: str | None = None
        self._discovered_name: str | None = None
        self._pending_setup: dict[str, str] | None = None
        self._cloud_client: XiaomiCloudClient | None = None

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
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the model, address, and bound-device token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = str(user_input[CONF_ADDRESS]).strip()
            model = str(user_input[CONF_MODEL])
            profile = get_profile(model)
            name = str(user_input.get(CONF_NAME) or self._discovered_name or profile.name)
            self._pending_setup = {
                CONF_ADDRESS: address,
                CONF_MODEL: model,
                CONF_NAME: name,
            }
            supplied_token = str(user_input.get(CONF_TOKEN, ""))
            try:
                token = (
                    normalize_token(supplied_token)
                    if supplied_token.strip()
                    else find_existing_token(self.hass, address)
                )
            except ValueError:
                errors[CONF_TOKEN] = "invalid_token"
            else:
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                if token is None:
                    return await self.async_step_cloud_auth()
                else:
                    result, error = await self._async_finish_setup(token)
                    if result is not None:
                        return result
                    errors["base"] = error

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
                    vol.Optional(CONF_TOKEN, default=values.get(CONF_TOKEN, "")): cv.string,
                    vol.Optional(CONF_NAME, default=name_default): cv.string,
                }
            ),
            errors=errors,
        )

    async def async_step_cloud_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fetch the selected device's BLE key from Xiaomi Cloud."""
        if self._pending_setup is None:
            return self.async_abort(reason="missing_setup_data")
        errors: dict[str, str] = {}
        if user_input is not None:
            self._clear_cloud_client()
            self._cloud_client = XiaomiCloudClient(
                str(user_input[CONF_USERNAME]),
                str(user_input[CONF_PASSWORD]),
                async_create_clientsession(
                    self.hass,
                    auto_cleanup=False,
                    cookie_jar=aiohttp.DummyCookieJar(),
                ),
                owns_session=True,
            )
            try:
                await self._cloud_client.async_login()
            except XiaomiCloudCaptchaRequiredError:
                return await self.async_step_cloud_captcha()
            except XiaomiCloudVerificationRequiredError:
                return await self.async_step_cloud_verify()
            except XiaomiCloudAuthenticationError:
                self._clear_cloud_client()
                errors["base"] = "cloud_invalid_auth"
            except (aiohttp.ClientError, TimeoutError, XiaomiCloudError) as error:
                _LOGGER.debug("Unable to authenticate with Xiaomi Cloud: %s", error)
                self._clear_cloud_client()
                errors["base"] = "cloud_error"
            else:
                return await self._async_complete_cloud_login()

        return self._show_cloud_auth(errors)

    async def async_step_cloud_captcha(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Continue Xiaomi Cloud login with an image captcha."""
        client = self._cloud_client
        if self._pending_setup is None or client is None or not client.captcha_image:
            return self.async_abort(reason="missing_cloud_challenge")
        errors: dict[str, str] = {}
        if user_input is not None:
            captcha = str(user_input.get("captcha") or "").strip()
            if not captcha:
                errors["base"] = "cloud_invalid_captcha"
            else:
                try:
                    await client.async_login(captcha=captcha)
                except XiaomiCloudCaptchaError, XiaomiCloudCaptchaRequiredError:
                    errors["base"] = "cloud_invalid_captcha"
                except XiaomiCloudVerificationRequiredError:
                    return await self.async_step_cloud_verify()
                except XiaomiCloudAuthenticationError:
                    self._clear_cloud_client()
                    return self._show_cloud_auth({"base": "cloud_invalid_auth"})
                except (aiohttp.ClientError, TimeoutError, XiaomiCloudError) as error:
                    _LOGGER.debug("Unable to submit Xiaomi captcha: %s", error)
                    errors["base"] = "cloud_error"
                else:
                    return await self._async_complete_cloud_login()

        return self.async_show_form(
            step_id="cloud_captcha",
            data_schema=vol.Schema({vol.Required("captcha"): cv.string}),
            errors=errors,
            description_placeholders={
                "name": self._pending_setup[CONF_NAME],
                "captcha_image": f"data:image/jpeg;base64,{client.captcha_image}",
            },
        )

    async def async_step_cloud_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Continue Xiaomi Cloud login with an identity verification ticket."""
        client = self._cloud_client
        if self._pending_setup is None or client is None or not client.verification_url:
            return self.async_abort(reason="missing_cloud_challenge")
        errors: dict[str, str] = {}
        if user_input is not None:
            ticket = str(user_input.get("verification_ticket") or "").strip()
            if not ticket:
                errors["base"] = "cloud_invalid_verification"
            else:
                try:
                    await client.async_login(verification_ticket=ticket)
                except XiaomiCloudVerificationError:
                    errors["base"] = "cloud_invalid_verification"
                except XiaomiCloudCaptchaRequiredError:
                    return await self.async_step_cloud_captcha()
                except XiaomiCloudAuthenticationError:
                    self._clear_cloud_client()
                    return self._show_cloud_auth({"base": "cloud_invalid_auth"})
                except (aiohttp.ClientError, TimeoutError, XiaomiCloudError) as error:
                    _LOGGER.debug("Unable to submit Xiaomi verification ticket: %s", error)
                    errors["base"] = "cloud_error"
                else:
                    return await self._async_complete_cloud_login()

        return self.async_show_form(
            step_id="cloud_verify",
            data_schema=vol.Schema({vol.Required("verification_ticket"): cv.string}),
            errors=errors,
            description_placeholders={
                "name": self._pending_setup[CONF_NAME],
                "verification_url": client.verification_url,
            },
        )

    async def _async_complete_cloud_login(self) -> ConfigFlowResult:
        """Retrieve the key and finish setup after cloud authentication."""
        assert self._pending_setup is not None
        assert self._cloud_client is not None
        try:
            token = await async_fetch_cloud_token(
                self._cloud_client,
                self._pending_setup[CONF_ADDRESS],
            )
        except XiaomiCloudAuthenticationError:
            error = "cloud_invalid_auth"
        except CloudDeviceNotFoundError:
            error = "cloud_device_not_found"
        except (aiohttp.ClientError, TimeoutError, XiaomiCloudError) as cloud_error:
            _LOGGER.debug("Unable to query Xiaomi Cloud: %s", cloud_error)
            error = "cloud_error"
        except ValueError:
            error = "cloud_key_unsupported"
        else:
            result, error = await self._async_finish_setup(token)
            if result is not None:
                self._clear_cloud_client()
                return result

        self._clear_cloud_client()
        return self._show_cloud_auth({"base": error})

    def _show_cloud_auth(self, errors: dict[str, str]) -> ConfigFlowResult:
        """Show the Xiaomi account form without persisting its values."""
        assert self._pending_setup is not None

        return self.async_show_form(
            step_id="cloud_auth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): cv.string,
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"name": self._pending_setup[CONF_NAME]},
        )

    def _clear_cloud_client(self) -> None:
        """Remove transient Xiaomi account and challenge state."""
        if self._cloud_client is not None:
            self._cloud_client.clear_sensitive_data()
        self._cloud_client = None

    @callback
    @override
    def async_remove(self) -> None:
        """Clear transient account data when Home Assistant removes the flow."""
        self._clear_cloud_client()
        super().async_remove()

    async def _async_finish_setup(self, token: str) -> tuple[ConfigFlowResult | None, str]:
        """Authenticate with a BLE token and create the pending config entry."""
        assert self._pending_setup is not None
        address = self._pending_setup[CONF_ADDRESS]
        try:
            await async_validate_connection(
                self.hass,
                address,
                bytes.fromhex(token),
                self._pending_setup[CONF_MODEL],
            )
        except AuthenticationError:
            return None, "invalid_auth"
        except Exception as error:
            _LOGGER.debug("Unable to connect to %s: %s", address, error)
            return None, "cannot_connect"
        return (
            self.async_create_entry(
                title=self._pending_setup[CONF_NAME],
                data={**self._pending_setup, CONF_TOKEN: token},
            ),
            "",
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start token replacement for an existing device."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and save a replacement bound-device token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token = normalize_token(str(user_input[CONF_TOKEN]))
            except ValueError:
                errors[CONF_TOKEN] = "invalid_token"
            else:
                try:
                    await async_validate_connection(
                        self.hass,
                        entry.data[CONF_ADDRESS],
                        bytes.fromhex(token),
                        entry.data[CONF_MODEL],
                    )
                except AuthenticationError:
                    errors["base"] = "invalid_auth"
                except Exception as error:
                    _LOGGER.debug("Unable to validate replacement token: %s", error)
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_TOKEN: token},
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): cv.string}),
            errors=errors,
            description_placeholders={"name": entry.title},
        )
