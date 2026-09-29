"""The SEC's Decisions and Reports in the app's own viewer: the pages of the
decision cited, fetched from HathiTrust with the clearance Firefox holds
(see sec_pdf, and eng_rep_pdf for the clearance), opened at the page cited
— and, where HathiTrust's CloudFlare check stands in the way, the choice of
passing it in Firefox or reading the page in the browser instead.

HathiTrust itself is never reached: a fake answers each request the way its
page service would, one single-page PDF a scan page (the page's width, in
points, is its scan page number, so the order can be read back).
"""

import io
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")

import eng_rep_pdf
import sec_decisions as sec
import sec_pdf

try:
    import pypdfium2 as pdfium
    from PIL import Image
    HAVE_PDF = True
except ImportError:  # pragma: no cover - depends on the machine
    HAVE_PDF = False

try:  # the viewer needs tkinter, which a headless run may not have
    import tkinter as tk
    from tkinter import ttk
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None


def _display_available() -> bool:
    if gui is None:
        return False
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


def _pdf(*widths) -> bytes:
    """A PDF of one blank page per width, each that many points across."""
    pages = [Image.new("L", (w, 900), 255) for w in widths]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:],
                  resolution=72)
    return buf.getvalue()


def _png(width) -> bytes:
    buf = io.BytesIO()
    Image.new("L", (width, 1200), 255).save(buf, "PNG")
    return buf.getvalue()


def _widths(data: bytes) -> list:
    doc = pdfium.PdfDocument(data)
    try:
        return [round(doc[i].get_size()[0]) for i in range(len(doc))]
    finally:
        doc.close()


CHALLENGE = (403, b"<html><title>Just a moment...</title>challenge-platform</html>")
TOO_MANY = (429, b"Too Many Requests")


def _seqs(vol, first, last):
    return [sec.locate(vol, p).seq for p in range(first, last + 1)]


class PageWindowTests(unittest.TestCase):
    def test_a_short_decision_is_fetched_whole(self):
        self.assertEqual(sec_pdf.page_window(sec.make_spec(10, 200, 205)),
                         (10, 200, 206, 205, False))

    def test_a_long_one_is_cut_around_the_page_cited(self):
        # Chenery's first decision runs 893-938: 46 pages.
        self.assertEqual(sec_pdf.page_window(sec.make_spec(8, 893, 915)),
                         (8, 911, 934, 915, True))

    def test_cited_at_its_first_page_it_opens_there(self):
        self.assertEqual(sec_pdf.page_window(sec.make_spec(8, 893)),
                         (8, 893, 916, 893, True))

    def test_a_pin_past_where_the_next_begins_is_fetched_all_the_same(self):
        window = sec_pdf.page_window(sec.make_spec(8, 893, 940))
        self.assertEqual((window.last, window.pin), (940, 940))
        self.assertEqual(window.last - window.first + 1, sec_pdf.MAX_PAGES)


@unittest.skipUnless(HAVE_PDF, "pypdfium2 and Pillow not installed")
class FetchTests(unittest.TestCase):
    def setUp(self):
        self.requests = []          # (url, headers, cookies) as sent
        self.reply = self._page     # how the fake answers a request
        self.lock = threading.Lock()
        self.candidate = eng_rep_pdf.Clearance(
            Path("/profiles/ff/cookies.sqlite"),
            {"cf_clearance": "cleared", "__cf_bm": "bm"},
            0.0, 0.0, eng_rep_pdf._user_agent_for("147"))
        self.candidates = [self.candidate]
        eng_rep_pdf._LAST_GOOD = None
        self.addCleanup(setattr, eng_rep_pdf, "_LAST_GOOD", None)
        for patcher in (
                mock.patch.object(sec_pdf, "CACHE_DIR",
                                  Path(tempfile.mkdtemp()) / "sec_cache"),
                mock.patch.object(eng_rep_pdf, "can_fetch", return_value=True),
                mock.patch.object(eng_rep_pdf, "_firefox_clearances",
                                  side_effect=lambda site: list(self.candidates)),
                mock.patch.object(eng_rep_pdf, "_get", side_effect=self._answer)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _answer(self, url, headers, cookies):
        with self.lock:
            self.requests.append((url, headers, cookies))
        route = "image" if "/imgsrv/image?" in url else "pdf"
        return self.reply(route, int(re.search(r"seq=(\d+)", url).group(1)))

    def _page(self, route, seq):
        return 200, (_png(seq) if route == "image" else _pdf(seq))

    def _sent(self):
        return [int(re.search(r"seq=(\d+)", url).group(1))
                for url, _h, _c in self.requests]

    def test_the_page_cited_comes_first_and_the_viewer_opens_at_it(self):
        pages = sec_pdf.fetch(sec.make_spec(10, 200, 205))
        self.assertEqual(self._sent()[0], sec.locate(10, 205).seq)
        self.assertEqual(_widths(pages.data), _seqs(10, 200, 206))
        self.assertEqual((pages.first, pages.last, pages.index), (200, 206, 5))
        self.assertEqual(pages.title, "10 S.E.C. 200, 205 (1941)")
        self.assertEqual(pages.note, "")

    def test_each_request_goes_with_firefoxs_clearance_from_the_viewer_page(self):
        sec_pdf.fetch(sec.make_spec(10, 200))
        url, headers, cookies = self.requests[0]
        where = sec.locate(10, 200)
        self.assertEqual(url, f"{sec_pdf.PAGE_PDF_URL}?id={where.htid}"
                              f"&attachment=1&seq={where.seq}")
        self.assertEqual(headers["Referer"], where.url)
        self.assertIn("Firefox/147.0", headers["User-Agent"])
        self.assertEqual(cookies, {"cf_clearance": "cleared", "__cf_bm": "bm"})

    def test_pages_fetched_once_open_again_without_the_network(self):
        first = sec_pdf.fetch(sec.make_spec(10, 200, 205))
        self.requests.clear()
        self.candidates = []        # no clearance now: none is needed
        again = sec_pdf.fetch(sec.make_spec(10, 200, 205))
        self.assertEqual(self.requests, [])
        self.assertEqual(_widths(again.data), _widths(first.data))

    def test_a_cover_sheet_is_left_out(self):
        self.reply = lambda route, seq: (200, _pdf(111, seq))
        pages = sec_pdf.fetch(sec.make_spec(10, 200))
        self.assertEqual(_widths(pages.data), _seqs(10, 200, 206))

    def test_where_the_page_service_gives_no_pdf_the_images_are_used(self):
        self.reply = lambda route, seq: (
            (404, b"not here") if route == "pdf" else self._page(route, seq))
        pages = sec_pdf.fetch(sec.make_spec(10, 200))
        self.assertEqual(len(_widths(pages.data)), 7)
        # Asked for a PDF once; every page after went straight to images.
        routes = ["image" if "/imgsrv/image?" in url else "pdf"
                  for url, _h, _c in self.requests]
        self.assertEqual(routes.count("pdf"), 1)
        self.assertEqual(routes.count("image"), 7)

    def test_a_whole_volume_is_not_taken_for_one_page(self):
        self.reply = lambda route, seq: (
            (200, _pdf(1, 2, 3, 4)) if route == "pdf" else self._page(route, seq))
        pages = sec_pdf.fetch(sec.make_spec(10, 200))
        self.assertEqual(len(_widths(pages.data)), 7)

    def test_no_clearance_anywhere_sends_the_reader_to_the_page_in_firefox(self):
        self.candidates = []
        with self.assertRaises(eng_rep_pdf.CloudflareChallenge) as cm:
            sec_pdf.fetch(sec.make_spec(8, 893, 915))
        self.assertEqual(cm.exception.web_url, sec.page_url(sec.make_spec(8, 893, 915)))
        self.assertEqual(self.requests, [])

    def test_so_does_a_clearance_cloudflare_refuses(self):
        self.reply = lambda route, seq: CHALLENGE
        with self.assertRaises(eng_rep_pdf.CloudflareChallenge) as cm:
            sec_pdf.fetch(sec.make_spec(10, 200))
        self.assertIn("Firefox/147.0", cm.exception.refused_ua)

    def test_without_firefox_or_curl_cffi_only_the_browser_can(self):
        with mock.patch.object(eng_rep_pdf, "can_fetch", return_value=False):
            with self.assertRaises(eng_rep_pdf.FetchUnavailable):
                sec_pdf.fetch(sec.make_spec(10, 200))

    def test_hathitrusts_refusal_of_the_page_cited_is_an_origin_error(self):
        self.reply = lambda route, seq: (500, b"server error")
        with self.assertRaises(eng_rep_pdf.OriginError) as cm:
            sec_pdf.fetch(sec.make_spec(10, 200))
        self.assertEqual(cm.exception.status, 500)

    def _stop_after(self, printed, reply):
        """HathiTrust answers *reply* for every scan page after *printed*'s."""
        last = sec.locate(10, printed).seq
        self.reply = lambda route, seq: (
            reply if seq > last else self._page(route, seq))

    def test_pages_that_stop_coming_leave_those_that_came(self):
        self._stop_after(202, CHALLENGE)
        pages = sec_pdf.fetch(sec.make_spec(10, 200))
        self.assertEqual(_widths(pages.data), _seqs(10, 200, 202))
        self.assertEqual((pages.first, pages.last, pages.index), (200, 202, 0))
        self.assertIn("Only pages 200–202 came", pages.note)
        self.assertIn("asked for its check again", pages.note)

    def test_too_many_pages_too_fast_is_said_so(self):
        self._stop_after(203, TOO_MANY)
        pages = sec_pdf.fetch(sec.make_spec(10, 200))
        self.assertEqual(pages.last, 203)
        self.assertIn("slow down", pages.note)

    def test_the_page_cited_kept_from_before_opens_even_where_nothing_can_be_fetched(self):
        sec_pdf.fetch(sec.make_spec(10, 200))
        for p in range(201, 207):
            sec_pdf.cache_path(sec.locate(10, p)).unlink()
        with mock.patch.object(eng_rep_pdf, "can_fetch", return_value=False):
            pages = sec_pdf.fetch(sec.make_spec(10, 200))
        self.assertEqual((pages.first, pages.last), (200, 200))
        self.assertIn("can't be fetched here", pages.note)

    def test_a_long_decision_says_it_is_not_all_there(self):
        pages = sec_pdf.fetch(sec.make_spec(8, 893, 915))
        self.assertEqual((pages.first, pages.last, pages.index), (911, 934, 4))
        self.assertIn("A long decision", pages.note)

    def test_a_page_the_index_cannot_place_is_unavailable(self):
        with mock.patch.object(sec, "locate", return_value=None):
            with self.assertRaises(sec_pdf.Unavailable):
                sec_pdf.fetch(sec.make_spec(10, 200))
        self.assertEqual(self.requests, [])


class _Inline:
    """A thread that runs its target at start()."""

    def __init__(self, target=None, daemon=None):
        self.target = target

    def start(self):
        self.target()


@unittest.skipIf(gui is None or not HAVE_PDF, "the viewer needs tkinter, pypdfium2 and Pillow")
class WindowFetchTests(unittest.TestCase):
    """What the viewer does with each outcome of the fetch."""

    SPEC = sec.make_spec(8, 893, 915)

    def setUp(self):
        win = object.__new__(gui._SecPdfWindow)
        win._spec, win._title = self.SPEC, sec.spec_label(self.SPEC)
        win._status_var, win._watch = mock.Mock(), None
        win._post = lambda fn, *args: fn(*args)
        for name in ("_need_clearance", "_link_out", "_error", "_arrived"):
            setattr(win, name, mock.Mock())
        self.win = win
        patcher = mock.patch.object(gui.threading, "Thread", _Inline)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fetch(self, **kw):
        with mock.patch.object(sec_pdf, "fetch", **kw):
            self.win._fetch()

    def test_hathitrusts_check_asks_about_firefox_or_the_browser(self):
        url = sec.page_url(self.SPEC)
        self._fetch(side_effect=eng_rep_pdf.CloudflareChallenge(url))
        self.win._need_clearance.assert_called_once_with(url, "")

    def test_a_clearance_refused_is_said_to_be(self):
        url, ua = sec.page_url(self.SPEC), eng_rep_pdf._user_agent_for("157")
        self._fetch(side_effect=eng_rep_pdf.CloudflareChallenge(url, ua))
        self.win._need_clearance.assert_called_once_with(url, ua)

    def test_without_firefox_it_offers_the_page_outside_the_app(self):
        self._fetch(side_effect=eng_rep_pdf.FetchUnavailable())
        self.win._link_out.assert_called_once_with()

    def test_a_refusal_offers_the_browser(self):
        self._fetch(side_effect=eng_rep_pdf.OriginError(404))
        self.win._error.assert_called_once_with(
            "HathiTrust returned an error (HTTP 404).")

    def test_the_pages_arrive(self):
        pages = sec_pdf.Pages(_pdf(935), 0, "8 S.E.C. 893, 915 (1941)", 915, 915)
        self._fetch(return_value=pages)
        self.assertIs(self.win._arrived.call_args.args[0], pages)

    def test_it_watches_firefox_for_hathitrusts_clearance(self):
        with mock.patch.object(eng_rep_pdf, "clearance_mark",
                               return_value=("mark",)) as mark:
            self.assertEqual(self.win._clearance_mark(), ("mark",))
        mark.assert_called_once_with("hathitrust")

    def test_the_page_opened_outside_is_the_page_cited(self):
        self.assertEqual(self.win._site_url(), sec.page_url(self.SPEC))


def _buttons(widget) -> dict:
    found = {}
    for child in widget.winfo_children():
        if isinstance(child, ttk.Button):
            found[str(child.cget("text"))] = child
        found.update(_buttons(child))
    return found


def _labels(widget) -> list:
    out = []
    for child in widget.winfo_children():
        if isinstance(child, ttk.Label):
            out.append(str(child.cget("text")))
        out.extend(_labels(child))
    return out


@unittest.skipUnless(_display_available(), "needs a display for Tk")
class PanelTests(unittest.TestCase):
    """The panel itself, drawn by Tk: the reader's choice."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def _panel(self, cls, **attrs):
        win = object.__new__(cls)
        win._body = ttk.Frame(self.root)
        win._win, win._status_var = mock.Mock(), mock.Mock()
        win._reveal = mock.Mock()
        win._watch_for_clearance = mock.Mock()
        win._fetch = mock.Mock()
        for name, value in attrs.items():
            setattr(win, name, value)
        return win

    def test_the_sec_panel_offers_firefox_retry_and_the_browser(self):
        spec = sec.make_spec(8, 893, 915)
        url = sec.page_url(spec)
        win = self._panel(gui._SecPdfWindow, _spec=spec)
        win._need_clearance(url)
        win._status_var.set.assert_called_with("HathiTrust needs a CloudFlare check.")
        self.assertIn("HathiTrust is behind a CloudFlare check.", _labels(win._body)[0])
        buttons = _buttons(win._body)
        self.assertEqual(sorted(buttons),
                         ["Open in Firefox", "Open in browser instead", "Retry"])
        win._watch_for_clearance.assert_called_once()

        with mock.patch.object(eng_rep_pdf, "open_in_firefox",
                               return_value=True) as firefox:
            buttons["Open in Firefox"].invoke()
        firefox.assert_called_once_with(url)
        win._status_var.set.assert_called_with(
            "Pass the check in Firefox — the pages load here then.")

        with mock.patch.object(eng_rep_pdf, "open_in_browser") as browser:
            buttons["Open in browser instead"].invoke()
        browser.assert_called_once_with(url)
        win._win.destroy.assert_called_once_with()

    def test_a_firefox_newer_than_curl_cffi_imitates_is_named(self):
        spec = sec.make_spec(10, 200)
        win = self._panel(gui._SecPdfWindow, _spec=spec)
        with mock.patch.object(eng_rep_pdf, "imitated_firefox_major",
                               return_value="147"):
            win._need_clearance(sec.page_url(spec),
                                eng_rep_pdf._user_agent_for("157"))
        text = _labels(win._body)[0]
        self.assertIn("Your Firefox is version 157", text)
        self.assertIn("goes up to version 147", text)
        self.assertIn("open it in your browser instead", text)

    def test_an_old_clearance_refused_asks_for_the_check_again(self):
        spec = sec.make_spec(10, 200)
        win = self._panel(gui._SecPdfWindow, _spec=spec)
        with mock.patch.object(eng_rep_pdf, "imitated_firefox_major",
                               return_value="147"):
            win._need_clearance(sec.page_url(spec),
                                eng_rep_pdf._user_agent_for("145"))
        self.assertIn("pass the check again in Firefox", _labels(win._body)[0])

    def test_retry_fetches_again(self):
        win = self._panel(gui._SecPdfWindow, _spec=sec.make_spec(10, 200))
        win._need_clearance(sec.page_url(sec.make_spec(10, 200)))
        _buttons(win._body)["Retry"].invoke()
        win._fetch.assert_called_once_with()

    def test_without_firefox_it_says_what_to_install(self):
        win = self._panel(gui._SecPdfWindow, _spec=sec.make_spec(10, 200))
        with mock.patch.object(eng_rep_pdf, "firefox_available", return_value=False), \
                mock.patch.object(eng_rep_pdf, "deps_present", return_value=False):
            win._link_out()
        text = _labels(win._body)[0]
        self.assertIn("install Firefox and the curl_cffi package", text)
        self.assertEqual(sorted(_buttons(win._body)), ["Open in browser"])

    def test_the_english_reports_panel_is_as_it_was(self):
        case = SimpleNamespace(web_url="https://www.commonlii.org/uk/cases/EngR/1854/296.html")
        win = self._panel(gui._EngRepPdfWindow, _case=case)
        win._need_clearance(case.web_url)
        win._status_var.set.assert_called_with("CommonLII needs a CloudFlare check.")
        self.assertTrue(_labels(win._body)[0].startswith(
            "CommonLII is behind a CloudFlare check.\n\nTo view this scan in the app"))
        with mock.patch.object(eng_rep_pdf, "open_in_browser") as browser:
            _buttons(win._body)["Open in browser instead"].invoke()
        browser.assert_called_once_with(case.web_url)


if __name__ == "__main__":
    unittest.main()
