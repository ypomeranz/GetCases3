"""Fetch Google Scholar pages through the user's real Firefox (Selenium).

Google Scholar's search endpoint validates the TLS/HTTP2 handshake, not just
cookies or the User-Agent: a real browser passes even from an IP that Scholar
has flagged, while every curl_cffi impersonation profile gets the "/sorry/
unusual traffic" challenge (verified 2026-07-03 — same IP, same google.com
cookies, real Firefox passed a fresh search and all curl_cffi fingerprints
429'd).  So when the impersonation layer in :mod:`google_scholar` is blocked,
it falls back here and drives the actual Firefox, whose genuine Gecko network
stack Scholar trusts. When Google challenges that fetch, Selenium quits and
the user answers in ordinary Firefox, with no WebDriver attached.

Design:
  * A **dedicated** headless Firefox profile (under the app cache) — never the
    user's live profile, which Firefox locks while it is open.  Headless uses
    the same NSS/TLS stack as a headed Firefox, so the fingerprint Scholar
    sees is identical; only rendering is skipped.
  * Selenium 4's built-in driver manager downloads geckodriver on demand, so
    there is no manual setup beyond ``pip install selenium`` and Firefox.
  * The driver is started lazily and kept alive for reuse (start-up is slow);
    :func:`ScholarBrowser.close` / atexit tear it down.
  * The CAPTCHA window uses a separate persistent profile that Selenium never
    opens. A temporary helper returns results and cookies and closes the
    window automatically; only a page needing attention is brought forward.
    If that helper cannot start, closing the window still imports its fresh
    cookies and saved results URL. Google sometimes puts the exemption only
    in that URL; Firefox's saved session preserves it too.
    On Windows, ``-wait-for-browser`` keeps the launcher alive until the
    actual window closes, avoiding a premature automated retry.

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
from email.utils import parsedate_to_datetime
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, parse_qsl, urlparse

import scholar_pages
from firefox_handoff import FirefoxHandoff, PREFERENCES as HANDOFF_PREFERENCES

_PROFILE_DIR = Path.home() / ".cache" / "courtlistener_scholar_ff"
_PAGE_LOAD_TIMEOUT = 40
_CONTENT_TIMEOUT = 20  # seconds to wait for results / opinion to appear
_SOLVE_TIMEOUT = 300   # seconds the reader has to answer a CAPTCHA
_SOLVE_SNOOZE = 600    # seconds no CAPTCHA window opens after one went unanswered
_MAX_SESSION_BYTES = 32 * 1024 * 1024

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
    return scholar_pages.is_challenge(url, source)


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


def _google_cookie_rows(dbs=None) -> list:
    """Full google.com cookie rows from the user's Firefox profile(s), as
    Selenium ``add_cookie`` dicts.  These carry the account/reputation state
    (chiefly ``NID``) that — together with a real Gecko fingerprint — is what
    lets a search pass; a fresh profile has the fingerprint but not the
    standing, so it is challenged.  Host-only ``__Host-*`` cookies are skipped
    (they can't be re-scoped)."""
    import shutil
    import sqlite3
    import tempfile
    import time
    try:
        import eng_rep_pdf
        if dbs is None:
            dbs = eng_rep_pdf._firefox_cookie_dbs()
    except Exception:
        return []
    seen: set = set()
    rows: list = []
    for db in dbs:
        try:
            # Firefox's newest cookies may still be in the WAL, even after
            # its window closes. Copy the DB and journal together, never
            # writing to the user's live store.
            with tempfile.TemporaryDirectory(prefix="scholar_cookies_") as tmp:
                snapshot = Path(tmp) / "cookies.sqlite"
                shutil.copy2(db, snapshot)
                for suffix in ("-wal", "-shm"):
                    side = Path(str(db) + suffix)
                    if side.is_file():
                        shutil.copy2(side, str(snapshot) + suffix)
                con = sqlite3.connect(snapshot)
                try:
                    have = {row[1] for row in con.execute("PRAGMA table_info(moz_cookies)")}
                    # The most recently updated partition wins for a cookie
                    # with the same domain, path and name.
                    stamps = [col for col in ("updateTime", "lastAccessed", "creationTime")
                              if col in have]
                    order = (" ORDER BY " + ", ".join(col + " DESC" for col in stamps)
                             if stamps else "")
                    found = con.execute(
                        "SELECT name, value, host, path, isSecure, expiry "
                        "FROM moz_cookies WHERE host LIKE '%google.com'" + order
                    ).fetchall()
                finally:
                    con.close()
            now = time.time()
            for name, value, host, path, is_secure, expiry in found:
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
        except Exception:
            continue
    return rows


def available() -> bool:
    """True when a Scholar fetch through Firefox is possible here."""
    try:
        import selenium  # noqa: F401
    except ImportError:
        return False
    return _firefox_binary() is not None


def _read_firefox_session(profile: Path) -> dict:
    """Read the latest native Firefox session, without starting a browser.

    A solved Scholar check may exist only in the results URL, rather than in
    cookies.sqlite. Firefox saves that URL in its Mozilla-LZ4 session file.
    Do not fall back to stale backups when the latest valid session is empty.
    """
    paths = [profile / "sessionstore.jsonlz4",
             profile / "sessionstore-backups" / "recovery.jsonlz4",
             profile / "sessionstore-backups" / "recovery.baklz4",
             profile / "sessionstore-backups" / "previous.jsonlz4"]
    existing = []
    for path in paths:
        try:
            if path.is_file():
                existing.append((path.stat().st_mtime_ns, path))
        except OSError:
            pass
    for _mtime, path in sorted(existing, reverse=True):
        try:
            if path.stat().st_size > _MAX_SESSION_BYTES:
                continue
            data = path.read_bytes()
            if (not data.startswith(b"mozLz40\0") or len(data) < 12
                    or int.from_bytes(data[8:12], "little") > _MAX_SESSION_BYTES):
                continue
            import lz4.block
            state = json.loads(lz4.block.decompress(data[8:]))
            if isinstance(state, dict):
                return state
        except ImportError:
            print("[scholar-browser] reading Firefox's cleared URL needs lz4 "
                  "(install with `pip install lz4`)")
            return {}
        except Exception:
            # Includes corrupt/truncated LZ4 data during a Firefox shutdown.
            continue
    return {}


def _scholar_page_key(url: str) -> tuple:
    """The requested Scholar page, ignoring Google's handoff parameters."""
    try:
        parts = urlparse(url)
        if (parts.scheme != "https" or parts.hostname != "scholar.google.com"
                or parts.port not in (None, 443) or parts.username or parts.password):
            return ()
    except (TypeError, ValueError):
        return ()
    return (parts.path, tuple(sorted(
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in {"google_abuse", "sgst"})))


def _session_clearance(state: dict, requested_url: str) -> tuple[list, str]:
    """Recover Google's cookie assignment and the matching cleared URL.

    Accept only Google's exemption, from HTTPS Scholar URLs. The encoded
    assignment contains domain/path/expiry attributes, not just a token.
    Never log the URL or the cookie's value.
    """
    tabs = []
    for group in ("windows", "_closedWindows"):
        windows = state.get(group, [])
        for window in windows if isinstance(windows, list) else []:
            if isinstance(window, dict) and isinstance(window.get("tabs"), list):
                tabs.extend(tab for tab in window["tabs"] if isinstance(tab, dict))
    tabs.sort(key=lambda tab: (tab.get("lastAccessed", 0)
                              if isinstance(tab.get("lastAccessed", 0), (int, float))
                              else 0), reverse=True)
    matching, other, cleared_url = [], [], ""
    wanted = _scholar_page_key(requested_url)
    for tab in tabs:
        entries = tab.get("entries", [])
        for entry in reversed(entries if isinstance(entries, list) else []):
            if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
                continue
            url = entry["url"]
            key = _scholar_page_key(url)
            if not key:
                continue
            try:
                assignment = parse_qs(urlparse(url).query).get("google_abuse", [""])[0]
                cookie = SimpleCookie()
                cookie.load(assignment)
                exemption = cookie.get("GOOGLE_ABUSE_EXEMPTION")
                if not exemption or not exemption.value:
                    continue
                domain = exemption["domain"].lstrip(".").lower() or "google.com"
                if domain not in {"google.com", "scholar.google.com"}:
                    continue
                row = {"name": "GOOGLE_ABUSE_EXEMPTION", "value": exemption.value,
                       "domain": "." + domain, "path": exemption["path"] or "/",
                       "secure": True, "httpOnly": bool(exemption["httponly"])}
                if exemption["expires"]:
                    row["expiry"] = int(parsedate_to_datetime(exemption["expires"]).timestamp())
                    if row["expiry"] <= time.time():
                        continue
                if wanted and key == wanted:
                    matching.append(row)
                    if not cleared_url:
                        cleared_url = url
                else:
                    other.append(row)
            except (CookieError, TypeError, ValueError, OverflowError):
                continue
    return matching + other, cleared_url


class ScholarBrowser:
    """A lazily-started headless Firefox that fetches Scholar pages.  Not
    safe for concurrent use — the caller serializes fetches with a lock."""

    def __init__(self) -> None:
        self._driver = None
        self._manual_process = None
        self._manual_imported = False
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
        self._save_cookies()

    def _save_cookies(self) -> None:
        try:
            _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            path = _PROFILE_DIR / "scholar_cookies.json"
            pending = path.with_suffix(".tmp")
            pending.write_text(json.dumps(self._earned), encoding="utf-8")
            pending.replace(path)
        except OSError:
            pass  # In-memory reuse still works when the cache isn't writable.

    def _manual_profile(self) -> Path:
        # This child directory is never passed to Selenium. Keep its browser
        # preferences and standing separate from the automated profile.
        return _PROFILE_DIR / "manual"

    def _prepare_manual_profile(self) -> Path:
        profile = self._manual_profile()
        profile.mkdir(parents=True, exist_ok=True)
        prefs = {
            "browser.shell.checkDefaultBrowser": False,
            "browser.aboutwelcome.enabled": False,
            "browser.startup.homepage_override.mstone": "ignore",
            "browser.startup.page": 0,
            "intl.accept_languages": "en-US, en",
            **HANDOFF_PREFERENCES,
        }
        (profile / "user.js").write_text("".join(
            f"user_pref({json.dumps(key)}, {json.dumps(value)});\n"
            for key, value in prefs.items()), encoding="utf-8")
        return profile

    def _import_manual_cookies(self, requested_url: str = "") -> str:
        state = _read_firefox_session(self._manual_profile())
        clearance, cleared_url = _session_clearance(state, requested_url)
        rows = _google_cookie_rows([self._manual_profile() / "cookies.sqlite"])
        # Import BEFORE restarting Selenium: otherwise its previously saved
        # exemption would overwrite the one the user has just earned.
        self._earned = self._valid_cookies(clearance + rows + self._earned)
        self._manual_imported = True
        self._save_cookies()
        if clearance:
            print("[scholar-browser] recovered CAPTCHA exemption from Firefox's cleared URL")
        return cleared_url or requested_url

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
            resume_url = url
            if self._manual_process is not None:
                if self._manual_process.poll() is None:
                    # A timed-out manual window is still owned by the reader.
                    # Do not relaunch it or race it with an automated request.
                    raise BrowserBlocked()
                self._manual_process = None
                resume_url = self._import_manual_cookies(url)
            elif not self._manual_imported and self._manual_profile().is_dir():
                # Recover an already-solved check after an app restart, too.
                resume_url = self._import_manual_cookies(url)
            driver = self._ensure_driver()
            if not wait_for:
                wait_for = ("#gs_opinion" if "scholar_case" in url
                            else "#gs_res_ccl, .gs_r")
            try:
                driver.get(resume_url)
            except TimeoutException:
                pass  # partial load is fine — we inspect what arrived
            except WebDriverException as exc:
                # A dead driver (crash / profile hiccup): restart once.
                self.restart()
                driver = self._driver
                try:
                    driver.get(resume_url)
                except Exception as exc2:
                    raise BrowserUnavailable(f"navigation failed: {exc2}")

            self._await_content(driver, wait_for)
            source = driver.page_source or ""
            final_url = driver.current_url or url
            if scholar_pages.response_needs_browser(url, final_url, source):
                if solve:
                    return self._solve(url, wait_for)
                raise BrowserBlocked()
            self._remember_cookies(driver)
            return source, final_url

    def _solve(self, url: str, wait_for: str) -> "tuple[str, str]":
        """Hand the check to an ordinary browser, then import its cookies.

        A temporary helper returns the page and cookies and closes its tab
        after results arrive. No Selenium driver attaches to this profile.
        If the helper is unavailable, closing the window still imports its
        saved cookies and session. A timeout leaves the reader's window open.
        """
        from selenium.common.exceptions import TimeoutException, WebDriverException

        if time.monotonic() < self._solve_after:
            raise BrowserBlocked()
        self._remember_cookies(self._driver)
        self.close()
        handoff = None

        def needs_reader():
            _tell(on_step, "Google Scholar needs attention in Firefox. Complete "
                           "the check there; the window will close automatically "
                           "when the results are ready…")

        try:
            binary = _firefox_binary()
            if not binary:
                raise BrowserUnavailable("Firefox not found")
            profile = self._prepare_manual_profile()
            args = [binary, "-no-remote", "-profile", str(profile)]
            try:
                handoff = FirefoxHandoff(profile, url, _scholar_page_key, needs_reader)
                port = handoff.start()
                args.extend(["-start-debugger-server", str(port)])
            except (OSError, ValueError) as exc:
                if handoff:
                    handoff.close()
                handoff = None
                print(f"[scholar-browser] automatic handoff unavailable: {exc}")
            if os.name == "nt":
                args.append("-wait-for-browser")
            args.append(url)
            env = {key: value for key, value in os.environ.items()
                   if key not in ("MOZ_AUTOMATION", "MOZ_MARIONETTE")
                   and not key.startswith("MOZ_HEADLESS")}
            startup = {}
            if handoff and os.name == "nt":
                info = subprocess.STARTUPINFO()
                info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                info.wShowWindow = 7  # SW_SHOWMINNOACTIVE: no foreground flash
                startup["startupinfo"] = info
            process = self._manual_process = subprocess.Popen(args, env=env, **startup)
        except (OSError, BrowserUnavailable) as exc:
            if handoff:
                handoff.close()
            print(f"[scholar-browser] no window for the CAPTCHA: {exc}")
            self._solve_after = time.monotonic() + _SOLVE_SNOOZE
            raise BrowserBlocked()

        if handoff:
            try:
                handoff.install(port, process)
                _tell(on_step, "Loading Google Scholar in Firefox, out of sight…")
            except (OSError, ValueError, KeyError) as exc:
                print(f"[scholar-browser] automatic handoff unavailable: {exc}")
                handoff.close()
                handoff = None
        if not handoff:
            print("[scholar-browser] waiting for the user in ordinary Firefox")
            _tell(on_step, "Open the ordinary Firefox window from the taskbar if "
                           "needed. Answer any CAPTCHA there. Once Scholar's "
                           "results appear, close that window to continue in GetCases…")
        deadline = time.monotonic() + _SOLVE_TIMEOUT
        try:
            while process.poll() is None and time.monotonic() < deadline:
                if handoff and handoff.ready.wait(0.5):
                    break
                if not handoff:
                    time.sleep(0.5)
            if handoff and handoff.result:
                source, final_url = handoff.result
                # A clearance may be carried only in the results URL.
                state = {"windows": [{"tabs": [{"entries": [{"url": final_url}], "index": 1}]}]}
                clearance, _ = _session_clearance(state, url)
                self._earned = self._valid_cookies(clearance + handoff.cookies + self._earned)
                self._save_cookies()
                self._manual_imported = True
                self._solve_after = 0.0
                # Save first, then close only this successfully completed
                # helper browser. Unanswered manual windows remain untouched.
                if handoff.finish(process):
                    self._manual_process = None
                return source, final_url
        finally:
            if handoff:
                handoff.close()
        if process.poll() is None:
            print("[scholar-browser] manual Firefox is still open; leaving it "
                  "with the user and pausing Scholar requests")
            self._solve_after = time.monotonic() + _SOLVE_SNOOZE
            raise BrowserBlocked()

        self._manual_process = None
        resume_url = self._import_manual_cookies(url)
        try:
            driver = self._ensure_driver()
            try:
                driver.get(resume_url)
            except TimeoutException:
                pass
            self._await_content(driver, wait_for)
            source, final_url = driver.page_source or "", driver.current_url or url
            if (urlparse(final_url).hostname == "scholar.google.com"
                    and not _challenged(final_url, source)
                    and self._content_ready(driver, wait_for)):
                self._remember_cookies(driver)
                self._solve_after = 0.0
                print("[scholar-browser] manual Firefox cleared the CAPTCHA")
                return source, final_url
        except (BrowserUnavailable, WebDriverException) as exc:
            print(f"[scholar-browser] could not resume Scholar: {exc}")

        self.close()
        print("[scholar-browser] the CAPTCHA was not cleared")
        self._solve_after = time.monotonic() + _SOLVE_SNOOZE
        raise BrowserBlocked()

    @staticmethod
    def _content_ready(driver, wait_for: str) -> bool:
        from selenium.webdriver.common.by import By

        if scholar_pages.is_search_url(driver.current_url):
            return scholar_pages.has_search_content(driver.page_source or "")
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
