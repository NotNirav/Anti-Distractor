"""Deterministic agent state machine driving Normal and Strict mode interventions."""

from __future__ import annotations

from typing import Callable, Literal

from app.cache import VerdictCache
from app.config import TimingConfig
from app.models import ActivityContext, AgentDecision, JudgeVerdict, ModeType
from app.server import CommandQueue


class AgentStateMachine:
    """Deterministic state machine converting judge verdicts into Layer 1/2 and strict actions."""

    def __init__(
        self,
        goal: str,
        mode: ModeType,
        timings: TimingConfig,
        cache: VerdictCache,
        command_queue: CommandQueue,
        on_decision: Callable[[AgentDecision], None],
    ) -> None:
        # TODO(T4.1): Initialize state, strike counter, grace/lockout deadlines, and last off-task context.
        self.goal = goal
        self.mode = mode
        self.timings = timings
        self.cache = cache
        self.command_queue = command_queue
        self.on_decision = on_decision

    def handle_verdict(self, ctx: ActivityContext, verdict: JudgeVerdict) -> AgentDecision:
        """Advance state machine for a new verdict and return the resulting AgentDecision."""
        raise NotImplementedError("TODO(T4.1): Implement AgentStateMachine.handle_verdict")

    def handle_tick(self, now: float | None = None) -> AgentDecision | None:
        """Check grace period and strict-mode lockout expirations on a periodic tick."""
        raise NotImplementedError("TODO(T4.1): Implement AgentStateMachine.handle_tick")

    def handle_user_action(self, action: Literal["back_to_work", "relevant"]) -> AgentDecision:
        """Handle user button clicks from Layer 1 popup, updating overrides and cache."""
        raise NotImplementedError("TODO(T4.1): Implement AgentStateMachine.handle_user_action")

    def end_session(self) -> AgentDecision:
        """Transition state machine to ENDED and clear active warnings/overlays."""
        raise NotImplementedError("TODO(T4.1): Implement AgentStateMachine.end_session")
