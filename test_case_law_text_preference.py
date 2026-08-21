"""Which text the app opens when Google Scholar has no copy of a case.

Google Scholar is asked first for every case and, where it answers, nothing
else is consulted.  Where it does not, the next thing asked is
**static.case.law** — the Caselaw Access Project's own text of the printed
report, filed under the citation and paginated to the reporter that citation
names — and only after that CourtListener, whose transcription need not be
paginated to any reporter at all and which is what answers for every decision
since CAP's scans stop.

These tests drive the three places that make the choice — opening a case from
a search result, opening one after a Google Scholar error, and resolving a
citation out of a list or a brief — plus the window each opens.

Lifted out of ``courtlistener_gui`` with ``ast`` (importing it needs tkinter,
absent on a headless run) and driven against stubs.
"""

import ast
import pathlib
import re
import typing
import unittest
from unittest import mock


SRC = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text(
    encoding="utf-8")
TREE = ast.parse(SRC)


class _Tk:
    TclError = Exception
    Misc = Menu = object


class _Thread:
    """Runs the worker inline, so a test sees the whole chain at once."""

    def __init__(self, target=None, daemon=False, **_kw):
        self._target = target

    def start(self):
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
    ns = {"tk": _Tk, "re": re, "Optional": typing.Optional,
          "threading": mock.Mock(Thread=_Thread)}
    ns.update(extra or {})
    for name in names:
        exec(found[name], ns)
    return ns


def _load_function(name: str, extra=None):
    src = next((ast.get_source_segment(SRC, n) for n in TREE.body
                if isinstance(n, ast.FunctionDef) and n.name == name), None)
    if src is None:
        raise AssertionError(f"module-level function not found: {name}")
    ns = {"tk": _Tk, "re": re, "Optional": typing.Optional}
    ns.update(extra or {})
    exec(src, ns)
    return ns[name]


#: The real reading of a search result's citations, name and date — the three
#: things a static.case.law lookup is made from.
ITEM_CASE_LAW_KEY = _load_function(
    "_item_case_law_key",
    {"_cluster_citations_to_strings": lambda cites: [str(c) for c in cites]})

#: And the real rule for which reporter leads a window's citation list, so a
#: test exercises it rather than a restatement of it.
CASE_LAW_PDF_FOR_JSON_URL = _load_function("_case_law_pdf_for_json_url")
CITES_LED_BY = _load_function("_cites_led_by")
CASE_LAW_REPORTER_CITE = _load_function(
    "_case_law_reporter_cite",
    {"_CASE_LAW_URL_RE": re.compile(
        r"static\.case\.law/([^/]+)/(\d+)/case-pdfs/0*(\d+)-\d+\.pdf", re.I),
     "_CASE_LAW_SLUG_REPORTERS": {"us": "U.S.", "sct": "S. Ct.",
                                  "f2d": "F.2d", "p2d": "P.2d",
                                  "wash-2d": "Wash. 2d"}})


class _Source:
    """A text source shaped the way the real ones come back."""

    def __init__(self, kind, label, url="", text="text", item=None,
                 parts=("part",), blocks=("block",)):
        self.kind, self.source_label, self.source_url = kind, label, url
        self.text, self.item = text, dict(item or {})
        self.parts, self.blocks = list(parts), list(blocks)
        self.button_label = "Text"


CAP = _Source(
    "case_law", "static.case.law",
    "https://static.case.law/f2d/410/cases/0701-01.json",
    text="The static.case.law report",
    item={"caseName": "Pearson v. Dodd", "citation": ["410 F.2d 701"],
          "court": "D.C. Cir.", "dateFiled": "1969-02-24"},
    parts=["cap part"], blocks=["PEARSON v. DODD"])

CLUSTER = {
    "cluster_id": 99,
    "caseName": "Pearson v. Dodd",
    "citation": ["410 F.2d 701"],
    "dateFiled": "1969-02-24",
    "court_id": "cadc",
}


class _Reader:
    """A ``_ScholarTextWindow`` the app opened."""

    built: list = []

    def __init__(self, parent, app, url, html, **kw):
        self.parent, self.url, self.html, self.kw = parent, url, html, kw
        self.searched = None
        self.retried = None
        _Reader.built.append(self)

    def _search_for_scholar_version(self, text=None):
        self.searched = text

    def _retry_scholar_link(self, *args):
        self.retried = args

    def jump_to_cite_page(self, cite, pin):
        self.jumped = (cite, pin)


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


CAP_LOOKUPS: list = []       # (cites, name, date) each lookup was asked with
CAP_ANSWER: list = [None]    # what static.case.law "has"
CL_PARTS: list = [(["cl part"], ["cl block"], "The CourtListener text", {})]


def _case_law_text_source(cites, name="", date=""):
    CAP_LOOKUPS.append((list(cites), name, date))
    return CAP_ANSWER[0]


def _assemble_case_parts(client, item):
    return CL_PARTS[0]


APP_NS = _load(
    "CourtListenerGUI",
    ["_open_case_law_window", "_open_cl_window", "_assemble_and_open_cl",
     "_scholar_first_worker"],
    {"_ScholarTextWindow": _Reader,
     "_case_law_text_source": _case_law_text_source,
     "_item_case_law_key": ITEM_CASE_LAW_KEY,
     "_assemble_case_parts": _assemble_case_parts,
     "_pick_citation": lambda cites: (cites or [""])[0],
     "blocks_to_text": lambda blocks: "scholar text",
     "parse_opinion_blocks": lambda html: [html],
     "text_similarity": lambda a, b: 0.0,
     "_SCHOLAR_MATCH_THRESHOLD": 0.72,
     "_find_scholar_for_item": lambda *a, **kw: (None, None, ""),
     "_cluster_citations_to_strings": lambda cites: [str(c) for c in cites],
     "_cites_led_by": CITES_LED_BY,
     "_case_law_pdf_for_json_url": CASE_LAW_PDF_FOR_JSON_URL,
     "_case_law_reporter_cite": CASE_LAW_REPORTER_CITE,
     })


class _App:
    """Just enough of the main window to run the open path on this thread."""

    def __init__(self):
        self.root = "root"
        self._status_var = _Var()
        self.posted: list = []
        self.failed: list = []

    def _post_root(self, fn, *args):
        self.posted.append((fn, args))
        fn(*args)

    def _scholar_open_failed(self, item, note):
        self.failed.append((item, note))

    def _open_scholar_window(self, url, html, item, cl_text, note,
                             prefetch_pdf=True):
        self.opened_scholar = (url, html, note)

    _open_case_law_window = APP_NS["_open_case_law_window"]
    _open_cl_window = APP_NS["_open_cl_window"]
    _assemble_and_open_cl = APP_NS["_assemble_and_open_cl"]
    _scholar_first_worker = APP_NS["_scholar_first_worker"]


class _Fetcher:
    """Google Scholar, with or without a copy of the case."""

    def __init__(self, result=None, error=False):
        self.result, self.error = result, error
        self.asked: list = []

    def get_cached(self, key):
        return None

    def put_cached(self, key, url, html):
        pass

    def fetch_by_citation(self, cite):
        self.asked.append(cite)
        if self.error:
            raise RuntimeError("blocked")
        return self.result


class SourcePreferenceTests(unittest.TestCase):
    def setUp(self):
        _Reader.built.clear()
        CAP_LOOKUPS.clear()
        CAP_ANSWER[0] = None
        CL_PARTS[0] = (["cl part"], ["cl block"], "The CourtListener text", {})
        self.app = _App()

    def _open(self, fetcher=None, client="client"):
        self.app._scholar_first_worker(
            dict(CLUSTER), fetcher or _Fetcher(), client)
        return _Reader.built[-1] if _Reader.built else None

    def _open_with(self, **fields):
        item = dict(CLUSTER)
        item.update(fields)
        self.app._scholar_first_worker(item, _Fetcher(), "client")
        return _Reader.built[-1]

    # --- opening a case from a search result -------------------------
    def test_static_case_law_is_preferred_to_courtlistener(self):
        CAP_ANSWER[0] = CAP
        reader = self._open()
        self.assertEqual(reader.kw["primary_source_kind"], "case_law")
        self.assertEqual(reader.kw["primary_source_label"], "static.case.law")
        self.assertEqual(reader.kw["cl_text"], "The static.case.law report")
        self.assertEqual(reader.kw["cl_parts"], ["cap part"])
        self.assertEqual(
            reader.kw["primary_source_url"],
            "https://static.case.law/f2d/410/cases/0701-01.json")

    def test_courtlistener_answers_for_a_case_cap_does_not_hold(self):
        reader = self._open()
        self.assertEqual(reader.kw["cl_text"], "The CourtListener text")
        self.assertNotIn("primary_source_kind", reader.kw)

    def test_the_lookup_carries_the_cites_the_name_and_the_date(self):
        self._open()
        self.assertEqual(
            CAP_LOOKUPS,
            [(["410 F.2d 701"], "Pearson v. Dodd", "1969-02-24")])

    def test_scholar_s_own_copy_is_still_what_opens(self):
        fetcher = _Fetcher(result=("https://scholar.test/x", "<p>same</p>"))
        with mock.patch.dict(APP_NS, {"text_similarity": lambda a, b: 1.0}):
            # A verified Scholar match: neither other source is consulted.
            self.app._scholar_first_worker(dict(CLUSTER), fetcher, "client")
        self.assertEqual(CAP_LOOKUPS, [])

    def test_a_scholar_error_still_reaches_static_case_law(self):
        CAP_ANSWER[0] = CAP
        reader = self._open(fetcher=_Fetcher(error=True))
        self.assertEqual(reader.kw["primary_source_kind"], "case_law")

    def test_and_courtlistener_after_a_scholar_error_when_cap_has_nothing(self):
        reader = self._open(fetcher=_Fetcher(error=True))
        self.assertEqual(reader.kw["cl_text"], "The CourtListener text")

    def test_a_case_no_source_has_reports_the_failure(self):
        CL_PARTS[0] = ([], [], "", {})
        self.app._assemble_and_open_cl(
            dict(CLUSTER), "client", True, lambda: None, note="n")
        self.assertEqual(_Reader.built, [])
        self.assertEqual(self.app.failed[-1][1], "n")

    def test_static_case_law_answers_with_no_courtlistener_token(self):
        CAP_ANSWER[0] = CAP
        self.app._assemble_and_open_cl(
            dict(CLUSTER), None, True, lambda: None)
        self.assertEqual(_Reader.built[-1].kw["primary_source_kind"],
                         "case_law")

    # --- and the window it opens -------------------------------------
    def test_the_search_result_still_identifies_the_case(self):
        # The cluster id the PDF button and the background Scholar hunt both
        # need survives; CAP fills in only what the result left blank.
        CAP_ANSWER[0] = _Source(
            "case_law", "static.case.law",
            item={"caseName": "Drew Pearson v. Dodd", "court": "D.C. Cir."})
        reader = self._open()
        item = reader.kw["item"]
        self.assertEqual(item["cluster_id"], 99)
        self.assertEqual(item["caseName"], "Pearson v. Dodd")  # not overwritten
        self.assertEqual(item["court"], "D.C. Cir.")           # filled in

    def test_the_hunt_for_a_scholar_copy_carries_on_behind_it(self):
        CAP_ANSWER[0] = CAP
        reader = self._open()
        self.assertEqual(reader.searched, "The static.case.law report")

    def test_with_no_scholar_at_all_there_is_no_hunt(self):
        CAP_ANSWER[0] = CAP
        self.app._scholar_first_worker(dict(CLUSTER), None, "client")
        self.assertIsNone(_Reader.built[-1].searched)

    def test_the_status_line_names_the_source(self):
        CAP_ANSWER[0] = CAP
        self._open()
        self.assertIn("static.case.law", self.app._status_var.get())

    # --- and the scan the PDF button will find ------------------------
    def test_the_reporter_the_text_came_from_leads_the_citations(self):
        # The PDF resolver walks a case's citations in order and stops at the
        # first reporter static.case.law has a scan of.  A window showing the
        # F.2d text must therefore ask for the F.2d scan, not the parallel
        # reporter CourtListener happened to list first.
        CAP_ANSWER[0] = _Source(
            "case_law", "static.case.law",
            "https://static.case.law/f2d/410/cases/0701-01.json",
            item={"citation": ["410 F.2d 701"]})
        reader = self._open_with(citation=["93 S. Ct. 705", "410 F.2d 701"])
        self.assertEqual(reader.kw["item"]["citation"],
                         ["410 F.2d 701", "93 S. Ct. 705"])

    def test_a_reporter_the_result_never_listed_is_added_at_the_front(self):
        CAP_ANSWER[0] = _Source(
            "case_law", "static.case.law",
            "https://static.case.law/p2d/506/cases/0020-01.json",
            item={"citation": ["506 P.2d 20"]})
        reader = self._open_with(citation=["81 Wash. 2d 886"])
        self.assertEqual(reader.kw["item"]["citation"],
                         ["506 P.2d 20", "81 Wash. 2d 886"])

    def test_a_source_that_names_no_cap_file_leaves_the_order_alone(self):
        CAP_ANSWER[0] = _Source("case_law", "static.case.law", "",
                                item={"citation": ["410 F.2d 701"]})
        reader = self._open_with(citation=["93 S. Ct. 705", "410 F.2d 701"])
        self.assertEqual(reader.kw["item"]["citation"],
                         ["93 S. Ct. 705", "410 F.2d 701"])


class CitationListTests(unittest.TestCase):
    """A citation resolved out of a list or a brief takes the same order."""

    def test_static_case_law_comes_before_the_courtlistener_lookup(self):
        src = ast.get_source_segment(SRC, next(
            n for c in TREE.body if isinstance(c, ast.ClassDef)
            and c.name == "CourtListenerGUI"
            for n in c.body
            if isinstance(n, ast.FunctionDef) and n.name == "_try_open_citation"
        ))
        self.assertLess(src.index("_case_law_text_source"),
                        src.index("_cl_item_for_citation"))
        self.assertIn('primary_source_kind=cap.kind', src)

    def test_a_pin_cite_still_jumps_in_the_case_law_text(self):
        src = ast.get_source_segment(SRC, next(
            n for c in TREE.body if isinstance(c, ast.ClassDef)
            and c.name == "CourtListenerGUI"
            for n in c.body
            if isinstance(n, ast.FunctionDef) and n.name == "_try_open_citation"
        ))
        cap = src[src.index("cap = _case_law_text_source"):]
        self.assertIn("w.jump_to_cite_page(cite, pin)", cap)


class FollowedCitationTests(unittest.TestCase):
    """And so does a citation followed out of the opinion text."""

    @staticmethod
    def _src(name):
        return ast.get_source_segment(SRC, next(
            n for c in TREE.body if isinstance(c, ast.ClassDef)
            and c.name == "_ScholarTextWindow"
            for n in c.body
            if isinstance(n, ast.FunctionDef) and n.name == name
        ))

    def test_static_case_law_is_asked_before_courtlistener(self):
        src = self._src("_follow_cite_via_cl")
        self.assertLess(src.index("_case_law_text_source"),
                        src.index("_cl_item_for_citation"))

    def test_without_a_token_the_case_law_scan_is_still_reachable(self):
        src = self._src("_follow_cite_via_cl")
        after = src[src.index("if client is None:"):]
        self.assertIn("_try_case_law_link_pdf(cite, pin, name)", after)

    def test_the_window_says_where_the_text_came_from(self):
        src = self._src("_on_case_law_link_ready")
        self.assertIn("primary_source_kind=source.kind", src)
        self.assertIn("primary_source_label=source.source_label", src)
        self.assertIn("win.jump_to_cite_page(cite, pin)", src)


if __name__ == "__main__":
    unittest.main()
