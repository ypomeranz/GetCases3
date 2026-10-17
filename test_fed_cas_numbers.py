"""The case number a Federal Cases citation carries.

Table T1 cites the Federal Cases with the case's number after the date —
"Ex parte Merryman, 17 F. Cas. 144 (C.C.D. Md. 1861) (No. 9487)" — which
the report prints above its caption ("Case No. 9,487.").  The texts are OCR:
static.case.law's United States v. The Little Charles, 26 F. Cas. 979, heads
itself "Case No. 16,612.", a number volume 28 holds; volume 26 runs 15,244 to
15,819, and one digit's change makes it 15,612, its number.
"""

import os
import unittest
from types import SimpleNamespace

import fed_cas

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None


class CaseNumberTests(unittest.TestCase):
    def test_the_head_matter_s_number(self):
        self.assertEqual(fed_cas.case_number_in(
            "Case No. 9,487.\nEx parte MERRYMAN.", 17), "9487")
        self.assertEqual(fed_cas.case_number_in(
            "Case No. 3,230. CORFIELD v. CORYELL.", "6"), "3230")

    def test_a_misread_digit_repaired_by_the_volume(self):
        self.assertEqual(fed_cas.case_number_in(
            "Case No. 16,612.\nUNITED STATES v. The LITTLE CHARLES.", 26),
            "15612")

    def test_a_number_no_one_change_fits_is_none(self):
        self.assertEqual(fed_cas.case_number_in("Case No. 99,999.", 6), "")
        self.assertEqual(fed_cas.case_number_in("no number here", 6), "")

    def test_printed_as_rule_6_2_has_it(self):
        self.assertEqual(fed_cas.bluebook_number("9487"), "9487")
        self.assertEqual(fed_cas.bluebook_number("15612"), "15,612")
        self.assertEqual(fed_cas.bluebook_number("6082a"), "6082a")


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class CitationTests(unittest.TestCase):
    def _reader(self, cite, number):
        reader = object.__new__(gui._ScholarTextWindow)
        reader._bb = {"name": "United States v. The Little Charles",
                      "cite": cite, "court": "C.C.D. Va.", "year": "1818",
                      "fedcas_no": number}
        reader._decided_en_banc = lambda: False
        reader._citation_rtf = lambda *a: ""
        return reader

    def test_the_number_follows_the_date(self):
        reader = self._reader("26 F. Cas. 979", "15612")
        self.assertEqual(
            reader._bluebook_citation(None)[0],
            "United States v. The Little Charles, 26 F. Cas. 979 "
            "(C.C.D. Va. 1818) (No. 15,612).")
        self.assertEqual(
            reader._automatic_base_citation(),
            "United States v. The Little Charles, 26 F. Cas. 979 "
            "(C.C.D. Va. 1818) (No. 15,612)")

    def test_only_with_the_federal_cases(self):
        reader = self._reader("26 F. Cas. 979", "15612")
        plain = reader._bluebook_citation(
            None, cite_override="1 Brock. 347")[0]
        self.assertNotIn("No.", plain)


if __name__ == "__main__":
    unittest.main()
