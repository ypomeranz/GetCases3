"""A year a source gives that may be the term's, not the decision's
(decision_year): Scholar heads Murray's Lessee, decided in 1856, "59 U.S.
272 (1855)", its December Term."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["GETCASES_SKIP_DEPENDENCY_PROMPT"] = "1"

import decision_year
from courtlistener_gui import _ScholarTextWindow
from google_scholar import Block, Span


def scotus_blocks(cite_line, caption, *extra):
    return [Block("center", [Span(cite_line)]), Block("center", [Span(caption)]),
            *extra, Block("center", [Span("Supreme Court of United States.")]),
            Block("para", [Span("Mr. Justice MILLER delivered the opinion "
                                "of the court.")])]


class IsolatedCache(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name) / "decision_years.json"
        cache = patch.object(decision_year, "CACHE_PATH", self.cache)
        cache.start()
        self.addCleanup(cache.stop)


class UncertainTests(unittest.TestCase):
    def test_a_supreme_court_term_that_ran_into_the_next_year(self):
        blocks = scotus_blocks("94 U.S. 391 (1876)", "McCREADY v. VIRGINIA.")
        self.assertTrue(decision_year.term_year_uncertain(
            blocks, "1876", is_scotus=True))

    def test_a_printed_decision_date_settles_it(self):
        blocks = scotus_blocks("112 U.S. 94 (1884)", "ELK v. WILKINS.",
                               Block("center", [Span("Decided November 3, "
                                                     "1884.")]))
        self.assertFalse(decision_year.term_year_uncertain(
            blocks, "1884", is_scotus=True))

    def test_terms_that_ended_where_they_began(self):
        # Until 1844 the Court sat from January into spring.
        blocks = scotus_blocks("37 U.S. 524 (1838)", "KENDALL v. UNITED "
                               "STATES ex rel. STOKES.")
        self.assertFalse(decision_year.term_year_uncertain(
            blocks, "1838", is_scotus=True))

    def test_another_courts_term_line(self):
        def blocks(term):
            return [Block("center", [Span("3 Ohio St. 1 (1855)")]),
                    Block("center", [Span("SMITH v. JONES.")]),
                    Block("center", [Span(term)]),
                    Block("para", [Span("The opinion.")])]
        self.assertTrue(decision_year.term_year_uncertain(
            blocks("December Term, 1855."), "1855"))
        self.assertFalse(decision_year.term_year_uncertain(
            blocks("January Term, 1855."), "1855"))
        self.assertFalse(decision_year.term_year_uncertain(
            blocks("December Term, 1854."), "1855"))

    def test_the_year_given_or_the_next(self):
        self.assertTrue(decision_year.plausible_year("1855", "1856"))
        self.assertTrue(decision_year.plausible_year("1855", "1855"))
        self.assertFalse(decision_year.plausible_year("1855", "1857"))
        self.assertFalse(decision_year.plausible_year("1855", "1854"))
        self.assertFalse(decision_year.plausible_year("1855", ""))


class CacheTests(IsolatedCache):
    def test_kept_by_citation_however_spaced(self):
        self.assertEqual(decision_year.remembered_year("94 U.S. 391"), "")
        decision_year.remember_year("94 U. S. 391", "1877", "courtlistener")
        self.assertEqual(decision_year.remembered_year("4 Otto 391",
                                                       "94 U.S. 391"), "1877")
        self.assertEqual(decision_year.known_decision_year("1876",
                                                           "94U.S.391"),
                         "1877")
        # …only for a year it can be the decision of.
        self.assertEqual(decision_year.known_decision_year("1944",
                                                           "94 U.S. 391"), "")


class TextViewTests(IsolatedCache):
    @staticmethod
    def _window(cite_line="94 U.S. 391 (1876)",
                caption="McCREADY v. VIRGINIA."):
        win = object.__new__(_ScholarTextWindow)
        win._item = {}
        win._blocks = scotus_blocks(cite_line, caption)
        return win

    def _enrich(self, win, courtlistener="", cap=""):
        class ImmediateThread:
            def __init__(self, *, target, daemon):
                self.target = target

            def start(self):
                self.target()

        win._bb = win._compute_bluebook_parts()
        win._base_citation_override = ""
        win._header_cites = []
        win._is_scotus = True
        win._app = Mock()
        win._app._token_var.get.return_value = "token"
        win._post = Mock()
        with (
            patch("courtlistener_gui.threading.Thread", ImmediateThread),
            patch.object(_ScholarTextWindow, "_cl_court_and_year",
                         return_value=("scotus", courtlistener)) as cl,
            patch("courtlistener_gui._case_law_decision_year",
                  return_value=cap) as case_law,
        ):
            win._enrich_citation()
        return cl, case_law

    def test_the_page_year_is_provisional(self):
        bb = self._window()._compute_bluebook_parts()
        self.assertEqual(bb["year"], "1876")
        self.assertEqual(bb["_term_year"], "1876")

    def test_courtlistener_first(self):
        win = self._window()
        _cl, case_law = self._enrich(win, courtlistener="1877", cap="1876")
        win._post.assert_called_once_with(
            win._apply_enriched_citation, "", "1877", win._bb["name"])
        case_law.assert_not_called()
        self.assertEqual(decision_year.remembered_year("94 U.S. 391"), "1877")

    def test_then_the_caselaw_access_project(self):
        win = self._window()
        self._enrich(win, courtlistener="1880", cap="1877")   # 1880: no term
        win._post.assert_called_once_with(
            win._apply_enriched_citation, "", "1877", win._bb["name"])

    def test_no_answer_keeps_the_page_year_and_asks_again(self):
        win = self._window()
        self._enrich(win)
        win._post.assert_not_called()
        self.assertEqual(decision_year.remembered_year("94 U.S. 391"), "")

    def test_a_confirmed_year_is_kept_too(self):
        # Luther v. Borden, heard in the December Term 1848: Scholar's 1849
        # is its decision's.
        win = self._window("48 U.S. 1 (1849)", "LUTHER v. BORDEN.")
        self._enrich(win, courtlistener="1849")
        win._post.assert_not_called()
        self.assertEqual(decision_year.remembered_year("48 U.S. 1"), "1849")
        self.assertEqual(win._compute_bluebook_parts()["_term_year"], "")

    def test_a_year_found_before_is_used_at_once(self):
        decision_year.remember_year("94 U.S. 391", "1877", "courtlistener")
        bb = self._window()._compute_bluebook_parts()
        self.assertEqual(bb["year"], "1877")
        self.assertEqual(bb["_term_year"], "")

    def test_a_settled_year_needs_no_asking(self):
        bb = self._window("59 U.S. 272 (1855)", "DEN v. HOBOKEN LAND AND "
                          "IMPROVEMENT COMPANY.")._compute_bluebook_parts()
        self.assertEqual(bb["year"], "1856")
        self.assertEqual(bb["_term_year"], "")


if __name__ == "__main__":
    unittest.main()
