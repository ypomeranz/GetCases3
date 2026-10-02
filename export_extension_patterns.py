"""Write the browser extension's ``src/patterns.js``: the app's citation
patterns, for the extension.

When GetCases is running, the extension asks it to read a page's citations
(see ``browser_bridge``).  When it is not, the extension reads them itself
(``src/citations.js``), and it does so with the app's own regular expressions
and reporter tables, copied here from the Python modules and converted to
JavaScript's dialect.  Run this after changing any of them::

    python export_extension_patterns.py

``test_browser_extension.py`` fails while the file is out of date, and checks
that each converted pattern matches in JavaScript exactly what it matches in
Python.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "browser_extension" / "src" / "patterns.js"


# ---------------------------------------------------------------------------
# Python's regular expressions → JavaScript's
# ---------------------------------------------------------------------------

def _strip_verbose(p: str) -> str:
    """*p* without the whitespace and comments re.VERBOSE ignores."""
    out: list[str] = []
    i, in_class = 0, False
    while i < len(p):
        c = p[i]
        if c == "\\":
            out.append(p[i:i + 2])
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
            out.append(c)
            i += 1
            continue
        if c == "[":
            in_class = True
            out.append(c)
            i += 1
            if p.startswith("^", i):
                out.append("^")
                i += 1
            if p.startswith("]", i):
                out.append("]")
                i += 1
            continue
        if c in " \t\n\r\f\v":
            i += 1
            continue
        if c == "#":
            j = p.find("\n", i)
            i = len(p) if j < 0 else j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _both_cases(ch: str) -> str:
    lo, up = ch.lower(), ch.upper()
    return ch if lo == up else lo + up


def _class_both_cases(body: str) -> str:
    """A character class's contents with every letter (and letter range) in
    both cases — the class as it matches under re.IGNORECASE."""
    out: list[str] = []
    extra: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\":
            out.append(body[i:i + 2])
            i += 2
            continue
        if (i + 2 < len(body) and body[i + 1] == "-" and body[i + 2] != "]"
                and body[i + 2] != "\\"):
            lo, hi = c, body[i + 2]
            out.append(body[i:i + 3])
            if lo.isalpha() and hi.isalpha() and lo.islower() == hi.islower():
                other = (lo.upper() + "-" + hi.upper() if lo.islower()
                         else lo.lower() + "-" + hi.lower())
                extra.append(other)
            i += 3
            continue
        out.append(c)
        if c.isalpha():
            other = _both_cases(c).replace(c, "")
            if other:
                extra.append(other)
        i += 1
    return "".join(out) + "".join(extra)


def to_js(pattern: str, flags: int) -> "tuple[str, str]":
    """``(source, flags)`` of a JavaScript RegExp matching what *pattern*
    matches in Python under *flags* (IGNORECASE, VERBOSE, MULTILINE and
    DOTALL are understood).

    Python's scoped ``(?-i:…)`` is kept only where JavaScript can say it
    too: a pattern that uses one loses its ``i`` flag instead, and spells
    every letter outside those groups in both cases."""
    if flags & re.VERBOSE:
        pattern = _strip_verbose(pattern)
    ignore_case = bool(flags & re.IGNORECASE)
    expand = ignore_case and "(?-i:" in pattern
    out: list[str] = []
    # Whether each open group matches case-blind.
    stack: list[bool] = []
    ci = ignore_case
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\":
            esc = pattern[i:i + 2]
            if esc == "\\Z":
                out.append("$")
            elif esc == "\\A":
                out.append("^")
            elif expand and ci and len(esc) == 2 and esc[1].isalpha() \
                    and esc[1] not in "dDsSwWbBntrfvxuUNpP0":
                out.append("[" + _both_cases(esc[1]) + "]")
            else:
                out.append(esc)
            i += 2
            continue
        if c == "[":
            j = i + 1
            if pattern.startswith("^", j):
                j += 1
            if pattern.startswith("]", j):
                j += 1
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" else 1
            head = "[^" if pattern.startswith("[^", i) else "["
            body = pattern[i + len(head):j]
            if expand and ci:
                body = _class_both_cases(body)
            out.append(head + body + "]")
            i = j + 1
            continue
        if c == "(":
            if pattern.startswith("(?P<", i):
                out.append("(?<")
                stack.append(ci)
                i += 4
                continue
            m = re.match(r"\(\?P=(\w+)\)", pattern[i:])
            if m:
                out.append(f"\\k<{m.group(1)}>")
                i += m.end()
                continue
            if pattern.startswith("(?-i:", i) and expand:
                out.append("(?:")
                stack.append(ci)
                ci = False
                i += 5
                continue
            if pattern.startswith("(?i:", i) and expand:
                out.append("(?:")
                stack.append(ci)
                ci = True
                i += 4
                continue
            m = re.match(r"\(\?<?[=!:]|\(\?<(\w+)>", pattern[i:])
            if m:
                out.append(m.group(0))
                stack.append(ci)
                i += m.end()
                continue
            if pattern.startswith("(?", i):
                raise ValueError(f"unsupported group at {i}: "
                                 f"{pattern[i:i + 12]!r}")
            out.append("(")
            stack.append(ci)
            i += 1
            continue
        if c == ")":
            out.append(")")
            ci = stack.pop() if stack else ignore_case
            i += 1
            continue
        if c == "{" and pattern.startswith("{,", i):
            out.append("{0,")
            i += 2
            continue
        if expand and ci and c.isalpha():
            out.append("[" + _both_cases(c) + "]")
            i += 1
            continue
        out.append(c)
        i += 1
    js_flags = ""
    if ignore_case and not expand:
        js_flags += "i"
    if flags & re.MULTILINE:
        js_flags += "m"
    if flags & re.DOTALL:
        js_flags += "s"
    return "".join(out), js_flags


# ---------------------------------------------------------------------------
# What the extension needs
# ---------------------------------------------------------------------------

def patterns() -> "dict[str, re.Pattern]":
    """The patterns ``src/citations.js`` reads citations with, by name."""
    import citations as c
    import constitution
    import ecfr
    import eng_rep
    import fed_rules
    import federal_register
    import sec_decisions
    import statutes_at_large
    import us_code
    return {
        "pinAfter": c.PINCITE_AFTER_RE,
        "caseCite": c.CITE_CAPTURE_RE,
        "broadCite": c.BROAD_CITE_CAPTURE_RE,
        "broadShortCite": c.BROAD_SHORT_CITE_RE,
        "nominativeCite": c.NOMINATIVE_CITE_RE,
        "nominativeParallel": c.NOMINATIVE_PARALLEL_RE,
        "usNominativeParallel": c.US_NOMINATIVE_PARALLEL_RE,
        "earlyFedCite": c.EARLY_FED_CITE_RE,
        "wlCite": c.WL_CITE_RE,
        "runningHead": c.RUNNING_HEAD_CITE_RE,
        "journalReporter": c._JOURNAL_REPORTER_RE,
        "agOpinion": c._AG_OPINION_RE,
        "courtRuleReporter": c._COURT_RULE_REPORTER_RE,
        "firm": c._FIRM_RE,
        "reportsSuffix": c._REPORTS_SUFFIX_RE,
        "usc": us_code.USC_CITE_RE,
        "cfr": ecfr.CFR_CITE_RE,
        "rule": fed_rules.RULE_CITE_RE,
        "const": constitution.CONST_CITE_RE,
        "constCitation": constitution.CITATION_MARK_RE,
        "stat": statutes_at_large.STAT_CITE_RE,
        "fedReg": federal_register.FR_CITE_RE,
        "engRep": eng_rep.ER_CITE_RE,
        "engRepShort": eng_rep.ER_SHORT_CITE_RE,
        "engRepPin": eng_rep._PIN_RE,
        "engRepBefore": eng_rep.PARALLEL_BEFORE_RE,
        "engRepAfter": eng_rep.PARALLEL_AFTER_RE,
        "engRepReporter": eng_rep._ER_REPORTER_RE,
        # The reporters of the nominate index (not the index itself).
        "engRepNominate": eng_rep.nominate_form_re(),
        "sec": sec_decisions.SEC_CITE_RE,
    }


def tables() -> dict:
    """The reporter and rule tables the patterns are read against."""
    import browser_links
    import citations as c
    import constitution
    import eng_rep
    import fed_rules
    import sec_decisions
    import statutes_at_large
    return {
        # Every known spelling of a reporter (its punctuation-free key) →
        # the spelling the app cites it by.
        "reporterCanonical": {
            k: f.canonical
            for k, f in sorted(c._REPORTER_FAMILY_BY_KEY.items())},
        # …and the Caselaw Access Project's name for it, where it has one of
        # its own (any other is made by rule: browser_links.case_law_slug).
        "caseLawSlugs": {
            k: f.case_law_slug
            for k, f in sorted(c._REPORTER_FAMILY_BY_KEY.items())
            if f.case_law_slug},
        # The early Supreme Court reporters → (U.S. volume offset, volumes).
        "nominativeUS": {
            k: list(v)
            for k, v in sorted(browser_links.NOMINATIVE_US_REPORTS.items())},
        # State reporters renumbered into an official series: name key →
        # [[series, volume offset, volumes], …].
        "stateNominative": {
            k: [list(s) for s in v]
            for k, v in sorted(c._STATE_NOMINATIVE.items())},
        "usReports": {
            "locMax": browser_links.LOC_US_REPORTS_MAX,
            "locPreferredMax": browser_links.LOC_US_REPORTS_PREFERRED_MAX,
            "govinfoMax": browser_links.GOVINFO_US_REPORTS_MAX,
        },
        # Reporters a citation beside an English Reports one is never, how
        # far before one such a citation is looked for, and how far past a
        # case's first page a short form still reads as a page of it.
        "americanKeys": sorted(eng_rep._AMERICAN_KEYS),
        "engRepReach": eng_rep.PARALLEL_REACH,
        "engRepShortSpan": eng_rep._SHORT_SPAN,
        "nonCaseReporters": sorted(c._NONCASE_REPORTERS),
        "plainCaseReporters": sorted(c._PLAIN_CASE_REPORTERS),
        "wordReporterKeys": sorted(c._WORD_REPORTER_KEYS),
        "ruleSets": {k: {"abbr": v[0], "path": v[2]}
                     for k, v in sorted(fed_rules.RULESETS.items())},
        "ordinalWords": constitution._ORDINAL_WORDS,
        "statMaxVolume": statutes_at_large.STAT_MAX_VOL,
        "secCatalogUrl": sec_decisions.CATALOG_URL,
    }


def render() -> str:
    """The text of ``src/patterns.js``."""
    out = {}
    for name, rx in patterns().items():
        if rx is None:
            continue                # an index missing from this copy
        source, flags = to_js(rx.pattern, rx.flags)
        out[name] = {"source": source, "flags": flags}
    body = json.dumps({"patterns": out, "tables": tables()},
                      indent=1, ensure_ascii=False, sort_keys=True)
    return (
        "// Generated by export_extension_patterns.py from the app's\n"
        "// citation patterns.  Do not edit: change the Python and run it again.\n"
        "globalThis.GetCasesPatterns = " + body + ";\n"
    )


def main() -> int:
    text = render()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
