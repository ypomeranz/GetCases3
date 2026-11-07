"""Fetch Google Scholar pages through the user's real Firefox (Selenium).

Google Scholar's search endpoint validates the TLS/HTTP2 handshake, not just
cookies or the User-Agent: a real browser passes even from an IP that Scholar
has flagged, while every curl_cffi impersonation profile gets the "/sorry/
unusual traffic" challenge (verified 2026-07-03 — same IP, same google.com
cookies, real Firefox passed a fresh search and all curl_cffi fingerprints
429'd).  So when the impersonation layer in :mod:`google_scholar` is blocked,
it falls back here and drives the actual Firefox, whose genuine Gecko network
stack Scholar trusts. When Google challenges that fetch, the CAPTCHA is
opened in a separate, ordinary Firefox process on the same dedicated profile;
Selenium is shut down first and never controls the CAPTCHA window.

Design:
  * A **dedicated** headless Firefox profile (under the app cache) — never the
    user's live profile, which Firefox locks while it is open.  Headless uses
    the same NSS/TLS stack as a headed Firefox, so the fingerprint Scholar
    sees is identical; only rendering is skipped.
  * Selenium 4's built-in driver manager downloads geckodriver on demand, so
    there is no manual setup beyond ``pip install selenium`` and Firefox.
  * The driver is started lazily and kept alive for reuse (start-up is slow);
    :func:`ScholarBrowser.close` / atexit tear it down.
  * When Google challenges even this Firefox, the reader solves it in an
    ordinary Firefox process on the same profile (see
    :meth:`ScholarBrowser._solve`). The app retries the page after that
    window closes.

Everything here is optional: :func:`available` is False when Selenium or
Firefox is missing, and the caller degrades to the local opinion DB /
CourtListener exactly as before.
"""

from __future__ import annotations

import atexit
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

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
    """True when a Scholar fetch through Firefox is possible here."""
    try:
        import selenium  # noqa: F401
    except ImportError:
        return False
    return _firefox_binary() is not None


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
        """Google challenged the automated fetch: let the reader solve it in
        an ordinary Firefox process, then resume the fetch with Selenium.

        Selenium quits before Firefox opens. The standalone process uses the
        same dedicated profile, but is started with ``-no-remote`` and is
        never attached to by WebDriver. Once the reader solves the challenge
        and closes that window, Selenium reopens the profile and fetches the
        requested page. This keeps the CAPTCHA interaction out of the
        WebDriver-controlled window.

        Raises :class:`BrowserBlocked` when Firefox cannot be opened, the
        reader closes it without clearing the challenge, or the solve times
        out. An unanswered challenge is snoozed for
        :data:`_SOLVE_SNOOZE`."""

        if time.monotonic() < self._solve_after:
            raise BrowserBlocked()
        self.close()  # a profile is open in one Firefox at a time
        binary = _firefox_binary()
        if not binary:
            print("[scholar-browser] Firefox unavailable for the CAPTCHA")
            self._solve_after = time.monotonic() + _SOLVE_SNOOZE
            raise BrowserBlocked()
        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        print("[scholar-browser] opening an ordinary Firefox window for "
              "the Scholar CAPTCHA")
        _tell(on_step, "Google Scholar needs a CAPTCHA check. An ordinary "
                       "Firefox window has opened; solve it there, then close "
                       "that window to continue…")
        try:
            process = subprocess.Popen([
                binary, "-no-remote", "-profile", str(_PROFILE_DIR), url,
            ])
        except Exception as exc:
            print(f"[scholar-browser] could not open ordinary Firefox: {exc}")
            self._solve_after = time.monotonic() + _SOLVE_SNOOZE
            raise BrowserBlocked()
        try:
            deadline = time.monotonic() + _SOLVE_TIMEOUT
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break  # the reader closed the ordinary Firefox window
                time.sleep(0.5)
        finally:
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    pass

        # Firefox has released the shared profile; retry the original page in
        # the normal headless fetch session and return its actual HTML.
        try:
            from selenium.common.exceptions import TimeoutException
            driver = self._ensure_driver()
            try:
                driver.get(url)
            except TimeoutException:
                pass
            self._await_content(driver, wait_for)
            source = driver.page_source or ""
            final_url = driver.current_url or url
            if not _challenged(final_url, source):
                self._earned = driver.get_cookies() or []
                print("[scholar-browser] ordinary Firefox cleared the CAPTCHA")
                return source, final_url
        except Exception as exc:
            print(f"[scholar-browser] could not resume Scholar fetch: {exc}")

        print("[scholar-browser] the CAPTCHA was not cleared")
        self._solve_after = time.monotonic() + _SOLVE_SNOOZE
        raise BrowserBlocked()

    @staticmethod
    def _await_content(driver, wait_for: str) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        def ready(d) -> bool:
            # Any target selector present, or Scholar's "no results" notice,
            # or the challenge page — all terminal states.
            for sel in wait_for.split(","):
                sel = sel.strip()
                if sel and d.find_elements(By.CSS_SELECTOR, sel):
                    return True
            src = (d.page_source or "").lower()
            return ("did not match any" in src or "/sorry/" in d.current_url
                    or "unusual traffic" in src)

        try:
            WebDriverWait(driver, _CONTENT_TIMEOUT).until(ready)
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
