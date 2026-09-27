"""Spotlight leads a Supreme Court case with its merits opinion.

A caption search for a Supreme Court case brings back a family of writings
under that caption — the merits opinion, and the orders issued along the way:
the grant of certiorari, a stay (sometimes with a dissent), argument and
briefing motions, a recall of the judgment.  Google Scholar lists them in its
own relevance order, and neither that order nor Scholar's citation counts put
the merits opinion first reliably: a stay with a dissent can be cited far
more than a merits opinion only months old.  The fixtures below are Scholar's
own rows for these cases (September 2026).

The saved-opinion database and CourtListener answer too, often before Scholar,
and a Spotlight row never moves once it is shown; their Supreme Court rows are
held until Scholar's merits-first page is up.
"""

import ast
import pathlib
import tempfile
import unittest
from types import SimpleNamespace

from courtlistener_gui import (
    _cl_name_search,
    _looks_like_scotus_order,
    _rank_scholar_spotlight_results,
    _saved_merits_first,
    _SAVED_SCOTUS_OPINION_BYTES,
    _SCOTUS_COURT_ID,
    _SPOTLIGHT_SCOTUS_HOLD_MS,
)
from google_scholar import ScholarResult


def row(title, source, snippet, cited_by=0, case="1"):
    return ScholarResult(
        title=title,
        url=f"https://scholar.google.com/scholar_case?case={case}",
        source=f"{source} - Google Scholar",
        snippet=snippet,
        cited_by=cited_by,
    )


def ids(results):
    return [r.url.rsplit("=", 1)[1] for r in results]


SLAUGHTER_STAY = (
    "On top of granting certiorari before judgment in this case, the Court "
    "today issues a stay enabling the President to immediately discharge, "
    "without any cause, a member of the Federal Trade Commission.")
SLAUGHTER_MERITS = (
    "Soon after President Trump began his second term in January 2025, he "
    "fired the FTC's two Democratic appointees, Rebecca Slaughter and "
    "Alvaro Bedoya. He did not")
WOLFORD_MERITS = (
    "For years, the State of Hawaii made it almost impossible to obtain a "
    "license to carry a firearm. Four years ago, however, this Court held in "
    "New York State Rifle & Pistol Assn., Inc …")
PUNG_MERITS = (
    "The Pung family owed $2,241.93 in real-property taxes, so local tax "
    "authorities in Isabella County, Michigan, initiated foreclosure "
    "proceedings and sold the Pung home")


class OrderSnippetTests(unittest.TestCase):
    """What gives an order away in a Scholar snippet."""

    ORDERS = [
        # The Supreme Court Reporter's heading: a bare date, no "Argued".
        "145 S.Ct. 434 (2024). LOUISIANA, Appellant, v. Phillip CALLAIS, et "
        "al. No. 24-109. Supreme Court of United States. November 4, 2024. "
        "Appeals from the United States District Court for …",
        "LOUISIANA, v. PHILLIP CALLAIS, ET AL. Nos. 24-109, 24-110. Supreme "
        "Court of the United States. May 6, 2026. The motion …",
        "146 S.Ct. 1438 (2026). Jason WOLFORD, et al., Petitioners, v. Anne "
        "E. LOPEZ. No. 24-1046. Supreme Court of United States. January …",
        # The order's own words.
        "IT IS ORDERED that the July 17, 2025 order of the United States "
        "District Court for the District of Columbia is hereby stayed",
        "Motion of Press Robinson, et al. to strike supplemental brief of "
        "Nancy Landry, Secretary of State of Louisiana denied.",
        "The motion of Press Robinson, et al. to strike the supplemental "
        "brief of Nancy Landry is denied.",
        "The parties is directed to file supplemental briefs addressing the "
        "following question",
        "Donald J. TRUMP, Applicant, v. UNITED STATES … Application for stay "
        "presented to The Chief Justice referred by him to the Court.",
        "Petition for writ of certiorari denied.",
        "The judgment is vacated, and the case is remanded for further "
        "consideration in light of Loper Bright Enterprises v. Raimondo.",
        "JUSTICE SOTOMAYOR, dissenting from the denial of certiorari.",
        # A dissent from an order.
        SLAUGHTER_STAY,
        "I respectfully dissent from the Court's decision to set these "
        "consolidated cases for reargument.",
    ]

    MERITS = [
        SLAUGHTER_MERITS,
        WOLFORD_MERITS,
        PUNG_MERITS,
        "1139 The parties originally briefed and argued this suit last Term, "
        "and their arguments at that time highlighted problems in the "
        "existing body of § 2 case law.",
        "Plaintiffs (respondents here)—individuals, organizations, and "
        "States—filed three separate suits to enjoin the implementation",
        # The Reporter's heading of an opinion: "Argued … Decided …".
        "144 S.Ct. 1889 (2024). UNITED STATES, Petitioner v. Zackey RAHIMI. "
        "No. 22-915. Supreme Court of United States. Argued November 7, "
        "2023. Decided June 21, 2024.",
        "No. 23-6573. Supreme Court of United States. Decided January 21, "
        "2025.",
        # A per curiam is an opinion, whatever else its snippet says.
        "PER CURIAM. The application for stay is granted.",
        "",
    ]

    def test_orders(self):
        for snippet in self.ORDERS:
            with self.subTest(snippet=snippet[:50]):
                self.assertTrue(_looks_like_scotus_order(snippet))

    def test_opinions(self):
        for snippet in self.MERITS:
            with self.subTest(snippet=snippet[:50]):
                self.assertFalse(_looks_like_scotus_order(snippet))


class ScholarMeritsFirstTests(unittest.TestCase):
    def test_a_recent_merits_opinion_beats_a_more_cited_stay_of_an_earlier_year(self):
        # The old ranking compared citation counts within one year only, so
        # the 2025 stay (with its dissent, cited 214 times) led the 2026
        # merits opinion (29).
        results = [
            row("Trump v. Slaughter",
                "146 S. Ct. 18, 222 L. Ed. 2d 1233 - Supreme Court, 2025",
                SLAUGHTER_STAY, 214, "stay"),
            row("Trump v. Slaughter", "Supreme Court, 2025",
                "IT IS ORDERED that the July 17, 2025 order of the United "
                "States District Court is hereby stayed", 0, "order"),
            row("Trump v. Slaughter", "Supreme Court, 2026",
                SLAUGHTER_MERITS, 29, "merits"),
        ]
        ranked = _rank_scholar_spotlight_results(
            "Trump v. Slaughter", results, 8)
        self.assertEqual(ids(ranked), ["merits", "stay", "order"])

    def test_the_merits_opinion_leads_the_decisions_below_it(self):
        results = [
            row("Wolford v. Lopez",
                "116 F. 4th 959 - Court of Appeals, 9th Circuit, 2024",
                "Argued and Submitted April 11, 2024 San Francisco", 101,
                "ca9"),
            row("Wolford v. Lopez",
                "146 S. Ct. 2032, 609 US __ - Supreme Court, 2026",
                WOLFORD_MERITS, 28, "merits"),
            row("Wolford v. Lopez",
                "146 S. Ct. 79, 222 L. Ed. 2d 1241 - Supreme Court, 2025",
                "146 S.Ct. 79 (2025). Jason WOLFORD, et al., petitioners, v. "
                "Anne E. LOPEZ. No. 24-1046. Supreme Court of United States. "
                "October 3, 2025 …", 44, "cert"),
            row("Wolford v. Lopez",
                "686 F. Supp. 3d 1034 - Dist. Court, D. Hawaii, 2023",
                "On June 2, 2023, Hawai`i Governor Josh Green signed", 17,
                "dhaw"),
        ]
        ranked = _rank_scholar_spotlight_results(
            "Wolford v. Lopez", results, 8)
        self.assertEqual(ids(ranked), ["merits", "ca9", "cert", "dhaw"])

    def test_it_leads_them_through_the_captions_abbreviations(self):
        results = [
            row("Bost v. Illinois State Board of Elections",
                "Court of Appeals, 7th Circuit, 2024",
                "In Illinois, voters can cast their ballots by mail", 0,
                "ca7a"),
            row("Bost v. Illinois State Board of Elections",
                "Court of Appeals, 7th Circuit, 2023",
                "Illinois law allows mail-in ballots postmarked on", 0,
                "ca7b"),
            row("Bost v. Illinois State Bd. of Elections",
                "146 S. Ct. 513, 607 US 71, 223 L. Ed. 2d 357 - Supreme "
                "Court, 2026",
                "Here, Congressman Bost has not alleged that Illinois", 103,
                "merits"),
        ]
        ranked = _rank_scholar_spotlight_results(
            "Bost v. Illinois State Board of Elections", results, 8)
        self.assertEqual(ids(ranked), ["merits", "ca7a", "ca7b"])

    def test_a_writing_listed_twice_is_shown_once(self):
        # Scholar lists the slip opinion and the reported one separately.
        results = [
            row("Pung v. ISABELLA COUNTY, MICHIGAN",
                "146 S. Ct. 1964, 609 US __ - Supreme Court, 2026",
                PUNG_MERITS, 12, "reported"),
            row("Pung v. ISABELLA COUNTY", "Supreme Court, 2026",
                PUNG_MERITS, 0, "slip"),
            row("Pung v. ISABELLA COUNTY", "Supreme Court, 2025",
                "MICHAEL PUNG, v. ISABELLA COUNTY, MICHIGAN. No. 25-95. "
                "Supreme Court of the United States. October 3, 2025. The "
                "petition for a writ of certiorari are granted.", 0,
                "cert"),
        ]
        ranked = _rank_scholar_spotlight_results(
            "Pung v. Isabella County", results, 8)
        self.assertEqual(ids(ranked), ["reported", "cert"])

    def test_the_reported_listing_is_the_one_kept(self):
        results = [
            row("Pung v. ISABELLA COUNTY", "Supreme Court, 2026",
                PUNG_MERITS, 0, "slip"),
            row("Pung v. ISABELLA COUNTY, MICHIGAN",
                "146 S. Ct. 1964, 609 US __ - Supreme Court, 2026",
                PUNG_MERITS, 12, "reported"),
        ]
        ranked = _rank_scholar_spotlight_results(
            "Pung v. Isabella County", results, 8)
        self.assertEqual(ids(ranked), ["reported"])

    def test_several_cases_under_one_caption_keep_scholars_order(self):
        # One caption, several cases: the newest writing is not "the" merits
        # opinion, and the 2024 stay writing must not jump the 2023 case.
        results = [
            row("United States v. Texas",
                "599 US 670, 143 S. Ct. 1964 - Supreme Court, 2023",
                "In 2021, the Secretary of Homeland Security promulgated new "
                "immigration-enforcement guidelines", 1208, "2023"),
            row("United States v. Texas",
                "339 US 707, 70 S. Ct. 918 - Supreme Court, 1950",
                "This is a suit by the United States against Texas", 1394,
                "1950"),
            row("United States v. Texas",
                "144 S. Ct. 797, 601 US __ - Supreme Court, 2024",
                "If the Fifth Circuit had issued a stay pending appeal, this "
                "Court would apply the four-factor test", 129, "2024"),
        ]
        ranked = _rank_scholar_spotlight_results(
            "United States v. Texas", results, 8)
        self.assertEqual(ids(ranked), ["2023", "1950", "2024"])

    def test_within_a_year_the_most_cited_writing_still_leads(self):
        # An order's concurrence that no pattern gives away: its year's merits
        # opinion, cited far more, still leads.
        results = [
            row("Bush v. Gore", "531 US 1046 - Supreme Court, 2000",
                "Though it is not customary for the Court to issue an "
                "opinion in connection with its grant of a stay, I believe "
                "a brief response is necessary", 300, "stay"),
            row("Bush v. Gore", "531 US 98, 121 S. Ct. 525 - Supreme Court, "
                "2000", "Florida's Supreme Court ordered a manual recount",
                8408, "merits"),
        ]
        ranked = _rank_scholar_spotlight_results("Bush v. Gore", results, 8)
        self.assertEqual(ids(ranked), ["merits", "stay"])

    def test_an_older_same_caption_decision_is_not_taken_for_the_one_below(self):
        results = [
            row("Smith v. Jones", "256 F. 3d 1135 - Court of Appeals, 11th "
                "Circuit, 2001", "The plaintiff appeals", 734, "ca11"),
            row("Smith v. Jones", "600 US 1 - Supreme Court, 2023",
                "We granted certiorari to decide", 50, "scotus"),
        ]
        ranked = _rank_scholar_spotlight_results("Smith v. Jones", results, 8)
        self.assertEqual(ids(ranked), ["ca11", "scotus"])

    def test_a_case_with_only_orders_is_left_in_scholars_order(self):
        results = [
            row("Doe v. Roe", "Court of Appeals, 9th Circuit, 2025",
                "We review the district court's order", 5, "ca9"),
            row("Doe v. Roe", "Supreme Court, 2026",
                "Petition for writ of certiorari denied.", 0, "denied"),
        ]
        ranked = _rank_scholar_spotlight_results("Doe v. Roe", results, 8)
        self.assertEqual(ids(ranked), ["ca9", "denied"])

    def test_the_limit_applies_after_the_merits_opinion_is_moved_up(self):
        results = [
            row("Trump v. Slaughter", "Supreme Court, 2025",
                "IT IS ORDERED that the order is hereby stayed", 0,
                f"order{i}")
            for i in range(3)
        ] + [row("Trump v. Slaughter", "Supreme Court, 2026",
                 SLAUGHTER_MERITS, 29, "merits")]
        ranked = _rank_scholar_spotlight_results(
            "Trump v. Slaughter", results, 2)
        self.assertEqual(ids(ranked), ["merits", "order0"])


class SavedMeritsFirstTests(unittest.TestCase):
    """The saved-opinion database knows each writing's stored size."""

    @staticmethod
    def hit(sid, name, year, size, court="scotus"):
        return {"scholar_id": sid, "name": name, "court": court,
                "year": year, "size": size}

    def test_a_saved_order_gives_way_to_the_merits_opinion(self):
        hits = [
            self.hit("order", "Pung v. Isabella Cnty., Michigan", "2026", 949),
            self.hit("merits", "Pung v. Isabella Cnty., Michigan", "2026",
                     27786),
            self.hit("other", "Pung v. Kopke", "2025", 16356, court="ca6"),
        ]
        self.assertEqual([h["scholar_id"] for h in _saved_merits_first(hits)],
                         ["merits", "order", "other"])

    def test_two_opinions_of_like_length_are_left_alone(self):
        hits = [
            self.hit("2023", "United States v. Texas", "2023", 60000),
            self.hit("1950", "United States v. Texas", "1950", 90000),
        ]
        self.assertEqual([h["scholar_id"] for h in _saved_merits_first(hits)],
                         ["2023", "1950"])

    def test_unknown_sizes_change_nothing(self):
        hits = [self.hit("a", "Roe v. Wade", "1973", 0),
                self.hit("b", "Roe v. Wade", "1973", 0)]
        self.assertEqual([h["scholar_id"] for h in _saved_merits_first(hits)],
                         ["a", "b"])

    def test_the_database_reports_each_records_size(self):
        from opinion_db import OpinionDB

        html = (
            '<div id="gs_opinion"><center>ROE v. WADE</center>'
            "<center>410 U.S. 113</center>"
            "<center>Supreme Court of United States.</center>"
            f"<p>{'The Court holds. ' * 200}</p></div>"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            db = OpinionDB(root / "opinions.jsonl", root / "opinions.db")
            try:
                db.add_opinion(
                    "https://scholar.google.com/scholar_case?case=1", html)
                hit = db.search_names("Roe v. Wade")[0]
            finally:
                db.close()
        self.assertGreater(hit["size"], 100)


class CourtListenerTiebreakTests(unittest.TestCase):
    def test_a_recent_merits_opinion_outranks_an_order_of_its_case(self):
        # Too recent to have been cited: the opinion citing dozens of cases is
        # the merits opinion; the one citing none is an order.
        def item(cluster, cites):
            return {"caseName": "Louisiana v. Callais", "court_id": "scotus",
                    "citeCount": 0, "cluster_id": cluster,
                    "opinions": [{"cites": list(range(cites))}]}

        client = SimpleNamespace(search=lambda *a, **k: {
            "results": [item("order", 0), item("merits", 43)]})
        found = _cl_name_search(client, "Louisiana v. Callais", "scotus",
                                limit=2)
        self.assertEqual([it["cluster_id"] for it in found],
                         ["merits", "order"])


# ---------------------------------------------------------------------------
# The dropdown: Supreme Court rows from other sources wait for Scholar
# ---------------------------------------------------------------------------

SRC = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text(
    encoding="utf-8")
TREE = ast.parse(SRC)


def _dropdown():
    cls = next(n for n in TREE.body
               if isinstance(n, ast.ClassDef) and n.name == "CourtListenerGUI")
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                and n.name == "_show_spotlight_dropdown")


def _nested(name):
    for node in ast.walk(_dropdown()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"_show_spotlight_dropdown has no {name}")


class HeldRowsTests(unittest.TestCase):
    def setUp(self):
        self.added = []
        self.ns = {
            "scholar_answered": [False],
            "scholar_returned": [False],
            "held_scotus": [],
            "_add_result": lambda *args: self.added.append(args),
            "_SCOTUS_COURT_ID": _SCOTUS_COURT_ID,
        }
        exec(_nested("_add_after_scholar"), self.ns)
        exec(_nested("_release_held_scotus"), self.ns)
        exec(_nested("_release_held_scotus_late"), self.ns)

    def test_the_time_limit_releases_them_while_scholar_is_still_out(self):
        self.ns["_add_after_scholar"]("cl", "scotus", "Trump v. Slaughter")
        self.ns["_release_held_scotus_late"]()
        self.assertEqual(len(self.added), 1)

    def test_but_not_ahead_of_rows_scholar_has_already_sent(self):
        # Scholar's page is in and its rows are being put up; its own thread
        # releases the held rows after them.
        self.ns["_add_after_scholar"]("cl", "scotus", "Trump v. Slaughter")
        self.ns["scholar_returned"][0] = True
        self.ns["_release_held_scotus_late"]()
        self.assertEqual(self.added, [])
        self.ns["_release_held_scotus"]()
        self.assertEqual(len(self.added), 1)

    def test_supreme_court_rows_wait_and_others_do_not(self):
        add = self.ns["_add_after_scholar"]
        add("opiniondb", "scotus", "Chatrie v. United States")
        add("cl", "ca4", "United States v. Chatrie")
        add("scotus", "scotus", "Chatrie v. United States")
        self.assertEqual([a[2] for a in self.added],
                         ["United States v. Chatrie"])

        self.ns["_release_held_scotus"]()
        self.assertEqual([a[0] for a in self.added],
                         ["cl", "opiniondb", "scotus"])

    def test_once_released_rows_go_straight_up(self):
        self.ns["_release_held_scotus"]()
        self.ns["_add_after_scholar"]("cl", "scotus", "Trump v. Slaughter")
        self.assertEqual(len(self.added), 1)

    def test_releasing_twice_adds_nothing_twice(self):
        self.ns["_add_after_scholar"]("cl", "scotus", "Trump v. Slaughter")
        self.ns["_release_held_scotus"]()
        self.ns["_release_held_scotus"]()
        self.assertEqual(len(self.added), 1)


class HoldWiringTests(unittest.TestCase):
    """Read off the dropdown's source, where the closures are wired."""

    def setUp(self):
        self.src = ast.get_source_segment(SRC, _dropdown())

    def test_scholar_releases_them_however_its_search_ends(self):
        search = _nested("scholar_search")
        self.assertIn("finally:", search)
        self.assertLess(search.index("finally:"),
                        search.index("_release_held_scotus"))

    def test_they_are_up_before_the_phrase_fallback_counts_rows(self):
        status = _nested("_update_status")
        self.assertLess(status.index("_release_held_scotus()"),
                        status.index("_phrase_fallback()"))

    def test_a_slow_scholar_does_not_keep_them_off_screen(self):
        self.assertIn(
            "self.root.after(_SPOTLIGHT_SCOTUS_HOLD_MS, "
            "_release_held_scotus_late)", self.src)
        self.assertLessEqual(_SPOTLIGHT_SCOTUS_HOLD_MS, 8000)

    def test_scholar_says_when_its_page_is_in(self):
        search = _nested("search_scholar")
        self.assertLess(search.index("fetcher.search_cases("),
                        search.index("scholar_returned[0] = True"))
        self.assertLess(search.index("scholar_returned[0] = True"),
                        search.index("_rank_scholar_spotlight_results("))

    def test_courtlistener_rows_are_held(self):
        cl = _nested("cl_search")
        self.assertIn("_add_after_scholar", cl)
        self.assertNotIn("_add_result", cl)

    def test_a_saved_merits_opinion_is_shown_at_once(self):
        saved = _nested("opinion_db_search")
        self.assertIn("_SAVED_SCOTUS_OPINION_BYTES", saved)
        self.assertIn("_add_after_scholar", saved)
        self.assertGreater(_SAVED_SCOTUS_OPINION_BYTES, 8_000)


if __name__ == "__main__":
    unittest.main()
