"""Fetch and cache English Reports PDFs from CommonLII, which is behind
CloudFlare.

CommonLII tar-pits every scripted request: a plain ``requests`` GET (even with a
valid ``cf_clearance`` cookie and a matching User-Agent) is rejected because
CloudFlare fingerprints the TLS handshake (JA3).  The combination that actually
works -- discovered empirically -- is:

  1. The user clears the "Just a moment..." check once **in Firefox** (whose
     cookies, unlike Chrome's app-bound-encrypted store, are readable without
     admin).  This is the only manual step, and only when there is no valid
     clearance yet.
  2. We read Firefox's ``cf_clearance`` (+ ``__cf_bm``/``__cflb``) straight from
     the profile's ``cookies.sqlite`` (Firefox stores cookies unencrypted).  We
     search every profile location ourselves -- standard, Microsoft Store,
     custom ``profiles.ini`` paths, Snap/Flatpak -- so this works where
     ``browser_cookie3``'s single hard-coded path fails ("Could not find firefox
     profile directory"); ``browser_cookie3`` remains a last-ditch fallback.

     What CloudFlare will honour is narrower than what the store says is live,
     and a clearance it has stopped honouring looks exactly like a good one
     until it is refused:

       * The clearance is bound to the **User-Agent of the Firefox that
         obtained it** -- the version string included.  Sent under any other
         version, even the same Firefox one release later, it is refused.  A
         machine can run two Firefox builds at two versions (a Microsoft
         Store build beside a regular one), each with its own profile and its
         own clearance, and either can update underneath a running app.  So
         the UA goes with the profile: it is read from the
         ``compatibility.ini`` beside the cookie store (which records the
         build that last ran that profile), afresh for every fetch.
       * A clearance dies long before ``moz_cookies.expiry`` says.  CloudFlare
         writes it with a year's life and stops honouring it within weeks
         (the address changes, the site re-keys), so the expiry is only a
         floor.  Every profile's clearance is therefore a *candidate*: the
         fetch tries them freshest first and takes the first CloudFlare
         accepts, and only when every one is refused does it send the reader
         back to Firefox.
       * ``moz_cookies.expiry`` is now written in **milliseconds**.  It used to
         be seconds (``creationTime``/``lastAccessed``/``updateTime`` are still
         microseconds), so a reader that trusts the old unit sees every cookie
         as either long dead or absurdly far in the future.
       * Under Total Cookie Protection a cookie is stored **per partition**:
         ``cf_clearance`` arrives with ``isPartitionedAttributeSet=1`` and an
         ``originAttributes`` of ``^partitionKey=(https,commonlii.org)``, and
         copies of the same host+name linger under other partition keys.  One
         host and one name can therefore mean several rows, some of them dead.
         A server re-set keeps a row's original ``creationTime`` too, so a
         clearance obtained this afternoon can carry a creation date from
         months ago; ``updateTime`` is what dates it.

     So we drop expired rows, keep the most recently set copy of each name,
     and order profiles by the age of the clearance itself rather than by
     which ``cookies.sqlite`` was touched last -- a machine with a Microsoft
     Store Firefox beside a regular one has several stores holding a CommonLII
     clearance, and file mtime says nothing about which the user just solved
     the check in.
  3. We GET the PDF with ``curl_cffi`` impersonating Firefox's TLS fingerprint,
     sending the UA of the Firefox that runs the profile the cookie came from
     (so it matches the cookie) and a ``Referer`` to the case's ``.html`` page
     -- the origin Apache hotlink-blocks PDFs requested without it.
  4. The bytes are cached on disk, so the same case never needs the network (or
     the captcha) again.

Chrome and Edge, through the GetCases extension.  Their cookie stores are
app-bound encrypted, so the app cannot read a clearance obtained in them --
but the GetCases browser extension (``browser_extension/``) can.  When a
CommonLII page loads past the check in a browser that has it, the extension
posts the site's cookies, and the headers the browser sent with them, to the
app's loopback port (:mod:`browser_bridge`), which hands them to
:func:`accept_browser_clearance`.  CloudFlare holds a clearance to the
browser it was issued to, so that browser's User-Agent *and* its client
hints go out with the cookies (curl_cffi's own ``sec-ch-ua*`` name another
version and another platform), under curl_cffi's Chrome fingerprint --
Edge's TLS is Chrome's.  The browser is the reader's own, opened on the
profile the extension is in (:func:`extension_browsers`) with nothing
driving it: a browser under automation never passes CommonLII's check.
What the extension hands over is kept on disk beside the scans' cache and
is a candidate like any Firefox profile's; refused, it is dropped, and the
same cookie sent again is not taken back.

All of this is optional: ``curl_cffi`` may be absent, and so may Firefox and
the extension, in which case :func:`can_fetch` is False and the caller falls
back to simply opening the citation in the user's browser.

No tkinter here -- the GUI drives the user-facing hand-off/retry; this module is
the headless fetch+cache engine.  Run ``python -X utf8 eng_rep_pdf.py`` to see
every profile, its clearance and the UA it will be sent under, and to fetch a
sample PDF live (``--fresh`` drops the sample from the cache first so the
network path is really exercised).
"""

from __future__ import annotations

import configparser
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import NamedTuple, Optional

# CommonLII case PDFs land here; keyed by the neutral cite (year-num), which is
# unique per case.  Sits next to the app's existing config file.
CACHE_DIR = Path.home() / ".config" / "courtlistener" / "engr_cache"

_TIMEOUT = 45


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

class FetchUnavailable(Exception):
    """In-app fetching isn't possible here (no browser the app can get a
    clearance from, or a dependency missing); the caller should just open
    the citation in the browser."""


class CloudflareChallenge(Exception):
    """The site served a CloudFlare challenge -- the user must clear it in
    Firefox, or in Chrome or Edge with the GetCases extension.  ``web_url``
    is the page to open for them to do so.  ``refused_ua``, where the app
    did hold a clearance, is the User-Agent it was sent under: CloudFlare
    turned the app away though the check had been passed.  ``refused_by``
    is the browser that clearance came from ("Firefox", "Chrome")."""

    def __init__(self, web_url: str, refused_ua: str = "",
                 refused_by: str = ""):
        super().__init__("CloudFlare challenge")
        self.web_url = web_url
        self.refused_ua = refused_ua
        self.refused_by = refused_by


class OriginError(Exception):
    """The site refused the request without a CloudFlare check (status
    code): the origin's own error, or a CloudFlare rule that blocks rather
    than checks.  Nothing passed in Firefox changes it."""

    def __init__(self, status: int):
        super().__init__(f"origin returned HTTP {status}")
        self.status = status


# ---------------------------------------------------------------------------
# Firefox discovery + User-Agent
# ---------------------------------------------------------------------------

def _firefox_exe() -> Optional[str]:
    """Path to firefox.exe / firefox, or None when Firefox isn't installed."""
    found = shutil.which("firefox")
    if found:
        return found
    cands = [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Mozilla Firefox\firefox.exe"),
        "/Applications/Firefox.app/Contents/MacOS/firefox",
        "/usr/bin/firefox",
        "/usr/local/bin/firefox",
    ]
    for pat in cands:
        for hit in glob.glob(pat):
            if os.path.exists(hit):
                return hit
    return None


def _major_from_ini(path: str, section: str, option: str) -> Optional[str]:
    """The leading version number of *option* in an INI file, or None."""
    try:
        cp = configparser.ConfigParser()
        with open(path, encoding="utf-8") as fh:
            cp.read_file(fh)
        m = re.match(r"(\d+)", cp.get(section, option, fallback=""))
        return m.group(1) if m else None
    except Exception:
        return None


def _profile_firefox_major(db: Path) -> Optional[str]:
    """The major version of the Firefox that last ran the profile holding the
    cookie store *db*, from the ``compatibility.ini`` beside it.  Firefox
    rewrites that file at start-up whenever the build has changed, so by the
    time the reader has cleared a check in it, it names the build that did."""
    return _major_from_ini(str(db.parent / "compatibility.ini"),
                           "Compatibility", "LastVersion")


def _installed_firefox_major() -> Optional[str]:
    """The major version from the ``application.ini`` beside firefox.exe --
    absent for a Microsoft Store install, whose exe is a stub alias."""
    exe = _firefox_exe()
    if not exe:
        return None
    return _major_from_ini(
        os.path.join(os.path.dirname(exe), "application.ini"), "App", "Version")


def _ua_platform() -> str:
    """The platform token real Firefox puts in its User-Agent on this OS.
    Firefox freezes these strings for anti-fingerprinting, so they don't vary
    with the OS point release or CPU: macOS always reports "Intel Mac OS X
    10.15" (even on Apple Silicon) and 64-bit Linux always "X11; Linux
    x86_64"."""
    if sys.platform == "win32":
        return "Windows NT 10.0; Win64; x64"
    if sys.platform == "darwin":
        return "Macintosh; Intel Mac OS X 10.15"
    return "X11; Linux x86_64"


#: Used only when no profile and no install says otherwise (a Firefox that
#: has never run has no clearance to send anyway).
_FALLBACK_MAJOR = "128"


def _user_agent_for(major: str) -> str:
    return (f"Mozilla/5.0 ({_ua_platform()}; rv:{major}.0) "
            f"Gecko/20100101 Firefox/{major}.0")


def firefox_user_agent(profile_db: Optional[Path] = None) -> str:
    """The real User-Agent of the Firefox that runs the profile whose cookie
    store is *profile_db* -- or, given none, of the profile Firefox wrote to
    most recently.

    CloudFlare ties a clearance to the exact UA that obtained it, platform
    token and version both, so the version comes from that profile's own
    ``compatibility.ini`` rather than from whichever firefox.exe is on PATH:
    two installs at two versions can share a machine (a Microsoft Store build
    beside a regular one), and a clearance from one is refused under the
    other's version.  Nothing here is cached, either -- Firefox updates
    underneath a running app, and a clearance obtained after the update is
    good only under the new version.  The ``application.ini`` beside the exe
    is the fallback when a profile does not say, and a recent version the
    fallback when nothing does."""
    major = None
    if profile_db is not None:
        major = _profile_firefox_major(profile_db)
    else:
        for db in _firefox_cookie_dbs():
            major = _profile_firefox_major(db)
            if major:
                break
    return _user_agent_for(major or _installed_firefox_major()
                           or _FALLBACK_MAJOR)


def firefox_available() -> bool:
    return _firefox_exe() is not None


def open_in_firefox(url: str) -> bool:
    """Open *url* in Firefox so the user can clear CloudFlare.  Returns False if
    Firefox couldn't be launched."""
    exe = _firefox_exe()
    if not exe:
        return False
    try:
        subprocess.Popen([exe, url])
        return True
    except Exception:
        return False


def open_in_browser(url: str) -> None:
    """Open *url* in the user's default browser (the no-Firefox fallback)."""
    try:
        webbrowser.open(url)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Chrome and Edge, where the GetCases extension is
# ---------------------------------------------------------------------------

#: The extension's name in its manifest: what it is known by in a profile.
EXTENSION_NAME = "GetCases Citation Links"


class _Chromium(NamedTuple):
    name: str               # as the reader knows it: "Chrome"
    data: Path              # where its profiles live ("User Data")
    exes: "list[str]"       # where the browser itself may be, or its command


def _chromium_browsers() -> "list[_Chromium]":
    """The Chromium browsers the extension can hand a clearance over from,
    on this system: Chrome, then Edge."""
    home = Path.home()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            return []
        roots = [os.environ.get(var, "") for var in
                 ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]

        def exes(*parts: str) -> "list[str]":
            return [os.path.join(root, *parts) for root in roots if root]

        return [
            _Chromium("Chrome", Path(local, "Google", "Chrome", "User Data"),
                      exes("Google", "Chrome", "Application", "chrome.exe")),
            _Chromium("Edge", Path(local, "Microsoft", "Edge", "User Data"),
                      exes("Microsoft", "Edge", "Application", "msedge.exe")),
        ]
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        return [
            _Chromium("Chrome", support / "Google" / "Chrome",
                      ["/Applications/Google Chrome.app/Contents/MacOS/"
                       "Google Chrome"]),
            _Chromium("Edge", support / "Microsoft Edge",
                      ["/Applications/Microsoft Edge.app/Contents/MacOS/"
                       "Microsoft Edge"]),
        ]
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return [
        _Chromium("Chrome", config / "google-chrome",
                  ["google-chrome", "google-chrome-stable"]),
        _Chromium("Edge", config / "microsoft-edge",
                  ["microsoft-edge", "microsoft-edge-stable"]),
    ]


def _first_exe(candidates: "list[str]") -> Optional[str]:
    for cand in candidates:
        if os.path.isabs(cand):
            if os.path.isfile(cand):
                return cand
        else:
            found = shutil.which(cand)
            if found:
                return found
    return None


def _is_extension_dir(folder: Path) -> bool:
    """Whether *folder* holds the GetCases extension (its manifest names it)."""
    try:
        manifest = json.loads(
            (folder / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("name") == EXTENSION_NAME


def _profile_has_extension(profile: Path) -> bool:
    """Whether the GetCases extension is installed, and not turned off, in
    the Chromium profile folder *profile*.  The profile's preferences are the
    browser's own record of its extensions: each entry's ``path`` is the
    folder it was loaded unpacked from (as the extension's README has it
    installed), or, packed, its folder under the profile's ``Extensions``."""
    for prefs in ("Secure Preferences", "Preferences"):
        try:
            settings = json.loads((profile / prefs).read_text(
                encoding="utf-8"))["extensions"]["settings"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not isinstance(settings, dict):
            continue
        for entry in settings.values():
            if not isinstance(entry, dict):
                continue
            manifest = entry.get("manifest")
            ours = (isinstance(manifest, dict)
                    and manifest.get("name") == EXTENSION_NAME)
            path = entry.get("path")
            if not ours and isinstance(path, str) and path:
                folder = Path(path)
                if not folder.is_absolute():
                    folder = profile / "Extensions" / folder
                ours = _is_extension_dir(folder)
            if not ours:
                continue
            # Turned off: a reason it is (a list now, a bit mask before), or
            # the old "state" 0.
            if entry.get("disable_reasons") or entry.get("state") == 0:
                continue
            return True
    return False


class ExtensionBrowser(NamedTuple):
    """A browser profile the GetCases extension is installed and on in."""

    name: str               # "Chrome", "Edge"
    exe: str                # the browser
    profile: str            # the profile's folder: "Default", "Profile 1"
    used: float             # when it was last used (its preferences' mtime)


#: (when looked, what was found): the profiles are read at most this often.
_EXTENSION_BROWSERS: Optional[tuple] = None
_EXTENSION_BROWSERS_TTL = 30.0


def extension_browsers() -> "list[ExtensionBrowser]":
    """The Chrome and Edge profiles the GetCases extension is installed and
    on in, most recently used first: where the reader can pass CloudFlare's
    check and have the extension hand the clearance to the app.  Read from
    the profiles' preferences, so known whether or not the browser is
    running; read afresh at most every half minute."""
    global _EXTENSION_BROWSERS
    cached = _EXTENSION_BROWSERS
    if cached is not None and time.monotonic() - cached[0] < _EXTENSION_BROWSERS_TTL:
        return list(cached[1])
    found: list[ExtensionBrowser] = []
    for browser in _chromium_browsers():
        exe = _first_exe(browser.exes)
        if not exe:
            continue
        try:
            profiles = [p for p in browser.data.iterdir()
                        if (p / "Preferences").is_file()]
        except OSError:
            continue
        for profile in profiles:
            if _profile_has_extension(profile):
                found.append(ExtensionBrowser(
                    browser.name, exe, profile.name,
                    _safe_mtime(profile / "Preferences")))
    found.sort(key=lambda b: b.used, reverse=True)
    _EXTENSION_BROWSERS = (time.monotonic(), found)
    return list(found)


def open_in_extension_browser(url: str, browser: ExtensionBrowser) -> bool:
    """Open *url* in *browser*, on the profile the extension is in, for the
    reader to pass CloudFlare's check there; the extension hands the
    clearance to the app once they have.  The browser is only asked to open
    a page, as a link opens one: CloudFlare's check refuses a browser under
    automation.  Returns False if it couldn't be launched."""
    try:
        subprocess.Popen([browser.exe, f"--profile-directory={browser.profile}",
                          url])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Optional dependencies (imported lazily so the app runs without them)
# ---------------------------------------------------------------------------

def _have(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def deps_present() -> bool:
    # curl_cffi does the TLS-impersonating fetch.  The Firefox clearance cookie
    # is read straight from cookies.sqlite (Firefox stores cookies unencrypted),
    # so browser_cookie3 is only an optional fallback now, not a hard requirement.
    return _have("curl_cffi")


def can_fetch() -> bool:
    """True when in-app fetching is possible: curl_cffi importable, and a
    clearance to be had -- Firefox installed, or the GetCases extension in
    Chrome or Edge, or a clearance the extension has handed over already.
    When False the caller links out instead."""
    return deps_present() and (firefox_available()
                               or bool(_browser_clearances())
                               or bool(extension_browsers()))


#: The target :func:`_impersonate_target` found, by browser family.
_IMPERSONATE_CACHE: dict = {}


def _impersonate_target(family: str = "firefox") -> str:
    """The newest TLS-fingerprint target curl_cffi offers for *family*
    ("firefox", or "chrome" -- Edge's too, whose TLS is Chrome's).  A
    browser's fingerprint barely moves between its versions, so the latest
    available matches a newer installed browser closely enough to pass
    CloudFlare."""
    if family in _IMPERSONATE_CACHE:
        return _IMPERSONATE_CACHE[family]
    target = family
    try:
        import typing
        from curl_cffi.requests.impersonate import BrowserTypeLiteral
        found = []
        for t in typing.get_args(BrowserTypeLiteral):
            m = re.fullmatch(rf"{family}(\d+)", t)
            if m:
                found.append((int(m.group(1)), t))
        if found:
            target = max(found)[1]
    except Exception:
        pass
    _IMPERSONATE_CACHE[family] = target
    return target


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _firefox_data_roots() -> "list[Path]":
    """Every directory a Firefox profile tree might live under -- far more
    thorough than browser_cookie3's single hard-coded location, which is why it
    raises "Could not find firefox profile directory" for these layouts:

      * Windows standard      %APPDATA%/%LOCALAPPDATA%\\Mozilla\\Firefox
      * Windows Microsoft Store %LOCALAPPDATA%\\Packages\\Mozilla.Firefox_*\\...
      * macOS                 ~/Library/Application Support/Firefox
      * Linux native / Snap / Flatpak
    """
    roots: list[Path] = []
    home = Path.home()
    if sys.platform == "win32":
        for env in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                roots.append(Path(base) / "Mozilla" / "Firefox")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            # Microsoft Store package keeps its own Roaming tree.
            roots += [Path(p) for p in glob.glob(os.path.join(
                local, "Packages", "Mozilla.Firefox_*", "LocalCache",
                "Roaming", "Mozilla", "Firefox"))]
    elif sys.platform == "darwin":
        roots.append(home / "Library" / "Application Support" / "Firefox")
    else:
        roots += [
            home / ".mozilla" / "firefox",
            home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
            home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
        ]
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = os.path.normcase(str(r))
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r)
    return out


def _profiles_ini_cookie_dbs(root: Path) -> "list[Path]":
    """cookies.sqlite for every profile named in *root*/profiles.ini -- the
    authoritative list, and the only way to find profiles stored outside the
    usual ``Profiles`` folder (a custom ``IsRelative=0`` path)."""
    ini = root / "profiles.ini"
    if not ini.is_file():
        return []
    cp = configparser.ConfigParser()
    try:
        cp.read(str(ini), encoding="utf-8")
    except Exception:
        return []
    out: list[Path] = []
    for sec in cp.sections():
        path = cp.get(sec, "Path", fallback="").strip()
        if not path:
            continue
        relative = cp.get(sec, "IsRelative", fallback="1").strip() != "0"
        prof = (root / path) if relative else Path(path)
        out.append(prof / "cookies.sqlite")
    return out


def _find_cookie_sqlites(base: Path, limit: int = 40) -> "list[Path]":
    """Recursively locate cookies.sqlite under *base*, pruning Firefox's large
    cache/storage/AppContainer subtrees so the walk stays fast.  Used for a
    Microsoft Store install, whose internal profile path varies by build."""
    skip = {
        "cache2", "startupcache", "storage", "thumbnails", "crashes",
        "datareporting", "minidumps", "saved-telemetry-pings", "gmp",
        "gmp-gmpopenh264", "ac", "temp", "tempstate", "inetcache",
        "settingsbackup", "doh-rollout", "weave",
    }
    found: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d.lower() not in skip]
            if "cookies.sqlite" in filenames:
                found.append(Path(dirpath) / "cookies.sqlite")
                if len(found) >= limit:
                    break
    except Exception:
        pass
    return found


def _windows_store_cookie_dbs() -> "list[Path]":
    """cookies.sqlite for a Microsoft Store Firefox (its firefox.EXE resolves to
    the WindowsApps alias).  The profile lives somewhere under
    ``%LOCALAPPDATA%\\Packages\\Mozilla.Firefox_*`` — redirected to a LocalCache
    subpath whose exact shape varies between builds — so search the package tree
    rather than guess the path."""
    if sys.platform != "win32":
        return []
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    pkgs = Path(local) / "Packages"
    out: list[Path] = []
    seen: set[str] = set()
    for pat in ("Mozilla.Firefox_*", "*Firefox*", "*Mozilla*"):
        for pkg in pkgs.glob(pat):
            key = os.path.normcase(str(pkg))
            if key in seen or not pkg.is_dir():
                continue
            seen.add(key)
            out += _find_cookie_sqlites(pkg)
    return out


def _firefox_cookie_dbs() -> "list[Path]":
    """All Firefox cookies.sqlite files we can find, most recently written
    first.  That is an order to scan them in, nothing more: SQLite touches a
    store for reasons that have nothing to do with the cookie, so which was
    written last says little about which clearance is good (see
    :func:`_firefox_clearances`)."""
    cands: list[Path] = []
    for root in _firefox_data_roots():
        cands += _profiles_ini_cookie_dbs(root)
        for pat in ("Profiles/*/cookies.sqlite", "*/cookies.sqlite",
                    "cookies.sqlite"):
            cands += [Path(p) for p in glob.glob(str(root / pat))]
    # Microsoft Store install: the profile is buried in the package tree.
    cands += _windows_store_cookie_dbs()
    dbs: list[Path] = []
    seen: set[str] = set()
    for db in cands:
        key = os.path.normcase(str(db))
        if key not in seen and db.is_file():
            seen.add(key)
            dbs.append(db)
    dbs.sort(key=_safe_mtime, reverse=True)
    return dbs


#: Above this, a ``moz_cookies.expiry`` cannot be seconds: as seconds it would
#: be the year 5138.  Firefox writes milliseconds now and wrote seconds before,
#: and no real cookie lands between the two magnitudes.
_MS_EXPIRY_FLOOR = 1e11


def _cookie_expiry_seconds(raw) -> float:
    """A ``moz_cookies.expiry`` as Unix seconds, whichever unit Firefox wrote.

    Returns 0.0 for a session cookie (no expiry) and for anything unreadable.
    """
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    return value / 1000.0 if value > _MS_EXPIRY_FLOOR else value


#: The columns Firefox stamps a row with, all in microseconds.  ``updateTime``
#: (newer schemas only) is when the cookie was last set; a server re-set keeps
#: the original ``creationTime``.
_STAMP_COLUMNS = ("updateTime", "lastAccessed", "creationTime")


def _micros_to_seconds(raw) -> float:
    try:
        return float(raw or 0) / 1_000_000.0
    except (TypeError, ValueError):
        return 0.0


def _read_cookie_rows(db: Path, domain_substr: str) -> dict:
    """``{name: (value, expiry, touched)}`` for hosts containing
    *domain_substr*, read straight from a Firefox cookies.sqlite; all times
    Unix seconds, ``expiry`` 0.0 for a session cookie.

    Firefox stores cookies unencrypted, so no browser_cookie3/decryption is
    needed.  The DB (and any -wal/-shm) is copied first so a running Firefox
    can't block the read and recent writes -- the just-obtained clearance --
    are visible.

    ``touched`` is the latest of the row's ``updateTime``/``lastAccessed``/
    ``creationTime`` -- when Firefox last set or sent the cookie.  A server
    re-set keeps the original ``creationTime``, so a ``cf_clearance`` reissued
    this afternoon can still carry a creation date from months ago; it is
    ``updateTime`` (or, on a schema without it, ``lastAccessed``) that dates
    the clearance the reader has just obtained.

    Expired rows are dropped, and where one host+name exists several times
    (Total Cookie Protection keeps a copy per partition) the most recently
    touched one wins, tie-broken by the furthest expiry: the clearance
    obtained minutes ago must not lose to a dead partitioned copy of the same
    name that happens to sort later.
    """
    import shutil
    import sqlite3
    import tempfile
    import time
    now = time.time()
    tmpdir = tempfile.mkdtemp(prefix="engr_ff_")
    try:
        tmp = os.path.join(tmpdir, "cookies.sqlite")
        shutil.copyfile(str(db), tmp)
        for ext in ("-wal", "-shm"):
            side = Path(str(db) + ext)
            if side.is_file():
                try:
                    shutil.copyfile(str(side), tmp + ext)
                except OSError:
                    pass
        con = sqlite3.connect(tmp)
        try:
            have = {row[1] for row in con.execute("PRAGMA table_info(moz_cookies)")}
            stamps = [c for c in _STAMP_COLUMNS if c in have]
            rows = con.execute(
                "SELECT name, value, expiry"
                + "".join(f", {c}" for c in stamps)
                + " FROM moz_cookies WHERE host LIKE ?",
                (f"%{domain_substr}%",),
            ).fetchall()
        finally:
            con.close()
    except Exception as exc:
        print(f"[eng_rep_pdf] reading {db} failed: {exc}")
        return {}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    best: dict = {}
    for name, value, expiry, *when in rows:
        exp = _cookie_expiry_seconds(expiry)
        if exp and exp <= now:
            continue                       # dead; sending it re-challenges us
        touched = max((_micros_to_seconds(w) for w in when), default=0.0)
        if name not in best or (touched, exp) > (best[name][2], best[name][1]):
            best[name] = (value, exp, touched)
    return best


def _read_cookies_sqlite(db: Path, domain_substr: str) -> dict:
    """{name: value} for hosts containing *domain_substr* (see
    :func:`_read_cookie_rows`, which does the work and the winnowing)."""
    return {name: row[0] for name, row in
            _read_cookie_rows(db, domain_substr).items()}


class Clearance(NamedTuple):
    """One browser's clearance for a site (CommonLII), with everything that
    has to travel with it: the browser's whole cookie set for the site and
    the User-Agent it sent them under -- a Firefox profile's, or what the
    GetCases extension handed over from Chrome or Edge, which brings the
    rest of that browser's headers too."""

    db: Optional[Path]      # the profile's cookies.sqlite (None: not read from one)
    cookies: dict           # every live cookie of that profile for the site
    obtained: float         # when cf_clearance was last set or sent, Unix seconds
    expiry: float           # its stated expiry -- a floor, not a promise
    user_agent: str         # what the clearance must be sent under
    #: The browser's other headers that go with it (client hints, its
    #: languages), from Chrome or Edge; None for Firefox's.
    headers: Optional[dict] = None
    #: The browser it was obtained in, as the reader knows it.
    browser: str = "Firefox"

    @property
    def value(self) -> str:
        return self.cookies.get("cf_clearance", "")

    @property
    def origin(self) -> str:
        """Where it came from, for the log."""
        if self.db is not None:
            return str(self.db)
        if self.headers is None:
            return "browser_cookie3"
        return f"{self.browser} (the GetCases extension)"


#: The cookie host of the site CommonLII's English Reports come from --
#: the default everywhere a site is asked for.
COMMONLII = "commonlii"


def _browser_cookie3_clearance(site: str = COMMONLII) -> Optional[Clearance]:
    """Last-ditch: browser_cookie3's own Firefox reader, for any layout the
    direct search misses.  Its expiry check cannot be trusted on a modern
    profile -- it reads ``expiry`` as seconds, so every cookie looks live to
    it -- and it cannot say which profile a cookie came from, so this gets
    the best-guess UA and goes last."""
    try:
        import browser_cookie3
        cj = browser_cookie3.firefox(domain_name=site)
        cookies = {c.name: c.value for c in cj if site in (c.domain or "")}
    except Exception as exc:
        print(f"[eng_rep_pdf] browser_cookie3 fallback failed: {exc}")
        return None
    if "cf_clearance" not in cookies:
        return None
    return Clearance(None, cookies, 0.0, 0.0, firefox_user_agent())


def _firefox_clearances(site: str = COMMONLII) -> "list[Clearance]":
    """Every Firefox profile's live-looking clearance for *site* (a cookie
    host, as "commonlii"), freshest first -- the order
    :func:`fetch_cleared` tries them in.

    Searches every known Firefox profile location and reads cookies.sqlite
    directly -- so a Microsoft Store install or a non-default profile path,
    which trip browser_cookie3's "Could not find firefox profile directory",
    are handled.

    Each candidate carries its own profile's whole cookie set (``cf_clearance``
    and the ``__cf_bm`` beside it are issued together and only accepted
    together, so one profile's cookies are never mixed with another's) and
    the UA of the Firefox that runs that profile, since CloudFlare refuses a
    clearance sent under any other version.  Ordering is by when the
    clearance was last set or sent, not by which store was written last: a
    machine running a Microsoft Store Firefox beside a regular one holds a
    clearance in each, and file mtime says nothing about which the user just
    solved the check in.

    "Live-looking" is as far as the store can take it.  CloudFlare stops
    honouring a clearance long before its stated expiry, so the list is a set
    of candidates, and only the fetch can tell which of them is good.
    """
    out: list[Clearance] = []
    dbs = _firefox_cookie_dbs()
    for db in dbs:
        rows = _read_cookie_rows(db, site)
        clearance = rows.get("cf_clearance")
        if clearance is None:
            continue
        out.append(Clearance(
            db, {name: row[0] for name, row in rows.items()},
            clearance[2], clearance[1], firefox_user_agent(db)))
    out.sort(key=lambda c: (c.obtained, c.expiry), reverse=True)
    if not dbs:
        print("[eng_rep_pdf] no Firefox cookies.sqlite found in: "
              + ", ".join(str(r) for r in _firefox_data_roots()))
    if not out:
        extra = _browser_cookie3_clearance(site)
        if extra is not None:
            out.append(extra)
    return out


# ---------------------------------------------------------------------------
# Clearances the GetCases extension hands over from Chrome or Edge
# ---------------------------------------------------------------------------

#: The sites the extension may hand a clearance over for: the domain a
#: page's address is in, to the cookie host it is filed under here.
_EXTENSION_SITES = {"commonlii.org": COMMONLII}

#: The headers a clearance from Chrome or Edge is sent under, as the browser
#: sent them.  CloudFlare holds a clearance to the browser it issued it to;
#: curl_cffi's own client hints name another version and another platform.
_BROWSER_HEADERS = ("user-agent", "sec-ch-ua", "sec-ch-ua-mobile",
                    "sec-ch-ua-platform", "accept-language")

_BROWSER_LOCK = threading.Lock()
#: What the extension has handed over, by site; None until read from disk.
_BROWSER_CLEARANCES: Optional[dict] = None
#: cf_clearance values CloudFlare has refused from the app.  The extension
#: sends the browser's clearance again each time a page of the site loads
#: there, and a dead one is no news.
_BROWSER_REFUSED: set = set()


def _browser_store() -> Path:
    """Where the extension's clearances are kept between runs: beside the
    scans' cache."""
    return CACHE_DIR.parent / "browser_clearances.json"


def _held_browser_clearances() -> dict:
    """The extension's clearances, by site, read from disk the first time;
    expired ones dropped.  Called with _BROWSER_LOCK held."""
    global _BROWSER_CLEARANCES
    if _BROWSER_CLEARANCES is None:
        _BROWSER_CLEARANCES = {}
        try:
            kept = json.loads(_browser_store().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            kept = {}
        for site, entry in (kept.items() if isinstance(kept, dict) else ()):
            try:
                _BROWSER_CLEARANCES[site] = Clearance(
                    None, dict(entry["cookies"]), float(entry["obtained"]),
                    float(entry["expiry"]), str(entry["user_agent"]),
                    headers=dict(entry["headers"]),
                    browser=str(entry["browser"]))
            except (KeyError, TypeError, ValueError):
                continue
    now = time.time()
    for site, held in list(_BROWSER_CLEARANCES.items()):
        if held.expiry and held.expiry < now:
            del _BROWSER_CLEARANCES[site]
    return _BROWSER_CLEARANCES


def _save_browser_clearances() -> None:
    """Write the extension's clearances to disk.  With _BROWSER_LOCK held."""
    kept = {site: {"cookies": c.cookies, "obtained": c.obtained,
                   "expiry": c.expiry, "user_agent": c.user_agent,
                   "headers": c.headers, "browser": c.browser}
            for site, c in (_BROWSER_CLEARANCES or {}).items()}
    path = _browser_store()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.part")
        tmp.write_text(json.dumps(kept, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        print(f"[eng_rep_pdf] could not keep the browser's clearance: {exc}")


def accept_browser_clearance(data: dict) -> bool:
    """Take what the GetCases extension hands over when a page of a site
    behind CloudFlare has loaded past the check in Chrome or Edge (through
    :mod:`browser_bridge`'s ``/clearance``)::

        {"url": the page, "browser": "Chrome",
         "cookies": [{"name", "value", "expires"}, ...],
         "headers": {"user-agent": ..., "sec-ch-ua": ..., ...}}

    Returns whether it was taken: not for a site the app fetches nothing
    from, nor without a ``cf_clearance`` and the User-Agent it went out
    under, nor a clearance CloudFlare has already refused from the app."""
    try:
        host = (urllib.parse.urlsplit(str(data.get("url") or "")).hostname
                or "").lower()
    except ValueError:
        return False
    site = next((s for domain, s in _EXTENSION_SITES.items()
                 if host == domain or host.endswith("." + domain)), None)
    if site is None:
        return False
    cookies, expiry = {}, 0.0
    for cookie in data.get("cookies") or ():
        if not isinstance(cookie, dict):
            continue
        name, value = cookie.get("name"), cookie.get("value")
        if not (isinstance(name, str) and name and isinstance(value, str)):
            continue
        cookies[name] = value
        if name == "cf_clearance":
            try:
                expiry = float(cookie.get("expires") or 0.0)
            except (TypeError, ValueError):
                expiry = 0.0
    sent = data.get("headers")
    headers = {k.lower(): v for k, v in (sent.items() if isinstance(
                   sent, dict) else ())
               if isinstance(k, str) and isinstance(v, str)
               and k.lower() in _BROWSER_HEADERS}
    agent = headers.pop("user-agent", "")
    value = cookies.get("cf_clearance")
    if not value or not agent:
        return False
    browser = str(data.get("browser") or "").strip() or "Chrome"
    with _BROWSER_LOCK:
        if value in _BROWSER_REFUSED:
            return False
        held = _held_browser_clearances()
        before = held.get(site)
        held[site] = Clearance(None, cookies, time.time(), expiry, agent,
                               headers=headers, browser=browser)
        _save_browser_clearances()
    if before is None or before.value != value:
        print(f"[eng_rep_pdf] {browser} handed over a {site} clearance")
    return True


def _browser_clearances(site: str = COMMONLII) -> "list[Clearance]":
    """The clearance the extension handed over for *site*, if it holds one."""
    with _BROWSER_LOCK:
        held = _held_browser_clearances().get(site)
    return [held] if held is not None else []


def _drop_browser_clearance(site: str, value: str) -> None:
    """CloudFlare refused the extension's clearance *value* for *site*: let
    it go, and don't take it again."""
    with _BROWSER_LOCK:
        _BROWSER_REFUSED.add(value)
        held = _held_browser_clearances()
        if site in held and held[site].value == value:
            del held[site]
            _save_browser_clearances()


def _clearances(site: str = COMMONLII) -> "list[Clearance]":
    """Every clearance the app can send for *site* -- what the extension
    handed over from Chrome or Edge, and each Firefox profile's --
    freshest first, the order :func:`fetch_cleared` tries them in.
    Firefox's stores are searched only where Firefox is."""
    out = _browser_clearances(site)
    if firefox_available():
        out += _firefox_clearances(site)
    out.sort(key=lambda c: (c.obtained, c.expiry), reverse=True)
    return out


def clearance_mark(site: str = COMMONLII) -> tuple:
    """The clearances the app holds for *site* now — each Firefox profile's,
    and what the extension handed over — as a token that changes the moment
    the reader passes CloudFlare's check in Firefox, or in Chrome or Edge
    with the extension, so the viewer waiting on it can try again without
    being told to.

    A clearance newly issued has a new value; when it was last *used* is
    left out, since Firefox moves that every time it sends the cookie, and
    a reader still reading the site in Firefox would look like one passing
    the check again and again."""
    return tuple(sorted((c.origin, c.value) for c in _clearances(site)))


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def cache_path(year: int, num: int) -> Path:
    return CACHE_DIR / f"{year}-{num}.pdf"


def get_cached(year: int, num: int) -> Optional[bytes]:
    p = cache_path(year, num)
    try:
        if p.is_file() and p.stat().st_size > 0:
            data = p.read_bytes()
            if data[:4] == b"%PDF":
                return data
    except Exception:
        pass
    return None


def _store(year: int, num: int, data: bytes) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cache_path(year, num).with_suffix(".pdf.part")
        tmp.write_bytes(data)
        tmp.replace(cache_path(year, num))
    except Exception as exc:
        print(f"[eng_rep_pdf] cache write failed: {exc}")


def is_cached(year: int, num: int) -> bool:
    return get_cached(year, num) is not None


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

#: What marks CloudFlare's check page where a response lacks the
#: ``cf-mitigated`` header.  Not CloudFlare's ``/cdn-cgi/challenge-platform/``
#: script: CloudFlare puts that into every page it serves, the page saying a
#: request is blocked included.
_CHALLENGE_MARKERS = (b"Just a moment", b"cf-browser-verification",
                      b"__cf_chl")

#: The (profile, clearance value) CloudFlare accepted last for each site,
#: tried first next time.  Harmless when wrong -- it is just tried first and,
#: refused, the others follow -- and it keeps a refused-but-unexpired
#: clearance in another profile from costing a round trip on every case.
_LAST_GOOD: Optional[dict] = None


def _is_challenge(status: int, body: bytes, cf_mitigated: str = "") -> bool:
    """Whether a response is CloudFlare's check rather than the page asked
    for.  CloudFlare says so in the ``cf-mitigated: challenge`` header of
    every check it serves; the check page's own markers are the fallback.
    A 403 with neither is a refusal, not a check."""
    if cf_mitigated.strip().lower() == "challenge":
        return True
    return status in (403, 503) and any(mk in body for mk in _CHALLENGE_MARKERS)


def _get(url: str, headers: dict, cookies: dict,
         impersonate: str = "") -> "tuple[int, bytes, str]":
    """One GET through curl_cffi under a browser's TLS fingerprint --
    *impersonate*, Firefox's by default: ``(status, body, cf-mitigated
    header)``."""
    from curl_cffi import requests as creq
    resp = creq.get(url, headers=headers, cookies=cookies,
                    impersonate=impersonate or _impersonate_target(),
                    timeout=_TIMEOUT)
    return (resp.status_code, resp.content or b"",
            resp.headers.get("cf-mitigated") or "")


#: What Firefox asks for when it follows a link.
_NAV_ACCEPT = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8")


def _headers_for(cand: Clearance, referer: str) -> dict:
    """The headers *cand* goes out with: its browser's User-Agent, and the
    Referer the origin wants.  A clearance from Chrome or Edge brings the
    browser's client hints and languages besides, over curl_cffi's own
    (which name another version, and macOS), and says it was asked for
    from the case's page, as a click there asks."""
    if cand.headers is None:
        return {
            "User-Agent": cand.user_agent,
            "Accept": _NAV_ACCEPT,
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": referer,
        }
    return {**cand.headers, "User-Agent": cand.user_agent,
            "Referer": referer, "Sec-Fetch-Site": "same-origin"}


def _agent_brief(user_agent: str) -> str:
    """The browser and version a User-Agent names, for the log."""
    m = (re.search(r"\b(?:Firefox|Edg)/[\d.]+", user_agent)
         or re.search(r"\bChrome/[\d.]+", user_agent))
    return m.group(0) if m else user_agent.rsplit(" ", 1)[-1]


def _is_pdf(data: bytes) -> bool:
    return data[:4] == b"%PDF"


def fetch_cleared(url: str, site: str, *, referer: str, web_url: str) -> bytes:
    """GET the PDF *url* from a site behind CloudFlare under a clearance the
    app holds for it -- *site* is the cookie host ("commonlii").

    Raises :class:`FetchUnavailable` when in-app fetching isn't possible (the
    caller links out), :class:`CloudflareChallenge` carrying *web_url* -- the
    page for the reader to pass the check on -- when the user must clear the
    check in a browser, or :class:`OriginError` when the site refused the
    request without a check -- the origin's own error, or a CloudFlare rule
    that blocks rather than checks.

    Every Firefox profile holding a live-looking clearance is a candidate,
    and so is what the GetCases extension handed over from Chrome or Edge,
    tried freshest first, each with its own cookies under its own browser's
    User-Agent (and, from Chrome or Edge, its client hints, under Chrome's
    TLS fingerprint).  A candidate CloudFlare refuses is simply passed over
    -- its cookie's expiry said nothing, CloudFlare had already dropped it
    -- and only when every candidate is refused is the reader sent to pass
    the check again.  That is what keeps a dead clearance in one profile
    (the regular Firefox's, say) from hiding the one just obtained in
    another (the Microsoft Store build's, or Chrome's), whichever the
    timestamps happen to favour.
    """
    global _LAST_GOOD
    if not can_fetch():
        raise FetchUnavailable()

    candidates = _clearances(site)
    if not candidates:
        # No clearance anywhere -- the user has to pass the check.
        raise CloudflareChallenge(web_url)
    last = (_LAST_GOOD or {}).get(site)
    if last is not None:
        # Stable sort: the one that worked last goes first, the rest keep
        # their freshest-first order.
        candidates.sort(key=lambda c: (c.origin, c.value) != last)

    for cand in candidates:
        family = "firefox" if cand.headers is None else "chrome"
        try:
            status, data, cf_mitigated = _get(
                url, _headers_for(cand, referer), cand.cookies,
                _impersonate_target(family))
        except Exception as exc:
            raise OriginError(0) from exc

        if status == 200 and _is_pdf(data):
            _LAST_GOOD = {**(_LAST_GOOD or {}),
                          site: (cand.origin, cand.value)}
            return data

        if _is_challenge(status, data, cf_mitigated):
            # CloudFlare no longer honours this clearance (dropped early, or
            # obtained by a browser since updated) -- on to the next one.
            print(f"[eng_rep_pdf] CloudFlare refused the {site} clearance "
                  f"from {cand.origin} "
                  f"(sent as {_agent_brief(cand.user_agent)})")
            if cand.headers is not None:
                _drop_browser_clearance(site, cand.value)
            continue
        raise OriginError(status)

    # Every clearance refused: cookie expired server-side, or fingerprint
    # mismatch -> the user must pass the check again.
    raise CloudflareChallenge(web_url, candidates[0].user_agent,
                              candidates[0].browser)


def fetch_pdf(year: int, num: int, web_url: str) -> bytes:
    """Return the PDF bytes for a CommonLII case, from cache or the network.

    Raises :class:`FetchUnavailable` when in-app fetching isn't possible (the
    caller links out), :class:`CloudflareChallenge` when the user must clear the
    check in a browser, or :class:`OriginError` on an origin HTTP error (see
    :func:`fetch_cleared`).  Successful fetches are cached.
    """
    cached = get_cached(year, num)
    if cached is not None:
        return cached
    pdf_url = re.sub(r"\.html?$", ".pdf", web_url)
    # The Referer is the case's page: the origin Apache hotlink-blocks PDFs
    # requested without it.
    data = fetch_cleared(pdf_url, COMMONLII, referer=web_url, web_url=web_url)
    _store(year, num, data)
    return data


# ---------------------------------------------------------------------------
# Pin pages
# ---------------------------------------------------------------------------

# The reprint marks where each page of the original report begins with its
# number in brackets, "[354]", which the scans' text layer mostly keeps.
_NOMINATE_PAGE_MARK_RE = re.compile(r"\[\s*(\d{1,5})\s*\]")

#: How far from the pin a bracketed number may be and still place it: a
#: year in square brackets ("[1896]") or a stray is well beyond this.
_MARK_REACH = 10


def pin_page(data: bytes, start_page: int, pin: str,
             nominate: bool = False) -> Optional[int]:
    """The page of a case's scan (0-based) a pin cite names, or None.

    A CommonLII scan runs from the case's first page in the reprint to the
    page where the next case begins, one PDF page to a page, so a pin to the
    English Reports ("156 Eng. Rep. 145, 151") is its distance from the
    first page.  A pin to the original report ("9 Exch. 341, 354") is found
    by the reprint's "[354]" for that page — or, where the text layer lost
    it, placed between the nearest marks either side."""
    m = re.match(r"\d+", str(pin or ""))
    if not m or not data:
        return None
    wanted = int(m.group(0))
    try:
        import pypdfium2 as pdfium
        from pdfium_lock import PDFIUM_LOCK
        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(data)
            count = len(doc)
    except Exception as exc:
        print(f"[eng_rep_pdf] could not read the scan for its pin: {exc}")
        return None
    try:
        if not nominate:
            page = wanted - int(start_page)
            return page if 0 <= page < count else None
        marks: list[tuple[int, int]] = []   # (original page, scan page)
        for i in range(count):
            with PDFIUM_LOCK:
                text = doc[i].get_textpage().get_text_range()
            marks.extend((int(n), i)
                         for n in _NOMINATE_PAGE_MARK_RE.findall(text))
    except Exception as exc:
        print(f"[eng_rep_pdf] reading the scan's text failed: {exc}")
        return None
    finally:
        with PDFIUM_LOCK:
            doc.close()
    exact = [i for n, i in marks if n == wanted]
    if exact:
        return min(exact)
    below = max(((n, i) for n, i in marks
                 if wanted - _MARK_REACH <= n < wanted), default=None)
    above = min(((n, i) for n, i in marks
                 if wanted < n <= wanted + _MARK_REACH), default=None)
    if below and above:
        (bn, bi), (an, ai) = below, above
        return bi + round((wanted - bn) / (an - bn) * (ai - bi))
    return below[1] if below else None


# ---------------------------------------------------------------------------
# Live test:  python -X utf8 eng_rep_pdf.py [--fresh] [--open]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import datetime as _dt

    def _when(ts: float) -> str:
        return f"{_dt.datetime.fromtimestamp(ts):%Y-%m-%d %H:%M}" if ts else "-"

    print("firefox exe       :", _firefox_exe())
    print("firefox UA        :", firefox_user_agent(), "(freshest profile)")
    print("deps present      :", deps_present())
    print("can_fetch         :", can_fetch())
    print("impersonate target:", _impersonate_target(), "(Firefox's),",
          _impersonate_target("chrome"), "(Chrome's and Edge's)")
    print("cache dir         :", CACHE_DIR)

    # Profile search diagnostics: shows where we looked and whether the
    # clearance cookie is actually on disk (the two distinct failure modes are
    # "no profile/cookie found" vs "found but refused at fetch").
    print("\nfirefox profile search:")
    print("  data roots      :",
          [str(r) for r in _firefox_data_roots()] or "(none)")
    dbs = _firefox_cookie_dbs()
    if not dbs:
        print("  cookies.sqlite  : (none found)")
    for db in dbs:
        rows = _read_cookie_rows(db, "commonlii")
        clearance = rows.get("cf_clearance")
        if clearance is None:
            flag = "cf_clearance=no (or expired)"
        else:
            flag = (f"cf_clearance=YES obtained {_when(clearance[2])}, "
                    + (f"expires {_when(clearance[1])}" if clearance[1]
                       else "session"))
        print(f"  - {db}\n      runs under {firefox_user_agent(db).rsplit(' ', 1)[-1]}"
              f"; live commonlii cookies {sorted(rows)}  {flag}")
    # The other route: Chrome or Edge with the GetCases extension, which
    # hands the clearance over through the app's browser bridge.
    print("\nchrome/edge with the GetCases extension:")
    browsers = extension_browsers()
    print("  installed in    :", ", ".join(
        f"{b.name} (profile {b.profile!r}, used {_when(b.used)})"
        for b in browsers) or "(none)")
    for c in _browser_clearances():
        print(f"  handed over     : {c.browser}'s clearance, "
              f"{_when(c.obtained)}, as {_agent_brief(c.user_agent)}; "
              f"cookies {sorted(c.cookies)}")

    cands = _clearances()
    print("\nwill try          :", "none" if not cands
          else f"{len(cands)} clearance(s), in this order:")
    for i, c in enumerate(cands, 1):
        print(f"    {i}. {c.origin}  as {_agent_brief(c.user_agent)}  "
              f"cookies {sorted(c.cookies)}")

    # Hadley v Baxendale, 156 E.R. 145  ->  [1854] EngR 296
    year, num = 1854, 296
    web = f"https://www.commonlii.org/uk/cases/EngR/{year}/{num}.html"
    if "--fresh" in sys.argv:
        try:
            cache_path(year, num).unlink()
        except OSError:
            pass
    print(f"\nfetching {year}/{num} (Hadley v Baxendale)"
          + (" from the cache -- pass --fresh to go to the network..."
             if is_cached(year, num) else " from CommonLII..."))
    try:
        data = fetch_pdf(year, num, web)
        print(f"  OK: {len(data):,} bytes, head={data[:8]!r}")
        print(f"  cached at: {cache_path(year, num)} "
              f"(exists={cache_path(year, num).exists()})")
        if (_LAST_GOOD or {}).get(COMMONLII):
            print(f"  accepted: the clearance from {_LAST_GOOD[COMMONLII][0]}")
        # second call should be served from cache
        again = fetch_pdf(year, num, web)
        print(f"  cache hit on 2nd call: {again == data}")
    except CloudflareChallenge as exc:
        print(f"  needs CloudFlare clearance in a browser: {exc.web_url}")
        if exc.refused_by:
            print(f"  ({exc.refused_by}'s clearance was refused)")
        if "--open" in sys.argv:
            if browsers and open_in_extension_browser(exc.web_url, browsers[0]):
                print(f"  opened {browsers[0].name} -- solve the check there; "
                      "the extension hands the clearance to a running "
                      "GetCases (not to this script).")
            elif open_in_firefox(exc.web_url):
                print("  opened Firefox -- solve the check and re-run.")
    except FetchUnavailable:
        print("  in-app fetch unavailable (needs curl_cffi, and Firefox or "
              "the GetCases extension in Chrome or Edge); would link out.")
    except OriginError as exc:
        print(f"  origin error: {exc}")
