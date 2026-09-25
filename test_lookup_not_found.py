"""A citation typed in by hand that finds nothing says so.

Looking up "42 USC 99999" or "29 CFR 9999.999" used to end with a line on the
status bar — out of sight when the lookup came from Spotlight, whose popup has
closed and whose main window is often hidden — so the app seemed to have given
up.  The U.S. Code and eCFR loaders now tell a section that does not exist
(``SectionNotFound``, a ``LookupError``) from a site that failed to answer, and
the reader is told which it was: in a message box over the dialog they typed
it into, or in Spotlight's toast.

The network is stubbed; the GUI pieces are lifted out of
``courtlistener_gui`` with ``ast`` (importing it needs tkinter).
"""

import ast
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ecfr
import us_code


class _Response:
    def __init__(self, status=200, text="", content=b""):
        self.status_code = status
        self.text = text
        self.content = content or text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Client Error")


def _with_requests(get):
    return patch.dict(sys.modules, {"requests": SimpleNamespace(get=get)})


class UsCodeTests(unittest.TestCase):
    def _load(self, get, section="99999"):
        us_code._cache.pop(("42", section), None)
        with _with_requests(get):
            return us_code.load_section("42", section)

    def test_a_section_the_code_does_not_have(self):
        with self.assertRaises(us_code.SectionNotFound):
            self._load(lambda url, **kw: _Response(404))

    def test_a_page_with_no_section_on_it(self):
        with self.assertRaises(us_code.SectionNotFound):
            self._load(lambda url, **kw: _Response(200, "<html></html>"))

    def test_a_site_that_fails_is_not_a_missing_section(self):
        def down(url, **kw):
            raise ConnectionError("unreachable")
        with self.assertRaises(RuntimeError) as ctx:
            self._load(down)
        self.assertNotIsInstance(ctx.exception, LookupError)


class EcfrTests(unittest.TestCase):
    def _load(self, get):
        ecfr._cache.pop(("29", "9999.999"), None)
        with patch.object(ecfr, "_issue_date", return_value="2026-07-27"):
            with _with_requests(get):
                return ecfr.load_section("29", "9999.999")

    def test_a_section_the_cfr_does_not_have(self):
        with self.assertRaises(ecfr.SectionNotFound):
            self._load(lambda url, **kw: _Response(404))

    def test_a_site_that_fails_is_not_a_missing_section(self):
        def failing(url, **kw):
            return _Response(503)
        with self.assertRaises(RuntimeError) as ctx:
            self._load(failing)
        self.assertNotIsInstance(ctx.exception, LookupError)


SRC = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text(
    encoding="utf-8")
TREE = ast.parse(SRC)


def _module_function(name, ns):
    node = next(n for n in TREE.body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(ast.get_source_segment(SRC, node), ns)
    return ns[name]


def _method(cls, name, ns):
    body = next(n.body for n in TREE.body
                if isinstance(n, ast.ClassDef) and n.name == cls)
    node = next(n for n in body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(ast.get_source_segment(SRC, node), ns)
    return ns[name]


class _Tk:
    TclError = Exception
    Misc = object


class _Parent:
    """Runs ``after(0, …)`` at once, so the worker's result lands inline."""

    def after(self, _ms, fn, *args):
        fn(*args)


class _Inline:
    def __init__(self, target=None, daemon=None):
        self.target = target

    def start(self):
        self.target()


class StatuteFetchTests(unittest.TestCase):
    def _fetch(self, error):
        source = SimpleNamespace(
            spec_label=lambda spec: "42 U.S.C. § 99999",
            load_section=Mock(side_effect=error))
        ns = {"tk": _Tk, "threading": SimpleNamespace(Thread=_Inline),
              "_STATUTE_SOURCES": {"usc": source},
              "_SOURCE_HOST": {"usc": "uscode.house.gov"},
              "_StatuteWindow": Mock()}
        fetch = _module_function("_fetch_statute_window", ns)
        status, missing = [], []
        fetch(_Parent(), "usc", "42:99999:", status.append,
              on_missing=missing.append)
        return status, missing

    def test_a_missing_section_is_reported_as_missing(self):
        status, missing = self._fetch(us_code.SectionNotFound("no such"))
        self.assertEqual(missing,
                         ["No such provision found: 42 U.S.C. § 99999."])
        self.assertEqual(status[-1], missing[0])

    def test_a_failing_source_is_reported_as_a_failure(self):
        _status, missing = self._fetch(RuntimeError("uscode.house.gov: 503"))
        self.assertEqual(
            missing,
            ["Couldn't open 42 U.S.C. § 99999: uscode.house.gov: 503"])


class NoticeTests(unittest.TestCase):
    def _app(self):
        ns = {"tk": _Tk, "messagebox": Mock()}
        notify = _method("CourtListenerGUI", "_notify_lookup_miss", ns)
        app = SimpleNamespace(_status_var=Mock(), _spotlight_notify=Mock())
        return app, notify, ns["messagebox"]

    def test_a_dialog_on_screen_gets_a_message_box(self):
        app, notify, box = self._app()
        dlg = Mock(winfo_exists=lambda: 1, winfo_viewable=lambda: 1)
        notify(app, "No case found for 999 U.S. 1.", dlg)
        box.showinfo.assert_called_once_with(
            "Not Found", "No case found for 999 U.S. 1.", parent=dlg)
        app._spotlight_notify.assert_not_called()

    def test_spotlight_with_nothing_on_screen_gets_the_toast(self):
        app, notify, box = self._app()
        notify(app, "No such provision found: 29 C.F.R. § 9999.999.")
        box.showinfo.assert_not_called()
        app._spotlight_notify.assert_called_once_with(
            "No such provision found: 29 C.F.R. § 9999.999.",
            duration_ms=8000)

    def test_a_dialog_closed_meanwhile_falls_back_to_the_toast(self):
        app, notify, box = self._app()
        gone = Mock(winfo_exists=Mock(side_effect=_Tk.TclError))
        notify(app, "No case found for 999 U.S. 1.", gone)
        app._spotlight_notify.assert_called_once()


class WhereItIsAskedTests(unittest.TestCase):
    """Every hand-typed lookup hands its misses to the notice."""

    def _source(self, name):
        body = next(n.body for n in TREE.body
                    if isinstance(n, ast.ClassDef)
                    and n.name == "CourtListenerGUI")
        node = next(n for n in body
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.get_source_segment(SRC, node)

    def test_spotlight(self):
        src = self._source("_toggle_quick_search_popup")
        self.assertIn("on_missing=self._notify_lookup_miss", src)
        self.assertIn('f"No case found for {label}."', src)

    def test_quick_look_up_and_the_statute_dialog(self):
        for name in ("_show_quick_lookup", "_show_statute_lookup"):
            with self.subTest(name=name):
                self.assertIn("self._notify_lookup_miss(m, dlg)",
                              self._source(name))


if __name__ == "__main__":
    unittest.main()
