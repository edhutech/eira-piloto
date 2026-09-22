from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, cast

from ..retry import execute_with_transient_retry
from ....application.pilot.operational import OperationalView


OPERATIONAL_HEADERS = [
    "participant_id", "Participante", "Email", "as_of_session",
    "evaluation_status", "alert_level", "signal_types", "signal_sessions",
    "explanation", "rule_versions",
]


@dataclass
class GoogleSheetsOperationalRepository:
    service: Any
    spreadsheet_id: str
    participant_repository: Any
    max_attempts: int = 3

    def persist(self, view: OperationalView) -> None:
        participants = {
            item.participant_id: item for item in self.participant_repository.load()
        }
        individual: list[list[Any]] = [OPERATIONAL_HEADERS]
        for case in sorted(view.cases, key=lambda item: item.participant_id):
            participant = participants.get(case.participant_id)
            signals = case.signals
            individual.append([
                case.participant_id,
                getattr(participant, "nombre", "") if participant else "",
                getattr(participant, "correo", "") if participant else "",
                case.as_of_session_id,
                case.evaluation_status,
                case.alert_level or "INSUFFICIENT_DATA",
                ", ".join(signal.signal_type for signal in signals),
                ", ".join(sorted({session for signal in signals for session in signal.session_ids})),
                _json(case.explanation),
                ", ".join(sorted({version for version in [case.rule_version, *(signal.rule_version for signal in signals)] if version})),
            ])
        summary = [["metric", "value", "as_of_session", "updated_at"],
                   *[[key, value, view.as_of_session_id, view.generated_at]
                     for key, value in view.summary().items()],
                   ["participants_evaluated", len(view.cases), view.as_of_session_id, view.generated_at],
                   ["attendance_observed_count", view.attendance_observed_count, view.as_of_session_id, view.generated_at],
                   *[[f"signal:{key}", value, view.as_of_session_id, view.generated_at]
                     for key, value in sorted(view.signal_counts.items())]]
        self._replace("Seguimiento", summary)
        self._replace("Seguimiento individual", individual)

    def _replace(self, sheet_name: str, values: list[list[Any]]) -> None:
        existing = self._read(sheet_name)
        self._execute(lambda: self.service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id, range=f"'{sheet_name}'!A:ZZ", body={}
        ).execute())
        self._execute(lambda: self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id, range=f"'{sheet_name}'!A1",
            valueInputOption="RAW", body={"values": values}
        ).execute())

    def _read(self, sheet_name: str) -> list[list[Any]]:
        response = self._execute(lambda: self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id, range=f"'{sheet_name}'!A:ZZ"
        ).execute())
        return cast(list[list[Any]], response.get("values", []))

    def _execute(self, operation: Any) -> Any:
        return execute_with_transient_retry(operation, self.max_attempts)


def _json(value: Mapping[str, object]) -> str:
    import json
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
