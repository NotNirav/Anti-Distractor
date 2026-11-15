"""Thread-safe in-memory verdict cache keyed by (source_key, normalized_title)."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any

from app.models import JudgeVerdict
from app.profile import normalize_domain, normalize_title


class VerdictCache:
    """Thread-safe in-memory cache of verdicts keyed by (source_key, normalized_title)."""

    def __init__(self, ttl_seconds: float = 1800.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}

    def make_key(self, source_key: str, title: str) -> tuple[str, str]:
        """Build a normalized cache key from app/domain and title."""
        s = (source_key or "").strip().lower()
        if s.endswith(".exe"):
            norm_source = s
        else:
            norm_source = normalize_domain(s) or s
        norm_title = normalize_title(title)
        return (norm_source, norm_title)

    def get(self, source_key: str, title: str) -> JudgeVerdict | None:
        """Return a non-expired cached verdict or None."""
        key = self.make_key(source_key, title)
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if now - entry["cached_at"] > self.ttl_seconds:
                del self._cache[key]
                return None
            verdict: JudgeVerdict = entry["verdict"]
            return replace(verdict, from_cache=True)

    def put(
        self,
        source_key: str,
        title: str,
        verdict: JudgeVerdict,
        user_override: bool = False,
    ) -> None:
        """Store or update a verdict entry in the cache."""
        key = self.make_key(source_key, title)
        with self._lock:
            self._cache[key] = {
                "verdict": verdict,
                "cached_at": time.time(),
                "user_override": user_override,
            }

    def record_override(
        self,
        source_key: str,
        title: str,
        reason: str = "User marked relevant",
    ) -> None:
        """Force-cache an on-task override when the user clicks 'This is relevant'."""
        key = self.make_key(source_key, title)
        override_verdict = JudgeVerdict(
            activity=f"User marked on-task: {title}",
            on_task=True,
            confidence=1.0,
            reason=reason,
            tabs_to_close=[],
            tier_used=0,
            from_cache=True,
            rate_limited=False,
        )
        with self._lock:
            self._cache[key] = {
                "verdict": override_verdict,
                "cached_at": time.time(),
                "user_override": True,
            }

    def clear(self) -> None:
        """Clear all cached entries between sessions."""
        with self._lock:
            self._cache.clear()
