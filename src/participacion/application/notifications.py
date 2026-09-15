from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, Sequence

from .events import ApplicationEvent

logger = logging.getLogger(__name__)


class NotificationLevel(str, Enum):
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    level: NotificationLevel


class Notifier(Protocol):
    def notify(self, notification: Notification) -> bool: ...


def build_program_notification(result: Any) -> Notification | None:
    failed = int(getattr(result, "sessions_failed", 0))
    errors = tuple(getattr(result, "errors", ()))
    if failed or errors:
        body = (f"{failed} {'sesión' if failed == 1 else 'sesiones'} "
                f"{'falló' if failed == 1 else 'fallaron'} · revisa los logs"
                if failed else f"{result.program_name} terminó con errores")
        return Notification("Error en participación", body, NotificationLevel.CRITICAL)
    attention = int(getattr(result, "sessions_needs_review", 0)) + int(getattr(result, "sessions_incomplete", 0))
    if attention:
        if attention == 1:
            statuses = {getattr(getattr(item, "status", None), "value", getattr(item, "status", None))
                        for item in getattr(result, "session_results", ())}
            if "NEEDS_REVIEW" in statuses:
                body = "1 sesión requiere revisión de identidad"
            else:
                body = "1 sesión incompleta · falta transcript o chat válido"
            return Notification("Participación requiere atención", body, NotificationLevel.WARNING)
        return Notification("Participación requiere atención",
                            f"{attention} sesiones requieren atención", NotificationLevel.WARNING)
    critical = next((event for event in getattr(result, "events", ())
                     if event.event_type == "follow_up.became_critical"), None)
    if critical:
        count = int(critical.data.get("count", 0))
        return Notification("Seguimiento requiere atención",
                            f"{count} estudiante{'s' if count != 1 else ''} "
                            f"{'pasaron' if count != 1 else 'pasó'} a Crítico",
                            NotificationLevel.WARNING)
    changed = (int(getattr(result, "session_results_changed", 0)) > 0
               or int(getattr(result, "participants_created", 0)) > 0
               or bool(getattr(result, "ranking_changed", False))
               or bool(getattr(result, "tracking_changed", False)))
    if not changed:
        return None
    processed = int(getattr(result, "sessions_processed", 0))
    created = int(getattr(result, "participants_created", 0))
    parts = []
    if processed:
        parts.append(f"{processed} {'sesión' if processed == 1 else 'sesiones'} procesada{'s' if processed != 1 else ''}")
    if created:
        parts.append(f"{created} participante{'s' if created != 1 else ''} nuevo{'s' if created != 1 else ''}")
    if getattr(result, "ranking_changed", False):
        parts.append("Ranking actualizado")
    elif getattr(result, "tracking_changed", False):
        parts.append("Seguimiento actualizado")
    return Notification("Participación actualizada", " · ".join(parts), NotificationLevel.SUCCESS)


def notify_program_result(notifier: Notifier, result: Any) -> bool:
    notification = build_program_notification(result)
    if notification is None:
        return False
    try:
        return bool(notifier.notify(notification))
    except Exception as exc:
        logger.warning("notification failed: %s", exc)
        return False
