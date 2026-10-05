"""Which of its names the courts cite an old ejectment case by.

An ejectment caption allows several citations of the claimant's party — "Den
ex dem. Murray", "Den", "Murray", "Murray's Lessee" (see
bluebook_names.ejectment_case_names) — and which one later courts settled on
differs case by case: "Murray's Lessee v. Hoboken Land & Improvement Co.",
but "Doe v. Considine" and "Jackson v. Lamphire".  So the opinions citing the
case are asked.  Each form is a phrase with the adverse party's word beside
the "v." ("Murray's Lessee v. Hoboken"), counted among the opinions
CourtListener's citation graph links to the case's (``cites:``); the form
most of them use is the one cited.

A phrase that a longer form contains — "Murray v. Hoboken" inside "Den ex
dem. Murray v. Hoboken", "Martin v. Hunter" inside "Martin v. Hunter's
Lessee" (CourtListener drops the possessive) — is counted only in opinions
not using the longer one.

The answer is kept on disk, so a case is asked about once.  Tkinter-free: the
text view calls :func:`most_cited_form` on a worker thread.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

#: The fewest citing opinions using the leading form that settle the
#: citation; fewer leave the caption's own form.
MIN_CITING = 2

CACHE_PATH = Path.home() / ".config" / "courtlistener" / "case_name_usage.json"

_lock = threading.Lock()
_cache: Optional[dict] = None
_cache_path: Optional[Path] = None


def _tokens(phrase: str) -> list[str]:
    """*phrase* as CourtListener's search reads it: lowercase words, the
    possessive dropped ("Hunter's" and "Hunters'" are "hunter")."""
    phrase = re.sub(r"['’]s\b|(?<=s)['’]", "", phrase.lower())
    return re.findall(r"[a-z0-9]+", phrase)


def _contains(longer: list[str], shorter: list[str]) -> bool:
    """Whether *shorter* runs, word for word, inside *longer*."""
    n = len(shorter)
    return 0 < n < len(longer) and any(
        longer[i:i + n] == shorter for i in range(len(longer) - n + 1))


def _quoted(phrase: str) -> str:
    return '"' + re.sub(r'\s+', " ", re.sub(r'["\\]', " ", phrase)).strip() + '"'


def usage_phrases(names) -> dict[str, str]:
    """Each form's phrase, the party beside the adverse party's word:
    ``{"lessee": "Murray's Lessee v. Hoboken", …}``."""
    anchor = names.anchor()
    if not anchor:
        return {}
    return {
        form: (f"{party} v. {anchor}" if names.side == 0
               else f"{anchor} v. {party}")
        for form, party in names.parties.items()
    }


def usage_queries(names, opinion_ids) -> dict[str, str]:
    """The CourtListener search counting each form among the opinions that
    cite one of *opinion_ids*."""
    phrases = usage_phrases(names)
    if not phrases or not opinion_ids:
        return {}
    graph = "(" + " OR ".join(f"cites:{int(i)}" for i in opinion_ids) + ")"
    anchor = names.anchor()
    out: dict[str, str] = {}
    for form, phrase in phrases.items():
        words = _tokens(phrase)
        longer = [p for f, p in phrases.items()
                  if f != form and _contains(_tokens(p), words)]
        party = names.parties[form]
        # Forms the caption's own wording does not offer contain these too:
        # a lessor's "Murray v. Hoboken" is in "ex dem. Murray v. Hoboken"
        # and "Lessee of Murray v. Hoboken" whatever the caption said, and
        # a nominal plaintiff's "McEwen v. Den" in "McEwen v. Den, lessee of
        # Bulkley".
        if form == "lessor" and names.side == 0:
            longer += [f"dem. {party} v. {anchor}", f"of {party} v. {anchor}"]
        if form == "nominal" and names.side == 1:
            longer += [f"{anchor} v. {party} ex dem",
                       f"{anchor} v. {party} lessee",
                       f"{anchor} v. {party} on the demise"]
        # One left out is left out whatever else it says: "dem. Murray v.
        # Hoboken" covers "Den ex dem. Murray v. Hoboken".
        kept = [p for p in dict.fromkeys(longer)
                if not any(q != p and _contains(_tokens(p), _tokens(q))
                           for q in longer)]
        out[form] = (f"{graph} AND {_quoted(phrase)}"
                     + "".join(f" NOT {_quoted(p)}" for p in kept))
    return out


def count_forms(client, names, opinion_ids) -> dict[str, int]:
    """How many opinions citing the case use each form.  A search that
    fails raises: a count missing is no count of nought."""
    counts: dict[str, int] = {}
    for form, query in usage_queries(names, opinion_ids).items():
        data = client.search(query, type="o", page_size=1)
        count = data.get("count") if isinstance(data, dict) else None
        counts[form] = int(count) if isinstance(count, int) else 0
    return counts


def pick_form(counts: dict, default: str) -> Optional[str]:
    """The form most citing opinions use — the caption's own where it ties
    for the lead — or None when too few use any (see MIN_CITING)."""
    if not counts:
        return None
    best = max(counts.values())
    if best < MIN_CITING:
        return None
    leaders = [form for form, n in counts.items() if n == best]
    return default if default in leaders else leaders[0]


def most_cited_form(client, names, opinion_ids
                    ) -> tuple[Optional[str], dict[str, int]]:
    """``(form, counts)``: the form to cite the case by (None to keep the
    caption's) and what each form counted."""
    counts = count_forms(client, names, opinion_ids)
    return pick_form(counts, names.default), counts


# ---------------------------------------------------------------------------
# What has been asked already
# ---------------------------------------------------------------------------

def cache_key(cite: str, names) -> str:
    """The case's key on disk: its citation, however spaced ("55 Cal.2d
    663" or "55 Cal. 2d 663"), and its caption-form name, so two cases
    sharing a reporter page are kept apart."""
    cite = re.sub(r"\s+", "", cite or "")
    return f"{cite}|{names.names[names.default]}"


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


def remembered_form(key: str, path: Optional[Path] = None) -> Optional[str]:
    """The form found for *key* before — "" when the citing opinions settled
    none — or None when the case has not been asked about."""
    with _lock:
        entry = _loaded(path).get(key)
    if not isinstance(entry, dict):
        return None
    form = entry.get("form")
    return form if isinstance(form, str) else None


def remember_form(key: str, form: Optional[str], counts: dict,
                  path: Optional[Path] = None) -> None:
    """Keep what the citing opinions said about *key*.  Failing to write is
    no failure: the case is only asked about again."""
    path = path or CACHE_PATH
    with _lock:
        data = _loaded(path)
        data[key] = {"form": form or "", "counts": dict(counts),
                     "checked": time.strftime("%Y-%m-%d")}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=1, sort_keys=True),
                            encoding="utf-8")
        except Exception as exc:
            print(f"[name-usage] could not save {path}: {exc}")
