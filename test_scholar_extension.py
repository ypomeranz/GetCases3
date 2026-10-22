"""Google Scholar's CAPTCHA, answered in Chrome or Edge with the GetCases
extension (``scholar_extension``).

When Google challenges the app's requests to Scholar, and the hidden Firefox
too or there is none, the page opens in the reader's own Chrome or Edge,
where the extension is.  Once it loads past Google's check, the extension
hands the app the page and Google's cookies.  These tests hold the app's
half of that:

* only Scholar's cookies are taken, and of those only Google's anti-abuse
  and preference cookies, never the ones that sign the reader in;
* the page goes to the fetch waiting for it, and only once it is past the
  CAPTCHA; the tab the app opened then closes;
* the fetch opens the page on the extension's profile, waits, and, left
  unanswered, does not open another for a while;
* the app's own requests then go out as that browser.
"""

import threading
import time
import unittest
from unittest import mock

import eng_rep_pdf
import google_scholar as gs
import scholar_browser
import scholar_extension as se

URL = "https://scholar.google.com/scholar?hl=en&as_sdt=4&q=%22347+U.S.+483%22"
RESULTS = ("<html><body><div id=\"gs_res_ccl\"><div class=\"gs_r\">"
           "Brown v. Board of Education</div></div></body></html>")
SCHOLARS_CAPTCHA = ("<html><body>Please show you're not a robot"
                    "<div class=\"g-recaptcha\"></div></body></html>")
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
CHROME = eng_rep_pdf.ExtensionBrowser("Chrome", r"C:\chrome.exe", "Default",
                                      1.0)


def handed_over(url=URL, **cookies):
    """What the extension posts as a Scholar page loads in Chrome."""
    jar = {"GOOGLE_ABUSE_EXEMPTION": "ID=1:TM=2:C=r:IP=1.2.3.4-:S=x",
           "NID": "nid", "SID": "signed-in", "__Secure-3PSID": "signed-in"}
    jar.update(cookies)
    return {"url": url, "browser": "Chrome",
            "cookies": [{"name": k, "value": v, "expires": 0}
                        for k, v in jar.items() if v is not None],
            "headers": {"user-agent": CHROME_UA,
                        "sec-ch-ua": '"Google Chrome";v="155"',
                        "sec-ch-ua-platform": '"Windows"',
                        "accept-language": "en-US,en;q=0.9",
                        "cookie": "SID=signed-in"}}


class _Fresh(unittest.TestCase):
    """Nothing handed over yet, no fetch waiting, and the extension's browser
    there with the app listening for it."""

    def setUp(self):
        for name, value in (("_identity", None), ("_waiting", {}),
                            ("_open_after", 0.0), ("on_answered", None),
                            ("bridge", mock.Mock(bound=True))):
            p = mock.patch.object(se, name, value)
            p.start()
            self.addCleanup(p.stop)
        for p in (mock.patch.object(eng_rep_pdf, "extension_browsers",
                                    return_value=[CHROME]),
                  mock.patch.object(scholar_browser, "on_step", None)):
            p.start()
            self.addCleanup(p.stop)


class AcceptTests(_Fresh):

    def test_google_s_anti_abuse_cookies_and_the_browser_s_headers(self):
        self.assertEqual(se.accept_clearance(handed_over()),
                         {"ok": True, "page": False})
        ident = se.identity()
        self.assertEqual(ident.cookies, {
            "GOOGLE_ABUSE_EXEMPTION": "ID=1:TM=2:C=r:IP=1.2.3.4-:S=x",
            "NID": "nid"})
        self.assertEqual(ident.user_agent, CHROME_UA)
        self.assertEqual(ident.headers["sec-ch-ua-platform"], '"Windows"')
        self.assertEqual(ident.browser, "Chrome")

    def test_never_those_that_sign_the_reader_in(self):
        se.accept_clearance(handed_over())
        ident = se.identity()
        self.assertNotIn("SID", ident.cookies)
        self.assertNotIn("__Secure-3PSID", ident.cookies)
        self.assertNotIn("cookie", ident.headers)

    def test_only_scholar_s(self):
        for url in ("https://www.google.com/search?q=x",
                    "https://scholar.google.com.evil.example/",
                    "https://www.commonlii.org/uk/cases/EngR/1854/296.html"):
            with self.subTest(url=url):
                self.assertFalse(se.handles(url))
                self.assertEqual(se.accept_clearance(handed_over(url))["ok"],
                                 False)
        self.assertIsNone(se.identity())

    def test_none_of_google_s_cookies_keeps_the_last_identity(self):
        se.accept_clearance(handed_over())
        reply = se.accept_clearance(handed_over(
            GOOGLE_ABUSE_EXEMPTION=None, NID=None))
        self.assertFalse(reply["ok"])
        self.assertEqual(se.identity().cookies["NID"], "nid")

    def test_it_says_when_the_app_is_waiting_for_the_page(self):
        se._waiting[se.page_key(URL)] = se._Wait(URL)
        self.assertTrue(se.accept_clearance(handed_over())["page"])
        # Google's way back can drop or reorder what doesn't name the page.
        back = "https://scholar.google.com/scholar?q=%22347+U.S.+483%22&as_sdt=4"
        self.assertTrue(se.accept_clearance(handed_over(back))["page"])
        other = "https://scholar.google.com/scholar?q=other&as_sdt=4"
        self.assertFalse(se.accept_clearance(handed_over(other))["page"])


class DeliverTests(_Fresh):

    def setUp(self):
        super().setUp()
        self.wait = se._waiting[se.page_key(URL)] = se._Wait(URL)

    def test_the_page_goes_to_the_fetch_waiting_for_it(self):
        answered = mock.Mock()
        with mock.patch.object(se, "on_answered", answered):
            reply = se.deliver_page({"url": URL, "html": RESULTS})
        self.assertEqual(reply, {"ok": True, "close": True})
        self.assertTrue(self.wait.done.is_set())
        self.assertEqual((self.wait.html, self.wait.final_url), (RESULTS, URL))
        answered.assert_called_once_with()   # the app comes forward

    def test_not_while_it_is_still_scholar_s_captcha(self):
        self.assertEqual(se.deliver_page({"url": URL,
                                          "html": SCHOLARS_CAPTCHA}),
                         {"ok": False})
        self.assertFalse(self.wait.done.is_set())

    def test_nor_a_page_nobody_is_waiting_for(self):
        other = "https://scholar.google.com/scholar?q=other"
        self.assertEqual(se.deliver_page({"url": other, "html": RESULTS}),
                         {"ok": False})


class FetchTests(_Fresh):

    def _deliver_when_opened(self, html=RESULTS, url=URL):
        """The reader answers in the browser: the page is handed over as
        soon as it has opened."""
        def opened(page, browser):
            threading.Timer(0.05, se.deliver_page,
                            [{"url": url, "html": html}]).start()
            return True
        return mock.patch.object(eng_rep_pdf, "open_in_extension_browser",
                                 side_effect=opened)

    def test_the_page_opens_in_the_browser_and_comes_back(self):
        with self._deliver_when_opened() as opened:
            self.assertEqual(se.fetch(URL), (RESULTS, URL))
        opened.assert_called_once_with(URL, CHROME)
        self.assertEqual(se._waiting, {})

    def test_the_app_says_where_to_answer(self):
        told = []
        with self._deliver_when_opened(), \
                mock.patch.object(scholar_browser, "on_step", told.append):
            se.fetch(URL)
        self.assertEqual(len(told), 1)
        self.assertIn("opened in Chrome", told[0])

    def test_unanswered_it_is_not_opened_again_for_a_while(self):
        with mock.patch.object(eng_rep_pdf, "open_in_extension_browser",
                               return_value=True) as opened, \
                mock.patch.object(scholar_browser, "_SOLVE_TIMEOUT", 0.05):
            self.assertIsNone(se.fetch(URL))
            self.assertIsNone(se.fetch(URL))
        self.assertEqual(opened.call_count, 1)
        self.assertGreater(se._open_after, time.monotonic())

    def test_one_tab_for_one_page_however_many_wait_for_it(self):
        se._waiting[se.page_key(URL)] = wait = se._Wait(URL)
        threading.Timer(0.05, se.deliver_page,
                        [{"url": URL, "html": RESULTS}]).start()
        with mock.patch.object(eng_rep_pdf,
                               "open_in_extension_browser") as opened:
            self.assertEqual(se.fetch(URL), (RESULTS, URL))
        opened.assert_not_called()
        self.assertIs(se._waiting[se.page_key(URL)], wait)  # still its own

    def test_not_while_the_app_isnt_listening_for_the_extension(self):
        se.bridge = None
        self.assertFalse(se.available())
        with mock.patch.object(eng_rep_pdf,
                               "open_in_extension_browser") as opened:
            self.assertIsNone(se.fetch(URL))
        opened.assert_not_called()

    def test_nor_without_the_extension_in_a_browser(self):
        eng_rep_pdf.extension_browsers.return_value = []
        self.assertFalse(se.available())


class PersonaTests(_Fresh):
    """After the browser answers, the app's own requests go out as it."""

    def setUp(self):
        super().setUp()
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fetcher = gs.GoogleScholarFetcher(
            cache_path=Path(self._tmp.name) / "cache.db", delay=0.0)
        self.addCleanup(self.fetcher._db.close)
        self.fetcher._save_state = mock.Mock()
        self.fetcher._stored_cookies = {}     # not this machine's own

    @unittest.skipIf(gs._curl_requests is None, "needs curl_cffi")
    def test_the_browser_s_identity_leads(self):
        se.accept_clearance(handed_over())
        with mock.patch.object(gs, "_browser_google_cookies",
                               return_value={}):
            self.fetcher._adopt_browser_identity()
        p = self.fetcher._persona()
        self.assertTrue(p.target.startswith("chrome"), p.target)
        self.assertEqual(p.ua, CHROME_UA)
        session = self.fetcher._session
        self.assertEqual(session.headers["User-Agent"], CHROME_UA)
        self.assertEqual(session.headers["sec-ch-ua-platform"], '"Windows"')
        self.assertEqual(session.headers["Accept-Language"], "en-US,en;q=0.9")
        self.assertEqual(session.cookies.get("GOOGLE_ABUSE_EXEMPTION"),
                         "ID=1:TM=2:C=r:IP=1.2.3.4-:S=x")
        self.assertIsNone(session.cookies.get("SID"))

    @unittest.skipIf(gs._curl_requests is None, "needs curl_cffi")
    def test_a_newer_identity_replaces_the_older(self):
        se.accept_clearance(handed_over())
        self.fetcher._adopt_browser_identity()
        se.accept_clearance(handed_over(NID="newer"))
        self.fetcher._adopt_browser_identity()
        from_browser = [p for p in self.fetcher._personas if p.cookies]
        self.assertEqual(len(from_browser), 1)
        self.assertIn(("NID", "newer"), from_browser[0].cookies)

    def test_the_hidden_firefox_starts_with_its_cookies(self):
        se.accept_clearance(handed_over())
        self.fetcher._browser = mock.Mock(_earned=[])
        self.fetcher._adopt_browser_identity()
        if gs._curl_requests is not None:
            self.assertEqual(
                sorted(c["name"] for c in self.fetcher._browser._earned),
                ["GOOGLE_ABUSE_EXEMPTION", "NID"])

    def test_the_page_the_browser_handed_over_is_the_response(self):
        with mock.patch.object(se, "fetch", return_value=(RESULTS, URL)), \
                mock.patch.object(self.fetcher,
                                  "_adopt_browser_identity") as adopt:
            resp = self.fetcher._extension_get(URL)
        self.assertEqual((resp.text, resp.url, resp.status_code),
                         (RESULTS, URL, 200))
        adopt.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
