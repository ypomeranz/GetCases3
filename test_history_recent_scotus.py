"""The Court's latest opinions at the foot of every History menu.

Under a line below the cases viewed last, each History menu — the main
window's, a case window's, the viewer strip's icon and its right-click
"Recent" — lists the Court's latest opinions and then its latest opinions
relating to orders (the Recent SCOTUS side panel's two lists), each by case
name and date, opening in the viewer every Supreme Court opinion opens in.
The list is read off the Tk thread and kept on the app: a menu never waits
for supremecourt.gov.
"""

import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import courtlistener_gui
import scotus_recent
from courtlistener_gui import (
    CourtListenerGUI,
    _menu_window,
    _recent_scotus_menu_label,
    _recent_scotus_menu_rows,
)


class FakeMenu(tk.Menu):
    """A menu that records its entries instead of drawing them (no display
    needed): ("separator",) or (label, state, command)."""

    def __init__(self, master=None):  # noqa: D401 - no Tk underneath
        self.master = master
        self.entries = []
        self.posted = []

    def delete(self, *_args):
        self.entries.clear()

    def add_separator(self, **_kw):
        self.entries.append(("separator",))

    def add_command(self, label="", command=None, state="normal", **_kw):
        self.entries.append((label, state, command))

    def tk_popup(self, x, y, *_args):
        self.posted.append((x, y))

    def grab_release(self):
        pass

    def labels(self):
        return [e[0] for e in self.entries]


class FakeWindow:
    def __init__(self):
        self.alive = True

    def winfo_exists(self):
        return self.alive


def decision(name, date="June 30, 2026", url="https://x/op.pdf", docket="24-1"):
    return scotus_recent.RecentDecision(
        name=name, docket=docket, date=date, description="", opinion_url=url)


def merits(name, date="2026-06-29", url="https://x/m.pdf", citation="609/2"):
    return scotus_recent.TermOpinion(
        term="25", date=date, docket="25-332", name=name, author="R",
        opinion_url=url, citation=citation)


def order(name, date="2026-09-14", url="https://x/o.pdf#page=2"):
    return scotus_recent.OrderOpinion(
        name=name, docket="26A305", date=date, authors=["BK"],
        opinion_url=url, citation="609/2")


def app(rows=None, history=()):
    """Just enough of CourtListenerGUI for the History menu's methods."""
    gui = SimpleNamespace(
        _case_history=[{"key": k, "label": k, "reopen": Mock()} for k in history],
        _recent_scotus_rows=rows, _recent_scotus_at=0.0,
        _recent_scotus_loading=False,
        root=SimpleNamespace(after_idle=Mock()),
        open_supreme_court_pdf=Mock(),
        _RECENT_SCOTUS_MENU_TTL=CourtListenerGUI._RECENT_SCOTUS_MENU_TTL,
        _RECENT_SCOTUS_MENU_RETRY=CourtListenerGUI._RECENT_SCOTUS_MENU_RETRY,
    )
    gui._post_root = lambda fn, *args: fn(*args)
    for name in ("populate_history_menu", "post_history_menu",
                 "_add_recent_scotus_to_menu", "_open_recent_scotus_row",
                 "_refresh_recent_scotus_menu", "_recent_scotus_menu_read"):
        setattr(gui, name, getattr(CourtListenerGUI, name).__get__(gui))
    return gui


class Now:
    """threading.Thread, run at once."""

    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


class MenuRowsTests(unittest.TestCase):
    def test_the_homepages_decisions_lead_when_there_are_any(self):
        opinions, orders = _recent_scotus_menu_rows(
            [decision("NRSC v. FEC"), decision("No PDF v. Yet", url="")],
            [merits("Trump v. Slaughter")], [order("Postal Service v. California")])

        # …and the Term's opinions that fill out the list follow them.
        self.assertEqual([(r["name"], r["date"]) for r in opinions],
                         [("NRSC v. FEC", "June 30, 2026"),
                          ("Trump v. Slaughter", "June 29, 2026")])
        self.assertEqual(opinions[0]["decided"], "2026-06-30")
        self.assertEqual(opinions[0]["writing"], "merits")
        self.assertEqual([(r["name"], r["date"], r["writing"]) for r in orders],
                         [("Postal Service v. California", "September 14, 2026",
                           "order")])
        self.assertEqual(orders[0]["url"], "https://x/o.pdf#page=2")

    def test_otherwise_the_terms_latest_opinions(self):
        opinions, _orders = _recent_scotus_menu_rows(
            [], [merits("Trump v. Slaughter"),
                 merits("People Not Politicians v. Onder", url="")], [])

        self.assertEqual(opinions, [{
            "name": "Trump v. Slaughter", "date": "June 29, 2026",
            "url": "https://x/m.pdf", "docket": "25-332",
            "decided": "2026-06-29", "citation": "609/2", "writing": "merits"}])

    def test_ten_opinions_and_five_orders_at_most(self):
        opinions, orders = _recent_scotus_menu_rows(
            [], [merits(f"Case {i}") for i in range(14)],
            [order(f"Order {i}") for i in range(8)])
        self.assertEqual((len(opinions), len(orders)), (10, 5))
        self.assertEqual(opinions[0]["name"], "Case 0")


class MenuLabelTests(unittest.TestCase):
    def test_case_name_and_date(self):
        self.assertEqual(_recent_scotus_menu_label("Trump v. Slaughter",
                                                   "June 29, 2026"),
                         "Trump v. Slaughter — June 29, 2026")

    def test_a_long_name_gives_way_the_date_does_not(self):
        name = "National Republican Senatorial Committee v. Federal Election Commission et al."
        label = _recent_scotus_menu_label(name, "June 30, 2026")
        self.assertLessEqual(len(label), 72)
        self.assertTrue(label.endswith("… — June 30, 2026"))
        self.assertTrue(label.startswith("National Republican Senatorial"))


class MenuWindowTests(unittest.TestCase):
    def test_a_cascade_belongs_to_the_window_its_bar_hangs_from(self):
        window = FakeWindow()
        bar = FakeMenu(window)
        self.assertIs(_menu_window(FakeMenu(bar)), window)
        self.assertIs(_menu_window(FakeMenu(window)), window)


class HistoryMenuTests(unittest.TestCase):
    ROWS = _recent_scotus_menu_rows(
        [], [merits("Trump v. Slaughter")], [order("Postal Service v. California")])

    def test_under_a_line_below_the_cases_viewed_last(self):
        gui = app(self.ROWS, history=["Roe v. Wade, 410 U.S. 113 (1973)"])
        menu = FakeMenu(FakeWindow())
        gui.populate_history_menu(menu)

        self.assertEqual(menu.labels(), [
            "Roe v. Wade, 410 U.S. 113 (1973)",
            "separator",
            "Recent Supreme Court opinions",
            "Trump v. Slaughter — June 29, 2026",
            "Opinions relating to orders",
            "Postal Service v. California — September 14, 2026",
        ])
        # The headings are headings, not things to click.
        states = {e[0]: e[1] for e in menu.entries if len(e) == 3}
        self.assertEqual(states["Recent Supreme Court opinions"], "disabled")
        self.assertEqual(states["Opinions relating to orders"], "disabled")
        self.assertEqual(states["Trump v. Slaughter — June 29, 2026"], "normal")

    def test_an_entry_opens_in_the_supreme_court_viewer_beside_its_window(self):
        gui = app(self.ROWS)
        window = FakeWindow()
        menu = FakeMenu(FakeMenu(window))       # a cascade of a menu bar
        gui.populate_history_menu(menu)
        command = next(e[2] for e in menu.entries
                       if e[0].startswith("Postal Service"))
        command()

        gui.open_supreme_court_pdf.assert_called_once_with(
            window, "https://x/o.pdf#page=2", "Postal Service v. California",
            citation="609/2", docket="26A305", decided="2026-09-14",
            writing="order")

    def test_a_window_closed_since_opens_it_from_the_main_window(self):
        gui = app(self.ROWS)
        window = FakeWindow()
        menu = FakeMenu(window)
        gui.populate_history_menu(menu)
        window.alive = False
        next(e[2] for e in menu.entries if e[0].startswith("Trump"))()
        self.assertIs(gui.open_supreme_court_pdf.call_args.args[0], gui.root)

    def test_before_the_first_read_it_says_it_is_loading(self):
        gui = app(None)
        menu = FakeMenu(FakeWindow())
        gui.populate_history_menu(menu)
        self.assertEqual(menu.labels()[-2:], [
            "separator", "Loading recent Supreme Court opinions…"])
        # The read is started from the event loop, never from the menu.
        gui.root.after_idle.assert_called_once_with(gui._refresh_recent_scotus_menu)

    def test_nothing_found_says_so(self):
        gui = app(([], []))
        menu = FakeMenu(FakeWindow())
        gui.populate_history_menu(menu)
        self.assertEqual(menu.labels()[-1],
                         "No recent Supreme Court opinions could be loaded")

    def test_the_history_button_drops_the_same_menu(self):
        gui = app(self.ROWS, history=["Roe v. Wade"])
        button = SimpleNamespace(winfo_rootx=lambda: 10, winfo_rooty=lambda: 20,
                                 winfo_height=lambda: 5)
        made = []

        def menu(master, tearoff=0):
            made.append(FakeMenu(master))
            return made[-1]

        with patch.object(courtlistener_gui.tk, "Menu", side_effect=menu):
            gui.post_history_menu(button)
        self.assertEqual(made[0].posted, [(10, 25)])
        self.assertIn("Recent Supreme Court opinions", made[0].labels())


class RefreshTests(unittest.TestCase):
    def read(self, gui, found):
        with patch("courtlistener_gui.threading.Thread", Now), \
                patch("courtlistener_gui._fetch_recent_scotus",
                      return_value=found) as fetch:
            gui._refresh_recent_scotus_menu()
        return fetch

    def test_a_read_is_kept_and_not_repeated_while_fresh(self):
        gui = app(None)
        fetch = self.read(gui, ([decision("NRSC v. FEC")], [], []))
        fetch.assert_called_once()
        self.assertEqual(gui._recent_scotus_rows[0][0]["name"], "NRSC v. FEC")
        self.assertFalse(gui._recent_scotus_loading)
        self.read(gui, ([], [], [])).assert_not_called()

    def test_a_stale_list_is_read_again(self):
        gui = app(None)
        self.read(gui, ([decision("NRSC v. FEC")], [], []))
        gui._recent_scotus_at -= CourtListenerGUI._RECENT_SCOTUS_MENU_TTL + 1
        self.read(gui, ([decision("Trump v. Barbara")], [], [])).assert_called_once()
        self.assertEqual(gui._recent_scotus_rows[0][0]["name"], "Trump v. Barbara")

    def test_finding_nothing_keeps_the_last_list(self):
        gui = app(None)
        self.read(gui, ([decision("NRSC v. FEC")], [], []))
        gui._recent_scotus_at = 1.0
        self.read(gui, ([], [], []))
        self.assertEqual(gui._recent_scotus_rows[0][0]["name"], "NRSC v. FEC")

    def test_after_finding_nothing_it_tries_again_sooner(self):
        gui = app(None)
        self.read(gui, ([], [], []))
        self.assertEqual(gui._recent_scotus_rows, ([], []))
        gui._recent_scotus_at -= CourtListenerGUI._RECENT_SCOTUS_MENU_RETRY + 1
        self.read(gui, ([], [], [])).assert_called_once()

    def test_a_read_under_way_is_not_started_twice(self):
        gui = app(None)
        gui._recent_scotus_loading = True
        self.read(gui, ([], [], [])).assert_not_called()

    def test_a_failed_read_is_nothing_found(self):
        gui = app(None)
        with patch("courtlistener_gui.threading.Thread", Now), \
                patch("courtlistener_gui._fetch_recent_scotus",
                      side_effect=OSError("offline")):
            gui._refresh_recent_scotus_menu()
        self.assertEqual(gui._recent_scotus_rows, ([], []))
        self.assertFalse(gui._recent_scotus_loading)


class SharedFetchTests(unittest.TestCase):
    def test_the_side_panel_and_the_menus_read_the_same_lists(self):
        with patch("scotus_recent.fetch_recent_decisions", return_value=[]), \
                patch("scotus_recent.recent_merits_opinions",
                      return_value=["m"]) as merits_fetch, \
                patch("scotus_recent.recent_order_opinions",
                      return_value=["o"]) as orders_fetch:
            self.assertEqual(courtlistener_gui._fetch_recent_scotus(),
                             ([], ["m"], ["o"]))
        merits_fetch.assert_called_once_with(10)
        orders_fetch.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
