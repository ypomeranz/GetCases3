"""Going up from a U.S. Code section to the units above it.

The OLRC's browse tree sets out a whole title: every unit — subtitle, part,
chapter, subchapter, subpart — nested in the one that holds it, with the
sections directly in it.  ``us_code.load_unit`` reads a unit's contents from
that, and a section's place in the Code too, so the viewer's breadcrumb goes
up exactly and one fetch serves a whole title.  A section's own page names
the sections before and after it.

The fixtures are the OLRC's own markup, cut down (November 2026).  Nothing
here touches the network.
"""

import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import courtlistener_gui
import us_code
from us_code import (
    UnitEntry,
    UnitNotFound,
    UscUnit,
    _parse_tree,
    crumb_label,
    load_section,
    load_unit,
    olrc_adjacent,
    open_unit_entry,
)


def sections(title, *rows):
    links = "".join(
        f'<li><a class="section-link" href="us/usc/t{title}/s{number}/">'
        f'§ {number.replace("-", "–")} — {text}</a></li>'
        for number, text in rows)
    return f'<ul class="tree-sections">{links}</ul>'


def node(title, slug, head, span, *inside):
    """A unit: its summary, its front matter link, then what is in it."""
    name = head.split("⁠—")[0]
    return (f'<details class="tree-node"><summary>{head} '
            f'<span class="section-range">({span})</span></summary>'
            f'<ul class="tree-sections"><li><a href="us/usc/t{title}/{slug}-'
            f'front.html">{name}⁠—Front Matter</a></li></ul>'
            + "".join(inside) + "</details>")


TITLE_42 = node(
    "42", "chapter21", "CHAPTER 21⁠—CIVIL RIGHTS",
    "sections 1981 to 2000h–6",
    node("42", "chapter21-subchapterI", "SUBCHAPTER I⁠—GENERALLY",
         "sections 1981 to 1996b",
         sections("42", ("1981", "Equal rights under the law"),
                  ("1983", "Civil action for deprivation of rights"))),
    node("42", "chapter21-subchapterVI",
         "SUBCHAPTER VI⁠—EQUAL EMPLOYMENT OPPORTUNITIES",
         "sections 2000e to 2000e–17",
         sections("42", ("2000e-2", "Unlawful employment practices"))),
) + node(
    "42", "chapter5", "[CHAPTER 5⁠—REPEALED]", "section 201",
    sections("42", ("201", "Repealed.  July 1, 1944, ch. 373 , title XIII, "
                           "§ 1313,  58 Stat. 714")),
)

# Topics with no designation share one name under their unit.
TITLE_12 = node(
    "12", "chapter7", "CHAPTER 7⁠—FARM CREDIT", "sections 636 to 1020",
    node("12", "chapter7-level", "Examinations", "sections 636 to 640",
         sections("12", ("636", "Omitted"))),
    node("12", "chapter7-level", "Penalties", "section 641",
         sections("12", ("641", "Repealed"))),
)

# A repealed chapter, and the new one that took its number.
TITLE_26 = node(
    "26", "subtitleD", "Subtitle D⁠—Miscellaneous Excise Taxes",
    "sections 4001 to 5000D",
    node("26", "subtitleD-chapter38", "[CHAPTER 38⁠—REPEALED]",
         "sections 4521 to 4601", sections("26", ("4521", "Repealed"))),
    node("26", "subtitleD-chapter38",
         "CHAPTER 38⁠—ENVIRONMENTAL TAXES", "sections 4611 to 4682",
         sections("26", ("4611", "Imposition of tax"))),
)

TITLE_18 = node(
    "18", "partI", "PART I⁠—CRIMES", "sections 1 to 2725",
    node("18", "partI-chapter44", "CHAPTER 44⁠—FIREARMS",
         "sections 921 to 934", sections("18", ("922", "Unlawful acts"))),
)

BROWSE = ('<details class="tree-title" data-lazy-title="41"><summary>* Title 41 '
          "— PUBLIC CONTRACTS</summary></details>"
          '<details class="tree-title" data-lazy-title="42"><summary>Title 42 '
          "— THE PUBLIC HEALTH AND WELFARE</summary></details>")

SECTION_1983 = (
    '<nav class="section-nav"><a href="../../t42/s1982/" class="prev-section">'
    "&laquo; Previous</a><span class=\"breadcrumb\"></span>"
    '<a href="../../t42/s1984/" class="next-section">Next &raquo;</a></nav>'
    '<div id="content" class="page_content_internal section-page">'
    '<h3 class="section-head">§1983. Civil action</h3>'
    '<div class="statutory-body-container">'
    '<p class="statutory-body">Every person</p></div>'
)


class _Response:
    def __init__(self, status=200, text=""):
        self.status_code = status
        self.content = text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Server Error")


def _site(pages):
    """A requests stand-in: the page, _Response or exception *pages* gives
    each URL's end, 404 for the rest.  Records the URLs asked for."""
    asked = []

    def get(url, **_kw):
        asked.append(url)
        for end, page in pages.items():
            if url.endswith(end):
                if isinstance(page, BaseException):
                    raise page
                return page if isinstance(page, _Response) \
                    else _Response(200, page)
        return _Response(404)

    return patch.dict(sys.modules, {"requests": SimpleNamespace(get=get)}), \
        asked


class _Fresh(unittest.TestCase):
    def setUp(self):
        self._reset()
        self.addCleanup(self._reset)

    @staticmethod
    def _reset():
        us_code._cache.clear()
        us_code._contents_cache.clear()
        us_code._olrc_rest_until = 0.0


class LabelTests(unittest.TestCase):
    def test_labels_as_the_reader_shows_them(self):
        self.assertEqual(crumb_label("SUBCHAPTER I"), "Subchapter I")
        self.assertEqual(crumb_label("part 5"), "Part 5")
        self.assertEqual(crumb_label("TITLE 42"), "Title 42")
        self.assertEqual(crumb_label("Subpart C"), "Subpart C")

    def test_a_unit_with_no_designation_is_named_as_printed(self):
        self.assertEqual(crumb_label("GENERAL PROVISIONS"),
                         "GENERAL PROVISIONS")
        self.assertEqual(crumb_label("Penalties"), "Penalties")


class TreeTests(unittest.TestCase):
    def setUp(self):
        self.contents = _parse_tree(TITLE_42, "42")

    def test_a_titles_chapters(self):
        title = self.contents.units["title42"]
        self.assertEqual((title.label, title.heading, title.crumbs),
                         ("Title 42", "TITLE 42", []))
        self.assertEqual(title.url,
                         "https://uscode.house.gov/us/usc/t42/frontmatter/")
        self.assertEqual([entry.granule for entry in title.entries],
                         ["title42-chapter21", "title42-chapter5"])

    def test_a_chapters_subchapters(self):
        chapter = self.contents.units["title42-chapter21"]
        self.assertEqual((chapter.label, chapter.heading),
                         ("Chapter 21", "CHAPTER 21—CIVIL RIGHTS"))
        self.assertEqual(chapter.url, "https://uscode.house.gov/us/usc/t42/"
                                      "chapter21-front.html")
        self.assertEqual(
            [(e.kind, e.label, e.heading, e.unit_kind, e.designation,
              e.granule, e.first_section) for e in chapter.entries], [
                ("unit", "Subchapter I", "GENERALLY", "subchapter", "I",
                 "title42-chapter21-subchapterI", "1981"),
                ("unit", "Subchapter VI", "EQUAL EMPLOYMENT OPPORTUNITIES",
                 "subchapter", "VI", "title42-chapter21-subchapterVI",
                 "2000e"),
            ])

    def test_a_subchapters_sections(self):
        unit = self.contents.units["title42-chapter21-subchapterVI"]
        self.assertEqual(
            [(e.kind, e.label, e.heading, e.section) for e in unit.entries],
            [("section", "§ 2000e–2", "Unlawful employment practices",
              "2000e-2")])

    def test_the_units_above_a_unit(self):
        self.assertEqual(
            self.contents.units["title42-chapter21-subchapterI"].crumbs,
            [("title42", "Title 42"), ("title42-chapter21", "Chapter 21")])

    def test_where_a_section_sits(self):
        want = [("title42", "Title 42"), ("title42-chapter21", "Chapter 21"),
                ("title42-chapter21-subchapterI", "Subchapter I")]
        self.assertEqual(self.contents.crumbs("1983"), want)

    def test_whatever_the_case_of_the_number_asked_for(self):
        contents = _parse_tree(node(
            "26", "subtitleA", "Subtitle A⁠—Income Taxes", "section 36B",
            sections("26", ("36B", "Refundable credit"))), "26")
        self.assertTrue(contents.crumbs("36b"))

    def test_a_section_the_title_does_not_list(self):
        self.assertIsNone(self.contents.crumbs("99999"))

    def test_a_repealed_chapter(self):
        title = self.contents.units["title42"]
        chapter = self.contents.units["title42-chapter5"]
        self.assertEqual(chapter.label, "Chapter 5")
        self.assertEqual(title.entries[1].heading, "REPEALED")
        self.assertEqual(title.entries[1].first_section, "201")
        self.assertEqual(
            chapter.entries[0].heading,
            "Repealed. July 1, 1944, ch. 373, title XIII, § 1313, 58 Stat. 714")

    def test_topics_of_one_name_stay_apart(self):
        contents = _parse_tree(TITLE_12, "12")
        chapter = contents.units["title12-chapter7"]
        self.assertEqual(
            [(e.label, e.heading, e.unit_kind, e.designation)
             for e in chapter.entries],
            [("Examinations", "", "", ""), ("Penalties", "", "", "")])
        first, second = (contents.units[e.granule] for e in chapter.entries)
        self.assertEqual([e.section for e in first.entries], ["636"])
        self.assertEqual([e.section for e in second.entries], ["641"])
        self.assertEqual(contents.crumbs("641")[-1],
                         (second.granule, "Penalties"))

    def test_a_chapter_and_the_one_that_took_its_number(self):
        contents = _parse_tree(TITLE_26, "26")
        repealed, new = contents.units["title26-subtitleD"].entries
        self.assertEqual(repealed.heading, "REPEALED")
        self.assertEqual(new.heading, "ENVIRONMENTAL TAXES")
        self.assertEqual(
            [e.section for e in contents.units[new.granule].entries], ["4611"])
        self.assertEqual(
            [e.section for e in contents.units[repealed.granule].entries],
            ["4521"])


class LoadUnitTests(_Fresh):
    def test_a_unit_by_its_id(self):
        stub, _asked = _site({"browse-tree/t42.html": TITLE_42})
        with stub:
            unit = load_unit("title42-chapter21-subchapterVI")
        self.assertEqual(unit.heading,
                         "SUBCHAPTER VI—EQUAL EMPLOYMENT OPPORTUNITIES")

    def test_one_fetch_serves_a_title(self):
        stub, asked = _site({"browse-tree/t42.html": TITLE_42})
        with stub:
            load_unit("title42-chapter21")
            load_unit("title42-chapter21-subchapterI")
        self.assertEqual(len(asked), 1)

    def test_a_titles_own_heading(self):
        stub, _asked = _site({"browse-tree/t42.html": TITLE_42,
                              "browse.html": BROWSE})
        with stub:
            unit = load_unit("title42")
        self.assertEqual(unit.heading,
                         "TITLE 42—THE PUBLIC HEALTH AND WELFARE")

    def test_its_contents_without_the_list_of_titles(self):
        stub, _asked = _site({"browse-tree/t42.html": TITLE_42,
                              "browse.html": ConnectionError("unreachable")})
        with stub:
            unit = load_unit("title42")
        self.assertEqual(unit.heading, "TITLE 42")
        self.assertEqual(len(unit.entries), 2)

    def test_no_such_unit(self):
        stub, _asked = _site({"browse-tree/t42.html": TITLE_42})
        with stub, self.assertRaises(UnitNotFound):
            load_unit("title42-chapter9999")

    def test_no_such_title(self):
        stub, _asked = _site({})
        with stub, self.assertRaises(UnitNotFound):
            load_unit("title99")

    def test_a_site_that_fails_is_not_a_missing_unit(self):
        for down in (ConnectionError("unreachable"), _Response(503),
                     "<html>Under Maintenance</html>"):
            with self.subTest(down=down):
                self._reset()
                stub, _asked = _site({"browse-tree/t42.html": down})
                with stub, self.assertRaises(RuntimeError) as ctx:
                    load_unit("title42-chapter21")
                self.assertNotIsInstance(ctx.exception, LookupError)
                self.assertFalse(us_code._contents_cache)

    def test_an_entry_opens_by_its_id(self):
        stub, _asked = _site({"browse-tree/t42.html": TITLE_42})
        with stub:
            chapter = load_unit("title42-chapter21")
            unit = open_unit_entry(chapter.entries[0], chapter)
        self.assertEqual(unit.label, "Subchapter I")


class ViewerIdTests(_Fresh):
    """Breadcrumbs saved in a bookmark in the days of the OLRC's viewer."""

    def _opens(self, granule, tree, title):
        stub, _asked = _site({f"browse-tree/t{title}.html": tree})
        with stub:
            return load_unit(granule).granule

    def test_roman_numerals_as_digits(self):
        self.assertEqual(
            self._opens("title42-chapter21-subchapter1", TITLE_42, "42"),
            "title42-chapter21-subchapterI")
        self.assertEqual(
            self._opens("title42-chapter21-subchapter6", TITLE_42, "42"),
            "title42-chapter21-subchapterVI")

    def test_a_chapter_hung_off_its_title(self):
        self.assertEqual(self._opens("title18-chapter44", TITLE_18, "18"),
                         "title18-partI-chapter44")

    def test_the_end_of_an_id_is_matched_whole(self):
        # "chapter4" is not the end of "chapter44"
        with self.assertRaises(UnitNotFound):
            self._opens("title18-chapter4", TITLE_18, "18")


class SectionPlaceTests(_Fresh):
    def test_a_section_opens_with_its_place_in_the_code(self):
        stub, _asked = _site({"browse-tree/t42.html": TITLE_42,
                              "t42/s1983/": SECTION_1983})
        with stub:
            doc = load_section("42", "1983")
        self.assertEqual(doc.source, "olrc")
        self.assertEqual(doc.url,
                         "https://uscode.house.gov/us/usc/t42/s1983/")
        self.assertEqual(doc.crumbs[-1],
                         ("title42-chapter21-subchapterI", "Subchapter I"))

    def test_a_section_does_not_wait_on_its_title_for_long(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def get(url, **_kw):
            if "browse-tree" in url:
                release.wait(5)
                return _Response(200, TITLE_42)
            return _Response(200, SECTION_1983)

        with patch.dict(sys.modules,
                        {"requests": SimpleNamespace(get=get)}), \
                patch.object(us_code, "_CRUMBS_WAIT_S", 0.01):
            doc = load_section("42", "1983")
        self.assertEqual(doc.source, "olrc")
        self.assertEqual(doc.crumbs, [("title42", "Title 42")])

    def test_without_its_title_it_opens_all_the_same(self):
        stub, _asked = _site({"t42/s1983/": SECTION_1983})
        with stub:
            doc = load_section("42", "1983")
        self.assertEqual(doc.source, "olrc")
        self.assertEqual(doc.crumbs, [("title42", "Title 42")])

    def test_a_section_printed_in_a_range_with_the_one_before(self):
        # 42 U.S.C. § 4 is printed in "§§ 3, 4": its page sends the reader on.
        stub, asked = _site({
            "browse-tree/t42.html": TITLE_42,
            "t42/s1983/": SECTION_1983,
            "t42/s1982/": '<!DOCTYPE html><html><head><meta charset="UTF-8">'
                          '<meta http-equiv="refresh" content="0; url='
                          '../s1983/"><link rel="canonical" href="../s1983/">'
                          "</head><body>Redirecting to the current edition"
                          "&hellip;</body></html>",
        })
        with stub:
            doc = load_section("42", "1982")
        self.assertEqual(doc.section, "1982")
        self.assertEqual(doc.url,
                         "https://uscode.house.gov/us/usc/t42/s1983/")
        self.assertEqual(doc.heading, "§1983. Civil action")
        self.assertEqual(doc.crumbs[-1][1], "Subchapter I")
        self.assertIn("https://uscode.house.gov/us/usc/t42/s1983/", asked)

    def test_a_stub_that_leads_to_itself_is_given_up_on(self):
        loop = '<meta http-equiv="refresh" content="0; url=../s5/">'
        stub, asked = _site({"browse-tree/t42.html": TITLE_42,
                             "t42/s5/": loop})
        with stub, self.assertRaises(RuntimeError):
            us_code._olrc_section("42", "5")
        self.assertLessEqual(
            len([url for url in asked if "/s5/" in url]), 3)


class NeighborTests(unittest.TestCase):
    def test_prev_and_next_come_from_the_page(self):
        self.assertEqual(olrc_adjacent(SECTION_1983),
                         (("42", "1982"), ("42", "1984")))

    def test_across_the_end_of_a_title(self):
        page = ('<a href="../../t41/s8707/" class="prev-section">Previous</a>'
                '<a href="../../t42/s2/" class="next-section">Next</a>')
        self.assertEqual(olrc_adjacent(page), (("41", "8707"), ("42", "2")))

    def test_the_first_section_of_all(self):
        page = '<a href="../../t1/s2/" class="next-section">Next</a>'
        self.assertEqual(olrc_adjacent(page), (None, ("1", "2")))

    def test_the_section_needs_no_one_asked(self):
        doc = us_code.UscSection(title="42", section="1983", url="u",
                                 adjacent=(("42", "1982"), ("42", "1984")))
        with patch("us_code.load_unit") as contents:
            self.assertEqual(doc.neighbors(),
                             (("42", "1982"), ("42", "1984")))
        contents.assert_not_called()


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

    def test_a_unit_answers_what_the_viewer_asks_of_any_contents(self):
        unit = UscUnit(title="42", granule="title42", url="u", label="Title 42",
                       heading="TITLE 42", entries=[
                           UnitEntry("section", 0, "§ 1", section="1")])
        self.assertFalse(unit.partial)
        self.assertEqual(unit.host, "uscode.house.gov")


if __name__ == "__main__":
    unittest.main()
