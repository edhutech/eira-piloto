from __future__ import annotations

from ...core.models import EvidenceContext, SourceArtifact
from .base import ParseResult, parse_semantics, result, sbv_events


class SbvParser:
    def can_parse(self, file_metadata: SourceArtifact) -> bool:
        return file_metadata.name.casefold().endswith(".sbv")

    def parse(self, file_metadata: SourceArtifact, content: str | bytes, session_number: int,
              evidence_context: EvidenceContext | None = None) -> ParseResult:
        evidence_type, channel = parse_semantics(evidence_context, "chat", "chat")
        return result("sbv", evidence_type, channel,
                      sbv_events(content, file_metadata.artifact_id, session_number, channel))
