"""A Scholar result linked to another source of the case.

Searched for "Burdick v. Takushi", Scholar lists the Supreme Court's
decision first — cited by 3,664 — but titled with a link to Cornell's Legal
Information Institute, not to a Scholar opinion page, and the search left
it out: only the Ninth Circuit's and Hawaii's Burdick decisions were listed.
Scholar holds its own copy all the same, among the case's "All 5 versions";
the result is kept where Scholar ranks it, and opened, it opens that copy.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from google_scholar import (
    GoogleScholarFetcher, is_versions_url, parse_citing_page,
    scholar_version_url,
)

CLUSTER = "/scholar?cluster=2203153069610266193&hl=en&as_sdt=2006"
OWN = "/scholar_case?case=5954330685109690289&hl=en&as_sdt=2006&scfhb=1"

# Scholar's results page for the search, cut down.
RESULTS = (
    '<html><body><div id="gs_res_ccl">'
    '<div class="gs_r gs_or gs_scl"><div class="gs_ri"><h3 class="gs_rt">'
    '<a href="https://www.law.cornell.edu/supct/html/91-0535.ZO.html">'
    'Burdick v. Takushi</a></h3>'
    '<div class="gs_a">504 US 428, 112 S. Ct. 2059, 119 L. Ed. 2d 245 - '
    'Supreme Court, 1992 - law.cornell.edu</div>'
    '<div class="gs_rs">Hawaii prohibits write-in voting.</div>'
    '<div class="gs_fl"><a href="/scholar?cites=2203153069610266193&amp;'
    'as_sdt=2005&amp;sciodt=2006&amp;hl=en">Cited by 3664</a> '
    '<a href="/scholar_case?about=2203153069610266193&amp;hl=en">How '
    'cited</a> <a href="/scholar?cluster=2203153069610266193&amp;hl=en&amp;'
    'as_sdt=2006">All 5 versions</a></div></div></div>'
    '<div class="gs_r gs_or gs_scl"><div class="gs_ri"><h3 class="gs_rt">'
    '<a href="/scholar_case?case=7260259758234452022">Burdick v. Takushi</a>'
    '</h3><div class="gs_a">937 F. 2d 415 - Court of Appeals, 9th Circuit, '
    '1991</div><div class="gs_fl"><a href="/scholar?cites=7260259758234452022'
    '">Cited by 55</a></div></div></div>'
    # Another source, and no versions to find Scholar's copy among.
    '<div class="gs_r gs_or gs_scl"><div class="gs_ri"><h3 class="gs_rt">'
    '<a href="https://example.gov/opinion.pdf">Doe v. Roe</a></h3>'
    '<div class="gs_a">1 F. 4th 2 - Court of Appeals, 2nd Circuit, 2021 - '
    'example.gov</div></div></div>'
    '</div></body></html>')
# Its "All 5 versions" page, cut down: Scholar's own copy is the fourth.
VERSIONS = (
    '<html><body><div id="gs_res_ccl">'
    '<div class="gs_r gs_or gs_scl"><h3 class="gs_rt"><a href="https://'
    'www.law.cornell.edu/supct/html/91-0535.ZO.html">Burdick v. Takushi</a>'
    '</h3></div>'
    '<div class="gs_r gs_or gs_scl"><h3 class="gs_rt"><span class="gs_ct1">'
    '[CITATION]</span> Burdick v. Takushi</h3></div>'
    '<div class="gs_r gs_or gs_scl"><h3 class="gs_rt">'
    f'<a href="{OWN.replace("&", "&amp;")}">Burdick v. Takushi</a></h3>'
    '<div class="gs_a">504 US 428 - Supreme Court, 1992 - Google Scholar'
    '</div></div></div></body></html>')
OPINION = ('<html><body><div id="gs_opinion"><h3 id="gsl_case_name">'
           'BURDICK v. TAKUSHI, DIRECTOR OF ELECTIONS OF HAWAII, et al.</h3>'
           '<p>JUSTICE WHITE delivered the opinion of the Court.</p>'
           '</div></body></html>')


class _Scholar:
    """Scholar's pages by URL; every request recorded."""

    def __init__(self):
        self.asked = []

    def __call__(self, url, *a, **kw):
        self.asked.append(url)
        text = (VERSIONS if "cluster=" in url else
                OPINION if "scholar_case?case=5954" in url else RESULTS)
        return SimpleNamespace(text=text, status_code=200)


class OtherSourceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fetcher = GoogleScholarFetcher(
            cache_path=Path(self._tmp.name) / "cache.db", delay=0.0)
        self.fetcher._browser_dead = True   # never launch a real Firefox
        self.scholar = _Scholar()
        self.fetcher._get = self.scholar    # type: ignore[assignment]

    def tearDown(self):
        self.fetcher._db.close()  # so Windows lets the directory go
        self._tmp.cleanup()

    def test_it_is_listed_where_scholar_ranks_it(self):
        results = self.fetcher.search_cases("Burdick v. Takushi",
                                            allow_db_fallback=False)
        self.assertEqual([(r.title, r.cited_by) for r in results],
                         [("Burdick v. Takushi", 3664),
                          ("Burdick v. Takushi", 55)])
        self.assertTrue(is_versions_url(results[0].url))
        self.assertIn("cluster=2203153069610266193", results[0].url)

    def test_opened_it_is_scholar_s_own_copy(self):
        url = self.fetcher.search_cases("Burdick v. Takushi",
                                        allow_db_fallback=False)[0].url
        fetched = self.fetcher.fetch_by_url(url)
        self.assertIsNotNone(fetched)
        self.assertIn("case=5954330685109690289", fetched[0])
        self.assertIn("BURDICK v. TAKUSHI", fetched[1])
        # Once read, not asked again.
        asked = len(self.scholar.asked)
        self.assertEqual(self.fetcher.fetch_by_url(url)[0], fetched[0])
        self.assertEqual(len(self.scholar.asked), asked)

    def test_a_versions_page_with_no_scholar_copy_opens_nothing(self):
        self.assertEqual(scholar_version_url(RESULTS.replace(
            "/scholar_case?case=7260259758234452022", "https://x.test/")),
            "")
        self.fetcher._get = lambda url, *a, **kw: SimpleNamespace(
            text="<html><body></body></html>", status_code=200)
        self.assertIsNone(self.fetcher.fetch_by_url(
            "https://scholar.google.com" + CLUSTER))
        self.assertTrue(self.fetcher.last_fetch_absent())

    def test_a_citing_case_linked_elsewhere_opens_too(self):
        page = parse_citing_page(RESULTS)
        self.assertTrue(is_versions_url(page.results[0].url))
        self.assertEqual(page.results[2].url, "")


if __name__ == "__main__":
    unittest.main()
