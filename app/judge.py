"""Three-tier judge (Tier 0 rules/cache, Tier 1 text AI, Tier 2 vision AI) and review generator."""

from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.cache import VerdictCache
from app.config import GEMINI_TEXT_MODEL, GEMINI_VISION_MODEL
from app.models import ActivityContext, GoalProfile, JudgeVerdict, ModeType


class GeminiRateLimitError(Exception):
    """Raised when the Gemini API returns a quota or rate-limit error (HTTP 429)."""


class _JudgeVerdictResponse(BaseModel):
    activity: str
    on_task: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    tabs_to_close: list[int] = Field(default_factory=list)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception represents an HTTP 429 or Gemini quota exhaustion."""
    msg = str(exc).lower()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429 or "429" in msg or "resource_exhausted" in msg or "quota" in msg or "rate limit" in msg:
        return True
    return False


def _format_context_text(ctx: ActivityContext, profile: GoalProfile) -> str:
    """Format ActivityContext and GoalProfile into a structured prompt."""
    tabs_desc = []
    if ctx.tab_snapshot and ctx.tab_snapshot.tabs:
        for t in ctx.tab_snapshot.tabs:
            active_marker = " [CURRENT ACTIVE TAB]" if t.active else ""
            tabs_desc.append(f"  - Tab ID {t.tab_id}: \"{t.title}\" ({t.url}){active_marker}")
    tabs_str = "\n".join(tabs_desc) if tabs_desc else "  (None reported)"

    open_wins_str = ", ".join(ctx.open_windows) if ctx.open_windows else "(None reported)"
    fg_title = ctx.foreground_window.title if ctx.foreground_window else ctx.display_title
    fg_proc = ctx.foreground_window.process_name if ctx.foreground_window else ctx.source_key

    return f"""User Focus Goal: "{ctx.goal}"
Allowed Topics: {profile.on_task_topics}
Allowed Domains: {profile.allowed_domains}
Borderline Domains (require specific content check): {profile.borderline_domains}
Blocked Domains: {profile.blocked_domains}
Allowed Apps: {profile.allowed_apps}
Blocked Apps: {profile.blocked_apps}

Current System Observation:
- Trigger Event: {ctx.trigger_type}
- Active Source: {ctx.source_key}
- Active Window Title: "{fg_title}"
- Active Process: {fg_proc}
- Active URL: {ctx.active_url or "N/A"}
- Display Title: "{ctx.display_title}"
- Other Open Windows: {open_wins_str}

Browser Tabs:
{tabs_str}
"""


def tier0_evaluate(
    ctx: ActivityContext,
    profile: GoalProfile,
    cache: VerdictCache,
) -> JudgeVerdict | None:
    """Evaluate using verdict cache and goal-profile allow/block lists; return None if ambiguous."""
    # TODO(T3.2): Implement Tier 0 cache + GoalProfile allow/block matching.
    raise NotImplementedError("TODO(T3.2): Implement tier0_evaluate")


def tier1_evaluate(
    ctx: ActivityContext,
    profile: GoalProfile,
    api_key: str,
) -> JudgeVerdict:
    """Call Gemini text model with metadata and open tabs/windows; returns strict JSON verdict."""
    prompt = f"""You are the Anti-Distractor AI Judge. Your job is to evaluate whether the user is ON-TASK or OFF-TASK based on their stated goal and current desktop activity metadata.

{_format_context_text(ctx, profile)}

Instructions:
1. "activity": Brief summary of the user's current activity (e.g., "Reading LeetCode problem description", "Browsing social media on Instagram").
2. "on_task": True if the active tab/window directly serves the user's goal or allowed topics; False if distracting or irrelevant.
3. "confidence": Value from 0.0 to 1.0.
   - If the title or URL is ambiguous (e.g. YouTube video or Reddit thread where title alone doesn't clearly reveal content), give lower confidence (< 0.75) so the system can escalate to vision.
   - If definitively on-task or definitively off-task, give high confidence (0.85 - 1.0).
4. "reason": A single clear sentence justifying the verdict.
5. "tabs_to_close": List of tab IDs corresponding to distracting/off-task browser tabs that should be closed if strict mode is active.
"""
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_TEXT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_JudgeVerdictResponse,
            ),
        )
        data = json.loads(response.text)
        return JudgeVerdict(
            activity=data["activity"],
            on_task=bool(data["on_task"]),
            confidence=float(data["confidence"]),
            reason=data["reason"],
            tabs_to_close=[int(tid) for tid in data.get("tabs_to_close", [])],
            tier_used=1,
            from_cache=False,
            rate_limited=False,
        )
    except Exception as e:
        if _is_rate_limit_error(e):
            raise GeminiRateLimitError(str(e)) from e
        raise


def tier2_evaluate(
    ctx: ActivityContext,
    profile: GoalProfile,
    screenshot_jpeg: bytes,
    api_key: str,
) -> JudgeVerdict:
    """Call Gemini vision model with in-memory screenshot and context; returns strict JSON verdict."""
    prompt = f"""You are the Anti-Distractor AI Vision Judge.
Analyze the user's live screen capture alongside their stated goal and system metadata to determine if they are on-task or distracted.

{_format_context_text(ctx, profile)}

Visual Evaluation Instructions:
1. Examine the provided screenshot to see what is ACTUALLY rendered on screen (e.g. video contents, code editor text, social media feeds, chat messages).
2. "activity": Specific description of what is visible on screen.
3. "on_task": True if the visible content is relevant to "{ctx.goal}"; False if distracting or entertainment.
4. "confidence": Score between 0.0 and 1.0 representing certainty from visual inspection.
5. "reason": 1-2 sentence explanation referencing what is visually seen on screen.
6. "tabs_to_close": List of integer tab IDs to close if strict mode is triggered.
"""
    try:
        client = genai.Client(api_key=api_key)
        image_part = types.Part.from_bytes(data=screenshot_jpeg, mime_type="image/jpeg")
        response = client.models.generate_content(
            model=GEMINI_VISION_MODEL,
            contents=[image_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_JudgeVerdictResponse,
            ),
        )
        data = json.loads(response.text)
        return JudgeVerdict(
            activity=data["activity"],
            on_task=bool(data["on_task"]),
            confidence=float(data["confidence"]),
            reason=data["reason"],
            tabs_to_close=[int(tid) for tid in data.get("tabs_to_close", [])],
            tier_used=2,
            from_cache=False,
            rate_limited=False,
        )
    except Exception as e:
        if _is_rate_limit_error(e):
            raise GeminiRateLimitError(str(e)) from e
        raise


def evaluate_context(
    ctx: ActivityContext,
    profile: GoalProfile,
    cache: VerdictCache,
    api_key: str,
    force_vision: bool = False,
) -> JudgeVerdict:
    """Run Tier 0 -> Tier 1 -> Tier 2 escalation with confidence gating and rate-limit fallback."""
    # TODO(T3.3): Implement tiered escalation, confidence threshold gating, cache write, and fallback.
    raise NotImplementedError("TODO(T3.3): Implement evaluate_context")


def generate_session_review(
    goal: str,
    mode: ModeType,
    logs: list[dict[str, Any]],
    api_key: str,
) -> str:
    """Summarize the session's text-only SQLite activity logs into a concise user review."""
    if not logs:
        return f"Session completed for goal: '{goal}' ({mode} mode). No activity recorded."

    log_lines = []
    total = len(logs)
    on_task_count = 0
    for l in logs:
        is_on = bool(l.get("on_task"))
        if is_on:
            on_task_count += 1
        t = l.get("timestamp", 0)
        app = l.get("app_or_domain", "")
        title = l.get("title", "")
        action = l.get("action_taken", "none")
        override = " (user override)" if l.get("user_override") else ""
        log_lines.append(f"[{t}] OnTask={is_on} | App/Domain={app} | Title=\"{title}\" | Action={action}{override}")

    log_summary_text = "\n".join(log_lines)
    prompt = f"""You are the Anti-Distractor Focus Reviewer.
The user completed a focus session with goal: "{goal}" in {mode.upper()} mode.

Chronological session activity log:
{log_summary_text}

Write an engaging, constructive, and concise post-session review for the user:
- State total focus score / percentage on-task (e.g. "82% on-task").
- Highlight key accomplishments or focused streaks.
- Mention any distractions or drift episodes and how they were handled.
- Keep the tone encouraging, concise (2-3 short paragraphs or clean markdown bullet points), and actionable.
"""
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_VISION_MODEL,
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        if _is_rate_limit_error(e):
            raise GeminiRateLimitError(str(e)) from e
        pct = int((on_task_count / total) * 100) if total > 0 else 0
        return f"Session summary for '{goal}' ({mode} mode): {pct}% on-task across {total} checks."
