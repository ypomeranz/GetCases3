"""The C.F.R.'s tables of contents, and going up to them from a section.

A C.F.R. section in the statute viewer shows where it sits in its title —
"Title 29 › Subtitle B › Chapter XIV › Part 1614 › Subpart A › § 1614.106" —
and Alt+Up, or a click on a level, shows that level's contents in the same
window, as a U.S. Code section does (test_us_code_units).  The units come
from eCFR's ancestry service; their contents from its structure service.
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

import ecfr

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui
except Exception:  # pragma: no cover - depends on the machine
    courtlistener_gui = None


def _section(ident, heading, reserved=False):
    return {"type": "section", "identifier": ident,
            "label_level": f"§ {ident}", "label_description": heading,
            "reserved": reserved}


# eCFR's structure of title 29 down to part 1614 (?part=1614), cut down.
PART_1614 = {
    "type": "title", "identifier": "29", "label": "Title 29—Labor",
    "children": [{
        "type": "subtitle", "identifier": "B", "label_level": "Subtitle B",
        "label": "Subtitle B—Regulations Relating to Labor",
        "children": [{
            "type": "chapter", "identifier": "XIV",
            "label_level": " Chapter XIV",
            "label": " Chapter XIV—Equal Employment Opportunity Commission",
            "children": [{
                "type": "part", "identifier": "1614",
                "label_level": "Part 1614",
                "label": "Part 1614—Federal Sector Equal Employment "
                         "Opportunity",
                "label_description": "Federal Sector Equal Employment "
                                     "Opportunity",
                "children": [
                    {"type": "subpart", "identifier": "A",
                     "label_level": "Subpart A",
                     "label": "Subpart A—Agency Program",
                     "label_description": "Agency Program",
                     "children": [
                         _section("1614.101", "General policy."),
                         _section("1614.106", "Individual complaints."),
                         _section("1614.111", "[Reserved]", reserved=True),
                     ]},
                    {"type": "subpart", "identifier": "B",
                     "label_level": "Subpart B",
                     "label": "Subpart B—Provisions Applicable to "
                              "Particular Complaints",
                     "label_description": "Provisions Applicable to "
                                          "Particular Complaints",
                     "children": [
                         {"type": "subject_group",
                          "identifier": "ECFR8a1b2c3",
                          "label": "Age Discrimination",
                          "label_description": "Age Discrimination",
                          "children": [
                              _section("1614.201", "Age Discrimination in "
                                                   "Employment Act."),
                          ]},
                     ]},
                ]}]}]}]}

ANCESTRY = {"ancestors": [
    {"type": "title", "identifier": "29", "label_level": "Title 29"},
    {"type": "subtitle", "identifier": "B", "label_level": "Subtitle B"},
    {"type": "chapter", "identifier": "XIV", "label_level": " Chapter XIV"},
    {"type": "part", "identifier": "1614", "label_level": "Part 1614"},
    {"type": "subject_group", "identifier": "ECFRxyz", "label_level": ""},
    {"type": "subpart", "identifier": "A", "label_level": "Subpart A"},
    {"type": "section", "identifier": "1614.106",
     "label_level": "§ 1614.106"},
]}


class CrumbTests(unittest.TestCase):
    def test_the_units_a_section_sits_in(self):
        response = mock.Mock(json=lambda: ANCESTRY)
        with mock.patch("requests.get", return_value=response) as get:
            crumbs = ecfr.section_crumbs("29", "1614.106", "2026-10-08")
        self.assertIn("ancestry/2026-10-08/title-29.json",
                      get.call_args.args[0])
        # A subject group is a heading over sections, not a unit.
        self.assertEqual(crumbs, [
            ("29", "Title 29"),
            ("29/subtitle:B", "Subtitle B"),
            ("29/subtitle:B/chapter:XIV", "Chapter XIV"),
            ("29/subtitle:B/chapter:XIV/part:1614", "Part 1614"),
            ("29/subtitle:B/chapter:XIV/part:1614/subpart:A", "Subpart A"),
        ])


class LoadUnitTests(unittest.TestCase):
    def setUp(self):
        self.asked = []

        def structure(title, date, **only):
            self.asked.append(only)
            return PART_1614

        for name, value in (("_structure", structure),
                            ("_issue_date", lambda title: "2026-10-08")):
            patcher = mock.patch.object(ecfr, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_part_lists_its_subparts_and_their_sections(self):
        unit = ecfr.load_unit("29/subtitle:B/chapter:XIV/part:1614")
        self.assertEqual(self.asked, [{"part": "1614"}])
        self.assertEqual((unit.label, unit.kind), ("Part 1614", "cfr"))
        self.assertEqual(unit.crumbs, [
            ("29", "Title 29"), ("29/subtitle:B", "Subtitle B"),
            ("29/subtitle:B/chapter:XIV", "Chapter XIV")])
        self.assertEqual(
            unit.url, "https://www.ecfr.gov/current/title-29/subtitle-B/"
                      "chapter-XIV/part-1614")
        self.assertEqual(
            [(e.kind, e.depth, e.label, e.section or e.granule)
             for e in unit.entries],
            [("unit", 0, "Subpart A",
              "29/subtitle:B/chapter:XIV/part:1614/subpart:A"),
             ("section", 1, "§ 1614.101", "1614.101"),
             ("section", 1, "§ 1614.106", "1614.106"),
             ("unit", 0, "Subpart B",
              "29/subtitle:B/chapter:XIV/part:1614/subpart:B"),
             ("group", 1, "Age Discrimination", ""),
             ("section", 2, "§ 1614.201", "1614.201")])

    def test_a_subpart_its_sections(self):
        unit = ecfr.load_unit(
            "29/subtitle:B/chapter:XIV/part:1614/subpart:A")
        self.assertEqual([e.label for e in unit.entries],
                         ["§ 1614.101", "§ 1614.106"])
        self.assertEqual(unit.crumbs[-1],
                         ("29/subtitle:B/chapter:XIV/part:1614",
                          "Part 1614"))

    def test_a_chapter_its_parts_alone(self):
        unit = ecfr.load_unit("29/subtitle:B/chapter:XIV")
        self.assertEqual(self.asked, [{"chapter": "XIV"}])
        self.assertEqual([(e.kind, e.label) for e in unit.entries],
                         [("unit", "Part 1614")])

    def test_a_unit_the_title_does_not_have(self):
        with self.assertRaises(RuntimeError):
            ecfr.load_unit("29/subtitle:B/chapter:XV")


@unittest.skipIf(courtlistener_gui is None, "courtlistener_gui needs tkinter")
class ViewerTests(unittest.TestCase):
    def test_a_section_goes_up_to_its_unit(self):
        shown = []
        doc = SimpleNamespace(kind="cfr", section="1614.106",
                              label="29 C.F.R. § 1614.106",
                              crumbs=[("29", "Title 29"),
                                      ("29/part:1614", "Part 1614")])
        window = SimpleNamespace(_unit=None, _doc=doc,
                                 _show_unit=shown.append)
        courtlistener_gui._StatuteWindow._go_up(window)
        self.assertEqual(shown, ["29/part:1614"])

    def test_its_contents_come_from_the_ecfr(self):
        window = SimpleNamespace(_doc=SimpleNamespace(kind="cfr"))
        self.assertIs(
            courtlistener_gui._StatuteWindow._contents_source(window), ecfr)


if __name__ == "__main__":
    unittest.main()
