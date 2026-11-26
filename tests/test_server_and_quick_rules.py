"""Tests for FastAPI bridge /status, /commands session piggyback, and POST /rules/quick."""

from __future__ import annotations

from typing import Literal
from fastapi.testclient import TestClient

from app.judge import domain_matches
from app.models import GoalProfile
from app.server import CommandQueue, TabSnapshotStore, create_app


def test_status_classification_and_quick_rules_endpoint() -> None:
    store = TabSnapshotStore()
    queue = CommandQueue()
    profile = GoalProfile(
        goal="Study DSA",
        on_task_topics=["algorithms"],
        allowed_domains=["leetcode.com"],
        borderline_domains=["youtube.com"],
        blocked_domains=["instagram.com"],
        allowed_apps=["code.exe"],
        blocked_apps=["steam.exe"],
    )

    def _classify(dom: str | None) -> str:
        if not dom:
            return "neutral"
        if domain_matches(dom, profile.allowed_domains):
            return "allowed"
        if domain_matches(dom, profile.borderline_domains):
            return "borderline"
        if domain_matches(dom, profile.blocked_domains):
            return "blocked"
        return "neutral"

    def _telemetry(dom: str | None) -> dict:
        return {
            "session_active": True,
            "goal": profile.goal,
            "mode": "strict",
            "state": "GRACE_PERIOD",
            "paused": False,
            "remaining_seconds": 1200.0,
            "pause_remaining_seconds": 0.0,
            "domain": dom or "",
            "domain_rule": _classify(dom),
        }

    def _quick_rule(action: Literal["allow", "block"], norm_domain: str) -> dict:
        profile.allowed_domains = [d for d in profile.allowed_domains if d != norm_domain]
        profile.borderline_domains = [d for d in profile.borderline_domains if d != norm_domain]
        profile.blocked_domains = [d for d in profile.blocked_domains if d != norm_domain]
        if action == "allow":
            profile.allowed_domains.append(norm_domain)
        else:
            profile.blocked_domains.append(norm_domain)
        return {"ok": True, "domain_rule": _classify(norm_domain)}

    app = create_app(
        tab_store=store,
        command_queue=queue,
        session_status_provider=lambda: (True, profile.goal),
        telemetry_provider=_telemetry,
        on_quick_rule=_quick_rule,
    )
    client = TestClient(app)

    # 1. /status classifies allowed, borderline, blocked, and neutral domains
    r_allow = client.get("/status", params={"domain": "https://www.leetcode.com/problems/two-sum"})
    assert r_allow.status_code == 200
    assert r_allow.json()["domain_rule"] == "allowed"
    assert store.is_extension_connected() is True

    r_border = client.get("/status", params={"domain": "m.youtube.com"})
    assert r_border.json()["domain_rule"] == "borderline"

    r_block = client.get("/status", params={"domain": "instagram.com"})
    assert r_block.json()["domain_rule"] == "blocked"

    r_neutral = client.get("/status", params={"domain": "arxiv.org"})
    assert r_neutral.json()["domain_rule"] == "neutral"

    # 2. /commands piggybacks lightweight session state for toolbar badge
    r_cmds = client.get("/commands")
    assert r_cmds.status_code == 200
    body = r_cmds.json()
    assert body["commands"] == []
    assert body["session"] == {
        "session_active": True,
        "state": "GRACE_PERIOD",
        "paused": False,
    }

    # 3. POST /rules/quick moves youtube.com from borderline -> allowed, then -> blocked
    r_quick1 = client.post("/rules/quick", json={"action": "allow", "domain": "https://www.youtube.com/watch?v=1"})
    assert r_quick1.status_code == 200
    assert r_quick1.json()["domain"] == "youtube.com"
    assert r_quick1.json()["domain_rule"] == "allowed"
    assert "youtube.com" in profile.allowed_domains
    assert "youtube.com" not in profile.borderline_domains

    r_quick2 = client.post("/rules/quick", json={"action": "block", "domain": "youtube.com"})
    assert r_quick2.status_code == 200
    assert r_quick2.json()["domain_rule"] == "blocked"
    assert "youtube.com" in profile.blocked_domains
    assert "youtube.com" not in profile.allowed_domains

    # 4. Rejects non-web / empty domains with HTTP 400
    r_bad = client.post("/rules/quick", json={"action": "allow", "domain": "chrome://extensions"})
    assert r_bad.status_code == 400

    r_empty = client.post("/rules/quick", json={"action": "block", "domain": "   "})
    assert r_empty.status_code == 400
