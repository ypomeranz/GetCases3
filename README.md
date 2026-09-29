# GetCases

GetCases is a desktop research tool for American case law, plus statutes,
regulations, legislative history, SEC decisions and the old English Reports.
Look a case up by name or citation and it opens the **scanned pages of the
printed reporter**, with the opinion's text a keystroke away. Every citation in
it is a link to the next case, statute or report.

It runs on Windows, macOS and Linux and draws only on free public sources
(CourtListener, Google Scholar, Harvard's Caselaw Access Project, the Library of
Congress, GovInfo and others; see [Where the material comes
from](#where-the-material-comes-from)).

> **Use it as a research aid, not an authority.** GetCases shows what its
> sources publish, and it is only as accurate as they are. The text of older
> opinions in particular can contain scanning errors, so rely on the page
> images, and check anything you quote or cite against an authoritative source.
> See [What to double-check](#what-to-double-check). (The code and this README
> were written with AI assistance.)

---

## Getting started

### 1. Install Python

You need **Python 3.9 or newer** with Tk:

- **Windows / macOS:** the installer from [python.org](https://www.python.org/downloads/)
  includes Tk.
- **Linux:** also install your distribution's Tk package, e.g.
  `sudo apt install python3-tk`.

### 2. Download GetCases and its packages

```bash
git clone https://github.com/ypomeranz/GetCases3.git
cd GetCases3
python -m pip install -r requirements.txt
python -m pip install pynput selenium     # optional, see below
```

(Or use **Code → Download ZIP** on GitHub and unzip it.) If a package is
missing when you start the app, it lists what is missing and offers to install
it for you.

| Package | What it is for |
| --- | --- |
| `requests`, `beautifulsoup4` | Required: talking to the sources |
| `pypdfium2`, `Pillow` | Showing scans inside the app (without them, scans open in your browser) |
| `customtkinter` | The modern look (optional) |
| `pynput` | The **Ctrl+Space** shortcut that works from any application |
| `curl_cffi`, `browser_cookie3` | English Reports scans (with Firefox, below) |
| `selenium` | Reaching Google Scholar through Firefox when Google blocks the app |

### 3. Get a free CourtListener API token

1. Create a free account at [courtlistener.com](https://www.courtlistener.com/).
2. Copy the API token from your account's API settings.
3. Start GetCases. With no token saved, a setup dialog asks for one (its
   **Get a token…** button takes you to the right page) and checks it before
   saving.

You can skip this step; everything except CourtListener still works. You can
add or change the token any time under **Settings → API Token…**.

### 4. Start the app

```bash
python courtlistener_gui.py
```

Started from a terminal, GetCases runs **in the background with no window**:

- **Ctrl+Space**, pressed in any application, opens **Spotlight**, a quick
  search box.
- Type **`s`** + Enter in the terminal to open the main search window.
- Type **`q`** + Enter in the terminal to quit.

Started without a terminal, the main search window opens.

Closing the main window does **not** quit GetCases; it keeps running in the
background. To quit, type `q` + Enter in the terminal (or close the terminal).
Starting GetCases a second time hands Ctrl+Space to the new copy, and the old
one closes (once any windows it has open are closed).

### 5. Look something up

Press **Ctrl+Space** and type one of these, then press Enter:

| Type | For example |
| --- | --- |
| A case citation | `410 U.S. 113`, `Monroe v. Pape, 365 U.S. 167, 171` |
| A case name | `Roe v. Wade` |
| A statute, regulation or rule | `42 USC 1983`, `29 CFR 1614.105`, `FRE 404` |
| The Federal Register | `88 Fed. Reg. 382` |
| Legislative history | `116 Cong. Rec. 36481`, `S. Rep. No. 95-797` |
| An SEC decision | `8 S.E.C. 893, 915` |
| The English Reports | `156 Eng. Rep. 145` |

A citation opens straight away. A name shows a list of matching cases; choose one
with ↑/↓ and Enter, or press Esc to close. If no case name matches your words,
Spotlight shows Google Scholar's results for them as a phrase search instead.

---

## Reading a case

### The scan first, the text a keystroke away

A case opens as the **scan of the printed report**, in a compact viewer beside the
window you came from. The title bar shows the case's Bluebook citation, cited to
the reporter those pages actually print.

- **T** (on the viewer's toolbar) switches to the opinion's **text**; **P**
  switches back to the scan. Each switch lands on the passage you were reading.
- If no scan exists anywhere, the case opens on its text in the same kind of
  window. GetCases keeps looking for a scan in the background, and **P** lights up
  if it finds one.
- Separate writings (concurrences, dissents) are marked on a slim colored rail
  beside the scrollbar: blue for the Court's opinion, green for a concurrence, red
  for a dissent. Point at the scrollbar to see who wrote each part, and click a
  band to jump to it.

**Where the scan comes from,** in this order: the official U.S. Reports (the
Library of Congress, GovInfo, and for the newest volumes the Supreme Court's own
bound volumes and preliminary prints), Harvard's Caselaw Access Project
(static.case.law), the Supreme Court's slip-opinion archive for decisions too new
for any reporter, and last CourtListener's stored copy.

**Where the text comes from,** in this order: Google Scholar; then the Caselaw
Access Project's text of the very pages on screen; then CourtListener, which is
the main source for anything decided after 2018, when the Caselaw Access
Project's coverage ends.

### Following citations

Every citation, whether in a scan, in the text or in a brief you open, is a link:

- A **case** citation opens that case's scan. A pin cite (`410 U.S. at 153`)
  opens at that page, and a footnote pin (`200 U.S. 12, 13 n.4`) at that note.
- A **statute, regulation, court rule or the Constitution** opens in a statute
  viewer. A **legislative history** citation opens the cited pages of the
  Congressional Record, the committee report and so on.
- A citation to the **SEC's Decisions and Reports** (`8 S.E.C. 893, 915`, and
  the short form `8 S.E.C. at 917`) opens HathiTrust's scan of the volume at
  the cited page, **in your web browser**. HathiTrust lets people, not apps,
  turn its pages, so the first time it may ask you to pass a quick check there.
  The decision is listed in History, to open again.
- **Right-click** a citation on a scan to open it in your web browser instead.

You can keep clicking while something loads. Each link loads on its own, so a
slow one does not hold up the others. When a document takes more than a few
seconds, a **status window** appears where the document will open. It shows
what is being tried ("Checking the Library of Congress's scan of the U.S.
Reports…"), how much of the file has downloaded, and how long it has taken. The
document then opens in its place. If nothing can be found, the window says so and
why. **Stop waiting** abandons that document.

### The case-details side panel

Press **s** (or the panel icon at the right end of the viewer's toolbar) to open
the case's details in a panel on the right. For a Supreme Court case this is the
Oyez summary and the Justices' line-up; for other courts, CourtListener's record.
The window widens to make room, so the page you are reading is not resized; a
maximized window takes the room from the page instead.

**Show** at the top of the panel switches to:

- **Recent SCOTUS:** the Supreme Court's latest opinions and opinions relating
  to orders.
- **Docket** (Supreme Court cases only): the cert-stage and merits-stage
  briefs, from the Court's docket and SCOTUSblog, colored like the booklet
  covers.

Press **s** again (or Esc with the panel in use) to close it. **A−**/**A+** change
its type size.

### History, Bookmarks and Window

These menus are on every window: in the main window's menu bar, and as icons at
the right end of a viewer's toolbar. Right-clicking the toolbar also offers
History and bookmarking.

- **History:** the last 15 documents you opened. At the bottom, below a line,
  the Supreme Court's latest opinions and opinions relating to orders, listed by
  case name and date.
- **Bookmarks:** bookmark what you are looking at and organize bookmarks into
  folders. A bookmark keeps a local copy, so it reopens even offline.
- **Window:** switch between open windows. Windows are independent; closing
  one never closes the windows you opened from it.

---

## The main search window

Type a query, optionally narrow it by court (**Courts: All ▾**) and filing dates,
and press Enter. Results from CourtListener appear on the left (with likely minor
orders listed separately below them) and from Google Scholar on the right.
Double-click a result to open it.

Its menus:

- **Look Up:** a U.S. Code or C.F.R. section (**Ctrl/Cmd+L**); **Open Citation
  List** to open many citations at once (one per line); **Quick Look Up** for a
  case or statute; **Search English Reports** by case name.
- **Brief:**
  - **Open Brief** (**Ctrl/Cmd+B**) reads a PDF, Word, RTF or text brief and
    highlights every citation as a clickable link. Its **Download Cited Cases…**
    button saves every authority the brief cites into one ZIP file, each named
    by its Bluebook citation.
  - **Import PDF & Link Citations** keeps the PDF's own pages and makes their
    citations clickable.
  - **Recent Briefs** reopens PDF briefs you have opened in the last 30 days.
- **Database:** every opinion GetCases fetches from Google Scholar is saved in a
  local database (`data/opinions.jsonl` in the app folder), which makes it
  faster to reopen and available offline. **Find Opinion in Database…** searches
  it; **Merge In Database File…** adds someone else's.
- **Settings:** the API token, the Spotlight shortcut, and **Check for
  Updates…**, which downloads the latest version, keeps your saved opinions and
  restarts.

---

## Keyboard shortcuts

On macOS, use **Cmd** where this table says **Ctrl/Cmd**. The Spotlight shortcut
is **Ctrl+Space** on every platform; you can change it under
**Settings → Spotlight Shortcut…**.

| Keys | What they do |
| --- | --- |
| **Ctrl+Space** | Open Spotlight, from any application |
| **Ctrl/Cmd+F** | Find in the page or text (press again to close) |
| **F3** / **Shift+F3** (Mac: **Cmd+G** / **Cmd+Shift+G**) | Next / previous match |
| **Ctrl/Cmd+S** | Save: the scan as a PDF, or the text as Rich Text |
| **Ctrl/Cmd+P** | Print (see below) |
| **Ctrl/Cmd+W** | Close the window |
| **Ctrl/Cmd +** / **−** / **0** | Zoom in / out / fit the page (on the text: type size) |
| **Ctrl + mouse wheel** | Zoom |
| **←** / **→** | Previous / next page of a scan |
| **↑** / **↓** | Scroll |
| **Shift+←** / **Shift+→**, **Shift + mouse wheel** | Move sideways across a zoomed-in page |
| **s** | Open or close the case-details side panel |
| **Ctrl/Cmd+C** | Copy (from the text: with its citation, in the chosen style) |
| **x** | Switch to the next copy style |
| **Esc** | Close the find bar, Spotlight, the side panel (while you are in it) or a status window |
| **Ctrl/Cmd+L** (main window) | Look up a U.S. Code / C.F.R. section |
| **Ctrl/Cmd+B** (main window) | Open a brief |

Single-letter keys (**s**, **x**) do nothing while you are typing in a box.

---

## Saving, printing and copying

**What Save and Print do depends on which side of the viewer is showing.**

### Save (the disk icon, or Ctrl/Cmd+S)

- **On a scan:** saves a PDF named with the case's Bluebook citation, as the
  viewer shows it. Pages are cropped to the printed text, the black redaction
  bars on Caselaw Access Project scans are whitened, and the running head is
  relettered with the citation the redaction removed. The crop is lossless, so
  the pages keep their full resolution and their searchable text.
- **On the text:** saves the opinion as Rich Text (`.rtf`, two columns with
  running heads) for Word and other word processors.

### Print (the printer icon, or Ctrl/Cmd+P)

- **On a scan:** prints the same cleaned-up PDF that Save writes.
  - **macOS:** the system's own print dialog opens.
  - **Windows and Linux:** GetCases shows a list of your printers, with a
    **Printer Settings…** button (paper, quality, duplex) and, where the system
    supports it, **Print on both sides**. **Open in Viewer** opens the PDF in
    your usual PDF viewer instead, to print from there; GetCases does that
    itself when it cannot reach a printer. (On Windows, printing to a printer
    other than the default one works best with a PDF reader such as SumatraPDF
    or Adobe Acrobat Reader installed.)
- **On the text:** typesets the opinion as a polished PDF with **LaTeX** (see
  below), saves it, and offers to open it for printing. Without LaTeX, it offers
  to save the LaTeX source (`.tex`) instead, which you can edit or typeset
  elsewhere.

### Copy

Copying from the text adds the citation for you, in the style chosen in the
**Copy** menu (**Copy ▾** on a viewer's toolbar when the text is showing; the
**x** key cycles through the styles). Your choice is remembered.

- **Copy with citation** (the default): the passage, then its Bluebook citation
  with the pinpoint page.
- **Copy as quote:** the passage in quotation marks (with the quotation marks
  inside it flipped), then the citation.
- **Copy as parenthetical:** the citation, then the passage quoted in a
  parenthetical after it: *Case*, 410 U.S. 113, 153 (1973) ("…").
- **Copy without citation:** the passage alone.

A small card shows exactly what was copied, italics and all, so you can check it
before you paste. You can turn the card off in the same menu. If the citation
GetCases builds is wrong, fix it once with **Edit citation…**. Your version is
saved and reused, with pinpoint pages still added automatically.

---

## Optional extras

### Firefox

Installing [Firefox](https://www.mozilla.org/firefox/) (free) turns on two
things:

1. **English Reports scans inside the app.** CommonLII, which hosts the English
   Reports, puts a CloudFlare "Just a moment…" check in front of its scans. Pass
   that check once in Firefox (open any English Reports case there), and
   GetCases reuses Firefox's clearance to download the scans itself. This needs
   the `curl_cffi` and `browser_cookie3` packages. When the clearance expires,
   GetCases asks you to pass the check in Firefox again. Without Firefox, English
   Reports cases open in your web browser.
2. **Google Scholar when Google blocks the app.** Heavy use can make Google
   Scholar answer with an "unusual traffic" challenge. With Firefox and the
   `selenium` package installed, GetCases then fetches Scholar pages through a
   private, invisible copy of Firefox, which Google accepts. If even that is
   challenged, solve the CAPTCHA once in your normal Firefox. Without Firefox,
   GetCases falls back to its local database and to CourtListener for the text.

### A LaTeX distribution

Installing LaTeX ([TeX Live](https://tug.org/texlive/),
[MiKTeX](https://miktex.org/) or [Tectonic](https://tectonic-typesetting.github.io/))
lets GetCases typeset opinions as print-quality PDFs when you **print from the
text side** of a viewer (the printer icon, or Ctrl/Cmd+P). The result is a
single justified column in a Century Schoolbook-style typeface, with footnotes
at the foot of the page that cites them and a running head showing the
reporter pages on each sheet. The syllabus or headnotes and each separate
opinion begin on a page of their own. GetCases finds an installed LaTeX by itself.

---

## Where the material comes from

| Source | What GetCases uses it for |
| --- | --- |
| [CourtListener](https://www.courtlistener.com/) (Free Law Project) | Case search, case records, opinion text (especially for cases after 2018), stored PDFs, docket entries (RECAP) |
| [Google Scholar](https://scholar.google.com/) | Opinion text (preferred where available), case search |
| [Caselaw Access Project](https://static.case.law/) (Harvard) | Scans of the printed state and federal reporters, and their text; coverage ends with volumes published in 2018 |
| [Library of Congress](https://www.loc.gov/) and [GovInfo](https://www.govinfo.gov/) | Official U.S. Reports scans; the Statutes at Large, Federal Register, Congressional Record and congressional reports and documents |
| [Supreme Court of the United States](https://www.supremecourt.gov/) | Recent opinions, slip opinions, bound volumes and preliminary prints, dockets |
| [Oyez](https://www.oyez.org/) | Supreme Court case summaries and line-ups |
| [SCOTUSblog](https://www.scotusblog.com/) | Supreme Court docket briefs |
| [Office of the Law Revision Counsel](https://uscode.house.gov/) | The U.S. Code |
| [eCFR](https://www.ecfr.gov/) | The Code of Federal Regulations |
| [Cornell LII](https://www.law.cornell.edu/) | The Federal Rules (Civil, Criminal, Evidence, Appellate, Bankruptcy) |
| State legislatures | California and Florida statutes in the app; other states' statute citations open on the state's official site |
| [Congress.gov](https://www.congress.gov/), the [Internet Archive](https://archive.org/), [HathiTrust](https://www.hathitrust.org/) | The Annals of Congress, Register of Debates and Congressional Globe; older reports and documents |
| [HathiTrust](https://www.hathitrust.org/) | The SEC's Decisions and Reports (1934 to 2006), opened at the cited page; the page index was built from the [HathiTrust Research Center](https://analytics.hathitrust.org/)'s Extracted Features |
| [CommonLII](http://www.commonlii.org/) | The English Reports (cases from 1220 to 1865) |

---

## What to double-check

GetCases is **only as accurate as the sources behind it**, and it fills gaps with
automatic matching that can go wrong. In particular:

- **The text of older opinions is the least reliable thing in the app.** For
  older cases, the text from CourtListener and the Caselaw Access Project was
  machine-read (OCR) from page scans. It can have misread words and numbers,
  garbled or misplaced footnotes, lost italics, wrong page breaks, and
  concurrences or dissents run into the majority opinion. **Rely on the scanned
  pages for older cases**, which is why the scan opens first. Check a quotation
  or pin cite against the scan before you use it.
- **Page numbers in the text can be approximate.** Where a source does not
  carry a reporter's page breaks (common for recent Supreme Court opinions),
  GetCases works them out by matching the text against the scan. Near a page
  break this can be off.
- **Citations and case names are built automatically** from the sources'
  records and Bluebook rules. Check them before filing, and correct one with
  **Edit citation…** when needed.
- **The right case is found by matching,** by citation first and by name when
  it must. Two cases can begin on the same page, and similar names can be
  confused. Glance at the caption to confirm you have the case you meant.
- **A citation to the SEC's Decisions and Reports can land a page off.**
  GetCases places each printed page in HathiTrust's scan from the page numbers
  the scans print, which it reads for nearly every page; beside a folded table
  or chart the placement can be a page or two out. Its years for SEC decisions
  are read the same way, from the decision's first page.
- **Coverage has gaps.** The Caselaw Access Project ends with volumes published
  in 2018. The newest cases may not have reporter pages yet. Google Scholar lacks
  some unpublished and some state decisions. Some sources occasionally go
  offline or change.

---

## Good to know

- **Your searches go to these services.** Queries and the citations you open are
  sent to CourtListener, Google Scholar and the other sources above. A brief you
  open is read on your computer; nothing from it leaves your computer except
  the citations you follow or download.
- **Google Scholar paces itself.** GetCases waits a few seconds between Scholar
  requests so Google does not block it. If Google blocks it anyway, see
  [Firefox](#firefox) above.
- **Where your data is kept:** your settings, token, history and bookmarks are in
  `~/.config/courtlistener/` (a folder in your home folder); downloaded caches are
  there and in `~/.cache/`. The local opinion database is `data/opinions.jsonl` in
  the app folder, and downloaded Supreme Court volumes are kept in its
  `US Reports` folder.
- **Updating:** **Settings → Check for Updates…** installs the latest version
  and keeps your saved opinions.

---

## Troubleshooting

| Problem | What to do |
| --- | --- |
| No Tk / `No module named tkinter` | Install Python's Tk (see [Install Python](#1-install-python)) |
| Ctrl+Space does nothing | Install `pynput`; on macOS, allow the terminal (or Python) under **System Settings → Privacy & Security → Accessibility** and **Input Monitoring** |
| Scans open in the browser instead of the app | Install `pypdfium2` and `Pillow` |
| "CourtListener" results missing | Add your API token under **Settings → API Token…** |
| Google Scholar returns nothing, or says it is blocked | Wait a few minutes, or set up [Firefox and selenium](#firefox) |
| English Reports ask you to "pass the check in Firefox" | Open any English Reports case in Firefox, let the check finish, then try again |
| PDF export says LaTeX was not found | Install a [LaTeX distribution](#a-latex-distribution), or save the `.tex` source instead |
| A document seems stuck | Its status window shows what is being tried; **Stop waiting** abandons it |

---

## Credits

GetCases is built on free legal information published by the Free Law Project
(CourtListener), Google Scholar, the Harvard Law School Library's Caselaw Access
Project, the Library of Congress, the U.S. Government Publishing Office
(GovInfo), the Supreme Court of the United States, Oyez, SCOTUSblog, the Office
of the Law Revision Counsel, the Office of the Federal Register (eCFR), Cornell's
Legal Information Institute, the California and Florida legislatures,
Congress.gov, the Internet Archive, HathiTrust (and the HathiTrust Research
Center) and CommonLII. All content remains
the property of its owners.
