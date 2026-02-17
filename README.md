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

### 仪表板指标说明

#### Ping 面板

| 指标 | 说明 |
|---|---|
| **Target** | 测试目标 IP 地址 |
| **Sent / Recv** | 已发送 / 已收到的 ICMP 包数量 |
| **Loss** | 丢包率 = (发送数 - 接收数) / 发送数 × 100%。<1% 为正常，>5% 说明链路存在问题 |
| **Min / Avg / Max** | 往返延迟 (RTT) 的最小值、平均值、最大值，单位 ms |
| **Jitter** | 抖动 = 相邻两次 RTT 差值的平均值，反映网络稳定性。越小越稳定 |
| **RTT Graph** | 最近 60 次 ping 的 RTT 趋势火花图，直观展示延迟波动 |

#### Traceroute 面板

| 指标 | 说明 |
|---|---|
| **#** | 跳数 (Hop)，从客户端到目标经过的路由节点编号 |
| **IP Address** | 该跳路由器的 IP 地址 |
| **RTT 1 / 2 / 3** | 每跳发送 3 个探测包的往返延迟。`*` 表示该包超时未响应 |
| **Hops** | 总跳数，反映网络路径长度 |
| **Lost** | 完全无响应的跳数 (3 个包均超时) |

#### Bandwidth (iperf) 面板

| 指标 | 说明 |
|---|---|
| **Server** | iperf 服务端地址和端口 |
| **Status** | 当前阶段：download → waiting → upload → done |
| **Download / Upload** | 下载 (服务端→客户端) 和上传 (客户端→服务端) 方向 |
| **Bandwidth** | 带宽吞吐量，单位 Mbps。柱状图最大刻度 1000 Mbps |
| **Transfer** | 测试期间传输的总数据量，单位 MB |
| **Graph** | 每秒带宽的火花图，反映测试期间吞吐量是否稳定 |

#### WiFi Connection 面板

| 指标 | 说明 |
|---|---|
| **SSID** | 当前连接的无线网络名称 |
| **BSSID** | 当前关联的接入点 MAC 地址，用于区分同名 AP |
| **State** | 连接状态 (connected / disconnected 等) |
| **Signal** | Windows 将 RSSI (dBm) 映射为百分比，公式：quality = 2×(RSSI+100)，范围 0-100%。100%≈-50dBm，80%≈-60dBm，60%≈-70dBm，40%≈-80dBm，20%≈-90dBm。仪表板颜色：>70% 绿色，40-70% 黄色，<40% 红色 |
| **Channel** | 当前使用的信道号。2.4GHz: 1-13，5GHz: 36-165 |
| **Radio** | 无线电类型，如 802.11ax、802.11ac 等 |
| **Auth** | 认证方式，如 WPA2-Personal、WPA3 等 |
| **RX / TX** | 协商的接收/发送速率 (Mbps)，受信号强度和协议版本影响 |

#### WiFi Networks 面板

| 指标 | 说明 |
|---|---|
| **SSID** | 扫描到的无线网络名称 |
| **BSSID** | 接入点 MAC 地址，同一 SSID 下可能有多个 BSSID (多 AP 组网) |
| **Ch** | 该 AP 使用的信道 |
| **Signal** | 同上，Windows RSSI→百分比映射值，带颜色柱状图 |
| **Radio** | 无线电类型 (802.11ax/ac/n 等) |

#### Signal Trend 面板

显示多轮扫描中信号最强的前 8 个网络的信号强度变化趋势 (火花图)，用于观察信号是否随时间波动。

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
