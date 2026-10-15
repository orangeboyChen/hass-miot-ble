"""Built-in Xiaomi MIoT BLE device profiles."""

from __future__ import annotations

from collections.abc import Iterator

from ..profile import DeviceProfile
from .muxnw_dryer_m73 import PROFILE as MUXNW_DRYER_M73

_PROFILES = {MUXNW_DRYER_M73.model: MUXNW_DRYER_M73}
_PRODUCT_ID_PROFILES = {0x5A5B: MUXNW_DRYER_M73}


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
    "get_profile",
    "iter_profiles",
    "profile_for_product_id",
]
