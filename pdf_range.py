"""Cut a few pages out of a large PDF on the web, reading only their bytes.

The scans legislative history lives in are big: a day of the bound
Congressional Record is some 35 MB on GovInfo, a part of the Congressional
Globe on Congress.gov over 200 MB.  Both servers answer HTTP range
requests, and a page of either is a handful of objects — the page, its
content stream, its image, perhaps the invisible OCR text's font — so
reading the file's cross-reference table and then just those objects gets
the pages in a few hundred kilobytes.

This is a deliberately small PDF reader, pure Python: no PDF library is
called, so nothing here holds the process-wide PDFium lock while the
network answers (the viewer renders with that lock held).  It reads classic
cross-reference tables and cross-reference streams, follows incremental
updates, takes objects out of object streams, and writes the pages it cuts
into a new, ordinary PDF: the objects keep their numbers, each page gets
the attributes it inherited from the page tree, and a new page tree holds
them.  Anything it cannot read raises :class:`PdfRangeError`, and the
caller falls back to fetching the whole file.
"""

from __future__ import annotations

import bisect
import re
import zlib
from dataclasses import dataclass, field
from typing import Callable, Optional


class PdfRangeError(Exception):
    """The file could not be read this way."""


# ---------------------------------------------------------------------------
# Remote bytes
# ---------------------------------------------------------------------------

class RemoteFile:
    """Byte ranges of a file on the web, each fetched once.

    *fetch(start, end)* returns bytes ``[start, end)`` (the caller supplies
    it, so a test can serve a local file).  Reads are coalesced: a batch of
    spans close together goes out as one request."""

    GAP = 96 * 1024          # merge spans closer than this into one request

    def __init__(self, size: int, fetch: Callable[[int, int], bytes]):
        self.size = size
        self._fetch = fetch
        self._have: list[tuple[int, bytes]] = []    # (start, data), sorted
        self.requests = 0
        self.fetched = 0

    def _covered(self, start: int, end: int) -> Optional[bytes]:
        for s, data in self._have:
            if s <= start and end <= s + len(data):
                return data[start - s:end - s]
        return None

    def prefetch(self, spans: list[tuple[int, int]]) -> None:
        """Fetch every span not already held, merging near neighbours."""
        need = sorted((max(0, s), min(self.size, e)) for s, e in spans
                      if self._covered(max(0, s), min(self.size, e)) is None)
        merged: list[list[int]] = []
        for s, e in need:
            if merged and s - merged[-1][1] <= self.GAP:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        for s, e in merged:
            if e <= s:
                continue
            data = self._fetch(s, e)
            if len(data) != e - s:
                raise PdfRangeError(f"short read {len(data)} of {e - s} at {s}")
            self.requests += 1
            self.fetched += len(data)
            self._have.append((s, data))
            self._have.sort(key=lambda t: t[0])

    def read(self, start: int, end: int) -> bytes:
        start, end = max(0, start), min(self.size, end)
        got = self._covered(start, end)
        if got is None:
            self.prefetch([(start, end)])
            got = self._covered(start, end)
        if got is None:
            raise PdfRangeError(f"could not read {start}-{end}")
        return got


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ref:
    num: int
    gen: int = 0


class Name(str):
    """A PDF name, without its slash."""


@dataclass
class Stream:
    dict: dict
    data: bytes            # the stream's bytes as stored (still encoded)


_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"
_NUM_RE = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")
_REF_RE = re.compile(rb"(\d+)\s+(\d+)\s+R(?![A-Za-z])")


class _Parser:
    def __init__(self, data: bytes, pos: int = 0):
        self.d = data
        self.p = pos

    def ws(self) -> None:
        d, n = self.d, len(self.d)
        while self.p < n:
            c = d[self.p]
            if c in _WS:
                self.p += 1
            elif c == 0x25:                         # % comment
                while self.p < n and d[self.p] not in b"\r\n":
                    self.p += 1
            else:
                break

    def value(self):
        self.ws()
        d, p = self.d, self.p
        if p >= len(d):
            raise PdfRangeError("unexpected end of data")
        c = d[p]
        if d.startswith(b"<<", p):
            self.p += 2
            out = {}
            while True:
                self.ws()
                if self.d.startswith(b">>", self.p):
                    self.p += 2
                    return out
                key = self.value()
                if not isinstance(key, Name):
                    raise PdfRangeError("dictionary key is not a name")
                out[str(key)] = self.value()
        if c == 0x5B:                               # [
            self.p += 1
            out = []
            while True:
                self.ws()
                if self.p < len(d) and d[self.p] == 0x5D:
                    self.p += 1
                    return out
                out.append(self.value())
        if c == 0x2F:                               # /Name
            q = p + 1
            while q < len(d) and d[q] not in _WS and d[q] not in _DELIM:
                q += 1
            raw = d[p + 1:q]
            self.p = q
            return Name(re.sub(rb"#([0-9A-Fa-f]{2})",
                               lambda m: bytes([int(m.group(1), 16)]),
                               raw).decode("latin-1"))
        if c == 0x28:                               # (string)
            depth, q, out = 0, p, bytearray()
            while q < len(d):
                ch = d[q]
                if ch == 0x5C:                      # backslash escape
                    out += d[q:q + 2]
                    q += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                    if depth == 0:
                        self.p = q + 1
                        return bytes(out[1:])
                out.append(ch)
                q += 1
            raise PdfRangeError("unterminated string")
        if c == 0x3C:                               # <hex>
            q = d.index(b">", p)
            self.p = q + 1
            return bytes(d[p:q + 1])
        m = _REF_RE.match(d, p)
        if m:
            self.p = m.end()
            return Ref(int(m.group(1)), int(m.group(2)))
        m = _NUM_RE.match(d, p)
        if m:
            self.p = m.end()
            t = m.group(0)
            return float(t) if b"." in t else int(t)
        q = p
        while q < len(d) and d[q] not in _WS and d[q] not in _DELIM:
            q += 1
        word = d[p:q]
        if not word:
            raise PdfRangeError(f"unexpected byte {d[p:p + 1]!r}")
        self.p = q
        return {b"true": True, b"false": False, b"null": None}.get(word, word)


def _serialize(v) -> bytes:
    if isinstance(v, Ref):
        return b"%d %d R" % (v.num, v.gen)
    if isinstance(v, Name):
        raw = v.encode("latin-1")
        return b"/" + re.sub(rb"[^!-~]|[()<>\[\]{}/%#]",
                             lambda m: b"#%02X" % m.group(0)[0], raw)
    if isinstance(v, bool):
        return b"true" if v else b"false"
    if v is None:
        return b"null"
    if isinstance(v, int):
        return str(v).encode()
    if isinstance(v, float):
        return (("%.6f" % v).rstrip("0").rstrip(".") or "0").encode()
    if isinstance(v, dict):
        return b"<<" + b" ".join(_serialize(Name(k)) + b" " + _serialize(x)
                                 for k, x in v.items()) + b">>"
    if isinstance(v, list):
        return b"[" + b" ".join(_serialize(x) for x in v) + b"]"
    if isinstance(v, bytes):
        if v.startswith(b"<") and v.endswith(b">"):
            return v                                # hex string, kept as read
        return b"(" + v + b")"                      # literal, escapes kept
    raise PdfRangeError(f"cannot write {type(v).__name__}")


def _refs(v, out: set) -> None:
    if isinstance(v, Ref):
        out.add(v.num)
    elif isinstance(v, dict):
        for x in v.values():
            _refs(x, out)
    elif isinstance(v, list):
        for x in v:
            _refs(x, out)
    elif isinstance(v, Stream):
        _refs(v.dict, out)


def _png_unpredict(data: bytes, columns: int) -> bytes:
    """Undo the PNG row predictors cross-reference streams use."""
    out = bytearray()
    prev = bytearray(columns)
    stride = columns + 1
    for i in range(0, len(data) - len(data) % stride, stride):
        kind, row = data[i], bytearray(data[i + 1:i + stride])
        if kind == 2:
            for j in range(columns):
                row[j] = (row[j] + prev[j]) & 0xFF
        elif kind == 1:
            for j in range(1, columns):
                row[j] = (row[j] + row[j - 1]) & 0xFF
        elif kind == 3:
            for j in range(columns):
                left = row[j - 1] if j else 0
                row[j] = (row[j] + ((left + prev[j]) >> 1)) & 0xFF
        elif kind == 4:
            for j in range(columns):
                a = row[j - 1] if j else 0
                b, c = prev[j], prev[j - 1] if j else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if pa <= pb and pa <= pc else b if pb <= pc else c
                row[j] = (row[j] + pr) & 0xFF
        out += row
        prev = row
    return bytes(out)


def _decode(stream: Stream) -> bytes:
    f = stream.dict.get("Filter")
    filters = f if isinstance(f, list) else [f] if f else []
    data = stream.data
    for flt in filters:
        if flt != "FlateDecode":
            raise PdfRangeError(f"cannot decode {flt}")
        data = zlib.decompress(data)
    parms = stream.dict.get("DecodeParms")
    if isinstance(parms, dict) and (parms.get("Predictor") or 1) >= 10:
        data = _png_unpredict(data, int(parms.get("Columns", 1)))
    return data


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

_INHERITED = ("Resources", "MediaBox", "CropBox", "Rotate")
# What a page's own dictionary may point to that the cut pages don't need.
_DROP = ("Parent", "Annots", "B", "Thumb", "StructParents", "PieceInfo",
         "Metadata", "Tabs", "AA")


@dataclass
class _Entry:
    kind: int              # 1: at an offset; 2: inside an object stream
    a: int                 # offset, or the object stream's number
    b: int = 0             # generation, or the index within the stream


@dataclass
class RemotePdf:
    file: RemoteFile
    xref: dict = field(default_factory=dict)       # num -> _Entry
    trailer: dict = field(default_factory=dict)
    _objs: dict = field(default_factory=dict)      # num -> value
    _raw: dict = field(default_factory=dict)       # num -> bytes as stored
    _ends: list = field(default_factory=list)      # sorted object offsets

    # -- reading ---------------------------------------------------------

    def load(self) -> "RemotePdf":
        size = self.file.size
        tail = self.file.read(max(0, size - 4096), size)
        m = list(re.finditer(rb"startxref\s+(\d+)", tail))
        if not m:
            raise PdfRangeError("no startxref")
        offset = int(m[-1].group(1))
        seen = set()
        while offset and offset not in seen:
            seen.add(offset)
            offset = self._read_xref(offset)
        if "Root" not in self.trailer:
            raise PdfRangeError("no document catalog")
        offs = sorted({e.a for e in self.xref.values() if e.kind == 1})
        self._ends = offs + [size]
        return self

    def _read_xref(self, offset: int) -> int:
        head = self.file.read(offset, offset + 64)
        if head.lstrip().startswith(b"xref"):
            return self._read_xref_table(offset)
        return self._read_xref_stream(offset)

    def _read_xref_table(self, offset: int) -> int:
        chunk = 65536
        data = self.file.read(offset, offset + chunk)
        while b"trailer" not in data and offset + len(data) < self.file.size:
            chunk *= 4
            data = self.file.read(offset, offset + chunk)
        t = data.find(b"trailer")
        if t < 0:
            raise PdfRangeError("no trailer")
        body = data[data.index(b"xref") + 4:t]
        cur = 0
        for line in (x.strip() for x in re.split(rb"\r\n|\r|\n", body)):
            if not line:
                continue
            parts = line.split()
            if len(parts) == 2:
                cur = int(parts[0])
                continue
            if len(parts) >= 3:
                if cur not in self.xref and parts[2][:1] == b"n":
                    self.xref[cur] = _Entry(1, int(parts[0]), int(parts[1]))
                elif cur not in self.xref:
                    self.xref[cur] = _Entry(0, 0)
                cur += 1
        # The trailer dictionary may run past what was read.
        tail = data[t + 7:]
        if b">>" not in tail[-4:] and offset + len(data) < self.file.size:
            tail = self.file.read(offset + t + 7, offset + t + 7 + 8192)
        trailer = _Parser(tail).value()
        for k, v in trailer.items():
            self.trailer.setdefault(k, v)
        if isinstance(trailer.get("XRefStm"), int):
            self._read_xref_stream(trailer["XRefStm"])
        return int(trailer.get("Prev") or 0)

    def _read_xref_stream(self, offset: int) -> int:
        num, obj = self._parse_at(offset, self.file.size)
        if not isinstance(obj, Stream) or obj.dict.get("Type") != "XRef":
            raise PdfRangeError("not a cross-reference stream")
        d = obj.dict
        data = _decode(obj)
        w = [int(x) for x in d["W"]]
        index = d.get("Index") or [0, int(d["Size"])]
        pos = 0
        rec = sum(w)
        for i in range(0, len(index), 2):
            first, count = int(index[i]), int(index[i + 1])
            for n in range(first, first + count):
                fields = []
                for width in w:
                    fields.append(int.from_bytes(data[pos:pos + width], "big") if width else None)
                    pos += width
                kind = fields[0] if w[0] else 1
                if n in self.xref:
                    continue
                if kind == 1:
                    self.xref[n] = _Entry(1, fields[1], fields[2] or 0)
                elif kind == 2:
                    self.xref[n] = _Entry(2, fields[1], fields[2] or 0)
                else:
                    self.xref[n] = _Entry(0, 0)
        for k, v in d.items():
            if k in ("Root", "Info", "ID", "Size", "Encrypt"):
                self.trailer.setdefault(k, v)
        return int(d.get("Prev") or 0)

    def _span(self, offset: int) -> tuple[int, int]:
        i = bisect.bisect_right(self._ends, offset)
        end = self._ends[i] if i < len(self._ends) else self.file.size
        return offset, end

    def _parse_at(self, offset: int, end: int) -> tuple[int, object]:
        # Read a first slice; a stream's full length is known once its
        # dictionary is.
        first = self.file.read(offset, min(end, offset + 16384))
        m = re.match(rb"\s*(\d+)\s+(\d+)\s+obj", first)
        if not m:
            raise PdfRangeError(f"no object at {offset}")
        p = _Parser(first, m.end())
        try:
            value = p.value()
        except PdfRangeError:
            first = self.file.read(offset, end)
            p = _Parser(first, m.end())
            value = p.value()
        p.ws()
        if isinstance(value, dict) and first.startswith(b"stream", p.p):
            s = p.p + 6
            if first[s:s + 2] == b"\r\n":
                s += 2
            elif first[s:s + 1] in (b"\n", b"\r"):
                s += 1
            length = value.get("Length")
            if isinstance(length, Ref):
                length = self.obj(length.num)
            if not isinstance(length, int):
                raise PdfRangeError("stream without a length")
            data = self.file.read(offset + s, offset + s + length)
            return int(m.group(1)), Stream(value, data)
        return int(m.group(1)), value

    def prefetch(self, nums) -> None:
        spans = []
        for n in nums:
            e = self.xref.get(n)
            if e is not None and e.kind == 1 and n not in self._objs:
                spans.append(self._span(e.a))
        if spans:
            self.file.prefetch(spans)

    def obj(self, num: int):
        if num in self._objs:
            return self._objs[num]
        e = self.xref.get(num)
        if e is None or e.kind == 0:
            value = None
        elif e.kind == 1:
            start, end = self._span(e.a)
            _n, value = self._parse_at(start, end)
        else:
            value = self._from_object_stream(e.a, e.b, num)
        self._objs[num] = value
        return value

    def _from_object_stream(self, snum: int, index: int, num: int):
        stream = self.obj(snum)
        if not isinstance(stream, Stream):
            raise PdfRangeError("bad object stream")
        data = _decode(stream)
        n, first = int(stream.dict["N"]), int(stream.dict["First"])
        head = data[:first].split()
        pairs = [(int(head[i]), int(head[i + 1])) for i in range(0, 2 * n, 2)]
        for k, (onum, off) in enumerate(pairs):
            if onum == num:
                end = pairs[k + 1][1] if k + 1 < len(pairs) else len(data) - first
                return _Parser(data[first + off:first + end]).value()
        raise PdfRangeError(f"object {num} not in its stream")

    def _raw_object(self, num: int) -> bytes:
        """The object as it appears in the file, ready to copy."""
        e = self.xref.get(num)
        value = self.obj(num)
        if e is not None and e.kind == 1 and not isinstance(value, Stream):
            start, end = self._span(e.a)
            raw = self.file.read(start, end)
            m = re.match(rb"\s*\d+\s+\d+\s+obj", raw)
            k = raw.find(b"endobj")
            if m and k > 0:
                return b"%d 0 obj" % num + raw[m.end():k] + b"endobj\n"
        if isinstance(value, Stream):
            d = dict(value.dict)
            d["Length"] = len(value.data)
            return (b"%d 0 obj\n" % num + _serialize(d) + b"\nstream\n"
                    + value.data + b"\nendstream\nendobj\n")
        return b"%d 0 obj\n" % num + _serialize(value) + b"\nendobj\n"

    # -- pages -------------------------------------------------------------

    def page_count(self) -> int:
        root = self.obj(self.trailer["Root"].num)
        pages = self.obj(root["Pages"].num)
        return int(pages.get("Count") or 0)

    def _locate(self, wanted: list[int]) -> list[tuple[int, dict]]:
        """The object numbers of pages *wanted* (0-based), each with the
        attributes it inherits — reading the page tree's nodes, not every
        page: where a node's /Count equals its kids, the kids are pages."""
        root = self.obj(self.trailer["Root"].num)
        top = root.get("Pages")
        if not isinstance(top, Ref):
            raise PdfRangeError("no page tree")
        found: dict[int, tuple[int, dict]] = {}

        def walk(ref: Ref, inherited: dict, base: int, depth: int) -> None:
            if depth > 32:
                raise PdfRangeError("page tree too deep")
            node = self.obj(ref.num)
            if not isinstance(node, dict):
                raise PdfRangeError("bad page tree node")
            here = dict(inherited)
            for k in _INHERITED:
                if k in node:
                    here[k] = node[k]
            kids = node.get("Kids") or []
            if isinstance(kids, Ref):
                kids = self.obj(kids.num)
            kids = [k for k in kids if isinstance(k, Ref)]
            if int(node.get("Count") or 0) == len(kids):
                for w in wanted:
                    if base <= w < base + len(kids):
                        found[w] = (kids[w - base].num, here)
                return
            self.prefetch([k.num for k in kids])
            i = base
            for kid in kids:
                kn = self.obj(kid.num)
                if isinstance(kn, dict) and (kn.get("Type") == "Pages" or "Kids" in kn):
                    count = int(kn.get("Count") or 0)
                    if any(i <= w < i + count for w in wanted):
                        walk(kid, here, i, depth + 1)
                    i += count
                else:
                    if i in wanted:
                        found[i] = (kid.num, here)
                    i += 1

        walk(top, {}, 0, 0)
        missing = [w for w in wanted if w not in found]
        if missing:
            raise PdfRangeError(f"no page {missing[0]}")
        return [found[w] for w in wanted]

    def cut(self, wanted: list[int]) -> bytes:
        """A new PDF of pages *wanted* (0-based, in that order)."""
        pages = self._locate(wanted)
        # Read the pages, then everything they reach, a layer at a time, each
        # layer fetched together.
        self.prefetch([n for n, _ in pages])
        new_root, new_pages = max(self.xref) + 1, max(self.xref) + 2
        written: dict[int, bytes] = {}
        queue: list[int] = []
        page_nums = []
        for num, inherited in pages:
            d = self.obj(num)
            if not isinstance(d, dict):
                raise PdfRangeError(f"page {num} is not a dictionary")
            d = dict(d)
            for k, v in inherited.items():
                d.setdefault(k, v)
            for k in _DROP:
                d.pop(k, None)
            d["Parent"] = Ref(new_pages)
            written[num] = b"%d 0 obj\n" % num + _serialize(d) + b"\nendobj\n"
            page_nums.append(num)
            refs: set = set()
            _refs({k: v for k, v in d.items() if k != "Parent"}, refs)
            queue.extend(sorted(refs))
        seen = set(written) | {new_pages}
        while queue:
            layer = [n for n in dict.fromkeys(queue) if n not in seen]
            queue = []
            if not layer:
                break
            self.prefetch(layer)
            for n in layer:
                seen.add(n)
                v = self.obj(n)
                refs = set()
                _refs(v, refs)
                queue.extend(sorted(refs - seen))
                written[n] = self._raw_object(n)
        written[new_pages] = (b"%d 0 obj\n" % new_pages + _serialize({
            "Type": Name("Pages"),
            "Kids": [Ref(n) for n in page_nums],
            "Count": len(page_nums)}) + b"\nendobj\n")
        written[new_root] = (b"%d 0 obj\n" % new_root + _serialize({
            "Type": Name("Catalog"), "Pages": Ref(new_pages)}) + b"\nendobj\n")
        return _write_pdf(written, new_root)


def _write_pdf(objects: dict[int, bytes], root: int) -> bytes:
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += objects[num]
    size = max(objects) + 1
    xref_at = len(out)
    out += b"xref\n0 %d\n" % size
    out += b"0000000000 65535 f \n"
    for num in range(1, size):
        if num in offsets:
            out += b"%010d 00000 n \n" % offsets[num]
        else:
            out += b"0000000000 65535 f \n"
    out += b"trailer\n<</Size %d /Root %d 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        size, root, xref_at)
    return bytes(out)


# ---------------------------------------------------------------------------
# On the web
# ---------------------------------------------------------------------------

def open_url(url: str, session=None, timeout: int = 60) -> RemotePdf:
    """The PDF at *url*, read by range requests (``requests`` session)."""
    import requests

    s = session or requests.Session()
    r = s.get(url, headers={"Range": "bytes=0-0"}, timeout=timeout, stream=True)
    try:
        cr = r.headers.get("Content-Range", "")
        if r.status_code != 206 or "/" not in cr:
            raise PdfRangeError(f"no range support (HTTP {r.status_code})")
        size = int(cr.rsplit("/", 1)[-1])
        # Where a redirect led (the Internet Archive sends every download
        # to one of its servers): ask there directly from now on.
        target = r.url or url
    finally:
        r.close()

    def fetch(start: int, end: int) -> bytes:
        # A connection the server dropped mid-answer is asked again, once.
        for attempt in range(2):
            try:
                resp = s.get(target, headers={"Range": f"bytes={start}-{end - 1}"},
                             timeout=timeout)
            except requests.RequestException as exc:
                if attempt:
                    raise PdfRangeError(f"range request failed: {exc}") from None
                continue
            if resp.status_code != 206:
                raise PdfRangeError(f"range request answered {resp.status_code}")
            return resp.content
        raise PdfRangeError("range request failed")

    return RemotePdf(RemoteFile(size, fetch)).load()


def cut_pages(url: str, wanted: list[int], session=None) -> tuple[bytes, int]:
    """Pages *wanted* (0-based) of the PDF at *url*, as a new PDF — and the
    file's page count.  Raises :class:`PdfRangeError` when the file can't be
    read this way."""
    pdf = open_url(url, session=session)
    total = pdf.page_count()
    wanted = [w for w in wanted if 0 <= w < total]
    if not wanted:
        raise PdfRangeError("no such page")
    return pdf.cut(wanted), total
