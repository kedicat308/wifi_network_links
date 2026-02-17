"""iperf2 bandwidth test module - measures upload/download throughput.

Uses iperf-2.2.1-win64.exe on Windows.  Unlike iperf3 which supports JSON
output (-J), iperf2 only produces human-readable text, so all parsing is
regex-based.
"""

import subprocess
import re
import os
import sys
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


@dataclass
class IperfResult:
    direction: str  # "upload" or "download"
    protocol: str = "TCP"
    bandwidth_mbps: float = 0.0
    transfer_mb: float = 0.0
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


# ---------------------------------------------------------------------------
# iperf2 text output regex (TCP only)
# ---------------------------------------------------------------------------
# Matches lines like:
#   [  4]  0.00-1.00 sec  11.2 MBytes  93.9 Mbits/sec
#   [  4]  0.00-10.00 sec   112 MBytes  93.8 Mbits/sec
#
# Groups: 1=start, 2=end, 3=transfer_val, 4=transfer_unit(KMG or empty),
#         5=bw_val, 6=bw_unit(KMG or empty)
_LINE_RE = re.compile(
    r"\[\s*\d+\]\s+"
    r"([\d.]+)\s*-\s*([\d.]+)\s+sec\s+"
    r"([\d.]+)\s+([KMG]?)Bytes\s+"
    r"([\d.]+)\s+([KMG]?)bits/sec"
)

# Binary multipliers for transfer (Bytes)
_BYTES_MULT = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}
# Decimal multipliers for bandwidth (bits/sec)
_BITS_MULT = {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}


def _bw_to_mbps(value: float, prefix: str) -> float:
    """Convert bandwidth value with unit prefix to Mbps."""
    return value * _BITS_MULT.get(prefix, 1) / 1e6


def _transfer_to_bytes(value: float, prefix: str) -> float:
    """Convert transfer value with unit prefix to bytes."""
    return value * _BYTES_MULT.get(prefix, 1)


class Iperf2Tester:
    """Runs iperf2 tests for bandwidth measurement."""

    def __init__(self, server: str, port: int = 62998, duration: int = 10,
                 iperf_path: str = ""):
        self.server = server
        self.port = port
        self.duration = duration
        self.stats = IperfStats(server=server, port=port)
        self._thread = None
        self._stop_event = threading.Event()
        self._iperf_path = iperf_path if iperf_path else self._find_iperf2()

    # ------------------------------------------------------------------
    # Executable discovery
    # ------------------------------------------------------------------
    _EXE_NAMES = ("iperf-2.2.1-win64.exe", "iperf.exe", "iperf")

    def _find_iperf2(self) -> str:
        """Find iperf2 executable.

        Search order:
        1. PyInstaller bundle (sys._MEIPASS) — for packaged .exe
        2. Directory next to the .exe / main_iperf2.py (app dir)
        3. Project root (development)
        4. Current working directory
        5. Fall back to system PATH
        """
        candidates = []

        # 1. PyInstaller bundle temp dir
        if getattr(sys, "frozen", False):
            for name in self._EXE_NAMES:
                candidates.append(os.path.join(sys._MEIPASS, name))
            # Also check next to the .exe itself
            exe_dir = os.path.dirname(sys.executable)
            for name in self._EXE_NAMES:
                candidates.append(os.path.join(exe_dir, name))
        else:
            # 2. Project root (where main_iperf2.py lives)
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            for name in self._EXE_NAMES:
                candidates.append(os.path.join(project_root, name))

        # 3. Current working directory
        for name in self._EXE_NAMES:
            candidates.append(os.path.join(os.getcwd(), name))

        for p in candidates:
            if os.path.isfile(p):
                return p
        # 4. Fallback to PATH
        return "iperf"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self):
        self.stats.running = True
        self._thread = threading.Thread(target=self._run_tests, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Test orchestration
    # ------------------------------------------------------------------
    def _run_tests(self):
        try:
            # Phase 1: Download test (server sends to client via --reverse)
            self.stats.current_phase = "download"
            self.stats.download.running = True
            self._run_single_test(reverse=True, result=self.stats.download)
            self.stats.download.running = False
            self.stats.download.done = True

            if self._stop_event.is_set():
                return

            # Cooldown between sessions
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

    _RETRYABLE_ERRORS = (
        "connect failed",
        "connection refused",
        "no route to host",
        "server is busy",
    )

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
            "-i", "1",  # 1-second interval reports
        ]

        result.protocol = "TCP"

        if reverse:
            cmd.append("--reverse")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            stdout, stderr = proc.communicate(timeout=self.duration + 120)

            # Parse the text output
            if stdout and stdout.strip():
                self._parse_text_output(stdout, result)

            # If parsing didn't set an error but the process failed, use stderr
            if proc.returncode != 0 and not result.error:
                err_msg = stderr.strip() if stderr else ""
                # iperf2 sometimes prints errors to stdout
                if not err_msg and stdout:
                    for line in stdout.splitlines():
                        if "connect failed" in line.lower() or "error" in line.lower():
                            err_msg = line.strip()
                            break
                result.error = err_msg or f"iperf2 exited with code {proc.returncode}"

            # Append the actual command for debugging
            if result.error:
                result.error += f"  [cmd: {' '.join(cmd)}]"

            # Retry on transient connection errors
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
            result.error = "iperf2 test timed out"
        except FileNotFoundError:
            result.error = f"iperf2 not found at: {self._iperf_path}"
        except Exception as e:
            result.error = str(e)

    # ------------------------------------------------------------------
    # Text output parsing
    # ------------------------------------------------------------------
    def _parse_text_output(self, output: str, result: IperfResult):
        """Parse iperf2 human-readable TCP text output.

        Example:
            [  4]  0.00-1.00 sec  11.2 MBytes  93.9 Mbits/sec
            ...
            [  4]  0.00-10.00 sec   112 MBytes  93.8 Mbits/sec
        """
        parsed = []
        for line in output.splitlines():
            m = _LINE_RE.search(line)
            if not m:
                continue

            start = float(m.group(1))
            end = float(m.group(2))
            transfer_val = float(m.group(3))
            transfer_prefix = m.group(4) or ""
            bw_val = float(m.group(5))
            bw_prefix = m.group(6) or ""

            entry = {
                "start": start,
                "end": end,
                "span": end - start,
                "bw_mbps": _bw_to_mbps(bw_val, bw_prefix),
                "transfer_bytes": _transfer_to_bytes(transfer_val, transfer_prefix),
            }
            parsed.append(entry)

        if not parsed:
            # No data lines found — check for errors in the output
            for line in output.splitlines():
                low = line.lower()
                if "connect failed" in low or "error" in low or "refused" in low:
                    result.error = line.strip()
                    return
            return

        # Separate interval lines from summary lines.
        # Interval lines have span ≈ 1s; summary lines span the full test.
        intervals = [p for p in parsed if p["span"] < 1.5]
        summaries = [p for p in parsed if p["span"] >= 1.5]

        # Edge case: 1-second test — interval and summary are the same line
        if not summaries and parsed:
            summaries = [parsed[-1]]
            intervals = parsed[:-1] if len(parsed) > 1 else []

        # Use the last summary line for final stats
        if summaries:
            summary = summaries[-1]
            result.bandwidth_mbps = summary["bw_mbps"]
            result.transfer_mb = summary["transfer_bytes"] / (1024 * 1024)

        # Add per-second intervals
        for iv in intervals:
            stream = IperfStream(
                interval=f"{iv['start']:.1f}-{iv['end']:.1f}",
                transfer_bytes=iv["transfer_bytes"],
                bandwidth_mbps=iv["bw_mbps"],
            )
            result.intervals.append(stream)
