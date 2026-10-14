"""Citation scans in a helper process.

Reading an opinion's citations (:func:`citations.detect_links`) is half a
second to a second of pure Python, much of it inside the regex engine, which
holds the GIL from the start of a search to its end.  On a worker thread it
starved the Tk thread: every Tk call the window made gave the GIL up and then
had to win it back from the scan, so the opinion stalled under the scroll
wheel for a tenth of a second at a time while its citations were read.  Here,
in a process of its own, the scan takes nothing from the window's interpreter:
the thread that asked for it waits on a pipe, and waiting on a pipe holds no
GIL.

The helper is a second copy of this program — the packaged .exe has no other
Python to run — started with :data:`WORKER_FLAG`.  It imports the citation
modules (none of them touches Tk), loads their indexes, and answers requests
until the app closes the connection, or dies, which closes it too.  Nothing
the app depends on hangs on it: a request it cannot answer — the helper would
not start, has died, took too long — raises :class:`Unavailable`, and the
caller scans in-process as it always has.

Only worker threads should send it anything (see ``_scan_off_thread`` in the
GUI): the helper answers one request at a time, and the Tk thread must not
queue behind a long scan.
"""

from __future__ import annotations

import atexit
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from multiprocessing.connection import Client, Listener

#: The command-line switch that makes this program the helper instead of
#: the application (see the top of courtlistener_gui.py).
WORKER_FLAG = "--getcases-scan-worker"

# Where the helper listens and the key it checks: the environment rather than
# the command line, which other programs can read.
_ADDRESS_ENV = "GETCASES_SCAN_ADDRESS"
_KEY_ENV = "GETCASES_SCAN_KEY"

#: How long the app gives the helper to start listening, and the helper the
#: app to connect, before either gives up on the other.
_CONNECT_S = 60.0
#: How long a request waits for the helper to be connected — it is started
#: with the app, so normally it long since has been — before scanning
#: in-process instead.
_READY_WAIT_S = 15.0
#: The longest a single scan is waited for.  The longest opinion takes a
#: couple of seconds; past this the helper is taken to be stuck, and stopped.
_REQUEST_S = 120.0


class Unavailable(Exception):
    """The helper cannot answer: scan in-process instead."""


class HelperError(Exception):
    """The scan itself raised, in the helper — as it would have here."""


# --------------------------------------------------------------------------
# The application's side
# --------------------------------------------------------------------------

_state_lock = threading.Lock()      # guards _status and the fields below
_request_lock = threading.Lock()    # one request at a time
_ready = threading.Event()          # set once connected, or given up on
_status = "off"                     # off | starting | ready | failed
_conn = None
_proc: "subprocess.Popen | None" = None
_socket_dir: "str | None" = None
_atexit_set = False


def _command() -> list:
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_FLAG]
    return [sys.executable, os.path.abspath(__file__), WORKER_FLAG]


def _new_address() -> str:
    global _socket_dir
    if sys.platform == "win32":
        return (rf"\\.\pipe\getcases-scan-{os.getpid()}-"
                f"{secrets.token_hex(8)}")
    _socket_dir = tempfile.mkdtemp(prefix="getcases-scan-")
    return os.path.join(_socket_dir, "socket")


def start() -> None:
    """Start the helper in the background, if it is not running already.
    Returns at once; requests made meanwhile wait for it (see call)."""
    global _status, _atexit_set
    with _state_lock:
        if _status != "off":
            return
        _status = "starting"
        _ready.clear()          # a helper stopped earlier is not this one
        first, _atexit_set = not _atexit_set, True
    if first:
        atexit.register(stop)
    threading.Thread(target=_launch, name="scan-helper-start",
                     daemon=True).start()


def _launch() -> None:
    global _status, _conn, _proc
    try:
        address = _new_address()
        key = secrets.token_bytes(32)
        env = dict(os.environ)
        env.update({_ADDRESS_ENV: address, _KEY_ENV: key.hex(),
                    # A copy of the program asks nothing of anyone.
                    "GETCASES_SKIP_DEPENDENCY_PROMPT": "1"})
        flags = 0
        if sys.platform == "win32":
            # No console window, and below the window's own priority: it is
            # there to keep the window responsive, not to compete with it.
            flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        proc = subprocess.Popen(_command(), env=env, stdin=subprocess.DEVNULL,
                                close_fds=True, creationflags=flags)
        with _state_lock:
            _proc = proc
        conn = None
        deadline = time.monotonic() + _CONNECT_S
        while conn is None:
            try:
                conn = Client(address, authkey=key)
            except (FileNotFoundError, ConnectionRefusedError):
                # Not listening yet.
                if proc.poll() is not None:
                    raise RuntimeError(f"it exited ({proc.returncode})")
                if time.monotonic() > deadline:
                    raise TimeoutError("it never started listening")
                time.sleep(0.05)
        with _state_lock:
            _conn = conn
            _status = "ready"
    except Exception as exc:
        print(f"[scan-helper] not started, scanning in-process: {exc}")
        _shut_down("failed")
    finally:
        _ready.set()


def _shut_down(status: str) -> None:
    """Close the connection — the helper exits on reading its end — and see
    that the process is gone."""
    global _status, _conn, _proc, _socket_dir
    with _state_lock:
        conn, proc, sock_dir = _conn, _proc, _socket_dir
        _conn, _proc, _socket_dir = None, None, None
        _status = status
    if conn is not None:
        try:
            conn.close()
        except OSError:
            pass
    if proc is not None:
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass
    if sock_dir:
        shutil.rmtree(sock_dir, ignore_errors=True)


def stop() -> None:
    """Stop the helper (at exit).  Scans go in-process from then on."""
    if _status in ("starting", "ready"):
        _shut_down("off")


def is_ready() -> bool:
    return _status == "ready"


def call(name: str, *args, **kwargs):
    """``citations.<name>(*args, **kwargs)``, run in the helper.

    Raises :class:`Unavailable` when the helper cannot answer — the caller
    then runs it in-process — and :class:`HelperError` when the function
    itself raised there."""
    if _status == "off":
        raise Unavailable("not started")
    if not _ready.wait(_READY_WAIT_S):
        raise Unavailable("still starting")
    with _request_lock:
        conn = _conn
        if conn is None:
            raise Unavailable(_status)
        try:
            conn.send((name, args, kwargs))
            if not conn.poll(_REQUEST_S):
                raise TimeoutError(f"no answer to {name} in {_REQUEST_S:.0f} s")
            outcome, value = conn.recv()
        except Exception as exc:
            # Broken, or out of step with us: neither is worth another try.
            print(f"[scan-helper] stopped, scanning in-process: {exc}")
            _shut_down("failed")
            raise Unavailable(str(exc)) from exc
    if outcome == "ok":
        return value
    raise HelperError(value)


# --------------------------------------------------------------------------
# The helper's side
# --------------------------------------------------------------------------

def _scans() -> dict:
    """What the helper will run: citations' whole-document scans."""
    import citations
    return {
        "detect_links": citations.detect_links,
        "build_short_cite_index": citations.build_short_cite_index,
    }


def serve() -> None:
    """Run as the helper: listen for the application, then answer its
    requests until it closes the connection."""
    address = os.environ.get(_ADDRESS_ENV, "")
    try:
        key = bytes.fromhex(os.environ.get(_KEY_ENV, ""))
    except ValueError:
        key = b""
    if not address or not key:
        sys.exit("scan helper: started without an address to listen on")
    if hasattr(os, "nice"):
        try:
            os.nice(5)          # as the Windows priority class, above
        except OSError:
            pass
    # Should the application never come — it closed while this started —
    # do not wait for it for ever.
    abandon = threading.Timer(_CONNECT_S, os._exit, args=(3,))
    abandon.daemon = True
    abandon.start()
    listener = Listener(address, authkey=key)
    try:
        conn = listener.accept()
    finally:
        listener.close()        # one client; and this removes a Unix socket
    abandon.cancel()
    scans = _scans()
    # Load the indexes the scans read now, so the first request does not
    # wait for them: one small scan that touches each of them.
    try:
        scans["detect_links"](
            "Smith v. Jones, 1 Ex. 1 (1850); 8 S.E.C. 893; "
            "116 Cong. Rec. 36481; 42 U.S.C. § 1983.")
    except Exception as exc:
        print(f"[scan-helper] warming up failed: {exc}")
    while True:
        try:
            name, args, kwargs = conn.recv()
        except (EOFError, OSError):
            break                       # the application has gone
        try:
            reply = ("ok", scans[name](*args, **kwargs))
        except Exception as exc:
            reply = ("error", f"{type(exc).__name__}: {exc}")
        try:
            conn.send(reply)
        except (EOFError, OSError):
            break
        except Exception as exc:        # an answer that would not pickle
            conn.send(("error", f"{type(exc).__name__}: {exc}"))
    conn.close()


if __name__ == "__main__" and WORKER_FLAG in sys.argv[1:]:
    serve()
