from __future__ import annotations

import re

from ...core.models import EvidenceContext, SourceArtifact
from .base import (ParseResult, embedded_speaker_events, embedded_transcript_section,
                   parse_semantics, result, speaker_events, text_content, TIME_PATTERN,
                   _looks_like_speaker_label)


class GoogleDocsParser:
    def can_parse(self, file_metadata: SourceArtifact) -> bool:
        return file_metadata.media_type == "application/vnd.google-apps.document"

    def parse(self, file_metadata: SourceArtifact, content: str | bytes, session_number: int,
              evidence_context: EvidenceContext | None = None) -> ParseResult:
        evidence_type, channel = parse_semantics(evidence_context, "transcript", "voice")
        text = text_content(content)
        tab_sections = _tab_sections(text)
        if tab_sections:
            selected = _select_transcript_tab(tab_sections)
            if selected is None:
                return result("google_docs", evidence_type, channel, [],
                              error="Documento multi-tab sin Transcript estructurado")
            embedded_text = "📖 Transcripción\n" + selected
            events = embedded_speaker_events(
                embedded_text, file_metadata.artifact_id, session_number, channel
            )
            if not events:
                return result("google_docs", evidence_type, channel, [],
                              error="Transcript multi-tab sin intervenciones válidas")
            return result("google_docs", evidence_type, channel, events)
        embedded, _section = embedded_transcript_section(text)
        events = (embedded_speaker_events(text, file_metadata.artifact_id, session_number, channel)
                  if embedded else speaker_events(text, file_metadata.artifact_id, session_number, channel))
        if embedded and not events:
            return result("google_docs", evidence_type, channel, [], error="Transcript embebido sin intervenciones válidas")
        return result("google_docs", evidence_type, channel, events)


def _tab_sections(text: str) -> list[tuple[str, str]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: list[tuple[str, str]] = []
    title: str | None = None
    body: list[str] = []
    header = re.compile(r"^\[TAB: (.*)\]$")
    for line in lines:
        match = header.match(line.strip())
        if match:
            if title is not None:
                sections.append((title, "\n".join(body)))
            title, body = match.group(1), []
        elif title is not None:
            body.append(line)
    if title is not None:
        sections.append((title, "\n".join(body)))
    return sections


def _select_transcript_tab(sections: list[tuple[str, str]]) -> str | None:
    for title, body in sections:
        lines = body.splitlines()
        has_marker = any(line.strip() == "📖 Transcripción" for line in lines)
        has_timestamp = any(re.fullmatch(TIME_PATTERN, line.strip()) for line in lines)
        speaker_count = sum(
            bool(re.match(r"^[^:\n]{1,80}:\s*\S", line.strip()))
            and _looks_like_speaker_label(line.split(":", 1)[0])
            for line in lines
        )
        title_is_transcript = "transcript" in title.casefold() or "transcripci" in title.casefold()
        if (title_is_transcript or has_marker) and has_timestamp and speaker_count >= 2:
            return body
    return None
