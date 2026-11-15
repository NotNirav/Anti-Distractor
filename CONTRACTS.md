# CONTRACTS.md

> **Note:** These interfaces and schemas are **recommended defaults, open to improvement**. If you choose a cleaner design during implementation, update `PROGRESS.md` under `Deviations` with a one-line explanation so subsequent tasks stay aligned.

---

## 1. Folder and Module Layout

```text
Anti-Distractor/
├── AGENTS.md                  # Agent working rules and escalation policy
├── CONTRACTS.md               # Module boundaries, schemas, API, and config contracts
├── implementation_plan.md     # Ordered task breakdown with model assignments
├── PROGRESS.md                # Task checklist, notes, deviations, escalations
├── plan.md                    # Original project plan
├── .env.example               # Template for GEMINI_API_KEY and runtime mode
├── .gitignore                 # Ignore .env, venv, __pycache__, *.db, logs
├── requirements.txt           # Python dependencies
├── app/
│   ├── __init__.py
│   ├── config.py              # Timing presets (dev/demo), thresholds, server & model settings
│   ├── models.py              # Shared dataclasses/TypedDicts for events, tabs, verdicts, state
│   ├── logger.py              # SQLite persistence for sessions and text-only activity logs
│   ├── profile.py             # GoalProfile generation via Gemini + domain/title normalization
│   ├── cache.py               # In-memory VerdictCache with override support
│   ├── screen.py              # In-memory mss screenshot capture + Pillow downscaling
│   ├── window_watcher.py      # pywin32 + psutil foreground window tracking & minimize helper
│   ├── judge.py               # Tier 0 rules/cache, Tier 1 text AI, Tier 2 vision AI, review AI
│   ├── server.py              # FastAPI local bridge (/health, /tabs, /commands, /commands/ack)
│   ├── event_engine.py        # Debounce, dwell, heartbeat, tier escalation, rate-limit fallback
│   ├── state_machine.py       # Deterministic agent state machine (Normal/Strict, Layer 1/2)
│   ├── ui.py                  # Tkinter UI (setup, HUD, Layer 1/2 popups, strict overlay, review)
│   └── main.py                # Application entry point & cross-thread orchestration
└── extension/
    ├── manifest.json          # Chrome/Edge Manifest V3 configuration
    ├── background.js          # Service worker: tab listeners, snapshot POST, command polling
    └── popup.html             # Minimal status popup for debugging connection/session state
```

---

## 2. Config Values (`app/config.py`)

All timings, thresholds, and runtime constants live in `app/config.py`, loaded from `.env` where applicable (`GEMINI_API_KEY`, `ANTI_DISTRACTOR_ENV=dev|demo`).

```python
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

# Judge & API settings
SERVER_HOST: str = "127.0.0.1"
SERVER_PORT: int = 8765
DB_PATH: str = "anti_distractor.db"
GEMINI_TEXT_MODEL: str = "gemini-2.5-flash-lite"   # Verify in AI Studio during Phase 0
GEMINI_VISION_MODEL: str = "gemini-2.5-flash"       # Verify in AI Studio during Phase 0
SCREENSHOT_MAX_WIDTH: int = 1024
PUNISH_CONFIDENCE_THRESHOLD: float = 0.60           # Below this -> treat as on_task
TIER1_ESCALATION_CONFIDENCE: float = 0.75           # Below this in Tier 1 -> escalate to Tier 2
```

---

## 3. Shared Data Models (`app/models.py`)

```python
ModeType = Literal["normal", "strict"]
TriggerType = Literal[
    "baseline", "tab_activated", "tab_updated", "tab_created",
    "tab_removed", "window_changed", "heartbeat", "dwell", "manual_recheck"
]
AgentStateName = Literal[
    "IDLE", "ON_TASK", "GRACE_PERIOD", "LAYER_1_ASK", "LAYER_2_ULTIMATUM", "ENDED"
]

@dataclass
class TabInfo:
    """Single browser tab snapshot sent by the Chrome/Edge extension."""
    tab_id: int
    window_id: int
    title: str
    url: str
    domain: str
    active: bool
    audible: bool = False

@dataclass
class TabSnapshot:
    """Latest browser tab collection and focus metadata."""
    tabs: list[TabInfo]
    active_tab_id: int | None
    window_focused: bool
    updated_at: float

@dataclass
class WindowInfo:
    """Foreground desktop window metadata from pywin32/psutil."""
    hwnd: int
    title: str
    process_name: str
    pid: int
    is_browser: bool

@dataclass
class ActivityContext:
    """Normalized observation bundle passed into the three-tier judge."""
    trigger_type: TriggerType
    goal: str
    foreground_window: WindowInfo | None
    tab_snapshot: TabSnapshot | None
    open_windows: list[str]
    source_key: str        # domain (e.g. "youtube.com") or process_name (e.g. "code.exe")
    display_title: str     # active tab title or foreground window title
    active_url: str = ""
    timestamp: float = 0.0

@dataclass
class GoalProfile:
    """Expanded semantic profile for the user's session goal."""
    goal: str
    on_task_topics: list[str]
    allowed_domains: list[str]
    borderline_domains: list[str]
    blocked_domains: list[str]
    allowed_apps: list[str]
    blocked_apps: list[str]

@dataclass
class JudgeVerdict:
    """Unified output produced by Tier 0, Tier 1, or Tier 2."""
    activity: str
    on_task: bool
    confidence: float
    reason: str
    tabs_to_close: list[int]
    tier_used: int         # 0, 1, or 2
    from_cache: bool = False
    rate_limited: bool = False

@dataclass
class AgentDecision:
    """Action instruction emitted by the state machine to the UI/executor."""
    state: AgentStateName
    action_taken: str      # "none" | "grace_started" | "ask_popup" | "warn_popup" | "strict_block" | "cleared"
    message: str
    tabs_to_close: list[int]
    minimize_hwnd: int | None = None
    lockout_seconds: float = 0.0
```

---

## 4. Module Interfaces (Python Signatures)

### Goal Profile (`app/profile.py`)
```python
def normalize_domain(url_or_domain: str) -> str:
    """Extract lowercase root/host domain without scheme or leading www."""

def normalize_title(title: str) -> str:
    """Normalize a tab or window title for consistent verdict cache lookups."""

def generate_goal_profile(goal: str, api_key: str) -> GoalProfile:
    """Call Gemini once at session start to expand a short goal into a GoalProfile."""

def fallback_goal_profile(goal: str) -> GoalProfile:
    """Return a safe default GoalProfile if the Gemini call fails or hits rate limits."""
```

### Verdict Cache (`app/cache.py`)
```python
class VerdictCache:
    """Thread-safe in-memory cache of verdicts keyed by (source_key, normalized_title)."""
    def __init__(self, ttl_seconds: float = 1800.0) -> None: ...
    def make_key(self, source_key: str, title: str) -> tuple[str, str]:
        """Build a normalized cache key from app/domain and title."""
    def get(self, source_key: str, title: str) -> JudgeVerdict | None:
        """Return a non-expired cached verdict or None."""
    def put(self, source_key: str, title: str, verdict: JudgeVerdict, user_override: bool = False) -> None:
        """Store or update a verdict entry in the cache."""
    def record_override(self, source_key: str, title: str, reason: str = "User marked relevant") -> None:
        """Force-cache an on-task override when the user clicks 'This is relevant'."""
    def clear(self) -> None:
        """Clear all cached entries between sessions."""
```

### Screen Capture (`app/screen.py`)
```python
def capture_screenshot_bytes(max_width: int = 1024, jpeg_quality: int = 75) -> bytes:
    """Capture the primary monitor in memory via mss and return downscaled JPEG bytes."""
```

### Window Watcher (`app/window_watcher.py`)
```python
def get_foreground_window() -> WindowInfo | None:
    """Return metadata for the currently focused Windows foreground window."""

def list_open_window_titles(limit: int = 15) -> list[str]:
    """Return visible top-level window titles/process names for Tier 1/2 context."""

def minimize_window(hwnd: int) -> bool:
    """Minimize a non-browser window safely without terminating the process."""

class WindowWatcher:
    """Background watcher polling foreground window changes and emitting callbacks."""
    def __init__(self, on_window_changed: Callable[[WindowInfo], None], poll_interval: float = 0.5) -> None: ...
    def start(self) -> None:
        """Start the daemon polling thread."""
    def stop(self) -> None:
        """Signal the watcher thread to stop."""
```

### Three-Tier Judge & Review (`app/judge.py`)
```python
class GeminiRateLimitError(Exception):
    """Raised when the Gemini API returns a quota or rate-limit error (HTTP 429)."""

def tier0_evaluate(ctx: ActivityContext, profile: GoalProfile, cache: VerdictCache) -> JudgeVerdict | None:
    """Evaluate using verdict cache and goal-profile allow/block lists; return None if ambiguous."""

def tier1_evaluate(ctx: ActivityContext, profile: GoalProfile, api_key: str) -> JudgeVerdict:
    """Call Gemini text model with metadata and open tabs/windows; returns strict JSON verdict."""

def tier2_evaluate(ctx: ActivityContext, profile: GoalProfile, screenshot_jpeg: bytes, api_key: str) -> JudgeVerdict:
    """Call Gemini vision model with in-memory screenshot and context; returns strict JSON verdict."""

def evaluate_context(
    ctx: ActivityContext,
    profile: GoalProfile,
    cache: VerdictCache,
    api_key: str,
    force_vision: bool = False,
) -> JudgeVerdict:
    """Run Tier 0 -> Tier 1 -> Tier 2 escalation with confidence gating and rate-limit fallback."""

def generate_session_review(goal: str, mode: ModeType, logs: list[dict[str, Any]], api_key: str) -> str:
    """Summarize the session's text-only SQLite activity logs into a concise user review."""
```

### Event Engine (`app/event_engine.py`)
```python
class EventEngine:
    """Coordinates triggers, debouncing, borderline dwell checks, heartbeat, and judge calls."""
    def __init__(
        self,
        goal: str,
        profile: GoalProfile,
        cache: VerdictCache,
        tab_store: TabSnapshotStore,
        timings: TimingConfig,
        api_key: str,
        on_verdict: Callable[[ActivityContext, JudgeVerdict], None],
    ) -> None: ...
    def start(self) -> None:
        """Start timers (heartbeat, dwell monitor, debounce worker) and trigger baseline check."""
    def stop(self) -> None:
        """Stop all timers and worker threads cleanly."""
    def submit_trigger(self, trigger_type: TriggerType) -> None:
        """Schedule a debounced evaluation for a tab or window trigger."""
    def run_immediate_check(self, trigger_type: TriggerType = "manual_recheck", force_vision: bool = True) -> None:
        """Bypass debounce to immediately evaluate current context (e.g. baseline or lockout end)."""
```

### Agent State Machine (`app/state_machine.py`)
```python
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
    ) -> None: ...
    def handle_verdict(self, ctx: ActivityContext, verdict: JudgeVerdict) -> AgentDecision:
        """Advance state machine for a new verdict and return the resulting AgentDecision."""
    def handle_tick(self, now: float | None = None) -> AgentDecision | None:
        """Check grace period and strict-mode lockout expirations on a periodic tick."""
    def handle_user_action(self, action: Literal["back_to_work", "relevant"]) -> AgentDecision:
        """Handle user button clicks from Layer 1 popup, updating overrides and cache."""
    def end_session(self) -> AgentDecision:
        """Transition state machine to ENDED and clear active warnings/overlays."""
```

### SQLite Logger (`app/logger.py`)
```python
class SessionLogger:
    """Thread-safe SQLite logger storing session metadata and text-only activity verdicts."""
    def __init__(self, db_path: str = "anti_distractor.db") -> None: ...
    def init_db(self) -> None:
        """Create tables if they do not exist."""
    def start_session(self, goal: str, mode: ModeType, duration_minutes: int, profile: GoalProfile) -> int:
        """Insert a new session row and return session_id."""
    def log_event(
        self,
        session_id: int,
        ctx: ActivityContext,
        verdict: JudgeVerdict,
        action_taken: str,
        user_override: bool = False,
    ) -> int:
        """Persist a single evaluation event and return the row id."""
    def mark_override(self, log_id: int) -> None:
        """Set user_override = 1 on an existing activity log row."""
    def finish_session(self, session_id: int, review_text: str) -> None:
        """Record ended_at timestamp and AI-generated session review."""
    def get_session_logs(self, session_id: int) -> list[dict[str, Any]]:
        """Fetch all activity log rows for a session ordered by timestamp."""
```

### UI Hooks (`app/ui.py`)
```python
class UIHooks(Protocol):
    """Thread-safe interface used by the app controller to drive Tkinter screens."""
    def show_setup(self, on_start: Callable[[str, int, ModeType], None]) -> None: ...
    def show_monitoring_hud(self, goal: str, mode: ModeType, on_end_session: Callable[[], None]) -> None: ...
    def update_hud_status(self, state: AgentStateName, summary: str) -> None: ...
    def show_layer1_popup(self, goal: str, reason: str, on_choice: Callable[[Literal["back_to_work", "relevant"]], None]) -> None: ...
    def show_layer2_warning(self, goal: str, reason: str) -> None: ...
    def show_strict_overlay(self, goal: str, reason: str, lockout_seconds: float, on_lockout_done: Callable[[], None]) -> None: ...
    def hide_popups_and_overlay(self) -> None: ...
    def show_review(self, review_text: str, on_close: Callable[[], None]) -> None: ...
```

---

## 5. Gemini JSON Schemas

### Goal-Profile JSON Schema
```json
{
  "type": "object",
  "required": [
    "goal",
    "on_task_topics",
    "allowed_domains",
    "borderline_domains",
    "blocked_domains",
    "allowed_apps",
    "blocked_apps"
  ],
  "properties": {
    "goal": { "type": "string" },
    "on_task_topics": { "type": "array", "items": { "type": "string" } },
    "allowed_domains": { "type": "array", "items": { "type": "string" } },
    "borderline_domains": { "type": "array", "items": { "type": "string" } },
    "blocked_domains": { "type": "array", "items": { "type": "string" } },
    "allowed_apps": { "type": "array", "items": { "type": "string" } },
    "blocked_apps": { "type": "array", "items": { "type": "string" } }
  }
}
```

### Judge Verdict JSON Schema
```json
{
  "type": "object",
  "required": ["activity", "on_task", "confidence", "reason", "tabs_to_close"],
  "properties": {
    "activity": { "type": "string" },
    "on_task": { "type": "boolean" },
    "confidence": { "type": "number", "minimum": 0.0, "maximum": 1.0 },
    "reason": { "type": "string" },
    "tabs_to_close": { "type": "array", "items": { "type": "integer" } }
  }
}
```

---

## 6. Chrome/Edge Extension <-> FastAPI API (`app/server.py`)

Local server listens on `http://127.0.0.1:8765` (CORS enabled for `chrome-extension://*` / `*` on localhost).

### Endpoints

1. **`GET /health`**
   - Response `200`: `{"status": "ok", "session_active": true, "goal": "DSA"}`

2. **`POST /tabs`**
   - Sent by `extension/background.js` on `tabs.onActivated`, `tabs.onUpdated` (when `title`/`url` changes or `status === "complete"`), `tabs.onCreated`, `tabs.onRemoved`, and `windows.onFocusChanged`.
   - Request JSON:
     ```json
     {
       "event_type": "tab_activated",
       "active_tab_id": 42,
       "window_focused": true,
       "timestamp": 1731642000.12,
       "tabs": [
         {
           "tab_id": 42,
           "window_id": 1,
           "title": "Binary Search Trees - YouTube",
           "url": "https://www.youtube.com/watch?v=abc",
           "domain": "youtube.com",
           "active": true,
           "audible": false
         }
       ]
     }
     ```
   - Response `200`: `{"ok": true, "received_tabs": 1}`

3. **`GET /commands`**
   - Polled by `extension/background.js` every `1.5–2.0 s` (using `setInterval` / `chrome.alarms` while active).
   - Response `200`:
     ```json
     {
       "commands": [
         {
           "command_id": "cmd-1234",
           "action": "close_tabs",
           "tab_ids": [42]
         }
       ]
     }
     ```

4. **`POST /commands/ack`**
   - Sent by extension after running `chrome.tabs.remove(tab_ids)`.
   - Request JSON: `{"command_id": "cmd-1234", "closed_tab_ids": [42], "errors": []}`
   - Response `200`: `{"ok": true}`

### Close-Tab Command Flow
1. Strict mode in `AgentStateMachine` queues a `close_tabs` command in `CommandQueue` with `tab_ids` (from `JudgeVerdict.tabs_to_close`, or the active off-task browser tab if `tabs_to_close` is empty).
2. `background.js` polls `GET /commands`, receives pending command(s), calls `chrome.tabs.remove(tab_ids)` (ignoring already-closed tab errors), and POSTs result to `POST /commands/ack`.
3. `CommandQueue` marks commands in-flight when fetched and removes them on ack (or auto-expires unacked commands after 15 s to prevent stuck duplicates).

---

## 7. SQLite Table Schema (`app/logger.py`)

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal TEXT NOT NULL,
    mode TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    profile_json TEXT NOT NULL,
    review_text TEXT
);

CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    trigger_type TEXT NOT NULL,
    app_or_domain TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    tier_used INTEGER NOT NULL,
    activity TEXT NOT NULL,
    on_task INTEGER NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    user_override INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
```
