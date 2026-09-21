from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Protocol, Sequence

from ..core.countability import COUNTABILITY_RULESET_VERSION, classify_event
from ..core.models import SourceArtifact, SessionInspection
from ..core.participants import Participant, ParticipantResolver, ResolutionStatus
from ..core.scoring import ParticipantSessionScore, score_events
from ..core.models import ParticipantSnapshot
from .ports.contracts import ContentReader, SessionResultsStore


class SessionProcessStatus(str, Enum):
    PROCESSED = "PROCESSED"
    INCOMPLETE = "INCOMPLETE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class SessionProcessingError(RuntimeError):
    """An expected operational failure at an injected component boundary."""


class EventResolver(Protocol):
    def resolve_event(self, event: Any) -> Any: ...


@dataclass(frozen=True)
class SessionProcessResult:
    """Outcome for one session.

    ``changed`` means that SessionResultsRepository confirmed a change in
    this session's persisted results. It excludes participant, sync-state,
    ranking, and any other external side effects.

    Participant creation is intentionally durable and independent from the
    session-results replacement. Therefore ``participants_created`` may be
    non-zero on ``FAILED`` while ``changed`` remains false.
    """

    session_number: int
    status: SessionProcessStatus
    changed: bool = False
    transcript_files: tuple[str, ...] = ()
    chat_files: tuple[str, ...] = ()
    human_events: int = 0
    system_events: int = 0
    discarded_metadata: int = 0
    participants_resolved: int = 0
    participants_created: int = 0
    participants_excluded_by_role: int = 0
    events_excluded_by_role: int = 0
    external_events_ignored: int = 0
    needs_review: int = 0
    voice_total: int = 0
    voice_valid: int = 0
    chat_total: int = 0
    chat_valid: int = 0
    ambiguous_total: int = 0
    score_total: Decimal = Decimal("0")
    result_rows: int = 0
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass
class SessionProcessor:
    """Orchestrate one complete session without owning sync state."""

    content_loader: ContentReader | Callable[[SourceArtifact], Any]
    parsers: Sequence[Any]
    participant_repository: Any
    session_results_repository: SessionResultsStore
    resolver_factory: Callable[[Iterable[dict[str, Any]]], EventResolver] | None = None

    def process(self, inspection: SessionInspection) -> SessionProcessResult:
        number = inspection.session.session_number
        warnings: list[str] = []
        errors: list[str] = []
        participants_created = 0
        try:
            parsed = self._parse(inspection, warnings)
            transcript_files = tuple(item[0].artifact_id for item in parsed if item[1].artifact_type == "transcript")
            chat_files = tuple(item[0].artifact_id for item in parsed if item[1].artifact_type == "chat")
            base: dict[str, Any] = dict(session_number=number, transcript_files=transcript_files,
                        chat_files=chat_files, warnings=tuple(warnings))
            if not transcript_files or not chat_files:
                return SessionProcessResult(status=SessionProcessStatus.INCOMPLETE, **base)

            events = [event for _, result in parsed for event in result.events]
            system_events = sum(getattr(event, "identity_type", "HUMAN") == "SYSTEM" for event in events)
            human = [event for event in events if getattr(event, "identity_type", "HUMAN") == "HUMAN"]
            discarded = sum(len(result.discarded_document_events) for _, result in parsed)
            base.update(human_events=len(human), system_events=system_events,
                        discarded_metadata=discarded)

            records = self._load_records()
            resolver = (self.resolver_factory or ParticipantResolver.auto)(records)
            resolved_events: list[Any] = []
            created: dict[str, Participant] = {}
            resolved_ids: set[str] = set()
            excluded_ids: set[str] = set()
            excluded_events = 0
            external_events_ignored = 0
            review_messages: list[str] = []
            for event in sorted(human, key=self._event_sort_key):
                resolution = resolver.resolve_event(event)
                resolution_status = getattr(resolution.status, "value", resolution.status)
                if resolution_status == "IGNORED":
                    if getattr(resolution, "reason", "") != "SYSTEM":
                        external_events_ignored += 1
                    continue
                if resolution.status is ResolutionStatus.NEEDS_REVIEW:
                    review_messages.append(f"Identidad requiere revisión: {event.participant_raw}")
                    continue
                if resolution.status is not ResolutionStatus.RESOLVED or resolution.participant is None:
                    continue
                participant = resolution.participant
                resolved_ids.add(participant.participant_id)
                if participant.participant_id not in {str(r.get("participant_id", "")).strip() for r in records}:
                    created[participant.participant_id] = participant
                if participant.role != "participant":
                    excluded_ids.add(participant.participant_id)
                    excluded_events += 1
                    continue
                resolved_events.append(SimpleNamespace(**vars(event), participant_id=participant.participant_id,
                                                       participant_email=participant.correo))
            if review_messages:
                return SessionProcessResult(status=SessionProcessStatus.NEEDS_REVIEW,
                    **base, participants_resolved=len(resolved_ids), needs_review=len(review_messages),
                    participants_excluded_by_role=len(excluded_ids), events_excluded_by_role=excluded_events,
                    external_events_ignored=external_events_ignored,
                    errors=tuple(review_messages))

            if created:
                self._operational_call(
                    self.participant_repository.upsert, list(created.values()),
                    label="persistencia de participantes nuevos")
                participants_created = len(created)
                records = list(records) + [{"participant_id": p.participant_id, "nombre": p.nombre,
                                             "correo": p.correo, "role": p.role} for p in created.values()]

            decisions = [classify_event(event) for event in resolved_events]
            decisions = [decision for decision in decisions if decision is not None]
            scores = self._operational_call(score_events, resolved_events, decisions,
                                            label="scoring")
            snapshots = {str(record["participant_id"]).strip(): ParticipantSnapshot(
                str(record["participant_id"]).strip(), str(record.get("nombre", "")), str(record.get("correo", "")))
                for record in records if str(record.get("participant_id", "")).strip()}
            self._validate_scores(number, scores, snapshots)
            write_result = self._operational_call(
                self.session_results_repository.replace_session,
                number, inspection.session.session_name, scores, snapshots,
                ruleset_version=COUNTABILITY_RULESET_VERSION,
                label="persistencia de resultados")
            status = str(getattr(write_result, "status", "REPLACE"))
            metrics = self._metrics(scores)
            return SessionProcessResult(status=SessionProcessStatus.PROCESSED,
                **base, participants_resolved=len(resolved_ids), participants_created=len(created),
                participants_excluded_by_role=len(excluded_ids), events_excluded_by_role=excluded_events,
                external_events_ignored=external_events_ignored,
                result_rows=len(scores), changed=status != "NOOP", **metrics)
        except SessionProcessingError as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            return SessionProcessResult(status=SessionProcessStatus.FAILED, session_number=number,
                                        participants_created=participants_created,
                                        warnings=tuple(warnings), errors=tuple(errors))

    def _parse(self, inspection: SessionInspection, warnings: list[str]) -> list[tuple[SourceArtifact, Any]]:
        parsed: list[tuple[SourceArtifact, Any]] = []
        for file in sorted(inspection.files, key=lambda item: (item.artifact_id, item.name)):
            parser = next((candidate for candidate in self.parsers
                           if self._operational_call(candidate.can_parse, file,
                                                     label="selección de parser")), None)
            if parser is None:
                warnings.append(f"Archivo no soportado omitido: {file.artifact_id}")
                continue
            loaded = self._operational_call(
                self.content_loader.read if hasattr(self.content_loader, "read") else self.content_loader,
                file, label="lectura de contenido")
            content = getattr(loaded, "content", loaded)
            evidence_context = inspection.evidence_contexts.get(file.artifact_id)
            parse_kwargs = ({"evidence_context": evidence_context}
                            if evidence_context is not None else {})
            result = self._operational_call(parser.parse, file, content,
                                            inspection.session.session_number,
                                            label="parseo de artefacto", **parse_kwargs)
            warnings.extend(result.warnings)
            expected_type = (evidence_context.evidence_type
                             if evidence_context is not None else result.artifact_type)
            if (result.valid and result.artifact_type == expected_type
                    and result.channel == ("voice" if expected_type == "transcript" else "chat")):
                parsed.append((file, result))
            else:
                warnings.extend(result.errors)
        return parsed

    def _load_records(self) -> list[dict[str, Any]]:
        if hasattr(self.participant_repository, "load_records"):
            return list(self._operational_call(self.participant_repository.load_records,
                                               label="lectura de participantes"))
        return [vars(participant) for participant in self._operational_call(
            self.participant_repository.load, label="lectura de participantes")]

    @staticmethod
    def _operational_call(callable_: Callable[..., Any], *args: Any,
                          label: str, **kwargs: Any) -> Any:
        try:
            return callable_(*args, **kwargs)
        except (OSError, RuntimeError, ValueError) as exc:
            raise SessionProcessingError(f"Fallo operativo en {label}: {exc}") from exc

    @staticmethod
    def _event_sort_key(event: Any) -> tuple[str, str, str]:
        return (str(getattr(event, "source_artifact_id", "")), str(getattr(event, "source_locator", "")), str(getattr(event, "event_id", "")))

    @staticmethod
    def _validate_scores(number: int, scores: Sequence[ParticipantSessionScore], snapshots: dict[str, ParticipantSnapshot]) -> None:
        ids: set[str] = set()
        for score in scores:
            if score.session_number != number or not score.participant_id or score.participant_id in ids:
                raise ValueError("Resultados inválidos o duplicados para la sesión")
            if score.participant_id not in snapshots:
                raise ValueError(f"Resultado sin participante: {score.participant_id}")
            ids.add(score.participant_id)

    @staticmethod
    def _metrics(scores: Sequence[ParticipantSessionScore]) -> dict[str, Any]:
        return {
            "voice_total": sum(item.voice_total for item in scores),
            "voice_valid": sum(item.voice_valid for item in scores),
            "chat_total": sum(item.chat_total for item in scores),
            "chat_valid": sum(item.chat_valid for item in scores),
            "ambiguous_total": sum(item.ambiguous_total for item in scores),
            "score_total": sum((item.score for item in scores), Decimal("0")),
        }
