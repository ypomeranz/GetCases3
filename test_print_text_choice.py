"""Printing an opinion's text: the PDF now, or the LaTeX first, to edit.

Print on the text side typesets the opinion with LaTeX.  Where LaTeX is
installed, it now asks how first:

  * **Save as PDF…** — the PDF is typeset and saved, as before;
  * **Edit LaTeX First…** — the LaTeX source is saved and opened for editing,
    and a small window stays up whose **Make PDF** typesets the file *as
    edited* into a PDF of the same name beside it, as often as it is pressed.

Without LaTeX nothing is asked: the export says so and offers the source.
The Export menu's "LaTeX source — edit, then typeset" entry takes the same
edit-then-make-the-PDF path where LaTeX is installed.

The hover tips on the viewer's strip call their keys hotkeys.

The methods are lifted out of ``courtlistener_gui`` with ``ast`` (importing
it needs tkinter) and driven against stubs.
"""

import ast
import os
import pathlib
import re
import sys
import tempfile
import typing
import unittest
from unittest import mock


SRC = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text()
TREE = ast.parse(SRC)

TEX = "\\documentclass{article}\\begin{document}Opinion\\end{document}\n"
ENGINE = ("pdflatex", "/usr/bin/pdflatex")


class _Tk:
    TclError = Exception


def _class_methods(cls: str, names) -> dict:
    body = next(n.body for n in TREE.body
                if isinstance(n, ast.ClassDef) and n.name == cls)
    found = {n.name: ast.get_source_segment(SRC, n) for n in body
             if isinstance(n, ast.FunctionDef) and n.name in names}
    missing = [n for n in names if n not in found]
    if missing:
        raise AssertionError(f"not found on {cls}: {missing}")
    return found


def _function(name: str) -> str:
    return next(ast.get_source_segment(SRC, n) for n in TREE.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


READER_NAMES = [
    "_print_text", "_no_latex_found", "_export_pdf_latex",
    "_export_latex_source", "_write_latex_source",
    "_edit_latex_then_typeset", "_typeset_source", "_save_latex_source",
]
READER_SRC = _class_methods("_ScholarTextWindow", READER_NAMES)


class _Var:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class _Reader:
    """A _ScholarTextWindow with just what the export touches, and the module
    names those methods reach for stubbed in their namespace."""

    def __init__(self, folder: str, engine=ENGINE, choice=None):
        self.folder = folder
        self.find_calls = 0
        self.compiled = []
        self.opened = []
        self.edit_windows = []
        self.compile_result = (True, "")
        self.save_as = {".pdf": os.path.join(folder, "Case.pdf"),
                        ".tex": os.path.join(folder, "Case.tex")}
        self.messagebox = mock.Mock()
        self.messagebox.askyesno.return_value = False
        self.filedialog = mock.Mock()
        self.filedialog.asksaveasfilename.side_effect = (
            lambda **kw: self.save_as[kw["defaultextension"]])
        self.ask_print = mock.Mock(return_value=choice)
        self.open_error = None

        reader = self

        def find():
            reader.find_calls += 1
            return engine

        def compile_latex(tex, eng, out):
            reader.compiled.append((tex, eng, out))
            return reader.compile_result

        class GUI:
            @staticmethod
            def _open_file(path):
                if reader.open_error is not None:
                    raise reader.open_error
                reader.opened.append(path)

        ns = {
            "tk": _Tk, "os": os, "re": re, "sys": sys,
            "Optional": typing.Optional,
            "_find_latex_engine": find,
            "_compile_latex": compile_latex,
            "_ask_print_text": self.ask_print,
            "_latex_edit_window": lambda parent, path, make: (
                reader.edit_windows.append((parent, path, make))),
            "_build_default_filename": lambda item: "Case",
            "messagebox": self.messagebox,
            "filedialog": self.filedialog,
            "CourtListenerGUI": GUI,
        }
        for name in READER_NAMES:
            exec(READER_SRC[name], ns)
            setattr(self, name, ns[name].__get__(self))
        self._win = mock.Mock()
        self._status_var = _Var()

    def _with_full_view(self, build):
        return build()

    def _build_export_latex(self):
        return TEX

    def _filename_item(self):
        return {}


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = self._tmp.name

    def reader(self, **kw) -> _Reader:
        return _Reader(self.folder, **kw)


class NoLatexTests(_Fixture):
    def test_nothing_is_asked_but_whether_to_save_the_source(self):
        reader = self.reader(engine=None)
        reader._print_text()
        reader.ask_print.assert_not_called()
        title = reader.messagebox.askyesno.call_args[0][0]
        self.assertEqual(title, "LaTeX Not Found")

    def test_and_the_source_is_saved_if_wanted(self):
        reader = self.reader(engine=None)
        reader.messagebox.askyesno.side_effect = [True, False]
        reader._print_text()
        with open(reader.save_as[".tex"], encoding="utf-8") as f:
            self.assertEqual(f.read(), TEX)
        self.assertEqual(reader.compiled, [])

    def test_latex_is_looked_for_once(self):
        # The look can start a login shell per engine: not twice per press.
        reader = self.reader(engine=None)
        reader._print_text()
        self.assertEqual(reader.find_calls, 1)


class ChoiceTests(_Fixture):
    def test_with_latex_the_reader_is_asked_how(self):
        reader = self.reader()
        reader._print_text()
        reader.ask_print.assert_called_once_with(reader._win)

    def test_put_away_nothing_happens(self):
        reader = self.reader(choice=None)
        reader._print_text()
        reader.filedialog.asksaveasfilename.assert_not_called()
        self.assertEqual(reader.compiled, [])

    def test_save_as_pdf_typesets_it_now(self):
        reader = self.reader(choice="pdf")
        reader._print_text()
        self.assertEqual(reader.compiled,
                         [(TEX, ENGINE, reader.save_as[".pdf"])])
        self.assertEqual(reader.find_calls, 1)
        self.assertEqual(reader.edit_windows, [])

    def test_edit_latex_first_saves_the_source_and_opens_it(self):
        reader = self.reader(choice="tex")
        reader._print_text()
        tex_path = reader.save_as[".tex"]
        with open(tex_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), TEX)
        self.assertEqual(reader.opened, [tex_path])
        self.assertEqual(reader.compiled, [])     # not yet: it is being edited

    def test_and_leaves_a_window_up_to_make_the_pdf_from(self):
        reader = self.reader(choice="tex")
        reader._print_text()
        parent, path, _make = reader.edit_windows[0]
        self.assertIs(parent, reader._win)
        self.assertEqual(path, reader.save_as[".tex"])

    def test_with_no_editor_for_tex_files_the_window_still_comes(self):
        reader = self.reader(choice="tex")
        reader.open_error = OSError("No application is associated")
        reader._print_text()
        self.assertEqual(len(reader.edit_windows), 1)


class MakePdfTests(_Fixture):
    def setUp(self):
        super().setUp()
        self.r = self.reader(choice="tex")
        self.r._print_text()
        self.tex_path = self.r.save_as[".tex"]
        self.make_pdf = self.r.edit_windows[0][2]

    def _edit(self, text: str, encoding: str = "utf-8"):
        with open(self.tex_path, "w", encoding=encoding) as f:
            f.write(text)

    def test_the_pdf_is_made_from_the_source_as_edited(self):
        self._edit(TEX.replace("Opinion", "Opinion, as edited"))
        self.make_pdf()
        tex, engine, out = self.r.compiled[0]
        self.assertIn("as edited", tex)
        self.assertEqual(engine, ENGINE)

    def test_beside_it_under_the_same_name(self):
        self.make_pdf()
        self.assertEqual(self.r.compiled[0][2],
                         os.path.join(self.folder, "Case.pdf"))

    def test_as_often_as_it_is_asked_for(self):
        self.make_pdf()
        self._edit(TEX.replace("Opinion", "Second draft"))
        self.make_pdf()
        self.assertEqual(len(self.r.compiled), 2)
        self.assertIn("Second draft", self.r.compiled[1][0])

    def test_and_offered_to_open(self):
        self.r.messagebox.askyesno.return_value = True
        self.make_pdf()
        self.assertEqual(self.r.opened[-1],
                         os.path.join(self.folder, "Case.pdf"))

    def test_a_source_that_does_not_build_says_why(self):
        self.r.compile_result = (False, "! Undefined control sequence.")
        self.make_pdf()
        message = self.r.messagebox.showerror.call_args[0][1]
        self.assertIn("Undefined control sequence", message)
        self.assertIn(self.tex_path, message)
        self.assertEqual(self.r._status_var.get(), "PDF export failed.")

    def test_a_source_saved_in_another_encoding_is_refused_plainly(self):
        self._edit("\\section{Caf\u00e9}\n", encoding="utf-16")
        self.make_pdf()
        self.assertEqual(self.r.compiled, [])
        self.assertIn("UTF-8", self.r.messagebox.showerror.call_args[0][1])


class ExportMenuTests(_Fixture):
    def test_the_tex_entry_edits_then_typesets_where_latex_is(self):
        reader = self.reader()
        reader._export_latex_source()
        self.assertEqual(len(reader.edit_windows), 1)
        reader.ask_print.assert_not_called()   # the entry was the choice

    def test_and_saves_the_source_alone_where_it_is_not(self):
        reader = self.reader(engine=None)
        reader._export_latex_source()
        self.assertEqual(reader.edit_windows, [])
        self.assertTrue(os.path.exists(reader.save_as[".tex"]))

    def test_the_pdf_entry_asks_nothing(self):
        reader = self.reader()
        reader._export_pdf_latex()
        reader.ask_print.assert_not_called()
        self.assertEqual(len(reader.compiled), 1)


class ViewerPrintTests(unittest.TestCase):
    def test_print_on_the_text_is_the_opinion_s_own_print(self):
        src = _class_methods("_FloatingPdfWindow", ["_print"])["_print"]
        self.assertIn("self._reader._print_text()", src)


class DialogTests(unittest.TestCase):
    """The dialogs themselves need Tk; their wiring is read from the source."""

    def test_the_choice_names_both_ways(self):
        src = _function("_ask_print_text")
        self.assertIn('"Save as PDF…"', src)
        self.assertIn('"Edit LaTeX First…"', src)
        self.assertIn('pick("pdf")', src)
        self.assertIn('pick("tex")', src)

    def test_the_edit_window_makes_the_pdf_and_reopens_the_source(self):
        src = _function("_latex_edit_window")
        self.assertIn('"Make PDF", command=make_pdf', src)
        self.assertIn("_open_file(tex_path)", src)
        self.assertNotIn("grab_set", src)      # the editor stays usable


# ---------------------------------------------------------------------------
# Hotkeys named as hotkeys on the strip's hover tips
# ---------------------------------------------------------------------------


def _hotkey_tip():
    ns = {}
    exec(_function("_hotkey_tip"), ns)
    return ns["_hotkey_tip"]


class HotkeyTipTests(unittest.TestCase):
    def setUp(self):
        self.tip = _hotkey_tip()

    def test_a_lone_letter_is_called_a_hotkey(self):
        self.assertEqual(self.tip("Case Details   s"),
                         "Case Details (hotkey: s)")

    def test_so_is_a_chord(self):
        self.assertEqual(self.tip("Save PDF As…   Ctrl+S"),
                         "Save PDF As… (hotkey: Ctrl+S)")
        self.assertEqual(self.tip("Print — typeset with LaTeX…   Ctrl+P"),
                         "Print — typeset with LaTeX… (hotkey: Ctrl+P)")

    def test_a_label_without_a_key_is_left_alone(self):
        self.assertEqual(self.tip("History"), "History")
        self.assertEqual(self.tip("Looking for the scanned pages…"),
                         "Looking for the scanned pages…")

    def test_the_strip_s_tips_go_through_it(self):
        src = _class_methods("_FloatingPdfWindow", ["_build_bar"])["_build_bar"]
        for label in ("_details_label", "_save_label", "_print_label"):
            self.assertIn(f"_hotkey_tip(self.{label}())", src)

    def test_while_the_right_click_menu_keeps_its_accelerator_column(self):
        src = _class_methods("_FloatingPdfWindow",
                             ["_post_bar_menu"])["_post_bar_menu"]
        self.assertIn('self._save_label().replace("   ", "\\t")', src)
        self.assertNotIn("_hotkey_tip", src)


if __name__ == "__main__":
    unittest.main()
