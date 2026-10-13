"""The federal rules' tables of contents, and going up to them from a rule.

A rule opened in the statute viewer shows where it sits in its set — "Fed.
R. Civ. P. › Title III › Rule 12" — and Alt+Up, or a click on a level, shows
that level's table of contents in the same window, as a U.S. Code section
does (test_us_code_units).  Each set's index page on Cornell LII lists its
rules under its titles (the Evidence Rules' articles, the Bankruptcy Rules'
parts); the samples here are that page's markup, cut down.
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

import fed_rules

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui
except Exception:  # pragma: no cover - depends on the machine
    courtlistener_gui = None


FRCP_INDEX = """<html><body><main><article><div>
<p>These are the Federal Rules of Civil Procedure.</p><ul>
<li>RULES OF CIVIL PROCEDURE FOR THE UNITED STATES DISTRICT COURTS</li>
<li>TITLE I. SCOPE OF RULES; FORM OF ACTION<ul>
<li><a href="/rules/frcp/rule_1">Rule 1</a>. Scope and Purpose</li>
<li><a href="/rules/frcp/rule_2">Rule 2</a>. One Form of Action</li></ul></li>
<li>TITLE II. COMMENCING AN ACTION; SERVICE OF PROCESS<ul>
<li><a href="/rules/frcp/rule_3">Rule 3</a>. Commencing an Action</li>
<li><a href="/rules/frcp/rule_4">Rule 4</a>. Summons</li>
<li><a href="/rules/frcp/rule_4-1">Rule 4.1</a>. Serving Other Process</li>
</ul></li>
<li>TITLE III. PLEADINGS AND MOTIONS<ul>
<li><a href="/rules/frcp/rule_7">Rule 7</a>. Pleadings Allowed</li>
<li><a href="/rules/frcp/rule_12">Rule 12</a>. Defenses and Objections</li>
</ul></li>
<li>TITLE IX. SPECIAL PROCEEDINGS<ul>
<li><a href="/rules/frcp/rule_71.1">Rule 71.1</a>. Condemning Property</li>
<li>[Rule 71A. Renumbered Rule 71.1]</li></ul></li>
<li>XII. APPENDIX OF FORMS [Abrogated]<ul>
<li><a href="https://www.law.cornell.edu/node/6735414">Rules 1 - 7</a></li>
</ul></li>
<li>XIII. SUPPLEMENTAL RULES FOR ADMIRALTY OR MARITIME CLAIMS<ul>
<li><a href="/rules/frcp/rule_A">Rule A</a>. Scope of Rules</li>
<li><a href="/rules/frcp/rule_B">Rule B</a>. In Personam Actions</li>
</ul></li></ul>
<nav><ul><li><a href="/rules/frcp/title_I">TITLE I. SCOPE OF RULES</a></li>
<li><a href="/rules/frcp/title_III">TITLE III. PLEADINGS</a></li></ul>
<a href="/rules/frcp/rule_1" rel="next">Rule 1. Scope and Purpose</a></nav>
</div></article>
<footer><ul><li><a href="/lii/about">About LII</a></li></ul></footer>
</body></html>"""

FRBP_INDEX = """<html><body><article><ul>
<li>BANKRUPTCY RULES<ul>
<li><a href="/rules/frbp/rule_1001">Rule 1001</a>. Scope; Title.</li></ul></li>
<li>PART I—COMMENCEMENT OF CASE<ul>
<li><a href="/rules/frbp/rule_1002">Rule 1002</a>. Commencing a Case.</li>
</ul></li></ul>
<nav><ul><li><a href="/rules/frbp/rule_1001">Rule 1001. Scope; Title</a>
</li><li><a href="/rules/frbp/part_I">PART I</a></li></ul></nav>
</article></body></html>"""

FRE_INDEX = """<html><body><article><ul>
<li>ARTICLE III. PRESUMPTIONS IN CIVIL CASES<ul>
<li><a href="/rules/fre/rule_301">Rule 301</a>. Presumptions Generally</li>
</ul></li></ul></article></body></html>"""


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.index = fed_rules.parse_set_index("frcp", FRCP_INDEX)

    def test_the_rules_under_their_titles(self):
        parts = [(p.label, p.title, [r for r, _h in p.rules])
                 for p in self.index.parts]
        self.assertEqual(parts, [
            ("Title I", "Scope of Rules; Form of Action", ["1", "2"]),
            ("Title II", "Commencing an Action; Service of Process",
             ["3", "4", "4.1"]),
            ("Title III", "Pleadings and Motions", ["7", "12"]),
            ("Title IX", "Special Proceedings", ["71.1"]),
            ("Title XIII",
             "Supplemental Rules for Admiralty or Maritime Claims",
             ["A", "B"]),
        ])

    def test_a_rule_s_number_is_read_from_its_link_s_words(self):
        # "rule_4-1" is Rule 4.1; the order serves previous/next, and read
        # from the addresses it had Rule 4 twice and no Supplemental Rules.
        self.assertEqual(self.index.order(),
                         ["1", "2", "3", "4", "4.1", "7", "12", "71.1",
                          "A", "B"])

    def test_headings_and_part_pages(self):
        title3 = self.index.parts[2]
        self.assertEqual(title3.rules[1], ("12", "Defenses and Objections"))
        self.assertEqual(title3.url,
                         "https://www.law.cornell.edu/rules/frcp/title_III")
        # A title whose page the index does not link: the index itself.
        self.assertEqual(self.index.parts[1].url,
                         "https://www.law.cornell.edu/rules/frcp")

    def test_the_book_navigation_is_no_list_of_rules(self):
        index = fed_rules.parse_set_index("frbp", FRBP_INDEX)
        self.assertEqual(index.loose, [("1001", "Scope; Title.")])
        self.assertEqual([(p.label, [r for r, _h in p.rules])
                          for p in index.parts], [("Part I", ["1002"])])
        self.assertEqual(index.order(), ["1002", "1001"])

    def test_civil_is_no_roman_numeral(self):
        index = fed_rules.parse_set_index("fre", FRE_INDEX)
        self.assertEqual(index.parts[0].label, "Article III")
        self.assertEqual(index.parts[0].title, "Presumptions in Civil Cases")


class LoadUnitTests(unittest.TestCase):
    def setUp(self):
        index = fed_rules.parse_set_index("frcp", FRCP_INDEX)
        patcher = mock.patch.object(fed_rules, "_set_index",
                                    return_value=index)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_set_lists_its_titles_and_their_rules(self):
        unit = fed_rules.load_unit("frcp")
        self.assertEqual((unit.label, unit.heading, unit.crumbs),
                         ("Fed. R. Civ. P.",
                          "Federal Rules of Civil Procedure", []))
        entries = [(e.kind, e.depth, e.label, e.section or e.granule)
                   for e in unit.entries[:5]]
        self.assertEqual(entries, [
            ("unit", 0, "Title I", "frcp/1"),
            ("section", 1, "Rule 1", "1"),
            ("section", 1, "Rule 2", "2"),
            ("unit", 0, "Title II", "frcp/2"),
            ("section", 1, "Rule 3", "3"),
        ])

    def test_a_title_lists_its_rules_under_its_set(self):
        unit = fed_rules.load_unit("frcp/3")
        self.assertEqual((unit.label, unit.heading),
                         ("Title III", "TITLE III. PLEADINGS AND MOTIONS"))
        self.assertEqual(unit.crumbs, [("frcp", "Fed. R. Civ. P.")])
        self.assertEqual([(e.label, e.heading) for e in unit.entries],
                         [("Rule 7", "Pleadings Allowed"),
                          ("Rule 12", "Defenses and Objections")])
        self.assertEqual(unit.kind, "rule")

    def test_a_title_entry_opens_that_title(self):
        whole = fed_rules.load_unit("frcp")
        title2 = next(e for e in whole.entries if e.label == "Title II")
        self.assertEqual(fed_rules.open_unit_entry(title2, whole).granule,
                         "frcp/2")

    def test_no_such_title(self):
        with self.assertRaises(RuntimeError):
            fed_rules.load_unit("frcp/40")

    def test_where_a_rule_sits(self):
        self.assertEqual(fed_rules.rule_crumbs("frcp", "12"),
                         [("frcp", "Fed. R. Civ. P."),
                          ("frcp/3", "Title III")])
        self.assertEqual(fed_rules.rule_crumbs("frcp", "999"),
                         [("frcp", "Fed. R. Civ. P.")])


@unittest.skipIf(courtlistener_gui is None, "courtlistener_gui needs tkinter")
class ViewerTests(unittest.TestCase):
    def test_a_rule_goes_up_to_its_title(self):
        shown = []
        doc = SimpleNamespace(kind="rule", section="12", label="Fed. R. Civ."
                              " P. 12", crumbs=[("frcp", "Fed. R. Civ. P."),
                                                ("frcp/3", "Title III")])
        window = SimpleNamespace(_unit=None, _doc=doc,
                                 _show_unit=shown.append)
        courtlistener_gui._StatuteWindow._go_up(window)
        self.assertEqual(shown, ["frcp/3"])

    def test_its_contents_come_from_the_rules(self):
        window = SimpleNamespace(_doc=SimpleNamespace(kind="rule"))
        self.assertIs(
            courtlistener_gui._StatuteWindow._contents_source(window),
            fed_rules)
        window = SimpleNamespace(_doc=SimpleNamespace(kind="usc"))
        self.assertIs(
            courtlistener_gui._StatuteWindow._contents_source(window),
            courtlistener_gui.us_code)

    def test_a_bookmarked_rule_keeps_its_place(self):
        doc = courtlistener_gui._SavedStatuteDoc({
            "paras": [["sechead", 0, "Rule 12. Defenses and Objections"]],
            "kind": "rule", "label": "Fed. R. Civ. P. 12",
            "crumbs": [["frcp", "Fed. R. Civ. P."], ["frcp/3", "Title III"]],
        })
        window = SimpleNamespace(_doc=doc)
        self.assertEqual(
            courtlistener_gui._StatuteWindow._doc_section(window), "12")
        self.assertEqual(doc.crumbs[-1], ("frcp/3", "Title III"))


if __name__ == "__main__":
    unittest.main()
