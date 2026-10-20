import subprocess
import time
from datetime import datetime, timezone

from sentinel.events import Event

PROPS = ["LoadState", "ActiveState", "Result", "ExecMainStatus", "ExecMainExitTimestamp"]
KNOWN = {"lifeboat-backup@tier1.service": 6, "lifeboat-backup@tier2.service": 24}
DEFAULT_RPO_HOURS = 24
GRACE_HOURS = 1


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return ""


def parse_ts(text):
    text = (text or "").strip()
    if not text or text == "n/a":
        return None
    for fmt in ("%a %Y-%m-%d %H:%M:%S UTC", "%a %Y-%m-%d %H:%M:%S %Z"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def tier_of(unit):
    name = unit.removesuffix(".service")
    return name.split("@", 1)[1] if "@" in name else name.removeprefix("lifeboat-backup").strip("-") or name


def hours(seconds):
    return f"{seconds / 3600:.1f}h"


class LifeboatBackups:
    def __init__(self, interval=300, known=None, shell=run, clock=time.time):
        self.interval = interval
        self.known = dict(known or KNOWN)
        self.shell = shell
        self.clock = clock
        self.last = {}

    def units(self):
        listed = self.shell(["systemctl", "list-units", "--all", "--type=service", "--plain", "--no-legend", "lifeboat-backup*"])
        found = {line.split()[0] for line in listed.splitlines() if line.strip()}
        return sorted(set(self.known) | found)

    def show(self, unit):
        out = self.shell(["systemctl", "show", "--timestamp=utc", unit] + [f"--property={p}" for p in PROPS])
        info = {}
        for line in out.splitlines():
            key, _, value = line.partition("=")
            info[key] = value
        return info

    def timer_state(self, unit):
        tier = tier_of(unit)
        out = self.shell(["systemctl", "show", f"lifeboat-backup-{tier}.timer", "--property=LoadState", "--property=ActiveState"])
        info = dict(line.partition("=")[::2] for line in out.splitlines() if "=" in line)
        if info.get("LoadState") != "loaded":
            return "none"
        return info.get("ActiveState", "unknown")

    def status(self, unit, info, now):
        if info.get("LoadState") == "not-found" and not parse_ts(info.get("ExecMainExitTimestamp")):
            return "not_installed", None
        if info.get("ActiveState") in ("activating", "deactivating"):
            return "running", None
        finished = parse_ts(info.get("ExecMainExitTimestamp"))
        if info.get("Result") not in ("", "success"):
            return "failed", finished
        if finished is None:
            return "never_run", None
        rpo = self.known.get(unit, DEFAULT_RPO_HOURS)
        if now - finished > (rpo + GRACE_HOURS) * 3600:
            return "stale", finished
        return "ok", finished

    def to_event(self, unit, status, info, finished, previous, now):
        tier = tier_of(unit)
        rpo = self.known.get(unit, DEFAULT_RPO_HOURS)
        timer = self.timer_state(unit)
        parts = ["source=lifeboat", f"backup={tier}", f"unit={unit}", "tool=restic"]
        if status == "failed":
            parts += [
                "last_run=failed",
                f"result={info.get('Result')}",
                f"exit_code={info.get('ExecMainStatus', '?')}",
                f"finished={hours(now - finished)} ago" if finished else "finished=unknown",
                f"rpo_target={rpo}h",
                f"timer={timer}",
                "meaning=the last backup run failed; data written since the previous good backup is not protected",
            ]
        elif status == "stale":
            parts += [
                "last_run=success",
                f"last_success={hours(now - finished)} ago",
                f"rpo_target={rpo}h",
                "rpo_breached=yes",
                f"timer={timer}",
                "meaning=no successful backup within the recovery point objective",
            ]
        else:
            parts += [
                "last_run=success",
                f"finished={hours(now - finished)} ago" if finished else "finished=unknown",
                f"rpo_target={rpo}h",
                f"previous_status={previous}",
                "recovered=yes",
                "meaning=backups are working again",
            ]
        return Event("lifeboat", tier, status, " ".join(parts), raw={"unit": unit, **info}, ts=now)

    def changes(self):
        now = self.clock()
        events = []
        for unit in self.units():
            info = self.show(unit)
            status, finished = self.status(unit, info, now)
            if status in ("running", "not_installed", "never_run"):
                continue
            previous = self.last.get(unit, "unknown")
            self.last[unit] = status
            if status == previous:
                continue
            if status in ("failed", "stale"):
                events.append(self.to_event(unit, status, info, finished, previous, now))
            elif status == "ok" and previous in ("failed", "stale"):
                events.append(self.to_event(unit, status, info, finished, previous, now))
        return events

    def stream(self):
        while True:
            yield from self.changes()
            time.sleep(self.interval)
