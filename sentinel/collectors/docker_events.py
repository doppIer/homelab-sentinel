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
