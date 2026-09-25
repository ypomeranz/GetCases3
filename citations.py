"""Citation detection shared by the opinion reader and the brief viewer.

This module is deliberately free of any ``tkinter`` dependency so the citation
logic can be unit-tested headlessly (``python3 citations.py``) and reused by the
"Open Brief…" feature, which renders a user's brief and highlights every
citation it can resolve.

It owns the reporter-citation regexes (case cites, short forms, ``Id.``) that
used to live in ``courtlistener_gui`` and adds :func:`detect_links`, which scans
a whole document and returns the clickable spans — case citations plus every
statute/regulation/rule/constitution source the app already knows how to open.

The per-source modules (``us_code``, ``ecfr``, ``fed_rules``, ``constitution``,
``state_statutes``, ``statutes_at_large``, ``federal_register``) each expose
their own citation parser; :func:`detect_links` simply runs them all over the
text and reconciles overlaps the same way the opinion reader does.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import bluebook_names
import constitution
import court_catalog
import ecfr
import eng_rep
import fed_cas
import fed_rules
import federal_register
import state_statutes
import statutes_at_large
import us_code

# A pinpoint page following a case citation: ", 171", ", at 171", or
# ", 171-72" — but not
# the volume of a parallel citation (", 510 A.2d 562"), recognized by the
# capital letter that follows the number.
PINCITE_AFTER_RE = re.compile(
    r",\s*(?:at\s+)?\*?(\d{1,6})(?:\s*[-–—]\s*\*?\d{1,6})?(?!\d|\s*[A-Z])",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Footnote pin cites
# ---------------------------------------------------------------------------
# Bluebook rule 3.2(b) cites material in a note by the page the note is *called
# on* plus the note's own number: "200 U.S. 12, 13 n.4", "13 n. 4", "13 nn.4-5",
# "13 & n.4", "13 nn. 4 & 6".  Without this the pin cite read as a plain
# reference to page 13 and the note number was left outside the link.
#
# Deliberately case-sensitive: an upper-case "N." next to digits is a reporter
# ("529 N.W. 2d 155"), never a note.
_NOTE_NUMBER = r"\d{1,4}[a-z]?|\*{1,3}|†{1,2}|‡{1,2}"
# ``(?!\d)`` after each number stops the digit run being taken apart: without
# it "…, 30 n.7" would fall back to reading a note "3" and leaving "0 n.7".
_NOTE_HEAD_RE = re.compile(
    r"\s*(?:,\s*)?(?:&\s*)?(nn\.|n\.|notes?\b\.?)\s*"
    r"(" + _NOTE_NUMBER + r")(?!\d)"
)
# A further note in the same pinpoint.  A range or an "&" always continues the
# list; a *comma* only does so after the plural marker, because "13 n.4, 25" is
# a note and then a second pin page.  Even after "nn.", a comma yields to a
# following note marker — "nn.4-5, 30 n.7" names page 30, not note 30.
_NOTE_JOIN_RE = re.compile(
    r"\s*(?:[-–—]|&)\s*(" + _NOTE_NUMBER + r")(?!\d)")
_NOTE_LIST_JOIN_RE = re.compile(
    r"\s*,\s*(" + _NOTE_NUMBER + r")(?!\d)"
    r"(?!\s*(?:&\s*)?n(?:n?\.|otes?\b))"
)
# A pin already encoded for an action: "13n4", "13n4,5", or a bare "13".
_ENCODED_NOTE_PIN_RE = re.compile(r"^(\d*)n(.+)$")

# The pinpoint page, matched where a note marker follows it.
# PINCITE_AFTER_RE ends in ``(?!\d|\s*[A-Z])`` — under its IGNORECASE flag that
# refuses a page followed by *any* letter, which is what keeps a parallel
# citation (", 510 A.2d 562") from reading as a pin page.  A note marker is a
# letter too, so the same page needs matching without that guard; the guard's
# job is then done by requiring an actual note to follow.
_NOTE_PINCITE_AFTER_RE = re.compile(
    r",\s*(?:at\s+)?\*?(\d{1,6})(?:\s*[-–—]\s*\*?\d{1,6})?(?!\d)",
    re.IGNORECASE,
)


def join_note_pin(page: str, notes: "list[str]") -> str:
    """Encode a footnote pin for a link action: page 13, note 4 → ``"13n4"``.

    The page is retained even though the note is what the link opens: separate
    writings in one report restart their notes at 1, so the page is what says
    *whose* note 4 is meant.  A note with no page ("Id. n.4") encodes as
    ``"n4"``."""
    notes = [str(n).strip() for n in notes if str(n).strip()]
    if not notes:
        return str(page or "")
    return f"{page or ''}n{','.join(notes)}"


def split_note_pin(pin: str) -> "tuple[str, list[str]]":
    """Decode :func:`join_note_pin` — ``"13n4"`` → ``("13", ["4"])``.  An
    ordinary page pin yields no notes."""
    pin = str(pin or "").strip()
    m = _ENCODED_NOTE_PIN_RE.match(pin)
    if not m:
        return pin, []
    return m.group(1), [n for n in m.group(2).split(",") if n]


def pin_display(pin: str) -> str:
    """A pin as a reader sees it: ``"13n4"`` → ``"13 n.4"``, ``"13n4,5"`` →
    ``"13 nn.4, 5"``.  Used in window titles and status lines."""
    page, notes = split_note_pin(pin)
    if not notes:
        return page
    marker = ("n." if len(notes) == 1 else "nn.") + ", ".join(notes)
    return f"{page} {marker}" if page else marker


def note_pin_after(text: str, pos: int) -> "tuple[list[str], int]":
    """The footnote pinpoint written at *pos*, as (note numbers, end)."""
    text = text or ""
    head = _NOTE_HEAD_RE.match(text, pos)
    if head is None:
        return [], pos
    plural = head.group(1).lower().rstrip(".") in ("nn", "notes")
    notes = [head.group(2)]
    end = head.end()
    while True:
        more = _NOTE_JOIN_RE.match(text, end)
        if more is None and plural:
            more = _NOTE_LIST_JOIN_RE.match(text, end)
        if more is None:
            return notes, end
        notes.append(more.group(1))
        end = more.end()


def pin_after(text: str, pos: int) -> "tuple[str, int]":
    """The whole pin cite written at *pos*: (encoded pin, end offset).

    Returns ``("", pos)`` when no pinpoint follows.  "…, 13 n.4" yields
    ``("13n4", <end past the note>)`` so the link covers the note number the
    reader sees and opens the note rather than the page carrying it."""
    noted = _NOTE_PINCITE_AFTER_RE.match(text or "", pos)
    if noted is not None:
        notes, end = note_pin_after(text, noted.end())
        if notes:
            return join_note_pin(noted.group(1), notes), end
    m = PINCITE_AFTER_RE.match(text or "", pos)
    if not m:
        return "", pos
    notes, end = note_pin_after(text, m.end())
    return join_note_pin(m.group(1), notes), end


# Citations recognized inside running text (made clickable → Scholar lookup).
# Pattern: volume, reporter abbreviation, page.
REPORTER_ALT = (
    r"(?:U\.\s?S\.(?!\s?C)|S\.\s?Ct\.|L\.\s?Ed\.(?:\s?2d)?|"
    r"F\.\s?Supp\.(?:\s?[23]d)?|F\.\s?(?:2d|3d|4th)|F\.\s?App[’']x|Fed\.\s?Appx\.|B\.R\.|"
    r"A\.(?:2d|3d)?|P\.(?:2d|3d)?|N\.E\.(?:2d|3d)?|N\.W\.(?:2d)?|S\.E\.(?:2d)?|"
    r"S\.W\.(?:2d|3d)?|So\.(?:\s?[23]d)?|Cal\.\s?Rptr\.(?:\s?[23]d)?|"
    r"N\.Y\.S\.(?:2d|3d)?|Ohio\s?St\.\s?(?:2d|3d)?|Ill\.\s?2d|Wis\.\s?2d|Wn\.\s?(?:2d|App\.))"
)
TEXT_CITE_RE = re.compile(r"\b\d{1,4}\s+" + REPORTER_ALT + r"\s+\d{1,5}\b")

# Some citators — and Google Scholar, for old state cases — drop a court /
# jurisdiction parenthetical between the reporter and the page:
# "5 Johns. (N.Y.) 37", "15 Johns. (N.Y.) 121".  Matched optionally and never
# captured, so the reporter/page groups stay clean; :func:`_case_match_text`
# strips it back out of the matched span so the normalized cite is "5 Johns.
# 37".  Requiring a letter-led parenthetical leaves a parallel-reporter form
# ("5 U.S. (1 Cranch) 137") untouched.
_COURT_PAREN = r"(?:\s*\([A-Za-z][A-Za-z.'’ ]{0,20}\))?"

# Capturing form (volume, reporter, page) — used to index every full citation
# in a document so short forms can be resolved back to it.
CITE_CAPTURE_RE = re.compile(
    r"\b(\d{1,4})\s+(" + REPORTER_ALT + r")" + _COURT_PAREN + r"\s+(\d{1,5})\b")

# Early-SCOTUS "nominative" reporters (Dallas through Otto, U.S. Reports 1-107).
# Old opinions cite them bare ("4 Wheat. 438", "1 Cranch 137") or with the
# parallel U.S. volume interpolated in brackets or parens between reporter and
# page — "4 Wheat. [17 U. S.] 438", "9 Wall. (76 U. S.) 136" — sometimes with
# an OCR hyphen glued to the page ("21 Wall. (88 U. S.)-597").  Modern text
# writes the orders swapped: "5 U.S. (1 Cranch) 137".  All three shapes are
# detected here; case_match_text() folds each down to the plain two-part cite
# the resolvers know ("4 Wheat. 438", "5 U.S. 137").  Case-sensitive on
# purpose: the capitalized reporter next to digits keeps prose "how", "black",
# "wall" from matching.
_NOM_SCOTUS_ALT = (r"(?:Dall(?:as)?|Cranch|Wheat(?:on)?|Pet(?:ers)?|"
                   r"How(?:ard)?|Black|Wall(?:ace)?|Otto)")
# The possessive guard keeps "1 Peters' Rep. 233" — Peters' *District Court*
# reports — from reading as 1 Pet. 233; the optional "Rep." absorbs the old
# spelled-out style "10 Wheat. Rep. 472" (case_match_text drops it).
NOMINATIVE_CITE_RE = re.compile(
    r"\b(\d{1,2})\s+(" + _NOM_SCOTUS_ALT
    + r"\.?)(?!['’])(?:\s+Rep\.)?\s+(\d{1,4})\b")
NOMINATIVE_PARALLEL_RE = re.compile(
    r"\b(\d{1,2})\s+(" + _NOM_SCOTUS_ALT + r"\.?)\s*"
    r"[\[(]\s*\d{1,3}\s+U\.\s?S\.\s*[\])]\s*[-–—]?\s*(\d{1,5})\b")
US_NOMINATIVE_PARALLEL_RE = re.compile(
    r"\b(\d{1,3})\s+(U\.\s?S\.)\s*[\[(]\s*\d{1,2}\s+" + _NOM_SCOTUS_ALT +
    r"\.?\s*[\])]\s*[-–—]?\s*(\d{1,5})\b")

# Nominative reports folded into a state's official series.  The early reports
# of several states were published under their reporters' names and later
# renumbered as volumes of the state's own Reports — the same books and pages,
# the volume offset by those before them — so "19 Pick. 234" is 36 Mass. 234
# and the Bluebook writes "45 Mass. (4 Met.) 111".  CourtListener and
# static.case.law mostly file these cases under the official volume.
#
# Each entry: the reporter's names as written (abbreviation first, then any
# other spelling in use), the official series, the offset, and how many
# volumes the reporter ran to.  Bluebook table T1.  An abbreviation used by two
# states ("Met." for Metcalf in Massachusetts and in Kentucky; "Sneed" in
# Kentucky and Tennessee) maps to both, and the lookup lets the case name
# settle which is meant.
_STATE_NOMINATIVE_SERIES: tuple[tuple[tuple[str, ...], str, int, int], ...] = (
    # Massachusetts: 2–96 Mass.
    (("Tyng",), "Mass.", 1, 16),
    (("Pick.", "Pickering"), "Mass.", 17, 24),
    (("Met.", "Metc.", "Metcalf"), "Mass.", 41, 13),
    (("Cush.", "Cushing"), "Mass.", 54, 12),
    (("Gray",), "Mass.", 66, 16),
    (("Allen",), "Mass.", 82, 14),
    # Illinois: 1–10 Ill.
    (("Breese",), "Ill.", 0, 1),
    (("Scam.", "Scammon"), "Ill.", 1, 4),
    (("Gilm.", "Gilman"), "Ill.", 5, 5),
    # Kentucky: 2–77 Ky.
    (("Sneed",), "Ky.", 1, 1),
    (("Hard.", "Hardin"), "Ky.", 2, 1),
    (("Bibb",), "Ky.", 3, 4),
    (("A.K. Marsh.", "A.K. Marshall"), "Ky.", 7, 3),
    (("Litt. Sel. Cas.",), "Ky.", 15, 1),
    (("Litt.", "Littell"), "Ky.", 10, 5),
    (("T.B. Mon.", "T.B. Monroe"), "Ky.", 16, 7),
    (("J.J. Marsh.", "J.J. Marshall"), "Ky.", 23, 7),
    (("Dana",), "Ky.", 30, 9),
    # "B. Munroe" is how Dred Scott v. Sandford spells Ben Monroe.
    (("B. Mon.", "B. Monroe", "Ben Monroe", "B. Munroe"), "Ky.", 39, 18),
    (("Met.", "Metc.", "Metcalf"), "Ky.", 57, 4),
    (("Duv.", "Duvall"), "Ky.", 61, 2),
    (("Bush",), "Ky.", 63, 14),
    # Tennessee: 1–84 Tenn.
    (("Overt.", "Overton"), "Tenn.", 0, 2),
    (("Cooke",), "Tenn.", 2, 1),
    (("Hayw.", "Haywood"), "Tenn.", 3, 3),
    (("Mart. & Yer.",), "Tenn.", 7, 1),
    (("Yer.", "Yerger"), "Tenn.", 8, 10),
    (("Meigs",), "Tenn.", 18, 1),
    (("Hum.", "Humph.", "Humphreys"), "Tenn.", 19, 11),
    (("Swan",), "Tenn.", 30, 2),
    (("Sneed",), "Tenn.", 32, 5),
    (("Head",), "Tenn.", 37, 3),
    (("Coldw.", "Coldwell"), "Tenn.", 40, 7),
    (("Heisk.", "Heiskell"), "Tenn.", 47, 12),
    (("Baxt.", "Baxter"), "Tenn.", 59, 9),
    (("Lea",), "Tenn.", 68, 16),
    # Virginia: 3–74 Va.
    (("Va. Cas.", "Virg. Cas."), "Va.", 2, 2),
    (("Call",), "Va.", 4, 6),
    (("Hen. & M.",), "Va.", 10, 4),
    (("Munf.", "Munford"), "Va.", 14, 6),
    (("Gilmer",), "Va.", 20, 1),
    (("Rand.", "Randolph"), "Va.", 21, 6),
    (("Leigh",), "Va.", 27, 12),
    (("Rob.",), "Va.", 39, 2),
    (("Gratt.", "Grattan"), "Va.", 41, 33),
    # Delaware: 1–59 Del.
    (("W.W. Harr.",), "Del.", 30, 9),
    (("Harr.", "Harrington"), "Del.", 0, 5),
    (("Houst.", "Houston"), "Del.", 5, 9),
    (("Marv.", "Marvel"), "Del.", 14, 2),
    (("Penne.", "Pennewill"), "Del.", 16, 7),
    (("Boyce",), "Del.", 23, 7),
    (("Terry",), "Del.", 39, 20),
    # Mississippi: 9–39 Miss.
    (("S. & M.", "Smedes & M."), "Miss.", 8, 14),
    (("Cushm.", "Cushman"), "Miss.", 22, 7),
    (("George",), "Miss.", 29, 10),
    # Pennsylvania: 1–50 Pa.
    (("Barr",), "Pa.", 0, 10),
    (("Jones",), "Pa.", 10, 2),
    (("Harris",), "Pa.", 12, 12),
    (("Casey",), "Pa.", 24, 12),
    (("Wright",), "Pa.", 36, 14),
    # New York Court of Appeals: 1–14 N.Y.
    (("Comst.", "Comstock"), "N.Y.", 0, 4),
    (("Seld.", "Selden"), "N.Y.", 4, 6),
    (("Kern.", "Kernan"), "N.Y.", 10, 4),
    # North Carolina: Iredell's Law 1–13 is 23–35 N.C., his Equity 1–8 is
    # 36–43 N.C. (static.case.law's own volumes carry both citations: 23 N.C.
    # opens with 1 Ired., 36 N.C. with 1 Ired. Eq.).
    (("Ired. Eq.", "Iredell Eq."), "N.C.", 35, 8),
    (("Ired.", "Iredell", "Ired. Law"), "N.C.", 22, 13),
    # Maine: Greenleaf's nine volumes (1820–1832) are 1–9 Me.
    (("Greenl.", "Greenleaf"), "Me.", 0, 9),
    # New Jersey: Dutcher 1–5 is 25–29 N.J.L. ("State v. Roe, 2 Dutcher,
    # 215" is 26 N.J.L. 215).
    (("Dutch.", "Dutcher"), "N.J.L.", 24, 5),
)


def _nominative_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


_STATE_NOMINATIVE: dict[str, list[tuple[str, int, int]]] = {}
for _names, _series, _offset, _volumes in _STATE_NOMINATIVE_SERIES:
    for _name in _names:
        _STATE_NOMINATIVE.setdefault(_nominative_key(_name), []).append(
            (_series, _offset, _volumes))


def _nominative_name_re(name: str) -> str:
    """A reporter name as a pattern: its periods optional, its spacing
    free ("A.K. Marsh." also reads "A. K. Marsh", "Hen. & M." "Hen.&M.")."""
    out = []
    for ch in name:
        if ch == ".":
            out.append(r"\.?\s*")
        elif ch == " ":
            out.append(r"\s*")
        else:
            out.append(re.escape(ch))
    return "".join(out)


# Longest names first, so "W.W. Harr." is not read as "Harr." and "Litt. Sel.
# Cas." not as "Litt.".
_STATE_NOM_ALT = "(?:" + "|".join(
    _nominative_name_re(n) for n in sorted(
        {n for names, *_rest in _STATE_NOMINATIVE_SERIES for n in names},
        key=len, reverse=True)) + ")"
# "19 Pick. 234", "1 Gray 1", "4 Met. 111".  Case-sensitive, digits on both
# sides; most of these names carry no period to mark them as reporters (Gray,
# Dana, Terry), so a match counts only when its volume is one the reporter
# actually published (see :func:`state_nominative_cites`).
STATE_NOMINATIVE_CITE_RE = re.compile(
    r"\b(\d{1,2})\s+(" + _STATE_NOM_ALT + r")(?!['’\w])\s*(\d{1,4})\b")
# The Bluebook's parallel form, "45 Mass. (4 Met.) 111", "61 Ky. (4 Met.) 1":
# read as the official cite it leads with.
STATE_NOMINATIVE_PARALLEL_RE = re.compile(
    r"\b(\d{1,3})\s+(Mass\.|Ill\.|Ky\.|Tenn\.|Va\.|Del\.|Miss\.|Pa\.|"
    r"N\.\s?Y\.|N\.\s?C\.|Me\.|N\.\s?J\.\s?L\.)\s*[\[(]\s*\d{1,2}\s+"
    + _STATE_NOM_ALT + r"\s*[\])]\s*[-–—]?\s*(\d{1,4})\b")


def state_nominative_cites(cite: str) -> list[str]:
    """A state nominative citation in its official-series form — "19 Pick.
    234" → ["36 Mass. 234"], "1 Sneed 5" → ["2 Ky. 5", "33 Tenn. 5"] — or []
    when *cite* names none of these reporters, or a volume past its run."""
    m = STATE_NOMINATIVE_CITE_RE.search(cite or "")
    if not m:
        return []
    vol, page = int(m.group(1)), m.group(3)
    return [f"{vol + offset} {series} {page}"
            for series, offset, volumes in _STATE_NOMINATIVE.get(
                _nominative_key(m.group(2)), ())
            if 1 <= vol <= volumes]


def mass_reports_cite(cite: str) -> str:
    """The Massachusetts Reports form of a Massachusetts nominative cite
    ("19 Pick. 234" → "36 Mass. 234"), or "" (see
    :func:`state_nominative_cites`)."""
    return next((c for c in state_nominative_cites(cite) if " Mass. " in c),
                "")


# Early lower-federal reporters, cited by the reporter's name in 19th-century
# opinions — "The Nestor, 1 Sumner, 73", "The Young Mechanic, 2 Curtis, 404",
# "The Amos D. Carver, 35 Fed. Rep. 665" — normalized to the abbreviation
# CourtListener indexes ("1 Sumn. 73", "2 Curt. 404", "35 F. 665"), which the
# ordinary citation-resolution path then opens.  Case-sensitive, digits on
# both sides, and "Fed." must be followed by the page (or "Rep." then the
# page), so "Fed. R. Civ. P. 56", "Fed. Reg." and "Fed. Cl." never match.
EARLY_FED_CITE_RE = re.compile(
    r"\b(\d{1,3})\s+(Sumner|Sumn\.|Curtis|Curt\.|Benedict|Ben\.|Lowell|"
    r"Low\.|Gallison|Gallis\.|Gall\.|Sprague|Story|Brock\.|Wall\.\s?Jr\.|"
    r"Fed\.(?:\s?Rep\.)?),?\s+(\d{1,5})\b")


def early_fed_cite_text(m: re.Match) -> str:
    """Normalized cite for an EARLY_FED_CITE_RE match ("2 Curtis, 72" ->
    "2 Curt. 72", "35 Fed. Rep. 665" -> "35 F. 665")."""
    rep = canonical_reporter(m.group(2))
    return f"{m.group(1)} {rep} {m.group(3)}"

# Briefs often cite official state reporters that are too numerous to list in
# REPORTER_ALT ("306 Md. 556", "100 Cal. 400", "515 Pa. 1").  This guarded
# fallback is intentionally broad but excludes statute/regulation abbreviations
# before they can become case links.
#
# No abbreviation runs two periods together.  A table of contents does, in the
# dot leaders that carry a heading across to its page: "SUMMARY OF THE ARGUMENT
# .... 2" over "ARGUMENT........ 6" reads as volume 2 of a reporter called
# "ARGUMENT........", page 6, unless the leader ends the word.
_REPORTER_TOKEN = r"(?:[A-Z](?:[A-Za-z0-9'’]|\.(?!\.))*|\d+d|\d+th)"
# Its words are joined by spaces — or by an ampersand, in the reporters named
# for two men: "3 H. & McH. 554", "10 Serg. & Rawle 240", "1 Woodb. & M. 401".
_REPORTER_JOIN = r"(?:\s+|\s*&\s*)"
BROAD_CITE_CAPTURE_RE = re.compile(
    r"\b(\d{1,4})\s+("
    + _REPORTER_TOKEN
    + r"(?:" + _REPORTER_JOIN
    + _REPORTER_TOKEN
    + r"){0,5}?)"
    + _COURT_PAREN
    + r"\s+(\d{1,6})(?=[\s,;.)(]|$)"
)

# Older opinions — the U.S. Reports among them, into the twentieth century —
# set a comma between the reporter and the page: "Commonwealth v. Pierce, 138
# Massachusetts, 165, 178" (Nash v. United States, 229 U.S. 373), "Marbury v.
# Madison, 1 Cranch, 137", "Ex parte Crouch, 112 U.S., 178".  The comma lets a
# count in prose take the same shape ("in 1990, 3 Bush, 5 Clinton
# appointees"), so _iter_case_cites reads this form only for a reporter known
# by name, where a citation's volume stands, and with no capitalized word
# following the page.
COMMA_CITE_CAPTURE_RE = re.compile(
    r"\b(\d{1,4})\s+("
    + _REPORTER_TOKEN
    + r"(?:" + _REPORTER_JOIN
    + _REPORTER_TOKEN
    + r"){0,5}?)"
    + _COURT_PAREN
    + r"\s*,\s*(\d{1,6})(?=[\s,;.:)(\]\-–—]|$)"
)

# A reporter citation entered on its own line (Spotlight, an edited citation,
# or a database query) can safely be more permissive than running-text
# detection.  This accepts official state reporters and unpunctuated aliases
# such as "81 Wash 2d 788"; callers scanning prose should use
# :func:`iter_case_citations` instead.
HAND_TYPED_CITE_RE = re.compile(
    r"(\d{1,4})\s+([A-Z][A-Za-z0-9.'’ &]{0,24}?)\s+"
    r"(\d{1,6})(?=[\s,;.)(]|$)"
)
_NONCASE_REPORTERS = {
    "usc", "usca", "uscs", "cfr", "fr", "fedr", "fedreg",
    # English Reports ("156 Eng. Rep. 145", "95 E.R. 807"): real case cites,
    # but ones Google Scholar / CourtListener / case.law cannot open — the
    # eng_rep pass links them to the CommonLII scan instead, so the broad case
    # regex must not claim them first (a Scholar lookup by an E.R. cite lands
    # on an unrelated case).
    "engrep", "er",
    # A *bare* "App." is the joint appendix, not a reporter — "2 App. 136" is
    # a page of the record, and linking it sends the reader to an unrelated
    # case (and gives a following "Id., at 137" the same wrong antecedent).
    # _RECORD_CITE_RE already treats the word that way.  Only the bare form is
    # excluded: the key drops punctuation and spacing, so the real reporters
    # that contain "App." keep their own keys — "Cal. App. 4th" is calapp4th,
    # "Wn. App." wnapp, "N.Y. App. Div." nyappdiv, "F. App'x" fappx.
    "app",
    # "Appl." is the application in a Supreme Court stay or emergency matter,
    # cited by its pages the way the joint appendix is ("Appl. 6, 31"), and
    # no reporter is abbreviated so.  With a footnote number set in front of it
    # — "…irreparable injury.⁴ Appl. 6, 31" — it reads as volume 4, page 6.
    "appl",
}
_PLAIN_CASE_REPORTERS = {
    "alaska", "idaho", "iowa", "ohio", "utah", "vermont", "wyoming",
    "maine", "hawaii",   # older spellings of "Me." and "Haw."
    "wl", "lexis",
}

# Law reviews are cited in exactly a reporter's shape — "125 Yale L. J. 946"
# parses like "125 U. S. 946" — so the broad reporter fallback claims them and
# offers the reader a case that was never decided.  These markers appear in
# journal abbreviations and in no case reporter:
#
#   "L. Rev."  Harv. L. Rev., Colum. L. Rev., N.Y.U. L. Rev.
#   "L. J."    Yale L. J., Duke L.J., Hastings L.J.
#   "L.Q."     Law Quarterly
#   "Rev."     Sup. Ct. Rev., Ann. Rev. — any remaining review
#   "J."       Am. J. Int'l L., J. Legal Stud. — a *standalone* J.
#
# The last one carries all the risk, because several real reporters end in a
# "J." that must survive: N.J. (New Jersey), M.J. (Military Justice), N.J.L.
# (New Jersey Law Reports), Wall. Jr.  What separates them from a journal is
# the character before the J — a period or letter there means reporter, a space
# or nothing means journal — so the standalone alternative is written with a
# lookbehind, and the "L. J." family is matched explicitly since its own J does
# follow a period.  A J. after an ampersand is the second man of a reporter
# named for two — Maryland's "Gill & J.", "Har. & J." — not a journal.
# Case-sensitive: journal abbreviations are capitalized, and the broad regex
# only ever hands us a capitalized token.
_JOURNAL_REPORTER_RE = re.compile(
    r"L\.\s?J\.|L\.\s?Rev\.|L\.\s?Q\.|\bRev\.|(?<![A-Za-z.&])(?<!&\s)J\."
)

# "10 Op. Atty Gen. 382" — an opinion of the Attorney General.  Cited in a
# reporter's shape, but it is executive advice, not a decided case, and no
# case-law source can open it.
_AG_OPINION_RE = re.compile(r"Att(?:orne)?y?\.?\s*'?y?\.?\s*Gen", re.IGNORECASE)

# Short-form citation: "Roe, 410 U.S., at 152" → volume, reporter, pin page.
SHORT_CITE_RE = re.compile(
    r"\b(\d{1,4})\s+(" + REPORTER_ALT + r")\s*,?\s+at\s+(\d{1,5})\b")
BROAD_SHORT_CITE_RE = re.compile(
    r"\b(\d{1,4})\s+("
    + _REPORTER_TOKEN
    + r"(?:" + _REPORTER_JOIN
    + _REPORTER_TOKEN
    + r"){0,5}?)\s*,?\s+at\s+\*?(\d{1,6})\b",
    re.IGNORECASE,
)

# "Id." short form — refers to the immediately preceding citation; group 1 is
# the optional pin page ("Id. at 152").  ("Ibid." is deliberately not traced —
# it usually points at a non-case source.)
ID_CITE_RE = re.compile(r"\bid\.(?:\s*,?\s*at\s+\*?(\d{1,6}))?", re.IGNORECASE)

# Record cites in briefs commonly use "Id." too.  If one appears between an
# authority and a later "Id. at N", do not carry the authority forward.  The
# suffix guard matters for bare "ER"/"SER": without it ordinary prose such as
# "error," "erred," and "errors" falsely breaks the chain.  "Appl." is a
# Supreme Court application, cited by its pages like the joint appendix.
_RECORD_CITE_RE = re.compile(
    r"\b(?:Appl?\.|J\.?A\.|A\.R\.|R\.|Tr\.|Dkt\.|Doc\.|ECF|Ex\.|ER|SER)"
    r"(?![A-Za-z])\s*(?:No\.?\s*)?[\w*.-]+"
    r"|\b(?:ECF|Dkt\.|Doc\.)\s+No\.?\s+\d+|¶\s*\d+",
    re.IGNORECASE,
)

# The Reporter of Decisions' running head, printed across the top of every
# other page of a slip opinion or a bound-volume excerpt: "Cite as: 583 U. S.
# 48 (2018)", or "Cite as: 609 U. S. ___ (2026)" before the volume is paged.
# It cites the very opinion being read, so linking it only offers the reader a
# trip back to where they already are — and, because the head sits between the
# text of one page and the next, letting it register as a citation would also
# hijack any "Id." that opens the following page.
RUNNING_HEAD_CITE_RE = re.compile(
    r"Cite\s+as\s*:\s*\d{1,4}\s+U\.\s?S\.\s+(?:\d{1,5}|_{2,})"
    r"(?:\s*\(\s*\d{4}\s*\))?",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# How much text a case-citation link should cover
# ---------------------------------------------------------------------------
# The reporter cite ("534 U. S. 266") is what the regexes find, but a reader
# sees one citation: "United States v. Arvizu, 534 U. S. 266, 277 (2002)".
# The span is grown to that whole unit — name, reporter cite, pin cite, and the
# court/year parenthetical — so the blue text matches the citation rather than
# a fragment of it.  Explanatory parentheticals ("(plurality opinion)",
# "(holding that …)") are left out: they are commentary, not the cite.

# A parenthetical belongs to the citation when it *ends* in a year — "(2002)",
# "(4th Cir. 2024)", "(D. Md. Apr. 30, 2015)".  Nothing else qualifies, which
# is what keeps "(holding that the totality controls)" black.
_COURT_YEAR_PAREN_RE = re.compile(r"\s*\((?:[^()]{0,60}?[\s.])?(?:1[6-9]|20)\d{2}\)")

# A further pin page in the same citation ("216, 225, 228" — the ", 228").
# Group 1 is the page it opens to; the range that may follow is highlighted but
# not captured, since a range opens at its first page.
_EXTRA_PIN_RE = re.compile(
    r",\s*\*?(\d{1,6})(?:\s*[-–—]\s*\*?\d{1,6})?(?!\d|\s*[A-Z])")

# The tail of a page range on a short cite.  SHORT_CITE_RE stops at the first
# page ("542 U. S., at 254" out of "at 254–255"), so the rest of the range has
# to be taken separately to finish the highlight.
_RANGE_TAIL_RE = re.compile(r"\s*[-–—]\s*\*?\d{1,6}(?!\d)")


def note_pin_after_page(text: str, pos: int) -> "tuple[list[str], int]":
    """The footnote pinpoint following a pin page already matched up to *pos*,
    as (note numbers, end offset).

    Used where the page match stops at the first page of a range — a short cite
    ("542 U. S., at 254–255 n.7") or an "Id., at 254–255 n.7" — so the range
    tail is stepped over before the note is read.  The end is past the range
    tail whether or not a note follows it, which is also what a short cite's
    highlight wants."""
    tail = _RANGE_TAIL_RE.match(text or "", pos)
    if tail:
        pos = tail.end()
    return note_pin_after(text, pos)


# Lowercase words that sit *inside* a case name and must not end the backward
# scan for one ("District of Columbia v. Wesby", "Rhode Island ex rel. …").
_NAME_CONNECTORS = frozenset({
    "of", "the", "and", "for", "et", "al", "al.", "ex", "rel.", "rel",
    # "Goodell v. Jackson ex dem. Smith" — the old form for a suit brought on
    # another's demise, and as much part of the name as "ex rel." is.
    "dem.", "dem",
    "de", "del", "des", "du", "da", "dos", "la", "le", "van", "von",
    "den", "der", "&", "on", "to",
})

# Words that can never begin a case name, so the scan stops before them even
# though many are capitalized at the start of a sentence or signal phrase.
_NAME_STOPPERS = frozenset({
    "see", "cf", "cf.", "accord", "compare", "contra", "citing", "quoting",
    "quoted", "e.g", "e.g.", "eg", "but", "also", "id", "id.", "ibid",
    "ibid.", "supra", "infra", "in", "at", "from", "under", "with", "than",
    "that", "since", "because", "overruled", "overruling", "rev'd", "aff'd",
    "cert", "cert.", "denied", "granted", "citation", "citations", "omitted",
    "quotation", "internal", "marks", "per", "curiam", "slip", "op", "op.",
    "no", "no.", "nos.", "v", "v.", "vs", "vs.",
})

# An abbreviation a name may legitimately contain ("Mgmt.", "Corp.", "U.S.",
# "R.R.", "E.").  Any *other* period-terminated word — "Amendment.",
# "reversed." — is the end of the previous sentence, and stops the scan.
_NAME_ABBREV_RE = re.compile(r"^(?:[A-Z][A-Za-z'’]{0,6}\.|(?:[A-Z]\.){1,4})$")

# Short words that pass for an abbreviation by their shape ("State." is a
# capital and four letters, like "Corp.") but are always spelled out in a case
# name, so a period after one ends a sentence: "…the decisions of this State.
# Garratt v. Dailey, 46 Wn.2d 197" names Garratt, not "State. Garratt".
_SPELLED_OUT_NAME_WORDS = frozenset("""
    state states people city town nation union government act law laws case
    cases rule rules code title clause statute section article record here
    there trial jury
""".split())


def _is_name_abbreviation(core: str) -> bool:
    """Whether a period-terminated word *core* ("Corp.", "U.S.") can sit
    inside a case name, rather than ending the sentence before it."""
    if not _NAME_ABBREV_RE.match(core):
        return False
    word = core.rstrip(".").replace("’", "'").lower()
    if "." in word:
        return True  # an initialism: "U.S.", "R.R."
    # A word the Bluebook tables abbreviate, written out in full ("Court.",
    # "County.", "Texas."), is not an abbreviation of anything.
    return (word not in _SPELLED_OUT_NAME_WORDS
            and word not in bluebook_names._WORD_MAP)

# Punctuation that can trail a word without being part of it.  Stripped before
# the sentence-end test, so a quotation's closing mark cannot hide the period
# that ends it — 'Amendment."' is still the end of a sentence.
_NAME_TRAIL = ",;:”\"’')]}"

# A token that can sit inside a party name: capitalized, quoted, or a company
# designation like "3M".  A bare number is not — that is a page number from a
# running head or the tail of an earlier citation.
_NAME_TOKEN_RE = re.compile(r"^[\"“'(]?(?:[A-Z]|\d+[A-Za-z])")

# How much text before a citation can hold its name.  Long enough for the
# worst real caption ("Liverpool, New York & Philadelphia S. S. Co. v.
# Commissioners of Emigration") with line breaks, short enough that the scan
# never reaches the running head of the page above.
_NAME_LOOKBEHIND = 200

# The docket number a citation may carry between the case name and the cite:
# ", No. A-61210-05T3", ", Nos. 12-6371, 12-6372", ", Civ. A. No. 96-3837".
_DOCKET_AFTER_NAME_RE = re.compile(
    r",\s*(?:Civ(?:il)?\.?\s*(?:A(?:ction)?\.?\s*)?|Case\s+)?Nos?\.\s*"
    r"[\w:().-]{2,30}(?:\s*(?:,|&|and)\s*[\w:().-]{2,30})*\s*$")

# "In re Winship", "Ex parte Young", "Matter of Doe" — a case name with no
# "v." in it, anchored to the end of the window before the citation.
_NAME_NO_V_RE = re.compile(
    r"(?:In\s+re|In\s+the\s+Matter\s+of|Ex\s+parte|Matter\s+of)\s+"
    r"[A-Z][\w.,'’&()-]*(?:\s+[\w.,'’&()-]+){0,6}$"
)


def _closes_parenthetical(tok: str) -> bool:
    """True when *tok* shuts a parenthetical that opened in an earlier word.

    A parenthetical between two citations belongs to the first one, not to the
    second one's name.  Casey's syllabus reads "…462 U. S. 416 (Akron I), and
    Thornburgh v. American College…"; without this the backward scan walks
    through "and" and "I)," and starts the Thornburgh name at "(Akron", so the
    two links run into each other on the page.

    A parenthetical that opens and closes inside one word — a name's own
    acronym, "…Gynecologists (ACOG), 476 U. S. 747" — is left alone.
    """
    core = tok.rstrip(",;:”\"’'")
    return core.endswith(")") and "(" not in core


def _mostly_italic(text: str, italic, lo: int, hi: int) -> bool:
    """True when the letters in ``text[lo:hi]`` are set in an italic face.

    A case name is italicised and the prose around a citation is not, which is
    what tells "Illinois v. Wardlow, 528 U. S. 119" from "by appointment of the
    Court, 551 U. S. 1186" — where "Court" is just the last word of a sentence
    that happens to be capitalised.  Judged on letters only (digits, spaces and
    punctuation carry no styling worth trusting) and by majority, so one roman
    glyph does not disqualify a name.
    """
    letters = [i for i in range(lo, min(hi, len(italic))) if text[i].isalpha()]
    if not letters:
        return False
    return sum(1 for i in letters if italic[i]) * 2 > len(letters)


def _set_in_roman(text: str, italic, lo: int, hi: int) -> bool:
    """True when ``text[lo:hi]`` has letters and they are not set in italics
    (see :func:`_mostly_italic`)."""
    return (any(ch.isalpha() for ch in text[lo:hi])
            and not _mostly_italic(text, italic, lo, hi))


def _past_connectors(toks: list, i: int) -> int:
    """The first of ``toks[i:]`` that is not a connector — where a name can
    start ("of Columbia v. Wesby" starts at "Columbia")."""
    while (i < len(toks)
           and toks[i].group(0).lower().strip(",;:") in _NAME_CONNECTORS):
        i += 1
    return i


# The last word of a line that heads a division of a table of authorities —
# "Cases", "Federal Cases", "Constitutional Provisions", "Other Authorities".
_HEADING_WORDS = frozenset("""
    cases authorities statutes provisions rules regulations materials sources
    treatises articles miscellaneous page(s) pages
""".split())


def _heading_line(line: str) -> bool:
    """Whether a line of text is a heading rather than prose: set in capitals
    throughout ("TABLE OF AUTHORITIES", "ARGUMENT"), or naming a division of
    a table of authorities ("Federal Cases")."""
    letters = [ch for ch in line if ch.isalpha()]
    words = line.split()
    return bool(words) and (
        (len(letters) > 1 and all(ch.isupper() for ch in letters))
        or words[-1].lower() in _HEADING_WORDS)


def _past_headings(text: str, base: int, head: str, toks: list, i: int) -> int:
    """Where a name found starting at ``toks[i]`` really starts, once any whole
    heading line the backward scan read into it is dropped.

    A table of authorities sets each entry on its own line under its
    division's heading, and a heading is capitalized word by word, so the scan
    back from the "v." of the first entry reads straight through it: "TABLE OF
    AUTHORITIES / Federal Cases / Barnes v. E-Systems".  A name's first words
    can stand on the line above its "v." — prose wraps — but a heading there
    stands on a line of its own, which is what gives it away: the name would
    begin at the very start of an earlier line, and that line reads as a
    heading (see :func:`_heading_line`).  ``text[base:]`` begins with *head*,
    the window the tokens were read from.
    """
    last = toks[-1].start()
    while i < len(toks):
        line_end = head.find("\n", toks[i].start())
        if line_end < 0 or line_end > last:
            break           # on the name's own line
        line_start = text.rfind("\n", 0, base + toks[i].start()) + 1
        if text[line_start:base + toks[i].start()].strip():
            break           # starts partway along the line: prose wrapping
        if not _heading_line(head[toks[i].start():line_end]):
            break
        i = _past_connectors(
            toks, next(k for k in range(i, len(toks))
                       if toks[k].start() > line_end))
    return i


def _case_name_start(
    text: str, cite_start: int, floor: int, italic=None) -> "int | None":
    """Index where the case name introducing the citation at *cite_start*
    begins, or ``None`` when no name reads as one.

    The scan never crosses *floor* — the end of the last thing already linked,
    or of a running head — so a name can never swallow a neighbouring citation
    or the page furniture above it.

    Both shapes of name count.  Most carry a "v." (or read "In re …"), but a
    string cite shortens the name while keeping the full citation — "Abdul
    Latif, 939 F. 3d 710", "National Broadcasting Co., 165 F. 3d 184" — so a
    lone party is accepted too.  What keeps that from swallowing prose is the
    same set of limits either way: the nearest comma bounds it, signal words
    and sentence ends stop it, and it may not run past a few words.
    """
    # Only the text just before the citation can hold its name.  Bounding the
    # window matters as much as the token rules: unbounded, the scan reaches
    # into the page above and finds the "v." of the running head
    # ("DISTRICT OF COLUMBIA v. WESBY"), which reads as a party name and
    # swallows everything after it.
    base = max(floor, cite_start - _NAME_LOOKBEHIND)
    head = text[base:cite_start]
    # Bluebook always separates the name from the volume with a comma.
    tail = re.search(r",\s*$", head)
    if not tail:
        return None
    head = head[:tail.start()]
    # A docket number set between the name and the citation belongs to the
    # citation — "Foxtons, Inc. v. Cirri Germain Realty, No. A-61210-05T3,
    # 2008 WL 465653" — and the name is read from before it, not taken to be
    # the number.
    docket = _DOCKET_AFTER_NAME_RE.search(head)
    if docket:
        head = head[:docket.start()]
    if not head.strip():
        return None

    nov = _NAME_NO_V_RE.search(head)
    if nov:
        if italic is not None and not _mostly_italic(
                text, italic, base + nov.start(), cite_start):
            return None
        return base + nov.start()

    # The last "v." in the window opens the second party; everything after it
    # (up to the comma) is that party, everything before it the first.
    left_end = None
    for split in reversed(list(re.finditer(r"(?<=[\w.'’)\]])\s+v[s]?\.\s+", head))):
        right = head[split.end():]
        if right and len(right) <= 70 and all(_name_token_ok(t) for t in right.split()):
            left_end = split.start()
        break
    # No "v." immediately before the cite: read the shortened one-party form.
    lone = left_end is None
    if lone:
        left_end = len(head)
    toks = list(re.finditer(r"\S+", head[:left_end]))
    i = len(toks)
    while i > 0:
        tok = toks[i - 1].group(0)
        low = tok.lower().strip(",;:")
        # A signal opening a parenthetical is capitalized as often as not —
        # "(Citing Hollingsworth v. Perry, …)" — and is no more a name for it.
        if low in _NAME_STOPPERS or low.lstrip("([\"“‘'") in _NAME_STOPPERS:
            break
        if lone and i < len(toks) and tok.endswith(","):
            # With no "v." to anchor it, the party is whatever follows the
            # nearest comma — "Later, Carpenter, 585 U. S., at 312" cites
            # Carpenter, not "Later, Carpenter".
            break
        if _closes_parenthetical(tok):
            break
        if _NAME_TOKEN_RE.match(tok):
            # A period ends the previous sentence unless it is an abbreviation
            # a name could contain.
            core = tok.rstrip(_NAME_TRAIL)
            if core.endswith(".") and not _is_name_abbreviation(core):
                break
            i -= 1
            continue
        if low in _NAME_CONNECTORS:
            i -= 1
            continue
        break
    # A name never *starts* with a connector ("of Columbia v. Wesby").
    i = _past_connectors(toks, i)
    i = _past_headings(text, base, head, toks, i)
    if italic is not None:
        # With the type to read, the name is what is set in italics, and what
        # the scan took in ahead of it in roman — a heading, a signal — is
        # not: under "TABLE OF AUTHORITIES / Federal Cases" the entry
        # "Barnes v. E-Systems" names Barnes.  Only leading words go, and
        # only while some of the party is left to start the name.
        k = i
        while k < len(toks) and _set_in_roman(
                text, italic, base + toks[k].start(), base + toks[k].end()):
            k += 1
        if k < len(toks):
            i = _past_connectors(toks, k)
    if i >= len(toks):
        return None
    if lone and len(toks) - i > 4:
        return None  # a lone party is one or two words, not half a sentence
    start = toks[i].start()
    if head[start] in "([":
        start += 1  # "(Hollingsworth v. Perry, …)": the name is inside
    if left_end - start > 90:
        return None  # implausibly long for a case name — leave it alone
    if italic is not None and not _mostly_italic(
            text, italic, base + start, cite_start):
        return None  # roman type: prose running up to the cite, not a name
    return base + start


# ---------------------------------------------------------------------------
# Statutes and regulations cited several sections at a time
# ---------------------------------------------------------------------------
# "18 U. S. C. §§ 1505, 1512, 1519" is three citations sharing one title, and
# the section-symbol regexes stop at the first.  The rest are picked up here
# and linked individually, each inheriting the title it was written under.
# A *range* ("§§ 1505-1515") is deliberately not split: the whole range reads
# as one citation and is highlighted as one, and the section group already
# carries the dash, which load_section resolves to the opening provision.

# ", 1512" / ", § 1512(b)" / ", 1614.106" — a bare section following the first,
# optionally repeating the section symbol, with its own subsections.
_MORE_SECTIONS_RE = re.compile(
    r"(,\s*(?:and\s+)?|\s+and\s+)(?:§+\s*)?"
    r"(\d+(?:\.\d+)?[a-zA-Z0-9]*(?:[-–—]\d+(?:\.\d+)?[a-zA-Z0-9]*)?)"
    r"((?:\s?\((?:\d{1,3}|[ivxIVX]{2,4}|[a-zA-Z]{1,3})\))*)"
)


def _sibling_section_cites(
    text: str, m: re.Match, dotted: bool,
) -> list[tuple[int, int, str]]:
    """Extra ``(start, end, spec)`` links for the sections listed after the
    first in a multi-section citation.

    *m* is a ``USC_CITE_RE``/``CFR_CITE_RE`` match and *dotted* says whether
    that source numbers its sections with a dot (C.F.R. "1614.105") — the test
    that keeps a plain pin cite or a date from being read as another section.
    """
    title = m.group(1)
    plural = "§§" in m.group(0) or re.search(r"[Ss]ec(?:tions|s)\b", m.group(0))
    out: list[tuple[int, int, str]] = []
    pos = m.end()
    while True:
        nxt = _MORE_SECTIONS_RE.match(text, pos)
        if not nxt:
            break
        section = nxt.group(2)
        # C.F.R. sections always carry a part.section dot; a U.S.C. section
        # never does.  Anything shaped the other way is a different animal —
        # a pin cite, a year, a subsection — and ends the list.
        if dotted != ("." in section):
            break
        # Without a plural section symbol ("§ 1505, 1512"), a following bare
        # number is much more likely a pin cite or a date than a second
        # section, so only an explicitly repeated "§" is trusted.
        if not plural and "§" not in nxt.group(0):
            break
        subs = re.findall(r"\(([^)]+)\)", nxt.group(3) or "")
        spec = (f"{title}:{section.replace('–', '-').replace('—', '-')}:"
                f"{','.join(subs)}")
        # The link covers the section itself, not the comma introducing it.
        out.append((nxt.start() + len(nxt.group(1)), nxt.end(), spec))
        pos = nxt.end()
    return out


def _name_token_ok(tok: str) -> bool:
    low = tok.lower().strip(",;:")
    if low in _NAME_STOPPERS:
        return False
    return bool(_NAME_TOKEN_RE.match(tok)) or low in _NAME_CONNECTORS


def _case_cite_spans(
    text: str, start: int, end: int, floor: int, *, short: bool = False,
    italic=None, with_name: bool = True,
) -> "list[tuple[int, int, str]]":
    """The links the citation at ``(start, end)`` should produce, as
    ``(span_start, span_end, pin)`` in document order.

    A citation to one page is one link covering the whole thing a reader sees:
    case name, reporter cite, pin cite, court/year parenthetical.  A citation to
    *several* pages — "5 F. 4th 216, 225, 228 (2021)" — is that many links: the
    name and first pin page open page 225, and each later page opens itself, so
    following a pin cite lands where the opinion actually pointed.

    ``pin`` is empty on the first segment, whose page the caller has already
    worked into the citation it built (a short cite resolves its own pin through
    the document index); later segments carry the page to open.

    ``with_name=False`` skips the backward scan for a case name — an "Id." has
    none, but its pages want splitting just the same ("id., at 675, 681–683,
    693" is three).  ``short=True`` says the match already ends at its first
    page, so only the tail of a range remains to be taken.

    A footnote pinpoint riding on any of those pages ("225 n.4") is taken into
    that page's segment and encoded into its pin, so the whole citation stays
    one blue run and following it opens the note.
    """
    if with_name:
        name_start = _case_name_start(text, start, floor, italic)
        if name_start is not None:
            start = name_start
    if short:
        # SHORT_CITE_RE ends at the first page of a range; take the rest so the
        # whole range is highlighted.  It still opens at that first page.
        _notes, end = note_pin_after_page(text, end)
    else:
        pin, pin_end = pin_after(text, end)
        if pin:
            end = pin_end
    segments: list[list] = [[start, end, ""]]
    while True:
        extra = _EXTRA_PIN_RE.match(text, segments[-1][1])
        if not extra:
            break
        notes, extra_end = note_pin_after(text, extra.end())
        # The comma joins the new segment so the blue runs unbroken.
        segments.append(
            [extra.start(), extra_end, join_note_pin(extra.group(1), notes)]
        )
    # The court/year parenthetical closes the citation, so it belongs to
    # whichever segment it follows.
    paren = _COURT_YEAR_PAREN_RE.match(text, segments[-1][1])
    if paren:
        segments[-1][1] = paren.end()
    return [(s, e, pin) for s, e, pin in segments]


def norm_reporter(rep: str) -> str:
    """Legacy reporter key, ignoring spacing/case (``U. S.`` == ``U.S.``).

    This punctuation-preserving form is kept for compatibility with saved
    citation-override keys and older local opinion indexes.  New comparisons
    should normally use :func:`canonical_norm_reporter` or
    :func:`reporter_key`.
    """
    return re.sub(r"\s+", "", (rep or "").replace("’", "'")).lower()


def _loose_reporter_key(rep: str) -> str:
    # Lowercase *before* stripping: the character class is lowercase-only, so
    # stripping first would delete every capital letter ("Eng. Rep." → "ngep")
    # and no key would ever match the reporter sets below.
    return re.sub(r"[^a-z0-9]+", "", (rep or "").lower())


@dataclass(frozen=True)
class _ReporterFamily:
    """Spellings that denote one reporter series.

    ``aliases`` contains every form we accept as the same reporter, including
    OCR/spacing forms retained by old local indexes.  ``search_forms`` is the
    smaller useful set sent to external search services.  ``case_law_slug`` is
    present only when static.case.law uses a non-mechanical canonical slug.
    """

    canonical: str
    aliases: tuple[str, ...] = ()
    search_forms: tuple[str, ...] = ()
    case_law_slug: str = ""


# Reporter identity belongs here rather than in individual resolvers.  These
# are true same-volume/same-page aliases; parallel reporters (for example a
# nominative Supreme Court cite and its U.S. Reports cite) remain a resolver
# concern because their volume numbers differ.
_REPORTER_FAMILIES = (
    _ReporterFamily(
        "F.", ("Fed.", "Fed. Rep."),
        ("F.", "Fed. Rep."), "f",
    ),
    _ReporterFamily(
        "F.2d", ("F. 2d", "Fed. Rep. 2d", "Fed. Rep.2d"),
        ("F.2d", "Fed. Rep. 2d"), "f2d",
    ),
    _ReporterFamily(
        "F.3d", ("F. 3d", "Fed. Rep. 3d", "Fed. Rep.3d"),
        ("F.3d", "Fed. Rep. 3d"), "f3d",
    ),
    _ReporterFamily(
        "F.4th", ("F. 4th", "Fed. Rep. 4th", "Fed. Rep.4th"),
        ("F.4th", "Fed. Rep. 4th"), "f4th",
    ),
    _ReporterFamily(
        "F. App'x",
        (
            "F.App'x", "F. Appx.", "F.Appx.",
            "Fed. App'x", "Fed.App'x", "Fed. Appx.", "Fed.Appx.",
        ),
        ("F. App'x", "Fed. Appx."),
        "f-appx",
    ),
    _ReporterFamily(
        "F. Cas.", ("Fed. Cas.",),
        ("F. Cas.", "Fed. Cas."), "f-cas",
    ),
    _ReporterFamily(
        "Wash.", ("Wn.", "Wash", "Wn"),
        ("Wash.", "Wn."), "wash",
    ),
    _ReporterFamily(
        "Wash. 2d", ("Wn. 2d", "Wash 2d", "Wn 2d", "Wash.2d", "Wn.2d"),
        ("Wash. 2d", "Wn. 2d"), "wash-2d",
    ),
    _ReporterFamily(
        "Wash. App.",
        ("Wn. App.", "Wash App", "Wn App", "Wash.App.", "Wn.App."),
        ("Wash. App.", "Wn. App."), "wash-app",
    ),
    # One series, more than one way of writing it: the forms courts and
    # older opinions used beside the Bluebook's (which is ``canonical``, and is
    # what the Bluebook line prints).  The static.case.law slug is the
    # canonical one's, so an older spelling still finds CAP's folder.
    _ReporterFamily("Or.", ("Ore.",), ("Or.", "Ore."), "or"),
    _ReporterFamily("Me.", ("Maine",), ("Me.", "Maine"), "me"),
    _ReporterFamily(
        "Haw.", ("Hawaii", "Hawai'i", "Hawai‘i", "Hawai’i"),
        ("Haw.", "Hawaii"), "haw"),
    _ReporterFamily("Kan.", ("Kans.",), ("Kan.", "Kans."), "kan"),
    _ReporterFamily("Neb.", ("Nebr.",), ("Neb.", "Nebr."), "neb"),
    _ReporterFamily("Okla.", ("Okl.",), ("Okla.", "Okl."), "okla"),
    _ReporterFamily(
        "Okla. Crim.", ("Okl. Cr.", "Okla. Cr.", "Okl. Crim."),
        ("Okla. Crim.", "Okl. Cr."), "okla-crim"),
    _ReporterFamily(
        "Tex. Crim.", ("Tex. Cr. R.", "Tex. Cr.", "Tex. Crim. App.",
                       "Tex. Cr. App."),
        ("Tex. Crim.", "Tex. Cr. R."), "tex-crim"),
    _ReporterFamily(
        "Pa. Commw.", ("Pa. Cmwlth.", "Pa. Commw. Ct.", "Pa. Commonwealth"),
        ("Pa. Commw.", "Pa. Cmwlth."), "pa-commw"),
    _ReporterFamily(
        "Mass. App. Ct.", ("Mass. App.",),
        ("Mass. App. Ct.", "Mass. App."), "mass-app-ct"),
    _ReporterFamily("F. Supp.", ("Fed. Supp.",), ("F. Supp.",), "f-supp"),
    _ReporterFamily("S. Ct.", ("Sup. Ct.",), ("S. Ct.",), "s-ct"),
    _ReporterFamily("L. Ed.", ("Law. Ed.",), ("L. Ed.",), "l-ed"),
    _ReporterFamily("N.Y.S.", ("N.Y. Supp.",), ("N.Y.S.", "N.Y. Supp."),
                    "nys"),
    # The first series of the Appellate Division Reports (1896–1955) is
    # "App. Div." in the Bluebook; courts and CAP write it "A.D.".
    _ReporterFamily("App. Div.", ("A.D.",), ("App. Div.", "A.D."), "ad"),
    # Early New York reporters, written out in full in old opinions.
    _ReporterFamily("Johns.", ("Johnson",), ("Johns.",), "johns"),
    _ReporterFamily("Cow.", ("Cowen",), ("Cow.",), "cow"),
    _ReporterFamily("Wend.", ("Wendell",), ("Wend.",), "wend"),
    _ReporterFamily("Barb.", ("Barbour",), ("Barb.",), "barb"),
    _ReporterFamily(
        "Johns. Ch.",
        (
            "John. Ch.", "John. Chan.", "Johns. Chan.",
            "John Ch", "John Chan", "Johns Ch", "Johns Chan",
        ),
        ("Johns. Ch.", "John. Chan.", "Johns. Chan."),
        "johns-ch",
    ),
    # Spelled-out nineteenth-century lower-federal reporters are normalized
    # by the link detector, but declaring them here also makes database,
    # override, and direct-search identity bidirectional.
    _ReporterFamily("Sumn.", ("Sumner",), ("Sumn.", "Sumner")),
    _ReporterFamily("Curt.", ("Curtis",), ("Curt.", "Curtis")),
    _ReporterFamily("Ben.", ("Benedict",), ("Ben.", "Benedict")),
    _ReporterFamily("Low.", ("Lowell",), ("Low.", "Lowell")),
    _ReporterFamily(
        "Gall.", ("Gallis.", "Gallison"),
        ("Gall.", "Gallison"),
    ),
    # The other U.S. circuit and district reporters of the nineteenth
    # century, reprinted in Federal Cases, by the name and abbreviations the
    # Free Law Project's reporters database records for each.  CourtListener
    # files each case under its Federal Cases citation *and* this one, so the
    # abbreviation finds it ("5 Sawy. 155" is In re Ah Yup, 1 F. Cas. 223);
    # the name as old opinions print it ("5 Sawyer, 155") finds nothing until
    # it is abbreviated.
    _ReporterFamily("Story", (), ("Story",)),
    _ReporterFamily("Sprague", ("Spr.",), ("Sprague",)),
    _ReporterFamily("Sawy.", ("Sawyer", "Saw."), ("Sawy.",)),
    _ReporterFamily("Dill.", ("Dillon",), ("Dill.",)),
    _ReporterFamily("McLean", ("Mc Lean",), ("McLean",)),
    _ReporterFamily("Woods", ("Woods C.C.",), ("Woods",)),
    _ReporterFamily("Biss.", ("Bissell",), ("Biss.",)),
    _ReporterFamily("Blatchf.", ("Blatchford",), ("Blatchf.",)),
    _ReporterFamily("Cliff.", ("Clifford",), ("Cliff.",)),
    _ReporterFamily("Flip.", ("Flippin", "Flipp."), ("Flip.",)),
    _ReporterFamily("Woolw.", ("Woolworth",), ("Woolw.",)),
    _ReporterFamily("Mason", ("Mason C.C.",), ("Mason",)),
    _ReporterFamily("Paine", ("Paine C.C.",), ("Paine",)),
    _ReporterFamily("Bond", (), ("Bond",)),
    _ReporterFamily(
        "Woodb. & M.",
        ("Woodbury & Minot", "Wood. & Minot", "Wood. & M.", "Woodb. & Min."),
        ("Woodb. & M.",)),
    # CourtListener knows it only spaced: "4 Wash. C. C. 371" (Corfield v.
    # Coryell) resolves, "4 Wash. C.C. 371" is refused.
    _ReporterFamily(
        "Wash. C.C.",
        ("Wash. C. C.", "Washington C.C.", "Washington Circuit Court"),
        ("Wash. C. C.",)),
    _ReporterFamily(
        "Abb.",
        ("Abb. U.S.", "Abbott U.S.", "Abbott United States"),
        ("Abb.",)),
    # Nineteenth-century state reports published under their reporters'
    # names and never renumbered into an official series; static.case.law
    # files each under a folder of its own.
    _ReporterFamily("Paige Ch.", ("Paige", "Pai. Ch."), ("Paige Ch.",),
                    "paige-ch"),
    _ReporterFamily("Denio", (), ("Denio",), "denio"),
    _ReporterFamily("Keyes", (), ("Keyes",), "keyes"),
    _ReporterFamily("Cai.", ("Caines", "Cai. R."), ("Cai.",), "cai"),
    _ReporterFamily("E.D. Smith", ("E. D. Smith",), ("E.D. Smith",),
                    "ed-smith"),
    _ReporterFamily(
        "H. & McH.", ("Harris & McHenry", "Har. & McH."), ("H. & McH.",),
        "h-mch"),
    _ReporterFamily(
        "Serg. & Rawle", ("Sergeant & Rawle", "Serg. & R.", "Serg. & Rawl."),
        ("Serg. & Rawle",), "serg-rawl"),
    _ReporterFamily(
        "La. Ann.", ("Louisiana Annual", "La. An.", "Louis. Ann."),
        ("La. Ann.",), "la-ann"),
    _ReporterFamily("Phila.", ("Philadelphia",), ("Phila.",)),
    # Official reports under the names they went by before the Bluebook's:
    # the Pennsylvania State Reports as "Pa. St." or "Penn. State", the New
    # Jersey Law Reports as "N.J. Law", the Missouri Reports as Dred Scott
    # prints them ("15 Misso., 576").
    _ReporterFamily(
        "Pa.",
        ("Pa. St.", "Penn. St.", "Pa. State", "Penn. State",
         "Pennsylvania State", "Penn."),
        ("Pa.",), "pa"),
    _ReporterFamily("N.J.L.", ("N.J. Law",), ("N.J.L.",), "njl"),
    _ReporterFamily("Mo.", ("Misso.",), ("Mo.",), "mo"),
)

_REPORTER_FAMILY_BY_KEY: dict[str, _ReporterFamily] = {}
for _family in _REPORTER_FAMILIES:
    for _form in (_family.canonical, *_family.aliases):
        _REPORTER_FAMILY_BY_KEY[_loose_reporter_key(_form)] = _family

# A state's official reports written with the state's name in full, as older
# opinions often cite them: "1 Massachusetts 15" is 1 Mass. 15, "12 North
# Carolina 30" is 12 N.C. 30.  Each name joins the family of its Bluebook
# abbreviation (Bluebook table T1) — the existing one where there is one
# (Wash., Or., Me., …) — so the lookup tries the abbreviation and the
# citation prints it.  Alaska, Idaho, Iowa, Ohio and Utah are cited by their
# full names already.
_STATE_REPORTER_NAMES = {
    "Alabama": "Ala.", "Arizona": "Ariz.", "Arkansas": "Ark.",
    "California": "Cal.", "Colorado": "Colo.", "Connecticut": "Conn.",
    "Delaware": "Del.", "Florida": "Fla.", "Georgia": "Ga.",
    "Hawaii": "Haw.", "Illinois": "Ill.", "Indiana": "Ind.",
    "Kansas": "Kan.", "Kentucky": "Ky.", "Louisiana": "La.",
    "Maine": "Me.", "Maryland": "Md.", "Massachusetts": "Mass.",
    "Michigan": "Mich.", "Minnesota": "Minn.", "Mississippi": "Miss.",
    "Missouri": "Mo.", "Montana": "Mont.", "Nebraska": "Neb.",
    "Nevada": "Nev.", "New Hampshire": "N.H.", "New Jersey": "N.J.",
    "New Mexico": "N.M.", "New York": "N.Y.", "North Carolina": "N.C.",
    "North Dakota": "N.D.", "Oklahoma": "Okla.", "Oregon": "Or.",
    "Pennsylvania": "Pa.", "Rhode Island": "R.I.",
    "South Carolina": "S.C.", "South Dakota": "S.D.",
    "Tennessee": "Tenn.", "Texas": "Tex.", "Vermont": "Vt.",
    "Virginia": "Va.", "Washington": "Wash.", "West Virginia": "W. Va.",
    "Wisconsin": "Wis.", "Wyoming": "Wyo.",
}
for _full, _abbr in _STATE_REPORTER_NAMES.items():
    _family = _REPORTER_FAMILY_BY_KEY.get(_loose_reporter_key(_abbr))
    if _family is None:
        # static.case.law's own slug rule: spaces become hyphens, other
        # punctuation goes ("N.C." → "nc", "W. Va." → "w-va").
        _family = _ReporterFamily(
            _abbr, (_full,), (_abbr,),
            re.sub(r"-+", "-", re.sub(
                r"[^a-z0-9-]", "", _abbr.lower().replace(" ", "-"))).strip("-"))
        _REPORTER_FAMILY_BY_KEY[_loose_reporter_key(_abbr)] = _family
    _REPORTER_FAMILY_BY_KEY[_loose_reporter_key(_full)] = _family

#: Reporters named by a word or two with no period to mark them — a state's
#: name in full ("northcarolina"), a reporter's own name ("sawyer", "paige",
#: "harrismchenry") — as reporter keys: accepted as a reporter in running
#: text, but only where a citation's volume stands (see _iter_case_cites) —
#: "about 3 Texas 12 counties" is prose.
_WORD_REPORTER_KEYS = frozenset(
    [_loose_reporter_key(name) for name in _STATE_REPORTER_NAMES]
    + [_loose_reporter_key(form) for family in _REPORTER_FAMILIES
       for form in (family.canonical, *family.aliases) if "." not in form])


def reporter_family(rep: str) -> "_ReporterFamily | None":
    """The known same-reporter family for *rep*, if any."""
    return _REPORTER_FAMILY_BY_KEY.get(_loose_reporter_key(rep))


def canonical_reporter(rep: str) -> str:
    """Preferred display spelling for *rep*, preserving unknown reporters."""
    family = reporter_family(rep)
    if family is not None:
        return family.canonical
    return re.sub(r"\s+", " ", (rep or "").replace("’", "'")).strip()


def canonical_norm_reporter(rep: str) -> str:
    """Punctuation-preserving canonical identity used by persistent data."""
    return norm_reporter(canonical_reporter(rep))


def reporter_key(rep: str) -> str:
    """Punctuation-free canonical reporter identity for loose comparisons."""
    return _loose_reporter_key(canonical_reporter(rep))


def reporter_variants(rep: str) -> tuple[str, ...]:
    """Useful external-search spellings for the reporter containing *rep*."""
    family = reporter_family(rep)
    if family is None:
        value = re.sub(r"\s+", " ", (rep or "").replace("’", "'")).strip()
        return (value,) if value else ()
    return family.search_forms or (family.canonical, *family.aliases)


def reporter_normalized_variants(rep: str) -> tuple[str, ...]:
    """Canonical and legacy normalized keys for querying an existing index."""
    family = reporter_family(rep)
    forms = (
        (family.canonical, *family.aliases)
        if family is not None else (rep,)
    )
    out: list[str] = []
    keys = [
        canonical_norm_reporter(rep),
        reporter_key(rep),
        *(norm_reporter(value) for value in (*forms, rep)),
    ]
    for key in keys:
        if key and key not in out:
            out.append(key)
    return tuple(out)


def case_law_reporter_slug(rep: str) -> str:
    """The canonical static.case.law slug for a known reporter family."""
    family = reporter_family(rep)
    return family.case_law_slug if family is not None else ""


# "Reports" written after a reporter's name, whole or cut short: "18 Pick. R.,
# 210", "10 Leigh Rep., 697", "15 Missouri Reports, 576".
_REPORTS_SUFFIX_RE = re.compile(r"\s+(?:R|Rep|Reps|Reports)\.?$")


def _reports_suffix_start(rep: str) -> "int | None":
    """Where a trailing "R." / "Rep." / "Reports" begins in *rep*, when the
    words before it name a reporter and the whole does not — "Pick. R." is
    Pickering, but "Fed. Rep." is a reporter of its own name — else None.

    A bare "R." after initials is part of them ("1 S. C. R. 30" is Canada's
    Supreme Court Reports), and after an edition it is a rule ("188 Ill. 2d
    R. 307" is Illinois Supreme Court Rule 307), so neither is dropped."""
    m = _REPORTS_SUFFIX_RE.search(rep or "")
    if m is None or reporter_family(rep) is not None:
        return None
    base = rep[:m.start()]
    if re.fullmatch(r"\s*R\.?", rep[m.start():]) and (
            re.fullmatch(r"(?:[A-Z]\.\s?){1,3}", base.strip())
            or re.search(r"\d(?:d|th)\.?$", base.strip())):
        return None
    known = (reporter_family(base) is not None
             or _NAMED_REPORTER_RE.fullmatch(base.strip())
             or _loose_reporter_key(base) in _WORD_REPORTER_KEYS
             or _STATE_NOMINATIVE.get(_nominative_key(base)))
    return m.start() if known else None


def reporter_without_suffix(rep: str) -> str:
    """*rep* without a "Reports" suffix it merely carries (see
    :func:`_reports_suffix_start`): "Missouri Reports" → "Missouri"."""
    start = _reports_suffix_start(rep)
    return rep[:start] if start is not None else rep


# A firm is written like a two-man reporter — "5 Smith & Co. 12" reads like
# "3 H. & McH. 554" — but ends with its trade.
_FIRM_RE = re.compile(r"&.*\b(?:Co|Cos|Inc|Corp|Ltd|Bros|Sons)\.?$")


def _valid_case_reporter(rep: str) -> bool:
    written = rep or ""
    rep = reporter_without_suffix(written)
    key = _loose_reporter_key(rep)
    if not key or key in _NONCASE_REPORTERS:
        return False
    if _JOURNAL_REPORTER_RE.search(rep):
        return False  # a law review, not a reporter — no case to open
    if _AG_OPINION_RE.search(rep):
        return False  # an Attorney General opinion, not a decided case
    if _FIRM_RE.search(rep):
        return False  # "Smith & Co.", a firm
    family = reporter_family(rep)
    if family is not None and family.canonical == "Johns. Ch.":
        return True
    if (key in _PLAIN_CASE_REPORTERS or key in _WORD_REPORTER_KEYS
            or key.endswith("lexis")):
        return True
    return "." in written   # "Gallis R.": the abbreviated "Reports" counts


def case_match_text(m: re.Match) -> str:
    """Normalized "vol reporter page" for a case-cite match: whitespace
    collapsed, and any interpolated parenthetical/bracketed matter dropped —
    the court/jurisdiction paren the reporter regexes tolerate ("5 Johns.
    (N.Y.) 37" -> "5 Johns. 37") and the parallel volume of an early-SCOTUS
    dual cite ("4 Wheat. [17 U. S.] 438" -> "4 Wheat. 438", "5 U.S. (1
    Cranch) 137" -> "5 U.S. 137"), plus the OCR hyphen sometimes glued to
    the page ("21 Wall. (88 U. S.)-597" -> "21 Wall. 597"), the comma
    older reports set before it ("138 Massachusetts, 165" -> "138
    Massachusetts 165"), and a "Reports" written after the reporter's name
    ("18 Pick. R., 210" -> "18 Pick. 210")."""
    if canonical_reporter(m.group(2)) == "Johns. Ch.":
        return f"{m.group(1)} Johns. Ch. {m.group(3)}"
    suffix = _reports_suffix_start(m.group(2))
    if suffix is not None:
        return re.sub(r"\s+", " ", " ".join(
            (m.group(1), m.group(2)[:suffix], m.group(3)))).strip()
    s = re.sub(r"\s+", " ", m.group(0)).replace("U. S.", "U.S.").replace("’", "'")
    s = re.sub(r"\s*[\[(][^\])]*[\])]\s*", " ", s)
    s = re.sub(r"\s[-–—]\s*(?=\d)", " ", s)
    s = re.sub(r"(?<=\.)\s+Rep\.(?=\s+\d)", "", s)  # "10 Wheat. Rep. 472"
    s = re.sub(r"\s*,\s*(?=\d+$)", " ", s)  # "138 Massachusetts, 165"
    return re.sub(r"\s+", " ", s).strip()


_case_match_text = case_match_text  # older internal name


def _at_citation_start(text: str, pos: int) -> bool:
    """Whether *pos* is where a citation's volume stands: the start of the
    text, or just after the comma closing a case name — or a semicolon, an
    opening parenthesis or bracket, or the year the English style writes
    first ("In re Ah Yup, (1878) 5 Sawyer, 155") — whitespace aside."""
    while pos > 0 and text[pos - 1].isspace():
        pos -= 1
    return (pos == 0 or text[pos - 1] in ",;(["
            or bool(_YEAR_FIRST_RE.search(text, max(0, pos - 8), pos)))


_YEAR_FIRST_RE = re.compile(r"\(\s*(?:1[5-9]|20)\d{2}\s*\)$")


# The reporters named in the running-text patterns: the national and federal
# series of REPORTER_ALT and the early Supreme Court reporters.
_NAMED_REPORTER_RE = re.compile(
    r"(?:" + REPORTER_ALT + r"|" + _NOM_SCOTUS_ALT + r"\.?)")
_CAPITALIZED_WORD_RE = re.compile(r"\s*[A-Z]")


def _comma_form_reporter(vol: str, rep: str, page: str) -> bool:
    """Whether *rep* is a reporter known by name, which the comma form of a
    citation ("138 Massachusetts, 165") requires: one the running-text
    patterns name, one with a family (each state's reports, by abbreviation
    or by the state's name in full, and the other spellings of a series), a
    state nominative in a volume it published ("5 Allen, 431"), or a state
    cited by its plain name ("12 Iowa, 44") — with or without a "Reports"
    after it ("15 Missouri Reports, 576")."""
    rep = re.sub(r"\s+", " ", reporter_without_suffix(rep)).strip()
    return bool(
        _NAMED_REPORTER_RE.fullmatch(rep)
        or reporter_family(rep) is not None
        or _loose_reporter_key(rep) in _PLAIN_CASE_REPORTERS
        or state_nominative_cites(f"{vol} {rep} {page}"))


def _bare_word_reporter(rep: str) -> bool:
    """Whether *rep*, as written, names its reporter by a word or two with
    no period ("Massachusetts", "Sawyer", "Harris & McHenry") — the form
    that must stand where a citation does to be read as one.  "Wash. 2d"
    is dotted, whatever its unpunctuated alias "Wash 2d" shares its key, and
    so is "4 Cowen R. 528": the abbreviated "Reports" marks it a citation."""
    return "." not in rep and (_loose_reporter_key(reporter_without_suffix(rep))
                               in _WORD_REPORTER_KEYS)


# A reporter's name that means more than one series in the volumes both ran
# to: "4 Washington, 380" in an 1850s opinion is Washington's Circuit Court
# Reports (four volumes, 1803–1827), not the state's Washington Reports
# (from 1889) that the name in full otherwise stands for, and its first two
# volumes are also Bushrod Washington's Virginia Reports.  Such a citation
# is left unread rather than sent to the wrong series.
_AMBIGUOUS_NAME_VOLUMES = {"washington": 4}


def _ambiguous_name(vol: str, rep: str) -> bool:
    """Whether *vol* *rep* is a volume :data:`_AMBIGUOUS_NAME_VOLUMES` says
    two series share."""
    last = _AMBIGUOUS_NAME_VOLUMES.get(
        _loose_reporter_key(reporter_without_suffix(rep)))
    return last is not None and int(vol) <= last


def _match_reporter_key(m: re.Match) -> str:
    """The reporter key of a case-cite match, a "Reports" suffix aside."""
    return reporter_key(reporter_without_suffix(m.group(2)))


def _index_first_pages(
    matches: "list[re.Match]",
) -> dict[tuple[str, str], list[int]]:
    """(volume, reporter key) → sorted first pages of *matches*."""
    idx: dict[tuple[str, str], set] = {}
    for m in matches:
        idx.setdefault((m.group(1), _match_reporter_key(m)),
                       set()).add(int(m.group(3)))
    return {k: sorted(v) for k, v in idx.items()}


def _pinned_without_at(
    text: str, m: re.Match, index: dict[tuple[str, str], list[int]],
) -> "int | None":
    """The first page of the case a comma-form match *m* is a pin into, its
    "at" left out — "Bryant, supra, at 361, 131 S.Ct., 1157", "Wilson, 501 U.
    S., 306-309" — or None when *m* cites a case in full.

    A pin is read only where *index* (see :func:`build_short_cite_index`)
    holds the volume cited in full a few pages before — within
    :data:`ID_PIN_WINDOW` — and no whole case name introduces *m*: a short
    form names one party, and "Jackson v. Ashton, (8 Peters, 148" is a case
    of its own whatever else that volume holds."""
    page = int(m.group(3))
    firsts = [first for first in (index or {}).get(
                  (m.group(1), _match_reporter_key(m)), ())
              if first < page <= first + ID_PIN_WINDOW]
    if not firsts:
        return None
    # The name ends at the comma before the volume, or before the parenthesis
    # the citation opens: "Jackson v. Ashton, (8 Peters, 148;)".
    pos = m.start()
    while pos > 0 and text[pos - 1].isspace():
        pos -= 1
    if pos > 0 and text[pos - 1] in "([":
        pos -= 1
    name_start = _case_name_start(text, pos, 0)
    if name_start is not None:
        name = re.sub(r"[\s,]+$", "", text[name_start:pos])
        if (_NAME_NO_V_RE.match(name)
                or re.search(r"(?<=[\w.'’)\]])\s+vs?\.\s+", name)):
            return None
    return max(firsts)


def _name_words(name: str) -> set:
    """The words of a case name that identify a party: "Trump v. Wilcox" →
    {"trump", "wilcox"}."""
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower())
            if w not in _NAME_CONNECTORS and w not in ("v", "vs")}


def _whole_caption(name: str) -> bool:
    """Whether *name* is a case's whole caption — "Trump v. Wilcox", "In re
    Winship" — rather than one party standing for it."""
    return bool(_NAME_NO_V_RE.match(name)
                or re.search(r"(?<=[\w.'’)\]])\s+vs?\.\s+", name))


def _names_of(
    text: str, matches: "list[re.Match]", italic=None,
) -> "list[tuple[re.Match, str]]":
    """``(match, name)`` for each of *matches* — citations in document order —
    that a case name introduces, the name as printed: a whole caption
    ("Trump v. Wilcox") or the one party a short form keeps ("Wilcox")."""
    out: list = []
    floor = 0
    for m in matches:
        name_start = _case_name_start(text, m.start(), floor, italic)
        floor = m.end()
        if name_start is not None:
            out.append(
                (m, re.sub(r"[\s,]+$", "", text[name_start:m.start()])))
    return out


def _at_less_pins(
    text: str, named: "list[tuple[re.Match, str]]",
) -> dict[int, int]:
    """``{start: first page}`` for each named case citation (see
    :func:`_names_of`) that is really a short form with its "at" left out:
    "Wilcox, 145 S. Ct. 1417" after "Trump v. Wilcox, 145 S. Ct. 1415" is a
    pin cite to page 1417 of Wilcox, not a case beginning there.

    Read that way only where everything says short form: a lone party
    introduces it rather than a whole caption; that party is a party to a case
    the document has already cited in full, in the same volume, from a first
    page a little before (within :data:`ID_PIN_WINDOW`); and no court/year
    parenthetical follows, which a full citation would carry.  A string cite
    naming one party of another case in the same volume ("Abdul Latif, 939
    F. 3d 710") fails the party test, and stays a case of its own.
    """
    captions: list = []     # (volume, reporter key, first page, name words)
    out: dict[int, int] = {}
    for m, name in named:
        key = (m.group(1), _match_reporter_key(m))
        if _whole_caption(name):
            captions.append((key, int(m.group(3)), _name_words(name)))
            continue
        page = int(m.group(3))
        party = _name_words(name)
        _notes, after = note_pin_after_page(text, m.end())
        if not party or _COURT_YEAR_PAREN_RE.match(text, after):
            continue
        firsts = [first for k, first, words in captions
                  if k == key and first < page <= first + ID_PIN_WINDOW
                  and party <= words]
        if firsts:
            out[m.start()] = max(firsts)
    return out


# A short form that names the case and the page but leaves out the reporter:
# "Nken, at 433-34", "Nken, supra, at 433".  The page may not run straight
# into a capitalized word, which would make it a volume ("at 5 U.S.C."), nor
# into a word of prose ("In Smith, at 5 years old") — a footnote aside
# ("at 433 n.2").
_NAME_AT_RE = re.compile(
    r"(?:supra\s*,\s*)?at\s+\*?(\d{1,6})"
    r"(?!\d|\s*[A-Z]|\s+(?!nn?\.|notes?\b)[a-z])")


def _name_at_cites(
    text: str, named: "list[tuple[re.Match, str]]", italic=None,
) -> "list[tuple[int, int, str]]":
    """Short forms that give the case by name and the page but no reporter —
    "Nken, at 433-34" — as ``(start, end, "vol rep first@page")``, from the
    "at" (or "supra") on; the caller grows the span back over the name.

    The name must pick out exactly one case the document cites in full under
    a whole caption, by its parties' words ("Nken" is Nken v. Holder), and the
    page must be one of that case's (within :data:`ID_PIN_WINDOW` of its first
    page).  A name no case answers to — an author's, "Bray, Preliminary
    Injunction Realism, at 225" — or one several answer to ("Trump") comes
    back with an empty cite: a citation, but not one to open.
    """
    cases: dict[str, set] = {}      # base cite → its parties' words
    for m, name in named:
        if _whole_caption(name):
            cases.setdefault(_case_match_text(m), set()).update(
                _name_words(name))
    out: list = []
    for m in _NAME_AT_RE.finditer(text):
        name_start = _case_name_start(text, m.start(), 0, italic)
        if name_start is None:
            continue
        words = _name_words(text[name_start:m.start()])
        hits = [base for base, parties in cases.items()
                if words and words <= parties]
        first = int(hits[0].rsplit(" ", 1)[1]) if len(hits) == 1 else 0
        page = int(m.group(1))
        if not first or not first <= page <= first + ID_PIN_WINDOW:
            out.append((m.start(), m.end(), ""))
            continue
        notes, _end = note_pin_after_page(text, m.end())
        pin = join_note_pin(str(page), notes)
        out.append((m.start(), m.end(),
                    f"{hits[0]}@{pin}" if pin != str(first) else hits[0]))
    return out


def _iter_case_cites(text: str) -> list[re.Match]:
    text = text or ""
    matches: list[re.Match] = list(CITE_CAPTURE_RE.finditer(text))
    # Early-SCOTUS nominative cites — the parallel-interpolated forms first
    # (longer spans), then the bare form — all with (vol, reporter, page)
    # groups, so the short-cite index and case_match_text treat them like
    # any other reporter match.
    for pat in (NOMINATIVE_PARALLEL_RE, US_NOMINATIVE_PARALLEL_RE,
                STATE_NOMINATIVE_PARALLEL_RE, NOMINATIVE_CITE_RE,
                STATE_NOMINATIVE_CITE_RE):
        for m in pat.finditer(text):
            if any(m.start() < km.end() and km.start() < m.end()
                   for km in matches):
                continue
            if pat is STATE_NOMINATIVE_CITE_RE:
                if not state_nominative_cites(m.group(0)):
                    continue  # "30 Gray 5": no such volume — not a citation
                # A reporter named by a bare word (Head, Call, Gray) is read
                # only where a citation's volume stands — after the comma
                # closing a case name — never in a run of prose: "He sold 3
                # Head 5 times."
                if ("." not in m.group(2)
                        and not _at_citation_start(text, m.start())):
                    continue
            matches.append(m)
    for m in BROAD_CITE_CAPTURE_RE.finditer(text):
        if not _valid_case_reporter(m.group(2)):
            continue
        if any(m.start() < km.end() and km.start() < m.end() for km in matches):
            continue
        # A reporter named by a bare word — a state's name in full, a
        # reporter's own ("Sawyer", "Paige") — reads as one only where a
        # citation's volume stands, after the comma closing a case name.
        if (_bare_word_reporter(m.group(2))
                and not _at_citation_start(text, m.start())):
            continue
        if _ambiguous_name(m.group(1), m.group(2)):
            continue
        matches.append(m)
    # The older comma form, "138 Massachusetts, 165" (COMMA_CITE_CAPTURE_RE),
    # yields to every other reading: a known reporter, where a citation's
    # volume stands, and no capitalized word after the page — "in 1990, 3
    # Bush, 5 Clinton appointees" is prose.  Nor is it a case beginning at the
    # page when it is a pin into one the text cites in full: "Bryant, supra,
    # at 361, 131 S.Ct., 1157" is 131 S.Ct. 1143 with the "at" left out.
    cited = _index_first_pages(matches)
    for m in COMMA_CITE_CAPTURE_RE.finditer(text):
        if any(m.start() < km.end() and km.start() < m.end() for km in matches):
            continue
        if (not _comma_form_reporter(m.group(1), m.group(2), m.group(3))
                or not _at_citation_start(text, m.start())
                or _CAPITALIZED_WORD_RE.match(text, m.end())
                or _ambiguous_name(m.group(1), m.group(2))
                or _pinned_without_at(text, m, cited) is not None):
            continue
        matches.append(m)
    matches.sort(key=lambda m: (m.start(), -(m.end() - m.start())))
    return matches


def iter_case_citations(text: str) -> tuple[re.Match, ...]:
    """Every safely detected full case citation in running *text*."""
    return tuple(_iter_case_cites(text or ""))


def find_case_citation(
    text: str, *, permissive: bool = False,
) -> "re.Match | None":
    """First full reporter citation in *text*.

    ``permissive=True`` is for hand-typed citation fields, not arbitrary prose;
    it tolerates unpunctuated and obscure official reporter spellings.
    """
    matches = _iter_case_cites(text or "")
    if matches:
        return matches[0]
    return HAND_TYPED_CITE_RE.search(text or "") if permissive else None


# Series renamed partway through while the volume numbers ran on: the Court of
# Appeals of the District of Columbia's reports became the "U.S. App. D.C." at
# volume 75, and the Claims Court Reporter the Federal Claims Reporter at
# volume 27 when the court was renamed.  A volume is cited by the name it was
# printed under.  (old name, new name, first volume under the new name)
_RENAMED_SERIES = (
    ("App. D.C.", "U.S. App. D.C.", 75),
    ("Cl. Ct.", "Fed. Cl.", 27),
)


def renamed_series_cite(cite: str) -> str:
    """*cite* under the name its volume was actually printed under, when it
    names the other one — "80 App. D.C. 12" → "80 U.S. App. D.C. 12", "20
    Fed. Cl. 5" → "20 Cl. Ct. 5" — or "" when the name is already right or
    belongs to no renamed series."""
    m = re.fullmatch(r"\s*(\d{1,4})\s+(.+?)\s+(\d{1,6})\s*", cite or "")
    if not m:
        return ""
    vol, key = int(m.group(1)), _loose_reporter_key(m.group(2))
    for old, new, first in _RENAMED_SERIES:
        if key == _loose_reporter_key(old) and vol >= first:
            return f"{vol} {new} {m.group(3)}"
        if key == _loose_reporter_key(new) and vol < first:
            return f"{vol} {old} {m.group(3)}"
    return ""


def reporter_citation_variants(query: str) -> tuple[str, ...]:
    """Equivalent same-volume/same-page reporter spellings in *query*.

    The exact input is always first.  Surrounding caption and pincite text are
    preserved while only the first hand-typed reporter citation is replaced.
    """
    query = re.sub(r"\s+", " ", query or "").strip()
    if not query:
        return ()
    variants = [query]
    match = find_case_citation(query, permissive=True)
    if match is None:
        return tuple(variants)
    cites = [f"{match.group(1)} {reporter} {match.group(3)}"
             for reporter in reporter_variants(match.group(2))]
    renamed = renamed_series_cite(case_match_text(match)
                                  if match.re is not HAND_TYPED_CITE_RE
                                  else match.group(0))
    if renamed:
        cites.append(renamed)
    for cite in cites:
        expanded = query[:match.start()] + cite + query[match.end():]
        if expanded not in variants:
            variants.append(expanded)
    return tuple(variants)


def _iter_short_cites(text: str) -> list[re.Match]:
    matches: list[re.Match] = list(SHORT_CITE_RE.finditer(text or ""))
    for m in BROAD_SHORT_CITE_RE.finditer(text or ""):
        if not _valid_case_reporter(m.group(2)):
            continue
        if any(m.start() < km.end() and km.start() < m.end() for km in matches):
            continue
        matches.append(m)
    matches.sort(key=lambda m: (m.start(), -(m.end() - m.start())))
    return matches


# Prose between an "Id." and the citation it would refer to.  Past ID_NEAR_GAP
# the reference is only followed when the page number itself corroborates it —
# a court discussing a case for a paragraph and then writing "Id., at 888" is
# ordinary, and 888 falling inside that reporter's pages is better evidence
# than proximity.  Past ID_FAR_GAP even that is not enough: the discussion has
# moved on.
ID_NEAR_GAP = 240
ID_FAR_GAP = 900


def _id_chain_hard_broken(gap: str) -> bool:
    """Signals that end an "Id." chain however the page number reads: a record
    citation in between — in a brief that "Id." means the record, not the
    authority — or a blank line, which starts a new discussion."""
    return bool("\n\n" in gap or _RECORD_CITE_RE.search(gap))


def _id_chain_broken(gap: str) -> bool:
    stripped = (gap or "").strip()
    return bool(stripped and (
        len(stripped) > ID_NEAR_GAP
        or _id_chain_hard_broken(gap)
    ))


def build_short_cite_index(text: str) -> dict[tuple[str, str], list[int]]:
    """Map (volume, reporter) → sorted first-pages of every full citation in
    `text`, so a short form ('410 U.S. at 152') can be resolved to the case's
    first page (and thence opened and pin-jumped)."""
    return _index_first_pages(_iter_case_cites(text or ""))


def cite_target_from_text(
    text: str, index: dict[tuple[str, str], list[int]]
) -> tuple[str, str]:
    """(base cite, pin) named in `text`.  The base is "vol reporter firstpage"
    whether the cite is written in full ("8 F.4th 557, 565") or short
    ("8 F.4th at 565", resolved to its first page via `index`); the pin is the
    pincite/short page — encoded with its footnote when the cite pins one
    ("8 F.4th 557, 565 n.4" → "565n4") — or "".  Empty base when no reporter
    cite is present."""
    case_matches = _iter_case_cites(text)
    if case_matches:
        cm = case_matches[0]
        if cm.re is COMMA_CITE_CAPTURE_RE:
            # "Wilson, 501 U. S., 306-309" — a pin into a case cited in full
            # elsewhere in the document, its "at" left out — as
            # _iter_case_cites reads the form in running text.
            first = _pinned_without_at(text, cm, index)
            if first is not None:
                rep = re.sub(r"\s+", " ", cm.group(2)).strip().replace(
                    "U. S.", "U.S.")
                notes, _end = note_pin_after_page(text, cm.end())
                return (f"{cm.group(1)} {rep} {first}",
                        join_note_pin(cm.group(3), notes))
        base = _case_match_text(cm)
        pin, _end = pin_after(text, cm.end())
        return base, pin
    short_matches = _iter_short_cites(text)
    if short_matches:
        sm = short_matches[0]
        rep = re.sub(r"\s+", " ", sm.group(2)).strip().replace("U. S.", "U.S.")
        pin = int(sm.group(3))
        pages = index.get((
            sm.group(1), reporter_key(sm.group(2)),
        ))
        if pages:
            below = [p for p in pages if p <= pin]
            first = max(below) if below else pages[0]
        else:
            first = pin  # no full cite indexed — best effort
        notes, _end = note_pin_after_page(text, sm.end())
        return f"{sm.group(1)} {rep} {first}", join_note_pin(str(pin), notes)
    return "", ""


# ---------------------------------------------------------------------------
# Unpublished opinions cited by Westlaw / LEXIS number
# ---------------------------------------------------------------------------
# "Care One Mgmt., LLC v. United Healthcare Workers E., No. 12-6371, 2024 WL
# 1327972, at *7 (D.N.J. Mar. 28, 2024)" — no reporter ever prints these, but
# the docket number and opinion date locate the document in CourtListener's
# RECAP (PACER) archive.  The docket number usually appears only in the
# citation's first (or table-of-authorities) occurrence, while later short
# forms carry just the WL number — so the fields are indexed per WL number
# across the whole document and every occurrence gets the merged spec.

WL_CITE_RE = re.compile(
    r"\b(\d{4})\s+(WL|U\.\s?S\.\s?(?:Dist\.|App\.)\s?LEXIS)\s+(\d{2,10})\b")

# The docket number written immediately before the WL cite: "No. 12-6371,",
# "Nos. 12-6371, 12-6372,", "Civ. A. No. 96-3837,", "Case No. 2:13-cv-7779,".
_RECAP_DOCKET_RE = re.compile(
    r"(?:Nos?\.|Civ(?:il)?\.?\s?(?:A(?:ction)?\.?)?\s?Nos?\.?|Case\s+No\.)\s*"
    r"([A-Za-z]{0,4}\s?[\w:().-]{3,30}?)\s*,\s*$"
)

# The court/date parenthetical after the cite (an optional star pin cite in
# between): ", at *7 (D.N.J. Mar. 28, 2024)".
_RECAP_AFTER_RE = re.compile(
    r"^(?:,\s*(?:at\s+)?\*?\d{1,6}(?:\s*[-–—]\s*\*?\d{1,6})?)?"
    r"\s*\(([^()]{2,45}?)\s+"
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)[a-z]*\.?)\s+"
    r"(\d{1,2}),\s+(\d{4})\)"
)

# The case name before the citation — for the viewer's window title, and,
# when the citation prints no docket number, as the RECAP search key itself.
_RECAP_NAME_BODY = (
    r"([A-Z][\w.,'’&()/ -]{1,180}?\sv\.?\s"
    r"[\w.,'’&()/ -]{1,140}?|"
    r"In\s+re\s+[\w.,'’&()/ -]{2,140}?)"
)
# … followed by the docket number ("Care One …, No. 12-6371, 2024 WL …"):
_RECAP_NAME_RE = re.compile(
    _RECAP_NAME_BODY + r",\s*(?:Nos?\.|Civ|Case\s+No\.)")
# … or running right up to the cite ("Pecos River Talc LLC v. Emory,
# 2025 WL 1249947 …") — anchored to the end of the before-window.
_RECAP_NAME_END_RE = re.compile(_RECAP_NAME_BODY + r",\s*$")

# Signal words a name grab may drag along ("See Peninsula Pathology …").
_NAME_SIGNAL_RE = re.compile(
    r"^(?:But\s+see|See\s+also|See|Accord|Cf|Compare|Contra|Citing|Quoting|"
    r"E\.g)\W+\s*")


def _clean_case_name(raw: str) -> str:
    """A searchable case name from the text just before a citation, or ``""``
    when the grab is unusable: leading signal words are dropped, and a "name"
    that swallowed a reporter citation (the parallel-cite form "Smith v.
    Jones, 123 F. Supp. 3d 456, 2015 WL …" makes the lazy name regex run to
    the end of the window) is rejected outright — reporter junk is not a
    name RECAP or Scholar can search."""
    name = (raw or "").strip(" ,;")
    while True:
        m = _NAME_SIGNAL_RE.match(name)
        if not m:
            break
        name = name[m.end():]
    if len(name) < 6 or _iter_case_cites(name):
        return ""
    return name


# A slip opinion cited by docket number alone, no WL/LEXIS number at all —
# "Peninsula Pathology Assocs. v. Am. Int'l Indus., No. 23-1971 (4th Cir.
# Feb. 12, 2024)" — the usual form for a decision too new (or too minor) for
# any electronic reporter number.  The docket plus the court/date
# parenthetical is exactly what a RECAP lookup needs.  The optional middle
# part absorbs companion dockets ("Nos. 23-1971, 23-1980") and a slip-op pin
# cite; a trailing WL/LEXIS number cannot follow the docket here (the comma
# form fails the parenthetical), so those citations stay with the WL pass.
_DOCKET_CITE_RE = re.compile(
    r"(?:Nos?\.|Civ(?:il)?\.?\s?(?:A(?:ction)?\.?)?\s?Nos?\.?|Case\s+No\.)\s*"
    r"([A-Za-z]{0,4}\s?[\w:.-]{3,30}?)"
    r"((?:,\s*[\w:.-]{3,30})*?)"
    r"(?:,\s*slip\s+op\.(?:\s+at\s+\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)?)?"
    r"\s*\(([^()]{2,45}?)\s+"
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)[a-z]*\.?)\s+"
    r"(\d{1,2}),\s+(\d{4})\)"
)

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

# Bluebook court abbreviation → CourtListener court id, federal courts only
# (RECAP is the PACER archive; state-court WL cites keep the Scholar path).
# Keyed with spacing/periods squashed so "D.N.J.", "D. N.J." both hit.
_FED_COURT_IDS: dict[str, str] = {}
for _map in (court_catalog.CIRCUIT_COURTS, court_catalog.DISTRICT_COURTS,
             court_catalog.SPECIAL_COURTS):
    for _cid, _abbr in _map.items():
        _FED_COURT_IDS[re.sub(r"[^a-z0-9]", "", _abbr.lower())] = _cid


def iter_recap_cites(text: str) -> list[tuple[int, int, "str | None"]]:
    """Every WL / LEXIS citation in *text* as ``(start, end, spec)``.

    ``spec`` is a JSON string with the fields a RECAP lookup needs —
    ``cite``, ``date``, ``docket`` and/or ``name``, and (when it resolved
    to a federal court) ``court`` — or ``None`` when the citation can't be
    a RECAP document (no docket or case name anywhere in the document, no
    date, or a state court), in which case the caller should treat it as
    an ordinary case citation.  A docket number is the preferred key, but
    a citation printing none at all — "Pecos River Talc LLC v. Emory,
    2025 WL 1249947 (E.D. Va. Apr. 30, 2025)" — still resolves when the
    case name, federal court and date are all present."""
    index: dict = {}
    occurrences: list = []
    for m in WL_CITE_RE.finditer(text or ""):
        key = (m.group(1), norm_reporter(m.group(2)), m.group(3))
        info = index.setdefault(
            key, {"cite": re.sub(r"\s+", " ", m.group(0))})
        before = re.sub(r"\s+", " ", text[max(0, m.start() - 260):m.start()])
        dm = _RECAP_DOCKET_RE.search(before)
        if dm:
            info.setdefault("docket", dm.group(1).strip())
            nm = _RECAP_NAME_RE.search(before)
        else:
            # No docket printed — the case name running right up to the
            # cite can key the RECAP lookup instead.
            nm = _RECAP_NAME_END_RE.search(before)
        if nm:
            name = _clean_case_name(nm.group(1))
            if name:
                info.setdefault("name", name)
        am = _RECAP_AFTER_RE.match(
            re.sub(r"\s+", " ", text[m.end():m.end() + 180]))
        if am:
            info.setdefault(
                "court_raw", re.sub(r"\s+", " ", am.group(1)).strip())
            mon = _MONTHS.get(am.group(2)[:3].lower())
            if mon and "date" not in info:
                info["date"] = (f"{am.group(4)}-{mon:02d}-"
                                f"{int(am.group(3)):02d}")
        occurrences.append((m.start(), m.end(), key))

    out: list = []
    for start, end, key in occurrences:
        info = index[key]
        spec = None
        court_raw = info.get("court_raw", "")
        court_id = _FED_COURT_IDS.get(
            re.sub(r"[^a-z0-9]", "", court_raw.lower()))
        # A federal docket + date is worth a RECAP lookup, and so is a
        # case name + date when the citation names the federal court
        # outright (without a docket, a name search across all courts is
        # noise); a court named but not federal is a state court's
        # unpublished opinion either way.
        if "date" in info and (
                ("docket" in info and (court_id or not court_raw))
                or ("docket" not in info and "name" in info and court_id)):
            fields = {"cite": info["cite"], "date": info["date"]}
            if "docket" in info:
                fields["docket"] = info["docket"]
            if court_id:
                fields["court"] = court_id
            if info.get("name"):
                fields["name"] = info["name"]
            spec = json.dumps(fields)
        out.append((start, end, spec))
    return out


# A modern Supreme Court docket number: "18-19", "24-1260", "24A884".
_SCOTUS_DOCKET_NUMBER_RE = re.compile(
    r"\b(?:\d{1,3}[-‐-―−]\d{1,5}|\d{1,3}[A-Z]\d{1,5})\b",
    re.IGNORECASE,
)
# The docket line of an opinion's front matter — "No. 22-451.", "Nos. 24-109
# and 24-110", "Docket No. 18-19" — and the run of numbers it lists.
_DOCKET_LINE_RE = re.compile(
    r"\bNos?\.\s*(\d[\w‐-―−-]*"
    r"(?:\s*(?:,|&|and)\s*(?:Nos?\.\s*)?\d[\w‐-―−-]*)*)",
    re.IGNORECASE,
)


def docket_numbers_in(text: str) -> list[str]:
    """Supreme Court docket numbers an opinion's front matter gives as its
    own — only those a "No."/"Nos." introduces, in order, dashes normalized.

    Anything else shaped like one is not a docket: the argument dates of an
    old opinion read "Argued October 18-19, 1961", and "18-19" is a real
    docket (Republic of Korea v. BAE Systems, 2018).  An old-style number with
    no term prefix ("No. 26.") names no page on supremecourt.gov and yields
    nothing."""
    out: list[str] = []
    for line in _DOCKET_LINE_RE.finditer(text or ""):
        for m in _SCOTUS_DOCKET_NUMBER_RE.finditer(line.group(1)):
            docket = re.sub(r"[‐-―−]", "-", m.group(0)).upper()
            if docket not in out:
                out.append(docket)
    return out


def iter_docket_cites(text: str) -> list[tuple[int, int, str]]:
    """Slip opinions cited by docket number with no WL/LEXIS number at all —
    "Peninsula Pathology Assocs. v. Am. Int'l Indus., No. 23-1971 (4th Cir.
    Feb. 12, 2024)" — as ``(start, end, spec)`` RECAP actions, spanning the
    "No." through the closing parenthesis.  Only federal courts qualify
    (RECAP is the PACER archive), so the court must be printed and resolve;
    the case name just before the "No." rides along for the window title and
    a name-keyed retry when the docket search misses."""
    out: list = []
    for m in _DOCKET_CITE_RE.finditer(text or ""):
        court_raw = re.sub(r"\s+", " ", m.group(3)).strip()
        court_id = _FED_COURT_IDS.get(
            re.sub(r"[^a-z0-9]", "", court_raw.lower()))
        mon = _MONTHS.get(m.group(4)[:3].lower())
        if not court_id or not mon:
            continue
        fields = {"docket": m.group(1).strip(),
                  "date": f"{m.group(6)}-{mon:02d}-{int(m.group(5)):02d}",
                  "court": court_id}
        before = re.sub(r"\s+", " ", text[max(0, m.start() - 260):m.start()])
        nm = _RECAP_NAME_END_RE.search(before)
        if nm:
            name = _clean_case_name(nm.group(1))
            if name:
                fields["name"] = name
        out.append((m.start(), m.end(), json.dumps(fields)))
    return out


# A later cite of a Supreme Court decision by its slip opinion's page, the
# case named just before it: "Trump v. California, slip op. at 2".
_SLIP_OP_SHORT_RE = re.compile(
    r"slip\s+op\.,?\s+at\s+\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?!\d)")


def iter_scotus_docket_cites(
    text: str, italic=None,
) -> list[tuple[int, int, str]]:
    """Supreme Court decisions cited by docket number rather than by volume
    and page — an order on an emergency application, or an opinion not yet in
    the U.S. Reports: "Trump v. California, No. 26A139, slip op. at 4 (U.S.
    Aug. 24, 2026)".  "(U.S. …)" is the Bluebook's name for the Court in a
    court/date parenthetical; the date is the decision's.

    Returns ``(start, end, spec)`` in document order: each such citation from
    its "No." through the parenthetical, and each later short form that names
    the case and pins its slip opinion ("Trump v. California, slip op. at 2")
    from "slip op." on — the caller grows either back over the case name.
    ``spec`` is JSON with the ``docket``, the decision ``date`` (ISO) and the
    case ``name`` where one is printed, the same for every cite of a docket
    (from its first).  A short form whose name matches no such citation, or
    more than one, is not read.
    """
    text = text or ""
    found: list = []                # (start, end, docket)
    dockets: dict[str, dict] = {}   # docket → spec fields, from its first cite
    parties: dict[str, set] = {}    # docket → every name's words
    for m in _DOCKET_CITE_RE.finditer(text):
        if re.sub(r"[^a-z0-9]", "", m.group(3).lower()) != "us":
            continue  # not the Supreme Court — see iter_docket_cites
        docket = re.sub(r"[‐-―−]", "-", m.group(1).strip()).upper()
        mon = _MONTHS.get(m.group(4)[:3].lower())
        if not mon or not _SCOTUS_DOCKET_NUMBER_RE.fullmatch(docket):
            continue
        fields = dockets.setdefault(docket, {
            "docket": docket,
            "date": f"{m.group(6)}-{mon:02d}-{int(m.group(5)):02d}",
        })
        name_start = _case_name_start(text, m.start(), 0, italic)
        if name_start is not None:
            name = re.sub(r"\s+", " ", text[name_start:m.start()]).strip(" ,")
            fields.setdefault("name", name)
            parties.setdefault(docket, set()).update(_name_words(name))
        found.append((m.start(), m.end(), docket))
    for m in _SLIP_OP_SHORT_RE.finditer(text):
        if any(s <= m.start() < e for s, e, _d in found):
            continue  # the pin of a full citation, read with it
        name_start = _case_name_start(text, m.start(), 0, italic)
        if name_start is None:
            continue
        words = _name_words(text[name_start:m.start()])
        hits = [d for d, ws in parties.items() if words and words <= ws]
        if len(hits) == 1:
            found.append((m.start(), m.end(), hits[0]))
    return [(s, e, json.dumps(dockets[d])) for s, e, d in sorted(found)]


# ---------------------------------------------------------------------------
# Whole-document detection (used by the brief viewer)
# ---------------------------------------------------------------------------

# An "Id., at N" links to the case last cited only when N is plausibly a page of
# that reporter — within this many pages of its start.  A far page ("Id. at 1450"
# pointing into the record / a joint appendix, not the reporter) falls outside the
# window and is left unlinked.  Mirrors the opinion reader's _id_pin_in_range.
ID_PIN_WINDOW = 100


def _cite_first_page(base_cite: str) -> "int | None":
    """Reporter start page of a base citation ("410 U.S. 113" → 113), ignoring
    any "@pin" suffix; ``None`` when it doesn't parse."""
    matches = _iter_case_cites((base_cite or "").split("@", 1)[0])
    m = matches[0] if matches else None
    try:
        return int(m.group(3)) if m else None
    except (TypeError, ValueError):
        return None


def _page_in_window(first_page: int, pin: str) -> bool:
    """True when *pin* is within :data:`ID_PIN_WINDOW` pages of *first_page*."""
    try:
        n = int(pin)
    except (TypeError, ValueError):
        return False
    return first_page <= n <= first_page + ID_PIN_WINDOW


def _id_pin_in_range(base_cite: str, pin: str) -> bool:
    """True when an "Id., at *pin*" page falls within :data:`ID_PIN_WINDOW` pages
    of *base_cite*'s start page — i.e. a page of that reporter, not a record page."""
    start = _cite_first_page(base_cite)
    try:
        n = int(pin)
    except (TypeError, ValueError):
        return False
    return start is not None and start <= n <= start + ID_PIN_WINDOW


# How many citations back an "Id." will look when the nearest one cannot be
# what it means.  Two or three intervening citations is already generous.
ID_LOOKBACK = 4


# The page each paginated GovInfo link opens to, read back out of its URL.
_STAT_URL_PAGE_RE = re.compile(r"/statute/(\d+)/(\d+)")
_FR_URL_PAGE_RE = re.compile(r"/fr/(\d+)/(\d+)")


def _id_antecedent(
    text: str, recent: list, pin: "str | None", start: int,
    unlinkable: "list[tuple[int, int]] | None" = None,
    notes: "list[str] | None" = None,
) -> "tuple[str, str] | None":
    """The citation an "Id., at *pin*" refers to, or ``None``.

    Usually that is simply the last citation, but two things send the search
    further back.  A constitutional citation is never it — the Constitution has
    no pages, so "Id., at 888" after a reference to the Fourth Amendment means
    the case cited before that.  Neither is a case whose reporter cannot hold
    the page: if *pin* falls outside :data:`ID_PIN_WINDOW` of that citation's
    first page, the page belongs to some earlier authority, so the one before
    is tried, and so on.

    *recent* is the citations seen so far as ``(action, end)``, oldest first.
    *unlinkable* holds the spans of authorities that were recognised but
    deliberately not linked — a law review, an Attorney General opinion.  An
    "Id." after one of those refers to *it*, so it must not reach past one to
    an earlier authority and cite the wrong source.

    *notes* carries a footnote pinpoint written after the page ("Id., at 13
    n.4"); it rides into the returned action's pin so the link opens the note.
    The page still decides which authority is meant — a note number could
    belong to any of them.
    """
    if pin is None:
        return None  # a bare "Id." names no page, and is never linked
    for action, end in reversed(recent[-ID_LOOKBACK:]):
        gap = len(text[end:start].strip())
        if gap > ID_FAR_GAP:
            break
        if any(end <= u_start and u_end <= start
               for u_start, u_end in unlinkable or ()):
            break  # something we cannot open stands in between
        if action[0] == "const":
            continue  # unpaginated — "at N" cannot be pointing here
        if action[0] == "cite":
            if _id_pin_in_range(action[1], pin):
                return ("cite",
                        f"{action[1]}@{join_note_pin(pin, notes or [])}")
            continue  # that reporter has no such page — look further back
        if action[0] in ("statpdf", "frpdf"):
            # Both official serials are paginated, so the same test applies.
            page_re = (
                _STAT_URL_PAGE_RE if action[0] == "statpdf"
                else _FR_URL_PAGE_RE
            )
            m = page_re.search(action[1])
            if m and not _page_in_window(int(m.group(2)), pin):
                continue
        if gap > ID_NEAR_GAP:
            break  # a statute has no page to corroborate the reference with
        return action  # a statute/rule/regulation simply reopens
    return None


_FOOTNOTE_MARKS_AS_SPACES = str.maketrans(
    {mark: " " for mark in "⁰¹²³⁴⁵⁶⁷⁸⁹"})


def without_footnote_marks(text: str) -> str:
    """*text* with its superscript digits — footnote marks, as a brief's text
    carries them ("injury.⁴", "183⁵") — read as spaces.

    No citation has one in it, and left in, one hides where a citation ends:
    a superscript digit is a word character, so "183⁵" has no page 183 in it
    to find.  One character for one, so offsets into the result are offsets
    into *text*."""
    return (text or "").translate(_FOOTNOTE_MARKS_AS_SPACES)


def detect_links(
    text: str, *, italic=None,
) -> list[tuple[int, int, tuple[str, str]]]:
    """Scan `text` and return ``(start, end, action)`` for every citation that
    can be opened, in document order with overlaps resolved (first/longest
    wins).  ``action`` is the same ``(kind, value)`` pair the opinion reader
    hands to its link dispatch:

      * ``("cite", "410 U.S. 113@152")`` — a case (optionally pin-cited),
      * ``("usc"|"cfr"|"rule"|"const"|"statestat", spec)`` — an in-app source,
      * ``("browse", url)`` — a state statute we only link out to,
      * ``("statpdf", url)`` — a Statutes at Large scan,
      * ``("frpdf", url)`` — a Federal Register scan,
      * ``("scotus", spec)`` — a Supreme Court decision cited by docket
        number (see :func:`iter_scotus_docket_cites`).

    Unlike the opinion reader this works over the whole document at once, so a
    short form ("410 U.S. at 152") or an ``Id.`` resolves against citations that
    appear anywhere in the brief.

    ``italic`` is an optional per-character sequence saying which glyphs are set
    in an italic face.  Given it, a case name is only read where the type says
    there is one — see :func:`_mostly_italic`.
    """
    if not text:
        return []
    text = without_footnote_marks(text)
    # Only usable when the document actually carries styling: a scan's OCR
    # layer has none, and gating on it would then drop every case name.
    if italic is not None and not any(italic):
        italic = None
    # Authorities recognised in a reporter's shape but deliberately not linked
    # — a law review, an Attorney General opinion, the joint appendix.  They
    # are still authorities, so an "Id." following one must stop there.
    # Not one whose name joins two words with "&" ("12 J.L. & Pol'y 1"): the
    # reporters named that way are read, but the journals were never counted
    # here, and the one in a "(quoting …)" parenthetical is not the authority
    # a following "id." means.
    unlinkable = [
        (m.start(), m.end()) for m in BROAD_CITE_CAPTURE_RE.finditer(text)
        if not _valid_case_reporter(m.group(2)) and "&" not in m.group(2)
    ]
    case_cites = _iter_case_cites(text)
    named = _names_of(text, case_cites, italic)
    # A short form written without its "at" opens the case it pins into, and
    # is no first page for the short forms after it to resolve against.
    at_less = _at_less_pins(text, named)
    index = _index_first_pages(
        [m for m in case_cites if m.start() not in at_less])
    matches: list[tuple[int, int, str, object]] = []
    # English Reports citations first — the reprint form ("156 Eng. Rep.
    # 145"), its short form ("156 Eng. Rep., at 151") and the original
    # nominate cites ("9 Exch. 341", resolution-gated in eng_rep), each with
    # its pin page — so the broad case-reporter fallback below can yield to
    # them: a Scholar lookup by an English cite lands on an unrelated case.
    engrep_spans: list[tuple[int, int]] = []
    for start, end, spec in eng_rep.iter_cites(text):
        engrep_spans.append((start, end))
        matches.append((start, end, "engrep", spec))
    # Unpublished opinions cited by WL/LEXIS number: RECAP-resolvable ones
    # (federal docket + date found in the document) get a "recap" action;
    # the rest become ordinary case cites (Scholar), including the 7-digit
    # WL numbers the broad reporter regex's page group won't match.
    recap_spans: list[tuple[int, int]] = []
    for start, end, spec in iter_recap_cites(text):
        recap_spans.append((start, end))
        if spec is not None:
            matches.append((start, end, "recap", spec))
        else:
            cite = re.sub(r"\s+", " ", text[start:end]).strip()
            matches.append((start, end, "cite", cite))
    # Slip opinions cited by docket number alone (no WL/LEXIS number) are
    # RECAP lookups too — "No. 23-1971 (4th Cir. Feb. 12, 2024)".
    for start, end, spec in iter_docket_cites(text):
        if any(start < e and s < end for s, e in recap_spans):
            continue
        recap_spans.append((start, end))
        matches.append((start, end, "recap", spec))
    # A Supreme Court decision cited by docket number — "Trump v. California,
    # No. 26A139, slip op. at 4 (U.S. Aug. 24, 2026)" — and its later "slip
    # op. at N" short forms open the decision from the Court itself.
    for start, end, spec in iter_scotus_docket_cites(text, italic):
        if any(start < e and s < end for s, e in recap_spans):
            continue
        recap_spans.append((start, end))
        matches.append((start, end, "scotus", spec))
    # Federal Cases cited by case number ("Cole v. The Atlantic, Case No.
    # 2,976"; chained "The Chusan, Id. 2,717") — resolved at click time
    # through the CourtListener API by the printed name and the number.
    fedcas_spans: list[tuple[int, int]] = []
    for start, end, spec in fed_cas.iter_cites(text):
        if any(start < e and s < end for s, e in recap_spans):
            continue
        fedcas_spans.append((start, end))
        matches.append((start, end, "fedcas", spec))
    # Early lower-federal reporters ("1 Sumner, 73", "35 Fed. Rep. 665"),
    # pre-normalized to the abbreviations CourtListener indexes — claimed
    # ahead of the broad reporter fallback so the normalized form wins.
    # The Statutes at Large ("14 Stat. 27") parse as a reporter cite as well,
    # and a tie goes to whichever pass ran first — so this one runs before the
    # case passes, and the volume GovInfo actually holds wins the span.
    stat_spans: list[tuple[int, int]] = []
    for m in statutes_at_large.STAT_CITE_RE.finditer(text):
        if statutes_at_large.url_for(m):  # only link volumes GovInfo has
            stat_spans.append((m.start(), m.end()))
            matches.append((m.start(), m.end(), "stat", m))
    fr_spans: list[tuple[int, int]] = []
    for m in federal_register.FR_CITE_RE.finditer(text):
        if federal_register.url_for(m):
            fr_spans.append((m.start(), m.end()))
            matches.append((m.start(), m.end(), "fr", m))
    efed_spans: list[tuple[int, int]] = []
    for m in EARLY_FED_CITE_RE.finditer(text):
        if any(m.start() < e and s < m.end()
               for s, e in engrep_spans + recap_spans + fedcas_spans
               + stat_spans + fr_spans):
            continue
        efed_spans.append((m.start(), m.end()))
        matches.append((m.start(), m.end(), "cite", early_fed_cite_text(m)))
    claimed_spans = (
        engrep_spans + recap_spans + fedcas_spans + efed_spans
        + stat_spans + fr_spans
    )
    for m in case_cites:
        if any(m.start() < e and s < m.end() for s, e in claimed_spans):
            continue
        matches.append((m.start(), m.end(), "cite", m))
    for m in us_code.USC_CITE_RE.finditer(text):
        matches.append((m.start(), m.end(), "usc", m))
        for s, e, spec in _sibling_section_cites(text, m, dotted=False):
            matches.append((s, e, "usc-spec", spec))
    for m in ecfr.CFR_CITE_RE.finditer(text):
        matches.append((m.start(), m.end(), "cfr", m))
        for s, e, spec in _sibling_section_cites(text, m, dotted=True):
            matches.append((s, e, "cfr-spec", spec))
    for m in fed_rules.RULE_CITE_RE.finditer(text):
        matches.append((m.start(), m.end(), "rule", m))
    for m in constitution.CONST_CITE_RE.finditer(text):
        matches.append((m.start(), m.end(), "const", m))
    # Short forms ("Roe, 410 U.S. at 152") resolve to the case's full citation.
    for m in _iter_short_cites(text):
        # A WL short form ("2014 WL 1922831 at *5") overlapping a RECAP span
        # would outrank it (same start, longer) — the RECAP action wins.
        if any(m.start() < e and s < m.end() for s, e in recap_spans):
            continue
        pages = index.get((
            m.group(1), reporter_key(m.group(2)),
        ))
        if not pages:
            # A case the document never cites in full — or cites under a
            # mistyped volume ("588 U.S. at 190" for 558) — is still the
            # authority a following "id." means, so it stops the chain.
            unlinkable.append((m.start(), m.end()))
            continue
        pin = int(m.group(3))
        below = [p for p in pages if p <= pin]
        first = max(below) if below else pages[0]
        rep = re.sub(r"\s+", " ", m.group(2)).strip().replace("U. S.", "U.S.")
        cite = f"{m.group(1)} {rep} {first}"
        notes, _end = note_pin_after_page(text, m.end())
        if pin != first or notes:
            cite += "@" + join_note_pin(str(pin), notes)
        matches.append((m.start(), m.end(), "shortcite", cite))
    # Short forms naming the case but not its reporter: "Nken, at 433-34".
    # One whose name answers to no case, or to several — an article's
    # author, "Bray, …, at 225" — is an authority all the same.
    for start, end, cite in _name_at_cites(text, named, italic):
        if cite:
            matches.append((start, end, "shortcite", cite))
        else:
            unlinkable.append((start, end))
    for m in ID_CITE_RE.finditer(text):
        matches.append((m.start(), m.end(), "idcite", m))
    for c in state_statutes.iter_cites(text):
        if re.match(r"\s*id\.", c.text, re.IGNORECASE):
            continue
        matches.append((c.start, c.end, "statestat", c))

    matches.sort(key=lambda t: (t[0], -t[1]))
    # The running head cites the opinion in hand; skip anything inside it, and
    # keep a name grab from reaching back through one into the page above.
    head_spans = [(hm.start(), hm.end())
                  for hm in RUNNING_HEAD_CITE_RE.finditer(text)]
    out: list[tuple[int, int, tuple[str, str]]] = []
    pos = 0
    # Every citation linked so far as (action, end), oldest first — an "Id."
    # may have to look past the nearest one to find what it means.
    recent: list[tuple[tuple[str, str], int]] = []
    last_cite_end: int | None = None
    const_linked: set[int] = set()  # amendments already linked (prose dedup)
    for start, end, kind, m in matches:
        if start < pos:
            continue  # overlapping match — first/longest wins
        if any(start < he and hs < end for hs, he in head_spans):
            continue  # the reporter's running head, not a citation to follow
        action: tuple[str, str] | None
        cite_base = ""
        at_less_first = (at_less.get(start)
                         if kind == "cite" and not isinstance(m, str) else None)
        if at_less_first is not None:
            # "Wilcox, 145 S. Ct. 1417": the page is a pin into the case.
            rep = re.sub(r"\s+", " ", m.group(2)).strip().replace(
                "U. S.", "U.S.")
            cite_base = f"{m.group(1)} {rep} {at_less_first}"
            notes, _end = note_pin_after_page(text, end)
            action = ("cite",
                      f"{cite_base}@{join_note_pin(m.group(3), notes)}")
        elif kind == "cite":
            # m is a regex match for reporter cites, a pre-normalized string
            # for the WL/LEXIS cites added by the RECAP pass.
            cite = m if isinstance(m, str) else _case_match_text(m)
            cite_base = cite
            pin, _pin_end = pin_after(text, end)
            if pin:
                cite += "@" + pin
            action = ("cite", cite)
        elif kind == "recap":
            action = ("recap", m)  # m is the pre-built JSON spec
        elif kind == "scotus":
            action = ("scotus", m)  # m is the pre-built JSON spec
        elif kind == "usc":
            action = ("usc", us_code.cite_spec(m))
        elif kind == "cfr":
            action = ("cfr", ecfr.cite_spec(m))
        elif kind in ("usc-spec", "cfr-spec"):
            # A later section of a multi-section cite; m is the built spec.
            action = (kind.split("-")[0], m)
        elif kind == "rule":
            action = ("rule", fed_rules.cite_spec(m))
        elif kind == "const":
            # Link a bare prose amendment mention ("the First Amendment …", no
            # section, not a "U.S. Const." citation) only the first time that
            # amendment appears; formal citations always link.
            spec = constitution.cite_spec(m)
            ck, cnum, csec = (spec.split(":") + ["", "", ""])[:3]
            prose = "const" not in re.sub(r"\s+", " ", m.group(0)).lower()
            if ck == "amend" and cnum.isdigit():
                cn = int(cnum)
                if prose and not csec and cn in const_linked:
                    action = None
                else:
                    const_linked.add(cn)
                    action = ("const", spec)
            else:
                action = ("const", spec)
        elif kind == "shortcite":
            action = ("cite", m)  # m is the pre-built "vol rep page@pin"
            cite_base = m.split("@")[0]
        elif kind == "idcite":
            # "Id." → the citation it refers back to, but conservatively:
            # in a brief an "Id." often points at a record document rather than
            # the cited authority.  A bare "Id." (no page) is never linked, and
            # a run of intervening prose or record cites breaks the chain
            # outright.  Which citation it means is _id_antecedent's job — not
            # always the nearest one.
            if last_cite_end is not None and _id_chain_hard_broken(
                text[last_cite_end:start]
            ):
                action = None
            else:
                id_notes, _end = note_pin_after_page(text, end)
                action = _id_antecedent(
                    text, recent, m.group(1), start, unlinkable,
                    notes=id_notes)
        elif kind == "statestat":
            action = state_statutes.action_for(m)
        elif kind == "stat":
            action = ("statpdf", statutes_at_large.url_for(m))
        elif kind == "fr":
            action = ("frpdf", federal_register.url_for(m))
        elif kind == "engrep":
            # English Reports cite — reprint ("156 Eng. Rep. 145") or nominate
            # ("9 Exch. 341") — -> CommonLII scan; m is the pre-built spec.
            action = ("engrep", m)
        elif kind == "fedcas":
            # Federal Cases case number -> CourtListener lookup at click
            # time; m is the pre-built JSON spec ({"no", "name"}).
            action = ("fedcas", m)
        else:  # pragma: no cover - defensive
            action = None
        if action is not None:
            span_end = end
            # An "Id." pointing at a case carries pages the same way a cite
            # does — "id., at 675, 681-683, 693" is three of them — so it is
            # split alongside them.  It has no name to find.
            id_pages = kind == "idcite" and action[0] == "cite"
            # A case name may not reach back past anything already linked, or
            # past a running head.
            floor = pos
            for hs, he in head_spans:
                if he <= start:
                    floor = max(floor, he)
            if kind in ("cite", "shortcite") or id_pages:
                # Blue the citation the reader sees, not just the reporter
                # fragment the regex matched.
                base = action[1].split("@")[0] if id_pages else cite_base
                segments = _case_cite_spans(
                    text, start, end, floor,
                    short=(kind in ("shortcite", "idcite")
                           or at_less_first is not None),
                    italic=italic, with_name=not id_pages,
                )
                for seg_start, seg_end, seg_pin in segments:
                    # The first segment opens the page already folded into
                    # *action*; each later pin cite opens its own page.
                    out.append((seg_start, seg_end, (
                        ("cite", f"{base}@{seg_pin}") if seg_pin else action
                    )))
                span_end = segments[-1][1]
                # An Id. is itself the nearest antecedent for a following Id.,
                # but retain the clean reporter cite rather than its @pin
                # action so a chain never becomes "base@23@24".
                recent.append((("cite", base), span_end))
            else:
                span_start = start
                if kind == "scotus":
                    # A decision cited by docket number is named like any
                    # other: "Trump v. California, No. 26A139 (…)".
                    name_start = _case_name_start(text, start, floor, italic)
                    if name_start is not None:
                        span_start = name_start
                out.append((span_start, end, action))
                # Non-case Id. chains (to a statute, rule, or regulation) are
                # safe too: their action has no pin suffix to accumulate.
                recent.append((action, span_end))
            last_cite_end = span_end
            pos = span_end
            continue
        pos = end
    return out


if __name__ == "__main__":  # pragma: no cover - offline smoke test
    import sys

    sample = (
        "The Court relied on Roe v. Wade, 410 U.S. 113, 152 (1973), and later "
        "on 410 U.S. at 164.  See also 42 U.S.C. § 1983; Fed. R. Civ. P. 56; "
        "29 C.F.R. § 1614.105; U.S. Const. amend. XIV, § 1; Cal. Penal Code "
        "§ 187; Id. at 170."
    )
    found = detect_links(sample)
    for start, end, action in found:
        print(f"{start:4d}-{end:<4d} {action[0]:10s} {sample[start:end]!r} -> {action[1]!r}")

    kinds = {a[0] for _, _, a in found}
    expect = {"cite", "usc", "rule", "cfr", "const"}
    missing = expect - kinds
    if missing:
        print("MISSING kinds:", missing)
        sys.exit(1)
    # The short form "410 U.S. at 164" must resolve to the indexed first page.
    if not any(a == ("cite", "410 U.S. 113@164") for _, _, a in found):
        print("short form did not resolve to 410 U.S. 113@164")
        sys.exit(1)

    # "Id., at N" links to the previous case only when N is within ID_PIN_WINDOW
    # of its start page; a far page is a record/appendix cite, left unlinked.
    near = detect_links("See Roe v. Wade, 410 U.S. 113 (1973). Id. at 160.")
    if not any(a == ("cite", "410 U.S. 113@160") for _, _, a in near):
        print("in-range Id. did not link:", near)
        sys.exit(1)
    far = detect_links("See Roe v. Wade, 410 U.S. 113 (1973). Id. at 1450.")
    if any(a[0] == "cite" and "@1450" in a[1] for _, _, a in far):
        print("out-of-range Id. should not link to the case:", far)
        sys.exit(1)
    # A bare "Id." (no page) is never linked — too often a record cite — so the
    # only case link here is the full citation itself, not the trailing "Id.".
    bare = detect_links("See Roe v. Wade, 410 U.S. 113 (1973). Id.")
    if sum(1 for _, _, a in bare if a == ("cite", "410 U.S. 113")) != 1:
        print("bare Id. should not add a link:", bare)
        sys.exit(1)

    # Official state reporters, common in briefs, should be clickable and should
    # support short forms and in-range Id. references.
    state = detect_links(
        "Smith v. Jones, 306 Md. 556, 560 (1986). 306 Md. at 561. Id. at 562."
    )
    for want in (
        ("cite", "306 Md. 556@560"),
        ("cite", "306 Md. 556@561"),
        ("cite", "306 Md. 556@562"),
    ):
        if not any(a == want for _, _, a in state):
            print("state reporter/short/Id. failed:", want, state)
            sys.exit(1)

    # Do not mistake U.S.C./C.F.R. references for broad case reporters.
    statutory = detect_links("See 42 U.S.C. 1983 and 29 C.F.R. 1614.105.")
    if any(a[0] == "cite" for _, _, a in statutory):
        print("statutory citations became case cites:", statutory)
        sys.exit(1)

    # Brief record cites between an authority and Id. break the Id. chain.
    record_gap = detect_links("See Foo, 1 F.4th 1. App. 5. Id. at 6.")
    if any(a == ("cite", "1 F.4th 1@6") for _, _, a in record_gap):
        print("record Id. should not point to the case:", record_gap)
        sys.exit(1)

    star_pin = detect_links("See Foo, 1 F.4th 1. Id. at *6.")
    if not any(a == ("cite", "1 F.4th 1@6") for _, _, a in star_pin):
        print("star-page Id. did not link:", star_pin)
        sys.exit(1)

    # A court/jurisdiction parenthetical between reporter and page (as Google
    # Scholar prints old state cases) must not defeat the cite — it normalizes
    # away so the link resolves to "5 Johns. 37" (Kilburn v. Woodworth), not a
    # dead cite that dead-ends on a fuzzy name search.
    juris = detect_links("Kilbourn v. Woodworth, 5 Johns. (N.Y.) 37, was an "
                         "action of debt; see Borden v. Fitch, 15 Johns. (N.Y.) 121.")
    for want in (("cite", "5 Johns. 37"), ("cite", "15 Johns. 121")):
        if not any(a == want for _, _, a in juris):
            print("embedded jurisdiction paren cite failed:", want, juris)
            sys.exit(1)
    base, _pin = cite_target_from_text("5 Johns. (N.Y.) 37", {})
    if base != "5 Johns. 37":
        print("cite_target_from_text kept the paren:", repr(base))
        sys.exit(1)

    # English Reports cites must route to the CommonLII viewer ("engrep"), not
    # become Scholar case links (a Scholar lookup by an E.R. cite lands on an
    # unrelated case) — in the Bluebook "Eng. Rep." form, the "E.R." form, and
    # never via the short form either.
    er = detect_links(
        "Hadley v. Baxendale, 156 Eng. Rep. 145, 151 (1854); Wain v. "
        "Warlters, 102 E.R. 972.  See 156 Eng. Rep. at 151."
    )
    for want in (("engrep", "156:145"), ("engrep", "102:972")):
        if not any(a == want for _, _, a in er):
            print("Eng. Rep. cite did not route to engrep:", want, er)
            sys.exit(1)
    if any(a[0] == "cite" for _, _, a in er):
        print("Eng. Rep. cite leaked into a Scholar case link:", er)
        sys.exit(1)

    # The nominate-report parallel cites route to the same viewer (resolution-
    # gated on the shipped index): "9 Exch. 341" is Hadley, "5 East 10" is
    # Wain.  U.S. cites sharing an abbreviation stay ordinary case links —
    # New York's volumed "5 Johns. 37" must never be claimed by the volumeless
    # English Johnson.
    nom = detect_links(
        "Hadley v. Baxendale, 9 Exch. 341, 156 Eng. Rep. 145 (1854); "
        "Wain v. Warlters, 5 East 10; Kilbourn v. Woodworth, 5 Johns. "
        "(N.Y.) 37."
    )
    for want in (("engrep", "n:exch:9:341"), ("engrep", "156:145"),
                 ("engrep", "n:east:5:10"), ("cite", "5 Johns. 37")):
        if not any(a == want for _, _, a in nom):
            print("nominate detection failed:", want, nom)
            sys.exit(1)
    if any(a == ("cite", "9 Exch. 341") or a == ("cite", "5 East 10")
           for _, _, a in nom):
        print("nominate cite leaked into a Scholar case link:", nom)
        sys.exit(1)

    # Early-SCOTUS nominative cites, in every printed shape The Nestor (18 F.
    # Cas. 9) uses: bracketed and parenthesized parallel U.S. volumes, the
    # OCR hyphen glued to a page, a trailing pin range, the modern swapped
    # order, and the bare nominative form.
    nomsc = detect_links(
        "So the doctrine was laid down in The General Smith, 4 Wheat. "
        "[17 U. S.] 438, and in The St. Jago de Cuba, 11 Wheat. [24 U. S.] "
        "409, 415-417.  See Thomas v. Osborn, 19 How. (60 U. S.) 28; Rodd "
        "v. Heartt, 21 Wall. (88 U. S.)-597; Marbury v. Madison, 5 U.S. "
        "(1 Cranch) 137; Calder v. Bull, 3 Dall. 386."
    )
    for want in (("cite", "4 Wheat. 438"), ("cite", "11 Wheat. 409@415"),
                 ("cite", "19 How. 28"), ("cite", "21 Wall. 597"),
                 ("cite", "5 U.S. 137"), ("cite", "3 Dall. 386")):
        if not any(a == want for _, _, a in nomsc):
            print("nominative SCOTUS cite failed:", want, nomsc)
            sys.exit(1)

    # Early lower-federal reporters normalize to CourtListener's forms; the
    # spelled-out "Wheat. Rep." folds to "Wheat."; the possessive "Peters'
    # Rep." (Peters' District Court reports) must NOT become "Pet." (26
    # U.S.); and "Fed. R. Civ. P." stays a rule.
    efed = detect_links(
        "The Nestor, 1 Sumner, 73; The Young Mechanic, 2 Curtis, 404; "
        "The Amos D. Carver, 35 Fed. Rep. 665; The Orient, 10 Benedict, 620; "
        "The Creole, 2 Wall. Jr. 485, 518; Manro v. Almeida, 10 Wheat. Rep. "
        "472; Stevens v. The Sandwich, 1 Peters' Rep. 233; Fed. R. Civ. P. 56."
    )
    for want in (("cite", "1 Sumn. 73"), ("cite", "2 Curt. 404"),
                 ("cite", "35 F. 665"), ("cite", "10 Ben. 620"),
                 ("cite", "2 Wall. Jr. 485@518"), ("cite", "10 Wheat. 472")):
        if not any(a == want for _, _, a in efed):
            print("early-federal reporter failed:", want, efed)
            sys.exit(1)
    if any(a == ("cite", "1 Pet. 233") for _, _, a in efed):
        print("possessive Peters' Rep. must not become Pet.:", efed)
        sys.exit(1)
    if not any(a[0] == "rule" for _, _, a in efed):
        print("Fed. R. Civ. P. lost to the Fed. reporter pass:", efed)
        sys.exit(1)

    # Unpublished opinions: a federal WL cite with docket + court/date routes
    # to RECAP — with the docket carried from the first occurrence to later
    # short forms — while a state-court WL cite stays an ordinary case link.
    recap = detect_links(
        "Care One Mgmt., LLC v. United Healthcare Workers E., No. 12-6371, "
        "2024 WL 1327972, at *7 (D.N.J. Mar. 28, 2024).  A later short form "
        "cites 2024 WL 1327972, at *9 (D.N.J. Mar. 28, 2024).  But Foxtons, "
        "Inc. v. Cirri Germain Realty, No. A-61210-05T3, 2008 WL 465653 "
        "(N.J. Super. Ct. App. Div. Feb. 22, 2008) is a state case."
    )
    recap_actions = [a for _s, _e, a in recap if a[0] == "recap"]
    if len(recap_actions) != 2:
        print("expected 2 recap links:", recap)
        sys.exit(1)
    spec = json.loads(recap_actions[0][1])
    if not (spec.get("docket") == "12-6371" and spec.get("court") == "njd"
            and spec.get("date") == "2024-03-28"
            and spec.get("cite") == "2024 WL 1327972"
            and "Care One" in spec.get("name", "")):
        print("bad recap spec:", spec)
        sys.exit(1)
    if json.loads(recap_actions[1][1]).get("docket") != "12-6371":
        print("short-form recap did not inherit the docket:", recap_actions)
        sys.exit(1)
    if not any(a == ("cite", "2008 WL 465653") for _s, _e, a in recap):
        print("state WL cite should stay a case link:", recap)
        sys.exit(1)

    # A WL cite with no docket anywhere stays a plain case link, even when
    # its number is too long for the broad reporter regex.
    plain = detect_links("ShotSpotter Inc. v. VICE Media, LLC, 2022 WL "
                         "2373418, at *12 (Del. Super. Ct. June 30, 2022).")
    if not any(a == ("cite", "2022 WL 2373418@12") for _s, _e, a in plain):
        print("7-digit WL cite did not become a case link:", plain)
        sys.exit(1)
    if any(a[0] == "recap" for _s, _e, a in plain):
        print("state WL cite must not become recap:", plain)
        sys.exit(1)

    # A slip opinion cited by docket number alone (no WL/LEXIS number) routes
    # to RECAP; so does a WL cite with no docket but a case name, federal
    # court and date.  A state docket-only cite stays unlinked.
    slip = detect_links(
        "See Peninsula Pathology Assocs. v. Am. Int'l Indus., No. 23-1971 "
        "(4th Cir. Feb. 12, 2024); see also Pecos River Talc LLC v. Emory, "
        "2025 WL 1249947 (E.D. Va. Apr. 30, 2025).  But Smith v. Jones, "
        "No. A-61210-05T3 (N.J. Super. Ct. App. Div. Feb. 22, 2008) is a "
        "state slip opinion."
    )
    slip_specs = [json.loads(a[1]) for _s, _e, a in slip if a[0] == "recap"]
    if len(slip_specs) != 2:
        print("expected 2 recap links:", slip)
        sys.exit(1)
    docket_spec = next((s for s in slip_specs if "docket" in s), None)
    if not (docket_spec and docket_spec.get("docket") == "23-1971"
            and docket_spec.get("court") == "ca4"
            and docket_spec.get("date") == "2024-02-12"
            and docket_spec.get("name", "").startswith("Peninsula Pathology")):
        print("bad docket-only recap spec:", slip_specs)
        sys.exit(1)
    name_spec = next((s for s in slip_specs if "docket" not in s), None)
    if not (name_spec and name_spec.get("cite") == "2025 WL 1249947"
            and name_spec.get("court") == "vaed"
            and name_spec.get("date") == "2025-04-30"
            and name_spec.get("name") == "Pecos River Talc LLC v. Emory"):
        print("bad name-keyed recap spec:", slip_specs)
        sys.exit(1)

    # A parallel-cited WL number whose name grab would swallow the reporter
    # citation stays an ordinary case link — reporter junk is not a name
    # RECAP can search.
    par = detect_links("Doe v. Roe, 100 F. Supp. 3d 200, 2015 WL 1249947 "
                       "(D. Md. Apr. 30, 2015).")
    if any(a[0] == "recap" for _s, _e, a in par):
        print("parallel-cited WL number must not become recap:", par)
        sys.exit(1)
    if not any(a == ("cite", "2015 WL 1249947") for _s, _e, a in par):
        print("parallel-cited WL number should stay a case link:", par)
        sys.exit(1)

    print("\nOK:", len(found), "links;", sorted(kinds))
