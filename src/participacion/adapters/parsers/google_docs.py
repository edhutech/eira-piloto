from __future__ import annotations

from ...core.models import SourceArtifact
from .base import ParseResult, result, speaker_events, text_content


class GoogleDocsParser:
    def can_parse(self, file_metadata: SourceArtifact) -> bool:
        return file_metadata.media_type == "application/vnd.google-apps.document"

    def parse(self, file_metadata: SourceArtifact, content: str | bytes, session_number: int) -> ParseResult:
        events = speaker_events(text_content(content), file_metadata.artifact_id, session_number, "voice")
        return result("google_docs", "transcript", "voice", events)
