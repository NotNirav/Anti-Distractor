"""Manual demo script for T5.3 Tkinter UI popups, strict overlay, rules editor, and HUD updates."""

from __future__ import annotations

import threading

from app.profile import fallback_goal_profile
from app.ui import TkinterUI


def main() -> None:
    ui = TkinterUI()
    goal = "Study Data Structures and Algorithms"
    profile = fallback_goal_profile(goal)
    stop_event = threading.Event()
    paused_state = {"paused": False}

    def on_toggle_pause() -> None:
        paused_state["paused"] = not paused_state["paused"]
        ui.update_pause_status(paused_state["paused"], 300.0 if paused_state["paused"] else 0.0)
        ui.update_hud_status(
            "ON_TASK",
            "Break active — monitoring paused." if paused_state["paused"] else "Monitoring resumed.",
        )

    def on_open_rules() -> None:
        def _save_rules(updated) -> None:
            nonlocal profile
            profile = updated
            print(f"[UI Demo] Rules saved ({len(updated.allowed_domains)} allowed domains)")
            ui.update_hud_status("ON_TASK", "Rules updated.")

        ui.show_rules_editor(profile, on_save=_save_rules)

    def on_end_session() -> None:
        print("[UI Demo] End Session clicked")
        stop_event.set()
        ui.hide_popups_and_overlay()
        ui.show_review(
            "## Demo Session Review\n\n- Manually ended via HUD button.",
            on_close=ui.root.quit,
            on_new_session=lambda: ui.show_setup(
                on_start=lambda g, d, m: print(f"[UI Demo] New session requested: {g} ({d}m, {m})"),
                initial_goal=goal,
                initial_duration=25,
                initial_mode="strict",
            ),
        )

    ui.show_monitoring_hud(
        goal=goal,
        mode="strict",
        on_end_session=on_end_session,
        duration_minutes=25,
        on_toggle_pause=on_toggle_pause,
        on_open_rules=on_open_rules,
        on_quit=ui.root.quit,
    )

    def background_sequence() -> None:
        print("[UI Demo] Background thread started. Watching HUD & popups...")
        ui.update_extension_status(True)
        ui.update_ai_calls(2, 1)
        ui.update_hud_status("ON_TASK", "LeetCode — Two Sum")
        if stop_event.wait(2.0):
            return

        print("[UI Demo] Triggering HUD GRACE_PERIOD...")
        ui.update_hud_status("GRACE_PERIOD", "YouTube tab detected")
        if stop_event.wait(2.0):
            return

        print("[UI Demo] Showing Layer 1 popup...")
        ui.update_hud_status("LAYER_1_ASK", "Asking user about YouTube")
        ui.show_layer1_popup(
            goal=goal,
            reason="YouTube video 'Top 10 Goals' appears unrelated.",
            on_choice=lambda choice: print(f"[UI Demo] Layer 1 choice: {choice}"),
        )
        if stop_event.wait(4.0):
            return

        print("[UI Demo] Showing Layer 2 warning...")
        ui.update_hud_status("LAYER_2_ULTIMATUM", "Normal-mode warning")
        ui.show_layer2_warning(
            goal=goal,
            reason="Still off-task on social media after grace period.",
        )
        if stop_event.wait(4.0):
            return

        print("[UI Demo] Showing Strict multi-monitor overlay (10s lockout; Esc / F12 or button to escape)...")
        ui.update_hud_status("LAYER_2_ULTIMATUM", "Strict lockout (10s)")
        ui.show_strict_overlay(
            goal=goal,
            reason="Instagram Reels detected during strict focus.",
            lockout_seconds=10.0,
            on_lockout_done=lambda: (
                print("[UI Demo] Lockout completed / escaped!"),
                ui.update_hud_status("ON_TASK", "Back to work"),
            ),
        )

    threading.Thread(target=background_sequence, daemon=True).start()
    ui.run()


if __name__ == "__main__":
    main()
