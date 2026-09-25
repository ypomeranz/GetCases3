"""Download Cited Cases: every case is looked up, and named, by its own name.

The compiler hands each case to the resolver with the name its citation
prints: the name that picks the right opinion when two begin on one page, that
CourtListener and Google Scholar are asked for, and that names the file when
nothing better is known.  It used to read that name from the text *before*
each citation's link — but the link already begins with the name, so what it
read was whatever came before it: in a table of authorities the entry above
("8 Hecht Co. v. Bowles, 321 U.S. 321" for Hollingsworth v. Perry), in running
text the case cited last.  The year, which the link also takes in, was lost.
"""

import os
import re
import tempfile
import unittest
import zipfile

from brief_compiler import collect_authorities, compile_to_zip


def _cases(text):
    """(cite, name, year) for every reporter case collected from *text*."""
    return [(a.value, a.name, a.year)
            for a in collect_authorities(text) if a.kind == "cite"]


TOA = (
    "ii\nTABLE OF AUTHORITIES\nFederal Cases\n"
    "Barnes v. E-Systems, Inc. Grp. Hosp. Med. & Surgical Ins. Plan, "
    "501 U.S. 1301\n(1991)....................................... 8\n"
    "Hecht Co. v. Bowles, 321 U.S. 321 (1933) ..................... 7\n"
    "Hollingsworth v. Perry, 558 U.S. 183 (2010) ............... 9, 10\n"
    "Maryland v. King, 567 U.S. 1301 (2012)...................... 10\n"
)

TOA_CASES = [
    ("501 U.S. 1301",
     "Barnes v. E-Systems, Inc. Grp. Hosp. Med. & Surgical Ins. Plan",
     "1991"),
    ("321 U.S. 321", "Hecht Co. v. Bowles", "1933"),
    ("558 U.S. 183", "Hollingsworth v. Perry", "2010"),
    ("567 U.S. 1301", "Maryland v. King", "2012"),
]


class TableOfAuthoritiesTests(unittest.TestCase):
    """Each entry keeps its own name, not the one above it."""

    def test_each_entry_keeps_its_own_name_and_year(self):
        self.assertEqual(_cases(TOA), TOA_CASES)

    def test_without_dot_leaders_too(self):
        self.assertEqual(_cases(re.sub(r"\.{3,}", "\t", TOA)), TOA_CASES)


class RunningTextTests(unittest.TestCase):

    def test_a_short_form_takes_no_name_from_the_cite_before_it(self):
        text = ("The Court relied on Roe v. Wade, 410 U.S. 113, 152 (1973), "
                "and again on 410 U.S. at 164.")
        self.assertEqual(_cases(text),
                         [("410 U.S. 113", "Roe v. Wade", "1973")])

    def test_later_short_forms_do_not_rename_earlier_cases(self):
        text = ("See Nken v. Holder, 556 U.S. 418, 432 (2009); Winter v. "
                "NRDC, 555 U.S. 7, 22 (2008).  Winter, 555 U.S. at 24; Nken, "
                "556 U.S. at 434.  Id. at 435.")
        self.assertEqual(_cases(text), [
            ("556 U.S. 418", "Nken v. Holder", "2009"),
            ("555 U.S. 7", "Winter v. NRDC", "2008"),
        ])

    def test_the_whole_caption_wins_over_a_short_forms_party(self):
        text = ("See Nken, 556 U.S. at 433.  Nken v. Holder, 556 U.S. 418 "
                "(2009).")
        self.assertEqual(_cases(text),
                         [("556 U.S. 418", "Nken v. Holder", "2009")])

    def test_a_pin_cite_split_off_keeps_the_year_for_the_case(self):
        text = "Devenpeck v. Alford, 543 U. S. 146, 149, 155-156 (2004)."
        self.assertEqual(_cases(text),
                         [("543 U.S. 146", "Devenpeck v. Alford", "2004")])

    def test_parallel_citations_share_the_name_and_year(self):
        text = ("Roe v. Wade, 410 U.S. 113, 93 S. Ct. 705, 35 L. Ed. 2d 147 "
                "(1973); Doe v. Bolton, 410 U.S. 179 (1973).")
        self.assertEqual(_cases(text), [
            ("410 U.S. 113", "Roe v. Wade", "1973"),
            ("93 S. Ct. 705", "Roe v. Wade", "1973"),
            ("35 L. Ed. 2d 147", "Roe v. Wade", "1973"),
            ("410 U.S. 179", "Doe v. Bolton", "1973"),
        ])

    def test_a_docket_number_is_not_the_name(self):
        text = ("But Foxtons, Inc. v. Cirri Germain Realty, No. A-61210-05T3, "
                "2008 WL 465653 (N.J. Super. Ct. App. Div. Feb. 22, 2008).")
        self.assertEqual(_cases(text), [
            ("2008 WL 465653", "Foxtons, Inc. v. Cirri Germain Realty",
             "2008")])

    def test_a_footnote_mark_does_not_disturb_the_name(self):
        text = "Hollingsworth v. Perry, 558 U.S. 183⁵ (2010)."
        self.assertEqual(_cases(text), [
            ("558 U.S. 183", "Hollingsworth v. Perry", "2010")])


class _Resolver:
    """Finds every case, but knows no citation for any: the file name falls
    back to what the brief printed."""

    def __init__(self):
        self.asked = []

    def is_fed_appx(self, cite):
        return False

    def case_rtf(self, cite, name):
        self.asked.append((cite, name))
        return ("{\\rtf1 opinion}", "", "Google Scholar (cache)")


class ResolverTests(unittest.TestCase):
    """What a lookup is asked for, and what the file is called."""

    def test_each_case_is_looked_up_and_named_by_its_own_name(self):
        resolver = _Resolver()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Cited Authorities.zip")
            compile_to_zip(collect_authorities(TOA), resolver, path)
            with zipfile.ZipFile(path) as zf:
                files = zf.namelist()
        self.assertEqual(resolver.asked,
                         [(cite, name) for cite, name, _year in TOA_CASES])
        self.assertIn("Hollingsworth v. Perry, 558 U.S. 183 (2010).rtf", files)
        self.assertIn("Hecht Co. v. Bowles, 321 U.S. 321 (1933).rtf", files)


if __name__ == "__main__":
    unittest.main()
