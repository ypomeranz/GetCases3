"""The browser the app drives for itself where Firefox isn't installed.

Google Scholar, once it flags this computer, answers only a real browser,
and CommonLII's English Reports scans sit behind a CloudFlare check that
admits people; both were done with Firefox, and without it the scans could
only be opened on the site and Scholar's pages not at all.  Now Chrome or
Edge (or Safari on a Mac) is driven instead, on a profile of the app's own:

  * **out of sight** — minimized, while it fetches; the app shows what it
    brings, and a scan is asked for from inside the page that links to it;
  * **to the front only for a person** — a check that passes by itself is
    waited out; one that wants a person brings the window up with a note,
    and it goes back down once answered, or after it went unanswered;
  * **Safari takes no answer while driven**, so there a check that wants a
    person is given up at once, said to be unanswerable.

Selenium need not be installed: its exceptions are stubbed, and the drivers
are fakes on a fake clock.
"""

import base64
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import app_browser as ab
import eng_rep_pdf
import scholar_browser as sb

URL = "https://www.commonlii.org/uk/cases/EngR/1854/296.html"
PDF_URL = URL[:-5] + ".pdf"
CHECK = ("<html><head><title>Just a moment...</title></head><body>"
         "<script>window._cf_chl_opt={};__cf_chl_opt=1</script></body></html>")
PAGE = ("<html><head><title>Hadley v Baxendale</title><script "
        "src=\"/cdn-cgi/challenge-platform/scripts/jsd/main.js\"></script>"
        "</head><body><a href=\"296.pdf\">PDF</a></body></html>")
CLOSED = object()   # the reader closed the window


class _WebDriverException(Exception):
    pass


class _TimeoutException(_WebDriverException):
    pass


class _Clock:
    """Time that passes only when the code under test sleeps — and the page
    moves on a step each time it does."""

    def __init__(self):
        self.now = 1000.0
        self.driver = None

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.driver is not None:
            self.driver.step()


class _FakeDriver:
    def __init__(self, *states, fetched=None):
        self.states = list(states)
        self.fetched = fetched
        self.got = []
        self.notes = []
        self.async_calls = []
        self.minimized = 0
        self.shown = 0
        self.quit_called = False

    def step(self):
        if len(self.states) > 1:
            self.states.pop(0)

    def _state(self):
        if self.states[0] is CLOSED:
            raise _WebDriverException("window closed")
        return self.states[0]

    def get(self, url):
        self.got.append(url)

    @property
    def current_url(self):
        return self._state()[0]

    @property
    def page_source(self):
        return self._state()[1]

    def execute_script(self, script, *args):
        self._state()
        if "readyState" in script:
            return "complete"
        if args:
            self.notes.append(args[0])
        return None

    def execute_async_script(self, script, *args):
        self.async_calls.append(args)
        return self.fetched

    def minimize_window(self):
        self.minimized += 1

    def set_window_rect(self, **_kw):
        self.shown += 1

    def maximize_window(self):
        self.shown += 1

    current_window_handle = "w1"

    @property
    def switch_to(self):
        return types.SimpleNamespace(window=lambda _h: None)

    def set_page_load_timeout(self, _s):
        pass

    def set_script_timeout(self, _s):
        pass

    def quit(self):
        self.quit_called = True


def _check_page(_url, source):
    return "<title>Just a moment" in source


class _Fixture(unittest.TestCase):
    def setUp(self):
        exc = types.ModuleType("selenium.common.exceptions")
        exc.WebDriverException = _WebDriverException
        exc.TimeoutException = _TimeoutException
        modules = {"selenium": types.ModuleType("selenium"),
                   "selenium.common": types.ModuleType("selenium.common"),
                   "selenium.common.exceptions": exc}
        self.clock = _Clock()
        for patcher in (mock.patch.dict(sys.modules, modules),
                        mock.patch.object(ab.time, "sleep", self.clock.sleep),
                        mock.patch.object(ab.time, "monotonic",
                                          self.clock.monotonic)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def browser(self, driver, key="chrome", name="Google Chrome"):
        self.clock.driver = driver
        browser = ab.AppBrowser(ab.Engine(key, name, "browser.exe"))
        browser._launch = lambda: driver
        return browser


class OutOfSightTests(_Fixture):
    def test_a_page_comes_back_from_a_minimized_window(self):
        driver = _FakeDriver((URL, PAGE))
        source, final = self.browser(driver).open(URL, challenged=_check_page)
        self.assertEqual((source, final), (PAGE, URL))
        self.assertEqual(driver.minimized, 1)
        self.assertEqual(driver.shown, 0)

    def test_a_check_that_passes_by_itself_is_waited_out_unseen(self):
        driver = _FakeDriver((URL, CHECK), (URL, CHECK), (URL, PAGE))
        source, _final = self.browser(driver).open(URL, challenged=_check_page)
        self.assertEqual(source, PAGE)
        self.assertEqual(driver.shown, 0)

    def test_a_file_is_asked_for_from_its_page(self):
        pdf = b"%PDF-1.4 the scan"
        driver = _FakeDriver((URL, PAGE), fetched={
            "status": 200, "mitigated": "",
            "body": base64.b64encode(pdf).decode()})
        status, body, mitigated = self.browser(driver).fetch_file(
            URL, PDF_URL, challenged=_check_page)
        self.assertEqual((status, body, mitigated), (200, pdf, ""))
        self.assertEqual(driver.got, [URL])
        self.assertEqual(driver.async_calls, [(PDF_URL,)])


class PersonTests(_Fixture):
    def _person_answers(self):
        # Long past the time a check is given to pass by itself.
        return _FakeDriver(*[(URL, CHECK)] * 80, (URL, PAGE))

    def test_the_window_comes_up_with_a_note_and_goes_back_down(self):
        driver = self._person_answers()
        told = []
        source, _final = self.browser(driver).open(
            URL, challenged=_check_page, note="GetCases: pass the check",
            waiting="Pass it in the window", on_step=told.append)
        self.assertEqual(source, PAGE)
        self.assertEqual(driver.shown, 1)
        self.assertEqual(driver.minimized, 2)     # at the start, and after
        self.assertIn("GetCases: pass the check", driver.notes)
        self.assertEqual(told, ["Pass it in the window"])

    def test_not_asked_for_no_window_comes_up(self):
        driver = self._person_answers()
        with self.assertRaises(ab.NeedsPerson) as caught:
            self.browser(driver).open(URL, challenged=_check_page, ask=False)
        self.assertTrue(caught.exception.answerable)
        self.assertEqual(driver.shown, 0)

    def test_unanswered_it_is_given_up_and_put_away(self):
        driver = _FakeDriver((URL, CHECK))
        with self.assertRaises(ab.NeedsPerson):
            self.browser(driver).open(URL, challenged=_check_page)
        self.assertEqual((driver.shown, driver.minimized), (1, 2))

    def test_a_window_closed_unanswered_gives_up(self):
        driver = _FakeDriver(*[(URL, CHECK)] * 40, CLOSED)
        browser = self.browser(driver)
        with self.assertRaises(ab.NeedsPerson):
            browser.open(URL, challenged=_check_page)
        self.assertTrue(driver.quit_called)

    def test_safari_takes_no_answer(self):
        driver = self._person_answers()
        with self.assertRaises(ab.NeedsPerson) as caught:
            self.browser(driver, "safari", "Safari").open(
                URL, challenged=_check_page)
        self.assertFalse(caught.exception.answerable)
        self.assertEqual(driver.shown, 0)


class WhichBrowserTests(unittest.TestCase):
    def test_chrome_then_edge_then_safari(self):
        files = {"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/"
                 "Microsoft Edge", "/usr/bin/safaridriver"}
        with mock.patch.object(ab.sys, "platform", "darwin"), \
                mock.patch.object(ab.os.path, "isfile",
                                  side_effect=lambda p: str(p) in files), \
                mock.patch.object(ab.os.path, "isdir", return_value=True):
            self.assertEqual([e.key for e in ab.engines()],
                             ["chrome", "edge", "safari"])

    def test_safari_only_on_a_mac(self):
        with mock.patch.object(ab.sys, "platform", "win32"), \
                mock.patch.object(ab.os.path, "isfile", return_value=False):
            self.assertEqual(ab.engines(), [])

    def test_the_first_that_starts_is_used_and_a_failure_remembered(self):
        chrome = ab.Engine("chrome", "Google Chrome", "c")
        edge = ab.Engine("edge", "Microsoft Edge", "e")
        launched = []

        def launch(browser):
            launched.append(browser.engine.key)
            raise ab.Unavailable(f"could not start {browser.name}")

        with mock.patch.object(ab, "_SHARED", None), \
                mock.patch.object(ab, "_FAILED", None), \
                mock.patch.object(ab, "_have_selenium", return_value=True), \
                mock.patch.object(ab, "engines", return_value=[chrome, edge]), \
                mock.patch.object(ab.AppBrowser, "start", launch):
            with self.assertRaises(ab.Unavailable):
                ab.shared()
            with self.assertRaises(ab.Unavailable):
                ab.shared()
        self.assertEqual(launched, ["chrome", "edge"])   # not tried twice


class _FakeAppBrowser:
    name = "Google Chrome"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def fetch_file(self, page_url, file_url, **kw):
        self.calls.append((page_url, file_url, kw))
        if self.error is not None:
            raise self.error
        return self.result

    def open(self, url, **kw):
        self.calls.append((url, kw))
        if self.error is not None:
            raise self.error
        return self.result


class EnglishReportsTests(unittest.TestCase):
    """A scan of the English Reports without Firefox."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for patcher in (
                mock.patch.object(eng_rep_pdf, "CACHE_DIR", Path(tmp.name)),
                mock.patch.object(eng_rep_pdf, "can_fetch",
                                  return_value=False),
                mock.patch.object(ab, "available", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fetch(self, browser):
        with mock.patch.object(ab, "shared", return_value=browser):
            return eng_rep_pdf.fetch_pdf(1854, 296, URL, on_step=print)

    def test_the_app_s_browser_fetches_it_from_the_case_s_page(self):
        browser = _FakeAppBrowser((200, b"%PDF-1.4 scan", ""))
        self.assertEqual(self.fetch(browser), b"%PDF-1.4 scan")
        page_url, file_url, kw = browser.calls[0]
        self.assertEqual((page_url, file_url), (URL, PDF_URL))
        self.assertIs(kw["on_step"], print)
        self.assertIn("Google Chrome window", kw["waiting"])
        # …and keeps it, as a scan fetched under Firefox's clearance is kept.
        self.assertEqual(eng_rep_pdf.get_cached(1854, 296), b"%PDF-1.4 scan")

    def test_an_unanswered_check_names_the_browser(self):
        browser = _FakeAppBrowser(error=ab.NeedsPerson("Safari", False))
        with self.assertRaises(eng_rep_pdf.CloudflareChallenge) as caught:
            self.fetch(browser)
        self.assertEqual(caught.exception.web_url, URL)
        self.assertEqual(caught.exception.browser, "Safari")
        self.assertFalse(caught.exception.answerable)

    def test_no_browser_to_drive_links_out_saying_why(self):
        browser = _FakeAppBrowser(error=ab.Unavailable(ab.SAFARI_SETUP))
        with self.assertRaises(eng_rep_pdf.FetchUnavailable) as caught:
            self.fetch(browser)
        self.assertEqual(caught.exception.reason, ab.SAFARI_SETUP)

    def test_a_refusal_is_the_origin_s(self):
        browser = _FakeAppBrowser((403, b"Forbidden", ""))
        with self.assertRaises(eng_rep_pdf.OriginError):
            self.fetch(browser)

    def test_with_firefox_its_clearance_is_still_used(self):
        browser = _FakeAppBrowser((200, b"%PDF-1.4 scan", ""))
        with mock.patch.object(eng_rep_pdf, "can_fetch", return_value=True), \
                mock.patch.object(eng_rep_pdf, "fetch_cleared",
                                  return_value=b"%PDF-1.4 ff") as cleared:
            self.assertEqual(self.fetch(browser), b"%PDF-1.4 ff")
        cleared.assert_called_once()
        self.assertEqual(browser.calls, [])

    def test_what_reads_as_cloudflare_s_check(self):
        self.assertTrue(eng_rep_pdf._page_is_check(URL, CHECK))
        # Every page CloudFlare serves carries its challenge-platform script.
        self.assertFalse(eng_rep_pdf._page_is_check(URL, PAGE))
        self.assertFalse(eng_rep_pdf._page_is_check(
            URL, PAGE + eng_rep_pdf._PERSON_NOTE))


class ScholarTests(unittest.TestCase):
    """Google Scholar's pages without Firefox."""

    def setUp(self):
        exc = types.ModuleType("selenium.common.exceptions")
        exc.WebDriverException = _WebDriverException
        exc.TimeoutException = _TimeoutException
        modules = {"selenium": types.ModuleType("selenium"),
                   "selenium.common": types.ModuleType("selenium.common"),
                   "selenium.common.exceptions": exc}
        for patcher in (mock.patch.dict(sys.modules, modules),
                        mock.patch.object(sb, "_firefox_binary",
                                          return_value=None),
                        mock.patch.object(sb, "on_step", None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_available_with_another_browser(self):
        with mock.patch.object(ab, "available", return_value=True):
            self.assertTrue(sb.available())
        with mock.patch.object(ab, "available", return_value=False):
            self.assertFalse(sb.available())

    def test_the_page_comes_from_the_app_s_browser(self):
        url = "https://scholar.google.com/scholar?q=%22347+U.S.+483%22"
        browser = _FakeAppBrowser(("<div id='gs_res_ccl'></div>", url))
        with mock.patch.object(ab, "shared", return_value=browser):
            got = sb.ScholarBrowser().fetch(url, solve=True)
        self.assertEqual(got, ("<div id='gs_res_ccl'></div>", url))
        (asked, kw), = browser.calls
        self.assertEqual(asked, url)
        self.assertTrue(kw["ask"])
        self.assertIs(kw["challenged"], sb._challenged)

    def test_an_unanswered_captcha_is_not_put_again_for_a_while(self):
        browser = _FakeAppBrowser(error=ab.NeedsPerson("Google Chrome"))
        scholar = sb.ScholarBrowser()
        with mock.patch.object(ab, "shared", return_value=browser):
            for _ in range(2):
                with self.assertRaises(sb.BrowserBlocked):
                    scholar.fetch("https://scholar.google.com/scholar?q=x",
                                  solve=True)
        self.assertEqual([kw["ask"] for _url, kw in browser.calls],
                         [True, False])

    def test_no_browser_is_unavailable(self):
        with mock.patch.object(ab, "shared",
                               side_effect=ab.Unavailable("none")):
            with self.assertRaises(sb.BrowserUnavailable):
                sb.ScholarBrowser().fetch("https://scholar.google.com/")


try:
    import tkinter as tk

    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class PanelTests(unittest.TestCase):
    """The window's panel when the check put in the app's browser stood."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def panel(self, browser, answerable):
        win = object.__new__(gui._EngRepPdfWindow)
        win._body = tk.Frame(self.root)
        win._status_var = tk.StringVar(self.root)
        win._reveal = lambda: None
        win._watch_for_clearance = mock.Mock()
        win._need_clearance(URL, "", browser, answerable)
        texts = []
        stack = list(win._body.winfo_children())
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            try:
                texts.append(widget.cget("text"))
            except tk.TclError:
                pass
        return win, texts

    def test_retry_brings_the_check_up_again(self):
        win, texts = self.panel("Google Chrome", True)
        self.assertIn("Retry", texts)
        self.assertIn("Open in browser instead", texts)
        self.assertNotIn("Open in Firefox", texts)
        self.assertTrue(any("Google Chrome window" in t for t in texts))
        win._watch_for_clearance.assert_not_called()

    def test_safari_says_it_takes_no_answer(self):
        _win, texts = self.panel("Safari", False)
        self.assertTrue(any("Safari can't take an answer" in t
                            for t in texts))


if __name__ == "__main__":
    unittest.main()
