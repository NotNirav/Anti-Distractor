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
