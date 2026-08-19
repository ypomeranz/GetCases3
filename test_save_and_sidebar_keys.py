"""Ctrl/Cmd+S saves the case; bare "s" opens the panel beside it.

Three rules, one key each:

  * **Ctrl/Cmd+S is the save key.**  It used to open the main window's Quick
    Look Up box, which is not what that key reaches for anywhere else; the
    lookup keeps its place on the Look Up menu and gives the key up.
  * Every window showing a case answers Ctrl/Cmd+S with *its* idea of saving:
    the scan written out where pages are showing, the opinion written out as
    Rich Text where the text is.
  * **Bare "s" opens the side panel** — the case's own details first (Oyez for
    a Supreme Court case), with the docket behind them.

The bindings live inside long ``__init__``/``_build_ui`` bodies that cannot be
exec'd without tkinter, so they are read out of the source with ``ast``.
"""

import ast
import pathlib
import unittest


SRC = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text()
TREE = ast.parse(SRC)


def _method_source(cls: str, name: str) -> str:
    body = next(n.body for n in TREE.body
                if isinstance(n, ast.ClassDef) and n.name == cls)
    for node in body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"{cls} has no {name}")


def _bound_sequences(src: str) -> set:
    """Every event sequence bound as a literal in *src*, including the ones
    looped over as a tuple of sequences."""
    found = set()
    tree = ast.parse(ast.unparse(ast.parse(src)))

    for node in ast.walk(tree):
        # win.bind("<Control-s>", …) / self._win.bind(seq, …)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "bind"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            found.add(node.args[0].value)
        # for seq in ("<Control-s>", "<Command-s>"): …bind(seq, …)
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            binds = any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "bind"
                for inner in ast.walk(node)
            )
            if binds:
                for element in node.iter.elts:
                    if (isinstance(element, ast.Constant)
                            and isinstance(element.value, str)):
                        found.add(element.value)
    return found


class QuickLookupGivesUpTheSaveKeyTests(unittest.TestCase):
    """The main window's Ctrl/Cmd+S is free."""

    def setUp(self):
        self.src = _method_source("CourtListenerGUI", "_build_ui")

    def test_the_key_no_longer_opens_the_lookup_box(self):
        self.assertNotIn('("s", self._show_quick_lookup)', self.src)

    def test_nor_does_the_menu_promise_it(self):
        # The entry is still there — only the accelerator is gone.
        self.assertIn("Quick Look Up (case or statute)…", self.src)
        quick = self.src[self.src.index("Quick Look Up (case or statute)"):]
        entry = quick[:quick.index("command=self._show_quick_lookup")]
        self.assertNotIn("accelerator", entry)

    def test_the_other_two_lookup_keys_are_untouched(self):
        self.assertIn('("l", self._show_statute_lookup)', self.src)
        self.assertIn('("b", self._open_brief)', self.src)

    def test_and_the_main_window_binds_no_save_key_of_its_own(self):
        # Nothing to shadow the case windows, which own this key now.
        self.assertNotIn("<Control-s>", self.src)
        self.assertNotIn("<Command-s>", self.src)


class SaveKeyTests(unittest.TestCase):
    """Every window showing a case saves on Ctrl/Cmd+S."""

    def test_the_reporter_viewer_saves_what_it_is_showing(self):
        src = _method_source("_FloatingPdfWindow", "__init__")
        self.assertLessEqual({"<Control-s>", "<Command-s>"},
                             _bound_sequences(src))
        self.assertIn("self._save()", src)

    def test_and_its_save_follows_the_surface_on_screen(self):
        # Pages → the scan; text → the opinion as Rich Text.
        src = _method_source("_FloatingPdfWindow", "_save")
        self.assertIn("if self.showing_text():", src)
        self.assertIn("self._reader._export_rtf()", src)
        self.assertIn("self._on_save(self._pane)", src)

    def test_an_opinion_in_a_window_of_its_own_saves_as_rich_text(self):
        src = _method_source("_ScholarTextWindow", "_build_ui")
        self.assertLessEqual({"<Control-s>", "<Command-s>"},
                             _bound_sequences(src))
        self.assertIn("self._export_rtf()", src)

    def test_but_a_chromeless_one_leaves_the_key_to_its_viewer(self):
        # One owner per key: the viewer's own save already comes back here.
        src = _method_source("_ScholarTextWindow", "_build_ui")
        guarded = src[src.index("if not self._chromeless:"):]
        self.assertIn("<Control-s>", guarded)

    def test_the_export_menu_names_the_key(self):
        src = _method_source("_ScholarTextWindow", "_post_export_menu")
        self.assertIn("Rich Text (.rtf)", src)
        self.assertIn("{_ACCEL}+S", src)

    def test_a_standalone_scan_saves_the_pdf(self):
        src = _method_source("_PdfWindow", "__init__")
        self.assertLessEqual({"<Control-s>", "<Command-s>"},
                             _bound_sequences(src))
        self.assertIn("self._download()", src)


class SidePanelKeyTests(unittest.TestCase):
    """Bare "s" opens the panel, on the case's own details."""

    def test_the_reporter_viewer_opens_it_over_pages_or_text(self):
        src = _method_source("_FloatingPdfWindow", "__init__")
        self.assertIn("<KeyPress-s>", _bound_sequences(src))
        self.assertIn("self._details_shortcut", src)

    def test_an_opinion_in_a_window_of_its_own_opens_it_too(self):
        src = _method_source("_ScholarTextWindow", "_build_ui")
        self.assertIn("<KeyPress-s>", _bound_sequences(src))
        self.assertIn("self._toggle_details_shortcut", src)

    def test_the_key_yields_to_a_field_being_typed_in(self):
        for cls, name in (("_FloatingPdfWindow", "_details_shortcut"),
                          ("_ScholarTextWindow", "_toggle_details_shortcut")):
            self.assertIn("_widget_accepts_typing", _method_source(cls, name))

    def test_the_panel_opens_on_the_case_s_details(self):
        # _details_mode reads the selector; "case" is the Oyez/CourtListener
        # view, and it is what the selector starts on either way.
        src = _method_source("_ScholarTextWindow", "_details_mode")
        self.assertIn('return "case"', src)
        self.assertIn("self._details_mode_combo.current(0)",
                      _method_source("_ScholarTextWindow", "_details_panel"))

    def test_and_the_docket_is_offered_beside_it(self):
        src = _method_source("_ScholarTextWindow", "_details_panel")
        self.assertIn("for name in self._details_views", src)
        self.assertIn('if name != "Docket" or self._is_scotus', src)
        self.assertIn("reader._details_views = _ScholarTextWindow."
                      "_SCAN_DETAILS_VIEWS",
                      _method_source("_FloatingPdfWindow", "_details_window"))

    def test_the_docket_comes_from_the_court_and_scotusblog(self):
        src = _method_source("_ScholarTextWindow", "_load_scotus_docket")
        self.assertIn("import scotus_docket", src)
        self.assertIn("scotus_docket.fetch_case_docket", src)
        module = pathlib.Path(__file__).with_name("scotus_docket.py").read_text()
        self.assertIn("supremecourt.gov", module)
        self.assertIn("scotusblog.com", module)


if __name__ == "__main__":
    unittest.main()
