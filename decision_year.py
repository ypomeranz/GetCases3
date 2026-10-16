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
it (not yet built — see HANDOFF_decision_years.md), then CourtListener's
date, then the Caselaw Access Project's.  An answer is taken only when it is
the year given or the next (:func:`plausible_year`): the decision cannot
precede its term, nor come a term after it.

What was found is kept on disk, so a case is asked about once.  Tkinter-free:
the text view asks on a worker thread.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

from bluebook_names import settled_year

CACHE_PATH = Path.home() / ".config" / "courtlistener" / "decision_years.json"

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
                  path: Optional[Path] = None) -> None:
    """Keep the decision year *source* gave for the case at *cite*.  Failing
    to write is no failure: the case is only asked about again."""
    path = path or CACHE_PATH
    with _lock:
        data = _loaded(path)
        data[cache_key(cite)] = {"year": year, "source": source,
                                 "checked": time.strftime("%Y-%m-%d")}
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
