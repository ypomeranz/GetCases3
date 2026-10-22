"""The browser the app opens for itself where Firefox isn't installed.

Two things in the app need a real browser.  Google Scholar, once it has
flagged this computer, answers only a genuine browser (see
:mod:`scholar_browser`); and CommonLII keeps the English Reports scans
behind a CloudFlare check that admits people, not scripts (see
:mod:`eng_rep_pdf`).  Both were done with Firefox.  Without it, this module
drives another browser the machine has — Google Chrome or Microsoft Edge on
Windows, macOS or Linux, Safari on a Mac — and that browser does the
fetching itself: its own network stack, its own cookies, on a profile kept
for the app alone, never the reader's own.

* **Out of sight.**  The window is minimized as soon as it opens and stays
  so while pages and scans are fetched; the app shows what they bring.
* **To the front only for a person.**  When a site wants a person — Google's
  CAPTCHA, CloudFlare's check — the window comes up with a note above the
  page saying so, the fetch waits for the reader, and the window goes back
  down once they have answered.  A check that passes by itself in a real
  browser (CloudFlare's often does) is given a few seconds first.
* **A file is fetched from its page.**  A scan is asked for from inside the
  page that links to it (``fetch()`` in that page), so the browser sends it
  as it would for a reader clicking the link — that page as its Referer, its
  own cookies — and the bytes come back to the app.
* **Nothing leaves it.**  Its cookies are never read out for another client
  to send: what a check admits is this browser, and only it asks.

Safari is driven by ``safaridriver``, with three limits: it has no hidden
mode (its window is minimized instead); its automation session is isolated
and forgets its cookies when it ends, so it is kept open while the app runs;
and a window under automation takes no input from a person — Safari offers
to stop the session instead — so a check that wants a person cannot be
passed there (:class:`NeedsPerson` says so, and the caller sends the reader
to the page in their own browser).  Its remote automation must be allowed
once: Safari ▸ Settings ▸ Advanced ▸ "Show features for web developers",
then Develop ▸ "Allow Remote Automation" (or ``safaridriver --enable``).

Everything here is optional: :func:`available` is False without Selenium or
a browser to drive, and the callers go on as they did without Firefox.
Selenium Manager fetches the browser's driver (chromedriver, msedgedriver)
the first time, as it does geckodriver for Firefox.
"""

from __future__ import annotations

import atexit
import base64
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

#: Where each browser keeps the app's own profile.
PROFILE_ROOT = Path.home() / ".cache" / "getcases_browser"

_PAGE_LOAD_TIMEOUT = 40
_CONTENT_TIMEOUT = 20   # seconds for a page's content to appear
_SETTLE_TIMEOUT = 8     # seconds a check is given to pass by itself
_PERSON_TIMEOUT = 300   # seconds the reader has to answer a check
_SCRIPT_TIMEOUT = 90    # seconds for a file fetched from its page

#: Put above a page a person is asked to answer.  It must not contain words
#: the callers' challenge tests look for, or the page would never read as
#: answered.
_NOTE_JS = """
var id = 'getcases-person-note';
if (document.body && !document.getElementById(id)) {
  var d = document.createElement('div');
  d.id = id;
  d.textContent = arguments[0];
  d.style.cssText = 'padding:10px 16px;background:#fff3cd;color:#222;'
    + 'font:15px/1.4 sans-serif;border-bottom:1px solid #d9c27a;';
  document.body.insertBefore(d, document.body.firstChild);
}
"""

#: A file fetched from inside the page that links to it, as base64.
_FETCH_JS = """
var url = arguments[0], done = arguments[arguments.length - 1];
fetch(url, {credentials: 'include'}).then(function (r) {
  return r.arrayBuffer().then(function (buf) {
    var bytes = new Uint8Array(buf), s = '';
    for (var i = 0; i < bytes.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    done({status: r.status, type: r.headers.get('content-type') || '',
          mitigated: r.headers.get('cf-mitigated') || '', body: btoa(s)});
  });
}).catch(function (e) { done({error: String(e)}); });
"""

#: How to turn Safari's remote automation on, for the message when it is off.
SAFARI_SETUP = ("Turn on Safari's remote automation once: Safari ▸ Settings ▸ "
                "Advanced ▸ “Show features for web developers”, then Develop "
                "▸ “Allow Remote Automation”.")


class Unavailable(Exception):
    """No browser can be driven here (Selenium or a browser missing, or the
    one there would not start) — the caller goes on without one."""


class NeedsPerson(Exception):
    """A site wants a person, and none answered: the window went unanswered
    for :data:`_PERSON_TIMEOUT`, or was closed, or the browser cannot take a
    person's answer at all (``answerable`` False: Safari)."""

    def __init__(self, browser: str, answerable: bool = True):
        super().__init__(f"{browser}: a check wants a person")
        self.browser = browser
        self.answerable = answerable


@dataclass(frozen=True)
class Engine:
    """A browser this machine has: ``key`` "chrome", "edge" or "safari",
    the name it goes by, and the program (none for Safari, which
    ``safaridriver`` starts)."""

    key: str
    name: str
    binary: str = ""

    @property
    def answerable(self) -> bool:
        """Whether a person can answer a check in the window while it is
        driven — not in Safari's."""
        return self.key != "safari"


def _first_file(paths) -> str:
    for path in paths:
        if path and os.path.isfile(path):
            return str(path)
    return ""


def _windows_paths(*tail: str) -> list:
    roots = [os.environ.get(v) for v in
             ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    return [os.path.join(root, *tail) for root in roots if root]


def _mac_paths(app: str, exe: str) -> list:
    return [f"{base}/{app}.app/Contents/MacOS/{exe}"
            for base in ("/Applications",
                         os.path.expanduser("~/Applications"))]


def engines() -> "list[Engine]":
    """The browsers this machine has that can be driven, in the order they
    are tried: Chrome, Edge, then — on a Mac — Safari, whose window cannot
    take a person's answer to a check."""
    found: list[Engine] = []
    if sys.platform == "win32":
        chrome = _first_file(_windows_paths("Google", "Chrome", "Application",
                                            "chrome.exe"))
        edge = _first_file(_windows_paths("Microsoft", "Edge", "Application",
                                          "msedge.exe"))
    elif sys.platform == "darwin":
        chrome = _first_file(_mac_paths("Google Chrome", "Google Chrome"))
        edge = _first_file(_mac_paths("Microsoft Edge", "Microsoft Edge"))
    else:
        chrome = next((p for p in map(shutil.which, (
            "google-chrome", "google-chrome-stable", "chromium",
            "chromium-browser")) if p), "")
        edge = shutil.which("microsoft-edge") or ""
    if chrome:
        found.append(Engine("chrome", "Google Chrome", chrome))
    if edge:
        found.append(Engine("edge", "Microsoft Edge", edge))
    if (sys.platform == "darwin" and os.path.isdir("/Applications/Safari.app")
            and os.path.isfile("/usr/bin/safaridriver")):
        found.append(Engine("safari", "Safari"))
    return found


def _have_selenium() -> bool:
    import importlib.util
    return importlib.util.find_spec("selenium") is not None


def available() -> bool:
    """Whether a browser can be driven here: Selenium, and a browser."""
    return _have_selenium() and bool(engines())


def _tell(hook, *args) -> None:
    if hook is not None:
        try:
            hook(*args)
        except Exception:
            pass


def _complete(driver) -> bool:
    try:
        return driver.execute_script("return document.readyState") == "complete"
    except Exception:
        return False


class AppBrowser:
    """One browser the app drives, started when first asked for and kept
    for the app's life (minimized).  Its fetches take turns."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._driver = None
        self._lock = threading.RLock()

    @property
    def name(self) -> str:
        return self.engine.name

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        """The driven browser, started minimized if it is not running.
        Raises :class:`Unavailable` when it will not start."""
        with self._lock:
            if self._driver is None:
                self._driver = self._launch()
                self._driver.set_page_load_timeout(_PAGE_LOAD_TIMEOUT)
                self._driver.set_script_timeout(_SCRIPT_TIMEOUT)
                self._hide()
            return self._driver

    def _launch(self):
        try:
            from selenium import webdriver
        except ImportError as exc:  # pragma: no cover - checked by available()
            raise Unavailable(f"selenium missing: {exc}")
        key = self.engine.key
        try:
            if key == "safari":
                return webdriver.Safari()
            if key == "edge":
                from selenium.webdriver.edge.options import Options
            else:
                from selenium.webdriver.chrome.options import Options
            opts = Options()
            opts.binary_location = self.engine.binary
            profile = PROFILE_ROOT / key
            profile.mkdir(parents=True, exist_ok=True)
            # A profile of the app's own, kept between runs, so a check passed
            # once stays passed — never the reader's own profile.
            opts.add_argument(f"--user-data-dir={profile}")
            opts.add_argument("--no-first-run")
            opts.add_argument("--no-default-browser-check")
            return (webdriver.Edge(options=opts) if key == "edge"
                    else webdriver.Chrome(options=opts))
        except Exception as exc:
            text = str(exc)
            if key == "safari" and "Remote Automation" in text:
                raise Unavailable(f"Safari's remote automation is off. "
                                  f"{SAFARI_SETUP}")
            raise Unavailable(f"could not start {self.name}: "
                              f"{text.splitlines()[0] if text else exc!r}")

    def close(self) -> None:
        with self._lock:
            driver, self._driver = self._driver, None
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    def _hide(self) -> None:
        try:
            self._driver.minimize_window()
        except Exception:
            pass

    def _show(self) -> None:
        driver = self._driver
        try:
            driver.set_window_rect(width=1100, height=860)
        except Exception:
            try:
                driver.maximize_window()
            except Exception:
                pass
        try:
            driver.switch_to.window(driver.current_window_handle)
            driver.execute_script("window.focus();")
        except Exception:
            pass

    # -- pages ---------------------------------------------------------------

    def _go(self, url: str) -> None:
        from selenium.common.exceptions import (
            TimeoutException, WebDriverException,
        )
        try:
            self._driver.get(url)
        except TimeoutException:
            pass    # what arrived is looked at
        except WebDriverException:
            # The browser went away (closed by the reader, crashed): once more
            # in a new one.
            self.close()
            self.start()
            try:
                self._driver.get(url)
            except TimeoutException:
                pass

    @staticmethod
    def _wait(driver, until: Callable, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            try:
                if until(driver):
                    return True
            except Exception:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    def _state(self) -> "tuple[str, str]":
        driver = self._driver
        return driver.current_url or "", driver.page_source or ""

    def open(self, url: str, *, challenged: Callable[[str, str], bool],
             ready: Optional[Callable] = None, ask: bool = True,
             note: str = "", waiting: str = "",
             on_step=None) -> "tuple[str, str]":
        """Load *url* and return ``(page source, final url)`` once it is
        there — *ready(driver)* says when (by default, the document has
        loaded) — or once it is plainly a check (*challenged(url, source)*).

        A check is given :data:`_SETTLE_TIMEOUT` to pass by itself.  Then,
        when *ask* and the browser can take a person's answer, the window
        comes to the front with *note* above the page, *waiting* goes to
        *on_step*, and this waits for the reader; the window goes back down
        either way.  Raises :class:`NeedsPerson` when the check stands, and
        :class:`Unavailable` when the browser will not start."""
        ready = ready or _complete
        with self._lock:
            driver = self.start()
            self._go(url)

            def settled(d) -> bool:
                return challenged(*self._state()) or ready(d)

            self._wait(driver, settled, _CONTENT_TIMEOUT)
            if not challenged(*self._state()):
                return self._page(url)
            if self._wait(self._driver,
                          lambda d: not challenged(*self._state()),
                          _SETTLE_TIMEOUT):
                self._wait(self._driver, ready, _CONTENT_TIMEOUT)
                return self._page(url)
            if not (ask and self.engine.answerable):
                raise NeedsPerson(self.name, self.engine.answerable)
            return self._ask_person(url, challenged, ready, note, waiting,
                                    on_step)

    def _page(self, url: str) -> "tuple[str, str]":
        final_url, source = self._state()
        return source, final_url or url

    def _ask_person(self, url, challenged, ready, note, waiting,
                    on_step) -> "tuple[str, str]":
        from selenium.common.exceptions import WebDriverException

        print(f"[app-browser] {self.name}: a check wants a person — "
              "showing the window")
        _tell(on_step, waiting or f"Answer the check in the {self.name} "
                                  "window that has opened…")
        self._show()
        deadline = time.monotonic() + _PERSON_TIMEOUT
        try:
            while time.monotonic() < deadline:
                final_url, source = self._state()
                if not challenged(final_url, source):
                    if _path(final_url) != _path(url):
                        # Answered, but left elsewhere: on to the page asked
                        # for, now that the site lets the browser in.
                        self._go(url)
                    self._wait(self._driver, ready, _CONTENT_TIMEOUT)
                    if not challenged(*self._state()):
                        print(f"[app-browser] {self.name}: answered")
                        return self._page(url)
                if note:
                    self._driver.execute_script(_NOTE_JS, note)
                time.sleep(1.0)
        except WebDriverException:
            self.close()    # the reader closed the window
        finally:
            if self._driver is not None:
                self._hide()
        print(f"[app-browser] {self.name}: the check went unanswered")
        raise NeedsPerson(self.name)

    # -- files ---------------------------------------------------------------

    def fetch_file(self, page_url: str, file_url: str, *,
                   challenged: Callable[[str, str], bool], ask: bool = True,
                   note: str = "", waiting: str = "",
                   on_step=None) -> "tuple[int, bytes, str]":
        """The file at *file_url*, asked for from inside *page_url* — the
        page that links to it, loaded first (and its check answered, see
        :meth:`open`) — as ``(status, body, cf-mitigated header)``."""
        with self._lock:
            self.open(page_url, challenged=challenged, ask=ask, note=note,
                      waiting=waiting, on_step=on_step)
            try:
                got = self._driver.execute_async_script(_FETCH_JS, file_url)
            except Exception as exc:
                raise Unavailable(f"{self.name} could not fetch "
                                  f"{file_url}: {exc}")
        if not isinstance(got, dict) or got.get("error"):
            raise Unavailable(f"{self.name} could not fetch {file_url}: "
                              f"{(got or {}).get('error', got)!r}")
        return (int(got.get("status") or 0),
                base64.b64decode(got.get("body") or ""),
                str(got.get("mitigated") or ""))


def _path(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url or "").path


_SHARED: Optional[AppBrowser] = None
_SHARED_LOCK = threading.Lock()
#: Why no browser would start, and when that was found: not tried again
#: for :data:`_RETRY_AFTER`, so every scan does not wait on the same failure.
_FAILED: "tuple[float, str] | None" = None
_RETRY_AFTER = 600.0


def shared() -> AppBrowser:
    """The one browser the app drives, started now if need be: the first of
    :func:`engines` that starts.  Raises :class:`Unavailable` (with what
    went wrong with each) when none does."""
    global _SHARED, _FAILED
    with _SHARED_LOCK:
        if _SHARED is not None:
            return _SHARED
        if not _have_selenium():
            raise Unavailable("selenium is not installed "
                              "(pip install selenium)")
        if _FAILED is not None and time.monotonic() - _FAILED[0] < _RETRY_AFTER:
            raise Unavailable(_FAILED[1])
        problems = []
        for engine in engines():
            browser = AppBrowser(engine)
            try:
                browser.start()
            except Unavailable as exc:
                problems.append(str(exc))
                continue
            print(f"[app-browser] driving {engine.name}")
            _SHARED, _FAILED = browser, None
            atexit.register(browser.close)
            return browser
        _FAILED = (time.monotonic(), "; ".join(problems)
                   or "no Chrome, Edge or Safari to drive")
        raise Unavailable(_FAILED[1])


def started() -> Optional[AppBrowser]:
    """The browser the app drives, if it has been started."""
    return _SHARED


if __name__ == "__main__":  # pragma: no cover - live smoke test
    print("selenium:", _have_selenium())
    print("engines :", [f"{e.name} ({e.binary or 'safaridriver'})"
                        for e in engines()])
    if not available():
        sys.exit(0)
    browser = shared()
    try:
        source, final = browser.open(
            sys.argv[1] if len(sys.argv) > 1 else "https://example.com/",
            challenged=lambda _u, _s: False)
        print(f"{browser.name}: {len(source):,} chars from {final}")
    finally:
        browser.close()
