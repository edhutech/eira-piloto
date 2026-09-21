from __future__ import annotations

import unittest
from decimal import Decimal

from participacion.application.modules.models import ApplicabilityResult, ApplicabilityStatus
from participacion.application.modules.resolution import ParticipantResolverAdapter
from participacion.application.pilot.config import PilotConfig
from participacion.application.pilot.runner import PilotRunner, PilotSources
from participacion.core.participants import ParticipantResolver
from participacion.core.scoring import ParticipantSessionScore
from participacion.cli.cloud import _preserve_unspecified_roster_fields
from participacion.application.roster import RosterRecord
from participacion.core.participants import Participant


class Applicable:
    def is_applicable(self, participant_id: str, session_id: str):
        return ApplicabilityResult(ApplicabilityStatus.APPLICABLE)


class CloudFirstPilotTests(unittest.TestCase):
    def test_participation_only_config_requires_no_attendance_source(self):
        config = PilotConfig.from_dict({
            "program": {
                "program_id": "p1",
                "program_name": "Program",
                "sheet_id": "sheet",
            },
            "participation": {"coverage": "UNKNOWN"},
            "session_mapping": {
                "1": {"session_id": "S01", "session_order": 1},
            },
            "longitudinal": {
                "minimum_observations": 1,
                "recent_window_size": 1,
                "trend_threshold": 0,
            },
            "signals": {
                "version": "pilot.v1",
                "participation_silence_streak": {
                    "enabled": True,
                    "minimum_streak": 2,
                },
            },
            "alerts": {"version": "pilot.v1", "silence_level": "OBSERVAR"},
        })
        self.assertIsNone(config.attendance_source)
        self.assertIsNone(config.attendance_mapping)

        resolver = ParticipantResolverAdapter(ParticipantResolver.official([{
            "participant_id": "person-1",
            "nombre": "Person",
            "correo": "person@example.test",
        }]), match_email=True)
        result = PilotRunner(
            config,
            PilotSources(
                participant_scores=lambda: (
                    ParticipantSessionScore(
                        1, "person-1", 1, 1, 0, 0, 0, Decimal("1"), True
                    ),
                ),
                attendance_table=None,
                participant_resolver=resolver,
                applicability_resolver=Applicable(),
                participant_ids=lambda: ["person-1"],
                participation_statuses=lambda: {"1": "PROCESSED"},
            ),
        ).run()

        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.observations[0].dimension, "participation")

    def test_live_roster_preserves_fields_not_declared_by_source(self):
        existing = [
            Participant(
                "p1",
                "Ana",
                "ana@example.test",
                ["Alias Manual"],
                "participant",
                "official",
                "unverified",
                "inactive",
                2,
                5,
            )
        ]
        incoming = [RosterRecord("Ana", "ana@example.test")]
        merged = _preserve_unspecified_roster_fields(
            incoming,
            existing,
            {"nombre", "correo"},
        )
        self.assertEqual(merged[0].aliases, ("Alias Manual",))
        self.assertEqual(merged[0].enrollment_status, "inactive")
        self.assertEqual(merged[0].start_session, 2)
        self.assertEqual(merged[0].end_session, 5)


if __name__ == "__main__":
    unittest.main()
