"""
NETCONF Stats Module
====================
Connects to a Cisco IOS-XE device via NETCONF and retrieves
CPU and memory utilization using YANG operational models.
"""

import xml.etree.ElementTree as ET
from ncclient import manager
from ncclient.transport.errors import SSHError, AuthenticationError


# NETCONF filter for CPU utilization (Cisco-IOS-XE-process-cpu-oper)
CPU_FILTER = """
<filter xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
  <cpu-usage xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-process-cpu-oper">
    <cpu-utilization/>
  </cpu-usage>
</filter>
"""

# NETCONF filter for memory statistics (Cisco-IOS-XE-memory-oper)
MEMORY_FILTER = """
<filter xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
  <memory-statistics xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-memory-oper">
    <memory-statistic/>
  </memory-statistics>
</filter>
"""

# NETCONF filter for per-process CPU (top processes)
PROCESS_CPU_FILTER = """
<filter xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
  <cpu-usage xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-process-cpu-oper">
    <cpu-utilization>
      <cpu-usage-processes/>
    </cpu-utilization>
  </cpu-usage>
</filter>
"""

CPU_NS = "http://cisco.com/ns/yang/Cisco-IOS-XE-process-cpu-oper"
MEM_NS = "http://cisco.com/ns/yang/Cisco-IOS-XE-memory-oper"


def connect(host: str, port: int, username: str, password: str):
    """Open a NETCONF session and return the manager object."""
    return manager.connect(
        host=host,
        port=port,
        username=username,
        password=password,
        hostkey_verify=False,
        device_params={"name": "iosxe"},
        manager_params={"timeout": 60},
        look_for_keys=False,
        allow_agent=False,
    )


def get_cpu_stats(conn) -> dict:
    """
    Retrieve overall CPU utilization (5s / 1min / 5min averages).

    Returns a dict, e.g.:
    {
        "five_seconds": 3,
        "one_minute": 4,
        "five_minutes": 3,
        "five_seconds_intr": 1,   # interrupt %
    }
    """
    reply = conn.get(filter=("subtree", CPU_FILTER))
    root = ET.fromstring(reply.xml)

    ns = {"cpu": CPU_NS}
    util = root.find(".//cpu:cpu-utilization", ns)
    if util is None:
        return {}

    def _int(tag):
        el = util.find(f"cpu:{tag}", ns)
        return int(el.text) if el is not None and el.text else 0

    return {
        "five_seconds":      _int("five-seconds"),
        "five_seconds_intr": _int("five-seconds-intr"),
        "one_minute":        _int("one-minute"),
        "five_minutes":      _int("five-minutes"),
    }


def get_process_cpu(conn, top_n: int = 10) -> list:
    """
    Retrieve per-process CPU usage.

    Returns a list of dicts sorted by 5-second CPU descending:
    [
        {"pid": 1, "name": "...", "five_sec": 2, "one_min": 1, "five_min": 1},
        ...
    ]
    """
    reply = conn.get(filter=("subtree", PROCESS_CPU_FILTER))
    root = ET.fromstring(reply.xml)

    ns = {"cpu": CPU_NS}
    processes = []
    for proc in root.findall(".//cpu:cpu-usage-process", ns):
        def _text(tag):
            el = proc.find(f"cpu:{tag}", ns)
            return el.text if el is not None else ""

        def _int(tag):
            el = proc.find(f"cpu:{tag}", ns)
            return int(el.text) if el is not None and el.text else 0

        processes.append({
            "pid":      _int("pid"),
            "name":     _text("name"),
            "five_sec": _int("five-seconds"),
            "one_min":  _int("one-minute"),
            "five_min": _int("five-minutes"),
        })

    processes.sort(key=lambda p: p["five_sec"], reverse=True)
    return processes[:top_n]


def get_memory_stats(conn) -> list:
    """
    Retrieve memory pool statistics.

    Returns a list of dicts, one per memory pool:
    [
        {
            "name": "Processor",
            "total_kb": 2097152,
            "used_kb":  512000,
            "free_kb":  1585152,
            "used_pct": 24.4,
        },
        ...
    ]
    """
    reply = conn.get(filter=("subtree", MEMORY_FILTER))
    root = ET.fromstring(reply.xml)

    ns = {"mem": MEM_NS}
    pools = []
    for stat in root.findall(".//mem:memory-statistic", ns):
        def _text(tag):
            el = stat.find(f"mem:{tag}", ns)
            return el.text if el is not None else ""

        def _int(tag):
            el = stat.find(f"mem:{tag}", ns)
            return int(el.text) if el is not None and el.text else 0

        total = _int("total-memory")
        used  = _int("used-memory")
        free  = total - used if total > 0 else _int("free-memory")
        pct   = round(used / total * 100, 1) if total > 0 else 0.0

        pools.append({
            "name":     _text("name"),
            "total_kb": total // 1024,
            "used_kb":  used  // 1024,
            "free_kb":  free  // 1024,
            "used_pct": pct,
        })

    return pools
