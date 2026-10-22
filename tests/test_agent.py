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


class FailingModel(ScriptedModel):
    async def stream(self, *a, **k):
        self.calls += 1
        raise RuntimeError("Model use case details have not been submitted")
        yield


class BedrockFailureTest(unittest.TestCase):
    def test_outage_alert_survives_a_bedrock_failure(self):
        model = FailingModel()
        out = analyst("escalate", model).handle("source=docker container=x outcome=down")
        self.assertEqual(out.triage.route, "escalate")
        self.assertEqual(out.triage.choice, "ongoing_outage")
        self.assertIn("Bedrock analysis failed", out.analysis)
        self.assertIn("use case details", out.analysis)


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


class FromCauseTest(unittest.TestCase):
    def test_preamble_is_cut(self):
        from sentinel.analyst import from_cause

        self.assertEqual(from_cause("Host is healthy. Here is the summary:\n\nCause: x\nFix: y"), "Cause: x\nFix: y")
        self.assertEqual(from_cause("Cause: x"), "Cause: x")
        self.assertEqual(from_cause("no shape at all"), "no shape at all")


class RedactTest(unittest.TestCase):
    def test_secrets_are_masked(self):
        text = "API_KEY=abc123 password: hunter2 Authorization: Bearer eyJhbGciOi.x.y token=zzz ok=1"
        out = redact(text)
        for secret in ("abc123", "hunter2", "eyJhbGciOi", "zzz"):
            self.assertNotIn(secret, out)
        self.assertIn("ok=1", out)

    def test_names_with_underscores_urls_and_known_tokens(self):
        lines = {
            "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG": "wJalrXUtnFEMI",
            "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz": "sk-proj",
            "POSTGRES_PASSWORD: hunter2": "hunter2",
            "DB_PASS=s3cret": "s3cret",
            "connecting to postgres://app:pw123@db:5432/app": "pw123",
            '{"password": "two words"}': "two words",
            "Authorization: Basic dXNlcjpwYXNz": "dXNlcjpwYXNz",
            "key id AKIAIOSFODNN7EXAMPLE": "AKIAIOSFODNN7EXAMPLE",
            "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789": "ghp_",
        }
        for line, secret in lines.items():
            self.assertNotIn(secret, redact(line), line)

    def test_ordinary_log_lines_survive(self):
        for line in (
            "FATAL: could not connect to database at db:5432: connection refused",
            "status=exited exit_code=1 oom_killed=false restarts=0 image=alpine:3.20",
            "AWS_PROFILE=homelab-backup-writer RESTIC_REPOSITORY=s3:s3.amazonaws.com/bucket",
        ):
            self.assertEqual(redact(line), line)

    def test_tool_output_is_redacted_before_it_is_cut(self):
        from sentinel.tools import run

        out = run(["sh", "-c", "printf 'x%.0s' $(seq 1 50); echo; echo MY_SECRET_TOKEN=abcdef"], limit=20)
        self.assertNotIn("abcdef", out)


if __name__ == "__main__":
    unittest.main()
