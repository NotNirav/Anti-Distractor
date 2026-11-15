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
- [ ] **T4.1** (Hydrogen) — Deterministic Agent State Machine & Strict Mode Enforcement (`todo`)
- [ ] **T5.1** (Flash) — SQLite Session & Activity Logger (`todo`)
- [ ] **T5.2** (Flash) — Tkinter Setup Window, Monitoring HUD, and End-of-Session Review Screen (`todo`)
- [ ] **T5.3** (Hydrogen) — Layer 1/2 Popups, Strict Full-Screen Overlay, and Main App Threading Orchestration (`todo`)
- [ ] **T6.1** (Flash) — Dev/Demo Timing Verification, Error Hardening, and Demo Runbook (`todo`)

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
  - Implemented `evaluate_context` in `app/judge.py` with Tier 0 -> Tier 1 -> Tier 2 (`TIER1_ESCALATION_CONFIDENCE`) escalation, `force_vision` cache bypass, `PUNISH_CONFIDENCE_THRESHOLD` fairness gating, automatic `VerdictCache` writes, screenshot-failure fallback (to Tier 1 / safe on-task), and `GeminiRateLimitError` fallback to Tier 0 / safe on-task.
  - Guarded `tier0_evaluate` domain matching on `foreground_window is None or foreground_window.is_browser` so background browser tabs don't trigger blocks when the user is in a non-browser app.
  - Implemented `EventEngine` in `app/event_engine.py` with a single `threading.Condition` worker loop handling `baseline`, debounced `submit_trigger`, borderline `dwell` timer, periodic `heartbeat`, `run_immediate_check`, rate-limit backoff, exception isolation, and clean `start()`/`stop()`.
  - Added 14 mocked unit/integration tests in `tests/test_event_engine.py` (all 14 passed in 1.05s) plus 1 live Tier 1 Gemini smoke test (`Red-Black Tree Insertion - GeeksforGeeks` -> `tier_used=1, on_task=True, confidence=0.95`).
- **Next up**: **T4.1** (Hydrogen) — Deterministic Agent State Machine & Strict Mode Enforcement (`app/state_machine.py`).

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

## Escalation suggestions
