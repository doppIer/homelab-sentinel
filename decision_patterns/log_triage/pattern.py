import json
import time
import urllib.request
from dataclasses import dataclass, field

QUESTION = "What happened on this server?"

CRITERIA = {
    "routine": "normal background activity, nothing changed",
    "planned_change": "an operator or an update intentionally restarted, stopped or changed something",
    "recovered_failure": "something crashed or failed but is running again",
    "ongoing_outage": "a service is down or unhealthy right now",
    "resource_risk": "disk, memory or CPU is running out or trending toward failure",
}

ROUTES = {
    "routine": "drop",
    "planned_change": "digest",
    "recovered_failure": "notify",
    "resource_risk": "notify",
    "ongoing_outage": "escalate",
}


@dataclass
class Triage:
    choice: str
    confidence: float
    probabilities: dict
    route: str
    reason: str
    latency_ms: float
    extra: dict = field(default_factory=dict)


class LogTriage:
    def __init__(self, url="http://127.0.0.1:8765", escalate_below=0.5, outage_risk=0.25, timeout=60, question=QUESTION, criteria=CRITERIA, routes=ROUTES):
        self.url = url.rstrip("/") + "/v1/systemone"
        self.escalate_below = escalate_below
        self.outage_risk = outage_risk
        self.timeout = timeout
        self.question = question
        self.criteria = criteria
        self.routes = routes

    def payload(self, state):
        return {
            "state": state,
            "questions": {
                "what": {"type": "choice", "instructions": self.question, "criteria": self.criteria},
            },
        }

    def ask(self, state):
        body = json.dumps(self.payload(state)).encode()
        req = urllib.request.Request(self.url, data=body, headers={"content-type": "application/json"})
        started = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.load(r)
        data.setdefault("latency_ms", (time.perf_counter() - started) * 1000)
        return data

    def route(self, choice, confidence, probabilities):
        outage = probabilities.get("ongoing_outage", 0)
        if self.routes[choice] == "escalate":
            return "escalate", f"decider saw {choice} at {confidence:.2f}"
        if confidence < self.escalate_below:
            return "escalate", f"unsure: {choice} at {confidence:.2f} < {self.escalate_below}"
        if outage >= self.outage_risk:
            return "escalate", f"chose {choice} but P(ongoing_outage)={outage:.2f} >= {self.outage_risk}"
        return self.routes[choice], f"decider saw {choice} at {confidence:.2f}"

    def __call__(self, state):
        data = self.ask(state)
        answer = data["answers"]["what"]
        choice = answer["choice"]
        confidence = float(answer.get("confidence", 0))
        probabilities = answer.get("probabilities", {})
        route, reason = self.route(choice, confidence, probabilities)
        return Triage(choice, confidence, probabilities, route, reason, float(data["latency_ms"]))
