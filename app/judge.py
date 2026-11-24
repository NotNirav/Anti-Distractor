"""Three-tier judge (Tier 0 rules/cache, Tier 1 text AI, Tier 2 vision AI) and review generator."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
import os
import threading
import time
from typing import Any, Callable

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

import app.screen
from app.cache import VerdictCache
from app.config import (
    GEMINI_TEXT_MODEL,
    GEMINI_VISION_MODEL,
    MODEL_PREFERENCES,
    MODEL_PREFERENCE_1,
    MODEL_PREFERENCE_2,
    MODEL_PREFERENCE_3,
    PUNISH_CONFIDENCE_THRESHOLD,
    SCREENSHOT_MAX_WIDTH,
    TIER1_ESCALATION_CONFIDENCE,
    VISION_MIN_INTERVAL_SECONDS,
)
from app.models import ActivityContext, GoalProfile, JudgeVerdict, ModeType
from app.profile import normalize_domain

logger = logging.getLogger(__name__)


class GeminiRateLimitError(Exception):
    """Raised when the Gemini API returns a quota or rate-limit error (HTTP 429)."""


class ModelPreferenceManager:
    """Manages 3-tier model preferences with automatic failover and Tier 0 shift notification.

    Preference 1: Gemini 3.5 Flash-Lite
    Preference 2: Gemma 31B (gemma-4-31b-it)
    Preference 3: Gemma 4 26B A4B (gemma-4-26b-a4b-it)
    Last resort: Shift to Tier 0 rules only (no AI) with user-facing notification.
    """

    def __init__(
        self,
        preferences: list[str] | None = None,
        on_shift_to_tier0: Callable[[str], None] | None = None,
        on_preference_changed: Callable[[str, int], None] | None = None,
    ) -> None:
        self.preferences = list(preferences or MODEL_PREFERENCES)
        self.active_index = 0
        self.tier0_mode_active = False
        self.on_shift_to_tier0 = on_shift_to_tier0
        self.on_preference_changed = on_preference_changed
        self._lock = threading.Lock()
        self._tier0_notified = False

    @property
    def current_model(self) -> str | None:
        with self._lock:
            if self.tier0_mode_active or self.active_index >= len(self.preferences):
                return None
            return self.preferences[self.active_index]

    def reset_preferences(self) -> None:
        """Reset back to Preference 1 when AI is available again."""
        with self._lock:
            self.active_index = 0
            self.tier0_mode_active = False
            self._tier0_notified = False
            if self.on_preference_changed and self.preferences:
                try:
                    self.on_preference_changed(self.preferences[0], 0)
                except Exception:
                    logger.exception("Error in on_preference_changed callback")

    def escalate_failure(self, failed_model: str, exc: Exception) -> str | None:
        """Fail over to next preference, or shift to Tier 0 if all preferences exhausted."""
        with self._lock:
            logger.warning(
                "Model %s failed with %s. Attempting failover to next preference...",
                failed_model,
                exc,
            )
            if self.active_index < len(self.preferences) and self.preferences[self.active_index] == failed_model:
                self.active_index += 1

            if self.active_index < len(self.preferences):
                next_model = self.preferences[self.active_index]
                logger.info(
                    "Shifted model to Preference %d/%d: %s",
                    self.active_index + 1,
                    len(self.preferences),
                    next_model,
                )
                if self.on_preference_changed:
                    try:
                        self.on_preference_changed(next_model, self.active_index)
                    except Exception:
                        logger.exception("Error in on_preference_changed callback")
                return next_model

            # All preferences failed -> Shift to Tier 0 local rules only
            self.tier0_mode_active = True
            logger.warning(
                "All %d AI model preferences exhausted! Shifting to Tier 0 local rules only.",
                len(self.preferences),
            )
            if not self._tier0_notified:
                self._tier0_notified = True
                if self.on_shift_to_tier0:
                    try:
                        self.on_shift_to_tier0(
                            "All AI models (Gemini 3.5 Flash-Lite, Gemma 31B, Gemma 4 26B A4B) are currently "
                            "unavailable due to rate limits, timeouts, or capacity. Shifting to Tier 0 rules only "
                            "until an AI becomes available."
                        )
                    except Exception:
                        logger.exception("Error in on_shift_to_tier0 callback")
            return None

    def probe_recovery(self, api_key: str) -> bool:
        """Probe Preference 1 to see if it has recovered from rate limits."""
        with self._lock:
            if not self.tier0_mode_active and self.active_index == 0:
                return True
        try:
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=self.preferences[0],
                contents="ping",
            )
            if resp and getattr(resp, "text", None):
                logger.info("AI model %s is responsive again. Restoring AI evaluation.", self.preferences[0])
                self.reset_preferences()
                return True
        except Exception:
            pass
        return False


_global_model_manager = ModelPreferenceManager()


def get_model_manager() -> ModelPreferenceManager:
    return _global_model_manager


def set_model_manager(mgr: ModelPreferenceManager) -> None:
    global _global_model_manager
    _global_model_manager = mgr


class _JudgeVerdictResponse(BaseModel):
    activity: str
    on_task: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    tabs_to_close: list[int] = Field(default_factory=list)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception warrants failover: 404, 429, 500, 503, timeouts, network errors, or quota."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (404, 429, 500, 502, 503, 504):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    s = str(exc).lower()
    return any(
        k in s
        for k in (
            "not found",
            "404",
            "timeout",
            "timed out",
            "connection",
            "network",
            "resource_exhausted",
            "quota",
            "rate limit",
            "tokens are over",
            "token limit",
            "high demand",
            "unavailable",
            "internal error",
        )
    )


def _extract_json_string(text: str) -> str:
    """Extract valid JSON from raw text, stripping markdown code fences (```json ... ```) or extra prose."""
    if not text:
        return ""
    cleaned = text.strip()

    # Strip markdown code fences if wrapped
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    # If code fences are embedded inside prose
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _parse_verdict_response(
    raw_text: str | None, ctx: ActivityContext, tier_used: int, model_used: str = ""
) -> JudgeVerdict:
    """Parse and validate the JSON verdict string from Gemini or Gemma, filtering out invented tab IDs."""
    if not raw_text or not raw_text.strip():
        raise ValueError(f"Empty response from Tier {tier_used} judge.")
    json_str = _extract_json_string(raw_text)
    parsed = _JudgeVerdictResponse.model_validate_json(json_str)
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
        model_used=model_used,
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
    # 0. Never penalize or spend AI tokens evaluating Anti-Distractor's own windows
    if (
        ctx.foreground_window is not None
        and ctx.foreground_window.pid == os.getpid()
        and "anti-distractor" in (ctx.foreground_window.title or "").lower()
    ):
        return JudgeVerdict(
            activity="Using Anti-Distractor",
            on_task=True,
            confidence=1.0,
            reason="Anti-Distractor focus window.",
            tabs_to_close=[],
            tier_used=0,
            from_cache=False,
            rate_limited=False,
        )

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
    model: str | None = None,
    model_manager: ModelPreferenceManager | None = None,
) -> JudgeVerdict:
    """Call text model with metadata and open tabs/windows, respecting model preference hierarchy."""
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

    mgr = model_manager if model_manager is not None else get_model_manager()

    def _eval_with_model(model_name: str) -> JudgeVerdict:
        client = genai.Client(api_key=api_key)
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_JudgeVerdictResponse,
                ),
            )
        except Exception as e:
            if (
                "schema" in str(e).lower()
                or "invalid_argument" in str(e).lower()
                or "not supported" in str(e).lower()
                or getattr(e, "code", None) == 400
                or getattr(e, "status_code", None) == 400
            ):
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt + "\n\nCRITICAL: Respond ONLY with a valid JSON object matching the schema. No markdown formatting.",
                )
            else:
                raise
        raw_text = getattr(response, "text", None) or ""
        if not raw_text and hasattr(response, "candidates") and response.candidates:
            parts = response.candidates[0].content.parts
            text_parts = [p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)]
            raw_text = "".join(text_parts) if text_parts else "".join(p.text for p in parts if getattr(p, "text", None))
        return _parse_verdict_response(raw_text, ctx, tier_used=1, model_used=model_name)

    if model is not None:
        return _eval_with_model(model)

    if mgr.tier0_mode_active:
        raise GeminiRateLimitError("Tier 0 mode active (all AI models offline)")

    while True:
        target_model = mgr.current_model
        if target_model is None:
            raise GeminiRateLimitError("All AI models offline; shifted to Tier 0 rules.")
        try:
            return _eval_with_model(target_model)
        except Exception as exc:
            next_m = mgr.escalate_failure(target_model, exc)
            if next_m is None:
                raise GeminiRateLimitError(f"All AI model preferences exhausted; shifting to Tier 0. ({exc})") from exc
            continue


def tier2_evaluate(
    ctx: ActivityContext,
    profile: GoalProfile,
    screenshot_jpeg: bytes,
    api_key: str,
    model: str | None = None,
    model_manager: ModelPreferenceManager | None = None,
) -> JudgeVerdict:
    """Call vision model with in-memory screenshot and context, respecting model preference hierarchy."""
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

    mgr = model_manager if model_manager is not None else get_model_manager()

    def _eval_with_model(model_name: str) -> JudgeVerdict:
        client = genai.Client(api_key=api_key)
        image_part = types.Part.from_bytes(data=screenshot_jpeg, mime_type="image/jpeg")
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[image_part, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_JudgeVerdictResponse,
                ),
            )
        except Exception as e:
            if (
                "schema" in str(e).lower()
                or "invalid_argument" in str(e).lower()
                or "not supported" in str(e).lower()
                or getattr(e, "code", None) == 400
                or getattr(e, "status_code", None) == 400
            ):
                response = client.models.generate_content(
                    model=model_name,
                    contents=[image_part, prompt + "\n\nCRITICAL: Respond ONLY with a valid JSON object matching the schema. No markdown formatting."],
                )
            else:
                raise
        raw_text = getattr(response, "text", None) or ""
        if not raw_text and hasattr(response, "candidates") and response.candidates:
            parts = response.candidates[0].content.parts
            text_parts = [p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)]
            raw_text = "".join(text_parts) if text_parts else "".join(p.text for p in parts if getattr(p, "text", None))
        return _parse_verdict_response(raw_text, ctx, tier_used=2, model_used=model_name)

    if model is not None:
        return _eval_with_model(model)

    if mgr.tier0_mode_active:
        raise GeminiRateLimitError("Tier 0 mode active (all AI models offline)")

    while True:
        target_model = mgr.current_model
        if target_model is None:
            raise GeminiRateLimitError("All AI models offline; shifted to Tier 0 rules.")
        try:
            return _eval_with_model(target_model)
        except Exception as exc:
            next_m = mgr.escalate_failure(target_model, exc)
            if next_m is None:
                raise GeminiRateLimitError(f"All AI model preferences exhausted; shifting to Tier 0. ({exc})") from exc
            continue


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
    prefix = reason_prefix if "AI unavailable" in reason_prefix else f"AI unavailable - rules only; {reason_prefix}"
    return JudgeVerdict(
        activity=f"AI unavailable: {label}",
        on_task=True,
        confidence=0.0,
        reason=f"{prefix}; treating as on-task without punishing.",
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


_vision_lock = threading.Lock()
_last_vision_call_at: float | None = None


def reset_vision_cooldown() -> None:
    """Reset the global vision cooldown timestamp (for tests)."""
    global _last_vision_call_at
    with _vision_lock:
        _last_vision_call_at = None


def _try_acquire_vision_slot(min_interval: float | None = None) -> bool:
    """Enforce global minimum interval between Tier 2 vision calls."""
    global _last_vision_call_at
    interval = VISION_MIN_INTERVAL_SECONDS if min_interval is None else min_interval
    now = time.monotonic()
    with _vision_lock:
        if _last_vision_call_at is not None and (now - _last_vision_call_at) < interval:
            return False
        _last_vision_call_at = now
        return True


def evaluate_context(
    ctx: ActivityContext,
    profile: GoalProfile,
    cache: VerdictCache,
    api_key: str,
    force_vision: bool = False,
    model_manager: ModelPreferenceManager | None = None,
) -> JudgeVerdict:
    """Run Tier 0 -> Tier 1 -> Tier 2 escalation with model preference failover, confidence gating, and Tier 0 shift."""
    mgr = model_manager if model_manager is not None else get_model_manager()

    # If all AI models are offline, shift exclusively to Tier 0 local rules
    if mgr.tier0_mode_active:
        tier0_verdict = tier0_evaluate(ctx, profile, cache)
        if tier0_verdict is not None:
            reason = f"AI unavailable - rules only; {tier0_verdict.reason}"
            return replace(tier0_verdict, reason=reason, rate_limited=True)
        return _safe_fallback(
            ctx,
            profile,
            cache,
            "AI unavailable - rules only (all AI models offline)",
            rate_limited=True,
        )

    if not force_vision:
        tier0_verdict = tier0_evaluate(ctx, profile, cache)
        if tier0_verdict is not None:
            return _apply_confidence_gate(tier0_verdict)

        try:
            tier1_verdict = tier1_evaluate(ctx, profile, api_key, model_manager=mgr)
        except GeminiRateLimitError:
            logger.warning("All AI models hit rate limits/unavailable during Tier 1; using Tier 0 fallback.")
            return _safe_fallback(
                ctx, profile, cache, "AI models unavailable at Tier 1; using Tier 0 rules", rate_limited=True
            )
        except Exception as exc:
            logger.warning("Tier 1 evaluation failed (%s); falling back to safe verdict.", exc)
            return _safe_fallback(
                ctx, profile, cache, f"Tier 1 evaluation failed ({exc})", rate_limited=False
            )

        if tier1_verdict.confidence < TIER1_ESCALATION_CONFIDENCE:
            if not _try_acquire_vision_slot():
                logger.info("Vision cooldown active; returning Tier 1 verdict without Tier 2.")
                gated = _apply_confidence_gate(tier1_verdict)
                _cache_if_confident(ctx, gated, cache)
                return gated
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
                tier2_verdict = tier2_evaluate(ctx, profile, screenshot_bytes, api_key, model_manager=mgr)
            except GeminiRateLimitError:
                logger.warning("All AI models hit rate limits/unavailable during Tier 2; using Tier 0 fallback.")
                return _safe_fallback(
                    ctx, profile, cache, "AI models unavailable at Tier 2; using Tier 0 rules", rate_limited=True
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

    # force_vision=True (dwell, manual_recheck): bypass cache and run Tier 2 unless cooldown blocks it
    if not _try_acquire_vision_slot():
        logger.info("Vision cooldown active; falling back to Tier 1 instead of Tier 2.")
        try:
            tier1_verdict = tier1_evaluate(ctx, profile, api_key, model_manager=mgr)
            final_verdict = _apply_confidence_gate(tier1_verdict)
            _cache_if_confident(ctx, final_verdict, cache)
            return final_verdict
        except GeminiRateLimitError:
            return _safe_fallback(
                ctx, profile, cache, "Vision cooldown active and AI models hit rate limits", rate_limited=True
            )
        except Exception as tier1_exc:
            return _safe_fallback(
                ctx, profile, cache, f"Vision cooldown active and Tier 1 failed ({tier1_exc})", rate_limited=False
            )

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
            tier1_verdict = tier1_evaluate(ctx, profile, api_key, model_manager=mgr)
            final_verdict = _apply_confidence_gate(tier1_verdict)
            _cache_if_confident(ctx, final_verdict, cache)
            return final_verdict
        except GeminiRateLimitError:
            logger.warning("AI models hit rate limits during Tier 1 fallback after screenshot failure.")
            return _safe_fallback(
                ctx,
                profile,
                cache,
                "Screenshot failed and AI models hit rate limits",
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
        tier2_verdict = tier2_evaluate(ctx, profile, screenshot_bytes, api_key, model_manager=mgr)
    except GeminiRateLimitError:
        logger.warning("AI models unavailable during forced Tier 2 vision check; using Tier 0 fallback.")
        return _safe_fallback(
            ctx, profile, cache, "AI models unavailable at Tier 2; using Tier 0 rules", rate_limited=True
        )
    except Exception as exc:
        logger.warning(
            "Forced Tier 2 vision check failed (%s); falling back to Tier 1.",
            exc,
        )
        try:
            tier1_verdict = tier1_evaluate(ctx, profile, api_key, model_manager=mgr)
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
    timeout_seconds: float = 15.0,
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
    result_box: list[str] = []
    done_event = threading.Event()

    def _call_model() -> None:
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=GEMINI_VISION_MODEL,
                contents=prompt,
            )
            text = (response.text or "").strip()
            result_box.append(text or fallback_summary)
        except Exception:
            result_box.append(fallback_summary)
        finally:
            done_event.set()

    worker = threading.Thread(target=_call_model, daemon=True, name="session-review-genai")
    worker.start()
    if not done_event.wait(timeout=max(0.01, timeout_seconds)):
        return fallback_summary
    return result_box[0] if result_box else fallback_summary
