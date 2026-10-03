"""A citation whose case has only CourtListener's text, or that several cases
share.

"Pleasants v Pleasants, 2 Va 319" opened Pleasant Grove City v. Summum: no
search hit bore the citation, and the name check, matching both of the
query's "Pleasants" to the "Pleasant" of "Pleasant Grove City", took Summum
for the case — so CourtListener's text of the real one, the last resort, was
never reached.  And "2 Va 319" alone said "No case found", though
CourtListener has three cases at that citation (it files the Virginia
nominatives under "Va."): Pleasants v. Pleasants, Commonwealth v. Carter and
Swope v. Chambers.  The reader is now asked which, in a window like the
English Reports' for a page several cases share.
"""

import ast
import unittest
from pathlib import Path
from unittest import mock

try:
    import tkinter as tk

    import courtlistener_gui as gui
    from google_scholar import ScholarResult
except ImportError:  # pragma: no cover - exercised on a bare checkout
    gui = None

SRC = Path(__file__).with_name("courtlistener_gui.py").read_text(
    encoding="utf-8")

SUMMUM = "Pleasant Grove City, Utah v. Summum"


def _cluster(name: str, cid: int, filed: str = "") -> dict:
    return {"case_name": name, "id": cid, "date_filed": filed,
            "citations": [{"volume": 2, "reporter": "Va.", "page": 319}]}


# CourtListener's answer for "2 Va. 319": six records of three cases.
VA_319 = [{"status": 300, "citation": "2 Va. 319", "clusters": [
    _cluster("Commonwealth v. Carter", 7736613, "1822-11-15"),
    _cluster("Commonwealth v. Carter", 7736612, "1822-11-15"),
    _cluster("Commonwealth v. Carter", 7736611, "1822-11-15"),
    _cluster("Pleasants v. Pleasants", 7730721, "1800-04-15"),
    _cluster("Pleasants v. Pleasants", 7730722, "1800-04-15"),
    _cluster("Swope v. Chambers", 6907096, "1845-07-15"),
]}]

THREE = [{"name": "Pleasants v. Pleasants", "year": "1800"},
         {"name": "Commonwealth v. Carter", "year": "1822"},
         {"name": "Swope v. Chambers", "year": "1845"}]


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


MASS_CARTER = ScholarResult(
    "Commonwealth v. Carter",
    "https://scholar.google.com/scholar_case?case=497949744644382689",
    "429 Mass. 266, 708 NE 2d 943 - Mass: Supreme Judicial Court, 1999 - "
    "Google Scholar") if gui else None


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class SameNameAnotherCaseTests(unittest.TestCase):
    """A hit found by its name alone is the case only if it bears another
    of the case's citations, or is of its year from its state's courts:
    Commonwealth v. Carter at "2 Va. 319" (Virginia, 1822) is no
    Commonwealth v. Carter, 429 Mass. 266 (1999)."""

    VA = (["2 Va 319", "2 Va. 319"], "1822", "virginia")

    def test_the_state_a_citation_or_a_byline_names(self):
        self.assertEqual(gui._cite_state("2 Va 319"), "virginia")
        self.assertEqual(gui._cite_state("100 W. Va. 1"), "west virginia")
        self.assertEqual(gui._cite_state("10 Mont. 5"), "montana")
        self.assertEqual(gui._cite_state("12 N.Y.S.2d 345"), "new york")
        self.assertEqual(gui._cite_state("46 Wn.2d 197"), "washington")
        for regional in ("543 P.3d 440", "708 NE 2d 943", "2 Call 319",
                         "410 U.S. 113"):
            self.assertEqual(gui._cite_state(regional), "", regional)
        self.assertEqual(gui._scholar_result_state(MASS_CARTER),
                         "massachusetts")
        self.assertEqual(gui._scholar_result_state(ScholarResult(
            "Barnette", "u", "319 US 624 - Supreme Court, 1943")), "")

    def test_the_same_name_from_another_state_and_year_is_not_it(self):
        self.assertFalse(gui._named_hit_is_the_case(
            MASS_CARTER, "Commonwealth v. Carter", *self.VA))

    def test_nor_from_the_same_state_another_year(self):
        later = ScholarResult("Commonwealth v. Carter", "u",
                              "100 Va. 1 - Va: Supreme Court, 1902")
        self.assertFalse(gui._named_hit_is_the_case(
            later, "Commonwealth v. Carter", *self.VA))

    def test_its_year_from_its_state_is_it(self):
        call = ScholarResult("Commonwealth v. Carter", "u",
                             "4 Va. 319 - Va: General Court, 1822")
        self.assertTrue(gui._named_hit_is_the_case(
            call, "Commonwealth v. Carter", *self.VA))

    def test_so_is_one_bearing_another_of_its_citations(self):
        parallel = ScholarResult("Commonwealth v. Carter", "u",
                                 "4 Va. 319 - Google Scholar")
        self.assertTrue(gui._named_hit_is_the_case(
            parallel, "Commonwealth v. Carter",
            ["2 Va. 319", "4 Va. 319"], "", ""))

    def test_a_year_or_state_unknown_proves_nothing(self):
        call = ScholarResult("Commonwealth v. Carter", "u",
                             "Va: General Court, 1822")
        self.assertFalse(gui._named_hit_is_the_case(
            call, "Commonwealth v. Carter", ["2 Call 319"], "1822", ""))

    def test_courtlistener_s_text_opens_instead(self):
        fetcher = mock.Mock()
        fetcher.fetch_by_citation.return_value = None
        fetcher.search_cases.return_value = [MASS_CARTER]
        fetcher.pick_cited_result.return_value = None
        fetcher.take_post_search_failure.return_value = ""
        target = {"caseName": "Commonwealth v. Carter", "cluster_id": 7736613,
                  "citation": ["2 Va. 319"], "dateFiled": "1822-11-15"}
        app = object.__new__(gui.CourtListenerGUI)
        app.root = object()
        app._post_root = mock.Mock()
        with mock.patch.object(gui, "_case_law_text_source",
                               return_value=None), \
                mock.patch.object(gui, "_cl_item_for_citation",
                                  return_value=target) as lookup, \
                mock.patch.object(gui, "_assemble_case_parts",
                                  return_value=([], [], "WHITE, J;", {})):
            self.assertTrue(app._try_open_citation(
                "Commonwealth v. Carter", "2 Va 319", "", fetcher, object()))
        fetcher.fetch_by_url.assert_not_called()
        lookup.assert_called_once()          # asked once, used twice
        (open_cl,), _kw = app._post_root.call_args
        with mock.patch.object(gui, "_ScholarTextWindow") as window:
            open_cl()
        self.assertIs(window.call_args.kwargs["item"], target)


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
        self.assertEqual(gui._cases_bearing_citation(client, "2 Va 319"),
                         THREE)

    def test_one_case_is_no_choice(self):
        client = mock.Mock()
        client.lookup_citation.return_value = [{"status": 200, "clusters": [
            _cluster("Pleasants v. Pleasants", 1, "1800-04-15"),
            _cluster("Pleasants v. Pleasants", 2, "1800-04-15")]}]
        self.assertEqual(gui._cases_bearing_citation(client, "2 Va. 319"),
                         [{"name": "Pleasants v. Pleasants", "year": "1800"}])

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


class _NowThread:
    """threading.Thread, run where it is started."""

    def __init__(self, target=None, daemon=None, **_kw):
        self._target = target

    def start(self):
        self._target()


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class WhichCaseTests(unittest.TestCase):
    """A citation several cases share asks which, in a window of its own."""

    def _app(self):
        win = object.__new__(gui.CourtListenerGUI)
        win.root = object()
        win._post_root = mock.Mock()
        win._status_var = mock.Mock()
        win.open_cited_case_pdf = mock.Mock(return_value=False)  # no scan
        win._try_open_citation = mock.Mock(return_value=False)  # no text
        return win

    def _open(self, app, name="", choose=True):
        client = mock.Mock()
        with mock.patch.object(gui, "_cases_bearing_citation",
                               return_value=THREE), \
                mock.patch.object(gui.threading, "Thread", _NowThread):
            app._open_typed_case_citation("2 Va 319", name, "2 Va 319", "",
                                          "", None, client, choose=choose)
        return client

    def test_nothing_opening_at_a_shared_citation_asks_which(self):
        app = self._app()
        client = self._open(app)
        app._post_root.assert_called_once_with(
            app._ask_which_cited_case, "2 Va 319", "2 Va 319", "", THREE,
            None, client)

    def test_the_case_picked_opens_by_its_name_and_year(self):
        app = self._app()
        app._open_typed_case_citation = mock.Mock()
        with mock.patch.object(gui, "_choose_cited_case",
                               return_value=THREE[0]) as choose:
            app._ask_which_cited_case("2 Va 319", "2 Va 319", "", THREE,
                                      "fetcher", "client")
        choose.assert_called_once_with(app.root, "2 Va 319", THREE,
                                       bring_to_front=app._bring_to_front)
        app._open_typed_case_citation.assert_called_once_with(
            "2 Va 319", "Pleasants v. Pleasants", "2 Va 319", "", "1800",
            "fetcher", "client", choose=False)

    def test_cancelling_opens_nothing(self):
        app = self._app()
        app._open_typed_case_citation = mock.Mock()
        with mock.patch.object(gui, "_choose_cited_case", return_value=None):
            app._ask_which_cited_case("2 Va 319", "2 Va 319", "", THREE,
                                      None, None)
        app._open_typed_case_citation.assert_not_called()

    def test_asked_once_a_miss_is_a_miss(self):
        # The case picked that cannot be opened either is not asked about
        # again.
        app = self._app()
        self._open(app, name="Pleasants v. Pleasants", choose=False)
        (fn, message), _kw = app._post_root.call_args
        self.assertEqual(fn, app._notify_lookup_miss)
        self.assertEqual(message,
                         "No case found for Pleasants v. Pleasants, 2 Va 319.")

    def test_the_window_lists_them_and_opens_the_one_picked(self):
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        root.withdraw()
        try:
            def pick_second():
                dlg = next(w for w in root.winfo_children()
                           if isinstance(w, tk.Toplevel))
                self.assertEqual(dlg.title(), "2 Va 319 — 3 cases")
                lb = next(w for w in _descendants(dlg)
                          if isinstance(w, tk.Listbox))
                self.assertEqual(lb.get(0, "end"), (
                    "Pleasants v. Pleasants  ·  1800",
                    "Commonwealth v. Carter  ·  1822",
                    "Swope v. Chambers  ·  1845"))
                lb.selection_clear(0, "end")
                lb.selection_set(1)
                dlg.event_generate("<Return>")

            root.after(200, pick_second)
            self.assertEqual(gui._choose_cited_case(root, "2 Va 319", THREE),
                             THREE[1])
        finally:
            root.destroy()


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


class DropdownTests(unittest.TestCase):

    def test_the_dropdown_takes_every_case_at_the_citation(self):
        tree = ast.parse(SRC)
        body = next(n.body for n in tree.body
                    if isinstance(n, ast.ClassDef)
                    and n.name == "CourtListenerGUI")
        node = next(n for n in body if isinstance(n, ast.FunctionDef)
                    and n.name == "_show_spotlight_dropdown")
        src = ast.get_source_segment(SRC, node)
        self.assertIn('entry.get("status") not in (200, 300)', src)
        self.assertIn("_scholar_bears_citation(r, c)", src)


if __name__ == "__main__":
    unittest.main()
