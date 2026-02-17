"""Ping and Tracert module - collects network diagnostic data like trippy."""

import subprocess
import re
import threading
import time
import platform
from dataclasses import dataclass, field


@dataclass
class PingResult:
    seq: int
    ttl: int
    rtt_ms: float
    lost: bool = False


@dataclass
class PingStats:
    target: str
    sent: int = 0
    received: int = 0
    loss_pct: float = 0.0
    min_ms: float = 0.0
    avg_ms: float = 0.0
    max_ms: float = 0.0
    jitter_ms: float = 0.0
    recent_rtts: list = field(default_factory=list)
    running: bool = False
    error: str = ""


@dataclass
class TraceHop:
    hop: int
    ip: str
    hostname: str
    rtt1_ms: float
    rtt2_ms: float
    rtt3_ms: float
    lost: bool = False


@dataclass
class TracertStats:
    target: str
    hops: list = field(default_factory=list)
    running: bool = False
    done: bool = False
    error: str = ""


class PingTracer:
    """Runs ping and tracert commands and parses output."""

    def __init__(self, target: str, ping_count: int = 50,
                 tracert_max_hops: int = 15, tracert_timeout_ms: int = 500,
                 tracert_max_seconds: int = 30):
        self.target = target
        self.ping_count = ping_count
        self.tracert_max_hops = tracert_max_hops
        self.tracert_timeout_ms = tracert_timeout_ms
        self.tracert_max_seconds = tracert_max_seconds
        self.ping_stats = PingStats(target=target)
        self.tracert_stats = TracertStats(target=target)
        self._ping_thread = None
        self._tracert_thread = None
        self._stop_event = threading.Event()

    def start_ping(self):
        self.ping_stats.running = True
        self._ping_thread = threading.Thread(target=self._run_ping, daemon=True)
        self._ping_thread.start()

    def start_tracert(self):
        self.tracert_stats.running = True
        self._tracert_thread = threading.Thread(target=self._run_tracert, daemon=True)
        self._tracert_thread.start()

    def stop(self):
        self._stop_event.set()

    def _run_ping(self):
        try:
            cmd = ["ping", "-n", str(self.ping_count), self.target]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            rtts = []
            seq = 0
            # Windows ping output patterns
            reply_re = re.compile(
                r"(?:来自|Reply from)\s+[\d.]+.*?(?:时间|time)[=<](\d+)\s*ms.*?TTL=(\d+)",
                re.IGNORECASE,
            )
            timeout_re = re.compile(r"(?:请求超时|Request timed out)", re.IGNORECASE)

            for line in proc.stdout:
                if self._stop_event.is_set():
                    proc.kill()
                    break

                line = line.strip()
                if not line:
                    continue

                match = reply_re.search(line)
                if match:
                    seq += 1
                    rtt = float(match.group(1))
                    ttl = int(match.group(2))
                    rtts.append(rtt)
                    self.ping_stats.sent = seq
                    self.ping_stats.received += 1
                    self.ping_stats.recent_rtts.append(rtt)
                    if len(self.ping_stats.recent_rtts) > 60:
                        self.ping_stats.recent_rtts.pop(0)
                    self._update_ping_stats(rtts)
                elif timeout_re.search(line):
                    seq += 1
                    self.ping_stats.sent = seq
                    self.ping_stats.recent_rtts.append(-1)
                    if len(self.ping_stats.recent_rtts) > 60:
                        self.ping_stats.recent_rtts.pop(0)
                    self._update_ping_stats(rtts)

            proc.wait()
        except FileNotFoundError:
            self.ping_stats.error = "ping command not found"
        except Exception as e:
            self.ping_stats.error = str(e)
        finally:
            self.ping_stats.running = False

    def _update_ping_stats(self, rtts):
        stats = self.ping_stats
        if stats.sent > 0:
            stats.loss_pct = ((stats.sent - stats.received) / stats.sent) * 100
        if rtts:
            stats.min_ms = min(rtts)
            stats.max_ms = max(rtts)
            stats.avg_ms = sum(rtts) / len(rtts)
            if len(rtts) >= 2:
                diffs = [abs(rtts[i] - rtts[i - 1]) for i in range(1, len(rtts))]
                stats.jitter_ms = sum(diffs) / len(diffs)

    def _run_tracert(self):
        is_windows = platform.system() == "Windows"
        try:
            if is_windows:
                cmd = [
                    "tracert", "-d",
                    "-w", str(self.tracert_timeout_ms),
                    "-h", str(self.tracert_max_hops),
                    self.target,
                ]
            else:
                # Linux/Mac: use traceroute
                cmd = [
                    "traceroute", "-n",
                    "-w", str(max(1, self.tracert_timeout_ms // 1000)),
                    "-m", str(self.tracert_max_hops),
                    self.target,
                ]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            # Windows tracert output patterns (English & Chinese)
            # "  1    <1 ms    <1 ms    <1 ms  192.168.1.1"
            # "  1    <1 毫秒   <1 毫秒   <1 毫秒  192.168.1.1"
            # "  2     *        *        *     Request timed out."
            # "  2     *        *        *     请求超时。"
            win_hop_re = re.compile(
                r"^\s*(\d+)\s+"
                r"([<\d]+\s*(?:ms|毫秒)|\*)\s+"
                r"([<\d]+\s*(?:ms|毫秒)|\*)\s+"
                r"([<\d]+\s*(?:ms|毫秒)|\*)\s+"
                r"([\d.]+|(?:请求超时|Request timed out))",
                re.IGNORECASE,
            )

            # Linux traceroute output patterns
            # " 1  192.168.1.1  0.466 ms  0.526 ms  0.638 ms"
            # " 2  * * *"
            linux_hop_re = re.compile(
                r"^\s*(\d+)\s+"
                r"([\d.]+)\s+"
                r"([\d.]+)\s*ms\s+"
                r"([\d.]+)\s*ms\s+"
                r"([\d.]+)\s*ms"
            )
            linux_timeout_re = re.compile(
                r"^\s*(\d+)\s+\*"
            )

            start_time = time.time()
            for line in proc.stdout:
                if self._stop_event.is_set():
                    proc.kill()
                    break

                # Overall timeout guard
                if time.time() - start_time > self.tracert_max_seconds:
                    proc.kill()
                    self.tracert_stats.error = (
                        f"Tracert timed out after {self.tracert_max_seconds}s"
                    )
                    break

                line = line.strip()
                if not line:
                    continue

                if is_windows:
                    match = win_hop_re.search(line)
                    if match:
                        hop_num = int(match.group(1))
                        rtt1 = self._parse_rtt(match.group(2))
                        rtt2 = self._parse_rtt(match.group(3))
                        rtt3 = self._parse_rtt(match.group(4))
                        ip = match.group(5)
                        lost = ip in ("Request timed out", "请求超时")

                        hop = TraceHop(
                            hop=hop_num,
                            ip="*" if lost else ip,
                            hostname="*" if lost else ip,
                            rtt1_ms=rtt1,
                            rtt2_ms=rtt2,
                            rtt3_ms=rtt3,
                            lost=lost,
                        )
                        self.tracert_stats.hops.append(hop)
                else:
                    match = linux_hop_re.search(line)
                    if match:
                        hop_num = int(match.group(1))
                        ip = match.group(2)
                        rtt1 = float(match.group(3))
                        rtt2 = float(match.group(4))
                        rtt3 = float(match.group(5))

                        hop = TraceHop(
                            hop=hop_num,
                            ip=ip,
                            hostname=ip,
                            rtt1_ms=rtt1,
                            rtt2_ms=rtt2,
                            rtt3_ms=rtt3,
                            lost=False,
                        )
                        self.tracert_stats.hops.append(hop)
                    elif linux_timeout_re.search(line):
                        hop_match = re.match(r"\s*(\d+)", line)
                        if hop_match:
                            hop = TraceHop(
                                hop=int(hop_match.group(1)),
                                ip="*",
                                hostname="*",
                                rtt1_ms=-1.0,
                                rtt2_ms=-1.0,
                                rtt3_ms=-1.0,
                                lost=True,
                            )
                            self.tracert_stats.hops.append(hop)

            proc.wait(timeout=5)
            self.tracert_stats.done = True
        except FileNotFoundError:
            if is_windows:
                self.tracert_stats.error = "tracert command not found"
            else:
                self.tracert_stats.error = (
                    "traceroute not found (install: apt install traceroute)"
                )
        except subprocess.TimeoutExpired:
            proc.kill()
            self.tracert_stats.error = "tracert process did not exit cleanly"
            self.tracert_stats.done = True
        except Exception as e:
            self.tracert_stats.error = str(e)
        finally:
            self.tracert_stats.running = False

    @staticmethod
    def _parse_rtt(value: str) -> float:
        if value.strip() == "*":
            return -1.0
        # Handle "<1 ms" / "<1 毫秒" — means less than 1ms
        if "<" in value:
            return 0.5
        num = re.search(r"([\d.]+)", value)
        if num:
            return float(num.group(1))
        return -1.0
