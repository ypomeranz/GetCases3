"""State courts' opinions as static.case.law and Google Scholar give them.

* CAP prints some reports' docket, court and date ahead of the caption —
  "[Sac. No. 7096. / In Bank. / Dec. 18, 1959.]" — and the case was named
  for them.  The caption now leads, and such lines are never the name.
* "BEATRICE V. DITTUS, …, Petitioner, v. ALAN CRANSTON" — Dittus v.
  Cranston, 53 Cal. 2d 284 — lost its middle initial to title-casing, which
  made it a "v.": "Beatrice v. Dittus v. Alan Cranston".
* "Article I, § 7" in a state court's opinion is as likely its own
  constitution as the United States', and is no longer linked there.
"""

import unittest

import case_law_parse
from bluebook_names import abbreviate_case_name, normal_case_caption

try:
    import courtlistener_gui as gui
    from google_scholar import parse_opinion_blocks
except ImportError:  # pragma: no cover - exercised on a bare checkout
    gui = None

DITTUS_HTML = """<section class="casebody" data-firstpage="284">
  <section class="head-matter">
    <p class="docketnumber">[Sac. No. 7096.</p>
    <p class="court">In Bank.</p>
    <p class="decisiondate">Dec. 18, 1959.]</p>
    <h4 class="parties">BEATRICE V. DITTUS, as Secretary, State Board of
    Control, Petitioner, v. ALAN CRANSTON, as State Controller,
    Respondent.</h4>
    <p class="attorneys">Louis J. Heinzer, for Petitioner.</p>
  </section>
  <article class="opinion" data-type="majority">
    <p class="author">GIBSON, C. J.</p>
    <p>The Budget Act of 1958 appropriated the sum.</p>
  </article>
</section>"""


class CaptionFirstTests(unittest.TestCase):

    def test_the_caption_leads_the_head_matter(self):
        parts, blocks = case_law_parse.parse_case_law_html(DITTUS_HTML)
        texts = [b.text().strip() for b in parts[0].blocks]
        self.assertTrue(texts[0].startswith("BEATRICE V. DITTUS"), texts[0])
        self.assertEqual(texts[1:4],
                         ["Sac. No. 7096.", "In Bank.", "Dec. 18, 1959."])

    def test_a_caption_already_first_is_left_alone(self):
        html = DITTUS_HTML.replace(
            '<p class="docketnumber">[Sac. No. 7096.</p>', "").replace(
            '<p class="court">In Bank.</p>', "").replace(
            '<p class="decisiondate">Dec. 18, 1959.]</p>', "")
        parts, _blocks = case_law_parse.parse_case_law_html(html)
        self.assertTrue(
            parts[0].blocks[0].text().startswith("BEATRICE V. DITTUS"))


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class CaptionNameTests(unittest.TestCase):

    def _name(self, *centers: str) -> str:
        html = ('<div id="gs_opinion">'
                + "".join(f"<center>{c}</center>" for c in centers)
                + "<p>Opinion.</p></div>")
        return abbreviate_case_name(
            gui._scholar_caption_name(parse_opinion_blocks(html)))

    def test_a_docket_or_court_line_is_never_the_name(self):
        self.assertEqual(self._name("[Sac. No. 4160.", "In Bank.",
                                    "THE PEOPLE, Respondent, v. SOUTHERN "
                                    "PACIFIC COMPANY, Appellant."),
                         "People v. S. Pac. Co.")
        self.assertEqual(self._name("CASE No. 1078.", "STATE v. WORKMAN."),
                         "State v. Workman")

    def test_an_annotation_mark_is_no_part_of_it(self):
        self.assertEqual(self._name("*Jones v. The Commonwealth."),
                         "Jones v. Commonwealth")

    def test_a_middle_initial_v_is_not_the_separator(self):
        self.assertEqual(self._name(
            "BEATRICE V. DITTUS, as Secretary, State Board of Control, "
            "Petitioner,<br/>v.<br/>ALAN CRANSTON, as State Controller, "
            "Respondent."), "Dittus v. Cranston")

    def test_normal_casing_keeps_it_where_the_caption_has_its_v(self):
        self.assertEqual(
            normal_case_caption("BEATRICE V. DITTUS v. ALAN CRANSTON"),
            "Beatrice V. Dittus v. Alan Cranston")
        # All capitals, a lone "V." may be the only separator.
        self.assertEqual(normal_case_caption("JAMES V. SMITH"),
                         "James v. Smith")


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class StateConstitutionTests(unittest.TestCase):
    TEXT = ("The act violates Article I, Section 7 of the Constitution and "
            "U.S. Const. art. I, § 8, cl. 3.  See Amendment XIV, § 1.")

    def test_only_the_united_states_constitution_named_is_linked(self):
        links = gui.detect_brief_links(self.TEXT)
        kept = gui._federal_constitution_only(links, self.TEXT)
        self.assertEqual(
            [self.TEXT[s:e] for s, e, a in kept if a[0] == "const"],
            ["U.S. Const. art. I, § 8, cl. 3"])

    def test_a_scan_s_links_alike(self):
        links = {0: [((0, 0, 1, 1), ("const", "art:1:7"),
                      "Article I, Section 7"),
                     ((0, 0, 1, 1), ("const", "art:1:8"),
                      "U.S. Const. art. I, § 8"),
                     ((0, 0, 1, 1), ("cite", "410 U.S. 113"),
                      "Roe v. Wade, 410 U.S. 113")]}
        kept = gui._federal_constitution_only_pages(links)
        self.assertEqual([link[2] for link in kept[0]],
                         ["U.S. Const. art. I, § 8",
                          "Roe v. Wade, 410 U.S. 113"])

    def test_which_cases_are_a_state_court_s(self):
        for cite, state in (("43 Mass. 329", True), ("53 Cal. 2d 284", True),
                            ("543 P.3d 440", True), ("12 N.Y.S.2d 345", True),
                            ("410 U.S. 113", False), ("100 F.3d 1", False),
                            ("93 B.R. 684", False)):
            with self.subTest(cite=cite):
                self.assertEqual(gui._state_court_cite(cite), state)
        win = object.__new__(gui._PdfWindow)
        win._is_case = True
        win._title = "Dittus v. Cranston — 53 Cal. 2d 284"
        self.assertTrue(win._is_state_case())
        win._title = "Roe v. Wade — 410 U.S. 113"
        self.assertFalse(win._is_state_case())

    def test_the_text_window_reads_it_from_the_court(self):
        win = object.__new__(gui._ScholarTextWindow)
        win._blocks = []
        win._item = {"court_id": "cal"}
        self.assertTrue(win._is_state_case())
        win._item = {"court_id": "scotus"}
        self.assertFalse(win._is_state_case())
        win._item = {"citation": ["543 P.3d 440"]}
        self.assertTrue(win._is_state_case())

    def test_a_state_opinion_s_text_links_only_the_united_states(self):
        from google_scholar import OpinionPart, Block, Span
        part = OpinionPart("Opinion", "majority",
                           [Block("para", [Span(self.TEXT)])])
        for state, expected in ((True, 1), (False, 3)):
            ranges = gui._text_opinion_link_ranges([part], state_court=state)
            consts = [a for links in ranges.values() for _s, _e, a in links
                      if a[0] == "const"]
            self.assertEqual(len(consts), expected, state)


if __name__ == "__main__":
    unittest.main()
