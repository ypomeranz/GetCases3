"""Fetch the pages of a decision in the SEC's Decisions and Reports from
HathiTrust, for the app's own viewer.

HathiTrust stands behind a CloudFlare check that admits people, not scripts.
So, as for the English Reports on CommonLII (see :mod:`eng_rep_pdf`, whose
machinery this uses), the reader passes the check once in Firefox and the
app fetches with the clearance Firefox then holds: its cookies, sent under
the User-Agent of the Firefox that obtained them, over a connection that
looks like Firefox's.  Without a clearance CloudFlare accepts, a fetch
raises :class:`eng_rep_pdf.CloudflareChallenge`, and the viewer offers to
open the page in Firefox to pass the check -- or in the browser instead.

Each page comes from HathiTrust's page service as a PDF of that one page
(``imgsrv/download/pdf``) -- or, where the service will not give one, as
the page's image, made into a PDF here -- and is kept on disk, so a page
fetched once opens again with no network and no check.  The pages of the
decision cited, from its first page to the page the next decision begins
on, are put together into one PDF opened at the page cited; a decision too
long to fetch whole (a few run past a hundred pages) is cut to the
:data:`MAX_PAGES` around that page.

The page cited is fetched first: it is the one the reader asked for, and
whether CloudFlare accepts the clearance is known from it.  Should the rest
not all come -- HathiTrust asked for its check again partway, or refused to
send more for a while -- the pages that did come are shown, with a note.

Network and PDF work only -- no GUI.  PDFium is called with
:data:`pdfium_lock.PDFIUM_LOCK` held.  Run ``python -X utf8 sec_pdf.py``
to see whether Firefox holds a HathiTrust clearance and to fetch a sample
page live.
"""

from __future__ import annotations

import io
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Optional

import eng_rep_pdf
import sec_decisions

#: The cookie host of HathiTrust's CloudFlare clearance.
SITE = "hathitrust"
PAGE_PDF_URL = "https://babel.hathitrust.org/cgi/imgsrv/download/pdf"
PAGE_IMAGE_URL = "https://babel.hathitrust.org/cgi/imgsrv/image"
CACHE_DIR = Path.home() / ".config" / "courtlistener" / "sec_cache"

#: The most pages fetched for one decision.  Nine in ten decisions run to
#: fewer; a longer one is cut around the page cited.
MAX_PAGES = 24
#: Of a decision cut short, how many pages are kept before the page cited.
BEFORE = 4
#: Pages fetched side by side, as a browser turning them would.
WORKERS = 3
#: How wide a page image is asked for, in pixels.
IMAGE_WIDTH = 1700

#: Told what a fetch is doing, when set: ``on_step(text)`` as each page is
#: fetched.  The GUI points it at its status reporting, which knows from the
#: calling thread which load, if any, is being watched.
on_step = None


def _tell(text: str) -> None:
    if on_step is not None:
        try:
            on_step(text)
        except Exception:
            pass


@dataclass
class Pages:
    """What the viewer shows: *data* (a PDF of printed pages *first* to
    *last*), opened at page *index* (0-based) -- the page cited."""
    data: bytes
    index: int
    title: str
    first: int
    last: int
    note: str = ""


class Unavailable(Exception):
    """The citation can't be placed in HathiTrust's scans (the page index
    is missing, or doesn't know the page); only the browser can look."""


# ---------------------------------------------------------------------------
# Which pages
# ---------------------------------------------------------------------------

class Window(NamedTuple):
    """The printed pages to fetch for a citation: *first* to *last* of
    volume *vol*, opened at *pin*; *cut* when the decision runs longer."""
    vol: int
    first: int
    last: int
    pin: int
    cut: bool


def page_window(spec: "str | dict") -> Window:
    """The pages to fetch for *spec*: the decision it cites, from its first
    page to the page the next one begins on -- no more than
    :data:`MAX_PAGES` of them, around the page cited."""
    s = sec_decisions.parse_spec(spec)
    try:
        vol = int(s.get("vol") or 0)
        start = int(s.get("page") or 0)
        pin = int(s.get("pin") or start)
    except (TypeError, ValueError):
        return Window(0, 0, 0, 0, False)
    first = min(start, pin)
    last = max(sec_decisions.decision_end(vol, start), pin)
    if last - first + 1 <= MAX_PAGES:
        return Window(vol, first, last, pin, False)
    lo = max(first, pin - BEFORE)
    hi = min(last, lo + MAX_PAGES - 1)
    return Window(vol, max(first, hi - MAX_PAGES + 1), hi, pin, True)


def page_pdf_url(where: "sec_decisions.ScanPage") -> str:
    """HathiTrust's PDF of the one scan page *where*."""
    return f"{PAGE_PDF_URL}?id={where.htid}&attachment=1&seq={where.seq}"


def page_image_url(where: "sec_decisions.ScanPage") -> str:
    """HathiTrust's image of the scan page *where*."""
    return f"{PAGE_IMAGE_URL}?id={where.htid}&seq={where.seq}&width={IMAGE_WIDTH}"


# ---------------------------------------------------------------------------
# The disk cache: one single-page PDF a scan page
# ---------------------------------------------------------------------------

def cache_path(where: "sec_decisions.ScanPage") -> Path:
    htid = re.sub(r"[^\w.-]+", "_", where.htid)
    return CACHE_DIR / f"{htid}-{where.seq:05d}.pdf"


def _cached(where) -> Optional[bytes]:
    try:
        data = cache_path(where).read_bytes()
    except OSError:
        return None
    return data if data.startswith(b"%PDF") else None


def _keep(where, data: bytes) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = cache_path(where)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError as exc:
        print(f"[sec_pdf] cache write failed: {exc}")


# ---------------------------------------------------------------------------
# PDF work
# ---------------------------------------------------------------------------

def _pdfium():
    import pypdfium2 as pdfium
    from pdfium_lock import PDFIUM_LOCK
    return pdfium, PDFIUM_LOCK


def _save(doc) -> bytes:
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _scan_page_only(data: bytes) -> Optional[bytes]:
    """The scan page of the PDF HathiTrust made of one page: its last page,
    since a cover sheet, where HathiTrust adds one, goes in front.  None for
    anything else -- a file that won't open, or more pages than a page and
    its cover sheet."""
    pdfium, lock = _pdfium()
    with lock:
        try:
            src = pdfium.PdfDocument(data)
        except Exception as exc:
            print(f"[sec_pdf] the page's PDF won't open: {exc}")
            return None
        try:
            count = len(src)
            if count == 1:
                return data
            if count != 2:
                return None
            out = pdfium.PdfDocument.new()
            try:
                out.import_pages(src, [count - 1])
                return _save(out)
            finally:
                out.close()
        finally:
            src.close()


def _merge(pages: "list[bytes]") -> bytes:
    """One PDF of the one-page PDFs *pages*, in order."""
    if len(pages) == 1:
        return pages[0]
    pdfium, lock = _pdfium()
    with lock:
        out = pdfium.PdfDocument.new()
        sources = []
        try:
            for data in pages:
                src = pdfium.PdfDocument(data)
                sources.append(src)
                out.import_pages(src)
            return _save(out)
        finally:
            out.close()
            for src in sources:
                src.close()


_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"II*\x00", b"MM\x00*",
                b"\x00\x00\x00\x0cjP")


def _is_image(data: bytes) -> bool:
    return data.startswith(_IMAGE_MAGIC)


def _image_pdf(data: bytes) -> bytes:
    """A one-page PDF of the page image *data*, no wider than
    :data:`IMAGE_WIDTH` pixels."""
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode == "1":
        img = img.convert("L")
    elif img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    if img.width > IMAGE_WIDTH:
        img = img.resize((IMAGE_WIDTH,
                          round(img.height * IMAGE_WIDTH / img.width)))
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=200)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

class _Fetcher:
    """Fetches scan pages for one decision, with one reading of Firefox's
    clearances and one choice of route: the page service's PDF, or -- once
    it has refused to give one -- the page's image."""

    def __init__(self) -> None:
        self._candidates = None
        self.images = False

    def _clearances(self):
        if self._candidates is None:
            if not eng_rep_pdf.can_fetch():
                raise eng_rep_pdf.FetchUnavailable()
            self._candidates = eng_rep_pdf.clearances(SITE)
        return self._candidates

    def page(self, where: "sec_decisions.ScanPage") -> bytes:
        """The scan page *where* as a one-page PDF, from the disk cache or
        HathiTrust (see :func:`eng_rep_pdf.fetch_cleared` for what it
        raises)."""
        cached = _cached(where)
        if cached is not None:
            return cached
        candidates = self._clearances()
        # The viewer's own page, which a reader's browser would be on.
        referer = where.url
        data = None
        if not self.images:
            why = "not a single page"
            try:
                data = _scan_page_only(eng_rep_pdf.fetch_cleared(
                    page_pdf_url(where), SITE, referer=referer,
                    web_url=referer, candidates=candidates))
            except eng_rep_pdf.OriginError as exc:
                if exc.status in (0, 429):
                    raise      # the network, or too many pages too fast
                why = f"HTTP {exc.status}"
            if data is None:
                # CloudFlare let the request through, and the page service
                # would not give a PDF of the page: its image, then, here
                # and for the pages after.
                print(f"[sec_pdf] no PDF of {where.htid} page {where.seq} "
                      f"({why}); fetching page images instead")
                self.images = True
        if data is None:
            data = _image_pdf(eng_rep_pdf.fetch_cleared(
                page_image_url(where), SITE, referer=referer,
                web_url=referer, want=_is_image, candidates=candidates))
        _keep(where, data)
        return data


def _why(exc: Exception) -> str:
    """Why the pages stopped coming, in a few words."""
    if isinstance(exc, eng_rep_pdf.CloudflareChallenge):
        return "HathiTrust asked for its check again"
    if isinstance(exc, eng_rep_pdf.FetchUnavailable):
        return "the rest can't be fetched here"
    if isinstance(exc, eng_rep_pdf.OriginError) and exc.status == 429:
        return "HathiTrust asked the app to slow down"
    if isinstance(exc, eng_rep_pdf.OriginError) and exc.status:
        return f"HathiTrust answered HTTP {exc.status}"
    return "the connection failed"


def fetch(spec: "str | dict") -> Pages:
    """The pages of the decision *spec* cites, opened at the page cited.

    Raises :class:`Unavailable` when the page index can't place the page
    cited, and whatever :func:`eng_rep_pdf.fetch_cleared` raises for that
    page: :class:`eng_rep_pdf.CloudflareChallenge` (the reader must pass
    HathiTrust's check in Firefox), :class:`eng_rep_pdf.FetchUnavailable`
    (no Firefox or no ``curl_cffi``: only the browser can open it) and
    :class:`eng_rep_pdf.OriginError`.  Once the page cited is in hand, a
    failure on any other page is not raised: the pages that came are shown.
    """
    window = page_window(spec)
    vol, pin = window.vol, window.pin
    title = sec_decisions.spec_label(spec) or "SEC decision"
    where_pin = sec_decisions.locate(vol, pin) if vol else None
    if where_pin is None:
        raise Unavailable(f"{title} can't be placed in HathiTrust's scans.")
    # A page the index can't place (a gap in a copy) is simply not there.
    wanted = [(p, w) for p, w in
              ((p, sec_decisions.locate(vol, p))
               for p in range(window.first, window.last + 1))
              if w is not None]
    label = sec_decisions.spec_label(spec, with_pin=False) or title
    fetcher = _Fetcher()

    _tell(f"Fetching page {pin} of {label} from HathiTrust…")
    got: dict[int, bytes] = {pin: fetcher.page(where_pin)}
    missing = []
    for p, w in wanted:
        if p != pin:
            data = _cached(w)
            if data is not None:
                got[p] = data
            else:
                missing.append((p, w))

    failure: Optional[Exception] = None
    if missing:
        try:
            fetcher._clearances()     # read once, before the pages go out
        except Exception as exc:
            failure = exc
    if missing and failure is None:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(fetcher.page, w): p for p, w in missing}
            for future in as_completed(futures):
                p = futures[future]
                try:
                    got[p] = future.result()
                except Exception as exc:
                    if failure is None:
                        failure = exc
                        print(f"[sec_pdf] page {p} of {label} did not come: "
                              f"{exc!r}")
                        for other in futures:
                            other.cancel()
                    continue
                _tell(f"Fetched {len(got)} of {len(wanted)} pages of "
                      f"{label} from HathiTrust…")

    # The pages that came, as one run through the page cited: a page
    # missing from the middle would leave the rest out of their order.
    pages = [p for p, _w in wanted]
    at = pages.index(pin)
    lo = hi = at
    while lo > 0 and pages[lo - 1] in got:
        lo -= 1
    while hi + 1 < len(pages) and pages[hi + 1] in got:
        hi += 1
    run = pages[lo:hi + 1]
    note = ""
    if len(run) < len(pages):
        note = (f"Only pages {run[0]}–{run[-1]} came ({_why(failure)}); the "
                "rest of the decision is at HathiTrust.")
    elif window.cut:
        note = (f"A long decision: pages {run[0]}–{run[-1]} of it, around the "
                "page cited, are here; the rest is at HathiTrust.")
    return Pages(_merge([got[p] for p in run]), at - lo, title,
                 run[0], run[-1], note)


# ---------------------------------------------------------------------------
# Live test:  python -X utf8 sec_pdf.py [--fresh]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("can fetch in the app :", eng_rep_pdf.can_fetch())
    cands = eng_rep_pdf.clearances(SITE)
    print("hathitrust clearances:", "none" if not cands else "")
    for i, c in enumerate(cands, 1):
        print(f"  {i}. {c.db or 'browser_cookie3'} as "
              f"{c.user_agent.rsplit(' ', 1)[-1]}  cookies {sorted(c.cookies)}")
    spec = sec_decisions.make_spec(8, 893, 915)
    where = sec_decisions.locate(8, 915)
    if "--fresh" in sys.argv and where is not None:
        try:
            cache_path(where).unlink()
        except OSError:
            pass
    print(f"\nfetching 8 S.E.C. 915 (Chenery) — {where.url if where else '?'}")
    try:
        pages = _Fetcher().page(where)
        print(f"  OK: {len(pages):,} bytes; cached at {cache_path(where)}")
    except eng_rep_pdf.CloudflareChallenge as exc:
        print(f"  needs HathiTrust's check passed in Firefox: {exc.web_url}")
    except eng_rep_pdf.FetchUnavailable:
        print("  in-app fetch unavailable (needs Firefox + curl_cffi)")
    except eng_rep_pdf.OriginError as exc:
        print(f"  HathiTrust refused: {exc}")
