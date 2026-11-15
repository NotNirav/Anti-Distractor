"""Thread-safe in-memory verdict cache keyed by (source_key, normalized_title)."""

from __future__ import annotations

from app.models import JudgeVerdict


class VerdictCache:
    """Thread-safe in-memory cache of verdicts keyed by (source_key, normalized_title)."""

    def __init__(self, ttl_seconds: float = 1800.0) -> None:
        # TODO(T3.2): Initialize lock and internal storage.
        self.ttl_seconds = ttl_seconds

    def make_key(self, source_key: str, title: str) -> tuple[str, str]:
        """Build a normalized cache key from app/domain and title."""
        raise NotImplementedError("TODO(T3.2): Implement VerdictCache.make_key")

    def get(self, source_key: str, title: str) -> JudgeVerdict | None:
        """Return a non-expired cached verdict or None."""
        raise NotImplementedError("TODO(T3.2): Implement VerdictCache.get")

    def put(
        self,
        source_key: str,
        title: str,
        verdict: JudgeVerdict,
        user_override: bool = False,
    ) -> None:
        """Store or update a verdict entry in the cache."""
        raise NotImplementedError("TODO(T3.2): Implement VerdictCache.put")

    def record_override(
        self,
        source_key: str,
        title: str,
        reason: str = "User marked relevant",
    ) -> None:
        """Force-cache an on-task override when the user clicks 'This is relevant'."""
        raise NotImplementedError("TODO(T3.2): Implement VerdictCache.record_override")

    def clear(self) -> None:
        """Clear all cached entries between sessions."""
        raise NotImplementedError("TODO(T3.2): Implement VerdictCache.clear")
