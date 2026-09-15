from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any


class CountabilityStatus(str, Enum):
    COUNT = "COUNT"
    NO_COUNT = "NO_COUNT"
    AMBIGUOUS = "AMBIGUOUS"


COUNTABILITY_RULESET_VERSION = 1


@dataclass(frozen=True)
class CountabilityDecision:
    event_id: str
    status: CountabilityStatus
    reason_code: str
    rule_id: str
    ruleset_version: int


_EXACT_NO_COUNT_RULES = (
    ("GREETING_ONLY", {"hola", "buenas", "buen dia", "buenas tardes", "buenas noches"}),
    ("FAREWELL_ONLY", {"chau", "adios", "hasta luego", "nos vemos"}),
    ("THANKS_ONLY", {"gracias", "muchas gracias", "gracias a todos"}),
    ("ACKNOWLEDGEMENT_ONLY", {"ok", "okay", "dale", "si", "no", "de acuerdo", "perfecto", "listo", "correcto", "confirmado"}),
    ("TECHNICAL_ONLY", {"me escuchan", "se escucha", "no se escucha", "se corto", "se corto el audio", "problema tecnico"}),
)
_AUTOMATED_PATTERNS = (
    re.compile(r"^.+\s+se unio a la reunion$"),
    re.compile(r"^.+\s+se unio a la llamada$"),
    re.compile(r"^.+\s+(joined|left) the meeting$"),
    re.compile(r"^(?:la|el) transcripcion (?:comenzo|finalizo).*$"),
)
_REACTION_CHARS = set("👍👎👏🙌👌🙏❤️❤💯✅❌🙂😊😂😄😅😉🤔🎉🔥")


def classify_event(event: Any) -> CountabilityDecision | None:
    """Classify one resolved HUMAN event without semantic interpretation."""
    if getattr(event, "identity_type", "HUMAN") == "SYSTEM":
        return None
    event_id = str(getattr(event, "event_id", ""))
    text = str(getattr(event, "text", "") or "")
    key = _message_key(text)

    if getattr(event, "context_ambiguous", False):
        return _decision(event_id, CountabilityStatus.AMBIGUOUS, "AMBIGUOUS_CONTEXT", "ambiguous_context.v1")

    if not key or not _has_alphanumeric(text):
        return _decision(event_id, CountabilityStatus.NO_COUNT, "EMPTY_OR_NON_CONTENT", "countability.empty_or_non_content.v1")
    if _is_reaction_only(text):
        return _decision(event_id, CountabilityStatus.NO_COUNT, "REACTION_ONLY", "countability.reaction_only.v1")

    for reason_code, phrases in _EXACT_NO_COUNT_RULES:
        if key in phrases:
            return _decision(event_id, CountabilityStatus.NO_COUNT, reason_code, f"{reason_code.casefold()}.v1")
    if any(pattern.fullmatch(key) for pattern in _AUTOMATED_PATTERNS):
        return _decision(event_id, CountabilityStatus.NO_COUNT, "AUTOMATED_MESSAGE", "automated_message.v1")
    return _decision(event_id, CountabilityStatus.COUNT, "SUBSTANTIVE_CONTENT", "substantive_default.v1")


def _decision(event_id: str, status: CountabilityStatus, reason_code: str, rule_id: str) -> CountabilityDecision:
    return CountabilityDecision(event_id, status, reason_code, rule_id, COUNTABILITY_RULESET_VERSION)


def _message_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    normalized = re.sub(r"\s+", " ", without_marks).strip()
    return re.sub(r"^[¿¡]+|[?!.,;:]+$", "", normalized).strip()


def _has_alphanumeric(value: str) -> bool:
    return bool(re.search(r"[\w]", value, re.UNICODE))


def _is_reaction_only(value: str) -> bool:
    meaningful = "".join(char for char in value if not char.isspace() and unicodedata.category(char)[0] != "P")
    return bool(meaningful) and all(char in _REACTION_CHARS or 0x1F000 <= ord(char) <= 0x1FAFF for char in meaningful)
