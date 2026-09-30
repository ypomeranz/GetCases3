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

Cases go to Google Scholar, statutes, regulations, rules and the Constitution
to the official page the app itself reads them from, and the rest to the
source the app would fetch.  Tkinter-free, so the extension's bridge
(:mod:`browser_bridge`) and the tests can use it headlessly.
"""

from __future__ import annotations

import datetime
import json
import urllib.parse

import eng_rep
import fed_cas
import fed_rules
import leghist_fetch
import sec_decisions
import state_ca
import state_fl
import us_code

#: Google Scholar's case search, the app's first source for a case's text.
SCHOLAR_SEARCH = "https://scholar.google.com/scholar?q="
#: The Library of Congress's annotated Constitution, the app's source.
CONSTITUTION_URL = "https://constitution.congress.gov/constitution/"


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
        cite = value.split("@")[0]
        return SCHOLAR_SEARCH + urllib.parse.quote(f'"{cite}"')
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
