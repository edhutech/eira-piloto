from __future__ import annotations

import re
import zipfile
from dataclasses import replace
from xml.etree import ElementTree as ET

from ...core.models import DiscardedDocumentEvent, EvidenceContext, SourceArtifact
from .base import ParseResult, parse_semantics, result, speaker_events


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
            paragraphs = []
            discarded = []
            for position, paragraph in enumerate(root.findall(".//{*}p"), 1):
                text = "".join(node.text or "" for node in paragraph.findall(".//{*}t"))
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
