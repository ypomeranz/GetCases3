"""Opening the case a citation means when more than one begins on its page.

NetChoice, LLC v. Fitch, 145 S. Ct. 2658 (2025), is not the only case that
begins on that page of the Supreme Court Reporter.  A Google Scholar search for
the quoted citation therefore lists more than one result bearing it, and taking
the first of them opened the wrong case.  The name the citation came with —
all of it, or just "Fitch" — and its year now pick the case; and a copy of the
other case already on hand, in the query cache or the opinion database from an
earlier lookup, is no longer served in its place.

"Harmon v. Delgado" is a made-up stand-in for the other case on that page.
Google Scholar itself is stubbed: the searches and the case pages come from the
fixtures below, and every URL asked for is recorded.
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

from google_scholar import (
    GoogleScholarFetcher,
    ScholarError,
    ScholarResult,
    opinion_caption,
)
from opinion_db import OpinionDB

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None


SCT = "145 S. Ct. 2658"
NETCHOICE, OTHER, CITING = "1111", "2222", "3333"


def _result(case_id: str, title: str, byline: str, snippet: str) -> str:
    """One row of a Scholar results page, marked up the way Scholar does."""
    return (
        '<div class="gs_r gs_or gs_scl">'
        f'<h3 class="gs_rt"><a href="/scholar_case?case={case_id}'
        '&amp;q=%22145+S.+Ct.+2658%22&amp;hl=en&amp;as_sdt=4">'
        f"{title}</a></h3>"
        f'<div class="gs_a">{byline}</div>'
        f'<div class="gs_rs">{snippet}</div></div>'
    )


def _case_page(cite: str, caption: str, docket: str, body: str,
               court: str = "Supreme Court of United States.",
               year: str = "2025") -> str:
    """A scholar_case page: the opinion division, caption and all."""
    return (
        "<html><body><div id=\"gs_opinion\">"
        f"<center><b>{cite} ({year})</b></center>"
        f'<center><h3 id="gsl_case_name">{caption}</h3></center>'
        f'<center><a href="/scholar?scidkt=1">{docket}</a></center>'
        f"<center><p><b>{court}</b></p></center>"
        f"<p>{body}</p></div></body></html>"
    )


PAGES = {
    NETCHOICE: _case_page(
        SCT,
        "NETCHOICE, LLC<br/> v.<br/> LYNN FITCH, ATTORNEY GENERAL OF "
        "MISSISSIPPI",
        "No. 25A97.",
        "The application to vacate stay presented to Justice Alito and by "
        "him referred to the Court is denied."),
    OTHER: _case_page(
        SCT, "HARMON<br/> v.<br/> DELGADO", "No. 25A10.",
        "The application for stay presented to Justice Kagan is denied."),
}

# Scholar's own order puts the other case first — which is what the first
# result bearing the citation used to open.
RESULTS = "".join((
    _result(OTHER, "Harmon v. Delgado",
            "145 S. Ct. 2658 - Supreme Court, 2025",
            "The application for stay presented to Justice Kagan is denied."),
    _result(NETCHOICE, "NetChoice, LLC v. Fitch",
            "145 S. Ct. 2658 - Supreme Court, 2025",
            "The application to vacate stay presented to Justice Alito…"),
    _result(CITING, "Doe v. Citing Co.",
            "140 F.4th 1 - Court of Appeals, 5th Circuit, 2025",
            "… NetChoice, LLC v. Fitch, 145 S. Ct. 2658 (2025) (Kavanaugh, "
            "J., concurring) …"),
))

ONLY_THE_OTHER = "".join((
    _result(OTHER, "Harmon v. Delgado",
            "145 S. Ct. 2658 - Supreme Court, 2025", "…"),
    _result(CITING, "Doe v. Citing Co.",
            "140 F.4th 1 - Court of Appeals, 5th Circuit, 2025", "…"),
))


def _case_url(case_id: str) -> str:
    return f"https://scholar.google.com/scholar_case?case={case_id}"


def _case_id(url: str) -> str:
    return (parse_qs(urlparse(url or "").query).get("case") or [""])[0]


class _FakeScholar:
    """Stands in for ``GoogleScholarFetcher._get``: the results page for any
    search, a case's own page for its scholar_case URL.  Records every URL,
    and raises the way a blocked Scholar does when ``down``."""

    def __init__(self, results=RESULTS, pages=None):
        self.results = results
        self.pages = dict(PAGES if pages is None else pages)
        self.urls: list[str] = []
        self.down = False

    def __call__(self, url, referer=""):
        self.urls.append(url)
        if self.down:
            raise ScholarError("403 blocked")
        if "/scholar_case?" in url:
            text = self.pages[_case_id(url)]
        else:
            text = self.results
        return SimpleNamespace(status_code=200, url=url, text=text)

    @property
    def searches(self) -> list[str]:
        return [u for u in self.urls if "/scholar?" in u]


class _SamePageFixture(unittest.TestCase):
    #: The name scorer the fetcher is given — none (its own token overlap) here,
    #: the application's in the subclass further down.
    name_scorer = None

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.db = None
        self.scholar = _FakeScholar()
        self.fetcher = self._fetcher()

    def tearDown(self):
        # Close the sqlite handles so Windows lets the directory go.
        self.fetcher._db.close()
        if self.db is not None:
            self.db.close()
        self._tmp.cleanup()

    def _fetcher(self, db=None) -> GoogleScholarFetcher:
        fetcher = GoogleScholarFetcher(
            cache_path=self.dir / "cache.db", delay=0.0, db=db,
            name_scorer=self.name_scorer)
        fetcher._browser_dead = True   # never launch a real Firefox
        fetcher._get = self.scholar    # type: ignore[assignment]
        return fetcher

    def _with_database(self, *case_ids) -> None:
        """Rebuild the fetcher over an opinion database holding *case_ids*."""
        self.fetcher._db.close()
        self.db = OpinionDB(jsonl_path=self.dir / "opinions.jsonl",
                            index_path=self.dir / "opinions.db")
        for case_id in case_ids:
            self.assertTrue(
                self.db.add_opinion(_case_url(case_id), PAGES[case_id]))
        self.fetcher = self._fetcher(db=self.db)

    def _cache(self, case_id, key=f"cite2:{SCT}") -> None:
        self.fetcher.put_cached(key, _case_url(case_id), PAGES[case_id])

    def assertOpened(self, got, case_id) -> None:
        self.assertIsNotNone(got, "nothing was opened")
        self.assertEqual(_case_id(got[0]), case_id)


class SamePageResultsTests(_SamePageFixture):
    """Which of the results bearing the citation is opened."""

    def test_without_a_name_the_first_result_bearing_the_cite_is_taken(self):
        # Nothing to tell the cases apart by: Scholar's order, as before.
        self.assertOpened(self.fetcher.fetch_by_citation(SCT), OTHER)

    def test_the_name_picks_the_case_meant(self):
        got = self.fetcher.fetch_by_citation(
            SCT, case_name="NetChoice, LLC v. Fitch")
        self.assertOpened(got, NETCHOICE)
        self.assertIn("NETCHOICE", got[1])

    def test_as_much_of_the_name_as_there_is_will_do(self):
        for name in ("Fitch", "NetChoice", "NetChoice v. Fitch",
                     "NETCHOICE, LLC v. LYNN FITCH"):
            with self.subTest(name=name):
                self.assertOpened(
                    self.fetcher.fetch_by_citation(SCT, case_name=name),
                    NETCHOICE)

    def test_the_other_case_is_still_had_by_its_own_name(self):
        self.assertOpened(
            self.fetcher.fetch_by_citation(SCT, case_name="Harmon v. Delgado"),
            OTHER)

    def test_a_name_no_case_on_the_page_answers_to_opens_nothing(self):
        # Scholar lists only other cases at that page: better that the caller
        # falls back to CourtListener than that a stranger opens.
        got = self.fetcher.fetch_by_citation(
            SCT, case_name="Doe v. Citing Co.")
        self.assertIsNone(got)
        self.assertIsNone(self.fetcher.take_post_search_failure())

    def test_a_name_with_nothing_in_it_counts_as_no_name(self):
        # "In re" alone tells no case from another — and must not turn every
        # result away either.
        for name in ("In re", "et al.", "v."):
            with self.subTest(name=name):
                self.assertOpened(
                    self.fetcher.fetch_by_citation(SCT, case_name=name), OTHER)

    def test_nor_does_a_lone_result_naming_another_case(self):
        self.scholar.results = ONLY_THE_OTHER
        self.assertIsNone(self.fetcher.fetch_by_citation(
            SCT, case_name="NetChoice, LLC v. Fitch"))
        # Without a name there is nothing to say it is the wrong case.
        self.assertOpened(self.fetcher.fetch_by_citation(SCT), OTHER)

    def test_a_lone_result_half_answering_to_the_name_is_still_taken(self):
        # Scholar calls Pennsylvania's cases "Com. v. …": one side answering is
        # not enough to call a lone result a stranger.
        self.scholar.results = _result(
            "4444", "Com. v. Smith", "100 A.3d 1 - Pa: Supreme Court, 2014",
            "…")
        self.scholar.pages = {"4444": _case_page(
            "100 A.3d 1", "COMMONWEALTH of Pennsylvania v. John SMITH",
            "No. 1 MAP 2014", "Affirmed.", "Supreme Court of Pennsylvania.",
            "2014")}
        self.assertOpened(
            self.fetcher.fetch_by_citation(
                "100 A.3d 1", case_name="Commonwealth v. Smith"),
            "4444")

    def test_a_case_that_only_cites_the_page_is_never_a_candidate(self):
        self.assertIsNone(self.fetcher.pick_cited_result(
            GoogleScholarFetcher._parse_results(RESULTS)[2:], SCT,
            "Doe v. Citing Co."))

    def test_the_year_settles_what_the_name_cannot(self):
        rows = [
            ScholarResult("In re Estate of Brown", "u1",
                          "500 N.W.2d 10 - Iowa: Supreme Court, 1992"),
            ScholarResult("In re Estate of Brown", "u2",
                          "500 N.W.2d 10 - Iowa: Court of Appeals, 1993"),
        ]
        pick = self.fetcher.pick_cited_result(
            rows, "500 N.W.2d 10", "In re Estate of Brown", "1993")
        self.assertEqual(pick.url, "u2")
        # Without the year, Scholar's order.
        self.assertEqual(self.fetcher.pick_cited_result(
            rows, "500 N.W.2d 10", "In re Estate of Brown").url, "u1")

    def test_but_never_outweighs_the_name(self):
        rows = [
            ScholarResult("Harmon v. Delgado", "u1",
                          "145 S. Ct. 2658 - Supreme Court, 2024"),
            ScholarResult("NetChoice, LLC v. Fitch", "u2",
                          "145 S. Ct. 2658 - Supreme Court, 2025"),
        ]
        self.assertEqual(self.fetcher.pick_cited_result(
            rows, SCT, "NetChoice, LLC v. Fitch", "2024").url, "u2")


class CopiesOnHandTests(_SamePageFixture):
    """A copy cached, or stored, by an earlier lookup of the same citation."""

    def test_a_cached_page_mate_is_passed_over_for_the_case_named(self):
        self._cache(OTHER)   # what the first-result rule cached before
        got = self.fetcher.fetch_by_citation(
            SCT, case_name="NetChoice, LLC v. Fitch")
        self.assertOpened(got, NETCHOICE)
        self.assertEqual(len(self.scholar.searches), 1)

    def test_and_the_pick_is_remembered_under_the_name(self):
        self._cache(OTHER)
        self.fetcher.fetch_by_citation(
            SCT, case_name="NetChoice, LLC v. Fitch")
        asked = len(self.scholar.urls)
        again = self.fetcher.fetch_by_citation(
            SCT, case_name="NetChoice, LLC v. Fitch")
        self.assertOpened(again, NETCHOICE)
        self.assertEqual(len(self.scholar.urls), asked)   # no network
        # The citation's own copy is untouched: the pick was the name's.
        self.assertEqual(
            _case_id(self.fetcher.get_cached(f"cite2:{SCT}")[0]), OTHER)

    def test_without_a_name_the_cached_copy_still_answers(self):
        self._cache(OTHER)
        self.assertOpened(self.fetcher.fetch_by_citation(SCT), OTHER)
        self.assertEqual(self.scholar.urls, [])

    def test_a_cached_copy_of_the_case_named_is_served_as_is(self):
        self._cache(NETCHOICE)
        self.assertOpened(
            self.fetcher.fetch_by_citation(SCT, case_name="Fitch"), NETCHOICE)
        self.assertEqual(self.scholar.urls, [])

    def test_the_cache_only_lookup_answers_to_the_name_too(self):
        self._cache(OTHER)
        self.assertIsNone(self.fetcher.cached_by_citation(
            SCT, "NetChoice, LLC v. Fitch"))
        self.assertOpened(self.fetcher.cached_by_citation(SCT), OTHER)
        self.assertOpened(
            self.fetcher.cached_by_citation(SCT, "Harmon v. Delgado"), OTHER)

    def test_a_page_mate_is_not_served_even_with_scholar_down(self):
        self._cache(OTHER)
        self.scholar.down = True
        self.assertIsNone(self.fetcher.fetch_by_citation(
            SCT, case_name="NetChoice, LLC v. Fitch"))

    def test_a_copy_half_answering_is_what_there_is_with_scholar_down(self):
        cite = "100 A.3d 1"
        page = _case_page(cite, "COM. v. SMITH", "No. 1 MAP 2014",
                          "Affirmed.", "Supreme Court of Pennsylvania.",
                          "2014")
        self.fetcher.put_cached(f"cite2:{cite}", _case_url("4444"), page)
        self.scholar.down = True
        self.assertOpened(self.fetcher.fetch_by_citation(
            cite, case_name="Commonwealth v. Smith"), "4444")

    def test_the_database_s_page_mate_is_not_served_for_the_case_named(self):
        self._with_database(OTHER)
        got = self.fetcher.fetch_by_citation(
            SCT, case_name="NetChoice, LLC v. Fitch")
        self.assertOpened(got, NETCHOICE)
        self.assertEqual(len(self.scholar.searches), 1)

    def test_with_both_stored_the_name_picks_one_without_the_network(self):
        self._with_database(NETCHOICE, OTHER)
        self.assertOpened(self.fetcher.fetch_by_citation(
            SCT, case_name="NetChoice, LLC v. Fitch"), NETCHOICE)
        self.assertOpened(self.fetcher.fetch_by_citation(
            SCT, case_name="Harmon v. Delgado"), OTHER)
        self.assertEqual(self.scholar.urls, [])

    def test_without_a_name_two_stored_cases_are_left_to_scholar(self):
        self._with_database(NETCHOICE, OTHER)
        self.assertOpened(self.fetcher.fetch_by_citation(SCT), OTHER)
        self.assertEqual(len(self.scholar.searches), 1)


class NameComparisonTests(_SamePageFixture):
    """What counts as a result answering to the name."""

    def _verdict(self, name, caption):
        return self.fetcher._verdict(
            self.fetcher._name_closeness(name, caption))

    def test_a_bluebook_short_form_answers_to_the_name_spelled_out(self):
        self.assertEqual(self._verdict(
            "Nat'l Insts. of Health v. Am. Pub. Health Ass'n",
            "National Institutes of Health v. American Public Health Assn."),
            "match")

    def test_so_does_an_all_capitals_caption(self):
        self.assertEqual(self._verdict(
            "NetChoice, LLC v. Fitch",
            "NETCHOICE, LLC v. LYNN FITCH, ATTORNEY GENERAL OF MISSISSIPPI"),
            "match")

    def test_one_shared_party_leaves_it_in_doubt(self):
        self.assertEqual(
            self._verdict("United States v. Smith", "United States v. Jones"),
            "doubt")

    def test_no_shared_party_is_another_case(self):
        self.assertEqual(
            self._verdict("NetChoice, LLC v. Fitch", "Harmon v. Delgado"),
            "other")

    def test_the_caption_is_read_off_the_page(self):
        self.assertEqual(
            opinion_caption(PAGES[NETCHOICE]),
            "NETCHOICE, LLC v. LYNN FITCH, ATTORNEY GENERAL OF MISSISSIPPI")
        self.assertEqual(opinion_caption("<div><p>No caption</p></div>"), "")


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class WithTheApplicationsNameScorerTests(SamePageResultsTests):
    """The same choices with the name matcher the application injects."""

    name_scorer = staticmethod(gui._name_match_score) if gui else None


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class CopiesOnHandWithTheApplicationsNameScorerTests(CopiesOnHandTests):
    name_scorer = staticmethod(gui._name_match_score) if gui else None


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class WhatTheAppPassesAlongTests(unittest.TestCase):
    """The name and year reach the Scholar lookup from where they are known."""

    def test_the_year_is_read_from_a_typed_citation(self):
        year = gui._citation_line_year
        self.assertEqual(year("NetChoice, LLC v. Fitch, 145 S. Ct. 2658 "
                              "(2025)"), "2025")
        self.assertEqual(year("NetChoice, LLC v. Fitch, 145 S. Ct. 2658, "
                              "2659 (2025) (Kavanaugh, J., concurring)"),
                         "2025")
        self.assertEqual(year("Doe v. Roe, 10 F.4th 20, 25 (5th Cir. 2021)"),
                         "2021")
        self.assertEqual(year("145 S. Ct. 2658"), "")
        self.assertEqual(year("145 S. Ct. 2658 (mem.)"), "")

    def test_a_cluster_gives_its_name_and_year(self):
        item = {"caseName": "<mark>NetChoice</mark>, LLC v. Fitch",
                "dateFiled": "2025-08-14"}
        self.assertEqual(gui._case_name_and_year(item),
                         ("NetChoice, LLC v. Fitch", "2025"))
        # A name the caller has wins over the cluster's.
        self.assertEqual(gui._case_name_and_year(item, "Fitch"),
                         ("Fitch", "2025"))
        self.assertEqual(gui._case_name_and_year(None), ("", ""))

    def _app(self):
        win = object.__new__(gui.CourtListenerGUI)
        win.root = object()
        win._post_root = mock.Mock()
        return win

    def test_a_typed_citation_s_name_and_year_go_to_scholar(self):
        fetcher = mock.Mock()
        fetcher.fetch_by_citation.return_value = ("url", "html")
        self.assertTrue(self._app()._try_open_citation(
            "NetChoice, LLC v. Fitch", SCT, "", fetcher, None, year="2025"))
        fetcher.fetch_by_citation.assert_called_once_with(
            SCT, case_name="NetChoice, LLC v. Fitch", year="2025")

    def test_the_name_and_cite_retry_passes_over_the_page_mate(self):
        # The citation lookup found nothing; the name+cite search's first hit
        # bearing the citation is the other case on the page.
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = GoogleScholarFetcher(
                cache_path=Path(tmp) / "c.db", delay=0.0,
                name_scorer=gui._name_match_score)
            try:
                hits = GoogleScholarFetcher._parse_results(RESULTS)
                opened = []
                with mock.patch.object(fetcher, "fetch_by_citation",
                                       return_value=None), \
                        mock.patch.object(fetcher, "search_cases",
                                          return_value=hits), \
                        mock.patch.object(
                            fetcher, "fetch_by_url",
                            side_effect=lambda u: opened.append(u) or (u, "h")):
                    self.assertTrue(self._app()._try_open_citation(
                        "NetChoice, LLC v. Fitch", SCT, "", fetcher, None))
                self.assertEqual([_case_id(u) for u in opened], [NETCHOICE])
            finally:
                fetcher._db.close()

    def test_a_link_s_caption_goes_with_its_citation_to_scholar(self):
        win = object.__new__(gui._ScholarTextWindow)
        win._link_actions = {"lnk1": ("cite", f"{SCT}@2659")}
        win._text = mock.Mock()
        win._text.tag_ranges.return_value = ("1.0", "1.40")
        win._text.get.return_value = f"NetChoice, LLC v. Fitch, {SCT}, 2659"
        fetcher = mock.Mock()
        fetcher.fetch_by_citation.return_value = None
        win._app = mock.Mock()
        win._app._get_scholar.return_value = fetcher
        win._app.open_cited_case_pdf.return_value = False
        win._following_as_text = False
        win._status_var = mock.Mock()
        win._post = mock.Mock()

        class _Inline:
            def __init__(self, target=None, daemon=None):
                self.target = target

            def start(self):
                self.target()

        with mock.patch.object(gui.threading, "Thread", _Inline):
            win._follow_link("lnk1")

        # The scan lookup was told the name, and so was Scholar.
        self.assertEqual(
            win._app.open_cited_case_pdf.call_args.kwargs["name"],
            "NetChoice, LLC v. Fitch")
        fetcher.fetch_by_citation.assert_called_once_with(
            SCT, case_name="NetChoice, LLC v. Fitch")


if __name__ == "__main__":
    unittest.main()
