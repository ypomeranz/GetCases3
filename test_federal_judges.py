"""The surname a federal judge goes by, from the Federal Judicial Center's
records (federal_judges.tsv.gz).

Wise v. Circosta, 978 F.3d 93 (4th Cir. 2020) (en banc), prints "DIANA
GRIBBON MOTZ, Circuit Judge, concurring"; she was cited (Gribbon Motz, J.,
concurring) — Gribbon is her maiden name.  Neither the last word of a name
nor the words after its given names finds every surname.
"""

import unittest

import federal_judges


class SurnameTests(unittest.TestCase):

    def test_a_middle_name_is_no_part_of_it(self):
        for name, surname in (
                ("Diana Gribbon Motz", "Motz"),
                ("Kim McLane Wardlaw", "Wardlaw"),
                ("Ellen Lipton Hollander", "Hollander"),
                ("Maria Araújo Kahn", "Kahn"),
                ("Ketanji Brown Jackson", "Jackson")):
            with self.subTest(name=name):
                self.assertEqual(federal_judges.surname(name.split()),
                                 surname)

    def test_a_surname_of_two_words_keeps_both(self):
        for name, surname in (
                ("Pedro A. Delgado Hernández", "Delgado Hernández"),
                ("Franklin S. Van Antwerpen", "Van Antwerpen"),
                ("Amy J. St. Eve", "St. Eve"),
                ("Laurie Smith Camp", "Smith Camp"),
                # Written as the FJC writes it, where only the capitals
                # differ.
                ("Ana De Alba", "de Alba")):
            with self.subTest(name=name):
                self.assertEqual(federal_judges.surname(name.split()),
                                 surname)

    def test_an_initial_or_middle_name_for_the_first(self):
        # James Harvie Wilkinson III, who signs "J. Harvie Wilkinson III".
        self.assertEqual(
            federal_judges.surname("J. Harvie Wilkinson III".split()),
            "Wilkinson")
        self.assertEqual(
            federal_judges.surname("Harvie Wilkinson".split()), "Wilkinson")

    def test_only_a_judge_whose_first_name_fits(self):
        # No federal judge Motz is a Zelda; no judge at all is a Quillfeather.
        for name in ("Zelda Gribbon Motz", "Rosemary T. Quillfeather",
                     "Motz", ""):
            with self.subTest(name=name):
                self.assertEqual(federal_judges.surname(name.split()), "")

    def test_the_table_is_the_fjc_s(self):
        table = federal_judges._judges()
        self.assertGreater(sum(map(len, table.values())), 4000)
        self.assertIn(("Motz", "diana", ("jane", "gribbon")),
                      table["motz"])


if __name__ == "__main__":
    unittest.main()
