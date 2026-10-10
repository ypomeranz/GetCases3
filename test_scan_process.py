"""Citation scans in a helper process.

An opinion's citations were read on a worker thread, and the scan — long
stretches inside the regex engine, which holds the GIL throughout — starved
the window: every Tk call it made had to win the GIL back, so the opinion
stalled under the scroll wheel for a tenth of a second at a time while its
citations were read.  The scan now runs in a process of its own
(scan_process), and the thread that asked for it waits on a pipe, holding no
GIL.  Anything wrong with the helper, and the scan runs in-process as before.
"""

import ast
import os
import pathlib
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")

import citations
import courtlistener_gui as gui
import scan_process

HERE = pathlib.Path(__file__).parent
SRC = (HERE / "courtlistener_gui.py").read_text(encoding="utf-8")


def _opinion_text(name="johnson_scholar.html"):
    html = (HERE / "test_data" / name).read_text(encoding="utf-8")
    blocks, _parts = gui._parsed_opinion(html)
    return gui.blocks_to_text(blocks)


class HelperTests(unittest.TestCase):
    """A real helper: started, asked, and stopped."""

    @classmethod
    def setUpClass(cls):
        scan_process.start()
        if not scan_process._ready.wait(60) or not scan_process.is_ready():
            scan_process.stop()
            raise unittest.SkipTest("the helper process could not start here")

    @classmethod
    def tearDownClass(cls):
        scan_process.stop()

    def test_stopped_it_answers_nothing_and_can_be_started_again(self):
        scan_process.stop()
        with self.assertRaises(scan_process.Unavailable):
            scan_process.call("detect_links", "410 U.S. 113")
        scan_process.start()
        # Waits for the new helper rather than failing on the old one.
        self.assertEqual(scan_process.call("detect_links", "410 U.S. 113"),
                         citations.detect_links("410 U.S. 113"))

    def test_it_reads_an_opinion_as_this_process_does(self):
        text = _opinion_text()
        italic = [i % 5 == 0 for i in range(len(text))]
        self.assertEqual(
            scan_process.call("detect_links", text, italic=italic),
            citations.detect_links(text, italic=italic))
        self.assertEqual(scan_process.call("build_short_cite_index", text),
                         citations.build_short_cite_index(text))

    def test_a_scan_that_raises_there_raises_here(self):
        with self.assertRaises(scan_process.HelperError):
            scan_process.call("no_such_scan", "text")
        # …and the helper is still there for the next one.
        self.assertEqual(scan_process.call("detect_links", "410 U.S. 113"),
                         citations.detect_links("410 U.S. 113"))

    def test_a_worker_thread_s_scan_goes_to_it(self):
        here = mock.Mock(side_effect=AssertionError("scanned in-process"))
        out = {}
        worker = threading.Thread(target=lambda: out.update(found=(
            gui._scan_off_thread("detect_links", here, "410 U.S. 113"))))
        worker.start()
        worker.join(60)
        self.assertEqual(out["found"], citations.detect_links("410 U.S. 113"))
        here.assert_not_called()


class RoutingTests(unittest.TestCase):
    def test_the_tk_thread_scans_in_process(self):
        # It must not queue behind a worker's scan in the helper.
        with mock.patch.object(scan_process, "call") as call:
            self.assertEqual(
                gui._scan_off_thread("detect_links", lambda t: ["here"], "x"),
                ["here"])
        call.assert_not_called()

    def test_a_helper_that_cannot_answer_leaves_it_to_this_process(self):
        out = {}
        with mock.patch.object(scan_process, "call",
                               side_effect=scan_process.Unavailable("gone")):
            worker = threading.Thread(target=lambda: out.update(found=(
                gui._scan_off_thread("detect_links", lambda t: ["here"],
                                     "x"))))
            worker.start()
            worker.join(10)
        self.assertEqual(out["found"], ["here"])

    def test_nothing_is_sent_to_a_helper_never_started(self):
        with mock.patch.object(scan_process, "_status", "off"):
            with self.assertRaises(scan_process.Unavailable):
                scan_process.call("detect_links", "x")

    def test_the_whole_document_scans_go_through_it(self):
        # Every caller of the GUI's detect_brief_links — the opinion text's
        # scan, a PDF's text layer's — and of its short-cite index.
        for name, scan in (("detect_brief_links", "detect_links"),
                           ("_build_short_cite_index",
                            "build_short_cite_index")):
            node = next(n for n in ast.parse(SRC).body
                        if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertIn(f'_scan_off_thread("{scan}"',
                          ast.get_source_segment(SRC, node), name)

    def test_the_tk_thread_s_scan_is_this_process_s(self):
        self.assertEqual(gui.detect_brief_links("See 410 U.S. 113, 152."),
                         citations.detect_links("See 410 U.S. 113, 152."))


class EntryPointTests(unittest.TestCase):
    """The packaged program has no other Python to run, so the helper is the
    program itself, told by a switch to be the helper and nothing else."""

    def test_the_switch_is_the_helper_s(self):
        self.assertIn(f'sys.argv[1:2] == ["{scan_process.WORKER_FLAG}"]', SRC)

    def test_it_is_read_before_any_window_or_dependency_check(self):
        switch = SRC.index(scan_process.WORKER_FLAG)
        self.assertLess(switch, SRC.index("import tkinter as tk"))
        self.assertLess(switch, SRC.index("def _ensure_dependencies"))

    def test_the_app_starts_it(self):
        node = next(n for n in ast.parse(SRC).body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        self.assertIn("scan_process.start", ast.get_source_segment(SRC, node))


class LinkSliceTests(unittest.TestCase):
    """Linking a long opinion in place is spread over slices of the Tk
    thread's time, and stops if the opinion is drawn again meanwhile."""

    def _reader(self, slice_s=0.0):
        linked, scheduled = [], []
        reader = SimpleNamespace(
            _location_render_seq=1, _text=object(),
            _LINK_SLICE_S=slice_s,
            _location_block_marks={b: f"m{b}" for b in range(5)},
            _link_block_in_place=lambda txt, mark, block, ranges:
                linked.append(block),
            _win=SimpleNamespace(after=lambda ms, fn, *a:
                                 scheduled.append((fn, a))),
        )
        reader._link_slice = gui._ScholarTextWindow._link_slice.__get__(reader)
        return reader, linked, scheduled

    def _run(self, reader, scheduled):
        while scheduled:
            fn, args = scheduled.pop(0)
            fn(*args)

    def test_every_block_is_linked_in_order(self):
        reader, linked, scheduled = self._reader()
        blocks = {b: f"block{b}" for b in range(5)}
        reader._link_slice(list(range(5)), blocks, {}, 1)
        self.assertTrue(scheduled)          # not all in one go
        self._run(reader, scheduled)
        self.assertEqual(linked, [f"block{b}" for b in range(5)])

    def test_a_new_drawing_stops_it(self):
        reader, linked, scheduled = self._reader()
        blocks = {b: f"block{b}" for b in range(5)}
        reader._link_slice(list(range(5)), blocks, {}, 1)
        reader._location_render_seq = 2    # drawn again: it linked them all
        self._run(reader, scheduled)
        self.assertEqual(linked, ["block0"])

    def test_a_short_opinion_is_linked_at_once(self):
        reader, linked, scheduled = self._reader(slice_s=60.0)
        blocks = {b: f"block{b}" for b in range(5)}
        reader._link_slice(list(range(5)), blocks, {}, 1)
        self.assertEqual(len(linked), 5)
        self.assertEqual(scheduled, [])


if __name__ == "__main__":
    unittest.main()
