"""A case's window is titled with the citation Copy with citation gives.

Brown v. United States, 524 F.2d 693, opened on its scan, was titled
"Brown v. United States, 524 F.2d 693 (1976)" while Copy with citation gave
"(Ct. Cl. 1975)".  Three things kept them apart:

* the opinion's header names "United States Court of Claims", which the
  header reader did not know, so the scan's record had no court;
* the header dates the decision "October 22, 1975." and then "As Amended
  January 9, 1976.", and the last bare date was taken as the decision's;
* the opinion beside the scan looked the court up on CourtListener, and its
  citation took it — but the window named itself from the record, and was
  never told.

Now the header names the federal courts outside the circuits and districts,
the decision date passes over amendments, rehearings and arguments, and the
window's title follows the citation the opinion settles on, re-deriving the
court for the reporter the scan prints.  A scan whose record still names no
court asks CourtListener itself, before the text is ever opened.
"""

import unittest
from unittest.mock import Mock, patch

import courtlistener_gui as g
from google_scholar import Block, Span
from opinion_db import decision_date_from_blocks


def centers(*lines):
    return [Block("center", [Span(line)]) for line in lines]


BROWN_HEADER = centers(
    "524 F.2d 693 (1975)",
    "Horton J. BROWN v. The UNITED STATES.",
    "No. 251-72.",
    "United States Court of Claims.",
    "October 22, 1975.",
    "As Amended January 9, 1976.",
)


class DecisionDateTests(unittest.TestCase):
    def test_an_amendment_is_not_the_decision(self):
        self.assertEqual(decision_date_from_blocks(BROWN_HEADER), "1975-10-22")

    def test_nor_a_rehearing_or_an_argument(self):
        self.assertEqual(decision_date_from_blocks(centers(
            "Argued March 1, 1980.", "May 5, 1980.",
            "Rehearing Denied June 30, 1980.")), "1980-05-05")

    def test_a_labelled_decision_still_wins(self):
        self.assertEqual(decision_date_from_blocks(centers(
            "Argued January 4, 2021.", "Decided June 1, 2021.",
            "As Amended July 2, 2021.")), "2021-06-01")

    def test_nothing_but_other_events_dates_nothing(self):
        # The year is then read another way (the citation's own "(1975)").
        self.assertEqual(decision_date_from_blocks(centers(
            "As Amended January 9, 1976.")), "")


class HeaderCourtTests(unittest.TestCase):
    def test_the_federal_courts_outside_the_circuits(self):
        for line, court in (
                ("United States Court of Claims.", "cc"),
                ("United States Court of Federal Claims.", "uscfc"),
                ("United States Court of International Trade.", "cit"),
                ("United States Court of Customs and Patent Appeals.",
                 "ccpa"),
                ("United States Tax Court.", "tax"),
                ("United States Court of Appeals for Veterans Claims.",
                 "cavet"),
                ("United States Court of Appeals for the Armed Forces.",
                 "caaf")):
            self.assertEqual(g._scholar_court_id(centers(line)), court, line)
        self.assertEqual(g._scholar_court_id(BROWN_HEADER), "cc")

    def test_a_line_that_only_mentions_one_is_about_another(self):
        self.assertEqual(g._scholar_court_id(centers(
            "On Appeal from the United States Court of Claims.")), "")

    def test_the_circuits_and_the_supreme_court_as_before(self):
        self.assertEqual(g._scholar_court_id(centers(
            "United States Court of Appeals, Ninth Circuit.")), "ca9")
        self.assertEqual(g._scholar_court_id(centers(
            "Supreme Court of United States.")), "scotus")


def reader(bb, item=None):
    win = object.__new__(g._ScholarTextWindow)
    win._bb = dict(bb)
    win._item = dict(item or {})
    return win


class CitationFactsTests(unittest.TestCase):
    RECORD = {"caseName": "Brown v. United States",
              "citation": ["524 F.2d 693"], "dateFiled": "1976-01-09",
              "court_id": "", "court": ""}

    def test_the_court_and_year_the_citation_settled_on(self):
        win = reader({"name": "Brown v. United States",
                      "cite": "524 F.2d 693", "court": "Ct. Cl.",
                      "court_id": "cc", "year": "1975"})
        item = win._with_citation_facts(self.RECORD)
        self.assertEqual(g._bluebook_display_name(item),
                         "Brown v. United States, 524 F.2d 693 "
                         "(Ct. Cl. 1975)")
        self.assertEqual(self.RECORD["dateFiled"], "1976-01-09")  # a copy

    def test_a_court_the_item_names_is_left_alone(self):
        win = reader({"court": "Ct. Cl.", "court_id": "cc", "year": "1975"})
        item = win._with_citation_facts({"court_id": "ca2",
                                         "dateFiled": "1975-10-22"})
        self.assertEqual((item["court_id"], item["dateFiled"]),
                         ("ca2", "1975-10-22"))

    def test_the_court_is_rederived_for_the_reporter_on_screen(self):
        # Cited to the Atlantic Reporter, the court is named; to the
        # Maryland Reports, which name it themselves, it is not (rule 10.4).
        win = reader({"court": "Md.", "court_id": "md", "year": "1986"})
        regional = win._with_citation_facts(
            {"caseName": "Mercy Hosp. v. Jackson",
             "citation": ["510 A.2d 562"]})
        official = dict(regional, citation=["306 Md. 556"])
        self.assertEqual(g._bluebook_display_name(regional),
                         "Mercy Hosp. v. Jackson, 510 A.2d 562 (Md. 1986)")
        self.assertEqual(g._bluebook_display_name(official),
                         "Mercy Hosp. v. Jackson, 306 Md. 556 (1986)")

    def test_a_court_read_without_an_id(self):
        win = reader({"court": "S.D.N.Y.", "court_id": "", "year": "2001"})
        item = win._with_citation_facts({"caseName": "A v. B",
                                         "citation": ["150 F. Supp. 2d 1"]})
        self.assertEqual(g._bluebook_display_name(item),
                         "A v. B, 150 F. Supp. 2d 1 (S.D.N.Y. 2001)")

    def test_the_window_title_and_file_name_take_them(self):
        win = reader({"name": "Brown v. United States",
                      "cite": "524 F.2d 693", "court": "Ct. Cl.",
                      "court_id": "cc", "year": "1975"},
                     item={"caseName": "BROWN v. UNITED STATES",
                           "citation": ["524 F.2d 693"],
                           "dateFiled": "1976-01-09"})
        self.assertEqual(
            g._bluebook_display_name(win._filename_item()),
            "Brown v. United States, 524 F.2d 693 (Ct. Cl. 1975)")


class TellingTheWindowTests(unittest.TestCase):
    def enriched(self, host):
        win = reader({"name": "Brown v. United States",
                      "cite": "524 F.2d 693", "court": "", "year": "1975"})
        win._win = host
        win._app = None
        win._history_key = Mock(return_value="key")
        win._title_citation = Mock(return_value="title")
        win._retitle_pdf_float = Mock()
        win._apply_enriched_citation("Ct. Cl.", "1975", "", "cc")
        return win

    def test_the_viewer_beside_the_scan_is_told(self):
        host = Mock(spec=g._EmbeddedCaseHost)
        win = self.enriched(host)
        self.assertEqual((win._bb["court"], win._bb["court_id"]),
                         ("Ct. Cl.", "cc"))
        host.citation_changed.assert_called_once()
        win._retitle_pdf_float.assert_called_once()   # and its own scan

    def test_a_host_beside_a_scan_renames_its_window(self):
        host = object.__new__(g._EmbeddedCaseHost)
        host._window = Mock()
        host._retitles = False
        host.citation_changed()
        host._window.citation_edited.assert_called_once()

    def test_a_host_holding_only_the_text_takes_the_reader_s_title(self):
        host = object.__new__(g._EmbeddedCaseHost)
        host._window = Mock()
        host._retitles = True
        host.citation_changed()
        host._window.citation_edited.assert_not_called()

    def test_building_the_opinion_beside_the_scan_retitles_it(self):
        viewer = object.__new__(g._FloatingPdfWindow)
        viewer._reader = None
        viewer._body = Mock()
        built = Mock()
        viewer._on_build_text = Mock(return_value=built)
        viewer._on_citation_edited = Mock()
        with patch.object(g, "_EmbeddedCaseHost"):
            self.assertIs(viewer._build_reader(), built)
        viewer._on_citation_edited.assert_called_once()

    def test_the_cited_scan_names_itself_from_the_opinion_beside_it(self):
        app = object.__new__(g.CourtListenerGUI)
        named = {"cite": "524 F.2d 693", "name": "Brown v. United States",
                 "url": "https://static.case.law/f2d/524/case-pdfs/x.pdf",
                 "record": {"name": "Brown v. United States",
                            "cites": ["524 F.2d 693"], "year": "1976",
                            "date_filed": "1976-01-09", "court": ""}}
        plain = g._bluebook_display_name(app._cited_filename_item(named))
        self.assertEqual(plain, "Brown v. United States, 524 F.2d 693 (1976)")
        window = Mock()
        window._reader = reader({"court": "Ct. Cl.", "court_id": "cc",
                                 "year": "1975"})
        named["window"] = window
        self.assertEqual(
            g._bluebook_display_name(app._cited_filename_item(named)),
            "Brown v. United States, 524 F.2d 693 (Ct. Cl. 1975)")


class AskingForTheCourtTests(unittest.TestCase):
    def app(self, token="tok"):
        app = object.__new__(g.CourtListenerGUI)
        app._token_var = Mock(get=Mock(return_value=token))
        app._get_client = Mock(return_value=Mock())
        app._post_root = lambda fn, *args: fn(*args)
        app._retitle_cited_pdf = Mock()
        return app

    def ask(self, app, named, found=("cc", "1975")):
        immediate = lambda target, daemon=None: Mock(start=target)
        with patch.object(g._ScholarTextWindow, "_cl_court_and_year",
                          return_value=found) as lookup, \
                patch.object(g.threading, "Thread", side_effect=immediate):
            app._learn_cited_court(named)
        return lookup

    def named(self, **record):
        return {"cite": "524 F.2d 693", "name": "Brown v. United States",
                "cl_item": None,
                "record": {"name": "Brown v. United States", **record}}

    def test_a_record_with_no_court_asks_and_retitles(self):
        app, named = self.app(), self.named()
        lookup = self.ask(app, named)
        lookup.assert_called_once()
        self.assertEqual(named["record"]["court"], "cc")
        app._retitle_cited_pdf.assert_called_once_with(named)
        # Asked once only.
        self.ask(app, named).assert_not_called()

    def test_not_when_the_record_names_one(self):
        self.ask(self.app(), self.named(court="ca9")).assert_not_called()

    def test_not_for_the_supreme_court_s_reporters(self):
        named = self.named()
        named["cite"] = "410 U.S. 113"
        self.ask(self.app(), named).assert_not_called()

    def test_not_without_a_token(self):
        self.ask(self.app(token=""), self.named()).assert_not_called()

    def test_courtlistener_not_knowing_changes_nothing(self):
        app, named = self.app(), self.named()
        self.ask(app, named, found=("", ""))
        self.assertNotIn("court", named["record"])
        app._retitle_cited_pdf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
