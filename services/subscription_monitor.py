from __future__ import annotations

import threading
import logging
from datetime import date, timedelta
from typing import Callable

from subscription_sql import SUBSCRIPTIONS_EXPIRING_SQL


logger = logging.getLogger(__name__)


class SubscriptionMonitor:
    def __init__(
        self,
        config_provider: Callable[[], dict],
        connection_factory: Callable[[dict], object],
        notifier,
        history,
        on_complete: Callable[[dict], None] | None = None,
    ):
        self.config_provider = config_provider
        self.connection_factory = connection_factory
        self.notifier = notifier
        self.history = history
        self.on_complete = on_complete
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.manual_check_event = threading.Event()
        self.check_lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(
            target=self._run, name="SubscriptionMonitor", daemon=True
        )
        self.thread.start()

    def request_check(self) -> None:
        self.manual_check_event.set()
        self.wake_event.set()

    def stop(self, timeout: float = 8.0) -> None:
        self.stop_event.set()
        self.wake_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout)

    def _run(self) -> None:
        consecutive_failures = 0
        while not self.stop_event.is_set():
            config = dict(self.config_provider())
            manual_check = self.manual_check_event.is_set()
            self.manual_check_event.clear()
            interval = max(1, int(config.get("subscription_check_minutes", 60))) * 60
            wait_seconds = interval
            if config.get("subscription_notifications_enabled", True) or manual_check:
                result = self.check_once()
                if result.get("error"):
                    consecutive_failures += 1
                    logger.error("Subscription check failed: %s", result["error"])
                    # Avoid hammering an unavailable database. Manual checks
                    # still interrupt this wait immediately through wake_event.
                    wait_seconds = min(interval * (2 ** min(consecutive_failures, 3)),
                                       6 * 60 * 60)
                else:
                    consecutive_failures = 0
            self.wake_event.wait(wait_seconds)
            self.wake_event.clear()

    def check_once(self) -> dict:
        if not self.check_lock.acquire(blocking=False):
            return {"checked": 0, "notified": 0, "busy": True}
        result = {"checked": 0, "notified": 0, "busy": False, "error": None}
        try:
            config = dict(self.config_provider())
            if not config.get("database"):
                return result
            threshold = max(0, int(config.get("subscription_notify_days", 2)))
            today = date.today()
            params = {"today": today, "last_date": today + timedelta(days=threshold)}
            with self.connection_factory(config) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(SUBSCRIPTIONS_EXPIRING_SQL, params)
                    subscriptions = cursor.fetchall()
            result["checked"] = len(subscriptions)
            for subscription in subscriptions:
                if self.stop_event.is_set():
                    break
                expiry = subscription["expiry_date"]
                days_remaining = (expiry - today).days
                if not 0 <= days_remaining <= threshold:
                    continue
                identity = str(subscription["subscription_line_id"])
                expiry_key = expiry.isoformat()
                if self.history.was_sent(identity, expiry_key, days_remaining):
                    continue
                if self.notifier.show_subscription(subscription, days_remaining):
                    self.history.mark_sent(subscription, days_remaining)
                    result["notified"] += 1
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            self.check_lock.release()
            if self.on_complete:
                self.on_complete(result)
        return result
