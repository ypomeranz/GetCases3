"""CommonLII's CloudFlare clearance from Chrome or Edge, through the GetCases
browser extension.

The app reads Firefox's clearance out of its cookie store; Chrome's and
Edge's stores are encrypted, so there the extension hands the clearance over
instead.  When a CommonLII page loads past the check in the browser, the
extension posts the site's cookies, and the headers the browser sent with
them, to the app's loopback port (``browser_bridge``'s ``/clearance``), and
``eng_rep_pdf`` fetches the scans with them.  These tests hold the app's half
of that:

* what the extension hands over is taken only for CommonLII, only with a
  ``cf_clearance`` and the User-Agent it went out under, and is kept across
  a restart;
* it goes out as the browser sent it -- its User-Agent and client hints,
  under Chrome's TLS fingerprint -- and, refused, is let go for good;
* the app knows which browsers have the extension, from their profiles, and
  opens the page there on that profile;
* the bridge takes ``/clearance`` from the extension alone, in shape.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import browser_bridge
import eng_rep_pdf
from test_browser_extension import EXTENSION, _Bridge
from test_firefox_cookies import DAY, _Reply

WEB = "https://www.commonlii.org/uk/cases/EngR/1854/296.html"
PDF = "https://www.commonlii.org/uk/cases/EngR/1854/296.pdf"
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
CHROME_CH = '"Chromium";v="155", "Not=A?Brand";v="24", "Google Chrome";v="155"'


def handed_over(value="chrome-pass", *, url=WEB, browser="Chrome",
                expires=None, **headers):
    """What the extension posts when a CommonLII page loads in Chrome."""
    sent = {"user-agent": CHROME_UA, "sec-ch-ua": CHROME_CH,
            "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
            "accept-language": "en-US,en;q=0.9"}
    sent.update(headers)
    return {
        "url": url, "browser": browser,
        "cookies": [
            {"name": "cf_clearance", "value": value,
             "expires": time.time() + 365 * DAY if expires is None else expires},
            {"name": "__cf_bm", "value": f"bm-{value}", "expires": 0},
        ],
        "headers": {k: v for k, v in sent.items() if v is not None},
    }


class _Isolated(unittest.TestCase):
    """The extension's clearances kept in a folder of the test's own, with
    nothing held and nothing refused yet."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="extclear_test_"))
        for name, value in (("CACHE_DIR", self.tmp / "engr_cache"),
                            ("_BROWSER_CLEARANCES", None),
                            ("_BROWSER_REFUSED", set()),
                            ("_LAST_GOOD", None)):
            p = mock.patch.object(eng_rep_pdf, name, value)
            p.start()
            self.addCleanup(p.stop)

    def restart(self):
        """The app quits and starts again: nothing held in memory."""
        eng_rep_pdf._BROWSER_CLEARANCES = None


class AcceptTests(_Isolated):

    def test_a_commonlii_clearance_is_taken_with_its_browser_s_headers(self):
        self.assertTrue(eng_rep_pdf.accept_browser_clearance(handed_over()))
        (held,) = eng_rep_pdf._browser_clearances()
        self.assertEqual(held.value, "chrome-pass")
        self.assertEqual(held.cookies, {"cf_clearance": "chrome-pass",
                                        "__cf_bm": "bm-chrome-pass"})
        self.assertEqual(held.user_agent, CHROME_UA)
        self.assertEqual(held.headers["sec-ch-ua"], CHROME_CH)
        self.assertEqual(held.headers["sec-ch-ua-platform"], '"Windows"')
        self.assertNotIn("user-agent", held.headers)   # it is .user_agent
        self.assertEqual(held.browser, "Chrome")
        self.assertIsNone(held.db)
        self.assertEqual(held.origin, "Chrome (the GetCases extension)")

    def test_the_bare_domain_too(self):
        self.assertTrue(eng_rep_pdf.accept_browser_clearance(
            handed_over(url="https://commonlii.org/uk/cases/EngR/")))

    def test_only_for_commonlii(self):
        for url in ("https://www.example.org/", "https://commonlii.org.evil.example/",
                    "https://notcommonlii.org/", "not a url", ""):
            with self.subTest(url=url):
                self.assertFalse(eng_rep_pdf.accept_browser_clearance(
                    handed_over(url=url)))
        self.assertEqual(eng_rep_pdf._browser_clearances(), [])

    def test_not_without_a_clearance_or_the_agent_it_went_out_under(self):
        no_clearance = handed_over()
        no_clearance["cookies"] = no_clearance["cookies"][1:]
        self.assertFalse(eng_rep_pdf.accept_browser_clearance(no_clearance))
        self.assertFalse(eng_rep_pdf.accept_browser_clearance(
            handed_over(**{"user-agent": None})))
        self.assertEqual(eng_rep_pdf._browser_clearances(), [])

    def test_only_the_headers_that_say_who_the_browser_is(self):
        eng_rep_pdf.accept_browser_clearance(handed_over(
            cookie="cf_clearance=x", referer="https://elsewhere.example/"))
        (held,) = eng_rep_pdf._browser_clearances()
        self.assertEqual(sorted(held.headers), [
            "accept-language", "sec-ch-ua", "sec-ch-ua-mobile",
            "sec-ch-ua-platform"])

    def test_the_newest_replaces_the_one_before(self):
        eng_rep_pdf.accept_browser_clearance(handed_over("monday"))
        eng_rep_pdf.accept_browser_clearance(handed_over("tuesday",
                                                         browser="Edge"))
        (held,) = eng_rep_pdf._browser_clearances()
        self.assertEqual((held.value, held.browser), ("tuesday", "Edge"))

    def test_it_is_kept_across_a_restart(self):
        eng_rep_pdf.accept_browser_clearance(handed_over())
        self.assertTrue((self.tmp / "browser_clearances.json").is_file())
        self.restart()
        (held,) = eng_rep_pdf._browser_clearances()
        self.assertEqual((held.value, held.user_agent, held.headers["sec-ch-ua"]),
                         ("chrome-pass", CHROME_UA, CHROME_CH))

    def test_an_expired_one_is_not(self):
        eng_rep_pdf.accept_browser_clearance(
            handed_over(expires=time.time() - 60))
        self.assertEqual(eng_rep_pdf._browser_clearances(), [])

    def test_a_damaged_store_is_no_store(self):
        (self.tmp / "browser_clearances.json").write_text(
            '{"commonlii": {"cookies": 5}}', encoding="utf-8")
        self.assertEqual(eng_rep_pdf._browser_clearances(), [])

    def test_the_panel_waiting_on_a_check_sees_it_arrive(self):
        with mock.patch.object(eng_rep_pdf, "firefox_available",
                               return_value=False):
            before = eng_rep_pdf.clearance_mark()
            eng_rep_pdf.accept_browser_clearance(handed_over())
            after = eng_rep_pdf.clearance_mark()
            # The extension sends it again with every CommonLII page the
            # reader opens: no news.
            eng_rep_pdf.accept_browser_clearance(handed_over())
            again = eng_rep_pdf.clearance_mark()
        self.assertEqual(before, ())
        self.assertNotEqual(after, before)
        self.assertEqual(again, after)


class FetchTests(_Isolated):
    """The clearance from Chrome goes out as Chrome sent it."""

    def setUp(self):
        super().setUp()
        self.requests = []          # (url, headers, cookies, impersonate)
        self.replies = {}           # cf_clearance value -> reply
        for name, kwargs in (("can_fetch", {"return_value": True}),
                             ("firefox_available", {"return_value": True}),
                             ("_firefox_clearances", {"return_value": []}),
                             ("_get", {"side_effect": self._answer})):
            p = mock.patch.object(eng_rep_pdf, name, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def _answer(self, url, headers, cookies, impersonate=""):
        self.requests.append((url, headers, cookies, impersonate))
        return self.replies[cookies["cf_clearance"]]

    def test_it_goes_out_under_the_browser_s_own_headers(self):
        eng_rep_pdf.accept_browser_clearance(handed_over())
        self.replies = {"chrome-pass": _Reply.PDF}
        self.assertEqual(eng_rep_pdf.fetch_pdf(1854, 296, WEB), _Reply.PDF[1])
        url, headers, cookies, impersonate = self.requests[0]
        self.assertEqual(url, PDF)
        self.assertEqual(cookies, {"cf_clearance": "chrome-pass",
                                   "__cf_bm": "bm-chrome-pass"})
        self.assertEqual(headers["User-Agent"], CHROME_UA)
        # curl_cffi's own client hints say Chrome 146 on macOS.
        self.assertEqual(headers["sec-ch-ua"], CHROME_CH)
        self.assertEqual(headers["sec-ch-ua-platform"], '"Windows"')
        self.assertEqual(headers["accept-language"], "en-US,en;q=0.9")
        # As a click on the case's page asks for its scan.
        self.assertEqual(headers["Referer"], WEB)
        self.assertEqual(headers["Sec-Fetch-Site"], "same-origin")
        self.assertTrue(impersonate.startswith("chrome"), impersonate)

    def test_firefox_s_still_go_out_as_firefox(self):
        firefox = eng_rep_pdf.Clearance(
            self.tmp / "ff" / "cookies.sqlite", {"cf_clearance": "ff-pass"},
            time.time(), time.time() + DAY, eng_rep_pdf._user_agent_for("158"))
        self.replies = {"ff-pass": _Reply.PDF}
        with mock.patch.object(eng_rep_pdf, "_firefox_clearances",
                               return_value=[firefox]):
            eng_rep_pdf.fetch_pdf(1854, 296, WEB)
        _url, headers, _cookies, impersonate = self.requests[0]
        self.assertIn("Firefox/158.0", headers["User-Agent"])
        self.assertNotIn("sec-ch-ua", headers)
        self.assertTrue(impersonate.startswith("firefox"), impersonate)

    def test_the_one_just_handed_over_is_tried_before_an_older_firefox_one(self):
        firefox = eng_rep_pdf.Clearance(
            self.tmp / "ff" / "cookies.sqlite", {"cf_clearance": "ff-pass"},
            time.time() - 7 * DAY, time.time() + DAY,
            eng_rep_pdf._user_agent_for("158"))
        eng_rep_pdf.accept_browser_clearance(handed_over())
        self.replies = {"chrome-pass": _Reply.PDF, "ff-pass": _Reply.PDF}
        with mock.patch.object(eng_rep_pdf, "_firefox_clearances",
                               return_value=[firefox]):
            eng_rep_pdf.fetch_pdf(1854, 296, WEB)
        self.assertEqual([r[2]["cf_clearance"] for r in self.requests],
                         ["chrome-pass"])

    def test_refused_it_is_let_go_and_not_taken_back(self):
        eng_rep_pdf.accept_browser_clearance(handed_over())
        self.replies = {"chrome-pass": _Reply.CHALLENGE}
        with self.assertRaises(eng_rep_pdf.CloudflareChallenge) as cm:
            eng_rep_pdf.fetch_pdf(1854, 296, WEB)
        self.assertEqual(cm.exception.refused_by, "Chrome")
        self.assertEqual(cm.exception.refused_ua, CHROME_UA)
        self.assertEqual(eng_rep_pdf._browser_clearances(), [])
        # The reader opens another CommonLII page in Chrome; the extension
        # sends the same dead clearance.
        self.assertFalse(eng_rep_pdf.accept_browser_clearance(handed_over()))
        self.assertEqual(eng_rep_pdf._browser_clearances(), [])
        # A new one, from passing the check again, is news.
        self.assertTrue(eng_rep_pdf.accept_browser_clearance(
            handed_over("chrome-pass-2")))
        self.restart()
        self.assertEqual([c.value for c in eng_rep_pdf._browser_clearances()],
                         ["chrome-pass-2"])

    def test_without_firefox_its_stores_are_not_searched(self):
        with mock.patch.object(eng_rep_pdf, "firefox_available",
                               return_value=False):
            with self.assertRaises(eng_rep_pdf.CloudflareChallenge):
                eng_rep_pdf.fetch_pdf(1854, 296, WEB)
        eng_rep_pdf._firefox_clearances.assert_not_called()


class CanFetchTests(_Isolated):
    CHROME = eng_rep_pdf.ExtensionBrowser("Chrome", "chrome.exe", "Default", 1.0)

    def _can_fetch(self, *, firefox=False, extension=(), deps=True):
        with mock.patch.object(eng_rep_pdf, "deps_present", return_value=deps), \
                mock.patch.object(eng_rep_pdf, "firefox_available",
                                  return_value=firefox), \
                mock.patch.object(eng_rep_pdf, "extension_browsers",
                                  return_value=list(extension)):
            return eng_rep_pdf.can_fetch()

    def test_firefox_or_the_extension_will_do(self):
        self.assertTrue(self._can_fetch(firefox=True))
        self.assertTrue(self._can_fetch(extension=[self.CHROME]))
        self.assertFalse(self._can_fetch())

    def test_so_will_a_clearance_already_handed_over(self):
        eng_rep_pdf.accept_browser_clearance(handed_over())
        self.assertTrue(self._can_fetch())

    def test_not_without_curl_cffi(self):
        self.assertFalse(self._can_fetch(firefox=True, extension=[self.CHROME],
                                         deps=False))


class DiscoveryTests(unittest.TestCase):
    """Which Chrome and Edge profiles the extension is in, from their own
    preferences."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="extfind_test_"))
        self.exe = self.tmp / "chrome.exe"
        self.exe.write_bytes(b"")
        self.data = self.tmp / "User Data"
        browsers = [eng_rep_pdf._Chromium("Chrome", self.data, [str(self.exe)])]
        for p in (mock.patch.object(eng_rep_pdf, "_chromium_browsers",
                                    return_value=browsers),
                  mock.patch.object(eng_rep_pdf, "_EXTENSION_BROWSERS", None)):
            p.start()
            self.addCleanup(p.stop)

    def profile(self, name, extensions, *, prefs="Secure Preferences",
                used=None):
        folder = self.data / name
        folder.mkdir(parents=True)
        (folder / "Preferences").write_text("{}", encoding="utf-8")
        (folder / prefs).write_text(json.dumps(
            {"extensions": {"settings": extensions}}), encoding="utf-8")
        if used is not None:
            import os
            os.utime(folder / "Preferences", (used, used))
        return folder

    def found(self):
        eng_rep_pdf._EXTENSION_BROWSERS = None
        return [(b.name, b.profile) for b in eng_rep_pdf.extension_browsers()]

    OTHER = {"path": "C:\\somewhere\\another_extension", "location": 4}

    def test_loaded_unpacked_from_the_getcases_folder(self):
        self.profile("Default", {"abc": {"path": str(EXTENSION), "location": 4,
                                         "disable_reasons": []},
                                 "xyz": self.OTHER})
        self.assertEqual(self.found(), [("Chrome", "Default")])

    def test_not_when_turned_off(self):
        self.profile("Default", {"abc": {"path": str(EXTENSION),
                                         "disable_reasons": [1]}})
        self.assertEqual(self.found(), [])
        eng_rep_pdf._EXTENSION_BROWSERS = None
        self.profile("Profile 1", {"abc": {"path": str(EXTENSION), "state": 0}},
                     prefs="Preferences")
        self.assertEqual(self.found(), [])

    def test_not_where_it_isnt(self):
        self.profile("Default", {"xyz": self.OTHER})
        self.assertEqual(self.found(), [])

    def test_installed_packed_under_the_profile(self):
        folder = self.profile("Default", {"abcdef": {"path": "abcdef/1.3.0_0",
                                                     "location": 1}})
        packed = folder / "Extensions" / "abcdef" / "1.3.0_0"
        packed.mkdir(parents=True)
        (packed / "manifest.json").write_text(
            (EXTENSION / "manifest.json").read_text(encoding="utf-8"),
            encoding="utf-8")
        self.assertEqual(self.found(), [("Chrome", "Default")])

    def test_the_profile_used_last_first(self):
        ours = {"abc": {"path": str(EXTENSION)}}
        self.profile("Default", ours, used=1_000_000)
        self.profile("Profile 3", ours, used=2_000_000)
        self.assertEqual(self.found(), [("Chrome", "Profile 3"),
                                        ("Chrome", "Default")])

    def test_not_without_the_browser_itself(self):
        self.profile("Default", {"abc": {"path": str(EXTENSION)}})
        self.exe.unlink()
        self.assertEqual(self.found(), [])

    def test_in_edge_too(self):
        edge_exe = self.tmp / "msedge.exe"
        edge_exe.write_bytes(b"")
        edge_data = self.tmp / "Edge User Data"
        eng_rep_pdf._chromium_browsers.return_value = [
            eng_rep_pdf._Chromium("Chrome", self.data, [str(self.exe)]),
            eng_rep_pdf._Chromium("Edge", edge_data, [str(edge_exe)])]
        self.profile("Default", {"xyz": self.OTHER})          # Chrome's
        self.data = edge_data
        self.profile("Profile 1", {"abc": {"path": str(EXTENSION)}})
        self.assertEqual(self.found(), [("Edge", "Profile 1")])
        (edge,) = eng_rep_pdf.extension_browsers()
        self.assertEqual(edge.exe, str(edge_exe))

    def test_the_page_opens_on_the_extension_s_profile(self):
        browser = eng_rep_pdf.ExtensionBrowser("Edge", r"C:\msedge.exe",
                                               "Profile 2", 0.0)
        with mock.patch.object(eng_rep_pdf.subprocess, "Popen") as popen:
            self.assertTrue(eng_rep_pdf.open_in_extension_browser(WEB, browser))
        popen.assert_called_once_with(
            [r"C:\msedge.exe", "--profile-directory=Profile 2", WEB])

    def test_the_extension_s_name_is_its_manifest_s(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual(manifest["name"], eng_rep_pdf.EXTENSION_NAME)


class BridgeClearanceTests(unittest.TestCase):
    """/clearance: from the extension alone, in shape."""

    def setUp(self):
        self.taken = []
        self.b = _Bridge()
        self.b.bridge._on_clearance = lambda data: self.taken.append(data) or True
        self.addCleanup(self.b.close)

    def test_the_extension_s_clearance_is_passed_on(self):
        body = handed_over()
        status, reply, _ = self.b.request("/clearance", body,
                                          headers={"Origin": "chrome-extension://x"})
        self.assertEqual((status, reply), (200, {"ok": True}))
        (data,) = self.taken
        self.assertEqual(data["url"], WEB)
        self.assertEqual(data["browser"], "Chrome")
        self.assertEqual([c["name"] for c in data["cookies"]],
                         ["cf_clearance", "__cf_bm"])
        self.assertEqual(data["headers"]["user-agent"], CHROME_UA)

    def test_what_the_app_didn_t_take_is_said(self):
        self.b.bridge._on_clearance = lambda data: False
        self.assertEqual(self.b.request("/clearance", handed_over())[:2],
                         (200, {"ok": False}))

    def test_a_web_page_cannot_hand_one_over(self):
        for headers in ({"Origin": "https://www.commonlii.org"},
                        {"X-GetCases": ""}):
            with self.subTest(headers=headers):
                self.assertEqual(self.b.request(
                    "/clearance", handed_over(), headers=headers)[0], 403)
        self.assertEqual(self.taken, [])

    def test_no_cookies_is_still_a_clearance_to_hand_on(self):
        # Scholar's page, from a browser holding none of Google's cookies.
        status, reply, _ = self.b.request("/clearance",
                                          {**handed_over(), "cookies": []})
        self.assertEqual((status, reply), (200, {"ok": True}))
        self.assertEqual(self.taken[0]["cookies"], [])

    def test_a_reply_of_the_app_s_own_is_passed_back(self):
        self.b.bridge._on_clearance = lambda data: {"ok": True, "page": True}
        self.assertEqual(self.b.request("/clearance", handed_over())[:2],
                         (200, {"ok": True, "page": True}))

    def test_out_of_shape_is_refused(self):
        good = handed_over()
        for bad in ({**good, "url": 5},
                    {**good, "cookies": [{"name": "a"}]},
                    {**good, "cookies": "cf_clearance=x"},
                    {**good, "headers": {"user-agent": 5}},
                    {**good, "cookies": [{"name": "a", "value": "b"}] * 51},
                    {**good, "cookies": [{"name": "a", "value": "b" * 9000}]}):
            with self.subTest(bad=str(bad)[:80]):
                self.assertEqual(self.b.request("/clearance", bad)[0], 400)
        self.assertEqual(self.taken, [])

    def test_a_bridge_without_anyone_to_take_it_has_no_such_door(self):
        bare = browser_bridge.BrowserBridge(mock.Mock(), port=0)
        bare.start()
        self.addCleanup(bare.close)
        self.assertTrue(bare.wait_bound(10))
        other = _Bridge.__new__(_Bridge)
        other.bridge = bare
        self.assertEqual(other.request("/clearance", handed_over())[0], 404)
        self.assertEqual(other.request("/page", {"url": WEB,
                                                 "html": "<p>"})[0], 404)

    def test_a_page_is_passed_on_and_the_app_s_reply_back(self):
        pages = []
        self.b.bridge._on_page = lambda data: pages.append(data) or {
            "ok": True, "close": True}
        page = {"url": "https://scholar.google.com/scholar?q=x",
                "html": "<html>results</html>"}
        self.assertEqual(self.b.request("/page", page)[:2],
                         (200, {"ok": True, "close": True}))
        self.assertEqual(pages, [page])

    def test_a_page_out_of_shape_or_too_big_is_refused(self):
        self.b.bridge._on_page = pages = mock.Mock(return_value=True)
        url = "https://scholar.google.com/scholar?q=x"
        self.assertEqual(self.b.request("/page", {"url": url})[0], 400)
        self.assertEqual(self.b.request("/page", {"url": url, "html": ""})[0],
                         400)
        with mock.patch.object(browser_bridge, "MAX_PAGE", 10):
            self.assertEqual(self.b.request(
                "/page", {"url": url, "html": "x" * 11})[0], 413)
        pages.assert_not_called()

    def test_end_to_end_into_eng_rep_pdf(self):
        tmp = Path(tempfile.mkdtemp(prefix="extclear_bridge_"))
        with mock.patch.object(eng_rep_pdf, "CACHE_DIR", tmp / "engr_cache"), \
                mock.patch.object(eng_rep_pdf, "_BROWSER_CLEARANCES", None), \
                mock.patch.object(eng_rep_pdf, "_BROWSER_REFUSED", set()):
            self.b.bridge._on_clearance = eng_rep_pdf.accept_browser_clearance
            self.assertEqual(self.b.request("/clearance", handed_over())[:2],
                             (200, {"ok": True}))
            (held,) = eng_rep_pdf._browser_clearances()
        self.assertEqual((held.value, held.user_agent), ("chrome-pass", CHROME_UA))


class ExtensionSourceTests(unittest.TestCase):
    """The extension's half, as far as Python can read it."""

    def test_it_may_read_cookies(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(
            encoding="utf-8"))
        self.assertIn("cookies", manifest["permissions"])

    def _listed(self, name):
        import re
        worker = (EXTENSION / "src" / "background.js").read_text(
            encoding="utf-8")
        found = re.search(rf"const {name} = \[([^\]]*)\]", worker)
        return re.findall(r'"([^"]+)"', found.group(1))

    def test_it_hands_over_the_sites_the_app_takes_and_no_others(self):
        import scholar_extension
        self.assertEqual(sorted(self._listed("CLEARANCE_SITES")),
                         sorted([*eng_rep_pdf._EXTENSION_SITES,
                                 scholar_extension.SCHOLAR_HOST]))
        self.assertEqual(self._listed("CLEARANCE_HEADERS"),
                         list(eng_rep_pdf._BROWSER_HEADERS))

    def test_of_google_s_cookies_only_those_the_app_takes(self):
        import scholar_extension
        listed = self._listed("GOOGLE_COOKIES")
        self.assertEqual(listed, list(scholar_extension.COOKIES))
        # None of those that sign the reader in to Google.
        for signs_in in ("SID", "HSID", "SSID", "APISID", "SAPISID",
                         "__Secure-1PSID", "__Secure-3PSID", "LSID"):
            self.assertNotIn(signs_in, listed)

    def test_the_page_script_keeps_scholar_s_page_as_it_arrived(self):
        content = (EXTENSION / "src" / "content.js").read_text(
            encoding="utf-8")
        # Taken before start() links anything in it.
        self.assertLess(content.index("const arrived ="),
                        content.index("async function start()"))
        self.assertIn('message.type === "pageHtml"', content)


if __name__ == "__main__":
    unittest.main()
