from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from ..adapters.google.bootstrap import get_google_services
from ..adapters.pilot.google_participation_source import ReadOnlyGoogleParticipationSource, build_participant_resolver
from ..application.attendance_identity import load_attendance_identity_policy
from ..adapters.tabular.xlsx_reader import read_xlsx_table
from ..application.pilot.config import PilotConfig
from ..application.pilot.outcomes import OutcomeMapping, map_outcomes
from ..application.pilot.runner import PilotRunner, PilotSources, RosterApplicabilityResolver
from ..application.pilot.results import RetrospectiveEvaluator
from ..core.participants import ParticipantResolver


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ejecuta el piloto Eira en modo read-only")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="No realiza escrituras externas")
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.error("eira-pilot requiere --dry-run; la persistencia aún no está habilitada")
    try:
        config = PilotConfig.load(str(args.config))
        raw = json.loads(args.config.expanduser().read_text(encoding="utf-8"))
        attendance_source = config.attendance_source
        attendance_path = _resolve_local_path(args.config, str(attendance_source.get("file", "")))
        if str(attendance_source.get("type", "")).casefold() != "xlsx":
            raise ValueError("El piloto actual requiere una fuente Attendance XLSX")
        _, sheets = get_google_services()
        statuses = raw.get("processing_statuses", {})
        if not isinstance(statuses, dict):
            raise ValueError("processing_statuses debe ser un objeto")
        google_source = ReadOnlyGoogleParticipationSource(sheets, config.google_sheet_id, statuses)
        participants = google_source.participants()
        participant_resolver: Any = build_participant_resolver(participants)
        if config.attendance_identity_path:
            identity_path = _resolve_local_path(args.config, config.attendance_identity_path)
            participant_resolver = load_attendance_identity_policy(identity_path, participant_resolver)
        session_orders = {item.session_id: item.session_order for item in config.session_mapping.values()}
        applicability = RosterApplicabilityResolver(participants, session_orders)
        table = read_xlsx_table(attendance_path, sheet_name=str(attendance_source.get("sheet", "")))
        result = PilotRunner(
            config,
            PilotSources(google_source.scores, lambda: table, participant_resolver, applicability,
                         participant_ids=lambda: [item.participant_id for item in participants],
                         participation_statuses=google_source.statuses),
        ).run()
        report = _aggregate(result)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 1


def _aggregate(result: Any) -> dict[str, Any]:
    observation_counts = Counter(f"{item.dimension}:{item.status.value}" for item in result.observations)
    signal_counts = Counter(signal.signal_type for snapshot in result.snapshots for signal in snapshot.signal_set.signals)
    alert_counts = Counter(
        "INSUFFICIENT_DATA" if alert.evaluation_status.value == "INSUFFICIENT_DATA" else str(alert.level.value)
        for snapshot in result.snapshots for alert in snapshot.alerts
    )
    sufficient = {
        snapshot.as_of_session_id: sum(item.sufficient_data for item in snapshot.signal_set.evaluations)
        for snapshot in result.snapshots
    }
    return {
        "program_id": result.program_id,
        "observations": dict(sorted(observation_counts.items())),
        "issues": result.issue_counts(),
        "snapshots": {
            "count": result.snapshot_count,
            "first_session_id": result.first_session_id,
            "last_session_id": result.last_session_id,
            "participants_with_sufficient_evaluation_by_session": sufficient,
        },
        "signals": dict(sorted(signal_counts.items())),
        "alerts": dict(sorted(alert_counts.items())),
    }


def _retrospective(raw: dict[str, Any], config_path: Path, result: Any, participants: list[Any], attendance_path: Path) -> dict[str, Any]:
    raw_outcomes = raw.get("outcomes")
    if not isinstance(raw_outcomes, dict):
        return {"status": "NOT_CONFIGURED"}
    source = raw_outcomes.get("source", {})
    mapping_raw = raw_outcomes.get("mapping", {})
    outcome_path = _resolve_local_path(config_path, str(source.get("file", attendance_path)))
    table = read_xlsx_table(outcome_path, sheet_name=str(source.get("sheet", "")))
    records = [{"participant_id": p.participant_id, "nombre": p.nombre, "correo": p.correo,
                "aliases": p.aliases, "role": p.role} for p in participants]
    name_resolver = ParticipantResolver.official(records)
    outcomes, issues = map_outcomes(
        table,
        OutcomeMapping(str(mapping_raw["participant_column"]), str(mapping_raw["date_column"]), str(mapping_raw["type_column"])),
        lambda value: _outcome_resolution(name_resolver, value),
    )
    session_dates: dict[str, datetime] = {}
    for external_id, entry in PilotConfig.load(str(config_path)).session_mapping.items():
        parsed = _parse_external_date(external_id)
        if parsed is not None:
            session_dates[entry.session_id] = parsed
    report = RetrospectiveEvaluator().evaluate(result, outcomes, session_dates)
    return {
        "status": "EVALUATED",
        "mapping_issues": len(issues),
        "outcomes_evaluable": report.outcomes_evaluable,
        "cases_with_prior_signal": report.cases_with_prior_signal,
        "cases_with_prior_alert": report.cases_with_prior_alert,
        "cases_with_prior_observar": report.cases_with_prior_observar,
        "cases_with_prior_critical": report.cases_with_prior_critical,
        "cases_without_prior_signal": report.cases_without_prior_signal,
        "alerts_without_outcome": report.alerts_without_outcome,
        "insufficient_data_snapshots": report.insufficient_data_snapshots,
    }


def _outcome_resolution(resolver: ParticipantResolver, value: str) -> Any:
    return resolver.resolve(value)


def _resolve_local_path(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else config_path.parent / path


def _parse_external_date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.fromisoformat(value.split(" ", 1)[0])
        except ValueError:
            return None


if __name__ == "__main__":
    raise SystemExit(main())
