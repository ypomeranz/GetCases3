"""Bookmark folders: named folders the bookmarks are kept in, a document in
as many of them as the reader likes, and the Bookmarks menu that shows them.

A bookmark is one stored copy of a document with a list of the places it is
in — "" for the top level of the menu, or a folder's id — so a case filed
under two folders is still one bookmark.  These tests drive the app's
bookmark methods on a bare ``CourtListenerGUI`` (no window is opened), with
the config file pointed at a scratch directory so no real bookmarks are
touched.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["GETCASES_SKIP_DEPENDENCY_PROMPT"] = "1"

import courtlistener_gui
from courtlistener_gui import CourtListenerGUI


def _entry(key, label, accessed, places=None):
    entry = {"key": key, "label": label, "noun": "case",
             "last_accessed": accessed, "bookmarked_at": accessed,
             "payload": {"type": "scholar", "url": "u", "html": "<p>x</p>",
                         "item": {}}}
    if places is not None:
        entry["folders"] = places
    return entry


class _Menu:
    """The menu surface populate_bookmarks_menu uses."""

    def __init__(self):
        self.items = []

    def delete(self, *_args):
        self.items.clear()

    def add_separator(self):
        self.items.append(("--", None))

    def add_command(self, label="", command=None, state=None):
        self.items.append((label, command if state != "disabled" else None))

    def add_cascade(self, label="", menu=None, **_kw):
        self.items.append((label, menu))

    def add_checkbutton(self, label="", variable=None, command=None, **_kw):
        self.items.append((label, (variable, command)))

    def labels(self):
        return [label for label, _x in self.items]

    def get(self, label):
        return next(x for lab, x in self.items if lab == label)


class _Var:
    def __init__(self, master=None, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Owner:
    """A window showing a document: what its bookmark toggle does."""

    def __init__(self, app, key="scholar:9", label="Haaland v. Brackeen",
                 kind="scholar"):
        self.app, self.key, self.label, self.kind = app, key, label, kind
        self.toggled = 0

    def _bookmark_descriptor(self):
        return {"key": self.key, "label": self.label, "noun": "case",
                "payload": {"type": self.kind, "url": "https://x/y.pdf"}}

    def _toggle_bookmark(self):
        self.toggled += 1
        desc = self._bookmark_descriptor()
        if self.app.is_bookmarked(desc["key"]):
            self.app.remove_bookmark(desc["key"])
        else:
            self.app.add_bookmark(desc)


class _Base(unittest.TestCase):
    def setUp(self):
        scratch = Path(tempfile.mkdtemp())
        self.config = scratch / "config.json"
        patcher = patch("courtlistener_gui._CONFIG_PATH", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        # A submenu is a real Tk menu in the app; here, one more stub.
        child = patch("courtlistener_gui._menu_child",
                      side_effect=lambda _menu: _Menu())
        child.start()
        self.addCleanup(child.stop)
        var = patch("courtlistener_gui.tk.BooleanVar", _Var)
        var.start()
        self.addCleanup(var.stop)
        self.app = self._app()

    def _app(self):
        app = object.__new__(CourtListenerGUI)
        app.root = object()
        app._root_hidden = False
        app._open_case_views = {}
        app._document_windows = []
        app._bookmark_listeners = []
        app._bookmark_organizer = None
        app._folder_icon_image = False     # no artwork to draw here
        app._bookmark_folders = app._load_bookmark_folders()
        app._bookmarks = app._load_bookmarks()
        return app

    def reload(self):
        """The app as the next run finds it, from the saved config."""
        return self._app()


class FolderTests(_Base):
    def test_a_folder_is_made_named_and_saved(self):
        folder = self.app.create_bookmark_folder("  Takings  clause ")
        self.assertEqual(self.reload().bookmark_folders(),
                         [{"id": folder, "name": "Takings clause"}])

    def test_no_name_no_folder(self):
        self.assertIsNone(self.app.create_bookmark_folder("   "))
        self.assertEqual(self.app.bookmark_folders(), [])

    def test_a_name_already_taken_is_that_folder(self):
        # Two folders of one name could not be told apart on the menu.
        first = self.app.create_bookmark_folder("Standing")
        self.assertEqual(self.app.create_bookmark_folder("standing"), first)
        self.assertEqual(len(self.app.bookmark_folders()), 1)

    def test_folders_are_listed_by_name(self):
        for name in ("takings", "Standing", "Abstention"):
            self.app.create_bookmark_folder(name)
        self.assertEqual([f["name"] for f in self.app.bookmark_folders()],
                         ["Abstention", "Standing", "takings"])

    def test_a_folder_is_renamed_but_not_onto_another(self):
        a = self.app.create_bookmark_folder("Takings")
        self.app.create_bookmark_folder("Standing")
        self.assertTrue(self.app.rename_bookmark_folder(a, "Regulatory Takings"))
        self.assertFalse(self.app.rename_bookmark_folder(a, "standing"))
        self.assertFalse(self.app.rename_bookmark_folder(a, " "))
        self.assertEqual(self.reload()._bookmark_folder(a)["name"],
                         "Regulatory Takings")

    def test_deleting_a_folder_keeps_its_bookmarks(self):
        a = self.app.create_bookmark_folder("Takings")
        b = self.app.create_bookmark_folder("Standing")
        self.app._bookmarks = [_entry("k1", "One", 1, [a]),
                               _entry("k2", "Two", 2, [a, b])]
        self.app.delete_bookmark_folder(a)
        # One in no other folder goes to the top level; the other stays put.
        self.assertEqual(self.app.bookmark_places("k1"), [""])
        self.assertEqual(self.app.bookmark_places("k2"), [b])
        self.assertIsNone(self.app._bookmark_folder(a))


class PlacementTests(_Base):
    def setUp(self):
        super().setUp()
        self.takings = self.app.create_bookmark_folder("Takings")
        self.standing = self.app.create_bookmark_folder("Standing")
        self.desc = {"key": "scholar:1", "label": "Knick v. Township of Scott",
                     "noun": "case", "payload": {"type": "scholar"}}

    def test_a_bookmark_goes_to_the_top_level_unless_a_folder_is_named(self):
        self.app.add_bookmark(self.desc)
        self.assertEqual(self.app.bookmark_places("scholar:1"), [""])

    def test_one_document_in_several_folders_is_one_bookmark(self):
        self.app.add_bookmark(self.desc, self.takings)
        self.app.add_bookmark(self.desc, self.standing)
        self.app.add_bookmark(self.desc, self.standing)   # already there
        self.assertEqual(self.app.bookmark_places("scholar:1"),
                         [self.takings, self.standing])
        self.assertEqual(len(self.app._bookmarks), 1)

    def test_a_folder_that_is_gone_is_no_place_to_put_one(self):
        self.app.add_bookmark(self.desc, "nowhere")
        self.assertEqual(self.app.bookmark_places("scholar:1"), [""])

    def test_moving_one_takes_it_out_of_where_it_was(self):
        self.app.add_bookmark(self.desc, self.takings)
        self.app.move_bookmark("scholar:1", self.takings, self.standing)
        self.assertEqual(self.app.bookmark_places("scholar:1"),
                         [self.standing])

    def test_moving_onto_a_place_it_is_already_in_merges_the_two(self):
        self.app.add_bookmark(self.desc, self.takings)
        self.app.add_bookmark_place("scholar:1", "")
        self.app.move_bookmark("scholar:1", self.takings, "")
        self.assertEqual(self.app.bookmark_places("scholar:1"), [""])

    def test_out_of_one_folder_it_stays_in_the_others(self):
        self.app.add_bookmark(self.desc, self.takings)
        self.app.add_bookmark_place("scholar:1", self.standing)
        self.assertIsNone(
            self.app.remove_bookmark_from_folder("scholar:1", self.takings))
        self.assertEqual(self.app.bookmark_places("scholar:1"),
                         [self.standing])

    def test_out_of_the_last_it_is_no_longer_bookmarked(self):
        self.app.add_bookmark(self.desc, self.takings)
        removed = self.app.remove_bookmark_from_folder("scholar:1",
                                                       self.takings)
        self.assertEqual(removed["key"], "scholar:1")
        self.assertFalse(self.app.is_bookmarked("scholar:1"))

    def test_the_stored_scan_goes_with_the_last_place(self):
        pdf = dict(self.desc, key="pdf:x",
                   payload={"type": "pdf", "url": "https://x/y.pdf"})
        self.app.add_bookmark(pdf, self.takings)
        self.app.add_bookmark_place("pdf:x", self.standing)
        with patch("courtlistener_gui._bookmark_pdf_delete") as delete:
            self.app.discard_bookmark_place("pdf:x", self.takings)
            delete.assert_not_called()
            self.app.discard_bookmark_place("pdf:x", self.standing)
            delete.assert_called_once_with("https://x/y.pdf")

    def test_places_are_saved_and_read_back(self):
        self.app.add_bookmark(self.desc, self.takings)
        self.app.add_bookmark_place("scholar:1", "")
        self.assertEqual(self.reload().bookmark_places("scholar:1"),
                         [self.takings, ""])

    def test_a_bookmark_saved_before_there_were_folders_is_at_the_top(self):
        self.config.write_text(json.dumps({"bookmarks": [
            {"key": "old:1", "label": "Old", "payload": {"type": "scholar"}},
        ]}))
        self.assertEqual(self.reload().bookmark_places("old:1"), [""])

    def test_a_saved_folder_that_no_longer_exists_is_dropped(self):
        self.config.write_text(json.dumps({
            "bookmark_folders": [{"id": "f1", "name": "Kept"}],
            "bookmarks": [{"key": "a", "label": "A", "folders": ["f1", "f2"],
                           "payload": {"type": "scholar"}},
                          {"key": "b", "label": "B", "folders": ["f2"],
                           "payload": {"type": "scholar"}}]}))
        app = self.reload()
        self.assertEqual(app.bookmark_places("a"), ["f1"])
        self.assertEqual(app.bookmark_places("b"), [""])

    def test_the_organizer_hears_of_every_change(self):
        heard = []
        self.app._bookmark_listeners.append(lambda: heard.append(1))
        self.app.add_bookmark(self.desc, self.takings)
        self.app.rename_bookmark_folder(self.takings, "Regulatory Takings")
        self.assertEqual(len(heard), 2)


class FolderCheckboxTests(_Base):
    """"Bookmark This Case In ▸" — the document on screen, filed and
    unfiled one place at a time."""

    def setUp(self):
        super().setUp()
        self.takings = self.app.create_bookmark_folder("Takings")
        self.standing = self.app.create_bookmark_folder("Standing")
        self.owner = _Owner(self.app)

    def test_the_first_place_goes_through_the_window_s_own_toggle(self):
        # Which is what stores the copy the bookmark reopens from.
        self.app.set_bookmark_in_folder(self.owner, self.takings, True)
        self.assertEqual(self.owner.toggled, 1)
        self.assertEqual(self.app.bookmark_places("scholar:9"),
                         [self.takings])

    def test_later_places_are_added_to_it(self):
        self.app.set_bookmark_in_folder(self.owner, self.takings, True)
        self.app.set_bookmark_in_folder(self.owner, "", True)
        self.assertEqual(self.owner.toggled, 1)
        self.assertEqual(self.app.bookmark_places("scholar:9"),
                         [self.takings, ""])

    def test_taken_out_of_one_it_stays_in_the_rest(self):
        self.app.set_bookmark_in_folder(self.owner, self.takings, True)
        self.app.set_bookmark_in_folder(self.owner, self.standing, True)
        self.app.set_bookmark_in_folder(self.owner, self.takings, False)
        self.assertEqual(self.app.bookmark_places("scholar:9"),
                         [self.standing])

    def test_the_last_place_goes_through_the_toggle_too(self):
        # Which drops the stored copy with the bookmark.
        self.app.set_bookmark_in_folder(self.owner, self.takings, True)
        self.app.set_bookmark_in_folder(self.owner, self.takings, False)
        self.assertEqual(self.owner.toggled, 2)
        self.assertFalse(self.app.is_bookmarked("scholar:9"))

    def test_a_new_folder_from_the_menu_takes_the_document_straight_in(self):
        with patch("courtlistener_gui._ask_name", return_value="Indian law"):
            folder = self.app.new_bookmark_folder(None, owner=self.owner)
        self.assertEqual(self.app._bookmark_folder(folder)["name"],
                         "Indian law")
        self.assertEqual(self.app.bookmark_places("scholar:9"), [folder])

    def test_putting_the_question_away_makes_nothing(self):
        with patch("courtlistener_gui._ask_name", return_value=None):
            self.assertIsNone(self.app.new_bookmark_folder(None, owner=self.owner))
        self.assertFalse(self.app.is_bookmarked("scholar:9"))


class BookmarksMenuTests(_Base):
    def setUp(self):
        super().setUp()
        self.takings = self.app.create_bookmark_folder("Takings")
        self.standing = self.app.create_bookmark_folder("Standing")
        self.empty = self.app.create_bookmark_folder("Empty one")
        self.app._bookmarks = [
            _entry("k1", "Knick v. Township of Scott", 5, [self.takings]),
            _entry("k2", "Lujan v. Defs. of Wildlife", 4, [self.standing]),
            _entry("k3", "Roe v. Wade", 3, [""]),
            _entry("k4", "Cedar Point Nursery v. Hassid", 6,
                   [self.takings, ""]),
        ]

    def test_folders_first_then_the_bookmarks_in_none(self):
        menu = _Menu()
        self.app.populate_bookmarks_menu(menu, self.app.root)
        self.assertEqual(menu.labels(), [
            "Empty one", "Standing", "Takings", "--",
            "Cedar Point Nursery v. Hassid", "Roe v. Wade",
            "--", "New Folder…", "Organize Bookmarks…"])

    def test_a_folder_lists_its_own_most_recently_used_first(self):
        menu = _Menu()
        self.app.populate_bookmarks_menu(menu, self.app.root)
        self.assertEqual(menu.get("Takings").labels(), [
            "Cedar Point Nursery v. Hassid", "Knick v. Township of Scott"])
        self.assertEqual(menu.get("Empty one").labels(), ["Empty"])

    def test_the_document_on_screen_leads_with_its_places(self):
        owner = _Owner(self.app, key="k4",
                       label="Cedar Point Nursery v. Hassid")
        menu = _Menu()
        self.app.populate_bookmarks_menu(menu, None, owner=owner)
        self.assertEqual(menu.labels()[:3], [
            "Remove Bookmark for This Case", "Bookmark This Case In", "--"])
        places = menu.get("Bookmark This Case In")
        self.assertEqual(places.labels(), [
            "No Folder", "Empty one", "Standing", "Takings", "--",
            "New Folder…"])
        checked = [label for label, x in places.items
                   if isinstance(x, tuple) and x[0].get()]
        self.assertEqual(checked, ["No Folder", "Takings"])
        # And marked where it is listed.
        self.assertIn("• Cedar Point Nursery v. Hassid",
                      menu.get("Takings").labels())

    def test_a_checkbox_files_the_document(self):
        owner = _Owner(self.app, key="k4")
        menu = _Menu()
        self.app.populate_bookmarks_menu(menu, None, owner=owner)
        places = menu.get("Bookmark This Case In")
        variable, command = places.get("Standing")
        variable.set(True)       # what Tk does before it calls the command
        command()
        self.assertEqual(self.app.bookmark_places("k4"),
                         [self.takings, "", self.standing])

    def test_a_document_not_yet_bookmarked_offers_to_be(self):
        owner = _Owner(self.app, key="new:1", label="New v. Case")
        menu = _Menu()
        self.app.populate_bookmarks_menu(menu, None, owner=owner)
        self.assertEqual(menu.labels()[0], "Bookmark This Case")

    def test_the_submenus_of_the_last_opening_are_destroyed(self):
        # The menu is refilled each time it opens; the submenus it made last
        # time go, rather than piling up one set per opening.
        destroyed = []

        class Child(_Menu):
            def destroy(self):
                destroyed.append(self)

        parent = _Menu()
        parent._filled_children = [Child(), Child()]
        kids = list(parent._filled_children)
        courtlistener_gui._drop_menu_children(parent)
        self.assertEqual((destroyed, parent._filled_children), (kids, []))
        menu = _Menu()
        with patch("courtlistener_gui._drop_menu_children") as drop:
            self.app.populate_bookmarks_menu(menu, self.app.root)
        drop.assert_called_once_with(menu)


class ForgetCopyTests(unittest.TestCase):
    def test_only_a_scan_kept_beside_the_bookmark_is_deleted(self):
        with patch("courtlistener_gui._bookmark_pdf_delete") as delete:
            for kind in ("scholar", "cl", "statute", "engrep"):
                courtlistener_gui._forget_bookmark_copy(
                    {"payload": {"type": kind, "url": "u"}})
            delete.assert_not_called()
            courtlistener_gui._forget_bookmark_copy(
                {"payload": {"type": "slip", "url": "https://s/1.pdf"}})
            delete.assert_called_once_with("https://s/1.pdf")

    def test_so_is_a_cited_case_s_scan(self):
        with patch("courtlistener_gui._bookmark_pdf_delete") as delete:
            courtlistener_gui._forget_bookmark_copy(
                {"payload": {"type": "cited", "url": "https://c/2.pdf"}})
            delete.assert_called_once_with("https://c/2.pdf")


class OrganizerSourceTests(unittest.TestCase):
    """The pieces of the Organize Bookmarks window easier read than driven
    without a display."""

    def test_ctrl_or_option_copies_instead_of_moving(self):
        copy = courtlistener_gui._BookmarkOrganizer._copy_modifier
        self.assertTrue(copy(0x0004))
        self.assertFalse(copy(0))
        # Tk's Windows port raises Mod1 for Num Lock: never a copy.
        self.assertFalse(copy(0x0008))

    def test_it_is_one_window_brought_forward_when_asked_again(self):
        app = object.__new__(CourtListenerGUI)

        class Open:
            surfaced = 0

            def alive(self):
                return True

            def surface(self):
                Open.surfaced += 1

        app._bookmark_organizer = Open()
        app.show_bookmark_organizer()
        self.assertEqual(Open.surfaced, 1)


class _Recorder:
    """Stands in for a window class: records how it was opened."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class EverySourceTests(_Base):
    """Whatever is open can be bookmarked, as pages or as text."""

    def setUp(self):
        super().setUp()
        self.app._status_var = type("Var", (), {"set": lambda _s, _t: None})()

    def test_a_statutes_at_large_page_is_a_source_to_bookmark(self):
        win = object.__new__(courtlistener_gui._PdfWindow)
        win._app, win._is_case = self.app, False
        win._url = "https://www.govinfo.gov/link/statute/124/119"
        win._title = "124 Stat. 119"
        desc = win._bookmark_descriptor()
        self.assertEqual(desc["key"], f"pdf:{win._url}")
        self.assertEqual(desc["noun"], "source")
        self.assertIs(desc["payload"]["is_case"], False)

    def test_and_reopens_as_one(self):
        opened = _Recorder()
        self.app.add_bookmark({
            "key": "pdf:https://g/statute/124/119", "label": "124 Stat. 119",
            "noun": "source",
            "payload": {"type": "pdf", "url": "https://g/statute/124/119",
                        "title": "124 Stat. 119", "is_case": False}})
        entry = self.app._bookmark_entry("pdf:https://g/statute/124/119")
        with patch("courtlistener_gui._PdfWindow", opened):
            self.app._bookmark_opener_from_entry(entry)()
        (_args, kwargs), = opened.calls
        self.assertIs(kwargs["is_case"], False)
        self.assertTrue(kwargs["local_pdf"])

    def test_a_case_scan_saved_before_still_reopens_as_a_case(self):
        opened = _Recorder()
        entry = {"key": "pdf:u", "label": "Roe",
                 "payload": {"type": "pdf", "url": "u", "title": "Roe"}}
        with patch("courtlistener_gui._PdfWindow", opened):
            self.app._bookmark_opener_from_entry(entry)()
        self.assertIs(opened.calls[0][1]["is_case"], True)

    def test_a_cited_case_s_scan_reopens_through_its_own_window(self):
        reopened = []
        self.app._reopen_cited_scan = lambda payload, label: reopened.append(
            (payload["url"], label))
        entry = {"key": "pdf:https://c/2.pdf", "label": "Roe v. Wade",
                 "payload": {"type": "cited", "url": "https://c/2.pdf",
                             "cite": "410 U.S. 113"}}
        self.app._bookmark_opener_from_entry(entry)()
        self.assertEqual(reopened, [("https://c/2.pdf", "Roe v. Wade")])

    def _viewer(self, mode="pdf", reader=None, bookmarks=None, host=None):
        viewer = object.__new__(courtlistener_gui._FloatingPdfWindow)
        viewer._app, viewer._mode = self.app, mode
        viewer._reader, viewer._bookmarks = reader, bookmarks
        viewer._text_host = host
        return viewer

    def test_the_text_on_screen_bookmarks_the_opinion(self):
        reader, scan = _Owner(self.app), _Owner(self.app, key="pdf:u")
        viewer = self._viewer("text", reader=reader, bookmarks=scan)
        self.assertIs(viewer._bookmark_owner(), reader)

    def test_the_pages_on_screen_bookmark_the_scan(self):
        reader, scan = _Owner(self.app), _Owner(self.app, key="pdf:u")
        viewer = self._viewer("pdf", reader=reader, bookmarks=scan)
        self.assertIs(viewer._bookmark_owner(), scan)

    def test_pages_found_for_a_text_window_bookmark_its_case(self):
        reader = _Owner(self.app)
        self.assertIs(self._viewer("pdf", reader=reader)._bookmark_owner(),
                      reader)

    def test_a_document_built_into_the_text_side_bookmarks_itself(self):
        # A slip opinion in a reporter window: no opinion reader, only the
        # document registered for the frame it was built in.
        host, slip = object(), _Owner(self.app, key="slip:u")
        self.app._open_case_views[id(host)] = {"owner": slip, "view": host}
        viewer = self._viewer("pdf", host=host)
        self.assertIs(viewer._bookmark_owner(), slip)

    def test_with_nothing_to_bookmark_there_is_nothing(self):
        self.assertIsNone(self._viewer("pdf")._bookmark_owner())

    def test_a_slip_opinion_s_text_is_bookmarked_as_the_slip_opinion(self):
        slip = _Owner(self.app, key="slip:u", label="Trump v. Anderson")
        text = object.__new__(courtlistener_gui._SlipTextWindow)
        text._bookmarks = slip
        self.assertEqual(text._bookmark_descriptor()["key"], "slip:u")
        text._toggle_bookmark()
        self.assertEqual(slip.toggled, 1)
        self.assertTrue(self.app.is_bookmarked("slip:u"))
        text._bookmarks = None
        self.assertIsNone(text._bookmark_descriptor())


if __name__ == "__main__":
    unittest.main()
