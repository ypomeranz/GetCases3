"""Citations in a filed brief, as the PDF brief viewer reads them.

The text here is taken from an amicus brief filed in the Supreme Court on an
emergency application (law professors' brief in United States Postal Service
v. California, No. 26A305): its table of contents and table of authorities,
its footnote marks, its record cites to the application ("Appl. 6, 31"), and
its cites of the Court's own recent orders by docket number.  Before these
tests each of those either linked something that is no citation, drew the
link over the wrong text, opened the wrong case, or was missed outright.

``citations.detect_links`` is exercised directly.  The PDF side — footnote
marks read off the glyphs — goes through the viewer's own extraction chain,
lifted out of ``courtlistener_gui`` with ``ast`` since that module imports
tkinter, which a headless run may not have.
"""

import __future__
import ast
import json
import os
import pathlib
import re
import threading
import unittest
from unittest.mock import patch

import brief_compiler
import brief_reader
import citations
from citations import detect_links

try:
    import pypdfium2 as pdfium
    HAVE_PDFIUM = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_PDFIUM = False

# courtlistener_gui offers to install missing packages when imported.
os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")

try:
    import tkinter  # noqa: F401
    HAVE_TK = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_TK = False


def _links(text, italic=None):
    """(linked text, action) for every link in *text*."""
    return [(text[s:e], a) for s, e, a in detect_links(text, italic=italic)]


def _mask(text, *italic_phrases):
    """A styling mask with every occurrence of *italic_phrases* italic."""
    mask = [False] * len(text)
    for phrase in italic_phrases:
        for m in re.finditer(re.escape(phrase), text):
            for i in range(m.start(), m.end()):
                mask[i] = True
    return mask


class TableOfContentsTests(unittest.TestCase):
    """Dot leaders carry a heading to its page; they are no reporter."""

    TOC = (
        "SUMMARY OF THE ARGUMENT ..................................... 2\n"
        "ARGUMENT..................................................... 6\n"
        "III. The Balance of the Equities Tip Sharply Against a Stay. 13\n"
        "CONCLUSION................................................... 20"
    )

    def test_a_heading_run_into_its_leaders_is_not_a_citation(self):
        # "2\nARGUMENT......6" read as volume 2 of a reporter "ARGUMENT...".
        self.assertEqual(_links(self.TOC), [])

    def test_a_real_citation_before_the_leaders_still_links(self):
        text = "Hecht Co. v. Bowles, 321 U.S. 321 (1933) ............. 7"
        self.assertEqual(
            _links(text),
            [("Hecht Co. v. Bowles, 321 U.S. 321 (1933)",
              ("cite", "321 U.S. 321"))])


class RecordCiteTests(unittest.TestCase):
    """A Supreme Court application is cited by its pages, like the record."""

    def test_a_number_before_an_application_cite_is_no_volume(self):
        # The footnote mark in "irreparable injury.⁴ Appl. 6, 31", as a text
        # layer without its superscript gives it.
        text = "of success on the merits and irreparable injury.\n4 Appl. 6, 31."
        self.assertEqual(_links(text), [])

    def test_application_cites_break_an_id_chain_as_record_cites_do(self):
        text = ("Nken v. Holder, 556 U.S. 418, 432 (2009).  The applicant "
                "says so (Appl. 29-30).  Id. at 434.")
        self.assertNotIn("Id. at 434", [t for t, _a in _links(text)])


class CaseNameSpanTests(unittest.TestCase):
    """The link covers the case name, and only the case name, before a cite."""

    TOA = ("ii\nTABLE OF AUTHORITIES\nFederal Cases\n"
           "Barnes v. E-Systems, Inc. Grp. Hosp. Med. & Surgical Ins. Plan, "
           "501 U.S. 1301\n(1991)........................................ 8\n"
           "Hecht Co. v. Bowles, 321 U.S. 321 (1933) .................... 7")

    def test_table_headings_are_not_read_into_the_first_entry(self):
        self.assertEqual(
            _links(self.TOA)[0],
            ("Barnes v. E-Systems, Inc. Grp. Hosp. Med. & Surgical Ins. Plan, "
             "501 U.S. 1301\n(1991)", ("cite", "501 U.S. 1301")))

    def test_with_styling_roman_headings_are_trimmed_off_an_italic_name(self):
        mask = _mask(self.TOA, "Barnes v. E-Systems, Inc. Grp. Hosp. Med. & "
                               "Surgical Ins. Plan", "Hecht Co. v. Bowles")
        self.assertEqual(
            _links(self.TOA, italic=mask)[0][0],
            "Barnes v. E-Systems, Inc. Grp. Hosp. Med. & Surgical Ins. Plan, "
            "501 U.S. 1301\n(1991)")

    def test_a_capitalized_signal_in_a_parenthetical_is_not_a_name(self):
        text = ("the relative harms.’” (Citing Hollingsworth v. \nPerry, "
                "558 U.S. 183, 190 (2010)) (emphasis added).")
        self.assertEqual(
            _links(text),
            [("Hollingsworth v. \nPerry, 558 U.S. 183, 190 (2010)",
              ("cite", "558 U.S. 183@190"))])

    def test_the_parenthesis_around_a_citation_is_not_part_of_it(self):
        text = "held so (Hollingsworth v. Perry, 558 U.S. 183, 190 (2010))."
        self.assertEqual(
            _links(text)[0][0],
            "Hollingsworth v. Perry, 558 U.S. 183, 190 (2010)")

    def test_a_name_wrapped_across_lines_is_kept_whole(self):
        for text, name in (
            ("Thus, in North\nCarolina v. Covington, 581 U.S. 486, 488 "
             "(2017), this Court", "North\nCarolina v. Covington"),
            ("the Court held in\nNational Republican\nSenatorial Committee v. "
             "Brown, 1 F.4th 5 (2020).",
             "National Republican\nSenatorial Committee v. Brown"),
            ("in the conclusion.\nCommonwealth of Pennsylvania, Department "
             "of\nTransportation v. Smith, 123 A.2d 456 (Pa. 1956).",
             "Commonwealth of Pennsylvania, Department of\n"
             "Transportation v. Smith"),
        ):
            with self.subTest(name=name):
                self.assertTrue(_links(text)[0][0].startswith(name))


class AtLeftOutTests(unittest.TestCase):
    """"Wilcox, 145 S. Ct. 1417" pins page 1417 of Wilcox (1415)."""

    FULL = "Trump v. Wilcox, 145 S. Ct. 1415, 1415 (2025).  "

    def test_the_page_is_a_pin_into_the_case_cited_in_full(self):
        text = self.FULL + ("…a “disruptive effect” on election "
                            "administration.  Wilcox, 145 S. Ct. 1417.")
        self.assertEqual(
            _links(text)[-1],
            ("Wilcox, 145 S. Ct. 1417", ("cite", "145 S. Ct. 1415@1417")))

    def test_later_short_forms_and_ids_keep_the_real_first_page(self):
        text = self.FULL + ("Wilcox, 145 S. Ct. 1417.  Then 145 S. Ct. at "
                            "1418.  Id. at 1419.")
        actions = [a for _t, a in _links(text)]
        self.assertIn(("cite", "145 S. Ct. 1415@1418"), actions)
        self.assertIn(("cite", "145 S. Ct. 1415@1419"), actions)

    def test_a_year_parenthetical_marks_a_full_citation(self):
        text = self.FULL + "Wilcox, 145 S. Ct. 1417 (2025)."
        self.assertEqual(_links(text)[-1][1], ("cite", "145 S. Ct. 1417"))

    def test_a_party_to_no_case_cited_before_is_a_case_of_its_own(self):
        text = self.FULL + "Abdul Latif, 145 S. Ct. 1450."
        self.assertEqual(_links(text)[-1][1], ("cite", "145 S. Ct. 1450"))

    def test_parallel_citations_are_left_alone(self):
        text = ("A v. B, 410 U.S. 113, 93 S. Ct. 705 (1973); C v. D, "
                "410 U.S. 179, 93 S. Ct. 739 (1973).")
        actions = [a for _t, a in _links(text)]
        self.assertIn(("cite", "93 S. Ct. 739"), actions)


class NamedShortFormTests(unittest.TestCase):
    """"Nken, at 433-34" — the case by name, the page, and no reporter."""

    FULL = "Nken v. Holder, 556 U.S. 418 (2009).  "

    def test_the_name_picks_out_the_case_cited_in_full(self):
        text = self.FULL + ("“The party requesting a stay bears the burden.” "
                            "Nken,\nat 433-34.")
        self.assertEqual(
            _links(text)[-1],
            ("Nken,\nat 433-34", ("cite", "556 U.S. 418@433")))

    def test_the_supra_form_and_a_footnote_pin(self):
        text = self.FULL + "Nken, supra, at 433 n.2."
        self.assertEqual(
            _links(text)[-1],
            ("Nken, supra, at 433 n.2", ("cite", "556 U.S. 418@433n2")))

    def test_an_authors_name_is_no_case(self):
        text = self.FULL + ("Bray, Preliminary Injunction Realism, at 225 "
                            "& nn.50-51.")
        self.assertEqual(len(_links(text)), 1)

    def test_a_name_several_cases_answer_to_is_not_guessed_at(self):
        text = ("Trump v. CASA, Inc., 606 U.S. 831 (2025); Trump v. Int’l "
                "Refugee Assistance Project, 582 U.S. 571 (2017).  Trump, at "
                "840.")
        self.assertEqual(len(_links(text)), 2)

    def test_a_page_outside_the_case_is_not_linked(self):
        self.assertEqual(len(_links(self.FULL + "Nken, at 900.")), 1)

    def test_prose_is_not_a_short_form(self):
        text = ("Smith v. Jones, 500 U.S. 1 (1991).  " + self.FULL
                + "In Nken, at issue was a stay.  Congress, at 5 U.S.C. § 552, "
                  "said so.  In Smith, at 5 years old, he sued.")
        self.assertEqual(
            [t for t, a in _links(text) if a[0] == "cite"],
            ["Smith v. Jones, 500 U.S. 1 (1991)",
             "Nken v. Holder, 556 U.S. 418 (2009)"])


class IdChainTests(unittest.TestCase):
    """An "Id." stops at an authority the document cannot open."""

    FULL = "Mirabelli v. Bonta, 607 U.S. 492, 501 (2026).  "

    def test_a_short_cite_to_a_volume_never_cited_in_full(self):
        # The brief mistypes 558 U.S. as 588; the "id." means that case.
        text = self.FULL + "Perry says so.  588 U.S. at 190.  But, id. at 500."
        self.assertNotIn("id. at 500", [t for t, _a in _links(text)])

    def test_a_named_short_form_to_an_article(self):
        text = self.FULL + ("Bray, Preliminary Injunction Realism, at 225.  "
                            "Id. at 500.")
        self.assertNotIn("Id. at 500", [t for t, _a in _links(text)])

    def test_a_resolved_named_short_form_anchors_the_id(self):
        text = ("Nken v. Holder, 556 U.S. 418 (2009).  Nken,\nat 433-34.  "
                "Id. at 435.")
        self.assertEqual(_links(text)[-1],
                         ("Id. at 435", ("cite", "556 U.S. 418@435")))


class SupremeCourtDocketTests(unittest.TestCase):
    """The Court's recent orders, cited by docket number and date."""

    TEXT = (
        "Trump v. California, No. 26A139 (U.S. Aug. 24, 2026) ........ 5\n"
        "…not concrete injury.  See Trump v. California, No. 26A139, slip "
        "op. \nat 4 (U.S. Aug. 24, 2026).\n"
        "As this Court explained in Trump v. California, slip op. at \n2, "
        "to secure a stay…\n"
        "National Republican Senatorial Committee v. Brown, No. 26A274 "
        "(U.S., Sept. 4, \n2026) ...... 9\n"
        "statement in National Republican Senatorial Comm. v. Brown, No. "
        "26A274, slip op. \nat 2 (U.S., Sept. 4, 2026), that"
    )

    def test_each_citation_links_to_its_docket_with_its_name(self):
        links = _links(self.TEXT)
        self.assertEqual([t for t, _a in links], [
            "Trump v. California, No. 26A139 (U.S. Aug. 24, 2026)",
            "Trump v. California, No. 26A139, slip op. \nat 4 (U.S. Aug. 24, "
            "2026)",
            "Trump v. California, slip op. at \n2",
            "National Republican Senatorial Committee v. Brown, No. 26A274 "
            "(U.S., Sept. 4, \n2026)",
            "National Republican Senatorial Comm. v. Brown, No. 26A274, slip "
            "op. \nat 2 (U.S., Sept. 4, 2026)",
        ])
        self.assertEqual({a[0] for _t, a in links}, {"scotus"})
        specs = [json.loads(a[1]) for _t, a in links]
        self.assertEqual(specs[0], {"docket": "26A139", "date": "2026-08-24",
                                    "name": "Trump v. California"})
        # Every cite of one docket carries the same spec, from its first.
        self.assertEqual(specs[1], specs[0])
        self.assertEqual(specs[2], specs[0])
        self.assertEqual(specs[4], specs[3])
        self.assertEqual(specs[3]["name"],
                         "National Republican Senatorial Committee v. Brown")

    def test_a_short_form_naming_no_docketed_case_is_left_alone(self):
        text = self.TEXT + "\nSmith v. Jones, slip op. at 3."
        self.assertNotIn("Smith", " ".join(t for t, _a in _links(text)))

    def test_a_filing_date_is_no_decision(self):
        text = "Brief for Petitioner, Doe v. Roe, No. 24-123 (U.S. filed Aug. 4, 2025)."
        self.assertEqual(_links(text), [])

    def test_a_district_court_docket_is_still_recap(self):
        text = ("Declaration of Doris Speer, Document 75-5 ¶ 14, No. "
                "1:26-cv-11549 (D. Mass, Apr. 23, 2026) Dkt. No. 75.")
        (_text, action), = _links(text)
        self.assertEqual(action[0], "recap")
        self.assertEqual(json.loads(action[1])["court"], "mad")

    def test_the_compiler_names_it_and_notes_it_is_not_bundled(self):
        auths = [a for a in brief_compiler.collect_authorities(self.TEXT)
                 if a.kind == "scotus"]
        self.assertEqual([a.label() for a in auths], [
            "Trump v. California, No. 26A139",
            "National Republican Senatorial Committee v. Brown, No. 26A274",
        ])
        self.assertIn("scotus", brief_compiler._NOTE_ONLY)


# ---------------------------------------------------------------------------
# Footnote marks, read off the glyphs of a PDF
# ---------------------------------------------------------------------------

def _pdf(*streams: str) -> bytes:
    """A one-page Helvetica PDF drawing the raw content-stream *streams*."""
    stream = "\n".join(streams).encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>",
        b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj" % i + body + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref))
    return bytes(out)


# Each mark is set the way a word processor sets one: two-thirds the size,
# lifted off the baseline by the text rise ("Ts").
_MARKED = _pdf(
    # The mark in front of a record cite …
    "BT /F1 12 Tf 72 700 Td (of success and irreparable injury.) Tj "
    "/F1 8 Tf 4 Ts (4) Tj /F1 12 Tf 0 Ts ( Opp. 6, 31. This Court held.) Tj ET",
    # … one glued to a page number, which the text layer reads as "1835" …
    "BT /F1 12 Tf 72 660 Td (See Hollingsworth v. Perry, 558 U.S. 183) Tj "
    "/F1 8 Tf 4 Ts (5) Tj /F1 12 Tf 0 Ts ( \\(2010\\).) Tj ET",
    # … and the number heading the note itself.
    "BT /F1 8 Tf 72 100 Td 4 Ts (4) Tj /F1 12 Tf 0 Ts "
    "( There is some disagreement, 12 F.3d 5.) Tj ET",
)

_POSTPONED = __future__.annotations.compiler_flag


def _load_gui(*names, consts=()):
    """Lift *names* (and the assignments to *consts*) out of courtlistener_gui
    without importing it."""
    src = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text()
    tree = ast.parse(src)
    ns = {"re": re, "_PDFIUM_LOCK": threading.RLock(),
          "brief_reader": brief_reader,
          "detect_brief_links": citations.detect_links}
    for node in tree.body:
        if (isinstance(node, (ast.FunctionDef, ast.ClassDef))
                and node.name in names):
            exec(compile(ast.get_source_segment(src, node), "<gui>", "exec",
                         _POSTPONED), ns)
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") in consts for t in node.targets)):
            exec(compile(ast.get_source_segment(src, node), "<gui>", "exec",
                         _POSTPONED), ns)
    return ns


@unittest.skipUnless(HAVE_PDFIUM, "pypdfium2 not installed")
class FootnoteMarkTests(unittest.TestCase):
    """A footnote mark is read as a mark, not as part of a citation."""

    @classmethod
    def setUpClass(cls):
        cls.ns = _load_gui(
            "_degenerate_ocr_metrics", "_repair_degenerate_ocr_page",
            "_PageSlants", "_union_line_runs", "_extract_pdf_text_and_style",
            "_citation_links_from_pages",
            consts=("_FONT_FLAG_ITALIC",))

    def _page(self):
        doc = pdfium.PdfDocument(_MARKED)
        tp = doc[0].get_textpage()
        self.addCleanup(doc.close)
        self.addCleanup(tp.close)
        return tp

    def test_the_marks_are_found_and_nothing_else(self):
        tp = self._page()
        text = tp.get_text_range()
        marks = brief_reader.superscript_digits(tp, text)
        self.assertEqual(len(marks), 3)
        self.assertEqual(sorted(text[i] for i in marks), ["4", "4", "5"])
        # "183" is set on the line, and stays a page number.
        at = text.index("183")
        self.assertFalse(marks & {at, at + 1, at + 2})

    def test_the_text_reader_shows_them_as_marks(self):
        text = brief_reader._page_text(self._page())
        self.assertIn("injury.⁴", text)
        self.assertIn("558 U.S. 183⁵", text)
        # The mark is no part of the page, nor does it hide where it ends.
        self.assertEqual([a for _t, a in _links(text)], [
            ("cite", "558 U.S. 183"), ("cite", "12 F.3d 5"),
        ])

    def test_the_viewer_links_the_page_the_brief_printed(self):
        pages, italics = self.ns["_extract_pdf_text_and_style"](_MARKED)
        self.assertEqual(len(italics[0].superscripts), 3)
        links = self.ns["_citation_links_from_pages"](pages, italics)
        actions = [a for _rect, a, _snippet in links[0]]
        self.assertIn(("cite", "558 U.S. 183"), actions)
        self.assertNotIn(("cite", "558 U.S. 1835"), actions)
        # "⁴ Opp. 6" is no volume 4 of a reporter "Opp.".
        self.assertFalse(any("Opp" in a[1] for a in actions))

    def test_the_page_text_keeps_its_digits(self):
        # Find, selection and copying read the page, not the scan's text.
        pages, _italics = self.ns["_extract_pdf_text_and_style"](_MARKED)
        self.assertIn("injury.4", "".join(ch for ch, _bx in pages[0]))

    def test_plain_flags_mean_no_marks(self):
        pages, italics = self.ns["_extract_pdf_text_and_style"](_MARKED)
        links = self.ns["_citation_links_from_pages"](
            pages, [list(flags) for flags in italics])
        actions = [a for _rect, a, _snippet in links[0]]
        self.assertIn(("cite", "558 U.S. 1835"), actions)


@unittest.skipUnless(HAVE_TK, "tkinter not installed")
class SupremeCourtDocketDispatchTests(unittest.TestCase):
    """A docket-number citation opens the Court's decision or its docket."""

    SPEC = json.dumps({"docket": "26A139", "date": "2026-08-24",
                       "name": "Trump v. California"})

    @classmethod
    def setUpClass(cls):
        import courtlistener_gui
        cls.gui = courtlistener_gui

    def test_the_brief_viewer_routes_it(self):
        with patch.object(self.gui, "_open_scotus_citation") as opener:
            self.gui._follow_brief_action("app", "parent",
                                          ("scotus", self.SPEC), print)
        opener.assert_called_once_with("app", "parent", self.SPEC, print)

    def test_right_click_opens_the_docket_page(self):
        with patch.object(self.gui.webbrowser, "open") as browse:
            self.gui._open_citation_in_browser(("scotus", self.SPEC))
        browse.assert_called_once_with(
            "https://www.supremecourt.gov/docket/docketfiles/html/public/"
            "26A139.html")

    def test_it_is_tinted_as_a_case(self):
        self.assertEqual(self.gui._brief_action_category("scotus"), "case")


if __name__ == "__main__":
    unittest.main()
