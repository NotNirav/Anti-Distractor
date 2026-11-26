"""Unit tests for SQLite SessionLogger (Task 5.1)."""

from __future__ import annotations

import os
import tempfile
import time

from app.logger import SessionLogger
from app.models import ActivityContext, GoalProfile, JudgeVerdict


def test_session_logger_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_distractor.db")
        logger = SessionLogger(db_path=db_path)

        profile = GoalProfile(
            goal="Master Graph Algorithms",
            on_task_topics=["dfs", "bfs", "dijkstra"],
            allowed_domains=["leetcode.com"],
            borderline_domains=["youtube.com"],
            blocked_domains=["instagram.com"],
            allowed_apps=["code.exe"],
            blocked_apps=["spotify.exe"],
        )

        # 1. Start session
        session_id = logger.start_session(
            goal="Master Graph Algorithms",
            mode="strict",
            duration_minutes=25,
            profile=profile,
        )
        assert session_id > 0

        # 2. Log 3 events
        ctx1 = ActivityContext(
            trigger_type="tab_activated",
            goal="Master Graph Algorithms",
            foreground_window=None,
            tab_snapshot=None,
            open_windows=[],
            source_key="leetcode.com",
            display_title="Course Schedule - LeetCode",
            active_url="https://leetcode.com/problems/course-schedule",
            timestamp=1000.0,
        )
        v1 = JudgeVerdict(
            activity="Solving problem",
            on_task=True,
            confidence=0.98,
            reason="On-task algorithm study",
            tabs_to_close=[],
            tier_used=0,
        )
        log1_id = logger.log_event(session_id, ctx1, v1, action_taken="none")
        assert log1_id > 0

        ctx2 = ActivityContext(
            trigger_type="tab_updated",
            goal="Master Graph Algorithms",
            foreground_window=None,
            tab_snapshot=None,
            open_windows=[],
            source_key="youtube.com",
            display_title="Graph Tutorial - YouTube",
            active_url="https://youtube.com/watch?v=123",
            timestamp=1005.0,
        )
        v2 = JudgeVerdict(
            activity="Watching video",
            on_task=False,
            confidence=0.7,
            reason="Video content uncertain",
            tabs_to_close=[],
            tier_used=1,
        )
        log2_id = logger.log_event(session_id, ctx2, v2, action_taken="ask_popup")
        assert log2_id > log1_id

        ctx3 = ActivityContext(
            trigger_type="window_changed",
            goal="Master Graph Algorithms",
            foreground_window=None,
            tab_snapshot=None,
            open_windows=[],
            source_key="spotify.exe",
            display_title="Spotify Free",
            timestamp=1010.0,
        )
        v3 = JudgeVerdict(
            activity="Listening to music",
            on_task=False,
            confidence=1.0,
            reason="Blocked desktop music app",
            tabs_to_close=[],
            tier_used=0,
        )
        log3_id = logger.log_event(session_id, ctx3, v3, action_taken="strict_block")
        assert log3_id > log2_id

        # 3. Mark override on event 2
        logger.mark_override(log2_id)

        # 4. Finish session
        review_text = "Solid focus session on Graph Algorithms. 67% on-task."
        logger.finish_session(session_id, review_text)

        # 5. Fetch logs and verify all rows
        logs = logger.get_session_logs(session_id)
        assert len(logs) == 3

        # Row 1 check
        assert logs[0]["id"] == log1_id
        assert logs[0]["app_or_domain"] == "leetcode.com"
        assert logs[0]["on_task"] == 1
        assert logs[0]["user_override"] == 0

        # Row 2 check (override marked)
        assert logs[1]["id"] == log2_id
        assert logs[1]["app_or_domain"] == "youtube.com"
        assert logs[1]["on_task"] == 0
        assert logs[1]["user_override"] == 1
        assert logs[1]["action_taken"] == "ask_popup"

        # Row 3 check
        assert logs[2]["id"] == log3_id
        assert logs[2]["app_or_domain"] == "spotify.exe"
        assert logs[2]["on_task"] == 0
        assert logs[2]["user_override"] == 0
        assert logs[2]["action_taken"] == "strict_block"

        # Check session table directly
        with logger._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            s_row = dict(cursor.fetchone())
            assert s_row["goal"] == "Master Graph Algorithms"
            assert s_row["mode"] == "strict"
            assert s_row["ended_at"] is not None
            assert s_row["ended_at"] >= s_row["started_at"]
            assert s_row["review_text"] == review_text


def test_session_logger_model_used_and_migration():
    """Verify model_used column storage and ALTER TABLE migration for legacy databases."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "legacy.db")

        # Create legacy schema without model_used column
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                mode TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL,
                profile_json TEXT NOT NULL,
                review_text TEXT
            );
            CREATE TABLE activity_logs (
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
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
        """)
        conn.close()

        # Initializing SessionLogger on legacy DB runs migration
        logger = SessionLogger(db_path=db_path)

        profile = GoalProfile(
            goal="DSA",
            on_task_topics=["algo"],
            allowed_domains=["leetcode.com"],
            borderline_domains=[],
            blocked_domains=[],
            allowed_apps=[],
            blocked_apps=[],
        )
        sid = logger.start_session("DSA", "normal", 15, profile)

        ctx = ActivityContext(
            trigger_type="tab_activated",
            goal="DSA",
            foreground_window=None,
            tab_snapshot=None,
            open_windows=[],
            source_key="leetcode.com",
            display_title="LeetCode",
            active_url="https://leetcode.com",
            timestamp=100.0,
        )
        verdict = JudgeVerdict(
            activity="LeetCode",
            on_task=True,
            confidence=0.99,
            reason="On-task",
            tabs_to_close=[],
            tier_used=1,
            model_used="gemini-3.5-flash-lite",
        )
        logger.log_event(sid, ctx, verdict, action_taken="none")

        logs = logger.get_session_logs(sid)
        assert len(logs) == 1
        assert logs[0]["model_used"] == "gemini-3.5-flash-lite"


def test_session_logger_closed_tabs_streaks_and_analytics():
    """Verify closed_tabs_json persistence, list_sessions_summary, streak calculation, and top_distractions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "analytics.db")
        logger = SessionLogger(db_path=db_path)

        profile = GoalProfile(
            goal="Deep Work",
            on_task_topics=["rust", "systems"],
            allowed_domains=["doc.rust-lang.org"],
            borderline_domains=["youtube.com"],
            blocked_domains=["instagram.com"],
            allowed_apps=["code.exe"],
            blocked_apps=["steam.exe"],
        )

        # Anchor noon timestamps across 3 consecutive calendar days to avoid midnight edge drift
        from datetime import datetime, timedelta
        base_dt = datetime(2026, 6, 10, 12, 0, 0)
        day0_ts = (base_dt - timedelta(days=2)).timestamp()
        day1_ts = (base_dt - timedelta(days=1)).timestamp()
        day2_ts = base_dt.timestamp()

        def _insert_completed_session(start_ts: float, dur_mins: int, closed_tabs: list[dict] | None = None) -> int:
            sid = logger.start_session("Deep Work", "strict", dur_mins, profile)
            logger.finish_session(sid, f"Review at {start_ts}", closed_tabs=closed_tabs)
            with logger._get_connection() as conn:
                conn.execute(
                    "UPDATE sessions SET started_at = ?, ended_at = ? WHERE id = ?",
                    (start_ts, start_ts + dur_mins * 60.0, sid),
                )
            return sid

        sid0 = _insert_completed_session(day0_ts, 30)
        sid1 = _insert_completed_session(day1_ts, 60)
        vault_tabs = [
            {"title": "Reels", "url": "https://www.instagram.com/reels/", "domain": "instagram.com", "closed_at": day2_ts}
        ]
        sid2 = _insert_completed_session(day2_ts, 30, closed_tabs=vault_tabs)

        # Log events on sid2: 2 on-task, 2 non-overridden instagram distractions, 1 overridden youtube
        for i in range(2):
            ctx_ok = ActivityContext(
                trigger_type="tab_activated",
                goal="Deep Work",
                foreground_window=None,
                tab_snapshot=None,
                open_windows=[],
                source_key="doc.rust-lang.org",
                display_title="Rust Book",
                timestamp=day2_ts + i,
            )
            v_ok = JudgeVerdict("Rust", True, 0.99, "Allowed", [], 0)
            logger.log_event(sid2, ctx_ok, v_ok, "none")

        for i in range(2):
            ctx_bad = ActivityContext(
                trigger_type="tab_activated",
                goal="Deep Work",
                foreground_window=None,
                tab_snapshot=None,
                open_windows=[],
                source_key="instagram.com",
                display_title="Instagram",
                timestamp=day2_ts + 10 + i,
            )
            v_bad = JudgeVerdict("Social", False, 1.0, "Blocked", [1], 0)
            logger.log_event(sid2, ctx_bad, v_bad, "strict_block")

        ctx_ovr = ActivityContext(
            trigger_type="tab_activated",
            goal="Deep Work",
            foreground_window=None,
            tab_snapshot=None,
            open_windows=[],
            source_key="youtube.com",
            display_title="Rust Conf Talk",
            timestamp=day2_ts + 20,
        )
        v_ovr = JudgeVerdict("Video", False, 0.75, "Uncertain", [], 1)
        ovr_id = logger.log_event(sid2, ctx_ovr, v_ovr, "ask_popup")
        logger.mark_override(ovr_id)

        summaries = logger.list_sessions_summary(limit=10)
        assert len(summaries) == 3
        latest = summaries[0]
        assert latest["id"] == sid2
        assert latest["total_checks"] == 5
        assert latest["distractions_count"] == 2
        assert latest["overrides_count"] == 1
        assert latest["focus_score_pct"] == 60  # (2 on_task + 1 override) / 5 = 60%
        assert len(latest["closed_tabs"]) == 1
        assert latest["closed_tabs"][0]["domain"] == "instagram.com"

        stats = logger.get_overall_stats(reference_ts=day2_ts + 3600)
        assert stats["current_streak_days"] == 3
        assert stats["completed_sessions"] == 3
        assert stats["total_focus_hours"] == 2.0  # 30m + 60m + 30m = 2.0h
        assert stats["top_distractions"] == [("instagram.com", 2)]

        # Streak stays alive on the next day before completing that day's session, then resets if a full day is skipped
        stats_next_morning = logger.get_overall_stats(reference_ts=(base_dt + timedelta(days=1)).timestamp())
        assert stats_next_morning["current_streak_days"] == 3

        stats_missed_day = logger.get_overall_stats(reference_ts=(base_dt + timedelta(days=2)).timestamp())
        assert stats_missed_day["current_streak_days"] == 0
