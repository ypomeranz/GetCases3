"""A citation to a printing static.case.law did not scan.

Some volumes were printed more than once, paginated differently.  20 N.C.
binds 3 and 4 Dev. & Bat. together: the Supreme Court cites "State v. Manuel,
20 N.C. 144, 152 (1838)" by the reprint that runs the two on, while the 20
N.C. static.case.law scanned keeps 3 Dev. & Bat.'s own pages — State v.
Manuel is 20 N.C. 20 there, and page 144 begins Carpenter v. Wall, which the
citation opened.  The volume's list of its cases says which case each page
begins; a citation whose name shares no party with that case is sent to the
volume's one case of its name, or to none.
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None

BASE = "https://static.case.law/nc/20"
# (file, case) pairs and each case's citations, as CasesMetadata.json lists
# them for 20 N.C. (cut down).
VOLUME = (
    {"20nc20": ("0020-01",), "3devbat20": ("0020-01",),
     "20nc144": ("0144-01",), "3devbat144": ("0144-01",),
     "20nc200": ("0200-01",), "20nc300": ("0300-01",)},
    {"0020-01": "State v. Manuel", "0144-01": "Carpenter v. Wall",
     "0200-01": "State v. Jones", "0300-01": "State v. Jones"},
    {},
)


class ParallelWithoutParenthesesTests(unittest.TestCase):
    """Google Scholar prints Trump v. Barbara's "Hood v. Hood, 93 Mass. (11
    Allen) 196" as "93 Mass. 11 Allen 196", which read as "93 Mass. 11" —
    the nominative volume taken for the page."""

    def cites(self, text):
        import citations
        return [action for _s, _e, action in citations.detect_links(text)]

    def test_read_as_the_official_cite_it_leads_with(self):
        for text, cite in (
                ("Hood v. Hood, 93 Mass. 11 Allen 196, 199-200 (1865).",
                 "93 Mass. 196@199"),
                ("Hood v. Hood, 93 Mass. (11 Allen) 196, 199-200 (1865).",
                 "93 Mass. 196@199"),
                ("Marbury v. Madison, 5 U.S. 1 Cranch 137 (1803).",
                 "5 U.S. 137")):
            with self.subTest(text=text):
                self.assertEqual(self.cites(text), [("cite", cite)])

    def test_a_volume_and_page_alone_are_still_a_citation(self):
        self.assertEqual(self.cites("Smith v. Jones, 93 Mass. 11."),
                         [("cite", "93 Mass. 11")])


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class NamedFileTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(gui, "_case_law_volume",
                                    return_value=VOLUME)
        patcher.start()
        self.addCleanup(patcher.stop)

    def named(self, cite, name, ext="pdf"):
        page = int(cite.split()[-1])
        folder = "case-pdfs" if ext == "pdf" else "cases"
        return gui._case_law_named_url(
            cite, f"{BASE}/{folder}/{page:04d}-01.{ext}", name)

    def test_the_named_case_in_the_printing_scanned(self):
        self.assertEqual(self.named("20 N.C. 144", "State v. Manuel"),
                         f"{BASE}/case-pdfs/0020-01.pdf")
        self.assertEqual(self.named("20 N.C. 144", "State v. Manuel", "json"),
                         f"{BASE}/cases/0020-01.json")

    def test_the_case_at_the_page_when_it_is_the_one_named(self):
        self.assertEqual(self.named("20 N.C. 144", "Carpenter v. Wall"),
                         f"{BASE}/case-pdfs/0144-01.pdf")
        # One party in common is enough to leave it be ("Carpenter v.
        # Wall's Adm'r" as another opinion may name it).
        self.assertEqual(self.named("20 N.C. 144", "Carpenter v. Walls"),
                         f"{BASE}/case-pdfs/0144-01.pdf")

    def test_a_common_party_alone_is_no_match(self):
        # "State" names half the volume.
        self.assertEqual(self.named("20 N.C. 20", "State v. Jones"), "")

    def test_never_one_of_several_of_the_name(self):
        self.assertEqual(self.named("20 N.C. 144", "State v. Jones"), "")

    def test_without_a_name_or_two_parties_the_page_decides(self):
        self.assertEqual(self.named("20 N.C. 144", ""),
                         f"{BASE}/case-pdfs/0144-01.pdf")
        self.assertEqual(self.named("20 N.C. 144", "In re Manuel"),
                         f"{BASE}/case-pdfs/0144-01.pdf")

    def test_a_page_the_list_does_not_name_is_left_to_the_page(self):
        self.assertEqual(self.named("20 N.C. 150", "State v. Manuel"),
                         f"{BASE}/case-pdfs/0150-01.pdf")


if __name__ == "__main__":
    unittest.main()
