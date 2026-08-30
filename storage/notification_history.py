from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path


class NotificationHistory:
    """Durable once-per-subscription-stage notification tracking."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS sent_notifications (
                        subscription_line_id TEXT NOT NULL,
                        expiry_date TEXT NOT NULL,
                        notification_stage INTEGER NOT NULL,
                        ticket_id TEXT,
                        customer_id TEXT,
                        sent_at TEXT NOT NULL,
                        PRIMARY KEY (
                            subscription_line_id,
                            expiry_date,
                            notification_stage
                        )
                    )
                """)

    def was_sent(self, subscription_line_id: str, expiry_date: str, stage: int) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute("""
                SELECT 1
                FROM sent_notifications
                WHERE subscription_line_id = ?
                  AND expiry_date = ?
                  AND notification_stage = ?
                LIMIT 1
            """, (subscription_line_id, expiry_date, stage)).fetchone()
        return row is not None

    def mark_sent(self, subscription: dict, stage: int) -> None:
        expiry_date = subscription["expiry_date"].isoformat()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("""
                    INSERT OR IGNORE INTO sent_notifications (
                        subscription_line_id, expiry_date, notification_stage,
                        ticket_id, customer_id, sent_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    str(subscription["subscription_line_id"]),
                    expiry_date,
                    int(stage),
                    str(subscription.get("ticket_id") or ""),
                    str(subscription.get("customer_id") or ""),
                    datetime.now().isoformat(timespec="seconds"),
                ))
