"""A case cited by another name than the one its sources give it.

Some cases go by a name no source files them under, or the other way about:
"Knox v. Lee, 79 U.S. 457" is the Legal Tender Cases; "United States v.
Stanley, 109 U.S. 3" the Civil Rights Cases; the Prize Cases are cited as
"The Brig Amy Warwick".  Every source turns away a case that does not answer
to the name a citation came with, so such a citation found nothing, or its
case.law scan opened unasked.  Where the name answers to none of the cases
beginning at the citation and one of the two names is no "A v. B" caption at
all — the shape of a name a case goes by — the reader is told there is no
exact match and shown the case or cases bearing the citation, to open one or
cancel.  Two "A v. B" captions with no party in common are two cases, and a
case decided years from the citation's own year is another: no question.
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import tkinter as tk

    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None

CITE = "79 U.S. 457"
LEGAL_TENDER = [{"name": "Legal Tender Cases", "year": "1871",
                 "url": "u-legal-tender"}]


class _NowThread:
    """threading.Thread, run where it is started."""

    def __init__(self, target=None, daemon=None, args=(), **_kw):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class RuleTests(unittest.TestCase):
    def test_a_name_the_case_goes_by(self):
        for name, held in (("Knox v. Lee", "Legal Tender Cases"),
                           ("United States v. Stanley", "Civil Rights Cases"),
                           ("The Brig Amy Warwick", "The Prize Cases"),
                           ("Steel Seizure Case",
                            "Youngstown Sheet & Tube Co. v. Sawyer")):
            with self.subTest(name=name):
                self.assertTrue(gui._cases_by_another_name(
                    name, [{"name": held, "year": ""}]))

    def test_two_captions_with_no_party_in_common_are_two_cases(self):
        self.assertFalse(gui._cases_by_another_name(
            "Smith v. Jones", [{"name": "Brown v. Green", "year": ""}]))

    def test_a_case_answering_to_the_name_is_no_question(self):
        # One party in common is the case for every source already.
        self.assertFalse(gui._cases_by_another_name(
            "Ashcroft v. Raich", [{"name": "Gonzales v. Raich",
                                   "year": "2005"}]))
        self.assertFalse(gui._cases_by_another_name(
            "Knox v. Lee", [{"name": "Legal Tender Cases", "year": "1871"},
                            {"name": "Knox v. Lee", "year": "1871"}]))

    def test_a_case_of_another_time_is_another_case(self):
        self.assertTrue(gui._cases_by_another_name(
            "Knox v. Lee", LEGAL_TENDER, year="1870"))
        self.assertFalse(gui._cases_by_another_name(
            "Knox v. Lee", LEGAL_TENDER, year="1950"))
        # …nor can an opinion cite a case decided after it.
        self.assertFalse(gui._cases_by_another_name(
            "Knox v. Lee", LEGAL_TENDER, latest="1860"))

    def test_no_name_or_no_case_asks_nothing(self):
        self.assertFalse(gui._cases_by_another_name("", LEGAL_TENDER))
        self.assertFalse(gui._cases_by_another_name("Knox v. Lee", []))


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class AskedTests(unittest.TestCase):
    def _app(self):
        app = object.__new__(gui.CourtListenerGUI)
        app.root = object()
        app._post_root = mock.Mock()
        app._post_case_law_pdf = mock.Mock()
        app._status_var = mock.Mock()
        return app

    def _nothing_opens(self, cases=LEGAL_TENDER):
        """Every source turns the name away; *cases* begin at the cite."""
        self.pdf = mock.Mock(return_value="u-legal-tender")
        for patch in (
                mock.patch.object(gui, "_case_law_text_source",
                                  return_value=None),
                mock.patch.object(gui, "_cl_item_for_citation",
                                  return_value=None),
                mock.patch.object(gui, "_case_law_pdf_for_cite", self.pdf),
                mock.patch.object(gui, "_case_law_page_cases",
                                  return_value=[]),
                mock.patch.object(gui, "_official_series_for",
                                  return_value=[]),
                mock.patch.object(gui, "_cases_beginning_at",
                                  return_value=cases)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_reader_is_asked_before_the_scan_opens_unasked(self):
        self._nothing_opens()
        app = self._app()
        self.assertTrue(app._try_open_citation(
            "Knox v. Lee", CITE, "", None, "client", year="1871"))
        app._post_root.assert_called_once_with(
            app._ask_which_case_at, app.root, CITE, "", LEGAL_TENDER, None,
            "client", True, "Knox v. Lee")
        self.pdf.assert_not_called()

    def test_not_to_ask_is_to_leave_it_to_the_caller(self):
        self._nothing_opens()
        app = self._app()
        self.assertFalse(app._try_open_citation(
            "Knox v. Lee", CITE, "", None, "client", choose=False))
        app._post_root.assert_not_called()
        self.pdf.assert_not_called()

    def test_another_case_at_the_citation_opens_as_before(self):
        self._nothing_opens([{"name": "Brown v. Green", "year": ""}])
        app = self._app()
        self.assertTrue(app._try_open_citation(
            "Smith v. Jones", CITE, "", None, "client"))
        app._post_case_law_pdf.assert_called_once()
        app._post_root.assert_not_called()

    def test_a_citation_typed_into_spotlight(self):
        self._nothing_opens()
        app = self._app()
        app.open_cited_case_pdf = mock.Mock(return_value=False)  # no scan
        app._try_open_citation = mock.Mock(return_value=False)  # no text
        with mock.patch.object(gui.threading, "Thread", _NowThread):
            app._open_typed_case_citation(
                f"Knox v. Lee, {CITE}", "Knox v. Lee", CITE, "", "1871",
                None, "client")
        app._post_root.assert_called_once_with(
            app._ask_which_cited_case, f"Knox v. Lee, {CITE}", CITE, "",
            LEGAL_TENDER, None, "client", "Knox v. Lee")

    def test_a_link_clicked_in_a_case(self):
        self._nothing_opens()
        win = object.__new__(gui._ScholarTextWindow)
        win._app = mock.Mock()
        win._app._get_client.return_value = "client"
        win._status_var = mock.Mock()
        win._post = mock.Mock()
        with mock.patch.object(gui.threading, "Thread", _NowThread):
            win._follow_cite_via_cl(CITE, "", name="Knox v. Lee",
                                    scholar_absent=True)
        win._post.assert_called_once_with(win._ask_cases_at, CITE, "",
                                          LEGAL_TENDER, "Knox v. Lee")
        self.pdf.assert_not_called()

    def test_the_case_picked_opens_by_its_own_name(self):
        app = self._app()
        app._bring_to_front = lambda win: None
        app._try_open_citation = mock.Mock(return_value=True)
        with mock.patch.object(
                gui, "_choose_cited_case",
                side_effect=lambda host, cite, cases, **kw: cases[0]) \
                as choose, \
                mock.patch.object(gui.threading, "Thread", _NowThread):
            app._ask_which_case_at("parent", CITE, "", LEGAL_TENDER, None,
                                   "client", name="Knox v. Lee")
        self.assertEqual(choose.call_args.kwargs["name"], "Knox v. Lee")
        app._try_open_citation.assert_called_once_with(
            "Legal Tender Cases", CITE, "", None, "client",
            prefetch_pdf=True, view_parent="parent", year="1871",
            choose=False)


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class DialogTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def _ask(self, cases, name, key):
        seen = {}

        def answer():
            dlg = next(w for w in self.root.winfo_children()
                       if isinstance(w, tk.Toplevel))
            seen["title"] = dlg.title()
            seen["texts"] = [w.cget("text") for w in _descendants(dlg)
                             if isinstance(w, tk.Label)]
            dlg.event_generate(key)

        self.root.after(200, answer)
        chosen = gui._choose_cited_case(self.root, CITE, cases, name=name)
        return chosen, seen

    def test_no_exact_match_and_the_cases_bearing_the_citation(self):
        chosen, seen = self._ask(LEGAL_TENDER, "Knox v. Lee", "<Return>")
        self.assertEqual(chosen, LEGAL_TENDER[0])
        self.assertEqual(seen["title"], f"{CITE} — no exact match")
        self.assertIn("No exact match for “Knox v. Lee” was found.",
                      seen["texts"])
        self.assertIn(f"The following case has this citation, {CITE}. "
                      "Open one, or cancel:", seen["texts"])

    def test_cancelling_opens_nothing(self):
        chosen, _seen = self._ask(LEGAL_TENDER, "Knox v. Lee", "<Escape>")
        self.assertIsNone(chosen)

    def test_a_name_one_of_them_answers_to_asks_as_before(self):
        cases = LEGAL_TENDER + [{"name": "Knox v. Lee", "year": "1871"}]
        _chosen, seen = self._ask(cases, "Knox v. Lee", "<Escape>")
        self.assertEqual(seen["title"], f"{CITE} — 2 cases")
        self.assertIn(f"2 cases are reported at {CITE}. Pick one:",
                      seen["texts"])


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


if __name__ == "__main__":
    unittest.main()
