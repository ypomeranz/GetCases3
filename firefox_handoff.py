"""A temporary, local-only page handoff for GetCases' dedicated Firefox.

Firefox's add-on development protocol installs the helper, then disconnects.
No WebDriver is attached to the CAPTCHA window. The helper reads its one tab,
returns the page and Google cookies over an authenticated loopback connection,
and closes that tab only after the app has accepted the results.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bs4 import BeautifulSoup

import scholar_pages


PREFERENCES = {
    "devtools.chrome.enabled": True,
    "devtools.debugger.remote-enabled": True,
    "devtools.debugger.prompt-connection": False,
    "devtools.debugger.force-local": True,
}
MAX_PAGE_BYTES = 16 * 1024 * 1024


def _packet(stream):
    size = bytearray()
    while True:
        char = stream.read(1)
        if char == b":":
            break
        if not char or not char.isdigit() or len(size) >= 9:
            raise OSError("Invalid Firefox debugging response")
        size.extend(char)
    length = int(size)
    if length > MAX_PAGE_BYTES:
        raise OSError("Firefox debugging response too large")
    payload = stream.read(length)
    if len(payload) != length:
        raise OSError("Firefox disconnected")
    return json.loads(payload)


def _request(sock, stream, actor, kind, **fields):
    payload = json.dumps({"to": actor, "type": kind, **fields}).encode()
    sock.sendall(str(len(payload)).encode() + b":" + payload)
    for _ in range(50):
        reply = _packet(stream)
        if reply.get("from") == actor:
            if reply.get("error"):
                raise OSError("Firefox refused the page handoff helper")
            return reply
    raise OSError("Firefox did not answer the page handoff request")


class FirefoxHandoff:
    def __init__(self, profile: Path, url: str, page_key, on_shown=None):
        self.url = url
        self.page_key = page_key
        self.on_shown = on_shown
        self.shown = False
        self.result = None
        self.cookies = []
        self.ready = threading.Event()
        self._lock = threading.Lock()
        self._token = secrets.token_urlsafe(32)
        self._server = None
        self.addon = profile / "getcases-handoff"

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= MAX_PAGE_BYTES:
                        self.send_error(413)
                        return
                    self.connection.settimeout(5)
                    payload = self.rfile.read(size)
                    if self.path != "/" + owner._token:
                        self.send_error(403)
                        return
                    data = json.loads(payload)
                    reply = owner.accept(data)
                except (ValueError, TypeError, OSError):
                    self.send_error(400)
                    return
                payload = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True,
                         name="firefox-page-handoff").start()
        endpoint = f"http://127.0.0.1:{self._server.server_port}/{self._token}"
        self.addon.mkdir(parents=True, exist_ok=True)
        manifest = {
            "manifest_version": 2, "name": "GetCases Scholar Handoff",
            "version": "1.0", "background": {"scripts": ["config.js", "background.js"]},
            "browser_specific_settings": {"gecko": {"id": "scholar-handoff@getcases.local"}},
            "permissions": ["tabs", "cookies", "https://*.google.com/*", "http://127.0.0.1/*"],
        }
        (self.addon / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (self.addon / "config.js").write_text(
            "const HANDOFF = " + json.dumps({"endpoint": endpoint, "url": self.url}) + ";",
            encoding="utf-8")
        (self.addon / "background.js").write_text(
            Path(__file__).with_name("firefox_handoff.js").read_text(encoding="utf-8"),
            encoding="utf-8")
        with socket.socket() as port:
            port.bind(("127.0.0.1", 0))
            return port.getsockname()[1]

    def install(self, port, process, timeout=20):
        deadline = time.monotonic() + timeout
        while process.poll() is None and time.monotonic() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=1)
            except OSError:
                time.sleep(0.2)
                continue
            with sock:
                sock.settimeout(10)
                with sock.makefile("rb") as stream:
                    _packet(stream)  # root greeting
                    root = _request(sock, stream, "root", "getRoot")
                    _request(sock, stream, root["addonsActor"], "installTemporaryAddon",
                             addonPath=str(self.addon.resolve()), openDevTools=False)
            return
        raise OSError("Firefox page handoff did not start")

    def accept(self, data):
        if not isinstance(data, dict):
            raise ValueError("Invalid page")
        url, html = data.get("url"), data.get("html")
        if not isinstance(url, str) or not isinstance(html, str):
            raise ValueError("Invalid page")
        with self._lock:
            if self.result is not None:
                return {"close": True}
            same_page = self.page_key(url) == self.page_key(self.url)
            usable = (same_page and not scholar_pages.response_needs_browser(self.url, url, html)
                      and (scholar_pages.has_search_content(html)
                           if scholar_pages.is_search_url(self.url)
                           else any(node.get_text(" ", strip=True) for node in
                                    BeautifulSoup(html, "html.parser").select(
                                        "#gs_opinion, .gs_opinion, #gs_howcited_ccl"))))
            if usable:
                self.cookies = data.get("cookies") if isinstance(data.get("cookies"), list) else []
                self.result = (html, url)
                self.ready.set()
                return {"close": True}
            if not self.shown:
                self.shown = True
                if self.on_shown:
                    self.on_shown()
            return {"show": True}

    def close(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def finish(self, process):
        """Release only our completed browser, after the caller saves cookies.

        Store Firefox can leave its launcher and background processes running
        after its last window closes. An unanswered window never takes this
        path. Do not leave the dedicated profile locked for the next lookup.
        """
        if self.result is None:
            return False
        try:
            process.wait(timeout=5)
            return True
        except subprocess.TimeoutExpired:
            pass
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                process.terminate()
            process.wait(timeout=5)
            return True
        except (OSError, subprocess.SubprocessError):
            return False
