"""Unified TUI dashboard (iperf2 variant) - all output in one terminal screen using rich."""

import sys
import time
import threading
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align


# Sparkline/bar chart characters for signal visualization
SPARK_CHARS = "▁▂▃▄▅▆▇█"
BAR_FULL = "█"
BAR_HALF = "▌"
BAR_EMPTY = "░"


def spark_line(values: list, width: int = 40) -> str:
    """Generate a sparkline string from a list of values."""
    if not values:
        return ""
    # Take last `width` values
    vals = values[-width:]
    filtered = [v for v in vals if v >= 0]
    if not filtered:
        return "?" * len(vals)
    vmin, vmax = min(filtered), max(filtered)
    rng = vmax - vmin if vmax > vmin else 1
    result = []
    for v in vals:
        if v < 0:
            result.append("×")
        else:
            idx = int((v - vmin) / rng * (len(SPARK_CHARS) - 1))
            idx = max(0, min(idx, len(SPARK_CHARS) - 1))
            result.append(SPARK_CHARS[idx])
    return "".join(result)


def signal_bar(pct: int, width: int = 20) -> Text:
    """Generate a colored signal strength bar."""
    filled = int(pct / 100 * width)
    if pct >= 70:
        color = "green"
    elif pct >= 40:
        color = "yellow"
    else:
        color = "red"
    text = Text()
    text.append(BAR_FULL * filled, style=color)
    text.append(BAR_EMPTY * (width - filled), style="dim")
    text.append(f" {pct}%", style=f"bold {color}")
    return text


def bandwidth_bar(mbps: float, max_mbps: float = 1000, width: int = 20) -> Text:
    """Generate a bandwidth bar."""
    if max_mbps <= 0:
        max_mbps = 1
    filled = int(min(mbps / max_mbps, 1.0) * width)
    text = Text()
    text.append(BAR_FULL * filled, style="cyan")
    text.append(BAR_EMPTY * (width - filled), style="dim")
    text.append(f" {mbps:.1f} Mbps", style="bold cyan")
    return text


class Dashboard:
    """Unified TUI dashboard combining all network diagnostics (iperf2 variant)."""

    def __init__(self, ping_tracer=None, iperf_tester=None, wifi_scanner=None):
        self.ping_tracer = ping_tracer
        self.iperf_tester = iperf_tester
        self.wifi_scanner = wifi_scanner
        self.console = Console()
        self._start_time = time.time()
        self._quit_event = threading.Event()
        self._all_done = False

    def _make_header(self) -> Panel:
        elapsed = int(time.time() - self._start_time)
        mins, secs = divmod(elapsed, 60)
        title = Text()
        title.append("  WiFi Network Diagnostics (iperf2)  ", style="bold white on blue")
        title.append(f"  [{mins:02d}:{secs:02d}]", style="dim")
        return Panel(Align.center(title), style="blue", height=3)

    def _make_ping_panel(self) -> Panel:
        if not self.ping_tracer:
            return Panel("Ping: not configured", title="Ping", border_style="dim")

        stats = self.ping_tracer.ping_stats
        table = Table(show_header=False, expand=True, padding=(0, 1))
        table.add_column("Key", style="bold", width=10)
        table.add_column("Value")

        status = "[green]Running[/]" if stats.running else "[dim]Stopped[/]"
        table.add_row("Target", f"{stats.target}  {status}")
        table.add_row("Sent/Recv", f"{stats.sent} / {stats.received}")

        loss_color = "green" if stats.loss_pct < 1 else "yellow" if stats.loss_pct < 5 else "red"
        table.add_row("Loss", f"[{loss_color}]{stats.loss_pct:.1f}%[/]")
        table.add_row("Min/Avg/Max",
                       f"{stats.min_ms:.0f} / {stats.avg_ms:.0f} / {stats.max_ms:.0f} ms")
        table.add_row("Jitter", f"{stats.jitter_ms:.1f} ms")

        # Sparkline of recent RTTs
        if stats.recent_rtts:
            spark = spark_line(stats.recent_rtts)
            table.add_row("RTT Graph", spark)

        if stats.error:
            table.add_row("Error", f"[red]{stats.error}[/]")

        return Panel(table, title="[bold]Ping[/]", border_style="green")

    def _make_tracert_panel(self) -> Panel:
        if not self.ping_tracer:
            return Panel("Tracert: not configured", title="Tracert", border_style="dim")

        stats = self.ping_tracer.tracert_stats
        table = Table(expand=True, padding=(0, 1))
        table.add_column("#", style="dim", width=3)
        table.add_column("IP Address", width=16)
        table.add_column("RTT 1", width=8)
        table.add_column("RTT 2", width=8)
        table.add_column("RTT 3", width=8)

        if stats.hops:
            for hop in stats.hops[-12:]:  # Show last 12 hops to fit
                rtt_fmt = lambda v: f"[red]*[/]" if v < 0 else f"{v:.0f}ms"
                style = "dim" if hop.lost else ""
                table.add_row(
                    str(hop.hop),
                    hop.ip,
                    rtt_fmt(hop.rtt1_ms),
                    rtt_fmt(hop.rtt2_ms),
                    rtt_fmt(hop.rtt3_ms),
                    style=style,
                )

            # Summary statistics row
            if stats.done:
                all_rtts = []
                lost_hops = 0
                for hop in stats.hops:
                    if hop.lost:
                        lost_hops += 1
                    else:
                        for rtt in [hop.rtt1_ms, hop.rtt2_ms, hop.rtt3_ms]:
                            if rtt >= 0:
                                all_rtts.append(rtt)

                table.add_row("", "", "", "", "", style="dim")
                summary_parts = [f"[bold]Hops:[/] {len(stats.hops)}"]
                if lost_hops > 0:
                    summary_parts.append(f"[red]Lost:[/] {lost_hops}")
                if all_rtts:
                    avg_rtt = sum(all_rtts) / len(all_rtts)
                    min_rtt = min(all_rtts)
                    max_rtt = max(all_rtts)
                    summary_parts.append(
                        f"[bold]RTT:[/] {min_rtt:.0f}/{avg_rtt:.0f}/{max_rtt:.0f} ms"
                    )
                table.add_row(
                    "",
                    " | ".join(summary_parts),
                    "", "", "",
                    style="cyan",
                )
        elif stats.running:
            table.add_row("", "[yellow]Waiting for hop responses...[/]", "", "", "")

        status = ""
        if stats.running:
            hop_count = len(stats.hops)
            status = f" [green]running... ({hop_count} hops)[/]"
        elif stats.done:
            status = f" [dim]done ({len(stats.hops)} hops)[/]"

        if stats.error:
            status += f" [red]{stats.error}[/]"

        return Panel(table, title=f"[bold]Traceroute → {stats.target}[/]{status}",
                     border_style="cyan")

    def _make_iperf_panel(self) -> Panel:
        if not self.iperf_tester:
            return Panel("iperf2: not configured", title="Bandwidth", border_style="dim")

        stats = self.iperf_tester.stats
        table = Table(show_header=False, expand=True, padding=(0, 1))
        table.add_column("Key", style="bold", width=12)
        table.add_column("Value")

        status = stats.current_phase or "idle"
        table.add_row("Server", f"{stats.server}:{stats.port}")
        table.add_row("Status", f"[yellow]{status}[/]" if stats.running else f"[dim]{status}[/]")

        if stats.error:
            table.add_row("Error", f"[red]{stats.error}[/]")

        # Download
        dl = stats.download
        if dl.done or dl.running:
            table.add_row("", "")
            table.add_row("[cyan]Download[/]", "[dim]TCP[/]")
            if dl.error:
                table.add_row("  Error", f"[red]{dl.error}[/]")
            else:
                table.add_row("  Bandwidth", bandwidth_bar(dl.bandwidth_mbps))
                table.add_row("  Transfer", f"{dl.transfer_mb:.1f} MB")

                # Interval sparkline
                if dl.intervals:
                    bws = [i.bandwidth_mbps for i in dl.intervals]
                    table.add_row("  Graph", spark_line(bws))

        # Upload
        ul = stats.upload
        if ul.done or ul.running:
            table.add_row("", "")
            table.add_row("[magenta]Upload[/]", "[dim]TCP[/]")
            if ul.error:
                table.add_row("  Error", f"[red]{ul.error}[/]")
            else:
                table.add_row("  Bandwidth", bandwidth_bar(ul.bandwidth_mbps))
                table.add_row("  Transfer", f"{ul.transfer_mb:.1f} MB")

                if ul.intervals:
                    bws = [i.bandwidth_mbps for i in ul.intervals]
                    table.add_row("  Graph", spark_line(bws))

        return Panel(table, title="[bold]Bandwidth (iperf2)[/]", border_style="magenta")

    def _make_wifi_info_panel(self) -> Panel:
        if not self.wifi_scanner:
            return Panel("WiFi: not configured", title="WiFi", border_style="dim")

        stats = self.wifi_scanner.stats
        info = stats.current

        table = Table(show_header=False, expand=True, padding=(0, 1))
        table.add_column("Key", style="bold", width=12)
        table.add_column("Value")

        phase = stats.current_phase or "idle"
        table.add_row("Phase",
                       f"[yellow]{phase}[/]" if stats.running else f"[dim]{phase}[/]")

        if info:
            table.add_row("SSID", f"[bold]{info.ssid}[/]" or "N/A")
            table.add_row("BSSID", info.bssid or "N/A")
            table.add_row("State", info.state or "N/A")
            table.add_row("Signal", signal_bar(info.signal_pct))
            table.add_row("Channel", str(info.channel) if info.channel else "N/A")
            table.add_row("Radio", info.radio_type or "N/A")
            table.add_row("Auth", info.auth or "N/A")
            table.add_row("RX/TX",
                          f"{info.rx_rate_mbps:.0f} / {info.tx_rate_mbps:.0f} Mbps")

        # Reconnect log
        if stats.reconnect_log:
            table.add_row("", "")
            for log_line in stats.reconnect_log[-3:]:
                table.add_row("", f"[dim]{log_line}[/]")

        if stats.error:
            table.add_row("Error", f"[red]{stats.error}[/]")

        return Panel(table, title="[bold]WiFi Connection[/]", border_style="yellow")

    def _make_wifi_scan_panel(self) -> Panel:
        if not self.wifi_scanner:
            return Panel("WiFi scan: not configured", title="WiFi Scan",
                         border_style="dim")

        stats = self.wifi_scanner.stats

        table = Table(expand=True, padding=(0, 0))
        table.add_column("SSID", width=20, no_wrap=True)
        table.add_column("BSSID", width=18)
        table.add_column("Ch", width=4)
        table.add_column("Signal", width=28)
        table.add_column("Radio", width=10)

        # Show the latest scan result
        if stats.scan_results:
            latest = stats.scan_results[-1]
            for net in latest.networks[:15]:  # Cap at 15 networks
                for i, bssid in enumerate(net.bssids[:3]):  # Cap at 3 BSSIDs per network
                    ssid_str = net.ssid if i == 0 else ""
                    table.add_row(
                        ssid_str,
                        bssid.bssid,
                        str(bssid.channel),
                        signal_bar(bssid.signal_pct, width=15),
                        bssid.radio_type,
                    )

        scan_info = f"Scan {stats.completed_scans}/{stats.total_scans}"
        if stats.scan_results and stats.scan_results[-1].timestamp:
            scan_info += f"  @{stats.scan_results[-1].timestamp}"

        return Panel(table, title=f"[bold]WiFi Networks[/]  [dim]{scan_info}[/]",
                     border_style="yellow")

    def _make_wifi_signal_history_panel(self) -> Panel:
        """Show signal strength history across scans as mini chart."""
        if not self.wifi_scanner or not self.wifi_scanner.stats.scan_results:
            return Panel("Waiting for scan data...",
                         title="Signal History", border_style="dim")

        stats = self.wifi_scanner.stats

        # Collect signal history for top networks
        network_signals = {}
        for scan in stats.scan_results:
            for net in scan.networks:
                if net.ssid not in network_signals:
                    network_signals[net.ssid] = []
                network_signals[net.ssid].append(net.best_signal)

        # Sort by latest signal and take top 8
        sorted_nets = sorted(
            network_signals.items(),
            key=lambda x: x[1][-1] if x[1] else 0,
            reverse=True,
        )[:8]

        lines = []
        for ssid, signals in sorted_nets:
            name = (ssid[:16] + "..") if len(ssid) > 18 else ssid
            spark = spark_line(signals, width=self.wifi_scanner.scan_count)
            latest = signals[-1] if signals else 0
            color = "green" if latest >= 70 else "yellow" if latest >= 40 else "red"
            lines.append(f"  {name:<20s} {spark} [{color}]{latest:3d}%[/]")

        content = "\n".join(lines) if lines else "No data yet"
        return Panel(content, title="[bold]Signal Trend[/]", border_style="yellow")

    def build_layout(self) -> Layout:
        """Build the full dashboard layout."""
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=1),
        )

        layout["body"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=1),
        )

        # Left side: Ping + Tracert + iperf
        layout["left"].split_column(
            Layout(name="ping", size=10),
            Layout(name="tracert", ratio=1),
            Layout(name="iperf", size=16),
        )

        # Right side: WiFi info + WiFi scan + signal history
        layout["right"].split_column(
            Layout(name="wifi_info", size=14),
            Layout(name="wifi_scan", ratio=1),
            Layout(name="wifi_signal", size=12),
        )

        # Fill panels
        layout["header"].update(self._make_header())
        layout["ping"].update(self._make_ping_panel())
        layout["tracert"].update(self._make_tracert_panel())
        layout["iperf"].update(self._make_iperf_panel())
        layout["wifi_info"].update(self._make_wifi_info_panel())
        layout["wifi_scan"].update(self._make_wifi_scan_panel())
        layout["wifi_signal"].update(self._make_wifi_signal_history_panel())

        # Footer
        if self._all_done:
            footer = Text(" All tests complete. Press q to quit ", style="bold green")
        else:
            footer = Text(" Running... Press q to quit ", style="dim")
        layout["footer"].update(Align.center(footer))

        return layout

    # ------------------------------------------------------------------
    # Keyboard listener — detect 'q' to quit
    # ------------------------------------------------------------------
    def _key_listener(self):
        """Background thread: poll for 'q' keypress (non-blocking)."""
        try:
            if sys.platform == "win32":
                import msvcrt
                while not self._quit_event.is_set():
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in (b"q", b"Q"):
                            self._quit_event.set()
                            return
                    time.sleep(0.1)
            else:
                import select
                import tty
                import termios
                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)
                try:
                    tty.setcbreak(fd)
                    while not self._quit_event.is_set():
                        if select.select([sys.stdin], [], [], 0.1)[0]:
                            ch = sys.stdin.read(1)
                            if ch in ("q", "Q"):
                                self._quit_event.set()
                                return
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            pass  # Ctrl+C still works as fallback

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self, refresh_rate: float = 0.5):
        """Run the live dashboard.  Stays on screen until 'q' is pressed."""
        key_thread = threading.Thread(target=self._key_listener, daemon=True)
        key_thread.start()

        with Live(self.build_layout(), console=self.console,
                  refresh_per_second=int(1 / refresh_rate), screen=True) as live:
            try:
                while not self._quit_event.is_set():
                    # Check if all tasks finished
                    if not self._all_done:
                        done = True
                        if self.ping_tracer and self.ping_tracer.ping_stats.running:
                            done = False
                        if self.ping_tracer and self.ping_tracer.tracert_stats.running:
                            done = False
                        if self.iperf_tester and self.iperf_tester.stats.running:
                            done = False
                        if self.wifi_scanner and self.wifi_scanner.stats.running:
                            done = False
                        if done and self._has_any_data():
                            self._all_done = True

                    live.update(self.build_layout())
                    time.sleep(refresh_rate)

            except KeyboardInterrupt:
                pass
            finally:
                self._quit_event.set()

    def _has_any_data(self) -> bool:
        if self.ping_tracer and self.ping_tracer.ping_stats.sent > 0:
            return True
        if self.iperf_tester and self.iperf_tester.stats.current_phase:
            return True
        if self.wifi_scanner and self.wifi_scanner.stats.current_phase:
            return True
        return False
