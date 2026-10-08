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
