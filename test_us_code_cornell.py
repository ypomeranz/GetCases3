"""The U.S. Code from Cornell's copy while the OLRC's site is down.

When uscode.house.gov is down for maintenance, house.gov answers every
request with an "Under Maintenance" page, and with HTTP 200.  A section is
then read from Cornell's Legal Information Institute, which republishes the
OLRC's releases: the same text, source credit and notes, set as the OLRC
sets them, so the viewer's pin-cite jumps and Copy + Cite read it the same
way.  A section the OLRC says it does not have is not looked for at Cornell,
whose copy can trail the OLRC's.

The fixtures are Cornell's and house.gov's own markup, cut down (October
2026).  Nothing here touches the network.
"""

import pathlib
import re
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import courtlistener_gui
import us_code
from us_code import (
    SectionNotFound,
    UnitNotFound,
    UscSection,
    lii_adjacent,
    lii_crumbs,
    load_section,
    load_unit,
    open_unit_entry,
    parse_lii_section,
    parse_lii_unit,
    parse_section,
    statute_paths,
)

HOUSE_DOWN = """<html lang="en" dir="ltr" class=" js"><head>
<meta charset="utf-8">
<link rel="canonical" href="https://www.house.gov/">
<title>Under Maintenance</title></head><body>
<a href="#main-content">Skip to main content</a>
<h1>Site is currently under maintenance</h1>
<p>The site you requested is currently unavailable. Please visit the Clerk of
the House to find your Representative's or a committee's contact
information.</p></body></html>"""


def cornell_page(h1, text="", notes="", crumbs=(), own="",
                 prev="", next_=""):
    """A Cornell page: breadcrumb, heading, prev/next links, the text tab
    and the notes tab."""
    items = "".join(
        f'<li class="breadcrumb-item"><a href="/uscode/text/{path}">{label}'
        "</a></li>" for path, label in crumbs)
    links = []
    if prev:
        links.append(f'<a href="/uscode/text/{prev}" title=" x">prev</a>')
    if next_:
        links.append(f'<a href="/uscode/text/{next_}" title=" y">next</a>')
    return (
        '<div id="breadcrumb"><nav aria-label="breadcrumb"><ol '
        'class="breadcrumb"><li class="breadcrumb-item"><a href="/">LII</a>'
        '</li><li class="breadcrumb-item" aria-label="U.S. Code table of '
        'contents"><a\n href="/uscode/text">U.S. Code</a></li>' + items
        + f'<li class="breadcrumb-item breadcrumb-last">{own}</li></ol></nav>'
        f'</div><h1 class="title" id="page_title"> {h1} </h1>'
        '<div class="tab-content"><div id="prevnext">\n'
        + " | ".join(links) + "\n</div>"
        "<div class=\"tab-pane active\" id=\"tab_default_1\">\n"
        "<!-- 'text' content area -->\n"
        f'<text><div class="text">\n{text}\n</div></text>\n</div>'
        "<div class=\"tab-pane\" id=\"tab_default_2\">\n"
        "<!-- 'notes' content area -->\n"
        f"<notes>{notes}</notes>\n</div></div>"
        '<aside id="supersizeme"><div class="block" id="toolbox">'
        '<h2 class="title toolbox">U.S. Code Toolbox</h2></div></aside>'
    )


# 28 U.S.C. § 2254(a)–(e)(2)(A)(i): an older section, its paragraphs flush
# with their subsection in print.
S2254 = """<div class="section">
<div class="subsection indent0"><a name="a"></a><span class="num" value="a">(a)</span>
<div class="content"> The Supreme Court, a Justice thereof, a circuit judge, or a district court shall entertain an application for a writ of habeas corpus in behalf of a person in custody pursuant to the judgment of a State court only on the ground that he is in custody in violation of the Constitution or laws or treaties of the United States.</div>
</div>
<div class="subsection indent0"><a name="b"></a><span class="num" value="b">(b)</span>
<div class="paragraph indent0"><a name="b_1"></a><span class="num" value="1">(1)</span><span class="chapeau"> An application for a writ of habeas corpus on behalf of a person in custody pursuant to the judgment of a State court shall not be granted unless it appears that—</span>
<div class="subparagraph indent1"><a name="b_1_A"></a><span class="num" value="A">(A)</span>
<div class="content"> the applicant has exhausted the remedies available in the courts of the State; or</div>
</div>
<div class="subparagraph indent1"><a name="b_1_B"></a><span class="num" value="B">(B)</span>
<div class="clause indent1"><a name="b_1_B_i"></a><span class="num" value="i">(i)</span>
<div class="content"> there is an absence of available State corrective process; or</div>
</div>
<div class="clause indent1"><a name="b_1_B_ii"></a><span class="num" value="ii">(ii)</span>
<div class="content"> circumstances exist that render such process ineffective to protect the rights of the applicant.</div>
</div>
</div>
</div>
<div class="paragraph indent0"><a name="b_2"></a><span class="num" value="2">(2)</span>
<div class="content"> An application for a writ of habeas corpus may be denied on the merits, notwithstanding the failure of the applicant to exhaust the remedies available in the courts of the State.</div>
</div>
</div>
<div class="subsection indent0"><a name="d"></a><span class="num" value="d">(d)</span><span class="chapeau"> An application for a writ of habeas corpus on behalf of a person in custody pursuant to the judgment of a State court shall not be granted with respect to any claim that was adjudicated on the merits in State court proceedings unless the adjudication of the claim—</span>
<div class="paragraph indent1"><a name="d_1"></a><span class="num" value="1">(1)</span>
<div class="content"> resulted in a decision that was contrary to, or involved an unreasonable application of, clearly established Federal law, as determined by the <span link="https://liicornell.org/liifedent/supreme_court_of_the_united_states" occur="1" src="named_federal_agencies">Supreme Court of the United States</span>; or</div>
</div>
</div>
<div class="subsection indent0"><a name="e"></a><span class="num" value="e">(e)</span>
<div class="paragraph indent0"><a name="e_1"></a><span class="num" value="1">(1)</span>
<div class="content"> In a proceeding instituted by an application for a writ of habeas corpus by a person in custody pursuant to the judgment of a State court, a determination of a factual issue made by a State court shall be presumed to be correct.</div>
</div>
<div class="paragraph indent0"><a name="e_2"></a><span class="num" value="2">(2)</span><span class="chapeau"> If the applicant has failed to develop the factual basis of a claim in State court proceedings, the court shall not hold an evidentiary hearing on the claim unless the applicant shows that—</span>
<div class="subparagraph indent1"><a name="e_2_A"></a><span class="num" value="A">(A)</span><span class="chapeau"> the claim relies on—</span>
<div class="clause indent2"><a name="e_2_A_i"></a><span class="num" value="i">(i)</span>
<div class="content"> a new rule of constitutional law, made retroactive to cases on collateral review by the Supreme Court, that was previously unavailable; or</div>
</div>
</div>
</div>
</div>
<div class="sourceCredit">(<span class="date" date="1948-06-25">June 25, 1948</span>, ch. 646, <a href="/rio/citation/62_Stat._967">62 Stat. 967</a>; <a href="/rio/citation/Pub._L._104-132">Pub. L. 104–132, title I, § 104</a>, <span class="date" date="1996-04-24">Apr. 24, 1996</span>, <a href="/rio/citation/110_Stat._1218">110 Stat. 1218</a>.)</div>
</div>"""

# 26 U.S.C. § 5000A(b): a newer section, every item headed.
S5000A_B = """<div class="section">
<div class="subsection indent2 firstIndent-2"><a name="b"></a><span class="num bold" value="b">(b)</span><span class="heading bold"> Shared responsibility payment</span>
<div class="paragraph indent3 firstIndent-2"><a name="b_1"></a><span class="num bold" value="1">(1)</span><span class="heading bold"> In general</span>
<div class="content">
<p>If a taxpayer who is an <a aria-label="Definitions - applicable individual" class="colorbox-load definedterm" href="/definitions/uscode.php?width=840&amp;height=800&amp;iframe=true&amp;def_id=26-USC-455687610-1217224269&amp;term_occur=999&amp;term_src=title:26:subtitle:D:chapter:48:section:5000A">applicable individual</a>, or an applicable individual for whom the taxpayer is liable under paragraph (3), fails to meet the requirement of subsection (a) for 1 or more months, then, except as provided in subsection (e), there is hereby imposed on the taxpayer a penalty with respect to such failures in the amount determined under subsection (c).</p>
</div>
</div>
<div class="paragraph indent3 firstIndent-2"><a name="b_2"></a><span class="num bold" value="2">(2)</span><span class="heading bold"> Inclusion with return</span>
<div class="content">
<p>Any penalty imposed by this section with respect to any month shall be included with a taxpayer’s return under chapter 1 for the taxable year which includes such month.</p>
</div>
</div>
<div class="paragraph indent3 firstIndent-2"><a name="b_3"></a><span class="num bold" value="3">(3)</span><span class="heading bold"> Payment of penalty</span><span class="chapeau indent1">If an individual with respect to whom a penalty is imposed by this section for any month—</span>
<div class="subparagraph indent2"><a name="b_3_A"></a><span class="num" value="A">(A)</span>
<div class="content"> is a dependent (as defined in section 152) of another taxpayer for the other taxpayer’s taxable year including such month, such other taxpayer shall be liable for such penalty, or</div>
</div>
<div class="subparagraph indent2"><a name="b_3_B"></a><span class="num" value="B">(B)</span>
<div class="content"> files a joint return for the taxable year including such month, such individual and the spouse of such individual shall be jointly liable for such penalty.</div>
</div>
</div>
</div>
</div>"""

# 15 U.S.C. § 78j: text of the section's own before and after its items,
# and a footnote.
S78J = """<div class="section">
<span class="chapeau indent0">It shall be unlawful for any person, directly or indirectly, by the use of any means or instrumentality of interstate commerce or of the mails, or of any facility of any national securities exchange—</span>
<div class="subsection indent1"><a name="a"></a><span class="num" value="a">(a)</span>
<div class="paragraph indent1"><a name="a_1"></a><span class="num" value="1">(1)</span>
<div class="content"> To effect a short sale, or to use or employ any stop-loss order in connection with the purchase or sale, of any security other than a government security, in contravention of such rules and regulations as the Commission may prescribe as necessary or appropriate in the public interest or for the protection of investors.</div>
</div>
<div class="paragraph indent1"><a name="a_2"></a><span class="num" value="2">(2)</span>
<div class="content"> Paragraph (1) of this subsection shall not apply to security futures products.</div>
</div>
</div>
<div class="subsection indent1"><a name="b"></a><span class="num" value="b">(b)</span>
<div class="content"> To use or employ, in connection with the purchase or sale of any security registered on a national securities exchange or any security not so registered, or any securities-based swap agreement <a class="footnoteRef" href="#fn002052" id="fn002052-ref" name="fn002052-ref">[1]</a> any manipulative or deceptive device or contrivance in contravention of such rules and regulations as the Commission may prescribe as necessary or appropriate in the public interest or for the protection of investors.</div>
</div>
<div class="continuation indent0 firstIndent0">Rules promulgated under subsection (b) that prohibit fraud, manipulation, or insider trading shall apply to security-based swap agreements to the same extent as they apply to securities.</div>
<div class="sourceCredit">(<span class="date" date="1934-06-06">June 6, 1934</span>, ch. 404, title I, § 10, <a href="/rio/citation/48_Stat._891">48 Stat. 891</a>.)</div>
</div>"""

N78J = """<div class="notes">
<hr class="footsep"/><br/><a href="#fn002052-ref" name="fn002052">[1] </a><span class="footnote"> So in original. Probably should be followed by a comma.</span><br/></div><div class="notes">
<div class="note" topic="editorialNotes"><span class="heading centered"><strong>Editorial Notes</strong></span></div>
<div class="note" topic="amendments"><span class="heading centered smallCaps">Amendments</span>
<p>2010—<a href="/rio/citation/Pub._L._111-203">Pub. L. 111–203, § 762(d)(3)(B)</a>, which directed amendment of the matter following subsection (b), was executed as the probable intent of Congress.</p>
</div>
</div>"""

# 26 U.S.C. § 7701(b)(3)(A)(ii): a table.
S7701_TABLE = """<div class="section">
<div class="clause indent3"><a></a><span class="num" value="ii">(ii)</span>
<div class="content"><span id="table_1_parent"> the sum of the number of days on which such individual was present in the United States during the current year and the 2 preceding calendar years (when multiplied by the applicable multiplier determined under the following table) equals or exceeds 183 days:</span>
<div class="table-responsive">
<table aria-labelledby="table_1_parent">
<colgroup>
<col/>
<col/>
</colgroup>
<thead>
<tr class="header">
<th>
<p><strong>   In the case of days in:</strong></p>
</th>
<th>
<p><strong>The applicable multiplier is:</strong></p>
</th>
</tr>
</thead>
<tbody>
<tr>
<td>
<p class="leaders"><span>Current year</span></p>
</td>
<td>
<p>1 </p>
</td>
</tr>
<tr>
<td>
<p class="leaders"><span>1st preceding year</span></p>
</td>
<td>
<p>⅓ </p>
</td>
</tr>
</tbody>
</table>
</div>
</div>
</div>
</div>"""

S1984_NOTES = """<div class="notes">
</div><div class="notes">
<div class="note" topic="editorialNotes"><span class="heading centered"><strong>Editorial Notes</strong></span></div>
<div class="note" topic="codification"><span class="heading centered smallCaps">Codification</span>
<p stripi="True">Section, act Mar. 1, 1875, ch. 114, § 5, <a href="/rio/citation/18_Stat._337">18 Stat. 337</a>, related to Supreme Court review of cases arising under act <span class="date" date="1875-03-01">Mar. 1, 1875</span>.</p>
</div>
</div>"""

CHAPTER_21 = """<div class="chapter">
</div>
<div class="toc chapter">
<ol class="list-unstyled">
<li class="tocitem"><a href="/uscode/text/42/chapter-21/subchapter-I" title="GENERALLY">SUBCHAPTER I—GENERALLY (§§ 1981 – 1996b)</a></li>
<li class="tocitem"><a href="/uscode/text/42/chapter-21/subchapter-I-A" title="INSTITUTIONALIZED PERSONS">SUBCHAPTER I–A—INSTITUTIONALIZED PERSONS (§§ 1997 – 1997j)</a></li>
<li class="tocitem"><a href="/uscode/text/42/chapter-21/subchapter-VII" title="REGISTRATION AND VOTING STATISTICS">SUBCHAPTER VII—REGISTRATION AND VOTING STATISTICS (§ 2000f)</a></li>
</ol>
</div>"""

CHAPTER_153 = """<div class="chapter">
</div>
<div class="toc chapter">
<ol class="list-unstyled">
<li class="tocitem"><a href="/uscode/text/28/2241" title=" Power to grant writ">§ 2241. Power to grant writ</a></li>
<li class="tocitem"><a href="/uscode/text/28/2254" title=" State custody; remedies in Federal courts">§ 2254. State custody; remedies in Federal courts</a></li>
<li class="tocitem"><a href="/uscode/text/28/2256" title=" Omitted]">[§ 2256. Omitted]</a></li>
</ol>
</div>"""

PAGE_2254 = cornell_page(
    "28 U.S. Code § 2254 - State custody; remedies in Federal courts",
    S2254, crumbs=(("28", "Title 28"), ("28/part-VI", "PART VI"),
                   ("28/part-VI/chapter-153", "CHAPTER 153")),
    own="§ 2254", prev="28/2253", next_="28/2255")


class _Response:
    def __init__(self, status=200, text=""):
        self.status_code = status
        self.content = text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Server Error")


def _sites(house, cornell):
    """A requests stand-in: *house* and *cornell* each a page, a
    _Response, or an exception, or a function of the URL giving one.
    Records the URLs asked for."""
    asked = []

    def answer(spec, url):
        if callable(spec) and not isinstance(spec, type):
            spec = spec(url)
        if isinstance(spec, BaseException):
            raise spec
        return spec if isinstance(spec, _Response) else _Response(200, spec)

    def get(url, **_kw):
        asked.append(url)
        if "uscode.house.gov" in url:
            return answer(house, url)
        if "law.cornell.edu" in url:
            return answer(cornell, url)
        raise AssertionError(f"unexpected request {url}")

    return patch.dict(sys.modules, {"requests": SimpleNamespace(get=get)}), \
        asked


def _opening(paras, want):
    """Each paragraph's kind and indent, and as much of its text as the
    paragraph it is checked against gives."""
    return [(kind, indent, text[:len(w[2])]) for (kind, indent, text), w
            in zip(paras, want + [("", 0, "")] * len(paras))]


class _Fresh(unittest.TestCase):
    """Each test starts with an empty cache and the OLRC not resting."""

    def setUp(self):
        self._reset()

    def tearDown(self):
        self._reset()

    @staticmethod
    def _reset():
        us_code._cache.clear()
        us_code._unit_cache.clear()
        us_code._olrc_rest_until = 0.0


class FallbackTests(_Fresh):
    def test_the_house_down_for_maintenance(self):
        stub, asked = _sites(HOUSE_DOWN, PAGE_2254)
        with stub:
            doc = load_section("28", "2254")
        self.assertEqual(doc.source, "lii")
        self.assertEqual(doc.url,
                         "https://www.law.cornell.edu/uscode/text/28/2254")
        self.assertEqual(doc.heading,
                         "§2254. State custody; remedies in Federal courts")
        self.assertEqual(doc.source_name, "U.S. Code (Cornell LII)")
        self.assertIn("uscode.house.gov was not answering", doc.source_note)
        self.assertEqual(doc.label, "28 U.S.C. § 2254")
        self.assertEqual(doc.bluebook_cite(("d", "1")),
                         "28 U.S.C. § 2254(d)(1)")
        self.assertEqual(len(asked), 2)
        self.assertIn("uscode.house.gov", asked[0])

    def test_the_house_unreachable_or_failing(self):
        for house in (ConnectionError("timed out"), _Response(503)):
            with self.subTest(house=house):
                self._reset()
                stub, _asked = _sites(house, PAGE_2254)
                with stub:
                    self.assertEqual(load_section("28", "2254").source, "lii")

    def test_the_olrcs_own_copy_is_preferred(self):
        olrc = (pathlib.Path(__file__).parent / "test_data"
                / "42 USC 1983_ Civil action for deprivation of rights.html"
                ).read_text(encoding="utf-8")
        stub, asked = _sites(olrc, AssertionError("Cornell asked"))
        with stub:
            doc = load_section("42", "1983")
        self.assertEqual(doc.source, "olrc")
        self.assertEqual(doc.source_name, "U.S. Code (OLRC)")
        self.assertEqual(doc.key_url, doc.url)
        self.assertEqual(len(asked), 1)

    def test_no_such_section_at_the_olrc_is_not_looked_for_at_cornell(self):
        # Cornell's copy can trail the OLRC's: a section renumbered or
        # repealed since would still be there.
        stub, asked = _sites(_Response(404), PAGE_2254)
        with stub, self.assertRaises(SectionNotFound):
            load_section("28", "2254")
        self.assertFalse([url for url in asked if "cornell" in url])

    def test_the_olrcs_own_page_with_no_section_on_it(self):
        # The OLRC's viewer loads its stylesheets from /javax.faces.resource/
        # whatever the page holds; house.gov's maintenance notice does not.
        page = ('<html><head><link type="text/css" rel="stylesheet" '
                'href="/javax.faces.resource/usc.css.xhtml?ln=css" /></head>'
                "<body>Document not found</body></html>")
        stub, asked = _sites(page, PAGE_2254)
        with stub, self.assertRaises(SectionNotFound):
            load_section("28", "2254")
        self.assertEqual(len(asked), 1)

    def test_no_such_section_at_cornell_either(self):
        stub, _asked = _sites(HOUSE_DOWN, _Response(404))
        with stub, self.assertRaises(SectionNotFound) as ctx:
            load_section("42", "99999")
        self.assertIn("law.cornell.edu", str(ctx.exception))

    def test_neither_site_answering(self):
        stub, _asked = _sites(HOUSE_DOWN, ConnectionError("unreachable"))
        with stub, self.assertRaises(RuntimeError) as ctx:
            load_section("28", "2254")
        self.assertNotIsInstance(ctx.exception, LookupError)
        self.assertIn("uscode.house.gov", str(ctx.exception))
        self.assertIn("law.cornell.edu", str(ctx.exception))
        # Probably the reader's own connection: the OLRC is asked next time.
        self.assertEqual(us_code._olrc_rest_until, 0.0)

    def test_cornell_is_asked_first_for_a_while(self):
        stub, asked = _sites(HOUSE_DOWN, PAGE_2254)
        with stub:
            load_section("28", "2254")
            asked.clear()
            load_section("28", "2255")
            self.assertEqual(len(asked), 1)
            self.assertIn("law.cornell.edu", asked[0])
            # and once the while is up, the OLRC is tried again
            us_code._olrc_rest_until = 0.0
            asked.clear()
            load_section("28", "2253")
            self.assertIn("uscode.house.gov", asked[0])

    def test_a_range_the_site_does_not_know(self):
        def cornell(url):
            if url.endswith("/78a-78pp"):
                return _Response(404)
            return cornell_page("15 U.S. Code § 78a - Short title",
                                '<div class="section"><div class="content">'
                                "<p>This chapter may be cited as the "
                                "Securities Exchange Act of 1934.</p></div>"
                                "</div>")
        stub, _asked = _sites(HOUSE_DOWN, cornell)
        with stub:
            doc = load_section("15", "78a-78pp")
        self.assertEqual(doc.section, "78a")
        self.assertEqual(doc.paras[1][2], "This chapter may be cited as the "
                                          "Securities Exchange Act of 1934.")


class ReadingTests(unittest.TestCase):
    def test_an_older_section_set_as_printed(self):
        paras = parse_lii_section(PAGE_2254)
        want = [
            ("sechead", 0, "§2254. State custody; re"),
            ("body", 0, "(a) The Supreme Court, a "),
            ("body", 0, "(b)(1) An application for"),
            ("body", 1, "(A) the applicant has exh"),
            ("body", 1, "(B)(i) there is an absenc"),
            ("body", 1, "(ii) circumstances exist "),
            ("body", 0, "(2) An application for a "),
            ("body", 0, "(d) An application for a "),
            ("body", 1, "(1) resulted in a decisio"),
            ("body", 0, "(e)(1) In a proceeding in"),
            ("body", 0, "(2) If the applicant has "),
            ("body", 1, "(A) the claim relies on—"),
            ("body", 2, "(i) a new rule of constit"),
            ("credit", 0, "(June 25, 1948, ch. 646, "),
        ]
        self.assertEqual(_opening(paras, want), want)
        self.assertEqual(statute_paths(paras), [
            (), ("a",), ("b", "1"), ("b", "1", "A"), ("b", "1", "B", "i"),
            ("b", "1", "B", "ii"), ("b", "2"), ("d",), ("d", "1"),
            ("e", "1"), ("e", "2"), ("e", "2", "A"), ("e", "2", "A", "i"),
            (),
        ])
        self.assertIn("determined by the Supreme Court of the United "
                      "States; or", paras[8][2])

    def test_a_newer_section_reads_as_the_olrcs_copy_does(self):
        olrc = parse_section(
            (pathlib.Path(__file__).parent / "test_data"
             / "26 USC 5000A_ Requirement to maintain minimum essential "
               "coverage.html").read_text(encoding="utf-8"))
        want = [(kind, indent, path, text)
                for (kind, indent, text), path
                in zip(olrc, statute_paths(olrc))
                if path[:1] == ("b",)]
        paras = parse_lii_section(cornell_page(
            "26 U.S. Code § 5000A - Requirement to maintain minimum "
            "essential coverage", S5000A_B))
        got = [(kind, indent, path, text)
               for (kind, indent, text), path
               in zip(paras, statute_paths(paras)) if path]
        # The saved OLRC page has plain quotes and hyphens for dashes.
        plain = lambda text: re.sub(r"[’'—-]", "", text)
        self.assertEqual([(k, i, p) for k, i, p, _t in got],
                         [(k, i, p) for k, i, p, _t in want])
        self.assertEqual([plain(t) for *_x, t in got],
                         [plain(t) for *_x, t in want])

    def test_a_sections_own_text_before_and_after_its_items(self):
        paras = parse_lii_section(cornell_page(
            "15 U.S. Code § 78j - Manipulative and deceptive devices",
            S78J, N78J))
        want = [
            ("sechead", 0, "§78j. Manipulative and d"),
            ("body", 0, "It shall be unlawful for"),
            ("body", 1, "(a)(1) To effect a short "),
            ("body", 1, "(2) Paragraph (1) of this"),
            ("body", 1, "(b) To use or employ, in "),
            ("body", 0, "Rules promulgated under s"),
            ("credit", 0, "(June 6, 1934, ch. 404, t"),
            ("note-head", 0, "Footnotes"),
            ("note-body", 0, "[1] So in original. Prob"),
            ("note-head", 0, "Editorial Notes"),
            ("note-head", 0, "Amendments"),
            ("note-body", 0, "2010—Pub. L. 111–203, § 7"),
        ]
        self.assertEqual(_opening(paras, want), want)
        self.assertEqual(statute_paths(paras)[:6], [
            (), (), ("a", "1"), ("a", "2"), ("b",), ()])
        self.assertIn("swap agreement [1] any manipulative", paras[4][2])

    def test_a_table_row_is_a_line(self):
        paras = parse_lii_section(cornell_page(
            "26 U.S. Code § 7701 - Definitions", S7701_TABLE))
        self.assertEqual([text for _k, _i, text in paras[2:]], [
            "In the case of days in: | The applicable multiplier is:",
            "Current year | 1",
            "1st preceding year | ⅓",
        ])
        self.assertTrue(all(indent == 0 for _k, indent, _t in paras[1:]))

    def test_an_omitted_section(self):
        paras = parse_lii_section(cornell_page(
            "42 U.S. Code § 1984 - Omitted",
            '<div class="section" status="omitted">\n</div>', S1984_NOTES))
        want = [
            ("sechead", 0, "§1984. Omitted"),
            ("note-head", 0, "Editorial Notes"),
            ("note-head", 0, "Codification"),
            ("note-body", 0, "Section, act Mar. 1, 1875"),
        ]
        self.assertEqual(_opening(paras, want), want)

    def test_a_page_with_no_section(self):
        self.assertEqual(parse_lii_section("<html><body>Not here</body>"
                                           "</html>"), [])
        self.assertEqual(parse_lii_section(HOUSE_DOWN), [])

    def test_where_the_section_sits(self):
        self.assertEqual(lii_crumbs(PAGE_2254), ([
            ("lii:28", "Title 28"), ("lii:28/part-VI", "PART VI"),
            ("lii:28/part-VI/chapter-153", "CHAPTER 153"),
        ], "§ 2254"))
        self.assertEqual(lii_adjacent(PAGE_2254),
                         (("28", "2253"), ("28", "2255")))
        # a chapter's prev/next are chapters, not sections
        chapter = cornell_page("42 U.S. Code Chapter 21 - CIVIL RIGHTS",
                               prev="42/chapter-20A", next_="42/chapter-21A")
        self.assertEqual(lii_adjacent(chapter), (None, None))

    def test_prev_and_next_without_asking_anyone(self):
        doc = UscSection(title="28", section="2254", url="u", source="lii",
                         adjacent=(("28", "2253"), ("28", "2255")))
        with patch("us_code.load_unit") as contents:
            self.assertEqual(doc.neighbors(),
                             (("28", "2253"), ("28", "2255")))
        contents.assert_not_called()
        self.assertEqual(doc.key_url, us_code.section_url("28", "2254"))


class UnitTests(_Fresh):
    def test_a_chapters_subchapters(self):
        unit = parse_lii_unit(
            cornell_page("42 U.S. Code Chapter 21 - CIVIL RIGHTS",
                         CHAPTER_21, crumbs=(("42", "Title 42"),),
                         own="CHAPTER 21"),
            "lii:42/chapter-21",
            "https://www.law.cornell.edu/uscode/text/42/chapter-21")
        self.assertEqual((unit.title, unit.label, unit.heading),
                         ("42", "Chapter 21", "CHAPTER 21—CIVIL RIGHTS"))
        self.assertEqual(unit.crumbs, [("lii:42", "Title 42")])
        self.assertEqual(unit.host, "law.cornell.edu")
        self.assertEqual(
            [(e.kind, e.label, e.heading, e.unit_kind, e.designation,
              e.granule, e.first_section) for e in unit.entries], [
                ("unit", "Subchapter I", "GENERALLY", "subchapter", "I",
                 "lii:42/chapter-21/subchapter-I", "1981"),
                ("unit", "Subchapter I–A", "INSTITUTIONALIZED PERSONS",
                 "subchapter", "I–A", "lii:42/chapter-21/subchapter-I-A",
                 "1997"),
                ("unit", "Subchapter VII", "REGISTRATION AND VOTING "
                 "STATISTICS", "subchapter", "VII",
                 "lii:42/chapter-21/subchapter-VII", "2000f"),
            ])

    def test_a_chapters_sections(self):
        unit = parse_lii_unit(
            cornell_page("28 U.S. Code Chapter 153 Part VI - HABEAS CORPUS",
                         CHAPTER_153, crumbs=(("28", "Title 28"),
                                              ("28/part-VI", "PART VI")),
                         own="CHAPTER 153"),
            "lii:28/part-VI/chapter-153", "u")
        self.assertEqual(unit.heading, "CHAPTER 153—HABEAS CORPUS")
        self.assertEqual(
            [(e.kind, e.label, e.heading, e.section) for e in unit.entries],
            [("section", "§ 2241", "Power to grant writ", "2241"),
             ("section", "§ 2254", "State custody; remedies in Federal "
              "courts", "2254"),
             ("section", "§ 2256", "Omitted", "2256")])

    def test_a_title(self):
        unit = parse_lii_unit(
            cornell_page("U.S. Code: Title 42 — THE PUBLIC HEALTH AND "
                         "WELFARE", own="Title 42"), "lii:42", "u")
        self.assertEqual((unit.label, unit.heading),
                         ("Title 42",
                          "Title 42—THE PUBLIC HEALTH AND WELFARE"))

    def test_the_breadcrumb_of_a_cornell_section_opens_cornell_pages(self):
        page = cornell_page("42 U.S. Code Chapter 21 - CIVIL RIGHTS",
                            CHAPTER_21, crumbs=(("42", "Title 42"),),
                            own="CHAPTER 21")
        stub, asked = _sites(AssertionError("OLRC asked"), page)
        with stub:
            unit = load_unit("lii:42/chapter-21")
            self.assertIs(load_unit("lii:42/chapter-21"), unit)   # cached
        self.assertEqual(asked, [
            "https://www.law.cornell.edu/uscode/text/42/chapter-21"])
        self.assertEqual(unit.url, asked[0])
        with patch("us_code.load_unit", return_value="opened") as load:
            self.assertEqual(open_unit_entry(unit.entries[0], unit),
                             "opened")
        load.assert_called_once_with("lii:42/chapter-21/subchapter-I")

    def test_a_cornell_page_that_is_not_there(self):
        stub, _asked = _sites(None, _Response(404))
        with stub, self.assertRaises(UnitNotFound):
            load_unit("lii:42/chapter-999")

    def test_an_olrc_unit_while_the_house_is_down(self):
        class Streamed(_Response):
            def iter_content(self, _size):
                yield self.content

            def close(self):
                pass

        stub, _asked = _sites(Streamed(200, HOUSE_DOWN), None)
        with stub, self.assertRaises(RuntimeError) as ctx:
            load_unit("title42-chapter21")
        self.assertNotIsInstance(ctx.exception, UnitNotFound)
        self.assertIn("not answering", str(ctx.exception))


class ViewerTests(unittest.TestCase):
    def _window(self, doc, app=None):
        return SimpleNamespace(_doc=doc, _app=app or SimpleNamespace(),
                               _bookmark_noun=lambda: "section")

    def test_one_bookmark_whichever_site_the_text_came_from(self):
        doc = UscSection(title="28", section="2254",
                         url="https://www.law.cornell.edu/uscode/text/28/"
                             "2254",
                         paras=parse_lii_section(PAGE_2254), source="lii")
        olrc_key = "statute:" + us_code.section_url("28", "2254")
        desc = courtlistener_gui._StatuteWindow._bookmark_descriptor(
            self._window(doc))
        self.assertEqual(desc["key"], olrc_key)
        local = desc["payload"]["doc"]
        self.assertEqual(local["url"], doc.url)
        self.assertEqual(local["source_name"], "U.S. Code (Cornell LII)")

        # reopened from the bookmark, it is still filed there
        saved = courtlistener_gui._SavedStatuteDoc(local)
        touched = []
        app = SimpleNamespace(touch_bookmark=touched.append)
        courtlistener_gui._StatuteWindow._touch_bookmark(
            self._window(saved, app))
        self.assertEqual(touched, [olrc_key])
        self.assertEqual(
            courtlistener_gui._StatuteWindow._bookmark_descriptor(
                self._window(saved))["key"], olrc_key)

    def test_other_statutes_keep_their_own_address(self):
        saved = courtlistener_gui._SavedStatuteDoc({
            "url": "https://www.ecfr.gov/x", "kind": "cfr",
            "paras": [["sechead", 0, "§ 1614.105"]]})
        touched = []
        courtlistener_gui._StatuteWindow._touch_bookmark(
            self._window(saved, SimpleNamespace(
                touch_bookmark=touched.append)))
        self.assertEqual(touched, ["statute:https://www.ecfr.gov/x"])


if __name__ == "__main__":
    unittest.main()
