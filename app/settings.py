"""Thread-safe local JSON stores for user settings, starter/custom focus presets, and safe .env key updates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Literal

from app.models import GoalProfile, ModeType
from app.profile import normalize_domain

logger = logging.getLogger(__name__)

TimingPresetType = Literal["dev", "demo"]


@dataclass
class AppSettings:
    """Persisted user preferences stored in settings.json."""

    break_minutes: int = 5
    mute_sounds: bool = False
    timing_preset: TimingPresetType = "dev"
    hotkeys_enabled: bool = True
    pomodoro_enabled: bool = False
    pomodoro_cycles: int = 4
    onboarding_skipped: bool = False

    def sanitized(self) -> AppSettings:
        try:
            brk = int(self.break_minutes)
        except Exception:
            brk = 5
        brk = max(1, min(60, brk))

        preset: TimingPresetType = "demo" if str(self.timing_preset).strip().lower() == "demo" else "dev"

        try:
            cycles = int(self.pomodoro_cycles)
        except Exception:
            cycles = 4
        cycles = max(2, min(12, cycles))

        return AppSettings(
            break_minutes=brk,
            mute_sounds=bool(self.mute_sounds),
            timing_preset=preset,
            hotkeys_enabled=bool(self.hotkeys_enabled),
            pomodoro_enabled=bool(self.pomodoro_enabled),
            pomodoro_cycles=cycles,
            onboarding_skipped=bool(self.onboarding_skipped),
        )


class SettingsStore:
    """Thread-safe reader/writer for settings.json."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def exists(self) -> bool:
        with self._lock:
            return self.path.is_file()

    def load(self) -> AppSettings:
        with self._lock:
            if not self.path.is_file():
                return AppSettings()
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    return AppSettings()
                return AppSettings(
                    break_minutes=raw.get("break_minutes", 5),
                    mute_sounds=raw.get("mute_sounds", False),
                    timing_preset=raw.get("timing_preset", "dev"),
                    hotkeys_enabled=raw.get("hotkeys_enabled", True),
                    pomodoro_enabled=raw.get("pomodoro_enabled", False),
                    pomodoro_cycles=raw.get("pomodoro_cycles", 4),
                    onboarding_skipped=raw.get("onboarding_skipped", False),
                ).sanitized()
            except Exception:
                logger.exception("Failed loading settings.json; using defaults")
                return AppSettings()

    def save(self, settings: AppSettings) -> AppSettings:
        clean = settings.sanitized()
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(asdict(clean), indent=2), encoding="utf-8")
            except Exception:
                logger.exception("Failed saving settings.json")
        return clean


@dataclass
class FocusPreset:
    """Named one-click goal + rules preset stored in presets.json."""

    name: str
    goal: str
    duration_minutes: int
    mode: ModeType
    profile: GoalProfile

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "goal": self.goal,
            "duration_minutes": self.duration_minutes,
            "mode": self.mode,
            "profile": asdict(self.profile),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> FocusPreset | None:
        try:
            name = str(data.get("name", "")).strip()
            goal = str(data.get("goal", "")).strip()
            if not name or not goal:
                return None
            dur = max(1, min(720, int(data.get("duration_minutes", 25))))
            raw_mode = str(data.get("mode", "strict")).strip().lower()
            mode: ModeType = "normal" if raw_mode == "normal" else "strict"
            p_raw = data.get("profile")
            if isinstance(p_raw, dict):
                profile = GoalProfile(
                    goal=str(p_raw.get("goal") or goal),
                    on_task_topics=[str(x).strip() for x in p_raw.get("on_task_topics", []) if str(x).strip()],
                    allowed_domains=[
                        d for d in (normalize_domain(str(x)) for x in p_raw.get("allowed_domains", [])) if d
                    ],
                    borderline_domains=[
                        d for d in (normalize_domain(str(x)) for x in p_raw.get("borderline_domains", [])) if d
                    ],
                    blocked_domains=[
                        d for d in (normalize_domain(str(x)) for x in p_raw.get("blocked_domains", [])) if d
                    ],
                    allowed_apps=[str(x).strip().lower() for x in p_raw.get("allowed_apps", []) if str(x).strip()],
                    blocked_apps=[str(x).strip().lower() for x in p_raw.get("blocked_apps", []) if str(x).strip()],
                )
            else:
                profile = GoalProfile(goal=goal, on_task_topics=[goal])
            return FocusPreset(
                name=name,
                goal=goal,
                duration_minutes=dur,
                mode=mode,
                profile=profile,
            )
        except Exception:
            return None


def default_starter_presets() -> list[FocusPreset]:
    """Return 3 built-in starter presets seeded when presets.json does not exist yet."""
    return [
        FocusPreset(
            name="DSA Prep",
            goal="Study Data Structures and Algorithms",
            duration_minutes=25,
            mode="strict",
            profile=GoalProfile(
                goal="Study Data Structures and Algorithms",
                on_task_topics=[
                    "algorithms",
                    "data structures",
                    "dynamic programming",
                    "graphs",
                    "trees",
                    "binary search",
                    "leetcode",
                ],
                allowed_domains=[
                    "leetcode.com",
                    "neetcode.io",
                    "cp-algorithms.com",
                    "geeksforgeeks.org",
                    "codeforces.com",
                    "github.com",
                    "stackoverflow.com",
                ],
                borderline_domains=["youtube.com", "reddit.com", "chatgpt.com", "claude.ai", "medium.com"],
                blocked_domains=[
                    "instagram.com",
                    "tiktok.com",
                    "x.com",
                    "twitter.com",
                    "netflix.com",
                    "twitch.tv",
                ],
                allowed_apps=["code.exe", "pycharm64.exe", "clion64.exe", "windowsterminal.exe", "powershell.exe"],
                blocked_apps=["steam.exe", "discord.exe", "spotify.exe"],
            ),
        ),
        FocusPreset(
            name="Deep Coding",
            goal="Build and debug software features",
            duration_minutes=45,
            mode="strict",
            profile=GoalProfile(
                goal="Build and debug software features",
                on_task_topics=[
                    "software engineering",
                    "debugging",
                    "unit testing",
                    "api documentation",
                    "architecture",
                ],
                allowed_domains=[
                    "github.com",
                    "stackoverflow.com",
                    "docs.python.org",
                    "developer.mozilla.org",
                    "pypi.org",
                    "npmjs.com",
                    "127.0.0.1",
                    "localhost",
                ],
                borderline_domains=["youtube.com", "reddit.com", "chatgpt.com", "claude.ai"],
                blocked_domains=[
                    "instagram.com",
                    "tiktok.com",
                    "x.com",
                    "twitter.com",
                    "netflix.com",
                    "twitch.tv",
                ],
                allowed_apps=[
                    "code.exe",
                    "devenv.exe",
                    "pycharm64.exe",
                    "windowsterminal.exe",
                    "cmd.exe",
                    "powershell.exe",
                ],
                blocked_apps=["steam.exe", "epicgameslauncher.exe", "discord.exe"],
            ),
        ),
        FocusPreset(
            name="Writing & Docs",
            goal="Write technical documentation and notes",
            duration_minutes=30,
            mode="normal",
            profile=GoalProfile(
                goal="Write technical documentation and notes",
                on_task_topics=["technical writing", "documentation", "markdown", "research notes"],
                allowed_domains=[
                    "docs.google.com",
                    "notion.so",
                    "overleaf.com",
                    "github.com",
                    "wikipedia.org",
                    "scholar.google.com",
                ],
                borderline_domains=["youtube.com", "chatgpt.com", "claude.ai", "medium.com"],
                blocked_domains=[
                    "instagram.com",
                    "tiktok.com",
                    "x.com",
                    "twitter.com",
                    "netflix.com",
                    "reddit.com",
                ],
                allowed_apps=["code.exe", "obsidian.exe", "winword.exe", "notepad.exe"],
                blocked_apps=["steam.exe", "discord.exe"],
            ),
        ),
    ]


class PresetsStore:
    """Thread-safe reader/writer for presets.json with starter seeding."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load_unlocked(self) -> list[FocusPreset]:
        if not self.path.is_file():
            starters = default_starter_presets()
            self._save_unlocked(starters)
            return starters
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                parsed = [FocusPreset.from_dict(item) for item in raw if isinstance(item, dict)]
                valid = [p for p in parsed if p is not None]
                if valid:
                    return valid
        except Exception:
            logger.exception("Failed loading presets.json; falling back to starter presets")
        return default_starter_presets()

    def _save_unlocked(self, presets: list[FocusPreset]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = [p.to_dict() for p in presets]
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            logger.exception("Failed saving presets.json")

    def list_presets(self) -> list[FocusPreset]:
        with self._lock:
            return list(self._load_unlocked())

    def get_preset(self, name: str) -> FocusPreset | None:
        target = (name or "").strip().lower()
        if not target:
            return None
        with self._lock:
            for p in self._load_unlocked():
                if p.name.strip().lower() == target:
                    return p
        return None

    def upsert_preset(self, preset: FocusPreset) -> list[FocusPreset]:
        with self._lock:
            current = self._load_unlocked()
            target = preset.name.strip().lower()
            updated: list[FocusPreset] = []
            replaced = False
            for existing in current:
                if existing.name.strip().lower() == target:
                    updated.append(preset)
                    replaced = True
                else:
                    updated.append(existing)
            if not replaced:
                updated.append(preset)
            self._save_unlocked(updated)
            return list(updated)

    def delete_preset(self, name: str) -> list[FocusPreset]:
        target = (name or "").strip().lower()
        with self._lock:
            current = self._load_unlocked()
            updated = [p for p in current if p.name.strip().lower() != target]
            self._save_unlocked(updated)
            return list(updated)


_ENV_KEY_PATTERN = re.compile(r"^\s*(?:export\s+)?GEMINI_API_KEY\s*=\s*(.*)$")
_PLACEHOLDER_API_KEYS: frozenset[str] = frozenset(
    {
        "your_gemini_api_key_here",
        "your_api_key_here",
        "your-api-key-here",
        "paste_key_here",
        "paste_your_key_here",
        "changeme",
        "replace_me",
        "none",
        "null",
        "<your_api_key>",
    }
)


def is_placeholder_or_empty_api_key(raw_key: str | None) -> bool:
    """Return True if raw_key is empty or matches a template placeholder value."""
    cleaned = (raw_key or "").strip().strip("'\"").strip()
    if not cleaned:
        return True
    low = cleaned.lower()
    if low in _PLACEHOLDER_API_KEYS:
        return True
    if low.startswith("your_") or low.startswith("replace_") or "placeholder" in low:
        return True
    return False


def get_fallback_dotenv_path() -> Path:
    """Return the per-user config directory .env path used when project root is not writable."""
    if os.name == "nt":
        base = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "Anti-Distractor" / ".env"
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / "anti-distractor" / ".env"


def _is_dir_writable(directory: Path) -> bool:
    """Return True if files can be created/updated inside directory."""
    try:
        if not directory.exists():
            return False
        fd, tmp_path = tempfile.mkstemp(dir=str(directory), prefix=".ad_write_check_")
        os.close(fd)
        os.unlink(tmp_path)
        return True
    except Exception:
        return False


def resolve_dotenv_path(preferred_path: str | Path | None = None) -> Path:
    """Resolve where GEMINI_API_KEY .env should be stored, keeping explicit paths isolated."""
    if preferred_path is not None:
        return Path(preferred_path)
    project_root = Path(__file__).resolve().parent.parent
    if _is_dir_writable(project_root):
        return project_root / ".env"
    return get_fallback_dotenv_path()


def mask_api_key(raw_key: str) -> str:
    """Return a non-reversible masked suffix of an API key for UI display."""
    cleaned = (raw_key or "").strip().strip("'\"")
    if not cleaned:
        return ""
    if len(cleaned) <= 6:
        return "••••••"
    return f"••••••••{cleaned[-4:]}"


def read_masked_api_key(env_path: str | Path | None = None) -> tuple[bool, str]:
    """Check whether GEMINI_API_KEY is configured and return (configured, masked_label) without exposing the key."""
    found_val = ""
    if env_path is not None:
        p = Path(env_path)
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    m = _ENV_KEY_PATTERN.match(line)
                    if m:
                        val = m.group(1).strip()
                        if val and not val.startswith("#"):
                            found_val = val.strip("'\"")
            except Exception:
                logger.debug("Could not read .env path for masked check", exc_info=True)
    if is_placeholder_or_empty_api_key(found_val):
        env_val = os.getenv("GEMINI_API_KEY", "").strip()
        if not is_placeholder_or_empty_api_key(env_val):
            found_val = env_val
        else:
            found_val = ""
    if is_placeholder_or_empty_api_key(found_val):
        return False, "Not configured"
    return True, f"Configured ({mask_api_key(found_val)})"


def write_api_key_to_dotenv(
    env_path: str | Path,
    new_key: str,
    *,
    update_environ: bool = True,
) -> None:
    """Safely insert or update GEMINI_API_KEY in .env while preserving comments and other keys."""
    sanitized = re.sub(r"[\r\n\"']", "", (new_key or "").strip())
    if is_placeholder_or_empty_api_key(sanitized):
        raise ValueError("Please enter a valid Google Gemini API key (not a placeholder).")
    p = Path(env_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    existing_lines: list[str] = []
    if p.is_file():
        try:
            existing_lines = p.read_text(encoding="utf-8").splitlines(keepends=False)
        except Exception:
            existing_lines = []

    updated_lines: list[str] = []
    replaced = False
    for line in existing_lines:
        if _ENV_KEY_PATTERN.match(line):
            if not replaced:
                updated_lines.append(f"GEMINI_API_KEY={sanitized}")
                replaced = True
        else:
            updated_lines.append(line)

    if not replaced:
        updated_lines.append(f"GEMINI_API_KEY={sanitized}")

    p.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    if update_environ:
        os.environ["GEMINI_API_KEY"] = sanitized


def clear_api_key_in_dotenv(
    env_path: str | Path,
    *,
    update_environ: bool = True,
) -> None:
    """Remove GEMINI_API_KEY from .env while preserving comments and other variables."""
    p = Path(env_path)
    if p.is_file():
        try:
            existing_lines = p.read_text(encoding="utf-8").splitlines(keepends=False)
        except Exception:
            existing_lines = []
        remaining = [line for line in existing_lines if not _ENV_KEY_PATTERN.match(line)]
        content = ("\n".join(remaining) + "\n") if remaining else ""
        p.write_text(content, encoding="utf-8")
    if update_environ:
        os.environ.pop("GEMINI_API_KEY", None)
