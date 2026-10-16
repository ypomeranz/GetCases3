"""The case citation for a textual sentence (Copy > Copy citation for use in
text): rule 10.2.1(c) abbreviates only widely known acronyms and eight words
there — "&", "Ass'n", "Bros.", "Co.", "Corp.", "Inc.", "Ltd.", "No." — and
the sentence goes on after it, so no period closes it."""

import os
import unittest
from unittest.mock import Mock, patch

os.environ["GETCASES_SKIP_DEPENDENCY_PROMPT"] = "1"

from bluebook_names import abbreviate_case_name
from courtlistener_gui import _ScholarTextWindow
from google_scholar import Block, Span


def textual(name, **kw):
    return abbreviate_case_name(name, textual=True, **kw)


class TextualNameTests(unittest.TestCase):
    def test_only_the_eight_words(self):
        self.assertEqual(textual("Guaranty Trust Company of New York v. York"),
                         "Guaranty Trust Co. of New York v. York")
        self.assertEqual(
            textual("Department of Homeland Security v. Regents of the "
                    "University of California"),
            "Department of Homeland Security v. Regents of the University "
            "of California")
        self.assertEqual(textual("Chicago, Burlington and Quincy Railroad "
                                 "Company v. Chicago"),
                         "Chicago, Burlington & Quincy Railroad Co. v. Chicago")
        # T6 abbreviates these; the rule's eight words do not include them.
        self.assertEqual(textual("Smith Brothers Corporate Companies v. Doe"),
                         "Smith Bros. Corporate Companies v. Doe")

    def test_widely_known_acronyms(self):
        self.assertEqual(
            textual("National Labor Relations Board v. Jones and Laughlin "
                    "Steel Corporation"),
            "NLRB v. Jones & Laughlin Steel Corp.")
        self.assertEqual(
            textual("Natural Resources Defense Council, Inc. v. United States "
                    "Environmental Protection Agency"),
            "Natural Resources Defense Council, Inc. v. EPA")

    def test_the_rest_of_rule_10_2_1_still_applies(self):
        self.assertEqual(textual("Oliver Brown et al. v. Board of Education "
                                 "of Topeka"),
                         "Brown v. Board of Education")
        self.assertEqual(textual("People of the State of New York v. "
                                 "Zackowitz", court_state="new york"),
                         "People v. Zackowitz")

    def test_the_citation_form_is_unchanged(self):
        self.assertEqual(
            abbreviate_case_name("Guaranty Trust Company of New York v. York"),
            "Guar. Tr. Co. of N.Y. v. York")


class TextViewTests(unittest.TestCase):
    def setUp(self):
        for target, value in (("case_name_usage.remembered_form", None),
                              ("decision_year.remembered_year", "")):
            p = patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _window(cite_line, caption, cites=()):
        win = object.__new__(_ScholarTextWindow)
        win._item = {}
        win._header_cites = list(cites)
        win._decided_en_banc = lambda: False
        win._blocks = [
            Block("center", [Span(cite_line)]),
            Block("center", [Span(caption)]),
            Block("center", [Span("Supreme Court of United States.")]),
            Block("para", [Span("MR. JUSTICE FRANKFURTER delivered the "
                                "opinion of the Court.")]),
        ]
        win._bb = win._compute_bluebook_parts()
        return win

    def test_guaranty_trust(self):
        win = self._window("326 U.S. 99 (1945)", "GUARANTY TRUST CO. v. YORK.")
        self.assertEqual(win._bb["name"], "Guar. Tr. Co. v. York")
        plain, rtf = win._bluebook_citation(None, inline=True, textual=True)
        self.assertEqual(plain, "Guaranty Trust Co. v. York, 326 U.S. 99 (1945)")
        self.assertIn("{\\i Guaranty Trust Co. v. York}", rtf)
        self.assertFalse(rtf.rstrip().endswith("."))
        # The citation itself is as it was.
        self.assertEqual(win._bluebook_citation(None, inline=True)[0],
                         "Guar. Tr. Co. v. York, 326 U.S. 99 (1945).")

    def test_the_name_the_citation_settled(self):
        # Murray's Lessee, by its claimant's lessee as the courts cite it.
        win = self._window(
            "59 U.S. 272 (1855)",
            "JOHN DEN, ex dem. JAMES B. MURRAY AND JOHN C. KAYSER, "
            "PLAINTIFFS, v. THE HOBOKEN LAND AND IMPROVEMENT COMPANY.")
        self.assertEqual(win._text_case_name(),
                         "Murray's Lessee v. Hoboken Land & Improvement Co.")
        # A group of cases by the group's name.
        win = self._window("109 U.S. 3 (1883)",
                           "UNITED STATES v. STANLEY. UNITED STATES v. RYAN.")
        self.assertEqual(win._text_case_name(), "Civil Rights Cases")

    def test_an_edited_citation_is_the_users(self):
        win = self._window("326 U.S. 99 (1945)", "GUARANTY TRUST CO. v. YORK.")
        win._base_citation_override = "Guaranty Trust v. York, 326 U.S. 99 (1945)"
        plain, _rtf = win._bluebook_citation(None, inline=True, textual=True)
        self.assertEqual(plain, "Guaranty Trust v. York, 326 U.S. 99 (1945)")

    def test_the_copy_command(self):
        win = self._window("326 U.S. 99 (1945)", "GUARANTY TRUST CO. v. YORK.")
        win._win = Mock()
        win._status_var = Mock()
        win._show_copy_preview = Mock()
        with patch("courtlistener_gui._copy_rich_clipboard",
                   return_value="formatted text") as copy:
            win._copy_text_citation_to_clipboard()
        rtf, plain = copy.call_args.args[1:]
        self.assertEqual(plain, "Guaranty Trust Co. v. York, 326 U.S. 99 (1945)")
        self.assertIn("{\\i Guaranty Trust Co. v. York}", rtf)


if __name__ == "__main__":
    unittest.main()
