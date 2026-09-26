CourtListener GUI – Case Law & Legal Research Tool - (Code and this readme file created by AI)

A desktop application (Tkinter) that searches U.S. case law, statutes, regulations, and historical legal materials. It pulls opinions from **CourtListener** and **Google Scholar**, and provides in‑app viewers for:

- Federal & state case law (Supreme Court, circuit courts, district courts, state appellate courts)
- U.S. Code, Code of Federal Regulations (CFR)
- Federal Rules (Civil, Criminal, Evidence, Appellate, Bankruptcy)
- U.S. Constitution (full text, searchable)
- California & Florida statutes (more states coming)
- U.S. Statutes at Large (official PDFs)
- English Reports (pre‑1865 cases from CommonLII)
- Federal Cases (pre‑1880 lower federal opinions cited by case number, resolved via CourtListener)
- Supreme Court case details (Oyez – summaries, vote splits, oral argument audio)

---

## Getting Started

### 1. Install Python 3.9+ and dependencies

```bash
# Clone or download the source code, then install required packages:
pip install requests beautifulsoup4 pypdfium2 Pillow pynput curl_cffi browser_cookie3

# English Reports scans are fetched with Firefox's CloudFlare clearance, so
# Firefox itself is the other thing they need.
Note: The app will prompt you to install missing packages on first run.

2. Get a CourtListener API Token
Go to CourtListener.com and create a free account.

Once logged in, visit your API settings page and copy your API token.

Launch the app – with no token saved, it opens a setup dialog on startup that explains where to get one, offers a “Get a token…” button, and checks the token against the API before saving it. “Skip for now” dismisses it (everything except CourtListener still works), and “Don’t ask again” stops it from reappearing.
You can enter or change the token at any time via Settings → API Token… in the menu bar.

The token is stored locally (~/.config/courtlistener/config.json) and is used for all CourtListener API requests. Setting COURTLISTENER_TOKEN in the environment overrides the saved token and suppresses the startup prompt.

3. Run the application
bash
python courtlistener_gui.py
On first start, the main window will be hidden – it runs in the background.

Press Ctrl+Space (or Cmd+Space on macOS) to open a quick‑search popup.

The popup ranks its results by how close each case’s **name** is to what you typed, and drops the rest – which is right for a caption (“Roe v. Wade”) and wrong for a subject. So when that ranking leaves nothing (no rows, or a single stray one) the query is read as a **phrase** instead: Google Scholar’s own first results page is shown as it stands, in Scholar’s relevance order, and the status line says so. A query carrying a reporter citation is never re‑read this way – it names one case, so finding none of it is a real miss rather than something the ranking did.

Type s + Enter in the terminal to show the main window, or q + Enter to quit.

How to Search
Main Search Window
Enter a search query (e.g., "Roe v. Wade", "Fifth Amendment", "42 U.S.C. § 1983").

Optionally filter by:

Court – click the “Courts: All ▾” button to select specific courts.

Date range – use the “Filed from:” and “to:” fields (YYYY‑MM‑DD).

Max results – number of results per page (default 20).

Press Search or hit Enter.

Results appear in the left treeview; click a row to preview the snippet.

Double‑click a row to download the opinion as PDF (or .txt if no PDF is available).

Click **Scholar** to fetch the full opinion from Google Scholar (often richer than CourtListener’s HTML); the button on the Scholar view reads **CourtListener** and switches back.

**Where the text comes from when Scholar has none.** Google Scholar is asked first for every case, and where it answers nothing else is consulted. Where it does not, the app opens **static.case.law** — the Caselaw Access Project's scan of the printed report — in preference to CourtListener: it is the reporter's own pages, so the star pagination on screen is the pagination the citation names, and it carries the head matter, the arguments of counsel, the separate writings and the footnotes as the volume printed them. **Which reporter, though, is not a matter of taste.** A case commonly carries three or four parallel citations and CAP scanned each reporter separately, so every one of them is a different set of pages for the same opinion — and the star pagination of the wrong one names pages you are not looking at, which would make every pin cite taken from it wrong. So wherever a scan is already on screen, **that scan decides**: a static.case.law scan is answered from its own file (same volume, same page, same numbered opinion), and any other scan — a U.S. Reports scan from the Library of Congress or GovInfo — only from CAP's copy of the reporter it prints. Where CAP has not got that reporter, CourtListener answers rather than a parallel printing. With no scan on screen, an official reporter leads the vendor series (S. Ct., L. Ed.) — the same reporter the PDF button would reach for — and the reporter the text was read from then leads the window's citation list, so the scan that button finds is the scan of the very pages showing.

CAP's scans stop with the volumes published in 2018, so **CourtListener's text is what answers for anything more recent** — and for anything CAP never held. This order holds wherever the app has to choose a text: opening a case from the results list or from Spotlight, following a citation out of an opinion or a brief, and the **T** button behind a scan.

In the opinion text, each separate writing is marked two ways and no more: a light tint behind it (red for a dissent, green for a concurrence, neutral grey where the role isn’t known) and a colour‑coded strip down the right naming every part – click one to jump to it. The strip has room for a surname at most, so resting on a part names it in full – the kind of writing, who wrote it, and the reporter page it starts on (“Dissent — Rehnquist — p. 171”). The slim rail that replaces this strip in Reporter View carries no such tip – pointing at the scrollbar there names every writing at once, as below. A writing that begins at the very end of the opinion keeps its place on that strip rather than being drawn off the bottom of it.

**Side panel** (or press **s**) opens the case‑details panel beside the opinion – the Oyez line‑up and summary for a Supreme Court case, CourtListener's own record for everything else. In the floating viewer the panel stands in a small window of its own against the viewer’s right‑hand edge (see below); in an ordinary text window it appears inside, taking its width from the opinion. Either way a **Show** selector at the top swaps it to another view: the Supreme Court **docket**, the Court's recent decisions, the case's appellate family, or the opinion's outline.

The app wears one interface: **Reporter View** — the reports themselves. A case opens as the **scan**, in the small floating viewer described below, and **T** turns it into the text.

**Reporter View answers with the report.** Opening a case — from the main search results, from Spotlight, from a citation in a brief, or from a link in the opinion text — looks for the scan first, by the same routes the **PDF** button uses: the official U.S. Reports scan whenever a U.S. citation is known, the Harvard static.case.law scan otherwise, the slip-opinion archive for a decision too recent for any reporter, and CourtListener’s stored copy last. Only when none of them has it does the case open as text — and then in the same window, in the same place on the desktop, at the same size, with the same strip: it reads like the scan’s window with the pages missing, not like a different kind of thing. A citation followed off the **T** side is looked up the same way, so a case reached from the text lands where one reached from the pages would.

Sources printed only as pages — Statutes at Large, the English Reports, a docket entry from RECAP — open in that viewer too, without the **T** button, since there is nothing to switch to.

**The strip’s right‑click menu is this window’s menu bar.** Under Save As… and Print… it carries a bookmark entry for whatever is showing (the opinion when the text is up, the document behind the pages when it is not, named for what that is — “Bookmark This Statute”, “Remove Bookmark for This Case”), then **Recent** — the same History list every other window carries — and Close at the foot. All of it is rebuilt each time you open it, so it always describes what the window is showing now. Sources that exist only as text — the CFR, the Federal Rules, the U.S. Code, the state codes — open in ordinary windows of their own: there is nothing about them a reporter view would improve.

**Every window here stands on its own.** Close the case you followed a citation out of and the case you followed it to stays exactly where it is, along with the statute you opened beside it and every window further down that chain — none of them is a child of any other, so only quitting the app closes them.

The scan opens in a small window of its own, on the left of the desktop – to the reader’s left where there is room, to its right where there is not – the page under one thin strip carrying small **save** and **print** icons, the zoom controls (**−**, **+**, **Fit**, and Ctrl/Cmd +, −, 0) and the **T** button described below, and nothing else. The window is named for the case in Bluebook form, cited to the reporter the pages on screen actually print rather than to every parallel reporter the case carries; for a case you reached by following a citation the name is filled in from the opinion text fetched in the background. Citations in the page are still clickable, Ctrl/Cmd+F still searches it, and Save (Ctrl/Cmd+S), Print (Ctrl/Cmd+P) and Close (Ctrl/Cmd+W) are also on the strip’s right‑click menu. Saved and printed files are named the same way.

When the opinion in that window carries **separate writings**, a slim rail appears just inside the scrollbar mapping them: each part covers the stretch of the document it occupies, in its own color (blue for the Court’s opinion, green for a concurrence, red for a dissent, grey for a syllabus), with a solid marker on its first line. Dragging the scrollbar you can see how far down the dissent starts, and clicking a band jumps straight to it. **Pointing at the scrollbar names every writing at once** – each author's surname in caps, in the part's own colour, set directly to the left of the band that writing begins at, the Court's own opinion among them (“ROBERTS”, “KAGAN”, “THOMAS”; “PER CURIAM” for an opinion in the Court's name, and the kind of writing where the heading names nobody). A syllabus is nobody's writing and is left unnamed. The names are up the moment the pointer reaches the scrollbar or the rail beside it – no click needed – and stay for as long as it is there or the thumb is held; a second or so after it leaves they fade back into the page, so nothing is left sitting over the opinion. Reaching past one with the pointer takes them down at once rather than swallowing the click. They are what a hover tip naming one band at a time used to do, and they say it all at once and in place, so that tip is gone. The parts come from the same detector the **Side panel** parts list uses, and the rail appears only for an opinion that actually has a concurrence, dissent or other separate writing – a lone majority gets no rail.

**A case Reporter View could find no scan of keeps looking.** It opens on the text with **P** on the strip, greyed out, while the fuller search runs behind it — every route the PDF button uses, now against the loaded opinion, which knows the case's parallel citations, its court and its date rather than just the citation that was clicked. If a scan turns up the pages go straight into that window, **P** comes alive and the window takes the case's Bluebook citation; if none does, the button stays grey and says so when you rest on it.

**The window is named for what is on screen.** Once the text has loaded — from Google Scholar, from static.case.law, or from CourtListener — the title becomes the case's Bluebook citation, cited to the reporter *these* pages print: a Supreme Court Reporter scan is called by its S. Ct. pages, not by the U.S. Reports pages it does not carry. The case name comes from the **opinion's own caption**, read the same way every other window in the app reads one, rather than from the docket caption a search result or a stored record carries — the reports print “Manuel v. City of Joliet” where the court filed “Elijah Manuel, Petitioner v. City of Joliet, Illinois, et al.” Everything else about the case — its court, its date, its parallel citations — still comes from the result. Saved and printed files are named the same way.

**Following a citation lands on the page it names.** "410 U.S. at 153" opens the scan at page 153, counted from the case's own first page and then checked against the numbers printed in the running heads, which corrects for a cover leaf. A pin cite in a *different* reporter from the file that was found — "93 S. Ct. at 710" against a U.S. Reports scan — gets no jump rather than a wrong one, since one reporter's pages say nothing about where another breaks.

**T on the strip reads the opinion instead of the scan.** The text loads into the same window in place of the pages – the copy fetched in the background while you were looking at the scan, so there is nothing to wait for – and the button becomes **P**, which puts the pages back. Switching either way is instant: neither surface is thrown away, and the scan keeps its zoom while the text is showing. Google Scholar does not carry every case — an unpublished disposition, a very recent decision, a state reporter it never scraped — and then **the report on static.case.law is what T shows instead**: Harvard's own text of the very pages on screen, read out of the formatted copy CAP publishes beside each case, so it arrives with its head matter, its separate writings, its footnotes and its reporter page breaks rather than as one undivided block of prose. The file it reads is the one the pages on screen were made from, so the text and the scan break to the same page at the same word. **CourtListener's text answers for a case CAP does not hold** — every decision since its scans stop, chiefly — and for a scan whose own reporter CAP never scanned, assembled behind the pages exactly as the Scholar copy would have been. Either one names the case as well: what it carries is the caption, the parallel citations, the court and the decision date the window's Bluebook title is made of, which the citation you clicked cannot supply on its own. Scholar is still asked first, being the better-typeset copy and the one every other view of a case prefers; only a case none of them has leaves the button saying so.

**Each switch lands where you were.** T opens the text at the passage the pages were open at, and P opens the scan at the page the text was scrolled to, using the same opinion-to-pages alignment the case window's own PDF/Text switch uses. Working that alignment out takes a moment in the background, so a very early first press may land at the top instead – it catches up as soon as the alignment is ready, unless you have started scrolling, in which case it leaves you where you are.

The text runs the full width of the window: instead of the labelled parts strip the case window uses, the separate writings are marked on the same slim colour rail beside the scrollbar the scan uses (click a band to jump to that part; pointing at the scrollbar – or at the rail itself – names every one of them in caps beside its own band), and the reporter page numbers keep a narrow gutter on the left, cut to exactly four figures across — as far as any reporter in ordinary use runs — with the type shrinking only for a page number longer than that. Both page tracks share that one column: where a U.S. Reports page recovered from the scan falls on the same line as the source’s own star page, the U.S. page keeps the line and the other moves to the line above, or below when the line above is taken. The strip follows what it is showing – **−** and **+** size the type rather than zooming the page (the readout says points instead of percent), **Fit** gives its place to a **Copy** menu offering the same copy styles the case window carries – led by **Edit citation…**, since everything under it copies *that* citation and this menu is the only way to it here – the save icon writes the opinion out as RTF, and the print icon typesets it with LaTeX. Ctrl/Cmd+F searches whichever surface is on screen — and pressing it again puts the find bar away, in every window that has one.

**s opens the case's details beside the window** – over the pages as much as over the text, since it is the same case either way. The panel is the one the case window carries on its right (the Oyez line‑up and summary for a Supreme Court case, CourtListener's own record for everything else, with the **A−**/**A+** buttons that size its type), but it stands in a small window of its own against this one's right‑hand edge rather than inside it: nothing here is resized, re‑laid out or re‑rendered by opening it, and the pages do not move under you. It follows the window about, and a maximized window – which has nothing to its right – gets it against the right of the desktop instead, over the edge of the pages. It opens on the case's details, and for a Supreme Court case its **Show** selector offers the **docket** behind them – the cert- and merits-stage briefs the Court's own docket and SCOTUSblog carry between them, colored to match the booklet covers. The rest of the views the case window offers (Recent SCOTUS, related cases, the outline) answer questions that want a window's room, so they stay there; a case from any other court has no docket to show and gets no selector at all. Press **s** again, Esc, or Ctrl/Cmd+W in the panel to put it away – it is kept, so opening it again costs neither a rebuild nor a second lookup – and it is on the strip's right‑click menu as **Case Details**. A window showing something with no case behind it (the Statutes at Large, an English report) has no details to show and ignores the key.

A PDF viewer is a window in its own right: **closing the opinion text leaves it open**, and Save, Print, **T** and the citations on its pages keep working.

**Ctrl/Cmd+S saves whatever case is in front of you**, and means by that what the window is showing: the scan written out as a PDF where the pages are up, the opinion written out as Rich Text where the text is – the same two things the strip's save icon does, and the first entry on a case window's **Export** menu. The Cmd half of every such accelerator is bound on macOS only: Tk treats `Command` as `Mod1`, and its Windows port sets `Mod1` from the **Num Lock** toggle, so a `<Command-s>` binding on Windows matched a *bare* "s" – beating the window's own **s** and saving the case instead of opening the panel beside it (and a bare "w" closed the window).

**Print asks which printer.** On macOS the system’s own print dialog comes up. Everywhere else the app lists the machine’s printers in a small dialog of its own, with a way straight into the chosen printer’s **own settings** – where duplex, paper size and quality live, since those belong to the printer and not to us – and, where it can honour the request itself (CUPS), a **Print on both sides** box so the common case needs no detour. If neither can be done, the document opens in the system’s PDF viewer to print from there, as it always did.

On **Windows** there is no general way to print a PDF to a chosen printer from outside an app: the shell’s “print to” only works if the program that owns PDFs supports it, and the one that owns them by default (Edge) does not. So three things are tried in turn – that shell verb; a PDF reader installed on the machine that does take a printer on its command line (SumatraPDF, Adobe Reader/Acrobat, Foxit, PDF‑XChange); and finally ordinary Print, which goes to the **default** printer and is therefore used only when that is the printer you picked. If none of them can reach it the dialog says so and points you at Open in Viewer.

What Print sends is the pages **as the viewer shows them**: each one cropped to its content, a redacted case.law scan whitened so the black bars don’t drink ink, and the running head re‑lettered with the citation the redaction took off it. **Save writes that same file** – so what you file away is what you would have printed, not the scan with its wide uneven borders as it happened to arrive.

The crop is **lossless**, and the bars are painted out **on the page** rather than on a picture of it, so the pages keep their own resolution and their text. That last part matters beyond being able to search the file afterwards: handed a PDF with no text in it at all, a PDF reader is entitled to conclude there is text to be recovered, and older Acrobat duly runs its own OCR over the pages and then prints the “suspect word” highlighting it produces, all over the opinion. (A file of images is also many times the size; a nine‑page scan came to 674 KB as pictures and 10 KB kept whole.)

**Clicking a citation on a page of a PDF opens the cited case’s own PDF**, in a viewer of its own, so you can follow a chain of authority through the reports as they were printed. The scan is looked for by the routes the **PDF** button already uses: the official U.S. Reports scan whenever a U.S. cite is known – including one recovered through CourtListener’s parallel citations from a Supreme Court Reporter or nominative cite – the Harvard static.case.law scan for everything else, supremecourt.gov’s slip‑opinion archive for a decision too recent to be in any reporter (matched on its docket), and CourtListener’s own stored copy last of all. A citation with no scan anywhere opens as text, exactly as it always did, and a statute or rule opens in its own viewer as before.

When an opinion is opened from the local database, a **U.S. Reports citation stored with it is the first thing the PDF lookup tries** – wherever it sits in the record’s list of parallel cites – rather than the app going to CourtListener or Google Scholar for a citation it already has.

**Two opinions occasionally begin on the same page of the U.S. Reports**, and then the citation cannot say which one you want: 71 U.S. 2 is where both *Brobst v. Brobst* and *Ex parte Milligan* begin. Each collection files the second one beside the first – the Library of Congress appends a letter to the file name, GovInfo a number – so the app looks for that sibling whenever it opens a U.S. Reports scan (one HEAD request; a page with one opinion on it costs nothing more) and, finding one, reads the **case name each PDF carries in its own title** and opens the one you actually asked for. Where the name settles nothing – it matches neither, or fits both – the first scan opens as it always did and both are listed on the window’s **PDF ▾** menu, named, for you to choose between. A second file that will not name itself is treated as no second file at all.

**The same goes for a case looked up on Google Scholar by its citation.** Any reporter can open two cases on one page – *NetChoice, LLC v. Fitch*, 145 S. Ct. 2658 (2025), shares its page with another case – and a search for the citation then lists both. The app takes the one whose name answers to the name the citation came with: the caption highlighted with a citation you click, the name a Google Scholar link carries, or whatever you typed before the citation in Spotlight or Quick Look Up. However much of the name there is will do – *Fitch* alone picks out *NetChoice, LLC v. Fitch* – and a Bluebook short form counts as the name it abbreviates (*Nat’l Insts. of Health* is *National Institutes of Health*). The year in the citation’s parenthetical settles anything the name cannot. A copy of the other case already on hand, cached or in the opinion database from an earlier lookup, is no longer opened in its place, and where Google Scholar lists only other cases at the page, the lookup goes on to static.case.law and CourtListener rather than open one of them. A citation with no name to go on opens Scholar’s first result, as before.

**A Google Scholar link to a “How cited” page is followed by its citation.** Where Scholar has matched a citation to no opinion it holds, it links it to the page listing the passages that cite the case – as *Nash v. United States*, 229 U.S. 373, links *Commonwealth v. Pierce*, 138 Massachusetts, 165 – and that page is never the opinion. So the case is looked up by the citation instead: its scan or its text on static.case.law, else CourtListener’s, with Scholar searched once by the citation in case it holds the case under another record. Where the link’s own words give no citation the app can read, the one Scholar prints atop the “How cited” page is used. The older way of citing, with a comma between the reporter and the page (“138 Massachusetts, 165”, “1 Cranch, 137”, “112 U.S., 178”), reads as a citation like any other – unless the opinion has already cited that volume in full a few pages earlier, when “131 S. Ct., 1157” is a pin cite that has dropped its “at”.

**Nineteenth‑century reporters are read by the names old opinions give them.** The U.S. circuit reports reprinted in Federal Cases – “5 Sawyer, 155”, “3 Dillon, 393”, “12 Blatchford, 553”, “4 Washington Circuit Court, 371” – are looked up by their abbreviations (5 Sawy. 155 is *In re Ah Yup*), which is how CourtListener files them; state reports never renumbered – “9 Paige, 507”, “4 Denio, 287”, “3 Harris & McHenry, 554”, “10 Sergeant & Rawle, 240”, “13 Louisiana Annual, 175” – reach static.case.law’s folder for each; and those later renumbered into their state’s series are looked up there: “5 Iredell, 250” is 27 N.C. 250, “3 Greenleaf, 326” is 3 Me. 326, “2 Dutcher, 215” is 26 N.J.L. 215. A reporter named for two men (“10 Serg. & Rawle 240”, “4 Gill & J. 1”) is read at all now, and “Reports” written after a name (“18 Pick. R., 210”, “15 Missouri Reports, 576”) is only that. A name that is also a word – “Woods”, “Bond”, “Paige” – counts only where a citation’s volume stands, and “Washington” in full is left alone in its first four volumes, which old opinions use for Washington’s Circuit Court Reports rather than the state’s.

**Google Scholar is asked again only when it could not be asked.** A block, a cooldown or a timeout is retried in the background, and the window’s **Scholar** button lights up if the opinion comes through. An answer is not retried: a “How cited” page, a page not found, or a search on which nothing is the case cited means Scholar has no copy. The status line says so, and the lookup goes on to static.case.law and CourtListener, trying Scholar once more only under the case’s parallel citations.

In every PDF view, **← and → turn the pages** – forward to the next page, back to the top of the page you are reading and then to the one before it, so neither key can carry you past a page you have not seen. ↑ and ↓ scroll as they always have. The keys work wherever they are pressed in a window showing a scan, without clicking the page first; on the text side of a window they stay the reader’s own.

Zooming in past the width of the window brings up a horizontal scrollbar so the whole page stays reachable: pan with it, with Shift+wheel (or a trackpad’s sideways swipe), or with Shift+← and Shift+→ after clicking the page. Zooming keeps whatever column is in the middle of the view rather than jumping to the margin, a search result off to one side is panned into view along with being scrolled to, and the bar disappears again as soon as the page fits. **Fit** (or Ctrl/Cmd+0) returns to a page sized to the window – and the page is re-fitted whenever the room it has changes, so it is never left clipped by a window narrower than it.

Quick Lookup (Look Up menu)
Instant citation lookup: paste a case citation (410 U.S. 113), a statute (42 USC 1983), a regulation (29 CFR 1614.105), or a Federal Rule (FRE 404), and open the source directly. It carries no keyboard shortcut: Ctrl/Cmd+S is the **save** key wherever a case is open, and a lookup box is not what that key should reach for.

Open Citation List
Bulk‑open multiple citations – one per line (case names optional).
The app resolves each one via Google Scholar and then CourtListener.

Browse Briefs (Ctrl+B)
Open a PDF, Word, RTF, or text brief – all citations are highlighted and clickable, linking directly to the cited source.

**A brief's furniture is not cited.** The dot leaders of a table of contents are no reporter (“ARGUMENT........ 6” is not volume 2, page 6), and a footnote mark – read off the PDF as a digit set small and raised – is no part of a citation: not a volume in front of a record cite (“…injury.⁴ Appl. 6, 31”), not more of a page after one (“183⁴”). A case's link starts at its name, not at the table-of-authorities heading above it or a signal like “(Citing”. Short forms that leave something out still open the right page: “Wilcox, 145 S. Ct. 1417” (the “at” missing) is page 1417 of Trump v. Wilcox, and “Nken, at 433” page 433 of the Nken v. Holder the brief cites in full – but only where the name picks out that one case. A Supreme Court decision cited by docket number – “Trump v. California, No. 26A139, slip op. at 4 (U.S. Aug. 24, 2026)”, and its later “Trump v. California, slip op. at 2” – opens the Court's slip opinion when its archive lists one for that docket and date, and the case's supremecourt.gov docket otherwise. An “Id.” never reaches past a citation the app cannot open (an article, a case the brief never cites in full) to link an earlier one.

Following a pin cite
A citation with a pinpoint page opens the cited case and jumps to that page. A citation to a footnote – “200 U.S. 12, 13 n.4”, “13 n. 4”, “13 nn.4–5”, “13 & n.4” – is read as a whole: the note number is part of the link, and following it lands on note 4 rather than on the page its reference sits on. Where a report carries several writings that each begin their notes at 1, the page in the pin cite is what says whose note 4 is meant. Short forms (“542 U. S., at 254 n.9”) and “Id., at 23 n.2” work the same way.

What Sources Are Included
Source	Description
CourtListener	Full‑text search across U.S. federal and state court opinions. Provides PDFs and structured opinion text.
Google Scholar	Opinion text with formatting, citations, and separate opinions (majority, concurrence, dissent). Used as primary text viewer.
static.case.law (Harvard CAP)	Scans of the printed reporters, and the text read off them. The scan is what Reporter View opens; the per-case JSON beside it carries the case’s metadata (name, court, decision date, parallel citations) and the HTML beside that carries the report itself — head matter, separate writings, footnotes and reporter page breaks — which is the text the app shows for any case Google Scholar lacks, in preference to CourtListener’s. Coverage ends with the volumes published in 2018.
U.S. Code	Current law from the Office of the Law Revision Counsel (OLRC). Renders with indentation and enumerator hierarchy.
Code of Federal Regulations	eCFR API – current regulations, section‑by‑section.
Federal Rules	Civil, Criminal, Evidence, Appellate, Bankruptcy – from Cornell LII.
U.S. Constitution	Full text with article/amendment navigation; detects both formal citations and prose references.
California & Florida Statutes	Official texts from the state legislatures (CA LegInfo, FL Senate). More states can be added.
Statutes at Large	U.S. Statutes at Large (GovInfo PDFs) – cited as 88 Stat. 1932.
US Reports PDFs	Official Supreme Court opinion scans: GPO’s GovInfo (vols 2–583) first, the Library of Congress CDN (vols 1–542) as fallback; for vols 584+ the app downloads the Court’s own bound‑volume / preliminary‑print PDF from supremecourt.gov into the “US Reports” folder (once per volume) and carves the cited opinion out of it. If those sources have no PDF for a post-2020 decision, the app matches its docket, citation, or caption and date against the Court’s slip-opinion archive, with CourtListener as the final fallback. A recent opinion reaches Google Scholar with the Supreme Court Reporter’s star pagination, or with none at all, so its U.S. Reports pages are worked out by matching the text against that scan – and the result is then **saved with the opinion in the local database**, so the next time it is opened the pages are there at once and a pin cite goes straight to its page. The scan is still fetched in the background and the saved pages replaced if the alignment has moved on (a preliminary print superseded by the bound volume).
English Reports	Pre‑1865 English case law from CommonLII – offline index + CloudFlare‑aware PDF download (via Firefox's clearance cookie). Cited by the reprint ("156 Eng. Rep. 145", its short form "156 Eng. Rep., at 151") or by the original reports under their abbreviations or their reporters' names as American opinions print them ("9 Exch. 341", "1 Strange, 481", "2 Mylne & Craig, 489", "3 Term, 80"); a pin page opens the scan at that page — of the reprint, or of the original report by the page marks the reprint prints in its margins.
Federal Cases	Pre‑1880 lower federal opinions cited by case number ("Cole v. The Atlantic, Case No. 2,976", chained "Id. 2,717") – no digital number‑to‑reporter index exists, so the case is found live on CourtListener by the printed name (OCR‑forgiving), confirmed by the number at the head of its headnotes or by the F. Cas. volume the number's alphabetical position dictates.
Oyez	Supreme Court case summaries, question presented, holdings, justice vote splits, and oral argument audio links.
Brief Reader	Extracts text from PDF, Word, RTF, and plain text briefs; highlights every citation and makes them clickable.
Tips
The app caches Google Scholar results and PDF downloads to speed up repeated lookups.

For English Reports PDFs, if the app can’t fetch them directly, it offers to open the case in Firefox for you to pass CloudFlare’s check – once you have, the scan loads in the app by itself (the app watches Firefox for the new clearance; **Retry** still works too).

Ctrl+C / Cmd+C copies from a case viewer in whichever of four styles the **Copy** menu has selected: **Copy without citation**, **Copy with citation** (the default – the Bluebook citation, with its pinpoint page, appended below the text), **Copy as quote**, which wraps the passage in double quotes, demotes the quotation marks inside it a level so the nesting reads correctly, and sets the citation one space after the closing quote the way a brief does, or **Copy as parenthetical**. The choice is remembered between sessions. In a case viewer, **Edit citation…** lets you correct the base citation once; the correction is saved locally and reused while pinpoint pages continue to be added automatically.

**A copy shows you what it copied.** A small card appears for a few seconds in the corner of the reading area with the text as it will arrive when you paste it – the case name in italics, the footnote marker superscript, the quotation marks flipped, the citation on the end – so the Bluebooking can be checked before it reaches the brief rather than after. It is read back out of the very document handed to the clipboard, so it shows the copy rather than the passage on screen: the styles that drop footnote markers and inline page numbers are shown having dropped them. A long passage is shown from both ends, since the citation is appended last and is the part worth reading. The card never takes focus and goes away on its own, or at your next click, keystroke or scroll; **Preview what was copied** in the Copy menu turns it off, and that choice is remembered too. When no RTF clipboard tool is available and only plain text could be copied, the card says so – the formatting it is showing would not survive that paste.

You can export opinions from the Export ▾ menu: as RTF (two‑column, with running heads) for word processors; as a print‑ready PDF typeset with LaTeX (single column, justified, Century Schoolbook, footnotes at the foot of the page that cites them, and a running head showing the reporter page range visible on each sheet) if a LaTeX installation (TeX Live, MiKTeX, or Tectonic) is available; or as **.tex source** – the same document the PDF export typesets, saved unbuilt so you can edit it first. Both exports break the opinion into sections the way a reporter prints it: front matter the report names – a **Syllabus**, the reporter's **Headnotes**, or, in the early volumes, the **Argument of Counsel** – leads on pages of its own headed by that name, each separate opinion follows on a fresh page headed by its author, and an ordinary caption (docket number, dates, counsel listings) stays on the opinion's first page where it belongs. The .tex export needs no LaTeX installed here, and is offered automatically if you ask for the PDF on a machine without an engine.

Troubleshooting
“Missing Token” – answer Yes to enter your CourtListener token, or paste it later via Settings → API Token….

Google Scholar not working – install beautifulsoup4 (pip install beautifulsoup4).

PDF viewer not working – install pypdfium2 and Pillow (pip install pypdfium2 Pillow).

English Reports CloudFlare issues – ensure you have curl_cffi and browser_cookie3 installed, and Firefox: clear the check in Firefox once – the app will reuse that cookie. CloudFlare ties that cookie to the exact Firefox version that obtained it and drops it well before the date the cookie itself claims, so a machine with two Firefox installations (a Microsoft Store build beside a regular one, usually at different versions) holds a clearance in each and only one of them is the one you just obtained: the app tries every profile’s clearance, freshest first, each under its own Firefox’s version, and only asks you to clear the check again when CloudFlare has refused them all. If clearing the check still doesn’t help, run `python -X utf8 eng_rep_pdf.py --fresh`: it lists every Firefox profile it found, the version each runs under, the live CommonLII cookies in each and when that profile’s clearance was obtained, the order the app will try them in, and – after a real download – which one CommonLII accepted.

License & Credits
This tool is built on top of the excellent free legal data sources:

CourtListener – Free Law Project

Google Scholar

Oyez – Cornell LII / Chicago‑Kent

eCFR – GPO / OFR

OLRC – U.S. Code

Cornell LII – Federal Rules

CommonLII – English Reports

All content remains the property of its respective owners.
