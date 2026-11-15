"""Thread-safe SQLite logger storing session metadata and text-only activity verdicts."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
import sqlite3
import threading
import time
from typing import Any, Generator

from app.models import ActivityContext, GoalProfile, JudgeVerdict, ModeType


class SessionLogger:
    """Thread-safe SQLite logger storing session metadata and text-only activity verdicts."""

    def __init__(self, db_path: str = "anti_distractor.db") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self.init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create tables if they do not exist."""
        with self._lock, self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    profile_json TEXT NOT NULL,
                    review_text TEXT
                );

                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    trigger_type TEXT NOT NULL,
                    app_or_domain TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    tier_used INTEGER NOT NULL,
                    activity TEXT NOT NULL,
                    on_task INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL,
                    action_taken TEXT NOT NULL,
                    user_override INTEGER NOT NULL DEFAULT 0,
                    model_used TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
                """
            )
            try:
                conn.execute("ALTER TABLE activity_logs ADD COLUMN model_used TEXT DEFAULT ''")
            except Exception:
                pass

    def start_session(
        self,
        goal: str,
        mode: ModeType,
        duration_minutes: int,
        profile: GoalProfile,
    ) -> int:
        """Insert a new session row and return session_id."""
        started_at = time.time()
        profile_json = json.dumps(asdict(profile))
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions (goal, mode, duration_minutes, started_at, profile_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (goal, mode, duration_minutes, started_at, profile_json),
            )
            return int(cursor.lastrowid)

    def log_event(
        self,
        session_id: int,
        ctx: ActivityContext,
        verdict: JudgeVerdict,
        action_taken: str,
        user_override: bool = False,
    ) -> int:
        """Persist a single evaluation event and return the row id."""
        ts = ctx.timestamp if ctx.timestamp > 0 else time.time()
        app_or_domain = ctx.source_key or ""
        title = ctx.display_title or ""
        url = ctx.active_url or ""
        on_task_int = 1 if verdict.on_task else 0
        override_int = 1 if user_override else 0
        model_used = getattr(verdict, "model_used", "") or ""

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO activity_logs (
                    session_id, timestamp, trigger_type, app_or_domain, title,
                    url, tier_used, activity, on_task, confidence, reason,
                    action_taken, user_override, model_used
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    ts,
                    ctx.trigger_type,
                    app_or_domain,
                    title,
                    url,
                    verdict.tier_used,
                    verdict.activity or "",
                    on_task_int,
                    float(verdict.confidence),
                    verdict.reason or "",
                    action_taken,
                    override_int,
                    model_used,
                ),
            )
            return int(cursor.lastrowid)

    def mark_override(self, log_id: int) -> None:
        """Set user_override = 1 on an existing activity log row."""
        with self._lock, self._get_connection() as conn:
            conn.execute(
                "UPDATE activity_logs SET user_override = 1 WHERE id = ?",
                (log_id,),
            )

    def finish_session(self, session_id: int, review_text: str) -> None:
        """Record ended_at timestamp and AI-generated session review."""
        ended_at = time.time()
        with self._lock, self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ?, review_text = ? WHERE id = ?",
                (ended_at, review_text, session_id),
            )

    def get_session_logs(self, session_id: int) -> list[dict[str, Any]]:
        """Fetch all activity log rows for a session ordered by timestamp."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM activity_logs
                WHERE session_id = ?
                ORDER BY timestamp ASC, id ASC
                """,
                (session_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
