"""Volumes static.case.law scanned from a reprint that sets two or more of
the original's pages on each page of its own.

Banks & Brothers reprinted 17 and 18 Wend. that way (1864, 1860), and J. B.
Steel 17 La. (1854), with 18 La. bound in after it.  A page
of the reprint prints its own number at the foot, the original's at the
head, and an original page's number in brackets where that page begins —
"[80] the trial.  The jury found a verdict …".  The reports cite the
original's pages.  static.case.law names each case's files by the reprint's
page, and its list of 18 Wend.'s cases cites them by it too: Judges of the
Oneida Common Pleas v. People ex rel. Savage, 18 Wend. 79, is its file
0045-01, listed as "18 Wend. 45" — and so it is on CourtListener, which took
its citations from that list.  18 Wend. 79 falls inside Noyes v. Hewitt
there (pages 77–80 of the reprint), which is the case a link to it opened.
(17 Wend.'s list cites the original's pages; only its files and the pages
of its scans are the reprint's.)

17 La.'s list cites the cases of the 18 La. bound in with it as 17 La.'s:
Leefe v. Walker, 18 La. 1, is listed "17 La. 1" beside Fleming v. Hill,
which is 17 La. 1 — and on CourtListener too.  (static.case.law has 18 La.
as a volume of its own as well, scanned page for page.)

``case_law_reprints.tsv.gz`` (built by ``_case_law_build/
build_reprint_index.py`` from the bracketed numbers in the cases' texts)
lists every case of those volumes, in the order of the volume.  Rows,
tab-separated:

    slug  vol  file  book  listed  first  last  heads  name

*book* is the volume the case was printed in (*vol*, but for a volume bound
in with it); *listed* the page static.case.law's list cites the case at;
*first* and *last* the original pages it runs, in *book*; *heads* the first
original page that
begins on each page of its scan, comma-separated ("" where none begins on a
page, which is all one original page); *name* the list's name for it.

The module is pure — no network, no GUI — and import-safe without the index
(nothing is then a reprint).
"""

from __future__ import annotations

import bisect
import gzip
import os
import re
import sys
import threading
from typing import NamedTuple, Optional

INDEX_FILENAME = "case_law_reprints.tsv.gz"

#: An original page's number as the reprint sets it in the text: "[80]".
MARK_RE = re.compile(r"\[(\d{1,4})\]")

#: How many of the original's page numbers in a row the reprint's OCR may
#: lose: a number past the last by more than this is a misreading ("[638]"
#: for 538), not a page some lost numbers lie before.
_MARK_GAP = 6

#: The most pages a run of numbers may jump where the ones between were
#: lost — further is a misreading (17 La.'s "[881]" for 381).
_MARK_JUMP = 40


def _one_figure_off(a: int, b: int) -> bool:
    """Whether *a* and *b* have as many figures and differ in one."""
    x, y = str(a), str(b)
    return len(x) == len(y) and sum(p != q for p, q in zip(x, y)) == 1

#: How citations name each reprinted volume's reporter.
_REPORTERS = {"wend": r"Wend(?:ell)?\.?", "la": r"La\.?"}


class ReprintCase(NamedTuple):
    stem: str           # static.case.law's file, "0045-01"
    listed: int         # the page its list cites the case at
    first: int          # the original pages the case runs
    last: int
    heads: tuple        # per page of its scan: the first original page
    #                     beginning on it, or None
    name: str = ""
    book: str = ""      # the volume it was printed in

    @property
    def page(self) -> int:
        """The reprint's page the case begins on — its file's number."""
        return int(self.stem.split("-")[0])

    def pdf_page(self, page: int) -> int:
        """Which page of the case's scan, 0-based, has original *page* —
        where it begins, or the page it runs on from."""
        found = 0
        for index, head in enumerate(self.heads):
            if head is None:
                continue
            if head == page:
                return index
            if head > page:
                break
            found = index
        return found


class _Index:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._volumes: "Optional[dict]" = None

    def volumes(self) -> dict:
        with self._lock:
            if self._volumes is None:
                self._volumes = _load(_index_path())
            return self._volumes


def _index_path() -> str:
    base = getattr(sys, "_MEIPASS", "") or os.path.dirname(
        os.path.abspath(__file__))
    return os.path.join(base, INDEX_FILENAME)


def _load(path: str) -> dict:
    volumes: dict = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                cols = line.rstrip("\n").split("\t")
                if len(cols) != 9 or line.startswith("#"):
                    continue
                slug, vol, stem, book, listed, first, last, heads, name = cols
                try:
                    case = ReprintCase(
                        stem, int(listed), int(first), int(last),
                        tuple(int(h) if h else None
                              for h in heads.split(",")), name, book)
                except ValueError:
                    continue
                volumes.setdefault((slug, vol), []).append(case)
    except OSError:
        return {}
    return {key: tuple(cases) for key, cases in volumes.items()}


_INDEX = _Index()


def volume(slug: str, vol: str) -> tuple:
    """Every case of a reprinted volume, in its order — () for a volume
    static.case.law scanned page for page."""
    return _INDEX.volumes().get((str(slug).lower(), str(vol)), ())


def case_of_file(slug: str, vol: str, stem: str) -> Optional[ReprintCase]:
    """The reprinted volume's case kept in file *stem*, or None."""
    return next((c for c in volume(slug, vol) if c.stem == stem), None)


def cases_at(slug: str, vol: str, page: int,
             book: "Optional[str]" = None) -> list:
    """The cases of volume *vol* — of *book*, where another is bound in with
    it — that begin at original *page*."""
    book = str(vol if book is None else book)
    return [c for c in volume(slug, vol) if c.first == page and c.book == book]


def case_over(slug: str, vol: str, page: int) -> Optional[ReprintCase]:
    """The case original *page* of volume *vol* is a page of: the last to
    begin at or before it, if it runs that far."""
    cases = [c for c in volume(slug, vol) if c.book == str(vol)]
    at = bisect.bisect_right([c.first for c in cases], page)
    if not at:
        return None
    case = cases[at - 1]
    return case if page <= case.last else None


def _relisted(case: ReprintCase, vol: str) -> bool:
    """Whether static.case.law's list cites *case* by another page, or
    another volume, than the one it was printed at."""
    return (case.book, case.first) != (str(vol), case.listed)


def listed_page(slug: str, vol: str, page: int) -> Optional[int]:
    """The page static.case.law's list — and CourtListener — has for
    original *page* of volume *vol*, where it is not *page* itself: the
    listed page of a case beginning there, and the reprint's page it is
    printed on where a case runs over it.  None where the list cites the
    page as it is."""
    begun = cases_at(slug, vol, page)
    if begun:
        return begun[0].listed if _relisted(begun[0], vol) else None
    case = case_over(slug, vol, page)
    if case is None or not _relisted(case, vol):
        return None
    return case.page + case.pdf_page(page)


def _words(name: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", (name or "").lower())
            if w not in ("the", "and", "ex", "rel", "people")}


def listed_case(slug: str, vol: str, listed: int,
                name: str = "") -> Optional[ReprintCase]:
    """The case static.case.law's list cites at *listed* that a citation
    CourtListener took from that list means: the one there, or of several,
    the one *name* shares the most words with.  None where there is none, or
    nothing to tell them by."""
    cases = [c for c in volume(slug, vol) if c.listed == listed]
    if len(cases) <= 1:
        return cases[0] if cases else None
    asked = _words(name)
    rated = sorted(((len(asked & _words(c.name)), c) for c in cases),
                   key=lambda row: -row[0])
    if not asked or rated[0][0] == 0 or rated[0][0] == rated[1][0]:
        return None
    return rated[0][1]


def _cite_re(slug: str) -> "Optional[re.Pattern]":
    reporter = _REPORTERS.get(slug)
    if reporter is None:
        return None
    return re.compile(r"\b(\d{1,3})(\s+)(" + reporter + r")(\s*,?\s*)(\d{1,4})\b")


def _slugs() -> list:
    return sorted({slug for slug, _vol in _INDEX.volumes()
                   if slug in _REPORTERS})


def asked(text: str) -> str:
    """*text* with each citation of a relisted volume's original page put
    as static.case.law's list has it — "18 Wend. 79" → "18 Wend. 45" — to
    ask CourtListener by."""
    for slug in _slugs():
        def listed(m: re.Match) -> str:
            page = listed_page(slug, m.group(1), int(m.group(5)))
            return m.group(0) if page is None else (
                m.group(1) + m.group(2) + m.group(3) + m.group(4) + str(page))
        text = _cite_re(slug).sub(listed, text)
    return text


def original_cite(cite: str, name: str = "") -> str:
    """*cite*, as static.case.law's list or CourtListener gives it, by the
    volume and page its case was printed at — "18 Wend. 45" → "18 Wend. 79",
    and for Leefe v. Walker "17 La. 1" → "18 La. 1" — for the case *name*
    names where the list puts several at the page."""
    for slug in _slugs():
        m = _cite_re(slug).fullmatch(str(cite or "").strip())
        if m:
            case = listed_case(slug, m.group(1), int(m.group(5)), name)
            if case is not None and _relisted(case, m.group(1)):
                return (case.book + m.group(2) + m.group(3) + m.group(4)
                        + str(case.first))
    return cite


def _keys(text: str) -> set:
    """The citations of reprinted volumes' reporters in *text*, as
    ``(volume, reporter's slug, page)``."""
    return {(m.group(1), slug, m.group(5)) for slug in _slugs()
            for m in _cite_re(slug).finditer(str(text or ""))}


def _cluster_keys(cluster: dict) -> set:
    found: set = set()
    for cite in cluster.get("citations") or ():
        if isinstance(cite, dict):
            cite = (f"{cite.get('volume')} {cite.get('reporter')} "
                    f"{cite.get('page')}")
        found |= _keys(cite)
    return found


def answers(entries, text: str):
    """CourtListener's citation-lookup *entries* for *text*, read (see read)
    — less the cases it gives only because static.case.law's list put them
    in the volume asked for: asked for 17 La. 1, the 18 La. case listed
    there, Leefe v. Walker, is no answer.  *entries* are changed, and
    returned."""
    if not _slugs() or not isinstance(entries, list):
        return read(entries)
    asked_for = _keys(text)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        clusters = entry.get("clusters")
        if not isinstance(clusters, list):
            continue
        kept = []
        for cluster in clusters:
            before = _cluster_keys(cluster) if isinstance(cluster, dict) \
                else set()
            read(cluster)
            after = _cluster_keys(cluster) if isinstance(cluster, dict) \
                else set()
            if (before - after) & asked_for and not after & asked_for:
                continue
            kept.append(cluster)
        entry["clusters"] = kept
    return entries


def read(data):
    """A CourtListener answer with its citations of relisted volumes put by
    the original's pages (see original_cite) — a search result's
    ``citation`` strings, a cluster's ``citations`` records.  *data* itself
    is changed, and returned."""
    if not _slugs():
        return data
    if isinstance(data, list):
        for item in data:
            read(item)
        return data
    if not isinstance(data, dict):
        return data
    name = str(data.get("caseName") or data.get("case_name")
               or data.get("case_name_full") or "")
    for key, value in data.items():
        if key in ("citation", "citations") and isinstance(value, list):
            for i, cite in enumerate(value):
                if isinstance(cite, str):
                    value[i] = original_cite(cite, name)
                elif isinstance(cite, dict) and cite.get("page") is not None:
                    text = (f"{cite.get('volume')} {cite.get('reporter')} "
                            f"{cite.get('page')}")
                    fixed = original_cite(text, name)
                    if fixed != text:
                        book, page = fixed.split()[0], fixed.split()[-1]
                        cite["page"] = type(cite["page"])(page)
                        if cite.get("volume") is not None:
                            cite["volume"] = type(cite["volume"])(book)
        elif isinstance(value, (dict, list)):
            read(value)
    return data


def clean_marks(values) -> list:
    """The original's page numbers as the reprint's text gives them, in
    order, with OCR's misreadings mended: each comes back as the page it
    marks, or None for a number that marks no page.

    The numbers run one after another.  One that breaks the run where the
    next resumes it is the page between misread ("[638]" between 537 and
    539 is 538); one ahead of the run by a few, or that the next carries on
    from, is a page whose predecessors' numbers were lost; anything else is
    set aside.  A number given twice, where the first skipped one, is the
    skipped page's misread the first time ("[540] [541] [543] [543] [544]"
    marks 542 and 543).  One far off the run that is a page just ahead of
    it with one figure misread is that page ("[881] [882] [884]" after 380
    are 381, 382 and 384)."""
    values = list(values)
    out: list = []
    prev: Optional[int] = None
    step = 1            # how far the last page taken was from the one before
    for i, value in enumerate(values):
        nxt = values[i + 1] if i + 1 < len(values) else None
        near = [page for page in range(prev + 1, prev + _MARK_GAP + 1)
                if _one_figure_off(value, page)] if prev is not None else []
        if prev is None or value == prev + 1:
            page = value
        elif value == prev and step == 2 and nxt in (None, value + 1):
            at = max(j for j, p in enumerate(out) if p is not None)
            out[at] = prev - 1
            page = value
        elif nxt == prev + 2:
            page = prev + 1
        elif abs(value - prev) > _MARK_JUMP and near:
            page = near[0]
        elif prev < value <= prev + _MARK_JUMP and (
                nxt is None and value <= prev + _MARK_GAP
                or nxt is not None and value < nxt <= value + _MARK_GAP
                and (value <= prev + _MARK_GAP or nxt <= value + 2)):
            page = value
        else:
            page = None
        out.append(page)
        if page is not None:
            step = page - prev if prev is not None else 1
            prev = page
    return out
