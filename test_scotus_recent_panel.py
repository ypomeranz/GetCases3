"""The Recent SCOTUS side panel.

The Court's homepage lists its Recent Decisions only while the Term is
sitting.  When it lists none, the panel shows the latest entries of the Term's
"Opinions of the Court" instead; either way it then shows the latest
"Opinions Relating to Orders", which the Court lists one row per separate
writing, gathered into one entry per order naming the Justices who wrote.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import courtlistener_gui
import scotus_recent
from courtlistener_gui import _ScholarTextWindow, _pdf_link_page_index


# The Term's "Opinions of the Court" as supremecourt.gov lays it out: the
# latest ten in one table, the rest behind "More" in another.  The newest rows
# are listed before the Court has linked their PDFs.
MERITS_PAGE = """
<table><tr><td><input id="search"/></td></tr></table>
<table class="table table-bordered">
  <tr><th>R-</th><th>Date</th><th>Docket</th><th>Name</th><th>J.</th>
      <th>Citation</th></tr>
  <tr><td>73</td><td>9/25/26</td><td>26A388</td>
      <td>People Not Politicians v. Onder</td><td>PC</td>
      <td><span>609/2</span></td></tr>
  <tr><td>66</td><td>6/30/26</td><td>25-365</td>
      <td><a href='/opinions/25pdf/25-365_new_5if6.pdf' target='_blank'
             title="Children born in the United States are citizens.">Trump
          v. Barbara</a></td><td>R</td><td><span>609/2</span></td></tr>
</table>
<table class="table table-bordered">
  <tr><th>R-</th><th>Date</th><th>Docket</th><th>Name</th><th>J.</th>
      <th>Citation</th></tr>
  <tr><td>62</td><td>6/29/26</td><td>25-332</td>
      <td><a href='/opinions/25pdf/25-332_new_geil.pdf'
             title="The FTC's removal provision is unconstitutional.">Trump
          v. Slaughter</a>
          <span>Revisions: <a href='/opinions/25pdf/25-332_rev.pdf'>7/02/26</a></span></td>
      <td>R</td><td><span>609 U.S. 422</span></td></tr>
</table>
"""

# "Opinions Relating to Orders": one row per separate writing.  In a closed
# Term every writing points into the preliminary print's orders section.
ORDERS_PAGE = """
<table class="table table-bordered">
  <tr><th>Date</th><th>Docket</th><th>Name</th><th>J.</th><th>Citation</th></tr>
  <tr><td>9/14/26</td><td>26A305</td>
      <td><a href="/opinions/25pdf/26a305_4g15.pdf">Postal Service v.
          California</a></td><td>BK</td><td>609/2</td></tr>
  <tr><td>9/14/26</td><td>26A305</td>
      <td><a href="/opinions/25pdf/26a305_4g15.pdf#page=2">Postal Service v.
          California</a></td><td>A</td><td>609/2</td></tr>
  <tr><td>6/30/26</td><td>25-524</td>
      <td><a href="/opinions/25pdf/25-524_0971.pdf">Jones v. United
          States</a></td><td>SS</td><td>609/2</td></tr>
  <tr><td>5/14/26</td><td>25A1207</td>
      <td><a href="/opinions/25pdf/25a1207_new_3d9g.pdf">Danco Laboratories,
          LLC v. Louisiana</a></td><td>T</td><td>608/1</td></tr>
  <tr><td>5/14/26</td><td>25A1207</td>
      <td><a href="/opinions/25pdf/25a1207_new_3d9g.pdf#page=3">Danco
          Laboratories, LLC v. Louisiana</a> Revisions : 5/15/26</td>
      <td>A</td><td>608/1</td></tr>
</table>
"""

PRELIMINARY_PRINT_ORDERS = """
<table>
  <tr><th>Date</th><th>Docket</th><th>Name</th><th>J.</th><th>Citation</th></tr>
  <tr><td>8/21/25</td><td>25A103</td>
      <td><a href="/opinions/preliminaryprint/606US2PP_Ord.pdf#page=87">NIH
          v. APHA</a></td><td>KJ</td><td>606 U.S. 1007</td></tr>
  <tr><td>8/21/25</td><td>25A103</td>
      <td><a href="/opinions/preliminaryprint/606US2PP_Ord.pdf#page=85">NIH
          v. APHA</a></td><td>BK</td><td>606 U.S. 1005</td></tr>
  <tr><td>8/21/25</td><td>25A103</td>
      <td><a href="/opinions/preliminaryprint/606US2PP_Ord.pdf#page=79">NIH
          v. APHA</a></td><td>AB</td><td>606 U.S. 999</td></tr>
</table>
"""


def merits(date, name, release="1", author="R", url="u", description=""):
    return scotus_recent.TermOpinion(
        term="25", date=date, docket="25-1", name=name, author=author,
        opinion_url=url, description=description, release=release)


class TermTableParsingTests(unittest.TestCase):
    def test_opinions_of_the_court(self):
        rows = scotus_recent.parse_term_opinions(MERITS_PAGE, "25")

        self.assertEqual(
            [(r.release, r.date, r.docket, r.name, r.author) for r in rows],
            [("73", "2026-09-25", "26A388",
              "People Not Politicians v. Onder", "PC"),
             ("66", "2026-06-30", "25-365", "Trump v. Barbara", "R"),
             ("62", "2026-06-29", "25-332", "Trump v. Slaughter", "R")])
        # A row the Court has not linked yet is kept, without a PDF.
        self.assertEqual(rows[0].opinion_url, "")
        # The holding the Court puts on the link is the description.
        self.assertEqual(
            rows[1].description,
            "Children born in the United States are citizens.")
        self.assertEqual(
            rows[1].opinion_url,
            "https://www.supremecourt.gov/opinions/25pdf/25-365_new_5if6.pdf")
        # The case-name link, not the revision notice; the note taken off.
        self.assertEqual(
            rows[2].opinion_url,
            "https://www.supremecourt.gov/opinions/25pdf/25-332_new_geil.pdf")
        self.assertEqual(rows[2].citation, "609 U.S. 422")

    def test_opinions_relating_to_orders(self):
        rows = scotus_recent.parse_term_opinions(ORDERS_PAGE, "25")

        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[1].opinion_url,
                         "https://www.supremecourt.gov/opinions/25pdf/"
                         "26a305_4g15.pdf#page=2")
        self.assertEqual(rows[4].name, "Danco Laboratories, LLC v. Louisiana")
        self.assertEqual(rows[0].release, "")

    def test_a_page_without_the_table_gives_nothing(self):
        self.assertEqual(
            scotus_recent.parse_term_opinions("<p>Not found</p>", "26"), [])


class OrderGroupingTests(unittest.TestCase):
    def test_each_order_is_listed_once_with_its_writers(self):
        orders = scotus_recent.group_order_opinions(
            scotus_recent.parse_term_opinions(ORDERS_PAGE, "25"))

        self.assertEqual(
            [(o.name, o.date, o.authors) for o in orders],
            [("Postal Service v. California", "2026-09-14", ["BK", "A"]),
             ("Jones v. United States", "2026-06-30", ["SS"]),
             ("Danco Laboratories, LLC v. Louisiana", "2026-05-14",
              ["T", "A"])])
        self.assertEqual(orders[0].opinion_url,
                         "https://www.supremecourt.gov/opinions/25pdf/"
                         "26a305_4g15.pdf")

    def test_writers_in_print_order_opening_at_the_first(self):
        # The Court lists the writings in its own order; the PDF prints them
        # from page 79.
        (order,) = scotus_recent.group_order_opinions(
            scotus_recent.parse_term_opinions(PRELIMINARY_PRINT_ORDERS, "24"))

        self.assertEqual(order.authors, ["AB", "BK", "KJ"])
        self.assertTrue(order.opinion_url.endswith("606US2PP_Ord.pdf#page=79"))
        self.assertEqual(order.citation, "606 U.S. 999")

    def test_bylines(self):
        label = scotus_recent.author_label
        self.assertEqual(label("R"), "Roberts, C.J.")
        self.assertEqual(label("KJ"), "Jackson, J.")
        self.assertEqual(label("EK"), "Kagan, J.")
        self.assertEqual(label("PC"), "Per curiam")
        self.assertEqual(label("ZZ"), "ZZ")
        self.assertEqual(scotus_recent.display_date("2026-06-29"),
                         "June 29, 2026")
        self.assertEqual(scotus_recent.pdf_page("x.pdf#page=87"), 87)
        self.assertEqual(scotus_recent.pdf_page("x.pdf"), 1)


class RecentListsTests(unittest.TestCase):
    def test_merits_reach_back_into_the_last_term_newest_first(self):
        terms = {
            2026: [merits("2026-10-14", "New Term Case", release="1")],
            2025: [merits("2026-06-29", "Trump v. Slaughter", release="62"),
                   merits("2026-06-30", "Trump v. Barbara", release="66"),
                   merits("2026-06-30", "West Virginia v. B. P. J.",
                          release="68")],
        }
        with patch("scotus_recent._current_term_year", return_value=2026), \
                patch("scotus_recent.fetch_term_opinions",
                      side_effect=lambda term, kind, session=None:
                      terms.get(term, [])) as fetch:
            rows = scotus_recent.recent_merits_opinions(3)

        self.assertEqual([r.name for r in rows],
                         ["New Term Case", "West Virginia v. B. P. J.",
                          "Trump v. Barbara"])
        self.assertEqual({c.args[1] for c in fetch.call_args_list},
                         {scotus_recent.MERITS})

    def test_a_full_term_needs_no_other(self):
        rows = [merits(f"2026-06-{d:02d}", f"Case {d}") for d in range(1, 13)]
        with patch("scotus_recent._current_term_year", return_value=2025), \
                patch("scotus_recent.fetch_term_opinions",
                      return_value=rows) as fetch:
            out = scotus_recent.recent_merits_opinions(10)

        self.assertEqual(len(out), 10)
        self.assertEqual(out[0].name, "Case 12")
        fetch.assert_called_once()

    def test_orders_five_at_most_newest_first(self):
        parsed = scotus_recent.parse_term_opinions(ORDERS_PAGE, "25")
        older = scotus_recent.parse_term_opinions(PRELIMINARY_PRINT_ORDERS, "24")
        pages = {2025: parsed, 2024: older}
        with patch("scotus_recent._current_term_year", return_value=2025), \
                patch("scotus_recent.fetch_term_opinions",
                      side_effect=lambda term, kind, session=None:
                      pages.get(term, [])):
            orders = scotus_recent.recent_order_opinions(5)

        self.assertEqual([o.name for o in orders],
                         ["Postal Service v. California",
                          "Jones v. United States",
                          "Danco Laboratories, LLC v. Louisiana",
                          "NIH v. APHA"])


class TermTableCacheTests(unittest.TestCase):
    def test_a_fetch_is_cached_and_a_failure_served_from_the_cache(self):
        page = SimpleNamespace(text=MERITS_PAGE, raise_for_status=lambda: None)
        with tempfile.TemporaryDirectory() as directory, \
                patch("scotus_recent._term_cache_path",
                      side_effect=lambda kind, term:
                      Path(directory) / f"{kind}_{term}.json"):
            session = SimpleNamespace(calls=[])

            def get(url, **_kw):
                session.calls.append(url)
                return page

            session.get = get
            first = scotus_recent.fetch_term_opinions(
                25, scotus_recent.MERITS, session=session)
            again = scotus_recent.fetch_term_opinions(
                25, scotus_recent.MERITS, session=session)
            self.assertEqual(session.calls, [
                "https://www.supremecourt.gov/opinions/slipopinion/25"])
            self.assertEqual(again, first)

            def down(url, **_kw):
                raise OSError("offline")

            stale = scotus_recent.fetch_term_opinions(
                25, scotus_recent.MERITS, force=True,
                session=SimpleNamespace(get=down))
            self.assertEqual([r.name for r in stale],
                             [r.name for r in first])


def panel():
    status = SimpleNamespace(set=lambda _text: None)
    return SimpleNamespace(_win=None, _status_var=status, _app=None)


def texts(lines):
    return [(line[0], line[1], len(line) == 3) for line in lines]


class PanelLinesTests(unittest.TestCase):
    ORDERS = [scotus_recent.OrderOpinion(
        name="Postal Service v. California", docket="26A305",
        date="2026-09-14", authors=["BK", "A"],
        opinion_url="https://www.supremecourt.gov/opinions/25pdf/x.pdf")]

    def test_the_homepages_decisions_lead_when_there_are_any(self):
        decision = scotus_recent.RecentDecision(
            name="NRSC v. FEC", docket="24-621", date="June 30, 2026",
            description="FECA limits violate the First Amendment.",
            opinion_url="https://www.supremecourt.gov/opinions/25pdf/y.pdf")
        lines = texts(_ScholarTextWindow._details_lines_recent(
            panel(), [decision], [], self.ORDERS))

        self.assertIn(("title", "Recent decisions", False), lines)
        self.assertIn(("h", "NRSC v. FEC", False), lines)
        self.assertNotIn(("title", "Opinions of the Court", False), lines)
        self.assertIn(("title", "Opinions relating to orders", False), lines)

    def test_otherwise_the_terms_latest_opinions_do(self):
        rows = [
            merits("2026-06-30", "Trump v. Barbara", author="R",
                   url="https://www.supremecourt.gov/opinions/25pdf/b.pdf",
                   description="Birthright citizenship."),
            scotus_recent.TermOpinion(
                term="25", date="2026-09-25", docket="26A388",
                name="People Not Politicians v. Onder", author="PC",
                opinion_url=""),
        ]
        lines = _ScholarTextWindow._details_lines_recent(
            panel(), [], rows, self.ORDERS)
        flat = texts(lines)

        self.assertIn(("title", "Opinions of the Court", False), flat)
        self.assertIn(("lbl", "June 30, 2026 · No. 25-1 · Roberts, C.J.",
                       False), flat)
        self.assertIn(("", "Birthright citizenship.", False), flat)
        self.assertIn(("", "Open the opinion", True), flat)
        # Listed but not linked yet: the docket is where it will appear.
        docket = next(line for line in lines
                      if line[1] == "Opinion not yet posted — the docket")
        self.assertEqual(docket[2], "https://www.supremecourt.gov/docket/"
                                    "docketfiles/html/public/26A388.html")
        # The orders below, once each, with their writers.
        self.assertLess(flat.index(("title", "Opinions of the Court", False)),
                        flat.index(("title", "Opinions relating to orders",
                                    False)))
        self.assertIn(("", "Separate opinions: Kavanaugh, J.; Alito, J.",
                       False), flat)
        self.assertIn(("", "Open the opinions", True), flat)

    def test_nothing_at_all_says_so(self):
        lines = texts(_ScholarTextWindow._details_lines_recent(panel(), [], []))
        self.assertIn(("lbl", "No recent decisions were found on "
                              "supremecourt.gov.", False), lines)


class PanelLoadTests(unittest.TestCase):
    def load(self, decisions):
        shown = []
        window = panel()
        window._recent_loaded = False
        window._set_details = lambda lines: None
        window._post = lambda fn, *args: fn(*args)
        window._apply_recent_scotus = lambda title, lines: shown.append(lines)
        window._details_lines_recent = (
            lambda d, m, o: _ScholarTextWindow._details_lines_recent(
                window, d, m, o))

        class Now:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()

        with patch("courtlistener_gui.threading.Thread", Now), \
                patch("scotus_recent.fetch_recent_decisions",
                      return_value=decisions), \
                patch("scotus_recent.recent_merits_opinions",
                      return_value=[merits("2026-06-30", "Trump v. Barbara")]
                      ) as merits_fetch, \
                patch("scotus_recent.recent_order_opinions",
                      return_value=[]) as orders_fetch:
            _ScholarTextWindow._load_recent_scotus(window)
        return shown[0], merits_fetch, orders_fetch

    def test_an_empty_homepage_falls_back_to_ten_opinions(self):
        lines, merits_fetch, orders_fetch = self.load([])
        merits_fetch.assert_called_once_with(10)
        orders_fetch.assert_called_once_with(5)
        self.assertIn(("h", "Trump v. Barbara"), [l[:2] for l in lines])

    def test_a_homepage_with_decisions_needs_no_fallback(self):
        decision = scotus_recent.RecentDecision(
            name="NRSC v. FEC", docket="24-621", date="June 30, 2026",
            description="", opinion_url="u")
        _lines, merits_fetch, orders_fetch = self.load([decision])
        merits_fetch.assert_not_called()
        orders_fetch.assert_called_once_with(5)


class PageLinkTests(unittest.TestCase):
    """An order's writings in a closed Term are pages of the preliminary
    print's orders section, linked with "#page=N" counted cover and all."""

    def test_the_page_a_link_names(self):
        self.assertEqual(_pdf_link_page_index("x/606US2PP_Ord.pdf#page=143"),
                         142)
        self.assertEqual(_pdf_link_page_index("x/26a305_4g15.pdf"), 0)
        self.assertEqual(_pdf_link_page_index(""), 0)

    def test_the_cover_is_kept_when_asked(self):
        with patch("courtlistener_gui._strip_page_proof_watermark",
                   side_effect=lambda data: data + b"-clean"), \
                patch("courtlistener_gui._strip_preliminary_print_cover",
                      side_effect=lambda data: data + b"-nocover"):
            self.assertEqual(
                courtlistener_gui._clean_reporter_pdf(b"%PDF"),
                b"%PDF-clean-nocover")
            self.assertEqual(
                courtlistener_gui._clean_reporter_pdf(b"%PDF", keep_cover=True),
                b"%PDF-clean")

    def test_the_fetch_passes_it_on(self):
        response = SimpleNamespace(
            url="https://www.supremecourt.gov/o.pdf", content=b"%PDF-1.7",
            headers={}, raise_for_status=lambda: None)
        with patch("courtlistener_gui._pdf_get", return_value=response), \
                patch("courtlistener_gui._clean_reporter_pdf",
                      side_effect=lambda data, keep_cover=False:
                      (data, keep_cover)) as clean:
            courtlistener_gui._fetch_pdf_bytes(
                "https://www.supremecourt.gov/o.pdf#page=3", keep_cover=True)
            courtlistener_gui._fetch_pdf_bytes(
                "https://www.supremecourt.gov/o.pdf")
        self.assertEqual([c.args[1] for c in clean.call_args_list],
                         [True, False])


if __name__ == "__main__":
    unittest.main()
