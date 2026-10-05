"""A citation typed by hand, however its reporter is spelled.

"486 us 1306" — Morison v. United States, 486 U.S. 1306 (1988) (Rehnquist,
J., in chambers), whose only copy is its scan — was no citation at all to
Spotlight, which searched for it as words and found nothing; "486 US 1306"
was one, but not the U.S. Reports', so its official scan went unasked.
"""

import unittest

from citations import typed_reporter

try:
    import courtlistener_gui as gui
except ImportError:  # pragma: no cover - exercised on a bare checkout
    gui = None


class TypedReporterTests(unittest.TestCase):

    def test_a_reporter_by_its_letters_and_digits(self):
        for typed, proper in (("us", "U.S."), ("US", "U.S."),
                              ("u.s.", "U.S."), ("s ct", "S. Ct."),
                              ("f4th", "F.4th"), ("f 3d", "F.3d"),
                              ("l ed 2d", "L. Ed. 2d"),
                              ("fed appx", "F. App'x"), ("ne2d", "N.E.2d"),
                              ("cal rptr 3d", "Cal. Rptr. 3d"),
                              ("wash 2d", "Wash. 2d")):
            with self.subTest(typed=typed):
                self.assertEqual(typed_reporter(typed, loose=True), proper)

    def test_a_word_is_no_reporter_in_lowercase(self):
        for word in ("days", "a", "p", "or", "me", "of"):
            with self.subTest(word=word):
                self.assertEqual(typed_reporter(word, loose=True), "")
        # Capitalized, a state's reporter all the same.
        self.assertEqual(typed_reporter("Or"), "Or.")


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class TypedCitationLineTests(unittest.TestCase):

    def test_however_typed_it_is_the_u_s_reports(self):
        for typed in ("486 us 1306", "486 US 1306", "486 u.s. 1306",
                      "486 U.S. 1306"):
            with self.subTest(typed=typed):
                self.assertEqual(gui._parse_citation_line(typed),
                                 ("", "486 U.S. 1306", ""))

    def test_with_its_name_pin_and_year(self):
        line = "morison v us, 486 us 1306, 1308 (1988)"
        self.assertEqual(gui._parse_citation_line(line),
                         ("morison v us", "486 U.S. 1306", "1308"))
        self.assertEqual(gui._citation_line_year(line), "1988")

    def test_words_and_numbers_stay_a_search(self):
        for query in ("100 days 5", "5 or 6", "1 a 2", "section 5 of 6"):
            with self.subTest(query=query):
                self.assertIsNone(gui._parse_citation_line(query))


if __name__ == "__main__":
    unittest.main()
