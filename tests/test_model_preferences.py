"""Tests for 3-tier model preferences, automatic failover, and Tier 0 shift."""

from __future__ import annotations

import pytest

from app.cache import VerdictCache
from app.judge import (
    GeminiRateLimitError,
    ModelPreferenceManager,
    evaluate_context,
    tier0_evaluate,
)
from app.models import (
    ActivityContext,
    GoalProfile,
    JudgeVerdict,
    TabInfo,
    TabSnapshot,
    WindowInfo,
)


def _make_context(
    source_key: str = "chrome.exe",
    title: str = "LeetCode - Two Sum",
    url: str = "https://leetcode.com/problems/two-sum",
) -> ActivityContext:
    tab = TabInfo(tab_id=1, window_id=10, url=url, title=title, active=True, domain="leetcode.com")
    snapshot = TabSnapshot(active_tab_id=1, window_focused=True, tabs=[tab], updated_at=100.0)
    win = WindowInfo(hwnd=100, title=title, process_name=source_key, pid=1234, is_browser=True)
    return ActivityContext(
        trigger_type="tab_activated",
        source_key=source_key,
        display_title=title,
        active_url=url,
        goal="Study DSA",
        tab_snapshot=snapshot,
        foreground_window=win,
        open_windows=[title],
    )


def _make_profile() -> GoalProfile:
    return GoalProfile(
        goal="Study DSA",
        on_task_topics=["binary search", "leetcode", "algorithms"],
        allowed_domains=["leetcode.com"],
        borderline_domains=["youtube.com"],
        blocked_domains=["instagram.com"],
        allowed_apps=["code.exe"],
        blocked_apps=["spotify.exe"],
    )


def test_model_preference_failover_sequence():
    """Verify failover: Preference 1 -> Preference 2 -> Preference 3 -> Tier 0 shift."""
    tier0_notifications: list[str] = []
    preference_changes: list[tuple[str, int]] = []

    mgr = ModelPreferenceManager(
        preferences=["gemini-3.5-flash-lite", "gemma-4-31b-it", "gemma-4-26b-a4b-it"],
        on_shift_to_tier0=lambda msg: tier0_notifications.append(msg),
        on_preference_changed=lambda m, idx: preference_changes.append((m, idx)),
    )

    # 1. Starts at Preference 1
    assert mgr.active_index == 0
    assert mgr.current_model == "gemini-3.5-flash-lite"
    assert not mgr.tier0_mode_active

    # 2. Preference 1 fails (429 rate limit)
    next_m = mgr.escalate_failure("gemini-3.5-flash-lite", Exception("429 RESOURCE_EXHAUSTED"))
    assert next_m == "gemma-4-31b-it"
    assert mgr.active_index == 1
    assert mgr.current_model == "gemma-4-31b-it"
    assert not mgr.tier0_mode_active
    assert preference_changes == [("gemma-4-31b-it", 1)]

    # 3. Preference 2 fails (503 high demand)
    next_m = mgr.escalate_failure("gemma-4-31b-it", Exception("503 High demand"))
    assert next_m == "gemma-4-26b-a4b-it"
    assert mgr.active_index == 2
    assert mgr.current_model == "gemma-4-26b-a4b-it"
    assert not mgr.tier0_mode_active
    assert preference_changes == [("gemma-4-31b-it", 1), ("gemma-4-26b-a4b-it", 2)]

    # 4. Preference 3 fails (internal error / exhausted)
    next_m = mgr.escalate_failure("gemma-4-26b-a4b-it", Exception("500 Internal error"))
    assert next_m is None
    assert mgr.current_model is None
    assert mgr.tier0_mode_active
    assert len(tier0_notifications) == 1
    assert "Tier 0" in tier0_notifications[0]


def test_evaluate_context_tier0_mode_active():
    """When tier0_mode_active is True, evaluate_context uses local rules and never calls AI."""
    mgr = ModelPreferenceManager(
        preferences=["gemini-3.5-flash-lite", "gemma-4-31b-it", "gemma-4-26b-a4b-it"],
    )
    mgr.tier0_mode_active = True

    cache = VerdictCache()
    profile = _make_profile()

    # Blocked domain (instagram.com) evaluated directly by Tier 0
    insta_ctx = _make_context(
        source_key="chrome.exe",
        title="Instagram",
        url="https://instagram.com/reels",
    )
    insta_tab = TabInfo(tab_id=2, window_id=10, url="https://instagram.com/reels", title="Instagram", active=True, domain="instagram.com")
    insta_ctx.tab_snapshot = TabSnapshot(active_tab_id=2, window_focused=True, tabs=[insta_tab], updated_at=100.0)

    verdict = evaluate_context(
        ctx=insta_ctx,
        profile=profile,
        cache=cache,
        api_key="mock_key",
        force_vision=False,
        model_manager=mgr,
    )

    assert verdict.on_task is False
    assert verdict.tier_used == 0
    assert 2 in verdict.tabs_to_close

    # Allowed domain (leetcode.com) evaluated directly by Tier 0
    leetcode_ctx = _make_context()
    verdict2 = evaluate_context(
        ctx=leetcode_ctx,
        profile=profile,
        cache=cache,
        api_key="mock_key",
        force_vision=False,
        model_manager=mgr,
    )
    assert verdict2.on_task is True
    assert verdict2.tier_used == 0


def test_model_preference_recovery():
    """Verify reset_preferences returns state to Preference 1."""
    mgr = ModelPreferenceManager(
        preferences=["gemini-3.5-flash-lite", "gemma-4-31b-it", "gemma-4-26b-a4b-it"],
    )
    # Force exhaustion
    mgr.escalate_failure("gemini-3.5-flash-lite", Exception("429"))
    mgr.escalate_failure("gemma-4-31b-it", Exception("503"))
    mgr.escalate_failure("gemma-4-26b-a4b-it", Exception("500"))
    assert mgr.tier0_mode_active

    # Recover
    mgr.reset_preferences()
    assert not mgr.tier0_mode_active
    assert mgr.active_index == 0
    assert mgr.current_model == "gemini-3.5-flash-lite"
