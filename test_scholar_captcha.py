"""A Google Scholar CAPTCHA goes straight to Firefox, where the reader answers it.

Two rules:

  * **One challenge is enough.**  With Selenium and Firefox installed, the
    first challenge to the app's own requests sends the page to the real
    Firefox at once, rather than through the other browser fingerprints in
    turn.  Without them, the fingerprints are still tried, as before.
  * **A challenge to Firefox is put to the reader.**  The page opens again in
    a Firefox window they can see; once the CAPTCHA is answered, the page it
    stood in front of is what the fetch returns, the window closes, and what
    answering earned goes on into the hidden Firefox.  A window closed
    unanswered is not opened again for a while.

Selenium need not be installed: its two exceptions are stubbed for each test,
and the drivers are fakes.
"""

import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import google_scholar as gs
import scholar_browser as sb
from google_scholar import GoogleScholarFetcher, ScholarError


URL = "https://scholar.google.com/scholar?q=%22347+U.S.+483%22&as_sdt=4"
SORRY = "https://www.google.com/sorry/index?continue=" + URL
CHALLENGE = ("<html><body>Our systems have detected unusual traffic from "
             "your computer network.<div class=\"g-recaptcha\"></div>"
             "</body></html>")
RESULTS = ("<html><body><div id=\"gs_res_ccl\"><div class=\"gs_r\">"
           "Brown v. Board of Education</div></div></body></html>")
HOME = "<html><body><div id=\"gs_hdr\">Google Scholar</div></body></html>"


# ---------------------------------------------------------------------------
# The fetcher: which channel a challenged page goes to
# ---------------------------------------------------------------------------


class _FetcherFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fetcher = GoogleScholarFetcher(
            cache_path=Path(self._tmp.name) / "cache.db", delay=0.0)
        f = self.fetcher
        f._warmed = True
        f._browser_first = False
        f._blocked_until = 0.0
        f._save_state = mock.Mock()   # leave the real state be
        f._personas = [gs._Persona(f"fp{i}", "UA", "en") for i in range(4)]
        f._persona_ix = 0
        self.session = mock.Mock()
        f._session = self.session
        f._build_session = mock.Mock(return_value=self.session)
        f._browser_usable = mock.Mock(return_value=True)
        f._browser_get = mock.Mock(
            return_value=gs._BrowserResponse(RESULTS, URL))
        self._answer(429, "https://scholar.google.com/sorry/")

    def tearDown(self):
        self.fetcher._db.close()
        self._tmp.cleanup()

    def _answer(self, *answers):
        """Each answer a (status, url) pair; a lone pair answers every time."""
        if len(answers) == 2 and isinstance(answers[0], int):
            answers = (answers,)
        responses = [SimpleNamespace(status_code=status, url=url,
                                     text=RESULTS,
                                     raise_for_status=lambda: None)
                     for status, url in answers]
        if len(responses) == 1:
            self.session.get.return_value = responses[0]
            self.session.get.side_effect = None
        else:
            self.session.get.side_effect = responses


class StraightToFirefoxTests(_FetcherFixture):
    def test_the_first_challenge_goes_straight_to_firefox(self):
        resp = self.fetcher._get(URL)
        self.assertEqual(self.session.get.call_count, 1)
        self.fetcher._browser_get.assert_called_once_with(URL)
        self.assertEqual(resp.text, RESULTS)

    def test_which_is_then_asked_first(self):
        self.fetcher._get(URL)
        self.assertTrue(self.fetcher._browser_first)

    def test_without_firefox_the_other_fingerprints_are_tried(self):
        self.fetcher._browser_usable.return_value = False
        self.fetcher._browser_get.return_value = None
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.assertEqual(self.session.get.call_count, 4)

    def test_and_one_that_passes_is_used(self):
        self.fetcher._browser_usable.return_value = False
        self._answer((429, "https://scholar.google.com/sorry/"),
                     (200, URL))
        resp = self.fetcher._get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.session.get.call_count, 2)
        self.fetcher._browser_get.assert_not_called()

    def test_a_hiccup_still_gets_its_retry(self):
        # A 503 is not a challenge: the same fingerprint asks again.
        self._answer((503, URL), (200, URL))
        with mock.patch.object(gs.time, "sleep"):
            resp = self.fetcher._get(URL)
        self.assertEqual(resp.status_code, 200)
        self.fetcher._browser_get.assert_not_called()

    def test_an_unanswered_captcha_is_given_up_with_a_cooldown(self):
        self.fetcher._browser_get.return_value = None
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.assertGreater(self.fetcher._blocked_until, time.monotonic())


class FirefoxFirstTests(_FetcherFixture):
    """Firefox already the working channel: it is asked before anything."""

    def setUp(self):
        super().setUp()
        self.fetcher._browser_first = True

    def test_the_page_comes_from_firefox(self):
        self.fetcher._get(URL)
        self.fetcher._browser_get.assert_called_once_with(URL, solve=True)
        self.session.get.assert_not_called()

    def test_an_unanswered_captcha_is_not_put_twice_for_one_page(self):
        self.fetcher._browser_get.return_value = None
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.assertEqual(self.fetcher._browser_get.call_count, 1)
        # Nor are the other fingerprints tried, with Firefox to hand.
        self.assertEqual(self.session.get.call_count, 1)

    def test_a_check_for_a_newer_version_puts_no_captcha_to_the_reader(self):
        self.fetcher._get(URL, once=True)
        self.fetcher._browser_get.assert_called_once_with(URL, solve=False)


# ---------------------------------------------------------------------------
# The browser: the window the reader answers the CAPTCHA in
# ---------------------------------------------------------------------------


class _WebDriverException(Exception):
    pass


class _TimeoutException(_WebDriverException):
    pass


CLOSED = object()   # the reader closed the window


class _FakeDriver:
    """A Firefox whose page moves on, a step at a time, as the reader works
    at the CAPTCHA — one step each time the app looks in on it."""

    def __init__(self, *states, routes=None, cookies=()):
        self.states = list(states)
        self.routes = dict(routes or {})
        self.cookies = list(cookies)
        self.got = []
        self.scripts = []
        self.added = []
        self.quit_called = False

    def _state(self):
        state = self.states[0]
        if state is CLOSED:
            raise _WebDriverException("Browsing context has been discarded")
        return state

    def get(self, url):
        self.got.append(url)
        if url in self.routes:
            self.states = [self.routes[url]]

    @property
    def current_url(self):
        return self._state()[0]

    @property
    def page_source(self):
        return self._state()[1]

    def execute_script(self, script, *args):
        self._state()
        self.scripts.append(args)
        if len(self.states) > 1:
            self.states.pop(0)

    def get_cookies(self):
        return list(self.cookies)

    def add_cookie(self, row):
        self.added.append(row)

    def quit(self):
        self.quit_called = True


class _BrowserFixture(unittest.TestCase):
    def setUp(self):
        exc = types.ModuleType("selenium.common.exceptions")
        exc.WebDriverException = _WebDriverException
        exc.TimeoutException = _TimeoutException
        modules = {"selenium": types.ModuleType("selenium"),
                   "selenium.common": types.ModuleType("selenium.common"),
                   "selenium.common.exceptions": exc}
        for patcher in (mock.patch.dict(sys.modules, modules),
                        mock.patch.object(sb.time, "sleep"),
                        mock.patch.object(sb, "on_step", None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.browser = sb.ScholarBrowser()
        self.addCleanup(self.browser.close)
        self.browser._await_content = lambda driver, wait_for: None
        self.drivers = []
        self.started = []    # True for a hidden Firefox, False for a window

        def ensure(headless=True):
            if self.browser._driver is None:
                self.browser._driver = self.drivers.pop(0)
                self.started.append(headless)
            return self.browser._driver

        self.browser._ensure_driver = ensure

    def _challenged_hidden(self):
        return _FakeDriver((URL, CHALLENGE))


class CaptchaWindowTests(_BrowserFixture):
    def setUp(self):
        super().setUp()
        self.hidden = self._challenged_hidden()
        self.window = _FakeDriver(
            (SORRY, CHALLENGE), (SORRY, CHALLENGE), (URL, RESULTS),
            cookies=[{"name": "GOOGLE_ABUSE_EXEMPTION", "value": "ok",
                      "domain": ".google.com", "path": "/"}])
        self.drivers = [self.hidden, self.window]

    def test_a_challenge_opens_a_window_on_the_page(self):
        self.browser.fetch(URL, solve=True)
        self.assertEqual(self.started, [True, False])
        self.assertEqual(self.window.got, [URL])

    def test_the_page_behind_the_captcha_is_returned_once_answered(self):
        html, final = self.browser.fetch(URL, solve=True)
        self.assertEqual((html, final), (RESULTS, URL))

    def test_the_hidden_firefox_makes_way_for_the_window(self):
        # A profile can be open in one Firefox at a time.
        self.browser.fetch(URL, solve=True)
        self.assertTrue(self.hidden.quit_called)

    def test_the_window_closes_by_itself(self):
        self.browser.fetch(URL, solve=True)
        self.assertTrue(self.window.quit_called)
        self.assertIsNone(self.browser._driver)

    def test_what_answering_earned_is_kept(self):
        self.browser.fetch(URL, solve=True)
        self.assertEqual([c["name"] for c in self.browser._earned],
                         ["GOOGLE_ABUSE_EXEMPTION"])

    def test_the_window_says_what_it_is_for(self):
        self.browser.fetch(URL, solve=True)
        self.assertIn((sb._SOLVE_NOTE,), self.window.scripts)

    def test_and_so_does_the_app(self):
        told = []
        with mock.patch.object(sb, "on_step", told.append):
            self.browser.fetch(URL, solve=True)
        self.assertEqual(len(told), 1)
        self.assertIn("Firefox window", told[0])

    def test_without_solve_no_window_opens(self):
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL)
        self.assertEqual(self.started, [True])

    def test_answered_but_left_elsewhere_the_page_is_asked_for_again(self):
        self.window.states = [(SORRY, CHALLENGE),
                              ("https://scholar.google.com/", HOME)]
        self.window.routes = {}

        def get(url, driver=self.window):
            driver.got.append(url)
            if len(driver.got) > 1:
                driver.states = [(URL, RESULTS)]

        self.window.get = get
        html, _final = self.browser.fetch(URL, solve=True)
        self.assertEqual(html, RESULTS)
        self.assertEqual(self.window.got, [URL, URL])


class UnansweredTests(_BrowserFixture):
    def setUp(self):
        super().setUp()
        self.window = _FakeDriver((SORRY, CHALLENGE), CLOSED)
        self.drivers = [self._challenged_hidden(), self.window]

    def test_closing_the_window_gives_up(self):
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)
        self.assertIsNone(self.browser._driver)
        self.assertEqual(self.browser._earned, [])

    def test_and_no_window_opens_again_for_a_while(self):
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)
        self.drivers = [self._challenged_hidden()]
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)
        self.assertEqual(self.started, [True, False, True])

    def test_but_one_does_after_that(self):
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)
        self.browser._solve_after = time.monotonic() - 1
        self.drivers = [self._challenged_hidden(),
                        _FakeDriver((SORRY, CHALLENGE), (URL, RESULTS))]
        html, _final = self.browser.fetch(URL, solve=True)
        self.assertEqual(html, RESULTS)
        self.assertEqual(self.started, [True, False, True, False])

    def test_a_window_left_unanswered_times_out(self):
        self.window.states = [(SORRY, CHALLENGE)]
        with mock.patch.object(sb, "_SOLVE_TIMEOUT", 0):
            with self.assertRaises(sb.BrowserBlocked):
                self.browser.fetch(URL, solve=True)
        self.assertTrue(self.window.quit_called)
        self.assertGreater(self.browser._solve_after, time.monotonic())

    def test_a_window_closed_while_it_loads_gives_up_too(self):
        def get(url):
            raise _WebDriverException("Browsing context has been discarded")

        self.window.get = get
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)
        self.assertTrue(self.window.quit_called)
        self.assertGreater(self.browser._solve_after, time.monotonic())

    def test_no_window_where_none_can_be_opened(self):
        # A Firefox that will not start with a window (no display) is a
        # challenge that stands, and is not tried again for a while either.
        def ensure(headless=True):
            if not headless:
                raise sb.BrowserUnavailable("no display")
            if self.browser._driver is None:
                self.browser._driver = self._challenged_hidden()
            return self.browser._driver

        self.browser._ensure_driver = ensure
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)
        self.assertGreater(self.browser._solve_after, time.monotonic())


class EarnedCookiesTests(unittest.TestCase):
    """What answering earned leads the cookies the hidden Firefox starts with."""

    def test_they_go_in_ahead_of_the_user_s_own(self):
        browser = sb.ScholarBrowser()
        self.addCleanup(browser.close)
        browser._earned = [{"name": "GOOGLE_ABUSE_EXEMPTION", "value": "new",
                            "domain": ".google.com", "path": "/"}]
        own = [{"name": "GOOGLE_ABUSE_EXEMPTION", "value": "old",
                "domain": ".google.com", "path": "/", "secure": True},
               {"name": "NID", "value": "n", "domain": ".google.com",
                "path": "/", "secure": True}]
        driver = _FakeDriver(("https://scholar.google.com/", HOME))
        with mock.patch.object(sb, "_google_cookie_rows", return_value=own):
            browser._seed_cookies(driver)
        self.assertEqual([(c["name"], c["value"]) for c in driver.added],
                         [("GOOGLE_ABUSE_EXEMPTION", "new"), ("NID", "n")])


class NoteTests(unittest.TestCase):
    def test_the_note_cannot_read_as_a_challenge(self):
        # It sits on the challenge page; were it to read as one, an answered
        # CAPTCHA that left it on the page would never be seen as answered.
        self.assertFalse(sb._challenged(URL, RESULTS + sb._SOLVE_NOTE))
        self.assertFalse(sb._challenged(URL, RESULTS + sb._NOTE_JS))

    def test_while_the_challenge_reads_as_one(self):
        self.assertTrue(sb._challenged(URL, CHALLENGE))
        self.assertTrue(sb._challenged(SORRY, ""))


if __name__ == "__main__":
    unittest.main()
