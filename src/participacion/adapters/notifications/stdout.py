from ...application.notifications import Notification


class StdoutNotifier:
    def notify(self, notification: Notification) -> bool:
        print(f"[{notification.level.value}] {notification.title}: {notification.body}")
        return True
