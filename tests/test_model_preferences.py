"""Tests for 3-tier model preferences, automatic failover, and Tier 0 shift."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from app.cache import VerdictCache
from app.config import DEV_TIMINGS
from app.judge import (
    GeminiRateLimitError,
    ModelPreferenceManager,
    _extract_json_string,
    _is_rate_limit_error,
    _parse_verdict_response,
    evaluate_context,
    tier0_evaluate,
    tier1_evaluate,
)
from app.models import (
    ActivityContext,
    GoalProfile,
    JudgeVerdict,
    TabInfo,
    TabSnapshot,
    WindowInfo,
)
from app.state_machine import AgentStateMachine


def _make_context(
    source_key: str = "chrome.exe",
    title: str = "LeetCode - Two Sum",
    url: str = "https://leetcode.com/problems/two-sum",
    domain: str | None = None,
) -> ActivityContext:
    if domain is None:
        if "://" in url:
            domain = url.split("://", 1)[1].split("/", 1)[0]
        else:
            domain = url.split("/", 1)[0]
    tab = TabInfo(tab_id=1, window_id=10, url=url, title=title, active=True, domain=domain)
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
    assert "Gemma 4 26B A4B" in tier0_notifications[0]
    assert "Tier 0" in tier0_notifications[0]


def test_failover_on_404_timeout_network_exception():
    """Verify model failover triggers on 404 (not found), timeouts, and network exceptions."""
    mgr = ModelPreferenceManager(
        preferences=["gemini-3.5-flash-lite", "gemma-4-31b-it", "gemma-4-26b-a4b-it"],
    )

    # 1. Preference 1 fails on 404 (model not found)
    exc_404 = Exception("404 Model not found")
    setattr(exc_404, "code", 404)
    assert _is_rate_limit_error(exc_404)
    next_m = mgr.escalate_failure("gemini-3.5-flash-lite", exc_404)
    assert next_m == "gemma-4-31b-it"

    # 2. Preference 2 fails on TimeoutError
    exc_timeout = TimeoutError("Request timed out after 30s")
    assert _is_rate_limit_error(exc_timeout)
    next_m = mgr.escalate_failure("gemma-4-31b-it", exc_timeout)
    assert next_m == "gemma-4-26b-a4b-it"

    # 3. Preference 3 fails on ConnectionError (network failure)
    exc_conn = ConnectionError("Failed to establish network connection")
    assert _is_rate_limit_error(exc_conn)
    next_m = mgr.escalate_failure("gemma-4-26b-a4b-it", exc_conn)
    assert next_m is None
    assert mgr.tier0_mode_active


def test_strip_markdown_code_fences_before_json_parsing():
    """Verify markdown code fences (```json ... ```) are cleanly stripped before JSON parsing."""
    raw_markdown = """```json
{
  "activity": "Studying LeetCode Two Sum",
  "on_task": true,
  "confidence": 0.98,
  "reason": "User is solving algorithmic problem.",
  "tabs_to_close": []
}
```"""
    extracted = _extract_json_string(raw_markdown)
    assert extracted.startswith("{")
    assert extracted.endswith("}")
    assert "```" not in extracted

    ctx = _make_context()
    verdict = _parse_verdict_response(raw_markdown, ctx, tier_used=1)
    assert verdict.on_task is True
    assert verdict.confidence == 0.98
    assert verdict.activity == "Studying LeetCode Two Sum"

    # Also verify thinking / prose prefix followed by fences
    prose_fenced = """Here is my evaluation:
```json
{"activity": "Browsing memes", "on_task": false, "confidence": 0.95, "reason": "Distracted", "tabs_to_close": []}
```
Hope this helps!"""
    extracted2 = _extract_json_string(prose_fenced)
    assert extracted2 == '{"activity": "Browsing memes", "on_task": false, "confidence": 0.95, "reason": "Distracted", "tabs_to_close": []}'
    verdict2 = _parse_verdict_response(prose_fenced, ctx, tier_used=1)
    assert verdict2.on_task is False


def test_all_models_fail_shows_popup_once_and_sets_hud_status():
    """If every model in the chain fails, show Tier 0 popup once and set visible HUD status."""
    popup_calls: list[str] = []
    hud_updates: list[tuple[str, str]] = []

    class FakeUI:
        def show_tier0_shift_popup(self, msg: str) -> None:
            popup_calls.append(msg)

        def update_hud_status(self, state: str, summary: str) -> None:
            hud_updates.append((state, summary))

    ui = FakeUI()
    mgr = ModelPreferenceManager(
        preferences=["gemini-3.5-flash-lite", "gemma-4-31b-it", "gemma-4-26b-a4b-it"],
        on_shift_to_tier0=ui.show_tier0_shift_popup,
    )

    # Trigger failure across all 3 models
    mgr.escalate_failure("gemini-3.5-flash-lite", Exception("404 Not Found"))
    mgr.escalate_failure("gemma-4-31b-it", TimeoutError("timed out"))
    mgr.escalate_failure("gemma-4-26b-a4b-it", ConnectionError("network down"))

    # Popup shown exactly once
    assert len(popup_calls) == 1
    assert "Tier 0" in popup_calls[0]
    assert "Gemma 4 26B A4B" in popup_calls[0]

    # Attempting another failure does NOT fire popup again
    mgr.escalate_failure("gemma-4-26b-a4b-it", Exception("more errors"))
    assert len(popup_calls) == 1

    # Now run evaluate_context in Tier 0 mode
    cache = VerdictCache()
    profile = _make_profile()
    ctx = _make_context(title="Unknown Page", url="https://unknown-domain.org")

    verdict = evaluate_context(
        ctx=ctx,
        profile=profile,
        cache=cache,
        api_key="mock_key",
        force_vision=False,
        model_manager=mgr,
    )

    # Failed check must never silently count as on-task with no indication
    assert verdict.rate_limited is True
    assert "AI unavailable - rules only" in verdict.reason
    assert "AI unavailable" in verdict.activity

    # Pass verdict to state machine
    sm = AgentStateMachine(
        goal="Study DSA",
        mode="normal",
        timings=DEV_TIMINGS,
        cache=cache,
        ui_hooks=ui,
    )
    decision = sm.handle_verdict(ctx, verdict)
    assert decision.message == "AI unavailable - rules only"

    # State machine dispatches HUD update with "AI unavailable - rules only"
    assert ("ON_TASK", "AI unavailable - rules only") in hud_updates


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
