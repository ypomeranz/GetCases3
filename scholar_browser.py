"""Fetch Google Scholar pages through the user's real Firefox (Selenium).

Google Scholar's search endpoint validates the TLS/HTTP2 handshake, not just
cookies or the User-Agent: a real browser passes even from an IP that Scholar
has flagged, while every curl_cffi impersonation profile gets the "/sorry/
unusual traffic" challenge (verified 2026-07-03 — same IP, same google.com
cookies, real Firefox passed a fresh search and all curl_cffi fingerprints
429'd).  So when the impersonation layer in :mod:`google_scholar` is blocked,
it falls back here and drives the actual Firefox, whose genuine Gecko network
stack Scholar trusts.

Design:
  * A **dedicated** headless Firefox profile (under the app cache) — never the
    user's live profile, which Firefox locks while it is open.  Headless uses
    the same NSS/TLS stack as a headed Firefox, so the fingerprint Scholar
    sees is identical; only rendering is skipped.
  * Selenium 4's built-in driver manager downloads geckodriver on demand, so
    there is no manual setup beyond ``pip install selenium`` and Firefox.
  * The driver is started lazily and kept alive for reuse (start-up is slow);
    :func:`ScholarBrowser.close` / atexit tear it down.
  * When Google challenges even this Firefox, the page is opened again in a
    Firefox window the reader can see, on the same profile, and the fetch
    waits for them to answer the CAPTCHA there (see
    :meth:`ScholarBrowser._solve`).  What answering earns is carried back into
    the headless session.

Where Firefox isn't installed, the browser the app drives for itself
(Chrome or Edge, or Safari on a Mac — see :mod:`app_browser`) fetches the
page instead, minimized, and comes up for the reader only should Google set
a CAPTCHA there too (Safari's window takes no answer while it is driven, so
there an unanswerable challenge is :class:`BrowserBlocked`).

Everything here is optional: :func:`available` is False when Selenium is
missing, or Firefox and every other browser to drive, and the caller
degrades to the local opinion DB / CourtListener exactly as before.
"""

from __future__ import annotations

import atexit
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

_PROFILE_DIR = Path.home() / ".cache" / "courtlistener_scholar_ff"
_PAGE_LOAD_TIMEOUT = 40
_CONTENT_TIMEOUT = 20  # seconds to wait for results / opinion to appear
_SOLVE_TIMEOUT = 300   # seconds the reader has to answer a CAPTCHA
_SOLVE_SNOOZE = 600    # seconds no CAPTCHA window opens after one went unanswered

# Substrings that mark Google's bot challenge, checked against the URL + source.
_BLOCK_MARKERS = (
    "/sorry/", "unusual traffic", "not a robot",
    "id=\"recaptcha\"", "g-recaptcha",
)


# Said above Google's challenge in the window the reader answers it in.  It must
# not contain any of _BLOCK_MARKERS, or the page would never read as answered.
_SOLVE_NOTE = (
    "GetCases: Google Scholar wants to check that a person is asking. Answer "
    "the CAPTCHA below and the page you asked for opens in GetCases; this "
    "window then gets out of the way by itself."
)

_NOTE_JS = """
if (document.body && !document.getElementById('getcases-solve-note')) {
  var d = document.createElement('div');
  d.id = 'getcases-solve-note';
  d.textContent = arguments[0];
  d.style.cssText = 'padding:10px 16px;background:#fff3cd;color:#222;'
    + 'font:15px/1.4 sans-serif;border-bottom:1px solid #d9c27a;';
  document.body.insertBefore(d, document.body.firstChild);
}
"""

#: Told what a fetch is waiting on, when set: ``on_step(text)`` as a CAPTCHA
#: window opens for the reader (the app shows it in its loading window).
on_step = None


def _tell(hook, *args) -> None:
    if hook is not None:
        try:
            hook(*args)
        except Exception:
            pass


def _challenged(url: str, source: str) -> bool:
    """Whether the page at *url* is Google's bot challenge."""
    low = (source or "").lower()
    return ("/sorry/" in (url or "").lower()
            or any(m in low for m in _BLOCK_MARKERS))


class BrowserUnavailable(Exception):
    """Selenium or Firefox isn't usable here — caller links out / falls back."""


class BrowserBlocked(Exception):
    """Even the real browser hit Scholar's challenge, and it was not answered
    (the reader closed the CAPTCHA window or let it time out, or no window
    was asked for)."""


def _firefox_binary() -> "Optional[str]":
    try:
        import eng_rep_pdf
        return eng_rep_pdf._firefox_exe()
    except Exception:
        return None


def _google_cookie_rows() -> list:
    """Full google.com cookie rows from the user's Firefox profile(s), as
    Selenium ``add_cookie`` dicts.  These carry the account/reputation state
    (chiefly ``NID``) that — together with a real Gecko fingerprint — is what
    lets a search pass; a fresh profile has the fingerprint but not the
    standing, so it is challenged.  Host-only ``__Host-*`` cookies are skipped
    (they can't be re-scoped)."""
    import os
    import shutil
    import sqlite3
    import tempfile
    import time
    try:
        import eng_rep_pdf
        dbs = eng_rep_pdf._firefox_cookie_dbs()
    except Exception:
        return []
    seen: set = set()
    rows: list = []
    for db in dbs:
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".sqlite")
            os.close(fd)
            shutil.copy2(db, tmp)
            con = sqlite3.connect(tmp)
            now = time.time()
            for name, value, host, path, is_secure, expiry in con.execute(
                "SELECT name, value, host, path, isSecure, expiry "
                "FROM moz_cookies WHERE host LIKE '%google.com'"
            ):
                if name.startswith("__Host-") or name in seen:
                    continue
                # Firefox writes ``expiry`` in milliseconds now; a dead cookie
                # replayed into the browser is worse than none, since it is the
                # standing behind these that keeps a search from being
                # challenged (see eng_rep_pdf._cookie_expiry_seconds).
                exp = eng_rep_pdf._cookie_expiry_seconds(expiry)
                if exp and exp <= now:
                    continue
                seen.add(name)
                rows.append({
                    "name": name, "value": value,
                    "domain": host if host.startswith(".") else "." + host,
                    "path": path or "/",
                    "secure": bool(is_secure) or name.startswith("__Secure-"),
                })
            con.close()
        except Exception:
            continue
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    return rows


def available() -> bool:
    """True when a Scholar fetch through a real browser is possible here:
    Firefox, or failing it a browser the app drives (see app_browser)."""
    try:
        import selenium  # noqa: F401
    except ImportError:
        return False
    if _firefox_binary() is not None:
        return True
    try:
        import app_browser
    except ImportError:
        return False
    return app_browser.available()


class ScholarBrowser:
    """A lazily-started headless Firefox that fetches Scholar pages.  Not
    safe for concurrent use — the caller serializes fetches with a lock."""

    def __init__(self) -> None:
        self._driver = None
        self._lock = threading.Lock()
        # google.com cookies a CAPTCHA answered in the visible window earned
        # (Google's exemption chiefly), seeded into the next headless session.
        self._earned: list = []
        # No CAPTCHA window before this (monotonic): one went unanswered.
        self._solve_after = 0.0
        atexit.register(self.close)

    # -- lifecycle -----------------------------------------------------------

    def _ensure_driver(self, headless: bool = True):
        if self._driver is not None:
            return self._driver
        try:
            from selenium import webdriver
            from selenium.webdriver.firefox.options import Options
            from selenium.webdriver.firefox.service import Service
        except ImportError as exc:  # pragma: no cover
            raise BrowserUnavailable(f"selenium missing: {exc}")

        binary = _firefox_binary()
        if not binary:
            raise BrowserUnavailable("Firefox not found")

        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        opts = Options()
        if headless:
            opts.add_argument("-headless")
        # A dedicated, persistent profile (accumulates its own good standing;
        # never touches the user's live, locked profile).
        opts.add_argument("-profile")
        opts.add_argument(str(_PROFILE_DIR))
        try:
            opts.binary_location = binary
        except Exception:
            pass
        # Trim obvious automation tells (harmless for the TLS-level block, but
        # cheap insurance against JS-level checks).
        for pref, val in (
            ("dom.webdriver.enabled", False),
            ("useAutomationExtension", False),
            ("general.useragent.override", None),  # keep Firefox's real UA
        ):
            if val is not None:
                opts.set_preference(pref, val)
        opts.set_preference("intl.accept_languages", "en-US, en")

        try:
            # Selenium Manager (4.6+) resolves geckodriver automatically.
            self._driver = webdriver.Firefox(options=opts, service=Service())
        except Exception as exc:
            raise BrowserUnavailable(f"could not start Firefox: {exc}")
        self._driver.set_page_load_timeout(_PAGE_LOAD_TIMEOUT)
        self._seed_cookies(self._driver)
        return self._driver

    def _seed_cookies(self, driver) -> None:
        """Copy the user's google.com cookies into this session, so the driven
        Firefox carries the same standing as their normal one — and, ahead of
        them, any a CAPTCHA answered in the visible window earned.
        Best-effort: cookies can only be set while on the domain, so we load
        the (laxly guarded) Scholar homepage first."""
        earned = list(self._earned)
        names = {row.get("name") for row in earned}
        rows = earned + [row for row in _google_cookie_rows()
                         if row["name"] not in names]
        if not rows:
            return
        try:
            driver.get("https://scholar.google.com/")
        except Exception:
            return
        added = 0
        for row in rows:
            try:
                driver.add_cookie(row)
                added += 1
            except Exception:
                # Retry without the domain (Selenium is strict about matching
                # the current host); Firefox then scopes it to google.com.
                try:
                    driver.add_cookie({k: v for k, v in row.items()
                                       if k != "domain"})
                    added += 1
                except Exception:
                    pass
        if added:
            print(f"[scholar-browser] seeded {added} google.com cookies")

    def close(self) -> None:
        drv, self._driver = self._driver, None
        if drv is not None:
            try:
                drv.quit()
            except Exception:
                pass

    def restart(self) -> None:
        self.close()
        self._ensure_driver()

    # -- fetch ---------------------------------------------------------------

    def fetch(self, url: str, wait_for: str = "",
              solve: bool = False) -> "tuple[str, str]":
        """Navigate to *url* and return ``(page_source, final_url)`` once the
        page's content has loaded.  ``wait_for`` is a CSS selector to wait for
        (the results container or the opinion div); when empty a sensible one
        is chosen from the URL.  On a challenge, *solve* puts it to the reader
        in a window they can see (see :meth:`_solve`); without it, or when
        they don't answer, :class:`BrowserBlocked` is raised.  Raises
        :class:`BrowserUnavailable` when Firefox can't be driven."""
        from selenium.common.exceptions import (
            TimeoutException, WebDriverException,
        )

        if _firefox_binary() is None:
            return self._fetch_elsewhere(url, wait_for, solve)
        with self._lock:
            driver = self._ensure_driver()
            if not wait_for:
                wait_for = ("#gs_opinion" if "scholar_case" in url
                            else "#gs_res_ccl, .gs_r")
            try:
                driver.get(url)
            except TimeoutException:
                pass  # partial load is fine — we inspect what arrived
            except WebDriverException as exc:
                # A dead driver (crash / profile hiccup): restart once.
                self.restart()
                driver = self._driver
                try:
                    driver.get(url)
                except Exception as exc2:
                    raise BrowserUnavailable(f"navigation failed: {exc2}")

            self._await_content(driver, wait_for)
            source = driver.page_source or ""
            final_url = driver.current_url or url
            if _challenged(final_url, source):
                if solve:
                    return self._solve(url, wait_for)
                raise BrowserBlocked()
            return source, final_url

    def _solve(self, url: str, wait_for: str) -> "tuple[str, str]":
        """Google has challenged the headless Firefox too: put the CAPTCHA to
        the reader.  *url* is opened again in a Firefox window they can see,
        on the same profile, and this waits — up to _SOLVE_TIMEOUT — for the
        challenge to be answered and the page behind it to load, and returns
        that page.  The window then closes; the cookies answering earned go
        on into the headless session the next fetch starts.

        Raises :class:`BrowserBlocked` when the reader closes the window or
        lets it time out — and, for _SOLVE_SNOOZE after that, at once and
        without a window, so an unanswered challenge does not come back on
        every fetch."""
        from selenium.common.exceptions import (
            TimeoutException, WebDriverException,
        )

        if time.monotonic() < self._solve_after:
            raise BrowserBlocked()
        self.close()  # a profile is open in one Firefox at a time
        try:
            driver = self._ensure_driver(headless=False)
        except BrowserUnavailable as exc:
            print(f"[scholar-browser] no window for the CAPTCHA: {exc}")
            self._solve_after = time.monotonic() + _SOLVE_SNOOZE
            raise BrowserBlocked()
        print("[scholar-browser] Google challenged Firefox too — waiting for "
              "the CAPTCHA to be answered in the window")
        _tell(on_step, "Google Scholar wants a CAPTCHA answered — answer it in "
                       "the Firefox window that has opened…")

        def go(target: str) -> None:
            try:
                driver.get(target)
            except TimeoutException:
                pass  # partial load is fine — we inspect what arrived

        try:
            go(url)
            deadline = time.monotonic() + _SOLVE_TIMEOUT
            while time.monotonic() < deadline:
                final_url = driver.current_url or url
                source = driver.page_source or ""
                if not _challenged(final_url, source):
                    if urlparse(final_url).path != urlparse(url).path:
                        # Answered, but left somewhere else: go on to the page
                        # asked for, now that Google lets us.
                        go(url)
                    self._await_content(driver, wait_for)
                    final_url = driver.current_url or url
                    source = driver.page_source or ""
                    if not _challenged(final_url, source):
                        self._earned = driver.get_cookies() or []
                        print("[scholar-browser] CAPTCHA answered")
                        return source, final_url
                driver.execute_script(_NOTE_JS, _SOLVE_NOTE)
                time.sleep(1.0)
        except WebDriverException:
            pass  # the reader closed the window
        finally:
            self.close()  # the next fetch starts headless again
        print("[scholar-browser] the CAPTCHA went unanswered")
        self._solve_after = time.monotonic() + _SOLVE_SNOOZE
        raise BrowserBlocked()

    def _fetch_elsewhere(self, url: str, wait_for: str,
                         solve: bool) -> "tuple[str, str]":
        """:meth:`fetch` where Firefox isn't installed: the browser the app
        drives for itself (see app_browser) loads the page, minimized, and
        on a challenge is brought up for the reader to answer it when
        *solve* — not for _SOLVE_SNOOZE after one went unanswered."""
        import app_browser

        if not wait_for:
            wait_for = ("#gs_opinion" if "scholar_case" in url
                        else "#gs_res_ccl, .gs_r")
        try:
            browser = app_browser.shared()
        except app_browser.Unavailable as exc:
            raise BrowserUnavailable(str(exc))
        ask = solve and time.monotonic() >= self._solve_after
        try:
            return browser.open(
                url, challenged=_challenged,
                ready=lambda d: self._content_ready(d, wait_for), ask=ask,
                note=_SOLVE_NOTE,
                waiting=(f"Google Scholar wants a CAPTCHA answered — answer "
                         f"it in the {browser.name} window that has opened…"),
                on_step=on_step)
        except app_browser.NeedsPerson:
            if ask:
                self._solve_after = time.monotonic() + _SOLVE_SNOOZE
            raise BrowserBlocked()
        except app_browser.Unavailable as exc:
            raise BrowserUnavailable(str(exc))

    @staticmethod
    def _content_ready(d, wait_for: str) -> bool:
        """Whether the page has come to a state worth reading: any of
        *wait_for*'s selectors present, or Scholar's "no results" notice, or
        the challenge page."""
        from selenium.webdriver.common.by import By

        for sel in wait_for.split(","):
            sel = sel.strip()
            if sel and d.find_elements(By.CSS_SELECTOR, sel):
                return True
        src = (d.page_source or "").lower()
        return ("did not match any" in src or "/sorry/" in d.current_url
                or "unusual traffic" in src)

    @staticmethod
    def _await_content(driver, wait_for: str) -> None:
        from selenium.webdriver.support.ui import WebDriverWait

        try:
            WebDriverWait(driver, _CONTENT_TIMEOUT).until(
                lambda d: ScholarBrowser._content_ready(d, wait_for))
        except Exception:
            pass  # timed out — return whatever is present; caller handles it


if __name__ == "__main__":  # pragma: no cover - live smoke test
    import sys

    print("available:", available())
    if not available():
        print("  (needs `pip install selenium` and Firefox)")
        sys.exit(0)
    q = sys.argv[1] if len(sys.argv) > 1 else '"347 U.S. 483"'
    from urllib.parse import quote_plus
    br = ScholarBrowser()
    try:
        t0 = time.perf_counter()
        html, final = br.fetch(
            f"https://scholar.google.com/scholar?q={quote_plus(q)}&as_sdt=4",
            solve=True)
        dt = time.perf_counter() - t0
        has_rows = "gs_r" in html or "did not match" in html
        print(f"fetched {len(html):,} chars in {dt:.1f}s; final={final[:80]}")
        print("results present:", has_rows)
    except BrowserBlocked:
        print("BLOCKED even in real Firefox, and the CAPTCHA went unanswered.")
    except BrowserUnavailable as exc:
        print("unavailable:", exc)
    finally:
        br.close()
