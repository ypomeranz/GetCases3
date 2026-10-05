"""Opinions Google Scholar is still expected to revise are checked against it.

A recent opinion whose Scholar page has no reporter's star pages yet — or
only the slip opinion's own — goes stale in the opinion database: Scholar adds
the reporter's pages, or the reported text, once the case is published.  Such
an opinion is asked after on Scholar before its stored copy is served, and a
newer version replaces the stored one.  Scholar cooling down after a block, or
failing the one request made, leaves the stored copy to answer.

Google Scholar itself is stubbed; every URL asked for is recorded.
"""

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")

from google_scholar import GoogleScholarFetcher, ScholarError
from opinion_db import OpinionDB, awaiting_reporter_pages


TODAY = date(2026, 10, 5)
SID = "1518252864075002865"
URL = f"https://scholar.google.com/scholar_case?case={SID}&hl=en&as_sdt=2006"
BODY = "The judgment of the Court of Appeals is affirmed. " * 12
LONG_BODY = "The judgment of the Court of Appeals is affirmed. " * 160


def _star(page: int) -> str:
    """A star page, marked up the way Scholar does."""
    return (f'<a class="gsl_pagenum" href="#p{page}">{page}</a>'
            f'<a class="gsl_pagenum2" href="#p{page}" id="p{page}">'
            f"*{page}</a>")


def _page(decided: date | None, cite: str = "", stars=(), body: str = BODY,
          year_only: bool = False) -> str:
    """An opinion division as Scholar serves it."""
    head = (f"<center><b>{cite} ({decided.year})</b></center>"
            if cite and decided else "")
    when = ""
    if decided and not year_only:
        when = (f"<center>Decided {decided:%B} {decided.day}, "
                f"{decided.year}.</center>")
    paras = "".join(f"<p>{_star(p)} {body}</p>" for p in stars) or \
        f"<p>{body}</p>"
    return ('<div id="gs_opinion">' + head
            + '<center><h3 id="gsl_case_name">DONALD J. TRUMP<br/> v.<br/> '
            "REBECCA KELLY SLAUGHTER.</h3></center>"
            "<center><p><b>Supreme Court of the United States.</b></p>"
            f"</center>{when}{paras}</div>")


class AwaitingReporterPagesTests(unittest.TestCase):
    RECENT = TODAY - timedelta(days=100)

    def awaiting(self, html: str) -> bool:
        return awaiting_reporter_pages(html, today=TODAY)

    def test_a_recent_slip_opinion_is_awaited(self):
        self.assertTrue(self.awaiting(_page(self.RECENT)))

    def test_a_reporter_s_star_pages_are_final(self):
        self.assertFalse(self.awaiting(
            _page(self.RECENT, "146 S.Ct. 2438", stars=(2440, 2441))))

    def test_star_pages_tracking_the_slip_opinion_are_not(self):
        # "*1", "*2" under a "146 S. Ct. 2438" heading are the slip
        # opinion's pages, not the reporter's.
        self.assertTrue(self.awaiting(
            _page(self.RECENT, "146 S.Ct. 2438", stars=(1, 2))))
        # …nor are star pages with no reporter citation to account for them.
        self.assertTrue(self.awaiting(_page(self.RECENT, stars=(1, 2))))

    def test_a_short_order_on_its_reporter_page_is_final(self):
        # Wolford v. Lopez, 146 S. Ct. 1438: a one-paragraph order needs no
        # star page and never gets one.
        self.assertFalse(self.awaiting(_page(self.RECENT, "146 S.Ct. 1438")))

    def test_but_a_long_opinion_without_star_pages_is_awaited(self):
        self.assertTrue(self.awaiting(
            _page(self.RECENT, "146 S.Ct. 1438", body=LONG_BODY)))

    def test_an_opinion_over_a_year_old_is_final(self):
        self.assertFalse(self.awaiting(_page(TODAY - timedelta(days=400))))
        self.assertTrue(self.awaiting(_page(TODAY - timedelta(days=360))))

    def test_a_year_alone_counts_to_its_last_day(self):
        # Dated only by the year after its citation: "(2025)".
        def slip(year: int) -> str:
            return _page(date(year, 3, 1), "146 S.Ct. 2438", stars=(1, 2),
                         year_only=True)
        self.assertTrue(self.awaiting(slip(2025)))
        self.assertFalse(self.awaiting(slip(2024)))

    def test_an_undated_opinion_is_final(self):
        self.assertFalse(self.awaiting(_page(None)))
        self.assertFalse(self.awaiting(""))


class _FakeScholar:
    """Stands in for ``GoogleScholarFetcher._get``: the case page it holds
    for any scholar_case URL, or a block when ``down``."""

    def __init__(self, page: str):
        self.page = page
        self.urls: list[str] = []
        self.once: list[bool] = []
        self.down = False

    def __call__(self, url, referer="", once=False):
        self.urls.append(url)
        self.once.append(once)
        if self.down:
            raise ScholarError("403 blocked")
        return SimpleNamespace(status_code=200, url=url,
                               text=f"<html><body>{self.page}</body></html>")


class CurrentVersionTests(unittest.TestCase):
    """The stored copy of an opinion Scholar may have revised is checked
    against Scholar before it is served."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        recent = date.today() - timedelta(days=60)
        self.slip = _page(recent)
        self.reported = _page(recent, "146 S.Ct. 2438", stars=(2440, 2441))
        self.db = OpinionDB(jsonl_path=self.dir / "opinions.jsonl",
                            index_path=self.dir / "opinions.db")
        self.assertTrue(self.db.add_opinion(URL, self.slip))
        self.scholar = _FakeScholar(self.reported)
        self.fetcher = GoogleScholarFetcher(
            cache_path=self.dir / "cache.db", delay=0.0, db=self.db)
        self.fetcher._browser_dead = True   # never launch a real Firefox
        self.fetcher._get = self.scholar    # type: ignore[assignment]

    def tearDown(self):
        self._settle()
        self.fetcher._db.close()
        self.db.close()
        self._tmp.cleanup()

    def _settle(self) -> None:
        """Wait out the database rewrites under way on their own threads."""
        for update in self.fetcher._db_updates:
            update.join(timeout=30)

    def test_a_revised_opinion_replaces_the_stored_copy(self):
        self.db.save_parallel_citation(SID, "607 U.S. 1")
        url, html = self.fetcher.fetch_by_url(URL)
        self.assertEqual(html, self.reported)
        self.assertEqual(self.scholar.once, [True])   # one attempt only
        self._settle()
        stored = self.db.get_by_scholar_id(SID)
        self.assertEqual(stored["html"], self.reported)
        # What the page can't give is kept: the parallel cite recovered.
        self.assertIn("607 U.S. 1", stored["cites"])
        self.assertIn("146 S.Ct. 2438", stored["cites"])

    def test_the_query_cache_is_brought_up_to_date_too(self):
        self.fetcher.put_cached("cite2:146 S.Ct. 2438", URL, self.slip)
        self.fetcher.fetch_by_url(URL)
        self.assertEqual(
            self.fetcher._cache_get("cite2:146 S.Ct. 2438")[1], self.reported)

    def test_the_newer_version_is_served_for_the_rest_of_the_session(self):
        # …though the database's rewrite, on its own thread, isn't done.
        with mock.patch.object(self.db, "update_opinion"):
            self.fetcher.fetch_by_url(URL)
            self._settle()
            url, html = self.fetcher.fetch_by_url(URL)
        self.assertEqual(html, self.reported)
        self.assertEqual(len(self.scholar.urls), 1)

    def test_an_unchanged_opinion_is_not_rewritten(self):
        self.scholar.page = self.slip
        with mock.patch.object(self.db, "update_opinion") as update:
            url, html = self.fetcher.fetch_by_url(URL)
        self.assertEqual(html, self.slip)
        self.assertEqual(len(self.scholar.urls), 1)
        update.assert_not_called()

    def test_scholar_failing_leaves_the_stored_copy(self):
        self.scholar.down = True
        url, html = self.fetcher.fetch_by_url(URL)
        self.assertEqual(html, self.slip)
        self.assertEqual(len(self.scholar.urls), 1)
        self.assertEqual(self.db.get_by_scholar_id(SID)["html"], self.slip)
        self.assertFalse(self.fetcher.last_fetch_absent())

    def test_each_opinion_is_asked_after_once_a_session(self):
        self.scholar.down = True
        self.fetcher.fetch_by_url(URL)
        self.scholar.down = False
        url, html = self.fetcher.fetch_by_url(URL)
        self.assertEqual(html, self.slip)
        self.assertEqual(len(self.scholar.urls), 1)

    def test_a_final_opinion_is_served_without_asking(self):
        old = _page(date.today() - timedelta(days=800))
        old_url = URL.replace(SID, "42")
        self.assertTrue(self.db.add_opinion(old_url, old))
        url, html = self.fetcher.fetch_by_url(old_url)
        self.assertEqual(html, old)
        self.assertEqual(self.scholar.urls, [])

    def test_a_citation_s_stored_copy_is_checked_too(self):
        # The database answers a name lookup without searching Scholar; the
        # copy it holds is still checked.
        url, html = self.fetcher.fetch_by_name("Trump v. Slaughter")
        self.assertEqual(html, self.reported)
        self.assertEqual(self.scholar.urls, [URL])


class OneAttemptTests(unittest.TestCase):
    """A check for a newer version makes one request at most, and none
    while Scholar is cooling down after a block."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fetcher = GoogleScholarFetcher(
            cache_path=Path(self._tmp.name) / "cache.db", delay=0.0)
        self.fetcher._browser_dead = True
        self.fetcher._warmed = True
        self.fetcher._browser_first = False
        self.fetcher._blocked_until = 0.0
        self.fetcher._browser_get = mock.Mock(return_value=None)
        self.fetcher._save_state = mock.Mock()   # leave the real state be
        self.session = mock.Mock()
        self.fetcher._session = self.session
        self.fetcher._build_session = mock.Mock(return_value=self.session)

    def tearDown(self):
        self.fetcher._db.close()
        self._tmp.cleanup()

    def _answer(self, status: int, url: str = URL):
        self.session.get.return_value = SimpleNamespace(
            status_code=status, url=url, text="",
            raise_for_status=lambda: None)

    def test_nothing_is_sent_while_cooling_down(self):
        import time
        self.fetcher._blocked_until = time.monotonic() + 60
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL, once=True)
        self.session.get.assert_not_called()
        self.fetcher._browser_get.assert_not_called()

    def test_a_challenge_is_neither_retried_nor_escalated(self):
        self._answer(429)
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL, once=True)
        self.assertEqual(self.session.get.call_count, 1)
        self.fetcher._browser_get.assert_not_called()
        self.assertEqual(self.fetcher._blocked_until, 0.0)

    def test_a_hiccup_is_not_retried(self):
        self._answer(503)
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL, once=True)
        self.assertEqual(self.session.get.call_count, 1)

    def test_an_answer_is_returned(self):
        self._answer(200)
        self.assertEqual(self.fetcher._get(URL, once=True).status_code, 200)


class UpdateOpinionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db = OpinionDB(jsonl_path=root / "opinions.jsonl",
                            index_path=root / "opinions.db")

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_enrichments_survive_the_update(self):
        recent = TODAY - timedelta(days=60)
        self.assertTrue(self.db.add_opinion(URL, _page(recent)))
        self.db.save_parallel_citation(SID, "607 U.S. 1")
        pages = {"v": 1, "cite": "607 U.S. 1", "pages": [[2, 0, 0, 3, 0]]}
        self.db.save_pagination(SID, pages)
        newer = _page(recent, "146 S.Ct. 2438", stars=(2440,))
        rec = self.db.update_opinion(URL, newer)
        self.assertIsNotNone(rec)
        stored = self.db.get_by_scholar_id(SID)
        self.assertEqual(stored["html"], newer)
        self.assertEqual(set(stored["cites"]),
                         {"146 S.Ct. 2438", "607 U.S. 1"})
        self.assertEqual(self.db.stored_pagination(SID), pages)
        self.assertEqual(self.db.count(), 1)

    def test_an_opinion_not_held_is_added(self):
        self.assertIsNotNone(self.db.update_opinion(URL, _page(TODAY)))
        self.assertEqual(self.db.count(), 1)


if __name__ == "__main__":
    unittest.main()
