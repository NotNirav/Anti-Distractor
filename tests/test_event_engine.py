"""Unit and integration tests for evaluate_context (app/judge.py) and EventEngine (app/event_engine.py)."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from app.cache import VerdictCache
from app.config import TimingConfig
from app.event_engine import EventEngine
from app.judge import GeminiRateLimitError, evaluate_context
from app.models import (
    ActivityContext,
    GoalProfile,
    JudgeVerdict,
    TabInfo,
    TabSnapshot,
    WindowInfo,
)
from app.server import TabSnapshotStore


def _make_profile() -> GoalProfile:
    return GoalProfile(
        goal="Study DSA",
        on_task_topics=["binary search trees", "dynamic programming", "leetcode"],
        allowed_domains=["leetcode.com", "github.com"],
        borderline_domains=["youtube.com", "reddit.com"],
        blocked_domains=["instagram.com", "tiktok.com"],
        allowed_apps=["code.exe", "windowsterminal.exe"],
        blocked_apps=["steam.exe", "discord.exe"],
    )


def _make_browser_ctx(
    domain: str,
    title: str,
    tab_id: int = 10,
    trigger_type: str = "tab_activated",
) -> ActivityContext:
    url = f"https://www.{domain}/watch?v=123"
    tab = TabInfo(
        tab_id=tab_id,
        window_id=1,
        title=title,
        url=url,
        domain=domain,
        active=True,
    )
    snap = TabSnapshot(tabs=[tab], active_tab_id=tab_id, window_focused=True, updated_at=time.time())
    win = WindowInfo(hwnd=100, title=title, process_name="chrome.exe", pid=1234, is_browser=True)
    return ActivityContext(
        trigger_type=trigger_type,  # type: ignore[arg-type]
        goal="Study DSA",
        foreground_window=win,
        tab_snapshot=snap,
        open_windows=[f"{title} (chrome.exe)"],
        source_key=domain,
        display_title=title,
        active_url=url,
        timestamp=time.time(),
    )


class TestEvaluateContext(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = _make_profile()
        self.cache = VerdictCache(ttl_seconds=1800.0)

    def test_tier0_decides_without_ai_call(self) -> None:
        ctx_blocked = _make_browser_ctx("instagram.com", "Instagram Reels", tab_id=77)
        ctx_allowed = _make_browser_ctx("leetcode.com", "Two Sum - LeetCode", tab_id=78)

        with (
            patch("app.judge.tier1_evaluate") as mock_t1,
            patch("app.judge.tier2_evaluate") as mock_t2,
            patch("app.screen.capture_screenshot_bytes") as mock_shot,
        ):
            v_blocked = evaluate_context(ctx_blocked, self.profile, self.cache, "fake-key")
            v_allowed = evaluate_context(ctx_allowed, self.profile, self.cache, "fake-key")

        self.assertEqual(v_blocked.tier_used, 0)
        self.assertFalse(v_blocked.on_task)
        self.assertEqual(v_blocked.tabs_to_close, [77])

        self.assertEqual(v_allowed.tier_used, 0)
        self.assertTrue(v_allowed.on_task)

        mock_t1.assert_not_called()
        mock_t2.assert_not_called()
        mock_shot.assert_not_called()

    def test_tier0_does_not_block_background_browser_tab_when_in_allowed_app(self) -> None:
        bg_tab = TabInfo(
            tab_id=99,
            window_id=1,
            title="Instagram",
            url="https://www.instagram.com/",
            domain="instagram.com",
            active=True,
        )
        snap = TabSnapshot(tabs=[bg_tab], active_tab_id=99, window_focused=False, updated_at=time.time())
        vscode_win = WindowInfo(
            hwnd=200,
            title="solution.py - Visual Studio Code",
            process_name="code.exe",
            pid=5555,
            is_browser=False,
        )
        ctx = ActivityContext(
            trigger_type="window_changed",
            goal="Study DSA",
            foreground_window=vscode_win,
            tab_snapshot=snap,
            open_windows=["solution.py (code.exe)"],
            source_key="code.exe",
            display_title="solution.py - Visual Studio Code",
            timestamp=time.time(),
        )

        with patch("app.judge.tier1_evaluate") as mock_t1:
            verdict = evaluate_context(ctx, self.profile, self.cache, "fake-key")

        self.assertEqual(verdict.tier_used, 0)
        self.assertTrue(verdict.on_task)
        mock_t1.assert_not_called()

    def test_tier1_confident_stops_at_tier1_and_caches(self) -> None:
        ctx = _make_browser_ctx("unknown-docs.org", "Dynamic Programming Intro")
        t1_verdict = JudgeVerdict(
            activity="Reading DP tutorial",
            on_task=True,
            confidence=0.90,
            reason="Topic is dynamic programming.",
            tabs_to_close=[],
            tier_used=1,
        )

        with (
            patch("app.judge.tier1_evaluate", return_value=t1_verdict) as mock_t1,
            patch("app.judge.tier2_evaluate") as mock_t2,
            patch("app.screen.capture_screenshot_bytes") as mock_shot,
        ):
            first = evaluate_context(ctx, self.profile, self.cache, "fake-key")
            second = evaluate_context(ctx, self.profile, self.cache, "fake-key")

        self.assertEqual(first.tier_used, 1)
        self.assertTrue(first.on_task)
        self.assertFalse(first.from_cache)

        self.assertTrue(second.from_cache)
        self.assertTrue(second.on_task)
        mock_t1.assert_called_once()
        mock_t2.assert_not_called()
        mock_shot.assert_not_called()

    def test_tier1_low_confidence_escalates_to_tier2(self) -> None:
        ctx = _make_browser_ctx("youtube.com", "Watch this crazy trick!", tab_id=42)
        t1_unsure = JudgeVerdict(
            activity="Ambiguous YouTube video",
            on_task=True,
            confidence=0.55,  # < TIER1_ESCALATION_CONFIDENCE (0.75)
            reason="Title is ambiguous.",
            tabs_to_close=[],
            tier_used=1,
        )
        t2_sure = JudgeVerdict(
            activity="Watching YouTube Shorts comedy",
            on_task=False,
            confidence=0.92,
            reason="Screen shows vertical YouTube Shorts entertainment feed.",
            tabs_to_close=[42],
            tier_used=2,
        )

        with (
            patch("app.judge.tier1_evaluate", return_value=t1_unsure) as mock_t1,
            patch("app.screen.capture_screenshot_bytes", return_value=b"\xff\xd8fakejpeg") as mock_shot,
            patch("app.judge.tier2_evaluate", return_value=t2_sure) as mock_t2,
        ):
            verdict = evaluate_context(ctx, self.profile, self.cache, "fake-key")

        mock_t1.assert_called_once()
        mock_shot.assert_called_once()
        mock_t2.assert_called_once()
        self.assertEqual(verdict.tier_used, 2)
        self.assertFalse(verdict.on_task)
        self.assertEqual(verdict.tabs_to_close, [42])

    def test_confidence_gating_never_punishes_uncertainty(self) -> None:
        ctx = _make_browser_ctx("youtube.com", "Ambiguous Stream", tab_id=12)
        t1_unsure = JudgeVerdict(
            activity="Ambiguous",
            on_task=False,
            confidence=0.50,
            reason="Not sure",
            tabs_to_close=[12],
            tier_used=1,
        )
        t2_unsure = JudgeVerdict(
            activity="Unclear screen content",
            on_task=False,
            confidence=0.48,  # < PUNISH_CONFIDENCE_THRESHOLD (0.60)
            reason="Screen is mostly blank/loading.",
            tabs_to_close=[12],
            tier_used=2,
        )

        with (
            patch("app.judge.tier1_evaluate", return_value=t1_unsure),
            patch("app.screen.capture_screenshot_bytes", return_value=b"\xff\xd8fakejpeg"),
            patch("app.judge.tier2_evaluate", return_value=t2_unsure),
        ):
            verdict = evaluate_context(ctx, self.profile, self.cache, "fake-key")

        self.assertTrue(verdict.on_task)
        self.assertEqual(verdict.tabs_to_close, [])
        self.assertEqual(verdict.tier_used, 2)

    def test_force_vision_bypasses_cache(self) -> None:
        ctx = _make_browser_ctx("youtube.com", "DSA Lecture", tab_id=55)
        self.cache.record_override("youtube.com", "DSA Lecture")

        t2_fresh = JudgeVerdict(
            activity="Scrolling Shorts inside same tab",
            on_task=False,
            confidence=0.95,
            reason="Screen shows Shorts feed despite earlier override.",
            tabs_to_close=[55],
            tier_used=2,
        )
        with (
            patch("app.screen.capture_screenshot_bytes", return_value=b"\xff\xd8fakejpeg"),
            patch("app.judge.tier2_evaluate", return_value=t2_fresh) as mock_t2,
        ):
            verdict = evaluate_context(ctx, self.profile, self.cache, "fake-key", force_vision=True)

        mock_t2.assert_called_once()
        self.assertEqual(verdict.tier_used, 2)
        self.assertFalse(verdict.on_task)

    def test_rate_limit_fallback_tier1_and_tier2(self) -> None:
        ctx_unknown = _make_browser_ctx("youtube.com", "Random Video")
        with patch("app.judge.tier1_evaluate", side_effect=GeminiRateLimitError("429")):
            v1 = evaluate_context(ctx_unknown, self.profile, self.cache, "fake-key")
        self.assertTrue(v1.on_task)
        self.assertTrue(v1.rate_limited)
        self.assertEqual(v1.tier_used, 0)

        # Forced vision on a blocked domain still enforces Tier 0 block on rate limit
        ctx_blocked = _make_browser_ctx("instagram.com", "Instagram", tab_id=88)
        with (
            patch("app.screen.capture_screenshot_bytes", return_value=b"\xff\xd8fakejpeg"),
            patch("app.judge.tier2_evaluate", side_effect=GeminiRateLimitError("RESOURCE_EXHAUSTED")),
        ):
            v2 = evaluate_context(
                ctx_blocked, self.profile, self.cache, "fake-key", force_vision=True
            )
        self.assertFalse(v2.on_task)
        self.assertTrue(v2.rate_limited)
        self.assertEqual(v2.tabs_to_close, [88])

    def test_screenshot_failure_fallback(self) -> None:
        ctx = _make_browser_ctx("youtube.com", "Binary Trees Tutorial", tab_id=15)
        t1_verdict = JudgeVerdict(
            activity="Watching Binary Trees Tutorial",
            on_task=True,
            confidence=0.70,
            reason="Title mentions Binary Trees.",
            tabs_to_close=[],
            tier_used=1,
        )

        # Tier 2 escalation after screenshot failure falls back to Tier 1
        with (
            patch("app.judge.tier1_evaluate", return_value=t1_verdict),
            patch("app.screen.capture_screenshot_bytes", side_effect=RuntimeError("Desktop locked")),
            patch("app.judge.tier2_evaluate") as mock_t2,
        ):
            v = evaluate_context(ctx, self.profile, self.cache, "fake-key")
        mock_t2.assert_not_called()
        self.assertEqual(v.tier_used, 1)
        self.assertTrue(v.on_task)

        # Forced vision with screenshot failure AND Tier 1 failure still treats safely as on_task
        with (
            patch("app.screen.capture_screenshot_bytes", side_effect=RuntimeError("UAC prompt")),
            patch("app.judge.tier1_evaluate", side_effect=RuntimeError("Network error")),
        ):
            v_safe = evaluate_context(
                ctx, self.profile, self.cache, "fake-key", force_vision=True
            )
        self.assertTrue(v_safe.on_task)
        self.assertFalse(v_safe.rate_limited)

    def test_non_rate_limit_exception_fallbacks(self) -> None:
        ctx = _make_browser_ctx("unknown-docs.org", "Graph Algorithms", tab_id=21)

        # Tier 1 transient error falls back to Tier 0 / safe on-task
        with patch("app.judge.tier1_evaluate", side_effect=RuntimeError("Timeout")):
            v1 = evaluate_context(ctx, self.profile, self.cache, "fake-key")
        self.assertTrue(v1.on_task)
        self.assertFalse(v1.rate_limited)
        self.assertEqual(v1.tier_used, 0)

        # Tier 2 escalation error falls back to Tier 1 verdict
        t1_low = JudgeVerdict(
            activity="Reading docs",
            on_task=True,
            confidence=0.65,
            reason="Likely on-task.",
            tabs_to_close=[],
            tier_used=1,
        )
        with (
            patch("app.judge.tier1_evaluate", return_value=t1_low),
            patch("app.screen.capture_screenshot_bytes", return_value=b"\xff\xd8fakejpeg"),
            patch("app.judge.tier2_evaluate", side_effect=ValueError("Bad JSON")),
        ):
            v2 = evaluate_context(ctx, self.profile, self.cache, "fake-key")
        self.assertTrue(v2.on_task)
        self.assertEqual(v2.tier_used, 1)

        # Forced vision Tier 2 error falls back to Tier 1, or safe on-task if Tier 1 also fails
        with (
            patch("app.screen.capture_screenshot_bytes", return_value=b"\xff\xd8fakejpeg"),
            patch("app.judge.tier2_evaluate", side_effect=RuntimeError("Vision error")),
            patch("app.judge.tier1_evaluate", side_effect=RuntimeError("Text error")),
        ):
            v3 = evaluate_context(ctx, self.profile, self.cache, "fake-key", force_vision=True)
        self.assertTrue(v3.on_task)
        self.assertFalse(v3.rate_limited)


class TestEventEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = _make_profile()
        self.cache = VerdictCache(ttl_seconds=1800.0)
        self.tab_store = TabSnapshotStore()
        self.timings = TimingConfig(
            debounce_seconds=0.06,
            heartbeat_seconds=0.22,
            dwell_seconds=0.11,
            grace_period_seconds=0.5,
            strict_lockout_seconds=0.5,
            extension_poll_seconds=0.5,
            window_poll_seconds=0.5,
        )

    def _set_active_site(self, domain: str, title: str, tab_id: int = 1) -> WindowInfo:
        url = f"https://www.{domain}/"
        self.tab_store.update(
            TabSnapshot(
                tabs=[TabInfo(tab_id=tab_id, window_id=1, title=title, url=url, domain=domain, active=True)],
                active_tab_id=tab_id,
                window_focused=True,
                updated_at=time.time(),
            )
        )
        return WindowInfo(hwnd=10, title=title, process_name="chrome.exe", pid=100, is_browser=True)

    def test_debounce_collapses_multiple_triggers_and_baseline_runs_on_start(self) -> None:
        win = self._set_active_site("leetcode.com", "Two Sum - LeetCode")
        results: list[tuple[ActivityContext, JudgeVerdict]] = []
        event_cond = threading.Condition()
        calls: list[tuple[str, bool]] = []

        def fake_eval(ctx: ActivityContext, profile: GoalProfile, cache: VerdictCache, api_key: str, force_vision: bool = False) -> JudgeVerdict:
            calls.append((ctx.trigger_type, force_vision))
            return JudgeVerdict(
                activity="Coding",
                on_task=True,
                confidence=1.0,
                reason="Allowed",
                tabs_to_close=[],
                tier_used=2 if force_vision else 0,
            )

        def on_verdict(ctx: ActivityContext, verdict: JudgeVerdict) -> None:
            with event_cond:
                results.append((ctx, verdict))
                event_cond.notify_all()

        engine = EventEngine(
            goal="Study DSA",
            profile=self.profile,
            cache=self.cache,
            tab_store=self.tab_store,
            timings=TimingConfig(
                debounce_seconds=0.08,
                heartbeat_seconds=5.0,
                dwell_seconds=5.0,
                grace_period_seconds=1.0,
                strict_lockout_seconds=1.0,
                extension_poll_seconds=0.5,
                window_poll_seconds=0.5,
            ),
            api_key="fake-key",
            on_verdict=on_verdict,
            get_foreground_fn=lambda: win,
            list_windows_fn=lambda: ["Two Sum (chrome.exe)"],
            evaluate_fn=fake_eval,
        )
        try:
            engine.start()
            with event_cond:
                self.assertTrue(event_cond.wait_for(lambda: len(results) >= 1, timeout=1.0))

            # Rapid burst of 5 triggers within ~25ms -> should collapse into ONE check
            for trig in ("tab_activated", "tab_updated", "tab_created", "tab_activated", "window_changed"):
                engine.submit_trigger(trig)  # type: ignore[arg-type]
                time.sleep(0.005)

            with event_cond:
                self.assertTrue(event_cond.wait_for(lambda: len(results) >= 2, timeout=1.0))
            time.sleep(0.12)  # Wait past another debounce window to verify no extra triggers fire

            self.assertEqual(len(results), 2)
            self.assertEqual(calls[0], ("baseline", True))
            self.assertEqual(calls[1], ("window_changed", False))
        finally:
            engine.stop()

    def test_dwell_and_heartbeat_fire_forced_vision(self) -> None:
        win = self._set_active_site("youtube.com", "Graph Theory - YouTube")
        seen: list[tuple[str, int]] = []
        seen_cond = threading.Condition()

        def on_verdict(ctx: ActivityContext, verdict: JudgeVerdict) -> None:
            with seen_cond:
                seen.append((ctx.trigger_type, verdict.tier_used))
                seen_cond.notify_all()

        t2_verdict = JudgeVerdict(
            activity="Watching lecture",
            on_task=True,
            confidence=0.90,
            reason="Lecture slides visible.",
            tabs_to_close=[],
            tier_used=2,
        )
        t1_verdict = JudgeVerdict(
            activity="YouTube lecture",
            on_task=True,
            confidence=0.88,
            reason="Title looks on-task.",
            tabs_to_close=[],
            tier_used=1,
        )

        engine = EventEngine(
            goal="Study DSA",
            profile=self.profile,
            cache=self.cache,
            tab_store=self.tab_store,
            timings=self.timings,  # debounce=0.06, dwell=0.11, heartbeat=0.22
            api_key="fake-key",
            on_verdict=on_verdict,
            get_foreground_fn=lambda: win,
            list_windows_fn=lambda: ["Graph Theory (chrome.exe)"],
        )
        with (
            patch("app.judge.tier1_evaluate", return_value=t1_verdict),
            patch("app.screen.capture_screenshot_bytes", return_value=b"\xff\xd8fakejpeg"),
            patch("app.judge.tier2_evaluate", return_value=t2_verdict),
        ):
            try:
                engine.start()
                with seen_cond:
                    ok = seen_cond.wait_for(
                        lambda: any(t == "dwell" for t, _ in seen) and any(t == "heartbeat" for t, _ in seen),
                        timeout=2.0,
                    )
                self.assertTrue(ok, f"Expected both dwell and heartbeat triggers; got {seen}")
                for trig, tier in seen:
                    if trig in ("baseline", "dwell", "heartbeat"):
                        self.assertEqual(tier, 2)
            finally:
                engine.stop()

    def test_rate_limit_backoff_skips_ai_but_still_runs_tier0(self) -> None:
        win = self._set_active_site("youtube.com", "Random Video")
        verdicts: list[tuple[str, JudgeVerdict]] = []
        cond = threading.Condition()

        def on_verdict(ctx: ActivityContext, verdict: JudgeVerdict) -> None:
            with cond:
                verdicts.append((ctx.source_key, verdict))
                cond.notify_all()

        engine = EventEngine(
            goal="Study DSA",
            profile=self.profile,
            cache=self.cache,
            tab_store=self.tab_store,
            timings=TimingConfig(
                debounce_seconds=0.03,
                heartbeat_seconds=5.0,
                dwell_seconds=5.0,
                grace_period_seconds=1.0,
                strict_lockout_seconds=1.0,
                extension_poll_seconds=0.5,
                window_poll_seconds=0.5,
            ),
            api_key="fake-key",
            on_verdict=on_verdict,
            get_foreground_fn=lambda: win,
            list_windows_fn=lambda: [],
            rate_limit_backoff_seconds=2.0,
        )

        with (
            patch("app.screen.capture_screenshot_bytes", return_value=b"\xff\xd8fakejpeg"),
            patch("app.judge.tier2_evaluate", side_effect=GeminiRateLimitError("429")),
            patch("app.judge.tier1_evaluate") as mock_t1,
        ):
            try:
                engine.start()  # baseline hits 429 -> enters 2.0s backoff
                with cond:
                    self.assertTrue(cond.wait_for(lambda: len(verdicts) >= 1, timeout=1.0))
                self.assertTrue(verdicts[0][1].rate_limited)
                self.assertTrue(verdicts[0][1].on_task)

                # While in backoff, unknown/borderline site must NOT call Tier 1 AI
                engine.submit_trigger("tab_activated")
                with cond:
                    self.assertTrue(cond.wait_for(lambda: len(verdicts) >= 2, timeout=1.0))
                self.assertTrue(verdicts[1][1].on_task)
                self.assertTrue(verdicts[1][1].rate_limited)
                mock_t1.assert_not_called()

                # While in backoff, blocked domain still gets caught by Tier 0!
                win = self._set_active_site("instagram.com", "Instagram", tab_id=66)
                engine.submit_trigger("tab_activated")
                with cond:
                    self.assertTrue(cond.wait_for(lambda: len(verdicts) >= 3, timeout=1.0))
                self.assertFalse(verdicts[2][1].on_task)
                self.assertEqual(verdicts[2][1].tier_used, 0)
                self.assertEqual(verdicts[2][1].tabs_to_close, [66])
            finally:
                engine.stop()

    def test_exception_in_check_or_callback_does_not_crash_engine_and_stop_is_clean(self) -> None:
        win = self._set_active_site("leetcode.com", "Two Sum - LeetCode")
        call_count = 0
        cond = threading.Condition()

        def flaky_eval(ctx: ActivityContext, profile: GoalProfile, cache: VerdictCache, api_key: str, force_vision: bool = False) -> JudgeVerdict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Transient explosion")
            return JudgeVerdict(
                activity="Coding",
                on_task=True,
                confidence=1.0,
                reason="OK",
                tabs_to_close=[],
                tier_used=0,
            )

        delivered: list[str] = []

        def flaky_callback(ctx: ActivityContext, verdict: JudgeVerdict) -> None:
            with cond:
                delivered.append(ctx.trigger_type)
                cond.notify_all()
            if len(delivered) == 1:
                raise ValueError("Callback error")

        engine = EventEngine(
            goal="Study DSA",
            profile=self.profile,
            cache=self.cache,
            tab_store=self.tab_store,
            timings=TimingConfig(
                debounce_seconds=0.03,
                heartbeat_seconds=5.0,
                dwell_seconds=5.0,
                grace_period_seconds=1.0,
                strict_lockout_seconds=1.0,
                extension_poll_seconds=0.5,
                window_poll_seconds=0.5,
            ),
            api_key="fake-key",
            on_verdict=flaky_callback,
            get_foreground_fn=lambda: win,
            list_windows_fn=lambda: [],
            evaluate_fn=flaky_eval,
        )

        engine.start()
        time.sleep(0.05)  # Let baseline fail inside flaky_eval
        engine.submit_trigger("tab_activated")
        with cond:
            self.assertTrue(cond.wait_for(lambda: len(delivered) >= 1, timeout=1.0))

        # Engine must still be alive after both eval error and callback error
        self.assertTrue(engine.is_running)
        engine.run_immediate_check("manual_recheck", force_vision=False)
        with cond:
            self.assertTrue(cond.wait_for(lambda: len(delivered) >= 2, timeout=1.0))

        engine.stop()
        engine.stop()  # Idempotent
        self.assertFalse(engine.is_running)
        self.assertIsNotNone(engine._thread)
        assert engine._thread is not None
        self.assertFalse(engine._thread.is_alive())

        # Submitting after stop is ignored
        count_before = len(delivered)
        engine.submit_trigger("tab_activated")
        engine.run_immediate_check("manual_recheck", force_vision=False)
        time.sleep(0.08)
        self.assertEqual(len(delivered), count_before)

    def test_leaving_borderline_clears_dwell(self) -> None:
        current_win = self._set_active_site("youtube.com", "DSA Lecture - YouTube")
        seen: list[str] = []
        cond = threading.Condition()

        def fake_eval(ctx: ActivityContext, profile: GoalProfile, cache: VerdictCache, api_key: str, force_vision: bool = False) -> JudgeVerdict:
            return JudgeVerdict(
                activity="OK",
                on_task=True,
                confidence=0.95,
                reason="OK",
                tabs_to_close=[],
                tier_used=2 if force_vision else 1,
            )

        def on_verdict(ctx: ActivityContext, verdict: JudgeVerdict) -> None:
            with cond:
                seen.append(ctx.trigger_type)
                cond.notify_all()

        engine = EventEngine(
            goal="Study DSA",
            profile=self.profile,
            cache=self.cache,
            tab_store=self.tab_store,
            timings=TimingConfig(
                debounce_seconds=0.03,
                heartbeat_seconds=5.0,
                dwell_seconds=0.10,
                grace_period_seconds=1.0,
                strict_lockout_seconds=1.0,
                extension_poll_seconds=0.5,
                window_poll_seconds=0.5,
            ),
            api_key="fake-key",
            on_verdict=on_verdict,
            get_foreground_fn=lambda: current_win,
            list_windows_fn=lambda: [],
            evaluate_fn=fake_eval,
        )
        try:
            engine.start()
            with cond:
                self.assertTrue(cond.wait_for(lambda: len(seen) >= 1, timeout=1.0))

            # Switch to non-borderline app before dwell fires
            current_win = WindowInfo(
                hwnd=20,
                title="main.py - Visual Studio Code",
                process_name="code.exe",
                pid=200,
                is_browser=False,
            )
            engine.submit_trigger("window_changed")
            with cond:
                self.assertTrue(cond.wait_for(lambda: len(seen) >= 2, timeout=1.0))

            # Wait longer than dwell_seconds and verify dwell never fires
            time.sleep(0.16)
            self.assertNotIn("dwell", seen)
        finally:
            engine.stop()

    def test_rate_limit_backoff_recovers_after_expiry(self) -> None:
        win = self._set_active_site("unknown-blog.org", "Tries Tutorial")
        verdicts: list[JudgeVerdict] = []
        cond = threading.Condition()

        def on_verdict(ctx: ActivityContext, verdict: JudgeVerdict) -> None:
            with cond:
                verdicts.append(verdict)
                cond.notify_all()

        ok_t1 = JudgeVerdict(
            activity="Reading Tries Tutorial",
            on_task=True,
            confidence=0.92,
            reason="Relevant to DSA.",
            tabs_to_close=[],
            tier_used=1,
        )

        engine = EventEngine(
            goal="Study DSA",
            profile=self.profile,
            cache=self.cache,
            tab_store=self.tab_store,
            timings=TimingConfig(
                debounce_seconds=0.02,
                heartbeat_seconds=5.0,
                dwell_seconds=5.0,
                grace_period_seconds=1.0,
                strict_lockout_seconds=1.0,
                extension_poll_seconds=0.5,
                window_poll_seconds=0.5,
            ),
            api_key="fake-key",
            on_verdict=on_verdict,
            get_foreground_fn=lambda: win,
            list_windows_fn=lambda: [],
            rate_limit_backoff_seconds=0.10,
        )

        with (
            patch("app.screen.capture_screenshot_bytes", return_value=b"\xff\xd8fakejpeg"),
            patch("app.judge.tier2_evaluate", side_effect=GeminiRateLimitError("429")),
            patch("app.judge.tier1_evaluate", return_value=ok_t1) as mock_t1,
        ):
            try:
                engine.start()  # baseline hits 429 -> 0.10s backoff
                with cond:
                    self.assertTrue(cond.wait_for(lambda: len(verdicts) >= 1, timeout=1.0))
                self.assertTrue(verdicts[0].rate_limited)

                # Wait past backoff expiry
                time.sleep(0.13)
                engine.submit_trigger("tab_activated")
                with cond:
                    self.assertTrue(cond.wait_for(lambda: len(verdicts) >= 2, timeout=1.0))
                self.assertFalse(verdicts[1].rate_limited)
                self.assertEqual(verdicts[1].tier_used, 1)
                mock_t1.assert_called_once()
            finally:
                engine.stop()

    def test_mocked_clock_drives_debounce_heartbeat_and_dwell_without_real_sleep(self) -> None:
        win = self._set_active_site("youtube.com", "Graphs Tutorial - YouTube")
        clock_lock = threading.Lock()
        current_time = 100.0

        def fake_clock() -> float:
            with clock_lock:
                return current_time

        def advance(seconds: float) -> None:
            nonlocal current_time
            with clock_lock:
                current_time += seconds

        events: list[tuple[str, bool]] = []
        cond = threading.Condition()

        def fake_eval(
            ctx: ActivityContext,
            profile: GoalProfile,
            cache: VerdictCache,
            api_key: str,
            force_vision: bool = False,
        ) -> JudgeVerdict:
            return JudgeVerdict(
                activity="Watching tutorial",
                on_task=True,
                confidence=0.9,
                reason="On-task",
                tabs_to_close=[],
                tier_used=2 if force_vision else 1,
            )

        def on_verdict(ctx: ActivityContext, verdict: JudgeVerdict) -> None:
            with cond:
                events.append((ctx.trigger_type, verdict.tier_used == 2))
                cond.notify_all()

        engine = EventEngine(
            goal="Study DSA",
            profile=self.profile,
            cache=self.cache,
            tab_store=self.tab_store,
            timings=TimingConfig(
                debounce_seconds=1.5,
                heartbeat_seconds=10.0,
                dwell_seconds=15.0,
                grace_period_seconds=5.0,
                strict_lockout_seconds=8.0,
                extension_poll_seconds=1.5,
                window_poll_seconds=0.5,
            ),
            api_key="fake-key",
            on_verdict=on_verdict,
            get_foreground_fn=lambda: win,
            list_windows_fn=lambda: ["Graphs Tutorial (chrome.exe)"],
            evaluate_fn=fake_eval,
            clock_fn=fake_clock,
        )

        try:
            engine.start()
            with cond:
                self.assertTrue(cond.wait_for(lambda: len(events) == 1, timeout=1.0))
            self.assertEqual(events[0], ("baseline", True))

            # Two triggers separated by 1.0s (< 1.5s debounce) collapse into the second one
            advance(1.0)
            engine.submit_trigger("tab_activated")
            advance(1.0)
            engine.submit_trigger("tab_updated")
            advance(1.5)  # now = 103.5 -> debounce fires, sets dwell at 118.5
            with cond:
                self.assertTrue(cond.wait_for(lambda: len(events) == 2, timeout=1.0))
            self.assertEqual(events[1], ("tab_updated", False))

            # Advance to first heartbeat at 110.0
            advance(6.5)
            with cond:
                self.assertTrue(cond.wait_for(lambda: len(events) == 3, timeout=1.0))
            self.assertEqual(events[2], ("heartbeat", True))

            # Advance to borderline dwell at 118.5
            advance(8.5)
            with cond:
                self.assertTrue(cond.wait_for(lambda: len(events) == 4, timeout=1.0))
            self.assertEqual(events[3], ("dwell", True))
        finally:
            engine.stop()


if __name__ == "__main__":
    unittest.main()
