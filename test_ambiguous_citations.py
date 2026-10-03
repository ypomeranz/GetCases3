"""A citation whose case has only CourtListener's text, or that several cases
share.

"Pleasants v Pleasants, 2 Va 319" opened Pleasant Grove City v. Summum: no
search hit bore the citation, and the name check, matching both of the
query's "Pleasants" to the "Pleasant" of "Pleasant Grove City", took Summum
for the case — so CourtListener's text of the real one, the last resort, was
never reached.  And "2 Va 319" alone said "No case found", though
CourtListener has three cases at that citation (it files the Virginia
nominatives under "Va."): Pleasants v. Pleasants, Commonwealth v. Carter and
Swope v. Chambers.  Spotlight now lists them to choose from.
"""

import ast
import unittest
from pathlib import Path
from unittest import mock

try:
    import courtlistener_gui as gui
    from google_scholar import ScholarResult
except ImportError:  # pragma: no cover - exercised on a bare checkout
    gui = None

SRC = Path(__file__).with_name("courtlistener_gui.py").read_text(
    encoding="utf-8")

SUMMUM = "Pleasant Grove City, Utah v. Summum"


def _cluster(name: str, cid: int) -> dict:
    return {"case_name": name, "id": cid,
            "citations": [{"volume": 2, "reporter": "Va.", "page": 319}]}


# CourtListener's answer for "2 Va. 319": six records of three cases.
VA_319 = [{"status": 300, "citation": "2 Va. 319", "clusters": [
    _cluster("Commonwealth v. Carter", 7736613),
    _cluster("Commonwealth v. Carter", 7736612),
    _cluster("Commonwealth v. Carter", 7736611),
    _cluster("Pleasants v. Pleasants", 7730721),
    _cluster("Pleasants v. Pleasants", 7730722),
    _cluster("Swope v. Chambers", 6907096),
]}]


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class NameMatchTests(unittest.TestCase):

    def test_one_party_cannot_answer_for_both(self):
        self.assertEqual(
            gui._name_match_score("Pleasants v Pleasants", SUMMUM), 0.5)
        self.assertEqual(gui._name_match_score(
            "Smith v. Jones", "Smith Jones v. Brown"), 0.5)

    def test_a_match_on_either_side_still_counts(self):
        self.assertEqual(gui._name_match_score(
            "Pleasants v Pleasants", "Pleasants v. Pleasants"), 1.0)
        self.assertEqual(
            gui._name_match_score("Roe v. Wade", "Wade v. Roe"), 1.0)

    def test_a_hit_on_its_name_alone_must_be_the_case(self):
        named = gui._is_the_named_case
        self.assertFalse(named("Pleasants v Pleasants", SUMMUM))
        self.assertFalse(named("Pleasants v Pleasants",
                               "State ex rel. Billing v. Point Pleasant"))
        self.assertTrue(named("Pleasants v Pleasants",
                              "Pleasants v. Pleasants"))
        self.assertTrue(named("NLRB v. Jones & Laughlin", "National Labor "
                              "Relations Board v. Jones & Laughlin Steel "
                              "Corp."))
        self.assertTrue(named("In re Gault", "In re Gault"))


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class LastResortTests(unittest.TestCase):
    """CourtListener's text opens when nothing better is the case."""

    def _app(self):
        win = object.__new__(gui.CourtListenerGUI)
        win.root = object()
        win._post_root = mock.Mock()
        return win

    def test_the_text_of_the_case_not_another_case_opens(self):
        fetcher = mock.Mock()
        fetcher.fetch_by_citation.return_value = None
        fetcher.search_cases.return_value = [ScholarResult(
            SUMMUM, "https://scholar.google.com/scholar_case?case=1",
            "555 US 460 - Supreme Court, 2009 - Google Scholar")]
        fetcher.pick_cited_result.return_value = None
        fetcher.take_post_search_failure.return_value = ""
        target = {"caseName": "Pleasants v. Pleasants", "cluster_id": 7730722,
                  "citation": ["2 Va. 319"]}
        app = self._app()
        with mock.patch.object(gui, "_case_law_text_source",
                               return_value=None), \
                mock.patch.object(gui, "_cl_item_for_citation",
                                  return_value=target) as lookup, \
                mock.patch.object(gui, "_assemble_case_parts",
                                  return_value=([], [], "ROANE, Judge.", {})):
            self.assertTrue(app._try_open_citation(
                "Pleasants v Pleasants", "2 Va 319", "", fetcher, object()))
        fetcher.fetch_by_url.assert_not_called()
        lookup.assert_called_once()
        (open_cl,), _kw = app._post_root.call_args
        with mock.patch.object(gui, "_ScholarTextWindow") as window:
            open_cl()
        self.assertIs(window.call_args.kwargs["item"], target)


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class SharedCitationTests(unittest.TestCase):

    def test_the_cases_a_citation_could_be(self):
        client = mock.Mock()
        client.lookup_citation.side_effect = (
            lambda cite: VA_319 if cite == "2 Va. 319" else [])
        self.assertEqual(
            gui._cases_bearing_citation(client, "2 Va 319"),
            ["Commonwealth v. Carter", "Pleasants v. Pleasants",
             "Swope v. Chambers"])

    def test_one_case_is_no_choice(self):
        client = mock.Mock()
        client.lookup_citation.return_value = [{"status": 200, "clusters": [
            _cluster("Pleasants v. Pleasants", 1),
            _cluster("Pleasants v. Pleasants", 2)]}]
        self.assertEqual(gui._cases_bearing_citation(client, "2 Va. 319"),
                         ["Pleasants v. Pleasants"])

    def _sig(self, name, cite="2 Va. 319"):
        return gui._case_signature(name, cite, "")

    def test_cases_sharing_a_citation_are_listed_apart(self):
        self.assertFalse(gui._same_case(self._sig("Pleasants v. Pleasants"),
                                        self._sig("Commonwealth v. Carter")))
        # A frequent name in common is no party in common.
        self.assertFalse(gui._same_case(self._sig("Commonwealth v. Carter"),
                                        self._sig("Commonwealth v. Smith")))

    def test_one_case_named_two_ways_is_still_one(self):
        self.assertTrue(gui._same_case(
            self._sig(SUMMUM, "555 U.S. 460"),
            self._sig("Pleasant Grove City v. Summum", "555 U.S. 460")))
        self.assertTrue(gui._same_case(
            self._sig("Com. v. Pleasants"),
            self._sig("Commonwealth v. Pleasants")))
        # A reverse-caption row carries no name to disagree with.
        self.assertTrue(gui._same_case(
            gui._case_signature("Wade v. Roe", "410 U.S. 113", "",
                                include_name=False),
            self._sig("Roe v. Wade", "410 U.S. 113")))


class SpotlightListsThemTests(unittest.TestCase):
    """Wiring: a citation several cases share brings Spotlight back as a
    list of them, which the direct open would otherwise skip."""

    def _source(self, name):
        tree = ast.parse(SRC)
        body = next(n.body for n in tree.body
                    if isinstance(n, ast.ClassDef)
                    and n.name == "CourtListenerGUI")
        node = next(n for n in body
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.get_source_segment(SRC, node)

    def test_a_miss_at_a_shared_citation_lists_the_cases(self):
        src = self._source("_open_lookup_query")
        self.assertIn("_cases_bearing_citation(client, cite)", src)
        self.assertIn("self._spotlight_search, query", src)

    def test_the_list_skips_the_direct_open(self):
        src = self._source("_toggle_quick_search_popup")
        self.assertIn("note=list_note", src)

    def test_the_dropdown_takes_every_case_at_the_citation(self):
        src = self._source("_show_spotlight_dropdown")
        self.assertIn('entry.get("status") not in (200, 300)', src)
        self.assertIn("_scholar_bears_citation(r, c)", src)


if __name__ == "__main__":
    unittest.main()
