# Anti-Distractor: Project Plan (some content has been removed for efficiency - work with the below content only)

A goal-aware focus agent for Windows PCs. Built as a hackathon prototype (round 1). It will not be continued afterwards, so we optimize for a working, impressive demo, not for perfection.

---

## 1. What we are building

You tell the app what you want to do ("Study DSA for 2 hours"). It then watches what you do on your PC and keeps you on that goal.

Existing screen-time apps only count time per app or website, and they judge by category ("YouTube = distracting"). They don't know why you are on YouTube. Ours judges every moment against your stated goal:

- A YouTube lecture on binary trees: fine.
- YouTube Shorts in the same tab: not fine.

When you drift, the agent acts. It asks, then warns, and in strict mode it closes the tab and blocks the screen. At the end it writes a review of your session.

**Why it counts as agentic:** it observes (tabs, windows, screen), decides (is this on-task?), acts (ask, warn, close, block), and adapts (verdict cache, override feedback, self-written review). It doesn't just report numbers.

## 3. Key design decision: event-driven, not constant polling

We do not take a screenshot every minute. That would be expensive, hit API limits, and waste effort when nothing changed. Instead the app reacts to events and escalates to AI only when needed.

**Triggers (events):**
1. Tab events from a Chrome/Edge extension: tab switched, opened, closed, or its title/URL changed.
2. Foreground window changed (app switch).
3. Heartbeat: a mandatory check every 5 minutes (10 s in dev mode).
4. Dwell: if we sit on a "borderline" site (YouTube, Reddit, AI chat) for 60 to 90 s with no events, run a vision check. This catches silent feed-scrolling.

We do **not** hook keystrokes or mouse clicks. It is noisy, it feels like a keylogger, and tab and window events already cover what we need. We also ignore background services and process lists. We only use visible things: open tabs, the foreground window, and open app windows.

After any trigger, wait 3 seconds (debounce) so rapid tab flipping doesn't spam checks.

## 4. The three-tier judge

Each trigger goes through the cheapest tier that can decide.

| Tier | What it uses | Cost | Used when |
|------|--------------|------|-----------|
| 0 | Allow/block lists from the goal profile + verdict cache | Free, instant | Domain or title already known (leetcode.com = on-task, instagram.com = off-task) |
| 1 | Text-only AI call: tab titles, URLs, window title, open apps, goal | Cheap | Unknown title or domain |
| 2 | Vision AI call: screenshot + the same text | Most expensive | Tier 1 is unsure (low confidence), dwell trigger, or heartbeat |

**One model does it all.** We use Gemini from Google AI Studio: a lighter Flash-class model for Tier 1 text and a regular Flash model for Tier 2 vision (check AI Studio for current model names). The same model is both the vision model and the judge, so there is no separate judge.

**Goal profile (one AI call at session start).** The user types a goal like "DSA". Gemini expands it into a cached profile: on-task topics, allowed sites, borderline cases, and clearly off-task items. This is what makes the agent understand the goal and also feeds Tier 0.

**Judge output (strict JSON):**
```json
{
  "activity": "watching a DSA lecture on YouTube",
  "on_task": true,
  "confidence": 0.86,
  "reason": "Video title is about BST insertion",
  "tabs_to_close": []
}
```
`tabs_to_close` lists off-task tab IDs, used in strict mode.

**Rules to keep it fair:**
- If confidence is below about 0.6, treat as on-task. We never punish uncertainty.
- Cache every verdict by (app/domain + normalized title) so repeat visits cost nothing.
- Rate limits: the design only needs roughly 20 to 40 AI calls per 2-hour session. Check the current free-tier limits in AI Studio. If the API returns a rate-limit error, fall back to Tier 0 rules and do not punish the user.

## 5. Agent behavior (action logic, plain Python, not AI)

The AI only judges. A deterministic state machine decides what happens, so the demo is predictable.

- On-task: reset the strike counter.
- Off-task: wait a 20 to 30 s grace period (so a quick search isn't punished), then:
  - **Layer 1, ask:** popup "Are you studying DSA? Go back." Buttons: *Back to work* / *This is relevant*. Clicking "relevant" is logged as an override and the verdict cache is updated.
  - **Layer 2, ultimatum**, if still off-task at the next check or if the model disagrees with the override:
    - **Normal mode:** loud warning with the model's reason ("This is a gaming video, not DSA").
    - **Strict mode:** the extension closes the specific bad tab (or the app window is minimized), and a full-screen blank overlay says "Start DSA again." It stays until the next check passes or a short lockout ends.
- Strict mode never force-kills a process with unsaved work. Minimize apps; close browser tabs only.

## 6. Architecture

```
Chrome/Edge extension ──(tab events, localhost)──►  Python app  ◄── window focus watcher (pywin32)
        ▲                                              │
        └───────(close-tab commands)───────────────────┤
                                                       ├─ Event engine (debounce, dwell, heartbeat)
                                                       ├─ Tier 0: rules + cache
                                                       ├─ Tier 1: Gemini text call
                                                       ├─ Tier 2: Gemini vision call (mss screenshot)
                                                       ├─ Agent state machine (Layer 1/2, modes)
                                                       ├─ UI (Tkinter): setup, popups, overlay, review
                                                       └─ SQLite log (text only)
```

**Tech stack (Windows only, Python 3.11+):**
- Python app: `mss` (screenshots), `pywin32` and `psutil` (foreground window, app windows), FastAPI + uvicorn (local server for the extension), `google-genai` (Gemini), Tkinter (UI), `sqlite3`.
- Extension: Chrome/Edge Manifest V3, loaded unpacked (fine for a prototype). Uses `chrome.tabs.query`, `tabs.onActivated`, `tabs.onUpdated`, `tabs.onCreated/onRemoved`, and `chrome.tabs.remove` for closing tabs.
- Extension to app: POST tab snapshots to `http://127.0.0.1:<port>/tabs`; the extension polls `/commands` every couple of seconds for close-tab commands (or uses a WebSocket).
- API key stored in a local `.env` file, never committed.

**What gets sent to the AI:**
- Background tabs: text only (title and URL).
- The tab or app you are actually looking at: screenshot + title + URL, but only at Tier 2.
- Non-browser apps: window title + process name (+ screenshot at Tier 2).

## 7. User flow

1. Open the app. A small setup window hovers in the background while we set things up.
2. Type the goal, set the duration, pick Normal or Strict mode.
3. The app builds the goal profile with Gemini.
4. Click **"I'm ready"**. Monitoring starts. A baseline vision check runs first to confirm the user really is on-task.
5. Events drive checks. The agent acts per section 5.
6. Session ends. The app sends the text log to Gemini and shows a short review ("78% on-task, drifted twice to YouTube, longest focus streak 34 min").

## 8. Privacy (prototype level)

- Screenshots live in memory only and are never saved to disk. We store text results only.
- We are **skipping** the sensitive-app skip list (banking, password managers, etc.) for now.

## 9. Data we log (SQLite, text only)

`timestamp, trigger_type, app/domain, title, tier_used, on_task, confidence, reason, action_taken, user_override , (etc - anything that seems reasonable)`

## 10. Build phases

**Phase 0: Setup**
- Repo, virtual environment, `.env` with Gemini key, check AI Studio rate limits.
- Done when: a Gemini test call works from Python.

**Phase 1: Judge core**
- Screenshot (`mss`, downscaled to about 1024 px wide) + title + goal sent to Gemini, returning the JSON in section 4.
- Goal-profile generation call.
- Done when: running the script on different screens (lecture, Shorts, IDE, game) gives sensible verdicts.

**Phase 2: Browser sensing**
- Chrome/Edge extension that reports all tabs and fires events.
- FastAPI receiver in the Python app holding the latest tab snapshot.
- Done when: switching tabs, or changing videos inside one YouTube tab, shows up in the app log.

**Phase 3: Event engine and tiers**
- Foreground window watcher, 3 s debounce, heartbeat, dwell timer.
- Tier 0 rules + verdict cache, Tier 1 text call, Tier 2 vision call, escalation by confidence.
- Rate-limit fallback.
- Done when: obvious sites are decided without any AI call and ambiguous ones escalate correctly.

**Phase 4: Agent behavior**
- State machine with grace period and strikes, Layer 1 popup, Layer 2 warning.
- Strict mode: close tab via extension, minimize apps, full-screen overlay.
- Override logging.
- Done when: the full ask, warn, block sequence works in both modes.

**Phase 5: UI, logging, review**
- Setup window with the "I'm ready" flow, SQLite logging, end-of-session review screen.
- Done when: a complete session runs start to finish with a review at the end.

**Phase 6: Demo polish**
- Dev vs demo timing config, error handling, a rehearsed run-through.

## 11. Demo script (about 2 minutes)

1. Set goal "DSA" in Strict mode, click "I'm ready".
2. Open a LeetCode problem or a DSA lecture. The agent passes it silently.
3. Open Instagram. Tier 0 blocks it instantly.
4. In the same YouTube tab, switch from the lecture to Shorts. The agent asks (Layer 1), then warns.
5. Ignore it. Strict mode closes the tab and shows "Start DSA again."
6. Go back to work, end the session, show the AI-written review.

Talking point: the agent understands intent (same site, same tab, different verdict), and it only calls AI when it needs to.

## 12. Risks and mitigations

| Risk | Plan |
|------|------|
| API rate limits | Tiered judging, verdict cache, fallback to rules |
| False positives (punishing real study) | Low-confidence = on-task, grace period, "this is relevant" override |
| Silent drift with no events | Dwell timer and heartbeat |
| Extension setup friction | Load unpacked; document the 3-step install |
| Closing tabs with unsaved work | Only close browser tabs; minimize apps instead of killing them |

## 13. Out of scope (later, if ever)

Sensitive-app skip list, Firefox or Mac/Linux support, voice output, local-model fallback, multi-monitor handling, packaging as an installer.
