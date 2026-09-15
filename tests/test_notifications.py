import unittest
from types import SimpleNamespace
from unittest.mock import patch

from participacion.cli import main
from participacion.notifications import (DesktopNotification, NotificationLevel,
                                     NotifySendNotifier, build_program_notification,
                                     notify_program_result)


def result(**overrides):
    values = dict(program_name="Programa", sessions_processed=0, sessions_skipped=3,
                  sessions_incomplete=0, sessions_needs_review=0, sessions_failed=0,
                  participants_created=0, session_results_changed=0,
                  ranking_changed=False, tracking_changed=False, errors=(),
                  session_results=[])
    values.update(overrides)
    return SimpleNamespace(**values)


class NotificationTests(unittest.TestCase):
    def test_all_skipped_is_silent(self):
        self.assertIsNone(build_program_notification(result()))

    def test_success_for_changed_processing_participant_or_derived_view(self):
        for changes in (
            {"sessions_processed": 1, "session_results_changed": 1},
            {"participants_created": 1},
            {"ranking_changed": True},
            {"tracking_changed": True},
        ):
            notification = build_program_notification(result(**changes))
            self.assertIsNotNone(notification)
            self.assertEqual(notification.level, NotificationLevel.SUCCESS)

    def test_incomplete_and_needs_review_are_warnings(self):
        incomplete = SimpleNamespace(status="INCOMPLETE")
        review = SimpleNamespace(status="NEEDS_REVIEW")
        self.assertEqual(build_program_notification(result(sessions_incomplete=1, session_results=[incomplete])).level, NotificationLevel.WARNING)
        self.assertIn("incompleta", build_program_notification(result(sessions_incomplete=1, session_results=[incomplete])).body)
        self.assertIn("identidad", build_program_notification(result(sessions_needs_review=1, session_results=[review])).body)

    def test_failed_has_priority_over_warning_and_success(self):
        notification = build_program_notification(result(
            sessions_failed=1, sessions_incomplete=1, sessions_processed=1,
            session_results_changed=1, errors=("operational",)))
        self.assertEqual(notification.level, NotificationLevel.CRITICAL)
        self.assertIn("falló", notification.body)

    def test_multiple_sessions_are_summarized_without_ids(self):
        notification = build_program_notification(result(
            sessions_processed=2, session_results_changed=2, participants_created=3,
            ranking_changed=True))
        self.assertEqual(notification.body, "2 sesiones procesadas · 3 participantes nuevos · Ranking actualizado")
        self.assertNotIn("participant_id", notification.body)

    @patch("participacion.notifications.subprocess.run")
    def test_notify_send_command_urgency_icon_and_timeout(self, run):
        run.return_value = SimpleNamespace(returncode=0, stderr="")
        notifier = NotifySendNotifier("/usr/bin/notify-send", timeout_seconds=3)
        self.assertTrue(notifier.notify(DesktopNotification("T", "B", NotificationLevel.CRITICAL)))
        command = run.call_args.args[0]
        self.assertEqual(command, ["/usr/bin/notify-send", "--urgency", "critical", "--expire-time", "12000", "--icon", "dialog-error", "T", "B"])
        self.assertEqual(run.call_args.kwargs["timeout"], 3)

    @patch("participacion.notifications.subprocess.run")
    def test_subprocess_failure_is_best_effort(self, run):
        run.side_effect = OSError("no dbus")
        notifier = NotifySendNotifier("notify-send")
        self.assertFalse(notify_program_result(notifier, result(sessions_failed=1)))

    def test_unavailable_notifier_is_best_effort(self):
        notifier = NotifySendNotifier(None)
        with patch("participacion.notifications.shutil.which", return_value=None):
            notifier = NotifySendNotifier()
        self.assertFalse(notify_program_result(notifier, result(sessions_processed=1, session_results_changed=1)))

    def test_cli_no_notify_does_not_construct_or_call_notifier(self):
        runner = SimpleNamespace(run=lambda program_id=None: [result()])
        def factory(programs, state): return runner
        def forbidden(): raise AssertionError("notifier should be disabled")
        self.assertEqual(main(["--no-notify"], runner_factory=factory, notifier_factory=forbidden), 0)


if __name__ == "__main__":
    unittest.main()
