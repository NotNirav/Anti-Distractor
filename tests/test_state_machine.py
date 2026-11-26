"""Tests for AgentStateMachine covering normal and strict modes, overrides, and lockout lifecycle."""

from __future__ import annotations

import threading
import time
from typing import Callable, Literal

import pytest

from app.cache import VerdictCache
from app.config import TimingConfig
from app.models import (
    ActivityContext,
    AgentDecision,
    AgentStateName,
    JudgeVerdict,
    ModeType,
    TabInfo,
    TabSnapshot,
    WindowInfo,
)
from app.state_machine import AgentStateMachine, UIHooks

TEST_TIMINGS = TimingConfig(
    debounce_seconds=1.0,
    heartbeat_seconds=10.0,
    dwell_seconds=5.0,
    grace_period_seconds=5.0,
    strict_lockout_seconds=8.0,
    extension_poll_seconds=1.5,
    window_poll_seconds=0.5,
)


class FakeClock:
    def __init__(self, initial_time: float = 1000.0) -> None:
        self.time = initial_time

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


class FakeCommandQueue:
    def __init__(self) -> None:
        self.enqueued_tabs: list[int] = []

    def enqueue_close_tabs(self, tab_ids: list[int]) -> None:
        self.enqueued_tabs.extend(tab_ids)


class FakeMinimizer:
    def __init__(self) -> None:
        self.minimized_hwnds: list[int] = []

    def __call__(self, hwnd: int) -> bool:
        self.minimized_hwnds.append(hwnd)
        return True


class FakeUIHooks:
    def __init__(self) -> None:
        self.hud_updates: list[tuple[AgentStateName, str]] = []
        self.layer1_popups: list[tuple[str, str, Callable]] = []
        self.layer2_warnings: list[tuple[str, str]] = []
        self.strict_overlays: list[tuple[str, str, float, Callable]] = []
        self.hide_calls: int = 0
        self.hook_callback: Callable[[], None] | None = None

    def show_setup(self, on_start: Callable[[str, int, ModeType], None]) -> None:
        pass

    def show_monitoring_hud(
        self, goal: str, mode: ModeType, on_end_session: Callable[[], None]
    ) -> None:
        pass

    def update_hud_status(self, state: AgentStateName, summary: str) -> None:
        self.hud_updates.append((state, summary))
        if self.hook_callback:
            self.hook_callback()

    def show_layer1_popup(
        self,
        goal: str,
        reason: str,
        on_choice: Callable[[Literal["back_to_work", "relevant"]], None],
    ) -> None:
        self.layer1_popups.append((goal, reason, on_choice))
        if self.hook_callback:
            self.hook_callback()

    def show_layer2_warning(self, goal: str, reason: str) -> None:
        self.layer2_warnings.append((goal, reason))
        if self.hook_callback:
            self.hook_callback()

    def show_strict_overlay(
        self,
        goal: str,
        reason: str,
        lockout_seconds: float,
        on_lockout_done: Callable[[], None],
    ) -> None:
        self.strict_overlays.append((goal, reason, lockout_seconds, on_lockout_done))
        if self.hook_callback:
            self.hook_callback()

    def hide_popups_and_overlay(self) -> None:
        self.hide_calls += 1
        if self.hook_callback:
            self.hook_callback()

    def show_review(self, review_text: str, on_close: Callable[[], None]) -> None:
        pass


def make_context(
    source_key: str,
    title: str,
    is_browser: bool = True,
    tab_ids: list[int] | None = None,
    active_tab_id: int | None = None,
    hwnd: int = 1234,
    process_name: str = "chrome.exe",
) -> ActivityContext:
    tabs: list[TabInfo] = []
    if is_browser and tab_ids:
        for tid in tab_ids:
            tabs.append(
                TabInfo(
                    tab_id=tid,
                    window_id=1,
                    title=title,
                    url=f"https://{source_key}/path",
                    domain=source_key,
                    active=(tid == active_tab_id),
                )
            )
    tab_snapshot = (
        TabSnapshot(
            tabs=tabs,
            active_tab_id=active_tab_id or (tab_ids[0] if tab_ids else None),
            window_focused=True,
            updated_at=1000.0,
        )
        if is_browser
        else None
    )
    fg_window = WindowInfo(
        hwnd=hwnd,
        title=title,
        process_name=process_name if not is_browser else "chrome.exe",
        pid=5678,
        is_browser=is_browser,
    )
    return ActivityContext(
        trigger_type="tab_updated",
        goal="Study DSA",
        foreground_window=fg_window,
        tab_snapshot=tab_snapshot,
        open_windows=[title],
        source_key=source_key,
        display_title=title,
        timestamp=1000.0,
    )


def make_verdict(
    on_task: bool,
    reason: str = "Verdict reason",
    confidence: float = 0.95,
    tabs_to_close: list[int] | None = None,
    from_cache: bool = False,
) -> JudgeVerdict:
    return JudgeVerdict(
        activity="Active testing activity",
        on_task=on_task,
        confidence=confidence,
        reason=reason,
        tabs_to_close=tabs_to_close or [],
        tier_used=1,
        from_cache=from_cache,
        rate_limited=False,
    )


def test_on_task_resets_strikes():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    sm = AgentStateMachine("Study DSA", "normal", TEST_TIMINGS, cache, clock=clock)

    ctx_bad = make_context("instagram.com", "Instagram", is_browser=True, tab_ids=[101])
    d1 = sm.handle_verdict(ctx_bad, make_verdict(on_task=False, reason="Distraction"))
    assert sm.state == "GRACE_PERIOD"

    # Expire grace period to get a strike
    clock.advance(TEST_TIMINGS.grace_period_seconds + 1.0)
    sm.handle_tick()
    assert sm.state == "LAYER_1_ASK"
    assert sm.strikes == 1

    # On-task verdict resets strikes to 0 and clears warnings
    ctx_good = make_context("leetcode.com", "LeetCode Problem", is_browser=True, tab_ids=[102])
    d2 = sm.handle_verdict(ctx_good, make_verdict(on_task=True, reason="Solving problem"))
    assert sm.state == "ON_TASK"
    assert sm.strikes == 0
    assert d2.action_taken == "cleared"


def test_first_off_task_enters_grace_period():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    ui_hooks = FakeUIHooks()
    sm = AgentStateMachine("Study DSA", "normal", TEST_TIMINGS, cache, ui_hooks=ui_hooks, clock=clock)

    ctx = make_context("youtube.com", "Cat Video", is_browser=True, tab_ids=[101])
    d = sm.handle_verdict(ctx, make_verdict(on_task=False, reason="Watching cats"))

    assert sm.state == "GRACE_PERIOD"
    assert d.action_taken == "grace_started"
    assert sm.strikes == 0
    assert any("grace period started" in summary for _, summary in ui_hooks.hud_updates)


def test_grace_expiry_goes_to_layer1():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    ui_hooks = FakeUIHooks()
    sm = AgentStateMachine("Study DSA", "normal", TEST_TIMINGS, cache, ui_hooks=ui_hooks, clock=clock)

    ctx = make_context("reddit.com", "Memes", is_browser=True, tab_ids=[101])
    sm.handle_verdict(ctx, make_verdict(on_task=False, reason="Browsing memes"))
    assert sm.state == "GRACE_PERIOD"

    # Tick before expiry remains in grace
    clock.advance(TEST_TIMINGS.grace_period_seconds - 1.0)
    d_early = sm.handle_tick()
    assert d_early is None or d_early.state == "GRACE_PERIOD"
    assert sm.state == "GRACE_PERIOD"
    assert len(ui_hooks.layer1_popups) == 0

    # Advance past grace expiry
    clock.advance(2.0)
    d_expired = sm.handle_tick()
    assert sm.state == "LAYER_1_ASK"
    assert sm.strikes == 1
    assert d_expired is not None
    assert d_expired.action_taken == "ask_popup"
    assert len(ui_hooks.layer1_popups) == 1
    assert ui_hooks.layer1_popups[0][0] == "Study DSA"


def test_back_to_work_clears_everything():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    ui_hooks = FakeUIHooks()
    sm = AgentStateMachine("Study DSA", "normal", TEST_TIMINGS, cache, ui_hooks=ui_hooks, clock=clock)

    ctx = make_context("reddit.com", "Memes", is_browser=True, tab_ids=[101])
    sm.handle_verdict(ctx, make_verdict(on_task=False, reason="Browsing memes"))
    clock.advance(TEST_TIMINGS.grace_period_seconds + 1.0)
    sm.handle_tick()
    assert sm.state == "LAYER_1_ASK"

    decision = sm.handle_user_action("back_to_work")
    assert sm.state == "ON_TASK"
    assert sm.strikes == 0
    assert decision.action_taken == "cleared"
    assert ui_hooks.hide_calls >= 1


def test_relevant_logs_override_and_updates_verdict_cache():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    ui_hooks = FakeUIHooks()
    overrides_logged: list[tuple[ActivityContext | None, str]] = []

    sm = AgentStateMachine(
        "Study DSA",
        "normal",
        TEST_TIMINGS,
        cache,
        ui_hooks=ui_hooks,
        clock=clock,
        on_override=lambda c, r: overrides_logged.append((c, r)),
    )

    ctx = make_context("youtube.com", "Graph Algorithms Video", is_browser=True, tab_ids=[101])
    sm.handle_verdict(ctx, make_verdict(on_task=False, reason="YouTube not in allowed list"))
    clock.advance(TEST_TIMINGS.grace_period_seconds + 1.0)
    sm.handle_tick()
    assert sm.state == "LAYER_1_ASK"

    # User clicks "relevant"
    decision = sm.handle_user_action("relevant")
    assert sm.state == "ON_TASK"
    assert sm.strikes == 0
    assert decision.action_taken == "cleared"
    assert len(sm.override_log) == 1
    assert sm.override_log[0]["source_key"] == "youtube.com"
    assert len(overrides_logged) == 1

    # Check cache was updated
    cached_verdict = cache.get("youtube.com", "Graph Algorithms Video")
    assert cached_verdict is not None
    assert cached_verdict.on_task is True
    assert cached_verdict.from_cache is True


def test_override_plus_model_disagreement_goes_straight_to_layer2():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    ui_hooks = FakeUIHooks()

    sm = AgentStateMachine("Study DSA", "normal", TEST_TIMINGS, cache, ui_hooks=ui_hooks, clock=clock)

    ctx = make_context("youtube.com", "Funny Gaming Video", is_browser=True, tab_ids=[101])
    sm.handle_verdict(ctx, make_verdict(on_task=False, reason="Gaming"))
    clock.advance(TEST_TIMINGS.grace_period_seconds + 1.0)
    sm.handle_tick()
    assert sm.state == "LAYER_1_ASK"

    # User falsely overrides as relevant
    sm.handle_user_action("relevant")
    assert sm.state == "ON_TASK"

    # Fresh vision check evaluates and disagrees (from_cache=False)
    fresh_verdict = make_verdict(on_task=False, reason="Fresh check confirmed gaming", from_cache=False)
    decision = sm.handle_verdict(ctx, fresh_verdict)

    # Must bypass grace period and go straight to Layer 2 Ultimatum
    assert sm.state == "LAYER_2_ULTIMATUM"
    assert decision.state == "LAYER_2_ULTIMATUM"
    assert decision.action_taken == "warn_popup"
    assert sm.strikes >= 2
    assert len(ui_hooks.layer2_warnings) == 1


def test_strict_mode_closes_only_tabs_in_snapshot_minimizes_and_shows_overlay():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    queue = FakeCommandQueue()
    minimizer = FakeMinimizer()
    ui_hooks = FakeUIHooks()

    sm = AgentStateMachine(
        "Study DSA",
        "strict",
        TEST_TIMINGS,
        cache,
        command_queue=queue,
        ui_hooks=ui_hooks,
        clock=clock,
        minimize_fn=minimizer,
    )

    # Step 1: Trigger grace
    ctx_browser = make_context("instagram.com", "Feed", is_browser=True, tab_ids=[101, 102], active_tab_id=101)
    sm.handle_verdict(ctx_browser, make_verdict(on_task=False, reason="Social media"))
    assert sm.state == "GRACE_PERIOD"

    # Step 2: Grace expires to Layer 1
    clock.advance(TEST_TIMINGS.grace_period_seconds + 1.0)
    sm.handle_tick()
    assert sm.state == "LAYER_1_ASK"

    # Step 3: Off-task continues -> escalates to Layer 2 Strict
    # Verdict requests tabs [101, 999] (where 999 is nonexistent/stale)
    v_strict = make_verdict(on_task=False, reason="Still on Instagram", tabs_to_close=[101, 999])
    decision = sm.handle_verdict(ctx_browser, v_strict)

    assert sm.state == "LAYER_2_ULTIMATUM"
    assert decision.action_taken == "strict_block"
    # Only tab 101 should be queued (999 filtered out)
    assert decision.tabs_to_close == [101]
    assert queue.enqueued_tabs == [101]
    assert decision.lockout_seconds == TEST_TIMINGS.strict_lockout_seconds
    assert len(ui_hooks.strict_overlays) == 1

    # Step 4: Test desktop window minimization (non-browser app)
    # Reset to ON_TASK for second test scenario
    sm.handle_verdict(ctx_browser, make_verdict(on_task=True))
    assert sm.state == "ON_TASK"

    ctx_app = make_context("game.exe", "Game Window", is_browser=False, hwnd=777, process_name="game.exe")
    # First strike -> grace
    sm.handle_verdict(ctx_app, make_verdict(on_task=False, reason="Playing game"))
    clock.advance(TEST_TIMINGS.grace_period_seconds + 1.0)
    sm.handle_tick()
    assert sm.state == "LAYER_1_ASK"

    # Second off-task -> Layer 2 Strict minimize
    d_app_l2 = sm.handle_verdict(ctx_app, make_verdict(on_task=False, reason="Still in game"))
    assert d_app_l2.minimize_hwnd == 777
    assert 777 in minimizer.minimized_hwnds


def test_strict_mode_skips_closing_when_none_match():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    queue = FakeCommandQueue()
    sm = AgentStateMachine("Study DSA", "strict", TEST_TIMINGS, cache, command_queue=queue, clock=clock)

    # Context with no tabs in snapshot
    ctx = make_context("test.com", "Title", is_browser=True, tab_ids=[])
    sm.handle_verdict(ctx, make_verdict(on_task=False))
    clock.advance(TEST_TIMINGS.grace_period_seconds + 1.0)
    sm.handle_tick()

    d = sm.handle_verdict(ctx, make_verdict(on_task=False, tabs_to_close=[999, 888]))
    assert d.tabs_to_close == []
    assert queue.enqueued_tabs == []


def test_bad_or_missing_verdicts_never_crash():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    sm = AgentStateMachine("Study DSA", "normal", TEST_TIMINGS, cache, clock=clock)

    # Missing verdict
    d1 = sm.handle_verdict(None, None)
    assert d1.state == "IDLE"
    assert d1.action_taken == "none"

    ctx = make_context("example.com", "Example")
    d2 = sm.handle_verdict(ctx, None)
    assert d2.state == "IDLE"

    # Malformed verdict object (missing on_task or invalid type)
    class BadVerdict:
        pass

    d3 = sm.handle_verdict(ctx, BadVerdict())  # type: ignore
    assert d3.action_taken == "none"


def test_hooks_called_outside_internal_lock_no_deadlock():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    ui_hooks = FakeUIHooks()
    decisions: list[AgentDecision] = []

    sm = AgentStateMachine(
        "Study DSA",
        "normal",
        TEST_TIMINGS,
        cache,
        ui_hooks=ui_hooks,
        clock=clock,
        on_decision=lambda d: decisions.append(d),
    )

    reentrant_success = []

    def hook_probe():
        # Verify from another thread that sm._lock is NOT held when hook is invoked
        acquired_in_thread = []

        def try_lock():
            acq = sm._lock.acquire(blocking=False)
            if acq:
                sm._lock.release()
            acquired_in_thread.append(acq)

        t = threading.Thread(target=try_lock)
        t.start()
        t.join(timeout=1.0)
        assert acquired_in_thread == [True], "Internal lock was held during hook call!"

        # And reentering state machine methods does not deadlock
        st = sm.state
        reentrant_success.append(st)

    ui_hooks.hook_callback = hook_probe

    ctx = make_context("reddit.com", "Memes", is_browser=True, tab_ids=[101])
    sm.handle_verdict(ctx, make_verdict(on_task=False))
    assert len(reentrant_success) > 0


def test_lockout_expiry_callback_fired():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    queue = FakeCommandQueue()
    lockout_done_called = []

    sm = AgentStateMachine(
        "Study DSA",
        "strict",
        TEST_TIMINGS,
        cache,
        command_queue=queue,
        clock=clock,
        on_lockout_done=lambda: lockout_done_called.append(True),
    )

    ctx = make_context("instagram.com", "Feed", is_browser=True, tab_ids=[101])
    sm.handle_verdict(ctx, make_verdict(on_task=False))
    clock.advance(TEST_TIMINGS.grace_period_seconds + 1.0)
    sm.handle_tick()

    # Escalate to strict Layer 2
    d = sm.handle_verdict(ctx, make_verdict(on_task=False, tabs_to_close=[101]))
    assert d.action_taken == "strict_block"
    assert sm.state == "LAYER_2_ULTIMATUM"
    initial_strikes = sm.strikes

    # Advance past lockout duration
    clock.advance(TEST_TIMINGS.strict_lockout_seconds + 1.0)
    tick_decision = sm.handle_tick()

    # on_lockout_done callback must be called
    assert len(lockout_done_called) == 1
    # When lockout expires, do NOT reset strikes to 0 or grant a new grace period
    assert sm.state == "LAYER_2_ULTIMATUM"
    assert sm.strikes == initial_strikes
    assert tick_decision is not None
    assert tick_decision.state == "LAYER_2_ULTIMATUM"


def test_lockout_expiry_post_check_on_task_resets_to_on_task():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    queue = FakeCommandQueue()
    ui_hooks = FakeUIHooks()
    lockout_done_called = []

    sm = AgentStateMachine(
        "Study DSA",
        "strict",
        TEST_TIMINGS,
        cache,
        command_queue=queue,
        ui_hooks=ui_hooks,
        clock=clock,
        on_lockout_done=lambda: lockout_done_called.append(True),
    )

    ctx_bad = make_context("instagram.com", "Feed", is_browser=True, tab_ids=[101])
    sm.handle_verdict(ctx_bad, make_verdict(on_task=False))
    clock.advance(TEST_TIMINGS.grace_period_seconds + 1.0)
    sm.handle_tick()

    # Enter Layer 2 Strict
    sm.handle_verdict(ctx_bad, make_verdict(on_task=False, tabs_to_close=[101]))
    assert sm.state == "LAYER_2_ULTIMATUM"

    # Expire lockout timer
    clock.advance(TEST_TIMINGS.strict_lockout_seconds + 1.0)
    sm.handle_tick()
    assert len(lockout_done_called) == 1
    assert sm.state == "LAYER_2_ULTIMATUM"

    # Immediate check runs and confirms user is back ON-TASK
    ctx_good = make_context("leetcode.com", "Two Sum", is_browser=True, tab_ids=[102])
    v_good = make_verdict(on_task=True, reason="Back to studying")
    d_verified = sm.handle_verdict(ctx_good, v_good)

    # State machine must reset to ON_TASK, zero strikes, and hide overlays
    assert sm.state == "ON_TASK"
    assert sm.strikes == 0
    assert d_verified.action_taken == "cleared"
    assert ui_hooks.hide_calls >= 1


def test_lockout_expiry_post_check_off_task_returns_straight_to_strict_layer2():
    clock = FakeClock(100.0)
    cache = VerdictCache()
    queue = FakeCommandQueue()
    ui_hooks = FakeUIHooks()
    lockout_done_called = []

    sm = AgentStateMachine(
        "Study DSA",
        "strict",
        TEST_TIMINGS,
        cache,
        command_queue=queue,
        ui_hooks=ui_hooks,
        clock=clock,
        on_lockout_done=lambda: lockout_done_called.append(True),
    )

    ctx_bad = make_context("instagram.com", "Feed", is_browser=True, tab_ids=[101, 103], active_tab_id=101)
    sm.handle_verdict(ctx_bad, make_verdict(on_task=False))
    clock.advance(TEST_TIMINGS.grace_period_seconds + 1.0)
    sm.handle_tick()

    # Enter Layer 2 Strict
    sm.handle_verdict(ctx_bad, make_verdict(on_task=False, tabs_to_close=[101]))
    assert sm.state == "LAYER_2_ULTIMATUM"
    assert len(ui_hooks.strict_overlays) == 1
    strikes_before_recheck = sm.strikes

    # Expire lockout timer
    clock.advance(TEST_TIMINGS.strict_lockout_seconds + 1.0)
    sm.handle_tick()
    assert len(lockout_done_called) == 1
    assert sm.state == "LAYER_2_ULTIMATUM"

    # Immediate check runs and finds user is STILL OFF-TASK
    v_still_bad = make_verdict(on_task=False, reason="User opened another distracting tab", tabs_to_close=[103])
    d_recheck = sm.handle_verdict(ctx_bad, v_still_bad)

    # Must go STRAIGHT back to Layer 2 strict (no new grace period!)
    assert sm.state == "LAYER_2_ULTIMATUM"
    assert d_recheck.action_taken == "strict_block"
    assert sm.strikes > strikes_before_recheck
    assert d_recheck.lockout_seconds == TEST_TIMINGS.strict_lockout_seconds
    # Enqueued second tab for closing
    assert 103 in queue.enqueued_tabs
    # Show strict overlay was called again
    assert len(ui_hooks.strict_overlays) == 2


def test_grace_remaining_seconds_and_allow_domain_action():
    clock = FakeClock(500.0)
    cache = VerdictCache()
    quick_rules: list[tuple[str, str]] = []

    sm = AgentStateMachine(
        "Study DSA",
        "normal",
        TEST_TIMINGS,
        cache,
        clock=clock,
        on_quick_rule=lambda action, dom: quick_rules.append((action, dom)),
    )
    assert sm.grace_remaining_seconds() == 0.0

    ctx = make_context("neetcode.io", "NeetCode Roadmap", is_browser=True, tab_ids=[101])
    sm.handle_verdict(ctx, make_verdict(on_task=False, reason="Unrecognized site"))
    assert sm.state == "GRACE_PERIOD"
    assert sm.grace_remaining_seconds() == pytest.approx(TEST_TIMINGS.grace_period_seconds)

    clock.advance(2.0)
    assert sm.grace_remaining_seconds() == pytest.approx(TEST_TIMINGS.grace_period_seconds - 2.0)

    clock.advance(TEST_TIMINGS.grace_period_seconds)
    sm.handle_tick()
    assert sm.state == "LAYER_1_ASK"
    assert sm.grace_remaining_seconds() == 0.0

    decision = sm.handle_user_action("allow_domain")
    assert decision.state == "ON_TASK"
    assert decision.action_taken == "cleared"
    assert quick_rules == [("allow", "neetcode.io")]
    assert cache.get("neetcode.io", "NeetCode Roadmap") is not None
