import unittest

from participacion.adapters.parsers.google_docs import GoogleDocsParser
from participacion.core.models import SourceArtifact


class EmbeddedMeetTranscriptTests(unittest.TestCase):
    artifact = SourceArtifact("doc-1", "notes", "application/vnd.google-apps.document")

    def parse(self, text):
        return GoogleDocsParser().parse(self.artifact, text, 1)

    def test_embedded_transcript_ignores_summary_and_reuses_speakers(self):
        result = self.parse("""Resumen: no es una intervención
📖 Transcripción
2026-08-25
Título - Transcripción
00:00:10

Alice Smith: Hola
Bob Jones: Buenas

00:01:20

Alice Smith: Segunda intervención
La transcripción finalizó...
""")
        self.assertTrue(result.valid)
        self.assertEqual(len(result.events), 3)
        self.assertEqual({event.participant_raw for event in result.events}, {"Alice Smith", "Bob Jones"})
        self.assertEqual([event.timestamp_raw for event in result.events], ["00:00:10", "00:00:10", "00:01:20"])
        self.assertEqual(result.events[2].timestamp_seconds, 80.0)

    def test_two_speakers_share_timestamp(self):
        result = self.parse("📖 Transcripción\n00:00:10\nAlice Smith: A\nBob Jones: B\nLa transcripción finalizó...")
        self.assertEqual([event.timestamp_raw for event in result.events], ["00:00:10", "00:00:10"])

    def test_timestamp_changes_boundary(self):
        result = self.parse("📖 Transcripción\n00:00:10\nAlice Smith: A\ncontinúa\n00:01:20\nAlice Smith: B\nLa transcripción finalizó...")
        self.assertEqual(len(result.events), 2)
        self.assertEqual(result.events[0].text, "A\ncontinúa")
        self.assertEqual(result.events[1].timestamp_seconds, 80.0)

    def test_colon_inside_message_is_not_new_speaker(self):
        result = self.parse("📖 Transcripción\n00:00:10\nAlice Smith: URL: https://example.test/a\nLa transcripción finalizó...")
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].participant_raw, "Alice Smith")
        self.assertIn("URL: https://example.test/a", result.events[0].text)

    def test_presentation_is_system(self):
        result = self.parse("📖 Transcripción\n00:00:10\nAlice's Presentation: contenido\nBob Jones: Hola\nLa transcripción finalizó...")
        self.assertEqual([event.identity_type for event in result.events], ["SYSTEM", "HUMAN"])

    def test_document_without_marker_keeps_legacy_behavior(self):
        result = self.parse("Alice Smith: Hola\nBob Jones: Buenas")
        self.assertEqual([event.participant_raw for event in result.events], ["Alice Smith", "Bob Jones"])

    def test_marker_without_valid_events_fails_closed(self):
        result = self.parse("Resumen\n📖 Transcripción\nsolo metadatos\nLa transcripción finalizó...")
        self.assertFalse(result.valid)
        self.assertEqual(result.events, [])


if __name__ == "__main__":
    unittest.main()
