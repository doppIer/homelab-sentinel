import json
import os
import re
import subprocess
import time

from strands import tool

from sentinel.redact import redact

def run(cmd, limit=6000):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        text = (out.stdout + out.stderr).strip()
    except Exception as e:
        text = f"{type(e).__name__}: {e}"
    return redact(text)[-limit:]


def container_names():
    out = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=10)
    return set(out.stdout.split())


@tool
def container_logs(container: str, lines: int = 80) -> str:
    """Last log lines of one Docker container, stdout and stderr, with timestamps. Secrets are redacted.

    Args:
        container: Exact container name.
        lines: How many lines from the end, 10 to 200.
    """
    lines = max(10, min(int(lines), 200))
    return run(["docker", "logs", "--tail", str(lines), "--timestamps", container])


@tool
def container_status(container: str) -> str:
    """State, health, restart count, last exit code, OOM flag, memory limit and restart policy of one container.

    Args:
        container: Exact container name.
    """
    fmt = (
        "status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} "
        "restarts={{.RestartCount}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}} "
        "started={{.State.StartedAt}} finished={{.State.FinishedAt}} memory_limit_bytes={{.HostConfig.Memory}} "
        "restart_policy={{.HostConfig.RestartPolicy.Name}} image={{.Config.Image}}"
    )
    return run(["docker", "inspect", "--format", fmt, container])


@tool
def container_stats() -> str:
    """Current CPU and memory use of every running container."""
    return run(["docker", "stats", "--no-stream", "--format", "{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}} mem_pct={{.MemPerc}}"])


@tool
def host_resources() -> str:
    """Memory, swap, disk usage per filesystem and load average of the server."""
    parts = [run(["free", "-h"]), run(["df", "-h", "-x", "tmpfs", "-x", "overlay", "-x", "devtmpfs", "-x", "squashfs"]), run(["uptime"])]
    return "\n\n".join(parts)


@tool
def recent_events(minutes: int = 60) -> str:
    """What homelab-sentinel saw and decided recently, oldest first.

    Args:
        minutes: How far back to look, up to 1440.
    """
    minutes = max(1, min(int(minutes), 1440))
    path = os.getenv("DECISION_LOG", "logs/decisions.jsonl")
    if not os.path.exists(path):
        return "no decisions logged yet"
    since = time.time() - minutes * 60
    rows = []
    with open(path) as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("ts", 0) >= since:
                stamp = time.strftime("%H:%M", time.localtime(row["ts"]))
                rows.append(f"{stamp} {row.get('source')}/{row.get('subject')} {row.get('choice')} -> {row.get('route')}")
    return "\n".join(rows[-30:]) or f"nothing in the last {minutes} minutes"


UNIT_NAME = re.compile(r"^lifeboat-backup[A-Za-z0-9@_.-]*$")


@tool
def backup_logs(unit: str, lines: int = 60) -> str:
    """Recent journal lines of one homelab-lifeboat restic backup unit, for example lifeboat-backup@tier1.service. Secrets are redacted.

    Args:
        unit: The systemd unit name from the event, starting with lifeboat-backup.
        lines: How many lines from the end, 10 to 200.
    """
    if not UNIT_NAME.match(unit):
        return f"refused: {unit!r} is not a lifeboat-backup unit"
    lines = max(10, min(int(lines), 200))
    return run(["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"])


ALL_TOOLS = [container_logs, container_status, container_stats, host_resources, recent_events, backup_logs]
