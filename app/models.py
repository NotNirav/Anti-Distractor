"""Shared data models for browser tabs, windows, judge verdicts, and state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ModeType = Literal["normal", "strict"]
TriggerType = Literal[
    "baseline",
    "tab_activated",
    "tab_updated",
    "tab_created",
    "tab_removed",
    "window_changed",
    "heartbeat",
    "dwell",
    "manual_recheck",
]
AgentStateName = Literal[
    "IDLE",
    "ON_TASK",
    "GRACE_PERIOD",
    "LAYER_1_ASK",
    "LAYER_2_ULTIMATUM",
    "ENDED",
]


@dataclass
class TabInfo:
    """Single browser tab snapshot sent by the Chrome/Edge extension."""

    tab_id: int
    window_id: int
    title: str
    url: str
    domain: str
    active: bool
    audible: bool = False


@dataclass
class TabSnapshot:
    """Latest browser tab collection and focus metadata."""

    tabs: list[TabInfo]
    active_tab_id: int | None
    window_focused: bool
    updated_at: float


@dataclass
class WindowInfo:
    """Foreground desktop window metadata from pywin32/psutil."""

    hwnd: int
    title: str
    process_name: str
    pid: int
    is_browser: bool


@dataclass
class ActivityContext:
    """Normalized observation bundle passed into the three-tier judge."""

    trigger_type: TriggerType
    goal: str
    foreground_window: WindowInfo | None
    tab_snapshot: TabSnapshot | None
    open_windows: list[str]
    source_key: str
    display_title: str
    active_url: str = ""
    timestamp: float = 0.0


@dataclass
class GoalProfile:
    """Expanded semantic profile for the user's session goal."""

    goal: str
    on_task_topics: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    borderline_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    allowed_apps: list[str] = field(default_factory=list)
    blocked_apps: list[str] = field(default_factory=list)


@dataclass
class JudgeVerdict:
    """Unified output produced by Tier 0, Tier 1, or Tier 2."""

    activity: str
    on_task: bool
    confidence: float
    reason: str
    tabs_to_close: list[int]
    tier_used: int
    from_cache: bool = False
    rate_limited: bool = False
    model_used: str = ""


@dataclass
class AgentDecision:
    """Action instruction emitted by the state machine to the UI/executor."""

    state: AgentStateName
    action_taken: str
    message: str
    tabs_to_close: list[int]
    minimize_hwnd: int | None = None
    lockout_seconds: float = 0.0
