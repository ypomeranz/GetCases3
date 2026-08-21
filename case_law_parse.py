"""Parse a static.case.law (Harvard CAP) case into the shared Block/Span model
so a CAP opinion reads like a Google Scholar one.

CAP publishes every case three ways under the same volume directory, keyed on
the page the case begins at::

    https://static.case.law/{reporter}/{volume}/cases/{page:04d}-{n}.json
    https://static.case.law/{reporter}/{volume}/html/{page:04d}-{n}.html
    https://static.case.law/{reporter}/{volume}/case-pdfs/{page:04d}-{n}.pdf

The JSON is the metadata (name, court, decision date, parallel citations) plus
a *flattened* body: one string per opinion, paragraphs separated by newlines,
every footnote run onto the end of the opinion that references it, and no
reporter pagination at all.  Rendered as it stands it is a wall of text whose
footnotes look like stray sentences.

The HTML beside it is the same case with its structure intact::

    <section class="casebody" data-firstpage="701" data-lastpage="709">
      <section class="head-matter">
        <h4 class="parties">Drew PEARSON …, v. Thomas J. DODD, Appellee.</h4>
        <p class="docketnumber">No. 21910.</p>
        <p class="court">United States Court of Appeals …</p>
        <p class="attorneys"><a class="page-label" data-label="702">*702</a>Mr. …</p>
      </section>
      <article class="opinion" data-type="majority">
        <p class="author">J. SKELLY WRIGHT, Circuit Judge:</p>
        <p>This case arises out of …<a class="footnotemark" href="#footnote_1_1">1</a></p>
        <blockquote>“* * * [O]n several occasions …”</blockquote>
        <aside class="footnote" data-label="1" id="footnote_1_1"><p>. The …</p></aside>
      </article>
    </section>

:func:`parse_case_law_html` turns that into the ``OpinionPart``/``Block``/
``Span`` model the viewer already renders — head matter as the header part,
one part per ``<article>`` with its own footnotes, ``page-label`` anchors as
star-pagination spans (so the page gutter, pin cites and the reporter page map
work), and ``footnotemark``/``aside`` pairs as clickable footnote references.
:func:`parse_case_law_json` is the fallback for the rare case whose HTML will
not load: the same model built from the JSON's flattened text, which at least
keeps paragraphs and separates the footnote run from the opinion.

Citation anchors are deliberately *not* turned into links.  The viewer runs its
own whole-opinion citation detector over the rendered parts, which resolves
short forms and ``Id.`` across paragraphs and routes each hit through the app's
own lookup; CAP's ``/citations/?q=…`` hrefs would only get in its way.
"""

from __future__ import annotations

import re

#: CAP's ``data-type`` → the viewer's part kinds.  Anything unrecognized is
#: read as the main opinion, matching how CourtListener's own type codes are
#: defaulted in :data:`courtlistener_gui._CL_TYPE_KIND`.
_CAP_OPINION_KIND = {
    "majority": "majority",
    "unanimous": "majority",
    "per-curiam": "majority",
    "on-the-merits": "majority",
    "rehearing": "majority",
    "remittitur": "majority",
    "concurrence": "concurrence",
    "concurring-in-part-and-dissenting-in-part": "concurrence",
    "dissent": "dissent",
}

#: Part labels, worded like the CourtListener assembler's so the Bluebook
#: writer parenthetical reads them the same way ("Concurrence (TAMM, …)").
_CAP_OPINION_LABEL = {
    "majority": "Opinion",
    "unanimous": "Unanimous Opinion",
    "per-curiam": "Opinion",
    "on-the-merits": "On the Merits",
    "rehearing": "Rehearing",
    "remittitur": "Remittitur",
    "concurrence": "Concurrence",
    "concurring-in-part-and-dissenting-in-part": "Concurrence in Part",
    "dissent": "Dissent",
}

#: Head-matter lines the reports print centred above the opinion.  Everything
#: else there (counsel, headnotes, syllabus, the panel) is running prose.
_CAP_CENTERED = {
    "parties", "docketnumber", "court", "decisiondate", "otherdate",
    "citation",
}

# A paragraph that is only a section marker — a roman numeral, a single
# capital, or a number.  The reports set these as headings; CAP emits them as
# ordinary paragraphs, which strands them as orphan one-character lines.
_CAP_SECTION_RE = re.compile(r"^(?:[IVXLC]{1,7}|[A-Z]|\d{1,2})\.?$")
_CAP_STARS_RE = re.compile(r"^\*(?:\s*\*){1,4}$")  # a "* * *" break

# CAP's OCR keeps the printed footnote marker as a bare leading period on the
# note's first line (". The operative part of the District Court's order …").
_CAP_NOTE_LEAD_RE = re.compile(r"^\.\s+")

# A byline CAP split across two paragraphs because the scan set it on two
# lines: "Mr. Justice Blackmun" / "delivered the opinion of the Court.", or
# "TAMM, Circuit Judge" / "(concurring):".  Joined back together, the part's
# first block names the writer *and* the role, which is what the Bluebook
# parenthetical and the part map both read.
_BYLINE_OPEN_RE = re.compile(r"[.:;!?]\s*$")
_BYLINE_TAIL_RE = re.compile(r"^[(\[a-z]")
# The punctuation that ends the byline *line* rather than the writer's name.
# A period is cut only when it closes a word ("TAMM, J., concurring." →
# "TAMM, J., concurring"), never when it closes an abbreviation — "HALE, C.J."
# and "PER CURIAM." keep theirs, which is what the Bluebook parenthetical
# reads them back by.
_BYLINE_TRIM_RE = re.compile(r"(?:[\s,:;]|(?<=[a-z])\.)+$")

_WS_RE = re.compile(r"\s+")
_H_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCK_TAGS = _H_TAGS | {
    "p", "div", "blockquote", "center", "pre", "table", "tbody", "thead",
    "tr", "ul", "ol", "li", "dl", "dt", "dd", "article", "section",
}


def parse_case_law_html(html: str) -> "tuple[list, list]":
    """Parse a CAP case HTML file into ``(parts, blocks)``.

    *parts* are ``OpinionPart``s — the head matter as a ``header`` part,
    then one part per ``<article class="opinion">`` carrying its own
    footnotes.  *blocks* is every body block in printed order, which is what
    the caption reader and the plain-text rendering read.

    Returns ``([], [])`` when beautifulsoup4 is missing or the file holds no
    casebody, so the caller can fall back to the JSON text.
    """
    try:
        from bs4 import BeautifulSoup, Comment, NavigableString, Tag
        from google_scholar import Block, OpinionPart, Span
    except ImportError:
        return [], []

    soup = BeautifulSoup(html or "", "html.parser")
    root = soup.find("section", class_="casebody")
    if root is None:
        return [], []
    for bad in root.find_all(["script", "style"]):
        bad.decompose()

    # Star-pagination markers, document-wide: a reporter page begins exactly
    # once, and CAP repeats the label where a page break falls inside material
    # it prints twice (a footnote continued under the page it interrupts).
    seen_pages: set[str] = set()

    def classes(tag) -> list:
        return [c.lower() for c in (tag.get("class") or [])]

    def parse_section(node) -> "tuple[list, list, str]":
        """``(blocks, footnotes, byline)`` for one head-matter or opinion."""
        blocks: list = []
        footnotes: list = []
        cur: list = []
        byline = ""
        author_at = -1

        def emit(text: str, fmt: dict, *, fnref: str = "",
                 sup: bool = False) -> None:
            text = _WS_RE.sub(" ", text)
            if not text:
                return
            if not cur:
                text = text.lstrip()
                if not text:
                    return
            elif cur[-1].text.endswith((" ", "\n")) and text.startswith(" "):
                text = text.lstrip(" ")
                if not text:
                    return
            issup = sup or fmt.get("sup", False)
            last = cur[-1] if cur else None
            if (
                last is not None and not fnref and not last.fnref
                and not last.pagenum  # never fold body text into a marker
                and last.sup == issup
                and all(getattr(last, k) == fmt.get(k, False)
                        for k in ("italic", "bold", "underline", "small"))
            ):
                last.text += text
            else:
                cur.append(Span(
                    text=text, fnref=fnref, sup=issup,
                    italic=fmt.get("italic", False),
                    bold=fmt.get("bold", False),
                    underline=fmt.get("underline", False),
                    small=fmt.get("small", False),
                ))

        def flush(kind: str) -> None:
            nonlocal cur
            while cur and not cur[-1].text.strip():
                cur.pop()
            if cur:
                cur[-1].text = cur[-1].text.rstrip()
                blocks.append(Block(kind=kind, spans=cur))
            cur = []

        def emit_pagenum(tag) -> None:
            page = str(tag.get("data-label") or "").strip() or _WS_RE.sub(
                " ", tag.get_text()).strip().lstrip("*").strip()
            if not page or page in seen_pages:
                return
            seen_pages.add(page)
            cur.append(Span(text="*" + page, pagenum=True))

        def note_block(aside) -> None:
            """Turn an <aside class="footnote"> into a footnote block: a
            clickable marker span (fndef) followed by the note's text.  Its
            own page labels are dropped — a note printed at the foot of the
            page it interrupts would otherwise put that page's marker out of
            order in the gutter, where the body already carries it."""
            fid = (aside.get("id") or "").strip()
            label = str(aside.get("data-label") or "").strip()
            note: list = []

            def nemit(text: str, fmt: dict) -> None:
                text = _WS_RE.sub(" ", text)
                if not text or (not text.strip() and (
                        not note or note[-1].text.endswith(("\n", " ")))):
                    return
                note.append(Span(
                    text=text,
                    italic=fmt.get("italic", False),
                    bold=fmt.get("bold", False),
                    underline=fmt.get("underline", False),
                    small=fmt.get("small", False),
                ))

            def nwalk(node, fmt: dict) -> None:
                for child in node.children:
                    if isinstance(child, Comment):
                        continue
                    if isinstance(child, NavigableString):
                        nemit(str(child), fmt)
                        continue
                    if not isinstance(child, Tag):
                        continue
                    name = (child.name or "").lower()
                    cls = classes(child)
                    if name == "a" and "page-label" in cls:
                        continue
                    if name == "a" and str(
                            child.get("href") or "").startswith("#ref_"):
                        # The back-reference to the in-text marker, which the
                        # marker span below already stands for.
                        continue
                    if name == "img":
                        continue
                    if name in _BLOCK_TAGS:
                        # A note the reports set as several paragraphs (a
                        # quoted statute, a string cite broken up).  It stays
                        # one footnote, but its paragraphs keep their breaks
                        # instead of running into one another.
                        if any(s.text.strip() for s in note):
                            while note and not note[-1].text.strip():
                                note.pop()
                            note[-1].text = note[-1].text.rstrip()
                            note.append(Span(text="\n"))
                        nwalk(child, fmt)
                        continue
                    if name in ("i", "em", "cite"):
                        nwalk(child, {**fmt, "italic": True})
                    elif name in ("b", "strong"):
                        nwalk(child, {**fmt, "bold": True})
                    elif name == "u":
                        nwalk(child, {**fmt, "underline": True})
                    elif name == "small":
                        nwalk(child, {**fmt, "small": True})
                    else:
                        nwalk(child, fmt)

            nwalk(aside, {})
            # Trim the ends only: a blank span *between* two runs of text is
            # the space or the paragraph break that separates them.
            while note and not note[0].text.strip():
                note.pop(0)
            while note and not note[-1].text.strip():
                note.pop()
            if not note:
                return
            note[0].text = _CAP_NOTE_LEAD_RE.sub("", note[0].text.lstrip())
            note[-1].text = note[-1].text.rstrip()
            if not any(s.text.strip() for s in note):
                return
            marker = Span(text=label or "•", fndef=fid)
            footnotes.append(Block(kind="para",
                                   spans=[marker, Span(text=" ")] + note))

        def walk(node, fmt: dict, kind: str) -> None:
            nonlocal byline, author_at
            for child in node.children:
                if isinstance(child, Comment):
                    continue
                if isinstance(child, NavigableString):
                    emit(str(child), fmt)
                    continue
                if not isinstance(child, Tag):
                    continue
                name = (child.name or "").lower()
                cls = classes(child)
                if name == "br":
                    if cur:
                        cur.append(Span(text="\n"))
                    continue
                if name in ("hr", "img"):
                    if name == "hr":
                        flush(kind)
                    continue
                if name == "aside" and "footnote" in cls:
                    flush(kind)
                    note_block(child)
                    continue
                if name == "a":
                    if "page-label" in cls:
                        emit_pagenum(child)
                        continue
                    href = str(child.get("href") or "")
                    if "footnotemark" in cls and href.startswith("#"):
                        marker = _WS_RE.sub(" ", child.get_text()).strip()
                        if marker:
                            emit(marker, fmt, fnref=href.lstrip("#"), sup=True)
                        continue
                    # A CAP citation anchor, or a stray one: keep the text and
                    # let the viewer's own citation detector claim it.
                    walk(child, fmt, kind)
                    continue
                if name in _BLOCK_TAGS:
                    flush(kind)
                    child_fmt = fmt
                    if "parties" in cls:
                        child_kind, child_fmt = "center", {**fmt, "bold": True}
                    elif cls and cls[0] in _CAP_CENTERED:
                        child_kind = "center"
                    elif name == "center":
                        child_kind = "center"
                    elif name == "blockquote":
                        child_kind = "blockquote"
                    elif name in _H_TAGS:
                        child_kind = kind if kind == "center" else "heading"
                        child_fmt = {**fmt, "bold": True}
                    else:
                        child_kind = kind
                    if "author" in cls:
                        author_at = len(blocks)
                    walk(child, child_fmt, child_kind)
                    if "author" in cls and len(blocks) == author_at:
                        byline = _WS_RE.sub(
                            " ", "".join(s.text for s in cur
                                         if not s.pagenum)).strip()
                    flush(child_kind)
                    continue
                if name in ("i", "em", "cite"):
                    walk(child, {**fmt, "italic": True}, kind)
                elif name in ("b", "strong"):
                    walk(child, {**fmt, "bold": True}, kind)
                elif name == "u":
                    walk(child, {**fmt, "underline": True}, kind)
                elif name == "small":
                    walk(child, {**fmt, "small": True}, kind)
                elif name in ("sup", "sub"):
                    walk(child, {**fmt, "sup": True}, kind)
                else:
                    walk(child, fmt, kind)

        walk(node, {}, "para")
        flush("para")
        _join_split_byline(blocks, author_at)
        return blocks, footnotes, byline

    parts: list = []
    body: list = []

    head = root.find("section", class_="head-matter")
    if head is not None:
        hblocks, hfootnotes, _byline = parse_section(head)
        if hblocks:
            parts.append(OpinionPart(
                label="Header", kind="header", blocks=hblocks,
                footnotes=hfootnotes,
            ))
            body.extend(hblocks)

    for article in root.find_all("article", class_="opinion"):
        oblocks, ofootnotes, byline = parse_section(article)
        if not oblocks:
            continue
        cap_type = str(article.get("data-type") or "").strip().lower()
        label = _CAP_OPINION_LABEL.get(
            cap_type, cap_type.replace("-", " ").title() or "Opinion")
        author = _BYLINE_TRIM_RE.sub("", byline.strip())
        if not author and cap_type == "per-curiam":
            author = "Per Curiam"
        if author and len(author) <= 90:
            label = f"{label} ({author})"
        parts.append(OpinionPart(
            label=label, kind=_CAP_OPINION_KIND.get(cap_type, "majority"),
            blocks=oblocks, footnotes=ofootnotes,
        ))
        body.extend(oblocks)

    if not body:
        return [], []
    _finish(parts)
    return parts, body


def parse_case_law_json(casebody) -> "tuple[list, list]":
    """Parse CAP's flattened JSON body into ``(parts, blocks)``.

    The fallback for a case whose HTML will not load.  There is no pagination
    to recover and no way to tie a note to the marker that called it, but the
    paragraphs are real and the footnote run CAP appends to each opinion — every
    line of it opening with the bare period its printed marker was OCR'd as —
    is recognizable, so the notes at least sit under the opinion as notes
    rather than trailing it as prose.
    """
    try:
        from google_scholar import Block, OpinionPart, Span
    except ImportError:
        return [], []
    if not isinstance(casebody, dict):
        return [], []

    def paragraphs(text: str) -> list:
        return [
            Block(kind="para", spans=[Span(text=line.strip())])
            for line in (text or "").split("\n") if line.strip()
        ]

    parts: list = []
    body: list = []
    head = str(casebody.get("head_matter") or "").strip()
    if head:
        hblocks = paragraphs(head)
        # The reports print the parties above everything else; centring the
        # first line is what makes the caption reader see a caption.
        if hblocks:
            hblocks[0] = Block(kind="center", spans=[
                Span(text=hblocks[0].text(), bold=True)])
        parts.append(OpinionPart(label="Header", kind="header", blocks=hblocks))
        body.extend(hblocks)

    for op in casebody.get("opinions") or []:
        if not isinstance(op, dict):
            continue
        blocks = paragraphs(str(op.get("text") or ""))
        if not blocks:
            continue
        cap_type = str(op.get("type") or "").strip().lower()
        notes: list = []
        while blocks and _CAP_NOTE_LEAD_RE.match(blocks[-1].text()):
            notes.insert(0, blocks.pop())
        if not blocks:  # every line looked like a note — it was the opinion
            blocks, notes = notes, []
        for note in notes:
            note.spans = [Span(text=_CAP_NOTE_LEAD_RE.sub(
                "", note.text().lstrip()))]
        label = _CAP_OPINION_LABEL.get(
            cap_type, cap_type.replace("-", " ").title() or "Opinion")
        author = _BYLINE_TRIM_RE.sub("", str(op.get("author") or "").strip())
        if not author and cap_type == "per-curiam":
            author = "Per Curiam"
        if author and len(author) <= 90:
            label = f"{label} ({author})"
        parts.append(OpinionPart(
            label=label, kind=_CAP_OPINION_KIND.get(cap_type, "majority"),
            blocks=blocks, footnotes=notes,
        ))
        body.extend(blocks)

    if not body:
        return [], []
    _finish(parts)
    return parts, body


def _join_split_byline(blocks: list, author_at: int) -> None:
    """Rejoin a byline the scan set on two lines (see :data:`_BYLINE_OPEN_RE`).

    Done in place, on the block the ``author`` class marked, and only when the
    byline is left hanging — no closing punctuation — and the paragraph after
    it opens the way a continuation does rather than the way a sentence does.
    """
    if not (0 <= author_at < len(blocks) - 1):
        return
    head, tail = blocks[author_at], blocks[author_at + 1]
    head_text = head.text().strip()
    tail_text = tail.text().strip()
    if not head_text or not tail_text:
        return
    if _BYLINE_OPEN_RE.search(head_text) or not _BYLINE_TAIL_RE.match(tail_text):
        return
    try:
        from google_scholar import Span
    except ImportError:
        return
    head.spans = head.spans + [Span(text=" ")] + tail.spans
    del blocks[author_at + 1]


def _finish(parts: list) -> None:
    """Reclassify bare section markers as headings and curl the quotes —
    the last two things every other opinion parser in the app does."""
    for part in parts:
        for block in part.blocks:
            if block.kind != "para":
                continue
            text = block.text().strip()
            if text and (_CAP_SECTION_RE.match(text)
                         or _CAP_STARS_RE.match(text)):
                block.kind = "heading"
    try:
        from google_scholar import _educate_block_quotes
    except ImportError:
        return
    for part in parts:
        for block in list(part.blocks) + list(part.footnotes):
            _educate_block_quotes(block)


if __name__ == "__main__":
    failed = 0

    def check(cond: bool, what: str) -> None:
        global failed
        failed += not cond
        print(("ok   " if cond else "FAIL ") + what)

    sample = """<section class="casebody" data-firstpage="701" data-lastpage="709">
  <section class="head-matter">
    <h4 class="parties" id="b763-4">Drew PEARSON, Appellants, v. Thomas J. DODD, Appellee.</h4>
    <p class="docketnumber">No. 21910.</p>
    <p class="court">United States Court of Appeals District of Columbia Circuit.</p>
    <p class="attorneys"><a id="p702" data-label="702" class="page-label">*702</a>Mr. John Donovan, for appellants.</p>
    <p>Before Wright, Tamm and Robinson, Circuit Judges.</p>
  </section>
  <article class="opinion" data-type="majority">
    <p class="author">J. SKELLY WRIGHT, Circuit Judge:</p>
    <p>This case arises out of the exposure.<a class="footnotemark" href="#footnote_1_1" id="ref_footnote_1_1">1</a></p>
    <p>I</p>
    <blockquote>&#8220;* * * [O]n several occasions in June.&#8221;</blockquote>
    <p><a id="p704" data-label="704" class="page-label">*704</a>Indeed, appellee has not urged.</p>
    <aside data-label="1" class="footnote" id="footnote_1_1">
      <a href="#ref_footnote_1_1">1</a>
      <p>. The operative part of the order stated <em>that</em> the defendants are liable.</p>
    </aside>
  </article>
  <article class="opinion" data-type="concurrence">
    <p class="author">TAMM, Circuit Judge</p>
    <p>(concurring):</p>
    <p>Some legal scholars will see an ironic aspect.</p>
  </article>
</section>"""

    parts, blocks = parse_case_law_html(sample)
    kinds = [(p.kind, p.label) for p in parts]
    check(kinds[0] == ("header", "Header"), f"head matter is the header: {kinds[0]}")
    check(kinds[1] == ("majority", "Opinion (J. SKELLY WRIGHT, Circuit Judge)"),
          f"majority labelled from its byline: {kinds[1]}")
    check(kinds[2] == ("concurrence", "Concurrence (TAMM, Circuit Judge)"),
          f"concurrence labelled from its byline: {kinds[2]}")
    check(parts[0].blocks[0].kind == "center",
          "parties line is centred (the caption reader looks for that)")
    pages = [s.text for p in parts for b in p.blocks for s in b.spans
             if s.pagenum]
    check(pages == ["*702", "*704"], f"reporter pages become markers: {pages}")
    refs = [(s.text, s.sup, s.fnref) for p in parts for b in p.blocks
            for s in b.spans if s.fnref]
    check(refs == [("1", True, "footnote_1_1")], f"in-text footnote ref: {refs}")
    notes = parts[1].footnotes
    check(len(notes) == 1 and notes[0].spans[0].fndef == "footnote_1_1",
          "the note is attached to the opinion that calls it")
    check(notes[0].text().startswith("1 The operative part"),
          f"OCR'd leading period dropped: {notes[0].text()[:30]!r}")
    check(any(b.kind == "heading" and b.text().strip() == "I"
              for b in parts[1].blocks), "bare section marker becomes a heading")
    check(any(b.kind == "blockquote" for b in parts[1].blocks),
          "blockquote preserved")
    check(parts[2].blocks[0].text() == "TAMM, Circuit Judge (concurring):",
          f"split byline rejoined: {parts[2].blocks[0].text()!r}")
    check(not any(s.link for p in parts for b in p.blocks for s in b.spans),
          "no CAP hrefs survive — the viewer's own detector links citations")

    json_parts, json_blocks = parse_case_law_json({
        "head_matter": "Drew PEARSON v. Thomas J. DODD.\nNo. 21910.",
        "opinions": [{
            "type": "majority", "author": "J. SKELLY WRIGHT, Circuit Judge:",
            "text": "J. SKELLY WRIGHT, Circuit Judge:\nThis case arises.\n"
                    ". The operative part of the order.",
        }],
    })
    check(json_parts[0].kind == "header" and json_parts[0].blocks[0].kind == "center",
          "JSON fallback centres the caption too")
    check(len(json_parts[1].footnotes) == 1
          and json_parts[1].footnotes[0].text().startswith("The operative"),
          f"JSON footnote run split off: {json_parts[1].footnotes}")
    check(len(json_parts[1].blocks) == 2,
          f"JSON body keeps its paragraphs: {len(json_parts[1].blocks)}")

    print("\n%d check(s) failed" % failed)
    raise SystemExit(1 if failed else 0)
