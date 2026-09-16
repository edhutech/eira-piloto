import unittest
from types import SimpleNamespace

from participacion.addons.follow_up.addon import IndividualFollowUpAddon
from participacion.application.events import ProgramAddonContext
from participacion.application.notifications import Notification, NotificationLevel
from participacion.adapters.notifications.none import NoneNotifier
from participacion.adapters.notifications.stdout import StdoutNotifier
from participacion.core.models import ProgramRecord, ProviderRef


class AddonAndNotificationTests(unittest.TestCase):
    def test_follow_up_addon_emits_aggregate_event_without_pii(self):
        class Repository:
            def refresh(self, sessions, statuses, *, official):
                return SimpleNamespace(status="REPLACE", critical_transitions=2)
        program = ProgramRecord("pid", "Programa", 0, "official",
                                ProviderRef("test", "source"), ProviderRef("test", "output"), ())
        result = IndividualFollowUpAddon(Repository(), ()).run(
            ProgramAddonContext(program, {}))
        self.assertEqual(result.events[0].event_type, "follow_up.became_critical")
        self.assertEqual(result.events[0].data, {"count": 2})
        self.assertNotIn("participant", repr(result.events[0].data))

    def test_none_and_stdout_notifiers_are_portable(self):
        notification = Notification("T", "B", NotificationLevel.SUCCESS)
        self.assertTrue(NoneNotifier().notify(notification))
        self.assertTrue(StdoutNotifier().notify(notification))


if __name__ == "__main__":
    unittest.main()
