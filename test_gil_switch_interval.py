"""A window's Tk calls are not held up behind a worker computing in Python.

Every Tk call gives the GIL up and must win it back, and while a worker
computes, winning it back waits out the switch interval (see
``_GIL_SWITCH_INTERVAL_S``).  On Windows a wait of a millisecond or more
lasts until the system timer next ticks — every 15.6 ms unless some program
has asked for a finer timer — so a long opinion's window, some ten thousand
Tk calls, took ten seconds and more to come up beside a busy worker, and its
text scrolled in jerks while its scan was read behind it.  How long a tick
is depends on what else is running, so the timing below catches a regression
only while the timer is coarse; the interval's own value is checked always.
"""

import ast
import os
import sys
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path

os.environ["GETCASES_SKIP_DEPENDENCY_PROMPT"] = "1"

import courtlistener_gui as gui


def _display_available() -> bool:
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


class SwitchIntervalTests(unittest.TestCase):

    def test_the_application_sets_it(self):
        tree = ast.parse(Path(gui.__file__).read_text(encoding="utf-8"))
        main = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "main")
        calls = [ast.unparse(call.args[0]) for call in ast.walk(main)
                 if isinstance(call, ast.Call)
                 and ast.unparse(call.func) == "sys.setswitchinterval"]
        self.assertEqual(calls, ["_GIL_SWITCH_INTERVAL_S"])

    def test_under_a_millisecond(self):
        self.assertGreater(gui._GIL_SWITCH_INTERVAL_S, 0)
        self.assertLess(gui._GIL_SWITCH_INTERVAL_S, 0.001)

    @unittest.skipUnless(_display_available(), "needs a display for Tk")
    def test_tk_calls_beside_a_busy_worker(self):
        root = tk.Tk()
        root.withdraw()
        text = tk.Text(root)
        stop = threading.Event()

        def busy():
            x = 0
            while not stop.is_set():
                x = (x * 31 + 7) % 1000003

        before = sys.getswitchinterval()
        sys.setswitchinterval(gui._GIL_SWITCH_INTERVAL_S)
        worker = threading.Thread(target=busy, daemon=True)
        worker.start()
        try:
            time.sleep(0.05)
            started = time.perf_counter()
            for _ in range(300):
                text.insert("end", "x")
            spent = time.perf_counter() - started
        finally:
            stop.set()
            worker.join()
            sys.setswitchinterval(before)
            root.destroy()
        # A 15.6 ms tick a call would be 4.7 s; unhindered, a few ms.
        self.assertLess(spent, 0.5)


if __name__ == "__main__":
    unittest.main()
