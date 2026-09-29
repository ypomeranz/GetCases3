"""Fetch the pages a legislative-history citation names.

:mod:`legislative_history` reads the citation; this goes and gets it:

* the bound Congressional Record (1873-2018) — the cited page and a few
  around it, cut out of GovInfo's scan of that day (see :mod:`pdf_range`),
  or, from 1999, GovInfo's PDF of the day;
* the daily Record from 1995 — the pages GovInfo's link service names; an
  earlier daily-edition page — the bound edition's record of the same day
  and chamber, where it appears under another page number;
* a committee report or document — GovInfo's link service (from 1995, and
  the digitized Serial Set before), else a scan on the Internet Archive;
  failing both, where HathiTrust holds it, for the browser;
* the Annals of Congress, the Register of Debates and the Congressional
  Globe — the cited page and a few around it, cut out of the Library of
  Congress's scans on Congress.gov.

Everything fetched whole is kept on disk, so a page opened once opens again
without the network.  Network and PDF text only — no GUI; PDFium is called
with :data:`pdfium_lock.PDFIUM_LOCK` held, never while waiting on the web.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import legislative_history as lh
import pdf_range

USER_AGENT = "GetCases/1.0 (legal research app)"
CACHE_DIR = Path.home() / ".config" / "courtlistener" / "leghist_cache"
GOVINFO_LINK = "https://www.govinfo.gov/link"
TIMEOUT = 60

# Pages cut around the cited one: a few before (a speech often starts
# there), more after (it goes on).
BEFORE, AFTER = 2, 7


@dataclass
class Pages:
    """What the viewer shows: *data* (a PDF), opened at page *index*."""
    data: bytes
    index: int
    title: str
    source_label: str
    source_url: str          # the whole document online, for the browser
    note: str = ""


class Unavailable(Exception):
    """No free copy could be fetched.  *browser_url*, when set, is where the
    reader can still look — a HathiTrust volume, a search."""

    def __init__(self, message: str, browser_url: str = ""):
        super().__init__(message)
        self.browser_url = browser_url


_SESSION = None
_SESSION_LOCK = threading.Lock()


def _session():
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            import requests
            s = requests.Session()
            s.headers["User-Agent"] = USER_AGENT
            _SESSION = s
        return _SESSION


# ---------------------------------------------------------------------------
# The disk cache
# ---------------------------------------------------------------------------

def _cache_path(key: str) -> Path:
    return CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".pdf")


def _cached(key: str) -> Optional[bytes]:
    try:
        data = _cache_path(key).read_bytes()
    except OSError:
        return None
    return data if data.startswith(b"%PDF") else None


def _keep(key: str, data: bytes) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _cache_path(key).with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(_cache_path(key))
    except OSError as exc:
        print(f"[leghist] cache write failed: {exc}")


def _get_pdf(url: str, *, max_bytes: int = 150_000_000) -> tuple[bytes, str]:
    """The PDF at *url* (following the link service's redirect), and the
    URL it came from — ``#page=N`` and all."""
    cached = _cached("whole:" + url)
    if cached is not None:
        meta = _cached_meta("whole:" + url)
        return cached, meta.get("final", url)
    r = _session().get(url, timeout=TIMEOUT, stream=True)
    try:
        if r.status_code != 200:
            raise Unavailable(f"HTTP {r.status_code}")
        final = r.url
        chunks, total = [], 0
        for chunk in r.iter_content(1 << 16):
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise Unavailable("the file is too large to fetch whole")
        data = b"".join(chunks)
    finally:
        r.close()
    if not data.startswith(b"%PDF"):
        raise Unavailable("the source did not return a PDF")
    _keep("whole:" + url, data)
    _keep_meta("whole:" + url, {"final": final})
    return data, final


def _cached_meta(key: str) -> dict:
    try:
        return json.loads(_cache_path(key).with_suffix(".json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _keep_meta(key: str, meta: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).with_suffix(".json").write_text(json.dumps(meta), "utf-8")
    except OSError:
        pass


def _cut(url: str, first: int, last: int, *, whole_ok: bool = True) -> tuple[bytes, int]:
    """Pages *first*..*last* (0-based) of the PDF at *url*, and the file's
    page count — cut by range requests, or out of the whole file where the
    server won't answer them (unless not *whole_ok*: a volume of the Globe
    is some 200 MB)."""
    key = f"cut:{url}:{first}:{last}"
    cached = _cached(key)
    if cached is not None:
        return cached, int(_cached_meta(key).get("total") or 0)
    try:
        pdf = pdf_range.open_url(url, session=_session(), timeout=TIMEOUT)
        total = pdf.page_count()
        last = min(last, total - 1)
        data = pdf.cut(list(range(first, last + 1)))
    except (pdf_range.PdfRangeError, OSError) as exc:
        if not whole_ok:
            raise Unavailable(f"The pages could not be cut out of the scan ({exc}).") from None
        print(f"[leghist] cutting pages failed ({exc}); fetching the whole file")
        whole, _final = _get_pdf(url)
        data, total = _cut_local(whole, first, last)
    _keep(key, data)
    _keep_meta(key, {"total": total})
    return data, total


def _cut_local(data: bytes, first: int, last: int) -> tuple[bytes, int]:
    import io
    import pypdfium2 as pdfium
    from pdfium_lock import PDFIUM_LOCK

    with PDFIUM_LOCK:
        src = pdfium.PdfDocument(data)
        try:
            total = len(src)
            last = min(last, total - 1)
            new = pdfium.PdfDocument.new()
            new.import_pages(src, list(range(first, last + 1)))
            buf = io.BytesIO()
            new.save(buf)
            new.close()
        finally:
            src.close()
    return buf.getvalue(), total


# ---------------------------------------------------------------------------
# Printed page numbers
# ---------------------------------------------------------------------------

def _page_texts(data: bytes, indexes: "Optional[list[int]]" = None) -> list[str]:
    """The text layer of each page (all, or *indexes*), "" where there is
    none; the PDFium lock is taken page by page."""
    import pypdfium2 as pdfium
    from pdfium_lock import PDFIUM_LOCK

    out: list[str] = []
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(data)
        count = len(doc)
    try:
        for i in (indexes if indexes is not None else range(count)):
            if not 0 <= i < count:
                out.append("")
                continue
            with PDFIUM_LOCK:
                try:
                    page = doc[i]
                    tp = page.get_textpage()
                    out.append(tp.get_text_range() or "")
                    tp.close()
                    page.close()
                except Exception:
                    out.append("")
    finally:
        with PDFIUM_LOCK:
            doc.close()
    return out


def _printed_number(text: str, near: int, spread: int = 40) -> Optional[int]:
    """The page number printed in a page's running head or foot — the
    number within *spread* of *near* standing on the first or last lines
    ("37264 CONGRESSIONAL RECORD- SENATE", "November 16, 1970 ...
    '37273", "- 41 -")."""
    lines = [ln for ln in re.split(r"[\r\n]+", text or "") if ln.strip()]
    for ln in lines[:2] + lines[-2:]:
        for tok in re.findall(r"(?<![\d,])[SHED]?\s?(\d{1,3}(?:,\d{3})+|\d{1,6})(?![\d,])", ln):
            n = int(tok.replace(",", ""))
            if abs(n - near) <= spread:
                return n
    return None


def _find_printed_page(data: bytes, page: int, guess: int, total: int) -> Optional[int]:
    """The index of the page printed *page*, looking first at *guess*,
    then its neighbours — possibly outside ``range(total)``, where the
    numbers the pages do print put it; None if no page says."""
    order = [guess] + [guess + d for k in range(1, 6) for d in (k, -k)]
    order = [i for i in order if 0 <= i < total]
    texts = _page_texts(data, order)
    found = {}
    for i, t in zip(order, texts):
        n = _printed_number(t, page)
        if n is not None:
            found[i] = n
            if n == page:
                return i
    # A neighbour's number says how far off the guess is.
    for i, n in found.items():
        return i + (page - n)
    return None


# ---------------------------------------------------------------------------
# The Congressional Record
# ---------------------------------------------------------------------------

def _cr(s: dict) -> Pages:
    vol = int(s.get("vol") or 0)
    page = str(s.get("page") or "")
    m = re.fullmatch(r"([SHEDA]?)(\d+)", page)
    if not m:
        raise Unavailable("That page number can't be read.")
    pfx, num = m.group(1), int(m.group(2))
    title = lh.spec_label(s)
    if s.get("daily") and pfx and vol >= lh.CR_DAILY_MIN_VOL:
        return _cr_daily(vol, pfx, num, title)
    if s.get("daily"):
        return _cr_daily_in_bound(s, vol, pfx, num, title)
    return _cr_bound(vol, num, title, appendix=(pfx == "A"),
                     chamber=s.get("chamber", ""))


def _cr_daily(vol: int, pfx: str, num: int, title: str) -> Pages:
    url = f"{GOVINFO_LINK}/crec/{vol}/{pfx.lower()}/{num}?link-type=pdf"
    try:
        data, final = _get_pdf(url)
    except Unavailable as exc:
        raise Unavailable(
            f"GovInfo has no daily-edition page {pfx}{num} in volume {vol} ({exc}).",
            url) from None
    pm = re.search(r"#page=(\d+)", final)
    index = int(pm.group(1)) - 1 if pm else 0
    return Pages(data, index, title, "GovInfo", final.split("#")[0])


def _cr_bound(vol: int, num: int, title: str, *, appendix: bool = False,
              chamber: str = "") -> Pages:
    g = lh.crecb_granule(vol, num, appendix=appendix, chamber=chamber)
    if g is None:
        raise Unavailable(
            f"GovInfo's bound Congressional Record has no page {num} in volume {vol}.",
            f"https://www.govinfo.gov/app/collection/crecb")
    return _granule_pages(g, num, title)


def _granule_pages(g: "lh.Granule", num: int, title: str, note: str = "",
                   _retry: bool = True, _offset: "Optional[int]" = None) -> Pages:
    """Page *num* of granule *g*, with a few pages around it.  The page's
    place in the PDF is its distance from the granule's first page, checked
    against the number the page prints in its running head (an unnumbered
    insert shifts it)."""
    offset = max(0, num - g.first) if _offset is None else _offset
    if not g.path.startswith("GPO-"):
        # From 1999 GovInfo's bound Record is born digital, a day to a
        # small PDF: fetched whole.
        data, _final = _get_pdf(g.url)
        total = _page_count(data)
        guess = min(offset, total - 1)
        index = _find_printed_page(data, num, guess, total)
        if index is None or not 0 <= index < total:
            index = guess
        return Pages(data, index, title, "GovInfo", g.url, note)
    first = max(0, offset - BEFORE)
    data, _total = _cut(g.url, first, offset + AFTER)
    n = _page_count(data)
    guess = min(offset - first, n - 1)
    index = _find_printed_page(data, num, guess, n)
    if index is not None and not 0 <= index < n and _retry:
        # The page lies outside the pages cut: cut again around it.
        return _granule_pages(g, num, title, note, _retry=False,
                              _offset=max(0, first + index))
    if index is None or not 0 <= index < n:
        index = guess
    return Pages(data, index, title, "GovInfo",
                 f"{g.url}#page={first + index + 1}", note)


def _page_count(data: bytes) -> int:
    import pypdfium2 as pdfium
    from pdfium_lock import PDFIUM_LOCK

    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(data)
        try:
            return len(doc)
        finally:
            doc.close()


def _cr_daily_in_bound(s: dict, vol: int, pfx: str, num: int, title: str) -> Pages:
    date = s.get("date", "")
    if not date:
        raise Unavailable("The daily edition before 1995 isn't online, and the "
                          "citation gives no date to find the day in the bound edition.")
    vols = [vol] + [v for v in lh.cr_volumes_for_year(int(date[:4])) if v != vol]
    for v in vols:
        if pfx == "D":
            g = lh.crecb_granule(v, num, digest=True)
            if g is not None:
                return _granule_pages(g, num, title, note=(
                    "The daily edition isn't online before 1995; this is the "
                    "Daily Digest as the bound edition reprints it."))
            continue
        g = lh.crecb_day(v, date, pfx)
        if g is not None:
            where = {"S": "the Senate's", "H": "the House's",
                     "E": "the Extensions of Remarks'"}.get(pfx, "the")
            note = (f"The daily edition isn't online before 1995. This is {where} "
                    f"record of {lh._date_label(date)} in the bound edition, which "
                    f"numbers its pages differently: daily page {pfx}{num} is in here.")
            data, total = _cut(g.url, 0, min(g.last - g.first, 40))
            return Pages(data, 0, title, "GovInfo", g.url, note)
    raise Unavailable(f"The bound Congressional Record has nothing for {lh._date_label(date)}.")


# ---------------------------------------------------------------------------
# The Annals, the Register, the Globe
# ---------------------------------------------------------------------------

def _debates(s: dict) -> Pages:
    # The first two volumes of the Annals were printed twice, their columns
    # numbered differently; the Library of Congress scanned the printing
    # headed "Gales & Seaton's History", where a column the courts cite
    # (from the printing headed "History of Congress") comes some pages on
    # — so more pages after it are cut, and the reader is told.
    two_printings = s.get("src") == "annals" and int(s.get("vol") or 0) in (1, 2)
    pages, at = lh.debates_pages(s, BEFORE, 14 if two_printings else AFTER)
    if not pages:
        raise Unavailable(
            f"The Library of Congress's scans have no {lh.spec_label(s)}.",
            "https://www.congress.gov/browse")
    hit = pages[at]
    spec = dict(s)
    if hit.date and not spec.get("year"):
        spec["year"] = int(hit.date[:4])
    title = lh.spec_label(spec)
    source = "Library of Congress (Congress.gov)"
    note = ("Volumes 1 and 2 of the Annals were printed twice, with different "
            "column numbers. This is the printing headed “Gales & Seaton's "
            "History”; in the one headed “History of Congress,” which "
            "courts usually cite, the same passage falls a few pages earlier, "
            "so it may be further on here." if two_printings else "")
    if hit.url:
        # Pages of the same PDF around the cited one, cut out of it.
        same = [p for p in pages if p.url == hit.url and p.index >= 0]
        first = min(p.index for p in same)
        try:
            data, _total = _cut(hit.url, first, max(p.index for p in same),
                                whole_ok=False)
            return Pages(data, hit.index - first, title, source,
                         f"{hit.url}#page={hit.index + 1}", note)
        except Unavailable as exc:
            print(f"[leghist] {exc} Fetching the page's image instead.")
            if not hit.image:
                raise
    # No PDF page to match it to: the page's own image (large — the Globe's
    # run to 7 MB — so just it and the page after), made into a PDF.
    images = [p.image for p in pages[at:at + 2] if p.image]
    data = _images_pdf(images)
    return Pages(data, 0, title, source, hit.image, note)


def _images_pdf(urls: list[str], width: int = 1700) -> bytes:
    """A PDF of the page images at *urls*, each scaled to *width* pixels
    across (the scans are far finer than a screen needs)."""
    import io
    from PIL import Image

    key = "images:" + "|".join(urls)
    cached = _cached(key)
    if cached is not None:
        return cached

    def get(url: str):
        return _session().get(url, timeout=TIMEOUT)

    # Side by side: each is several megabytes.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(urls) or 1) as pool:
        answers = list(pool.map(get, urls))
    pages = []
    for url, r in zip(urls, answers):
        if r.status_code != 200:
            raise Unavailable(f"The page image could not be fetched (HTTP {r.status_code}).", url)
        img = Image.open(io.BytesIO(r.content))
        img = img.convert("L") if img.mode not in ("L", "RGB") else img
        if img.width > width:
            img = img.resize((width, round(img.height * width / img.width)))
        pages.append(img)
    if not pages:
        raise Unavailable("The page has no image to fetch.")
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:],
                  resolution=150, quality=80)
    data = buf.getvalue()
    _keep(key, data)
    return data


# ---------------------------------------------------------------------------
# Reports and documents
# ---------------------------------------------------------------------------

_RPT_TYPES = {"s": "srpt", "h": "hrpt"}
_DOC_TYPES = {("h", "doc"): "hdoc", ("s", "doc"): "sdoc", ("s", "treaty"): "tdoc",
              ("h", "exec"): "hedoc", ("s", "exec"): "sedoc",
              ("h", "misc"): "hmdoc", ("s", "misc"): "smdoc"}


def _paper_urls(s: dict, cong: int) -> list[str]:
    num, part = int(s.get("num") or 0), int(s.get("part") or 0)
    if s.get("src") == "rpt":
        kind = "erpt" if s.get("exec") else _RPT_TYPES[s.get("ch", "h")]
        urls = [f"{GOVINFO_LINK}/crpt/{cong}/{kind}/{num}?link-type=pdf"]
        if cong >= 104:
            pkg = f"CRPT-{cong}{kind}{num}" + (f"-pt{part}" if part else "")
            urls.append(f"{lh.GOVINFO_PKG}{pkg}/pdf/{pkg}.pdf")
        return urls
    kind = _DOC_TYPES.get((s.get("ch", "h"), s.get("type", "doc")), "hdoc")
    urls = [f"{GOVINFO_LINK}/cdoc/{cong}/{kind}/{num}?link-type=pdf"]
    if cong >= 104:
        pkg = f"CDOC-{cong}{kind}{num}"
        urls.append(f"{lh.GOVINFO_PKG}{pkg}/pdf/{pkg}.pdf")
    return urls


def _paper(s: dict) -> Pages:
    congs = [int(s["cong"])] if s.get("cong") else [
        int(c) for c in str(s.get("congs") or "").split(",") if c.strip()]
    tried = []
    for cong in congs:
        spec = dict(s, cong=cong)
        spec.pop("congs", None)
        for url in _paper_urls(spec, cong):
            tried.append(url)
            try:
                data, final = _get_pdf(url)
            except Exception:
                continue
            return _at_pin(data, spec, "GovInfo", final.split("#")[0])
    for cong in congs:
        spec = dict(s, cong=cong)
        spec.pop("congs", None)
        found = _internet_archive(spec)
        if found is not None:
            return found
    label = lh.spec_label(dict(s, cong=congs[0] if congs else 0), with_pin=False)
    hathi = _hathitrust(dict(s, cong=congs[0] if congs else 0))
    if hathi:
        raise Unavailable(
            f"No free copy of {label} could be fetched. HathiTrust has it — "
            "open it there in your browser.", hathi)
    raise Unavailable(f"No free copy of {label} could be found online.",
                      _hathitrust_search_url(dict(s, cong=congs[0] if congs else 0)))


def _at_pin(data: bytes, s: dict, source: str, url: str) -> Pages:
    title = lh.spec_label(s)
    pin = str(s.get("pin") or "")
    index = 0
    if pin.isdigit():
        total = _page_count(data)
        want = int(pin)
        guess = min(max(0, want - 1), total - 1)
        found = _find_printed_page(data, want, guess, total)
        index = found if found is not None and 0 <= found < total else guess
    return Pages(data, index, title, source, url)


def _ia_search(query: str, rows: int = 15) -> list[dict]:
    try:
        r = _session().get("https://archive.org/advancedsearch.php", params={
            "q": query, "fl[]": ["identifier", "title", "description"],
            "rows": rows, "output": "json"}, timeout=TIMEOUT)
        return (r.json().get("response") or {}).get("docs") or []
    except Exception as exc:
        print(f"[leghist] Internet Archive search failed: {exc}")
        return []


def _names_report(text: str, s: dict) -> bool:
    """Whether a catalogue entry's words name exactly this report or
    document — the number right after the word for it ("Report No.
    94-1476", "(House Report 94-1476)", "H. Rept. 94-1476", "Report / 94th
    Congress, 2d session, House of Representatives ; no. 94-1476") and the
    House or Senate somewhere in them."""
    cong, num = s.get("cong"), s.get("num")
    t = re.sub(r"\s+", " ", text or "")
    kind = (r"(?:Rep(?:ort|t)?\.?|Rpt\.?)" if s.get("src") == "rpt"
            else r"(?:Doc(?:ument)?\.?)")
    number = rf"{cong}\s*[-–]\s*{num}(?!\d)"
    named = (re.search(rf"{kind}\s*(?:No\.?\s*)?{number}", t, re.IGNORECASE)
             or re.search(rf"{kind}\b[^;]{{0,80}};\s*no\.\s*{number}", t, re.IGNORECASE))
    if not named:
        return False
    house = s.get("ch") == "h"
    word = r"(?:House|H\.\s?R?\.?|Representatives)" if house else r"(?:Senate|S\.)"
    return bool(re.search(word, t, re.IGNORECASE))


_BILL_TITLE_RE = re.compile(r"^\W*(?:ERIC\s+\w+:\s*)?(?:S\.|H\.\s?R\.|H\.\s?J\.\s?Res\.)\s*\d+\s*[;,.]|\bAn Act\b",
                            re.IGNORECASE)


def _ia_rank(title: str, s: dict) -> int:
    """How surely an Internet Archive item's title is the report itself —
    not the bill as reported, which carries the report's number too ("S.
    22; An Act for the General Revision of the Copyright Law ... Report No.
    94-1476").  0: not it."""
    if not _names_report(title, s):
        return 0
    if _BILL_TITLE_RE.search(title):
        return 0
    rank = 1
    if re.search(r"\breport\b(?!\s*(?:No\.?\s*)?\d)", title, re.IGNORECASE):
        rank += 1                   # "Report Together with Additional Views"
    return rank


def _internet_archive(s: dict) -> Optional[Pages]:
    cong, num = s.get("cong"), s.get("num")
    if not cong or not num:
        return None
    found = _ia_search(f'"{cong}-{num}" AND mediatype:texts')
    # The title must name the report: a bill's or a hearing's entry can
    # mention the report's number too.
    ranked = sorted(((r, d) for d in found
                     if (r := _ia_rank(str(d.get("title") or ""), s))),
                    key=lambda t: -t[0])
    for _rank, doc in ranked:
        ident = doc.get("identifier")
        try:
            meta = _session().get(f"https://archive.org/metadata/{ident}", timeout=TIMEOUT).json()
        except Exception:
            continue
        files = meta.get("files") or []
        pdfs = [f["name"] for f in files if f.get("name", "").lower().endswith(".pdf")]
        if not pdfs:
            continue
        pdf_url = f"https://archive.org/download/{ident}/{urllib.parse.quote(pdfs[0])}"
        pin = str(s.get("pin") or "")
        leaf = _ia_leaf(ident, files, pin) if pin.isdigit() else None
        title = lh.spec_label(s)
        page_url = f"https://archive.org/details/{ident}"
        if leaf is not None:
            first = max(0, leaf - BEFORE)
            try:
                data, _total = _cut(pdf_url, first, leaf + AFTER)
                return Pages(data, leaf - first, title, "Internet Archive", page_url)
            except Exception as exc:
                print(f"[leghist] Internet Archive pages failed: {exc}")
        try:
            data, _final = _get_pdf(pdf_url)
        except Exception:
            continue
        return _at_pin(data, s, "Internet Archive", page_url)
    return None


def _ia_leaf(ident: str, files: list, pin: str) -> Optional[int]:
    """The PDF page (0-based) printed *pin*, from the item's page-number
    file."""
    name = next((f["name"] for f in files
                 if f.get("name", "").endswith("_page_numbers.json")), None)
    if not name:
        return None
    try:
        data = _session().get(f"https://archive.org/download/{ident}/{name}",
                              timeout=TIMEOUT).json()
    except Exception:
        return None
    pages = data.get("pages") or []
    for i, p in enumerate(pages):
        if str(p.get("pageNumber") or "") == pin:
            return i
    return None


def _chamber_word(s: dict) -> str:
    """The word a report's or document's title page prints for the chamber
    that issued it — "Senate", or "House" (of Representatives) — or "" where
    the spec doesn't say."""
    return {"s": "Senate", "h": "House"}.get(s.get("ch", ""), "")


def _hathitrust_records(lookfor: str) -> list[str]:
    """The catalogue record numbers a HathiTrust search for *lookfor*
    finds, in its order."""
    try:
        r = _session().get("https://catalog.hathitrust.org/Search/Home", params={
            "lookfor": lookfor, "type": "all", "pagesize": 20},
            timeout=TIMEOUT)
        return list(dict.fromkeys(re.findall(r"/Record/(\d+)\?", r.text)))
    except Exception:
        return []


def _hathitrust(s: dict) -> str:
    """The HathiTrust volume holding the report, for the browser — found in
    its catalogue by the series statement libraries give reports ("Report /
    94th Congress, 2d session, House of Representatives ; no. 94-1476").

    The chamber is searched for with the number: a House and a Senate report
    of one Congress often share a number, and the other chamber's can fill
    the first results.  A catalogue that doesn't spell the chamber out is
    searched again for the number alone."""
    cong, num = s.get("cong"), s.get("num")
    if not cong or not num:
        return ""
    number = f'"no. {cong}-{num}"'
    chamber = _chamber_word(s)
    checked: set[str] = set()
    for lookfor in ([f"{number} {chamber}"] if chamber else []) + [number]:
        for rec in _hathitrust_records(lookfor)[:6]:
            if rec in checked:
                continue
            checked.add(rec)
            try:
                d = _session().get(
                    f"https://catalog.hathitrust.org/api/volumes/full/recordnumber/{rec}.json",
                    timeout=TIMEOUT).json()
            except Exception:
                continue
            for rv in (d.get("records") or {}).values():
                marc = rv.get("marc-xml", "")
                series = " ".join(re.findall(r'<datafield tag="(?:490|830|086|245)"[^>]*>(.*?)</datafield>', marc, re.S))
                series = re.sub(r"<[^>]+>", " ", series)
                if not _names_report(series, s):
                    continue
                for item in d.get("items") or []:
                    if "Full view" in (item.get("usRightsString") or "") and item.get("htid"):
                        return f"https://babel.hathitrust.org/cgi/pt?id={item['htid']}"
    return ""


def _hathitrust_search_url(s: dict) -> str:
    """A full-text search of HathiTrust's public-domain volumes for the
    report as its title page prints it: "Report No. 91-1234" from the 91st
    Congress (1969) on, "Report No. 245" with its "80th Congress" before —
    and the chamber the title page names, "Senate" or "House" (of
    Representatives), so the other chamber's report of that number isn't
    found with it."""
    cong, num = int(s.get("cong") or 0), s.get("num")
    word = "Report" if s.get("src") == "rpt" else "Document"
    if cong >= 91:
        query, mode = f'"{word} No. {cong}-{num}"', "all"
    elif cong:
        th = "th" if 10 <= cong % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(cong % 10, "th")
        query, mode = f'"{word} No. {num}" "{cong}{th} Congress"', "all"
    else:
        query, mode = f'"{word} No. {num}"', "all"
    chamber = _chamber_word(s)
    if chamber:
        query += " " + chamber
    return ("https://babel.hathitrust.org/cgi/ls?lmt=ft&anyall1=" + mode
            + "&q1=" + urllib.parse.quote(query))


# ---------------------------------------------------------------------------
# Everything
# ---------------------------------------------------------------------------

def fetch(spec: "str | dict") -> Pages:
    """The pages *spec* names.  Worker thread: this waits on the network.
    Raises :class:`Unavailable` when no free copy can be had."""
    s = lh.parse_spec(spec)
    src = s.get("src")
    if src == "cr":
        return _cr(s)
    if src in ("globe", "annals", "regdeb"):
        return _debates(s)
    if src in ("rpt", "doc"):
        return _paper(s)
    raise Unavailable("That isn't a citation this can open.")


def browser_url(spec: "str | dict") -> str:
    """Where to read *spec* in a web browser, without fetching anything
    beyond the offline indexes."""
    s = lh.parse_spec(spec)
    src = s.get("src")
    if src == "cr":
        page = str(s.get("page") or "")
        m = re.fullmatch(r"([SHEDA]?)(\d+)", page)
        if not m:
            return ""
        pfx, num = m.group(1), int(m.group(2))
        vol = int(s.get("vol") or 0)
        if s.get("daily") and pfx and vol >= lh.CR_DAILY_MIN_VOL:
            return f"{GOVINFO_LINK}/crec/{vol}/{pfx.lower()}/{num}"
        if not s.get("daily"):
            g = lh.crecb_granule(vol, num, appendix=(pfx == "A"))
            if g is not None:
                return f"{g.url}#page={num - g.first + 1}"
        return "https://www.govinfo.gov/app/collection/crecb"
    if src in ("globe", "annals", "regdeb"):
        hit = lh.debates_page(s)
        if hit is None:
            return "https://www.congress.gov/browse"
        return f"{hit.url}#page={hit.index + 1}" if hit.url else hit.image
    if src in ("rpt", "doc"):
        cong = s.get("cong") or (str(s.get("congs") or "").split(",") or [""])[0]
        if cong:
            return _paper_urls(dict(s, cong=int(cong)), int(cong))[0].split("?")[0]
    return ""
