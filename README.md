# Anti-Distractor 🎯

An intelligent, goal-aware focus agent for Windows PCs powered by Google Gemini.

Unlike traditional site-blockers that rely on crude host blacklists or constant polling loops, **Anti-Distractor** understands context and intent. It knows that watching a binary search tree tutorial on YouTube is on-task, while browsing YouTube Shorts or Instagram Reels during the same study session is a distraction.

---

## ⚡ Key Highlights

- **First-Launch API Key Onboarding (Zero Code Editing):** Built-in Welcome screen on first launch to paste, validate (**`Test Key`**), and save your Google Gemini API key locally to `.env` (or skip directly into Local Rules Only mode), paired with a Setup header **`Key ✓` / `Add Key`** badge and in-app **`Settings`** key rotation/removal.
- **Modern Linear / Vercel Desktop UI (`CustomTkinter`):** Monochrome Zinc dark/light design system with Indigo accent (`#6366F1` / `#4F46E5`), automatic Windows system theme detection, Win11 rounded corners (`DWMWA_WINDOW_CORNER_PREFERENCE`), immediate native Windows DWM dark titlebars (`DwmSetWindowAttribute`), subtle popup fade-ins, and DPI-compensated typography (`Inter` &rarr; `Segoe UI Variable` &rarr; `Segoe UI`, `Cascadia Mono` &rarr; `Consolas`) crisp across 100% and 125%+ scaling.
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
- **Native Windows Chrome & Multi-Monitor Awareness:** Branded target window icon across all windows, native Windows DWM dark titlebars and Win11 rounded corners, non-focus-stealing Layer 1/2 nudges and Strict banner (`WS_EX_NOACTIVATE`), multi-monitor HUD dragging across the full virtual desktop with remembered `(x, y)` coordinates, and active-monitor popup placement.
- **Compact Floating HUD, Mini-Pill Mode & System Tray (`pystray`):** Draggable HUD featuring hover tooltips, live `Grace: Xs` countdown, `Pinned`/`Unpinned` toggle, live `MM:SS` timer, `"AI calls: T1 x / T2 y"`, Chrome/Edge extension liveness dot (`Ext: Live` / `Ext: Off`), collapsible **Mini-Pill** mode (`● ON_TASK | 25:00 | Pause | Expand`), and one-click **`Tray`** minimization (`Show HUD`, `Pause / Resume`, `End Session`, `Quit`).
- **Intentional Pause / Snooze & Built-In Pomodoro Cycles:** Pause monitoring for configurable breaks (`5m / 10m / 15m` via `Pause` / `Resume`) without losing focus streaks, or enable **Pomodoro Mode** (e.g., `4 × 25m work / 5m break`) for automatic work &harr; break loops.
- **Smarter Editable Rules & 1-Click Presets:** Pre-session Setup (`Edit Rules` + **`AI Suggest Rules`**) and live mid-session (`Rules` with 1-click **`Current domain/app: + Allow / Block`** banner), automatic conflict deduplication across Allowed/Borderline/Blocked lists, `<Delete>`/`<BackSpace>` removal, and named **Saved Focus Presets** (*DSA Prep*, *Deep Coding*, *Writing & Docs*, plus custom presets).
- **Live Activity Feed ("Why Was This Flagged?"):** Collapsible **`Activity`** drawer showing the latest checks live (timestamp, domain/app, Tier 0/1/2 + model badge, confidence, AI reason) with 1-click **`Mark Relevant / Allow`**.
- **Strict Mode Tab Vault & Compact Lockout Banner:** Closes distracting tabs in Strict Mode while archiving their title + URL into the **`Closed Tabs Vault`** on Review (one-click **`Reopen`** / **`Reopen All`**), minimizes distracting desktop apps, and shows a compact, always-on-top, non-focus-stealing (`WS_EX_NOACTIVATE`) lockout banner anchored bottom-right of the active monitor with a live countdown bar and `Esc` / `F12` dismissal.
- **Chrome/Edge Extension 2.0 & Quick Rules Bridge:** Dark-mode-aware popup (`prefers-color-scheme: dark`) polling `/status` for live session timer, current tab rule classification (`Allowed` / `Borderline` / `Blocked` / `AI-Judged`), 1-click **`+ Allow Site`** / **`Block Site`** (`POST /rules/quick` + Layer 1 **`Always allow`**), and live toolbar badge (`ON` / `BRK` / `!`).
- **History & Focus Streaks + Settings & Export:** SQLite-backed **History & Focus Analytics** dashboard (daily streak, deep-work hours, top distractions, past review viewer, **`Repeat Goal`**), in-app **Settings** dialog (break length, mute chimes, dev/demo sensitivity, global hotkeys toggle, `.env` API key config & connection test), global Windows hotkeys (`Ctrl+Alt+P` / `Ctrl+Alt+H` / `Ctrl+Alt+R`), and Review **KPI cards** (`Focus Score`, `Focused Time`, `Distractions`, `Overrides`), animated loading bar, Markdown report, **`Copy Summary`**, **`Save .md`**, and **`New Session`**.
- **Fairness & Overrides:** Click *"This is relevant"* to cache an immediate override. Low-confidence judgements (< 0.60) are never punished.
- **Dark & Light Themes + Session Memory:** Instant `Dark` / `Light` theme toggle persisted alongside goal, duration, mode, and `(hud_x, hud_y)` in `last_session.json`. Single DPI strategy (`SetProcessDpiAwareness(1)` + DPI-compensated CTk pixel scaling) renders crisp text without clipping at 100% and 125%+ Windows scaling.

---

## 🏗️ Architecture

```text
 Chrome/Edge Extension (MV3)          Windows Desktop Watcher & Hotkeys
  - Tab activate/update/close          - win32gui & psutil (self-PID filtered)
  - Popup Dark/Light & Quick Rules     - RegisterHotKey (Ctrl+Alt+P / H / R)
  - Toolbar Badge (ON / BRK / !)       - DWM Dark Titlebars, Win11 Rounded Corners & WS_EX_NOACTIVATE
             │                                     │
             ▼                                     ▼
      FastAPI Receiver ◄─────────────────────► EventEngine
      (localhost:8765)                         - Debounce (1.5-3.0s) & Pause/Pomodoro
      - /tabs, /commands, /status, /rules/quick - Dwell, Heartbeat & Live Profile
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
      AgentStateMachine (Normal vs Strict, Live Grace Countdown)
             │
             ├── Dispatches UI Actions (Bounded Thread-Safe Queue)
             ▼
      CustomTkinter UI + Dialogs
      (Onboarding, Setup/Presets, Mini-Pill HUD, Compact Strict Banner, Activity Feed, History, Settings, Review KPIs & Tab Vault)
             │                                     │
             ▼                                     ▼
      SessionLogger (SQLite Analytics & Vault) SystemTrayManager (pystray)
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

### 3. Configure Your Gemini API Key (No Code Editing Required)
On first launch (`python -m app.main`), Anti-Distractor opens the **Welcome Onboarding** screen:
1. Click **Get a free key from Google AI Studio** (or paste an existing `GEMINI_API_KEY`).
2. Click **`Test Key`** to verify connectivity, then click **`Save & Continue`** to store it locally in `.env` (never shared or uploaded).
3. Prefer to try without an API key first? Click **`Skip — Use Local Rules Only`** (you can add or change your key anytime via the Setup **`Add Key` / `Key ✓`** badge or **`Settings`**).

<details>
<summary>Optional / Advanced: Manual <code>.env</code> configuration</summary>

Copy `.env.example` to `.env` in the project root to customize timing presets, offline demo mode, or model preferences:

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

</details>

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

1. **First-Launch Onboarding (First Run Only):**
   - Paste your Google Gemini API key, click **`Test Key`** &rarr; **`Save & Continue`**, or choose **`Skip — Use Local Rules Only`**.
2. **Setup Screen:**
   - Pick a **Saved Focus Preset** (*DSA Prep*, *Deep Coding*, *Writing & Docs*) or enter a custom goal and click **`+ Save`** to store a new preset.
   - Choose a quick duration (`15m`, `25m`, `45m`, `60m`) or custom minutes, and optionally enable **`Pomodoro`** for automatic work/break loops.
   - Select **Strict Mode** or **Normal Mode**.
   - Use header controls for **`History`** (streaks, hours, top distractions), **`Key ✓` / `Add Key`**, **`Settings`** (break length, mute sounds, dev/demo sensitivity, hotkeys, `.env` API key), **`Dark` / `Light`**, or **`Edit Rules`** (with **`AI Suggest Rules`**).
   - Click **`I'm Ready — Start Session`** (or press `Enter`).
3. **Monitoring HUD & Extension Badge:**
   - Draggable across single or multi-monitor setups (remembers position in `last_session.json`), with hover tooltips, `Grace: Xs` countdown, `MM:SS` timer, `AI calls: T1 x / T2 y`, and `Ext: Live` / `Ext: Off` while the browser toolbar displays **`ON`**, **`BRK`**, or **`!`**.
   - Use **`▾`** / **`Expand`** to switch to **Mini-Pill Mode**, **`Activity`** to inspect live AI decisions (`Mark Relevant / Allow`), **`Rules`** for instant allow/block edits, **`Pause`** for breaks, **`Tray`** to minimize to the Windows tray, or global shortcuts (`Ctrl+Alt+P` Pause, `Ctrl+Alt+H` Show/Hide HUD, `Ctrl+Alt+R` Rules).
4. **Ending Session & Review:**
   - Click **`End Session`** (or complete the timer/Pomodoro cycles) to view top-level **KPI cards**, an animated progress bar while AI summarizes, collapsible **Closed Tabs Vault** tab recovery (**`Reopen`** / **`Reopen All`**), **`Copy Summary`**, **`Save .md`**, **`New Session`**, and **`Done / Close`**.

---

## ⏱️ The 2-Minute Live Demo Script

Follow this rehearsed 6-step walkthrough for hackathons or live demonstrations:

| Step | Action | Expected Agent Behavior |
|:---:|---|---|
| **1** | Enter goal `"DSA"`, select **Strict Mode**, click **`I'm Ready — Start Session`**. | The Setup window transitions to the compact floating HUD with `ON_TASK` and `Ext: Live`. Baseline check passes silently. |
| **2** | Open a LeetCode problem or algorithm lecture on YouTube. | Agent passes silently at Tier 0 (or Tier 1) with zero interruptions. |
| **3** | Open Instagram in a new tab. | Tier 0 immediately detects `instagram.com` as blocked. HUD updates to `GRACE_PERIOD` (`Grace: Xs`). |
| **4** | Wait 5 seconds (grace expires). | Non-focus-stealing Layer 1 popup appears: *"Quick focus check — Are you studying DSA?"*. |
| **5** | Ignore the popup and continue browsing. | Layer 2 Strict engages: the Instagram tab is **automatically closed** (and archived into the **Closed Tabs Vault**), and the compact always-on-top **Strict lockout banner** appears bottom-right with a live countdown bar. |
| **6** | Wait for the lockout timer to expire (or press `Esc` / `F12`), return to LeetCode, and click **`End Session`**. | State resets to `ON_TASK`, and the scrollable AI Session Review screen appears showing KPI cards, closed-tab recovery, focus score, and model breakdown. |

> **Demo Extras:** During step 2 or 6 you can also showcase **`Rules`** (live domain/app editing), **`Activity`** (live Tier 0/1/2 explainability feed), **`Pause 5m`** (intentional break without losing your session timer), **`Tray`** (minimizing to the Windows tray), and **`Dark` / `Light`** theme toggling.

---

## ⚙️ Configuration & Timings

Timings can be switched in `.env` via `ANTI_DISTRACTOR_ENV` or inside the in-app **Settings** dialog:

| Parameter | Dev Mode (`dev`) | Demo Mode (`demo`) | Description |
|---|:---:|:---:|---|
| `debounce_seconds` | 1.5 s | 3.0 s | Coalesces rapid tab-switching bursts into a single check |
| `grace_period_seconds` | 5.0 s | 25.0 s | Time allowed to realize drift before Layer 1 popup appears |
| `strict_lockout_seconds` | 8.0 s | 15.0 s | Compact Strict lockout banner countdown duration during Strict Mode Layer 2 |
| `dwell_seconds` | 15.0 s | 75.0 s | Inactivity timer on borderline sites (e.g. YouTube) before vision recheck |
| `heartbeat_seconds` | 10.0 s | 300.0 s | Periodic background check to catch non-event content drift |

---

## 🧪 Test Suite & Visual Harness

Anti-Distractor includes a comprehensive unit and integration test suite with fakes for clocks, Win32 APIs, tray icons, browser queues, and GUI hooks:

```powershell
python -m pytest tests -v
```

All **72 tests across 10 test modules** pass with zero network or window dependencies:
- `tests/test_event_engine.py` (18 tests): Debounce, dwell, heartbeat, 5m pause/snooze suppression & expiry, tier escalation, background-browser guard, confidence gating, rate-limit backoff.
- `tests/test_state_machine.py` (14 tests): State transitions, user overrides, `allow_domain` quick action, `grace_remaining_seconds`, disagreement fast-track, tab closure validation, lockout expiry & post-lockout recheck.
- `tests/test_ui.py` (10 tests): DPI-aware setup, presets, Mini-Pill HUD, hover-color sync, multi-monitor virtual-screen drag clamping, tooltips, `Grace: Xs` countdown, smart Rules conflict deduplication & `<Delete>`/`AI Suggest`, non-focus-stealing Layer 1/2 popups, Activity Drawer, Review KPI cards + Tab Vault + `.md` export, History, and Settings dialogs.
- `tests/test_main_wiring.py` (8 tests): `AppController` orchestration, first-launch API key onboarding/skip/clear lifecycle, timer expiry, review fallback, `last_session.json` + theme + `(hud_x, hud_y)` persistence, Tab Vault capture/SQLite save, Pomodoro multi-cycle work/break loop, presets, quick rules, and Activity Feed overrides.
- `tests/test_model_preferences.py` (6 tests): Preference failover (`gemini-3.5-flash-lite` &rarr; `gemma-4-31b-it` &rarr; `gemma-4-26b-a4b-it`), 404/timeouts/network errors, markdown fence stripping, Tier 0 shift popup & HUD status, recovery probing.
- `tests/test_win32_and_hotkeys.py` (5 tests): Native DWM dark titlebar fallback (`20` &rarr; `19`), `WS_EX_NOACTIVATE` bitmask, multi-monitor `GetMonitorInfoW` work area, brand icon caching, and `GlobalHotkeyManager` (`Ctrl+Alt+P/H/R`).
- `tests/test_settings_and_presets.py` (4 tests): `SettingsStore` sanitization & round-trip, `.env` `GEMINI_API_KEY` update preserving comments, placeholder detection + key clearing + onboarding flag, and `PresetsStore` starter seeding/CRUD.
- `tests/test_demo_runbook.py` (3 tests): Full 6-step demo walkthrough, timing presets, and extension disconnect resilience.
- `tests/test_logger.py` (3 tests): SQLite session lifecycle, event logging, override updates, `model_used` + `closed_tabs_json` storage/migration, streak calculation, and top-distractions analytics.
- `tests/test_server_and_quick_rules.py` (1 test): `GET /status?domain=...` rule classification and `POST /rules/quick` live profile/cache mutation.

For interactive UI inspection without live API calls:
- `python scripts/ui_demo.py`: Interactive visual demo harness cycling through screens and states.
- `python scripts/capture_ui_shots.py`: Automated 14-screen screenshot capture into gitignored `ui_shots/` across Dark and Light themes.

---

## 🛠️ Troubleshooting & FAQ

- **Extension says *"Server Disconnected"* or HUD shows `Ext: Off`:** Ensure `python -m app.main` is running and a focus session has started. The local bridge listens on `http://127.0.0.1:8765`, and `Ext: Off` clears to `Ext: Live` as soon as the extension posts tabs or polls `/commands`.
- **Minimized HUD to System Tray:** Click the Anti-Distractor target icon in the Windows notification area (or right-click &rarr; **Show HUD**) to restore the floating HUD. You can also pause/resume, end the session, or quit directly from the tray menu.
- **Strict Lockout Banner Dismissal:** If you need to dismiss the compact Strict lockout banner immediately during work or a presentation, press **`Esc`**, **`F12`**, or click the **`Dismiss (Esc / F12)`** button. Closed tabs can be restored from the **Closed Tabs Vault** on the Review screen.
- **Offline / No API Key Mode:** Click **`Skip — Use Local Rules Only`** on first launch to run on customizable Tier 0 allow/block lists, or set `ANTI_DISTRACTOR_FAKE_JUDGE=1` in `.env` to test the full desktop UI, state machine, and browser tab closing flow using mock verdicts without consuming Gemini quota.
- **Elevated Windows / UAC:** If an admin command prompt or Task Manager is focused, `WindowWatcher` safely falls back without raising permission errors.
