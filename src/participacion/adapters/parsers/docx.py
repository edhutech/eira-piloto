from __future__ import annotations

import re
import zipfile
from dataclasses import replace
from xml.etree import ElementTree as ET

from ...core.models import DiscardedDocumentEvent, EvidenceContext, SourceArtifact
from .base import (ParseResult, TIME_PATTERN, _looks_like_speaker_label,
                   embedded_speaker_events, parse_semantics, result, speaker_events)


class DocxParser:
    def can_parse(self, file_metadata: SourceArtifact) -> bool:
        return file_metadata.name.casefold().endswith(".docx")

    def parse(self, file_metadata: SourceArtifact, content: str | bytes, session_number: int,
              evidence_context: EvidenceContext | None = None) -> ParseResult:
        evidence_type, channel = parse_semantics(evidence_context, "transcript", "voice")
        if not isinstance(content, bytes):
            return ParseResult(False, evidence_type, channel, [], "docx", [],
                               ["DOCX requiere contenido binario"])
        try:
            with zipfile.ZipFile(__import__("io").BytesIO(content)) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
            raw_paragraphs = ["".join(node.text or "" for node in paragraph.findall(".//{*}t"))
                              for paragraph in root.findall(".//{*}p")]
            embedded = _embedded_docx_lines(raw_paragraphs)
            if embedded is not None:
                events = embedded_speaker_events("\n".join(embedded), file_metadata.artifact_id,
                                                 session_number, channel)
                if not events:
                    return result("docx", evidence_type, channel, [], error="Transcript embebido sin intervenciones válidas")
                return result("docx", evidence_type, channel, events)
            paragraphs = []
            discarded = []
            for position, (paragraph, text) in enumerate(zip(root.findall(".//{*}p"), raw_paragraphs), 1):
                ppr = paragraph.find("{*}pPr")
                style = ppr.find("{*}pStyle") if ppr is not None else None
                style_value = style.attrib.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val", "") if style is not None else ""
                has_bookmark = bool(
                    paragraph.findall("{*}bookmarkStart")
                    or paragraph.findall("{*}bookmarkEnd")
                )
                label = text.split(":", 1)[0].strip() if ":" in text else ""
                has_intervention_shape = bool(label) and bool(re.search(r"[^\W\d_]", label))
                is_heading = style_value.casefold().startswith("heading")
                is_document_metadata = is_heading or (has_bookmark and not has_intervention_shape)
                if is_document_metadata:
                    if label and bool(re.search(r"[^\W\d_]", label)):
                        discarded.append(DiscardedDocumentEvent(label, f"paragraph:{position}", "document_structure"))
                    paragraphs.append("")
                else:
                    paragraphs.append(text)
            events = speaker_events("\n".join(paragraphs), file_metadata.artifact_id, session_number, channel)
            return replace(result("docx", evidence_type, channel, events), discarded_document_events=discarded)
        except (KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
            return result("docx", evidence_type, channel, [], error=f"DOCX inválido: {exc}")


def _embedded_docx_lines(paragraphs: list[str]) -> list[str] | None:
    """Find an explicit transcript block without changing legacy DOCX parsing."""
    marker = next((index for index, line in enumerate(paragraphs)
                   if line.strip() == "📖 Transcripción"), None)
    if marker is not None:
        return ["📖 Transcripción", *paragraphs[marker + 1:]]
    # Some DOCX exports encode the book/icon marker outside w:t. The explicit
    # transcript closure plus timestamp/name structure is the remaining
    # deterministic boundary signal for that representation.
    close = next((index for index, line in enumerate(paragraphs)
                  if line.strip().casefold().startswith("la transcripción finalizó")), None)
    timestamps = [index for index, line in enumerate(paragraphs)
                  if re.fullmatch(TIME_PATTERN, line.strip())]
    if close is None or not timestamps:
        return None
    start = timestamps[0]
    has_speaker = any(
        ":" in line and _looks_like_speaker_label(line.split(":", 1)[0])
        for line in paragraphs[start + 1:close]
    )
    return ["📖 Transcripción", *paragraphs[start:close + 1]] if has_speaker else None
