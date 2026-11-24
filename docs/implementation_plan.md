# implementation_plan.md

Ordered build plan aligned with the phases in [plan.md](file:///D:/Computer Programming/Hackathons/Upcomming/Agentix 2.0/Anti-Distractor/plan.md) and interfaces in [CONTRACTS.md](file:///D:/Computer Programming/Hackathons/Upcomming/Agentix 2.0/Anti-Distractor/CONTRACTS.md). Tasks are sized so Gemini 3.8 Flash handles routine, self-contained modules while Gemini 4 Hydrogen handles only the genuinely hard cross-cutting logic (3 Hydrogen tasks total).

---

## Phase 0: Setup

### T0.1 — Environment, Config, Data Models, and Gemini Smoke Check
- **Files:** `.env` (user-populated from `.env.example`), `app/config.py`, `app/models.py`, `requirements.txt`
- **What to build & goal:** Implement `TimingConfig` (`DEV_TIMINGS`, `DEMO_TIMINGS`), environment loading (`ANTI_DISTRACTOR_ENV`, `GEMINI_API_KEY`), constants, and all shared dataclasses/type aliases in `app/models.py`. Verify the Python environment can import dependencies and complete one minimal `google-genai` text generation call.
- **Contracts:** `CONTRACTS.md` §2 (`app/config.py`), §3 (`app/models.py`).
- **Acceptance test:** Run `python -c "from app.config import get_timings; from app.models import JudgeVerdict; print(get_timings('dev'))"` (prints dev timings), plus a one-line `google-genai` call verifying `GEMINI_API_KEY` returns a non-empty response.
- **Assigned model:** Flash

---

## Phase 1: Judge Core

### T1.1 — In-Memory Screenshot Capture & Downscaling
- **Files:** `app/screen.py`
- **What to build & goal:** Capture the primary Windows monitor entirely in memory with `mss` and downscale to `SCREENSHOT_MAX_WIDTH` (1024 px) using `Pillow`, encoding to JPEG bytes without writing any file to disk (preserving privacy contract).
- **Contracts:** `CONTRACTS.md` §4 (`capture_screenshot_bytes`).
- **Acceptance test:** Run `python -c "from app.screen import capture_screenshot_bytes; b = capture_screenshot_bytes(); assert b[:2] == b'\xff\xd8' and len(b) > 5000; print(len(b))"` and confirm no image files are created in the workspace.
- **Assigned model:** Flash

### T1.2 — Goal Profile Generation & Normalization Helpers
- **Files:** `app/profile.py`
- **What to build & goal:** Implement `normalize_domain`, `normalize_title`, `generate_goal_profile` (one Gemini call using structured JSON schema), and `fallback_goal_profile` (safe defaults if API fails).
- **Contracts:** `CONTRACTS.md` §4 (`app/profile.py`), §5 (Goal-Profile JSON Schema).
- **Acceptance test:** Run `python -c "from app.profile import generate_goal_profile, normalize_domain; import os; p = generate_goal_profile('Study DSA', os.environ['GEMINI_API_KEY']); assert 'leetcode.com' in p.allowed_domains or len(p.on_task_topics) > 0; print(p)"`.
- **Assigned model:** Flash

### T1.3 — Tier 1 Text Judge, Tier 2 Vision Judge, and Session Review AI Calls
- **Files:** `app/judge.py`
- **What to build & goal:** Implement `tier1_evaluate` (text-only Gemini call with active window, open apps, tab list, and goal), `tier2_evaluate` (multimodal Gemini call with JPEG screenshot bytes + text context), and `generate_session_review` (end-of-session text-log summary). Raise `GeminiRateLimitError` cleanly when the SDK reports HTTP 429 / quota exhaustion.
- **Contracts:** `CONTRACTS.md` §4 (`app/judge.py`), §5 (Judge Verdict JSON Schema).
- **Acceptance test:** Run a small script invoking `tier2_evaluate` on a live `capture_screenshot_bytes()` with goal `"Study DSA"` and print a valid `JudgeVerdict` with `on_task`, `confidence`, `reason`, and `tabs_to_close`.
- **Assigned model:** Flash *(Note: likely escalation candidate if `google-genai` multimodal/JSON schema enforcement throws subtle SDK errors.)*

---

## Phase 2: Browser Sensing

### T2.1 — FastAPI Local Receiver and Command Queue
- **Files:** `app/server.py`
- **What to build & goal:** Implement `TabSnapshotStore`, `CommandQueue` (with in-flight TTL expiry and ack handling), and the FastAPI app (`GET /health`, `POST /tabs`, `GET /commands`, `POST /commands/ack`) plus a helper to run `uvicorn` in a background thread. Calling `POST /tabs` should invoke an optional callback notifying the event engine of new tab events.
- **Contracts:** `CONTRACTS.md` §6 (Extension <-> FastAPI API & Close-Tab Command Flow).
- **Acceptance test:** Start the server locally and run `curl`/Python requests against `GET /health`, `POST /tabs`, and `GET /commands` + `POST /commands/ack`, verifying `200` responses and tab snapshot updates.
- **Assigned model:** Flash

### T2.2 — Chrome/Edge Manifest V3 Extension
- **Files:** `extension/manifest.json`, `extension/background.js`, `extension/popup.html`
- **What to build & goal:** Build an unpacked MV3 extension that queries all open tabs on `tabs.onActivated`, `tabs.onUpdated` (URL/title changes), `tabs.onCreated`, `tabs.onRemoved`, and `windows.onFocusChanged`, POSTs the snapshot to `http://127.0.0.1:8765/tabs`, and polls `GET /commands` to execute `chrome.tabs.remove` and POST `/commands/ack`. Fail silently if the Python app isn't running.
- **Contracts:** `CONTRACTS.md` §6 (Extension <-> FastAPI API).
- **Acceptance test:** Load `extension/` unpacked in Chrome/Edge (`chrome://extensions`), run `app/server.py` standalone, switch tabs and navigate within a YouTube tab, and confirm tab events appear in the server console. Queue a test close command and confirm the target tab closes.
- **Assigned model:** Flash *(Note: likely escalation candidate if MV3 service worker sleep/alarm timing misses fast polls.)*

---

## Phase 3: Event Engine and Tiers

### T3.1 — Windows Foreground Window Watcher & Minimize Helper
- **Files:** `app/window_watcher.py`
- **What to build & goal:** Implement `get_foreground_window`, `list_open_window_titles`, `minimize_window`, and the `WindowWatcher` background polling class using `pywin32` (`win32gui`, `win32process`, `win32con`) and `psutil`. Handle elevated/protected windows gracefully without crashing.
- **Contracts:** `CONTRACTS.md` §4 (`app/window_watcher.py`).
- **Acceptance test:** Run `WindowWatcher` for 10 seconds while alt-tabbing between VS Code, Chrome, and Explorer; verify `on_window_changed` prints accurate `process_name`, `title`, and `is_browser` flags, and test `minimize_window` on a sample Notepad window.
- **Assigned model:** Flash *(Note: likely escalation candidate if Windows COM/UWP app title resolution behaves inconsistently.)*

### T3.2 — Verdict Cache & Tier 0 Rule Evaluator
- **Files:** `app/cache.py`, `app/judge.py` (`tier0_evaluate`)
- **What to build & goal:** Implement thread-safe `VerdictCache` (TTL expiry, normalized `(source_key, title)` lookup, `record_override`) and `tier0_evaluate` matching exact/subdomain rules from `GoalProfile` (`allowed_domains`, `blocked_domains`, `allowed_apps`, `blocked_apps`) while leaving `borderline_domains` (e.g., `youtube.com`, `reddit.com`) as `None` unless already cached.
- **Contracts:** `CONTRACTS.md` §4 (`app/cache.py`, `tier0_evaluate`).
- **Acceptance test:** Run unit assertions via `python -c "..."` verifying: `instagram.com` -> Tier 0 `on_task=False`, `leetcode.com` -> Tier 0 `on_task=True`, `youtube.com` (uncached) -> `None`, `record_override` -> subsequent lookup returns `on_task=True`.
- **Assigned model:** Flash

### T3.3 — Event Engine, Tier Escalation, Dwell/Heartbeat, and Rate-Limit Fallback
- **Files:** `app/event_engine.py`, `app/judge.py` (`evaluate_context`)
- **What to build & goal:** Build the core event-driven decision pipeline:
  - `evaluate_context`: Tier 0 -> Tier 1 -> Tier 2 escalation (`TIER1_ESCALATION_CONFIDENCE`), `PUNISH_CONFIDENCE_THRESHOLD` fairness gate (`confidence < 0.6` -> `on_task=True`), automatic `VerdictCache` population, and `GeminiRateLimitError` fallback to Tier 0 rules (never punishing unknown contexts on rate limit).
  - `EventEngine`: 3s/1.5s debounce coalescing rapid tab/window bursts, borderline-site dwell timer (triggers Tier 2 vision check after `dwell_seconds` with no events), periodic `heartbeat` timer (forces Tier 2 vision check), and `run_immediate_check` for baseline/post-lockout checks.
- **Contracts:** `CONTRACTS.md` §2, §3, §4 (`evaluate_context`, `EventEngine`).
- **Acceptance test:** Run an integration script with mocked or live triggers showing: (1) rapid bursts coalesce into one check after debounce; (2) `instagram.com` resolves at Tier 0 with 0 AI calls; (3) an unknown/low-confidence Tier 1 result escalates to Tier 2; (4) sitting on `youtube.com` with no events triggers a dwell Tier 2 check; (5) simulated `GeminiRateLimitError` falls back cleanly without crashing or punishing.
- **Assigned model:** **Hydrogen**

---

## Phase 4: Agent Behavior

### T4.1 — Deterministic Agent State Machine & Strict Mode Enforcement
- **Files:** `app/state_machine.py`
- **What to build & goal:** Implement `AgentStateMachine` handling `Normal` and `Strict` modes:
  - `ON_TASK` resets strikes and clears grace/warnings.
  - First off-task verdict enters `GRACE_PERIOD` (`grace_period_seconds`).
  - Persisting off-task past grace enters `LAYER_1_ASK` ("Are you studying <goal>? Go back.").
  - User clicking `relevant` logs override and updates `VerdictCache`; user clicking `back_to_work` resets to `ON_TASK`/grace-cleared.
  - Persisting off-task at the next check (or model disagreeing with an override on a fresh vision check) enters `LAYER_2_ULTIMATUM`: Normal mode emits loud warning; Strict mode queues `close_tabs` via `CommandQueue` (for browser off-task tabs) or minimizes the foreground non-browser window via `minimize_window`, and triggers a full-screen lockout duration.
- **Contracts:** `CONTRACTS.md` §3 (`AgentDecision`), §4 (`AgentStateMachine`), §6 (Close-Tab Command Flow).
- **Acceptance test:** Run deterministic state-transition tests covering Normal mode (On-task -> Off-task -> Grace expiry -> Layer 1 -> Override vs Ignore -> Layer 2 warning -> On-task reset) and Strict mode (Layer 2 queues `close_tabs` in `CommandQueue` for browser tabs, calls minimize for desktop apps, and sets lockout).
- **Assigned model:** **Hydrogen**

---

## Phase 5: UI, Logging, Review

### T5.1 — SQLite Session & Activity Logger
- **Files:** `app/logger.py`
- **What to build & goal:** Implement `SessionLogger` with thread-safe SQLite connections (e.g., `threading.Lock` or short-lived connections per call), creating `sessions` and `activity_logs` tables, recording session start/end, logging every evaluated event (text only), updating `user_override`, and fetching logs for review.
- **Contracts:** `CONTRACTS.md` §4 (`SessionLogger`), §7 (SQLite Schema).
- **Acceptance test:** Run a Python test creating a temp DB, starting a session, logging 3 events, marking 1 override, finishing the session, and reading back all rows cleanly.
- **Assigned model:** Flash

### T5.2 — Tkinter Setup Window, Monitoring HUD, and End-of-Session Review Screen
- **Files:** `app/ui.py` (setup, HUD, and review views)
- **What to build & goal:** Build the Tkinter setup window (goal input, duration minutes, Normal/Strict radio toggle, background profile build indicator, "I'm ready" button), compact monitoring HUD (current goal, mode, state badge, "End Session" button), and scrollable end-of-session review screen displaying the Gemini-generated summary.
- **Contracts:** `CONTRACTS.md` §4 (`UIHooks`: `show_setup`, `show_monitoring_hud`, `update_hud_status`, `show_review`).
- **Acceptance test:** Launch `app/ui.py` standalone test harness, enter goal `"DSA"`, toggle Strict mode, click "I'm ready", verify HUD appears, then trigger `show_review` with sample text and verify clean exit.
- **Assigned model:** Flash

### T5.3 — Layer 1/2 Popups, Strict Full-Screen Overlay, and Main App Threading Orchestration
- **Files:** `app/ui.py` (Layer 1 popup, Layer 2 warning, strict overlay, thread-safe queue pump), `app/main.py`
- **What to build & goal:**
  - In `app/ui.py`: implement thread-safe UI dispatch (`queue.Queue` + `root.after(...)`) so worker threads never touch Tkinter directly; build Layer 1 Ask popup (*Back to work* / *This is relevant*), Layer 2 Normal loud warning, and Strict-mode full-screen topmost overlay ("Start `<goal>` again.") that stays active until lockout expires and the post-lockout check passes (or emergency exit shortcut for demo safety).
  - In `app/main.py`: wire `Config`, `SessionLogger`, `TabSnapshotStore`, `CommandQueue`, FastAPI/uvicorn thread, `WindowWatcher`, `VerdictCache`, `EventEngine`, `AgentStateMachine`, and `TkinterUI` end-to-end, including baseline check on "I'm ready", state-machine tick loop, and session-end review generation.
- **Contracts:** `CONTRACTS.md` §1, §4 (`UIHooks`, `EventEngine`, `AgentStateMachine`, `SessionLogger`), §6.
- **Acceptance test:** Run `python -m app.main` in `dev` mode with extension loaded; complete the full loop: Setup -> "I'm ready" (baseline check) -> Instagram (Tier 0 -> Grace -> Layer 1 -> Layer 2 Strict tab close + overlay) -> return to allowed app -> End Session -> Review displayed and saved in SQLite.
- **Assigned model:** **Hydrogen**

---

## Phase 6: Demo Polish

### T6.1 — Dev/Demo Timing Verification, Error Hardening, and Demo Runbook
- **Files:** `README.md` (setup, 3-step unpacked extension install, `.env` instructions, 2-minute demo script checklist), minor resilience polish in `app/main.py` / `app/ui.py` if needed.
- **What to build & goal:** Document 3-step extension installation, `.env` configuration, `dev` vs `demo` toggle, and perform a rehearsed end-to-end dry run ensuring graceful recovery if the extension is disconnected or Gemini briefly errors.
- **Contracts:** `CONTRACTS.md` §2 (`DEV_TIMINGS` vs `DEMO_TIMINGS`), `plan.md` §11 (Demo script).
- **Acceptance test:** Follow `README.md` on a fresh run in `demo` mode (or fast `dev` verify + `demo` switch) and confirm all 6 steps of the 2-minute demo script execute without unhandled exceptions.
- **Assigned model:** Flash *(Note: likely escalation candidate if rehearsed demo reveals a cross-thread race condition.)*

---

## Decisions and Assumptions

1. **Added Dependencies (beyond `plan.md`):**
   - `Pillow`: Required to downscale `mss` raw BGRA frames to `1024 px` wide and encode as in-memory JPEG bytes cleanly.
   - `python-dotenv`: Standard `.env` loader so `GEMINI_API_KEY` and `ANTI_DISTRACTOR_ENV` work reliably across PowerShell/IDE runs.
   - `pydantic` (explicit): Used by both `fastapi` and `google-genai`; listing it explicitly simplifies structured JSON schema validation.
2. **Two Confidence Thresholds:** `plan.md` uses low confidence both to trigger Tier 2 escalation (Tier 1 unsure) and to avoid punishing uncertainty (`< 0.6`). We split these cleanly into `TIER1_ESCALATION_CONFIDENCE = 0.75` (Tier 1 -> Tier 2) and `PUNISH_CONFIDENCE_THRESHOLD = 0.60` (final verdict `< 0.60` treated as `on_task=True`).
3. **Extension Transport (HTTP Polling vs WebSocket):** Chose HTTP `POST /tabs` + `GET /commands` polling every `1.5–2.0 s` over WebSockets because MV3 service workers suspend idle WebSockets aggressively and polling is trivial to debug in a hackathon.
4. **Gemini Model IDs:** Placeholder defaults `gemini-2.5-flash-lite` (Tier 1 text) and `gemini-2.5-flash` (Tier 2 vision/profile) are set in `app/config.py`; verify exact current free-tier model names in Google AI Studio during `T0.1`.
5. **Override Semantics:** Clicking *"This is relevant"* caches `(source_key, normalized_title)` as `on_task=True, user_override=True`. However, periodic heartbeat or dwell vision checks (`force_vision=True`) bypass the cache and evaluate fresh screen content so prolonged off-task drift after a false override is still caught at Layer 2.
6. **Multi-monitor Scope:** Per `plan.md` §13, only the primary monitor (`mss` monitor index `1`) is captured.
7. **Tkinter Threading Model:** Tkinter runs exclusively on the main thread; FastAPI (`uvicorn`), `WindowWatcher`, and `EventEngine` run on daemon threads and push UI commands through a thread-safe `queue.Queue` drained via `root.after(100, ...)`.

## Risks

- **Windows Focus & Browser Title Quirks:** Minimized/UWP apps or browser popups may report empty titles or `ApplicationFrameHost.exe`; `window_watcher.py` must guard against `pywin32` access-denied errors on elevated processes.
- **MV3 Service Worker Suspension:** Chrome/Edge can sleep an idle MV3 worker; `tabs` event listeners wake it for `POST /tabs`, and `chrome.alarms` / active interval polling must keep `/commands` responsive during off-task states.
- **Strict Overlay Lockout Trap:** An uncloseable topmost Tkinter overlay could lock out the presenter during a live demo if something hangs; include a discreet emergency hotkey (e.g. `Ctrl+Shift+Esc` / `F12` escape hatch or strict max lockout timer) so the demo can never freeze the presenter's laptop.
- **Free-Tier Gemini Rate Limits:** Rapid manual testing can hit RPM limits; `DEV_TIMINGS` + Tier 0 cache + `GeminiRateLimitError` fallback mitigate this, and unit tests should mock Gemini where possible.
- **Tkinter Cross-Thread Crashes:** Any direct widget call from FastAPI or `EventEngine` threads will crash Tcl/Tk; all UI updates must flow strictly through `UIHooks` queue dispatch.
