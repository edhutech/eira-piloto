import unittest

from participacion.adapters.google.source import resolve_evidence_sources, resolve_evidence_sources_with_context
from participacion.core.models import EvidenceSourceRef


class FakeFiles:
    def __init__(self, by_folder, by_file):
        self.by_folder = by_folder
        self.by_file = by_file

    def list(self, **kwargs):
        folder_id = kwargs["q"].split("'")[1]
        return FakeRequest({"files": self.by_folder.get(folder_id, [])})

    def get(self, **kwargs):
        return FakeRequest(self.by_file[kwargs["fileId"]])


class FakeRequest:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeDrive:
    def __init__(self, by_folder, by_file):
        self._files = FakeFiles(by_folder, by_file)

    def files(self):
        return self._files


def artifact(identifier):
    return {"id": identifier, "name": identifier, "mimeType": "text/plain"}


class EvidenceSourceTests(unittest.TestCase):
    def test_legacy_container_and_new_artifact_sources_are_resolved_in_order(self):
        drive = FakeDrive({"folder": [artifact("f1"), artifact("f2")]}, {"f3": artifact("f3")})
        sources = (EvidenceSourceRef("google_drive", "container", "folder"),
                   EvidenceSourceRef("google_drive", "artifact", "f3"))
        self.assertEqual([item.artifact_id for item in resolve_evidence_sources(drive, sources)], ["f1", "f2", "f3"])

    def test_duplicate_artifact_is_kept_once_by_id(self):
        drive = FakeDrive({"folder": [artifact("f1")]}, {"f1": artifact("f1")})
        sources = (EvidenceSourceRef("google_drive", "artifact", "f1"),
                   EvidenceSourceRef("google_drive", "container", "folder"))
        self.assertEqual([item.artifact_id for item in resolve_evidence_sources(drive, sources)], ["f1"])

    def test_provider_error_is_not_silently_converted_to_empty_evidence(self):
        drive = FakeDrive({}, {})
        with self.assertRaises(KeyError):
            resolve_evidence_sources(drive, (EvidenceSourceRef("google_drive", "artifact", "missing"),))

    def test_same_artifact_and_same_context_is_deduplicated(self):
        drive = FakeDrive({"folder": [artifact("f1")]}, {"f1": artifact("f1")})
        sources = (EvidenceSourceRef("google_drive", "artifact", "f1", "chat"),
                   EvidenceSourceRef("google_drive", "container", "folder", "chat"))
        resolved = resolve_evidence_sources_with_context(drive, sources)
        self.assertEqual([item.artifact.artifact_id for item in resolved], ["f1"])
        self.assertEqual(resolved[0].evidence_context.evidence_type, "chat")

    def test_same_artifact_with_different_context_is_invalid(self):
        drive = FakeDrive({}, {"f1": artifact("f1")})
        sources = (EvidenceSourceRef("google_drive", "artifact", "f1", "transcript"),
                   EvidenceSourceRef("google_drive", "artifact", "f1", "chat"))
        with self.assertRaisesRegex(ValueError, "conflicto"):
            resolve_evidence_sources_with_context(drive, sources)


if __name__ == "__main__":
    unittest.main()