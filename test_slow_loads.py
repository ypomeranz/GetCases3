"""A document slow to come says so — where it will open — and holds nothing
else up.

A load that takes more than a moment gets a window of its own
(``_LoadWatch``), standing where the document will open: what is being tried
and what has been, how much of the file has come in, how long it has taken,
and — when nothing can be found — that it failed, and why.  The document
opens in that window's place.  Reports reach it from the load's own thread
(``_load_step``, ``_load_bytes``) without being handed anything, and nothing
the load does holds up another: the pages are measured on the worker thread
rather than the Tk thread every window shares, and a Supreme Court volume
downloading does not keep another volume from downloading beside it.
"""

import io
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import courtlistener_gui as gui
import leghist_fetch
import legislative_history as lh
import us_reports_pdf
from courtlistener_gui import (
    CourtListenerGUI,
    _LoadWatch,
    _ScholarTextWindow,
    _load_bytes,
    _load_step,
    _load_watched,
    _watching,
)

try:
    import pypdfium2  # noqa: F401
    from PIL import Image
    HAVE_PDFIUM = True
except ImportError:  # pragma: no cover - the in-app viewer's own requirement
    HAVE_PDFIUM = False


class FakeRoot:
    """The Tk root as a watch uses it: ``after`` queues, ``run`` runs what is
    due, as the event loop would."""

    def __init__(self):
        self.queue: list = []       # [ms, fn, args, id]
        self.cancelled: set = set()
        self._n = 0
        self._lock = threading.Lock()

    def after(self, ms, fn, *args):
        with self._lock:
            self._n += 1
            after_id = f"after#{self._n}"
            self.queue.append([ms, fn, args, after_id])
        return after_id

    def after_cancel(self, after_id):
        self.cancelled.add(after_id)

    def run(self, upto_ms=0):
        """Run everything due within *upto_ms* — 0: what was posted to run at
        once — including what that posts in turn."""
        while True:
            with self._lock:
                due = [e for e in self.queue if e[0] <= upto_ms]
                for entry in due:
                    self.queue.remove(entry)
            if not due:
                return
            for _ms, fn, args, after_id in due:
                if after_id not in self.cancelled:
                    fn(*args)

    def focus_get(self):
        return None


class FakeApp:
    def __init__(self, root=None):
        self.root = root or FakeRoot()
        self._load_watches: list = []

    def _load_watch_ended(self, watch):
        CourtListenerGUI._load_watch_ended(self, watch)


def watch(app=None, **kw):
    app = app or FakeApp()
    w = _LoadWatch(app, None, "Roe v. Wade, 410 U.S. 113",
                   cite="410 U.S. 113", **kw)
    app._load_watches.append(w)
    return w


class WatchTests(unittest.TestCase):
    """What a watch knows and does, before any window is drawn."""

    def test_a_quick_load_never_shows_a_window(self):
        app = FakeApp()
        w = watch(app)
        self.assertIsNone(w.hand_off())          # it came in a moment
        self.assertTrue(w.done)
        # The pending "show the window" is cancelled, and running the clock
        # on shows nothing.
        with mock.patch.object(_LoadWatch, "_build") as build:
            app.root.run(upto_ms=10 ** 6)
        build.assert_not_called()
        self.assertEqual(app._load_watches, [])

    def test_a_slow_one_shows_after_the_threshold(self):
        app = FakeApp()
        w = watch(app)
        self.assertEqual(app.root.queue[0][0], _LoadWatch.SLOW_MS)
        self.assertEqual(app.root.queue[0][1], w._show)

    def test_steps_from_the_load_s_thread_arrive_through_the_event_loop(self):
        app = FakeApp()
        w = watch(app)

        def load():
            with _watching(w):
                _load_step("Looking 410 U.S. 113 up on CourtListener…")
                _load_step("Checking the Library of Congress's scan…")

        t = threading.Thread(target=load)
        t.start()
        t.join()
        self.assertEqual(w._now, "")             # nothing touched off-thread
        app.root.run()
        self.assertEqual(w._now, "Checking the Library of Congress's scan…")
        self.assertEqual(w._steps,
                         ["Looking 410 U.S. 113 up on CourtListener…"])

    def test_a_thread_no_load_is_watched_on_reports_to_nothing(self):
        self.assertFalse(_load_watched())
        _load_step("nobody hears this")
        _load_bytes(1, 2)
        w = watch()
        with _watching(w):
            self.assertTrue(_load_watched())
        self.assertFalse(_load_watched())

    def test_byte_counts_are_thinned_but_the_last_always_arrives(self):
        app = FakeApp()
        w = watch(app)
        w.received(10, 1000)
        w.received(20, 1000)                    # too soon after the first
        w.received(1000, 1000)                  # complete: always told
        told = [e[2] for e in app.root.queue if e[1] == w._set_bytes]
        self.assertEqual(told, [(10, 1000), (1000, 1000)])
        app.root.run()
        self.assertEqual(w._bytes, (1000, 1000))

    def test_a_failure_before_the_window_shows_is_left_to_the_status_line(self):
        app = FakeApp()
        w = watch(app)
        w.fail("No scan of it could be found.")
        self.assertTrue(w.done)
        self.assertFalse(w.failed)              # nothing was shown to fail
        self.assertEqual(app._load_watches, [])

    def test_stop_waiting_gives_up_the_scan(self):
        w = watch()
        w._stop_clicked()
        self.assertTrue(w.cancelled and w.done)
        self.assertIsNone(w.hand_off())

    def test_but_closes_a_wait_for_text_without_giving_it_up(self):
        # The text opens in a window of its own either way.
        app = FakeApp()
        w = watch(app)
        w.to_text("No scan of it could be found — looking for its text "
                  "instead…")
        self.assertEqual(w.phase, "text")
        self.assertEqual(w._now, "No scan of it could be found — looking for "
                                 "its text instead…")
        w._stop_clicked()
        self.assertFalse(w.cancelled)
        self.assertTrue(w.done)


class RegistryTests(unittest.TestCase):
    """How a load waiting on its text is found by what ends it."""

    def app(self):
        app = FakeApp()
        for name in ("watch_load", "claim_text_load", "_take_text_watch"):
            setattr(app, name, getattr(CourtListenerGUI, name).__get__(app))
        return app

    def test_a_text_lookup_takes_up_the_load_waiting_on_its_citation(self):
        app = self.app()
        parent = SimpleNamespace(winfo_toplevel=lambda: "win1")
        w = app.watch_load(parent, "Roe", cite="410 U.S. 113")
        self.assertIsNone(app.claim_text_load(parent, "410 U.S. 113"))
        w.to_text("looking for the text")
        self.assertIs(app.claim_text_load(parent, "410 U.S. 113"), w)
        self.assertTrue(w.claimed)
        # Once, and only by a lookup from the same window, for the same case.
        self.assertIsNone(app.claim_text_load(parent, "410 U.S. 113"))

    def test_not_by_a_lookup_from_another_window(self):
        app = self.app()
        w = app.watch_load(SimpleNamespace(winfo_toplevel=lambda: "win1"),
                           "Roe", cite="410 U.S. 113")
        w.to_text("looking")
        self.assertIsNone(app.claim_text_load(
            SimpleNamespace(winfo_toplevel=lambda: "win2"), "410 U.S. 113"))

    def test_how_it_ended_is_told_to_the_load(self):
        w = watch()
        w.fail = mock.Mock()
        w.finish = mock.Mock()
        CourtListenerGUI.end_text_load(w, "Its text could not be found either.")
        w.fail.assert_called_once_with("Its text could not be found either.")
        CourtListenerGUI.end_text_load(w)
        w.finish.assert_called_once_with()
        CourtListenerGUI.end_text_load(None, "nothing to tell")


def _display_available() -> bool:
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


@unittest.skipUnless(_display_available(), "needs a display for Tk")
class WatchWindowTests(unittest.TestCase):
    """The window itself, drawn by Tk."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = SimpleNamespace(root=self.root, _load_watches=[],
                                   _load_watch_ended=lambda w: None)

    def tearDown(self):
        self.root.destroy()

    def shown(self):
        w = _LoadWatch(self.app, None, "Roe v. Wade, 410 U.S. 113",
                       cite="410 U.S. 113")
        w._show()
        self.root.update_idletasks()
        return w

    def test_it_is_named_for_the_document_and_says_it_is_under_way(self):
        w = self.shown()
        self.assertEqual(w._win.title(), "Opening Roe v. Wade, 410 U.S. 113")
        self.assertEqual(w._vars["heading"].get(), "Opening")
        self.assertEqual(w._vars["now"].get(), "Getting started…")
        self.assertIn("It will open in this window", w._vars["footer"].get())
        self.assertEqual(str(w._button.cget("text")), "Stop waiting")
        w.finish()

    def test_it_says_what_is_being_tried_and_what_has_been(self):
        w = self.shown()
        w._set_step("Looking 410 U.S. 113 up on CourtListener…")
        w._set_step("Checking the Library of Congress's scan…")
        self.assertEqual(w._vars["now"].get(),
                         "Checking the Library of Congress's scan…")
        self.assertEqual(w._vars["tried_head"].get(), "Tried so far")
        self.assertIn("Looking 410 U.S. 113 up on CourtListener…",
                      w._vars["tried"].get())
        w.finish()

    def test_and_how_much_of_the_file_has_come_in(self):
        w = self.shown()
        w._set_step("Downloading from the Library of Congress…")
        w._set_bytes(1_700_000, 3_400_000)
        self.assertEqual(w._vars["bytes"].get(), "1.7 MB of 3.4 MB (50%)")
        self.assertEqual(str(w._bar.cget("mode")), "determinate")
        self.assertAlmostEqual(float(w._bar.cget("value")), 50.0)
        w._set_bytes(420_000, 0)                 # a server that did not say
        self.assertEqual(w._vars["bytes"].get(), "420 KB so far")
        self.assertEqual(str(w._bar.cget("mode")), "indeterminate")
        w.finish()

    def test_a_failure_stays_up_saying_why_until_closed(self):
        w = self.shown()
        w._set_step("Checking static.case.law's scan…")
        w.fail("No scan of it could be found.")
        self.assertTrue(w._win.winfo_exists())
        self.assertEqual(w._vars["heading"].get(), "Couldn't open")
        self.assertEqual(w._vars["now"].get(), "No scan of it could be found.")
        self.assertIn("static.case.law", w._vars["tried"].get())
        self.assertEqual(str(w._button.cget("text")), "Close")
        w._stop_clicked()
        self.assertIsNone(w._win)
        self.assertFalse(w.cancelled)            # nothing left to give up

    def test_a_failure_says_when_it_stopped(self):
        w = self.shown()
        w.fail("No scan of it could be found.")
        self.assertTrue(w._vars["elapsed"].get().startswith("Stopped after"))
        w._stop_clicked()

    def test_the_app_can_quit_with_a_failure_still_up(self):
        # A timer cancelled through another widget than the one that set it
        # left its name behind, and destroying the window — the root's
        # destroy() on quitting — failed on it.
        w = self.shown()
        w._tick()
        w.fail("No scan of it could be found.")
        self.root.update()
        self.root.destroy()             # must not raise
        self.root = tk.Tk()             # for tearDown

    def test_the_document_opens_where_it_stood(self):
        w = self.shown()
        w._win.geometry("700x800+123+45")
        self.root.update_idletasks()
        spec = w.hand_off()
        self.assertTrue(spec.startswith("700x800"), spec)
        self.assertIsNone(w._win)

    def test_waiting_on_text_it_changes_its_words(self):
        w = self.shown()
        w.to_text("No scan of it could be found — looking for its text "
                  "instead…")
        self.assertEqual(w._vars["heading"].get(), "Opening the text of")
        self.assertEqual(str(w._button.cget("text")), "Close")
        w.finish()


class DownloadProgressTests(unittest.TestCase):
    class Recorder:
        def __init__(self):
            self.steps, self.bytes = [], []

        def step(self, text):
            self.steps.append(text)

        def received(self, done, total):
            self.bytes.append((done, total))

    def response(self, chunks, length=None):
        headers = {} if length is None else {"Content-Length": str(length)}
        resp = mock.Mock(url="https://tile.loc.gov/x.pdf", headers=headers)
        resp.iter_content.return_value = iter(chunks)
        resp.content = b"".join(chunks)
        return resp

    def test_a_watched_download_counts_what_has_come(self):
        rec = self.Recorder()
        resp = self.response([b"%PDF-", b"12345", b"67890"], length=15)
        with _watching(rec):
            body = gui._read_pdf_body(resp)
        self.assertEqual(body, b"%PDF-1234567890")
        self.assertEqual(rec.bytes[0], (0, 15))
        self.assertEqual(rec.bytes[-1], (15, 15))

    def test_an_unwatched_one_is_read_as_it_always_was(self):
        resp = self.response([b"%PDF-x"])
        self.assertEqual(gui._read_pdf_body(resp), b"%PDF-x")
        resp.iter_content.assert_not_called()

    def test_the_fetch_streams_only_when_someone_is_watching(self):
        asked = []

        def get(url, client=None, timeout=30, stream=False):
            asked.append(stream)
            return self.response([b"%PDF-1.4 body"], length=13)

        rec = self.Recorder()
        with mock.patch.object(gui, "_pdf_get", side_effect=get), \
                mock.patch.object(gui, "_clean_reporter_pdf",
                                  side_effect=lambda d, keep_cover=False: d):
            gui._fetch_pdf_bytes("https://tile.loc.gov/x.pdf")
            with _watching(rec):
                gui._fetch_pdf_bytes("https://tile.loc.gov/x.pdf")
        self.assertEqual(asked, [False, True])
        self.assertEqual(rec.steps,
                         ["Downloading from the Library of Congress…"])
        self.assertEqual(rec.bytes[-1], (13, 13))

    def test_sources_are_named_as_readers_know_them(self):
        name = gui._source_name
        self.assertEqual(name("https://tile.loc.gov/storage/x.pdf"),
                         "the Library of Congress")
        self.assertEqual(name("https://www.govinfo.gov/link/x"), "GovInfo")
        self.assertEqual(name("https://static.case.law/us/410/x.pdf"),
                         "static.case.law")
        self.assertEqual(name("https://www.example.org/x.pdf"), "example.org")

    def test_and_so_are_the_copies_the_resolver_checks(self):
        text = gui._resolve_step_text
        self.assertEqual(text("LOC US Reports", ""),
                         "Checking the Library of Congress's scan of the U.S. "
                         "Reports…")
        self.assertEqual(text("GovInfo direct PDF", ""),
                         "Checking GovInfo's scan of the U.S. Reports…")
        self.assertEqual(text("local_path (opinion record)", ""),
                         "Checking CourtListener's stored copy…")
        self.assertEqual(
            text("download_url (search result)",
                 "https://www.ca9.uscourts.gov/x.pdf"),
            "Checking the court's own copy (ca9.uscourts.gov)…")

    def test_sizes(self):
        self.assertEqual(gui._size_label(840_000), "840 KB")
        self.assertEqual(gui._size_label(3_400_000), "3.4 MB")
        self.assertEqual(gui._size_label(10), "1 KB")


@unittest.skipUnless(HAVE_PDFIUM, "pypdfium2 and Pillow not installed")
class PageMeasurementTests(unittest.TestCase):
    """The measuring a pane used to do on the Tk thread, done on the load's."""

    def pdf(self, pages=3):
        imgs = [Image.new("RGB", (306, 396), "white") for _ in range(pages)]
        buf = io.BytesIO()
        imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:],
                     resolution=36)
        return buf.getvalue()

    def test_every_page_is_measured(self):
        meta = gui._measure_pdf_pages(self.pdf(3))
        self.assertEqual(len(meta), 3)
        w_pt, h_pt, frac = meta[0]
        self.assertAlmostEqual(w_pt, 612, delta=1)
        self.assertAlmostEqual(h_pt, 792, delta=1)
        self.assertEqual(len(frac), 4)

    def test_what_cannot_be_measured_is_left_to_the_pane(self):
        self.assertIsNone(gui._measure_pdf_pages(b"not a pdf"))

    def test_the_pane_takes_measurements_it_is_handed(self):
        src = Path(gui.__file__).read_text(encoding="utf-8")
        self.assertIn("if meta is not None and len(meta) == count:", src)
        self.assertIn("meta=page_meta)", src)


class VolumeDownloadTests(unittest.TestCase):
    """Two volumes of the U.S. Reports download side by side; one volume
    asked for twice downloads once."""

    def test_each_volume_has_its_own_lock(self):
        self.assertIs(us_reports_pdf._volume_lock(590),
                      us_reports_pdf._volume_lock(590))
        self.assertIsNot(us_reports_pdf._volume_lock(590),
                         us_reports_pdf._volume_lock(591))

    def test_a_slow_volume_does_not_hold_up_another(self):
        release = threading.Event()
        started = threading.Event()
        got: dict = {}

        def download(url, dest):
            if "592" in dest.name:
                started.set()
                release.wait(5)            # the slow one
            dest.write_bytes(b"%PDF-1.4")
            return True

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(us_reports_pdf, "US_REPORTS_DIR",
                                  Path(folder)), \
                mock.patch.object(us_reports_pdf, "_download",
                                  side_effect=download):
            slow = threading.Thread(
                target=lambda: got.setdefault(
                    592, us_reports_pdf.ensure_volume(592)))
            slow.start()
            self.assertTrue(started.wait(5))
            t0 = time.monotonic()
            quick = us_reports_pdf.ensure_volume(593)
            elapsed = time.monotonic() - t0
            release.set()
            slow.join(5)
        self.assertTrue(quick)
        self.assertLess(elapsed, 2.0)        # not waiting on volume 592
        self.assertTrue(got.get(592))

    def test_a_download_says_what_it_is_doing(self):
        steps, counts = [], []
        resp = mock.MagicMock(status_code=200,
                              headers={"Content-Length": "12"})
        resp.__enter__.return_value = resp
        resp.iter_content.return_value = iter([b"%PDF-1.4", b"body"])
        session = mock.Mock()
        session.get.return_value = resp
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(us_reports_pdf, "_get_session",
                                  return_value=session), \
                mock.patch.object(us_reports_pdf, "on_step", steps.append), \
                mock.patch.object(us_reports_pdf, "on_bytes",
                                  lambda d, t: counts.append((d, t))):
            ok = us_reports_pdf._download("https://x/590BV.pdf",
                                          Path(folder) / "590BV.pdf")
        self.assertTrue(ok)
        self.assertEqual(steps, ["Downloading 590BV.pdf from "
                                 "supremecourt.gov…"])
        self.assertEqual(counts[-1], (12, 12))

    def test_the_gui_hears_the_downloads_through_the_watched_thread(self):
        self.assertIs(us_reports_pdf.on_step, gui._load_step)
        self.assertIs(us_reports_pdf.on_bytes, gui._load_bytes)
        self.assertIs(leghist_fetch.on_step, gui._load_step)
        self.assertIs(leghist_fetch.on_bytes, gui._load_bytes)


class LegislativeHistorySourcesTests(unittest.TestCase):
    def test_each_source_tried_for_a_report_is_said(self):
        steps = []
        spec = lh.make_spec(src="rpt", ch="s", cong=95, num=797)
        with mock.patch.object(leghist_fetch, "on_step", steps.append), \
                mock.patch.object(leghist_fetch, "_get_pdf",
                                  side_effect=leghist_fetch.Unavailable("x")), \
                mock.patch.object(leghist_fetch, "_internet_archive",
                                  return_value=None), \
                mock.patch.object(leghist_fetch, "_hathitrust",
                                  return_value=""):
            with self.assertRaises(leghist_fetch.Unavailable):
                leghist_fetch.fetch(spec)
        self.assertEqual(steps, ["Asking GovInfo for it…",
                                 "Searching the Internet Archive…",
                                 "Searching HathiTrust's catalogue…"])


class TextFallbackReportingTests(unittest.TestCase):
    """A case with no scan falls back to its text; the load waiting on that
    hears how the text lookup ends."""

    def test_a_brief_s_lookup_that_finds_nothing_says_so(self):
        watch_ = mock.Mock()
        app = mock.Mock()
        app._following_as_text = True
        app.claim_text_load.return_value = watch_
        app._token_var.get.return_value = "token"
        app._try_open_citation.return_value = False
        parent = mock.Mock()
        parent.after.side_effect = lambda ms, fn: fn()
        with mock.patch.object(gui.threading, "Thread",
                               lambda target, daemon=None: SimpleNamespace(
                                   start=target)):
            gui._follow_brief_action(app, parent, ("cite", "5 Johns. 37@40"),
                                     lambda _s: None, snippet="Kilburn")
        app.claim_text_load.assert_called_once_with(parent, "5 Johns. 37")
        app.end_text_load.assert_called_once_with(
            watch_, "Its text could not be found either.")

    def test_and_one_that_opened_it_ends_the_wait(self):
        app = mock.Mock()
        app._following_as_text = True
        app._token_var.get.return_value = "token"
        app._try_open_citation.return_value = True
        parent = mock.Mock()
        parent.after.side_effect = lambda ms, fn: fn()
        with mock.patch.object(gui.threading, "Thread",
                               lambda target, daemon=None: SimpleNamespace(
                                   start=target)):
            gui._follow_brief_action(app, parent, ("cite", "5 Johns. 37"),
                                     lambda _s: None)
        app.end_text_load.assert_called_once_with(
            app.claim_text_load.return_value, "")

    def test_nothing_is_claimed_when_it_is_no_fallback(self):
        app = mock.Mock()
        app._following_as_text = False
        app.open_cited_case_pdf.return_value = True
        gui._follow_brief_action(app, mock.Mock(), ("cite", "5 Johns. 37"),
                                 lambda _s: None)
        app.claim_text_load.assert_not_called()

    def text_window(self):
        win = object.__new__(_ScholarTextWindow)
        win._app = mock.Mock()
        win._win = mock.Mock()
        win._status_var = mock.Mock()
        win._live_parent = lambda: win._win
        return win

    def test_the_text_window_hears_how_its_lookup_ended(self):
        win = self.text_window()
        waiting = win._app.claim_text_load.return_value
        win._claim_text_load("5 Johns. 37")
        win._on_cl_link_error("No match for 5 Johns. 37.", "5 Johns. 37")
        win._app.end_text_load.assert_called_once_with(
            waiting, "No match for 5 Johns. 37.")
        # Told once: the load is no longer this window's to tell.
        win._on_cl_link_error("again", "5 Johns. 37")
        self.assertEqual(win._app.end_text_load.call_count, 1)

    def test_a_lookup_nobody_waits_on_tells_nobody(self):
        win = self.text_window()
        win._on_cl_link_error("No match.", "5 Johns. 37")
        win._app.end_text_load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
