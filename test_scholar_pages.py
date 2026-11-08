"""A blocked search must not masquerade as a real empty result page."""

import unittest
from pathlib import Path

from scholar_pages import (
    has_search_content,
    is_challenge,
    is_search_url,
    response_needs_browser,
)


URL = "https://scholar.google.com/scholar?q=test&as_sdt=4"
OPINION_URL = "https://scholar.google.com/scholar_case?case=123"
RESULTS = """<div class="gs_r gs_or gs_scl"><h3 class="gs_rt">
<a href="/scholar_case?case=123">Example v. Respondent</a></h3>
<div class="gs_rs">A case result.</div></div>"""


class SearchContentTests(unittest.TestCase):
    def test_real_citing_page_fixture_has_search_content(self):
        html = (Path(__file__).parent / "test_data" / "scholar_cites_roe.html").read_text(
            encoding="utf-8")
        self.assertTrue(has_search_content(html))
        self.assertFalse(response_needs_browser(URL, URL, html))

    def test_a_result_does_not_need_the_outer_results_container(self):
        self.assertTrue(has_search_content(RESULTS))

    def test_citation_only_titles_are_results(self):
        html = '<div class="gs_r"><h3 class="gs_rt"><span>[CITATION]</span> '
        html += 'Example v. Respondent, 123 US 456</h3></div>'
        self.assertTrue(has_search_content(html))

    def test_empty_shells_and_result_shaped_placeholders_are_not_results(self):
        for html in ('', '<div id="gs_res_ccl"></div>',
                     '<div class="gs_r">Loading...</div>',
                     '<div class="gs_r"><h3 class="gs_rt"> </h3></div>',
                     '<div class="gs_r"><h3 class="gs_rt">[CITATION]</h3></div>',
                     '<html><body>Access denied</body></html>'):
            with self.subTest(html=html):
                self.assertFalse(has_search_content(html))
                self.assertTrue(response_needs_browser(URL, URL, html))

    def test_explicit_empty_search_notices_are_valid_answers(self):
        for notice in ('Your search - example - did not match any articles.',
                       'No results found.', 'No articles were found for this search.',
                       'Your query returned no results.', 'No results',
                       'Your search did not return any results.'):
            with self.subTest(notice=notice):
                html = f'<div id="gs_res_ccl"><div class="gs_r">{notice}</div></div>'
                self.assertTrue(has_search_content(html))
                self.assertFalse(response_needs_browser(URL, URL, html))

    def test_did_you_mean_without_an_answer_is_not_proof_of_results(self):
        html = '<div id="gs_res_ccl"><div class="gs_r">Did you mean: something?</div></div>'
        self.assertFalse(has_search_content(html))

    def test_a_full_page_can_have_a_standalone_no_results_notice(self):
        html = ('<html><head><title>Google Scholar</title></head><body>'
                '<nav>My library</nav><div id="gs_res_ccl">No results</div>'
                '</body></html>')
        self.assertTrue(has_search_content(html))
        self.assertFalse(response_needs_browser(URL, URL, html))


class ChallengeTests(unittest.TestCase):
    def test_known_challenge_variants(self):
        for html in ("<p>Your network may be sending automated queries.</p>",
                     "<p>Our systems have detected UNUSUAL TRAFFIC.</p>",
                     "<p>Show you're not a robot.</p>",
                     "<p>Verify you are human.</p>",
                     "<div CLASS='G-RECAPTCHA'></div>",
                     "<form ID='ReCaptcha'></form>",
                     "<form id=captcha-form></form>",
                     "<iframe src='https://www.google.com/recaptcha/api2/anchor'></iframe>",
                     "<script src='https://www.google.com/recaptcha/api.js'></script>"):
            with self.subTest(html=html):
                self.assertTrue(is_challenge(URL, html))
                self.assertTrue(response_needs_browser(URL, URL, html))

    def test_redirect_to_sorry_is_a_challenge_even_with_empty_html(self):
        self.assertTrue(is_challenge("https://www.google.com/SORRY/index", ""))
        self.assertTrue(response_needs_browser(URL, "https://www.google.com/sorry/index", ""))

    def test_sorry_link_in_a_query_does_not_make_a_real_result_a_challenge(self):
        url = URL + "&ref=https://www.google.com/sorry/index"
        self.assertFalse(is_challenge(url, RESULTS))

    def test_opinion_quotes_of_challenge_phrases_are_not_challenges(self):
        html = '<div id="gs_opinion"><p>The warning said "unusual traffic" '
        html += 'and "automated queries", and asked if the user was "not a robot".</p></div>'
        self.assertFalse(is_challenge(OPINION_URL, html))
        self.assertFalse(response_needs_browser(OPINION_URL, OPINION_URL, html))

    def test_search_snippets_can_quote_challenge_phrases(self):
        html = RESULTS.replace('A case result.', 'The message said "automated queries".')
        self.assertFalse(is_challenge(URL, html))
        self.assertFalse(response_needs_browser(URL, URL, html))

    def test_an_empty_query_can_contain_challenge_words(self):
        html = '<div id="gs_res_ccl">Your search - "not a robot" - did not match any articles.</div>'
        self.assertFalse(is_challenge(URL, html))
        self.assertFalse(response_needs_browser(URL, URL, html))

    def test_actual_captcha_widget_wins_over_stale_results(self):
        self.assertTrue(is_challenge(URL, RESULTS + '<div class="g-recaptcha"></div>'))


class ResponseTests(unittest.TestCase):
    def test_search_url_is_the_exact_scholar_search_endpoint(self):
        self.assertTrue(is_search_url(URL))
        self.assertTrue(is_search_url('https://scholar.google.com/scholar?cites=123'))
        self.assertTrue(is_search_url('https://scholar.google.com/scholar?cluster=123'))
        for url in (OPINION_URL, 'https://scholar.google.com/',
                    'https://scholar.google.com.evil.example/scholar',
                    'https://www.google.com/sorry/index', 'not a URL'):
            self.assertFalse(is_search_url(url))

    def test_successful_search_redirected_to_a_consent_page_needs_browser(self):
        self.assertTrue(response_needs_browser(URL, 'https://consent.google.com/m',
                                              '<h1>Before you continue to Google</h1>'))

    def test_refusal_statuses_need_browser_even_if_body_looks_like_results(self):
        for status in (403, 429):
            self.assertTrue(response_needs_browser(URL, URL, RESULTS, status))

    def test_server_errors_keep_their_existing_retry_path(self):
        for status in (404, 500, 502, 503):
            self.assertFalse(response_needs_browser(URL, URL, '<h1>Temporary error</h1>', status))

    def test_non_search_pages_do_not_need_a_results_container(self):
        self.assertFalse(response_needs_browser(OPINION_URL, OPINION_URL, '<h1>Opinion</h1>'))
        self.assertFalse(response_needs_browser('https://scholar.google.com/',
                                              'https://scholar.google.com/', '<h1>Google Scholar</h1>'))


if __name__ == '__main__':
    unittest.main()
