"""Google Scholar uses an available browser from the first request.

Two rules:

  * **An available browser goes first.** The extension and Selenium Firefox
    handle Scholar directly. The app's own HTTP session is for installations
    without either browser route.
  * **A challenge to Firefox is put to the reader.**  The page opens again in
    a separate, ordinary Firefox window. Once results appear, the reader closes
    that window; its cleared cookies are imported before retrying. Selenium
    never attaches to the CAPTCHA window.

Selenium need not be installed: its two exceptions are stubbed for each test,
and the drivers are fakes.
"""

import sys
import json
import sqlite3
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from email.utils import formatdate
from urllib.parse import urlencode

import google_scholar as gs
import scholar_browser as sb
from google_scholar import GoogleScholarFetcher, ScholarError


URL = "https://scholar.google.com/scholar?q=%22347+U.S.+483%22&as_sdt=4"
SORRY = "https://www.google.com/sorry/index?continue=" + URL
CHALLENGE = ("<html><body>Our systems have detected unusual traffic from "
             "your computer network.<div class=\"g-recaptcha\"></div>"
             "</body></html>")
RESULTS = ("<html><body><div id=\"gs_res_ccl\"><div class=\"gs_r\">"
           '<h3 class="gs_rt"><a href="/scholar_case?case=123">'
           "Brown v. Board of Education</a></h3></div></div></body></html>")
HOME = "<html><body><div id=\"gs_hdr\">Google Scholar</div></body></html>"


def _cleared_url(url=URL, expires=None, domain="google.com"):
    if expires is None:
        expires = time.time() + 3600
    assignment = ("GOOGLE_ABUSE_EXEMPTION=ID=synthetic:TM=123:C=r:S=example; "
                  f"path=/; domain={domain}; expires={formatdate(expires, usegmt=True)}")
    return url + "&" + urlencode({"google_abuse": assignment, "sgst": "handoff"})


def _session_state(url, *, closed=False):
    return {"_closedWindows" if closed else "windows": [
        {"tabs": [{"entries": [{"url": SORRY}, {"url": url}], "index": 2,
                   "lastAccessed": 10}]}], "cookies": []}


def _write_session(path, state):
    import lz4.block

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mozLz40\0" + lz4.block.compress(json.dumps(state).encode("utf-8")))


# ---------------------------------------------------------------------------
# The fetcher: which channel a challenged page goes to
# ---------------------------------------------------------------------------


class _FetcherFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        for patcher in (mock.patch.object(GoogleScholarFetcher, "_load_state", return_value={}),
                        mock.patch.object(gs, "_browser_google_cookies", return_value={})):
            patcher.start()
            self.addCleanup(patcher.stop)
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
        # No Chrome or Edge with the extension, unless a test says so.
        f._extension_usable = mock.Mock(return_value=False)
        f._extension_get = mock.Mock(return_value=None)
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


class DirectHTTPTests(_FetcherFixture):
    def setUp(self):
        super().setUp()
        self.fetcher._browser_usable.return_value = False
        self.fetcher._browser_get.return_value = None

    def test_unrecognized_http_200_search_pages_are_not_returned(self):
        pages = ["<html>We cannot process automated queries right now.</html>",
                 "<html><h1>Access denied</h1></html>",
                 '<html><div id="gs_res_ccl"></div></html>', CHALLENGE, ""]
        for page in pages:
            with self.subTest(page=page):
                self.fetcher._blocked_until = 0.0
                self._answer(200, URL)
                self.session.get.return_value.text = page
                with self.assertRaises(ScholarError):
                    self.fetcher._get(URL)

    def test_a_genuine_empty_search_does_not_open_a_browser(self):
        self._answer(200, URL)
        empty = '<div id="gs_res_ccl">Your search did not match any articles.</div>'
        self.session.get.return_value.text = empty
        self.assertEqual(self.fetcher._get(URL).text, empty)
        self.fetcher._browser_get.assert_not_called()

    def test_an_incomplete_cached_copy_check_does_not_prompt_for_a_captcha(self):
        self._answer(200, URL)
        self.session.get.return_value.text = '<div id="gs_res_ccl"></div>'
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL, once=True)
        self.fetcher._browser_get.assert_not_called()
        self.fetcher._extension_get.assert_not_called()

    def test_a_later_block_is_detected_after_successful_searches(self):
        self._answer(200, URL)
        for words in ("first search", "second search", "third search"):
            self.assertEqual(len(self.fetcher.search_cases(words, allow_db_fallback=False)), 1)
        self.session.get.return_value.text = '<html><h1>Access denied</h1></html>'
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.assertGreater(self.fetcher._blocked_until, time.monotonic())

    def test_other_fingerprints_are_tried_before_cooling_down(self):
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.assertEqual(self.session.get.call_count, 4)
        self.assertGreater(self.fetcher._blocked_until, time.monotonic())
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.assertEqual(self.session.get.call_count, 4)

    def test_a_fingerprint_that_passes_is_used(self):
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

class FirefoxFirstTests(_FetcherFixture):
    def test_the_first_page_comes_from_firefox_without_http_warmup(self):
        self.fetcher._warmed = False
        self.assertEqual(self.fetcher._get(URL).text, RESULTS)
        self.fetcher._browser_get.assert_called_once_with(URL, solve=True)
        self.session.get.assert_not_called()

    def test_later_searches_still_use_firefox(self):
        for words in ("first search", "second search", "third search"):
            self.assertEqual(len(self.fetcher.search_cases(words, allow_db_fallback=False)), 1)
        self.assertEqual(self.fetcher._browser_get.call_count, 3)
        self.session.get.assert_not_called()

    def test_an_unanswered_captcha_cools_down_without_using_http(self):
        self.fetcher._browser_get.return_value = None
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.fetcher._browser_get.assert_called_once_with(URL, solve=True)
        self.assertGreater(self.fetcher._blocked_until, time.monotonic())
        self.session.get.assert_not_called()

    def test_losing_firefox_after_a_success_does_not_enable_http(self):
        self.fetcher._get(URL)
        self.fetcher._browser_usable.return_value = False
        self.fetcher._browser_get.return_value = None
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.session.get.assert_not_called()

    def test_a_check_for_a_newer_version_puts_no_captcha_to_the_reader(self):
        self.fetcher._get(URL, once=True)
        self.fetcher._browser_get.assert_called_once_with(URL, solve=False)
        self.session.get.assert_not_called()

    def test_a_block_page_from_firefox_is_not_returned_as_an_empty_search(self):
        self.fetcher._browser_get.return_value = gs._BrowserResponse(
            '<html><div id="gs_res_ccl"></div></html>', URL)
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.fetcher._browser_get.assert_called_once_with(URL, solve=True)
        self.session.get.assert_not_called()

    def test_recovery_clears_the_cooldown_for_a_following_cached_copy_check(self):
        self.fetcher._blocked_until = time.monotonic() + 60
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL, once=True)
        self.fetcher._browser_get.assert_not_called()
        self.fetcher._get(URL)
        self.assertEqual(self.fetcher._blocked_until, 0.0)
        self.fetcher._get(URL, once=True)
        self.assertEqual(self.fetcher._browser_get.call_args_list, [
            mock.call(URL, solve=True), mock.call(URL, solve=False)])
        self.session.get.assert_not_called()


# ---------------------------------------------------------------------------
# Chrome or Edge, where the GetCases extension is: where the CAPTCHA goes
# ---------------------------------------------------------------------------


class ExtensionBrowserTests(_FetcherFixture):
    """With the extension in Chrome or Edge, a CAPTCHA is put to the reader
    there rather than in a Firefox window."""

    def setUp(self):
        super().setUp()
        f = self.fetcher
        f._extension_usable.return_value = True
        f._extension_get.return_value = gs._BrowserResponse(RESULTS, URL)
        f._browser_get.return_value = None     # the hidden Firefox challenged

    def test_the_extension_goes_first_without_firefox_or_http_warmup(self):
        self.fetcher._warmed = False
        self.fetcher._browser_get.return_value = gs._BrowserResponse(RESULTS, URL)
        resp = self.fetcher._get(URL)
        self.fetcher._browser_get.assert_not_called()
        self.fetcher._extension_get.assert_called_once_with(URL)
        self.session.get.assert_not_called()
        self.assertEqual(resp.text, RESULTS)

    def test_the_extension_goes_first_when_firefox_is_unavailable(self):
        self.fetcher._browser_usable.return_value = False
        self.assertEqual(self.fetcher._get(URL).text, RESULTS)
        self.fetcher._extension_get.assert_called_once_with(URL)
        self.session.get.assert_not_called()

    def test_an_unanswered_extension_cools_down_without_using_http(self):
        self.fetcher._extension_get.return_value = None
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.assertGreater(self.fetcher._blocked_until, time.monotonic())
        self.session.get.assert_not_called()

    def test_a_failed_extension_does_not_enable_http_after_disconnect(self):
        self.fetcher._browser_usable.return_value = False
        self.fetcher._extension_get.return_value = None
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.fetcher._extension_usable.return_value = False
        self.fetcher._blocked_until = 0.0
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL)
        self.fetcher._extension_get.assert_called_once_with(URL)
        self.session.get.assert_not_called()

    def test_an_extension_only_cached_copy_check_skips_network(self):
        self.fetcher._browser_usable.return_value = False
        with self.assertRaises(ScholarError):
            self.fetcher._get(URL, once=True)
        self.fetcher._extension_get.assert_not_called()
        self.fetcher._browser_get.assert_not_called()
        self.session.get.assert_not_called()

    def test_a_cached_copy_check_uses_silent_firefox_with_both_available(self):
        self.fetcher._browser_get.return_value = gs._BrowserResponse(RESULTS, URL)
        self.assertEqual(self.fetcher._get(URL, once=True).text, RESULTS)
        self.fetcher._extension_get.assert_not_called()
        self.fetcher._browser_get.assert_called_once_with(URL, solve=False)
        self.session.get.assert_not_called()

    def test_a_failed_extension_falls_through_to_firefox(self):
        self.fetcher._extension_get.return_value = None
        self.fetcher._browser_get.return_value = gs._BrowserResponse(RESULTS, URL)
        self.assertEqual(self.fetcher._get(URL).text, RESULTS)
        self.fetcher._extension_get.assert_called_once_with(URL)
        self.fetcher._browser_get.assert_called_once_with(URL, solve=True)
        self.session.get.assert_not_called()

    def test_an_extension_error_falls_through_to_firefox(self):
        self.fetcher._extension_get.side_effect = OSError("bridge disconnected")
        self.fetcher._browser_get.return_value = gs._BrowserResponse(RESULTS, URL)
        self.assertEqual(self.fetcher._get(URL).text, RESULTS)
        self.fetcher._browser_get.assert_called_once_with(URL, solve=True)
        self.session.get.assert_not_called()

    def test_a_cooling_down_extension_does_not_skip_firefox(self):
        self.fetcher._blocked_until = time.monotonic() + 60
        self.fetcher._extension_get.return_value = None
        self.fetcher._browser_get.return_value = gs._BrowserResponse(RESULTS, URL)
        self.assertEqual(self.fetcher._get(URL).text, RESULTS)
        self.fetcher._browser_get.assert_called_once_with(URL, solve=True)
        self.assertEqual(self.fetcher._blocked_until, 0.0)

    def test_a_later_extension_block_falls_through_to_firefox(self):
        for words in ("first search", "second search", "third search"):
            self.assertEqual(len(self.fetcher.search_cases(words, allow_db_fallback=False)), 1)
        self.assertEqual(self.fetcher._extension_get.call_count, 3)
        self.fetcher._browser_get.assert_not_called()
        self.fetcher._extension_get.return_value = gs._BrowserResponse(
            '<div id="gs_res_ccl"></div>', URL)
        self.fetcher._browser_get.return_value = gs._BrowserResponse(RESULTS, URL)
        found = self.fetcher.search_cases("later search", allow_db_fallback=False)
        self.assertEqual(len(found), 1)
        self.assertEqual(self.fetcher._extension_get.call_count, 4)
        self.fetcher._browser_get.assert_called_once()
        self.session.get.assert_not_called()


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

    def find_elements(self, by, selector):
        return gs.BeautifulSoup(self.page_source, "html.parser").select(selector)

    def add_cookie(self, row):
        self.added.append(row)

    def quit(self):
        self.quit_called = True


class _BrowserFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        profile_patch = mock.patch.object(sb, "_PROFILE_DIR", Path(self._tmp.name))
        profile_patch.start()
        self.addCleanup(profile_patch.stop)
        exc = types.ModuleType("selenium.common.exceptions")
        exc.WebDriverException = _WebDriverException
        exc.TimeoutException = _TimeoutException
        by = types.ModuleType("selenium.webdriver.common.by")
        by.By = SimpleNamespace(CSS_SELECTOR="css selector")
        modules = {"selenium": types.ModuleType("selenium"),
                   "selenium.common": types.ModuleType("selenium.common"),
                   "selenium.common.exceptions": exc,
                   "selenium.webdriver.common.by": by}
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


class _NativeBrowserFixture(_BrowserFixture):
    def setUp(self):
        super().setUp()
        self.cookie = {"name": "GOOGLE_ABUSE_EXEMPTION", "value": "cleared",
                       "domain": ".google.com", "path": "/"}
        self.hidden = self._challenged_hidden()
        self.resumed = _FakeDriver((URL, RESULTS), cookies=[self.cookie])
        self.drivers = [self.hidden, self.resumed]
        self.process = mock.Mock()
        self.process.poll.return_value = 0
        self.launch = mock.Mock(return_value=self.process)
        for patcher in (mock.patch.object(sb, "_firefox_binary", return_value="firefox.exe"),
                        mock.patch.object(sb.subprocess, "Popen", self.launch),
                        mock.patch.object(sb, "_google_cookie_rows", return_value=[self.cookie])):
            patcher.start()
            self.addCleanup(patcher.stop)


class CaptchaWindowTests(_NativeBrowserFixture):
    def test_firefox_escalates_a_later_unrecognized_block_to_the_manual_window(self):
        self.hidden.states = [(URL, RESULTS)]
        self.assertEqual(self.browser.fetch(URL, solve=True), (RESULTS, URL))
        self.launch.assert_not_called()
        self.hidden.states = [(URL, '<html><div id="gs_res_ccl"></div></html>')]
        self.assertFalse(self.browser._content_ready(self.hidden, "#gs_res_ccl, .gs_r"))
        self.assertEqual(self.browser.fetch(URL, solve=True), (RESULTS, URL))
        self.launch.assert_called_once()

    def test_a_cookie_only_in_the_results_url_is_used_after_the_window_closes(self):
        cleared = _cleared_url()

        def launch(*args, **kwargs):
            _write_session(sb._PROFILE_DIR / "manual" / "sessionstore.jsonlz4",
                           _session_state(cleared))
            return self.process

        self.launch.side_effect = launch
        self.resumed.routes[cleared] = (cleared, RESULTS)
        self.resumed.cookies = []  # Google did not persist its exemption.
        self.assertEqual(self.browser.fetch(URL, solve=True), (RESULTS, cleared))
        self.assertEqual(self.resumed.got, [cleared])
        self.assertEqual(self.browser._earned[0]["value"], "ID=synthetic:TM=123:C=r:S=example")
        self.assertIn("GOOGLE_ABUSE_EXEMPTION", [c["name"] for c in sb.ScholarBrowser._load_cookies()])

    def test_an_already_solved_url_is_recovered_after_an_app_restart(self):
        cleared = _cleared_url()
        _write_session(sb._PROFILE_DIR / "manual" / "sessionstore.jsonlz4",
                       _session_state(cleared, closed=True))
        ready = _FakeDriver((cleared, RESULTS), cookies=[])
        self.drivers = [ready]
        self.assertEqual(self.browser.fetch(URL, solve=True), (RESULTS, cleared))
        self.assertEqual(ready.got, [cleared])
        self.assertEqual(self.started, [True])
        self.launch.assert_not_called()
        self.assertEqual(self.browser._earned[0]["value"], "ID=synthetic:TM=123:C=r:S=example")

    def test_the_captcha_window_is_never_controlled_by_selenium(self):
        self.browser.fetch(URL, solve=True)
        self.assertEqual(self.started, [True, True])
        args = self.launch.call_args.args[0]
        self.assertEqual(args[0], "firefox.exe")
        self.assertIn("-no-remote", args)
        self.assertEqual(args[args.index("-profile") + 1],
                         str(sb._PROFILE_DIR / "manual"))
        self.assertEqual(args[-1], URL)
        self.assertNotIn("-marionette", args)
        self.assertNotIn("-headless", args)
        if sb.os.name == "nt":
            self.assertIn("-wait-for-browser", args)

    def test_the_selenium_profile_is_not_used_for_the_manual_window(self):
        self.browser.fetch(URL, solve=True)
        preferences = (sb._PROFILE_DIR / "manual" / "user.js").read_text()
        self.assertNotIn("webdriver", preferences)
        self.assertNotIn("marionette", preferences)
        self.assertNotIn("remote.", preferences)

    def test_automation_environment_is_not_inherited(self):
        with mock.patch.dict(sb.os.environ, {"MOZ_AUTOMATION": "1", "MOZ_HEADLESS": "1",
                                             "MOZ_MARIONETTE": "1", "MOZ_HEADLESS_WIDTH": "800"}):
            self.browser.fetch(URL, solve=True)
        env = self.launch.call_args.kwargs["env"]
        for key in ("MOZ_AUTOMATION", "MOZ_HEADLESS", "MOZ_MARIONETTE", "MOZ_HEADLESS_WIDTH"):
            self.assertNotIn(key, env)

    def test_no_automated_retry_while_the_user_is_answering(self):
        count = 0

        def poll():
            nonlocal count
            count += 1
            self.assertEqual(self.started, [True])
            self.assertTrue(self.hidden.quit_called)
            return None if count < 3 else 0

        self.process.poll.side_effect = poll
        self.browser.fetch(URL, solve=True)
        self.assertEqual(self.resumed.got, [URL])
        self.assertEqual(self.started, [True, True])

    def test_the_requested_page_is_returned_after_a_manual_solve(self):
        self.assertEqual(self.browser.fetch(URL, solve=True), (RESULTS, URL))

    def test_cleared_cookies_replace_the_old_automation_cookie(self):
        self.hidden.cookies = [dict(self.cookie, value="old")]
        original_ensure = self.browser._ensure_driver

        def ensure(headless=True):
            if self.started:
                self.assertEqual(self.browser._earned, [self.cookie])
            return original_ensure(headless)

        self.browser._ensure_driver = ensure
        self.browser.fetch(URL, solve=True)
        self.assertEqual(self.browser._earned, [self.cookie])

    def test_session_state_survives_a_new_browser_instance(self):
        self.browser.fetch(URL, solve=True)
        restarted = sb.ScholarBrowser()
        self.addCleanup(restarted.close)
        self.assertEqual(restarted._earned, [self.cookie])

    def test_subsequent_fetch_reuses_the_cleared_session(self):
        self.browser.fetch(URL, solve=True)
        self.assertEqual(self.browser.fetch(URL, solve=True), (RESULTS, URL))
        self.assertEqual(self.started, [True, True])
        self.launch.assert_called_once()

    def test_the_app_explains_the_manual_handoff(self):
        told = []
        with mock.patch.object(sb, "on_step", told.append):
            self.browser.fetch(URL, solve=True)
        self.assertEqual(len(told), 1)
        self.assertIn("ordinary Firefox window", told[0])
        self.assertIn("close that window", told[0])

    def test_a_navigation_timeout_can_still_return_loaded_results(self):
        def get(url):
            raise _TimeoutException()

        self.resumed.get = get
        self.assertEqual(self.browser.fetch(URL, solve=True), (RESULTS, URL))

    def test_no_results_is_still_a_successful_solve(self):
        empty = "<html><body>Your search did not match any articles.</body></html>"
        self.resumed.states = [(URL, empty)]
        self.assertEqual(self.browser.fetch(URL, solve=True), (empty, URL))

    def test_a_blank_retry_is_not_a_successful_solve(self):
        self.resumed.states = [(URL, "<html></html>")]
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)

    def test_without_solve_no_manual_window_opens(self):
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL)
        self.launch.assert_not_called()
        self.assertEqual(self.started, [True])


class UnansweredTests(_NativeBrowserFixture):
    def setUp(self):
        super().setUp()
        self.resumed.states = [(SORRY, CHALLENGE)]

    def test_an_unanswered_window_gives_up_and_snoozes(self):
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)
        self.assertIsNone(self.browser._driver)
        self.assertGreater(self.browser._solve_after, time.monotonic())
        self.drivers = [self._challenged_hidden()]
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)
        self.launch.assert_called_once()

    def test_a_new_window_can_open_after_the_snooze(self):
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)
        self.browser._solve_after = time.monotonic() - 1
        self.resumed.states = [(URL, RESULTS)]
        self.drivers = [self._challenged_hidden(), self.resumed]
        self.assertEqual(self.browser.fetch(URL, solve=True), (RESULTS, URL))
        self.assertEqual(self.launch.call_count, 2)

    def test_timeout_leaves_the_manual_window_with_the_user(self):
        self.process.poll.return_value = None
        with mock.patch.object(sb, "_SOLVE_TIMEOUT", 0):
            with self.assertRaises(sb.BrowserBlocked):
                self.browser.fetch(URL, solve=True)
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()
        self.assertEqual(self.started, [True])
        self.assertGreater(self.browser._solve_after, time.monotonic())

    def test_an_open_timed_out_window_is_not_reopened_or_automated(self):
        self.process.poll.return_value = None
        with mock.patch.object(sb, "_SOLVE_TIMEOUT", 0):
            with self.assertRaises(sb.BrowserBlocked):
                self.browser.fetch(URL, solve=True)
        self.browser._solve_after = 0
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)
        self.launch.assert_called_once()
        self.assertEqual(self.started, [True])

    def test_a_late_solve_is_imported_when_the_user_closes_the_window(self):
        self.process.poll.return_value = None
        with mock.patch.object(sb, "_SOLVE_TIMEOUT", 0):
            with self.assertRaises(sb.BrowserBlocked):
                self.browser.fetch(URL, solve=True)
        self.process.poll.return_value = 0
        self.resumed.states = [(URL, RESULTS)]
        self.assertEqual(self.browser.fetch(URL, solve=True), (RESULTS, URL))
        self.assertEqual(self.browser._earned, [self.cookie])
        self.launch.assert_called_once()

    def test_no_window_where_firefox_cannot_be_opened(self):
        self.launch.side_effect = OSError("could not launch Firefox")
        with self.assertRaises(sb.BrowserBlocked):
            self.browser.fetch(URL, solve=True)
        self.assertGreater(self.browser._solve_after, time.monotonic())
        self.assertEqual(self.started, [True])


class EarnedCookiesTests(unittest.TestCase):
    """What answering earned leads the cookies the hidden Firefox starts with."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(sb, "_PROFILE_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_the_persistent_profile_also_beats_an_older_borrowed_cookie(self):
        browser = sb.ScholarBrowser()
        self.addCleanup(browser.close)
        fresh = {"name": "GOOGLE_ABUSE_EXEMPTION", "value": "new",
                 "domain": ".google.com", "path": "/"}
        stale = dict(fresh, value="old")
        driver = _FakeDriver((URL, RESULTS), cookies=[fresh])
        with mock.patch.object(sb, "_google_cookie_rows", return_value=[stale]):
            browser._seed_cookies(driver)
        self.assertEqual(driver.added, [fresh])

    def test_cookie_scope_expiry_and_flags_are_preserved(self):
        browser = sb.ScholarBrowser()
        self.addCleanup(browser.close)
        live = {"name": "GOOGLE_ABUSE_EXEMPTION", "value": "new",
                "domain": "scholar.google.com", "path": "/scholar",
                "expiry": int(time.time()) + 3600, "secure": True,
                "httpOnly": True, "sameSite": "Lax"}
        other_path = dict(live, path="/", value="other-path")
        driver = _FakeDriver((URL, RESULTS), cookies=[
            live, dict(live, value="old"), other_path,
            dict(live, name="expired", expiry=1),
            dict(live, domain="notgoogle.com"),
        ])
        browser._remember_cookies(driver)
        self.assertEqual(browser._earned, [live, other_path])
        self.assertEqual(sb.ScholarBrowser._load_cookies(), [live, other_path])

    def test_a_corrupt_cookie_file_does_not_disable_firefox(self):
        (sb._PROFILE_DIR / "scholar_cookies.json").write_text("{bad json")
        browser = sb.ScholarBrowser()
        self.addCleanup(browser.close)
        self.assertEqual(browser._earned, [])

    def test_a_redirected_homepage_still_allows_scholar_scoped_cookies(self):
        browser = sb.ScholarBrowser()
        self.addCleanup(browser.close)
        cookie = {"name": "GOOGLE_ABUSE_EXEMPTION", "value": "new",
                  "domain": "scholar.google.com", "path": "/"}
        browser._earned = [cookie]
        robots = "https://scholar.google.com/robots.txt"
        driver = _FakeDriver((SORRY, CHALLENGE), routes={robots: (robots, "User-agent: *")})
        with mock.patch.object(sb, "_google_cookie_rows", return_value=[]):
            browser._seed_cookies(driver)
        self.assertEqual(driver.got, ["https://scholar.google.com/", robots])
        self.assertEqual(driver.added, [cookie])

    def test_expired_persisted_cookies_are_not_replayed(self):
        (sb._PROFILE_DIR / "scholar_cookies.json").write_text(json.dumps([
            {"name": "GOOGLE_ABUSE_EXEMPTION", "value": "old",
             "domain": ".google.com", "path": "/", "expiry": 1}]))
        self.assertEqual(sb.ScholarBrowser._load_cookies(), [])

    def test_manual_clearance_is_read_from_the_sqlite_wal(self):
        browser = sb.ScholarBrowser()
        self.addCleanup(browser.close)
        profile = browser._prepare_manual_profile()
        db = profile / "cookies.sqlite"
        con = sqlite3.connect(db)
        self.addCleanup(con.close)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT, "
                    "path TEXT, isSecure INTEGER, expiry INTEGER, updateTime INTEGER)")
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.executemany("INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("GOOGLE_ABUSE_EXEMPTION", "old-partition", ".google.com", "/", 1,
             int(time.time() + 3600) * 1000, 1),
            ("GOOGLE_ABUSE_EXEMPTION", "fresh", ".google.com", "/", 1,
             int(time.time() + 3600) * 1000, 2),
            ("expired", "expired", ".google.com", "/", 1, 1, 3),
            ("unrelated", "not-google", "notgoogle.com", "/", 1,
             int(time.time() + 3600) * 1000, 4),
        ])
        con.commit()  # Keep this connection open, like a running Firefox.
        self.assertTrue(Path(str(db) + "-wal").is_file())
        browser._earned = [{"name": "GOOGLE_ABUSE_EXEMPTION", "value": "stale",
                            "domain": ".google.com", "path": "/"}]
        browser._import_manual_cookies()
        self.assertEqual(len(browser._earned), 1)
        self.assertEqual(browser._earned[0]["value"], "fresh")
        self.assertEqual(browser._earned[0]["domain"], ".google.com")
        self.assertTrue(browser._earned[0]["secure"])
        self.assertLess(browser._earned[0]["expiry"], time.time() + 3700)
        self.assertEqual(sb.ScholarBrowser._load_cookies(), browser._earned)


class SessionClearanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_mozilla_lz4_session_preserves_the_url_and_cookie_assignment(self):
        cleared = _cleared_url()
        state = _session_state(cleared)
        _write_session(self.root / "sessionstore.jsonlz4", state)
        loaded = sb._read_firefox_session(self.root)
        rows, resume = sb._session_clearance(loaded, URL)
        self.assertEqual(resume, cleared)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["domain"], ".google.com")
        self.assertEqual(rows[0]["path"], "/")
        self.assertEqual(rows[0]["value"], "ID=synthetic:TM=123:C=r:S=example")
        self.assertTrue(rows[0]["secure"])
        self.assertGreater(rows[0]["expiry"], time.time())

    def test_expired_clearance_is_never_replayed(self):
        self.assertEqual(sb._session_clearance(_session_state(_cleared_url(expires=1)), URL),
                         ([], ""))

    def test_other_cookie_assignments_are_not_taken(self):
        url = URL + "&" + urlencode({"google_abuse": "SID=unrelated; domain=google.com"})
        self.assertEqual(sb._session_clearance(_session_state(url), URL), ([], ""))

    def test_clearance_cannot_change_the_requested_search(self):
        cleared = _cleared_url(URL.replace("as_sdt=4", "as_sdt=2006"))
        rows, resume = sb._session_clearance(_session_state(cleared), URL)
        self.assertEqual(len(rows), 1)  # Still a usable Google cookie.
        self.assertEqual(resume, "")

    def test_offsite_domains_and_insecure_urls_are_rejected(self):
        for url in (_cleared_url(domain="evil.example"),
                    _cleared_url().replace("https://", "http://"),
                    _cleared_url().replace("scholar.google.com", "notgoogle.com"),
                    _cleared_url().replace("scholar.google.com", "scholar.google.com:444")):
            with self.subTest(url=url.split("&google_abuse")[0]):
                self.assertEqual(sb._session_clearance(_session_state(url), URL), ([], ""))

    def test_corrupt_latest_session_can_fall_back_to_a_valid_recovery_file(self):
        (self.root / "sessionstore.jsonlz4").write_bytes(b"mozLz40\0\x20\x00\x00\x00broken")
        state = _session_state(_cleared_url())
        backup = self.root / "sessionstore-backups" / "recovery.jsonlz4"
        _write_session(backup, state)
        sb.os.utime(self.root / "sessionstore.jsonlz4", (200, 200))
        sb.os.utime(backup, (100, 100))
        self.assertEqual(sb._read_firefox_session(self.root), state)

    def test_a_current_empty_session_does_not_reuse_an_old_solved_session(self):
        current = self.root / "sessionstore.jsonlz4"
        backup = self.root / "sessionstore-backups" / "previous.jsonlz4"
        _write_session(current, {"windows": []})
        _write_session(backup, _session_state(_cleared_url()))
        sb.os.utime(current, (200, 200))
        sb.os.utime(backup, (100, 100))
        self.assertEqual(sb._read_firefox_session(self.root), {"windows": []})

    def test_oversized_lz4_output_is_rejected_before_decompression(self):
        (self.root / "sessionstore.jsonlz4").write_bytes(
            b"mozLz40\0" + (sb._MAX_SESSION_BYTES + 1).to_bytes(4, "little"))
        self.assertEqual(sb._read_firefox_session(self.root), {})


class FirefoxBinaryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = mock.patch.object(sb, "_store_binary", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_store_launcher_is_resolved_to_the_actual_browser(self):
        import eng_rep_pdf

        alias = self.root / "firefox.exe"
        alias.touch()
        package = self.root / "Mozilla.Firefox_159_x64"
        real = package / "VFS" / "ProgramFiles" / "Firefox Package Root" / "firefox.exe"
        real.parent.mkdir(parents=True)
        real.write_bytes(b"firefox")
        reply = SimpleNamespace(returncode=0, stdout=str(package) + "\n")
        with mock.patch.object(eng_rep_pdf, "_firefox_exe", return_value=str(alias)), \
                mock.patch.object(sb.subprocess, "run", return_value=reply) as run, \
                mock.patch.object(sb.os, "name", "nt"):
            self.assertEqual(sb._firefox_binary(), str(real))
            self.assertEqual(sb._firefox_binary(), str(real))
        self.assertEqual(run.call_count, 1)

    def test_a_normal_firefox_binary_needs_no_package_query(self):
        import eng_rep_pdf

        real = self.root / "firefox.exe"
        real.write_bytes(b"firefox")
        with mock.patch.object(eng_rep_pdf, "_firefox_exe", return_value=str(real)), \
                mock.patch.object(sb.subprocess, "run") as run:
            self.assertEqual(sb._firefox_binary(), str(real))
        run.assert_not_called()

    def test_an_unresolvable_store_launcher_is_unavailable(self):
        with mock.patch.object(sb.subprocess, "run", side_effect=OSError()):
            self.assertIsNone(sb._store_firefox_binary())


class ChallengeDetectionTests(unittest.TestCase):
    def test_results_are_not_a_challenge(self):
        self.assertFalse(sb._challenged(URL, RESULTS))

    def test_the_challenge_reads_as_one(self):
        self.assertTrue(sb._challenged(URL, CHALLENGE))
        self.assertTrue(sb._challenged(SORRY, ""))


if __name__ == "__main__":
    unittest.main()
