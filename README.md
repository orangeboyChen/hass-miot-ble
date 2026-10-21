[简体中文](README_zh.md)

# Xiaomi Miot BLE for Home Assistant

`hass-miot-ble` is a Home Assistant custom integration for controlling supported
Xiaomi Miot Bluetooth Low Energy devices locally.

## Supported devices

| Device | Model | Available controls and states |
| --- | --- | --- |
| Muxiang M73 Shoe Dryer | `muxnw.dryer.m73` | Power, UV, timer, remaining time |
| Xiaomi Smart Waist Massager YB01 | `xiaomi.magic_touch.yb01` | Power, device status, mode, timer, heating, battery |
| Xiaomi Smart Waist and Back Massager YB11 | `xiaomi.magic_touch.yb11` | Power, device status, mode, timer, heating, regional massage, beep, battery |

Every supported device also provides its connection state and Connect and
Disconnect buttons.

## Installation

### HACS

1. Open **HACS** > **Integrations**.
2. Open the three-dot menu and select **Custom repositories**.
3. Add `https://github.com/orangeboyChen/hass-miot-ble` as an **Integration**.
4. Install **Xiaomi Miot BLE** and restart Home Assistant.

### Manual installation

Copy `custom_components/miot_ble` into the `custom_components` directory in your
Home Assistant configuration directory, then restart Home Assistant.

## Adding a device

1. Make sure Home Assistant has access to a Bluetooth adapter or Bluetooth
   proxy and the device is nearby.
2. Open **Settings** > **Devices & services** > **Add integration**.
3. Search for **Xiaomi Miot BLE** and select a supported device.
4. Sign in to the Xiaomi account that owns the device when prompted.
