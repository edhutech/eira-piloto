from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from ...application.modules.resolution import ParticipantResolverAdapter
from ...core.participants import Participant
from ...core.scoring import ParticipantSessionScore
from ..google.sheets.participants import GoogleSheetsValuesGateway, ParticipantRepository
from ..google.sheets.session_results import GoogleSheetsSessionResultsGateway


@dataclass
class ReadOnlyGoogleParticipationSource:
    sheets_service: Any
    spreadsheet_id: str
    processing_statuses: Mapping[str, str] | None = None

    def participants(self) -> list[Participant]:
        gateway = GoogleSheetsValuesGateway(self.sheets_service, self.spreadsheet_id, "Participantes")
        return ParticipantRepository(gateway).load_read_only()

    def scores(self) -> list[ParticipantSessionScore]:
        values = GoogleSheetsSessionResultsGateway(
            self.sheets_service, self.spreadsheet_id, worksheet_id=0, sheet_name="Sesiones"
        ).read_values()
        if not values:
            return []
        headers = [str(value).strip().casefold() for value in values[0]]
        required = {"session_number", "participant_id", "voice_total", "voice_valid", "chat_total",
                    "chat_valid", "ambiguous_total", "score", "scoring_complete"}
        missing = required.difference(headers)
        if missing:
            raise ValueError("La hoja Sesiones requiere columnas: " + ", ".join(sorted(missing)))
        positions = {header: index for index, header in enumerate(headers)}
        result: list[ParticipantSessionScore] = []
        for row_number, row in enumerate(values[1:], 2):
            padded = list(row) + [""] * (len(headers) - len(row))
            if not any(str(value).strip() for value in padded):
                continue
            try:
                result.append(ParticipantSessionScore(
                    session_number=_integer(padded[positions["session_number"]], row_number),
                    participant_id=str(padded[positions["participant_id"]]).strip(),
                    voice_total=_integer(padded[positions["voice_total"]], row_number),
                    voice_valid=_integer(padded[positions["voice_valid"]], row_number),
                    chat_total=_integer(padded[positions["chat_total"]], row_number),
                    chat_valid=_integer(padded[positions["chat_valid"]], row_number),
                    ambiguous_total=_integer(padded[positions["ambiguous_total"]], row_number),
                    score=Decimal(str(padded[positions["score"]]).strip()),
                    scoring_complete=_boolean(padded[positions["scoring_complete"]], row_number),
                ))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"Fila {row_number} inválida en Sesiones") from exc
        return result

    def statuses(self) -> Mapping[str, str]:
        if self.processing_statuses:
            return dict(self.processing_statuses)
        response = self.sheets_service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range="'Control'!A:ZZ",
        ).execute()
        values = response.get("values", [])
        if not values:
            return {}
        headers = [str(value).strip().casefold() for value in values[0]]
        required = {"session_number", "processing_status"}
        if not required.issubset(headers):
            raise ValueError("La hoja Control requiere session_number y processing_status")
        positions = {header: index for index, header in enumerate(headers)}
        result: dict[str, str] = {}
        for row_number, row in enumerate(values[1:], 2):
            padded = list(row) + [""] * (len(headers) - len(row))
            raw_number = str(padded[positions["session_number"]]).strip()
            if not raw_number:
                continue
            number = _integer(raw_number, row_number)
            status = str(padded[positions["processing_status"]]).strip().upper()
            if status:
                result[str(number)] = status
        return result


def build_participant_resolver(participants: list[Participant]) -> ParticipantResolverAdapter:
    records = [{
        "participant_id": item.participant_id,
        "nombre": item.nombre,
        "correo": item.correo,
        "aliases": item.aliases,
        "role": item.role,
        "source": item.source,
        "status": item.status,
        "enrollment_status": item.enrollment_status,
        "start_session": item.start_session,
        "end_session": item.end_session,
    } for item in participants]
    from ...core.participants import ParticipantResolver
    return ParticipantResolverAdapter(ParticipantResolver.official(records), match_email=True)


def _integer(value: Any, row_number: int) -> int:
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"Fila {row_number}: entero inválido") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"Fila {row_number}: entero inválido")
    return int(number)


def _boolean(value: Any, row_number: int) -> bool:
    text = str(value).strip().casefold()
    if text == "true":
        return True
    if text == "false":
        return False
    raise ValueError(f"Fila {row_number}: booleano inválido")
