import time
from dataclasses import dataclass

from strands import Agent

from decision_patterns.log_triage import Triage
from decision_patterns.log_triage.strands_gate import LogTriageGate
from sentinel.guards import BedrockBudget, KnownContainers
from sentinel.tools import ALL_TOOLS, container_names

SYSTEM = """You are the on-call engineer for a single Ubuntu homelab server (Dell T1700, 16 GB RAM, Docker).
The user message is an event that homelab-sentinel flagged as an outage or a likely outage.
Investigate with the read-only tools before you conclude.
For source=docker events, check the container's status and logs first.
For source=lifeboat events (restic backups run by systemd), read backup_logs for the unit in the event first, then host_resources.
You cannot change anything on the server. Never claim you fixed, restarted or changed something.

Reply in plain text for a Telegram message: no markdown, no code fences, no backticks.
At most 900 characters, in exactly this shape:
Cause: one sentence
Evidence: two or three short lines quoting what you saw
Fix: the exact commands the operator should run, one per line
Confidence: low, medium or high"""


@dataclass
class Outcome:
    triage: Triage
    analysis: str | None
    input_tokens: int
    output_tokens: int
    seconds: float


class Analyst:
    def __init__(self, triage, model, max_calls_per_hour=20, tools=None, list_names=container_names):
        self.gate = LogTriageGate(triage)
        self.budget = BedrockBudget(max_calls_per_hour)
        self.known = KnownContainers(list_names)
        self.model = model
        self.tools = tools if tools is not None else ALL_TOOLS

    def agent(self):
        return Agent(
            model=self.model,
            tools=self.tools,
            system_prompt=SYSTEM,
            interventions=[self.gate, self.budget, self.known],
            callback_handler=None,
        )

    def handle(self, state):
        started = time.perf_counter()
        try:
            result = self.agent()(state)
        except Exception as e:
            triage = self.gate.last or Triage("unknown", 0.0, {}, "escalate", "decider unavailable", 0.0)
            note = f"Bedrock analysis failed ({type(e).__name__}): {str(e)[:300]}"
            return Outcome(triage, note if triage.route == "escalate" else None, 0, 0, time.perf_counter() - started)
        seconds = time.perf_counter() - started
        triage = self.gate.last or Triage("unknown", 0.0, {}, "escalate", "decider unavailable, sent to Bedrock", 0.0)
        usage = result.metrics.accumulated_usage
        analysis = str(result).strip() if triage.route == "escalate" else None
        return Outcome(triage, analysis, usage.get("inputTokens", 0), usage.get("outputTokens", 0), seconds)


def bedrock_model(model_id, region):
    from strands.models import BedrockModel

    return BedrockModel(model_id=model_id, region_name=region, max_tokens=800, temperature=0.2)
