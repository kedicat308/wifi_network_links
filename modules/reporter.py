"""
Client Reporter
================
Serializes diagnostic results from all modules into a JSON-friendly dict
and uploads the report to the collection server via HTTP POST.
"""

import json
import os
import socket
import urllib.request
import urllib.error
from datetime import datetime

DEFAULT_REPORT_URL = "http://10.216.65.91:62998/report"


def _get_client_name() -> str:
    """Return USERNAME env var if available, otherwise hostname."""
    return os.environ.get("USERNAME") or socket.gethostname()


def _build_report(ping_tracer, iperf_tester, wifi_scanner) -> dict:
    """Collect all module stats into a single serializable dict."""
    client_name = _get_client_name()
    upload_time = datetime.now().strftime("%Y%m%d_%H%M%S")

    report = {
        "hostname": client_name,
        "upload_time": upload_time,
        "ping": _serialize_ping(ping_tracer),
        "tracert": _serialize_tracert(ping_tracer),
    }

    if iperf_tester is not None:
        report["iperf"] = _serialize_iperf(iperf_tester)

    if wifi_scanner is not None:
        report["wifi"] = _serialize_wifi(wifi_scanner)

    return report


def _serialize_ping(pt) -> dict:
    """Serialize PingStats, stripping the mutable 'running' flag."""
    s = pt.ping_stats
    return {
        "target": s.target,
        "sent": s.sent,
        "received": s.received,
        "loss_pct": round(s.loss_pct, 2),
        "min_ms": round(s.min_ms, 2),
        "avg_ms": round(s.avg_ms, 2),
        "max_ms": round(s.max_ms, 2),
        "jitter_ms": round(s.jitter_ms, 2),
    }


def _serialize_tracert(pt) -> dict:
    tr = pt.tracert_stats
    hops = []
    for h in tr.hops:
        hops.append({
            "hop": h.hop,
            "ip": h.ip,
            "hostname": h.hostname,
            "rtt1_ms": round(h.rtt1_ms, 2),
            "rtt2_ms": round(h.rtt2_ms, 2),
            "rtt3_ms": round(h.rtt3_ms, 2),
            "lost": h.lost,
        })
    return {
        "target": tr.target,
        "hops": hops,
        "done": tr.done,
        "error": tr.error,
    }


def _serialize_iperf(it) -> dict:
    s = it.stats
    return {
        "server": s.server,
        "port": s.port,
        "current_phase": s.current_phase,
        "download": _serialize_iperf_result(s.download),
        "upload": _serialize_iperf_result(s.upload),
        "error": s.error,
    }


def _serialize_iperf_result(r) -> dict:
    intervals = []
    for iv in r.intervals:
        iv_dict = {
            "interval": iv.interval,
            "transfer_bytes": iv.transfer_bytes,
            "bandwidth_mbps": round(iv.bandwidth_mbps, 2),
        }
        if iv.retransmits:
            iv_dict["retransmits"] = iv.retransmits
        intervals.append(iv_dict)
    result = {
        "direction": r.direction,
        "protocol": r.protocol,
        "bandwidth_mbps": round(r.bandwidth_mbps, 2),
        "transfer_mb": round(r.transfer_mb, 2),
        "retransmits": r.retransmits,
        "done": r.done,
        "error": r.error,
        "intervals": intervals,
    }
    # iperf3 has jitter/loss fields, iperf2 may not
    if hasattr(r, "jitter_ms"):
        result["jitter_ms"] = round(r.jitter_ms, 2)
        result["lost_packets"] = r.lost_packets
        result["total_packets"] = r.total_packets
        result["loss_pct"] = round(r.loss_pct, 2)
    return result


def _serialize_wifi(ws) -> dict:
    s = ws.stats
    result = {
        "current_phase": s.current_phase,
        "total_scans": s.total_scans,
        "completed_scans": s.completed_scans,
        "reconnect_log": list(s.reconnect_log),
        "error": s.error,
    }
    if s.current:
        c = s.current
        result["current"] = {
            "ssid": c.ssid,
            "bssid": c.bssid,
            "state": c.state,
            "radio_type": c.radio_type,
            "auth": c.auth,
            "cipher": c.cipher,
            "channel": c.channel,
            "signal_pct": c.signal_pct,
            "rx_rate_mbps": c.rx_rate_mbps,
            "tx_rate_mbps": c.tx_rate_mbps,
        }
    # Scan results
    scans = []
    for sr in s.scan_results:
        networks = []
        for net in sr.networks:
            bssids = []
            for b in net.bssids:
                bssids.append({
                    "bssid": b.bssid,
                    "signal_pct": b.signal_pct,
                    "radio_type": b.radio_type,
                    "channel": b.channel,
                    "band": b.band,
                })
            networks.append({
                "ssid": net.ssid,
                "network_type": net.network_type,
                "auth": net.auth,
                "cipher": net.cipher,
                "best_signal": net.best_signal,
                "bssids": bssids,
            })
        scan_entry = {
            "scan_num": sr.scan_num,
            "timestamp": sr.timestamp,
            "networks": networks,
            "error": sr.error,
        }
        scans.append(scan_entry)
    result["scan_results"] = scans
    return result


def upload_report(ping_tracer, iperf_tester, wifi_scanner,
                  server_url: str) -> tuple[bool, str]:
    """Build the report and POST it to the collection server.

    Args:
        ping_tracer: PingTracer instance (required).
        iperf_tester: IperfTester instance or None.
        wifi_scanner: WifiScanner instance or None.
        server_url: Full URL, e.g. ``http://10.216.65.91:62998/report``.

    Returns:
        (success: bool, message: str)
    """
    report = _build_report(ping_tracer, iperf_tester, wifi_scanner)
    payload = json.dumps(report, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        server_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            key = body.get("key", "?")
            return True, f"Report uploaded -> {key}"
    except urllib.error.HTTPError as exc:
        return False, f"Server returned {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return False, f"Connection failed: {exc.reason}"
    except Exception as exc:
        return False, f"Upload error: {exc}"
