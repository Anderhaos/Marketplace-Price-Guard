"""Isolated operational alerts test; no actual Docker or network calls."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import json

file_path = Path(__file__).with_name("nosale-ops-alert.py")
spec = importlib.util.spec_from_file_location("nosale_ops_alert", file_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class OpsAlertTests(TestCase):
    def test_new_failure_dedup_reminder_recovery(self):
        now = 100000
        baseline = {"issues": [], "last_sent": 0}
        self.assertIsNone(mod.monitor([], now, baseline))
        self.assertIn("container", mod.monitor(["container offline"], now, baseline)[1])
        active = {"issues": ["container offline"], "last_sent": now}
        self.assertIsNone(mod.monitor(["container offline"], now + 60, active))
        self.assertIsNotNone(mod.monitor(["container offline"], now + 6 * 3600, active))
        self.assertIsNotNone(mod.monitor(["container offline", "backup stale"], now + 60, active))
        recovered = mod.monitor([], now + 65, active)
        self.assertIn("восстановлены", recovered[0])
        self.assertIsNone(mod.monitor([], now + 65, baseline))

    def test_persistent_alert_state_is_only_root_private(self):
        with TemporaryDirectory() as directory:
            p = Path(directory) / "alerts" / "state.json"
            self.assertEqual(mod.load_state(p), {"issues": [], "last_sent": 0})
            mod.save_state(["service unavailable"], 1234, p)
            self.assertEqual(mod.load_state(p)["issues"], ["service unavailable"])
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_collect_health_issues_without_real_services(self):
        with TemporaryDirectory() as directory:
            backup = Path(directory)
            snap = backup / "20261025T112529Z"
            snap.mkdir()
            sums = snap / "SHA256SUMS"
            sums.write_text("fixture", encoding="ascii")

            def cmd(*args, **kwargs):
                if args[:2] == ("docker", "inspect"):
                    return json.dumps({"Status": "running", "Health": {"Status": "healthy"}})
                if args[:2] == ("systemctl", "is-active"):
                    return "active"
                if args[:2] == ("systemctl", "show"):
                    return "success"
                if args[0] == "curl":
                    return "200"
                raise AssertionError(f"Unexpected: {args}")

            with patch.object(mod, "BACKUPS", backup), patch.object(mod, "command", side_effect=cmd), \
                 patch.object(mod.subprocess, "run") as verifier:
                self.assertEqual(mod.collect_issues(now=sums.stat().st_mtime), [])
                verifier.assert_called_once()
                issues = mod.collect_issues(now=sums.stat().st_mtime + 41 * 3600)
                self.assertIn("latest completed offsite backup is missing or stale", issues)

            def failed(*args, **kwargs):
                if args[:2] == ("docker", "inspect"):
                    if args[-1] == "mpnosale-saas-worker-1":
                        return json.dumps({"Status": "running", "Health": {"Status": "unhealthy"}})
                    return json.dumps({"Status": "running", "Health": {"Status": "healthy"}})
                if args[:2] == ("systemctl", "is-active"):
                    return "active"
                if args[:2] == ("systemctl", "show"):
                    return "failed"
                if args[0] == "curl":
                    return "503"
                raise AssertionError(args)
            with patch.object(mod, "BACKUPS", backup), patch.object(mod, "command", side_effect=failed), \
                 patch.object(mod.subprocess, "run"):
                issues = mod.collect_issues(now=sums.stat().st_mtime)
            self.assertTrue(any("worker-1" in issue for issue in issues))
            self.assertTrue(any("backup service failed" in issue for issue in issues))
            self.assertTrue(any("non-200" in issue for issue in issues))

    def test_no_unapproved_mail_relay_hosts(self):
        with self.assertRaisesRegex(RuntimeError, "approved"):
            mod.send_owner_alert("Synthetic", "Test", {
                "MAIL_RELAY_URL": "https://evil.example/svdev-relay.php",
                "MAIL_RELAY_TOKEN": "a" * 40,
                "NOSALE_OWNER_EMAIL": "person@example.org",
                "SMTP_FROM": "noreply@svdev.pro",
            })

    def test_no_secret_in_alert_subject_or_health_issue_text(self):
        with TemporaryDirectory() as directory:
            p = Path(directory) / ".env"
            p.write_text("MAIL_RELAY_TOKEN='secret_test_123'\nNOSALE_OWNER_EMAIL=owner@example.test\n", encoding="utf-8")
            env = mod.read_env(p)
            self.assertEqual(env["MAIL_RELAY_TOKEN"], "secret_test_123")
            self.assertEqual(env["NOSALE_OWNER_EMAIL"], "owner@example.test")
            self.assertNotIn("secret_test_123", mod.monitor(["worker unhealthy"], 100, {"issues": [], "last_sent": 0})[1])
