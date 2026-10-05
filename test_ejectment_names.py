"""Ejectment captions: the party a fictitious lessee sued for, and the form
of it the opinions citing the case use ("Murray's Lessee" for John Den, ex
dem. James B. Murray; "Doe" in Doe v. Considine)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["GETCASES_SKIP_DEPENDENCY_PROMPT"] = "1"

import case_name_usage
from bluebook_names import (
    abbreviate_case_name,
    ejectment_case_names,
    ejectment_party_caption,
    normal_case_caption,
)
from courtlistener_gui import _ScholarTextWindow, _scholar_caption_name
from google_scholar import Block, Span

MURRAY = (
    "JOHN DEN, ex dem. JAMES B. MURRAY AND JOHN C. KAYSER, PLAINTIFFS, v. "
    "THE HOBOKEN LAND AND IMPROVEMENT COMPANY. JOHN DEN, ex dem. JAMES B. "
    "MURRAY ET AL. v. THE HOBOKEN LAND AND IMPROVEMENT COMPANY.")
JACKSON = ("JAMES JACKSON, ON THE DEMISE OF HARMAN V. HART, PLAINTIFF IN "
           "ERROR v. ELIAS LAMPHIRE, DEFENDANT IN ERROR.")
MARTIN = "MARTIN, Heir at law and devisee of FAIRFAX, v. HUNTER'S Lessee."


def names(caption: str):
    return ejectment_case_names(normal_case_caption(caption))


class EjectmentCaptionTests(unittest.TestCase):
    def test_demise_is_cited_as_captioned_until_asked(self):
        # Murray's Lessee v. Hoboken Land & Improvement Co., 59 U.S. (18
        # How.) 272 (1856): Scholar's caption names Den on Murray's demise.
        self.assertEqual(
            abbreviate_case_name(normal_case_caption(MURRAY)),
            "Den ex dem. Murray v. Hoboken Land & Improvement Co.")
        found = names(MURRAY)
        self.assertEqual(found.side, 0)
        self.assertEqual(found.default, "demise")
        self.assertEqual(found.names, {
            "demise": "Den ex dem. Murray v. Hoboken Land & Improvement Co.",
            "nominal": "Den v. Hoboken Land & Improvement Co.",
            "lessor": "Murray v. Hoboken Land & Improvement Co.",
            "lessee": "Murray's Lessee v. Hoboken Land & Improvement Co.",
        })
        self.assertEqual(found.anchor(), "Hoboken")

    def test_lessors_middle_initial_is_not_the_separator(self):
        # Jackson v. Lamphire, 28 U.S. (3 Pet.) 280 (1830).
        self.assertEqual(abbreviate_case_name(normal_case_caption(JACKSON)),
                         "Jackson ex dem. Hart v. Lamphire")
        self.assertEqual(names(JACKSON).parties["lessee"], "Hart's Lessee")

    def test_lessee_of_and_possessive_lessee_captions(self):
        cases = {
            "The Lessee of Edward Livingston and others v. John Moore and "
            "others": ("Lessee of Livingston v. Moore", "Livingston v. Moore"),
            "John Pollard et al., Lessee, Plaintiff in error, v. John Hagan "
            "et al., Defendants in Error": (
                "Pollard's Lessee v. Hagan", "Pollard v. Hagan"),
            "James Greenleaf's Lessee, Plaintiff in error, v. James Birth, "
            "Defendant in error": (
                "Greenleaf's Lessee v. Birth", "Greenleaf v. Birth"),
            "Hinde's Lessee against Longworth": (
                "Hinde's Lessee v. Longworth", "Hinde v. Longworth"),
            "Doe ex dem, William Patterson, Plaintiff in error, v. Elisha "
            "Winn and others, Defendants in error": (
                "Doe ex dem. Patterson v. Winn", "Patterson v. Winn"),
        }
        for caption, (cited, lessor) in cases.items():
            with self.subTest(caption=caption):
                self.assertEqual(
                    abbreviate_case_name(normal_case_caption(caption)), cited)
                self.assertEqual(names(caption).names["lessor"], lessor)

    def test_party_named_second_and_its_adversarys_description(self):
        # Martin v. Hunter's Lessee, 14 U.S. (1 Wheat.) 304 (1816): the heir's
        # capacity is no part of the name.
        self.assertEqual(abbreviate_case_name(normal_case_caption(MARTIN)),
                         "Martin v. Hunter's Lessee")
        found = names(MARTIN)
        self.assertEqual((found.side, found.anchor()), (1, "Martin"))
        self.assertEqual(found.names["lessor"], "Martin v. Hunter")

    def test_two_lessors_keep_their_possessive(self):
        self.assertEqual(
            abbreviate_case_name(
                "Johnson & Graham's Lessee v. William M‘intosh"),
            "Johnson & Graham's Lessee v. M'intosh")
        self.assertEqual(
            names("Johnson & Graham's Lessee v. William M'Intosh")
            .names["lessor"], "Johnson v. M'Intosh")

    def test_real_leases_and_other_captions_are_not_ejectment(self):
        for caption in (
            "PENNSYLVANIA RAILROAD COMPANY, LESSEE OF THE NORTHERN CENTRAL "
            "RAILWAY COMPANY, v. TOWERS ET AL.",
            "UNITED STATES v. NEW YORK CENTRAL RAILROAD COMPANY, LESSEE",
            "John Scott and Carl Boland, Plaintiffs in error, v. John Jones, "
            "Lessee of The Detroit Young Men's Society",
            "Doe d. Bennett v. Turner",
            "Ex Parte Martha Bradstreet; in the Matter of James Jackson ex "
            "dem. Martha Bradstreet vs. Daniel Thomas",
            "Truck Rent-a-Center, Inc. v. Puritan Farms 2nd, Inc.",
        ):
            with self.subTest(caption=caption):
                self.assertIsNone(names(caption))

    def test_every_form_abbreviates_to_itself(self):
        for caption in (MURRAY, JACKSON, MARTIN,
                        "The Lessee of Edward Livingston v. John Moore",
                        "Pomeroy's Lessee v. The State Bank of Indiana"):
            for form, name in names(caption).names.items():
                with self.subTest(caption=caption, form=form):
                    self.assertEqual(abbreviate_case_name(name), name)

    def test_caption_reader_keeps_the_demise_as_one_party(self):
        self.assertEqual(
            ejectment_party_caption(
                "JOHN DEN, ex dem. JAMES B. MURRAY AND JOHN C. KAYSER, "
                "PLAINTIFFS,"),
            "JOHN DEN, ex dem. JAMES B. MURRAY AND JOHN C. KAYSER")
        self.assertEqual(ejectment_party_caption("ELIAS LAMPHIRE"), "")
        blocks = [
            Block("center", [Span("28 U.S. 280 (1830)")]),
            Block("center", [Span(JACKSON)]),
            Block("para", [Span("Mr. Justice JOHNSON delivered the opinion.")]),
        ]
        self.assertEqual(
            _scholar_caption_name(blocks),
            "James Jackson, on the Demise of Harman V. Hart v. Elias Lamphire")


class UsageQueryTests(unittest.TestCase):
    def test_a_phrase_inside_a_longer_form_excludes_it(self):
        queries = case_name_usage.usage_queries(names(MURRAY), [87010])
        self.assertEqual(
            queries["lessee"],
            '(cites:87010) AND "Murray\'s Lessee v. Hoboken"')
        self.assertEqual(
            queries["lessor"],
            '(cites:87010) AND "Murray v. Hoboken" '
            'NOT "dem. Murray v. Hoboken" NOT "of Murray v. Hoboken"')
        self.assertEqual(queries["nominal"],
                         '(cites:87010) AND "Den v. Hoboken"')
        # CourtListener drops the possessive: "Martin v. Hunter" is in
        # every "Martin v. Hunter's Lessee".
        queries = case_name_usage.usage_queries(names(MARTIN), [85160, 9])
        self.assertEqual(
            queries["lessor"],
            '(cites:85160 OR cites:9) AND "Martin v. Hunter" '
            'NOT "Martin v. Hunter\'s Lessee"')

    def test_the_form_most_citing_opinions_use(self):
        found = names(MURRAY)
        counts = {"Murray's Lessee v. Hoboken": 124, "Den v. Hoboken": 3,
                  "Den ex dem. Murray v. Hoboken": 27, "Murray v. Hoboken": 26}

        def search(query, **_kw):
            phrase = query.split(" AND ", 1)[1].split('"')[1]
            return {"count": counts[phrase]}

        client = Mock(search=Mock(side_effect=search))
        form, seen = case_name_usage.most_cited_form(client, found, [87010])
        self.assertEqual(form, "lessee")
        self.assertEqual(seen["lessee"], 124)

    def test_too_few_or_a_tie(self):
        self.assertIsNone(case_name_usage.pick_form(
            {"demise": 1, "nominal": 1, "lessor": 0}, "demise"))
        self.assertEqual(case_name_usage.pick_form(
            {"nominal": 9, "demise": 9}, "demise"), "demise")
        self.assertEqual(case_name_usage.pick_form(
            {"demise": 5, "nominal": 20}, "demise"), "nominal")

    def test_answers_are_kept_by_citation_however_spaced(self):
        found = names(MURRAY)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage.json"
            key = case_name_usage.cache_key("59 U.S. 272", found)
            self.assertIsNone(case_name_usage.remembered_form(key, path))
            case_name_usage.remember_form(key, "lessee", {"lessee": 124},
                                          path)
            self.assertEqual(case_name_usage.remembered_form(key, path),
                             "lessee")
            self.assertEqual(case_name_usage.cache_key("59 U.S.272", found),
                             key)
            case_name_usage.remember_form("other", None, {}, path)
            self.assertEqual(case_name_usage.remembered_form("other", path),
                             "")


class TextViewTests(unittest.TestCase):
    @staticmethod
    def _window():
        win = object.__new__(_ScholarTextWindow)
        win._item = {}
        win._blocks = [
            Block("center", [Span("59 U.S. 272 (1855)")]),
            Block("center", [Span("18 How. 272")]),
            Block("center", [Span(MURRAY)]),
            Block("center", [Span("Supreme Court of United States.")]),
            Block("para", [Span("Mr. Justice CURTIS delivered the opinion "
                                "of the court.")]),
        ]
        return win

    def test_caption_form_until_the_citing_opinions_are_asked(self):
        win = self._window()
        with patch("case_name_usage.remembered_form", return_value=None):
            bb = win._compute_bluebook_parts()
        self.assertEqual(
            bb["name"], "Den ex dem. Murray v. Hoboken Land & Improvement Co.")
        self.assertEqual(bb["_ejectment"].default, "demise")

    def test_an_answer_had_before_is_used_at_once(self):
        win = self._window()
        with patch("case_name_usage.remembered_form", return_value="lessee"):
            bb = win._compute_bluebook_parts()
        self.assertEqual(
            bb["name"], "Murray's Lessee v. Hoboken Land & Improvement Co.")
        self.assertIsNone(bb["_ejectment"])

    def _enrich(self, win, client):
        class ImmediateThread:
            def __init__(self, *, target, daemon):
                self.target = target

            def start(self):
                self.target()

        win._header_cites = []
        win._is_scotus = True
        win._app = Mock()
        win._app._token_var.get.return_value = "token"
        win._app._get_client.return_value = client
        win._post = Mock()
        with (
            patch("courtlistener_gui.threading.Thread", ImmediateThread),
            patch("courtlistener_gui._cited_opinion_ids",
                  return_value=[87010]),
            patch("case_name_usage.remembered_form", return_value=None),
            patch("case_name_usage.remember_form") as remember,
        ):
            win._enrich_citation()
        return remember

    def test_the_citing_opinions_choose_the_party_form(self):
        win = self._window()
        with patch("case_name_usage.remembered_form", return_value=None):
            win._bb = win._compute_bluebook_parts()
        win._base_citation_override = ""
        counts = {"Murray's Lessee v. Hoboken": 124, "Den v. Hoboken": 3,
                  "Den ex dem. Murray v. Hoboken": 27, "Murray v. Hoboken": 26}
        client = Mock(search=Mock(side_effect=lambda q, **_: {
            "count": counts[q.split(" AND ", 1)[1].split('"')[1]]}))

        remember = self._enrich(win, client)

        win._post.assert_called_once_with(
            win._apply_enriched_citation, "", "1855",
            "Murray's Lessee v. Hoboken Land & Improvement Co.")
        remember.assert_called_once()
        self.assertEqual(remember.call_args.args[1], "lessee")

    def test_an_edited_citation_is_not_second_guessed(self):
        win = self._window()
        with patch("case_name_usage.remembered_form", return_value=None):
            win._bb = win._compute_bluebook_parts()
        win._base_citation_override = "Murray v. Hoboken, 59 U.S. 272 (1856)"
        client = Mock()

        self._enrich(win, client)

        client.search.assert_not_called()
        win._post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
