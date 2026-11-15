"""Thread-safe SQLite logger storing session metadata and text-only activity verdicts."""

from __future__ import annotations

from typing import Any

from app.models import ActivityContext, GoalProfile, JudgeVerdict, ModeType


class SessionLogger:
    """Thread-safe SQLite logger storing session metadata and text-only activity verdicts."""

    def __init__(self, db_path: str = "anti_distractor.db") -> None:
        # TODO(T5.1): Store db_path, initialize threading.Lock, and call init_db().
        self.db_path = db_path

    def init_db(self) -> None:
        """Create tables if they do not exist."""
        raise NotImplementedError("TODO(T5.1): Implement SessionLogger.init_db")

    def start_session(
        self,
        goal: str,
        mode: ModeType,
        duration_minutes: int,
        profile: GoalProfile,
    ) -> int:
        """Insert a new session row and return session_id."""
        raise NotImplementedError("TODO(T5.1): Implement SessionLogger.start_session")

    def log_event(
        self,
        session_id: int,
        ctx: ActivityContext,
        verdict: JudgeVerdict,
        action_taken: str,
        user_override: bool = False,
    ) -> int:
        """Persist a single evaluation event and return the row id."""
        raise NotImplementedError("TODO(T5.1): Implement SessionLogger.log_event")

    def mark_override(self, log_id: int) -> None:
        """Set user_override = 1 on an existing activity log row."""
        raise NotImplementedError("TODO(T5.1): Implement SessionLogger.mark_override")

    def finish_session(self, session_id: int, review_text: str) -> None:
        """Record ended_at timestamp and AI-generated session review."""
        raise NotImplementedError("TODO(T5.1): Implement SessionLogger.finish_session")

    def get_session_logs(self, session_id: int) -> list[dict[str, Any]]:
        """Fetch all activity log rows for a session ordered by timestamp."""
        raise NotImplementedError("TODO(T5.1): Implement SessionLogger.get_session_logs")
