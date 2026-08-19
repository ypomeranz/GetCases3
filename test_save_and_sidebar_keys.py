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
    """Every event sequence *src* binds, as it would be bound off macOS.

    Covers the three shapes the module uses: a literal passed straight to
    ``bind``, a literal in a tuple looped over, and the keys handed to
    ``_accel_sequences`` — which is where the Ctrl/Cmd pair now comes from, and
    which yields Control alone on every platform but macOS.
    """
    found = set()
    tree = ast.parse(ast.unparse(ast.parse(src)))

    def binds_inside(node) -> bool:
        return any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "bind"
            for inner in ast.walk(node)
        )

    def literals(node):
        for element in getattr(node, "elts", ()):
            if (isinstance(element, ast.Constant)
                    and isinstance(element.value, str)):
                yield element.value

    for node in ast.walk(tree):
        # win.bind("<Control-s>", …)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "bind"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            found.add(node.args[0].value)
        if not isinstance(node, ast.For) or not binds_inside(node):
            continue
        # for seq in ("<KeyPress-s>", "<Escape>") [+ _accel_sequences("w")]:
        for part in (
            node.iter.left, node.iter.right,
        ) if isinstance(node.iter, ast.BinOp) else (node.iter,):
            found.update(literals(part))
            if (isinstance(part, ast.Call)
                    and isinstance(part.func, ast.Name)
                    and part.func.id == "_accel_sequences"):
                found.update(
                    f"<Control-{a.value}>" for a in part.args
                    if isinstance(a, ast.Constant)
                )
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
        self.assertIn("<Control-s>", _bound_sequences(src))
        self.assertIn("self._save()", src)

    def test_and_its_save_follows_the_surface_on_screen(self):
        # Pages → the scan; text → the opinion as Rich Text.
        src = _method_source("_FloatingPdfWindow", "_save")
        self.assertIn("if self.showing_text():", src)
        self.assertIn("self._reader._export_rtf()", src)
        self.assertIn("self._on_save(self._pane)", src)

    def test_an_opinion_in_a_window_of_its_own_saves_as_rich_text(self):
        src = _method_source("_ScholarTextWindow", "_build_ui")
        self.assertIn("<Control-s>", _bound_sequences(src))
        self.assertIn("self._export_rtf()", src)

    def test_but_a_chromeless_one_leaves_the_key_to_its_viewer(self):
        # One owner per key: the viewer's own save already comes back here.
        src = _method_source("_ScholarTextWindow", "_build_ui")
        guarded = src[src.index("if not self._chromeless:"):]
        self.assertIn('_accel_sequences("s")', guarded)

    def test_the_export_menu_names_the_key(self):
        src = _method_source("_ScholarTextWindow", "_post_export_menu")
        self.assertIn("Rich Text (.rtf)", src)
        self.assertIn("{_ACCEL}+S", src)

    def test_a_standalone_scan_saves_the_pdf(self):
        src = _method_source("_PdfWindow", "__init__")
        self.assertIn("<Control-s>", _bound_sequences(src))
        self.assertIn("self._download()", src)


class MacAcceleratorTests(unittest.TestCase):
    """``<Command-…>`` is bound on macOS and nowhere else.

    Tk aliases ``Command`` to Mod1, and Tk's Windows port sets Mod1 from the
    **Num Lock** toggle, so on a Windows keyboard with Num Lock on every plain
    keypress carries Mod1.  A ``<Command-s>`` binding then matches a bare "s"
    and, being the more specific pattern, beats the window's ``<KeyPress-s>``:
    "s" saved the case instead of opening the panel, and "w" closed the window.
    """

    @staticmethod
    def _accel(platform: str):
        ns = {"sys": type("sys", (), {"platform": platform})}
        exec(_function_source("_accel_sequences"), ns)
        return ns["_accel_sequences"]

    def test_a_mac_gets_both_modifiers(self):
        self.assertEqual(self._accel("darwin")("s"),
                         ("<Control-s>", "<Command-s>"))

    def test_windows_gets_control_alone(self):
        self.assertEqual(self._accel("win32")("s"), ("<Control-s>",))

    def test_and_so_does_linux(self):
        self.assertEqual(self._accel("linux")("s"), ("<Control-s>",))

    def test_several_keys_keep_control_first(self):
        # Control for every key, then the Mac twins — so a window binding a
        # run of them never leaves a gap on the platform that has no Cmd.
        self.assertEqual(
            self._accel("darwin")("plus", "equal"),
            ("<Control-plus>", "<Control-equal>",
             "<Command-plus>", "<Command-equal>"))
        self.assertEqual(self._accel("win32")("plus", "equal"),
                         ("<Control-plus>", "<Control-equal>"))

    def test_no_command_pattern_is_bound_outside_a_mac_guard(self):
        # The whole module, so a new binding cannot reintroduce the fault.
        safe = []
        for node in ast.walk(TREE):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == "_accel_sequences"):
                safe.append((node.lineno, node.end_lineno))
            if isinstance(node, ast.If) and _is_darwin_test(node.test):
                for stmt in node.body:
                    safe.append((stmt.lineno, stmt.end_lineno))
        stray = [
            (node.lineno, node.value)
            for node in ast.walk(TREE)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("<Command-")
            and node.value.endswith(">")       # a pattern, not a prefix test
            and not any(lo <= node.lineno <= hi for lo, hi in safe)
        ]
        self.assertEqual(stray, [], f"unguarded <Command-…> bindings: {stray}")


def _function_source(name: str) -> str:
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"no module-level {name}")


def _is_darwin_test(test) -> bool:
    """``sys.platform == "darwin"``, however it is spelled."""
    return (isinstance(test, ast.Compare)
            and isinstance(test.ops[0], ast.Eq)
            and any(isinstance(c, ast.Constant) and c.value == "darwin"
                    for c in test.comparators))


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

    def test_the_save_key_cannot_swallow_it_off_a_mac(self):
        # The bug this pins: <Command-s> next to <KeyPress-s> on the same
        # window took every bare "s" on a Num Lock keyboard.
        for cls, name in (("_FloatingPdfWindow", "__init__"),
                          ("_ScholarTextWindow", "_build_ui"),
                          ("_PdfWindow", "__init__")):
            src = _method_source(cls, name)
            self.assertNotIn("<Command-s>", src, f"{cls}.{name}")
            self.assertIn("_accel_sequences(\"s\")", src, f"{cls}.{name}")

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
