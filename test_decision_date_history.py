"""A case is dated by its decision, never by what its header says came later.

Run over every opinion saved in data/opinions.jsonl, the front-matter date
reader still dated eight of them by another event:

* North Carolina ex rel. Morrow v. Califano, 445 F. Supp. 532, prints
  "September 22, 1977." and beneath it "Judgment Affirmed April 17, 1978."
  — the Supreme Court's affirmance — and was dated 1978;
* "Probable Jurisdiction Noted …", "Concurring Opinion …", "Opinion Denying
  Reconsideration …" and "Decree …" lines were taken the same way;
* an opinion withdrawn and refiled (Al-Shabazz v. State, 338 S.C. 354
  (2000)) was dated by the withdrawn one.

And the index never asked again: a record saved by an earlier release kept
the year it was saved with — Brown v. United States, 524 F.2d 693, still
read 1976, the year of its amendment; City of Bloomington v. Westinghouse,
1990, its rehearing's; Nieves v. Bartlett, 1717, a star page.  Each now
reads its decision's year, as Scholar's own citation line gives it.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ["GETCASES_SKIP_DEPENDENCY_PROMPT"] = "1"

import courtlistener_gui as g
from google_scholar import Block, Span
from opinion_db import (
    OpinionDB,
    _gz_pack,
    decision_date_from_blocks,
    decision_year_from_blocks,
    extract_record,
)


def centers(*lines):
    return [Block("center", [Span(line)]) for line in lines]


MORROW = centers(
    "445 F.Supp. 532 (1977)",
    "STATE OF NORTH CAROLINA ex rel. Sarah T. MORROW v. Joseph A. "
    "CALIFANO, Secretary of Health, Education and Welfare.",
    "No. 76-0049-CIV-5.",
    "United States District Court, E. D. North Carolina, Raleigh Division.",
    "September 22, 1977.",
    "Judgment Affirmed April 17, 1978.",
)


class SubsequentHistoryTests(unittest.TestCase):
    def date(self, *lines):
        return decision_date_from_blocks(centers(*lines))

    def test_an_affirmance_is_not_the_decision(self):
        self.assertEqual(decision_date_from_blocks(MORROW), "1977-09-22")

    def test_nor_any_later_event_the_header_dates(self):
        for later, decided in (
                ("Probable Jurisdiction Noted March 30, 1990.",
                 "March 5, 1990."),
                ("Concurring Opinion October 21, 1994.", "October 17, 1994."),
                ("Opinion Denying Reconsideration September 25, 2009.",
                 "August 12, 2009."),
                ("Decree June 24, 1957.", "June 19, 1957."),
                ("Writ of Certiorari Granted March 4, 1957.",
                 "December 28, 1956."),
                ("Transfer denied October 31, 1967.", "May 31, 1967."),
                ("Cert. denied, October 1, 1990.", "June 1, 1990."),
                ("Reversed November 2, 1990.", "June 1, 1990.")):
            self.assertEqual(self.date(decided, later),
                             decision_date_from_blocks(centers(decided)),
                             later)

    def test_a_label_another_event_wears_is_not_the_decision_s(self):
        self.assertEqual(self.date(
            "May 1, 1990.", "Petition for Certiorari Filed: July 1, 1990."),
            "1990-05-01")

    def test_a_judgment_s_entry_is_the_decision(self):
        # Ohio's courts of appeals date the decision by its judgment entry.
        self.assertEqual(self.date(
            "Court of Appeals of Ohio, Fifth District, Guernsey County.",
            "Judgment Entry: October 28, 2020."), "2020-10-28")

    def test_a_refiled_opinion_is_dated_by_its_refiling(self):
        self.assertEqual(self.date(
            "Submitted November 18, 1998.", "Decided August 23, 1999.",
            "Heard December 14, 1999.", "Refiled February 14, 2000."),
            "2000-02-14")

    def test_a_date_inside_the_caption_dates_nothing(self):
        self.assertEqual(self.date(
            "In re TERRORIST ATTACKS ON SEPTEMBER 11, 2001 (Asat Trust Reg., "
            "et al.) John Patrick O'Neill, Jr., et al., Plaintiffs-Appellants, "
            "v. Asat Trust Reg."), "")

    def test_one_line_for_the_argument_and_the_decision(self):
        self.assertEqual(self.date(
            "Argued May 1, 1990, Decided June 1, 1990."), "1990-06-01")
        self.assertEqual(self.date("June 1, 1990.*"), "1990-06-01")

    def test_argument_dates_before_the_decision_still_pass(self):
        # Goodridge v. Department of Public Health, 440 Mass. 309: argued,
        # then decided, neither labelled.
        self.assertEqual(self.date(
            "Supreme Judicial Court of Massachusetts, Suffolk.",
            "March 4, 2003.", "November 18, 2003."), "2003-11-18")
        self.assertEqual(self.date(
            "May 15, 2008.", "Rehearing Denied June 4, 2008.[*]"),
            "2008-05-15")


class DecisionYearTests(unittest.TestCase):
    def test_the_citation_s_own_year(self):
        self.assertEqual(decision_year_from_blocks(MORROW), "1977")

    def test_without_one_the_decision_s_not_the_affirmance_s(self):
        self.assertEqual(decision_year_from_blocks(MORROW[1:]), "1977")

    def test_a_docket_line_s_term_is_not_the_year(self):
        # Servotronics, Inc. v. Rolls-Royce PLC (2021), argued in OT 2020.
        self.assertEqual(decision_year_from_blocks(centers(
            "SERVOTRONICS, INC. v. ROLLS-ROYCE PLC, et al.",
            "No. 20-794 (R46-44/OT 2020).",
            "Supreme Court of United States.",
            "September 29, 2021.")), "2021")

    def test_a_star_page_is_not_a_year(self):
        self.assertEqual(decision_year_from_blocks([
            Block("center", [Span("139 S.Ct. 1715")]),
            Block("center", [Span("Decided May 28, 2019.")]),
            Block("heading", [Span("*1717 ", pagenum=True),
                              Span("Syllabus")]),
        ]), "2019")


class ViewerYearTests(unittest.TestCase):
    def bluebook_year(self, blocks, item=None):
        win = object.__new__(g._ScholarTextWindow)
        win._item = dict(item or {})
        win._blocks = blocks + [Block("para", [Span("The opinion.")])]
        return win._compute_bluebook_parts()["year"]

    def test_a_header_without_the_citation_s_year(self):
        self.assertEqual(self.bluebook_year(
            [Block("center", [Span("445 F. Supp. 532")])] + MORROW[1:]),
            "1977")

    def test_a_supreme_court_docket_line_s_term(self):
        self.assertEqual(self.bluebook_year(centers(
            "142 S.Ct. 54 (2021)",
            "SERVOTRONICS, INC. v. ROLLS-ROYCE PLC, et al.",
            "No. 20-794 (R46-44/OT 2020).",
            "Supreme Court of United States.",
            "September 29, 2021."), {"court_id": "scotus"}), "2021")

    def test_a_search_result_s_rehearing_date(self):
        # CourtListener can date a case by its rehearing's denial; the
        # header, which dates the decision, controls in every court.
        self.assertEqual(self.bluebook_year(centers(
            "891 F.2d 611", "CITY OF BLOOMINGTON v. WESTINGHOUSE",
            "Decided December 6, 1989.",
            "Rehearing and Rehearing Denied January 23, 1990."),
            {"court_id": "ca7", "dateFiled": "1990-01-23"}), "1989")

    def test_but_not_a_year_the_header_only_suggests(self):
        self.assertEqual(self.bluebook_year(centers(
            "CITY OF NORWOOD v. HORNEY", "Nos. 2005-0227 and 2005-0228."),
            {"court_id": "ohio", "dateFiled": "2006-07-26"}), "2006")

    def test_a_compiled_brief_s_file_name(self):
        item = g._scholar_item_from_blocks(MORROW)
        self.assertEqual(item["dateFiled"], "1977-01-01")


def scholar_html(*lines):
    return ('<div id="gs_opinion">'
            + "".join(f"<center>{line}</center>" for line in lines)
            + "<p>The opinion.</p></div>")


BROWN = scholar_html(
    "524 F.2d 693 (1975)", "Horton J. BROWN v. The UNITED STATES.",
    "No. 251-72.", "United States Court of Claims.", "October 22, 1975.",
    "As Amended January 9, 1976.")
BLOOMINGTON = scholar_html(
    "891 F.2d 611 (1989)",
    "CITY OF BLOOMINGTON, INDIANA v. WESTINGHOUSE ELECTRIC CORPORATION",
    "No. 88-2660.", "United States Court of Appeals, Seventh Circuit.",
    "Argued September 18, 1989.", "Decided December 6, 1989.",
    "Rehearing and Rehearing Denied January 23, 1990.")


class StoredRecordTests(unittest.TestCase):
    def test_records_saved_by_an_earlier_release_are_redated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = OpinionDB(root / "opinions.jsonl", root / "opinions.db")
            try:
                # As the JSONL holds them: Brown with its amendment's date,
                # Bloomington with its rehearing's year and no date.
                db.add({"scholar_id": "1", "name": "Brown v. United States",
                        "cites": ["524 F.2d 693"], "court": "",
                        "year": "1976", "date_filed": "1976-01-09",
                        "html_gz": _gz_pack(BROWN)})
                db.add({"scholar_id": "2",
                        "name": "City of Bloomington v. Westinghouse",
                        "cites": ["891 F.2d 611"], "court": "",
                        "year": "1990", "date_filed": "",
                        "html_gz": _gz_pack(BLOOMINGTON)})
                for sid, date in (("1", "1975-10-22"), ("2", "1989-12-06")):
                    rec = db.get_by_scholar_id(sid)
                    self.assertEqual((rec["date_filed"], rec["year"]),
                                     (date, date[:4]))
                self.assertEqual(
                    [hit["year"] for hit in db.find("524 F.2d 693")],
                    ["1975"])
            finally:
                db.close()

    def test_a_search_result_s_later_date_yields_to_the_header(self):
        record = extract_record(
            "https://scholar.google.com/scholar_case?case=3", BLOOMINGTON,
            {"caseName": "City of Bloomington v. Westinghouse",
             "court_id": "ca7", "dateFiled": "1990-01-23"})
        self.assertEqual((record["date_filed"], record["year"]),
                         ("1989-12-06", "1989"))


if __name__ == "__main__":
    unittest.main()
