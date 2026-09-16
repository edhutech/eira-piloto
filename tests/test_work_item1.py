import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace

from participacion.adapters.google.source import select_and_inspect
from participacion.adapters.google.sheets.participants import ParticipantRepository
from participacion.adapters.parsers.docx import DocxParser
from participacion.application.registry import load_programs
from participacion.application.roster import RosterImporter, RosterRecord
from participacion.cli.main import main
from participacion.core.models import SourceArtifact


class ValuesFake:
    def __init__(self, values):
        self.values = [list(row) for row in values]
        self.writes = []

    def read_values(self):
        return [list(row) for row in self.values]

    def write_cells(self, row, column, values):
        self.writes.append((row, column, values))
        while len(self.values) < row:
            self.values.append([])
        while len(self.values[row - 1]) < column - 1:
            self.values[row - 1].append("")
        for index, value in enumerate(values[0], column - 1):
            while len(self.values[row - 1]) <= index:
                self.values[row - 1].append("")
            self.values[row - 1][index] = value

    def append_rows(self, values):
        self.values.extend(values)

    def append_row(self, values):
        self.append_rows([values])


class WorkItem1Tests(unittest.TestCase):
    def valid_record(self):
        return {
            "program_id": "program-basf-2026", "program_name": "Programa",
            "session_count": 1, "participant_mode": "auto",
            "source": {"provider": "google_drive", "ref": "folder-123"},
            "output": {"provider": "google_sheets", "ref": "sheet-123"},
            "sessions": [{"session_number": 1, "session_name": "Sesion 1", "source_ref": "session-1"}],
        }

    def load_record(self, record):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "programs.json"
            path.write_text(json.dumps({record["program_id"]: record}), encoding="utf-8")
            return load_programs(path)

    def test_invalid_registry_values_fail_closed(self):
        for mutate, message in (
            (lambda r: r.update(participant_mode="offical"), "participant_mode"),
            (lambda r: r["source"].update(provider=""), "source.provider"),
            (lambda r: r.update(session_count=2), "session_count"),
            (lambda r: r["sessions"].append(dict(r["sessions"][0])), "session_number"),
        ):
            record = self.valid_record()
            mutate(record)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.load_record(record)

    def test_legacy_zero_padded_session_numbers_are_normalized(self):
        record = self.valid_record()
        record["session_count"] = "03"
        record["sessions"] = [
            {"session_number": "01", "session_name": "Sesion 1", "source_ref": "s1"},
            {"session_number": "02", "session_name": "Sesion 2", "source_ref": "s2"},
            {"session_number": "03", "session_name": "Sesion 3", "source_ref": "s3"},
        ]
        program = self.load_record(record)[record["program_id"]]
        self.assertEqual([s.session_number for s in program.sessions], [1, 2, 3])
        self.assertTrue(all(isinstance(s.session_number, int) for s in program.sessions))
        self.assertEqual(program.session_count, 3)

    def test_duplicate_detection_happens_after_normalization(self):
        record = self.valid_record()
        record["sessions"] = [
            {"session_number": "01", "session_name": "Sesion 1", "source_ref": "s1"},
            {"session_number": 1, "session_name": "Sesion 1 bis", "source_ref": "s2"},
        ]
        record["session_count"] = 2
        with self.assertRaisesRegex(ValueError, "session_number"):
            self.load_record(record)

    def test_invalid_positive_integer_values_remain_rejected(self):
        for invalid in (0, "00", -1, "-1", "1.5", "", "   ", True, False):
            record = self.valid_record()
            record["sessions"][0]["session_number"] = invalid
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, "session_number"):
                self.load_record(record)

    def test_program_identity_uses_program_id_not_source_ref(self):
        programs = self.load_record(self.valid_record())
        fingerprinted = {"id": "file-1", "name": "voice.txt", "modifiedTime": "same"}
        state = {"programs": {"program-basf-2026": {"sessions": {
            "session-1": {"files": {"file-1": "wrong-placeholder"}}
        }}}}
        from participacion.adapters.filesystem.state import file_fingerprint
        state["programs"]["program-basf-2026"]["sessions"]["session-1"]["files"]["file-1"] = file_fingerprint(fingerprinted)
        drive = SimpleNamespace(files=lambda: SimpleNamespace(list=lambda **kwargs:
            SimpleNamespace(execute=lambda: {"files": [fingerprinted]})))
        inspection = select_and_inspect(drive, programs, state)[0]
        self.assertEqual(inspection.program.program_id, "program-basf-2026")
        self.assertFalse(inspection.sessions[0].requires_processing)
        self.assertNotIn("folder-123", state["programs"])

    def test_roster_dry_run_reads_without_writes_and_apply_writes(self):
        headers = [["nombre", "correo"], ["Ana", "ana@example.com"]]
        backend = ValuesFake(headers)
        repository = ParticipantRepository(backend)
        plan = RosterImporter(repository).dry_run([RosterRecord("Ana", "ana@example.com")])
        self.assertEqual(backend.writes, [])
        self.assertEqual(repository.load_read_only()[0].nombre, "Ana")
        self.assertEqual(backend.writes, [])
        canonical = ParticipantRepository(backend)
        canonical.migrate()
        apply_plan = RosterImporter(canonical).dry_run([RosterRecord("Bea", "bea@example.com")])
        RosterImporter(canonical).apply(apply_plan)
        self.assertTrue(backend.values)
        self.assertGreaterEqual(len(backend.values), 3)

    def test_normal_read_requires_explicit_migration_without_mutating_sheet(self):
        backend = ValuesFake([["nombre", "correo"], ["Ana", "ana@example.com"]])
        repository = ParticipantRepository(backend)
        with self.assertRaisesRegex(RuntimeError, "migración explícita"):
            repository.load()
        self.assertEqual(backend.writes, [])
        repository.load_read_only()
        self.assertEqual(backend.writes, [])

    def test_docx_non_bytes_is_clean_invalid_result(self):
        result = DocxParser().parse(SourceArtifact("id", "file.docx"), "not-bytes", 1)
        self.assertFalse(result.valid)
        self.assertEqual((result.parser_name, result.artifact_type, result.channel, result.events),
                         ("docx", "transcript", "voice", []))
        self.assertIn("binario", result.errors[0])

    def test_sync_operational_errors_are_stderr_only(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["--no-notify"], runner_factory=lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
        self.assertEqual(code, 1)
        self.assertIn("ERROR:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
