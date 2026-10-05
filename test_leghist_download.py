"""A report's PDF downloads however large, as long as it keeps coming.

GovInfo serves a report as one PDF, and some run past 400 MB.  Cut off at
150 MB, a Senate report that was arriving steadily was given up on, and the
reader sent to the Internet Archive.  What ends a download now is its
stalling, or the reader giving up on the load.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import leghist_fetch as lf

MB = 1 << 20


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Response:
    """A streamed download: *chunks* bytes each, the clock moved on by
    *step* seconds before each one arrives."""

    def __init__(self, chunks, clock, step, head=b"%PDF-1.7\n"):
        self.status_code = 200
        self.url = "https://www.govinfo.gov/content/pkg/CRPT/pdf/CRPT.pdf"
        self.headers = {"Content-Length": str(sum(chunks) + len(head))}
        self._chunks, self._clock, self._step = chunks, clock, step
        self._head = head
        self.closed = False

    def iter_content(self, _size):
        yield self._head
        for n in self._chunks:
            self._clock.now += self._step
            yield b"x" * n

    def close(self):
        self.closed = True


class DownloadTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = _Clock()
        self.patches = [
            mock.patch.object(lf, "CACHE_DIR", Path(self.tmp.name)),
            mock.patch.object(lf.time, "monotonic", self.clock),
            mock.patch.object(lf, "stopped", None),
            mock.patch.object(lf, "on_step", None),
            mock.patch.object(lf, "on_bytes", None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _serve(self, response):
        session = mock.Mock()
        session.get.return_value = response
        return mock.patch.object(lf, "_session", return_value=session)

    def _leftovers(self):
        return sorted(p.suffix for p in Path(self.tmp.name).iterdir())

    def test_a_file_that_keeps_coming_is_fetched_whole(self):
        # Twelve megabytes over a minute: slow, but never stalled.
        response = _Response([MB] * 12, self.clock, step=5)
        with self._serve(response):
            data, final = lf._get_pdf("https://www.govinfo.gov/link/x")
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(len(data), 12 * MB + len(b"%PDF-1.7\n"))
        self.assertEqual(final, response.url)
        self.assertTrue(response.closed)
        # Written to the cache as it came, and found there next time.
        self.assertEqual(self._leftovers(), [".json", ".pdf"])
        with self._serve(None):
            again, _ = lf._get_pdf("https://www.govinfo.gov/link/x")
        self.assertEqual(again, data)

    def test_no_size_short_of_the_ceiling_stops_it(self):
        self.assertGreater(lf.MAX_BYTES, 400 * 10 ** 6)

    def test_a_download_that_stalls_is_given_up(self):
        # 10 KB every ten seconds: 30 KB in half a minute.
        response = _Response([10_000] * 10, self.clock, step=10)
        with self._serve(response), \
                self.assertRaisesRegex(lf.Unavailable, "stalled"):
            lf._get_pdf("https://www.govinfo.gov/link/x")
        self.assertEqual(self._leftovers(), [])     # no half-file kept

    def test_giving_up_on_the_load_stops_the_download(self):
        response = _Response([MB] * 5, self.clock, step=1)
        with self._serve(response), \
                mock.patch.object(lf, "stopped", lambda: True), \
                self.assertRaisesRegex(lf.Unavailable, "stopped"):
            lf._get_pdf("https://www.govinfo.gov/link/x")
        self.assertEqual(self._leftovers(), [])

    def test_and_no_other_copy_is_looked_for(self):
        spec = {"src": "rpt", "ch": "s", "cong": 95, "num": 797}
        with mock.patch.object(lf, "_get_pdf",
                               side_effect=lf.Unavailable("stopped")), \
                mock.patch.object(lf, "stopped", lambda: True), \
                mock.patch.object(lf, "_internet_archive") as archive, \
                self.assertRaises(lf.Unavailable):
            lf._paper(spec)
        archive.assert_not_called()

    def test_a_page_that_is_no_pdf_is_not_kept(self):
        response = _Response([1000], self.clock, step=1, head=b"<html>")
        with self._serve(response), \
                self.assertRaisesRegex(lf.Unavailable, "not return a PDF"):
            lf._get_pdf("https://www.govinfo.gov/link/x")
        self.assertEqual(self._leftovers(), [])


if __name__ == "__main__":
    unittest.main()
