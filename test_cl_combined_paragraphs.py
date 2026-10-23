"""CourtListener's text of a PDF opinion: pre chunks with a citation link set
between each two (``html_with_citations``)."""

import os
import re
import unittest

os.environ["GETCASES_SKIP_DEPENDENCY_PROMPT"] = "1"

from cl_parse import parse_cl_html
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


def _typed_double(lines: list) -> str:
    """Typed lines a blank line apart, as a double-spaced slip opinion's."""
    return "\n\n".join(lines)


class SlipTextTests(unittest.TestCase):
    """CourtListener's text of a slip opinion kept as it was typed: its page
    numbers and form feeds, its running heads, and — typed double-spaced —
    a blank line after every line, which says nothing of paragraphs."""

    BODY = ("the court has no occasion to reach the question the parties "
            "brief at such length here,")

    def _wise(self) -> str:
        # Wise v. Circosta, 978 F.3d 93 (4th Cir. 2020) (en banc), as the
        # Fourth Circuit typed it.
        page1 = _typed_double(
            ["                         ORDER",
             "       Upon consideration of the motions, the court denies "
             "relief.",
             "       Judge Motz wrote a concurring opinion.",
             "                                  For the Court",
             "                                  /s/ Patricia S. Connor, "
             "Clerk"])
        page2 = _typed_double(
            ["WYNN, Circuit Judge, denying emergency injunctive relief:",
             "       The judges of the Fourth Circuit have done an admirable",
             "job of it, and " + self.BODY] + [self.BODY] * 30
            + ["and so we deny the injunction."])
        page3 = _typed_double(
            ["DIANA GRIBBON MOTZ, Circuit Judge, concurring in the denial "
             "of emergency",
             "injunctive relief:",
             "       I concur in full with Judge Wynn’s opinion, for"]
            + [self.BODY] * 30 + ["and I say no more."])
        return _pre(page1 + "\n\n\n\n\n                    2\n\x0c"
                    + page2 + "\n\n\n\n                    3\n\x0c" + page3)

    def test_paragraphs_by_their_indentation(self):
        texts = [b.text() for b in parse_cl_html(self._wise())[0]]
        # The byline is whole, and no page's number opens the next page —
        # "3 DIANA GRIBBON MOTZ, …" had opened nothing.
        self.assertIn("DIANA GRIBBON MOTZ, Circuit Judge, concurring in the "
                      "denial of emergency injunctive relief:", texts)
        self.assertIn("WYNN, Circuit Judge, denying emergency injunctive "
                      "relief:", texts)
        self.assertIn("/s/ Patricia S. Connor, Clerk", texts)
        self.assertFalse(any(re.match(r"\d+ ", t) for t in texts), texts)
        # A paragraph runs on over its lines, not one block to a line.
        self.assertTrue(any(t.startswith("The judges of the Fourth Circuit")
                            and t.endswith("we deny the injunction.")
                            for t in texts))

    def test_and_its_writings_are_found(self):
        parts = _assemble_case_parts(_Client(self._wise()),
                                     {"cluster_id": 1, "court_id": "ca4"})[0]
        concurrence = next(p for p in parts if p.kind == "concurrence")
        self.assertTrue(concurrence.label.startswith("DIANA GRIBBON MOTZ"))

    def test_a_running_head_opens_no_writing(self):
        # People v. Esquivias (Cal. 2025): "PEOPLE v. ESQUIVIAS / Evans, J.,
        # dissenting" atop each page of the dissent opened a phantom dissent
        # on every one, once the page number glued to it was gone.
        def page(part: str, lines: list) -> str:
            return "PEOPLE v. ESQUIVIAS\n" + part + "\n" + "\n".join(lines)

        body = [self.BODY] * 20
        html = _pre("\n                    1\n\x0c".join([
            "PEOPLE v. ESQUIVIAS\nS286371\n\nOpinion of the Court by "
            "Groban, J.\n\n" + "\n".join(body),
            page("Opinion of the Court by Groban, J.", body + ["affirmed."]),
            page("Evans, J., dissenting",
                 ["Dissenting Opinion by Justice Evans", ""] + body),
            page("Evans, J., dissenting", body),
            page("Evans, J., dissenting", body + ["I dissent."])]))
        parts = _assemble_case_parts(_Client(html), {"cluster_id": 1,
                                                     "court_id": "cal"})[0]
        self.assertLessEqual(sum(1 for p in parts if p.kind == "dissent"), 1,
                             [(p.kind, p.label) for p in parts])
        self.assertFalse(any(b.text().startswith("PEOPLE v. ESQUIVIAS")
                             for b in parse_cl_html(html)[0][1:]))


if __name__ == "__main__":
    unittest.main()
