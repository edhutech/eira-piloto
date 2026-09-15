from __future__ import annotations

from typing import Any, Protocol

from ...application.roster import RosterRecord, roster_records_from_rows


class RosterSource(Protocol):
    def read_values(self) -> list[list[Any]]: ...


def read_google_roster(source: RosterSource) -> list[RosterRecord]:
    values = source.read_values()
    return [] if not values else roster_records_from_rows(values)
