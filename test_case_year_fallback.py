"""A case's year when its opinion prints none, and Spotlight's citations.

Ex parte Milligan's page on Google Scholar prints "71 U.S. 2 (____)" and no
term line, so the window bar and the copied citation went without a year.
Scholar's own results give it — the byline "71 US 2, 18 L. Ed. 281 - Supreme
Court, 1866" — so they are asked first, then CourtListener, then the
Caselaw Access Project.

And a citation typed into Spotlight ("Ex parte Merryman, 17 F. Cas. 144")
opens the case's pages at once, as a search result does, with the text
coming in behind them — also when typed over an earlier search's results.
"""

import ast
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import courtlistener_gui
from courtlistener_gui import (
    CourtListenerGUI,
    _bluebook_display_name,
    _fallback_decision_year,
    _ScholarTextWindow,
)
from google_scholar import GoogleScholarFetcher, ScholarResult

MILLIGAN = "https://scholar.google.com/scholar_case?case=111"
OTHER = "https://scholar.google.com/scholar_case?case=222"


def result(title, url, source):
    return ScholarResult(title=title, url=url, source=source, snippet="",
                         cited_by=0)


class Now:
    def __init__(self, target=None, daemon=None, **_kw):
        self.target = target

    def start(self):
        self.target()


class ScholarBylineYearTests(unittest.TestCase):
    def fetcher(self, cached=None):
        f = object.__new__(GoogleScholarFetcher)
        f._cited_search_cache = {} if cached is None else dict(cached)
        f._name_scorer = None           # the fetcher's own party matching
        return f

    BEARING = [
        result("ex parte Milligan", MILLIGAN,
               "71 US 2, 18 L. Ed. 281 - Supreme Court, 1866"),
        result("Other v. Case", OTHER, "71 US 2 - Supreme Court, 1867"),
    ]

    def test_the_byline_of_the_very_page_the_text_came_from(self):
        f = self.fetcher({"71 U.S. 2": self.BEARING})
        self.assertEqual(f.decision_year("71 U.S. 2", case_url=MILLIGAN),
                         "1866")
        self.assertEqual(f.decision_year("71 U.S. 2", case_url=OTHER),
                         "1867")

    def test_no_neighbors_year_for_a_page_scholar_does_not_list(self):
        f = self.fetcher({"71 U.S. 2": self.BEARING})
        self.assertEqual(
            f.decision_year("71 U.S. 2", case_url=MILLIGAN[:-3] + "999"), "")

    def test_by_name_where_the_page_is_not_known(self):
        f = self.fetcher({"71 U.S. 2": self.BEARING})
        self.assertEqual(
            f.decision_year("71 U.S. 2", case_name="Ex parte Milligan"),
            "1866")

    def test_one_search_when_the_lookup_had_none(self):
        f = self.fetcher()
        f._get = Mock(return_value=SimpleNamespace(
            text='<div id="gs_res_ccl">results</div>'))
        f._cited_results = Mock(return_value=self.BEARING)
        self.assertEqual(f.decision_year("71 U.S. 2", case_url=MILLIGAN),
                         "1866")
        self.assertEqual(f.decision_year("71 U.S. 2", case_url=OTHER),
                         "1867")
        f._get.assert_called_once()
        self.assertIn("%2271+U.S.+2%22", f._get.call_args.args[0])

    def test_a_blocked_search_is_no_answer_and_is_not_kept(self):
        f = self.fetcher()
        f._get = Mock(return_value=SimpleNamespace(text="<p>unusual traffic"))
        self.assertEqual(f.decision_year("71 U.S. 2", case_url=MILLIGAN), "")
        self.assertNotIn("71 U.S. 2", f._cited_search_cache)


class FallbackOrderTests(unittest.TestCase):
    def fallback(self, scholar="", courtlistener="", cap=""):
        fetcher = Mock()
        fetcher.decision_year.return_value = scholar
        with patch.object(_ScholarTextWindow, "_cl_court_and_year",
                          return_value=("scotus", courtlistener)) as cl, \
                patch("courtlistener_gui._case_law_decision_year",
                      return_value=cap) as case_law:
            year = _fallback_decision_year(
                ["71 U.S. 2", "4 Wall. 2"], "Ex parte Milligan", MILLIGAN,
                fetcher=fetcher, client=object())
        return year, fetcher, cl, case_law

    def test_scholar_first(self):
        year, fetcher, cl, case_law = self.fallback(scholar="1866")
        self.assertEqual(year, "1866")
        fetcher.decision_year.assert_called_once_with(
            "71 U.S. 2", case_name="Ex parte Milligan", case_url=MILLIGAN)
        cl.assert_not_called()
        case_law.assert_not_called()

    def test_then_courtlistener(self):
        year, _f, cl, case_law = self.fallback(courtlistener="1866")
        self.assertEqual(year, "1866")
        cl.assert_called_once()
        case_law.assert_not_called()

    def test_then_the_caselaw_access_project(self):
        year, _f, _cl, case_law = self.fallback(cap="1866")
        self.assertEqual(year, "1866")
        case_law.assert_called_once()


class ReporterViewRecordTests(unittest.TestCase):
    def describe(self, record, year_for):
        fake = types.ModuleType("opinion_db")
        fake.extract_record = lambda url, html: dict(record)
        got = []
        with patch.dict(sys.modules, {"opinion_db": fake}):
            CourtListenerGUI._describe_warmed_case(
                (MILLIGAN, "<html></html>"), "Ex parte Milligan", got.append,
                year_for=year_for)
        return got[0]

    def test_a_page_with_no_year_gets_one_from_elsewhere(self):
        asked = []

        def year_for(cites, name):
            asked.append((cites, name))
            return "1866"

        record = self.describe({"name": "Ex parte Milligan",
                                "cites": ["71 U.S. 2", "4 Wall. 2"],
                                "court": "scotus", "year": "",
                                "date_filed": ""}, year_for)
        self.assertEqual(record["year"], "1866")
        self.assertEqual(asked, [(["71 U.S. 2", "4 Wall. 2"],
                                  "Ex parte Milligan")])

    def test_a_page_with_its_own_year_asks_nobody(self):
        record = self.describe(
            {"name": "Roe v. Wade", "cites": ["410 U.S. 113"],
             "court": "scotus", "year": "1973", "date_filed": "1973-01-22"},
            lambda *_a: self.fail("asked elsewhere"))
        self.assertEqual(record["year"], "1973")

    def test_the_title_the_viewer_gets(self):
        self.assertEqual(
            _bluebook_display_name({
                "caseName": "Ex parte Milligan",
                "citation": ["71 U.S. 2", "4 Wall. 2"],
                "dateFiled": "1866-01-01", "court_id": "scotus"}),
            "Ex parte Milligan, 71 U.S. (4 Wall.) 2 (1866)")


class TextWindowTests(unittest.TestCase):
    def test_the_text_window_asks_scholar_first_for_the_year(self):
        fetcher = Mock()
        fetcher.decision_year.return_value = "1866"
        win = object.__new__(_ScholarTextWindow)
        win._bb = {"name": "Ex parte Milligan", "cite": "71 U.S. 2",
                   "court": "", "year": ""}
        win._is_scotus = True
        win._item = {}
        win._header_cites = ["4 Wall. 2"]
        win._base_citation_override = ""
        win._scholar_url = MILLIGAN
        win._app = SimpleNamespace(
            _token_var=SimpleNamespace(get=lambda: ""),
            _get_scholar=lambda: fetcher)
        win._post = Mock()
        with patch("courtlistener_gui.threading.Thread", Now):
            win._enrich_citation()
        fetcher.decision_year.assert_called_once_with(
            "71 U.S. 2", case_name="Ex parte Milligan", case_url=MILLIGAN)
        win._post.assert_called_once_with(
            win._apply_enriched_citation, "", "1866", "Ex parte Milligan")

    def test_federal_cases_text_comes_from_static_case_law(self):
        # Scholar finds hardly any Federal Cases by citation.
        scholar = Mock()
        gui = SimpleNamespace(
            _get_scholar=lambda: scholar,
            _token_var=SimpleNamespace(get=lambda: ""),
            _describe_warmed_case=lambda *_a, **_k: None)
        kept = []
        with patch("courtlistener_gui.threading.Thread", Now), \
                patch("courtlistener_gui._case_law_text_for_scan",
                      return_value="the report's text"):
            CourtListenerGUI._warm_case_text(
                gui, "17 F. Cas. 144", "Ex parte Merryman",
                on_text_source=kept.append,
                scan_url="https://static.case.law/f-cas/17/case-pdfs/"
                         "0144-02.pdf")
        scholar.fetch_by_citation.assert_not_called()
        self.assertEqual(kept, ["the report's text"])


SRC = pathlib.Path(courtlistener_gui.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def gui_source(name):
    return ast.get_source_segment(SRC, next(
        n for c in TREE.body if isinstance(c, ast.ClassDef)
        and c.name == "CourtListenerGUI" for n in c.body
        if isinstance(n, ast.FunctionDef) and n.name == name))


class SpotlightCitationTests(unittest.TestCase):
    def test_a_typed_citation_opens_its_pages_with_the_text_behind(self):
        # Spotlight reads a typed citation through _open_lookup_query.
        self.assertIn("self._open_lookup_query(query",
                      gui_source("_toggle_quick_search_popup"))
        self.assertIn("self._open_typed_case_citation(",
                      gui_source("_open_lookup_query"))
        src = gui_source("_open_typed_case_citation")
        self.assertIn("self.open_cited_case_pdf(", src)
        self.assertIn("fallback=as_text", src)

    def test_so_does_one_typed_over_an_earlier_searchs_results(self):
        self.assertIn("popup._spotlight_submit = _submit",
                      gui_source("_toggle_quick_search_popup"))
        self.assertIn('getattr(popup, "_spotlight_submit", None)',
                      gui_source("_show_spotlight_dropdown"))


if __name__ == "__main__":
    unittest.main()
