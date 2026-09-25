"""Following a Google Scholar link to a case Scholar holds no text of.

Nash v. United States, 229 U.S. 373 (1913), cites "Commonwealth v. Pierce, 138
Massachusetts, 165, 178" and "Commonwealth v. Chance, 174 Massachusetts, 245,
252".  Scholar links each to its "How cited" page (``scholar_case?about=``) —
the citing passages, never an opinion — because it matched neither citation to
an opinion it holds.  Two things went wrong following them:

* The comma the U.S. Reports then set between reporter and page ("138
  Massachusetts, 165") kept the citation from being read at all, so the link
  had only its case name to go on, and CourtListener's name search opened
  whichever "Commonwealth v. Pierce" it ranked first.
* An empty answer from Scholar was taken for a busy Scholar: the "How cited"
  page was asked for again and again, as if the opinion might yet appear on
  it, and so was every search that had already found nothing.

Now the older comma form reads as a citation, and the fetcher tells an answer
("no copy of that here") from a failure to ask (a block, a timeout), so only
the second is retried.  Scholar itself is stubbed: every URL asked of it is
recorded.
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import requests

import citations
from google_scholar import (
    GoogleScholarFetcher,
    ScholarError,
    is_how_cited_url,
    parse_opinion_blocks,
)

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None


PIERCE_URL = ("https://scholar.google.com/scholar_case?about=8429242000591666836"
              "&q=%22496+U.S.+310%22&hl=en&as_sdt=2006")

# The passage of Nash v. United States as Scholar serves it.
NASH = (
    '<div id="gs_opinion"><p>"The very meaning of the fiction of implied '
    "malice in such cases at common law was, that a man might have to answer "
    "with his life for consequences which he neither intended nor foresaw.\" "
    '<a class="gsl_co_link" href="/scholar_case?about=8429242000591666836'
    '&amp;q=%22496+U.S.+310%22&amp;hl=en&amp;as_sdt=2006"><i>Commonwealth</i>'
    " v. <i>Pierce,</i> 138 Massachusetts, 165, 178</a>. "
    '<a class="gsl_co_link" href="/scholar_case?about=14711276049707085020'
    '&amp;q=%22496+U.S.+310%22&amp;hl=en&amp;as_sdt=2006"><i>Commonwealth</i>'
    " v. <i>Chance,</i> 174 Massachusetts, 245, 252</a>. \"The criterion in "
    "such cases is to examine whether common social duty would, under the "
    "circumstances, have suggested a more circumspect conduct.\"</p></div>"
)

# Scholar's "How cited" page: the case as Scholar records it, and the
# passages citing it — no #gs_opinion.
HOW_CITED = (
    "<html><head><title>Commonwealth v. Pierce, 138 Mass. 165 - 1884 - Google "
    'Scholar</title></head><body><div id="gs_ab_md"><h1 dir="ltr">'
    "Commonwealth v. Pierce, 138 Mass. 165 - 1884</h1></div>"
    '<div id="gs_bdy_ccl"><div id="gs_howcited_ccl"><h3 class="gs_section_title">'
    'How this document has been cited</h3><div class="gs_result">'
    '<div class="gs_citation">- in <a href="/scholar_case?case=4663763378165729878">'
    "Stoianoff v. State of Montana, 1981</a></div></div></div></div></body></html>"
)

# A results page on which nothing is the case searched for: Scholar lists
# the citation-only record without a link, then opinions citing it.
NO_MATCH = (
    '<html><body><div id="gs_res_ccl"><div id="gs_res_ccl_mid">'
    '<div class="gs_r gs_or gs_scl"><h3 class="gs_rt">'
    '<span class="gs_ctu">[CITATION]</span> Commonwealth v. Pierce</h3>'
    '<div class="gs_a">138 Mass. 165 - 1884</div></div>'
    '<div class="gs_r gs_or gs_scl"><h3 class="gs_rt">'
    '<a href="/scholar_case?case=18172200469031796162">Commonwealth v. Burke'
    '</a></h3><div class="gs_a">457 NE 2d 622, 390 Mass. 480 - Mass: Supreme '
    "Judicial Court, 1983</div></div></div></div></body></html>"
)

# Not a results page at all: a bot check served with a 200.
INTERSTITIAL = ('<html><body><form id="gs_captcha_f">Please show you\'re not '
                "a robot</form></body></html>")


class _FakeScholar:
    """Stands in for ``GoogleScholarFetcher._get``: a page per URL (a
    callable page raises instead), recording every URL asked for."""

    def __init__(self, page=NO_MATCH):
        self.page = page
        self.urls: list[str] = []

    def __call__(self, url, referer=""):
        self.urls.append(url)
        page = self.page(url) if callable(self.page) else self.page
        return SimpleNamespace(status_code=200, url=url, text=page)


def _raise(exc):
    def page(_url):
        raise exc
    return page


class _Inline:
    """``threading.Thread`` run in place, so a worker's posts are made
    before the test looks."""

    def __init__(self, target=None, daemon=None):
        self.target = target

    def start(self):
        self.target()


# ---------------------------------------------------------------------------
# Reading the citation
# ---------------------------------------------------------------------------

class OlderCommaFormTests(unittest.TestCase):
    """"138 Massachusetts, 165" — a comma between reporter and page."""

    def test_nash_s_citations_are_read(self):
        for text, cite, pin in (
            ("Commonwealth v. Pierce, 138 Massachusetts, 165, 178",
             "138 Massachusetts 165", "178"),
            ("Commonwealth v. Chance, 174 Massachusetts, 245, 252",
             "174 Massachusetts 245", "252"),
        ):
            with self.subTest(text=text):
                self.assertEqual(citations.cite_target_from_text(text, {}),
                                 (cite, pin))

    def test_in_every_kind_of_reporter_it_was_used_with(self):
        for text, cite in (
            ("Marbury v. Madison, 1 Cranch, 137", "1 Cranch 137"),
            ("Johnson v. McIntosh, (8 Wheat., 595,)", "8 Wheat. 595"),
            ("Ex parte Crouch, 112 U.S., 178, 180", "112 U.S. 178"),
            ("Commonwealth v. Savings Bank, 5 Allen, 431", "5 Allen 431"),
            ("Winney v. Whitesides, 1 Mo., 473", "1 Mo. 473"),
            ("Cochran v. McCleary, 22 Iowa, 75", "22 Iowa 75"),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    citations.cite_target_from_text(text, {})[0], cite)

    def test_it_links_in_running_text(self):
        text = ('"… which he neither intended nor foresaw." Commonwealth v. '
                "Pierce, 138 Massachusetts, 165, 178. Commonwealth v. Chance, "
                "174 Massachusetts, 245, 252.")
        self.assertEqual(
            [action for _s, _e, action in citations.detect_links(text)],
            [("cite", "138 Massachusetts 165@178"),
             ("cite", "174 Massachusetts 245@252")])

    def test_prose_of_the_same_shape_is_left_alone(self):
        for text in (
            "In 1990, 3 Bush, 5 Clinton appointees sat on the court.",
            "the jury of 1 Black, 2 Hispanic and 9 white jurors",
            "the venire: 12 white, 1 Black, 2 Hispanic",
            "notice given on 1 Jan., 1990",
            "(2 Cir., 1940)",
            "In 1990 Congress, 12 States objected",
        ):
            with self.subTest(text=text):
                self.assertEqual(citations.iter_case_citations(text), ())

    def test_only_a_reporter_known_by_name(self):
        # A treatise is cited in the same shape, but is no reporter.
        self.assertEqual(
            citations.cite_target_from_text(
                "Mitchel v. Reynolds, 1 Smith's Leading Cases, 705", {}),
            ("", ""))

    def test_each_nominative_cite_is_found_once(self):
        text = "Marbury v. Madison, 1 Cranch 137; Smith v. Jones, 19 Pick. 234"
        self.assertEqual(
            [citations.case_match_text(m)
             for m in citations.iter_case_citations(text)],
            ["1 Cranch 137", "19 Pick. 234"])


class ShortFormWithoutAtTests(unittest.TestCase):
    """A modern short form that drops its "at" takes the same shape — "131
    S.Ct., 1157" — and is a pin into the case the text cites in full."""

    TEXT = ("Michigan v. Bryant, 562 U.S. 344, 131 S.Ct. 1143 (2011). Later: "
            "id., at 358-359, 131 S.Ct., 1157-1158. And Bryant, supra, at 379, "
            "131 S.Ct., at 1167.")

    def test_it_is_no_case_of_its_own(self):
        self.assertEqual(citations.build_short_cite_index(self.TEXT),
                         {("562", "us"): [344], ("131", "sct"): [1143]})

    def test_later_short_forms_still_find_the_case(self):
        actions = [action for _s, _e, action in citations.detect_links(self.TEXT)]
        self.assertIn(("cite", "131 S.Ct. 1143@1167"), actions)
        self.assertNotIn(("cite", "131 S.Ct. 1157"), actions)

    def test_a_link_reading_it_pins_the_case(self):
        index = citations.build_short_cite_index(
            "Wilson v. Seiter, 501 U. S. 294 (1991).")
        self.assertEqual(
            citations.cite_target_from_text(
                "Wilson, 501 U. S., 306-309", index),
            ("501 U.S. 294", "306"))

    def test_a_whole_case_name_makes_a_case_of_its_own(self):
        # Brown v. Keene, 8 Pet. 112, and Jackson v. Ashton, 8 Pet. 148, are
        # two cases in one volume, 36 pages apart.
        index = citations.build_short_cite_index(
            "Brown v. Keene, 8 Peters 112; Ex parte Watkins, 3 Peters 193.")
        for text, cite in (
            ("Jackson v. Ashton, (8 Peters, 148;)", "8 Peters 148"),
            ("Ex parte Kearney, 3 Peters, 201", "3 Peters 201"),
            ("Ashton, 8 Peters, 148", "8 Peters 112"),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    citations.cite_target_from_text(text, index)[0], cite)


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class NashLinkActionTests(unittest.TestCase):
    """What clicking the Pierce and Chance links in Nash sets off."""

    def _actions(self):
        win = object.__new__(gui._ScholarTextWindow)
        win._short_cite_index = {}
        links: dict[str, str] = {}
        for block in parse_opinion_blocks(NASH):
            for span in block.spans:
                if span.link:
                    links[span.link] = links.get(span.link, "") + span.text
        return [win._scholar_link_action(text, href)
                for href, text in links.items()]

    def test_each_link_carries_its_citation_pin_and_name(self):
        (kind1, pierce), (kind2, chance) = self._actions()
        self.assertEqual((kind1, kind2), ("url", "url"))
        self.assertTrue(is_how_cited_url(pierce.split("\t")[0]))
        self.assertEqual(pierce.split("\t")[1:], [
            "pin=178", "cite=138 Massachusetts 165",
            "name=Commonwealth v. Pierce"])
        self.assertEqual(chance.split("\t")[1:], [
            "pin=252", "cite=174 Massachusetts 245",
            "name=Commonwealth v. Chance"])


# ---------------------------------------------------------------------------
# Telling Scholar's answer from a failure to ask it
# ---------------------------------------------------------------------------

class _FetcherFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.scholar = _FakeScholar()
        self.fetcher = GoogleScholarFetcher(
            cache_path=Path(self._tmp.name) / "cache.db", delay=0.0)
        self.fetcher._browser_dead = True   # never launch a real Firefox
        self.fetcher._get = self.scholar    # type: ignore[assignment]

    def tearDown(self):
        self.fetcher._db.close()  # so Windows lets the directory go
        self._tmp.cleanup()


class AbsentOrUnreachableTests(_FetcherFixture):

    def test_a_how_cited_link_is_never_fetched_as_an_opinion(self):
        self.assertIsNone(self.fetcher.fetch_by_url(PIERCE_URL))
        self.assertTrue(self.fetcher.last_fetch_absent())
        self.assertEqual(self.scholar.urls, [])

    def test_a_how_cited_page_is_an_answer(self):
        # A case= link that turns out to be one all the same.
        self.scholar.page = HOW_CITED
        url = "https://scholar.google.com/scholar_case?case=1"
        self.assertIsNone(self.fetcher.fetch_by_url(url))
        self.assertTrue(self.fetcher.last_fetch_absent())

    def test_a_page_not_found_is_an_answer(self):
        self.scholar.page = _raise(requests.HTTPError(
            "404", response=SimpleNamespace(status_code=404)))
        url = "https://scholar.google.com/scholar_case?case=2"
        self.assertIsNone(self.fetcher.fetch_by_url(url))
        self.assertTrue(self.fetcher.last_fetch_absent())

    def test_a_block_or_a_timeout_is_not(self):
        for exc in (ScholarError("HTTP 429 blocked"),
                    requests.Timeout("timed out"),
                    requests.HTTPError(
                        "503", response=SimpleNamespace(status_code=503))):
            with self.subTest(exc=exc):
                self.scholar.page = _raise(exc)
                url = "https://scholar.google.com/scholar_case?case=3"
                self.assertIsNone(self.fetcher.fetch_by_url(url))
                self.assertFalse(self.fetcher.last_fetch_absent())
                self.assertIsNone(
                    self.fetcher.fetch_by_citation("138 Mass. 165"))
                self.assertFalse(self.fetcher.last_fetch_absent())

    def test_a_results_page_with_nothing_bearing_the_cite_is_an_answer(self):
        self.assertIsNone(self.fetcher.fetch_by_citation(
            "138 Mass. 165", case_name="Commonwealth v. Pierce"))
        self.assertTrue(self.fetcher.last_fetch_absent())
        self.assertIsNone(self.fetcher.take_post_search_failure())

    def test_a_page_that_is_no_results_page_is_not(self):
        self.scholar.page = INTERSTITIAL
        self.assertIsNone(self.fetcher.fetch_by_citation("138 Mass. 165"))
        self.assertFalse(self.fetcher.last_fetch_absent())

    def test_a_case_found_whose_page_answers_nothing_is_not_retried(self):
        results = NO_MATCH.replace(
            "457 NE 2d 622, 390 Mass. 480", "138 Mass. 165")
        self.scholar.page = lambda url: (
            HOW_CITED if "scholar_case" in url else results)
        self.assertIsNone(self.fetcher.fetch_by_citation("138 Mass. 165"))
        self.assertTrue(self.fetcher.last_fetch_absent())
        self.assertIsNone(self.fetcher.take_post_search_failure())

    def test_each_thread_hears_its_own_answer(self):
        self.assertIsNone(self.fetcher.fetch_by_url(PIERCE_URL))
        seen = []
        self.scholar.page = _raise(ScholarError("blocked"))

        def other() -> None:
            self.fetcher.fetch_by_url(
                "https://scholar.google.com/scholar_case?case=4")
            seen.append(self.fetcher.last_fetch_absent())

        worker = threading.Thread(target=other)
        worker.start()
        worker.join()
        self.assertEqual(seen, [False])
        self.assertTrue(self.fetcher.last_fetch_absent())

    def test_the_how_cited_heading_is_read_once(self):
        self.scholar.page = HOW_CITED
        for _ in range(2):
            self.assertEqual(self.fetcher.how_cited_heading(PIERCE_URL),
                             "Commonwealth v. Pierce, 138 Mass. 165 - 1884")
        self.assertEqual(len(self.scholar.urls), 1)

    def test_a_heading_is_only_read_off_a_how_cited_page(self):
        self.scholar.page = INTERSTITIAL
        self.assertEqual(self.fetcher.how_cited_heading(PIERCE_URL), "")
        self.scholar.page = _raise(ScholarError("blocked"))
        self.assertEqual(self.fetcher.how_cited_heading(PIERCE_URL), "")


# ---------------------------------------------------------------------------
# Following the link
# ---------------------------------------------------------------------------

@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class FollowingHowCitedLinksTests(unittest.TestCase):

    def _window(self, action, fetcher):
        win = object.__new__(gui._ScholarTextWindow)
        win._link_actions = {"lnk1": action}
        win._app = mock.Mock()
        win._app._get_scholar.return_value = fetcher
        win._app.open_cited_case_pdf.return_value = False  # no scan: text
        win._following_as_text = False
        win._status_var = mock.Mock()
        win._post = mock.Mock()
        return win

    def _scholar(self, absent=True):
        fetcher = mock.Mock()
        fetcher.fetch_by_url.return_value = None
        fetcher.fetch_by_citation.return_value = None
        fetcher.last_fetch_absent.return_value = absent
        return fetcher

    def test_the_link_is_looked_up_by_its_citation(self):
        fetcher = self._scholar()
        win = self._window(("url", "\t".join([
            PIERCE_URL, "pin=178", "cite=138 Massachusetts 165",
            "name=Commonwealth v. Pierce"])), fetcher)
        with mock.patch.object(gui.threading, "Thread", _Inline):
            win._follow_link("lnk1")
        # The scan first, by the citation …
        self.assertEqual(win._app.open_cited_case_pdf.call_args.args[1],
                         ("cite", "138 Massachusetts 165@178"))
        # … then Scholar, searched by it — never the "How cited" page.
        fetcher.fetch_by_url.assert_not_called()
        fetcher.fetch_by_citation.assert_called_once_with(
            "138 Massachusetts 165", case_name="Commonwealth v. Pierce")
        win._post.assert_called_once_with(
            win._on_link_ready, None, "138 Massachusetts 165", "178", "",
            "Commonwealth v. Pierce", True)

    def test_scholar_s_citation_is_read_when_the_link_gives_none(self):
        fetcher = self._scholar()
        fetcher.how_cited_heading.return_value = (
            "Commonwealth v. Pierce, 138 Mass. 165 - 1884")
        win = self._window(("url", PIERCE_URL), fetcher)
        win._post = lambda fn, *args: fn(*args)
        win._link_scholar_failed = mock.Mock()
        with mock.patch.object(gui.threading, "Thread", _Inline):
            win._follow_link("lnk1")
        fetcher.how_cited_heading.assert_called_once_with(PIERCE_URL)
        self.assertEqual(win._link_actions["lnk1"], ("url", "\t".join([
            PIERCE_URL, "cite=138 Mass. 165",
            "name=Commonwealth v. Pierce"])))
        fetcher.fetch_by_url.assert_not_called()
        fetcher.fetch_by_citation.assert_called_once_with(
            "138 Mass. 165", case_name="Commonwealth v. Pierce")

    def test_an_answer_opens_the_next_best_copy_without_retrying(self):
        win = self._window(("cite", "138 Mass. 165"), self._scholar())
        win._app._get_client.return_value = object()
        win._follow_cite_via_cl = mock.Mock()
        win._retry_scholar_only = mock.Mock()
        win._link_scholar_failed(
            "138 Mass. 165", "178", "", "Commonwealth v. Pierce", absent=True)
        win._follow_cite_via_cl.assert_called_once_with(
            "138 Mass. 165", "178", name="Commonwealth v. Pierce",
            retry=("138 Mass. 165", "178", "", "Commonwealth v. Pierce", 0),
            scholar_absent=True)
        # With nothing to look the case up by, it is left at that.
        win._app._get_client.return_value = None
        win._link_scholar_failed("", "", PIERCE_URL, "", absent=True)
        win._retry_scholar_only.assert_not_called()

    def test_a_failure_to_ask_still_retries(self):
        win = self._window(("cite", "138 Mass. 165"), self._scholar(False))
        win._app._get_client.return_value = object()
        win._follow_cite_via_cl = mock.Mock()
        win._link_scholar_failed("138 Mass. 165", "", "", "Pierce")
        self.assertEqual(
            win._follow_cite_via_cl.call_args.kwargs["retry"][-1], 3)

    def test_nothing_found_elsewhere_does_not_send_it_back_to_scholar(self):
        win = self._window(("cite", "138 Mass. 165"), self._scholar())
        win._app._get_client.return_value = None
        win._try_case_law_link_pdf = mock.Mock(return_value=False)
        win._retry_scholar_only = mock.Mock()
        win._on_cl_link_error = mock.Mock()
        win._post = lambda fn, *args: fn(*args)
        with mock.patch.object(gui, "_case_law_text_source",
                               return_value=None), \
                mock.patch.object(gui.threading, "Thread", _Inline):
            win._follow_cite_via_cl(
                "138 Mass. 165", "", name="Commonwealth v. Pierce",
                retry=("138 Mass. 165", "", "", "Commonwealth v. Pierce", 0),
                scholar_absent=True)
        win._retry_scholar_only.assert_not_called()
        self.assertIn("nor has Google Scholar a copy",
                      win._on_cl_link_error.call_args.args[0])

    def test_retrying_stops_at_scholar_s_answer(self):
        fetcher = self._scholar()
        win = self._window(("cite", "138 Mass. 165"), fetcher)
        win._post = lambda fn, *args: fn(*args)
        with mock.patch.object(gui.threading, "Thread", _Inline), \
                mock.patch.object(gui.time, "sleep"):
            win._retry_scholar_only("138 Mass. 165", "", "", "Pierce")
        self.assertEqual(fetcher.fetch_by_citation.call_count, 1)
        win._status_var.set.assert_called_with(
            "Google Scholar has no copy of the cited case.")


if __name__ == "__main__":
    unittest.main()
