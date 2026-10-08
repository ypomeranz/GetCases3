"""Spotlight's result rows, drawn on one canvas.

The rows were built of CustomTkinter widgets — a rounded frame, a badge, five
labels and the frames between them, each its own canvas — and a highlight
re-coloured every widget of every row whenever the pointer crossed any of
them.  The dropdown lagged under the mouse and the arrow keys, and took a
moment to fill.  The rows are now a few items on a single canvas
(_SpotlightList): a highlight touches the two rows it moves between, and the
popup is fitted to its rows by arithmetic rather than a layout pass per row.
"""

import ast
import pathlib
import tkinter as tk
import unittest

import courtlistener_gui as gui
from courtlistener_gui import _SpotlightList

SRC = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text(
    encoding="utf-8")
TREE = ast.parse(SRC)


def _method(name):
    cls = next(n for n in TREE.body
               if isinstance(n, ast.ClassDef) and n.name == "CourtListenerGUI")
    return next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _nested(outer, name):
    for node in ast.walk(_method(outer)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"{outer} has no {name}")


class _ListTest:
    """Run against both looks: CustomTkinter's and plain Tk's."""

    modern = False

    def setUp(self):
        if self.modern and not gui._CTK_AVAILABLE:
            self.skipTest("customtkinter is not installed")
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.root.withdraw()
        self._ctk = gui._CTK_AVAILABLE
        gui._CTK_AVAILABLE = self.modern
        self.spot = _SpotlightList(self.root, width=596)
        self.spot.canvas.pack()

    def tearDown(self):
        gui._CTK_AVAILABLE = self._ctk
        self.root.destroy()

    def add(self, name="Smith v. Jones", detail="1 U.S. 1  ·  Scholar",
            snippet="The question presented"):
        return self.spot.add("SCOTUS", gui._UI["badge"], name, detail,
                             snippet=snippet, year="1990")

    def test_a_row_is_a_few_canvas_items_not_widgets(self):
        for _ in range(3):
            self.add()
        self.assertEqual(self.spot.canvas.winfo_children(), [])
        self.assertEqual(len(self.spot.canvas.find_all()), 3 * 7)

    def test_the_pointer_finds_its_row_and_not_the_gap_between(self):
        for _ in range(3):
            self.add()
        for idx in range(3):
            top, bottom = self.spot.row_bounds(idx)
            self.assertEqual(self.spot.index_at(top), idx)
            self.assertEqual(self.spot.index_at(bottom - 1), idx)
        self.assertEqual(self.spot.index_at(self.spot.row_bounds(1)[0] - 1),
                         -1)
        self.assertEqual(self.spot.index_at(self.spot.content_height() + 5),
                         -1)

    def test_a_highlight_redraws_only_the_rows_it_moves_between(self):
        for _ in range(5):
            self.add()
        redrawn = []
        place = self.spot._place_bg
        self.spot._place_bg = lambda i: (redrawn.append(i), place(i))
        self.spot.select(2)
        self.assertEqual(redrawn, [2])
        del redrawn[:]
        self.spot.select(3)
        self.assertEqual(sorted(redrawn), [2, 3])
        del redrawn[:]
        self.spot.select(3)
        self.assertEqual(redrawn, [])

    def test_a_long_name_is_cut_short_of_the_citation(self):
        r = self.add(name="Smith v. Jones Holding Company of America, "
                          "Incorporated, and Its Several Subsidiaries " * 2)
        c = self.spot.canvas
        items = r["spot"]["items"]
        self.assertTrue(c.itemcget(items["name"], "text").endswith("…"))
        self.assertEqual(c.itemcget(items["detail"], "text"),
                         "1 U.S. 1  ·  Scholar")
        self.root.update_idletasks()
        self.assertLess(c.bbox(items["name"])[2], c.bbox(items["detail"])[0])

    def test_a_snippet_is_kept_to_one_line(self):
        r = self.add()
        self.spot.update(r, snippet="The question\n  presented\tis")
        text = self.spot.canvas.itemcget(r["spot"]["items"]["snippet"], "text")
        self.assertEqual(text, "The question presented is")

    def test_a_badge_is_filled_in_later(self):
        r = self.add()
        self.spot.update(r, court="9th Cir.", color=gui._UI["badge_alt"])
        self.assertEqual(
            self.spot.canvas.itemcget(r["spot"]["items"]["court"], "text"),
            "9th Cir.")

    def test_a_row_taken_down_with_the_list_is_left_be(self):
        r = self.add()
        self.spot.canvas.destroy()
        self.spot.update(r, court="9th Cir.")     # no TclError
        self.assertEqual(self.spot._images, {})


class ModernListTests(_ListTest, unittest.TestCase):
    modern = True


class PlainListTests(_ListTest, unittest.TestCase):
    modern = False


class DropdownTests(unittest.TestCase):
    def test_rows_go_on_the_list(self):
        src = ast.get_source_segment(SRC, _method("_show_spotlight_dropdown"))
        self.assertIn("spot.add(", src)
        self.assertNotIn("_bind_recursive", src)

    def test_a_row_streaming_in_costs_no_layout_pass(self):
        self.assertNotIn("update_idletasks",
                         _nested("_show_spotlight_dropdown", "_resize_to"))
        self.assertNotIn(
            "update_idletasks",
            _nested("_show_spotlight_dropdown", "_scroll_into_view"))


if __name__ == "__main__":
    unittest.main()
