"""Configuration presets (dev vs demo timings), thresholds, and environment settings."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root with override=True so local keys take precedence
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)


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
# Model preference hierarchy:
# Preference 1: Gemini 3.5 Flash-Lite (fast, economical, multimodal)
# Preference 2: Gemma 31B (gemma-4-31b-it)
# Preference 3: Gemma 24B (gemma-4-26b-a4b-it MoE)
MODEL_PREFERENCE_1: str = os.getenv("MODEL_PREFERENCE_1", "gemini-3.5-flash-lite")
MODEL_PREFERENCE_2: str = os.getenv("MODEL_PREFERENCE_2", "gemma-4-31b-it")
MODEL_PREFERENCE_3: str = os.getenv("MODEL_PREFERENCE_3", "gemma-4-26b-a4b-it")

MODEL_PREFERENCES: list[str] = [
    MODEL_PREFERENCE_1,
    MODEL_PREFERENCE_2,
    MODEL_PREFERENCE_3,
]

GEMINI_TEXT_MODEL: str = MODEL_PREFERENCE_1
GEMINI_VISION_MODEL: str = MODEL_PREFERENCE_1
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
