from __future__ import annotations

import csv
from pathlib import Path

from ...application.roster import RosterRecord, roster_records_from_rows


def read_csv_roster(path: str | Path) -> list[RosterRecord]:
    with Path(path).expanduser().open("r", encoding="utf-8-sig", newline="") as handle:
        return roster_records_from_rows(list(csv.reader(handle)))
