# PROGRESS.md

## Task Checklist

- [x] **T0.1** (Flash) — Environment, Config, Data Models, and Gemini Smoke Check (`done`)
- [x] **T1.1** (Flash) — In-Memory Screenshot Capture & Downscaling (`done`)
- [x] **T1.2** (Flash) — Goal Profile Generation & Normalization Helpers (`done`)
- [x] **T1.3** (Flash) — Tier 1 Text Judge, Tier 2 Vision Judge, and Session Review AI Calls (`done`)
- [ ] **T2.1** (Flash) — FastAPI Local Receiver and Command Queue (`todo`)
- [ ] **T2.2** (Flash) — Chrome/Edge Manifest V3 Extension (`todo`)
- [ ] **T3.1** (Flash) — Windows Foreground Window Watcher & Minimize Helper (`todo`)
- [ ] **T3.2** (Flash) — Verdict Cache & Tier 0 Rule Evaluator (`todo`)
- [ ] **T3.3** (Hydrogen) — Event Engine, Tier Escalation, Dwell/Heartbeat, and Rate-Limit Fallback (`todo`)
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
- **Next up**: **T2.1** (Flash) — FastAPI Local Receiver and Command Queue (`app/server.py`).

## Deviations
- Updated `GEMINI_TEXT_MODEL` and `GEMINI_VISION_MODEL` in `app/config.py` to `gemini-3.5-flash-lite` and `gemini-3.5-flash` because the legacy 2.5 models were retired for new keys in AI Studio (HTTP 404).

## Escalation suggestions
