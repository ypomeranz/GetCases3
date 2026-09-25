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

All of this is optional: ``curl_cffi`` and Firefox may be absent, in which case
:func:`can_fetch` is False and the caller falls back to simply opening the
citation in the user's browser.

No tkinter here -- the GUI drives the user-facing hand-off/retry; this module is
the headless fetch+cache engine.  Run ``python -X utf8 eng_rep_pdf.py`` to see
every profile, its clearance and the UA it will be sent under, and to fetch a
sample PDF live (``--fresh`` drops the sample from the cache first so the
network path is really exercised).
"""

from __future__ import annotations

import configparser
import glob
import os
import re
import subprocess
import sys
import threading
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
    """In-app fetching isn't possible here (Firefox or a dependency missing);
    the caller should just open the citation in the browser."""


class CloudflareChallenge(Exception):
    """CommonLII served a CloudFlare challenge -- the user must clear it in
    Firefox.  ``web_url`` is the page to open for them to do so."""

    def __init__(self, web_url: str):
        super().__init__("CloudFlare challenge")
        self.web_url = web_url


class OriginError(Exception):
    """CloudFlare was passed but the origin returned an error (status code)."""

    def __init__(self, status: int):
        super().__init__(f"origin returned HTTP {status}")
        self.status = status


# ---------------------------------------------------------------------------
# Firefox discovery + User-Agent
# ---------------------------------------------------------------------------

def _firefox_exe() -> Optional[str]:
    """Path to firefox.exe / firefox, or None when Firefox isn't installed."""
    import shutil
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
    """True when in-app fetching is possible: Firefox installed and both
    optional packages importable.  When False the caller links out instead."""
    return firefox_available() and deps_present()


_IMPERSONATE_CACHE: Optional[str] = None


def _impersonate_target() -> str:
    """The newest Firefox TLS-fingerprint target curl_cffi offers (its JA3 is
    stable across Firefox versions, so the latest available matches a newer
    installed Firefox closely enough to pass CloudFlare)."""
    global _IMPERSONATE_CACHE
    if _IMPERSONATE_CACHE:
        return _IMPERSONATE_CACHE
    target = "firefox"
    try:
        import typing
        from curl_cffi.requests.impersonate import BrowserTypeLiteral
        ffs = []
        for t in typing.get_args(BrowserTypeLiteral):
            m = re.fullmatch(r"firefox(\d+)", t)
            if m:
                ffs.append((int(m.group(1)), t))
        if ffs:
            target = max(ffs)[1]
    except Exception:
        pass
    _IMPERSONATE_CACHE = target
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
    """One Firefox profile's CommonLII clearance, with everything that has to
    travel with it: the profile's whole cookie set and the User-Agent of the
    Firefox that runs that profile."""

    db: Optional[Path]      # the profile's cookies.sqlite (None: browser_cookie3)
    cookies: dict           # every live commonlii cookie of that profile
    obtained: float         # when cf_clearance was last set or sent, Unix seconds
    expiry: float           # its stated expiry -- a floor, not a promise
    user_agent: str         # what the clearance must be sent under

    @property
    def value(self) -> str:
        return self.cookies.get("cf_clearance", "")


def _browser_cookie3_clearance() -> Optional[Clearance]:
    """Last-ditch: browser_cookie3's own Firefox reader, for any layout the
    direct search misses.  Its expiry check cannot be trusted on a modern
    profile -- it reads ``expiry`` as seconds, so every cookie looks live to
    it -- and it cannot say which profile a cookie came from, so this gets
    the best-guess UA and goes last."""
    try:
        import browser_cookie3
        cj = browser_cookie3.firefox(domain_name="commonlii.org")
        cookies = {c.name: c.value for c in cj if "commonlii" in (c.domain or "")}
    except Exception as exc:
        print(f"[eng_rep_pdf] browser_cookie3 fallback failed: {exc}")
        return None
    if "cf_clearance" not in cookies:
        return None
    return Clearance(None, cookies, 0.0, 0.0, firefox_user_agent())


def _firefox_clearances() -> "list[Clearance]":
    """Every Firefox profile's live-looking CommonLII clearance, freshest
    first -- the order :func:`fetch_pdf` tries them in.

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
        rows = _read_cookie_rows(db, "commonlii")
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
        extra = _browser_cookie3_clearance()
        if extra is not None:
            out.append(extra)
    return out


def clearance_mark() -> tuple:
    """The CommonLII clearances Firefox holds now — each profile's, with
    when it was set — as a token that changes the moment the reader passes
    CloudFlare's check in Firefox, so the viewer waiting on it can try again
    without being told to."""
    return tuple(sorted(
        (str(c.db), c.value, c.obtained) for c in _firefox_clearances()))


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

_CHALLENGE_MARKERS = (b"Just a moment", b"challenge-platform",
                      b"cf-browser-verification", b"__cf_chl")

#: The (profile, clearance value) CloudFlare accepted last, tried first next
#: time.  Harmless when wrong -- it is just tried first and, refused, the
#: others follow -- and it keeps a refused-but-unexpired clearance in another
#: profile from costing a round trip on every case.
_LAST_GOOD: Optional[tuple] = None


def _is_challenge(status: int, body: bytes) -> bool:
    return status in (403, 503) and any(mk in body for mk in _CHALLENGE_MARKERS)


def _get(url: str, headers: dict, cookies: dict) -> "tuple[int, bytes]":
    """One GET through curl_cffi under Firefox's TLS fingerprint:
    ``(status, body)``."""
    from curl_cffi import requests as creq
    resp = creq.get(url, headers=headers, cookies=cookies,
                    impersonate=_impersonate_target(), timeout=_TIMEOUT)
    return resp.status_code, resp.content or b""


def fetch_pdf(year: int, num: int, web_url: str) -> bytes:
    """Return the PDF bytes for a CommonLII case, from cache or the network.

    Raises :class:`FetchUnavailable` when in-app fetching isn't possible (the
    caller links out), :class:`CloudflareChallenge` when the user must clear the
    check in Firefox, or :class:`OriginError` on an origin HTTP error.
    Successful fetches are cached.

    Every Firefox profile holding a live-looking clearance is a candidate,
    tried freshest first with its own cookies under its own Firefox's
    User-Agent.  A candidate CloudFlare refuses is simply passed over --
    its cookie's expiry said nothing, CloudFlare had already dropped it --
    and only when every candidate is refused is the reader sent to Firefox.
    That is what keeps a dead clearance in one profile (the regular Firefox's,
    say) from hiding the one just obtained in another (the Microsoft Store
    build's), whichever the store's timestamps happen to favour.
    """
    global _LAST_GOOD
    cached = get_cached(year, num)
    if cached is not None:
        return cached

    if not can_fetch():
        raise FetchUnavailable()

    candidates = _firefox_clearances()
    if not candidates:
        # No clearance anywhere -- the user has to pass the check in Firefox.
        raise CloudflareChallenge(web_url)
    if _LAST_GOOD is not None:
        # Stable sort: the one that worked last goes first, the rest keep
        # their freshest-first order.
        candidates.sort(key=lambda c: (str(c.db), c.value) != _LAST_GOOD)

    pdf_url = re.sub(r"\.html?$", ".pdf", web_url)
    for cand in candidates:
        headers = {
            "User-Agent": cand.user_agent,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": web_url,  # origin Apache hotlink-blocks PDFs without this
        }
        try:
            status, data = _get(pdf_url, headers, cand.cookies)
        except Exception as exc:
            raise OriginError(0) from exc

        if status == 200 and data[:4] == b"%PDF":
            _LAST_GOOD = (str(cand.db), cand.value)
            _store(year, num, data)
            return data

        if _is_challenge(status, data):
            # CloudFlare no longer honours this clearance (dropped early, or
            # obtained by a Firefox since updated) -- on to the next profile.
            print(f"[eng_rep_pdf] CloudFlare refused the clearance from "
                  f"{cand.db or 'browser_cookie3'} "
                  f"(sent as {cand.user_agent.rsplit(' ', 1)[-1]})")
            continue
        raise OriginError(status)

    # Every clearance refused: cookie expired server-side, or fingerprint
    # mismatch -> the user must re-clear in Firefox.
    raise CloudflareChallenge(web_url)


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
    print("impersonate target:", _impersonate_target())
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
    cands = _firefox_clearances()
    print("  will try        :", "none" if not cands
          else f"{len(cands)} clearance(s), in this order:")
    for i, c in enumerate(cands, 1):
        print(f"    {i}. {c.db or 'browser_cookie3'}  as "
              f"{c.user_agent.rsplit(' ', 1)[-1]}  cookies {sorted(c.cookies)}")

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
        if _LAST_GOOD:
            print(f"  accepted: the clearance from {_LAST_GOOD[0]}")
        # second call should be served from cache
        again = fetch_pdf(year, num, web)
        print(f"  cache hit on 2nd call: {again == data}")
    except CloudflareChallenge as exc:
        print(f"  needs CloudFlare clearance in Firefox: {exc.web_url}")
        if "--open" in sys.argv:
            open_in_firefox(exc.web_url)
            print("  opened Firefox -- solve the check and re-run.")
    except FetchUnavailable:
        print("  in-app fetch unavailable (needs Firefox + curl_cffi); "
              "would link out.")
    except OriginError as exc:
        print(f"  origin error: {exc}")
