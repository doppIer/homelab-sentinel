import json
import unittest

from strands import tool
from strands.models.model import Model

from decision_patterns.log_triage import Triage
from sentinel.analyst import Analyst
from sentinel.guards import BedrockBudget
from sentinel.tools import redact

TOOL_CALLS = []


@tool
def container_status(container: str) -> str:
    """Fake status.

    Args:
        container: name
    """
    TOOL_CALLS.append(container)
    return f"status=exited exit_code=1 container={container}"


class ScriptedModel(Model):
    def __init__(self, tool_container=None):
        self.config = {}
        self.calls = 0
        self.tool_container = tool_container

    def update_config(self, **kw):
        self.config.update(kw)

    def get_config(self):
        return self.config

    async def structured_output(self, *a, **k):
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if self.tool_container and self.calls == 1:
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "t1", "name": "container_status"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({"container": self.tool_container})}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            return
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": "Cause: app crashed\nFix: docker compose up -d\nConfidence: medium"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}


class FakeTriage:
    def __init__(self, route):
        self.route = route
        self.seen = []

    def __call__(self, state):
        self.seen.append(state)
        return Triage("ongoing_outage" if self.route == "escalate" else "planned_change", 0.8, {}, self.route, "test", 3700.0)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def analyst(route, model, names=("opengym-api-1",), max_calls=20):
    return Analyst(FakeTriage(route), model, max_calls_per_hour=max_calls, tools=[container_status], list_names=lambda: set(names))


class AnalystTest(unittest.TestCase):
    def setUp(self):
        TOOL_CALLS.clear()

    def test_gate_keeps_bedrock_out_of_non_outages(self):
        model = ScriptedModel()
        a = analyst("digest", model)
        out = a.handle("source=docker container=mympd action=restart")
        self.assertEqual(model.calls, 0)
        self.assertIsNone(out.analysis)
        self.assertEqual(out.triage.route, "digest")
        self.assertEqual(a.gate.triage.seen, ["source=docker container=mympd action=restart"])

    def test_outage_goes_to_model_and_tools(self):
        model = ScriptedModel(tool_container="opengym-api-1")
        out = analyst("escalate", model).handle("source=docker container=opengym-api-1 outcome=down")
        self.assertEqual(model.calls, 2)
        self.assertEqual(TOOL_CALLS, ["opengym-api-1"])
        self.assertIn("Cause: app crashed", out.analysis)

    def test_unknown_container_is_denied_before_the_tool_runs(self):
        model = ScriptedModel(tool_container="made-up-container")
        out = analyst("escalate", model).handle("source=docker container=opengym-api-1 outcome=down")
        self.assertEqual(TOOL_CALLS, [])
        self.assertEqual(model.calls, 2)
        self.assertIsNotNone(out.analysis)

    def test_decider_failure_falls_back_to_bedrock(self):
        class Broken:
            def __call__(self, state):
                raise ConnectionError("decider down")

        model = ScriptedModel()
        a = Analyst(Broken(), model, tools=[container_status], list_names=lambda: set())
        out = a.handle("anything")
        self.assertEqual(model.calls, 1)
        self.assertEqual(out.triage.route, "escalate")
        self.assertIn("decider unavailable", out.triage.reason)


class BudgetTest(unittest.TestCase):
    def test_budget_blocks_after_limit_and_recovers(self):
        clock = FakeClock()
        budget = BedrockBudget(max_per_hour=2, clock=clock)
        self.assertEqual(budget.before_model_call(None).type, "proceed")
        self.assertEqual(budget.before_model_call(None).type, "proceed")
        self.assertEqual(budget.before_model_call(None).type, "deny")
        clock.t = 3601
        self.assertEqual(budget.before_model_call(None).type, "proceed")

    def test_budget_stops_a_live_agent(self):
        model = ScriptedModel()
        a = analyst("escalate", model, max_calls=1)
        a.handle("first outage")
        out = a.handle("second outage")
        self.assertEqual(model.calls, 1)
        self.assertIn("budget", out.analysis)


class RedactTest(unittest.TestCase):
    def test_secrets_are_masked(self):
        text = "API_KEY=abc123 password: hunter2 Authorization: Bearer eyJhbGciOi.x.y token=zzz ok=1"
        out = redact(text)
        for secret in ("abc123", "hunter2", "eyJhbGciOi", "zzz"):
            self.assertNotIn(secret, out)
        self.assertIn("ok=1", out)


if __name__ == "__main__":
    unittest.main()
