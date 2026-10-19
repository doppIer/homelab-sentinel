import unittest

from decision_patterns.log_triage import Triage
from sentinel.events import Event
from sentinel.notify import Telegram, format_message


def result(route, choice="action_needed"):
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

    def test_escalate_is_never_suppressed(self):
        for t in (0, 1, 2):
            self.assertTrue(self.bot.send_decision(self.event, result("escalate", "critical"), now=t))
        self.assertEqual(len(self.outbox), 3)

    def test_message_format(self):
        text = format_message(self.event, result("escalate", "critical"))
        self.assertTrue(text.startswith("🔴 ESCALATE · homeserver · docker/jellyfin"))
        self.assertIn("Decider: critical (0.60) in 3.8s", text)
        self.assertIn("exit_code=137", text)


if __name__ == "__main__":
    unittest.main()
