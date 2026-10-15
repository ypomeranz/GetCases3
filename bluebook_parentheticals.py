"""The order of a citation's parentheticals (Bluebook rule 1.5(b)).

When a citation carries more than one parenthetical, the rule fixes their
order (22nd ed.):

    (date) [hereinafter short name] (en banc) (Lastname, J., concurring)
    (plurality opinion) (per curiam) (alteration in original)
    (emphasis added) (footnote omitted) (citations omitted)
    (quoting another source) (citing another source) URL (last visited)
    (explanatory parenthetical), prior or subsequent history.

The date parenthetical is part of the citation proper and is placed by the
caller; everything after it is ordered here.  A note about a quotation that
sits inside an explanatory parenthetical nests within that parenthetical
rather than following the citation: Case, 1 F.4th 2, 3 (2d Cir. 2020)
("passage" (footnote omitted)).

Pure functions with no tkinter dependency, so they test headlessly.
"""

from __future__ import annotations

import re
from typing import Iterable

# Rank of each kind of parenthetical, in the rule's order.  An explanatory
# parenthetical — anything not recognized as one of the others — ranks last.
_HEREINAFTER = 0
_EN_BANC = 1
_WRITER = 2
_PLURALITY = 3
_PER_CURIAM = 4
_ALTERATION = 5
_EMPHASIS = 6
_FOOTNOTE_OMITTED = 7
_OTHER_OMITTED = 8
_MODIFIED = 9
_QUOTING = 10
_CITING = 11
_URL = 12
_EXPLANATORY = 13

# "Scalia, J., dissenting", "Roberts, C.J., concurring in the judgment",
# "Kennedy, J., in chambers", "per curiam, concurring" — a writer and what
# the writing is.  The capitalized name and the title both have to be there,
# so an explanatory parenthetical ("holding that …", lower case by rule
# 1.5(a)) that merely mentions a judge is not mistaken for one.
_WRITER_RE = re.compile(
    r"^(?:[Pp]er\s+[Cc]uriam"
    r"|[A-Z][^,()]{0,59},\s*(?:C\.\s?J\.|J\.|JJ\.|P\.\s?J\.|V\.\s?C\.|"
    r"(?:Chief\s+|Senior\s+|Presiding\s+)?(?:Circuit\s+|District\s+)?"
    r"(?:Judge|Justice)))"
    r"\s*,\s*\S"
)
# "opinion of Rehnquist, J.", "statement of Sotomayor, J., respecting the
# denial of certiorari", and the bare "separate opinion".
_WRITER_OF_RE = re.compile(
    r"^(?:(?:opinion|statement|memorandum)\s+of\s+[^,()]{1,60},\s*"
    r"(?:c\.\s?j\.|j\.|jj\.)|separate\s+opinion$)",
    re.IGNORECASE,
)
# The words an omission note is made of: "footnote omitted", "internal
# quotation marks and citations omitted", "brackets omitted".
_OMISSION_WORDS = frozenset((
    "all", "and", "bracket", "brackets", "call", "citation", "citations",
    "ellipses", "ellipsis", "footnote", "footnotes", "internal", "mark",
    "marks", "number", "numbers", "other", "quotation", "quotations",
    "signal", "signals", "some",
))


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'")).strip()


def parenthetical_rank(text: str) -> int:
    """Where the parenthetical *text* (its contents, without the
    parentheses; a bracketed "[hereinafter …]" with its brackets) falls in
    rule 1.5(b)'s order."""
    n = _normalized(text)
    t = n.lower()
    if t.startswith("["):
        return _HEREINAFTER
    if t == "en banc":
        return _EN_BANC
    if t == "plurality opinion":
        return _PLURALITY
    if t in ("per curiam", "mem.", "table", "unpublished table decision"):
        return _PER_CURIAM
    if _WRITER_RE.match(n) or _WRITER_OF_RE.match(n):
        return _WRITER
    if re.fullmatch(r"(?:emphasis and )?alterations? in original", t):
        return _ALTERATION
    if re.fullmatch(
        r"(?:(?:first|second|third|all) )?emphasis "
        r"(?:added|omitted|in original|added and omitted)", t,
    ):
        return _EMPHASIS
    if t.endswith(" omitted"):
        words = re.findall(r"[a-z]+", t[: -len(" omitted")])
        if words and set(words) <= _OMISSION_WORDS:
            return (_FOOTNOTE_OMITTED if words[0].startswith("footnote")
                    else _OTHER_OMITTED)
    if t in ("citation modified", "cleaned up"):
        return _MODIFIED
    if re.match(r"quoting\b", t):
        return _QUOTING
    if re.match(r"citing\b", t):
        return _CITING
    if re.match(r"(?:https?://|last visited\b)", t):
        return _URL
    return _EXPLANATORY


def order_parentheticals(parentheticals: Iterable[str]) -> list[str]:
    """*parentheticals* in rule 1.5(b)'s order, blanks and repeats dropped.
    Two of the same kind keep the order they came in."""
    seen: set[str] = set()
    kept: list[str] = []
    for p in parentheticals:
        p = re.sub(r"\s+", " ", p or "").strip()
        key = _normalized(p).lower()
        if not p or key in seen:
            continue
        seen.add(key)
        kept.append(p)
    return sorted(kept, key=parenthetical_rank)


def join_parentheticals(parentheticals: Iterable[str]) -> str:
    """The ordered parentheticals as they follow a citation:
    " (Scalia, J., dissenting) (footnote omitted)"."""
    return "".join(
        f" {p}" if p.startswith("[") else f" ({p})"
        for p in order_parentheticals(parentheticals)
    )


def _group_end(text: str, start: int) -> int:
    """The index just past the bracket that closes the one at *start*,
    nested groups counted; -1 when it never closes."""
    pairs = {"(": ")", "[": "]"}
    stack: list[str] = []
    for i in range(start, len(text)):
        ch = text[i]
        if ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return i + 1
    return -1


_DATE_PAREN_RE = re.compile(r"\((?:[^()]*\s)?(?:1[5-9]|20)\d\d\)$")


def split_trailing_parentheticals(
    citation: str, search_from: int = 0,
) -> tuple[str, list[str], str]:
    """Split *citation* into (through the date parenthetical, the
    parentheticals after it, prior or subsequent history).

    The date parenthetical is the first one at or after *search_from* (the
    end of the reporter cite, when there is one) that ends in a year.  Each
    later parenthetical is returned as its contents, a bracketed one
    ("[hereinafter …]") whole; the history is whatever follows them
    (", aff'd, 2 U.S. 3 (1991)").  Without a date parenthetical the whole
    citation is the first part and there is nothing to reorder.
    """
    text = (citation or "").rstrip()
    i = max(0, search_from)
    date_end = -1
    while True:
        i = text.find("(", i)
        if i < 0:
            break
        end = _group_end(text, i)
        if end < 0:
            break
        if _DATE_PAREN_RE.match(text[i:end]):
            date_end = end
            break
        i = end
    if date_end < 0:
        return text, [], ""
    parens: list[str] = []
    pos = date_end
    while True:
        m = re.match(r"\s*([(\[])", text[pos:])
        if not m:
            break
        start = pos + m.start(1)
        end = _group_end(text, start)
        if end < 0:
            break
        group = text[start:end]
        parens.append(group[1:-1].strip() if group[0] == "(" else group)
        pos = end
    return text[:date_end], parens, text[pos:].rstrip()


# ---------------------------------------------------------------------------
# Whether a decision was the full court's: "(en banc)"
# ---------------------------------------------------------------------------
#
# Rule 10.6.1(a) marks a decision of a court that ordinarily sits in panels
# but heard this case as a whole: "(7th Cir. 2010) (en banc)".  An opinion's
# front matter is where it says so, but the words "en banc" there as often
# say the opposite — a panel opinion whose rehearing en banc was denied
# ("Rehearing and Rehearing En Banc Denied", "As Amended on Denial of
# Rehearing En Banc"), or the order denying it, with its statements and
# dissents from the denial.  Those are no decisions of the full court, and
# nor is a panel opinion later vacated for rehearing en banc ("Rehearing En
# Banc Granted"), which says nothing here of its own hearing.

_EN_BANC_WORDS = r"en\s+banc"

# The front matter of a decision denying rehearing en banc, or of one that
# records its denial: "Rehearing En Banc Denied", "As Amended on Denial of
# Rehearing and Rehearing En Banc", "voted to deny rehearing en banc", "the
# request for en banc review is denied", "dissenting from the denial of
# rehearing en banc", and the heading of the order on a petition for both
# kinds of rehearing, "On Petition for Rehearing and Rehearing En Banc".  A
# heading "On Petition for Rehearing En Banc" alone says neither way: the
# D.C. Circuit heads its en banc decisions so (PHH Corp. v. CFPB, 881 F.3d
# 75 (2018)), and an order denying the petition says so in words of its
# own.
_EN_BANC_REFUSED_RES = (
    re.compile(r"\b(?:deni(?:al|ed|es)|deny(?:ing)?)\b[^.;]{0,100}?\b"
               + _EN_BANC_WORDS, re.IGNORECASE),
    re.compile(_EN_BANC_WORDS + r"\b[^.;]{0,60}?\b(?:deni(?:al|ed)|"
               r"(?:is|are|was|were)\s+denied)\b", re.IGNORECASE),
    re.compile(r"\bon\s+(?:the\s+)?(?:[\w’']+\s+)?petitions?\s+for\s+"
               r"(?:panel\s+)?rehearing\s+(?:and|or)\s+(?:for\s+)?rehearing\s+"
               + _EN_BANC_WORDS, re.IGNORECASE),
)
# The front matter saying the full court heard the case: "Argued En Banc",
# "Reargued En Banc", "Argued and Submitted En Banc", "Submitted En Banc",
# "Reheard En Banc", "ON REHEARING EN BANC", "Upon a Rehearing En Banc".
_EN_BANC_HEARD_RES = (
    re.compile(r"\b(?:re)?(?:argued|submitted|heard)(?:\s+and\s+submitted)?"
               r"\s+" + _EN_BANC_WORDS + r"\b", re.IGNORECASE),
    re.compile(r"\b(?:on|upon)\s+(?:a\s+)?rehearing\s+" + _EN_BANC_WORDS
               + r"\b", re.IGNORECASE),
)
_BENCH_LINE_RE = re.compile(r"^(?:before|present)\b\s*:?\s*", re.IGNORECASE)
# The reporter's page markers Scholar sets ahead of a line: "*292 Before
# CLARK, Chief Judge, and HINCKS and LUMBARD, Circuit Judges."
_LEADING_STAR_PAGES_RE = re.compile(r"^(?:\*\d+[A-Za-z]{0,2}\s*)+")
# …or the bench it lists sat so: "…, Circuit Judges, sitting en banc", "…,
# Circuit Judges, En Banc", "…, Justices, constituting the court en banc",
# "Before the court en banc".
_BENCH_EN_BANC_RE = re.compile(
    r"\b(?:judges?|justices?|j{1,2}\.|court)\s*,?\s*(?:sitting\s+|"
    r"constituting\s+the\s+court\s+)?" + _EN_BANC_WORDS + r"\b",
    re.IGNORECASE)
_JUDGE_TITLE_RE = re.compile(
    r"\b(?:judges?|justices?)\b|\b(?:c\.\s?j|jj?|p\.\s?j)\.", re.IGNORECASE)
# What a bench line says of its judges besides their names.
_BENCH_WORDS_RE = re.compile(
    r"\b(?:chief|senior|presiding|circuit|district|associate|retired|active|"
    r"judges?|justices?|united|states|sitting|by|designation|en\s+banc|the|"
    r"court|constituting|all|jr|sr|ii|iii|iv)\b\.?|\bu\.\s?s\.|"
    r"\b(?:c\.\s?j|jj?|p\.\s?j)\.",
    re.IGNORECASE)


def bench_names(line: str) -> list[str]:
    """The judges a bench line names — "Before EASTERBROOK, Chief Judge,
    and BAUER, POSNER, …, Circuit Judges." gives ["EASTERBROOK", "BAUER",
    "POSNER", …] — or [] for a line that is none: it must open "Before" or
    "Present" and give its judges a title."""
    line = _LEADING_STAR_PAGES_RE.sub("", (line or "").strip())
    m = _BENCH_LINE_RE.match(line)
    if not m or not _JUDGE_TITLE_RE.search(line):
        return []
    body = re.sub(r"\[[^\]]{0,6}\]|[*†‡]", "", line[m.end():])
    # The bench ends with the sentence that names it.
    body = re.split(r"(?<![A-Z])\.\s+(?=[A-Z][a-z])", body)[0]
    names = []
    for piece in re.split(r"[,;]|\band\b|&", body):
        # "…, United States District Judge for the Eastern District of …"
        piece = re.sub(r"\b(?:for|of)\s+the\b.*", "", piece,
                       flags=re.IGNORECASE)
        name = re.sub(r"\s+", " ", _BENCH_WORDS_RE.sub(" ", piece)).strip()
        if re.search(r"(?<![^\W\d_])[^\W\d_a-z][^\W\d_]", name):
            names.append(name)
    return names


def bench_size(line: str) -> int:
    """How many judges a bench line names (see :func:`bench_names`)."""
    return len(bench_names(line))


# The D.C. Circuit lists its writings by their authors ahead of the
# opinion: "Dissenting opinion filed by Circuit Judge Kavanaugh, with whom
# Senior Circuit Judge Randolph joins."
_FILED_BY_JUDGE_RE = re.compile(
    r"\b(?:filed|written)\s+by\s+(?:(?:Chief|Senior|Presiding)\s+)?"
    r"(?:Circuit\s+|District\s+)?Judges?\s+"
    r"(?P<name>[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)*)")


def judges_named(front_matter: Iterable[str]) -> set[str]:
    """The judges the opinion's front matter names, as it names them, in
    lower case: its bench, and the authors of the writings it lists ("…
    filed by Circuit Judge Henderson")."""
    found: set[str] = set()
    for text in front_matter:
        line = _front_line(text)
        found.update(n.lower() for n in bench_names(line))
        found.update(m.group("name").rstrip(".,;:").lower()
                     for m in _FILED_BY_JUDGE_RE.finditer(line))
    return found


def _front_line(text: str) -> str:
    return _LEADING_STAR_PAGES_RE.sub("", re.sub(r"\s+", " ", text or "").strip())


def decided_en_banc(front_matter: Iterable[str],
                    separate_openings: Iterable[str] = (), *,
                    benches: Iterable[str] = (),
                    panel_court: bool = False) -> bool:
    """Whether an opinion was the decision of its court sitting en banc.

    *front_matter* is the opinion's caption, docket, court, date and bench
    lines, through the first lines of the lead opinion;
    *separate_openings* the first line of each separate writing ("WOOD,
    Circuit Judge, … dissenting from the denial of rehearing en banc").
    *benches* are further lines that may hold the bench, read for that
    alone — the list of counsel can push it well into the opinion's first
    blocks, past where a passing "the Ninth Circuit denied rehearing en
    banc" should be taken as anything about this decision.
    *panel_court* says the court ordinarily sits in panels of three — a
    federal court of appeals — so that a bench of more names the whole
    court even where the front matter does not say so (Google Scholar's
    copy of United States v. Skoien, 614 F.3d 638 (7th Cir. 2010) (en banc),
    prints its "Argued En Banc" as "Argued").  The size of a bench says
    nothing of a court that is not one: a state supreme court sits whole.

    Words of the court's own name ("Supreme Court of Washington, En Banc")
    are not taken: a court of last resort that always sits en banc says so
    of every decision, and none is cited "(en banc)" for it."""
    lines = [t for t in map(_front_line, front_matter) if t]
    openings = [_front_line(t) for t in separate_openings]
    if any(r.search(t) for t in lines + openings
           for r in _EN_BANC_REFUSED_RES):
        return False
    bench_lines = [t for t in lines + [_front_line(t) for t in benches]
                   if _BENCH_LINE_RE.match(t) and len(t) <= 600]
    if any(_BENCH_EN_BANC_RE.search(t) for t in bench_lines):
        return True
    for t in lines:
        if len(t) > 300:
            continue            # the opinion's prose, not its front matter
        if any(r.search(t) for r in _EN_BANC_HEARD_RES):
            return True
        if panel_court and re.fullmatch(_EN_BANC_WORDS + r"\.?", t,
                                        re.IGNORECASE):
            return True
    return panel_court and any(bench_size(t) > 3 for t in bench_lines)
