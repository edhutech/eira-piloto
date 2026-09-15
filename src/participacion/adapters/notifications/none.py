from ...application.notifications import Notification


class NoneNotifier:
    def notify(self, notification: Notification) -> bool:
        return True
