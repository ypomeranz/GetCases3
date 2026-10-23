"""The surnames federal judges go by, from the Federal Judicial Center.

A byline prints a judge's whole name — "DIANA GRIBBON MOTZ, Circuit Judge,
concurring" — and the writer parenthetical names her by surname alone:
(Motz, J., concurring).  The words before a surname are not all given
names: Gribbon is Judge Motz's maiden name, McLane Judge Wardlaw's, Lipton
Judge Hollander's, Araújo Judge Kahn's; and a surname may be two words —
Delgado Hernández, Van Antwerpen, St. Eve, de Alba.  Neither the last word
of a name nor the words after its given names finds them all.  The FJC's
Biographical Directory of Article III Federal Judges records each federal
judge's name in its parts, and this module reads it.

``federal_judges.tsv.gz`` holds one judge to a line — last name, first
name, middle name, tab-separated, as the directory gives them.  It is a
public-domain work of the United States government; rebuild it with

    python federal_judges.py judges.csv

from https://www.fjc.gov/sites/default/files/history/judges.csv.

The directory is not the last word on what a judge is called: it files
Yvonne Gonzalez Rogers as "Rogers", though her court and the Ninth Circuit
call her Judge Gonzalez Rogers.  The name an opinion itself gives a judge
("Opinion by Judge Gonzalez Rogers") is read before this.
"""

from __future__ import annotations

import csv
import gzip
import os
import re
import sys
import unicodedata

FILENAME = "federal_judges.tsv.gz"

#: last-name key → [(the last name as the FJC writes it, first-name key,
#: middle-name keys)], loaded on first use.
_JUDGES: "dict[str, list[tuple[str, str, tuple[str, ...]]]] | None" = None

#: The generational suffix closing a name: "J. HARVIE WILKINSON III".
_SUFFIX_RE = re.compile(r"(?:Jr|Sr)\.?|I{2,3}|IV", re.IGNORECASE)


def _key(text: str) -> str:
    """*text*'s letters alone, unaccented and in lower case: "St. Eve" is
    "steve", "Hernández" "hernandez"."""
    folded = unicodedata.normalize("NFKD", text or "")
    return re.sub(r"[^a-z]", "", "".join(
        c for c in folded if not unicodedata.combining(c)).lower())


def _judges() -> "dict[str, list[tuple[str, str, tuple[str, ...]]]]":
    global _JUDGES
    if _JUDGES is None:
        table: dict = {}
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            FILENAME)
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("#"):
                        continue
                    last, first, middle = (line.rstrip("\n").split("\t")
                                           + ["", ""])[:3]
                    if _key(last):
                        table.setdefault(_key(last), []).append(
                            (last, _key(first),
                             tuple(_key(m) for m in middle.split())))
        except (OSError, EOFError, ValueError):
            table = {}
        _JUDGES = table
    return _JUDGES


def _fits(word: str, first: str, middles: "tuple[str, ...]") -> bool:
    """Whether *word*, a name's first, is the judge's first or middle name
    — or its initial: "J. Harvie Wilkinson" is James Harvie Wilkinson."""
    key = _key(word)
    if not key:
        return False
    if len(key) == 1:
        return first[:1] == key or any(m[:1] == key for m in middles)
    return key == first or key in middles


def surname(words: "list[str]") -> str:
    """The surname of the federal judge a byline's name *words* names —
    ["Diana", "Gribbon", "Motz"] is "Motz", ["Pedro", "A.", "Delgado",
    "Hernández"] "Delgado Hernández", ["Ana", "De", "Alba"] "de Alba" — or
    "" where no federal judge of the name has the first name it opens with.
    The longest surname that fits is taken, as the name prints it unless
    the FJC's differs only in its capitals ("de Alba")."""
    words = list(words)
    while len(words) > 1 and _SUFFIX_RE.fullmatch(words[-1]):
        words.pop()
    if len(words) < 2:
        return ""
    table = _judges()
    for k in range(1, len(words)):
        written = " ".join(words[k:])
        for last, first, middles in table.get(_key(written), ()):
            if _fits(words[0], first, middles):
                return last if last.casefold() == written.casefold() \
                    else written
    return ""


def build(csv_path: str, out_path: str = "") -> int:
    """Write the table from the FJC's ``judges.csv``; the number of judges."""
    out_path = out_path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), FILENAME)
    with open(csv_path, encoding="utf-8", newline="") as fh:
        rows = {(r["Last Name"].strip(), r["First Name"].strip(),
                 r["Middle Name"].strip())
                for r in csv.DictReader(fh) if r["Last Name"].strip()}
    with gzip.open(out_path, "wt", encoding="utf-8", newline="\n") as out:
        out.write("# The names of federal judges, from the Federal Judicial "
                  "Center's\n# Biographical Directory of Article III Federal "
                  "Judges (judges.csv),\n# a public-domain work of the "
                  "United States government: last name,\n# first name, "
                  "middle name, tab-separated.\n")
        for row in sorted(rows):
            out.write("\t".join(row) + "\n")
    return len(rows)


if __name__ == "__main__":
    print(build(*sys.argv[1:3]), "judges")
