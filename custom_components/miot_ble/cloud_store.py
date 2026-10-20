"""Local storage for reusable Xiaomi Cloud sessions."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .cloud import XiaomiCloudSession
from .const import DOMAIN

_STORE_VERSION = 1
_STORE_KEY = f"{DOMAIN}.cloud_accounts"
_DATA_STORE = "cloud_account_store"


class XiaomiCloudAccountStore:
    """Persist reusable Xiaomi Cloud sessions without account passwords."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, _STORE_VERSION, _STORE_KEY)
        self._lock = asyncio.Lock()
        self._accounts: dict[str, XiaomiCloudSession] | None = None

    async def async_list(self) -> tuple[XiaomiCloudSession, ...]:
        """Return all valid saved sessions sorted by display name."""
        async with self._lock:
            await self._async_ensure_loaded()
            assert self._accounts is not None
            return tuple(sorted(self._accounts.values(), key=lambda item: item.display_name))

    async def async_get(self, account_id: str) -> XiaomiCloudSession | None:
        """Return one saved session by its stable account ID."""
        async with self._lock:
            await self._async_ensure_loaded()
            assert self._accounts is not None
            return self._accounts.get(account_id)

    async def async_save(self, cloud_session: XiaomiCloudSession) -> None:
        """Create or replace one saved Xiaomi Cloud session."""
        async with self._lock:
            await self._async_ensure_loaded()
            assert self._accounts is not None
            accounts = dict(self._accounts)
            accounts[cloud_session.account_id] = cloud_session
            await self._async_write(accounts)
            self._accounts = accounts

    async def async_remove(self, account_id: str) -> None:
        """Remove an expired or explicitly replaced Xiaomi Cloud session."""
        async with self._lock:
            await self._async_ensure_loaded()
            assert self._accounts is not None
            if account_id in self._accounts:
                accounts = dict(self._accounts)
                accounts.pop(account_id)
                await self._async_write(accounts)
                self._accounts = accounts

    async def _async_ensure_loaded(self) -> None:
        if self._accounts is not None:
            return
        data = await self._store.async_load()
        stored = data.get("accounts", {}) if isinstance(data, dict) else {}
        accounts: dict[str, XiaomiCloudSession] = {}
        changed = not isinstance(stored, dict)
        if isinstance(stored, dict):
            for account_id, value in stored.items():
                try:
                    cloud_session = XiaomiCloudSession.from_dict(value)
                except ValueError:
                    changed = True
                    continue
                if account_id != cloud_session.account_id:
                    changed = True
                accounts[cloud_session.account_id] = cloud_session
        self._accounts = accounts
        if changed:
            await self._async_write(accounts)

    async def _async_write(self, accounts: dict[str, XiaomiCloudSession]) -> None:
        await self._store.async_save(
            {
                "accounts": {
                    account_id: cloud_session.as_dict()
                    for account_id, cloud_session in accounts.items()
                }
            }
        )


def get_cloud_account_store(hass: HomeAssistant) -> XiaomiCloudAccountStore:
    """Return the shared Xiaomi Cloud account store for this Home Assistant instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get(_DATA_STORE)
    if not isinstance(store, XiaomiCloudAccountStore):
        store = XiaomiCloudAccountStore(hass)
        domain_data[_DATA_STORE] = store
    return store
