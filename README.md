# Xiaomi MIoT BLE for Home Assistant

`hass-miot-ble` is an early-stage Home Assistant integration for local control
of Xiaomi devices that expose MIoT properties over Bluetooth Low Energy.

The project is designed around reusable protocol layers instead of embedding
device-specific behavior in the Bluetooth transport:

- Xiaomi Standard Auth session key derivation and AES-CCM helpers
- MIoT property request and response codecs
- Declarative device profiles
- Home Assistant entities generated from profile capabilities

The shared profile engine can generate binary sensors, numbers, sensors,
selects, and switches. It supports Boolean values, signed and unsigned
8/16/32/64-bit integers, bounded numeric ranges, and non-contiguous MIoT value
lists. Adding a device that follows the same transport therefore only requires
a tested declarative profile rather than another integration implementation.

## Current status

The protocol core, Xiaomi Standard Auth session, encrypted property transport,
Home Assistant config flow, coordinator, and profile-driven entities are
implemented with simulated GATT tests. The protocol has not yet been validated
against a physical M73, so this repository should still be treated as
experimental.

The first target is the Muxin smart clothes dryer:

| Model | MIoT specification | Status |
| --- | --- | --- |
| `muxnw.dryer.m73` | `urn:miot-spec-v2:device:clothes-dryer:0000A06D:muxnw-m73:1` | Software path implemented; hardware validation pending |

The profile is based on the device's Mi Home plugin bundle:

| Property | SIID/PIID | Wire type | Access |
| --- | --- | --- | --- |
| Power | `2/1` | `uint8` | Read, write, notify |
| UV | `2/2` | `uint8` | Read, write, notify |
| Timer | `2/3` | `uint8` | Read, write, notify |
| Remaining time | `2/4` | `uint16` | Notify |

## Installation

The integration is not yet included in the default HACS repository list.

1. In HACS, open **Integrations**, choose the three-dot menu, and select
   **Custom repositories**.
2. Add `https://github.com/orangeboyChen/hass-miot-ble` with the category
   **Integration**.
3. Install **Xiaomi MIoT BLE** and restart Home Assistant.

For a manual installation, copy `custom_components/miot_ble` into the
`custom_components` directory under the Home Assistant configuration directory,
then restart Home Assistant.

## Adding a device

Before starting, Home Assistant needs a local Bluetooth adapter or a supported
Bluetooth proxy that can make active connections. Keep the device powered on,
near a Bluetooth adapter or proxy, and advertising.

You also need the device's Xiaomi BLE binding token. This is a 12-byte value
written as exactly 24 hexadecimal characters. It is not the 32-character
Wi-Fi `miio` token. The integration does not currently retrieve binding tokens
from Xiaomi Cloud, so the token must be extracted from the Mi Home binding data
before setup.

1. In Home Assistant, open **Settings** > **Devices & services**.
2. Select **Add integration** and search for **Xiaomi MIoT BLE**.
3. Select a discovered device, or enter its Bluetooth address manually when no
   discovery is available.
4. Select the device model, enter the 24-character BLE token, and optionally
   set a display name.
5. Submit the form. The integration connects to the device and authenticates
   before it saves the config entry.

For the initial M73 profile, a successful setup creates power and UV switches,
a timer selector, and a remaining-time sensor. If setup reports that the device
cannot be reached, check Bluetooth range and make sure Mi Home is not holding an
active connection. If authentication fails, verify that the token belongs to
the same physical device.

If the same address is already configured in Home Assistant's official Xiaomi
BLE integration with a 24-character legacy bindkey, the token field can be left
empty and the locally stored key is reused. Xiaomi account credentials are
never read or stored by this integration. If a device later rejects its token,
Home Assistant starts a reauthentication flow so the token can be replaced
without deleting the device or its entities.

## Current limitations

- Only `muxnw.dryer.m73` has a device profile.
- The integration does not sign in to Xiaomi Cloud or extract BLE tokens.
- The M73 login and property operations still need validation on physical
  hardware.
- Supporting another MIoT BLE model requires a tested device profile and may
  require a protocol quirk.

## Development

```shell
uv sync
uv run pytest
uv run ruff check .
```

The test suite enforces at least 90 percent branch coverage.

## Design boundary

"Generic" means the authentication, encrypted transport, MIoT codec, and Home
Assistant entity machinery are shared. A device can still require a profile or
a small protocol quirk handler when its firmware differs from the standard
MIoT BLE behavior.

## License

MIT
