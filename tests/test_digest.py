import json
import os
import tempfile
import unittest

from sentinel.digest import build, event_lines, load_rows, stats


class FakeBedrock:
    def __init__(self):
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "output": {"message": {"content": [{"text": "Bumpy day: one crash, two restarts."}]}},
            "usage": {"inputTokens": 900, "outputTokens": 60},
        }


def row(ts, route, choice="planned_change", subject="mympd", **extra):
    r = {"ts": ts, "source": "docker", "subject": subject, "route": route, "choice": choice, "confidence": 0.6, "state": f"source=docker container={subject}"}
    r.update(extra)
    return r


class DigestTest(unittest.TestCase):
    def test_load_rows_keeps_window_and_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "decisions.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps(row(100, "digest")) + "\n")
                f.write("not json\n")
                f.write(json.dumps(row(5000, "notify")) + "\n")
            rows = load_rows(path, since=1000)
        self.assertEqual([r["route"] for r in rows], ["notify"])

    def test_stats_counts_routes_and_bedrock_usage(self):
        rows = [row(1, "drop"), row(2, "digest"), row(3, "escalate", "ongoing_outage", bedrock_input_tokens=2600, bedrock_output_tokens=300)]
        s = stats(rows)
        self.assertEqual(s["routes"], {"escalate": 1, "notify": 0, "digest": 1, "drop": 1})
        self.assertEqual(s["bedrock_calls"], 1)
        self.assertEqual(s["tokens_in"], 2600)

    def test_dropped_events_are_left_out_and_outages_come_first(self):
        rows = [row(1, "drop", "routine"), row(2, "digest"), row(3, "escalate", "ongoing_outage", subject="api", analysis="Cause: db down\nFix: start db")]
        lines = event_lines(rows)
        self.assertEqual(len(lines), 2)
        self.assertIn("[escalate]", lines[0])
        self.assertIn("analysis Cause: db down", lines[0])

    def test_quiet_day_makes_no_bedrock_call(self):
        fake = FakeBedrock()
        text, tokens_in, _ = build([row(1, "drop", "routine")], 24, "eu.anthropic.claude-haiku-4-5-20251001-v1:0", "eu-north-1", fake)
        self.assertEqual(fake.calls, [])
        self.assertEqual(tokens_in, 0)
        self.assertIn("quiet day", text)

    def test_busy_day_calls_the_summary_model_once(self):
        fake = FakeBedrock()
        rows = [row(1, "digest"), row(2, "escalate", "ongoing_outage", subject="api"), row(3, "drop", "routine"), row(4, "notify", "recovered_failure")]
        text, tokens_in, tokens_out = build(rows, 24, "eu.anthropic.claude-haiku-4-5-20251001-v1:0", "eu-north-1", fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["modelId"], "eu.anthropic.claude-haiku-4-5-20251001-v1:0")
        self.assertIn("escalate=1", fake.calls[0]["messages"][0]["content"][0]["text"])
        self.assertTrue(text.startswith("🌙 Daily digest\n\nBumpy day"))
        self.assertIn("4 events, 1 sent to Bedrock for analysis, 3 handled locally by Decider", text)
        self.assertIn("claude-haiku-4-5-20251001", text)
        self.assertEqual((tokens_in, tokens_out), (900, 60))


if __name__ == "__main__":
    unittest.main()
