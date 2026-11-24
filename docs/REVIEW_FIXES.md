# Review fixes (Phases 0 to 2): trimmed list

Source: Hydrogen's review, with the overkill removed. Apply in order, one item at a time. After each item, run a quick smoke test (import the module, or run the related acceptance test) and add a one-line note to PROGRESS.md under "Deviations".

These patches are guidance. If variable names or structure in the real code differ, adapt the fix sensibly instead of pasting blindly. If you're unsure about something, add an "Escalation suggestion" instead of guessing.

---

## 1. app/config.py: load the project's own .env and override shared env vars
Problem: `load_dotenv()` doesn't override an existing `GEMINI_API_KEY` in the environment, so the app can silently use the CLI's key. It also misses `.env` if launched from another folder.
```python
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
```

## 2. app/judge.py and app/profile.py: reject an empty API key
Problem: `genai.Client(api_key="")` silently falls back to environment variables.
- At the start of `tier1_evaluate` and `tier2_evaluate`:
```python
if not api_key or not api_key.strip():
    raise ValueError("GEMINI_API_KEY is missing or empty.")
```
- In `generate_goal_profile`: `if not api_key or not api_key.strip(): return fallback_goal_profile(goal)`

## 3. app/judge.py: `generate_session_review` must never crash
Problem: a 429 or an empty response at session end raises instead of showing a local summary.
```python
pct = int((on_task_count / total) * 100) if total > 0 else 0
fallback_summary = f"Session summary for '{goal}' ({mode} mode): {pct}% on-task across {total} checks."
if not api_key or not api_key.strip():
    return fallback_summary
try:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(model=GEMINI_VISION_MODEL, contents=prompt)
    return (response.text or "").strip() or fallback_summary
except Exception:
    return fallback_summary
```

## 4. app/server.py: `CommandQueue` re-delivers in-flight commands (real bug)
Problem: `result.append(...)` sits outside the `if c["in_flight_since"] is None:` check, so every poll returns commands already in flight and the extension closes the same tab repeatedly. Move the append inside the `if`:
```python
for c in self._pending.values():
    if c["in_flight_since"] is None:
        c["in_flight_since"] = now
        result.append({
            "command_id": c["command_id"],
            "action": c["action"],
            "tab_ids": c["tab_ids"],
        })
```
Also in `enqueue_close_tabs`, dedupe the ids: `unique_ids = list(dict.fromkeys(int(t) for t in tab_ids))`.

## 5. extension/background.js: two small fixes
- Stop overlapping command polls:
```js
let isPollingCommands = false;
async function pollCommands() {
  if (isPollingCommands) return;
  isPollingCommands = true;
  try {
    // existing fetch + executeCommand loop
  } catch {
    // server unreachable: ignore
  } finally {
    isPollingCommands = false;
  }
}
```
- Don't fire snapshot events for background tabs. In `tabs.onUpdated`, only schedule a snapshot when `tab.active` is true and (`changeInfo.status === "complete"` or `changeInfo.title` or `changeInfo.url`).

## 6. app/judge.py: validate the model's JSON and never close tabs that don't exist
Problem: the model can return empty text, nulls, or invented tab IDs, and in strict mode that could close the wrong tab.
```python
def _parse_verdict_response(raw_text, ctx, tier_used):
    if not raw_text or not raw_text.strip():
        raise ValueError(f"Empty response from Tier {tier_used} judge.")
    parsed = _JudgeVerdictResponse.model_validate_json(raw_text)
    valid_ids = {t.tab_id for t in ctx.tab_snapshot.tabs} if ctx.tab_snapshot and ctx.tab_snapshot.tabs else set()
    return JudgeVerdict(
        activity=parsed.activity,
        on_task=parsed.on_task,
        confidence=float(parsed.confidence),
        reason=parsed.reason,
        tabs_to_close=[t for t in (parsed.tabs_to_close or []) if t in valid_ids],
        tier_used=tier_used,
        from_cache=False,
        rate_limited=False,
    )
```
Use it in both tier functions on `response.text`. Also make the rate-limit check exact, because the current one matches any text containing "429":
```python
def _is_rate_limit_error(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code == 429 or "RESOURCE_EXHAUSTED" in str(exc)
```

## 7. app/profile.py: `normalize_domain` must ignore non-web URLs
Problem: `file:///...` becomes "c" and `about:blank` becomes "about", which pollutes the cache and Tier 0 matching.
```python
def normalize_domain(url_or_domain: str) -> str:
    raw = (url_or_domain or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw and ":" in raw.split("/")[0] and not raw.split("/")[0].replace(".", "").replace(":", "").isdigit():
        return ""  # about:, data:, chrome:, etc.
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https"):
            return ""
        host = (parsed.hostname or "").removeprefix("www.")
        return host
    except Exception:
        return ""
```
Quick test: `file:///C:/x.pdf`, `about:blank` and `chrome://settings` return `""`; `https://www.youtube.com/watch?v=1` returns `youtube.com`; `leetcode.com/problems` returns `leetcode.com`.

## 8. app/server.py: stop hiding errors
- `allow_credentials=False` in the CORS middleware (the `"*"` origin plus credentials combination is invalid).
- Replace `except Exception: pass` in `health()` and `post_tabs()` with `logger.exception("...")`, so Phase 3 errors don't vanish. Add `import logging; logger = logging.getLogger(__name__)`.
- In `start_server_thread`, after starting the thread, wait up to 5 s for `server.started`. If the thread dies or the wait times out, raise a `RuntimeError` saying the port is probably in use.

---

## Skipped on purpose (not needed for the prototype)
- Reworking active-tab detection for multiple browser windows, and the `window_focused` tracking: the Python window watcher already knows what's in the foreground.
- Relabeling "[CURRENT ACTIVE TAB]" in the Tier 1/2 prompt text.
- Pydantic validation of the goal-profile response (the fallback profile already covers failures).
- Changing the desktop access flags in `screen.py`: leave it as long as screenshots work.

## Note for task T3.3 (event engine)
`capture_screenshot_bytes()` can fail on a locked screen or UAC prompt. `evaluate_context()` should catch that and fall back to a text-only judgment (Tier 1) or treat the moment as on-task. It must never crash the event engine thread.