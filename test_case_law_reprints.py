"""A volume static.case.law scanned from a reprint that sets two or more of
the original's pages on each page of its own.

Banks & Brothers reprinted 18 Wend. so in 1860.  Its files, and its list of
the volume's cases, go by the reprint's pages, and the citations by the
original's: Judges of the Oneida Common Pleas v. People ex rel. Savage, 18
Wend. 79, is file 0045-01, listed "18 Wend. 45" — and CourtListener, which
took its citations from that list, has it at 45 too.  A link to 18 Wend. 79
opened Noyes v. Hewitt, file 0077-01, the case the reprint's pages 77–80
hold.  case_law_reprints.tsv.gz says where each case begins in the
original, read from the numbers the reprint prints in brackets ("[80]").
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

import browser_links
import case_law_parse
import case_law_reprints as reprints
from courtlistener import CourtListenerClient

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None

BASE = "https://static.case.law/wend/18"
ONEIDA = "Judges of the Oneida Common Pleas v. People ex rel. Savage"


class IndexTests(unittest.TestCase):
    """What the index says of 18 Wend. — checked against the pages other
    opinions cite these cases at, on CourtListener."""

    def test_where_a_case_begins_in_the_original(self):
        for stem, first in (("0045-01", 79), ("0009-01", 9),
                            ("0115-01", 213), ("0127-01", 236),
                            ("0225-01", 425), ("0286-01", 543),
                            ("0290-01", 550)):
            with self.subTest(stem=stem):
                self.assertEqual(
                    reprints.case_of_file("wend", "18", stem).first, first)

    def test_the_case_a_page_begins_and_the_one_it_is_inside(self):
        self.assertEqual([c.stem for c in reprints.cases_at("wend", "18", 79)],
                         ["0045-01"])
        self.assertEqual(reprints.cases_at("wend", "18", 45), [])
        self.assertEqual(reprints.case_over("wend", "18", 45).stem, "0009-01")
        self.assertEqual(reprints.case_over("wend", "18", 85).stem, "0045-01")

    def test_the_page_of_the_scan_an_original_page_begins_on(self):
        oneida = reprints.case_of_file("wend", "18", "0045-01")
        self.assertEqual([oneida.pdf_page(p) for p in (79, 81, 84, 85, 107)],
                         [0, 1, 2, 3, 15])

    def test_the_list_s_citation_and_the_original_s(self):
        self.assertEqual(reprints.listed_page("wend", "18", 79), 45)
        # A page inside a case: the reprint's page it is printed on, which
        # CourtListener reads as a page of the case.
        self.assertEqual(reprints.listed_page("wend", "18", 85), 48)
        self.assertEqual(reprints.original_cite("18 Wend. 45"), "18 Wend. 79")
        self.assertEqual(reprints.original_cite("18 Wend. 9"), "18 Wend. 9")
        self.assertEqual(reprints.asked(f"{ONEIDA}, 18 Wend. 79."),
                         f"{ONEIDA}, 18 Wend. 45.")

    def test_a_page_of_the_reprint_holding_several_cases(self):
        # Reprint page 268 holds five: three of 509, two of 510.
        self.assertEqual(reprints.original_cite("18 Wend. 268"),
                         "18 Wend. 268")
        self.assertEqual(
            reprints.original_cite("18 Wend. 268", "Cross v. Franklin"),
            "18 Wend. 510")

    def test_a_volume_whose_list_cites_the_original(self):
        # 17 Wend.'s list cites the original's pages; only its files are the
        # reprint's.
        canal = reprints.cases_at("wend", "17", 570)[0]
        self.assertEqual((canal.stem, canal.listed), ("0321-01", 570))
        self.assertIsNone(reprints.listed_page("wend", "17", 570))
        self.assertEqual(reprints.asked("17 Wend. 570"), "17 Wend. 570")

    def test_any_other_volume_is_none(self):
        self.assertEqual(reprints.volume("wend", "19"), ())
        self.assertIsNone(reprints.listed_page("wend", "19", 79))


class BoundInTests(unittest.TestCase):
    """J. B. Steel's reprint of 17 La. (1854) binds 18 La. in after it,
    numbered from 1 again, and static.case.law lists its cases as 17 La.'s:
    "17 La. 1" is Fleming v. Hill, and Leefe v. Walker, 18 La. 1, too."""

    def test_each_case_in_its_own_volume(self):
        self.assertEqual(
            [c.name for c in reprints.cases_at("la", "17", 1)],
            ["Fleming v. Hill"])
        self.assertEqual(
            [c.name for c in reprints.cases_at("la", "17", 1, book="18")],
            ["Leefe v. Walker"])
        # Listed at 553; 18 La.'s own list has it at 495.
        porter = reprints.case_of_file("la", "17", "0668-01")
        self.assertEqual((porter.book, porter.listed, porter.first),
                         ("18", 553, 495))

    def test_a_page_inside_a_case_of_17_la(self):
        # 17 La. 122 is a page of Gravier's Curator v. Carraby's Executor,
        # not Municipality No. 2 v. Orleans Cotton Press, 18 La. 122.
        self.assertEqual(reprints.case_over("la", "17", 122).stem, "0074-01")
        self.assertEqual(reprints.cases_at("la", "17", 122), [])

    def test_courtlistener_s_citations(self):
        self.assertEqual(reprints.asked("17 La. 1"), "17 La. 1")
        self.assertEqual(reprints.original_cite("17 La. 1", "Leefe v. Walker"),
                         "18 La. 1")
        self.assertEqual(reprints.original_cite("17 La. 1", "Fleming v. Hill"),
                         "17 La. 1")
        # Nothing to tell them by: as given.
        self.assertEqual(reprints.original_cite("17 La. 1"), "17 La. 1")

    def test_the_other_volume_s_case_is_no_answer(self):
        def cluster(name):
            return {"case_name": name, "citations": [
                {"volume": 17, "reporter": "La.", "page": "1"}]}
        found = reprints.answers(
            [{"citation": "17 La. 1", "status": 300,
              "clusters": [cluster("Fleming v. Hill"),
                           cluster("Leefe v. Walker")]}], "17 La. 1")
        self.assertEqual([c["case_name"] for c in found[0]["clusters"]],
                         ["Fleming v. Hill"])
        found = reprints.answers(
            [{"citation": "18 La. 1", "clusters": [cluster("Leefe v. Walker")]}],
            "18 La. 1")
        self.assertEqual(found[0]["clusters"][0]["citations"][0],
                         {"volume": 18, "reporter": "La.", "page": "1"})

    def test_the_browser_s_scan(self):
        self.assertEqual(
            browser_links.case_pdf_urls("17 La. 1"),
            ["https://static.case.law/la/17/case-pdfs/0005-01.pdf"])
        self.assertEqual(browser_links.case_pdf_urls("17 La. 122"), [])


class CleanMarksTests(unittest.TestCase):
    def test_a_run(self):
        self.assertEqual(reprints.clean_marks([79, 80, 81]), [79, 80, 81])

    def test_a_misread_number_between_two(self):
        self.assertEqual(reprints.clean_marks([537, 638, 539]),
                         [537, 538, 539])

    def test_numbers_lost(self):
        self.assertEqual(reprints.clean_marks([39, 42, 43]), [39, 42, 43])

    def test_a_number_that_marks_no_page(self):
        self.assertEqual(reprints.clean_marks([100, 101, 7, 102]),
                         [100, 101, None, 102])

    def test_a_number_given_twice(self):
        # Coventry's last page and Spalding v. Congdon's first, 18 Wend. 543.
        self.assertEqual(reprints.clean_marks([541, 543, 543, 544]),
                         [541, 542, 543, 544])

    def test_a_figure_misread_far_off_the_run(self):
        # 17 La.'s second book, its 3s read as 8s.
        self.assertEqual(reprints.clean_marks([380, 881, 882, 884, 385]),
                         [380, 381, 382, 384, 385])


class StarPageTests(unittest.TestCase):
    HTML = (
        '<section class="casebody" data-firstpage="45" data-lastpage="47">'
        '<section class="head-matter"><h4 class="parties">[79] The Judges '
        'of the Oneida Common Pleas <em>vs. </em>The People.</h4>'
        '<p class="summary">[80] Error from the supreme court.</p>'
        '</section><article class="opinion" data-type="majority">'
        '<p>The title to land <a id="p46" href="#p46" data-label="46" '
        'class="page-label">*46</a>did not come in question. [81] The logs '
        'were received.</p><p>[28] So they were. [83] Judgment '
        'reversed.</p></article></section>')

    def pages(self, **kw):
        _parts, blocks = case_law_parse.parse_case_law_html(self.HTML, **kw)
        return [s.text for b in blocks for s in b.spans if s.pagenum]

    def test_the_original_s_numbers_are_its_pages(self):
        self.assertEqual(self.pages(pages=(79, 83)), ["*80", "*81", "*82",
                                                      "*83"])

    def test_the_brackets_go(self):
        _parts, blocks = case_law_parse.parse_case_law_html(
            self.HTML, pages=(79, 83))
        text = " ".join(b.text() for b in blocks)
        self.assertNotIn("[", text)
        self.assertIn("The Judges of the Oneida", text)
        self.assertIn("question. *81The logs", text)

    def test_any_other_case_keeps_its_labels(self):
        self.assertEqual(self.pages(), ["*46"])

    def test_a_case_begun_inside_a_page(self):
        # Duke of Richmond v. Milne's Executors, 17 La. 312: its first
        # number is the next page's, "[814]" a misread 314.
        html = ('<section class="casebody"><article class="opinion">'
                '<p>The Duke. [313] Legacy. [814] Fochabers. [315] So.</p>'
                '</article></section>')
        _parts, blocks = case_law_parse.parse_case_law_html(
            html, pages=(312, 326))
        self.assertEqual([s.text for b in blocks for s in b.spans
                          if s.pagenum], ["*313", "*314", "*315"])


class _Response:
    def __init__(self, data):
        self._data = data
        self.ok = True
        self.status_code = 200

    def json(self):
        return self._data


class CourtListenerTests(unittest.TestCase):
    """CourtListener has 18 Wend.'s citations from static.case.law's list:
    asked for 18 Wend. 79 it answers with Noyes v. Hewitt, 77–80 there."""

    def setUp(self):
        self.client = CourtListenerClient("token")
        self.sent = []
        cluster = {"case_name": ONEIDA, "citations": [
            {"volume": 18, "reporter": "Wend.", "page": "45", "type": 2}]}

        def post(url, data=None, timeout=None):
            self.sent.append(data["text"])
            return _Response([{"citation": data["text"], "status": 200,
                               "clusters": [cluster]}])

        def get(url, params=None, timeout=None):
            self.sent.append(params.get("q"))
            return _Response({"results": [{"caseName": ONEIDA,
                                           "citation": ["18 Wend. 45"]}]})

        self.client._session = SimpleNamespace(post=post, get=get)

    def test_a_citation_is_looked_up_by_the_list_s_page(self):
        found = self.client.lookup_citation("18 Wend. 79")
        self.assertEqual(self.sent, ["18 Wend. 45"])
        self.assertEqual(found[0]["clusters"][0]["citations"][0]["page"],
                         "79")

    def test_a_search_asks_and_answers_by_the_original_s_page(self):
        results = self.client.search("citation:(18 Wend. 79)")["results"]
        self.assertEqual(self.sent, ["citation:(18 Wend. 45)"])
        self.assertEqual(results[0]["citation"], ["18 Wend. 79"])

    def test_the_opinions_words_are_searched_as_they_are(self):
        # The opinions citing it cite the original's page.
        self.client.search('"18 Wend. 79"')
        self.assertEqual(self.sent, ['"18 Wend. 79"'])

    def test_any_other_citation_is_as_it_was(self):
        self.client.lookup_citation("19 Wend. 79")
        self.assertEqual(self.sent, ["19 Wend. 79"])


class BrowserLinkTests(unittest.TestCase):
    def test_the_scan_of_the_case_beginning_at_the_page(self):
        self.assertEqual(browser_links.case_pdf_urls("18 Wend. 79@85"),
                         [f"{BASE}/case-pdfs/0045-01.pdf"])
        self.assertEqual(
            browser_links.case_pdf_urls("17 Wend. 570"),
            ["https://static.case.law/wend/17/case-pdfs/0321-01.pdf"])

    def test_no_scan_where_no_case_begins(self):
        # Page 45 is inside Bloodgood v. Mohawk & Hudson R.R., 18 Wend. 9.
        self.assertEqual(browser_links.case_pdf_urls("18 Wend. 45"), [])


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class FileTests(unittest.TestCase):
    def test_the_file_a_citation_is_in(self):
        self.assertEqual(gui._case_law_listed_url(
            "18 Wend. 79", f"{BASE}/case-pdfs/0079-01.pdf"),
            f"{BASE}/case-pdfs/0045-01.pdf")
        self.assertEqual(gui._case_law_listed_url(
            "18 Wend. 79@85", f"{BASE}/cases/0079-01.json"),
            f"{BASE}/cases/0045-01.json")

    def test_no_file_where_no_case_begins(self):
        self.assertEqual(gui._case_law_listed_url(
            "18 Wend. 45", f"{BASE}/case-pdfs/0045-01.pdf"), "")

    def test_a_file_s_citation(self):
        self.assertEqual(gui._case_law_reporter_cite(
            f"{BASE}/case-pdfs/0045-01.pdf"), "18 Wend. 79")
        self.assertEqual(gui._case_law_true_cite(
            "18 Wend. 45", f"{BASE}/cases/0045-01.json"), "18 Wend. 79")
        # Another case's citation, or another file's, is left alone.
        self.assertEqual(gui._case_law_true_cite(
            "18 Wend. 9", f"{BASE}/cases/0045-01.json"), "18 Wend. 9")
        self.assertEqual(gui._case_law_true_cite(
            "19 Wend. 45", "https://static.case.law/wend/19/cases/"
            "0045-01.json"), "19 Wend. 45")

    def test_a_case_of_the_volume_bound_in(self):
        la = "https://static.case.law/la/17"
        self.assertEqual(gui._case_law_listed_url(
            "17 La. 1", f"{la}/case-pdfs/0001-01.pdf"),
            f"{la}/case-pdfs/0005-01.pdf")
        self.assertEqual(gui._case_law_true_cite(
            "17 La. 1", f"{la}/cases/0362-01.json"), "18 La. 1")
        self.assertEqual(gui._case_law_true_cite(
            "17 La. 1", f"{la}/cases/0005-01.json"), "17 La. 1")
        self.assertEqual(gui._case_law_reporter_cite(
            f"{la}/case-pdfs/0362-01.pdf"), "18 La. 1")

    def test_the_cases_beginning_at_one_page(self):
        mates = gui._case_law_page_opinions(f"{BASE}/case-pdfs/0268-04.pdf")
        self.assertEqual([m.name for m in mates],
                         ["Cross v. Franklin", "Davenport v. Hull",
                          "Ex parte Webber's Executors"])
        self.assertEqual(mates[2].url, f"{BASE}/case-pdfs/0269-01.pdf")
        self.assertEqual(
            gui._case_law_page_opinions(f"{BASE}/case-pdfs/0045-01.pdf"), [])

    def test_the_volume_s_list_by_the_original_s_pages(self):
        listing = [
            {"file_name": "0009-01", "first_page": "9", "last_page": "45",
             "name_abbreviation": "Bloodgood v. Mohawk & Hudson Railroad",
             "citations": [{"type": "official", "cite": "18 Wend. 9"}]},
            {"file_name": "0045-01", "first_page": "45", "last_page": "60",
             "name_abbreviation": ONEIDA,
             "citations": [{"type": "official", "cite": "18 Wend. 45"}]},
            {"file_name": "0077-01", "first_page": "77", "last_page": "80",
             "name_abbreviation": "Noyes & Pettingill v. Hewitt",
             "citations": [{"type": "official", "cite": "18 Wend. 77"}]}]
        answer = SimpleNamespace(status_code=200, json=lambda: listing)
        with mock.patch.dict(gui._CASE_LAW_VOLUMES, clear=True), \
                mock.patch.object(gui._anon_session, "get",
                                  return_value=answer):
            files, _names, lengths = gui._case_law_volume("wend", "18")
            inside = gui._case_law_case_containing_page("18 Wend. 85")
            begins = gui._case_law_case_containing_page("18 Wend. 79")
        self.assertEqual(files, {"18wend9": ("0009-01",),
                                 "18wend79": ("0045-01",),
                                 "18wend141": ("0077-01",)})
        self.assertEqual(lengths["0045-01"], 29)
        self.assertEqual(inside, ("18 Wend. 79", ONEIDA))
        self.assertIsNone(begins)

    def test_the_case_s_text_and_citation(self):
        data = {"name_abbreviation": ONEIDA, "decision_date": "1837-12",
                "citations": [{"type": "official", "cite": "18 Wend. 45"}],
                "court": {"name_abbreviation": "N.Y. Sup. Ct."},
                "casebody": {"head_matter": "[79] The Judges.",
                             "opinions": [{"type": "majority",
                                           "text": "[80] Reversed."}]}}
        html = ('<section class="casebody"><section class="head-matter">'
                '<h4 class="parties">[79] The Judges.</h4></section>'
                '<article class="opinion" data-type="majority"><p>Then '
                '<a class="page-label" data-label="46">*46</a>so. [80] '
                'Reversed.</p></article></section>')
        record = gui._case_law_text_record(
            data, "18 Wend. 79", f"{BASE}/cases/0045-01.json", html)
        self.assertIn("18 Wend. 79", record.citation)
        self.assertEqual(record.item["citation"], ["18 Wend. 79"])
        self.assertEqual([s.text for b in record.blocks for s in b.spans
                          if s.pagenum], ["*80"])

    def test_a_pin_in_the_scan(self):
        url = f"{BASE}/case-pdfs/0045-01.pdf"
        jump = gui._scan_page_for_pin
        self.assertEqual(jump("18 Wend. 79", "85", "18 Wend. 79", url=url), 3)
        self.assertEqual(jump("18 Wend. 79", "79", url=url), 0)
        # The numbers printed at the pages' heads, where they were read,
        # say it — not the reprint's own at their feet.
        with mock.patch.object(gui, "_printed_page_numbers",
                               return_value={0: 45, 1: 80, 2: 82}):
            pages = [[]] * 16
            self.assertEqual(jump("18 Wend. 79", "80", url=url,
                                  pdf_pages=pages), 1)
            self.assertEqual(jump("18 Wend. 79", "45", url=url,
                                  pdf_pages=pages), None)
        # Any other scan: a page of the file per page of the reporter.
        self.assertEqual(jump("19 Wend. 79", "85"), 6)


if __name__ == "__main__":
    unittest.main()
