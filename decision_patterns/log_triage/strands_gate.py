from strands.interventions import Deny, InterventionHandler, Proceed


def last_user_text(messages):
    for message in reversed(messages or []):
        if message.get("role") != "user":
            continue
        for block in message.get("content", []):
            if "text" in block:
                return block["text"]
    return ""


class LogTriageGate(InterventionHandler):
    name = "log-triage-gate"

    def __init__(self, triage, allow=("escalate",)):
        self.triage = triage
        self.allow = set(allow)
        self.last = None

    @property
    def on_error(self):
        return "proceed"

    def before_invocation(self, event, **kwargs):
        self.last = None
        result = self.triage(last_user_text(event.messages))
        self.last = result
        if result.route in self.allow:
            return Proceed(reason=result.reason)
        return Deny(reason=f"{result.route}: {result.reason}")
