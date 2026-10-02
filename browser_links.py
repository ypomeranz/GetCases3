"""Where a citation opens in a web browser.

Every citation the app links — a case, a statute, the Congressional Record, a
page of the SEC's reports — is carried as a ``(kind, value)`` action (see
:func:`citations.detect_links`).  In the app it opens in a viewer of its own;
this module says which *web page* shows the same thing, for the two places
that need one:

* the right-click "open in your browser" on a citation in the app, and
* the browser extension (``browser_extension/``), which links the citations
  on the pages a user reads and, when GetCases is not running, sends a click
  to this page instead.

A case goes to its scanned report where the citation alone gives the PDF's
address — the official U.S. Reports at the Library of Congress or GovInfo,
any other reporter at the Caselaw Access Project — and otherwise to Google
Scholar's case-law search; statutes, regulations, rules and the Constitution
go to the official page the app itself reads them from, and the rest to the
source the app would fetch.  Tkinter-free, so the extension's bridge
(:mod:`browser_bridge`) and the tests can use it headlessly.
"""

from __future__ import annotations

import datetime
import json
import re
import urllib.parse

import citations
import eng_rep
import fed_cas
import fed_rules
import leghist_fetch
import sec_decisions
import state_ca
import state_fl
import us_code

#: Google Scholar's search of case law in every court (``as_sdt=2006``);
#: without it Scholar searches articles, where a citation finds law reviews
#: citing the case.
SCHOLAR_SEARCH = "https://scholar.google.com/scholar?hl=en&as_sdt=2006&q="
#: The Library of Congress's annotated Constitution, the app's source.
CONSTITUTION_URL = "https://constitution.congress.gov/constitution/"

# ---------------------------------------------------------------------------
# Where a case's scan is, from its citation (the app's own routing: see
# courtlistener_gui's _us_reports_loc_url, _us_reports_govinfo_url and
# _static_case_law_url)
# ---------------------------------------------------------------------------

#: The Library of Congress's scan of each opinion in volumes 1–542 of the
#: U.S. Reports, named by its first page.
LOC_US_REPORTS = ("https://tile.loc.gov/storage-services/service/ll/usrep/"
                  "usrep{vol:03d}/usrep{vol:03d}{page:03d}/"
                  "usrep{vol:03d}{page:03d}.pdf")
LOC_US_REPORTS_MAX = 542
#: Up to this volume the Library's scan is the better one; past it GPO's.
LOC_US_REPORTS_PREFERRED_MAX = 501
#: GPO's edition at GovInfo, from volume 2.  Its own stable "link" address
#: refuses some volumes (550 U.S. 544 is a 400), so the file is named
#: directly; past the last volume known to be there it is still worth
#: asking for, as GovInfo adds them.
GOVINFO_US_REPORTS = ("https://www.govinfo.gov/content/pkg/USREPORTS-{vol}/"
                      "pdf/USREPORTS-{vol}-{page}.pdf")
GOVINFO_US_REPORTS_MAX = 583
#: The Caselaw Access Project's scan of each case it holds, named by the
#: case's first page (the "-01": the first case to begin there).
CASE_LAW_PDF = ("https://static.case.law/{slug}/{vol}/case-pdfs/"
                "{page:04d}-01.pdf")

#: The Supreme Court's early reports, cited by their reporters' names: each
#: one's volumes are U.S. Reports volumes *offset* on, page for page —
#: Dallas 1–4 is 1–4 U.S., Cranch 1–9 is 5–13 U.S., and so on to Otto 1–17,
#: 91–107 U.S.  (offset, volumes).
NOMINATIVE_US_REPORTS = {
    "dall": (0, 4), "dallas": (0, 4),
    "cranch": (4, 9),
    "wheat": (13, 12), "wheaton": (13, 12),
    "pet": (25, 16), "peters": (25, 16),
    "how": (41, 24), "howard": (41, 24),
    "black": (65, 2),
    "wall": (67, 23), "wallace": (67, 23),
    "otto": (90, 17),
}

_CITE_PARTS_RE = re.compile(r"\s*(\d+)\s+(.+?)\s+(\d+)\s*")


def _loose_key(rep: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (rep or "").lower())


def case_law_slug(rep: str) -> str:
    """The Caselaw Access Project's name for reporter *rep* in its
    addresses: the reporter's own where the app knows one ("N.Y.2d" is
    "ny-2d"), else CAP's rule — lowercase, initials closed up, a space a
    hyphen, other punctuation gone ("F. Supp. 2d" → "f-supp-2d", "N. E." →
    "ne")."""
    slug = citations.case_law_reporter_slug(rep)
    if slug:
        return slug
    rep = re.sub(r"(?<![A-Za-z])([A-Za-z]\.)\s+(?=[A-Za-z]\.|\d)", r"\1",
                 rep or "")
    s = re.sub(r"[^a-z0-9-]", "", rep.lower().replace(" ", "-"))
    return re.sub(r"-+", "-", s).strip("-")


def us_reports_volume(vol: int, rep: str) -> int:
    """The U.S. Reports volume of volume *vol* of reporter *rep* — itself
    for "U.S.", the renumbered one for the early reporters ("1 Cranch" is
    5 U.S.) — or 0 when *rep* is neither."""
    key = _loose_key(rep)
    if key == "us":
        return vol
    offset, volumes = NOMINATIVE_US_REPORTS.get(key, (0, 0))
    return vol + offset if 1 <= vol <= volumes else 0


def us_reports_pdf_urls(vol: int, page: int) -> "list[str]":
    """The official scans of the opinion beginning at page *page* of volume
    *vol* of the U.S. Reports, the better first."""
    loc = (LOC_US_REPORTS.format(vol=vol, page=page)
           if 1 <= vol <= LOC_US_REPORTS_MAX else "")
    gpo = GOVINFO_US_REPORTS.format(vol=vol, page=page) if vol >= 2 else ""
    order = ((loc, gpo) if vol <= LOC_US_REPORTS_PREFERRED_MAX
             else (gpo, loc))
    return [u for u in order if u]


def case_pdf_urls(cite: str) -> "list[str]":
    """The scans of the case *cite* names (``"201 F. 20"``, a pin after an
    ``@`` aside) whose addresses its citation gives, the best first: the
    official U.S. Reports for the Supreme Court; the Caselaw Access
    Project's for any other reporter, under the official series too where
    the reporter's volumes were renumbered into one ("19 Pick. 234" is 36
    Mass. 234).  Not checked: CAP holds only some of a reporter's cases,
    so whoever opens one looks first (an unchecked list of guesses)."""
    base = (cite or "").split("@")[0]
    m = _CITE_PARTS_RE.fullmatch(base)
    if not m:
        return []
    vol, rep, page = int(m.group(1)), m.group(2), int(m.group(3))
    us = us_reports_volume(vol, rep)
    if us:
        return us_reports_pdf_urls(us, page)
    key = _loose_key(rep)
    if not key or key == "wl" or key.endswith("lexis"):
        return []
    out: "list[str]" = []
    for official in [*citations.state_nominative_cites(base), base]:
        om = _CITE_PARTS_RE.fullmatch(official)
        slug = case_law_slug(om.group(2)) if om else ""
        if slug:
            url = CASE_LAW_PDF.format(slug=slug, vol=int(om.group(1)),
                                      page=int(om.group(3)))
            if url not in out:
                out.append(url)
    return out


def scholar_case_url(cite: str) -> str:
    """Google Scholar's case-law search for citation *cite*."""
    return SCHOLAR_SEARCH + urllib.parse.quote(
        f'"{(cite or "").split("@")[0]}"')


def case_urls(cite: str) -> "list[str]":
    """Every page that may show the case *cite* names, in the order to try
    them: its scans (:func:`case_pdf_urls`), then Google Scholar — the one
    always there."""
    return case_pdf_urls(cite) + [scholar_case_url(cite)]


def case_url(cite: str) -> str:
    """The one page for the case *cite* names when there is no looking
    first: its official U.S. Reports scan where that is surely there,
    else Google Scholar's case-law search."""
    base = (cite or "").split("@")[0]
    m = _CITE_PARTS_RE.fullmatch(base)
    if m:
        us = us_reports_volume(int(m.group(1)), m.group(2))
        if us and us <= GOVINFO_US_REPORTS_MAX:
            return us_reports_pdf_urls(us, int(m.group(3)))[0]
    return scholar_case_url(cite)


def _split_spec(value: str) -> "tuple[str, str, list[str]]":
    """``"title:section:sub,sub"`` → (title, section, [subs])."""
    # Key first and subdivisions last, so a section with a colon of its own
    # (N.J.'s "2C:11-3") survives.
    first, _sep, rest = (value or "").partition(":")
    section, sep, subs = rest.rpartition(":")
    if not sep:
        section, subs = rest, ""
    return first, section, [s for s in subs.split(",") if s]


def _google(text: str) -> str:
    return ("https://www.google.com/search?q="
            + urllib.parse.quote((text or "").strip()))


def scotus_docket_url(spec_json: str) -> str:
    """The supremecourt.gov docket page for a Supreme Court decision cited by
    docket number (a ``("scotus", spec)`` action), or ""."""
    try:
        docket = json.loads(spec_json).get("docket") or ""
        import scotus_docket
        return scotus_docket.official_docket_url(docket)
    except Exception:
        return ""


def _statute_url(kind: str, value: str, text: str) -> str:
    """The official page for a statute, regulation, rule or constitutional
    provision — where the app's own viewer reads it from."""
    first, section, _subs = _split_spec(value)
    if kind == "usc" and first and section:
        return us_code.section_url(first, section)
    if kind == "cfr" and first and section:
        return f"https://www.ecfr.gov/current/title-{first}/section-{section}"
    if kind == "rule" and first in fed_rules.RULESETS and section:
        return fed_rules.rule_url(first, section)
    if kind == "const":
        number = section
        if first == "amend" and number.isdigit():
            return f"{CONSTITUTION_URL}amendment-{int(number)}/"
        if first == "art" and number.isdigit():
            return f"{CONSTITUTION_URL}article-{int(number)}/"
        if first == "pmbl":
            return f"{CONSTITUTION_URL}preamble/"
        return CONSTITUTION_URL
    if kind == "statestat" and section:
        if first.startswith("ca-"):
            return state_ca.section_url(first.split("-", 1)[1].upper(), section)
        if first == "fl":
            # A year's statutes are published over its summer; before then
            # the latest edition is the year before's.
            today = datetime.date.today()
            year = today.year if today.month > 6 else today.year - 1
            return state_fl.section_url(year, section)
    return _google(text or value)


def browser_url(action: "tuple[str, str]", text: str = "") -> str:
    """The web page for *action*, or "" when there is none to open.

    *text* is the citation as printed, for the web search that is the last
    resort when a source has no page of its own for it."""
    kind, value = action
    if kind in ("browse", "statpdf", "frpdf"):
        return value
    if kind in ("usc", "cfr", "rule", "const", "statestat"):
        return _statute_url(kind, value, text)
    if kind == "leghist":
        return leghist_fetch.browser_url(value) or ""
    if kind == "sec":
        return sec_decisions.page_url(value)
    if kind == "scotus":
        return scotus_docket_url(value)
    if kind == "recap":
        # The RECAP search on CourtListener, pre-filtered to the docket (or,
        # for a citation printing none, the case name), court and opinion
        # date the citation names.
        try:
            spec = json.loads(value)
        except Exception:
            spec = {}
        params = {"type": "rd", "q": "",
                  "entry_date_filed_after": spec.get("date", ""),
                  "entry_date_filed_before": spec.get("date", "")}
        if spec.get("docket"):
            params["docket_number"] = spec["docket"]
        elif spec.get("name"):
            params["case_name"] = spec["name"]
        if spec.get("court"):
            params["court"] = spec["court"]
        return "https://www.courtlistener.com/?" + urllib.parse.urlencode(params)
    if kind == "cite":
        return case_url(value)
    if kind == "fedcas":
        # Federal Cases number → the CourtListener search the in-app lookup
        # would run, pre-filtered to the era: by printed case name when the
        # citation gives one, else by the "Case No. N" phrase.
        try:
            spec = json.loads(value)
        except Exception:
            spec = {}
        name = spec.get("name") or ""
        q = (f'caseName:"{name}"' if name
             else f'"Case No. {fed_cas.pretty_number(spec.get("no") or "")}"')
        return ("https://www.courtlistener.com/?"
                + urllib.parse.urlencode(
                    {"q": q, "type": "o", "filed_before": "1882-12-31"}))
    if kind == "engrep":
        # English Reports → the CommonLII case page (first case at that page),
        # not the .pdf directly: the origin hotlink-blocks the scan unless you
        # reach it from a link on the site.  Falls back to a CommonLII search
        # when the citation isn't in our index.
        base, _pin = eng_rep.split_pin(value)
        cases = eng_rep.resolve(base)
        if cases:
            return cases[0].web_url
        vp = eng_rep.parse_spec(base)
        return eng_rep.search_url(*vp) if vp else ""
    return _google(text or value)
