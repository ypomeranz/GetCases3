"""Find on a long opinion, without the window freezing.

On a slower computer Ctrl-F lagged on long opinions:

  * In the text view, every match was asked of Tk one at a time and then
    highlighted on its own, so the first letter typed into the find bar
    froze the window — 13 seconds for "t" (57,692 matches) in Dred Scott,
    636,000 characters.  Now the text is searched in one call, and the
    matches are highlighted from the view on, a batch at a time, the reader
    typing on in between.
  * In the scan, every page's text was gone through again at every key
    typed.  Now the search waits for typing to pause, and the pages' text is
    gone through once for the document.

Either way, Enter pressed before the pause finds what was typed, not what
was found for the word before it.
"""

import os
import types
import unittest
from unittest import mock

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:
    import tkinter as tk
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None


def _display_available() -> bool:
    if gui is None:
        return False
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


TEXT = ("The Court held that the statute applies.\n"
        "Justice Story dissented from the court's reading.\n"
        "See 42 U.S.C. § 1983; the court below agreed.\n")


@unittest.skipUnless(_display_available(), "needs a display for Tk")
class TextFinderTests(unittest.TestCase):

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        frame = tk.Frame(self.root)
        frame.pack()
        self.txt = tk.Text(frame, height=3)
        self.txt.pack()
        self.txt.insert("1.0", TEXT)
        self.finder = gui._TextFinder(self.root, self.txt, frame,
                                      bind_keys=False)
        self.finder._visible = True     # as open() leaves it

    def find(self, query):
        self.finder._var.set(query)
        self.finder.refresh()
        while self.finder._tagging is not None:
            self.root.update()
        return self.finder._matches

    def highlighted(self, tag="findmatch"):
        r = self.txt.tag_ranges(tag)
        return [(str(r[i]), str(r[i + 1])) for i in range(0, len(r), 2)]

    def test_every_match_is_found_whatever_its_case(self):
        self.assertEqual(self.find("court"),
                         [("1.4", "1.9"), ("2.33", "2.38"), ("3.26", "3.31")])
        self.assertEqual(self.finder._count_var.get(), "1 of 3")

    def test_a_space_finds_a_non_breaking_one(self):
        self.assertEqual(self.find("U.S.C. § 1983"), [("3.7", "3.20")])

    def test_none_is_said_so(self):
        self.assertEqual(self.find("certiorari"), [])
        self.assertEqual(self.finder._count_var.get(), "no matches")
        self.assertEqual(self.highlighted(), [])

    def test_the_text_is_searched_in_one_call(self):
        # Not one call a match: that is what froze the window.
        with mock.patch.object(self.txt, "search",
                               side_effect=AssertionError("one at a time")):
            self.assertEqual(len(self.find("the")), 4)

    def test_it_finds_what_searching_one_match_at_a_time_found(self):
        # The old way, as the oracle: the same matches, in the same places.
        self.txt.insert("end", TEXT * 40 + "THE COURT, the\u00a0court.\n")

        def one_at_a_time(needle):
            out, idx, n = [], "1.0", tk.IntVar(self.root)
            spaced = " " in needle or "\u00a0" in needle
            if spaced:
                needle = gui._find_pattern(needle)
            while True:
                idx = self.txt.search(needle, idx, stopindex="end",
                                      nocase=True, count=n, regexp=spaced)
                if not idx or not n.get():
                    return out
                end = self.txt.index(f"{idx}+{n.get()}c")
                out.append((idx, end))
                idx = end

        for query in ("t", "the", "court", "the court", "U.S.C. \u00a7",
                      "court's", "(", "1983;"):
            with self.subTest(query=query):
                self.assertEqual(self.find(query), one_at_a_time(query))

    def test_every_match_is_highlighted_a_batch_at_a_time(self):
        with mock.patch.object(gui._TextFinder, "_TAG_BATCH", 2):
            self.finder._var.set("the")
            self.finder.refresh()
            # The first batch at once; the rest as the window has a moment.
            self.assertEqual(len(self.highlighted()), 2)
            self.assertIsNotNone(self.finder._tagging)
            while self.finder._tagging is not None:
                self.root.update()
        self.assertEqual(sorted(self.highlighted()),
                         sorted(self.finder._matches))

    def test_from_the_view_on_first(self):
        self.txt.yview("2.0")
        with mock.patch.object(gui._TextFinder, "_TAG_BATCH", 1):
            self.finder._var.set("court")
            self.finder.refresh()
            self.assertEqual(self.highlighted(), [("2.33", "2.38")])
        self.assertEqual(self.highlighted("findcur"), [("2.33", "2.38")])
        self.assertEqual(self.finder._count_var.get(), "2 of 3")

    def test_a_new_search_leaves_nothing_of_the_old_one(self):
        with mock.patch.object(gui._TextFinder, "_TAG_BATCH", 1):
            self.finder._var.set("the")
            self.finder.refresh()           # batches still to come
            self.finder._var.set("story")
            self.finder.refresh()
            while self.finder._tagging is not None:
                self.root.update()
        self.assertEqual(self.highlighted(), [("2.8", "2.13")])

    def test_enter_before_the_pause_finds_what_was_typed(self):
        self.find("court")
        self.finder._var.set("story")
        self.finder._pending = self.root.after(10_000, self.finder.refresh)
        self.finder.step(+1)
        self.assertIsNone(self.finder._pending)
        self.assertEqual(self.finder._matches, [("2.8", "2.13")])
        self.assertEqual(self.finder._count_var.get(), "1 of 1")

    def test_closing_stops_the_highlighting(self):
        with mock.patch.object(gui._TextFinder, "_TAG_BATCH", 1):
            self.finder._var.set("the")
            self.finder.refresh()
            self.finder._bar.pack = mock.Mock()
            self.finder.close()
        self.assertIsNone(self.finder._tagging)
        self.root.update()
        self.assertEqual(self.highlighted(), [])


@unittest.skipUnless(gui is not None, "needs the app")
class ScanFindTests(unittest.TestCase):
    """The PDF pane's find: on the scan's text layer."""

    def setUp(self):
        pane = object.__new__(gui._PdfPane)
        box = (0.0, 0.0, 1.0, 1.0)
        pane._search_pages = [[(ch, box) for ch in page]
                              for page in ("The Court held that", "the  court\nbelow")]
        pane._search_matches, pane._search_cur = [], -1
        pane._find_bar = object()
        pane._find_var = types.SimpleNamespace(get=lambda: self.query)
        pane._find_after = None
        pane.after = mock.Mock(return_value="after#1")
        pane.after_cancel = mock.Mock()
        for name in ("_update_find_count", "_redraw_search",
                     "_scroll_to_match"):
            setattr(pane, name, mock.Mock())
        self.pane = pane
        self.query = "court"

    def key(self, keysym="t"):
        self.pane._on_find_key(types.SimpleNamespace(keysym=keysym))

    def test_a_key_waits_for_typing_to_pause(self):
        self.key()
        self.assertEqual(self.pane._search_matches, [])
        delay, run = self.pane.after.call_args.args
        self.assertEqual(delay, gui._PdfPane._FIND_DELAY_MS)
        run()
        self.assertEqual([pi for pi, _r in self.pane._search_matches], [0, 1])

    def test_each_key_puts_the_search_off_again(self):
        self.key()
        self.key()
        self.pane.after_cancel.assert_called_once_with("after#1")

    def test_enter_before_the_pause_searches_at_once(self):
        self.key()
        self.pane._find_step(1)
        self.assertEqual(len(self.pane._search_matches), 2)
        self.assertEqual(self.pane._search_cur, 0)
        self.assertIsNone(self.pane._find_after)

    def test_closing_drops_the_search_waiting(self):
        self.key()
        self.pane._find_showing = True
        self.pane._find_bar = types.SimpleNamespace(pack_forget=mock.Mock())
        self.pane._canvas = mock.Mock()
        self.pane._close_find()
        self.pane.after_cancel.assert_called_with("after#1")
        self.assertIsNone(self.pane._find_after)

    def test_the_pages_text_is_gone_through_once(self):
        first = self.pane._search_text()
        self.assertEqual(first[1][0], "the court below")   # white space folded
        self.assertIs(self.pane._search_text(), first)
        # Another document's pages are read afresh.
        self.pane._search_pages = [[("x", (0, 0, 1, 1))]]
        self.assertEqual(self.pane._search_text()[0][0], "x")

    def test_white_space_in_the_query_matches_any_run_of_it(self):
        self.query = "the court"
        self.pane._run_find()
        self.assertEqual(len(self.pane._search_matches), 2)


if __name__ == "__main__":
    unittest.main()
