"""A case's citing cases are its own window's to show, in the side panel
("Citing cases") — not a right-click away in the main window's results, which
opened a separate "Citing: …" window of its own."""

import ast
import re
import unittest
from pathlib import Path

SRC = Path(__file__).with_name("courtlistener_gui.py").read_text(
    encoding="utf-8")
TREE = ast.parse(SRC)


def _class_source(name: str) -> str:
    node = next(n for n in TREE.body
                if isinstance(n, ast.ClassDef) and n.name == name)
    return ast.get_source_segment(SRC, node)


class MainWindowTests(unittest.TestCase):

    def test_the_results_lists_open_no_citing_window(self):
        src = _class_source("CourtListenerGUI")
        for tree in ("self._tree", "self._orders_tree"):
            with self.subTest(tree=tree):
                self.assertIsNone(re.search(
                    re.escape(tree) + r"\.bind\(\s*\"<Button-[23]>\"", src))
        self.assertNotIn("_CitingOpinionsWindow(", src)

    def test_the_side_panel_still_shows_them(self):
        src = _class_source("_ScholarTextWindow")
        self.assertIn('"Citing cases"', src)
        self.assertIn("def _show_citing_cases", src)


if __name__ == "__main__":
    unittest.main()
