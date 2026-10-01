"""Fetch and parse U.S. Code sections from the Office of the Law Revision
Counsel (uscode.house.gov), the House's official online edition.

The OLRC "prelim" edition serves one section per page at

    https://uscode.house.gov/view.xhtml?req=granuleid:
        USC-prelim-title{T}-section{S}&num=0&edition=prelim

Pages are machine-generated with a stable structure: HTML comments
``<!-- field-start:statute -->`` … ``<!-- field-end:statute -->`` delimit
the head, statute text, source credit, and notes, and every paragraph
carries a CSS class encoding its role and indentation depth
(``statutory-body``, ``statutory-body-1em``, …, ``subsection-head``).
``parse_section()`` walks those markers; the GUI renders the resulting
(kind, indent, text) stream with bolding and indentation.
"""

from __future__ import annotations

import html as _html
import re
import threading
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Citation recognition
# ---------------------------------------------------------------------------

# "42 U.S.C. § 1983", "28 U. S. C. §2254(d)(1)", "18 U.S.C. §§ 922(g)(1)",
# "15 U.S.C.A. § 78j(b)", "42 U.S.C. § 2000e-2(a)", "5 U.S.C. 552".
# A parenthesized subdivision is 1-4 alphanumerics but never 4 digits, so a
# trailing year parenthetical "(1982)" is not swallowed.
#
# Off the printed page the Code is written other ways too, and web pages use
# them all: without the periods ("42 USC § 1983", "5 USC 552", the way
# "29 CFR 1614.105" goes for the C.F.R.), Lexis's annotated "U.S.C.S.", and
# "42 U.S. Code § 1983" (Cornell's and Wikipedia's style).  The unpunctuated
# form is matched in capitals only, so prose about the university is not.
USC_CITE_RE = re.compile(
    r"\b(\d{1,2})\s+"
    r"(?:U\.\s?S\.\s?C\.?\s?(?:[AS]\.)?|USC[AS]?\b\.?|U\.\s?S\.\s?Code\b)\s*"
    r"(?:§§?|[Ss]ec(?:tions?|s)?\.?)?\s*"
    r"(\d+[a-zA-Z0-9]*(?:[-–—]\d+[a-zA-Z0-9]*)?)"
    r"((?:\s?\((?:\d{1,3}|[ivxIVX]{2,4}|[a-zA-Z]{1,3})\))*)"
)


def cite_spec(m: re.Match) -> str:
    """Compact "title:section:sub,sub" spec from a USC_CITE_RE match."""
    section = m.group(2).replace("–", "-").replace("—", "-")
    subs = re.findall(r"\(([^)]+)\)", m.group(3) or "")
    return f"{m.group(1)}:{section}:{','.join(subs)}"


def spec_label(spec: str) -> str:
    """Display form of a cite_spec: '42 U.S.C. § 1983(b)(1)'."""
    title, section, subs = spec.split(":", 2)
    tail = "".join(f"({s})" for s in subs.split(",") if s)
    return f"{title} U.S.C. § {section}{tail}"


# ---------------------------------------------------------------------------
# Enumerator-level inference (used by ecfr.py and fed_rules.py)
#
# Where a source gives no usable indentation, nesting is inferred from the
# enumerators themselves — each enumerator type at its own level.
# Hierarchies differ: C.F.R. runs (a) -> (1) -> (i) -> (A), the Federal
# Rules their own way.  The "(i) after (h)" ambiguity is resolved by
# preferring a successor at an already-open level over starting a deeper
# one.  The U.S. Code does not need this: the OLRC's pages carry their own
# indentation, which is followed as it stands (see parse_section).
# ---------------------------------------------------------------------------

# CFR subdivisions repeat the Arabic/lower-roman pair after the capital-letter
# level: (a) -> (1) -> (i) -> (A) -> (1) -> (i).  Deep Treasury regulations
# use the whole sequence (for example 26 C.F.R. § 1.36B-2(c)(3)(v)(A)(1)).
CFR_HIERARCHY = ("a", "1", "i", "A", "1", "i")

ENUM_LEAD_RE = re.compile(r"^((?:\((?:\d{1,3}|[a-zA-Z]{1,5})\)\s*)+)")

_ROMAN_VALS = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500,
               "m": 1000}


def _roman_to_int(s: str) -> int:
    total, prev = 0, 0
    for ch in reversed(s.lower()):
        v = _ROMAN_VALS.get(ch, 0)
        total += v if v >= prev else -v
        prev = max(prev, v)
    return total


def _enum_value(enum: str, kind: str) -> int:
    """Ordinal of an enumerator interpreted as `kind` (one of "1", "a",
    "A", "i", "I"), or 0 if it doesn't fit that kind."""
    if kind == "1":
        return int(enum) if enum.isdigit() else 0
    if kind in ("a", "A"):
        ok = enum.islower() if kind == "a" else enum.isupper()
        # single letters a..z, then repeated letters (aa), (bb), ...
        if ok and enum.isalpha() and len(set(enum.lower())) == 1:
            return 26 * (len(enum) - 1) + ord(enum[0].lower()) - ord("a") + 1
        return 0
    if kind in ("i", "I"):
        ok = enum.islower() if kind == "i" else enum.isupper()
        if ok and enum and all(c in _ROMAN_VALS for c in enum.lower()):
            return _roman_to_int(enum)
        return 0
    return 0


def infer_enum_level(enums: list[str], stack: list[tuple[str, str]],
                     hierarchy: tuple[str, ...]) -> int | None:
    """Indent level for a paragraph opening with `enums`, updating `stack`
    (open levels as (kind, enum) pairs) in place.  Returns None — leaving
    the stack untouched — when the first token cannot be an enumerator at
    all ("(See)"), so the caller can keep its fallback indent."""
    e = enums[0]
    if not any(_enum_value(e, k) for k in ("1", "a", "A", "i", "I")):
        return None
    level = kind = None
    # 1) successor of an open level, deepest first — "(i)" after "(h)"
    #    continues that level rather than starting romans
    for lvl in range(len(stack) - 1, -1, -1):
        k, prev = stack[lvl]
        if _enum_value(prev, k) and \
                _enum_value(e, k) == _enum_value(prev, k) + 1:
            level, kind = lvl, k
            break
    # 2) the first value one level deeper
    if level is None:
        k = hierarchy[min(len(stack), len(hierarchy) - 1)]
        if _enum_value(e, k) == 1:
            level, kind = len(stack), k
    # 3) the first value of some shallower level
    if level is None:
        for lvl in range(min(len(stack), len(hierarchy)) - 1, -1, -1):
            if _enum_value(e, hierarchy[lvl]) == 1:
                level, kind = lvl, hierarchy[lvl]
                break
    if level is None:  # give up gracefully: sibling of the deepest level
        level = max(len(stack) - 1, 0)
        kind = hierarchy[min(level, len(hierarchy) - 1)]
    del stack[level:]
    stack.append((kind, e))
    # further enumerators in the same paragraph open deeper levels
    for extra in enums[1:]:
        k = hierarchy[min(len(stack), len(hierarchy) - 1)]
        stack.append((k, extra))
    return level


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


def section_url(title: str, section: str) -> str:
    return (
        "https://uscode.house.gov/view.xhtml?req=granuleid:"
        f"USC-prelim-title{title}-section{section}&num=0&edition=prelim"
    )


@dataclass
class UscSection:
    title: str
    section: str
    url: str
    # (kind, indent, text); kind in {"sechead", "head", "body", "credit",
    # "note-head", "note-body"}
    paras: list[tuple[str, int, str]] = field(default_factory=list)
    # deepest container granule from the page breadcrumbs, e.g.
    # "title26-chapter48" — its table of sections gives document order
    container: str | None = None
    # the units above the section, outermost first, as the page's navigation
    # bar names them: ("title42", "TITLE 42"), ("title42-chapter21",
    # "CHAPTER 21") … — see load_unit
    crumbs: list[tuple[str, str]] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return "usc"

    def neighbors(self) -> tuple[tuple[str, str] | None,
                                 tuple[str, str] | None]:
        """Adjacent sections, from the container's table of sections
        (fetched lazily and cached).  Failures yield (None, None)."""
        if not self.container:
            print(f"[usc-nav] no container breadcrumb found for "
                  f"§ {self.section}")
            return None, None
        with _cache_lock:
            order = _order_cache.get(self.container)
        if order is None:
            # The container's table of contents — the same one its
            # breadcrumb opens, fetched once for both.
            try:
                order = [entry.section
                         for entry in load_unit(self.container).entries
                         if entry.kind == "section"]
            except Exception as exc:
                print(f"[usc-nav] contents of {self.container} failed: "
                      f"{exc}")
                order = []
        if not order:
            try:
                order = _container_sections(self.container)
            except Exception as exc:
                print(f"[usc-nav] section list fetch failed for "
                      f"{self.container}: {exc}")
                return None, None
        try:
            i = next(idx for idx, s in enumerate(order)
                     if s.lower() == self.section.lower())
        except StopIteration:
            print(f"[usc-nav] § {self.section} not in {self.container} "
                  f"list ({len(order)} sections: {order[:8]}…)")
            return None, None
        prev = (self.title, order[i - 1]) if i > 0 else None
        nxt = (self.title, order[i + 1]) if i + 1 < len(order) else None
        return prev, nxt

    @property
    def heading(self) -> str:
        for kind, _i, text in self.paras:
            if kind == "sechead":
                return text
        return f"{self.title} U.S.C. § {self.section}"

    @property
    def label(self) -> str:
        return f"{self.title} U.S.C. § {self.section}"

    @property
    def source_name(self) -> str:
        return "U.S. Code (OLRC)"

    @property
    def source_note(self) -> str:
        return "OLRC preliminary edition (current law)"

    def bluebook_cite(self, subs: tuple = ()) -> str:
        """Bluebook citation (rule 12.3); current official code, so no
        edition year: '42 U.S.C. § 1983(b)(1)'."""
        tail = "".join(f"({s})" for s in subs)
        return f"{self.title} U.S.C. § {self.section}{tail}"


_cache: dict[tuple[str, str], UscSection] = {}
_cache_lock = threading.Lock()


class SectionNotFound(RuntimeError, LookupError):
    """The Code has no such section — as against the site failing to answer,
    which stays a plain RuntimeError.  A LookupError, so a caller can tell the
    two apart without knowing which source it asked."""


def load_section(title: str, section: str) -> UscSection:
    """Fetch and parse a section, with an in-memory cache.  For a range or
    hyphenated section that the OLRC does not know ("78a-78pp"), falls back
    to the part before the dash.  Raises RuntimeError with a readable
    message on failure."""
    title, section = str(title).strip(), str(section).strip()
    key = (title, section)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    import requests

    candidates = [section]
    if "-" in section:
        candidates.append(section.split("-", 1)[0])
    last_err = "section not found"
    for cand in candidates:
        url = section_url(title, cand)
        try:
            resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=30)
            if resp.status_code == 404:
                last_err = f"no such section {title} U.S.C. § {cand}"
                continue
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"uscode.house.gov: {exc}") from exc
        # Decode explicitly: a missing charset in the Content-Type would
        # otherwise turn "§" into "Â§" via requests' Latin-1 fallback
        page = resp.content.decode("utf-8", "replace")
        paras = parse_section(page)
        if paras:
            doc = UscSection(title=title, section=cand, url=url, paras=paras)
            doc.container = _find_container(page)
            doc.crumbs = page_crumbs(page)[0]
            with _cache_lock:
                _cache[key] = doc
            return doc
        last_err = f"no text found for {title} U.S.C. § {cand}"
    raise SectionNotFound(f"uscode.house.gov: {last_err}")


# Previous/next navigation.  The OLRC viewer's own prev/next controls are
# JSF form postbacks (no usable hrefs), so neighbors are derived instead
# from the page's breadcrumb trail: the deepest container granule (part,
# subchapter, or chapter) is fetched — streamed, stopping at the end of
# its "analysis" field — and its table of sections gives document order.

_GRANULE_PATH_RE = re.compile(
    r"granuleid(?:%3[Aa]|:)USC-prelim-([A-Za-z0-9\-]+)"
)


def _find_container(page_html: str) -> str | None:
    """Deepest non-section granule path in the page's breadcrumb links,
    e.g. "title26-chapter48"."""
    best = None
    for m in _GRANULE_PATH_RE.finditer(page_html):
        path = m.group(1)
        if "-section" in path.lower():
            continue
        # breadcrumbs run shallow -> deep, so on equal depth keep the
        # later one (Subtitle D and Chapter 48 both have one dash)
        if best is None or path.count("-") >= best.count("-"):
            best = path
    return best


# The closing comment is optional so a streamed buffer cut mid-comment
# still parses; the cell classes only occur inside analyses, so falling
# back to the whole buffer when the field markers are absent is safe.
_ANALYSIS_REGION_RE = re.compile(
    r"<!--\s*field-start:analysis\s*-->(.*?)"
    r"(?:<!--\s*field-end:analysis|\Z)",
    re.DOTALL,
)
_ANALYSIS_LEFT_RE = re.compile(
    r'class="(?:two|three)-column-analysis-style-content-left"[^>]*>'
    r"(.*?)</(?:div|td)>",
    re.DOTALL,
)
_ANALYSIS_SEC_RE = re.compile(
    r"\[?(\d+[A-Za-z0-9]*(?:[-–]\d+[A-Za-z0-9]*)?)"
)
# Fallback ordering: the section heads themselves, in document order
_SECHEAD_NUM_RE = re.compile(
    r'<h3\s+class="section-head"[^>]*>\s*(?:<[^>]+>\s*)*\[?\s*'
    # OLRC's current container pages emit ``&sect;1983``; older/saved
    # pages and tests may contain the decoded character or a numeric entity.
    r"(?:(?:§|&sect;|&#0*167;|&#x0*a7;)\s*)+"
    r"(\d+[A-Za-z0-9]*(?:[-–]\d+[A-Za-z0-9]*)?)",
    re.IGNORECASE,
)


def _sections_from_analysis(page_html: str) -> list[str]:
    """Ordered section numbers from a container page's table of sections.
    A range entry ("5001 to 5011. Repealed.") contributes its first
    number; bracketed (repealed/omitted) entries are kept — the OLRC
    serves a page for them too.  When no analysis cells are found, the
    section heads in the buffer provide the order instead."""
    m = _ANALYSIS_REGION_RE.search(page_html)
    region = m.group(1) if m else page_html
    out: list[str] = []
    for cell in _ANALYSIS_LEFT_RE.finditer(region):
        text = _clean(cell.group(1))
        sec = _ANALYSIS_SEC_RE.match(text)
        if sec:
            ident = sec.group(1).replace("–", "-")
            if not out or out[-1] != ident:
                out.append(ident)
    if not out:
        for head in _SECHEAD_NUM_RE.finditer(page_html):
            ident = head.group(1).replace("–", "-")
            if not out or out[-1] != ident:
                out.append(ident)
    return out


_order_cache: dict[str, list[str]] = {}


def _container_sections(container: str) -> list[str]:
    """Ordered sections of a container granule, cached.  The container
    page holds the whole unit's text, so the response is streamed and cut
    off once the table of sections has arrived."""
    with _cache_lock:
        if container in _order_cache:
            return _order_cache[container]

    import requests

    url = ("https://uscode.house.gov/view.xhtml?req=granuleid:"
           f"USC-prelim-{container}&num=0&edition=prelim")
    resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=30,
                        stream=True)
    try:
        resp.raise_for_status()
        buf = b""
        for chunk in resp.iter_content(65536):
            buf += chunk
            if b"field-end:analysis" in buf or len(buf) > 2_000_000:
                break
    finally:
        resp.close()
    order = _sections_from_analysis(buf.decode("utf-8", "replace"))
    print(f"[usc-nav] {container}: {len(order)} sections "
          f"from {len(buf):,} bytes")
    if order:
        with _cache_lock:
            _order_cache[container] = order
    return order


# ---------------------------------------------------------------------------
# The Code above the section
#
# Every unit the Code is divided into — title, subtitle, part, chapter,
# subchapter, subpart — has an OLRC page of its own: its heading, a table of
# its contents, then the full text of everything in it.  The table lists the
# unit's sections ("1983. | Civil action for deprivation of rights.") or the
# units below it ("21. | Civil Rights | 1981"), under subheads for the groups
# in between ("SUBCHAPTER I—GENERALLY"), and in a big chapter runs on through
# notes, part after part.  Only that much of the page is read; the text after
# it can run to hundreds of megabytes.  A unit with no table of its own, as
# the lowest ones often are, is listed in its parent's under its own subhead.
#
# Every page's navigation bar names the units above it by page id
# ("title42-chapter21"), so going up is exact.  A table names a unit below
# only by its designation ("Subchapter A"); its page id follows the OLRC's
# naming ("title26-chapter1-subchapterA", roman numerals mostly as digits),
# and where that misses, the navigation bar of its first section names it.
# ---------------------------------------------------------------------------

@dataclass
class UnitEntry:
    """One line of a unit's table of contents."""

    kind: str               # "group" (a subhead), "section", or "unit"
    depth: int              # groups nest; an entry sits one in from its group
    label: str              # "§ 1983", "Chapter 21", "SUBCHAPTER I—GENERALLY"
    heading: str = ""       # "Civil action for deprivation of rights."
    section: str = ""       # a section entry's number: "1983"
    unit_kind: str = ""     # a unit entry's "chapter", "subchapter" …
    designation: str = ""   # a unit entry's "21", "A", "IV"
    granule: str = ""       # a unit entry's page id, as the OLRC names them
    first_section: str = "" # a unit entry's first section, where the table
                            # gives it


@dataclass
class UscUnit:
    """A title, chapter, subchapter … of the Code, with its contents."""

    title: str
    granule: str            # "title42-chapter21"
    url: str
    label: str              # "Chapter 21"
    heading: str            # "CHAPTER 21—CIVIL RIGHTS"
    # the units above this one, outermost first: ("title42", "TITLE 42")
    crumbs: list[tuple[str, str]] = field(default_factory=list)
    entries: list[UnitEntry] = field(default_factory=list)
    # the contents were read from the unit's own text and stopped short
    partial: bool = False


class UnitNotFound(RuntimeError, LookupError):
    """The OLRC has no page by that id."""


def unit_url(granule: str) -> str:
    return (
        "https://uscode.house.gov/view.xhtml?req=granuleid:"
        f"USC-prelim-{granule}&num=0&edition=prelim"
    )


def crumb_label(label: str) -> str:
    """A navigation-bar label as the reader shows it: "SUBCHAPTER I" →
    "Subchapter I", "part 5" → "Part 5", "TITLE 42" → "Title 42"."""
    words = (label or "").split(None, 1)
    if not words:
        return ""
    head = words[0][:1].upper() + words[0][1:].lower()
    return f"{head} {words[1]}" if len(words) > 1 else head


_NAV_CRUMB_RE = re.compile(
    r'granuleid(?:%3A|:)USC-prelim-([A-Za-z0-9\-]+)[^"]*"\s+'
    r'class="link_class">([^<]+)</a>'
)
_NAV_OWN_RE = re.compile(
    r'<span style="font-size: 11px; font-weight: bold;">([^<]*)</span>'
)


def page_crumbs(page_html: str) -> tuple[list[tuple[str, str]], str]:
    """The units above a page, outermost first, as (page id, label) — and
    the page's own label ("CHAPTER 21", "§ 1983") — from its navigation
    bar."""
    start = page_html.find('class="navigator"')
    if start < 0:
        return [], ""
    end = page_html.find("</form>", start)
    nav = page_html[start:end if end > 0 else len(page_html)]
    crumbs = [(granule, _clean(label))
              for granule, label in _NAV_CRUMB_RE.findall(nav)]
    own = _NAV_OWN_RE.search(nav)
    return crumbs, _clean(own.group(1)) if own else ""


# A unit's own heading: a title's <h1>, any other unit's first structural
# heading (<h3> mostly; title 49's subtitles are <h2>).  Its body begins at
# the next heading field — a section's, or the first unit's inside it.
_UNIT_HEAD_RE = re.compile(
    r"<!--\s*field-start:(titlehead|structuralhead)\s*-->\s*"
    r"<h[1-3]\b[^>]*>(.*?)</h[1-3]>",
    re.DOTALL,
)
_UNIT_BODY_RE = re.compile(
    r"<!--\s*field-start:(?:head|structuralhead)\s*-->")
# The table's pieces: subheads; column labels ("Sec.", "Chap.") naming what
# a table lists; and its rows — designation, heading and, where given, first
# section — as <div> cells, or in some chapters (10 U.S.C. ch. 47) a <table>.
_UNIT_TOKEN_RE = re.compile(
    r'<h[3-5] class="(?:analysis-subhead|note-head)"[^>]*>'
    r"(?P<group>.*?)</h[3-5]>"
    r'|class="analysis-head-left">(?P<label>.*?)</div>'
    r"|<tr>\s*<th\b[^>]*>(?P<thlabel>.*?)</th>(?P<threst>.*?)</tr>"
    r'|<div class="(?:two|three)-column-analysis-style-content-left">'
    r"(?P<left>.*?)</div>"
    r'<div class="(?:two|three)-column-analysis-style-content-'
    r'(?:right|center)"[^>]*>(?P<middle>.*?)</div>'
    r'(?:<div class="three-column-analysis-style-content-right"[^>]*>'
    r"(?P<right>.*?)</div>)?"
    r'|<tr>\s*<td class="left">(?P<tleft>.*?)</td>\s*'
    r'<td class="middle">(?P<tmiddle>.*?)</td>'
    r'(?:\s*<td class="(?:middle|right)">(?P<tright>.*?)</td>)?',
    re.DOTALL,
)
# A subhead that names a unit: "SUBCHAPTER I–A—INSTITUTIONALIZED PERSONS",
# "Part A—Administration", "subpart i—general".  Others ("Editorial Notes",
# "Amendments") head notes, not lists.
_UNIT_DESIGNATION_RE = re.compile(
    r"^(title|subtitle|division|subdivision|part|subpart|chapter|"
    r"subchapter|article)\s+([A-Za-z0-9]+(?:[–-][A-Za-z0-9]+)?)\s*(?:—|$)",
    re.IGNORECASE,
)
_TABLE_KINDS = (
    ("chap", "chapter"), ("subchap", "subchapter"), ("subtitle", "subtitle"),
    ("subpart", "subpart"), ("part", "part"), ("subdivision", "subdivision"),
    ("division", "division"), ("article", "article"), ("sec", "section"),
)
_PLURALS = {"chapter": "Chapters", "subchapter": "Subchapters",
            "subtitle": "Subtitles", "subpart": "Subparts", "part": "Parts",
            "subdivision": "Subdivisions", "division": "Divisions",
            "article": "Articles", "section": "Sections"}


def _cell(fragment: str) -> str:
    """A table cell's text, less its footnote markers."""
    fragment = re.sub(r"<sup\b.*?</sup>", "", fragment or "",
                      flags=re.DOTALL)
    return _clean(fragment)


def _fetch_unit_top(url: str, cap: int = 8_000_000) -> str:
    """A unit page as far as its table of contents: streamed, and cut off
    where the unit's body begins."""
    import codecs

    import requests

    resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=30,
                        stream=True)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    text, read, head_end = "", 0, -1
    try:
        resp.raise_for_status()
        for chunk in resp.iter_content(65536):
            read += len(chunk)
            # Search only what is new, less a margin for a marker split
            # between chunks.
            start = max(0, len(text) - 200)
            text += decoder.decode(chunk)
            if head_end < 0:
                head = _UNIT_HEAD_RE.search(text)
                if head:
                    head_end = start = head.end()
            if head_end >= 0 and _UNIT_BODY_RE.search(
                    text, max(start, head_end)):
                break
            if read > cap:
                break
    finally:
        resp.close()
    return text


def _is_roman_list(designations: list[str]) -> bool:
    """Whether a table's designations are roman numerals ("I", "II", "IV")
    rather than letters ("A", "B", "C" — C and D are numerals too)."""
    marks = [d for d in designations if d]
    return bool(marks) and all(
        re.fullmatch(r"[IVXLCDM]+|[ivxlcdm]+", d) for d in marks
    ) and (any(len(d) > 1 for d in marks) or marks == ["I"]
           or marks == ["i"])


def _child_granule(parent: str, kind: str, designation: str,
                   roman: bool) -> str:
    """The page id the OLRC gives a unit listed in *parent*'s table.

    A chapter's id hangs off the title whatever holds it ("title18-
    chapter44", in part I), and so does a subtitle's, and a part's directly
    under a title; everything else is nested under its parent.  Roman
    numerals become digits ("SUBCHAPTER XVIII" → subchapter18)."""
    title = parent.split("-", 1)[0]
    mark = designation
    if roman:
        mark = str(_roman_to_int(designation))
    if kind in ("chapter", "subtitle") or (kind == "part" and parent == title):
        return f"{title}-{kind}{mark}"
    return f"{parent}-{kind}{mark}"


def _unit_entries(page: str, granule: str) -> list[UnitEntry]:
    """The entries of the table of contents between a unit page's heading
    and its body."""
    head = _UNIT_HEAD_RE.search(page)
    if head is None:
        return []
    body = _UNIT_BODY_RE.search(page, head.end())
    region = page[head.end():body.start() if body else len(page)]
    tokens: list[tuple] = []
    for m in _UNIT_TOKEN_RE.finditer(region):
        if m.group("group") is not None:
            tokens.append(("group", _cell(m.group("group"))))
        elif (m.group("label") or m.group("thlabel")) is not None:
            label = _cell(m.group("label") or m.group("thlabel")).lower()
            # a table of something else (a note's conversion table)
            if any(label.startswith(prefix) for prefix, _k in _TABLE_KINDS):
                # The UCMJ numbers its sections as articles too, in a
                # column of their own: Sec. | Art. | heading.
                articles = bool(re.search(
                    r">\s*Art\.", m.group("threst") or ""))
                tokens.append(("label", label, articles))
        elif m.group("left") is not None:
            tokens.append(("row", _cell(m.group("left")),
                           _cell(m.group("middle")),
                           _cell(m.group("right") or "")))
        else:
            tokens.append(("row", _cell(m.group("tleft")),
                           _cell(m.group("tmiddle")),
                           _cell(m.group("tright") or "")))

    # Which rows are numerals, a table (label to label) at a time.
    roman_blocks: dict[int, bool] = {}
    block, marks = 0, []
    for i, token in enumerate(tokens):
        if token[0] == "label":
            if marks:
                roman_blocks[block] = _is_roman_list(marks)
            block, marks = i, []
        elif token[0] == "row":
            marks.append(token[1].strip("[]. ").split(" ")[0])
    if marks:
        roman_blocks[block] = _is_roman_list(marks)

    entries: list[UnitEntry] = []
    group_kinds: list[str] = []   # subhead kinds, outermost first
    group_depth = -1
    row_kind, block, articles = "section", 0, False
    tables: list[tuple[int, str]] = []   # (entry index, kind) per table
    for i, token in enumerate(tokens):
        if token[0] == "label":
            row_kind = next((kind for prefix, kind in _TABLE_KINDS
                             if token[1].startswith(prefix)), "section")
            articles = token[2]
            block = i
            tables.append((len(entries), row_kind))
            continue
        if token[0] == "group":
            m = _UNIT_DESIGNATION_RE.match(token[1])
            if m is None:
                continue
            kind = m.group(1).lower()
            if kind not in group_kinds:
                group_kinds.append(kind)
            group_depth = group_kinds.index(kind)
            entries.append(UnitEntry("group", group_depth, token[1]))
            continue
        _row, left, middle, right = token
        mark = left.strip("[]").rstrip(".").strip()
        heading = middle.rstrip("]").strip()
        depth = group_depth + 1
        if row_kind == "section":
            first = re.split(r"\s+to\s+|,", mark)[0].strip()
            label = f"§ {mark}"
            if articles and right:
                label += f" (Art. {middle.rstrip('.').strip()})"
                heading = right.rstrip("]").strip()
            entries.append(UnitEntry(
                "section", depth, label, heading,
                section=first.replace("–", "-"),
            ))
        else:
            designation = mark.split(" ")[0]
            entries.append(UnitEntry(
                "unit", depth,
                f"{crumb_label(row_kind)} {designation}", heading,
                unit_kind=row_kind, designation=designation,
                granule=_child_granule(
                    granule, row_kind, designation,
                    roman_blocks.get(block, False)),
                first_section=(right.split() or [""])[-1],
            ))
    # A title can list its units twice over, subtitles and then every
    # chapter (title 26): with no subheads to tell the lists apart, each is
    # headed with what it lists.
    kinds = {kind for start, kind in tables
             if any(e.kind != "group" for e in entries[start:])}
    if len(kinds) > 1 and not any(e.kind == "group" for e in entries):
        ends = [start for start, _kind in tables[1:]] + [len(entries)]
        for (start, kind), end in reversed(list(zip(tables, ends))):
            for entry in entries[start:end]:
                entry.depth += 1
            entries.insert(start, UnitEntry(
                "group", 0, _PLURALS.get(kind, kind.title() + "s")))
    # A subhead is kept only where something is listed under it.
    kept: list[UnitEntry] = []
    for i, entry in enumerate(entries):
        if entry.kind == "group" and not any(
                e.kind != "group" and e.depth > entry.depth
                for e in entries[i + 1:
                                 next((j for j in range(i + 1, len(entries))
                                       if entries[j].kind == "group"
                                       and entries[j].depth <= entry.depth),
                                      len(entries))]):
            continue
        kept.append(entry)
    return kept


def _same_designation(text: str, label: str) -> bool:
    """Whether a subhead ("SUBCHAPTER I—GENERALLY") heads the unit a
    navigation bar calls *label* ("SUBCHAPTER I") — and not "SUBCHAPTER
    I–A"."""
    norm = lambda s: re.sub(r"\s+", " ", s or "").strip().casefold()
    text, label = norm(text), norm(label)
    return bool(label) and (text == label or text.startswith(label + "—")
                            or text.startswith(label + " —"))


def _group_in(entries: list[UnitEntry], label: str) -> list[UnitEntry]:
    """The entries under the subhead naming *label*, one level out."""
    for i, entry in enumerate(entries):
        if entry.kind == "group" and _same_designation(entry.label, label):
            out: list[UnitEntry] = []
            for later in entries[i + 1:]:
                if later.kind == "group" and later.depth <= entry.depth:
                    break
                out.append(UnitEntry(**{
                    **later.__dict__, "depth": later.depth - entry.depth - 1,
                }))
            return out
    return []


_BODY_HEAD_RE = re.compile(
    r"<!--\s*field-start:structuralhead\s*-->\s*"
    r'<h[1-3] class="([a-z-]+)"[^>]*>(.*?)</h[1-3]>'
    r'|<h3 class="section-head"[^>]*>(.*?)</h3>',
    re.DOTALL,
)
_SECTION_HEAD_TEXT_RE = re.compile(
    r"^\[?\s*§+\s*([0-9][\w\-–]*?)\.?\s+(.*?)\]?$")


def _entries_from_body(url: str,
                       cap: int = 6_000_000) -> tuple[list[UnitEntry], bool]:
    """A unit's contents read off its own text — the headings of the units
    and sections in it — for a unit no table lists.  Reads at most *cap*
    bytes; the flag says whether it stopped short."""
    import codecs

    import requests

    resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=30,
                        stream=True)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    text, read, partial = "", 0, False
    try:
        resp.raise_for_status()
        for chunk in resp.iter_content(65536):
            read += len(chunk)
            text += decoder.decode(chunk)
            if read > cap:
                partial = True
                break
    finally:
        resp.close()
    head = _UNIT_HEAD_RE.search(text)
    entries: list[UnitEntry] = []
    kinds: list[str] = []
    depth = -1
    for m in _BODY_HEAD_RE.finditer(text, head.end() if head else 0):
        if m.group(1):
            kind = m.group(1)
            if kind not in kinds:
                kinds.append(kind)
            depth = kinds.index(kind)
            entries.append(UnitEntry("group", depth, _clean(m.group(2))))
            continue
        sec = _SECTION_HEAD_TEXT_RE.match(_clean(m.group(3)))
        if sec:
            number = sec.group(1).replace("–", "-")
            entries.append(UnitEntry(
                "section", depth + 1, f"§ {number}", sec.group(2),
                section=number))
    return entries, partial


_unit_cache: dict[str, UscUnit] = {}


def load_unit(granule: str) -> UscUnit:
    """A unit of the Code with its table of contents, cached.  A unit with
    no table of its own is given the part of its parent's that lists it.
    Raises UnitNotFound when the OLRC has no page by that id."""
    granule = str(granule).strip()
    with _cache_lock:
        if granule in _unit_cache:
            return _unit_cache[granule]
    url = unit_url(granule)
    try:
        page = _fetch_unit_top(url)
    except Exception as exc:
        raise RuntimeError(f"uscode.house.gov: {exc}") from exc
    head = _UNIT_HEAD_RE.search(page)
    if head is None:
        raise UnitNotFound(f"uscode.house.gov has no page {granule}")
    crumbs, own = page_crumbs(page)
    heading = _clean(head.group(2))
    if not own:
        own = heading.split("—", 1)[0].strip()
    entries = _unit_entries(page, granule)
    partial = False
    if not any(e.kind != "group" for e in entries) and crumbs:
        try:
            parent = load_unit(crumbs[-1][0])
            entries = _group_in(parent.entries, own) or entries
        except Exception as exc:
            print(f"[usc-unit] {granule}: parent's contents failed: {exc}")
    if not any(e.kind != "group" for e in entries):
        # Listed nowhere (52 U.S.C. subtitle III): read its own headings.
        try:
            entries, partial = _entries_from_body(url)
        except Exception as exc:
            print(f"[usc-unit] {granule}: reading its text failed: {exc}")
    unit = UscUnit(
        title=granule.split("-", 1)[0].replace("title", "", 1),
        granule=granule, url=url, label=crumb_label(own),
        heading=heading, crumbs=crumbs, entries=entries, partial=partial,
    )
    print(f"[usc-unit] {granule}: {len(entries)} entries")
    with _cache_lock:
        _unit_cache[granule] = unit
    return unit


def open_unit_entry(entry: UnitEntry, parent: UscUnit) -> UscUnit:
    """The unit a table entry names.  Its page id is the OLRC's usual one
    for its designation; failing that, the designation as printed
    ("subpartII"); failing that, the one the navigation bar of its first
    section gives it."""
    tried: list[str] = []
    for granule in (entry.granule,
                    f"{entry.granule.rsplit(entry.unit_kind, 1)[0]}"
                    f"{entry.unit_kind}{entry.designation}"):
        if not granule or granule in tried:
            continue
        tried.append(granule)
        try:
            return load_unit(granule)
        except UnitNotFound:
            continue
    if entry.first_section:
        section = load_section(parent.title, entry.first_section)
        for granule, label in section.crumbs:
            if (granule not in tried and crumb_label(label).casefold()
                    == entry.label.casefold()):
                return load_unit(granule)
    raise UnitNotFound(f"uscode.house.gov has no page for {entry.label}")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# The page's own indentation.  The OLRC stylesheet (usc.css) gives every
# paragraph class its left margin in ems — statutory-body-2em sits 2em in,
# statutory-body-block-2em is a block at 2em — and each subdivision heading
# the margin of its level: subsection-head 0, paragraph-head 1em, on to
# subsubclause-head at 5em.  That margin is the depth shown here.  A hanging
# paragraph, flushX_hangY, starts its first line at X em, which is where the
# reader's own hanging indent puts it at depth X.  The stylesheet omits a few
# rules the pages rely on (block-2em, 6em; the page puts the first in an
# inline style), so the number in the class name is read rather than the CSS.
#
# The page follows the printed Code's layout, and so does the reader: in an
# older section a subsection's paragraphs sit flush with it — "(a)", "(1)",
# "(2)", "(b)" at one margin, only "(A)" stepping in — and a paragraph that
# opens "(d)(1)" is followed by a flush "(2)".  statute_paths() reads the
# subdivision each paragraph belongs to from the same indentation.
_HEAD_DEPTH = {
    "subsection-head": 0, "paragraph-head": 1, "subparagraph-head": 2,
    "clause-head": 3, "subclause-head": 4, "subsubclause-head": 5,
}
_BODY_CLASS_PREFIXES = ("statutory-body", "note-body", "tableftnt", "usc28a")
_MAX_DEPTH = 6

_NOTE_HEAD_CLASSES = {"note-head", "note-sub-head", "analysis-subhead"}


def _is_body_class(cls: str) -> bool:
    return cls.lower().startswith(_BODY_CLASS_PREFIXES)


def _class_depth(cls: str) -> int:
    """How far in the page sets a paragraph of this class — see above."""
    if cls in _HEAD_DEPTH:
        return _HEAD_DEPTH[cls]
    m = re.search(r"flush(\d+)_hang\d+", cls)
    if m is None:
        m = re.search(r"-(\d+)em\b", cls)
    if m is not None:
        return min(int(m.group(1)), _MAX_DEPTH)
    if cls.lower().startswith("usc28a"):   # the title 28 appendix's forms
        return 4
    return 0

# Fields whose content is shown as statute vs. notes; anything else inside
# an unrecognized field is treated as a note so quoted statutory text in
# amendment notes never masquerades as current law.
_TOKEN_RE = re.compile(
    r"<!--\s*field-(start|end):([\w-]+)\s*-->"
    r"|<(h\d|p)\b[^>]*?class=\"([^\"]+)\"[^>]*>(.*?)</\3>",
    re.IGNORECASE | re.DOTALL,
)


def _clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_section(page_html: str) -> list[tuple[str, int, str]]:
    """Parse an OLRC section page into a (kind, indent, text) stream, each
    paragraph at the indentation the page gives it (see _class_depth)."""
    paras: list[tuple[str, int, str]] = []
    fields: list[str] = []  # stack of open field names
    for m in _TOKEN_RE.finditer(page_html):
        if m.group(1):  # field comment
            name = m.group(2).lower()
            if m.group(1) == "start":
                fields.append(name)
            else:
                while fields and fields.pop() != name:
                    pass
            continue
        cls = m.group(4).split()[0]
        text = _clean(m.group(5))
        if not text:
            continue
        ctx = fields[-1] if fields else None
        if ctx is None:
            # No field markers (format drift): classify by class name
            is_note = cls.startswith("note") or cls == "analysis-subhead"
        else:
            is_note = ctx not in ("statute", "head", "sourcecredit",
                                  "repealedhead", "omittedhead")
        if cls == "section-head" or (
            cls.endswith("-head")
            and ctx in ("head", "repealedhead", "omittedhead")
        ):
            paras.append(("sechead", 0, text))
        elif cls == "source-credit":
            paras.append(("credit", 0, text))
        elif not is_note and cls in _HEAD_DEPTH:
            paras.append(("head", _class_depth(cls), text))
        elif not is_note and _is_body_class(cls):
            paras.append(("body", _class_depth(cls), text))
        elif is_note and (cls in _NOTE_HEAD_CLASSES
                          or cls.endswith("-head")):
            paras.append(("note-head", 0, text))
        elif is_note and _is_body_class(cls):
            # note text, or statute-classed text quoted inside a note
            paras.append(("note-body", _class_depth(cls), text))
    return paras


# ---------------------------------------------------------------------------
# Which subdivision each paragraph belongs to
# ---------------------------------------------------------------------------

# A paragraph's leading enumerators: "(a)", "(4A)", "(ii)(I)", "(aa)".
_PATH_LEAD_RE = re.compile(
    r"^((?:\((?:\d{1,3}[A-Za-z]{0,2}|[a-zA-Z]{1,5})\)\s*)+)"
)
# The U.S. Code's levels, subsection to subitem.
_LEVEL_KINDS = ("a", "1", "A", "i", "I", "aa", "AA")


def _enum_kinds(enum: str) -> list[tuple[str, float]]:
    """Every level an enumerator can stand for, with its ordinal there:
    "(i)" is the ninth letter and the first roman numeral, "(4A)" falls
    between "(4)" and "(5)"."""
    out: list[tuple[str, float]] = []
    if enum.isdigit():
        out.append(("1", float(enum)))
    m = re.fullmatch(r"(\d+)([A-Za-z]{1,2})", enum)
    if m:
        out.append(("1", int(m.group(1))
                    + (ord(m.group(2)[0].upper()) - 64) / 100))
    if re.fullmatch(r"[a-z]", enum):
        out.append(("a", ord(enum) - 96))
    if re.fullmatch(r"[A-Z]", enum):
        out.append(("A", ord(enum) - 64))
    if re.fullmatch(r"[ivxlcdm]+", enum):
        out.append(("i", _roman_to_int(enum)))
    if re.fullmatch(r"[IVXLCDM]+", enum):
        out.append(("I", _roman_to_int(enum)))
    if re.fullmatch(r"([a-z])\1+", enum):
        out.append(("aa", ord(enum[0]) - 96 + 26 * (len(enum) - 2)))
    if re.fullmatch(r"([A-Z])\1+", enum):
        out.append(("AA", ord(enum[0]) - 64 + 26 * (len(enum) - 2)))
    return out


def _later_sibling(prev: str, enum: str) -> bool:
    """*enum* can come after *prev* at the same level: "(2)" after "(1)",
    "(i)" after "(h)", "(5)" after "(4A)"."""
    before = dict(_enum_kinds(prev))
    return any(kind in before and value > before[kind]
               for kind, value in _enum_kinds(enum))


def _next_sibling(prev: str, enum: str) -> bool:
    """*enum* comes straight after *prev*: "(C)" after "(B)"."""
    before = dict(_enum_kinds(prev))
    return any(kind in before and value == before[kind] + 1
               for kind, value in _enum_kinds(enum))


def _first_child(prev: str, enum: str) -> bool:
    """*enum* opens the level below *prev*'s: "(1)" under "(a)", "(A)"
    under "(4A)", "(I)" under "(ii)", "(AA)" under "(bb)"."""
    parents = {kind for kind, _value in _enum_kinds(prev)}
    return any(
        value == 1 and kind in _LEVEL_KINDS and any(
            p in _LEVEL_KINDS
            and _LEVEL_KINDS.index(kind) == _LEVEL_KINDS.index(p) + 1
            for p in parents
        )
        for kind, value in _enum_kinds(enum)
    )


def statute_paths(
    paras: list[tuple[str, int, str]],
) -> list[tuple[str, ...]]:
    """The subdivision each paragraph of a section belongs to — ("b", "3",
    "B") for § 36B(b)(3)(B) — read from the page's own indentation; () for
    the section's own text and for everything outside the statute (its
    heading, the source credit, the notes).

    A paragraph set further in opens a level below the one it follows;
    one at the same margin is a sibling.  Two readings of the enumerators
    settle what the margin alone cannot, both between paragraphs the page
    sets at one margin: a later sibling of an open item takes its place —
    so after "(d)(1)", a flush "(2)" replaces "(1)", not "(d)" — and the
    first of the next level down nests under it, as an older section's
    "(1)" does under "(a)".  Text between the items (flush language after
    a list) belongs to the level at its margin; it sets deeper levels
    aside rather than closing them, since the list can resume beneath it
    ("(I)" under a clause "(ii)" whose own text came first).  A heading
    styled a level deeper than its siblings ("(C)" after body-styled "(A)",
    "(B)") is still their sibling.

    The pages also carry an anchor for every enumerated paragraph, but it
    goes wrong on some pages (after an item "(a)" in 8 U.S.C. § 1182, every
    later anchor is off) where this reading does not."""
    stack: list[tuple[int, str]] = []      # (margin, enumerator) per level
    set_aside: list[tuple[int, str]] = []  # levels flush text interrupted
    out: list[tuple[str, ...]] = []
    for kind, depth, text in paras:
        if kind not in ("body", "head"):
            out.append(())
            continue
        m = _PATH_LEAD_RE.match(text)
        enums = re.findall(r"\(([^)]+)\)", m.group(1)) if m else []
        if not enums:
            while stack and stack[-1][0] > depth:
                set_aside.insert(0, stack.pop())
            out.append(tuple(enum for _d, enum in stack))
            continue
        lead = enums[0]
        first = next((i for i, (d, _e) in enumerate(stack) if d >= depth),
                     len(stack))
        same = [i for i in range(first, len(stack))
                if stack[i][0] == depth]
        cut = next((i for i in reversed(same)
                    if _later_sibling(stack[i][1], lead)), None)
        if cut is None:
            for j in range(len(set_aside) - 1, -1, -1):
                if (set_aside[j][0] == depth
                        and _first_child(set_aside[j][1], lead)):
                    stack.extend(set_aside[:j + 1])
                    cut = len(stack)
                    break
        if cut is None and same and _first_child(stack[same[-1]][1], lead):
            cut = same[-1] + 1
        if (cut is None and not same and stack and stack[-1][0] < depth
                and _next_sibling(stack[-1][1], lead)):
            cut = len(stack) - 1
        if cut is None:
            cut = first
        set_aside = []
        del stack[cut:]
        stack.extend((depth, enum) for enum in enums)
        out.append(tuple(enum for _d, enum in stack))
    return out


if __name__ == "__main__":
    failed = 0

    def check(cond: bool, what: str) -> None:
        global failed
        failed += not cond
        print(("ok   " if cond else "FAIL ") + what)

    # --- citation regex ---
    cases = [
        ("42 U.S.C. § 1983.", "42:1983:"),
        ("28 U. S. C. §2254(d)(1)", "28:2254:d,1"),
        ("18 U.S.C. §§ 922(g)(1)", "18:922:g,1"),
        ("15 U.S.C.A. § 78j(b)", "15:78j:b"),
        ("42 U.S.C. § 2000e-2(a)", "42:2000e-2:a"),
        ("see 5 U.S.C. 552", "5:552:"),
        ("15 U.S.C. §§ 78a–78pp", "15:78a-78pp:"),
        ("42 U.S.C. § 1983 (1982)", "42:1983:"),
        ("42 USC § 1983", "42:1983:"),
        ("see 5 USC 552(b)(6)", "5:552:b,6"),
        ("42 U.S.C.S. § 1983", "42:1983:"),
        ("42 U.S. Code § 1983", "42:1983:"),
        ("42 U.S.C. Section 1985(3)", "42:1985:3"),
    ]
    for text, want in cases:
        m = USC_CITE_RE.search(text)
        got = cite_spec(m) if m else None
        check(got == want, f"{text!r} -> {got!r}")
    for text in ("501 U.S. 32", "1988 U.S.C.C.A.N. 5982",
                 "U.S. Const. art. I", "120 U.S. 678", "the 12 USCIS 400",
                 "12 usc 400"):
        check(USC_CITE_RE.search(text) is None, f"no match in {text!r}")
    check(spec_label("42:1983:") == "42 U.S.C. § 1983", "label plain")
    check(spec_label("18:922:g,1") == "18 U.S.C. § 922(g)(1)", "label subsec")

    # --- parser, against authentic OLRC markup ---
    sample = """
<html><body><div class="uscnav">junk <p class="navhead">nav</p></div>
<!-- field-start:head -->
<h3 class="section-head">&sect;110. Same; income tax</h3>
<!-- field-end:head -->
<!-- field-start:statute -->
<p class="statutory-body">(a) No State, or political subdivision thereof,
 may, for purposes of any income tax levied by such State&mdash;</p>
<p class="statutory-body-1em">(1) treat such Member as a <em>resident</em>
 or domiciliary of such State; or</p>
<p class="statutory-body-1em">(2) treat any compensation paid by the
 United States to such Member as income for services performed within
 such State,</p>
<p class="statutory-body-2em">(A) a deeper clause;</p>
<p class="statutory-body">(b) For purposes of subsection (a)&mdash;</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(Added Pub. L. 99&ndash;190, Dec. 19, 1985,
 99 Stat. 1185.)</p>
<!-- field-end:sourcecredit -->
<!-- field-start:notes -->
<h4 class="note-head">Editorial Notes</h4>
<h4 class="note-sub-head">Amendments</h4>
<p class="note-body">1985&mdash;Subsec. (a). Pub. L. 99&ndash;190 added
 text reading as follows:</p>
<p class="statutory-body">(x) quoted statute text inside a note</p>
<!-- field-end:notes -->
</body></html>"""
    paras = parse_section(sample)
    kinds = [(k, i) for k, i, _t in paras]
    check(paras[0] == ("sechead", 0, "§110. Same; income tax"),
          f"section head: {paras[0]!r}")
    check(("body", 0) in kinds and ("body", 1) in kinds
          and ("body", 2) in kinds, f"body indents: {kinds!r}")
    check(any(k == "credit" for k, _i in kinds), "source credit captured")
    check(kinds.count(("note-head", 0)) == 2, "note heads captured")
    check(paras[-1][0] == "note-body",
          f"quoted statute in note stays a note: {paras[-1]!r}")
    check(not any("nav" in t for _k, _i, t in paras), "nav junk dropped")
    body1 = next(t for k, i, t in paras if (k, i) == ("body", 1))
    check("resident" in body1 and "<em>" not in body1,
          "inline tags stripped")

    # The page's own layout: OLRC prints "(a)(1)" and the following "(2)"
    # both flush left, as the printed Code does, and the reader keeps it;
    # the subdivision each paragraph belongs to is still read correctly.
    quirk = """
<!-- field-start:statute -->
<p class="statutory-body">(a)(1) Combined opening paragraph.</p>
<p class="statutory-body">(2) Print-flush sibling of (1).</p>
<p class="statutory-body-1em">(A) A subparagraph.</p>
<p class="statutory-body-2em">(i) A clause.</p>
<p class="statutory-body-2em">(ii) Another clause.</p>
<p class="statutory-body">Flush text closing paragraph (2).</p>
<p class="statutory-body">(b) Next subsection.</p>
<p class="statutory-body">Continuation of subsection (b).</p>
<p class="statutory-body">(c) Then (h)-style:</p>
<p class="statutory-body">(h) Skip ahead.</p>
<p class="statutory-body">(i) Letter i, not roman.</p>
<!-- field-end:statute -->"""
    quirk_paras = parse_section(quirk)
    got_lvls = [(t.split()[0], i) for k, i, t in quirk_paras]
    want_lvls = [("(a)(1)", 0), ("(2)", 0), ("(A)", 1), ("(i)", 2),
                 ("(ii)", 2), ("Flush", 0), ("(b)", 0),
                 ("Continuation", 0), ("(c)", 0), ("(h)", 0),
                 ("(i)", 0)]
    check(got_lvls == want_lvls, f"page indentation kept: {got_lvls!r}")
    got_paths = statute_paths(quirk_paras)
    want_paths = [("a", "1"), ("a", "2"), ("a", "2", "A"),
                  ("a", "2", "A", "i"), ("a", "2", "A", "ii"), ("a", "2"),
                  ("b",), ("b",), ("c",), ("h",), ("i",)]
    check(got_paths == want_paths, f"subdivision paths: {got_paths!r}")

    # Headings sit at their level's margin; flush text after a list and
    # items (aa) keep the page's indent (26 U.S.C. § 36B(b)(3)).
    headed = """
<!-- field-start:statute -->
<h4 class="subsection-head">(b) Premium assistance credit amount</h4>
<h4 class="paragraph-head">(3) Other terms</h4>
<h4 class="subparagraph-head">(B) Applicable second lowest cost silver plan</h4>
<p class="statutory-body-2em">The applicable plan is the plan which-</p>
<p class="statutory-body-3em">(ii) provides-</p>
<p class="statutory-body-4em">(I) self-only coverage-</p>
<p class="statutory-body-5em">(aa) whose tax is determined under section 1(c), or</p>
<p class="statutory-body-4em">(II) family coverage.</p>
<p class="statutory-body-block-2em">If a taxpayer files a joint return, the plan is determined as follows.</p>
<h4 class="subparagraph-head">(C) Adjusted monthly premium</h4>
<!-- field-end:statute -->"""
    headed_paras = parse_section(headed)
    check([i for _k, i, _t in headed_paras] == [0, 1, 2, 2, 3, 4, 5, 4, 2, 2],
          f"heading and block depths: {headed_paras!r}")
    check(statute_paths(headed_paras) == [
        ("b",), ("b", "3"), ("b", "3", "B"), ("b", "3", "B"),
        ("b", "3", "B", "ii"), ("b", "3", "B", "ii", "I"),
        ("b", "3", "B", "ii", "I", "aa"), ("b", "3", "B", "ii", "II"),
        ("b", "3", "B"), ("b", "3", "C")],
        f"headed paths: {statute_paths(headed_paras)!r}")

    # Breadcrumb container extraction (real OLRC breadcrumb form: the
    # links are URL-encoded and carry jsessionid/saved parameters)
    crumbs = """
<a href="/view.xhtml;jsessionid=9600DA?req=granuleid%3AUSC-prelim-title26&amp;saved=%7CZ3%3D%7C&amp;edition=prelim" class="link_class">TITLE 26</a>
<a href="/view.xhtml;jsessionid=9600DA?req=granuleid%3AUSC-prelim-title26-subtitleD&amp;edition=prelim" class="link_class">Subtitle D</a>
<a href="/view.xhtml;jsessionid=9600DA?req=granuleid%3AUSC-prelim-title26-chapter48&amp;edition=prelim" class="link_class">CHAPTER 48</a>
"""
    check(_find_container(crumbs) == "title26-chapter48",
          f"container: {_find_container(crumbs)!r}")
    check(_find_container("<p>nothing</p>") is None, "no container -> None")

    # Table-of-sections parsing (verbatim OLRC analysis markup)
    analysis = """
<!-- field-start:analysis -->
<div class="analysis">
<div><div class="analysis-head-left">Sec.</div></div>
<div><div class="two-column-analysis-style-content-left">5000A.</div><div class="two-column-analysis-style-content-right">Requirement to maintain minimum essential coverage.</div></div>
<div><div class="two-column-analysis-style-content-left">5000B.</div><div class="two-column-analysis-style-content-right">Imposition of tax.</div></div>
<div><div class="two-column-analysis-style-content-left">5001 to 5011. Repealed.</div><div class="two-column-analysis-style-content-right"></div></div>
<div><div class="two-column-analysis-style-content-left">[5012.</div><div class="two-column-analysis-style-content-right">Omitted.]</div></div>
<div><div class="two-column-analysis-style-content-left">2000e&#8211;2.</div><div class="two-column-analysis-style-content-right">Hyphenated.</div></div>
</div>
<!-- field-end:analysis -->
<h3 class="section-head">§5000A. Not part of the analysis</h3>"""
    order = _sections_from_analysis(analysis)
    check(order == ["5000A", "5000B", "5001", "5012", "2000e-2"],
          f"analysis order: {order!r}")
    # Streamed buffer cut off mid closing comment still parses
    truncated = analysis.split("<!-- field-end:analysis")[0] \
        + "<!-- field-end:analysis"
    check(_sections_from_analysis(truncated)
          == ["5000A", "5000B", "5001", "5012", "2000e-2"],
          "truncated buffer parses")
    # Fallback: no analysis cells -> order from section heads
    heads = """
<h3 class="section-head">§1981. Equal rights under the law</h3>
<p class="statutory-body">text</p>
<h3 class="section-head">§1981a. Damages in cases of intentional
 discrimination</h3>
<h3 class="section-head">[§1982. Repealed]</h3>
<h3 class="section-head">§1983. Civil action for deprivation of rights</h3>
"""
    fb = _sections_from_analysis(heads)
    check(fb == ["1981", "1981a", "1982", "1983"],
          f"section-head fallback: {fb!r}")
    doc = UscSection(title="26", section="5000A", url="u",
                     container="title26-chapter48")
    _order_cache["title26-chapter48"] = order
    check(doc.neighbors() == (None, ("26", "5000B")),
          f"neighbors: {doc.neighbors()!r}")
    doc2 = UscSection(title="26", section="5000B", url="u",
                      container="title26-chapter48")
    check(doc2.neighbors() == (("26", "5000A"), ("26", "5001")),
          f"neighbors mid: {doc2.neighbors()!r}")
    _order_cache.clear()

    # Fallback: same page without field comments still classifies by class
    stripped = re.sub(r"<!--.*?-->", "", sample, flags=re.DOTALL)
    paras2 = parse_section(stripped)
    kinds2 = [(k, i) for k, i, _t in paras2]
    check(("body", 0) in kinds2 and ("body", 2) in kinds2,
          f"fallback bodies: {kinds2!r}")
    check(("note-head", 0) in kinds2 and ("note-body", 0) in kinds2,
          "fallback notes")
    check(paras2[0][0] == "sechead", "fallback section head")

    raise SystemExit(1 if failed else 0)
