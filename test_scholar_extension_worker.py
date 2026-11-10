"""The extension closes Scholar errands only after handing over their HTML."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


NODE = shutil.which("node")
WORKER = Path(__file__).with_name("browser_extension") / "src" / "background.js"
SCHOLAR = "https://scholar.google.com/scholar?q=Brown&as_sdt=4"
COMMONLII = "https://www.commonlii.org/uk/cases/EngR/1854/296.html"
HTML = '<div id="gs_res_ccl"><div class="gs_r"><h3 class="gs_rt">Brown</h3></div></div>'


@unittest.skipUnless(NODE, "node not installed")
class ScholarErrandHandoffTests(unittest.TestCase):
    def run_handoff(self, url, clearance, taken=None):
        source = WORKER.read_text(encoding="utf-8")
        start = source.index("async function sendClearance(")
        end = source.index("\n}", start) + 2
        payload = {
            "function": source[start:end],
            "url": url,
            "clearance": clearance,
            "taken": taken,
            "html": HTML,
        }
        script = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const result = { requests: [], finished: [], shown: [], removed: [] };
const errand = { id: 'wanted-page', tabId: 7 };
const handing = new Set();
const deps = {
  appRunning: async () => true,
  clearanceSite: (url) => new URL(url).hostname.endsWith('commonlii.org')
    ? 'commonlii.org' : 'scholar.google.com',
  siteCookies: async () => [{ name: 'cf_clearance', value: 'test' }],
  loadErrands: async () => {},
  errands: new Map([[7, errand]]),
  handing,
  appFetch: async (path, options) => {
    result.requests.push({ path, body: options.body });
    return path === '/clearance' ? input.clearance : input.taken;
  },
  browserName: () => 'Chrome',
  ownHeaders: () => ({ 'user-agent': 'test-browser' }),
  arrivedPage: async () => input.html,
  finishErrand: async (item) => { result.finished.push(item.id); },
  showErrand: async (item) => { result.shown.push(item.id); },
  chrome: { tabs: { remove: async (tabId) => { result.removed.push(tabId); } } },
};
const names = Object.keys(deps);
const run = new Function(...names, 'details', input.function +
  '\nreturn sendClearance(details);');
run(...Object.values(deps), { url: input.url, tabId: 7 }).then(() => {
  result.pending = [...handing];
  process.stdout.write(JSON.stringify(result));
}).catch((error) => { console.error(error); process.exitCode = 1; });
"""
        done = subprocess.run(
            [NODE, "-e", script], input=json.dumps(payload),
            capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout)
        self.assertEqual(result["pending"], [])
        return result

    def test_scholar_cookie_acceptance_without_a_page_request_keeps_the_tab_open(self):
        result = self.run_handoff(SCHOLAR, {"ok": True, "page": False})
        self.assertEqual([r["path"] for r in result["requests"]], ["/clearance"])
        self.assertEqual(result["finished"], [])
        self.assertEqual(result["removed"], [])

    def test_commonlii_cookie_acceptance_finishes_its_clearance_only_errand(self):
        result = self.run_handoff(COMMONLII, {"ok": True, "page": False})
        self.assertEqual([r["path"] for r in result["requests"]], ["/clearance"])
        self.assertEqual(result["finished"], ["wanted-page"])

    def test_scholar_finishes_after_its_requested_html_is_accepted(self):
        result = self.run_handoff(
            SCHOLAR, {"ok": True, "page": True}, {"ok": True, "close": True})
        self.assertEqual([r["path"] for r in result["requests"]],
                         ["/clearance", "/page"])
        self.assertEqual(result["requests"][1]["body"], {"url": SCHOLAR, "html": HTML})
        self.assertEqual(result["finished"], ["wanted-page"])
        self.assertEqual(result["shown"], [])

    def test_a_scholar_check_is_shown_without_closing_its_errand(self):
        result = self.run_handoff(
            SCHOLAR, {"ok": True, "page": True}, {"ok": False, "check": True})
        self.assertEqual([r["path"] for r in result["requests"]],
                         ["/clearance", "/page"])
        self.assertEqual(result["shown"], ["wanted-page"])
        self.assertEqual(result["finished"], [])
        self.assertEqual(result["removed"], [])


@unittest.skipUnless(NODE, "node not installed")
class BackgroundWindowTests(unittest.TestCase):
    def run_window(self, *, reader=True, action="open", url=SCHOLAR, deliver=False):
        source = WORKER.read_text(encoding="utf-8")
        start = source.index("const ERRAND_GRACE")
        end = source.index("chrome.tabs.onUpdated.addListener", start)
        payload = {"source": source[start:end], "reader": reader, "action": action,
                   "url": url, "deliver": deliver}
        script = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = { createdTabs: [], createdWindows: [], updates: [], removed: [], events: [], reads: 0 };
let current = { id: 7, windowId: 3, url: input.url, status: 'complete' };
let timer = 0;
const deps = {
  chrome: {
    storage: { session: { get: async () => ({}), set: async () => {} } },
    windows: {
      getAll: async () => input.reader ? [{ id: 3, tabs: [{id: 2}] }] : [],
      create: async (props) => { out.createdWindows.push(props); return {id: 3, tabs: [current]}; },
      update: async (id, props) => { out.updates.push({kind: 'window', ...props}); },
    },
    tabs: {
      create: async (props) => { out.createdTabs.push(props); return current; },
      update: async (id, props) => { out.updates.push({kind: 'tab', ...props}); },
      get: async () => current,
      remove: async (id) => { out.removed.push(id); },
    },
  },
  appFetch: async (path, options) => { out.events.push(options.body.event); },
  setTimeout: () => ++timer,
  clearTimeout: () => {},
  clearanceSite: (url) => new URL(url).hostname === 'scholar.google.com' ? 'scholar.google.com' : '',
  sendClearance: async () => { out.reads++; if (input.deliver) await finish(); },
};
let finish;
const run = new Function(...Object.keys(deps), 'input', 'setFinish', input.source + `
return (async () => {
  await runErrand({ id: 'request', url: '${SCHOLAR}' });
  const errand = errands.get(7);
  setFinish(() => finishErrand(errand));
  if (input.action === 'timeout') errand.expires = Date.now() - 1;
  if (input.action !== 'open') await checkErrand(errand);
})();`);
run(...Object.values(deps), input, f => {finish = f;}).then(() => {
  process.stdout.write(JSON.stringify(out));
}).catch(error => { console.error(error); process.exitCode = 1; });
"""
        # The JS fixture embeds only this constant test URL, never a shell command.
        script = script.replace("${SCHOLAR}", SCHOLAR)
        done = subprocess.run([NODE, "-e", script], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_running_browser_uses_an_inactive_tab_without_a_new_window(self):
        result = self.run_window()
        self.assertEqual(result["createdTabs"], [{"windowId": 3, "url": "about:blank", "active": False}])
        self.assertEqual(result["createdWindows"], [])
        self.assertFalse(any(update.get("focused") or update.get("active") for update in result["updates"]))

    def test_no_existing_window_starts_minimized_without_focus(self):
        result = self.run_window(reader=False)
        self.assertEqual(result["createdWindows"], [{"url": "about:blank", "type": "popup",
                                                      "state": "minimized", "focused": False}])

    def test_slow_successful_handoff_closes_without_showing(self):
        result = self.run_window(action="check", deliver=True)
        self.assertEqual(result["reads"], 1)
        self.assertEqual(result["removed"], [7])
        self.assertNotIn("show", result["events"])

    def test_delayed_app_reply_does_not_show_results(self):
        result = self.run_window(action="check")
        self.assertEqual(result["removed"], [])
        self.assertNotIn("show", result["events"])

    def test_timeout_cleans_up_without_showing_results(self):
        result = self.run_window(action="timeout")
        self.assertEqual(result["removed"], [7])
        self.assertIn("closed", result["events"])
        self.assertNotIn("show", result["events"])

    def test_captcha_or_consent_restores_window_and_activates_the_tab(self):
        for url in ("https://www.google.com/sorry/index", "https://consent.google.com/m"):
            with self.subTest(url=url):
                result = self.run_window(action="check", url=url)
                self.assertIn("show", result["events"])
                self.assertIn({"kind": "tab", "active": True}, result["updates"])
                self.assertEqual(result["removed"], [])


if __name__ == "__main__":
    unittest.main()
