"""A section or paragraph sign stays on the line with its number.

The space between "§" or "¶" and the number after it — "42 U.S.C. § 1983",
"29 C.F.R. §§ 1614.105-.106", "Compl. ¶ 12" — is a non-breaking one in every
parsed opinion, so the reader's text view never leaves a bare "§" or "¶" at
the end of a line.  The
exports keep it non-breaking (RTF "\\~", LaTeX "~"), and the find bar finds
it when the space is typed as an ordinary one.
"""

import unittest

import case_law_parse
import cl_parse
from google_scholar import (
    Block, NBSP, Span, bind_block_signs_to_numbers, bind_signs_to_numbers,
    parse_opinion_blocks,
)

try:
    import tkinter as tk

    import courtlistener_gui as gui
except ImportError:  # pragma: no cover - exercised on a bare checkout
    gui = None


class BindSectionSignsTests(unittest.TestCase):

    def test_the_space_before_the_number_binds(self):
        for text, bound in (
                ("42 U.S.C. § 1983", "42 U.S.C. §" + NBSP + "1983"),
                ("§§ 1981-1983", "§§" + NBSP + "1981-1983"),
                ("Cal. Penal Code § 187(a)", "Cal. Penal Code §" + NBSP
                 + "187(a)"),
                ("§  12", "§" + NBSP + NBSP + "12"),
                ("Compl. ¶ 12", "Compl. ¶" + NBSP + "12"),
                ("¶¶ 4-7", "¶¶" + NBSP + "4-7"),
                ("§ 5, ¶ 3", "§" + NBSP + "5, ¶" + NBSP + "3")):
            with self.subTest(text=text):
                self.assertEqual(bind_signs_to_numbers(text), bound)

    def test_only_before_a_number(self):
        for text in ("art. I, § III", "§1983", "the § sign", "§ (a)",
                     "¶ A", "the ¶ mark"):
            with self.subTest(text=text):
                self.assertEqual(bind_signs_to_numbers(text), text)

    def test_across_spans(self):
        # "§ " closes the text and the number opens Scholar's link.
        block = Block(spans=[Span("42 U.S.C. § "), Span("1983", link="x"),
                             Span(" applies.")])
        bind_block_signs_to_numbers(block)
        self.assertEqual([s.text for s in block.spans],
                         ["42 U.S.C. §" + NBSP, "1983", " applies."])


class ParsersTests(unittest.TestCase):

    def test_scholar(self):
        blocks = parse_opinion_blocks(
            '<div id="gs_opinion"><p>Under 42 U.S.C. <a href="/scholar_case'
            '?case=1">§ 1983</a> and §§ 1981-1982.</p></div>')
        self.assertEqual(blocks[0].text(),
                         "Under 42 U.S.C. §" + NBSP + "1983 and §§" + NBSP
                         + "1981-1982.")

    def test_courtlistener(self):
        blocks, _notes = cl_parse.parse_cl_html(
            "<p>See § 1983 and Compl. ¶ 12.</p>")
        self.assertEqual(blocks[0].text(),
                         "See §" + NBSP + "1983 and Compl. ¶" + NBSP + "12.")

    def test_case_law(self):
        parts, _body = case_law_parse.parse_case_law_html(
            '<section class="casebody"><article class="opinion" '
            'data-type="majority"><p>Code § 187 applies.</p></article>'
            '</section>')
        self.assertEqual(parts[0].blocks[0].text(),
                         "Code §" + NBSP + "187 applies.")


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class ExportAndFindTests(unittest.TestCase):

    def test_rtf_writes_its_own_non_breaking_space(self):
        self.assertEqual(gui._rtf_escape("§" + NBSP + "1983"),
                         "\\u167?\\~1983")

    def test_latex_too(self):
        self.assertEqual(gui._latex_escape("§" + NBSP + "1983"),
                         "\\S{}~1983")

    def test_a_saved_copy_is_bound_when_reopened(self):
        block = gui._block_from_json(
            {"kind": "para", "spans": [{"text": "See § 1983."}]})
        self.assertEqual(block.text(), "See §" + NBSP + "1983.")

    def test_a_typed_space_finds_a_non_breaking_one(self):
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        try:
            root.withdraw()
            txt = tk.Text(root)
            txt.insert("1.0", "See 42 U.S.C. §" + NBSP + "1983 (a). And § 1983.")
            found = []
            n = tk.IntVar()
            idx = "1.0"
            pattern = gui._find_pattern("u.s.c. § 1983 (a)")
            while True:
                idx = txt.search(pattern, idx, stopindex="end", nocase=True,
                                 count=n, regexp=True)
                if not idx or not n.get():
                    break
                found.append(txt.get(idx, f"{idx}+{n.get()}c"))
                idx = f"{idx}+{n.get()}c"
            self.assertEqual(found, ["U.S.C. §" + NBSP + "1983 (a)"])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
