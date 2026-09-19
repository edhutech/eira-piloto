import io
import unittest
import zipfile

from participacion.adapters.parsers.docx import DocxParser
from participacion.core.models import SourceArtifact


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx_with_paragraphs(paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{value}</w:t></w:r></w:p>" for value in paragraphs)
    xml = f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return output.getvalue()


class EmbeddedDocxTranscriptTests(unittest.TestCase):
    artifact = SourceArtifact("docx-1", "notes.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    def parse(self, paragraphs):
        return DocxParser().parse(self.artifact, docx_with_paragraphs(paragraphs), 1)

    def test_summary_before_embedded_transcript_is_ignored(self):
        result = self.parse([
            "Resumen: no es una intervención",
            "📖 Transcripción",
            "Título - Transcripción",
            "00:00:10",
            "Alice Smith: Hola",
            "Bob Jones: Buenas",
            "00:01:20",
            "Alice Smith: Segunda: intervención",
            "continúa",
            "La transcripción finalizó...",
        ])
        self.assertTrue(result.valid)
        self.assertEqual(len(result.events), 3)
        self.assertEqual({event.participant_raw for event in result.events}, {"Alice Smith", "Bob Jones"})
        self.assertEqual([event.timestamp_raw for event in result.events], ["00:00:10", "00:00:10", "00:01:20"])
        self.assertEqual(result.events[2].text, "Segunda: intervención\ncontinúa")

    def test_presentation_is_system_and_speakers_repeat(self):
        result = self.parse([
            "📖 Transcripción", "00:00:10", "Alice Smith: Hola",
            "Alice's Presentation: slides", "Alice Smith: Otra", "La transcripción finalizó...",
        ])
        self.assertEqual(len(result.events), 3)
        self.assertEqual(sum(event.identity_type == "SYSTEM" for event in result.events), 1)
        self.assertEqual(sum(event.participant_raw == "Alice Smith" for event in result.events), 2)

    def test_legacy_docx_without_marker_keeps_current_behavior(self):
        result = self.parse(["Alice Smith: Hola", "Bob Jones: Buenas"])
        self.assertEqual([event.participant_raw for event in result.events], ["Alice Smith", "Bob Jones"])

    def test_marker_without_valid_events_fails_closed(self):
        result = self.parse(["Resumen", "📖 Transcripción", "solo metadatos", "La transcripción finalizó..."])
        self.assertFalse(result.valid)
        self.assertEqual(result.events, [])


if __name__ == "__main__":
    unittest.main()
