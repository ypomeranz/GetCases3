# GetCases Citation Links (Chrome extension)

Links the case, statute, regulation and rule citations on the web pages and PDFs
you read in Chrome. Click one and:

- **with GetCases running**, it opens in GetCases, just as a citation in one of
  the app's own windows does (a case opens on its scanned pages, a statute in
  the statute viewer, and so on);
- **without GetCases**, it opens the web page for that citation on the web:
  a case on its scanned report when the citation says where that is and the
  scan is there — the official U.S. Reports at the Library of Congress or
  GovInfo for the Supreme Court (the early reporters too: "1 Cranch 137" is
  5 U.S. 137), the Caselaw Access Project's scan for any other reporter —
  and otherwise Google Scholar's search of case law; uscode.house.gov for the
  U.S. Code, eCFR for the C.F.R., Cornell LII for the federal rules, the
  Library of Congress for the Constitution, GovInfo for the Statutes at
  Large and the Federal Register.

It also works in Chrome, Edge, Brave and other Chromium browsers.

## Installing

The extension is not in the Chrome Web Store; you load it from this folder.

1. Open `chrome://extensions` in Chrome.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose this `browser_extension` folder.
4. Optional: to link citations in PDF files on your own computer (`file://`
   addresses), click **Details** on the extension and turn on **Allow access to
   file URLs**.

After **Settings → Check for Updates…** updates GetCases, click the reload
arrow on the extension's card in `chrome://extensions` to pick up any changes.

## Using it

- Citations get a dotted underline: blue for cases, green for statutes, rules
  and regulations, amber for the Constitution. Point at one to see what it
  cites. The Constitution is linked where a page cites it (`U.S. Const. art.
  I, § 3`, `Article I, Section 3`), the first time it cites each provision;
  "the First Amendment" in a sentence is not a link.
- **Click** a citation to open it (in GetCases if it is running, otherwise on
  the web, in a new tab). **Ctrl/Cmd-click** or **middle-click** always opens
  the web page instead, in a tab behind this one. A case's scan takes a
  moment to find: the Caselaw Access Project has only some of each
  reporter's cases, so the extension asks before opening one.
- **Links a page already has** to a case or statute (a Justia or CourtListener
  opinion, a section on Cornell or uscode.house.gov, a link whose text is a
  citation) open in GetCases too while it is running. Without GetCases they
  work as they always did.
- **Select any text**, right-click and choose **Look up "…" in GetCases**: a
  citation opens directly; anything else (a case name, say) opens in GetCases's
  Spotlight search.
- **PDFs** that Chrome opens are shown in the extension's own viewer, with
  their citations linked the same way. **Chrome viewer** on its toolbar opens
  the PDF in Chrome's own viewer instead (to print, fill in or annotate it).
  To open a linked PDF in the viewer by hand, right-click the link and choose
  **Open PDF with citation links**.

The toolbar button shows whether GetCases is running ("on" on the icon), how
many citations are linked on the page, and these settings:

| Setting | What it does |
| --- | --- |
| Link citations on web pages | Turns the extension's links off everywhere |
| …on *this site* | Turns them off on the site you are on (adds it to the sites left alone) |
| Sites left alone | The sites the extension stays out of — see below |
| Open PDFs with citation links | Off: PDFs open in Chrome's viewer as usual |
| Open pages' own links … in GetCases | Off: a page's existing links always go where they point |
| Connection → GetCases port | Only if you changed GetCases's port (below) |

### Sites left alone

On the sites in this list, and their subdomains, the extension links no
citations, leaves the pages' own links as they are, and lets their PDFs open
in Chrome's viewer. It starts with the research services, which link their
own citations and whose pages are applications the links would only get in
the way of: Westlaw, Lexis and LexisNexis, Bloomberg Law, vLex and Fastcase.
A site reached through a library's proxy (`next-westlaw-com.ezproxy.…`)
counts as the site itself.

Open **Sites left alone** in the popup to see the list: **×** takes a site off
it, and the box below adds one — type a domain (`example.com`) or paste an
address from the browser. **Add the research services back** restores any of
the starting ones you have removed. The list is kept with your Chrome
settings, so with Chrome sync on it follows you to your other computers.

## How it reads citations

When GetCases is running, the extension sends the text of the page to it and
GetCases reads the citations with its own detector: the same one that links
citations in its opinion reader and in briefs, so everything GetCases can open
is linked, including short forms ("410 U.S. at 164"), case names, state
statutes, legislative history and more.

When GetCases is not running, the extension reads the page itself, with a copy
of GetCases's own citation patterns (`src/patterns.js`), and links cases, the
U.S. Code, the C.F.R., the federal rules, the Constitution, the Statutes at
Large, the Federal Register, the English Reports and the SEC's reports. (For
the last two, GetCases's own index names the exact page; without it the link
opens a search of CommonLII, or HathiTrust's catalogue record.) A citation
to the original reports the English Reports reprint ("2 Russ. & M. 639", "6
Mod. 16") is linked when it stands beside the reprint's own ("…, 39 Eng.
Rep. 538"), and opens the same case; GetCases reads them wherever they
stand. If you start GetCases later, the page is read again GetCases's way
the next time you come back to its tab.

## Privacy

The extension talks to GetCases on your own computer
(`http://127.0.0.1:21983`). Page text goes to GetCases only while it is
running, and never leaves your computer; nothing is sent anywhere else. When
you click a citation without GetCases, the web page for it opens like any link
— and, for a case, the extension first asks the sites its scan would be at
(the Library of Congress, GovInfo, the Caselaw Access Project) whether they
have that one file: a request for its address alone, without cookies.

GetCases answers only the extension: its door takes requests only with the
extension's header, refuses anything a web page sends, and is reachable only
from this computer.

## Settings in GetCases

- **Settings → Open Citations Clicked in Chrome Here** turns the connection off
  (and on) in the app.
- The port is 21983. To use another, add `"browser_bridge_port": <number>` to
  GetCases's `~/.config/courtlistener/config.json`, restart GetCases, and set the
  same number under **Connection** in the extension's popup.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| Clicks open the web, though GetCases is running | Check the extension's popup says GetCases is running. If not: is **Settings → Open Citations Clicked in Chrome Here** ticked in GetCases, and do the ports match? A second copy of GetCases takes the connection over from the first. |
| A statute opens in the browser although GetCases is running | GetCases couldn't reach its source (uscode.house.gov, eCFR, Cornell) and says so in a message; the web page opened instead. Try again later, or check your connection. |
| A GetCases window opened behind Chrome | Click its icon in the taskbar or Dock; GetCases brings the first window of each click forward, but some systems refuse. |
| Links look wrong on some site | Untick **…on this site** in the popup, or add it under **Sites left alone**. |
| No links on Westlaw, Lexis, … | They are among the **Sites left alone**; take one off the list to link it. |
| A PDF says it can't be opened | Use **Open it in Chrome's viewer instead**. For PDFs on your computer, allow file access (step 4 above). |
| A PDF has no links | It may be a scan with no text layer; the button on the viewer's toolbar says "No text to read". |

## For developers

- `src/patterns.js` is generated from the app's Python regular expressions:
  run `python export_extension_patterns.py` (in the GetCases folder) after
  changing any of them.
  `test_browser_extension.py` fails while it is out of date, and (with Node
  installed) checks that each pattern matches the same text in JavaScript as in
  Python and that the extension opens the same web pages as the app.
- `vendor/pdfjs/` is Mozilla's pdf.js (Apache 2.0), unmodified apart from the
  source-map comment; see `vendor/pdfjs/VERSION.txt`.
- The app's side is `browser_bridge.py` (the local server) and
  `browser_links.py` (where each kind of citation opens on the web).
