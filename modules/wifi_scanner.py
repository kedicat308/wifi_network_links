"""WiFi scanner module - uses Windows netsh commands for WiFi diagnostics."""

import subprocess
import re
import threading
import time
from dataclasses import dataclass, field


@dataclass
class WifiInterface:
    """Current WiFi connection info."""
    name: str = ""
    description: str = ""
    ssid: str = ""
    bssid: str = ""
    state: str = ""
    radio_type: str = ""
    auth: str = ""
    cipher: str = ""
    channel: int = 0
    signal_pct: int = 0
    rx_rate_mbps: float = 0.0
    tx_rate_mbps: float = 0.0
    profile: str = ""


@dataclass
class WifiBssid:
    """A single BSSID entry."""
    bssid: str = ""
    signal_pct: int = 0
    radio_type: str = ""
    channel: int = 0
    band: str = ""


@dataclass
class WifiNetwork:
    """A visible WiFi network (SSID)."""
    ssid: str = ""
    network_type: str = ""
    auth: str = ""
    cipher: str = ""
    bssids: list = field(default_factory=list)
    best_signal: int = 0


@dataclass
class WifiScanResult:
    """Result of a single WiFi scan iteration."""
    scan_num: int
    timestamp: str = ""
    networks: list = field(default_factory=list)
    current_interface: WifiInterface = None
    error: str = ""


@dataclass
class WifiStats:
    current: WifiInterface = None
    scan_results: list = field(default_factory=list)
    reconnect_log: list = field(default_factory=list)
    running: bool = False
    current_phase: str = ""  # "info", "reconnect", "scan_N", "done"
    total_scans: int = 5
    completed_scans: int = 0
    error: str = ""


class WifiScanner:
    """Manages WiFi diagnostics using Windows netsh commands."""

    def __init__(self, scan_count: int = 5, scan_interval: float = 3.0):
        self.scan_count = scan_count
        self.scan_interval = scan_interval
        self.stats = WifiStats(total_scans=scan_count)
        self._thread = None
        self._stop_event = threading.Event()

    def start(self):
        self.stats.running = True
        self._thread = threading.Thread(target=self._run_all, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _run_cmd(self, cmd: list) -> str:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.stdout
        except FileNotFoundError:
            return ""
        except subprocess.TimeoutExpired:
            return ""
        except Exception:
            return ""

    def _run_all(self):
        try:
            # Phase 1: Get current WiFi interface info
            self.stats.current_phase = "info"
            self.stats.current = self._get_interface_info()

            if self._stop_event.is_set():
                return

            # Phase 2: Disconnect and reconnect
            if self.stats.current and self.stats.current.ssid:
                self.stats.current_phase = "reconnect"
                self._reconnect_wifi(self.stats.current.ssid, self.stats.current.name)

            if self._stop_event.is_set():
                return

            # Phase 3: Scan WiFi networks (repeat N times)
            for i in range(self.scan_count):
                if self._stop_event.is_set():
                    break
                self.stats.current_phase = f"scan_{i + 1}"
                scan = self._scan_networks(i + 1)
                self.stats.scan_results.append(scan)
                self.stats.completed_scans = i + 1

                # Also refresh interface info after each scan
                scan.current_interface = self._get_interface_info()

                if i < self.scan_count - 1:
                    # Wait between scans
                    for _ in range(int(self.scan_interval * 10)):
                        if self._stop_event.is_set():
                            break
                        time.sleep(0.1)

            self.stats.current_phase = "done"
        except Exception as e:
            self.stats.error = str(e)
        finally:
            self.stats.running = False

    def _get_interface_info(self) -> WifiInterface:
        """Get current WiFi connection details via netsh wlan show interfaces."""
        output = self._run_cmd(["netsh", "wlan", "show", "interfaces"])
        if not output:
            return WifiInterface(state="not available")

        info = WifiInterface()
        # Parse both English and Chinese netsh output
        patterns = {
            "name": r"(?:名称|Name)\s*:\s*(.+)",
            "description": r"(?:描述|Description)\s*:\s*(.+)",
            "ssid": r"\bSSID\s*:\s*(.+)",
            "bssid": r"BSSID\s*:\s*(.+)",
            "state": r"(?:状态|State)\s*:\s*(.+)",
            "radio_type": r"(?:无线电类型|Radio type)\s*:\s*(.+)",
            "auth": r"(?:身份验证|Authentication)\s*:\s*(.+)",
            "cipher": r"(?:密码|Cipher)\s*:\s*(.+)",
            "channel": r"(?:频道|Channel)\s*:\s*(\d+)",
            "signal": r"(?:信号|Signal)\s*:\s*(\d+)%",
            "rx_rate": r"(?:接收速率|Receive rate)\s*\(Mbps\)\s*:\s*([\d.]+)",
            "tx_rate": r"(?:传输速率|Transmit rate)\s*\(Mbps\)\s*:\s*([\d.]+)",
            "profile": r"(?:配置文件|Profile)\s*:\s*(.+)",
        }

        for key, pat in patterns.items():
            match = re.search(pat, output, re.IGNORECASE)
            if match:
                val = match.group(1).strip()
                if key == "channel":
                    info.channel = int(val)
                elif key == "signal":
                    info.signal_pct = int(val)
                elif key == "rx_rate":
                    info.rx_rate_mbps = float(val)
                elif key == "tx_rate":
                    info.tx_rate_mbps = float(val)
                else:
                    setattr(info, key, val)

        return info

    def _reconnect_wifi(self, ssid: str, interface_name: str):
        """Disconnect then reconnect to the same WiFi AP."""
        ts = time.strftime("%H:%M:%S")

        # Disconnect
        self.stats.reconnect_log.append(f"[{ts}] Disconnecting from {ssid}...")
        self._run_cmd(["netsh", "wlan", "disconnect", f"interface={interface_name}"])
        time.sleep(2)

        if self._stop_event.is_set():
            return

        # Reconnect
        ts = time.strftime("%H:%M:%S")
        self.stats.reconnect_log.append(f"[{ts}] Reconnecting to {ssid}...")
        self._run_cmd([
            "netsh", "wlan", "connect",
            f"name={ssid}",
            f"interface={interface_name}",
        ])

        # Wait for connection to establish
        for attempt in range(15):
            if self._stop_event.is_set():
                break
            time.sleep(1)
            info = self._get_interface_info()
            if info.state and "connect" in info.state.lower():
                ts = time.strftime("%H:%M:%S")
                self.stats.reconnect_log.append(
                    f"[{ts}] Reconnected! Signal: {info.signal_pct}% "
                    f"Channel: {info.channel}"
                )
                return

        ts = time.strftime("%H:%M:%S")
        self.stats.reconnect_log.append(f"[{ts}] Reconnect timeout - check WiFi manually")

    def _scan_networks(self, scan_num: int) -> WifiScanResult:
        """Scan all visible WiFi networks."""
        output = self._run_cmd(["netsh", "wlan", "show", "networks", "mode=bssid"])

        result = WifiScanResult(
            scan_num=scan_num,
            timestamp=time.strftime("%H:%M:%S"),
        )

        if not output:
            result.error = "Failed to scan WiFi networks"
            return result

        result.networks = self._parse_network_list(output)
        return result

    def _parse_network_list(self, output: str) -> list:
        """Parse netsh wlan show networks mode=bssid output."""
        networks = []
        current_network = None
        current_bssid = None

        # Patterns for both English and Chinese output
        ssid_re = re.compile(r"^SSID\s+\d+\s*:\s*(.*)$", re.MULTILINE)
        net_type_re = re.compile(r"(?:网络类型|Network type)\s*:\s*(.+)", re.IGNORECASE)
        auth_re = re.compile(r"(?:身份验证|Authentication)\s*:\s*(.+)", re.IGNORECASE)
        cipher_re = re.compile(r"(?:加密|Encryption|Cipher)\s*:\s*(.+)", re.IGNORECASE)
        bssid_re = re.compile(r"BSSID\s+\d+\s*:\s*(.+)", re.IGNORECASE)
        signal_re = re.compile(r"(?:信号|Signal)\s*:\s*(\d+)%", re.IGNORECASE)
        radio_re = re.compile(r"(?:无线电类型|Radio type)\s*:\s*(.+)", re.IGNORECASE)
        channel_re = re.compile(r"(?:频道|Channel)\s*:\s*(\d+)", re.IGNORECASE)
        band_re = re.compile(r"(?:带区|Band)\s*:\s*(.+)", re.IGNORECASE)

        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue

            m = ssid_re.match(line)
            if m:
                if current_network is not None:
                    if current_bssid:
                        current_network.bssids.append(current_bssid)
                    if current_network.bssids:
                        current_network.best_signal = max(
                            b.signal_pct for b in current_network.bssids
                        )
                    networks.append(current_network)
                current_network = WifiNetwork(ssid=m.group(1).strip() or "(Hidden)")
                current_bssid = None
                continue

            if current_network is None:
                continue

            m = net_type_re.match(line)
            if m:
                current_network.network_type = m.group(1).strip()
                continue

            m = auth_re.match(line)
            if m:
                current_network.auth = m.group(1).strip()
                continue

            m = cipher_re.match(line)
            if m:
                current_network.cipher = m.group(1).strip()
                continue

            m = bssid_re.match(line)
            if m:
                if current_bssid:
                    current_network.bssids.append(current_bssid)
                current_bssid = WifiBssid(bssid=m.group(1).strip())
                continue

            if current_bssid is None:
                continue

            m = signal_re.match(line)
            if m:
                current_bssid.signal_pct = int(m.group(1))
                continue

            m = radio_re.match(line)
            if m:
                current_bssid.radio_type = m.group(1).strip()
                continue

            m = channel_re.match(line)
            if m:
                current_bssid.channel = int(m.group(1))
                continue

            m = band_re.match(line)
            if m:
                current_bssid.band = m.group(1).strip()

        # Don't forget the last network
        if current_network is not None:
            if current_bssid:
                current_network.bssids.append(current_bssid)
            if current_network.bssids:
                current_network.best_signal = max(
                    b.signal_pct for b in current_network.bssids
                )
            networks.append(current_network)

        # Sort by best signal descending
        networks.sort(key=lambda n: n.best_signal, reverse=True)
        return networks
