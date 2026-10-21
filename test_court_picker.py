"""The court picker's lower federal courts, grouped by circuit.

They were filed by kind — the courts of appeals in one list, the district
courts in another, grouped by state in alphabetical order — so every court
in one circuit (the Eleventh Circuit's own and the district courts of
Alabama, Florida and Georgia) took a dozen clicks in three places.  Each
circuit now has a group of its own, its court of appeals first, then its
district courts by state in alphabetical order, then the specialized courts
whose appeals go to it alone; a click on the circuit takes in all of them.
"""

import tkinter as tk
import unittest

import court_catalog as cc


def federal():
    return dict(cc.CATALOG)["Federal"]


def circuit_groups():
    """Federal's groups that are circuits, by name."""
    return {name: courts for name, courts in federal()
            if isinstance(courts, list) and name.endswith("Circuit")}


def leaves(nodes):
    """Every court id under *nodes*, in order."""
    out = []
    for key, payload in nodes:
        out += leaves(payload) if isinstance(payload, list) else [key]
    return out


class CatalogTests(unittest.TestCase):
    def test_a_group_to_each_circuit_in_order(self):
        self.assertEqual(
            [name for name, courts in federal() if isinstance(courts, list)],
            ["First Circuit", "Second Circuit", "Third Circuit",
             "Fourth Circuit", "Fifth Circuit", "Sixth Circuit",
             "Seventh Circuit", "Eighth Circuit", "Ninth Circuit",
             "Tenth Circuit", "Eleventh Circuit", "D.C. Circuit",
             "Federal Circuit", "Specialized"])
        self.assertEqual(federal()[0],
                         ("scotus", "Supreme Court of the United States"))

    def test_the_court_of_appeals_leads_its_circuit(self):
        for name, courts in circuit_groups().items():
            cid, label = courts[0]
            self.assertIn(cid, cc.CIRCUIT_COURTS, name)
            self.assertEqual(label,
                             f"Court of Appeals ({cc.CIRCUIT_COURTS[cid]})")

    def test_the_eleventh_circuit_is_one_group(self):
        eleventh = circuit_groups()["Eleventh Circuit"]
        self.assertEqual(
            leaves(eleventh),
            ["ca11", "almd", "alnd", "alsd", "flmd", "flnd", "flsd",
             "gamd", "gand", "gasd"])
        self.assertEqual([state for state, _courts in eleventh[1:]],
                         ["Alabama", "Florida", "Georgia"])

    def test_districts_follow_by_state_in_alphabetical_order(self):
        ninth = circuit_groups()["Ninth Circuit"]
        self.assertEqual(
            [key if isinstance(payload, list) else payload.split(" (")[0]
             for key, payload in ninth[1:-1]],
            ["Alaska", "Arizona", "California", "Guam", "Hawaii", "Idaho",
             "Montana", "Nevada", "Northern Mariana Islands", "Oregon",
             "Washington"])
        # A state of several districts is a group of its own…
        self.assertEqual(dict(ninth[1:-1])["California"],
                         [("cacd", "C.D. Cal."), ("caed", "E.D. Cal."),
                          ("cand", "N.D. Cal."), ("casd", "S.D. Cal.")])
        # …and a state of one is named with its court.
        self.assertIn(("akd", "Alaska (D. Alaska)"), ninth)
        self.assertIn(("dcd", "District of Columbia (D.D.C.)"),
                      circuit_groups()["D.C. Circuit"])

    def test_every_district_court_under_its_own_circuit_once(self):
        filed = []
        for courts in circuit_groups().values():
            circuit = courts[0][0]
            districts = [cid for cid in leaves(courts)
                         if cid in cc.DISTRICT_COURTS]
            for cid in districts:
                self.assertEqual(cc.CIRCUIT_OF_DISTRICT[cid], circuit, cid)
            filed += districts
        self.assertCountEqual(filed, cc.DISTRICT_COURTS)

    def test_specialized_courts_go_with_the_circuit_hearing_their_appeals(self):
        groups = circuit_groups()
        self.assertEqual(groups["First Circuit"][-1],
                         ("bap1", "Bankruptcy Appellate Panel "
                                  "(B.A.P. 1st Cir.)"))
        self.assertEqual(leaves(groups["Federal Circuit"]),
                         ["cafc", "cavet", "uscfc", "cit"])
        self.assertEqual(leaves(dict(federal())["Specialized"]),
                         ["tax", "caaf"])

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
        ids = leaves(cc.CATALOG)
        self.assertEqual(len(ids), len(set(ids)))       # each court once
        self.assertTrue(set(cc.DISTRICT_COURTS) <= cc.all_court_ids())
        self.assertTrue(set(cc.SPECIAL_COURTS) <= cc.all_court_ids())
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

    def test_one_click_takes_in_a_circuit(self):
        from courtlistener_gui import _CourtPickerDialog
        applied = []
        dlg = _CourtPickerDialog(self.root, set(), applied.append)
        tree = dlg._tree

        def child(parent, label):
            return next(iid for iid in tree.get_children(parent)
                        if dlg._labels.get(iid) == label)

        eleventh = child(child("", "Federal"), "Eleventh Circuit")
        # A click on the circuit's row (not its expander).
        tree.identify_element = lambda x, y: "text"
        tree.identify_row = lambda y: eleventh
        dlg._on_click(type("Click", (), {"x": 40, "y": 10})())
        self.assertEqual(tree.item(eleventh, "text"), "☑ Eleventh Circuit")
        dlg._apply()
        self.assertEqual(applied, [{"ca11", "almd", "alnd", "alsd", "flmd",
                                    "flnd", "flsd", "gamd", "gand",
                                    "gasd"}])

    def test_a_state_s_group_ticks_its_districts(self):
        from courtlistener_gui import _CourtPickerDialog
        dlg = _CourtPickerDialog(self.root, set(), lambda _sel: None)
        tree = dlg._tree

        def child(parent, label):
            return next(iid for iid in tree.get_children(parent)
                        if dlg._labels.get(iid) == label)

        texas = child(child(child("", "Federal"), "Fifth Circuit"), "Texas")
        self.assertEqual(dlg._group_leaves[texas],
                         {"txed", "txnd", "txsd", "txwd"})
        # Texas's own courts are in the State branch, apart from these.
        state_texas = child(child("", "State"), "Texas")
        self.assertFalse(dlg._group_leaves[state_texas]
                         & dlg._group_leaves[texas])
        dlg._win.destroy()


if __name__ == "__main__":
    unittest.main()
