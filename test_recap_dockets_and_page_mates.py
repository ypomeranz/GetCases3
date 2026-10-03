"""Two citations clicked to nothing, or to the wrong case.

"United States v. Braxton, No. CR-90-135-JFM (D. Md. June 25, 2001)", in
United States v. Braxton, 18 F. App'x 84 (4th Cir. 2001), found nothing in
RECAP — an order from 2001 in a 1990 case — and said so only in the status
line.  The docket itself is on CourtListener, filed in PACER's form
("1:90-cr-00135") under the lead defendant (United States v. Williams); it is
now looked up and opened, and a click that finds nothing at all says so.

And a citation several cases begin at — "18 F. App'x 81", McCarthy and
Johnson both — opened the first of them when nothing came with it to say
which, clicked in Chrome or in a case on screen.  The reader is now asked.
"""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import citations
import courtlistener as cl

try:
    import courtlistener_gui as gui
    from google_scholar import ScholarResult
except ImportError:  # pragma: no cover - exercised on a bare checkout
    gui = None


def _session(answers):
    """A requests session answering each search by its docket number (or
    case name): ``answers[value] -> (count, results)``."""
    seen = []

    def get(url, params=None, timeout=None):
        seen.append(dict(params or {}))
        key = params.get("docket_number") or params.get("case_name") or ""
        count, results = answers.get(key, (0, []))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"count": count, "results": results})

    return SimpleNamespace(get=get, seen=seen)


def _docket(docket_id, name, number="1:90-cr-00135", party=()):
    return {"docket_id": docket_id, "caseName": name, "docketNumber": number,
            "party": list(party),
            "docket_absolute_url": f"/docket/{docket_id}/x/"}


class DocketNumberTests(unittest.TestCase):

    def test_the_forms_a_docket_number_is_searched_by(self):
        self.assertEqual(cl._docket_variants("CR-90-135-JFM"),
                         ["CR-90-135-JFM", "CR-90-135", "90-cr-135"])
        self.assertEqual(cl._docket_variants("Civ. 98-1234"),
                         ["Civ. 98-1234", "98-cv-1234"])
        self.assertEqual(cl._docket_variants("12-6371-JFM"),
                         ["12-6371-JFM", "12-6371"])
        self.assertEqual(cl._docket_variants("2:13-cv-7779"),
                         ["2:13-cv-7779"])
        self.assertEqual(cl._docket_variants(""), [])

    def test_the_document_search_tries_pacer_s_form(self):
        session = _session({})
        cl.find_recap_document("CR-90-135-JFM", "mdd", "2001-06-25",
                               session=session)
        self.assertIn("90-cr-135",
                      [p.get("docket_number") for p in session.seen])


class DocketLookupTests(unittest.TestCase):

    def test_one_docket_opens_itself(self):
        session = _session({"90-cr-135": (1, [_docket(7, "United States v. "
                                                         "Braxton")])})
        found = cl.find_recap_docket("CR-90-135-JFM", "mdd",
                                     "United States v. Braxton",
                                     session=session)
        self.assertEqual(found["web_url"],
                         "https://www.courtlistener.com/docket/7/x/")
        self.assertEqual(found["count"], 1)

    def test_of_several_the_one_naming_the_party(self):
        session = _session({"90-cr-135": (3, [
            _docket(1, "United States v. Williams", party=["Williams"]),
            _docket(2, "United States v. Williams",
                    party=["Kevin Braxton", "USA"]),
            _docket(3, "United States v. Williams")])})
        found = cl.find_recap_docket("CR-90-135-JFM", "mdd",
                                     "United States v. Braxton",
                                     session=session)
        self.assertEqual(found["web_url"],
                         "https://www.courtlistener.com/docket/2/x/")

    def test_several_none_naming_it_are_listed(self):
        # A multi-defendant case keeps a docket for each defendant, all
        # captioned for the first.
        session = _session({"90-cr-135": (15, [
            _docket(i, "United States v. Williams") for i in range(15)])})
        found = cl.find_recap_docket("CR-90-135-JFM", "mdd",
                                     "United States v. Braxton",
                                     session=session)
        self.assertEqual(found["count"], 15)
        self.assertEqual(found["case_name"], "United States v. Williams")
        self.assertEqual(found["docket_number"], "1:90-cr-00135")
        self.assertIn("docket_number=90-cr-135", found["web_url"])
        self.assertIn("court=mdd", found["web_url"])

    def test_none_is_none(self):
        self.assertIsNone(cl.find_recap_docket(
            "CR-90-135-JFM", "mdd", "United States v. Braxton",
            session=_session({})))


class ScannedNameTests(unittest.TestCase):

    def test_a_name_read_off_a_scan_is_cleaned(self):
        text = ("we affirm on the reasoning of the district court. "
                "United,States v. Brax￾ton, No. CR-90-135-JFM (D. Md. "
                "June 25, 2001).")
        specs = [json.loads(a[1]) for _s, _e, a in citations.detect_links(text)
                 if a[0] == "recap"]
        self.assertEqual(specs[0]["name"], "United States v. Braxton")
        self.assertEqual(specs[0]["docket"], "CR-90-135-JFM")
        self.assertEqual(specs[0]["court"], "mdd")


def _scholar_page(court_line: str, date_line: str) -> str:
    return ('<div id="gs_opinion"><center><b>2001 WL 1</b></center>'
            '<center><h3 id="gsl_case_name">UNITED STATES v. Kevin BRAXTON'
            f'</h3></center><center><p><b>{court_line}</b></p></center>'
            f'<center>{date_line}</center><p>Order.</p></div>')


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class ScholarByNameTests(unittest.TestCase):
    """A docket-only citation's opinion, found on Scholar by the case name,
    is taken only when its court and day are the citation's: "United States
    v. Braxton, 2001" brought up another case altogether."""

    def test_the_court_and_day_cited(self):
        page = _scholar_page("United States District Court, D. Maryland.",
                             "June 25, 2001.")
        self.assertTrue(gui._recap_opinion_is_cited(page, "mdd",
                                                    "2001-06-25"))

    def test_another_court(self):
        page = _scholar_page("United States Court of Appeals, Fourth "
                             "Circuit.", "June 25, 2001.")
        self.assertFalse(gui._recap_opinion_is_cited(page, "mdd",
                                                     "2001-06-25"))

    def test_another_day(self):
        page = _scholar_page("United States District Court, D. Maryland.",
                             "March 2, 2001.")
        self.assertFalse(gui._recap_opinion_is_cited(page, "mdd",
                                                     "2001-06-25"))


class _NowThread:
    def __init__(self, target=None, daemon=None, **_kw):
        self._target = target

    def start(self):
        self._target()


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class RecapClickTests(unittest.TestCase):
    SPEC = json.dumps({"docket": "CR-90-135-JFM", "date": "2001-06-25",
                       "court": "mdd",
                       "name": "United,States v. Brax\u0002ton"})

    def _app(self):
        app = mock.Mock()
        app._token_var.get.return_value = "token"
        app._post_root.side_effect = lambda fn, *args: fn(*args)
        app._get_scholar.return_value.fetch_by_name.return_value = None
        return app

    def _click(self, app, docket=None):
        with mock.patch.object(gui.cl_api, "find_recap_document",
                               return_value=None), \
                mock.patch.object(gui.cl_api, "find_recap_docket",
                                  return_value=docket) as lookup, \
                mock.patch.object(gui.threading, "Thread", _NowThread), \
                mock.patch.object(gui.webbrowser, "open") as browser:
            gui._open_recap_citation(app, "parent", self.SPEC)
        return lookup, browser

    def test_the_docket_opens_when_the_opinion_is_not_in_recap(self):
        app = self._app()
        lookup, browser = self._click(app, {
            "web_url": "https://www.courtlistener.com/?type=r",
            "case_name": "United States v. Williams",
            "docket_number": "1:90-cr-00135", "count": 15})
        lookup.assert_called_once()
        self.assertEqual(lookup.call_args.args[:3],
                         ("CR-90-135-JFM", "mdd", "United States v. Braxton"))
        browser.assert_called_once_with("https://www.courtlistener.com/?type=r")
        app._notify_lookup_miss.assert_not_called()
        # The name searched on Scholar was cleaned too.
        app._get_scholar.return_value.fetch_by_name.assert_called_once_with(
            "United States v. Braxton", "2001")

    def test_nothing_anywhere_is_said(self):
        app = self._app()
        _lookup, browser = self._click(app, None)
        browser.assert_not_called()
        app._notify_lookup_miss.assert_called_once()
        message, parent = app._notify_lookup_miss.call_args.args
        self.assertIn("United States v. Braxton, No. CR-90-135-JFM", message)
        self.assertEqual(parent, "parent")


def _page_opinion(url, name):
    return SimpleNamespace(url=url, json_url="", name=name, court_id="",
                           pages=3)


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class PageChoiceTests(unittest.TestCase):
    MCCARTHY = "https://static.case.law/f-appx/18/case-pdfs/0081-01.pdf"
    JOHNSON = "https://static.case.law/f-appx/18/case-pdfs/0081-02.pdf"

    def _choices(self, cites, name=""):
        siblings = [_page_opinion(self.MCCARTHY, "United States v. McCarthy"),
                    _page_opinion(self.JOHNSON, "Johnson v. United States")]
        session = mock.Mock()
        session.head.return_value = SimpleNamespace(status_code=200)
        with mock.patch.object(gui, "_anon_session", session), \
                mock.patch.object(gui, "_case_law_page_opinions",
                                  side_effect=lambda url: (
                                      siblings if "f-appx" in url else [])), \
                mock.patch.object(gui, "_case_law_listed_url",
                                  side_effect=lambda cite, url: url):
            return gui._case_law_pdf_choices_for_cites(
                cites, expected_name=name)

    def test_a_bare_citation_offers_every_case_at_it(self):
        choices = self._choices(["18 F. App'x 81"])
        self.assertEqual([c.url for c in choices],
                         [self.MCCARTHY, self.JOHNSON])
        self.assertTrue(all(c.pick for c in choices))
        self.assertEqual(gui._choices_to_ask(choices, self.MCCARTHY),
                         choices)

    def test_a_name_settles_it(self):
        choices = self._choices(["18 F. App'x 81"],
                                name="Johnson v. United States")
        self.assertEqual([(c.url, c.pick) for c in choices],
                         [(self.JOHNSON, False)])
        self.assertEqual(gui._choices_to_ask(choices, self.JOHNSON), [])

    def test_a_parallel_cite_that_settles_it_wins(self):
        choices = self._choices(["18 F. App'x 81", "250 F.3d 1"])
        settled = gui._settled_choice(choices)
        self.assertFalse(settled.pick)
        self.assertEqual(gui._choices_to_ask(choices, settled.url), [])

    def test_a_us_reports_page_left_unsettled_is_asked_about(self):
        opinions = [SimpleNamespace(url="u1", name="Brobst v. Brobst"),
                    SimpleNamespace(url="u2", name="Ex parte Milligan")]
        with mock.patch.object(gui, "_us_reports_page_opinions",
                               return_value=opinions):
            url, choices = gui._us_reports_page_choice("71 U.S. 2", "u1", "")
        self.assertEqual(url, "u1")
        self.assertEqual(len(gui._choices_to_ask(choices, url)), 2)


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class ScholarPageMatesTests(unittest.TestCase):
    NIH = ScholarResult("NAT INST OF HEALTH v. AM PUBLIC HEALTH ASSN", "u1",
                        "145 S. Ct. 2658 - Supreme Court, 2025") if gui else None
    NETCHOICE = ScholarResult("NETCHOICE, LLC v. Fitch", "u2",
                              "145 S. Ct. 2658 - Supreme Court, 2025") if gui \
        else None

    def _app(self):
        app = object.__new__(gui.CourtListenerGUI)
        app.root = object()
        app._post_root = mock.Mock()
        return app

    def test_a_bare_citation_scholar_cannot_settle_is_asked_about(self):
        fetcher = mock.Mock()
        fetcher.fetch_by_citation.return_value = None
        fetcher.take_page_mates.return_value = [self.NIH, self.NETCHOICE]
        app = self._app()
        self.assertTrue(app._try_open_citation(
            "", "145 S. Ct. 2658", "2660", fetcher, None))
        app._post_root.assert_called_once_with(
            app._ask_which_scholar_case, app.root, "145 S. Ct. 2658", "2660",
            [self.NIH, self.NETCHOICE], fetcher, None, True)
        fetcher.fetch_by_url.assert_not_called()

    def test_the_case_picked_is_opened_by_its_name_and_year(self):
        app = self._app()
        app._bring_to_front = lambda win: None
        app._try_open_citation = mock.Mock(return_value=True)
        app._notify_lookup_miss = mock.Mock()
        parent = mock.Mock()
        with mock.patch.object(
                gui, "_choose_cited_case",
                side_effect=lambda host, cite, cases, **kw: cases[1]) \
                as choose, \
                mock.patch.object(gui.threading, "Thread", _NowThread):
            app._ask_which_scholar_case(parent, "145 S. Ct. 2658", "2660",
                                        [self.NIH, self.NETCHOICE], "fetcher",
                                        "client")
        cases = choose.call_args.args[2]
        self.assertEqual([c["year"] for c in cases], ["2025", "2025"])
        self.assertEqual(cases[0]["name"],
                         "Nat Inst of Health v. Am Public Health Assn")
        app._try_open_citation.assert_called_once_with(
            cases[1]["name"], "145 S. Ct. 2658", "2660", "fetcher", "client",
            prefetch_pdf=True, view_parent=parent, year="2025")
        app._notify_lookup_miss.assert_not_called()


if __name__ == "__main__":
    unittest.main()
