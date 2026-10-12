"""Regression coverage for reentrant popup creation (no display required)."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import AlarmApp, TooltipManager


class FakePopup:
    def __init__(self, owner):
        self.owner = owner
        self.bindings = {}
        self.destroyed = False
        self.withdraw = Mock()
        self.wm_overrideredirect = Mock()
        self.transient = Mock()
        self.geometry = Mock()
        self.deiconify = Mock()

    def bind(self, event, callback, add=None):
        self.bindings[event] = callback

    def destroy(self):
        if self.destroyed:
            raise AssertionError("Popup destroyed twice")
        self.destroyed = True
        callback = self.bindings.get("<Destroy>")
        if callback:
            callback(SimpleNamespace(widget=self))

    def update_idletasks(self):
        raise AssertionError("Do not pump the Tk event loop inside a tooltip callback")

    update = update_idletasks


class TooltipManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = Mock()
        self.root.after.return_value = "timer"
        self.owner = Mock()
        self.owner.winfo_screenwidth.return_value = 1200
        self.owner.winfo_screenheight.return_value = 800
        self.allowed = True
        self.manager = TooltipManager(self.root, lambda owner: self.allowed)
        self.popups = []

        def factory(owner):
            popup = FakePopup(owner)
            self.popups.append(popup)
            return popup

        self.addCleanup(patch.stopall)
        patch("app.tk.Toplevel", side_effect=factory).start()
        self.label = patch("app.tk.Label").start().return_value
        self.label.winfo_reqwidth.return_value = 250
        self.label.winfo_reqheight.return_value = 30

    def show(self, key="file"):
        self.manager.show(self.owner, key, "sonido.mp3", 100, 200)

    def test_one_popup_for_repeated_and_alternating_cells(self):
        self.show()
        first = self.manager.window
        self.show()
        self.assertIs(self.manager.window, first)
        for key in ("name", "file", "name", "file"):
            self.show(key)
            self.assertEqual(sum(not p.destroyed for p in self.popups), 1)
        self.manager.hide()
        self.assertTrue(all(p.destroyed for p in self.popups))

    def test_does_not_dispatch_nested_idle_events(self):
        self.show()
        self.label.update_idletasks.assert_not_called()
        self.label.update.assert_not_called()
        self.manager.window.geometry.assert_called_once_with("250x30+112+212")

    def test_ownership_published_before_window_is_shown(self):
        def check_pack():
            self.assertIs(self.manager.window, self.popups[-1])
            self.assertIs(self.manager.owner, self.owner)
            self.assertEqual(self.manager.key, "file")
        self.label.pack.side_effect = check_pack
        self.show()

    def test_destroy_callback_sees_cleared_references(self):
        self.show()
        popup = self.manager.window
        def check_destroy(event):
            self.assertIsNone(self.manager.window)
            self.assertIsNone(self.manager.owner)
            self.assertIsNone(self.manager.key)
            self.manager.hide()  # Must not try to destroy the same popup again.
        popup.bindings["<Destroy>"] = check_destroy
        self.manager.hide()
        self.assertTrue(popup.destroyed)
        self.root.after_cancel.assert_called_with("timer")

    def test_cleanup_allows_same_cell_to_show_again(self):
        self.show()
        first = self.manager.window
        self.manager.hide()
        self.show()
        self.assertTrue(first.destroyed)
        self.assertIsNot(self.manager.window, first)

    def test_child_unmap_does_not_hide_owners_tooltip(self):
        self.manager.bind_owner(self.owner)
        bindings = {c.args[0]: c.args[1] for c in self.owner.bind.call_args_list}
        self.show()
        bindings["<Unmap>"](SimpleNamespace(widget=Mock()))
        self.assertIsNotNone(self.manager.window)
        bindings["<Unmap>"](SimpleNamespace(widget=self.owner))
        self.assertIsNone(self.manager.window)

    def test_focus_out_and_owner_destroy_hide_tooltip(self):
        self.manager.bind_owner(self.owner)
        bindings = {c.args[0]: c.args[1] for c in self.owner.bind.call_args_list}
        for event in ("<FocusOut>", "<Destroy>"):
            with self.subTest(event=event):
                self.show()
                bindings[event](SimpleNamespace(widget=self.owner))
                self.assertIsNone(self.manager.window)

    def test_hidden_owner_rejects_show_and_clears_existing_tooltip(self):
        self.show()
        self.allowed = False
        self.show("name")
        self.assertIsNone(self.manager.window)
        self.assertEqual(len(self.popups), 1)

    def test_watchdog_handles_missed_visibility_event(self):
        self.show()
        self.allowed = False
        self.manager._check_owner()
        self.assertIsNone(self.manager.window)

    def test_destroyed_popup_clears_cell_and_timer(self):
        self.show()
        self.manager.window.destroy()
        self.assertIsNone(self.manager.key)
        self.root.after_cancel.assert_called_with("timer")
        self.show()
        self.assertEqual(len(self.popups), 2)

    def test_hidden_during_construction_never_maps_popup(self):
        self.label.pack.side_effect = lambda: setattr(self, "allowed", False)
        self.show()
        self.assertTrue(self.popups[0].destroyed)
        self.popups[0].deiconify.assert_not_called()


class TooltipVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.app = Mock()
        self.app.running = True
        self.app.window_hidden = False
        self.app.state.return_value = "normal"
        self.app.winfo_viewable.return_value = True
        self.app.winfo_exists.return_value = True
        self.app.focus_displayof.return_value.winfo_toplevel.return_value = self.app
        self.app.grab_current.return_value = None

    def test_visible_focused_owner_is_allowed(self):
        self.assertTrue(AlarmApp.can_show_tooltip(self.app, self.app))

    def test_hidden_iconic_inactive_and_blocked_owner_are_rejected(self):
        for state in ("withdrawn", "iconic"):
            self.app.state.return_value = state
            self.assertFalse(AlarmApp.can_show_tooltip(self.app, self.app))
        self.app.state.return_value = "normal"
        self.app.focus_displayof.return_value = None
        self.assertFalse(AlarmApp.can_show_tooltip(self.app, self.app))
        self.app.focus_displayof.return_value = Mock()
        self.app.focus_displayof.return_value.winfo_toplevel.return_value = self.app
        self.app.grab_current.return_value = Mock()
        self.assertFalse(AlarmApp.can_show_tooltip(self.app, self.app))


if __name__ == "__main__":
    unittest.main()
