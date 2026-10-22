"""The door the GetCases browser extension knocks on.

The extension (``browser_extension/``) links the citations on the pages a user
reads in Chrome.  While GetCases is running, a click on one opens it here, in
the app, rather than on the web.  This module is the app's end of that
arrangement: a small HTTP server on this machine's loopback address that

* ``GET /status`` answers that GetCases is here (the extension asks before it
  decides where a click goes);
* ``POST /detect`` reads a page's text with the app's own citation detector
  (:func:`citations.detect_links`) and returns the spans to link, each with
  its action and the web page it opens when the app is not running (see
  :mod:`browser_links`); and
* ``POST /open`` hands a clicked citation to the app, which opens it the way a
  link in one of its own windows opens;
* ``POST /clearance`` hands over the browser's cookies for a site behind a
  check for people, and the headers it sent them with, once a page of it
  has loaded past the check there -- which the app, unable to read
  Chrome's or Edge's cookie store, fetches with: CommonLII's CloudFlare
  clearance for the English Reports' scans (see
  :func:`eng_rep_pdf.accept_browser_clearance`), Google's CAPTCHA
  exemption for Scholar (see :mod:`scholar_extension`); and
* ``POST /page`` hands over a page the app opened in the browser to have a
  CAPTCHA answered, once it has been (see :mod:`scholar_extension`).

Only the extension may use it.  Web pages can reach a loopback server too, so
every request must carry the ``X-GetCases`` header (a page cannot add one
without a CORS preflight, which is refused), must not come from a web origin,
and must name this machine as its host (which defeats DNS rebinding).  No
response carries CORS headers, so a page cannot read one either.

Tkinter-free and started only from the app's ``main()``: the tests build the
app directly and never open a port.
"""

from __future__ import annotations

import bisect
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

#: The loopback port the extension looks for GetCases on.  "§ 1983": below the
#: ephemeral ranges every OS hands out, and no well-known service uses it.
DEFAULT_PORT = 21983
#: The header only the extension sends.
CLIENT_HEADER = "X-GetCases"
#: Bumped when a request or response changes shape.
API_VERSION = 1
#: The most text one /detect request may carry, in characters.
MAX_TEXT = 3_000_000
_MAX_BODY = 4 * MAX_TEXT + 65_536       # UTF-8 at its widest, plus JSON
#: The most of a refused request's body read (and thrown away) before the
#: refusal is sent; see the handler's _discard_body.
_DISCARD_MAX = 1_048_576
#: The most cookies, and headers, one /clearance may carry, and the longest
#: any one may be: a site's cookies are few, and a cookie is at most 4 KB.
_MAX_COOKIES = 50
_MAX_COOKIE = 8192
_MAX_HEADERS = 20
_MAX_HEADER = 2048
#: The longest page /page may carry, in characters: a long opinion on
#: Scholar runs to a couple of million.
MAX_PAGE = 2 * MAX_TEXT

#: The origins a browser gives an extension's own requests.
_EXTENSION_SCHEMES = ("chrome-extension://", "moz-extension://",
                      "safari-web-extension://", "extension://")


# ---------------------------------------------------------------------------
# Offsets: JavaScript counts a string in UTF-16 code units, Python in code
# points.  They differ only past the Basic Multilingual Plane (emoji, some
# mathematical letters), so the conversion is skipped when there is none.
# ---------------------------------------------------------------------------

def utf16_offsets(text: str) -> "Optional[list[int]]":
    """``offsets[i]`` is where code point *i* of *text* starts in UTF-16, for
    ``0 <= i <= len(text)``; None when the two agree throughout."""
    if all(ord(ch) <= 0xFFFF for ch in text):
        return None
    out = [0]
    n = 0
    for ch in text:
        n += 2 if ord(ch) > 0xFFFF else 1
        out.append(n)
    return out


def _to_utf16(offsets: "Optional[list[int]]", i: int) -> int:
    return i if offsets is None else offsets[i]


def _from_utf16(offsets: "Optional[list[int]]", u: int) -> int:
    if offsets is None:
        return u
    return bisect.bisect_left(offsets, u)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _category(kind: str) -> str:
    """What a link points at, for the extension to colour it by: the same
    three groups the app's brief reader tints."""
    if kind in ("cite", "url", "engrep", "recap", "fedcas", "scotus", "sec"):
        return "case"
    if kind == "const":
        return "const"
    return "statute"


#: The module that names each kind of statute action (its ``spec_label``).
_STATUTE_MODULES = {"usc": "us_code", "cfr": "ecfr", "rule": "fed_rules",
                    "const": "constitution", "statestat": "state_statutes"}


def action_label(action: "tuple[str, str]", text: str = "") -> str:
    """A short name for what *action* opens, for the link's tooltip."""
    kind, value = action
    try:
        if kind == "cite":
            import citations
            cite, _, pin = value.partition("@")
            return f"{cite}, {citations.pin_display(pin)}" if pin else cite
        if kind in _STATUTE_MODULES:
            import importlib
            return importlib.import_module(
                _STATUTE_MODULES[kind]).spec_label(value)
        if kind == "sec":
            import sec_decisions
            return sec_decisions.spec_label(value)
        if kind == "engrep":
            # The case in the English Reports — what a nominate citation
            # ("2 Russ. & M. 639") opens, which its own text does not say.
            import eng_rep
            cases = eng_rep.resolve(eng_rep.split_pin(value)[0])
            if len(cases) == 1:
                return cases[0].label
            reprinted = {c.er_cite for c in cases}
            if len(reprinted) == 1:
                return reprinted.pop()
            if cases:
                return f"{' '.join((text or '').split())} (English Reports)"
        if kind == "leghist":
            import legislative_history
            return legislative_history.spec_label(value)
    except Exception:
        pass
    return " ".join((text or "").split())


def detect(text: str, italic_ranges=None) -> "list[dict]":
    """The citations in *text* the extension should link, as dicts with
    ``start``/``end`` (UTF-16 offsets into *text*), ``kind``/``value`` (the
    action ``/open`` takes back), ``url`` (the web page for it), ``label``
    and ``category``.

    *italic_ranges* are ``[start, end]`` UTF-16 ranges of *text* set in
    italics, when the page says; with them a case name is read only where
    the type shows one (see :func:`citations.detect_links`)."""
    import browser_links
    import citations

    offsets = utf16_offsets(text)
    italic = None
    if italic_ranges:
        italic = [False] * len(text)
        for pair in italic_ranges:
            try:
                s = max(0, _from_utf16(offsets, int(pair[0])))
                e = min(len(text), _from_utf16(offsets, int(pair[1])))
            except (TypeError, ValueError, IndexError):
                continue
            for i in range(s, e):
                italic[i] = True
    out = []
    for start, end, action in citations.detect_links(text, italic=italic):
        snippet = text[start:end]
        try:
            url = browser_links.browser_url(action, snippet)
        except Exception:
            url = ""
        out.append({
            "start": _to_utf16(offsets, start),
            "end": _to_utf16(offsets, end),
            "kind": action[0],
            "value": action[1],
            "url": url,
            "label": action_label(action, snippet),
            "category": _category(action[0]),
        })
    return out


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second socket bind a port already in
    # use, so two GetCases would both "own" it.  Elsewhere it only lets a
    # port be taken again straight after its owner quit.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET,
                                   socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class BrowserBridge:
    """The app's end of the extension's link: :meth:`start` opens the port
    (in the background, waiting for it while another GetCases still holds
    it), :meth:`close` gives it up.

    *on_open* receives each ``/open`` request's JSON object — ``{"kind",
    "value", "text"}`` for a link the app detected, ``{"text"}`` for one the
    extension found itself — on a server thread, so it must not touch Tk.

    *on_clearance* receives each ``/clearance`` — ``{"url", "browser",
    "cookies": [{"name", "value", "expires"}], "headers": {name: value}}``,
    in those shapes — on a server thread too, and says whether it took it:
    True or False, or the reply itself, a dict with ``"ok"`` in it.
    *on_page* receives each ``/page`` — ``{"url", "html"}`` — likewise.
    Without them, those paths are not found.
    """

    def __init__(self, on_open: Callable[[dict], None], *,
                 port: int = DEFAULT_PORT, version: str = "",
                 detector: Callable = detect,
                 on_clearance: Optional[Callable[[dict], object]] = None,
                 on_page: Optional[Callable[[dict], object]] = None) -> None:
        self.port = int(port)
        self.version = version
        self._on_open = on_open
        self._on_clearance = on_clearance
        self._on_page = on_page
        self._detector = detector
        # One page's detection at a time: a burst of tabs queues rather than
        # taking every core from the app.
        self._detect_lock = threading.Lock()
        self._server: Optional[_Server] = None
        self._closed = threading.Event()
        self._bound = threading.Event()

    @property
    def bound(self) -> bool:
        """Whether the port is this app's now."""
        return self._bound.is_set() and not self._closed.is_set()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True,
                         name="getcases-browser-bridge").start()

    def wait_bound(self, timeout: float) -> bool:
        return self._bound.wait(timeout)

    def close(self) -> None:
        self._closed.set()
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass

    # ------------------------------------------------------------------

    def _run(self) -> None:
        delay = 0.25
        told = False
        while not self._closed.is_set():
            try:
                server = _Server(("127.0.0.1", self.port), self._handler())
            except OSError as exc:
                # Another GetCases still has it (it lets go once it has seen
                # this one start), or something else does.
                if not told:
                    print(f"[browser] port {self.port} is in use ({exc}); "
                          "the browser extension will reach this GetCases "
                          "once it is free.")
                    told = True
                self._closed.wait(delay)
                delay = min(delay * 2, 5.0)
                continue
            if self._closed.is_set():
                server.server_close()
                return
            self.port = server.server_address[1]    # port 0: the one given
            self._server = server
            self._bound.set()
            if told:
                print(f"[browser] the browser extension reaches this "
                      f"GetCases on port {self.port} now.")
            try:
                server.serve_forever(poll_interval=0.5)
            finally:
                self._bound.clear()
            return

    def _handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "GetCases"
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                pass                                # no console noise

            # -- answering ------------------------------------------------

            def _reply(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type",
                                 "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _allowed(self) -> bool:
                """Whether the request comes from the extension: see the
                module's docstring."""
                host = (self.headers.get("Host") or "").strip().lower()
                if host not in (f"127.0.0.1:{bridge.port}",
                                f"localhost:{bridge.port}"):
                    return False
                if self.headers.get(CLIENT_HEADER) != "1":
                    return False
                origin = (self.headers.get("Origin") or "").strip().lower()
                if origin and not origin.startswith(_EXTENSION_SCHEMES):
                    return False
                return True

            def _discard_body(self) -> None:
                """Read a request's body, unwanted, before refusing it.

                A reply sent over a body still unread is often lost: the
                connection closes after the reply, and on Windows closing a
                socket with data waiting in it resets the connection, so the
                client sees "connection aborted" rather than the refusal
                (and a client keeping the connection open would have the
                body read as its next request).  A body past _DISCARD_MAX is
                left: no client of ours sends one to be refused."""
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return
                if 0 < length <= _DISCARD_MAX:
                    try:
                        self.rfile.read(length)
                    except OSError:
                        pass

            def _body(self) -> "Optional[dict]":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return None
                if length <= 0 or length > _MAX_BODY:
                    return None
                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return None
                return data if isinstance(data, dict) else None

            # -- methods --------------------------------------------------

            def do_OPTIONS(self) -> None:
                # A web page's CORS preflight: refused, with no CORS
                # headers, so the page's request is never sent.
                self._reply(403, {"error": "forbidden"})

            def do_GET(self) -> None:
                if not self._allowed():
                    self._reply(403, {"error": "forbidden"})
                    return
                if self.path.split("?")[0] == "/status":
                    self._reply(200, {"app": "GetCases", "api": API_VERSION,
                                      "version": bridge.version})
                    return
                self._reply(404, {"error": "not found"})

            def do_POST(self) -> None:
                if not self._allowed():
                    self._discard_body()
                    self._reply(403, {"error": "forbidden"})
                    return
                path = self.path.split("?")[0]
                known = ["/detect", "/open"]
                if bridge._on_clearance is not None:
                    known.append("/clearance")
                if bridge._on_page is not None:
                    known.append("/page")
                if path not in known:
                    self._discard_body()
                    self._reply(404, {"error": "not found"})
                    return
                data = self._body()
                if data is None:
                    self._reply(400, {"error": "expected a JSON object"})
                    return
                if path == "/detect":
                    self._detect(data)
                elif path == "/open":
                    self._open(data)
                elif path == "/clearance":
                    self._clearance(data)
                else:
                    self._page(data)

            def _detect(self, data: dict) -> None:
                text = data.get("text")
                if not isinstance(text, str):
                    self._reply(400, {"error": "no text"})
                    return
                if len(text) > MAX_TEXT:
                    self._reply(413, {"error": "too much text"})
                    return
                italic = data.get("italic")
                if not isinstance(italic, list):
                    italic = None
                started = time.monotonic()
                try:
                    with bridge._detect_lock:
                        links = bridge._detector(text, italic)
                except Exception as exc:
                    print(f"[browser] reading a page's citations failed: "
                          f"{exc}")
                    self._reply(500, {"error": "detection failed"})
                    return
                self._reply(200, {
                    "links": links,
                    "seconds": round(time.monotonic() - started, 3),
                })

            def _open(self, data: dict) -> None:
                request = {}
                for key in ("kind", "value", "text", "url"):
                    value = data.get(key)
                    if isinstance(value, str) and len(value) <= 20_000:
                        request[key] = value
                if not (request.get("text")
                        or (request.get("kind") and request.get("value"))):
                    self._reply(400, {"error": "nothing to open"})
                    return
                try:
                    bridge._on_open(request)
                except Exception as exc:
                    print(f"[browser] opening a citation failed: {exc}")
                    self._reply(500, {"error": "could not open"})
                    return
                self._reply(200, {"ok": True})

            def _clearance(self, data: dict) -> None:
                def text(value, limit: int) -> bool:
                    return isinstance(value, str) and len(value) <= limit

                url, browser = data.get("url"), data.get("browser", "")
                cookies, headers = data.get("cookies"), data.get("headers")
                # No cookies is a clearance too: Scholar's page, opened in a
                # browser that holds none of the Google cookies asked for.
                if not (text(url, 2048) and url and text(browser, 100)
                        and isinstance(cookies, list)
                        and len(cookies) <= _MAX_COOKIES
                        and isinstance(headers, dict)
                        and len(headers) <= _MAX_HEADERS):
                    self._reply(400, {"error": "not a clearance"})
                    return
                jar = []
                for cookie in cookies:
                    if not (isinstance(cookie, dict)
                            and text(cookie.get("name"), 256)
                            and text(cookie.get("value"), _MAX_COOKIE)):
                        self._reply(400, {"error": "not a cookie"})
                        return
                    expires = cookie.get("expires")
                    if not isinstance(expires, (int, float)) \
                            or isinstance(expires, bool):
                        expires = 0
                    jar.append({"name": cookie["name"],
                                "value": cookie["value"],
                                "expires": expires})
                sent = {}
                for name, value in headers.items():
                    if not (text(name, 100) and text(value, _MAX_HEADER)):
                        self._reply(400, {"error": "not a header"})
                        return
                    sent[name] = value
                self._hand_on(bridge._on_clearance, {
                    "url": url, "browser": browser, "cookies": jar,
                    "headers": sent}, "the browser's clearance")

            def _page(self, data: dict) -> None:
                url, html = data.get("url"), data.get("html")
                if not (isinstance(url, str) and 0 < len(url) <= 2048
                        and isinstance(html, str) and html):
                    self._reply(400, {"error": "not a page"})
                    return
                if len(html) > MAX_PAGE:
                    self._reply(413, {"error": "too much page"})
                    return
                self._hand_on(bridge._on_page, {"url": url, "html": html},
                              "a page from the browser")

            def _hand_on(self, take, request: dict, what: str) -> None:
                """Give *request* to *take* and answer with what it says:
                whether it took it, or a reply of its own."""
                try:
                    said = take(request)
                except Exception as exc:
                    print(f"[browser] taking {what} failed: {exc}")
                    self._reply(500, {"error": "could not take it"})
                    return
                if isinstance(said, dict):
                    self._reply(200, {**said, "ok": bool(said.get("ok"))})
                else:
                    self._reply(200, {"ok": bool(said)})

        return Handler
