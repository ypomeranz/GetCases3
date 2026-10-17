"""A case's name, read off the caption at the head of its Google Scholar page.

The opinion window and the opinion database both name a case this way: the
window as it opens one, the database as it stores it and, for a record saved
without a name, as it indexes it.  Pure functions on parsed Scholar blocks —
no tkinter — so the database can use them on a worker thread.
"""

from __future__ import annotations

import re

from bluebook_names import (
    collapse_personal_all_caps_run,
    collective_case_name,
    cut_companion_cases as _cut_companion_cases,
    described_person_surname,
    ejectment_party_caption,
    first_case_lines,
    first_party_in_capitals,
    in_re_caps_name,
    in_re_for_petition,
    middle_initial_v,
    name_persons_by_surname,
    normal_case_caption as _titlecase_caps,
    normalize_et_al,
    refine_caption_case,
    simplify_historical_entity_caption,
    split_relator,
    strip_related_case_note,
)

# A citation line in the Scholar header: each parallel cite sits on its own
# centered line, e.g. "306 Md. 556 (1986)" / "510 A.2d 562" / "87 F.4th 563 (2023)"
_HEADER_CITE_RE = re.compile(
    r"^\s*(\d{1,4})\s+([A-Z][A-Za-z0-9.'’ ]{0,24}?)\s+(\d{1,5})\s*(?:\(|$)"
)
# Entity abbreviations that legitimately end a party name with a period, so the
# period must be kept ("Acme Co.", "Foo Corp.", "Bar Inc.").  Initialisms with
# internal periods (L.L.C., N.A., S.A.) are recognized separately.
_PARTY_ABBR_SUFFIXES = {
    "co", "cos", "corp", "corps", "inc", "ltd", "llc", "llp", "lp", "lllp",
    "plc", "pc", "pa", "pllc", "na", "sa", "ag", "nv", "bros",
}
# One letter and a period: an initial, whether it stands in a person's name
# or is a letter of a spaced initialism ("A. A. R. P.").
_SINGLE_INITIAL_RE = re.compile(r"[A-Za-z]\.")
# The same dotted abbreviation repeated back to back, as consolidated
# captions leave it ("United States U.S. U.S.").
_REPEATED_ABBREV_RE = re.compile(r"\b((?:[A-Za-z]+\.)+)(\s+\1)+")


def _party_ends_in_abbrev(s: str) -> bool:
    """True if the party name ends in an abbreviation whose trailing period is
    part of the name (an entity suffix like 'Co.' or an initialism like
    'L.L.C.'), as opposed to a stray sentence period."""
    parts = s.split()
    if not parts:
        return False
    last = parts[-1]
    if "." in last[:-1]:  # an internal period → initialism (L.L.C., N.A.)
        return True
    # A lone initial closing the name — the last letter of a spaced
    # initialism ("A. A. R. P."), or a person's middle initial ("John Q.").
    # A single letter is never a word a sentence ended on.
    if _SINGLE_INITIAL_RE.fullmatch(last):
        return True
    return last.rstrip(".").replace("’", "'").lower() in _PARTY_ABBR_SUFFIXES


# A comma segment of a caption that is a procedural designation, personal
# suffix, or descriptive office — not part of the party's name — so
# caption_party can strip such segments from the right.  Role/office
# segments swallow their qualifiers ("Warden of the Penitentiary", "U.S.
# Senator from the State of New York", "Sec'y of the U.S. Senate").
_ROLE_WORD = (
    r"(?:cross[- ])?(?:plaintiffs?|defendants?|appellants?|appellees?|"
    r"petitioners?|respondents?|intervenors?|movants?|claimants?|relators?|"
    r"libell?ants?|libell?ees?|garnishees?|contestants?)"
)
_US_STATE_NAMES = (
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|"
    r"delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|"
    r"kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
    r"mississippi|missouri|montana|nebraska|nevada|new\s+hampshire|"
    r"new\s+jersey|new\s+mexico|new\s+york|north\s+carolina|north\s+dakota|"
    r"ohio|oklahoma|oregon|pennsylvania|rhode\s+island|south\s+carolina|"
    r"south\s+dakota|tennessee|texas|utah|vermont|virginia|washington|"
    r"west\s+virginia|wisconsin|wyoming|district\s+of\s+columbia"
)
_PARTY_DESIGNATION_RE = re.compile(
    r"(?:"
    r"et\s+als?\.?|etc\.?|jr\.?|sr\.?|ii|iii|iv|deceased|minor|an?\s+minor|"
    r"m\.?d\.?|ph\.?d\.?|esq\.?|afl[-\s]?cio(?:[-\s]?clc)?|"
    + _ROLE_WORD + r"(?:[-/\s]+" + _ROLE_WORD + r")*(?:\s+in\s+error)?|"
    # a role ending one consolidated caption, with the next spilling into the
    # same segment ("Appellant. United States of America")
    + _ROLE_WORD + r"\.\s+.*|"
    r"in\s+error|successors?\b.*|"
    # the capacity partners sue or are sued in, which names no firm:
    # "RUSSELL ET AL., CO-PARTNERS," (309 U.S. 280) is Russell
    r"(?:co-?)?partners?(?:\s+(?:doing|trading|carrying\s+on)\b.*)?|"
    # a bare state name qualifying a governmental party ("Humboldt County,
    # California"; "City of Milwaukee, Wisconsin")
    r"(?:" + _US_STATE_NAMES + r")|"
    # offices / roles, with any qualifiers around them ("Warden", "Michigan
    # Secretary of State", "District Attorney of Dallas County",
    # "Corrections Director", "Assessor of Contra Costa County")
    r"[\w.'’ -]{0,40}\b"
    r"(?:secretary|sec'y|superintendent|directors?|commissioners?|"
    r"administrat(?:ors?|rix)|attorneys?|att'y|assessors?|treasurer|"
    r"wardens?|sheriffs?|governor|senators?|representatives?|"
    r"execut(?:ors?|rix)|trustees?|receivers?|guardians?|conservators?|"
    r"comptroller|clerk|marshal|postmaster|solicitor)\b[\w.'’ -]{0,60}|"
    r"president\s+of\b.*|grand\s+jury\b.*|fictitious\w*\b.*|"
    r"national\s+association|"
    # descriptive phrases
    r"(?:individually|personally)\b.*|(?:as|by|in\s+(?:his|her|its|their))\b.*|"
    r"on\s+behalf\b.*|for\s+the\s+use\b.*|d/?b/?a\b.*|"
    r"(?:[A-Za-z]\.\s*){2,}(?:[;,]\s*(?:[A-Za-z]\.\s*){2,})+|"  # minors' initials
    r"an?\s+(?:\w+\s+)*(?:corporation|company|partnership|association|"
    r"municipality|municipal\s+corporation|body\s+politic)\b.*"
    r")\s*\.?\s*$",
    re.IGNORECASE,
)

# A comma segment that is a business-entity suffix continuing the previous
# segment ("Socony-Vacuum Oil Co., Inc." — one party, two segments), spelled
# out as the old reports print it too: "CANADIAN AVIATOR, LIMITED" (324 U.S.
# 215) is Canadian Aviator, Ltd., which lost its "Limited" as a co-party.
_ENTITY_SUFFIX_RE = re.compile(
    r"(?:inc|incorporated|l\.?l\.?c|l\.?l\.?p|l\.?p|ltd|limited|ltda|p\.?l\.?c|"
    r"p\.?c|s\.?a|s\.?p\.?a|a\.?g|n\.?v|s\.?l|gmbh|co|corp|n\.?a)\.?",
    re.IGNORECASE,
)

# A segment *ending* in an organization word plausibly continues the first
# segment's name ("CHICAGO, BURLINGTON AND QUINCY RAILROAD COMPANY";
# "LOCAL 174, TEAMSTERS, ..., WAREHOUSEMEN & HELPERS OF AMERICA").
_ORG_TAIL_RE = re.compile(
    r"\b(?:company|co\.?|corp\.?|corporation|incorporated|railroad|railway|"
    r"r\.?r\.?|union|association|ass'n|brotherhood|workers|helpers|"
    r"america|bank|trust|society|club|university|college|institute|"
    r"partners(?:hip)?|group|bros\.?|brothers|sons|"
    r"inc\.?|llc|llp|ltd\.?|limited|plc)\s*\.?\s*$",
    re.IGNORECASE,
)


def caption_party(s: str) -> str:
    """One side of a Scholar caption → its Bluebook party name.  Drops the
    procedural designation and 'et al.'; when Scholar mixes cases, the
    all-caps run is the operative name ('Brent BREWBAKER' → 'Brewbaker',
    'UNITED STATES of America' → 'United States').

    Comma segments after the first are handled structurally rather than by
    cutting at the first comma (which would amputate a party whose own name
    contains one — "CHICAGO, BURLINGTON AND QUINCY RAILROAD COMPANY" is one
    party, not "CHICAGO"):

      1. cut at the first sign of a co-party / consolidated caption (a
         repeated party, an "and Ace Garage" co-defendant, a new "v."),
      2. strip procedural designations, offices and suffixes from the right,
      3. keep a business-entity suffix ("…Oil Co., Inc."), and
      4. keep the remaining segments only when they read as one entity name
         (few, short, ending in an organization word — "…RAILROAD COMPANY",
         "…HELPERS OF AMERICA"); otherwise they are co-parties: dropped."""

    def clean_seg(p: str) -> str:
        p = re.sub(r"\s*\[[^\]]{1,4}\]\s*$", "", p.strip())  # footnote marker
        p = re.sub(r"[:]+$", "", p).strip()
        return re.sub(r"\s+et\s+als?\.?$", "", p, flags=re.IGNORECASE).strip()

    # The early reports' "JOHN GLASS AND AL." is "et al." (see
    # bluebook_names.normalize_et_al).
    s = normalize_et_al(s)
    # The comma a reporter sets inside a closing quote — "The “ATLANTIC
    # SUN,” Her Engines, Boilers, etc." — closes the vessel's name, not a
    # segment of it: the quotes stay together (see abbreviate_case_name's
    # in rem res).
    s = re.sub(r"([,;])([”\"])", r"\2\1", s)

    # A relator clause is the party's, not a co-party to cut away (rule
    # 10.2.1(b)): "THE UNITED STATES, ON THE RELATION OF WILLIAM B. STOKES
    # ET AL." is United States ex rel. Stokes — Kendall v. United States ex
    # rel. Stokes, 37 U.S. (12 Pet.) 524, was cited without its relator.
    # One past a semicolon is a co-party's, though: "WASHINGTON State; The
    # People of the State of California, ex rel. Kamala D. Harris, …" is
    # Washington alone (rule 10.2.1(a)).
    relation = split_relator(s.split(";", 1)[0])
    if relation is not None:
        party, relator = (caption_party(p) for p in relation)
        if party and relator:
            return f"{party} ex rel. {relator}"
    # So is a demise, the old ejectment plaintiff's: "JOHN DEN, ex dem. JAMES
    # B. MURRAY AND JOHN C. KAYSER" is Den suing on Murray's demise — cited
    # "Murray's Lessee v. Hoboken Land & Improvement Co." — not John Den and
    # a co-party; abbreviate_case_name reads it whole.
    ejectment = ejectment_party_caption(s)
    if ejectment:
        # (A "V." in it is a lessor's middle initial — "ON THE DEMISE OF
        # HARMAN V. HART" — which title-casing would take for "v.".)
        out = _titlecase_caps(ejectment)
        if re.search(r"(?<=\s)V\.(?=\s)", ejectment):
            out = re.sub(r"(?<=\s)v\.(?=\s)", "V.", out)
        return out

    # The party in capitals ends where the co-parties in ordinary case begin:
    # "The TRUSTEES OF INDIANA UNIVERSITY and Teresa Dobrzykowski" is the
    # Trustees, though no comma parts them.
    s = first_party_in_capitals(s)
    raw = [clean_seg(p) for p in re.split(r"[,;]", s)]
    segs = [p for p in raw if p]
    description = ""
    if segs:
        # A charter-era bank's formal corporate style contains structural
        # commas that do not separate parties: "The President, Directors,
        # and Company of the Bank of ...".  Reassemble that fixed title
        # before the ordinary co-party logic examines comma segments.  The
        # opinion-evidence refinement later decides whether the embedded
        # "Bank of ..." may stand alone as the working party name.
        if (len(segs) >= 3
                and re.fullmatch(r"(?:the\s+)?(?:president|governor)",
                                 segs[0], re.IGNORECASE)
                and re.fullmatch(r"(?:directors?|dirs?\.?|trustees?)",
                                 segs[1], re.IGNORECASE)
                and re.match(
                    r"(?:and|&)\s+(?:company|co\.?|corporation)\s+of\s+"
                    r"(?:the\s+)?bank\s+of\b",
                    segs[2], re.IGNORECASE)):
            segs = [", ".join(segs[:3])] + segs[3:]
        # 1. Consolidation cut: a later segment that re-names the first party
        # (same final token), contains its own " v. ", or introduces another
        # party ("and Ace Garage" — unless it ends the entity's own name,
        # "…, and Paperhangers of America") starts a different case/party.
        first_last_tok = segs[0].split()[-1].rstrip(".,").lower() if segs[0].split() else ""
        cut = len(segs)
        for i in range(1, len(segs)):
            seg = segs[i]
            toks = [t.rstrip(".,").lower() for t in seg.split()]
            # An "and X" segment continues the entity's own name only when it
            # ends in an organization word and is not itself a complete
            # company ("…, and Paperhangers of America" continues; "…, and
            # Midway Mfg. Co." is a co-plaintiff).
            and_co_party = (
                re.match(r"and\b", seg, re.IGNORECASE)
                and (not _ORG_TAIL_RE.search(seg)
                     or any(_ENTITY_SUFFIX_RE.fullmatch(t.strip(".,"))
                            or t.strip(".,").lower() in ("mfg", "mfrs")
                            for t in seg.split()))
            )
            if (re.search(r"\s[vV]s?\.\s", f" {seg} ")
                    or re.match(r"(?:et\s+als?\.?|successors?)\b", seg,
                                re.IGNORECASE)
                    or (first_last_tok and first_last_tok in toks)
                    or and_co_party):
                cut = i
                break
        segs = segs[:cut]
        # What follows the first party's comma, which an office there shows
        # to be a person (below) before step 2 strips the office away.
        description = ", ".join(segs[1:])
        # 2. Designations / offices / suffixes strip from the right.
        while len(segs) > 1 and _PARTY_DESIGNATION_RE.fullmatch(segs[-1]):
            segs.pop()
        # 3. A business-entity suffix continues the first segment's name.
        kept = [segs[0]]
        rest = segs[1:]
        if rest and _ENTITY_SUFFIX_RE.fullmatch(rest[0]):
            kept.append(rest.pop(0))
        # 4. The remaining segments stay only when they read as one entity
        # name; anything else is a co-party list, dropped as before.  Bare
        # leftover suffixes ("…, LLC, Inc." from a consolidated caption)
        # never extend the name a second time.
        joined = ", ".join(kept + rest)
        if rest and (
            all(_ENTITY_SUFFIX_RE.fullmatch(p) for p in rest)
            or not (len(rest) <= 3
                    and _ORG_TAIL_RE.search(rest[-1])
                    and len(joined) <= 72)
        ):
            rest = []
        s = ", ".join(kept + rest)
        if len(kept + rest) > 1:
            description = ""    # the segments were more of an entity's name
    else:
        s = ""
    s = s.strip().lstrip(".;").rstrip(";").strip()
    s = re.sub(r"\s+et\s+als?\.?$", "", s, flags=re.IGNORECASE).strip()
    # Repeated dotted abbreviations from consolidated captions ("United
    # States U.S. U.S.") collapse to the first occurrence.  A doubled *single*
    # initial is not one of those — it is the name: the anonymized applicants
    # in "A. A. R. P. v. Trump" and "W. M. M. v. Trump" lose their case if
    # the second letter goes, and so does "J. J. Smith".
    s = _REPEATED_ABBREV_RE.sub(
        lambda m: (m.group(0) if _SINGLE_INITIAL_RE.fullmatch(m.group(1))
                   else m.group(1)),
        s,
    )
    s = re.sub(r"\b([Uu]nited\s+[Ss]tates)(?:\s+U\.?S\.?A?\.?)+", r"\1", s)
    # Drop a stray trailing period, but keep an entity abbreviation's period
    # ("Acme Co.", "Foo Corp.", "Bar Inc.").
    if s.endswith(".") and not _party_ends_in_abbrev(s):
        s = s[:-1].rstrip()
    # Scholar signals surnames with all caps ("Corrine Morgan THOMAS"), but
    # entity initialisms ("McDonald's USA, LLC") must remain intact.
    s = collapse_personal_all_caps_run(s)
    out = _titlecase_caps(s).strip(" ,;&")
    # One side of a caption has no "v." of its own: a "V." in it is a middle
    # initial, which title-casing takes for the separator — "BEATRICE V.
    # DITTUS" became "Beatrice v. Dittus", and Dittus v. Cranston, 53 Cal. 2d
    # 284, was cited "Beatrice v. Dittus v. Alan Cranston".
    if re.search(r"(?<=\s)V\.(?=\s)", s):
        out = re.sub(r"(?<=\s)v\.(?=\s)", "V.", out)
    # A suffix or an office only a person holds, after the party's comma,
    # makes it a person whose given names drop, though no list knows them
    # (rules 10.2.1(e), (g)): "MARKWAYNE MULLIN, SECRETARY, DEPARTMENT OF
    # HOMELAND SECURITY, ET AL." is Mullin — Mullin v. Doe (2026) had been
    # cited "Markwayne Mullin v. Doe" — and so is "MARKWAYNE MULLIN, JR.".
    if description:
        out = described_person_surname(
            out, _titlecase_caps(description)) or out
    return out


_PROCEDURAL_ALIAS_RE = re.compile(
    r",?\s+(?:d/b/a|d\.b\.a\.|dba|a/k/a|a\.k\.a\.|f/k/a|f\.k\.a\.|"
    r"doing\s+business\s+as|also\s+known\s+as|formerly\s+known\s+as)\s+",
    re.IGNORECASE,
)


def trim_procedural_caption(text: str) -> str:
    """Remove structural matter following an ``In re``-style case name.

    Lowercase text alone is not a boundary (``In re Estate of Smith`` and
    ballot-title matters need it).  Alias introductions and comma-delimited
    litigation roles are reliable boundaries, however: neither identifies the
    primary party used in the citation.  Cutting at the marker also handles a
    run of aliases whose individual corporate suffixes contain commas.
    """
    alias = _PROCEDURAL_ALIAS_RE.search(text or "")
    if alias:
        text = text[:alias.start()]
    text = re.sub(
        r",\s*(?:et\s+al\.?|debtors?|appellants?|appellees?|petitioners?|"
        r"respondents?|relators?|movants?|claimants?|plaintiffs?|defendants?|"
        r"creditors?|trustees?)\b.*$",
        "", text, flags=re.IGNORECASE,
    )
    return text.strip().rstrip(",")


# What heads an admiralty case as its res: "THE SADIE." on a line to itself,
# in capitals, its period closing it (a footnote mark may follow).
_RES_HEADING_RE = re.compile(
    r"(THE\s+[A-Z0-9][A-Z0-9'’&.\- ]{0,60}?)\.(?:\[[^\]\s]{1,6}\]|[*†‡])?")
# …and what is a party, never a res: a sovereign, a public body, a firm, a
# group of cases cited by its name ("THE PRIZE CASES.").
_NOT_A_RES_RE = re.compile(
    r"\b(?:PEOPLE|STATE|STATES|COMMONWEALTH|KING|QUEEN|TERRITORY|CITY|COUNTY|"
    r"TOWN|MAYOR|BOARD|TRUSTEES|PRESIDENT|COMPANY|CORPORATION|INC|CO|CORP|"
    r"BANK|RAILROAD|RAILWAY|CASES?)\b\.?$"
    r"|\b(?:PEOPLE|STATE|COMMONWEALTH|ET\s+AL|AND\s+OTHERS)\b")
_CAPTION_V_RE = re.compile(r"(?:^|\s)vs?\.(?:\s|$)")


def res_heading(text: str) -> str:
    """The res an admiralty case's caption heads it with, above the parties'
    own caption — "THE SADIE" for "THE SADIE. / NEW YORK & CUBA MAIL S. S.
    CO. / v. / FULTON LIGHTERAGE CO., Inc." (57 F.2d 908), "THE SCOTLAND"
    for "THE SCOTLAND. / DYER & Others / v. / NATIONAL STEAM NAVIGATION
    COMPANY." (118 U.S. 507) — which the case is cited by, The Sadie and
    The Scotland; "" for any other caption.  *text* is the caption's lines,
    a line to each "\\n" (see caption_text).  A party set on its own line
    ("THE PEOPLE, / v. / …") has the separator next, or a comma; the res
    has a party of the case's under it, and only then a "v."."""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    if len(lines) < 2:
        return ""
    m = _RES_HEADING_RE.fullmatch(lines[0])
    if not m or _NOT_A_RES_RE.search(m.group(1)):
        return ""
    under = lines[1:]
    if re.fullmatch(r"(?i)vs?\.?|versus|against|ags?t\.", under[0]):
        return ""
    if not any(_CAPTION_V_RE.search(ln) for ln in under):
        return ""
    return re.sub(r"\s+", " ", m.group(1))


def caption_text(block) -> str:
    """A caption block's text without the reporter's star pages — and
    without a case decided with it that Scholar links to its own page:
    447 Mich. 436 is headed "PEOPLE v. KEVORKIAN" and, linked, "HOBBINS v.
    ATTORNEY GENERAL", line by line and with no period between the two, and
    had been cited "People v. Kevorkian Hobbins v. Att'y Gen.".  Only a
    link that opens after the case's own "v.": a caption linked throughout
    is its own.  A case set under it unlinked, with only a line break
    between the two, goes too (see first_case_lines)."""
    out: list[str] = []
    links: set = set()
    for s in block.spans:
        if s.pagenum:
            continue
        if (s.link and "scholar_case" in s.link and s.link not in links
                and re.search(r"(?:^|\s)vs?\.\s+\S", "".join(out),
                              re.IGNORECASE)):
            break
        links.add(s.link)
        out.append(s.text)
    # The Michigan reports' "v" without its period ("PEOPLE v KEVORKIAN",
    # as CAP prints 447 Mich 436) is the separator every reader looks for.
    text = re.sub(r"(?:^|(?<=\s))(vs?)(?=\s|$)", r"\1.", "".join(out),
                  flags=re.MULTILINE)
    # …nor, where nothing but a line break sets it apart, a case under it.
    return first_case_lines(text)


def scholar_caption_name(blocks) -> str:
    """Bluebook case name derived from the Scholar page's party caption."""

    def refine(name: str) -> str:
        body = scholar_body_text(blocks)
        # The prose also settles a person's surname where the given-name
        # lists can't ("Chad Everet Brackeen" is Brackeen), which every
        # later abbreviation of this caption — a file name, a window title
        # — then keeps without needing the text again.
        return name_persons_by_surname(simplify_historical_entity_caption(
            refine_caption_case(name, body), body), body)

    for b in blocks[:8]:
        if b.kind != "center":
            continue
        # Keep a trailing period here — it may belong to an entity abbreviation
        # ending the caption ("… v. Acme Co."); caption_party drops only a
        # stray one.
        # A reporter star-page can occur in the middle of a long, wrapped
        # caption.  It is pagination metadata, not part of the case name.
        t = re.sub(r"\s+", " ", caption_text(b)).strip()
        # The reporter's annotation mark a caption can carry — "*Jones v.
        # The Commonwealth." (72 Va. 582), its note below — is no part of
        # the name.
        t = re.sub(r"^[*†‡]+\s*", "", t)
        # An Alabama-style "(Re <underlying case>)" cross-reference carries
        # its own " v. " and would masquerade as this case's caption.
        t = strip_related_case_note(t)
        if not t or _HEADER_CITE_RE.match(t) or t.startswith(("No.", "Nos.")):
            continue
        # A docket, court or date line set ahead of the caption — "[Sac.
        # No. 7096.", "CASE No. 1078.", "In Bank.", "Dec. 18, 1959.]" — is
        # not the case's name.  (A caption's own footnote mark closes a
        # bracket it opened: "IN RE NEAGLE, Petitioner.[1]".)
        if (t.startswith("[") or (t.endswith("]") and "[" not in t)
                or re.match(r"(?:case\s+|sac\.\s+|crim\.\s+|civ\.\s+|"
                            r"l\.\s*a\.\s+|s\.\s*f\.\s+)?nos?\.\s*\d",
                            t, re.IGNORECASE)
                or re.fullmatch(r"(?:in\s+bank|en\s+banc|(?:department|"
                                r"division)\s+\w+)\.?", t, re.IGNORECASE)):
            continue
        # A lone petitioner's caption is an in-re matter: "NIELSEN,
        # Petitioner." is In re Nielsen.
        t = in_re_for_petition(t) or t
        # An in-re style caption owns no separator, so any "v." inside it
        # belongs to a companion case ("In re Nexium Antitrust Litigation.
        # AstraZeneca AB v. United Food…") — classify it before splitting.
        # Only a role tail is cut at a comma; a comma can continue the
        # matter's own title ("In re Title, Ballot Title & Submission
        # Clause for 2015-2016 #156").  Companions are cut BEFORE the role
        # trim: trimming first would delete the companion's ", et al.,
        # Defendants-Appellants, v. …" tail — the very " v. " that proves
        # a companion case follows.
        if re.match(r"(?:IN\s+RE|EX\s+PARTE|(?:IN\s+THE\s+)?MATTER\s+OF)\b", t, re.IGNORECASE):
            # Scholar sets the matter's name in capitals, all else in
            # ordinary case: the capitals are the name.
            named = in_re_caps_name(t)
            if named:
                return refine(_titlecase_caps(named))
            t2 = trim_procedural_caption(_cut_companion_cases(t))
            return refine(_titlecase_caps(t2.strip()))
        # A group of cases the reporter heads with its popular name
        # ("SLAUGHTER-HOUSE CASES. THE BUTCHERS' BENEVOLENT ASSOCIATION … v.
        # …") is cited by that name, not by the first caption under it.
        collective = collective_case_name(t)
        if collective:
            return refine(_titlecase_caps(collective))
        # An admiralty case the reporter heads with its res, above the
        # parties' caption, is cited by the res: "The Sadie".
        res = res_heading(caption_text(b))
        if res:
            return refine(_titlecase_caps(res))
        # Google Scholar renders the party separator in lowercase ("… v. …")
        # even for ALL-CAPS captions ("MERCY HOSPITAL, INC. v. JACKSON"), so
        # a lowercase "v."/"vs." — or the earliest reports' spelled-out
        # "versus"/"against", or the New York reports' "agt." ("LOWBER agt.
        # WELLS", 13 How. Pr. 454) — is the reliable separator and never
        # collides with an uppercase middle initial like the "V." in
        # "Francis V. Lorenzo" or a party's own "AGAINST".  Only fall back
        # to a case-insensitive split (for a caption that happens to
        # capitalize the separator) when no lowercase one is found — and
        # never on a capitalized "Against", which is an organization's
        # ("MOTHERS AGAINST DRUNK DRIVING, Appellant."), not a separator.
        sides = re.split(r"\s+(?:vs?\.|versus|against|agt\.|agst\.)\s+", t,
                         maxsplit=1)
        if len(sides) != 2:
            # …past a capital "V." that is a middle initial ("FRANCIS V.
            # LORENZO, PETITIONER, V. SECURITIES AND EXCHANGE COMMISSION").
            sides = next(
                ([t[:m.start()], t[m.end():]]
                 for m in re.finditer(r"\s+[vV]s?\.\s+", t)
                 if m.group().strip() != "V."
                 or not middle_initial_v(t[:m.start()])), [t])
        if len(sides) != 2:
            sides = re.split(r"\s+(?:VERSUS|Versus)\s+", t, maxsplit=1)
        if len(sides) == 2:
            sides[1] = _cut_companion_cases(sides[1])
            left, right = caption_party(sides[0]), caption_party(sides[1])
            if left and right:
                return refine(f"{left} v. {right}")
        # A single-party caption ("HAYBURN'S CASE.", "THE AMISTAD.") has no
        # separator to split on; without this branch the cite line would
        # become the case name downstream.  Court, date, and docket lines
        # never qualify (cites and "No." lines were filtered above; the
        # digit guard covers public-domain cites — "2006-Ohio-3799" — that
        # the volume-reporter-page pattern does not).
        if (len(t) <= 80
                and sum(c.isalpha() for c in t) >= 4
                and not t[0].isdigit()
                and "court" not in t.lower()
                and not re.match(
                    r"(?:Argued|Reargued|Decided|Submitted|Filed|Released|"
                    r"January|February|March|April|May|June|July|August|"
                    r"September|October|November|December)\b",
                    t, re.IGNORECASE)
                # Procedural notes ride the header as their own centered
                # lines ("As Modified on Denial of Petition for Rehearing
                # December 1, 1964.") and are never the caption.
                and not re.search(
                    r"\b(?:rehearing|certiorari|en banc|as amended|"
                    r"as modified|as corrected|petition for)\b",
                    t, re.IGNORECASE)):
            return refine(
                _titlecase_caps(_cut_companion_cases(t).split(",")[0].strip()))
    return ""


def scholar_body_text(blocks) -> str:
    """The opinion's prose paragraphs, joined — the mixed-case evidence
    :func:`refine_caption_case` reads party-name capitalization from.
    Capped: the parties are named within the first pages."""
    out: list = []
    total = 0
    for b in blocks:
        if b.kind in ("center", "heading"):
            continue  # caption/heading lines: their casing is stylized
        t = re.sub(r"\s+", " ", b.text()).strip()
        if not t:
            continue
        out.append(t)
        total += len(t)
        if total > 40000:
            break
    return "\n".join(out)
