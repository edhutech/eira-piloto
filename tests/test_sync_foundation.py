import tempfile
import unittest
from pathlib import Path

from src.sync.drive_discovery import discover_changes, select_programs
from src.sync.models import FileStatus
from src.sync.registry import load_programs
from src.sync.state import empty_state, file_fingerprint, load_state, save_state


PROGRAMS = {
    "root-1": {
        "program_name": "Programa 1",
        "folder_id": "root-1",
        "folder_url": "https://drive.google.com/drive/folders/root-1",
        "session_count": 2,
        "participant_mode": "auto",
        "sheet_id": "sheet-1",
        "sessions": [
            {"session_number": 1, "session_name": "01 - Sesión 1", "folder_id": "session-1"},
            {"session_number": 2, "session_name": "02 - Sesión 2", "folder_id": "session-2"},
        ],
    },
    "root-2": {
        "program_name": "Programa 2",
        "folder_id": "root-2",
        "folder_url": "https://drive.google.com/drive/folders/root-2",
        "session_count": 1,
        "participant_mode": "import",
        "sheet_id": "sheet-2",
        "sessions": [{"session_number": 1, "session_name": "01 - Sesión 1", "folder_id": "session-3"}],
    },
}


class FakeFiles:
    def __init__(self, files_by_folder):
        self.files_by_folder = files_by_folder

    def list(self, **kwargs):
        folder_id = kwargs["q"].split("'")[1]
        payload = {"files": self.files_by_folder.get(folder_id, [])}
        return FakeRequest(payload)


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeDrive:
    def __init__(self, files_by_folder):
        self._files = FakeFiles(files_by_folder)

    def files(self):
        return self._files


class SyncFoundationTests(unittest.TestCase):
    def test_load_programs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "programs.json"
            path.write_text(__import__("json").dumps(PROGRAMS), encoding="utf-8")
            programs = load_programs(path)
        self.assertEqual(programs["root-1"].program_name, "Programa 1")
        self.assertEqual(programs["root-1"].sessions[0].session_number, 1)

    def test_empty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = load_state(Path(directory) / "missing.json")
        self.assertEqual(state, empty_state())

    def test_state_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sync_state.json"
            state = {"version": 1, "programs": {"root-1": {"sessions": {}}}}
            save_state(path, state)
            self.assertEqual(load_state(path), state)

    def test_fingerprint_changes_when_metadata_changes(self):
        base = {"id": "f1", "modifiedTime": "2026-01-01T00:00:00Z", "size": "10"}
        self.assertEqual(file_fingerprint(base), file_fingerprint(dict(base)))
        changed = dict(base, modifiedTime="2026-01-02T00:00:00Z")
        self.assertNotEqual(file_fingerprint(base), file_fingerprint(changed))

    def test_new_file(self):
        drive = FakeDrive({"session-1": [{"id": "f1", "name": "voice.txt", "modifiedTime": "t1"}]})
        result = discover_changes(drive, load_programs_from_dict({"root-1": PROGRAMS["root-1"]}), empty_state())
        changes = result[0].sessions[0].changes
        self.assertEqual(changes[0].status, FileStatus.NEW)
        self.assertTrue(result[0].sessions[0].requires_processing)

    def test_modified_file(self):
        drive = FakeDrive({"session-1": [{"id": "f1", "name": "voice.txt", "modifiedTime": "t2"}]})
        state = state_with_file("root-1", "session-1", "f1", {"id": "f1", "modifiedTime": "t1"})
        result = discover_changes(drive, load_programs_from_dict({"root-1": PROGRAMS["root-1"]}), state)
        self.assertEqual(result[0].sessions[0].changes[0].status, FileStatus.MODIFIED)

    def test_unchanged_file(self):
        metadata = {"id": "f1", "name": "voice.txt", "modifiedTime": "t1"}
        drive = FakeDrive({"session-1": [metadata]})
        state = state_with_file("root-1", "session-1", "f1", metadata)
        result = discover_changes(drive, load_programs_from_dict({"root-1": PROGRAMS["root-1"]}), state)
        self.assertEqual(result[0].sessions[0].changes[0].status, FileStatus.UNCHANGED)
        self.assertFalse(result[0].sessions[0].requires_processing)

    def test_modified_session_does_not_affect_other_sessions(self):
        files = {
            "session-1": [{"id": "f1", "name": "voice.txt", "modifiedTime": "new"}],
            "session-2": [{"id": "f2", "name": "chat.txt", "modifiedTime": "same"}],
        }
        state = state_with_file("root-1", "session-1", "f1", {"id": "f1", "modifiedTime": "old"})
        state["programs"]["root-1"]["sessions"]["session-2"] = {"files": {"f2": file_fingerprint(files["session-2"][0])}}
        result = discover_changes(FakeDrive(files), load_programs_from_dict({"root-1": PROGRAMS["root-1"]}), state)
        self.assertTrue(result[0].sessions[0].requires_processing)
        self.assertFalse(result[0].sessions[1].requires_processing)

    def test_select_all_programs(self):
        programs = load_programs_from_dict(PROGRAMS)
        self.assertEqual([p.folder_id for p in select_programs(programs)], ["root-1", "root-2"])

    def test_select_one_program(self):
        programs = load_programs_from_dict(PROGRAMS)
        self.assertEqual([p.folder_id for p in select_programs(programs, "root-2")], ["root-2"])


def load_programs_from_dict(data):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "programs.json"
        path.write_text(__import__("json").dumps(data), encoding="utf-8")
        return load_programs(path)


def state_with_file(program_id, session_id, file_id, metadata):
    return {"version": 1, "programs": {program_id: {"sessions": {
        session_id: {"files": {file_id: file_fingerprint(metadata)}}
    }}}}


if __name__ == "__main__":
    unittest.main()
