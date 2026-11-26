# PROGRESS.md

## Task Checklist

- [x] **T0.1** (Flash) — Environment, Config, Data Models, and Gemini Smoke Check (`done`)
- [x] **T1.1** (Flash) — In-Memory Screenshot Capture & Downscaling (`done`)
- [x] **T1.2** (Flash) — Goal Profile Generation & Normalization Helpers (`done`)
- [x] **T1.3** (Flash) — Tier 1 Text Judge, Tier 2 Vision Judge, and Session Review AI Calls (`done`)
- [x] **T2.1** (Flash) — FastAPI Local Receiver and Command Queue (`done`)
- [x] **T2.2** (Flash) — Chrome/Edge Manifest V3 Extension (`done`)
- [x] **T3.1** (Flash) — Windows Foreground Window Watcher & Minimize Helper (`done`)
- [x] **T3.2** (Flash) — Verdict Cache & Tier 0 Rule Evaluator (`done`)
- [x] **T3.3** (Hydrogen) — Event Engine, Tier Escalation, Dwell/Heartbeat, and Rate-Limit Fallback (`done`)
- [x] **T4.1** (Hydrogen) — Deterministic Agent State Machine & Strict Mode Enforcement (`done`)
- [x] **T5.1** (Flash) — SQLite Session & Activity Logger (`done`)
- [x] **T5.2** (Flash) — Tkinter Setup Window, Monitoring HUD, and End-of-Session Review Screen (`done`)
- [x] **T5.3** (Hydrogen) — Layer 1/2 Popups, Strict Full-Screen Overlay, and Main App Threading Orchestration (`done`)
- [x] **T6.1** (Flash) — Dev/Demo Timing Verification, Error Hardening, and Demo Runbook (`done`)

## Notes
- **T0.1 completed**:
  - Installed missing dependencies: `mss` (10.2.0), `psutil` (7.2.2), `pywin32` (312).
  - Created `.env` from `.env.example`.
  - Implemented `get_timings()` and `get_gemini_api_key()` with automatic `dotenv` loading in `app/config.py`.
  - Verified `app/models.py` dataclasses and type definitions.
  - Acceptance tests passed: `get_timings('dev')` and `get_timings('demo')` return valid `TimingConfig` instances.
  - SDK verification: `google-genai` (2.33.0) client and types import and load cleanly. Live Gemini API smoke test passed using `gemini-3.5-flash`.
- **T1.1 completed**:
  - Implemented `capture_screenshot_bytes` in `app/screen.py` with in-memory `mss` grab, Pillow downscaling (&le;1024px), and JPEG encoding.
  - Added safe Windows default desktop handle attachment to support execution across interactive and background thread contexts.
  - Acceptance tests passed: returns valid JPEG bytes (`magic: b'\xff\xd8'`), length > 5000 bytes, zero files written to disk.
- **T1.2 completed**:
  - Implemented `normalize_domain`, `normalize_title`, `generate_goal_profile`, and `fallback_goal_profile` in `app/profile.py`.
  - Structured output validated via `pydantic` schema and Gemini API.
  - Acceptance tests passed: verified goal profile expansion on `"Study DSA"` correctly returns on-task topics, allowed domains (`leetcode.com`, `geeksforgeeks.org`), borderline domains (`youtube.com`, `reddit.com`), and blocked domains (`instagram.com`, `tiktok.com`).
- **T1.3 completed**:
  - Implemented `tier1_evaluate` (text-only AI evaluation with metadata and open tabs/windows), `tier2_evaluate` (multimodal AI evaluation combining live screenshot bytes and context), and `generate_session_review` in `app/judge.py`.
  - Added `GeminiRateLimitError` detection and clean propagation.
  - Acceptance tests passed: `tier1_evaluate` returned on-task verdict for Wikipedia BST article (`confidence: 0.95`); `tier2_evaluate` analyzed live desktop screenshot and recognized off-task IRCTC booking (`confidence: 0.98`, identified `tabs_to_close`); `generate_session_review` generated a comprehensive markdown focus report.
- **T2.1 completed**:
  - Implemented `TabSnapshotStore` with thread-safe locking and automatic domain normalization.
  - Implemented `CommandQueue` with UUID command generation, in-flight TTL expiry (15s), and acknowledgment tracking.
  - Implemented FastAPI app with `GET /health`, `POST /tabs`, `GET /commands`, `POST /commands/ack`, and CORS support.
  - Implemented `start_server_thread` to host uvicorn cleanly in a background daemon thread.
  - Acceptance tests passed: verified all HTTP endpoints, event triggering, tab store updates, command queuing, and ack-clearing over localhost:8765.
- **T2.2 completed**:
  - Implemented Chrome/Edge Manifest V3 extension in `extension/` (`manifest.json`, `background.js`, `popup.html`, `popup.js`).
  - Added listeners for `tabs.onActivated`, `tabs.onUpdated`, `tabs.onCreated`, `tabs.onRemoved`, and `windows.onFocusChanged` with micro-debouncing.
  - Implemented 1.5s command polling loop with `chrome.alarms` keepalive fallback for service worker lifecycles.
  - Implemented strict-mode `chrome.tabs.remove` execution and `/commands/ack` reporting.
  - Verified syntax cleanly via Node.js and validated manifest schema.
- **T3.1 completed**:
  - Implemented `get_foreground_window`, `list_open_window_titles`, `minimize_window`, and `WindowWatcher` in `app/window_watcher.py`.
  - Added desktop station connection resilience and UWP `ApplicationFrameHost.exe` child process unwrapping.
  - Acceptance tests passed: verified foreground window queries (`claude.exe`, `antigravity.exe`), window enumeration, safe minimization via `SW_MINIMIZE`, and background thread polling with change detection.
- **T3.2 completed**:
  - Implemented `VerdictCache` in `app/cache.py` with thread-safe locking, TTL expiry (1800s), normalized key generation (`source_key` and `title`), and `record_override`.
  - Implemented `tier0_evaluate` in `app/judge.py` with cache-first lookup, domain rule matching (`allowed_domains`, `blocked_domains`), app matching (`allowed_apps`, `blocked_apps`), and borderline domain passthrough to `None`.
  - Smoke tests passed: `instagram.com` -> on_task=False (tier 0, closes tab), `leetcode.com` -> on_task=True (tier 0), `youtube.com` -> None (borderline passthrough), `spotify.exe` -> on_task=False (tier 0), `code.exe` -> on_task=True (tier 0), user override -> on_task=True (cached), TTL expiration verified.
- **T3.3 completed**:
  - Implemented `evaluate_context` in `app/judge.py` with Tier 0 -> Tier 1 -> Tier 2 (`TIER1_ESCALATION_CONFIDENCE`) escalation, `force_vision` cache bypass, `PUNISH_CONFIDENCE_THRESHOLD` fairness gating, automatic `VerdictCache` writes, screenshot-failure fallback (to Tier 1 / safe on-task), `GeminiRateLimitError` fallback to Tier 0 / safe on-task, and non-rate-limit Tier 1/2 exception fallbacks so transient API errors never crash evaluation.
  - Guarded `tier0_evaluate` domain matching on `foreground_window is None or foreground_window.is_browser` so background browser tabs don't trigger blocks when the user is in a non-browser app.
  - Implemented `EventEngine` in `app/event_engine.py` with a single `threading.Condition` worker loop handling `baseline`, debounced `submit_trigger`, borderline `dwell` timer, periodic `heartbeat`, `run_immediate_check` (including pre-start sync exception isolation), rate-limit backoff, exception isolation, and clean `start()`/`stop()`.
  - Added 16 mocked unit/integration tests in `tests/test_event_engine.py` (covering Tier 0 no-AI, escalation, confidence gating, cache/force_vision, debounce, dwell & heartbeat with both real and mocked `clock_fn`, rate-limit backoff & recovery, screenshot failure, non-rate-limit exception fallbacks, and clean `stop()`) — all 16 passed via `python -m pytest tests -q` (`16 passed in 1.85s`), plus 1 earlier live Tier 1 Gemini smoke test.
  - Verified T3.3 against full checklist (triggers, debounce, Tier 0→1→2 escalation, low-confidence on-task gate, caching, rate-limit/screenshot/exception fallbacks, `on_verdict` callback, clean `start()`/`stop()`): no code gaps or test failures found.
- **T4.1 completed**:
  - Implemented `AgentStateMachine` in `app/state_machine.py` with Normal and Strict modes, handling strike counts, grace period, Layer 1 ask, user overrides ("relevant" and "back to work"), Layer 2 ultimatum, and safe side-effect dispatch.
  - Implemented Strict mode enforcement: snapshot-validated tab closure filtering via `CommandQueue`, window minimization via `minimize_window`, and lockout timer expiry.
  - Added 13 unit tests in `tests/test_state_machine.py` covering state transitions, override flows, deadlock safety, and lockout expiration without premature grace resets.
- **T5.1 completed**:
  - Implemented thread-safe `SessionLogger` in `app/logger.py` with scoped connection management and SQLite row mapping.
  - Created `sessions` and `activity_logs` tables following `CONTRACTS.md` §7.
  - Verified session start, text-only event logging, override marking, session finishing with review text, and ordered log fetching.
  - Acceptance tests passed cleanly in `tests/test_logger.py` (`test_session_logger_lifecycle`).
- **T5.2 completed**:
  - Implemented `TkinterUI` in `app/ui.py` with thread-safe `_queue` dispatch and `root.after()` event pump.
  - Built the session Setup window (goal input, duration validation, Normal vs Strict radio buttons, and "I'm ready" action).
  - Built the compact topmost monitoring HUD (goal title, mode badge, color-coded state badge, latest activity summary, and "End Session" action).
  - Built the scrollable AI Session Review window with markdown formatting support and clean close action.
  - Verified via `tests/test_ui.py` and standalone test harness (`ALL 31 TESTS PASSED`).
- **T5.3 completed**:
  - Implemented Layer 1 popup ("Back to work" / "This is relevant"), Layer 2 warning popup with alert sound, and Strict-mode full-screen topmost dark overlay (`show_strict_overlay`) with live countdown and demo emergency escape hatch (`Esc` / `F12`) in `app/ui.py`.
  - Implemented `AppController` in `app/main.py` orchestrating `Config`, `SessionLogger`, `TabSnapshotStore`, `CommandQueue`, `WindowWatcher`, `EventEngine`, `AgentStateMachine`, and `TkinterUI` across background threads and main Tkinter GUI loop.
  - Added zero-quota fake judge support (`ANTI_DISTRACTOR_FAKE_JUDGE=1`) for local offline validation and rehearsed demos.
  - Verified via `tests/test_main_wiring.py` covering session start, tab/window event triggers, state machine decisions, override propagation, and session completion review (`ALL 32 TESTS PASSED`).
- **T6.1 completed**:
  - Implemented `tests/test_demo_runbook.py` verifying both `dev` and `demo` timing configurations, simulating all 6 steps of the 2-minute live demo script (Setup -> on-task silent pass -> off-task grace period -> Layer 1 popup -> Layer 2 strict tab closure & overlay -> lockout expiration & post-session review), and testing extension disconnect resilience.
  - Authored comprehensive [README.md](file:///d:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/README.md) with system architecture diagram, quick start, 3-step browser extension loading instructions, 2-minute demo runbook, timing parameters, test suite overview, and troubleshooting FAQs (including emergency escape hatches).
  - Verified full test suite (`python -m pytest tests -q`): **35 passed**, 0 failures.
- **Model Preference Hierarchy, Failover Hardening & 20-Image Benchmark completed**:
  - Configured 3-tier model preference system in `app/config.py`:
    - Preference 1: `gemini-3.5-flash-lite` (primary for both Tier 1 text and Tier 2 vision)
    - Preference 2: `gemma-4-31b-it` (automatic failover on 429/quota/tokens exhausted, 404, timeouts, or network exceptions)
    - Preference 3: `gemma-4-26b-a4b-it` (tertiary fallback)
    - Last resort: Shift to Tier 0 local rules only (no AI) with user notification popup.
  - Implemented `ModelPreferenceManager` in `app/judge.py` with thread-safe preference tracking, automatic failover across 404, 429, 500, 502, 503, 504, timeouts, connection errors, and token depletion.
  - Added HTTP 400 schema retry before failover in `tier1_evaluate` and `tier2_evaluate`.
  - Added markdown code fence stripping (` ```json ... ``` `) in `_extract_json_string` prior to JSON parsing.
  - Added `show_tier0_shift_popup` to `UIHooks` and `TkinterUI` in `app/ui.py` to notify users whenever all AI models are offline, and set visible HUD status to `"AI unavailable - rules only"` so failed checks never silently count as normal on-task.
  - Fixed all "Gemma 24B" typo occurrences to "Gemma 4 26B A4B" across config, judge, UI, and test suites.
  - Implemented unit tests in `tests/test_model_preferences.py` covering failover sequence, 404/timeout/network failover, markdown fence stripping, Tier 0 shift popup and HUD status, and automatic recovery probing (6 tests in suite, 41 total passed).
  - Created 20 realistic desktop benchmark screenshots (`scripts/run_image_benchmark.py`) covering 10 DSA on-task scenarios and 10 non-DSA distraction scenarios.
  - Verified benchmark runner: live API inference (`ANTI_DISTRACTOR_FAKE_JUDGE=False`), cache bypassed, real generated image bytes, and `model_used` logged for every image.
  - Benchmarked all 20 images against the preference models: **100.0% accuracy** (20/20 correct predictions, 0 false positives, 0 false negatives, average confidence 1.00).
- **What's left**: None. All phases, tasks, model preference hierarchy, and benchmark evaluations are 100% complete.

## Deviations
- Updated `GEMINI_TEXT_MODEL` and `GEMINI_VISION_MODEL` in `app/config.py` to `gemini-3.5-flash-lite` and `gemini-3.5-flash` because the legacy 2.5 models were retired for new keys in AI Studio (HTTP 404).
- [Review Fix 1] Config: `load_dotenv` now explicitly resolves `.env` from project root with `override=True` to prevent accidental inheritance of ambient shell keys.
- [Review Fix 2] Judge/Profile: Explicitly reject empty/whitespace API keys with `ValueError` in `tier1_evaluate`/`tier2_evaluate` and return `fallback_goal_profile` in `generate_goal_profile` to prevent silent ambient fallback.
- [Review Fix 3] Judge: `generate_session_review` now catches all exceptions (including rate limits and invalid keys) and returns a deterministic local summary so session end never crashes.
- [Review Fix 4] Server: `CommandQueue.poll_commands` now only appends commands that are newly transitioned to in-flight, preventing duplicate re-deliveries during extension polling, and `enqueue_close_tabs` deduplicates tab IDs.
- [Review Fix 5] Extension: Prevent overlapping command polls via reentrancy lock `isPollingCommands`, and filter `tabs.onUpdated` to only trigger snapshots when the updated tab is active.
- [Review Fix 6] Judge: `_parse_verdict_response` validates non-empty model responses and strictly filters `tabs_to_close` against currently known tab IDs to prevent closing non-existent tabs; narrowed `_is_rate_limit_error` to code 429 and RESOURCE_EXHAUSTED.
- [Review Fix 7] Profile: `normalize_domain` now rejects non-web schemes (`file://`, `about:`, `chrome:`, `edge:`, etc.) and returns empty string to prevent polluting cache and Tier 0 domain matching.
- [Review Fix 8] Server: Set CORS `allow_credentials=False` for wildcard origins, replaced silent exception pass in `/health` and `/tabs` callbacks with `logger.exception`, and added synchronous startup verification in `start_server_thread` to fail fast on port collision.
- [T3.3] `tier0_evaluate` in `app/judge.py` now skips `tab_snapshot` domain rules when `foreground_window.is_browser` is `False` so background browser tabs do not override an active allowed desktop app.
- [T3.3] `EventEngine` in `app/event_engine.py` uses a single `threading.Condition` worker loop (with optional injectable test hooks) instead of multiple `threading.Timer` threads, and heartbeat checks preserve an active borderline dwell deadline so `dwell` can still fire when `heartbeat_seconds < dwell_seconds` (as in `DEV_TIMINGS`).
- [T4.1 Post-Review] In `app/state_machine.py`, strict lockout expiration now preserves state in `LAYER_2_ULTIMATUM` and retains strike count without prematurely resetting to `ON_TASK` or granting a new grace period. When `on_lockout_done` triggers an immediate check, an on-task verdict resets to `ON_TASK` and clears warnings, while an off-task verdict returns immediately to Layer 2 strict enforcement.
- [Multi-Model Preference & Failover] Expanded failover triggers in `app/judge.py` to cover 404, timeouts, connection/network errors, and token exhaustion. HTTP 400 schema mismatches retry without schema before failing over. Markdown code fences are stripped before JSON decoding. Exhaustion of all AI models sets HUD status to `"AI unavailable - rules only"` and triggers a one-time user alert. Corrected "Gemma 24B" label typos to "Gemma 4 26B A4B". Added global pytest test isolation fixture in `tests/conftest.py`.
- [UI & Visibility Polish] Implemented Items 1-5: Start button shows 'Starting...' and re-enables on failure; Layer 2 warning popup auto-closes after 12s; HUD is compact and draggable with clear AI unavailable status styling; activity_logs schema and migration store model_used with end-of-session review breakdown and HUD 'AI calls: T1 x / T2 y' counter; last session settings are persisted to last_session.json (in .gitignore) and prefilled in setup window.
- [UI/UX Overhaul & Window/Self-Monitoring Fixes] Added DPI-aware dynamic window sizing (`_fit_window`) and window-state resets (`_reset_window_state`) across all `TkinterUI` views so the Setup button and Tier 0 popup never clip at >100% Windows scaling, maximized Setup transitions cleanly to a compact non-maximizable HUD, and Review centers properly without Windows `SetWindowPos` offset bugs. Added HUD pin toggle (`-topmost`), minimize button, live `MM:SS` timer, Setup preset chips (`15m/25m/45m/60m`) + inline validation, Layer 2 live auto-close countdown, Review in-place update + `New Session` button, thread-safe `TkinterUI.close()`, bounded `_pump` queue draining, and self-PID filtering in `app/window_watcher.py` and `app/judge.py` (`tier0_evaluate`) so interacting with Anti-Distractor's own windows never triggers off-task checks or self-minimization.
- [Feature Upgrades: Extension Dot, Pause/Snooze, Editable Rules, System Tray, Multi-Monitor Overlay & Dark Mode] Added `pystray>=0.19.5` dependency, `TabSnapshotStore` extension heartbeat tracking (`is_extension_connected`), `EventEngine` pause/resume and `update_profile`, backward-compatible optional `UIHooks` and Setup/HUD callbacks (`update_extension_status`, `update_pause_status`, `show_rules_editor`, `minimize_to_tray`/`restore_from_tray`, Dark/Light `set_theme` persisted in `last_session.json`), and Windows virtual-desktop multi-monitor `show_strict_overlay` bounds.

## Escalation suggestions


## T4.1 (Step 1 of 2) — Core Agent State Machine
- **Status:** Done (Step 1 of 2; Step 2 strict-mode enforcement pending).
- **Files changed:** [app/state_machine.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/state_machine.py).
- **Tests:** Not run (tool-call budget reserved for implementation and progress update per task limits).
- **Deviations:** Added optional `ui_hooks: UIHooks | None = None`, `clock: Callable[[], float] = time.monotonic`, and `on_override` parameters (and defaulted `command_queue` and `on_decision` to `None`) in `AgentStateMachine.__init__` so the state machine drives `UIHooks` directly and supports injectable clocks in tests while keeping `CONTRACTS.md` compatibility.
- **What's left:** T4.1 Step 2 — implement strict-mode enforcement inside `AgentStateMachine._enforce_strict_mode`.

### T4.1 (Step 2 of 2) — Strict Mode Enforcement [DONE]
- **Status:** Completed
- **Files changed:** `app/state_machine.py`
- **Summary:** Implemented `_enforce_strict_mode`, snapshot-validated tab closing (`_resolve_tabs_to_close`), non-browser foreground window minimization (`_resolve_minimize_hwnd` + `minimize_window`), strict lockout expiry in `handle_tick`, `complete_lockout()` for overlay timer/demo escape hatch completion, and resilient side-effect dispatch (`_dispatch_side_effects`).
- **Deviations:** Added optional `minimize_fn` and `on_lockout_done` constructor parameters plus `complete_lockout()` on `AgentStateMachine` (backwards-compatible with `CONTRACTS.md`) so `UIHooks.show_strict_overlay` completion/escape hatch and `EventEngine.run_immediate_check` can cleanly terminate strict lockout and test safely without Win32 side effects.
- **Test result:** Passed custom T4.1 Normal + Strict mode acceptance test (`ALL T4.1 ACCEPTANCE TESTS PASSED`, exit code 0) covering snapshot tab-ID filtering, active-tab fallback, non-browser minimize, tick/on-task/escape-hatch lockout ending, and missing/raising queue/window/snapshot resilience.
- **What's left:** None — Phase 6 (T6.1) is complete. The full project is wrapped up and production-ready.

### UI Review Top 5 Polish [DONE]
- **Files changed:** [app/ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/ui.py), [app/main.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/main.py), [tests/test_ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_ui.py), [UI_REVIEW.md](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/UI_REVIEW.md).
- **Summary:** Replaced `<Control-Shift-Escape>` with `<Escape>`/`<F12>` in `show_strict_overlay`; enabled Windows DPI awareness in `main()`; deduplicated and tracked `show_tier0_shift_popup` in `_active_popups`; added lightweight Markdown rendering (`#`/`##`/`###`, `**bold**`, bullets) and a `Copy Summary` button in `show_review`; bound `<Return>` to start in `show_setup`, bound drag events to all non-button HUD labels, and styled `"AI unavailable"` in `#C62828` red in `update_hud_status`.
- **Test result:** `python -m pytest tests -q` — **50 passed** in 3.15s.
- **What's left:** Remaining P0/P1/P2 items in `UI_REVIEW.md` if desired later.

### Full UI/UX Redesign, Window Trap Fixes & Self-Monitoring Guard [DONE]
- **Files changed:** [app/ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/ui.py), [app/main.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/main.py), [app/window_watcher.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/window_watcher.py), [app/judge.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/judge.py), [scripts/ui_demo.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/scripts/ui_demo.py), [tests/test_ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_ui.py), [tests/test_main_wiring.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_main_wiring.py).
- **Summary:**
  - Fixed Setup window DPI clipping (`460x380` crushed `start_btn` to `h=1px` at 125% Windows scaling) and Tier 0 popup clipping via DPI-aware `_fit_window()` dynamic sizing.
  - Fixed the maximized Setup -> fullscreen `-topmost` HUD trap by resetting `state("normal")` / `-fullscreen False`, setting `resizable(False, False)` on HUD (disabling Maximize), positioning HUD in the top-right corner, and adding one-click `Pinned`/`Unpinned` (`-topmost` toggle) and Minimize (`—`) controls.
  - Fixed Windows `SetWindowPos` geometry ordering bug in `show_review` that left the Review screen partially off the right edge of the monitor after transitioning from the top-right HUD.
  - Redesigned all 7 screens (Setup, HUD, Layer 1, Layer 2, Tier 0, Strict Overlay, Review) with a unified modern Slate/White/Indigo design system matching `extension/popup.html`, including Setup duration presets (`15m/25m/45m/60m`), clickable mode cards, inline validation, HUD live `MM:SS` timer, Layer 2 live countdown, Strict overlay progress bar, Review in-place async updates + `New Session` button, bounded `_pump` queue draining, and thread-safe `TkinterUI.close()`.
  - Filtered Anti-Distractor's own PID in `WindowWatcher`, `list_open_window_titles`, `minimize_window`, and `tier0_evaluate` so interacting with the HUD/popups never triggers off-task checks or self-minimization; fixed `end_session` during startup and HUD startup status race.
- **Test result:** `python -m pytest tests -q` — **53 passed, 0 failed** in 2.62s; visually verified all 7 screens via Win32 `PrintWindow` captures + live `python -m app.main` launch smoke check.
- **What's left:** None for core UI/UX bugs; optional future enhancements noted in response (system tray icon, multi-monitor overlay, editable domain allow/block lists in UI, live extension connection indicator on HUD).

### Feature Upgrades: Extension Dot, Pause/Snooze, Editable Rules, System Tray, Multi-Monitor Overlay & Dark Mode [DONE]
- **Files changed:** [requirements.txt](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/requirements.txt), [app/server.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/server.py), [app/event_engine.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/event_engine.py), [app/tray.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/tray.py), [app/main.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/main.py), [app/ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/ui.py), [tests/test_event_engine.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_event_engine.py), [tests/test_main_wiring.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_main_wiring.py), [tests/test_ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_ui.py).
- **Summary:**
  - **Extension Connection Dot:** `TabSnapshotStore` records heartbeats on `/tabs`, `/commands`, and `/commands/ack` and exposes `is_extension_connected(stale_seconds=8.0)`; `AppController._on_tick` updates the HUD footer dot (`Ext: Live` / `Ext: Off`).
  - **Pause / Snooze (5m Break):** Added `EventEngine.pause`/`resume`, `AppController.pause_session`/`resume_session`/`toggle_pause` (extending `_session_deadline` on resume and auto-resuming at countdown end), and HUD `Pause 5m`/`Resume` button + `PAUSED` status badge + live break countdown.
  - **Editable Domain/App Rules Preview:** Added tabbed Websites (`allowed_domains`, `borderline_domains`, `blocked_domains`) and Desktop Apps (`allowed_apps`, `blocked_apps`) editor dialog (`show_rules_editor`), accessible pre-session from Setup (`Edit Rules`) and live mid-session from HUD (`Rules`), updating `EventEngine` profile and clearing `VerdictCache`.
  - **System Tray Icon (`pystray`):** Implemented `SystemTrayManager` and `create_tray_icon_image` in `app/tray.py`, HUD `Tray` button (`minimize_to_tray` / `restore_from_tray`), menu actions (Show HUD, Pause / Resume, End Session, Quit), and clean shutdown on exit.
  - **Multi-Monitor Strict Overlay & Dark/Light Theme Toggle:** `show_strict_overlay` now spans the full virtual desktop via Windows `GetSystemMetrics` (`76/77/78/79`) while centering the lockout card on the primary monitor (`0/1`), and added `LIGHT_PALETTE`/`DARK_PALETTE` theme switching across Setup, HUD, Review, Popups, and Rules Editor persisted in `last_session.json`.
- **Test result:** `python -m pytest tests -v` — **56 passed, 16 warnings in 2.81s**; verified Win32 `PrintWindow` screenshots at 125% DPI (Light/Dark Setup, Light/Dark HUD, Rules Editor Websites & Desktop Apps tabs, Dark Review), real `pystray` `SystemTrayManager` start/update/stop lifecycle, and clean `ANTI_DISTRACTOR_FAKE_JUDGE=1 python -m app.main` launch smoke check.
- **What's left:** None — ready for review/commit when requested.

### Documentation, Contracts & Config Sync [DONE]
- **Files changed:** [README.md](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/README.md), [.env.example](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/.env.example), [CONTRACTS.md](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/CONTRACTS.md), [UI_REVIEW.md](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/UI_REVIEW.md), [scripts/ui_demo.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/scripts/ui_demo.py), [PROGRESS.md](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/PROGRESS.md).
- **Summary:** Updated `README.md` (key highlights, architecture diagram, setup/HUD/review controls, optional `.env` overrides, 2-minute demo notes, exact 56-test breakdown, tray & troubleshooting FAQ), `.env.example`, `CONTRACTS.md` (layout, model preferences, `JudgeVerdict.model_used`, `EventEngine` pause/update_profile, `SystemTrayManager`, updated `UIHooks`, extension heartbeat, and SQLite `model_used` schema), `UI_REVIEW.md` resolution summary, and `scripts/ui_demo.py` HUD/rules/pause/review hooks.
- **Test result:** `python -m pytest tests -v` — **56 passed, 0 failed**; `python -c "import scripts.ui_demo; print('ok')"` exited cleanly (`ok`).
- **What's left:** None — ready for review/commit when requested.

### Repository Root Cleanup for GitHub Front Page [DONE]
- **Files changed:** [README.md](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/README.md), [scripts/run_image_benchmark.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/scripts/run_image_benchmark.py), [docs/AGENTS.md](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/docs/AGENTS.md), [docs/CONTRACTS.md](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/docs/CONTRACTS.md), [docs/PROGRESS.md](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/docs/PROGRESS.md), moved internal docs (`AGENTS.md`, `CONTRACTS.md`, `PROGRESS.md`, `REVIEW_FIXES.md`, `UI_REVIEW.md`, `implementation_plan.md`, `plan.md`) into `docs/`, and moved `benchmark_images/` into `scripts/benchmark_images/`.
- **Summary:** Decluttered the repository root so GitHub displays only `README.md`, essential config files (`.env.example`, `.gitignore`, `requirements.txt`), and clean top-level directories (`app/`, `docs/`, `extension/`, `scripts/`, `tests/`) while preserving local runtime/secret files in place.
- **Test result:** Verified with `python -m pytest tests -v` (**56 passed, 0 failed**) and `scripts.run_image_benchmark.BENCHMARK_DIR` / `scripts.ui_demo` imports.
- **What's left:** None — ready to commit/push when requested.

### 15 UX Polish Upgrades & Power Features [DONE]
- **Files added/changed:** [app/win32_ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/win32_ui.py), [app/hotkeys.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/hotkeys.py), [app/settings.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/settings.py), [app/ui_dialogs.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/ui_dialogs.py), [app/models.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/models.py), [app/state_machine.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/state_machine.py), [app/logger.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/logger.py), [app/server.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/server.py), [app/ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/ui.py), [app/main.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/main.py), [extension/background.js](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/extension/background.js), [extension/popup.html](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/extension/popup.html), [extension/popup.js](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/extension/popup.js), [.gitignore](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/.gitignore), [README.md](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/README.md), [docs/CONTRACTS.md](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/docs/CONTRACTS.md), [tests/test_win32_and_hotkeys.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_win32_and_hotkeys.py), [tests/test_settings_and_presets.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_settings_and_presets.py), [tests/test_server_and_quick_rules.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_server_and_quick_rules.py), [tests/test_logger.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_logger.py), [tests/test_state_machine.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_state_machine.py), [tests/test_ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_ui.py), [tests/test_main_wiring.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_main_wiring.py).
- **Summary:** Implemented all 7 UX polish fixes and 8 new features: branded target window icon + Windows DWM dark titlebar, `WS_EX_NOACTIVATE` non-focus-stealing Layer 1/2 popups, virtual-screen multi-monitor HUD drag + persisted `(hud_x, hud_y)` + monitor-aware popups, hover tooltips + pause hover-color sync + live `Grace: Xs` countdown + Mini-Pill HUD toggle, smart conflict-preventing Rules Editor (`<Delete>` binding, `AI Suggest Rules`, 1-click current site/app allow/block), staged startup progress + Review KPI cards + animated review progress + `Save .md`, Chrome/Edge MV3 dark-mode popup + live `/status` + toolbar badge (`ON`/`BRK`/`!`) + `POST /rules/quick` + Layer 1 `Always allow`, SQLite History/Streaks/Top Distractions dashboard, Strict Mode `Closed Tabs Vault`, Live Activity & Explainability Drawer, Saved Focus Presets (`presets.json`), built-in Pomodoro work/break cycles, global Win32 hotkeys (`Ctrl+Alt+P/H/R`), and Settings dialog (`settings.json` + `.env` API key config/test).
- **Test result:** `python -m pytest tests -v` — **70 passed, 0 failed**; visually verified 8 screenshots via Win32 `PrintWindow` at 125% Windows DPI + `ANTI_DISTRACTOR_FAKE_JUDGE=1 python -m app.main` clean 2s launch + `node --check extension/background.js extension/popup.js`.
- **What's left:** None.

### First-Launch Gemini API Key Onboarding & Linear / Vercel UI Restyle [DONE]
- **Files added/changed:** [app/theme.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/theme.py), [app/win32_ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/win32_ui.py), [app/ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/ui.py), [app/ui_dialogs.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/ui_dialogs.py), [app/settings.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/settings.py), [app/config.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/config.py), [app/main.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/app/main.py), [requirements.txt](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/requirements.txt), [.gitignore](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/.gitignore), [scripts/ui_demo.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/scripts/ui_demo.py), [scripts/capture_ui_shots.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/scripts/capture_ui_shots.py), [tests/test_ui.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_ui.py), [tests/test_main_wiring.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_main_wiring.py), [tests/test_settings_and_presets.py](file:///D:/Computer%20Programming/Hackathons/Upcomming/Agentix%202.0/Anti-Distractor/tests/test_settings_and_presets.py).
- **Summary:**
  - Created `app/theme.py` with Linear/Vercel Zinc monochrome palettes + Indigo accent (`#6366F1` / `#4F46E5`), `ui_font()` (`Inter` -> `Segoe UI Variable` -> `Segoe UI`), `mono_font()` (`Cascadia Mono` -> `Consolas`), `detect_system_theme()`, and CustomTkinter primitives (`ModernButton`, `ModernLabel`, `SurfaceCard`, `StatusPill`, `ModernEntry`, `ModernProgressBar`, `bind_drag`, `fade_in_window`).
  - Added `apply_rounded_corners()` (`DWMWA_WINDOW_CORNER_PREFERENCE`) and immediate `SWP_FRAMECHANGED` repaint inside `apply_dark_titlebar()` in `app/win32_ui.py`.
  - Restyled all screens (Onboarding, Setup, Compact HUD, Mini-Pill, Layer 1, Layer 2, Strict Banner, Tier 0 Notice, Review, Rules Editor, Settings, History, Activity Drawer) with zero behavior regressions.
- **Deviations:**
  - **Single DPI Strategy:** `SetProcessDpiAwareness(1)` in `app/main.py` remains the sole DPI authority; `app/theme.py` calls `ctk.deactivate_automatic_dpi_awareness()` and pins CustomTkinter scaling to `1.0` to prevent double-scaling.
  - **CTk Root Isolation:** Because CustomTkinter patches its parent's `.configure()` method, screens on `self.root` host CTk containers inside a replaceable `tk.Frame` shell so `_clear_root_widgets()` never leaves stale bindings on `self.root`.
  - **Compact Strict Banner (not Full Screen):** Replaced the multi-monitor full-screen Strict overlay with a compact, always-on-top, non-focus-stealing (`WS_EX_NOACTIVATE`) floating banner anchored bottom-right of the active monitor with `Esc`/`F12` dismissal and slim animated countdown bar.
  - **HUD Visibility on Session Start:** Kept `self.root` visible in Compact HUD mode when a session starts (since `self.root` hosts the HUD widget tree itself), while retaining manual `—` Minimize-to-taskbar, `Tray`, Mini-Pill, and Review auto-restore from minimized states.
  - **Review & Listbox Scrollbars:** Retained `tk.Text` inside a `tk.Frame` container (with `ctk.CTkScrollbar`) so markdown rendering and `tests/test_ui.py` structural checks stay intact without gray Win32 scrollbars.
- **Test result:** `python -m pytest tests -q` — **72 passed, 0 failed**; visually reviewed 14 screenshots captured via `scripts/capture_ui_shots.py` into gitignored `ui_shots/`.
- **What's left:** None.
