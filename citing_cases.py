"""The cases citing a case, ten at a time: the side panel's Citing cases view.

Google Scholar's "Cited by" list comes first — every case citing this one
that Scholar holds, with the passage where each cites it and Scholar's mark
for how much it discusses it, in Scholar's order of relevance or newest
first, narrowed to a span of years or to courts.  Scholar keeps such a list
for a case it holds only the citation of, too.

CourtListener answers when Scholar has no list for the case or is not
answering.  Each page is one search: the cases whose opinions CourtListener's
citation graph links to this case's (``cites:``), ten at a time, with the
case's own citations and name searched for as well — but only as
alternatives to the citation graph, so they keep no citing case off the list
and add none to it.  They decide which passage CourtListener quotes from each
case: the one that cites this one.  (Asked for by ``cites:`` alone, it
quotes each opinion's first lines, the caption.)  Nothing is looked up case
by case: the search result says all the list shows.

Tkinter-free: the panel calls :meth:`CitingLookup.page` on a worker thread
and shows what comes back.
"""

from __future__ import annotations

import html as _html
import re
import threading
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Optional

#: Cases on a page, from either source.
PAGE_SIZE = 10

SCHOLAR = "scholar"
COURTLISTENER = "courtlistener"


@dataclass(frozen=True)
class Filters:
    """The order the reader asked for, and what they narrowed the list to."""

    #: Newest first; None leaves it to the source — Scholar's own order, by
    #: relevance, or CourtListener's newest first.
    by_date: Optional[bool] = None
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    #: CourtListener court ids; empty for every court.
    courts: frozenset = frozenset()

    def newest_first(self, source: str) -> bool:
        """Whether the list from *source* is newest first under these."""
        if self.by_date is not None:
            return self.by_date
        return source == COURTLISTENER


@dataclass
class CourtListenerTarget:
    """What CourtListener finds a case's citing cases by: the CourtListener
    *client*, the ids of the case's opinions (its citation graph's nodes),
    and *phrases* — its citations and name — that pick out each citing
    case's passage about it."""

    client: object
    opinion_ids: list
    phrases: list = field(default_factory=list)


@dataclass
class CitingPage:
    """A page of citing cases, as the panel shows it."""

    #: SCHOLAR or COURTLISTENER; "" when neither has a list for the case.
    source: str
    #: google_scholar.CitingResult rows from Scholar, CourtListener's search
    #: results (dicts) from CourtListener.
    cases: list
    index: int = 0
    total: Optional[int] = None
    #: Scholar's count is its estimate ("About 4,290 results").
    total_estimated: bool = False
    has_next: bool = False
    newest_first: bool = False
    #: Why the list is from where it is, or why there is none.
    note: str = ""
    #: The source did not answer (the note says so): asking again may do.
    failed: bool = False
    #: The ids of the case's opinions, which a CourtListener result's own
    #: opinions are checked against for the passage citing it.
    opinion_ids: list = field(default_factory=list)


class CitingLookup:
    """One case's citing cases, a page at a time.

    *scholar* is the Google Scholar fetcher, or None.  *scholar_ids* are the
    ids Scholar may list the citing cases under, best first — the open
    opinion's own ``case=`` id, or the one the opinion database holds for the
    case.  Failing those, Scholar is searched for *citation* (*name* and
    *year* pick the case when several begin on the page).

    *courtlistener* is called, on the worker, for the
    :class:`CourtListenerTarget` — once, and only when Scholar has no list —
    or is None when CourtListener cannot be asked (no API token).

    Which source answers is settled by the first page and kept, so paging
    and narrowing never switch lists under the reader.  When Scholar does
    not answer at the outset, the list is CourtListener's and says why;
    :meth:`retry_scholar` asks Scholar again.  When it stops answering
    partway through, the page says so instead (:meth:`use_courtlistener`
    switches).
    """

    def __init__(self, *, scholar=None, scholar_ids=(), citation: str = "",
                 name: str = "", year: str = "",
                 courtlistener: Optional[Callable[[], Optional[
                     CourtListenerTarget]]] = None) -> None:
        self._scholar = scholar
        self._scholar_ids = list(dict.fromkeys(
            str(i).strip() for i in scholar_ids if str(i or "").strip()))
        self._citation = (citation or "").strip()
        self._name = (name or "").strip()
        self._year = str(year or "").strip()[:4]
        self._courtlistener = courtlistener
        self._lock = threading.Lock()
        #: The source the list comes from, once the first page has settled it.
        self.source: Optional[str] = None
        # Scholar's id for the case's list: None until looked for, "" for none.
        self._scholar_id: Optional[str] = None
        self._scholar_unanswered = False
        self._note = ""
        self._target: Optional[CourtListenerTarget] = None
        self._target_resolved = False
        # CourtListener's results for each list (by Filters), as far as the
        # reader has gone — see _cl_results.
        self._cl_lists: dict[Filters, _ResultRun] = {}

    @property
    def scholar_unanswered(self) -> bool:
        """Whether the list is CourtListener's because Scholar did not answer
        (rather than because Scholar has none)."""
        return self._scholar_unanswered

    def page(self, filters: Filters, index: int = 0) -> CitingPage:
        """The *index*-th page of the list under *filters* (blocking: call
        it on a worker)."""
        with self._lock:
            if self.source != COURTLISTENER:
                page = self._from_scholar(filters, index)
                if page is not None:
                    return page
            return self._from_courtlistener(filters, index)

    def use_courtlistener(self) -> None:
        """Take the list from CourtListener from now on."""
        with self._lock:
            self.source = COURTLISTENER
            self._scholar_unanswered = True
            self._note = ("Google Scholar is not answering, so these are "
                          "CourtListener's citing cases.")

    def retry_scholar(self) -> None:
        """Ask Scholar again, after it did not answer."""
        with self._lock:
            if self._scholar_unanswered:
                self.source = None
                self._scholar_unanswered = False
                self._scholar_id = None
                self._note = ""

    # ------------------------------------------------------------------
    # Google Scholar
    # ------------------------------------------------------------------

    def _from_scholar(self, filters: Filters, index: int
                      ) -> Optional[CitingPage]:
        """The page from Scholar, or None to take CourtListener's."""
        newest = filters.newest_first(SCHOLAR)
        if self._scholar is None:
            self.source = COURTLISTENER
            return None
        try:
            if self._scholar_id is None:
                self._scholar_id = self._scholar_list_id()
            if not self._scholar_id:
                self.source = COURTLISTENER
                self._note = "Google Scholar lists no cases citing this one."
                return None
            found = self._scholar.citing_page(
                self._scholar_id, by_date=newest,
                year_from=filters.year_from, year_to=filters.year_to,
                courts=filters.courts, start=index * PAGE_SIZE)
        except Exception as exc:
            if self.source == SCHOLAR:
                # The list being read is Scholar's: say it did not answer,
                # rather than switch lists under the reader.
                return CitingPage(
                    SCHOLAR, [], index, newest_first=newest, failed=True,
                    note=f"Google Scholar did not answer ({exc}).")
            print(f"[citing] Google Scholar did not answer: {exc}")
            self.source = COURTLISTENER
            self._scholar_unanswered = True
            self._scholar_id = None
            self._note = ("Google Scholar is not answering right now, so "
                          "these are CourtListener's citing cases.")
            return None
        self.source = SCHOLAR
        return CitingPage(
            SCHOLAR, list(found.results), index, total=found.total,
            total_estimated=found.total is not None,
            has_next=found.has_next, newest_first=newest)

    def _scholar_list_id(self) -> str:
        """The id Scholar lists the case's citing cases under: the first of
        the ids known for the case whose list has anything on it, else the
        one a search for its citation finds — "" when none has a list."""
        tried: set[str] = set()
        for cites_id in self._scholar_ids:
            tried.add(cites_id)
            if self._scholar.citing_page(cites_id).results:
                return cites_id
        if self._citation:
            found = self._scholar.find_citing_id(
                self._citation, self._name, self._year)
            if found and found not in tried:
                if self._scholar.citing_page(found).results:
                    return found
        return ""

    # ------------------------------------------------------------------
    # CourtListener
    # ------------------------------------------------------------------

    def _target_for_case(self) -> Optional[CourtListenerTarget]:
        if not self._target_resolved:
            self._target_resolved = True
            if self._courtlistener is not None:
                try:
                    self._target = self._courtlistener()
                except Exception as exc:
                    print(f"[citing] finding the case on CourtListener "
                          f"failed: {exc}")
        return self._target

    def _from_courtlistener(self, filters: Filters, index: int
                            ) -> CitingPage:
        newest = filters.newest_first(COURTLISTENER)
        target = self._target_for_case()
        if target is None or not target.opinion_ids:
            if self._courtlistener is None:
                why = ("CourtListener's citing cases need a CourtListener "
                       "API token (Settings ▸ API Token…).")
            else:
                why = "CourtListener does not have this case."
            return CitingPage(
                "", [], index, newest_first=newest,
                note=" ".join(p for p in (self._note, why) if p))
        run = self._cl_lists.setdefault(filters, _ResultRun())
        start = index * PAGE_SIZE
        try:
            self._read_through(run, target, filters, start + PAGE_SIZE)
        except Exception as exc:
            return CitingPage(
                COURTLISTENER, [], index, newest_first=newest, failed=True,
                note=f"CourtListener did not answer ({exc}).",
                opinion_ids=list(target.opinion_ids))
        self.source = COURTLISTENER
        return CitingPage(
            COURTLISTENER, run.results[start:start + PAGE_SIZE], index,
            total=run.total,
            has_next=len(run.results) > start + PAGE_SIZE or not run.done,
            newest_first=newest, note=self._note,
            opinion_ids=list(target.opinion_ids))

    @staticmethod
    def _read_through(run: "_ResultRun", target: CourtListenerTarget,
                      filters: Filters, wanted: int) -> None:
        """Read CourtListener's pages of the list, in order, until *run*
        holds *wanted* results or the list ends.  CourtListener sets its own
        page size (twenty, whatever is asked for), so a page of its results
        can hold two of the panel's, or end partway through one."""
        newest = filters.newest_first(COURTLISTENER)
        query = cl_query(target.opinion_ids, target.phrases)
        while len(run.results) < wanted and not run.done:
            data = target.client.search(
                query, type="o", cursor=run.cursor, page_size=PAGE_SIZE,
                highlight=True,
                court=" ".join(sorted(filters.courts)) or None,
                date_filed_min=(f"{filters.year_from}-01-01"
                                if filters.year_from else None),
                date_filed_max=(f"{filters.year_to}-12-31"
                                if filters.year_to else None),
                extra={"order_by": ("dateFiled desc" if newest
                                    else "score desc")},
            )
            results = list(data.get("results") or [])
            run.results.extend(results)
            count = data.get("count")
            if isinstance(count, int):
                run.total = count
            run.cursor = _cursor(str(data.get("next") or ""))
            run.done = not run.cursor or not results


@dataclass
class _ResultRun:
    """CourtListener's results for one list, as far as they have been read:
    the results in order, the count it gives, and the cursor for the next of
    its pages (``done`` once there is none)."""

    results: list = field(default_factory=list)
    total: Optional[int] = None
    cursor: Optional[str] = None
    done: bool = False


def _cursor(next_url: str) -> Optional[str]:
    """The cursor a CourtListener search's ``next`` URL carries."""
    if not next_url:
        return None
    query = urllib.parse.urlparse(next_url).query
    return urllib.parse.parse_qs(query).get("cursor", [None])[0]


# ---------------------------------------------------------------------------
# CourtListener's search, and the passages it quotes
# ---------------------------------------------------------------------------

def cl_query(opinion_ids, phrases=()) -> str:
    """The CourtListener search for the cases citing a case: those whose
    opinions cite one of *opinion_ids*, with *phrases* (the case's
    citations and name) as alternatives — which a citing case satisfies
    anyway, by citing the case, so they change nothing on the list, only
    which passage of each case CourtListener quotes."""
    graph = " OR ".join(f"cites:{int(i)}" for i in opinion_ids)
    graph = f"({graph})"
    quoted: list[str] = []
    for phrase in phrases:
        phrase = re.sub(r"\s+", " ", re.sub(r'["\\]', " ", str(phrase or "")))
        phrase = phrase.strip()
        if len(phrase) >= 3 and f'"{phrase}"' not in quoted:
            quoted.append(f'"{phrase}"')
    if not quoted:
        return graph
    return f"{graph} AND ({' OR '.join([graph] + quoted)})"


_MARK_RE = re.compile(r"<mark>(.*?)</mark>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _repair(text: str) -> str:
    """CourtListener's text keeps a "�" where a character was lost in its
    conversion — a dash in a page span ("231�32"), an apostrophe in a word
    ("Hawai�i").  Put back what it most likely was."""
    text = re.sub(r"(?<=\d)�(?=\d)", "–", text)
    text = re.sub(r"(?<=[A-Za-z])�(?=[A-Za-z])", "’", text)
    return text.replace("�", "")


def clean_text(value) -> str:
    """A CourtListener search field as plain text: its highlighting and
    other markup taken out, entities read, spacing tidied."""
    text = _html.unescape(_TAG_RE.sub("", str(value or "")))
    return re.sub(r"\s+", " ", _repair(text)).strip()


def mark_runs(snippet: str) -> list[tuple[str, bool]]:
    """A CourtListener snippet as runs of (text, highlighted) — its
    ``<mark>``s are the words searched for — set off as the excerpt it is."""
    raw: list[tuple[str, bool]] = []
    at = 0
    for m in _MARK_RE.finditer(snippet or ""):
        raw.append((snippet[at:m.start()], False))
        raw.append((m.group(1), True))
        at = m.end()
    raw.append(((snippet or "")[at:], False))
    runs: list[tuple[str, bool]] = []
    for text, hit in raw:
        text = re.sub(r"\s+", " ", _repair(
            _html.unescape(_TAG_RE.sub("", text))))
        if runs and runs[-1][0].endswith(" ") and text.startswith(" "):
            text = text[1:]
        if not text:
            continue
        if runs and runs[-1][1] == hit:
            runs[-1] = (runs[-1][0] + text, hit)
        else:
            runs.append((text, hit))
    if not any(hit for _text, hit in runs):
        return []
    # An excerpt from mid-sentence, both ends: the marks of omission are
    # never part of what was searched for, even beside it.
    runs[0] = (runs[0][0].lstrip(), runs[0][1])
    runs[-1] = (runs[-1][0].rstrip(), runs[-1][1])
    if runs[0][1]:
        runs.insert(0, ("… ", False))
    else:
        runs[0] = ("… " + runs[0][0], False)
    if runs[-1][1]:
        runs.append((" …", False))
    else:
        runs[-1] = (runs[-1][0] + " …", False)
    return runs


def _writing(kind) -> str:
    """A CourtListener opinion type as the separate writing it is, or ""."""
    kind = str(kind or "").lower()
    if "dissent" in kind and "concur" in kind:
        return "concurrence and dissent"
    if "dissent" in kind:
        return "dissent"
    if "concur" in kind:
        return "concurrence"
    return ""


def cl_passage(result: dict, opinion_ids=()) -> tuple[list, str]:
    """Where a CourtListener search *result* cites the case: the quoted
    passage of the first of its opinions to cite one of *opinion_ids*, as
    runs of (text, highlighted), and that opinion's kind when it is a
    separate writing ("dissent") — ``([], "")`` when CourtListener quoted no
    passage naming the case (only the opinion's first lines)."""
    opinions = result.get("opinions") or []
    if isinstance(opinions, dict):
        opinions = [opinions]
    targets = {int(i) for i in opinion_ids if str(i).isdigit()}

    def cites_case(opinion: dict) -> bool:
        cited = {int(c) for c in opinion.get("cites") or []
                 if str(c).isdigit()}
        return bool(cited & targets)

    ordered = sorted((op for op in opinions if isinstance(op, dict)),
                     key=lambda op: not cites_case(op))
    for opinion in ordered:
        runs = mark_runs(str(opinion.get("snippet") or ""))
        if runs:
            return runs, _writing(opinion.get("type"))
    return [], ""
