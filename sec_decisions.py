"""Detect citations to the SEC's Decisions and Reports ("8 S.E.C. 893") and
say where each cited page is online.

The Securities and Exchange Commission printed its formal opinions, 1934 to
2006, in 58 volumes of *Decisions and Reports*, which opinions cite as "8
S.E.C. 893, 915-921" (and "10 S. E. C. 200", "8 SEC 893", and the short
form "8 S.E.C. at 915").  HathiTrust holds every volume in full view — the
Commission's own publications are in the public domain — at

    https://babel.hathitrust.org/cgi/pt?id=<htid>&seq=<seq>

where *seq* counts the scan's pages from the front cover, so it is not the
printed page.  ``sec_index.tsv.gz`` (built by ``_sec_build/build_index.py``
from the HathiTrust Research Center's Extracted Features, which record the
words of every scanned page's running head) says which scan page each
printed page is; where a decision begins; and the year of each decision.
Rows, tab-separated:

    p  vol  first page  last page  scan page of the first  htid
    v  vol  first year  last year          (the years the volume covers)
    d  vol  page a decision begins on  its year (0: the pages don't say)

HathiTrust lets people, not scripts, turn its pages (a CloudFlare check
stands in front of them), so a citation opens HathiTrust's own viewer, in
the web browser, at the cited page.

A citation is linked only in a volume the series has (1-58) and on a page
the volume prints.  Each becomes a *spec* — ``{"vol": 8, "page": 893,
"pin": 915}`` as compact JSON — naming the decision's first page and the
page cited in it.

The module is pure — no network, no GUI — and import-safe without the index
(a citation then opens the series' catalogue record).  Run ``python -X utf8
sec_decisions.py`` for offline self-tests.
"""

from __future__ import annotations

import bisect
import gzip
import json
import os
import re
import threading
from dataclasses import dataclass

INDEX_FILENAME = "sec_index.tsv.gz"
VIEWER_URL = "https://babel.hathitrust.org/cgi/pt"
CATALOG_URL = "https://catalog.hathitrust.org/Record/011329639"
VOLUMES = range(1, 59)

#: The furthest a page may lie from the decision it is cited as a page of —
#: an "Id., at 1150" after "8 S.E.C. 893" is some other authority's page.
#: The longest decisions in the index run to some 140 pages.
PIN_WINDOW = 200

#: How far past a volume's last page a citation may point when the index
#: is missing, and so can't say where the volume ends.
_UNINDEXED_MAX_PAGE = 1500


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScanPage:
    """Where a printed page is: page *seq* of HathiTrust's scan *htid*."""
    htid: str
    seq: int

    @property
    def url(self) -> str:
        return f"{VIEWER_URL}?id={self.htid}&seq={self.seq}"


class _Index:
    def __init__(self) -> None:
        # vol -> [(first page, last page, seq of the first, htid)], in order
        self.pages: dict[int, list[tuple[int, int, int, str]]] = {}
        self.years: dict[int, tuple[int, int]] = {}
        # vol -> sorted first pages, and each one's year
        self.starts: dict[int, list[int]] = {}
        self.start_years: dict[int, dict[int, int]] = {}


_INDEX: "_Index | None" = None
_INDEX_LOCK = threading.Lock()


def _index_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), INDEX_FILENAME)


def _index() -> _Index:
    global _INDEX
    with _INDEX_LOCK:
        if _INDEX is None:
            idx = _Index()
            try:
                with gzip.open(_index_path(), "rt", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("#"):
                            continue
                        t = line.rstrip("\n").split("\t")
                        try:
                            if t[0] == "p" and len(t) >= 6:
                                idx.pages.setdefault(int(t[1]), []).append(
                                    (int(t[2]), int(t[3]), int(t[4]), t[5]))
                            elif t[0] == "v" and len(t) >= 4:
                                idx.years[int(t[1])] = (int(t[2]), int(t[3]))
                            elif t[0] == "d" and len(t) >= 4:
                                vol, page, year = int(t[1]), int(t[2]), int(t[3])
                                idx.starts.setdefault(vol, []).append(page)
                                idx.start_years.setdefault(vol, {})[page] = year
                        except ValueError:
                            continue
            except OSError:
                pass
            for rows in idx.pages.values():
                rows.sort()
            for firsts in idx.starts.values():
                firsts.sort()
            _INDEX = idx
        return _INDEX


def is_available() -> bool:
    """Whether the shipped index is present (a trimmed install may lack
    it; citations then open the series' catalogue record)."""
    return bool(_index().pages)


def warm() -> None:
    """Load the index on a background thread, off the first click's path."""
    threading.Thread(target=_index, daemon=True).start()


def last_page(vol: int) -> int:
    """The last printed page of volume *vol* the index knows (0: none)."""
    rows = _index().pages.get(vol) or []
    return rows[-1][1] if rows else 0


def has_page(vol: int, page: int) -> bool:
    """Whether volume *vol* prints a page *page*."""
    if vol not in VOLUMES or page < 1:
        return False
    if not is_available():
        return page <= _UNINDEXED_MAX_PAGE
    return page <= last_page(vol)


def locate(vol: int, page: int) -> "ScanPage | None":
    """The scan page printed *page* in volume *vol*, or None."""
    rows = _index().pages.get(vol) or []
    i = bisect.bisect_right(rows, (page, float("inf"))) - 1
    if i >= 0:
        first, last, seq, htid = rows[i]
        if first <= page <= last:
            return ScanPage(htid, seq + page - first)
    return None


def decision_start(vol: int, page: int) -> int:
    """The first page of the decision *page* is a page of (0: unknown)."""
    firsts = _index().starts.get(vol) or []
    i = bisect.bisect_right(firsts, page) - 1
    return firsts[i] if i >= 0 else 0


def decision_year(vol: int, page: int) -> int:
    """The year of the decision holding *page*: its own, as the index has
    it, else the volume's where the volume covers a single year (0: not
    known)."""
    start = decision_start(vol, page)
    year = _index().start_years.get(vol, {}).get(start, 0) if start else 0
    if not year:
        first, last = _index().years.get(vol, (0, 0))
        if first and first == last:
            year = first
    return year


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------

def make_spec(vol: int, page: int, pin: "int | str | None" = None) -> str:
    """The action value for a citation: compact JSON, keys sorted, a pin
    that is the first page itself left out."""
    fields = {"vol": int(vol), "page": int(page)}
    try:
        pin_n = int(str(pin)) if pin not in (None, "") else 0
    except ValueError:
        pin_n = 0
    if pin_n and pin_n != int(page):
        fields["pin"] = pin_n
    return json.dumps(fields, sort_keys=True, separators=(",", ":"))


def parse_spec(spec: "str | dict") -> dict:
    if isinstance(spec, dict):
        return dict(spec)
    try:
        value = json.loads(spec or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def with_page(spec: "str | dict", page: "str | int") -> str:
    """*spec* opened at another page of the same decision — an "Id., at
    917" after it."""
    s = parse_spec(spec)
    m = re.match(r"\d+", str(page or "").strip())
    if not m:
        return make_spec(s.get("vol", 0), s.get("page", 0), s.get("pin"))
    return make_spec(s.get("vol", 0), s.get("page", 0), int(m.group(0)))


def base_spec(spec: "str | dict") -> str:
    """*spec* without its pin: the decision itself."""
    s = parse_spec(spec)
    return make_spec(s.get("vol", 0), s.get("page", 0))


def pin_in_range(spec: "str | dict", pin: "str | int") -> bool:
    """Whether *pin* can be a page of the decision *spec* names: on or after
    its first page, within :data:`PIN_WINDOW`, and in the volume."""
    s = parse_spec(spec)
    try:
        vol, first, n = int(s.get("vol", 0)), int(s.get("page", 0)), int(str(pin))
    except (TypeError, ValueError):
        return False
    return first <= n <= first + PIN_WINDOW and has_page(vol, n)


def spec_label(spec: "str | dict", *, with_pin: bool = True) -> str:
    """The citation a spec stands for, the Bluebook's way — the title a
    link opens under: "8 S.E.C. 893, 915 (1941)"."""
    s = parse_spec(spec)
    try:
        vol, page = int(s.get("vol", 0)), int(s.get("page", 0))
    except (TypeError, ValueError):
        return ""
    if not vol or not page:
        return ""
    label = f"{vol} S.E.C. {page}"
    if with_pin and s.get("pin"):
        label += f", {s['pin']}"
    year = decision_year(vol, page)
    return label + (f" ({year})" if year else "")


def page_url(spec: "str | dict") -> str:
    """Where to read *spec* — the cited page of HathiTrust's scan, in its
    viewer; the series' catalogue record when the index can't say."""
    s = parse_spec(spec)
    try:
        vol = int(s.get("vol", 0))
        page = int(s.get("pin") or s.get("page") or 0)
    except (TypeError, ValueError):
        return CATALOG_URL
    where = locate(vol, page)
    return where.url if where is not None else CATALOG_URL


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_DASH = "-‐‑‒–—−"
# The reporter: "S.E.C." however it is spaced ("S. E. C.", "S.E. C."), or
# "SEC" written without the periods.  A citation to the SEC Docket ("45
# S.E.C. Docket 1234"), the Judicial Decisions ("1 S.E.C. Jud. Dec. 12") or
# an annual report ("22 S.E.C. Ann. Rep. 45") never has its page straight
# after the reporter, so none of them matches.
_REPORTER = r"(?:S\.\s?E\.\s?C\.|SEC(?![\w.]))"
_RANGE = r"(?:\s*[" + _DASH + r"]\s*\d{1,4}(?![\d\w]))?"
# A page is a page: not a section's "12(b)", not a form's "10-K".
_NOT_A_NAME = r"(?!\()(?!\s*[" + _DASH + r"]\s*[A-Za-z])"
SEC_CITE_RE = re.compile(
    r"(?<![\w.,])(?P<vol>\d{1,2})\s+" + _REPORTER
    + r"(?P<short>\s*,?\s*at)?\s+(?P<page>\d{1,4})(?![\d\w])" + _NOT_A_NAME
    + _RANGE
)
# A pin page (or range, or footnote) after the page, or another page after
# that — not the volume of a citation that follows (", 10 S.E.C. 200"), an
# ordinal, or a year of another reporter ("…, 1941 SEC LEXIS 12").
_PIN_RE = re.compile(
    r"\s*,\s*(?:at\s+)?(?P<pin>\d{1,4})(?![\d\w])" + _NOT_A_NAME + _RANGE
    # "915 n.3", "915 nn.3-4, 7" (a list of notes goes with "nn." only)
    + r"(?:\s*n\.\s*\d{1,3}(?:\s*[" + _DASH + r"]\s*\d{1,3})?"
    + r"|\s*nn\.\s*\d{1,3}(?:\s*[" + _DASH + r",&]\s*\d{1,3})*)?"
    r"(?!\s*(?:st|nd|rd|th|d)\b)(?!\s+[A-Z][A-Za-z]*\.)(?!\s+SEC\b)"
)
_ANY_RE = re.compile(r"S\.\s?E\.\s?C\.|SEC")


def _pins(text: str, pos: int) -> "list[tuple[int, int, int, int]]":
    """The pin pages written after position *pos*, each as ``(start of its
    comma, start of its number, end, page)``."""
    out = []
    while True:
        m = _PIN_RE.match(text, pos)
        if not m:
            return out
        out.append((m.start(), m.start("pin"), m.end(), int(m.group("pin"))))
        pos = m.end()


def iter_cites(text: str) -> "list[tuple[int, int, str]]":
    """Every citation to the Decisions and Reports in *text* worth a link,
    as ``(start, end, spec)`` in document order.

    A full citation's link runs through its first pin ("8 S.E.C. 893,
    915-921" opens at 915); each further pin of a list is a link of its own
    ("10 S.E.C. 200, 205, 207").  A short form ("8 S.E.C. at 915") is a page
    of the decision last cited in full in that volume at or before the page
    — or, with none, of the decision the index says the page is in."""
    if not text or not _ANY_RE.search(text):
        return []
    out: list[tuple[int, int, str]] = []
    cited: dict[int, list[int]] = {}        # vol -> first pages cited in full
    for m in SEC_CITE_RE.finditer(text):
        vol, page = int(m.group("vol")), int(m.group("page"))
        if not has_page(vol, page):
            continue
        if m.group("short"):
            prior = [p for p in cited.get(vol, ()) if p <= page <= p + PIN_WINDOW]
            first = max(prior) if prior else decision_start(vol, page)
            if not first or page - first > PIN_WINDOW:
                first = page
            end, pins = m.end(), []
        else:
            first = page
            cited.setdefault(vol, []).append(page)
            pins = [p for p in _pins(text, m.end())
                    if has_page(vol, p[3]) and page <= p[3] <= page + PIN_WINDOW]
            # The first pin, straight after the page, rides in its link.
            end, pin = m.end(), None
            if pins and pins[0][0] == m.end():
                _c, _n, end, pin = pins.pop(0)
            page = pin or page
        out.append((m.start(), end, make_spec(vol, first, page)))
        for _c, start, end, pin in pins:
            out.append((start, end, make_spec(vol, first, pin)))
    out.sort(key=lambda t: (t[0], -t[1]))
    return out


def parse_query(query: str) -> "tuple[str, str] | None":
    """A Spotlight query that is one citation to the Decisions and Reports
    — its case name and pins, year and parenthetical allowed — as the
    ``("sec", spec)`` action that opens it."""
    text = (query or "").strip().rstrip(".").strip()
    found = iter_cites(text)
    if not found:
        return None
    start, end, spec = found[0]
    before = text[:start].strip()
    if before and not before.endswith(","):
        return None                      # "see 8 S.E.C. 893 and …": not one
    rest = re.sub(r"\([^()]*\)", " ", text[end:])
    if re.sub(r"[" + _DASH + r"\d\s,.;]", "", rest):
        return None
    return ("sec", spec)


# ---------------------------------------------------------------------------
# Offline self-tests:  python -X utf8 sec_decisions.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    failed = 0

    def check(cond: bool, what: str) -> None:
        global failed
        failed += not cond
        print(("ok   " if cond else "FAIL ") + what)

    def cites(text: str):
        return [(text[s:e], parse_spec(spec)) for s, e, spec in iter_cites(text)]

    chenery = ("during the reorganization period. 8 S.E.C. 893, 915-921. And "
               "so the plan was amended … over the management’s objections. "
               "10 S.E.C. 200.")
    got = cites(chenery)
    check(got == [("8 S.E.C. 893, 915-921", {"vol": 8, "page": 893, "pin": 915}),
                  ("10 S.E.C. 200", {"vol": 10, "page": 200})],
          f"Chenery's two citations (got {got})")
    check(cites("10 S. E. C. 200, 205, 207")
          == [("10 S. E. C. 200, 205", {"vol": 10, "page": 200, "pin": 205}),
              ("207", {"vol": 10, "page": 200, "pin": 207})],
          "spaced reporter, and a list of pins")
    check([c for _t, c in cites("8 SEC 893")] == [{"vol": 8, "page": 893}],
          "SEC without periods")
    check(cites("8 S.E.C. 893 (1941); 8 S.E.C. at 915")[1]
          == ("8 S.E.C. at 915", {"vol": 8, "page": 893, "pin": 915}),
          "short form reads against the full cite")
    check(cites("See 45 S.E.C. Docket 1234; 1 S.E.C. Jud. Dec. 12; "
                "22 S.E.C. Ann. Rep. 45; SEC v. Chenery Corp., 318 U.S. 80.") == [],
          "the SEC Docket, Judicial Decisions and annual reports are not claimed")
    check(cites("8 S.E.C. 893, 10 S.E.C. 200")
          == [("8 S.E.C. 893", {"vol": 8, "page": 893}),
              ("10 S.E.C. 200", {"vol": 10, "page": 200})],
          "a following citation's volume is no pin")
    check(cites("59 S.E.C. 12") == [] and cites("8 S.E.C. 9999") == [],
          "no such volume or page")
    if is_available():
        check(page_url(make_spec(8, 893, 915))
              == f"{VIEWER_URL}?id=osu.32435025999830&seq=935",
              f"8 S.E.C. 915 is scan page 935 (got {page_url(make_spec(8, 893, 915))})")
        check(spec_label(make_spec(8, 893, 915)) == "8 S.E.C. 893, 915 (1941)",
              f"label with year (got {spec_label(make_spec(8, 893, 915))!r})")
        check(decision_start(8, 915) == 893, "915 is a page of the decision at 893")
        check(cites("8 S.E.C. at 915")
              == [("8 S.E.C. at 915", {"vol": 8, "page": 893, "pin": 915})],
              "a short form alone finds its decision in the index")
    check(parse_query("8 S.E.C. 893") == ("sec", make_spec(8, 893)), "Spotlight query")
    check(parse_query("Federal Water Serv. Corp., 8 S.E.C. 893, 915 (1941)")
          == ("sec", make_spec(8, 893, 915)), "Spotlight query with name and year")
    check(parse_query("8 S.E.C. 893 and more words") is None,
          "Spotlight leaves other queries alone")
    print("\n" + ("all tests passed" if not failed else f"{failed} checks FAILED"))
    raise SystemExit(1 if failed else 0)
