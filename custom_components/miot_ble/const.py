"""Constants for Xiaomi MIoT BLE."""

DOMAIN = "miot_ble"

CONF_TOKEN = "token"
DEFAULT_UPDATE_INTERVAL = 60
PLATFORMS = ("binary_sensor", "button", "number", "sensor", "select", "switch")

BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"


def expand_uuid(short_uuid: str) -> str:
    """Expand a four-character Bluetooth UUID to its canonical form."""
    if len(short_uuid) != 4:
        raise ValueError("A short Bluetooth UUID must contain four characters")
    try:
        int(short_uuid, 16)
    except ValueError as error:
        raise ValueError("A short Bluetooth UUID must be hexadecimal") from error
    return f"0000{short_uuid.lower()}{BASE_UUID_SUFFIX}"


UUID_MI_SERVICE = expand_uuid("fe95")
UUID_UPNP = expand_uuid("0010")
UUID_AUTH = expand_uuid("0019")
UUID_MIOT_WRITE = expand_uuid("001a")
UUID_MIOT_NOTIFY = expand_uuid("001b")
