"""The SEC's Decisions and Reports: read as opinions cite them ("8 S.E.C. 893,
915-921"), found in the shipped page index (every printed page of the 58
volumes placed in HathiTrust's scans), and opened at HathiTrust, in the web
browser, at the cited page.

The citations are from SEC v. Chenery Corp., 332 U.S. 194, 197-98 (1947),
and from decisions whose citations and years are well known.
"""

import json
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import brief_compiler
import citations
import courtlistener_gui
import sec_decisions as sec

CHENERY = (
    "It felt that the officers and directors of a holding company in process "
    "of reorganization under the Act were fiduciaries and were under a duty "
    "not to trade in the securities of that company during the reorganization "
    "period. 8 S.E.C. 893, 915-921. And so the plan was amended to provide "
    "that the preferred stock acquired by the management, unlike that held by "
    "others, was not to be converted into the new common stock; instead, it "
    "was to be surrendered at cost plus dividends accumulated since the "
    "purchase dates. As amended, the plan was approved by the Commission over "
    "the management’s objections. 10 S.E.C. 200."
)


def specs(text):
    return [(text[s:e], sec.parse_spec(spec)) for s, e, spec in sec.iter_cites(text)]


class DetectionTests(unittest.TestCase):
    def test_chenery(self):
        self.assertEqual(specs(CHENERY), [
            ("8 S.E.C. 893, 915-921", {"vol": 8, "page": 893, "pin": 915}),
            ("10 S.E.C. 200", {"vol": 10, "page": 200}),
        ])

    def test_the_reporter_however_it_is_spaced(self):
        for cite in ("10 S. E. C. 200", "10 S.E. C. 200", "10 SEC 200"):
            self.assertEqual(specs(cite), [(cite, {"vol": 10, "page": 200})], cite)

    def test_the_first_pin_rides_in_the_link_and_the_rest_are_their_own(self):
        self.assertEqual(specs("10 S.E.C. 200, 205, 207-208 (1941)"), [
            ("10 S.E.C. 200, 205", {"vol": 10, "page": 200, "pin": 205}),
            ("207-208", {"vol": 10, "page": 200, "pin": 207}),
        ])
        self.assertEqual(specs("8 S.E.C. 893, at 915 n.3, 920")[1],
                         ("920", {"vol": 8, "page": 893, "pin": 920}))

    def test_a_short_form_is_a_page_of_the_decision_cited_before(self):
        found = specs("8 S.E.C. 893 (1941); later, 8 S.E.C., at 917.")
        self.assertEqual(found[1], ("8 S.E.C., at 917", {"vol": 8, "page": 893, "pin": 917}))

    def test_a_short_form_alone_finds_its_decision_in_the_index(self):
        self.assertEqual(specs("8 S.E.C. at 915"),
                         [("8 S.E.C. at 915", {"vol": 8, "page": 893, "pin": 915})])

    def test_a_following_citation_is_no_pin(self):
        self.assertEqual([h for h, _s in specs("8 S.E.C. 893, 10 S.E.C. 200")],
                         ["8 S.E.C. 893", "10 S.E.C. 200"])
        self.assertEqual([h for h, _s in specs("8 S.E.C. 893, 1941 SEC LEXIS 12")],
                         ["8 S.E.C. 893"])

    def test_other_sec_publications_and_words_are_left_alone(self):
        for text in ("45 S.E.C. Docket 1234", "1 S.E.C. Jud. Dec. 12",
                     "22 S.E.C. Ann. Rep. 45", "SEC v. Chenery Corp., 318 U.S. 80",
                     "the 2 SEC 10-Ks it filed", "under 3 SEC 12(b)"):
            self.assertEqual(specs(text), [], text)

    def test_only_volumes_and_pages_the_series_has(self):
        self.assertEqual(specs("59 S.E.C. 12"), [])      # 58 volumes
        self.assertEqual(specs("8 S.E.C. 1500"), [])     # volume 8 ends at 1100
        self.assertEqual(sec.last_page(8), 1100)


class IndexTests(unittest.TestCase):
    def test_chenery_pages_in_the_scans(self):
        self.assertEqual(sec.locate(8, 893),
                         sec.ScanPage("osu.32435025999830", 913))
        self.assertEqual(sec.page_url(sec.make_spec(8, 893, 915)),
                         "https://babel.hathitrust.org/cgi/pt?id=osu.32435025999830&seq=935")
        self.assertEqual(sec.page_url(sec.make_spec(10, 200)),
                         "https://babel.hathitrust.org/cgi/pt?id=osu.32435056042609&seq=218")

    def test_every_volume_is_mapped(self):
        for vol in sec.VOLUMES:
            self.assertGreater(sec.last_page(vol), 600, vol)
            self.assertIsNotNone(sec.locate(vol, 1), vol)
            self.assertIsNotNone(sec.locate(vol, sec.last_page(vol)), vol)

    def test_pages_a_copy_lacks_come_from_one_that_has_them(self):
        # Ohio State's copy of volume 1 is missing leaves (after 392, 422 and
        # 453); Illinois' has them all.
        for page in (393, 425, 455):
            self.assertEqual(sec.locate(1, page).htid, "uiug.30112070124661", page)

    def test_decisions_begin_where_they_are_cited_and_carry_their_years(self):
        known = [(8, 893, 1941), (10, 200, 1941), (40, 907, 1961), (27, 629, 1948),
                 (13, 676, 1943), (44, 633, 1971), (6, 386, 1939), (13, 373, 1943),
                 (47, 471, 1981), (51, 93, 1992)]
        for vol, page, year in known:
            self.assertEqual(sec.decision_start(vol, page), page, (vol, page))
            self.assertEqual(sec.decision_year(vol, page), year, (vol, page))
        self.assertEqual(sec.decision_start(8, 915), 893)

    def test_without_the_index_citations_still_open_somewhere(self):
        empty = sec._Index()
        with patch.object(sec, "_INDEX", empty):
            self.assertFalse(sec.is_available())
            self.assertEqual(specs("8 S.E.C. 893"), [("8 S.E.C. 893", {"vol": 8, "page": 893})])
            self.assertEqual(sec.page_url(sec.make_spec(8, 893)), sec.CATALOG_URL)
            self.assertEqual(sec.spec_label(sec.make_spec(8, 893)), "8 S.E.C. 893")


class SpecTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(sec.spec_label(sec.make_spec(8, 893, 915)), "8 S.E.C. 893, 915 (1941)")
        self.assertEqual(sec.spec_label(sec.make_spec(8, 893, 915), with_pin=False),
                         "8 S.E.C. 893 (1941)")

    def test_a_pin_on_the_first_page_is_no_pin(self):
        self.assertEqual(sec.make_spec(8, 893, 893), sec.make_spec(8, 893))

    def test_another_page_of_the_decision(self):
        spec = sec.make_spec(8, 893, 915)
        self.assertEqual(sec.parse_spec(sec.with_page(spec, "917")),
                         {"vol": 8, "page": 893, "pin": 917})
        self.assertEqual(sec.base_spec(spec), sec.make_spec(8, 893))
        self.assertTrue(sec.pin_in_range(spec, 917))
        self.assertFalse(sec.pin_in_range(spec, 880))       # before it begins
        self.assertFalse(sec.pin_in_range(spec, 1450))      # past the volume

    def test_spotlight_queries(self):
        self.assertEqual(sec.parse_query("8 S.E.C. 893"), ("sec", sec.make_spec(8, 893)))
        self.assertEqual(sec.parse_query("Federal Water Serv. Corp., 8 S.E.C. 893, 915 (1941)"),
                         ("sec", sec.make_spec(8, 893, 915)))
        self.assertEqual(sec.parse_query("10 S.E.C. 200, 205, 207."),
                         ("sec", sec.make_spec(10, 200, 205)))
        self.assertIsNone(sec.parse_query("see 8 S.E.C. 893 and more"))
        self.assertIsNone(sec.parse_query("Roe v. Wade, 410 U.S. 113"))


class DetectLinksTests(unittest.TestCase):
    """What the opinion text, scans and briefs link (citations.detect_links)."""

    def actions(self, text):
        return [(text[s:e], a) for s, e, a in citations.detect_links(text)]

    def test_chenery_opens_the_sec_decisions_not_a_case_search(self):
        got = self.actions(CHENERY)
        self.assertEqual([(h, a[0]) for h, a in got],
                         [("8 S.E.C. 893, 915-921", "sec"), ("10 S.E.C. 200", "sec")])

    def test_an_id_is_another_page_of_the_decision(self):
        got = self.actions("10 S.E.C. 200. Id., at 205.")
        self.assertEqual(got[1], ("Id., at 205",
                                  ("sec", sec.make_spec(10, 200, 205))))

    def test_an_id_past_the_decision_looks_further_back(self):
        got = self.actions("Roe v. Wade, 410 U.S. 113 (1973); 10 S.E.C. 200. Id. at 153.")
        self.assertEqual(got[-1], ("Id. at 153", ("cite", "410 U.S. 113@153")))

    def test_the_decisions_name_comes_into_the_link(self):
        got = self.actions("See In re Federal Water Service Corp., 8 S.E.C. 893 (1941).")
        self.assertEqual(got, [("In re Federal Water Service Corp., 8 S.E.C. 893",
                                ("sec", sec.make_spec(8, 893)))])

    def test_a_short_form_is_not_read_as_a_case(self):
        got = self.actions("8 S.E.C. 893 (1941). 8 S.E.C. at 917.")
        self.assertEqual([a for _h, a in got], [("sec", sec.make_spec(8, 893)),
                                                 ("sec", sec.make_spec(8, 893, 917))])


class BriefCompilerTests(unittest.TestCase):
    def test_one_entry_a_decision_with_where_to_read_it(self):
        auths = brief_compiler.collect_authorities(
            "8 S.E.C. 893, 915 (1941); 8 S.E.C. at 917; 10 S.E.C. 200.")
        self.assertEqual([(a.kind, a.value) for a in auths],
                         [("sec", sec.make_spec(8, 893)), ("sec", sec.make_spec(10, 200))])
        self.assertEqual(auths[0].label(), "8 S.E.C. 893 (1941)")
        resolved = brief_compiler._resolve(Mock(), auths[0])
        self.assertIsNone(resolved.data)
        self.assertIn("babel.hathitrust.org/cgi/pt?id=osu.32435025999830&seq=913",
                      resolved.note)


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.opened = []
        patcher = patch.object(courtlistener_gui.webbrowser, "open",
                               side_effect=self.opened.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = SimpleNamespace(root=object(), record_case_view=Mock())

    def test_a_citation_opens_the_cited_page_in_the_browser(self):
        status = Mock()
        courtlistener_gui._open_sec(None, sec.make_spec(8, 893, 915), status, app=self.app)
        self.assertEqual(self.opened,
                         ["https://babel.hathitrust.org/cgi/pt?id=osu.32435025999830&seq=935"])
        self.assertIn("8 S.E.C. 893, 915 (1941)", status.call_args[0][0])

    def test_history_keeps_one_entry_a_decision(self):
        courtlistener_gui._open_sec(None, sec.make_spec(8, 893, 915), app=self.app)
        key, label, reopen, payload = self.app.record_case_view.call_args[0]
        self.assertEqual(key, f"sec:{sec.make_spec(8, 893)}")
        self.assertEqual(label, "8 S.E.C. 893, 915 (1941)")
        self.assertEqual(payload, {"type": "sec", "spec": sec.make_spec(8, 893, 915)})
        reopen()
        self.assertEqual(len(self.opened), 2)

    def test_history_reopens_it_after_a_restart(self):
        fake = SimpleNamespace(root=object(), _status_var=Mock())
        opener = courtlistener_gui.CourtListenerGUI._history_opener_from_payload(
            fake, {"type": "sec", "spec": sec.make_spec(10, 200)}, "10 S.E.C. 200 (1941)")
        with patch.object(courtlistener_gui, "_open_sec") as open_sec:
            opener()
        self.assertEqual(open_sec.call_args[0][1], sec.make_spec(10, 200))

    def test_briefs_and_scans_open_it_too(self):
        # A brief's link, a scan's link and Spotlight's all go this way.
        courtlistener_gui._follow_brief_action(
            self.app, None, ("sec", sec.make_spec(10, 200)), Mock())
        self.assertEqual(self.opened,
                         ["https://babel.hathitrust.org/cgi/pt?id=osu.32435056042609&seq=218"])
        self.assertEqual(courtlistener_gui._brief_action_category("sec"), "case")
        self.assertIn("sec", courtlistener_gui._SPOTLIGHT_CASE_ACTIONS)

    def test_right_click_opens_the_page_in_the_browser(self):
        courtlistener_gui._open_citation_in_browser(("sec", sec.make_spec(10, 200)))
        self.assertEqual(self.opened,
                         ["https://babel.hathitrust.org/cgi/pt?id=osu.32435056042609&seq=218"])


class _Text:
    """Just enough of a Tk Text widget to watch the linker write into it."""

    def __init__(self):
        self.runs = []

    def insert(self, _index, text, tags=()):
        self.runs.append((text, tags))

    def index(self, _what):
        return "1.0"

    def tag_add(self, *_args):
        pass


class TextWindowLinkerTests(unittest.TestCase):
    """The text window's own per-span linker (used where the whole-opinion
    pass can't run) links the same way."""

    def links(self, text):
        win = object.__new__(courtlistener_gui._ScholarTextWindow)
        win._text = _Text()
        win._pending_id = None
        win._recap_spec_index = {}
        win._short_cite_index = {}
        win._const_linked = set()
        win._last_cite_action = None
        actions = []

        def new_link(action):
            actions.append(action)
            return f"link{len(actions) - 1}"

        win._new_link = new_link
        win._insert_plain_with_links(text, ())
        out = []
        for run, tags in win._text.runs:
            tag = next((t for t in tags if t.startswith("link")), None)
            if tag is not None:
                out.append((run, actions[int(tag[4:])]))
        return out

    def test_chenery_and_an_id_after_it(self):
        self.assertEqual(self.links(CHENERY + " Id., at 205."), [
            ("8 S.E.C. 893, 915-921", ("sec", sec.make_spec(8, 893, 915))),
            ("10 S.E.C. 200", ("sec", sec.make_spec(10, 200))),
            ("Id., at 205", ("sec", sec.make_spec(10, 200, 205))),
        ])


SRC = pathlib.Path(courtlistener_gui.__file__).read_text(encoding="utf-8")


class GuiWiringTests(unittest.TestCase):
    def test_every_window_opens_it(self):
        # The text window, the statute viewer's cross-references, briefs and
        # scans (via _follow_brief_action), Spotlight, History.
        self.assertGreaterEqual(SRC.count("_open_sec("), 6)
        self.assertIn("sec_decisions.parse_query(query)", SRC)

    def test_the_text_windows_own_linker_knows_it(self):
        self.assertIn("sec_decisions.iter_cites(text)", SRC)
        self.assertIn("sec_decisions.with_page(la[1], pin)", SRC)


if __name__ == "__main__":
    unittest.main()
