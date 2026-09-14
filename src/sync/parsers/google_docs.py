from __future__ import annotations

from ..models import DriveFile, NormalizedEvent
from .base import ParseResult, result, speaker_events, text_content


class GoogleDocsParser:
    def can_parse(self, file_metadata: DriveFile) -> bool:
        return file_metadata.mime_type == "application/vnd.google-apps.document"

    def parse(self, file_metadata: DriveFile, content: str | bytes, session_number: int) -> ParseResult:
        events = speaker_events(text_content(content), file_metadata.file_id, session_number, "voice")
        return result("google_docs", "transcript", "voice", events)
