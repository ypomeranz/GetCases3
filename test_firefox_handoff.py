"""Firefox hands results back and closes only after the app accepts them."""

import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import firefox_handoff as fh
import scholar_browser as sb

URL = "https://scholar.google.com/scholar?q=test&as_sdt=4"
HTML = '<div class="gs_r"><h3 class="gs_rt">Example v. Respondent</h3></div>'
COOKIE = {"name": "NID", "value": "saved", "domain": ".google.com", "path": "/"}


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.shown = mock.Mock()
        self.handoff = fh.FirefoxHandoff(Path(self.tmp.name), URL, sb._scholar_page_key, self.shown)
        self.addCleanup(self.handoff.close)

    def test_results_return_cookies_and_close_without_showing(self):
        self.assertEqual(self.handoff.accept({"url": URL, "html": HTML, "cookies": [COOKIE]}),
                         {"close": True})
        self.assertEqual(self.handoff.result, (HTML, URL))
        self.assertEqual(self.handoff.cookies, [COOKIE])
        self.assertTrue(self.handoff.ready.is_set())
        self.shown.assert_not_called()

    def test_captcha_is_shown_once_then_results_close_automatically(self):
        page = {"url": "https://www.google.com/sorry/index", "html": '<div class="g-recaptcha"></div>'}
        for _ in range(2):
            self.assertEqual(self.handoff.accept(page), {"show": True})
        self.shown.assert_called_once_with()
        self.assertFalse(self.handoff.ready.is_set())
        self.assertEqual(self.handoff.accept({"url": URL, "html": HTML}), {"close": True})

    def test_empty_results_are_a_success_but_empty_shells_are_not(self):
        self.assertEqual(self.handoff.accept({"url": URL, "html": '<div id="gs_res_ccl"></div>'}),
                         {"show": True})
        self.assertEqual(self.handoff.accept({"url": URL, "html": 'Your search did not match any articles.'}),
                         {"close": True})

    def test_another_search_does_not_close_the_window(self):
        self.assertEqual(self.handoff.accept({"url": URL.replace("test", "other"), "html": HTML}),
                         {"show": True})
        self.assertIsNone(self.handoff.result)

    def test_cleared_url_is_the_same_page(self):
        url = URL + "&google_abuse=clearance"
        self.assertEqual(self.handoff.accept({"url": url, "html": HTML}), {"close": True})
        self.assertEqual(self.handoff.result, (HTML, url))

    def test_loopback_requires_the_random_token(self):
        self.handoff.start()
        root = f"http://127.0.0.1:{self.handoff._server.server_port}/"
        data = json.dumps({"url": URL, "html": HTML, "cookies": [COOKIE]}).encode()
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(urllib.request.Request(root + "wrong", data=data), timeout=3)
        self.assertEqual(denied.exception.code, 403)
        with urllib.request.urlopen(urllib.request.Request(root + self.handoff._token, data=data), timeout=3) as reply:
            self.assertEqual(json.load(reply), {"close": True})

    def test_protocol_handles_framed_unicode_and_rejects_truncation(self):
        payload = json.dumps({"from": "root", "value": "é"}, ensure_ascii=False).encode()
        self.assertEqual(fh._packet(io.BytesIO(str(len(payload)).encode() + b":" + payload))["value"], "é")
        for payload in (b"20:{}", b"not-json", b"99999999999:", b"999999999:"):
            with self.subTest(payload=payload), self.assertRaises(OSError):
                fh._packet(io.BytesIO(payload))

    def test_unanswered_browser_is_never_terminated(self):
        process = mock.Mock()
        self.assertFalse(self.handoff.finish(process))
        process.wait.assert_not_called()
        process.terminate.assert_not_called()


@unittest.skipUnless(shutil.which("node"), "node not installed")
class HelperWindowTests(unittest.TestCase):
    def run_helper(self, replies):
        script = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = { updates: [], removed: [], posts: [] };
const HANDOFF = {url: 'https://scholar.google.com/scholar?q=test', endpoint: 'http://127.0.0.1/token'};
const tab = {id: 7, windowId: 3, url: HANDOFF.url, status: 'complete'};
const browser = {
  tabs: {query: async () => [tab], get: async () => tab, update: async () => {},
    executeScript: async () => [{url: tab.url, html: '<p>page</p>'}]},
  windows: {update: async (id, props) => out.updates.push(props),
    remove: async (id) => out.removed.push(id)},
  cookies: {getAll: async () => [{name: 'NID', value: 'test', domain: '.google.com', path: '/',
    expirationDate: 2000000000.5, secure: true, httpOnly: true}]},
};
const fetch = async (url, options) => {
  out.posts.push(JSON.parse(options.body));
  return {ok: true, json: async () => input.replies.shift()};
};
const setTimeout = callback => callback();
eval(input.source).then(() => process.stdout.write(JSON.stringify(out)));
"""
        payload = {"source": Path(fh.__file__).with_suffix(".js").read_text(), "replies": replies}
        done = subprocess.run([shutil.which("node"), "-e", script], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_results_export_cookies_and_close_without_foregrounding(self):
        result = self.run_helper([{"close": True}])
        self.assertEqual(result["updates"], [{"state": "minimized"}])
        self.assertEqual(result["removed"], [3])
        self.assertEqual(result["posts"][0]["cookies"][0]["expiry"], 2000000000)

    def test_captcha_appears_once_then_closes_on_results(self):
        result = self.run_helper([{"show": True}, {"show": True}, {"close": True}])
        self.assertEqual(result["updates"], [{"state": "minimized"}, {"state": "normal", "focused": True}])
        self.assertEqual(result["removed"], [3])


@unittest.skipUnless(os.environ.get("GETCASES_TEST_FIREFOX_HANDOFF"), "optional real Firefox smoke test")
class LiveFirefoxTests(unittest.TestCase):
    def test_native_firefox_returns_local_results_and_closes(self):
        binary = sb._firefox_binary()
        self.assertIsNotNone(binary)

        class Page(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (b'<div id="gs_opinion">Local test opinion</div>'
                        b'<script>document.body.dataset.webdriver = String(navigator.webdriver)</script>')
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/opinion"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            profile = Path(tmp) / "profile"
            profile.mkdir()
            prefs = {**fh.PREFERENCES, "browser.shell.checkDefaultBrowser": False,
                     "browser.aboutwelcome.enabled": False,
                     "browser.startup.homepage_override.mstone": "ignore",
                     "browser.startup.page": 0,
                     "datareporting.policy.firstRunURL": "",
                     "termsofuse.bypassNotification": True}
            (profile / "user.js").write_text("".join(
                f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k, v in prefs.items()))
            shown = mock.Mock()
            handoff = fh.FirefoxHandoff(profile, url, lambda u: u, shown)
            try:
                port = handoff.start()
                helper_js = handoff.addon / "background.js"
                helper_js.write_text(helper_js.read_text().replace(
                    "(async () => {", "(async () => {\n await browser.cookies.set({"
                    "url: 'https://scholar.google.com/', name: 'NID', value: 'local-fixture'});", 1))
                args = [binary, "-no-remote", "-profile", str(profile), "-start-debugger-server", str(port)]
                startup = {}
                if os.name == "nt":
                    args.append("-wait-for-browser")
                    info = subprocess.STARTUPINFO()
                    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    info.wShowWindow = 7
                    startup["startupinfo"] = info
                env = {k: v for k, v in os.environ.items()
                       if k not in ("MOZ_AUTOMATION", "MOZ_MARIONETTE") and not k.startswith("MOZ_HEADLESS")}
                process = subprocess.Popen(args + [url], env=env, **startup)
                try:
                    handoff.install(port, process)
                    self.assertTrue(handoff.ready.wait(30), "Firefox did not return the page")
                    self.assertIn('data-webdriver="false"', handoff.result[0])
                    self.assertTrue(any(c["name"] == "NID" and c["value"] == "local-fixture"
                                        for c in handoff.cookies))
                    shown.assert_not_called()
                    self.assertTrue(handoff.finish(process))
                    self.assertIsNotNone(process.poll())
                finally:
                    if process.poll() is None:
                        process.terminate()  # Only this disposable, local-fixture browser.
                        process.wait(timeout=5)
            finally:
                handoff.close()


if __name__ == "__main__":
    unittest.main()
