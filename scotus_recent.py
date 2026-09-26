"""Recent Supreme Court decisions and slip-opinion archives.

The Court's homepage carries a "Recent Decisions" panel: for each recently
decided case it lists the case name, docket number, decision date, a plain
English one-paragraph description, and a link to the slip-opinion PDF.
This module fetches and parses that panel into :class:`RecentDecision` records,
with a short-lived on-disk cache so the panel opens instantly and the site is
polled at most a few times a day.  It also parses the Court's per-Term
slip-opinion tables and safely matches a case by docket, reporter citation, or
caption plus the exact decision date printed in the opinion.

The homepage panel is empty between Terms, so the latest entries of the
Term's "Opinions of the Court" table stand in for it
(:func:`recent_merits_opinions`), and the Term's "Opinions Relating to
Orders" — which lists each separate writing on an order as a row of its own —
is gathered into one entry per order naming the Justices who wrote
(:func:`recent_order_opinions`).

Headless and dependency-light (``requests`` + ``beautifulsoup4``, both already
required by the app); no tkinter.  Run ``python -X utf8 scotus_recent.py`` for
an offline self-test (parses a bundled fixture) plus, with ``--live``, a real
fetch.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "scotus_recent requires 'requests' and 'beautifulsoup4'."
    ) from exc

HOME_URL = "https://www.supremecourt.gov/"
SLIP_INDEX_URL = (
    "https://www.supremecourt.gov/opinions/slipopinion/{term}"
)
# A Term's opinion table: ``slipopinion`` is "Opinions of the Court", the
# merits opinions; ``relatingtoorders`` is "Opinions Relating to Orders".
TERM_TABLE_URL = "https://www.supremecourt.gov/opinions/{kind}/{term}"
MERITS = "slipopinion"
RELATING_TO_ORDERS = "relatingtoorders"
_BASE = "https://www.supremecourt.gov/"
_CACHE_PATH = Path.home() / ".cache" / "courtlistener_scotus_recent.json"
_CACHE_VERSION = 3
_CACHE_TTL = 6 * 3600  # seconds; the homepage updates on decision days only
_SLIP_CACHE_VERSION = 1
_SLIP_CURRENT_TTL = 6 * 3600
_SLIP_PAST_TTL = 30 * 24 * 3600
_TERM_CACHE_VERSION = 1
_TIMEOUT = 25
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) "
       "Gecko/20100101 Firefox/144.0")

# "National Republican Senatorial Committee v. FEC (24-621)" — split the
# trailing "(docket)" off the case name.
_DOCKET_RE = re.compile(r"\s*\((\d{1,3}[-‐-―]\d{1,5}(?:,[^)]*)?)\)\s*$")


@dataclass
class RecentDecision:
    """One case from the homepage's Recent Decisions panel."""
    name: str            # "National Republican Senatorial Committee v. FEC"
    docket: str          # "24-621"
    date: str            # "June 30, 2026" (as printed)
    description: str     # the plain-English summary paragraph
    opinion_url: str     # absolute URL of the slip-opinion PDF ("" if none)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SlipOpinion:
    """One row from an October Term slip-opinion archive page."""

    term: str            # two-digit October Term slug, e.g. "24"
    release: str         # release number in the Court's table
    date: str            # ISO decision date when parseable
    docket: str          # normalized ASCII-hyphen docket
    name: str
    opinion_url: str
    citation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TermOpinion:
    """One row of a Term's opinion table — "Opinions of the Court" or
    "Opinions Relating to Orders" — including a row the Court has not linked
    a PDF to yet."""

    term: str            # two-digit October Term slug, e.g. "25"
    date: str            # ISO date
    docket: str          # as printed: "25-332", "26A305", "162, Orig."
    name: str            # revision notes taken off
    author: str          # the "J." column: "R", "BK", "PC" (per curiam)
    opinion_url: str     # absolute PDF URL, with any #page=; "" if none
    description: str = ""  # the holding the Court puts on the link, if any
    citation: str = ""   # "609 U.S. 422", or a preliminary-print "609/2"
    release: str = ""    # the "R-" number (Opinions of the Court only)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrderOpinion:
    """The separate writings on one order, gathered from the rows "Opinions
    Relating to Orders" lists them in — one row per writing."""

    name: str
    docket: str
    date: str            # ISO date of the order
    authors: list        # each writing's "J." initials, in print order
    opinion_url: str     # the first writing's PDF (with any #page=)
    citation: str = ""


#: The "J." column's initials → the Justice's surname.
JUSTICES = {
    "R": "Roberts", "T": "Thomas", "A": "Alito", "SS": "Sotomayor",
    "EK": "Kagan", "NG": "Gorsuch", "BK": "Kavanaugh", "AB": "Barrett",
    "KJ": "Jackson", "B": "Breyer", "G": "Ginsburg",
}


def _abs(href: str) -> str:
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return ""
    return urljoin(_BASE, href)


def _clean(text: str) -> str:
    # The site uses a soft hyphen (U+00AD, often rendered as a box) at line
    # wraps and non-breaking spaces; normalize whitespace and drop soft hyphens.
    text = (text or "").replace("­", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


_DASH_RE = re.compile(r"[\u2010-\u2015\u2212]")
_DOCKET_TOKEN_RE = re.compile(
    r"\b(?:\d{1,3}-\d{1,5}|\d{1,3}[A-Z]\d{1,5})\b", re.IGNORECASE,
)
_US_CITE_RE = re.compile(
    r"\b(\d{1,3})\s+U\.?\s*S\.?\s+(\d{1,5})\b", re.IGNORECASE,
)


def _normalize_docket(value: str) -> str:
    return re.sub(r"\s+", "", _DASH_RE.sub("-", value or "")).upper()


def _docket_tokens(value: str) -> set[str]:
    normalized = _normalize_docket(value)
    return {
        _normalize_docket(m.group(0))
        for m in _DOCKET_TOKEN_RE.finditer(normalized)
    }


def _date_iso(value: str) -> str:
    text = _clean(value).rstrip(".")
    if not text:
        return ""
    text = re.sub(
        r"^(?:Decided|Filed|Released)\s+", "", text, flags=re.IGNORECASE,
    )
    for fmt in (
        "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y",
        "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
    ):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"\b(20\d{2})\b", text)
    return m.group(1) if m else ""


def _cite_key(value: str) -> tuple[str, str] | None:
    m = _US_CITE_RE.search(value or "")
    return (m.group(1), m.group(2)) if m else None


def parse_slip_opinions(html: str, term: str) -> list[SlipOpinion]:
    """Parse an October Term archive table from ``slipopinion/<term>``.

    The Court occasionally adds a second PDF link for a revision notice.
    The case-name link is selected instead of those date-labelled links.
    """
    term = re.sub(r"\D", "", str(term or ""))[-2:]
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[SlipOpinion] = []
    seen: set[str] = set()
    for row in soup.find_all("tr"):
        pdf_links = [
            a for a in row.find_all("a", href=True)
            if re.search(
                r"/opinions/\d{2}pdf/[^?#]+\.pdf(?:[?#].*)?$",
                _abs(a.get("href")), re.IGNORECASE,
            )
        ]
        if not pdf_links:
            continue
        opinion_link = next(
            (
                a for a in pdf_links
                if _clean(a.get_text())
                and not re.fullmatch(
                    r"\d{1,2}/\d{1,2}/\d{2,4}", _clean(a.get_text())
                )
            ),
            pdf_links[0],
        )
        url = _abs(opinion_link.get("href"))
        if not url or url in seen:
            continue
        cells = row.find_all(["td", "th"])
        name_cell = opinion_link.find_parent(["td", "th"])
        if name_cell is None or name_cell not in cells:
            continue
        name_ix = cells.index(name_cell)
        if name_ix < 2:
            continue
        texts = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        name = _clean(opinion_link.get_text(" ", strip=True))
        if not name:
            continue
        docket = texts[name_ix - 1]
        decision_date = texts[name_ix - 2]
        release = texts[name_ix - 3] if name_ix >= 3 else ""
        citation = texts[-1] if len(texts) > name_ix + 2 else ""
        seen.add(url)
        out.append(SlipOpinion(
            term=term,
            release=release,
            date=_date_iso(decision_date),
            docket=_normalize_docket(docket),
            name=name,
            opinion_url=url,
            citation=citation,
        ))
    return out


def _term_slug(value: str | int) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 4:
        digits = digits[-2:]
    return digits.zfill(2) if digits else ""


def _current_term_year() -> int:
    today = date.today()
    return today.year if today.month >= 10 else today.year - 1


def _slip_cache_path(term: str) -> Path:
    return (
        Path.home() / ".cache"
        / f"courtlistener_scotus_slip_{_term_slug(term)}.json"
    )


def _term_cache_ttl(term: str) -> int:
    """How long a Term's tables are cached: hours while the Term is sitting,
    a month once it has closed."""
    term_year = 2000 + int(_term_slug(term))
    return (
        _SLIP_CURRENT_TTL
        if term_year >= _current_term_year()
        else _SLIP_PAST_TTL
    )


def _read_slip_cache(
    term: str, *, allow_stale: bool = False,
) -> "Optional[list[SlipOpinion]]":
    try:
        blob = json.loads(
            _slip_cache_path(term).read_text(encoding="utf-8")
        )
        if blob.get("version") != _SLIP_CACHE_VERSION:
            return None
        if (
            not allow_stale
            and time.time() - float(blob.get("fetched_at") or 0)
            > _term_cache_ttl(term)
        ):
            return None
        return [SlipOpinion(**item) for item in (blob.get("items") or [])]
    except Exception:
        return None


def _write_slip_cache(term: str, items: list[SlipOpinion]) -> None:
    try:
        path = _slip_cache_path(term)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": _SLIP_CACHE_VERSION,
            "fetched_at": time.time(),
            "term": _term_slug(term),
            "items": [item.to_dict() for item in items],
        }), encoding="utf-8")
    except Exception:
        pass


def fetch_slip_opinions(
    term: str | int, *, force: bool = False, session=None,
) -> list[SlipOpinion]:
    """All opinions on one October Term archive page.

    A current-term index is cached for six hours; completed terms are cached
    for thirty days.  A stale cache is used when supremecourt.gov is
    temporarily unavailable.
    """
    slug = _term_slug(term)
    if not slug:
        return []
    if not force:
        cached = _read_slip_cache(slug)
        if cached is not None:
            return cached
    url = SLIP_INDEX_URL.format(term=slug)
    try:
        get = session.get if session is not None else requests.get
        resp = get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
        items = parse_slip_opinions(resp.text, slug)
    except Exception as exc:
        print(f"[scotus] slip-opinion index {slug} failed: {exc}")
        return _read_slip_cache(slug, allow_stale=True) or []
    if items:
        _write_slip_cache(slug, items)
    return items


def _default_name_score(query: str, candidate: str) -> float:
    def tokens(value: str) -> set[str]:
        return {
            word for word in re.findall(r"[a-z0-9]+", (value or "").lower())
            if len(word) > 1 and word not in {
                "the", "of", "and", "a", "an", "v", "vs", "inc", "llc",
                "corp", "company", "co", "et", "al",
            }
        }

    wanted = tokens(query)
    found = tokens(candidate)
    return (len(wanted & found) / len(wanted)) if wanted else 0.0


def _candidate_terms(date_filed: str, docket: str) -> list[str]:
    """Likely archive slugs, strongest first, from date/year and docket."""
    out: list[int] = []

    def add(year: int) -> None:
        if 2018 <= year <= _current_term_year() and year not in out:
            out.append(year)

    parsed = _date_iso(date_filed)
    year = int(parsed[:4]) if parsed[:4].isdigit() else 0
    if len(parsed) == 10:
        month = int(parsed[5:7])
        add(year if month >= 10 else year - 1)
    if year:
        # A saved Scholar record may know only the decision year and represent
        # it as January 1.  Try both October Terms that can issue in that year.
        add(year - 1)
        add(year)
    docket_terms = {
        int(token[:2]) + 2000
        for token in _docket_tokens(docket)
        if token[:2].isdigit()
    }
    for docket_year in sorted(docket_terms, reverse=True):
        add(docket_year)
        add(docket_year + 1)
    return [_term_slug(year) for year in out]


def find_slip_opinion(
    *,
    name: str = "",
    docket: str = "",
    date_filed: str = "",
    citations: tuple[str, ...] | list[str] = (),
    session=None,
    name_scorer=None,
) -> Optional[SlipOpinion]:
    """Match a case to the Court's archived opinion PDF.

    The exact decision date is required even when the docket or U.S.-Reports
    citation matches.  A merits opinion and a later order or separate writing
    can share the same docket, so identity metadata alone is not conclusive.
    Within that date, docket, citation, or a strong caption match identifies
    the archive row.
    """
    parsed_date = _date_iso(date_filed)
    if len(parsed_date) != 10:
        return None
    terms = _candidate_terms(date_filed, docket)
    if not terms:
        return None
    wanted_dockets = _docket_tokens(docket)
    wanted_cites = {
        key for key in (_cite_key(str(c)) for c in citations) if key
    }
    scorer = name_scorer or _default_name_score
    rated: list[tuple[float, SlipOpinion]] = []
    for term in terms:
        for item in fetch_slip_opinions(term, session=session):
            if item.date != parsed_date:
                continue
            docket_match = bool(
                wanted_dockets & _docket_tokens(item.docket)
            )
            cite_key = _cite_key(item.citation)
            cite_match = bool(cite_key and cite_key in wanted_cites)
            name_score = (
                float(scorer(name, item.name)) if name and item.name else 0.0
            )
            if not (docket_match or cite_match or name_score >= 0.65):
                continue
            score = (
                (1000.0 if docket_match else 0.0)
                + (900.0 if cite_match else 0.0)
                + name_score * 30.0
            )
            rated.append((score, item))
    if not rated:
        return None
    rated.sort(key=lambda pair: pair[0], reverse=True)
    return rated[0][1]


def parse_recent_decisions(html: str) -> list[RecentDecision]:
    """Parse the homepage HTML into recent-decision records, in the order the
    site lists them (newest first).  Only cases with an opinion PDF link are
    returned — the panel also carries order-list items, which have no opinion."""
    soup = BeautifulSoup(html or "", "html.parser")
    panels = soup.find_all(id="opinionsbyday")
    if not panels:
        panel = (soup.find(id=lambda v: v and "RecentDecisions" in v)
                 or soup)
        panels = [panel]
    out: list[RecentDecision] = []
    seen: set[tuple[str, str]] = set()
    current_date = ""
    # Walk the panel in document order: date headers (span.soday) set the
    # running date; each case is a casenamerow + following casedetail, with the
    # opinion PDF in the preceding buttonrow.
    nodes = (node for panel in panels for node in panel.find_all(["span", "div"]))
    for el in nodes:
        classes = el.get("class") or []
        if "soday" in classes:
            current_date = _clean(el.get_text())
            continue
        if "casenamerow" not in classes:
            continue
        name_raw = _clean(el.get_text())
        if not name_raw:
            continue
        docket = ""
        m = _DOCKET_RE.search(name_raw)
        if m:
            docket = re.sub(r"[‐-―]", "-", m.group(1)).strip()
            name = name_raw[: m.start()].strip()
        else:
            name = name_raw
        # The description is the next casedetail sibling.
        description = ""
        sib = el.find_next_sibling()
        while sib is not None:
            sc = sib.get("class") or []
            if "casedetail" in sc:
                description = _clean(sib.get_text())
                break
            if "casenamerow" in sc or "buttonrow" in sc:
                break  # ran into the next case — no detail for this one
            sib = sib.find_next_sibling()
        # The opinion PDF link lives in the preceding buttonrow.
        opinion_url = ""
        prev = el.find_previous_sibling()
        while prev is not None:
            pc = prev.get("class") or []
            if "buttonrow" in pc:
                a = prev.find("a", href=re.compile(r"opinions/.*\.pdf$",
                                                    re.IGNORECASE))
                if a:
                    opinion_url = _abs(a.get("href"))
                break
            if "casenamerow" in pc:
                break
            prev = prev.find_previous_sibling()
        if not opinion_url:
            continue  # an order or a case without a released opinion
        key = (docket or name, opinion_url)
        if key in seen:
            continue
        seen.add(key)
        out.append(RecentDecision(
            name=name, docket=docket, date=current_date,
            description=description, opinion_url=opinion_url,
        ))
    return out


def _read_cache() -> "Optional[list[RecentDecision]]":
    try:
        blob = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        if blob.get("version") != _CACHE_VERSION:
            return None
        if time.time() - blob.get("fetched_at", 0) > _CACHE_TTL:
            return None
        items = blob.get("items") or []
        return [RecentDecision(**it) for it in items]
    except Exception:
        return None


def _write_cache(items: list[RecentDecision]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps({
            "version": _CACHE_VERSION,
            "fetched_at": time.time(),
            "items": [it.to_dict() for it in items],
        }), encoding="utf-8")
    except Exception:
        pass


def fetch_recent_decisions(
    *, force: bool = False, session=None,
) -> list[RecentDecision]:
    """Recent decisions, from the cache when fresh, else the live homepage.
    Returns ``[]`` on any failure (the caller shows nothing / links out)."""
    if not force:
        cached = _read_cache()
        if cached is not None:
            return cached
    try:
        get = session.get if session is not None else requests.get
        resp = get(HOME_URL, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
        items = parse_recent_decisions(resp.text)
    except Exception as exc:
        print(f"[scotus] recent-decisions fetch failed: {exc}")
        stale = _read_cache_stale()
        return stale or []
    if items:
        _write_cache(items)
    return items


def _read_cache_stale() -> "Optional[list[RecentDecision]]":
    """Cache contents ignoring the TTL — a fetch failure is better served by
    a day-old list than by nothing."""
    try:
        blob = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        if blob.get("version") != _CACHE_VERSION:
            return None
        return [RecentDecision(**it) for it in (blob.get("items") or [])]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# A Term's opinion tables: Opinions of the Court, Opinions Relating to Orders
# ---------------------------------------------------------------------------

# The tables' column headings, as printed, → TermOpinion fields.
_TERM_COLUMNS = {
    "r-": "release", "date": "date", "docket": "docket", "name": "name",
    "j.": "author", "citation": "citation",
}
# "Danco Laboratories, LLC v. Louisiana Revisions : 5/15/26" — the note of a
# revised opinion, printed after the name in the same cell.
_REVISIONS_RE = re.compile(r"\s*\bRevisions?\s*:.*$", re.IGNORECASE)
_BARE_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")


def parse_term_opinions(html: str, term: str | int) -> list[TermOpinion]:
    """Every row of a Term's opinion tables, in the order the page lists them
    (newest first).

    Columns are read by their headings, so one parser serves both pages:
    "Opinions of the Court" (R-, Date, Docket, Name, J., Citation) and
    "Opinions Relating to Orders" (the same without R-).  A row the Court has
    not linked a PDF to is kept, with no ``opinion_url``.  The case-name link
    is taken over a revision notice's date-labelled one.
    """
    slug = _term_slug(term)
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[TermOpinion] = []
    seen: set[tuple] = set()
    for table in soup.find_all("table"):
        columns: dict[str, int] = {}
        for tr in table.find_all("tr"):
            heads = tr.find_all("th")
            if heads:
                labels = [_clean(th.get_text(" ", strip=True)).lower()
                          for th in heads]
                columns = {
                    _TERM_COLUMNS[label]: i
                    for i, label in enumerate(labels) if label in _TERM_COLUMNS
                }
                if not {"date", "docket", "name"} <= columns.keys():
                    columns = {}
                continue
            cells = tr.find_all("td")
            if not columns or len(cells) <= max(columns.values()):
                continue

            def text(field: str) -> str:
                i = columns.get(field)
                return (_clean(cells[i].get_text(" ", strip=True))
                        if i is not None else "")

            name_cell = cells[columns["name"]]
            links = [
                a for a in name_cell.find_all("a", href=True)
                if re.search(r"\.pdf(?:[?#].*)?$", a["href"], re.IGNORECASE)
            ]
            link = next(
                (a for a in links
                 if not _BARE_DATE_RE.fullmatch(_clean(a.get_text()))),
                links[0] if links else None,
            )
            name = _REVISIONS_RE.sub("", text("name")).strip()
            iso = _date_iso(text("date"))
            if not name or not iso:
                continue
            row = TermOpinion(
                term=slug,
                date=iso,
                docket=text("docket"),
                name=name,
                author=text("author"),
                opinion_url=_abs(link["href"]) if link is not None else "",
                description=(_clean(link.get("title") or "")
                             if link is not None else ""),
                citation=text("citation"),
                release=text("release"),
            )
            key = (row.date, row.docket, row.name, row.author, row.opinion_url)
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out


def _term_cache_path(kind: str, term: str) -> Path:
    return (
        Path.home() / ".cache"
        / f"courtlistener_scotus_{kind}_{_term_slug(term)}.json"
    )


def _read_term_cache(
    kind: str, term: str, *, allow_stale: bool = False,
) -> "Optional[list[TermOpinion]]":
    try:
        blob = json.loads(
            _term_cache_path(kind, term).read_text(encoding="utf-8")
        )
        if blob.get("version") != _TERM_CACHE_VERSION:
            return None
        if (
            not allow_stale
            and time.time() - float(blob.get("fetched_at") or 0)
            > _term_cache_ttl(term)
        ):
            return None
        return [TermOpinion(**item) for item in (blob.get("items") or [])]
    except Exception:
        return None


def _write_term_cache(kind: str, term: str, items: list[TermOpinion]) -> None:
    try:
        path = _term_cache_path(kind, term)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": _TERM_CACHE_VERSION,
            "fetched_at": time.time(),
            "items": [item.to_dict() for item in items],
        }), encoding="utf-8")
    except Exception:
        pass


def fetch_term_opinions(
    term: str | int, kind: str = MERITS, *, force: bool = False, session=None,
) -> list[TermOpinion]:
    """A Term's "Opinions of the Court" (``kind=MERITS``) or "Opinions
    Relating to Orders" (``kind=RELATING_TO_ORDERS``), cached like the
    slip-opinion index: six hours for the sitting Term, thirty days for a
    closed one, and a stale copy when supremecourt.gov is unreachable.
    Returns ``[]`` for a Term with no table yet."""
    slug = _term_slug(term)
    if not slug:
        return []
    if not force:
        cached = _read_term_cache(kind, slug)
        if cached is not None:
            return cached
    url = TERM_TABLE_URL.format(kind=kind, term=slug)
    try:
        get = session.get if session is not None else requests.get
        resp = get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        resp.raise_for_status()
        items = parse_term_opinions(resp.text, slug)
    except Exception as exc:
        print(f"[scotus] {kind} {slug} failed: {exc}")
        return _read_term_cache(kind, slug, allow_stale=True) or []
    if items:
        _write_term_cache(kind, slug, items)
    return items


def _release_number(release: str) -> int:
    digits = re.sub(r"\D", "", release or "")
    return int(digits) if digits else 0


def recent_merits_opinions(limit: int = 10, *, session=None) -> list[TermOpinion]:
    """The Court's latest opinions of the Court, newest first: the sitting
    Term's table, reaching back into the last Term while this one has fewer
    than *limit*.  What stands in for the homepage's Recent Decisions panel
    when it is empty."""
    out: list[TermOpinion] = []
    year = _current_term_year()
    for term in (year, year - 1):
        out.extend(fetch_term_opinions(term, MERITS, session=session))
        if len(out) >= limit:
            break
    out.sort(key=lambda row: (row.date, _release_number(row.release)),
             reverse=True)
    return out[:limit]


def pdf_page(url: str) -> int:
    """The page a PDF link's ``#page=N`` opens at (1 when it names none)."""
    m = re.search(r"#page=(\d+)", url or "", re.IGNORECASE)
    return max(1, int(m.group(1))) if m else 1


def group_order_opinions(rows: list[TermOpinion]) -> list[OrderOpinion]:
    """One entry per order, in the order the rows come: "Opinions Relating to
    Orders" gives each separate writing on an order a row of its own, all
    with the order's date and docket.

    The writings of an order share one PDF, each row pointing at its own
    ``#page=`` — in a closed Term, pages of the preliminary print's orders
    section.  The entry opens at the first of them, and names its writers in
    the order their writings are printed."""
    groups: dict[tuple[str, str], tuple[OrderOpinion, list]] = {}
    out: list[OrderOpinion] = []
    for row in rows:
        key = (row.date, _normalize_docket(row.docket) or row.name.lower())
        if key not in groups:
            group = OrderOpinion(
                name=row.name, docket=row.docket, date=row.date, authors=[],
                opinion_url="", citation=row.citation,
            )
            groups[key] = (group, [])
            out.append(group)
        groups[key][1].append(row)
    for group, members in groups.values():
        printed = sorted(
            enumerate(members),
            key=lambda pair: (pdf_page(pair[1].opinion_url), pair[0]),
        )
        for _i, row in printed:
            if row.author and row.author not in group.authors:
                group.authors.append(row.author)
        group.opinion_url = next(
            (row.opinion_url for _i, row in printed if row.opinion_url), "",
        )
        group.citation = printed[0][1].citation
    return out


def recent_order_opinions(limit: int = 5, *, session=None) -> list[OrderOpinion]:
    """The latest orders that drew separate writings, newest first, each
    listed once with the Justices who wrote (see :func:`group_order_opinions`)
    — from the sitting Term, reaching back into the last while it has fewer
    than *limit*."""
    out: list[OrderOpinion] = []
    year = _current_term_year()
    for term in (year, year - 1):
        out.extend(group_order_opinions(
            fetch_term_opinions(term, RELATING_TO_ORDERS, session=session)
        ))
        if len(out) >= limit:
            break
    out.sort(key=lambda order: order.date, reverse=True)  # stable within a day
    return out[:limit]


def author_label(initials: str) -> str:
    """The "J." column's initials as a byline: "Roberts, C.J.", "Kagan, J.",
    "Per curiam" — or the initials as printed, for ones not known here."""
    code = (initials or "").strip().upper()
    if code == "PC":
        return "Per curiam"
    name = JUSTICES.get(code)
    if not name:
        return (initials or "").strip()
    return f"{name}, C.J." if code == "R" else f"{name}, J."


def display_date(iso: str) -> str:
    """"2026-06-29" → "June 29, 2026"; anything else as given."""
    try:
        day = datetime.strptime(iso or "", "%Y-%m-%d").date()
    except ValueError:
        return iso or ""
    return f"{day:%B} {day.day}, {day.year}"


if __name__ == "__main__":  # pragma: no cover - offline smoke test
    import sys

    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
        print(("ok   " if cond else "FAIL ") + msg)

    FIXTURE = """
    <div id="opinionsbyday">
      <span class="soday">June 30, 2026</span>
      <div class="buttonrow">
        <a href="opinions/25pdf/24-43_2b35.pdf" target="_blank">op</a>
        <a href='#' onclick="x()">docket</a>
      </div>
      <div class="casenamerow"><span>West Virginia v. B.&nbsp;P.&nbsp;J. (24-43)</span></div>
      <div class="casedetail"><span>Title IX allows schools to provide sepa&shy;rate teams.</span></div>
      <div class="buttonrow">
        <a href="opinions/25pdf/24-621_h315.pdf">op</a>
      </div>
      <div class="casenamerow"><span>NRSC v. FEC (24-621)</span></div>
      <div class="casedetail"><span>FECA limits violate the First Amendment.</span></div>
      <div class="buttonrow"></div>
      <div class="casenamerow"><span>Some Order (25-100)</span></div>
      <div class="casedetail"><span>No opinion released.</span></div>
    </div>
    """
    items = parse_recent_decisions(FIXTURE)
    check(len(items) == 2, f"two opinions parsed (orders skipped): {len(items)}")
    check(items[0].name == "West Virginia v. B. P. J.",
          f"name split from docket + soft-hyphen cleaned: {items[0].name!r}")
    check(items[0].docket == "24-43", f"docket: {items[0].docket!r}")
    check(items[0].date == "June 30, 2026", f"date: {items[0].date!r}")
    check("separate teams" in items[0].description,
          f"soft hyphen removed in detail: {items[0].description!r}")
    check(items[0].opinion_url ==
          "https://www.supremecourt.gov/opinions/25pdf/24-43_2b35.pdf",
          f"opinion url absolutized: {items[0].opinion_url!r}")
    check(items[1].docket == "24-621", f"second docket: {items[1].docket!r}")
    check(all("Order" not in it.name for it in items),
          "the order without an opinion PDF is dropped")

    ORDERS_FIXTURE = """
    <table>
      <tr><th>Date</th><th>Docket</th><th>Name</th><th>J.</th><th>Citation</th></tr>
      <tr><td>9/14/26</td><td>26A305</td>
          <td><a href="/opinions/25pdf/26a305_4g15.pdf">Postal Service v. California</a></td>
          <td>BK</td><td>609/2</td></tr>
      <tr><td>9/14/26</td><td>26A305</td>
          <td><a href="/opinions/25pdf/26a305_4g15.pdf#page=2">Postal Service v. California</a></td>
          <td>A</td><td>609/2</td></tr>
    </table>
    """
    rows = parse_term_opinions(ORDERS_FIXTURE, "25")
    check(len(rows) == 2, f"one row per separate writing: {len(rows)}")
    orders = group_order_opinions(rows)
    check(len(orders) == 1 and orders[0].authors == ["BK", "A"],
          f"one entry per order, naming its writers: {orders}")

    if "--live" in sys.argv:
        print("\n--- live fetch ---")
        live = fetch_recent_decisions(force=True)
        print(f"fetched {len(live)} decisions")
        for it in live[:5]:
            print(f"  {it.date} | {it.name} ({it.docket})")
            print(f"      {it.description[:90]}")
            print(f"      {it.opinion_url}")
        latest = recent_merits_opinions(10)
        print(f"\n{len(latest)} latest opinions of the Court")
        for it in latest:
            print(f"  {display_date(it.date)} | {it.name} ({it.docket}) | "
                  f"{author_label(it.author)}")
        recent = recent_order_opinions(5)
        print(f"\n{len(recent)} latest opinions relating to orders")
        for it in recent:
            print(f"  {display_date(it.date)} | {it.name} ({it.docket}) | "
                  + "; ".join(author_label(a) for a in it.authors))
        check(bool(live or latest),
              "live homepage or Term table returned at least one opinion")

    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nOK: scotus_recent smoke test passed")
