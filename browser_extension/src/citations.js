// GetCases citation reading, for when the app isn't running.
//
// While GetCases runs, the extension asks it to read a page's citations (the
// app's browser_bridge), which finds everything the app's own viewers link.
// Without it, this file reads the common ones itself — cases, the U.S. Code,
// the C.F.R., the federal rules, the Constitution, the Statutes at Large, the
// Federal Register, federal courts' unpublished opinions (in RECAP), the
// English Reports and the SEC's reports — with the
// app's own regular expressions (patterns.js, exported from the Python), and
// sends a click to the web page the app would open (browser_links.py): a
// case to its scanned report where the citation gives the address and the
// scan is there (the service worker looks first), else Google Scholar.
//
// A classic script, so the content script, the PDF viewer and the service
// worker can all load it: it sets globalThis.GetCasesCitations.

(function (root) {
  "use strict";

  const DATA = root.GetCasesPatterns;
  const T = DATA.tables;
  const compiled = new Map();

  /** The exported pattern *name*, with extra JavaScript flags. */
  function rx(name, extra = "g") {
    const key = name + "/" + extra;
    let re = compiled.get(key);
    if (!re) {
      const p = DATA.patterns[name];
      re = new RegExp(p.source, p.flags + extra);
      compiled.set(key, re);
    }
    re.lastIndex = 0;
    return re;
  }

  function* matchAll(name, text) {
    const re = rx(name, "g");
    let m;
    while ((m = re.exec(text)) !== null) {
      if (m[0] === "") { re.lastIndex++; continue; }
      yield m;
    }
  }

  /** The pattern *name* matched exactly at *pos* in *text*, or null
   *  (*flags*: "yd" for its groups' indices too). */
  function matchAt(name, text, pos, flags = "y") {
    const re = rx(name, flags);
    re.lastIndex = pos;
    return re.exec(text);
  }

  const squash = (s) => (s || "").replace(/\s+/g, " ").trim();

  // -------------------------------------------------------------------------
  // Reporters (citations.py: reporter_key, _valid_case_reporter, …)
  // -------------------------------------------------------------------------

  const NONCASE = new Set(T.nonCaseReporters);
  const PLAIN = new Set(T.plainCaseReporters);
  const WORD = new Set(T.wordReporterKeys);

  const looseKey = (rep) => (rep || "").toLowerCase().replace(/[^a-z0-9]+/g, "");

  function knownReporter(rep) {
    return Object.prototype.hasOwnProperty.call(T.reporterCanonical, looseKey(rep));
  }

  function canonicalReporter(rep) {
    const key = looseKey(rep);
    if (Object.prototype.hasOwnProperty.call(T.reporterCanonical, key)) {
      return T.reporterCanonical[key];
    }
    return squash((rep || "").replace(/’/g, "'"));
  }

  const reporterKey = (rep) => looseKey(canonicalReporter(rep));

  /** Where a "Reports" suffix merely carried by *rep* begins, or -1. */
  function reportsSuffixStart(rep) {
    const m = rx("reportsSuffix", "").exec(rep || "");
    if (!m || knownReporter(rep)) return -1;
    const base = rep.slice(0, m.index);
    if (/^\s*R\.?$/.test(rep.slice(m.index)) &&
        (/^(?:[A-Z]\.\s?){1,3}$/.test(base.trim()) || /\d(?:d|th)\.?$/.test(base.trim()))) {
      return -1;
    }
    return knownReporter(base) || WORD.has(looseKey(base)) ? m.index : -1;
  }

  function reporterWithoutSuffix(rep) {
    const at = reportsSuffixStart(rep);
    return at >= 0 ? rep.slice(0, at) : rep;
  }

  function validCaseReporter(written) {
    const rep = reporterWithoutSuffix(written || "");
    const key = looseKey(rep);
    if (!key || NONCASE.has(key)) return false;
    for (const name of ["journalReporter", "agOpinion", "courtRuleReporter", "firm"]) {
      if (rx(name, "").test(rep)) return false;
    }
    if (canonicalReporter(rep) === "Johns. Ch.") return true;
    if (PLAIN.has(key) || WORD.has(key) || key.endsWith("lexis")) return true;
    return written.includes(".");
  }

  function bareWordReporter(rep) {
    return !rep.includes(".") && WORD.has(looseKey(reporterWithoutSuffix(rep)));
  }

  // A state court's unpublished opinion is not linked: there is no docket
  // archive to find it in, as RECAP is for the federal courts', and nothing
  // free indexes it by its Westlaw or LEXIS number (citations.state_court).
  const FEDERAL_COURT_IDS = T.federalCourtIds;
  const STATE_KEYS = new Set(T.stateKeys);

  /** CourtListener's id for a federal court, as a citation abbreviates it
   *  ("D.N.J." → "njd"), or "". */
  function federalCourtId(court) {
    const key = looseKey(court);
    return Object.prototype.hasOwnProperty.call(FEDERAL_COURT_IDS, key) ? FEDERAL_COURT_IDS[key] : "";
  }

  /** Whether a court or reporter abbreviation begins with a state's. */
  function namesAState(abbr) {
    const words = (abbr || "").trim().split(/\s+/);
    const heads = words.length > 1 ? [words[0], words[0] + words[1]] : [words[0]];
    return heads.some((h) => STATE_KEYS.has(looseKey(h)));
  }

  /** Whether a citation's court parenthetical names a state's court ("N.D.
   *  Cal." is federal, "N.D." alone North Dakota). */
  function stateCourt(court) {
    return !federalCourtId(court) && namesAState(court);
  }

  /** Whether *rep* is a state's LEXIS designation ("Tex. App. LEXIS"). */
  function stateLexisReporter(rep) {
    return looseKey(rep).endsWith("lexis") && namesAState(rep);
  }

  const AMERICAN = new Set(T.americanKeys);
  const ER_REPORTER = new RegExp(`^(?:${DATA.patterns.engRepReporter.source})$`);

  /** Whether *rep*, written beside an English Reports citation, is the same
   *  case's nominate report: an abbreviation, not the reprint's own, and
   *  no American reporter (eng_rep.parallel_cites). */
  function parallelReporter(rep) {
    if (!/[.&]/.test(rep) || ER_REPORTER.test(rep.trim())) return false;
    return !(knownReporter(rep) || AMERICAN.has(looseKey(rep)) ||
             has(T.stateNominative, nominativeKey(rep)));
  }

  let nominate = null;
  /** Whether *rep* names a reporter of the nominate reports, as the
   *  app's index of them writes it (eng_rep.nominate_form_re). */
  function nominateReporter(rep) {
    const p = DATA.patterns.engRepNominate;
    if (!p) return false;
    nominate = nominate || new RegExp(`^(?:${p.source})$`, p.flags);
    return nominate.test(rep.trim());
  }

  /** Whether *pos* is where a citation's volume stands (after a case name's
   *  comma, a semicolon, an opening bracket, or the start). */
  function atCitationStart(text, pos) {
    while (pos > 0 && /\s/.test(text[pos - 1])) pos--;
    return pos === 0 || ",;([".includes(text[pos - 1]) ||
      /\(\s*(?:1[5-9]|20)\d{2}\s*\)$/.test(text.slice(Math.max(0, pos - 8), pos));
  }

  /** "vol reporter page" for a case-cite match (citations.case_match_text). */
  function caseMatchText(m) {
    const [whole, vol, rep, page] = m;
    if (canonicalReporter(rep) === "Johns. Ch.") return `${vol} Johns. Ch. ${page}`;
    const suffix = reportsSuffixStart(rep);
    if (suffix >= 0) return squash([vol, rep.slice(0, suffix), page].join(" "));
    let s = whole.replace(/\s+/g, " ").replace(/U\. S\./g, "U.S.").replace(/’/g, "'");
    s = s.replace(/\s*[[(][^\])]*[\])]\s*/g, " ");
    s = s.replace(/\s[-–—]\s*(?=\d)/g, " ");
    s = s.replace(/(?<=\.)\s+Rep\.(?=\s+\d)/g, "");
    s = s.replace(/\s*,\s*(?=\d+$)/, " ");
    return squash(s);
  }

  // -------------------------------------------------------------------------
  // Web pages (browser_links.py)
  // -------------------------------------------------------------------------

  // Google Scholar's search of case law in every court: without as_sdt it
  // searches articles, where a citation finds the law reviews citing it.
  const SCHOLAR_SEARCH = "https://scholar.google.com/scholar?hl=en&as_sdt=2006&q=";
  const CONSTITUTION_URL = "https://constitution.congress.gov/constitution/";
  const ENG_REP_URL = "https://www.commonlii.org/uk/cases/EngR";

  /** Python's urllib.parse.quote: everything but letters, digits, "_.-~"
   *  and "/" percent-encoded. */
  function pyQuote(s) {
    return encodeURIComponent(s)
      .replace(/%2F/g, "/")
      .replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
  }

  /** Python's urllib.parse.quote_plus, as urlencode uses it: a space is
   *  "+", and everything but letters, digits and "_.-~" percent-encoded. */
  function pyQuotePlus(s) {
    return encodeURIComponent(s)
      .replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase())
      .replace(/%20/g, "+");
  }

  const google = (text) => "https://www.google.com/search?q=" + pyQuote(squash(text));

  // A case's scan, from its citation (browser_links.case_pdf_urls).

  const CITE_PARTS = /^\s*(\d+)\s+(.+?)\s+(\d+)\s*$/;
  const has = (table, key) => Object.prototype.hasOwnProperty.call(table, key);
  const pad = (n, width) => String(n).padStart(width, "0");
  const nominativeKey = (rep) => (rep || "").toLowerCase().replace(/[^a-z]/g, "");

  /** The Caselaw Access Project's name for reporter *rep* in its addresses. */
  function caseLawSlug(rep) {
    const key = looseKey(rep);
    if (has(T.caseLawSlugs, key)) return T.caseLawSlugs[key];
    return (rep || "")
      .replace(/(?<![A-Za-z])([A-Za-z]\.)\s+(?=[A-Za-z]\.|\d)/g, "$1")
      .toLowerCase().replace(/ /g, "-").replace(/[^a-z0-9-]/g, "")
      .replace(/-+/g, "-").replace(/^-+|-+$/g, "");
  }

  /** The U.S. Reports volume of volume *vol* of *rep* ("1 Cranch" is 5
   *  U.S.), or 0 when *rep* is no Supreme Court reporter. */
  function usReportsVolume(vol, rep) {
    const key = looseKey(rep);
    if (key === "us") return vol;
    const nom = T.nominativeUS[key];
    return nom && vol >= 1 && vol <= nom[1] ? vol + nom[0] : 0;
  }

  /** The official scans of the opinion at *page* of volume *vol* of the
   *  U.S. Reports, the better first: the Library of Congress's, GPO's. */
  function usReportsPdfUrls(vol, page) {
    const U = T.usReports;
    const v = pad(vol, 3), vp = v + pad(page, 3);
    const loc = vol >= 1 && vol <= U.locMax
      ? `https://tile.loc.gov/storage-services/service/ll/usrep/usrep${v}/usrep${vp}/usrep${vp}.pdf` : "";
    const gpo = vol >= 2
      ? `https://www.govinfo.gov/content/pkg/USREPORTS-${vol}/pdf/USREPORTS-${vol}-${page}.pdf` : "";
    return (vol <= U.locPreferredMax ? [loc, gpo] : [gpo, loc]).filter(Boolean);
  }

  /** The scans of the case *cite* names whose addresses its citation gives,
   *  best first — the official U.S. Reports, else the Caselaw Access
   *  Project's — unchecked: CAP holds only some of a reporter's cases. */
  function casePdfUrls(cite) {
    const m = CITE_PARTS.exec((cite || "").split("@")[0]);
    if (!m) return [];
    const vol = +m[1], rep = m[2], page = +m[3];
    const us = usReportsVolume(vol, rep);
    if (us) return usReportsPdfUrls(us, page);
    const key = looseKey(rep);
    if (!key || key === "wl" || key.endsWith("lexis")) return [];
    const out = [];
    const add = (v, r) => {
      const slug = caseLawSlug(r);
      const url = `https://static.case.law/${slug}/${v}/case-pdfs/${pad(page, 4)}-01.pdf`;
      if (slug && !out.includes(url)) out.push(url);
    };
    // A state's reporter renumbered into its official series ("19 Pick.
    // 234" is 36 Mass. 234), where CAP files it.
    for (const [series, offset, volumes] of T.stateNominative[nominativeKey(rep)] || []) {
      if (vol >= 1 && vol <= volumes) add(vol + offset, series);
    }
    add(vol, rep);
    return out;
  }

  const scholarCaseUrl = (cite) => SCHOLAR_SEARCH + pyQuote(`"${(cite || "").split("@")[0]}"`);

  /** Every page that may show the case *cite* names, in the order to try
   *  them: its scans, then Google Scholar, which always answers. */
  function caseUrls(cite) {
    return [...casePdfUrls(cite), scholarCaseUrl(cite)];
  }

  /** The one page for the case when there is no looking first: its official
   *  U.S. Reports scan where that is surely there, else Google Scholar. */
  function caseUrl(cite) {
    const m = CITE_PARTS.exec((cite || "").split("@")[0]);
    if (m) {
      const us = usReportsVolume(+m[1], m[2]);
      if (us && us <= T.usReports.govinfoMax) return usReportsPdfUrls(us, +m[3])[0];
    }
    return scholarCaseUrl(cite);
  }

  /** "title:section:sub,sub" → [title, section, [subs]]. */
  function splitSpec(value) {
    const s = value || "";
    const first = s.indexOf(":");
    if (first < 0) return [s, "", []];
    const head = s.slice(0, first);
    const rest = s.slice(first + 1);
    const last = rest.lastIndexOf(":");
    const section = last < 0 ? rest : rest.slice(0, last);
    const subs = last < 0 ? "" : rest.slice(last + 1);
    return [head, section, subs.split(",").filter(Boolean)];
  }

  /** The web page for a (kind, value) action — what the app's right-click
   *  "open in browser" opens. */
  function browserUrl(kind, value, text = "") {
    const [first, section] = splitSpec(value);
    switch (kind) {
      case "browse": case "statpdf": case "frpdf":
        return value;
      case "usc":
        if (first && section) {
          return "https://uscode.house.gov/view.xhtml?req=granuleid:" +
            `USC-prelim-title${first}-section${section}&num=0&edition=prelim`;
        }
        break;
      case "cfr":
        if (first && section) return `https://www.ecfr.gov/current/title-${first}/section-${section}`;
        break;
      case "rule":
        if (T.ruleSets[first] && section) {
          return `https://www.law.cornell.edu/rules/${T.ruleSets[first].path}/rule_${section}`;
        }
        break;
      case "const":
        if (first === "amend" && /^\d+$/.test(section)) return `${CONSTITUTION_URL}amendment-${+section}/`;
        if (first === "art" && /^\d+$/.test(section)) return `${CONSTITUTION_URL}article-${+section}/`;
        if (first === "pmbl") return `${CONSTITUTION_URL}preamble/`;
        return CONSTITUTION_URL;
      case "cite":
        return caseUrl(value);
      case "sec":
        return T.secCatalogUrl;
      case "recap": {
        // CourtListener's search of RECAP, filtered to the docket (or, with
        // none printed, the case's name), the court and the opinion's date.
        let spec = {};
        try { spec = JSON.parse(value); } catch (e) { /* no spec: an empty search */ }
        const params = [["type", "rd"], ["q", ""],
          ["entry_date_filed_after", spec.date || ""], ["entry_date_filed_before", spec.date || ""]];
        if (spec.docket) params.push(["docket_number", spec.docket]);
        else if (spec.name) params.push(["case_name", spec.name]);
        if (spec.court) params.push(["court", spec.court]);
        return "https://www.courtlistener.com/?" + params.map(([k, v]) => `${pyQuotePlus(k)}=${pyQuotePlus(v)}`).join("&");
      }
      case "engrep": {
        const m = /^(\d+):(\d+)/.exec(value);
        if (m) {
          return `${ENG_REP_URL}/cgi-bin/sinosrch.cgi?query=${pyQuote(`${m[1]} ER ${m[2]}`)}` +
            "&mask_path=uk/cases/EngR";
        }
        return "";
      }
      default:
        break;
    }
    return google(text || value);
  }

  // -------------------------------------------------------------------------
  // Specs (each source module's cite_spec)
  // -------------------------------------------------------------------------

  const subsOf = (s) => [...(s || "").matchAll(/\(([^)]+)\)/g)].map((m) => m[1]);
  const dashes = (s) => s.replace(/[–—]/g, "-");

  function uscSpec(m) { return `${m[1]}:${dashes(m[2])}:${subsOf(m[3]).join(",")}`; }

  const RULE_SETS = {
    frcp: ["civ", "civ2", "civ3"], frcrmp: ["crim", "crim2", "crim3"],
    fre: ["evid", "evid2", "evid3"], frap: ["app", "app2", "app3"],
    frbp: ["bankr", "bankr2", "bankr3"],
  };

  function ruleSpec(m) {
    const g = m.groups || {};
    const set = Object.keys(RULE_SETS).find((k) => RULE_SETS[k].some((n) => g[n]));
    const rule = g.rule || g.rule2 || g.rule3;
    const subs = subsOf(g.subs || g.subs2 || g.subs3 || "");
    return `${set}:${rule}:${subs.join(",")}`;
  }

  const ROMAN = { i: 1, v: 5, x: 10, l: 50, c: 100, d: 500, m: 1000 };
  function toInt(token) {
    if (/^\d+$/.test(token)) return parseInt(token, 10);
    let total = 0, prev = 0;
    for (const ch of token.toLowerCase().split("").reverse()) {
      const v = ROMAN[ch] || 0;
      total += v >= prev ? v : -v;
      prev = Math.max(prev, v);
    }
    return total;
  }

  const ORD_ALT = "(?:" + Object.keys(T.ordinalWords)
    .sort((a, b) => b.length - a.length)
    .map((w) => w.replace(/-/g, "[- ]"))
    .join("|") + ")";
  const ORD_AMENDMENT = new RegExp("^" + ORD_ALT + "\\s+Amendment", "i");

  /** [kind, number, section] for a constitution match (constitution._spec_parts). */
  function constParts(matched) {
    const s = squash(matched);
    const low = s.toLowerCase();
    const secM = /(?:§|\bsec(?:tion|\.)?)\s*(\d+)/i.exec(s);
    const sec = secM ? secM[1] : "";
    if (low.includes("pmbl") || low.includes("preamble")) return ["pmbl", 0, ""];
    let m = ORD_AMENDMENT.exec(s);
    if (m) {
      const words = m[0].split(" ");
      words.pop();
      const n = T.ordinalWords[words.join(" ").toLowerCase().replace(/\s+/g, "-")];
      if (n) return ["amend", n, ""];
    }
    m = /(?:amend(?:ment)?s?|amdts?)\.?\s*([IVXLCDM]+|\d+)/i.exec(s);
    if (m) return ["amend", toInt(m[1]), sec];
    m = /(?:art(?:icle)?s?)\.?\s*([IVXLCDM]+|\d+)/i.exec(s);
    if (m) {
      const n = toInt(m[1]);
      if (n >= 1 && n <= 7) return ["art", n, sec];
    }
    return null;
  }

  function secSpec(vol, page, pin) {
    const spec = { page: +page };
    if (pin && +pin !== +page) spec.pin = +pin;
    spec.vol = +vol;
    return JSON.stringify(spec);
  }

  // -------------------------------------------------------------------------
  // Labels
  // -------------------------------------------------------------------------

  const ROMAN_OUT = [[1000, "M"], [900, "CM"], [500, "D"], [400, "CD"], [100, "C"],
    [90, "XC"], [50, "L"], [40, "XL"], [10, "X"], [9, "IX"], [5, "V"], [4, "IV"], [1, "I"]];
  function roman(n) {
    let out = "";
    for (const [v, sym] of ROMAN_OUT) while (n >= v) { out += sym; n -= v; }
    return out;
  }

  function label(kind, value, text) {
    const [first, section, subs] = splitSpec(value);
    const tail = subs.map((s) => `(${s})`).join("");
    switch (kind) {
      case "cite": {
        const [cite, pin] = value.split("@");
        return pin ? `${cite}, ${pin}` : cite;
      }
      case "engrep": {
        // The reprint's citation, which a nominate one opens too.
        const m = /^(\d+):(\d+)(?:@(\S+))?$/.exec(value);
        return m ? `${m[1]} Eng. Rep. ${m[2]}` + (m[3] ? `, ${m[3]}` : "") : squash(text);
      }
      case "usc": return `${first} U.S.C. § ${section}${tail}`;
      case "cfr": return `${first} C.F.R. § ${section}${tail}`;
      case "rule": return T.ruleSets[first] ? `${T.ruleSets[first].abbr} ${section}${tail}` : squash(text);
      case "const": {
        if (first === "pmbl") return "U.S. Const. pmbl.";
        const word = first === "amend" ? "amend." : "art.";
        const sub = subs.length ? `, § ${subs[0]}` : "";
        return `U.S. Const. ${word} ${roman(+section)}${sub}`;
      }
      default: return squash(text);
    }
  }

  function category(kind) {
    if (["cite", "url", "engrep", "recap", "fedcas", "scotus", "sec"].includes(kind)) return "case";
    return kind === "const" ? "const" : "statute";
  }

  // -------------------------------------------------------------------------
  // Detection
  // -------------------------------------------------------------------------

  // -------------------------------------------------------------------------
  // Unpublished opinions in RECAP (citations.iter_recap_cites and
  // iter_docket_cites): a federal court's, cited by Westlaw or LEXIS number
  // or by docket alone, found in CourtListener's archive of PACER by its
  // docket (or, printing none, its name), court and date.
  // -------------------------------------------------------------------------

  const NAME_CONNECTORS = new Set(T.nameConnectors);
  const NAME_STOPPERS = new Set(T.nameStoppers);
  const SPELLED_OUT = new Set(T.nameSpelledOut);
  const HEADING_WORDS = new Set(T.headingWords);
  const TABLE_ABBREVS = new Set(T.nameTableAbbrevs);
  const WORD_MAP = new Set(T.nameWordMap);
  const NAME_TRAIL = ",;:”\"’')]}";
  const NAME_LOOKBEHIND = 200;
  const MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };

  /** Python's str.strip / rstrip / lstrip with a set of characters. */
  const rstrip = (s, chars) => { let e = s.length; while (e > 0 && chars.includes(s[e - 1])) e--; return s.slice(0, e); };
  const lstrip = (s, chars) => { let b = 0; while (b < s.length && chars.includes(s[b])) b++; return s.slice(b); };
  const strip = (s, chars) => lstrip(rstrip(s, chars), chars);
  const isUpper = (ch) => !!ch && ch !== ch.toLowerCase() && ch === ch.toUpperCase();

  /** citations._is_name_abbreviation: whether a period-ended word can sit
   *  inside a case name ("Corp.", "U.S."), rather than end a sentence. */
  function isNameAbbreviation(core) {
    const word = rstrip(core, ".").replace(/’/g, "'").toLowerCase();
    if (!rx("nameAbbrev", "").test(core)) {
      return isUpper(core[0]) && core.endsWith(".") &&
        TABLE_ABBREVS.has(word.replace(/[^a-z]/g, "")) && !WORD_MAP.has(word);
    }
    if (word.includes(".")) return true;
    return !SPELLED_OUT.has(word) && !WORD_MAP.has(word);
  }

  function nameTokenOk(tok) {
    const low = strip(tok.toLowerCase(), ",;:");
    if (NAME_STOPPERS.has(low)) return false;
    return rx("nameToken", "").test(tok) || NAME_CONNECTORS.has(low);
  }

  /** Whether the words after a caption's "v." can all be the second party. */
  function partyOk(words) {
    return words.every((w, k) => nameTokenOk(w) ||
      (w === "at" && k > 0 && k < words.length - 1 &&
       rx("nameToken", "").test(words[k - 1]) && rx("nameToken", "").test(words[k + 1])));
  }

  function closesParenthetical(tok) {
    const core = rstrip(tok, ",;:”\"’'");
    return core.endsWith(")") && !core.includes("(");
  }

  function headingLine(line) {
    const letters = [...line].filter((ch) => /\p{L}/u.test(ch));
    const words = line.split(/\s+/).filter(Boolean);
    return words.length > 0 && (
      (letters.length > 1 && letters.every((ch) => ch === ch.toUpperCase())) ||
      HEADING_WORDS.has(words[words.length - 1].toLowerCase()));
  }

  function pastConnectors(toks, i) {
    while (i < toks.length && NAME_CONNECTORS.has(strip(toks[i].text.toLowerCase(), ",;:"))) i++;
    return i;
  }

  /** citations._past_headings: past a heading line the scan read into a
   *  name ("TABLE OF AUTHORITIES / Federal Cases / Barnes v. E-Systems"). */
  function pastHeadings(text, base, head, toks, i) {
    const last = toks[toks.length - 1].start;
    while (i < toks.length) {
      const lineEnd = head.indexOf("\n", toks[i].start);
      if (lineEnd < 0 || lineEnd > last) break;
      const at = base + toks[i].start;
      const lineStart = at > 0 ? text.lastIndexOf("\n", at - 1) + 1 : 0;
      if (text.slice(lineStart, at).trim()) break;
      if (!headingLine(head.slice(toks[i].start, lineEnd))) break;
      let k = i;
      while (k < toks.length && toks[k].start <= lineEnd) k++;
      i = pastConnectors(toks, k);
    }
    return i;
  }

  /** citations._case_name_start (without italics): where the case name
   *  introducing the citation at *citeStart* begins, or null. */
  function caseNameStart(text, citeStart) {
    const base = Math.max(0, citeStart - NAME_LOOKBEHIND);
    let head = text.slice(base, citeStart);
    const tail = /,\s*$/.exec(head);
    if (!tail) return null;
    head = head.slice(0, tail.index);
    const docket = rx("docketAfterName", "").exec(head);
    if (docket) head = head.slice(0, docket.index);
    if (!head.trim()) return null;
    const nov = rx("nameNoV", "").exec(head);
    if (nov) return base + nov.index;
    let leftEnd = null;
    const splits = [...head.matchAll(/(?<=[\p{L}\p{N}_.'’)\]])\s+vs?\.\s+/gu)];
    if (splits.length) {
      const split = splits[splits.length - 1];
      const right = head.slice(split.index + split[0].length);
      if (right && squash(right).length <= 70 && partyOk(right.split(/\s+/).filter(Boolean))) {
        leftEnd = split.index;
      }
    }
    const lone = leftEnd === null;
    if (lone) leftEnd = head.length;
    const toks = [...head.slice(0, leftEnd).matchAll(/\S+/g)].map((m) => ({ text: m[0], start: m.index }));
    let i = toks.length;
    while (i > 0) {
      const tok = toks[i - 1].text;
      if (tok.endsWith(";")) break;
      const low = strip(tok.replace(/[\ufffe\u00ad]/g, "").toLowerCase(), ",;:");
      if (NAME_STOPPERS.has(low) || NAME_STOPPERS.has(lstrip(low, "([\"“‘'"))) break;
      if (lone && i < toks.length && tok.endsWith(",")) break;
      if (closesParenthetical(tok)) break;
      if (rx("nameToken", "").test(tok)) {
        const core = rstrip(tok, NAME_TRAIL);
        if (core.endsWith(".") && !isNameAbbreviation(core)) break;
        i--;
        continue;
      }
      if (NAME_CONNECTORS.has(low)) { i--; continue; }
      break;
    }
    i = pastConnectors(toks, i);
    if (i < toks.length) i = pastHeadings(text, base, head, toks, i);
    if (i >= toks.length) return null;
    if (lone && toks.length - i > 4) return null;
    let start = toks[i].start;
    if ("([".includes(head[start])) start++;
    const glued = /^\d{1,3}(?=[A-Z][a-z]{2,})/.exec(head.slice(start));
    if (glued) start += glued[0].length;
    if (squash(head.slice(start, leftEnd)).length > 90) return null;
    return base + start;
  }

  /** Whether *name* holds a case citation (a grab that ran too far). */
  function holdsCaseCite(name) {
    if (rx("caseCite", "").test(name)) return true;
    for (const m of matchAll("broadCite", name)) if (validCaseReporter(m[2])) return true;
    return false;
  }

  /** citations._recap_name: the caption of the case cited at *citeStart*,
   *  or "" — only a whole caption ("X v. Y", "In re X") keys a search. */
  function recapName(text, citeStart) {
    const start = caseNameStart(text, citeStart);
    if (start === null) return "";
    let head = text.slice(start, citeStart).replace(/,\s*$/, "");
    const docket = rx("docketAfterName", "").exec(head);
    if (docket) head = head.slice(0, docket.index);
    let name = strip(head.replace(/\s+/g, " "), " ,;");
    for (let m; (m = rx("nameSignal", "").exec(name)); ) name = name.slice(m[0].length);
    if (name.length < 6 || holdsCaseCite(name)) return "";
    return rx("caption", "").test(name) ? name : "";
  }

  /** Python's json.dumps of a flat object of strings, so the extension's
   *  RECAP spec is the app's, character for character. */
  function pyJson(obj) {
    const str = (s) => JSON.stringify(s).replace(/[\u0080-\uffff]/g,
      (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
    return "{" + Object.entries(obj).map(([k, v]) => `${str(k)}: ${str(v)}`).join(", ") + "}";
  }

  const isoDate = (year, month, day) => `${year}-${pad(month, 2)}-${pad(+day, 2)}`;
  const wlKey = (m) => `${m[1]}|${m[2].replace(/\s+/g, "").toLowerCase()}|${m[3]}`;

  /** citations.iter_recap_cites: each Westlaw/LEXIS citation as [start,
   *  end, spec] — the RECAP lookup's JSON spec; "" for a state court's,
   *  left unlinked; null when it is no RECAP lookup (an ordinary cite). */
  function recapCites(text) {
    const index = new Map();
    const occurrences = [];
    for (const m of matchAll("wlCite", text)) {
      const key = wlKey(m);
      if (!index.has(key)) index.set(key, { cite: m[0].replace(/\s+/g, " ") });
      const info = index.get(key);
      const end = m.index + m[0].length;
      const before = text.slice(Math.max(0, m.index - 260), m.index).replace(/\s+/g, " ");
      const dm = rx("recapDocket", "").exec(before);
      if (dm && !("docket" in info)) info.docket = dm[1].trim();
      if (!("name" in info)) {
        const name = recapName(text, m.index);
        if (name) info.name = name;
      }
      const am = rx("recapAfter", "").exec(text.slice(end, end + 180).replace(/\s+/g, " "));
      if (am) {
        if (!("court" in info)) info.court = squash(am[1]);
        const month = MONTHS[am[2].slice(0, 3).toLowerCase()];
        if (month && !("date" in info)) info.date = isoDate(am[4], month, am[3]);
      }
      occurrences.push([m.index, end, key]);
    }
    return occurrences.map(([start, end, key]) => {
      const info = index.get(key);
      const court = info.court || "";
      if (stateCourt(court)) return [start, end, ""];
      const courtId = federalCourtId(court);
      let spec = null;
      if (info.date && ((info.docket && (courtId || !court)) || (!info.docket && info.name && courtId))) {
        const fields = { cite: info.cite, date: info.date };
        if (info.docket) fields.docket = info.docket;
        if (courtId) fields.court = courtId;
        if (info.name) fields.name = info.name;
        spec = pyJson(fields);
      }
      return [start, end, spec];
    });
  }

  /** citations.iter_docket_cites: a federal slip opinion cited by docket
   *  alone, "No. 23-1971 (4th Cir. Feb. 12, 2024)", as [start, end, spec]. */
  function docketCites(text) {
    const out = [];
    for (const m of matchAll("docketCite", text)) {
      const courtId = federalCourtId(squash(m[3]));
      const month = MONTHS[m[4].slice(0, 3).toLowerCase()];
      if (!courtId || !month) continue;
      const fields = { docket: m[1].trim(), date: isoDate(m[6], month, m[5]), court: courtId };
      const name = recapName(text, m.index);
      if (name) fields.name = name;
      out.push([m.index, m.index + m[0].length, pyJson(fields)]);
    }
    return out;
  }

  /** Where a Westlaw/LEXIS citation ending at *end* ends: past its star-page
   *  pin and its court/date parenthetical (citations._recap_end). */
  function recapEnd(text, end) {
    const pin = matchAt("recapPin", text, end);
    if (pin) end = pin.index + pin[0].length;
    const paren = matchAt("courtYearParen", text, end);
    return paren ? paren.index + paren[0].length : end;
  }

  const COURT_YEAR_PAREN = /\s*\((?:[^()]{0,60}?[\s.])?(?:1[6-9]|20)\d{2}\)/y;

  /** A quick look for anything worth reading: most pages have no citation,
   *  and need not be read at all. */
  const CANDIDATE = /\d\s*(?:U\.\s?S\.|S\.\s?Ct\.|L\.\s?Ed|F\.|[A-Z][A-Za-z.'’]*\.?\s*\d|Stat\.|Fed\.\s?Reg|Eng\.\s?Rep|E\.\s?R\.|S\.\s?E\.\s?C\.|WL\s)|§|U\.\s?S\.\s?C|C\.\s?F\.\s?R|Const|Amendment|Fed\.?\s*R\.|Federal Rules? of|Article\s+[IV]|\bNos?\.\s*\w/;

  function mightHaveCitations(text) {
    return CANDIDATE.test(text || "");
  }

  function overlaps(spans, s, e) {
    for (const [a, b] of spans) if (s < b && a < e) return true;
    return false;
  }

  /**
   * The citations in *text*, as {start, end, kind, value, url, label,
   * category} with UTF-16 offsets into *text* — the shape the app's
   * /detect returns — in document order, overlaps resolved first/longest.
   */
  function detect(text) {
    text = text || "";
    if (!mightHaveCitations(text)) return [];
    const found = [];           // [start, end, kind, value]
    const claimed = [];

    const claim = (s, e, kind, value) => { claimed.push([s, e]); found.push([s, e, kind, value]); };

    // Sources a reporter's shape would otherwise claim as a case.  First the
    // English Reports (eng_rep.iter_cites): the reprint's citations with
    // their pins, its short forms in a case cited in full, and the
    // citations written beside them — the same cases in the nominate
    // reports the reprint collects ("2 Russ. & M. 639, 39 Eng. Rep. 538"),
    // which open where the reprint's citation does.
    const reprint = [];
    const reprinted = new Map();    // volume → first pages cited in full
    for (const m of matchAll("engRep", text)) {
      let end = m.index + m[0].length;
      let value = `${m[1]}:${m[2]}`;
      const p = matchAt("engRepPin", text, end);
      if (p) { value += "@" + p[1]; end = p.index + p[0].length; }
      reprint.push([m.index, end, value]);
      if (!reprinted.has(+m[1])) reprinted.set(+m[1], []);
      reprinted.get(+m[1]).push(+m[2]);
    }
    for (const m of matchAll("engRepShort", text)) {
      const vol = +m[1], page = +m[2];
      const near = (reprinted.get(vol) || []).filter((s) => s <= page && page <= s + T.engRepShortSpan);
      if (near.length) reprint.push([m.index, m.index + m[0].length, `${vol}:${Math.max(...near)}@${page}`]);
    }
    for (const [s, e, value] of reprint) claim(s, e, "engrep", value);
    const parallel = (m, value) => {
      const [cs, ce] = m.indices.groups.cite;
      if (!overlaps(claimed, cs, ce)) claim(cs, ce, "engrep", value);
    };
    // Just before one first: a citation between two is the second's.
    for (const [s, , value] of reprint) {
      const before = rx("engRepBefore", "gd");
      before.lastIndex = Math.max(0, s - T.engRepReach);
      const m = before.exec(text.slice(0, s));
      if (m && parallelReporter(m.groups.rep)) parallel(m, value);
    }
    // Just after one, only a reporter of the nominate reports: else it is
    // as likely the next authority of a string cite.
    for (const [, e, value] of reprint) {
      const m = matchAt("engRepAfter", text, e, "yd");
      if (m && nominateReporter(m.groups.rep) && parallelReporter(m.groups.rep)) parallel(m, value);
    }
    for (const m of matchAll("sec", text)) {
      if (m.groups.short) continue;       // "8 S.E.C. at 915": needs the index
      let end = m.index + m[0].length;
      let pin = "";
      const p = matchAt("pinAfter", text, end);
      if (p) { pin = p[1]; end = p.index + p[0].length; }
      claim(m.index, end, "sec", secSpec(m.groups.vol, m.groups.page, pin));
    }
    for (const m of matchAll("stat", text)) {
      const vol = +m[1], page = +m[2];
      if (vol >= 1 && vol <= T.statMaxVolume && page >= 1) {
        claim(m.index, m.index + m[0].length, "statpdf", `https://www.govinfo.gov/link/statute/${vol}/${page}`);
      }
    }
    for (const m of matchAll("fedReg", text)) {
      const vol = +m[1].replace(/,/g, ""), page = +m[2].replace(/,/g, "");
      if (vol >= 1 && page >= 1) {
        claim(m.index, m.index + m[0].length, "frpdf", `https://www.govinfo.gov/link/fr/${vol}/${page}?link-type=pdf`);
      }
    }
    // Unpublished opinions by Westlaw/LEXIS number (citations.iter_recap_cites):
    // a federal court's opens CourtListener's search of RECAP for it; a
    // state court's is left unlinked, but claimed so no other reading links
    // it; the rest are ordinary citations.  Then a federal slip opinion
    // cited by docket alone.
    for (const [start, numberEnd, spec] of recapCites(text)) {
      if (overlaps(claimed, start, numberEnd)) continue;
      if (spec === "") { claimed.push([start, recapEnd(text, numberEnd)]); continue; }
      if (spec) { claim(start, recapEnd(text, numberEnd), "recap", spec); continue; }
      let end = numberEnd;
      let value = squash(text.slice(start, numberEnd));
      const p = matchAt("pinAfter", text, end);         // ", at *12"
      if (p) { value += "@" + p[1]; end = p.index + p[0].length; }
      claim(start, end, "cite", value);
    }
    for (const [start, end, spec] of docketCites(text)) {
      if (!overlaps(claimed, start, end)) claim(start, end, "recap", spec);
    }
    for (const m of matchAll("earlyFedCite", text)) {
      if (overlaps(claimed, m.index, m.index + m[0].length)) continue;
      claim(m.index, m.index + m[0].length, "cite", `${m[1]} ${canonicalReporter(m[2])} ${m[3]}`);
    }

    // The Code and the C.F.R.: "42 U.S.C. Section 1983" has a reporter's
    // shape too ("U.S.C. Section", page 1983), and must not read as a case.
    for (const name of ["usc", "cfr"]) {
      for (const m of matchAll(name, text)) claimed.push([m.index, m.index + m[0].length]);
    }

    // Cases: the named reporters, the early Supreme Court's, then any other
    // reporter the app would accept (citations._iter_case_cites).
    const cases = [];
    const taken = (s, e) => overlaps(claimed, s, e) || cases.some((c) => s < c.end && c.start < e);
    for (const name of ["caseCite", "nominativeParallel", "usNominativeParallel", "nominativeCite"]) {
      for (const m of matchAll(name, text)) {
        const s = m.index, e = s + m[0].length;
        if (!taken(s, e)) cases.push({ start: s, end: e, m });
      }
    }
    for (const m of matchAll("broadCite", text)) {
      const s = m.index, e = s + m[0].length;
      if (!validCaseReporter(m[2]) || taken(s, e)) continue;
      if (bareWordReporter(m[2]) && !atCitationStart(text, s)) continue;
      if (stateLexisReporter(m[2])) { claimed.push([s, e]); continue; }   // a state court's, by LEXIS number
      cases.push({ start: s, end: e, m });
    }
    const index = new Map();      // "vol|reporter key" → first pages
    const heads = [...matchAll("runningHead", text)].map((m) => [m.index, m.index + m[0].length]);
    for (const c of cases) {
      if (overlaps(heads, c.start, c.end)) continue;
      const cite = caseMatchText(c.m);
      let end = c.end;
      let value = cite;
      const p = matchAt("pinAfter", text, end);
      if (p) { value += "@" + p[1]; end = p.index + p[0].length; }
      COURT_YEAR_PAREN.lastIndex = end;
      const paren = COURT_YEAR_PAREN.exec(text);
      if (paren) end = paren.index + paren[0].length;
      found.push([c.start, end, "cite", value]);
      const key = `${c.m[1]}|${reporterKey(reporterWithoutSuffix(c.m[2]))}`;
      if (!index.has(key)) index.set(key, []);
      index.get(key).push(+c.m[3]);
    }
    // Short forms, "410 U.S. at 152": a page of a case cited in full.
    for (const m of matchAll("broadShortCite", text)) {
      const s = m.index, e = s + m[0].length;
      if (overlaps(claimed, s, e) || stateLexisReporter(m[2])) continue;
      const pages = index.get(`${m[1]}|${reporterKey(m[2])}`);
      if (!pages) continue;
      const pin = +m[3];
      const below = pages.filter((p) => p <= pin);
      const first = below.length ? Math.max(...below) : Math.min(...pages);
      const rep = squash(m[2]).replace(/U\. S\./g, "U.S.");
      found.push([s, e, "cite", `${m[1]} ${rep} ${first}` + (pin !== first ? `@${pin}` : "")]);
    }

    for (const m of matchAll("usc", text)) found.push([m.index, m.index + m[0].length, "usc", uscSpec(m)]);
    for (const m of matchAll("cfr", text)) found.push([m.index, m.index + m[0].length, "cfr", uscSpec(m)]);
    for (const m of matchAll("rule", text)) found.push([m.index, m.index + m[0].length, "rule", ruleSpec(m)]);
    // The Constitution where it is cited — "U.S. Const. art. I, § 3",
    // "Article I, Section 8" — never a mention in prose ("the First
    // Amendment", "Article III standing"); each provision only the first time
    // (below), as the app does (constitution.is_citation).
    for (const m of matchAll("const", text)) {
      if (!rx("constCitation", "").test(m[0])) continue;
      const parts = constParts(m[0]);
      const [kind, n, sec] = parts || ["pmbl", 0, ""];
      found.push([m.index, m.index + m[0].length, "const", `${kind}:${n}:${sec}`]);
    }

    found.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
    const out = [];
    const constLinked = new Set();
    let pos = 0;
    for (const [start, end, kind, value] of found) {
      if (start < pos) continue;
      if (kind === "const") {
        if (constLinked.has(value)) { pos = end; continue; }
        constLinked.add(value);
      }
      const matched = text.slice(start, end);
      out.push({
        start, end, kind, value,
        url: browserUrl(kind, value, matched),
        label: label(kind, value, matched),
        category: category(kind),
      });
      pos = end;
    }
    return out;
  }

  // -------------------------------------------------------------------------
  // Links to legal material already on a page
  // -------------------------------------------------------------------------

  const RULE_PATHS = Object.fromEntries(Object.entries(T.ruleSets).map(([k, v]) => [v.path, k]));

  function reporterFromSlug(slug) {
    const key = looseKey(decodeURIComponent(slug));
    return Object.prototype.hasOwnProperty.call(T.reporterCanonical, key) ? T.reporterCanonical[key] : "";
  }

  function caseAction(vol, rep, page) {
    if (!rep) return null;
    const value = `${+vol} ${rep} ${+page}`;
    return { kind: "cite", value, label: value, url: browserUrl("cite", value) };
  }

  function statuteAction(kind, value) {
    return { kind, value, label: label(kind, value, value), url: browserUrl(kind, value) };
  }

  /**
   * What a link to a legal site points at, when its address says — a case
   * on Justia, CourtListener, Cornell, the Caselaw Access Project or the
   * Library of Congress; a section of the U.S. Code, the C.F.R. or the
   * federal rules; a page of the Statutes at Large or the Federal Register —
   * as an action like detect()'s, or null.
   */
  function actionForUrl(href) {
    let u;
    try { u = new URL(href); } catch (e) { return null; }
    if (!/^https?:$/.test(u.protocol)) return null;
    const host = u.hostname.replace(/^www\./, "");
    const path = u.pathname;
    let m;
    if (host === "supreme.justia.com" && (m = /^\/cases\/federal\/us\/(\d+)\/(\d+)\/?$/.exec(path))) {
      return caseAction(m[1], "U.S.", m[2]);
    }
    if (host === "courtlistener.com" && (m = /^\/c\/([^/]+)\/(\d+)\/(\d+)\/?$/.exec(path))) {
      return caseAction(m[2], reporterFromSlug(m[1]), m[3]);
    }
    if (host === "law.cornell.edu") {
      if ((m = /^\/supremecourt\/text\/(\d+)\/(\d+)\/?$/.exec(path))) return caseAction(m[1], "U.S.", m[2]);
      if ((m = /^\/uscode\/text\/(\d+)\/([\w.-]+)\/?$/.exec(path))) return statuteAction("usc", `${m[1]}:${m[2]}:`);
      if ((m = /^\/cfr\/text\/(\d+)\/(\d+[a-z]?\.[\w.-]+)\/?$/i.exec(path))) return statuteAction("cfr", `${m[1]}:${m[2]}:`);
      if ((m = /^\/rules\/([a-z]+)\/rule_(\d+(?:\.\d+)?)\/?$/.exec(path)) && RULE_PATHS[m[1]]) {
        return statuteAction("rule", `${RULE_PATHS[m[1]]}:${m[2]}:`);
      }
      if ((m = /^\/constitution\/(amendment|article)([ivxl]+)\/?$/i.exec(path))) {
        return statuteAction("const", `${m[1].toLowerCase() === "amendment" ? "amend" : "art"}:${toInt(m[2])}:`);
      }
    }
    if (host === "uscode.house.gov") {
      const req = decodeURIComponent(u.search);
      if ((m = /granuleid:USC-(?:prelim-)?title(\d+)[a-z]?-section([\w.-]+)/i.exec(req))) {
        return statuteAction("usc", `${m[1]}:${m[2]}:`);
      }
      if ((m = /title:(\d+)\s+section:([\w.-]+)/i.exec(req))) return statuteAction("usc", `${m[1]}:${m[2]}:`);
    }
    if (host === "ecfr.gov" && (m = /^\/current\/title-(\d+)(?:\/.*)?\/section-([\w.-]+)\/?$/.exec(path))) {
      return statuteAction("cfr", `${m[1]}:${m[2]}:`);
    }
    if (host === "govinfo.gov") {
      if ((m = /^\/link\/statute\/(\d+)\/(\d+)/.exec(path))) {
        return statuteAction("statpdf", `https://www.govinfo.gov/link/statute/${+m[1]}/${+m[2]}`);
      }
      if ((m = /^\/link\/fr\/(\d+)\/(\d+)/.exec(path))) {
        return statuteAction("frpdf", `https://www.govinfo.gov/link/fr/${+m[1]}/${+m[2]}?link-type=pdf`);
      }
      if ((m = /\/USCODE-\d{4}-title(\d+)[^/]*-sec([\w.-]+?)(?:\.(?:htm|pdf))?$/.exec(path))) {
        return statuteAction("usc", `${m[1]}:${m[2]}:`);
      }
    }
    if (host === "cite.case.law" && (m = /^\/([a-z0-9-]+)\/(\d+)\/(\d+)\/?$/.exec(path))) {
      return caseAction(m[2], reporterFromSlug(m[1]), m[3]);
    }
    if (host === "static.case.law" && (m = /^\/([a-z0-9-]+)\/(\d+)\/(?:cases|case-pdfs)\/(\d+)-\d+\.(?:json|pdf)$/.exec(path))) {
      return caseAction(m[2], reporterFromSlug(m[1]), m[3]);
    }
    if (/^(?:tile|cdn)\.loc\.gov$|^loc\.gov$/.test(host) && (m = /usrep(\d{3})(\d{3,4})/.exec(path))) {
      return caseAction(m[1], "U.S.", m[2]);
    }
    if (host === "govinfo.gov" &&
        (m = /^\/(?:link\/usreports\/(\d+)\/(\d+)|content\/pkg\/USREPORTS-(\d+)\/pdf\/USREPORTS-\d+-(\d+)\.pdf)$/.exec(path))) {
      return caseAction(m[1] || m[3], "U.S.", m[2] || m[4]);
    }
    return null;
  }

  root.GetCasesCitations = {
    detect, mightHaveCitations, actionForUrl, browserUrl, label, category, caseUrls,
    // For the tests.
    _internal: {
      caseMatchText, validCaseReporter, reporterKey, canonicalReporter, constParts, pyQuote, splitSpec,
      casePdfUrls, caseLawSlug,
    },
  };
})(globalThis);
