"""Reading the CloudFlare clearance out of Firefox's cookie store, and
sending it in a way CloudFlare will honour.

The English Reports scans on CommonLII sit behind CloudFlare.  The reader
clears the check once in Firefox and the app replays the ``cf_clearance``
cookie it left in ``cookies.sqlite``.  What CloudFlare will honour is
narrower than what the store says is live, and a clearance it has stopped
honouring looks exactly like a good one until it is refused — at which point
the app asks the reader to clear the check again, which they have already
done.  So four things have to be right:

* ``moz_cookies.expiry`` is written in **milliseconds** now.  It used to be
  seconds, and ``creationTime``/``lastAccessed``/``updateTime`` still are
  microseconds, so a reader that assumes the old unit either believes every
  cookie is dead or believes every cookie is live.
* Under Total Cookie Protection a cookie is stored per partition, so one host
  and one name can mean several rows — some of them long expired — and a
  server re-set keeps a row's original ``creationTime``, so the clearance
  obtained this afternoon can carry a creation date from months ago.
* A clearance is bound to the **User-Agent of the Firefox that obtained it**,
  version and all.  A machine can run two Firefox builds at two versions (a
  Microsoft Store build beside a regular one), each with its own profile and
  its own clearance, and either can update underneath a running app — so the
  UA has to come from the profile the cookie came from, read fresh each time.
* CloudFlare drops a clearance long before its stated expiry, so the expiry
  is only a floor: every profile's clearance is a candidate, and only the
  fetch can tell which one is good.

These tests build cookie stores and profiles with those shapes and check the
reader takes the right rows out of them, pairs each with its own Firefox's
UA, and keeps trying until CloudFlare accepts one.
"""

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import eng_rep_pdf


SECOND = 1
DAY = 86400

_SCHEMA_COLUMNS = (
    "originAttributes TEXT, name TEXT, value TEXT, host TEXT, path TEXT, "
    "expiry INTEGER, lastAccessed INTEGER, creationTime INTEGER, "
    "isSecure INTEGER, isHttpOnly INTEGER, inBrowserElement INTEGER, "
    "sameSite INTEGER, schemeMap INTEGER, isPartitionedAttributeSet INTEGER"
)


def _make_db(path: Path, rows, *, update_time: bool = True) -> Path:
    """A cookies.sqlite holding *rows* — Firefox's real schema, near enough.

    Each row is ``(name, value, host, expiry, touched, originAttributes)`` or,
    with a seventh element, ``(..., originAttributes, created)``: *touched*
    stamps ``lastAccessed``/``updateTime``, *created* (default *touched*)
    ``creationTime``, and *expiry* is in whatever unit the test means to
    exercise.  ``update_time=False`` builds the older schema without an
    ``updateTime`` column.
    """
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, " + _SCHEMA_COLUMNS
        + (", updateTime INTEGER" if update_time else "") + ")"
    )
    for row in rows:
        name, value, host, expiry, touched, origin = row[:6]
        created = row[6] if len(row) > 6 else touched
        cols = ("originAttributes, name, value, host, path, expiry, "
                "lastAccessed, creationTime, isSecure, isHttpOnly, "
                "inBrowserElement, sameSite, schemeMap, "
                "isPartitionedAttributeSet")
        vals = [origin, name, value, host, "/", expiry, touched, created,
                1, 1, 0, 0, 2, 1 if origin else 0]
        if update_time:
            cols += ", updateTime"
            vals.append(touched)
        con.execute(
            f"INSERT INTO moz_cookies ({cols}) VALUES "
            f"({','.join('?' * len(vals))})", vals)
    con.commit()
    con.close()
    return path


def _write_compat(profile_dir: Path, last_version: str) -> None:
    """The ``compatibility.ini`` Firefox leaves beside a profile's stores,
    in its real shape (``version_buildid/buildid``)."""
    (profile_dir / "compatibility.ini").write_text(
        "[Compatibility]\n"
        f"LastVersion={last_version}\n"
        "LastOSABI=WINNT_x86_64-msvc\n"
        "LastPlatformDir=C:\\Program Files\\Mozilla Firefox\n"
        "LastAppDir=C:\\Program Files\\Mozilla Firefox\\browser\n",
        encoding="utf-8")


class ExpiryUnitTests(unittest.TestCase):
    """Seconds then, milliseconds now — and the magnitudes never overlap."""

    def test_milliseconds_are_read_as_milliseconds(self):
        now = time.time()
        self.assertAlmostEqual(
            eng_rep_pdf._cookie_expiry_seconds(int(now * 1000)), now, places=0)

    def test_seconds_are_still_read_as_seconds(self):
        now = int(time.time())
        self.assertEqual(eng_rep_pdf._cookie_expiry_seconds(now), float(now))

    def test_a_session_cookie_has_no_expiry(self):
        for empty in (0, None, "", "nonsense"):
            self.assertEqual(eng_rep_pdf._cookie_expiry_seconds(empty), 0.0)

    def test_the_two_units_cannot_be_confused(self):
        # A date far enough ahead to matter, in each unit: as seconds it is
        # this century, as milliseconds it is still this century, and neither
        # reading strays into the other's range.
        far = time.time() + 50 * 365 * DAY
        self.assertLess(eng_rep_pdf._cookie_expiry_seconds(int(far)),
                        eng_rep_pdf._MS_EXPIRY_FLOOR)
        self.assertAlmostEqual(
            eng_rep_pdf._cookie_expiry_seconds(int(far * 1000)), far, places=0)


class CookieRowTests(unittest.TestCase):
    """One store, read into {name: (value, expiry, touched)}."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ffcookie_test_"))
        self.now = time.time()

    def _rows(self, rows, domain="commonlii", **kw):
        db = _make_db(self.tmp / "cookies.sqlite", rows, **kw)
        return eng_rep_pdf._read_cookie_rows(db, domain)

    def _ms(self, offset):
        return int((self.now + offset) * 1000)

    def _us(self, offset):
        return int((self.now + offset) * 1_000_000)

    def test_a_live_clearance_is_read(self):
        rows = self._rows([
            ("cf_clearance", "live", ".commonlii.org", self._ms(365 * DAY),
             self._us(0), "^partitionKey=%28https%2Ccommonlii.org%29"),
        ])
        self.assertEqual(rows["cf_clearance"][0], "live")

    def test_when_the_cookie_was_touched_comes_back_in_seconds(self):
        rows = self._rows([
            ("cf_clearance", "live", ".commonlii.org", self._ms(365 * DAY),
             self._us(-90), ""),
        ])
        self.assertAlmostEqual(rows["cf_clearance"][2], self.now - 90,
                               places=0)

    def test_an_expired_cookie_is_dropped(self):
        rows = self._rows([
            ("cf_clearance", "dead", ".commonlii.org", self._ms(-DAY),
             self._us(-DAY), ""),
        ])
        self.assertEqual(rows, {})

    def test_an_expired_cookie_in_the_old_unit_is_dropped_too(self):
        rows = self._rows([
            ("cf_clearance", "dead", ".commonlii.org",
             int(self.now - DAY), self._us(-DAY), ""),
        ])
        self.assertEqual(rows, {})

    def test_a_dead_partitioned_copy_never_beats_the_live_one(self):
        # Total Cookie Protection leaves a copy per partition.  The dead one is
        # inserted last here, so a reader that just keeps the last row it sees
        # would send it — and be re-challenged.
        rows = self._rows([
            ("__cf_bm", "live", ".commonlii.org", self._ms(30 * 60),
             self._us(0), ""),
            ("__cf_bm", "dead", ".commonlii.org", self._ms(-30 * DAY),
             self._us(-30 * DAY), "^partitionKey=%28resource%2Cpdf.js%29"),
        ])
        self.assertEqual(rows["__cf_bm"][0], "live")

    def test_the_most_recently_set_copy_wins_among_live_ones(self):
        rows = self._rows([
            ("cf_clearance", "fresh", ".commonlii.org", self._ms(DAY),
             self._us(0), ""),
            ("cf_clearance", "stale", ".commonlii.org", self._ms(365 * DAY),
             self._us(-3600), ""),
        ])
        # The one Firefox set (or sent) last is the one it considers current,
        # however long the other claims it will live.
        self.assertEqual(rows["cf_clearance"][0], "fresh")

    def test_a_reset_clearance_keeps_its_old_creation_date_and_still_wins(self):
        # Firefox keeps a cookie's original creationTime when the server
        # re-sets it; updateTime is what dates the clearance obtained today.
        rows = self._rows([
            ("cf_clearance", "reissued", ".commonlii.org", self._ms(365 * DAY),
             self._us(0), "^partitionKey=%28https%2Ccommonlii.org%29",
             self._us(-90 * DAY)),
            ("cf_clearance", "lingering", ".commonlii.org", self._ms(300 * DAY),
             self._us(-60 * DAY), "", self._us(-60 * DAY)),
        ])
        self.assertEqual(rows["cf_clearance"][0], "reissued")
        self.assertAlmostEqual(rows["cf_clearance"][2], self.now, places=0)

    def test_the_furthest_expiry_breaks_a_tie(self):
        rows = self._rows([
            ("cf_clearance", "shorter", ".commonlii.org", self._ms(DAY),
             self._us(0), ""),
            ("cf_clearance", "longer", ".commonlii.org", self._ms(365 * DAY),
             self._us(0), ""),
        ])
        self.assertEqual(rows["cf_clearance"][0], "longer")

    def test_an_older_schema_without_update_time_still_reads(self):
        rows = self._rows([
            ("cf_clearance", "live", ".commonlii.org", self._ms(DAY),
             self._us(-30), ""),
        ], update_time=False)
        self.assertEqual(rows["cf_clearance"][0], "live")
        self.assertAlmostEqual(rows["cf_clearance"][2], self.now - 30,
                               places=0)

    def test_a_session_cookie_survives_the_expiry_filter(self):
        rows = self._rows([
            ("__cflb", "sess", "www.commonlii.org", 0, self._us(0), ""),
        ])
        self.assertEqual(rows["__cflb"][0], "sess")

    def test_only_the_asked_for_host_comes_back(self):
        rows = self._rows([
            ("cf_clearance", "ours", ".commonlii.org", self._ms(DAY),
             self._us(0), ""),
            ("SID", "theirs", ".google.com", self._ms(DAY), self._us(0), ""),
        ])
        self.assertEqual(sorted(rows), ["cf_clearance"])

    def test_the_plain_reader_still_answers_name_to_value(self):
        db = _make_db(self.tmp / "cookies.sqlite", [
            ("cf_clearance", "live", ".commonlii.org", self._ms(DAY),
             self._us(0), ""),
        ])
        self.assertEqual(eng_rep_pdf._read_cookies_sqlite(db, "commonlii"),
                         {"cf_clearance": "live"})

    def test_a_store_that_will_not_open_is_not_fatal(self):
        bad = self.tmp / "not-a-db.sqlite"
        bad.write_bytes(b"this is not sqlite")
        self.assertEqual(eng_rep_pdf._read_cookie_rows(bad, "commonlii"), {})


class UserAgentTests(unittest.TestCase):
    """The UA a clearance is sent under is that of the Firefox that runs the
    profile it came from — CloudFlare refuses it under any other version."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ffua_test_"))

    def _profile(self, name, last_version=None) -> Path:
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        if last_version:
            _write_compat(d, last_version)
        return _make_db(d / "cookies.sqlite", [])

    def test_the_version_comes_from_the_profiles_own_compatibility_ini(self):
        store = self._profile("store", "156.0_20260909172920/20260909172920")
        regular = self._profile("regular", "154.0.1_20260824154132/20260824154132")
        self.assertIn("Firefox/156.0", eng_rep_pdf.firefox_user_agent(store))
        self.assertIn("rv:156.0", eng_rep_pdf.firefox_user_agent(store))
        self.assertIn("Firefox/154.0", eng_rep_pdf.firefox_user_agent(regular))

    def test_two_installs_at_two_versions_get_two_user_agents(self):
        store = self._profile("store", "156.0_1/1")
        regular = self._profile("regular", "155.0.1_1/1")
        self.assertNotEqual(eng_rep_pdf.firefox_user_agent(store),
                            eng_rep_pdf.firefox_user_agent(regular))

    def test_nothing_is_cached_across_a_firefox_update(self):
        # Firefox updates underneath a running app; a clearance obtained after
        # the update is good only under the new version, so the UA must track
        # compatibility.ini as it changes.
        db = self._profile("one", "155.0_1/1")
        self.assertIn("Firefox/155.0", eng_rep_pdf.firefox_user_agent(db))
        _write_compat(db.parent, "156.0_2/2")
        self.assertIn("Firefox/156.0", eng_rep_pdf.firefox_user_agent(db))

    def test_without_a_profile_the_freshest_profile_answers(self):
        newer = self._profile("newer", "156.0_1/1")
        older = self._profile("older", "154.0_1/1")
        with mock.patch.object(eng_rep_pdf, "_firefox_cookie_dbs",
                               return_value=[newer, older]):
            self.assertIn("Firefox/156.0", eng_rep_pdf.firefox_user_agent())

    def test_a_profile_that_does_not_say_falls_back_to_the_install(self):
        mute = self._profile("mute")
        with mock.patch.object(eng_rep_pdf, "_installed_firefox_major",
                               return_value="151"):
            self.assertIn("Firefox/151.0", eng_rep_pdf.firefox_user_agent(mute))
        with mock.patch.object(eng_rep_pdf, "_installed_firefox_major",
                               return_value=None):
            self.assertIn(f"Firefox/{eng_rep_pdf._FALLBACK_MAJOR}.0",
                          eng_rep_pdf.firefox_user_agent(mute))

    def test_the_platform_token_is_this_os(self):
        db = self._profile("one", "156.0_1/1")
        ua = eng_rep_pdf.firefox_user_agent(db)
        self.assertTrue(ua.startswith(f"Mozilla/5.0 ({eng_rep_pdf._ua_platform()}; rv:156.0)"))


class ClearanceCandidateTests(unittest.TestCase):
    """Several Firefox profiles, each with its own clearance and its own
    Firefox: the candidates the fetch will try, freshest first."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ffprofile_test_"))
        self.now = time.time()

    def _ms(self, offset):
        return int((self.now + offset) * 1000)

    def _us(self, offset):
        return int((self.now + offset) * 1_000_000)

    def _profile(self, name, rows, last_version="156.0_1/1") -> Path:
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        _write_compat(d, last_version)
        return _make_db(d / "cookies.sqlite", rows)

    def _candidates(self, dbs):
        with mock.patch.object(eng_rep_pdf, "_firefox_cookie_dbs",
                               return_value=list(dbs)), \
             mock.patch.dict("sys.modules", {"browser_cookie3": None}):
            return eng_rep_pdf._firefox_clearances()

    def test_the_freshest_clearance_comes_first(self):
        # The failure this guards against: the store Firefox happened to touch
        # last holds a clearance from weeks ago, so the reader passes the check
        # again and again while the app keeps leading with the old cookie.
        old = self._profile("old", [
            ("cf_clearance", "aug", ".commonlii.org", self._ms(340 * DAY),
             self._us(-28 * DAY), ""),
        ])
        fresh = self._profile("fresh", [
            ("cf_clearance", "today", ".commonlii.org", self._ms(365 * DAY),
             self._us(0), "^partitionKey=%28https%2Ccommonlii.org%29"),
            ("__cf_bm", "bm", ".commonlii.org", self._ms(30 * 60),
             self._us(0), ""),
        ])
        for order in ([old, fresh], [fresh, old]):
            got = self._candidates(order)
            self.assertEqual([c.value for c in got], ["today", "aug"])

    def test_freshness_is_when_it_was_set_not_how_long_it_will_live(self):
        long_lived = self._profile("long", [
            ("cf_clearance", "long", ".commonlii.org", self._ms(365 * DAY),
             self._us(-DAY), ""),
        ])
        just_now = self._profile("now", [
            ("cf_clearance", "now", ".commonlii.org", self._ms(30 * DAY),
             self._us(0), ""),
        ])
        got = self._candidates([long_lived, just_now])
        self.assertEqual([c.value for c in got], ["now", "long"])

    def test_a_dead_clearance_is_no_candidate_at_all(self):
        stale = self._profile("stale", [
            ("cf_clearance", "dead", ".commonlii.org", self._ms(-14 * DAY),
             self._us(-14 * DAY), ""),
        ])
        live = self._profile("live", [
            ("cf_clearance", "good", ".commonlii.org", self._ms(365 * DAY),
             self._us(0), ""),
        ])
        got = self._candidates([stale, live])
        self.assertEqual([c.value for c in got], ["good"])

    def test_each_candidate_carries_its_own_firefoxs_user_agent(self):
        store = self._profile("store", [
            ("cf_clearance", "store", ".commonlii.org", self._ms(365 * DAY),
             self._us(0), ""),
        ], last_version="156.0_1/1")
        regular = self._profile("regular", [
            ("cf_clearance", "regular", ".commonlii.org", self._ms(365 * DAY),
             self._us(-DAY), ""),
        ], last_version="154.0.1_1/1")
        got = {c.value: c for c in self._candidates([regular, store])}
        self.assertIn("Firefox/156.0", got["store"].user_agent)
        self.assertIn("Firefox/154.0", got["regular"].user_agent)
        self.assertEqual(got["store"].db, store)
        self.assertEqual(got["regular"].db, regular)

    def test_one_profiles_cookies_are_never_mixed_with_anothers(self):
        # cf_clearance and the __cf_bm beside it are issued together and only
        # accepted together, so each candidate's set goes over whole.
        other = self._profile("other", [
            ("__cf_bm", "wrong-bm", ".commonlii.org", self._ms(30 * 60),
             self._us(0), ""),
            ("cf_clearance", "old", ".commonlii.org", self._ms(DAY),
             self._us(-DAY), ""),
        ])
        live = self._profile("live", [
            ("cf_clearance", "good", ".commonlii.org", self._ms(365 * DAY),
             self._us(0), ""),
            ("__cf_bm", "right-bm", ".commonlii.org", self._ms(30 * 60),
             self._us(0), ""),
        ])
        got = self._candidates([other, live])
        self.assertEqual(got[0].cookies,
                         {"cf_clearance": "good", "__cf_bm": "right-bm"})
        self.assertEqual(got[1].cookies,
                         {"cf_clearance": "old", "__cf_bm": "wrong-bm"})

    def test_a_profile_without_a_clearance_is_skipped(self):
        # Cookies but no clearance: nothing to send from there.
        plain = self._profile("plain", [
            ("__cflb", "lb", "www.commonlii.org", self._ms(DAY),
             self._us(0), ""),
        ])
        live = self._profile("live", [
            ("cf_clearance", "good", ".commonlii.org", self._ms(365 * DAY),
             self._us(0), ""),
        ])
        got = self._candidates([plain, live])
        self.assertEqual([c.db for c in got], [live])

    def test_no_clearance_anywhere_means_no_candidates(self):
        plain = self._profile("plain", [
            ("__cflb", "lb", "www.commonlii.org", self._ms(DAY),
             self._us(0), ""),
        ])
        self.assertEqual(self._candidates([plain]), [])

    def test_every_clearance_expired_is_the_same_as_none(self):
        stale = self._profile("stale", [
            ("cf_clearance", "dead", ".commonlii.org", self._ms(-DAY),
             self._us(-DAY), ""),
        ])
        self.assertEqual(self._candidates([stale]), [])


class _Reply:
    """What a fake CommonLII answers a request with."""

    PDF = (200, b"%PDF-1.3 scan")
    CHALLENGE = (403, b"<html><title>Just a moment...</title>"
                      b"<script src='/cdn-cgi/challenge-platform/'></script>")
    NOT_FOUND = (404, b"<html>Not Found</html>")


class FetchTests(unittest.TestCase):
    """The fetch tries every candidate, in order, until CloudFlare accepts
    one — a refused clearance is passed over, not the end of the road."""

    WEB = "https://www.commonlii.org/uk/cases/EngR/1854/296.html"
    PDF = "https://www.commonlii.org/uk/cases/EngR/1854/296.pdf"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ffetch_test_"))
        self.requests = []          # (url, headers, cookies) as sent
        self.replies = {}           # cf_clearance value -> reply
        eng_rep_pdf._LAST_GOOD = None
        patches = [
            mock.patch.object(eng_rep_pdf, "CACHE_DIR", self.tmp / "cache"),
            mock.patch.object(eng_rep_pdf, "can_fetch", return_value=True),
            mock.patch.object(eng_rep_pdf, "_get", side_effect=self._answer),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, eng_rep_pdf, "_LAST_GOOD", None)

    def _answer(self, url, headers, cookies):
        self.requests.append((url, headers, cookies))
        reply = self.replies[cookies["cf_clearance"]]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _candidate(self, name, value, major, obtained=0.0):
        return eng_rep_pdf.Clearance(
            self.tmp / name / "cookies.sqlite",
            {"cf_clearance": value, "__cf_bm": f"bm-{value}"},
            obtained, obtained + 365 * DAY, eng_rep_pdf._user_agent_for(major))

    def _fetch(self, candidates):
        with mock.patch.object(eng_rep_pdf, "_firefox_clearances",
                               return_value=list(candidates)):
            return eng_rep_pdf.fetch_pdf(1854, 296, self.WEB)

    def test_the_first_accepted_clearance_is_the_one_used(self):
        self.replies = {"today": _Reply.PDF}
        data = self._fetch([self._candidate("store", "today", "156")])
        self.assertEqual(data, _Reply.PDF[1])
        url, headers, cookies = self.requests[0]
        self.assertEqual(url, self.PDF)
        self.assertEqual(headers["Referer"], self.WEB)
        self.assertEqual(cookies, {"cf_clearance": "today", "__cf_bm": "bm-today"})
        self.assertIn("Firefox/156.0", headers["User-Agent"])

    def test_a_refused_clearance_is_passed_over_for_the_next(self):
        # The failure this fixes: the regular Firefox's clearance from weeks
        # ago is unexpired on paper, so it was chosen, refused, and the reader
        # was sent back to Firefox — where they had just cleared the check in
        # the Store build, whose clearance was never tried.
        self.replies = {"aug": _Reply.CHALLENGE, "today": _Reply.PDF}
        data = self._fetch([self._candidate("regular", "aug", "154"),
                            self._candidate("store", "today", "156")])
        self.assertEqual(data, _Reply.PDF[1])
        self.assertEqual([r[2]["cf_clearance"] for r in self.requests],
                         ["aug", "today"])

    def test_each_attempt_goes_out_under_its_own_firefoxs_user_agent(self):
        self.replies = {"aug": _Reply.CHALLENGE, "today": _Reply.PDF}
        self._fetch([self._candidate("regular", "aug", "154"),
                     self._candidate("store", "today", "156")])
        agents = [r[1]["User-Agent"].rsplit(" ", 1)[-1] for r in self.requests]
        self.assertEqual(agents, ["Firefox/154.0", "Firefox/156.0"])

    def test_every_clearance_refused_sends_the_reader_to_firefox(self):
        self.replies = {"aug": _Reply.CHALLENGE, "july": _Reply.CHALLENGE}
        with self.assertRaises(eng_rep_pdf.CloudflareChallenge) as cm:
            self._fetch([self._candidate("a", "aug", "156"),
                         self._candidate("b", "july", "154")])
        self.assertEqual(cm.exception.web_url, self.WEB)
        self.assertEqual(len(self.requests), 2)   # both were tried

    def test_no_clearance_anywhere_asks_without_a_request(self):
        with self.assertRaises(eng_rep_pdf.CloudflareChallenge):
            self._fetch([])
        self.assertEqual(self.requests, [])

    def test_an_origin_error_is_reported_not_retried_with_another_cookie(self):
        # CloudFlare was passed; the origin said 404.  Another profile's
        # cookie would get the same answer.
        self.replies = {"today": _Reply.NOT_FOUND, "aug": _Reply.PDF}
        with self.assertRaises(eng_rep_pdf.OriginError) as cm:
            self._fetch([self._candidate("store", "today", "156"),
                         self._candidate("regular", "aug", "154")])
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(len(self.requests), 1)

    def test_a_network_failure_is_an_origin_error(self):
        self.replies = {"today": OSError("connection reset")}
        with self.assertRaises(eng_rep_pdf.OriginError) as cm:
            self._fetch([self._candidate("store", "today", "156")])
        self.assertEqual(cm.exception.status, 0)

    def test_the_clearance_that_worked_last_is_tried_first_next_time(self):
        # Freshest-first is a guess; an acceptance is knowledge.  Leading with
        # the accepted one spares a refused-but-unexpired clearance in another
        # profile a round trip on every case.
        self.replies = {"aug": _Reply.CHALLENGE, "today": _Reply.PDF}
        order = [self._candidate("regular", "aug", "154", obtained=2.0),
                 self._candidate("store", "today", "156", obtained=1.0)]
        self._fetch(order)
        self.assertEqual([r[2]["cf_clearance"] for r in self.requests],
                         ["aug", "today"])
        self.requests.clear()
        with mock.patch.object(eng_rep_pdf, "_firefox_clearances",
                               return_value=list(order)):
            eng_rep_pdf.fetch_pdf(1854, 297, self.WEB.replace("296", "297"))
        self.assertEqual([r[2]["cf_clearance"] for r in self.requests],
                         ["today"])

    def test_the_fetched_scan_is_cached(self):
        self.replies = {"today": _Reply.PDF}
        self._fetch([self._candidate("store", "today", "156")])
        self.assertTrue(eng_rep_pdf.is_cached(1854, 296))
        self.requests.clear()
        self.assertEqual(self._fetch([]), _Reply.PDF[1])   # no candidates needed
        self.assertEqual(self.requests, [])

    def test_without_firefox_or_curl_cffi_the_caller_links_out(self):
        with mock.patch.object(eng_rep_pdf, "can_fetch", return_value=False):
            with self.assertRaises(eng_rep_pdf.FetchUnavailable):
                self._fetch([self._candidate("store", "today", "156")])
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
