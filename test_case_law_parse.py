"""Reading a static.case.law case into the shape the opinion viewer renders.

The Caselaw Access Project publishes each case three ways under one file name:
a PDF of the printed page, a JSON file holding the metadata and a flattened
body, and an HTML file holding the report itself.  Only the HTML knows where
the reporter's pages break, which paragraphs are the head matter, which
opinion is the dissent and which sentence each footnote hangs off — so that is
what :mod:`case_law_parse` reads, falling back to the JSON's paragraphs for a
case whose HTML will not load.

These tests drive the parser against the markup CAP actually publishes.  The
module's own ``__main__`` block covers the ordinary case end to end; what is
here is the awkward middle — bylines split across two lines, notes printed
under the page they interrupt, a page label CAP repeats, head matter that
carries footnotes of its own, and files with pieces missing.
"""

import unittest

from case_law_parse import parse_case_law_html, parse_case_law_json


def _wrap(head: str = "", *articles: str) -> str:
    """A casebody around the pieces a test cares about."""
    head_html = f'<section class="head-matter">{head}</section>' if head else ""
    return (
        '<section class="casebody" data-firstpage="1" data-lastpage="9">'
        + head_html + "".join(articles) + "</section>"
    )


def _article(body: str, kind: str = "majority") -> str:
    return f'<article class="opinion" data-type="{kind}">{body}</article>'


def _pages(parts) -> list:
    return [s.text for p in parts for b in p.blocks for s in b.spans
            if s.pagenum]


def _texts(part) -> list:
    return [b.text() for b in part.blocks]


class HeadMatterTests(unittest.TestCase):
    """The material the volume prints before the opinion begins."""

    def test_the_parties_line_is_centred_so_the_caption_can_be_read(self):
        parts, _ = parse_case_law_html(_wrap(
            '<h4 class="parties">SMITH <em>v. </em>JONES</h4>'
            '<p class="docketnumber">No. 21910.</p>'
            '<p class="court">United States Court of Appeals.</p>',
            _article("<p>Affirmed.</p>"),
        ))
        head = parts[0]
        self.assertEqual(head.kind, "header")
        self.assertEqual([b.kind for b in head.blocks],
                         ["center", "center", "center"])
        self.assertEqual(head.blocks[0].text(), "SMITH v. JONES")
        self.assertTrue(head.blocks[0].spans[0].bold)

    def test_counsel_and_the_panel_stay_running_prose(self):
        parts, _ = parse_case_law_html(_wrap(
            '<h4 class="parties">SMITH v. JONES</h4>'
            '<p class="attorneys">Mr. John Donovan, for appellants.</p>'
            '<p>Before Wright, Tamm and Robinson, Circuit Judges.</p>',
            _article("<p>Affirmed.</p>"),
        ))
        self.assertEqual([b.kind for b in parts[0].blocks],
                         ["center", "para", "para"])

    def test_head_matter_carries_its_own_footnotes(self):
        # The star footnote on the list of amici in Roe hangs off the head
        # matter, not off any opinion.
        parts, _ = parse_case_law_html(_wrap(
            '<h4 class="parties">SMITH v. JONES</h4>'
            '<p class="attorneys">Mr. Donovan.'
            '<a class="footnotemark" href="#footnote_0_1"><em>*</em></a></p>'
            '<aside data-label="*" class="footnote" id="footnote_0_1">'
            '<a href="#ref_footnote_0_1">*</a><p>Briefs of amici curiae.</p>'
            "</aside>",
            _article("<p>Affirmed.</p>"),
        ))
        head = parts[0]
        self.assertEqual(len(head.footnotes), 1)
        self.assertEqual(head.footnotes[0].spans[0].fndef, "footnote_0_1")
        self.assertEqual(head.footnotes[0].text(), "* Briefs of amici curiae.")
        marks = [s for b in head.blocks for s in b.spans if s.fnref]
        self.assertEqual([(m.text, m.sup) for m in marks], [("*", True)])

    def test_a_case_printed_with_no_head_matter_still_parses(self):
        parts, blocks = parse_case_law_html(_wrap(
            "", _article('<p class="author">PER CURIAM.</p><p>Affirmed.</p>')))
        self.assertEqual([p.kind for p in parts], ["majority"])
        self.assertTrue(blocks)


class OpinionPartTests(unittest.TestCase):
    """One part per writing, labelled the way the Bluebook parenthetical
    reads labels."""

    def test_each_writing_becomes_its_own_part(self):
        parts, _ = parse_case_law_html(_wrap(
            '<h4 class="parties">SMITH v. JONES</h4>',
            _article('<p class="author">HALE, C.J.</p><p>Affirmed.</p>'),
            _article('<p class="author">TAMM, J., concurring.</p><p>I agree.</p>',
                     "concurrence"),
            _article('<p class="author">ROE, J., dissenting.</p><p>I do not.</p>',
                     "dissent"),
        ))
        self.assertEqual([p.kind for p in parts],
                         ["header", "majority", "concurrence", "dissent"])
        self.assertEqual([p.label for p in parts[1:]], [
            "Opinion (HALE, C.J.)",
            "Concurrence (TAMM, J., concurring)",
            "Dissent (ROE, J., dissenting)",
        ])

    def test_a_partial_concurrence_reads_as_a_concurrence(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            '<p class="author">TAMM, J.</p><p>In part.</p>',
            "concurring-in-part-and-dissenting-in-part")))
        self.assertEqual(parts[0].kind, "concurrence")
        self.assertEqual(parts[0].label, "Concurrence in Part (TAMM, J.)")

    def test_an_unfamiliar_type_is_read_as_the_main_opinion(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            "<p>On the motion.</p>", "on-motion-to-strike-cost-bill")))
        self.assertEqual(parts[0].kind, "majority")
        self.assertEqual(parts[0].label, "On Motion To Strike Cost Bill")

    def test_a_byline_the_scan_broke_in_two_is_joined_back_up(self):
        # CAP sets the author on its own line, so the role word that follows
        # it lands in the next paragraph — where nothing would read it.
        parts, _ = parse_case_law_html(_wrap("", _article(
            '<p class="author">Mr. Justice Stewart,</p>'
            "<p>concurring.</p><p>In 1963, this Court held.</p>",
            "concurrence")))
        self.assertEqual(parts[0].blocks[0].text(),
                         "Mr. Justice Stewart, concurring.")
        self.assertEqual(len(parts[0].blocks), 2)
        # The label still names the writer alone, not the whole first line.
        self.assertEqual(parts[0].label, "Concurrence (Mr. Justice Stewart)")

    def test_a_byline_that_ends_a_sentence_is_left_alone(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            '<p class="author">J. SKELLY WRIGHT, Circuit Judge:</p>'
            "<p>This case arises.</p>")))
        self.assertEqual(_texts(parts[0]),
                         ["J. SKELLY WRIGHT, Circuit Judge:",
                          "This case arises."])

    def test_nor_is_a_new_sentence_pulled_up_into_the_byline(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            '<p class="author">PER CURIAM</p><p>The judgment is affirmed.</p>')))
        self.assertEqual(_texts(parts[0]),
                         ["PER CURIAM", "The judgment is affirmed."])


class ReporterPageTests(unittest.TestCase):
    """The page breaks the JSON has no room for."""

    def test_page_labels_become_star_pagination(self):
        parts, _ = parse_case_law_html(_wrap(
            '<h4 class="parties">SMITH v. JONES</h4>'
            '<p class="attorneys">'
            '<a class="page-label" data-label="702">*702</a>Mr. Donovan.</p>',
            _article('<p><a class="page-label" data-label="703">*703</a>'
                     "Affirmed.</p>"),
        ))
        self.assertEqual(_pages(parts), ["*702", "*703"])
        # The marker is its own span, so the gutter gets it and the prose does
        # not begin with a stray asterisk.
        first = parts[1].blocks[0].spans
        self.assertTrue(first[0].pagenum)
        self.assertEqual(first[1].text, "Affirmed.")

    def test_a_page_label_is_marked_once_however_often_cap_repeats_it(self):
        parts, _ = parse_case_law_html(_wrap(
            '<p class="judges"><a class="page-label" data-label="116">*116</a>'
            "Blackmun, J., delivered the opinion.</p>",
            _article('<p class="author">'
                     '<a class="page-label" data-label="116">*116</a>'
                     "Mr. Justice Blackmun</p>"
                     "<p>delivered the opinion of the Court.</p>"
                     '<p><a class="page-label" data-label="117">*117</a>'
                     "We have inquired.</p>"),
        ))
        self.assertEqual(_pages(parts), ["*116", "*117"])

    def test_a_note_printed_under_the_page_it_interrupts_keeps_out_of_the_gutter(self):
        # The note carries the label for the page it runs onto; the body
        # carries it too, and the body is where the reader is.
        parts, _ = parse_case_law_html(_wrap("", _article(
            "<p>Text.<a class=\"footnotemark\" href=\"#footnote_1_1\">1</a></p>"
            '<p><a class="page-label" data-label="704">*704</a>More text.</p>'
            '<aside data-label="1" class="footnote" id="footnote_1_1">'
            '<a href="#ref_footnote_1_1">1</a>'
            '<p>. A long note running to <a class="page-label" '
            'data-label="704">*704</a> the next page.</p></aside>')))
        self.assertEqual(_pages(parts), ["*704"])
        self.assertNotIn("*704", parts[0].footnotes[0].text())


class FootnoteTests(unittest.TestCase):
    """Notes attached to the writing that calls them, and clickable."""

    def test_the_marker_and_the_note_share_an_anchor(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            '<p>Text.<a class="footnotemark" href="#footnote_1_2" '
            'id="ref_footnote_1_2">2</a></p>'
            '<aside data-label="2" class="footnote" id="footnote_1_2">'
            '<a href="#ref_footnote_1_2">2</a><p>. Dodd v. Pearson.</p>'
            "</aside>")))
        ref = next(s for b in parts[0].blocks for s in b.spans if s.fnref)
        note = parts[0].footnotes[0]
        self.assertEqual(ref.fnref, note.spans[0].fndef)
        self.assertEqual(note.text(), "2 Dodd v. Pearson.")

    def test_a_note_set_as_several_paragraphs_keeps_its_breaks(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            "<p>Text.</p>"
            '<aside data-label="1" class="footnote" id="footnote_1_1">'
            '<a href="#ref_footnote_1_1">1</a>'
            "<p>. &#8220;Article 1191. Abortion</p>"
            "<p>&#8220;If any person shall administer.</p></aside>")))
        self.assertEqual(
            parts[0].footnotes[0].text(),
            "1 “Article 1191. Abortion\n“If any person shall administer.",
        )

    def test_italics_inside_a_note_survive(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            "<p>Text.</p>"
            '<aside data-label="3" class="footnote" id="footnote_1_3">'
            '<a href="#ref_footnote_1_3">3</a>'
            "<p>. See <em>Griswold</em> v. Connecticut.</p></aside>")))
        note = parts[0].footnotes[0]
        self.assertEqual([s.text for s in note.spans if s.italic],
                         ["Griswold"])

    def test_the_notes_leave_the_body_they_were_printed_in(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            "<p>First.</p>"
            '<aside data-label="1" class="footnote" id="footnote_1_1">'
            '<a href="#r">1</a><p>. A note.</p></aside>'
            "<p>Second.</p>")))
        self.assertEqual(_texts(parts[0]), ["First.", "Second."])
        self.assertEqual(len(parts[0].footnotes), 1)


class BodyFormattingTests(unittest.TestCase):
    def test_quotations_keep_their_indentation(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            "<p>The court said:</p><blockquote>Quoted matter.</blockquote>")))
        self.assertEqual([b.kind for b in parts[0].blocks],
                         ["para", "blockquote"])

    def test_bare_section_markers_become_headings(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            "<p>Text.</p><p>I</p><p>A</p><p>* * *</p><p>More.</p>")))
        self.assertEqual([b.kind for b in parts[0].blocks],
                         ["para", "heading", "heading", "heading", "para"])

    def test_caps_citation_links_are_left_for_the_apps_own_detector(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            '<p>See <a href="/us/410/0113-01" class="citation" '
            'data-cite="410 U.S. 113">410 U. S. 113</a>, 153.</p>')))
        spans = parts[0].blocks[0].spans
        self.assertEqual([s.link for s in spans], [""])
        self.assertEqual(parts[0].blocks[0].text(), "See 410 U. S. 113, 153.")

    def test_illustrations_are_dropped_rather_than_left_as_holes(self):
        parts, _ = parse_case_law_html(_wrap("", _article(
            '<p><img class="p" src="fig.png">Affirmed.</p>')))
        self.assertEqual(_texts(parts[0]), ["Affirmed."])

    def test_a_file_with_no_casebody_yields_nothing(self):
        self.assertEqual(parse_case_law_html("<html><body>x</body></html>"),
                         ([], []))
        self.assertEqual(parse_case_law_html(""), ([], []))
        self.assertEqual(parse_case_law_html(_wrap()), ([], []))


class JsonFallbackTests(unittest.TestCase):
    """The rare case whose HTML will not load: CAP's flattened body, which at
    least keeps its paragraphs and knows a footnote run when it sees one."""

    def test_paragraphs_and_the_footnote_run_are_separated(self):
        parts, _ = parse_case_law_json({
            "head_matter": "SMITH v. JONES.\nNo. 21910.",
            "opinions": [{
                "type": "dissent", "author": "ROE, J.",
                "text": "ROE, J.\nI dissent.\nAnd say why.\n"
                        ". The first note.\n. The second note.",
            }],
        })
        self.assertEqual([p.kind for p in parts], ["header", "dissent"])
        self.assertEqual(parts[0].blocks[0].kind, "center")
        self.assertEqual(parts[1].label, "Dissent (ROE, J.)")
        self.assertEqual(_texts(parts[1]),
                         ["ROE, J.", "I dissent.", "And say why."])
        self.assertEqual([b.text() for b in parts[1].footnotes],
                         ["The first note.", "The second note."])

    def test_an_opinion_of_nothing_but_notes_is_read_as_the_opinion(self):
        # A body whose every line opens with a period is prose that happens to
        # look like notes, not an opinion with no text.
        parts, _ = parse_case_law_json({
            "opinions": [{"type": "majority", "text": ". Affirmed."}],
        })
        self.assertEqual(_texts(parts[0]), [". Affirmed."])
        self.assertEqual(parts[0].footnotes, [])

    def test_there_is_no_pagination_to_invent(self):
        parts, _ = parse_case_law_json({
            "head_matter": "SMITH v. JONES.",
            "opinions": [{"type": "majority", "text": "Affirmed."}],
        })
        self.assertEqual(_pages(parts), [])

    def test_an_empty_body_yields_nothing(self):
        self.assertEqual(parse_case_law_json({}), ([], []))
        self.assertEqual(parse_case_law_json(None), ([], []))
        self.assertEqual(parse_case_law_json({"opinions": [{"text": "  "}]}),
                         ([], []))


if __name__ == "__main__":
    unittest.main()
