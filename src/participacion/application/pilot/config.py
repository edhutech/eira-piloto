from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..external_data.mapping import ExternalDataMapping
from ..modules.models import SourceCoverage
from ...core.longitudinal import LongitudinalConfig


@dataclass(frozen=True)
class SessionMappingEntry:
    session_id: str
    session_order: int


@dataclass(frozen=True)
class PilotConfig:
    program_id: str
    program_name: str
    google_sheet_id: str
    attendance_source: Mapping[str, Any]
    attendance_mapping: ExternalDataMapping
    session_mapping: Mapping[str, SessionMappingEntry]
    longitudinal: LongitudinalConfig
    signal_version: str
    silence_enabled: bool
    silence_minimum_streak: int
    alert_version: str
    participation_coverage: SourceCoverage = SourceCoverage.UNKNOWN
    attendance_coverage: SourceCoverage = SourceCoverage.UNKNOWN

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PilotConfig":
        program = _mapping(raw.get("program"), "program")
        attendance = _mapping(raw.get("attendance"), "attendance")
        session_mapping = _mapping(raw.get("session_mapping"), "session_mapping")
        sessions: dict[str, SessionMappingEntry] = {}
        for external_id, value in session_mapping.items():
            item = _mapping(value, f"session_mapping.{external_id}")
            session_id = _text(item.get("session_id"), f"session_mapping.{external_id}.session_id")
            order = item.get("session_order")
            if isinstance(order, bool) or not isinstance(order, int) or order < 1:
                raise ValueError(f"session_mapping.{external_id}.session_order inválido")
            sessions[str(external_id)] = SessionMappingEntry(session_id, order)
        if not sessions or len({item.session_order for item in sessions.values()}) != len(sessions):
            raise ValueError("session_mapping debe contener órdenes únicos y no vacío")
        longitudinal_raw = _mapping(raw.get("longitudinal"), "longitudinal")
        longitudinal = LongitudinalConfig(
            minimum_observations=int(longitudinal_raw.get("minimum_observations", 1)),
            recent_window_size=int(longitudinal_raw.get("recent_window_size", 1)),
            trend_threshold=float(longitudinal_raw.get("trend_threshold", 0)),
        )
        signals = _mapping(raw.get("signals"), "signals")
        silence = _mapping(signals.get("participation_silence_streak", {}), "signals.participation_silence_streak")
        minimum_streak = int(silence.get("minimum_streak", 2))
        if minimum_streak < 1:
            raise ValueError("minimum_streak debe ser positivo")
        return cls(
            program_id=_text(program.get("program_id"), "program.program_id"),
            program_name=_text(program.get("program_name"), "program.program_name"),
            google_sheet_id=_text(program.get("sheet_id"), "program.sheet_id"),
            attendance_source=_mapping(attendance.get("source"), "attendance.source"),
            attendance_mapping=ExternalDataMapping.from_dict(_mapping(attendance.get("mapping"), "attendance.mapping")),
            session_mapping=sessions,
            longitudinal=longitudinal,
            signal_version=_text(signals.get("version", "pilot.v1"), "signals.version"),
            silence_enabled=bool(silence.get("enabled", False)),
            silence_minimum_streak=minimum_streak,
            alert_version=_text(_mapping(raw.get("alerts"), "alerts").get("version", "pilot.v1"), "alerts.version"),
            participation_coverage=_coverage(_mapping(raw.get("participation"), "participation").get("coverage", "UNKNOWN")),
            attendance_coverage=_coverage(attendance.get("coverage", "UNKNOWN")),
        )

    @classmethod
    def load(cls, path: str) -> "PilotConfig":
        import json
        from pathlib import Path
        config_path = Path(path).expanduser()
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("La configuración del piloto debe ser un objeto JSON")
        session_mapping_file = raw.get("session_mapping_file")
        if session_mapping_file:
            mapping_path = Path(str(session_mapping_file)).expanduser()
            if not mapping_path.is_absolute():
                mapping_path = config_path.parent / mapping_path
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            if not isinstance(mapping, Mapping):
                raise ValueError("session_mapping_file debe contener un objeto JSON")
            raw = dict(raw)
            raw["session_mapping"] = mapping
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "program": {"program_id": self.program_id, "program_name": self.program_name, "sheet_id": self.google_sheet_id},
            "participation": {"source_id": f"google_sheets:{self.google_sheet_id}", "coverage": self.participation_coverage.value},
            "attendance": {"source": dict(self.attendance_source), "mapping": _mapping_to_dict(self.attendance_mapping), "coverage": self.attendance_coverage.value},
            "session_mapping": {key: {"session_id": value.session_id, "session_order": value.session_order} for key, value in self.session_mapping.items()},
            "longitudinal": {"minimum_observations": self.longitudinal.minimum_observations, "recent_window_size": self.longitudinal.recent_window_size, "trend_threshold": self.longitudinal.trend_threshold},
            "signals": {"version": self.signal_version, "participation_silence_streak": {"enabled": self.silence_enabled, "minimum_streak": self.silence_minimum_streak}},
            "alerts": {"version": self.alert_version, "silence_level": "OBSERVAR"},
        }


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} requiere un objeto")
    return value


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} requiere un valor")
    return result


def _coverage(value: Any) -> SourceCoverage:
    try:
        return SourceCoverage(str(value).upper())
    except ValueError as exc:
        raise ValueError(f"Cobertura inválida: {value}") from exc


def _mapping_to_dict(mapping: ExternalDataMapping) -> dict[str, Any]:
    def field(item: Any) -> dict[str, Any]:
        return {"column": item.column, "type": item.value_type, "required": item.required}
    return {
        "participant_external_id": field(mapping.participant),
        "session_external_id": field(mapping.session),
        "metrics": {key: field(value) for key, value in mapping.metrics.items()},
    }
