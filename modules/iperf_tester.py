"""iperf3 bandwidth test module - measures upload/download throughput."""

import subprocess
import json
import os
import socket
import time
import threading
from dataclasses import dataclass, field


@dataclass
class IperfStream:
    """A single interval measurement."""
    interval: str
    transfer_bytes: float
    bandwidth_mbps: float
    jitter_ms: float = 0.0
    lost_packets: int = 0
    total_packets: int = 0
    loss_pct: float = 0.0


@dataclass
class IperfResult:
    direction: str  # "upload" or "download"
    protocol: str  # "UDP" or "TCP"
    bandwidth_mbps: float = 0.0
    transfer_mb: float = 0.0
    jitter_ms: float = 0.0
    lost_packets: int = 0
    total_packets: int = 0
    loss_pct: float = 0.0
    intervals: list = field(default_factory=list)
    running: bool = False
    done: bool = False
    error: str = ""


@dataclass
class IperfStats:
    server: str
    port: int
    upload: IperfResult = None
    download: IperfResult = None
    running: bool = False
    current_phase: str = ""  # "download", "upload", "done"
    error: str = ""

    def __post_init__(self):
        if self.upload is None:
            self.upload = IperfResult(direction="upload", protocol="TCP")
        if self.download is None:
            self.download = IperfResult(direction="download", protocol="TCP")


class IperfTester:
    """Runs iperf3 tests for bandwidth measurement."""

    def __init__(self, server: str, port: int = 5201, duration: int = 10,
                 protocol: str = "tcp", bandwidth: str = "100M"):
        self.server = server
        self.port = port
        self.duration = duration
        self.protocol = protocol
        self.bandwidth = bandwidth
        self.stats = IperfStats(server=server, port=port)
        self._thread = None
        self._stop_event = threading.Event()
        self._iperf_path = self._find_iperf3()

    def _find_iperf3(self) -> str:
        """Find iperf3 executable in current directory or PATH."""
        # Check current directory first
        local_paths = [
            os.path.join(os.getcwd(), "iperf3.exe"),
            os.path.join(os.getcwd(), "iperf3"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "iperf3.exe"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "iperf3"),
        ]
        for p in local_paths:
            if os.path.isfile(p):
                return p
        # Fallback to PATH
        return "iperf3"

    def start(self):
        self.stats.running = True
        self._thread = threading.Thread(target=self._run_tests, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _run_tests(self):
        try:
            # Phase 1: Download test (server sends to client)
            self.stats.current_phase = "download"
            self.stats.download.running = True
            self._run_single_test(reverse=True, result=self.stats.download)
            self.stats.download.running = False
            self.stats.download.done = True

            if self._stop_event.is_set():
                return

            # iperf3 server only handles one client at a time and needs a
            # cooldown between sessions; connect immediately → "connection refused"
            self.stats.current_phase = "waiting"
            for _ in range(3):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

            # Phase 2: Upload test (client sends to server)
            self.stats.current_phase = "upload"
            self.stats.upload.running = True
            self._run_single_test(reverse=False, result=self.stats.upload)
            self.stats.upload.running = False
            self.stats.upload.done = True

            self.stats.current_phase = "done"
        except Exception as e:
            self.stats.error = str(e)
        finally:
            self.stats.running = False

    _RETRYABLE_ERRORS = ("unable to connect", "connection refused", "the server is busy")

    def _check_port_open(self, timeout: float = 3) -> str | None:
        """Quick TCP connect check. Returns error string or None if OK."""
        try:
            with socket.create_connection((self.server, self.port), timeout=timeout):
                return None
        except OSError as e:
            return f"TCP connect to {self.server}:{self.port} failed: {e}"

    def _run_single_test(self, reverse: bool, result: IperfResult, _retries: int = 3):
        # Pre-check: can we even reach the port?
        port_err = self._check_port_open()
        if port_err:
            result.error = port_err
            # Still allow retries
            if _retries > 0 and not self._stop_event.is_set():
                time.sleep(2)
                result.error = ""
                self._run_single_test(reverse, result, _retries - 1)
            return

        cmd = [
            self._iperf_path,
            "-c", self.server,
            "-p", str(self.port),
            "-t", str(self.duration),
            "-J",  # JSON output
        ]

        if self.protocol == "udp":
            cmd.extend(["-u", "-b", self.bandwidth])
            result.protocol = "UDP"
        else:
            result.protocol = "TCP"

        if reverse:
            cmd.append("-R")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            stdout, stderr = proc.communicate(timeout=self.duration + 120)

            # Always try to parse JSON first — iperf3 -J puts error details
            # in stdout JSON even when exit code is non-zero.
            if stdout and stdout.strip():
                self._parse_json_output(stdout, result)

            # If parsing didn't set an error but the process failed, use stderr
            if proc.returncode != 0 and not result.error:
                result.error = (stderr.strip()
                                or f"iperf3 exited with code {proc.returncode}")

            # Append the actual command for debugging
            if result.error:
                result.error += f"  [cmd: {' '.join(cmd)}]"

            # Retry on transient connection errors (server busy / cooldown)
            if result.error and _retries > 0:
                err_lower = result.error.lower()
                if any(msg in err_lower for msg in self._RETRYABLE_ERRORS):
                    if not self._stop_event.is_set():
                        time.sleep(2)
                        result.error = ""
                        result.intervals.clear()
                        self._run_single_test(reverse, result, _retries - 1)

        except subprocess.TimeoutExpired:
            proc.kill()
            result.error = "iperf3 test timed out"
        except FileNotFoundError:
            result.error = f"iperf3 not found at: {self._iperf_path}"
        except Exception as e:
            result.error = str(e)

    def _parse_json_output(self, output: str, result: IperfResult):
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            result.error = "Failed to parse iperf3 JSON output"
            return

        if "error" in data:
            result.error = data["error"]
            return

        # Parse intervals
        for interval in data.get("intervals", []):
            streams = interval.get("sum", {})
            bw_bps = streams.get("bits_per_second", 0)
            transfer = streams.get("bytes", 0)

            stream = IperfStream(
                interval=f"{streams.get('start', 0):.1f}-{streams.get('end', 0):.1f}",
                transfer_bytes=transfer,
                bandwidth_mbps=bw_bps / 1_000_000,
                jitter_ms=streams.get("jitter_ms", 0),
                lost_packets=streams.get("lost_packets", 0),
                total_packets=streams.get("packets", 0),
            )
            if stream.total_packets > 0:
                stream.loss_pct = (stream.lost_packets / stream.total_packets) * 100
            result.intervals.append(stream)

        # Parse summary
        end = data.get("end", {})
        summary = end.get("sum_received", end.get("sum", {}))
        if not summary:
            # For UDP, look at sum_sent or server output
            summary = end.get("sum_sent", {})

        result.bandwidth_mbps = summary.get("bits_per_second", 0) / 1_000_000
        result.transfer_mb = summary.get("bytes", 0) / (1024 * 1024)

        # UDP-specific stats
        udp_summary = end.get("sum", {})
        result.jitter_ms = udp_summary.get("jitter_ms", 0)
        result.lost_packets = udp_summary.get("lost_packets", 0)
        result.total_packets = udp_summary.get("packets", 0)
        if result.total_packets > 0:
            result.loss_pct = (result.lost_packets / result.total_packets) * 100
