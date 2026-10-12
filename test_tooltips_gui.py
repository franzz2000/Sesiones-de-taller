"""Opt-in Tk integration tests: ALARM_GUI_TESTS=1 .venv/bin/python -m unittest test_tooltips_gui.

Uses an in-memory store, no audio and no tray icon. Never changes user alarms.
"""
import os
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

import app


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@unittest.skipUnless(os.environ.get("ALARM_GUI_TESTS") == "1", "Requires a graphical session")
class TooltipGuiTests(unittest.TestCase):
    def setUp(self):
        store = Mock()
        store.alarms = [
            app.Alarm("A", ["Mon"], "08:00:00", "08:01:00", "/nonexistent/first.mp3"),
            app.Alarm("B", ["Tue"], "09:00:00", "09:01:00", "/nonexistent/second.mp3"),
        ]
        store.sounds.return_value = [Path(a.sound) for a in store.alarms]
        for patcher in (
            patch("app.AlarmStore", return_value=store),
            patch("app.AudioPlayer", return_value=None),
            patch.object(app.AlarmApp, "start_tray_icon"),
            patch.object(app.AlarmApp, "start_ntp_sync"),
            patch.object(app.AlarmApp, "scheduler_tick"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root = app.AlarmApp()
        self.root.tray_icon = Mock()  # Exercise close-to-tray, not exit.
        # Integration tests exercise lifecycle, while unit tests verify the 1 s delay.
        self.root.tooltips.DELAY_MS = 0
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.addCleanup(self.root.quit_application)
        self.root.restore_window()
        self.pump()
        self.root.focus_force()
        self.pump()

    def tearDown(self):
        self.assertEqual(self.errors, [], "Unexpected Tk callback exception")

    def pump(self):
        # Only the test harness pumps events, never the tooltip handlers.
        for _ in range(3):
            self.root.update()

    def popup_windows(self):
        return [w for w in descendants(self.root)
                if isinstance(w, tk.Toplevel) and w.wm_overrideredirect()]

    def motion(self, table, row, column):
        box = table.bbox(row, column)
        self.assertTrue(box, f"Cell is not visible: {row}, {column}")
        x, y, width, height = box
        x += min(width // 2, 30)
        y += height // 2
        table.event_generate("<Motion>", x=x, y=y,
                             rootx=table.winfo_rootx() + x, rooty=table.winfo_rooty() + y)

    def library(self):
        self.root.open_sound_library()
        window = self.root.sound_library_window
        self.pump()
        window.focus_force()
        self.pump()
        table = next(w for w in descendants(window) if isinstance(w, ttk.Treeview))
        return window, table

    def assert_one_popup(self):
        self.assertEqual(len(self.popup_windows()), 1)
        self.assertIs(self.popup_windows()[0], self.root.tooltips.window)

    def assert_no_popup(self):
        self.assertEqual(self.popup_windows(), [])
        self.assertIsNone(self.root.tooltips.window)
        self.assertIsNone(self.root.tooltips.key)
        self.assertIsNone(self.root.tooltips._check_id)

    def test_pending_idle_hover_cannot_orphan_main_tooltip(self):
        second = Mock(side_effect=lambda: self.motion(self.root.tree, "1", "#7"))
        self.root.after_idle(second)
        self.motion(self.root.tree, "0", "#7")
        # Regression: update_idletasks in the old handler dispatched this early.
        second.assert_not_called()
        self.assert_one_popup()
        self.pump()
        second.assert_called_once()
        self.assert_one_popup()
        self.root.tree.event_generate("<Leave>")
        self.pump()
        self.assert_no_popup()

    def test_pending_idle_hover_cannot_orphan_library_tooltip(self):
        window, table = self.library()
        second = Mock(side_effect=lambda: self.motion(table, "1", "#2"))
        self.root.after_idle(second)
        self.motion(table, "0", "#1")
        second.assert_not_called()
        self.assert_one_popup()
        self.pump()
        second.assert_called_once()
        self.assert_one_popup()
        table.event_generate("<Leave>")
        self.pump()
        self.assert_no_popup()

    def test_many_library_cells_then_close_button(self):
        window, table = self.library()
        for row, column in (("0", "#1"), ("1", "#2"), ("1", "#1"), ("0", "#2")) * 10:
            self.motion(table, row, column)
            self.pump()
            self.assert_one_popup()
        button = next(w for w in descendants(window)
                      if isinstance(w, ttk.Button) and w.cget("text") == "Cerrar")
        button.invoke()
        self.pump()
        self.assert_no_popup()
        self.assertIsNone(self.root.sound_library_window)

    def test_wm_close_direct_destroy_and_reopen_same_cell(self):
        for close_mode in ("protocol", "destroy"):
            with self.subTest(close_mode=close_mode):
                window, table = self.library()
                self.motion(table, "0", "#2")
                self.pump()
                self.assert_one_popup()
                if close_mode == "protocol":
                    window.tk.call(window.protocol("WM_DELETE_WINDOW"))
                else:
                    window.destroy()
                self.pump()
                self.assert_no_popup()
                self.assertIsNone(self.root.sound_library_window)

    def test_global_cleanup_resets_library_cell_identity(self):
        window, table = self.library()
        self.motion(table, "0", "#1")
        self.pump()
        self.assert_one_popup()
        self.root.hide_all_tooltips()
        self.assert_no_popup()
        self.motion(table, "0", "#1")
        self.pump()
        self.assert_one_popup()

    def test_alert_tooltip_is_removed_on_leave(self):
        self.motion(self.root.tree, "0", "#8")
        self.pump()
        self.assert_one_popup()
        self.root.tree.event_generate("<Leave>")
        self.pump()
        self.assert_no_popup()

    def test_hide_root_with_library_tooltip_and_pending_motion(self):
        window, table = self.library()
        self.motion(table, "0", "#2")
        self.pump()
        self.assert_one_popup()
        self.root.after_idle(lambda: self.motion(table, "1", "#1"))
        self.root.on_close()
        self.pump()
        self.assert_no_popup()
        self.assertEqual(self.root.state(), "withdrawn")

    def test_inactive_and_minimized_owners_cannot_show_tooltip(self):
        self.motion(self.root.tree, "0", "#7")
        self.pump()
        self.assert_one_popup()
        # A separate window takes focus; existing tooltip disappears and a stale
        # Motion over the main table must not create a new one.
        other = tk.Toplevel(self.root)
        other.focus_force()
        self.pump()
        self.motion(self.root.tree, "0", "#7")
        self.assert_no_popup()
        other.destroy()
        self.root.iconify()
        self.pump()
        self.root.show_sound_tooltip(SimpleNamespace(x=1, y=1, x_root=1, y_root=1))
        self.assert_no_popup()


if __name__ == "__main__":
    unittest.main()
