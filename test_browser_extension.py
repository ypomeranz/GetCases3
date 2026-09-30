"""The browser extension and the app's end of it.

The GetCases extension for Chrome (``browser_extension/``) links the
citations on web pages and in PDFs.  While the app runs, a click opens the
citation in it, through a small server the app keeps on this machine
(``browser_bridge``); when it does not, the click goes to the web page the
app itself would open (``browser_links``).  These tests hold the pieces to
that:

* the server answers the extension and nothing else — not a web page, not a
  page reached by DNS rebinding;
* it reads a page's citations with the app's own detector, and counts its
  offsets the way JavaScript does;
* the extension's own reader, for when the app is not running, uses the
  app's regular expressions (exported by ``export_extension_patterns.py``)
  and opens the same web pages the app does;
* the app opens what the extension sends it as its own links open, and
  brings the window to the front.

The JavaScript half runs in Node when Node is installed, and is skipped
otherwise.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

import browser_bridge
import browser_links
import citations

ROOT = Path(__file__).resolve().parent
EXTENSION = ROOT / "browser_extension"
import export_extension_patterns as export_patterns

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")

try:
    import tkinter  # noqa: F401
    HAVE_TK = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_TK = False

NODE = shutil.which("node")

#: Text with a citation of every kind the extension reads by itself.
SAMPLE = (
    "The Court relied on Roe v. Wade, 410 U.S. 113, 153 (1973), and later "
    "on 410 U.S. at 164.  See also 42 U.S.C. § 1983(a); Fed. R. Civ. P. "
    "56(a); 29 C.F.R. § 1614.105(a)(1); U.S. Const. amend. XIV, § 1; 88 "
    "Stat. 1932; 88 Fed. Reg. 382; Hadley v. Baxendale, 156 Eng. Rep. 145 "
    "(1854); 8 S.E.C. 893, 915; Anderson v. Liberty Lobby, Inc., 477 U.S. "
    "242, 248 (1986); 2022 WL 2373418, at *12; Marbury v. Madison, 5 U.S. "
    "(1 Cranch) 137 (1803); the Fifth Amendment; Article III, § 2; "
    "Rule 404 of the Federal Rules of Evidence; 574 F.3d 1098, 1101 (9th "
    "Cir. 2009); 125 Yale L.J. 946 (2016)."
)


def run_node(script: str, payload) -> object:
    """Run *script* in Node with the extension's patterns.js and citations.js
    loaded and *payload* (JSON) on stdin; its stdout is JSON."""
    prelude = (
        "require(%r); require(%r);\n"
        "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        % (str(EXTENSION / "src" / "patterns.js"),
           str(EXTENSION / "src" / "citations.js")))
    done = subprocess.run(
        [NODE, "-e", prelude + script], input=json.dumps(payload),
        capture_output=True, text=True, timeout=60, check=False)
    if done.returncode:
        raise AssertionError(f"node failed: {done.stderr}")
    return json.loads(done.stdout)


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------

class _Bridge:
    """A bridge on a free port, and the requests it has handed on."""

    def __init__(self, port=0):
        self.opened = []
        self.bridge = browser_bridge.BrowserBridge(self.opened.append,
                                                   port=port)
        self.bridge.start()
        if not self.bridge.wait_bound(10):
            raise AssertionError("the bridge never bound its port")

    @property
    def port(self):
        return self.bridge.port

    def request(self, path, body=None, *, method=None, headers=None,
                host=None):
        """(status, parsed JSON or raw bytes, response headers)."""
        h = {"X-GetCases": "1"}
        h.update(headers or {})
        if host is not None:
            h["Host"] = host
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, headers=h,
            method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw), dict(resp.headers)
        except urllib.error.HTTPError as err:
            return err.code, err.read(), dict(err.headers)

    def close(self):
        self.bridge.close()


class BridgeSecurityTests(unittest.TestCase):
    """Only the extension may use the server."""

    @classmethod
    def setUpClass(cls):
        cls.b = _Bridge()

    @classmethod
    def tearDownClass(cls):
        cls.b.close()

    def test_the_extension_is_answered(self):
        status, body, _ = self.b.request("/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["app"], "GetCases")
        self.assertEqual(body["api"], browser_bridge.API_VERSION)

    def test_an_extension_origin_is_answered(self):
        for origin in ("chrome-extension://abcdef", "moz-extension://1234"):
            status, _body, _ = self.b.request("/status",
                                              headers={"Origin": origin})
            self.assertEqual(status, 200, origin)

    def test_a_request_without_the_header_is_refused(self):
        status, _body, _ = self.b.request("/status",
                                          headers={"X-GetCases": ""})
        self.assertEqual(status, 403)
        status, _body, _ = self.b.request(
            "/open", {"text": "42 U.S.C. § 1983"}, headers={"X-GetCases": "0"})
        self.assertEqual(status, 403)
        self.assertEqual(self.b.opened, [])

    def test_a_web_page_is_refused(self):
        # A page can send the header only after a CORS preflight, which is
        # refused below; and a request that says it comes from a page is
        # refused outright.
        status, _body, _ = self.b.request(
            "/open", {"text": "42 U.S.C. § 1983"},
            headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.b.opened, [])

    def test_the_preflight_is_refused_without_cors_headers(self):
        status, _body, headers = self.b.request("/open", method="OPTIONS")
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_no_answer_carries_cors_headers(self):
        _status, _body, headers = self.b.request("/status")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_dns_rebinding_is_refused(self):
        # evil.example resolved to 127.0.0.1: its page is "same-origin" with
        # the server, but its requests name evil.example as their host.
        status, _body, _ = self.b.request(
            "/status", host=f"evil.example:{self.b.port}")
        self.assertEqual(status, 403)
        status, _body, _ = self.b.request(
            "/status", host=f"localhost:{self.b.port}")
        self.assertEqual(status, 200)

    def test_unknown_paths_and_bad_bodies(self):
        self.assertEqual(self.b.request("/nothing")[0], 404)
        self.assertEqual(self.b.request("/open", [1, 2])[0], 400)
        self.assertEqual(self.b.request("/open", {})[0], 400)
        self.assertEqual(self.b.request("/detect", {"text": 5})[0], 400)


class BridgeOpenTests(unittest.TestCase):
    def setUp(self):
        self.b = _Bridge()

    def tearDown(self):
        self.b.close()

    def test_a_link_the_app_detected_comes_back_as_its_action(self):
        status, body, _ = self.b.request("/open", {
            "kind": "cite", "value": "410 U.S. 113@153",
            "text": "Roe v. Wade, 410 U.S. 113, 153 (1973)"})
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(self.b.opened, [{
            "kind": "cite", "value": "410 U.S. 113@153",
            "text": "Roe v. Wade, 410 U.S. 113, 153 (1973)"}])

    def test_text_alone_is_enough(self):
        self.b.request("/open", {"text": "Roe v. Wade"})
        self.assertEqual(self.b.opened, [{"text": "Roe v. Wade"}])

    def test_a_second_app_takes_the_port_once_the_first_lets_go(self):
        # A newer GetCases starts while the older still holds the port; the
        # older steps back (single_instance), and the newer gets it.
        newer = browser_bridge.BrowserBridge(Mock(), port=self.b.port)
        newer.start()
        try:
            self.assertFalse(newer.wait_bound(0.6))
            self.b.close()
            self.assertTrue(newer.wait_bound(10))
        finally:
            newer.close()


class BridgeDetectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = _Bridge()

    @classmethod
    def tearDownClass(cls):
        cls.b.close()

    def detect(self, text, italic=None):
        status, body, _ = self.b.request("/detect",
                                         {"text": text, "italic": italic})
        self.assertEqual(status, 200)
        return body["links"]

    def test_it_reads_with_the_apps_detector(self):
        links = self.detect(SAMPLE)
        got = [(l["kind"], l["value"]) for l in links]
        want = [a for _s, _e, a in citations.detect_links(SAMPLE)]
        self.assertEqual(got, [tuple(a) for a in want])
        roe = links[0]
        self.assertEqual(SAMPLE[roe["start"]:roe["end"]],
                         "Roe v. Wade, 410 U.S. 113, 153 (1973)")
        self.assertEqual(roe["category"], "case")
        self.assertEqual(roe["label"], "410 U.S. 113, 153")
        self.assertEqual(roe["url"], browser_links.browser_url(
            ("cite", "410 U.S. 113@153")))

    def test_offsets_count_as_javascript_does(self):
        # An emoji is one character to Python and two to JavaScript.
        text = "😀😀 See 42 U.S.C. § 1983."
        (link,) = self.detect(text)
        utf16 = text.encode("utf-16-le")
        self.assertEqual(
            utf16[link["start"] * 2:link["end"] * 2].decode("utf-16-le"),
            "42 U.S.C. § 1983")

    def test_italics_say_where_a_case_name_is(self):
        text = "The rule in Smith v. Jones, 306 Md. 556, 560 (1986) holds."
        start = text.index("Smith")
        end = text.index(",", start)
        (link,) = self.detect(text, italic=[[start, end]])
        self.assertEqual(text[link["start"]:link["end"]],
                         "Smith v. Jones, 306 Md. 556, 560 (1986)")

    def test_labels(self):
        labels = [l["label"] for l in self.detect(SAMPLE)]
        for want in ("42 U.S.C. § 1983(a)", "29 C.F.R. § 1614.105(a)(1)",
                     "U.S. Const. amend. XIV, § 1", "U.S. Const. amend. V",
                     "U.S. Const. art. III, § 2", "Fed. R. Civ. P. 56(a)",
                     "8 S.E.C. 893, 915 (1941)"):
            self.assertIn(want, labels)


class Utf16Tests(unittest.TestCase):
    def test_round_trip(self):
        text = "a😀b𝒜c"
        offsets = browser_bridge.utf16_offsets(text)
        self.assertEqual(offsets, [0, 1, 3, 4, 6, 7])
        for i in range(len(text) + 1):
            self.assertEqual(browser_bridge._from_utf16(offsets, offsets[i]), i)

    def test_plain_text_needs_no_table(self):
        self.assertIsNone(browser_bridge.utf16_offsets("§ 1983 — ¶ 4"))


# ---------------------------------------------------------------------------
# Web pages
# ---------------------------------------------------------------------------

class BrowserUrlTests(unittest.TestCase):
    """Where a citation opens when GetCases is not running."""

    def url(self, kind, value, text=""):
        return browser_links.browser_url((kind, value), text)

    def test_cases_go_to_google_scholar(self):
        self.assertEqual(self.url("cite", "410 U.S. 113@153"),
                         "https://scholar.google.com/scholar?q="
                         "%22410%20U.S.%20113%22")

    def test_statutes_go_to_their_official_pages(self):
        self.assertEqual(
            self.url("usc", "42:1983:a"),
            "https://uscode.house.gov/view.xhtml?req=granuleid:"
            "USC-prelim-title42-section1983&num=0&edition=prelim")
        self.assertEqual(self.url("cfr", "29:1614.105:a,1"),
                         "https://www.ecfr.gov/current/title-29/"
                         "section-1614.105")
        self.assertEqual(self.url("rule", "fre:404:b"),
                         "https://www.law.cornell.edu/rules/fre/rule_404")
        self.assertEqual(self.url("const", "amend:14:1"),
                         "https://constitution.congress.gov/constitution/"
                         "amendment-14/")
        self.assertEqual(self.url("const", "art:3:2"),
                         "https://constitution.congress.gov/constitution/"
                         "article-3/")
        self.assertEqual(self.url("const", "pmbl:0:"),
                         "https://constitution.congress.gov/constitution/"
                         "preamble/")
        self.assertTrue(self.url("statestat", "ca-pen:187:").startswith(
            "https://leginfo.legislature.ca.gov/"))

    def test_link_outs_keep_their_address(self):
        for kind in ("browse", "statpdf", "frpdf"):
            self.assertEqual(self.url(kind, "https://example.gov/x"),
                             "https://example.gov/x")

    def test_the_rest_as_the_app_right_click_had_them(self):
        spec = json.dumps({"docket": "26A139", "date": "2026-08-24"})
        self.assertEqual(
            self.url("scotus", spec),
            "https://www.supremecourt.gov/docket/docketfiles/html/public/"
            "26A139.html")
        recap = self.url("recap", json.dumps(
            {"docket": "12-6371", "court": "njd", "date": "2024-03-28"}))
        self.assertIn("docket_number=12-6371", recap)
        self.assertIn("court=njd", recap)
        self.assertTrue(self.url("engrep", "156:145@151").startswith(
            "https://www.commonlii.org/uk/cases/EngR/"))
        self.assertTrue(self.url("mystery", "x", "some text").startswith(
            "https://www.google.com/search?q=some%20text"))

    @unittest.skipUnless(HAVE_TK, "tkinter not installed")
    def test_the_apps_right_click_opens_the_same_page(self):
        import courtlistener_gui
        with patch.object(courtlistener_gui.webbrowser, "open") as browse:
            courtlistener_gui._open_citation_in_browser(("usc", "42:1983:"))
        browse.assert_called_once_with(self.url("usc", "42:1983:"))


# ---------------------------------------------------------------------------
# The patterns the extension reads with when the app is not running
# ---------------------------------------------------------------------------

class PatternExportTests(unittest.TestCase):
    def test_patterns_js_is_up_to_date(self):
        on_disk = export_patterns.OUTPUT.read_text(encoding="utf-8")
        self.assertEqual(
            on_disk, export_patterns.render(),
            "browser_extension/src/patterns.js is out of date: run "
            "python export_extension_patterns.py")

    def test_named_groups_and_verbose_mode(self):
        source, flags = export_patterns.to_js(
            r"""(?P<vol> \d+ )   # the volume
                \s+ [ ]? (?P=vol)""", re.VERBOSE | re.IGNORECASE)
        self.assertEqual(source, r"(?<vol>\d+)\s+[ ]?\k<vol>")
        self.assertEqual(flags, "i")

    def test_a_case_sensitive_group_in_a_case_blind_pattern(self):
        # JavaScript before Chrome 125 (and Node 22) has no (?-i:…): the
        # pattern loses its i flag and spells its other letters both ways.
        source, flags = export_patterns.to_js(r"amend\.?\s*(?-i:[IVX]+)",
                                              re.IGNORECASE)
        self.assertEqual(flags, "")
        self.assertEqual(source,
                         r"[aA][mM][eE][nN][dD]\.?\s*(?:[IVX]+)")

    def test_python_only_syntax(self):
        self.assertEqual(export_patterns.to_js(r"\Aa{,3}\Z", 0),
                         ("^a{0,3}$", ""))

    @unittest.skipUnless(NODE, "node not installed")
    def test_each_pattern_matches_what_it_matches_in_python(self):
        corpus = SAMPLE + "\n" + "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in sorted((ROOT / "test_data").glob("*.html")))
        want = {name: [[m.start(), m.end()] for m in rx.finditer(corpus)]
                for name, rx in export_patterns.patterns().items()}
        got = run_node(
            "const out = {};\n"
            "for (const [k, v] of Object.entries(GetCasesPatterns.patterns)) {\n"
            "  const re = new RegExp(v.source, v.flags + 'g');\n"
            "  out[k] = [...input.matchAll(re)].map(m => [m.index, m.index + m[0].length]);\n"
            "}\nprocess.stdout.write(JSON.stringify(out));", corpus)
        # The corpus is all Basic Multilingual Plane, where Python's and
        # JavaScript's offsets agree.
        self.assertTrue(all(ord(c) <= 0xFFFF for c in corpus))
        for name in want:
            self.assertEqual(got[name], want[name], name)
        self.assertGreater(len(want["caseCite"]), 100)


@unittest.skipUnless(NODE, "node not installed")
class FallbackReaderTests(unittest.TestCase):
    """citations.js: the extension reading a page without the app."""

    @classmethod
    def setUpClass(cls):
        cls.links = run_node(
            "process.stdout.write(JSON.stringify(GetCasesCitations.detect(input)));",
            SAMPLE)

    def found(self):
        return [(SAMPLE[l["start"]:l["end"]], l["kind"], l["value"])
                for l in self.links]

    def test_what_it_links(self):
        found = self.found()
        for want in [
            ("410 U.S. 113, 153 (1973)", "cite", "410 U.S. 113@153"),
            ("410 U.S. at 164", "cite", "410 U.S. 113@164"),
            ("42 U.S.C. § 1983(a)", "usc", "42:1983:a"),
            ("Fed. R. Civ. P. 56(a)", "rule", "frcp:56:a"),
            ("29 C.F.R. § 1614.105(a)(1)", "cfr", "29:1614.105:a,1"),
            ("U.S. Const. amend. XIV, § 1", "const", "amend:14:1"),
            ("88 Stat. 1932", "statpdf",
             "https://www.govinfo.gov/link/statute/88/1932"),
            ("88 Fed. Reg. 382", "frpdf",
             "https://www.govinfo.gov/link/fr/88/382?link-type=pdf"),
            ("156 Eng. Rep. 145", "engrep", "156:145"),
            ("8 S.E.C. 893, 915", "sec", '{"page":893,"pin":915,"vol":8}'),
            ("2022 WL 2373418, at *12", "cite", "2022 WL 2373418@12"),
            ("5 U.S. (1 Cranch) 137 (1803)", "cite", "5 U.S. 137"),
            ("Fifth Amendment", "const", "amend:5:"),
            ("Article III, § 2", "const", "art:3:2"),
            ("Rule 404 of the Federal Rules of Evidence", "rule", "fre:404:"),
            ("574 F.3d 1098, 1101 (9th Cir. 2009)", "cite",
             "574 F.3d 1098@1101"),
        ]:
            self.assertIn(want, found)
        # A law review is cited like a reporter, but is none.
        self.assertFalse(any("Yale" in text for text, _k, _v in found))

    def test_the_same_actions_as_the_app(self):
        # Where both read a citation, they mean the same thing by it: the
        # app opens a link the extension found exactly as one it found.
        app = {tuple(a) for _s, _e, a in citations.detect_links(SAMPLE)}
        for _text, kind, value in self.found():
            if kind == "sec":
                continue        # the app pins it through its page index
            self.assertIn((kind, value), app)

    def test_the_same_web_pages_as_the_app(self):
        for link in self.links:
            if link["kind"] in ("sec", "engrep"):
                continue        # the app's index names the exact page
            self.assertEqual(
                link["url"],
                browser_links.browser_url((link["kind"], link["value"]),
                                          SAMPLE[link["start"]:link["end"]]),
                link)

    def test_pages_without_citations_are_not_read(self):
        self.assertEqual(run_node(
            "process.stdout.write(JSON.stringify([\n"
            "  GetCasesCitations.mightHaveCitations(input[0]),\n"
            "  GetCasesCitations.mightHaveCitations(input[1])]));",
            ["Lunch at noon; the meeting moved to room 12.", SAMPLE]),
            [False, True])

    def test_links_to_legal_sites(self):
        urls = [
            "https://supreme.justia.com/cases/federal/us/410/113/",
            "https://www.courtlistener.com/c/F.%203d/574/1098/",
            "https://www.law.cornell.edu/supremecourt/text/347/483",
            "https://www.law.cornell.edu/uscode/text/42/1983",
            "https://www.law.cornell.edu/cfr/text/29/1614.105",
            "https://www.law.cornell.edu/rules/frcp/rule_56",
            "https://www.law.cornell.edu/constitution/amendmentxiv",
            "https://uscode.house.gov/view.xhtml?req=granuleid:"
            "USC-prelim-title18-section922&num=0&edition=prelim",
            "https://www.ecfr.gov/current/title-40/chapter-I/"
            "subchapter-I/part-261/section-261.4",
            "https://www.govinfo.gov/link/statute/88/1932",
            "https://cite.case.law/f3d/574/1098/",
            "https://tile.loc.gov/storage-services/service/ll/usrep/"
            "usrep410/usrep410113/usrep410113.pdf",
            "https://en.wikipedia.org/wiki/Roe_v._Wade",
            "https://example.com/42/1983",
        ]
        got = run_node(
            "process.stdout.write(JSON.stringify(input.map(u => {\n"
            "  const a = GetCasesCitations.actionForUrl(u);\n"
            "  return a && [a.kind, a.value]; })));", urls)
        self.assertEqual(got, [
            ["cite", "410 U.S. 113"],
            ["cite", "574 F.3d 1098"],
            ["cite", "347 U.S. 483"],
            ["usc", "42:1983:"],
            ["cfr", "29:1614.105:"],
            ["rule", "frcp:56:"],
            ["const", "amend:14:"],
            ["usc", "18:922:"],
            ["cfr", "40:261.4:"],
            ["statpdf", "https://www.govinfo.gov/link/statute/88/1932"],
            ["cite", "574 F.3d 1098"],
            ["cite", "410 U.S. 113"],
            None,
            None,
        ])


# ---------------------------------------------------------------------------
# The app's side
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_TK, "tkinter not installed")
class AppOpensWhatTheBrowserSendsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import courtlistener_gui
        cls.gui = courtlistener_gui

    def app(self):
        win = object.__new__(self.gui.CourtListenerGUI)
        win.root = Mock()
        win._status_var = Mock()
        win._quick_popup = None
        win._spotlight_toggle_at = 0.0
        return win

    def test_a_detected_link_opens_as_a_brief_link_does(self):
        win = self.app()
        with patch.object(self.gui, "_follow_brief_action") as follow:
            win._open_from_browser({"kind": "cite", "value": "410 U.S. 113@153",
                                    "text": "Roe v. Wade,\n410 U.S. 113, 153"})
        follow.assert_called_once_with(
            win, win.root, ("cite", "410 U.S. 113@153"),
            win._status_var.set, snippet="Roe v. Wade, 410 U.S. 113, 153")
        self.assertGreater(win._browser_raise_until, time.monotonic())

    def test_only_the_kinds_of_link_the_app_has(self):
        win = self.app()
        with patch.object(self.gui, "_follow_brief_action") as follow:
            win._open_from_browser({"kind": "shell", "value": "rm -rf"})
            # A link-out opens a web page, never a file or a program.
            win._open_from_browser({"kind": "browse",
                                    "value": "file:///C:/Windows/calc.exe"})
            win._open_from_browser({"kind": "statpdf",
                                    "value": "javascript:alert(1)"})
        follow.assert_not_called()

    def test_a_citation_as_text_opens_as_typed_into_spotlight(self):
        win = self.app()
        with patch.object(self.gui.CourtListenerGUI, "_open_lookup_query",
                          autospec=True, return_value=True) as lookup, \
                patch.object(self.gui.CourtListenerGUI, "_spotlight_search",
                             autospec=True) as search:
            win._open_from_browser({"text": "42 U.S.C. § 1983"})
        lookup.assert_called_once_with(win, "42 U.S.C. § 1983")
        search.assert_not_called()

    def test_other_text_is_searched_for_in_spotlight(self):
        win = self.app()
        with patch.object(self.gui.CourtListenerGUI, "_open_lookup_query",
                          autospec=True, return_value=False), \
                patch.object(self.gui.CourtListenerGUI, "_spotlight_search",
                             autospec=True) as search:
            win._open_from_browser({"text": "Roe v. Wade"})
        search.assert_called_once_with(win, "Roe v. Wade")

    def test_the_lookup_reads_statutes_and_leaves_names(self):
        win = self.app()
        before = Mock()
        with patch.object(self.gui, "_open_statute_action") as opened:
            self.assertTrue(win._open_lookup_query("42 USC 1983",
                                                   before_open=before))
        before.assert_called_once_with()
        self.assertEqual(opened.call_args[0][1], ("usc", "42:1983:"))
        with patch.object(self.gui, "_spotlight_case_action",
                          return_value=None), \
                patch.object(self.gui, "_parse_citation_line",
                             return_value=None):
            self.assertFalse(win._open_lookup_query("Roe v. Wade"))

    def test_requests_queued_off_the_tk_thread_open_on_it(self):
        import queue
        win = self.app()
        win._browser_requests = queue.SimpleQueue()
        win._browser_requests.put({"text": "a"})
        win._browser_requests.put({"text": "b"})
        with patch.object(self.gui.CourtListenerGUI, "_open_from_browser",
                          autospec=True) as opened:
            win._drain_browser_requests()
        self.assertEqual([c.args[1] for c in opened.call_args_list],
                         [{"text": "a"}, {"text": "b"}])
        win.root.after.assert_called_once_with(
            100, win._drain_browser_requests)


@unittest.skipUnless(HAVE_TK, "tkinter not installed")
class WindowComesForwardTests(unittest.TestCase):
    """A window opened from Chrome comes in front of it."""

    @classmethod
    def setUpClass(cls):
        import courtlistener_gui
        cls.gui = courtlistener_gui
        try:
            cls.root = tkinter.Tk()
        except tkinter.TclError as exc:  # pragma: no cover - no display
            raise unittest.SkipTest(f"no display: {exc}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def app(self):
        win = object.__new__(self.gui.CourtListenerGUI)
        win.root = self.root
        win._quick_popup = None
        return win

    def mapped(self, win, top):
        with patch.object(self.gui.CourtListenerGUI, "_bring_to_front",
                          autospec=True) as front:
            win._on_window_mapped(Mock(widget=top))
            self.root.update()
        return front

    def test_the_first_window_after_a_click(self):
        win = self.app()
        win._browser_raise_until = time.monotonic() + 30
        top = tkinter.Toplevel(self.root)
        try:
            front = self.mapped(win, top)
            front.assert_called_once_with(win, top)
            # Only the first: the next window the reader opens is theirs.
            front = self.mapped(win, tkinter.Toplevel(self.root))
            front.assert_not_called()
        finally:
            top.destroy()

    def test_not_once_the_time_is_up(self):
        win = self.app()
        win._browser_raise_until = time.monotonic() - 1
        top = tkinter.Toplevel(self.root)
        try:
            self.mapped(win, top).assert_not_called()
            self.assertEqual(win._browser_raise_until, 0.0)
        finally:
            top.destroy()

    def test_not_without_a_click(self):
        win = self.app()
        top = tkinter.Toplevel(self.root)
        try:
            self.mapped(win, top).assert_not_called()
        finally:
            top.destroy()


@unittest.skipUnless(HAVE_TK, "tkinter not installed")
class BridgeLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import courtlistener_gui
        cls.gui = courtlistener_gui

    def app(self):
        win = object.__new__(self.gui.CourtListenerGUI)
        win.root = Mock()
        win._hotkey_yielded = False
        return win

    def test_turned_off_in_settings(self):
        win = self.app()
        with patch.object(self.gui, "_load_config",
                          return_value={"browser_extension": False}), \
                patch.object(browser_bridge, "BrowserBridge") as bridge:
            self.assertIsNone(win._start_browser_bridge())
        bridge.assert_not_called()

    def test_on_by_default_on_the_configured_port(self):
        win = self.app()
        with patch.object(self.gui, "_load_config",
                          return_value={"browser_bridge_port": 31999}), \
                patch.object(browser_bridge, "BrowserBridge") as bridge:
            self.assertIs(win._start_browser_bridge(), bridge.return_value)
        self.assertEqual(bridge.call_args.kwargs["port"], 31999)
        bridge.return_value.start.assert_called_once_with()

    def test_a_newer_getcases_gets_the_port(self):
        win = self.app()
        closing = threading.Event()
        bridge = Mock()
        bridge.close.side_effect = closing.set
        win._browser_bridge = bridge
        win._stop_browser_bridge()
        self.assertTrue(closing.wait(5))
        self.assertIsNone(win._browser_bridge)
        # And, having stepped back, it does not take the port again.
        win._hotkey_yielded = True
        with patch.object(browser_bridge, "BrowserBridge") as made:
            self.assertIsNone(win._start_browser_bridge())
        made.assert_not_called()


class ManifestTests(unittest.TestCase):
    def test_every_file_the_manifest_names_exists(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(
            encoding="utf-8"))
        named = list(manifest["icons"].values())
        named += list(manifest["action"]["default_icon"].values())
        named.append(manifest["action"]["default_popup"])
        named.append(manifest["background"]["service_worker"])
        for script in manifest["content_scripts"]:
            named += script["js"] + script.get("css", [])
        for path in named:
            self.assertTrue((EXTENSION / path).is_file(), path)
        # The service worker loads the reader alongside itself.
        worker = (EXTENSION / manifest["background"]["service_worker"])
        for name in re.findall(r'"([\w.]+\.js)"', re.search(
                r"importScripts\(([^)]*)\)", worker.read_text()).group(1)):
            self.assertTrue((worker.parent / name).is_file(), name)

    def test_the_default_port_is_the_apps(self):
        worker = (EXTENSION / "src" / "background.js").read_text()
        popup = (EXTENSION / "popup" / "popup.js").read_text()
        for source in (worker, popup):
            self.assertIn(f"port: {browser_bridge.DEFAULT_PORT}", source)


if __name__ == "__main__":
    unittest.main()
