"""Citations in a court of appeals opening brief, as the PDF brief viewer, its
text reader and Download Cited Cases read them.

The text here is taken from an opening brief filed in the Seventh Circuit
(Diaz v. Schmidt, No. 26-1507): the e-filing stamp and page number on every
page, which a citation broken across a page runs through; its table of
authorities, where one entry's page number runs into the rule listed after
it; the district court's docket number on the cover; its string cites of
unpublished district court opinions by Westlaw number; and case names with a
lower-case particle or a long abbreviation in them.  Before these tests each
of those linked something that is no citation, opened the wrong case, named
the wrong case, or drew the link over the wrong text.

``citations.detect_links`` is exercised directly.  The PDF side — the page
furniture read off a page — goes through the viewer's own extraction chain,
lifted out of ``courtlistener_gui`` with ``ast`` since that module imports
tkinter, which a headless run may not have.
"""

import __future__
import ast
import json
import pathlib
import re
import threading
import unittest

import brief_compiler
import brief_reader
import citations
import fed_cas
from citations import detect_links

try:
    import pypdfium2  # noqa: F401
    HAVE_PDFIUM = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_PDFIUM = False


def _links(text, italic=None):
    """(linked text, whitespace collapsed; action) for every link in *text*."""
    return [(re.sub(r"\s+", " ", text[s:e]), a)
            for s, e, a in detect_links(text, italic=italic)]


def _recap(text):
    """(linked text, spec) for every unpublished-opinion link in *text*."""
    return [(t, json.loads(a[1])) for t, a in _links(text) if a[0] == "recap"]


# The stamp the court of appeals prints on every page of a filed brief.
STAMP = "Case: 26-1507 Document: 11 Filed: 05/01/2026 Pages: 87"


# ---------------------------------------------------------------------------
# Page furniture
# ---------------------------------------------------------------------------

class PageFurnitureTests(unittest.TestCase):
    """A citation broken across a page reads through the stamp and the page
    number between its halves."""

    # Page text as the PDF gives it: the stamp comes last on a page, the next
    # page's number first.
    LUDER = ("has the identical effect as a suit against the state.” Luder v. "
             "Endicott, 253 \r\n" + STAMP + "\n20\r\nF.3d 1020, 1023 (7th Cir. "
             "2001). The same is true here.")
    GREENGRASS = ("support of it. Greengrass v. Int’l Monetary Sys. Ltd., 776 "
                  "F.3d 481, 486-\r\n" + STAMP + "\n46\r\n87 (7th Cir. 2015). "
                  "Schmidt asserted that the complaint would add “fuel")

    def test_a_citation_broken_across_a_page_reads_whole(self):
        (text, action), = _links(self.LUDER)
        # Not "20 F.3d 1020": the page number is no volume.
        self.assertEqual(action, ("cite", "253 F.3d 1020@1023"))
        self.assertTrue(text.startswith("Luder v. Endicott, 253 "))
        self.assertTrue(text.endswith("1023 (7th Cir. 2001)"))

    def test_a_pin_range_broken_across_a_page(self):
        (text, action), = _links(self.GREENGRASS)
        self.assertEqual(action, ("cite", "776 F.3d 481@486"))
        self.assertTrue(text.endswith("87 (7th Cir. 2015)"))

    def test_a_page_number_set_off_by_blank_lines(self):
        # The text reader's form: pages joined by blank lines, no stamp.
        text = ("the state.” Luder v. Endicott, 253\n\n20\n\nF.3d 1020, 1023 "
                "(7th Cir. 2001).")
        self.assertEqual([a for _t, a in _links(text)],
                         [("cite", "253 F.3d 1020@1023")])

    def test_the_scan_keeps_every_offset_and_the_text_around_it(self):
        scanned = citations.scan_text(self.LUDER)
        self.assertEqual(len(scanned), len(self.LUDER))
        self.assertNotIn("Document", scanned)
        self.assertNotIn("\n20\r", scanned)
        # Everything but the furniture reads as it was written.
        self.assertEqual(scanned.replace(" ", ""),
                         self.LUDER.replace(" ", "").replace(STAMP.replace(
                             " ", ""), "").replace("\n20\r", "\n\r"))

    def test_a_page_says_where_its_furniture_is(self):
        end_of_page = "the state.” Luder v. Endicott, 253 \r\n" + STAMP
        self.assertEqual(
            [end_of_page[s:e] for s, e in
             citations.page_furniture(end_of_page)], [STAMP])
        top_of_page = "20\r\nF.3d 1020, 1023 (7th Cir. 2001).\r\n"
        self.assertEqual(
            [top_of_page[s:e].strip() for s, e in
             citations.page_furniture(top_of_page)], ["20"])

    def test_other_courts_stamps(self):
        for stamp in (
            "Case: 3:24-cv-00161-jdp Document #: 73 Filed: 02/20/26 "
            "Page 1 of 8",
            "Case: 1:20-cv-01234 Document #: 55 Filed: 03/02/21 Page 5 of 12 "
            "PageID #:423",
            "Case 2:19-cv-01234-ABC-DEF Document 88 Filed 04/05/20 Page 7 of "
            "30 Page ID #:1234",
            "USCA11 Case: 22-10000 Document: 30 Date Filed: 03/01/2023 "
            "Page: 5 of 40",
            "Appellate Case: 23-1100 Document: 010110912345 Date Filed: "
            "08/21/2023 Page: 12",
            "Case: 21-1234 Document: 00117654321 Page: 4 Date Filed: "
            "01/05/2022 Entry ID: 6470000",
            "Case: 19-35678, 07/10/2020, ID: 11747321, DktEntry: 25, Page 3 "
            "of 40",
        ):
            with self.subTest(stamp=stamp):
                text = ("See Smith v. Jones, 123 F.3d\n" + stamp
                        + "\n14\n456, 460 (9th Cir. 1997).")
                self.assertEqual([a for _t, a in _links(text)],
                                 [("cite", "123 F.3d 456@460")])

    def test_the_text_reader_keeps_a_range_broken_at_a_line_end(self):
        # Ctrl+B joins a word broken across lines; a range is no word.
        page = ("Lane v. Franks, 573 U.S. 228, 238 (2014). Diaz testified in "
                "response to\r\nsubpoenaed testimony. Lane, 573 U.S. at 232-\r\n"
                "33; the evi-\r\ndence shows it.")
        text = brief_reader._clean_pdf_text(page)
        self.assertIn("Lane, 573 U.S. at 232-33;", text)
        self.assertIn("the evidence shows", text)
        self.assertEqual([a for _t, a in _links(text)], [
            ("cite", "573 U.S. 228@238"), ("cite", "573 U.S. 228@232"),
        ])

    def test_the_text_reader_keeps_a_range_broken_across_a_page(self):
        pages = ("support of it. Greengrass v. Int’l Monetary Sys. Ltd., 776 "
                 "F.3d 481, 486-\r\n" + STAMP,
                 "46\r\n87 (7th Cir. 2015). Schmidt asserted that")
        text = brief_reader._clean_pdf_text("\n\n".join(pages))
        (linked, action), = _links(text)
        self.assertEqual(action, ("cite", "776 F.3d 481@486"))
        self.assertTrue(linked.endswith("87 (7th Cir. 2015)"), linked)

    def test_numbers_in_the_text_are_left_alone(self):
        text = ("In 2019 the court held as much. See Smith v. Jones, 12 F.3d "
                "5, 7 (7th Cir. 1993).\n12\nmonths later it held it again.")
        self.assertEqual([a for _t, a in _links(text)],
                         [("cite", "12 F.3d 5@7")])
        self.assertIn("In 2019", citations.scan_text(text))


# ---------------------------------------------------------------------------
# Things that are no citation
# ---------------------------------------------------------------------------

class CoverPageTests(unittest.TestCase):
    def test_the_district_courts_docket_is_no_federal_cases_number(self):
        for text in (
            "United States District Court for the Western District of "
            "Wisconsin\r\nCase No. 24-cv-00161-jdp, Hon. James D. Peterson",
            "OPINION and ORDER\nCase No. 24-cv-161-jdp",
        ):
            with self.subTest(text=text):
                self.assertEqual(fed_cas.iter_cites(text), [])
                self.assertEqual(_links(text), [])

    def test_a_federal_cases_number_still_reads(self):
        (_text, action), = _links("The Avon, Case No. 680; Rodd v. Heartt")
        self.assertEqual(action[0], "fedcas")
        self.assertEqual(json.loads(action[1]),
                         {"no": "680", "name": "The Avon"})


class TableOfAuthoritiesRuleTests(unittest.TestCase):
    """An entry's page number is no volume of the rule listed after it."""

    TOA = ("Wis. Stat. § 895.46(1)(a) ........................................ "
           "23\nOther Authorities\nFed. R. App. P. 43(c) "
           "........................................ 23\nFed. R. Civ. P. "
           "25(d) ........................................ 25\nFed. R. Civ. "
           "P. 54(c) ........................................ 32\n")

    def test_each_rule_links_as_a_rule(self):
        links = _links(self.TOA)
        self.assertEqual([a for _t, a in links if a[0] != "browse"], [
            ("rule", "frap:43:c"), ("rule", "frcp:25:d"),
            ("rule", "frcp:54:c"),
        ])
        self.assertFalse([t for t, a in links if a[0] == "cite"])

    def test_a_rules_service_is_still_a_reporter(self):
        for cite in ("45 Fed. R. Serv. 3d 1234", "12 Fed. R. Evid. Serv. 1234"):
            with self.subTest(cite=cite):
                self.assertEqual(_links(cite), [(cite, ("cite", cite))])


class EnglishReportsTests(unittest.TestCase):
    def test_a_college_is_no_english_report(self):
        text = ("Adebiyi v. S. Suburban Coll., 98 F.4th 886, 892 (7th Cir. "
                "2024).")
        self.assertEqual(_links(text), [(
            "Adebiyi v. S. Suburban Coll., 98 F.4th 886, 892 (7th Cir. 2024)",
            ("cite", "98 F.4th 886@892"),
        )])


# ---------------------------------------------------------------------------
# Unpublished opinions cited by Westlaw number (RECAP)
# ---------------------------------------------------------------------------

class UnpublishedOpinionTests(unittest.TestCase):
    """Each opinion is named by its own caption, and its link covers the
    whole citation — name, docket, number, pin and parenthetical."""

    STRING = (
        "which involved a § 1983 due process claim, not a § 1981 claim. 902 "
        "F.3d at 732. See Sizyuk v. Purdue Univ., No. 4:20-cv-75, 2024 WL "
        "68282 (N.D. Ind. Jan. 5, 2024), Doe v. Purdue Univ., No. 4:18-cv-89, "
        "2019 WL 1369348 (N.D. Ind. Mar. 25, 2019); Williams v. Ne. Illinois "
        "Univ., No. 23-cv-03961, 2024 WL 2959504 (N.D. Ill. June 11, 2024); "
        "Reinebold v. Indiana Univ. at S. Bend, No. 3:18-cv-525, 2019 WL "
        "1897288 (N.D. Ind. Apr. 25, 2019); Higgins v. Lake Cnty. Cir. Ct. "
        "Clerk’s Off., No. 17-cv-07637, 2025 WL 3683058 (N.D. Ill. Dec. 18, "
        "2025). But see Smith v. Illinois Dep’t of Corr., No. 24-cv-5022, "
        "2025 WL 744098, at *4 (N.D. Ill. Mar. 7, 2025) (considering and "
        "rejecting this argument)."
    )
    PROSE = (
        "Hoffman, a white woman who had formerly worked in Blugold Beginnings, "
        "to a leadership role in the new MSS department. The court described "
        "Hoffman’s case in detail in Hoffman v. Board of Regents of the "
        "University of Wisconsin System, No. 23-cv-853-jdp, 2025 WL 1504376 "
        "(W.D. Wis. May 27, 2025). To briefly summarize, the claims were "
        "barred. Hoffman, 2025 WL 1504376, at *4–*5; Melgaard v. Wisconsin "
        "Dep’t of Nat. Res., No. 24-cv-561-jdp, 2025 WL 3268370 (W.D. Wis. "
        "Nov. 24, 2025); Melgaard, 2025 WL 3268370 at *4."
    )

    def test_each_opinion_in_a_string_cite_has_its_own_name(self):
        got = _recap(self.STRING)
        self.assertEqual([spec["name"] for _t, spec in got], [
            "Sizyuk v. Purdue Univ.",
            "Doe v. Purdue Univ.",
            "Williams v. Ne. Illinois Univ.",
            "Reinebold v. Indiana Univ. at S. Bend",
            "Higgins v. Lake Cnty. Cir. Ct. Clerk’s Off.",
            "Smith v. Illinois Dep’t of Corr.",
        ])
        self.assertEqual([spec["docket"] for _t, spec in got], [
            "4:20-cv-75", "4:18-cv-89", "23-cv-03961", "3:18-cv-525",
            "17-cv-07637", "24-cv-5022",
        ])
        self.assertEqual(got[0][0], "Sizyuk v. Purdue Univ., No. 4:20-cv-75, "
                                    "2024 WL 68282 (N.D. Ind. Jan. 5, 2024)")
        self.assertEqual(got[-1][0],
                         "Smith v. Illinois Dep’t of Corr., No. 24-cv-5022, "
                         "2025 WL 744098, at *4 (N.D. Ill. Mar. 7, 2025)")

    def test_prose_before_the_caption_is_no_part_of_the_name(self):
        text, spec = _recap(self.PROSE)[0]
        caption = ("Hoffman v. Board of Regents of the University of "
                   "Wisconsin System")
        self.assertEqual(spec["name"], caption)
        self.assertEqual(text, caption + ", No. 23-cv-853-jdp, 2025 WL "
                                         "1504376 (W.D. Wis. May 27, 2025)")

    def test_a_short_form_links_its_name_and_pin_and_opens_the_case(self):
        got = _recap(self.PROSE)
        self.assertEqual([t for t, _s in got], [
            "Hoffman v. Board of Regents of the University of Wisconsin "
            "System, No. 23-cv-853-jdp, 2025 WL 1504376 (W.D. Wis. May 27, "
            "2025)",
            "Hoffman, 2025 WL 1504376, at *4–*5",
            "Melgaard v. Wisconsin Dep’t of Nat. Res., No. 24-cv-561-jdp, "
            "2025 WL 3268370 (W.D. Wis. Nov. 24, 2025)",
            "Melgaard, 2025 WL 3268370 at *4",
        ])
        # A short form's spec is its full citation's.
        self.assertEqual(got[1][1], got[0][1])
        self.assertEqual(got[3][1], got[2][1])

    def test_a_signal_or_the_sentence_before_is_no_part_of_the_name(self):
        text = (
            "Far from “requir[ing] the payment of funds from the state’s "
            "treasury,” Lenea, 882 F.2d at 1178 (emphasis added), the damages "
            "Diaz seeks cannot possibly be assessed against Wisconsin. See "
            "Tanner v. Bd. of Trs. of Univ. of Ill., 2018 WL 1161140, at *9 "
            "(C.D. Ill. Mar. 5, 2018). Unless the claim sounds in contract. "
            "See, e.g., Sizyuk v. Purdue Univ., 2024 WL 68282, at *4 (N.D. "
            "Ind. Jan. 5, 2024)."
        )
        got = _recap(text)
        self.assertEqual([spec["name"] for _t, spec in got], [
            "Tanner v. Bd. of Trs. of Univ. of Ill.", "Sizyuk v. Purdue Univ.",
        ])
        self.assertEqual(got[1][0], "Sizyuk v. Purdue Univ., 2024 WL 68282, "
                                    "at *4 (N.D. Ind. Jan. 5, 2024)")

    def test_a_table_entry_is_named_by_itself_not_the_prose_after(self):
        text = (
            "Hafer v. Melo, 502 U.S. 21 (1991) ........ 17, 19, 29, 30, 31\n"
            "Haynes v. Ind. Univ., 2017 WL 3243895 (S.D. Ind. July 31, 2017) "
            "........ 21\n"
            "…Haynes, 902 F.3d at 732. Although Haynes’s complaint also sought "
            "compensatory damages, Complaint at 20, Haynes v. Ind. Univ., 2017 "
            "WL 3243895 (S.D. Ind. July 31, 2017), that fact played no role."
        )
        got = _recap(text)
        self.assertEqual({spec["name"] for _t, spec in got},
                         {"Haynes v. Ind. Univ."})
        self.assertEqual(got[-1][0], "Haynes v. Ind. Univ., 2017 WL 3243895 "
                                     "(S.D. Ind. July 31, 2017)")

    def test_download_cited_cases_names_each_opinion(self):
        auths = [a for a in brief_compiler.collect_authorities(
            self.STRING + " " + self.PROSE) if a.kind == "recap"]
        self.assertEqual([a.name for a in auths], [
            "Sizyuk v. Purdue Univ.",
            "Doe v. Purdue Univ.",
            "Williams v. Ne. Illinois Univ.",
            "Reinebold v. Indiana Univ. at S. Bend",
            "Higgins v. Lake Cnty. Cir. Ct. Clerk’s Off.",
            "Smith v. Illinois Dep’t of Corr.",
            "Hoffman v. Board of Regents of the University of Wisconsin System",
            "Melgaard v. Wisconsin Dep’t of Nat. Res.",
        ])


# ---------------------------------------------------------------------------
# Case names
# ---------------------------------------------------------------------------

class CaseNameTests(unittest.TestCase):
    def test_a_particle_set_in_lower_case(self):
        for text, name in (
            ("violates a clearly established federal right. Ashcroft v. "
             "al-Kidd, 563 U.S. 731, 735 (2011). Schmidt",
             "Ashcroft v. al-Kidd"),
            ("to plead around sovereign immunity. See Idaho v. Coeur d’Alene "
             "Tribe of Idaho, 521 U.S. 261, 270 (1997). Diaz",
             "Idaho v. Coeur d’Alene Tribe of Idaho"),
        ):
            with self.subTest(name=name):
                (linked, _action), = _links(text)
                self.assertTrue(linked.startswith(name + ", "), linked)

    def test_the_name_is_read_in_the_type_the_brief_sets_it_in(self):
        text = ("right. Ashcroft v. al-Kidd, 563 U.S. 731, 735 (2011). The")
        italic = [False] * len(text)
        at = text.index("Ashcroft")
        for i in range(at, at + len("Ashcroft v. al-Kidd")):
            italic[i] = True
        (linked, _action), = _links(text, italic)
        self.assertTrue(linked.startswith("Ashcroft v. al-Kidd, "), linked)

    def test_a_long_bluebook_abbreviation(self):
        text = ("Edelman v. Jordan, 415 U.S. 651, 664 (1974); MCI Telecomms. "
                "Corp. v. Ill. Bell Tel. Co., 222 F.3d 323, 337 (7th Cir. "
                "2000).")
        self.assertEqual([t for t, _a in _links(text)][-1],
                         "MCI Telecomms. Corp. v. Ill. Bell Tel. Co., 222 "
                         "F.3d 323, 337 (7th Cir. 2000)")

    def test_a_campus_named_with_at(self):
        text = ("Nor has Schmidt pointed to any contract. Reinebold v. Ind. "
                "Univ. at S. Bend, 123 F. Supp. 3d 456, 460 (N.D. Ind. 2019).")
        (linked, _action), = _links(text)
        self.assertTrue(linked.startswith("Reinebold v. Ind. Univ. at S. "
                                          "Bend, "), linked)

    def test_a_prefix_in_prose_is_still_prose(self):
        text = ("That was a pre-Miranda confession, Smith v. Jones, 123 F.3d "
                "456 (2d Cir. 1997).")
        (linked, _action), = _links(text)
        self.assertEqual(linked, "Smith v. Jones, 123 F.3d 456 (2d Cir. 1997)")


# ---------------------------------------------------------------------------
# The same, read off a PDF
# ---------------------------------------------------------------------------

def _pdf(*pages: str) -> bytes:
    """A Helvetica PDF with one page per raw content stream in *pages*."""
    n = len(pages)
    font = 3 + 2 * n
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[%s]/Count %d>>" % (
            b" ".join(b"%d 0 R" % (3 + 2 * i) for i in range(n)), n),
    ]
    for i, content in enumerate(pages):
        stream = content.encode("latin-1")
        objects.append(
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            b"/Resources<</Font<</F1 %d 0 R>>>>/Contents %d 0 R>>"
            % (font, 4 + 2 * i))
        objects.append(b"<</Length %d>>stream\n" % len(stream) + stream
                       + b"\nendstream")
    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
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


def _line(x: int, y: int, text: str, size: int = 12) -> str:
    return "BT /F1 %d Tf %d %d Td (%s) Tj ET" % (size, x, y, text)


# A citation broken across a page, the way the brief's pages are drawn: the
# stamp across the top of each page, drawn last; the page number at the foot,
# drawn first.
_BROKEN = _pdf(
    "\n".join([
        _line(72, 700, "has the identical effect as a suit against the "
                       "state. Luder v. Endicott, 253"),
        _line(150, 770, STAMP, 9),
    ]),
    "\n".join([
        _line(300, 40, "20"),
        _line(72, 720, "F.3d 1020, 1023 \\(7th Cir. 2001\\). The same is "
                       "true here."),
        _line(150, 770, STAMP, 9),
    ]),
)

_POSTPONED = __future__.annotations.compiler_flag


def _load_gui(*names, consts=()):
    """Lift *names* (and the assignments to *consts*) out of courtlistener_gui
    without importing it."""
    src = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text()
    tree = ast.parse(src)
    ns = {"re": re, "_PDFIUM_LOCK": threading.RLock(),
          "brief_reader": brief_reader,
          "detect_brief_links": citations.detect_links,
          "_scan_text": citations.scan_text,
          "_page_furniture": citations.page_furniture}
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
class PdfPageBreakTests(unittest.TestCase):
    """The viewer links a citation broken across a page as one citation, on
    both pages, and draws nothing over the page's furniture."""

    @classmethod
    def setUpClass(cls):
        cls.ns = _load_gui(
            "_degenerate_ocr_metrics", "_repair_degenerate_ocr_page",
            "_PageSlants", "_union_line_runs", "_extract_pdf_text_and_style",
            "_citation_links_from_pages",
            consts=("_FONT_FLAG_ITALIC",))
        cls.pages, cls.italics = cls.ns["_extract_pdf_text_and_style"](
            _BROKEN)
        cls.links = cls.ns["_citation_links_from_pages"](
            cls.pages, cls.italics)

    def test_one_citation_on_both_pages(self):
        self.assertEqual(sorted(self.links), [0, 1])
        for page in (0, 1):
            with self.subTest(page=page):
                self.assertEqual(
                    {a for _r, a, _s in self.links[page]},
                    {("cite", "253 F.3d 1020@1023")})
        snippets = {s for page in self.links.values() for _r, _a, s in page}
        self.assertEqual(snippets, {
            "Luder v. Endicott, 253 F.3d 1020, 1023 (7th Cir. 2001)"})

    def test_nothing_is_drawn_over_the_stamp_or_the_page_number(self):
        for page, runs in self.links.items():
            for rect, _action, _snippet in runs:
                with self.subTest(page=page, rect=rect):
                    top = max(rect[1], rect[3])
                    bottom = min(rect[1], rect[3])
                    self.assertLess(top, 760)      # the stamp is at 770
                    self.assertGreater(bottom, 60)  # the number is at 40

    def test_the_page_keeps_its_furniture(self):
        # Find, selection and copying read the page, not the scan's text.
        first = "".join(ch for ch, _bx in self.pages[0])
        second = "".join(ch for ch, _bx in self.pages[1])
        self.assertIn("Document: 11", first)
        self.assertTrue(second.lstrip().startswith("20"))


if __name__ == "__main__":
    unittest.main()
