"""The year a case was decided, where its source may give its term's.

A citation gives the year of decision (Bluebook rule 10.5), but the old
reports were arranged by term, and a term that opened late in one year ran
into the next: from December 1844 the Supreme Court sat from December into
spring, from October 1873 from October.  Google Scholar heads such a case
with the one year or the other, and not consistently — Luther v. Borden,
heard in the December Term 1848 and decided in 1849, is "48 U.S. 1
(1849)"; Murray's Lessee, heard in the December Term 1855 and decided in
1856, is "59 U.S. 272 (1855)".  Nothing in the page tells the two apart
once it prints no decision date.

So such a year is only provisional (:func:`term_year_uncertain`), and the
case's decision year is asked elsewhere: of a sample of the opinions citing
it (:func:`citing_sample_year`), then CourtListener's date, then the
Caselaw Access Project's.  An answer is taken only when it is the year given
or the next (:func:`plausible_year`): the decision cannot precede its term,
nor come a term after it.

What was found is kept on disk, so a case is asked about once.  Tkinter-free:
the text view asks on a worker thread, passing the CourtListener client in.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

import citations
from bluebook_names import settled_year
from browser_links import NOMINATIVE_US_REPORTS, us_reports_volume

CACHE_PATH = Path.home() / ".config" / "courtlistener" / "decision_years.json"

#: The citing opinions asked: one page of CourtListener's search, the latest
#: first — a modern opinion gives the year with every full citation, where
#: the nineteenth century's mostly wrote "18 How. 272" alone.
SAMPLE_SIZE = 20
#: The fewest citing opinions that must give a year, and the share of those
#: giving either candidate year it must hold, for the sample to settle it.
MIN_CITING = 2
MIN_SHARE = (2, 3)

#: The years the Supreme Court's terms ran into the next: the December Terms
#: from 1844 and the October Terms after them, until the U.S. Reports — and
#: Google Scholar's copies — print each opinion's decision date.  A case
#: with its date printed is never in doubt (see term_year_uncertain).
SCOTUS_SPANNING_TERM_YEARS = (1844, 1890)

# A term line that opens late in its year, as a reporter heads the cases of
# that term: "December Term, 1855.", "OCTOBER TERM, 1877".
_SPANNING_TERM_LINE_RE = re.compile(
    r"\b(?:September|October|November|December)\s+Term,?\s+"
    r"(1[6-9]\d{2})\b", re.IGNORECASE)

_lock = threading.Lock()
_cache: Optional[dict] = None
_cache_path: Optional[Path] = None


def term_year_uncertain(blocks, year: str, *, is_scotus: bool = False) -> bool:
    """Whether *year*, the year an opinion's source gives it, may be its
    term's rather than its decision's: the opinion's front matter prints no
    decision date, and either the case is the Supreme Court's from a term
    that ran into the next year (SCOTUS_SPANNING_TERM_YEARS) or the front
    matter heads it with a term opening late in *year* ("December Term,
    1855.")."""
    if not re.fullmatch(r"1[6-9]\d{2}", year or ""):
        return False
    from opinion_db import decision_date_from_blocks
    if decision_date_from_blocks(blocks):
        return False
    first, last = SCOTUS_SPANNING_TERM_YEARS
    if is_scotus and first <= int(year) <= last:
        return True
    for block in list(blocks or ())[:16]:
        if getattr(block, "kind", None) not in ("center", "heading"):
            continue
        m = _SPANNING_TERM_LINE_RE.search(block.text())
        if m and m.group(1) == year:
            return True
    return False


def plausible_year(given: str, found: str) -> bool:
    """Whether *found* can be the decision year of a case whose source gives
    *given*, possibly its term's: the same year or the next."""
    if not (re.fullmatch(r"\d{4}", given or "")
            and re.fullmatch(r"\d{4}", found or "")):
        return False
    return int(found) - int(given) in (0, 1)


# ---------------------------------------------------------------------------
# What the opinions citing the case say
# ---------------------------------------------------------------------------

#: The early reporters' names as a citation abbreviates them (Bluebook table
#: T1), keyed as browser_links.NOMINATIVE_US_REPORTS keys them.
_NOMINATIVE_ABBREVIATIONS = {
    "dall": "Dall.", "cranch": "Cranch", "wheat": "Wheat.", "pet": "Pet.",
    "how": "How.", "black": "Black", "wall": "Wall.", "otto": "Otto",
}

# The year closing a citation, after any court: "(1856)", "(N.Y. 1856)".
_YEAR_PAREN_RE = re.compile(
    r"\s*\((?:[^()]{0,40}?[\s.])?(1[6-9]\d{2}|20\d{2})\)")
_PARALLEL_JOIN_RE = re.compile(r"\s*,\s*")


def _cite_parts(cite: str) -> Optional[tuple[str, str, str]]:
    m = re.fullmatch(r"\s*(\d{1,4})\s+(.+?)\s+(\d{1,5})\s*", cite or "")
    return (m.group(1), m.group(2), m.group(3)) if m else None


def _cite_key(cite: str) -> str:
    """The case *cite* names, however written: an early Supreme Court
    reporter's citation as its U.S. Reports one ("18 How. 272" and "59 U. S.
    272" are "59us272"), any other with spacing and punctuation ignored."""
    parts = _cite_parts(cite)
    if parts:
        vol, rep, page = parts
        us = us_reports_volume(int(vol), rep)
        if us:
            return f"{us}us{page}"
    return re.sub(r"[\W_]+", "", (cite or "").lower())


def citing_phrases(cite: str) -> list[str]:
    """The case at *cite* as the opinions citing it write it, as
    CourtListener phrase-searches it: a Supreme Court case's U.S. Reports
    citation and its reporter's own ("59 U.S. 272", "18 How. 272"), which
    between them find "59 U.S. (18 How.) 272" and "18 How. (59 U.S.) 272"
    too; any other case's citation as given."""
    parts = _cite_parts(cite)
    if not parts:
        return []
    vol, rep, page = parts
    us = us_reports_volume(int(vol), rep)
    if not us:
        return [re.sub(r"\s+", " ", cite).strip()]
    phrases = [f"{us} U.S. {page}"]
    for key, name in _NOMINATIVE_ABBREVIATIONS.items():
        offset, volumes = NOMINATIVE_US_REPORTS[key]
        if offset < us <= offset + volumes:
            phrases.append(f"{us - offset} {name} {page}")
    return phrases


def citing_query(cite: str, opinion_ids) -> str:
    """The CourtListener search for the opinions citing one of
    *opinion_ids* (the case's) that write its citation: the snippet each
    result highlights is then that citation, with the year after it."""
    graph = "(" + " OR ".join(f"cites:{int(i)}" for i in opinion_ids) + ")"
    phrases = " OR ".join(f'"{p}"' for p in citing_phrases(cite))
    return f"{graph} AND ({phrases})"


def years_cited(text: str, cite: str) -> list[str]:
    """The year after each full citation of the case at *cite* in *text* —
    a citing opinion, or CourtListener's highlighted snippet of one — past a
    pin cite and any parallel citation: "59 U.S. (18 How.) 272, 276-77, 15
    L. Ed. 372 (1856)" gives "1856"."""
    plain = html.unescape(re.sub(r"<[^>]+>", "", text or ""))
    plain = re.sub(r"\s+", " ", plain)
    key = _cite_key(cite)
    matches = citations.iter_case_citations(plain)
    starts = {m.start(): m for m in matches}
    years: list[str] = []
    for m in matches:
        if _cite_key(citations.case_match_text(m)) != key:
            continue
        end = m.end()
        while True:
            _pin, after = citations.pin_after(plain, end)
            if after > end:
                end = after
                continue
            join = _PARALLEL_JOIN_RE.match(plain, end)
            parallel = starts.get(join.end()) if join else None
            if parallel is None:
                break
            end = parallel.end()
        year = _YEAR_PAREN_RE.match(plain, end)
        if year:
            years.append(year.group(1))
    return years


def count_citing_years(results, cite: str, given: str) -> dict[str, int]:
    """How many of the opinions in *results* — a CourtListener search's, see
    citing_query — cite the case at *cite*, whose source gives *given*, by
    each year it can have been decided in (see plausible_year).  An opinion
    counts by the year its snippet's citation gives; a snippet repeated
    word for word (the same opinion filed twice) counts once."""
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for result in results or ():
        for opinion in (result or {}).get("opinions") or ():
            snippet = re.sub(r"\s+", " ", str((opinion or {}).get("snippet")
                                              or "")).strip()
            if not snippet or snippet in seen:
                continue
            seen.add(snippet)
            years = years_cited(snippet, cite)
            if years and plausible_year(given, years[0]):
                counts[years[0]] = counts.get(years[0], 0) + 1
    return counts


def pick_year(counts: dict) -> str:
    """The year a clear majority of the citing opinions give — at least
    MIN_CITING of them, and MIN_SHARE of all those giving a candidate year —
    or "" when they do not settle it."""
    total = sum(counts.values())
    num, den = MIN_SHARE
    for year, n in counts.items():
        if n >= MIN_CITING and n * den >= num * total:
            return year
    return ""


def citing_sample_year(client, cite: str, opinion_ids, given: str, *,
                       court: str) -> tuple[str, dict[str, int]]:
    """``(year, counts)``: the decision year of the case at *cite*, whose
    source gives *given*, as the latest opinions citing it (one of
    *opinion_ids*, the case's own) cite it — "" when they do not settle it
    (see pick_year) — and how many gave each candidate year.

    Only the opinions of *court*, the CourtListener id of the court that
    decided the case, are asked: other courts often give the term's year,
    after the citators.  Of the latest twenty opinions citing Ex parte
    Lange, 85 U.S. 163, decided January 30, 1874, fourteen give it (1873);
    the Supreme Court's own give 1874, all fifteen.  No court, no sample.
    A search that fails raises: no answer is no evidence."""
    if not court or not opinion_ids or not citing_phrases(cite):
        return "", {}
    data = client.search(citing_query(cite, opinion_ids), type="o",
                         court=court, highlight=True, page_size=SAMPLE_SIZE,
                         extra={"order_by": "dateFiled desc"})
    results = data.get("results") if isinstance(data, dict) else None
    counts = count_citing_years(results, cite, given)
    return pick_year(counts), counts


# ---------------------------------------------------------------------------
# What has been found already
# ---------------------------------------------------------------------------

def cache_key(cite: str) -> str:
    """The case's key on disk: its citation, however spaced ("59 U.S. 272"
    or "59 U. S. 272")."""
    return re.sub(r"\s+", "", cite or "")


def _loaded(path: Optional[Path]) -> dict:
    """The answers on disk, read once per file (callers hold _lock)."""
    global _cache, _cache_path
    path = path or CACHE_PATH
    if _cache is None or _cache_path != path:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        _cache = data if isinstance(data, dict) else {}
        _cache_path = path
    return _cache


def remembered_year(*cites: str, path: Optional[Path] = None) -> str:
    """The decision year found before for the case at any of *cites*, or
    "" when it has not been asked about."""
    with _lock:
        data = _loaded(path)
        for cite in cites:
            entry = data.get(cache_key(str(cite or "")))
            if isinstance(entry, dict) and re.fullmatch(
                    r"\d{4}", str(entry.get("year") or "")):
                return str(entry["year"])
    return ""


def remember_year(cite: str, year: str, source: str,
                  path: Optional[Path] = None,
                  counts: Optional[dict] = None) -> None:
    """Keep the decision year *source* gave for the case at *cite*, and
    what the citing opinions counted (see citing_sample_year) where they
    were asked.  Failing to write is no failure: the case is only asked
    about again."""
    path = path or CACHE_PATH
    with _lock:
        data = _loaded(path)
        entry = {"year": year, "source": source,
                 "checked": time.strftime("%Y-%m-%d")}
        if counts:
            entry["counts"] = dict(counts)
        data[cache_key(cite)] = entry
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=1, sort_keys=True),
                            encoding="utf-8")
        except Exception as exc:
            print(f"[decision-year] could not save {path}: {exc}")


def remembered_decision_year(given: str, *cites: str) -> str:
    """The decision year found before for the case at any of *cites* whose
    source gives *given*, when it can be (see plausible_year), or ""."""
    found = remembered_year(*cites)
    return found if plausible_year(given, found) else ""


def known_decision_year(given: str, *cites: str) -> str:
    """The decision year already known for the case at any of *cites* whose
    source gives *given* — one the courts' citations settle
    (bluebook_names.settled_year), or one found before — or ""."""
    return settled_year(*cites) or remembered_decision_year(given, *cites)
