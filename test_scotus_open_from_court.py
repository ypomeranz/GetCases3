"""Supreme Court opinions opened from the Court's own lists open in the viewer
every other Supreme Court opinion opens in.

The Recent SCOTUS panel lists the Court's latest writings straight from
supremecourt.gov — the homepage's Recent Decisions, the Term's "Opinions of
the Court", its "Opinions Relating to Orders" — and a decision cited by
docket number ("Trump v. California, No. 26A139, slip op. at 4 (U.S. Aug. 24,
2026)") is found in the Court's archive.  Either way the PDF opens in the
chromeless scan window, its text a press of T away: found, for a writing too
recent to have a page in the U.S. Reports, by its caption and the day it was
decided — a page is kept only if it prints that day — and, when nobody has
it yet, read off the Court's own PDF.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, patch

import scotus_recent
from courtlistener_gui import (
    CourtListenerGUI,
    _ScholarTextWindow,
    _open_scotus_citation,
    _page_decided_on,
    _slip_text_source,
    _supreme_court_writing_urls,
)
from google_scholar import ScholarResult


class Now:
    """A thread that runs its target on start()."""

    def __init__(self, target=None, daemon=None, **_kw):
        self.target = target

    def start(self):
        self.target()


class RecordingApp:
    def __init__(self):
        self.opened = []

    def open_supreme_court_pdf(self, parent, url, name, **kw):
        self.opened.append((parent, url, name, kw))


def panel(app=None):
    status = SimpleNamespace(set=lambda _text: None)
    return SimpleNamespace(_win="panel-window", _status_var=status, _app=app)


def click_all(lines):
    for line in lines:
        if len(line) == 3 and callable(line[2]):
            line[2]()


DECISION = scotus_recent.RecentDecision(
    name="West Virginia v. B. P. J.", docket="24-43", date="June 30, 2026",
    description="Title IX allows schools to provide separate teams.",
    opinion_url="https://www.supremecourt.gov/opinions/25pdf/24-43_2b35.pdf")
MERITS = scotus_recent.TermOpinion(
    term="25", date="2026-06-30", docket="24-43",
    name="West Virginia v. B. P. J.", author="BK",
    opinion_url="https://www.supremecourt.gov/opinions/25pdf/24-43_2b35.pdf",
    description="Title IX allows schools to provide separate teams.",
    citation="609/2", release="68")
ORDER = scotus_recent.OrderOpinion(
    name="NIH v. APHA", docket="25A103", date="2025-08-21",
    authors=["AB", "BK", "KJ"],
    opinion_url="https://www.supremecourt.gov/opinions/preliminaryprint/"
                "606US2PP_Ord.pdf#page=79",
    citation="606 U.S. 999")


class PanelLinkTests(unittest.TestCase):
    def test_a_homepage_decision_opens_by_its_docket_and_date(self):
        app = RecordingApp()
        click_all(_ScholarTextWindow._details_lines_recent(
            panel(app), [DECISION], [], []))

        self.assertEqual(app.opened, [(
            "panel-window", DECISION.opinion_url, "West Virginia v. B. P. J.",
            {"citation": "", "docket": "24-43", "decided": "2026-06-30",
             "writing": "merits", "status": ANY})])

    def test_an_opinion_of_the_court_carries_its_citation(self):
        app = RecordingApp()
        click_all(_ScholarTextWindow._details_lines_recent(
            panel(app), [], [MERITS], []))

        self.assertEqual(app.opened, [(
            "panel-window", MERITS.opinion_url, "West Virginia v. B. P. J.",
            {"citation": "609/2", "docket": "24-43", "decided": "2026-06-30",
             "writing": "merits", "status": ANY})])

    def test_an_orders_writings_open_as_an_order(self):
        app = RecordingApp()
        click_all(_ScholarTextWindow._details_lines_recent(
            panel(app), [], [], [ORDER]))

        self.assertEqual(app.opened, [(
            "panel-window", ORDER.opinion_url, "NIH v. APHA",
            {"citation": "606 U.S. 999", "docket": "25A103",
             "decided": "2025-08-21", "writing": "order", "status": ANY})])

    def test_without_the_app_the_slip_opinion_viewer_still_opens(self):
        with patch("courtlistener_gui._SlipOpinionWindow") as slip:
            click_all(_ScholarTextWindow._details_lines_recent(
                panel(), [DECISION], [], []))

        slip.assert_called_once_with(
            "panel-window", DECISION.opinion_url, "West Virginia v. B. P. J.",
            ANY, app=None, description=DECISION.description)

    def test_the_homepage_date_in_iso_form(self):
        self.assertEqual(scotus_recent.iso_date("June 30, 2026"),
                         "2026-06-30")
        self.assertEqual(scotus_recent.iso_date("Decided June 3, 2026."),
                         "2026-06-03")
        self.assertEqual(scotus_recent.iso_date("2026"), "")
        self.assertEqual(scotus_recent.iso_date(""), "")


RAW, CLEAN = b"%PDF-raw", b"%PDF-clean"
FINAL = "https://www.supremecourt.gov/opinions/final.pdf"


class OpenFromTheCourtTests(unittest.TestCase):
    def open(self, url, name="West Virginia v. B. P. J.", *, citation="",
             docket="", decided="", writing="merits", fetched=(RAW, FINAL),
             cover=False):
        self.shown, self.statuses, self.fetches = [], [], []
        gui = SimpleNamespace(
            _safe_root_status=self.statuses.append,
            _post_root=lambda fn: fn(),
            _show_cited_case_pdf=lambda *a, **kw: self.shown.append((a, kw)))

        def fetch(pdf_url, timeout=30, keep_cover=False, **_kw):
            self.fetches.append((pdf_url, keep_cover))
            return fetched

        # A preliminary print's cover is a page of its own, and comes out.
        pages = {RAW: 146 if cover else 9, CLEAN: 145 if cover else 9}
        with patch("courtlistener_gui.threading.Thread", Now), \
                patch("courtlistener_gui._fetch_pdf_bytes", side_effect=fetch), \
                patch("courtlistener_gui._strip_preliminary_print_cover",
                      side_effect=lambda data: CLEAN if cover else data), \
                patch("courtlistener_gui._pdf_page_total",
                      side_effect=lambda data: pages[data]):
            CourtListenerGUI.open_supreme_court_pdf(
                gui, "parent", url, name, citation=citation, docket=docket,
                decided=decided, writing=writing)

    def only_shown(self):
        self.assertEqual(len(self.shown), 1)
        return self.shown[0]

    def test_a_slip_opinion_is_cited_to_its_volume(self):
        url = "https://www.supremecourt.gov/opinions/25pdf/24-43_2b35.pdf"
        self.open(url, citation="609/2", docket="24-43",
                  decided="2026-06-30")

        self.assertEqual(self.fetches, [(url, False)])
        args, kw = self.only_shown()
        self.assertEqual(
            args, ("parent", RAW, FINAL, "609 U.S. ___", "",
                   "West Virginia v. B. P. J.", ("cite", "609 U.S. ___"),
                   "West Virginia v. B. P. J.", self.statuses.append))
        self.assertEqual(kw, {
            "cl_item": {"caseName": "West Virginia v. B. P. J.",
                        "court_id": "scotus", "dateFiled": "2026-06-30",
                        "docketNumber": "24-43", "citation": []},
            "decided": "2026-06-30", "writing": "merits",
            "start_page": None})

    def test_a_writing_in_the_orders_section_opens_at_its_page(self):
        # "#page=143" counts the cover; the viewer shows the pages without.
        self.open("https://www.supremecourt.gov/opinions/preliminaryprint/"
                  "606US2PP_Ord.pdf#page=143", "Noem v. National TPS Alliance",
                  citation="606 U.S. 1063", docket="25A326",
                  decided="2025-10-03", writing="order", cover=True)

        self.assertEqual(self.fetches, [(
            "https://www.supremecourt.gov/opinions/preliminaryprint/"
            "606US2PP_Ord.pdf", True)])
        args, kw = self.only_shown()
        self.assertEqual(args[1], CLEAN)
        self.assertEqual(args[3:5], ("606 U.S. 1063", ""))
        self.assertEqual(kw["start_page"], 141)
        self.assertEqual(kw["writing"], "order")
        self.assertEqual(kw["cl_item"]["citation"], ["606 U.S. 1063"])

    def test_a_later_writing_in_an_orders_file_opens_at_its_page(self):
        self.open("https://www.supremecourt.gov/opinions/25pdf/"
                  "26a305_4g15.pdf#page=2", "Postal Service v. California",
                  citation="609/2", docket="26A305", decided="2026-09-14",
                  writing="order")

        args, kw = self.only_shown()
        self.assertEqual(args[1], RAW)
        self.assertEqual(kw["start_page"], 1)

    def test_a_decision_without_a_citation_goes_by_its_docket(self):
        self.open("https://www.supremecourt.gov/opinions/25pdf/24-621.pdf",
                  "NRSC v. FEC", docket="24-621", decided="2026-06-30")

        args, _kw = self.only_shown()
        self.assertEqual(args[3], "No. 24-621")

    def test_a_failed_fetch_says_so(self):
        self.open("https://www.supremecourt.gov/opinions/25pdf/24-621.pdf",
                  "NRSC v. FEC", docket="24-621", fetched=None)

        self.assertEqual(self.shown, [])
        self.assertEqual(self.statuses[-1],
                         "Could not load NRSC v. FEC from supremecourt.gov.")


class StartPageTests(unittest.TestCase):
    """The page a link names in the file is where the viewer opens."""

    def jump(self, named, pdf_pages=None):
        gui = SimpleNamespace(
            _scan_cite_for=lambda _named: self.fail("no arithmetic needed"))
        CourtListenerGUI._jump_to_pin(gui, named, pdf_pages)

    def window(self):
        window = SimpleNamespace(scrolled=[], alive=lambda: True,
                                 viewport_page=lambda: 141)
        window.scroll_to_page = window.scrolled.append
        return window

    def test_opened_there_once(self):
        window = self.window()
        named = {"window": window, "pin": "", "start_page": 141,
                 "pin_page": None, "cite": "606 U.S. 1063"}
        self.jump(named)
        # Read the pages' numbers: nothing to correct.
        self.jump(named, pdf_pages=[object()] * 145)

        self.assertEqual(window.scrolled, [141])

    def test_neither_pin_nor_page_stays_at_the_top(self):
        window = self.window()
        self.jump({"window": window, "pin": "", "start_page": None,
                   "pin_page": None, "cite": "609 U.S. ___"})
        self.assertEqual(window.scrolled, [])


def row(title, source, snippet, cited_by=0, case="1"):
    return ScholarResult(
        title=title,
        url=f"https://scholar.google.com/scholar_case?case={case}",
        source=f"{source} - Google Scholar",
        snippet=snippet,
        cited_by=cited_by,
    )


CALLAIS = [
    row("Louisiana v. Callais", "Supreme Court, 2026",
        "LOUISIANA, v. PHILLIP CALLAIS, ET AL. Nos. 24-109, 24-110. Supreme "
        "Court of the United States. May 6, 2026. The motion …", 0,
        "motion"),
    row("Louisiana v. Callais", "608 US __ - Supreme Court, 2026",
        "1139 The parties originally briefed and argued this suit last Term, "
        "and their arguments at that time highlighted problems in the "
        "existing body of § 2 case law.", 40, "merits"),
    row("Louisiana v. Callais", "145 S. Ct. 434 - Supreme Court, 2024",
        "145 S.Ct. 434 (2024). LOUISIANA, Appellant, v. Phillip CALLAIS, et "
        "al. No. 24-109. Supreme Court of United States. November 4, 2024. "
        "Appeals from the United States District Court for …", 12, "stay"),
    row("Callais v. Landry", "Dist. Court, WD Louisiana, 2024",
        "Plaintiffs challenge the congressional map enacted by Louisiana",
        30, "district"),
]


def case_id(url):
    return url.rsplit("=", 1)[1] if url else url


class WritingUrlTests(unittest.TestCase):
    """Which of a caption's writings may be the one decided that day,
    likeliest first."""

    def order_of(self, decided, writing="merits"):
        return [case_id(url) for url in _supreme_court_writing_urls(
            CALLAIS, "Louisiana v. Callais", decided, writing)]

    def test_the_merits_opinion_of_its_year_leads(self):
        self.assertEqual(self.order_of("2026-06-26"), ["merits", "motion"])

    def test_an_order_printing_the_very_date_leads(self):
        self.assertEqual(self.order_of("2026-05-06", "order"),
                         ["motion", "merits"])

    def test_a_merits_opinion_is_not_the_order_of_that_day(self):
        self.assertEqual(self.order_of("2026-05-06"), ["merits", "motion"])

    def test_an_order_whose_date_no_snippet_prints(self):
        self.assertEqual(self.order_of("2026-01-09", "order"),
                         ["motion", "merits"])
        self.assertEqual(self.order_of("2024-11-04", "order"), ["stay"])

    def test_nothing_of_that_year_under_the_caption(self):
        self.assertEqual(self.order_of("2025-06-27"), [])
        self.assertEqual(self.order_of(""), [])


NOEM_PAGE = """<div id="gs_opinion_wrapper"><div id="gs_opinion">
<center><b>146 S.Ct. 23 (2025)</b></center>
<center><h3 id="gsl_case_name">Kristi NOEM, Secretary, Department of Homeland
Security, et al.<br>v.<br>NATIONAL TPS ALLIANCE, et al.</h3></center>
<center>No. 25A326.</center>
<center><b>Supreme Court of United States.</b></center>
<center>October 3, 2025.</center>
<p>The application for stay presented to Justice Kagan and by her referred
to the Court is granted.</p>
</div></div>"""
NOEM_URL = "https://scholar.google.com/scholar_case?case=17057854072759264299"


class PageDateTests(unittest.TestCase):
    """Only the page can say which day a writing was decided."""

    def test_an_orders_bare_date(self):
        self.assertTrue(_page_decided_on((NOEM_URL, NOEM_PAGE), "2025-10-03"))
        self.assertFalse(_page_decided_on((NOEM_URL, NOEM_PAGE), "2025-10-04"))
        self.assertFalse(_page_decided_on((NOEM_URL, NOEM_PAGE), ""))

    def test_decided_not_argued(self):
        page = NOEM_PAGE.replace(
            "<center>October 3, 2025.</center>",
            "<center>Argued January 12, 2026.</center>"
            "<center>Decided June 26, 2026.</center>")
        self.assertTrue(_page_decided_on((NOEM_URL, page), "2026-06-26"))
        self.assertFalse(_page_decided_on((NOEM_URL, page), "2026-01-12"))

    def test_a_page_that_is_not_scholars(self):
        self.assertFalse(_page_decided_on(
            ("https://example.com/opinion", NOEM_PAGE), "2025-10-03"))


class Scholar:
    """Google Scholar with no copy at any citation, and *found* for a
    search."""

    def __init__(self, found=(), by_citation=None):
        self.calls = []
        self.courts = None
        self.found = list(found)
        self.by_citation = by_citation

    def fetch_by_citation(self, cite, case_name=None, year=None):
        self.calls.append(("cite", cite))
        return self.by_citation

    def search_cases(self, name, limit=10, courts=None):
        self.calls.append(("search", name))
        self.courts = courts
        return self.found

    def fetch_by_url(self, url):
        self.calls.append(("url", case_id(url)))
        return (url, "<html>opinion</html>")


# The day each fixture's page prints as the day it was decided.
DECIDED = {"motion": "2026-05-06", "merits": "2026-06-26",
           "stay": "2024-11-04", "cited": "2025-10-03"}


def decided_on(fetched, decided):
    return DECIDED.get(case_id(fetched[0])) == decided


class TextByDateTests(unittest.TestCase):
    """The text beside a writing opened from the Court: found by caption and
    date when there is no page to look it up by."""

    def warm(self, scholar, cite, *, decided, writing="merits", client=None,
             on_text_source=None, own_text=None):
        pages = []
        gui = SimpleNamespace(
            _get_scholar=lambda: scholar,
            _token_var=SimpleNamespace(get=lambda: "token" if client else ""),
            _get_client=lambda: client,
            _describe_warmed_case=lambda *_a: None)
        with patch("courtlistener_gui.threading.Thread", Now), \
                patch("courtlistener_gui._page_decided_on",
                      side_effect=decided_on):
            CourtListenerGUI._warm_case_text(
                gui, cite, "Louisiana v. Callais",
                on_page=lambda url, html: pages.append(case_id(url)),
                on_text_source=on_text_source, decided=decided,
                writing=writing, own_text=own_text)
        return pages

    def test_a_volume_without_a_page_is_searched_by_caption(self):
        scholar = Scholar(CALLAIS)
        pages = self.warm(scholar, "608 U.S. ___", decided="2026-06-26")

        self.assertEqual(scholar.calls, [("search", "Louisiana v. Callais"),
                                         ("url", "merits")])
        self.assertEqual(scholar.courts, ["scotus"])
        self.assertEqual(pages, ["merits"])

    def test_an_orders_writing_is_the_order_of_that_day(self):
        scholar = Scholar(CALLAIS)
        pages = self.warm(scholar, "No. 24-109", decided="2026-05-06",
                          writing="order")
        self.assertEqual(pages, ["motion"])

    def test_a_page_to_look_it_up_by_comes_first(self):
        scholar = Scholar(CALLAIS, by_citation=(
            "https://scholar.google.com/scholar_case?case=cited", "<html>"))
        pages = self.warm(scholar, "606 U.S. 1063", decided="2025-10-03",
                          writing="order")

        self.assertEqual(scholar.calls[0], ("cite", "606 U.S. 1063"))
        self.assertNotIn(("search", "Louisiana v. Callais"), scholar.calls)
        self.assertEqual(pages, ["cited"])

    def test_a_writing_scholar_lacks_is_not_stood_in_for(self):
        # Decided yesterday: Scholar has the case's other writings of the
        # year, and neither is it.
        scholar = Scholar(CALLAIS)
        pages = self.warm(scholar, "609 U.S. ___", decided="2026-09-25")

        self.assertEqual(pages, [])
        self.assertEqual(scholar.calls[1:], [("url", "merits"),
                                             ("url", "motion")])

    def test_three_are_read_at_most(self):
        rows = [row("Louisiana v. Callais", "Supreme Court, 2026",
                    f"The application for stay number {n} is denied.", 0,
                    f"order{n}") for n in range(5)]
        scholar = Scholar(rows)
        self.warm(scholar, "No. 24-109", decided="2026-09-25",
                  writing="order")

        self.assertEqual(len([c for c in scholar.calls if c[0] == "url"]), 3)

    def test_failing_scholar_courtlistener_has_it_by_the_same_day(self):
        def hit(cluster, filed, cites):
            return {"cluster_id": cluster, "dateFiled": filed,
                    "opinions": [{"cites": list(range(cites))}]}

        hits = [hit(1, "2026-05-06", 0), hit(2, "2026-06-26", 0),
                hit(3, "2026-06-26", 48), hit(4, "2024-11-04", 5)]
        asked, kept = [], []

        def text_source(client, cites, name, item=None):
            asked.append((list(cites), item["cluster_id"]))
            return "the text"

        with patch("courtlistener_gui._cl_name_search",
                   return_value=hits) as search, \
                patch("courtlistener_gui._case_law_text_for_scan",
                      return_value=None), \
                patch("courtlistener_gui._courtlistener_text_source",
                      side_effect=text_source):
            self.warm(Scholar(), "608 U.S. ___", decided="2026-06-26",
                      client=object(), on_text_source=kept.append,
                      own_text=lambda: self.fail("CourtListener had it"))
            # An order is whichever was filed that day; there is one.
            self.warm(Scholar(), "No. 24-109", decided="2026-05-06",
                      writing="order", client=object(),
                      on_text_source=kept.append)

        self.assertEqual(search.call_args.args[1:], ("Louisiana v. Callais",
                                                     "scotus"))
        # The merits opinion is the one citing most that day.
        self.assertEqual(asked, [([], 3), ([], 1)])
        self.assertEqual(kept, ["the text", "the text"])

    def test_the_pdfs_own_text_is_the_last_resort(self):
        kept = []
        with patch("courtlistener_gui._cl_name_search", return_value=[]), \
                patch("courtlistener_gui._case_law_text_for_scan",
                      return_value=None), \
                patch("courtlistener_gui._courtlistener_text_source",
                      return_value=None):
            pages = self.warm(Scholar(CALLAIS), "609 U.S. ___",
                              decided="2026-09-25", client=object(),
                              on_text_source=kept.append,
                              own_text=lambda: "the slip opinion's text")

        self.assertEqual(pages, [])
        self.assertEqual(kept, ["the slip opinion's text"])


SLIP = "https://www.supremecourt.gov/opinions/25pdf/26a305_4g15.pdf"


class SlipTextTests(unittest.TestCase):
    """A slip opinion's own text, read off the PDF as the slip-opinion viewer
    always read it."""

    def source(self, url, text="PER CURIAM.\n\nThe application is denied."):
        with patch("courtlistener_gui._extract_pdf_text_and_style",
                   return_value=([["page"]], [])) as extract, \
                patch("courtlistener_gui.slip_opinion.to_clean_text",
                      return_value=text):
            source = _slip_text_source(
                b"%PDF", url, {"caseName": "Postal Service v. California"})
        return source, extract

    def test_a_slip_opinion(self):
        source, _extract = self.source(SLIP + "#page=2")

        self.assertEqual(source.kind, "slip")
        self.assertEqual(source.source_label, "supremecourt.gov")
        self.assertEqual(source.source_url, SLIP)
        self.assertEqual(source.text,
                         "PER CURIAM.\n\nThe application is denied.")
        self.assertEqual(source.item,
                         {"caseName": "Postal Service v. California"})
        self.assertEqual((source.parts, source.blocks), ([], []))

    def test_not_a_preliminary_print_nor_anyone_elses_pdf(self):
        for url in ("https://www.supremecourt.gov/opinions/preliminaryprint/"
                    "606US2PP_Ord.pdf#page=143",
                    "https://storage.courtlistener.com/pdf/2026/x.pdf"):
            with self.subTest(url=url):
                source, extract = self.source(url)
                self.assertIsNone(source)
                extract.assert_not_called()

    def test_no_text_layer_no_text(self):
        source, _extract = self.source(SLIP, text="  ")
        self.assertIsNone(source)


class DocketCitationTests(unittest.TestCase):
    def test_a_slip_opinion_found_in_the_archive_opens_in_the_viewer(self):
        app = RecordingApp()
        app._post_root = lambda fn: fn()
        match = scotus_recent.SlipOpinion(
            term="25", release="70", date="2026-08-24", docket="26A124",
            name="Trump v. California",
            opinion_url="https://www.supremecourt.gov/opinions/25pdf/"
                        "26a124_hgci.pdf",
            citation="609/2")
        spec = json.dumps({"docket": "26A124", "date": "2026-08-24",
                           "name": "Trump v. California"})
        with patch("courtlistener_gui.threading.Thread", Now), \
                patch("scotus_recent.find_slip_opinion", return_value=match):
            _open_scotus_citation(app, "brief", spec)

        self.assertEqual(app.opened, [(
            "brief", match.opinion_url, "Trump v. California",
            {"citation": "609/2", "docket": "26A124",
             "decided": "2026-08-24", "status": ANY})])


if __name__ == "__main__":
    unittest.main()
