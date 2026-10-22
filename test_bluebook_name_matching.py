"""A case cited by its Bluebook name answers to its name written out.

Following "Incredible Techs., Inc. v. Virtual Techs., Inc., 400 F.3d 1007",
the scan was turned away: static.case.law's volume names the case at the
page "Incredible Technologies, Inc. v. Virtual Technologies, Inc.", and the
name check compared "techs" with "technologies" letter by letter — neither
begins the other, nor are they spelled nearly alike — so no party answered,
and CourtListener's record was turned away the same way.  Any name a
citation abbreviates by tables T6 and T10 met the same end: "Int'l Shoe Co."
(split at the apostrophe into "int" and an "l" too short to count), "Bd. of
Educ.", "N.Y. Times Co." (whose "N.Y." was dropped as two stray letters),
"Erie R.R. Co.".  A token now answers to the words the tables abbreviate to
it, and a run of initials standing for a place of several words is read as
the place.
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None

INCREDIBLE = "Incredible Techs., Inc. v. Virtual Techs., Inc."
HELD = "Incredible Technologies, Inc. v. Virtual Technologies, Inc."

#: As cited, and as a court's record writes the name out.
ABBREVIATED = (
    (INCREDIBLE, HELD),
    ("Int'l Shoe Co. v. Washington", "International Shoe Co. v. Washington"),
    ("Bd. of Educ. v. Pico", "Board of Education v. Pico"),
    ("N.Y. Times Co. v. Sullivan", "New York Times Co. v. Sullivan"),
    ("Erie R.R. Co. v. Tompkins", "Erie Railroad Co. v. Tompkins"),
    ("U.S. Steel Corp. v. Fuller", "United States Steel Corp. v. Fuller"),
    ("W. Va. State Bd. of Educ. v. Barnette",
     "West Virginia State Board of Education v. Barnette"),
    ("Nat'l Ass'n of Mfrs. v. Dep't of Def.",
     "National Association of Manufacturers v. Department of Defense"),
)


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class AbbreviationTests(unittest.TestCase):
    def test_the_abbreviated_name_is_the_case_either_way(self):
        for cited, held in ABBREVIATED:
            with self.subTest(cited=cited):
                self.assertTrue(gui._is_the_named_case(cited, held))
                self.assertTrue(gui._is_the_named_case(held, cited))
                self.assertEqual(gui._match_tier(cited, held), 3)

    def test_other_cases_are_still_other_cases(self):
        for cited, held in (
                ("Pleasants v. Pleasants", "Pleasant Grove City v. Summum"),
                ("Kilburn v. Woodworth", "Kilbourn v. Thompson"),
                ("Incredible Techs., Inc. v. Smith",
                 "Incredible Technologies, Inc. v. Jones")):
            with self.subTest(cited=cited):
                self.assertFalse(gui._is_the_named_case(cited, held))

    def test_an_abbreviation_is_one_token(self):
        self.assertEqual(gui._name_tokens("Nat'l Ass'n of Mfrs."),
                         ["natl", "assn", "mfrs"])
        self.assertEqual(gui._name_tokens("Erie R.R. Co."), ["erie", "rr"])
        self.assertTrue(gui._token_close("techs", "technologies"))
        self.assertTrue(gui._token_close("dept", "department"))
        self.assertFalse(gui._token_close("techs", "teachers"))

    def test_a_place_s_initials_are_the_place(self):
        self.assertEqual(gui._name_tokens("N.Y. Times"),
                         ["new", "york", "times"])
        self.assertEqual(gui._name_tokens("N. Y. Times"),
                         ["new", "york", "times"])
        self.assertEqual(gui._name_tokens("W. Va. Univ."),
                         ["west", "virginia", "univ"])
        self.assertEqual(gui._name_tokens("D.C. Water"),
                         ["district", "columbia", "water"])

    def test_other_initials_and_apostrophes_as_before(self):
        self.assertEqual(gui._name_tokens("J.P. Morgan & Co."), ["morgan"])
        self.assertEqual(gui._name_tokens("Chevron U.S.A. Inc."), ["chevron"])
        self.assertEqual(gui._name_tokens("O'Brien"), ["brien"])
        self.assertEqual(gui._name_tokens("Children's Hosp."),
                         ["children", "hosp"])


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class ReportedCaseTests(unittest.TestCase):
    """The case reported, as each source has it."""

    def test_static_case_law_keeps_the_scan(self):
        base = "https://static.case.law/f3d/400"
        volume = ({"400f3d1007": ("1007-01",)}, {"1007-01": HELD}, {})
        with mock.patch.object(gui, "_case_law_volume", return_value=volume):
            self.assertEqual(
                gui._case_law_named_url(
                    "400 F.3d 1007", f"{base}/case-pdfs/1007-01.pdf",
                    INCREDIBLE),
                f"{base}/case-pdfs/1007-01.pdf")

    def test_courtlistener_s_record_is_taken(self):
        client = mock.Mock()
        client.lookup_citation.return_value = [{"status": 200, "clusters": [{
            "id": 7, "case_name": HELD, "date_filed": "2005-03-08",
            "citations": [{"volume": 400, "reporter": "F.3d",
                           "page": 1007}]}]}]
        item = gui._cl_item_for_citation(client, "400 F.3d 1007",
                                         name=INCREDIBLE)
        self.assertEqual((item or {}).get("cluster_id"), 7)


if __name__ == "__main__":
    unittest.main()
