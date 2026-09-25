"""English Reports citations: how they are read, and the page they open at.

American opinions cite the old English reports by their reporters' names —
"Rex v. Burton, 1 Strange, 481" (Nash v. United States), "2 Mylne & Craig,
489", "3 Term, 80" — where CommonLII's index of the reprint knows them only by
abbreviation ("Str", "My & Cr", "TR").  Each name is checked against the
English Reports' own table of contents (Wikisource, Portal:The English
Reports).  They also cite pages: a pin after the citation ("156 Eng. Rep. 145,
151"), a short form ("103 Eng. Rep., at 658"), a page of the original report
("9 Exch. 341, 354") — and the scan should open at that page.

And the scans are behind CloudFlare: once the reader has passed its check in
Firefox, the viewer should carry on by itself.
"""

import os
import unittest
from unittest import mock

import citations
import eng_rep
import eng_rep_pdf

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None


def specs(text):
    return [spec for _s, _e, spec in eng_rep.iter_cites(text)]


def names(spec):
    return [c.name for c in eng_rep.resolve(spec)]


class ReportersByNameTests(unittest.TestCase):
    """The reporters as American opinions name them."""

    def test_each_opens_the_case_cited(self):
        for text, spec, name in (
            ("Rex v. Burton, 1 Strange, 481", "n:str:1:481", "Burton"),
            ("Reg. v. Swindall, 2 C. & K. 230", "n:cark:2:230", "Swindall"),
            ("Dobree v. Schroder, 2 Mylne & Craig, 489", "n:mycr:2:489",
             "Dobree"),
            ("Gee v. Pritchard, 2 Swanston, 402", "n:swans:2:402", "Gee"),
            ("Brown v. Davies, 3 Term, 80", "n:tr:3:80", "Davies"),
            ("Bidleson v. Whytel, 3 Burrow, 1545", "n:burr:3:1545",
             "Bildleson"),
            ("Mitchel v. Reynolds, 1 P. Williams, 181", "n:pwms:1:181",
             "Reynolds"),
            ("Tolson v. Kage, (3 Brod. & Bing., 217)", "n:brb:3:217",
             "Tolson"),
            ("Percival v. Frampton, 2 Cromp. Mees. & Rosc. 180",
             "n:crmr:2:180", "Frampton"),
            ("Redie v. Railway Company, 4 Exchequer, 244", "n:exch:4:244",
             ""),
        ):
            with self.subTest(text=text):
                self.assertEqual(specs(text), [spec])
                self.assertTrue(any(name in n for n in names(spec)))

    def test_a_name_with_an_apostrophe_is_read(self):
        # "M'Cle." never matched: the pattern builder mangled the apostrophe.
        eng_rep._load_nominate()
        first = sorted(p for (k, _v, p) in eng_rep._NOM_INDEX if k == "mcle")[0]
        self.assertEqual(specs(f"Smith v. Jones, M'Cle. {first}"),
                         [f"n:mcle:0:{first}"])
        self.assertTrue(specs("Dewitt v. Post, 2 Williams's Saunders, 101"))

    def test_a_case_name_does_not_swallow_the_volume(self):
        # "Hobart" is a reporter too; "Hobart, 3" is turned away, and the
        # citation after it is read whole.
        text = "Ripon v. Hobart, 3 Mylne & Keen, 169,"
        (start, end, spec), = eng_rep.iter_cites(text)
        self.assertEqual((text[start:end], spec),
                         ("3 Mylne & Keen, 169", "n:myk:3:169"))

    def test_nor_does_a_case_name_that_is_a_reporter_s_index_form(self):
        # "Lane" is Lane's Exchequer reports too.
        self.assertEqual(specs("Fisher v. Lane, 3 Wils. 297, in 1772."),
                         ["n:wilskb:3:297"])

    def test_an_american_cite_turned_away_is_not_read_again_short(self):
        # New York's "12 Johns. 220" is no English case; read again without
        # its volume it would be English Johnson's page 220.
        for text in ("Handy vs. Dobbin, 12 Johns. 220, when the proceeding",
                     "In Thellusson vs. Woodford, 4 Ves. Jun. 325, Buller"):
            with self.subTest(text=text):
                self.assertEqual(specs(text), [])

    def test_a_sentence_s_period_is_no_abbreviation(self):
        text = "ordered reargument this Term. 369 U. S. 833. Since"
        self.assertEqual(specs(text), [])
        self.assertEqual(
            [action for _s, _e, action in citations.detect_links(text)],
            [("cite", "369 U.S. 833")])

    def test_the_law_reports_are_not_the_reprint_s_series(self):
        # The Queen's Bench the reprint holds ran 1841-1852; these are the
        # Law Reports'.
        for text in ("Cattle v. Stockton Waterworks Co., 10 Q.B. 453 (1875).",
                     "Osgood v. Nelson, L.R. 5 H.L. 636",
                     "Smith v. Jones, [1891] 1 Q.B. 1"):
            with self.subTest(text=text):
                self.assertEqual(specs(text), [])
        # A year that fits keeps its link.
        self.assertEqual(specs("Hadley v. Baxendale, 9 Exch. 341, 354 (1854)"),
                         ["n:exch:9:341@354"])

    def test_an_edition_s_year_does_not_count_against_it(self):
        # CommonLII files Lane v. Cotton (decided 1701) under 1796, the
        # year of the Modern Reports' edition; only a later date is foreign.
        self.assertEqual(
            specs("Lane v. Cotton, 12 Mod. 472 (K. B. 1701), post, at 305"),
            ["n:mod:12:472"])
        self.assertEqual(
            specs("Harlan v. People, 1 Doug. 207, 212 (Mich. 1843)"), [])

    def test_a_name_with_no_volume_where_a_citation_stands(self):
        # Lord Raymond's volumes are paged straight through.
        self.assertEqual(specs("communis error facit jus. (Lord Raym. 576.)"),
                         ["n:ldraym:1:576"])

    def test_a_name_with_no_volume_only_where_a_citation_stands(self):
        eng_rep._load_nominate()
        first = sorted(p for (k, _v, p) in eng_rep._NOM_INDEX if k == "skin")[1]
        self.assertEqual(specs(f"Rex v. Smith, Skinner, {first}"),
                         [f"n:skin:0:{first}"])
        self.assertEqual(specs(f"B. F. Skinner {first} experiments"), [])


class PinPageTests(unittest.TestCase):
    """The page a citation names travels with it."""

    def test_after_the_reprint_s_citation(self):
        self.assertEqual(
            specs("Hadley v. Baxendale (1854) 156 Eng. Rep. 145, 151"),
            ["156:145@151"])

    def test_after_the_original_report_s(self):
        self.assertEqual(specs("Hadley v. Baxendale, 9 Exch. 341, 354"),
                         ["n:exch:9:341@354"])

    def test_a_short_form_opens_the_case_the_page_is_in(self):
        self.assertEqual(specs("156 Eng. Rep., at 151"), ["156:145@151"])
        self.assertEqual(specs("156 Eng.Rep. at 151"), ["156:145@151"])

    def test_a_short_form_prefers_the_case_the_text_cites(self):
        # 152 is where the case after Hadley begins; Hadley runs onto it.
        after = eng_rep.lookup_nearest(156, 152)[0].page
        self.assertEqual(after, 152)
        self.assertEqual(
            specs("Hadley v. Baxendale, 156 Eng. Rep. 145. Later: 156 Eng. "
                  "Rep., at 152.")[-1],
            "156:145@152")

    def test_the_reprint_cited_as_eng_rep_r(self):
        self.assertEqual(specs("100 Eng.Rep.R. 359"), ["100:359"])

    def test_resolution_ignores_the_pin(self):
        self.assertEqual(names("156:145@151"), names("156:145"))
        self.assertEqual(eng_rep.parse_spec("156:145@151"), (156, 145))
        self.assertEqual(eng_rep.split_pin("n:exch:9:341@354"),
                         ("n:exch:9:341", "354"))

    def test_the_link_detector_carries_it(self):
        text = "Hadley v. Baxendale, 9 Exch. 341, 354, 156 Eng. Rep. 145, 151."
        self.assertEqual(
            [action for _s, _e, action in citations.detect_links(text)],
            [("engrep", "n:exch:9:341@354"), ("engrep", "156:145@151")])


def _pdf(page_texts):
    """A minimal PDF whose pages carry *page_texts* in its text layer — what
    pdfium reads off a scan's OCR layer."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", None,
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in page_texts:
        stream = b"BT /F1 12 Tf 72 700 Td (" + text.encode() + b") Tj ET"
        objects.append(None)
        kids.append(len(objects))
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream"
                       % (len(stream), stream))
        objects[kids[-1] - 1] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
            % (kids[-1] + 1))
    objects[1] = (b"<< /Type /Pages /Kids [%s] /Count %d >>"
                  % (b" ".join(b"%d 0 R" % k for k in kids), len(kids)))
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref))
    return bytes(out)


class ScanPageTests(unittest.TestCase):
    """Which page of a case's scan a pin names."""

    # A case beginning at page 145 of the reprint and at page 341 of the
    # original report, whose margins mark 342, 343, 346 and 348.
    SCAN = _pdf(["145 HADLEY v. BAXENDALE [341] [1896]",
                 "146 9 EXCH. 342. [342] [343]",
                 "147 [346]",
                 "148",
                 "149 [348]"])

    def test_a_page_of_the_reprint(self):
        self.assertEqual(eng_rep_pdf.pin_page(self.SCAN, 145, "147"), 2)
        self.assertIsNone(eng_rep_pdf.pin_page(self.SCAN, 145, "150"))

    def test_a_page_of_the_original_by_its_mark(self):
        self.assertEqual(
            eng_rep_pdf.pin_page(self.SCAN, 145, "343", nominate=True), 1)

    def test_between_the_marks_the_scan_kept(self):
        # 347 is unmarked: between 346 (page 2) and 348 (page 4).
        self.assertEqual(
            eng_rep_pdf.pin_page(self.SCAN, 145, "347", nominate=True), 3)

    def test_nowhere_near_a_mark(self):
        # "[1896]", a year in brackets, is no mark for page 1895.
        self.assertIsNone(
            eng_rep_pdf.pin_page(self.SCAN, 145, "1895", nominate=True))


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class ViewerTests(unittest.TestCase):

    def test_a_pinned_citation_opens_at_its_page(self):
        with mock.patch.object(gui, "_EngRepPdfWindow") as window:
            gui._open_eng_rep(None, "n:exch:9:341@354")
        case = window.call_args.args[1]
        self.assertIn("Baxendale", case.name)
        self.assertEqual(window.call_args.kwargs["pin"], "354")
        self.assertTrue(window.call_args.kwargs["nominate"])

    def test_the_viewer_turns_to_it(self):
        win = object.__new__(gui._EngRepPdfWindow)
        win._pin, win._float, win._pane = "151", mock.Mock(), None
        win._win = mock.Mock()
        win._win.after.side_effect = lambda _ms, fn: fn()
        win._say = mock.Mock()
        win._turn_to_pin(6)
        win._float.scroll_to_page.assert_called_once_with(6)
        win._turn_to_pin(None)
        self.assertIn("isn't marked", win._say.call_args.args[0])

    def test_passing_the_check_in_firefox_loads_the_scan(self):
        win = object.__new__(gui._EngRepPdfWindow)
        win._win = mock.Mock()
        win._post = lambda fn, *args: fn(*args)
        win._say = mock.Mock()
        retry = mock.Mock()

        class Inline:
            def __init__(self, target=None, daemon=None):
                self.target = target

            def start(self):
                self.target()

        marks = iter([("old",), ("old",), ("new",)])
        with mock.patch.object(gui.threading, "Thread", Inline), \
                mock.patch.object(gui.time, "sleep"), \
                mock.patch.object(eng_rep_pdf, "clearance_mark",
                                  side_effect=lambda: next(marks)):
            win._watch_for_clearance(retry)
        retry.assert_called_once_with()

    def test_but_not_once_the_panel_is_gone(self):
        win = object.__new__(gui._EngRepPdfWindow)
        win._win = mock.Mock()
        win._post = lambda fn, *args: fn(*args)
        win._say = mock.Mock()
        retry = mock.Mock()

        class Inline:
            def __init__(self, target=None, daemon=None):
                self.target = target

            def start(self):
                win._clearance_watch = None   # the reader clicked Retry
                self.target()

        with mock.patch.object(gui.threading, "Thread", Inline), \
                mock.patch.object(gui.time, "sleep"), \
                mock.patch.object(eng_rep_pdf, "clearance_mark",
                                  side_effect=[("old",), ("new",)]):
            win._watch_for_clearance(retry)
        retry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
