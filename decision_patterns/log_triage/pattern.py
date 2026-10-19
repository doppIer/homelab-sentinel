import json
import time
import urllib.request
from dataclasses import dataclass, field

QUESTION = "How should a homelab operator treat this event?"

CRITERIA = {
    "noise": "routine log line, nothing to do",
    "info": "worth a line in the daily summary, no action",
    "action_needed": "operator should fix it within a day",
    "critical": "service down, data at risk, or failure within hours",
}

ROUTES = {
    "noise": "drop",
    "info": "digest",
    "action_needed": "notify",
    "critical": "escalate",
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
    def __init__(self, url="http://127.0.0.1:8765", escalate_below=0.5, risk_mass=0.35, timeout=60, question=QUESTION, criteria=CRITERIA):
        self.url = url.rstrip("/") + "/v1/systemone"
        self.escalate_below = escalate_below
        self.risk_mass = risk_mass
        self.timeout = timeout
        self.question = question
        self.criteria = criteria

    def payload(self, state):
        return {
            "state": state,
            "questions": {
                "severity": {"type": "choice", "instructions": self.question, "criteria": self.criteria},
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
        risk = probabilities.get("action_needed", 0) + probabilities.get("critical", 0)
        if choice == "critical":
            return "escalate", "decider chose critical"
        if confidence < self.escalate_below:
            return "escalate", f"low confidence {confidence:.2f} < {self.escalate_below}"
        if choice in ("noise", "info") and risk >= self.risk_mass:
            return "escalate", f"chose {choice} but risk mass {risk:.2f} >= {self.risk_mass}"
        return ROUTES[choice], f"decider chose {choice} at {confidence:.2f}"

    def __call__(self, state):
        data = self.ask(state)
        answer = data["answers"]["severity"]
        choice = answer["choice"]
        confidence = float(answer.get("confidence", 0))
        probabilities = answer.get("probabilities", {})
        route, reason = self.route(choice, confidence, probabilities)
        return Triage(choice, confidence, probabilities, route, reason, float(data["latency_ms"]))
