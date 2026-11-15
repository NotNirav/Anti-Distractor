"""Three-tier judge (Tier 0 rules/cache, Tier 1 text AI, Tier 2 vision AI) and review generator."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

import app.screen
from app.cache import VerdictCache
from app.config import (
    GEMINI_TEXT_MODEL,
    GEMINI_VISION_MODEL,
    PUNISH_CONFIDENCE_THRESHOLD,
    SCREENSHOT_MAX_WIDTH,
    TIER1_ESCALATION_CONFIDENCE,
)
from app.models import ActivityContext, GoalProfile, JudgeVerdict, ModeType
from app.profile import normalize_domain

logger = logging.getLogger(__name__)


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
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code == 429 or "RESOURCE_EXHAUSTED" in str(exc)


def _parse_verdict_response(raw_text: str | None, ctx: ActivityContext, tier_used: int) -> JudgeVerdict:
    """Parse and validate the JSON verdict string from Gemini, filtering out invented tab IDs."""
    if not raw_text or not raw_text.strip():
        raise ValueError(f"Empty response from Tier {tier_used} judge.")
    parsed = _JudgeVerdictResponse.model_validate_json(raw_text)
    valid_ids = {t.tab_id for t in ctx.tab_snapshot.tabs} if ctx.tab_snapshot and ctx.tab_snapshot.tabs else set()
    return JudgeVerdict(
        activity=parsed.activity,
        on_task=parsed.on_task,
        confidence=float(parsed.confidence),
        reason=parsed.reason,
        tabs_to_close=[t for t in (parsed.tabs_to_close or []) if t in valid_ids],
        tier_used=tier_used,
        from_cache=False,
        rate_limited=False,
    )


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


def domain_matches(domain: str, candidate_list: list[str]) -> bool:
    """Check if domain matches or is a subdomain of any domain in candidate_list."""
    norm = normalize_domain(domain) or domain.strip().lower()
    if not norm:
        return False
    for c in candidate_list:
        cnorm = normalize_domain(c) or c.strip().lower()
        if not cnorm:
            continue
        if norm == cnorm or norm.endswith("." + cnorm):
            return True
    return False


_domain_matches = domain_matches


def tier0_evaluate(
    ctx: ActivityContext,
    profile: GoalProfile,
    cache: VerdictCache,
) -> JudgeVerdict | None:
    """Evaluate using verdict cache and goal-profile allow/block lists; return None if ambiguous."""
    # 1. First, check in-memory VerdictCache (handles repeat visits and user overrides)
    cached = cache.get(ctx.source_key, ctx.display_title)
    if cached is not None:
        return cached

    # 2. Check website domain rules (only when browser is focused or no foreground window info)
    source = (ctx.source_key or "").strip().lower()
    is_browser_context = ctx.foreground_window is None or ctx.foreground_window.is_browser
    domain_candidate = ""
    if is_browser_context and ctx.tab_snapshot and ctx.tab_snapshot.tabs:
        active_tab = next(
            (t for t in ctx.tab_snapshot.tabs if t.tab_id == ctx.tab_snapshot.active_tab_id),
            None,
        ) or next(
            (t for t in ctx.tab_snapshot.tabs if t.active),
            ctx.tab_snapshot.tabs[0],
        )
        domain_candidate = active_tab.domain or normalize_domain(active_tab.url)
    if not domain_candidate and is_browser_context and not source.endswith(".exe"):
        domain_candidate = normalize_domain(source) or source

    if domain_candidate:
        # Borderline domains (e.g. youtube.com, reddit.com) require content inspection -> Tier 1/2
        if _domain_matches(domain_candidate, profile.borderline_domains):
            return None

        # Blocked domains -> instantaneous off-task verdict
        if _domain_matches(domain_candidate, profile.blocked_domains):
            tabs_to_close: list[int] = []
            if ctx.tab_snapshot and ctx.tab_snapshot.active_tab_id:
                tabs_to_close.append(ctx.tab_snapshot.active_tab_id)
            return JudgeVerdict(
                activity=f"Visiting blocked domain: {domain_candidate}",
                on_task=False,
                confidence=1.0,
                reason=f"{domain_candidate} is in the blocked domains list for goal '{profile.goal}'.",
                tabs_to_close=tabs_to_close,
                tier_used=0,
                from_cache=False,
                rate_limited=False,
            )

        # Allowed domains -> instantaneous on-task verdict
        if _domain_matches(domain_candidate, profile.allowed_domains):
            return JudgeVerdict(
                activity=f"Browsing allowed domain: {domain_candidate}",
                on_task=True,
                confidence=1.0,
                reason=f"{domain_candidate} is directly on-task for goal '{profile.goal}'.",
                tabs_to_close=[],
                tier_used=0,
                from_cache=False,
                rate_limited=False,
            )

    # 3. Check desktop application rules
    proc = (
        ctx.foreground_window.process_name if ctx.foreground_window else ctx.source_key
    ).lower()
    if proc:
        proc_clean = proc.strip().lower()
        if any(proc_clean == b.lower() for b in profile.blocked_apps):
            return JudgeVerdict(
                activity=f"Using blocked application: {proc}",
                on_task=False,
                confidence=1.0,
                reason=f"{proc} is in the blocked apps list for goal '{profile.goal}'.",
                tabs_to_close=[],
                tier_used=0,
                from_cache=False,
                rate_limited=False,
            )

        if any(proc_clean == a.lower() for a in profile.allowed_apps):
            return JudgeVerdict(
                activity=f"Working in allowed application: {proc}",
                on_task=True,
                confidence=1.0,
                reason=f"{proc} is in the allowed apps list for goal '{profile.goal}'.",
                tabs_to_close=[],
                tier_used=0,
                from_cache=False,
                rate_limited=False,
            )

    # 4. Unknown or ambiguous -> return None to escalate to Tier 1
    return None


def tier1_evaluate(
    ctx: ActivityContext,
    profile: GoalProfile,
    api_key: str,
) -> JudgeVerdict:
    """Call Gemini text model with metadata and open tabs/windows; returns strict JSON verdict."""
    if not api_key or not api_key.strip():
        raise ValueError("GEMINI_API_KEY is missing or empty.")

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
        return _parse_verdict_response(response.text, ctx, tier_used=1)
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
    if not api_key or not api_key.strip():
        raise ValueError("GEMINI_API_KEY is missing or empty.")

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
        return _parse_verdict_response(response.text, ctx, tier_used=2)
    except Exception as e:
        if _is_rate_limit_error(e):
            raise GeminiRateLimitError(str(e)) from e
        raise


def _apply_confidence_gate(verdict: JudgeVerdict) -> JudgeVerdict:
    """Never punish uncertainty: treat low-confidence off-task verdicts as on-task."""
    if not verdict.on_task and verdict.confidence < PUNISH_CONFIDENCE_THRESHOLD:
        return replace(
            verdict,
            on_task=True,
            tabs_to_close=[],
            reason=(
                f"{verdict.reason} "
                f"(Treated as on-task due to low confidence {verdict.confidence:.2f} < {PUNISH_CONFIDENCE_THRESHOLD:.2f})"
            ).strip(),
        )
    return verdict


def _safe_fallback(
    ctx: ActivityContext,
    profile: GoalProfile,
    cache: VerdictCache,
    reason_prefix: str,
    rate_limited: bool = True,
) -> JudgeVerdict:
    """Fall back to Tier 0 rules, or safely treat unknown context as on-task."""
    tier0_verdict = tier0_evaluate(ctx, profile, cache)
    if tier0_verdict is not None:
        return replace(tier0_verdict, rate_limited=rate_limited)
    label = ctx.display_title or ctx.source_key or "current activity"
    return JudgeVerdict(
        activity=f"Fallback: {label}",
        on_task=True,
        confidence=0.0,
        reason=f"{reason_prefix}; treating as on-task without punishing.",
        tabs_to_close=[],
        tier_used=0,
        from_cache=False,
        rate_limited=rate_limited,
    )


def _cache_if_confident(ctx: ActivityContext, verdict: JudgeVerdict, cache: VerdictCache) -> None:
    """Cache Tier 1/2 verdicts that meet the confidence threshold."""
    if (
        not verdict.rate_limited
        and verdict.tier_used in (1, 2)
        and verdict.confidence >= PUNISH_CONFIDENCE_THRESHOLD
        and (ctx.source_key or ctx.display_title)
    ):
        cache.put(ctx.source_key, ctx.display_title, verdict)


def evaluate_context(
    ctx: ActivityContext,
    profile: GoalProfile,
    cache: VerdictCache,
    api_key: str,
    force_vision: bool = False,
) -> JudgeVerdict:
    """Run Tier 0 -> Tier 1 -> Tier 2 escalation with confidence gating and rate-limit fallback."""
    if not force_vision:
        tier0_verdict = tier0_evaluate(ctx, profile, cache)
        if tier0_verdict is not None:
            return _apply_confidence_gate(tier0_verdict)

        try:
            tier1_verdict = tier1_evaluate(ctx, profile, api_key)
        except GeminiRateLimitError:
            logger.warning("Rate limit hit during Tier 1 evaluation; using Tier 0 / on-task fallback.")
            return _safe_fallback(
                ctx, profile, cache, "Gemini API rate limit hit at Tier 1", rate_limited=True
            )
        except Exception as exc:
            logger.warning("Tier 1 evaluation failed (%s); falling back to safe verdict.", exc)
            return _safe_fallback(
                ctx, profile, cache, f"Tier 1 evaluation failed ({exc})", rate_limited=False
            )

        if tier1_verdict.confidence < TIER1_ESCALATION_CONFIDENCE:
            try:
                screenshot_bytes = app.screen.capture_screenshot_bytes(
                    max_width=SCREENSHOT_MAX_WIDTH
                )
            except Exception as exc:
                logger.warning(
                    "Screenshot capture failed during Tier 2 escalation (%s); falling back to Tier 1.",
                    exc,
                )
                gated = _apply_confidence_gate(tier1_verdict)
                _cache_if_confident(ctx, gated, cache)
                return gated

            try:
                tier2_verdict = tier2_evaluate(ctx, profile, screenshot_bytes, api_key)
            except GeminiRateLimitError:
                logger.warning("Rate limit hit during Tier 2 escalation; using Tier 0 / on-task fallback.")
                return _safe_fallback(
                    ctx, profile, cache, "Gemini API rate limit hit at Tier 2", rate_limited=True
                )
            except Exception as exc:
                logger.warning(
                    "Tier 2 escalation failed (%s); falling back to Tier 1.",
                    exc,
                )
                gated = _apply_confidence_gate(tier1_verdict)
                _cache_if_confident(ctx, gated, cache)
                return gated

            final_verdict = _apply_confidence_gate(tier2_verdict)
            _cache_if_confident(ctx, final_verdict, cache)
            return final_verdict

        final_verdict = _apply_confidence_gate(tier1_verdict)
        _cache_if_confident(ctx, final_verdict, cache)
        return final_verdict

    # force_vision=True (baseline, dwell, heartbeat, manual_recheck): bypass cache and run Tier 2
    try:
        screenshot_bytes = app.screen.capture_screenshot_bytes(
            max_width=SCREENSHOT_MAX_WIDTH
        )
    except Exception as exc:
        logger.warning(
            "Screenshot capture failed during forced vision check (%s); falling back to Tier 1.",
            exc,
        )
        try:
            tier1_verdict = tier1_evaluate(ctx, profile, api_key)
            final_verdict = _apply_confidence_gate(tier1_verdict)
            _cache_if_confident(ctx, final_verdict, cache)
            return final_verdict
        except GeminiRateLimitError:
            logger.warning("Rate limit hit during Tier 1 fallback after screenshot failure.")
            return _safe_fallback(
                ctx,
                profile,
                cache,
                "Screenshot failed and Tier 1 hit rate limit",
                rate_limited=True,
            )
        except Exception as tier1_exc:
            logger.warning(
                "Tier 1 fallback failed after screenshot failure (%s); treating as on-task.",
                tier1_exc,
            )
            return _safe_fallback(
                ctx,
                profile,
                cache,
                "Screenshot failed and Tier 1 unavailable",
                rate_limited=False,
            )

    try:
        tier2_verdict = tier2_evaluate(ctx, profile, screenshot_bytes, api_key)
    except GeminiRateLimitError:
        logger.warning("Rate limit hit during forced Tier 2 vision check; using Tier 0 / on-task fallback.")
        return _safe_fallback(
            ctx, profile, cache, "Gemini API rate limit hit at Tier 2", rate_limited=True
        )
    except Exception as exc:
        logger.warning(
            "Forced Tier 2 vision check failed (%s); falling back to Tier 1.",
            exc,
        )
        try:
            tier1_verdict = tier1_evaluate(ctx, profile, api_key)
            final_verdict = _apply_confidence_gate(tier1_verdict)
            _cache_if_confident(ctx, final_verdict, cache)
            return final_verdict
        except GeminiRateLimitError:
            return _safe_fallback(
                ctx,
                profile,
                cache,
                "Tier 2 failed and Tier 1 hit rate limit",
                rate_limited=True,
            )
        except Exception as tier1_exc:
            return _safe_fallback(
                ctx,
                profile,
                cache,
                f"Tier 2 and Tier 1 failed ({tier1_exc})",
                rate_limited=False,
            )

    final_verdict = _apply_confidence_gate(tier2_verdict)
    _cache_if_confident(ctx, final_verdict, cache)
    return final_verdict


def generate_session_review(
    goal: str,
    mode: ModeType,
    logs: list[dict[str, Any]],
    api_key: str,
) -> str:
    """Summarize the session's text-only SQLite activity logs into a concise user review."""
    total = len(logs)
    on_task_count = sum(1 for l in logs if bool(l.get("on_task")))
    pct = int((on_task_count / total) * 100) if total > 0 else 0
    fallback_summary = f"Session summary for '{goal}' ({mode} mode): {pct}% on-task across {total} checks."

    if not logs:
        return f"Session completed for goal: '{goal}' ({mode} mode). No activity recorded."

    if not api_key or not api_key.strip():
        return fallback_summary

    log_lines = []
    for l in logs:
        is_on = bool(l.get("on_task"))
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
        return (response.text or "").strip() or fallback_summary
    except Exception:
        return fallback_summary
