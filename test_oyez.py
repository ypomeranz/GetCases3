"""The case details pane's Supreme Court record comes from Oyez, and must be
the case open, not another.

Oyez records a case's U.S. Reports page only for the older volumes: from
about 2009 most cases carry the volume alone, and the newest no citation at
all.  So a case is found by its volume and page where Oyez has the page,
else by the docket number the opinion gives, else by name among the cases
of its volume or its term.  The name the app has is the Bluebook's ("Brown
v. Ent. Merchs. Ass'n"), and a name must fit on both sides of the "v.": the
year and "Brown" alone once picked McDaniel v. Brown for Brown v.
Entertainment Merchants Association.

Oyez itself is never reached: a fake index answers each query the way its
search service would, from records shaped like the live ones.
"""

import os
import unittest
from unittest import mock

import oyez

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None


def _case(title, docket, term, vol=None, page=None, year=None, also=()):
    return {
        "title": title,
        "field_docket_number": docket,
        "field_additional_docket_numbers": list(also),
        "field_court_term": term,
        "field_citation:field_volume": vol,
        "field_citation:field_page": page,
        "field_citation:field_year": year,
        "url": f"https://api.oyez.org/cases/{term}/{docket}",
    }


BROWN = _case("brown v. entertainment merchants association", "08-1448",
              "2010", "564", None, "2011")
INDEX = [
    BROWN,
    _case("bond v. united states", "09-1227", "2010", "564", None, "2011"),
    _case("davis v. united states", "09-11328", "2010", "564", None, "2011"),
    _case("brown v. plata", "09-1233", "2010", "563", None, "2011"),
    _case("mcdaniel v. brown", "08-559", "2009", "558", "120", "2010"),
    _case("roe v. wade", "70-18", "1971", "410", "113", "1973"),
    _case("doe v. bolton", "70-40", "1971", "410", "179", "1973"),
    _case("obergefell v. hodges", "14-556", "2014", "576", None, "2015",
          also=("14-562", "14-571", "14-574")),
    _case("west virginia v. environmental protection agency", "20-1530",
          "2021", "597", None, "2022"),
    _case("moyle v. united states", "23-726", "2023", "603", None, "2024"),
    _case("trump v. united states", "23-939", "2023", "603", None, "2024"),
    _case("arizona v. smith", "23-1", "2023", "603", None, "2024"),
    _case("united states v. skrmetti", "23-477", "2024"),
]


class _FakeIndex:
    """Answers the queries oyez sends, as its search service would."""

    def __init__(self, cases):
        self.cases = cases
        self.queries = []

    def search(self, body):
        self.queries.append(body)
        query = body["query"]
        if "term" in query:
            (field, value), = query["term"].items()
            hits = [c for c in self.cases if c.get(field) == value]
        elif "terms" in query:
            (field, values), = query["terms"].items()
            hits = [c for c in self.cases if c.get(field) in values]
        elif "should" in query.get("bool", {}):
            wanted = set(query["bool"]["should"][0]["terms"]
                         ["field_docket_number"])
            hits = [c for c in self.cases
                    if c["field_docket_number"] in wanted
                    or wanted & set(c["field_additional_docket_numbers"])]
        else:
            match, *also = query["bool"]["must"] if "bool" in query else [
                query]
            words = set(match["multi_match"]["query"].lower().split())
            hits = [c for c in self.cases if words & set(c["title"].split())]
            for clause in also:
                (field, values), = clause["terms"].items()
                hits = [c for c in hits if c.get(field) in values]
        return hits[:body.get("size", 10)]


class LookupTests(unittest.TestCase):
    def setUp(self):
        oyez._CACHE.clear()
        self.addCleanup(oyez._CACHE.clear)
        self.index = _FakeIndex(list(INDEX))
        for patcher in (
                mock.patch.object(oyez, "_es_search",
                                  side_effect=self.index.search),
                mock.patch.object(oyez, "_get_json", side_effect=self._record)):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _record(url):
        term, docket = url.rsplit("/", 2)[-2:]
        return {"name": docket, "term": term, "docket_number": docket}

    def docket(self, cites=(), name="", year="", dockets=()):
        case = oyez.lookup(cites=list(cites), name=name, year=year,
                           dockets=list(dockets))
        return case.docket if case else None

    def test_brown_by_its_bluebook_name_in_its_volume(self):
        self.assertEqual(self.docket(["564 U.S. 786", "131 S. Ct. 2729"],
                                     "Brown v. Ent. Merchs. Ass'n", "2011"),
                         "08-1448")

    def test_a_party_and_the_year_alone_are_not_enough(self):
        # Without Brown in the index, Brown v. Plata (2011) and McDaniel v.
        # Brown (2010) each share "Brown" and a year with it.
        self.index.cases.remove(BROWN)
        self.assertIsNone(self.docket(
            ["564 U.S. 786"], "Brown v. Ent. Merchs. Ass'n", "2011"))
        self.assertIsNone(self.docket(
            [], "Brown v. Ent. Merchs. Ass'n", "2011"))

    def test_by_name_and_year_where_there_is_no_citation(self):
        self.assertEqual(self.docket([], "Brown v. Ent. Merchs. Ass'n",
                                     "2011"), "08-1448")

    def test_the_page_decides_where_oyez_records_it(self):
        self.assertEqual(self.docket(["410 U.S. 113"], "", "1973"), "70-18")

    def test_a_case_oyez_files_at_another_page_is_never_taken(self):
        self.assertIsNone(self.docket(["410 U.S. 999"], "Doe v. Bolton",
                                      "1973"))

    def test_the_docket_number_finds_a_case_with_no_citation(self):
        self.assertEqual(self.docket(["605 U.S. ___"], "", "2025",
                                     ["23-477"]), "23-477")

    def test_a_docket_the_volume_contradicts_is_not_taken(self):
        self.assertIsNone(self.docket(["603 U.S. 593"], "", "2024",
                                      ["08-1448"]))

    def test_cases_decided_together_by_any_of_their_dockets(self):
        self.assertEqual(self.docket(["576 U.S. 644"], "", "2015",
                                     ["14-571"]), "14-556")

    def test_the_united_states_as_a_party_is_not_enough(self):
        self.assertIsNone(self.docket(["603 U.S. 1"],
                                      "Smith v. United States", "2024"))

    def test_the_parties_are_compared_in_order(self):
        self.assertIsNone(self.docket(["603 U.S. 1"], "Smith v. Arizona",
                                      "2024"))

    def test_an_agency_by_its_initials(self):
        self.assertEqual(self.docket(["597 U.S. 697"], "West Virginia v. EPA",
                                     "2022"), "20-1530")

    def test_of_two_that_fit_the_one_decided_that_year(self):
        self.index.cases += [
            _case("brown v. board of education of topeka (1)", "1",
                  "1940-1955", "347", "483", "1954"),
            _case("brown v. board of education of topeka (2)", "2",
                  "1940-1955", "349", "294", "1955")]
        self.assertEqual(self.docket([], "Brown v. Bd. of Educ.", "1954"), "1")
        oyez._CACHE.clear()
        self.assertEqual(self.docket([], "Brown v. Bd. of Educ.", "1955"), "2")

    def test_two_cases_that_fit_equally_leave_none(self):
        self.index.cases.append(_case(
            "brown v. entertainment merchants association", "08-9999",
            "2010", "564", None, "2011"))
        self.assertIsNone(self.docket(["564 U.S. 786"],
                                      "Brown v. Ent. Merchs. Ass'n", "2011"))


class NameTests(unittest.TestCase):
    def test_bluebook_abbreviations_are_the_words_they_cut_short(self):
        for short, word in (("merchs", "merchants"), ("assn", "association"),
                            ("ent", "entertainment"), ("dept", "department"),
                            ("sch", "school"), ("bd", "board")):
            self.assertTrue(oyez._word_matches(short, word), short)

    def test_but_not_another_word(self):
        self.assertFalse(oyez._word_matches("co", "commission"))
        self.assertFalse(oyez._word_matches("brown", "mcdaniel"))

    def test_a_run_of_initials_is_one_word(self):
        self.assertEqual(oyez._name_words("Trump v. J.G.G."), ["trump", "jgg"])
        self.assertEqual(oyez._name_words("Brown v. Ent. Merchs. Ass'n"),
                         ["brown", "ent", "merchs", "assn"])

    def test_a_volume_not_yet_paginated(self):
        self.assertEqual(oyez._parse_us_citation("602 U.S. ___"), ("602", ""))
        self.assertEqual(oyez._parse_us_citation("564 U.S. 786"),
                         ("564", "786"))
        self.assertIsNone(oyez._parse_us_citation("131 S. Ct. 2729"))


class _Block:
    """A line of an opinion's front matter."""

    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


class _Inline:
    """A thread that runs its target at start()."""

    def __init__(self, target=None, daemon=None):
        self.target = target

    def start(self):
        self.target()


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class DetailsPaneTests(unittest.TestCase):
    """The case details pane asks Oyez with the docket numbers too."""

    def _asked(self, item=None, front=()):
        win = object.__new__(gui._ScholarTextWindow)
        win._bb = {"cite": "564 U.S. 786", "year": "2011",
                   "name": "Brown v. Ent. Merchs. Ass'n"}
        win._is_scotus = True
        win._header_cites = ["564 U.S. 786", "131 S. Ct. 2729"]
        win._item = item or {}
        win._blocks = [_Block(t) for t in front]
        win._app = None
        win._set_details = mock.Mock()
        win._post = lambda fn, *args: None
        win._details_lines_parts = lambda: []
        with mock.patch.object(gui.threading, "Thread", _Inline), \
                mock.patch.object(gui.oyez, "lookup",
                                  return_value=None) as lookup:
            win._load_details()
        return lookup.call_args.kwargs

    def test_the_search_results_docket(self):
        asked = self._asked(item={"docketNumber": "08-1448"})
        self.assertEqual(asked["dockets"], ["08-1448"])
        self.assertEqual(asked["name"], "Brown v. Ent. Merchs. Ass'n")

    def test_else_the_docket_the_opinion_prints(self):
        asked = self._asked(front=[
            "BROWN, GOVERNOR OF CALIFORNIA, et al. v. ENTERTAINMENT "
            "MERCHANTS ASSOCIATION et al.",
            "No. 08-1448.",
            "Argued November 2, 2010—Decided June 27, 2011"])
        self.assertEqual(asked["dockets"], ["08-1448"])


if __name__ == "__main__":
    unittest.main()
