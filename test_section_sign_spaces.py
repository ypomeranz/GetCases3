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


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class MacReaderTextTests(unittest.TestCase):
    """On a Mac the reader's widget holds an ordinary space instead.

    Tk on macOS trims a non-breaking space off the end of every run of text
    it measures (tkMacOSXFont.c), and a run ends at any tag change or mark —
    Tk's own "current" and "insert" marks among them.  A run ending in the
    space after a "§" made its line end before the "§" and the next line
    end after it, the "§" alone on a line: in an opinion already open, as
    the pointer passed over the number or a click landed by it."""

    BOUND = "Under 42 U.S.C. §" + NBSP + "1983 and §§" + NBSP + "1981-1982."

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def text(self, mac: bool = True):
        txt = gui._ReaderText(self.root)
        txt._PLAIN_SPACES = mac
        return txt

    @staticmethod
    def held(txt) -> str:
        """What the widget itself holds, as Tk lays it out."""
        return txt.tk.call(txt._w, "get", "1.0", "end-1c")

    def test_the_widget_holds_an_ordinary_space(self):
        txt = self.text()
        txt.insert("1.0", self.BOUND)
        self.assertNotIn(NBSP, self.held(txt))
        self.assertEqual(self.held(txt), self.BOUND.replace(NBSP, " "))

    def test_in_every_piece_of_a_tagged_insert(self):
        txt = self.text()
        txt.insert("end", "42 U.S.C. §" + NBSP, ("plain",),
                   "1983", ("citelink", "lnk1"), " and ¶" + NBSP + "12", ())
        self.assertNotIn(NBSP, self.held(txt))
        self.assertEqual(txt.tag_ranges("citelink")[0].string, "1.12")

    def test_read_back_it_is_bound_again(self):
        txt = self.text()
        txt.insert("1.0", self.BOUND)
        self.assertEqual(txt.get("1.0", "end-1c"), self.BOUND)

    def test_one_for_one_so_every_offset_holds(self):
        mac, other = self.text(), self.text(mac=False)
        for txt in (mac, other):
            txt.insert("1.0", self.BOUND)
        self.assertEqual(mac.index("end-1c"), other.index("end-1c"))
        self.assertEqual(mac.search("1983", "1.0"), other.search("1983", "1.0"))

    def test_across_a_tag_change_as_well(self):
        # "§ " closing one run and the number opening a link: the very case
        # whose run ended in the space.
        txt = self.text()
        txt.insert("end", "42 U.S.C. §" + NBSP, (), "1983", ("citelink",),
                   " applies.", ())
        pieces = [value for key, value, _i in
                  txt.dump("1.0", "end-1c", text=True, tag=True)
                  if key == "text"]
        self.assertEqual(pieces, ["42 U.S.C. §" + NBSP, "1983", " applies."])

    def test_so_copies_and_exports_keep_it(self):
        txt = self.text()
        txt.insert("end", "Under §" + NBSP, (), "1983", ("citelink",), ".", ())
        plain = gui._plain_without_layout_chars(txt, "1.0", "end-1c")
        self.assertEqual(plain, "Under §" + NBSP + "1983.")
        paras = gui._dump_export_paragraphs(txt, [("1.0", "end-1c")], {})
        self.assertEqual("".join(r.text for r in paras[0].runs), plain)
        self.assertIn("\\~1983", gui._rtf_escape(plain))

    def test_a_typed_space_still_finds_it(self):
        txt = self.text()
        txt.insert("1.0", self.BOUND)
        n = tk.IntVar()
        idx = txt.search(gui._find_pattern("§ 1983"), "1.0", stopindex="end",
                         regexp=True, count=n)
        self.assertEqual(txt.get(idx, f"{idx}+{n.get()}c"), "§" + NBSP + "1983")

    def test_elsewhere_the_widget_keeps_the_non_breaking_space(self):
        txt = self.text(mac=False)
        txt.insert("1.0", self.BOUND)
        self.assertEqual(self.held(txt), self.BOUND)

    def test_only_on_a_mac(self):
        import sys
        self.assertEqual(gui._ReaderText._PLAIN_SPACES,
                         sys.platform == "darwin")


if __name__ == "__main__":
    unittest.main()
