"""Reporters an old opinion names that the citation reader used to miss.

Each is a real reporter, checked against the Free Law Project's reporters
database (the standard abbreviation and its recorded variants), against
CourtListener's citation lookup (the form it resolves: "5 Sawy. 155" is In re
Ah Yup, 1 F. Cas. 223), and against static.case.law's list of the reporters it
scanned (the folder it files each under).  The renumbered series were checked
case by case: 5 Ired. 250 is 27 N.C. 250 (State v. Newsom), 2 Dutch. 215 is 26
N.J.L. 215 (State v. Roe), 3 Greenl. 326 is 3 Me. 326 (Lewis v. Webb).

What the reader has to do with each:

* the U.S. circuit reporters of the nineteenth century, written out as old
  opinions print them — "5 Sawyer, 155" — are looked up by their abbreviation;
* the state reports published under their reporters' names — "9 Paige, 507",
  "3 Harris & McHenry, 554" — reach static.case.law's folder for them;
* the ones renumbered into a state's official series — "5 Iredell, 250" —
  are looked up there;
* a reporter named for two men ("10 Serg. & Rawle 240") is read at all;
* "Reports" after a name ("18 Pick. R., 210") is only that.
"""

import os
import unittest

import citations
from citations import (
    case_law_reporter_slug,
    case_match_text,
    iter_case_citations,
    state_nominative_cites,
)

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None


def cites(text):
    return [case_match_text(m) for m in iter_case_citations(text)]


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class LookupFormTests(unittest.TestCase):
    """The form each citation is looked up by, and where."""

    def assertLookedUpAs(self, text, cite, lookup):
        got, _pin = gui._link_cite(text, {})
        self.assertEqual(got, cite)
        self.assertIn(lookup, gui._citation_search_variants(got))

    def test_the_circuit_reporters_by_their_abbreviations(self):
        for text, cite, lookup in (
            ("In re Ah Yup, 5 Sawyer, 155", "5 Sawyer 155", "5 Sawy. 155"),
            ("Gray v. Coffman, 3 Dillon, 393", "3 Dillon 393", "3 Dill. 393"),
            ("United States v. Mason, 6 Bissell, 350", "6 Bissell 350",
             "6 Biss. 350"),
            ("United States v. Hughes, 12 Blatchford, 553",
             "12 Blatchford 553", "12 Blatchf. 553"),
            ("Stockwell v. United States, 3 Clifford, 284", "3 Clifford 284",
             "3 Cliff. 284"),
            ("In re Ramsey, 2 Flippin, 451", "2 Flippin 451", "2 Flip. 451"),
            ("Root v. Shields, 1 Woolworth, 340", "1 Woolworth 340",
             "1 Woolw. 340"),
            ("United States v. New Bedford Bridge, 1 Woodbury & Minot, 401",
             "1 Woodbury & Minot 401", "1 Woodb. & M. 401"),
            # CourtListener resolves this one only with the spaced "C. C.".
            ("Corfield v. Coryell, 4 Washington Circuit Court, 371",
             "4 Washington Circuit Court 371", "4 Wash. C. C. 371"),
            ("Matter of Turner, 1 Abbott United States Reports, 84",
             "1 Abbott United States 84", "1 Abb. 84"),
        ):
            with self.subTest(text=text):
                self.assertLookedUpAs(text, cite, lookup)

    def test_the_circuit_reporters_named_as_the_abbreviation_is(self):
        for text in ("Bartlett v. Crittenden, 5 McLean, 32",
                     "United States v. Cruikshank, 1 Woods, 308",
                     "Harden v. Gordon, 2 Mason, 541",
                     "Clayton v. Stone, 2 Paine, 382",
                     "Drury v. Ewing (1 Bond, 540)",
                     "Taber v. United States, 1 Story, 1",
                     "The Mercer, 1 Sprague, 284"):
            with self.subTest(text=text):
                self.assertTrue(gui._link_cite(text, {})[0])

    def test_state_reports_under_their_reporters_names(self):
        for text, cite, lookup in (
            ("Tappan v. Gray, 9 Paige, 507", "9 Paige 507", "9 Paige Ch. 507"),
            ("Hitchcock vs. Aicken, 1 Caines, 460", "1 Caines 460",
             "1 Cai. 460"),
            ("Campbell v. Morris, 3 Harris & McHenry, 554",
             "3 Harris & McHenry 554", "3 H. & McH. 554"),
            ("Benton v. Burgot, (10 Sergeant & Rawle, 240)",
             "10 Sergeant & Rawle 240", "10 Serg. & Rawle 240"),
            ("Lanier v. Gallatas, 13 Louisiana Annual, 175",
             "13 Louisiana Annual 175", "13 La. Ann. 175"),
            ("The Amelia, 4 Philadelphia, 417", "4 Philadelphia 417",
             "4 Phila. 417"),
            ("Meehan v. Williams, 48 Penn. State, 238", "48 Penn. State 238",
             "48 Pa. 238"),
            ("Sinnickson v. Johnson, 17 N.J. Law, 129", "17 N.J. Law 129",
             "17 N.J.L. 129"),
            ("Scott v. Emerson, 15 Misso., 576", "15 Misso. 576", "15 Mo. 576"),
        ):
            with self.subTest(text=text):
                self.assertLookedUpAs(text, cite, lookup)

    def test_and_find_static_case_law_s_folder(self):
        for reporter, folder in (("Paige", "paige-ch"),
                                 ("Denio", "denio"),
                                 ("Keyes", "keyes"),
                                 ("Caines", "cai"),
                                 ("E.D. Smith", "ed-smith"),
                                 ("H. & McH.", "h-mch"),
                                 ("Serg. & Rawle", "serg-rawl"),
                                 ("Louisiana Annual", "la-ann")):
            with self.subTest(reporter=reporter):
                self.assertEqual(case_law_reporter_slug(reporter), folder)

    def test_the_renumbered_reports_in_their_official_series(self):
        for text, official in (
            ("North Carolina v. Newsom, 5 Iredell, 250", "27 N.C. 250"),
            ("Smith v. Jones, 1 Ired. Eq. 9", "36 N.C. 9"),
            ("Lewis v. Webb, 3 Greenleaf, 326", "3 Me. 326"),
            ("State v. Roe, 2 Dutcher, 215", "26 N.J.L. 215"),
            ("Davies v. Tingle, 8 B. Munroe, 539", "47 Ky. 539"),
            ("Rankin v. Lydia, 2 A.K. Marshall, 467", "9 Ky. 467"),
            ("Kemper v. Hawkins, 1 Virg. Cas., 74", "3 Va. 74"),
        ):
            with self.subTest(text=text):
                cite, _pin = gui._link_cite(text, {})
                self.assertEqual(gui._official_series_cites(cite), [official])

    def test_only_in_the_volumes_each_ran_to(self):
        # Iredell's Law has 13 volumes, Greenleaf 9, Dutcher 5.
        for cite in ("14 Ired. 1", "10 Greenl. 1", "6 Dutch. 1"):
            with self.subTest(cite=cite):
                self.assertEqual(state_nominative_cites(cite), [])


class AmpersandReporterTests(unittest.TestCase):
    """A reporter named for two men — read at all only now."""

    def test_it_is_read(self):
        for text, cite in (
            ("Campbell v. Morris, 3 H. & McH. 554", "3 H. & McH. 554"),
            ("Benton v. Burgot, 10 Serg. & Rawle 240", "10 Serg. & Rawle 240"),
            ("Smith v. Jones, 4 Gill & J. 1", "4 Gill & J. 1"),
        ):
            with self.subTest(text=text):
                self.assertEqual(cites(text), [cite])

    def test_a_journal_named_the_same_way_does_not_stop_an_id(self):
        # Betz: the journal McClain quotes is not what "id." means.
        text = ("McClain v. Metabolife, 401 F.3d 1233, 1241-42 (11th Cir.2005) "
                "(quoting Science for Judges I, 12 J.L. & Pol’y 1, 11 "
                "(2003)); see also id. at 1242")
        self.assertEqual(
            [action for _s, _e, action in citations.detect_links(text)],
            [("cite", "401 F.3d 1233@1241"), ("cite", "401 F.3d 1233@1242")])

    def test_a_firm_is_not(self):
        for text in ("In 1990 Smith & Wesson Co. 12 guns were sold",
                     "Procter & Gamble Co. 10 stores"):
            with self.subTest(text=text):
                self.assertEqual(cites(text), [])


class ReportsSuffixTests(unittest.TestCase):
    """ "Reports" after a reporter's name is only that."""

    def test_it_is_dropped(self):
        for text, cite in (
            ("Com. v. Aves, 18 Pick. R., 210", "18 Pick. 210"),
            ("Warren v. Lynch, 5 Johns. R. 289, the", "5 Johns. 289"),
            ("Commonwealth v. Pleasants, 10 Leigh Rep., 697", "10 Leigh 697"),
            ("Scott v. Emerson, (15 Missouri Reports, 576", "15 Missouri 576"),
            ("Thurber v. Blackburne, (1 New Hampshire Reports, 246)",
             "1 New Hampshire 246"),
        ):
            with self.subTest(text=text):
                self.assertEqual(cites(text), [cite])

    def test_but_not_from_initials_or_after_an_edition(self):
        # Canada's Supreme Court Reports; an Illinois Supreme Court Rule.
        self.assertEqual(cites("Morgentaler v. Queen, 1 S. C. R. 30 (1988)"),
                         ["1 S. C. R. 30"])
        self.assertEqual(cites("(188 Ill.2d R. 307(a)(7))"),
                         ["188 Ill.2d R. 307"])


class WhereAWordReporterStandsTests(unittest.TestCase):
    """A reporter named by a bare word is read only where a citation's
    volume stands."""

    def test_after_a_case_name_or_a_year(self):
        for text, cite in (
            ("In re Ah Yup, 5 Sawyer 155", "5 Sawyer 155"),
            ("In re Ah Yup, (1878) 5 Sawyer, 155", "5 Sawyer 155"),
            ("State v. Ah Chew, (1881) 16 Nevada, 50", "16 Nevada 50"),
        ):
            with self.subTest(text=text):
                self.assertEqual(cites(text), [cite])

    def test_not_in_prose(self):
        for text in ("we sold 5 Bond 12 times",
                     "the 2 Paine 3 Paige witnesses",
                     "at 5 Denio 3",
                     "In 1990, 3 Woods, 5 Mason jars"):
            with self.subTest(text=text):
                self.assertEqual(cites(text), [])

    def test_nor_is_one_with_its_reports_abbreviated(self):
        # After a sentence's period, "Cowen R." is still a citation.
        self.assertEqual(
            cites("Higgins vs. Scott, 2 Barn. and Adolp. 413. 4 Cowen R. 528, "
                  "note 10."),
            ["4 Cowen 528"])

    def test_a_dotted_reporter_is_no_bare_word(self):
        # "Wash. 2d" shares its key with the unpunctuated "Wash 2d"; it is
        # read wherever it stands, as it always was.
        self.assertEqual(
            cites("the Supreme Court of Washington. 57 Wash. 2d 95, 356 P. 2d "
                  "1."),
            ["57 Wash. 2d 95", "356 P. 2d 1"])


class AmbiguousNameTests(unittest.TestCase):
    """ "Washington" in full names more than one series in its first four
    volumes."""

    def test_its_first_volumes_are_left_unread(self):
        # Corfield v. Coryell: Washington's Circuit Court Reports.
        self.assertEqual(cites("Corfield v. Coryell, 4 Washington, 380"), [])

    def test_later_ones_are_the_state_s(self):
        self.assertEqual(cites("Some v. Case, 25 Washington, 60"),
                         ["25 Washington 60"])


if __name__ == "__main__":
    unittest.main()
