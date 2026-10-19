import json
import time
import urllib.request

from sentinel.events import Event

ACTIVE = {"WARNING", "CRITICAL"}


class NetdataAlarms:
    def __init__(self, url="http://127.0.0.1:19999", interval=60):
        self.url = url.rstrip("/")
        self.interval = interval
        self.last = {}
        self.reachable = True

    def fetch(self):
        with urllib.request.urlopen(f"{self.url}/api/v1/alarms?all", timeout=10) as r:
            return json.load(r).get("alarms", {})

    def to_event(self, alarm_id, alarm, previous, now=None):
        now = now or time.time()
        since = alarm.get("last_status_change") or now
        minutes = max(0, int((now - since) / 60))
        status = alarm.get("status", "UNKNOWN")
        parts = [
            "source=netdata",
            f"alarm={alarm.get('name')}",
            f"chart={alarm.get('chart')}",
            f"status={status}",
            f"previous_status={previous}",
            f"value={alarm.get('value_string', '?')}",
            f"in_status_for={minutes}min",
        ]
        info = (alarm.get("summary") or alarm.get("info") or "").strip()
        if info:
            parts.append(f"meaning={info[:140]}")
        if status == "CLEAR" and previous in ACTIVE:
            parts.append("recovered=yes")
        return Event(
            source="netdata",
            subject=alarm.get("chart") or alarm_id,
            kind=f"{alarm.get('name')}:{status}",
            state=" ".join(parts),
            raw=alarm,
            ts=now,
        )

    def changes(self, alarms):
        events = []
        for alarm_id, alarm in alarms.items():
            status = alarm.get("status")
            previous = self.last.get(alarm_id, "UNKNOWN")
            self.last[alarm_id] = status
            if status == previous:
                continue
            if status in ACTIVE or (status == "CLEAR" and previous in ACTIVE):
                events.append(self.to_event(alarm_id, alarm, previous))
        return events

    def stream(self):
        while True:
            try:
                alarms = self.fetch()
                if not self.reachable:
                    self.reachable = True
                    yield Event("netdata", "netdata", "reachable", "source=sentinel netdata_api=reachable again recovered=yes")
                yield from self.changes(alarms)
            except Exception as e:
                if self.reachable:
                    self.reachable = False
                    yield Event("netdata", "netdata", "unreachable", f"source=sentinel netdata_api=unreachable error={type(e).__name__} monitoring_blind=yes")
            time.sleep(self.interval)
