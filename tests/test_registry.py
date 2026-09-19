import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from participacion.application.registry import load_programs, save_programs
from participacion.core.models import EvidenceSourceRef, ProgramRecord, ProviderRef, SessionRecord


class RegistryTests(unittest.TestCase):
    def program(self, identifier):
        return ProgramRecord(
            identifier, "Programa " + identifier, 1, "auto",
            ProviderRef("google_drive", "folder-" + identifier),
            ProviderRef("google_sheets", "sheet-" + identifier),
            (SessionRecord(1, "Sesion 1", "session-" + identifier),),
        )

    def test_versioned_registry_loads_and_legacy_registry_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "programs.json"
            legacy = {"p1": {"program_id": "p1", "program_name": "P1", "session_count": 1,
                              "participant_mode": "auto", "source": {"provider": "google_drive", "ref": "f"},
                              "output": {"provider": "google_sheets", "ref": "s"},
                              "sessions": [{"session_number": "01", "session_name": "S", "source_ref": "ss"}]}}
            path.write_text(json.dumps(legacy), encoding="utf-8")
            before = path.read_bytes()
            loaded = load_programs(path)
            self.assertEqual(loaded["p1"].sessions[0].session_number, 1)
            self.assertEqual(path.read_bytes(), before)
            save_programs(path, loaded)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 1)
            self.assertEqual(set(payload["programs"]), {"p1"})

    def test_unknown_future_registry_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "programs.json"
            path.write_text('{"version": 99, "programs": {}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Versión"):
                load_programs(path)

    def test_new_registry_session_requires_explicit_id_and_sources_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "programs.json"
            payload = {"version": 1, "programs": {"p1": {
                "program_id": "p1", "program_name": "P1", "session_count": 1,
                "participant_mode": "official",
                "source": {"provider": "google_drive", "ref": "root"},
                "output": {"provider": "google_sheets", "ref": "sheet"},
                "sessions": [{"session_number": 1, "session_id": "S01",
                               "session_name": "S01", "evidence_sources": [
                                   {"provider": "google_drive", "kind": "artifact", "ref": "f1", "evidence_type": "transcript"}
                               ]}]
            }}}
            path.write_text(json.dumps(payload), encoding="utf-8")
            session = load_programs(path)["p1"].sessions[0]
            self.assertEqual(session.session_id, "S01")
            self.assertEqual(session.evidence_sources, (EvidenceSourceRef("google_drive", "artifact", "f1", "transcript"),))

    def test_new_registry_rejects_both_or_neither_evidence_mode(self):
        for session in (
            {"session_number": 1, "session_id": "S01", "session_name": "S01",
             "source_ref": "folder", "evidence_sources": []},
            {"session_number": 1, "session_id": "S01", "session_name": "S01"},
        ):
            with self.subTest(session=session), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "programs.json"
                payload = {"version": 1, "programs": {"p1": {
                    "program_id": "p1", "program_name": "P1", "session_count": 1,
                    "participant_mode": "official", "source": {"provider": "google_drive", "ref": "root"},
                    "output": {"provider": "google_sheets", "ref": "sheet"}, "sessions": [session]
                }}}
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "INVALID"):
                    load_programs(path)

    def test_new_registry_requires_evidence_type(self):
        record = {
            "program_id": "p1", "program_name": "P1", "session_count": 1,
            "participant_mode": "official", "source": {"provider": "google_drive", "ref": "root"},
            "output": {"provider": "google_sheets", "ref": "sheet"},
            "sessions": [{"session_number": 1, "session_id": "S01",
                          "session_name": "S01", "evidence_sources": [
                              {"provider": "google_drive", "kind": "artifact", "ref": "f1"}
                          ]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "programs.json"
            path.write_text(json.dumps({"p1": record}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evidence_type"):
                load_programs(path)

    def test_save_preserves_multiple_programs_and_cleans_failed_temporary_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "programs.json"
            save_programs(path, {"p1": self.program("p1"), "p2": self.program("p2")})
            self.assertEqual(set(load_programs(path)), {"p1", "p2"})
            with patch("participacion.application.registry.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    save_programs(path, {"p1": self.program("p1")})
            self.assertEqual(set(load_programs(path)), {"p1", "p2"})
            self.assertEqual(list(Path(directory).glob(".*programs.json.*")), [])

    def test_google_sheet_schema_is_shared_by_bootstrap_and_styling(self):
        from participacion.adapters.google import bootstrap
        from participacion.adapters.google.sheets import styling
        from participacion.adapters.google.sheets.schema import STATIC_HEADERS

        self.assertIs(bootstrap.PROGRAM_HEADERS, STATIC_HEADERS["Programa"])
        self.assertIs(styling.HEADERS, STATIC_HEADERS)
        self.assertEqual(bootstrap.SESSION_HEADERS, STATIC_HEADERS["Sesiones"])
        self.assertEqual(bootstrap.PARTICIPANT_HEADERS, STATIC_HEADERS["Participantes"])
        self.assertEqual(bootstrap.RANKING_HEADERS, STATIC_HEADERS["Ranking"])
        self.assertEqual(bootstrap.CONTROL_HEADERS, STATIC_HEADERS["Control"])


if __name__ == "__main__":
    unittest.main()
