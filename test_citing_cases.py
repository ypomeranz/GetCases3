"""The side panel's Citing cases view.

A case's citing cases, ten at a time: Google Scholar's "Cited by" list when
Scholar keeps one for the case — even where it holds only the citation, not
the opinion — with the passage where each case cites it, ordered by
relevance or newest first, narrowed to years and courts, a page at a time;
CourtListener's citation graph when Scholar has no list, one search per page,
newest first, with the passage that cites the case — never the per-case
lookups the old Citing Opinions window made for every row.
"""

import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import citing_cases as cc
import google_scholar as gs
from courtlistener_gui import _ScholarTextWindow

FIXTURE = Path(__file__).with_name("test_data") / "scholar_cites_roe.html"
ROE = "12334123945835207673"


def fixture_page(start=0):
    return gs.parse_citing_page(FIXTURE.read_text(encoding="utf-8"), start)


def text_of(runs):
    return "".join(text for text, _hit in runs)


def hits_of(runs):
    return [text for text, hit in runs if hit]


class ScholarPageTests(unittest.TestCase):
    def test_a_page_of_citing_cases(self):
        page = fixture_page()
        self.assertEqual(page.total, 4290)          # "About 4,290 results"
        self.assertTrue(page.has_next)
        # The row naming the cited case itself is not one of them.
        self.assertEqual(
            [r.title for r in page.results],
            ["Guam Society of Obstetricians and Gynecologists v. Moylan",
             "FUND TEXAS CHOICE v. DESKI",
             "Planned Parenthood Arizona, Inc. v. Mayes"])

    def test_each_case_says_where_it_is_and_how_much_it_discusses(self):
        guam, fund, arizona = fixture_page().results
        self.assertTrue(guam.url.startswith(
            "https://scholar.google.com/scholar_case?case="))
        self.assertEqual((guam.depth, guam.depth_label),
                         (3, "Discusses cited case at length"))
        self.assertEqual(fund.source, "790 F. Supp. 3d 534 - Dist. Court, "
                                      "WD Texas, 2025 - Google Scholar")
        self.assertEqual((arizona.cited_by, arizona.cites_id),
                         (95, "9382635598486422250"))
        self.assertEqual((guam.cited_by, guam.cites_id), (0, ""))

    def test_the_passage_keeps_scholar_s_highlighting(self):
        guam = fixture_page().results[0]
        self.assertEqual(hits_of(guam.passage), ["Roe", "v", "Wade"])
        text = text_of(guam.passage)
        self.assertTrue(text.startswith("… Second, while Roe v. Wade, "
                                        "410 US 113 (1973), may no longer"))
        self.assertTrue(text.endswith("practical …"))
        self.assertNotIn("  ", text)

    def test_a_list_sorted_by_date_drops_scholar_s_age_stamp(self):
        html = ('<div id="gs_res_ccl"><div class="gs_r gs_or gs_scl">'
                '<h3 class="gs_rt"><a href="/scholar_case?case=1">A v. B</a>'
                '</h3><div class="gs_a">Court of Appeals, 9th Circuit, 2010 '
                '- Google Scholar</div><div class="gs_rs"><span class='
                '"gs_age">16 years ago - </span>See <b>Roe</b> v. '
                '<b>Wade</b>.</div></div></div>')
        (result,) = gs.parse_citing_page(html).results
        self.assertEqual(text_of(result.passage), "See Roe v. Wade.")
        # One page and no page links: nothing after it.
        self.assertFalse(gs.parse_citing_page(html).has_next)

    def test_a_case_scholar_holds_only_the_citation_of(self):
        html = ('<div id="gs_res_ccl"><div class="gs_r gs_or gs_scl">'
                '<div class="gs_ri"><h3 class="gs_rt"><span class="gs_ctu">'
                '<span class="gs_ct1">[CITATION]</span><span class="gs_ct2">'
                '[C]</span></span> Smith v. Jones</h3><div class="gs_a">'
                '12 F.2d 34 - 1925</div><div class="gs_fl"><a href="/scholar?'
                'cites=777&amp;as_sdt=2006">Cited by 12</a></div></div></div>'
                '</div>')
        (result,) = gs.parse_citing_page(html).results
        self.assertEqual((result.title, result.url), ("Smith v. Jones", ""))
        bare = gs.GoogleScholarFetcher._parse_results(html, bare=True)
        self.assertEqual([(r.title, r.url, r.cites_id, r.cited_by)
                          for r in bare], [("Smith v. Jones", "", "777", 12)])
        # The search results the rest of the app opens keep to opinions.
        self.assertEqual(gs.GoogleScholarFetcher._parse_results(html), [])

    def test_the_list_s_address(self):
        self.assertEqual(
            gs.citing_url(ROE),
            f"https://scholar.google.com/scholar?cites={ROE}&as_sdt=2006"
            "&hl=en")
        self.assertEqual(
            gs.citing_url(ROE, by_date=True, year_from=2000, year_to=2010,
                          courts={"ca9"}, start=10),
            f"https://scholar.google.com/scholar?cites={ROE}"
            "&as_sdt=4,129,114&hl=en&scisbd=2&as_ylo=2000&as_yhi=2010"
            "&start=10")
        # Courts Scholar does not carry: no list to ask for.
        self.assertIsNone(gs.citing_url(ROE, courts={"nmid"}))


class _Response:
    def __init__(self, text):
        self.text = text


class ScholarFetchTests(unittest.TestCase):
    def fetcher(self):
        fetcher = object.__new__(gs.GoogleScholarFetcher)
        fetcher._citing_cache = {}
        fetcher._cited_search_cache = {}
        fetcher._name_scorer = None
        fetcher._get = Mock(return_value=_Response(
            FIXTURE.read_text(encoding="utf-8")))
        return fetcher

    def test_a_page_is_read_once(self):
        fetcher = self.fetcher()
        first = fetcher.citing_page(ROE)
        again = fetcher.citing_page(ROE)
        self.assertIs(first, again)
        fetcher._get.assert_called_once()

    def test_a_challenge_is_not_an_empty_list(self):
        fetcher = self.fetcher()
        fetcher._get.return_value = _Response("<html>unusual traffic</html>")
        with self.assertRaises(gs.ScholarError):
            fetcher.citing_page(ROE)

    def test_courts_scholar_lacks_ask_nothing(self):
        fetcher = self.fetcher()
        page = fetcher.citing_page(ROE, courts={"nmid"})
        self.assertEqual((page.results, page.total), ([], 0))
        fetcher._get.assert_not_called()

    def test_the_list_found_by_searching_for_the_citation(self):
        fetcher = self.fetcher()
        fetcher._get.return_value = _Response(
            '<div id="gs_res_ccl"><div class="gs_r gs_or gs_scl">'
            '<h3 class="gs_rt"><span class="gs_ctu"><span class="gs_ct1">'
            '[CITATION]</span></span> Smith v. Jones</h3><div class="gs_a">'
            '12 F.2d 34 - 1925</div><a href="/scholar?cites=777">Cited by 3'
            '</a></div><div class="gs_r gs_or gs_scl"><h3 class="gs_rt">'
            '<a href="/scholar_case?case=9">Doe v. Roe</a></h3><div class='
            '"gs_a">50 F.3d 1 - 1995</div><div class="gs_rs">citing 12 F.2d '
            '34</div><a href="/scholar?cites=9">Cited by 4</a></div></div>')
        self.assertEqual(fetcher.find_citing_id("12 F.2d 34", "Smith v. "
                                                "Jones"), "777")
        # The case that merely cites the citation is not the case.
        self.assertEqual(fetcher.find_citing_id("99 F.2d 1"), "")

    def test_a_lookup_already_made_answers_without_searching(self):
        fetcher = self.fetcher()
        fetcher._cited_search_cache["410 U.S. 113"] = [gs.ScholarResult(
            title="Roe v. Wade", url=f"x?case={ROE}",
            source="410 US 113 - Supreme Court, 1973", cites_id=ROE)]
        self.assertEqual(fetcher.find_citing_id("410 U.S. 113"), ROE)
        fetcher._get.assert_not_called()


def scholar_page(n=10, total=4290, has_next=True):
    return gs.CitingPage(
        [gs.CitingResult(title=f"Case {i}", url=f"x?case={i}")
         for i in range(n)], total=total, has_next=has_next)


class FakeScholar:
    """A Scholar fetcher holding lists by id."""

    def __init__(self, lists=None, found="", error=None):
        self.lists = lists or {}
        self.found = found
        self.error = error
        self.asked = []

    def citing_page(self, cites_id, **options):
        self.asked.append((cites_id, options))
        if self.error is not None:
            raise self.error
        return self.lists.get(cites_id, gs.CitingPage([], total=0))

    def find_citing_id(self, citation, name="", year=""):
        if self.error is not None:
            raise self.error
        return self.found


def cl_results(start, n):
    return [{"caseName": f"CL {i}", "cluster_id": i,
             "opinions": [{"id": 1000 + i, "cites": [5],
                           "snippet": f"see <mark>Roe v. Wade</mark> {i}"}]}
            for i in range(start, start + n)]


class FakeClient:
    """CourtListener's search, twenty results a page whatever is asked."""

    def __init__(self, total=45):
        self.total = total
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        start = int(kwargs.get("cursor") or 0)
        n = max(0, min(20, self.total - start))
        more = start + n < self.total
        return {"count": self.total, "results": cl_results(start, n),
                "next": (f"https://x/?cursor={start + 20}" if more else None)}


def target_for(client, phrases=("410 U.S. 113", "Roe v. Wade")):
    return lambda: cc.CourtListenerTarget(client, [5, 6], list(phrases))


class ChoosingTheSourceTests(unittest.TestCase):
    def test_scholar_s_list_when_it_keeps_one(self):
        scholar = FakeScholar({ROE: scholar_page()})
        cl = Mock()
        lookup = cc.CitingLookup(scholar=scholar, scholar_ids=[ROE],
                                 courtlistener=cl)
        page = lookup.page(cc.Filters())
        self.assertEqual((page.source, len(page.cases), page.total,
                          page.total_estimated, page.newest_first),
                         (cc.SCHOLAR, 10, 4290, True, False))
        cl.assert_not_called()                      # never asked

    def test_the_first_id_with_a_list_on_it(self):
        scholar = FakeScholar({"2": scholar_page(3, 3, False)})
        lookup = cc.CitingLookup(scholar=scholar, scholar_ids=["1", "2"])
        page = lookup.page(cc.Filters())
        self.assertEqual(len(page.cases), 3)
        self.assertEqual(scholar.asked[-1][0], "2")

    def test_a_list_found_by_the_citation(self):
        # Scholar holds only the citation — no opinion, no id from it — but
        # keeps a list of the cases citing it all the same.
        scholar = FakeScholar({"777": scholar_page(4, 4, False)},
                              found="777")
        lookup = cc.CitingLookup(scholar=scholar, citation="12 F.2d 34",
                                 name="Smith v. Jones")
        self.assertEqual(lookup.page(cc.Filters()).source, cc.SCHOLAR)

    def test_courtlistener_when_scholar_has_no_list(self):
        client = FakeClient()
        lookup = cc.CitingLookup(scholar=FakeScholar(), scholar_ids=[ROE],
                                 courtlistener=target_for(client))
        page = lookup.page(cc.Filters())
        self.assertEqual(page.source, cc.COURTLISTENER)
        self.assertEqual(page.note,
                         "Google Scholar lists no cases citing this one.")
        self.assertTrue(page.newest_first)          # CourtListener's default
        self.assertEqual(client.calls[0][1]["extra"],
                         {"order_by": "dateFiled desc"})

    def test_courtlistener_when_scholar_does_not_answer(self):
        scholar = FakeScholar({ROE: scholar_page()},
                              error=gs.ScholarError("HTTP 429 blocked"))
        lookup = cc.CitingLookup(scholar=scholar, scholar_ids=[ROE],
                                 courtlistener=target_for(FakeClient()))
        page = lookup.page(cc.Filters())
        self.assertEqual(page.source, cc.COURTLISTENER)
        self.assertIn("not answering", page.note)
        self.assertTrue(lookup.scholar_unanswered)
        # Asked again, Scholar answers.
        scholar.error = None
        lookup.retry_scholar()
        self.assertEqual(lookup.page(cc.Filters()).source, cc.SCHOLAR)

    def test_a_list_being_read_is_not_switched_under_the_reader(self):
        scholar = FakeScholar({ROE: scholar_page()})
        lookup = cc.CitingLookup(scholar=scholar, scholar_ids=[ROE],
                                 courtlistener=target_for(FakeClient()))
        lookup.page(cc.Filters())
        scholar.error = gs.ScholarError("blocked")
        page = lookup.page(cc.Filters(), 1)
        self.assertEqual((page.source, page.failed), (cc.SCHOLAR, True))
        lookup.use_courtlistener()
        self.assertEqual(lookup.page(cc.Filters()).source, cc.COURTLISTENER)

    def test_scholar_is_asked_in_the_order_and_span_chosen(self):
        scholar = FakeScholar({ROE: scholar_page()})
        lookup = cc.CitingLookup(scholar=scholar, scholar_ids=[ROE])
        lookup.page(cc.Filters(by_date=True, year_from=1990, year_to=2000,
                               courts=frozenset({"ca9"})), 2)
        self.assertEqual(scholar.asked[-1], (ROE, {
            "by_date": True, "year_from": 1990, "year_to": 2000,
            "courts": frozenset({"ca9"}), "start": 20}))

    def test_without_a_token_courtlistener_is_not_asked(self):
        lookup = cc.CitingLookup(scholar=FakeScholar())
        page = lookup.page(cc.Filters())
        self.assertEqual(page.source, "")
        self.assertIn("API token", page.note)

    def test_a_case_courtlistener_lacks(self):
        lookup = cc.CitingLookup(scholar=None, courtlistener=lambda: None)
        self.assertIn("does not have this case",
                      lookup.page(cc.Filters()).note)


class CourtListenerPagesTests(unittest.TestCase):
    def lookup(self, client):
        return cc.CitingLookup(scholar=None, courtlistener=target_for(client))

    def test_ten_at_a_time_from_pages_of_twenty(self):
        # Asked for ten, CourtListener sends twenty: the second ten are the
        # next page, not skipped for the twenty-first.
        client = FakeClient(total=45)
        lookup = self.lookup(client)
        first = lookup.page(cc.Filters())
        second = lookup.page(cc.Filters(), 1)
        self.assertEqual([c["cluster_id"] for c in first.cases],
                         list(range(10)))
        self.assertEqual([c["cluster_id"] for c in second.cases],
                         list(range(10, 20)))
        self.assertEqual(len(client.calls), 1)      # one search, two pages
        third = lookup.page(cc.Filters(), 2)
        self.assertEqual(third.cases[0]["cluster_id"], 20)
        self.assertEqual(client.calls[1][1]["cursor"], "20")
        last = lookup.page(cc.Filters(), 4)
        self.assertEqual([c["cluster_id"] for c in last.cases],
                         list(range(40, 45)))
        self.assertFalse(last.has_next)
        self.assertTrue(third.has_next)
        self.assertEqual(last.total, 45)

    def test_the_search_carries_the_years_courts_and_order(self):
        client = FakeClient()
        self.lookup(client).page(cc.Filters(
            by_date=False, year_from=2000, year_to=2010,
            courts=frozenset({"ca9", "ca2"})))
        query, options = client.calls[0]
        self.assertEqual(options["court"], "ca2 ca9")
        self.assertEqual((options["date_filed_min"], options["date_filed_max"]),
                         ("2000-01-01", "2010-12-31"))
        self.assertEqual(options["extra"], {"order_by": "score desc"})
        self.assertTrue(options["highlight"])
        self.assertEqual(query, '(cites:5 OR cites:6) AND ((cites:5 OR '
                                'cites:6) OR "410 U.S. 113" OR "Roe v. Wade")')

    def test_each_list_keeps_its_own_place(self):
        client = FakeClient()
        lookup = self.lookup(client)
        lookup.page(cc.Filters())
        lookup.page(cc.Filters(courts=frozenset({"ca9"})))
        self.assertEqual(len(client.calls), 2)
        lookup.page(cc.Filters(), 1)                # still in hand
        self.assertEqual(len(client.calls), 2)

    def test_courtlistener_not_answering(self):
        client = FakeClient()
        client.search = Mock(side_effect=RuntimeError("HTTP 502"))
        page = self.lookup(client).page(cc.Filters())
        self.assertEqual((page.source, page.failed), (cc.COURTLISTENER, True))
        self.assertIn("HTTP 502", page.note)


class CourtListenerTextTests(unittest.TestCase):
    def test_the_query(self):
        self.assertEqual(cc.cl_query([7]), "(cites:7)")
        self.assertEqual(
            cc.cl_query([7], ['Roe v. "Wade"', "Roe v. Wade", "  ", "ab"]),
            '(cites:7) AND ((cites:7) OR "Roe v. Wade")')

    def test_the_passage_as_runs(self):
        runs = cc.mark_runs(
            "Casey, 505 U.S. 833, 852 (1992); <mark>Roe v. Wade</mark>, "
            "<mark>410 U.S. 113</mark>, 116\n  (1973). Dobbs, 597 U.S. at "
            "231�32, Hawai�i")
        self.assertEqual(hits_of(runs), ["Roe v. Wade", "410 U.S. 113"])
        self.assertEqual(
            text_of(runs),
            "… Casey, 505 U.S. 833, 852 (1992); Roe v. Wade, 410 U.S. 113, "
            "116 (1973). Dobbs, 597 U.S. at 231–32, Hawai’i …")

    def test_an_opinion_s_first_lines_are_no_passage(self):
        self.assertEqual(cc.mark_runs("UNITED STATES COURT OF APPEALS\n"), [])

    def test_the_passage_from_the_writing_that_cites_the_case(self):
        result = {"opinions": [
            {"type": "combined-opinion", "cites": [1],
             "snippet": "other <mark>Roe</mark>"},
            {"type": "dissent", "cites": [5],
             "snippet": "but see <mark>Roe v. Wade</mark>"},
        ]}
        runs, writing = cc.cl_passage(result, [5])
        self.assertEqual((text_of(runs), writing),
                         ("… but see Roe v. Wade …", "dissent"))
        self.assertEqual(cc.cl_passage({"opinions": []}, [5]), ([], ""))

    def test_fields_as_plain_text(self):
        self.assertEqual(cc.clean_text("<mark>Roe</mark> v. Wade &amp; Co."),
                         "Roe v. Wade & Co.")


def panel():
    win = object.__new__(_ScholarTextWindow)
    win._app = Mock()
    win._win = Mock()
    win._status_var = Mock()
    win._citing_filters = cc.Filters()
    return win


class PanelRowTests(unittest.TestCase):
    def test_a_scholar_case(self):
        fund = fixture_page().results[1]
        name, meta, depth, extra, passage, opener = (
            panel()._citing_scholar_row(fund))
        self.assertEqual(name, "Fund Texas Choice v. Deski")
        self.assertEqual(meta, "790 F. Supp. 3d 534 · W.D. Tex. · 2025")
        self.assertEqual((depth, extra),
                         (3, "Discusses it at length · Cited by 1"))
        self.assertEqual(hits_of(passage), ["Roe", "v", "Wade"])
        self.assertIsNotNone(opener)

    def test_a_scholar_court_of_appeals_and_a_state_court(self):
        guam, _fund, arizona = fixture_page().results
        win = panel()
        self.assertEqual(win._citing_scholar_row(guam)[1], "9th Cir. · 2026")
        self.assertEqual(win._citing_scholar_row(arizona)[1],
                         "545 P.3d 892 · Ariz. · 2024")

    def test_a_courtlistener_case(self):
        result = {
            "caseName": "Rachel Welty v. Bryant Dunaway", "court_id": "ca6",
            "court_citation_string": "6th Cir.", "dateFiled": "2026-09-24",
            "citation": [], "cluster_id": 10983171,
            "opinions": [{"type": "dissent", "cites": [108713],
                          "snippet": "<mark>Roe v. Wade</mark>, 410 U.S."}],
        }
        name, meta, depth, extra, passage, opener = (
            panel()._citing_cl_row(result, [108713]))
        self.assertEqual(name, "Rachel Welty v. Bryant Dunaway")
        self.assertEqual(meta, "6th Cir. · Sept. 24, 2026")
        self.assertEqual((depth, extra), (0, "In a dissent"))
        self.assertEqual(hits_of(passage), ["Roe v. Wade"])

    def test_the_heading(self):
        win = panel()
        page = cc.CitingPage(cc.SCHOLAR, [object()] * 10, 1, total=4290,
                             total_estimated=True)
        self.assertEqual(win._citing_heading(page),
                         "Google Scholar · about 4,290 citing cases · 11–20")
        page = cc.CitingPage(cc.COURTLISTENER, [object()] * 3, 0, total=3,
                             newest_first=True)
        self.assertEqual(win._citing_heading(page),
                         "CourtListener · 3 citing cases · 1–3 · newest first")

    def test_the_view_is_offered_and_chosen(self):
        win = panel()
        win._details_mode_combo = Mock()
        win._details_mode_combo.get.return_value = "Citing cases"
        self.assertEqual(win._details_mode(), "citing")


class OpeningACitingCaseTests(unittest.TestCase):
    def test_a_scholar_case_opens_like_a_scholar_search_hit(self):
        win = panel()
        fund = fixture_page().results[1]
        *_rest, opener = win._citing_scholar_row(fund)
        opener()
        (result, cite), _kw = win._app._scholar_result_opener.call_args
        self.assertEqual((result.url, cite), (fund.url, "790 F. Supp. 3d 534"))
        win._app._scholar_result_opener.return_value.assert_called_once()

    def test_a_case_scholar_holds_only_the_citation_of_is_followed_by_it(
            self):
        win = panel()
        case = gs.CitingResult(title="Smith v. Jones", url="",
                               source="12 F.2d 34 - 1925")
        *_rest, opener = win._citing_scholar_row(case)
        with patch("courtlistener_gui._follow_brief_action") as follow:
            opener()
        (_app, _parent, action), kwargs = follow.call_args
        self.assertEqual(action, ("cite", "12 F.2d 34"))
        self.assertEqual(kwargs["snippet"], "Smith v. Jones, 12 F.2d 34")

    def test_nothing_to_open_by_is_no_link(self):
        case = gs.CitingResult(title="Unknown", url="", source="1925")
        self.assertIsNone(panel()._citing_scholar_row(case)[-1])

    def test_a_courtlistener_case_opens_like_a_main_window_result(self):
        win = panel()
        result = {"caseName": "A v. B", "cluster_id": 3, "opinions": []}
        *_rest, opener = win._citing_cl_row(result, [])
        opener()
        (item,), kwargs = win._app.open_search_result.call_args
        self.assertEqual(item["cluster_id"], 3)


class YearsTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.root.withdraw()
        self.win = panel()
        self.win._citing_from_var = tk.StringVar(master=self.root)
        self.win._citing_to_var = tk.StringVar(master=self.root)
        self.win._set_citing_filters = Mock()

    def tearDown(self):
        self.root.destroy()

    def test_the_span_runs_from_the_earlier_year(self):
        self.win._citing_from_var.set("2010")
        self.win._citing_to_var.set(" 1990 ")
        self.win._citing_years_entered()
        (filters,), _kw = self.win._set_citing_filters.call_args
        self.assertEqual((filters.year_from, filters.year_to), (1990, 2010))
        self.assertEqual(self.win._citing_from_var.get(), "1990")

    def test_one_end_open(self):
        self.win._citing_to_var.set("1999")
        self.win._citing_years_entered()
        (filters,), _kw = self.win._set_citing_filters.call_args
        self.assertEqual((filters.year_from, filters.year_to), (None, 1999))

    def test_not_a_year(self):
        self.win._citing_from_var.set("99")
        self.win._citing_years_entered()
        self.win._set_citing_filters.assert_not_called()
        self.win._status_var.set.assert_called_once()


class RenderTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.root.withdraw()
        self.win = panel()
        self.win._details_text = tk.Text(self.root)
        self.win._details_title_var = tk.StringVar(master=self.root)
        self.win._citing_loading = False
        self.win._citing_lookup = None

    def tearDown(self):
        self.root.destroy()

    def shown(self):
        return self.win._details_text.get("1.0", "end")

    def test_a_scholar_page(self):
        page = fixture_page()
        self.win._citing_page = cc.CitingPage(
            cc.SCHOLAR, page.results, 0, total=4290, total_estimated=True,
            has_next=True)
        self.win._render_citing()
        text = self.shown()
        self.assertEqual(self.win._details_title_var.get(), "Citing Cases")
        self.assertIn("Google Scholar · about 4,290 citing cases · 1–3", text)
        self.assertIn("Fund Texas Choice v. Deski\n790 F. Supp. 3d 534", text)
        self.assertIn("▮▮▮ Discusses it at length · Cited by 1", text)
        self.assertIn("Next 10 ▶", text)
        self.assertNotIn("Previous", text)

    def test_courtlistener_s_list_says_why(self):
        self.win._citing_lookup = Mock(scholar_unanswered=True)
        self.win._citing_page = cc.CitingPage(
            cc.COURTLISTENER, [], 0, total=0,
            note="Google Scholar is not answering right now, so these are "
                 "CourtListener's citing cases.")
        self.win._render_citing()
        text = self.shown()
        self.assertIn("Try Google Scholar again", text)
        self.assertIn("No citing cases found.", text)

    def test_while_the_first_page_is_read(self):
        self.win._citing_page = None
        self.win._citing_loading = True
        self.win._render_citing()
        self.assertIn("Looking for the cases citing this one", self.shown())


if __name__ == "__main__":
    unittest.main()
