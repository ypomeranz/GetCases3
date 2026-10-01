"""GetCases packaged as a one-file .exe (PyInstaller).

Such a build runs from a folder it unpacks afresh at every start and deletes
at exit.  The opinion database lived beside the program's modules — inside
that folder — so every opinion saved was gone at the next start.  A packaged
build keeps it in ``data`` beside the .exe instead, filled the first time from
the database the build carries.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import opinion_db


class PackagedDatabaseTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        self.exe_dir = root / "GetCases"
        self.exe_dir.mkdir()
        self.unpacked = root / "_MEI12345"
        (self.unpacked / "data").mkdir(parents=True)
        (self.unpacked / "data" / "opinions.jsonl").write_text(
            '{"carried": true}\n', encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "GETCASES_DB_DIR"}
        for p in (patch.dict(os.environ, env, clear=True),
                  patch.object(opinion_db.sys, "frozen", True, create=True),
                  patch.object(opinion_db.sys, "_MEIPASS",
                               str(self.unpacked), create=True),
                  patch.object(opinion_db.sys, "executable",
                               str(self.exe_dir / "GetCases.exe"))):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._dir.cleanup()

    def test_the_database_lives_beside_the_exe(self):
        self.assertEqual(opinion_db.data_dir(), self.exe_dir / "data")

    def test_the_first_start_fills_it_from_the_build(self):
        jsonl = opinion_db.data_dir() / "opinions.jsonl"
        self.assertEqual(jsonl.read_text(encoding="utf-8"),
                         '{"carried": true}\n')
        self.assertEqual(sorted(p.name for p in jsonl.parent.iterdir()),
                         ["opinions.jsonl"])        # no scratch copy left

    def test_opinions_saved_since_are_kept(self):
        jsonl = opinion_db.data_dir() / "opinions.jsonl"
        jsonl.write_text('{"saved": true}\n', encoding="utf-8")
        opinion_db.data_dir()                       # the next start
        self.assertEqual(jsonl.read_text(encoding="utf-8"),
                         '{"saved": true}\n')

    def test_the_folder_named_in_the_environment_still_wins(self):
        elsewhere = Path(self._dir.name) / "elsewhere"
        with patch.dict(os.environ, {"GETCASES_DB_DIR": str(elsewhere)}):
            self.assertEqual(opinion_db.data_dir(), elsewhere)
        self.assertFalse((self.exe_dir / "data").exists())


if __name__ == "__main__":
    unittest.main()
