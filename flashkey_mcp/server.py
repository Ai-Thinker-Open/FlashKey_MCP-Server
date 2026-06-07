"""
FlashKey FK-01 MCP Server — FastMCP-based tool interface.

AI Agent 通过这个 MCP Server 控制 FlashKey 硬件的 BOOT/RST 引脚、
5V/3.3V 电源、Challenge-Response 握手认证。

使用方法:
    pip install flashkey-mcp
    flashkey-mcp              # 启动 MCP Server (stdio transport)
"""
import logging
import sys

log = logging.getLogger("flashkey.server")

# ============================================================
# MCP 工具定义
# ============================================================

try:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "FlashKey FK-01",
        instructions="""FlashKey FK-01 USB programmer/debugger control.
        
连接流程:
1. flashkey_list_devices()  → 发现设备串口
2. flashkey_connect(port)   → 打开串口
3. flashkey_ping()          → 验证设备 (期望 MAGIC "FK01")
4. flashkey_handshake()     → Challenge-Response 认证
5. 认证后所有 GPIO/电源/烧录命令可用
6. flashkey_disconnect()    → 用完断开""",
    )

    # ---- 发现 & 连接 ----

    @mcp.tool()
    def flashkey_list_devices() -> str:
        """扫描 USB 串口，列出所有 FlashKey 控制接口。"""
        return "等待实现 — 返回 端口 | 描述 | VID:PID 表格"

    @mcp.tool()
    def flashkey_connect(port: str) -> str:
        """连接到 FlashKey 控制串口。
        
        Args:
            port: 串口名，例如 COM3、/dev/ttyACM0
        """
        return f"等待实现 — 连接到 {port}"

    @mcp.tool()
    def flashkey_disconnect() -> str:
        """断开 FlashKey 串口连接。"""
        return "等待实现 — 断开连接"

    # ---- 认证 ----

    @mcp.tool()
    def flashkey_ping() -> str:
        """验证 FlashKey 连通性，期望返回 MAGIC 'FK01'。
        必须在 flashkey_connect() 之后调用。"""
        return "等待实现 — PONG! MAGIC: FK01"

    @mcp.tool()
    def flashkey_handshake() -> str:
        """Challenge-Response 握手认证。
        必须在 flashkey_ping() 之后调用。
        认证后所有控制命令（BOOT/RST/电源）可用。"""
        return "等待实现 — 握手成功/失败"

    # ---- 状态查询 ----

    @mcp.tool()
    def flashkey_get_version() -> str:
        """读取 FlashKey 固件版本号。"""
        return "等待实现 — v0.0.0"

    @mcp.tool()
    def flashkey_get_uid() -> str:
        """读取 CH32V203 芯片唯一 ID（12 字节 hex）。"""
        return "等待实现 — 芯片 UID"

    @mcp.tool()
    def flashkey_get_all_status() -> str:
        """一次读取所有状态：BOOT、RST、5V、3.3V、握手标记。"""
        return "等待实现 — 全状态"

    @mcp.tool()
    def flashkey_reset_device() -> str:
        """软复位 FlashKey MCU。复位后需重新连接和握手。"""
        return "等待实现 — 复位成功"

    # ---- GPIO 控制 ----

    @mcp.tool()
    def flashkey_boot_set(level: str) -> str:
        """设置 BOOT 引脚电平。
        
        Args:
            level: 'HIGH' 或 'LOW'
            BL618 烧录: BOOT=HIGH
            ESP32 烧录: BOOT=LOW
        """
        return f"等待实现 — BOOT 设为 {level.upper()}"

    @mcp.tool()
    def flashkey_boot_get() -> str:
        """读取 BOOT 引脚当前电平。"""
        return "等待实现 — BOOT: HIGH/LOW"

    @mcp.tool()
    def flashkey_rst_set(level: str) -> str:
        """设置 RST 引脚电平。
        
        Args:
            level: 'HIGH' 或 'LOW'。LOW 保持目标板复位。
        """
        return f"等待实现 — RST 设为 {level.upper()}"

    @mcp.tool()
    def flashkey_rst_get() -> str:
        """读取 RST 引脚当前电平。"""
        return "等待实现 — RST: HIGH/LOW"

    @mcp.tool()
    def flashkey_rst_pulse(ms: int = 10) -> str:
        """生成 RST 脉冲（拉低 → 等待 → 拉高）。
        
        Args:
            ms: 脉冲宽度（1-250ms，默认 10ms）
        """
        return f"等待实现 — RST 脉冲 {ms}ms"

    # ---- 电源控制 ----

    @mcp.tool()
    def flashkey_v5v_set(on: bool) -> str:
        """开关 5V 输出。建议先开 3.3V 再开 5V。"""
        return f"等待实现 — 5V: {'ON' if on else 'OFF'}"

    @mcp.tool()
    def flashkey_v5v_get() -> str:
        """读取 5V 输出状态。"""
        return "等待实现 — 5V: ON/OFF"

    @mcp.tool()
    def flashkey_v3v3_set(on: bool) -> str:
        """开关 3.3V 输出。电源变化后等 10ms 再操作引脚。"""
        return f"等待实现 — 3.3V: {'ON' if on else 'OFF'}"

    @mcp.tool()
    def flashkey_v3v3_get() -> str:
        """读取 3.3V 输出状态。"""
        return "等待实现 — 3.3V: ON/OFF"

    # ---- 一键烧录 ----

    @mcp.tool()
    def flashkey_enter_bootloader(target: str = "bl618") -> str:
        """一键进入指定 MCU 的烧录模式。
        
        Args:
            target: MCU 类型
                - 'bl618' (默认): BOOT=HIGH → RST 脉冲
                - 'esp32': BOOT=LOW → RST 脉冲
                - 'stm32': BOOT=HIGH → RST 脉冲
        """
        return f"等待实现 — {target} 进入烧录模式"

    # ---- 串口透传 ----

    @mcp.tool()
    def flashkey_serial_open(port: str, baud: int = 115200) -> str:
        """打开 CH340C 串口，与目标板 TTL UART 通信。"""
        return f"等待实现 — 串口 {port} @ {baud}"

    @mcp.tool()
    def flashkey_serial_send(data: str) -> str:
        """通过 CH340C 向目标板发送数据。"""
        return f"等待实现 — 发送: {data[:50]}..."

    @mcp.tool()
    def flashkey_serial_read(timeout: float = 1.0) -> str:
        """读取 CH340C 接收缓冲区数据。"""
        return "等待实现 — 接收数据"

    @mcp.tool()
    def flashkey_serial_close() -> str:
        """关闭 CH340C 串口。"""
        return "等待实现 — 串口已关闭"

    HAS_MCP = True

except ImportError:
    HAS_MCP = False
    log.warning("FastMCP not installed. Install with: pip install 'mcp[fastmcp]'")


def main():
    """flashkey-mcp CLI 入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if not HAS_MCP:
        print("Error: FastMCP not available. Install with: pip install 'mcp[fastmcp]'")
        sys.exit(1)
    log.info("FlashKey FK-01 MCP Server starting...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
