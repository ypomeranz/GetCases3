# To-do — from reviewing a cert petition end to end (2026-10-18)

**Source:** *United Biologics, LLC v. Amerigroup Tennessee, Inc.*, petition for
certiorari (June 12, 2026; 300 pages: brief + appendix of the Sixth Circuit's
opinions and the district court's). Two checks:

1. **Citation detection** on the petition's text as the browser extension's PDF
   viewer reads it (pdf.js text layer): the app's detector (`citations.detect_links`,
   which the extension uses whenever GetCases is running) — 630 links — and the
   extension's own JavaScript detector (used only when the app isn't running) — 543.
2. **Reading the cited cases**: 153 of the 156 cited cases opened as the program
   opens them — 109 on Google Scholar's text, 44 on static.case.law's or
   CourtListener's (Scholar began challenging after ~110 fetches 30 s apart) — and
   59 scans (Supreme Court: Library of Congress / GovInfo; lower courts:
   static.case.law, CourtListener) read for the parts rail.

**Working well:** the app's detector (few false positives); separate opinions in
Scholar texts (right in nearly all 30 cases that have them); Supreme Court scans
(30 of 33 exactly right); Bluebook abbreviations mostly right.

Ranked by importance to a user × feasibility.

**Status (2026-10-18):** 2, 3, 4, 6 and 8 done; 5 and 7 done in part (see
each). Re-checked on the petition: 31 more links; the two wrong "Id." links gone.
Then 9, 10, 11, 13, 14 and 15 done, and 16's doubled court (see each). Checked
against the 59 scans (only the item-9 changes), the 1,912 stored Scholar texts'
parts (one change, a fix) and their Bluebook names (14 changes, all intended).

1. **Separate opinions on lower-court scans** — the view a citation clicked in a
   brief opens. Of 14 static.case.law scans with a concurrence or dissent, 10 show
   none; five label the whole case "Syllabus" (Ostrofe, 740 F.2d 739; France v.
   Lucas, 836 F.3d 612; Reid, 790 F.2d 453; the Tennessee cases). Cause: two-column
   pages read as single lines, so the byline sits mid-line ("…plausible ex - KENNEDY,
   Circuit Judge, dissenting:"). Fix: read columns separately (`slip_opinion.group_lines`
   / `detect_sections`). Medium effort.
2. **Appendix page labels break "Id." chains.** "App-13", "App-74", … read as record
   citations to the appendix, so every "Id." after a page break goes unlinked (~30
   lost links here; every cert petition has such an appendix). Easy.
   **Done** — the cause was the blank line the extension's viewer sets between pages,
   read as a new paragraph, not the label itself. Appendix labels ("App-13", "App.
   13", "Pet. App. 13a", "13a", "A-13") are now page furniture, and a blank line
   beside furniture is a page break, not a paragraph break.
3. **"Id." antecedents.** "Id. at 280" and "(Id. at 42-54.)" resolve to 15 U.S.C. —
   a page-style "Id." never means a statute. "Id. (quoting NicSand, 507 F.3d at 450)"
   makes NicSand (the quoted case) the next "Id."'s antecedent. Easy.
   **Done** — "Id. at N" skips U.S.C., C.F.R., rules and state statutes; a citation
   inside "(quoting …)" / "(citing …)" is no antecedent.
4. **Case-name leftovers.**
   - parties after a semicolon kept: *W. Penn Allegheny … v. UPMC, Highmark, Inc.* (627 F.3d 85)
   - docket text kept: "In re Beef Indus. Antitrust Litig., MDL Docket No. 248" (600 F.2d 1148);
     "Mary Ann Reid (No. 84-1189) v. Sears" (790 F.2d 453)
   - doubled party: "Merican, Inc. v. Caterpillar Tractor Co. Caterpillar Tractor Co." (713 F.2d 958)
   - In re caption run on into the adversary caption: "In re Dual-Deck … Litig., Go-Video, Inc." (11 F.3d 1460)
   - personal names in full (static.case.law): "James McCoy (Yazoo) Smith v. Pro Football, Inc." (593 F.2d 1173)
   - CourtListener's short names used as is: "pls.com, LLC v. N.A.R." (32 F.4th 824),
     "Royal Truck & Trailer Sales v. Kraft" (974 F.3d 756), "Boykin v. Fam. Dollar Stores" (3 F.4th 832)

   **Done** — a semicolon ends a party; docket-number and nickname parentheticals go;
   a doubled party collapses; an in-re title ends at its docket designation or after
   "LITIGATION"; a text with no Scholar caption is named from the court's own slip
   caption (the Sixth Circuit's box, the Ninth's docket column). Also: three or four
   capitals run together ("UPMC", "IBM") are a firm's, no longer "U.P.M.C.".
5. **Place names that are part of a business name dropped.** "Blue Shield v. McCready"
   (*Blue Shield of Va.*, 457 U.S. 465), "Werblood v. Columbia Coll." (*of Chi.*, 536
   N.E.2d 750), "Exxon Co. v. Sofec" (*Exxon Co., U.S.A.*, 517 U.S. 830 — Scholar prints
   "U. S. A."), "Aerel v. PCC Airfoils" (*Aerel, S.R.L.*, 448 F.3d 899). Easy–medium.
   **Done in part** — "Exxon Co., U.S.A." and "Aerel, S.R.L." (and S.A.R.L., B.V., K.K.,
   Pty …). **Open question:** the tests have it that *Planned Parenthood of
   Southeastern Pennsylvania v. Casey* is "Planned Parenthood v. Casey" — the reading
   that also makes "Blue Shield v. McCready" and "Werblood v. Columbia Coll." The
   business-name exception, read to cover organizations, would keep all three places.
6. **A state court's vote line taken for a concurrence.** "SHRIVER and CROWNOVER, JJ.,
   concurring." (Mefford, 354 S.W.2d 823) became a part, cited "Shriver and Crownover,
   J., concurring". Easy.
   **Done** — several judges' votes with no writing after them are no part; judges
   writing together are cited "JJ.".
7. **Names the brief's links carry** (used to find the case):
   "Whether Illinois Brick Co. v. Illinois" (the question presented); "Illinois Brick.”
   Motorola Mobility…" (a sentence's end before it); "Primary Care v. …" (lost
   "Academy of Allergy & Asthma in"); "Sixth Circuit, 155 F.4th 795" (named
   "Sixth Circuit"); "App-23 NYNEX Corp.", "United Biologics, App-11 LLC" (page
   labels inside); "Cont'l T.⏎V., Inc." (cut at a line break). Medium.
   **Done except "Primary Care"** — sentence-opening words stop a name; a period
   inside a closing quotation mark ends one; a court is no lone party; a capital "V."
   is an initial; page labels are furniture (item 2). Left: "in" stays a stop word —
   letting it join names would make "the Court in Brown v. Board" name the Court.
8. **The lead opinion's label.** Scholar texts often give the full name ("Opinion
   (Myrna Pérez, J.)", "Opinion (Karen Lecraft Henderson, J.)"); Tennessee's
   "BARKER, J., delivered the opinion of the court, in which …" isn't read, so the
   label has no author; static.case.law / CourtListener texts are inconsistent
   ("Opinion (KAREN NELSON MOORE, Circuit Judge)", "Opinion (Justice Thomas)",
   "Opinion (PER CURIAM.)", or none). Easy.
   **Done** — surnames throughout (Census given names count; "R. Nelson" keeps its
   initial; a judge the bench names by surname is not kept in full); the state-court
   and run-on bylines read; static.case.law and CourtListener labels refined too, from
   the author their own labels name. Also: accented capitals cased ("PÉREZ" → Pérez).
9. **Scan rail labels.** Hemi Group (559 U.S. 1): the concurrence labelled Sotomayor —
   the name read off "Justice Sotomayor took no part…", the line above Ginsburg's
   byline. OCR spellings "Rennquist", "Oconnor" (check against the bench). A cited
   dissent read as a part: "(Sotomayor, J., dissenting from denial…)" in Hodge v.
   Plappert (136 F.4th 648). Easy–medium.
   **Done** — a name followed by a sentence's verb ("took no part", "observed") opens
   no attribution; a running head's name is read against the roster of Justices
   (Rehnquist, O'Connor); "concurring in judgment" heads read "in the judgment".
10. **static.case.law's own split can be wrong.** Ashcroft v. Iqbal: Souter's dissent is
    stored inside the majority. Run byline detection inside each static.case.law
    opinion. Medium.
    **Done** — each static.case.law opinion, and each CourtListener sub-opinion, is
    read for writings its bylines open inside it, which become parts of their own.
11. **Recent cases without Scholar.** 707 S.W.3d 581 (Mo. 2025), 719 S.W.3d 525 (Tex.
    2025), 155 F.4th 859 (6th Cir. 2025): no text — static.case.law stops at 2018 and
    CourtListener's citation lookup didn't know them. Search CourtListener by name,
    court and date when the cite lookup fails. Also: no scan found for the two
    Amerigroup Sixth Circuit opinions (155 F.4th 795, 164 F.4th 529). Medium.
    **Done** — when nothing bears the citation, CourtListener is searched by the
    parties' names (abbreviations in every spelling: "Acad." is Academy or Academic),
    in the citation's year, among the courts the reporter prints (S.W.3d: Missouri's,
    not Georgia's); a party of initials must have the record's. All five recent cases
    now open with text and with the court's slip PDF. Reading them turned up, and
    fixed: a paragraph opening "Justice Roberts has said:" read as a heading
    (Randolph); Missouri's writings, headed by their title alone and signed at the
    end ("DISSENTING OPINION" … "Paul C. Wilson, Judge"), in the text and on the
    scan; the Missouri caption's ")" column, lettered docket number and court
    heading in the name; the Sixth Circuit's en banc order, its statement
    "respecting the denial" and the "with whom … join" concurrence on the scan;
    Texas's line-up ("JUSTICE YOUNG filed a concurring opinion") read as writings;
    a lead opinion's author named after the caption or only where the writer signs.
    Werner's concurrence and dissent are not on CourtListener at all.
12. **The extension without the app** (`browser_extension/src/citations.js`). Wrong
    targets first: pins without "at" ("McCready, 457 U.S. 476" → nine links to a case
    at 476); the brief's page numbers inside a citation ("15 U.S.C.⏎⏎13⏎§15(a)" → § 13,
    "431 U.S.⏎⏎25⏎at 729" → 431 U.S. 25, "587 U.S. at⏎⏎33⏎282" → pin 33). Then
    what's missing: "Id.", footnote pins ("n.5"), pins after a full cite with a
    footnote, Supreme Court docket cites, state code sections. Medium–large.
    Underlying much of it: the extension has none of the app's page-furniture
    handling (`citations.scan_text`).
13. **"Concurring in part and dissenting in part"** writings (static.case.law,
    CourtListener) labelled "Concurrence in Part" and coloured as concurrences. Easy.
    **Done** — they are dissents ("Concurrence in Part & Dissent in Part"), cited
    "concurring in part and dissenting in part".
14. **Capitals.** "Hctec Partners" (676 S.W.3d 619): an anchored 1–1 tie between
    "HCTEC" and "HCTec" goes to the capitals; the 161 bare "HCTec"s are never counted.
    "FCA Us, LLC" (44 F.4th 548). Easy. Also "Pls.com" (32 F.4th 824, from the slip
    caption): a word with a period inside is never weighed against the text.
    **Done** — a tie goes to the spelling the opinion writes most anywhere, then to
    mixed case; a neighbor in capitals counts where the caption keeps it so and the
    match stands in a sentence; a word with a period inside is weighed whole (also
    fixes "Georgia v. Public.Resource.Org, Inc.").
15. **Small name rules.** "ex rel." relator dropped (*Gieseke ex rel. Diversified Water
    Diversion, Inc.*, 844 N.W.2d 210); "Wilgar Land Co. v. Director" (*Dir., Off. of
    Workers' Comp. Programs*, 85 F.4th 828); "Inc." lost before "d/b/a/" (Trau-Med,
    71 S.W.3d 691) and Realty One's "Inc." (173 F.3d 995); "Re/max". Easy.
    **Done** — rule 10.2.1(b)'s "similar expressions" are "ex rel." ("on behalf of",
    "for the use of", "by and through his next friend …"), but not a class's or an
    association's ("on behalf of himself and all others", "of its members"), a
    clause naming no one ("by his guardian ad litem, et al."), or a co-party's; an
    officer named by the office keeps it; a trade name, a docket number or a
    co-party run on after "Inc." no longer takes the "Inc." along; "Re/Max".
    **Note:** applied this way, *Schipper v. Levitt & Sons* (44 N.J. 70) is now
    "Schipper ex rel. Schipper", *West Virginia v. B.P.J.* "West Virginia v. B.P.J.
    ex rel. Jackson", and *Barron v. Baltimore* "Barron ex rel. Tiernan" — the rule's
    form (as the petition cites *D.J. ex rel. R.J.*), not always the familiar one.
16. **State cases cited to the state reporter.** "350 S.C. 7 (Ct. App. 2002)", "2 Cal.
    5th 505 (2017)", "207 N.C. 545 (N.C. 1935)" rather than S.E./P./S.E. — the same
    pin-page question as S. Ct.; the doubled "N.C. … (N.C." is an easy fix.
    **Doubled court done** — the court is worked out for the reporter shown, so "207
    N.C. 545 (1935)" (and "409 Mich. 672 (1980)"). The reporter choice stays: the one
    whose pages the text gives.
17. **Scholar's own data errors.** Exxon (517 U.S. 830): another case's Souter dissent
    appended (star page *1045; Exxon was unanimous). Low.
18. **Minor.** A docket number split across lines ("No. 1:21-⏎cv-3") isn't read, so the
    WL cite isn't sent to RECAP; on an orders page (568 U.S. 1140) CourtListener's
    first case was taken over the named one; Scholar is searched for 1910 lower-court
    cases, which it doesn't cover (it starts in 1923).
    **Done** — a hyphen ending a line inside a docket number joins the next line, in
    the app and the extension (Zitzow, Assist-2-Sell, and C.S. Sewell's docket-only
    cite now go to RECAP). The orders page was right after all: 568 U.S. 1140 grants
    Chadbourne & Parke's, Willis's and Proskauer's petitions in one entry, filed under
    Chadbourne, and rule 10.2.1(a) cites a consolidation by its first-listed case. A
    case named only in an entry's full name is now matched there, so it is found even
    when its parties share nothing distinctive with the first. Scholar is not asked
    for a lower federal court's decision before 1923 (Federal Cases, the circuit
    reporters reprinted in them, F. first series), which static.case.law supplies.
19. **Supreme Court cases cited to S. Ct.** when Scholar's text is paginated so (Apple
    v. Pepper, Lexmark, Bridge, Sekhar, …). Last, by the user's choice: U.S. cites come
    from the official scans when copying.
