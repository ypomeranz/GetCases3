"""The side panel's "Docket" view for a lower federal court's case.

For the Supreme Court the view lists the case's briefs (scotus_docket).  A
district court's or a court of appeals' case has a docket in CourtListener's
RECAP archive instead — when anyone has bought it from PACER — and the view
offers it: what the docket says of the case, its newest filings, and the
whole docket on CourtListener.  It is offered only when there is one.

The record CourtListener files an opinion under is not that docket: Floyd v.
City of New York, 959 F. Supp. 2d 540 (S.D.N.Y. 2013), sits on a docket of
its own numbered "No. 08 Civ. 1034(SAS)", while the case's PACER docket is
1:08-cv-01034.  The docket is found by its number in its court.
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

import courtlistener
from court_catalog import is_federal_court

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None


FLOYD = {
    "web_url": "https://www.courtlistener.com/docket/4339720/floyd/",
    "case_name": "Floyd v. The City of New York",
    "docket_number": "1:08-cv-01034", "count": 1, "docket_id": 4339720,
    "court_id": "nysd", "date_filed": "2008-01-31", "date_terminated": "",
    "assigned_to": "Analisa Nadine Torres",
    "nature_of_suit": "440 Civil Rights: Other",
    "cause": "42:1983 Civil Rights Act",
    "document_count": 1709,
    "latest": [
        {"entry_number": 991, "description": "Status Report",
         "date_filed": "2026-10-06", "web_url": "https://cl/991/",
         "pdf_url": "https://storage/991.pdf"},
        {"entry_number": 990, "description": "Status Report",
         "date_filed": "2026-09-28", "web_url": "https://cl/990/",
         "pdf_url": ""},
    ],
}


class DocketNumberTests(unittest.TestCase):
    def test_the_southern_district_s_year_first_number(self):
        self.assertIn("08-cv-1034",
                      courtlistener._docket_variants("08 Civ. 1034(SAS)"))
        self.assertIn("13-cr-55",
                      courtlistener._docket_variants("No. 13 Cr. 55"))

    def test_the_pacer_order_still_read(self):
        self.assertIn("90-cr-135",
                      courtlistener._docket_variants("CR-90-135-JFM"))

    def test_federal_courts(self):
        for court in ("nysd", "ca2", "cadc", "nysb", "scotus"):
            self.assertTrue(is_federal_court(court), court)
        for court in ("ny", "nyappdiv", "cal", ""):
            self.assertFalse(is_federal_court(court), court)


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class LookupTests(unittest.TestCase):
    def test_each_number_of_several(self):
        self.assertEqual(gui._docket_number_list("Nos. 12-3088, 12-3416"),
                         ["12-3088", "12-3416"])
        self.assertEqual(gui._docket_number_list("No. 08 Civ. 1034(SAS)"),
                         ["08 Civ. 1034(SAS)"])
        self.assertEqual(gui._docket_number_list(""), [])

    def _find(self, dockets, court, hit=None, found=dict(FLOYD)):
        client = SimpleNamespace(_session="session")
        with mock.patch.object(gui, "_cl_item_for_citation",
                               return_value=hit) as by_cite, \
                mock.patch.object(gui.cl_api, "find_recap_docket",
                                  return_value=found) as find, \
                mock.patch.object(gui.cl_api, "recap_docket_documents",
                                  return_value=(1709, ["doc"])) as docs:
            got = gui._recap_docket_for_case(
                client, dockets, court, "959 F. Supp. 2d 540",
                "Floyd v. City of New York")
        return got, by_cite, find, docs

    def test_by_its_number_in_its_court_with_its_newest_filings(self):
        got, by_cite, find, docs = self._find(["08 Civ. 1034(SAS)"], "nysd")
        by_cite.assert_not_called()
        self.assertEqual(find.call_args.args[:2],
                         ("08 Civ. 1034(SAS)", "nysd"))
        docs.assert_called_once_with(4339720, session="session")
        self.assertEqual((got["document_count"], got["latest"]),
                         (1709, ["doc"]))

    def test_the_citation_s_record_supplies_what_the_opinion_lacks(self):
        got, by_cite, find, _docs = self._find(
            [], "", hit={"court_id": "nysd",
                         "docketNumber": "No. 08 Civ. 1034(SAS)"})
        by_cite.assert_called_once()
        self.assertEqual(find.call_args.args[:2],
                         ("08 Civ. 1034(SAS)", "nysd"))
        self.assertIsNotNone(got)

    def test_or_the_docket_of_the_citation_s_cluster(self):
        # The citation lookup's record is a cluster's: Lorenzo v. SEC, 872
        # F.3d 578, carries no docket number until its docket is read.
        client = SimpleNamespace(
            _session="session",
            get_cluster=mock.Mock(return_value={"docket_id": 6160901}),
            get_docket=mock.Mock(return_value={"docket_number": "15-1202",
                                               "court_id": "cadc"}))
        with mock.patch.object(gui, "_cl_item_for_citation",
                               return_value={"cluster_id": 4255,
                                             "court_id": "cadc"}), \
                mock.patch.object(gui.cl_api, "find_recap_docket",
                                  return_value=None) as find:
            gui._recap_docket_for_case(client, [], "cadc", "872 F.3d 578",
                                       "Lorenzo v. SEC")
        client.get_docket.assert_called_once_with(
            6160901, fields="docket_number,court_id")
        self.assertEqual(find.call_args.args[:2], ("15-1202", "cadc"))

    def test_never_for_the_supreme_court_or_a_state_court(self):
        for court in ("scotus", "ny"):
            got, _by_cite, find, _docs = self._find(["12-3088"], court)
            self.assertIsNone(got)
            find.assert_not_called()


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class ViewTests(unittest.TestCase):
    def test_offered_only_where_there_is_a_docket(self):
        views = ("Case details", "Citing cases", "Docket", "Recent SCOTUS")
        names = gui._ScholarTextWindow._details_view_names
        reader = SimpleNamespace(_details_views=views, _is_scotus=False,
                                 _recap_docket=None)
        self.assertNotIn("Docket", names(reader))
        reader._recap_docket = FLOYD
        self.assertIn("Docket", names(reader))
        reader = SimpleNamespace(_details_views=views, _is_scotus=True,
                                 _recap_docket=None)
        self.assertIn("Docket", names(reader))

    def test_what_it_shows(self):
        lines = gui._ScholarTextWindow._details_lines_recap(FLOYD)
        self.assertEqual(lines[0], ("title", "Floyd v. The City of New York"))
        self.assertEqual(lines[1], ("lbl", "S.D.N.Y. · No. 1:08-cv-01034"))
        self.assertIn(("", "Analisa Nadine Torres"), lines)
        self.assertIn(("h", "Newest filings in the archive (of 1,709)"),
                      lines)
        # A filing the archive holds opens as its PDF; one it doesn't, as
        # its page on CourtListener.
        self.assertIn(("", "No. 991 · Status Report",
                       "https://storage/991.pdf"), lines)
        self.assertIn(("", "No. 990 · Status Report", "https://cl/990/"),
                      lines)
        self.assertIn(("lbl", "2026-09-28 · not in the archive"), lines)
        self.assertEqual(lines[-1], ("", "The whole docket on CourtListener "
                                         "(RECAP)", FLOYD["web_url"]))

    def test_a_court_of_appeals_entry_without_the_clerk_s_tags(self):
        found = dict(FLOYD, court_id="cadc", docket_number="15-1202",
                     latest=[{
                         "entry_number": 1207964139,
                         "description": "OPINION [1695572] filed (Pages: 34)"
                                        " for the Court by Judge Srinivasan."
                                        " [15-1202] [Entered: 09/29/2017 "
                                        "09:59 AM]",
                         "date_filed": "2017-09-29",
                         "web_url": "https://cl/1/", "pdf_url": ""}])
        lines = gui._ScholarTextWindow._details_lines_recap(found)
        self.assertIn(("", "OPINION filed (Pages: 34) for the Court by Judge "
                           "Srinivasan.", "https://cl/1/"), lines)

    def test_several_dockets_under_one_number(self):
        lines = gui._ScholarTextWindow._details_lines_recap(
            {"case_name": "United States v. Williams", "count": 4,
             "docket_number": "90-cr-135", "court_id": "mdd",
             "web_url": "https://cl/?type=r"})
        self.assertEqual(lines[-1][2], "https://cl/?type=r")
        self.assertIn("4 dockets", lines[-1][1])

    def test_a_state_court_is_not_looked_up(self):
        reader = SimpleNamespace(
            _recap_checked=False, _is_scotus=False,
            _bb={"court_id": "ny"}, _pdf_item=lambda: {}, _app=mock.Mock())
        gui._ScholarTextWindow._check_recap_docket(reader)
        reader._app._get_client.assert_not_called()
        self.assertTrue(reader._recap_checked)

    def test_nor_without_a_token(self):
        app = mock.Mock()
        app._token_var.get.return_value = ""
        reader = SimpleNamespace(
            _recap_checked=False, _is_scotus=False,
            _bb={"court_id": "nysd"}, _pdf_item=lambda: {}, _app=app)
        gui._ScholarTextWindow._check_recap_docket(reader)
        app._get_client.assert_not_called()
        self.assertFalse(reader._recap_checked)


if __name__ == "__main__":
    unittest.main()
