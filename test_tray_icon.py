"""GetCases in the Windows taskbar's notification area.

Started without a terminal — a packaged .exe — GetCases ran in the background
with nothing on screen to show it, and nothing but the hotkey to reach it: no
way back to the main window, no way to quit.  So on Windows it opened its main
window at start instead.  Now the spotlight's scales sit at the right of the
taskbar: a click opens the spotlight, a right-click offers the main window and
Quit, and the app starts out of sight.
"""

import ctypes
import queue
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import tray_icon
from courtlistener_gui import CourtListenerGUI

on_windows = unittest.skipUnless(sys.platform == "win32", "Windows only")


@on_windows
class DrawingTests(unittest.TestCase):
    def ink(self, image):
        px = image.load()
        w, h = image.size
        return [px[x, y] for x in range(w) for y in range(h)
                if px[x, y][3] > 128]

    def test_the_scales_fill_the_square_they_are_drawn_in(self):
        # Fitted by the font's box for the glyph, which takes in the space
        # either side of it, they came out a quarter smaller than they could.
        for size in (16, 20, 24, 32, 40):
            image = tray_icon.draw_scales(size, light_taskbar=True)
            self.assertEqual(image.size, (size, size))
            margin = size // 16
            left, top, right, bottom = image.getbbox()
            self.assertGreaterEqual(min(left, top), margin)
            self.assertLessEqual(max(right, bottom), size - margin)
            self.assertGreaterEqual(max(right - left, bottom - top),
                                    size - 2 * margin - 1)
            self.assertAlmostEqual(left, size - right, delta=1)  # centred
            self.assertAlmostEqual(top, size - bottom, delta=1)

    def test_dark_on_a_light_taskbar_and_white_on_a_dark_one(self):
        light = self.ink(tray_icon.draw_scales(40, light_taskbar=True))
        dark = self.ink(tray_icon.draw_scales(40, light_taskbar=False))
        self.assertTrue(light and dark)
        self.assertTrue(all(max(px[:3]) < 80 for px in light))
        self.assertTrue(all(min(px[:3]) > 200 for px in dark))


class Calls:
    def __init__(self):
        self.count = 0
        self.called = threading.Event()

    def __call__(self):
        self.count += 1
        self.called.set()


@on_windows
class IconTests(unittest.TestCase):
    """A real icon in the taskbar, for as long as each test takes."""

    def setUp(self):
        self.clicks = Calls()
        self.chosen = Calls()
        self.menu = Mock(return_value=[
            tray_icon.MenuItem("Open Spotlight\tCtrl+Space", Mock(),
                               default=True),
            tray_icon.SEPARATOR,
            tray_icon.MenuItem("Quit GetCases", self.chosen),
        ])
        self.icon = tray_icon.TrayIcon("GetCases test", self.clicks, self.menu)
        self.assertTrue(self.icon.start())
        self.addCleanup(self.icon.close)
        user32 = ctypes.WinDLL("user32")
        self.send = user32.SendMessageW
        self.send.restype = ctypes.c_ssize_t
        self.send.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                              ctypes.c_size_t, ctypes.c_ssize_t]

    def taskbar_says(self, event, anchor=0):
        """What Explorer sends the icon's window (NOTIFYICON_VERSION_4)."""
        self.send(self.icon._hwnd, tray_icon._WM_TRAY, anchor,
                  event | (tray_icon._ICON_ID << 16))

    def test_the_icon_is_in_the_taskbar_until_it_is_closed(self):
        self.assertTrue(self.icon.shown)
        self.assertTrue(self.icon._icon_is_ours)   # the scales, not a stand-in
        self.icon.close()
        self.assertFalse(self.icon.shown)
        self.assertFalse(self.icon._thread.is_alive())
        self.icon.close()                           # and again, harmlessly

    def test_a_click_or_the_keyboard_reaches_the_app(self):
        self.taskbar_says(tray_icon._NIN_SELECT)
        self.assertEqual(self.clicks.count, 1)
        self.icon._last_click = float("-inf")
        self.taskbar_says(tray_icon._NIN_KEYSELECT)
        self.assertEqual(self.clicks.count, 2)

    def test_a_double_click_is_one_click(self):
        # Opening the spotlight and closing it again at once.
        self.taskbar_says(tray_icon._NIN_SELECT)
        self.taskbar_says(tray_icon._NIN_SELECT)
        self.assertEqual(self.clicks.count, 1)

    def test_a_choice_from_the_menu_reaches_the_app(self):
        appended = []
        real_append = tray_icon._AppendMenuW

        def append(menu, flags, command, label):
            appended.append((flags, command, label))
            return real_append(menu, flags, command, label)

        # The reader picks the third line, Quit.
        with patch.object(tray_icon, "_AppendMenuW", append), \
                patch.object(tray_icon, "_TrackPopupMenuEx",
                             return_value=3) as track:
            self.taskbar_says(tray_icon._WM_CONTEXTMENU,
                              anchor=(1000 << 16) | 1200)
        self.assertEqual(self.chosen.count, 1)
        self.assertEqual([label for _f, _c, label in appended],
                         ["Open Spotlight\tCtrl+Space", None, "Quit GetCases"])
        # At the icon, where the taskbar said.
        self.assertEqual(track.call_args.args[2:4], (1200, 1000))

    def test_a_new_taskbar_gets_the_icon_again(self):
        self.send(self.icon._hwnd, self.icon._taskbar_created, 0, 0)
        self.assertTrue(self.icon.shown)

    def test_a_menu_line_that_fails_does_not_take_the_icon_down(self):
        self.menu.return_value = [tray_icon.MenuItem("Boom", Mock(
            side_effect=RuntimeError("boom")))]
        with patch.object(tray_icon, "_TrackPopupMenuEx", return_value=1):
            self.taskbar_says(tray_icon._WM_CONTEXTMENU)
        self.taskbar_says(tray_icon._NIN_SELECT)
        self.assertEqual(self.clicks.count, 1)


def app():
    win = object.__new__(CourtListenerGUI)
    win.root = Mock()
    win._quick_popup = None
    win._hotkey_listener = Mock()
    win._spotlight_hotkey = "<ctrl>+<space>"
    win._tray_requests = queue.SimpleQueue()
    return win


class AppTests(unittest.TestCase):
    def drain(self, win, *requests):
        for request in requests:
            win._tray_requests.put((request, 12.5))
        with patch.object(CourtListenerGUI, "_toggle_quick_search_popup",
                          autospec=True) as toggle, \
                patch.object(CourtListenerGUI, "_show_main_window",
                             autospec=True) as show, \
                patch.object(CourtListenerGUI, "_bring_to_front",
                             autospec=True) as front:
            win._drain_tray_requests()
        return toggle, show, front

    def test_a_click_toggles_the_spotlight_like_the_hotkey(self):
        win = app()
        toggle, _show, _front = self.drain(win, "toggle")
        toggle.assert_called_once_with(win, pressed_at=12.5)

    def test_the_menu_opens_the_spotlight_or_brings_it_forward(self):
        win = app()
        toggle, _show, front = self.drain(win, "spotlight")
        toggle.assert_called_once_with(win, pressed_at=12.5)
        win._quick_popup = popup = Mock()
        toggle, _show, front = self.drain(win, "spotlight")
        toggle.assert_not_called()                  # not closing it
        front.assert_called_once_with(win, popup)

    def test_the_menu_opens_the_main_window(self):
        win = app()
        _toggle, show, _front = self.drain(win, "window")
        show.assert_called_once_with(win)
        win.root.after.assert_called_once()         # and keeps looking

    def test_quit_quits(self):
        win = app()
        self.drain(win, "quit")
        win.root.destroy.assert_called_once()
        win.root.after.assert_not_called()

    def test_the_menu_names_the_shortcut(self):
        win = app()
        items = win._tray_menu()
        self.assertEqual(items[0].label, "Open Spotlight\tCtrl+Space")
        self.assertTrue(items[0].default)
        self.assertIn("Ctrl+Space", win._tray_tooltip())
        win._hotkey_listener = None                 # no hotkey to name
        self.assertEqual(win._tray_menu()[0].label, "Open Spotlight")
        self.assertNotIn("Ctrl", win._tray_tooltip())

    def test_choosing_from_the_menu_only_queues_it_for_the_tk_thread(self):
        win = app()
        items = [i for i in win._tray_menu() if i is not tray_icon.SEPARATOR]
        for item in items:
            item.action()
        self.assertEqual(win.root.method_calls, [])
        requests = [win._tray_requests.get_nowait()[0] for _ in items]
        self.assertEqual(requests, ["spotlight", "window", "quit"])

    def test_with_the_icon_up_closing_the_window_only_hides_it(self):
        win = app()
        win._hotkey_listener = None
        win._tray = Mock()
        with patch("courtlistener_gui._stdin_is_tty", return_value=False), \
                patch.object(CourtListenerGUI, "_print_background_help"):
            self.assertTrue(win._can_run_headless())
            win._on_close_window()
        win.root.withdraw.assert_called_once()
        win.root.destroy.assert_not_called()

    def test_a_copy_that_steps_back_takes_its_icon_down(self):
        win = app()
        win._hotkey_yielded = False
        win._tray = tray = Mock()
        with patch.object(CourtListenerGUI, "_anything_on_screen",
                          return_value=True), \
                patch.object(CourtListenerGUI, "_stop_browser_bridge"):
            win._step_back_for_newer_instance()
        tray.close.assert_called_once()
        self.assertIsNone(win._tray)

    def test_no_icon_off_windows(self):
        win = app()
        with patch("courtlistener_gui.sys.platform", "darwin"):
            self.assertIsNone(win._start_tray_icon())


if __name__ == "__main__":
    unittest.main()
