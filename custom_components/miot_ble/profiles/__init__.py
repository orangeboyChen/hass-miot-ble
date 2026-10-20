"""Built-in Xiaomi Miot BLE device profiles."""

from __future__ import annotations

from collections.abc import Iterator

from ..profile import DeviceProfile
from .muxnw_dryer_m73 import PROFILE as MUXNW_DRYER_M73
from .xiaomi_magic_touch_yb01 import PROFILE as XIAOMI_MAGIC_TOUCH_YB01
from .xiaomi_magic_touch_yb11 import PROFILE as XIAOMI_MAGIC_TOUCH_YB11

_PROFILES = {
    profile.model: profile
    for profile in (MUXNW_DRYER_M73, XIAOMI_MAGIC_TOUCH_YB01, XIAOMI_MAGIC_TOUCH_YB11)
}
_PRODUCT_ID_PROFILES = {
    0x46C6: XIAOMI_MAGIC_TOUCH_YB01,
    0x5A5B: MUXNW_DRYER_M73,
    0x8FD3: XIAOMI_MAGIC_TOUCH_YB11,
}


def get_profile(model: str) -> DeviceProfile:
    """Return the profile registered for a Xiaomi model."""
    try:
        return _PROFILES[model]
    except KeyError as error:
        raise KeyError(f"Unsupported Xiaomi BLE model: {model}") from error


def iter_profiles() -> Iterator[DeviceProfile]:
    """Iterate over all built-in profiles in model order."""
    yield from (_PROFILES[model] for model in sorted(_PROFILES))


def profile_for_product_id(product_id: int) -> DeviceProfile | None:
    """Return the profile registered for a Xiaomi Bluetooth product ID."""
    return _PRODUCT_ID_PROFILES.get(product_id)


__all__ = [
    "MUXNW_DRYER_M73",
    "XIAOMI_MAGIC_TOUCH_YB01",
    "XIAOMI_MAGIC_TOUCH_YB11",
    "get_profile",
    "iter_profiles",
    "profile_for_product_id",
]
