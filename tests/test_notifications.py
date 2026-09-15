import unittest
from types import SimpleNamespace
from unittest.mock import patch

from participacion.cli import main
from participacion.application.events import ApplicationEvent
from participacion.application.notifications import (Notification, NotificationLevel,
                                     build_program_notification, notify_events)
from participacion.adapters.notifications.notify_send import NotifySendNotifier


def event(event_type, data=None, name="Programa"):
    return ApplicationEvent(event_type, "pid", name, data or {})


class NotificationTests(unittest.TestCase):
    def test_all_skipped_is_silent(self):
        self.assertIsNone(build_program_notification(()))

    def test_success_for_changed_processing_participant_or_derived_view(self):
        for changes in (
            {"sessions_processed": 1, "participants_created": 0},
            {"sessions_processed": 0, "participants_created": 1},
            {"ranking_changed": True},
            {"tracking_changed": True},
        ):
            notification = build_program_notification((event("program.updated", changes),))
            self.assertIsNotNone(notification)
            self.assertEqual(notification.level, NotificationLevel.SUCCESS)

    def test_incomplete_and_needs_review_are_warnings(self):
        incomplete = build_program_notification((event("program.requires_attention", {"incomplete": 1, "needs_review": 0}),))
        review = build_program_notification((event("program.requires_attention", {"incomplete": 0, "needs_review": 1}),))
        self.assertEqual(incomplete.level, NotificationLevel.WARNING)
        self.assertIn("incompleta", incomplete.body)
        self.assertIn("identidad", review.body)

    def test_failed_has_priority_over_warning_and_success(self):
        notification = build_program_notification((
            event("program.failed", {"sessions_failed": 1, "error_count": 1}),
            event("program.requires_attention", {"incomplete": 1, "needs_review": 0}),
            event("program.updated", {"sessions_processed": 1}),
        ))
        self.assertEqual(notification.level, NotificationLevel.CRITICAL)
        self.assertIn("falló", notification.body)

    def test_multiple_sessions_are_summarized_without_ids(self):
        notification = build_program_notification((event("program.updated", {
            "sessions_processed": 2, "participants_created": 3,
            "ranking_changed": True, "tracking_changed": False,
        }),))
        self.assertEqual(notification.body, "2 sesiones procesadas · 3 participantes nuevos · Ranking actualizado")
        self.assertNotIn("participant_id", notification.body)

    @patch("participacion.adapters.notifications.notify_send.subprocess.run")
    def test_notify_send_command_urgency_icon_and_timeout(self, run):
        run.return_value = SimpleNamespace(returncode=0, stderr="")
        notifier = NotifySendNotifier("/usr/bin/notify-send", timeout_seconds=3)
        self.assertTrue(notifier.notify(Notification("T", "B", NotificationLevel.CRITICAL)))
        command = run.call_args.args[0]
        self.assertEqual(command, ["/usr/bin/notify-send", "--urgency", "critical", "--expire-time", "12000", "--icon", "dialog-error", "T", "B"])
        self.assertEqual(run.call_args.kwargs["timeout"], 3)

    @patch("participacion.adapters.notifications.notify_send.subprocess.run")
    def test_subprocess_failure_is_best_effort(self, run):
        run.side_effect = OSError("no dbus")
        notifier = NotifySendNotifier("notify-send")
        self.assertFalse(notify_events(notifier, (event("program.failed", {"sessions_failed": 1}),)))

    def test_unavailable_notifier_is_best_effort(self):
        notifier = NotifySendNotifier(None)
        with patch("participacion.adapters.notifications.notify_send.shutil.which", return_value=None):
            notifier = NotifySendNotifier()
        self.assertFalse(notify_events(notifier, (event("program.updated", {"sessions_processed": 1}),)))

    def test_cli_no_notify_does_not_construct_or_call_notifier(self):
        runner = SimpleNamespace(run=lambda program_id=None: [])
        def factory(programs, state): return runner
        def forbidden(): raise AssertionError("notifier should be disabled")
        self.assertEqual(main(["--no-notify"], runner_factory=factory, notifier_factory=forbidden), 0)

    def test_addon_only_change_is_silent(self):
        addon_event = event("follow_up.updated", {"count": 1})
        self.assertIsNone(build_program_notification((addon_event,)))

    def test_follow_up_critical_beats_success(self):
        notification = build_program_notification((
            event("follow_up.became_critical", {"count": 1}),
            event("program.updated", {"sessions_processed": 1}),
        ))
        self.assertEqual(notification.level, NotificationLevel.WARNING)

    def test_event_payload_is_aggregate_and_non_pii(self):
        payload = {"sessions_failed": 1, "error_count": 1}
        application_event = event("program.failed", payload)
        self.assertEqual(set(application_event.data), {"sessions_failed", "error_count"})
        self.assertNotIn("name@example.com", repr(application_event.data))

    def test_cli_runtime_failure_uses_event_policy(self):
        class Notifier:
            def __init__(self):
                self.notifications = []

            def notify(self, notification):
                self.notifications.append(notification)
                return True

        notifier = Notifier()
        def factory(programs, state):
            raise RuntimeError("discovery failed")
        self.assertEqual(main([], runner_factory=factory,
                              notifier_factory=lambda: notifier), 1)
        self.assertEqual(len(notifier.notifications), 1)
        self.assertEqual(notifier.notifications[0].level, NotificationLevel.CRITICAL)


if __name__ == "__main__":
    unittest.main()
