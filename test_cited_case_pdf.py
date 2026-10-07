"""Following citations from a PDF, and the U.S. Reports cite already on file.

1. The case name on the floating viewer's strip opens that case's **text**.
   For a PDF opened from the opinion reader it goes back to that reader; for a
   case reached by following a citation it opens the text the ordinary way,
   warmed in the background while the reader looks at the scan.

2. A citation clicked **inside a PDF** opens the cited case's own PDF in a
   viewer of its own, resolved by the routes the PDF button already uses, and
   falls back to the text when no scan exists anywhere.

3. A U.S. Reports cite that came with the opinion — from the opinion database
   or the opinion's own header — is what the PDF resolver tries first, before
   it goes looking for one on CourtListener or Google Scholar.

4. Google Scholar has no copy of every case.  Where the scan was found through
   CourtListener, its text is what the viewer's T button shows instead, and
   what names the window — the same job the Scholar page would have done.

Lifted out of ``courtlistener_gui`` with ``ast`` (importing it needs tkinter,
absent on a headless run) and driven against stubs.
"""

import ast
import contextlib
import dataclasses
import pathlib
import re
import sys
import tempfile
import types
import typing
import unittest
from types import SimpleNamespace
from unittest import mock

import citations
import opinion_location
import slip_opinion
from citation_overrides import (
    citation_identity_keys, find_override, update_overrides,
)


SRC = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text()
TREE = ast.parse(SRC)


class _Tk:
    TclError = Exception
    Misc = Menu = object


class _Thread:
    """Runs the worker inline, so a test sees the whole chain at once."""

    started: list = []

    def __init__(self, target=None, daemon=False, **_kw):
        self._target = target
        _Thread.started.append(self)

    def start(self):
        if self._target is not None:
            self._target()


def _load(cls, names, extra=None) -> dict:
    body = next(n.body for n in TREE.body
                if isinstance(n, ast.ClassDef) and n.name == cls)
    found = {n.name: ast.get_source_segment(SRC, n) for n in body
             if isinstance(n, ast.FunctionDef) and n.name in names}
    missing = [n for n in names if n not in found]
    if missing:
        raise AssertionError(f"not found on {cls}: {missing}")
    ns = {"tk": _Tk, "re": re, "Optional": typing.Optional,
          "threading": mock.Mock(Thread=_Thread)}
    ns.update(extra or {})
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


def _load_dataclass(name: str, extra=None):
    """Exec one module-level dataclass into a stub namespace.

    ``get_source_segment`` starts a class at its ``class`` keyword, so the
    decorator that makes it a dataclass has to be picked up separately — read
    without it, the class would have no ``__init__`` at all.
    """
    node = next((n for n in TREE.body
                 if isinstance(n, ast.ClassDef) and n.name == name), None)
    if node is None:
        raise AssertionError(f"module-level class not found: {name}")
    lines = SRC.splitlines(keepends=True)
    first = min([node.lineno] + [d.lineno for d in node.decorator_list])
    ns = {"dataclass": dataclasses.dataclass, "Optional": typing.Optional}
    ns.update(extra or {})
    exec("".join(lines[first - 1:node.end_lineno]), ns)
    return ns[name]


def _module_value(name: str):
    """A module-level constant of courtlistener_gui, evaluated from its own
    assignment (the module itself needs tkinter to import)."""
    node = next(n for n in TREE.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name
                        for t in n.targets))
    ns = {"re": re}
    exec(ast.get_source_segment(SRC, node), ns)
    return ns[name]


def _load_function(name: str, extra=None):
    """Exec one module-level function into a stub namespace."""
    src = next((ast.get_source_segment(SRC, n) for n in TREE.body
                if isinstance(n, ast.FunctionDef) and n.name == name), None)
    if src is None:
        raise AssertionError(f"module-level function not found: {name}")
    ns = {"tk": _Tk, "re": re, "Optional": typing.Optional}
    ns.update(extra or {})
    exec(src, ns)
    return ns[name]


#: The real thing: what saving and printing both write, so a test that watches
#: the pane sees exactly the calls the app makes.
WRITE_OUTPUT_PDF = _load_function("_write_output_pdf")


def _fake_static_case_law_url(cite: str):
    """The static.case.law file a citation names, as the real one builds it."""
    m = re.fullmatch(r"(\d+)\s+(.+?)\s+(\d+)", cite or "")
    if not m:
        return None
    slug = re.sub(r"[^a-z0-9]", "", m.group(2).lower())
    return (f"https://static.case.law/{slug}/{int(m.group(1))}/case-pdfs/"
            f"{int(m.group(3)):04d}-01.pdf")


def _fake_case_law_reporter_cite(url: str) -> str:
    """The reporter a static.case.law file names, as the real one reads it."""
    m = re.search(r"static\.case\.law/([a-z0-9-]+)/(\d+)/(\d+)", url or "")
    reporters = {"us": "U.S.", "sct": "S. Ct.", "f2d": "F.2d"}
    if not m or m.group(1) not in reporters:
        return ""
    return f"{int(m.group(2))} {reporters[m.group(1)]} {int(m.group(3))}"


_NORMALIZED_US_CITE = (
    lambda c: (str(c).strip()
               if re.search(r"\d+\s+U\.?\s?S\.?\s+\d+", str(c)) else "")
)

#: The real pin-cite arithmetic, so a test exercises the rule rather than a
#: restatement of it.
PIN_PAGE_NUMBER = _load_function(
    "_pin_page_number", {"_split_note_pin": citations.split_note_pin})
def _module_value(name: str):
    """A module-level constant, evaluated from the source, so a test reads the
    real thing rather than a restatement of it."""
    for node in TREE.body:
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return eval(ast.get_source_segment(SRC, node.value),  # noqa: S307
                        {"re": re, "frozenset": frozenset})
    raise AssertionError(f"module-level constant not found: {name}")


PRINTED_PAGE_NUMBERS = _load_function(
    "_printed_page_numbers",
    {"slip_opinion": slip_opinion,
     "_FOLIO_LINES": _module_value("_FOLIO_LINES"),
     "_FOLIO_RE": _module_value("_FOLIO_RE"),
     "_SPACED_DIGITS_RE": _module_value("_SPACED_DIGITS_RE")})
SCAN_PAGE_FOR_PIN = _load_function(
    "_scan_page_for_pin",
    {"opinion_location": opinion_location,
     "_pin_page_number": PIN_PAGE_NUMBER,
     "_printed_page_numbers": PRINTED_PAGE_NUMBERS})


#: The real narrowing: which of a case's parallel citations names the pages
#: actually on screen.
SCAN_CITATION_ITEM = _load_function(
    "_scan_citation_item",
    {"_normalized_us_cite": _NORMALIZED_US_CITE,
     "_is_us_reports_pdf": lambda url: "usrep" in (url or "").lower(),
     "_case_law_reporter_cite": _fake_case_law_reporter_cite},
)


# ---------------------------------------------------------------------------
# 2. A citation clicked inside a PDF opens the cited case's PDF
# ---------------------------------------------------------------------------

RESOLVED: dict = {}          # cite -> url the resolver "finds"
FETCHED: dict = {}           # url  -> (bytes, final url)
CL_ITEMS: dict = {}          # cite -> the CourtListener cluster record


def _fetch_pdf_bytes(url, client=None, timeout=30):
    return FETCHED.get(url)


def _cl_item_for_citation(client, cite, name=""):
    CL_LOOKUPS.append((cite, name))
    return CL_ITEMS.get(cite)


CL_LOOKUPS: list = []        # (cite, name) the fallback resolved a cluster by
CL_PARTS: dict = {}          # cluster id -> (parts, blocks, plain text, meta)
CL_CAPTIONS: dict = {}       # tuple(blocks) -> the caption they read as


def _assemble_case_parts(client, item):
    return CL_PARTS.get(item.get("cluster_id"), ([], [], "", {}))


#: The real fallback, and the real reading of the Bluebook pieces off what it
#: returns — so a test exercises the rules rather than a restatement of them.
CASE_PDF_TEXT_SOURCE = _load_dataclass("_CasePdfTextSource")
COURTLISTENER_TEXT_SOURCE = _load_function(
    "_courtlistener_text_source",
    {"_CasePdfTextSource": CASE_PDF_TEXT_SOURCE,
     "_cl_item_for_citation": _cl_item_for_citation,
     "_assemble_case_parts": _assemble_case_parts,
     "_assemble_case_text": lambda client, item: ""})

CASE_LAW_TEXTS: dict = {}    # cite -> the static.case.law text found for it
CASE_LAW_SCANS: dict = {}    # scan url -> the text that scan's own file holds
CASE_LAW_LOOKUPS: list = []  # (scan url, cites, name, date) each lookup got


def _case_law_text_for_scan(pdf_url, cites, name="", date=""):
    """static.case.law's text of the pages the scan at *pdf_url* prints.

    The real one is exercised against real CAP URLs in
    ``test_bluebook_citations``; here it only has to record what it was asked,
    so a test can see that the scan on screen — not the first citation
    CourtListener happens to list — is what the lookup was keyed on.
    """
    CASE_LAW_LOOKUPS.append((pdf_url, tuple(cites), name, date))
    if pdf_url in CASE_LAW_SCANS:
        return CASE_LAW_SCANS[pdf_url]
    for cite in cites:
        source = CASE_LAW_TEXTS.get(cite)
        if source is not None:
            return source
    return None


def _case_law_source(text="The static.case.law text", parts=("cap part",),
                     blocks=("PEARSON v. DODD",), item=None):
    """A CAP source shaped the way the real one comes back."""
    return CASE_PDF_TEXT_SOURCE(
        "case_law", "Text", "static.case.law",
        "https://static.case.law/us/410/cases/0113-01.json",
        text, dict(item or {}), list(parts), list(blocks), None)
CL_TEXT_RECORD = _load_function(
    "_cl_text_record",
    {"_scholar_caption_name": lambda blocks: CL_CAPTIONS.get(tuple(blocks), ""),
     "_cluster_citations_to_strings": lambda cites: [str(c) for c in cites]})


class _FakeReader:
    """A ``_ScholarTextWindow`` built inside the viewer by its T button."""

    built: list = []

    def __init__(self, host, app, url, html, **kw):
        self.host, self.app, self.url, self.html, self.kw = (
            host, app, url, html, kw)
        _FakeReader.built.append(self)


def _bluebook_display_name(item):
    TITLED.append(item)
    name = item.get("caseName") or ""
    cites = item.get("citation") or []
    return f"{name}, {cites[0]}" if name and cites else name


TITLED: list = []            # every item a window title was built from


SAVED_CONFIG: dict = {}    # what _load_config returns
HEADER_COURTS: dict = {}   # opinion html -> the court its header names


def _header_court(blocks):
    """HEADER_COURTS' court for a page — or, stored as a callable, what
    reading it does (raises, say)."""
    court = HEADER_COURTS.get(blocks, "")
    return court() if callable(court) else court


#: The scans kept beside bookmarks: url -> bytes, as the app's cache would
#: hold them.
BOOKMARKED_SCANS: dict = {}

#: What bookmarks a cited scan's window — the real class, over that cache.
CITED_SCAN_BOOKMARK = _load_dataclass("_CitedScanBookmark", {
    "tk": _Tk, "re": re,
    "_bookmark_pdf_save": lambda url, data: BOOKMARKED_SCANS.__setitem__(
        url, data) or True,
    "_bookmark_pdf_delete": lambda url: BOOKMARKED_SCANS.pop(url, None),
})

class _ScanOnlyRace:
    """Stands in for _CaseOpenRace here, where workers run inline: the scan
    lookup reports to it, and it opens what that lookup finds as the lookup
    always has — the race between the scan and the text, which needs real
    threads, is test_case_open_race's to check."""

    def __init__(self, app, parent, *, cite, name="", pin="", year="",
                 client=None, fetcher=None, watch=None, status=None,
                 action=("cite", ""), snippet="", scholar_url="", item=None,
                 on_nothing=None, prefer_scan=False):
        self.app, self.parent, self.cite, self.name = app, parent, cite, name
        self.pin, self.client, self.watch = pin, client, watch
        self.status, self.action, self.snippet = status, action, snippet
        self.on_nothing = on_nothing

    def start(self):
        pass

    def official_scan(self):
        return None

    def case_law_scan(self):
        return None

    def cl_item(self):
        return self.app._cited_case_pdf_item(self.client, self.cite,
                                             self.name)

    def use_us_cite(self, item):
        pass

    def may_ask(self):
        return True

    def scan_found(self, data, url, meta, item, shown_name):
        self.app._show_cited_case_pdf(
            self.parent, data, url, self.cite, self.pin, shown_name,
            self.action, self.snippet, self.status, cl_item=item,
            watch=self.watch, page_meta=meta)

    def scan_missing(self, reason=""):
        if self.on_nothing is not None:
            self.watch.to_text(reason)
            self.on_nothing()

    def scan_refused(self, message):
        self.watch.fail(message)


APP_NS = _load(
    "CourtListenerGUI",
    ["open_cited_case_pdf", "_cited_case_pdf_item", "_show_cited_case_pdf",
     "_cited_pdf_window_closed", "_warm_case_text",
     "_request_cited_pdf_analysis", "_cited_filename_item",
     "_jump_to_pin", "_scan_cite_for", "_retitle_cited_pdf",
     "_embed_cited_case_text", "_cited_citation_override",
     "_describe_warmed_case", "_save_cited_pdf", "_print_cited_pdf",
     "_reopen_cited_scan"],
    {"_CitedScanBookmark": CITED_SCAN_BOOKMARK,
     "_bookmark_pdf_read": lambda url: BOOKMARKED_SCANS.get(url),
     "_citation_link_name": lambda snippet, cite="": snippet.strip(),
     # The court a fetched page's header names, keyed like CAPTIONS.
     "_scholar_header_court": lambda blocks: _header_court(blocks),
     # The citations the reader has saved: none, unless a test saves one.
     "_load_config": lambda: dict(SAVED_CONFIG),
     "citation_identity_keys": citation_identity_keys,
     "find_override": find_override,
     "_cl_item_for_citation": _cl_item_for_citation,
     "_fetch_pdf_bytes": _fetch_pdf_bytes,
     "_shared_pdf_bytes": _fetch_pdf_bytes,
     "_CaseOpenRace": _ScanOnlyRace,
     "_scan_opinion_links_ahead": lambda html: None,
     "_us_reports_cite": lambda cite: (
         "5 U.S. 137" if "Cranch" in cite else ""),
     # The official-series form: the same stand-in for the early Supreme
     # Court reporters, and the real Massachusetts mapping.
     "_official_series_cites": lambda cite: (
         ["5 U.S. 137"] if "Cranch" in cite
         else citations.state_nominative_cites(cite)),
     "_pin_display": lambda pin: pin,
     "_is_us_reports_pdf": lambda url: "usrep" in (url or "").lower(),
     # A load's progress goes to its status window (see _LoadWatch); here,
     # only the watch the app hands out records what it was told.
     "_watching": lambda watch: contextlib.nullcontext(watch),
     "_load_step": lambda text: None,
     "_measure_pdf_pages": lambda data: MEASURED.get(data),
     "_PdfPane": type("_PdfPane", (), {"_MARGIN": 18}),
     "_FloatingPdfWindow": lambda *a, **kw: _FakeViewer(*a, **kw),
     "_follow_brief_action": lambda *a, **kw: TEXT_OPENS.append((a, kw)),
     "_open_citation_in_browser": lambda *a: None,
     "_SCHOLAR_AVAILABLE": True,
     # The Federal Cases pattern, as the module defines it.
     "_FED_CAS_CITE_RE": _module_value("_FED_CAS_CITE_RE"),
     "_citation_search_variants": lambda cite: (cite,),
     "_case_law_text_for_scan": _case_law_text_for_scan,
     "_courtlistener_text_source": COURTLISTENER_TEXT_SOURCE,
     # None of these scans is a Supreme Court slip opinion with its own
     # text to fall back on.
     "_slip_text_source": lambda data, url, item=None: None,
     "_cl_text_record": CL_TEXT_RECORD,
     "_ScholarTextWindow": _FakeReader,
     "_bluebook_display_name": _bluebook_display_name,
     "_extract_pdf_text_and_style": lambda data, **_kw: (
         [[("c", (0, 0, 1, 1))]], []),
     "_yield_to_ui": lambda: None,
     "_citation_links_from_visible_pdf_text": lambda d, p, i: ({0: ["x"]}, set()),
     "slip_opinion": mock.Mock(detect_sections=lambda pages: []),
     "_normalized_us_cite": _NORMALIZED_US_CITE,
     "_scan_citation_item": SCAN_CITATION_ITEM,
     "_scan_page_for_pin": SCAN_PAGE_FOR_PIN,
     # The reader's caption reader, which names a case the way the reports do.
     "parse_opinion_blocks": lambda html: html,
     "_scholar_caption_name": lambda blocks: CAPTIONS.get(blocks, ""),
     # The abbreviator is its own (well-tested) machinery; the caption goes
     # through untouched here.
     "abbreviate_case_name": lambda name, **_kw: name,
     "_state_of_court": lambda *_a: "",
     "_scholar_body_text": lambda blocks: "",
     "_case_law_reporter_cite": _fake_case_law_reporter_cite,
     "_static_case_law_url": _fake_static_case_law_url,
     "_cluster_citations_to_strings": lambda cites: [str(c) for c in cites],
     "_is_redacted_case_pdf": lambda url: "case.law" in (url or ""),
     "_build_default_filename": lambda item: FILENAMES.append(item) or "NAME",
     "_named_temp_pdf_path": lambda stem: str(
         pathlib.Path(tempfile.gettempdir()) / f"{stem}.pdf"),
     "_write_output_pdf": WRITE_OUTPUT_PDF,
     "_print_pdf_file": lambda parent, path, status: PRINTED.append(path),
     "_case_law_print_citation": lambda *a, **kw: "HEADER",
     "filedialog": mock.Mock(),
     "messagebox": mock.Mock(),
     # The real reading of the name and year the text lookup is given.
     "_case_name_and_year": _load_function("_case_name_and_year"),
     # Which of a page's several cases to ask about (the real rule); the
     # asking itself, a test's to answer (see PICKED).
     "_choices_to_ask": _load_function("_choices_to_ask"),
     "_choose_case_law_page_opinion": (
         lambda parent, opinions, title, bring_to_front=None:
         ASKED.append((title, [o.name for o in opinions]))
         or (PICKED[0](opinions) if PICKED else None)),
     "_case_law_opinion_name": lambda opinion: opinion.name,
     "_CaseLawPdfChoice": lambda **kw: SimpleNamespace(**kw),
     "_is_the_named_case": lambda name, other: name == other,
     # The official forms, with no ambiguous reporter here to choose among
     # by name; and no state court's scan to keep the Constitution from.
     "_official_series_for": lambda cite, name="", year="": (
         ["5 U.S. 137"] if "Cranch" in cite
         else citations.state_nominative_cites(cite)),
     "_state_court_cite": lambda cite: False,
     },
)

#: What the reader was asked (title, the cases offered), and how a test has
#: them answer: PICKED[0](opinions) -> the one chosen, or None to cancel.
ASKED: list = []
PICKED: list = []

TEXT_OPENS: list = []
FILENAMES: list = []
PRINTED: list = []
CAPTIONS: dict = {}          # opinion html -> what its caption reads as
MEASURED: dict = {}          # pdf bytes -> the pages' measurements


class _FakeWatch:
    """A slow document's status window (_LoadWatch), recording what the load
    told it."""

    def __init__(self, parent, label, cite=""):
        self.parent, self.label, self.cite = parent, label, cite
        self.cancelled = False
        self.claimed = False
        self.geometry = None        # where its window stood, if it showed
        self.told: list = []        # ("to_text" | "fail" | "finish", text)

    def hand_off(self):
        self.told.append(("hand_off", ""))
        return self.geometry

    def to_text(self, reason):
        self.told.append(("to_text", reason))

    def fail(self, message):
        self.told.append(("fail", message))

    def finish(self):
        self.told.append(("finish", ""))


class _FakeViewer:
    opened: list = []

    def __init__(self, parent, data, url, title, **kw):
        self.parent, self.data, self.url, self.title = parent, data, url, title
        self.kw = kw
        self._win = f"toplevel of {title}"   # what a click here starts from
        self.surfaced = 0
        self.analyses = []
        self.scrolled: list = []
        self.at_page = None
        self.living = True
        _FakeViewer.opened.append(self)

    def surface(self):
        self.surfaced += 1

    def set_title(self, title):
        self.title = title

    def current_title(self):
        return self.title

    def apply_analysis(self, result):
        self.analyses.append(result)

    def alive(self):
        return self.living

    def viewport_page(self):
        return self.at_page

    def scroll_to_page(self, page, y_pt=None):
        self.scrolled.append(page)
        self.at_page = page


class _FakeHost:
    def winfo_toplevel(self):
        return self


class _App:
    def __init__(self, token="tok", resolves=True, scholar=True):
        self.root = _FakeHost()
        self._token_var = mock.Mock()
        self._token_var.get.return_value = token
        self._cited_pdf_windows = set()
        self.resolved_items = []
        self._resolves = resolves
        self._scholar_has_it = scholar   # whether Scholar carries the case
        self.scholar_calls = []
        self.scholar_names = []          # (case_name, year) each call had
        self._describe_warmed_case = APP_NS["_describe_warmed_case"]
        # A static method on the app: lifted, it is a plain function.
        self._cited_citation_override = APP_NS["_cited_citation_override"]
        for name in ("open_cited_case_pdf", "_cited_case_pdf_item",
                     "_show_cited_case_pdf", "_cited_pdf_window_closed",
                     "_warm_case_text", "_request_cited_pdf_analysis",
                     "_cited_filename_item", "_save_cited_pdf",
                     "_print_cited_pdf", "_jump_to_pin", "_scan_cite_for",
                     "_retitle_cited_pdf", "_embed_cited_case_text",
                     "_reopen_cited_scan"):
            setattr(self, name, APP_NS[name].__get__(self))
        self.bookmarks: dict = {}        # key -> descriptor
        self.touched: list = []          # keys marked accessed-now
        self.root_status: list = []

    # --- the bookmarks list ---
    def is_bookmarked(self, key):
        return key in self.bookmarks

    def add_bookmark(self, desc):
        self.bookmarks[desc["key"]] = desc

    def remove_bookmark(self, key):
        return self.bookmarks.pop(key, None)

    def touch_bookmark(self, key):
        self.touched.append(key)

    def _safe_root_status(self, text):
        self.root_status.append(text)

    # --- collaborators ---
    def watch_load(self, parent, label, cite=""):
        self.watches = getattr(self, "watches", []) + [
            _FakeWatch(parent, label, cite)]
        return self.watches[-1]

    def _get_client(self):
        return "client"

    def _resolve_pdf_url(self, client, item):
        self.resolved_items.append(item)
        if not self._resolves:
            return None
        for cite in item.get("citation") or []:
            if cite in RESOLVED:
                return RESOLVED[cite]
        return None

    def _post_root(self, fn):
        fn()

    def _get_scholar(self):
        def fetch(cite, case_name="", year=""):
            self.scholar_calls.append(cite)
            self.scholar_names.append((case_name, year))
            if not self._scholar_has_it:
                return None
            return (f"https://scholar.test/{cite}", "<html>")

        return mock.Mock(fetch_by_citation=fetch)


class CitedCasePdfTests(unittest.TestCase):
    def setUp(self):
        RESOLVED.clear(); FETCHED.clear(); CL_ITEMS.clear()
        TEXT_OPENS.clear(); _FakeViewer.opened.clear(); _Thread.started.clear()
        RESOLVED["410 U.S. 113"] = "https://loc.test/usrep410113.pdf"
        FETCHED["https://loc.test/usrep410113.pdf"] = (
            b"%PDF-1", "https://loc.test/usrep410113.pdf")
        self.app = _App()
        self.status = []

    def _click(self, action=("cite", "410 U.S. 113"), snippet="Roe v. Wade",
               fallback=None):
        return self.app.open_cited_case_pdf(
            _FakeHost(), action, snippet, self.status.append,
            fallback=fallback)

    def test_a_case_citation_opens_the_cited_case_s_pdf(self):
        self.assertTrue(self._click())
        self.assertEqual(len(_FakeViewer.opened), 1)
        self.assertEqual(_FakeViewer.opened[0].data, b"%PDF-1")

    def test_the_viewer_is_named_for_the_case_and_cite(self):
        self._click()
        self.assertEqual(_FakeViewer.opened[0].title,
                         "Roe v. Wade — 410 U.S. 113")

    def test_it_comes_to_the_front(self):
        self._click()
        self.assertEqual(_FakeViewer.opened[0].surfaced, 1)

    MINE = "Roe v. Wade, 410 U.S. 113 (1973) (as I cite it)"

    def _save(self, citation):
        SAVED_CONFIG.clear()
        if citation:
            SAVED_CONFIG["citation_overrides"] = update_overrides(
                {}, citation_identity_keys({}, "410 U.S. 113"), citation)

    def test_a_citation_the_reader_saved_names_the_viewer(self):
        self._save(self.MINE)
        try:
            self._click()
        finally:
            self._save("")
        self.assertEqual(_FakeViewer.opened[0].title, self.MINE)

    def test_an_edit_in_the_text_beside_it_renames_the_viewer(self):
        self._click()
        viewer = _FakeViewer.opened[0]
        automatic = viewer.title
        self._save(self.MINE)
        try:
            viewer.kw["on_citation_edited"]()
            self.assertEqual(viewer.title, self.MINE)
        finally:
            self._save("")
        # Restoring automatic Bluebooking restores the automatic name.
        viewer.kw["on_citation_edited"]()
        self.assertNotEqual(viewer.title, self.MINE)
        self.assertIn("410 U.S. 113", viewer.title)
        self.assertNotEqual(automatic, "")

    def test_a_us_reports_scan_gets_the_roomier_margin(self):
        self._click()
        self.assertEqual(_FakeViewer.opened[0].kw["margin"], 18 * 3)

    def test_the_app_holds_the_window_until_it_closes(self):
        self._click()
        window = _FakeViewer.opened[0]
        self.assertIn(window, self.app._cited_pdf_windows)
        self.app._cited_pdf_window_closed(window)
        self.assertNotIn(window, self.app._cited_pdf_windows)

    def test_the_pin_is_reported_but_the_scan_is_what_opens(self):
        self._click(action=("cite", "410 U.S. 113@153"))
        self.assertEqual(len(_FakeViewer.opened), 1)
        self.assertTrue(any("153" in s for s in self.status))

    def test_and_the_scan_opens_at_the_page_the_pin_names(self):
        self._click(action=("cite", "410 U.S. 113@153"))
        # 153 is the 41st page of a scan that starts at 113.
        self.assertEqual(_FakeViewer.opened[0].scrolled[0], 40)

    def test_a_citation_with_no_pin_opens_at_the_start(self):
        self._click(action=("cite", "410 U.S. 113"))
        self.assertEqual(_FakeViewer.opened[0].scrolled, [])

    def test_a_pin_in_another_reporter_is_not_counted_against_this_one(self):
        # The link cites the Supreme Court Reporter; the scan found for it is
        # the U.S. Reports, whose pages break somewhere else entirely.
        RESOLVED["93 S. Ct. 705"] = "https://loc.test/usrep410113.pdf"
        self._click(action=("cite", "93 S. Ct. 705@710"))
        self.assertEqual(_FakeViewer.opened[0].scrolled, [])

    def test_a_pin_opens_its_page_of_a_reporter_the_slug_table_lacks(self):
        # Wakely v. Hart, 6 Binn. 316, 318, followed out of Acevedo: the
        # file's own name says nothing ("binn" is no reporter in the table),
        # but it was found by the very citation clicked.
        url = "https://static.case.law/binn/6/case-pdfs/0316-01.pdf"
        RESOLVED["6 Binn. 316"] = url
        FETCHED[url] = (b"%PDF-binn", url)
        self._click(action=("cite", "6 Binn. 316@318"), snippet="Wakely v. Hart")
        self.assertEqual(_FakeViewer.opened[0].scrolled[0], 2)

    def test_but_not_a_parallel_reporter_s_pin_on_another_s_file(self):
        # "142 N. E. 583, 585" found only in the New York Reports' file: its
        # pages break elsewhere, so no page is guessed at.
        url = "https://static.case.law/ny/237/case-pdfs/0193-01.pdf"
        RESOLVED["142 N.E. 583"] = url
        FETCHED[url] = (b"%PDF-ny", url)
        self._click(action=("cite", "142 N.E. 583@585"), snippet="People v. Chiagles")
        self.assertEqual(_FakeViewer.opened[0].scrolled, [])

    def test_no_scan_anywhere_falls_back_to_the_text(self):
        self.app._resolves = False
        fallback = mock.Mock()
        self._click(fallback=fallback)
        self.assertEqual(_FakeViewer.opened, [])
        fallback.assert_called_once_with()

    # --- a slow document's status window (see _LoadWatch) ---

    def test_the_load_is_watched_under_the_case_s_name(self):
        self._click()
        (watch,) = self.app.watches
        self.assertEqual((watch.label, watch.cite),
                         ("Roe v. Wade, 410 U.S. 113", "410 U.S. 113"))

    def test_the_scan_opens_where_its_status_window_stood(self):
        watch_load = self.app.watch_load

        def slow(parent, label, cite=""):
            watch = watch_load(parent, label, cite)
            watch.geometry = "720x880+900+40"
            return watch

        self.app.watch_load = slow
        self._click()
        self.assertEqual(_FakeViewer.opened[0].kw["geometry"],
                         "720x880+900+40")
        self.assertIn(("hand_off", ""), self.app.watches[0].told)

    def test_or_where_the_viewer_would_have_gone_had_it_come_quickly(self):
        self._click()
        self.assertEqual(_FakeViewer.opened[0].kw["geometry"], "")

    def test_its_pages_are_measured_before_the_viewer_opens(self):
        MEASURED[b"%PDF-1"] = [(612.0, 792.0, (0.0, 0.0, 1.0, 1.0))]
        try:
            self._click()
        finally:
            MEASURED.clear()
        self.assertEqual(_FakeViewer.opened[0].kw["page_meta"],
                         [(612.0, 792.0, (0.0, 0.0, 1.0, 1.0))])

    def test_a_case_the_reader_stopped_waiting_for_is_let_go(self):
        watch_load = self.app.watch_load

        def given_up(parent, label, cite=""):
            watch = watch_load(parent, label, cite)
            watch.cancelled = True
            return watch

        self.app.watch_load = given_up
        fallback = mock.Mock()
        self._click(fallback=fallback)
        self.assertEqual(_FakeViewer.opened, [])
        fallback.assert_not_called()

    def test_no_scan_waits_on_the_text_that_falls_back_to(self):
        self.app._resolves = False

        def takes_it_up():
            self.app.watches[0].claimed = True   # as a text lookup does

        self._click(fallback=takes_it_up)
        told = self.app.watches[0].told
        self.assertEqual(told[0][0], "to_text")
        self.assertNotIn(("finish", ""), told)

    def test_a_fallback_that_will_not_say_how_it_ends_ends_the_wait(self):
        self.app._resolves = False
        self._click(fallback=mock.Mock())
        self.assertEqual([t for t, _ in self.app.watches[0].told],
                         ["to_text", "finish"])

    def test_no_scan_and_nothing_to_fall_back_to_says_so(self):
        self.app._resolves = False
        self._click()
        self.assertEqual(self.app.watches[0].told,
                         [("fail", "No scan of it could be found.")])

    def test_a_scan_that_will_not_download_falls_back_too(self):
        FETCHED.clear()
        fallback = mock.Mock()
        self._click(fallback=fallback)
        self.assertEqual(_FakeViewer.opened, [])
        fallback.assert_called_once_with()

    def test_a_statute_citation_is_not_ours_to_open(self):
        self.assertFalse(self._click(action=("statute", "42 U.S.C. 1983")))
        self.assertEqual(_FakeViewer.opened, [])

    def test_an_empty_citation_is_not_ours_either(self):
        self.assertFalse(self._click(action=("cite", "   ")))

    def test_the_text_is_fetched_while_the_reader_looks_at_the_scan(self):
        # So the case name on the strip opens a page already in hand.
        self._click()
        self.assertEqual(self.app.scholar_calls, ["410 U.S. 113"])

    # --- which case at the page the text lookup asks for ----------------
    # Two cases can begin on one reporter page (NetChoice, LLC v. Fitch shares
    # 145 S. Ct. 2658 with another case); the name is what tells Google
    # Scholar which is meant.

    def test_the_text_lookup_is_told_the_name_the_link_carries(self):
        self._click()
        self.assertEqual(self.app.scholar_names, [("Roe v. Wade", "")])

    def test_a_bare_cite_is_named_by_the_cluster_its_scan_came_from(self):
        # No caption on the link: the case whose pages are shown names it, so
        # the text beside the scan is of that case and not its page-mate.
        CL_ITEMS["410 U.S. 113"] = {
            "cluster_id": 1, "caseName": "Roe v. Wade",
            "citation": ["410 U.S. 113"], "dateFiled": "1973-01-22"}
        self._click(snippet="")
        self.assertEqual(self.app.scholar_names, [("Roe v. Wade", "1973")])

    def test_a_name_the_caller_has_on_its_own_is_used(self):
        # A Google Scholar link, or a search result, has the caption already —
        # not a highlighted "Name, cite" to read it from.
        CL_LOOKUPS.clear()
        RESOLVED["145 S. Ct. 2658"] = "https://loc.test/sct2658.pdf"
        FETCHED["https://loc.test/sct2658.pdf"] = (
            b"%PDF-3", "https://loc.test/sct2658.pdf")
        self.app.open_cited_case_pdf(
            _FakeHost(), ("cite", "145 S. Ct. 2658"), "", self.status.append,
            name="<mark>NetChoice</mark>, LLC v. Fitch")
        self.assertEqual(CL_LOOKUPS,
                         [("145 S. Ct. 2658", "NetChoice, LLC v. Fitch")])
        self.assertEqual(self.app.scholar_names,
                         [("NetChoice, LLC v. Fitch", "")])
        self.assertEqual(_FakeViewer.opened[0].title,
                         "NetChoice, LLC v. Fitch — 145 S. Ct. 2658")

    def test_a_citation_inside_that_scan_opens_its_case_too(self):
        # Following citations from scan to scan, not just the first hop.
        RESOLVED["381 U.S. 479"] = "https://loc.test/usrep381479.pdf"
        FETCHED["https://loc.test/usrep381479.pdf"] = (
            b"%PDF-2", "https://loc.test/usrep381479.pdf")
        self._click()
        _FakeViewer.opened[0].kw["on_cite"](
            ("cite", "381 U.S. 479"), "Griswold v. Connecticut")
        self.assertEqual(len(_FakeViewer.opened), 2)
        self.assertEqual(_FakeViewer.opened[1].data, b"%PDF-2")

    def test_a_statute_inside_that_scan_opens_as_well(self):
        # The scan lookup has no answer for anything but a case, so a statute,
        # a rule or a regulation clicked on these pages has to go on to the
        # ordinary opener rather than fall on the floor.
        self._click()
        _FakeViewer.opened[0].kw["on_cite"](
            ("usc", "42:1983:"), "42 U.S.C. § 1983")
        self.assertEqual(len(_FakeViewer.opened), 1)   # no second scan
        self.assertEqual([args[2] for args, _kw in TEXT_OPENS],
                         [("usc", "42:1983:")])

    def test_and_so_do_the_other_sources_that_are_not_cases(self):
        self._click()
        for action in (("statpdf", "https://govinfo.test/statute/14/27"),
                       ("cfr", "29:1614.105:a,1"),
                       ("engrep", "156:145")):
            _FakeViewer.opened[0].kw["on_cite"](action, "")
        self.assertEqual([args[2] for args, _kw in TEXT_OPENS],
                         [("statpdf", "https://govinfo.test/statute/14/27"),
                          ("cfr", "29:1614.105:a,1"),
                          ("engrep", "156:145")])

    def test_a_statute_click_starts_from_the_scan_it_was_clicked_in(self):
        # Not from the window that scan was opened out of, which may be gone.
        self._click()
        window = _FakeViewer.opened[0]
        window.kw["on_cite"](("usc", "42:1983:"), "")
        self.assertIs(TEXT_OPENS[0][0][1], window._win)

    def test_the_viewer_can_save_and_print_the_scan(self):
        self._click()
        kw = _FakeViewer.opened[0].kw
        self.assertIsNotNone(kw["on_save"])
        self.assertIsNotNone(kw["on_print"])

    def test_the_scan_gets_clickable_citations_and_search(self):
        self._click()
        analysis = _FakeViewer.opened[0].analyses[0]
        self.assertEqual(analysis["url"],
                         "https://loc.test/usrep410113.pdf")
        self.assertIn(0, analysis["links"])


_ORDERS_PAGE_PDF = "https://static.case.law/us/498/case-pdfs/0807-01.pdf"


class _OrdersApp(_App):
    """The resolver meeting 498 U.S. 807, where eleven cases begin: showing
    the page itself when it is all orders (``page=True``), else unable to
    choose among them."""

    def __init__(self, page=False, **kw):
        super().__init__(**kw)
        self.page = page
        self.toasts: list = []

    def _resolve_pdf_url(self, client, item):
        self.resolved_items.append(dict(item))
        if self.page:
            item["_orders_page"] = True
            return _ORDERS_PAGE_PDF
        item["_page_mates"] = 11
        return None

    def _spotlight_notify(self, message, duration_ms=4000):
        self.toasts.append(message)


_APPX = "18 F. App'x 81"
_MCCARTHY = "https://static.case.law/f-appx/18/case-pdfs/0081-01.pdf"
_JOHNSON = "https://static.case.law/f-appx/18/case-pdfs/0081-02.pdf"


class _PageMatesApp(_App):
    """The resolver meeting 18 F. App'x 81, where United States v. McCarthy
    and Johnson v. United States both begin, with nothing to say which: it
    offers both, the first's scan to hand (see _case_law_pdf_choices_for_cites
    and _us_reports_page_choice)."""

    def _resolve_pdf_url(self, client, item):
        self.resolved_items.append(dict(item))
        item["_case_law_pdf_choices"] = [
            SimpleNamespace(cite=_APPX, url=url, pick=True,
                            opinion=SimpleNamespace(url=url, name=name))
            for url, name in ((_MCCARTHY, "United States v. McCarthy"),
                              (_JOHNSON, "Johnson v. United States"))]
        return _MCCARTHY


class PageMatesAskTests(unittest.TestCase):
    """A citation several cases begin at, clicked in Chrome or in a case on
    screen with nothing to say which, asks — it opened the first."""

    def setUp(self):
        RESOLVED.clear(); FETCHED.clear(); CL_ITEMS.clear()
        TEXT_OPENS.clear(); _FakeViewer.opened.clear(); _Thread.started.clear()
        ASKED.clear(); PICKED.clear()
        FETCHED[_MCCARTHY] = (b"%PDF-mccarthy", _MCCARTHY)
        FETCHED[_JOHNSON] = (b"%PDF-johnson", _JOHNSON)
        self.app = _PageMatesApp()
        self.app._bring_to_front = lambda win: None

    def _click(self):
        return self.app.open_cited_case_pdf(
            _FakeHost(), ("cite", _APPX), _APPX, lambda _s: None,
            fallback=lambda: None)

    def test_the_reader_picks_and_that_case_opens(self):
        PICKED.append(lambda opinions: opinions[1])
        self.assertTrue(self._click())
        self.assertEqual(ASKED, [(_APPX, ["United States v. McCarthy",
                                          "Johnson v. United States"])])
        (viewer,) = _FakeViewer.opened
        self.assertEqual(viewer.data, b"%PDF-johnson")
        self.assertIn("Johnson v. United States", viewer.title)

    def test_cancelling_opens_nothing(self):
        self.assertTrue(self._click())          # no answer: cancelled
        self.assertEqual(len(ASKED), 1)
        self.assertEqual(_FakeViewer.opened, [])
        self.assertEqual(self.app.watches[-1].told, [("finish", "")])

    def test_a_case_settled_by_its_name_is_not_asked_about(self):
        app = self.app

        def settled(client, item):
            app.resolved_items.append(dict(item))
            item["_case_law_pdf_choices"] = [
                SimpleNamespace(cite=_APPX, url=_JOHNSON, pick=False,
                                opinion=None)]
            return _JOHNSON

        app._resolve_pdf_url = settled
        self._click()
        self.assertEqual(ASKED, [])
        self.assertEqual(_FakeViewer.opened[0].data, b"%PDF-johnson")


class CitedScanBookmarkTests(unittest.TestCase):
    """A case's scan, opened from a citation, bookmarked while its pages are
    what is on screen — and reopened from the bookmark."""

    URL = "https://loc.test/usrep410113.pdf"

    def setUp(self):
        RESOLVED.clear(); FETCHED.clear(); CL_ITEMS.clear()
        TEXT_OPENS.clear(); _FakeViewer.opened.clear(); _Thread.started.clear()
        BOOKMARKED_SCANS.clear()
        RESOLVED["410 U.S. 113"] = self.URL
        FETCHED[self.URL] = (b"%PDF-1", self.URL)
        CL_ITEMS["410 U.S. 113"] = {"caseName": "Roe v. Wade",
                                    "cluster_id": 108713,
                                    "citation": ["410 U.S. 113"]}
        self.app = _App()
        self.app.open_cited_case_pdf(
            _FakeHost(), ("cite", "410 U.S. 113"), "Roe v. Wade",
            lambda _s: None)
        self.viewer = _FakeViewer.opened[0]
        self.owner = self.viewer.kw["bookmarks"]

    def test_the_window_can_bookmark_the_scan_it_shows(self):
        desc = self.owner._bookmark_descriptor()
        self.assertEqual(desc["key"], f"pdf:{self.URL}")
        self.assertEqual(desc["noun"], "case")
        payload = desc["payload"]
        self.assertEqual(payload["type"], "cited")
        self.assertEqual(payload["url"], self.URL)
        self.assertEqual(payload["cite"], "410 U.S. 113")
        self.assertEqual(payload["name"], "Roe v. Wade")
        self.assertEqual(payload["cl_item"]["cluster_id"], 108713)

    def test_it_is_named_as_the_window_is_now(self):
        self.viewer.set_title("Roe v. Wade, 410 U.S. 113 (1973)")
        self.assertEqual(self.owner._bookmark_descriptor()["label"],
                         "Roe v. Wade, 410 U.S. 113 (1973)")

    def test_bookmarking_keeps_the_scan_and_unbookmarking_lets_it_go(self):
        self.owner._toggle_bookmark()
        self.assertIn(f"pdf:{self.URL}", self.app.bookmarks)
        self.assertEqual(BOOKMARKED_SCANS[self.URL], b"%PDF-1")
        self.owner._toggle_bookmark()
        self.assertEqual(self.app.bookmarks, {})
        self.assertNotIn(self.URL, BOOKMARKED_SCANS)

    def test_the_bookmark_reopens_the_saved_scan_in_the_same_window(self):
        self.owner._toggle_bookmark()
        payload = self.app.bookmarks[f"pdf:{self.URL}"]["payload"]
        FETCHED.clear()          # offline: only the saved copy is to hand
        _FakeViewer.opened.clear()
        self.app._reopen_cited_scan(payload, "Roe v. Wade")
        self.assertEqual(len(_FakeViewer.opened), 1)
        reopened = _FakeViewer.opened[0]
        self.assertEqual(reopened.data, b"%PDF-1")
        self.assertEqual(reopened.url, self.URL)
        self.assertIn("bookmarks", reopened.kw)
        self.assertEqual(self.app.touched, [f"pdf:{self.URL}"])

    def test_without_the_saved_copy_the_scan_is_fetched_again(self):
        payload = self.owner._bookmark_descriptor()["payload"]
        _FakeViewer.opened.clear()
        self.app._reopen_cited_scan(payload, "Roe v. Wade")
        self.assertEqual([v.data for v in _FakeViewer.opened], [b"%PDF-1"])

    def test_and_failing_that_the_citation_is_looked_up_again(self):
        payload = self.owner._bookmark_descriptor()["payload"]
        payload["url"] = "https://gone.test/scan.pdf"
        _FakeViewer.opened.clear()
        self.app._reopen_cited_scan(payload, "Roe v. Wade")
        self.assertEqual([v.url for v in _FakeViewer.opened], [self.URL])


class OrdersPageTests(unittest.TestCase):
    """A U.S. Reports page of orders names no one case.  Unless something
    picks one out, the page opens as the page — never as the case whose order
    happens to come first, which is how Acevedo's own grant opened Carnival
    Cruise Lines — and a page that is not all orders opens nothing."""

    def setUp(self):
        RESOLVED.clear(); FETCHED.clear(); CL_ITEMS.clear()
        TEXT_OPENS.clear(); _FakeViewer.opened.clear(); _Thread.started.clear()
        FETCHED[_ORDERS_PAGE_PDF] = (b"%PDF-orders", _ORDERS_PAGE_PDF)
        self.app = _OrdersApp()
        self.status: list = []
        self.fell_back: list = []

    def _click(self, snippet="", context_name=""):
        return self.app.open_cited_case_pdf(
            _FakeHost(), ("cite", "498 U.S. 807"), snippet,
            self.status.append, fallback=lambda: self.fell_back.append(1),
            context_name=context_name)

    def test_a_page_of_orders_opens_named_for_no_case(self):
        self.app = _OrdersApp(page=True)
        self._click()
        (viewer,) = _FakeViewer.opened
        self.assertEqual(viewer.data, b"%PDF-orders")
        self.assertEqual(viewer.title, "498 U.S. 807")
        # No text behind it (any would be one order's), none fetched, and
        # nothing to rename it later.
        self.assertIsNone(viewer.kw["on_build_text"])
        self.assertEqual(self.app.scholar_calls, [])
        self.assertEqual(self.fell_back, [])

    def test_nor_named_for_a_case_the_link_names_but_the_page_lacks(self):
        self.app = _OrdersApp(page=True)
        self._click(snippet="Doe v. Roe")
        self.assertEqual(_FakeViewer.opened[0].title, "498 U.S. 807")

    def test_a_page_not_all_orders_opens_nothing_and_says_why(self):
        self.assertTrue(self._click())
        self.assertEqual(self.fell_back, [])     # no text to guess with
        self.assertEqual(_FakeViewer.opened, [])
        # Said by the load: in its window, or to the reader directly when it
        # never showed (see _LoadWatch.fail) — and only once.
        self.assertEqual(
            self.app.watches[-1].told,
            [("fail", "11 cases begin at 498 U.S. 807 — can't tell which, "
                      "so nothing opened")])
        self.assertEqual(self.app.toasts, [])

    def test_a_page_whose_cases_are_known_asks_which(self):
        # 71 U.S. 2: Brobst v. Brobst and Ex parte Milligan both begin there
        # — no page of orders, and nothing to say which: the reader picks,
        # where nothing used to open.
        brobst = SimpleNamespace(
            url="https://static.case.law/us/71/case-pdfs/0002-01.pdf",
            name="Brobst v. Brobst")
        milligan = SimpleNamespace(
            url="https://static.case.law/us/71/case-pdfs/0002-02.pdf",
            name="Ex parte Milligan")
        app = self.app
        app._bring_to_front = lambda win: None

        def resolve(client, item):
            app.resolved_items.append(dict(item))
            item["_page_mates"] = 2
            item["_page_mate_opinions"] = [brobst, milligan]
            return None

        app._resolve_pdf_url = resolve
        FETCHED[milligan.url] = (b"%PDF-milligan", milligan.url)
        ASKED.clear()
        PICKED.clear()
        PICKED.append(lambda opinions: opinions[1])
        try:
            self.assertTrue(self._click())
        finally:
            PICKED.clear()
        self.assertEqual(ASKED, [("498 U.S. 807",
                                  ["Brobst v. Brobst", "Ex parte Milligan"])])
        (viewer,) = _FakeViewer.opened
        self.assertEqual(viewer.data, b"%PDF-milligan")
        self.assertIn("Ex parte Milligan", viewer.title)
        self.assertNotIn("fail", [k for k, _m in app.watches[-1].told])

    def test_a_citation_typed_on_its_own_asks_for_the_order(self):
        # Spotlight passes ask_orders; a link in a case does not.
        self.app.open_cited_case_pdf(
            _FakeHost(), ("cite", "498 U.S. 807"), "", self.status.append,
            fallback=lambda: None, ask_orders=True)
        self.assertTrue(self.app.resolved_items[-1].get("_ask_orders"))
        self._click()
        self.assertNotIn("_ask_orders", self.app.resolved_items[-1])

    def test_the_case_read_in_is_offered_when_the_link_names_none(self):
        self._click(context_name="California v. Acevedo")
        self.assertEqual(self.app.resolved_items[-1]["_context_name"],
                         "California v. Acevedo")

    def test_but_not_over_a_name_the_link_carries(self):
        self._click(snippet="Carnival Cruise Lines, Inc. v. Shute",
                    context_name="California v. Acevedo")
        self.assertNotIn("_context_name", self.app.resolved_items[-1])


class CitedCaseFilenameTests(unittest.TestCase):
    """What a cited case's scan is saved and printed as."""

    def setUp(self):
        FILENAMES.clear(); PRINTED.clear()
        self.app = _App()
        self.named = {"data": b"%PDF", "url": "https://loc.test/usrep410113.pdf",
                      "cite": "410 U.S. 113", "name": "Roe v. Wade",
                      "record": None}

    def test_before_the_text_arrives_the_citation_names_the_file(self):
        item = self.app._cited_filename_item(self.named)
        self.assertEqual(item["caseName"], "Roe v. Wade")
        self.assertEqual(item["citation"], ["410 U.S. 113"])

    def test_the_opinion_text_supplies_the_bluebook_pieces(self):
        # A citation and a link caption cannot give a court or a date; the
        # fetched opinion can.
        self.named["record"] = {
            "name": "Roe v. Wade", "cites": ["410 U.S. 113", "93 S. Ct. 705"],
            "date_filed": "1973-01-22", "year": "1973", "court": "scotus",
        }
        item = self.app._cited_filename_item(self.named)
        self.assertEqual(item["caseName"], "Roe v. Wade")
        self.assertEqual(item["dateFiled"], "1973-01-22")
        self.assertEqual(item["court_id"], "scotus")
        self.assertIn("93 S. Ct. 705", item["citation"])

    def test_a_year_alone_still_dates_the_file(self):
        self.named["record"] = {"name": "Roe", "cites": [], "year": "1973"}
        self.assertEqual(
            self.app._cited_filename_item(self.named)["dateFiled"],
            "1973-01-01")

    def test_a_us_reports_scan_is_filed_under_the_pages_it_prints(self):
        self.named["record"] = {"name": "Roe",
                                "cites": ["93 S. Ct. 705", "410 U.S. 113"]}
        item = self.app._cited_filename_item(self.named)
        self.assertEqual(item["_us_reports_cite"], "410 U.S. 113")

    def test_another_reporter_s_scan_is_not(self):
        self.named.update(url="https://static.case.law/f2d/500/0123-01.pdf",
                          record={"name": "Smith", "cites": ["500 F.2d 123"]})
        self.assertNotIn("_us_reports_cite",
                         self.app._cited_filename_item(self.named))

    def test_it_is_filed_under_the_reporter_its_own_file_names(self):
        # A Supreme Court Reporter scan prints S. Ct. pages: naming it by the
        # U.S. Reports pages it does not print describes the wrong document.
        self.named.update(
            url="https://static.case.law/sct/93/0705-01.pdf",
            record={"name": "Roe", "cites": ["410 U.S. 113", "93 S. Ct. 705"]})
        item = self.app._cited_filename_item(self.named)
        self.assertEqual(item["_scan_cite"], "93 S. Ct. 705")
        self.assertNotIn("_us_reports_cite", item)

    def test_the_us_reports_pages_still_win_where_they_are_what_is_shown(self):
        self.named.update(
            url="https://static.case.law/us/410/0113-01.pdf",
            record={"name": "Roe", "cites": ["410 U.S. 113", "93 S. Ct. 705"]})
        item = self.app._cited_filename_item(self.named)
        self.assertEqual(item["_scan_cite"], "410 U.S. 113")

    def test_a_scan_whose_file_names_no_reporter_keeps_the_usual_order(self):
        self.named.update(url="https://www.supremecourt.gov/x/22-1234.pdf",
                          record={"name": "Roe", "cites": ["410 U.S. 113"]})
        item = self.app._cited_filename_item(self.named)
        self.assertNotIn("_scan_cite", item)
        self.assertNotIn("_us_reports_cite", item)

    def test_the_clicked_citation_is_kept_when_the_text_omits_it(self):
        self.named["record"] = {"name": "Roe", "cites": ["93 S. Ct. 705"]}
        self.assertIn("410 U.S. 113",
                      self.app._cited_filename_item(self.named)["citation"])

    def test_saving_names_the_file_from_that(self):
        self.app._save_cited_pdf(self.named)
        self.assertEqual(len(FILENAMES), 1)
        self.assertEqual(FILENAMES[0]["caseName"], "Roe v. Wade")

    def test_printing_names_it_the_same_way(self):
        self.app._print_cited_pdf(self.named, pane=None)
        self.assertEqual(
            PRINTED,
            [str(pathlib.Path(tempfile.gettempdir()) / "NAME.pdf")])

    def test_printing_uses_the_rendering_on_screen(self):
        pane = mock.Mock()
        self.app._print_cited_pdf(self.named, pane=pane)
        pane.export_cropped_pdf.assert_called_once()

    def test_a_redacted_scan_is_whitened_and_re_lettered_for_paper(self):
        self.named["url"] = "https://static.case.law/f2d/500/0123-01.pdf"
        pane = mock.Mock()
        self.app._print_cited_pdf(self.named, pane=pane)
        kwargs = pane.export_cropped_pdf.call_args.kwargs
        self.assertTrue(kwargs["whiten_redactions"])
        self.assertEqual(kwargs["header_cite"], "HEADER")

    def test_a_pane_that_will_not_export_still_prints_the_original(self):
        pane = mock.Mock()
        pane.export_cropped_pdf.side_effect = RuntimeError("no")
        pane.export_pdf.side_effect = RuntimeError("no")
        self.app._print_cited_pdf(self.named, pane=pane)
        self.assertEqual(
            PRINTED,
            [str(pathlib.Path(tempfile.gettempdir()) / "NAME.pdf")])

    def test_nothing_is_saved_before_there_is_a_scan(self):
        self.app._save_cited_pdf({"data": None})
        self.assertEqual(FILENAMES, [])


class WarmedCaseRecordTests(unittest.TestCase):
    """Reading the caption and citations off the opinion fetched behind it."""

    def setUp(self):
        self.got = []
        self.app = _App()

    def _describe(self, record, name="Roe"):
        with mock.patch.dict(
            "sys.modules",
            {"opinion_db": mock.Mock(extract_record=lambda u, h: record)},
        ):
            self.app._describe_warmed_case(("u", "<html>"), name,
                                           self.got.append)

    def test_the_record_reaches_the_caller(self):
        self._describe({"name": "Roe v. Wade", "cites": ["410 U.S. 113"]})
        self.assertEqual(self.got[0]["name"], "Roe v. Wade")

    def test_the_link_s_own_caption_fills_in_a_missing_one(self):
        self._describe({"name": "", "cites": []}, name="Roe v. Wade")
        self.assertEqual(self.got[0]["name"], "Roe v. Wade")

    def test_a_page_with_no_record_reports_nothing(self):
        self._describe(None)
        self.assertEqual(self.got, [])


class CourtListenerFallbackTests(unittest.TestCase):
    """Google Scholar has no copy: the scan's T button shows the best text
    there is instead — static.case.law's report where the Caselaw Access
    Project has the case, CourtListener's where it does not — and that text
    names the window."""

    CLUSTER = {
        "cluster_id": 99,
        "caseName": "Roe v. Wade, Illinois",   # the docket caption
        "citation": ["410 U.S. 113", "93 S. Ct. 705"],
        "dateFiled": "1973-01-22",
        "court_id": "scotus",
        "absolute_url": "/opinion/99/roe-v-wade/",
    }

    def setUp(self):
        RESOLVED.clear(); FETCHED.clear(); CL_ITEMS.clear()
        CL_LOOKUPS.clear(); CL_PARTS.clear(); CL_CAPTIONS.clear()
        CASE_LAW_TEXTS.clear(); CASE_LAW_SCANS.clear()
        CASE_LAW_LOOKUPS.clear()
        TITLED.clear(); TEXT_OPENS.clear()
        _FakeViewer.opened.clear(); _FakeReader.built.clear()
        _Thread.started.clear()
        RESOLVED["410 U.S. 113"] = "https://loc.test/usrep410113.pdf"
        FETCHED["https://loc.test/usrep410113.pdf"] = (
            b"%PDF-1", "https://loc.test/usrep410113.pdf")
        CL_ITEMS["410 U.S. 113"] = dict(self.CLUSTER)
        CL_PARTS[99] = (["part"], ["ROE v. WADE"], "The CourtListener text", {})
        CL_CAPTIONS[("ROE v. WADE",)] = "Roe v. Wade"
        self.app = _App(scholar=False)
        self.status = []

    def _click(self, snippet="Roe v. Wade"):
        self.app.open_cited_case_pdf(
            _FakeHost(), ("cite", "410 U.S. 113"), snippet,
            self.status.append)
        return _FakeViewer.opened[0]

    def _press_t(self, viewer):
        """What the viewer's T button does: build the text beside the pages."""
        return viewer.kw["on_build_text"]("body")

    # --- the text behind the button ---------------------------------
    def test_scholar_is_still_asked_first(self):
        self._click()
        self.assertEqual(self.app.scholar_calls, ["410 U.S. 113"])

    def test_the_t_button_shows_the_courtlistener_text(self):
        reader = self._press_t(self._click())
        self.assertIsNotNone(reader)
        self.assertEqual(reader.kw["cl_text"], "The CourtListener text")
        self.assertEqual(reader.kw["cl_parts"], ["part"])
        self.assertEqual(reader.kw["cl_blocks"], ["ROE v. WADE"])

    def test_it_opens_as_a_courtlistener_window_would(self):
        # No Scholar page, so no html: the reader is CourtListener-primary,
        # and says where the text came from.
        reader = self._press_t(self._click())
        self.assertEqual((reader.url, reader.html), ("", ""))
        self.assertEqual(reader.kw["primary_source_kind"], "courtlistener")
        self.assertEqual(reader.kw["primary_source_label"], "CourtListener")
        self.assertEqual(
            reader.kw["primary_source_url"],
            "https://www.courtlistener.com/opinion/99/roe-v-wade/")

    def test_it_is_built_inside_the_viewer_beside_the_pages(self):
        viewer = self._click()
        reader = self._press_t(viewer)
        self.assertIs(reader.host, "body")
        self.assertTrue(reader.kw["chromeless"])
        self.assertFalse(reader.kw["prefetch_pdf"])
        # The scan goes across too, so T and P keep the reader's place.
        self.assertEqual(reader.kw["initial_pdf"],
                         (b"%PDF-1", "https://loc.test/usrep410113.pdf"))

    def test_the_cluster_already_in_hand_is_not_looked_up_again(self):
        self._click()
        # Once, by the PDF resolver — the fallback reuses what it found.
        self.assertEqual(CL_LOOKUPS, [("410 U.S. 113", "Roe v. Wade")])

    def test_a_case_courtlistener_does_not_have_either_stays_on_the_scan(self):
        CL_ITEMS.clear()
        viewer = self._click()
        self.assertIsNone(self._press_t(viewer))

    def test_nor_does_a_cluster_with_no_text_in_it(self):
        CL_PARTS.clear()
        self.assertIsNone(self._press_t(self._click()))

    def test_without_a_token_there_is_no_courtlistener_to_ask(self):
        self.app = _App(token="", scholar=False)
        self.assertIsNone(self._press_t(self._click()))

    def test_scholar_s_own_copy_still_wins_where_there_is_one(self):
        self.app = _App(scholar=True)
        reader = self._press_t(self._click())
        self.assertEqual(reader.url, "https://scholar.test/410 U.S. 113")
        self.assertNotIn("cl_text", reader.kw)

    # --- a slip opinion nobody else has yet -------------------------
    @staticmethod
    def _own_text(calls):
        """_slip_text_source, as for a Supreme Court slip opinion."""
        def own(data, url, item=None):
            calls.append((data, url))
            return CASE_PDF_TEXT_SOURCE(
                "slip", "Text", "supremecourt.gov", url,
                "The slip opinion's own text", dict(item or {}), [], [],
                None)
        return own

    def test_the_pdfs_own_text_is_the_last_resort(self):
        CL_ITEMS.clear()
        calls = []
        with mock.patch.dict(APP_NS,
                             {"_slip_text_source": self._own_text(calls)}):
            reader = self._press_t(self._click())

        self.assertEqual(calls, [(b"%PDF-1",
                                  "https://loc.test/usrep410113.pdf")])
        self.assertEqual(reader.kw["cl_text"], "The slip opinion's own text")
        self.assertEqual(reader.kw["primary_source_kind"], "slip")
        self.assertEqual(reader.kw["primary_source_label"], "supremecourt.gov")

    def test_the_pdfs_own_text_waits_for_courtlistener(self):
        calls = []
        with mock.patch.dict(APP_NS,
                             {"_slip_text_source": self._own_text(calls)}):
            reader = self._press_t(self._click())

        self.assertEqual(calls, [])
        self.assertEqual(reader.kw["primary_source_kind"], "courtlistener")

    # --- static.case.law comes before CourtListener -----------------
    def test_the_report_on_static_case_law_is_preferred_to_courtlistener(self):
        CASE_LAW_TEXTS["410 U.S. 113"] = _case_law_source()
        reader = self._press_t(self._click())
        self.assertEqual(reader.kw["cl_text"], "The static.case.law text")
        self.assertEqual(reader.kw["cl_parts"], ["cap part"])
        self.assertEqual(reader.kw["primary_source_kind"], "case_law")
        self.assertEqual(reader.kw["primary_source_label"], "static.case.law")
        self.assertEqual(
            reader.kw["primary_source_url"],
            "https://static.case.law/us/410/cases/0113-01.json")

    def test_courtlistener_still_answers_for_a_case_cap_lacks(self):
        reader = self._press_t(self._click())
        self.assertEqual(reader.kw["primary_source_kind"], "courtlistener")

    def test_scholar_is_asked_before_static_case_law(self):
        CASE_LAW_TEXTS["410 U.S. 113"] = _case_law_source()
        self.app = _App(scholar=True)
        reader = self._press_t(self._click())
        self.assertEqual(reader.url, "https://scholar.test/410 U.S. 113")
        self.assertEqual(CASE_LAW_LOOKUPS, [])

    def test_the_scan_on_screen_is_what_the_lookup_is_keyed_on(self):
        # Not the first citation CourtListener lists: CAP scanned each
        # parallel reporter separately, and only one of them paginates the
        # pages the reader is looking at.  The date travels with them, so a
        # decision CAP's scans stop short of is not asked for at all.
        self._click()
        self.assertEqual(
            CASE_LAW_LOOKUPS,
            [("https://loc.test/usrep410113.pdf",
              ("410 U.S. 113", "93 S. Ct. 705"),
              "Roe v. Wade", "1973-01-22")])

    def test_the_scans_own_text_wins_over_any_parallel_reporters(self):
        CASE_LAW_SCANS["https://loc.test/usrep410113.pdf"] = _case_law_source(
            text="The U.S. Reports text")
        CASE_LAW_TEXTS["93 S. Ct. 705"] = _case_law_source(
            text="The S. Ct. text")
        reader = self._press_t(self._click())
        self.assertEqual(reader.kw["cl_text"], "The U.S. Reports text")

    def test_a_case_neither_source_has_still_stays_on_the_scan(self):
        CL_ITEMS.clear()
        self.assertIsNone(self._press_t(self._click()))

    def test_static_case_law_names_the_window_without_a_token(self):
        CASE_LAW_TEXTS["410 U.S. 113"] = _case_law_source(
            item={"caseName": "Roe v. Wade", "citation": ["410 U.S. 113"]})
        self.app = _App(token="", scholar=False)
        reader = self._press_t(self._click())
        self.assertIsNotNone(reader)
        self.assertEqual(reader.kw["primary_source_kind"], "case_law")

    # --- and what it names the window -------------------------------
    def test_the_window_is_named_from_courtlistener(self):
        viewer = self._click()
        self.assertEqual(viewer.title, "Roe v. Wade, 410 U.S. 113")

    def test_the_opinion_s_own_caption_beats_the_docket_caption(self):
        self._click()
        self.assertEqual(TITLED[-1]["caseName"], "Roe v. Wade")

    def test_the_court_and_the_date_come_with_it(self):
        self._click()
        self.assertEqual(TITLED[-1]["dateFiled"], "1973-01-22")
        self.assertEqual(TITLED[-1]["court_id"], "scotus")

    def test_a_case_with_no_text_anywhere_keeps_the_clicked_citation(self):
        CL_ITEMS.clear()
        viewer = self._click()
        self.assertEqual(viewer.title, "Roe v. Wade — 410 U.S. 113")


class CourtListenerTextRecordTests(unittest.TestCase):
    """The Bluebook pieces read off a CourtListener cluster — the same shape
    the Google Scholar page yields, so a window is named from either."""

    def setUp(self):
        CL_CAPTIONS.clear()

    def _record(self, item, blocks=()):
        return CL_TEXT_RECORD(
            CASE_PDF_TEXT_SOURCE(
                "courtlistener", "Text", "CourtListener", "", "text",
                item, [], list(blocks)))

    def test_the_caption_the_reports_print_names_the_case(self):
        CL_CAPTIONS[("ROE v. WADE",)] = "Roe v. Wade"
        record = self._record({"caseName": "Roe v. Wade, Illinois"},
                              ["ROE v. WADE"])
        self.assertEqual(record["name"], "Roe v. Wade")

    def test_an_unreadable_caption_leaves_the_cluster_s_own_name(self):
        record = self._record({"caseName": "Roe v. <em>Wade</em>"})
        self.assertEqual(record["name"], "Roe v. Wade")

    def test_the_parallel_citations_come_across(self):
        record = self._record(
            {"citation": ["410 U.S. 113", "93 S. Ct. 705"]})
        self.assertEqual(record["cites"], ["410 U.S. 113", "93 S. Ct. 705"])

    def test_the_date_dates_the_case_and_yields_its_year(self):
        record = self._record({"dateFiled": "1973-01-22"})
        self.assertEqual(record["date_filed"], "1973-01-22")
        self.assertEqual(record["year"], "1973")

    def test_the_court_id_is_what_a_bluebook_parenthetical_wants(self):
        record = self._record({"court_id": "ca2", "court": "Second Circuit"})
        self.assertEqual(record["court"], "ca2")

    def test_a_cluster_without_one_falls_back_to_the_court_s_name(self):
        record = self._record({"court": "Second Circuit"})
        self.assertEqual(record["court"], "Second Circuit")

    def test_a_bare_cluster_reports_empty_pieces_rather_than_failing(self):
        self.assertEqual(
            self._record({}),
            {"name": "", "cites": [], "date_filed": "", "year": "",
             "court": ""})


class CitedCaseItemTests(unittest.TestCase):
    """What the resolver is handed for a cited case."""

    def setUp(self):
        CL_ITEMS.clear()
        self.app = _App()

    def test_the_clicked_citation_is_always_among_them(self):
        item = self.app._cited_case_pdf_item("client", "410 U.S. 113", "Roe")
        self.assertIn("410 U.S. 113", item["citation"])

    def test_the_case_name_comes_along_for_the_reporter_scans(self):
        item = self.app._cited_case_pdf_item("client", "410 U.S. 113", "Roe")
        self.assertEqual(item["caseName"], "Roe")

    def test_courtlistener_s_record_brings_the_court_date_and_docket(self):
        # What the official-report and slip-opinion paths key on.
        CL_ITEMS["410 U.S. 113"] = {
            "citation": ["410 U.S. 113", "93 S. Ct. 705"],
            "court_id": "scotus", "dateFiled": "1973-01-22",
            "docketNumber": "70-18", "caseName": "Roe v. Wade",
        }
        item = self.app._cited_case_pdf_item("client", "410 U.S. 113", "Roe")
        self.assertEqual(item["court_id"], "scotus")
        self.assertEqual(item["docketNumber"], "70-18")
        self.assertIn("93 S. Ct. 705", item["citation"])

    def test_an_old_nominative_cite_also_offers_its_modern_form(self):
        # "1 Cranch 137" is filed as "5 U.S. 137" in the official reports.
        item = self.app._cited_case_pdf_item("client", "1 Cranch 137",
                                             "Marbury")
        self.assertEqual(item["citation"], ["1 Cranch 137", "5 U.S. 137"])

    def test_and_so_does_a_massachusetts_nominative_cite(self):
        # 19 Pick. is 36 Mass.: the scans are filed under the Mass. volume.
        item = self.app._cited_case_pdf_item(None, "19 Pick. 234", "Smith")
        self.assertEqual(item["citation"], ["19 Pick. 234", "36 Mass. 234"])

    def test_without_courtlistener_the_citation_alone_will_do(self):
        item = self.app._cited_case_pdf_item(None, "410 U.S. 113", "Roe")
        self.assertEqual(item["citation"], ["410 U.S. 113"])

    def test_a_courtlistener_name_is_not_overwritten(self):
        CL_ITEMS["410 U.S. 113"] = {"caseName": "Roe v. Wade",
                                    "citation": ["410 U.S. 113"]}
        item = self.app._cited_case_pdf_item("client", "410 U.S. 113", "Roe")
        self.assertEqual(item["caseName"], "Roe v. Wade")


class PdfClickRoutingTests(unittest.TestCase):
    """The reader sends a click on a page through the cited-PDF path."""

    def setUp(self):
        self.ns = _load(
            "_ScholarTextWindow", ["_open_pdf_cite"],
            {"_follow_brief_action": lambda *a, **kw: TEXT_OPENS.append(a),
             "_open_citation_in_browser": lambda *a: BROWSER.append(a)},
        )
        TEXT_OPENS.clear()
        BROWSER.clear()

    def _reader(self, app):
        reader = mock.Mock()
        reader._app = app
        reader._open_pdf_cite = self.ns["_open_pdf_cite"].__get__(reader)
        return reader

    def test_a_case_citation_goes_to_the_cited_pdf_path(self):
        app = mock.Mock()
        app.open_cited_case_pdf.return_value = True
        reader = self._reader(app)
        reader._open_pdf_cite(("cite", "410 U.S. 113"), "Roe")
        app.open_cited_case_pdf.assert_called_once()
        self.assertEqual(TEXT_OPENS, [])

    def test_the_fallback_it_is_given_opens_the_text(self):
        app = mock.Mock()
        app.open_cited_case_pdf.return_value = True
        reader = self._reader(app)
        reader._open_pdf_cite(("cite", "410 U.S. 113"), "Roe")
        app.open_cited_case_pdf.call_args.kwargs["fallback"]()
        self.assertEqual(len(TEXT_OPENS), 1)

    def test_anything_that_is_not_a_case_opens_the_old_way(self):
        app = mock.Mock()
        app.open_cited_case_pdf.return_value = False   # a statute, a rule
        reader = self._reader(app)
        reader._open_pdf_cite(("statute", "42 U.S.C. 1983"), "")
        self.assertEqual(len(TEXT_OPENS), 1)

    def test_without_an_app_the_citation_opens_in_the_browser(self):
        reader = self._reader(None)
        reader._open_pdf_cite(("cite", "410 U.S. 113"), "Roe")
        self.assertEqual(len(BROWSER), 1)


BROWSER: list = []


# ---------------------------------------------------------------------------
# 3. The U.S. Reports cite already on file leads
# ---------------------------------------------------------------------------

ITEM_NS = _load(
    "_ScholarTextWindow",
    ["_pdf_item", "_adopt_stored_us_reports_cite"],
    {"_normalized_us_cite": lambda c: (
        c.strip() if re.search(r"\d+\s+U\.\s?S\.\s+\d+", str(c)) else ""),
     "_scotus_docket_tokens": lambda text: set(),
     "_item_docket_text": lambda item: "",
     },
)


class _DbReader:
    """A reader opened from the opinion database: no search result behind it,
    just the stored record."""

    def __init__(self, stored=(), header=(), bb_cite="", item=None,
                 scholar_id="sid"):
        self._item = item
        self._app = mock.Mock()
        self._app._get_opinion_db.return_value = mock.Mock(
            stored_citations=lambda sid: list(stored))
        self._header_cites = list(header)
        self._bb = {"cite": bb_cite, "name": "Cedar Point Nursery"}
        self._is_scotus = False
        self._us_reports_cite = ""
        self._sid = scholar_id
        for name in ("_pdf_item", "_adopt_stored_us_reports_cite"):
            setattr(self, name, ITEM_NS[name].__get__(self))

    def _opinion_scholar_id(self):
        return self._sid


class StoredUsCiteTests(unittest.TestCase):
    def test_the_stored_us_cite_leads_even_when_it_is_not_first(self):
        # The database has it second, behind the S. Ct. cite Scholar saved.
        reader = _DbReader(stored=["141 S.Ct. 2063", "594 U. S. 139"])
        item = reader._pdf_item()
        self.assertEqual(item["citation"][0], "594 U. S. 139")

    def test_it_is_handed_to_the_resolver_by_name(self):
        reader = _DbReader(stored=["141 S.Ct. 2063", "594 U. S. 139"])
        self.assertEqual(reader._pdf_item()["_us_reports_cite"],
                         "594 U. S. 139")

    def test_the_reader_learns_it_from_the_database(self):
        reader = _DbReader(stored=["141 S.Ct. 2063", "594 U. S. 139"])
        reader._adopt_stored_us_reports_cite()
        self.assertEqual(reader._us_reports_cite, "594 U. S. 139")

    def test_the_other_cites_are_still_offered_after_it(self):
        reader = _DbReader(stored=["141 S.Ct. 2063", "594 U. S. 139"])
        self.assertEqual(reader._pdf_item()["citation"],
                         ["594 U. S. 139", "141 S.Ct. 2063"])

    def test_a_cite_from_the_opinion_s_own_header_counts_too(self):
        reader = _DbReader(header=["143 S.Ct. 1369", "598 U. S. 631"])
        self.assertEqual(reader._pdf_item()["_us_reports_cite"],
                         "598 U. S. 631")

    def test_one_already_known_to_the_reader_is_not_looked_up_again(self):
        reader = _DbReader(stored=["141 S.Ct. 2063"])
        reader._us_reports_cite = "594 U. S. 139"
        reader._adopt_stored_us_reports_cite()
        self.assertEqual(reader._us_reports_cite, "594 U. S. 139")

    def test_an_opinion_with_no_us_cite_names_none(self):
        reader = _DbReader(stored=["141 S.Ct. 2063"], bb_cite="141 S. Ct. 2063")
        item = reader._pdf_item()
        self.assertNotIn("_us_reports_cite", item)
        self.assertEqual(item["citation"][0], "141 S.Ct. 2063")

    def test_an_opinion_the_database_does_not_have_is_no_worse_off(self):
        reader = _DbReader(scholar_id="", bb_cite="141 S. Ct. 2063")
        self.assertEqual(reader._pdf_item()["citation"], ["141 S. Ct. 2063"])

    def test_the_resolver_tries_that_cite_before_any_network_lookup(self):
        body = _source_of("CourtListenerGUI", "_resolve_pdf_url")
        given = body.index("if given_us_cite:")
        gather = body.index("all_cites = _gather_all_citations")
        self.assertLess(given, gather)


# ---------------------------------------------------------------------------
# 4. What names the case: its own caption, not the docket one
# ---------------------------------------------------------------------------

NAME_NS = _load("_ScholarTextWindow", ["_filename_item", "_scan_window_title",
                                       "_pdf_filename_item",
                                       "_with_citation_facts"],
                {"_scan_citation_item": SCAN_CITATION_ITEM,
                 "_bluebook_display_name": lambda item: (
                     f"{item.get('caseName')} | "
                     f"{(item.get('_us_reports_cite') or item.get('_scan_cite') or (item.get('citation') or [''])[0])}")})


class _NamedReader:
    def __init__(self, item, caption):
        self._item = item
        self._bb = {"name": caption, "cite": "137 S. Ct. 911"}
        for name in ("_filename_item", "_scan_window_title",
                     "_pdf_filename_item", "_with_citation_facts"):
            setattr(self, name, NAME_NS[name].__get__(self))

    def _shown_us_reports_cite(self):
        return ""

    def _title_citation(self):
        return ""


#: What CourtListener's search result calls it, and what the reports print.
DOCKET_ITEM = {"caseName": "Manuel v. City of Joliet, Illinois",
               "citation": ["137 S. Ct. 911"],
               "dateFiled": "2017-03-21", "court_id": "scotus"}
CAPTION = "Manuel v. City of Joliet"
SLIP_URL = "https://www.supremecourt.gov/opinions/16pdf/14-9496.pdf"


class WarmedCaseNameTests(unittest.TestCase):
    """What the background fetch of the opinion tells the scan's window the
    case is called."""

    HTML = "<opinion of Manuel>"

    def setUp(self):
        CAPTIONS.clear()
        CAPTIONS[self.HTML] = "Manuel v. City of Joliet"

    def _describe(self, stored_name="Manuel v. City of Joliet, Illinois",
                  fallback="Manuel v. City of Joliet, Ill.", html=None,
                  court="scotus"):
        html = self.HTML if html is None else html
        record = {"name": stored_name, "cites": ["137 S. Ct. 911"],
                  "year": "2017", "court": court}
        fake = types.ModuleType("opinion_db")
        fake.extract_record = lambda url, h: dict(record)
        got: list = []
        with mock.patch.dict(sys.modules, {"opinion_db": fake}):
            APP_NS["_describe_warmed_case"](
                ("https://scholar.google.com/scholar_case?case=1", html),
                fallback, got.append)
        return got[0] if got else None

    def test_the_caption_the_reports_print_names_the_case(self):
        # The stored record keeps the docket caption whole; the window is
        # named the way every other window in the app names a case.
        self.assertEqual(self._describe()["name"], "Manuel v. City of Joliet")

    def test_the_stored_record_itself_is_not_rewritten(self):
        # A copy goes to the window; the database keeps what it keeps.
        record = self._describe()
        self.assertEqual(record["cites"], ["137 S. Ct. 911"])
        self.assertEqual(record["court"], "scotus")

    def test_an_unreadable_caption_leaves_the_stored_name(self):
        self.assertEqual(self._describe(html="<no caption here>")["name"],
                         "Manuel v. City of Joliet, Illinois")

    def test_and_with_neither_the_clicked_name_stands_in(self):
        record = self._describe(stored_name="", html="<no caption here>")
        self.assertEqual(record["name"], "Manuel v. City of Joliet, Ill.")

    def test_the_header_names_a_court_the_citation_does_not(self):
        # United States v. Republic Steel Corp., 155 F. Supp. 442 (N.D. Ill.
        # 1957): an F. Supp. cite names no court, so the stored record had
        # none, and the window's parenthetical went without one.
        HEADER_COURTS[self.HTML] = "ilnd"
        try:
            self.assertEqual(self._describe(court="")["court"], "ilnd")
            # A court the record already has is kept.
            self.assertEqual(self._describe()["court"], "scotus")
        finally:
            HEADER_COURTS.clear()

    def test_a_court_that_will_not_read_leaves_the_caption(self):
        def unreadable():
            raise ValueError("no header")
        HEADER_COURTS[self.HTML] = unreadable
        try:
            record = self._describe(court="")
        finally:
            HEADER_COURTS.clear()
        self.assertEqual(record["name"], "Manuel v. City of Joliet")
        self.assertEqual(record["court"], "")


class CaseNameTests(unittest.TestCase):
    """A search result carries the caption the court docketed the case under;
    the opinion carries the one the reports print."""

    def test_the_opinion_s_own_caption_names_the_case(self):
        reader = _NamedReader(DOCKET_ITEM, CAPTION)
        self.assertEqual(reader._filename_item()["caseName"], CAPTION)

    def test_and_names_the_window_the_scan_opens_in(self):
        reader = _NamedReader(DOCKET_ITEM, CAPTION)
        self.assertTrue(
            reader._scan_window_title(SLIP_URL).startswith(CAPTION + " |"))

    def test_unless_the_reader_has_written_the_citation_differently(self):
        reader = _NamedReader(DOCKET_ITEM, CAPTION)
        reader._base_citation_override = "Manuel v. Joliet, 580 U.S. 357"
        self.assertEqual(reader._scan_window_title(SLIP_URL),
                         "Manuel v. Joliet, 580 U.S. 357")

    def test_and_the_file_a_scan_is_saved_as(self):
        reader = _NamedReader(DOCKET_ITEM, CAPTION)
        self.assertEqual(reader._pdf_filename_item()["caseName"], CAPTION)

    def test_everything_else_about_the_case_still_comes_from_the_result(self):
        item = _NamedReader(DOCKET_ITEM, CAPTION)._filename_item()
        self.assertEqual(item["dateFiled"], "2017-03-21")
        self.assertEqual(item["court_id"], "scotus")
        self.assertEqual(item["citation"], ["137 S. Ct. 911"])

    def test_the_result_itself_is_not_rewritten(self):
        _NamedReader(DOCKET_ITEM, CAPTION)._filename_item()
        self.assertEqual(DOCKET_ITEM["caseName"],
                         "Manuel v. City of Joliet, Illinois")

    def test_no_caption_to_read_leaves_the_result_s_name(self):
        # A window opened straight onto a scan has no opinion text to ask.
        reader = _NamedReader(DOCKET_ITEM, "")
        self.assertEqual(reader._filename_item()["caseName"],
                         "Manuel v. City of Joliet, Illinois")

    def test_a_stale_case_name_key_does_not_outvote_it(self):
        item = dict(DOCKET_ITEM, case_name="Manuel v. City of Joliet, Ill.")
        self.assertNotIn("case_name",
                         _NamedReader(item, CAPTION)._filename_item())

    def test_a_reader_with_no_result_behind_it_is_unchanged(self):
        reader = _NamedReader(None, CAPTION)
        reader._bb = {"name": CAPTION, "cite": "137 S. Ct. 911",
                      "year": "2017", "court": ""}
        self.assertEqual(reader._filename_item()["caseName"], CAPTION)


# ---------------------------------------------------------------------------
# 5. A pin cite, and the page of the scan that prints it
# ---------------------------------------------------------------------------


def _page_with(text: str) -> list:
    """One page of a scan's text layer, as a run of positioned glyphs."""
    return [(ch, (i * 6.0, 700.0, i * 6.0 + 5.0, 710.0))
            for i, ch in enumerate(text)]


class PinPageTests(unittest.TestCase):
    """Counting a pin cite out to a page of the file it is printed in."""

    def test_the_offset_from_the_case_s_first_page(self):
        self.assertEqual(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "153", "410 U.S. 113"), 40)

    def test_the_first_page_is_the_first_page(self):
        self.assertEqual(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "113", "410 U.S. 113"), 0)

    def test_a_pin_before_the_case_starts_is_not_in_this_file(self):
        self.assertIsNone(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "94", "410 U.S. 113"))

    def test_a_footnote_pin_still_names_its_page(self):
        self.assertEqual(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "153n4", "410 U.S. 113"), 40)

    def test_a_range_opens_at_the_first_of_it(self):
        self.assertEqual(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "153-55", "410 U.S. 113"), 40)

    def test_a_pin_naming_only_a_footnote_names_no_page(self):
        self.assertIsNone(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "n4", "410 U.S. 113"))

    def test_a_pin_in_another_reporter_is_refused(self):
        # "93 S. Ct. at 710" says nothing about where 410 U.S. breaks.
        self.assertIsNone(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "710", "93 S. Ct. 705"))

    def test_the_same_reporter_in_another_volume_is_still_refused(self):
        self.assertIsNone(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "710", "93 S.Ct. 705"))

    def test_a_link_that_names_no_reporter_is_taken_at_its_word(self):
        self.assertEqual(SCAN_PAGE_FOR_PIN("410 U.S. 113", "153"), 40)

    def test_a_scan_that_names_no_reporter_answers_nothing(self):
        self.assertIsNone(SCAN_PAGE_FOR_PIN("", "153", "410 U.S. 113"))

    def test_a_pin_past_the_end_of_the_file_is_refused(self):
        self.assertIsNone(SCAN_PAGE_FOR_PIN(
            "410 U.S. 113", "900", "410 U.S. 113",
            pdf_pages=[_page_with("113"), _page_with("114")]))

    def test_the_printed_numbers_confirm_the_arithmetic(self):
        pages = [_page_with("113  ROE v. WADE"),
                 _page_with("114  OCTOBER TERM, 1972")]
        self.assertEqual(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "114", "410 U.S. 113", pages), 1)

    def test_and_correct_it_where_a_cover_leaf_shifts_the_pages(self):
        pages = [_page_with("Supreme Court of the United States"),
                 _page_with("113  ROE v. WADE"),
                 _page_with("114  OCTOBER TERM, 1972")]
        self.assertEqual(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "114", "410 U.S. 113", pages), 2)

    def test_pages_that_print_nothing_leave_the_arithmetic_alone(self):
        pages = [_page_with(""), _page_with(""), _page_with("")]
        self.assertEqual(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "115", "410 U.S. 113", pages), 2)

    def test_a_number_read_twice_is_not_trusted_over_the_count(self):
        # Two pages both claiming 114: the arithmetic is the better answer.
        pages = [_page_with("113"), _page_with("114"), _page_with("114")]
        self.assertEqual(
            SCAN_PAGE_FOR_PIN("410 U.S. 113", "114", "410 U.S. 113", pages), 1)


class PrintedFolioTests(unittest.TestCase):
    def test_a_number_at_the_left_of_the_running_head(self):
        self.assertEqual(
            PRINTED_PAGE_NUMBERS([_page_with("152  OCTOBER TERM, 1972")]),
            {0: 152})

    def test_and_one_at_the_right(self):
        self.assertEqual(
            PRINTED_PAGE_NUMBERS([_page_with("ROE v. WADE  153")]), {0: 153})

    def test_figures_parted_by_the_line_reader_are_one_number(self):
        # A "1" leaves more room after its ink than the type's own advance,
        # so a page number comes off the scan as "1 13".
        self.assertEqual(
            PRINTED_PAGE_NUMBERS([_page_with("ROE v. WADE  1 13")]), {0: 113})
        self.assertEqual(
            PRINTED_PAGE_NUMBERS([_page_with("1 14  OCTOBER TERM, 1972")]),
            {0: 114})

    def test_a_page_printing_no_number_is_absent(self):
        self.assertEqual(PRINTED_PAGE_NUMBERS([_page_with("Syllabus")]), {})

    def test_an_empty_page_is_absent_too(self):
        self.assertEqual(PRINTED_PAGE_NUMBERS([[]]), {})


class PinNumberTests(unittest.TestCase):
    def test_a_plain_page(self):
        self.assertEqual(PIN_PAGE_NUMBER("153"), 153)

    def test_a_page_and_a_note(self):
        self.assertEqual(PIN_PAGE_NUMBER("153n4"), 153)

    def test_a_note_alone(self):
        self.assertIsNone(PIN_PAGE_NUMBER("n4"))

    def test_nothing_at_all(self):
        self.assertIsNone(PIN_PAGE_NUMBER(""))


if __name__ == "__main__":
    unittest.main()
