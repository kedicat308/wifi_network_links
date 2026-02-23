#!/usr/bin/env python3
"""
Cisco IOS-XE NETCONF CPU & Memory Monitor
==========================================
Connects to a Cisco IOS-XE device via NETCONF (port 830) and displays:
  - Overall CPU utilization (5s / 1min / 5min)
  - Top CPU-consuming processes
  - Memory pool utilization

Usage:
    python netconf_stats.py
    python netconf_stats.py --host devnetsandboxiosxec8k.cisco.com \
                            --port 830 \
                            --user fanwei19751208 \
                            --password '-0vkBE4V-j0e' \
                            --top 15 \
                            --watch 5

Arguments:
    --host       NETCONF host  (default: devnetsandboxiosxec8k.cisco.com)
    --port       NETCONF port  (default: 830)
    --user       SSH username  (default: fanwei19751208)
    --password   SSH password
    --top        Number of top processes to show  (default: 10)
    --watch      Poll interval in seconds; 0 = run once  (default: 0)
"""

import argparse
import getpass
import time

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.live import Live
from rich.layout import Layout
from rich.text import Text
from rich import box

from modules.netconf_stats import connect, get_cpu_stats, get_process_cpu, get_memory_stats

console = Console()

DEFAULT_HOST = "devnetsandboxiosxec8k.cisco.com"
DEFAULT_PORT = 830
DEFAULT_USER = "fanwei19751208"


# ─── Rendering helpers ───────────────────────────────────────────────────────

def _bar(pct: float, width: int = 20) -> str:
    """Return a simple ASCII bar, e.g. '████████░░░░░░░░░░░░  40%'"""
    filled = int(round(pct / 100 * width))
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar}  {pct:5.1f}%"


def render_cpu_overview(stats: dict) -> Panel:
    """Render the CPU utilization summary panel."""
    if not stats:
        return Panel("[red]No CPU data retrieved[/red]", title="CPU Utilization")

    lines = []
    for label, key in [
        ("5 Seconds  ", "five_seconds"),
        ("5s (Intr)  ", "five_seconds_intr"),
        ("1 Minute   ", "one_minute"),
        ("5 Minutes  ", "five_minutes"),
    ]:
        pct = stats.get(key, 0)
        color = "green" if pct < 50 else ("yellow" if pct < 80 else "red")
        bar = _bar(pct)
        lines.append(f"  [bold]{label}[/bold] [{color}]{bar}[/{color}]")

    return Panel("\n".join(lines), title="[bold cyan]CPU Utilization[/bold cyan]", box=box.ROUNDED)


def render_process_table(processes: list) -> Table:
    """Render a table of top CPU processes."""
    table = Table(
        title="[bold cyan]Top CPU Processes[/bold cyan]",
        box=box.SIMPLE_HEAVY,
        header_style="bold magenta",
        show_lines=False,
    )
    table.add_column("PID",       justify="right",  style="dim",    width=8)
    table.add_column("Process",   justify="left",   style="white",  width=35)
    table.add_column("5-Sec %",   justify="right",  style="yellow", width=10)
    table.add_column("1-Min %",   justify="right",  style="cyan",   width=10)
    table.add_column("5-Min %",   justify="right",  style="cyan",   width=10)

    for p in processes:
        color = "red" if p["five_sec"] >= 10 else ("yellow" if p["five_sec"] >= 5 else "white")
        table.add_row(
            str(p["pid"]),
            p["name"],
            f"[{color}]{p['five_sec']:>6}[/{color}]",
            str(p["one_min"]),
            str(p["five_min"]),
        )

    return table


def render_memory_table(pools: list) -> Table:
    """Render a table of memory pools."""
    table = Table(
        title="[bold cyan]Memory Pools[/bold cyan]",
        box=box.SIMPLE_HEAVY,
        header_style="bold magenta",
        show_lines=False,
    )
    table.add_column("Pool",      justify="left",  style="white",  width=20)
    table.add_column("Total(KB)", justify="right", style="dim",    width=12)
    table.add_column("Used(KB)",  justify="right", style="yellow", width=12)
    table.add_column("Free(KB)",  justify="right", style="green",  width=12)
    table.add_column("Usage",     justify="left",  style="cyan",   width=30)

    for pool in pools:
        color = "green" if pool["used_pct"] < 60 else ("yellow" if pool["used_pct"] < 80 else "red")
        bar   = _bar(pool["used_pct"], width=16)
        table.add_row(
            pool["name"],
            f"{pool['total_kb']:,}",
            f"{pool['used_kb']:,}",
            f"{pool['free_kb']:,}",
            f"[{color}]{bar}[/{color}]",
        )

    return table


# ─── Main logic ──────────────────────────────────────────────────────────────

def fetch_and_display(conn, top_n: int) -> None:
    """Fetch all stats and print them to the console."""
    console.print()

    with console.status("[bold green]Fetching CPU stats...[/bold green]"):
        cpu_stats  = get_cpu_stats(conn)
        cpu_procs  = get_process_cpu(conn, top_n=top_n)

    with console.status("[bold green]Fetching memory stats...[/bold green]"):
        mem_pools  = get_memory_stats(conn)

    console.print(render_cpu_overview(cpu_stats))
    console.print()
    console.print(render_process_table(cpu_procs))
    console.print()
    console.print(render_memory_table(mem_pools))
    console.print()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Cisco IOS-XE NETCONF CPU & Memory Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host",     default=DEFAULT_HOST,  help="NETCONF host")
    parser.add_argument("--port",     type=int, default=DEFAULT_PORT, help="NETCONF port (default: 830)")
    parser.add_argument("--user",     default=DEFAULT_USER,  help="SSH username")
    parser.add_argument("--password", default=None,          help="SSH password (prompted if omitted)")
    parser.add_argument("--top",      type=int, default=10,  help="Top N processes to display (default: 10)")
    parser.add_argument(
        "--watch", type=int, default=0,
        help="Poll every N seconds; 0 = run once (default: 0)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    password = args.password or getpass.getpass(
        f"Password for {args.user}@{args.host}: "
    )

    console.rule(f"[bold cyan]Connecting to {args.host}:{args.port} via NETCONF[/bold cyan]")

    try:
        with connect(args.host, args.port, args.user, password) as conn:
            console.print(f"[bold green]Connected.[/bold green]  "
                          f"Server capabilities: {len(conn.server_capabilities)} advertised.\n")

            if args.watch == 0:
                fetch_and_display(conn, args.top)
            else:
                console.print(
                    f"[bold yellow]Watch mode:[/bold yellow] refreshing every "
                    f"{args.watch}s.  Press Ctrl+C to stop.\n"
                )
                while True:
                    fetch_and_display(conn, args.top)
                    console.rule(
                        f"[dim]Next refresh in {args.watch}s — Ctrl+C to quit[/dim]"
                    )
                    time.sleep(args.watch)

    except KeyboardInterrupt:
        console.print("\n[dim]Stopped by user.[/dim]")
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
