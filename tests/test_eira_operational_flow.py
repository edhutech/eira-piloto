from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from participacion.adapters.google.sheets.operational import GoogleSheetsOperationalRepository
from participacion.application.init_program import build_plan
from participacion.application.pilot.operational import build_operational_view
from participacion.application.pilot.results import HistoricalSnapshot, PilotResult
from participacion.application.program_runner import ProgramDependencies, ProgramRunner
from participacion.application.session_processor import SessionProcessResult, SessionProcessStatus
from participacion.core.alerts import Alert, AlertEvaluationStatus, AlertLevel
from participacion.core.models import FileChange, FileStatus, ProgramInspection, ProgramRecord, ProviderRef, SessionInspection, SessionRecord, SourceArtifact
from participacion.core.observation import Observation, ObservationStatus
from participacion.core.signals import Signal, SignalSet


class _Request:
    def __init__(self, result=None):
        self.result = result or {}

    def execute(self):
        return self.result


class _Values:
    def __init__(self):
        self.writes = []

    def get(self, **kwargs):
        return _Request({"values": []})

    def clear(self, **kwargs):
        self.writes.append(("clear", kwargs))
        return _Request()

    def update(self, **kwargs):
        self.writes.append(("update", kwargs))
        return _Request()


class _Sheets:
    def __init__(self):
        self.values_api = _Values()

    def spreadsheets(self):
        return self

    def values(self):
        return self.values_api


class OperationalFlowTests(unittest.TestCase):
    def test_current_view_uses_only_latest_snapshot(self):
        old_signal = Signal("p1", "old_signal", ("participation",), ("S01",), {}, "v1")
        old_alert = Alert("p1", AlertEvaluationStatus.EVALUATED, AlertLevel.OBSERVAR,
                          (old_signal,), {"reason": "old"}, "alert.v1")
        latest = HistoricalSnapshot("S02", 2, (), (), SignalSet((), ()), ())
        result = PilotResult("program", (
            HistoricalSnapshot("S01", 1, (), (), SignalSet((old_signal,), ()), (old_alert,)),
            latest,
        ), (), ())
        view = build_operational_view(result)
        self.assertEqual(view.as_of_session_id, "S02")
        self.assertEqual(view.cases, ())
        self.assertEqual(view.summary(), {"NORMAL": 0, "OBSERVAR": 0, "INSUFFICIENT_DATA": 0})

    def test_planned_snapshot_does_not_advance_operational_as_of(self):
        result = PilotResult("program", (
            HistoricalSnapshot("S01", 1, (), (), SignalSet((), ()), (), operational=True),
            HistoricalSnapshot("S02", 2, (), (), SignalSet((), ()), (), operational=False),
            HistoricalSnapshot("S03", 3, (), (), SignalSet((), ()), (), operational=False),
        ), (), ())
        self.assertEqual(build_operational_view(result).as_of_session_id, "S01")

    def test_operational_repository_uses_observations_not_manual_tracking(self):
        observation = Observation("p1", "S01", "attendance", "attendance_ratio", Decimal("0.8"),
                                  ObservationStatus.OBSERVED)
        snapshot = HistoricalSnapshot("S01", 1, (observation,), (), SignalSet((), ()), ())
        view = build_operational_view(PilotResult("program", (snapshot,), (observation,), ()))
        participant = type("Participant", (), {"participant_id": "p1", "nombre": "Synthetic", "correo": "p1@example.test"})
        repository = GoogleSheetsOperationalRepository(
            _Sheets(), "sheet", type("Participants", (), {"load": lambda self: [participant()]})()
        )
        repository.persist(view)
        writes = repository.service.values_api.writes
        summary_update = next(item for item in writes if item[0] == "update" and "Seguimiento'!A1" in item[1]["range"])
        self.assertEqual(summary_update[1]["body"]["values"][5][1], 1)

    def test_cloud_dependencies_disable_legacy_views_and_addons(self):
        tracking = MagicMock()
        addon = MagicMock()
        addon.run.return_value = None
        dependencies = ProgramDependencies(
            session_processor=MagicMock(), participant_repository=MagicMock(),
            session_results_repository=MagicMock(), ranking_repository=MagicMock(),
            tracking_repository=tracking, addons=(addon,), control_repository=None,
            enable_legacy_tracking=False, enable_legacy_addons=False,
        )
        self.assertFalse(dependencies.enable_legacy_tracking)
        self.assertFalse(dependencies.enable_legacy_addons)

    def test_cloud_runner_processes_participation_without_legacy_views(self):
        session = SessionRecord(1, "S01", "folder")
        program = ProgramRecord("p", "Demo", 1, "auto", ProviderRef("google_drive", "root"),
                                ProviderRef("google_sheets", "sheet"), (session,))
        artifact = SourceArtifact("artifact", "evidence")
        inspection = ProgramInspection(program, [SessionInspection(
            session, [artifact], [FileChange(artifact, FileStatus.NEW, "fingerprint")]
        )])
        processor = MagicMock()
        processor.process.return_value = SessionProcessResult(1, SessionProcessStatus.PROCESSED, changed=True)
        participants = MagicMock()
        participants.load.return_value = []
        results = MagicMock()
        results.load_scores.return_value = []
        ranking = MagicMock()
        ranking.persist.return_value = SimpleNamespace(status="NOOP")
        tracking = MagicMock()
        addon = MagicMock()
        dependencies = ProgramDependencies(
            processor, participants, results, ranking, tracking, (addon,), None,
            enable_legacy_tracking=False, enable_legacy_addons=False,
        )
        state = {"version": 1, "programs": {}}
        store = SimpleNamespace(load=lambda: state, save=lambda value: state.update(value))
        runner = ProgramRunner({"p": program}, object(), lambda _id, _program: dependencies,
                                store, lambda *_args: [inspection])
        runner.run("p")
        tracking.refresh.assert_not_called()
        addon.run.assert_not_called()

    def test_planned_session_is_valid_without_evidence(self):
        plan = build_plan(
            program_name="Demo", folder_id="folder", folder_url="url", session_count=2,
            participant_mode="auto", imported_participants=[], existing_children={},
            existing_sheet_id=None,
            evidence_sessions=[
                {"session_number": 1, "session_id": "S01", "session_name": "S01",
                 "evidence_sources": [{"provider": "google_drive", "kind": "container",
                                        "ref": "evidence", "evidence_type": "transcript"}]},
                {"session_number": 2, "session_id": "S02", "session_name": "S02",
                 "evidence_sources": []},
            ],
        )
        self.assertTrue(plan.evidence_sessions[1]["planned"])

    def test_setup_validation_failure_happens_before_execute_init(self):
        spec = {"program": {"program_name": "Demo", "participant_mode": "auto", "sessions": [
            {"session_number": 1, "session_id": "S01", "session_name": "S01", "evidence_sources": []}
        ]}}
        with (
            patch("participacion.cli.cloud._load_json_arg", return_value=spec),
            patch("participacion.cli.cloud.extract_folder_id", return_value="folder"),
            patch("participacion.cli.cloud.get_google_services", return_value=(object(), object())),
            patch("participacion.cli.cloud.validate_drive_folder", return_value={}),
            patch("participacion.cli.cloud._find_sheet_in_folder", return_value=None),
            patch("participacion.cli.cloud._validate_rule_lists", side_effect=ValueError("late invalid config")),
            patch("participacion.cli.cloud.execute_init") as execute,
            redirect_stdout(io.StringIO()),
        ):
            from participacion.cli.cloud import setup_main
            self.assertEqual(setup_main(["--drive-folder", "url", "--spec", "ignored", "--yes"]), 1)
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
