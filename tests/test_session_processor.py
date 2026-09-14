import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from src.sync.models import DriveFile, SessionInspection, SessionRecord
from src.sync.parsers.base import ParseResult
from src.sync.participants import Participant, ParticipantResolver
from src.sync.session_processor import SessionProcessStatus, SessionProcessor



def event(event_id, name, channel="voice", text="contenido", identity_type="HUMAN"):
    return SimpleNamespace(
        event_id=event_id, participant_raw=name, channel=channel, text=text,
        identity_type=identity_type, session_number=1,
        source_file_id="file", source_locator=event_id,
    )


def parsed(artifact_type, channel, events, valid=True, discarded=()):
    return ParseResult(valid, artifact_type if valid else None, channel if valid else None,
                       list(events) if valid else [], "fake", [], [], list(discarded))


def inspection(files):
    return SessionInspection(SessionRecord(1, "01 - Sesión 1", "folder"), files=list(files), changes=[])


class FakeLoader:
    def __init__(self, contents):
        self.contents = contents
    def read(self, file):
        return SimpleNamespace(file=file, content=self.contents[file.file_id])


class FakeParser:
    def __init__(self, results):
        self.results = results
    def can_parse(self, file):
        return file.file_id in self.results
    def parse(self, file, content, session_number):
        return self.results[file.file_id]


class FakeParticipants:
    def __init__(self, values):
        self.values = list(values)
        self.upserted = []
    def load(self):
        return list(self.values)
    def load_records(self):
        return [{"participant_id": p.participant_id, "nombre": p.nombre,
                 "correo": p.correo, "aliases": p.aliases, "role": p.role}
                for p in self.values]
    def upsert(self, values):
        self.upserted.extend(values)
        self.values.extend(values)


class FakeResults:
    def __init__(self, status="REPLACE", existing=None):
        self.status = status
        self.existing = existing
        self.calls = []
    def replace_session(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(status=self.status, rows_written=len(args[2]))


class SessionProcessorTests(unittest.TestCase):
    def make_processor(self, files, parser_results, participants=(), result_status="REPLACE", resolver_factory=None):
        loader = FakeLoader({file.file_id: "content" for file in files})
        parser = FakeParser(parser_results)
        participant_repo = FakeParticipants(participants)
        result_repo = FakeResults(result_status)
        processor = SessionProcessor(
            content_loader=loader,
            parsers=[parser],
            participant_repository=participant_repo,
            session_results_repository=result_repo,
            resolver_factory=resolver_factory,
        )
        return processor, participant_repo, result_repo

    def test_valid_session_processes_and_persists(self):
        files = [DriveFile("voice", "voice.vtt"), DriveFile("chat", "chat.sbv")]
        parser_results = {
            "voice": parsed("transcript", "voice", [event("v1", "Ana")]),
            "chat": parsed("chat", "chat", [event("c1", "Ana", "chat")]),
        }
        processor, participants, results = self.make_processor(files, parser_results, [Participant("p1", "Ana")])
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.PROCESSED)
        self.assertTrue(output.changed)
        self.assertEqual((output.human_events, output.voice_total, output.chat_total), (2, 1, 1))
        self.assertEqual((output.voice_valid, output.chat_valid, output.score_total), (1, 1, Decimal("1.5")))
        self.assertEqual(output.result_rows, 1)
        self.assertEqual(len(results.calls), 1)
        self.assertEqual(results.calls[0][0][0:2], (1, "01 - Sesión 1"))

    def test_missing_transcript_or_chat_is_incomplete_without_persist(self):
        for artifact_type in ("transcript", "chat"):
            with self.subTest(missing=artifact_type):
                present = "chat" if artifact_type == "transcript" else "voice"
                file = DriveFile(present, present)
                parsed_result = parsed("chat" if present == "chat" else "transcript", present, [event("e", "Ana")])
                processor, _, results = self.make_processor([file], {present: parsed_result}, [Participant("p1", "Ana")])
                output = processor.process(inspection([file]))
                self.assertEqual(output.status, SessionProcessStatus.INCOMPLETE)
                self.assertFalse(output.changed)
                self.assertEqual(results.calls, [])

    def test_invalid_or_empty_artifacts_are_incomplete(self):
        files = [DriveFile("voice", "voice"), DriveFile("chat", "chat")]
        parser_results = {file.file_id: parsed("transcript" if file.file_id == "voice" else "chat", file.file_id, [], False) for file in files}
        processor, _, results = self.make_processor(files, parser_results, [Participant("p1", "Ana")])
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.INCOMPLETE)
        self.assertEqual(results.calls, [])

    def test_system_and_metadata_are_excluded(self):
        files = [DriveFile("voice", "voice"), DriveFile("chat", "chat")]
        discarded = [SimpleNamespace(reason="metadata")]
        parser_results = {
            "voice": parsed("transcript", "voice", [event("system", "System", identity_type="SYSTEM"), event("human", "Ana")], discarded=discarded),
            "chat": parsed("chat", "chat", [event("chat", "Ana", "chat")]),
        }
        processor, _, results = self.make_processor(files, parser_results, [Participant("p1", "Ana")])
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.PROCESSED)
        self.assertEqual((output.system_events, output.discarded_metadata, output.human_events), (1, 1, 2))
        scores = results.calls[0][0][2]
        self.assertEqual(sum(item.voice_total + item.chat_total for item in scores), 2)

    def test_facilitator_and_other_are_excluded_before_scoring(self):
        files = [DriveFile("voice", "voice"), DriveFile("chat", "chat")]
        parser_results = {
            "voice": parsed("transcript", "voice", [
                event("fac", "Facilitador"), event("other", "Otro"),
                event("screen", "Facilitador's Presentation", identity_type="SYSTEM"),
            ]),
            "chat": parsed("chat", "chat", []),
        }
        people = [Participant("f", "Facilitador", role="facilitator"), Participant("o", "Otro", role="other")]
        processor, _, results = self.make_processor(files, parser_results, people)
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.PROCESSED)
        self.assertEqual(output.participants_excluded_by_role, 2)
        self.assertEqual(output.events_excluded_by_role, 2)
        self.assertEqual(output.system_events, 1)
        self.assertEqual(results.calls[0][0][2], [])

    def test_existing_participant_is_not_created(self):
        files = [DriveFile("v", "v"), DriveFile("c", "c")]
        results_map = {"v": parsed("transcript", "voice", [event("v", "Ana")]), "c": parsed("chat", "chat", [event("c", "Ana", "chat")])}
        processor, participants, _ = self.make_processor(files, results_map, [Participant("p1", "Ana")])
        output = processor.process(inspection(files))
        self.assertEqual(output.participants_created, 0)
        self.assertEqual(participants.upserted, [])
        self.assertEqual(output.participants_resolved, 1)

    def test_clear_new_participant_is_created(self):
        files = [DriveFile("v", "v"), DriveFile("c", "c")]
        results_map = {"v": parsed("transcript", "voice", [event("v", "Nueva Persona")]), "c": parsed("chat", "chat", [event("c", "Nueva Persona", "chat")])}
        processor, participants, _ = self.make_processor(files, results_map)
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.PROCESSED)
        self.assertEqual(output.participants_created, 1)
        self.assertEqual(len(participants.upserted), 1)

    def test_needs_review_does_not_persist_results(self):
        people = [Participant("p1", "Ana"), Participant("p2", "Ana")]
        files = [DriveFile("v", "v"), DriveFile("c", "c")]
        results_map = {"v": parsed("transcript", "voice", [event("v", "Ana")]), "c": parsed("chat", "chat", [event("c", "Ana", "chat")])}
        processor, _, results = self.make_processor(files, results_map, people, resolver_factory=ParticipantResolver.imported)
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.NEEDS_REVIEW)
        self.assertFalse(output.changed)
        self.assertEqual(results.calls, [])
        self.assertGreater(output.needs_review, 0)

    def test_ambiguous_scoring_is_persisted_as_incomplete(self):
        files = [DriveFile("v", "v"), DriveFile("c", "c")]
        first = event("v", "Ana")
        first.context_ambiguous = True
        results_map = {"v": parsed("transcript", "voice", [first]), "c": parsed("chat", "chat", [event("c", "Ana", "chat")])}
        processor, _, results = self.make_processor(files, results_map, [Participant("p1", "Ana")])
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.PROCESSED)
        self.assertEqual(output.ambiguous_total, 1)
        self.assertFalse(results.calls[0][0][2][0].scoring_complete)

    def test_repository_noop_is_processed_and_unchanged(self):
        files = [DriveFile("v", "v"), DriveFile("c", "c")]
        results_map = {"v": parsed("transcript", "voice", [event("v", "Ana")]), "c": parsed("chat", "chat", [event("c", "Ana", "chat")])}
        processor, _, results = self.make_processor(files, results_map, [Participant("p1", "Ana")], result_status="NOOP")
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.PROCESSED)
        self.assertFalse(output.changed)

    def test_failure_before_persist_preserves_previous_results(self):
        files = [DriveFile("v", "v"), DriveFile("c", "c")]
        class FailingParser(FakeParser):
            def parse(self, file, content, session_number):
                raise RuntimeError("parser failure")
        loader = FakeLoader({"v": "x", "c": "x"})
        repo = FakeParticipants([Participant("p1", "Ana")])
        results = FakeResults()
        processor = SessionProcessor(loader, [FailingParser({"v": parsed("transcript", "voice", [])})], repo, results)
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.FAILED)
        self.assertEqual(results.calls, [])

    def test_persistence_failure_returns_failed_without_retrying_or_deleting(self):
        files = [DriveFile("v", "v"), DriveFile("c", "c")]
        results_map = {"v": parsed("transcript", "voice", [event("v", "Ana")]),
                       "c": parsed("chat", "chat", [event("c", "Ana", "chat")])}
        class FailingResults(FakeResults):
            def replace_session(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise RuntimeError("write failure")
        loader = FakeLoader({"v": "x", "c": "x"})
        participants = FakeParticipants([Participant("p1", "Ana")])
        results = FailingResults()
        processor = SessionProcessor(loader, [FakeParser(results_map)], participants, results)
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.FAILED)
        self.assertEqual(len(results.calls), 1)

    def test_known_operational_error_returns_failed(self):
        files = [DriveFile("v", "v"), DriveFile("c", "c")]
        parser = FakeParser({"v": parsed("transcript", "voice", [event("v", "Ana")])})
        class FailingLoader(FakeLoader):
            def read(self, file):
                raise OSError("temporary read failure")
        processor = SessionProcessor(FailingLoader({}), [parser], FakeParticipants([]), FakeResults())
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.FAILED)

    def test_unexpected_typeerror_keyerror_and_assertion_propagate(self):
        files = [DriveFile("v", "v")]
        for error in (TypeError("bug"), KeyError("bug"), AssertionError("invariant")):
            with self.subTest(error=type(error).__name__):
                class BrokenParser(FakeParser):
                    def can_parse(self, file):
                        raise error
                processor = SessionProcessor(FakeLoader({"v": "x"}), [BrokenParser({})], FakeParticipants([]), FakeResults())
                with self.assertRaises(type(error)):
                    processor.process(inspection(files))

    def test_new_participant_is_durable_when_scoring_fails_and_rerun_reuses_id(self):
        files = [DriveFile("v", "v"), DriveFile("c", "c")]
        results_map = {"v": parsed("transcript", "voice", [event("v", "Nueva Persona")]),
                       "c": parsed("chat", "chat", [event("c", "Nueva Persona", "chat")])}
        processor, participants, results = self.make_processor(files, results_map)
        with patch("src.sync.session_processor.score_events", side_effect=RuntimeError("scoring unavailable")):
            failed = processor.process(inspection(files))
        self.assertEqual(failed.status, SessionProcessStatus.FAILED)
        self.assertEqual(failed.participants_created, 1)
        self.assertFalse(failed.changed)
        self.assertEqual(results.calls, [])
        participant_id = participants.values[0].participant_id
        succeeded = processor.process(inspection(files))
        self.assertEqual(succeeded.status, SessionProcessStatus.PROCESSED)
        self.assertEqual(succeeded.participants_created, 0)
        self.assertEqual([p.participant_id for p in participants.values], [participant_id])

    def test_incomplete_keeps_previous_results(self):
        files = [DriveFile("v", "v")]
        processor, _, results = self.make_processor(files, {"v": parsed("transcript", "voice", [event("v", "Ana")])}, [Participant("p1", "Ana")])
        output = processor.process(inspection(files))
        self.assertEqual(output.status, SessionProcessStatus.INCOMPLETE)
        self.assertEqual(results.calls, [])

    def test_multiple_compatible_files_are_combined(self):
        files = [DriveFile("v1", "v1"), DriveFile("v2", "v2"), DriveFile("c", "c")]
        results_map = {
            "v1": parsed("transcript", "voice", [event("v1e", "Ana")]),
            "v2": parsed("transcript", "voice", [event("v2e", "Ana")]),
            "c": parsed("chat", "chat", [event("ce", "Ana", "chat")]),
        }
        processor, _, _ = self.make_processor(files, results_map, [Participant("p1", "Ana")])
        output = processor.process(inspection(files))
        self.assertEqual(output.human_events, 3)
        self.assertEqual(output.transcript_files, ("v1", "v2"))

    def test_file_order_does_not_change_functional_result(self):
        files = [DriveFile("v", "v"), DriveFile("c", "c")]
        results_map = {"v": parsed("transcript", "voice", [event("v", "Ana")]), "c": parsed("chat", "chat", [event("c", "Ana", "chat")])}
        p1, _, r1 = self.make_processor(files, results_map, [Participant("p1", "Ana")])
        p2, _, r2 = self.make_processor(list(reversed(files)), results_map, [Participant("p1", "Ana")])
        o1, o2 = p1.process(inspection(files)), p2.process(inspection(list(reversed(files))))
        self.assertEqual((o1.voice_total, o1.chat_total, o1.score_total), (o2.voice_total, o2.chat_total, o2.score_total))
        self.assertEqual(r1.calls[0][0][2][0].score, r2.calls[0][0][2][0].score)

    def test_no_ranking_or_sync_state_side_effects(self):
        files = [DriveFile("v", "v"), DriveFile("c", "c")]
        results_map = {"v": parsed("transcript", "voice", [event("v", "Ana")]), "c": parsed("chat", "chat", [event("c", "Ana", "chat")])}
        processor, _, results = self.make_processor(files, results_map, [Participant("p1", "Ana")])
        output = processor.process(inspection(files))
        self.assertFalse(hasattr(output, "ranking"))
        self.assertFalse(hasattr(processor, "sync_state"))
        self.assertEqual(len(results.calls), 1)


if __name__ == "__main__":
    unittest.main()
