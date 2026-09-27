import json
import os
import sqlite3
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call, patch

os.environ["GETCASES_SKIP_DEPENDENCY_PROMPT"] = "1"

from bluebook_names import (
    apply_caption_case_reference,
    abbreviate_case_name,
    caption_case_reference_tokens,
    collapse_personal_all_caps_run,
    courtlistener_case_name,
    is_personal_all_caps_run,
    name_persons_by_surname,
    normal_case_caption,
    refine_caption_case,
)
from citation_overrides import (
    add_pin_to_base,
    citation_identity_keys,
    find_override,
    format_edited_citation,
    update_overrides,
)
from court_catalog import bluebook_federal_trial_court, state_of_court
from courtlistener_gui import (
    detect_brief_links,
    _bluebook_display_name,
    _scholar_item_from_blocks,
    CourtListenerGUI,
    _CasePdfTextSource,
    _PdfWindow,
    _ScholarTextWindow,
    _CaseLawPageOpinion,
    _CaseLawTextRecord,
    _case_law_case_html,
    _case_law_html_url,
    _case_law_pdf_for_json_url,
    _case_law_text_for_scan,
    _cites_led_by,
    _scan_printed_cite,
    _case_law_may_hold,
    _case_law_text_for_cite,
    _case_law_text_for_pdf_url,
    _case_law_text_record,
    _case_law_text_source,
    _case_law_pdf_choices_for_cites,
    _case_law_listed_url,
    _CASE_LAW_VOLUMES,
    _case_pdf_text_source,
    _open_statute_action,
    _case_law_page_opinions,
    _match_page_opinion,
    _opinion_db_spotlight_results,
    _citation_search_variants,
    _citation_link_name,
    _cl_item_for_citation,
    _combined_parts_cover_typed,
    _cut_companion_cases,
    _decision_date_paren,
    _docket_cite,
    _dump_to_rtf,
    _nominative_display_cite,
    _pick_combined_opinion,
    _plain_without_layout_chars,
    _recap_citation_ranges,
    _recap_spec_index,
    _scholar_caption_name,
    _spotlight_case_action,
    _static_case_law_url,
    _special_citation_ranges,
    _follow_brief_action,
    _wisconsin_display_cite,
)
from google_scholar import (
    Block, OpinionPart, ScholarResult, Span, bears_citation, educate_quotes,
)
from opinion_db import (
    OpinionDB, _gz_pack, _gz_unpack_prefix, _header_cites, extract_record,
)
import us_code

try:
    import pypdfium2  # noqa: F401
    HAVE_PDFIUM = True
except ImportError:
    HAVE_PDFIUM = False


class SmartQuoteTests(unittest.TestCase):
    def test_outer_double_quote_closes_after_spaced_inner_single_quote(self):
        text = (
            'The Government safeguards the flag\'s identity " \'as the unique '
            'and unalloyed symbol of the Nation.\' " Brief for United States.'
        )
        expected = (
            "The Government safeguards the flag" + chr(0x2019)
            + "s identity " + chr(0x201c) + " " + chr(0x2018)
            + "as the unique and unalloyed symbol of the Nation."
            + chr(0x2019) + " " + chr(0x201d)
            + " Brief for United States."
        )
        self.assertEqual(educate_quotes(text), expected)

    def test_separate_double_quoted_phrases_still_pair(self):
        expected = (
            "One " + chr(0x201c) + "quotation" + chr(0x201d)
            + " and another " + chr(0x201c) + "quotation" + chr(0x201d) + "."
        )
        self.assertEqual(
            educate_quotes('One "quotation" and another "quotation".'), expected,
        )


class MassachusettsNominativeTests(unittest.TestCase):
    """Tyng, Pickering, Metcalf, Cushing, Gray and Allen are volumes of the
    Massachusetts Reports under their reporters' names: same pages, the
    volume offset by the series before them."""

    def test_each_reporter_maps_onto_its_mass_volumes(self):
        from citations import mass_reports_cite
        for cite, mass in (
            ("1 Tyng 1", "2 Mass. 1"), ("16 Tyng 1", "17 Mass. 1"),
            ("1 Pick. 1", "18 Mass. 1"), ("19 Pick. 234", "36 Mass. 234"),
            ("24 Pick. 1", "41 Mass. 1"),
            ("4 Met. 111", "45 Mass. 111"), ("13 Met. 1", "54 Mass. 1"),
            ("5 Cush. 198", "59 Mass. 198"), ("12 Cush. 1", "66 Mass. 1"),
            ("1 Gray 1", "67 Mass. 1"), ("16 Gray 1", "82 Mass. 1"),
            ("1 Allen 1", "83 Mass. 1"), ("14 Allen 1", "96 Mass. 1"),
            ("1 Pickering 5", "18 Mass. 5"), ("19 Pick 234", "36 Mass. 234"),
        ):
            with self.subTest(cite=cite):
                self.assertEqual(mass_reports_cite(cite), mass)

    def test_a_volume_past_the_series_is_no_citation(self):
        from citations import mass_reports_cite
        self.assertEqual(mass_reports_cite("25 Pick. 1"), "")
        self.assertEqual(mass_reports_cite("17 Gray 1"), "")
        self.assertEqual(mass_reports_cite("8 Wall. 168"), "")

    def test_the_names_link_in_running_text(self):
        for text, action in (
            ("Commonwealth v. Hunt, 45 Mass. (4 Met.) 111 (1842).",
             ("cite", "45 Mass. 111")),
            ("Brown v. Kendall, 6 Cush. 292, 295 (1850).",
             ("cite", "6 Cush. 292@295")),
            ("Doe v. Roe, 1 Gray 1 (1854).", ("cite", "1 Gray 1")),
            ("Doe v. Roe, 4 Allen 5 (1862).", ("cite", "4 Allen 5")),
        ):
            with self.subTest(text=text):
                links = detect_brief_links(text)
                self.assertEqual(links[0][2], action)
        self.assertEqual(detect_brief_links("He had 30 Gray 5 hairs."), [])

    def test_a_lookup_tries_the_mass_cite_after_the_one_typed(self):
        from courtlistener_gui import _citation_search_variants
        self.assertEqual(_citation_search_variants("19 Pick. 234"),
                         ("19 Pick. 234", "36 Mass. 234"))
        self.assertEqual(
            _citation_search_variants("Smith v. Jones, 5 Cush. 198, 200"),
            ("Smith v. Jones, 5 Cush. 198, 200",
             "Smith v. Jones, 59 Mass. 198, 200"))

    def test_courtlistener_is_asked_for_the_mass_cite_too(self):
        client = Mock()
        client.lookup_citation.side_effect = [
            [],
            [{"status": 200, "clusters": [{
                "id": 36, "case_name": "Smith v. Jones",
                "citations": ["36 Mass. 234"], "court_id": "mass"}]}],
        ]
        item = _cl_item_for_citation(client, "19 Pick. 234")
        self.assertEqual(item["cluster_id"], 36)
        self.assertEqual(client.lookup_citation.call_args_list,
                         [call("19 Pick. 234"), call("36 Mass. 234")])


class RenamedAndRenumberedReporterTests(unittest.TestCase):
    """Reporters known by more than one name: state nominative reports
    renumbered into the official series, series renamed partway through,
    and one series written several ways."""

    def test_each_state_s_nominative_reports_map_onto_its_official_series(self):
        from citations import state_nominative_cites
        for cite, official in (
            # Illinois
            ("1 Breese 5", "1 Ill. 5"), ("4 Scam. 5", "5 Ill. 5"),
            ("1 Gilm. 5", "6 Ill. 5"), ("5 Gilm. 5", "10 Ill. 5"),
            # Kentucky
            ("1 Bibb 5", "4 Ky. 5"), ("3 A.K. Marsh. 5", "10 Ky. 5"),
            ("3 A. K. Marsh. 5", "10 Ky. 5"),
            ("1 Litt. Sel. Cas. 5", "16 Ky. 5"), ("7 T.B. Mon. 5", "23 Ky. 5"),
            ("1 J.J. Marsh. 5", "24 Ky. 5"), ("9 Dana 5", "39 Ky. 5"),
            ("18 B. Mon. 5", "57 Ky. 5"), ("2 Duv. 5", "63 Ky. 5"),
            ("14 Bush 5", "77 Ky. 5"),
            # Tennessee
            ("2 Overt. 5", "2 Tenn. 5"), ("10 Yer. 5", "18 Tenn. 5"),
            ("1 Hum. 5", "20 Tenn. 5"), ("3 Head 5", "40 Tenn. 5"),
            ("12 Heisk. 5", "59 Tenn. 5"), ("16 Lea 5", "84 Tenn. 5"),
            # Virginia
            ("1 Va. Cas. 5", "3 Va. 5"), ("6 Call 5", "10 Va. 5"),
            ("1 Hen. & M. 5", "11 Va. 5"), ("12 Leigh 5", "39 Va. 5"),
            ("33 Gratt. 5", "74 Va. 5"),
            # Delaware
            ("5 Harr. 5", "5 Del. 5"), ("1 W.W. Harr. 5", "31 Del. 5"),
            ("20 Terry 5", "59 Del. 5"),
            # Mississippi, Pennsylvania, New York
            ("1 S. & M. 5", "9 Miss. 5"), ("10 George 5", "39 Miss. 5"),
            ("10 Barr 5", "10 Pa. 5"), ("14 Wright 5", "50 Pa. 5"),
            ("1 Seld. 5", "5 N.Y. 5"), ("4 Kern. 5", "14 N.Y. 5"),
        ):
            with self.subTest(cite=cite):
                self.assertEqual(state_nominative_cites(cite), [official])

    def test_an_abbreviation_two_states_used_tries_both(self):
        from citations import state_nominative_cites
        self.assertEqual(state_nominative_cites("4 Met. 111"),
                         ["45 Mass. 111", "61 Ky. 111"])
        self.assertEqual(state_nominative_cites("1 Sneed 5"),
                         ["2 Ky. 5", "33 Tenn. 5"])
        # Only Massachusetts' Metcalf ran past four volumes.
        self.assertEqual(state_nominative_cites("9 Met. 5"), ["50 Mass. 5"])

    def test_a_bare_name_links_only_where_a_citation_stands(self):
        text = "He sold 3 Head 5 times; see Doe v. Roe, 3 Head 5 (1859)."
        spans = [text[s:e] for s, e, _a in detect_brief_links(text)]
        self.assertEqual(spans, ["Doe v. Roe, 3 Head 5 (1859)"])

    def test_the_bluebook_parallel_form_links_as_the_official_cite(self):
        links = detect_brief_links("Doe v. Roe, 61 Ky. (4 Met.) 1 (1862).")
        self.assertEqual(links[0][2], ("cite", "61 Ky. 1"))

    def test_a_lookup_tries_every_official_series_it_could_be(self):
        from courtlistener_gui import _citation_search_variants
        self.assertEqual(_citation_search_variants("4 Met. 111"),
                         ("4 Met. 111", "45 Mass. 111", "61 Ky. 111"))

    def test_a_renamed_series_is_tried_under_its_other_name(self):
        from citations import reporter_citation_variants
        self.assertEqual(reporter_citation_variants("80 App. D.C. 12"),
                         ("80 App. D.C. 12", "80 U.S. App. D.C. 12"))
        self.assertEqual(reporter_citation_variants("20 Fed. Cl. 5"),
                         ("20 Fed. Cl. 5", "20 Cl. Ct. 5"))
        self.assertEqual(reporter_citation_variants("30 Fed. Cl. 5"),
                         ("30 Fed. Cl. 5",))

    def test_another_spelling_of_a_series_is_tried_and_cited_as_bluebook(self):
        from citations import reporter_citation_variants
        for typed, bluebook in (
            ("250 Ore. 12", "250 Or. 12"), ("12 Maine 45", "12 Me. 45"),
            ("120 Okl. Cr. 5", "120 Okla. Crim. 5"),
            ("140 Tex. Cr. R. 3", "140 Tex. Crim. 3"),
            ("95 Sup. Ct. 1", "95 S. Ct. 1"),
            ("250 A.D. 5", "250 App. Div. 5"),
            ("5 Johnson 10", "5 Johns. 10"),
        ):
            with self.subTest(typed=typed):
                self.assertIn(bluebook, reporter_citation_variants(typed))
        item = {"caseName": "Doe v. Roe", "citation": ["250 Ore. 12"],
                "dateFiled": "1968-01-01", "court_id": "or"}
        self.assertEqual(_bluebook_display_name(item),
                         "Doe v. Roe, 250 Or. 12 (1968)")


class FullStateNameReporterTests(unittest.TestCase):
    """A state's reports cited by the state's name in full."""

    def test_the_full_name_is_tried_as_the_bluebook_abbreviation(self):
        from citations import reporter_citation_variants
        for typed, bluebook in (
            ("1 Massachusetts 15", "1 Mass. 15"),
            ("12 North Carolina 30", "12 N.C. 30"),
            ("5 West Virginia 7", "5 W. Va. 7"),
            ("200 Washington 5", "200 Wash. 5"),
            ("3 Maine 4", "3 Me. 4"),
        ):
            with self.subTest(typed=typed):
                self.assertIn(bluebook, reporter_citation_variants(typed))

    def test_and_finds_the_same_folder_on_static_case_law(self):
        from courtlistener_gui import _static_case_law_url
        self.assertEqual(_static_case_law_url("12 North Carolina 30"),
                         _static_case_law_url("12 N.C. 30"))
        self.assertIn("/nc/12/", _static_case_law_url("12 N.C. 30"))

    def test_every_state_keeps_static_case_law_s_own_folder_name(self):
        import re
        from citations import _STATE_REPORTER_NAMES, case_law_reporter_slug
        for full, abbr in _STATE_REPORTER_NAMES.items():
            mechanical = re.sub(r"-+", "-", re.sub(
                r"[^a-z0-9-]", "", abbr.lower().replace(" ", "-"))).strip("-")
            with self.subTest(state=full):
                self.assertEqual(case_law_reporter_slug(full), mechanical)

    def test_it_links_in_a_citation_but_not_in_prose(self):
        text = "Commonwealth v. Smith, 1 Massachusetts 15 (1804)."
        self.assertEqual(detect_brief_links(text)[0][2],
                         ("cite", "1 Massachusetts 15"))
        self.assertEqual(detect_brief_links("in about 3 Texas 12 counties"),
                         [])

    def test_it_is_cited_by_the_abbreviation(self):
        item = {"caseName": "Commonwealth v. Smith",
                "citation": ["1 Massachusetts 15"],
                "dateFiled": "1804-01-01", "court_id": "mass"}
        self.assertEqual(_bluebook_display_name(item),
                         "Commonwealth v. Smith, 1 Mass. 15 (1804)")


class WashingtonCertificationTests(unittest.TestCase):
    """Bradley v. Am. Smelting & Refin. Co., 104 Wash. 2d 677 (1985), and
    the passage citing Garratt v. Dailey that turned up the problems."""

    BRADLEY = (
        '<div id="gs_opinion"><center><b>104 Wn.2d 677 (1985)</b></center>'
        "<center><b>709 P.2d 782</b></center>"
        '<center><h3 id="gsl_case_name">CERTIFICATION FROM THE UNITED STATES '
        "DISTRICT COURT FOR THE WESTERN DISTRICT OF WASHINGTON IN<br/> "
        "MICHAEL O. BRADLEY, ET AL, Plaintiffs,<br/> v.<br/> AMERICAN "
        "SMELTING AND REFINING COMPANY, Defendant.</h3></center>"
        "<center>No. 51094-6.</center><center><p><b>The Supreme Court of "
        "Washington, En Banc.</b></p></center><center>November 14, 1985."
        "</center><p>Michael Bradley owns land. American Smelting and "
        "Refining Company operates a smelter.</p></div>"
    )

    def test_a_sentence_ending_in_state_is_not_part_of_the_next_name(self):
        text = ("This has been the reasoning of the decisions of this State. "
                "Garratt v. Dailey, 46 Wn.2d 197, 279 P.2d 1091 (1955) "
                "involved a 5-year-old boy.")
        spans = [text[s:e] for s, e, _a in detect_brief_links(text)]
        self.assertEqual(spans[0], "Garratt v. Dailey, 46 Wn.2d 197")

    def test_nor_is_a_spelled_out_table_word(self):
        text = "So held this Court. Roe v. Wade, 410 U.S. 113 (1973)."
        spans = [text[s:e] for s, e, _a in detect_brief_links(text)]
        self.assertEqual(spans[0], "Roe v. Wade, 410 U.S. 113 (1973)")

    def test_but_a_name_s_own_abbreviations_still_belong_to_it(self):
        text = "See Palsgraf v. Long Island R.R. Co., 248 N.Y. 339 (1928)."
        spans = [text[s:e] for s, e, _a in detect_brief_links(text)]
        self.assertEqual(
            spans[0], "Palsgraf v. Long Island R.R. Co., 248 N.Y. 339 (1928)")

    def test_the_certifying_court_is_not_part_of_the_name(self):
        from google_scholar import parse_opinion_blocks
        from opinion_db import extract_record
        blocks = parse_opinion_blocks(self.BRADLEY)
        self.assertEqual(
            _bluebook_display_name(_scholar_item_from_blocks(blocks)),
            "Bradley v. Am. Smelting & Refin. Co., 104 Wash. 2d 677 (1985)")
        self.assertEqual(
            extract_record("https://scholar.google.com/scholar_case?case=1",
                           self.BRADLEY)["name"],
            "Bradley v. Am. Smelting & Refin. Co.")

    def test_a_certification_lead_needs_a_case_after_it(self):
        self.assertEqual(abbreviate_case_name("Certification Bd. v. Smith"),
                         "Certification Bd. v. Smith")

    def test_wn_2d_is_cited_as_the_official_wash_2d(self):
        item = {"caseName": "Garratt v. Dailey", "citation": ["46 Wn.2d 197"],
                "dateFiled": "1955-01-27", "court_id": "wash"}
        self.assertEqual(_bluebook_display_name(item),
                         "Garratt v. Dailey, 46 Wash. 2d 197 (1955)")


class CaptionCapitalizationTests(unittest.TestCase):
    def test_a_generational_suffix_is_not_the_surname(self):
        # State v. McKelvey, 544 P.3d 632 (Alaska 2024): Scholar's caption
        # "John William McKELVEY III" cited as "State v. I.I.I." — the
        # all-caps "III" read as the surname, "McKELVEY" missed for its "c".
        self.assertEqual(
            collapse_personal_all_caps_run("John William McKELVEY III"),
            "McKELVEY")
        self.assertEqual(collapse_personal_all_caps_run("John SMITH, Jr."),
                         "SMITH")
        self.assertEqual(collapse_personal_all_caps_run("Angus MacDONALD"),
                         "MacDONALD")
        # An entity numbering itself keeps its numeral.
        self.assertEqual(collapse_personal_all_caps_run("ACME FUND II"),
                         "ACME FUND II")
        self.assertEqual(
            collapse_personal_all_caps_run("Blackstone Fund III"),
            "Blackstone Fund III")

    def test_a_surname_standing_before_its_suffix_cites_alone(self):
        self.assertEqual(
            abbreviate_case_name("State v. McKelvey III",
                                 court_state="alaska"),
            "State v. McKelvey")
        self.assertEqual(abbreviate_case_name("Acme Fund III v. Jones"),
                         "Acme Fund III v. Jones")

    def test_mckelvey_is_cited_by_his_surname(self):
        from google_scholar import parse_opinion_blocks
        blocks = parse_opinion_blocks(
            '<div id="gs_opinion"><center><b>544 P.3d 632 (2024)</b>'
            '</center><center><h3 id="gsl_case_name">STATE of Alaska, '
            "Petitioner,<br/> v.<br/> John William McKELVEY III, "
            "Respondent.</h3></center><center>Supreme Court No. S-17910."
            "</center><center><p><b>Supreme Court of Alaska.</b></p>"
            "</center><center>March 8, 2024.</center><p>John William "
            "McKelvey III lived on a property. McKelvey grew marijuana.</p>"
            "</div>")
        item = _scholar_item_from_blocks(blocks)
        self.assertEqual(_bluebook_display_name(item),
                         "State v. McKelvey, 544 P.3d 632 (Alaska 2024)")

    def test_apostrophe_and_mc_names_from_all_caps(self):
        self.assertEqual(
            normal_case_caption("O'BRIEN v. MCFADDEN"),
            "O'Brien v. McFadden",
        )
        self.assertEqual(normal_case_caption("McFADDEN"), "McFadden")

    def test_authoritative_mixed_case_brand_is_preserved(self):
        self.assertEqual(
            normal_case_caption("NBCUniversal Media, LLC"),
            "NBCUniversal Media, LLC",
        )

    def test_quoted_all_caps_word_capitalizes_inside_the_quotes(self):
        # THE "SCOTLAND.", 105 U.S. 24: the capital must reach the first
        # letter through the reporter's quotation mark, in either style.
        self.assertEqual(
            normal_case_caption("THE “SCOTLAND.”"), "The “Scotland.”")
        self.assertEqual(
            normal_case_caption('THE "SCOTLAND."'), 'The "Scotland."')
        # A digit-led token is an ordinal, not a name: its tail stays lower.
        self.assertEqual(
            normal_case_caption("42ND STREET CO. v. SMITH"),
            "42nd Street Co. v. Smith")

    def test_naacp_survives_all_caps_caption_normalization(self):
        name = normal_case_caption("NAACP v. ALABAMA")
        self.assertEqual(name, "NAACP v. Alabama")
        self.assertEqual(abbreviate_case_name(name), "NAACP v. Alabama")

    def test_courtlistener_name_preserves_api_capitalization(self):
        self.assertEqual(
            courtlistener_case_name({
                "case_name": "NBCUniversal Media, LLC v. Example",
                "case_name_full": "A Different Full Caption",
            }),
            "NBCUniversal Media, LLC v. Example",
        )
        self.assertEqual(
            courtlistener_case_name({
                "caseNameFull": "O'Brien v. McFadden",
            }),
            "O'Brien v. McFadden",
        )

    def test_usa_entity_is_not_mistaken_for_caps_surname(self):
        self.assertFalse(is_personal_all_caps_run(["USA", "LLC"], ["McDonald's"]))
        self.assertFalse(is_personal_all_caps_run(["MEDIA", "LLC"], ["NBCUniversal"]))
        self.assertTrue(is_personal_all_caps_run(["BREWBAKER"], ["Brent"]))
        self.assertTrue(is_personal_all_caps_run(["THOMAS"], ["Corrine", "Morgan"]))
        self.assertTrue(is_personal_all_caps_run(["EMORY"], ["Dr.", "Theresa", "Swain"]))

    def test_table_abbreviation_is_not_mistaken_for_caps_surname(self):
        # "R.R." is all caps because its letters are capitals, not because a
        # reporter set a surname that way.  Read as a surname it took the
        # place name with it, citing Palsgraf as "Palsgraf v. R.R. Co.".
        self.assertFalse(is_personal_all_caps_run(["R.R."], ["Long", "Island"]))
        self.assertFalse(is_personal_all_caps_run(["RY."], ["Long", "Island"]))
        self.assertFalse(is_personal_all_caps_run(["CENT."], ["New", "York"]))
        self.assertEqual(
            collapse_personal_all_caps_run("Long Island R.R. Co."),
            "Long Island R.R. Co.",
        )
        self.assertEqual(
            abbreviate_case_name("Palsgraf v. Long Island R.R. Co."),
            "Palsgraf v. Long Island R.R. Co.",
        )
        self.assertEqual(
            abbreviate_case_name("Palsgraf v. Long Island Railroad Co."),
            "Palsgraf v. Long Island R.R. Co.",
        )

    def test_mixed_case_caps_run_drops_any_name_shaped_first_names(self):
        self.assertEqual(
            collapse_personal_all_caps_run("Corrine Morgan THOMAS"),
            "THOMAS",
        )
        self.assertEqual(
            collapse_personal_all_caps_run("Dr. Theresa Swain EMORY"),
            "EMORY",
        )
        self.assertEqual(
            collapse_personal_all_caps_run("McDonald's USA, LLC"),
            "McDonald's USA, LLC",
        )
        self.assertEqual(
            collapse_personal_all_caps_run("NBCUniversal MEDIA, LLC"),
            "NBCUniversal MEDIA, LLC",
        )
        self.assertEqual(
            collapse_personal_all_caps_run("The BOEING COMPANY"),
            "The BOEING COMPANY",
        )
        self.assertEqual(
            collapse_personal_all_caps_run("A&M Records, Inc."),
            "A&M Records, Inc.",
        )
        self.assertEqual(
            collapse_personal_all_caps_run("CITIZENS FOR A BETTER ENVIRONMENT"),
            "CITIZENS FOR A BETTER ENVIRONMENT",
        )
        self.assertEqual(
            collapse_personal_all_caps_run("The PRESIDENT"),
            "The PRESIDENT",
        )

    def test_possessive_s_is_not_a_surname_prefix(self):
        # The O'BRIEN → O'Brien rule must not capitalize a possessive:
        # Wasserman's Inc. v. Township of Middletown, 137 N.J. 238 (1994).
        self.assertEqual(
            normal_case_caption("WASSERMAN'S INC. v. MIDDLETOWN"),
            "Wasserman's Inc. v. Middletown",
        )
        self.assertEqual(
            normal_case_caption("JACKSON WOMEN'S HEALTH ORGANIZATION"),
            "Jackson Women's Health Organization",
        )
        self.assertEqual(normal_case_caption("McDONALD'S"), "McDonald's")
        # An already-stored artifact is repaired at abbreviation time (the
        # party-leading "The" drops under rule 10.2.1(d) as before).
        self.assertEqual(
            abbreviate_case_name("Inglis v. The Sailor'S Snug Harbour"),
            "Inglis v. Sailor's Snug Harbour",
        )

    def test_single_letter_initials_keep_their_capitals(self):
        # R.A.V. v. City of St. Paul, 505 U.S. 377 (1992): the spaced
        # initials "R. A. V." collide with the small words "a" and "v".  (The
        # caption's trailing ", Minnesota" goes under rule 10.2.1(f).)
        self.assertEqual(
            abbreviate_case_name(normal_case_caption(
                "R. A. V., PETITIONER v. CITY OF ST. PAUL, MINNESOTA")),
            "R.A.V. v. City of St. Paul",
        )
        self.assertEqual(
            normal_case_caption("SAMUEL A. WORCESTER v. GEORGIA"),
            "Samuel A. Worcester v. Georgia",
        )
        # A lone "V." stays the separator when no initial precedes it.
        self.assertEqual(
            normal_case_caption("SMITH V. JONES"), "Smith v. Jones")

    def test_mixed_case_small_words_are_lowercased(self):
        # Partially mixed-case captions bypass all-caps normalization, so
        # "Of"/"OF" survive into the name ("District Of Columbia").
        self.assertEqual(
            abbreviate_case_name("District Of Columbia v. Heller"),
            "District of Columbia v. Heller",
        )
        self.assertEqual(
            abbreviate_case_name(
                "Sec'y OF State of Md. v. Joseph H. Munson Co."),
            "Sec'y of State of Md. v. Joseph H. Munson Co.",
        )
        # An abbreviation that spells a small word is not one: "Or." is
        # Oregon.
        self.assertEqual(
            abbreviate_case_name(
                "Employment Division, Department of Human Resources of "
                "Oregon v. Smith"),
            "Emp. Div., Dep't of Hum. Res. of Or. v. Smith",
        )
        # Small words inside an all-caps run carry no casing signal and
        # keep their caps (T6 word abbreviation applies as before).
        self.assertEqual(
            abbreviate_case_name(
                "CITIZENS FOR A BETTER ENVIRONMENT v. Anne Gorsuch"),
            "CITIZENS FOR A BETTER Env't v. Gorsuch",
        )

    def test_deslandes_caption_keeps_mcdonalds(self):
        self.assertEqual(
            abbreviate_case_name("Leinani Deslandes v. McDonald's USA LLC"),
            "Deslandes v. McDonald's USA LLC",
        )

    def test_titled_person_reduces_to_surname(self):
        # Pecos River Talc LLC v. Emory (E.D. Va. 2026): the honorific drops
        # and the surname survives an unrecognized middle name.
        self.assertEqual(
            abbreviate_case_name(
                "Pecos River Talc LLC v. Dr. Theresa Swain Emory"),
            "Pecos River Talc LLC v. Emory",
        )
        self.assertEqual(
            abbreviate_case_name("Smith v. Sgt. William Brown, Jr."),
            "Smith v. Brown",
        )

    def test_middle_initial_marks_a_natural_person(self):
        # Rule 10.2.1(g): organizations never reduce a middle word to a
        # single letter, so the initial licenses the surname reduction
        # even for a given name no list covers.
        self.assertEqual(
            abbreviate_case_name("Okello T. Chatrie v. United States"),
            "Chatrie v. United States",
        )
        self.assertEqual(
            abbreviate_case_name("Dred Scott v. John F.A. Sandford"),
            "Scott v. Sandford",
        )
        self.assertEqual(
            abbreviate_case_name("Moore v. Mahendra J. Shah"),
            "Moore v. Shah",
        )
        # …but a firm named for a person keeps its full name.
        self.assertEqual(
            abbreviate_case_name("Susan B. Anthony List v. Driehaus"),
            "Susan B. Anthony List v. Driehaus",
        )
        # (Its initials close up, as adjacent single capitals do: rule
        # 6.1(a).)
        self.assertEqual(
            abbreviate_case_name("A. H. Robins Co. v. Piccinin"),
            "A.H. Robins Co. v. Piccinin",
        )

    def test_generational_suffix_marks_a_natural_person(self):
        self.assertEqual(
            abbreviate_case_name("Valentino Shine, Sr. v. United States"),
            "Shine v. United States",
        )

    def test_title_never_truncates_a_brand_name(self):
        for name in ("Dr Pepper Bottling Co. v. Smith",
                     "Mrs. Fields Cookies v. Smith",
                     "Miss Universe L.P. v. Smith"):
            self.assertEqual(abbreviate_case_name(name), name)

    def test_mid_name_municipal_unit_is_omitted(self):
        # Doremus v. Bd. of Educ., 342 U.S. 429 (1952): rule 10.2.1(f) omits
        # the phrase of location "of the Borough of Hawthorne" whole, as it
        # does "of the Township of Ewing" in Everson v. Bd. of Educ.
        self.assertEqual(
            abbreviate_case_name(normal_case_caption(
                "DOREMUS ET AL. v. BOARD OF EDUCATION OF THE BOROUGH OF "
                "HAWTHORNE ET AL.")),
            "Doremus v. Bd. of Educ.",
        )
        # Where that would leave a single word the place stays, and only the
        # "city of" expression drops — the Bluebook's own example.
        self.assertEqual(
            abbreviate_case_name("Mayor of the City of New York v. Clark"),
            "Mayor of N.Y. v. Clark",
        )
        # A party that begins with the expression keeps it.
        self.assertEqual(
            abbreviate_case_name("City of New York v. Doe"),
            "City of New York v. Doe",
        )

    def test_ex_parte_caption_with_related_case_note(self):
        # Ex parte Murphy, 596 So. 2d 45 (Ala. 1992): the "(Re Murphy v.
        # State)" cross-reference to the underlying case drops, and the
        # petitioner reduces to the surname.
        self.assertEqual(
            abbreviate_case_name(normal_case_caption(
                "Ex parte Anthony P. MURPHY. "
                "(Re Anthony Paul Murphy v. State).")),
            "Ex parte Murphy",
        )

    def test_caption_role_designations_are_stripped(self):
        self.assertEqual(
            abbreviate_case_name(
                "Pecos River Talc LLC, Plaintiff, v. "
                "Dr. Theresa Swain Emory, et al., Defendants."),
            "Pecos River Talc LLC v. Emory",
        )
        self.assertEqual(
            abbreviate_case_name(
                "Standard Oil Co., Defendant-Appellant v. United States"),
            "Standard Oil Co. v. United States",
        )


class GeographicTermTests(unittest.TestCase):
    """Rule 10.2.1(f): "Omit all prepositional phrases of location not
    following 'City,' or like expressions, unless the omission would leave
    only one word in the name of a party or the location is part of a
    business name."""

    def assertNames(self, cases):
        for raw, want in cases:
            with self.subTest(raw=raw):
                got = abbreviate_case_name(raw)
                self.assertEqual(got, want)
                # Safe to call twice.
                self.assertEqual(abbreviate_case_name(got), got)

    def test_a_phrase_of_location_is_omitted(self):
        self.assertNames([
            ("Brown v. Board of Education of Topeka", "Brown v. Bd. of Educ."),
            # The rule's own example.
            ("Surrick v. Board of Wardens of the Port of Philadelphia",
             "Surrick v. Bd. of Wardens"),
            ("Planned Parenthood of Southeastern Pennsylvania v. Casey",
             "Planned Parenthood v. Casey"),
            ("Florence v. Board of Chosen Freeholders of Burlington",
             "Florence v. Bd. of Chosen Freeholders"),
        ])

    def test_a_city_or_township_phrase_goes_whole(self):
        # The place follows "City", but the phrase itself follows the
        # party's own name — so it is omitted, "City of" and all.
        self.assertNames([
            ("Everson v. Board of Education of the Township of Ewing",
             "Everson v. Bd. of Educ."),
            ("Monell v. Department of Social Services of the City of "
             "New York", "Monell v. Dep't of Soc. Servs."),
            ("Walz v. Tax Commission of the City of New York",
             "Walz v. Tax Comm'n"),
        ])

    def test_the_full_supreme_court_caption_reads_the_same(self):
        self.assertEqual(
            abbreviate_case_name(normal_case_caption(
                "OLIVER BROWN, ET AL. v. BOARD OF EDUCATION OF TOPEKA, "
                "SHAWNEE COUNTY, KANSAS, ET AL.")),
            "Brown v. Bd. of Educ.",
        )

    def test_only_the_place_goes_where_the_name_goes_on(self):
        # The school district is the board's own name, not a place.
        self.assertNames([
            ("Board of Education of Independent School District No. 92 of "
             "Pottawatomie County v. Earls",
             "Bd. of Educ. of Indep. Sch. Dist. No. 92 v. Earls"),
            ("Board of Education of Westside Community Schools v. Mergens",
             "Bd. of Educ. of Westside Cmty. Schs. v. Mergens"),
            ("Board of Regents of State Colleges v. Roth",
             "Bd. of Regents of State Colls. v. Roth"),
        ])

    def test_a_trailing_designation_after_a_comma_goes(self):
        self.assertNames([
            ("Bostock v. Clayton County, Georgia",
             "Bostock v. Clayton County"),
            ("Kelo v. City of New London, Connecticut",
             "Kelo v. City of New London"),
            ("Town of Castle Rock, Colorado v. Gonzales",
             "Town of Castle Rock v. Gonzales"),
            ("Haycraft v. Board of Education of Jefferson County, Kentucky",
             "Haycraft v. Bd. of Educ."),
            # A court's own place stays; the county after it does not.
            ("Bristol-Myers Squibb Co. v. Superior Court of California, "
             "San Francisco County",
             "Bristol-Myers Squibb Co. v. Superior Ct. of Cal."),
        ])

    def test_a_name_left_one_word_long_keeps_its_place(self):
        self.assertNames([
            ("Shapiro v. Bank of Harrisburg", "Shapiro v. Bank of Harrisburg"),
            # Widely recognized initials count as one word, and are then what
            # the party is called.
            ("McCreary County, Kentucky v. American Civil Liberties Union "
             "of Kentucky", "McCreary County v. ACLU of Ky."),
        ])

    def test_a_business_keeps_its_place(self):
        self.assertNames([
            ("Standard Oil Co. of New Jersey v. United States",
             "Standard Oil Co. of N.J. v. United States"),
            ("Riley v. National Federation of the Blind of North Carolina, "
             "Inc.", "Riley v. Nat'l Fed'n of the Blind of N.C., Inc."),
            ("Hackner v. Federal Reserve Bank of New York",
             "Hackner v. Fed. Rsrv. Bank of N.Y."),
        ])

    def test_a_place_that_names_an_office_or_institution_stays(self):
        self.assertNames([
            ("Attorney General of New York v. Soto-Lopez",
             "Att'y Gen. of N.Y. v. Soto-Lopez"),
            ("Personnel Administrator of Massachusetts v. Feeney",
             "Pers. Adm'r of Mass. v. Feeney"),
            ("Secretary of State of Maryland v. Joseph H. Munson Co.",
             "Sec'y of State of Md. v. Joseph H. Munson Co."),
            ("Regents of the University of California v. Bakke",
             "Regents of the Univ. of Cal. v. Bakke"),
            ("School District of Abington Township v. Schempp",
             "Sch. Dist. of Abington Twp. v. Schempp"),
            ("Roman Catholic Archdiocese of San Juan v. Acevedo Feliciano",
             "Roman Cath. Archdiocese of San Juan v. Acevedo Feliciano"),
        ])

    def test_a_national_designation_stays(self):
        self.assertNames([
            ("Boy Scouts of America v. Dale", "Boy Scouts of Am. v. Dale"),
            ("Communist Party of the United States v. Subversive Activities "
             "Control Board",
             "Communist Party of the U.S. v. Subversive Activities Control "
             "Bd."),
        ])

    def test_an_already_abbreviated_name_reads_the_same(self):
        self.assertNames([
            ("Planned Parenthood of Se. Pa. v. Casey",
             "Planned Parenthood v. Casey"),
            ("Atl. Refin. Co. v. Pub. Serv. Comm'n of N.Y.",
             "Atl. Refin. Co. v. Pub. Serv. Comm'n"),
            # The county that is the whole party is named in full again.
            ("Soldal v. Cook Cnty., Ill.", "Soldal v. Cook County"),
        ])

    def test_a_tribe_keeps_the_place_it_is_named_for(self):
        self.assertNames([
            ("Seminole Tribe of Florida v. Florida",
             "Seminole Tribe of Fla. v. Florida"),
            ("Kiowa Tribe of Oklahoma v. Manufacturing Technologies, Inc.",
             "Kiowa Tribe of Okla. v. Mfg. Techs., Inc."),
        ])


class CityNameTests(unittest.TestCase):
    """Table T10's cities abbreviate inside a longer party name, and stay
    whole when the city is the entire party (rule 10.2.2)."""

    def assertNames(self, cases):
        for raw, want in cases:
            with self.subTest(raw=raw):
                got = abbreviate_case_name(raw)
                self.assertEqual(got, want)
                self.assertEqual(abbreviate_case_name(got), got)

    def test_a_city_inside_a_longer_name_is_abbreviated(self):
        self.assertNames([
            ("Cannon v. University of Chicago", "Cannon v. Univ. of Chi."),
            ("Chicago Teachers Union v. Hudson",
             "Chi. Tchrs. Union v. Hudson"),
            ("San Francisco Arts & Athletics, Inc. v. United States Olympic "
             "Committee",
             "S.F. Arts & Athletics, Inc. v. U.S. Olympic Comm."),
            ("Baltimore & Ohio Railroad Co. v. United States",
             "Balt. & Ohio R.R. Co. v. United States"),
            ("Miami Herald Publishing Co. v. Tornillo",
             "Mia. Herald Publ'g Co. v. Tornillo"),
            ("United States v. Philadelphia National Bank",
             "United States v. Phila. Nat'l Bank"),
            ("Board of Trade of the City of Chicago v. United States",
             "Bd. of Trade of Chi. v. United States"),
            ("City of Los Angeles, Department of Water & Power v. Manhart",
             "City of L.A., Dep't of Water & Power v. Manhart"),
        ])

    def test_a_city_that_is_the_whole_party_stays_whole(self):
        self.assertNames([
            ("Dallas v. Stanglin", "Dallas v. Stanglin"),
            ("Terminiello v. Chicago", "Terminiello v. Chicago"),
            ("Los Angeles v. Lyons", "Los Angeles v. Lyons"),
            ("McDonald v. City of Chicago", "McDonald v. City of Chicago"),
            ("Lockyer v. City and County of San Francisco",
             "Lockyer v. City & County of San Francisco"),
        ])

    def test_a_person_or_a_people_named_like_a_city_is_left_alone(self):
        self.assertNames([
            # A surname, as the whole party or after a given name.
            ("Houston v. Lack", "Houston v. Lack"),
            ("Sam Houston State University v. Doe",
             "Sam Houston State Univ. v. Doe"),
            ("Miami Tribe of Oklahoma v. United States",
             "Miami Tribe of Okla. v. United States"),
        ])


class OfficeTitleTests(unittest.TestCase):
    """An office or capacity after a party's name describes the party and is
    omitted (rules 10.2.1(e), (g)), even where the caption gives the person's
    surname alone."""

    def assertNames(self, cases):
        for raw, want in cases:
            with self.subTest(raw=raw):
                self.assertEqual(abbreviate_case_name(raw), want)

    def test_an_office_after_a_lone_surname_goes(self):
        self.assertNames([
            ("Bowers, Attorney General of Georgia v. Hardwick",
             "Bowers v. Hardwick"),
            ("Roe v. Wade, District Attorney of Dallas County", "Roe v. Wade"),
            ("Printz, Sheriff/Coroner, Ravalli County, Montana v. United "
             "States", "Printz v. United States"),
            ("Alexander, Director, Alabama Department of Public Safety v. "
             "Sandoval, Individually and on Behalf of All Others Similarly "
             "Situated", "Alexander v. Sandoval"),
            ("Gideon v. Wainwright, Corrections Director",
             "Gideon v. Wainwright"),
            ("Indiana ex rel. Anderson v. Brand, Trustee",
             "Indiana ex rel. Anderson v. Brand"),
        ])

    def test_the_office_vouches_for_an_unfamiliar_middle_name(self):
        self.assertEqual(
            abbreviate_case_name(
                "Whole Woman's Health v. Austin Reeve Jackson, Judge, "
                "District Court of Texas"),
            "Whole Woman's Health v. Jackson",
        )

    def test_a_capacity_goes_the_same_way(self):
        self.assertNames([
            ("Blunt v. Spears, by Next Friend", "Blunt v. Spears"),
            ("United States v. Edith Schlain Windsor, in her capacity as "
             "Executor of the Estate of Thea Clara Spyer",
             "United States v. Windsor"),
            ("Shadi Dabit, on behalf of himself and all others similarly "
             "situated v. Merrill Lynch, Pierce, Fenner & Smith, Inc.",
             "Dabit v. Merrill Lynch, Pierce, Fenner & Smith, Inc."),
        ])

    def test_a_relator_named_with_an_office(self):
        self.assertEqual(
            abbreviate_case_name(
                "Northern Pacific Railway Co. v. North Dakota on Rel. of "
                "McCue, Attorney General"),
            "N. Pac. Ry. Co. v. North Dakota ex rel. McCue",
        )

    def test_a_firm_s_designator_is_not_an_office(self):
        self.assertNames([
            ("Sara Lee, Inc. v. Kraft Foods", "Sara Lee, Inc. v. Kraft Foods"),
            ("Reno, Attorney General v. American Civil Liberties Union",
             "Reno v. ACLU"),
        ])


class StoredCaptionTests(unittest.TestCase):
    """A name that reaches the abbreviator without passing a caption reader —
    a saved record, a CourtListener caseName — cites the same as one that
    did: its companion cases go, and a geographic party is named in full."""

    def assertNames(self, cases):
        for raw, want in cases:
            with self.subTest(raw=raw):
                got = abbreviate_case_name(raw)
                self.assertEqual(got, want)
                self.assertEqual(abbreviate_case_name(got), got)

    def test_bostock(self):
        self.assertNames([
            ("Bostock v. Clayton County, Georgia. Altitude Express, Inc., et "
             "al., Petitioners v. Zarda", "Bostock v. Clayton County"),
            # As saved, already abbreviated: "Cnty." is no surname.
            ("Bostock v. Clayton Cnty., Georgia. Altitude Express, Inc., et "
             "al., Petitioners v. Melissa Zarda", "Bostock v. Clayton County"),
            ("Shelby Cnty. v. Holder", "Shelby County v. Holder"),
        ])

    def test_companion_cases_go(self):
        self.assertNames([
            ("Perez v. Mortg. Bankers Ass'n et al. Jerome Nickols, et al., "
             "Petitioners v. Mortg. Bankers Association",
             "Perez v. Mortg. Bankers Ass'n"),
            ("Olmstead v. U.S.. Green Et Al. v. Same. McInnis v. Same",
             "Olmstead v. United States"),
            ("Mugler v. Kansas. Same v. Same. Kan. v. Ziebold",
             "Mugler v. Kansas"),
            ("Riley v. California. United States, Petitioner, v. Brima Wurie",
             "Riley v. California"),
            ("In re Nexium Antitrust Litigation. AstraZeneca AB v. United "
             "Food & Commercial Workers Unions",
             "In re Nexium Antitrust Litig."),
            ("In re Rhodium Encore LLC, Debtors. 345 Partners SPV2 LLC, "
             "Plaintiffs, v. Nichols", "In re Rhodium Encore LLC, Debtors"),
            ("In re MCP No. 165, Emergency Temporary Standard, 86 Fed. Reg. "
             "61402. Massachusetts Building Trades Council v. OSHA",
             "In re MCP No. 165, Emergency Temp. Standard, 86 Fed. Reg. "
             "61402"),
        ])

    def test_no_cut_after_an_initial_or_inside_a_parenthesis(self):
        for caption in (
                "Van Kingsley Sullivan & Ronald C. Unterberger v. Kappa",
                "In re Adoption of T.R.M., An Indian Child. & J.Q., (Nat. "
                "Mother) v. D.R.L."):
            with self.subTest(caption=caption):
                self.assertEqual(_cut_companion_cases(caption), caption)

    def test_a_municipal_party_named_first_is_the_party(self):
        self.assertNames([
            ("Lyons v. City of Los Angeles, Doe Crupi, Doe Hills",
             "Lyons v. City of Los Angeles"),
            ("Patel v. City of Los Angeles, a Municipal Corporation",
             "Patel v. City of Los Angeles"),
            ("Knick v. Twp. of Scott, Pennsylvania",
             "Knick v. Township of Scott"),
            ("Sadowsky v. City of N.Y.", "Sadowsky v. City of New York"),
            # Two parties joined, not one county.
            ("Purdue Pharma L.P. v. Kentucky & Pike County",
             "Purdue Pharma L.P. v. Kentucky"),
        ])


class PartyListTests(unittest.TestCase):
    """Only the first party on each side is cited (rule 10.2.1(a)), however
    the caption lists the rest."""

    def assertNames(self, cases):
        for raw, want in cases:
            with self.subTest(raw=raw):
                got = abbreviate_case_name(raw)
                self.assertEqual(got, want)
                self.assertEqual(abbreviate_case_name(got), got)

    def test_semicolons_separate_parties(self):
        self.assertNames([
            ("Jason Wolford; Alison Wolford; Atom Kasprzycki; Haw. Firearms "
             "Coal. v. Lopez", "Wolford v. Lopez"),
            ("Tex.; State of La., Plaintiffs-Appellees, v. U.S.; Alejandro "
             "Mayorkas, Sec'y, U.S. Dep't of Homeland Sec.",
             "Texas v. United States"),
            ("Fasano v. Fed. Rsrv. Bank of N.Y.; Ron Henry; Cynthia Ramos",
             "Fasano v. Fed. Rsrv. Bank of N.Y."),
        ])

    def test_an_in_re_matter_title_is_no_party_list(self):
        name = ("In re MCP No. 165, Occupational Safety & Health Admin., "
                "Interim Final Rule: Covid-19 Vaccination & Testing; "
                "Emergency Temp. Standard")
        self.assertIn("; Emergency Temp. Standard", abbreviate_case_name(name))

    def test_a_role_designation_closes_the_first_party(self):
        self.assertNames([
            ("Ethel Louise Hill, Plaintiff-Appellant, v. Lockheed Martin "
             "Logistics Mgmt., Inc., Defendant-Appellee, Equal Emp. "
             "Opportunity Comm'n, Amicus Supporting Appellant.",
             "Hill v. Lockheed Martin Logistics Mgmt., Inc."),
            ("Raymond Interior Sys., Inc., Petitioner, v. Nat'l Lab. Rels. "
             "Bd., Respondent. S. Cal. Painters & Allied Trades Dist. "
             "Council No. 36, Intervenor.",
             "Raymond Interior Sys., Inc. v. NLRB"),
            ("Frigaliment Importing Co. Plaintiff v. B.N.S. Int'l Sales "
             "Corp.", "Frigaliment Importing Co. v. B.N.S. Int'l Sales Corp."),
            ("Pa., Complainant v. Wheeling & Belmont Bridge Co.",
             "Pennsylvania v. Wheeling & Belmont Bridge Co."),
        ])

    def test_a_firms_designator_closes_its_name(self):
        self.assertNames([
            ("Suburban Restoration Co. Plaintiff-Appellant v. Acmat Corp., "
             "Laborers' Int'l Union of N. Am., Loc. 665",
             "Suburban Restoration Co. v. Acmat Corp."),
            ("Leinani Deslandes & Stephanie Turner v. McDonald's USA, LLC, & "
             "McDonald's Corp.", "Deslandes v. McDonald's USA, LLC"),
            ("Fisher v. Swift Transp. Co. & J & D Truck Repair",
             "Fisher v. Swift Transp. Co."),
            # …but not before another designator.
            ("Bear Stearns & Co., Inc. v. Smith",
             "Bear Stearns & Co. v. Smith"),
        ])

    def test_a_person_after_a_comma_is_another_party(self):
        self.assertNames([
            ("Lynch v. N.J. Educ. Ass'n, Betty Kraemer, Karen Joseph",
             "Lynch v. N.J. Educ. Ass'n"),
            ("North Carolina v. Robert Lee Neal, & Eugene Davis",
             "North Carolina v. Neal"),
        ])

    def test_descriptions_and_representatives_go(self):
        # Rule 10.2.1(e).
        self.assertNames([
            ("Apple Computer, Inc., a California Corporation v. Microsoft "
             "Corporation, a Delaware Corporation",
             "Apple Comput., Inc. v. Microsoft Corp."),
            ("Action Apartment Ass'n a Cal. Corp. v. Santa Monica Rent "
             "Control Bd.", "Action Apartment Ass'n v. Santa Monica Rent "
             "Control Bd."),
            ("Louisiana, by & through its Att'y Gen., Jeff Landry v. Biden",
             "Louisiana v. Biden"),
            ("A.N., a Minor, by & Through Her Next Friend, J.N. v. Jackson "
             "R-II Sch. Dist.", "A.N. v. Jackson R-II Sch. Dist."),
            ("John P. Van Ness & Marcia His Wife v. Pacard",
             "Van Ness v. Pacard"),
            ("John Auvil et ux; et al. v. CBS 60 Minutes; et al.",
             "Auvil v. CBS 60 Minutes"),
            ("Slack Techs., LLC, fka Slack Techs., Inc. v. Pirani",
             "Slack Techs., LLC v. Pirani"),
        ])

    def test_a_cut_never_leaves_a_parenthesis_open(self):
        self.assertNames([
            ("Lanter Courier v. Indus. Comm'n, et al. (Kay Whitis, "
             "Appellee)", "Lanter Courier v. Indus. Comm'n"),
        ])

    def test_a_lowered_middle_initial_is_no_separator(self):
        # Lorenzo v. SEC, 587 U.S. 71 (2019): Francis V. Lorenzo.
        self.assertNames([
            ("Francis v. Lorenzo, Petitioner v. Securities and Exchange "
             "Commission", "Lorenzo v. SEC"),
        ])
        # A caption that lost its "ex dem." is left whole rather than cut
        # to another case's name.
        self.assertIn("Lamphire", abbreviate_case_name(
            "Jackson v. Hart, Plaintiff in Error v. Elias Lamphire, "
            "Defendant in Error"))


class AcronymCaseTests(unittest.TestCase):
    """Acronyms keep their capitals through a caption set in capitals, and
    get them back from a title-casing pass that took them away."""

    def test_a_caption_in_capitals_keeps_its_acronyms(self):
        for raw, want in (
                ("RENO v. ACLU", "Reno v. ACLU"),
                ("NATIONAL BROADCASTING CO. v. SBC WARBURG, INC.",
                 "National Broadcasting Co. v. SBC Warburg, Inc."),
                ("UNITED FEDERATION OF TEACHERS, AFT NYSUT, AFL-CIO",
                 "United Federation of Teachers, AFT NYSUT, AFL-CIO"),
                ("FASANO v. FED. RSRV. BANK OF N.Y.; RON HENRY",
                 "Fasano v. Fed. Rsrv. Bank of N.Y.; Ron Henry"),
                ("JACKSON R-II SCH. DIST.", "Jackson R-II Sch. Dist."),
                # An abbreviation, not an agency: its period says so.
                ("ALLSTATE INS. CO. v. HAGUE", "Allstate Ins. Co. v. Hague"),
                ("ST PAUL FIRE & MARINE", "St Paul Fire & Marine")):
            with self.subTest(raw=raw):
                self.assertEqual(normal_case_caption(raw), want)

    def test_a_title_cased_acronym_is_restored(self):
        for raw, want in (
                ("Reno v. Aclu", "Reno v. ACLU"),
                ("Nifla v. Becerra", "NIFLA v. Becerra"),
                ("Deslandes v. McDonald's Usa, LLC",
                 "Deslandes v. McDonald's USA, LLC"),
                ("Oil, Chem. & Atomic Workers Int'l Union, Afl-cio v. Delta "
                 "Refin. Co.",
                 "Oil, Chem. & Atomic Workers Int'l Union, AFL-CIO v. Delta "
                 "Refin. Co."),
                ("Fairfax v. Cbs Corp.", "Fairfax v. CBS Corp."),
                ("Ins v. Chadha", "INS v. Chadha"),
                ("A&m Records, Inc. v. Napster, Inc.",
                 "A&M Records, Inc. v. Napster, Inc."),
                ("Whittle v. U.s.", "Whittle v. United States"),
                ("Ware v. La. Dep't of Corr.", "Ware v. La. Dep't of Corr."),
                ("T.m. v. Univ. of Md. Med. Sys. Corp.",
                 "T.M. v. Univ. of Md. Med. Sys. Corp."),
                ("Home Depot U. S. A., Inc. v. Jackson",
                 "Home Depot U.S.A., Inc. v. Jackson"),
                ("Lohan v. Take-two Interactive Software, Inc.",
                 "Lohan v. Take-Two Interactive Software, Inc."),
                # A word stays a word.
                ("Toys R Us, Inc. v. Smith", "Toys R Us, Inc. v. Smith"),
                ("Ng v. Sessions", "Ng v. Sessions")):
            with self.subTest(raw=raw):
                got = abbreviate_case_name(raw)
                self.assertEqual(got, want)
                self.assertEqual(abbreviate_case_name(got), got)

    def test_prose_never_capitalizes_a_table_abbreviation(self):
        # The prose's "LA" (a postal code) is no reading of "La.".
        self.assertEqual(
            refine_caption_case(
                "Ware v. La. Dep't of Corr.",
                "Ware sued in Baton Rouge, LA. The LA office responded. "
                "LA law applies."),
            "Ware v. La. Dep't of Corr.",
        )

    def test_a_surname_set_in_capitals_is_typography(self):
        self.assertEqual(
            refine_caption_case(
                "David King v. Burwell",
                "David KING; Douglas HURST, Plaintiffs-Appellants. The "
                "petitioners are David KING and others. KING argues."),
            "David King v. Burwell",
        )
        # A word the prose writes in lowercase is a word.
        self.assertEqual(
            refine_caption_case(
                "Mellberg LLC v. Jovan Will",
                "Jovan WILL, et al., Defendants. WILL moved to dismiss, "
                "and the court will grant it. It will also rule."),
            "Mellberg LLC v. Jovan Will",
        )

    def test_small_words_follow_the_prose(self):
        # A sentence's opening "The", or a heading's "OF THE", is no reading
        # of the caption's "of the"…
        self.assertEqual(
            refine_caption_case(
                "Church of the Lukumi Babalu Aye, Inc. v. City of Hialeah",
                "CHURCH OF THE LUKUMI BABALU AYE, INC. v. CITY OF HIALEAH. "
                "The Lukumi Babalu Aye church leased land. Petitioner is the "
                "Church of the Lukumi Babalu Aye, and the Lukumi faithful "
                "gathered."),
            "Church of the Lukumi Babalu Aye, Inc. v. City of Hialeah")
        # …but the prose's "Di Re" still fixes a title-casing slip.
        self.assertEqual(
            refine_caption_case(
                "United States v. Di re",
                "Di Re was a passenger. The officers searched Di Re."),
            "United States v. Di Re")

    def test_an_acronym_the_opinion_defines(self):
        body = ("Petitioners are the National Institute of Family and Life "
                "Advocates (NIFLA) and two clinics. NIFLA argues the Act "
                "compels speech. NIFLA is right.")
        self.assertEqual(
            refine_caption_case("Nifla v. Becerra", body), "NIFLA v. Becerra")

    def test_each_piece_of_a_hyphenated_word(self):
        body = ("Americo Norberto Pena-Irala was the Inspector General. "
                "Pena-Irala had tortured Joelito. Later Pena-Irala left.")
        self.assertEqual(
            refine_caption_case("Filartiga v. Pena-irala", body),
            "Filartiga v. Pena-Irala",
        )


class UncommonNameTests(unittest.TestCase):
    """A person is cited by surname (rule 10.2.1(g)) even when no list knows
    the given name: the opinion's own prose, or the Census name files, say
    where the surname begins."""

    BRACKEEN = ("Chad and Jennifer Brackeen, a non-Indian couple, fostered "
                "A.L.M. The Brackeens then sought to adopt him. The "
                "Brackeens' petition was opposed.")

    def assertNames(self, cases, body=""):
        for raw, want in cases:
            with self.subTest(raw=raw):
                got = abbreviate_case_name(raw, body_text=body)
                self.assertEqual(got, want)
                self.assertEqual(abbreviate_case_name(got, body_text=body),
                                 got)

    def test_the_prose_names_the_surname(self):
        self.assertNames([("Haaland v. Chad Everet Brackeen",
                           "Haaland v. Brackeen")], self.BRACKEEN)
        self.assertNames([("Noem v. Pedro Vasquez Perdomo",
                           "Noem v. Vasquez Perdomo")],
                         "Respondent Vasquez Perdomo was detained. Vasquez "
                         "Perdomo's claim was certified.")
        self.assertNames([("United States v. Gary Evans Jackson",
                           "United States v. Jackson")],
                         "Gary Evans Jackson pleaded guilty. Jackson later "
                         "appealed, and Jackson's sentence was vacated.")
        # A mention that skips the middle name marks it as one.
        self.assertNames([("Kentucky v. Hollis Deshaun King",
                           "Kentucky v. King")],
                         "They found respondent Hollis King in the front "
                         "room.")

    def test_without_the_prose_an_unknown_name_stays_whole(self):
        self.assertEqual(
            abbreviate_case_name("Kentucky v. Hollis Deshaun King"),
            "Kentucky v. Hollis Deshaun King")
        self.assertEqual(
            abbreviate_case_name("Haaland v. Chad Everet Brackeen"),
            "Haaland v. Chad Everet Brackeen")

    def test_the_census_files_know_more_given_names(self):
        self.assertNames([
            ("Dulles v. Susanne Richter", "Dulles v. Richter"),
            ("Lilia Twombly v. AIG Life Ins. Co.",
             "Twombly v. AIG Life Ins. Co."),
            ("District of Columbia v. Dick Anthony Heller",
             "District of Columbia v. Heller"),
        ])

    def test_a_name_written_surname_first_stays_whole(self):
        self.assertNames([
            ("United States v. Wong Kim Ark",
             "United States v. Wong Kim Ark"),
            ("Weedin v. Chin Bow", "Weedin v. Chin Bow"),
        ], "Chin Bow applied for admission. The rights of Chin Bow are "
           "determined by statute.")

    def test_an_entity_is_no_person(self):
        self.assertNames([
            ("Citizens United v. FEC", "Citizens United v. FEC")],
            "Citizens United is a nonprofit. The United argument fails; "
            "citizens may speak.")
        self.assertNames([
            ("Sidibe v. Sutter Health", "Sidibe v. Sutter Health")],
            "Sutter Health operates hospitals. Health care costs rose.")
        self.assertNames([
            ("Murray v. Schooner Charming Betsy",
             "Murray v. Schooner Charming Betsy")],
            "The Charming Betsy was seized. Charming Betsy's cargo was sold.")
        self.assertNames([
            ("Florida Star v. B.J.F.", "Fla. Star v. B.J.F."),
            ("Morgan Stanley v. Smith", "Morgan Stanley v. Smith"),
            ("Barnes v. Glen Theatre", "Barnes v. Glen Theatre"),
            ("Montoya De Hernandez v. Smith", "Montoya De Hernandez v. Smith"),
            ("Van Ness v. Pacard", "Van Ness v. Pacard"),
        ])

    def test_titles_placeholders_and_capitals(self):
        self.assertNames([
            ("Whole Woman's Health v. Judge Austin Reeve Jackson",
             "Whole Woman's Health v. Jackson"),
            ("Fnu Tanzin v. Tanvir", "Tanzin v. Tanvir"),
            ("David KING v. Burwell", "King v. Burwell"),
            ("Philip Morris USA v. Williams",
             "Philip Morris USA v. Williams"),
            ("Murphy v. Terry Royal Warden, Okla. State Penitentiary",
             "Murphy v. Royal"),
        ])

    def test_a_caption_kept_whole_takes_the_surname_now(self):
        self.assertEqual(
            name_persons_by_surname(
                "Debra A. Haaland v. Chad Everet Brackeen", self.BRACKEEN),
            "Debra A. Haaland v. Brackeen")
        # A place is no person, however the prose names it.
        self.assertEqual(
            name_persons_by_surname("New Jersey v. T.L.O.",
                                    "Jersey was quiet. New Jersey appealed."),
            "New Jersey v. T.L.O.")

    def test_the_scholar_caption_reads_the_opinion(self):
        blocks = [
            Block("center", [Span("599 U.S. 255 (2023)")]),
            Block("center", [Span(
                "DEBRA A. HAALAND, SECRETARY OF THE INTERIOR, et al., "
                "Petitioners v. CHAD EVERET BRACKEEN, et al.")]),
            Block("center", [Span("Supreme Court of United States.")]),
            Block("para", [Span(self.BRACKEEN)]),
        ]
        self.assertEqual(
            abbreviate_case_name(_scholar_caption_name(blocks)),
            "Haaland v. Brackeen")

    def test_the_opinion_database_reads_the_opinion(self):
        html = ('<div id="gs_opinion">'
                "<center>599 U.S. 255</center>"
                "<center>DEBRA A. HAALAND, SECRETARY OF THE INTERIOR, "
                "PETITIONERS v. CHAD EVERET BRACKEEN</center>"
                "<center>Supreme Court of the United States.</center>"
                f"<p>{self.BRACKEEN}</p>"
                "</div>")
        record = extract_record(
            "https://scholar.google.com/scholar_case?case=5555", html)
        self.assertEqual(record["name"], "Haaland v. Brackeen")


class ConsolidatedAndSinglePartyCaptionTests(unittest.TestCase):
    def test_historical_bank_wrapper_uses_opinions_own_entity_name(self):
        # Osborn v. Bank of the United States, 22 U.S. (9 Wheat.) 738
        # (1824): the reporter caption gives the bank's formal charter style,
        # while Marshall and Johnson repeatedly call the party the Bank of
        # the United States in their opinions.
        blocks = [
            Block("center", [Span(
                "OSBORN and others, Appellants, v. The PRESIDENT, "
                "DIRECTORS, AND COMPANY OF THE BANK OF THE UNITED STATES, "
                "Respondents."
            )]),
            Block("para", [Span(
                "The Bank of the United States is an instrument of the "
                "national government."
            )]),
            Block("para", [Span(
                "The charter permits the Bank of the United States to sue."
            )]),
        ]

        name = _scholar_caption_name(blocks)
        self.assertEqual(
            name, "Osborn and others v. Bank of the United States")
        self.assertEqual(
            abbreviate_case_name(name), "Osborn v. Bank of the U.S.")

    def test_geographic_party_starts_joined_respondent_list(self):
        # General Telephone Co. of the Southwest v. United States,
        # 449 F.2d 846 (5th Cir. 1971): United States and the FCC are two
        # respondents, not one institutional name.  Only the first is kept,
        # and a geographic party is never shortened to "U.S.".
        blocks = [Block("center", [Span(
            "GENERAL TELEPHONE COMPANY OF the SOUTHWEST et al., Petitioners, "
            "v. UNITED STATES of America and Federal Communications "
            "Commission, Respondents, National Cable Television Association, "
            "Inc., et al., Intervenors."
        )])]

        self.assertEqual(
            abbreviate_case_name(_scholar_caption_name(blocks)),
            "Gen. Tel. Co. of the Sw. v. United States",
        )
        self.assertNotIn(
            "southwest",
            caption_case_reference_tokens(
                "General Telephone Company of the Southwest v. United States",
                "",
            ),
        )
        self.assertEqual(
            abbreviate_case_name(
                "National Labor Relations Board v. "
                "Jones and Laughlin Steel Corporation"
            ),
            "NLRB v. Jones & Laughlin Steel Corp.",
        )

    def test_in_re_caption_uses_alias_role_and_page_markers_as_boundaries(self):
        blocks = [
            Block("center", [
                Span(
                    "IN RE: IMERYS TALC AMERICA, INC., a/k/a Luzenac "
                    "America, Inc. a/k/a Imerys Talc Ohio Inc. a/k/a "
                    "Imerys Talc Delaware, Inc., et al., Debtors "
                ),
                Span("*362", pagenum=True),
                Span(" Cyprus Historical Excess Insurers, Appellants."),
            ]),
            Block("para", [Span(
                "Appellees Imerys Talc America, Inc. and its affiliates "
                "filed for bankruptcy."
            )]),
        ]

        name = _scholar_caption_name(blocks)

        self.assertEqual(
            abbreviate_case_name(name),
            "In re Imerys Talc Am., Inc.",
        )

    def test_quoted_in_rem_vessel_caption_drops_the_quotes(self):
        # The Scotland, 105 U.S. 24 (1882): the reporter prints the vessel's
        # name in quotation marks ("THE “SCOTLAND.”").  The quotes are the
        # reporter's typography — the citation name is "The Scotland", with
        # the ship's capital and its rule-10.2.1(d) "The" intact.
        blocks = [
            Block("center", [Span("105 U.S. 24 (____)")]),
            Block("center", [Span("THE “SCOTLAND.”")]),
            Block("center", [Span("Supreme Court of United States.")]),
            Block("para", [Span(
                "The case was argued by Mr. William Allen Butler, with "
                "whom was Mr. Thomas E. Stillman and Mr. John Chetwood, "
                "for the “Scotland,” and by Mr. James C. Carter and Mr. "
                "Robert D. Benedict, with whom was Mr. Joseph H. Choate, "
                "for the libellants."
            )]),
        ]

        name = _scholar_caption_name(blocks)

        self.assertEqual(name, "The “Scotland.”")
        self.assertEqual(abbreviate_case_name(name), "The Scotland")

    def test_a_second_vessel_in_the_caption_is_a_companion_case(self):
        # The Paquete Habana, 175 U.S. 677 (1900): two fishing smacks, seized
        # off Cuba and condemned as prize, were appealed and decided together,
        # so the reports head the case with both ships and the docket line
        # carries both numbers.  The citation is to the first alone (rule
        # 10.2.1(b)) — and neither of the older companion cuts could see the
        # boundary, there being no party, and so no "v.", anywhere in it.
        # Scholar sets the two names as one centered heading broken by a rule.
        blocks = [
            Block("center", [Span("175 U.S. 677 (1900)")]),
            Block("center", [Span("THE PAQUETE HABANA."), Span("\n"),
                             Span("THE LOLA.")]),
            Block("center", [Span("Nos. 395, 396.")]),
            Block("center", [Span("Supreme Court of United States.")]),
            # The line naming the court below rides the header as ordinary
            # text, and its capitals are what refine_caption_case reads the
            # vessel's own "THE" from.
            Block("para", [Span(
                "APPEALS FROM THE DISTRICT COURT OF THE UNITED STATES FOR "
                "THE SOUTHERN DISTRICT OF FLORIDA."
            )]),
            Block("para", [Span(
                "These are two appeals from decrees of the District Court of "
                "the United States for the Southern District of Florida "
                "condemning two fishing vessels and their cargoes as prize "
                "of war."
            )]),
        ]

        self.assertEqual(
            abbreviate_case_name(_scholar_caption_name(blocks)),
            "The Paquete Habana",
        )

    def test_the_in_rem_companion_cut_leaves_one_ship_alone(self):
        cut = _cut_companion_cases
        # Both ships, however the source cases them.
        self.assertEqual(cut("THE PAQUETE HABANA. THE LOLA."),
                         "THE PAQUETE HABANA.")
        self.assertEqual(cut("The Paquete Habana. The Lola."),
                         "The Paquete Habana.")
        # One ship, whatever its name holds: nothing to cut.
        for caption in ("THE PAQUETE HABANA.", "THE SCOTLAND.",
                        "THE ST. LAWRENCE.", "THE JAMES G. SWAN.",
                        "THE NEW YORK & CUBA MAIL S.S. CO."):
            self.assertEqual(cut(caption), caption)
        # An abbreviated given name ends a party, never a case — "The William
        # the Fourth" is a single ship, not the Wm. and the Fourth.
        self.assertEqual(cut("The Wm. The Fourth."), "The Wm. The Fourth.")
        # A caption with parties in it belongs to the older strategies, which
        # read the separator rather than the ship's "The" — however the source
        # happens to case that separator.
        for caption in ("THE STEAMSHIP CO. v. The Lola.",
                        "THE ACME V. THE LOLA.", "THE ACME VS. THE LOLA."):
            self.assertEqual(cut(caption), caption)

    def test_a_doubled_initial_survives_the_repeated_abbreviation_collapse(self):
        # A.A.R.P. v. Trump, 605 U.S. 246 (2025): the applicants are
        # anonymized to initials, and the reporter spaces them out.  The
        # collapse that folds a consolidated caption's repeated abbreviation
        # ("United States U.S. U.S.") used to read "A. A." as one of those
        # and hand back "A.R.P." — the wrong party, and a Bluebook cite to a
        # case that does not exist.
        blocks = [
            Block("center", [Span("145 S.Ct. 1364 (2025)")]),
            Block("center", [Span(
                "A. A. R. P., et al. v. Donald J. TRUMP, President of the "
                "United States, et al."
            )]),
            Block("center", [Span("Supreme Court of United States.")]),
        ]

        name = _scholar_caption_name(blocks)

        self.assertEqual(name, "A. A. R. P. v. Trump")
        self.assertEqual(abbreviate_case_name(name), "A.A.R.P. v. Trump")

    def test_a_doubled_initial_survives_for_the_companion_case(self):
        # W.M.M. v. Trump, 606 U.S. ___ (2025), argued the same term.
        blocks = [Block("center", [Span(
            "W. M. M., et al. v. Donald J. TRUMP, President of the United "
            "States, et al."
        )])]

        self.assertEqual(
            abbreviate_case_name(_scholar_caption_name(blocks)),
            "W.M.M. v. Trump",
        )

    def test_a_consolidated_captions_repeated_abbreviation_still_collapses(self):
        blocks = [Block("center", [Span(
            "ACME OIL CO. v. UNITED STATES of America U.S. U.S."
        )])]

        self.assertEqual(
            _scholar_caption_name(blocks),
            "Acme Oil Co. v. United States of America U.S.",
        )

    def test_lowercase_words_do_not_end_a_procedural_case_name(self):
        blocks = [Block("center", [Span(
            "IN RE TITLE, BALLOT TITLE & SUBMISSION CLAUSE FOR 2015-2016 #156"
        )])]

        self.assertEqual(
            _scholar_caption_name(blocks),
            "In re Title, Ballot Title & Submission Clause for 2015-2016 #156",
        )

    def test_multiple_party_words_are_omitted(self):
        # Rule 10.2.1(a): "et Wife", "et vir", "and Others" drop.
        self.assertEqual(
            abbreviate_case_name("Calder et Wife v. Bull et Wife"),
            "Calder v. Bull",
        )
        self.assertEqual(
            abbreviate_case_name("Troxel et vir v. Granville"),
            "Troxel v. Granville",
        )
        self.assertEqual(
            abbreviate_case_name("Wayman & another v. Southard & another"),
            "Wayman v. Southard",
        )

    def test_descriptive_parenthetical_drops(self):
        self.assertEqual(
            abbreviate_case_name(
                "Escola v. Coca Cola Bottling Co. of Fresno "
                "(a Corporation)"),
            "Escola v. Coca Cola Bottling Co. of Fresno",
        )

    def test_alias_clauses_drop_but_full_name_stays(self):
        # NIFLA v. Becerra, 138 S. Ct. 2361 (2018): the d/b/a alias is not
        # the Bluebook name — the first party keeps its full (abbreviated)
        # name and the alias clause drops.
        self.assertEqual(
            abbreviate_case_name(
                "National Institute of Family and Life Advocates, dba "
                "NIFLA, et al., Petitioners, v. Xavier Becerra, Attorney "
                "General of California, et al."),
            "Nat'l Inst. of Fam. & Life Advocs. v. Becerra",
        )
        self.assertEqual(
            abbreviate_case_name(
                "United States v. Mitchell Robertson a/k/a Mitchell "
                "Robinson a/k/a Bryheer McMichael"),
            "United States v. Robertson",
        )
        # Bare "aka" is a real surname, never an alias marker.
        self.assertEqual(
            abbreviate_case_name("Ethel Aka v. Washington Hospital Center"),
            "Aka v. Wash. Hosp. Ctr.",
        )

    def test_in_re_alias_chain_and_role_tail_drop_as_one_unit(self):
        self.assertEqual(
            abbreviate_case_name(normal_case_caption(
                "IN RE: IMERYS TALC AMERICA, INC., a/k/a Luzenac America, "
                "Inc. a/k/a Imerys Talc Ohio Inc. a/k/a Imerys Talc "
                "Delaware, Inc., et al., Debtors *362 Cyprus Historical "
                "Excess Insurers, Appellants."
            )),
            "In re Imerys Talc Am., Inc.",
        )

    def test_turned_comma_apostrophe_surname(self):
        # Johnson v. M'Intosh, 21 U.S. (8 Wheat.) 543 (1823): OCR renders
        # the turned-comma apostrophe as U+2018 ("M‘INTOSH"); the caption
        # party is the single nominal ejectment plaintiff and stays whole
        # (CAP's own name_abbreviation is "Johnson & Graham's Lessee v.
        # McIntosh").
        self.assertEqual(
            normal_case_caption("WILLIAM M‘INTOSH."),
            "William M'Intosh.",
        )
        self.assertEqual(
            abbreviate_case_name(
                "Johnson & Graham's Lessee v. William M‘intosh"),
            "Johnson & Graham's Lessee v. M'intosh",
        )

    def test_zf_automotive_consolidated_caption(self):
        # ZF Automotive US, Inc. v. Luxshare, Ltd., 596 U.S. 619 (2022):
        # the consolidated AlixPartners case follows the first respondent's
        # "LTD." and is omitted (rule 10.2.1(b)).
        right = _cut_companion_cases(
            "LUXSHARE, LTD. AlixPartners, LLP, et al., Petitioners v. The "
            "Fund for Protection of Investors' Rights in Foreign States.")
        self.assertEqual(right, "LUXSHARE, LTD.")
        self.assertEqual(
            abbreviate_case_name(
                "ZF Automotive US, Inc., et al., Petitioners, v. "
                "Luxshare, Ltd."),
            "ZF Auto. US, Inc. v. Luxshare, Ltd.",
        )

    def test_geographic_first_party_is_not_cut_from_a_firm_name(self):
        # "New York & Cuba Mail Steamship Co." is one business that merely
        # opens with a place — nothing is omitted; a true government
        # co-party list still reduces to its first party.
        self.assertEqual(
            abbreviate_case_name(
                "New York & Cuba Mail Steamship Company v. The Barge Sadie"),
            "N.Y. & Cuba Mail S.S. Co. v. Barge Sadie",
        )
        self.assertEqual(
            abbreviate_case_name(
                "Texas & Pacific Railway Company v. Behymer"),
            "Tex. & Pac. Ry. Co. v. Behymer",
        )
        self.assertEqual(
            abbreviate_case_name(
                "United States and Federal Communications Commission "
                "v. Acme Corp."),
            "United States v. Acme Corp.",
        )

    def test_companion_cases_cut_at_the_earliest_boundary(self):
        # Bostock: the companion party's own periods ("Inc.") defeat the
        # simple lookahead; the fallback cuts before "Altitude".
        self.assertEqual(
            _cut_companion_cases(
                "CLAYTON COUNTY, GEORGIA. Altitude Express, Inc., et al., "
                "Petitioners v. Melissa Zarda"),
            "CLAYTON COUNTY, GEORGIA.",
        )
        # Olmstead: "GREEN ET AL. v. SAME." defeats the lookahead at the
        # first boundary but not the second — the earliest cut wins.
        self.assertEqual(
            _cut_companion_cases(
                "UNITED STATES. GREEN ET AL. v. SAME. McINNIS v. SAME."),
            "UNITED STATES.",
        )
        # An entity abbreviation's period is a boundary only when the name
        # does not continue past it.
        self.assertEqual(
            _cut_companion_cases(
                "ST. PAUL FIRE & MARINE INS. CO. SAME v. OTHER."),
            "ST. PAUL FIRE & MARINE INS. CO.",
        )
        self.assertEqual(
            _cut_companion_cases("Acme Co. of America"),
            "Acme Co. of America",
        )
        # ZF Automotive US, Inc. v. Luxshare, Ltd., 596 U.S. 619 (2022):
        # the first respondent's own "Ltd." closes the case, and the
        # consolidated AlixPartners case follows.
        self.assertEqual(
            _cut_companion_cases(
                "LUXSHARE, LTD. AlixPartners, LLP, et al., Petitioners v. "
                "The Fund for Protection of Investors' Rights in Foreign "
                "States."),
            "LUXSHARE, LTD.",
        )
        # …but a continuing name keeps its entity abbreviation mid-name.
        self.assertEqual(
            _cut_companion_cases(
                "TRAVELERS INS. CO. OF HARTFORD. Acme Widgets, Inc., "
                "Petitioners v. Doe"),
            "TRAVELERS INS. CO. OF HARTFORD.",
        )


class RefineCaptionCaseTests(unittest.TestCase):
    """The opinion's own prose settles casing an all-caps caption destroys."""

    def test_body_restores_initialism_capitalization(self):
        # US Dominion, Inc. v. Byrne, 600 F. Supp. 3d 24 (D.D.C. 2022):
        # "US DOMINION" title-cases to "Us Dominion"; the body knows better.
        name = normal_case_caption("US DOMINION, INC. v. BYRNE.")
        self.assertEqual(name, "Us Dominion, Inc. v. Byrne.")
        body = ("Plaintiffs US Dominion, Inc., Dominion Voting Systems, "
                "Inc., and their affiliates sued Patrick Byrne. US "
                "Dominion, Inc. alleges defamation.")
        self.assertEqual(
            refine_caption_case(name, body),
            "US Dominion, Inc. v. Byrne.",
        )

    def test_body_confirms_title_case_where_us_is_a_word(self):
        name = normal_case_caption("TOYS R US, INC. v. SMITH")
        body = "Toys R Us, Inc. operates stores. Smith sued Toys R Us, Inc."
        self.assertEqual(
            refine_caption_case(name, body), "Toys R Us, Inc. v. Smith")

    def test_prose_articles_never_decapitalize_the_name(self):
        self.assertEqual(
            refine_caption_case(
                "The Boeing Co. v. Smith",
                "Smith sued the Boeing Company. Later the Boeing Company "
                "answered."),
            "The Boeing Co. v. Smith",
        )

    def test_all_caps_headings_carry_no_signal(self):
        self.assertEqual(
            refine_caption_case(
                "Toys R Us, Inc. v. Smith",
                "TOYS R US IS LIABLE. The court holds Toys R Us, Inc. "
                "liable."),
            "Toys R Us, Inc. v. Smith",
        )

    def test_no_body_is_a_no_op(self):
        self.assertEqual(
            refine_caption_case("Us Dominion, Inc. v. Byrne", ""),
            "Us Dominion, Inc. v. Byrne",
        )

    def test_single_token_party_uses_unanchored_evidence(self):
        # "IBM v. JOHNSON" leaves IBM with no adjacent anchor token; the
        # bare-word fallback still corrects it given repeated evidence.
        self.assertEqual(
            refine_caption_case(
                "Ibm v. Johnson",
                "IBM manufactures computers. Johnson worked for IBM "
                "until IBM terminated him."),
            "IBM v. Johnson",
        )

    def test_spelled_out_prose_still_anchors_caption_abbreviation(self):
        # The caption's "Corp." anchors against the body's "Corporation".
        self.assertEqual(
            refine_caption_case(
                "It Corp. v. County of Imperial",
                "IT Corporation contracted with the County. "
                "IT Corporation then sued."),
            "IT Corp. v. County of Imperial",
        )

    def test_am_general_initialism_restored(self):
        self.assertEqual(
            refine_caption_case(
                normal_case_caption(
                    "AM GENERAL LLC v. ACTIVISION BLIZZARD, INC."),
                "AM General LLC manufactures the Humvee. "
                "AM General LLC sued Activision."),
            "AM General LLC v. Activision Blizzard, Inc.",
        )

    def test_single_anchored_gmac_occurrence_restores_initialism(self):
        # Murray v. GMAC Mortgage Corp., 434 F.3d 948 (7th Cir. 2006):
        # the opinion spells the full name only once, then uses "GMACM".
        name = normal_case_caption(
            "MURRAY v. GMAC MORTGAGE CORPORATION")
        body = (
            "After her debts had been discharged, Nancy Murray received a "
            "credit solicitation from GMAC Mortgage, which had learned her "
            "address from credit bureaus. GMACM offered Murray a loan."
        )

        refined = refine_caption_case(name, body)

        self.assertEqual(refined, "Murray v. GMAC Mortgage Corporation")
        self.assertEqual(caption_case_reference_tokens(refined, body), ())
        self.assertEqual(
            abbreviate_case_name(refined),
            "Murray v. GMAC Mortg. Corp.",
        )

    def test_caps_styled_surnames_are_typography_not_spelling(self):
        # Opinions that set party surnames in caps mid-prose must not
        # rewrite the caption's ordinary spelling.
        self.assertEqual(
            refine_caption_case(
                "United States v. Smith",
                "SMITH was convicted. SMITH argues the evidence was "
                "insufficient. SMITH appeals."),
            "United States v. Smith",
        )

    def test_ampersand_and_dotted_initialisms_keep_caps(self):
        self.assertEqual(
            normal_case_caption("AT&T CORP. v. IOWA UTILITIES BOARD"),
            "AT&T Corp. v. Iowa Utilities Board",
        )
        self.assertEqual(
            normal_case_caption("A&M RECORDS, INC. v. NAPSTER, INC."),
            "A&M Records, Inc. v. Napster, Inc.",
        )
        self.assertEqual(
            normal_case_caption("MERCEXCHANGE, L.L.C."),
            "Mercexchange, L.L.C.",
        )

    def test_opinion_evidence_prevents_entity_reference_lookup(self):
        name = normal_case_caption("NBCUNIVERSAL MEDIA, LLC v. DOE")
        body = ("NBCUniversal Media, LLC distributes programming. "
                "Doe later contacted NBCUniversal Media.")
        refined = refine_caption_case(name, body)

        self.assertEqual(refined, "NBCUniversal Media, LLC v. Doe")
        self.assertEqual(caption_case_reference_tokens(refined, body), ())

    def test_reference_can_only_donate_case_to_existing_words(self):
        name = normal_case_caption("NBCUNIVERSAL MEDIA, LLC v. DOE")
        unresolved = caption_case_reference_tokens(name, "")

        self.assertEqual(unresolved, ("nbcuniversal",))
        self.assertEqual(
            apply_caption_case_reference(
                name,
                "NBCUniversal Holdings, LLC v. Completely Different Party",
                unresolved,
            ),
            "NBCUniversal Media, LLC v. Doe",
        )

    def test_reference_with_different_parties_cannot_substitute_caption(self):
        name = "Nbcuniversal Media, LLC v. Doe"
        self.assertEqual(
            apply_caption_case_reference(
                name, "Another Plaintiff v. Another Defendant",
                ("nbcuniversal",),
            ),
            name,
        )


class CitationOverrideTests(unittest.TestCase):
    def test_override_is_shared_by_parallel_reporters(self):
        item = {
            "cluster_id": 123,
            "citation": ["81 F.4th 699", "2023-2 Trade Cas. 81465"],
        }
        keys = citation_identity_keys(item, "81 F.4th 699")
        saved = update_overrides({}, keys, "Deslandes v. McDonald's USA, LLC, 81 F.4th 699 (7th Cir. 2023)")
        self.assertEqual(find_override(saved, ["cl:123"]), saved["cl:123"])
        self.assertIn("cite:81:f.4th:699", saved)

    def test_override_and_pin_work_across_federal_cases_aliases(self):
        fed_keys = citation_identity_keys({}, "18 Fed. Cas. 9")
        saved = update_overrides(
            {}, fed_keys, "The Nestor, 18 Fed. Cas. 9 (C.C.D. Me. 1831)",
        )
        short_keys = citation_identity_keys({}, "18 F. Cas. 9")

        self.assertEqual(
            find_override(saved, short_keys),
            "The Nestor, 18 Fed. Cas. 9 (C.C.D. Me. 1831)",
        )
        self.assertEqual(
            add_pin_to_base(
                "The Nestor, 18 Fed. Cas. 9 (C.C.D. Me. 1831)", "12",
            ),
            "The Nestor, 18 Fed. Cas. 9, 12 (C.C.D. Me. 1831)",
        )

    def test_pin_is_inserted_before_parenthetical(self):
        base = "Deslandes v. McDonald's USA, LLC, 81 F.4th 699 (7th Cir. 2023)"
        self.assertEqual(
            add_pin_to_base(base, "703"),
            "Deslandes v. McDonald's USA, LLC, 81 F.4th 699, 703 (7th Cir. 2023)",
        )
        self.assertEqual(add_pin_to_base(base, "699"), base)

    def test_writer_parenthetical_follows_edited_base(self):
        plain, name = format_edited_citation(
            "Example v. Example, 1 F.4th 10 (2d Cir. 2021)",
            "12",
            ("Smith, J., dissenting",),
        )
        self.assertEqual(name, "Example v. Example")
        self.assertEqual(
            plain,
            "Example v. Example, 1 F.4th 10, 12 (2d Cir. 2021) "
            "(Smith, J., dissenting).",
        )


class ReporterAndDecisionDateTests(unittest.TestCase):
    def test_early_scotus_uses_modern_and_nominative_reporters(self):
        examples = [
            ("3 U.S. 199", "3 Dall. 199", "3 U.S. (3 Dall.) 199"),
            ("10 U.S. 87", "6 Cranch 87", "10 U.S. (6 Cranch) 87"),
            ("23 U.S. 66", "10 Wheat. 66", "23 U.S. (10 Wheat.) 66"),
            ("36 U.S. 420", "11 Pet. 420", "36 U.S. (11 Pet.) 420"),
        ]
        for modern, nominative, expected in examples:
            with self.subTest(modern=modern):
                self.assertEqual(
                    _nominative_display_cite(modern, [modern, nominative]),
                    expected,
                )

    def test_early_scotus_window_title_does_not_repeat_combined_reporters(self):
        win = object.__new__(_ScholarTextWindow)
        win._base_citation_override = ""
        win._bb = {
            "name": "Stuart v. Laird",
            "cite": "5 U.S. 299",
            "display_cite": "5 U.S. (1 Cranch) 299",
            "court": "",
            "year": "1803",
            "omit_parenthetical": "",
        }
        win._header_cites = ["5 U.S. 299", "1 Cranch 299"]
        win._item = {"citation": ["5 U.S. 299", "1 Cranch 299"]}

        self.assertEqual(
            win._title_citation(),
            "Stuart v. Laird, 5 U.S. (1 Cranch) 299 (1803)",
        )

    def test_early_scotus_window_title_keeps_parallels_without_combined_cite(self):
        win = object.__new__(_ScholarTextWindow)
        win._base_citation_override = ""
        win._bb = {
            "name": "Stuart v. Laird",
            "cite": "5 U.S. 299",
            "display_cite": "5 U.S. 299",
            "court": "",
            "year": "1803",
            "omit_parenthetical": "",
        }
        win._header_cites = ["1 Cranch 299"]
        win._item = {"citation": []}

        self.assertEqual(
            win._title_citation(),
            "Stuart v. Laird, 5 U.S. 299, 1 Cranch 299 (1803)",
        )

    def test_scotus_header_year_beats_rehearing_date(self):
        win = object.__new__(_ScholarTextWindow)
        win._item = {
            "case_name": "Korematsu v. United States",
            "citation": ["323 U.S. 214"],
            "court_id": "scotus",
            "date_filed": "1945-02-26",
        }
        win._blocks = [
            Block("center", [Span("Korematsu v. United States")]),
            Block("center", [Span("323 U.S. 214 (1944)")]),
            Block("para", [Span("MR. JUSTICE BLACK delivered the opinion.")]),
        ]

        bb = win._compute_bluebook_parts()

        self.assertEqual(bb["year"], "1944")

    def test_star_page_and_sct_page_numbers_are_not_years(self):
        # Cedar Point Nursery v. Hassid, 141 S. Ct. 2063 (2021): the header
        # ends with the star marker "*2066 Syllabus", and S. Ct. page
        # numbers (1600-2099) are indistinguishable from years — the
        # parenthesized year next to the citation controls.
        win = object.__new__(_ScholarTextWindow)
        win._item = {}
        win._blocks = [
            Block("center", [Span("141 S.Ct. 2063 (2021)")]),
            Block("center", [Span("594 U.S. 139")]),
            Block("center", [Span("CEDAR POINT NURSERY v. Victoria HASSID")]),
            Block("center", [Span("Decided June 23, 2021.")]),
            Block("heading", [Span("*2066 ", pagenum=True), Span("Syllabus")]),
            Block("para", [Span("CHIEF JUSTICE ROBERTS delivered the "
                                "opinion of the Court.")]),
        ]

        bb = win._compute_bluebook_parts()

        self.assertEqual(bb["year"], "2021")

    def test_body_heading_dates_do_not_supply_the_year(self):
        # United States v. Thomas, 818 F.3d 1230 (11th Cir. 2016): section
        # headings ("A. December 20, 2013 Suppression Hearing") fall inside
        # the first blocks and must not beat the citation's own year.
        win = object.__new__(_ScholarTextWindow)
        win._item = {}
        win._blocks = [
            Block("center", [Span("818 F.3d 1230 (2016)")]),
            Block("center", [Span("UNITED STATES v. Eric THOMAS")]),
            Block("center", [Span("United States Court of Appeals, "
                                  "Eleventh Circuit.")]),
            Block("heading", [Span("A. December 20, 2013 Suppression "
                                   "Hearing")]),
            Block("para", [Span("WILSON, Circuit Judge:")]),
        ]

        bb = win._compute_bluebook_parts()

        self.assertEqual(bb["year"], "2016")

    def test_official_state_reporter_is_source_independent_without_stars(self):
        win = object.__new__(_ScholarTextWindow)
        win._item = {
            "case_name": "People v. Aaron",
            "citation": ["299 N.W.2d 304"],
            "court_id": "mich",
            "date_filed": "1980-11-24",
        }
        win._blocks = [
            Block("center", [Span("People v. Aaron")]),
            Block("center", [Span("299 N.W.2d 304, 409 Mich. 672")]),
            Block("para", [Span("The Court holds as follows.")]),
        ]

        bb = win._compute_bluebook_parts()

        self.assertEqual(bb["display_cite"], "409 Mich. 672")


class StateCourtPartyTests(unittest.TestCase):
    """Rule 10.2.1(f): "State of," "Commonwealth of," and "People of" drop out
    of a party name, leaving the state's name — except when the citation is to
    a decision of that state's own courts, where the designation is what
    survives instead."""

    # The caption as static.case.law prints it (254 N.Y. 192).
    ZACKOWITZ = ("The People of the State of New York, Respondent, "
                 "v. Joseph Zackowitz, Appellant.")

    def test_state_high_court_keeps_the_people(self):
        self.assertEqual(
            abbreviate_case_name(self.ZACKOWITZ, court_state="new york"),
            "People v. Zackowitz",
        )

    def test_another_courts_citation_names_the_state(self):
        # People of the State of New York v. Tanella, 374 F.3d 141 (2d Cir.
        # 2004) — a federal court, so the state is named.
        self.assertEqual(
            abbreviate_case_name(
                "People of the State of New York v. Tanella"),
            "New York v. Tanella",
        )

    def test_state_and_commonwealth_designations(self):
        self.assertEqual(
            abbreviate_case_name("State of Washington v. Glucksberg",
                                 court_state="washington"),
            "State v. Glucksberg",
        )
        self.assertEqual(
            abbreviate_case_name("Commonwealth of Massachusetts v. John Smith",
                                 court_state="massachusetts"),
            "Commonwealth v. Smith",
        )

    def test_designation_survives_a_relator_clause(self):
        self.assertEqual(
            abbreviate_case_name(
                "People of the State of New York ex rel. Spitzer v. Grasso",
                court_state="new york"),
            "People ex rel. Spitzer v. Grasso",
        )

    def test_abbreviation_is_idempotent_and_state_spelling_agnostic(self):
        once = abbreviate_case_name(self.ZACKOWITZ, court_state="N.Y.")
        self.assertEqual(once, "People v. Zackowitz")
        self.assertEqual(
            abbreviate_case_name(once, court_state="new york"), once)

    def test_court_resolves_from_an_id_or_a_name(self):
        for court_id, court_name in (
            ("ny", ""),                              # CourtListener id
            ("ny-app-div", ""),                      # CAP's hyphenated slug
            ("", "N.Y."),                            # CAP name_abbreviation
            ("", "Court of Appeals of New York"),    # CourtListener name
        ):
            with self.subTest(court_id=court_id, court_name=court_name):
                self.assertEqual(
                    state_of_court(court_id, court_name), "new york")

    def test_a_federal_court_sitting_in_the_state_is_not_its_court(self):
        for court_id, court_name in (
            ("nysd", "United States District Court, S.D. New York"),
            ("ca2", "United States Court of Appeals for the Second Circuit"),
            ("scotus", "Supreme Court of the United States"),
            ("", ""),
        ):
            with self.subTest(court_id=court_id, court_name=court_name):
                self.assertEqual(state_of_court(court_id, court_name), "")

    def test_case_law_caption_cites_as_people_in_the_reader(self):
        # The whole path the reader runs for a static.case.law opinion: the
        # caption comes off the head matter, the court off CAP's metadata.
        win = object.__new__(_ScholarTextWindow)
        win._item = {
            "caseName": "",
            "citation": ["254 N.Y. 192"],
            "court": "N.Y.",
            "court_id": "ny",
            "dateFiled": "1930-07-08",
        }
        win._blocks = [
            Block("center", [Span(self.ZACKOWITZ)]),
            Block("center", [Span("(Argued June 9, 1930; "
                                  "decided July 8, 1930.)")]),
            Block("para", [Span("Cardozo, Ch. J.")]),
            Block("para", [Span("On November 10, 1929, shortly after "
                                "midnight, the defendant in Kings county "
                                "shot Frank Coppola and killed him.")]),
        ]

        bb = win._compute_bluebook_parts()

        self.assertEqual(bb["name"], "People v. Zackowitz")
        self.assertEqual(bb["cite"], "254 N.Y. 192")
        self.assertEqual(bb["year"], "1930")


class NominativeCitationSearchTests(unittest.TestCase):
    def test_search_variants_preserve_caption_and_pincite(self):
        self.assertEqual(
            _citation_search_variants(
                "Stuart v. Laird, 1 Cranch 299, 301"
            ),
            (
                "Stuart v. Laird, 1 Cranch 299, 301",
                "Stuart v. Laird, 5 U.S. 299, 301",
            ),
        )
        self.assertEqual(
            _citation_search_variants("8 Wall 168"),
            ("8 Wall 168", "75 U.S. 168"),
        )

    def test_ordinary_citation_does_not_gain_an_alias(self):
        self.assertEqual(
            _citation_search_variants("410 U.S. 113"),
            ("410 U.S. 113",),
        )

    def test_washington_reporter_aliases_expand_in_both_directions(self):
        self.assertEqual(
            _citation_search_variants("81 Wn. 2d 788"),
            ("81 Wn. 2d 788", "81 Wash. 2d 788"),
        )
        self.assertEqual(
            _citation_search_variants("81 Wash. 2d 788"),
            ("81 Wash. 2d 788", "81 Wn. 2d 788"),
        )
        self.assertEqual(
            _citation_search_variants(
                "Example v. State, 81 Wash 2d 788, 791"
            ),
            (
                "Example v. State, 81 Wash 2d 788, 791",
                "Example v. State, 81 Wash. 2d 788, 791",
                "Example v. State, 81 Wn. 2d 788, 791",
            ),
        )

    def test_federal_reporter_aliases_expand_in_both_directions(self):
        self.assertEqual(
            _citation_search_variants("18 F. Cas. 9"),
            ("18 F. Cas. 9", "18 Fed. Cas. 9"),
        )
        self.assertEqual(
            _citation_search_variants("18 Fed. Cas. 9"),
            ("18 Fed. Cas. 9", "18 F. Cas. 9"),
        )
        self.assertEqual(
            _citation_search_variants("35 Fed. Rep. 665"),
            ("35 Fed. Rep. 665", "35 F. 665"),
        )
        self.assertEqual(
            _citation_search_variants("514 Fed. Appx. 210"),
            ("514 Fed. Appx. 210", "514 F. App'x 210"),
        )

    def test_courtlistener_retries_us_reports_alias_only_after_miss(self):
        client = Mock()
        client.lookup_citation.side_effect = [
            [],
            [{
                "status": 200,
                "clusters": [{
                    "id": 75,
                    "case_name": "The Case",
                    "citations": ["75 U.S. 168"],
                    "court_id": "scotus",
                }],
            }],
        ]

        item = _cl_item_for_citation(client, "8 Wall 168")

        self.assertEqual(item["cluster_id"], 75)
        self.assertEqual(
            client.lookup_citation.call_args_list,
            [call("8 Wall 168"), call("75 U.S. 168")],
        )

    def test_courtlistener_retries_federal_cases_alias_only_after_miss(self):
        client = Mock()
        client.lookup_citation.side_effect = [
            [],
            [{
                "status": 200,
                "clusters": [{
                    "id": 18,
                    "case_name": "The Nestor",
                    "citations": ["18 F. Cas. 9"],
                    "court_id": "circtdme",
                }],
            }],
        ]

        item = _cl_item_for_citation(client, "18 Fed. Cas. 9")

        self.assertEqual(item["cluster_id"], 18)
        self.assertEqual(
            client.lookup_citation.call_args_list,
            [call("18 Fed. Cas. 9"), call("18 F. Cas. 9")],
        )

    def test_courtlistener_keeps_successful_typed_citation_authoritative(self):
        client = Mock()
        client.lookup_citation.return_value = [{
            "status": 200,
            "clusters": [{
                "id": 8,
                "case_name": "Different Cranch Case",
                "citations": ["1 Cranch 299"],
                "court_id": "cadc",
            }],
        }]

        item = _cl_item_for_citation(client, "1 Cranch 299")

        self.assertEqual(item["cluster_id"], 8)
        client.lookup_citation.assert_called_once_with("1 Cranch 299")

    def test_same_reporter_page_is_disambiguated_by_case_name(self):
        client = Mock()
        client.lookup_citation.return_value = [{
            "status": 300,
            "clusters": [
                {
                    "id": 1917,
                    "case_name": "In re Cooper",
                    "citations": ["243 F. 797"],
                    "court_id": "mad",
                },
                {
                    "id": 1916,
                    "case_name": "The Buena Ventura",
                    "citations": ["243 F. 797"],
                    "court_id": "nysd",
                },
            ],
        }]

        item = _cl_item_for_citation(
            client, "243 F. 797", name="The Buena Ventura",
        )

        self.assertEqual(item["cluster_id"], 1916)
        self.assertEqual(item["caseName"], "The Buena Ventura")

    def test_exact_name_breaks_tie_between_two_acceptable_name_matches(self):
        client = Mock()
        client.lookup_citation.return_value = [{
            "status": 300,
            "clusters": [
                {
                    "id": 2,
                    "case_name": "The Buena Ventura Shipping Co.",
                    "citations": ["243 F. 797"],
                },
                {
                    "id": 1,
                    "case_name": "The Buena Ventura",
                    "citations": ["243 F. 797"],
                },
            ],
        }]

        item = _cl_item_for_citation(
            client, "243 F. 797", name="The Buena Ventura",
        )

        self.assertEqual(item["cluster_id"], 1)

    @staticmethod
    def _orders_client():
        # 498 U.S. 807 grants certiorari in eleven cases, and CourtListener
        # answers 300 with all of them, most twice (four records shown).
        client = Mock()
        client.lookup_citation.return_value = [{"status": 300, "clusters": [
            {"id": 9099744, "case_name": "Carnival Cruise Lines, Inc. v. Shute",
             "citations": ["498 U.S. 807"]},
            {"id": 9099743, "case_name": "Carnival Cruise Lines, Inc. v. Shute",
             "citations": ["498 U.S. 807", "111 S. Ct. 39"]},
            {"id": 9099738, "case_name": "California v. Acevedo",
             "citations": ["498 U.S. 807"]},
            {"id": 9099737, "case_name": "California v. Acevedo",
             "citations": ["498 U.S. 807", "111 S. Ct. 39"]},
        ]}]
        return client

    def test_a_page_of_orders_with_no_name_is_not_guessed(self):
        # Acevedo's "We granted certiorari, 498 U. S. 807" opened Carnival
        # Cruise Lines, CourtListener's first record for the page.
        client = self._orders_client()
        self.assertIsNone(_cl_item_for_citation(client, "498 U.S. 807"))
        client.search.assert_not_called()   # nor guessed by full-text search

    def test_a_name_picks_its_order_off_the_page(self):
        item = _cl_item_for_citation(
            self._orders_client(), "498 U.S. 807",
            name="California v. Acevedo")
        self.assertEqual(item["cluster_id"], 9099737)   # the fuller record

    def test_two_records_of_one_case_are_no_choice(self):
        client = Mock()
        client.lookup_citation.return_value = [{"status": 300, "clusters": [
            {"id": 1, "case_name": "Carnival Cruise Lines, Inc. v. Shute",
             "citations": ["498 U.S. 807"]},
            {"id": 2, "case_name": "Carnival Cruise Lines v. Shute",
             "citations": ["498 U.S. 807", "111 S. Ct. 39"]},
        ]}]
        self.assertEqual(
            _cl_item_for_citation(client, "498 U.S. 807")["cluster_id"], 2)

    def test_highlighted_text_supplies_name_to_ambiguous_cite_dispatch(self):
        self.assertEqual(
            _citation_link_name(
                "The Buena Ventura, 243 F. 797, 799 (S.D.N.Y. 1916)",
                "243 F. 797",
            ),
            "The Buena Ventura",
        )
        app = Mock()
        parent = Mock()
        client = object()
        app._get_scholar.return_value = None
        app._get_client.return_value = client
        app._token_var.get.return_value = "token"
        app._try_open_citation.return_value = True

        class ImmediateThread:
            def __init__(self, *, target, daemon):
                self.target = target

            def start(self):
                self.target()

        with patch("courtlistener_gui.threading.Thread", ImmediateThread):
            _follow_brief_action(
                app,
                parent,
                ("cite", "243 F. 797@799"),
                snippet=(
                    "The Buena Ventura, 243 F. 797, 799 "
                    "(S.D.N.Y. 1916)"
                ),
            )

        app._try_open_citation.assert_called_once_with(
            "The Buena Ventura",
            "243 F. 797",
            "799",
            None,
            client,
            prefetch_pdf=True,
            view_parent=parent,
        )

    def test_opinion_text_click_preserves_highlighted_case_name(self):
        win = object.__new__(_ScholarTextWindow)
        win._link_actions = {"lnk1": ("cite", "243 F. 797@799")}
        win._text = Mock()
        win._text.tag_ranges.return_value = ("1.0", "1.52")
        win._text.get.return_value = (
            "The Buena Ventura, 243 F. 797, 799 (S.D.N.Y. 1916)"
        )
        win._app = Mock()
        win._app._get_scholar.return_value = None
        # The scan-first lookup declines, leaving the text path to follow it.
        win._app.open_cited_case_pdf.return_value = False
        win._following_as_text = False
        win._follow_cite_via_cl = Mock()
        win._status_var = Mock()

        win._follow_link("lnk1")

        win._follow_cite_via_cl.assert_called_once_with(
            "243 F. 797", "799", name="The Buena Ventura",
        )

    def test_direct_lookup_retries_us_reports_alias_after_scholar_miss(self):
        win = object.__new__(CourtListenerGUI)
        win.root = object()
        win._post_root = Mock()
        fetcher = Mock()
        fetcher.fetch_by_citation.side_effect = [None, ("url", "html")]

        self.assertTrue(
            win._try_open_citation("", "8 Wall 168", "", fetcher, None)
        )
        self.assertEqual(
            fetcher.fetch_by_citation.call_args_list,
            [call("8 Wall 168", case_name="", year=""),
             call("75 U.S. 168", case_name="", year="")],
        )

    def test_direct_lookup_retries_federal_cases_alias_after_scholar_miss(self):
        win = object.__new__(CourtListenerGUI)
        win.root = object()
        win._post_root = Mock()
        fetcher = Mock()
        fetcher.fetch_by_citation.side_effect = [None, ("url", "html")]

        self.assertTrue(
            win._try_open_citation("", "18 Fed. Cas. 9", "", fetcher, None)
        )
        self.assertEqual(
            fetcher.fetch_by_citation.call_args_list,
            [call("18 Fed. Cas. 9", case_name="", year=""),
             call("18 F. Cas. 9", case_name="", year="")],
        )


class CopyWithCitationTests(unittest.TestCase):
    class DumpText:
        def __init__(self, before="words ", after=" remain"):
            self.before = before
            self.after = after

        @staticmethod
        def tag_names(_start):
            return ()

        def dump(self, _start, _end, **_kwargs):
            return [
                ("text", self.before, "1.0"),
                ("tagon", "pagenum", "1.6"),
                ("text", "*123", "1.6"),
                ("tagoff", "pagenum", "1.10"),
                ("text", self.after, "1.10"),
            ]

    @staticmethod
    def _window(mode: str):
        win = object.__new__(_ScholarTextWindow)
        win._text = Mock()
        win._text.index.side_effect = ["2.0", "2.20"]
        win._text.tag_ranges.return_value = ()
        # The Copy menu's style, read through copy_mode().  It swallows an
        # AttributeError and falls back to the default, so a window that never
        # sets this silently copies *with* a citation whichever style the test
        # meant to exercise.
        win._copy_mode_var = Mock()
        win._copy_mode_var.get.return_value = mode
        win._omitted_footnote_tags = Mock(return_value=(set(), 0))
        win._bluebook_citation = Mock(return_value=("Case, 1 F.4th 2.", "rtf"))
        win._parts = []
        win._rendered_parts = []
        win._link_actions = {}
        win._mode = "courtlistener"
        win._win = Mock()
        win._status_var = Mock()
        return win

    def test_copy_with_citation_omits_inline_star_pagination(self):
        win = self._window("cite")
        with (
            patch("courtlistener_gui._dump_to_rtf", return_value="body") as dump,
            patch("courtlistener_gui._plain_without_layout_chars",
                  return_value="quotation") as plain,
            patch("courtlistener_gui._rtf_document", return_value="document"),
            patch("courtlistener_gui._copy_rich_clipboard", return_value="rich text"),
        ):
            win._copy_formatted()

        self.assertEqual(dump.call_args.kwargs["omit_tags"], {"pagenum"})
        self.assertEqual(plain.call_args.kwargs["omit_tags"], {"pagenum"})

    def test_copy_without_citation_keeps_inline_star_pagination(self):
        win = self._window("plain")
        with (
            patch("courtlistener_gui._dump_to_rtf", return_value="body") as dump,
            patch("courtlistener_gui._plain_without_layout_chars",
                  return_value="quotation") as plain,
            patch("courtlistener_gui._rtf_document", return_value="document"),
            patch("courtlistener_gui._copy_rich_clipboard", return_value="rich text"),
        ):
            win._copy_formatted()

        self.assertEqual(dump.call_args.kwargs["omit_tags"], set())
        self.assertEqual(plain.call_args.kwargs["omit_tags"], set())

    def test_omitted_pagination_collapses_two_surrounding_spaces(self):
        txt = self.DumpText()

        plain = _plain_without_layout_chars(
            txt, "1.0", "end", omit_tags={"pagenum"},
        )
        rtf = _dump_to_rtf(txt, "1.0", "end", omit_tags={"pagenum"})

        self.assertEqual(plain, "words remain")
        self.assertIn("words remain", rtf)
        self.assertNotIn("words  remain", rtf)

    def test_omitted_pagination_does_not_invent_or_remove_one_sided_space(self):
        cases = (
            ("words ", "remain", "words remain"),
            ("words", " remain", "words remain"),
            ("words", "remain", "wordsremain"),
        )
        for before, after, expected in cases:
            with self.subTest(before=before, after=after):
                txt = self.DumpText(before, after)
                self.assertEqual(
                    _plain_without_layout_chars(
                        txt, "1.0", "end", omit_tags={"pagenum"},
                    ),
                    expected,
                )

    def test_plain_copy_retains_pagination_and_its_original_spacing(self):
        txt = self.DumpText()

        self.assertEqual(
            _plain_without_layout_chars(txt, "1.0", "end"),
            "words *123 remain",
        )


class OpinionDatabaseSpotlightTests(unittest.TestCase):
    class DB:
        def __init__(self, rows):
            self.rows = rows

        def search_names(self, _query, _limit):
            return list(self.rows)

        def find(self, _query):
            return list(self.rows)

        def get_by_scholar_id(self, sid):
            return {"text": f"The parties in saved opinion {sid}."}

    def test_saved_results_use_name_tier_then_court_and_cap_at_three(self):
        rows = [
            {"scholar_id": "1", "name": "ACME CORPORATION v. SMITH",
             "cite": "1 U.S. 1", "cites": ["1 U.S. 1"],
             "court": "ca9", "year": "2020", "url": "u1"},
            {"scholar_id": "2", "name": "Acme Corp. v. Smith",
             "cite": "2 U.S. 2", "cites": ["2 U.S. 2"],
             "court": "scotus", "year": "2019", "url": "u2"},
            {"scholar_id": "3", "name": "Acme Corp. v. Smith",
             "cite": "3 F.4th 3", "cites": ["3 F.4th 3"],
             "court": "ca2", "year": "2021", "url": "u3"},
            {"scholar_id": "4", "name": "Acme Corp. v. Smith",
             "cite": "4 F. Supp. 4", "cites": ["4 F. Supp. 4"],
             "court": "nysd", "year": "2024", "url": "u4"},
            {"scholar_id": "5", "name": "Acme Corp. v. Jones",
             "cite": "5 F.4th 5", "cites": ["5 F.4th 5"],
             "court": "ca1", "year": "2025", "url": "u5"},
        ]

        hits = _opinion_db_spotlight_results(
            self.DB(rows), "Acme Corporation v. Smith", limit=3)

        self.assertEqual([h["scholar_id"] for h in hits], ["2", "3", "1"])
        self.assertTrue(all(h["name"] == "Acme Corp. v. Smith" for h in hits))

    def test_saved_results_honor_spotlight_court_hint(self):
        rows = [
            {"scholar_id": "9", "name": "Acme Corp. v. Smith",
             "cite": "9 F.4th 9", "cites": ["9 F.4th 9"],
             "court": "ca9", "year": "2024", "url": "u9"},
            {"scholar_id": "1", "name": "Acme Corp. v. Smith",
             "cite": "1 U.S. 1", "cites": ["1 U.S. 1"],
             "court": "scotus", "year": "2024", "url": "u1"},
        ]

        hits = _opinion_db_spotlight_results(
            self.DB(rows), "Acme Corp. v. Smith (9th Cir. 2024)", limit=3)

        self.assertEqual([h["scholar_id"] for h in hits], ["9"])

    def test_a_common_party_only_match_is_dropped(self):
        # Only "United States" matches "Foo Industries v. United States" — the
        # distinctive party did not.  In the wide search this is a last-resort
        # filler; in Spotlight, where three rows sit beside live results, it is
        # noise and is dropped.
        rows = [
            {"scholar_id": "1", "name": "United States v. Wong Kim Ark",
             "cite": "1 U.S. 1", "cites": ["1 U.S. 1"],
             "court": "scotus", "year": "1898", "url": "u1"},
            {"scholar_id": "2", "name": "Massachusetts v. EPA",
             "cite": "2 U.S. 2", "cites": ["2 U.S. 2"],
             "court": "scotus", "year": "2007", "url": "u2"},
        ]
        hits = _opinion_db_spotlight_results(
            self.DB(rows), "Foo Industries v. United States", limit=3)
        self.assertEqual(hits, [])

    def test_a_state_only_match_is_dropped(self):
        rows = [
            {"scholar_id": "1", "name": "Arizona v. Gant",
             "cite": "1 U.S. 1", "cites": ["1 U.S. 1"],
             "court": "scotus", "year": "2009", "url": "u1"},
        ]
        hits = _opinion_db_spotlight_results(
            self.DB(rows), "Miranda v. Arizona", limit=3)
        self.assertEqual(hits, [])

    def test_a_distinctive_one_party_match_still_shows(self):
        # "Carpenter" is distinctive, so a one-sided match on it is kept even
        # though the second party did not match.
        rows = [
            {"scholar_id": "1", "name": "Carpenter v. Koch",
             "cite": "1 U.S. 1", "cites": ["1 U.S. 1"],
             "court": "scotus", "year": "2018", "url": "u1"},
        ]
        hits = _opinion_db_spotlight_results(
            self.DB(rows), "Carpenter v. United States", limit=3)
        self.assertEqual([h["scholar_id"] for h in hits], ["1"])

    def test_a_full_two_party_match_with_a_common_party_still_shows(self):
        # "United States" as one side of a genuine two-party match still counts
        # (tier 3) — this filter only drops matches on a common party *alone*.
        rows = [
            {"scholar_id": "1", "name": "United States v. Nixon",
             "cite": "1 U.S. 1", "cites": ["1 U.S. 1"],
             "court": "scotus", "year": "1974", "url": "u1"},
        ]
        hits = _opinion_db_spotlight_results(
            self.DB(rows), "United States v. Nixon", limit=3)
        self.assertEqual([h["scholar_id"] for h in hits], ["1"])


class SpotlightCitationDetectionTests(unittest.TestCase):
    def test_early_federal_reporter_uses_shared_normalization(self):
        hit = _spotlight_case_action("The Nestor, 1 Sumner, 73")

        self.assertIsNotNone(hit)
        self.assertEqual(hit[2], ("cite", "1 Sumn. 73"))

    def test_federal_cases_number_routes_to_special_opener(self):
        hit = _spotlight_case_action(
            "Cole v. The Atlantic, Case No. 2,976"
        )

        self.assertIsNotNone(hit)
        kind, value = hit[2]
        self.assertEqual(kind, "fedcas")
        self.assertEqual(json.loads(value)["no"], "2976")

    def test_federal_cases_reporter_is_a_direct_case_action(self):
        hit = _spotlight_case_action("The Nestor, 18 F. Cas. 9")

        self.assertIsNotNone(hit)
        self.assertEqual(hit[2], ("cite", "18 F. Cas. 9"))

    def test_multiple_case_citations_are_not_opened_arbitrarily(self):
        self.assertIsNone(
            _spotlight_case_action("1 Sumner, 73; 35 Fed. Rep. 665")
        )


class ReporterEquivalenceTests(unittest.TestCase):
    def test_scholar_verification_accepts_equivalent_reporter_names(self):
        nestor = ScholarResult(
            "The Nestor, 18 F. Cas. 9", "https://example.test/nestor",
        )
        washington = ScholarResult(
            "Example v. State", "https://example.test/washington",
            source="81 Wn. 2d 788 (1973)",
        )

        self.assertTrue(bears_citation(nestor, "18 Fed. Cas. 9"))
        self.assertTrue(bears_citation(washington, "81 Wash. 2d 788"))

    def test_local_opinion_database_indexes_broad_reporters_and_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = OpinionDB(root / "opinions.jsonl", root / "opinions.db")
            try:
                for sid, name, cite in (
                    ("1", "The Nestor", "18 Fed. Cas. 9"),
                    ("2", "Example v. State", "81 Wn. 2d 788"),
                    ("3", "The Amos D. Carver", "35 Fed. Rep. 665"),
                    ("4", "Regional Example", "529 N.W.2d 155"),
                ):
                    self.assertTrue(db.add({
                        "scholar_id": sid,
                        "name": name,
                        "cites": [cite],
                        "parties": [],
                    }))

                self.assertEqual(
                    [hit["scholar_id"] for hit in db.find("18 F. Cas. 9")],
                    ["1"],
                )
                self.assertEqual(
                    [hit["scholar_id"]
                     for hit in db.find("81 Wash. 2d 788")],
                    ["2"],
                )
                self.assertEqual(
                    [hit["scholar_id"] for hit in db.find("35 F. 665")],
                    ["3"],
                )
                self.assertEqual(
                    [hit["scholar_id"] for hit in db.find("529 NW 2d 155")],
                    ["4"],
                )
            finally:
                db.close()

    def test_local_database_caption_extraction_uses_broad_detector(self):
        cites = _header_cites([
            Block("center", [Span("18 F. Cas. 9")]),
            Block("center", [Span("35 F. 665")]),
            Block("center", [Span("81 Wash. 2d 788")]),
        ])

        self.assertEqual(
            cites, ["18 F. Cas. 9", "35 F. 665", "81 Wash. 2d 788"],
        )

    def test_old_local_index_rebuild_recovers_cites_from_stored_html(self):
        html = (
            '<div id="gs_opinion"><center>THE NESTOR</center>'
            '<center>18 F. Cas. 9</center><p>The decree is affirmed.</p></div>'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jsonl = root / "opinions.jsonl"
            index = root / "opinions.db"
            db = OpinionDB(jsonl, index)
            self.assertTrue(db.add({
                "scholar_id": "18",
                "name": "The Nestor",
                "cites": [],  # what the pre-v3 narrow extractor persisted
                "parties": ["nestor"],
                "html_gz": _gz_pack(html),
            }))
            # Simulate the stale v2 materialized index.  Reopening must notice
            # the version and rebuild from JSONL/the stored opinion HTML.
            db._db.execute("DELETE FROM citations")
            db._set_meta("schema_version", "2")
            db._db.commit()
            db.close()

            rebuilt = OpinionDB(jsonl, index)
            try:
                self.assertEqual(
                    [hit["scholar_id"]
                     for hit in rebuilt.find("18 Fed. Cas. 9")],
                    ["18"],
                )
            finally:
                rebuilt.close()


    def test_index_points_at_the_jsonl_instead_of_copying_opinions(self):
        # The index keeps only what search reads plus a byte offset into the
        # JSONL; the opinion itself is read back through that pointer.
        html = (
            '<div id="gs_opinion"><center>THE NESTOR</center>'
            '<center>18 F. Cas. 9</center><p>The decree is affirmed.</p></div>'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jsonl = root / "opinions.jsonl"
            db = OpinionDB(jsonl, root / "opinions.db")
            try:
                for sid, name in (("1", "First Case"), ("18", "The Nestor")):
                    self.assertTrue(db.add({
                        "scholar_id": sid,
                        "name": name,
                        "cites": [],
                        "parties": [name.lower()],
                        "html_gz": _gz_pack(html),
                    }))

                columns = {
                    r[1] for r in
                    db._db.execute("PRAGMA table_info(opinions)").fetchall()
                }
                self.assertIn("line_offset", columns)
                self.assertNotIn("html", columns)   # no second copy of the corpus
                self.assertNotIn("text", columns)

                # The stored offset addresses that record's line in the JSONL.
                row = db._db.execute(
                    "SELECT line_offset, line_length FROM opinions "
                    "WHERE scholar_id='18'"
                ).fetchone()
                raw = jsonl.read_bytes()[
                    row["line_offset"]:row["line_offset"] + row["line_length"]
                ]
                self.assertEqual(
                    json.loads(raw.decode("utf-8"))["scholar_id"], "18",
                )

                # Reading an opinion still yields its HTML and plain text.
                rec = db.get_by_scholar_id("18")
                self.assertIn("The decree is affirmed.", rec["html"])
                self.assertIn("The decree is affirmed.", rec["text"])
                self.assertEqual(rec["name"], "The Nestor")
                self.assertIsNone(db.get_by_scholar_id("no-such-id"))
            finally:
                db.close()

    def test_index_from_an_older_schema_is_rebuilt_not_reused(self):
        # CREATE TABLE IF NOT EXISTS leaves an existing table's columns alone,
        # so an index written by an earlier release has to be dropped: reusing
        # it rejected every insert and left the database reporting 0 opinions.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jsonl = root / "opinions.jsonl"
            index = root / "opinions.db"
            jsonl.write_text(json.dumps({
                "scholar_id": "18",
                "name": "The Nestor",
                "cites": ["18 F. Cas. 9"],
                "parties": ["nestor"],
                "html_gz": _gz_pack("<p>The decree is affirmed.</p>"),
            }) + "\n", encoding="utf-8")

            # An index in the previous layout: the old html/text columns, no
            # line pointer, stamped with the schema before this one.
            legacy = sqlite3.connect(index)
            legacy.executescript(
                "CREATE TABLE opinions (scholar_id TEXT PRIMARY KEY, url TEXT,"
                " name TEXT, court TEXT, year TEXT, date_filed TEXT,"
                " html TEXT, text TEXT, cites_json TEXT, parties_json TEXT,"
                " added_at REAL, source TEXT);"
                "CREATE TABLE citations (scholar_id TEXT, vol INTEGER,"
                " reporter TEXT, page INTEGER, raw TEXT);"
                "CREATE TABLE parties (scholar_id TEXT, token TEXT);"
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
                "INSERT INTO meta VALUES ('schema_version', '3');"
            )
            legacy.commit()
            legacy.close()

            db = OpinionDB(jsonl, index)
            try:
                self.assertEqual(db.count(), 1)          # not 0
                columns = {
                    r[1] for r in
                    db._db.execute("PRAGMA table_info(opinions)").fetchall()
                }
                self.assertIn("line_offset", columns)
                self.assertNotIn("html", columns)
                self.assertIn("The decree is affirmed.",
                              db.get_by_scholar_id("18")["html"])
                # Storing a newly fetched opinion works after the upgrade.
                self.assertTrue(db.add({
                    "scholar_id": "19", "name": "Later Case",
                    "cites": ["1 U.S. 1"], "parties": ["later"],
                    "html_gz": _gz_pack("<p>Body.</p>"),
                }))
                self.assertEqual(
                    [h["scholar_id"] for h in db.find("1 U.S. 1")], ["19"],
                )
            finally:
                db.close()

    def test_stale_jsonl_pointer_is_recovered_by_scanning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = OpinionDB(root / "opinions.jsonl", root / "opinions.db")
            try:
                db.add({
                    "scholar_id": "7",
                    "name": "Example",
                    "cites": [],
                    "parties": ["example"],
                    "html_gz": _gz_pack("<p>Body text.</p>"),
                })
                # A pointer left behind by an out-of-band rewrite must not
                # return the wrong opinion — it is re-found and healed.
                db._db.execute("UPDATE opinions SET line_offset=99999")
                db._db.commit()

                rec = db.get_by_scholar_id("7")
                self.assertIsNotNone(rec)
                self.assertIn("Body text.", rec["html"])
                healed = db._db.execute(
                    "SELECT line_offset FROM opinions WHERE scholar_id='7'"
                ).fetchone()
                self.assertEqual(healed["line_offset"], 0)
            finally:
                db.close()

    def test_caption_prefix_yields_the_header_citations(self):
        # Indexing expands only the caption, so a citation printed above the
        # opinion is still found without parsing the body.
        body = "<p>%s</p>" % ("padding text " * 4000)
        html = (
            '<div id="gs_opinion"><center>THE NESTOR</center>'
            '<center>18 F. Cas. 9</center>' + body + '</div>'
        )
        packed = _gz_pack(html)
        head = _gz_unpack_prefix(packed)

        self.assertLess(len(head), len(html))  # body never inflated
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = OpinionDB(root / "opinions.jsonl", root / "opinions.db")
            try:
                db.add({
                    "scholar_id": "18", "name": "The Nestor",
                    "cites": [], "parties": ["nestor"], "html_gz": packed,
                })
                self.assertEqual(
                    [h["scholar_id"] for h in db.find("18 Fed. Cas. 9")], ["18"],
                )
            finally:
                db.close()


class UsCodeNavigationTests(unittest.TestCase):
    def test_current_olrc_section_heads_supply_neighbor_order(self):
        # Current OLRC container pages no longer include the old analysis
        # table and HTML-encode the section sign in each fallback heading.
        page = """
        <h3 class="section-head">&sect;1981. Equal rights</h3>
        <h3 class="section-head">&sect;1981a. Damages</h3>
        <h3 class="section-head">&#167;1982. Property rights</h3>
        <h3 class="section-head">&#xA7;1983. Civil action</h3>
        """

        self.assertEqual(
            us_code._sections_from_analysis(page),
            ["1981", "1981a", "1982", "1983"],
        )

    def test_neighbors_use_section_head_fallback_order(self):
        doc = us_code.UscSection(
            title="42", section="1982", url="url",
            container="title42-chapter21-subchapter1",
        )

        with patch(
            "us_code._container_sections",
            return_value=["1981", "1981a", "1982", "1983"],
        ):
            self.assertEqual(
                doc.neighbors(),
                (("42", "1981a"), ("42", "1983")),
            )


class CaseLawSharedPageTests(unittest.TestCase):
    def test_federal_reporter_aliases_share_cap_slugs(self):
        self.assertEqual(
            _static_case_law_url("18 F. Cas. 9"),
            "https://static.case.law/f-cas/18/case-pdfs/0009-01.pdf",
        )
        self.assertEqual(
            _static_case_law_url("18 Fed. Cas. 9"),
            "https://static.case.law/f-cas/18/case-pdfs/0009-01.pdf",
        )
        self.assertEqual(
            _static_case_law_url("35 Fed. Rep. 665"),
            "https://static.case.law/f/35/case-pdfs/0665-01.pdf",
        )
        self.assertEqual(
            _static_case_law_url("514 Fed. Appx. 210"),
            "https://static.case.law/f-appx/514/case-pdfs/0210-01.pdf",
        )

    def test_initials_printed_apart_name_cap_s_folder(self):
        # The U.S. Reports print "142 N. E. 583"; CAP's folder is "ne", and
        # "n-e" was a 404 whenever CourtListener had no parallel cite to try.
        for cite, folder in (("142 N. E. 583", "ne/142"),
                             ("678 P. 2d 720", "p2d/678"),
                             ("559 S. W. 2d 704", "sw2d/559"),
                             ("500 U. S. 565", "us/500"),
                             ("216 Cal. App. 3d 586", "cal-app-3d/216"),
                             ("114 L. Ed. 2d 619", "l-ed-2d/114")):
            with self.subTest(cite=cite):
                self.assertIn(f"static.case.law/{folder}/case-pdfs/",
                              _static_case_law_url(cite))

    def test_washington_text_cite_uses_cap_washington_reporter_slug(self):
        expected = (
            "https://static.case.law/wash-2d/81/"
            "case-pdfs/0788-01.pdf"
        )
        self.assertEqual(_static_case_law_url("81 Wn. 2d 788"), expected)
        self.assertEqual(_static_case_law_url("81 Wn 2d 788"), expected)
        self.assertEqual(_static_case_law_url("81 Wash. 2d 788"), expected)
        self.assertEqual(
            _static_case_law_url("12 Wn. App. 45"),
            "https://static.case.law/wash-app/12/case-pdfs/0045-01.pdf",
        )
        self.assertEqual(
            _static_case_law_url("10 Wn. 25"),
            "https://static.case.law/wash/10/case-pdfs/0025-01.pdf",
        )

        session = Mock()
        session.head.side_effect = lambda url, **_kwargs: SimpleNamespace(
            status_code=200 if url == expected else 404,
        )
        with patch("courtlistener_gui._anon_session", session):
            choices = _case_law_pdf_choices_for_cites([
                "81 Wn. 2d 788", "81 Wash. 2d 788",
            ])

        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0].url, expected)

    def test_discovers_sequential_pdfs_and_reads_every_json_name(self):
        base = "https://static.case.law/f2d/100/case-pdfs/0123-01.pdf"
        session = Mock()

        def head(url, **_kwargs):
            return SimpleNamespace(
                status_code=200 if url.endswith(("-02.pdf", "-03.pdf")) else 404
            )

        def get(url, **_kwargs):
            suffix = url.rsplit("-", 1)[-1].split(".", 1)[0]
            names = {
                "01": "Alpha Corp. v. One",
                "02": "Beta Corp. v. Two",
                "03": "Gamma Corp. v. Three",
            }
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"name_abbreviation": names[suffix]},
            )

        session.head.side_effect = head
        session.get.side_effect = get
        with patch("courtlistener_gui._anon_session", session):
            opinions = _case_law_page_opinions(base)

        self.assertEqual([o.name for o in opinions], [
            "Alpha Corp. v. One", "Beta Corp. v. Two", "Gamma Corp. v. Three",
        ])
        self.assertEqual(session.head.call_args_list[-1].args[0],
                         base.replace("-01.pdf", "-04.pdf"))
        self.assertEqual(session.get.call_count, 3)

    def test_source_case_name_selects_matching_sibling(self):
        opinions = [
            _CaseLawPageOpinion("first.pdf", "first.json", "Alpha v. One"),
            _CaseLawPageOpinion("second.pdf", "second.json", "Beta v. Two"),
        ]
        chosen = _match_page_opinion(opinions, "Beta v. Two")
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.url, "second.pdf")


# static.case.law/johns/10/CasesMetadata.json, around the case cited in
# California v. Acevedo, 500 U.S. 565, 583 (Scalia, J.).  CAP's page labels
# run four ahead of the citations in this reprinted volume.
_JOHNS_10 = [
    {"name_abbreviation": "Sackrider v. M'Donald", "file_name": "0257-01",
     "citations": [{"cite": "10 Johns. 253"}]},
    {"name_abbreviation": "Spencer v. Southwick", "file_name": "0263-01",
     "citations": [{"cite": "10 Johns. 259"}]},
    {"name_abbreviation": "Bell v. Clapp", "file_name": "0267-01",
     "citations": [{"cite": "10 Johns. 263"}]},
    {"name_abbreviation": "Jones v. Gardner", "file_name": "0270-01",
     "citations": [{"cite": "10 Johns. 266"}]},
]


class CaseLawPageLabelTests(unittest.TestCase):
    """A CAP file is found by the citation it carries.  Page arithmetic
    alone opened Spencer v. Southwick for "Bell v. Clapp, 10 Johns. 263"."""

    PDF = "https://static.case.law/johns/10/case-pdfs/0263-01.pdf"
    BELL = "https://static.case.law/johns/10/case-pdfs/0267-01.pdf"

    def setUp(self):
        _CASE_LAW_VOLUMES.clear()
        self.addCleanup(_CASE_LAW_VOLUMES.clear)

    @staticmethod
    def _session(status=200, listing=_JOHNS_10):
        session = Mock()
        session.get.side_effect = lambda url, **_kwargs: SimpleNamespace(
            status_code=(status if url.endswith("/CasesMetadata.json")
                         else 404),
            json=lambda: listing,
        )
        return session

    def test_the_citation_is_found_in_its_own_file(self):
        with patch("courtlistener_gui._anon_session", self._session()):
            self.assertEqual(
                _case_law_listed_url("10 Johns. 263", self.PDF), self.BELL)
            self.assertEqual(
                _case_law_listed_url(
                    "10 Johns. 263",
                    "https://static.case.law/johns/10/cases/0263-01.json"),
                "https://static.case.law/johns/10/cases/0267-01.json")

    def test_a_citation_listed_on_its_own_page_is_left_alone(self):
        pdf = "https://static.case.law/cal-app-3d/216/case-pdfs/0586-01.pdf"
        listing = [{"file_name": "0586-01",
                    "citations": [{"cite": "216 Cal. App. 3d 586"}]}]
        with patch("courtlistener_gui._anon_session",
                   self._session(listing=listing)):
            self.assertEqual(
                _case_law_listed_url("216 Cal. App. 3d 586", pdf), pdf)

    def test_an_unlisted_cite_or_unreadable_volume_goes_by_the_page(self):
        other = "https://static.case.law/johns/10/case-pdfs/0999-01.pdf"
        with patch("courtlistener_gui._anon_session", self._session()):
            self.assertEqual(_case_law_listed_url("10 Johns. 999", other),
                             other)
        _CASE_LAW_VOLUMES.clear()
        with patch("courtlistener_gui._anon_session",
                   self._session(status=503)):
            self.assertEqual(
                _case_law_listed_url("10 Johns. 263", self.PDF), self.PDF)
        # A server error is no answer about the volume: ask again next time.
        self.assertEqual(_CASE_LAW_VOLUMES, {})

    def test_the_scan_lookup_opens_the_case_cited(self):
        session = self._session()
        session.head.side_effect = lambda url, **_kwargs: SimpleNamespace(
            status_code=200 if url in (self.PDF, self.BELL) else 404)
        with patch("courtlistener_gui._anon_session", session):
            choices = _case_law_pdf_choices_for_cites(["10 Johns. 263"])
        self.assertEqual([c.url for c in choices], [self.BELL])

    def test_a_page_of_orders_is_told_from_one_two_opinions_begin_on(self):
        from courtlistener_gui import _case_law_page_cases, _orders_only

        def listing(last_pages):
            return [{"name_abbreviation": f"Case {i}",
                     "file_name": f"0807-{i:02d}", "first_page": "807",
                     "last_page": last, "citations": [{"cite": "498 U.S. 807"}]}
                    for i, last in enumerate(last_pages, 1)]

        with patch("courtlistener_gui._anon_session",
                   self._session(listing=listing(["807", "807", "808"]))):
            orders = _case_law_page_cases("498 U.S. 807")
        self.assertEqual([c.pages for c in orders], [1, 1, 2])
        self.assertEqual(orders[2].url,
                         "https://static.case.law/us/498/case-pdfs/0807-03.pdf")
        self.assertTrue(_orders_only(orders))
        _CASE_LAW_VOLUMES.clear()
        with patch("courtlistener_gui._anon_session",
                   self._session(listing=listing(["807", "846"]))):
            self.assertFalse(_orders_only(_case_law_page_cases("498 U.S. 807")))
        self.assertFalse(_orders_only(orders[:1]))   # one case is no page of them

    def test_page_mates_no_name_picks_are_offered_by_name(self):
        # The PDF menu's labels once carried a mis-encoded dash (UTF-8 read
        # as cp1252) where the em dash belongs.
        first = "https://static.case.law/f/243/case-pdfs/0797-01.pdf"
        second = "https://static.case.law/f/243/case-pdfs/0797-02.pdf"
        siblings = [_CaseLawPageOpinion(first, "a.json", "In re Cooper"),
                    _CaseLawPageOpinion(second, "b.json", "")]
        session = Mock()
        session.head.return_value = SimpleNamespace(status_code=200)
        with (
            patch("courtlistener_gui._anon_session", session),
            patch("courtlistener_gui._case_law_page_opinions",
                  return_value=siblings),
            patch("courtlistener_gui._case_law_listed_url",
                  side_effect=lambda cite, url: url),
        ):
            choices = _case_law_pdf_choices_for_cites(
                ["243 F. 797"], expected_name="Gamma v. Three")
        self.assertEqual([c.label for c in choices],
                         ["243 F. 797 — In re Cooper", "243 F. 797 — Opinion 2"])

    def test_a_us_page_of_orders_is_not_answered_by_its_first_order(self):
        # A bare "498 U.S. 807" typed into Quick Look Up opened Hoffman v.
        # Native Village of Noatak, the first of the page's eleven grants.
        first = "https://static.case.law/us/498/case-pdfs/0807-01.pdf"
        acevedo = "https://static.case.law/us/498/case-pdfs/0807-08.pdf"
        siblings = [
            _CaseLawPageOpinion(
                first, "a.json", "Hoffman v. Native Village of Noatak"),
            _CaseLawPageOpinion(acevedo, "b.json", "California v. Acevedo"),
        ]
        session = Mock()
        session.head.return_value = SimpleNamespace(status_code=200)
        with (
            patch("courtlistener_gui._anon_session", session),
            patch("courtlistener_gui._case_law_page_opinions",
                  return_value=siblings),
            patch("courtlistener_gui._case_law_listed_url",
                  side_effect=lambda cite, url: url),
        ):
            self.assertEqual(
                _case_law_pdf_choices_for_cites(["498 U.S. 807"]), [])
            named = _case_law_pdf_choices_for_cites(
                ["498 U.S. 807"], expected_name="California v. Acevedo")
        self.assertEqual([c.url for c in named], [acevedo])

    def test_the_scan_is_named_for_its_citation_not_its_file(self):
        # Printed and saved, the file kept as "0267-01" is 10 Johns. 263.
        from courtlistener_gui import _case_law_print_citation
        meta = {"name_abbreviation": "Bell v. Clapp",
                "decision_date": "1813-08",
                "court": {"name_abbreviation": "N.Y. Sup. Ct.",
                          "slug": "ny-sup-ct"},
                "citations": [{"cite": "10 Johns. 263"}]}
        with patch("courtlistener_gui._case_law_case_json",
                   return_value=meta):
            self.assertEqual(
                _case_law_print_citation(b"", self.BELL),
                "Bell v. Clapp, 10 Johns. 263 (N.Y. Sup. Ct. 1813)")


class CaseLawPdfTextTests(unittest.TestCase):
    @staticmethod
    def _data():
        return {
            "name_abbreviation": "Smith v. Jones",
            "citations": [
                {"type": "vendor", "cite": "123 F. App'x 456"},
                {"type": "official", "cite": "77 Example 12"},
            ],
            "court": {
                "name_abbreviation": "2d Cir.",
                "slug": "ca2",
            },
            "decision_date": "2004-05-06",
            "casebody": {
                "data": {
                    "head_matter": "Smith v. Jones\nNo. 03-1000",
                    "opinions": [
                        {
                            "author": "Per Curiam.",
                            "text": "See Roe v. Wade, 410 U.S. 113.",
                        },
                        {"text": "The judgment is affirmed."},
                    ],
                },
            },
        }

    @staticmethod
    def _record():
        return _CaseLawTextRecord(
            "CAP text", "Pearson v. Dodd, 410 F.2d 701 (D.C. Cir. 1969)",
            {"caseName": "Pearson v. Dodd", "citation": ["410 F.2d 701"]},
            "https://static.case.law/f2d/410/cases/0701-01.json",
            ["part"], ["block"],
        )

    def test_cap_json_record_keeps_exact_text_and_display_metadata(self):
        record = _case_law_text_record(
            self._data(), "123 F. App'x 456",
            "https://static.case.law/f-appx/123/cases/0456-02.json",
        )

        self.assertIsNotNone(record)
        self.assertIn("Per Curiam.", record.text)
        self.assertIn("410 U.S. 113", record.text)
        self.assertEqual(
            record.citation,
            "Smith v. Jones, 77 Example 12 (2d Cir. 2004)",
        )
        self.assertEqual(record.item["court_id"], "ca2")
        self.assertEqual(record.item["citation"],
                         ["123 F. App'x 456", "77 Example 12"])

    def test_exact_numbered_pdf_uses_its_matching_json(self):
        response = SimpleNamespace(
            status_code=200, json=lambda: self._data(), text="",
        )
        session = Mock()
        session.get.return_value = response
        pdf = "https://static.case.law/f-appx/123/case-pdfs/0456-02.pdf"

        with patch("courtlistener_gui._anon_session", session):
            _case_law_case_html.cache_clear()
            record = _case_law_text_for_pdf_url(pdf)

        self.assertIsNotNone(record)
        # The JSON for this exact numbered case, and the formatted HTML CAP
        # publishes beside it under the same file name.
        self.assertEqual(
            [c.args[0] for c in session.get.call_args_list],
            ["https://static.case.law/f-appx/123/cases/0456-02.json",
             "https://static.case.law/f-appx/123/html/0456-02.html"],
        )

    def test_the_html_beside_a_case_is_found_by_its_json_url(self):
        self.assertEqual(
            _case_law_html_url(
                "https://static.case.law/f2d/410/cases/0701-01.json"),
            "https://static.case.law/f2d/410/html/0701-01.html",
        )
        # Anything that is not a CAP per-case JSON has no HTML twin.
        self.assertEqual(_case_law_html_url("https://example.test/a.json"), "")
        self.assertEqual(_case_law_html_url(""), "")

    def test_cap_html_supplies_the_parts_the_json_cannot(self):
        html = (
            '<section class="casebody">'
            '<section class="head-matter">'
            '<h4 class="parties">SMITH v. JONES</h4></section>'
            '<article class="opinion" data-type="majority">'
            '<p class="author">Per Curiam.</p>'
            '<p><a class="page-label" data-label="457">*457</a>Affirmed.</p>'
            '</article></section>'
        )
        record = _case_law_text_record(
            self._data(), "123 F. App\'x 456",
            "https://static.case.law/f-appx/123/cases/0456-01.json", html,
        )

        self.assertEqual([p.kind for p in record.parts],
                         ["header", "majority"])
        self.assertEqual(
            [s.text for p in record.parts for b in p.blocks
             for s in b.spans if s.pagenum],
            ["*457"],
        )
        # The flat CAP text is still the record's text: the brief compiler and
        # the short-cite index both read the case as one string.
        self.assertIn("410 U.S. 113", record.text)

    def test_without_the_html_the_json_paragraphs_still_render(self):
        record = _case_law_text_record(self._data(), "123 F. App\'x 456")
        self.assertEqual([p.kind for p in record.parts],
                         ["header", "majority", "majority"])
        self.assertEqual(record.parts[0].blocks[0].kind, "center")

    def test_a_decision_after_caps_scans_is_not_looked_up_at_all(self):
        self.assertTrue(_case_law_may_hold("1973-01-22"))
        self.assertTrue(_case_law_may_hold(""))          # unknown — go and see
        self.assertTrue(_case_law_may_hold("nonsense"))
        self.assertFalse(_case_law_may_hold("2024-03-01"))
        with patch("courtlistener_gui._case_law_text_for_cite") as lookup:
            self.assertIsNone(_case_law_text_source(
                ["601 U.S. 416"], "Smith v. Jones", "2024-03-01"))
        lookup.assert_not_called()

    def test_a_scan_is_answered_from_its_own_file(self):
        # The pages on screen and the text behind them are one document.
        with (
            patch("courtlistener_gui._case_law_text_for_pdf_url",
                  return_value=self._record()) as exact,
            patch("courtlistener_gui._case_law_text_source") as by_cite,
        ):
            source = _case_law_text_for_scan(
                "https://static.case.law/f2d/410/case-pdfs/0701-01.pdf",
                ["93 S. Ct. 705", "410 F.2d 701"], "Pearson v. Dodd")

        self.assertEqual(source.kind, "case_law")
        exact.assert_called_once_with(
            "https://static.case.law/f2d/410/case-pdfs/0701-01.pdf")
        by_cite.assert_not_called()

    def test_another_scan_is_answered_only_by_the_reporter_it_prints(self):
        # A U.S. Reports scan: CAP's copy of that volume, and no other
        # printing of the same case, whatever else the result lists.
        with (
            patch("courtlistener_gui._case_law_text_for_pdf_url",
                  return_value=None),
            patch("courtlistener_gui._case_law_text_source") as by_cite,
        ):
            _case_law_text_for_scan(
                "https://tile.loc.gov/.../usrep410113.pdf",
                ["93 S. Ct. 705", "410 U.S. 113"], "Roe v. Wade", "1973-01-22")

        by_cite.assert_called_once_with(
            ["410 U.S. 113"], "Roe v. Wade", "1973-01-22",
            prefer="410 U.S. 113")

    def test_a_scan_naming_no_reporter_is_left_to_courtlistener(self):
        # A slip opinion or CourtListener's own stored copy: CAP's text would
        # be paginated to a reporter these pages are not.
        with patch("courtlistener_gui._case_law_text_for_pdf_url",
                   return_value=None):
            self.assertIsNone(_case_law_text_for_scan(
                "https://storage.courtlistener.com/pdf/2020/x.pdf",
                ["410 U.S. 113"], "Roe v. Wade"))

    def test_with_no_scan_the_ordinary_preference_applies(self):
        with (
            patch("courtlistener_gui._case_law_text_for_pdf_url",
                  return_value=None),
            patch("courtlistener_gui._case_law_text_source") as by_cite,
        ):
            _case_law_text_for_scan("", ["410 U.S. 113"], "Roe v. Wade", "d")
        by_cite.assert_called_once_with(["410 U.S. 113"], "Roe v. Wade", "d")

    def test_the_reporter_on_screen_leads_whatever_series_it_is(self):
        tried = []

        def lookup(cite, name=""):
            tried.append(cite)
            return None

        with patch("courtlistener_gui._case_law_text_for_cite", lookup):
            _case_law_text_source(
                ["410 U.S. 113", "93 S. Ct. 705"], "Roe v. Wade",
                prefer="93 S. Ct. 705")

        # The vendor series would ordinarily come last; the scan on screen
        # settles it instead.
        self.assertEqual(tried, ["93 S. Ct. 705", "410 U.S. 113"])

    def test_the_scans_reporter_is_read_off_its_url(self):
        self.assertEqual(
            _scan_printed_cite(
                "https://static.case.law/p2d/506/case-pdfs/0020-01.pdf",
                ["81 Wash. 2d 886"]),
            "506 P.2d 20")
        self.assertEqual(
            _scan_printed_cite("https://tile.loc.gov/x/usrep410113.pdf",
                               ["93 S. Ct. 705", "410 U.S. 113"]),
            "410 U.S. 113")
        self.assertEqual(
            _scan_printed_cite("https://storage.courtlistener.com/a.pdf",
                               ["410 U.S. 113"]),
            "")

    def test_the_file_a_text_came_from_names_the_scan_of_those_pages(self):
        self.assertEqual(
            _case_law_pdf_for_json_url(
                "https://static.case.law/f2d/410/cases/0701-01.json"),
            "https://static.case.law/f2d/410/case-pdfs/0701-01.pdf")
        self.assertEqual(_case_law_pdf_for_json_url("https://x.test/a.json"),
                         "")

    def test_that_reporter_then_leads_the_windows_citations(self):
        self.assertEqual(
            _cites_led_by(["93 S. Ct. 705", "410 U.S. 113"], "410 U.S. 113"),
            ["410 U.S. 113", "93 S. Ct. 705"])
        # A reporter the result never listed is added at the front.
        self.assertEqual(_cites_led_by(["81 Wash. 2d 886"], "506 P.2d 20"),
                         ["506 P.2d 20", "81 Wash. 2d 886"])
        # Nothing to lead with leaves the order alone.
        self.assertEqual(_cites_led_by(["410 U.S. 113"], ""), ["410 U.S. 113"])

    def test_the_source_leads_its_own_citations_by_the_file_it_read(self):
        record = _CaseLawTextRecord(
            "CAP text", "Pearson v. Dodd, 410 F.2d 701 (D.C. Cir. 1969)",
            {"caseName": "Pearson v. Dodd",
             "citation": ["93 S. Ct. 705", "410 F.2d 701"]},
            "https://static.case.law/f2d/410/cases/0701-01.json",
            ["part"], ["block"],
        )
        with patch("courtlistener_gui._case_law_text_for_cite",
                   return_value=record):
            source = _case_law_text_source(["410 F.2d 701"], "Pearson v. Dodd")
        self.assertEqual(source.item["citation"],
                         ["410 F.2d 701", "93 S. Ct. 705"])

    def test_official_reporters_are_tried_before_the_vendor_series(self):
        tried = []

        def lookup(cite, name=""):
            tried.append(cite)
            return None

        with patch("courtlistener_gui._case_law_text_for_cite", lookup):
            _case_law_text_source(
                ["93 S. Ct. 705", "410 U.S. 113", "35 L. Ed. 2d 147",
                 "1973 WL 4187", "<i>410 U. S. 113</i>"],
                "Roe v. Wade",
            )

        # The official cite first; the vendor series after it; the Westlaw
        # cite and the duplicate spelling of the official one not at all.
        self.assertEqual(tried,
                         ["410 U.S. 113", "93 S. Ct. 705", "35 L. Ed. 2d 147"])

    def test_the_source_carries_the_caps_own_parts_and_url(self):
        record = _CaseLawTextRecord(
            "CAP text", "Roe v. Wade, 410 U.S. 113 (U.S. 1973)",
            {"caseName": "Roe v. Wade", "citation": ["410 U.S. 113"]},
            "https://static.case.law/us/410/cases/0113-01.json",
            ["part"], ["block"],
        )
        with patch("courtlistener_gui._case_law_text_for_cite",
                   return_value=record):
            source = _case_law_text_source(["410 U.S. 113"], "Roe v. Wade")

        self.assertEqual(source.kind, "case_law")
        self.assertEqual(source.source_label, "static.case.law")
        self.assertEqual(source.button_label, "Text")
        self.assertEqual(source.parts, ["part"])
        self.assertEqual(source.blocks, ["block"])
        self.assertEqual(
            source.source_url,
            "https://static.case.law/us/410/cases/0113-01.json")

    def test_a_shared_reporter_page_is_settled_by_the_case_name(self):
        siblings = [
            _CaseLawPageOpinion(
                "https://static.case.law/f2d/410/case-pdfs/0701-01.pdf",
                "https://static.case.law/f2d/410/cases/0701-01.json",
                "Alpha v. One"),
            _CaseLawPageOpinion(
                "https://static.case.law/f2d/410/case-pdfs/0701-02.pdf",
                "https://static.case.law/f2d/410/cases/0701-02.json",
                "Beta v. Two"),
        ]
        with (
            patch("courtlistener_gui._case_law_metadata",
                  return_value=self._data()),
            patch("courtlistener_gui._case_law_page_opinions",
                  return_value=siblings),
            patch("courtlistener_gui._case_law_text_for_json_url",
                  return_value="the second opinion") as read,
        ):
            got = _case_law_text_for_cite("410 F.2d 701", "Beta v. Two")

        self.assertEqual(got, "the second opinion")
        read.assert_called_once_with(
            "https://static.case.law/f2d/410/cases/0701-02.json",
            "410 F.2d 701")

    def test_a_shared_page_no_name_settles_is_left_alone(self):
        siblings = [
            _CaseLawPageOpinion("a.pdf", "a.json", "Alpha v. One"),
            _CaseLawPageOpinion("b.pdf", "b.json", "Beta v. Two"),
        ]
        with (
            patch("courtlistener_gui._case_law_metadata",
                  return_value=self._data()),
            patch("courtlistener_gui._case_law_page_opinions",
                  return_value=siblings),
        ):
            self.assertIsNone(
                _case_law_text_for_cite("410 F.2d 701", "Gamma v. Three"))

    def test_pdf_text_source_prefers_the_scans_own_cap_file(self):
        # The scan on screen was made from this file, so its text is the text
        # on those very pages — CourtListener is not even asked.
        record = _CaseLawTextRecord(
            "CAP text with 410 U.S. 113",
            "Smith v. Jones, 123 F. App'x 456 (2d Cir. 2004)",
            {
                "caseName": "Smith v. Jones",
                "citation": ["123 F. App'x 456"],
                "court": "2d Cir.",
                "court_id": "ca2",
                "dateFiled": "2004-05-06",
            },
            "https://static.case.law/f-appx/123/cases/0456-01.json",
            ["part"], ["block"],
        )
        with (
            patch("courtlistener_gui._case_law_text_for_pdf_url",
                  return_value=record),
            patch("courtlistener_gui._cl_item_for_citation") as find,
        ):
            source = _case_pdf_text_source(
                "https://static.case.law/f-appx/123/case-pdfs/0456-01.pdf",
                "123 F. App'x 456", client=object(),
            )

        self.assertEqual(source.kind, "case_law")
        # The button says just "Text"; source_label still names the origin.
        self.assertEqual(source.button_label, "Text")
        self.assertEqual(source.source_label, "static.case.law")
        self.assertEqual(source.parts, ["part"])
        self.assertIn("410 U.S. 113", source.text)
        find.assert_not_called()

    def test_pdf_text_source_falls_back_to_courtlistener(self):
        # A CAP file that yielded no renderable parts: CourtListener answers,
        # with CAP's better reporter metadata behind it for the title.
        record = _CaseLawTextRecord(
            "CAP text", "Smith v. Jones, 123 F. App'x 456 (2d Cir. 2004)",
            {
                "caseName": "Smith v. Jones",
                "citation": ["123 F. App'x 456"],
                "court": "2d Cir.",
                "court_id": "ca2",
                "dateFiled": "2004-05-06",
            },
            "https://static.case.law/f-appx/123/cases/0456-01.json",
        )
        target = {
            "cluster_id": 99,
            "absolute_url": "/opinion/99/smith-v-jones/",
        }
        with (
            patch("courtlistener_gui._case_law_text_for_pdf_url",
                  return_value=record),
            patch("courtlistener_gui._cl_item_for_citation",
                  return_value=target) as find,
            patch("courtlistener_gui._assemble_case_parts",
                  return_value=(["part"], ["block"], "CL opinion", {})),
        ):
            source = _case_pdf_text_source(
                "https://static.case.law/f-appx/123/case-pdfs/0456-01.pdf",
                "123 F. App'x 456", client=object(),
            )

        self.assertEqual(source.kind, "courtlistener")
        self.assertEqual(source.text, "CL opinion")
        self.assertEqual(
            source.source_url,
            "https://www.courtlistener.com/opinion/99/smith-v-jones/",
        )
        find.assert_called_once_with(
            ANY, "123 F. App'x 456", name="Smith v. Jones",
        )

    def test_pdf_text_source_keeps_the_cap_text_when_nothing_else_answers(self):
        record = _CaseLawTextRecord(
            "CAP fallback with 410 U.S. 113", "Smith v. Jones",
            {"caseName": "Smith v. Jones",
             "citation": ["123 F. App'x 456"]},
            "https://static.case.law/f-appx/123/cases/0456-01.json",
        )
        with (
            patch("courtlistener_gui._case_law_text_for_pdf_url",
                  return_value=record),
            patch("courtlistener_gui._cl_item_for_citation",
                  return_value=None),
        ):
            source = _case_pdf_text_source(
                "https://static.case.law/f-appx/123/case-pdfs/0456-01.pdf",
                "123 F. App'x 456", client=object(),
            )

        self.assertEqual(source.kind, "case_law")
        self.assertEqual(source.source_label, "static.case.law")
        self.assertIn("410 U.S. 113", source.text)


class CaseWindowTests(unittest.TestCase):
    def test_main_window_bookmarks_menu_lists_saved_documents(self):
        # The root window is not a document view, so its Bookmarks cascade
        # lists the saved documents with no "Bookmark This …" toggle first.
        class Menu:
            def __init__(self):
                self.items = []

            def delete(self, *_args):
                self.items.clear()

            def add_separator(self):
                self.items.append("--")

            def add_command(self, label="", command=None, state=None):
                self.items.append(label)

        app = object.__new__(CourtListenerGUI)
        app.root = object()
        app._open_case_views = {}
        app._bookmarks = [
            {"key": "scholar:1", "label": "Roe v. Wade", "last_accessed": 200,
             "payload": {"type": "scholar", "url": "u", "html": "<p>x</p>",
                         "item": {}}},
            {"key": "statute:2", "label": "18 U.S.C. § 922",
             "last_accessed": 300,
             "payload": {"type": "statute",
                         "doc": {"url": "u2", "label": "18 USC 922",
                                 "paras": [], "kind": "usc"}}},
        ]

        menu = Menu()
        app.populate_bookmarks_menu(menu, app.root)
        # Most recently accessed first, and nothing to bookmark from here.
        self.assertEqual(menu.items, ["18 U.S.C. § 922", "Roe v. Wade"])

        app._bookmarks = []
        app.populate_bookmarks_menu(menu, app.root)
        self.assertEqual(menu.items, ["No bookmarks yet"])

    def test_citation_result_uses_launching_view_parent(self):
        app = object.__new__(CourtListenerGUI)
        app.root = object()
        app._status_var = Mock()
        app._post_root = lambda fn, *args: fn(*args)
        fetcher = Mock()
        fetcher.fetch_by_citation.return_value = ("url", "html")
        parent = object()

        with patch("courtlistener_gui._ScholarTextWindow") as text_window:
            opened = app._try_open_citation(
                "", "410 U.S. 113", "", fetcher, None,
                view_parent=parent,
            )

        self.assertTrue(opened)
        self.assertIs(text_window.call_args.args[0], parent)

    def test_statute_and_statute_pdf_forward_the_shared_app(self):
        app = object()
        parent = object()
        status = Mock()
        with patch("courtlistener_gui._fetch_statute_window") as fetch:
            _open_statute_action(
                parent, ("usc", "42:1983:"), status, app=app,
            )
        fetch.assert_called_once_with(
            parent, "usc", "42:1983:", status, app=app, on_missing=None,
        )

        with patch("courtlistener_gui._open_statute_pdf") as open_pdf:
            _open_statute_action(
                parent,
                ("statpdf", "https://www.govinfo.gov/example.pdf"),
                status, app=app,
            )
        open_pdf.assert_called_once_with(
            parent, "https://www.govinfo.gov/example.pdf", status, app=app,
        )


class SpotlightPopupLifecycleTests(unittest.TestCase):
    @staticmethod
    def _win():
        win = object.__new__(CourtListenerGUI)
        win._quick_popup = None
        win._spotlight_toggle_at = 0.0
        win._mac_return_focus = Mock()  # platform-specific; not under test
        return win

    def test_close_quick_popup_withdraws_before_destroy(self):
        # On macOS, destroying the borderless popup without unmapping it
        # first can leave it painted on screen; the close helper withdraws,
        # then destroys, and always clears the tracked reference.
        win = self._win()
        popup = Mock()
        win._quick_popup = popup

        win._close_quick_popup()

        self.assertIsNone(win._quick_popup)
        self.assertEqual(popup.mock_calls, [call.withdraw(), call.destroy()])

    def test_close_quick_popup_survives_a_dead_window(self):
        win = self._win()
        popup = Mock()
        popup.withdraw.side_effect = tk.TclError("gone")
        popup.destroy.side_effect = tk.TclError("gone")
        win._quick_popup = popup

        win._close_quick_popup()  # must not raise

        self.assertIsNone(win._quick_popup)
        win._close_quick_popup()  # idempotent with nothing tracked

    def test_hotkey_toggle_debounces_a_duplicate_fire(self):
        # A duplicated hotkey delivery must not close the popup and
        # immediately reopen it: a second toggle arriving within the
        # debounce window is ignored outright.
        win = self._win()
        first = Mock()
        win._quick_popup = first

        win._toggle_quick_search_popup()

        self.assertIsNone(win._quick_popup)
        first.destroy.assert_called_once()
        win._mac_return_focus.assert_called_once()

        second = Mock()
        win._quick_popup = second
        win._toggle_quick_search_popup()  # the duplicate of the same press

        self.assertIs(win._quick_popup, second)
        second.destroy.assert_not_called()


class CitationEnrichmentTriggerTests(unittest.TestCase):
    @staticmethod
    def _win(*, court: str, year: str, is_scotus: bool = False):
        win = object.__new__(_ScholarTextWindow)
        win._bb = {
            "name": "Example v. Example",
            "cite": "10 F.4th 20",
            "court": court,
            "year": year,
        }
        win._item = {"citation": ["10 F.4th 20"]}
        win._header_cites = []
        win._base_citation_override = ""
        win._is_scotus = is_scotus
        win._app = None
        win._post = Mock()
        return win

    def test_known_opinion_court_and_year_start_no_external_work(self):
        win = object.__new__(_ScholarTextWindow)
        win._item = {}
        win._blocks = [
            Block("center", [Span("10 F.4th 20 (2024)")]),
            Block("center", [Span("Example v. Example")]),
            Block("center", [Span(
                "United States Court of Appeals, Eleventh Circuit."
            )]),
            Block("para", [Span("JORDAN, Circuit Judge:")]),
        ]
        win._bb = win._compute_bluebook_parts()
        win._base_citation_override = ""
        win._app = None
        win._post = Mock()

        self.assertEqual(win._bb["court"], "11th Cir.")
        self.assertEqual(win._bb["year"], "2024")

        with (
            patch("courtlistener_gui.threading.Thread") as thread,
            patch("courtlistener_gui._case_law_name_for_cites") as cap_name,
            patch("courtlistener_gui._case_law_metadata") as cap_metadata,
            patch("courtlistener_gui._cl_item_for_citation") as cl_lookup,
        ):
            win._enrich_citation()

        thread.assert_not_called()
        cap_name.assert_not_called()
        cap_metadata.assert_not_called()
        cl_lookup.assert_not_called()

    def test_opened_opinion_caption_controls_and_local_body_fixes_case(self):
        win = object.__new__(_ScholarTextWindow)
        win._item = {
            "case_name": "Different Metadata Name v. Other Party",
            "citation": ["10 F.4th 20"],
        }
        win._blocks = [
            Block("center", [Span("NBCUNIVERSAL MEDIA, LLC v. DOE")]),
            Block("center", [Span("10 F.4th 20 (2024)")]),
            Block("center", [Span(
                "United States Court of Appeals, Eleventh Circuit."
            )]),
            Block("para", [Span(
                "NBCUniversal Media, LLC sued Doe. "
                "NBCUniversal Media later appealed."
            )]),
        ]

        win._bb = win._compute_bluebook_parts()
        win._base_citation_override = ""
        win._app = None
        win._post = Mock()

        self.assertEqual(win._bb["name"], "NBCUniversal Media, LLC v. Doe")
        self.assertEqual(win._bb["_caption_case_unresolved"], ())
        with (
            patch("courtlistener_gui.threading.Thread") as thread,
            patch("courtlistener_gui._case_law_name_for_cites") as cap_name,
        ):
            win._enrich_citation()
        thread.assert_not_called()
        cap_name.assert_not_called()

    def test_known_scotus_status_and_year_start_no_external_work(self):
        # A Supreme Court citation correctly has no court abbreviation in its
        # parenthetical; known SCOTUS status satisfies the court requirement.
        win = self._win(court="", year="2024", is_scotus=True)

        with patch("courtlistener_gui.threading.Thread") as thread:
            win._enrich_citation()

        thread.assert_not_called()

    def test_missing_year_starts_the_enrichment_path(self):
        win = self._win(court="11th Cir.", year="")

        class ImmediateThread:
            def __init__(self, *, target, daemon):
                self.target = target

            def start(self):
                self.target()

        with (
            patch("courtlistener_gui.threading.Thread", ImmediateThread),
            patch("courtlistener_gui._case_law_name_for_cites") as cap_name,
            patch(
                "courtlistener_gui._case_law_metadata",
                return_value={"decision_date": "2023-06-01"},
            ) as cap_metadata,
        ):
            win._enrich_citation()

        cap_name.assert_not_called()
        cap_metadata.assert_called_once_with("10 F.4th 20")
        win._post.assert_called_once()

    def test_unresolved_entity_case_uses_only_capitalization_donor(self):
        win = self._win(court="11th Cir.", year="2024")
        win._bb["name"] = "Nbcuniversal Media, LLC v. Doe"
        win._bb["_caption_case_unresolved"] = ("nbcuniversal",)

        class ImmediateThread:
            def __init__(self, *, target, daemon):
                self.target = target

            def start(self):
                self.target()

        with (
            patch("courtlistener_gui.threading.Thread", ImmediateThread),
            patch(
                "courtlistener_gui._case_law_name_for_cites",
                return_value=(
                    "NBCUniversal Holdings, LLC v. Completely Different Party"
                ),
            ) as cap_name,
            patch("courtlistener_gui._case_law_metadata") as cap_metadata,
            patch("courtlistener_gui._cl_item_for_citation") as cl_lookup,
        ):
            win._enrich_citation()

        cap_name.assert_called_once()
        cap_metadata.assert_not_called()
        cl_lookup.assert_not_called()
        win._post.assert_called_once_with(
            win._apply_enriched_citation,
            "11th Cir.",
            "2024",
            "NBCUniversal Media, LLC v. Doe",
        )


class OpinionUnpublishedCaseLinkTests(unittest.TestCase):
    def test_wl_link_inherits_docket_from_full_opinion_text(self):
        full_text = (
            "Care One Mgmt., LLC v. United Healthcare Workers E., "
            "No. 12-6371, 2024 WL 1327972, at *7 "
            "(D.N.J. Mar. 28, 2024). Later the court cited "
            "2024 WL 1327972, at *9 (D.N.J. Mar. 28, 2024)."
        )
        index = _recap_spec_index(full_text)

        ranges = _recap_citation_ranges(
            "2024 WL 1327972, at *9 (D.N.J. Mar. 28, 2024).",
            index,
        )

        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0][2][0], "recap")
        spec = json.loads(ranges[0][2][1])
        self.assertEqual(spec["docket"], "12-6371")
        self.assertEqual(spec["court"], "njd")
        self.assertEqual(spec["date"], "2024-03-28")

    def test_docket_only_opinion_citation_gets_recap_action(self):
        text = (
            "Peninsula Pathology Assocs. v. Am. Int'l Indus., "
            "No. 23-1971 (4th Cir. Feb. 12, 2024)"
        )

        ranges = _special_citation_ranges([Span(text)], {})

        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0][2][0], "recap")
        spec = json.loads(ranges[0][2][1])
        self.assertEqual(spec["docket"], "23-1971")
        self.assertEqual(spec["court"], "ca4")

    def test_opinion_recap_action_uses_brief_reader_opener(self):
        win = object.__new__(_ScholarTextWindow)
        spec = json.dumps({"docket": "23-1971", "court": "ca4"})
        win._link_actions = {"link": ("recap", spec)}
        win._app = Mock()
        win._win = Mock()
        win._status_var = Mock()

        with patch("courtlistener_gui._open_recap_citation") as opener:
            win._follow_link("link")

        opener.assert_called_once_with(
            win._app, win._win, spec, win._status_var.set)


class HistoricalFederalCourtTests(unittest.TestCase):
    def test_old_circuit_court_id_reaches_bluebook_form(self):
        # United States v. Cohn, 128 F. 615 (C.C.S.D.N.Y. 1904):
        # CourtListener's id for the old circuit court (abolished 1912) is
        # "circtsdny" — previously printed raw in the parenthetical.
        win = object.__new__(_ScholarTextWindow)
        win._item = {
            "case_name": "United States v. Cohn",
            "citation": ["128 F. 615"],
            "court_id": "circtsdny",
            "date_filed": "1904-02-15",
        }
        win._blocks = [
            Block("center", [Span("128 F. 615 (1904)")]),
            Block("center", [Span("UNITED STATES v. COHN.")]),
            Block("center", [Span("Circuit Court, S. D. New York.")]),
            Block("para", [Span("HOLT, District Judge.")]),
        ]

        bb = win._compute_bluebook_parts()

        self.assertEqual(bb["court"], "C.C.S.D.N.Y.")
        self.assertEqual(bb["year"], "1904")

    def test_historical_and_bankruptcy_ids_are_mapped(self):
        from court_catalog import COURT_BLUEBOOK
        self.assertEqual(COURT_BLUEBOOK["circtsdny"], "C.C.S.D.N.Y.")
        self.assertEqual(COURT_BLUEBOOK["circtdal"], "C.C.D. Ala.")
        self.assertEqual(COURT_BLUEBOOK["ald"], "D. Ala.")
        self.assertEqual(COURT_BLUEBOOK["nysb"], "Bankr. S.D.N.Y.")
        # CL's "arb" is Arizona (not Arkansas — those are areb/arwb): the
        # table is generated from court *names*, immune to the id scheme.
        self.assertEqual(COURT_BLUEBOOK["arb"], "Bankr. D. Ariz.")

    def test_unknown_court_id_is_never_printed_raw(self):
        from courtlistener_gui import _court_for_paren
        self.assertEqual(
            _court_for_paren("100 F. 1", "someunknownid", "someunknownid"),
            "",
        )

    def test_old_circuit_header_line_parses(self):
        self.assertEqual(
            bluebook_federal_trial_court("Circuit Court, S. D. New York."),
            "C.C.S.D.N.Y.",
        )
        self.assertEqual(
            bluebook_federal_trial_court("Circuit Court, D. Massachusetts."),
            "C.C.D. Mass.",
        )
        # County circuit courts are state trial courts, never C.C.
        for name in ("Circuit Court for Baltimore County, Maryland",
                     "Circuit Court of Cook County, Illinois"):
            self.assertEqual(bluebook_federal_trial_court(name), "")


class FederalTrialCourtTests(unittest.TestCase):
    def test_district_captions_reach_bluebook_form(self):
        cases = [
            ("United States District Court, M.D. North Carolina.",
             "M.D.N.C."),
            ("United States District Court, District of Columbia.",
             "D.D.C."),
            ("United States District Court, N.D. Illinois, "
             "Eastern Division.", "N.D. Ill."),
            ("District Court, E. D. Pennsylvania.", "E.D. Pa."),
            # A single-district state's division tail is not a district:
            # "C. D." after the state means Central Division.
            ("United States District Court, South Dakota, C. D.",
             "D.S.D."),
            ("United States Bankruptcy Court, S.D. Texas, "
             "Houston Division.", "Bankr. S.D. Tex."),
            ("United States District Court for the Eastern District "
             "of Pennsylvania", "E.D. Pa."),
        ]
        for name, want in cases:
            with self.subTest(name=name):
                self.assertEqual(bluebook_federal_trial_court(name), want)

    def test_state_and_appellate_courts_are_not_federal_districts(self):
        for name in ("District Court of Appeal of Florida, Third District.",
                     "District Court, City and County of Denver, Colorado.",
                     "Supreme Court of Wisconsin.",
                     "United States Court of Appeals, Fourth Circuit."):
            with self.subTest(name=name):
                self.assertEqual(bluebook_federal_trial_court(name), "")

    def test_f_supp_citation_gets_the_district_parenthetical(self):
        win = object.__new__(_ScholarTextWindow)
        win._item = {}
        win._blocks = [
            Block("center", [Span("627 F.Supp.3d 520 (2022)")]),
            Block("center", [Span("Lucille BELL v. AMERICAN "
                                  "INTERNATIONAL INDUSTRIES")]),
            Block("center", [Span("United States District Court, "
                                  "M.D. North Carolina.")]),
            Block("para", [Span("OSTEEN, JR., District Judge.")]),
        ]

        bb = win._compute_bluebook_parts()

        self.assertEqual(bb["court"], "M.D.N.C.")
        self.assertEqual(bb["year"], "2022")


class PublicDomainCitationTests(unittest.TestCase):
    def test_wisconsin_initial_citation_orders_all_three_sources(self):
        self.assertEqual(
            _wisconsin_display_cite([
                "960 N.W.2d 869", "2021 WI 64", "397 Wis. 2d 719",
            ]),
            "2021 WI 64, 397 Wis. 2d 719, 960 N.W.2d 869",
        )

    def test_paragraph_pin_follows_public_domain_cite(self):
        win = object.__new__(_ScholarTextWindow)
        win._base_citation_override = ""
        win._bb = {
            "name": "State v. Prado",
            "cite": "397 Wis. 2d 719",
            "display_cite": "2021 WI 64, 397 Wis. 2d 719, 960 N.W.2d 869",
            "court": "Wis.", "year": "2021",
            "omit_parenthetical": "1", "pin_kind": "paragraph",
        }

        plain, _rtf = win._bluebook_citation("¶ 12")

        self.assertEqual(
            plain,
            "State v. Prado, 2021 WI 64, ¶ 12, 397 Wis. 2d 719, "
            "960 N.W.2d 869.",
        )


class NotYetReportedCitationTests(unittest.TestCase):
    """Bluebook 10.8.1(b): an opinion the reporters have not reached is cited
    by docket number and exact date.  Clark v. Sweeney was decided in
    November 2025 and has no U.S. Reports page — the Court prints
    "606 U. S. ____" — so the app used to print no citation at all."""

    @staticmethod
    def _blocks():
        return [
            Block("center", [Span(
                "TERENCE CLARK, DIRECTOR, PRINCE GEORGE'S COUNTY DEPARTMENT "
                "OF CORRECTIONS, ET AL., v. JEREMIAH ANTOINE SWEENEY."
            )]),
            Block("center", [Span("No. 25-52.")]),
            Block("center", [Span("Supreme Court of the United States.")]),
            Block("center", [Span("Decided November 24, 2025.")]),
        ]

    def test_docket_and_decision_date_are_read_from_the_opinion(self):
        blocks = self._blocks()
        self.assertEqual(_docket_cite({}, blocks), "No. 25-52")
        self.assertEqual(_decision_date_paren({}, blocks), "Nov. 24, 2025")

    def test_the_search_result_supplies_them_when_the_header_does_not(self):
        self.assertEqual(_docket_cite({"docketNumber": "25-52"}, []),
                         "No. 25-52")
        self.assertEqual(_decision_date_paren({"dateFiled": "2025-11-24"}, []),
                         "Nov. 24, 2025")

    def test_a_consolidated_docket_line_cites_only_the_first(self):
        blocks = [Block("center", [Span("Nos. 24-1287, 25-250.")])]
        self.assertEqual(_docket_cite({}, blocks), "No. 24-1287")

    def test_an_application_docket_is_recognized(self):
        blocks = [Block("center", [Span("No. 24A1007 24-1177.")])]
        self.assertEqual(_docket_cite({}, blocks), "No. 24A1007")

    def test_a_bare_year_is_not_enough_for_a_docket_citation(self):
        # Without the day there is no rule-10.8.1(b) parenthetical to print,
        # and a bare year belongs to a reported cite.
        self.assertEqual(_decision_date_paren({"dateFiled": "2025"}, []), "")

    def test_the_citation_reads_as_the_rule_prints_it(self):
        win = object.__new__(_ScholarTextWindow)
        win._base_citation_override = ""
        win._bb = {
            "name": "Clark v. Sweeney", "cite": "", "display_cite": "",
            "court": "", "year": "2025",
            "docket_cite": "No. 25-52",
            "docket_paren": "U.S. Nov. 24, 2025",
            "omit_parenthetical": "", "pin_kind": "page",
        }

        self.assertEqual(
            win._bluebook_citation(None)[0],
            "Clark v. Sweeney, No. 25-52 (U.S. Nov. 24, 2025).",
        )
        self.assertEqual(
            win._bluebook_citation(None, writer="per curiam")[0],
            "Clark v. Sweeney, No. 25-52 (U.S. Nov. 24, 2025) (per curiam).",
        )
        self.assertEqual(
            win._automatic_base_citation(),
            "Clark v. Sweeney, No. 25-52 (U.S. Nov. 24, 2025)",
        )

    def test_a_reported_opinion_is_untouched(self):
        win = object.__new__(_ScholarTextWindow)
        win._base_citation_override = ""
        win._bb = {
            "name": "Carpenter v. United States", "cite": "585 U.S. 296",
            "display_cite": "585 U.S. 296", "court": "", "year": "2018",
            "docket_cite": "", "docket_paren": "",
            "omit_parenthetical": "", "pin_kind": "page",
        }

        self.assertEqual(
            win._bluebook_citation(None)[0],
            "Carpenter v. United States, 585 U.S. 296 (2018).",
        )


class MappedUsReportsCitationTests(unittest.TestCase):
    @staticmethod
    def _window():
        win = object.__new__(_ScholarTextWindow)
        win._base_citation_override = ""
        win._bb = {
            "name": "Ramos v. Louisiana",
            "cite": "140 S. Ct. 1390",
            "display_cite": "140 S. Ct. 1390",
            "court": "",
            "year": "2020",
            "omit_parenthetical": "",
            "pin_kind": "page",
        }
        return win

    def test_copy_only_override_uses_us_reporter_and_pin(self):
        plain, _rtf = self._window()._bluebook_citation(
            "91", cite_override="590 U.S. 83",
        )
        self.assertEqual(
            plain, "Ramos v. Louisiana, 590 U.S. 83, 91 (2020)."
        )

    def test_manual_citation_remains_authoritative(self):
        win = self._window()
        win._base_citation_override = (
            "Ramos v. Louisiana, 140 S. Ct. 1390 (2020)"
        )
        plain, _rtf = win._bluebook_citation(
            "1395", cite_override="590 U.S. 83",
        )
        self.assertEqual(
            plain, "Ramos v. Louisiana, 140 S. Ct. 1390, 1395 (2020)."
        )

    def test_restarted_note_number_uses_its_own_writings_page(self):
        class Text:
            marks = {
                "majority-note": "10.0", "majority-end": "11.0",
                "dissent-note": "20.0", "dissent-end": "21.0",
            }

            def index(self, value):
                return self.marks.get(value, value)

            def compare(self, left, operator, right):
                def key(value):
                    line, char = self.index(value).split(".")
                    return int(line), int(char)

                lhs, rhs = key(left), key(right)
                return {
                    "<": lhs < rhs, ">": lhs > rhs,
                    "<=": lhs <= rhs, ">=": lhs >= rhs,
                }[operator]

            @staticmethod
            def get(_start, _end):
                return ""

        win = self._window()
        win._text = Text()
        win._fn_regions = [
            ("majority-note", "majority-end", "1", None),
            ("dissent-note", "dissent-end", "1", None),
        ]
        win._fn_region_fids = {
            "majority-note": "majority-fn-1",
            "dissent-note": "dissent-fn-1",
        }
        win._mapped_us_note_pages = {
            "majority-fn-1": 90,
            "dissent-fn-1": 121,
        }
        win._mapped_copy_cite = "590 U.S. 83"

        self.assertEqual(
            win._pin_with_footnotes(
                "20.0", "21.0", mapped_us=True
            ),
            "121 n.1",
        )


class PdfReporterCitationInferenceTests(unittest.TestCase):
    @staticmethod
    def _window():
        win = object.__new__(_ScholarTextWindow)
        win._us_reports_cite = ""
        win._item = {}
        win._bb = {"cite": ""}
        win._header_cites = []
        win._case_law_pdf_choices = []
        return win

    def test_pdf_first_case_law_url_supplies_its_reporter(self):
        win = self._window()
        url = _static_case_law_url("754 F.2d 898")

        self.assertEqual(win._pdf_reporter_cite(url), "754 F.2d 898")

    def test_us_reports_url_can_use_item_parallel_citation(self):
        win = self._window()
        win._item = {"citation": ["140 S. Ct. 1390", "590 U.S. 83"]}

        self.assertEqual(
            win._pdf_reporter_cite(
                "https://tile.loc.gov/storage-services/service/ll/usrep/"
                "usrep590/usrep590083/usrep590083.pdf"
            ),
            "590 U.S. 83",
        )


class PdfFirstLocationPipelineTests(unittest.TestCase):
    @staticmethod
    def _glyphs(text, y):
        return [
            (
                char,
                (
                    72.0 + index * 5.0,
                    y,
                    77.0 + index * 5.0,
                    y + 10.0,
                ),
            )
            for index, char in enumerate(text)
        ]

    def test_discovered_text_and_extracted_pages_build_switch_map(self):
        first = (
            "The first reporter page explains the distinctive federal "
            "jurisdictional question."
        )
        second = (
            "The next reporter page applies the governing rule and affirms "
            "the judgment."
        )
        source = _CasePdfTextSource(
            "case_law", "Text", "static.case.law", "case.json",
            f"{first} *899 {second}", {}, [], [],
        )
        win = object.__new__(_PdfWindow)
        win._url = _static_case_law_url("754 F.2d 898")
        win._text_source = source
        win._pdf_text_pages = [
            self._glyphs(first, 700.0),
            self._glyphs(second, 700.0),
        ]
        win._location_map = None
        win._location_map_running = False
        win._post = lambda fn, *args: fn(*args)

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch("courtlistener_gui.threading.Thread", ImmediateThread):
            win._maybe_start_location_map()

        self.assertIsNotNone(win._location_map)
        self.assertTrue(win._location_map.navigation_ready)
        self.assertEqual(
            [row.reporter_page for row in win._location_map.boundaries],
            [898, 899],
        )

    def test_sct_stars_under_us_pdf_enable_us_mapping_without_false_exacts(self):
        first = (
            "The opening Supreme Court page explains the distinctive "
            "constitutional question and relevant history."
        )
        second = (
            "The following Supreme Court page applies that history and "
            "announces the controlling judgment."
        )
        source = _CasePdfTextSource(
            "courtlistener", "Text", "CourtListener", "opinion",
            f"*1390 {first} *1391 {second}",
            {"citation": ["140 S. Ct. 1390", "590 U.S. 83"]},
            [], [],
        )
        win = object.__new__(_PdfWindow)
        win._url = _static_case_law_url("590 U.S. 83")
        win._text_source = source
        win._pdf_text_pages = [
            self._glyphs(first, 700.0),
            self._glyphs(second, 700.0),
        ]
        win._location_map = None
        win._location_map_running = False
        win._post = lambda fn, *args: fn(*args)

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch("courtlistener_gui.threading.Thread", ImmediateThread):
            win._maybe_start_location_map()

        self.assertEqual(
            win._location_map.native_cite, "140 S. Ct. 1390"
        )
        self.assertTrue(win._location_map.navigation_ready)
        self.assertTrue(win._location_map.copy_ready)
        self.assertEqual(win._location_map.copy_cite, "590 U.S. 83")
        self.assertTrue(all(
            not row.exact for row in win._location_map.boundaries
        ))


@unittest.skipUnless(HAVE_PDFIUM, "pypdfium2 not installed")
class PdfLocationAnalysisPipelineTests(unittest.TestCase):
    @staticmethod
    def _pdf(*page_lines):
        """Small multi-page PDF used by the real pdfium extractor."""
        objects = [
            b"<</Type/Catalog/Pages 2 0 R>>",
            (
                b"<</Type/Pages/Kids[3 0 R 4 0 R]/Count 2>>"
            ),
            (
                b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
                b"/Resources<</Font<</F1 7 0 R>>>>/Contents 5 0 R>>"
            ),
            (
                b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
                b"/Resources<</Font<</F1 7 0 R>>>>/Contents 6 0 R>>"
            ),
        ]
        for line in page_lines:
            escaped = str(line).replace("\\", "\\\\").replace("(", "\\(") \
                .replace(")", "\\)")
            stream = (
                f"BT /F1 12 Tf 72 700 Td ({escaped}) Tj ET"
            ).encode("latin-1")
            objects.append(
                b"<</Length %d>>stream\n" % len(stream)
                + stream + b"\nendstream"
            )
        objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj" % number + body + b"endobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n" % (len(objects) + 1)
        out += b"0000000000 65535 f \n"
        for offset in offsets:
            out += b"%010d 00000 n \n" % offset
        out += (
            b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref)
        )
        return bytes(out)

    @staticmethod
    def _window(text):
        win = object.__new__(_ScholarTextWindow)
        win._pdf_analysis_cache = {}
        win._pdf_analysis_running = set()
        win._pdf_analysis_waiters = {}
        win._pdf_url_keys = {}
        win._pdf_map_jobs = set()
        win._active_pdf_analysis_key = None
        win._active_text_location_map = None
        win._pdf_url = ""
        win._mode = "courtlistener"
        win._bb = {"cite": "754 F.2d 898"}
        win._base_citation_override = ""
        win._us_reports_cite = ""
        win._item = {}
        win._header_cites = []
        win._case_law_pdf_choices = []
        win._cl_primary = True
        win._parts = []
        win._scholar_text = ""
        win._cl_parts = []
        win._cl_text = text
        win._post = lambda fn, *args: fn(*args)
        win._install_current_location_map = Mock()
        win._ensure_cached_location_maps = (
            _ScholarTextWindow._ensure_cached_location_maps.__get__(win)
        )
        return win

    def test_real_extraction_reaches_cache_callback_and_location_map(self):
        first = (
            "The first reporter page explains the distinctive federal "
            "jurisdictional question."
        )
        second = (
            "The next reporter page applies the governing rule and affirms "
            "the judgment."
        )
        win = self._window(f"{first} *899 {second}")
        callback = Mock()

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch("courtlistener_gui.threading.Thread", ImmediateThread):
            key = win._request_pdf_analysis(
                self._pdf(first, second),
                _static_case_law_url("754 F.2d 898"),
                callback,
            )

        result = win._pdf_analysis_cache[key]
        self.assertEqual(len(result["pages"]), 2)
        self.assertIn("courtlistener", result["maps"])
        self.assertEqual(
            [row.reporter_page
             for row in result["maps"]["courtlistener"].boundaries],
            [898, 899],
        )
        callback.assert_called_once_with(result)

    def test_finished_analysis_rechecks_text_sources_that_arrived_late(self):
        win = self._window("Text that was loaded while PDF extraction ran.")
        key = ("pdf", 1, b"late-source")
        win._pdf_analysis_running.add(key)
        win._ensure_cached_location_maps = Mock()

        win._finish_pdf_analysis(
            key,
            {
                "url": _static_case_law_url("754 F.2d 898"),
                "pages": [],
                "links": {},
                "page_info": [],
                "part_starts": {},
                "maps": {},
            },
        )

        win._ensure_cached_location_maps.assert_called_once_with(key)


class WriterParentheticalTests(unittest.TestCase):
    @staticmethod
    def _win():
        return object.__new__(_ScholarTextWindow)

    @staticmethod
    def _part(kind: str, first_line: str, label: str = "") -> OpinionPart:
        return OpinionPart(
            label or first_line[:90], kind,
            [Block("para", [Span(first_line)])],
        )

    def test_joinder_byline_without_role_uses_part_kind(self):
        # Cohen v. California, 403 U.S. 15 (1971): Blackmun's byline names
        # only the joiners; the role was read from his opening lines when
        # the part was segmented.
        part = self._part(
            "dissent",
            "MR. JUSTICE BLACKMUN, with whom THE CHIEF JUSTICE and "
            "MR. JUSTICE BLACK join.",
        )
        self.assertEqual(
            self._win()._writer_parenthetical(part),
            "Blackmun, J., dissenting",
        )

    def test_separate_opinion_of_heading_uses_resolved_role(self):
        part = self._part(
            "dissent", "Separate opinion of MR. JUSTICE McREYNOLDS."
        )
        self.assertEqual(
            self._win()._writer_parenthetical(part),
            "McReynolds, J., dissenting",
        )

    def test_unresolved_separate_opinion_uses_neutral_parenthetical(self):
        part = self._part(
            "separate", "Separate opinion of MR. JUSTICE STORY."
        )
        win = self._win()
        self.assertEqual(
            win._writer_parenthetical(part),
            "Story, J., separate opinion",
        )
        win._base_citation_override = ""
        win._bb = {
            "name": "Example v. Example", "cite": "1 U.S. 10",
            "display_cite": "1 U.S. 10", "court": "", "year": "1800",
            "omit_parenthetical": "", "pin_kind": "page",
        }
        plain, _rtf = win._bluebook_citation(
            None, win._writer_parenthetical(part))
        self.assertEqual(
            plain,
            "Example v. Example, 1 U.S. 10 (1800) "
            "(Story, J., separate opinion).",
        )
        self.assertEqual(win._PART_BOX_TAGS["separate"], "box-separate")
        self.assertEqual(win._PART_LABEL_COLORS["separate"], "#59636f")
        self.assertEqual(win._SEPARATE_BG, "#f1f3f5")

    def test_spelled_out_bare_judge_byline(self):
        part = self._part("concurrence", "CLINTON, Judge.")
        self.assertEqual(
            self._win()._writer_parenthetical(part),
            "Clinton, J., concurring",
        )

    def test_comma_after_justice_in_byline(self):
        # Alleyne v. United States: Scholar prints "Justice, ALITO,
        # dissenting."
        part = self._part("dissent", "Justice, ALITO, dissenting.")
        self.assertEqual(
            self._win()._writer_parenthetical(part),
            "Alito, J., dissenting",
        )

    def test_full_name_circuit_byline_reduces_to_surname(self):
        part = self._part(
            "concurrence",
            "TOBY HEYTENS, Circuit Judge, with whom Judges HARRIS and "
            "BENJAMIN join, concurring:",
        )
        self.assertEqual(
            self._win()._writer_parenthetical(part),
            "Heytens, J., concurring",
        )
        # Disambiguating initials survive (two Nelsons on the CA9 bench).
        part = self._part(
            "dissent", "R. NELSON, Circuit Judge, dissenting:")
        self.assertEqual(
            self._win()._writer_parenthetical(part),
            "R. Nelson, J., dissenting",
        )


class CombinedOpinionCompletenessTests(unittest.TestCase):
    def test_lone_unpaginated_combined_record_is_still_a_body_candidate(self):
        combined = {
            "type": "010combined",
            "plain_text": "Lead opinion.\n\nJustice Jones, dissenting.\n\nI dissent.",
        }
        self.assertIs(_pick_combined_opinion([combined]), combined)

    def test_truncated_combined_cannot_hide_typed_separate_writings(self):
        opinions = [
            {"type": "010combined", "html": "<p>combined</p>"},
            {"type": "020lead", "html": "<p>lead</p>"},
            {"type": "035concurrenceinpart", "html": "<p>Ryan</p>"},
            {"type": "030concurrence", "html": "<p>Williams</p>"},
        ]
        combined_parts = [OpinionPart("Opinion", "majority", [])]

        self.assertFalse(_combined_parts_cover_typed(opinions, combined_parts))

    def test_more_complete_combined_document_remains_eligible(self):
        opinions = [
            {"type": "010combined", "html": "<p>combined</p>"},
            {"type": "020lead", "html": "<p>lead</p>"},
            {"type": "040dissent", "html": "<p>dissent</p>"},
        ]
        combined_parts = [
            SimpleNamespace(kind="majority"),
            SimpleNamespace(kind="concurrence"),
            SimpleNamespace(kind="dissent"),
        ]

        self.assertTrue(_combined_parts_cover_typed(opinions, combined_parts))


class FederalCasesLookupTests(unittest.TestCase):
    """Offline pieces of the Federal Cases case-number resolution: the name
    query builder's OCR forgiveness, the headnote-number verifier, and the
    F. Cas. volume extraction (courtlistener.find_fedcas_case's helpers)."""

    def test_name_queries_tightest_first_with_ocr_variants(self):
        from courtlistener import _fedcas_name_queries

        qs = _fedcas_name_queries("Har-ney v. The Sydney L. Wright")
        # The name as printed, then de-hyphenated, then ANDed tokens, then
        # the one-edit fuzzy tokens.
        self.assertEqual(qs[0], 'caseName:"Har-ney v. The Sydney L. Wright"')
        self.assertIn('caseName:"Harney v. The Sydney L. Wright"', qs)
        self.assertTrue(any("~1" in q for q in qs))

    def test_name_queries_join_surname_particles(self):
        from courtlistener import _fedcas_name_queries

        # CourtListener titles the case "Macy v. DeWolf" — the joined
        # spelling must be searched as its own variant.
        qs = _fedcas_name_queries("Macy v. De Wolf")
        self.assertIn('caseName:"Macy v. DeWolf"', qs)

    def test_headnote_number_reads_only_the_head(self):
        from courtlistener import _fedcas_headnote_number

        self.assertEqual(
            _fedcas_headnote_number(
                "<p>Case No. 2,717. Lien on Foreign Vessel.</p>"),
            "2717")
        self.assertEqual(
            _fedcas_headnote_number("[Case No. 6,082a.]"), "6082a")
        # A number later in the headnotes is a cross-reference, not the
        # case's own number.
        self.assertIsNone(
            _fedcas_headnote_number(
                "Approving The Nestor, Case No. 10,126."))
        self.assertIsNone(_fedcas_headnote_number(""))

    def test_fcas_volume_from_citation_strings_and_dicts(self):
        from courtlistener import _fcas_volume

        self.assertEqual(_fcas_volume(["18 F. Cas. 9", "1 Sumn. 73"]), 18)
        self.assertEqual(_fcas_volume(["18 Fed. Cas. 9"]), 18)
        self.assertEqual(
            _fcas_volume([{"volume": 5, "reporter": "F. Cas.", "page": 680}]),
            5)
        self.assertEqual(
            _fcas_volume([
                {"volume": 5, "reporter": "Fed. Cas.", "page": 680},
            ]),
            5,
        )
        self.assertIsNone(_fcas_volume(["410 U.S. 113"]))

    def test_detect_links_routes_fedcas_and_nominative_parallels(self):
        from citations import detect_links

        links = detect_links(
            "See The General Smith, 4 Wheat. [17 U. S.] 438; Cole v. The "
            "Atlantic, Case No. 2,976; The Chusan, Id. 2,717.")
        actions = [a for _s, _e, a in links]
        self.assertIn(("cite", "4 Wheat. 438"), actions)
        fedcas = [json.loads(v) for k, v in actions if k == "fedcas"]
        self.assertEqual(
            [(f["no"], f.get("name")) for f in fedcas],
            [("2976", "Cole v. The Atlantic"), ("2717", "The Chusan")])


if __name__ == "__main__":
    unittest.main()
