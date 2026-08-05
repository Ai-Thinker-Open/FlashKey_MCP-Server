# Bundled WCH OpenOCD — Component Notice

`flashkey-mcp` 随 wheel 附带 WCH OpenOCD（v1.6，OpenOCD 0.11.0 分支）用于
FK-01 CH32V203 的 SDI 烧录。相关许可证与源码如下：

| 组件 | 许可证 | 说明 / 源码 |
|------|--------|-------------|
| openocd（wlinke / wch_riscv 驱动） | GPL-2.0-or-later | 见 `LICENSE-OPENOCD.txt`；源码：https://github.com/openwch/wch-openocd |
| libusb（Windows 随附） | LGPL-2.1 | https://github.com/libusb/libusb |
| libftdi1 | LGPL-2.1 | https://github.com/libusb/hidapi 同源构建（hidapi：BSD-3-Clause） |
| libhidapi | BSD-3-Clause | https://github.com/libusb/hidapi |

Linux 构建依赖系统动态库（libusb-1.0、libudev、libhidapi、libftdi1），
由用户系统提供；Windows 构建所需的 DLL 已随包附带在 `bin/win-x64/`。

二进制来源于 FlashKey 仓库子模块 `wch-openocd`（version v1.6），
本包仅做再分发，未修改源码。
