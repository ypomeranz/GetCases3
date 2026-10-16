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
from unittest import mock

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
                                 ("Louisiana Annual", "la-ann"),
                                 ("Sandf. Ch.", "sand-ch"),
                                 ("Sandf.", "sand-ch")):
            with self.subTest(reporter=reporter):
                self.assertEqual(case_law_reporter_slug(reporter), folder)

    def test_sandfords_chancery_as_old_opinions_cite_it(self):
        # "Sandf. Ch." and the bare "Sandf." are Sandford's Chancery
        # Reports, the Bluebook's "Sand. Ch.".
        for text, cite, lookup in (
            ("Smith v. Jones, 4 Sandf. Ch. 123", "4 Sandf. Ch. 123",
             "4 Sand. Ch. 123"),
            ("Smith v. Jones, 2 Sandf. Ch., 97", "2 Sandf. Ch. 97",
             "2 Sand. Ch. 97"),
            ("Smith v. Jones, 1 Sandf. Ch. R., 409", "1 Sandf. Ch. 409",
             "1 Sand. Ch. 409"),
            ("Smith v. Jones, 3 Sandf., 5", "3 Sandf. 5", "3 Sand. Ch. 5"),
        ):
            with self.subTest(text=text):
                self.assertLookedUpAs(text, cite, lookup)
                self.assertEqual(
                    gui._static_case_law_url(cite),
                    gui._static_case_law_url(lookup))

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
    """ "Wash." — and "Washington" in full — names three series in its first
    volumes: Bushrod Washington's Virginia Reports (1–2 Va.), his Circuit
    Court Reports (four volumes), and the Washington Reports from 1889.  The
    year of the opinion citing it says which it can be, and the case's name
    picks among those left."""

    def test_its_first_volumes_are_read(self):
        # Corfield v. Coryell: Washington's Circuit Court Reports.
        self.assertEqual(cites("Corfield v. Coryell, 4 Washington, 380"),
                         ["4 Washington 380"])

    def test_later_ones_are_the_state_s(self):
        self.assertEqual(cites("Some v. Case, 25 Washington, 60"),
                         ["25 Washington 60"])

    def test_an_old_opinion_s_citation_is_bushrod_washington_s(self):
        for cite, year, meant in (
                # The Slaughter-House Cases (1873) and Ex parte Lange (1873).
                ("4 Washington 380", "1873", "4 Wash. C.C. 380"),
                ("4 Wash. 410", "1873", "4 Wash. C.C. 410"),
                # Cherokee Nation v. Georgia (1831): Warder v. Arell, 2 Va.
                # 282 — CourtListener files a Washington case of 1891 at
                # "2 Wash. 298".
                ("2 Wash. 298", "1831", "2 Va. 298"),
                ("1 Wash. 336", "1823", "1 Va. 336")):
            with self.subTest(cite=cite, year=year):
                self.assertEqual(citations.cite_for_era(cite, year), meant)

    def test_a_later_or_undated_one_is_left_as_written(self):
        for cite, year in (("8 Wash. 412", "1926"), ("2 Wash. 298", ""),
                           ("4 Wash. C.C. 371", "1877"),
                           ("81 Wash. 2d 788", "1974"),
                           ("4 Wn. 380", "1873")):
            with self.subTest(cite=cite, year=year):
                self.assertEqual(citations.cite_for_era(cite, year), cite)

    def test_the_other_series_are_tried_after_the_state_s(self):
        self.assertEqual(citations.washington_series_cites("2 Wash. 298"),
                         ["2 Va. 298", "2 Wash. C.C. 298"])
        self.assertEqual(citations.washington_series_cites("4 Washington 380"),
                         ["4 Wash. C.C. 380"])
        self.assertEqual(citations.washington_series_cites("8 Wash. 412"), [])
        self.assertEqual(
            citations.washington_series_cites("57 Wash. 2d 95"), [])


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class PageInsideCaseTests(unittest.TestCase):
    """A page cited as though the case began there — McCready v. Virginia's
    "Corfield v. Coryell, 4 Wash. C.C. 380" — is found as the case the name
    names, beginning a little before it in the same volume."""

    CORFIELD = {"caseName": "Corfield v. Coryell", "cluster_id": 1,
                "citation": ["6 F. Cas. 546", "4 Wash. C. C. 371"]}
    COATS = {"caseName": "Coats v. West Coast Fire & Marine Insurance",
             "cluster_id": 2, "citation": ["4 Wash. 375", "30 P. 404"]}

    def _client(self, results):
        client = mock.Mock()
        client.search.return_value = {"results": results}
        client.lookup_citation.return_value = []
        return client

    def test_the_named_case_is_found_from_a_page_inside_it(self):
        client = self._client([self.CORFIELD])
        self.assertIs(gui._cl_case_containing_page(
            client, "4 Wash. C.C. 380", "Corfield v. Coryell"), self.CORFIELD)
        query = client.search.call_args.args[0]
        self.assertIn('citation:("4 Wash. C. C.")', query)
        self.assertIn("corfield OR coryell", query)

    def test_only_the_case_the_name_names(self):
        client = self._client([self.COATS])
        self.assertIsNone(gui._cl_case_containing_page(
            client, "4 Wash. 380", "Corfield v. Coryell"))

    def test_far_from_the_page_only_the_volume_s_one_case_of_the_name(self):
        # Another printing may paginate the volume otherwise (20 N.C.: the
        # Court's "State v. Manuel, 20 N.C. 144" is page 20 of the printing
        # CourtListener and static.case.law have).
        client = self._client([self.CORFIELD])
        self.assertIs(gui._cl_case_containing_page(
            client, "4 Wash. C.C. 900", "Corfield v. Coryell"), self.CORFIELD)
        again = dict(self.CORFIELD, cluster_id=9,
                     citation=["4 Wash. C. C. 600"])
        client = self._client([self.CORFIELD, again])
        self.assertIsNone(gui._cl_case_containing_page(
            client, "4 Wash. C.C. 900", "Corfield v. Coryell"))

    def test_without_a_name_nothing_is_guessed(self):
        client = self._client([self.CORFIELD])
        self.assertIsNone(gui._cl_case_containing_page(
            client, "4 Wash. C.C. 380", ""))
        client.search.assert_not_called()

    def test_the_resolver_turns_to_it_when_another_case_is_filed_there(self):
        # CourtListener files the Washington Reports' Coats at "4 Wash. 380";
        # the name says the citation is another case's.
        client = self._client([self.CORFIELD])
        client.lookup_citation.return_value = [
            {"status": 200, "clusters": [{
                "case_name": self.COATS["caseName"], "id": 2,
                "citations": [{"volume": "4", "reporter": "Wash.",
                               "page": "375"}]}]}]
        with mock.patch.object(gui, "_cl_case_containing_page",
                                        return_value=self.CORFIELD) as inside:
            got = gui._cl_item_for_citation(client, "4 Wash. 380",
                                            name="Corfield v. Coryell")
        self.assertIs(got, self.CORFIELD)
        inside.assert_called_once()

    def test_the_name_from_the_span_survives_the_series_it_is_read_as(self):
        self.assertEqual(gui._citation_link_name(
            "Warder vs. Arrell, 2 Wash. 298", "2 Va. 298"), "Warder vs. Arrell")
        self.assertEqual(gui._citation_link_name(
            "Roe v. Wade, 410 U.S. 113", "411 U.S. 113"), "")



class PeriodlessReporterTests(unittest.TestCase):
    """A court that drops a reporter's periods — Michigan's own style, New
    York's — still cites it.  "People v Roberts, 211 Mich 187; 178 NW 690
    (1920)" in People v. Kevorkian, 447 Mich. 436, 445, was no citation the
    reader could open."""

    def _links(self, text):
        return [(text[s:e], a) for s, e, a in citations.detect_links(text)]

    def test_read_and_given_their_periods(self):
        self.assertEqual(
            self._links("In the murder case, People v Roberts, 211 Mich 187; "
                        "178 NW 690 (1920), is overruled.  Id. at 190."),
            [("Roberts, 211 Mich 187", ("cite", "211 Mich. 187")),
             ("178 NW 690 (1920)", ("cite", "178 N.W. 690")),
             ("Id. at 190", ("cite", "211 Mich. 187@190"))])
        for text, cite in (
                ("Hobbins v Attorney General, 205 Mich App 1; 518 NW2d 487",
                 "205 Mich. App. 1"),
                ("Palsgraf v Long Is. R.R. Co., 248 NY 339, 344 [1928]",
                 "248 N.Y. 339@344"),
                ("see Paterno v Laser Spine Inst., 24 NY3d 370, 377 [2014]",
                 "24 N.Y.3d 370@377"),
                ("Friedman v Gearrity, 33 AD2d 1044", "33 A.D.2d 1044"),
                ("Roe v Wade, 410 US 113, 153", "410 U.S. 113@153"),
                ("People v Smith, 93 Cal App 4th 1", "93 Cal. App. 4th 1")):
            with self.subTest(text=text):
                self.assertEqual(self._links(text)[0][1], ("cite", cite))
        self.assertEqual(citations.canonical_reporter("NW2d"), "N.W.2d")

    def test_only_where_a_citation_stands(self):
        for text in ("In 410 US 113 the Court held",
                     "about 211 Mich 187 people",
                     "the 1600 NW 690 building",
                     # A lone letter, a word, or a series a state never had.
                     "Smith v Jones, 5 A 10",
                     "Halpern v Attorney Gen. of Can., 65 OR3d 161",
                     # A year where the volume stands: a public-domain
                     # citation, a session law.
                     "Rose v. United Equitable Ins. Co., 2001 ND 154, ¶ 10",
                     "See, e.g., 1993 PA 3, which was enacted"):
            with self.subTest(text=text):
                self.assertEqual(self._links(text), [])
        # The state names cited bare as ever are read anywhere still.
        self.assertEqual(
            self._links("administrators. 93 Idaho 511, 465 P. 2d 635.")[0],
            ("93 Idaho 511", ("cite", "93 Idaho 511")))


if __name__ == "__main__":
    unittest.main()
