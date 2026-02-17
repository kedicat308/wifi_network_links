#!/usr/bin/env python3
"""
WiFi Network Diagnostics Tool (iperf2 variant)
================================================
A terminal-based network diagnostic tool inspired by trippy.
Combines ping, tracert, iperf2 bandwidth testing, and WiFi analysis
into a unified TUI dashboard.

This variant uses iperf2 (iperf-2.2.1-win64.exe) instead of iperf3.
Server port defaults to 62998.

Usage:
    python main_iperf2.py                         (use default IP 10.216.65.91 for all)
    python main_iperf2.py --target 192.168.1.1    (unified IP for ping/tracert/iperf)
    python main_iperf2.py --target 10.216.65.91 --iperf-server 192.168.1.100

Requirements:
    pip install rich
"""

import argparse
import os
import sys
import threading
import time

from modules.ping_tracer import PingTracer
from modules.iperf2_tester import Iperf2Tester
from modules.wifi_scanner import WifiScanner
from modules.dashboard_iperf2 import Dashboard


def get_resource_path(relative: str = "") -> str:
    """Return the path to bundled resources (iperf exe, etc.).

    When running as a PyInstaller --onefile bundle, data files are extracted
    to a temporary ``sys._MEIPASS`` directory.  During normal development the
    resources live next to main_iperf2.py (the project root).
    """
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative) if relative else base


def parse_args():
    parser = argparse.ArgumentParser(
        description="WiFi Network Diagnostics - Terminal Dashboard (iperf2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python main_iperf2.py                                (default: 10.216.65.91 for all)
  python main_iperf2.py --target 192.168.1.1           (unified IP for ping/tracert/iperf)
  python main_iperf2.py --target 10.216.65.91 --iperf-server 192.168.1.100
  python main_iperf2.py --target 10.216.65.91 --iperf-port 62998 --iperf-proto udp
  python main_iperf2.py --no-wifi --target 10.216.65.91
""",
    )

    # Ping / Tracert
    parser.add_argument(
        "--target", "-t",
        default="10.216.65.91",
        help="Target host for ping, tracert, and iperf (default: 10.216.65.91)",
    )
    parser.add_argument(
        "--ping-count", "-n",
        type=int,
        default=50,
        help="Number of ping packets (default: 50)",
    )
    parser.add_argument(
        "--tracert-max-hops",
        type=int,
        default=15,
        help="Max hops for tracert (default: 15)",
    )
    parser.add_argument(
        "--tracert-timeout",
        type=int,
        default=30,
        help="Overall tracert timeout in seconds (default: 30)",
    )

    # iperf2
    parser.add_argument(
        "--iperf-server", "-s",
        default=None,
        help="iperf2 server address (default: same as --target)",
    )
    parser.add_argument(
        "--iperf-port",
        type=int,
        default=62998,
        help="iperf2 server port (default: 62998)",
    )
    parser.add_argument(
        "--iperf-duration",
        type=int,
        default=10,
        help="iperf2 test duration in seconds (default: 10)",
    )
    parser.add_argument(
        "--iperf-proto",
        choices=["tcp", "udp"],
        default="tcp",
        help="iperf2 protocol: tcp or udp (default: tcp)",
    )
    parser.add_argument(
        "--iperf-bandwidth",
        default="100M",
        help="iperf2 UDP target bandwidth (default: 100M, only for UDP)",
    )
    parser.add_argument(
        "--no-iperf",
        action="store_true",
        help="Skip iperf2 bandwidth test",
    )

    # WiFi
    parser.add_argument(
        "--no-wifi",
        action="store_true",
        help="Skip WiFi scanning (for non-WiFi connections)",
    )
    parser.add_argument(
        "--wifi-scans",
        type=int,
        default=5,
        help="Number of WiFi scan iterations (default: 5)",
    )
    parser.add_argument(
        "--no-reconnect",
        action="store_true",
        help="Skip WiFi disconnect/reconnect test",
    )

    args = parser.parse_args()

    # Unified IP: if iperf-server not specified, use same IP as target
    if args.iperf_server is None:
        args.iperf_server = args.target

    return args


def main():
    args = parse_args()

    print("\n  WiFi Network Diagnostics Tool (iperf2)")
    print("  ========================================")
    print(f"  Target:       {args.target}")
    if not args.no_iperf:
        print(f"  iperf2:       {args.iperf_server}:{args.iperf_port} ({args.iperf_proto.upper()})")
    print(f"  WiFi scan:    {'disabled' if args.no_wifi else f'{args.wifi_scans} rounds'}")
    print()
    print("  Starting in 2 seconds... (Ctrl+C to cancel)")
    time.sleep(2)

    # Initialize modules
    ping_tracer = PingTracer(
        target=args.target,
        ping_count=args.ping_count,
        tracert_max_hops=args.tracert_max_hops,
        tracert_max_seconds=args.tracert_timeout,
    )

    iperf_tester = None
    if not args.no_iperf:
        # Resolve iperf2 path: bundled resource (PyInstaller) or local file
        # Try iperf-2.2.1-win64.exe first, then generic iperf.exe
        iperf_exe = ""
        for name in ("iperf-2.2.1-win64.exe", "iperf.exe"):
            candidate = get_resource_path(name)
            if os.path.isfile(candidate):
                iperf_exe = candidate
                break

        iperf_tester = Iperf2Tester(
            server=args.iperf_server,
            port=args.iperf_port,
            duration=args.iperf_duration,
            protocol=args.iperf_proto,
            bandwidth=args.iperf_bandwidth,
            iperf_path=iperf_exe,
        )

    wifi_scanner = None
    if not args.no_wifi:
        wifi_scanner = WifiScanner(
            scan_count=args.wifi_scans,
            scan_interval=3.0,
            do_reconnect=not args.no_reconnect,
        )

    # Build dashboard
    dashboard = Dashboard(
        ping_tracer=ping_tracer,
        iperf_tester=iperf_tester,
        wifi_scanner=wifi_scanner,
    )

    # Helper: start network tests after WiFi reconnect is done (or immediately
    # if WiFi is disabled).
    def _start_network_tests():
        if wifi_scanner:
            wifi_scanner.reconnect_done.wait()
        ping_tracer.start_ping()
        ping_tracer.start_tracert()
        if iperf_tester:
            iperf_tester.start()

    # Start tasks
    try:
        if wifi_scanner:
            wifi_scanner.start()

        starter = threading.Thread(target=_start_network_tests, daemon=True)
        starter.start()

        dashboard.run()

    except KeyboardInterrupt:
        pass
    finally:
        ping_tracer.stop()
        if iperf_tester:
            iperf_tester.stop()
        if wifi_scanner:
            wifi_scanner.stop()

        print("\n  Diagnostics complete. Results summary:")
        print(f"  Ping: {ping_tracer.ping_stats.sent} sent, "
              f"{ping_tracer.ping_stats.loss_pct:.1f}% loss, "
              f"avg {ping_tracer.ping_stats.avg_ms:.0f}ms")

        # Tracert summary
        tr = ping_tracer.tracert_stats
        if tr.hops:
            all_rtts = []
            lost_hops = 0
            for hop in tr.hops:
                if hop.lost:
                    lost_hops += 1
                else:
                    for rtt in [hop.rtt1_ms, hop.rtt2_ms, hop.rtt3_ms]:
                        if rtt >= 0:
                            all_rtts.append(rtt)
            avg_rtt = sum(all_rtts) / len(all_rtts) if all_rtts else 0
            print(f"  Tracert: {len(tr.hops)} hops"
                  f" (lost: {lost_hops},"
                  f" RTT min/avg/max:"
                  f" {min(all_rtts):.0f}/{avg_rtt:.0f}/{max(all_rtts):.0f} ms)"
                  if all_rtts else
                  f"  Tracert: {len(tr.hops)} hops (all timed out)")
            for hop in tr.hops:
                rtts = []
                for rtt in [hop.rtt1_ms, hop.rtt2_ms, hop.rtt3_ms]:
                    rtts.append("*" if rtt < 0 else f"{rtt:.0f}ms")
                print(f"    {hop.hop:>2d}  {hop.ip:<16s}"
                      f"  {rtts[0]:>6s}  {rtts[1]:>6s}  {rtts[2]:>6s}")
        elif tr.error:
            print(f"  Tracert: {tr.error}")

        if iperf_tester:
            dl = iperf_tester.stats.download
            ul = iperf_tester.stats.upload
            print(f"  iperf2 Download: {dl.bandwidth_mbps:.1f} Mbps")
            print(f"  iperf2 Upload:   {ul.bandwidth_mbps:.1f} Mbps")
        if wifi_scanner and wifi_scanner.stats.current:
            info = wifi_scanner.stats.current
            print(f"  WiFi: {info.ssid} (Signal: {info.signal_pct}%, "
                  f"Channel: {info.channel})")
        print()


if __name__ == "__main__":
    main()
