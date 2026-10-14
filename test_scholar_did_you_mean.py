"""Google Scholar's "Did you mean" for a search that found nothing.

Searched for "United States v Toussett", Scholar finds no case and offers
"Did you mean: United States v Touset" — the case meant.  The search takes
the suggestion and searches it instead, once, and Spotlight shows its
results, ranked against the words searched and saying which they were.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from google_scholar import GoogleScholarFetcher, did_you_mean

# Scholar's page for the misspelled search, cut down.
NO_RESULTS = (
    '<html><body><div id="gs_res_ccl"><div id="gs_res_ccl_top">'
    '<div class="gs_r gs_pda" style="font-size:15px"><span class="gs_red">'
    'Did you mean: </span><a href="/scholar?hl=en&amp;as_sdt=2006&amp;q='
    'United+States+v+Touset">United States v <b><i>Touset</i></b></a></div>'
    '<div class="gs_r" style="font-size:15px">Your search - <b>United States'
    ' v Toussett</b> - did not match any articles.</div></div></div>'
    '</body></html>')
NOTHING = ('<html><body><div id="gs_res_ccl"><div class="gs_r">Your search'
           ' did not match any articles.</div></div></body></html>')
TOUSET = (
    '<html><body><div id="gs_res_ccl">'
    '<div class="gs_r gs_or gs_scl"><h3 class="gs_rt">'
    '<a href="/scholar_case?case=111">United States v. Touset</a></h3>'
    '<div class="gs_a">890 F. 3d 1227 - Court of Appeals, 11th Circuit, '
    '2018</div><div class="gs_rs">A border search of electronic devices.'
    '</div></div></div></body></html>')


class _Scholar:
    """Scholar's answers by the words searched; every search recorded."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def __call__(self, url, *a, **kw):
        q = parse_qs(urlparse(url).query).get("q", [""])[0]
        self.asked.append(q)
        return SimpleNamespace(text=self.pages.get(q, NOTHING),
                               status_code=200)


class DidYouMeanTests(unittest.TestCase):
    def test_the_suggestion_read_off_the_page(self):
        self.assertEqual(did_you_mean(NO_RESULTS), "United States v Touset")
        self.assertEqual(did_you_mean(NOTHING), "")
        self.assertEqual(did_you_mean(TOUSET), "")


class SearchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fetcher = GoogleScholarFetcher(
            cache_path=Path(self._tmp.name) / "cache.db", delay=0.0)
        self.fetcher._browser_dead = True   # never launch a real Firefox

    def tearDown(self):
        self.fetcher._db.close()  # so Windows lets the directory go
        self._tmp.cleanup()

    def search(self, pages, query="United States v Toussett"):
        scholar = _Scholar(pages)
        self.fetcher._get = scholar  # type: ignore[assignment]
        results = self.fetcher.search_cases(query, allow_db_fallback=False)
        return results, scholar.asked

    def test_nothing_found_so_the_suggestion_is_searched(self):
        results, asked = self.search({
            "United States v Toussett": NO_RESULTS,
            "United States v Touset": TOUSET})
        self.assertEqual(asked, ["United States v Toussett",
                                 "United States v Touset"])
        self.assertEqual([r.title for r in results],
                         ["United States v. Touset"])
        self.assertEqual(self.fetcher.last_suggestion(),
                         "United States v Touset")

    def test_and_remembered_with_the_search(self):
        self.search({"United States v Toussett": NO_RESULTS,
                     "United States v Touset": TOUSET})
        results, asked = self.search({})
        self.assertEqual(asked, [])                 # cached
        self.assertEqual(len(results), 1)
        self.assertEqual(self.fetcher.last_suggestion(),
                         "United States v Touset")

    def test_only_once(self):
        # A suggestion that finds nothing either, and suggests again.
        again = NO_RESULTS.replace("Touset", "Tousett")
        results, asked = self.search({
            "United States v Toussett": NO_RESULTS,
            "United States v Touset": again})
        self.assertEqual(results, [])
        self.assertEqual(len(asked), 2)
        self.assertEqual(self.fetcher.last_suggestion(), "")

    def test_a_search_that_finds_something_is_its_own(self):
        results, asked = self.search({"United States v Touset": TOUSET},
                                     query="United States v Touset")
        self.assertEqual(asked, ["United States v Touset"])
        self.assertEqual(len(results), 1)
        self.assertEqual(self.fetcher.last_suggestion(), "")


if __name__ == "__main__":
    unittest.main()
