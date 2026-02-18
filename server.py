#!/usr/bin/env python3
"""
Network Inspect Collection Server
===================================
Listens on a specified address and port, receives JSON diagnostic reports
from multiple clients, and merges them into a single ``network_inspect.json``
file.  Each client entry is keyed by ``hostname:upload_time`` so concurrent
writes from different machines never collide.

Also provides a logical **port lock** for the externally-managed iperf3
server so that multiple clients queue instead of getting RST.  Clients
call ``GET /iperf-port`` to acquire the lock before testing.

Usage:
    python server.py                        (default: 0.0.0.0:62997, listen on all interfaces)
    python server.py --host 10.216.65.91    (bind to specific IP)
    python server.py --port 9999            (custom port)

Clients POST JSON to ``/report``.  The server responds with 200 on success.
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ---------- configuration ----------

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 62997
DATA_FILE = "network_inspect.json"
LOG_FILE = "server_log.log"

# A single lock guards all reads/writes to the JSON file so concurrent
# client submissions never corrupt or lose data.
_file_lock = threading.Lock()

# ---------- logging setup ----------

logger = logging.getLogger("server")
logger.setLevel(logging.INFO)
_log_fmt = logging.Formatter("%(asctime)s  %(levelname)-5s  %(message)s",
                             datefmt="%Y-%m-%d %H:%M:%S")

# Console handler
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_log_fmt)
logger.addHandler(_ch)

# File handler (append mode, UTF-8)
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(_log_fmt)
logger.addHandler(_fh)


# ---------- helpers ----------

def _load_data(path: str) -> dict:
    """Load existing JSON data or return an empty dict."""
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_data(path: str, data: dict) -> None:
    """Atomically write data to *path* (write-tmp then rename)."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # os.replace is atomic on the same filesystem
    os.replace(tmp_path, path)


def merge_report(path: str, report: dict) -> str:
    """Merge a single client report into the collection file.

    Returns the key under which the report was stored.
    """
    hostname = report.get("hostname", "unknown")
    upload_time = report.get("upload_time", datetime.now().strftime("%Y%m%d_%H%M%S"))
    key = f"{hostname}:{upload_time}"

    with _file_lock:
        data = _load_data(path)
        data[key] = report
        _save_data(path, data)

    return key


# ---------- iperf3 port lock ----------

class IperfLock:
    """Logical lock for the externally-managed iperf3 server port.

    The iperf3 process is started by the user separately (e.g.
    ``iperf3 -s -p 60998``).  This class only tracks whether a client
    is currently using the port so that other clients wait their turn
    instead of getting RST.  Busy ports auto-release after *timeout*
    seconds in case a client crashes.
    """

    def __init__(self, port: int, timeout: int):
        self._port = port
        self._timeout = timeout
        self._lock = threading.Lock()
        self._busy = False
        self._client = ""
        self._ts = 0.0
        self._stop = threading.Event()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)

    def start(self):
        self._reaper.start()
        logger.info("iperf3 lock enabled for port %d (timeout %ds)",
                     self._port, self._timeout)

    def stop(self):
        self._stop.set()

    def allocate(self, client: str) -> int | None:
        """Return the port if free, or None if busy."""
        with self._lock:
            if not self._busy:
                self._busy = True
                self._client = client
                self._ts = time.time()
                logger.info("ALLOC  port %d -> %s", self._port, client)
                return self._port
        return None

    def release(self, port: int):
        with self._lock:
            if port == self._port and self._busy:
                logger.info("RELEASE  port %d (was %s)", self._port, self._client)
                self._busy = False
                self._client = ""
                self._ts = 0.0

    def status(self) -> dict:
        with self._lock:
            return {
                "port": self._port,
                "busy": self._busy,
                "client": self._client,
            }

    def _reap_loop(self):
        while not self._stop.is_set():
            self._stop.wait(10)
            now = time.time()
            with self._lock:
                if self._busy and (now - self._ts) > self._timeout:
                    logger.warning(
                        "TIMEOUT  port %d held by %s for %ds — auto-releasing",
                        self._port, self._client, int(now - self._ts),
                    )
                    self._busy = False
                    self._client = ""
                    self._ts = 0.0


# Module-level lock reference, set in main()
_iperf_lock: IperfLock | None = None


# ---------- HTTP handler ----------

class ReportHandler(BaseHTTPRequestHandler):
    """Handle POST /report from diagnostic clients."""

    def _client(self) -> str:
        """Return 'ip:port' of the remote client."""
        return f"{self.client_address[0]}:{self.client_address[1]}"

    def _client_ip(self) -> str:
        return self.client_address[0]

    # Route all default HTTP log lines through our logger
    def log_message(self, fmt, *args):
        logger.info("%s  %s", self._client(), fmt % args)

    def _json_response(self, code: int, obj: dict):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        """GET / returns a simple status page; GET /data returns the JSON."""
        logger.info("CONNECT  %s  GET %s", self._client(), self.path)

        # ---- iperf port allocation ----
        if self.path == "/iperf-port":
            port = _iperf_lock.allocate(self._client_ip())
            if port is None:
                self._json_response(503, {
                    "error": "iperf3 port is busy, try again later"
                })
                logger.warning("FAIL  %s  GET /iperf-port  503 busy",
                               self._client())
                return
            self._json_response(200, {"port": port})
            logger.info("SUCCESS  %s  GET /iperf-port  -> %d", self._client(), port)
            return

        # ---- iperf port release ----
        if self.path.startswith("/iperf-release/"):
            try:
                port = int(self.path.split("/")[-1])
            except ValueError:
                self._json_response(400, {"error": "invalid port"})
                return
            _iperf_lock.release(port)
            self._json_response(200, {"status": "released", "port": port})
            logger.info("SUCCESS  %s  GET /iperf-release/%d", self._client(), port)
            return

        # ---- iperf status ----
        if self.path == "/iperf-status":
            self._json_response(200, _iperf_lock.status())
            logger.info("SUCCESS  %s  GET /iperf-status", self._client())
            return

        if self.path == "/data":
            with _file_lock:
                data = _load_data(DATA_FILE)
            payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            logger.info("SUCCESS  %s  GET /data  (%d reports)", self._client(), len(data))
            return

        # Default: status page
        with _file_lock:
            data = _load_data(DATA_FILE)
        count = len(data)
        clients = "\n".join(f"  - {k}" for k in sorted(data.keys())) if data else "  (none)"
        lines = [
            "Network Inspect Server",
            "======================",
            f"Reports collected: {count}",
            "",
            f"Clients:\n{clients}",
        ]
        if _iperf_lock is not None:
            s = _iperf_lock.status()
            state = f"BUSY ({s['client']})" if s["busy"] else "free"
            lines.append("")
            lines.append(f"iperf3 port {s['port']}: {state}")
        body = ("\n".join(lines) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        logger.info("SUCCESS  %s  GET /  (%d reports)", self._client(), count)

    def do_POST(self):
        logger.info("CONNECT  %s  POST %s", self._client(), self.path)

        if self.path != "/report":
            logger.warning("FAIL  %s  POST %s  404 Not Found", self._client(), self.path)
            self.send_error(404, "Not Found")
            return

        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            logger.warning("FAIL  %s  POST /report  400 Empty body", self._client())
            self.send_error(400, "Empty body")
            return
        if content_length > 10 * 1024 * 1024:  # 10 MB safety limit
            logger.warning("FAIL  %s  POST /report  413 Payload too large (%d bytes)",
                           self._client(), content_length)
            self.send_error(413, "Payload too large")
            return

        raw = self.rfile.read(content_length)
        try:
            report = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("FAIL  %s  POST /report  400 Invalid JSON: %s", self._client(), exc)
            self.send_error(400, f"Invalid JSON: {exc}")
            return

        if not isinstance(report, dict):
            logger.warning("FAIL  %s  POST /report  400 Top-level not object", self._client())
            self.send_error(400, "Top-level JSON must be an object")
            return

        # Merge into collection
        key = merge_report(DATA_FILE, report)

        resp = json.dumps({"status": "ok", "key": key}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)
        logger.info("SUCCESS  %s  POST /report  stored -> %s", self._client(), key)


class ThreadedHTTPServer(HTTPServer):
    """Handle each request in a new thread so multiple clients don't block."""
    allow_reuse_address = True

    def process_request(self, request, client_address):
        t = threading.Thread(target=self._handle, args=(request, client_address), daemon=True)
        t.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


# ---------- main ----------

def parse_args():
    parser = argparse.ArgumentParser(description="Network Inspect Collection Server")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind address (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port (default: {DEFAULT_PORT})")
    parser.add_argument("--output", default=DATA_FILE, help=f"Output JSON file (default: {DATA_FILE})")
    parser.add_argument("--log", default=LOG_FILE, help=f"Log file path (default: {LOG_FILE})")

    # iperf3 port lock (iperf3 is started separately by the user)
    parser.add_argument(
        "--iperf-port", type=int, default=60998,
        help="iperf3 port to manage for client queuing (default: 60998)",
    )
    parser.add_argument(
        "--iperf-timeout", type=int, default=120,
        help="Auto-release iperf3 port lock after N seconds (default: 120)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    global DATA_FILE, _iperf_lock
    DATA_FILE = args.output

    # Update file handler path if user changed --log
    _fh.close()
    logger.removeHandler(_fh)
    fh = logging.FileHandler(args.log, encoding="utf-8")
    fh.setFormatter(_log_fmt)
    logger.addHandler(fh)

    # Start iperf3 port lock for client queuing
    _iperf_lock = IperfLock(
        port=args.iperf_port,
        timeout=args.iperf_timeout,
    )
    _iperf_lock.start()

    server = ThreadedHTTPServer((args.host, args.port), ReportHandler)
    print()
    print("  Network Inspect Collection Server")
    print("  ==================================")
    print(f"  Listening on  : {args.host}:{args.port}")
    print(f"  Data file     : {os.path.abspath(DATA_FILE)}")
    print(f"  Log file      : {os.path.abspath(args.log)}")
    print(f"  POST endpoint : http://{args.host}:{args.port}/report")
    print(f"  GET  status   : http://{args.host}:{args.port}/")
    print(f"  GET  JSON     : http://{args.host}:{args.port}/data")
    print()
    print(f"  iperf3 lock   : port {args.iperf_port} (timeout {args.iperf_timeout}s)")
    print(f"  iperf3 alloc  : GET http://{args.host}:{args.port}/iperf-port")
    print(f"  iperf3 release: GET http://{args.host}:{args.port}/iperf-release/{args.iperf_port}")
    print()
    print("  Waiting for client reports... (Ctrl+C to stop)")
    print()

    logger.info("Server started on %s:%d", args.host, args.port)
    logger.info("iperf3 lock: port %d (timeout %ds)",
                 args.iperf_port, args.iperf_timeout)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down (Ctrl+C)")
        print("\n  Server shutting down.")
        _iperf_lock.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
