"""A citation of a page inside a case, as the old reports cite.

Corfield v. Coryell, 6 F. Cas. 546 (C.C.E.D. Pa. 1823): "So as to trover. 1
Chit. Pl. 150; 8 Johns. 435; 7 Johns. 9; … 11 Johns. 385" — no case begins at
8 Johns. 435, the fourth page of Putnam v. Wyley, 8 Johns. 432–36.
static.case.law's list of the volume's cases says which case runs over the
page.
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("GETCASES_SKIP_DEPENDENCY_PROMPT", "1")
try:  # the app itself needs tkinter, which a headless run may not have
    import courtlistener_gui as gui
except Exception:  # pragma: no cover - depends on the machine
    gui = None

# 8 Johns. as CasesMetadata.json lists it (cut down): each citation's files,
# each file's case, and the pages each runs (0 where the list doesn't say).
VOLUME = (
    {"8johns430": ("0430-01",), "8johns432": ("0432-01",),
     "8johns436": ("0436-01",), "8johns440": ("0440-01",),
     "8johns450": ("0450-01", "0450-02"),
     # Another volume's citation of a case (a parallel) is no page here.
     "18johns433": ("0430-01",)},
    {"0430-01": "Jackson v. Smith", "0432-01": "Putnam v. Wyley",
     "0436-01": "Carter v. Simpson", "0440-01": "Hart v. Ten Eyck",
     "0450-01": "Doe v. Roe", "0450-02": "Van Brunt v. Schenck"},
    {"0430-01": 2, "0432-01": 5, "0436-01": 2, "0440-01": 0,
     "0450-01": 1, "0450-02": 6},
)


@unittest.skipIf(gui is None, "courtlistener_gui needs tkinter")
class CaseContainingPageTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(gui, "_case_law_volume",
                                    return_value=VOLUME)
        patcher.start()
        self.addCleanup(patcher.stop)

    def inside(self, cite, name=""):
        return gui._case_law_case_containing_page(cite, name)

    def test_the_case_the_page_lies_in(self):
        self.assertEqual(self.inside("8 Johns. 435"),
                         ("8 Johns. 432", "Putnam v. Wyley"))
        # A pin after it changes nothing.
        self.assertEqual(self.inside("8 Johns. 435@436"),
                         ("8 Johns. 432", "Putnam v. Wyley"))

    def test_a_page_a_case_begins_on_is_that_case(self):
        self.assertIsNone(self.inside("8 Johns. 436"))

    def test_a_page_past_the_case_s_end_is_none_of_them(self):
        # Carter v. Simpson runs 436–37; nothing the list knows begins
        # before 438 and runs on to it.
        self.assertIsNone(self.inside("8 Johns. 438"))
        self.assertIsNone(self.inside("8 Johns. 12"))

    def test_a_case_whose_length_is_not_listed(self):
        self.assertEqual(self.inside("8 Johns. 445"),
                         ("8 Johns. 440", "Hart v. Ten Eyck"))

    def test_the_last_of_several_begun_on_one_page(self):
        self.assertEqual(self.inside("8 Johns. 453"),
                         ("8 Johns. 450", "Van Brunt v. Schenck"))

    def test_a_name_that_is_not_the_case_s(self):
        self.assertEqual(self.inside("8 Johns. 435", "Putnam v. Wyley"),
                         ("8 Johns. 432", "Putnam v. Wyley"))
        self.assertIsNone(self.inside("8 Johns. 435", "Kilham v. Ward"))

    def test_no_list_no_answer(self):
        with mock.patch.object(gui, "_case_law_volume",
                               return_value=({}, {}, {})):
            self.assertIsNone(self.inside("8 Johns. 435"))


if __name__ == "__main__":
    unittest.main()
