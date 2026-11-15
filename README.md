# Anti-Distractor 🎯

An intelligent, goal-aware focus agent for Windows PCs powered by Google Gemini.

Unlike traditional site-blockers that rely on crude host blacklists or constant polling loops, **Anti-Distractor** understands context and intent. It knows that watching a binary search tree tutorial on YouTube is on-task, while browsing YouTube Shorts or Instagram Reels during the same study session is a distraction.

---

## ⚡ Key Highlights

- **Event-Driven Architecture:** Zero CPU-wasting screen polling loops. Only evaluates on active tab switches, title updates, window focus shifts, dwell timers, or periodic heartbeats.
- **3-Tier AI Judge (Fast & Economical):**
  - **Tier 0 (Local, 0ms, Free):** Instant local check against in-memory verdict cache, user overrides, and goal domain/app allow/block lists.
  - **Tier 1 (Gemini 3.5 Flash Lite, ~200ms):** Fast text-only metadata evaluation over open tabs, window titles, and goal intent.
  - **Tier 2 (Gemini 3.5 Flash Multimodal, ~700ms):** High-precision in-memory screen analysis for ambiguous content (e.g. YouTube videos, Reddit threads).
- **Multi-Model Fallback Hierarchy & Graceful Degradation:**
  - **Preference 1:** `gemini-3.5-flash-lite` (primary fast judge for text metadata and vision).
  - **Preference 2:** `gemma-4-31b-it` (automatic failover on rate limits, 404, timeouts, or network exceptions).
  - **Preference 3:** `gemma-4-26b-a4b-it` (tertiary fallback).
  - **Last Resort (Tier 0 Shift):** If all AI models are unavailable, shifts cleanly to local rule-based matching with a one-time user alert and persistent `"AI unavailable - rules only"` HUD status.
- **Privacy-First Design:** Desktop screenshots exist only in ephemeral RAM (`io.BytesIO`) during Tier 2 evaluation and are **never saved to disk**.
- **Compact & Draggable Monitoring HUD:** Floating HUD showing current goal, mode badge, color-coded state, live activity summary, and live `"AI calls: T1 x / T2 y"` telemetry.
- **Deterministic State Machine:**
  - **Normal Mode:** Gentle nudges &mdash; initial Grace Period &rarr; Layer 1 Ask (*"Are you studying `<goal>`?"*) &rarr; Layer 2 Warning (auto-closes after 12s).
  - **Strict Mode:** Nudges fail &rarr; closes offending browser tabs via extension commands, minimizes off-task desktop apps, and engages a full-screen lockout overlay.
- **Fairness & Overrides:** User can click *"This is relevant"* to cache an immediate override. Low-confidence judgements (< 0.60) are never punished.
- **Session Memory:** Remembers last goal, duration, and mode in local `last_session.json` to prefill the setup window.
- **Post-Session AI Review:** Stores text-only logs in SQLite (with `model_used` attribution) and generates an end-of-session markdown report highlighting focus scores, distraction logs, and per-model check counts.

---

## 🏗️ Architecture

```text
 Chrome/Edge Extension (MV3)          Windows Desktop Watcher
  - Tab activate/update/close          - win32gui & psutil
  - GET /commands (close_tabs)         - Foreground window change
             │                                     │
             ▼                                     ▼
      FastAPI Receiver ◄─────────────────────► EventEngine
      (localhost:8765)                         - Debounce (1.5-3.0s)
             │                                 - Dwell & Heartbeat
             ▼                                     │
    VerdictCache (Tier 0) ◄────────────────────────┘
             │
             ├── Uncached / Borderline?
             ▼
      Tier 1 Text Judge (Gemini 3.5 Flash Lite)
             │
             ├── Low Confidence (< 0.75) / Force Vision?
             ▼
      Tier 2 Vision Judge (Gemini 3.5 Flash Multimodal)
             │
             ▼
      AgentStateMachine (Normal vs Strict)
             │
             ├── Dispatches UI Actions (Thread-safe Queue)
             ▼
      Tkinter UI (Setup, HUD, Popups, Strict Overlay, Review)
             │
             ▼
      SessionLogger (SQLite Database)
```

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
Create a `.env` file in the project root:

```ini
# Google AI Studio API Key (Get free key from https://aistudio.google.com/)
GEMINI_API_KEY=your_gemini_api_key_here

# Runtime timing preset: 'dev' (fast 1.5-8s triggers for testing) or 'demo' (3-25s for live presentation)
ANTI_DISTRACTOR_ENV=dev

# Optional: Set to 1 for zero-quota offline rehearsals with canned verdicts
ANTI_DISTRACTOR_FAKE_JUDGE=0
```

---

## 🧩 3-Step Browser Extension Installation

To enable tab monitoring and automatic tab closure in Chrome or Edge:

1. Open your browser and navigate to the extension manager:
   - **Chrome:** `chrome://extensions`
   - **Edge:** `edge://extensions`
2. Turn on the **Developer mode** toggle in the top-right corner.
3. Click **Load unpacked** in the top-left corner and select the `extension/` folder inside the `Anti-Distractor` directory.

> **Verification:** Click the Anti-Distractor extension puzzle icon. The popup will display *"Server: Connected (Port 8765)"* once the desktop app is running.

---

## 💻 Running Anti-Distractor

Start the application from your terminal:

```powershell
python -m app.main
```

1. **Setup Screen:**
   - Enter your focus goal (e.g. *"Study Data Structures and Algorithms"*).
   - Enter your session duration in minutes (e.g. `25`).
   - Select **Strict Mode** or **Normal Mode**.
   - Click **🚀 I'm Ready — Start Session**.
2. **Monitoring HUD:**
   - A compact, floating HUD appears showing your current goal, mode badge (`[STRICT]` or `[NORMAL]`), color-coded state badge (`🟢 ON_TASK`), and live activity summary.
3. **Ending Session:**
   - Click **⏹ End Session** on the HUD at any time to generate your AI Session Review.

---

## ⏱️ The 2-Minute Live Demo Script

Follow this rehearsed 6-step walkthrough for hackathons or live demonstrations:

| Step | Action | Expected Agent Behavior |
|:---:|---|---|
| **1** | Enter goal `"DSA"`, select **Strict Mode**, click *"I'm ready"*. | The Setup window transitions to the compact floating HUD with `🟢 ON_TASK`. Baseline check passes silently. |
| **2** | Open a LeetCode problem or algorithm lecture on YouTube. | Agent passes silently at Tier 0 (or Tier 1) with zero interruptions. |
| **3** | Open Instagram in a new tab. | Tier 0 immediately detects `instagram.com` as blocked. HUD updates to `🟠 GRACE_PERIOD`. |
| **4** | Wait 5 seconds (grace expires). | Layer 1 popup appears: *"Quick Focus Check: Are you studying DSA?"*. |
| **5** | Ignore the popup and continue browsing. | Layer 2 Strict engages: the Instagram tab is **automatically closed**, and a full-screen dark overlay appears stating *"Start DSA again"*. |
| **6** | Wait for the lockout timer to expire (or press `Esc` / `F12`), return to LeetCode, and click *"End Session"*. | State resets to `🟢 ON_TASK`, and the scrollable AI Session Review screen appears showing your focus score and breakdown. |

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

Anti-Distractor includes a comprehensive unit and integration test suite with fakes for clocks, Win32 APIs, browser queues, and GUI hooks:

```powershell
python -m pytest tests -v
```

All 47 tests pass with zero network or window dependencies:
- `tests/test_event_engine.py` (16 tests): Debounce, dwell, heartbeat, tier escalation, confidence gating, rate-limit backoff.
- `tests/test_state_machine.py` (13 tests): State transitions, user overrides, disagreement fast-track, tab closure validation, lockout expiry.
- `tests/test_model_preferences.py` (6 tests): Preference failover (Gemini &rarr; Gemma 31B &rarr; Gemma 4 26B A4B), 404/timeouts/network errors, markdown fence stripping, Tier 0 shift popup & HUD status.
- `tests/test_logger.py` (2 tests): SQLite session lifecycle, event logging, override updates, `model_used` column storage, and legacy DB ALTER TABLE migration.
- `tests/test_ui.py` (5 tests): Tkinter thread-safe event queue pump, setup, HUD draggability, AI call counter, auto-close timers, and review screens.
- `tests/test_main_wiring.py` (2 tests): AppController cross-thread orchestration, lifecycle shutdown, and last session persistence.
- `tests/test_demo_runbook.py` (3 tests): Full 6-step demo walkthrough, timing presets, and extension disconnect resilience.

---

## 🛠️ Troubleshooting & FAQ

- **Extension says *"Server Disconnected"*:* Ensure `python -m app.main` is running. The local bridge listens on `http://127.0.0.1:8765`.
- **Strict Overlay Emergency Exit:** If you need to exit the full-screen lockout overlay immediately during a presentation, press **`Esc`**, **`F12`**, or click the red **`Emergency Exit (Demo)`** button in the lower-right corner.
- **Offline / No API Key Mode:** Set `ANTI_DISTRACTOR_FAKE_JUDGE=1` in `.env` to run the complete desktop UI, state machine, and browser tab closing logic using pre-recorded mock verdicts without consuming Gemini quota.
- **Elevated Windows / UAC:** If an admin command prompt or Task Manager is focused, `WindowWatcher` safely falls back without raising permission errors.
