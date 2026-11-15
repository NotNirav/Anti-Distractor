"""Event engine coordinating debounce, dwell checks, heartbeat, and three-tier judge calls."""

from __future__ import annotations

from typing import Callable

from app.cache import VerdictCache
from app.config import TimingConfig
from app.models import ActivityContext, GoalProfile, JudgeVerdict, TriggerType
from app.server import TabSnapshotStore


class EventEngine:
    """Coordinates triggers, debouncing, borderline dwell checks, heartbeat, and judge calls."""

    def __init__(
        self,
        goal: str,
        profile: GoalProfile,
        cache: VerdictCache,
        tab_store: TabSnapshotStore,
        timings: TimingConfig,
        api_key: str,
        on_verdict: Callable[[ActivityContext, JudgeVerdict], None],
    ) -> None:
        # TODO(T3.3): Initialize thread locks, debounce timers, dwell tracking, and heartbeat thread.
        self.goal = goal
        self.profile = profile
        self.cache = cache
        self.tab_store = tab_store
        self.timings = timings
        self.api_key = api_key
        self.on_verdict = on_verdict

    def start(self) -> None:
        """Start timers (heartbeat, dwell monitor, debounce worker) and trigger baseline check."""
        raise NotImplementedError("TODO(T3.3): Implement EventEngine.start")

    def stop(self) -> None:
        """Stop all timers and worker threads cleanly."""
        raise NotImplementedError("TODO(T3.3): Implement EventEngine.stop")

    def submit_trigger(self, trigger_type: TriggerType) -> None:
        """Schedule a debounced evaluation for a tab or window trigger."""
        raise NotImplementedError("TODO(T3.3): Implement EventEngine.submit_trigger")

    def run_immediate_check(
        self,
        trigger_type: TriggerType = "manual_recheck",
        force_vision: bool = True,
    ) -> None:
        """Bypass debounce to immediately evaluate current context (e.g. baseline or lockout end)."""
        raise NotImplementedError("TODO(T3.3): Implement EventEngine.run_immediate_check")
