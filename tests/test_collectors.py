import unittest

from decision_patterns.log_triage import LogTriage
from sentinel.coalesce import Coalescer
from sentinel.collectors.docker_events import DockerEvents
from sentinel.collectors.netdata import NetdataAlarms


def docker_raw(action, name="jellyfin", t=1792443214, **extra):
    attrs = {"name": name, "image": "jellyfin/jellyfin:latest", "com.docker.compose.project": "jellyfin"}
    attrs.update(extra)
    return {"Type": "container", "Action": action, "Actor": {"ID": "abc", "Attributes": attrs}, "time": t}


class DockerEventsTest(unittest.TestCase):
    def test_die_with_oom_exit_code(self):
        event = DockerEvents().to_event(docker_raw("die", exitCode="137"))
        self.assertEqual(event.subject, "jellyfin")
        self.assertEqual(event.kind, "die")
        self.assertIn("exit_code=137 (SIGKILL, often out of memory or docker kill)", event.state)
        self.assertIn("compose_project=jellyfin", event.state)

    def test_health_status_detail(self):
        event = DockerEvents().to_event(docker_raw("health_status: unhealthy"))
        self.assertEqual(event.kind, "health_status:unhealthy")
        self.assertIn("detail=unhealthy", event.state)

    def test_repeat_counter_within_window(self):
        collector = DockerEvents(window_seconds=3600)
        collector.to_event(docker_raw("die", t=1000, exitCode="1"))
        collector.to_event(docker_raw("die", t=1500, exitCode="1"))
        third = collector.to_event(docker_raw("die", t=2000, exitCode="1"))
        self.assertIn("same_event_last_hour=3", third.state)
        late = collector.to_event(docker_raw("die", t=9000, exitCode="1"))
        self.assertIn("same_event_last_hour=1", late.state)

    def test_exec_events_are_not_watched(self):
        command = " ".join(DockerEvents().command())
        self.assertNotIn("exec_", command)
        self.assertIn("event=die", command)


class CoalescerTest(unittest.TestCase):
    def test_restart_burst_becomes_one_event(self):
        collector = DockerEvents()
        coalescer = Coalescer(window=15)
        for i, action in enumerate(["kill", "die", "stop", "start", "restart"]):
            extra = {"exitCode": "143"} if action == "die" else {}
            coalescer.add(collector.to_event(docker_raw(action, name="mympd", **extra)), now=100 + i)
        self.assertEqual(coalescer.ready(now=110), [])
        out = coalescer.ready(now=120)
        self.assertEqual(len(out), 1)
        self.assertIn("sequence=kill>die>stop>start>restart", out[0].state)
        self.assertIn("events_in_burst=5", out[0].state)


class NetdataTest(unittest.TestCase):
    def alarm(self, status, value="97%"):
        return {
            "name": "disk_space_usage",
            "chart": "disk_space._",
            "status": status,
            "value_string": value,
            "last_status_change": 1000,
            "info": "Disk / space utilization",
        }

    def test_only_transitions_into_and_out_of_active(self):
        collector = NetdataAlarms()
        first = collector.changes({"a": self.alarm("CLEAR")})
        self.assertEqual(first, [])
        warn = collector.changes({"a": self.alarm("WARNING")})
        self.assertEqual(len(warn), 1)
        self.assertIn("status=WARNING previous_status=CLEAR", warn[0].state)
        same = collector.changes({"a": self.alarm("WARNING")})
        self.assertEqual(same, [])
        back = collector.changes({"a": self.alarm("CLEAR", "70%")})
        self.assertIn("recovered=yes", back[0].state)

    def test_undefined_states_are_ignored(self):
        collector = NetdataAlarms()
        self.assertEqual(collector.changes({"a": self.alarm("UNDEFINED")}), [])
        self.assertEqual(collector.changes({"a": self.alarm("REMOVED")}), [])


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.triage = LogTriage(escalate_below=0.5, risk_mass=0.35)

    def test_raw_disk_case_from_day_one_escalates(self):
        probs = {"noise": 0.1835, "info": 0.4614, "action_needed": 0.3096, "critical": 0.0455}
        route, reason = self.triage.route("info", 0.2819, probs)
        self.assertEqual(route, "escalate")
        self.assertIn("low confidence", reason)

    def test_confident_info_with_hidden_risk_escalates(self):
        probs = {"noise": 0.05, "info": 0.55, "action_needed": 0.30, "critical": 0.10}
        route, reason = self.triage.route("info", 0.6, probs)
        self.assertEqual(route, "escalate")
        self.assertIn("risk mass", reason)

    def test_confident_noise_is_dropped(self):
        probs = {"noise": 0.9, "info": 0.08, "action_needed": 0.01, "critical": 0.01}
        self.assertEqual(self.triage.route("noise", 0.86, probs)[0], "drop")

    def test_confident_action_needed_notifies(self):
        probs = {"noise": 0.01, "info": 0.1, "action_needed": 0.8, "critical": 0.09}
        self.assertEqual(self.triage.route("action_needed", 0.73, probs)[0], "notify")

    def test_critical_always_escalates(self):
        probs = {"noise": 0.0, "info": 0.0, "action_needed": 0.1, "critical": 0.9}
        self.assertEqual(self.triage.route("critical", 0.95, probs)[0], "escalate")

    def test_payload_uses_criteria_dict(self):
        payload = self.triage.payload("x")
        self.assertIsInstance(payload["questions"]["severity"]["criteria"], dict)


if __name__ == "__main__":
    unittest.main()


class DockerSummaryTest(unittest.TestCase):
    def burst(self, collector, steps, name="mympd", t0=1000):
        events = []
        for i, (action, extra) in enumerate(steps):
            events.append(collector.to_event(docker_raw(action, name=name, t=t0 + i, **extra)))
        return collector.summarize(events)

    def test_planned_restart_is_graceful_and_running(self):
        steps = [("kill", {"signal": "15"}), ("die", {"exitCode": "143"}), ("stop", {}), ("start", {}), ("restart", {})]
        event = self.burst(DockerEvents(), steps)
        self.assertIn("steps=kill(signal 15)>die(exit 143)>stop>start>restart", event.state)
        self.assertIn("outcome=running_again", event.state)
        self.assertIn("graceful_stop=yes", event.state)
        self.assertIn("starts_last_hour=1", event.state)
        self.assertNotIn("crash_reason", event.state)
        self.assertEqual(event.kind, "kill..restart")

    def test_oom_crash_loop(self):
        collector = DockerEvents()
        for k in range(3):
            self.burst(collector, [("die", {"exitCode": "137"}), ("start", {})], name="jellyfin", t0=1000 + k * 60)
        event = self.burst(collector, [("oom", {}), ("die", {"exitCode": "137"}), ("start", {})], name="jellyfin", t0=1300)
        self.assertIn("graceful_stop=no", event.state)
        self.assertIn("crash_reason=SIGKILL, often out of memory or docker kill", event.state)
        self.assertIn("out_of_memory=yes", event.state)
        self.assertIn("starts_last_hour=4", event.state)

    def test_container_left_down(self):
        event = self.burst(DockerEvents(), [("die", {"exitCode": "1"})], name="opengym-api-1")
        self.assertIn("outcome=down", event.state)
        self.assertIn("crash_reason=application error", event.state)

    def test_coalescer_uses_summarizer(self):
        collector = DockerEvents()
        coalescer = Coalescer(window=15, summarizers={"docker": collector.summarize})
        coalescer.add(collector.to_event(docker_raw("die", name="mympd", exitCode="143")), now=100)
        coalescer.add(collector.to_event(docker_raw("start", name="mympd")), now=101)
        out = coalescer.ready(now=120)
        self.assertEqual(len(out), 1)
        self.assertIn("outcome=running_again", out[0].state)
