"""Jumping to a separate opinion that begins partway down a PDF page.

The parts strip used to scroll to the top of the page a part starts on.  Where
the previous opinion runs on above it — the usual thing in a bound reporter —
that leaves the reader hunting for the line.  ``SlipSection.start_at`` carries
the height of the opening line for exactly that case, and these tests pin down
when it is offered and when it is not.

``slip_opinion`` is free of tkinter, so it runs headless as-is; the reader's
footnote-position lookup is lifted out of ``courtlistener_gui`` with ``ast``.
"""

import ast
import pathlib
import re
import typing
import unittest

import slip_opinion as so


def _glyphs(text: str, x: float, y: float, size: float = 10.0):
    """Glyph tuples for *text* laid out left to right at (x, y)."""
    out = []
    for i, ch in enumerate(text):
        left = x + i * size * 0.5
        out.append((ch, (left, y, left + size * 0.5, y + size)))
    return out


def _page(lines, x=72.0, top=700.0, step=14.0, size=10.0):
    """A page of glyphs from ``lines``, one text line per entry."""
    chars = []
    for n, text in enumerate(lines):
        chars.extend(_glyphs(text, x, top - n * step, size))
    return chars


def _two_column_page(left_lines, right_lines):
    chars = []
    chars.extend(_page(left_lines, x=72.0))
    chars.extend(_page(right_lines, x=330.0))
    return chars


class SingleColumnTests(unittest.TestCase):
    def test_a_full_measure_page_is_one_column(self):
        page = _page(["The Court holds that the statute is plain enough " * 2]
                     * 25)
        self.assertTrue(so.is_single_column(page))

    def test_a_page_with_a_centre_gutter_is_two_columns(self):
        page = _two_column_page(["left column text here"] * 20,
                                ["right column text here"] * 20)
        self.assertFalse(so.is_single_column(page))

    def test_a_nearly_empty_page_is_treated_as_one_column(self):
        self.assertTrue(so.is_single_column(_page(["short"])))

    def test_glyphs_without_boxes_are_ignored(self):
        page = [("x", None)] * 200
        self.assertTrue(so.is_single_column(page))


# A US Reports-style opinion: running head, body, and a concurrence opening
# partway down the third page with the majority's close still above it.
_MIDPAGE_DOC = [
    _page(["48 OCTOBER TERM, 2017",
           "Syllabus",
           "The syllabus text begins here and runs on for a while."]),
    _page(["Cite as: 583 U. S. 48 (2018) 49",
           "Opinion of the Court",
           "Justice Thomas delivered the opinion of the Court.",
           "The opinion runs on for several lines of body text here."]),
    # The running head names the new part from this page on, but the
    # majority's last lines are still above the attribution.
    _page(["50 DISTRICT OF COLUMBIA v. WESBY",
           "Opinion of Sotomayor, J.",
           "and the case is remanded for further proceedings.",
           "It is so ordered.",
           "Justice Sotomayor, concurring in the judgment.",
           "I agree with the majority that the officers are entitled."]),
]

# A slip opinion: each part opens its own page, under a divider rule.
_FRESH_PAGE_DOC = [
    _page(["(Slip Opinion) OCTOBER TERM, 2025",
           "Syllabus",
           "The syllabus text begins here and runs on for a while."]),
    _page(["Cite as: 609 U. S. ____ (2026) 1",
           "Opinion of the Court",
           "Justice Barrett delivered the opinion of the Court.",
           "The opinion runs on for several lines of body text here."]),
    _page(["Cite as: 609 U. S. ____ (2026) 1",
           "JACKSON, J., concurring",
           "SUPREME COURT OF THE UNITED STATES",
           "_________________",
           "No. 25-112",
           "_________________",
           "OKELLO T. CHATRIE, PETITIONER v. UNITED STATES",
           "ON WRIT OF CERTIORARI TO THE UNITED STATES COURT",
           "Justice Jackson, concurring.",
           "I join the opinion of the Court in full."]),
]


class StartAtTests(unittest.TestCase):
    def _sections(self, pages):
        return {s.kind: s for s in so.detect_sections(pages)}

    def test_a_part_opening_midpage_carries_its_line(self):
        secs = self._sections(_MIDPAGE_DOC)
        concur = secs.get("concurrence")
        self.assertIsNotNone(concur, secs)
        self.assertIsNotNone(concur.start_at,
                             "a mid-page opening should name its line")
        page, y = concur.start_at
        self.assertEqual(page, 2)
        # The attribution sits below "It is so ordered." and above the body.
        lines = so.group_lines(_MIDPAGE_DOC[2])
        att = next(ln for ln in lines if ln.text.startswith("Justice Sotomayor"))
        self.assertAlmostEqual(y, att.y, places=3)

    def test_a_part_opening_its_own_page_does_not(self):
        secs = self._sections(_FRESH_PAGE_DOC)
        concur = secs.get("concurrence")
        self.assertIsNotNone(concur, secs)
        self.assertIsNone(
            concur.start_at,
            "a part under a divider rule opens the page; the top is right")

    def test_the_syllabus_and_majority_never_carry_a_line(self):
        for doc in (_MIDPAGE_DOC, _FRESH_PAGE_DOC):
            for sec in so.detect_sections(doc):
                if sec.kind in ("syllabus", "majority"):
                    self.assertIsNone(sec.start_at, sec.label)

    def test_start_page_is_unchanged_by_the_new_field(self):
        # The field is additive: where a part starts is still start_page.
        secs = self._sections(_MIDPAGE_DOC)
        self.assertEqual(secs["syllabus"].start_page, 0)
        self.assertEqual(secs["majority"].start_page, 1)
        self.assertEqual(secs["concurrence"].start_page, 2)

    def test_sections_default_to_no_line(self):
        self.assertIsNone(so.SlipSection("X", "majority", 3).start_at)


class GivingWayTests(unittest.TestCase):
    """A worker reading the parts stands aside a page at a time while the
    window is busy (the GUI passes its _yield_to_ui), and reads the same."""

    def test_each_pass_gives_way_page_by_page(self):
        calls = []
        paced = so.detect_sections(
            _MIDPAGE_DOC, between_pages=lambda: calls.append(1))
        # The lines, the running heads, the parts: a page at a time each.
        self.assertEqual(len(calls), 3 * len(_MIDPAGE_DOC))
        self.assertEqual(paced, so.detect_sections(_MIDPAGE_DOC))

    def test_so_does_the_scan_for_openings_on_headless_pages(self):
        doc = [_page(["SMITH, Circuit Judge:", "The judgment is affirmed."]),
               _page(["JONES, Circuit Judge, dissenting.", "I would not."])]
        calls = []
        paced = so.detect_sections(doc, between_pages=lambda: calls.append(1))
        self.assertEqual(len(calls), 3 * len(doc))
        self.assertEqual(paced, so.detect_sections(doc))


# ---------------------------------------------------------------------------
# The reader's footnote markers, which have to survive justification
# ---------------------------------------------------------------------------

def _load_fn_marker_pos():
    src = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text()
    tree = ast.parse(src)
    wanted = ("_fn_marker_pos", "_fn_link_map")
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            found[node.name] = ast.get_source_segment(src, node)
    missing = [w for w in wanted if w not in found]
    if missing:
        raise AssertionError(f"not found: {missing}")
    ns = {"re": re, "tk": type("tk", (), {"TclError": Exception}),
          "Optional": typing.Optional}
    for name in wanted:
        exec(found[name], ns)
    return ns


class _FakeText:
    """Just enough Text for the marker lookup: tags with movable ranges."""

    def __init__(self, ranges):
        self._ranges = ranges

    def tag_ranges(self, tag):
        return self._ranges.get(tag, ())


class FootnoteMarkerTests(unittest.TestCase):
    """The jump target is read from the marker's tag, not a stored index.

    Justification inserts padding into the text as the window is resized, which
    shifts every index recorded at render time.  A tag moves with its
    characters, so it still points at the marker.
    """

    @classmethod
    def setUpClass(cls):
        ns = _load_fn_marker_pos()
        cls.Reader = type("Reader", (), {
            "_fn_marker_pos": ns["_fn_marker_pos"],
            "_fn_link_map": ns["_fn_link_map"],
        })

    def _reader(self, actions, ranges, ref_pos=None, def_pos=None):
        r = self.Reader()
        r._link_actions = actions
        r._text = _FakeText(ranges)
        r._fn_ref_pos = ref_pos or {}
        r._fn_def_pos = def_pos or {}
        return r

    def test_position_comes_from_the_tag_not_the_stored_index(self):
        r = self._reader(
            actions={"lnk1": ("fnref", "f4"), "lnk2": ("fndef", "f4")},
            # Where the tags are *now*, after justification moved them.
            ranges={"lnk1": ("12.30", "12.31"), "lnk2": ("80.4", "80.5")},
            # Where they were when the text was first laid out.
            ref_pos={"f4": "12.5"}, def_pos={"f4": "77.0"},
        )
        self.assertEqual(r._fn_marker_pos("fnref", "f4"), "12.30")
        self.assertEqual(r._fn_marker_pos("fndef", "f4"), "80.4")

    def test_the_stored_index_is_the_fallback(self):
        # A marker never rendered as a link has no tag to read.
        r = self._reader(actions={}, ranges={},
                         ref_pos={"f4": "12.5"}, def_pos={"f4": "77.0"})
        self.assertEqual(r._fn_marker_pos("fnref", "f4"), "12.5")
        self.assertEqual(r._fn_marker_pos("fndef", "f4"), "77.0")

    def test_an_unknown_footnote_has_no_position(self):
        r = self._reader(actions={}, ranges={})
        self.assertIsNone(r._fn_marker_pos("fnref", "nope"))

    def test_a_tag_with_no_range_left_falls_back(self):
        r = self._reader(actions={"lnk1": ("fnref", "f4")}, ranges={"lnk1": ()},
                         ref_pos={"f4": "12.5"})
        self.assertEqual(r._fn_marker_pos("fnref", "f4"), "12.5")

    def test_the_first_reference_wins_when_a_note_is_cited_twice(self):
        r = self._reader(
            actions={"a": ("fnref", "f4"), "b": ("fnref", "f4")},
            ranges={"a": ("10.1", "10.2"), "b": ("40.1", "40.2")},
        )
        self.assertEqual(r._fn_marker_pos("fnref", "f4"), "10.1")

    def test_the_two_sides_do_not_cross(self):
        r = self._reader(
            actions={"a": ("fnref", "f1"), "b": ("fndef", "f2")},
            ranges={"a": ("10.1", "10.2"), "b": ("90.1", "90.2")},
        )
        self.assertEqual(r._fn_marker_pos("fnref", "f1"), "10.1")
        self.assertIsNone(r._fn_marker_pos("fndef", "f1"))
        self.assertIsNone(r._fn_marker_pos("fnref", "f2"))


class ScanPartLabelTests(unittest.TestCase):
    """What the parts rail calls the writings it finds on a scan."""

    def test_an_opinion_in_chambers(self):
        # Morison v. United States, 486 U.S. 1306 (1988): every page heads
        # "Opinion in Chambers", the syllabus's too, and the whole scan had
        # been called its syllabus.  The opinion opens partway down the
        # first page, at its Circuit Justice's byline.
        first = ["1306 OCTOBER TERM, 1987", "Opinion in Chambers 486 U. S.",
                 "MORISON v. UNITED STATES",
                 "ON APPLICATION FOR CONTINUED RELEASE FROM DETENTION",
                 "No. A-896. Decided June 2, 1988",
                 "Application for continued release from detention is "
                 "denied.",
                 "CHIEF JUSTICE REHNQUIST, Circuit Justice.",
                 "Samuel Loring Morison was convicted in the District Court"]
        secs = so.detect_sections([
            _page(first),
            _page(["MORISON v. UNITED STATES 1307",
                   "1306 Opinion in Chambers",
                   "States v. Bayko, 774 F. 2d 516, 522 (CA1 1985), his "
                   "application is denied."])])
        self.assertEqual([(s.label, s.kind, s.start_page) for s in secs],
                         [("Syllabus", "syllabus", 0),
                          ("Rehnquist, C.J., in chambers", "majority", 0)])
        byline = next(ln for ln in so.group_lines(_page(first))
                      if ln.text.startswith("CHIEF JUSTICE"))
        self.assertEqual(secs[1].start_at, (0, byline.y))

    def test_a_justice_who_took_no_part_is_not_the_writer(self):
        # Hemi Group, LLC v. City of New York, 559 U.S. 1: the one-page
        # concurrence, named by its byline, had been named for Justice
        # Sotomayor, from the line above it.
        secs = so.detect_sections([
            _page(["1 OCTOBER TERM, 2009", "Syllabus",
                   "The syllabus text begins here and runs on."]),
            _page(["Cite as: 559 U. S. 1 (2010) 3", "Opinion of the Court",
                   "Chief Justice Roberts delivered the opinion of the Court.",
                   "The opinion runs on for several lines of body text."]),
            _page(["Cite as: 559 U. S. 1 (2010) 17",
                   "GINSBURG, J., concurring in part and in judgment",
                   "and the judgment is reversed.",
                   "Justice Sotomayor took no part in the consideration or",
                   "decision of this case.",
                   "Justice Ginsburg, concurring in part and concurring in",
                   "the judgment."]),
            _page(["18 HEMI GROUP, LLC v. CITY OF NEW YORK",
                   "BREYER, J., dissenting",
                   "Justice Breyer, with whom Justice Stevens and Justice",
                   "Kennedy join, dissenting."]),
        ])
        self.assertTrue(secs[2].label.startswith("Ginsburg, J., concurring"),
                        secs[2].label)

    def test_a_sentence_about_a_dissent_opens_none(self):
        # Hodge v. Plappert, 136 F.4th 648 (6th Cir. 2025) (en banc).
        line = ("Justice Sotomayor observed in her dissent from the denial "
                "of certiorari")
        self.assertIsNone(so._line_us_attribution(
            so.group_lines(_page([line, "in Hodge’s state case, that …"])),
            0))
        self.assertEqual(
            so._line_us_attribution(so.group_lines(_page(
                ["Justice Ginsburg, concurring in part and concurring in",
                 "the judgment."])), 0)[0], "concurrence")

    def test_a_running_head_s_ocr_is_read_against_the_bench(self):
        # Reiter v. Sonotone Corp., 442 U.S. 330 ("Rennquist"); Holmes v.
        # Sec. Inv. Prot. Corp., 503 U.S. 258 ("Oconnor").
        self.assertEqual(so._names_display(("RENNQUIST",), roster=True),
                         "Rehnquist")
        self.assertEqual(so._names_display(("OCONNOR",), roster=True),
                         "O’Connor")
        # A lower court's judge is no Justice: read as printed.
        self.assertEqual(so._names_display(("BLACKMAN",)), "Blackman")

    def test_justices_writing_together_as_the_bluebook_names_them(self):
        # Dobbs v. Jackson Women’s Health Org., 597 U.S. 215 (2022).
        self.assertEqual(
            so._names_display(("BREYER", "SOTOMAYOR", "KAGAN"), roster=True),
            "Breyer, Sotomayor & Kagan")

    def test_in_the_judgment_as_the_byline_says_it(self):
        self.assertEqual(
            so._canon_role_phrase(("CONCURRING", "IN", "JUDGMENT")),
            "concurring in the judgment")


class CourtSlipWritingTests(unittest.TestCase):
    """A court's own slip opinion, which has no running heads: its writings
    found as the courts that issued the petition's cases set them."""

    BODY = "The opinion runs on for several lines of body text."

    def test_an_en_banc_order_its_statement_and_concurrence(self):
        # Acad. of Allergy & Asthma in Primary Care v. Amerigroup Tenn., Inc.,
        # 164 F.4th 529 (6th Cir. 2026): no writing had been found.
        secs = so.detect_sections([
            _page(["RECOMMENDED FOR PUBLICATION",
                   "The court delivered an ORDER denying the petition for "
                   "rehearing en banc.", self.BODY]),
            _page(["_________________", "ORDER", "_________________",
                   "The court received a petition for rehearing en banc."]),
            _page(["__________________", "STATEMENT", "__________________",
                   "JOHN K. BUSH, Circuit Judge, respecting the denial of "
                   "rehearing en banc. This case", self.BODY]),
            _page(["__________________", "CONCURRENCE", "__________________",
                   "MURPHY, Circuit Judge, with whom SUTTON, ChiefJudge, and "
                   "KETHLEDGE, Circuit",
                   "Judge,join, concurring in the denial of rehearing en banc. "
                   "Congress has", self.BODY]),
        ])
        self.assertEqual(
            [(s.kind, s.label, s.start_page) for s in secs][1:],
            [("majority", "Order", 1),
             ("separate", "Statement of Bush, J., respecting the denial of "
                          "rehearing en banc", 2),
             ("concurrence", "Murphy, J., concurring in the denial of "
                             "rehearing en banc", 3)])

    def test_a_titled_dissent_named_by_its_signature(self):
        # D.J. ex rel. R.J. v. First Student, Inc., 707 S.W.3d 581 (Mo.
        # 2025): Missouri heads the dissent with its title alone; each
        # writer signs, underlined, at the writing's end.
        secs = so.detect_sections([
            _page(["SUPREME COURT OF MISSOURI", "en banc",
                   "The Honorable Rex Burlison, Judge", self.BODY]),
            _page([self.BODY] * 5 + [
                   "_Z_e_l _M__F__is_c_h_e_r _J_u_d_g_e_________",
                   "Russell, C.J., Ransom, Broniec, and Gooch, JJ., concur;",
                   "Wilson J dissents in separate opinion filed; Powell J"]),
            _page(["SUPREME COURT OF MISSOURI", "en banc",
                   "DISSENTING OPINION",
                   "The principal opinion vacates the judgment.", self.BODY]),
            _page([self.BODY] * 5 + [
                   "For the foregoing reasons, I respectfully dissent.",
                   "_P_a_u_l _C_. _W__il_s_o_n_, _Ju__d _g _e ________"]),
        ])
        self.assertEqual([(s.kind, s.label, s.start_page) for s in secs],
                         [("majority", "Opinion of the Court", 0),
                          ("dissent", "Wilson, J., dissenting", 2)])

    def test_a_line_up_announces_writings_the_slip_does_not_hold(self):
        # Werner Enters., Inc. v. Blake, 719 S.W.3d 525 (Tex. 2025): the
        # concurrence and the dissent are filed apart; the slip closes on the
        # Chief Justice's signature and "OPINION DELIVERED".
        secs = so.detect_sections([
            _page(["Supreme Court of Texas",
                   "CHIEF JUSTICE BLACKLOCK delivered the opinion of the "
                   "Court, in",
                   "which Justice Devine and Justice Busby joined.",
                   "JUSTICE YOUNG filed a concurring opinion, in which Justice",
                   "Huddle joined.",
                   "JUSTICE BLAND filed an opinion dissenting in part, in "
                   "which", self.BODY]),
            _page([self.BODY]),
            _page([self.BODY, "James D. Blacklock", "Chief Justice",
                   "OPINION DELIVERED: June 27, 2025"]),
        ])
        self.assertEqual([(s.kind, s.label, s.start_page) for s in secs],
                         [("majority", "Opinion of the Court", 0)])

    def test_the_whole_role_and_the_space_the_text_lost(self):
        # Randolph v. Macauley, 155 F.4th 859 (6th Cir. 2025).
        lines = so.group_lines(_page([
            "MATHIS, Circuit Judge, concurring in part and concurring in "
            "thejudgment. I"]))
        self.assertEqual(so._west_attribution(lines, 0),
                         ("concurrence", "Mathis",
                          "concurring in part and concurring in the judgment"))


if __name__ == "__main__":
    unittest.main()
