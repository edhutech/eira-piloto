import unittest
from pathlib import Path

from participacion.adapters.google.content import ContentArtifact, content_hash, extract_google_doc_text
from participacion.core.models import SourceArtifact
from participacion.adapters.parsers.docx import DocxParser
from participacion.adapters.parsers.google_docs import GoogleDocsParser
from participacion.adapters.parsers.sbv import SbvParser
from participacion.adapters.parsers.txt import TxtParser
from participacion.adapters.parsers.vtt import VttParser

FIXTURES = Path(__file__).parent / "fixtures"


def file_meta(name, media_type="text/plain"):
    return SourceArtifact(artifact_id=f"id-{name}", name=name, media_type=media_type)


class ContentAndParserTests(unittest.TestCase):
    def test_google_doc_text_extraction_and_hash(self):
        payload = {"body": {"content": [
            {"paragraph": {"elements": [{"textRun": {"content": "Alice: Hola\n"}}]}},
            {"paragraph": {"elements": [{"textRun": {"content": "Bob: Adiós\n"}}]}},
        ]}}
        text = extract_google_doc_text(payload)
        self.assertEqual(text, "Alice: Hola\nBob: Adiós\n")
        self.assertEqual(len(content_hash(text)), 64)

    def test_google_docs_multiple_speakers_multiline_and_raw_text(self):
        content = (FIXTURES / "google_docs_transcript.txt").read_text()
        result = GoogleDocsParser().parse(file_meta("doc", "application/vnd.google-apps.document"), content, 1)
        self.assertTrue(result.valid)
        self.assertEqual(result.channel, "voice")
        self.assertEqual(len(result.events), 3)
        self.assertEqual(result.events[0].participant_raw, "Alice")
        self.assertEqual(result.events[1].raw_text, "Primera intervención.\ncontinuación en otra línea.")
        self.assertFalse(hasattr(result.events[0], "participant_id"))
        self.assertFalse(hasattr(result.events[0], "countability"))
        self.assertEqual(result.events[0].source_locator, "line:1")
        self.assertEqual(result.events[0].event_id, result.events[0].event_id)

    def test_google_docs_invalid(self):
        result = GoogleDocsParser().parse(file_meta("doc", "application/vnd.google-apps.document"), "texto arbitrario", 1)
        self.assertFalse(result.valid)

    def test_strong_google_docs_format_is_valid_with_one_event(self):
        result = GoogleDocsParser().parse(file_meta("one", "application/vnd.google-apps.document"), "Alice: Una intervención\n", 1)
        self.assertTrue(result.valid)
        self.assertEqual(result.artifact_type, "transcript")

    def test_txt_requires_two_structured_events(self):
        result = TxtParser().parse(file_meta("one.txt"), "Alice: Una intervención\n", 1)
        self.assertFalse(result.valid)

    def test_speaker_parser_rejects_numeric_labels(self):
        result = GoogleDocsParser().parse(
            file_meta("numeric", "application/vnd.google-apps.document"),
            "00: 03:13\nAlice: Intervención válida\nBob: Otra intervención\n",
            1,
        )
        self.assertTrue(result.valid)
        self.assertEqual([event.participant_raw for event in result.events], ["Alice", "Bob"])

    def test_presentation_label_is_system_for_any_base_name(self):
        parser = GoogleDocsParser()
        result = parser.parse(
            file_meta("presentation", "application/vnd.google-apps.document"),
            "Persona Uno's Presentation: Segmento\nPersona Dos's Presentation: Otro segmento\n",
            1,
        )
        self.assertTrue(result.valid)
        self.assertEqual([event.identity_type for event in result.events], ["SYSTEM", "SYSTEM"])
        self.assertEqual([event.participant_base_raw for event in result.events], ["Persona Uno", "Persona Dos"])

    def test_sbv_chat_with_timestamps_and_multiline(self):
        content = (FIXTURES / "chat.sbv").read_text()
        result = SbvParser().parse(file_meta("chat.sbv"), content, 2)
        self.assertTrue(result.valid)
        self.assertEqual(result.channel, "chat")
        self.assertEqual(len(result.events), 2)
        self.assertEqual(result.events[0].timestamp_raw, "0:00:01.000")
        self.assertEqual(result.events[0].timestamp_seconds, 1.0)
        self.assertIn("segunda línea", result.events[0].raw_text)

    def test_sbv_invalid(self):
        result = SbvParser().parse(file_meta("bad.sbv"), "no timestamp\ntexto", 1)
        self.assertFalse(result.valid)

    def test_vtt_speaker_timestamp(self):
        result = VttParser().parse(file_meta("transcript.vtt"), (FIXTURES / "transcript.vtt").read_text(), 3)
        self.assertTrue(result.valid)
        self.assertEqual(result.channel, "voice")
        self.assertEqual(result.events[0].participant_raw, "Alice")
        self.assertEqual(result.events[0].timestamp_seconds, 1.0)

    def test_vtt_without_speaker_is_invalid(self):
        content = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nMensaje sin speaker\n"
        result = VttParser().parse(file_meta("no-speaker.vtt"), content, 1)
        self.assertFalse(result.valid)

    def test_vtt_invalid_format(self):
        result = VttParser().parse(file_meta("bad.vtt"), "WEBVTT\nesto no es un bloque", 1)
        self.assertFalse(result.valid)

    def test_txt_recognizable_and_arbitrary(self):
        parser = TxtParser()
        good = parser.parse(file_meta("recognizable.txt"), (FIXTURES / "recognizable.txt").read_text(), 1)
        bad = parser.parse(file_meta("arbitrary.txt"), (FIXTURES / "arbitrary.txt").read_text(), 1)
        self.assertTrue(good.valid)
        self.assertEqual(good.channel, "voice")
        self.assertFalse(bad.valid)

    def test_docx_extraction_and_arbitrary_document(self):
        parser = DocxParser()
        good = parser.parse(file_meta("docx_transcript.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), (FIXTURES / "docx_transcript.docx").read_bytes(), 1)
        bad = parser.parse(file_meta("docx_arbitrary.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), (FIXTURES / "docx_arbitrary.docx").read_bytes(), 1)
        self.assertTrue(good.valid)
        self.assertEqual(len(good.events), 2)
        self.assertFalse(bad.valid)

    def test_docx_document_structure_is_discarded_not_event(self):
        parser = DocxParser()
        metadata = file_meta("docx_metadata.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        result = parser.parse(metadata, (FIXTURES / "docx_metadata.docx").read_bytes(), 1)
        self.assertTrue(result.valid)
        self.assertEqual([event.participant_raw for event in result.events], ["Alice", "Bob"])
        self.assertEqual(len(result.discarded_document_events), 1)
        self.assertEqual(result.discarded_document_events[0].source_locator, "paragraph:1")
        self.assertEqual(result.discarded_document_events[0].reason, "document_structure")

    def test_normalized_event_contract(self):
        result = TxtParser().parse(file_meta("recognizable.txt"), (FIXTURES / "recognizable.txt").read_text(), 7)
        event = result.events[0]
        self.assertIsInstance(event.session_number, int)
        self.assertEqual(event.source_artifact_id, "id-recognizable.txt")
        self.assertIn(event.channel, {"voice", "chat"})
        self.assertTrue(hasattr(event, "timestamp_raw"))
        self.assertTrue(hasattr(event, "timestamp_seconds"))
        self.assertFalse(hasattr(event, "participant_id"))
        self.assertFalse(hasattr(event, "countability"))

    def test_event_id_is_deterministic_and_locator_is_preserved(self):
        parser = GoogleDocsParser()
        metadata = file_meta("same", "application/vnd.google-apps.document")
        first = parser.parse(metadata, "Alice: Hola\n", 1).events[0]
        second = parser.parse(metadata, "Alice: Hola\n", 1).events[0]
        self.assertEqual(first.source_locator, "line:1")
        self.assertEqual(first.event_id, second.event_id)

    def test_content_artifact_preserves_metadata_and_kind(self):
        artifact = ContentArtifact(artifact=file_meta("doc"), content="texto", content_kind="text", content_hash=content_hash("texto"))
        self.assertEqual(artifact.artifact.artifact_id, "id-doc")
        self.assertEqual(artifact.content_kind, "text")


if __name__ == "__main__":
    unittest.main()
