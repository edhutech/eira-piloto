from __future__ import annotations

from ..models import DriveFile, NormalizedEvent
from .base import ParseResult, result, speaker_events, text_content


class TxtParser:
    def can_parse(self, file_metadata: DriveFile) -> bool:
        return file_metadata.name.casefold().endswith(".txt")

    def parse(self, file_metadata: DriveFile, content: str | bytes, session_number: int) -> ParseResult:
        events = speaker_events(text_content(content), file_metadata.file_id, session_number, "voice")
        return result("txt", "transcript", "voice", events, minimum_events=2)
