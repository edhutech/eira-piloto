from __future__ import annotations

import logging
import shutil
import subprocess

from ...application.notifications import Notification, NotificationLevel

logger = logging.getLogger(__name__)


class NotifySendNotifier:
    """Optional best-effort libnotify adapter."""

    def __init__(self, executable: str | None = None, timeout_seconds: float = 3.0):
        self.executable = executable or shutil.which("notify-send")
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        return bool(self.executable)

    def notify(self, notification: Notification) -> bool:
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
            logger.warning("notification failed: notify-send exit %s", completed.returncode)
            return False
        return True
