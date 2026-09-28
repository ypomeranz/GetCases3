"""Legislative history: the Congressional Record and the debates before it,
committee reports and documents — read as opinions cite them, found in the
shipped indexes, and fetched from GovInfo, Congress.gov and, failing
those, the Internet Archive (with HathiTrust left to the browser).

The citations below are taken from opinions in the database.
"""

import ast
import io
import json
import pathlib
import unittest
from unittest.mock import Mock, patch

import citations
import courtlistener_gui
import legislative_history as lh
import leghist_fetch
import pdf_range


def specs(text):
    return [(text[s:e], lh.parse_spec(spec)) for s, e, spec in lh.iter_cites(text)]


def labels(text):
    return [(hit, lh.spec_label(spec)) for hit, spec in specs(text)]


class CongressionalRecordTests(unittest.TestCase):
    def test_bound_edition(self):
        self.assertEqual(labels("116 Cong.Rec. 36481 (1970) (emphasis added)."),
                         [("116 Cong.Rec. 36481", "116 Cong. Rec. 36,481 (1970)")])
        hit, spec = specs("124 Cong. Rec. 34,005 (1978) (statement of Sen. DeConcini)")[0]
        self.assertEqual((spec["vol"], spec["page"], spec.get("daily")), (124, "34005", None))

    def test_a_range_and_later_pages_of_the_volume(self):
        found = specs("See 93 Cong. Rec. 4769-4770, 4833-4847, 4858-4875 (1947).")
        self.assertEqual([h for h, _ in found], ["93 Cong. Rec. 4769-4770", "4833-4847", "4858-4875"])
        self.assertEqual([s["page"] for _, s in found], ["4769", "4833", "4858"])

    def test_a_later_number_that_is_no_page(self):
        # "60th Cong." and the U.S.C.C.A.N.'s year are not pages of the Record.
        self.assertEqual(len(specs("59 Cong. Rec. 4482, 60th Cong., 2d Sess. (1920).")), 1)
        found = specs("120 Cong. Rec. 33848, 33850-33851 (1974).")
        self.assertEqual([s["page"] for _, s in found], ["33848", "33850"])

    def test_daily_edition(self):
        hit, spec = specs("148 Cong. Rec. S2101 (Mar. 20, 2002) (statement of Sen. Feingold)")[0]
        self.assertEqual((spec["page"], spec["daily"], spec["date"]), ("S2101", True, "2002-03-20"))
        self.assertEqual(lh.spec_label(spec), "148 Cong. Rec. S2101 (daily ed. Mar. 20, 2002)")
        hit, spec = specs("155 Cong. Rec. S11,964 (Nov. 21, 2009).")[0]
        self.assertEqual(spec["page"], "S11964")

    def test_an_earlier_daily_edition_needs_its_date(self):
        # Before 1995 the daily edition isn't online; only the date finds the
        # day in the bound edition.
        _hit, spec = specs("120 Cong.Rec. H10333 (daily ed., Oct. 10, 1974).")[0]
        self.assertEqual((spec["vol"], spec["date"]), (120, "1974-10-10"))
        self.assertEqual(specs("93 Cong. Rec. 3734, 6540 (daily ed. 1947)."), [])
        self.assertEqual(specs("140 Cong. Rec. H10752, H10765 (1994)"), [])

    def test_a_dated_daily_edition_without_its_volume(self):
        _hit, spec = specs("Cong.Rec. S 18891 (daily ed. Nov. 17, 1971).")[0]
        self.assertEqual((spec["vol"], spec["page"]), (117, "S18891"))

    def test_older_forms(self):
        _hit, spec = specs("See 67 Cong. Rec., Part 1, pp. 735, 752.")[0]
        self.assertEqual((spec["vol"], spec["page"]), (67, "735"))
        _hit, spec = specs("Cong. Rec. vol. 21, part 6, p. 5950.")[0]
        self.assertEqual((spec["vol"], spec["page"]), (21, "5950"))

    def test_a_volume_govinfo_has_not_bound(self):
        self.assertEqual(specs("170 Cong. Rec. 1234 (2024)"), [])


class DebatesTests(unittest.TestCase):
    def test_globe(self):
        self.assertEqual(labels("Cong. Globe, 39th Cong., 1st Sess., 2765 (1866)"),
                         [("Cong. Globe, 39th Cong., 1st Sess., 2765",
                           "Cong. Globe, 39th Cong., 1st Sess. 2765 (1866)")])
        _hit, spec = specs("Cong. Globe, 31st Cong., 1st sess., App. 1043.")[0]
        self.assertEqual((spec["cong"], spec["sess"], spec["page"], spec["app"]), (31, 1, 1043, True))
        _hit, spec = specs("Cong. Globe, 42d Cong, 1st Sess., 374 (1871)")[0]
        self.assertEqual((spec["cong"], spec["page"]), (42, 374))

    def test_globe_forms(self):
        # McDonald v. Chicago's "39th Cong. Globe 1088"; the Globe's session
        # before its Congress; a typo'd "Cong., Globe".
        _hit, spec = specs("39th Cong. Globe 1088.")[0]
        self.assertEqual((spec["cong"], spec["sess"], spec["page"]), (39, 1, 1088))
        _hit, spec = specs("Cong. Globe, 1st Sess., 42d Cong., at 575;")[0]
        self.assertEqual((spec["cong"], spec["sess"], spec["page"]), (42, 1, 575))
        _hit, spec = specs("See, e. g., Cong., Globe, 42d Cong., 1st Sess., App. 166-167.")[0]
        self.assertEqual((spec["page"], spec["app"]), (166, True))

    def test_globe_short_forms_follow_the_full_cite(self):
        text = ("Cong. Globe, 42d Cong., 1st Sess., 749 (1871) (hereinafter Globe). "
                "See Globe 761 (Sen. Sherman); Cong. Globe 804; id., at App. 68.")
        found = specs(text)
        self.assertEqual([(s["cong"], s["page"], bool(s.get("app"))) for _h, s in found],
                         [(42, 749, False), (42, 761, False), (42, 804, False), (42, 68, True)])
        # With no full cite before it, "Globe 799" is some other Globe.
        self.assertEqual(specs("the Globe 799 times"), [])
        self.assertEqual(specs("Globe Newspaper Co. v. Superior Court, 457 U.S. 596"), [])

    def test_globe_later_pages(self):
        found = specs("Cong. Globe, 40th Cong., 2d Sess., 968, 1129-1131.")
        self.assertEqual([s["page"] for _h, s in found], [968, 1129])

    def test_annals(self):
        self.assertEqual(labels("1 Annals of Cong. 434 (1789)"),
                         [("1 Annals of Cong. 434", "1 Annals of Cong. 434 (1789)")])
        _hit, spec = specs("I Annals of Congress, 432, 761")[0]
        self.assertEqual((spec["vol"], spec["page"]), (1, 432))
        self.assertEqual(specs("298 Annals Am. Acad. Pol. & Soc. Sci. 57 (1955)"), [])
        self.assertEqual(specs("67 Annals Internal Med. Supp. 7"), [])

    def test_register_of_debates(self):
        self.assertEqual(labels("11 Cong. Deb. 518 (1835)"),
                         [("11 Cong. Deb. 518", "11 Reg. Deb. 518 (1835)")])
        _hit, spec = specs("13 Cong.Deb. Part 2, App. at 202 (1837)")[0]
        self.assertEqual((spec["vol"], spec["part"], spec["app"], spec["page"]), (13, 2, True, 202))


class ReportTests(unittest.TestCase):
    def test_modern_form(self):
        self.assertEqual(labels("S. Rep. No. 95-370, p. 41 (1977)"),
                         [("S. Rep. No. 95-370, p. 41", "S. Rep. No. 95-370, at 41 (1977)")])
        _hit, spec = specs("H.R.Rep. No. 109-478, at 21.")[0]
        self.assertEqual((spec["ch"], spec["cong"], spec["num"], spec["pin"]), ("h", 109, 478, "21"))
        _hit, spec = specs("S. Rep. No. 101— 545, p. 37 (1990)")[0]
        self.assertEqual((spec["cong"], spec["num"]), (101, 545))

    def test_older_form(self):
        _hit, spec = specs("H. R. Rep. No. 1980, 79th Cong., 2d Sess., 44 (1946)")[0]
        self.assertEqual((spec["cong"], spec["num"], spec["pin"], spec["year"]), (79, 1980, "44", 1946))
        _hit, spec = specs("S. Rep. No. 1177, 79th Cong., 2d Sess. 8.")[0]
        self.assertEqual(spec["pin"], "8")
        _hit, spec = specs("S. Rep. No. 6, 83d Cong., 1st Sess., pt. 1, pp. 64-65 (1953)")[0]
        self.assertEqual((spec["part"], spec["pin"]), (1, "64"))
        _hit, spec = specs("S. Rep. No. 626, on S. 3151, 70th Cong., 1st Sess. (1928)")[0]
        self.assertEqual(spec["cong"], 70)

    def test_conference_reports(self):
        self.assertEqual(labels("H.R. Conf. Rep. No. 103-711, at 385 (1994)")[0][1],
                         "H.R. Rep. No. 103-711, at 385 (1994) (Conf. Rep.)")
        _hit, spec = specs("H. R. Rep. No. 510 (Conference Report), 80th Cong., 1st Sess. 42 (1947)")[0]
        self.assertEqual((spec["conf"], spec["cong"], spec["pin"]), (True, 80, "42"))

    def test_a_short_form_takes_the_full_cites_congress(self):
        found = specs("S. Rep. No. 1580, 88th Cong., 2d Sess. (1964). ... S. Rep. No. 1580, at 9")
        self.assertEqual([(s["cong"], s.get("pin")) for _h, s in found], [(88, None), (88, "9")])

    def test_hereinafter_and_supra(self):
        text = ("H. R. Rep. No. 94-1476, p. 51 (1976) (emphasis added) (hereinafter H. R. Rep.). "
                "The key is H. R. Rep., at 57; and S. Rep. No. 98-225, p. 3 (1983). "
                "S.Rep., supra, at 27.")
        found = specs(text)
        got = [(h, s["num"], s.get("pin")) for h, s in found]
        self.assertIn(("H. R. Rep., at 57", 1476, "57"), got)
        self.assertIn(("S.Rep., supra, at 27", 225, "27"), got)

    def test_the_reprint_opens_the_report(self):
        found = specs("S. Rep. No. 98-225, at 3 (1983), reprinted in 1984 U.S.C.C.A.N. 3182, 3185.")
        self.assertEqual([h for h, _s in found],
                         ["S. Rep. No. 98-225, at 3", "1984 U.S.C.C.A.N. 3182, 3185"])
        self.assertEqual(found[1][1]["num"], 225)
        self.assertNotIn("pin", found[1][1])

    def test_not_reports(self):
        # A page of a report named "S. Rep." earlier, pages of a 1998
        # report, a Representative, a bill.
        self.assertEqual(specs("S. Rep. 20. Yet this interest is not unyielding"), [])
        self.assertEqual(specs("1 1998 Senate Report 43-44; 2 id., at 2907"), [])
        self.assertEqual(specs("(remarks of Rep. Udall) and H.R. 1234"), [])
        self.assertEqual(specs("the U.S. Rep. said"), [])

    def test_a_year_gives_the_congress(self):
        _hit, spec = specs("S. Rep. No. 497 (1880).")[0]
        self.assertEqual(spec["cong"], 46)
        # An odd year before 1935 could be either Congress's.
        _hit, spec = specs("H.R. Rep. No. 12 (1867).")[0]
        self.assertEqual(spec["congs"], "39,40")

    def test_documents(self):
        _hit, spec = specs("H. R. Doc. No. 494, 72d Cong., 2d Sess.")[0]
        self.assertEqual((spec["src"], spec["ch"], spec["type"], spec["cong"]), ("doc", "h", "doc", 72))
        _hit, spec = specs("S. Treaty Doc. No. 103-39 (1994)")[0]
        self.assertEqual((spec["type"], spec["cong"], spec["num"]), ("treaty", 103, 39))
        _hit, spec = specs("H.R. Exec. Doc. No. 1, 40th Cong., 2d Sess.")[0]
        self.assertEqual(spec["type"], "exec")


class HelperTests(unittest.TestCase):
    def test_congresses_for_year(self):
        self.assertEqual(lh.congresses_for_year(1976), [94])
        self.assertEqual(lh.congresses_for_year(1977), [95])
        self.assertEqual(lh.congresses_for_year(1866), [39])
        self.assertEqual(lh.congresses_for_year(1867), [39, 40])

    def test_ordinals(self):
        self.assertEqual([lh.ordinal(n) for n in (1, 2, 3, 4, 11, 21, 42, 43, 102)],
                         ["1st", "2d", "3d", "4th", "11th", "21st", "42d", "43d", "102d"])

    def test_with_page(self):
        cr = lh.make_spec(src="cr", vol=132, page="16823")
        self.assertEqual(lh.parse_spec(lh.with_page(cr, "17607"))["page"], "17607")
        daily = lh.make_spec(src="cr", vol=148, page="S2101", daily=True)
        self.assertEqual(lh.parse_spec(lh.with_page(daily, "2105"))["page"], "S2105")
        rpt = lh.make_spec(src="rpt", ch="h", cong=94, num=1476, pin="51")
        self.assertEqual(lh.parse_spec(lh.with_page(rpt, "66"))["pin"], "66")

    def test_parse_query(self):
        kind, spec = lh.parse_query("116 Cong. Rec. 36481")
        self.assertEqual((kind, lh.parse_spec(spec)["vol"]), ("leghist", 116))
        self.assertEqual(lh.parse_query("S. Rep. No. 95-797 (1978)")[0], "leghist")
        self.assertIsNone(lh.parse_query("Roe v. Wade"))
        self.assertIsNone(lh.parse_query("see 116 Cong. Rec. 36481 and more words"))


class IndexTests(unittest.TestCase):
    """The shipped indexes, read as the fetcher reads them."""

    @classmethod
    def setUpClass(cls):
        if not lh.has_indexes():
            raise unittest.SkipTest("legislative-history indexes not present")

    def test_a_bound_page_is_its_chambers_day(self):
        g = lh.crecb_granule(116, 37264)
        self.assertEqual((g.kind, g.date, g.first), ("S", "1970-11-16", 37263))
        self.assertTrue(g.url.endswith("GPO-CRECB-1970-pt28-1-1.pdf"))

    def test_a_page_two_chambers_share(self):
        # Page 37347 ends the Senate's day and begins the House's.
        self.assertEqual(lh.crecb_granule(116, 37347, chamber="H").kind, "H")
        self.assertEqual(lh.crecb_granule(116, 37347, chamber="S").kind, "S")

    def test_the_daily_editions_day(self):
        g = lh.crecb_day(120, "1974-10-10", "H")
        self.assertEqual((g.kind, g.date), ("H", "1974-10-10"))

    def test_born_digital_volumes(self):
        g = lh.crecb_granule(151, 7400)
        self.assertEqual((g.kind, g.date, g.first, g.last), ("X", "2005-04-21", 7329, 7533))

    def test_debates(self):
        page = lh.debates_page(lh.make_spec(src="globe", cong=39, sess=1, page=2765))
        self.assertIn("congress-39-session-1", page.url)
        self.assertEqual((page.lo, page.date[:4]), (2765, "1866"))
        page = lh.debates_page(lh.make_spec(src="annals", vol=1, page=434))
        self.assertTrue(page.lo <= 434 <= page.hi)
        self.assertTrue(page.url.endswith("/annals-of-congress/volume-1.pdf"))


def _pdf(pages, *, nested=False):
    """A small PDF whose pages print *pages* (strings) in Helvetica —
    written by hand, so a test controls its structure."""
    objs = {1: b"<</Type/Catalog/Pages 2 0 R>>",
            3: b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"}
    kids = []
    num = 4
    for text in pages:
        content = b"BT /F1 12 Tf 72 720 Td (" + text.encode() + b") Tj ET"
        objs[num + 1] = b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream"
        parent = 2
        objs[num] = (b"<</Type/Page/Parent %d 0 R/Contents %d 0 R>>" % (parent, num + 1))
        kids.append(num)
        num += 2
    if nested:
        half = len(kids) // 2
        a, b = num, num + 1
        objs[a] = b"<</Type/Pages/Parent 2 0 R/Kids[%s]/Count %d>>" % (
            b" ".join(b"%d 0 R" % k for k in kids[:half]), half)
        objs[b] = b"<</Type/Pages/Parent 2 0 R/Kids[%s]/Count %d>>" % (
            b" ".join(b"%d 0 R" % k for k in kids[half:]), len(kids) - half)
        objs[2] = (b"<</Type/Pages/Kids[%d 0 R %d 0 R]/Count %d"
                   b"/Resources<</Font<</F1 3 0 R>>>>/MediaBox[0 0 612 792]>>" % (a, b, len(kids)))
    else:
        objs[2] = (b"<</Type/Pages/Kids[%s]/Count %d/Resources<</Font<</F1 3 0 R>>>>"
                   b"/MediaBox[0 0 612 792]>>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids)))
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for n in sorted(objs):
        offsets[n] = len(out)
        out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"
    xref = len(out)
    size = max(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for n in range(1, size):
        out += (b"%010d 00000 n \n" % offsets[n]) if n in offsets else b"0000000000 65535 f \n"
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    return bytes(out)


def _texts(data):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    try:
        return [doc[i].get_textpage().get_text_range().strip() for i in range(len(doc))]
    finally:
        doc.close()


class PdfRangeTests(unittest.TestCase):
    def remote(self, data):
        calls = []

        def fetch(start, end):
            calls.append((start, end))
            return data[start:end]

        return pdf_range.RemotePdf(pdf_range.RemoteFile(len(data), fetch)).load(), calls

    def test_cuts_the_pages_asked_for(self):
        data = _pdf([f"Page {i + 1}" for i in range(12)])
        pdf, _calls = self.remote(data)
        self.assertEqual(pdf.page_count(), 12)
        self.assertEqual(_texts(pdf.cut([4, 5, 9])), ["Page 5", "Page 6", "Page 10"])

    def test_a_nested_page_tree(self):
        data = _pdf([f"Page {i + 1}" for i in range(10)], nested=True)
        pdf, _calls = self.remote(data)
        self.assertEqual(_texts(pdf.cut([0, 7])), ["Page 1", "Page 8"])

    def test_the_pages_keep_what_they_inherited(self):
        # The font and page size come from the page tree's root.
        data = _pdf(["Inherited"])
        pdf, _calls = self.remote(data)
        self.assertEqual(_texts(pdf.cut([0])), ["Inherited"])

    def test_reads_are_merged(self):
        f = pdf_range.RemoteFile(1000, lambda s, e: b"x" * (e - s))
        f.prefetch([(0, 10), (50, 60), (100, 120)])
        self.assertEqual(f.requests, 1)
        self.assertEqual(f.read(55, 58), b"xxx")
        self.assertEqual(f.requests, 1)


class FetchTests(unittest.TestCase):
    def test_the_daily_edition_through_the_link_service(self):
        with patch.object(leghist_fetch, "_get_pdf", return_value=(
                b"%PDF-", "https://www.govinfo.gov/content/pkg/CREC-2002-03-20/pdf/"
                          "CREC-2002-03-20-pt1-PgS2096-2.pdf#page=6")) as get:
            pages = leghist_fetch.fetch(lh.make_spec(
                src="cr", vol=148, page="S2101", daily=True, date="2002-03-20"))
        get.assert_called_once_with("https://www.govinfo.gov/link/crec/148/s/2101?link-type=pdf")
        self.assertEqual(pages.index, 5)
        self.assertEqual(pages.title, "148 Cong. Rec. S2101 (daily ed. Mar. 20, 2002)")

    def test_a_report_govinfo_lacks_is_looked_for_elsewhere(self):
        spec = lh.make_spec(src="rpt", ch="h", cong=94, num=1476, pin="66")
        with patch.object(leghist_fetch, "_get_pdf", side_effect=leghist_fetch.Unavailable("HTTP 400")), \
                patch.object(leghist_fetch, "_internet_archive", return_value=None) as ia, \
                patch.object(leghist_fetch, "_hathitrust",
                             return_value="https://babel.hathitrust.org/cgi/pt?id=osu.1"):
            with self.assertRaises(leghist_fetch.Unavailable) as ctx:
                leghist_fetch.fetch(spec)
        ia.assert_called_once()
        self.assertEqual(ctx.exception.browser_url, "https://babel.hathitrust.org/cgi/pt?id=osu.1")

    def test_either_congress_of_an_odd_year(self):
        spec = lh.make_spec(src="rpt", ch="h", num=12, congs="39,40")
        urls = []

        def get(url, **_kw):
            urls.append(url)
            raise leghist_fetch.Unavailable("HTTP 400")

        with patch.object(leghist_fetch, "_get_pdf", side_effect=get), \
                patch.object(leghist_fetch, "_internet_archive", return_value=None), \
                patch.object(leghist_fetch, "_hathitrust", return_value=""):
            with self.assertRaises(leghist_fetch.Unavailable):
                leghist_fetch.fetch(spec)
        self.assertEqual(urls, ["https://www.govinfo.gov/link/crpt/39/hrpt/12?link-type=pdf",
                                "https://www.govinfo.gov/link/crpt/40/hrpt/12?link-type=pdf"])

    def test_a_catalogue_entry_names_this_report(self):
        s = {"src": "rpt", "ch": "h", "cong": 94, "num": 1476}
        self.assertTrue(leghist_fetch._names_report(
            "COPYRIGHT LAW REVISION ... HOUSE OF REPRESENTATIVES, 94TH CONG., 2D SESS. REPORT NO. 94-1476.", s))
        self.assertTrue(leghist_fetch._names_report(
            "Report / 94th Congress, 2d session, House of Representatives ; no. 94-1476.", s))
        self.assertFalse(leghist_fetch._names_report("Senate Report No. 94-1476", s))
        self.assertFalse(leghist_fetch._names_report("House Report No. 94-14760", s))

    def test_the_printed_page_number(self):
        self.assertEqual(leghist_fetch._printed_number(
            "37264 CONGRESSIONAL RECORD- SENATE November 16, 1970\nMESSAGES", 37264), 37264)
        self.assertEqual(leghist_fetch._printed_number(
            "November 16, 1970 CONGRESSIONAL RECORD- SENATE '37273\ndiagnostic", 37270), 37273)
        self.assertIsNone(leghist_fetch._printed_number("no number here", 41))

    def test_finds_the_printed_page(self):
        data = _pdf(["1", "2", "iii", "3", "4", "5"])
        # Page 4 is the fifth page: a roman-numbered insert shifts it.
        self.assertEqual(leghist_fetch._find_printed_page(data, 4, 3, 6), 4)

    def test_the_report_opens_at_its_pin(self):
        data = _pdf(["1", "2", "3", "4", "5", "6", "7"])
        pages = leghist_fetch._at_pin(data, {"src": "rpt", "ch": "s", "cong": 95,
                                             "num": 797, "pin": "5"}, "GovInfo", "u")
        self.assertEqual(pages.index, 4)


class DetectLinksTests(unittest.TestCase):
    def actions(self, text):
        return [(text[s:e], a) for s, e, a in citations.detect_links(text)]

    def test_an_id_after_the_record_is_another_page(self):
        text = "See 132 Cong. Rec. 16823-16825 (1986) (Senate); id., at 17607-17612 (House)."
        got = self.actions(text)
        self.assertEqual(got[1][0], "id., at 17607")
        self.assertEqual(lh.parse_spec(got[1][1][1])["page"], "17607")

    def test_the_record_is_no_case_reporter(self):
        got = self.actions("116 Cong. Rec. 36481 (1970).")
        self.assertEqual([a[0] for _h, a in got], ["leghist"])

    def test_a_docket_number_is_still_a_docket(self):
        got = self.actions("Mitchell v. Jones, No. 94-1476 (4th Cir. Feb. 12, 1995).")
        self.assertEqual([a[0] for _h, a in got], ["recap"])


SRC = pathlib.Path(courtlistener_gui.__file__).read_text(encoding="utf-8")


class GuiWiringTests(unittest.TestCase):
    def test_every_dispatch_opens_legislative_history(self):
        # The text window, briefs and scans (via the statute dispatcher), the
        # statute viewer's cross-references, the browser fallback, Spotlight.
        self.assertGreaterEqual(SRC.count("_open_leghist("), 5)
        self.assertIn('elif kind == "leghist":\n        url = leghist_fetch.browser_url(value)', SRC)
        self.assertIn("legislative_history.parse_query(query)", SRC)

    def test_history_and_bookmarks_reopen_it(self):
        self.assertEqual(SRC.count('if kind == "leghist":\n            spec = str(payload.get("spec") or "")'), 2)

    def test_an_id_after_a_record_cite_in_the_text_window(self):
        self.assertIn("legislative_history.with_page(la[1], pin)", SRC)


if __name__ == "__main__":
    unittest.main()
