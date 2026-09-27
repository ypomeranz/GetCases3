"""The U.S. Code keeps the OLRC page's own indentation.

The OLRC stylesheet gives every paragraph class its margin — a heading the
margin of its level (paragraph-head 1em, subparagraph-head 2em, …), body text
the number in its class (statutory-body-2em, statutory-body-block-2em) — and
the reader shows each paragraph there.  That is the printed Code's layout: in
an older section a subsection's paragraphs sit flush with it, and a "(2)"
after "(d)(1)" is flush too.  The subdivision each paragraph belongs to —
what a pin cite jumps to and Copy + Cite cites — is read from the same layout
(``us_code.statute_paths``).

The fixtures are the OLRC's own markup and text, cut down, from the sections
named in each test (September 2026).
"""

import pathlib
import unittest

import us_code
from us_code import _class_depth, parse_section, statute_paths


def statute(*paras: str) -> str:
    return ("<!-- field-start:statute -->\n" + "\n".join(paras)
            + "\n<!-- field-end:statute -->")


def p(cls: str, text: str) -> str:
    return f'<p class="{cls}">{text}</p>'


def h(cls: str, text: str) -> str:
    return f'<h4 class="{cls}">{text}</h4>'


def read(page: str) -> list[tuple[int, tuple[str, ...], str]]:
    """(depth, subdivision, opening words) per statute paragraph."""
    paras = parse_section(page)
    return [(depth, path, " ".join(text.split()[:2]))
            for (kind, depth, text), path in zip(paras, statute_paths(paras))
            if kind in ("body", "head")]


def paths(page: str) -> list[tuple[str, ...]]:
    return [path for _depth, path, _words in read(page)]


def depths(page: str) -> list[int]:
    return [depth for depth, _path, _words in read(page)]


class ClassDepthTests(unittest.TestCase):
    def test_headings_sit_at_their_levels_margin(self):
        for cls, depth in (("subsection-head", 0), ("paragraph-head", 1),
                           ("subparagraph-head", 2), ("clause-head", 3),
                           ("subclause-head", 4), ("subsubclause-head", 5)):
            with self.subTest(cls=cls):
                self.assertEqual(_class_depth(cls), depth)

    def test_body_text_at_the_margin_its_class_names(self):
        for cls, depth in (("statutory-body", 0), ("statutory-body-3em", 3),
                           ("statutory-body-6em", 6),
                           ("statutory-body-block", 0),
                           ("statutory-body-block-1em", 1),
                           ("statutory-body-block-2em", 2),
                           ("statutory-body-block-4em", 4),
                           ("statutory-body-block-2em-right", 2),
                           ("tableftnt", 0), ("note-body-2em", 2),
                           ("usc28aForm-left", 4)):
            with self.subTest(cls=cls):
                self.assertEqual(_class_depth(cls), depth)

    def test_a_hanging_paragraph_starts_where_its_first_line_does(self):
        self.assertEqual(_class_depth("statutory-body-flush2_hang3"), 2)
        self.assertEqual(_class_depth("statutory-body-flush0_hang2"), 0)
        self.assertEqual(_class_depth("note-body-flush3_hang4"), 3)

    def test_a_class_the_parser_has_not_seen_is_kept(self):
        # Formerly an unknown class dropped its paragraph from the text.
        page = statute(p("statutory-body-7em", "(AAA) a deep item;"))
        self.assertEqual(read(page), [(6, ("AAA",), "(AAA) a")])


class HeadedSectionTests(unittest.TestCase):
    """26 U.S.C. § 36B(b)(3): headings at every level, items (aa), and
    flush text closing a list."""

    PAGE = statute(
        h("subsection-head", "(b) Premium assistance credit amount"),
        p("statutory-body", "For purposes of this section-"),
        h("paragraph-head", "(3) Other terms and rules"),
        h("subparagraph-head", "(B) Applicable second lowest cost silver plan"),
        p("statutory-body-2em", "The applicable second lowest cost silver "
          "plan is the plan which-"),
        p("statutory-body-3em", "(ii) provides-"),
        p("statutory-body-4em", "(I) self-only coverage in the case of an "
          "applicable taxpayer-"),
        p("statutory-body-5em", "(aa) whose tax for the taxable year is "
          "determined under section 1(c)"),
        p("statutory-body-5em", "(bb) who is not described in item (aa)"),
        p("statutory-body-4em", "(II) family coverage in the case of any "
          "other applicable taxpayer."),
        p("statutory-body-block-2em", "If a taxpayer files a joint return "
          "and no credit is allowed under this section"),
        h("subparagraph-head", "(C) Adjusted monthly premium"),
        h("clause-head", "(i) In general"),
        p("statutory-body-3em", "Except as provided in clause (ii), the "
          "applicable percentage"),
    )

    def test_each_paragraph_where_the_page_puts_it(self):
        self.assertEqual(depths(self.PAGE),
                         [0, 0, 1, 2, 2, 3, 4, 5, 5, 4, 2, 2, 3, 3])

    def test_and_in_the_subdivision_it_belongs_to(self):
        B = ("b", "3", "B")
        self.assertEqual(paths(self.PAGE), [
            ("b",), ("b",), ("b", "3"), B, B, B + ("ii",),
            B + ("ii", "I"), B + ("ii", "I", "aa"), B + ("ii", "I", "bb"),
            B + ("ii", "II"),
            # the flush text closing the list is subparagraph (B)'s own
            B,
            ("b", "3", "C"), ("b", "3", "C", "i"), ("b", "3", "C", "i"),
        ])


class PrintedCodeLayoutTests(unittest.TestCase):
    def test_a_subsections_paragraphs_sit_flush_with_it(self):
        # 5 U.S.C. § 552(a): "(a)", "(1)", "(2)" at one margin, "(A)" in.
        page = statute(
            p("statutory-body", "(a) Each agency shall make available to "
              "the public information as follows:"),
            p("statutory-body", "(1) Each agency shall separately state"),
            p("statutory-body-1em", "(A) descriptions of its central and "
              "field organization"),
            p("statutory-body-1em", "(B) statements of the general course"),
            p("statutory-body", "Except to the extent that a person has "
              "actual and timely notice"),
            p("statutory-body", "(2) Each agency, in accordance with "
              "published rules, shall make available"),
            p("statutory-body", "(b) This section does not apply to matters"),
        )
        self.assertEqual(depths(page), [0, 0, 1, 1, 0, 0, 0])
        self.assertEqual(paths(page), [
            ("a",), ("a", "1"), ("a", "1", "A"), ("a", "1", "B"),
            ("a", "1"), ("a", "2"), ("b",),
        ])

    def test_after_d1_a_flush_2_replaces_the_1(self):
        # 18 U.S.C. § 1030(c)-(e).
        page = statute(
            p("statutory-body", "(c) The punishment for an offense is-"),
            p("statutory-body-1em", "(1)(A) a fine under this title or "
              "imprisonment for not more than ten years"),
            p("statutory-body-1em", "(B) a fine under this title or "
              "imprisonment for not more than twenty years"),
            p("statutory-body-1em", "(2)(A) except as provided in "
              "subparagraph (B), a fine"),
            p("statutory-body-2em", "(i) the offense was committed for "
              "purposes of commercial advantage"),
            p("statutory-body", "(d)(1) The United States Secret Service "
              "shall have the authority"),
            p("statutory-body", "(2) The Federal Bureau of Investigation "
              "shall have primary authority"),
            p("statutory-body", "(3) Such authority shall be exercised"),
            p("statutory-body", "(e) As used in this section-"),
        )
        self.assertEqual(depths(page), [0, 1, 1, 1, 2, 0, 0, 0, 0])
        self.assertEqual(paths(page), [
            ("c",), ("c", "1", "A"), ("c", "1", "B"), ("c", "2", "A"),
            ("c", "2", "A", "i"), ("d", "1"), ("d", "2"), ("d", "3"), ("e",),
        ])

    def test_a_whole_section_set_one_em_in(self):
        # 15 U.S.C. § 78j: subsections at 1em, closing text at the margin.
        page = statute(
            p("statutory-body", "It shall be unlawful for any person-"),
            p("statutory-body-1em", "(a)(1) To effect a short sale"),
            p("statutory-body-1em", "(2) Paragraph (1) of this subsection "
              "shall not apply"),
            p("statutory-body-1em", "(b) To use or employ any manipulative "
              "or deceptive device"),
            p("statutory-body-block", "Rules promulgated under subsection "
              "(b) that prohibit fraud"),
        )
        self.assertEqual(paths(page), [
            (), ("a", "1"), ("a", "2"), ("b",), (),
        ])

    def test_closing_text_of_a_section_cites_the_section(self):
        # 17 U.S.C. § 107.
        page = statute(
            p("statutory-body", "In determining whether the use made of a "
              "work is a fair use the factors shall include-"),
            p("statutory-body-1em", "(1) the purpose and character of the use"),
            p("statutory-body-1em", "(4) the effect of the use upon the "
              "potential market"),
            p("statutory-body-block", "The fact that a work is unpublished "
              "shall not itself bar a finding of fair use"),
        )
        self.assertEqual(paths(page), [(), ("1",), ("4",), ()])


class ListResumedAfterFlushTextTests(unittest.TestCase):
    def test_a_clauses_own_list_after_its_flush_text(self):
        # 5 U.S.C. § 552(a)(6)(A)(ii)(I), cited as such: the "(I)" follows
        # the clause's own text, printed flush.
        page = statute(
            p("statutory-body", "(6)(A) Each agency, upon any request for "
              "records made under paragraph (1), (2), or (3), shall-"),
            p("statutory-body-1em", "(i) determine within 20 days"),
            p("statutory-body-1em", "(ii) make a determination with respect "
              "to any appeal within twenty days"),
            p("statutory-body-block", "The 20-day period shall not be tolled "
              "by the agency except-"),
            p("statutory-body-1em", "(I) that the agency may make one "
              "request to the requester"),
            p("statutory-body-1em", "(II) if necessary to clarify with the "
              "requester issues regarding fee assessment."),
            p("statutory-body", "(B)(i) In unusual circumstances"),
        )
        self.assertEqual(paths(page), [
            ("6", "A"), ("6", "A", "i"), ("6", "A", "ii"), ("6", "A"),
            ("6", "A", "ii", "I"), ("6", "A", "ii", "II"), ("6", "B", "i"),
        ])


class LabelsThePageMarginSettlesTests(unittest.TestCase):
    def test_a_subsection_i_after_h(self):
        # 20 U.S.C. § 1415(h)-(i): "(i)" is the next subsection, not a
        # clause of (h)(4)(B) — the page's own anchors get this wrong.
        page = statute(
            h("subsection-head", "(h) Safeguards"),
            h("paragraph-head", "(4) the right to have"),
            h("subparagraph-head", "(B) Written findings"),
            h("subsection-head", "(i) Administrative procedures"),
            h("paragraph-head", "(1) In general"),
            h("subparagraph-head", "(A) Decision made in hearing"),
        )
        self.assertEqual(paths(page), [
            ("h",), ("h", "4"), ("h", "4", "B"), ("i",), ("i", "1"),
            ("i", "1", "A"),
        ])

    def test_items_a_and_b_under_a_subclause(self):
        # 8 U.S.C. § 1182(a)(3)(B)(iii)(V): items "(a)", "(b)" at 5em — after
        # which the page's anchors restart as if at subsection (a).
        page = statute(
            p("statutory-body-3em", "(iii) \"Terrorist activity\" defined"),
            p("statutory-body-4em", "(V) The use of any-"),
            p("statutory-body-5em", "(a) biological agent, chemical agent, "
              "or nuclear weapon or device, or"),
            p("statutory-body-5em", "(b) explosive, firearm, or other weapon"),
            p("statutory-body-block-4em", "with intent to endanger the safety "
              "of one or more individuals"),
            p("statutory-body-4em", "(VI) A threat, attempt, or conspiracy "
              "to do any of the foregoing."),
        )
        self.assertEqual(paths(page), [
            ("iii",), ("iii", "V"), ("iii", "V", "a"), ("iii", "V", "b"),
            ("iii", "V"), ("iii", "VI"),
        ])

    def test_a_subitem_heading_at_its_items_margin(self):
        # 26 U.S.C. § 7701(a)(51)(D)(ii)(III)(bb): the page has no deeper
        # heading class, so "(AA)" shares the item heading's 5em.
        page = statute(
            h("subclause-head", "(III) Special rule"),
            h("subsubclause-head", "(bb) Exception"),
            h("subsubclause-head", "(AA) In general"),
            p("statutory-body-6em", "Item (aa) shall not apply in the case of "
              "a bona fide purchase"),
            h("subsubclause-head", "(BB) Bona fide purchase or sale"),
        )
        self.assertEqual(paths(page), [
            ("III",), ("III", "bb"), ("III", "bb", "AA"),
            ("III", "bb", "AA"), ("III", "bb", "BB"),
        ])

    def test_a_heading_styled_deeper_than_its_siblings(self):
        # 29 U.S.C. § 1132(c)(13): "(A)", "(B)" body-styled, "(C)" a heading.
        page = statute(
            p("statutory-body", "(13) Secretarial enforcement authority"),
            p("statutory-body-1em", "(A) Failure to provide information.-"),
            p("statutory-body-2em", "The Secretary may impose a penalty"),
            p("statutory-body-1em", "(B) False information.-"),
            p("statutory-body-2em", "The Secretary may impose a penalty"),
            h("subparagraph-head", "(C) Waivers.-"),
            p("statutory-body-2em", "The Secretary may waive penalties"),
        )
        self.assertEqual(paths(page), [
            ("13",), ("13", "A"), ("13", "A"), ("13", "B"), ("13", "B"),
            ("13", "C"), ("13", "C"),
        ])

    def test_inserted_paragraphs_and_a_combined_opening(self):
        # 11 U.S.C. § 101: "(4A)" between "(4)" and "(5)", and "(B)(i)".
        page = statute(
            p("statutory-body-1em", "(4) The term \"attorney\" means"),
            p("statutory-body-1em", "(4A) The term \"bankruptcy "
              "assistance\" means"),
            p("statutory-body-1em", "(10A) The term \"current monthly "
              "income\"-"),
            p("statutory-body-2em", "(A) means the average monthly income"),
            p("statutory-body-2em", "(B)(i) includes any amount paid"),
            p("statutory-body-2em", "(ii) excludes-"),
            p("statutory-body-3em", "(I) benefits received under the Social "
              "Security Act;"),
            p("statutory-body-1em", "(11) The term \"custodian\" means-"),
        )
        self.assertEqual(paths(page), [
            ("4",), ("4A",), ("10A",), ("10A", "A"), ("10A", "B", "i"),
            ("10A", "B", "ii"), ("10A", "B", "ii", "I"), ("11",),
        ])


class NotStatuteTextTests(unittest.TestCase):
    def test_heading_credit_and_notes_belong_to_no_subdivision(self):
        page = ('<!-- field-start:head --><h3 class="section-head">'
                "§552. Public information</h3><!-- field-end:head -->"
                + statute(p("statutory-body", "(a) Each agency shall"))
                + '<!-- field-start:sourcecredit --><p class="source-credit">'
                "(Pub. L. 89-554.)</p><!-- field-end:sourcecredit -->"
                '<!-- field-start:notes --><h4 class="note-head">Amendments'
                '</h4><p class="statutory-body">(b) quoted text</p>'
                "<!-- field-end:notes -->")
        paras = parse_section(page)
        self.assertEqual(
            [(kind, path) for (kind, _d, _t), path
             in zip(paras, statute_paths(paras))],
            [("sechead", ()), ("body", ("a",)), ("credit", ()),
             ("note-head", ()), ("note-body", ())])


class ViewerTests(unittest.TestCase):
    """The statute viewer reads the U.S. Code's subdivisions from
    statute_paths rather than from the indent — which in the U.S. Code is
    the page's, not a depth."""

    SRC = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text(
        encoding="utf-8")

    @classmethod
    def render_source(cls) -> str:
        import ast

        tree = ast.parse(cls.SRC)
        window = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef)
                      and node.name == "_StatuteWindow")
        method = next(node for node in window.body
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "_render")
        return ast.get_source_segment(cls.SRC, method)

    def test_the_viewer_asks_statute_paths_for_the_us_code(self):
        render = self.render_source()
        self.assertIn("us_code.statute_paths(self._doc.paras)", render)
        self.assertIn('if self._doc.kind == "usc"', render)
        # every statute paragraph gets an anchor, flush text included, so
        # Copy + Cite of flush text cites the subdivision it belongs to
        self.assertIn("self._anchors.append((txt.index(\"end-1c\"), "
                      "para_path))", render)

    def test_the_old_relevel_is_gone(self):
        self.assertFalse(hasattr(us_code, "_relevel_statute"))
        self.assertFalse(hasattr(us_code, "USC_HIERARCHY"))


if __name__ == "__main__":
    unittest.main()
