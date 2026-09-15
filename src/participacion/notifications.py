from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class NotificationLevel(str, Enum):
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class DesktopNotification:
    title: str
    body: str
    level: NotificationLevel


class DesktopNotifier(Protocol):
    def is_available(self) -> bool: ...
    def notify(self, notification: DesktopNotification) -> bool: ...


class NotifySendNotifier:
    """Best-effort Fedora desktop notifications through libnotify."""

    def __init__(self, executable: str | None = None, timeout_seconds: float = 3.0):
        self.executable = executable or shutil.which("notify-send")
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        return bool(self.executable)

    def notify(self, notification: DesktopNotification) -> bool:
        if not self.is_available():
            logger.warning("notification unavailable: notify-send not found")
            return False
        urgency, icon, timeout_ms = {
            NotificationLevel.SUCCESS: ("normal", "dialog-information", 6000),
            NotificationLevel.WARNING: ("normal", "dialog-warning", 9000),
            NotificationLevel.CRITICAL: ("critical", "dialog-error", 12000),
        }[notification.level]
        command = [self.executable, "--urgency", urgency, "--expire-time", str(timeout_ms),
                   "--icon", icon, notification.title, notification.body]
        try:
            completed = subprocess.run(command, check=False, capture_output=True,
                                       text=True, timeout=self.timeout_seconds)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("notification failed: %s", exc)
            return False
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()
            logger.warning("notification failed: notify-send exit %s%s",
                           completed.returncode, f" ({detail})" if detail else "")
            return False
        return True


def build_program_notification(result: Any) -> DesktopNotification | None:
    """Build at most one concise notification from a final program result."""
    failed = int(getattr(result, "sessions_failed", 0))
    errors = tuple(getattr(result, "errors", ()))
    if failed or errors:
        body = (f"{failed} {'sesión' if failed == 1 else 'sesiones'} {'falló' if failed == 1 else 'fallaron'} · revisa los logs"
                if failed else f"{result.program_name} terminó con errores")
        return DesktopNotification("Error en participación", body, NotificationLevel.CRITICAL)

    needs_review = int(getattr(result, "sessions_needs_review", 0))
    incomplete = int(getattr(result, "sessions_incomplete", 0))
    attention = needs_review + incomplete
    if attention:
        if attention == 1:
            session_results = getattr(result, "session_results", ())
            status = next((getattr(item, "status", None) for item in session_results
                           if getattr(getattr(item, "status", None), "value", getattr(item, "status", None))
                           in {"INCOMPLETE", "NEEDS_REVIEW"}), None)
            if getattr(status, "value", status) == "NEEDS_REVIEW":
                body = "1 sesión requiere revisión de identidad"
            else:
                body = "1 sesión incompleta · falta transcript o chat válido"
        else:
            body = f"{attention} sesiones requieren atención"
        return DesktopNotification("Participación requiere atención", body, NotificationLevel.WARNING)

    critical_transitions = int(getattr(result, "follow_up_critical_transitions", 0))
    if critical_transitions:
        return DesktopNotification(
            "Seguimiento requiere atención",
            f"{critical_transitions} estudiante{'s' if critical_transitions != 1 else ''} {'pasaron' if critical_transitions != 1 else 'pasó'} a Crítico",
            NotificationLevel.WARNING,
        )

    changed = (int(getattr(result, "session_results_changed", 0)) > 0
               or int(getattr(result, "participants_created", 0)) > 0
               or bool(getattr(result, "ranking_changed", False))
               or bool(getattr(result, "tracking_changed", False)))
    if not changed:
        return None

    parts: list[str] = []
    processed = int(getattr(result, "sessions_processed", 0))
    created = int(getattr(result, "participants_created", 0))
    if processed:
        parts.append(f"{processed} {'sesión' if processed == 1 else 'sesiones'} {'procesada' if processed == 1 else 'procesadas'}")
    if created:
        parts.append(f"{created} participante{'s' if created != 1 else ''} nuevo{'s' if created != 1 else ''}")
    if getattr(result, "ranking_changed", False):
        parts.append("Ranking actualizado")
    elif getattr(result, "tracking_changed", False):
        parts.append("Seguimiento actualizado")
    return DesktopNotification("Participación actualizada", " · ".join(parts), NotificationLevel.SUCCESS)


def notify_program_result(notifier: DesktopNotifier, result: Any) -> bool:
    notification = build_program_notification(result)
    if notification is None:
        return False
    try:
        return bool(notifier.notify(notification))
    except Exception as exc:  # notifier is deliberately best-effort
        logger.warning("notification failed: %s", exc)
        return False
