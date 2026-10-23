"""A nominative reporter whose name two jurisdictions' reporters share.

Boyd v. United States, 116 U.S. 616, cites "Commonwealth v. Dana, 2 Met.
(Mass.) 329" — Metcalf's Massachusetts reports, 43 Mass. 329.  "2 Met." is
also Metcalfe's Kentucky reports (59 Ky.), and with the "(Mass.)" dropped
CourtListener answered with a Kentucky case running onto page 329, which
opened.  The parenthetical now picks the series; a CourtListener record
naming another case is refused; and with no parenthetical, the case's name
(or year) picks among the series that hold a case on the page.
"""

import unittest
from unittest import mock

import citations

try:
    import courtlistener_gui as gui
except ImportError:  # pragma: no cover - exercised on a bare checkout
    gui = None


def _links(text):
    return [action for _s, _e, action in citations.detect_links(text)]


class ParentheticalTests(unittest.TestCase):

    def test_the_parenthetical_s_state_picks_the_series(self):
        self.assertEqual(
            _links("Commonwealth v. Dana, 2 Met. (Mass.) 329, held"),
            [("cite", "43 Mass. 329")])
        self.assertEqual(_links("Smith v. Jones, 2 Met. (Ky.) 100."),
                         [("cite", "59 Ky. 100")])

    def test_and_its_short_forms_follow(self):
        self.assertEqual(
            _links("Commonwealth v. Dana, 2 Met. (Mass.) 329.  See Dana, "
                   "2 Met., at 331."),
            [("cite", "43 Mass. 329"), ("cite", "43 Mass. 329@331")])

    def test_a_state_s_reporter_sharing_a_supreme_court_name(self):
        # Howard's Mississippi reports, 1–7 How. (Miss.), are 2–8 Miss.
        self.assertEqual(_links("Doe v. Roe, 5 How. (Miss.) 100."),
                         [("cite", "6 Miss. 100")])
        # Without the parenthetical, the Supreme Court's Howard.
        self.assertEqual(_links("Doe v. Roe, 5 How. 100."),
                         [("cite", "5 How. 100")])

    def test_a_state_none_of_the_series_belong_to(self):
        # Louisiana's Robinson is no Virginia Reports volume.
        self.assertEqual(_links("Smith v. Jones, 1 Rob. (La.) 50."),
                         [("cite", "1 Rob. (La.) 50")])
        self.assertEqual(citations.state_nominative_cites("1 Rob. (La.) 50"),
                         [])

    def test_nothing_to_choose_is_left_as_written(self):
        for text, cite in (
                ("Smith v. Jones, 19 Pick. (Mass.) 234.", "19 Pick. 234"),
                ("Johnson v. X, 5 Johns. (N.Y.) 37.", "5 Johns. 37"),
                ("Doe v. Roe, 2 Met. 329.", "2 Met. 329")):
            with self.subTest(text=text):
                self.assertEqual(_links(text), [("cite", cite)])

    def test_a_link_s_own_text_is_read_alike(self):
        self.assertEqual(citations.cite_target_from_text(
            "Commonwealth v. Dana, 2 Met. (Mass.) 329", {}),
            ("43 Mass. 329", ""))


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class NameAndYearTests(unittest.TestCase):
    META = {
        "43 Mass. 100": {"name_abbreviation": "Commonwealth v. Dana",
                         "decision_date": "1841-03-15"},
        "59 Ky. 100": {"name_abbreviation": "Sanders v. Bank of Kentucky",
                       "decision_date": "1859-10-13"},
    }

    def _official(self, name="", year=""):
        with mock.patch.object(gui, "_case_law_metadata",
                               side_effect=lambda c: self.META.get(c)):
            return gui._official_series_for("2 Met. 100", name, year)

    def test_the_name_picks_the_state(self):
        self.assertEqual(self._official("Sanders v. Bank of Ky."),
                         ["59 Ky. 100"])
        self.assertEqual(self._official("Commonwealth v. Dana"),
                         ["43 Mass. 100"])

    def test_failing_a_name_the_year(self):
        self.assertEqual(self._official(year="1859"),
                         ["59 Ky. 100", "43 Mass. 100"])

    def test_with_neither_the_table_s_order(self):
        self.assertEqual(self._official(), ["43 Mass. 100", "59 Ky. 100"])

    def test_an_unambiguous_reporter_is_never_looked_up(self):
        with mock.patch.object(gui, "_case_law_metadata") as meta:
            self.assertEqual(
                gui._official_series_for("19 Pick. 234", "Doe v. Roe"),
                ["36 Mass. 234"])
        meta.assert_not_called()


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class ReadingsToAskTests(unittest.TestCase):
    """With nothing to say which series a shared name means, the reader is
    asked — the first series' case opened, whichever was meant.  "1 Dud. 7"
    is Cole v. Broom in South Carolina's Dudley (23 S.C.L. 7) and Heirs of
    Early v. Sherwood in Georgia's (1 Dudley Rep. 7)."""

    META = {
        "23 S.C.L. 7": {"name_abbreviation": "Cole v. Broom",
                        "decision_date": "1837-12",
                        "jurisdiction": {"name": "S.C."}},
        "1 Dudley Rep. 7": {"name_abbreviation": "Heirs of Early v. Sherwood",
                            "decision_date": "1831-07",
                            "jurisdiction": {"name": "Ga."}},
    }

    def _readings(self, cite="1 Dud. 7", name="", year="", cited_in="",
                  meta=None):
        meta = self.META if meta is None else meta

        def page_cases(reading):
            m = meta.get(reading)
            return [gui._CaseLawPageOpinion(
                f"https://static.case.law/x/{reading}.pdf", "",
                m["name_abbreviation"])] if m else []

        with mock.patch.object(gui, "_case_law_metadata",
                               side_effect=lambda c: meta.get(c)), \
                mock.patch.object(gui, "_case_law_page_cases",
                                  side_effect=page_cases):
            return [(o.cite, o.name, o.paren) for o in gui._readings_to_ask(
                cite, name, year, cited_in)]

    def test_bare_the_reader_is_asked(self):
        self.assertEqual(self._readings(), [
            ("23 S.C.L. 7", "Cole v. Broom", "S.C. 1837"),
            ("1 Dudley Rep. 7", "Heirs of Early v. Sherwood", "Ga. 1831")])

    def test_the_case_s_name_says_which(self):
        self.assertEqual(self._readings(name="Cole v. Broom"), [])

    def test_or_the_year_it_was_decided(self):
        self.assertEqual(self._readings(year="1831"), [])

    def test_or_the_year_of_the_opinion_citing_it(self):
        # An opinion of 1835 cannot cite a case of 1837.
        self.assertEqual(self._readings(cited_in="1835"), [])

    def test_or_a_case_at_the_page_in_one_book_only(self):
        self.assertEqual(self._readings(
            meta={"23 S.C.L. 7": self.META["23 S.C.L. 7"]}), [])

    def test_a_citation_of_one_book_is_never_looked_up(self):
        with mock.patch.object(gui, "_case_law_metadata") as meta:
            self.assertEqual(gui._readings_to_ask("1 Dudley Rep. 7"), [])
            self.assertEqual(gui._readings_to_ask("19 Pick. 234"), [])
        meta.assert_not_called()

    def test_a_state_parenthetical_settles_it_before_anything_is_asked(self):
        self.assertEqual(_links("Doe v. Roe, 1 Dud. (Ga.) 7."),
                         [("cite", "1 Dudley Rep. 7")])
        self.assertEqual(_links("Doe v. Roe, 1 Dud. (S.C.) 7."),
                         [("cite", "23 S.C.L. 7")])


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class CourtListenerRecordTests(unittest.TestCase):

    def test_a_record_naming_another_case_is_refused(self):
        client = mock.Mock()
        client.lookup_citation.return_value = [{"status": 200, "clusters": [{
            "id": 7217775, "case_name": "Sanders v. Bank of Kentucky",
            "citations": [{"volume": 2, "reporter": "Met.", "page": 327}],
        }]}]
        self.assertIsNone(gui._cl_item_for_citation(
            client, "2 Met. 329", name="Commonwealth v. Dana"))
        # One of the same name is taken, however written.
        client.lookup_citation.return_value = [{"status": 200, "clusters": [{
            "id": 6534076, "case_name": "Com. v. Dana",
            "citations": [{"volume": 43, "reporter": "Mass.", "page": 329}],
        }]}]
        item = gui._cl_item_for_citation(client, "43 Mass. 329",
                                         name="Commonwealth v. Dana")
        self.assertEqual(item["cluster_id"], 6534076)


if __name__ == "__main__":
    unittest.main()
