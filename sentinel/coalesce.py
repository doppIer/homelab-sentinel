import time


class Coalescer:
    def __init__(self, window=15, summarizers=None):
        self.window = window
        self.pending = {}
        self.summarizers = summarizers or {}

    def add(self, event, now=None):
        now = time.time() if now is None else now
        group = (event.source, event.subject)
        if group in self.pending:
            self.pending[group]["events"].append(event)
            self.pending[group]["last"] = now
        else:
            self.pending[group] = {"events": [event], "last": now}

    def ready(self, now=None):
        now = time.time() if now is None else now
        done = [g for g, p in self.pending.items() if now - p["last"] >= self.window]
        out = []
        for group in done:
            events = self.pending.pop(group)["events"]
            latest = events[-1]
            summarize = self.summarizers.get(group[0])
            if summarize:
                out.append(summarize(events))
                continue
            if len(events) > 1:
                sequence = ">".join(e.kind for e in events)
                latest.state = f"{latest.state} sequence={sequence} events_in_burst={len(events)}"
            out.append(latest)
        return out
