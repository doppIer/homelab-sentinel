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
        self.triage = LogTriage(escalate_below=0.5, outage_risk=0.25)

    def probs(self, **kw):
        base = {"routine": 0.0, "planned_change": 0.0, "recovered_failure": 0.0, "ongoing_outage": 0.0, "resource_risk": 0.0}
        base.update(kw)
        return base

    def test_disk_from_day_two_notifies(self):
        p = self.probs(resource_risk=0.97, routine=0.02, ongoing_outage=0.01)
        self.assertEqual(self.triage.route("resource_risk", 0.96, p)[0], "notify")

    def test_outage_always_escalates(self):
        p = self.probs(ongoing_outage=0.81, recovered_failure=0.08)
        route, reason = self.triage.route("ongoing_outage", 0.77, p)
        self.assertEqual(route, "escalate")
        self.assertIn("ongoing_outage", reason)

    def test_unsure_escalates(self):
        p = self.probs(recovered_failure=0.46, planned_change=0.38, routine=0.11, ongoing_outage=0.04)
        route, reason = self.triage.route("recovered_failure", 0.33, p)
        self.assertEqual(route, "escalate")
        self.assertIn("unsure", reason)

    def test_hidden_outage_risk_escalates(self):
        p = self.probs(planned_change=0.66, ongoing_outage=0.30)
        route, reason = self.triage.route("planned_change", 0.6, p)
        self.assertEqual(route, "escalate")
        self.assertIn("P(ongoing_outage)", reason)

    def test_confident_planned_change_goes_to_digest(self):
        p = self.probs(planned_change=0.80, recovered_failure=0.12, ongoing_outage=0.03)
        self.assertEqual(self.triage.route("planned_change", 0.75, p)[0], "digest")

    def test_routine_is_dropped(self):
        p = self.probs(routine=0.9, planned_change=0.08)
        self.assertEqual(self.triage.route("routine", 0.88, p)[0], "drop")

    def test_payload_uses_criteria_dict(self):
        payload = self.triage.payload("x")
        self.assertIsInstance(payload["questions"]["what"]["criteria"], dict)
        self.assertEqual(len(payload["questions"]), 1)


class DockerSummaryTest(unittest.TestCase):
    def burst(self, collector, steps, name="mympd", t0=1000):
        events = []
        for i, (action, extra) in enumerate(steps):
            events.append(collector.to_event(docker_raw(action, name=name, t=t0 + i, **extra)))
        return collector.summarize(events)

    def test_planned_restart_is_graceful_and_running(self):
        steps = [("kill", {"signal": "15"}), ("die", {"exitCode": "143"}), ("stop", {}), ("start", {}), ("restart", {})]
        event = self.burst(DockerEvents(), steps)
        self.assertIn("action=restart stop=clean outcome=running_again starts_last_hour=1", event.state)
        self.assertTrue(event.state.endswith("meaning=restarted on request"))
        for word in ("die", "kill", "steps=", "exit_codes", "crash_reason"):
            self.assertNotIn(word, event.state)
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
        self.assertIn("steps=oom>die(exit 137)>start", event.state)
        self.assertNotIn("meaning=", event.state)

    def test_sigkill_is_not_graceful(self):
        steps = [("kill", {"signal": "9"}), ("die", {"exitCode": "137"}), ("start", {})]
        event = self.burst(DockerEvents(), steps)
        self.assertIn("steps=kill(signal 9)>die(exit 137)>start", event.state)
        self.assertNotIn("meaning=", event.state)

    def test_clean_stop(self):
        event = self.burst(DockerEvents(), [("kill", {"signal": "15"}), ("die", {"exitCode": "0"}), ("stop", {})], name="opengym-web-1")
        self.assertIn("action=stop stop=clean outcome=down", event.state)
        self.assertIn("meaning=stopped on request", event.state)

    def test_healthcheck_passed(self):
        event = self.burst(DockerEvents(), [("health_status: healthy", {})], name="jellyfin")
        self.assertIn("action=healthcheck outcome=running", event.state)
        self.assertNotIn("stop=clean", event.state)

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
