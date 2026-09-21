from __future__ import annotations

import csv
from pathlib import Path
from typing import Mapping

from .modules.resolution import KnownExternalIdentity
from ..core.participants import strict_name_key


def load_known_external(path: str | Path) -> Mapping[str, KnownExternalIdentity]:
    """Load and validate an explicit known-external identity policy."""
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"known_external no existe: {source}")
    with source.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"observed_name", "reason", "provenance"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("known_external requiere observed_name, reason y provenance")
        result: dict[str, KnownExternalIdentity] = {}
        for row_number, row in enumerate(reader, start=2):
            observed = str(row.get("observed_name", "")).strip()
            reason = str(row.get("reason", "")).strip()
            provenance = str(row.get("provenance", "")).strip()
            if not observed:
                raise ValueError(f"known_external observed_name vacío en fila {row_number}")
            if not reason or not provenance:
                raise ValueError(f"known_external requiere reason y provenance en fila {row_number}")
            key = strict_name_key(observed)
            identity = KnownExternalIdentity(observed, reason, provenance)
            previous = result.get(key)
            if previous is not None and previous != identity:
                raise ValueError(f"known_external tiene conflicto para observed_name: {observed}")
            result[key] = identity
    return result
