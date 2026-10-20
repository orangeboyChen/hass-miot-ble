"""Tests for reusable Xiaomi Cloud session storage."""

import base64
from unittest.mock import AsyncMock

import pytest

from custom_components.miot_ble.cloud import XiaomiCloudSession
from custom_components.miot_ble.cloud_store import (
    XiaomiCloudAccountStore,
    get_cloud_account_store,
)


def cloud_session(user_id: str = "123") -> XiaomiCloudSession:
    """Build one persistable cloud session."""
    return XiaomiCloudSession(
        username=f"user-{user_id}@example.com",
        user_id=user_id,
        service_token=f"SERVICE-{user_id}",
        ssecurity=base64.b64encode(f"security-{user_id}".encode()).decode(),
        device_id=f"DEVICE{user_id}",
    )


@pytest.mark.asyncio
async def test_store_saves_lists_gets_and_removes_accounts(hass) -> None:
    store = get_cloud_account_store(hass)
    assert get_cloud_account_store(hass) is store
    assert await store.async_list() == ()

    second = cloud_session("456")
    first = cloud_session("123")
    await store.async_save(second)
    await store.async_save(first)
    assert await store.async_list() == (first, second)
    assert await store.async_get("123") == first
    assert await store.async_get("missing") is None

    replacement = XiaomiCloudSession(
        username=first.username,
        user_id=first.user_id,
        service_token="NEW-SERVICE",
        ssecurity=first.ssecurity,
        device_id=first.device_id,
    )
    await store.async_save(replacement)
    assert await store.async_get("123") == replacement

    await store.async_remove("missing")
    await store.async_remove("123")
    assert await store.async_list() == (second,)


@pytest.mark.asyncio
async def test_store_discards_invalid_and_normalizes_mismatched_keys(hass) -> None:
    expected = cloud_session()
    store = XiaomiCloudAccountStore(hass)
    await store._store.async_save(
        {
            "accounts": {
                "wrong-key": expected.as_dict(),
                "invalid": {"username": "incomplete"},
            }
        }
    )

    assert await store.async_list() == (expected,)
    saved = await store._store.async_load()
    assert saved == {"accounts": {expected.account_id: expected.as_dict()}}


@pytest.mark.asyncio
async def test_store_recovers_from_invalid_root_data(hass) -> None:
    store = XiaomiCloudAccountStore(hass)
    await store._store.async_save({"accounts": []})
    assert await store.async_list() == ()
    assert await store._store.async_load() == {"accounts": {}}


@pytest.mark.asyncio
async def test_failed_write_does_not_change_memory_cache(hass) -> None:
    store = XiaomiCloudAccountStore(hass)
    assert await store.async_list() == ()
    store._store.async_save = AsyncMock(side_effect=OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        await store.async_save(cloud_session())
    assert await store.async_list() == ()
