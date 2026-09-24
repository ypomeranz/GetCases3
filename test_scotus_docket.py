import unittest
from pathlib import Path

import scotus_docket


OFFICIAL_FIXTURE = """
<table>
  <tr>
    <td class="ProceedingDate">Oct 11 2022</td>
    <td>
      Application (22A100) to extend the time to file a petition for a writ
      of certiorari.
      <span class="documentlinks">
        <a href="https://court.test/extension.pdf">Main Document</a>
      </span>
    </td>
  </tr>
  <tr>
    <td class="ProceedingDate">Nov 10 2022</td>
    <td>
      Petition for a writ of certiorari filed. (Response due December 15, 2022)
      <span class="documentlinks">
        <a href="https://court.test/petition.pdf">Petition</a>
        <a href="https://court.test/appendix.pdf">Appendix</a>
        <a href="https://court.test/certificate.pdf">
          Certificate of Word Count
        </a>
        <a href="https://court.test/proof.pdf">Proof of Service</a>
      </span>
    </td>
  </tr>
  <tr>
    <td class="ProceedingDate">Dec 6 2022</td>
    <td>
      Brief amicus curiae of New England Legal Foundation filed.
      <span class="documentlinks">
        <a href="https://court.test/cert-amicus.pdf">Main Document</a>
      </span>
    </td>
  </tr>
  <tr>
    <td class="ProceedingDate">Jan 17 2023</td>
    <td>
      Brief of respondents Gina Raimondo, et al. in opposition filed.
      <span class="documentlinks">
        <a href="https://court.test/opposition.pdf">Main Document</a>
      </span>
    </td>
  </tr>
  <tr>
    <td class="ProceedingDate">Mar 3 2023</td>
    <td>
      Reply of petitioners Loper Bright Enterprises, et al. filed. (Distributed)
      <span class="documentlinks">
        <a href="https://court.test/cert-reply.pdf">Main Document</a>
      </span>
    </td>
  </tr>
  <tr>
    <td class="ProceedingDate">May 1 2023</td>
    <td>Petition GRANTED.</td>
  </tr>
  <tr>
    <td class="ProceedingDate">Jul 24 2023</td>
    <td>
      Joint appendix filed.
      <span class="documentlinks">
        <a href="https://court.test/joint-appendix.pdf">Main Document</a>
      </span>
    </td>
  </tr>
  <tr>
    <td class="ProceedingDate">Jul 24 2023</td>
    <td>
      Brief of petitioners Loper Bright Enterprises, et al. filed.
      <span class="documentlinks">
        <a href="https://court.test/petitioner.pdf">Main Document</a>
      </span>
    </td>
  </tr>
  <tr>
    <td class="ProceedingDate">Jul 31 2023</td>
    <td>
      Brief amici curiae of Cato Institute, et al. filed.
      <span class="documentlinks">
        <a href="https://court.test/petitioner-amicus.pdf">Main Document</a>
      </span>
    </td>
  </tr>
  <tr>
    <td class="ProceedingDate">Sep 22 2023</td>
    <td>
      Brief of respondents Gina Raimondo, et al. filed.
      <span class="documentlinks">
        <a href="https://court.test/respondent.pdf">Main Document</a>
      </span>
    </td>
  </tr>
  <tr>
    <td class="ProceedingDate">Sep 29 2023</td>
    <td>
      Brief amicus curiae of Public Citizen filed.
      <span class="documentlinks">
        <a href="https://court.test/respondent-amicus.pdf">Main Document</a>
      </span>
    </td>
  </tr>
  <tr>
    <td class="ProceedingDate">Oct 20 2023</td>
    <td>
      Reply of petitioners Loper Bright Enterprises, et al. filed.
      <span class="documentlinks">
        <a href="https://court.test/merits-reply.pdf">Main Document</a>
      </span>
    </td>
  </tr>
  <tr>
    <td class="ProceedingDate">Jan 17 2024</td>
    <td>
      Transcript of oral argument filed.
      <span class="documentlinks">
        <a href="https://court.test/transcript.pdf">Main Document</a>
      </span>
    </td>
  </tr>
</table>
"""

RAMOS_INITIAL_FILING_FIXTURE = """
<table>
  <tr>
    <td class="ProceedingDate">Sep 07 2018</td>
    <td>
      Petition for a writ of certiorari and motion for leave to proceed in
      forma pauperis filed. (Response due October 11, 2018)
      <span class="documentlinks">
        <a href="/DocketPDF/18/18-5924/63126/20180910110241307_Motion%20for%20Leave%20to%20Proceed%20IFP.Ramos.pdf">
          Motion for Leave to Proceed in Forma Pauperis
        </a>
        <a href="/DocketPDF/18/18-5924/63126/20180910111103199_Evangelisto%20Ramos.cert.%20petition.final.pdf">
          Petition
        </a>
        <a href="/DocketPDF/18/18-5924/63126/20180910111116558_Appendix.Ramos.pdf">
          Petition
        </a>
        <a href="/proof.pdf">Proof of Service</a>
      </span>
    </td>
  </tr>
</table>
"""


ARCHIVE_FIXTURE = """
<main>
  <div class="prose max-w-none">
    <h4>Supplemental Merits Briefs</h4>
    <ul>
      <li><a href="/files/appellant-supp.pdf">
        Supplemental brief of appellant Citizens United
      </a></li>
      <li><a href="/files/appellee-supp.pdf">
        Supplemental brief of appellee Federal Election Commission
      </a></li>
      <li><a href="/files/reply-supp.pdf">
        Supplemental reply brief of appellant Citizens United
      </a></li>
    </ul>
    <h4>Supplemental Amicus Briefs</h4>
    <p><em>Neither party</em></p>
    <ul>
      <li><a href="/files/neither.pdf">
        Brief of Independent Sector in Support of Neither Party
      </a></li>
    </ul>
    <p><em>Appellee</em></p>
    <ul>
      <li><a href="/files/appellee-amicus.pdf">
        Brief Amicus Curiae of Campaign Legal Center
      </a></li>
    </ul>
    <h4>Merits Briefs</h4>
    <ul>
      <li><a href="/files/appellant.pdf">Brief for Appellant Citizens United</a></li>
      <li><a href="/files/appellee.pdf">Brief for Appellee United States</a></li>
      <li><a href="/files/reply.pdf">Reply Brief for Appellant Citizens United</a></li>
    </ul>
    <h4>Cert Stage</h4>
    <ul>
      <li><a href="/files/jurisdiction.pdf">
        Jurisdictional Statement of Citizens United
      </a></li>
      <li><a href="/files/cert-appendix.pdf">
        Appendix to Jurisdictional Statement
      </a></li>
      <li><a href="http://not-a-real-document">
        Amicus Curiae Brief with a broken migrated URL
      </a></li>
      <li><a href="/files/dismiss.pdf">FEC's Motion to Dismiss or Affirm</a></li>
      <li><a href="/files/extension.pdf">
        Motion for extension of time to file jurisdictional statement
      </a></li>
    </ul>
    <h2>Links and Further Information</h2>
    <ul><li><a href="/news.pdf">Unrelated news</a></li></ul>
  </div>
</main>
"""

OBERGEFELL_TIMELINE_FIXTURE = """
<main>
  <a href="https://cdn.test/cert-response.pdf"
     class="bg-brief-orange flex items-center">
    <span>Dec 12, 2014</span>
    <span>Brief of respondent Richard Hodges, Director, Ohio Department of
      Health filed.</span>
  </a>
  <div class="bg-brief-white flex items-center">
    <span>Jan 16, 2015</span>
    <span>Petition GRANTED.</span>
  </div>
  <a href="https://cdn.test/petitioner.pdf"
     class="bg-brief-light-blue flex items-center">
    <span>Feb 27, 2015</span>
    <span>Brief of petitioners James Obergefell, et al. filed.</span>
  </a>
  <a href="https://cdn.test/amicus-before-argument-order.pdf"
     class="bg-brief-light-green flex items-center">
    <span>Mar 5, 2015</span>
    <span>Brief amicus curiae of Institute for Justice filed. VIDED.</span>
  </a>
  <div class="bg-brief-white flex items-center">
    <span>Mar 6, 2015</span>
    <span>SET FOR ARGUMENT ON Tuesday, April 28, 2015</span>
  </div>
  <a href="https://cdn.test/amicus-after-argument-order.pdf"
     class="bg-brief-light-green flex items-center">
    <span>Mar 6, 2015</span>
    <span>Brief amicus curiae of United States filed. VIDED.</span>
  </a>
  <a href="https://cdn.test/respondent.pdf"
     class="bg-brief-light-red flex items-center">
    <span>Mar 27, 2015</span>
    <span>Brief of respondents Richard Hodges, et al. filed.</span>
  </a>
  <a href="https://cdn.test/respondent-amicus.pdf"
     class="bg-brief-dark-green flex items-center">
    <span>Apr 2, 2015</span>
    <span>Brief amicus curiae of 100 Scholars of Marriage filed.</span>
  </a>
</main>
"""


CURRENT_BLOG_FIXTURE = """
<main>
  <a href="https://court.test/petition.pdf"
     class="bg-brief-white flex items-center">
    <span>Nov 10, 2022</span>
    <span>Petition for a writ of certiorari filed.</span>
  </a>
  <div class="bg-brief-white flex items-center">
    <span>May 1, 2023</span><span>Petition GRANTED.</span>
  </div>
  <a href="https://blog.test/merits.pdf"
     class="bg-brief-light-blue flex items-center">
    <span>Jul 24, 2023</span>
    <span>Brief of petitioners on the merits filed.</span>
  </a>
  <a href="https://www.scotusblog.com/cases/example/"
     class="bg-brief-cream flex items-center">
    <span>Jul 25, 2023</span>
    <span>Motion for leave to file amicus brief not accepted for filing.
      (Duplicate submission)</span>
  </a>
  <a href="https://court.test/Certificate%20of%20Compliance.pdf"
     class="bg-brief-dark-green flex items-center">
    <span>Sep 29, 2023</span>
    <span>Brief amicus curiae of Public Citizen filed.</span>
  </a>
  <a href="http://www.oyez.org/cases/2020-2029/2023/2023_22_451"
     class="bg-brief-white flex items-center">
    <span>Oct 3, 2023</span>
    <span>Argued. For petitioners: Roman Martinez, Washington, D. C.; and
      Elizabeth B. Prelogar, Solicitor General, Department of Justice,
      Washington, D. C., as amicus curiae, supporting the petitioners.
      For respondents: Sarah M. Harris, Washington, D. C.</span>
  </a>
  <a href="https://www.supremecourt.gov/oral_arguments/argument_transcripts/2023/22-451_114p.pdf"></a>
</main>
"""


class OfficialDocketParsingTests(unittest.TestCase):
    def test_one_initial_row_gets_three_filing_specific_labels(self):
        documents = scotus_docket.parse_official_docket(
            RAMOS_INITIAL_FILING_FIXTURE, "18-5924"
        )

        self.assertEqual(
            [(document.kind, document.label) for document in documents],
            [
                (
                    "cert_ifp_motion",
                    "Motion for leave to proceed in forma pauperis",
                ),
                (
                    "cert_petition",
                    "Petition for a writ of certiorari",
                ),
                (
                    "cert_appendix",
                    "Appendix to petition for a writ of certiorari",
                ),
            ],
        )
        self.assertTrue(documents[2].url.endswith("Appendix.Ramos.pdf"))

    def test_filters_procedural_filings_and_keeps_requested_documents(self):
        documents = scotus_docket.parse_official_docket(
            OFFICIAL_FIXTURE, "22-451"
        )

        self.assertEqual(len(documents), 12)
        self.assertEqual(
            [document.kind for document in documents[:5]],
            [
                "cert_petition",
                "cert_appendix",
                "cert_amicus",
                "cert_opposition",
                "cert_reply",
            ],
        )
        self.assertEqual(documents[0].cover, "white")
        self.assertEqual(documents[1].label, "Appendix to petition for a writ of certiorari")
        self.assertEqual(documents[2].cover, "cream")
        self.assertEqual(documents[3].cover, "orange")
        self.assertEqual(documents[4].cover, "tan")
        self.assertNotIn("extension.pdf", {document.url for document in documents})
        self.assertNotIn("certificate.pdf", {document.url for document in documents})
        self.assertNotIn("proof.pdf", {document.url for document in documents})
        transcript = documents[-1]
        self.assertEqual(transcript.kind, "oral_argument_transcript")
        self.assertEqual(transcript.cover, "plain")
        self.assertEqual(transcript.url, "https://court.test/transcript.pdf")

    def test_merits_colors_follow_party_and_filing_sequence(self):
        documents = scotus_docket.parse_official_docket(
            OFFICIAL_FIXTURE, "22-451"
        )
        colors = {document.kind: document.cover for document in documents}

        self.assertEqual(colors["joint_appendix"], "tan")
        self.assertEqual(colors["merits_petitioner"], "light_blue")
        self.assertEqual(colors["merits_amicus_petitioner"], "light_green")
        self.assertEqual(colors["merits_respondent"], "light_red")
        self.assertEqual(colors["merits_amicus_respondent"], "dark_green")
        self.assertEqual(colors["merits_reply"], "yellow")


class SCOTUSblogParsingTests(unittest.TestCase):
    def test_finds_only_the_exact_docket_case_result(self):
        search_html = """
        <a href="/cases/smith-v-united-states/">
          Smith v. United States No. 21-100
        </a>
        <a href="/cases/loper-bright-enterprises-v-raimondo/">
          Loper Bright Enterprises v. Raimondo No. 22-451 · OT2023
        </a>
        """

        url = scotus_docket.find_scotusblog_case_url(search_html, "22\u2013451")

        self.assertEqual(
            url,
            "https://www.scotusblog.com/cases/"
            "loper-bright-enterprises-v-raimondo/",
        )

    def test_finds_exact_docket_in_client_search_payload(self):
        payload = {
            "hits": [
                {"document": {
                    "docket_number": "21-100",
                    "slug": "smith-v-united-states",
                }},
                {"document": {
                    "docket_number": "08-205",
                    "docket_number_compact": "08205",
                    "slug": "citizens-united-v-federal-election-commission",
                }},
            ]
        }

        url = scotus_docket.find_scotusblog_case_url_json(
            payload, "08\u2011205"
        )

        self.assertEqual(
            url,
            "https://www.scotusblog.com/cases/"
            "citizens-united-v-federal-election-commission/",
        )

    def test_parses_archived_headed_brief_lists_and_side_hints(self):
        documents = scotus_docket.parse_scotusblog_case(
            ARCHIVE_FIXTURE,
            "https://www.scotusblog.com/cases/citizens-united/",
            "08-205",
        )
        by_name = {document.url.rsplit("/", 1)[-1]: document for document in documents}

        self.assertEqual(by_name["jurisdiction.pdf"].stage, "cert")
        self.assertEqual(by_name["jurisdiction.pdf"].cover, "white")
        self.assertEqual(by_name["cert-appendix.pdf"].kind, "cert_appendix")
        self.assertEqual(
            by_name["cert-appendix.pdf"].label,
            "Appendix to jurisdictional statement",
        )
        self.assertEqual(by_name["dismiss.pdf"].cover, "orange")
        self.assertEqual(by_name["appellant-supp.pdf"].cover, "light_blue")
        self.assertEqual(by_name["appellee-supp.pdf"].cover, "light_red")
        self.assertEqual(by_name["reply-supp.pdf"].cover, "yellow")
        self.assertEqual(by_name["neither.pdf"].cover, "light_green")
        self.assertEqual(by_name["appellee-amicus.pdf"].cover, "dark_green")
        self.assertNotIn("extension.pdf", by_name)
        self.assertNotIn("news.pdf", by_name)
        self.assertNotIn("not-a-real-document", by_name)
        self.assertEqual(documents[0].stage, "cert")

    def test_parses_current_timeline_and_ignores_non_document_rows(self):
        documents = scotus_docket.parse_scotusblog_case(
            CURRENT_BLOG_FIXTURE,
            "https://www.scotusblog.com/cases/example/",
            "22-451",
        )

        self.assertEqual(len(documents), 3)
        self.assertEqual(documents[0].kind, "cert_petition")
        self.assertEqual(documents[0].date, "Nov 10, 2022")
        self.assertEqual(documents[1].kind, "merits_petitioner")
        self.assertEqual(documents[2].kind, "oral_argument_transcript")
        self.assertEqual(documents[2].cover, "plain")
        self.assertIn("/argument_transcripts/", documents[2].url)
        # "Argued. For petitioners: ... as amicus curiae ..." records the
        # sitting; SCOTUSblog links it to Oyez, and its counsel list names
        # both sides and an amicus, so it must not read as a merits brief.
        self.assertFalse([
            document for document in documents
            if "oyez.org" in document.url
        ])

    def test_older_timeline_uses_cover_hints_without_resetting_filing_side(self):
        documents = scotus_docket.parse_scotusblog_case(
            OBERGEFELL_TIMELINE_FIXTURE,
            "https://www.scotusblog.com/cases/obergefell-v-hodges/",
            "14-556",
        )
        by_file = {
            document.url.rsplit("/", 1)[-1]: document
            for document in documents
        }

        # Older entries omit the words "in opposition"; the orange cover
        # remains an unambiguous SCOTUSblog classification signal.
        self.assertEqual(
            by_file["cert-response.pdf"].kind,
            "cert_opposition",
        )
        # "Set for argument" is a stage signal, not a new merits filing
        # window.  Amici on both sides of it still support petitioners.
        self.assertEqual(
            by_file["amicus-before-argument-order.pdf"].kind,
            "merits_amicus_petitioner",
        )
        self.assertEqual(
            by_file["amicus-after-argument-order.pdf"].kind,
            "merits_amicus_petitioner",
        )
        self.assertEqual(
            by_file["respondent-amicus.pdf"].kind,
            "merits_amicus_respondent",
        )


class _Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        return self.text if isinstance(self.text, dict) else {}


class _Session:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, timeout, headers=None):
        self.calls.append((url, timeout, headers))
        return _Response(self.responses.get(url, ""), 200)


def _brotli_decoder_installed() -> bool:
    for module in ("brotlicffi", "brotli"):
        try:
            __import__(module)
        except ImportError:
            continue
        return True
    return False


class ContentEncodingTests(unittest.TestCase):
    """A coding the client cannot decode looks like a case with no briefs.

    SCOTUSblog answers ``Accept-Encoding: br`` with Brotli.  Without a Brotli
    decoder installed the response body stays compressed, ``response.text`` is
    mojibake, and the case page yields an empty document list instead of an
    error -- so the panel reports no briefs for every pre-2017 case, whose
    filings the Court's own docket does not carry.
    """

    def test_every_request_asks_only_for_decodable_codings(self):
        session = _Session({})

        scotus_docket.fetch_case_docket("22-451", session=session)

        self.assertTrue(session.calls)
        for url, _timeout, headers in session.calls:
            offered = {
                part.strip().casefold()
                for part in (headers or {}).get("Accept-Encoding", "").split(",")
                if part.strip()
            }
            self.assertTrue(offered, url)
            self.assertEqual("br" in offered, _brotli_decoder_installed(), url)

    def test_request_headers_survive_beside_the_typesense_api_key(self):
        session = _Session({})

        scotus_docket.fetch_case_docket("22-451", session=session)

        keyed = [
            headers for url, _timeout, headers in session.calls
            if scotus_docket.SCOTUSBLOG_TYPESENSE_HOST in url
        ]
        self.assertTrue(keyed)
        for headers in keyed:
            self.assertIn("X-TYPESENSE-API-KEY", headers)
            self.assertIn("Accept-Encoding", headers)

    def test_shared_gui_session_does_not_hardcode_brotli(self):
        source = Path("courtlistener_gui.py").read_text(encoding="utf-8")

        self.assertIn('"Accept-Encoding": _ACCEPT_ENCODING,', source)
        self.assertNotIn('"gzip, deflate, br"', source)


class DocketFetchAndPanelTests(unittest.TestCase):
    def test_merges_sources_and_deduplicates_the_same_court_pdf(self):
        official_url = scotus_docket.official_docket_url("22-451")
        search_url = scotus_docket.SCOTUSBLOG_SEARCH_URL.format(query="22-451")
        blog_url = "https://www.scotusblog.com/cases/loper-bright/"
        search_html = (
            f'<a href="{blog_url}">Loper Bright v. Raimondo No. 22-451</a>'
        )
        session = _Session({
            official_url: OFFICIAL_FIXTURE,
            search_url: search_html,
            blog_url: CURRENT_BLOG_FIXTURE,
        })

        result = scotus_docket.fetch_case_docket("22-451", session=session)

        self.assertEqual(len(result.documents), 14)
        self.assertEqual(
            sum(
                document.url == "https://court.test/petition.pdf"
                for document in result.documents
            ),
            1,
        )
        self.assertEqual(result.scotusblog_url, blog_url)
        self.assertTrue(all(
            document.stage == "cert"
            for document in result.documents[:5]
        ))

    def test_right_panel_exposes_docket_mode_and_cover_color_tags(self):
        source = Path("courtlistener_gui.py").read_text(encoding="utf-8")

        # The panel now names its views in a tuple and drops the docket for
        # any court but the Supreme Court, rather than appending it.
        self.assertIn('_DETAILS_VIEWS = ("Case details", "Docket"', source)
        self.assertIn('_SCAN_DETAILS_VIEWS = ("Case details", "Docket")', source)
        self.assertIn('if name != "Docket" or self._is_scotus', source)
        self.assertIn('if sel == "Docket":', source)
        self.assertIn('elif mode == "docket":', source)
        self.assertIn('f"docket_{cover}"', source)
        self.assertIn('f"docket_{document.cover}"', source)
        self.assertIn('"docket_plain", foreground="#25232e"', source)

    def test_right_panel_has_a_fixed_width_and_wraps_its_entries(self):
        source = Path("courtlistener_gui.py").read_text(encoding="utf-8")

        self.assertIn(
            "else self._text_frame,\n"
            "                width=self._details_panel_w,",
            source,
        )
        self.assertIn("f.pack_propagate(False)", source)
        self.assertIn('f, width=1, wrap="word"', source)


class FrontMatterDocketTests(unittest.TestCase):
    """Only the docket line of an opinion names its docket."""

    # Garner v. Louisiana, 368 U.S. 157, as Google Scholar heads it.
    GARNER = (
        "368 U.S. 157 (1961)  GARNER ET AL. v. LOUISIANA.  No. 26.  "
        "Supreme Court of United States.  Argued October 18-19, 1961.  "
        "Decided December 11, 1961."
    )

    def test_an_argument_date_is_not_a_docket(self):
        # "18-19" is Republic of Korea v. BAE Systems (2018).
        from citations import docket_numbers_in
        self.assertEqual(docket_numbers_in(self.GARNER), [])

    def test_nor_is_it_stored_as_one(self):
        from google_scholar import parse_opinion_blocks
        from opinion_db import _header_dockets
        html = (
            '<div id="gs_opinion"><center><b>368 U.S. 157 (1961)</b></center>'
            '<center><h3 id="gsl_case_name">GARNER ET AL.<br/> v.<br/> '
            "LOUISIANA.</h3></center><center>No. 26.</center>"
            "<center><p><b>Supreme Court of United States.</b></p></center>"
            "<center>Argued October 18-19, 1961.</center>"
            "<center>Decided December 11, 1961.</center><p>Text.</p></div>"
        )
        self.assertEqual(_header_dockets(parse_opinion_blocks(html)), [])

    def test_the_docket_line_still_counts(self):
        from citations import docket_numbers_in
        self.assertEqual(docket_numbers_in(
            "No. 22-451.  Argued January 17, 2024"), ["22-451"])
        self.assertEqual(docket_numbers_in(
            "Nos. 24-109 and 24-110.  Argued October 15-16, 2025"),
            ["24-109", "24-110"])
        self.assertEqual(docket_numbers_in("Nos. 22-451, 22–1219"),
                         ["22-451", "22-1219"])
        self.assertEqual(docket_numbers_in("No. 24A884"), ["24A884"])
        self.assertEqual(docket_numbers_in("Docket No. 18-19"), ["18-19"])


if __name__ == "__main__":
    unittest.main()
