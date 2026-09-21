from __future__ import annotations

from ...core.models import EvidenceContext, SourceArtifact
from .base import ParseResult, is_sbv_content, parse_semantics, result, sbv_events, speaker_events, text_content


class TxtParser:
    def can_parse(self, file_metadata: SourceArtifact) -> bool:
        return (file_metadata.name.casefold().endswith(".txt") or
                file_metadata.media_type == "text/plain")

    def parse(self, file_metadata: SourceArtifact, content: str | bytes, session_number: int,
              evidence_context: EvidenceContext | None = None) -> ParseResult:
        evidence_type, channel = parse_semantics(evidence_context, "transcript", "voice")
        if is_sbv_content(content):
            events = sbv_events(content, file_metadata.artifact_id, session_number, channel)
            return result("sbv", evidence_type, channel, events)
        events = speaker_events(text_content(content), file_metadata.artifact_id, session_number, channel)
        return result("txt", evidence_type, channel, events, minimum_events=2)
