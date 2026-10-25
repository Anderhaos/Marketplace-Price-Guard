"""Worker health must reflect successful cycles, not merely process existence."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from guard.worker_health import heartbeat_path, is_healthy, mark_success


class WorkerHeartbeatTests(SimpleTestCase):
    def test_absent_stale_future_and_corrupt_heartbeats_fail_closed(self):
        with TemporaryDirectory() as directory, \
             patch("guard.worker_health.HEARTBEAT_DIR", Path(directory)), \
             patch("guard.worker_health.time", return_value=1000):
            self.assertFalse(is_healthy("guard", 180))
            mark_success("guard")
            self.assertTrue(is_healthy("guard", 180))
            self.assertFalse(is_healthy("notifications", 900))
            path = heartbeat_path("guard")
            path.write_text("garbage", encoding="ascii")
            self.assertFalse(is_healthy("guard", 180))
            path.write_text("1300", encoding="ascii")
            self.assertFalse(is_healthy("guard", 180))
            path.write_text("800", encoding="ascii")
            self.assertFalse(is_healthy("guard", 180))
            path.write_text("850", encoding="ascii")
            self.assertTrue(is_healthy("guard", 180))
        with self.assertRaises(ValueError):
            heartbeat_path("../other")

    def test_each_successful_cycle_marks_its_own_worker(self):
        for command, module, work_fn, name in (
            ("run_guard", "guard.management.commands.run_guard", "run_due_checks", "guard"),
            ("run_notifications", "push.management.commands.run_notifications", "process_notification_cycle", "notifications"),
            ("reconcile_tochka", "billing.management.commands.reconcile_tochka", "reconcile_due_payments", "reconciliation"),
        ):
            with self.subTest(command=command), \
                 patch(f"{module}.{work_fn}", return_value=0), \
                 patch(f"{module}.mark_success") as marker:
                call_command(command, "--once")
                marker.assert_called_once_with(name)

    def test_failed_cycles_never_update_heartbeat(self):
        for command, module, work_fn in (
            ("run_guard", "guard.management.commands.run_guard", "run_due_checks"),
            ("run_notifications", "push.management.commands.run_notifications", "process_notification_cycle"),
            ("reconcile_tochka", "billing.management.commands.reconcile_tochka", "reconcile_due_payments"),
        ):
            with self.subTest(command=command), \
                 patch(f"{module}.{work_fn}", side_effect=RuntimeError("synthetic failure")), \
                 patch(f"{module}.mark_success") as marker:
                with self.assertRaises(RuntimeError):
                    call_command(command, "--once")
                marker.assert_not_called()
