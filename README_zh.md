[English](README.md)

# 小米 MIoT 蓝牙 Home Assistant 集成

`hass-miot-ble` 是一个 Home Assistant 自定义集成，用于在本地控制受支持的
小米 MIoT 低功耗蓝牙设备。

## 支持的设备

| 设备 | 型号 | 可用控制和状态 |
| --- | --- | --- |
| Muxiang M73 烘鞋器 | `muxnw.dryer.m73` | 电源、紫外线、定时、剩余时间 |
| 米家腰部按摩仪 YB01 | `xiaomi.magic_touch.yb01` | 电源、模式、定时、加热、电量 |
| 米家智能腰背按摩仪 YB11 | `xiaomi.magic_touch.yb11` | 电源、模式、定时、加热、分区按摩、提示音、电量 |

每个受支持的设备还会提供连接状态，以及连接和断开连接按钮。

## 安装

### HACS

1. 打开 **HACS** > **集成**。
2. 打开右上角三点菜单，选择 **自定义存储库**。
3. 添加 `https://github.com/orangeboyChen/hass-miot-ble`，类别选择 **集成**。
4. 安装 **小米 MIoT 蓝牙**，然后重启 Home Assistant。

### 手动安装

将 `custom_components/miot_ble` 复制到 Home Assistant 配置目录下的
`custom_components` 目录，然后重启 Home Assistant。

## 添加设备

1. 确保 Home Assistant 可以使用蓝牙适配器或蓝牙代理，并将设备放在附近。
2. 打开 **设置** > **设备与服务** > **添加集成**。
3. 搜索 **小米 MIoT 蓝牙**，然后选择受支持的设备。
4. 按照提示登录绑定该设备的小米账号。
