# Handoff — the decision year when a source gives the term's

**Audience:** a Claude Code session running on the user's own computer, with
network access to Google Scholar, CourtListener (the user's API token is in
the app's config) and the Caselaw Access Project (static.case.law). The
cloud session that wrote this could reach none of them, so everything below
that needs a live answer was left for you. Read this through before touching
code.

## The goal (from the user)

Many old cases are given the year of their **term** instead of the year they
were **decided**. A citation gives the decision year (Bluebook rule 10.5).
So the app should detect when a year may be the term's, and then get the
decision year from somewhere else, in this order:

1. a sample of the ways the opinions citing the case cite it;
2. CourtListener;
3. the Caselaw Access Project (CAP).

Steps 2 and 3 are built. **Step 1 is yours.**

## Why it happens

From December 1844 the Supreme Court sat from December into spring; from
October 1873, from October. A term opened in one year and ran into the next.
Google Scholar heads such a case with one year or the other, and not
consistently:

- "48 U.S. 1 (1849)": Luther v. Borden, December Term 1848, decided 1849.
  Scholar gives the decision year.
- "59 U.S. 272 (1855)": Murray's Lessee, December Term 1855, decided 1856.
  Scholar gives the term year.

From about 112 U.S. (1884) on, Scholar's pages print "Decided <date>" and the
year is right. Before 1844 the terms opened in January or February and ended
the same spring, so the two years coincide.

## What is built (`decision_year.py`, wired in `courtlistener_gui.py`)

- **`term_year_uncertain(blocks, year, is_scotus=...)`** flags a year the page
  gives when the front matter prints no decision date and either:
  - the case is the Supreme Court's from 1844–1890, or
  - the front matter has a September–December "… Term, YYYY" line for that
    year.

  Among the stored opinions it flags 21, all Supreme Court cases from
  1849–1884. To list them, run `term_year_uncertain` over `data/opinions.jsonl`,
  using `opinion_db.decision_year_from_blocks` for the year and
  `record["court"] == "scotus"`.
- **`_ScholarTextWindow._compute_bluebook_parts`** sets `bb["_term_year"]` for
  a flagged year that came from the page's own header. A year already known
  is used at once:
  - one in `bluebook_names._DECISION_YEAR_BY_CITE` (Murray's Lessee, ad hoc),
    or
  - one found before (`remembered_decision_year(given, *cites)`). A
    remembered year is used only for a year in doubt, and only if it is
    plausible against it.
- **`_ScholarTextWindow._enrich_citation`** asks, on its worker thread, in the
  `if need_decision_year:` block:
  - CourtListener's cluster `date_filed` (`_cl_court_and_year`), then
  - CAP's `decision_date` (`_case_law_decision_year`).

  An answer counts only if `plausible_year(given, found)`: the given year or
  the next one. A found year goes into the citation, and
  `remember_year(cite, year, source)` keeps it in
  `~/.config/courtlistener/decision_years.json`.
- **`known_decision_year(given, *cites)`** gives the search-result citation line
  (`_bluebook_display_name`) and a scan's header citation the same answer.

## Your task, part 1: verify steps 2 and 3 live

Neither has been run against real data for these cases. For each case in the
table below, check:

- **CourtListener:** is the cluster's `date_filed` the decision date? If
  CourtListener marks it approximate (`date_filed_is_approximate`), skip it in
  `_enrich_citation`, which means fetching that field in
  `_cl_court_and_year`.
- **CAP:** is its `decision_date` the decision's, or the term's? If CAP is
  unreliable for these volumes, drop it from the chain or keep it as a last
  resort. Tell the user what you found.

The years expected below are from memory, not checked. Confirm each one
against CourtListener and the Supreme Court Database (scdb.wustl.edu) before
you use it as a test fixture:

| Case | Cite | Scholar's year | Expected decision year |
|---|---|---|---|
| Murray's Lessee v. Hoboken Land & Improvement Co. | 59 U.S. 272 | 1855 | 1856 |
| McCready v. Virginia | 94 U.S. 391 | 1876 | 1877 |
| Williams v. Bruffy | 96 U.S. 176 | 1877 | 1878 |
| Insurance Co. v. Dunham | 78 U.S. 1 | 1870 | 1871 |
| Gilman v. Philadelphia | 70 U.S. 713 | 1865 | 1866 |
| Luther v. Borden | 48 U.S. 1 | 1849 | 1849 (Scholar right) |
| Fleming v. Page | 50 U.S. 603 | 1850 | 1850 (Scholar right) |
| Civil Rights Cases | 109 U.S. 3 | 1883 | 1883 (Scholar right) |
| Hurtado v. California | 110 U.S. 516 | 1884 | 1884 (Scholar right) |

## Your task, part 2: the citing-opinions sample (step 1)

Put the logic in `decision_year.py`, which must stay tkinter-free, and pass
the network clients in so it can be tested. For example:
`citing_sample_year(client, cite, opinion_ids, given) -> (year, counts)`.
Call it first in the `need_decision_year` block of `_enrich_citation`, where
the comment marks the place. Remember its answer with source `"citing"`.

**What to count.** Count each citing opinion's year for the case's own
citation:
- **Cite forms to match:**
  - "59 U.S. 272", "59 U. S. 272", "18 How. 272"
  - "59 U.S. (18 How.) 272", "18 How. (59 U.S.) 272"
- **Between the cite and the year, allow:**
  - a pin cite (", 276", ", 276-77")
  - a parallel cite (", 15 L. Ed. 372")
- **The year:** "(1856)".

Use `citations.py`, for example `find_case_citation` and the reporter
normalizers, rather than a new reporter regex. Only the years in
`{given, given + 1}` are candidates.

**Decision rule (suggested).** Take a year only if at least two citing
opinions agree on it and it holds a clear majority, say two thirds.
Otherwise fall through to CourtListener's date.

**Where to sample.** Try the options in this order:

1. **CourtListener, recommended.**
   - `_cited_opinion_ids(client, item, case_name, cite)` (courtlistener_gui)
     already finds the case's opinion ids.
   - `case_name_usage` already runs live searches of the form
     `cites:(id1 OR id2) AND "<phrase>"`, so that query shape is known to
     work.
   - Search `cites:(…)` with `type="o"`, take about 10–20 citing opinions,
     fetch their text (`html_with_citations` or `plain_text`) and run the
     matcher.
   - Alternatively, count per candidate year with a phrase or proximity
     query, such as `"How. 272 1856"~4`. Check the syntax against the live
     API first: pin cites sit between the page and the year.
2. **Google Scholar.**
   - `ScholarFetcher.decision_year` already searches `"59 U.S. 272"`. Its
     results page lists opinions citing the case, with snippets that often
     include the cite and the year.
   - Parsing those snippets costs no extra request when that search has run.
   - Snippets can be cut off before the year. Scholar rate-limits; see how
     `_RESULTS_PAGE_RE` treats a blocked page.
   - Skip the result that is the case's own page.

## Tests

- **No network in tests.** Save live responses as fixtures under `test_data/`.
- **Never let a test read or write the real
  `~/.config/courtlistener/decision_years.json`.** Patch
  `decision_year.CACHE_PATH`, as `test_decision_year.IsolatedCache` does, and
  patch `courtlistener_gui._case_law_decision_year` and `_cl_court_and_year`
  wherever a test runs `_enrich_citation` on an old Supreme Court case. See
  `test_ejectment_names.TextViewTests._enrich`.
- **Run the whole suite** (`python -m unittest`, one module at a time under
  xvfb). `test_tray_icon` fails without pynput; that is known.

## Possible follow-ups (ask the user first)

- **The "(____)" path.** A case whose page prints no year at all (`need_year`
  in `_enrich_citation`) gets its year from Scholar's results byline, then
  CourtListener, then CAP. Those can be term years too, but there is no given
  year to check them against.
- **Stored records.** Each record in `data/opinions.jsonl` keeps the year
  Scholar gave it, and the database picker shows that year. The user syncs
  that file: never edit it by hand. If the stored year should change, change
  what the app writes or how the index derives the year.
- **Other courts.** No stored opinion from another court is flagged. Old state
  reports with December terms would be caught by the term-line rule only if
  the page prints the term line.
