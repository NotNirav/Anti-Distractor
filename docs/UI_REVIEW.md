# Anti-Distractor UI Review

## P0 (Could break a live demo)
- [S] app/ui.py:show_strict_overlay - Binding `<Control-Shift-Escape>` conflicts with Windows Task Manager's global OS shortcut and fails if the overlay lacks focus - Change the emergency exit shortcut to `<Escape>` or `<F12>` and bind it reliably when the overlay is shown. (demo-safe)
- [M] app/main.py:AppController.shutdown - Calling `root.quit()` and `root.destroy()` directly can crash if invoked off the main thread and leaves `TkinterUI._pump`'s `after()` callback uncancelled - Dispatch UI teardown via `self.ui.dispatch` and store/cancel `_pump`'s `after` ID before destroying `root`. (risky)
- [S] app/ui.py:show_tier0_shift_popup - Popups created here are not tracked in `_active_popups`, so `hide_popups_and_overlay()` never closes them and repeated calls stack windows - Track and deduplicate the Tier-0 notice in `_active_popups` and close it cleanly on `WM_DELETE_WINDOW` or session end. (demo-safe)
- [M] app/ui.py:show_monitoring_hud - Main window morphs into a `-topmost` HUD instead of minimizing, and `-topmost` is not explicitly cleared when switching to setup or review - Reset `root.attributes("-topmost", False)` in `show_setup` and `show_review`, or host the HUD in its own `Toplevel` while minimizing `root`. (risky)
- [S] app/main.py:main - No Windows DPI awareness is configured before creating `tk.Tk()`, causing blurry text and mis-scaled popup/overlay geometry on high-DPI displays - Call `ctypes.windll.shcore.SetProcessDpiAwareness(1)` (guarded for Windows) before instantiating `TkinterUI`. (demo-safe)

## P1 (Noticeable bugs or rough edges)
- [M] app/ui.py:show_layer1_popup - Creating `tk.Toplevel` with `-topmost True` steals keyboard focus from the user's active app on Windows - Apply Windows `WS_EX_NOACTIVATE` (or withdraw/show without focus) so Layer 1/2 popups appear topmost without interrupting typing. (risky)
- [S] app/ui.py:show_review - End-of-session review text containing Markdown (`##` headers, `**bold**`, `-` bullets) is displayed as raw literal characters - Parse basic Markdown headings, bold spans, and bullet lists into Tkinter `Text` tags for clean readability. (demo-safe)
- [S] app/ui.py:show_monitoring_hud - Drag bindings are missing on `_hud_mode_label`, `_hud_state_label`, `_hud_summary_label`, `bottom_row`, and `_hud_ai_calls_label`, so clicking most of the HUD does not drag it - Bind `<Button-1>` and `<B1-Motion>` to all non-button HUD child widgets. (demo-safe)
- [S] app/ui.py:update_hud_status - `"AI unavailable"` status only bolds the text in the default color instead of high-contrast red, making rule-only fallback easy to miss - Set `foreground="#C62828"` when `"AI unavailable"` appears in `summary` and restore the default foreground otherwise. (demo-safe)
- [S] app/ui.py:show_layer1_popup - Popup positioning uses `winfo_screenwidth()`/`winfo_screenheight()`, which only reflect the primary monitor on multi-monitor Windows setups - Position popups relative to the active or HUD monitor bounds instead of primary-only screen dimensions. (demo-safe)
- [S] app/ui.py:show_strict_overlay - Fullscreen overlay uses primary-monitor `winfo_screenwidth()xwinfo_screenheight()+0+0`, leaving secondary monitors uncovered - Cover virtual screen bounds across monitors or switch to the compact topmost banner design. (risky)
- [S] app/ui.py:TkinterUI.drain_queue - Unbounded `while not self._queue.empty()` loop can starve the Tk event loop if background threads flood updates - Cap callbacks drained per `_pump` tick (e.g., max 20) and reschedule immediately if more remain. (demo-safe)

## P2 (Polish)
- [S] app/ui.py:show_setup - Pressing Enter in the goal or duration `Entry` does not start the session - Bind `<Return>` on `goal_entry` and `dur_entry` to `_handle_start`. (demo-safe)
- [S] app/ui.py:show_setup - Missing goal or invalid duration triggers a modal `messagebox.showwarning` instead of inline feedback - Show validation errors inline in `status_lbl` with red text and focus the invalid `Entry`. (demo-safe)
- [S] app/ui.py:update_hud_status - Activity summary is hard-truncated at 50 characters even though `wraplength=270` is configured - Increase the character limit or let `wraplength` wrap up to two lines cleanly. (demo-safe)
- [S] app/ui.py:show_layer1_popup - No keyboard shortcuts (`Escape`/`Return`) exist on Layer 1 or Layer 2 popups - Bind `<Escape>` to dismiss/back-to-work when focused and highlight the primary button. (demo-safe)
- [S] app/ui.py:show_monitoring_hud - Mixing `tk.Frame`/`tk.Label` and `ttk` widgets creates inconsistent borders and padding across Windows themes - Standardize widget styling via `ttk.Style` or consistent `tk` theme colors. (demo-safe)

## Nice-to-have
- [S] app/ui.py:show_review - Review screen lacks a one-click Copy to Clipboard button for saving session summaries - Add a "Copy Summary" `ttk.Button` that writes `review_text` via `clipboard_clear()` and `clipboard_append()`. (demo-safe)
- [S] app/ui.py:show_monitoring_hud - HUD does not show remaining session time or elapsed progress - Add a lightweight 1-second Tk timer label showing `MM:SS` remaining beside the AI call counter. (demo-safe)
- [S] app/ui.py:show_setup - Duration field requires manual typing for common focus intervals - Add quick-select preset buttons (15m, 25m, 45m, 60m) that populate `dur_var`. (demo-safe)
- [S] app/ui.py:show_layer2_warning - The 12-second auto-close happens silently with no visible countdown - Update the dismiss button or footer with `"Auto-closing in Xs"` so the user sees the timer. (demo-safe)

## Top 5 to do first
1. Replace `<Control-Shift-Escape>` with `<Escape>`/`<F12>` in `app/ui.py:show_strict_overlay` so Windows Task Manager doesn't intercept the emergency exit.
2. Enable Windows DPI awareness in `app/main.py:main` (`SetProcessDpiAwareness(1)`) so all text and popups render crisply on high-DPI screens.
3. Track and deduplicate `show_tier0_shift_popup` in `_active_popups` and clear `-topmost` in `show_setup`/`show_review` so windows never stack or stay stuck on top.
4. Add lightweight Markdown rendering (headers, bold, bullets) and a Copy button in `app/ui.py:show_review` so the end-of-session review looks polished.
5. Bind `<Return>` to start on `app/ui.py:show_setup`, bind drag events to all non-button HUD labels, and color `"AI unavailable"` red in `update_hud_status`.

## Resolution & Upgrade Summary
All items above (P0, P1, P2, Nice-to-have, and Top 5) have been resolved in the redesigned `app/ui.py`, `app/main.py`, `app/window_watcher.py`, `app/judge.py`, `app/server.py`, `app/event_engine.py`, and `app/tray.py`:
- **P0 & Window State Fixes:** Configured DPI awareness (`SetProcessDpiAwareness(1)`), dynamic DPI-aware `_fit_window()` sizing, clean window-state resets (`_reset_window_state`) across screens, HUD non-maximizable `-topmost` toggle (`Pinned`/`Unpinned`), `<Escape>`/`<F12>` emergency exit shortcuts, deduplicated `_tier0_popup` tracking, and thread-safe `TkinterUI.close()`.
- **P1/P2/Polish Fixes:** Bounded `_pump` queue draining, Markdown formatting with `Copy Summary` and `New Session` on Review, drag bindings across HUD widgets, high-contrast `"AI unavailable"` status styling, self-PID filtering in `WindowWatcher` and Tier 0, Setup `15m/25m/45m/60m` presets + `<Return>` submit + inline validation, and live 12s Layer 2 auto-close countdown.
- **Feature Upgrades:** Added HUD extension liveness dot (`Ext: Live` / `Ext: Off` via `TabSnapshotStore.is_extension_connected`), 5-minute intentional break (`Pause 5m` / `Resume` with deadline extension and auto-resume), pre-session & live tabbed Domain/App Rules Editor (`show_rules_editor`), Windows system tray minimization via `pystray` (`app/tray.py`), virtual-desktop multi-monitor strict overlay (`GetSystemMetrics(76/77/78/79)`), and persisted Light/Dark theme toggling (`last_session.json`).
