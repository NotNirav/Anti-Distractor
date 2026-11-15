"""Deterministic agent state machine driving Normal and Strict mode interventions."""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Callable, Literal, Protocol

from app.cache import VerdictCache
from app.config import TimingConfig
from app.models import (
    ActivityContext,
    AgentDecision,
    AgentStateName,
    JudgeVerdict,
    ModeType,
)
from app.server import CommandQueue

logger = logging.getLogger(__name__)


class UIHooks(Protocol):
    """Thread-safe interface used to drive Tkinter popups, overlays, and HUD status."""

    def show_setup(self, on_start: Callable[[str, int, ModeType], None]) -> None: ...
    def show_monitoring_hud(
        self, goal: str, mode: ModeType, on_end_session: Callable[[], None]
    ) -> None: ...
    def update_hud_status(self, state: AgentStateName, summary: str) -> None: ...
    def show_layer1_popup(
        self,
        goal: str,
        reason: str,
        on_choice: Callable[[Literal["back_to_work", "relevant"]], None],
    ) -> None: ...
    def show_layer2_warning(self, goal: str, reason: str) -> None: ...
    def show_strict_overlay(
        self,
        goal: str,
        reason: str,
        lockout_seconds: float,
        on_lockout_done: Callable[[], None],
    ) -> None: ...
    def hide_popups_and_overlay(self) -> None: ...
    def show_review(self, review_text: str, on_close: Callable[[], None]) -> None: ...


class AgentStateMachine:
    """Deterministic state machine converting judge verdicts into Layer 1/2 and strict actions."""

    def __init__(
        self,
        goal: str,
        mode: ModeType,
        timings: TimingConfig,
        cache: VerdictCache,
        command_queue: CommandQueue | None = None,
        on_decision: Callable[[AgentDecision], None] | None = None,
        ui_hooks: UIHooks | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_override: Callable[[ActivityContext | None, str], None] | None = None,
    ) -> None:
        self.goal = goal
        self.mode: ModeType = mode
        self.timings = timings
        self.cache = cache
        self.command_queue = command_queue
        self.on_decision = on_decision
        self.ui_hooks = ui_hooks
        self._clock = clock
        self.on_override = on_override

        self._lock = threading.RLock()
        self._state: AgentStateName = "IDLE"
        self._strikes: int = 0
        self._grace_deadline: float | None = None
        self._last_off_task_ctx: ActivityContext | None = None
        self._last_off_task_verdict: JudgeVerdict | None = None
        self._pending_overrides: set[tuple[str, str]] = set()
        self._override_log: list[dict[str, Any]] = []

    @property
    def state(self) -> AgentStateName:
        with self._lock:
            return self._state

    @property
    def strikes(self) -> int:
        with self._lock:
            return self._strikes

    @property
    def override_log(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._override_log)

    def handle_verdict(
        self, ctx: ActivityContext | None, verdict: JudgeVerdict | None
    ) -> AgentDecision:
        """Advance state machine for a new verdict and return the resulting AgentDecision."""
        parsed = self._parse_verdict(verdict)
        if parsed is None:
            logger.warning("Ignoring invalid or missing verdict: %r", verdict)
            with self._lock:
                current_state = self._state
            return AgentDecision(
                state=current_state,
                action_taken="none",
                message="Ignored invalid or missing verdict.",
                tabs_to_close=[],
            )

        on_task, reason, from_cache, sanitized_verdict = parsed
        now = self._now()
        source_key, display_title = self._extract_source_and_title(ctx)
        cache_key = self._safe_cache_key(source_key, display_title)

        ui_action: tuple[str, str] | None = None
        decision: AgentDecision

        with self._lock:
            if self._state == "ENDED":
                return AgentDecision(
                    state="ENDED",
                    action_taken="none",
                    message="Session ended.",
                    tabs_to_close=[],
                )

            if on_task:
                prev_state = self._state
                self._strikes = 0
                self._grace_deadline = None
                self._last_off_task_ctx = None
                self._last_off_task_verdict = None
                if not from_cache and cache_key in self._pending_overrides:
                    self._pending_overrides.discard(cache_key)
                self._state = "ON_TASK"

                if prev_state in ("GRACE_PERIOD", "LAYER_1_ASK", "LAYER_2_ULTIMATUM"):
                    action_taken = "cleared"
                    message = "Back on task; cleared warnings."
                    ui_action = ("hide", message)
                else:
                    action_taken = "none"
                    message = "On task."
                    ui_action = ("hud", message)

                decision = AgentDecision(
                    state="ON_TASK",
                    action_taken=action_taken,
                    message=message,
                    tabs_to_close=[],
                )
            else:
                self._last_off_task_ctx = ctx
                self._last_off_task_verdict = sanitized_verdict

                # Fresh model disagreement after a user "relevant" override jumps straight to Layer 2.
                override_disagreed = (not from_cache) and (
                    cache_key in self._pending_overrides
                )

                if override_disagreed:
                    self._pending_overrides.discard(cache_key)
                    self._grace_deadline = None
                    self._strikes = max(self._strikes + 1, 2)
                    decision, ui_action = self._transition_to_layer2_locked(
                        ctx, sanitized_verdict, reason
                    )
                elif self._state in ("IDLE", "ON_TASK"):
                    grace_seconds = self._safe_grace_seconds()
                    self._state = "GRACE_PERIOD"
                    self._grace_deadline = now + grace_seconds
                    message = f"Off-task detected ({reason}); grace period started."
                    ui_action = ("hud", message)
                    decision = AgentDecision(
                        state="GRACE_PERIOD",
                        action_taken="grace_started",
                        message=message,
                        tabs_to_close=[],
                    )
                elif self._state == "GRACE_PERIOD":
                    if self._grace_deadline is not None and now >= self._grace_deadline:
                        self._grace_deadline = None
                        self._strikes += 1
                        self._state = "LAYER_1_ASK"
                        message = f"Are you studying {self.goal}? Go back."
                        ui_action = ("layer1", reason)
                        decision = AgentDecision(
                            state="LAYER_1_ASK",
                            action_taken="ask_popup",
                            message=message,
                            tabs_to_close=[],
                        )
                    else:
                        message = f"Still in grace period ({reason})."
                        decision = AgentDecision(
                            state="GRACE_PERIOD",
                            action_taken="none",
                            message=message,
                            tabs_to_close=[],
                        )
                elif self._state in ("LAYER_1_ASK", "LAYER_2_ULTIMATUM"):
                    self._grace_deadline = None
                    self._strikes += 1
                    decision, ui_action = self._transition_to_layer2_locked(
                        ctx, sanitized_verdict, reason
                    )
                else:
                    decision = AgentDecision(
                        state=self._state,
                        action_taken="none",
                        message=reason,
                        tabs_to_close=[],
                    )

        self._dispatch_side_effects(decision, ui_action)
        return decision

    def handle_tick(self, now: float | None = None) -> AgentDecision | None:
        """Check grace period and strict-mode lockout expirations on a periodic tick."""
        current_time = self._now(now)
        ui_action: tuple[str, str] | None = None
        decision: AgentDecision | None = None

        with self._lock:
            if self._state == "ENDED":
                return None

            if (
                self._state == "GRACE_PERIOD"
                and self._grace_deadline is not None
                and current_time >= self._grace_deadline
            ):
                self._grace_deadline = None
                self._strikes += 1
                self._state = "LAYER_1_ASK"
                reason = self._extract_last_reason()
                message = f"Are you studying {self.goal}? Go back."
                ui_action = ("layer1", reason)
                decision = AgentDecision(
                    state="LAYER_1_ASK",
                    action_taken="ask_popup",
                    message=message,
                    tabs_to_close=[],
                )

        if decision is not None:
            self._dispatch_side_effects(decision, ui_action)
        return decision

    def handle_user_action(
        self, action: Literal["back_to_work", "relevant"]
    ) -> AgentDecision:
        """Handle user button clicks from Layer 1 popup, updating overrides and cache."""
        now = self._now()
        override_ctx: ActivityContext | None = None
        override_source = ""
        override_title = ""
        override_reason = ""
        should_record_override = False

        with self._lock:
            if self._state == "ENDED":
                return AgentDecision(
                    state="ENDED",
                    action_taken="none",
                    message="Session ended.",
                    tabs_to_close=[],
                )

            if action == "back_to_work":
                self._state = "ON_TASK"
                self._strikes = 0
                self._grace_deadline = None
                self._last_off_task_ctx = None
                self._last_off_task_verdict = None
                decision = AgentDecision(
                    state="ON_TASK",
                    action_taken="cleared",
                    message="User chose back to work.",
                    tabs_to_close=[],
                )
                ui_action: tuple[str, str] | None = ("hide", decision.message)
            elif action == "relevant":
                override_ctx = self._last_off_task_ctx
                override_source, override_title = self._extract_source_and_title(
                    override_ctx
                )
                override_reason = f"User marked relevant for {self.goal}"
                if override_source or override_title:
                    key = self._safe_cache_key(override_source, override_title)
                    self._pending_overrides.add(key)
                    should_record_override = True
                self._override_log.append(
                    {
                        "timestamp": now,
                        "source_key": override_source,
                        "title": override_title,
                        "reason": override_reason,
                    }
                )

                self._state = "ON_TASK"
                self._strikes = 0
                self._grace_deadline = None
                self._last_off_task_ctx = None
                self._last_off_task_verdict = None
                decision = AgentDecision(
                    state="ON_TASK",
                    action_taken="cleared",
                    message="User marked activity relevant; override logged.",
                    tabs_to_close=[],
                )
                ui_action = ("hide", decision.message)
            else:
                logger.warning("Ignoring unknown user action: %r", action)
                return AgentDecision(
                    state=self._state,
                    action_taken="none",
                    message=f"Unknown user action: {action!r}",
                    tabs_to_close=[],
                )

        if action == "relevant":
            logger.info(
                "User override logged: source=%r title=%r reason=%r",
                override_source,
                override_title,
                override_reason,
            )
            if should_record_override and self.cache is not None:
                try:
                    self.cache.record_override(
                        override_source, override_title, reason=override_reason
                    )
                except Exception:
                    logger.exception("Failed to record override in VerdictCache")
            if self.on_override is not None:
                try:
                    self.on_override(override_ctx, override_reason)
                except Exception:
                    logger.exception("on_override callback failed")

        self._dispatch_side_effects(decision, ui_action)
        return decision

    def end_session(self) -> AgentDecision:
        """Transition state machine to ENDED and clear active warnings/overlays."""
        with self._lock:
            self._state = "ENDED"
            self._strikes = 0
            self._grace_deadline = None
            self._last_off_task_ctx = None
            self._last_off_task_verdict = None
            self._pending_overrides.clear()
            decision = AgentDecision(
                state="ENDED",
                action_taken="cleared",
                message="Session ended.",
                tabs_to_close=[],
            )

        self._dispatch_side_effects(decision, ("hide", decision.message))
        return decision

    def _transition_to_layer2_locked(
        self,
        ctx: ActivityContext | None,
        verdict: JudgeVerdict,
        reason: str,
    ) -> tuple[AgentDecision, tuple[str, str]]:
        """Enter LAYER_2_ULTIMATUM (must be called with self._lock held)."""
        self._state = "LAYER_2_ULTIMATUM"
        if self.mode == "strict":
            return self._enforce_strict_mode(ctx, verdict, reason)
        decision = AgentDecision(
            state="LAYER_2_ULTIMATUM",
            action_taken="warn_popup",
            message=reason,
            tabs_to_close=[],
        )
        return decision, ("layer2", reason)

    def _enforce_strict_mode(
        self,
        ctx: ActivityContext | None,
        verdict: JudgeVerdict,
        reason: str,
    ) -> tuple[AgentDecision, tuple[str, str]]:
        """Plug-in point for Step 2 strict-mode enforcement.

        TODO(T4.1-step-2): Queue close_tabs in self.command_queue for browser tabs,
        resolve minimize_hwnd for non-browser foreground windows, set lockout_seconds,
        and trigger UIHooks.show_strict_overlay.
        """
        logger.warning(
            "Strict-mode enforcement is not implemented yet (Step 2); falling back to Layer 2 warning."
        )
        decision = AgentDecision(
            state="LAYER_2_ULTIMATUM",
            action_taken="warn_popup",
            message=reason,
            tabs_to_close=[],
        )
        return decision, ("layer2", reason)

    def _dispatch_side_effects(
        self,
        decision: AgentDecision,
        ui_action: tuple[str, str] | None,
    ) -> None:
        """Invoke UI hooks and on_decision outside self._lock to prevent deadlocks."""
        if self.ui_hooks is not None and ui_action is not None:
            kind, payload = ui_action
            try:
                if kind == "layer1" and hasattr(self.ui_hooks, "show_layer1_popup"):
                    self.ui_hooks.show_layer1_popup(
                        self.goal,
                        payload,
                        lambda choice: self.handle_user_action(choice),
                    )
                elif kind == "layer2" and hasattr(self.ui_hooks, "show_layer2_warning"):
                    self.ui_hooks.show_layer2_warning(self.goal, payload)
                elif kind == "hide" and hasattr(
                    self.ui_hooks, "hide_popups_and_overlay"
                ):
                    self.ui_hooks.hide_popups_and_overlay()

                if hasattr(self.ui_hooks, "update_hud_status"):
                    self.ui_hooks.update_hud_status(decision.state, decision.message)
            except Exception:
                logger.exception("UI hook dispatch failed for action %s", kind)

        if self.on_decision is not None:
            try:
                self.on_decision(decision)
            except Exception:
                logger.exception("on_decision callback failed")

    def _now(self, override: float | None = None) -> float:
        if (
            override is not None
            and isinstance(override, (int, float))
            and not isinstance(override, bool)
            and math.isfinite(override)
        ):
            return float(override)
        try:
            val = float(self._clock())
            if math.isfinite(val):
                return val
        except Exception:
            logger.exception("Injectable clock failed; falling back to time.monotonic")
        return time.monotonic()

    def _safe_grace_seconds(self) -> float:
        try:
            val = float(getattr(self.timings, "grace_period_seconds", 0.0))
            return val if math.isfinite(val) and val >= 0.0 else 0.0
        except Exception:
            return 0.0

    def _extract_last_reason(self) -> str:
        if self._last_off_task_verdict is not None:
            raw = getattr(self._last_off_task_verdict, "reason", None)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return "Off-task activity detected."

    @staticmethod
    def _extract_source_and_title(ctx: ActivityContext | None) -> tuple[str, str]:
        if ctx is None:
            return "", ""
        raw_source = getattr(ctx, "source_key", "")
        raw_title = getattr(ctx, "display_title", "")
        source = raw_source if isinstance(raw_source, str) else str(raw_source or "")
        title = raw_title if isinstance(raw_title, str) else str(raw_title or "")
        return source, title

    def _safe_cache_key(self, source_key: str, title: str) -> tuple[str, str]:
        if self.cache is not None and hasattr(self.cache, "make_key"):
            try:
                return self.cache.make_key(source_key, title)
            except Exception:
                logger.exception("VerdictCache.make_key failed; using fallback key")
        return (source_key.strip().lower(), title.strip().lower())

    @staticmethod
    def _parse_verdict(
        verdict: JudgeVerdict | None,
    ) -> tuple[bool, str, bool, JudgeVerdict] | None:
        if verdict is None:
            return None
        try:
            on_task = getattr(verdict, "on_task", None)
        except Exception:
            return None
        if not isinstance(on_task, bool):
            return None

        raw_reason = getattr(verdict, "reason", None)
        if isinstance(raw_reason, str) and raw_reason.strip():
            reason = raw_reason.strip()
        else:
            reason = "Off-task activity detected." if not on_task else "On task."

        from_cache = bool(getattr(verdict, "from_cache", False))

        raw_conf = getattr(verdict, "confidence", 0.0)
        if (
            isinstance(raw_conf, (int, float))
            and not isinstance(raw_conf, bool)
            and math.isfinite(raw_conf)
        ):
            confidence = float(raw_conf)
        else:
            confidence = 0.0

        raw_tabs = getattr(verdict, "tabs_to_close", None)
        tabs_to_close: list[int] = []
        if isinstance(raw_tabs, (list, tuple)):
            for item in raw_tabs:
                if isinstance(item, int) and not isinstance(item, bool):
                    tabs_to_close.append(item)

        raw_activity = getattr(verdict, "activity", "")
        activity = raw_activity if isinstance(raw_activity, str) else ""
        raw_tier = getattr(verdict, "tier_used", 0)
        tier_used = raw_tier if isinstance(raw_tier, int) and not isinstance(raw_tier, bool) else 0

        sanitized = JudgeVerdict(
            activity=activity,
            on_task=on_task,
            confidence=confidence,
            reason=reason,
            tabs_to_close=tabs_to_close,
            tier_used=tier_used,
            from_cache=from_cache,
            rate_limited=bool(getattr(verdict, "rate_limited", False)),
        )
        return on_task, reason, from_cache, sanitized
