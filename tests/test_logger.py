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
