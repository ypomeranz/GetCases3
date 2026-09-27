"""The spotlight hotkey: one press, one toggle — however busy the app is,
and however many copies of it are running.

The bug: pressing Ctrl+Space to put the spotlight away closed it for less
than a second, then it reopened by itself, over and over.  Two causes, both
on Windows especially: a second GetCases (one left running in the background)
toggling its own popup on every press, out of step with this one's (see
test_single_instance); and presses made while the app was still busy
carrying out the last one, each taken up later as a toggle of its own — the
second reopening what the first had just closed.
"""

import queue
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from courtlistener_gui import CourtListenerGUI


def app():
    win = object.__new__(CourtListenerGUI)
    win.root = Mock()
    win._quick_popup = None
    win._spotlight_toggle_at = 0.0
    win._spotlight_toggle_done = 0.0
    win._hotkey_presses = queue.SimpleQueue()
    win._hotkey_poll_started = False
    win._hotkey_yielded = False
    win._step_back_requested = False
    win._hotkey_listener = Mock()
    win._mac_return_focus = Mock()
    return win


class ListenerThreadTests(unittest.TestCase):
    def test_a_press_is_only_noted_down_on_the_listener_thread(self):
        # A Tk call from the listener thread waits for the Tk thread, and
        # while it waits Windows holds back every keystroke on the machine.
        win = app()
        win._on_global_hotkey()
        win.root.after.assert_not_called()
        self.assertEqual(win._hotkey_presses.qsize(), 1)

    def test_once_a_newer_getcases_has_the_hotkey_presses_are_not_ours(self):
        win = app()
        win._newer_instance_started()
        win._on_global_hotkey()
        self.assertTrue(win._hotkey_presses.empty())


class TakingUpPressesTests(unittest.TestCase):
    def drain(self, win, *presses):
        for t in presses:
            win._hotkey_presses.put(t)
        with patch.object(CourtListenerGUI, "_toggle_quick_search_popup",
                          autospec=True) as toggle:
            win._drain_hotkey_presses()
        return toggle

    def test_presses_that_came_in_together_are_one_request(self):
        win = app()
        now = time.monotonic()
        toggle = self.drain(win, now - 0.6, now - 0.1)
        toggle.assert_called_once_with(win, pressed_at=now - 0.6)

    def test_a_press_made_while_the_last_toggle_was_under_way_is_not_another(
            self):
        # The popup was still closing when the reader pressed again, seeing
        # it still up; taken as a toggle, that press reopened it.
        win = app()
        win._spotlight_toggle_done = time.monotonic()
        toggle = self.drain(win, win._spotlight_toggle_done - 0.2)
        toggle.assert_not_called()

    def test_a_press_after_the_toggle_is_done_counts(self):
        win = app()
        win._spotlight_toggle_done = time.monotonic() - 1
        toggle = self.drain(win, time.monotonic())
        toggle.assert_called_once()
        self.assertGreater(win._spotlight_toggle_done,
                           time.monotonic() - 1)

    def test_the_tk_thread_keeps_looking(self):
        win = app()
        self.drain(win)
        win.root.after.assert_called_once_with(40, win._drain_hotkey_presses)

    def test_closing_then_a_press_made_while_it_closed_leaves_it_closed(self):
        win = app()
        popup = Mock()
        win._quick_popup = popup
        pressed = time.monotonic()
        win._hotkey_presses.put(pressed)
        win._drain_hotkey_presses()                 # the press that closes it
        self.assertIsNone(win._quick_popup)
        popup.destroy.assert_called_once()
        # The same reader, pressing again before the close had finished.
        win._hotkey_presses.put(win._spotlight_toggle_done - 0.01)
        with patch.object(tk, "Toplevel") as toplevel:
            win._drain_hotkey_presses()
        toplevel.assert_not_called()
        self.assertIsNone(win._quick_popup)


class SteppingBackTests(unittest.TestCase):
    def step_back(self, win, on_screen):
        win._newer_instance_started()
        with patch.object(CourtListenerGUI, "_anything_on_screen",
                          return_value=on_screen), \
                patch("courtlistener_gui.sys.platform", "win32"):
            win._drain_hotkey_presses()

    def test_a_copy_running_in_the_background_quits(self):
        win = app()
        listener = win._hotkey_listener
        popup = Mock()
        win._quick_popup = popup
        self.step_back(win, on_screen=False)

        listener.stop.assert_called_once()
        self.assertIsNone(win._hotkey_listener)
        popup.destroy.assert_called_once()
        win.root.destroy.assert_called_once()
        win.root.after.assert_not_called()          # and stops looking

    def test_a_copy_with_windows_open_keeps_them_but_not_the_hotkey(self):
        win = app()
        self.step_back(win, on_screen=True)

        win.root.destroy.assert_not_called()
        self.assertIsNone(win._hotkey_listener)
        # Its listener is gone, so closing its last window quits it rather
        # than hiding it; and a press that reaches it anyway is not taken up.
        win._on_global_hotkey()
        self.assertTrue(win._hotkey_presses.empty())

    def test_windows_on_screen_are_found(self):
        root = Mock(spec=tk.Tk)
        root.state.return_value = "withdrawn"
        case = Mock(spec=tk.Toplevel)
        case.state.return_value = "normal"
        case.winfo_children.return_value = []
        root.winfo_children.return_value = [case]
        win = app()
        win.root = root
        self.assertTrue(win._anything_on_screen())
        case.state.return_value = "withdrawn"
        self.assertFalse(win._anything_on_screen())

    def test_only_a_copy_with_a_hotkey_claims_it(self):
        win = app()
        win._hotkey_listener = None
        self.assertIsNone(win._claim_instance())


if __name__ == "__main__":
    unittest.main()
