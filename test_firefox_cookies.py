"""Reading the CloudFlare clearance out of Firefox's cookie store.

The English Reports scans on CommonLII sit behind CloudFlare.  The reader
clears the check once in Firefox and the app replays the ``cf_clearance``
cookie it left in ``cookies.sqlite``; a *stale* clearance is indistinguishable
from a fresh one until CloudFlare rejects it, at which point the app asks the
reader to clear the check again — which they have already done.  So which row
is picked matters, and two things about the store make picking hard:

* ``moz_cookies.expiry`` is written in **milliseconds** now.  It used to be
  seconds, and ``creationTime``/``lastAccessed``/``updateTime`` still are
  microseconds, so a reader that assumes the old unit either believes every
  cookie is dead or believes every cookie is live.
* Under Total Cookie Protection a cookie is stored per partition, so one host
  and one name can mean several rows — some of them long expired.

And a machine can have more than one Firefox (a Microsoft Store build beside a
regular one), each with its own profile holding its own CommonLII clearance.

These tests build cookie stores with those shapes and check the reader takes
the live clearance out of them.
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


def _make_db(path: Path, rows) -> Path:
    """A cookies.sqlite holding *rows* — Firefox's real schema, near enough.

    Each row is ``(name, value, host, expiry, lastAccessed, originAttributes)``
    with *expiry* in whatever unit the test means to exercise.
    """
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes "
        "TEXT, name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, "
        "lastAccessed INTEGER, creationTime INTEGER, isSecure INTEGER, "
        "isHttpOnly INTEGER, inBrowserElement INTEGER, sameSite INTEGER, "
        "schemeMap INTEGER, isPartitionedAttributeSet INTEGER, "
        "updateTime INTEGER)"
    )
    for name, value, host, expiry, accessed, origin in rows:
        con.execute(
            "INSERT INTO moz_cookies (originAttributes, name, value, host, "
            "path, expiry, lastAccessed, creationTime, isSecure, isHttpOnly, "
            "inBrowserElement, sameSite, schemeMap, "
            "isPartitionedAttributeSet, updateTime) "
            "VALUES (?,?,?,?,'/',?,?,?,1,1,0,0,2,?,?)",
            (origin, name, value, host, expiry, accessed, accessed,
             1 if origin else 0, accessed),
        )
    con.commit()
    con.close()
    return path


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
    """One store, read into {name: (value, expiry, last used)}."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ffcookie_test_"))
        self.now = time.time()

    def _rows(self, rows, domain="commonlii"):
        db = _make_db(self.tmp / "cookies.sqlite", rows)
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

    def test_the_longest_lived_copy_wins_among_live_ones(self):
        rows = self._rows([
            ("cf_clearance", "older", ".commonlii.org", self._ms(DAY),
             self._us(0), ""),
            ("cf_clearance", "fresher", ".commonlii.org", self._ms(365 * DAY),
             self._us(-60), ""),
        ])
        # Firefox keeps a cookie's original creationTime when the server
        # re-sets it, so age says nothing; the lifetime is fixed at issue, so
        # the furthest expiry is the most recently granted.
        self.assertEqual(rows["cf_clearance"][0], "fresher")

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


class ProfileChoiceTests(unittest.TestCase):
    """Several Firefox profiles, one of them holding the clearance that works."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ffprofile_test_"))
        self.now = time.time()

    def _ms(self, offset):
        return int((self.now + offset) * 1000)

    def _us(self, offset):
        return int((self.now + offset) * 1_000_000)

    def _profile(self, name, rows) -> Path:
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        return _make_db(d / "cookies.sqlite", rows)

    def _cookies(self, dbs):
        with mock.patch.object(eng_rep_pdf, "_firefox_cookie_dbs",
                               return_value=list(dbs)):
            return eng_rep_pdf._firefox_cookies()

    def test_the_live_clearance_wins_over_a_dead_one_in_another_profile(self):
        # The failure this fixes: the store Firefox happened to touch last
        # holds a clearance that expired weeks ago, so the reader passes the
        # check again and again while the app keeps sending the dead cookie.
        stale = self._profile("stale", [
            ("cf_clearance", "dead", ".commonlii.org", self._ms(-14 * DAY),
             self._us(-14 * DAY), ""),
        ])
        live = self._profile("live", [
            ("cf_clearance", "good", ".commonlii.org", self._ms(365 * DAY),
             self._us(0), "^partitionKey=%28https%2Ccommonlii.org%29"),
            ("__cf_bm", "bm", ".commonlii.org", self._ms(30 * 60),
             self._us(0), ""),
        ])
        got = self._cookies([stale, live])
        self.assertEqual(got["cf_clearance"], "good")

    def test_the_order_the_stores_are_offered_in_does_not_decide(self):
        stale = self._profile("stale", [
            ("cf_clearance", "dead", ".commonlii.org", self._ms(-DAY),
             self._us(-DAY), ""),
        ])
        live = self._profile("live", [
            ("cf_clearance", "good", ".commonlii.org", self._ms(365 * DAY),
             self._us(0), ""),
        ])
        for order in ([stale, live], [live, stale]):
            self.assertEqual(self._cookies(order)["cf_clearance"], "good")

    def test_the_longest_lived_of_two_live_clearances_is_taken(self):
        older = self._profile("older", [
            ("cf_clearance", "older", ".commonlii.org", self._ms(DAY),
             self._us(0), ""),
        ])
        newer = self._profile("newer", [
            ("cf_clearance", "newer", ".commonlii.org", self._ms(365 * DAY),
             self._us(-3600), ""),
        ])
        self.assertEqual(self._cookies([older, newer])["cf_clearance"],
                         "newer")

    def test_one_profiles_cookies_are_never_mixed_with_anothers(self):
        # cf_clearance and the __cf_bm beside it are issued together and only
        # accepted together, so the winning profile's set goes over whole.
        other = self._profile("other", [
            ("__cf_bm", "wrong-bm", ".commonlii.org", self._ms(30 * 60),
             self._us(0), ""),
            ("cf_clearance", "old", ".commonlii.org", self._ms(DAY),
             self._us(0), ""),
        ])
        live = self._profile("live", [
            ("cf_clearance", "good", ".commonlii.org", self._ms(365 * DAY),
             self._us(0), ""),
            ("__cf_bm", "right-bm", ".commonlii.org", self._ms(30 * 60),
             self._us(0), ""),
        ])
        got = self._cookies([other, live])
        self.assertEqual(got, {"cf_clearance": "good", "__cf_bm": "right-bm"})

    def test_no_clearance_anywhere_reports_what_there_was(self):
        # Cookies but no clearance: the caller shows the hand-off panel.
        plain = self._profile("plain", [
            ("__cflb", "lb", "www.commonlii.org", self._ms(DAY),
             self._us(0), ""),
        ])
        with mock.patch.object(eng_rep_pdf, "_firefox_cookie_dbs",
                               return_value=[plain]), \
             mock.patch.dict("sys.modules", {"browser_cookie3": None}):
            got = eng_rep_pdf._firefox_cookies()
        self.assertNotIn("cf_clearance", got or {})

    def test_every_clearance_expired_is_the_same_as_none(self):
        stale = self._profile("stale", [
            ("cf_clearance", "dead", ".commonlii.org", self._ms(-DAY),
             self._us(-DAY), ""),
        ])
        with mock.patch.object(eng_rep_pdf, "_firefox_cookie_dbs",
                               return_value=[stale]), \
             mock.patch.dict("sys.modules", {"browser_cookie3": None}):
            got = eng_rep_pdf._firefox_cookies()
        self.assertNotIn("cf_clearance", got or {})


if __name__ == "__main__":
    unittest.main()
