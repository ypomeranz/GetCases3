"""Detect citations to legislative history and say where each one is online.

Six kinds of source are read, in the forms U.S. opinions cite them:

* the Congressional Record, bound edition ("116 Cong. Rec. 36481 (1970)")
  and daily edition ("148 Cong. Rec. S2101 (daily ed. Mar. 20, 2002)");
* its predecessors — the Annals of Congress ("1 Annals of Cong. 434
  (1789)"), the Register of Debates ("11 Cong. Deb. 518 (1835)", "2 Reg.
  Deb. 1234") and the Congressional Globe ("Cong. Globe, 39th Cong., 1st
  Sess. 2765 (1866)", "39th Cong. Globe 1088");
* committee reports ("S. Rep. No. 95-797, at 5 (1978)", "H.R. Rep. No.
  1476, 94th Cong., 2d Sess. 66 (1976)", "H.R. Conf. Rep. No. 103-711");
* congressional documents ("S. Doc. No. 91-35", "H.R. Exec. Doc. No. 1,
  40th Cong., 2d Sess.", "S. Treaty Doc. No. 103-39").

Each citation becomes a *spec* — a small JSON object naming the source and
the page — which :mod:`leghist_fetch` turns into the pages themselves:

* the bound Record, 1873-2018, from GovInfo's scans (every volume), found
  through ``crecb_index.tsv.gz`` — each day's proceedings in each chamber
  with the printed pages it holds, read from GovInfo's own metadata;
* the daily Record from 1995, reports and documents from 1995, and the
  digitized Serial Set's older reports and documents, from GovInfo's link
  service;
* the Annals, the Register and the Globe from the Library of Congress's
  page images on Congress.gov, found through ``debates_index.tsv.gz``.

A citation is linked only where one of those sources can hold it: a bound
Record volume GovInfo has, a daily edition from 1995 (or, earlier, one
whose date names the day in the bound edition), a report or document whose
Congress the citation gives or implies.

The module is pure — no network, no GUI — and import-safe without the index
files (it then resolves nothing that needs them).  Run ``python -X utf8
legislative_history.py`` for offline self-tests.
"""

from __future__ import annotations

import bisect
import gzip
import json
import os
import re
import threading
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Congresses, sessions, volumes and years
# ---------------------------------------------------------------------------


def ordinal(n: int) -> str:
    """The Bluebook's ordinal for a Congress or session: 1st, 2d, 3d, 4th,
    21st, 42d, 43d, 101st, 102d, 103d."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "d", 3: "d"}.get(n % 10, "th")


def congresses_for_year(year: int) -> list[int]:
    """The Congresses that sat in *year*, the likelier first.

    From 1935 a Congress runs from January 3 of an odd year; before, from
    March 4, so an odd year's January to March belongs to the Congress
    ending then and the rest to the next — and a report of that year could
    be either's."""
    if year < 1789:
        return []
    if year >= 1935:
        return [(year - 1789) // 2 + 1]
    if year % 2 == 0:
        return [(year - 1788) // 2]
    later = (year - 1789) // 2 + 1
    return [later - 1, later] if later > 1 else [later]


def year_for_congress(congress: int, session: int = 1) -> int:
    """The year a Congress's session mostly sat in (approximate)."""
    first = 1789 + 2 * (congress - 1)
    if congress >= 74:
        return first + (session - 1)
    # Before 1935 the first regular session opened in the December after
    # the Congress began and ran into the next year.
    return first + 1 if session <= 1 else first + 2


# Congressional Record volume -> year (GovInfo's own listing); from 1995
# each year is one volume.
_CR_VOLUME_YEARS = (
    1873, 1874, 1875, 1876, 1877, 1877, 1878, 1879, 1879, 1880, 1881, 1881,
    1882, 1883, 1884, 1885, 1886, 1887, 1888, 1889, 1890, 1891, 1892, 1893,
    1893, 1894, 1895, 1896, 1897, 1897, 1898, 1899, 1900, 1901, 1902, 1903,
    1903, 1904, 1905, 1906, 1907, 1908, 1909, 1909, 1910, 1911, 1911, 1912,
    1913, 1913, 1914, 1915, 1916, 1917, 1917, 1918, 1919, 1919, 1920, 1921,
    1921, 1922, 1922, 1923, 1924, 1925, 1926, 1927, 1928, 1929, 1929, 1930,
    1930, 1931, 1932, 1933, 1933, 1934, 1935, 1936, 1937, 1937, 1938, 1939,
    1939, 1940, 1941, 1942, 1943, 1944, 1945, 1946, 1947, 1948, 1949, 1950,
    1951, 1952, 1953, 1954, 1955, 1956, 1957, 1958, 1959, 1960, 1961, 1962,
    1963, 1964, 1965, 1966, 1967, 1968, 1969, 1970, 1971, 1972, 1973, 1974,
    1975, 1976, 1977, 1978, 1979, 1980, 1981, 1982, 1983, 1984, 1985, 1986,
    1987, 1988, 1989, 1990, 1991, 1992, 1993, 1994,
)

# The bound edition GovInfo holds runs through volume 164 (2018); the daily
# edition, through its link service, from volume 141 (1995).
CR_BOUND_MAX_VOL = 164
CR_DAILY_MIN_VOL = 141


def cr_volume_year(vol: int) -> int:
    """The year a Congressional Record volume covers (0 if unknown)."""
    if 1 <= vol <= len(_CR_VOLUME_YEARS):
        return _CR_VOLUME_YEARS[vol - 1]
    if vol > len(_CR_VOLUME_YEARS):
        return 1854 + vol
    return 0


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


def make_spec(**fields) -> str:
    """The action value for a citation: compact JSON, keys sorted, empty
    fields left out."""
    clean = {k: v for k, v in fields.items() if v not in (None, "", 0, False)}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def parse_spec(spec: "str | dict") -> dict:
    if isinstance(spec, dict):
        return dict(spec)
    try:
        value = json.loads(spec or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def with_page(spec: "str | dict", page: str) -> str:
    """*spec* opened at another page — an "id., at 17607" after it, or a
    later page of a list ("93 Cong. Rec. 4033, 4906")."""
    s = parse_spec(spec)
    src = s.get("src")
    page = (page or "").strip()
    if not page:
        return make_spec(**s)
    if src in ("rpt", "doc"):
        s["pin"] = page
    else:
        m = re.fullmatch(r"(?i)(app\.?\s*)?([SHED])?\s?(\d+)", page.replace(",", ""))
        if not m:
            return make_spec(**s)
        if src == "cr":
            pfx = (m.group(2) or "").upper()
            if s.get("daily") and not pfx and re.match(r"[SHED]", str(s.get("page", ""))):
                pfx = str(s["page"])[0]
            s["page"] = f"{pfx}{int(m.group(3))}"
        else:
            s["page"] = int(m.group(3))
            if m.group(1):
                s["app"] = True
    return make_spec(**s)


def _commas(n: int) -> str:
    return f"{n:,}" if n >= 10000 else str(n)


def _cr_page_label(page: str) -> str:
    m = re.fullmatch(r"([SHED]?)(\d+)", str(page))
    if not m:
        return str(page)
    return m.group(1) + _commas(int(m.group(2)))


_MONTH_ABBR = ("Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.",
               "Sept.", "Oct.", "Nov.", "Dec.")


def _date_label(date: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d\d)-(\d\d)", date or "")
    if not m:
        return ""
    return f"{_MONTH_ABBR[int(m.group(2)) - 1]} {int(m.group(3))}, {m.group(1)}"


_DOC_PREFIX = {("s", "doc"): "S. Doc.", ("h", "doc"): "H.R. Doc.",
               ("s", "exec"): "S. Exec. Doc.", ("h", "exec"): "H.R. Exec. Doc.",
               ("s", "misc"): "S. Misc. Doc.", ("h", "misc"): "H.R. Misc. Doc.",
               ("s", "treaty"): "S. Treaty Doc.", ("h", "treaty"): "S. Treaty Doc."}


def spec_label(spec: "str | dict", *, with_pin: bool = True) -> str:
    """The citation a spec stands for, written the Bluebook's way — the
    viewer's title."""
    s = parse_spec(spec)
    src = s.get("src")
    year = s.get("year") or 0
    if src == "cr":
        vol = s.get("vol", 0)
        page = _cr_page_label(s.get("page", ""))
        if s.get("daily"):
            when = _date_label(s.get("date", "")) or (str(year) if year else "")
            paren = f" (daily ed. {when})" if when else " (daily ed.)"
        else:
            y = year or cr_volume_year(vol)
            paren = f" ({y})" if y else ""
        return f"{vol} Cong. Rec. {page}{paren}"
    if src == "globe":
        cong, sess = s.get("cong", 0), s.get("sess", 0)
        where = f", {ordinal(cong)} Cong."
        if sess:
            where += f", {ordinal(sess)} Sess."
        page = f"app. {s.get('page')}" if s.get("app") else str(s.get("page"))
        # The year the page's own date gives (see leghist_fetch), else the
        # citation's; a session's year can't be told from its number.
        return f"Cong. Globe{where} {page}" + (f" ({year})" if year else "")
    if src == "annals":
        paren = f" ({year})" if year else ""
        return f"{s.get('vol')} Annals of Cong. {s.get('page')}{paren}"
    if src == "regdeb":
        page = f"app. {s.get('page')}" if s.get("app") else str(s.get("page"))
        paren = f" ({year})" if year else ""
        return f"{s.get('vol')} Reg. Deb. {page}{paren}"
    if src in ("rpt", "doc"):
        ch = s.get("ch", "h")
        if src == "rpt":
            prefix = ("S." if ch == "s" else "H.R.") + (
                " Exec." if s.get("exec") else "") + " Rep."
        else:
            prefix = _DOC_PREFIX.get((ch, s.get("type", "doc")), "Doc.")
        cong, num = s.get("cong", 0), s.get("num", 0)
        label = f"{prefix} No. {cong}-{num}" if cong else f"{prefix} No. {num}"
        if s.get("part"):
            label += f", pt. {s['part']}"
        if with_pin and s.get("pin"):
            label += f", at {s['pin']}"
        if year:
            label += f" ({year})"
        if src == "rpt" and s.get("conf"):
            label += " (Conf. Rep.)"
        return label
    return ""


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_DASH = "-‐‑‒–—−"
_D = f"[{_DASH}]"
_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d{1,6})(?!\d)"
_ORD = r"(?:st|nd|rd|th|d)"

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_DATE_RE = re.compile(
    r"\b(?P<mon>Jan|Feb|Mar|Apr|May|June?|July?|Aug|Sept?|Oct|Nov|Dec)[a-z]*\.?"
    r"\s*(?P<day>\d{1,2}),?\s*(?P<year>1[789]\d\d|20\d\d)\b")
_YEAR_PAREN_RE = re.compile(r"\s*\((?:[^()]*?\b)?(?P<year>1[789]\d\d|20\d\d)\)")


def _num(value: str) -> int:
    return int(re.sub(r"[^\d]", "", value or "0") or 0)


def _date_in(text: str) -> tuple[str, int]:
    """("YYYY-MM-DD", year) of the first date in *text*; ("", year) when it
    gives a year only."""
    m = _DATE_RE.search(text or "")
    if m:
        mon = _MONTHS[m.group("mon")[:3].lower()]
        return (f"{m.group('year')}-{mon:02d}-{int(m.group('day')):02d}",
                int(m.group("year")))
    y = re.search(r"\b(1[789]\d\d|20\d\d)\b", text or "")
    return "", int(y.group(1)) if y else 0


@dataclass(frozen=True)
class Cite:
    start: int
    end: int
    spec: str

    def as_tuple(self) -> tuple[int, int, str]:
        return (self.start, self.end, self.spec)


# -- Congressional Record ----------------------------------------------------

_CR_PART = r"(?:,?\s*(?:[Pp]art|[Pp]t\.)\s*\d{1,2})?"
CR_RE = re.compile(
    r"\b(?P<vol>\d{1,3})\s+Cong\.?\s?Rec\.?(?![A-Za-z])" + _CR_PART +
    r"\s*,?\s*(?:at\s+|pp?\.\s*)?"
    r"(?P<pfx>[SHED])?\.?\s?(?P<page>" + _NUM + r")"
    r"(?:\s*" + _D + r"\s*(?:[SHED]\.?\s?)?(?P<end>" + _NUM + r"))?"
)
# The old form, "Cong. Rec. vol. 21, part 6, p. 5950".
CR_VOL_RE = re.compile(
    r"Cong\.?\s?Rec\.?,?\s*[Vv]ol\.\s*(?P<vol>\d{1,3})" + _CR_PART +
    r",?\s*(?:pp?\.\s*|at\s+)(?P<pfx>)(?P<page>" + _NUM + r")"
    r"(?:\s*" + _D + r"\s*" + _NUM + r")?"
)
# A daily-edition page with its date but no volume: "Cong. Rec. S 18891
# (daily ed. Nov. 17, 1971)" — the date names the volume.
CR_NOVOL_RE = re.compile(
    r"Cong\.?\s?Rec\.?\s*(?P<vol>)(?P<pfx>[SHED])\.?\s?(?P<page>" + _NUM + r")"
    r"(?:\s*" + _D + r"\s*(?:[SHED]\.?\s?)?" + _NUM + r")?"
)
# A later page of the same volume, in a list: ", 4906", ", 5411-5415",
# ", H10334" — but not a year, an ordinal ("60th Cong."), or the volume of
# a reporter that follows ("1978 U.S.C.C.A.N.").
_CR_MORE_RE = re.compile(
    r"\s*,\s*(?:at\s+)?(?P<pfx>[SHED])?\.?\s?(?P<page>" + _NUM + r")"
    r"(?:\s*" + _D + r"\s*(?:[SHED]\.?\s?)?" + _NUM + r")?"
    r"(?!\s*" + _ORD + r"\b)(?!\s+[A-Z][A-Za-z]*\.)"
)
_PAREN_RE = re.compile(r"\s*\((?P<body>[^()]{1,90})\)")


def cr_volumes_for_year(year: int) -> list[int]:
    """The Congressional Record volumes of *year* (two, some years before
    1941: a special session's and the regular session's)."""
    vols = [i + 1 for i, y in enumerate(_CR_VOLUME_YEARS) if y == year]
    if not vols and year > _CR_VOLUME_YEARS[-1]:
        vols = [year - 1854]
    return vols


def _cr_matches(text: str):
    for rx in (CR_RE, CR_VOL_RE, CR_NOVOL_RE):
        yield from rx.finditer(text)


def _cr_cites(text: str) -> list[Cite]:
    out: list[Cite] = []
    if "Rec" not in text:
        return out
    for m in _cr_matches(text):
        vol = int(m.group("vol") or 0)
        pfx = (m.group("pfx") or "").upper()
        page = _num(m.group("page"))
        if page < 1 or (vol < 1 and m.re is not CR_NOVOL_RE):
            continue
        # Later pages, then the parenthetical that dates them all.
        more: list[tuple[int, int, str, int]] = []
        pos = m.end()
        while True:
            mm = _CR_MORE_RE.match(text, pos)
            if not mm:
                break
            more.append((mm.start("page") - (1 if mm.group("pfx") else 0),
                         mm.end(), (mm.group("pfx") or "").upper(),
                         _num(mm.group("page"))))
            pos = mm.end()
        date, year = "", 0
        daily = bool(pfx)
        pm = _PAREN_RE.match(text, pos)
        if pm:
            body = pm.group("body")
            if re.search(r"daily\s+ed", body, re.IGNORECASE):
                daily = True
            date, year = _date_in(body)
        if m.re is CR_NOVOL_RE:
            # No volume: only a dated daily edition says which one.
            vols = cr_volumes_for_year(year) if date and daily else []
            if not vols:
                continue
            vol = vols[-1]
        if daily and not _cr_daily_linkable(vol, pfx, date):
            continue
        if not daily and not (1 <= vol <= CR_BOUND_MAX_VOL):
            continue
        base = dict(src="cr", vol=vol, daily=daily, date=date, year=year)
        out.append(Cite(m.start(), m.end(),
                        make_spec(page=f"{pfx}{page}", **base)))
        for s, e, p2, n2 in more:
            if daily and not p2:
                p2 = pfx
            if daily and not _cr_daily_linkable(vol, p2, date):
                continue
            out.append(Cite(s, e, make_spec(page=f"{p2}{n2}", **base)))
    return out


def _cr_daily_linkable(vol: int, pfx: str, date: str) -> bool:
    """A daily-edition page GovInfo has (from 1995, by its S/H/E/D page) —
    or an earlier one whose date names the day to open in the bound
    edition."""
    if pfx and vol >= CR_DAILY_MIN_VOL:
        return True
    return bool(date) and 1 <= vol <= CR_BOUND_MAX_VOL


# -- Annals, Register of Debates, Congressional Globe --------------------------

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50}


def _roman(value: str) -> int:
    total, prev = 0, 0
    for ch in reversed(value.upper()):
        v = _ROMAN.get(ch, 0)
        if not v:
            return 0
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


ANNALS_RE = re.compile(
    r"\b(?P<vol>\d{1,2}|[IVXL]{1,6})\s+Annals(?P<of>\s+of\s+Cong(?:ress|\.)?)?"
    r",?\s*(?:at\s+)?(?:pp?\.\s*)?(?P<page>\d{1,4})(?!\d)"
    r"(?:\s*" + _D + r"\s*\d{1,4})?"
)
REGDEB_RE = re.compile(
    r"\b(?P<vol>\d{1,2})\s+(?:Reg(?:ister)?\.?\s*(?:of\s+)?Deb(?:ates|\.)?|Cong\.\s?Deb\.)"
    r"(?:,?\s*(?:pt\.|Pt\.|[Pp]art)\s*(?P<part>\d))?"
    r",?\s*(?:(?P<app>App(?:endix)?\.?),?\s*)?(?:at\s+)?(?:pp?\.\s*)?"
    r"(?P<page>\d{1,4})(?!\d)(?:\s*" + _D + r"\s*\d{1,4})?"
)
GLOBE_RE = re.compile(
    r"Cong(?:ressional)?\.?,?\s*Globe\.?,?\s*"
    r"(?P<cong>\d{1,2})\s?" + _ORD + r"\s+Cong(?:ress)?\.?,?\s*"
    r"(?:(?P<sess>\d)\s?" + _ORD + r"\s+[Ss]ess(?:ion)?\.?)?"
    r"(?:,?\s*(?:pt\.|Pt\.|[Pp]art)\s*\d+)?"
    r",?\s*(?:at\s+)?(?:(?P<app>App(?:endix)?\.?)\s*)?(?:pp?\.\s*)?"
    r"(?P<page>\d{1,4})(?!\d)(?:\s*" + _D + r"\s*\d{1,4})?"
)
# "39th Cong. Globe 1088" (as McDonald v. Chicago cites it).
GLOBE_ALT_RE = re.compile(
    r"\b(?P<cong>\d{1,2})\s?" + _ORD + r"\s+Cong\.?\s*Globe,?\s*"
    r"(?:(?P<sess>\d)\s?" + _ORD + r"\s+[Ss]ess\.?,?\s*)?"
    r"(?:(?P<app>App\.?)\s*)?(?P<page>\d{1,4})(?!\d)(?:\s*" + _D + r"\s*\d{1,4})?"
)
# "Cong. Globe, 1st Sess., 42d Cong., at 575"; "Congressional Globe, 1st
# Session, 39th Congress, part 1, page 474".
GLOBE_REV_RE = re.compile(
    r"Cong(?:ressional)?\.?,?\s*Globe\.?,?\s*"
    r"(?P<sess>\d)\s?" + _ORD + r"\s+[Ss]ess(?:ion)?\.?,?\s*"
    r"(?P<cong>\d{1,2})\s?" + _ORD + r"\s+Cong(?:ress)?\.?"
    r"(?:,?\s*(?:pt\.|Pt\.|[Pp]art)\s*\d+)?"
    r",?\s*(?:at\s+|pages?\s+)?(?:(?P<app>App(?:endix)?\.?)\s*)?(?:pp?\.\s*)?"
    r"(?P<page>\d{1,4})(?!\d)(?:\s*" + _D + r"\s*\d{1,4})?"
)
# "Cong. Globe 1861" — a later page of the Globe last cited in full.
GLOBE_SHORT_RE = re.compile(
    r"Cong\.?\s*Globe,?\s*(?:at\s+)?(?:(?P<app>App\.?)\s*)?(?P<page>\d{1,4})(?!\d)"
    r"(?!\s*" + _ORD + r"\b)(?:\s*" + _D + r"\s*\d{1,4})?"
)
# "Globe 799", "Globe App. 67" — the same, where the opinion calls it "the
# Globe" after citing it in full.
GLOBE_BARE_RE = re.compile(
    r"(?<!Boston\s)(?<![A-Za-z.])Globe\s+(?:(?P<app>App\.?)\s*)?(?P<page>\d{1,4})(?!\d)"
    r"(?!\s*" + _ORD + r"\b)(?:\s*" + _D + r"\s*\d{1,4})?"
)
_PAGE_MORE_RE = re.compile(
    r"\s*,\s*(?:(?P<app>App\.?)\s*)?(?P<page>\d{1,4})(?!\d)(?:\s*" + _D + r"\s*\d{1,4})?"
    r"(?!\s*" + _ORD + r"\b)(?!\s+[A-Z][A-Za-z]*\.)"
)
# "id., at App. 68" after a page of the Globe (or the Register) — the
# appendix's page, which a plain "Id., at 68" reading could not reach.  (A
# plain "id., at 1117" is every citation's; see citations._id_antecedent.)
_ID_AT_RE = re.compile(
    r"\b[Ii]d\.,?\s*at\s+(?P<app>App\.?)\s*(?P<page>\d{1,4})(?!\d)"
    r"(?:\s*" + _D + r"\s*\d{1,4})?")

GLOBE_CONGRESSES = range(23, 43)
ANNALS_VOLUMES = range(1, 43)
REGDEB_VOLUMES = range(1, 15)


def _year_after(text: str, pos: int) -> int:
    ym = _YEAR_PAREN_RE.match(text, pos)
    return int(ym.group("year")) if ym else 0


def _globe_session(cong: int, sess: int, year: int) -> int:
    if sess:
        return sess
    if year:
        first = 1789 + 2 * (cong - 1)
        return 1 if year <= first + 1 else 2
    return 1


def _debates_cites(text: str) -> list[Cite]:
    found: list[tuple[int, int, str, dict, re.Match]] = []
    has_annals, has_deb, has_globe = ("Annals" in text, "Deb" in text,
                                      "Globe" in text)
    if not (has_annals or has_deb or has_globe):
        return []
    for m in (ANNALS_RE.finditer(text) if has_annals else ()):
        v = m.group("vol")
        vol = int(v) if v.isdigit() else (_roman(v) if m.group("of") else 0)
        if vol not in ANNALS_VOLUMES:
            continue
        if not m.group("of") and "Annals of Cong" not in text[:m.start()]:
            continue        # "5 Annals 123": some other annals, unless the
                            # Annals of Congress were cited in full before
        found.append((m.start(), m.end(), "annals",
                      dict(vol=vol, page=int(m.group("page"))), m))
    for m in (REGDEB_RE.finditer(text) if has_deb else ()):
        vol = int(m.group("vol"))
        if vol not in REGDEB_VOLUMES:
            continue
        found.append((m.start(), m.end(), "regdeb",
                      dict(vol=vol, page=int(m.group("page")),
                           part=int(m.group("part") or 0),
                           app=bool(m.group("app"))), m))
    for rx in ((GLOBE_RE, GLOBE_ALT_RE, GLOBE_REV_RE) if has_globe else ()):
        for m in rx.finditer(text):
            cong = int(m.group("cong"))
            if cong not in GLOBE_CONGRESSES:
                continue
            found.append((m.start(), m.end(), "globe",
                          dict(cong=cong, sess=int(m.group("sess") or 0),
                               page=int(m.group("page")),
                               app=bool(m.group("app"))), m))
    found.sort(key=lambda t: (t[0], -t[1]))
    out: list[Cite] = []
    taken: list[tuple[int, int]] = []
    last_globe: "dict | None" = None
    last_any: "tuple[str, dict] | None" = None
    last_end = -1

    def overlaps(s: int, e: int) -> bool:
        return any(s < te and ts < e for ts, te in taken)

    # Short forms and Id.s are read in order with the full cites.
    shorts = [(m.start(), m.end(), "gshort", m)
              for rx in ((GLOBE_SHORT_RE, GLOBE_BARE_RE) if has_globe else ())
              for m in rx.finditer(text)]
    ids = ([(m.start(), m.end(), "id", m) for m in _ID_AT_RE.finditer(text)]
           if has_globe or has_deb else [])
    events = sorted([(s, e, k, d, m) for s, e, k, d, m in found]
                    + [(s, e, k, {}, m) for s, e, k, m in shorts + ids],
                    key=lambda t: (t[0], -t[1]))
    for s, e, kind, fields, m in events:
        if overlaps(s, e):
            continue
        if kind in ("annals", "regdeb", "globe"):
            year = _year_after(text, _list_end(text, e))
            if kind == "globe":
                fields["sess"] = _globe_session(fields["cong"], fields["sess"], year)
                last_globe = dict(fields)
            base = dict(src=kind, year=year, **fields)
            out.append(Cite(s, e, make_spec(**base)))
            taken.append((s, e))
            last_any, last_end = (kind, base), e
            end = _more_pages(text, e, base, out, taken)
            last_end = max(last_end, end)
        elif kind == "gshort":
            if last_globe is None:
                continue
            base = dict(src="globe", **last_globe)
            base["page"] = int(m.group("page"))
            base["app"] = bool(m.group("app"))
            base["year"] = _year_after(text, _list_end(text, e))
            out.append(Cite(s, e, make_spec(**base)))
            taken.append((s, e))
            last_any, last_end = ("globe", base), e
            last_end = max(last_end, _more_pages(text, e, base, out, taken))
        elif kind == "id":
            # Only close behind a debates citation that has an appendix: an
            # "id." further on means whatever was cited in between.
            if (last_any is None or s - last_end > 400
                    or last_any[0] not in ("globe", "regdeb")):
                continue
            between = text[last_end:s]
            if re.search(r"\d\s+[A-Z][\w.]*\.?\s+\d|\bCong\.\s?Rec\b|\bRep\.|\bU\.\s?S\.\s?C", between):
                continue
            base = dict(last_any[1])
            base["page"] = int(m.group("page"))
            base["app"] = True
            base.pop("year", None)
            out.append(Cite(s, e, make_spec(**base)))
            taken.append((s, e))
            last_end = max(e, _more_pages(text, e, base, out, taken))
    return out


def _list_end(text: str, pos: int) -> int:
    while True:
        mm = _PAGE_MORE_RE.match(text, pos)
        if not mm:
            return pos
        pos = mm.end()


def _more_pages(text: str, pos: int, base: dict, out: list, taken: list) -> int:
    """Link the later pages of a list after a page of the debates:
    "Cong. Globe, 40th Cong., 2d Sess., 968, 1129-1131"."""
    while True:
        mm = _PAGE_MORE_RE.match(text, pos)
        if not mm:
            return pos
        spec = dict(base)
        spec["page"] = int(mm.group("page"))
        if mm.group("app"):
            spec["app"] = True
        s = mm.start("app") if mm.group("app") else mm.start("page")
        out.append(Cite(s, mm.end(), make_spec(**spec)))
        taken.append((s, mm.end()))
        pos = mm.end()


# -- Committee reports and documents ------------------------------------------

# The House a report or document is of.  Each alternative opens on its own
# letter, the "not after a word or a period" test just behind it, so the
# regex engine can skip to the S's and H's instead of trying every place.
_CH = (r"(?P<ch>S(?<![\w.]S)(?:\.|enate)"
       r"|H(?<![\w.]H)(?:\.\s?R\.|\.|ouse))")
RPT_RE = re.compile(
    _CH + r"\s*(?P<exec>Exec(?:utive)?\.?\s*)?"
    r"(?P<conf>Conf(?:erence)?\.?\s*)?"
    r"(?:Rep(?:t|ort)?s?\.?|Rpt\.?)\s*(?P<no>No(?:s)?\.?|Number)?\s*"
    r"(?:(?P<cong>\d{1,3})\s*" + _D + r"\s*(?P<num>\d{1,5})|(?P<num2>\d{1,5}))(?![\d\w])"
    r"(?:\s*\((?P<partp>[IVX]{1,4}|\d)\))?"
)
DOC_RE = re.compile(
    r"(?:" + _CH + r"\s*)?"
    r"(?P<dtype>Exec(?:utive)?\.?\s*|Misc(?:ellaneous)?\.?\s*|Mis\.\s*|Treaty\s+)?"
    r"Doc(?:ument)?s?\.?\s*(?P<no>No(?:s)?\.?|Number)\s*"
    r"(?:(?P<cong>\d{1,3})\s*" + _D + r"\s*(?P<num>\d{1,5})|(?P<num2>\d{1,5}))(?![\d\w])"
    r"(?P<partp>)"
)
_TAIL_CONF_RE = re.compile(r"\s*\((?:Conf(?:erence)?\.?\s*Rep(?:ort|t)?\.?)\)")
_TAIL_PART_RE = re.compile(r",?\s*(?:pt\.|Pt\.|[Pp]art)\s*(?P<part>\d{1,2}|[IVX]{1,4})\b")
# "S. Rep. No. 626, on S. 3151, 70th Cong." — the bill it reports on.
_TAIL_BILL_RE = re.compile(
    r",?\s*(?:on|to\s+accompany)\s+(?:S\.|H\.\s?R\.|H\.\s?J\.\s?Res\.|S\.\s?J\.\s?Res\.)\s*\d{1,5}")
_TAIL_CONG_RE = re.compile(
    r",?\s*(?P<cong>\d{1,3})\s?" + _ORD + r"\s+Cong(?:ress)?\.?"
    r"(?:,?\s*(?P<sess>\d)\s?" + _ORD + r"\s+[Ss]ess(?:ion)?\.?)?")
_TAIL_PIN_RE = re.compile(
    r",?\s*(?:supra,?\s*)?(?:(?P<how>at|p\.|pp\.|Pp\.)\s*)?(?P<pin>\d{1,4})(?!\d)"
    r"(?:\s*" + _D + r"\s*\d{1,4})?(?!\s*" + _ORD + r"\b)(?!\s+[A-Z][A-Za-z]*\.)")
_TAIL_YEAR_RE = re.compile(r"\s*\((?P<year>1[789]\d\d|20\d\d)\)")
# "(hereinafter House Report)", after up to two other parentheticals.
_HEREINAFTER_RE = re.compile(
    r"(?:\s*\([^()]{0,60}\)){0,2}\s*\((?:[^()]{0,40}?;\s*)?"
    r"hereinafter(?:\s+cited\s+as)?\s+(?P<alias>[^()]{2,40}?)\s*\)")
# The reprint West's U.S. Code Congressional and Administrative News makes
# of a report: "reprinted in 1978 U.S.C.C.A.N. 9260, 9264", "1976 U.S. Code
# Cong. & Admin. News 5659".  It is the report, so it opens the report.
_USCCAN_RE = re.compile(
    r"[,;]?\s*(?:reprinted\s+in|in|,)?\s*(?P<cite>(?:1[89]\d\d|20\d\d)\s+"
    r"(?:U\.\s?S\.\s?C\.\s?C\.\s?A\.\s?N\.|U\.\s?S\.\s?Code\s+Cong\.\s*(?:&|and)\s*"
    r"(?:Admin|Adm|Ad)\.\s*News),?\s*(?:p\.\s*)?\d{1,5}"
    r"(?:\s*,\s*\d{1,5}(?!\s*[A-Z]))?)")
# A report named by its House alone, cited back to: "S. Rep., supra, at
# 27", "H.R. Rep., at 3", "S.Rep. at 32".
_BARE_RPT_RE = re.compile(
    r"(?P<ch>S(?<![\w.]S)\.|H(?<![\w.]H)(?:\.\s?R\.|\.))\s*(?P<conf>Conf\.\s*)?Rep(?:t|ort)?\.?"
    r"(?!\s*(?:No|Nos|Number)\b)"
    r",?\s*(?:supra,?\s*)?(?:at\s+|pp?\.\s*)(?P<pin>\d{1,4})(?!\d)"
    r"(?:\s*" + _D + r"\s*\d{1,4})?")


def _report_tail(text: str, pos: int) -> tuple[dict, int, int]:
    """What follows a report's number: "(Conf. Rep.)", its part, the bill
    it reports on, its Congress and session, a pin page, the year.  Returns
    the facts, where the citation's text ends (before the year), and where
    everything read ends."""
    info: dict = {}
    m = _TAIL_CONF_RE.match(text, pos)
    if m:
        info["conf"] = True
        pos = m.end()
    m = _TAIL_PART_RE.match(text, pos)
    if m:
        info["part"] = m.group("part")
        pos = m.end()
    m = _TAIL_BILL_RE.match(text, pos)
    if m and _TAIL_CONG_RE.match(text, m.end()):
        pos = m.end()
    m = _TAIL_CONG_RE.match(text, pos)
    sess = False
    if m:
        info["cong"] = int(m.group("cong"))
        sess = bool(m.group("sess"))
        pos = m.end()
        m = _TAIL_PART_RE.match(text, pos)
        if m:
            info["part"] = m.group("part")
            pos = m.end()
    m = _TAIL_PIN_RE.match(text, pos)
    # A bare number is a page only right after the session ("2d Sess. 739");
    # after the report's own number it needs its "at" or "p.".
    if m and (m.group("how") or sess):
        info["pin"] = m.group("pin")
        pos = m.end()
    link_end = pos
    m = _TAIL_YEAR_RE.match(text, pos)
    if m:
        info["year"] = int(m.group("year"))
        pos = m.end()
    m = _TAIL_CONF_RE.match(text, pos)
    if m:
        info["conf"] = True
        pos = m.end()
    if "part" in info:
        p = info["part"]
        info["part"] = int(p) if p.isdigit() else _roman(p)
    return info, link_end, pos


def _alias_re(alias: str) -> "re.Pattern | None":
    """The pattern of a report's "hereinafter" name used as a short form:
    "House Report, at 53", "S. Rep. 20", "H. R. Rep., supra, at 27"."""
    alias = alias.strip().rstrip(",;")
    if not re.search(r"Rep|Report", alias) or len(alias) > 40:
        return None
    body = r"\s*".join(re.escape(tok) for tok in re.split(r"\s+", alias))
    body = body.replace(r"\.", r"\.?")
    return re.compile(r"(?<![\w.])" + body + r",?\s*(?:supra,?\s*)?(?:at\s+|pp?\.\s*)?"
                      r"(?P<pin>\d{1,4})(?!\d)(?:\s*" + _D + r"\s*\d{1,4})?"
                      r"(?!\s*" + _ORD + r"\b)(?!\s*" + _D + r"\s*\d)")


def _paper_cites(text: str) -> list[Cite]:
    """Committee reports and documents.  A later short form takes what it
    leaves out from the full cite before it: "S. Rep. No. 1580, at 9" its
    Congress, "S. Rep., supra, at 27" the last Senate report cited,
    "House Report, at 53" the report the opinion named "House Report"."""
    if not ("Rep" in text or "Rpt" in text or "Doc" in text):
        return []
    events = [("rpt", m) for m in RPT_RE.finditer(text)]
    for m in DOC_RE.finditer(text):
        if not m.group("ch") and not (m.group("dtype") or "").startswith("Treaty"):
            continue
        events.append(("doc", m))
    events.sort(key=lambda t: (t[1].start(), -t[1].end()))
    known: dict[tuple, int] = {}
    fulls: list[tuple[int, dict]] = []          # (end, spec fields) in order
    aliases: list[tuple[int, re.Pattern, dict]] = []
    out: list[Cite] = []
    pos_taken = -1
    for kind, m in events:
        if m.start() < pos_taken:
            continue
        ch_tok = (m.group("ch") or "S.").replace(" ", "")
        ch = "s" if ch_tok.startswith("S") else "h"
        spelled = ch_tok in ("Senate", "House")
        num = int(m.group("num") or m.group("num2"))
        cong = int(m.group("cong")) if m.group("cong") else 0
        tail, link_end, read_end = _report_tail(text, m.end())
        # A number is a report's only with its "No.", its Congress ("95-797")
        # or its Congress after it: "S. Rep. 20" is page 20 of a report the
        # opinion named "S. Rep.", and "1998 Senate Report 43-44" pages.
        if not (m.group("no") or tail.get("cong")
                or (cong and not spelled)):
            continue
        cong = cong or tail.get("cong", 0)
        if kind == "rpt":
            fields = dict(src="rpt", ch=ch, exec=bool(m.group("exec")),
                          conf=bool(m.group("conf")) or tail.get("conf", False))
            key = ("rpt", ch, bool(m.group("exec")), num)
        else:
            dt = (m.group("dtype") or "").strip().lower()
            dtype = ("exec" if dt.startswith("exec") else "misc" if dt.startswith("mis")
                     else "treaty" if dt.startswith("treaty") else "doc")
            if dtype == "treaty":
                ch = "s"
            fields = dict(src="doc", ch=ch, type=dtype)
            key = ("doc", ch, dtype, num)
        year = tail.get("year", 0)
        if not cong:
            cong = known.get(key, 0)
        if not cong and year:
            cands = congresses_for_year(year)
            if len(cands) == 1:
                cong = cands[0]
            else:
                fields["congs"] = ",".join(str(c) for c in cands)
        if cong:
            known.setdefault(key, cong)
        elif "congs" not in fields:
            continue            # no Congress to look the report up in
        if cong and not 1 <= cong <= 200:
            continue
        part = tail.get("part", 0)
        if m.group("partp"):
            p = m.group("partp")
            part = int(p) if p.isdigit() else _roman(p)
        whole = dict(cong=cong, num=num, part=part, year=year, **fields)
        spec = make_spec(pin=tail.get("pin", ""), **whole)
        out.append(Cite(m.start(), link_end, spec))
        pos_taken = link_end
        fulls.append((link_end, whole))
        after = read_end
        h = _HEREINAFTER_RE.match(text, after)
        if h:
            rx = _alias_re(h.group("alias"))
            if rx is not None:
                aliases.append((h.end(), rx, whole))
            after = h.end()
        u = _USCCAN_RE.match(text, after)
        if u and kind == "rpt":
            out.append(Cite(u.start("cite"), u.end("cite"), make_spec(**whole)))
            pos_taken = u.end("cite")
    # Short forms, each read against what was cited before it.
    for m in _BARE_RPT_RE.finditer(text):
        ch = "s" if m.group("ch").startswith("S") else "h"
        prior = [w for end, w in fulls
                 if end <= m.start() and w["src"] == "rpt" and w["ch"] == ch]
        if prior:
            out.append(Cite(m.start(), m.end(),
                            make_spec(pin=m.group("pin"), **prior[-1])))
    for defined_at, rx, whole in aliases:
        for m in rx.finditer(text, defined_at):
            out.append(Cite(m.start(), m.end(),
                            make_spec(pin=m.group("pin"), **whole)))
    return out


# -- Everything ------------------------------------------------------------------

_ANY_RE = re.compile(r"Rec\b|Rec\.|Rep|Rpt|Doc|Globe|Annals|Deb")


def iter_cites(text: str) -> list[tuple[int, int, str]]:
    """Every legislative-history citation in *text* worth a link, as
    ``(start, end, spec)`` in document order, overlaps resolved (first,
    then longest, wins)."""
    if not text or not _ANY_RE.search(text):
        return []
    cites = _cr_cites(text) + _debates_cites(text) + _paper_cites(text)
    cites.sort(key=lambda c: (c.start, -c.end))
    out: list[tuple[int, int, str]] = []
    pos = -1
    for c in cites:
        if c.start < pos:
            continue
        out.append(c.as_tuple())
        pos = c.end
    return out


def parse_query(query: str) -> "tuple[str, str] | None":
    """A Spotlight query that is one legislative-history citation, as the
    ``("leghist", spec)`` action that opens it."""
    text = (query or "").strip().rstrip(".").strip()
    if not text:
        return None
    found = iter_cites(text)
    if len(found) != 1:
        return None
    start, end, spec = found[0]
    # The citation must be the whole query, give or take its parenthetical.
    rest = (text[:start] + " " + text[end:]).strip()
    rest = re.sub(r"\([^()]*\)", "", rest).strip(" ,.;")
    if rest:
        return None
    return ("leghist", spec)


# ---------------------------------------------------------------------------
# The shipped indexes
# ---------------------------------------------------------------------------

CRECB_INDEX_FILENAME = "crecb_index.tsv.gz"
DEBATES_INDEX_FILENAME = "debates_index.tsv.gz"
GOVINFO_PKG = "https://www.govinfo.gov/content/pkg/"
CONGRESS_GOV = "https://www.congress.gov"


def _index_path(name: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


@dataclass(frozen=True)
class Granule:
    """A stretch of the bound Record GovInfo holds as one PDF: a chamber's
    proceedings on one day ("S", "H", "E"), a whole day ("X"), the Appendix
    ("A") or the Daily Digest ("D"); "p" after the kind marks a Senate
    special session, paged apart from the regular session."""
    vol: int
    first: int
    last: int
    kind: str
    date: str
    path: str

    @property
    def url(self) -> str:
        return GOVINFO_PKG + self.path


_CRECB: "dict[int, list[Granule]] | None" = None
_CRECB_LOCK = threading.Lock()


def _crecb() -> "dict[int, list[Granule]]":
    global _CRECB
    with _CRECB_LOCK:
        if _CRECB is None:
            out: dict[int, list[Granule]] = {}
            try:
                with gzip.open(_index_path(CRECB_INDEX_FILENAME), "rt",
                               encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("#"):
                            continue
                        p = line.rstrip("\n").split("\t")
                        if len(p) < 6:
                            continue
                        g = Granule(int(p[0]), int(p[1]), int(p[2]), p[3], p[4], p[5])
                        out.setdefault(g.vol, []).append(g)
            except OSError:
                pass
            _CRECB = out
        return _CRECB


_CHAMBER_HINT_RE = re.compile(
    r"\((?:Senate|remarks of Sen\.|statement of Sen\.|Sen\.)|\b(?:Senator|Sen\.)\s+[A-Z]"
    r"|(?P<house>\((?:House|remarks of Rep\.|statement of Rep\.|Rep\.)|\b(?:Representative|Rep\.|Mr\.\s+Speaker)\s+[A-Z])")


def crecb_granule(vol: int, page: int, *, appendix: bool = False,
                  digest: bool = False, chamber: str = "") -> "Granule | None":
    """The bound-Record PDF holding *page* of volume *vol* — the chamber's
    day where GovInfo divides it so (*chamber* "S", "H" or "E" breaks a tie
    on a page two chambers share), else the whole day.  A special session's
    pages are a separate run, used only when the regular session has no
    such page."""
    rows = _crecb().get(vol) or []
    want = {"A"} if appendix else {"D"} if digest else {"S", "H", "E", "X"}
    cands = [g for g in rows if g.kind.rstrip("p") in want and g.first <= page <= g.last]
    if not cands:
        return None
    regular = [g for g in cands if not g.kind.endswith("p")]
    cands = regular or cands

    def rank(g: Granule) -> tuple:
        k = g.kind.rstrip("p")
        return (0 if chamber and k == chamber else 1,
                0 if k != "X" else 1,
                g.last - g.first,
                0 if page < g.last else 1)

    return min(cands, key=rank)


def crecb_day(vol: int, date: str, chamber: str = "") -> "Granule | None":
    """The bound Record's proceedings of *date* — in *chamber* ("S", "H",
    "E") where GovInfo divides the day so, else the whole day."""
    rows = [g for g in _crecb().get(vol) or [] if g.date == date]
    if not rows:
        return None
    if chamber:
        mine = [g for g in rows if g.kind.rstrip("p") == chamber]
        if mine:
            return min(mine, key=lambda g: g.first)
    whole = [g for g in rows if g.kind.rstrip("p") == "X"]
    return min(whole or rows, key=lambda g: g.first)


def chamber_hint(text: str) -> str:
    """"S" or "H" when a citation's parenthetical names the speaker's
    chamber ("(remarks of Sen. Hatch)", "(House)"), else ""."""
    m = _CHAMBER_HINT_RE.search(text or "")
    if not m:
        return ""
    return "H" if m.group("house") else "S"


@dataclass(frozen=True)
class DebatesPage:
    """A page of the Annals, the Register or the Globe, printed with
    *lo*-*hi* (two columns to a page in the Annals and the Register) and
    dated *date*: page *index* (from 0) of the Congress.gov PDF at *url* —
    or, where the PDF's pages can't be matched to the volume's (*url* "",
    *index* -1), the page's own image at *image*."""
    url: str
    index: int
    lo: int
    hi: int
    date: str
    image: str = ""


_DEBATES: "dict[str, list[DebatesPage]] | None" = None
_DEBATES_LOCK = threading.Lock()


def _absolute(url: str) -> str:
    return CONGRESS_GOV + url if url.startswith("/") else url


def _debates() -> "dict[str, list[DebatesPage]]":
    """{"globe 39.1a": [DebatesPage, ...] in page order}."""
    global _DEBATES
    with _DEBATES_LOCK:
        if _DEBATES is None:
            out: dict[str, list] = {}
            pdfs: dict[str, str] = {}
            try:
                with gzip.open(_index_path(DEBATES_INDEX_FILENAME), "rt",
                               encoding="utf-8") as f:
                    for line in f:
                        p = line.rstrip("\n").split("\t")
                        if p[0] == "#pdf" and len(p) >= 3:
                            pdfs[p[1]] = p[2]
                            continue
                        if line.startswith("#") or len(p) < 6:
                            continue
                        series, key, lo, hi, pdf, idx = p[:6]
                        date = p[6] if len(p) > 6 else ""
                        image = p[7] if len(p) > 7 else ""
                        url = pdfs.get(pdf, "") if pdf != "-" else ""
                        out.setdefault(f"{series} {key}", []).append(DebatesPage(
                            _absolute(url) if url else "", int(idx), int(lo),
                            int(hi), date, _absolute(image) if image else ""))
            except OSError:
                pass
            for rows in out.values():
                rows.sort(key=lambda d: (d.lo, d.index))
            _DEBATES = out
        return _DEBATES


def debates_key(spec: "str | dict") -> str:
    s = parse_spec(spec)
    src = s.get("src")
    app = "a" if s.get("app") else ""
    if src == "globe":
        return f"globe {s.get('cong')}.{s.get('sess') or 1}{app}"
    if src == "annals":
        return f"annals {s.get('vol')}"
    if src == "regdeb":
        return f"regdeb {s.get('vol')}{app}"
    return ""


def debates_pages(spec: "str | dict", before: int = 0,
                  after: int = 0) -> "tuple[list[DebatesPage], int]":
    """The page a debates citation names, with up to *before* and *after*
    pages of the same volume around it — and which of them is the cited one.
    ([], -1) when the index has no such page."""
    s = parse_spec(spec)
    page = int(s.get("page") or 0)
    rows = _debates().get(debates_key(s)) or []
    for i, d in enumerate(rows):
        if d.lo <= page <= d.hi:
            first = max(0, i - before)
            return rows[first:i + after + 1], i - first
    return [], -1


def debates_page(spec: "str | dict") -> "DebatesPage | None":
    """The Congress.gov page a debates citation names."""
    pages, at = debates_pages(spec)
    return pages[at] if pages else None


def has_indexes() -> bool:
    """Whether the shipped indexes are present (a trimmed install may lack
    them, and then the Record's bound pages and the early debates resolve
    nothing)."""
    return bool(_crecb()) and bool(_debates())
