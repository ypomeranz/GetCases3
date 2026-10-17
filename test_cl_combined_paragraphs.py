"""CourtListener's text of a PDF opinion: pre chunks with a citation link set
between each two (``html_with_citations``)."""

import os
import unittest

os.environ["GETCASES_SKIP_DEPENDENCY_PROMPT"] = "1"

from courtlistener_gui import _assemble_case_parts


def _cite(text: str) -> str:
    return ('<span class="citation" data-id="1"><a href="/opinion/1/x/">'
            f"{text}</a></span>")


def _pre(text: str) -> str:
    return f'<pre class="inline">{text}</pre>'


class _Client:
    def __init__(self, html: str):
        self.html = html

    def get_cluster(self, cluster_id, fields=""):
        return {"case_name": "Acad. of Allergy & Asthma in Primary Care v. "
                             "Amerigroup Tenn., Inc.",
                "sub_opinions": ["https://cl/opinions/1/"]}

    def _get_url(self, url, params=None):
        return {"type": "010combined", "html_with_citations": self.html}


class PreChunkParagraphTests(unittest.TestCase):
    def test_paragraphs_survive_the_links_between_chunks(self):
        # 164 F.4th 529 (6th Cir. 2026): split at each of its 95 links, the
        # order denying rehearing en banc read as one "Opinion" of fragments,
        # with no paragraph a byline could open.
        filler = ("The panel considered the question with care, and the "
                  "parties briefed it at length. ") * 12
        html = (
            _pre("Before: SUTTON, Chief Judge; KETHLEDGE and MURPHY, Circuit "
                 "Judges.\n\n_________________\n\nORDER\n_________________"
                 "\n\nThe court received a petition for rehearing en banc."
                 "\n\nTherefore, the petition is denied.\n\n"
                 "__________________\n\nSTATEMENT\n__________________\n\n"
                 "JOHN K. BUSH, Circuit Judge, respecting the denial of "
                 "rehearing en banc. This case involves an alleged boycott. "
                 "See ")
            + _cite("431 U.S. 720")
            + _pre(", 735–36 (1977). " + filler + "\n\n" + filler
                   + "\n\n__________________\n\nCONCURRENCE\n"
                   "__________________\n\nMURPHY, Circuit Judge, with whom "
                   "SUTTON, Chief Judge, and KETHLEDGE, Circuit Judge, join, "
                   "concurring in the denial of rehearing en banc. Congress "
                   "has granted a cause of action. See ")
            + _cite("15 U.S.C. § 15(a)")
            + _pre(". " + filler + "\n\n" + filler)
        )
        self.assertGreater(len(html), 4000)
        parts = _assemble_case_parts(_Client(html), {"cluster_id": 1,
                                                     "court_id": "ca6"})[0]
        kinds = [(p.kind, p.label) for p in parts]
        self.assertIn(("majority", "Order"), kinds)
        statement = next(p for p in parts if p.kind == "separate")
        self.assertTrue(statement.label.startswith("JOHN K. BUSH"))
        concurrence = next(p for p in parts if p.kind == "concurrence")
        self.assertTrue(concurrence.label.startswith("MURPHY"))


if __name__ == "__main__":
    unittest.main()
