"""One GetCases answering the hotkey at a time.

Closing GetCases's window only hides it — the process keeps running so
Ctrl+Space still opens the spotlight — so a copy started later (from another
checkout, say, or by the updater) runs beside one already in the background,
and both listen for the hotkey.  Every press then toggles both spotlight
popups, which is harmless until one of them is closed some other way (Escape,
a search, a click): from then on they are out of step, every press closes one
popup and opens the other, and the spotlight cannot be got rid of.

So the newest instance takes over.  Each running instance listens on a local
socket and records where in a small file beside the settings; a new one tells
whichever instance that file names that it has arrived, and that instance
steps back — gives up the hotkey, and quits if it has nothing on screen.  The
newest wins, not the oldest, because a launch is the user asking for the copy
they just started: newer code from another checkout, or the one the updater
has just relaunched.

Tkinter-free, and only ever run from the app's ``main()``: the tests build the
app directly and never announce themselves to a GetCases the user has open.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import threading
from pathlib import Path
from typing import Callable, Optional

#: How long a new instance waits for an older one to answer the door, and
#: then for its reply — short, since a stale record (an instance that crashed)
#: means nobody is there, and startup should not wait on it.
_CONNECT_TIMEOUT = 0.5
_REPLY_TIMEOUT = 1.5


class InstanceChannel:
    """This process's place in the one-at-a-time arrangement: its record in
    *path*, and the socket a newer instance knocks on.

    *on_newer* is called — on the channel's own thread, so it must not touch
    Tk — when a newer instance has started.
    """

    def __init__(self, path, on_newer: Callable[[], None]) -> None:
        self.path = Path(path)
        self._on_newer = on_newer
        # Proves a knock comes from a GetCases that read this instance's
        # record, not from anything else that finds the port.
        self._token = secrets.token_hex(16)
        self._server: Optional[socket.socket] = None
        self._closed = False

    def claim(self) -> bool:
        """Tell the instance *path* names, if one answers, that this one has
        arrived; then open this instance's socket and record it in *path*.
        Returns whether an older instance answered."""
        answered = self._notify_older()
        self._listen()
        self._record()
        return answered

    def close(self) -> None:
        """Stop listening, and take this instance's record out of *path*
        unless a newer instance has already replaced it."""
        self._closed = True
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(record, dict) and record.get("token") == self._token:
                self.path.unlink()
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------

    def _notify_older(self) -> bool:
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            port, token = int(record["port"]), str(record["token"])
        except (OSError, ValueError, KeyError, TypeError):
            return False                      # no record, or not one of ours
        message = json.dumps({"token": token, "newer": os.getpid()}) + "\n"
        try:
            with socket.create_connection(
                    ("127.0.0.1", port), timeout=_CONNECT_TIMEOUT) as conn:
                conn.settimeout(_REPLY_TIMEOUT)
                conn.sendall(message.encode("utf-8"))
                with conn.makefile("r", encoding="utf-8") as reply:
                    answer = reply.readline()
        except OSError:
            return False                      # gone: a stale record
        try:
            return bool(json.loads(answer).get("ok"))
        except (ValueError, AttributeError):
            return False

    def _listen(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))         # this machine only, any port
        server.listen(4)
        self._server = server
        threading.Thread(target=self._serve, args=(server,), daemon=True,
                         name="getcases-instance").start()

    def _serve(self, server: socket.socket) -> None:
        while not self._closed:
            try:
                conn, _addr = server.accept()
            except OSError:
                return                        # closed
            with conn:
                try:
                    conn.settimeout(_REPLY_TIMEOUT)
                    with conn.makefile("r", encoding="utf-8") as knock:
                        message = json.loads(knock.readline())
                except (OSError, ValueError):
                    continue
                if (not isinstance(message, dict)
                        or message.get("token") != self._token):
                    continue                  # not a GetCases that knows us
                try:
                    conn.sendall(b'{"ok": true}\n')
                except OSError:
                    pass
            try:
                self._on_newer()
            except Exception as exc:          # never kill the channel
                print(f"[instance] stepping back for a newer GetCases "
                      f"failed: {exc}")

    def _record(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"pid": os.getpid(),
                  "port": self._server.getsockname()[1],
                  "token": self._token}
        # Written whole and swapped in, so a reader never sees half of it.
        scratch = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        scratch.write_text(json.dumps(record), encoding="utf-8")
        os.replace(scratch, self.path)
