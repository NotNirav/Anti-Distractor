"""Configuration presets (dev vs demo timings), thresholds, and environment settings."""

from __future__ import annotations

from dataclasses import dataclass
import os
from dotenv import load_dotenv

# Load .env file automatically
load_dotenv()


@dataclass(frozen=True)
class TimingConfig:
    """Timing parameters in seconds for either dev or demo mode."""

    debounce_seconds: float
    heartbeat_seconds: float
    dwell_seconds: float
    grace_period_seconds: float
    strict_lockout_seconds: float
    extension_poll_seconds: float
    window_poll_seconds: float


# TODO(T0.1): Populate DEV_TIMINGS, DEMO_TIMINGS, and load .env values.
DEV_TIMINGS = TimingConfig(
    debounce_seconds=1.5,
    heartbeat_seconds=10.0,
    dwell_seconds=15.0,
    grace_period_seconds=5.0,
    strict_lockout_seconds=8.0,
    extension_poll_seconds=1.5,
    window_poll_seconds=0.5,
)

DEMO_TIMINGS = TimingConfig(
    debounce_seconds=3.0,
    heartbeat_seconds=300.0,
    dwell_seconds=75.0,
    grace_period_seconds=25.0,
    strict_lockout_seconds=15.0,
    extension_poll_seconds=2.0,
    window_poll_seconds=0.5,
)

SERVER_HOST: str = "127.0.0.1"
SERVER_PORT: int = 8765
DB_PATH: str = "anti_distractor.db"
GEMINI_TEXT_MODEL: str = "gemini-3.5-flash-lite"
GEMINI_VISION_MODEL: str = "gemini-3.5-flash"
SCREENSHOT_MAX_WIDTH: int = 1024
PUNISH_CONFIDENCE_THRESHOLD: float = 0.60
TIER1_ESCALATION_CONFIDENCE: float = 0.75


def get_timings(env: str | None = None) -> TimingConfig:
    """Return DEV_TIMINGS or DEMO_TIMINGS based on env or ANTI_DISTRACTOR_ENV."""
    mode = (env or os.getenv("ANTI_DISTRACTOR_ENV", "dev")).strip().lower()
    if mode == "demo":
        return DEMO_TIMINGS
    return DEV_TIMINGS


def get_gemini_api_key() -> str:
    """Load and return GEMINI_API_KEY from environment / .env."""
    return os.getenv("GEMINI_API_KEY", "").strip()
