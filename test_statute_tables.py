"""Tables in the U.S. Code and the C.F.R.

A table reaches the reader a row at a time, each row a paragraph of a kind of
its own with its cells separated by tabs, and the statute viewer lines the
cells up, at any window size.  The rate tables of 26 U.S.C. § 1 and the limits of 40 C.F.R.
§ 261.24 were left out altogether before: both parsers read only paragraphs.

Some of the OLRC's tables are not tables on its page: 26 U.S.C. § 1(a) and
7 U.S.C. § 1441 run theirs into the paragraph before, cells and all ("Not over
$36,90015% of taxable income.").  Those are read back into rows, and split
where a dollar figure shows the place or Cornell's copy of the same row does.

The fixtures are the OLRC's and eCFR's own markup, cut down (November 2026).
Nothing here touches the network.

Run with:  python -m unittest test_statute_tables -v
"""

import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import courtlistener_gui
import ecfr
import us_code
from us_code import (
    TABLE_KINDS,
    UscSection,
    _flat_table,
    _mend_tables,
    parse_section,
    statute_paths,
    table_rows,
)

RATES = (
    '<table class="usc"><tr><th class="left">If taxable income is:</th>'
    '<th class="right">The tax is:</th></tr>'
    '<tr><td class="left">Not over $29,600</td>'
    '<td class="right">15% of taxable income.</td></tr>'
    '<tr><td class="left">Over $29,600 but not over $76,400</td>'
    '<td class="right">$4,440, plus 28% of the excess over $29,600.</td></tr>'
    "</table>"
)

# 26 U.S.C. § 1(a), as the page has it: no table, a row a line.
FLAT_RATES = (
    '<p class="statutory-body">a tax determined in accordance with the '
    "following table:\n\n\n\n\n\n\n<b>If taxable income is:</b><b>The tax is:"
    "</b>\n\n\nNot over $36,90015% of taxable income.\n"
    "Over $36,900 but not over $89,150$5,535, plus 28% of the excess over "
    "$36,900.\n"
    "Over $250,000$75,528.50, plus 39.6% of the excess over $250,000.\n\n\n"
    "</p>"
)

# 7 U.S.C. § 1441: no headings in <b>s, and no dollar sign to show the place.
FLAT_SUPPORT = (
    '<p class="statutory-body">as follows:\n\n\n\n\n\n\n'
    "(a) For corn, if the supply percentage is:The level of support shall be "
    "not less than:\n\n\nNot more than 10290\n"
    "More than 102 but not more than 10489\n</p>"
)


def statute(*paras: str) -> str:
    return ('<div class="statutory-body-container">' + "".join(paras)
            + "</div>")


def p(cls: str, text: str) -> str:
    return f'<p class="{cls}">{text}</p>'


def note(*inside: str) -> str:
    return ('<div class="note-block" id="x-note">'
            '<h4 class="note-head legacy-note-style">Revision Notes</h4>'
            + "".join(inside) + "</div>")


class ReadingTests(unittest.TestCase):
    def test_a_table_is_read_a_row_at_a_time(self):
        page = statute(p("statutory-body", "the following table:"), RATES)
        self.assertEqual(parse_section(page), [
            ("body", 0, "the following table:"),
            ("table-head", 0, "If taxable income is:\tThe tax is:"),
            ("table", 0, "Not over $29,600\t15% of taxable income."),
            ("table", 0, "Over $29,600 but not over $76,400\t"
                         "$4,440, plus 28% of the excess over $29,600."),
        ])

    def test_it_is_set_where_the_text_before_it_is(self):
        page = statute(p("statutory-body-3em", "(ii) as follows:"), RATES)
        self.assertEqual({depth for _k, depth, _t in parse_section(page)}, {3})

    def test_it_belongs_to_the_subdivision_it_stands_in(self):
        # 26 U.S.C. § 7701(b)(3)(A)(ii): what a pin cite and Copy + Cite see.
        page = statute(p("statutory-body", "(b) Definition"),
                       p("statutory-body-1em", "(3) Substantial presence"),
                       RATES,
                       p("statutory-body-1em", "(4) First year"))
        paras = parse_section(page)
        self.assertEqual(statute_paths(paras), [
            ("b",), ("b", "3"), ("b", "3"), ("b", "3"), ("b", "3"),
            ("b", "4"),
        ])

    def test_a_cell_opening_like_an_item_is_not_one(self):
        rows = ('<table class="usc"><tr><td>(2) Over $1</td><td>5%</td></tr>'
                "</table>")
        page = statute(p("statutory-body", "(a) First"), rows,
                       p("statutory-body", "(b) Second"))
        self.assertEqual(statute_paths(parse_section(page)),
                         [("a",), ("a",), ("b",)])

    def test_in_a_note_it_goes_with_the_notes(self):
        table = ('<table class="hnr-table"><tr class="header"><th><p><i>Revised'
                 " section</i></p></th><th><p><i>Source</i></p></th></tr><tr>"
                 '<td><p class="leaders"><span>801</span></p></td><td><p>50:551'
                 "</p></td></tr></table>")
        page = (statute(p("statutory-body", "(a) Text"))
                + '<p class="source-credit">(Pub. L. 1.)</p>' + note(table))
        paras = parse_section(page)
        self.assertEqual(paras[-2:], [
            ("note-table-head", 0, "Revised section\tSource"),
            ("note-table", 0, "801\t50:551"),
        ])
        self.assertTrue(all(kind.startswith("note") for kind, _d, _t
                            in paras[-2:]))
        self.assertEqual(statute_paths(paras)[-2:], [(), ()])

    def test_a_cells_paragraphs_are_read_once(self):
        page = note('<table class="quoted-table"><tr><td>'
                    '<p class="leaders"><span>7</span></p></td><td><p>209</p>'
                    "</td></tr></table>")
        self.assertEqual([para for para in parse_section(page)
                          if para[0] != "note-head"],
                         [("note-table", 0, "7\t209")])


class GridTests(unittest.TestCase):
    """Each cell stands under its heading, whatever spans what."""

    @staticmethod
    def rows(*trs: str, caption: str = "") -> list[tuple[bool, str]]:
        return table_rows("<table>" + caption + "".join(trs) + "</table>")

    def test_a_heading_is_a_row_of_th(self):
        self.assertEqual(self.rows("<tr><th>A</th><th>B</th></tr>",
                                   "<tr><th>Row</th><td>1</td></tr>"),
                         [(True, "A\tB"), (False, "Row\t1")])

    def test_a_caption_heads_the_table(self):
        self.assertEqual(
            self.rows("<tr><td>x</td></tr>", caption='<caption><p class="title">'
                      "Table Z-1—Limits</p></caption>"),
            [(True, "Table Z-1—Limits"), (False, "x")])

    def test_a_cell_spanning_columns(self):
        self.assertEqual(
            self.rows('<tr><th colspan="2">Rates</th><th>Year</th></tr>',
                      "<tr><td>a</td><td>b</td><td>c</td></tr>"),
            [(True, "Rates\t\tYear"), (False, "a\tb\tc")])

    def test_a_cell_spanning_rows(self):
        # 49 C.F.R. § 172.101: what follows the spanned cell stays in column.
        self.assertEqual(
            self.rows('<tr><td rowspan="2">A</td><td>b</td><td>c</td></tr>',
                      "<tr><td>d</td><td>e</td></tr>",
                      "<tr><td>f</td><td>g</td><td>h</td></tr>"),
            [(False, "A\tb\tc"), (False, "\td\te"), (False, "f\tg\th")])

    def test_a_cell_spanning_rows_in_the_last_column(self):
        self.assertEqual(
            self.rows('<tr><td>a</td><td rowspan="2">B</td></tr>',
                      "<tr><td>c</td></tr>"),
            [(False, "a\tB"), (False, "c")])

    def test_a_cell_spanning_both(self):
        self.assertEqual(
            self.rows('<tr><td rowspan="2" colspan="2">A</td><td>b</td></tr>',
                      "<tr><td>c</td></tr>"),
            [(False, "A\t\tb"), (False, "\t\tc")])

    def test_a_row_with_nothing_in_it_is_left_out(self):
        self.assertEqual(self.rows("<tr><td> </td><td></td></tr>",
                                   "<tr><td>x</td></tr>"), [(False, "x")])

    def test_an_empty_cell_keeps_its_place(self):
        self.assertEqual(self.rows("<tr><td></td><td>b</td></tr>"),
                         [(False, "\tb")])

    def test_lines_in_a_cell_are_kept_apart(self):
        self.assertEqual(
            self.rows("<tr><td><p>Revised Statutes and</p><p>Statutes at Large"
                      "</p></td><td>a<br/>b</td></tr>"),
            [(False, "Revised Statutes and Statutes at Large\ta b")])

    def test_raised_and_lowered_figures_stay_off_the_text(self):
        # 29 C.F.R. § 1910.1000: "mg/m3" is not a figure of 3.
        self.assertEqual(
            self.rows("<tr><td>mg/m<sup>3</sup></td><td>CO<sub>2</sub></td>"
                      "<td>No. <sup>12</sup></td></tr>"),
            [(False, "mg/m³\tCO₂\tNo. ¹²")])

    def test_a_footnote_mark_in_a_cell(self):
        self.assertEqual(
            self.rows('<tr><td>$500<sup><a href="#fn1">1</a></sup></td></tr>'),
            [(False, "$500[1]")])


class FlatTableTests(unittest.TestCase):
    def test_a_table_run_into_its_paragraph(self):
        page = statute(p("statutory-body", "(a) Married"), FLAT_RATES,
                       p("statutory-body", "(b) Heads of households"))
        self.assertEqual(parse_section(page), [
            ("body", 0, "(a) Married"),
            ("body", 0, "a tax determined in accordance with the following "
                        "table:"),
            ("table-head", 0, "If taxable income is:\tThe tax is:"),
            ("table", 0, "Not over $36,900\t15% of taxable income."),
            ("table", 0, "Over $36,900 but not over $89,150\t"
                         "$5,535, plus 28% of the excess over $36,900."),
            ("table", 0, "Over $250,000\t"
                         "$75,528.50, plus 39.6% of the excess over $250,000."),
            ("body", 0, "(b) Heads of households"),
        ])

    def test_no_figure_stays_run_together(self):
        for kind, _depth, text in parse_section(statute(FLAT_RATES)):
            self.assertNotIn("90015", text)
            self.assertNotIn("150$", text)

    def test_with_no_figure_to_show_the_place_a_row_stands_as_printed(self):
        paras = parse_section(statute(FLAT_SUPPORT))
        self.assertEqual(paras[1:], [
            ("table-head", 0, "(a) For corn, if the supply percentage is:The "
                              "level of support shall be not less than:"),
            ("table", 0, "Not more than 10290"),
            ("table", 0, "More than 102 but not more than 10489"),
        ])

    def test_a_paragraph_with_no_table_is_left_alone(self):
        for inner in ("Plain text", "One\nline break", "A form:\n\nSignature",
                      "<b>Bold</b> text", "<b>Two</b><b>bolds</b> in a line"):
            with self.subTest(inner=inner):
                self.assertEqual(_flat_table(inner), (inner, []))

    def test_text_in_a_row_with_two_figures_elsewhere_is_not_split(self):
        _lead, rows = _flat_table(
            "x\n\n\n\n\n\n\nA\n\n\nthe sum of $100 and $200 in all\n")
        self.assertEqual(rows[1], (False, "the sum of $100 and $200 in all"))


class MendTests(unittest.TestCase):
    """Cornell's copy gives the place to split a row, and nothing more."""

    PARAS = [("body", 0, "as follows:"),
             ("table-head", 0, "(a) For corn:The level of support:"),
             ("table", 0, "Not more than 10290")]

    @staticmethod
    def copy(*rows: str) -> UscSection:
        return UscSection("7", "1441", "u", source="lii", paras=[
            ("body", 0, row) for row in rows])

    def _mend(self, paras, copy):
        with patch.object(us_code, "_lii_section",
                          side_effect=copy if isinstance(copy, Exception)
                          else None, return_value=copy) as asked:
            return _mend_tables(paras, "7", "1441"), asked

    def test_a_row_reading_the_same_is_split_where_cornell_splits_it(self):
        mended, _asked = self._mend(self.PARAS, self.copy(
            "(a) For corn: | The level of support:",
            "Not more than 102 | 90"))
        self.assertEqual(mended, [
            ("body", 0, "as follows:"),
            ("table-head", 0, "(a) For corn:\tThe level of support:"),
            ("table", 0, "Not more than 102\t90"),
        ])

    def test_a_row_reading_otherwise_stands_as_the_olrc_prints_it(self):
        # Cornell's copy can trail the OLRC's.
        mended, _asked = self._mend(self.PARAS, self.copy(
            "Not more than 102 | 85"))
        self.assertEqual(mended, self.PARAS)

    def test_with_cornell_not_answering_it_stands_too(self):
        mended, _asked = self._mend(self.PARAS, RuntimeError("503"))
        self.assertEqual(mended, self.PARAS)

    def test_cornell_is_not_asked_when_every_row_is_split(self):
        paras = [("table-head", 0, "Caption"), ("table", 0, "a\tb")]
        mended, asked = self._mend(paras, self.copy())
        self.assertEqual(mended, paras)
        asked.assert_not_called()

    def test_nor_for_a_section_with_no_table(self):
        paras = [("body", 0, "Every person")]
        _mended, asked = self._mend(paras, self.copy())
        asked.assert_not_called()

    def test_a_section_is_read_mended(self):
        page = statute(FLAT_SUPPORT)
        copy = self.copy("Not more than 102 | 90")
        with patch.object(us_code, "_olrc_fetch", return_value=(page, "u/s1/")), \
                patch.object(us_code, "_title_contents",
                             side_effect=RuntimeError("no list")), \
                patch.object(us_code, "_lii_section", return_value=copy):
            doc = us_code._olrc_section("7", "1441")
        self.assertIn(("table", 0, "Not more than 102\t90"), doc.paras)
        self.assertEqual(doc.source, "olrc")


class EcfrTests(unittest.TestCase):
    # 40 C.F.R. § 261.24
    PAGE = """
    <p>Chrome before the section</p>
    <table><tr><td>Not the section's</td></tr></table>
    <div class="section" id="261.24">
      <h4>§ 261.24 Toxicity characteristic.</h4>
      <p class="indent-1">(a) A solid waste exhibits the characteristic.</p>
      <p class="indent-2">(1) As shown in the table:</p>
      <div class="table-wrapper">
      <table border="1" class="gpo_table" width="100%">
        <caption><p class="headnote"><strong>Table</strong> 1
          <strong>—Maximum Concentration</strong></p></caption>
        <thead><tr>
          <th class="center">EPA HW No. <sup>1</sup></th>
          <th class="center">Contaminant</th>
          <th class="center">Regulatory Level (mg/L)</th>
        </tr></thead>
        <tbody>
          <tr><td class="left">D004</td><td class="left">Arsenic</td>
              <td class="right">5.0</td></tr>
          <tr><td class="left">D008</td><td class="left">Lead</td>
              <td class="right">5.0</td></tr>
        </tbody>
        <tfoot><tr><td colspan="3"><sup>1</sup> Hazardous waste number.</td>
        </tr></tfoot>
      </table>
      </div>
      <p class="indent-1">(b) A solid waste that exhibits it.</p>
    </div>
    <p>Chrome after</p>
    """

    def setUp(self):
        self.paras, self.styles = ecfr.parse_section_html(self.PAGE, "261.24")

    def test_a_table_stands_among_the_paragraphs(self):
        self.assertEqual(self.paras[3:], [
            ("table-head", 1, "Table 1 —Maximum Concentration"),
            ("table-head", 1, "EPA HW No. ¹\tContaminant\t"
                              "Regulatory Level (mg/L)"),
            ("table", 1, "D004\tArsenic\t5.0"),
            ("table", 1, "D008\tLead\t5.0"),
            ("table", 1, "¹ Hazardous waste number."),
            ("body", 0, "(b) A solid waste that exhibits it."),
        ])

    def test_its_caption_is_read_once(self):
        self.assertEqual(
            sum("Maximum Concentration" in text for _k, _i, text in self.paras),
            1)

    def test_the_styles_stay_in_step_with_the_paragraphs(self):
        self.assertEqual(len(self.styles), len(self.paras))
        self.assertEqual(self.styles[3:8], [[], [], [], [], []])

    def test_only_the_sections_own(self):
        self.assertFalse([para for para in self.paras
                          if "Not the section's" in para[2]])

    def test_a_page_with_no_table_reads_as_before(self):
        paras, _styles = ecfr.parse_section_html(
            '<div class="section" id="1.1"><h4>§ 1.1</h4>'
            '<p class="indent-1">(a) Text.</p></div>', "1.1")
        self.assertEqual([kind for kind, _i, _t in paras], ["sechead", "body"])


class ColumnTests(unittest.TestCase):
    stops = staticmethod(courtlistener_gui._table_stops)

    @staticmethod
    def measure(cell: str) -> int:
        return 10 * len(cell)

    def test_each_column_clears_its_widest_cell(self):
        rows = [(True, ["Name", "Age", "Town"]),
                (False, ["Alexander", "9", "x"]),
                (False, ["Al", "100", "y"])]
        self.assertEqual(self.stops(rows, self.measure, 20, gap=10),
                         [20 + 90 + 10, 20 + 90 + 10 + 30 + 10])

    def test_the_last_column_wraps_so_it_sets_no_width(self):
        rows = [(False, ["a", "a very long last cell " * 5])]
        self.assertEqual(self.stops(rows, self.measure, 0, gap=0), [10])

    def test_a_short_rows_last_cell_sets_no_width(self):
        # a note under the table, in one cell
        rows = [(False, ["a", "b"]), (False, ["a very long note " * 5])]
        self.assertEqual(self.stops(rows, self.measure, 0, gap=0), [10])

    def test_a_heading_runs_on_across_the_cells_it_spans(self):
        rows = [(True, ["Maximum concentration", "", "Year"]),
                (False, ["1", "2", "3"])]
        self.assertEqual(self.stops(rows, self.measure, 0, gap=0), [10, 20])

    def test_a_blank_in_a_row_of_data_spans_nothing(self):
        rows = [(False, ["wide cell", "", "x"]), (False, ["a", "b", "c"])]
        self.assertEqual(self.stops(rows, self.measure, 0, gap=0)[0], 90)

    def test_a_heading_too_long_for_its_column_sets_no_width(self):
        # 7 U.S.C. § 1441: the heading wraps in print, over a narrow column.
        rows = [(True, ["(a) For corn and wheat, if the supply percentage as "
                        "of the beginning of the marketing year is:", "Level"]),
                (False, ["Not more than 102", "90"])]
        self.assertEqual(self.stops(rows, self.measure, 0, gap=0), [170])

    def test_nor_does_any_other_cell_of_its_row(self):
        rows = [(True, ["Wide heading", "x" * 50, "Last"]),
                (False, ["a", "b", "c"])]
        self.assertEqual(self.stops(rows, self.measure, 0, gap=0), [10, 20])

    def test_a_heading_of_a_fair_length_still_counts(self):
        rows = [(True, ["Regulatory Level (mg/L)", "Year"]),
                (False, ["5.0", "2026"])]
        self.assertEqual(self.stops(rows, self.measure, 0, gap=0), [230])

    def test_one_column_has_no_stops(self):
        self.assertEqual(self.stops([(False, ["only"])], self.measure, 5), [])

    def test_no_rows(self):
        self.assertEqual(self.stops([], self.measure, 5), [])

    def test_thousands_of_rows_cost_a_few_measures(self):
        rows = [(False, [str(n), str(n), "x"]) for n in range(5000)]
        measure = Mock(side_effect=self.measure)
        self.stops(rows, measure, 0)
        self.assertLessEqual(measure.call_count, 6)


class LongCellTests(unittest.TestCase):
    """A cell far longer than its column's others gets a line to itself, the
    rest of its row following in their columns."""

    limits = staticmethod(courtlistener_gui._table_limits)
    lines = staticmethod(courtlistener_gui._table_lines)

    @staticmethod
    def table(*firsts: str) -> list:
        return [(False, [first, "67-64-1", "1000"]) for first in firsts]

    def test_a_column_of_like_cells_has_no_outlier(self):
        rows = self.table("Acetone", "Acrolein", "Aldrin", "Ammonia")
        limits = self.limits(rows)
        self.assertTrue(all(self.lines(cells, limits) == [cells]
                            for _head, cells in rows))

    def test_a_few_long_names_among_many(self):
        # 29 C.F.R. § 1910.1000: 6 long names of 575.
        rows = self.table(*["Acetone"] * 40, "Coal tar pitch volatiles " * 4)
        limits = self.limits(rows)
        self.assertEqual(limits[0], 12)
        self.assertEqual(self.lines(rows[-1][1], limits), [
            ["Coal tar pitch volatiles " * 4],
            ["", "67-64-1", "1000"],
        ])

    def test_a_short_table_sets_no_cell_apart(self):
        # Too few cells to say what is usual: 26 U.S.C. § 1's rate tables.
        rows = [(False, ["Not over $36,900", "15%"]),
                (False, ["Over $140,000 but not over $250,000", "36%"])]
        limits = self.limits(rows)
        self.assertEqual(self.lines(rows[1][1], limits), [rows[1][1]])

    def test_the_last_cell_can_run_on_as_it_likes(self):
        rows = self.table(*["Acetone"] * 40)
        rows[-1] = (False, ["Acetone", "67-64-1", "x" * 200])
        self.assertEqual(self.lines(rows[-1][1], self.limits(rows)),
                         [rows[-1][1]])

    def test_two_long_cells_in_a_row(self):
        limits = [12, 12]
        self.assertEqual(self.lines(["a" * 40, "b" * 40, "c"], limits), [
            ["a" * 40], ["", "b" * 40], ["", "", "c"],
        ])

    def test_a_long_cell_past_the_first_column(self):
        self.assertEqual(self.lines(["a", "b" * 40, "c"], [12, 12]),
                         [["a", "b" * 40], ["", "", "c"]])

    def test_no_cell_is_lost_or_moved_out_of_its_column(self):
        cells = ["a" * 40, "b", "c" * 40, "d"]
        lines = self.lines(cells, [12, 12, 12])
        for column, cell in enumerate(cells):
            self.assertEqual(sum(line[column] == cell for line in lines
                                 if column < len(line)), 1)

    def test_headings_and_a_table_of_none(self):
        self.assertEqual(self.limits([(True, ["a", "b"])]), [])
        self.assertEqual(self.limits([]), [])

    def test_the_columns_are_set_for_the_usual_cells(self):
        rows = self.table(*["Acetone"] * 40, "Coal tar pitch volatiles " * 4)
        limits = self.limits(rows)
        lines = [(head, line) for head, cells in rows
                 for line in self.lines(cells, limits)]
        stops = courtlistener_gui._table_stops(
            lines, lambda cell: 10 * len(cell), 0, gap=0)
        self.assertEqual(stops, [70, 140])


class FitTests(unittest.TestCase):
    ROWS = [(False, ["a" * 30, "b"])]       # 300 px wide at 10 px a character

    def window(self, width):
        font = SimpleNamespace(measure=lambda cell: 10 * len(cell))
        return SimpleNamespace(
            _text=Mock(winfo_width=lambda: width),
            _tables={"table-3": (self.ROWS, 10, font, [])},
            _table_fit={})

    fit = staticmethod(courtlistener_gui._StatuteWindow._fit_tables)

    def test_columns_in_a_window_with_room_for_them(self):
        win = self.window(800)
        self.fit(win)
        win._text.tag_configure.assert_called_once_with(
            "table-3", tabs=(328,), lmargin1=10, lmargin2=328, wrap="word",
            spacing3=3)

    def test_a_table_too_wide_keeps_its_columns_and_can_be_scrolled_to(self):
        # 29 C.F.R. § 1910.1000: wrapped, a figure loses the column saying
        # whether it is ppm or mg/m³.
        win = self.window(400)
        self.fit(win)
        win._text.tag_configure.assert_called_once_with(
            "table-3", tabs=(328,), lmargin1=10, lmargin2=328, wrap="none",
            spacing3=3)

    def test_a_table_of_one_column_has_no_stops_to_set(self):
        win = self.window(800)
        win._tables["table-3"] = ([(False, ["only"])], 10, SimpleNamespace(
            measure=len), [])
        self.fit(win)
        win._text.tag_configure.assert_called_once_with(
            "table-3", tabs="", lmargin1=10, lmargin2=32, wrap="word",
            spacing3=3)

    def test_a_window_being_dragged_changes_nothing_until_it_must(self):
        win = self.window(800)
        self.fit(win)
        self.fit(win)
        self.assertEqual(win._text.tag_configure.call_count, 1)
        win._text.winfo_width = lambda: 400
        self.fit(win)
        self.fit(win)
        self.assertEqual(win._text.tag_configure.call_count, 2)


class XScrollTests(unittest.TestCase):
    """The text's own word on whether there is anything to scroll to."""

    moved = staticmethod(courtlistener_gui._StatuteWindow._on_xscroll)

    @staticmethod
    def window():
        manager = []
        return SimpleNamespace(_text=Mock(), _hsb=Mock(
            pack=Mock(side_effect=lambda **_kw: manager.append("pack")),
            pack_forget=Mock(side_effect=manager.clear),
            winfo_manager=lambda: "pack" if manager else ""))

    def test_text_that_fits_needs_no_scrollbar(self):
        win = self.window()
        self.moved(win, "0.0", "1.0")
        win._hsb.pack.assert_not_called()

    def test_a_table_past_the_edge_shows_it_once(self):
        win = self.window()
        self.moved(win, "0.0", "0.6")
        self.moved(win, "0.2", "0.8")
        self.moved(win, "0.4", "1.0")
        win._hsb.pack.assert_called_once()
        self.assertEqual(win._hsb.set.call_count, 3)

    def test_it_goes_when_the_window_grows_to_fit(self):
        win = self.window()
        self.moved(win, "0.0", "0.6")
        self.moved(win, "0.0", "1.0")
        win._hsb.pack_forget.assert_called_once()


class ViewerTests(unittest.TestCase):
    SRC = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text(
        encoding="utf-8")

    @staticmethod
    def render_source():
        import inspect
        return inspect.getsource(courtlistener_gui._StatuteWindow._render)

    def test_the_viewer_shows_every_kind_of_row(self):
        # A kind it does not name it drops without a word.
        self.assertIn("elif kind in us_code.TABLE_KINDS:", self.render_source())
        self.assertEqual(TABLE_KINDS, {"table", "table-head", "note-table",
                                       "note-table-head"})

    def test_cells_stay_apart_wherever_a_tab_falls(self):
        # A tab just short of a stop would leave "10002400".
        self.assertIn('self._insert_refs(" \\t".join(line), tuple(tags))',
                      self.render_source())

    def test_a_long_heading_is_set_as_text(self):
        self.assertIn("(head and _long_heading(line))", self.render_source())

    def test_a_row_is_set_on_the_lines_its_cells_need(self):
        self.assertIn("_table_lines(", self.render_source())

    def test_a_row_is_anchored_for_pin_cites_and_copy_cite(self):
        self.assertIn('if kind in ("body", "head", "table", "table-head"):',
                      self.render_source())

    def test_zoom_and_resizing_refit_the_columns(self):
        import inspect
        self.assertIn("self._fit_tables()", inspect.getsource(
            courtlistener_gui._StatuteWindow._zoom))
        self.assertIn('txt.bind("<Configure>", lambda _e: self._fit_tables()',
                      inspect.getsource(
                          courtlistener_gui._StatuteWindow._build_ui))

    def test_a_table_goes_with_a_bookmarked_section(self):
        doc = courtlistener_gui._SavedStatuteDoc({
            "kind": "usc", "label": "26 U.S.C. § 1",
            "paras": [["table-head", 0, "a\tb"], ["table", 0, "1\t2"]]})
        self.assertEqual(doc.paras, [("table-head", 0, "a\tb"),
                                     ("table", 0, "1\t2")])


class _Text:
    """The little of a Tk Text that the RTF dump asks of it.  Tk gives a
    tag's tab stops as a tuple once it has any, and as "" before."""

    def __init__(self, tabs):
        self._tabs = tabs
        self.tk = SimpleNamespace(
            splitlist=lambda v: v if isinstance(v, tuple) else tuple(v.split()))

    def tag_names(self, _index):
        return ()

    def tag_cget(self, _tag, _option):
        return self._tabs

    def dump(self, *_args, **_kw):
        return [("tagon", "table-6", "1.0"), ("tagon", "tablehead", "1.0"),
                ("text", "Income:\tTax:\n", "1.0"),
                ("tagoff", "tablehead", "2.0"), ("tagoff", "table-6", "2.0")]


class RtfTests(unittest.TestCase):
    dump = staticmethod(courtlistener_gui._dump_statute_rtf)

    def test_a_tab_is_rtfs_own(self):
        self.assertEqual(courtlistener_gui._rtf_escape("a\tb"), "a\\tab b")

    def test_a_row_keeps_its_columns(self):
        body = self.dump(_Text((284,)), "1.0", "end")
        self.assertIn("\\tx4260", body)
        self.assertIn("Income:\\tab Tax:", body)

    def test_a_heading_is_bold(self):
        self.assertIn("{\\b Income:", self.dump(_Text((284,)), "1.0", "end"))

    def test_a_table_set_row_by_row_has_no_stops(self):
        self.assertNotIn("\\tx", self.dump(_Text(""), "1.0", "end"))


if __name__ == "__main__":
    unittest.main()
