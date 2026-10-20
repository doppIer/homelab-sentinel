import unittest

from sentinel.collectors.lifeboat import LifeboatBackups, parse_ts, tier_of

NOW = 1792500000.0


def ts(hours_ago):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(NOW - hours_ago * 3600, tz=timezone.utc).strftime("%a %Y-%m-%d %H:%M:%S UTC")


class FakeSystemd:
    def __init__(self):
        self.units = {}
        self.timers = {}
        self.listed = []

    def __call__(self, cmd):
        if cmd[1] == "list-units":
            return "\n".join(f"{u} loaded failed failed demo" for u in self.listed)
        if cmd[1] == "show":
            unit = cmd[3]
            info = self.units.get(unit, {"LoadState": "not-found", "ActiveState": "inactive", "Result": "success", "ExecMainStatus": "0", "ExecMainExitTimestamp": ""})
            return "\n".join(f"{k}={v}" for k, v in info.items())
        if cmd[1] == "is-active":
            return self.timers.get(cmd[2], "inactive")
        return ""


def unit(result="success", exit_code="0", hours_ago=1.0, active="inactive"):
    return {"LoadState": "loaded", "ActiveState": active, "Result": result, "ExecMainStatus": exit_code, "ExecMainExitTimestamp": ts(hours_ago)}


class LifeboatTest(unittest.TestCase):
    def setUp(self):
        self.systemd = FakeSystemd()
        self.collector = LifeboatBackups(shell=self.systemd, clock=lambda: NOW)

    def test_helpers(self):
        self.assertEqual(tier_of("lifeboat-backup@tier1.service"), "tier1")
        self.assertEqual(tier_of("lifeboat-backup-demo.service"), "demo")
        self.assertAlmostEqual(parse_ts(ts(2)), NOW - 7200, delta=1)
        self.assertIsNone(parse_ts("n/a"))

    def test_not_installed_is_silent(self):
        self.assertEqual(self.collector.changes(), [])

    def test_healthy_backups_are_silent(self):
        self.systemd.units["lifeboat-backup@tier1.service"] = unit(hours_ago=2)
        self.systemd.units["lifeboat-backup@tier2.service"] = unit(hours_ago=20)
        self.assertEqual(self.collector.changes(), [])

    def test_failed_run(self):
        self.systemd.units["lifeboat-backup@tier1.service"] = unit(result="exit-code", exit_code="1", hours_ago=0.5)
        self.systemd.timers["lifeboat-backup-tier1.timer"] = "active"
        events = self.collector.changes()
        self.assertEqual(len(events), 1)
        state = events[0].state
        self.assertIn("backup=tier1 unit=lifeboat-backup@tier1.service tool=restic last_run=failed result=exit-code exit_code=1 finished=0.5h ago rpo_target=6h timer=active", state)
        self.assertEqual(self.collector.changes(), [])

    def test_stale_tier1_after_rpo_plus_grace(self):
        self.systemd.units["lifeboat-backup@tier1.service"] = unit(hours_ago=6.5)
        self.assertEqual(self.collector.changes(), [])
        self.collector.last.clear()
        self.systemd.units["lifeboat-backup@tier1.service"] = unit(hours_ago=13)
        events = self.collector.changes()
        self.assertIn("last_success=13.0h ago rpo_target=6h rpo_breached=yes", events[0].state)

    def test_recovery_after_failure(self):
        self.systemd.units["lifeboat-backup@tier2.service"] = unit(result="exit-code", exit_code="1", hours_ago=3)
        self.collector.changes()
        self.systemd.units["lifeboat-backup@tier2.service"] = unit(hours_ago=0.1)
        events = self.collector.changes()
        self.assertEqual(events[0].kind, "ok")
        self.assertIn("previous_status=failed recovered=yes", events[0].state)

    def test_running_backup_is_ignored(self):
        self.systemd.units["lifeboat-backup@tier1.service"] = unit(result="exit-code", exit_code="1", active="activating")
        self.assertEqual(self.collector.changes(), [])

    def test_extra_units_are_discovered(self):
        self.systemd.listed = ["lifeboat-backup-demo.service"]
        self.systemd.units["lifeboat-backup-demo.service"] = unit(result="exit-code", exit_code="1", hours_ago=0.01)
        events = self.collector.changes()
        self.assertEqual(events[0].subject, "demo")
        self.assertIn("rpo_target=24h timer=inactive", events[0].state)


if __name__ == "__main__":
    unittest.main()
