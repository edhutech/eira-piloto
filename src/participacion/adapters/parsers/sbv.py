from __future__ import annotations

import re

from ...core.models import EvidenceContext, SourceArtifact, NormalizedEvent
from .base import ParseResult, classify_participant_label, parse_semantics, result, text_content, timestamp_seconds


class SbvParser:
    def can_parse(self, file_metadata: SourceArtifact) -> bool:
        return file_metadata.name.casefold().endswith(".sbv")

    def parse(self, file_metadata: SourceArtifact, content: str | bytes, session_number: int,
              evidence_context: EvidenceContext | None = None) -> ParseResult:
        evidence_type, channel = parse_semantics(evidence_context, "chat", "chat")
        blocks = re.split(r"\n\s*\n", text_content(content).replace("\r\n", "\n"))
        events: list[NormalizedEvent] = []
        pattern = re.compile(r"^(?P<start>(?:\d+:)?\d{1,2}:\d{2}(?:[\.,]\d{1,3})?),")
        for block_number, block in enumerate(blocks, 1):
            lines = [line for line in block.split("\n") if line.strip()]
            if len(lines) < 2:
                continue
            match = pattern.match(lines[0].strip())
            speaker, first = (lines[1].split(":", 1) if ":" in lines[1] else ("", ""))
            if not match or not speaker.strip() or not first.strip():
                continue
            raw = "\n".join([first.strip(), *[line.strip() for line in lines[2:]]])
            locator = f"block:{block_number}"
            label = speaker.strip()
            identity_type, base_label = classify_participant_label(label)
            events.append(NormalizedEvent(session_number, label, channel, match.group("start"), timestamp_seconds(match.group("start")), raw, raw, file_metadata.artifact_id, locator, __import__("hashlib").sha256("\x1f".join((file_metadata.artifact_id, locator, label, raw)).encode()).hexdigest(), identity_type, base_label))
        return result("sbv", evidence_type, channel, events)
