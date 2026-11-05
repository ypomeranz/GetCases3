"""Fetch and parse U.S. Code sections from the Office of the Law Revision
Counsel (uscode.house.gov), the House's official online edition.

The OLRC serves one section per page at

    https://uscode.house.gov/us/usc/t{T}/s{S}/

Pages are machine-generated with a stable structure: the section's text in
a ``<div class="statutory-body-container">``, then its source credit, then
each note in a ``<div class="note-block">``, and every paragraph carries a
CSS class encoding its role and indentation depth (``statutory-body``,
``statutory-body-1em``, …, ``note-body``).  ``parse_section()`` walks those
markers; the GUI renders the resulting (kind, indent, text) stream with
bolding and indentation.

When the OLRC's site is down, a section is read from Cornell's Legal
Information Institute instead (see "Cornell's copy" below).
"""

from __future__ import annotations

import html as _html
import re
import threading
import time
from concurrent.futures import (FIRST_COMPLETED, Future,
                                TimeoutError as FutureTimeout, wait)
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin

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


OLRC_BASE = "https://uscode.house.gov/"


def section_url(title: str, section: str) -> str:
    return f"{OLRC_BASE}us/usc/t{title}/s{section}/"


# Until 2026 the OLRC served sections through a viewer, and that address is
# still what bookmarks made then are filed under, and hold as the section's
# source.  The viewer's pages are gone.
_VIEWER_URL = ("https://uscode.house.gov/view.xhtml?req=granuleid:"
               "USC-prelim-title{}-section{}&num=0&edition=prelim")
_VIEWER_URL_RE = re.compile(
    r"/view\.xhtml\?req=granuleid:USC-prelim-title(\w+)-section([^&]+)")


def live_url(url: str) -> str:
    """*url*, or the section's address today if it is the viewer's."""
    m = _VIEWER_URL_RE.search(url)
    return section_url(m.group(1), m.group(2)) if m else url


@dataclass
class UscSection:
    title: str
    section: str
    url: str
    # (kind, indent, text); kind in {"sechead", "head", "body", "credit",
    # "note-head", "note-body"}
    paras: list[tuple[str, int, str]] = field(default_factory=list)
    # the units above the section, outermost first: ("title42", "Title 42"),
    # ("title42-chapter21", "Chapter 21") … — see load_unit
    crumbs: list[tuple[str, str]] = field(default_factory=list)
    # where the text was read: "olrc", or "lii" for Cornell's copy, read
    # while the OLRC's site was down
    source: str = "olrc"
    # Cornell's copy taken because the OLRC was slow to answer, not down
    olrc_slow: bool = False
    # the sections before and after it, as its page names them
    adjacent: tuple = (None, None)

    @property
    def kind(self) -> str:
        return "usc"

    @property
    def key_url(self) -> str:
        """What a bookmark of the section is filed under, wherever its text
        was read: the viewer's address, so that a section bookmarked in the
        viewer's day is still the same bookmark."""
        return _VIEWER_URL.format(self.title, self.section)

    def neighbors(self) -> tuple[tuple[str, str] | None,
                                 tuple[str, str] | None]:
        """Adjacent sections, as the section's own page names them."""
        return self.adjacent

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
        if self.source == "lii":
            return "U.S. Code (Cornell LII)"
        return "U.S. Code (OLRC)"

    @property
    def source_note(self) -> str:
        if self.source == "lii":
            why = ("was slow to answer" if self.olrc_slow
                   else "was not answering")
            return f"Cornell LII's copy of the OLRC text (uscode.house.gov {why})"
        return "OLRC online edition (current law)"

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


def _candidates(section: str) -> list[str]:
    """The section asked for, then — for a range or hyphenated section the
    site does not know ("78a-78pp") — the part before the dash."""
    return [section] + ([section.split("-", 1)[0]] if "-" in section else [])


class _OlrcUnavailable(RuntimeError):
    """The OLRC did not answer with a page of its own."""


def _olrc_page(page: str) -> bool:
    """Whether a page is the OLRC's own, as against the house.gov "Under
    Maintenance" notice or an error page put up in front of the site.  Every
    page of the OLRC's, one saying it has no such section included, sets its
    content in this frame."""
    return "page_content_internal" in page


# After the OLRC fails and Cornell answers, Cornell is asked first for this
# long: a site that is down tends to stay down a while, and one that hangs
# would cost every section its full timeout before Cornell was tried.
_OLRC_REST = 600.0
_olrc_rest_until = 0.0


def load_section(title: str, section: str) -> UscSection:
    """Fetch and parse a section, with an in-memory cache.  For a range or
    hyphenated section that the OLRC does not know ("78a-78pp"), falls back
    to the part before the dash.  While the OLRC's site is down, reads
    Cornell's copy instead.  Raises SectionNotFound when the Code has no
    such section, RuntimeError with a readable message when neither site
    answers."""
    global _olrc_rest_until
    title, section = str(title).strip(), str(section).strip()
    key = (title, section)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    resting = time.monotonic() < _olrc_rest_until
    if resting:
        down = "was not answering a few minutes ago"
    else:
        try:
            doc = _olrc_or_quicker(title, section)
        except _OlrcUnavailable as exc:
            down = str(exc)
        else:
            down = ""
    if down:
        try:
            doc = _lii_section(title, section)
        except SectionNotFound:
            raise
        except Exception as exc:
            raise RuntimeError(f"uscode.house.gov {down}; law.cornell.edu: "
                               f"{exc}") from exc
        if not resting:
            _olrc_rest_until = time.monotonic() + _OLRC_REST
            print(f"[usc] uscode.house.gov {down}; reading Cornell's copy "
                  f"for the next {int(_OLRC_REST // 60)} minutes")
    with _cache_lock:
        _cache[key] = doc
    return doc


#: How long the OLRC is given before Cornell's copy is asked for as well.
_OLRC_HEDGE_S = 1.5


def _in_thread(fetch) -> Future:
    """*fetch* set going on a thread of its own, its outcome a Future."""
    future: Future = Future()

    def run() -> None:
        try:
            future.set_result(fetch())
        except BaseException as exc:
            future.set_exception(exc)

    threading.Thread(target=run, daemon=True).start()
    return future


def _olrc_or_quicker(title: str, section: str) -> UscSection:
    """The OLRC's copy of a section, as :func:`_olrc_section` — unless the
    OLRC is slow: past ``_OLRC_HEDGE_S`` Cornell's copy is asked for too, and
    the first to arrive is taken (the OLRC's whenever it is first).  The
    OLRC's word that there is no such section stands, Cornell's copy being
    able to trail it; its failing to answer is raised as before, and
    load_section falls back to Cornell."""
    olrc = _in_thread(lambda: _olrc_section(title, section))
    try:
        return olrc.result(timeout=_OLRC_HEDGE_S)
    except FutureTimeout:
        pass
    print(f"[usc] uscode.house.gov is slow; asking Cornell for "
          f"{title} U.S.C. § {section} as well")
    lii = _in_thread(lambda: _lii_section(title, section))
    wait([olrc, lii], return_when=FIRST_COMPLETED, timeout=65)
    if not olrc.done() and lii.done() and lii.exception() is None:
        doc = lii.result()
        doc.olrc_slow = True
        return doc
    # The OLRC's own answer, whatever it is, once Cornell has none to give.
    return olrc.result(timeout=65)


# A section with no page of its own — one printed in a range with the section
# before it, as 42 U.S.C. § 4 is in "§§ 3, 4" — has a stub that sends the
# reader on, which requests does not follow by itself.
_REFRESH_RE = re.compile(
    r'<meta http-equiv="refresh" content="[^"]*?url=([^"]+)"', re.IGNORECASE)
_SECTION_PATH_RE = re.compile(r"/t(\w+)/s([^/\"]+)/?$")
_ADJACENT_RE = re.compile(
    r'<a href="[^"]*/t(\w+)/s([^/"]+)/" class="(prev|next)-section"')
#: How long a section waits for the list of its title's contents, which only
#: places it in the Code: the section itself is worth more than its crumbs.
_CRUMBS_WAIT_S = 0.75


def _olrc_fetch(url: str) -> tuple[str, str] | None:
    """The page at *url* and the address it ended up at, stubs followed, or
    None when the OLRC has no such page."""
    import requests

    page = ""
    for _hop in range(3):
        resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        # Decode explicitly: a missing charset in the Content-Type would
        # otherwise turn "§" into "Â§" via requests' Latin-1 fallback
        page = resp.content.decode("utf-8", "replace")
        moved = _REFRESH_RE.search(page)
        if moved is None:
            break
        url = urljoin(url, moved.group(1))
    return page, url


def olrc_adjacent(page: str) -> tuple[tuple[str, str] | None,
                                      tuple[str, str] | None]:
    """The sections before and after a page's, as (title, section): its
    navigation bar names them, across the end of a title too."""
    found = {m.group(3): (m.group(1), m.group(2))
             for m in _ADJACENT_RE.finditer(page)}
    return found.get("prev"), found.get("next")


def _section_crumbs(title: str, url: str,
                    contents: Future) -> list[tuple[str, str]]:
    """The units above the section at *url*, or just its title when the
    title's contents aren't to hand in time."""
    m = _SECTION_PATH_RE.search(url)
    try:
        crumbs = contents.result(timeout=_CRUMBS_WAIT_S).crumbs(m.group(2))
    except Exception as exc:
        print(f"[usc-unit] title {title}: contents not to hand: {exc!r}")
        crumbs = None
    return crumbs or [(f"title{title}", f"Title {title}")]


def _olrc_section(title: str, section: str) -> UscSection:
    """A section from the OLRC.  Raises SectionNotFound when the OLRC says
    there is no such section, _OlrcUnavailable when it does not answer.

    A 404 alone is not its word on that.  When the OLRC rebuilt its site every
    address the app knew answered 404, and 42 U.S.C. § 1983 was reported not
    to exist; so the title's list of its sections has to agree, and if the
    list names the section, or there is no list, the site has changed under
    the app rather than the Code."""
    contents = _in_thread(lambda: _title_contents(title))
    last_err = "section not found"
    for cand in _candidates(section):
        try:
            fetched = _olrc_fetch(section_url(title, cand))
        except Exception as exc:
            raise _OlrcUnavailable(f"failed: {exc}") from exc
        if fetched is None:
            last_err = f"no such section {title} U.S.C. § {cand}"
            continue
        page, url = fetched
        paras = parse_section(page)
        if paras:
            return UscSection(title=title, section=cand, url=url, paras=paras,
                              crumbs=_section_crumbs(title, url, contents),
                              adjacent=olrc_adjacent(page))
        if not _olrc_page(page):
            raise _OlrcUnavailable("answered with a page that is not the "
                                   "Code's (down for maintenance?)")
        last_err = f"no text found for {title} U.S.C. § {cand}"
    try:
        listed = contents.result(timeout=35)
    except Exception as exc:
        raise _OlrcUnavailable(f"has no list of title {title}: {exc}") from exc
    if any(listed.crumbs(cand) for cand in _candidates(section)):
        raise _OlrcUnavailable(f"lists {title} U.S.C. § {section} but has no "
                               "page for it")
    raise SectionNotFound(f"uscode.house.gov: {last_err}")


# ---------------------------------------------------------------------------
# The Code above the section
#
# The OLRC's browse page opens each title from a file of its own,
#
#     https://uscode.house.gov/browse-tree/t{T}.html
#
# which sets out the whole title: every unit — subtitle, part, chapter,
# subchapter, subpart — as a <details> nested in the one that holds it, each
# with a link to the unit's front matter ("us/usc/t42/chapter21-subchapterI-
# front.html") and a link to each section directly in it.  So one fetch gives
# every table of contents in a title and where each section sits in it.  A
# unit's id is that link's name under its title: "title42-chapter21-
# subchapterI".
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
    granule: str = ""       # a unit entry's id: see load_unit
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
    # the units above this one, outermost first: ("title42", "Title 42")
    crumbs: list[tuple[str, str]] = field(default_factory=list)
    entries: list[UnitEntry] = field(default_factory=list)
    # the contents stop short — never so here, but the viewer asks it of
    # every source's units
    partial: bool = False
    # the site the contents were read from
    host: str = "uscode.house.gov"


class UnitNotFound(RuntimeError, LookupError):
    """The OLRC has no unit by that id."""


_UNIT_KINDS = frozenset((
    "title", "subtitle", "division", "subdivision", "part", "subpart",
    "chapter", "subchapter", "article",
))


def crumb_label(label: str) -> str:
    """A unit's designation as the reader shows it: "SUBCHAPTER I" →
    "Subchapter I", "part 5" → "Part 5", "TITLE 42" → "Title 42".  A unit
    with no designation is named as printed: "GENERAL PROVISIONS"."""
    words = (label or "").split(None, 1)
    if not words:
        return ""
    if words[0].lower() not in _UNIT_KINDS:
        return label
    head = words[0][:1].upper() + words[0][1:].lower()
    return f"{head} {words[1]}" if len(words) > 1 else head


@dataclass
class _TitleContents:
    """A title's units by id, and the unit holding each of its sections by
    the section's number in lower case."""

    units: dict[str, UscUnit]
    sections: dict[str, UscUnit]

    def crumbs(self, section: str) -> list[tuple[str, str]] | None:
        """The units above a section, outermost first; None when the title
        does not list it."""
        unit = self.sections.get(section.casefold())
        if unit is None:
            return None
        return unit.crumbs + [(unit.granule, unit.label)]


_TREE_TOKEN_RE = re.compile(
    r'<details class="tree-node">\s*<summary>(?P<summary>.*?)</summary>\s*'
    r'<ul class="tree-sections">\s*<li>\s*'
    r'<a href="us/usc/t\w+/(?P<slug>[^"]+)-front\.html"'
    r"|(?P<end></details>)"
    r'|<a class="section-link" href="us/usc/t\w+/s(?P<section>[^/"]+)/">'
    r"(?P<line>.*?)</a>",
    re.DOTALL,
)
_TREE_RANGE_RE = re.compile(
    r'<span class="section-range">\(sections?\s+([^\s)]+).*?</span>',
    re.DOTALL,
)


def _parse_tree(page: str, title: str) -> _TitleContents:
    """Read a title's browse tree: see "The Code above the section"."""
    root = UscUnit(title=title, granule=f"title{title}",
                   url=f"{OLRC_BASE}us/usc/t{title}/frontmatter/",
                   label=f"Title {title}", heading=f"TITLE {title}")
    units = {root.granule: root}
    sections: dict[str, UscUnit] = {}
    stack = [root]
    for m in _TREE_TOKEN_RE.finditer(page):
        holder = stack[-1]
        if m.group("end"):
            if len(stack) > 1:
                stack.pop()
        elif m.group("section"):
            # "§ 2000e–2 — Unlawful employment practices"
            label, _dash, heading = _tidy(_clean(m.group("line"))).partition(
                " — ")
            holder.entries.append(UnitEntry(
                kind="section", depth=0, label=label, heading=heading,
                section=m.group("section")))
            sections[m.group("section").casefold()] = holder
        else:
            summary = m.group("summary")
            found = _TREE_RANGE_RE.search(summary)
            first = found.group(1).replace("–", "-") if found else ""
            heading = _clean(_TREE_RANGE_RE.sub("", summary))
            # repealed units are bracketed: "[CHAPTER 5—REPEALED]"
            own, _dash, name = heading.strip("[] ").partition("—")
            kind, _space, designation = own.strip().partition(" ")
            if kind.lower() not in _UNIT_KINDS:
                # a topic with no designation of its own ("Penalties")
                own, name, kind, designation = heading, "", "", ""
            granule = f"title{title}-{m.group('slug')}"
            if granule in units:
                # Topics under one unit share a name ("chapter7-level"), and
                # a repealed chapter its successor's ("subtitleD-chapter38").
                granule += f"@{first}"
            unit = UscUnit(
                title=title, granule=granule,
                url=f"{OLRC_BASE}us/usc/t{title}/{m.group('slug')}-front.html",
                label=crumb_label(own.strip()), heading=heading,
                crumbs=holder.crumbs + [(holder.granule, holder.label)],
            )
            holder.entries.append(UnitEntry(
                kind="unit", depth=0, label=unit.label, heading=name.strip(),
                unit_kind=kind.lower(), designation=designation,
                granule=granule, first_section=first,
            ))
            units[granule] = unit
            stack.append(unit)
    return _TitleContents(units, sections)


_contents_cache: dict[str, _TitleContents] = {}


def _title_contents(title: str) -> _TitleContents:
    """A title's units and sections, cached.  Raises UnitNotFound when the
    OLRC has no such title, RuntimeError when it does not answer."""
    with _cache_lock:
        if title in _contents_cache:
            return _contents_cache[title]

    import requests

    resp = requests.get(f"{OLRC_BASE}browse-tree/t{title}.html",
                        headers=_BROWSER_HEADERS, timeout=30)
    if resp.status_code == 404:
        raise UnitNotFound(f"uscode.house.gov has no title {title}")
    resp.raise_for_status()
    contents = _parse_tree(resp.content.decode("utf-8", "replace"), title)
    if not contents.sections:
        raise RuntimeError("uscode.house.gov is not answering (down for "
                           "maintenance?)")
    print(f"[usc-unit] title {title}: {len(contents.units)} units, "
          f"{len(contents.sections)} sections")
    with _cache_lock:
        _contents_cache[title] = contents
    return contents


def _title_heading(title: str) -> str | None:
    """"TITLE 42—THE PUBLIC HEALTH AND WELFARE": only the site's list of the
    titles names them.  None when it can't be read."""
    import requests

    try:
        resp = requests.get(f"{OLRC_BASE}browse.html",
                            headers=_BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[usc-unit] title {title}: list of titles failed: {exc}")
        return None
    m = re.search(rf'data-lazy-title="{title}"><summary>.*? — (.*?)</summary>',
                  resp.content.decode("utf-8", "replace"))
    return f"TITLE {title}—{_clean(m.group(1))}" if m else None


def _digits_id(granule: str) -> str:
    """A unit's id with its roman numerals as digits."""
    return re.sub(r"(?<=[a-z])[IVXLC]+\b",
                  lambda m: str(_roman_to_int(m.group(0))), granule)


def _unit_by_viewer_id(contents: _TitleContents,
                       granule: str) -> UscUnit | None:
    """The unit a bookmark from the OLRC's viewer names in its breadcrumbs.
    The viewer's ids wrote roman numerals as digits and left out what holds
    a chapter ("title18-chapter44", in part I), so one reads as the end of
    an id today."""
    tail = _digits_id(granule).partition("-")[2]
    if not tail:
        return None
    return next((unit for unit in contents.units.values()
                 if _digits_id(unit.granule).endswith(f"-{tail}")), None)


_unit_cache: dict[str, UscUnit] = {}


def load_unit(granule: str) -> UscUnit:
    """A unit of the Code with its table of contents.  Raises UnitNotFound
    when the OLRC has no unit by that id.  A unit above a section read from
    Cornell has a Cornell id ("lii:28/part-VI") and is read from Cornell."""
    granule = str(granule).strip()
    if granule.startswith(_LII_PREFIX):
        with _cache_lock:
            if granule in _unit_cache:
                return _unit_cache[granule]
        unit = _lii_unit(granule)
        with _cache_lock:
            _unit_cache[granule] = unit
        return unit
    title = granule.split("-", 1)[0].replace("title", "", 1)
    try:
        contents = _title_contents(title)
    except UnitNotFound:
        raise
    except Exception as exc:
        raise RuntimeError(f"uscode.house.gov: {exc}") from exc
    unit = contents.units.get(granule) or _unit_by_viewer_id(contents, granule)
    if unit is None:
        raise UnitNotFound(f"uscode.house.gov has no page {granule}")
    if unit.granule == f"title{title}" and "—" not in unit.heading:
        unit.heading = _title_heading(title) or unit.heading
    return unit


def open_unit_entry(entry: UnitEntry, parent: UscUnit) -> UscUnit:
    """The unit a table entry names."""
    return load_unit(entry.granule)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# The page's own indentation.  Every paragraph class gives its left margin in
# ems — statutory-body-2em sits 2em in — and a subdivision's heading is set at
# the margin of its level.  That margin is the depth shown here.  A hanging
# paragraph, flushX_hangY, starts its first line at X em, which is where the
# reader's own hanging indent puts it at depth X.
#
# The page follows the printed Code's layout, and so does the reader: in an
# older section a subsection's paragraphs sit flush with it — "(a)", "(1)",
# "(2)", "(b)" at one margin, only "(A)" stepping in — and a paragraph that
# opens "(d)(1)" is followed by a flush "(2)".  statute_paths() reads the
# subdivision each paragraph belongs to from the same indentation.
_BODY_CLASS_PREFIXES = ("statutory-body", "note-body", "tableftnt", "usc28a")
_MAX_DEPTH = 6

_NOTE_HEAD_CLASSES = {"note-head", "note-sub-head", "analysis-subhead"}


def _is_body_class(cls: str) -> bool:
    return cls.lower().startswith(_BODY_CLASS_PREFIXES)


def _class_depth(cls: str) -> int:
    """How far in the page sets a paragraph of this class — see above."""
    m = re.search(r"flush(\d+)_hang\d+", cls)
    if m is None:
        m = re.search(r"-(\d+)em\b", cls)
    if m is not None:
        return min(int(m.group(1)), _MAX_DEPTH)
    if cls.lower().startswith("usc28a"):   # the title 28 appendix's forms
        return 4
    return 0

# The paragraphs, and the <div>s that say what they are.  A note quotes the
# provisions it amends in the very classes of the statute's own text, so it
# is where a paragraph stands, not its class, that keeps quoted statutory
# text in an amendment note from masquerading as current law.
_TOKEN_RE = re.compile(
    r'<div\b[^>]*?class="(statutory-body-container|note-block)\b[^"]*"'
    r"|<(h\d|p)\b[^>]*?class=\"([^\"]+)\"[^>]*>(.*?)</\2>",
    re.IGNORECASE | re.DOTALL,
)
# A subdivision's heading, a line of its own: "(a) <em>Employer practices</em>".
_LEVEL_HEAD_RE = re.compile(
    r'^[^<]*<em class="level-heading">.*</em>\s*$', re.DOTALL)
# A footnote's marker, kept off the text it follows: "$150,000." and footnote
# 1 are not "$150,000.1".
_FOOTNOTE_MARK_RE = re.compile(
    r"<sup\b[^>]*>\s*<a\b[^>]*>(.*?)</a>\s*</sup>", re.DOTALL)
_FOOTNOTE_NUMBER_RE = re.compile(r"^\s*<sup\b[^>]*>(.*?)</sup>", re.DOTALL)


def _clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    # the OLRC sets a word joiner before every dash
    text = _html.unescape(text).replace("⁠", "")
    return re.sub(r"\s+", " ", text).strip()


def _tidy(text: str) -> str:
    """A source credit's text, less the space the OLRC leaves after each of
    its links: "§ 1 , Dec. 29, 1979 , 93 Stat. 1284 ."."""
    return re.sub(r"\s+(?=[,;]|\.\)?$)", "", text)


def parse_section(page_html: str) -> list[tuple[str, int, str]]:
    """Parse an OLRC section page into a (kind, indent, text) stream, each
    paragraph at the indentation the page gives it (see _class_depth)."""
    paras: list[tuple[str, int, str]] = []
    is_note = False         # past the statute's own text
    footnotes = False
    for m in _TOKEN_RE.finditer(page_html):
        if m.group(1):
            is_note = m.group(1).lower() == "note-block"
            continue
        cls = m.group(3).split()[0]
        inner = _FOOTNOTE_MARK_RE.sub(r"[\1]", m.group(4))
        if cls == "footnote":
            inner = _FOOTNOTE_NUMBER_RE.sub(r"[\1] ", inner)
        text = _clean(inner)
        if not text:
            continue
        if cls == "section-head":
            paras.append(("sechead", 0, _tidy(text)))
        elif cls == "source-credit":
            is_note = True
            paras.append(("credit", 0, _tidy(text)))
        elif cls == "footnote":
            if not footnotes:
                footnotes = True
                paras.append(("note-head", 0, "Footnotes"))
            paras.append(("note-body", 0, text))
        elif not is_note and _is_body_class(cls):
            kind = "head" if _LEVEL_HEAD_RE.match(inner) else "body"
            paras.append((kind, _class_depth(cls), text))
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
# A repealed item is bracketed: "[(4) Repealed.".
_PATH_LEAD_RE = re.compile(
    r"^\[?((?:\((?:\d{1,3}[A-Za-z]{0,2}|[a-zA-Z]{1,5})\)\s*)+)"
)
# And an older section's heading runs in, with the first item below it on the
# same line: "(a) Authority To Convey.—(1) The Secretary may" opens as "(a)(1)"
# does, and a flush "(2)" follows it.
_PATH_RUN_IN_RE = re.compile(
    r"^\([^)]+\)\s[^()]*—((?:\((?:\d{1,3}[A-Za-z]{0,2}|[a-zA-Z]{1,5})\)\s*)+)"
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
        run_in = _PATH_RUN_IN_RE.match(text)
        if run_in:
            enums += re.findall(r"\(([^)]+)\)", run_in.group(1))
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


# ---------------------------------------------------------------------------
# Cornell's copy, for when the OLRC's site is down
#
# The OLRC takes its site down for maintenance now and then, and house.gov
# then answers every request with an "Under Maintenance" page — with HTTP
# 200, so only the page itself shows the site is down.  Cornell's Legal
# Information Institute republishes the OLRC's releases (its "How current is
# this?" page names the latest it has, so its copy can trail the OLRC's by
# a release), one section per page at
#
#     https://www.law.cornell.edu/uscode/text/{title}/{section}
#
# Its markup keeps the OLRC's XML element names.  A section's items nest as
# <div class="subsection">, "paragraph" … each with its designation
# (<span class="num">), heading, "chapeau" (its text before its items),
# "content", and "continuation" (its text after them).  Then comes the
# "sourceCredit", and the notes sit on a tab of their own.  Each item is set
# where the OLRC sets it: one level in from the text of the item it belongs
# to.  An item with no text of its own before its first item ("(b)" over
# "(1)") shares that item's line, "(b)(1)", and its items stay at its
# margin, so statute_paths reads Cornell's copy as it does the OLRC's.
#
# A unit above a section is a Cornell page too, listing the units or
# sections directly under it.  Its id is "lii:" and Cornell's path
# ("lii:28/part-VI/chapter-153").
# ---------------------------------------------------------------------------

LII_BASE = "https://www.law.cornell.edu/uscode/text/"
_LII_PREFIX = "lii:"
# Cornell answers a script that says what it is.
_LII_HEADERS = {
    "User-Agent": "GetCases/1.0 (legal research app)",
    "Accept": "text/html,application/xhtml+xml",
}


def lii_section_url(title: str, section: str) -> str:
    return f"{LII_BASE}{title}/{section}"


def _lii_section(title: str, section: str) -> UscSection:
    """A section from Cornell's copy.  Raises SectionNotFound when Cornell
    has no such section; any other failure propagates."""
    import requests

    last_err = "section not found"
    for cand in _candidates(section):
        url = lii_section_url(title, cand)
        resp = requests.get(url, headers=_LII_HEADERS, timeout=30)
        if resp.status_code == 404:
            last_err = f"no such section {title} U.S.C. § {cand}"
            continue
        resp.raise_for_status()
        page = resp.content.decode("utf-8", "replace")
        paras = parse_lii_section(page)
        if paras:
            return UscSection(
                title=title, section=cand, url=url, paras=paras,
                crumbs=lii_crumbs(page)[0], source="lii",
                adjacent=lii_adjacent(page),
            )
        last_err = f"no text found for {title} U.S.C. § {cand}"
    raise SectionNotFound(f"law.cornell.edu: {last_err}")


class _Node:
    """An element of a page, as _Tree reads it."""

    __slots__ = ("tag", "classes", "children")

    def __init__(self, tag: str, attrs: list) -> None:
        self.tag = tag
        self.classes = (dict(attrs).get("class") or "").split()
        self.children: list = []          # _Nodes and strings, in order

    @property
    def kind(self) -> str:
        """The element's first class: "subsection", "num", "chapeau" …"""
        return self.classes[0] if self.classes else ""


_VOID_TAGS = frozenset((
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
))


class _Tree(HTMLParser):
    """A piece of a page as a tree of _Nodes.  An end tag closes the
    nearest open element of its name, and whatever was left open in it."""

    def __init__(self, fragment: str) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", [])
        self._open = [self.root]
        self.feed(fragment)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self._open[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._open.append(node)

    def handle_startendtag(self, tag, attrs):
        self._open[-1].children.append(_Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self._open) - 1, 0, -1):
            if self._open[i].tag == tag:
                del self._open[i:]
                return

    def handle_data(self, data):
        self._open[-1].children.append(data)


def _find(node: _Node, kind: str) -> _Node | None:
    """The first element under *node* whose first class is *kind*."""
    for child in node.children:
        if isinstance(child, _Node):
            if child.kind == kind:
                return child
            found = _find(child, kind)
            if found is not None:
                return found
    return None


_BLOCK_TAGS = frozenset((
    "address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "h1",
    "h2", "h3", "h4", "h5", "h6", "hr", "li", "ol", "p", "pre", "section",
    "table", "tbody", "tfoot", "thead", "ul",
))


def _lii_blocks(node) -> list[str]:
    """The paragraphs of text in an element: its runs of inline text, split
    where a block element begins or ends.  A table row is one line, its
    cells separated by " | "."""
    out: list[str] = []
    run: list[str] = []

    def end_run() -> None:
        text = re.sub(r"\s+", " ", "".join(run)).strip()
        run.clear()
        if text:
            out.append(text.replace("⁄", "/"))   # the ⁄ in "1⁄12"

    def visit(n) -> None:
        if isinstance(n, str):
            run.append(n)
        elif n.tag in ("script", "style"):
            return
        elif n.tag == "tr":
            end_run()
            cells = [" ".join(_lii_blocks(cell)) for cell in n.children
                     if isinstance(cell, _Node) and cell.tag in ("td", "th")]
            row = " | ".join(cell for cell in cells if cell)
            if row:
                out.append(row)
        elif n.tag in _BLOCK_TAGS:
            end_run()
            for child in n.children:
                visit(child)
            end_run()
        else:
            for child in n.children:
                visit(child)

    visit(node)
    end_run()
    return out


_LII_LEVELS = frozenset((
    "subsection", "paragraph", "subparagraph", "clause", "subclause", "item",
    "subitem", "subsubitem",
))


class _LiiReader:
    """Reads Cornell's markup into the (kind, indent, text) stream that
    parse_section makes of the OLRC's page (see above)."""

    def __init__(self) -> None:
        self.paras: list[tuple[str, int, str]] = []
        # designations of items with no text of their own yet, which open
        # the line of the first text inside them: "(b)", "(1)"
        self._waiting: list[str] = []
        self._waiting_depth = 0

    def add(self, kind: str, depth: int, text: str) -> None:
        if self._waiting:
            text = "".join(self._waiting) + " " + text
            depth = self._waiting_depth
            self._waiting = []
        self.paras.append((kind, min(depth, _MAX_DEPTH), text))

    def items(self, node: _Node, depth: int, body: str, head: str) -> None:
        """The text and items inside a section or an item, its own text at
        *depth*.  Its items sit one level in, or at its own margin when
        nothing of its own comes before the first of them."""
        inner = depth
        for child in node.children:
            if isinstance(child, _Node) and child.kind in _LII_LEVELS:
                break
            if isinstance(child, str):
                if child.strip():
                    inner = depth + 1
                    break
            elif child.kind != "num" and _lii_blocks(child):
                inner = depth + 1
                break
        for child in node.children:
            if isinstance(child, str):
                text = re.sub(r"\s+", " ", child).strip()
                if text:
                    self.add(body, depth, text)
            elif child.kind in ("num", "heading"):
                continue            # the item's own, read by item()
            elif child.kind in _LII_LEVELS:
                self.item(child, inner, body, head)
            elif child.kind == "sourceCredit":
                for text in _lii_blocks(child):
                    self.paras.append(("credit", 0, text))
            elif child.tag == "div" and child.kind == "quotedContent":
                self.items(child, depth, body, head)
            else:
                for text in _lii_blocks(child):
                    self.add(body, depth, text)

    def item(self, node: _Node, depth: int, body: str, head: str) -> None:
        num = heading = ""
        for child in node.children:
            if isinstance(child, _Node) and child.kind == "num" and not num:
                num = " ".join(_lii_blocks(child))
            elif (isinstance(child, _Node) and child.kind == "heading"
                    and not heading):
                heading = " ".join(_lii_blocks(child))
        if heading:
            self.add(head, depth, f"{num} {heading}".strip())
        elif num:
            if not self._waiting:
                self._waiting_depth = depth
            self._waiting.append(num)
        self.items(node, depth, body, head)
        if self._waiting:           # nothing in it but its designation
            self.paras.append((body, min(self._waiting_depth, _MAX_DEPTH),
                               "".join(self._waiting)))
            self._waiting = []

    def notes(self, node: _Node) -> None:
        """The notes tab: each note's heading, then its paragraphs, tables
        and quoted provisions; the footnotes first, as Cornell sets them."""
        run: list = []

        def end_run() -> None:
            if run:
                holder = _Node("span", [])
                holder.children = list(run)
                run.clear()
                for text in _lii_blocks(holder):
                    self.add("note-body", 0, text)

        for child in node.children:
            if isinstance(child, str):
                run.append(child)
                continue
            if child.tag == "notes" or child.kind in ("notes", "note"):
                end_run()
                self.notes(child)
            elif child.tag == "span" and child.kind == "heading":
                end_run()
                text = " ".join(_lii_blocks(child))
                if text:
                    self.paras.append(("note-head", 0, text))
            elif child.tag == "hr" and "footsep" in child.classes:
                end_run()
                self.paras.append(("note-head", 0, "Footnotes"))
            elif child.kind in _LII_LEVELS:
                end_run()
                self.item(child, 0, "note-body", "note-body")
            elif child.tag == "div" and child.kind == "quotedContent":
                end_run()
                self.items(child, 0, "note-body", "note-body")
            elif child.tag in _BLOCK_TAGS:
                end_run()
                for text in _lii_blocks(child):
                    self.add("note-body", 0, text)
            else:
                run.append(child)
        end_run()


_LII_TITLE_RE = re.compile(
    r'<h1\b[^>]*\bid="page_title"[^>]*>(.*?)</h1>', re.DOTALL)
_LII_SECHEAD_RE = re.compile(
    r"U\.\s*S\.\s*Code\s*(§+)\s*(\S+)(?:\s+[-–—]\s+(.*))?$")
_LII_TEXT_RE = re.compile(r"<text>(.*?)</text>", re.DOTALL)
_LII_NOTES_RE = re.compile(r"<notes>(.*?)</notes>", re.DOTALL)


def parse_lii_section(page: str) -> list[tuple[str, int, str]]:
    """Parse a Cornell section page into the stream parse_section makes of
    the OLRC's: the section heading, its text, the source credit, then the
    notes.  Empty when the page holds no section."""
    title = _LII_TITLE_RE.search(page)
    if title is None:
        return []
    paras: list[tuple[str, int, str]] = []
    heading = _clean(title.group(1))
    m = _LII_SECHEAD_RE.search(heading)
    if m:   # "42 U.S. Code § 1983 - Civil action …" as the OLRC heads it
        sign, number, name = m.groups()
        heading = f"{sign}{number}. {name}" if name else f"{sign}{number}"
    paras.append(("sechead", 0, heading))
    reader = _LiiReader()
    text = _LII_TEXT_RE.search(page, title.end())
    if text:
        root = _Tree(text.group(1)).root
        reader.items(_find(root, "section") or root, 0, "body", "head")
    notes = _LII_NOTES_RE.search(page, title.end())
    if notes:
        reader.notes(_Tree(notes.group(1)).root)
    paras.extend(reader.paras)
    return paras


_LII_CRUMBS_RE = re.compile(r'<ol class="breadcrumb">(.*?)</ol>', re.DOTALL)
_LII_LI_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", re.DOTALL)
_LII_HREF_RE = re.compile(r'href="([^"]*)"')
_LII_PATH_RE = re.compile(
    r"^(?:https?://www\.law\.cornell\.edu)?/uscode/text/([^?#]+?)/?"
    r"(?:[?#].*)?$")


def _lii_path(href: str) -> str | None:
    """Cornell's path for a link to a page of its U.S. Code — "28/part-VI"
    for "/uscode/text/28/part-VI" — or None for any other link."""
    m = _LII_PATH_RE.match(_html.unescape(href))
    return unquote(m.group(1)) if m else None


def lii_crumbs(page: str) -> tuple[list[tuple[str, str]], str]:
    """The units above a Cornell page, outermost first, as (id, label) —
    and the page's own label ("CHAPTER 153", "§ 2254") — from its
    breadcrumb."""
    m = _LII_CRUMBS_RE.search(page)
    if m is None:
        return [], ""
    crumbs: list[tuple[str, str]] = []
    own = ""
    for item in _LII_LI_RE.findall(m.group(1)):
        href = _LII_HREF_RE.search(item)
        if href is None:
            own = _clean(item)
            continue
        path = _lii_path(href.group(1))
        if path:
            crumbs.append((_LII_PREFIX + path, _clean(item)))
    return crumbs, own


_LII_PREVNEXT_RE = re.compile(r'<div id="prevnext">(.*?)</div>', re.DOTALL)
_LII_PREVNEXT_LINK_RE = re.compile(
    r'<a\b[^>]*\bhref="([^"]+)"[^>]*>\s*(prev|next)\s*</a>')


def _lii_section_path(path: str | None) -> tuple[str, str] | None:
    """(title, section) for the path of a section's page; None for a unit's
    ("28/part-VI/chapter-151")."""
    parts = (path or "").split("/")
    if len(parts) == 2 and parts[1][:1].isdigit():
        return parts[0], parts[1]
    return None


def lii_adjacent(page: str) -> tuple[tuple[str, str] | None,
                                     tuple[str, str] | None]:
    """The sections before and after a Cornell section page, from its
    prev/next links."""
    found: dict[str, tuple[str, str] | None] = {"prev": None, "next": None}
    m = _LII_PREVNEXT_RE.search(page)
    if m:
        for href, which in _LII_PREVNEXT_LINK_RE.findall(m.group(1)):
            found[which] = _lii_section_path(_lii_path(href))
    return found["prev"], found["next"]


_LII_TOC_ITEM_RE = re.compile(
    r'<li class="tocitem">\s*<a\b([^>]*)>(.*?)</a>', re.DOTALL)
_LII_TITLE_ATTR_RE = re.compile(r'\btitle="([^"]*)"')
_LII_TOC_SECTION_RE = re.compile(r"^\[?\s*§+\s*(.+?)\.(?:\s+(.*?))?\]?$")
_LII_TOC_UNIT_RE = re.compile(
    r"^(title|subtitle|division|subdivision|part|subpart|chapter|"
    r"subchapter|article)\s+(\S+?)\s*(?:—\s*(.*))?$",
    re.IGNORECASE,
)
_LII_TOC_FIRST_RE = re.compile(r"\s*\(§§?\s*([^\s)]+)[^)]*\)\s*$")


def parse_lii_unit(page: str, granule: str, url: str) -> UscUnit:
    """A Cornell unit page: its heading, and the units or sections it
    lists — "SUBCHAPTER I—GENERALLY (§§ 1981 – 1996b)", "§ 2254. State
    custody; remedies in Federal courts"."""
    path = granule[len(_LII_PREFIX):]
    crumbs, own = lii_crumbs(page)
    title = _LII_TITLE_RE.search(page)
    h1 = _clean(title.group(1)) if title else ""
    name = re.split(r"\s+[-–—]\s+", h1, maxsplit=1)
    own = own or h1
    heading = f"{own}—{name[1]}" if len(name) > 1 else own
    entries: list[UnitEntry] = []
    for attrs, inner in _LII_TOC_ITEM_RE.findall(page):
        href = _LII_HREF_RE.search(attrs)
        target = _lii_path(href.group(1)) if href else None
        if not target:
            continue
        text = _clean(inner)
        section = _lii_section_path(target)
        if section:
            m = _LII_TOC_SECTION_RE.match(text)
            entries.append(UnitEntry(
                "section", 0, f"§ {m.group(1)}" if m else text,
                (m.group(2) or "").strip() if m else "",
                section=section[1]))
            continue
        first = _LII_TOC_FIRST_RE.search(text)
        unit = _LII_TOC_UNIT_RE.match(text[:first.start()] if first else text)
        named = _LII_TITLE_ATTR_RE.search(attrs)
        entries.append(UnitEntry(
            "unit", 0,
            (f"{crumb_label(unit.group(1))} {unit.group(2)}" if unit
             else text),
            (_html.unescape(named.group(1)).strip() if named
             else (unit.group(3) or "") if unit else ""),
            unit_kind=unit.group(1).lower() if unit else "",
            designation=unit.group(2) if unit else "",
            granule=_LII_PREFIX + target,
            first_section=(first.group(1).replace("–", "-")
                           if first else ""),
        ))
    return UscUnit(
        title=path.split("/", 1)[0], granule=granule, url=url,
        label=crumb_label(own), heading=heading, crumbs=crumbs,
        entries=entries, host="law.cornell.edu",
    )


def _lii_unit(granule: str) -> UscUnit:
    import requests

    path = granule[len(_LII_PREFIX):].strip("/")
    url = LII_BASE + path
    try:
        resp = requests.get(url, headers=_LII_HEADERS, timeout=30)
        if resp.status_code == 404:
            raise UnitNotFound(f"law.cornell.edu has no page {path}")
        resp.raise_for_status()
    except UnitNotFound:
        raise
    except Exception as exc:
        raise RuntimeError(f"law.cornell.edu: {exc}") from exc
    unit = parse_lii_unit(resp.content.decode("utf-8", "replace"),
                          granule, url)
    print(f"[usc-unit] {granule}: {len(unit.entries)} entries")
    return unit


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
<h3 class="section-head">&sect;110. Same; income tax</h3>
<div class="statutory-body-container">
<p class="statutory-body">(a) No State, or political subdivision thereof,
 may, for purposes of any income tax levied by such State&mdash;</p>
<p class="statutory-body-1em">(1) treat such Member as a <em>resident</em>
 or domiciliary of such State; or</p>
<p class="statutory-body-1em">(2) treat any compensation paid by the
 United States to such Member as income for services performed within
 such State,</p>
<p class="statutory-body-2em">(A) a deeper clause;</p>
<p class="statutory-body">(b) For purposes of subsection (a)&mdash;</p>
</div>
<p class="source-credit">(Added Pub. L. 99&ndash;190, Dec. 19, 1985 ,
 <a href="/statviewer.html?volume=99&page=1185">99 Stat. 1185</a> .)</p>
<div class="note-block" id="editorialNotes-note">
<h4 class="note-head legacy-note-style">Editorial Notes</h4></div>
<div class="note-block" id="amendments-note">
<h4 class="note-sub-head">Amendments</h4>
<p class="note-body">1985&mdash;Subsec. (a). Pub. L. 99&ndash;190 added
 text reading as follows:</p>
<p class="statutory-body">(x) quoted statute text inside a note</p>
</div>
</body></html>"""
    paras = parse_section(sample)
    kinds = [(k, i) for k, i, _t in paras]
    check(paras[0] == ("sechead", 0, "§110. Same; income tax"),
          f"section head: {paras[0]!r}")
    check(("body", 0) in kinds and ("body", 1) in kinds
          and ("body", 2) in kinds, f"body indents: {kinds!r}")
    check(("credit", 0, "(Added Pub. L. 99–190, Dec. 19, 1985, "
           "99 Stat. 1185.)") in paras, "source credit captured, tidied")
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
<div class="statutory-body-container">
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
</div>"""
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
<div class="statutory-body-container">
<p class="statutory-body" id="b">(b)&nbsp;<em class="level-heading"> Premium assistance credit amount</em></p>
<p class="statutory-body-1em" id="b-3">(3)&nbsp;<em class="level-heading"> Other terms</em></p>
<p class="statutory-body-2em" id="b-3-B">(B)&nbsp;<em class="level-heading"> Applicable second lowest cost silver plan</em></p>
<p class="statutory-body-2em">The applicable plan is the plan which-</p>
<p class="statutory-body-3em">(ii) provides-</p>
<p class="statutory-body-4em">(I) self-only coverage-</p>
<p class="statutory-body-5em">(aa) whose tax is determined under section 1(c), or</p>
<p class="statutory-body-4em">(II) family coverage.</p>
<p class="statutory-body-block-2em">If a taxpayer files a joint return, the plan is determined as follows.</p>
<p class="statutory-body-2em" id="b-3-C">(C)&nbsp;<em class="level-heading"> Adjusted monthly premium</em></p>
</div>"""
    headed_paras = parse_section(headed)
    check([k for k, _i, _t in headed_paras].count("head") == 4,
          f"headings are heads: {headed_paras!r}")
    check([i for _k, i, _t in headed_paras] == [0, 1, 2, 2, 3, 4, 5, 4, 2, 2],
          f"heading and block depths: {headed_paras!r}")
    check(statute_paths(headed_paras) == [
        ("b",), ("b", "3"), ("b", "3", "B"), ("b", "3", "B"),
        ("b", "3", "B", "ii"), ("b", "3", "B", "ii", "I"),
        ("b", "3", "B", "ii", "I", "aa"), ("b", "3", "B", "ii", "II"),
        ("b", "3", "B"), ("b", "3", "C")],
        f"headed paths: {statute_paths(headed_paras)!r}")

    raise SystemExit(1 if failed else 0)
