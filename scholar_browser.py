"""Fetch Google Scholar pages through the user's real Firefox (Selenium).

Google Scholar's search endpoint validates the TLS/HTTP2 handshake, not just
cookies or the User-Agent: a real browser passes even from an IP that Scholar
has flagged, while every curl_cffi impersonation profile gets the "/sorry/
unusual traffic" challenge (verified 2026-07-03 — same IP, same google.com
cookies, real Firefox passed a fresh search and all curl_cffi fingerprints
429'd).  So when the impersonation layer in :mod:`google_scholar` is blocked,
it falls back here and drives the actual Firefox, whose genuine Gecko network
stack Scholar trusts. When Google challenges that fetch, Selenium opens a
visible Firefox window and waits for the user to answer the CAPTCHA.

Design:
  * A **dedicated** headless Firefox profile (under the app cache) — never the
    user's live profile, which Firefox locks while it is open.  Headless uses
    the same NSS/TLS stack as a headed Firefox, so the fingerprint Scholar
    sees is identical; only rendering is skipped.
  * Selenium 4's built-in driver manager downloads geckodriver on demand, so
    there is no manual setup beyond ``pip install selenium`` and Firefox.
  * The driver is started lazily and kept alive for reuse (start-up is slow);
    :func:`ScholarBrowser.close` / atexit tear it down.
  * When Google challenges even this Firefox, the reader solves it in a
    visible Selenium window on the same profile (see
    :meth:`ScholarBrowser._solve`). The loaded page and cookies are captured
    before that window closes automatically. Session cookies are also saved
    separately, so the next headless session and app restart retain them.

Everything here is optional: :func:`available` is False when Selenium or
Firefox is missing, and the caller degrades to the local opinion DB /
CourtListener exactly as before.
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
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
    "id=\"recaptcha\"", "g-recaptcha", "google.com/recaptcha",
    "automated queries",
)


_SOLVE_NOTE = (
    "GetCases: Google Scholar wants to check that a person is asking. Answer "
    "the CAPTCHA below and the page you asked for opens in GetCases; this "
    "window then closes by itself."
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
_store_binary = None


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
        binary = eng_rep_pdf._firefox_exe()
        # Microsoft's app execution alias is a zero-byte launcher. It opens
        # Firefox normally, but geckodriver rejects it as an invalid binary.
        if binary and os.name == "nt" and Path(binary).stat().st_size == 0:
            return _store_firefox_binary()
        return binary
    except Exception:
        return None


def _store_firefox_binary() -> Optional[str]:
    """Resolve Store Firefox through its registered package, without admin
    access or assuming its version-dependent WindowsApps directory name."""
    global _store_binary
    if _store_binary and Path(_store_binary).is_file():
        return _store_binary
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "Get-AppxPackage -Name Mozilla.Firefox | "
             "Select-Object -ExpandProperty InstallLocation"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            for location in result.stdout.splitlines():
                if not location.strip():
                    continue
                root = Path(location.strip())
                for exe in (root / "VFS" / "ProgramFiles" / "Firefox Package Root" / "firefox.exe",
                            root / "firefox.exe"):
                    if exe.is_file():
                        _store_binary = str(exe)
                        return _store_binary
    except (OSError, subprocess.SubprocessError):
        pass
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
                domain = host.lstrip(".").lower()
                key = (domain, path or "/", name)
                if (domain != "google.com" and not domain.endswith(".google.com")
                        or name.startswith("__Host-") or key in seen):
                    continue
                # Firefox writes ``expiry`` in milliseconds now; a dead cookie
                # replayed into the browser is worse than none, since it is the
                # standing behind these that keeps a search from being
                # challenged (see eng_rep_pdf._cookie_expiry_seconds).
                exp = eng_rep_pdf._cookie_expiry_seconds(expiry)
                if exp and exp <= now:
                    continue
                seen.add(key)
                row = {
                    "name": name, "value": value,
                    "domain": host,
                    "path": path or "/",
                    "secure": bool(is_secure) or name.startswith("__Secure-"),
                }
                if exp:
                    row["expiry"] = int(exp)
                rows.append(row)
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
        self._earned: list = self._load_cookies()
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
        opts.add_argument("-no-remote")
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
        Scholar first. If that redirects to Google's check on another host,
        its robots.txt page gives us a Scholar origin for scoped cookies."""
        try:
            driver.get("https://scholar.google.com/")
            if urlparse(driver.current_url).hostname != "scholar.google.com":
                driver.get("https://scholar.google.com/robots.txt")
        except Exception:
            return
        # A solved CAPTCHA in this dedicated profile is newer than cookies
        # borrowed from the user's everyday Firefox. Never overwrite it with
        # an older exemption on the next start.
        try:
            existing = driver.get_cookies() or []
        except Exception:
            existing = []
        rows = self._valid_cookies(
            list(self._earned) + existing + _google_cookie_rows())
        added = 0
        for row in rows:
            try:
                driver.add_cookie(row)
                added += 1
            except Exception:
                # Do not widen or change a cookie's domain when Google has
                # redirected the homepage to a different host for a check.
                pass
        if added:
            print(f"[scholar-browser] seeded {added} google.com cookies")

    @staticmethod
    def _valid_cookies(rows) -> list:
        """Keep live Google cookies, preserving their domain/path and flags.

        The first copy of each scoped cookie wins. In particular, a borrowed
        Firefox cookie must not replace one earned by our CAPTCHA window.
        """
        out, seen = [], set()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            if not isinstance(row.get("domain"), str):
                continue
            domain = row["domain"].lstrip(".").lower()
            if domain != "google.com" and not domain.endswith(".google.com"):
                continue
            if (not isinstance(row.get("name"), str)
                    or not isinstance(row.get("value"), str)
                    or not isinstance(row.get("path", "/"), str)):
                continue
            try:
                if row.get("expiry") and float(row["expiry"]) <= time.time():
                    continue
            except (TypeError, ValueError):
                continue
            key = (domain, row.get("path", "/"), row["name"])
            if key not in seen:
                seen.add(key)
                out.append(dict(row))
        return out

    @staticmethod
    def _load_cookies() -> list:
        try:
            rows = json.loads((_PROFILE_DIR / "scholar_cookies.json").read_text(
                encoding="utf-8"))
            return ScholarBrowser._valid_cookies(rows)
        except (OSError, ValueError, TypeError):
            return []

    def _remember_cookies(self, driver) -> None:
        try:
            self._earned = self._valid_cookies(
                list(driver.get_cookies() or []) + self._earned)
        except Exception:
            return  # A closed window must not discard the previous clearance.
        try:
            _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            path = _PROFILE_DIR / "scholar_cookies.json"
            pending = path.with_suffix(".tmp")
            pending.write_text(json.dumps(self._earned), encoding="utf-8")
            pending.replace(path)
        except OSError:
            pass  # In-memory reuse still works when the cache isn't writable.

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
            self._remember_cookies(driver)
            return source, final_url

    def _solve(self, url: str, wait_for: str) -> "tuple[str, str]":
        """Let the reader answer in visible Firefox, capturing the resulting
        page and cookies before closing it. Never require them to close the
        window to continue: that loses session cookies and the loaded page.
        An unanswered window is snoozed for :data:`_SOLVE_SNOOZE`."""
        from selenium.common.exceptions import TimeoutException, WebDriverException

        if time.monotonic() < self._solve_after:
            raise BrowserBlocked()
        self._remember_cookies(self._driver)
        self.close()  # a profile is open in one Firefox at a time
        try:
            driver = self._ensure_driver(headless=False)
        except BrowserUnavailable as exc:
            self.close()
            print(f"[scholar-browser] no window for the CAPTCHA: {exc}")
            self._solve_after = time.monotonic() + _SOLVE_SNOOZE
            raise BrowserBlocked()

        print("[scholar-browser] waiting for the user in the Firefox window")
        _tell(on_step, "Google Scholar needs a CAPTCHA check. Answer it in the "
                       "Firefox window that has opened; GetCases will "
                       "continue automatically…")

        def go(target):
            try:
                driver.get(target)
            except TimeoutException:
                pass  # Inspect the partial load rather than abandoning it.

        try:
            go(url)
            deadline = time.monotonic() + _SOLVE_TIMEOUT
            while time.monotonic() < deadline:
                final_url = driver.current_url or ""
                source = driver.page_source or ""
                # A blank page or an intermediate redirect isn't a completed
                # check. Only resume once Scholar itself has loaded content.
                if (urlparse(final_url).hostname == "scholar.google.com"
                        and source.strip() and not _challenged(final_url, source)):
                    if urlparse(final_url).path != urlparse(url).path:
                        go(url)
                    self._await_content(driver, wait_for)
                    final_url = driver.current_url or url
                    source = driver.page_source or ""
                    if (urlparse(final_url).hostname == "scholar.google.com"
                            and not _challenged(final_url, source)
                            and self._content_ready(driver, wait_for)):
                        self._remember_cookies(driver)
                        self._solve_after = 0.0
                        print("[scholar-browser] CAPTCHA answered")
                        return source, final_url
                driver.execute_script(_NOTE_JS, _SOLVE_NOTE)
                time.sleep(1.0)
        except WebDriverException:
            pass  # The reader closed the window or Firefox exited.
        finally:
            self.close()  # The next fetch starts headless with the new cookies.

        print("[scholar-browser] the CAPTCHA was not cleared")
        self._solve_after = time.monotonic() + _SOLVE_SNOOZE
        raise BrowserBlocked()

    @staticmethod
    def _content_ready(driver, wait_for: str) -> bool:
        from selenium.webdriver.common.by import By

        return (any(driver.find_elements(By.CSS_SELECTOR, sel.strip())
                    for sel in wait_for.split(",") if sel.strip())
                or "did not match any" in (driver.page_source or "").lower())

    @staticmethod
    def _await_content(driver, wait_for: str) -> None:
        from selenium.webdriver.support.ui import WebDriverWait

        def ready(d) -> bool:
            # Any target selector present, or Scholar's "no results" notice,
            # or the challenge page — all terminal states.
            return (_challenged(d.current_url, d.page_source)
                    or ScholarBrowser._content_ready(d, wait_for))

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
