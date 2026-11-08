"""A window goes up where it belongs, at its own size — never first as an
empty square at the system's default spot.

Tk puts a new top-level window on screen the next time its pending work is
brought up to date (``update_idletasks``), at whatever size and place it has
by then: 200x200 at the desktop's cascade spot for one with nothing in it yet.
Finding where a document window goes measures the window it was opened from,
and measuring brings that pending work up to date — so a window made before
its place had been found flashed up empty and small, away from where it then
jumped to: a case opened from a search, a slow document's status window, the
bookmarks organizer.  Each now finds its place before it is made.
"""

import os
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ["GETCASES_SKIP_DEPENDENCY_PROMPT"] = "1"

import courtlistener_gui as gui
from courtlistener_gui import CourtListenerGUI


def _display_available() -> bool:
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


@unittest.skipUnless(_display_available(), "needs a display for Tk")
class WindowsOpenInPlaceTests(unittest.TestCase):

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        # The window the reader opened the document from, on screen.
        self.reader = tk.Toplevel(self.root)
        self.reader.geometry("600x500+700+80")
        tk.Frame(self.reader, width=600, height=500).pack()
        self.root.update()
        # Every window given its place, and those already on screen when
        # they got it: the empty square, flashed up before its place was
        # found.
        self.placed: set = set()
        self.shown_before_placed: list = []
        real = tk.Wm.wm_geometry

        def wm_geometry(win, newGeometry=None):
            if (newGeometry is not None and isinstance(win, tk.Toplevel)
                    and str(win) not in self.placed):
                self.placed.add(str(win))
                if win.winfo_ismapped():
                    self.shown_before_placed.append(
                        (win.wm_title(), win.wm_geometry()))
            return real(win, newGeometry)

        for name in ("wm_geometry", "geometry"):
            patcher = mock.patch.object(tk.Wm, name, wm_geometry)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.root.destroy()

    def assertOpensInPlace(self, win):
        self.assertEqual(self.shown_before_placed, [])
        self.assertIn(str(win), self.placed)
        self.root.update_idletasks()
        self.assertTrue(win.winfo_ismapped())      # and it does go up

    def test_a_case_opens_in_its_place(self):
        viewer = gui._FloatingPdfWindow(self.root, None, "", "Roe v. Wade",
                                        anchor=self.reader)
        self.assertOpensInPlace(viewer._win)
        viewer.close()

    def test_and_where_a_slow_documents_window_stood(self):
        viewer = gui._FloatingPdfWindow(self.root, None, "", "Roe v. Wade",
                                        anchor=self.reader,
                                        geometry="700x800+123+45")
        self.assertOpensInPlace(viewer._win)
        viewer.close()

    def test_a_slow_documents_window_opens_in_its_place(self):
        app = SimpleNamespace(root=self.root, _load_watches=[],
                              _load_watch_ended=lambda w: None)
        watch = gui._LoadWatch(app, self.reader, "Roe v. Wade, 410 U.S. 113")
        watch._show()
        self.assertOpensInPlace(watch._win)
        watch.finish()

    def test_the_bookmarks_organizer_opens_in_its_place(self):
        config = Path(tempfile.mkdtemp()) / "config.json"
        patcher = mock.patch("courtlistener_gui._CONFIG_PATH", config)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = object.__new__(CourtListenerGUI)
        app.root = self.root
        app._root_hidden = False
        app._open_case_views = {}
        app._document_windows = []
        app._bookmark_listeners = []
        app._bookmark_organizer = None
        app._folder_icon_image = False      # no artwork to draw here
        app._bookmark_folders = app._load_bookmark_folders()
        app._bookmarks = app._load_bookmarks()
        organizer = gui._BookmarkOrganizer(app, self.reader)
        self.assertOpensInPlace(organizer._win)
        organizer._win.destroy()


if __name__ == "__main__":
    unittest.main()
