import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.sync.models import DriveFile, FileChange, FileStatus, ProgramInspection, ProgramRecord, SessionInspection, SessionRecord
from src.sync.program_runner import FileStateStore, ProgramDependencies, ProgramRunner
from src.sync.ranking import build_program_ranking
from src.sync.session_processor import SessionProcessResult, SessionProcessStatus
from src.sync.state import empty_state, file_fingerprint, load_state
from src.sync.participants import Participant
from src.sync.scoring import ParticipantSessionScore
from decimal import Decimal


def program():
    sessions = tuple(SessionRecord(i, f"{i:02d} - Sesión {i}", f"s{i}") for i in range(1, 3))
    return ProgramRecord("Programa", "root", "url", 2, "auto", "sheet", sessions)


def inspection_for(p, fingerprints=("a", "b")):
    sessions = []
    for number, fingerprint in enumerate(fingerprints, 1):
        file = DriveFile(f"f{number}", f"file{number}")
        change = FileChange(file, FileStatus.NEW, fingerprint)
        sessions.append(SessionInspection(p.sessions[number - 1].__class__(number, f"{number:02d} - Sesión {number}", f"s{number}"), [file], [change]))
    return ProgramInspection(p, sessions)


class FakeProcessor:
    def __init__(self, outcomes=None, error=None):
        self.outcomes = outcomes or {}
        self.error = error
        self.calls = []
    def process(self, inspection):
        self.calls.append(inspection.session.session_number)
        if self.error:
            raise self.error
        return self.outcomes.get(inspection.session.session_number, SessionProcessResult(
            inspection.session.session_number, SessionProcessStatus.PROCESSED, changed=True))


class FakeParticipants:
    def __init__(self):
        self.values = [Participant("p1", "Ana", role="participant")]
    def load(self):
        return list(self.values)


class FakeSessionResults:
    def __init__(self):
        self.values = [ParticipantSessionScore(1, "p1", 1, 1, 0, 0, 0, Decimal("1"), True)]
    def load_scores(self):
        return list(self.values)


class FakeRanking:
    def __init__(self):
        self.calls = []
    def persist(self, ranking, participants):
        self.calls.append((ranking, list(participants)))
        return SimpleNamespace(status="NOOP")


class ProgramRunnerTests(unittest.TestCase):
    def make_runner(self, state, inspections, processor=None, ranking=None):
        p = program()
        processor = processor or FakeProcessor()
        participants = FakeParticipants()
        sessions = FakeSessionResults()
        ranking = ranking or FakeRanking()
        dependencies = ProgramDependencies(processor, participants, sessions, ranking)
        store = SimpleNamespace(value=state, saves=[])
        store.load = lambda: store.value
        def save(value):
            store.value = value
            store.saves.append(value)
        store.save = save
        discovery = lambda drive, programs, current_state, selected: [inspections]
        runner = ProgramRunner({"root": p}, object(), lambda pid, program: dependencies, store, discovery)
        return runner, processor, ranking, store

    def test_first_run_without_state_processes_sessions_and_marks_state(self):
        p = program()
        processor = FakeProcessor()
        runner, processor, ranking, store = self.make_runner(empty_state(), inspection_for(p), processor)
        result = runner.run()
        self.assertEqual(processor.calls, [1, 2])
        self.assertEqual(result[0].sessions_processed, 2)
        self.assertEqual(store.value["programs"]["root"]["sessions"]["s1"]["status"], "PROCESSED")
        self.assertEqual(store.value["programs"]["root"]["sessions"]["s2"]["status"], "PROCESSED")
        self.assertTrue(ranking.calls)

    def test_processed_and_incomplete_unchanged_are_skipped(self):
        p = program()
        state = {"version": 1, "programs": {"root": {"sessions": {
            "s1": {"status": "PROCESSED", "processing_version": 2, "files": {"f1": "a"}},
            "s2": {"status": "INCOMPLETE", "processing_version": 2, "files": {"f2": "b"}},
        }}}}
        runner, processor, _, _ = self.make_runner(state, inspection_for(p, ("a", "b")))
        result = runner.run()[0]
        self.assertEqual(processor.calls, [])
        self.assertEqual(result.sessions_skipped, 2)
        self.assertEqual([item.status for item in result.session_results], [SessionProcessStatus.SKIPPED] * 2)

    def test_legacy_processing_version_forces_reprocess(self):
        p = program()
        state = {"version": 1, "programs": {"root": {"sessions": {
            "s1": {"status": "PROCESSED", "files": {"f1": "a"}},
            "s2": {"status": "INCOMPLETE", "files": {"f2": "b"}},
        }}}}
        runner, processor, _, store = self.make_runner(state, inspection_for(p, ("a", "b")))
        result = runner.run()[0]
        self.assertEqual(result.sessions_processed, 2)
        self.assertEqual(processor.calls, [1, 2])
        self.assertEqual(store.value["programs"]["root"]["sessions"]["s1"]["processing_version"], 2)
    def test_failed_needs_review_processing_and_changed_fingerprint_retry(self):
        p = program()
        state = {"version": 1, "programs": {"root": {"sessions": {
            "s1": {"status": "FAILED", "files": {"f1": "a"}},
            "s2": {"status": "NEEDS_REVIEW", "files": {"f2": "b"}},
        }}}}
        runner, processor, _, _ = self.make_runner(state, inspection_for(p, ("a", "changed")))
        result = runner.run()[0]
        self.assertEqual(processor.calls, [1, 2])
        self.assertEqual(result.sessions_processed, 2)

    def test_processing_state_is_retried_and_unexpected_exception_propagates(self):
        p = program()
        state = {"version": 1, "programs": {"root": {"sessions": {
            "s1": {"status": "PROCESSING", "files": {"f1": "a"}},
        }}}}
        processor = FakeProcessor(error=AssertionError("bug"))
        runner, _, _, store = self.make_runner(state, inspection_for(p), processor)
        with self.assertRaises(AssertionError):
            runner.run()
        self.assertEqual(store.value["programs"]["root"]["sessions"]["s1"]["status"], "PROCESSING")

    def test_ranking_rebuilds_when_every_session_is_skipped(self):
        p = program()
        state = {"version": 1, "programs": {"root": {"sessions": {
            "s1": {"status": "PROCESSED", "processing_version": 2, "files": {"f1": "a"}},
            "s2": {"status": "INCOMPLETE", "processing_version": 2, "files": {"f2": "b"}},
        }}}}
        runner, _, ranking, _ = self.make_runner(state, inspection_for(p, ("a", "b")))
        result = runner.run()[0]
        self.assertEqual(result.sessions_skipped, 2)
        self.assertEqual(len(ranking.calls), 1)
        self.assertEqual(result.ranking_entries, 1)
        self.assertFalse(result.ranking_changed)

    def test_program_selection_accepts_id_or_unique_name(self):
        p = program()
        runner, _, _, _ = self.make_runner(empty_state(), inspection_for(p))
        self.assertEqual(len(runner.run("root")), 1)
        self.assertEqual(len(runner.run("Programa")), 1)
        with self.assertRaises(KeyError):
            runner.run("missing")

    def test_file_state_store_uses_atomic_state_module(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = FileStateStore(path)
            state = empty_state()
            store.save(state)
            self.assertEqual(store.load(), state)
            self.assertEqual(load_state(path), state)


if __name__ == "__main__":
    unittest.main()
