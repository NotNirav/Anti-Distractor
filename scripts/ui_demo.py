"""Manual demo harness for all Anti-Distractor UI screens and popup states using fake data."""

from __future__ import annotations

import argparse
import ctypes
import sys
import threading

from app.models import ClosedTabEntry, SessionKPIs
from app.profile import fallback_goal_profile
from app.settings import AppSettings, default_starter_presets
from app.theme import ThemeType
from app.ui import TkinterUI

SAMPLE_REVIEW = (
    "## Session Review: Study Data Structures & Algorithms\n\n"
    "- **Focus Score:** 91% across 24 evaluations\n"
    "- **Highlights:** Deep uninterrupted blocks on **LeetCode** and **CP-Algorithms**.\n"
    "- **Distractions:** 1 brief YouTube diversion caught during grace period and dismissed.\n"
    "- **Model Breakdown:** Tier 0 (18), Tier 1 gemini-3.5-flash-lite (5), Tier 2 (1)\n"
)

SAMPLE_KPIS = SessionKPIs(
    focus_score_pct=91,
    focused_minutes=22.8,
    distractions_count=1,
    overrides_count=0,
    total_checks=24,
)

SAMPLE_VAULT = [
    ClosedTabEntry(
        title="Trending Reels — Instagram",
        url="https://www.instagram.com/reels/",
        domain="instagram.com",
        closed_at=1700000400.0,
    )
]


def _enable_dpi() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def render_demo_screen(ui: TkinterUI, screen: str, goal: str = "Study Data Structures & Algorithms") -> None:
    """Render a specific demo view cleanly with realistic fake data and zero API calls."""
    profile = fallback_goal_profile(goal)
    presets = default_starter_presets()

    if screen == "onboarding":
        ui.show_onboarding(
            on_save_key=lambda k: (bool(k.strip()), "Configured (••••••••demo)"),
            on_test_key=lambda _k, cb: cb(True, "✓ Connected (demo)"),
            on_continue=lambda: render_demo_screen(ui, "setup", goal),
            on_skip=lambda: render_demo_screen(ui, "setup", goal),
        )
        return

    if screen == "setup":
        ui.show_setup(
            on_start=lambda g, d, m: ui.show_monitoring_hud(g, m, on_end_session=lambda: render_demo_screen(ui, "review", g), duration_minutes=d),
            initial_goal=goal,
            initial_duration=25,
            initial_mode="strict",
            on_edit_rules=lambda g: ui.show_rules_editor(fallback_goal_profile(g or goal), on_save=lambda _p: None),
            presets=presets,
            on_select_preset=lambda _p: None,
            on_save_preset=lambda _n, _g, _d, _m: presets,
            initial_pomodoro=True,
            pomodoro_cycles=4,
            on_open_history=lambda: None,
            on_open_settings=lambda: render_demo_screen(ui, "settings", goal),
            api_key_status=(True, "Configured (••••••••4a9F)"),
            on_open_api_key=lambda: render_demo_screen(ui, "settings", goal),
        )
        return

    if screen == "settings":
        render_demo_screen(ui, "setup", goal)
        ui.show_settings_dialog(
            AppSettings(break_minutes=5, timing_preset="dev", hotkeys_enabled=True),
            (True, "Configured (••••••••4a9F)"),
            on_save_settings=lambda _s: None,
            on_save_api_key=lambda _k: (True, "Configured (••••••••4a9F)"),
            on_test_api_key=lambda _k, cb: cb(True, "✓ Connected (demo)"),
            on_clear_api_key=lambda: (False, "Not configured"),
            focus_api_key=True,
        )
        return

    if screen in ("hud", "hud-offline", "hud-mini", "layer1", "layer2", "strict", "tier0"):
        ui.show_monitoring_hud(
            goal=goal,
            mode="strict",
            on_end_session=lambda: render_demo_screen(ui, "review", goal),
            duration_minutes=25,
            on_toggle_pause=lambda: None,
            on_open_rules=lambda: ui.show_rules_editor(profile, on_save=lambda _p: None),
            on_quit=ui.close,
        )
        ui.update_extension_status(screen != "hud-offline")
        ui.update_ai_calls(4, 1)
        ui.update_hud_status("ON_TASK", "LeetCode — Two Sum (Tier 0 allowlist)")

        if screen == "hud-offline":
            ui.update_hud_status("ON_TASK", "AI unavailable - rules only")
        elif screen == "hud-mini":
            ui.update_hud_status("GRACE_PERIOD", "YouTube tab detected")
            ui.update_grace_countdown(4.0)
            ui.set_mini_mode(True)
        elif screen == "layer1":
            ui.update_hud_status("LAYER_1_ASK", "Checking YouTube video relevance")
            ui.show_layer1_popup(
                goal=goal,
                reason="YouTube video 'Top 10 Coding Setups' looks unrelated to algorithms.",
                on_choice=lambda c: print(f"[UI Demo] Layer 1 choice: {c}"),
                source_label="youtube.com",
            )
        elif screen == "layer2":
            ui.update_hud_status("LAYER_2_ULTIMATUM", "Normal-mode off-task warning")
            ui.show_layer2_warning(
                goal=goal,
                reason="Still browsing social feed after the 5-second grace period.",
            )
        elif screen == "strict":
            ui.update_hud_status("LAYER_2_ULTIMATUM", "Strict banner active (10s)")
            ui.show_strict_overlay(
                goal=goal,
                reason="Instagram Reels closed in Strict Mode.",
                lockout_seconds=10.0,
                on_lockout_done=lambda: print("[UI Demo] Lockout done"),
            )
        elif screen == "tier0":
            ui.update_hud_status("ON_TASK", "AI unavailable - rules only")
            ui.show_tier0_shift_popup(
                "All AI models (Gemini 3.5 Flash-Lite, Gemma 31B, Gemma 4 26B A4B) hit quota limits. "
                "Local domain and app rules remain active."
            )
        return

    if screen == "review-loading":
        ui.show_review("Generating your review...", on_close=ui.close, on_new_session=lambda: render_demo_screen(ui, "setup", goal))
        return

    if screen == "review":
        ui.show_review(
            SAMPLE_REVIEW,
            on_close=ui.close,
            on_new_session=lambda: render_demo_screen(ui, "setup", goal),
            kpis=SAMPLE_KPIS,
            closed_tabs=SAMPLE_VAULT,
        )


def _run_sequence(ui: TkinterUI, goal: str) -> None:
    profile = fallback_goal_profile(goal)
    stop_event = threading.Event()
    paused_state = {"paused": False}

    def on_toggle_pause() -> None:
        paused_state["paused"] = not paused_state["paused"]
        ui.update_pause_status(paused_state["paused"], 300.0 if paused_state["paused"] else 0.0)

    def on_open_rules() -> None:
        ui.show_rules_editor(profile, on_save=lambda _u: ui.update_hud_status("ON_TASK", "Rules updated."))

    def on_end_session() -> None:
        stop_event.set()
        ui.hide_popups_and_overlay()
        ui.show_review(
            SAMPLE_REVIEW,
            on_close=ui.close,
            on_new_session=lambda: render_demo_screen(ui, "setup", goal),
            kpis=SAMPLE_KPIS,
            closed_tabs=SAMPLE_VAULT,
        )

    ui.show_monitoring_hud(
        goal=goal,
        mode="strict",
        on_end_session=on_end_session,
        duration_minutes=25,
        on_toggle_pause=on_toggle_pause,
        on_open_rules=on_open_rules,
        on_quit=ui.close,
    )

    def _bg() -> None:
        ui.update_extension_status(True)
        ui.update_ai_calls(2, 1)
        ui.update_hud_status("ON_TASK", "LeetCode — Two Sum")
        if stop_event.wait(2.0):
            return
        ui.update_hud_status("GRACE_PERIOD", "YouTube tab detected")
        ui.update_grace_countdown(4.0)
        if stop_event.wait(2.0):
            return
        ui.update_hud_status("LAYER_1_ASK", "Asking user about YouTube")
        ui.show_layer1_popup(
            goal=goal,
            reason="YouTube video 'Top 10 Goals' appears unrelated.",
            on_choice=lambda choice: print(f"[UI Demo] Layer 1 choice: {choice}"),
            source_label="youtube.com",
        )
        if stop_event.wait(4.0):
            return
        ui.update_hud_status("LAYER_2_ULTIMATUM", "Normal-mode warning")
        ui.show_layer2_warning(goal=goal, reason="Still off-task on social media after grace period.")
        if stop_event.wait(4.0):
            return
        ui.update_hud_status("LAYER_2_ULTIMATUM", "Strict lockout (10s)")
        ui.show_strict_overlay(
            goal=goal,
            reason="Instagram Reels detected during strict focus.",
            lockout_seconds=10.0,
            on_lockout_done=lambda: ui.update_hud_status("ON_TASK", "Back to work"),
        )

    threading.Thread(target=_bg, daemon=True).start()


def main() -> None:
    _enable_dpi()
    parser = argparse.ArgumentParser(description="Anti-Distractor UI demo harness")
    parser.add_argument(
        "--screen",
        default="all",
        choices=[
            "all",
            "onboarding",
            "setup",
            "hud",
            "hud-offline",
            "hud-mini",
            "layer1",
            "layer2",
            "strict",
            "tier0",
            "review-loading",
            "review",
            "settings",
        ],
        help="Specific screen/state to render (default: interactive 'all' sequence)",
    )
    parser.add_argument("--theme", default="dark", choices=["dark", "light"], help="UI theme")
    args = parser.parse_args()

    theme: ThemeType = "light" if args.theme == "light" else "dark"
    ui = TkinterUI(initial_theme=theme)
    goal = "Study Data Structures & Algorithms"
    if args.screen == "all":
        _run_sequence(ui, goal)
    else:
        render_demo_screen(ui, args.screen, goal)
    ui.run()


if __name__ == "__main__":
    main()
