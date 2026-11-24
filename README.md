# Anti-Distractor 🎯

An intelligent, goal-aware focus agent for Windows PCs powered by Google Gemini.

Unlike traditional site-blockers that rely on crude host blacklists or constant polling loops, **Anti-Distractor** understands context and intent. It knows that watching a binary search tree tutorial on YouTube is on-task, while browsing YouTube Shorts or Instagram Reels during the same study session is a distraction.

---

## ⚡ Key Highlights

- **Event-Driven Architecture:** Zero CPU-wasting screen polling loops. Only evaluates on active tab switches, title updates, window focus shifts, dwell timers, or periodic heartbeats.
- **3-Tier AI Judge (Fast & Economical):**
  - **Tier 0 (Local, 0ms, Free):** Instant local check against in-memory verdict cache, user overrides, and customizable goal domain/app allow/block lists.
  - **Tier 1 (Gemini 3.5 Flash Lite, ~200ms):** Fast text-only metadata evaluation over open tabs, window titles, and goal intent.
  - **Tier 2 (Multimodal Vision, ~700ms):** High-precision in-memory screen analysis for ambiguous content (e.g. YouTube videos, Reddit threads).
- **Multi-Model Fallback Hierarchy & Graceful Degradation:**
  - **Preference 1:** `gemini-3.5-flash-lite` (primary fast judge for text metadata and vision).
  - **Preference 2:** `gemma-4-31b-it` (automatic failover on rate limits, 404, timeouts, or network exceptions).
  - **Preference 3:** `gemma-4-26b-a4b-it` (tertiary fallback).
  - **Last Resort (Tier 0 Shift):** If all AI models are unavailable, shifts cleanly to local rule-based matching with a one-time user popup and persistent `"AI unavailable - rules only"` HUD status.
- **Privacy-First Design & Self-Monitoring Guard:** Desktop screenshots exist only in ephemeral RAM (`io.BytesIO`) during Tier 2 evaluation and are **never saved to disk**. Both `WindowWatcher` and Tier 0 ignore Anti-Distractor's own windows so interacting with controls never triggers self-penalties or self-minimization.
- **Compact Floating HUD & System Tray (`pystray`):** Draggable top-right HUD featuring a `-topmost` pin toggle (`Pinned`/`Unpinned`), live `MM:SS` countdown, `"AI calls: T1 x / T2 y"` telemetry, Chrome/Edge extension liveness dot (`Ext: Live` / `Ext: Off`), and one-click **`Tray`** minimization to the Windows system tray (`Show HUD`, `Pause / Resume`, `End Session`, `Quit`).
- **Intentional Pause / Snooze (`Pause 5m`):** Pause monitoring for a 5-minute break (`Pause 5m` / `Resume`) without ending the session or losing focus streaks — triggers are suppressed during breaks, the session deadline shifts forward by elapsed break time, and monitoring auto-resumes when the break expires.
- **Editable Domain & App Rules (Pre-Session & Live):** Inspect and edit Websites (`allowed_domains`, `borderline_domains`, `blocked_domains`) and Desktop Apps (`allowed_apps`, `blocked_apps`) before starting (`Edit Rules` on Setup) or mid-session (`Rules` on HUD), updating `EventEngine` and clearing `VerdictCache` immediately.
- **Multi-Monitor Strict Mode Overlay & Deterministic State Machine:**
  - **Normal Mode:** Gentle nudges &mdash; Grace Period &rarr; Layer 1 Ask (*"Are you studying `<goal>`?"*) &rarr; Layer 2 Warning (live auto-close countdown over 12s).
  - **Strict Mode:** Escalates past nudges &rarr; closes offending browser tabs via extension commands, minimizes off-task desktop apps, and locks across the entire virtual desktop spanning multiple monitors (`GetSystemMetrics(76/77/78/79)`) with the lockout card centered on the primary screen.
- **Fairness & Overrides:** Click *"This is relevant"* to cache an immediate override. Low-confidence judgements (< 0.60) are never punished.
- **Dark & Light Themes + Session Memory:** Instant `Dark` / `Light` theme toggle across Setup, HUD, Rules Editor, Popups, and Review, persisted alongside last goal, duration, and mode in `last_session.json`. DPI-aware dynamic window sizing (`SetProcessDpiAwareness(1)`) prevents clipping at 125%+ Windows scaling.
- **Post-Session AI Review:** Stores text-only logs in SQLite (with `model_used` attribution) and renders an end-of-session Markdown report with `Copy Summary`, `New Session`, and `Done / Close` actions.

---

## 🏗️ Architecture

```text
 Chrome/Edge Extension (MV3)          Windows Desktop Watcher
  - Tab activate/update/close          - win32gui & psutil (self-PID filtered)
  - GET /commands (close_tabs)         - Foreground window change
             │                                     │
             ▼                                     ▼
      FastAPI Receiver ◄─────────────────────► EventEngine
      (localhost:8765)                         - Debounce (1.5-3.0s) & Pause/Snooze
      - Ext: Live/Off heartbeat                - Dwell, Heartbeat & Live Profile
             │                                     │
             ▼                                     │
    VerdictCache (Tier 0) ◄────────────────────────┘
             │
             ├── Uncached / Borderline?
             ▼
      Tier 1 Text Judge (gemini-3.5-flash-lite -> gemma-4-31b-it -> gemma-4-26b-a4b-it)
             │
             ├── Low Confidence (< 0.75) / Force Vision?
             ▼
      Tier 2 Vision Judge (Multimodal Failover Chain)
             │
             ▼
      AgentStateMachine (Normal vs Strict)
             │
             ├── Dispatches UI Actions (Bounded Thread-Safe Queue)
             ▼
      Tkinter UI (Dark/Light, Setup, HUD, Rules Editor, Multi-Monitor Overlay, Review)
             │                                     │
             ▼                                     ▼
      SessionLogger (SQLite Database)       SystemTrayManager (pystray)
```

> Internal module contracts, design notes, and progress logs live in [docs/](docs/).

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- **Operating System:** Windows 10 or Windows 11
- **Python:** Python 3.10 to 3.14
- **Browser:** Google Chrome, Microsoft Edge, Brave, or Chromium

### 2. Install Dependencies
Clone the repository and install the required Python packages:

```powershell
git clone https://github.com/NotNirav/Anti-Distractor.git
cd Anti-Distractor

python -m venv venv
.\venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 3. Configure `.env`
Copy `.env.example` to `.env` in the project root and add your key:

```ini
# Google AI Studio API Key (Get free key from https://aistudio.google.com/)
GEMINI_API_KEY=your_gemini_api_key_here

# Runtime timing preset: 'dev' (fast 1.5-8s triggers for testing) or 'demo' (3-25s for live presentation)
ANTI_DISTRACTOR_ENV=dev

# Optional: Set to 1 for zero-quota offline rehearsals with canned verdicts
ANTI_DISTRACTOR_FAKE_JUDGE=0

# Optional: Override model preference hierarchy (Primary -> Tertiary)
# MODEL_PREFERENCE_1=gemini-3.5-flash-lite
# MODEL_PREFERENCE_2=gemma-4-31b-it
# MODEL_PREFERENCE_3=gemma-4-26b-a4b-it
```

---

## 🧩 3-Step Browser Extension Installation

To enable tab monitoring and automatic tab closure in Chrome or Edge:

1. Open your browser and navigate to the extension manager:
   - **Chrome:** `chrome://extensions`
   - **Edge:** `edge://extensions`
2. Turn on the **Developer mode** toggle in the top-right corner.
3. Click **Load unpacked** in the top-left corner and select the `extension/` folder inside the `Anti-Distractor` directory.

> **Verification:** Click the Anti-Distractor extension puzzle icon. The popup will display *"Server: Connected (Port 8765)"*, and once a session starts, the HUD footer indicator switches to **`Ext: Live`**.

---

## 💻 Running Anti-Distractor

Start the application from your terminal:

```powershell
python -m app.main
```

1. **Setup Screen:**
   - Enter your focus goal (e.g. *"Study Data Structures and Algorithms"*).
   - Pick a quick duration preset (`15m`, `25m`, `45m`, `60m`) or enter custom minutes.
   - Select **Strict Mode** or **Normal Mode**.
   - Optionally toggle **`Dark` / `Light`** theme or click **`Edit Rules`** to inspect and customize domain/app allow and block lists before starting.
   - Click **`I'm Ready — Start Session`** (or press `Enter`).
2. **Monitoring HUD:**
   - A compact, floating HUD docks in the top-right corner showing your current goal, mode badge (`[STRICT]` or `[NORMAL]`), state badge (`ON_TASK`, `GRACE_PERIOD`, `PAUSED`), live `MM:SS` countdown, `AI calls: T1 x / T2 y`, and `Ext: Live` / `Ext: Off`.
   - Use **`Rules`** to add/remove allowed or blocked sites/apps live, **`Pause 5m` / `Resume`** for intentional breaks, **`Pinned` / `Unpinned`** to toggle always-on-top behavior, or **`Tray`** to minimize the HUD into the Windows system tray.
3. **Ending Session & Review:**
   - Click **`End Session`** (or let the timer finish) to open the formatted Markdown **Focus Session Review** with **`Copy Summary`**, **`New Session`**, and **`Done / Close`** buttons.

---

## ⏱️ The 2-Minute Live Demo Script

Follow this rehearsed 6-step walkthrough for hackathons or live demonstrations:

| Step | Action | Expected Agent Behavior |
|:---:|---|---|
| **1** | Enter goal `"DSA"`, select **Strict Mode**, click **`I'm Ready — Start Session`**. | The Setup window transitions to the compact floating HUD with `ON_TASK` and `Ext: Live`. Baseline check passes silently. |
| **2** | Open a LeetCode problem or algorithm lecture on YouTube. | Agent passes silently at Tier 0 (or Tier 1) with zero interruptions. |
| **3** | Open Instagram in a new tab. | Tier 0 immediately detects `instagram.com` as blocked. HUD updates to `GRACE_PERIOD`. |
| **4** | Wait 5 seconds (grace expires). | Layer 1 popup appears: *"Quick Focus Check — Are you studying DSA?"*. |
| **5** | Ignore the popup and continue browsing. | Layer 2 Strict engages: the Instagram tab is **automatically closed**, and the multi-monitor dark overlay appears stating *"Start DSA again"*. |
| **6** | Wait for the lockout timer to expire (or press `Esc` / `F12`), return to LeetCode, and click **`End Session`**. | State resets to `ON_TASK`, and the scrollable AI Session Review screen appears showing your focus score and model breakdown. |

> **Demo Extras:** During step 2 or 6 you can also showcase **`Rules`** (live domain/app editing), **`Pause 5m`** (intentional break without losing your session timer), **`Tray`** (minimizing to the Windows tray), and **`Dark`** theme toggling.

---

## ⚙️ Configuration & Timings

Timings can be switched in `.env` via `ANTI_DISTRACTOR_ENV`:

| Parameter | Dev Mode (`dev`) | Demo Mode (`demo`) | Description |
|---|:---:|:---:|---|
| `debounce_seconds` | 1.5 s | 3.0 s | Coalesces rapid tab-switching bursts into a single check |
| `grace_period_seconds` | 5.0 s | 25.0 s | Time allowed to realize drift before Layer 1 popup appears |
| `strict_lockout_seconds` | 8.0 s | 15.0 s | Full-screen lockout duration during Strict Mode Layer 2 |
| `dwell_seconds` | 15.0 s | 75.0 s | Inactivity timer on borderline sites (e.g. YouTube) before vision recheck |
| `heartbeat_seconds` | 10.0 s | 300.0 s | Periodic background check to catch non-event content drift |

---

## 🧪 Test Suite

Anti-Distractor includes a comprehensive unit and integration test suite with fakes for clocks, Win32 APIs, tray icons, browser queues, and GUI hooks:

```powershell
python -m pytest tests -v
```

All 56 tests pass with zero network or window dependencies:
- `tests/test_event_engine.py` (18 tests): Debounce, dwell, heartbeat, 5m pause/snooze suppression & expiry, tier escalation, background-browser guard, confidence gating, rate-limit backoff.
- `tests/test_state_machine.py` (13 tests): State transitions, user overrides, disagreement fast-track, tab closure validation, lockout expiry & post-lockout recheck.
- `tests/test_ui.py` (8 tests): DPI-aware setup & inline validation, HUD pin/unmaximize/timer, extension dot, pause/resume UI, Dark/Light theme toggle, editable rules dialog, system tray minimize/restore, multi-monitor strict overlay, Layer 2 auto-close countdown, and Markdown review.
- `tests/test_main_wiring.py` (6 tests): `AppController` cross-thread orchestration, timer expiry, review fallback, `last_session.json` + theme persistence, self-window Tier 0 ignore, `restart_to_setup`, extension connectivity tick, pause/resume deadline extension, and pre-session & live rules editing.
- `tests/test_model_preferences.py` (6 tests): Preference failover (`gemini-3.5-flash-lite` &rarr; `gemma-4-31b-it` &rarr; `gemma-4-26b-a4b-it`), 404/timeouts/network errors, markdown fence stripping, Tier 0 shift popup & HUD status, recovery probing.
- `tests/test_demo_runbook.py` (3 tests): Full 6-step demo walkthrough, timing presets, and extension disconnect resilience.
- `tests/test_logger.py` (2 tests): SQLite session lifecycle, event logging, override updates, `model_used` column storage, and legacy DB `ALTER TABLE` migration.

---

## 🛠️ Troubleshooting & FAQ

- **Extension says *"Server Disconnected"* or HUD shows `Ext: Off`:** Ensure `python -m app.main` is running and a focus session has started. The local bridge listens on `http://127.0.0.1:8765`, and `Ext: Off` clears to `Ext: Live` as soon as the extension posts tabs or polls `/commands`.
- **Minimized HUD to System Tray:** Click the Anti-Distractor target icon in the Windows notification area (or right-click &rarr; **Show HUD**) to restore the floating HUD. You can also pause/resume, end the session, or quit directly from the tray menu.
- **Strict Overlay Emergency Exit:** If you need to exit the multi-monitor lockout overlay immediately during a presentation, press **`Esc`**, **`F12`**, or click the red **`Emergency Exit (Demo)`** button.
- **Offline / No API Key Mode:** Set `ANTI_DISTRACTOR_FAKE_JUDGE=1` in `.env` to run the complete desktop UI, state machine, and browser tab closing logic using pre-recorded mock verdicts without consuming Gemini quota.
- **Elevated Windows / UAC:** If an admin command prompt or Task Manager is focused, `WindowWatcher` safely falls back without raising permission errors.
