"""One GetCases answering the hotkey at a time.

Two copies of the app — one left running in the background, one started
later — both listened for Ctrl+Space, and once their spotlight popups fell
out of step every press closed one and opened the other: the spotlight
"closed for less than a second and reopened", indefinitely.  The newest
instance now takes over, telling the one before it (through a local socket
recorded in a file beside the settings) to step back.
"""

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from single_instance import InstanceChannel


class Knocks:
    """An on_newer callback that records being called."""

    def __init__(self):
        self.called = threading.Event()
        self.count = 0

    def __call__(self):
        self.count += 1
        self.called.set()


class InstanceChannelTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "courtlistener" / "instance.json"
        self.channels = []

    def tearDown(self):
        for channel in self.channels:
            channel.close()
        self._dir.cleanup()

    def channel(self, knocks=None):
        channel = InstanceChannel(self.path, knocks or Knocks())
        self.channels.append(channel)
        return channel

    def record(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_the_first_instance_finds_no_one(self):
        first = self.channel()
        self.assertFalse(first.claim())
        self.assertEqual(self.record()["token"], first._token)

    def test_a_newer_instance_tells_the_older_one(self):
        older_knocks = Knocks()
        older = self.channel(older_knocks)
        older.claim()
        newer = self.channel()

        self.assertTrue(newer.claim())
        self.assertTrue(older_knocks.called.wait(3))
        # The record names the newest instance now.
        self.assertEqual(self.record()["token"], newer._token)

    def test_each_newer_instance_tells_the_one_before_it(self):
        knocks = [Knocks(), Knocks(), Knocks()]
        first, second, third = (self.channel(k) for k in knocks)
        first.claim()
        second.claim()
        self.assertTrue(knocks[0].called.wait(3))
        third.claim()
        self.assertTrue(knocks[1].called.wait(3))
        time.sleep(0.2)
        self.assertEqual((knocks[0].count, knocks[1].count, knocks[2].count),
                         (1, 1, 0))

    def test_an_older_instance_leaving_keeps_the_newer_ones_record(self):
        older = self.channel()
        older.claim()
        newer = self.channel()
        newer.claim()

        older.close()
        self.assertEqual(self.record()["token"], newer._token)
        newer.close()
        self.assertFalse(self.path.exists())

    def test_a_stale_record_does_not_hold_up_the_start(self):
        # An instance that crashed left its record behind; nothing listens.
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(
            {"pid": 1, "port": port, "token": "gone"}), encoding="utf-8")

        started = time.monotonic()
        fresh = self.channel()
        self.assertFalse(fresh.claim())
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(self.record()["token"], fresh._token)

    def test_an_unreadable_record_is_replaced(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not json", encoding="utf-8")
        fresh = self.channel()
        self.assertFalse(fresh.claim())
        self.assertEqual(self.record()["token"], fresh._token)

    def test_a_knock_without_the_token_is_ignored(self):
        knocks = Knocks()
        running = self.channel(knocks)
        running.claim()
        port = self.record()["port"]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as c:
            c.sendall(b'{"token": "wrong", "newer": 1}\n')
            c.settimeout(2)
            self.assertEqual(c.recv(64), b"")   # closed without a reply
        self.assertFalse(knocks.called.wait(0.5))

    def test_a_closed_instance_answers_no_more(self):
        older_knocks = Knocks()
        older = self.channel(older_knocks)
        older.claim()
        older.close()
        # Its record is gone with it; a new instance finds no one.
        self.assertFalse(self.channel().claim())
        self.assertFalse(older_knocks.called.is_set())


if __name__ == "__main__":
    unittest.main()
