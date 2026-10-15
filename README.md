# Xiaomi MIoT BLE for Home Assistant

`hass-miot-ble` is an early-stage Home Assistant integration for local control
of Xiaomi devices that expose MIoT properties over Bluetooth Low Energy.

The project is designed around reusable protocol layers instead of embedding
device-specific behavior in the Bluetooth transport:

- Xiaomi Standard Auth session key derivation and AES-CCM helpers
- MIoT property request and response codecs
- Declarative device profiles
- Home Assistant entities generated from profile capabilities

## Current status

The protocol core and the first device profile are under development. The
integration is not ready for installation or real-device control yet.

The first target is the Muxin smart clothes dryer:

| Model | MIoT specification | Status |
| --- | --- | --- |
| `muxnw.dryer.m73` | `urn:miot-spec-v2:device:clothes-dryer:0000A06D:muxnw-m73:1` | Profile added; transport pending |

The profile is based on the device's Mi Home plugin bundle:

| Property | SIID/PIID | Wire type | Access |
| --- | --- | --- | --- |
| Power | `2/1` | `uint8` | Read, write, notify |
| UV | `2/2` | `uint8` | Read, write, notify |
| Timer | `2/3` | `uint8` | Read, write, notify |
| Remaining time | `2/4` | `uint16` | Notify |

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
