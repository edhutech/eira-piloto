from __future__ import annotations

import re

from ..models import DriveFile, NormalizedEvent
from .base import ParseResult, classify_participant_label, result, text_content, timestamp_seconds


class SbvParser:
    def can_parse(self, file_metadata: DriveFile) -> bool:
        return file_metadata.name.casefold().endswith(".sbv")

    def parse(self, file_metadata: DriveFile, content: str | bytes, session_number: int) -> ParseResult:
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
            events.append(NormalizedEvent(session_number, label, "chat", match.group("start"), timestamp_seconds(match.group("start")), raw, raw, file_metadata.file_id, locator, __import__("hashlib").sha256("\x1f".join((file_metadata.file_id, locator, label, raw)).encode()).hexdigest(), identity_type, base_label))
        return result("sbv", "chat", "chat", events)
