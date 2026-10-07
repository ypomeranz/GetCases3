"""Opening a case from Spotlight: every source asked at once.

The scan and Google Scholar's text race, and whichever comes first is what
the case opens on; the other is handed to that window when it arrives.
static.case.law's and CourtListener's texts open only once Scholar has failed
or had its head start and no scan has come either (see _CaseOpenRace).

Google Scholar's side: the one-second hop from a search to the opinion page
it found, once per case opened; the record of the cases Scholar has no copy
of; and a results page already listed this session standing in for a second
search.

Run with:  python -m unittest test_case_open_race -v
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import courtlistener_gui as gui
import google_scholar as gs
from google_scholar import GoogleScholarFetcher, ScholarResult


# ---------------------------------------------------------------------------
# The race's decisions, driven lane by lane (no threads, no network)
# ---------------------------------------------------------------------------

class _App:
    def __init__(self) -> None:
        self.timers: list = []
        self.root = SimpleNamespace(
            after=lambda ms, fn, *a: self.timers.append((ms, fn)))
        self.scans: list = []
        self.asked: list = []

    def _post_root(self, fn, *args):
        fn(*args)

    def _show_cited_case_pdf(self, *args, **kw):
        self.scans.append((args, kw))

    def _ask_which_scholar_case(self, *args):
        self.asked.append(args)


class _Watch:
    def __init__(self) -> None:
        self.cancelled = False
        self.claimed = False
        self.calls: list = []

    def to_text(self, reason):
        self.calls.append(("to_text", reason))

    def finish(self):
        self.calls.append(("finish",))

    def fail(self, message):
        self.calls.append(("fail", message))


class _Reader:
    """Stands in for _ScholarTextWindow: what it was opened with, and what
    was handed to it afterwards."""

    opened: list = []

    def __init__(self, parent, app, url, html, **kw) -> None:
        self.url, self.html, self.kw = url, html, kw
        self.handed: list = []
        _Reader.opened.append(self)

    def receive_race_scan(self, data, url, meta=None, item=None):
        self.handed.append(("scan", data, url))

    def race_scan_missing(self):
        self.handed.append(("no scan",))

    def _attach_scholar_version(self, url, html, note=""):
        self.handed.append(("scholar", url))

    def _retry_scholar_link(self, cite, pin, url):
        self.handed.append(("retry", url))

    def jump_to_cite_page(self, cite, pin):
        self.handed.append(("pin", pin))


def _text(kind: str):
    return SimpleNamespace(item={"caseName": "X"}, text="body", parts=[],
                           blocks=[], source_label=kind,
                           source_url=f"https://{kind}.test/x.json",
                           kind=kind)


SCAN = (b"%PDF-1", "https://loc.test/usrep365167.pdf", [(1, 1, (0, 0, 1, 1))],
        {"citation": ["365 U.S. 167"]}, "Monroe v. Pape")
PAGE = ("https://scholar.test/case=1", "<div>opinion</div>")


class RaceDecisionTests(unittest.TestCase):

    def setUp(self):
        _Reader.opened.clear()
        self.app = _App()
        self.watch = _Watch()
        self.nothing: list = []
        self.status: list = []
        self.race = gui._CaseOpenRace(
            self.app, "parent", cite="365 U.S. 167", name="Monroe v. Pape",
            watch=self.watch, status=self.status.append,
            on_nothing=lambda: self.nothing.append(1))
        patcher = mock.patch.object(gui, "_ScholarTextWindow", _Reader)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _after(self, seconds: float) -> None:
        """As if *seconds* had passed since the case was asked for."""
        self.race.t0 = time.monotonic() - seconds
        self.race._decide()

    def test_a_scan_first_opens_on_the_scan(self):
        self.race._settle("scan", True, SCAN)
        self.assertEqual(self.race.view, "scan")
        (args, kw), = self.app.scans
        self.assertEqual(args[1:3], SCAN[:2])
        self.assertIs(kw["race"], self.race)     # its text is the race's
        self.assertEqual(kw["page_meta"], SCAN[2])
        self.assertEqual(_Reader.opened, [])

    def test_scholar_s_text_first_opens_on_the_text(self):
        self.race._settle("scholar", True, PAGE)
        self.assertEqual(self.race.view, "scholar")
        reader, = _Reader.opened
        self.assertEqual((reader.url, reader.html), PAGE)
        # Its P waits for the race's scan rather than looking for its own.
        self.assertTrue(reader.kw["scan_pending"])
        self.assertEqual(self.app.scans, [])

    def test_the_scan_that_comes_after_goes_behind_the_text(self):
        self.race._settle("scholar", True, PAGE)
        self.race._settle("scan", True, SCAN)
        reader, = _Reader.opened
        self.assertEqual(reader.handed, [("scan", SCAN[0], SCAN[1])])
        self.assertEqual(self.app.scans, [])     # no second window

    def test_no_scan_after_the_text_leaves_it_to_look_for_itself(self):
        self.race._settle("scholar", True, PAGE)
        self.race.scan_missing("none")
        self.assertEqual(_Reader.opened[0].handed, [("no scan",)])

    def test_a_lesser_text_waits_for_scholar_s_head_start(self):
        self.race.scan_missing("none")
        self.race._settle("caselaw", True, _text("case_law"))
        self.assertIsNone(self.race.view)        # Scholar still has time
        self._after(self.race.GRACE_S + 0.1)
        self.assertEqual(self.race.view, "caselaw")
        self.assertEqual(_Reader.opened[0].kw["primary_source_kind"],
                         "case_law")

    def test_nor_while_a_scan_may_still_come(self):
        self.race._settle("scholar", False)
        self.race._settle("caselaw", True, _text("case_law"))
        self.assertIsNone(self.race.view)        # the scan is still coming
        self._after(self.race.GRACE_S + 0.1)
        self.assertEqual(self.race.view, "caselaw")

    def test_scholar_and_the_scan_both_failed_opens_one_at_once(self):
        self.race._settle("scholar", False)
        self.race.scan_missing("none")
        self.race._settle("caselaw", True, _text("case_law"))
        self.assertEqual(self.race.view, "caselaw")

    def test_static_case_law_s_text_before_courtlistener_s(self):
        self.race._settle("scholar", False)
        self.race.scan_missing("none")
        self.race._settle("cl", True, _text("courtlistener"))
        self.assertIsNone(self.race.view)        # static.case.law may come
        self.race._settle("caselaw", True, _text("case_law"))
        self.assertEqual(self.race.view, "caselaw")

    def test_courtlistener_s_when_static_case_law_has_none(self):
        self.race._settle("scholar", False)
        self.race.scan_missing("none")
        self.race._settle("caselaw", False)
        self.race._settle("cl", True, _text("courtlistener"))
        self.assertEqual(self.race.view, "cl")

    def test_scholar_s_text_arriving_late_lights_up_the_window(self):
        self.race._settle("scholar", False)
        self.race.scan_missing("none")
        self.race._settle("caselaw", True, _text("case_law"))
        self.race._state["scholar"] = "pending"   # as if still under way
        self.race._scholar_handed = False
        self.race._settle("scholar", True, PAGE)
        self.assertIn(("scholar", PAGE[0]), _Reader.opened[0].handed)

    def test_a_scholar_page_that_failed_to_load_is_retried_behind(self):
        self.race.scholar_retry_url = "https://scholar.test/case=9"
        self.race._settle("scholar", False)
        self.race.scan_missing("none")
        self.race._settle("caselaw", True, _text("case_law"))
        self.assertIn(("retry", "https://scholar.test/case=9"),
                      _Reader.opened[0].handed)

    def test_an_old_case_scholar_missed_before_gets_a_short_head_start(self):
        self.race.quick = True
        self.race.scan_missing("none")
        self.race._settle("caselaw", True, _text("case_law"))
        self._after(self.race.QUICK_GRACE_S - 1)
        self.assertIsNone(self.race.view)
        self._after(self.race.QUICK_GRACE_S + 0.1)
        self.assertEqual(self.race.view, "caselaw")

    def test_nothing_anywhere_runs_the_caller_s_further_tries(self):
        for lane in ("scholar", "caselaw", "cl"):
            self.race._settle(lane, False)
        self.assertEqual(self.nothing, [])
        self.race.scan_missing("none")
        self.assertEqual(self.nothing, [1])
        self.assertEqual(self.race.view, "nothing")

    def test_several_cases_at_scholar_s_citation_ask_which(self):
        self.race.scholar_mates = ["a", "b"]
        self.race.fetcher = object()
        for lane in ("scholar", "caselaw", "cl"):
            self.race._settle(lane, False)
        self.race.scan_missing("none")
        self.assertEqual(len(self.app.asked), 1)
        self.assertEqual(self.nothing, [])

    def test_a_page_the_scan_lookup_cannot_tell_opens_nothing_lesser(self):
        self.race._settle("caselaw", True, _text("case_law"))
        self.race.scan_refused("3 cases begin at 365 U.S. 167")
        self.assertIsNone(self.race.view)        # Scholar may still pick
        self.race._settle("scholar", False)
        self.assertEqual(self.race.view, "refused")
        self.assertIn(("fail", "3 cases begin at 365 U.S. 167"),
                      self.watch.calls)
        self.assertEqual(_Reader.opened, [])

    def test_the_reader_is_asked_which_case_only_before_anything_opens(self):
        self.assertTrue(self.race.may_ask())
        self.race._settle("scholar", True, PAGE)
        self.assertEqual(_Reader.opened, [])     # the reader is choosing
        other = gui._CaseOpenRace(self.app, "parent", cite="365 U.S. 167",
                                  watch=_Watch())
        other._settle("scholar", True, PAGE)
        self.assertFalse(other.may_ask())

    def test_a_case_given_up_on_opens_nothing(self):
        self.watch.cancelled = True
        self.race._settle("scholar", True, PAGE)
        self.assertEqual(self.race.view, "cancelled")
        self.assertEqual(_Reader.opened, [])

    def test_the_text_opens_where_the_load_s_window_stood(self):
        self.race._settle("scholar", True, PAGE)
        self.assertEqual(self.watch.calls[0][0], "to_text")
        self.assertIn(("finish",), self.watch.calls)

    def test_a_pin_cite_is_jumped_to(self):
        self.race.pin = "171"
        self.race._settle("scholar", True, PAGE)
        self.assertIn(("pin", "171"), _Reader.opened[0].handed)


class ScanFirstTests(RaceDecisionTests):
    """A citation followed out of a document: its scan wherever there is
    one, the text sought beside it from the click."""

    def setUp(self):
        super().setUp()
        self.race.prefer_scan = True

    def test_scholar_s_text_first_waits_for_the_scan_lookup(self):
        self.race._settle("scholar", True, PAGE)
        self.assertIsNone(self.race.view)
        self.race._settle("scan", True, SCAN)
        self.assertEqual(self.race.view, "scan")
        self.assertEqual(_Reader.opened, [])

    def test_with_no_scan_the_text_already_in_hand_opens(self):
        self.race._settle("scholar", True, PAGE)
        self.race.scan_missing("none")
        self.assertEqual(self.race.view, "scholar")

    def test_nor_does_a_lesser_text_open_while_the_scan_may_come(self):
        self.race._settle("scholar", False)
        self.race._settle("caselaw", True, _text("case_law"))
        self._after(self.race.GRACE_S + 0.1)
        self.assertIsNone(self.race.view)

    # Inherited checks that assume the text may open before the scan.
    test_nor_while_a_scan_may_still_come = None
    test_scholar_s_text_first_opens_on_the_text = None
    test_the_scan_that_comes_after_goes_behind_the_text = None
    test_no_scan_after_the_text_leaves_it_to_look_for_itself = None
    test_the_text_opens_where_the_load_s_window_stood = None
    test_a_pin_cite_is_jumped_to = None
    test_a_case_given_up_on_opens_nothing = None
    test_the_reader_is_asked_which_case_only_before_anything_opens = None


class ScanUrlTests(unittest.TestCase):
    """The scan a citation names on its face, downloaded ahead."""

    def test_the_library_s_copy_through_501(self):
        urls = gui._scan_urls_for_cite("410 U.S. 113")
        self.assertEqual(len(urls), 1)
        self.assertIn("usrep410113", urls[0])

    def test_gpo_s_after(self):
        urls = gui._scan_urls_for_cite("520 U.S. 1")
        self.assertTrue(urls and all("govinfo.gov" in u for u in urls))

    def test_static_case_law_for_any_other_reporter(self):
        with mock.patch.object(gui, "_case_law_listed_url",
                               side_effect=lambda cite, url: url):
            self.assertEqual(gui._scan_urls_for_cite("253 F.3d 34"),
                             ["https://static.case.law/f3d/253/case-pdfs/"
                              "0034-01.pdf"])


class RaceLaneTests(unittest.TestCase):
    """What the lanes ask for."""

    def _race(self, cite, name="", year="", fetcher=None, **kw):
        race = gui._CaseOpenRace(_App(), "parent", cite=cite, name=name,
                                 year=year, fetcher=fetcher, watch=_Watch(),
                                 **kw)
        race.lesser = []
        race._start_lesser = lambda: race.lesser.append(1)
        return race

    @staticmethod
    def _fetcher(missed=None, found=None):
        calls = []
        fetcher = SimpleNamespace(
            calls=calls,
            missed_before=lambda cites: missed,
            grant_quick_hop=mock.Mock(),
            fetch_by_citation=lambda cite, case_name="", year="": (
                calls.append((cite, case_name, year)) or found),
            fetch_by_url=lambda url: calls.append(url) or found,
            last_fetch_absent=lambda: True,
            take_page_mates=lambda: [],
            take_post_search_failure=lambda: "",
            search_cases=lambda query, limit=3: [],
            pick_cited_result=lambda *a: None,
            note_miss=mock.Mock(), forget_miss=mock.Mock(),
        )
        return fetcher

    def test_one_quick_hop_is_granted_for_the_case(self):
        fetcher = self._fetcher(found=PAGE)
        race = self._race("365 U.S. 167", "Monroe v. Pape", fetcher=fetcher)
        self.assertEqual(race._run_scholar(), (True, PAGE))
        fetcher.grant_quick_hop.assert_called_once_with()

    def test_a_scholar_result_s_own_page_is_fetched_without_a_search(self):
        fetcher = self._fetcher(found=PAGE)
        race = self._race("365 U.S. 167", "Monroe v. Pape", fetcher=fetcher,
                          scholar_url="https://scholar.test/case=1")
        race._run_scholar()
        self.assertEqual(fetcher.calls, ["https://scholar.test/case=1"])

    def test_an_old_case_scholar_missed_before_gets_one_try(self):
        fetcher = self._fetcher(missed="1842")
        race = self._race("45 Mass. 111", "Commonwealth v. Hunt",
                          fetcher=fetcher)
        with mock.patch.object(gui, "_citation_search_variants",
                               return_value=("45 Mass. 111",
                                             "45 Mass 111")):
            race._run_scholar()
        self.assertTrue(race.quick)
        self.assertEqual([c[0] for c in fetcher.calls], ["45 Mass. 111"])
        self.assertEqual(race.lesser, [1])       # sent for at once

    def test_a_newer_case_missed_before_is_asked_as_usual(self):
        fetcher = self._fetcher(missed="1986")
        race = self._race("253 F.3d 34", "United States v. Microsoft Corp.",
                          fetcher=fetcher)
        with mock.patch.object(gui, "_citation_search_variants",
                               return_value=("253 F.3d 34", "253 F. 3d 34")):
            race._settled["scan"].set()          # a scan was found
            race._state["scan"] = "ok"
            race._run_scholar()
        self.assertFalse(race.quick)
        self.assertEqual(len(fetcher.calls), 2)

    def test_scholar_s_answer_that_it_has_none_is_remembered(self):
        fetcher = self._fetcher()
        race = self._race("45 Mass. 111", "Commonwealth v. Hunt",
                          year="1842", fetcher=fetcher)
        race._settled["scan"].set()
        race._state["scan"] = "failed"
        race._lane("scholar", race._run_scholar)
        fetcher.note_miss.assert_called_once()
        self.assertEqual(fetcher.note_miss.call_args.args[1], "1842")

    def test_and_forgotten_when_scholar_has_it_after_all(self):
        fetcher = self._fetcher(missed="1842", found=PAGE)
        race = self._race("45 Mass. 111", "Commonwealth v. Hunt",
                          fetcher=fetcher)
        race._lane("scholar", race._run_scholar)
        fetcher.forget_miss.assert_called_once()
        fetcher.note_miss.assert_not_called()

    def test_a_bare_citation_borrows_courtlistener_s_name(self):
        fetcher = self._fetcher(found=PAGE)
        race = self._race("410 U.S. 113", fetcher=fetcher)
        race.cl_item = lambda: {"caseName": "Roe v. Wade",
                                "dateFiled": "1973-01-22"}
        race._run_scholar()
        self.assertEqual(fetcher.calls[0][1:], ("Roe v. Wade", "1973"))

    def test_the_official_scan_is_looked_for_before_courtlistener(self):
        app = _App()
        seen = []
        app._resolve_pdf_url = lambda client, item: (
            seen.append(dict(item)) or "https://loc.test/x.pdf")
        race = gui._CaseOpenRace(app, "p", cite="1 Cranch 137",
                                 name="Marbury v. Madison", watch=_Watch())
        item, url = race.official_scan()
        self.assertEqual(url, "https://loc.test/x.pdf")
        self.assertTrue(seen[0]["_official_only"])
        self.assertEqual(seen[0]["_us_reports_cite"], "5 U.S. 137")
        self.assertNotIn("_official_only", item)

    def test_but_not_without_the_case_s_name(self):
        app = _App()
        app._resolve_pdf_url = mock.Mock()
        race = gui._CaseOpenRace(app, "p", cite="410 U.S. 113",
                                 watch=_Watch())
        self.assertIsNone(race.official_scan())
        app._resolve_pdf_url.assert_not_called()

    def test_courtlistener_is_asked_once_for_every_lane(self):
        app = _App()
        app._cited_case_pdf_item = mock.Mock(
            return_value={"cluster_id": 7, "citation": ["365 U.S. 167"]})
        race = gui._CaseOpenRace(app, "p", cite="365 U.S. 167",
                                 watch=_Watch())
        results = []
        threads = [threading.Thread(target=lambda: results.append(
            race.cl_item())) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        app._cited_case_pdf_item.assert_called_once()
        self.assertEqual([r["cluster_id"] for r in results], [7] * 4)
        results[0]["citation"].append("mutated")     # each its own copy
        self.assertEqual(race.cl_item()["citation"], ["365 U.S. 167"])

    def test_a_result_s_cluster_is_not_looked_up_again(self):
        app = SimpleNamespace()
        item = {"cluster_id": 9, "caseName": "Roe v. Wade",
                "citation": ["410 U.S. 113"]}
        with mock.patch.object(gui, "_cl_item_for_citation") as lookup:
            got = gui.CourtListenerGUI._cited_case_pdf_item(
                app, "client", "410 U.S. 113", "Roe v. Wade", known=item)
        lookup.assert_not_called()
        self.assertEqual(got["cluster_id"], 9)


class WarmedFromTheRaceTests(unittest.TestCase):
    """The text behind a scan that won comes from the race's lanes."""

    class _Inline:
        def __init__(self, target=None, daemon=False, **_kw):
            self._target = target

        def start(self):
            self._target()

    def _app(self, fetcher):
        return SimpleNamespace(
            _get_scholar=lambda: fetcher,
            _token_var=SimpleNamespace(get=lambda: "tok"),
            _get_client=lambda: "client")

    def test_scholar_s_page_is_the_race_s(self):
        fetcher = mock.Mock()
        race = SimpleNamespace(wait=lambda lane, timeout=None:
                               PAGE if lane == "scholar" else None)
        pages = []
        with mock.patch.object(gui.threading, "Thread", self._Inline):
            gui.CourtListenerGUI._warm_case_text(
                self._app(fetcher), "365 U.S. 167", "Monroe v. Pape",
                on_page=lambda *page: pages.append(page), race=race)
        self.assertEqual(pages, [PAGE])
        fetcher.fetch_by_citation.assert_not_called()

    def test_without_it_the_lanes_texts_follow(self):
        source = _text("courtlistener")
        race = SimpleNamespace(
            wait=lambda lane, timeout=None: source if lane == "cl" else None,
            case_law_for_scan=lambda url: None)
        got = []
        with mock.patch.object(gui.threading, "Thread", self._Inline), \
                mock.patch.object(gui, "_case_law_text_for_scan",
                                  return_value=None), \
                mock.patch.object(gui, "_courtlistener_text_source") as cl:
            gui.CourtListenerGUI._warm_case_text(
                self._app(mock.Mock()), "365 U.S. 167", "Monroe v. Pape",
                on_text_source=got.append, race=race,
                scan_url="https://loc.test/x.pdf")
        self.assertEqual(got, [source])
        cl.assert_not_called()


class DecidedBefore1900Tests(unittest.TestCase):

    def test_by_year(self):
        self.assertTrue(gui._decided_before_1900("1842"))
        self.assertFalse(gui._decided_before_1900("1900"))

    def test_by_the_u_s_reports_volume(self):
        self.assertTrue(gui._decided_before_1900("", ["45 U.S. 1"]))
        self.assertFalse(gui._decided_before_1900("", ["175 U.S. 1"]))
        self.assertFalse(gui._decided_before_1900("", ["253 F.3d 34"]))


# ---------------------------------------------------------------------------
# Network answers shared among a case's lookups
# ---------------------------------------------------------------------------

class SharedFetchTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(gui, "_SCAN_CACHE_DIR",
                                    Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        gui._SHARED_NET.clear()
        self.addCleanup(gui._SHARED_NET.clear)

    def test_one_fetch_answers_everyone_asking_meanwhile(self):
        shared = gui._SharedFetches()
        release = threading.Event()
        calls = []

        def fetch():
            calls.append(1)
            release.wait(5)
            return b"pdf"

        got = []
        threads = [threading.Thread(target=lambda: got.append(
            shared.get("k", fetch))) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(0.1)
        release.set()
        for t in threads:
            t.join(5)
        self.assertEqual(calls, [1])
        self.assertEqual(got, [b"pdf"] * 3)
        self.assertEqual(shared.get("k", lambda: b"other"), b"pdf")  # kept

    def test_nothing_and_failures_are_asked_again(self):
        shared = gui._SharedFetches()
        self.assertIsNone(shared.get("k", lambda: None))
        self.assertEqual(shared.get("k", lambda: b"pdf"), b"pdf")
        with self.assertRaises(OSError):
            shared.get("e", mock.Mock(side_effect=OSError("down")))
        self.assertEqual(shared.get("e", lambda: b"up"), b"up")

    def test_keep_false_shares_only_while_it_is_coming(self):
        shared = gui._SharedFetches()
        self.assertEqual(shared.get("h", lambda: 200, keep=False), 200)
        self.assertEqual(shared.get("h", lambda: 404, keep=False), 404)

    def test_a_download_under_way_answers_the_check_that_it_is_there(self):
        gui._SHARED_NET.clear()
        self.addCleanup(gui._SHARED_NET.clear)
        url = "https://loc.test/usrep410113.pdf"
        self.assertFalse(gui._shared_pdf_answer(url))
        with mock.patch.object(gui, "_fetch_pdf_bytes",
                               return_value=(b"%PDF", url)) as fetch:
            gui._shared_pdf_bytes(url, probe=True)
            self.assertEqual(gui._shared_pdf_bytes(url), (b"%PDF", url))
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs["max_hops"], 1)
        self.assertTrue(gui._shared_pdf_answer(url))


class ScanCacheTests(SharedFetchTests):
    """The official reports' scans and static.case.law's, kept on disk."""

    LOC = ("https://tile.loc.gov/storage-services/service/ll/usrep/"
           "usrep410/usrep410113/usrep410113.pdf")

    def _fetch(self, url=LOC):
        return mock.patch.object(gui, "_fetch_pdf_bytes",
                                 return_value=(b"%PDF-1.4 roe", url + "#x"))

    def test_a_scan_fetched_once_is_read_from_disk_after(self):
        with self._fetch() as fetch:
            first = gui._shared_pdf_bytes(self.LOC)
        gui._SHARED_NET.clear()            # a later run of the app
        with self._fetch() as fetch_again:
            again = gui._shared_pdf_bytes(self.LOC)
        fetch.assert_called_once()
        fetch_again.assert_not_called()
        self.assertEqual(again, first)
        self.assertTrue(gui._shared_pdf_answer(self.LOC))

    def test_only_the_reports_and_static_case_law_are_kept(self):
        other = "https://www.supremecourt.gov/opinions/24pdf/1_abc.pdf"
        with self._fetch(other):
            gui._shared_pdf_bytes(other)
        self.assertEqual(list(Path(self._tmp.name).glob("*.pdf")), [])

    def test_what_is_not_a_pdf_is_not_kept(self):
        with mock.patch.object(gui, "_fetch_pdf_bytes",
                               return_value=(b"<html>", self.LOC)):
            gui._shared_pdf_bytes(self.LOC)
        self.assertEqual(list(Path(self._tmp.name).glob("*.pdf")), [])

    def test_the_oldest_go_past_the_limit(self):
        urls = [f"https://static.case.law/f3d/{v}/case-pdfs/0001-01.pdf"
                for v in (1, 2, 3)]
        with mock.patch.object(gui, "_SCAN_CACHE_MAX_BYTES", 40):
            for i, url in enumerate(urls):
                with mock.patch.object(gui, "_fetch_pdf_bytes",
                                       return_value=(b"%PDF-1.4" + b"x" * 12,
                                                     url)):
                    gui._shared_pdf_bytes(url)
                time.sleep(0.02)           # distinct ages
        kept = [u for u in urls if gui._scan_cache_read(u) is not None]
        self.assertEqual(kept, urls[1:])

    def test_the_library_s_preferred_copy_is_checked_by_downloading_it(self):
        app = object.__new__(gui.CourtListenerGUI)
        session = mock.Mock(headers={})
        with self._fetch() as fetch, \
                mock.patch.object(gui, "_anon_session", session), \
                mock.patch.object(gui, "_us_reports_loc_url",
                                  return_value=self.LOC), \
                mock.patch.object(gui, "_us_reports_govinfo_url",
                                  return_value=None), \
                mock.patch.object(gui, "_us_reports_page_opinions",
                                  return_value=[]):
            url = gui.CourtListenerGUI._resolve_pdf_url(
                app, None, {"citation": ["410 U.S. 113"],
                            "_us_reports_cite": "410 U.S. 113",
                            "_official_only": True})
        self.assertEqual(url, self.LOC)
        session.head.assert_not_called()   # no HEAD for the Library's copy
        self.assertEqual(fetch.call_args.kwargs["max_hops"], 1)


class OfficialOnlyTests(unittest.TestCase):
    """The official scan alone, before CourtListener is asked anything."""

    class Session:
        headers: dict = {}

        def __init__(self, ok):
            self.ok = ok

        def head(self, url, **_kw):
            return SimpleNamespace(
                status_code=200 if url in self.ok else 404,
                headers={"Content-Type": "application/pdf"})

    def _resolve(self, item, ok):
        app = object.__new__(gui.CourtListenerGUI)
        loc = "https://loc.test/{}.pdf"
        with mock.patch.object(gui, "_anon_session", self.Session(ok)), \
                mock.patch.object(gui, "_us_reports_loc_url",
                                  side_effect=lambda c: loc.format(
                                      c.replace(" ", ""))), \
                mock.patch.object(gui, "_us_reports_govinfo_url",
                                  side_effect=lambda c: (
                                      "https://gov.test/link/" + c,
                                      "https://gov.test/file/" + c)), \
                mock.patch.object(gui, "_us_reports_page_opinions",
                                  return_value=[]), \
                mock.patch.object(gui.us_reports_pdf, "extract_citation",
                                  return_value=None), \
                mock.patch.object(gui, "_gather_all_citations") as gather:
            url = gui.CourtListenerGUI._resolve_pdf_url(app, None, item)
        return url, gather

    def test_found_it_needs_no_courtlistener(self):
        url, gather = self._resolve(
            {"citation": ["365 U.S. 167"], "_us_reports_cite": "365 U.S. 167",
             "_official_only": True},
            ok={"https://loc.test/365U.S.167.pdf"})
        self.assertEqual(url, "https://loc.test/365U.S.167.pdf")
        gather.assert_not_called()

    def test_not_found_it_stops_there(self):
        url, gather = self._resolve(
            {"citation": ["365 U.S. 167"], "_us_reports_cite": "365 U.S. 167",
             "_official_only": True}, ok=set())
        self.assertIsNone(url)
        gather.assert_not_called()

    def test_both_asked_the_library_s_scan_leads_through_501(self):
        url, _ = self._resolve(
            {"citation": ["410 U.S. 113"], "_us_reports_cite": "410 U.S. 113",
             "_official_only": True},
            ok={"https://loc.test/410U.S.113.pdf",
                "https://gov.test/link/410 U.S. 113"})
        self.assertEqual(url, "https://loc.test/410U.S.113.pdf")

    def test_and_gpo_s_after_it(self):
        url, _ = self._resolve(
            {"citation": ["520 U.S. 1"], "_us_reports_cite": "520 U.S. 1",
             "_official_only": True},
            ok={"https://loc.test/520U.S.1.pdf",
                "https://gov.test/link/520 U.S. 1"})
        self.assertEqual(url, "https://gov.test/link/520 U.S. 1")


# ---------------------------------------------------------------------------
# Google Scholar
# ---------------------------------------------------------------------------

class _FetcherFixture(unittest.TestCase):
    DELAY = 3.0

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fetcher = GoogleScholarFetcher(
            cache_path=Path(self._tmp.name) / "cache.db", delay=self.DELAY)
        self.fetcher._browser_dead = True
        self.fetcher._warmed = True
        self.fetcher._browser_first = False
        self.fetcher._blocked_until = 0.0
        self.fetcher._quick_hop_off_wall = 0.0
        self.fetcher._save_state = mock.Mock()   # leave the real state be

    def tearDown(self):
        self.fetcher._db.close()
        self._tmp.cleanup()


class QuickHopTests(_FetcherFixture):
    """A second, not three or more, from a search to the page it found."""

    def _throttle(self, page: bool) -> tuple:
        sleeps = []
        self.fetcher._hop.page = page
        with mock.patch.object(gs.time, "sleep", sleeps.append):
            quick = self.fetcher._throttle()
        return quick, sum(sleeps)

    def _just_answered(self):
        now = time.monotonic()
        self.fetcher._last_request = now - 0.4
        self.fetcher._last_response = now

    def test_a_granted_opinion_page_follows_in_a_second(self):
        self._just_answered()
        self.fetcher.grant_quick_hop()
        quick, slept = self._throttle(page=True)
        self.assertTrue(quick)
        self.assertGreater(slept, 0.8)
        self.assertLess(slept, 1.3)

    def test_once(self):
        self._just_answered()
        self.fetcher.grant_quick_hop()
        self._throttle(page=True)
        quick, slept = self._throttle(page=True)
        self.assertFalse(quick)
        self.assertGreaterEqual(slept, self.DELAY - 0.1)

    def test_a_search_does_not_spend_it(self):
        self._just_answered()
        self.fetcher.grant_quick_hop()
        quick, slept = self._throttle(page=False)
        self.assertFalse(quick)
        self.assertGreaterEqual(slept, self.DELAY - 0.5)
        self.assertTrue(self._throttle(page=True)[0])

    def test_without_a_grant_the_usual_pacing(self):
        self._just_answered()
        quick, slept = self._throttle(page=True)
        self.assertFalse(quick)
        self.assertGreaterEqual(slept, self.DELAY - 0.5)

    def test_a_challenge_after_one_turns_them_off_for_a_day(self):
        self._just_answered()
        session = mock.Mock()
        session.get.return_value = SimpleNamespace(
            status_code=429, url="https://scholar.google.com/sorry/", text="")
        self.fetcher._session = session
        self.fetcher._rotate_persona = mock.Mock()
        self.fetcher.grant_quick_hop()
        self.fetcher._hop.page = True
        with mock.patch.object(gs.time, "sleep"):
            with self.assertRaises(gs.ScholarError):
                self.fetcher._get("https://scholar.google.com/scholar_case?"
                                  "case=1", once=True)
        self.assertGreater(self.fetcher._quick_hop_off_wall,
                           time.time() + 23 * 3600)
        self.fetcher._save_state.assert_called()
        self.fetcher.grant_quick_hop()
        self.assertFalse(self._throttle(page=True)[0])

    def test_requests_from_two_threads_are_spaced_from_each_other(self):
        self.fetcher._last_request = time.monotonic()
        slots = []
        real_sleep = time.sleep
        with mock.patch.object(gs.time, "sleep", lambda s: None):
            for _ in range(2):
                self.fetcher._throttle()
                slots.append(self.fetcher._last_request)
        real_sleep(0)
        self.assertGreaterEqual(slots[1] - slots[0], self.DELAY - 0.01)


class WarmTests(_FetcherFixture):
    """Spotlight opened: the session's homepage visit, made now."""

    def setUp(self):
        super().setUp()
        self.fetcher._warmed = False
        self.fetcher._warm_up = mock.Mock()

    def test_made_once_spotlight_opens(self):
        self.fetcher.warm()
        self.fetcher._warm_up.assert_called_once()

    def test_not_while_google_is_refusing_the_session(self):
        self.fetcher._browser_first = True
        self.fetcher.warm()
        self.fetcher._browser_first = False
        self.fetcher._blocked_until = time.monotonic() + 60
        self.fetcher.warm()
        self.fetcher._warm_up.assert_not_called()

    def test_nor_twice(self):
        self.fetcher._warmed = True
        self.fetcher.warm()
        self.fetcher._warm_up.assert_not_called()


class MissMemoryTests(_FetcherFixture):
    DELAY = 0.0

    def test_a_miss_is_kept_with_its_year(self):
        self.assertIsNone(self.fetcher.missed_before(["45 Mass. 111"]))
        self.fetcher.note_miss(["45 Mass. 111", "45 Mass 111"], "1842")
        self.assertEqual(self.fetcher.missed_before(["45  Mass. 111"]),
                         "1842")
        self.assertEqual(self.fetcher.missed_before(["45 Mass 111"]), "1842")

    def test_with_no_year_known(self):
        self.fetcher.note_miss(["2 Va. 319"])
        self.assertEqual(self.fetcher.missed_before(["2 Va. 319"]), "")

    def test_forgotten_when_found(self):
        self.fetcher.note_miss(["45 Mass. 111"], "1842")
        self.fetcher.forget_miss(["45 Mass. 111"])
        self.assertIsNone(self.fetcher.missed_before(["45 Mass. 111"]))

    def test_kept_across_runs(self):
        self.fetcher.note_miss(["45 Mass. 111"], "1842")
        self.fetcher._db.close()
        self.fetcher = GoogleScholarFetcher(
            cache_path=Path(self._tmp.name) / "cache.db", delay=0.0)
        self.fetcher._save_state = mock.Mock()
        self.assertEqual(self.fetcher.missed_before(["45 Mass. 111"]), "1842")


class ListedAlreadyTests(_FetcherFixture):
    """A case Spotlight's search already listed opens with no second
    search."""

    DELAY = 0.0
    URL = "https://scholar.google.com/scholar_case?case=9116244287806866358"
    MONROE = ScholarResult("Monroe v. Pape", URL,
                           "365 US 167 - Supreme Court, 1961")

    def setUp(self):
        super().setUp()
        self.fetcher._search_cache[("Monroe v. Pape", ())] = [self.MONROE]
        self.fetcher._get = mock.Mock(side_effect=RuntimeError("searched"))
        self.fetcher._fetch_case_page = mock.Mock(
            return_value=(self.URL, "<div>Monroe</div>"))

    def test_its_page_is_fetched_straight_off_the_list(self):
        got = self.fetcher.fetch_by_citation("365 U.S. 167",
                                             case_name="Monroe v. Pape")
        self.assertEqual(got, (self.URL, "<div>Monroe</div>"))
        self.fetcher._get.assert_not_called()
        self.fetcher._fetch_case_page.assert_called_once_with(self.URL)

    def test_not_for_a_bare_citation(self):
        self.assertIsNone(self.fetcher.fetch_by_citation("365 U.S. 167"))
        self.fetcher._get.assert_called_once()     # searched after all
        self.fetcher._fetch_case_page.assert_not_called()

    def test_nor_for_another_case_at_the_page(self):
        self.assertIsNone(self.fetcher.fetch_by_citation(
            "365 U.S. 167", case_name="Smith v. Jones"))
        self.fetcher._get.assert_called_once()
        self.fetcher._fetch_case_page.assert_not_called()


# ---------------------------------------------------------------------------
# A scan's pages: the first measured before it is shown, the rest after
# ---------------------------------------------------------------------------

def _pdf(*rects: tuple, size=(612, 792)) -> bytes:
    """A PDF with one page per ``(x, y, w, h)``: a black box of ink there."""
    n = len(rects)
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[%s]/Count %d>>" % (
            b" ".join(b"%d 0 R" % (3 + 2 * i) for i in range(n)), n),
    ]
    for i, (x, y, w, h) in enumerate(rects):
        stream = b"0 g %d %d %d %d re f" % (x, y, w, h)
        objects.append(b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 %d %d]"
                       b"/Contents %d 0 R>>" % (size[0], size[1], 4 + 2 * i))
        objects.append(b"<</Length %d>>stream\n" % len(stream) + stream
                       + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj" % i + body + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref))
    return bytes(out)


TEXT_BLOCK = (100, 150, 400, 500)        # where an opinion's text sits
WIDER = (60, 150, 480, 500)              # a page whose text runs wider


class PartialMeasureTests(unittest.TestCase):

    def test_only_the_first_pages_are_read_before_the_scan_shows(self):
        data = _pdf(*[TEXT_BLOCK] * 5)
        meta = gui._PdfPane.measure_pdf(data, first=2)
        self.assertIsInstance(meta, gui._ProvisionalMeta)
        self.assertEqual(meta.measured, 2)
        self.assertEqual(len(meta), 5)
        # The rest are given the box the first pages share.
        self.assertEqual({m[2] for m in meta}, {meta[0][2]})
        self.assertNotEqual(meta[0][2], (0.0, 0.0, 1.0, 1.0))

    def test_a_short_scan_is_read_whole(self):
        meta = gui._PdfPane.measure_pdf(_pdf(TEXT_BLOCK, TEXT_BLOCK), first=6)
        self.assertNotIsInstance(meta, gui._ProvisionalMeta)

    def test_without_first_every_page_is_read(self):
        meta = gui._PdfPane.measure_pdf(_pdf(TEXT_BLOCK, WIDER))
        self.assertNotIsInstance(meta, gui._ProvisionalMeta)
        self.assertNotEqual(meta[0][2], meta[1][2])

    def test_pages_of_different_sizes_are_guessed_whole(self):
        full = (0.0, 0.0, 1.0, 1.0)
        guessed = gui._PdfPane._provisional(
            [(612, 792, (0.1, 0.1, 0.9, 0.9)), (300, 400, None)])
        self.assertEqual(guessed[1][2], full)

    def _pane(self, meta, raw=None):
        pane = SimpleNamespace(
            _disposed=False, _meta=list(meta),
            _raw_meta=list(raw if raw is not None else meta),
            _layout=mock.Mock(), _render_visible=mock.Mock(),
            _view_anchor=mock.Mock(return_value=(3, 0.25)),
            _restore_anchor=mock.Mock())
        pane._apply_uniform_crop = (
            lambda: gui._PdfPane._apply_uniform_crop(pane))
        return pane

    def test_the_rest_read_as_guessed_leaves_the_pages_be(self):
        box = (0.1, 0.1, 0.9, 0.9)
        pane = self._pane([(612, 792, box)] * 4)
        gui._PdfPane._take_measured(pane, 2, [(612, 792, box)] * 2, True)
        pane._layout.assert_not_called()

    def test_the_rest_read_otherwise_lays_them_out_again_in_place(self):
        box, wider = (0.1, 0.1, 0.9, 0.9), (0.05, 0.1, 0.95, 0.9)
        pane = self._pane([(612, 792, box)] * 4)
        gui._PdfPane._take_measured(
            pane, 2, [(612, 792, box), (612, 792, wider)], True)
        pane._layout.assert_called_once()
        pane._restore_anchor.assert_called_once_with((3, 0.25))
        # The shared crop now takes in the wider page.
        self.assertEqual({m[2] for m in pane._meta}, {(0.05, 0.1, 0.95, 0.9)})

    def test_a_closed_pane_is_left_alone(self):
        pane = self._pane([(612, 792, (0.1, 0.1, 0.9, 0.9))] * 4)
        pane._disposed = True
        gui._PdfPane._take_measured(pane, 2, [(1, 1, (0, 0, 1, 1))] * 2, True)
        pane._layout.assert_not_called()


# ---------------------------------------------------------------------------
# A window going up comes before the background reading of PDFs
# ---------------------------------------------------------------------------

class UiFirstTests(unittest.TestCase):

    def tearDown(self):
        gui._UI_FIRST["until"] = 0.0

    def _waited(self) -> float:
        took = []

        def work():
            start = time.monotonic()
            gui._yield_to_ui()
            took.append(time.monotonic() - start)

        t = threading.Thread(target=work)
        t.start()
        t.join(10)
        return took[0]

    def test_a_reader_waits_while_a_window_goes_up(self):
        gui._ui_first(0.3)
        self.assertGreaterEqual(self._waited(), 0.25)

    def test_and_goes_on_once_it_is_up(self):
        gui._ui_first(5.0)
        gui._ui_first(0.0)          # built: the gate opens
        self.assertLess(self._waited(), 0.1)

    def test_never_for_long(self):
        gui._ui_first(60.0)
        with mock.patch.object(gui, "_UI_FIRST_MAX_S", 0.2):
            self.assertLess(self._waited(), 1.0)

    def test_the_tk_thread_never_waits_on_itself(self):
        gui._ui_first(60.0)
        start = time.monotonic()
        gui._yield_to_ui()
        self.assertLess(time.monotonic() - start, 0.05)

    def test_text_extraction_gives_way_page_by_page(self):
        calls = []
        gui._extract_pdf_text_and_style(
            _pdf(TEXT_BLOCK, TEXT_BLOCK, TEXT_BLOCK),
            between_pages=lambda: calls.append(1))
        self.assertEqual(len(calls), 3)


# ---------------------------------------------------------------------------
# An opinion's citations, read once for every window that shows it
# ---------------------------------------------------------------------------

class LinkScanCacheTests(unittest.TestCase):

    FOUND = [(0, 4, ("cite", "410 U.S. 113"))]

    def setUp(self):
        gui._TEXT_LINK_SCANS.clear()
        self.addCleanup(gui._TEXT_LINK_SCANS.clear)

    def test_the_same_text_is_scanned_once(self):
        with mock.patch.object(gui, "detect_brief_links",
                               return_value=list(self.FOUND)) as detect:
            first = gui._detected_text_links("Roe.", [False] * 4)
            first.append("a caller's own change")
            again = gui._detected_text_links("Roe.", [False] * 4)
        detect.assert_called_once()
        self.assertEqual(again, self.FOUND)

    def test_other_text_or_other_italics_is_scanned_afresh(self):
        with mock.patch.object(gui, "detect_brief_links",
                               return_value=[]) as detect:
            gui._detected_text_links("Roe.", [False] * 4)
            gui._detected_text_links("Roe.", [True] * 4)
            gui._detected_text_links("Doe.", [False] * 4)
        self.assertEqual(detect.call_count, 3)

    def test_the_text_behind_a_scan_is_scanned_as_it_comes(self):
        html = ('<div id="gs_opinion"><p>See <i>Roe</i> v. <i>Wade</i>, '
                '410 U.S. 113 (1973).</p></div>')
        with mock.patch.object(gui, "detect_brief_links",
                               wraps=gui.detect_brief_links) as detect:
            gui._scan_opinion_links_ahead(html)
            parts = gui.segment_blocks(gui.parse_opinion_blocks(html))
            gui._text_opinion_link_ranges(parts)   # the window, later
        detect.assert_called_once()


class LinkInPlaceTests(unittest.TestCase):
    """A long opinion shown before its citations are read: they are linked
    where they lie afterwards, past the justification's padding, with
    nothing drawn again (see _ScholarTextWindow._link_block_in_place)."""

    PAD = "justify-pad"
    HIDE = "justify-hide"

    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.txt = tk.Text(self.root)
        self.txt.tag_configure(self.HIDE, elide=True)
        win = SimpleNamespace(_JUSTIFY_PAD_TAG=self.PAD, _link_actions={},
                              _link_n=0)

        def new_link(action):
            win._link_n += 1
            name = f"lnk{win._link_n}"
            win._link_actions[name] = action
            return name

        win._new_link = new_link
        win._scholar_link_action = lambda text, href: ("url", href)
        win._justify_padding = (
            lambda txt, mark, upto: gui._ScholarTextWindow._justify_padding(
                win, txt, mark, upto))
        self.win = win

    def _block(self, *spans):
        from google_scholar import Span
        block = SimpleNamespace(spans=[Span(**s) for s in spans])
        self.txt.insert("end", "Before.\n")
        self.txt.mark_set("blk", "end-1c")
        self.txt.mark_gravity("blk", "left")
        for span in block.spans:
            self.txt.insert("end", span.text)
        return block

    def _link(self, block, ranges):
        gui._ScholarTextWindow._link_block_in_place(
            self.win, self.txt, "blk", block, ranges)

    def _linked(self, name):
        """The opinion's own text under *name*, padding left out."""
        out, ranges = [], self.txt.tag_ranges(name)
        for a, b in zip(ranges[::2], ranges[1::2]):
            i = a
            while self.txt.compare(i, "<", b):
                if self.PAD not in self.txt.tag_names(i):
                    out.append(self.txt.get(i))
                i = self.txt.index(f"{i} +1c")
        return "".join(out)

    def test_a_citation_is_linked_on_its_own_characters(self):
        block = self._block({"text": "See Roe v. Wade, 410 U.S. 113 (1973)."})
        self._link(block, [(4, 29, ("cite", "410 U.S. 113"))])
        name, = self.win._link_actions
        self.assertEqual(self._linked(name), "Roe v. Wade, 410 U.S. 113")

    def test_past_the_justification_s_padding(self):
        text = "See Roe v. Wade, 410 U.S. 113 (1973)."
        block = self._block({"text": text})
        # Two runs of padding before the citation, one inside it.
        self.txt.insert("blk + 4 chars", "  ", (self.PAD,))
        self.txt.insert("blk + 2 chars", " ", (self.PAD,))
        self._link(block, [(4, 29, ("cite", "410 U.S. 113"))])
        name, = self.win._link_actions
        self.assertEqual(self._linked(name), "Roe v. Wade, 410 U.S. 113")
        # The padding spaces themselves are not underlined as the link.
        for a, b in zip(*[iter(self.txt.tag_ranges(self.PAD))] * 2):
            self.assertNotIn(name, self.txt.tag_names(a))

    def test_a_hyphenated_word_s_visible_half_is_linked_too(self):
        block = self._block({"text": "See Wakely v. Hart, 6 Binn. 316."})
        # "Wak-" set at the end of a line, the original "Wak" hidden.
        self.txt.insert("blk + 4 chars", "Wak-\n", (self.PAD,))
        self.txt.tag_add(self.HIDE, "blk + 9 chars", "blk + 12 chars")
        self._link(block, [(4, 31, ("cite", "6 Binn. 316"))])
        name, = self.win._link_actions
        self.assertEqual(self._linked(name), "Wakely v. Hart, 6 Binn. 316")
        self.assertIn(name, self.txt.tag_names("blk + 4 chars"))

    def test_a_scholar_link_the_citation_overlaps_goes_with_it(self):
        block = self._block(
            {"text": "See "},
            {"text": "Roe v. Wade, 410 U.S. 113", "link": "https://s/roe"},
            {"text": " (1973)."})
        self._link(block, [(17, 29, ("cite", "410 U.S. 113"))])
        actions = set(self.win._link_actions.values())
        self.assertEqual(actions, {("cite", "410 U.S. 113")})
        name, = self.win._link_actions
        self.assertEqual(self._linked(name), "Roe v. Wade, 410 U.S. 113")

    def test_one_no_citation_touches_is_a_link_of_its_own(self):
        block = self._block(
            {"text": "As in "},
            {"text": "Doe v. Bolton", "link": "https://s/doe"},
            {"text": ", supra."})
        self._link(block, [])
        name, = self.win._link_actions
        self.assertEqual(self.win._link_actions[name], ("url", "https://s/doe"))
        self.assertEqual(self._linked(name), "Doe v. Bolton")


# ---------------------------------------------------------------------------
# static.case.law's scan of the cited reporter, before CourtListener
# ---------------------------------------------------------------------------

class CaseLawScanTests(unittest.TestCase):
    URL = "https://static.case.law/ny/248/case-pdfs/0339-01.pdf"

    def _race(self, cite="248 N.Y. 339", name="Palsgraf v. Long Island R.R."):
        return gui._CaseOpenRace(_App(), "p", cite=cite, name=name,
                                 watch=_Watch())

    def _choices(self, *choices):
        return mock.patch.object(gui, "_case_law_pdf_choices_for_cites",
                                 return_value=list(choices))

    def test_the_cited_reporter_s_scan_needs_no_courtlistener(self):
        choice = SimpleNamespace(cite="248 N.Y. 339", url=self.URL,
                                 pick=False)
        with self._choices(choice) as asked:
            item, url = self._race().case_law_scan()
        self.assertEqual(url, self.URL)
        self.assertEqual(asked.call_args.kwargs["expected_name"],
                         "Palsgraf v. Long Island R.R.")
        self.assertTrue(item["_other_reporters_unasked"])

    def test_not_without_the_case_s_name(self):
        with self._choices() as asked:
            self.assertIsNone(self._race(name="").case_law_scan())
        asked.assert_not_called()

    def test_not_for_the_supreme_court_s_reporters(self):
        with self._choices() as asked:
            self.assertIsNone(self._race("125 S. Ct. 1183",
                                         "Roper v. Simmons").case_law_scan())
            self.assertIsNone(self._race("1 Cranch 137",
                                         "Marbury v. Madison").case_law_scan())
        asked.assert_not_called()

    def test_several_cases_on_the_page_go_the_long_way(self):
        choice = SimpleNamespace(cite="248 N.Y. 339", url=self.URL, pick=True)
        with self._choices(choice):
            self.assertIsNone(self._race().case_law_scan())


if __name__ == "__main__":
    unittest.main()
