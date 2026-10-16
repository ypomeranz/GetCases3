"""A year a source gives that may be the term's, not the decision's
(decision_year): Scholar heads Murray's Lessee, decided in 1856, "59 U.S.
272 (1855)", its December Term."""

import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["GETCASES_SKIP_DEPENDENCY_PROMPT"] = "1"

import courtlistener_gui
import decision_year
from courtlistener_gui import _ScholarTextWindow
from google_scholar import Block, Span

#: Live CourtListener and Caselaw Access Project answers (see its "_about").
LIVE = json.loads((Path(__file__).with_name("test_data")
                   / "decision_year_citing.json").read_text(encoding="utf-8"))


def searching(*responses):
    """A CourtListener client whose searches answer *responses* in turn."""
    return Mock(search=Mock(side_effect=list(responses)))


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


class CitedFormTests(unittest.TestCase):
    """The year a citing opinion gives the case, read off its citation."""

    def test_the_forms_searched_for(self):
        self.assertEqual(decision_year.citing_phrases("59 U.S. 272"),
                         ["59 U.S. 272", "18 How. 272"])
        self.assertEqual(decision_year.citing_phrases("18 How. 272"),
                         ["59 U.S. 272", "18 How. 272"])
        self.assertEqual(decision_year.citing_phrases("94 U. S. 391"),
                         ["94 U.S. 391", "4 Otto 391"])
        self.assertEqual(decision_year.citing_phrases("109 U.S. 3"),
                         ["109 U.S. 3"])
        self.assertEqual(decision_year.citing_phrases("3 Ohio St. 1"),
                         ["3 Ohio St. 1"])
        self.assertEqual(decision_year.citing_query("48 U.S. 1",
                                                    [9416470, 86466]),
                         '(cites:9416470 OR cites:86466) AND '
                         '("48 U.S. 1" OR "7 How. 1")')

    def test_every_way_of_writing_the_citation(self):
        for text in ("59 U.S. 272 (1856)", "59 U. S. 272 (1856)",
                     "18 How. 272 (1856)", "59 U.S. (18 How.) 272 (1856)",
                     "18 How. (59 U.S.) 272 (1856)"):
            with self.subTest(text):
                self.assertEqual(
                    decision_year.years_cited(text, "59 U.S. 272"), ["1856"])

    def test_past_pin_and_parallel_cites(self):
        for text in ("59 U.S. 272, 276 (1856)",
                     "59 U.S. 272, 276-77 (1856)",
                     "59 U.S. 272, 284 n.4 (1856)",
                     "59 U.S. 272, 15 L. Ed. 372 (1856)",
                     "59 U.S. (18 How.) 272, 276, 15 L. Ed. 372 (1856)",
                     "<mark>59 U.S. 272</mark>, 277 (1856)",
                     "59 U.S. (<mark>18 How.) 272</mark>, 282–84 (1856)"):
            with self.subTest(text):
                self.assertEqual(
                    decision_year.years_cited(text, "59 U.S. 272"), ["1856"])
        self.assertEqual(decision_year.years_cited(
            "Smith v. Jones, 3 Ohio St. 1 (Ohio 1855)", "3 Ohio St. 1"),
            ["1855"])

    def test_only_the_case_s_own_year(self):
        self.assertEqual(decision_year.years_cited(
            "59 U.S. 272, 284; Smith v. Jones, 60 U.S. 1 (1857)",
            "59 U.S. 272"), [])
        self.assertEqual(decision_year.years_cited(
            "18 How. 272. In 1856 the Court", "59 U.S. 272"), [])
        self.assertEqual(decision_year.years_cited(
            "59 U.S. 393 (1857)", "59 U.S. 272"), [])

    def test_a_clear_majority(self):
        pick = decision_year.pick_year
        self.assertEqual(pick({"1877": 9, "1876": 1}), "1877")
        self.assertEqual(pick({"1856": 4, "1855": 2}), "1856")   # two thirds
        self.assertEqual(pick({"1856": 2}), "1856")
        self.assertEqual(pick({"1856": 1}), "")                  # too few
        self.assertEqual(pick({"1856": 9, "1855": 8}), "")       # split
        self.assertEqual(pick({}), "")

    def test_an_opinion_filed_twice_counts_once(self):
        snippet = "Murray's Lessee, 59 U.S. 272, 277 (1856)). Lastly"
        results = [{"opinions": [{"snippet": snippet}]},
                   {"opinions": [{"snippet": snippet + " "}]},
                   {"opinions": [{"snippet": "18 How. 272 (1855)"}]},
                   {"opinions": [{"snippet": "18 How. 272 (1955)"}]}]
        self.assertEqual(decision_year.count_citing_years(
            results, "59 U.S. 272", "1855"), {"1856": 1, "1855": 1})


class CitingSampleTests(unittest.TestCase):
    """The deciding court's own citations, as CourtListener returned them."""

    def sample(self, cite, which="scotus", court="scotus"):
        case = LIVE[cite]
        client = searching(case[which]["response"])
        found = decision_year.citing_sample_year(
            client, cite, case["opinion_ids"], case["given"], court=court)
        return found, client

    def test_the_supreme_court_s_own_citations(self):
        # Ex parte Lange, December Term 1873, decided January 30, 1874.
        found, client = self.sample("85 U.S. 163")
        self.assertEqual(found, ("1874", {"1874": 15}))
        query, = client.search.call_args.args
        self.assertEqual(query, LIVE["85 U.S. 163"]["scotus"]["query"])
        kwargs = client.search.call_args.kwargs
        self.assertEqual(kwargs["court"], "scotus")
        self.assertTrue(kwargs["highlight"])
        self.assertEqual(kwargs["extra"], {"order_by": "dateFiled desc"})

    def test_not_other_courts_which_give_the_term_s_year(self):
        lange = LIVE["85 U.S. 163"]
        counts = decision_year.count_citing_years(
            lange["all_courts"]["response"]["results"], "85 U.S. 163",
            lange["given"])
        self.assertEqual(counts, {"1873": 14, "1874": 5})
        self.assertEqual(decision_year.pick_year(counts), "1873")

    def test_the_year_given_confirmed(self):
        # Luther v. Borden, December Term 1848, decided January 3, 1849.
        self.assertEqual(self.sample("48 U.S. 1")[0], ("1849", {"1849": 15}))

    def test_a_majority_over_a_dissenter(self):
        # McCready v. Virginia, October Term 1876, decided April 30, 1877.
        self.assertEqual(self.sample("94 U.S. 391")[0],
                         ("1877", {"1877": 9, "1876": 1}))

    def test_too_few_settle_nothing(self):
        # Buck v. Colbath: of 37 the Court's own, one gives a year.
        self.assertEqual(self.sample("70 U.S. 334")[0], ("", {"1866": 1}))

    def test_no_court_no_sample(self):
        found, client = self.sample("85 U.S. 163", court="")
        self.assertEqual(found, ("", {}))
        client.search.assert_not_called()


class CourtListenerAndCapTests(unittest.TestCase):
    """What the two databases answer for the early U.S. Reports."""

    def test_an_approximate_courtlistener_date_is_no_answer(self):
        # The Civil Rights Cases' cluster: right, but marked approximate.
        cluster = LIVE["courtlistener_clusters"]["109 U.S. 3"]
        client = Mock(get_cluster=Mock(return_value=cluster))
        item = {"cluster_id": cluster["id"]}
        self.assertEqual(_ScholarTextWindow._cl_court_and_year(
            client, "109 U.S. 3", item), ("", "1883"))
        self.assertEqual(_ScholarTextWindow._cl_court_and_year(
            client, "109 U.S. 3", item, exact_year=True), ("", ""))
        self.assertIn("date_filed_is_approximate",
                      client.get_cluster.call_args.kwargs["fields"])

    def test_an_exact_courtlistener_date(self):
        cluster = LIVE["courtlistener_clusters"]["94 U.S. 391"]
        client = Mock(get_cluster=Mock(return_value=cluster))
        self.assertEqual(_ScholarTextWindow._cl_court_and_year(
            client, "94 U.S. 391", {"cluster_id": cluster["id"]},
            exact_year=True), ("", "1877"))

    def test_cap_s_month_only_date_is_the_term_s(self):
        dates = LIVE["caselaw_decision_dates"]

        def metadata(cite):
            return {"decision_date": dates[cite]}

        year = courtlistener_gui._case_law_decision_year
        with patch("courtlistener_gui._case_law_metadata", metadata):
            # McCready v. Virginia, decided April 30, 1877: "1876-10".
            self.assertEqual(year(["94 U.S. 391"]), "1876")
            self.assertEqual(year(["94 U.S. 391"], full_date=True), "")
            self.assertEqual(year(["110 U.S. 516"], full_date=True), "1884")


class AskingTheCitingOpinionsTests(unittest.TestCase):
    """courtlistener_gui._citing_decision_year, the text view's way in."""

    def ask(self, client, court="scotus", ids=(88804,)):
        with patch("courtlistener_gui._cited_opinion_ids",
                   return_value=list(ids)) as cited:
            found = courtlistener_gui._citing_decision_year(
                client, "85 U.S. 163", {}, "Ex parte Lange", "1873", court)
        return found, cited

    def test_the_answer_and_its_counts(self):
        client = searching(LIVE["85 U.S. 163"]["scotus"]["response"])
        self.assertEqual(self.ask(client)[0], ("1874", {"1874": 15}))

    def test_a_failed_search_is_no_answer(self):
        client = Mock(search=Mock(side_effect=OSError("offline")))
        self.assertEqual(self.ask(client)[0], ("", {}))

    def test_nothing_asked_without_the_court_or_the_case(self):
        client = Mock()
        found, cited = self.ask(client, court="")
        self.assertEqual(found, ("", {}))
        cited.assert_not_called()
        self.assertEqual(self.ask(client, ids=())[0], ("", {}))
        client.search.assert_not_called()


class CacheTests(IsolatedCache):
    def test_what_the_citing_opinions_counted_is_kept(self):
        decision_year.remember_year("94 U.S. 391", "1877", "citing",
                                    counts={"1877": 9, "1876": 1})
        entry = json.loads(self.cache.read_text(encoding="utf-8"))["94U.S.391"]
        self.assertEqual((entry["year"], entry["source"], entry["counts"]),
                         ("1877", "citing", {"1877": 9, "1876": 1}))

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

    def _enrich(self, win, courtlistener="", cap="", citing=("", {}),
                cap_metadata=None):
        """Run the window's enrichment with each source answering as given:
        the citing opinions *citing* (year, counts), CourtListener
        *courtlistener*, and the Caselaw Access Project *cap* — or, given
        *cap_metadata*, its record, read by the app's own code."""
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
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("courtlistener_gui.threading.Thread", ImmediateThread))
            sample = stack.enter_context(
                patch("courtlistener_gui._citing_decision_year",
                      return_value=citing))
            cl = stack.enter_context(
                patch.object(_ScholarTextWindow, "_cl_court_and_year",
                             return_value=("scotus", courtlistener)))
            if cap_metadata is None:
                case_law = stack.enter_context(
                    patch("courtlistener_gui._case_law_decision_year",
                          return_value=cap))
            else:
                case_law = stack.enter_context(
                    patch("courtlistener_gui._case_law_metadata",
                          return_value=cap_metadata))
            win._enrich_citation()
        return sample, cl, case_law

    def test_the_page_year_is_provisional(self):
        bb = self._window()._compute_bluebook_parts()
        self.assertEqual(bb["year"], "1876")
        self.assertEqual(bb["_term_year"], "1876")

    def test_the_citing_opinions_first(self):
        win = self._window()
        sample, cl, case_law = self._enrich(
            win, citing=("1877", {"1877": 9, "1876": 1}),
            courtlistener="1876", cap="1876")
        win._post.assert_called_once_with(
            win._apply_enriched_citation, "", "1877", win._bb["name"])
        # Asked of the Supreme Court's own opinions.
        self.assertEqual(sample.call_args.args[1:],
                         ("94 U.S. 391", {}, win._bb["name"], "1876",
                          "scotus"))
        case_law.assert_not_called()
        self.assertEqual(decision_year.remembered_year("94 U.S. 391"), "1877")
        entry = json.loads(self.cache.read_text(encoding="utf-8"))["94U.S.391"]
        self.assertEqual((entry["source"], entry["counts"]),
                         ("citing", {"1877": 9, "1876": 1}))

    def test_then_courtlistener(self):
        win = self._window()
        _sample, cl, case_law = self._enrich(
            win, citing=("", {"1877": 1}), courtlistener="1877", cap="1876")
        win._post.assert_called_once_with(
            win._apply_enriched_citation, "", "1877", win._bb["name"])
        # A date CourtListener marks approximate is none (see
        # CourtListenerAndCapTests).
        self.assertIs(cl.call_args.kwargs["exact_year"], True)
        case_law.assert_not_called()
        entry = json.loads(self.cache.read_text(encoding="utf-8"))["94U.S.391"]
        self.assertEqual((entry["year"], entry["source"], entry["counts"]),
                         ("1877", "courtlistener", {"1877": 1}))

    def test_then_the_caselaw_access_project(self):
        win = self._window()
        _sample, _cl, case_law = self._enrich(
            win, courtlistener="1880", cap="1877")   # 1880: no term
        win._post.assert_called_once_with(
            win._apply_enriched_citation, "", "1877", win._bb["name"])
        self.assertIs(case_law.call_args.kwargs["full_date"], True)

    def test_cap_s_term_date_confirms_nothing(self):
        # CAP gives McCready "1876-10", the month its term opened: it must
        # not settle the page's 1876 as the decision's.
        win = self._window()
        self._enrich(win, cap_metadata={"decision_date": "1876-10"})
        win._post.assert_not_called()
        self.assertEqual(decision_year.remembered_year("94 U.S. 391"), "")

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
