"""The writers' names, flashed beside the scrollbar while it is in use.

A case carrying separate writings maps them onto a slim rail just inside the
scrollbar — each part a band in its own colour.  The colour says a dissent
begins here; it does not say whose.  Taking the pointer to the scrollbar (or
to the rail beside it) now names them all at once: each author's surname in
caps, in the part's own colour, set directly to the left of the band that
writing starts at — the Court's opinion among them.  They stay while the
pointer is there or the thumb is held, and fade out a moment after it leaves.
This is what took the place of the hover tip that named one band at a time.

``_PartNameFlash`` is lifted out of ``courtlistener_gui`` with ``ast``
(importing it needs tkinter, absent on a headless run) and driven against
stubs for the widgets and the clock.
"""

import ast
import pathlib
import re
import sys
import typing
import unittest


SRC = pathlib.Path(__file__).with_name("courtlistener_gui.py").read_text()
TREE = ast.parse(SRC)


class _FakeLabel:
    """A name's own little box: what it was configured with, and where it was
    put.  Created by the class under test through ``tk.Label``."""

    def __init__(self, master, **kw):
        self.master = master
        self.kw = dict(kw)
        self.placed = None
        self.bindings = {}
        self.lifted = 0
        self.destroyed = False
        master.labels.append(self)

    def winfo_reqheight(self):
        return self.master.label_h

    def bind(self, seq, fn, add=None):
        self.bindings.setdefault(seq, []).append(fn)

    def fire(self, seq):
        for fn in self.bindings.get(seq, []):
            fn(None)

    def place(self, **kw):
        self.placed = kw

    def lift(self):
        self.lifted += 1

    def configure(self, **kw):
        self.kw.update(kw)

    def destroy(self):
        self.destroyed = True


class _Tk:
    TclError = Exception
    Menu = Misc = Frame = Toplevel = object
    Label = _FakeLabel


def _base_ns(extra=None) -> dict:
    ns = {"tk": _Tk, "sys": sys, "re": re, "Optional": typing.Optional}
    ns.update(extra or {})
    return ns


def _load_class(name: str, extra=None):
    """Exec the whole of class *name* into a namespace built from stubs."""
    node = next(n for n in TREE.body
                if isinstance(n, ast.ClassDef) and n.name == name)
    ns = _base_ns(extra)
    exec(ast.get_source_segment(SRC, node), ns)  # noqa: S102
    return ns[name]


def _load(cls: str, names, extra=None) -> dict:
    """Exec the named methods of *cls* into a namespace built from stubs."""
    body = next(n.body for n in TREE.body
                if isinstance(n, ast.ClassDef) and n.name == cls)
    found = {n.name: ast.get_source_segment(SRC, n) for n in body
             if isinstance(n, ast.FunctionDef) and n.name in names}
    missing = [n for n in names if n not in found]
    if missing:
        raise AssertionError(f"not found on {cls}: {missing}")
    ns = _base_ns(extra)
    for name in names:
        exec(found[name], ns)  # noqa: S102
    return ns


def _load_functions(names, extra=None) -> dict:
    found = {n.name: ast.get_source_segment(SRC, n) for n in TREE.body
             if isinstance(n, ast.FunctionDef) and n.name in names}
    missing = [n for n in names if n not in found]
    if missing:
        raise AssertionError(f"module-level functions not found: {missing}")
    ns = _base_ns(extra)
    for name in names:
        exec(found[name], ns)  # noqa: S102
    return ns


def _class_attr(cls: str, name: str):
    body = next(n.body for n in TREE.body
                if isinstance(n, ast.ClassDef) and n.name == cls)
    for node in body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return eval(ast.get_source_segment(SRC, node.value),  # noqa: S307
                        {"frozenset": frozenset})
    raise AssertionError(f"{cls} has no {name}")


def _module_value(name: str, ns=None):
    for node in TREE.body:
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return eval(ast.get_source_segment(SRC, node.value),  # noqa: S307
                        dict(ns or {}, frozenset=frozenset))
    raise AssertionError(f"module-level constant not found: {name}")


# ---------------------------------------------------------------------------
# The real helpers the flash is made of
# ---------------------------------------------------------------------------

FUNCS = _load_functions(["_wash_hex", "_blend_hex", "_part_author"])
WASH_HEX, BLEND_HEX = FUNCS["_wash_hex"], FUNCS["_blend_hex"]
KIND_NAMES = _module_value("_PART_KIND_NAMES")
AUTHORED = _module_value("_AUTHORED_PART_KINDS")
FALLBACKS = _module_value("_PART_FLASH_FALLBACKS")
FLASH_NAME = _load_functions(
    ["_part_flash_name"],
    {"_part_author": FUNCS["_part_author"], "_PART_KIND_NAMES": KIND_NAMES,
     "_AUTHORED_PART_KINDS": AUTHORED,
     "_PART_FLASH_FALLBACKS": FALLBACKS},
)["_part_flash_name"]

PART_COLORS = {
    kind: _class_attr("_ScholarTextWindow", attr)
    for kind, attr in (("majority", "_MAJORITY_COLOR"),
                       ("concurrence", "_CONCUR_COLOR"),
                       ("dissent", "_DISSENT_COLOR"),
                       ("separate", "_SEPARATE_COLOR"))
}
PDF_PART_COLORS = _module_value(
    "_PDF_PART_COLORS",
    {"_ScholarTextWindow": type("_ScholarTextWindow", (), {
        "_MAJORITY_COLOR": PART_COLORS["majority"],
        "_CONCUR_COLOR": PART_COLORS["concurrence"],
        "_DISSENT_COLOR": PART_COLORS["dissent"],
        "_SEPARATE_COLOR": PART_COLORS["separate"]})},
)

Flash = _load_class(
    "_PartNameFlash",
    {"_wash_hex": WASH_HEX, "_blend_hex": BLEND_HEX},
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeHost:
    """The frame the names are placed in — and the clock they run on, wound
    by hand so a test can watch the fade step out."""

    label_h = 16

    def __init__(self):
        self.labels = []
        self.timers = {}
        self._n = 0

    def after(self, ms, fn):
        self._n += 1
        tid = f"after#{self._n}"
        self.timers[tid] = (ms, fn)
        return tid

    def after_cancel(self, tid):
        self.timers.pop(tid, None)

    # --- the test's own handles ---
    def pending(self):
        return sorted(ms for ms, _fn in self.timers.values())

    def tick(self):
        """Fire the one timer that is waiting."""
        assert len(self.timers) == 1, f"{len(self.timers)} timers pending"
        tid = next(iter(self.timers))
        _ms, fn = self.timers.pop(tid)
        fn()

    def live(self):
        return [lbl for lbl in self.labels if not lbl.destroyed]


class _FakeStrip:
    """The rail (or the scrollbar), as the flash reads it: a place on screen
    and the bindings it hands out."""

    def __init__(self, x=280, y=0, height=600):
        self.x, self.y, self.height = x, y, height
        self.bindings = {}

    def bind(self, seq, fn, add=None):
        self.bindings.setdefault(seq, []).append(fn)

    def fire(self, seq):
        for fn in self.bindings.get(seq, []):
            fn(None)

    def winfo_x(self):
        return self.x

    def winfo_y(self):
        return self.y

    def winfo_height(self):
        return self.height


ROWS = [(0.0, "ROBERTS", PART_COLORS["majority"]),
        (200.0, "KAGAN", PART_COLORS["concurrence"]),
        (420.0, "THOMAS", PART_COLORS["dissent"])]


def _flash(rows=None, rail=None, host=None, **kw):
    host = host or _FakeHost()
    rail = _FakeStrip() if rail is None else rail
    rows = ROWS if rows is None else rows
    flash = Flash(host, lambda: list(rows), lambda: rail, **kw)
    flash.host, flash.rail = host, rail
    return flash


def _texts(host):
    return [lbl.kw["text"] for lbl in host.live()]


def _ys(host):
    return [lbl.placed["y"] for lbl in host.live()]


# ---------------------------------------------------------------------------
# The names themselves
# ---------------------------------------------------------------------------


class FlashNameTests(unittest.TestCase):
    """Surnames, in caps, the way the reports set them."""

    def test_a_separate_writing_is_named_for_its_author(self):
        self.assertEqual(
            FLASH_NAME("MR. JUSTICE REHNQUIST, dissenting", "dissent"),
            "REHNQUIST")

    def test_the_court_s_own_opinion_is_named_too(self):
        # The point of the flash: the majority is one of the writings, not the
        # unlabelled remainder of the document.
        self.assertEqual(
            FLASH_NAME("Opinion of the Court (Blackmun)", "majority"),
            "BLACKMUN")

    def test_a_chief_justice_keeps_his_surname(self):
        self.assertEqual(FLASH_NAME("ROBERTS, C. J., concurring",
                                    "concurrence"), "ROBERTS")

    def test_an_opinion_in_the_court_s_name_says_so(self):
        self.assertEqual(FLASH_NAME("PER CURIAM", "majority"), "PER CURIAM")

    def test_a_heading_that_is_only_the_word_dissent_names_nobody(self):
        self.assertEqual(FLASH_NAME("Dissent", "dissent"), "DISSENT")

    def test_an_unsigned_writing_falls_back_to_its_kind(self):
        self.assertEqual(FLASH_NAME("", "concurrence"), "CONCURRENCE")
        self.assertEqual(FLASH_NAME("", "majority"), "OPINION")
        self.assertEqual(FLASH_NAME("", "separate"), "SEPARATE OPINION")

    def test_a_kind_nobody_has_named_still_says_something(self):
        self.assertEqual(FLASH_NAME("", "footnotes"), "OPINION")


class BlendTests(unittest.TestCase):
    def test_nothing_of_the_fade_leaves_the_colour_alone(self):
        self.assertEqual(BLEND_HEX("#1a3e72", "#ffffff", 0), "#1a3e72")

    def test_all_of_it_arrives_at_the_page_behind(self):
        self.assertEqual(BLEND_HEX("#1a3e72", "#d9d9d9", 1), "#d9d9d9")

    def test_half_way_is_half_way(self):
        self.assertEqual(BLEND_HEX("#000000", "#ffffff", 0.5), "#808080")

    def test_a_colour_it_cannot_read_still_gives_a_colour(self):
        self.assertTrue(BLEND_HEX("chartreuse", "#ffffff", 0.5).startswith("#"))


# ---------------------------------------------------------------------------
# Showing them
# ---------------------------------------------------------------------------


class FlashShowTests(unittest.TestCase):
    def setUp(self):
        self.flash = _flash()
        self.host, self.rail = self.flash.host, self.flash.rail

    def test_taking_hold_of_the_scrollbar_names_every_writing(self):
        self.flash._pressed()
        self.assertEqual(_texts(self.host), ["ROBERTS", "KAGAN", "THOMAS"])

    def test_each_name_is_in_its_own_part_s_colour(self):
        self.flash._pressed()
        self.assertEqual([lbl.kw["fg"] for lbl in self.host.live()],
                         [PART_COLORS["majority"], PART_COLORS["concurrence"],
                          PART_COLORS["dissent"]])

    def test_a_name_stands_against_the_page_it_is_drawn_over(self):
        # Its own colour washed out: readable over the text without hiding it.
        self.flash._pressed()
        first = self.host.live()[0]
        self.assertNotEqual(first.kw["bg"], first.kw["fg"])
        self.assertEqual(first.kw["bg"],
                         WASH_HEX(PART_COLORS["majority"], Flash._BOX_WASH))

    def test_the_names_sit_directly_to_the_left_of_the_strip(self):
        self.flash._pressed()
        for lbl in self.host.live():
            self.assertEqual(lbl.placed["anchor"], "ne")   # right edge to it
            self.assertEqual(lbl.placed["x"], self.rail.x - Flash._GAP)

    def test_each_name_is_level_with_where_its_writing_begins(self):
        self.flash._pressed()
        self.assertEqual(_ys(self.host), [0, 200, 420])

    def test_they_are_lifted_over_what_they_are_drawn_across(self):
        self.flash._pressed()
        self.assertTrue(all(lbl.lifted for lbl in self.host.live()))

    def test_a_strip_with_no_place_on_screen_yet_shows_nothing(self):
        flash = _flash(rail=_FakeStrip(x=0, height=0))
        flash._pressed()
        self.assertFalse(flash.showing())

    def test_no_rail_means_no_names(self):
        host = _FakeHost()
        flash = Flash(host, lambda: ROWS, lambda: None)
        flash._pressed()
        self.assertEqual(host.labels, [])

    def test_a_document_with_nothing_to_name_shows_nothing(self):
        flash = _flash(rows=[])
        flash._pressed()
        self.assertFalse(flash.showing())

    def test_dragging_on_does_not_rebuild_what_is_already_up(self):
        self.flash._pressed()
        before = list(self.host.live())
        self.flash._dragged()
        self.flash._dragged()
        self.assertEqual(self.host.live(), before)

    def test_the_names_follow_the_rail_when_it_is_redrawn(self):
        # A zoom or a resize moves every band; the next flash has to follow.
        rows = list(ROWS)
        flash = _flash(rows=rows)
        flash._pressed()
        rows[2] = (500.0, "THOMAS", PART_COLORS["dissent"])
        flash._dragged()
        self.assertEqual(_ys(flash.host), [0, 200, 500])


class FlashStackTests(unittest.TestCase):
    """Names kept off one another, and inside the run of the strip."""

    def test_two_writings_beginning_together_are_pushed_apart(self):
        flash = _flash(rows=[(100.0, "ALITO", PART_COLORS["dissent"]),
                             (104.0, "THOMAS", PART_COLORS["dissent"])])
        flash._pressed()
        self.assertEqual(_ys(flash.host),
                         [100, 100 + _FakeHost.label_h + Flash._MIN_GAP])

    def test_a_dissent_at_the_very_end_stays_inside_the_strip(self):
        flash = _flash(rows=[(0.0, "ROBERTS", PART_COLORS["majority"]),
                             (598.0, "ALITO", PART_COLORS["dissent"])])
        flash._pressed()
        last = flash.host.live()[-1]
        self.assertLessEqual(last.placed["y"] + _FakeHost.label_h, 600)

    def test_a_run_packed_up_from_the_foot_keeps_them_all_readable(self):
        flash = _flash(rows=[(560.0, "ROBERTS", PART_COLORS["majority"]),
                             (580.0, "KAGAN", PART_COLORS["concurrence"]),
                             (599.0, "ALITO", PART_COLORS["dissent"])])
        flash._pressed()
        ys = _ys(flash.host)
        self.assertLessEqual(ys[-1] + _FakeHost.label_h, 600)
        for a, b in zip(ys, ys[1:]):
            self.assertGreaterEqual(b - a, _FakeHost.label_h + Flash._MIN_GAP)

    def test_a_strip_that_does_not_start_at_the_top_carries_them_down(self):
        flash = _flash(rows=[(0.0, "ROBERTS", PART_COLORS["majority"])],
                       rail=_FakeStrip(y=40, height=500))
        flash._pressed()
        self.assertEqual(flash.host.live()[0].placed["y"], 40)


class FlashHoverTests(unittest.TestCase):
    """Pointing at the scrollbar is enough — no click needed."""

    def setUp(self):
        self.flash = _flash()
        self.host, self.rail = self.flash.host, self.flash.rail

    def test_the_pointer_arriving_names_every_writing(self):
        self.flash._over_strip()
        self.assertEqual(_texts(self.host), ["ROBERTS", "KAGAN", "THOMAS"])

    def test_they_stay_for_as_long_as_it_is_there(self):
        self.flash._over_strip()
        self.flash._over_strip()          # …and on across the strip
        self.assertEqual(self.host.pending(), [])
        self.assertTrue(self.flash.showing())

    def test_they_start_fading_once_it_leaves(self):
        self.flash._over_strip()
        self.flash._left_strip()
        self.assertEqual(self.host.pending(), [Flash._LINGER_MS])

    def test_letting_go_over_the_scrollbar_leaves_them_up(self):
        # The pointer is still on the thumb it just released — nothing has
        # stopped hovering, so nothing starts counting down.
        self.flash._over_strip()
        self.flash._pressed()
        self.flash._released()
        self.assertEqual(self.host.pending(), [])
        self.assertTrue(self.flash.showing())

    def test_a_drag_that_wanders_off_the_scrollbar_keeps_them(self):
        # Tk sends <Leave> mid-drag when the pointer crosses the scrollbar's
        # edge; the names must not go with it.
        self.flash._over_strip()
        self.flash._pressed()
        self.flash._left_strip()
        self.flash._dragged()
        self.assertEqual(self.host.pending(), [])
        self.assertTrue(self.flash.showing())

    def test_the_pointer_leaving_a_strip_with_nothing_on_it_starts_nothing(self):
        flash = _flash(rows=[])
        flash._over_strip()
        flash._left_strip()
        self.assertEqual(flash.host.pending(), [])

    def test_hovering_is_bound_on_the_strip_itself(self):
        strip = _FakeStrip()
        self.flash.watch(strip)
        for seq in ("<Enter>", "<Motion>", "<Leave>"):
            self.assertIn(seq, strip.bindings)
        strip.fire("<Enter>")
        self.assertTrue(self.flash.showing())


class FlashTimingTests(unittest.TestCase):
    """While the scrollbar is in use, and a moment after — no longer."""

    def setUp(self):
        self.flash = _flash()
        self.host = self.flash.host

    def test_a_held_scrollbar_keeps_the_names_up(self):
        self.flash._pressed()
        self.flash._dragged()
        self.assertEqual(self.host.pending(), [])   # nothing is taking them away
        self.assertTrue(self.flash.showing())

    def test_letting_go_away_from_the_scrollbar_starts_the_clock(self):
        # A press that came from the keyboard-less case: the thumb was grabbed
        # and released with the pointer already gone from the strip.
        self.flash._pressed()
        self.flash._released()
        self.assertEqual(self.host.pending(), [Flash._LINGER_MS])

    def test_the_wheel_over_the_scrollbar_flashes_them_too(self):
        self.flash._over_strip()       # the pointer is there to wheel at all
        self.flash._wheeled()
        self.assertTrue(self.flash.showing())
        self.assertEqual(self.host.pending(), [])
        self.flash._left_strip()
        self.assertEqual(self.host.pending(), [Flash._LINGER_MS])

    def test_they_fade_out_rather_than_being_snatched_away(self):
        self.flash._pressed()
        self.flash._released()
        self.host.tick()                      # the linger runs out
        first = self.host.live()[0]
        self.assertNotEqual(first.kw["fg"], PART_COLORS["majority"])
        self.assertEqual(self.host.pending(), [Flash._FADE_MS])
        self.assertTrue(self.flash.showing())

    def test_the_fade_ends_with_the_page_to_itself(self):
        self.flash._pressed()
        self.flash._released()
        for _ in range(Flash._FADE_STEPS + 2):
            if self.host.timers:
                self.host.tick()
        self.assertFalse(self.flash.showing())
        self.assertTrue(all(lbl.destroyed for lbl in self.host.labels))
        self.assertEqual(self.host.pending(), [])

    def test_scrolling_again_mid_fade_brings_them_back_to_full_strength(self):
        self.flash._pressed()
        self.flash._released()
        self.host.tick()                      # a step into the fade
        self.flash._pressed()
        self.assertEqual([lbl.kw["fg"] for lbl in self.host.live()],
                         [PART_COLORS["majority"], PART_COLORS["concurrence"],
                          PART_COLORS["dissent"]])
        self.assertEqual(self.host.pending(), [])

    def test_a_second_flash_does_not_leave_two_clocks_running(self):
        self.flash._wheeled()
        self.flash._wheeled()
        self.assertEqual(len(self.host.timers), 1)

    def test_a_name_gets_out_of_the_way_of_the_pointer(self):
        # It sits over the opinion for a second or two; a reader reaching past
        # it should not have the click swallowed by a fading label.
        self.flash._over_strip()
        self.flash._left_strip()          # off the scrollbar, onto a name
        self.host.live()[0].fire("<Enter>")
        self.assertFalse(self.flash.showing())

    def test_it_stands_its_ground_while_the_thumb_is_held(self):
        self.flash._over_strip()
        self.flash._pressed()
        self.flash._left_strip()          # the drag has wandered off the strip
        self.host.live()[0].fire("<Enter>")
        self.assertTrue(self.flash.showing())

    def test_putting_them_away_stops_the_clock(self):
        self.flash._pressed()
        self.flash._released()
        self.flash.hide()
        self.assertEqual(self.host.pending(), [])
        self.assertFalse(self.flash.showing())


class FlashBindingTests(unittest.TestCase):
    def setUp(self):
        self.flash = _flash()
        self.strip = _FakeStrip()
        self.flash.watch(self.strip)

    def test_every_way_of_scrolling_with_it_is_watched(self):
        for seq in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>",
                    "<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.assertIn(seq, self.strip.bindings)

    def test_the_bindings_are_added_to_what_is_already_there(self):
        # The scrollbar's own class bindings do the scrolling; a binding that
        # replaced them would leave the thumb dead.
        flash = _flash()
        strip = _FakeStrip()
        seen = []
        strip.bind = lambda seq, fn, add=None: seen.append(add)
        flash.watch(strip)
        self.assertTrue(seen and all(add == "+" for add in seen))

    def test_a_press_on_the_strip_shows_the_names(self):
        self.strip.fire("<Button-1>")
        self.assertEqual(_texts(self.flash.host), ["ROBERTS", "KAGAN", "THOMAS"])

    def test_a_handler_returns_nothing_so_the_scroll_still_happens(self):
        # Returning "break" from any of these would stop Tk running the
        # scrollbar's own binding for the same event.
        self.assertIsNone(self.flash._pressed(None))
        self.assertIsNone(self.flash._dragged(None))
        self.assertIsNone(self.flash._released(None))
        self.assertIsNone(self.flash._wheeled(None))

    def test_nothing_to_watch_is_no_error(self):
        self.flash.watch(None)


# ---------------------------------------------------------------------------
# What each viewer hands the flash
# ---------------------------------------------------------------------------


PANE_NS = _load(
    "_PdfPane", ["_flash_rows"],
    {"_PDF_PART_COLORS": PDF_PART_COLORS, "_AUTHORED_PART_KINDS": AUTHORED,
     "_part_flash_name": FLASH_NAME},
)


class _Sec:
    def __init__(self, kind, label):
        self.kind, self.label = kind, label


class ScanRowTests(unittest.TestCase):
    """The scan: the names come off the rail's own bands."""

    def _rows(self, spans):
        pane = type("_Pane", (), {"_rail_spans": spans})()
        return PANE_NS["_flash_rows"].__get__(pane)()

    def test_every_writing_on_the_rail_is_named_where_it_begins(self):
        rows = self._rows([
            (0, 120, _Sec("majority", "Opinion of the Court (Roberts)")),
            (120, 300, _Sec("dissent", "JUSTICE ALITO, dissenting")),
        ])
        self.assertEqual(rows, [
            (0, "ROBERTS", PDF_PART_COLORS["majority"]),
            (120, "ALITO", PDF_PART_COLORS["dissent"]),
        ])

    def test_a_syllabus_is_nobody_s_writing_and_is_left_unnamed(self):
        rows = self._rows([
            (0, 60, _Sec("syllabus", "Syllabus")),
            (60, 300, _Sec("majority", "Opinion of the Court (Kagan)")),
        ])
        self.assertEqual([name for _y, name, _c in rows], ["KAGAN"])

    def test_a_rail_that_is_not_up_names_nothing(self):
        self.assertEqual(self._rows([]), [])


TEXT_NS = _load(
    "_ScholarTextWindow", ["_flash_part_rows"],
    {"_AUTHORED_PART_KINDS": AUTHORED, "_part_flash_name": FLASH_NAME},
)


class TextRowTests(unittest.TestCase):
    """The text: the names come off the same rows the rail was drawn from."""

    def _rows(self, rows, chromeless=True):
        reader = type("_Reader", (), {
            "_PARTMAP_COLORS": PART_COLORS,
            "_chromeless": chromeless,
            "_partmap_rows": rows,
        })()
        return TEXT_NS["_flash_part_rows"].__get__(reader)()

    def test_the_writings_on_the_rail_are_named_where_they_begin(self):
        rows = self._rows([
            (0, 300, "1.0", "Opinion of the Court (Blackmun)", "majority"),
            (300, 600, "80.0", "MR. JUSTICE REHNQUIST, dissenting", "dissent"),
        ])
        self.assertEqual(rows, [
            (0, "BLACKMUN", PART_COLORS["majority"]),
            (300, "REHNQUIST", PART_COLORS["dissent"]),
        ])

    def test_a_syllabus_band_is_left_unnamed(self):
        rows = self._rows([
            (0, 40, "1.0", "Syllabus", "syllabus"),
            (40, 600, "9.0", "Opinion of the Court (Kagan)", "majority"),
        ])
        self.assertEqual([name for _y, name, _c in rows], ["KAGAN"])

    def test_the_case_window_s_labelled_strip_is_not_named_twice(self):
        # It prints the names already; flashing a second set over the top of
        # them would only say it again.
        rows = self._rows(
            [(0, 300, "1.0", "Opinion of the Court (Blackmun)", "majority"),
             (300, 600, "80.0", "JUSTICE ALITO, dissenting", "dissent")],
            chromeless=False,
        )
        self.assertEqual(rows, [])

    def test_a_reader_with_no_rail_drawn_names_nothing(self):
        self.assertEqual(self._rows([]), [])


class WiringTests(unittest.TestCase):
    """The two viewers hand their scrollbar to the flash."""

    def _wires(self, cls: str, method: str, line: str) -> bool:
        """Whether *cls* builds its scrollbar with *line* in it — the one
        assembly a headless test cannot run for itself."""
        body = next(n.body for n in TREE.body
                    if isinstance(n, ast.ClassDef) and n.name == cls)
        node = next(n for n in body
                    if isinstance(n, ast.FunctionDef) and n.name == method)
        return line in ast.get_source_segment(SRC, node)

    def test_the_scan_s_scrollbar_flashes_the_names(self):
        for line in ("_PartNameFlash(", "self._part_flash.watch(vsb)"):
            self.assertTrue(self._wires("_PdfPane", "__init__", line), line)

    def test_the_text_s_scrollbar_and_rail_both_do(self):
        for line in ("_PartNameFlash(", "self._part_flash.watch(vsb)",
                     "self._part_flash.watch(self._partmap)"):
            self.assertTrue(
                self._wires("_ScholarTextWindow", "_build_ui", line), line)


if __name__ == "__main__":
    unittest.main()
