"""Google Scholar's CAPTCHA answered in Chrome or Edge, with the GetCases
browser extension.

When Google challenges the app's own requests to Scholar, and the hidden
Firefox too (or there is no Firefox -- see :mod:`scholar_browser`), the
reader can answer the CAPTCHA in the browser they use every day, if the
GetCases extension is there.  The app opens the page it wants in that
browser, on the extension's profile, and waits.  Nothing drives the browser:
Google, like CloudFlare, is harder on one under automation, and the reader's
own browser often passes without a CAPTCHA at all.

Once the page loads past Google's check, the extension (``background.js``)
hands the app, through :mod:`browser_bridge`:

* Google's anti-abuse and preference cookies -- the CAPTCHA exemption among
  them, never the cookies that sign the reader in to Google -- with the
  headers the browser sent them under (``/clearance``,
  :func:`accept_clearance`).  The app's own requests go out with them from
  then on, as that browser (see ``google_scholar``); and
* the page itself, as the browser has it, for the fetch waiting on it
  (``/page``, :func:`deliver_page`).  A page loaded only so far as Scholar's
  own CAPTCHA is not taken: the fetch waits on.

The tab the app opened is closed once its page is handed over, as the
Firefox window is.

Tkinter-free: the app sets :data:`bridge` (its browser bridge, to know the
extension can reach it) and the :data:`on_answered` hook.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
from typing import NamedTuple, Optional

import eng_rep_pdf
import scholar_browser

#: The host whose pages the extension hands over.
SCHOLAR_HOST = "scholar.google.com"

#: The Google cookies the extension hands over (it reads no others): the
#: CAPTCHA exemption and the AEC anti-abuse token, the standing a returning
#: browser has (NID, __Secure-ENID), Scholar's preferences and the consent
#: cookies.  Never the ones that sign the reader in to Google.
COOKIES = ("GOOGLE_ABUSE_EXEMPTION", "AEC", "NID", "__Secure-ENID", "GSP",
           "SOCS", "CONSENT")

#: The headers the browser sent with them, which go with them.
HEADERS = eng_rep_pdf._BROWSER_HEADERS

#: The query parameters a page's address may gain or lose on its way past
#: Google's check without being another page.
_VOLATILE = frozenset(("hl", "gws_rd", "sei", "ei", "oi", "authuser"))

#: The app's browser bridge (browser_bridge.BrowserBridge), set while it is
#: listening for the extension; None otherwise.
bridge = None

#: Called (from a server thread) as a page the reader answered for is handed
#: over: the app brings its next window to the front, the reader being in
#: the browser.
on_answered = None


class Identity(NamedTuple):
    """The browser the extension handed Google's cookies over from."""

    cookies: dict           # Google's cookies (COOKIES only)
    user_agent: str
    headers: dict           # its client hints and languages
    browser: str            # "Chrome", "Edge"
    obtained: float         # Unix seconds


class _Wait:
    """A fetch waiting for the extension to hand a page over."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.done = threading.Event()
        self.html = ""
        self.final_url = ""


_lock = threading.Lock()
_identity: Optional[Identity] = None
_waiting: "dict[str, _Wait]" = {}
#: No browser is opened before this (monotonic): a page went unanswered.
_open_after = 0.0


def _tell(hook, *args) -> None:
    if hook is not None:
        try:
            hook(*args)
        except Exception:
            pass


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def handles(url: str) -> bool:
    """Whether what the extension hands over from *url* is Scholar's."""
    return _host(url) == SCHOLAR_HOST


def page_key(url: str) -> str:
    """*url* as the page it names: its path and its query, less what Google
    adds or drops on the way past its check, in order."""
    try:
        parts = urllib.parse.urlsplit(url or "")
    except ValueError:
        return ""
    query = sorted((k, v) for k, v in urllib.parse.parse_qsl(
        parts.query, keep_blank_values=True) if k not in _VOLATILE)
    return f"{(parts.hostname or '').lower()}{parts.path}?{urllib.parse.urlencode(query)}"


def available() -> bool:
    """Whether a page can be put to the reader in Chrome or Edge: the
    extension is installed there, and the app is listening for it."""
    return bool(getattr(bridge, "bound", False)
                and eng_rep_pdf.extension_browsers())


def identity() -> Optional[Identity]:
    """The browser identity the extension last handed over, if any."""
    with _lock:
        return _identity


def accept_clearance(data: dict) -> dict:
    """What the extension hands over as a Scholar page loads past Google's
    check (``/clearance``): its Google cookies and headers.  Replies whether
    they were taken, and whether the app is waiting for that page (the
    extension then hands it over too)."""
    global _identity
    url = str(data.get("url") or "")
    if not handles(url):
        return {"ok": False}
    cookies = {}
    for cookie in data.get("cookies") or ():
        if (isinstance(cookie, dict) and cookie.get("name") in COOKIES
                and isinstance(cookie.get("value"), str)):
            cookies[cookie["name"]] = cookie["value"]
    sent = data.get("headers") if isinstance(data.get("headers"), dict) else {}
    headers = {k.lower(): v for k, v in sent.items()
               if isinstance(k, str) and isinstance(v, str)
               and k.lower() in HEADERS}
    agent = headers.pop("user-agent", "")
    browser = str(data.get("browser") or "").strip() or "Chrome"
    taken = bool(cookies and agent)
    with _lock:
        if taken:
            _identity = Identity(cookies, agent, headers, browser, time.time())
        wanted = page_key(url) in _waiting
    return {"ok": taken, "page": wanted}


def deliver_page(data: dict) -> dict:
    """The page a fetch is waiting for, as the extension hands it over
    (``/page``).  Not a page that is still Google's CAPTCHA: the reader has
    not answered it yet, and the fetch waits on.  Replies whether it was
    taken, and that the tab the app opened for it may close."""
    url, html = str(data.get("url") or ""), data.get("html")
    if not handles(url) or not isinstance(html, str) or not html:
        return {"ok": False}
    with _lock:
        wait = _waiting.get(page_key(url))
    if wait is None or scholar_browser._challenged(url, html):
        return {"ok": False}
    wait.html, wait.final_url = html, url
    wait.done.set()
    _tell(on_answered)
    return {"ok": True, "close": True}


def fetch(url: str) -> "Optional[tuple[str, str]]":
    """Open *url* in Chrome or Edge, where the extension is, and wait (up
    to scholar_browser's five minutes) for the extension to hand the page
    over once it loads past Google's check -- the reader answering a
    CAPTCHA there, if Google asks for one.  Returns ``(html, final_url)``,
    or None when the page wasn't handed over; for ten minutes after that,
    None at once, so an unanswered challenge does not come back on every
    fetch."""
    global _open_after
    if time.monotonic() < _open_after or not available():
        return None
    browser = eng_rep_pdf.extension_browsers()[0]
    key = page_key(url)
    with _lock:
        wait = _waiting.get(key)
        mine = wait is None
        if mine:
            wait = _waiting[key] = _Wait(url)
    try:
        if mine:
            if not eng_rep_pdf.open_in_extension_browser(url, browser):
                return None
            print(f"[scholar] waiting for the page in {browser.name} "
                  "(a CAPTCHA there, if Google asks for one)")
            _tell(scholar_browser.on_step,
                  f"Google Scholar wants to check that a person is asking — "
                  f"the page has opened in {browser.name}: answer the CAPTCHA "
                  f"there if it shows one…")
        if wait.done.wait(scholar_browser._SOLVE_TIMEOUT):
            return wait.html, wait.final_url
    finally:
        if mine:
            with _lock:
                _waiting.pop(key, None)
    print(f"[scholar] the page in {browser.name} was not answered")
    _open_after = time.monotonic() + scholar_browser._SOLVE_SNOOZE
    return None
