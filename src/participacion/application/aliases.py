from __future__ import annotations

from typing import Any, Iterable

from ..core.participants import strict_name_key


def decode_aliases(cell: Any) -> list[str]:
    """Decode the canonical one-alias-per-line representation."""
    aliases: list[str] = []
    seen: set[str] = set()
    for value in str(cell or "").splitlines():
        alias = value.strip()
        key = strict_name_key(alias)
        if alias and key not in seen:
            aliases.append(alias)
            seen.add(key)
    return aliases


def encode_aliases(aliases: Iterable[str] | None) -> str:
    """Encode aliases without using comma as a delimiter."""
    encoded: list[str] = []
    seen: set[str] = set()
    for value in aliases or ():
        alias = str(value).strip()
        key = strict_name_key(alias)
        if alias and key not in seen:
            encoded.append(alias)
            seen.add(key)
    return "\n".join(encoded)
