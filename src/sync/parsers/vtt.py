from __future__ import annotations

import re

from ..models import DriveFile, NormalizedEvent
from .base import ParseResult, classify_participant_label, result, text_content, timestamp_seconds


class VttParser:
    def can_parse(self, file_metadata: DriveFile) -> bool:
        return file_metadata.name.casefold().endswith(".vtt")

    def parse(self, file_metadata: DriveFile, content: str | bytes, session_number: int) -> ParseResult:
        blocks = re.split(r"\n\s*\n", text_content(content).replace("\r\n", "\n"))
        events: list[NormalizedEvent] = []
        timing = re.compile(r"^(\d{2}:\d{2}:\d{2}[\.,]\d{3})\s+-->\s+")
        for block_number, block in enumerate(blocks, 1):
            lines = [line.strip() for line in block.split("\n") if line.strip()]
            timing_line = next((line for line in lines if timing.match(line)), "")
            match = timing.match(timing_line)
            if not match:
                continue
            payload = lines[lines.index(timing_line) + 1:] if timing_line in lines else []
            text = "\n".join(payload)
            speaker_match = re.match(r"<v\s+([^>]+)>(.*?)</v>\s*$", text, re.S)
            if not speaker_match and ":" in text.split("\n", 1)[0]:
                name, first = text.split(":", 1)
                speaker_match = (name.strip(), first.strip() + ("\n" + "\n".join(text.split("\n")[1:]) if "\n" in text else ""))
            if not speaker_match:
                continue
            if isinstance(speaker_match, tuple):
                speaker, raw = speaker_match
            else:
                speaker, raw = speaker_match.group(1).strip(), speaker_match.group(2).strip()
            if not speaker or not raw:
                continue
            locator = f"block:{block_number}"
            identity_type, base_label = classify_participant_label(speaker)
            events.append(NormalizedEvent(session_number, speaker, "voice", match.group(1), timestamp_seconds(match.group(1)), raw, raw, file_metadata.file_id, locator, __import__("hashlib").sha256("\x1f".join((file_metadata.file_id, locator, speaker, raw)).encode()).hexdigest(), identity_type, base_label))
        return result("vtt", "transcript", "voice", events)
