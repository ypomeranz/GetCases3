"""Going up from a U.S. Code section to the units above it.

Every unit of the Code — title, subtitle, part, chapter, subchapter, subpart —
has an OLRC page: its heading, a table of its contents, then its full text.
``us_code.load_unit`` reads a unit's table (and only that much of the page);
a unit with no table of its own takes the part of its parent's that lists it.
Every page's navigation bar names the units above it by page id, so the
viewer's breadcrumb goes up exactly; a unit below is found from the OLRC's
naming, then the designation as printed, then its first section's navigation
bar.

The fixtures are the OLRC's own markup, cut down (September 2026).  Nothing
here touches the network.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import courtlistener_gui
import us_code
from us_code import (
    UnitEntry,
    UnitNotFound,
    UscSection,
    UscUnit,
    _child_granule,
    _group_in,
    _is_roman_list,
    _same_designation,
    _unit_entries,
    crumb_label,
    load_unit,
    open_unit_entry,
    page_crumbs,
)


def nav(*crumbs, own):
    links = " / ".join(
        f'<a href="/view.xhtml;jsessionid=X?req=granuleid%3AUSC-prelim-{g}'
        f'&amp;saved=%7CZ%7C&amp;edition=prelim" class="link_class">{label}'
        "</a>" for g, label in crumbs)
    return ('<div class="navigator"><form id="frmNav">'
            '<a id="frmNav:prevSection" href="#">&lt;&lt; Previous</a> '
            f'{links} / <span style="font-size: 11px; font-weight: bold;">'
            f"{own}</span></form></div>")


def row2(left, right):
    return ('<div><div class="two-column-analysis-style-content-left">'
            f'{left}</div><div class="two-column-analysis-style-content-right">'
            f"{right}</div></div>")


def row3(left, center, right=""):
    return ('<div><div class="three-column-analysis-style-content-left">'
            f'{left}</div><div class="three-column-analysis-style-content-'
            f'center">{center}</div><div class="three-column-analysis-style-'
            f'content-right">{right}</div></div>')


def label(text):
    return f'<div><div class="analysis-head-left">{text}</div></div>'


def subhead(text, tag="h4", cls="analysis-subhead"):
    return f'<{tag} class="{cls}">{text}</{tag}>'


def unit_page(head, *table, crumbs=(), own="", title_page=False):
    """A unit page: navigation bar, heading, table, then the body."""
    field = "titlehead" if title_page else "structuralhead"
    tag = "h1" if title_page else "h3"
    return (nav(*crumbs, own=own)
            + f'<div id="docViewer"><!-- field-start:{field} -->'
            f'<{tag} class="x-head">{head}</{tag}>'
            f"<!-- field-end:{field} -->"
            "<!-- field-start:analysis -->" + "".join(table)
            + "<!-- field-end:analysis -->"
            '<!-- field-start:head --><h3 class="section-head">§1. First'
            "</h3><!-- field-end:head -->")


CHAPTER_21 = unit_page(
    "CHAPTER 21&mdash;CIVIL RIGHTS",
    subhead("SUBCHAPTER I&mdash;GENERALLY"),
    '<div class="analysis">', label("Sec."),
    row2("1981.", "Equal rights under the law."),
    row2("1983.", "Civil action for deprivation of rights."),
    row2("1993.", '<span id="wide">Repealed.</span>'),
    "</div>",
    subhead("SUBCHAPTER I&ndash;A&mdash;INSTITUTIONALIZED PERSONS"),
    '<div class="analysis">', row2("1997.", "Definitions."), "</div>",
    crumbs=[("title42", "TITLE 42")], own="CHAPTER 21",
)

SUBCHAPTER_I = unit_page(
    "SUBCHAPTER I&mdash;GENERALLY",   # no table of its own
    crumbs=[("title42", "TITLE 42"), ("title42-chapter21", "CHAPTER 21")],
    own="SUBCHAPTER I",
)


class NavigationBarTests(unittest.TestCase):
    def test_the_units_above_and_the_page_itself(self):
        crumbs, own = page_crumbs(SUBCHAPTER_I)
        self.assertEqual(crumbs, [("title42", "TITLE 42"),
                                  ("title42-chapter21", "CHAPTER 21")])
        self.assertEqual(own, "SUBCHAPTER I")

    def test_labels_as_the_reader_shows_them(self):
        self.assertEqual(crumb_label("SUBCHAPTER I"), "Subchapter I")
        self.assertEqual(crumb_label("part 5"), "Part 5")
        self.assertEqual(crumb_label("TITLE 42"), "Title 42")
        self.assertEqual(crumb_label("Subpart C"), "Subpart C")

    def test_a_page_without_a_bar(self):
        self.assertEqual(page_crumbs("<p>nothing</p>"), ([], ""))


class TableOfContentsTests(unittest.TestCase):
    def test_sections_under_the_subheads_of_a_chapter(self):
        entries = _unit_entries(CHAPTER_21, "title42-chapter21")
        self.assertEqual(
            [(e.kind, e.depth, e.label, e.heading) for e in entries], [
                ("group", 0, "SUBCHAPTER I—GENERALLY", ""),
                ("section", 1, "§ 1981", "Equal rights under the law."),
                ("section", 1, "§ 1983",
                 "Civil action for deprivation of rights."),
                ("section", 1, "§ 1993", "Repealed."),
                ("group", 0, "SUBCHAPTER I–A—INSTITUTIONALIZED PERSONS", ""),
                ("section", 1, "§ 1997", "Definitions."),
            ])
        self.assertEqual(entries[2].section, "1983")

    def test_a_titles_chapters_with_their_first_sections(self):
        page = unit_page(
            "TITLE 42&mdash;THE PUBLIC HEALTH AND WELFARE",
            '<div class="analysis"><div><div class="analysis-head-left">'
            'Chap.</div><div class="analysis-head-right">Sec.</div></div>',
            row3("1A.", "The Public Health Service; Supplemental Provisions",
                 "71"),
            row3("5.<sup>1</sup>", "Administrative Procedure",
                 "<sup>1</sup>501"),
            "</div>", title_page=True, own="TITLE 42")
        entries = _unit_entries(page, "title42")
        self.assertEqual(
            [(e.label, e.granule, e.first_section) for e in entries],
            [("Chapter 1A", "title42-chapter1A", "71"),
             ("Chapter 5", "title42-chapter5", "501")])

    def test_a_title_listing_subtitles_then_every_chapter(self):
        # 26 U.S.C.: two tables, told apart by headings.
        page = unit_page(
            "TITLE 26&mdash;INTERNAL REVENUE CODE",
            label("Subtitle"), row3("A.", "Income taxes."),
            row3("B.", "Estate and gift taxes."),
            label("Chapter"), row3("1.", "Normal taxes and surtaxes", "1"),
            title_page=True, own="TITLE 26")
        entries = _unit_entries(page, "title26")
        self.assertEqual(
            [(e.kind, e.depth, e.label) for e in entries],
            [("group", 0, "Subtitles"), ("unit", 1, "Subtitle A"),
             ("unit", 1, "Subtitle B"), ("group", 0, "Chapters"),
             ("unit", 1, "Chapter 1")])
        self.assertEqual(entries[1].granule, "title26-subtitleA")

    def test_notes_are_not_contents(self):
        # 42 U.S.C. ch. 6A lists part after part in notes; "Editorial
        # Notes" and "Amendments" head real notes and list nothing.
        page = unit_page(
            "CHAPTER 6A&mdash;PUBLIC HEALTH SERVICE",
            subhead("SUBCHAPTER I&mdash;ADMINISTRATION"),
            label("Sec."), row2("201.", "Definitions."),
            "<!-- field-end:analysis --><!-- field-start:notes -->",
            subhead("Part A&mdash;Administration", cls="note-head"),
            row2("202.", "Administration and supervision of Service."),
            subhead("<strong>Editorial Notes</strong>", cls="note-head"),
            subhead("Amendments", cls="note-head"),
            own="CHAPTER 6A")
        entries = _unit_entries(page, "title42-chapter6A")
        self.assertEqual(
            [(e.kind, e.depth, e.label) for e in entries],
            [("group", 0, "SUBCHAPTER I—ADMINISTRATION"),
             ("section", 1, "§ 201"),
             ("group", 1, "Part A—Administration"),
             ("section", 2, "§ 202")])

    def test_part_subheads_set_as_h3(self):
        # 10 U.S.C. subtitle A.
        page = unit_page(
            "Subtitle A&mdash;General Military Law",
            subhead("<strong>PART I&mdash;ORGANIZATION</strong>", tag="h3"),
            label("Chap."), row3("1.", "Definitions", "101"),
            own="Subtitle A")
        entries = _unit_entries(page, "title10-subtitleA")
        self.assertEqual([(e.kind, e.label, e.granule) for e in entries], [
            ("group", "PART I—ORGANIZATION", ""),
            ("unit", "Chapter 1", "title10-chapter1")])

    def test_a_table_set_as_a_table(self):
        # 10 U.S.C. ch. 47 (the UCMJ): subchapters, and sections numbered as
        # articles too.
        chapter = unit_page(
            "CHAPTER 47&mdash;UNIFORM CODE OF MILITARY JUSTICE",
            '<table class="usc"><tr><th id="row0col0">Subchapter</th>'
            '<th id="row0col1">&#160;</th><th id="row0col2">Sec.</th>'
            '<th id="row0col3">Art.</th></tr>'
            '<tr>\n<td class="left">I. </td>\n<td class="middle">General '
            'Provisions </td>\n<td class="middle">801 </td>\n'
            '<td class="right">1</td>\n</tr>'
            '<tr>\n<td class="left">XII. </td>\n<td class="middle">Court of '
            'Appeals </td>\n<td class="middle">941 </td>\n'
            '<td class="right">141</td>\n</tr></table>',
            own="CHAPTER 47")
        units = _unit_entries(chapter, "title10-chapter47")
        self.assertEqual(
            [(e.label, e.granule, e.first_section) for e in units],
            [("Subchapter I", "title10-chapter47-subchapter1", "801"),
             ("Subchapter XII", "title10-chapter47-subchapter12", "941")])
        subchapter = unit_page(
            "SUBCHAPTER I&mdash;GENERAL PROVISIONS",
            '<table class="usc"><tr><th id="row0col0">Sec.</th>'
            '<th id="row0col1">Art.</th><th id="row0col2">&#160;</th></tr>'
            '<tr>\n<td class="left">801. </td>\n<td class="middle">1. </td>'
            '\n<td class="right">Definitions.</td>\n</tr></table>',
            own="SUBCHAPTER I")
        (entry,) = _unit_entries(subchapter, "title10-chapter47-subchapter1")
        self.assertEqual((entry.label, entry.heading, entry.section),
                         ("§ 801 (Art. 1)", "Definitions.", "801"))


class PageIdTests(unittest.TestCase):
    """The ids the OLRC gives the units a table lists, as its navigation
    bars show them."""

    def test_the_naming(self):
        cases = [
            # chapters, subtitles, and parts directly under a title hang
            # off the title
            (("title18-part1", "chapter", "44", False), "title18-chapter44"),
            (("title26-subtitleA", "chapter", "1", False), "title26-chapter1"),
            (("title49", "subtitle", "VII", True), "title49-subtitle7"),
            (("title18", "part", "I", True), "title18-part1"),
            # everything else nests, roman numerals as digits
            (("title10-subtitleA", "part", "II", True),
             "title10-subtitleA-part2"),
            (("title26-chapter1", "subchapter", "A", False),
             "title26-chapter1-subchapterA"),
            (("title42-chapter7", "subchapter", "XVIII", True),
             "title42-chapter7-subchapter18"),
            (("title49-subtitle7-partA", "subpart", "i", True),
             "title49-subtitle7-partA-subpart1"),
        ]
        for args, want in cases:
            with self.subTest(args=args):
                self.assertEqual(_child_granule(*args), want)

    def test_roman_numerals_or_letters(self):
        self.assertTrue(_is_roman_list(["I", "II", "III", "IV"]))
        self.assertTrue(_is_roman_list(["i", "ii"]))
        self.assertTrue(_is_roman_list(["I"]))
        # 26 U.S.C. ch. 1's subchapters: C, D, I, L and M are numerals too
        self.assertFalse(_is_roman_list(["A", "B", "C", "D", "I", "L"]))
        self.assertFalse(_is_roman_list(["1", "1A", "2"]))


class GroupTests(unittest.TestCase):
    ENTRIES = [
        UnitEntry("group", 0, "SUBCHAPTER I—GENERALLY"),
        UnitEntry("section", 1, "§ 1981", section="1981"),
        UnitEntry("group", 0, "SUBCHAPTER I–A—INSTITUTIONALIZED PERSONS"),
        UnitEntry("section", 1, "§ 1997", section="1997"),
        UnitEntry("group", 0, "SUBCHAPTER II—IMMIGRATION"),
        UnitEntry("group", 1, "Part I—Selection System"),
        UnitEntry("section", 2, "§ 1151", section="1151"),
        UnitEntry("group", 1, "Part II—Admission Qualifications"),
        UnitEntry("section", 2, "§ 1182", section="1182"),
    ]

    def test_a_designation_is_matched_whole(self):
        self.assertTrue(_same_designation("SUBCHAPTER I—GENERALLY",
                                          "SUBCHAPTER I"))
        self.assertFalse(_same_designation(
            "SUBCHAPTER I–A—INSTITUTIONALIZED PERSONS", "SUBCHAPTER I"))
        self.assertTrue(_same_designation("part 5—administration", "part 5"))
        self.assertTrue(_same_designation("Part II", "PART II"))

    def test_a_group_one_level_out(self):
        self.assertEqual([e.section for e in _group_in(self.ENTRIES,
                                                       "SUBCHAPTER I")],
                         ["1981"])
        part = _group_in(self.ENTRIES, "Part II")
        self.assertEqual([(e.kind, e.depth, e.section) for e in part],
                         [("section", 0, "1182")])
        subchapter = _group_in(self.ENTRIES, "SUBCHAPTER II")
        self.assertEqual([(e.kind, e.depth) for e in subchapter],
                         [("group", 0), ("section", 1), ("group", 0),
                          ("section", 1)])
        self.assertEqual(_group_in(self.ENTRIES, "SUBCHAPTER IX"), [])


class LoadUnitTests(unittest.TestCase):
    def setUp(self):
        us_code._unit_cache.clear()
        self.addCleanup(us_code._unit_cache.clear)

    def fetch_from(self, pages):
        def fetch(url, cap=0):
            granule = url.split("USC-prelim-")[1].split("&")[0]
            return pages.get(granule, "<html>no such page</html>")
        return patch("us_code._fetch_unit_top", side_effect=fetch)

    def test_a_unit_without_a_table_is_listed_in_its_parent(self):
        with self.fetch_from({"title42-chapter21-subchapter1": SUBCHAPTER_I,
                              "title42-chapter21": CHAPTER_21}) as fetch:
            unit = load_unit("title42-chapter21-subchapter1")
            again = load_unit("title42-chapter21")
        self.assertEqual(unit.label, "Subchapter I")
        self.assertEqual(unit.heading, "SUBCHAPTER I—GENERALLY")
        self.assertEqual([e.section for e in unit.entries],
                         ["1981", "1983", "1993"])
        self.assertEqual(unit.crumbs[-1], ("title42-chapter21", "CHAPTER 21"))
        self.assertEqual(unit.title, "42")
        # the parent, read for its table, is kept
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(len(again.entries), 6)

    def test_no_such_page(self):
        with self.fetch_from({}):
            with self.assertRaises(UnitNotFound):
                load_unit("title42-chapter999")

    def test_listed_nowhere_its_own_text_is_read(self):
        # 52 U.S.C. subtitle III: no table, and the title lists chapters
        # without subtitles.
        subtitle = unit_page(
            "Subtitle III&mdash;Federal Campaign Finance",
            crumbs=[("title52", "TITLE 52")], own="Subtitle III")
        title = unit_page("TITLE 52&mdash;VOTING", label("Chapter"),
                          row3("301.", "Federal Election Campaigns", "30101"),
                          title_page=True, own="TITLE 52")
        body = [UnitEntry("section", 0, "§ 30101", section="30101")]
        with self.fetch_from({"title52-subtitle3": subtitle,
                              "title52": title}), \
                patch("us_code._entries_from_body",
                      return_value=(body, False)) as read:
            unit = load_unit("title52-subtitle3")
        read.assert_called_once()
        self.assertEqual([e.section for e in unit.entries], ["30101"])

    def test_the_body_reading(self):
        page = (nav(("title52", "TITLE 52"), own="Subtitle III")
                + "<!-- field-start:structuralhead -->"
                '<h3 class="subtitle-head">Subtitle III&mdash;Federal '
                "Campaign Finance</h3><!-- field-end:structuralhead -->"
                '<!-- field-start:structuralhead --><h3 class="chapter-head">'
                "CHAPTER 301&mdash;FEDERAL ELECTION CAMPAIGNS</h3>"
                "<!-- field-end:structuralhead -->"
                '<h3 class="section-head">&sect;30101. Definitions</h3>'
                '<h3 class="section-head">[&sect;30102. Repealed]</h3>')

        class Response:
            def raise_for_status(self):
                pass

            def iter_content(self, _size):
                yield page.encode("utf-8")

            def close(self):
                pass

        with patch("requests.get", return_value=Response()):
            entries, partial = us_code._entries_from_body("u")
        self.assertFalse(partial)
        self.assertEqual(
            [(e.kind, e.depth, e.label) for e in entries],
            [("group", 0, "CHAPTER 301—FEDERAL ELECTION CAMPAIGNS"),
             ("section", 1, "§ 30101"), ("section", 1, "§ 30102")])


class OpenUnitEntryTests(unittest.TestCase):
    PARENT = UscUnit(title="42", granule="title42-chapter6A-subchapter25-partA",
                     url="u", label="Part A", heading="Part A")

    def test_the_designation_as_printed(self):
        # 42 U.S.C. ch. 6A's "Subpart II" is subpartII, not subpart2.
        entry = UnitEntry(
            "unit", 0, "Subpart II", unit_kind="subpart", designation="II",
            granule="title42-chapter6A-subchapter25-partA-subpart2")
        tried = []

        def load(granule):
            tried.append(granule)
            if granule.endswith("subpartII"):
                return "found"
            raise UnitNotFound(granule)

        with patch("us_code.load_unit", side_effect=load):
            self.assertEqual(open_unit_entry(entry, self.PARENT), "found")
        self.assertEqual(tried, [
            "title42-chapter6A-subchapter25-partA-subpart2",
            "title42-chapter6A-subchapter25-partA-subpartII"])

    def test_the_first_sections_navigation_bar(self):
        # 29 U.S.C. ch. 18's "part 5" is title29-chapter18-subchapter1-
        # node555-part5, which no naming produces.
        entry = UnitEntry(
            "unit", 0, "Part 5", unit_kind="part", designation="5",
            granule="title29-chapter18-subchapter1-part5",
            first_section="1131")
        section = UscSection(title="29", section="1131", url="u", crumbs=[
            ("title29", "TITLE 29"), ("title29-chapter18", "CHAPTER 18"),
            ("title29-chapter18-subchapter1", "SUBCHAPTER I"),
            ("title29-chapter18-subchapter1-node555-part5", "part 5")])

        def load(granule):
            if "node555" in granule:
                return "found"
            raise UnitNotFound(granule)

        parent = UscUnit(title="29", granule="title29-chapter18-subchapter1",
                         url="u", label="Subchapter I", heading="")
        with patch("us_code.load_unit", side_effect=load), \
                patch("us_code.load_section", return_value=section):
            self.assertEqual(open_unit_entry(entry, parent), "found")


class NeighborTests(unittest.TestCase):
    def test_prev_and_next_come_from_the_same_contents(self):
        us_code._order_cache.pop("title42-chapter21-subchapter1", None)
        unit = UscUnit(title="42", granule="title42-chapter21-subchapter1",
                       url="u", label="Subchapter I", heading="", entries=[
                           UnitEntry("section", 0, "§ 1982", section="1982"),
                           UnitEntry("section", 0, "§ 1983", section="1983"),
                           UnitEntry("section", 0, "§ 1984", section="1984"),
                       ])
        doc = UscSection(title="42", section="1983", url="u",
                         container="title42-chapter21-subchapter1")
        with patch("us_code.load_unit", return_value=unit), \
                patch("us_code._container_sections") as streamed:
            self.assertEqual(doc.neighbors(),
                             (("42", "1982"), ("42", "1984")))
        streamed.assert_not_called()


class ViewerTests(unittest.TestCase):
    def test_contents_entries_open_in_the_same_window(self):
        calls = []
        window = SimpleNamespace(
            _link_actions={"lnk1": ("usc-sec", "42:1985"),
                           "lnk2": ("usc-unit", "3")},
            _open_section_here=lambda t, s: calls.append(("section", t, s)),
            _open_unit_entry=lambda i: calls.append(("unit", i)),
        )
        courtlistener_gui._StatuteWindow._follow_link(window, "lnk1")
        courtlistener_gui._StatuteWindow._follow_link(window, "lnk2")
        self.assertEqual(calls, [("section", "42", "1985"), ("unit", 3)])

    def test_a_bookmarked_section_keeps_its_place_in_the_code(self):
        doc = courtlistener_gui._SavedStatuteDoc({
            "paras": [["sechead", 0, "§1983. Civil action"]],
            "kind": "usc", "label": "42 U.S.C. § 1983",
            "crumbs": [["title42", "TITLE 42"],
                       ["title42-chapter21", "CHAPTER 21"]],
        })
        self.assertEqual(doc.crumbs, [("title42", "TITLE 42"),
                                      ("title42-chapter21", "CHAPTER 21")])
        # and its section number, for marking it in a table of contents
        window = SimpleNamespace(_doc=doc)
        self.assertEqual(
            courtlistener_gui._StatuteWindow._doc_section(window), "1983")

    def test_a_section_load_leaves_the_table_of_contents(self):
        import inspect

        source = inspect.getsource(courtlistener_gui._StatuteWindow._load_doc)
        self.assertIn("self._unit = None", source)
        render = inspect.getsource(courtlistener_gui._StatuteWindow._render)
        self.assertLess(render.index("self._render_unit()"),
                        render.index("txt.delete"))


if __name__ == "__main__":
    unittest.main()
