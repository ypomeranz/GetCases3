"""A recent decision CourtListener has filed from the court's release, with
no reporter citation yet, is found by its parties' names (to-do item 11 of
the cert-petition review): "Randolph v. Macauley, 155 F.4th 859 (6th Cir.
2025)", "D.J. ex rel. R.J. v. First Student, Inc., 707 S.W.3d 581 (Mo.
2025)", "Werner Enters., Inc. v. Blake, 719 S.W.3d 525 (Tex. 2025)"."""

import unittest
from unittest import mock

try:
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - tkinter missing
    gui = None


def _hit(name, court, date, cluster, citation=()):
    return {"caseName": name, "court_id": court, "dateFiled": date,
            "cluster_id": cluster, "citation": list(citation)}


RANDOLPH = _hit("Andrew Maurice Randolph v. Matt Macauley", "ca6",
                "2025-10-21", 10708268)
DJ_MO = _hit("D.J., by and through his Next Friend, R.J. v. First Student, "
             "Inc.", "mo", "2025-02-28", 10627557)
DJ_MOCTAPP = _hit("D.J., by and through his next friend, R.J. v. First "
                  "Student, Inc., and Tomika L. Richardson", "moctapp",
                  "2024-06-25", 9500000)
GRUBBS = _hit("CHRIMISHA GRUBBS v. FIRST STUDENT, INC.", "gactapp",
              "2025-01-27", 9600000)
ALMONTE = _hit("Almonte v. First Student, Inc.", "nyappdiv", "2025-07-30",
               9700000, ["2025 NY Slip Op 04450"])
WERNER = _hit("Werner Enterprises, Inc. and Shiraz A. Ali v. Jennifer Blake, "
              "Individually and as Next Friend for Nathan Blake", "tex",
              "2025-06-27", 10618517)
ACADEMY = _hit("Academy of Allergy & Asthma in Primary Care v. Amerigroup "
               "Tennessee, Inc.", "ca6", "2025-10-10", 10700492)
ACADEMY_EN_BANC = _hit("Academy of Allergy & Asthma v. Amerigroup Tennessee, "
                       "Inc.", "ca6", "2026-01-13", 10773979)
ACADEMY_NAME = ("Acad. of Allergy & Asthma in Primary Care v. Amerigroup "
                "Tenn., Inc.")


def _client(results):
    client = mock.Mock()
    client.search.return_value = {"results": list(results)}
    client.lookup_citation.return_value = []
    return client


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class UnreportedCaseTests(unittest.TestCase):

    def test_found_by_its_parties(self):
        client = _client([RANDOLPH])
        self.assertIs(gui._cl_unreported_case(
            client, "155 F.4th 859", "Randolph v. Macauley", "2025"), RANDOLPH)
        kwargs = client.search.call_args.kwargs
        self.assertEqual(kwargs["date_filed_min"], "2025-01-01")
        self.assertEqual(kwargs["date_filed_max"], "2025-12-31")

    def test_the_parties_in_their_places(self):
        self.assertIsNone(gui._cl_unreported_case(
            _client([RANDOLPH]), "155 F.4th 859", "Macauley v. Randolph",
            "2025"))

    def test_a_party_of_initials_has_the_record_s_initials(self):
        # Georgia's Grubbs v. First Student answers to "v. First Student" —
        # but not to D.J., and the South Western Reporter prints no Georgia
        # case.  New York's is reported, under its own citation.
        client = _client([GRUBBS, ALMONTE, DJ_MO])
        self.assertIs(gui._cl_unreported_case(
            client, "707 S.W.3d 581", "D.J. ex rel. R.J. v. First Student, "
            "Inc.", "2025"), DJ_MO)

    def test_the_year_tells_the_decision_from_the_one_below(self):
        # The Court of Appeals' 2024 opinion in the same case, by the same
        # name.
        name = "D.J. ex rel. R.J. v. First Student, Inc."
        self.assertIs(gui._cl_unreported_case(
            _client([DJ_MOCTAPP, DJ_MO]), "707 S.W.3d 581", name, "2025"),
            DJ_MO)
        # Without a year, two decisions answer: neither is guessed at.
        self.assertIsNone(gui._cl_unreported_case(
            _client([DJ_MOCTAPP, DJ_MO]), "707 S.W.3d 581", name))

    def test_an_abbreviation_is_searched_in_every_spelling(self):
        client = _client([WERNER])
        self.assertIs(gui._cl_unreported_case(
            client, "719 S.W.3d 525", "Werner Enters., Inc. v. Blake",
            "2025"), WERNER)
        query = client.search.call_args.args[0]
        self.assertIn("caseName:(enterprises OR enters)", query)
        # "Acad." stands for "Academy" or "Academic".
        client = _client([ACADEMY])
        self.assertIs(gui._cl_unreported_case(
            client, "155 F.4th 795", ACADEMY_NAME, "2025"), ACADEMY)
        self.assertIn("caseName:(acad OR academic OR academy)",
                      client.search.call_args.args[0])

    def test_a_record_s_shorter_name(self):
        # The order denying rehearing en banc, 164 F.4th 529, is filed as
        # "Academy of Allergy & Asthma v. Amerigroup Tennessee, Inc."
        self.assertIs(gui._cl_unreported_case(
            _client([ACADEMY_EN_BANC]), "164 F.4th 529", ACADEMY_NAME,
            "2026"), ACADEMY_EN_BANC)

    def test_a_court_the_reporter_does_not_print(self):
        # A Federal Reporter citation is no state court's decision.
        state = dict(RANDOLPH, court_id="mich")
        self.assertIsNone(gui._cl_unreported_case(
            _client([state]), "155 F.4th 859", "Randolph v. Macauley",
            "2025"))

    def test_a_case_reported_elsewhere_is_another_case(self):
        reported = dict(RANDOLPH, citation=["99 F.4th 100"])
        self.assertIsNone(gui._cl_unreported_case(
            _client([reported]), "155 F.4th 859", "Randolph v. Macauley",
            "2025"))

    def test_the_resolver_turns_to_it_last(self):
        client = _client([])
        with mock.patch.object(gui, "_cl_case_containing_page",
                               return_value=None), \
                mock.patch.object(gui, "_cl_unreported_case",
                                  return_value=RANDOLPH) as unreported:
            got = gui._cl_item_for_citation(
                client, "155 F.4th 859", name="Randolph v. Macauley",
                year="2025")
        self.assertIs(got, RANDOLPH)
        unreported.assert_called_once_with(
            client, "155 F.4th 859", "Randolph v. Macauley", "2025")


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class ReporterCourtTests(unittest.TestCase):

    def test_the_courts_each_reporter_prints(self):
        holds = gui._reporter_holds_court
        self.assertTrue(holds("155 F.4th 859", "ca6"))
        self.assertFalse(holds("155 F.4th 859", "mich"))
        self.assertTrue(holds("600 F. Supp. 3d 1", "nysd"))
        self.assertFalse(holds("600 F. Supp. 3d 1", "ca2"))
        self.assertTrue(holds("600 U.S. 1", "scotus"))
        self.assertTrue(holds("707 S.W.3d 581", "mo"))
        self.assertTrue(holds("707 S.W.3d 581", "moctapp"))
        self.assertFalse(holds("707 S.W.3d 581", "gactapp"))
        self.assertFalse(holds("707 S.W.3d 581", "ca8"))
        self.assertTrue(holds("207 N.C. 545", "nc"))
        self.assertFalse(holds("207 N.C. 545", "sc"))
        # A court it can't place: no reason to refuse it.
        self.assertTrue(holds("719 S.W.3d 525", "txctapp14"))


if __name__ == "__main__":
    unittest.main()
