from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from participacion.adapters.google.sheets.control import GoogleSheetsControlRepository
from participacion.cli.cloud import (
    CLOUD_BUNDLE_KEY,
    CLOUD_BUNDLE_VERSION,
    _find_sheet_in_folder,
    run_main,
    setup_main,
)
from participacion.core.models import SessionRecord


class Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class DiscoveryValues:
    def __init__(self, config_rows):
        self.config_rows = config_rows

    def get(self, *, spreadsheetId, range):
        if range == "'Configuración'!A:C":
            return Request({"values": self.config_rows.get(spreadsheetId, [])})
        return Request({"values": []})


class DiscoverySpreadsheets:
    def __init__(self, titles, config_rows):
        self.titles = titles
        self._values = DiscoveryValues(config_rows)

    def get(self, *, spreadsheetId, **kwargs):
        return Request({
            "sheets": [
                {"properties": {"title": title}}
                for title in self.titles.get(spreadsheetId, [])
            ]
        })

    def values(self):
        return self._values


class DiscoverySheets:
    def __init__(self, titles, config_rows):
        self.api = DiscoverySpreadsheets(titles, config_rows)

    def spreadsheets(self):
        return self.api


def bundle_row(folder_id: str, sheet_id: str, program_name: str = "BASF"):
    bundle = {
        "version": CLOUD_BUNDLE_VERSION,
        "program": {
            "program_id": folder_id,
            "program_name": program_name,
            "participant_mode": "official",
            "source": {"provider": "google_drive", "ref": folder_id},
            "output": {"provider": "google_sheets", "ref": sheet_id},
            "sessions": [],
        },
    }
    return [
        ["key", "value_json", "updated_at"],
        [CLOUD_BUNDLE_KEY, json.dumps(bundle), ""],
    ]


class CloudWorkbookDiscoveryTests(unittest.TestCase):
    def test_run_discovers_cloud_identity_not_title_count(self):
        children = [
            {"id": "fixture", "name": "Participación - Prueba Participación",
             "mimeType": "application/vnd.google-apps.spreadsheet"},
            {"id": "basf", "name": "Participación - BASF - Eira Piloto",
             "mimeType": "application/vnd.google-apps.spreadsheet"},
            {"id": "mgmt", "name": "BASF - MGMT",
             "mimeType": "application/vnd.google-apps.spreadsheet"},
        ]
        sheets = DiscoverySheets(
            {
                "fixture": ["Participantes", "Control", "Sesiones", "Programa"],
                "basf": ["Participantes", "Control", "Sesiones", "Programa", "Configuración", "Estado"],
                "mgmt": ["PARTICIPANTES", "gOS_class_participants"],
            },
            {"basf": bundle_row("root", "basf")},
        )
        with patch("participacion.cli.cloud.list_children", return_value=children):
            self.assertEqual(
                _find_sheet_in_folder(object(), sheets, "root"),
                "basf",
            )

    def test_setup_reuses_single_legacy_workbook_with_extended_title(self):
        children = [
            {"id": "basf", "name": "Participación - BASF - Eira Piloto",
             "mimeType": "application/vnd.google-apps.spreadsheet"},
            {"id": "fixture", "name": "Participación - Prueba Participación",
             "mimeType": "application/vnd.google-apps.spreadsheet"},
        ]
        sheets = DiscoverySheets(
            {
                "basf": ["Participantes", "Control", "Sesiones", "Programa"],
                "fixture": ["Participantes", "Control", "Sesiones", "Programa"],
            },
            {},
        )
        with patch("participacion.cli.cloud.list_children", return_value=children):
            self.assertEqual(
                _find_sheet_in_folder(
                    object(), sheets, "root",
                    program_name="BASF", allow_legacy=True,
                ),
                "basf",
            )

    def test_explicit_sheet_must_belong_to_eira_folder(self):
        children = [
            {"id": "inside", "name": "Any",
             "mimeType": "application/vnd.google-apps.spreadsheet"},
        ]
        sheets = DiscoverySheets({"inside": []}, {})
        with patch("participacion.cli.cloud.list_children", return_value=children):
            with self.assertRaisesRegex(ValueError, "no pertenece"):
                _find_sheet_in_folder(
                    object(), sheets, "root",
                    "https://docs.google.com/spreadsheets/d/12345678901234567890/edit",
                )


class ControlStructuralReconciliationTests(unittest.TestCase):
    def test_missing_session_is_appended_without_touching_manual_tracking(self):
        headers = [
            "session_number", "session_name", "folder_id", "transcript_status",
            "chat_status", "processing_status", "last_processed_at",
            "tracking_eligible",
        ]
        rows = [
            headers,
            [1, "Old name", "old-key", "present", "present", "PROCESSED",
             "2026-01-01T00:00:00+00:00", "No"],
        ]
        values = MagicMock()
        values.get.return_value = Request({"values": rows})
        values.batchUpdate.return_value = Request({})
        values.append.return_value = Request({})
        service = MagicMock()
        service.spreadsheets.return_value.values.return_value = values

        result = GoogleSheetsControlRepository(service, "sheet").ensure_sessions([
            SessionRecord(1, "Sesión 1", "folder-1"),
            SessionRecord(2, "Sesión 2", "folder-2"),
        ])

        self.assertEqual(result, "REPLACE")
        updates = values.batchUpdate.call_args.kwargs["body"]["data"]
        self.assertEqual(
            {item["range"] for item in updates},
            {"'Control'!B2", "'Control'!C2"},
        )
        inserted = values.append.call_args.kwargs["body"]["values"][0]
        self.assertEqual(inserted[0:3], [2, "Sesión 2", "folder-2"])
        self.assertEqual(inserted[3:7], ["pending", "pending", "pending", ""])
        self.assertEqual(inserted[7], "Sí")
        self.assertEqual(rows[1][7], "No")

    def test_duplicate_control_session_fails_closed(self):
        rows = [
            ["session_number", "session_name", "folder_id", "transcript_status",
             "chat_status", "processing_status", "last_processed_at"],
            [1, "A", "a", "pending", "pending", "pending", ""],
            [1, "B", "b", "pending", "pending", "pending", ""],
        ]
        values = MagicMock()
        values.get.return_value = Request({"values": rows})
        service = MagicMock()
        service.spreadsheets.return_value.values.return_value = values
        with self.assertRaisesRegex(ValueError, "duplicado"):
            GoogleSheetsControlRepository(service, "sheet").ensure_sessions([
                SessionRecord(1, "Sesión 1", "folder-1"),
            ])


class CloudNoRosterFlowTests(unittest.TestCase):
    def test_setup_accepts_auto_mode_without_external_roster(self):
        spec = {
            "program": {
                "program_name": "Demo",
                "participant_mode": "auto",
                "sessions": [{
                    "session_number": 1,
                    "session_id": "DEMO-S01",
                    "session_name": "Sesión 1",
                    "evidence_sources": [{
                        "provider": "google_drive",
                        "kind": "artifact",
                        "ref": "artifact-1",
                        "evidence_type": "chat",
                    }],
                }],
            },
        }
        record = {
            "program_id": "root",
            "program_name": "Demo",
            "session_count": 1,
            "participant_mode": "auto",
            "source": {"provider": "google_drive", "ref": "root", "metadata": {"url": "url"}},
            "output": {"provider": "google_sheets", "ref": "sheet"},
            "sessions": [{
                "session_number": 1,
                "session_id": "DEMO-S01",
                "session_name": "Sesión 1",
                "evidence_sources": [{
                    "provider": "google_drive",
                    "kind": "artifact",
                    "ref": "artifact-1",
                    "evidence_type": "chat",
                }],
            }],
        }
        json_store = MagicMock()
        json_store.get.return_value = None
        state_store = MagicMock()
        state_store.store.get.return_value = None
        control = MagicMock()

        with (
            patch("participacion.cli.cloud._load_json_arg", return_value=spec),
            patch("participacion.cli.cloud.extract_folder_id", return_value="root"),
            patch("participacion.cli.cloud.get_google_services", return_value=(object(), object())),
            patch("participacion.cli.cloud.validate_drive_folder", return_value={}),
            patch("participacion.cli.cloud._find_sheet_in_folder", return_value="sheet"),
            patch("participacion.cli.cloud.execute_init", return_value=record),
            patch("participacion.cli.cloud.GoogleSheetsControlRepository", return_value=control),
            patch("participacion.cli.cloud.GoogleSheetsJsonStore", return_value=json_store),
            patch("participacion.cli.cloud.GoogleSheetsStateStore", return_value=state_store),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                setup_main([
                    "--drive-folder", "https://drive.google.com/drive/folders/root",
                    "--spec", "ignored.json",
                    "--yes",
                ]),
                0,
            )
        written_bundle = json_store.put.call_args.args[1]
        self.assertNotIn("roster", written_bundle)
        self.assertEqual(written_bundle["program"]["participant_mode"], "auto")

    def test_run_recovers_auto_program_without_roster(self):
        program = {
            "program_id": "root",
            "program_name": "Demo",
            "session_count": 1,
            "participant_mode": "auto",
            "source": {"provider": "google_drive", "ref": "root", "metadata": {}},
            "output": {"provider": "google_sheets", "ref": "sheet"},
            "sessions": [{
                "session_number": 1,
                "session_name": "Sesión 1",
                "source_ref": "folder-1",
            }],
        }
        bundle = {
            "version": CLOUD_BUNDLE_VERSION,
            "program": program,
            "pilot": {
                "program": {"program_id": "root", "program_name": "Demo", "sheet_id": "sheet"},
                "participation": {"coverage": "UNKNOWN"},
                "session_mapping": {"1": {"session_id": "folder-1", "session_order": 1}},
                "longitudinal": {"minimum_observations": 1, "recent_window_size": 1, "trend_threshold": 0},
                "signals": {
                    "version": "pilot.v1",
                    "participation_silence_streak": {"enabled": True, "minimum_streak": 2},
                },
                "alerts": {"version": "pilot.v1", "silence_level": "OBSERVAR"},
            },
            "known_external": [],
            "attendance_identity": [],
        }
        config_store = MagicMock()
        config_store.get.return_value = bundle
        state_store = MagicMock()
        runner = MagicMock()
        runner.run.return_value = []
        control = MagicMock()

        with (
            patch("participacion.cli.cloud.extract_folder_id", return_value="root"),
            patch("participacion.cli.cloud.get_google_services_with_docs", return_value=(object(), object(), object())),
            patch("participacion.cli.cloud.validate_drive_folder", return_value={}),
            patch("participacion.cli.cloud._find_sheet_in_folder", return_value="sheet"),
            patch("participacion.cli.cloud.GoogleSheetsJsonStore", return_value=config_store),
            patch("participacion.cli.cloud.GoogleSheetsStateStore", return_value=state_store),
            patch("participacion.cli.cloud.GoogleSheetsControlRepository", return_value=control),
            patch("participacion.cli.cloud.build_runner", return_value=runner),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                run_main([
                    "--drive-folder", "https://drive.google.com/drive/folders/root",
                    "--sync-only",
                ]),
                0,
            )
        control.ensure_sessions.assert_called_once()
        runner.run.assert_called_once_with("root")


if __name__ == "__main__":
    unittest.main()
