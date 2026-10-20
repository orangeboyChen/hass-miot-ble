# Device profiles

This document records the captured Mi Home plugin evidence and assumptions used
by each profile. Hardware behavior can differ across firmware revisions, so
these details should be updated when a real device reveals a protocol change.

## Xiaomi Smart Waist Massager YB01

- Model: `xiaomi.magic_touch.yb01`
- MiBeacon product ID: `0x46C6` (`18118`)
- Miot type: `urn:miot-spec-v2:device:massager:0000A083:xiaomi-yb01:1`
- Plugin package: `1019936`
- Captured plugin version: `1.0.93` (`1038189`)
- Authentication type: Xiaomi Standard Auth (`authType=4`)

The plugin subscribes to battery `3/1` and massager properties `4/1` through
`4/8`, plus power `4/10`. The profile omits `4/8` because the captured plugin
only presents one massage heat-level value, which would not form a useful Home
Assistant control. The plugin displays the raw battery value multiplied by five.

The captured plugin writes target-time values `10`, `15`, `20`, `25`, and `30`
directly, even though one public specification revision describes that property
as an enumeration from `1` through `5`. The profile follows the captured wire
behavior. It treats power as an unsigned byte with `0` for off, `1` for on, and
`2` for charging. Older firmware lists may describe power as a Boolean.

## Xiaomi Smart Waist and Back Massager YB11

- Model: `xiaomi.magic_touch.yb11`
- MiBeacon product ID: `0x8FD3` (`36819`)
- Miot type: `urn:miot-spec-v2:device:massager:0000A083:xiaomi-yb11:1`
- Plugin package: `1042019`
- Captured plugin version: `1.1.16` (`1069485`)
- Authentication type: Xiaomi Standard Auth (`authType=4`)

The captured plugin declares battery `3/1003` and massager properties under
service `4`, ending at `4/15`. String properties, the firmware report code, and
properties without a useful user-facing control are omitted. Battery `3/1003`
proves that the Miot BLE property identifier occupies two little-endian bytes;
the profile codec supports the full `1..65535` PIID range. The plugin displays
the raw battery value multiplied by five.

Power `4/8` is an unsigned byte: `0` means off, `1` means on, `2` means
charging, `3` means fully charged, and `4` means restoring. The switch entity
therefore reports on only for value `1`; charging and maintenance states do not
appear as active massage sessions.
