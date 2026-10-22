import unittest

from decision_patterns.log_triage import Triage
from sentinel.events import Event
from sentinel.notify import Telegram, format_message


def result(route, choice="recovered_failure"):
    return Triage(choice, 0.6, {}, route, "because", 3800.0)


class TelegramTest(unittest.TestCase):
    def setUp(self):
        self.outbox = []
        self.bot = Telegram("t", "1", cooldown=600, post=self.outbox.append)
        self.event = Event("docker", "jellyfin", "die", "source=docker container=jellyfin event=die exit_code=137")

    def test_drop_and_digest_are_silent(self):
        self.assertFalse(self.bot.send_decision(self.event, result("drop", "noise"), now=0))
        self.assertFalse(self.bot.send_decision(self.event, result("digest", "info"), now=0))
        self.assertEqual(self.outbox, [])

    def test_notify_has_cooldown_per_subject(self):
        self.assertTrue(self.bot.send_decision(self.event, result("notify"), now=0))
        self.assertFalse(self.bot.send_decision(self.event, result("notify"), now=100))
        self.assertTrue(self.bot.send_decision(self.event, result("notify"), now=700))
        self.assertEqual(len(self.outbox), 2)

    def test_crash_loop_escalates_once_per_window_and_counts_repeats(self):
        outage = result("escalate", "ongoing_outage")
        self.assertTrue(self.bot.send_decision(self.event, outage, now=0))
        for t in (30, 60, 90):
            self.assertFalse(self.bot.send_decision(self.event, outage, now=t))
        self.assertTrue(self.bot.send_decision(self.event, outage, now=901))
        self.assertEqual(len(self.outbox), 2)
        self.assertIn("repeated 3 more times", self.outbox[1])
        self.assertNotIn("repeated", self.outbox[0])

    def test_escalate_for_another_container_is_not_blocked(self):
        outage = result("escalate", "ongoing_outage")
        other = Event("docker", "navidrome", "die", "source=docker container=navidrome")
        self.assertTrue(self.bot.send_decision(self.event, outage, now=0))
        self.assertTrue(self.bot.send_decision(other, outage, now=1))

    def test_message_is_redacted(self):
        event = Event("docker", "db", "die", "source=docker container=db POSTGRES_PASSWORD=hunter2")
        text = format_message(event, result("escalate", "ongoing_outage"), analysis="Cause: DB_PASS=s3cret wrong")
        self.assertNotIn("hunter2", text)
        self.assertNotIn("s3cret", text)

    def test_message_format(self):
        text = format_message(self.event, result("escalate", "ongoing_outage"))
        self.assertTrue(text.startswith("🔴 ESCALATE · homeserver · docker/jellyfin"))
        self.assertIn("Decider: ongoing_outage (0.60) in 3.8s", text)
        self.assertIn("exit_code=137", text)
        self.assertNotIn("Bedrock", text)

    def test_message_carries_bedrock_analysis(self):
        text = format_message(self.event, result("escalate", "ongoing_outage"), analysis="Cause: OOM\nFix: raise mem_limit")
        self.assertTrue(text.endswith("🧠 Bedrock:\nCause: OOM\nFix: raise mem_limit"))


if __name__ == "__main__":
    unittest.main()
