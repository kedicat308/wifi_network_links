#!/usr/bin/env python3
"""
Network Inspect Collection Server
===================================
Listens on a specified address and port, receives JSON diagnostic reports
from multiple clients, and merges them into a single ``network_inspect.json``
file.  Each client entry is keyed by ``hostname:upload_time`` so concurrent
writes from different machines never collide.

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


# ---------- HTTP handler ----------

class ReportHandler(BaseHTTPRequestHandler):
    """Handle POST /report from diagnostic clients."""

    def _client(self) -> str:
        """Return 'ip:port' of the remote client."""
        return f"{self.client_address[0]}:{self.client_address[1]}"

    # Route all default HTTP log lines through our logger
    def log_message(self, fmt, *args):
        logger.info("%s  %s", self._client(), fmt % args)

    def do_GET(self):
        """GET / returns a simple status page; GET /data returns the JSON."""
        logger.info("CONNECT  %s  GET %s", self._client(), self.path)

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
        body = (
            f"Network Inspect Server\n"
            f"======================\n"
            f"Reports collected: {count}\n\n"
            f"Clients:\n{clients}\n"
        ).encode("utf-8")
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
    return parser.parse_args()


def main():
    args = parse_args()
    global DATA_FILE
    DATA_FILE = args.output

    # Update file handler path if user changed --log
    _fh.close()
    logger.removeHandler(_fh)
    fh = logging.FileHandler(args.log, encoding="utf-8")
    fh.setFormatter(_log_fmt)
    logger.addHandler(fh)

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
    print("  Waiting for client reports... (Ctrl+C to stop)")
    print()

    logger.info("Server started on %s:%d", args.host, args.port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down (Ctrl+C)")
        print("\n  Server shutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
