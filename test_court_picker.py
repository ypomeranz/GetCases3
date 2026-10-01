"""The court picker's district courts, grouped by state.

They were one alphabetical list of ninety-four Bluebook abbreviations —
"C.D. Cal." ahead of "D. Alaska", a state's districts scattered through it —
so finding a state's courts meant reading the whole list.  Each state (and
territory) now has a group of its own, named as the State branch names it,
which ticks all its districts at a click.
"""

import tkinter as tk
import unittest

import court_catalog as cc


def district_groups():
    federal = dict(cc.CATALOG)["Federal"]
    return dict(n for n in federal if isinstance(n[1], list))[
        "District Courts"]


class CatalogTests(unittest.TestCase):
    def test_a_group_to_each_state_in_alphabetical_order(self):
        names = [state for state, _courts in district_groups()]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), 55)    # 50 states, D.C., 4 territories
        for state, courts in district_groups():
            for cid, label in courts:
                self.assertIsInstance(label, str, (state, cid))

    def test_every_district_court_under_its_state_once(self):
        filed = [cid for _state, courts in district_groups()
                 for cid, _abbr in courts]
        self.assertCountEqual(filed, cc.DISTRICT_COURTS)
        self.assertNotIn("Other", dict(district_groups()))

    def test_a_state_s_districts_together(self):
        groups = dict(district_groups())
        self.assertEqual([abbr for _cid, abbr in groups["New York"]],
                         ["E.D.N.Y.", "N.D.N.Y.", "S.D.N.Y.", "W.D.N.Y."])
        self.assertEqual([cid for cid, _abbr in groups["Alabama"]],
                         ["almd", "alnd", "alsd"])
        self.assertEqual(groups["District of Columbia"], [("dcd", "D.D.C.")])
        self.assertEqual(groups["Northern Mariana Islands"],
                         [("nmid", "D.N. Mar. I.")])

    def test_named_as_the_state_branch_names_them(self):
        states = {state for state, _courts in cc.STATE_COURTS}
        territories = {"Guam", "Northern Mariana Islands", "Puerto Rico",
                       "Virgin Islands"}
        self.assertEqual({state for state, _c in district_groups()},
                         states | territories)

    def test_the_state_an_abbreviation_names(self):
        for abbr, state in (("M.D. Ala.", "Alabama"),
                            ("S.D.N.Y.", "New York"),
                            ("D.D.C.", "District of Columbia"),
                            ("D.N.D.", "North Dakota"),
                            ("N.D. W. Va.", "West Virginia"),
                            ("D. Haw.", "Hawaii"),
                            ("D.P.R.", "Puerto Rico"),
                            ("D. Guam", "Guam"),
                            ("Ct. Int'l Trade", "")):
            self.assertEqual(cc._district_state(abbr), state, abbr)

    def test_the_picker_offers_the_same_courts(self):
        self.assertTrue(set(cc.DISTRICT_COURTS) <= cc.all_court_ids())
        self.assertEqual(len(cc.all_court_ids()), 217)


class PickerTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_a_state_s_group_ticks_its_districts(self):
        from courtlistener_gui import _CourtPickerDialog
        dlg = _CourtPickerDialog(self.root, set(), lambda _sel: None)
        tree = dlg._tree

        def child(parent, label):
            return next(iid for iid in tree.get_children(parent)
                        if dlg._labels.get(iid) == label)

        districts = child(child("", "Federal"), "District Courts")
        texas = child(districts, "Texas")
        self.assertEqual(dlg._group_leaves[texas],
                         {"txed", "txnd", "txsd", "txwd"})
        # Texas's own courts are in the State branch, apart from these.
        state_texas = child(child("", "State"), "Texas")
        self.assertFalse(dlg._group_leaves[state_texas]
                         & dlg._group_leaves[texas])
        dlg._win.destroy()


if __name__ == "__main__":
    unittest.main()
