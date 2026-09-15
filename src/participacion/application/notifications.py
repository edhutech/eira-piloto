from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence

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


def build_program_notification(events: Sequence[ApplicationEvent]) -> Notification | None:
    """Map application events to one notification without inspecting run objects."""
    failed = next((event for event in events
                   if event.event_type in {"program.failed", "runtime.failed"}), None)
    if failed:
        count = int(failed.data.get("sessions_failed", 0))
        body = (f"{count} {'sesión' if count == 1 else 'sesiones'} "
                f"{'falló' if count == 1 else 'fallaron'} · revisa los logs"
                if count else f"{failed.program_name or 'participacion-sync'} terminó con errores")
        return Notification("Error en participación", body, NotificationLevel.CRITICAL)

    attention = next((event for event in events
                      if event.event_type == "program.requires_attention"), None)
    if attention:
        incomplete = int(attention.data.get("incomplete", 0))
        review = int(attention.data.get("needs_review", 0))
        total = incomplete + review
        if total == 1 and review:
            body = "1 sesión requiere revisión de identidad"
        elif total == 1:
            body = "1 sesión incompleta · falta transcript o chat válido"
        else:
            body = f"{total} sesiones requieren atención"
        return Notification("Participación requiere atención", body, NotificationLevel.WARNING)

    critical = next((event for event in events
                     if event.event_type == "follow_up.became_critical"), None)
    if critical:
        count = int(critical.data.get("count", 0))
        return Notification("Seguimiento requiere atención",
                            f"{count} estudiante{'s' if count != 1 else ''} "
                            f"{'pasaron' if count != 1 else 'pasó'} a Crítico",
                            NotificationLevel.WARNING)

    updated = next((event for event in events if event.event_type == "program.updated"), None)
    if not updated:
        return None
    processed = int(updated.data.get("sessions_processed", 0))
    created = int(updated.data.get("participants_created", 0))
    parts: list[str] = []
    if processed:
        parts.append(f"{processed} {'sesión' if processed == 1 else 'sesiones'} procesada{'s' if processed != 1 else ''}")
    if created:
        parts.append(f"{created} participante{'s' if created != 1 else ''} nuevo{'s' if created != 1 else ''}")
    if bool(updated.data.get("ranking_changed", False)):
        parts.append("Ranking actualizado")
    elif bool(updated.data.get("tracking_changed", False)):
        parts.append("Seguimiento actualizado")
    return Notification("Participación actualizada", " · ".join(parts), NotificationLevel.SUCCESS)


def notify_events(notifier: Notifier, events: Sequence[ApplicationEvent]) -> bool:
    notification = build_program_notification(events)
    if notification is None:
        return False
    try:
        return bool(notifier.notify(notification))
    except Exception as exc:
        logger.warning("notification failed: %s", exc)
        return False