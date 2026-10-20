import json
import subprocess
import time
from collections import defaultdict, deque

from sentinel.events import Event

WATCHED = ["die", "oom", "health_status", "restart", "start", "stop", "kill"]

EXIT_CODES = {
    "0": "clean exit",
    "1": "application error",
    "2": "misuse or bad arguments",
    "125": "docker run failed",
    "126": "command not executable",
    "127": "command not found",
    "137": "SIGKILL, often out of memory or docker kill",
    "139": "segmentation fault",
    "143": "SIGTERM, normal stop",
}


GRACEFUL_EXIT = {"0", "143"}
UP_KINDS = {"start", "restart", "health_status:healthy"}
DOWN_KINDS = {"die", "stop", "kill", "oom", "health_status:unhealthy"}


SOFT_SIGNALS = {"15", "SIGTERM", "2", "SIGINT"}

PLAIN_WORDS = {
    "kill": "stop_requested",
    "die": "exited",
    "stop": "stopped",
    "start": "started",
    "restart": "restart_command",
}


def step_label(event, graceful):
    attrs = event.raw.get("Actor", {}).get("Attributes", {})
    if graceful:
        if event.kind == "kill":
            return f"stop_requested({attrs.get('signal', '?')})"
        if event.kind == "die":
            return "exited_cleanly"
        return PLAIN_WORDS.get(event.kind, event.kind)
    if event.kind == "die" and "exitCode" in attrs:
        return f"die(exit {attrs['exitCode']})"
    if event.kind == "kill" and "signal" in attrs:
        return f"kill(signal {attrs['signal']})"
    return event.kind


class DockerEvents:
    def __init__(self, window_seconds=3600):
        self.window = window_seconds
        self.history = defaultdict(deque)

    def command(self):
        cmd = ["docker", "events", "--format", "{{json .}}", "--filter", "type=container"]
        for name in WATCHED:
            cmd += ["--filter", f"event={name}"]
        return cmd

    def count_recent(self, container, kind, now):
        seen = self.history[(container, kind)]
        seen.append(now)
        while seen and now - seen[0] > self.window:
            seen.popleft()
        return len(seen)

    def to_event(self, raw):
        action = raw.get("Action", "")
        attrs = raw.get("Actor", {}).get("Attributes", {})
        container = attrs.get("name", "unknown")
        now = raw.get("time", time.time())
        kind, _, detail = action.partition(":")
        kind = kind.strip()
        detail = detail.strip()

        parts = [
            "source=docker",
            f"container={container}",
            f"image={attrs.get('image', '?')}",
            f"event={kind}",
        ]
        if detail:
            parts.append(f"detail={detail}")
        if "exitCode" in attrs:
            code = attrs["exitCode"]
            parts.append(f"exit_code={code} ({EXIT_CODES.get(code, 'unknown code')})")
        if "signal" in attrs:
            parts.append(f"signal={attrs['signal']}")
        parts.append(f"same_event_last_hour={self.count_recent(container, kind, now)}")
        project = attrs.get("com.docker.compose.project")
        if project:
            parts.append(f"compose_project={project}")

        return Event(
            source="docker",
            subject=container,
            kind=f"{kind}:{detail}" if detail else kind,
            state=" ".join(parts),
            raw=raw,
            ts=now,
        )

    def summarize(self, events):
        first, last = events[0], events[-1]
        attrs = first.raw.get("Actor", {}).get("Attributes", {})
        kinds = [e.kind for e in events]
        exit_codes = [e.raw.get("Actor", {}).get("Attributes", {}).get("exitCode") for e in events if e.kind == "die"]
        exit_codes = [c for c in exit_codes if c is not None]
        crashed = [c for c in exit_codes if c not in GRACEFUL_EXIT]
        signals = [e.raw.get("Actor", {}).get("Attributes", {}).get("signal") for e in events if e.kind == "kill"]
        hard_kill = [x for x in signals if x is not None and x not in SOFT_SIGNALS]
        graceful = not crashed and not hard_kill and "oom" not in kinds and "health_status:unhealthy" not in kinds

        if last.kind in UP_KINDS:
            outcome = "running_again" if any(k in DOWN_KINDS for k in kinds) else "running"
        elif last.kind in DOWN_KINDS:
            outcome = "down"
        else:
            outcome = "unknown"

        restarts = len([t for t in self.history[(first.subject, "start")] if last.ts - t <= self.window])

        if graceful:
            if set(kinds) == {"health_status:healthy"}:
                action, meaning = "healthcheck", "healthcheck passed"
            elif "restart" in kinds or (outcome == "running_again"):
                action, meaning = "restart", "restarted on request"
            elif outcome == "down":
                action, meaning = "stop", "stopped on request"
            else:
                action, meaning = "start", "started"
            parts = [
                "source=docker",
                f"container={first.subject}",
                f"image={attrs.get('image', '?')}",
                f"action={action}",
                "stop=clean" if action in ("restart", "stop") else "",
                f"outcome={outcome}",
            ]
            parts = [p for p in parts if p]
        else:
            meaning = None
            parts = [
                "source=docker",
                f"container={first.subject}",
                f"image={attrs.get('image', '?')}",
                f"steps={'>'.join(step_label(e, False) for e in events)}",
                f"outcome={outcome}",
            ]
            if exit_codes:
                parts.append(f"exit_codes={','.join(exit_codes)}")
                parts.append("graceful_stop=no")
            if crashed:
                parts.append(f"crash_reason={'; '.join(EXIT_CODES.get(c, 'unknown code ' + c) for c in crashed)}")
            if "oom" in kinds:
                parts.append("out_of_memory=yes")
            if "health_status:unhealthy" in kinds:
                parts.append("healthcheck_failed=yes")
        parts.append(f"starts_last_hour={restarts}")
        project = attrs.get("com.docker.compose.project")
        if project:
            parts.append(f"compose_project={project}")
        if meaning:
            parts.append(f"meaning={meaning}")

        return Event(
            source="docker",
            subject=first.subject,
            kind=last.kind if len(events) == 1 else f"{kinds[0]}..{last.kind}",
            state=" ".join(parts),
            raw={"events": [e.raw for e in events]},
            ts=last.ts,
        )

    def stream(self):
        while True:
            proc = subprocess.Popen(self.command(), stdout=subprocess.PIPE, text=True)
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield self.to_event(raw)
            proc.wait()
            time.sleep(5)
