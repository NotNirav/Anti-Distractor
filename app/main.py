"""Main entry point orchestrating server, watchers, event engine, state machine, logger, settings, hotkeys, and UI."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, replace
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Literal

from google import genai

from app.cache import VerdictCache
from app.config import MODEL_PREFERENCE_1, TimingConfig, get_gemini_api_key, get_timings
from app.event_engine import EventEngine
from app.hotkeys import GlobalHotkeyManager
from app.judge import domain_matches, generate_session_review, get_model_manager
from app.logger import SessionLogger
from app.models import (
    ActivityContext,
    ActivityFeedItem,
    AgentDecision,
    ClosedTabEntry,
    GoalProfile,
    JudgeVerdict,
    ModeType,
    SessionKPIs,
)
from app.profile import fallback_goal_profile, generate_goal_profile, normalize_domain
from app.server import CommandQueue, TabSnapshotStore, create_app, start_server_thread
from app.settings import (
    AppSettings,
    FocusPreset,
    PresetsStore,
    SettingsStore,
    clear_api_key_in_dotenv,
    is_placeholder_or_empty_api_key,
    mask_api_key,
    read_masked_api_key,
    resolve_dotenv_path,
    write_api_key_to_dotenv,
)
from app.state_machine import AgentStateMachine
from app.ui import TkinterUI, UIHooks
from app.window_watcher import WindowWatcher

logger = logging.getLogger(__name__)


def _is_fake_judge_enabled() -> bool:
    return os.getenv("ANTI_DISTRACTOR_FAKE_JUDGE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def make_fake_judge(
    verdicts: list[JudgeVerdict] | None = None,
) -> Callable[..., JudgeVerdict]:
    """Return a zero-quota fake evaluate_fn cycling through canned verdicts."""
    canned = verdicts or [
        JudgeVerdict(
            activity="Working on goal",
            on_task=True,
            confidence=0.95,
            reason="Fake judge: on-task baseline.",
            tabs_to_close=[],
            tier_used=1,
            model_used="fake-judge",
        ),
        JudgeVerdict(
            activity="Browsing social media",
            on_task=False,
            confidence=0.95,
            reason="Fake judge: off-task distraction.",
            tabs_to_close=[],
            tier_used=1,
            model_used="fake-judge",
        ),
    ]
    idx = 0
    lock = threading.Lock()

    def _fake_evaluate(
        ctx: ActivityContext,
        profile: GoalProfile,
        cache: VerdictCache,
        api_key: str,
        force_vision: bool = False,
    ) -> JudgeVerdict:
        nonlocal idx
        with lock:
            verdict = canned[idx % len(canned)]
            idx += 1
        return verdict

    return _fake_evaluate


class AppController:
    """Coordinates lifecycle, threading, settings, presets, Pomodoro, and callbacks across all modules."""

    def __init__(
        self,
        ui: UIHooks,
        timings: TimingConfig,
        api_key: str,
        *,
        db_path: str = "anti_distractor.db",
        env_path: str | None = None,
        host: str = "127.0.0.1",
        port: int = 8765,
        fake_judge: bool = False,
        enable_os_hotkeys: bool | None = None,
        session_logger_cls: type[SessionLogger] = SessionLogger,
        engine_cls: type[EventEngine] = EventEngine,
        watcher_cls: type[WindowWatcher] = WindowWatcher,
        server_starter: Callable[..., Any] = start_server_thread,
        app_factory: Callable[..., Any] = create_app,
        profile_fn: Callable[[str, str], GoalProfile] = generate_goal_profile,
        review_fn: Callable[..., str] = generate_session_review,
        schedule_fn: Callable[[int, Callable[[], None]], Any] | None = None,
        cancel_schedule_fn: Callable[[Any], None] | None = None,
    ) -> None:
        self.ui = ui
        self.timings = timings
        self.api_key = api_key
        self.db_path = db_path
        self.env_path = str(resolve_dotenv_path(env_path))
        self.host = host
        self.port = port
        self.fake_judge = fake_judge

        self._session_logger_cls = session_logger_cls
        self._engine_cls = engine_cls
        self._watcher_cls = watcher_cls
        self._server_starter = server_starter
        self._app_factory = app_factory
        self._profile_fn = profile_fn
        self._review_fn = review_fn
        self._schedule_fn = schedule_fn
        self._cancel_schedule_fn = cancel_schedule_fn

        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        self.settings_store = SettingsStore(os.path.join(db_dir, "settings.json"))
        self.presets_store = PresetsStore(os.path.join(db_dir, "presets.json"))
        self.settings: AppSettings = self.settings_store.load()
        if self.settings_store.exists():
            self.timings = get_timings(self.settings.timing_preset)

        if enable_os_hotkeys is None:
            enable_os_hotkeys = bool(
                getattr(ui, "root", None) is not None and "PYTEST_CURRENT_TEST" not in os.environ
            )
        self.hotkey_manager = GlobalHotkeyManager(
            on_toggle_pause=self._on_hotkey_toggle_pause,
            on_toggle_hud=self._on_hotkey_toggle_hud,
            on_open_rules=self._on_hotkey_open_rules,
            enable_os_registration=enable_os_hotkeys,
        )

        self._lock = threading.RLock()
        self.session_active = False
        self.current_goal: str | None = None
        self._current_mode: ModeType = "normal"
        self._session_id: int | None = None
        self._session_deadline: float | None = None
        self._session_started_mono: float | None = None
        self._total_paused_seconds: float = 0.0
        self._last_log_id: int | None = None
        self._tick_handle: Any = None
        self._ending = False
        self._t1_calls = 0
        self._t2_calls = 0
        self._paused = False
        self._pause_started_at: float | None = None
        self._pause_until: float | None = None
        self._last_ext_connected: bool | None = None
        self.profile: GoalProfile | None = None
        self._draft_profile: GoalProfile | None = None
        self._hud_pos: tuple[int, int] | None = None

        # Pomodoro state
        self._pomodoro_enabled: bool = self.settings.pomodoro_enabled
        self._pomodoro_total_cycles: int = self.settings.pomodoro_cycles
        self._pomodoro_cycle: int = 1
        self._pomodoro_pending_next_work: bool = False
        self._work_duration_minutes: int = 25

        # Tab Vault & Live Activity Feed
        self._closed_tabs_vault: list[ClosedTabEntry] = []
        self._activity_feed: deque[ActivityFeedItem] = deque(maxlen=20)
        self._last_active_target: tuple[Literal["domain", "app"], str] | None = None

        self.logger: SessionLogger | None = None
        self.tab_store: TabSnapshotStore | None = None
        self.command_queue: CommandQueue | None = None
        self.cache: VerdictCache | None = None
        self.state_machine: AgentStateMachine | None = None
        self.engine: EventEngine | None = None
        self.watcher: WindowWatcher | None = None
        self.server_handle: Any = None

    def _dispatch_ui(self, fn: Callable[[], None]) -> None:
        if hasattr(self.ui, "dispatch"):
            try:
                self.ui.dispatch(fn)  # type: ignore[attr-defined]
                return
            except Exception:
                pass
        fn()

    def _schedule(self, delay_ms: int, callback: Callable[[], None]) -> Any:
        if self._schedule_fn is not None:
            return self._schedule_fn(delay_ms, callback)
        root = getattr(self.ui, "root", None)
        if root is not None:
            return root.after(delay_ms, callback)
        return None

    def _cancel_schedule(self, handle: Any) -> None:
        if handle is None:
            return
        if self._cancel_schedule_fn is not None:
            self._cancel_schedule_fn(handle)
            return
        root = getattr(self.ui, "root", None)
        if root is not None:
            try:
                root.after_cancel(handle)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Global Hotkey Handlers
    # ------------------------------------------------------------------
    def _on_hotkey_toggle_pause(self) -> None:
        with self._lock:
            active = self.session_active and not self._ending
        if active:
            self.toggle_pause()

    def _on_hotkey_toggle_hud(self) -> None:
        if hasattr(self.ui, "toggle_hud_visibility"):
            try:
                self.ui.toggle_hud_visibility()  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Failed toggling HUD visibility via hotkey")

    def _on_hotkey_open_rules(self) -> None:
        with self._lock:
            active = self.session_active and not self._ending
        if active:
            self.open_rules_editor()
        else:
            def _open_pre() -> None:
                goal_var = getattr(self.ui, "_setup_goal_var", None)
                g_txt = goal_var.get().strip() if goal_var is not None else ""
                self.open_setup_rules_editor(g_txt)

            self._dispatch_ui(_open_pre)

    # ------------------------------------------------------------------
    # Session & Window Position Persistence
    # ------------------------------------------------------------------
    def _get_last_session_path(self) -> str:
        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        return os.path.join(db_dir, "last_session.json")

    def _load_last_session(self) -> dict[str, Any]:
        try:
            path = self._get_last_session_path()
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
        except Exception:
            logger.exception("Failed to load last_session.json")
        return {}

    def _save_last_session(
        self,
        goal: str,
        duration_minutes: int,
        mode: ModeType,
        theme: str | None = None,
    ) -> None:
        try:
            existing = self._load_last_session()
            path = self._get_last_session_path()
            resolved_theme = theme if theme in ("light", "dark") else existing.get("theme")
            payload: dict[str, Any] = dict(existing)
            payload["goal"] = goal
            payload["duration_minutes"] = duration_minutes
            payload["mode"] = mode
            if resolved_theme in ("light", "dark"):
                payload["theme"] = resolved_theme
            if self._hud_pos is not None:
                payload["hud_x"] = self._hud_pos[0]
                payload["hud_y"] = self._hud_pos[1]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            logger.exception("Failed to save last_session.json")

    def _save_theme_preference(self, theme: str) -> None:
        if theme not in ("light", "dark"):
            return
        try:
            existing = self._load_last_session()
            existing["theme"] = theme
            path = self._get_last_session_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
        except Exception:
            logger.exception("Failed to save theme preference in last_session.json")

    def _save_hud_position(self, x: int, y: int) -> None:
        with self._lock:
            self._hud_pos = (int(x), int(y))
        try:
            existing = self._load_last_session()
            existing["hud_x"] = int(x)
            existing["hud_y"] = int(y)
            path = self._get_last_session_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
        except Exception:
            logger.exception("Failed to save HUD position in last_session.json")

    # ------------------------------------------------------------------
    # Presets, Settings & History Actions
    # ------------------------------------------------------------------
    def _on_select_preset(self, preset: FocusPreset) -> None:
        with self._lock:
            self._draft_profile = GoalProfile(
                goal=preset.goal,
                on_task_topics=list(preset.profile.on_task_topics) or [preset.goal],
                allowed_domains=list(preset.profile.allowed_domains),
                borderline_domains=list(preset.profile.borderline_domains),
                blocked_domains=list(preset.profile.blocked_domains),
                allowed_apps=list(preset.profile.allowed_apps),
                blocked_apps=list(preset.profile.blocked_apps),
            )

    def _on_save_preset(
        self,
        name: str,
        goal: str,
        duration_minutes: int,
        mode: ModeType,
    ) -> list[FocusPreset]:
        with self._lock:
            draft = self._draft_profile
        if draft is not None and draft.goal == goal:
            prof = draft
        else:
            prof = fallback_goal_profile(goal)
        preset = FocusPreset(
            name=name.strip(),
            goal=goal.strip(),
            duration_minutes=duration_minutes,
            mode=mode,
            profile=prof,
        )
        return self.presets_store.upsert_preset(preset)

    def _on_delete_preset(self, name: str) -> list[FocusPreset]:
        return self.presets_store.delete_preset(name)

    def _on_pomodoro_toggled(self, enabled: bool) -> None:
        with self._lock:
            self._pomodoro_enabled = bool(enabled)

    def apply_settings(self, new_settings: AppSettings) -> None:
        """Persist updated settings and apply them live across UI, timings, and hotkeys."""
        saved = self.settings_store.save(new_settings)
        new_timings = get_timings(saved.timing_preset)
        with self._lock:
            self.settings = saved
            self.timings = new_timings
            self._pomodoro_enabled = saved.pomodoro_enabled
            self._pomodoro_total_cycles = saved.pomodoro_cycles
            eng = self.engine
            sm = self.state_machine

        if eng is not None:
            eng.timings = new_timings
        if sm is not None:
            sm.timings = new_timings

        if hasattr(self.ui, "configure_preferences"):
            try:
                self.ui.configure_preferences(  # type: ignore[attr-defined]
                    break_minutes=saved.break_minutes,
                    mute_sounds=saved.mute_sounds,
                )
            except Exception:
                pass

        if saved.hotkeys_enabled:
            self.hotkey_manager.start()
        else:
            self.hotkey_manager.stop()

        if getattr(self.ui, "_active_screen", "") == "setup":
            cur_goal = getattr(self.ui, "_setup_goal_var", None)
            cur_dur = getattr(self.ui, "_setup_dur_var", None)
            cur_mode = getattr(self.ui, "_setup_mode_var", None)
            g_val = cur_goal.get() if cur_goal is not None else None
            try:
                d_val = int(cur_dur.get().strip()) if cur_dur is not None else None
            except Exception:
                d_val = None
            m_raw = cur_mode.get() if cur_mode is not None else None
            m_val: ModeType | None = m_raw if m_raw in ("strict", "normal") else None  # type: ignore[assignment]
            self._render_setup_with_fallbacks(g_val, d_val, m_val)

    def _current_api_key_status(self) -> tuple[bool, str]:
        p = Path(self.env_path)
        if p.is_file():
            # Read only from self.env_path if it exists; avoid leaking host env into empty test controllers
            key_from_file = ""
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("GEMINI_API_KEY="):
                        val = stripped.split("=", 1)[1].strip().strip("'\"")
                        if not is_placeholder_or_empty_api_key(val):
                            key_from_file = val
            except Exception:
                pass
            if key_from_file:
                return True, f"Configured ({mask_api_key(key_from_file)})"
        if not is_placeholder_or_empty_api_key(self.api_key):
            return True, f"Configured ({mask_api_key(self.api_key)})"
        return False, "Not configured"

    def _sync_setup_api_key_badge(self, status: tuple[bool, str]) -> None:
        if hasattr(self.ui, "update_setup_api_key_status"):
            try:
                self.ui.update_setup_api_key_status(status)  # type: ignore[attr-defined]
            except Exception:
                pass

    def _save_api_key(self, new_key: str) -> tuple[bool, str]:
        cleaned = (new_key or "").strip()
        if is_placeholder_or_empty_api_key(cleaned):
            return False, "Please enter a valid Google Gemini API key."
        try:
            write_api_key_to_dotenv(self.env_path, cleaned)
            with self._lock:
                self.api_key = cleaned
                if self.engine is not None:
                    self.engine.api_key = cleaned
                should_clear_skip = self.settings.onboarding_skipped
            if should_clear_skip:
                saved = self.settings_store.save(replace(self.settings, onboarding_skipped=False))
                with self._lock:
                    self.settings = saved
            get_model_manager().reset_preferences()
            status = read_masked_api_key(self.env_path)
            self._sync_setup_api_key_badge(status)
            return status
        except Exception as exc:
            logger.exception("Failed saving GEMINI_API_KEY")
            return False, f"Save failed: {exc}"

    def _clear_api_key(self) -> tuple[bool, str]:
        try:
            clear_api_key_in_dotenv(self.env_path, update_environ=True)
            with self._lock:
                self.api_key = ""
                if self.engine is not None:
                    self.engine.api_key = ""
            get_model_manager().reset_preferences()
            status = (False, "Not configured")
            self._sync_setup_api_key_badge(status)
            return status
        except Exception as exc:
            logger.exception("Failed clearing GEMINI_API_KEY")
            return False, f"Remove failed: {exc}"

    def _test_api_key_async(
        self,
        candidate_key: str,
        done_cb: Callable[[bool, str], None],
    ) -> None:
        def _worker() -> None:
            key = (candidate_key or "").strip() or (self.api_key or "").strip()
            if is_placeholder_or_empty_api_key(key):
                self._dispatch_ui(lambda: done_cb(False, "No valid API key configured."))
                return
            try:
                client = genai.Client(api_key=key)
                resp = client.models.generate_content(
                    model=MODEL_PREFERENCE_1,
                    contents="Reply with OK",
                )
                txt = (getattr(resp, "text", None) or "").strip()
                if txt:
                    self._dispatch_ui(lambda: done_cb(True, f"✓ Connected ({MODEL_PREFERENCE_1})"))
                else:
                    self._dispatch_ui(lambda: done_cb(True, "✓ API key accepted."))
            except Exception as exc:
                err_short = str(exc).splitlines()[0][:90]
                self._dispatch_ui(lambda: done_cb(False, f"✕ Connection failed: {err_short}"))

        threading.Thread(target=_worker, daemon=True, name="api-key-test").start()

    def open_settings_dialog(self, *, focus_api_key: bool = False) -> None:
        if hasattr(self.ui, "show_settings_dialog"):
            key_status = self._current_api_key_status()
            try:
                self.ui.show_settings_dialog(  # type: ignore[attr-defined]
                    self.settings,
                    key_status,
                    on_save_settings=self.apply_settings,
                    on_save_api_key=self._save_api_key,
                    on_test_api_key=self._test_api_key_async,
                    on_clear_api_key=self._clear_api_key,
                    focus_api_key=focus_api_key,
                )
            except TypeError:
                self.ui.show_settings_dialog(  # type: ignore[attr-defined]
                    self.settings,
                    key_status,
                    on_save_settings=self.apply_settings,
                    on_save_api_key=self._save_api_key,
                    on_test_api_key=self._test_api_key_async,
                )

    def open_history_dialog(self) -> None:
        if not hasattr(self.ui, "show_history_dialog"):
            return
        hist_logger = self.logger or self._session_logger_cls(db_path=self.db_path)
        stats = hist_logger.get_overall_stats()
        sessions = hist_logger.list_sessions_summary(limit=50)

        def _read_past_review(sess: dict[str, Any]) -> None:
            started = float(sess.get("started_at") or 0.0)
            ended = float(sess.get("ended_at") or started)
            dur_cap = max(1.0, float(sess.get("duration_minutes") or 25))
            elapsed_m = min(dur_cap, max(0.1, (ended - started) / 60.0)) if ended > started else dur_cap
            score = int(sess.get("focus_score_pct", 100))
            kpis = SessionKPIs(
                focus_score_pct=score,
                focused_minutes=round(elapsed_m * (score / 100.0), 1),
                distractions_count=int(sess.get("distractions_count", 0)),
                overrides_count=int(sess.get("overrides_count", 0)),
                total_checks=int(sess.get("total_checks", 0)),
            )
            vault_entries: list[ClosedTabEntry] = []
            for raw_tab in sess.get("closed_tabs") or []:
                if isinstance(raw_tab, dict) and raw_tab.get("url"):
                    vault_entries.append(
                        ClosedTabEntry(
                            title=str(raw_tab.get("title") or raw_tab.get("domain") or ""),
                            url=str(raw_tab.get("url") or ""),
                            domain=str(raw_tab.get("domain") or ""),
                            closed_at=float(raw_tab.get("closed_at") or 0.0),
                        )
                    )
            rev_txt = str(sess.get("review_text") or "").strip() or (
                f"## {sess.get('goal', 'Focus Session')}\n\nNo saved review text for this session."
            )
            self._show_review_screen(rev_txt, kpis=kpis, closed_tabs=vault_entries)

        def _repeat_past_session(sess: dict[str, Any]) -> None:
            goal = str(sess.get("goal") or "Focus").strip()
            dur = max(1, int(sess.get("duration_minutes") or 25))
            raw_mode = str(sess.get("mode") or "strict").lower()
            mode: ModeType = "normal" if raw_mode == "normal" else "strict"
            prof_dict = sess.get("profile")
            if isinstance(prof_dict, dict) and prof_dict:
                prof = GoalProfile(
                    goal=goal,
                    on_task_topics=list(prof_dict.get("on_task_topics") or [goal]),
                    allowed_domains=list(prof_dict.get("allowed_domains") or []),
                    borderline_domains=list(prof_dict.get("borderline_domains") or []),
                    blocked_domains=list(prof_dict.get("blocked_domains") or []),
                    allowed_apps=list(prof_dict.get("allowed_apps") or []),
                    blocked_apps=list(prof_dict.get("blocked_apps") or []),
                )
                with self._lock:
                    self._draft_profile = prof
            self._save_last_session(goal, dur, mode)
            self._render_setup_with_fallbacks(goal, dur, mode)

        self.ui.show_history_dialog(  # type: ignore[attr-defined]
            stats,
            sessions,
            on_read_review=_read_past_review,
            on_repeat_session=_repeat_past_session,
        )

    def open_activity_drawer(self) -> None:
        if hasattr(self.ui, "show_activity_drawer"):
            self.ui.show_activity_drawer(on_mark_relevant=self.mark_activity_item_relevant)  # type: ignore[attr-defined]

    def _render_setup_with_fallbacks(
        self,
        init_goal: str | None,
        init_dur: int | None,
        init_mode: ModeType | None,
    ) -> None:
        presets = self.presets_store.list_presets()
        key_status = self._current_api_key_status()
        try:
            self.ui.show_setup(  # type: ignore[call-arg]
                on_start=self.start_session,
                initial_goal=init_goal,
                initial_duration=init_dur,
                initial_mode=init_mode,
                on_edit_rules=self.open_setup_rules_editor,
                presets=presets,
                on_select_preset=self._on_select_preset,
                on_save_preset=self._on_save_preset,
                on_delete_preset=self._on_delete_preset,
                initial_pomodoro=self._pomodoro_enabled,
                pomodoro_cycles=self._pomodoro_total_cycles,
                on_pomodoro_toggled=self._on_pomodoro_toggled,
                on_open_history=self.open_history_dialog,
                on_open_settings=self.open_settings_dialog,
                api_key_status=key_status,
                on_open_api_key=lambda: self.open_settings_dialog(focus_api_key=True),
            )
        except TypeError:
            try:
                self.ui.show_setup(  # type: ignore[call-arg]
                    on_start=self.start_session,
                    initial_goal=init_goal,
                    initial_duration=init_dur,
                    initial_mode=init_mode,
                    on_edit_rules=self.open_setup_rules_editor,
                    presets=presets,
                    on_select_preset=self._on_select_preset,
                    on_save_preset=self._on_save_preset,
                    on_delete_preset=self._on_delete_preset,
                    initial_pomodoro=self._pomodoro_enabled,
                    pomodoro_cycles=self._pomodoro_total_cycles,
                    on_pomodoro_toggled=self._on_pomodoro_toggled,
                    on_open_history=self.open_history_dialog,
                    on_open_settings=self.open_settings_dialog,
                )
            except TypeError:
                try:
                    self.ui.show_setup(
                        on_start=self.start_session,
                        initial_goal=init_goal,
                        initial_duration=init_dur,
                        initial_mode=init_mode,
                        on_edit_rules=self.open_setup_rules_editor,
                    )
                except TypeError:
                    try:
                        self.ui.show_setup(
                            on_start=self.start_session,
                            initial_goal=init_goal,
                            initial_duration=init_dur,
                            initial_mode=init_mode,
                        )
                    except TypeError:
                        self.ui.show_setup(on_start=self.start_session)

    # ------------------------------------------------------------------
    # Core Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Show the initial setup screen, prefilled with last session, theme, HUD pos, and hotkeys."""
        root = getattr(self.ui, "root", None)
        if root is not None and hasattr(root, "protocol"):
            try:
                root.protocol("WM_DELETE_WINDOW", self.shutdown)
            except Exception:
                pass

        last_sess = self._load_last_session()
        init_goal = last_sess.get("goal")
        init_dur = last_sess.get("duration_minutes")
        init_mode = last_sess.get("mode")
        init_theme = last_sess.get("theme")

        hx = last_sess.get("hud_x")
        hy = last_sess.get("hud_y")
        if isinstance(hx, int) and not isinstance(hx, bool) and isinstance(hy, int) and not isinstance(hy, bool):
            self._hud_pos = (hx, hy)

        if hasattr(self.ui, "on_hud_moved"):
            try:
                self.ui.on_hud_moved = self._save_hud_position  # type: ignore[attr-defined]
            except Exception:
                pass

        if hasattr(self.ui, "configure_preferences"):
            try:
                self.ui.configure_preferences(  # type: ignore[attr-defined]
                    break_minutes=self.settings.break_minutes,
                    mute_sounds=self.settings.mute_sounds,
                    hud_pos=self._hud_pos,
                )
            except Exception:
                pass

        if self.settings.hotkeys_enabled:
            self.hotkey_manager.start()

        if hasattr(self.ui, "on_theme_changed"):
            try:
                self.ui.on_theme_changed = self._save_theme_preference  # type: ignore[attr-defined]
            except Exception:
                pass
        if init_theme in ("light", "dark") and hasattr(self.ui, "set_theme"):
            try:
                self.ui.set_theme(init_theme)  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Failed to apply saved theme")

        needs_onboarding = (
            (not self.fake_judge)
            and is_placeholder_or_empty_api_key(self.api_key)
            and (not self.settings.onboarding_skipped)
            and hasattr(self.ui, "show_onboarding")
        )
        if needs_onboarding:
            self.ui.show_onboarding(  # type: ignore[attr-defined]
                on_save_key=self._save_api_key,
                on_test_key=self._test_api_key_async,
                on_continue=lambda: self._render_setup_with_fallbacks(init_goal, init_dur, init_mode),
                on_skip=self._make_onboarding_skip_handler(init_goal, init_dur, init_mode),
            )
            return

        self._render_setup_with_fallbacks(init_goal, init_dur, init_mode)

    def _make_onboarding_skip_handler(
        self,
        init_goal: str | None,
        init_dur: int | None,
        init_mode: ModeType | None,
    ) -> Callable[[], None]:
        def _skip() -> None:
            saved = self.settings_store.save(replace(self.settings, onboarding_skipped=True))
            with self._lock:
                self.settings = saved
            self._render_setup_with_fallbacks(init_goal, init_dur, init_mode)
            if hasattr(self.ui, "update_setup_progress"):
                try:
                    self.ui.update_setup_progress(  # type: ignore[attr-defined]
                        "Running in local rules-only mode. Click 'Set API Key' anytime to enable Gemini AI.",
                        running=False,
                    )
                except Exception:
                    pass

        return _skip

    def _classify_domain(self, domain: str | None) -> str:
        if not domain:
            return "neutral"
        with self._lock:
            prof = self.profile
        if prof is None:
            return "neutral"
        if domain_matches(domain, prof.allowed_domains):
            return "allowed"
        if domain_matches(domain, prof.borderline_domains):
            return "borderline"
        if domain_matches(domain, prof.blocked_domains):
            return "blocked"
        return "neutral"

    def _telemetry_for_extension(self, domain: str | None = None) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            active = self.session_active and not self._ending
            goal = self.current_goal
            mode = self._current_mode
            paused = self._paused
            deadline = self._session_deadline
            pause_until = self._pause_until
            sm = self.state_machine

        state = sm.state if (active and sm is not None) else ("ON_TASK" if active else "IDLE")
        rem_secs = max(0.0, (deadline - now)) if (active and deadline is not None and not paused) else 0.0
        pause_rem = max(0.0, (pause_until - now)) if (active and paused and pause_until is not None) else 0.0
        if active and paused and deadline is not None and self._pause_started_at is not None:
            rem_secs = max(0.0, deadline - self._pause_started_at)

        return {
            "session_active": active,
            "goal": goal,
            "mode": mode,
            "state": "PAUSED" if paused else state,
            "paused": paused,
            "remaining_seconds": round(rem_secs, 1),
            "pause_remaining_seconds": round(pause_rem, 1),
            "domain": domain or "",
            "domain_rule": self._classify_domain(domain) if active else "neutral",
        }

    def apply_quick_rule(
        self,
        action: Literal["allow", "block"],
        target: str,
    ) -> dict[str, Any]:
        """Allow or block a website domain or desktop .exe live during an active session."""
        raw = (target or "").strip().lower()
        if not raw:
            return {"ok": False, "error": "Empty target."}

        is_app = raw.endswith(".exe")
        norm = raw if is_app else normalize_domain(raw)
        if not norm:
            return {"ok": False, "error": "Invalid domain."}

        with self._lock:
            if not self.session_active or self.profile is None:
                return {"ok": False, "error": "No active session."}
            cur = self.profile
            if is_app:
                allowed_apps = [a for a in cur.allowed_apps if a.lower() != norm]
                blocked_apps = [b for b in cur.blocked_apps if b.lower() != norm]
                if action == "allow":
                    allowed_apps.append(norm)
                else:
                    blocked_apps.append(norm)
                updated = GoalProfile(
                    goal=cur.goal,
                    on_task_topics=list(cur.on_task_topics),
                    allowed_domains=list(cur.allowed_domains),
                    borderline_domains=list(cur.borderline_domains),
                    blocked_domains=list(cur.blocked_domains),
                    allowed_apps=allowed_apps,
                    blocked_apps=blocked_apps,
                )
            else:
                allowed_doms = [d for d in cur.allowed_domains if d.lower() != norm]
                border_doms = [d for d in cur.borderline_domains if d.lower() != norm]
                blocked_doms = [d for d in cur.blocked_domains if d.lower() != norm]
                if action == "allow":
                    allowed_doms.append(norm)
                else:
                    blocked_doms.append(norm)
                updated = GoalProfile(
                    goal=cur.goal,
                    on_task_topics=list(cur.on_task_topics),
                    allowed_domains=allowed_doms,
                    borderline_domains=border_doms,
                    blocked_domains=blocked_doms,
                    allowed_apps=list(cur.allowed_apps),
                    blocked_apps=list(cur.blocked_apps),
                )
            eng = self.engine

        self.update_goal_profile(updated)
        if action == "allow" and hasattr(self.ui, "hide_popups_and_overlay"):
            try:
                self.ui.hide_popups_and_overlay()
            except Exception:
                pass
        if eng is not None:
            try:
                eng.run_immediate_check("manual_recheck", force_vision=False)
            except Exception:
                pass

        return {
            "ok": True,
            "domain": norm,
            "domain_rule": "allowed" if action == "allow" else "blocked",
        }

    def mark_activity_item_relevant(self, item: ActivityFeedItem) -> None:
        """Handle 1-click 'Mark Relevant / Allow' from the live Activity Drawer."""
        item.overridden = True
        with self._lock:
            sess_logger = self.logger
            cache = self.cache
            sm = self.state_machine
            goal = self.current_goal or "Focus"

        if item.log_id is not None and sess_logger is not None:
            try:
                sess_logger.mark_override(item.log_id)
            except Exception:
                logger.exception("Failed marking override in SQLite from Activity Drawer")

        if item.source_key:
            self.apply_quick_rule("allow", item.source_key)

        if cache is not None and (item.source_key or item.title):
            try:
                cache.record_override(
                    item.source_key,
                    item.title,
                    reason=f"Marked relevant in Activity Feed for {goal}",
                )
            except Exception:
                pass

        if sm is not None and sm.state in ("GRACE_PERIOD", "LAYER_1_ASK", "LAYER_2_ULTIMATUM"):
            try:
                sm.handle_user_action("relevant")
            except Exception:
                pass

        if hasattr(self.ui, "mark_activity_overridden"):
            try:
                self.ui.mark_activity_overridden(item.log_id, item.source_key)  # type: ignore[attr-defined]
            except Exception:
                pass

    def start_session(self, goal: str, duration_minutes: int, mode: ModeType) -> None:
        """Show HUD immediately and initialize session components in a background thread."""
        self._save_last_session(goal, duration_minutes, mode)
        ui_pomo = getattr(self.ui, "setup_pomodoro_enabled", None)
        with self._lock:
            if self.session_active or getattr(self, "_starting", False):
                return
            self._starting = True
            self._ending = False
            self.current_goal = goal
            self._current_mode = mode
            self._work_duration_minutes = max(1, int(duration_minutes))
            if isinstance(ui_pomo, bool):
                self._pomodoro_enabled = ui_pomo
            self._pomodoro_total_cycles = max(2, int(self.settings.pomodoro_cycles))
            self._pomodoro_cycle = 1
            self._pomodoro_pending_next_work = False
            self._session_started_mono = time.monotonic()
            self._total_paused_seconds = 0.0
            self._last_log_id = None
            self._t1_calls = 0
            self._t2_calls = 0
            self._paused = False
            self._pause_started_at = None
            self._pause_until = None
            self._last_ext_connected = None
            self._closed_tabs_vault.clear()
            self._activity_feed.clear()
            self._last_active_target = None
            hud_pos = self._hud_pos

        if hasattr(self.ui, "clear_activity_items"):
            try:
                self.ui.clear_activity_items()  # type: ignore[attr-defined]
            except Exception:
                pass

        try:
            self.ui.show_monitoring_hud(  # type: ignore[call-arg]
                goal,
                mode,
                on_end_session=self.end_session,
                duration_minutes=duration_minutes,
                on_toggle_pause=self.toggle_pause,
                on_open_rules=self.open_rules_editor,
                on_quit=self.shutdown,
                on_open_activity=self.open_activity_drawer,
                initial_hud_pos=hud_pos,
            )
        except TypeError:
            try:
                self.ui.show_monitoring_hud(
                    goal,
                    mode,
                    on_end_session=self.end_session,
                    duration_minutes=duration_minutes,
                    on_toggle_pause=self.toggle_pause,
                    on_open_rules=self.open_rules_editor,
                    on_quit=self.shutdown,
                )
            except TypeError:
                try:
                    self.ui.show_monitoring_hud(
                        goal,
                        mode,
                        on_end_session=self.end_session,
                        duration_minutes=duration_minutes,
                    )
                except TypeError:
                    self.ui.show_monitoring_hud(goal, mode, on_end_session=self.end_session)

        if hasattr(self.ui, "update_extension_status"):
            try:
                self.ui.update_extension_status(False)
            except Exception:
                pass
        if hasattr(self.ui, "update_hud_status"):
            try:
                self.ui.update_hud_status("ON_TASK", "Starting...")
            except Exception:
                pass

        def _set_stage(text: str, running: bool = True) -> None:
            if hasattr(self.ui, "update_setup_progress"):
                try:
                    self.ui.update_setup_progress(text, running=running)  # type: ignore[attr-defined]
                except Exception:
                    pass

        def _startup_worker() -> None:
            try:
                with self._lock:
                    draft = self._draft_profile
                    self._draft_profile = None

                if draft is not None:
                    profile = GoalProfile(
                        goal=goal,
                        on_task_topics=list(draft.on_task_topics) or [goal.strip()],
                        allowed_domains=list(draft.allowed_domains),
                        borderline_domains=list(draft.borderline_domains),
                        blocked_domains=list(draft.blocked_domains),
                        allowed_apps=list(draft.allowed_apps),
                        blocked_apps=list(draft.blocked_apps),
                    )
                elif self.fake_judge:
                    profile = fallback_goal_profile(goal)
                else:
                    _set_stage("Generating focus profile...", running=True)
                    try:
                        profile = self._profile_fn(goal, self.api_key)
                    except Exception:
                        logger.exception("Goal profile generation failed; using fallback profile")
                        profile = fallback_goal_profile(goal)

                with self._lock:
                    self.profile = profile

                _set_stage("Starting local bridge...", running=True)

                session_logger = self._session_logger_cls(db_path=self.db_path)
                session_logger.init_db()
                session_id = session_logger.start_session(goal, mode, duration_minutes, profile)

                model_manager = get_model_manager()

                def _on_tier0_shift(msg: str) -> None:
                    if hasattr(self.ui, "show_tier0_shift_popup"):
                        try:
                            self.ui.show_tier0_shift_popup(msg)
                        except Exception:
                            logger.exception("Failed to show Tier 0 shift popup")
                    if hasattr(self.ui, "update_hud_status"):
                        try:
                            self.ui.update_hud_status("ON_TASK", "AI unavailable - rules only")
                        except Exception:
                            pass

                model_manager.on_shift_to_tier0 = _on_tier0_shift

                tab_store = TabSnapshotStore()
                command_queue = CommandQueue()
                cache = VerdictCache()

                def _on_override(_ctx: ActivityContext | None, _reason: str) -> None:
                    with self._lock:
                        log_id = self._last_log_id
                    if log_id is not None:
                        try:
                            session_logger.mark_override(log_id)
                        except Exception:
                            logger.exception("Failed to mark override in SQLite")
                        if hasattr(self.ui, "mark_activity_overridden"):
                            try:
                                self.ui.mark_activity_overridden(log_id)  # type: ignore[attr-defined]
                            except Exception:
                                pass

                def _on_lockout_done() -> None:
                    with self._lock:
                        eng = self.engine
                    if eng is not None:
                        try:
                            eng.run_immediate_check("manual_recheck", force_vision=True)
                        except Exception:
                            logger.exception("Failed to run post-lockout immediate check")

                state_machine = AgentStateMachine(
                    goal=goal,
                    mode=mode,
                    timings=self.timings,
                    cache=cache,
                    command_queue=command_queue,
                    ui_hooks=self.ui,
                    on_override=_on_override,
                    on_lockout_done=_on_lockout_done,
                    on_quick_rule=self.apply_quick_rule,
                )

                def _record_closed_tabs(ctx: ActivityContext, tab_ids: list[int]) -> None:
                    if not tab_ids:
                        return
                    snap = ctx.tab_snapshot or tab_store.get_latest()
                    if snap is None or not snap.tabs:
                        return
                    by_id = {t.tab_id: t for t in snap.tabs}
                    now_wall = time.time()
                    with self._lock:
                        existing_urls = {entry.url for entry in self._closed_tabs_vault}
                        for tid in tab_ids:
                            t_info = by_id.get(tid)
                            if t_info is not None and t_info.url and t_info.url not in existing_urls:
                                dom = t_info.domain or normalize_domain(t_info.url)
                                self._closed_tabs_vault.append(
                                    ClosedTabEntry(
                                        title=t_info.title or dom or t_info.url,
                                        url=t_info.url,
                                        domain=dom,
                                        closed_at=now_wall,
                                    )
                                )
                                existing_urls.add(t_info.url)

                def _on_verdict(ctx: ActivityContext, verdict: JudgeVerdict) -> None:
                    # Update active target for mid-session 1-click Rules Editor shortcut
                    src_raw = (ctx.source_key or "").strip()
                    if src_raw:
                        if src_raw.lower().endswith(".exe"):
                            with self._lock:
                                self._last_active_target = ("app", src_raw.lower())
                        else:
                            nd = normalize_domain(src_raw)
                            if nd:
                                with self._lock:
                                    self._last_active_target = ("domain", nd)

                    try:
                        decision: AgentDecision = state_machine.handle_verdict(ctx, verdict)
                    except Exception:
                        logger.exception("State machine handle_verdict failed")
                        return

                    if decision.action_taken == "strict_block" and decision.tabs_to_close:
                        _record_closed_tabs(ctx, decision.tabs_to_close)

                    log_id: int | None = None
                    try:
                        log_id = session_logger.log_event(
                            session_id=session_id,
                            ctx=ctx,
                            verdict=verdict,
                            action_taken=decision.action_taken,
                        )
                        with self._lock:
                            self._last_log_id = log_id
                            if verdict.tier_used == 1:
                                self._t1_calls += 1
                            elif verdict.tier_used == 2:
                                self._t2_calls += 1
                            t1_cnt = self._t1_calls
                            t2_cnt = self._t2_calls

                        if hasattr(self.ui, "update_ai_calls"):
                            try:
                                self.ui.update_ai_calls(t1_cnt, t2_cnt)
                            except Exception:
                                pass
                    except Exception:
                        logger.exception("Failed to log verdict to SQLite")

                    feed_item = ActivityFeedItem(
                        log_id=log_id,
                        timestamp=ctx.timestamp if ctx.timestamp > 0 else time.time(),
                        source_key=ctx.source_key or ctx.display_title or "Activity",
                        title=ctx.display_title or "",
                        on_task=verdict.on_task,
                        tier_used=verdict.tier_used,
                        model_used=getattr(verdict, "model_used", "") or "",
                        confidence=float(verdict.confidence),
                        reason=verdict.reason or "",
                        action_taken=decision.action_taken,
                        overridden=False,
                    )
                    with self._lock:
                        self._activity_feed.append(feed_item)
                    if hasattr(self.ui, "append_activity_item"):
                        try:
                            self.ui.append_activity_item(  # type: ignore[attr-defined]
                                feed_item,
                                on_mark_relevant=self.mark_activity_item_relevant,
                            )
                        except Exception:
                            pass

                evaluate_fn = make_fake_judge() if self.fake_judge else None
                engine = self._engine_cls(
                    goal=goal,
                    profile=profile,
                    cache=cache,
                    tab_store=tab_store,
                    timings=self.timings,
                    api_key=self.api_key,
                    on_verdict=_on_verdict,
                    evaluate_fn=evaluate_fn,
                )

                def _on_tab_event(trigger_type: Any) -> None:
                    try:
                        engine.submit_trigger(trigger_type)
                    except Exception:
                        logger.exception("submit_trigger failed on tab event")

                def _on_window_changed(_info: Any) -> None:
                    try:
                        engine.submit_trigger("window_changed")
                    except Exception:
                        logger.exception("submit_trigger failed on window change")

                watcher = self._watcher_cls(
                    on_window_changed=_on_window_changed,
                    poll_interval=self.timings.window_poll_seconds,
                )

                try:
                    fastapi_app = self._app_factory(
                        tab_store=tab_store,
                        command_queue=command_queue,
                        on_tab_event=_on_tab_event,
                        session_status_provider=lambda: (self.session_active, self.current_goal),
                        telemetry_provider=self._telemetry_for_extension,
                        on_quick_rule=self.apply_quick_rule,
                    )
                except TypeError:
                    fastapi_app = self._app_factory(
                        tab_store=tab_store,
                        command_queue=command_queue,
                        on_tab_event=_on_tab_event,
                        session_status_provider=lambda: (self.session_active, self.current_goal),
                    )
                try:
                    server_handle = self._server_starter(fastapi_app, host=self.host, port=self.port)
                except Exception:
                    logger.exception("Failed to start FastAPI bridge server")
                    server_handle = None

                with self._lock:
                    if self._ending:
                        if server_handle is not None:
                            try:
                                if hasattr(server_handle, "should_exit"):
                                    server_handle.should_exit = True
                                elif hasattr(server_handle, "stop"):
                                    server_handle.stop()
                            except Exception:
                                pass
                        return
                    self.logger = session_logger
                    self._session_id = session_id
                    self.tab_store = tab_store
                    self.command_queue = command_queue
                    self.cache = cache
                    self.state_machine = state_machine
                    self.engine = engine
                    self.watcher = watcher
                    self.server_handle = server_handle
                    self.session_active = True
                    self._session_started_mono = time.monotonic()
                    self._session_deadline = time.monotonic() + max(1, duration_minutes) * 60.0
                    pomo_on = self._pomodoro_enabled
                    pomo_tot = self._pomodoro_total_cycles

                _set_stage("Monitoring active.", running=False)
                active_msg = (
                    f"Pomodoro 1/{pomo_tot} — Monitoring active."
                    if pomo_on
                    else "Monitoring active."
                )
                if hasattr(self.ui, "update_hud_status"):
                    try:
                        self.ui.update_hud_status("ON_TASK", active_msg)
                    except Exception:
                        pass
                watcher.start()
                engine.start()
                engine.run_immediate_check("baseline", force_vision=False)
                if hasattr(self.ui, "dispatch"):
                    self.ui.dispatch(self._schedule_tick)  # type: ignore[attr-defined]
                else:
                    self._schedule_tick()
            except Exception:
                logger.exception("Session startup failed")
                _set_stage("Startup fallback error.", running=False)
                if hasattr(self.ui, "update_hud_status"):
                    try:
                        self.ui.update_hud_status("ON_TASK", "Startup fallback error.")
                    except Exception:
                        pass
            finally:
                with self._lock:
                    self._starting = False

        t = threading.Thread(target=_startup_worker, daemon=True, name="session-startup")
        with self._lock:
            self._startup_thread = t
        t.start()

    def wait_for_startup(self, timeout: float = 5.0) -> None:
        """Wait for background session startup thread to finish (for tests)."""
        with self._lock:
            t = getattr(self, "_startup_thread", None)
        if t is not None:
            t.join(timeout=timeout)

    def _schedule_tick(self) -> None:
        with self._lock:
            if not self.session_active or self._ending:
                return
            self._tick_handle = self._schedule(500, self._on_tick)

    def _on_tick(self) -> None:
        with self._lock:
            if not self.session_active or self._ending:
                return
            sm = self.state_machine
            deadline = self._session_deadline
            tab_store = self.tab_store
            paused = self._paused
            pause_until = self._pause_until
            prev_ext = self._last_ext_connected
            pomo_on = self._pomodoro_enabled
            pomo_cur = self._pomodoro_cycle
            pomo_tot = self._pomodoro_total_cycles
            brk_mins = self.settings.break_minutes
            mute = self.settings.mute_sounds

        if tab_store is not None and hasattr(tab_store, "is_extension_connected"):
            try:
                connected = bool(tab_store.is_extension_connected(stale_seconds=8.0))
                if connected != prev_ext:
                    with self._lock:
                        self._last_ext_connected = connected
                    if hasattr(self.ui, "update_extension_status"):
                        self.ui.update_extension_status(connected)
            except Exception:
                logger.exception("Error checking extension connectivity")

        if paused:
            rem = max(0.0, (pause_until or 0.0) - time.monotonic())
            if rem <= 0.0:
                self.resume_session()
            else:
                if hasattr(self.ui, "update_pause_status"):
                    try:
                        self.ui.update_pause_status(True, rem)
                    except Exception:
                        pass
            self._schedule_tick()
            return

        if deadline is not None and time.monotonic() >= deadline:
            if pomo_on and pomo_cur < pomo_tot:
                with self._lock:
                    completed_cycle = self._pomodoro_cycle
                    self._pomodoro_cycle += 1
                    self._pomodoro_pending_next_work = True
                break_msg = f"Cycle {completed_cycle}/{pomo_tot} complete — {brk_mins}m break!"
                self.pause_session(duration_seconds=max(1, brk_mins) * 60.0, summary=break_msg)
                if not mute:
                    root = getattr(self.ui, "root", None)
                    if root is not None and hasattr(root, "bell"):
                        try:
                            self._dispatch_ui(root.bell)
                        except Exception:
                            pass
                self._schedule_tick()
                return

            self.end_session()
            return

        if sm is not None:
            try:
                sm.handle_tick()
                if sm.state == "GRACE_PERIOD" and hasattr(self.ui, "update_grace_countdown"):
                    self.ui.update_grace_countdown(sm.grace_remaining_seconds())  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Error in state_machine.handle_tick")

        self._schedule_tick()

    def pause_session(
        self,
        duration_seconds: float | None = None,
        summary: str | None = None,
    ) -> None:
        """Intentionally pause monitoring for a short break without ending the session."""
        default_dur = max(1, int(self.settings.break_minutes)) * 60.0
        dur = max(1.0, float(duration_seconds if duration_seconds is not None else default_dur))
        now = time.monotonic()
        with self._lock:
            if not self.session_active or self._ending:
                return
            self._paused = True
            self._pause_started_at = now
            self._pause_until = now + dur
            engine = self.engine

        if engine is not None and hasattr(engine, "pause"):
            try:
                engine.pause(dur)
            except Exception:
                logger.exception("Failed to pause EventEngine")

        if hasattr(self.ui, "hide_popups_and_overlay"):
            try:
                self.ui.hide_popups_and_overlay()
            except Exception:
                pass
        if hasattr(self.ui, "update_pause_status"):
            try:
                if summary is not None:
                    try:
                        self.ui.update_pause_status(True, dur, summary=summary)  # type: ignore[call-arg]
                    except TypeError:
                        self.ui.update_pause_status(True, dur)
                else:
                    self.ui.update_pause_status(True, dur)
            except Exception:
                logger.exception("Failed to update HUD pause status")

    def resume_session(self) -> None:
        """Resume monitoring after a break, shifting deadline or starting the next Pomodoro work cycle."""
        now = time.monotonic()
        with self._lock:
            if not self._paused:
                return
            elapsed = max(0.0, now - (self._pause_started_at or now))
            self._total_paused_seconds += elapsed
            starting_next_pomodoro = self._pomodoro_pending_next_work
            if starting_next_pomodoro:
                self._pomodoro_pending_next_work = False
                work_secs = max(1, self._work_duration_minutes) * 60.0
                self._session_deadline = now + work_secs
            else:
                work_secs = 0.0
                if self._session_deadline is not None:
                    self._session_deadline += elapsed
            pomo_cur = self._pomodoro_cycle
            pomo_tot = self._pomodoro_total_cycles
            self._paused = False
            self._pause_started_at = None
            self._pause_until = None
            engine = self.engine
            active = self.session_active and not self._ending

        if engine is not None and hasattr(engine, "resume"):
            try:
                engine.resume()
            except Exception:
                logger.exception("Failed to resume EventEngine")

        if starting_next_pomodoro and hasattr(self.ui, "reset_hud_timer"):
            try:
                self.ui.reset_hud_timer(work_secs)  # type: ignore[attr-defined]
            except Exception:
                pass

        if hasattr(self.ui, "update_pause_status"):
            try:
                self.ui.update_pause_status(False, 0.0)
            except Exception:
                pass

        if active and hasattr(self.ui, "update_hud_status"):
            msg = (
                f"Pomodoro {pomo_cur}/{pomo_tot} — Focus active."
                if starting_next_pomodoro
                else "Monitoring resumed."
            )
            try:
                self.ui.update_hud_status("ON_TASK", msg)
            except Exception:
                pass

    def toggle_pause(self, duration_seconds: float | None = None) -> None:
        """Toggle between active monitoring and a break."""
        with self._lock:
            currently_paused = self._paused
        if currently_paused:
            self.resume_session()
        else:
            self.pause_session(duration_seconds=duration_seconds)

    def update_goal_profile(self, new_profile: GoalProfile) -> None:
        """Apply edited allow/block rules live to the active engine and clear cached verdicts."""
        with self._lock:
            self.profile = new_profile
            engine = self.engine
            cache = self.cache

        if cache is not None:
            try:
                cache.clear()
            except Exception:
                logger.exception("Failed clearing VerdictCache after profile update")
        if engine is not None and hasattr(engine, "update_profile"):
            try:
                engine.update_profile(new_profile)
            except Exception:
                logger.exception("Failed updating EventEngine profile")
        if hasattr(self.ui, "update_hud_status"):
            try:
                self.ui.update_hud_status("ON_TASK", "Rules updated.")
            except Exception:
                pass

    def _save_draft_profile(self, draft: GoalProfile) -> None:
        with self._lock:
            self._draft_profile = draft

    def _suggest_rules_async(
        self,
        goal: str,
        done_cb: Callable[[GoalProfile], None],
    ) -> None:
        def _worker() -> None:
            try:
                if self.fake_judge:
                    suggested = fallback_goal_profile(goal)
                else:
                    suggested = self._profile_fn(goal, self.api_key)
            except Exception:
                logger.exception("AI Suggest Rules failed; returning fallback profile")
                suggested = fallback_goal_profile(goal)
            done_cb(suggested)

        threading.Thread(target=_worker, daemon=True, name="ai-suggest-rules").start()

    def open_setup_rules_editor(self, goal_text: str = "") -> None:
        """Open the rules editor before starting a session (with optional AI Suggest button)."""
        clean_goal = (goal_text or "").strip() or "Focus"
        with self._lock:
            draft = self._draft_profile
        if draft is None or draft.goal != clean_goal:
            draft = fallback_goal_profile(clean_goal)
        if hasattr(self.ui, "show_rules_editor"):
            try:
                self.ui.show_rules_editor(  # type: ignore[call-arg]
                    draft,
                    on_save=self._save_draft_profile,
                    on_ai_suggest=self._suggest_rules_async,
                )
            except TypeError:
                self.ui.show_rules_editor(draft, on_save=self._save_draft_profile)
            except Exception:
                logger.exception("Failed to open setup rules editor")

    def open_rules_editor(self) -> None:
        """Open the live editable domain/app rules dialog with 1-click current target shortcut."""
        with self._lock:
            profile = self.profile or fallback_goal_profile(self.current_goal or "Focus")
            active_target = self._last_active_target
            tab_store = self.tab_store

        if active_target is None and tab_store is not None:
            snap = tab_store.get_latest()
            if snap and snap.tabs:
                act_tab = next((t for t in snap.tabs if t.active), snap.tabs[0])
                dom = act_tab.domain or normalize_domain(act_tab.url)
                if dom:
                    active_target = ("domain", dom)

        if hasattr(self.ui, "show_rules_editor"):
            try:
                self.ui.show_rules_editor(  # type: ignore[call-arg]
                    profile,
                    on_save=self.update_goal_profile,
                    active_target=active_target,
                )
            except TypeError:
                self.ui.show_rules_editor(profile, on_save=self.update_goal_profile)
            except Exception:
                logger.exception("Failed to open rules editor")

    def _stop_background_services(self) -> None:
        with self._lock:
            tick_handle = self._tick_handle
            self._tick_handle = None
            engine = self.engine
            watcher = self.watcher
            server = self.server_handle
            self.session_active = False
            self._paused = False
            self._pause_started_at = None
            self._pause_until = None
            self._pomodoro_pending_next_work = False
            self._last_ext_connected = None

        self._cancel_schedule(tick_handle)

        if engine is not None:
            try:
                engine.stop()
            except Exception:
                logger.exception("Error stopping EventEngine")

        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                logger.exception("Error stopping WindowWatcher")

        if server is not None:
            try:
                server.should_exit = True
            except Exception:
                logger.exception("Error stopping uvicorn server")

    def _show_review_screen(
        self,
        review_text: str,
        *,
        kpis: SessionKPIs | None = None,
        closed_tabs: list[ClosedTabEntry] | None = None,
    ) -> None:
        try:
            self.ui.show_review(  # type: ignore[call-arg]
                review_text,
                on_close=self.shutdown,
                on_new_session=self.restart_to_setup,
                kpis=kpis,
                closed_tabs=closed_tabs,
            )
        except TypeError:
            try:
                self.ui.show_review(
                    review_text,
                    on_close=self.shutdown,
                    on_new_session=self.restart_to_setup,
                )
            except TypeError:
                self.ui.show_review(review_text, on_close=self.shutdown)

    @staticmethod
    def _compute_session_kpis(
        logs: list[dict[str, Any]],
        elapsed_work_minutes: float,
    ) -> SessionKPIs:
        total = len(logs)
        overrides = sum(1 for r in logs if bool(r.get("user_override")))
        on_task_cnt = sum(1 for r in logs if bool(r.get("on_task")) or bool(r.get("user_override")))
        distractions = sum(1 for r in logs if not bool(r.get("on_task")) and not bool(r.get("user_override")))
        score = int(round((on_task_cnt / total) * 100)) if total > 0 else 100
        focused_mins = round(max(0.0, elapsed_work_minutes * (score / 100.0)), 1)
        return SessionKPIs(
            focus_score_pct=score,
            focused_minutes=focused_mins,
            distractions_count=distractions,
            overrides_count=overrides,
            total_checks=total,
        )

    def restart_to_setup(self) -> None:
        """Stop any active services and return cleanly to the Setup screen for a new session."""
        self._stop_background_services()
        with self._lock:
            self._ending = False
            self._starting = False
            self._session_deadline = None
            self.session_active = False
            self.profile = None
        self.start()

    def end_session(self) -> None:
        """Stop monitoring, show review screen immediately, and generate review + KPIs off the UI thread."""
        now_mono = time.monotonic()
        with self._lock:
            was_starting = bool(getattr(self, "_starting", False))
            if self._ending or (not self.session_active and not was_starting):
                return
            self._ending = True
            if self._paused and self._pause_started_at is not None:
                self._total_paused_seconds += max(0.0, now_mono - self._pause_started_at)
            started_mono = self._session_started_mono or now_mono
            elapsed_work_minutes = max(0.0, ((now_mono - started_mono) - self._total_paused_seconds) / 60.0)
            closed_tabs = list(self._closed_tabs_vault)
            sm = self.state_machine
            session_logger = self.logger
            session_id = self._session_id
            goal = self.current_goal or ""
            mode = self._current_mode
            api_key = self.api_key

        try:
            self._stop_background_services()
        except Exception:
            logger.exception("Error stopping background services")

        if sm is not None:
            try:
                sm.end_session()
            except Exception:
                logger.exception("Error ending state machine")

        try:
            self._show_review_screen(
                "Generating your review...",
                kpis=None,
                closed_tabs=closed_tabs,
            )
        except Exception:
            logger.exception("Failed to show initial review screen")

        def _local_summary(logs: list[dict[str, Any]]) -> str:
            total = len(logs)
            if total == 0:
                return f"Session completed for goal: '{goal}' ({mode} mode). No activity recorded."
            on_task_count = sum(1 for row in logs if bool(row.get("on_task")) or bool(row.get("user_override")))
            pct = int((on_task_count / total) * 100)
            return f"Session summary for '{goal}' ({mode} mode): {pct}% on-task across {total} checks."

        def _review_worker() -> None:
            logs: list[dict[str, Any]] = []
            review_text = ""
            kpis: SessionKPIs | None = None
            try:
                if session_logger is not None and session_id is not None:
                    try:
                        logs = session_logger.get_session_logs(session_id)
                    except Exception:
                        logger.exception("Failed to fetch session logs")

                kpis = self._compute_session_kpis(logs, elapsed_work_minutes)
                fallback_text = _local_summary(logs)
                review_box: list[str] = []
                done_event = threading.Event()

                def _run_review_fn() -> None:
                    try:
                        text = self._review_fn(goal, mode, logs, api_key)
                        review_box.append((text or "").strip() or fallback_text)
                    except Exception:
                        logger.exception("Review generation failed")
                        review_box.append(fallback_text)
                    finally:
                        done_event.set()

                inner = threading.Thread(target=_run_review_fn, daemon=True, name="session-review-fn")
                inner.start()
                if done_event.wait(timeout=15.0) and review_box:
                    review_text = review_box[0]
                else:
                    review_text = fallback_text

                model_counts: dict[str, int] = {}
                for row in logs:
                    m = row.get("model_used")
                    if m:
                        model_counts[m] = model_counts.get(m, 0) + 1
                    elif row.get("tier_used") == 0:
                        model_counts["Tier 0 (rules)"] = model_counts.get("Tier 0 (rules)", 0) + 1

                if model_counts:
                    breakdown_lines = ["\n\n### Model Usage:"]
                    for m_name, count in sorted(model_counts.items(), key=lambda x: -x[1]):
                        breakdown_lines.append(f"- {m_name}: {count}")
                    review_text += "\n".join(breakdown_lines)

                if session_logger is not None and session_id is not None:
                    try:
                        closed_dicts = [asdict(t) for t in closed_tabs]
                        try:
                            session_logger.finish_session(session_id, review_text, closed_tabs=closed_dicts)
                        except TypeError:
                            session_logger.finish_session(session_id, review_text)
                    except Exception:
                        logger.exception("Failed to save session review to SQLite")
            except Exception:
                logger.exception("Unexpected error in end-of-session review worker")
                review_text = _local_summary(logs)
                if kpis is None:
                    kpis = self._compute_session_kpis(logs, elapsed_work_minutes)
            finally:
                if not review_text:
                    review_text = _local_summary(logs)
                if kpis is None:
                    kpis = self._compute_session_kpis(logs, elapsed_work_minutes)
                try:
                    self._show_review_screen(review_text, kpis=kpis, closed_tabs=closed_tabs)
                except Exception:
                    logger.exception("Failed to show final review screen")

        t = threading.Thread(target=_review_worker, daemon=True, name="session-review")
        with self._lock:
            self._review_thread = t
        t.start()

    def wait_for_review(self, timeout: float = 5.0) -> None:
        """Wait for background session review thread to finish (for tests)."""
        with self._lock:
            t = getattr(self, "_review_thread", None)
        if t is not None:
            t.join(timeout=timeout)

    def shutdown(self) -> None:
        """Cleanly stop hotkeys, background threads, and close the Tkinter window."""
        try:
            self.hotkey_manager.stop()
        except Exception:
            logger.exception("Error stopping GlobalHotkeyManager")
        self._stop_background_services()
        with self._lock:
            sm = self.state_machine
        if sm is not None:
            try:
                sm.end_session()
            except Exception:
                pass
        if hasattr(self.ui, "close"):
            try:
                self.ui.close()  # type: ignore[attr-defined]
                return
            except Exception:
                logger.exception("Failed to close UI cleanly")
        root = getattr(self.ui, "root", None)
        if root is not None:
            try:
                root.quit()
                root.destroy()
            except Exception:
                pass


def main() -> None:
    """Initialize all Anti-Distractor components and run the Tkinter mainloop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    timings = get_timings()
    api_key = get_gemini_api_key()
    fake_judge = _is_fake_judge_enabled()

    ui = TkinterUI()
    controller = AppController(
        ui=ui,
        timings=timings,
        api_key=api_key,
        fake_judge=fake_judge,
        enable_os_hotkeys=True,
    )
    controller.start()
    ui.run()


if __name__ == "__main__":
    main()
