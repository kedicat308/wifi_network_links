# WiFi Network Diagnostics Tool

Terminal-based网络诊断工具，集成 ping、tracert、iperf 带宽测试和 WiFi 扫描，通过 [rich](https://github.com/Textualize/rich) 库渲染为统一的 TUI 仪表板。

```
┌───────────────────────────────────────────────┐
│       WiFi Network Diagnostics  [01:23]       │
├───────────────────────┬───────────────────────┤
│  Ping                 │  WiFi Connection      │
│  Target  10.216.65.91 │  SSID   OfficeNet     │
│  Loss    0.0%         │  Signal ████████░ 82%  │
│  Avg     3ms          │  Ch     36             │
│  RTT     ▃▅▄▃▅▆▄▃▅▄  │                       │
├───────────────────────┤───────────────────────┤
│  Traceroute           │  WiFi Networks        │
│   1  10.216.65.1  1ms │  SSID  BSSID  Ch Sig  │
│   2  10.216.0.1   2ms │  ...                  │
├───────────────────────┤───────────────────────┤
│  Bandwidth (iperf)    │  Signal Trend         │
│  DL ████████ 93.8Mbps │  OfficeNet ▅▆▇▆ 82%  │
│  UL ██████░░ 47.2Mbps │  Guest     ▃▃▄▃ 45%  │
└───────────────────────┴───────────────────────┘
```

## 项目结构

```
wifi_network_links/
├── main.py                      # 入口 (iperf3 版本)
├── main_iperf2.py               # 入口 (iperf2 版本)
├── requirements.txt             # 依赖: rich>=13.0.0
├── wifi_diag.spec               # PyInstaller 打包 (iperf3)
├── wifi_diag_iperf2.spec        # PyInstaller 打包 (iperf2)
└── modules/
    ├── ping_tracer.py           # Ping + Tracert (共用)
    ├── wifi_scanner.py          # WiFi 扫描/重连 (共用)
    ├── iperf_tester.py          # iperf3 带宽测试 (JSON 解析)
    ├── iperf2_tester.py         # iperf2 带宽测试 (文本正则解析)
    ├── dashboard.py             # TUI 仪表板 (iperf3)
    └── dashboard_iperf2.py      # TUI 仪表板 (iperf2)
```

## 两个版本对比

| | iperf3 版本 (`main.py`) | iperf2 版本 (`main_iperf2.py`) |
|---|---|---|
| 可执行文件 | `iperf3.exe` + `cygwin1.dll` | `iperf-2.2.1-win64.exe` |
| 默认端口 | 60998 | 62998 |
| 协议 | TCP / UDP | TCP only |
| 输出解析 | JSON (`-J` flag) | 文本正则 (`-i 1` 逐秒输出) |
| 反向测试 | `-R` | `--reverse` |
| 服务端启动 | `iperf3 -s -p 60998` | `iperf -s -p 62998` |

**ping、tracert、WiFi 扫描模块两个版本完全共用。**

## 使用方式

### 方式一：直接运行 .exe（推荐，目标电脑无需安装任何东西）

通过 PyInstaller 打包后，生成的 `.exe` 是单文件可执行程序，已内含 Python 运行时、rich 库和 iperf 二进制。
目标电脑**不需要安装 Python、pip 或任何依赖**，双击即可运行。

```bash
# 直接运行
wifi_diag.exe
wifi_diag.exe --target 192.168.1.1
wifi_diag.exe --no-wifi

# iperf2 版本
wifi_diag_iperf2.exe
wifi_diag_iperf2.exe --target 192.168.1.1
```

打包方法见下方 [打包为 .exe](#打包为-exe) 章节。

### 方式二：源码运行（开发/调试用）

需要 Python 3.10+ 环境：

```bash
pip install rich
```

```bash
# iperf3 版本
python main.py
python main.py --target 192.168.1.1
python main.py --target 10.216.65.91 --iperf-server 192.168.1.100
python main.py --no-wifi

# iperf2 版本
python main_iperf2.py
python main_iperf2.py --target 192.168.1.1
```

### 服务端

测试前需在服务器上启动 iperf 服务：

```bash
# iperf3 版本
iperf3 -s -p 60998

# iperf2 版本
iperf -s -p 62998
```

### 命令行参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--target`, `-t` | `10.216.65.91` | 目标主机 (ping/tracert/iperf 统一) |
| `--iperf-server`, `-s` | 同 target | 单独指定 iperf 服务器地址 |
| `--iperf-port` | 60998 / 62998 | iperf 服务端口 |
| `--iperf-duration` | 10 | 带宽测试时长 (秒) |
| `--iperf-proto` | tcp | 协议选择 (仅 iperf3 版本支持 udp) |
| `--ping-count`, `-n` | 50 | Ping 包数量 |
| `--tracert-max-hops` | 15 | Tracert 最大跳数 |
| `--tracert-timeout` | 30 | Tracert 超时 (秒) |
| `--wifi-scans` | 5 | WiFi 扫描轮次 |
| `--no-iperf` | - | 跳过带宽测试 |
| `--no-wifi` | - | 跳过 WiFi 扫描 |
| `--no-reconnect` | - | 跳过 WiFi 断开/重连测试 |

## 测试流程

工具启动后按以下顺序执行：

1. **WiFi 信息采集** — 获取当前连接的 SSID、BSSID、信号强度等
2. **WiFi 断开/重连** — 断开当前 WiFi 后重连，测量重连耗时 (可跳过)
3. **网络测试** (等待 WiFi 重连完成后并行启动)
   - Ping — 连续发包，实时统计丢包率/延迟/抖动
   - Tracert — 路由跳数追踪
   - iperf 带宽测试 — 先下载 (--reverse) 再上传，中间 3 秒冷却
4. **WiFi 环境扫描** — 多轮扫描周围所有可见 AP 的信号强度

所有结果在 TUI 仪表板中实时刷新 (2 FPS)，测试全部完成后持续显示 10 秒。

## 打包为 .exe

将 iperf 可执行文件放在项目根目录，然后运行：

```bash
# iperf3 版本
# 需要: iperf3.exe, cygwin1.dll
pyinstaller wifi_diag.spec

# iperf2 版本
# 需要: iperf-2.2.1-win64.exe
pyinstaller wifi_diag_iperf2.spec
```

生成的 `dist/wifi_diag.exe` 或 `dist/wifi_diag_iperf2.exe` 为单文件可执行程序，内含 iperf 二进制，无需额外安装。

## 平台说明

- **WiFi 扫描/重连**：依赖 Windows `netsh wlan` 命令，仅支持 Windows
- **Ping/Tracert**：支持 Windows (`ping`/`tracert`) 和 Linux (`ping`/`traceroute`)，输出解析兼容中英文
- **iperf 带宽测试**：跨平台，需要对端运行 iperf 服务
