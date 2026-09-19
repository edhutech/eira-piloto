import unittest

from participacion.adapters.google.content import extract_google_doc_text
from participacion.adapters.parsers.google_docs import GoogleDocsParser
from participacion.core.models import SourceArtifact


def run(text: str):
    return GoogleDocsParser().parse(
        SourceArtifact("doc", "notes", "application/vnd.google-apps.document"),
        text,
        1,
    )


def doc_tabs(*tabs: dict) -> dict:
    return {"tabs": list(tabs)}


def tab(tab_id: str, title: str, text: str, children: list[dict] | None = None) -> dict:
    return {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {
            "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": text}}]}}]}
        },
        "childTabs": children or [],
    }


class GoogleDocsTabsTests(unittest.TestCase):
    def test_single_body_keeps_legacy_extraction(self):
        self.assertEqual(extract_google_doc_text({"body": {"textRun": {"content": "legacy\n"}}}), "legacy\n")

    def test_nested_tabs_preserve_order_and_do_not_duplicate_runs(self):
        payload = doc_tabs(
            tab("notes", "Notes", "summary\n", [tab("child", "Child", "child\n")]),
            tab("transcript", "Transcript", "00:00:01\nAlice Smith: Hi\n"),
        )
        text = extract_google_doc_text(payload)
        self.assertEqual(text.count("child\n"), 1)
        self.assertLess(text.index("summary"), text.index("child"))
        self.assertLess(text.index("child"), text.index("00:00:01"))

    def test_selects_transcript_tab_and_ignores_notes(self):
        text = "[TAB: Notes]\nSummary Person: not an event\n[TAB: Transcript]\n00:00:01\nAlice Smith: Hello\nBob Jones: Hi\n00:00:02\nAlice Smith: Again\nLa transcripción finalizó...\n"
        result = run(text)
        self.assertTrue(result.valid)
        self.assertEqual(len(result.events), 3)
        self.assertEqual({event.participant_raw for event in result.events}, {"Alice Smith", "Bob Jones"})

    def test_multi_tab_without_transcript_fails_closed(self):
        result = run("[TAB: Notes]\nSummary Person: not an event\n")
        self.assertFalse(result.valid)
        self.assertEqual(result.events, [])

    def test_marker_in_tab_is_structural_evidence(self):
        result = run("[TAB: Notes]\nSummary\n[TAB: Other]\n📖 Transcripción\n00:00:01\nAlice Smith: Hello\nBob Jones: Hi\nLa transcripción finalizó...\n")
        self.assertTrue(result.valid)
        self.assertEqual(len(result.events), 2)


if __name__ == "__main__":
    unittest.main()
