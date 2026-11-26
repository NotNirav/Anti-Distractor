"""Thread-safe SQLite logger storing session metadata, analytics, and text-only activity verdicts."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
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
        """Create tables and run safe schema migrations if needed."""
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
                    review_text TEXT,
                    closed_tabs_json TEXT NOT NULL DEFAULT '[]'
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
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN closed_tabs_json TEXT NOT NULL DEFAULT '[]'")
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
                INSERT INTO sessions (goal, mode, duration_minutes, started_at, profile_json, closed_tabs_json)
                VALUES (?, ?, ?, ?, ?, '[]')
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

    def finish_session(
        self,
        session_id: int,
        review_text: str,
        closed_tabs: list[dict[str, Any]] | None = None,
    ) -> None:
        """Record ended_at timestamp, AI-generated review, and optional closed-tabs vault JSON."""
        ended_at = time.time()
        closed_json = json.dumps(closed_tabs or [])
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET ended_at = ?, review_text = ?, closed_tabs_json = ?
                WHERE id = ?
                """,
                (ended_at, review_text, closed_json, session_id),
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

    def list_sessions_summary(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent sessions with aggregated focus score, distractions, overrides, and vault data."""
        lim = max(1, int(limit))
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    s.id,
                    s.goal,
                    s.mode,
                    s.duration_minutes,
                    s.started_at,
                    s.ended_at,
                    s.profile_json,
                    s.review_text,
                    s.closed_tabs_json,
                    COUNT(l.id) AS total_checks,
                    SUM(CASE WHEN l.on_task = 1 OR l.user_override = 1 THEN 1 ELSE 0 END) AS on_task_checks,
                    SUM(CASE WHEN l.on_task = 0 AND l.user_override = 0 THEN 1 ELSE 0 END) AS distractions_count,
                    SUM(CASE WHEN l.user_override = 1 THEN 1 ELSE 0 END) AS overrides_count
                FROM sessions s
                LEFT JOIN activity_logs l ON l.session_id = s.id
                GROUP BY s.id
                ORDER BY s.started_at DESC, s.id DESC
                LIMIT ?
                """,
                (lim,),
            )
            results: list[dict[str, Any]] = []
            for row in cursor.fetchall():
                d = dict(row)
                total = int(d.get("total_checks") or 0)
                on_cnt = int(d.get("on_task_checks") or 0)
                d["total_checks"] = total
                d["on_task_checks"] = on_cnt
                d["distractions_count"] = int(d.get("distractions_count") or 0)
                d["overrides_count"] = int(d.get("overrides_count") or 0)
                d["focus_score_pct"] = int(round((on_cnt / total) * 100)) if total > 0 else 100
                try:
                    d["closed_tabs"] = json.loads(d.get("closed_tabs_json") or "[]")
                except Exception:
                    d["closed_tabs"] = []
                try:
                    d["profile"] = json.loads(d.get("profile_json") or "{}")
                except Exception:
                    d["profile"] = {}
                results.append(d)
            return results

    def get_overall_stats(self, reference_ts: float | None = None) -> dict[str, Any]:
        """Compute daily focus streak, lifetime deep-work hours, average focus %, and top distractions."""
        ref_time = float(reference_ts) if reference_ts is not None else time.time()
        with self._lock, self._get_connection() as conn:
            sess_rows = conn.execute(
                """
                SELECT
                    s.id,
                    s.duration_minutes,
                    s.started_at,
                    s.ended_at,
                    COUNT(l.id) AS total_checks,
                    SUM(CASE WHEN l.on_task = 1 OR l.user_override = 1 THEN 1 ELSE 0 END) AS on_task_checks
                FROM sessions s
                LEFT JOIN activity_logs l ON l.session_id = s.id
                WHERE s.ended_at IS NOT NULL
                GROUP BY s.id
                ORDER BY s.started_at DESC
                """
            ).fetchall()

            dist_rows = conn.execute(
                """
                SELECT app_or_domain, COUNT(*) AS cnt
                FROM activity_logs
                WHERE on_task = 0 AND user_override = 0 AND TRIM(app_or_domain) != ''
                GROUP BY app_or_domain
                ORDER BY cnt DESC, app_or_domain ASC
                LIMIT 5
                """
            ).fetchall()

        completed_sessions = len(sess_rows)
        total_seconds = 0.0
        scores: list[int] = []
        active_dates = set()

        for r in sess_rows:
            started_at = float(r["started_at"] or 0.0)
            ended_at = float(r["ended_at"] or started_at)
            dur_cap = max(60.0, float(r["duration_minutes"] or 25) * 60.0)
            elapsed = max(0.0, min(ended_at - started_at, dur_cap))
            total_seconds += elapsed

            total_chk = int(r["total_checks"] or 0)
            on_chk = int(r["on_task_checks"] or 0)
            score = int(round((on_chk / total_chk) * 100)) if total_chk > 0 else 100
            scores.append(score)

            if started_at > 0:
                active_dates.add(datetime.fromtimestamp(started_at).date())

        today = datetime.fromtimestamp(ref_time).date()
        streak = 0
        cursor_day = today if today in active_dates else (today - timedelta(days=1))
        if cursor_day in active_dates:
            while cursor_day in active_dates:
                streak += 1
                cursor_day -= timedelta(days=1)

        avg_focus = int(round(sum(scores) / len(scores))) if scores else 0
        top_distractions = [(str(r["app_or_domain"]), int(r["cnt"])) for r in dist_rows]

        return {
            "current_streak_days": streak,
            "total_focus_hours": round(total_seconds / 3600.0, 2),
            "completed_sessions": completed_sessions,
            "avg_focus_score_pct": avg_focus,
            "top_distractions": top_distractions,
        }
