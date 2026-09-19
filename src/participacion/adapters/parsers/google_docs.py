from __future__ import annotations

from ...core.models import EvidenceContext, SourceArtifact
from .base import (ParseResult, embedded_speaker_events, embedded_transcript_section,
                   parse_semantics, result, speaker_events, text_content)


class GoogleDocsParser:
    def can_parse(self, file_metadata: SourceArtifact) -> bool:
        return file_metadata.media_type == "application/vnd.google-apps.document"

    def parse(self, file_metadata: SourceArtifact, content: str | bytes, session_number: int,
              evidence_context: EvidenceContext | None = None) -> ParseResult:
        evidence_type, channel = parse_semantics(evidence_context, "transcript", "voice")
        text = text_content(content)
        embedded, _section = embedded_transcript_section(text)
        events = (embedded_speaker_events(text, file_metadata.artifact_id, session_number, channel)
                  if embedded else speaker_events(text, file_metadata.artifact_id, session_number, channel))
        if embedded and not events:
            return result("google_docs", evidence_type, channel, [], error="Transcript embebido sin intervenciones válidas")
        return result("google_docs", evidence_type, channel, events)
