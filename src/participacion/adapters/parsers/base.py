from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ...core.models import (Channel, DiscardedDocumentEvent, EvidenceContext, EvidenceType,
                            SourceArtifact, NormalizedEvent, channel_for_evidence_type)


@dataclass(frozen=True)
class ParseResult:
    valid: bool
    artifact_type: Literal["transcript", "chat"] | None
    channel: Literal["voice", "chat"] | None
    events: list[NormalizedEvent]
    parser_name: str
    warnings: list[str]
    errors: list[str]
    discarded_document_events: list[DiscardedDocumentEvent] = field(default_factory=list)


class Parser(Protocol):
    def can_parse(self, file_metadata: SourceArtifact) -> bool: ...
    def parse(self, file_metadata: SourceArtifact, content: str | bytes, session_number: int,
              evidence_context: EvidenceContext | None = None) -> ParseResult: ...


TIME_PATTERN = r"(?:\d+:)?\d{1,2}:\d{2}(?:[\.,]\d{1,3})?"
PRESENTATION_LABEL = re.compile(r"^(?P<base>.+?)[\u0027\u2019]s\s+Presentation\s*$", re.I)


def classify_participant_label(label: str) -> tuple[Literal["HUMAN", "SYSTEM"], str | None]:
    match = PRESENTATION_LABEL.fullmatch(label.strip())
    if match:
        return "SYSTEM", match.group("base").strip()
    return "HUMAN", None

def timestamp_seconds(value: str) -> float | None:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    try:
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    return None


def clean_text(value: str) -> str:
    return "\n".join(line.strip() for line in value.replace("\r", "").split("\n")).strip()


def event_id(artifact_id: str, source_locator: str, participant: str, raw_text: str) -> str:
    value = "\x1f".join((artifact_id, source_locator, participant, raw_text)).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def speaker_events(text: str, artifact_id: str, session_number: int, channel: Literal["voice", "chat"]) -> list[NormalizedEvent]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header = re.compile(rf"^(?:\[(?P<bracket>{TIME_PATTERN})\]\s*)?(?:(?P<plain>{TIME_PATTERN})\s+)?(?P<speaker>[^:\n]{{1,80}}):\s*(?P<text>.*)$")
    events: list[NormalizedEvent] = []
    current: dict[str, Any] | None = None

    def emit(value: dict[str, Any]) -> None:
        raw = clean_text("\n".join(value.pop("parts")))
        value["raw_text"] = raw
        value["text"] = raw
        value["identity_type"], value["participant_base_raw"] = classify_participant_label(value["participant_raw"])
        value["event_id"] = event_id(value["source_artifact_id"], value["source_locator"], value["participant_raw"], raw)
        events.append(NormalizedEvent(**value))

    for line_number, line in enumerate(lines, 1):
        match = header.match(line.strip())
        if match:
            speaker = match.group("speaker").strip()
            if not re.search(r"[^\W\d_]", speaker):
                continue
            if current:
                emit(current)
            raw_time = match.group("bracket") or match.group("plain")
            current = {
                "session_number": session_number,
                "participant_raw": speaker,
                "channel": channel,
                "timestamp_raw": raw_time,
                "timestamp_seconds": timestamp_seconds(raw_time) if raw_time else None,
                "raw_text": "",
                "text": "",
                "source_artifact_id": artifact_id,
                "source_locator": f"line:{line_number}",
                "parts": [match.group("text")],
            }
        elif current and line.strip():
            current["parts"].append(line)
    if current:
        emit(current)
    return events


def parse_semantics(evidence_context: EvidenceContext | None, legacy_type: EvidenceType,
                    legacy_channel: Channel) -> tuple[EvidenceType, Channel]:
    if evidence_context is None:
        return legacy_type, legacy_channel
    return evidence_context.evidence_type, channel_for_evidence_type(evidence_context.evidence_type)


def result(parser_name: str, artifact_type: EvidenceType, channel: Channel, events: list[NormalizedEvent], minimum_events: int = 1, error: str = "") -> ParseResult:
    valid = len(events) >= minimum_events
    return ParseResult(valid, artifact_type if valid else None, channel if valid else None, events if valid else [], parser_name, [], [] if valid else [error or "Estructura compatible insuficiente"])


def text_content(content: str | bytes) -> str:
    if isinstance(content, bytes):
        return content.decode("utf-8-sig")
    return content
