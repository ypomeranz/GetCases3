"""Reporter View: the app answers with the report itself.

Opening a document means: a case opens as its scan, in the small floating
viewer, with T turning it into the opinion text; a case with no scan anywhere
opens in that same window at that same size, on the text side, rather than as
a different kind of window.  A source printed only as pages — Statutes at
Large, the English Reports — opens there too, without the switch, because
there is nothing to switch to.  And a citation followed out of a search
result, a brief or the text side is looked up as a scan first, exactly as one
clicked on a page already was.

As elsewhere in this suite the methods are lifted out of
``courtlistener_gui`` with ``ast`` (importing it needs tkinter, absent on a
headless run) and driven against stubs.
"""

import ast
import pathlib
import re
import sys
import typing
import unittest


SRC = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text()
TREE = ast.parse(SRC)


class _Tk:
    TclError = Exception
    Menu = Misc = Frame = Toplevel = object


def _base_ns(extra=None) -> dict:
    ns = {"tk": _Tk, "sys": sys, "re": re, "Optional": typing.Optional,
          "threading": _Threading, "print": lambda *a, **k: None}
    ns.update(extra or {})
    return ns


class _Threading:
    """Runs a worker inline, so a test sees the whole path in one call."""

    started: list = []

    class Thread:
        def __init__(self, target=None, daemon=False, **kw):
            self._target = target

        def start(self):
            _Threading.started.append(self._target)
            if self._target is not None:
                self._target()


def _load(cls: str, names, extra=None) -> dict:
    body = next(n.body for n in TREE.body
                if isinstance(n, ast.ClassDef) and n.name == cls)
    found = {n.name: ast.get_source_segment(SRC, n) for n in body
             if isinstance(n, ast.FunctionDef) and n.name in names}
    missing = [n for n in names if n not in found]
    if missing:
        raise AssertionError(f"not found on {cls}: {missing}")
    ns = _base_ns(extra)
    for name in names:
        exec(found[name], ns)
    return ns


def _load_functions(names, extra=None) -> dict:
    found = {n.name: ast.get_source_segment(SRC, n) for n in TREE.body
             if isinstance(n, ast.FunctionDef) and n.name in names}
    missing = [n for n in names if n not in found]
    if missing:
        raise AssertionError(f"module-level functions not found: {missing}")
    ns = _base_ns(extra)
    for name in names:
        exec(found[name], ns)
    return ns


def _source_of(cls: str, name: str) -> str:
    body = next(n.body for n in TREE.body
                if isinstance(n, ast.ClassDef) and n.name == cls)
    for node in body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"{cls} has no {name}")


def _class_value(cls: str, name: str):
    """A class-level constant, evaluated from the source, so a test measures
    against the value the app itself uses."""
    body = next(n.body for n in TREE.body
                if isinstance(n, ast.ClassDef) and n.name == cls)
    for node in body:
        for target in getattr(node, "targets", ()):
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError(f"{cls} has no {name}")


def _module_value(name: str):
    """A module-level constant, evaluated from the source."""
    for node in TREE.body:
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return eval(ast.get_source_segment(SRC, node.value),  # noqa: S307
                        {"frozenset": frozenset})
    raise AssertionError(f"module-level constant not found: {name}")


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Widget:
    def __init__(self, top=None):
        self._top = top
        self.destroyed = False
        self.shown = False

    def winfo_exists(self):
        return not self.destroyed

    def winfo_toplevel(self):
        return self._top if self._top is not None else self

    def destroy(self):
        self.destroyed = True

    def deiconify(self):
        self.shown = True

    def after(self, _ms, fn=None, *args):
        if fn is not None:
            fn(*args)


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


# ---------------------------------------------------------------------------
# PDF-first: a citation is looked up as a scan before it is looked up as text
# ---------------------------------------------------------------------------

APP_NAMES = ["reporter_open_case", "_safe_root_status"]

APP_NS = _load(
    "CourtListenerGUI", APP_NAMES,
    {"_pick_citation": lambda cites: (cites[0] if cites else "")},
)


class _App:
    def __init__(self, opens=True):
        self.root = _Widget()
        self.asked: list = []
        self.opens = opens
        self._status_var = _Var()
        for name in APP_NAMES:
            setattr(self, name, APP_NS[name].__get__(self))

    def open_cited_case_pdf(self, parent, action, snippet="",
                            status=lambda _s: None, fallback=None, name=""):
        self.asked.append((parent, action, snippet, fallback))
        self.names = getattr(self, "names", []) + [name]
        return self.opens


class ReporterOpenCaseTests(unittest.TestCase):
    """What a search result means by "open this case"."""

    def test_a_result_is_opened_by_its_citation(self):
        app = _App()
        item = {"citation": ["410 U.S. 113"], "caseName": "Roe v. Wade"}
        self.assertTrue(app.reporter_open_case(app.root, item))
        _parent, action, snippet, _fb = app.asked[0]
        self.assertEqual(action, ("cite", "410 U.S. 113"))
        self.assertEqual(snippet, "Roe v. Wade")

    def test_the_result_s_caption_goes_as_the_case_s_name(self):
        # A bare caption is no "Name, cite" snippet to read a name out of, so
        # it is handed over as the name itself — what picks the case when
        # another begins on the same reporter page.
        app = _App()
        item = {"citation": ["145 S. Ct. 2658"],
                "caseName": "NetChoice, LLC v. Fitch"}
        app.reporter_open_case(app.root, item)
        self.assertEqual(app.names, ["NetChoice, LLC v. Fitch"])

    def test_an_explicit_citation_wins_over_the_item_s(self):
        app = _App()
        app.reporter_open_case(app.root, {"citation": ["1 U.S. 1"]},
                               cite="410 U.S. 113", name="Roe")
        self.assertEqual(app.asked[0][1], ("cite", "410 U.S. 113"))

    def test_a_result_with_no_citation_is_left_to_the_text_path(self):
        app = _App()
        self.assertFalse(app.reporter_open_case(app.root, {"caseName": "X"}))
        self.assertEqual(app.asked, [])

    def test_no_parent_means_the_main_window(self):
        app = _App()
        app.reporter_open_case(None, cite="410 U.S. 113")
        self.assertIs(app.asked[0][0], app.root)

    def test_the_caller_s_own_way_of_opening_it_is_the_fallback(self):
        app = _App()
        marker = []
        app.reporter_open_case(app.root, cite="1 U.S. 1",
                               fallback=lambda: marker.append(1))
        app.asked[0][3]()
        self.assertEqual(marker, [1])

    def test_a_lookup_that_cannot_start_leaves_the_caller_to_it(self):
        app = _App(opens=False)
        self.assertFalse(app.reporter_open_case(app.root, cite="1 U.S. 1"))


class WhereItIsAskedTests(unittest.TestCase):
    """Every way of opening a case asks the same question first."""

    def test_the_main_window_s_search_results_do(self):
        src = _source_of("CourtListenerGUI", "_fetch_scholar_text")
        self.assertIn("self.reporter_open_case(self.root, item", src)
        self.assertIn("fallback=ordinary", src)

    def test_and_so_does_a_brief_s_citation(self):
        src = next(ast.get_source_segment(SRC, n) for n in TREE.body
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "_follow_brief_action")
        self.assertIn("open_cited_case_pdf", src)
        self.assertIn("_following_as_text", src)

    def test_and_a_link_clicked_in_the_opinion_text(self):
        src = _source_of("_ScholarTextWindow", "_follow_link")
        self.assertIn("self._app.open_cited_case_pdf(", src)
        self.assertIn("fallback=as_text", src)

    def test_but_federal_appendix_keeps_its_own_scan_route(self):
        src = _source_of("_ScholarTextWindow", "_follow_link")
        self.assertIn("not _FED_APPX_RE.search(cite)", src)

    def test_and_a_citation_re_followed_as_text_is_not_asked_twice(self):
        src = _source_of("_ScholarTextWindow", "_follow_link")
        self.assertIn("not self._following_as_text", src)
        self.assertIn("self._following_as_text = True", src)


# ---------------------------------------------------------------------------
# A case with no scan: the same window, on the text side
# ---------------------------------------------------------------------------

HOST_NS = _load(
    "CourtListenerGUI", ["new_case_view_host", "_reporter_text_host",
                         "_take_text_watch"],
    {"_FloatingPdfWindow": lambda *a, **kw: _StubViewer(*a, **kw),
     "_toplevel_path": _load_functions(["_toplevel_path"])["_toplevel_path"]},
)


class _StubViewer:
    made: list = []

    def __init__(self, parent, data, url, title, **kw):  # noqa: D107
        self.parent, self.data, self.kw = parent, data, kw
        self.host = object()
        _StubViewer.made.append(self)

    def text_host(self, standalone=True):
        self.standalone = standalone
        return self.host


class _HostApp:
    def __init__(self):
        self.root = _Widget()
        self._cited_pdf_windows: set = set()
        self.secondary: list = []
        self._load_watches: list = []
        for name in ("new_case_view_host", "_reporter_text_host",
                     "_take_text_watch"):
            setattr(self, name, HOST_NS[name].__get__(self))

    def new_secondary_view_host(self, parent):
        self.secondary.append(parent)
        return "ordinary host"

    def _cited_pdf_window_closed(self, window):
        self._cited_pdf_windows.discard(window)


class TextOnlyCaseTests(unittest.TestCase):
    def setUp(self):
        _StubViewer.made.clear()

    def test_it_opens_in_a_viewer_of_its_own(self):
        app = _HostApp()
        host = app.new_case_view_host(app.root)
        self.assertEqual(len(_StubViewer.made), 1)
        self.assertIs(host, _StubViewer.made[0].host)

    def test_with_no_scan_in_it(self):
        app = _HostApp()
        app.new_case_view_host(app.root)
        self.assertIsNone(_StubViewer.made[0].data)

    def test_and_the_window_belongs_to_the_app_not_the_caller(self):
        # A viewer opened from a reader has to outlive it.
        app = _HostApp()
        app.new_case_view_host(_Widget())
        self.assertIs(_StubViewer.made[0].parent, app.root)

    def test_it_sits_beside_the_window_it_was_opened_from(self):
        app = _HostApp()
        caller = _Widget()
        app.new_case_view_host(caller)
        self.assertIs(_StubViewer.made[0].kw["anchor"], caller)

    def test_but_not_beside_the_main_window(self):
        app = _HostApp()
        app.new_case_view_host(app.root)
        self.assertIsNone(_StubViewer.made[0].kw["anchor"])

    def test_something_has_to_hold_the_window(self):
        app = _HostApp()
        app.new_case_view_host(app.root)
        self.assertIn(_StubViewer.made[0], app._cited_pdf_windows)

    def test_it_is_a_window_in_its_own_right_not_an_inset(self):
        # standalone: it records itself in History and answers to Window ▸ …
        app = _HostApp()
        app.new_case_view_host(app.root)
        self.assertTrue(_StubViewer.made[0].standalone)

    def test_a_viewer_that_will_not_open_is_not_fatal(self):
        ns = _load(
            "CourtListenerGUI", ["new_case_view_host", "_reporter_text_host"],
            {"_FloatingPdfWindow": _raise},
        )
        app = _HostApp()
        for name in ("new_case_view_host", "_reporter_text_host"):
            setattr(app, name, ns[name].__get__(app))
        self.assertEqual(app.new_case_view_host(app.root), "ordinary host")


def _raise(*_a, **_kw):
    raise RuntimeError("no window")


# ---------------------------------------------------------------------------
# A source printed only as pages: no switch on the strip
# ---------------------------------------------------------------------------

VIEWER_NAMES = ["has_scan", "has_text_side", "set_text_side", "_toggle_mode",
                "showing_text", "_sync_bar", "_refresh_scale",
                "_place_details_btn",
                "attach_scan", "no_scan_to_find", "_mode_button_tip"]


class _StubPane:
    made: list = []

    def __init__(self, body, data, **kw):
        self.body, self.data, self.kw = body, data, kw
        self.packed = False
        _StubPane.made.append(self)

    def pack(self, **kw):
        self.packed = True


VIEWER_NS = _load("_FloatingPdfWindow", VIEWER_NAMES,
                  {"_PdfPane": _StubPane})


class _Button:
    def __init__(self, text=""):
        self.text = text
        self.packed = False
        self.before = None
        self.side = None
        self.packings = 0
        self.state = "normal"

    def configure(self, **kw):
        self.text = kw.get("text", self.text)
        self.state = kw.get("state", self.state)

    def pack(self, **kw):
        self.packed = True
        self.before = kw.get("before")
        self.side = kw.get("side")
        self.packings += 1

    def pack_forget(self):
        self.packed = False

    def winfo_manager(self):
        return "pack" if self.packed else ""


class _Strip:
    """The strip, as far as saying what is packed on it, in packing order."""

    def __init__(self, *buttons):
        self.buttons = buttons

    def pack_slaves(self):
        return [b for b in self.buttons if b.packed]


class _Viewer:
    _W = 720
    _MIN_W = 380

    def __init__(self, scan=True, build_text=None, reader=None):
        self._pane = object() if scan else None
        self._bytes = b"%PDF" if scan else None
        self._scan_search = True if scan else None
        self._on_save = self._on_print = None
        self._on_cite = self._on_cite_browser = None
        self._url = ""
        self._analysed_url = "x"
        self._body = object()
        self._win = _Widget()
        self.titles: list = []
        self._mode = "pdf"
        self._reader = reader
        self._on_build_text = build_text
        self._text_host = None
        self._flash_after = None
        self._zoom_var = _Var()
        self._mode_btn = _Button("T")
        self._mode_btn.packed = True
        self._fit_btn = _Button("Fit")
        self._fit_btn.packed = True
        self._copy_btn = _Button("Copy ▾")
        self._zoom_label = object()
        # The side panel's switch, packed where _build_bar packs it for a
        # case: first, so at the strip's very end.
        self._details_btn = _Button()
        self._details_btn.packed = True
        self._window_btn = _Button()
        self._window_btn.packed = True
        self._bar = _Strip(self._details_btn, self._window_btn,
                           self._mode_btn, self._fit_btn)
        self.switched = []
        for name in VIEWER_NAMES:
            setattr(self, name, VIEWER_NS[name].__get__(self))

    def set_title(self, title):
        self.titles.append(title)

    def _show_zoom(self, *a, **kw):
        pass

    # what _toggle_mode calls once it has decided to switch
    def _show_text(self):
        self.switched.append("text")

    def _show_scan(self):
        self.switched.append("scan")


class NoTextSideTests(unittest.TestCase):
    def test_a_scan_with_an_opinion_behind_it_offers_the_switch(self):
        viewer = _Viewer(build_text=lambda host: object())
        self.assertTrue(viewer.has_text_side())
        viewer._sync_bar()
        self.assertTrue(viewer._mode_btn.packed)

    def test_so_does_one_already_showing_the_opinion(self):
        self.assertTrue(_Viewer(reader=object()).has_text_side())

    def test_but_statutes_at_large_has_none(self):
        viewer = _Viewer()
        self.assertFalse(viewer.has_text_side())
        viewer._sync_bar()
        self.assertFalse(viewer._mode_btn.packed)

    def test_and_pressing_it_anyway_does_nothing(self):
        viewer = _Viewer()
        viewer._toggle_mode()
        self.assertEqual(viewer.switched, [])

    def test_fit_still_belongs_to_the_pages(self):
        viewer = _Viewer()
        viewer._sync_bar()
        self.assertTrue(viewer._fit_btn.packed)
        self.assertFalse(viewer._copy_btn.packed)

    def test_a_lookup_that_came_back_empty_takes_the_switch_off(self):
        viewer = _Viewer(build_text=lambda host: object())
        viewer._sync_bar()
        self.assertTrue(viewer._mode_btn.packed)
        viewer.set_text_side(False)
        self.assertFalse(viewer._mode_btn.packed)
        self.assertIsNone(viewer._on_build_text)

    def test_a_lookup_that_found_something_leaves_it_alone(self):
        viewer = _Viewer(build_text=lambda host: object())
        viewer.set_text_side(True)
        self.assertIsNotNone(viewer._on_build_text)

    def test_a_window_that_is_only_text_is_not_touched_by_that(self):
        # No scan at all: the switch is greyed, for the other reason.
        viewer = _Viewer(scan=False, reader=object())
        viewer.set_text_side(False)
        self.assertTrue(viewer.has_text_side())

    def test_nor_has_it_a_side_panel_icon(self):
        # No case behind the pages, so no details to show: the icon comes off
        # rather than sit there refusing to work, as T does.
        viewer = _Viewer()
        viewer._sync_bar()
        self.assertFalse(viewer._details_btn.packed)

    def test_a_case_keeps_the_icon_where_it_is(self):
        viewer = _Viewer(build_text=lambda host: object())
        viewer._sync_bar()
        self.assertTrue(viewer._details_btn.packed)
        self.assertEqual(viewer._details_btn.packings, 0)   # never re-packed

    def test_a_lookup_that_came_back_empty_takes_the_icon_off_too(self):
        viewer = _Viewer(build_text=lambda host: object())
        viewer._sync_bar()
        viewer.set_text_side(False)
        self.assertFalse(viewer._details_btn.packed)

    def test_an_icon_that_comes_back_comes_back_at_the_very_end(self):
        # Packed from the right ahead of everything now on the strip — the
        # first packed from the right is the one at the far end.
        viewer = _Viewer()
        viewer._sync_bar()
        viewer._on_build_text = lambda host: object()
        viewer._sync_bar()
        self.assertTrue(viewer._details_btn.packed)
        self.assertEqual(viewer._details_btn.side, "right")
        self.assertIs(viewer._details_btn.before, viewer._window_btn)


class ScanStillComingTests(unittest.TestCase):
    """A case that opened on the text keeps P on the strip, greyed, while the
    scan is looked for behind it — and comes alive if one turns up."""

    def setUp(self):
        _StubPane.made.clear()

    def _text_only(self):
        # As adopt_reader leaves it: the opinion is the surface on screen.
        viewer = _Viewer(scan=False, reader=object())
        viewer._mode = "text"
        return viewer

    def test_p_is_on_the_strip_from_the_start(self):
        viewer = self._text_only()
        viewer._sync_bar()
        self.assertTrue(viewer._mode_btn.packed)
        self.assertEqual(viewer._mode_btn.text, "P")

    def test_but_greyed_out_while_there_is_nothing_to_show(self):
        viewer = self._text_only()
        viewer._sync_bar()
        self.assertEqual(viewer._mode_btn.state, "disabled")

    def test_and_it_says_which_it_is(self):
        viewer = self._text_only()
        self.assertIn("Looking", viewer._mode_button_tip())
        viewer.no_scan_to_find()
        self.assertIn("could be found", viewer._mode_button_tip())

    def test_a_scan_that_turns_up_is_taken(self):
        viewer = self._text_only()
        self.assertTrue(viewer.attach_scan(b"%PDF", "https://x/1.pdf"))
        self.assertTrue(viewer.has_scan())
        self.assertEqual(_StubPane.made[0].data, b"%PDF")

    def test_and_wakes_the_button_up(self):
        viewer = self._text_only()
        viewer.attach_scan(b"%PDF", "https://x/1.pdf")
        self.assertEqual(viewer._mode_btn.state, "normal")
        self.assertEqual(viewer._mode_btn.text, "P")

    def test_the_opinion_on_screen_is_left_where_it_is(self):
        viewer = self._text_only()
        viewer.attach_scan(b"%PDF", "https://x/1.pdf")
        self.assertTrue(viewer.showing_text())
        self.assertFalse(_StubPane.made[0].packed)   # built, not shown

    def test_and_the_window_takes_the_name_the_pages_carry(self):
        viewer = self._text_only()
        viewer.attach_scan(b"%PDF", "https://x/1.pdf", "Roe v. Wade, 410 U.S. 113")
        self.assertEqual(viewer.titles, ["Roe v. Wade, 410 U.S. 113"])

    def test_a_window_already_showing_pages_keeps_them(self):
        viewer = _Viewer(scan=True, build_text=lambda h: object())
        self.assertFalse(viewer.attach_scan(b"%PDF2", "https://x/2.pdf"))
        self.assertEqual(_StubPane.made, [])

    def test_an_empty_search_leaves_the_button_greyed(self):
        viewer = self._text_only()
        viewer.no_scan_to_find()
        viewer._sync_bar()
        self.assertTrue(viewer._mode_btn.packed)
        self.assertEqual(viewer._mode_btn.state, "disabled")

    def test_the_reader_behind_it_is_the_one_that_looks(self):
        src = _source_of("_ScholarTextWindow", "_offer_scan_to_host")
        self.assertIn("if not self._standalone_embed:", src)
        self.assertIn("window.no_scan_to_find()", src)
        self.assertIn("data, url, self._scan_window_title(url), "
                      "margin=margin,", src)

    def test_and_the_pages_own_links_follow_when_they_are_read(self):
        src = _source_of("_ScholarTextWindow", "_offer_scan_to_host")
        self.assertIn("self._request_pdf_analysis(", src)
        self.assertIn("self._apply_host_analysis(", src)

    def test_the_handlers_come_across_with_the_pages(self):
        # Without them a citation on the page clicks into nothing: the window
        # was built around a case with no scan, so it has no scan handlers.
        viewer = self._text_only()
        self.assertIsNone(viewer._on_cite)
        marks: list = []
        viewer.attach_scan(
            b"%PDF", "https://x/1.pdf",
            on_cite=lambda a, s: marks.append(("cite", a)),
            on_cite_browser=lambda a, s: marks.append(("browser", a)),
            on_save=lambda pane: marks.append("save"),
            on_print=lambda pane: marks.append("print"))
        viewer._on_cite(("cite", "1 U.S. 1"), "")
        viewer._on_cite_browser(("cite", "1 U.S. 1"), "")
        viewer._on_save(None)
        viewer._on_print(None)
        self.assertEqual(marks, [("cite", ("cite", "1 U.S. 1")),
                                 ("browser", ("cite", "1 U.S. 1")),
                                 "save", "print"])

    def test_a_window_that_already_had_them_keeps_its_own(self):
        viewer = self._text_only()
        viewer._on_cite = "original"
        viewer.attach_scan(b"%PDF", "https://x/1.pdf")
        self.assertEqual(viewer._on_cite, "original")

    def test_the_reader_hands_over_the_ones_it_uses_for_its_own_scans(self):
        src = _source_of("_ScholarTextWindow", "_offer_scan_to_host")
        for handler in ("on_save=self._download_pdf",
                        "on_print=self._print_pdf",
                        "on_cite=self._open_pdf_cite",
                        "on_cite_browser=self._open_pdf_cite_browser"):
            self.assertIn(handler, src)

    def test_and_keeps_the_scan_those_handlers_act_on(self):
        # _download_pdf and _print_pdf read the bytes off the reader.
        src = _source_of("_ScholarTextWindow", "_offer_scan_to_host")
        self.assertIn("self._pdf_bytes = data", src)


class ScanTitleTests(unittest.TestCase):
    """The window takes the case's Bluebook citation once the text says what
    it is — cited to the reporter the pages on screen actually print."""

    def test_a_cited_scan_is_renamed_from_the_text_found_for_it(self):
        src = _source_of("CourtListenerGUI", "_cited_filename_item")
        self.assertIn("_scan_citation_item(item, named.get(\"url\") or \"\")",
                      src)

    def test_and_so_is_one_the_courier_went_looking_for(self):
        src = _source_of("_PdfWindow", "_retitle_from_text")
        self.assertIn("_scan_citation_item(item, self._url)", src)
        self.assertIn("self._float.set_title(title)", src)
        discovered = _source_of("_PdfWindow", "_on_text_discovered")
        self.assertIn("self._retitle_from_text(source)", discovered)

    def test_named_from_the_opinion_s_caption_not_its_docket_one(self):
        for cls, name in (("_PdfWindow", "_retitle_from_text"),
                          ("_ScholarTextWindow", "_filename_item")):
            src = _source_of(cls, name)
            self.assertIn('item.pop("case_name", None)', src)
        self.assertIn("_scholar_caption_name(list(source.blocks or ()))",
                      _source_of("_PdfWindow", "_retitle_from_text"))
        self.assertIn('name = bb.get("name") or ""',
                      _source_of("_ScholarTextWindow", "_filename_item"))

    def test_the_reporter_the_scan_names_beats_the_usual_order(self):
        src = _source_of("CourtListenerGUI", "_show_cited_case_pdf")
        self.assertIn("self._jump_to_pin(named)", src)
        name = next(ast.get_source_segment(SRC, n) for n in TREE.body
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "_bluebook_display_name")
        self.assertIn('item.get("_scan_cite")', name)


# ---------------------------------------------------------------------------
# The scan window hands its pages to the viewer and steps aside
# ---------------------------------------------------------------------------

PDFWIN_NAMES = ["_hand_to_viewer", "_viewer_closed", "_dialog_parent",
                "_reveal", "_say", "_show", "_reporter_analysis",
                "_end_watch"]


class _HandoffViewer:
    made: list = []

    def __init__(self, parent, data, url, title, **kw):
        self.parent, self.data, self.url, self.title, self.kw = (
            parent, data, url, title, kw)
        self._win = _Widget()
        self._pane = object()
        self.analyses: list = []
        self.surfaced = False
        _HandoffViewer.made.append(self)

    def surface(self):
        self.surfaced = True

    def apply_analysis(self, result):
        self.analyses.append(result)


def _pdfwin_ns(pages=(("some text",),)):
    return _load(
        "_PdfWindow", PDFWIN_NAMES,
        {"_FloatingPdfWindow": _HandoffViewer,
         "_PdfPane": _raise,
         "_extract_pdf_text_and_style": lambda data: (list(pages), []),
         "_citation_links_from_visible_pdf_text":
             lambda data, pages, italics: ({1: ["link"]}, set()),
         "slip_opinion": type("slip", (), {
             "detect_sections": staticmethod(lambda pages: ["section"])}),
         },
    )


PDFWIN_NS = _pdfwin_ns()


class _ScanWindow:
    def __init__(self, is_case=False, can_discover=False, reporter=True,
                 ns=None):
        ns = ns or PDFWIN_NS
        self._app = _HostApp()
        self._win = _Widget()
        self._body = _Widget()
        self._url = "https://example.test/scan.pdf"
        self._title = "5 Stat. 797"
        self._is_case = is_case
        self._can_discover_text = can_discover
        self._text_lookup_empty = False
        self._reporter = reporter
        self._reporter_anchor = None
        self._float = None
        self._pane = None
        self._bytes = None
        self._status_var = _Var()
        self._pdf_text_pages = None
        self._pdf_text_italics: list = []
        self._pdf_text_links: dict = {}
        self._pdf_quiet_pages: set = set()
        self._pdf_sections: list = []
        self.mapped = 0
        for name in PDFWIN_NAMES:
            setattr(self, name, ns[name].__get__(self))

    def _post(self, fn, *args):
        fn(*args)

    def _maybe_start_location_map(self):
        self.mapped += 1

    def _download(self, pane=None):
        pass

    def _print(self, pane=None):
        pass

    def _open_cite(self, action, snippet):
        pass

    def _open_cite_browser(self, action, snippet):
        pass

    def _embed_discovered_text(self, host):
        return None


class ScanHandoffTests(unittest.TestCase):
    def setUp(self):
        _HandoffViewer.made.clear()

    def test_the_pages_go_to_the_floating_viewer(self):
        win = _ScanWindow()
        win._show(b"%PDF-1.4")
        self.assertEqual(len(_HandoffViewer.made), 1)
        self.assertEqual(_HandoffViewer.made[0].data, b"%PDF-1.4")

    def test_and_the_window_that_fetched_them_stays_out_of_sight(self):
        win = _ScanWindow()
        win._show(b"%PDF-1.4")
        self.assertFalse(win._win.shown)

    def test_statutes_at_large_gets_no_switch_to_offer(self):
        win = _ScanWindow()
        win._show(b"%PDF-1.4")
        self.assertIsNone(_HandoffViewer.made[0].kw["on_build_text"])

    def test_a_case_scan_whose_text_is_being_looked_for_does(self):
        win = _ScanWindow(is_case=True, can_discover=True)
        win._show(b"%PDF-1.4")
        self.assertIsNotNone(_HandoffViewer.made[0].kw["on_build_text"])

    def test_but_not_once_that_lookup_has_come_back_empty(self):
        win = _ScanWindow(is_case=True, can_discover=True)
        win._text_lookup_empty = True
        win._show(b"%PDF-1.4")
        self.assertIsNone(_HandoffViewer.made[0].kw["on_build_text"])

    def test_the_viewer_is_raised_when_it_opens(self):
        win = _ScanWindow()
        win._show(b"%PDF-1.4")
        self.assertTrue(_HandoffViewer.made[0].surfaced)

    def test_something_holds_the_viewer_open(self):
        win = _ScanWindow()
        win._show(b"%PDF-1.4")
        self.assertIn(_HandoffViewer.made[0], win._app._cited_pdf_windows)

    def test_closing_the_viewer_closes_the_courier_behind_it(self):
        win = _ScanWindow()
        win._show(b"%PDF-1.4")
        _HandoffViewer.made[0].kw["on_close"](_HandoffViewer.made[0])
        self.assertTrue(win._win.destroyed)

    def test_a_viewer_that_will_not_open_leaves_the_window_to_show_it(self):
        ns = _pdfwin_ns()
        ns["_FloatingPdfWindow"] = _raise
        win = _ScanWindow(ns=ns)
        # _show falls through to its own pane, which this stub refuses to build
        with self.assertRaises(Exception):
            win._show(b"%PDF-1.4")

    def test_the_other_interfaces_show_the_scan_in_the_window(self):
        win = _ScanWindow(reporter=False)
        with self.assertRaises(Exception):
            win._show(b"%PDF-1.4")
        self.assertEqual(_HandoffViewer.made, [])


class ScanTextLayerTests(unittest.TestCase):
    def setUp(self):
        _HandoffViewer.made.clear()

    def test_the_viewer_gets_the_text_layer(self):
        win = _ScanWindow(is_case=True)
        win._show(b"%PDF-1.4")
        result = _HandoffViewer.made[0].analyses[0]
        self.assertEqual(result["pages"], [("some text",)])
        self.assertEqual(result["links"], {1: ["link"]})
        self.assertEqual(result["sections"], ["section"])

    def test_and_the_window_keeps_it_for_the_opinion_to_align_against(self):
        win = _ScanWindow(is_case=True)
        win._show(b"%PDF-1.4")
        self.assertEqual(win._pdf_text_pages, [("some text",)])
        self.assertEqual(win.mapped, 1)

    def test_a_statute_scan_is_not_scanned_for_citations(self):
        win = _ScanWindow(is_case=False)
        win._show(b"%PDF-1.4")
        self.assertEqual(_HandoffViewer.made[0].analyses[0]["links"], {})

    def test_it_is_read_once_for_both(self):
        win = _ScanWindow(is_case=True)
        win._show(b"%PDF-1.4")
        self.assertEqual(len(_HandoffViewer.made[0].analyses), 1)


class ScanWindowChromeTests(unittest.TestCase):
    def setUp(self):
        _HandoffViewer.made.clear()

    def test_a_dialog_belongs_to_the_viewer_once_there_is_one(self):
        win = _ScanWindow()
        self.assertIs(win._dialog_parent(), win._win)
        win._show(b"%PDF-1.4")
        self.assertIs(win._dialog_parent(), _HandoffViewer.made[0]._win)

    def test_a_hand_off_panel_brings_the_hidden_window_out(self):
        win = _ScanWindow()
        win._reveal()
        self.assertTrue(win._win.shown)

    def test_but_not_once_the_pages_are_showing_elsewhere(self):
        win = _ScanWindow()
        win._show(b"%PDF-1.4")
        win._reveal()
        self.assertFalse(win._win.shown)

    def test_nothing_to_reveal_in_the_other_interfaces(self):
        win = _ScanWindow(reporter=False)
        win._reveal()
        self.assertFalse(win._win.shown)


class _FakeWatch:
    """A slow document's status window (``_LoadWatch``), as far as the window
    that finally shows the document asks it anything."""

    def __init__(self, anchor_path="", phase="text", cancelled=False,
                 geometry="700x800+40+50"):
        self.anchor_path = anchor_path
        self.phase = phase
        self.cancelled = cancelled
        self.geometry = geometry
        self.done = False
        self.handed = self.finished = 0

    def hand_off(self):
        self.handed += 1
        self.done = True
        return self.geometry

    def finish(self):
        self.finished += 1
        self.done = True


TOPLEVEL_PATH = _load_functions(["_toplevel_path"])["_toplevel_path"]


class SlowDocumentHandOffTests(unittest.TestCase):
    """A document slow to come has had a status window standing where it will
    open: it opens in that window's place, and the window goes."""

    def setUp(self):
        _StubViewer.made.clear()
        _HandoffViewer.made.clear()

    def test_a_scan_opens_where_its_status_window_stood(self):
        win = _ScanWindow()
        watch = win._watch = _FakeWatch(phase="scan")
        win._show(b"%PDF-1.4")
        self.assertEqual(_HandoffViewer.made[0].kw["geometry"], watch.geometry)
        self.assertEqual(watch.handed, 1)
        self.assertIsNone(win._watch)

    def test_with_its_pages_measured_before_it_came(self):
        win = _ScanWindow()
        win._show(b"%PDF-1.4", [(612, 792, (0, 0, 1, 1))])
        self.assertEqual(_HandoffViewer.made[0].kw["page_meta"],
                         [(612, 792, (0, 0, 1, 1))])

    def test_a_scan_the_reader_stopped_waiting_for_is_let_go(self):
        win = _ScanWindow()
        win._watch = _FakeWatch(phase="scan", cancelled=True)
        win._show(b"%PDF-1.4")
        self.assertEqual(_HandoffViewer.made, [])
        self.assertTrue(win._win.destroyed)

    def test_a_panel_this_window_shows_itself_ends_the_wait(self):
        # The CloudFlare hand-off, an error: this window says it from here.
        win = _ScanWindow()
        watch = win._watch = _FakeWatch(phase="scan")
        win._reveal()
        self.assertEqual(watch.finished, 1)
        self.assertIsNone(win._watch)

    def test_text_found_for_a_case_with_no_scan_opens_where_it_waited(self):
        app = _HostApp()
        parent = _Widget()
        watch = _FakeWatch(TOPLEVEL_PATH(parent))
        app._load_watches.append(watch)
        app.new_case_view_host(parent)
        self.assertEqual(_StubViewer.made[0].kw["geometry"], watch.geometry)
        self.assertEqual(watch.handed, 1)

    def test_but_not_a_load_from_another_window(self):
        app = _HostApp()
        watch = _FakeWatch(TOPLEVEL_PATH(_Widget()))
        app._load_watches.append(watch)
        app.new_case_view_host(_Widget())
        self.assertEqual(_StubViewer.made[0].kw["geometry"], "")
        self.assertEqual(watch.handed, 0)

    def test_nor_one_still_looking_for_its_scan(self):
        app = _HostApp()
        parent = _Widget()
        watch = _FakeWatch(TOPLEVEL_PATH(parent), phase="scan")
        app._load_watches.append(watch)
        app.new_case_view_host(parent)
        self.assertEqual(watch.handed, 0)

    def test_the_oldest_waiting_there_is_the_one_answered(self):
        app = _HostApp()
        parent = _Widget()
        first = _FakeWatch(TOPLEVEL_PATH(parent), geometry="1x1+1+1")
        second = _FakeWatch(TOPLEVEL_PATH(parent), geometry="2x2+2+2")
        app._load_watches.extend([first, second])
        app.new_case_view_host(parent)
        self.assertEqual((first.handed, second.handed), (1, 0))


class WindowIndependenceTests(unittest.TestCase):
    """No window in Reporter View closes another.  Tk destroys a top-level
    with its master, so every one of them hangs on the application root."""

    def setUp(self):
        self.ns = _load(
            "CourtListenerGUI", ["window_master", "new_secondary_view_host"],
            {"_ui_toplevel": lambda master: ("window on", master)},
        )

    def _app(self):
        app = _App()
        for name in ("window_master", "new_secondary_view_host"):
            setattr(app, name, self.ns[name].__get__(app))
        return app

    def test_a_new_window_hangs_on_the_app(self):
        app = self._app()
        self.assertIs(app.window_master(_Widget()), app.root)

    def test_the_root_going_away_is_not_fatal(self):
        app = self._app()
        app.root.destroy()
        origin = _Widget()
        self.assertIs(app.window_master(origin), origin)

    def test_a_new_document_window_is_opened_on_that_master(self):
        app = self._app()
        self.assertEqual(app.new_secondary_view_host(_Widget()),
                         ("window on", app.root))

    def test_a_cited_scan_is_owned_by_the_app_not_by_the_case_it_came_from(self):
        src = _source_of("CourtListenerGUI", "_show_cited_case_pdf")
        self.assertIn("self.root, data, url, title, margin=margin, app=self",
                      src)
        self.assertIn("anchor = host if host is not self.root else None", src)
        self.assertIn("anchor=anchor", src)

    def test_and_a_citation_followed_out_of_it_starts_from_it(self):
        src = _source_of("CourtListenerGUI", "_show_cited_case_pdf")
        self.assertIn("on_cite=cite_clicked", src)
        self.assertIn(
            "if not self.open_cited_case_pdf(onward(), act, snip, status,",
            src)
        self.assertIn(
            "_follow_brief_action(self, onward(), a, status, snippet=s)", src)

    def test_the_hidden_courier_is_owned_by_the_app_too(self):
        src = _source_of("_PdfWindow", "__init__")
        self.assertIn('_ui_toplevel(getattr(app, "root", parent))', src)


class _StripMenu:
    def __init__(self):
        self.items: list = []

    def add_command(self, label="", command=None, **kw):
        self.items.append(("command", label, command))

    def add_cascade(self, label="", menu=None, **kw):
        self.items.append(("cascade", label, menu))

    def add_separator(self, **kw):
        self.items.append(("separator", "", None))

    def index(self, _what):
        return len(self.items) - 1 if self.items else None

    def delete(self, first, last):
        del self.items[first:last + 1]

    def entryconfigure(self, index, **kw):
        kind, label, cmd = self.items[index]
        self.items[index] = (kind, kw.get("label", label), cmd)

    def labels(self):
        return [label for kind, label, _c in self.items
                if kind in ("command", "cascade")]


class _BookmarkOwner:
    def __init__(self, desc=None):
        self.desc = desc
        self.toggled = 0

    def _bookmark_descriptor(self):
        return self.desc

    def _toggle_bookmark(self):
        self.toggled += 1


class _BookmarkApp:
    def __init__(self, marked=()):
        self.marked = set(marked)

    def is_bookmarked(self, key):
        return key in self.marked


STRIP_NAMES = ["_sync_bar_menu", "_add_bar_menu_bookmark", "_bookmark_owner",
               "showing_text", "has_text_side", "details_showing",
               "_details_label"]
STRIP_NS = _load("_FloatingPdfWindow", STRIP_NAMES, {"_ACCEL": "Ctrl"})


class _StripViewer:
    """Just the strip's menu and what decides what goes on it."""

    def __init__(self, owner=None, app=None, reader=None, mode="pdf",
                 recent=None):
        self._bookmarks = owner
        self._app = app
        self._reader = reader
        self._mode = mode
        self._recent_menu = recent
        self._text_host = None     # nothing built into the text side
        # What has_text_side/details_showing read: a viewer with an opinion
        # behind it offers the case's details on the menu; one showing only
        # pages has none to offer.
        self._on_build_text = None
        self._pane = None
        self._bytes = b""
        self._details_side = None
        self._details_open = False
        self.details_toggled = 0
        self.closed = 0
        self._bar_menu = _StripMenu()
        for label in ("Save As…", "Print…"):
            self._bar_menu.add_command(label=label)
        self._bar_menu_fixed = self._bar_menu.index("end")
        for name in STRIP_NAMES:
            setattr(self, name, STRIP_NS[name].__get__(self))

    def close(self):
        self.closed += 1

    def toggle_details(self):
        self.details_toggled += 1


class StripMenuTests(unittest.TestCase):
    """A window with no menu bar keeps History, Bookmarks and Close on the
    strip's own menu."""

    def test_save_and_print_lead_and_close_ends_it(self):
        viewer = _StripViewer()
        viewer._sync_bar_menu()
        self.assertEqual(viewer._bar_menu.labels(),
                         ["Save As…", "Print…", "Close\tCtrl+W"])

    def test_close_closes_the_window_properly(self):
        viewer = _StripViewer()
        viewer._sync_bar_menu()
        viewer._bar_menu.items[-1][2]()
        self.assertEqual(viewer.closed, 1)

    def test_history_is_on_it(self):
        viewer = _StripViewer(recent=object())
        viewer._sync_bar_menu()
        self.assertEqual(viewer._bar_menu.labels()[2], "Recent")

    def test_an_app_that_cannot_fill_it_gets_no_recent(self):
        viewer = _StripViewer()
        viewer._sync_bar_menu()
        self.assertNotIn("Recent", viewer._bar_menu.labels())

    def test_posting_it_twice_does_not_stack_the_menu_up(self):
        viewer = _StripViewer(recent=object())
        viewer._sync_bar_menu()
        first = viewer._bar_menu.labels()
        viewer._sync_bar_menu()
        self.assertEqual(viewer._bar_menu.labels(), first)

    def test_the_menu_is_rebuilt_whenever_it_is_posted(self):
        src = _source_of("_FloatingPdfWindow", "_post_bar_menu")
        self.assertIn("self._sync_bar_menu()", src)

    def test_the_submenu_is_made_once_and_re_attached(self):
        src = _source_of("_FloatingPdfWindow", "_build_bar")
        self.assertIn('self._recent_menu = self._bar_submenu('
                      '"populate_history_menu")', src)
        sub = _source_of("_FloatingPdfWindow", "_bar_submenu")
        self.assertIn("postcommand=lambda m=sub: fill(m, *args)", sub)


class StripBookmarkTests(unittest.TestCase):
    def test_a_statute_scan_can_be_bookmarked_from_the_strip(self):
        owner = _BookmarkOwner({"key": "pdf:x", "noun": "statute"})
        viewer = _StripViewer(owner, _BookmarkApp())
        viewer._sync_bar_menu()
        self.assertIn("Bookmark This Statute", viewer._bar_menu.labels())

    def test_and_taken_off_again(self):
        owner = _BookmarkOwner({"key": "pdf:x", "noun": "case"})
        viewer = _StripViewer(owner, _BookmarkApp(marked={"pdf:x"}))
        viewer._sync_bar_menu()
        self.assertIn("Remove Bookmark for This Case",
                      viewer._bar_menu.labels())

    def test_the_entry_does_the_bookmarking(self):
        owner = _BookmarkOwner({"key": "pdf:x", "noun": "case"})
        viewer = _StripViewer(owner, _BookmarkApp())
        viewer._sync_bar_menu()
        viewer._bar_menu.items[3][2]()
        self.assertEqual(owner.toggled, 1)

    def test_the_state_is_re_read_each_time(self):
        owner = _BookmarkOwner({"key": "pdf:x", "noun": "case"})
        app = _BookmarkApp()
        viewer = _StripViewer(owner, app)
        viewer._sync_bar_menu()
        app.marked.add("pdf:x")
        viewer._sync_bar_menu()
        self.assertIn("Remove Bookmark for This Case",
                      viewer._bar_menu.labels())

    def test_a_document_that_cannot_be_bookmarked_gets_no_entry(self):
        viewer = _StripViewer(_BookmarkOwner(None), _BookmarkApp())
        viewer._sync_bar_menu()
        self.assertEqual(viewer._bar_menu.labels(),
                         ["Save As…", "Print…", "Close\tCtrl+W"])

    def test_nor_does_a_viewer_with_no_owner_to_ask(self):
        viewer = _StripViewer(None, _BookmarkApp())
        viewer._sync_bar_menu()
        self.assertEqual(viewer._bar_menu.labels(),
                         ["Save As…", "Print…", "Close\tCtrl+W"])

    def test_the_text_side_bookmarks_the_opinion_it_is_showing(self):
        scan = _BookmarkOwner({"key": "pdf:x", "noun": "statute"})
        reader = _BookmarkOwner({"key": "case:y", "noun": "case"})
        viewer = _StripViewer(scan, _BookmarkApp(), reader=reader, mode="text")
        viewer._sync_bar_menu()
        self.assertIn("Bookmark This Case", viewer._bar_menu.labels())
        viewer._bar_menu.items[3][2]()
        self.assertEqual((reader.toggled, scan.toggled), (1, 0))

    def test_and_the_pages_bookmark_the_document_behind_them(self):
        scan = _BookmarkOwner({"key": "pdf:x", "noun": "statute"})
        reader = _BookmarkOwner({"key": "case:y", "noun": "case"})
        viewer = _StripViewer(scan, _BookmarkApp(), reader=reader, mode="pdf")
        viewer._sync_bar_menu()
        self.assertIn("Bookmark This Statute", viewer._bar_menu.labels())

    def test_the_courier_hands_the_viewer_its_bookmark(self):
        _HandoffViewer.made.clear()
        win = _ScanWindow()
        win._show(b"%PDF-1.4")
        self.assertIs(_HandoffViewer.made[0].kw["bookmarks"], win)


class ScanWindowSourceTests(unittest.TestCase):
    """The pieces that are easier to read off the source than to drive."""

    def test_the_hidden_courier_is_not_in_the_window_registry(self):
        src = _source_of("_PdfWindow", "__init__")
        self.assertIn("self._win.withdraw()", src)
        self.assertNotIn("register_secondary_window", src)

    def test_saving_and_printing_use_the_rendering_on_screen(self):
        src = _source_of("_PdfWindow", "_hand_to_viewer")
        self.assertIn("on_save=lambda pane: self._download(pane)", src)
        self.assertIn("on_print=lambda pane: self._print(pane)", src)

    def test_the_place_the_reader_is_comes_from_the_viewer_s_pages(self):
        src = _source_of("_PdfWindow", "_discovered_text_target")
        self.assertIn('getattr(self._float, "_pane", None)', src)

    def test_an_empty_lookup_takes_the_switch_off_the_strip(self):
        src = _source_of("_PdfWindow", "_on_text_discovered")
        self.assertIn("self._float.set_text_side(False)", src)

    def test_the_switch_builds_the_discovered_text_in_place(self):
        src = _source_of("_PdfWindow", "_embed_discovered_text")
        self.assertIn("chromeless=True", src)
        self.assertIn("initial_pdf_analysis=self._initial_pdf_analysis()", src)


# ---------------------------------------------------------------------------
# The case's details, in a column along the window's right-hand side ("s")
# ---------------------------------------------------------------------------

DETAILS_NAMES = ["_details_shortcut", "details_showing", "toggle_details",
                 "_open_details", "_hide_details", "_details_column",
                 "_pack_details", "_details_width", "_pin_body_width",
                 "_unpin_body_width", "_details_growth", "_details_shrink",
                 "_when_resized", "_build_reader", "adopt_reader", "has_scan",
                 "has_text_side", "showing_text", "_details_label",
                 "_mark_details_btn"]


class _Packable:
    def __init__(self, master=None):
        self.master = master
        self.packed = False
        self.destroyed = False

    def pack(self, **_kw):
        self.packed = True

    def destroy(self):
        self.destroyed = True


class _PanelWindow:
    """This window's geometry, as opening the panel reads and writes it —
    the window manager's own string, the width it has taken, and where on the
    desktop it stands.  Every change goes into one log, beside the column's
    packing and the pages' hold, so a test can read the order they came in."""

    def __init__(self, x=100, y=80, w=720, h=880):
        self.x, self.y, self.width, self.height = x, y, w, h
        self.zoomed = False
        self.applied: list = []
        self.minsizes: list = []
        self.events: list = []
        self.bindings: dict = {}
        self.focused = None
        self.pages = None           # the frame the pages sit in
        self.deferred = None        # a list, to hold after() callbacks back

    def wm_geometry(self):
        return f"{self.width}x{self.height}+{self.x}+{self.y}"

    def geometry(self, spec):
        self.applied.append(spec)
        self.events.append(("geometry", spec))
        m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", spec)
        self.width, self.height, self.x, self.y = map(int, m.groups())

    def winfo_width(self):
        return self.width

    def winfo_rootx(self):
        return self.x

    def state(self):
        return "zoomed" if self.zoomed else "normal"

    def attributes(self, name):
        raise _Tk.TclError(name)    # as Windows answers "-zoomed"

    def minsize(self, w, h):
        self.minsizes.append((w, h))

    def update_idletasks(self):
        pass

    def after(self, _ms, fn, *args):
        """The window manager answers at once here, so the wait resolves on
        the spot — unless a test holds the answer back."""
        if self.deferred is not None:
            self.deferred.append((fn, args))
        else:
            fn(*args)
        return "timer"

    def answer(self):
        while self.deferred:
            fn, args = self.deferred.pop(0)
            fn(*args)

    def focus_get(self):
        return self.focused

    def bind(self, sequence, callback, add=None):
        self.bindings[sequence] = callback


class _Pages:
    """The frame the pages sit in, whose width the panel's coming and going
    holds."""

    def __init__(self, window, width=None):
        self.window = window
        self.width = window.width if width is None else width
        self.requested = None
        self.given: list = []       # every width it was told to keep
        self.propagate = True
        self.expand = True

    def winfo_width(self):
        return self.width

    def configure(self, **kw):
        if "width" in kw:
            self.requested = kw["width"]
            self.given.append(kw["width"])

    def pack_propagate(self, flag):
        self.propagate = flag

    def pack_configure(self, **kw):
        if "expand" in kw:
            self.expand = kw["expand"]
            self.window.events.append(("free",) if self.expand else ("held",))

    def held(self):
        return not self.expand and not self.propagate


class _Frame:
    """A frame of the window's: the details column, its hairline and the
    panel's host."""

    made: list = []

    def __init__(self, master=None, **kw):
        self.master = master
        self.kw = kw
        self.packed = False
        self.pack_kw = None
        self.bindings: dict = {}
        _Frame.made.append(self)

    def pack(self, **kw):
        self.packed, self.pack_kw = True, kw
        if isinstance(self.master, _PanelWindow):     # the column itself
            self.master.events.append(("pack", self.master.pages.held()))

    def pack_forget(self):
        self.packed = False
        if isinstance(self.master, _PanelWindow):
            self.master.events.append(("unpack", self.master.pages.held()))


def _no_window_of_its_own(*_a, **_kw):
    raise AssertionError("the panel is not a window of its own any more")


class _DetailsReader:
    """The chromeless opinion the details panel is built by."""

    def __init__(self):
        self._details_panel_w = 300
        self._details_host = None
        self._details_views = True
        self._details_on = False
        self._details_var = _Var(False)
        self.built = 0
        self.refreshed = 0
        self.panel = None

    def _details_panel(self):
        self.built += 1
        self.panel = _Packable(self._details_host)
        return self.panel

    def _refresh_details_view(self):
        self.refreshed += 1


class _PanelIcon:
    """The strip's side panel icon, as far as which artwork it shows."""

    def __init__(self):
        self.image = "panel"

    def configure(self, **kw):
        self.image = kw.get("image", self.image)


class _TypingWidget:
    def __init__(self, cls="TEntry", state="normal"):
        self.cls, self.state = cls, state

    def winfo_class(self):
        return self.cls

    def cget(self, _option):
        return self.state


def _module_regex(name: str):
    """A module-level compiled pattern, evaluated from the source."""
    for node in TREE.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return eval(ast.get_source_segment(SRC, node.value),  # noqa: S307
                        {"re": re})
    raise AssertionError(f"module-level pattern not found: {name}")


#: A desktop 1600x1000 wide, so the placement arithmetic is checkable.
DETAILS_WORK_AREA = (0, 0, 1600, 1000)

DETAILS_NS = _load(
    "_FloatingPdfWindow", DETAILS_NAMES,
    {"tk": type("tk", (), {"TclError": Exception, "Frame": _Frame}),
     "ttk": type("ttk", (), {"Frame": _Frame}),
     "_ui_toplevel": _no_window_of_its_own,
     "_UI": {"border": "#e2e4e9"},
     "_bind_recursive": lambda widget, seq, fn: widget.bindings.__setitem__(
         seq, fn),
     "_work_area": lambda _w: DETAILS_WORK_AREA,
     "_window_zoomed": _load_functions(["_window_zoomed"])["_window_zoomed"],
     "_WM_GEOMETRY_RE": _module_regex("_WM_GEOMETRY_RE"),
     "_widget_accepts_typing": _load_functions(
         ["_widget_accepts_typing"])["_widget_accepts_typing"],
     "_EmbeddedCaseHost": lambda body, window: _Packable(body),
     "_ScholarTextWindow": type(
         "_ScholarTextWindow", (),
         {"_DETAILS_PANEL_W": 300,
          "_SCAN_DETAILS_VIEWS": _class_value(
              "_ScholarTextWindow", "_SCAN_DETAILS_VIEWS")}),
     },
)

#: The column the window grows by: the panel and the hairline before it.
COLUMN_W = 300 + _class_value("_FloatingPdfWindow", "_DETAILS_RULE_W")


class _DetailsViewer:
    _MIN_H = 280
    _MIN_W = _class_value("_FloatingPdfWindow", "_MIN_W")
    _DETAILS_RULE_W = _class_value("_FloatingPdfWindow", "_DETAILS_RULE_W")
    _DETAILS_SLACK = _class_value("_FloatingPdfWindow", "_DETAILS_SLACK")

    def __init__(self, scan=True, reader="build", x=100, y=80, w=720, h=880):
        self._pane = object() if scan else None
        self._bytes = b"%PDF" if scan else None
        self._win = _PanelWindow(x, y, w, h)
        self._body = _Pages(self._win)
        self._win.pages = self._body
        self._mode = "pdf" if scan else "text"
        self._text_host = None
        self._details_side = None
        self._details_open = False
        self._details_grew = None
        self._details_resize_target = None
        self._details_btn = _PanelIcon()
        self._strip_icons = {"panel": "panel",
                             "panel_showing": "panel_showing"}
        self.flashed: list = []
        self.synced = 0
        self.reader = _DetailsReader() if reader == "build" else None
        # What the reader's own __init__ does as it finishes: tells the window
        # around it that it now has an opinion.
        self._on_build_text = None if reader is None else (
            lambda host: (self.adopt_reader(self.reader), self.reader)[1])
        self._reader = self.reader if reader == "ready" else None
        for name in DETAILS_NAMES:
            setattr(self, name, DETAILS_NS[name].__get__(self))

    def _flash(self, message, ms=2500):
        self.flashed.append(message)

    def _sync_bar(self):
        self.synced += 1

    def press_s(self, focused=None):
        self._win.focused = focused
        return self._details_shortcut(None)

    def columns(self):
        """The details columns built into this window."""
        return [f for f in _Frame.made if f.master is self._win]


class DetailsPanelTests(unittest.TestCase):
    """"s" opens the case's details in a column along the window's right-hand
    side — over the pages as much as over the text — and the window grows to
    hold it, so the pages keep the room they had."""

    def setUp(self):
        _Frame.made.clear()

    def test_s_opens_the_panel(self):
        viewer = _DetailsViewer()
        self.assertEqual(viewer.press_s(), "break")
        self.assertTrue(viewer.details_showing())
        self.assertEqual(len(viewer.columns()), 1)

    def test_in_this_window_not_in_one_of_its_own(self):
        # _ui_toplevel would fail the test: the column's master is the window.
        viewer = _DetailsViewer()
        viewer.press_s()
        (column,) = viewer.columns()
        self.assertTrue(column.packed)

    def test_to_the_right_of_the_pages(self):
        viewer = _DetailsViewer()
        viewer.press_s()
        (column,) = viewer.columns()
        self.assertEqual(column.pack_kw,
                         {"side": "right", "fill": "y", "before": viewer._body})

    def test_it_is_the_opinion_s_own_panel_holding_the_case_s_details(self):
        viewer = _DetailsViewer()
        viewer.press_s()
        reader = viewer.reader
        (column,) = viewer.columns()
        self.assertEqual(reader.built, 1)
        self.assertIs(reader.panel.master, reader._details_host)
        self.assertIs(reader._details_host.master, column)
        self.assertTrue(reader.panel.packed)
        self.assertTrue(reader._details_on)
        self.assertEqual(reader.refreshed, 1)

    def test_it_offers_the_case_s_details_and_the_docket_behind_them(self):
        # The shorter list — the case, its docket, the Court's recent
        # opinions: related cases and the outline want a window's room.
        viewer = _DetailsViewer()
        viewer.press_s()
        self.assertEqual(
            viewer.reader._details_views,
            _class_value("_ScholarTextWindow", "_SCAN_DETAILS_VIEWS"))

    def test_and_opens_on_the_case_s_own_details(self):
        # First in the list is what the selector starts on, so a panel opened
        # with "s" shows the Oyez/CourtListener details, not the docket.
        viewer = _DetailsViewer()
        viewer.press_s()
        self.assertEqual(viewer.reader._details_views[0], "Case details")

    def test_the_window_grows_by_the_column_s_width(self):
        viewer = _DetailsViewer(x=100, y=80, w=720, h=880)
        viewer.press_s()
        # To the right: where it stands, and its height, are left alone.
        self.assertEqual(viewer._win.applied, [f"{720 + COLUMN_W}x880+100+80"])

    def test_the_pages_are_held_at_their_width_while_it_grows(self):
        # Tk lays out the resize and the packing in separate passes, and the
        # window manager answers in its own time: held across both, the pages
        # are never given the column's width, or the window's, in between.
        viewer = _DetailsViewer()
        viewer._body.width = 720
        viewer.press_s()
        # Held at the width they had, then given back to the packer (0).
        self.assertEqual(viewer._body.given, [720, 0])
        self.assertEqual(viewer._win.events, [
            ("held",), ("geometry", f"{720 + COLUMN_W}x880+100+80"),
            ("pack", True), ("free",)])

    def test_closing_gives_the_width_back(self):
        viewer = _DetailsViewer()
        viewer.press_s()
        viewer._win.events.clear()
        viewer.press_s()
        self.assertFalse(viewer.details_showing())
        self.assertEqual((viewer._win.width, viewer._win.x), (720, 100))
        # Unpacked while the pages are held, before the window shrinks: the
        # other way round they would be squeezed beside the column.
        self.assertEqual(viewer._win.events, [
            ("held",), ("unpack", True), ("geometry", "720x880+100+80"),
            ("free",)])

    def test_near_the_desktop_s_right_edge_the_window_steps_left(self):
        # 800 + 720 + the column is past the 1600 desktop: the panel would
        # open out of sight, so the window moves left just far enough.
        viewer = _DetailsViewer(x=800, w=720)
        viewer.press_s()
        new_w = 720 + COLUMN_W
        self.assertEqual(viewer._win.applied, [f"{new_w}x880+{1600 - new_w}+80"])
        # …and back again when it goes.
        viewer.press_s()
        self.assertEqual(viewer._win.applied[-1], "720x880+800+80")

    def test_as_does_one_already_hanging_off_that_edge(self):
        viewer = _DetailsViewer(x=1200, w=720)
        viewer.press_s()
        new_w = 720 + COLUMN_W
        self.assertEqual(viewer._win.applied, [f"{new_w}x880+{1600 - new_w}+80"])

    def test_a_window_moved_since_is_not_moved_back(self):
        viewer = _DetailsViewer(x=800, w=720)
        viewer.press_s()
        viewer._win.x = 300                      # the reader moved it
        viewer.press_s()
        self.assertEqual(viewer._win.applied[-1], "720x880+300+80")

    def test_a_maximized_window_gives_the_panel_the_pages_room(self):
        viewer = _DetailsViewer(x=0, y=0, w=1600, h=1000)
        viewer._win.zoomed = True
        viewer.press_s()
        self.assertEqual(viewer._win.applied, [])
        self.assertEqual(viewer._win.events, [("pack", False)])
        viewer.press_s()
        self.assertEqual(viewer._win.applied, [])
        self.assertEqual(viewer._win.events, [("pack", False), ("unpack", False)])

    def test_as_does_one_the_desktop_could_not_hold_grown(self):
        viewer = _DetailsViewer(x=100, w=1400)
        viewer.press_s()
        self.assertEqual(viewer._win.applied, [])
        self.assertTrue(viewer.columns()[0].packed)

    def test_a_window_on_a_screen_the_desktop_does_not_cover_just_grows(self):
        # A second monitor _work_area cannot see: no room to measure, so no
        # step left that would throw the window back onto the first.
        viewer = _DetailsViewer(x=1700, w=720)
        viewer.press_s()
        self.assertEqual(viewer._win.applied, [f"{720 + COLUMN_W}x880+1700+80"])

    def test_a_window_maximized_since_keeps_its_size_as_the_panel_goes(self):
        viewer = _DetailsViewer()
        viewer.press_s()
        viewer._win.zoomed = True
        viewer.press_s()
        self.assertEqual(len(viewer._win.applied), 1)     # the growth only
        self.assertFalse(viewer.columns()[0].packed)

    def test_pages_not_laid_out_yet_are_not_held(self):
        viewer = _DetailsViewer()
        viewer._body.width = 1
        viewer.press_s()
        self.assertEqual(viewer._win.applied, [])
        self.assertEqual(viewer._win.events, [("pack", False)])

    def test_put_away_before_the_window_has_grown_it_stays_away(self):
        viewer = _DetailsViewer()
        viewer._win.deferred = []                # the resize not yet granted
        viewer.press_s()
        viewer.press_s()
        viewer._win.answer()
        self.assertFalse(viewer.columns()[0].packed)
        self.assertFalse(viewer._body.held())

    def test_the_window_keeps_room_for_pages_and_panel(self):
        viewer = _DetailsViewer()
        viewer.press_s()
        self.assertEqual(viewer._win.minsizes[-1],
                         (viewer._MIN_W + COLUMN_W, viewer._MIN_H))
        viewer.press_s()
        self.assertEqual(viewer._win.minsizes[-1],
                         (viewer._MIN_W, viewer._MIN_H))

    def test_pressing_it_again_puts_the_panel_away_but_keeps_it(self):
        viewer = _DetailsViewer()
        viewer.press_s()
        viewer.press_s()
        self.assertFalse(viewer.details_showing())
        (column,) = viewer.columns()
        self.assertFalse(column.packed)
        viewer.press_s()
        self.assertTrue(viewer.details_showing())
        self.assertTrue(column.packed)
        self.assertEqual(len(viewer.columns()), 1)
        self.assertEqual(viewer.reader.built, 1)   # neither rebuilt nor refetched

    def test_esc_in_the_panel_puts_it_away(self):
        viewer = _DetailsViewer()
        viewer.press_s()
        (column,) = viewer.columns()
        self.assertEqual(column.bindings["<Escape>"](None), "break")
        self.assertFalse(viewer.details_showing())
        self.assertFalse(column.packed)

    def test_the_key_is_left_alone_while_a_field_is_being_typed_in(self):
        viewer = _DetailsViewer()
        self.assertIsNone(viewer.press_s(_TypingWidget("TEntry")))
        self.assertEqual(viewer.columns(), [])
        # A disabled box takes no typing, so the key is free to fire.
        self.assertEqual(
            viewer.press_s(_TypingWidget("Text", state="disabled")), "break")

    def test_opening_it_over_the_pages_leaves_the_pages_showing(self):
        # The opinion is built for its details, behind the scan — building it
        # must not switch the window to the text side.
        viewer = _DetailsViewer()
        viewer.press_s()
        self.assertIsNotNone(viewer._reader)   # built, and behind the pages
        self.assertEqual(viewer._mode, "pdf")
        self.assertFalse(viewer.showing_text())

    def test_but_a_window_with_no_pages_is_the_text_as_it_arrives(self):
        # adopt_reader's other half: with no scan the opinion *is* the surface.
        viewer = _DetailsViewer(scan=False, reader=None)
        viewer._mode = "pdf"
        viewer.adopt_reader(_DetailsReader())
        self.assertEqual(viewer._mode, "text")

    def test_a_case_whose_text_has_not_arrived_says_so(self):
        viewer = _DetailsViewer(reader="none")
        viewer._on_build_text = lambda host: None
        viewer.press_s()
        self.assertEqual(viewer.flashed, ["Case details not ready"])
        self.assertEqual(viewer.columns(), [])
        self.assertEqual(viewer._win.applied, [])

    def test_a_document_with_no_case_behind_it_has_no_details(self):
        # The Statutes at Large, an English report: pages and nothing else.
        viewer = _DetailsViewer(reader=None)
        viewer.press_s()
        self.assertEqual(viewer.columns(), [])
        self.assertEqual(viewer.flashed, [])
        self.assertEqual(viewer._win.applied, [])


class DetailsGeometryTests(unittest.TestCase):
    """The arithmetic of growing and giving back, on its own."""

    def test_a_negative_position_is_read(self):
        viewer = _DetailsViewer(x=-8, w=720)
        spec, shift = viewer._details_growth(COLUMN_W)
        self.assertEqual((spec, shift), (f"{720 + COLUMN_W}x880+-8+80", 0))

    def test_the_step_left_is_never_past_the_desktop_s_left_edge(self):
        # Whatever needs a bigger step than that cannot fit, and so is None.
        viewer = _DetailsViewer(x=10, w=1600 - COLUMN_W + 5)
        self.assertIsNone(viewer._details_growth(COLUMN_W))

    def test_giving_back_never_goes_below_the_least_width(self):
        viewer = _DetailsViewer(w=500)
        spec = viewer._details_shrink((COLUMN_W, 0, "801x880+100+80"))
        self.assertEqual(spec, f"{viewer._MIN_W}x880+100+80")


class DetailsIconTests(unittest.TestCase):
    """The strip's side panel icon is the "s" key's switch, and shows which
    way it is set."""

    def setUp(self):
        _Frame.made.clear()

    def test_a_click_opens_the_panel_and_another_puts_it_away(self):
        # The icon's command is toggle_details, the switch "s" throws.
        viewer = _DetailsViewer()
        viewer.toggle_details()
        self.assertTrue(viewer.details_showing())
        viewer.toggle_details()
        self.assertFalse(viewer.details_showing())
        self.assertEqual(len(viewer.columns()), 1)

    def test_its_column_is_filled_in_while_the_panel_is_up(self):
        viewer = _DetailsViewer()
        self.assertEqual(viewer._details_btn.image, "panel")
        viewer.toggle_details()
        self.assertEqual(viewer._details_btn.image, "panel_showing")
        viewer.toggle_details()
        self.assertEqual(viewer._details_btn.image, "panel")

    def test_the_key_moves_it_as_well(self):
        viewer = _DetailsViewer()
        viewer.press_s()
        self.assertEqual(viewer._details_btn.image, "panel_showing")
        viewer.press_s()
        self.assertEqual(viewer._details_btn.image, "panel")

    def test_and_esc_in_the_panel(self):
        viewer = _DetailsViewer()
        viewer.toggle_details()
        self.assertEqual(viewer._details_btn.image, "panel_showing")
        viewer.columns()[0].bindings["<Escape>"](None)
        self.assertEqual(viewer._details_btn.image, "panel")

    def test_a_case_whose_text_has_not_arrived_leaves_it_empty(self):
        viewer = _DetailsViewer(reader="none")
        viewer._on_build_text = lambda host: None
        viewer.toggle_details()
        self.assertEqual(viewer.flashed, ["Case details not ready"])
        self.assertEqual(viewer._details_btn.image, "panel")

    def test_its_tip_says_what_a_click_will_do(self):
        viewer = _DetailsViewer()
        self.assertEqual(viewer._details_label(), "Case Details   s")
        viewer.toggle_details()
        self.assertEqual(viewer._details_label(), "Hide Case Details   s")


class DetailsMenuTests(unittest.TestCase):
    def test_the_strip_offers_the_panel_and_says_when_it_is_open(self):
        viewer = _StripViewer()
        viewer._on_build_text = object()
        viewer._sync_bar_menu()
        self.assertIn("Case Details\ts", viewer._bar_menu.labels())
        viewer._details_side = object()
        viewer._details_open = True
        viewer._sync_bar_menu()
        self.assertIn("Hide Case Details\ts", viewer._bar_menu.labels())

    def test_the_entry_opens_it(self):
        viewer = _StripViewer()
        viewer._on_build_text = object()
        viewer._sync_bar_menu()
        entry = next(cmd for kind, label, cmd in viewer._bar_menu.items
                     if label.startswith("Case Details"))
        entry()
        self.assertEqual(viewer.details_toggled, 1)

    def test_a_document_with_no_case_behind_it_is_not_offered_it(self):
        viewer = _StripViewer()
        viewer._sync_bar_menu()
        self.assertNotIn("Case Details\ts", viewer._bar_menu.labels())


if __name__ == "__main__":
    unittest.main()
