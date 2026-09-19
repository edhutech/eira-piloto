from __future__ import annotations

from ...core.models import EvidenceContext, SourceArtifact
from .base import ParseResult, parse_semantics, result, speaker_events, text_content


class GoogleDocsParser:
    def can_parse(self, file_metadata: SourceArtifact) -> bool:
        return file_metadata.media_type == "application/vnd.google-apps.document"

    def parse(self, file_metadata: SourceArtifact, content: str | bytes, session_number: int,
              evidence_context: EvidenceContext | None = None) -> ParseResult:
        evidence_type, channel = parse_semantics(evidence_context, "transcript", "voice")
        events = speaker_events(text_content(content), file_metadata.artifact_id, session_number, channel)
        return result("google_docs", evidence_type, channel, events)
