"""Statutes and regulations open without waiting on what they need not.

The U.S. Code: when the OLRC is slow to answer, Cornell's copy is asked for
as well and the first to come is shown (the OLRC's word that a section does
not exist still stands).  The C.F.R.: eCFR's page, its issue date and its XML
are asked for together rather than one after another.

Run with:  python -m unittest test_statute_speed -v
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import ecfr
import us_code
from us_code import SectionNotFound, UscSection


def _doc(source: str) -> UscSection:
    return UscSection(title="28", section="2254", url=f"https://{source}.test",
                      paras=[("sechead", 0, "§2254")], source=source)


class _Fresh(unittest.TestCase):
    def setUp(self):
        self._reset()
        patcher = mock.patch.object(us_code, "_OLRC_HEDGE_S", 0.1)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._reset()

    @staticmethod
    def _reset():
        us_code._cache.clear()
        us_code._olrc_rest_until = 0.0


class SlowOlrcTests(_Fresh):

    def _sites(self, olrc_delay, olrc, lii_delay=0.0, lii=None):
        asked = []

        def olrc_section(title, section):
            asked.append("olrc")
            time.sleep(olrc_delay)
            if isinstance(olrc, BaseException):
                raise olrc
            return olrc

        def lii_section(title, section):
            asked.append("lii")
            time.sleep(lii_delay)
            if isinstance(lii, BaseException):
                raise lii
            return lii

        return (mock.patch.object(us_code, "_olrc_section", olrc_section),
                mock.patch.object(us_code, "_lii_section", lii_section),
                asked)

    def test_a_prompt_olrc_is_all_that_is_asked(self):
        a, b, asked = self._sites(0.0, _doc("olrc"))
        with a, b:
            self.assertEqual(us_code.load_section("28", "2254").source, "olrc")
        self.assertEqual(asked, ["olrc"])

    def test_a_slow_olrc_has_cornell_asked_too_and_the_first_is_shown(self):
        a, b, asked = self._sites(1.0, _doc("olrc"), 0.0, _doc("lii"))
        start = time.monotonic()
        with a, b:
            doc = us_code.load_section("28", "2254")
        self.assertLess(time.monotonic() - start, 0.8)
        self.assertEqual(doc.source, "lii")
        self.assertIn("was slow to answer", doc.source_note)
        self.assertEqual(asked, ["olrc", "lii"])
        # A slow OLRC is not a down one: it is asked first next time.
        self.assertEqual(us_code._olrc_rest_until, 0.0)

    def test_the_olrc_still_wins_when_it_comes_first(self):
        a, b, _asked = self._sites(0.3, _doc("olrc"), 1.5, _doc("lii"))
        with a, b:
            self.assertEqual(us_code.load_section("28", "2254").source, "olrc")

    def test_the_olrc_s_word_that_there_is_no_such_section_stands(self):
        a, b, _asked = self._sites(0.3, SectionNotFound("no such section"),
                                   1.0, _doc("lii"))
        with a, b, self.assertRaises(SectionNotFound):
            us_code.load_section("28", "2254")

    def test_cornell_failing_leaves_the_olrc_s_answer(self):
        a, b, _asked = self._sites(0.4, _doc("olrc"), 0.0,
                                   ConnectionError("down"))
        with a, b:
            self.assertEqual(us_code.load_section("28", "2254").source, "olrc")


class EcfrTogetherTests(unittest.TestCase):

    XML = b"""<?xml version="1.0" encoding="UTF-8"?>
    <DIV8 N="1614.105" TYPE="SECTION">
      <HEAD>Section heading.</HEAD>
      <P>(a) Body.</P>
    </DIV8>"""

    def setUp(self):
        ecfr._cache.pop(("29", "1614.105"), None)
        self.addCleanup(ecfr._cache.pop, ("29", "1614.105"), None)

    def test_the_page_is_asked_for_while_the_issue_date_is(self):
        started = {}

        class Response:
            status_code = 200

            def __init__(self, text="", content=b""):
                self.text, self.content = text, content

            def raise_for_status(self):
                return None

        def get(url, **_kw):
            if "/current/" in url:
                started["page"] = time.monotonic()
                time.sleep(0.3)
                return Response(text="<html><body></body></html>")
            started["xml"] = time.monotonic()
            return Response(content=self.XML)

        def issue_date(title):
            started["date"] = time.monotonic()
            time.sleep(0.3)
            return "2026-07-27"

        begin = time.monotonic()
        with mock.patch.object(ecfr, "_issue_date", issue_date), \
                mock.patch.dict(sys.modules,
                                {"requests": SimpleNamespace(get=get)}):
            doc = ecfr.load_section("29", "1614.105")
        took = time.monotonic() - begin
        self.assertEqual(doc.paras[0][2], "Section heading.")
        # One after the other would take 0.6 s; together, the slower of them.
        self.assertLess(took, 0.55)
        self.assertLess(abs(started["page"] - started["date"]), 0.15)

    def test_a_page_that_fails_still_leaves_the_xml(self):
        class Response:
            status_code = 200
            text = ""
            content = XML = b""

            def raise_for_status(self):
                return None

        def get(url, **_kw):
            if "/current/" in url:
                raise ConnectionError("page down")
            response = Response()
            response.content = EcfrTogetherTests.XML
            return response

        with mock.patch.object(ecfr, "_issue_date",
                               return_value="2026-07-27"), \
                mock.patch.dict(sys.modules,
                                {"requests": SimpleNamespace(get=get)}):
            doc = ecfr.load_section("29", "1614.105")
        self.assertFalse(doc.site_formatting)
        self.assertEqual(doc.paras[0][2], "Section heading.")


if __name__ == "__main__":
    unittest.main()
