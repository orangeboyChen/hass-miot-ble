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

The first target is the Muxiang M73 shoe dryer:

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

1. In Home Assistant, open **Settings** > **Devices & services**.
2. Select **Add integration** and search for **Xiaomi MIoT BLE**.
3. Select a supported nearby device and optionally set a display name. Devices
   without an implemented profile are not listed.
4. Home Assistant automatically reuses existing local access data from its
   official Xiaomi BLE integration when available. Otherwise, sign in with the
   Xiaomi account that owns the device. The credentials are sent directly to
   Xiaomi Cloud during setup and are not stored in the config entry.
5. If Xiaomi requests an image captcha, enter the characters shown in Home
   Assistant. If Xiaomi requests account verification, open the supplied Xiaomi
   page and request a phone or email verification code. Do not submit the code
   on the Xiaomi page; enter it in Home Assistant instead. Challenge data and
   account credentials remain only in the active setup flow and are discarded
   afterward.
6. The integration connects to the device and authenticates before it saves the
   config entry.

For the initial M73 profile, a successful setup creates power and UV switches,
a timer selector, and a remaining-time sensor. If setup reports that the device
cannot be reached, check Bluetooth range and make sure Mi Home is not holding an
active connection. If authentication fails, restart setup so Home Assistant can
refresh the device's local access data.

If the same address is already configured in Home Assistant's official Xiaomi
BLE integration, its local access data is reused without showing the cloud
login step. If a device later rejects that data, Home Assistant starts a Xiaomi
account reauthentication flow and retrieves a replacement automatically without
deleting the device or its entities.

## Current limitations

- Only `muxnw.dryer.m73` has a device profile.
- Xiaomi may introduce account verification methods that are not yet supported;
  image captcha and phone or email verification-ticket flows are implemented.
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
