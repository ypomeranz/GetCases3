// GetCases citation reading, for when the app isn't running.
//
// While GetCases runs, the extension asks it to read a page's citations (the
// app's browser_bridge), which finds everything the app's own viewers link.
// Without it, this file reads the common ones itself — cases, the U.S. Code,
// the C.F.R., the federal rules, the Constitution, the Statutes at Large, the
// Federal Register, the English Reports and the SEC's reports — with the
// app's own regular expressions (patterns.js, exported from the Python), and
// sends a click to the web page the app would open (browser_links.py).
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

  /** The pattern *name* matched exactly at *pos* in *text*, or null. */
  function matchAt(name, text, pos) {
    const re = rx(name, "y");
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

  const SCHOLAR_SEARCH = "https://scholar.google.com/scholar?q=";
  const CONSTITUTION_URL = "https://constitution.congress.gov/constitution/";
  const ENG_REP_URL = "https://www.commonlii.org/uk/cases/EngR";

  /** Python's urllib.parse.quote: everything but letters, digits, "_.-~"
   *  and "/" percent-encoded. */
  function pyQuote(s) {
    return encodeURIComponent(s)
      .replace(/%2F/g, "/")
      .replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
  }

  const google = (text) => "https://www.google.com/search?q=" + pyQuote(squash(text));

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
        return SCHOLAR_SEARCH + pyQuote(`"${value.split("@")[0]}"`);
      case "sec":
        return T.secCatalogUrl;
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

  const COURT_YEAR_PAREN = /\s*\((?:[^()]{0,60}?[\s.])?(?:1[6-9]|20)\d{2}\)/y;

  /** A quick look for anything worth reading: most pages have no citation,
   *  and need not be read at all. */
  const CANDIDATE = /\d\s*(?:U\.\s?S\.|S\.\s?Ct\.|L\.\s?Ed|F\.|[A-Z][A-Za-z.'’]*\.?\s*\d|Stat\.|Fed\.\s?Reg|Eng\.\s?Rep|E\.\s?R\.|S\.\s?E\.\s?C\.|WL\s)|§|U\.\s?S\.\s?C|C\.\s?F\.\s?R|Const|Amendment|Fed\.?\s*R\.|Federal Rules? of|Article\s+[IV]/;

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

    // Sources a reporter's shape would otherwise claim as a case.
    for (const m of matchAll("engRep", text)) {
      claim(m.index, m.index + m[0].length, "engrep", `${m[1]}:${m[2]}`);
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
    for (const m of matchAll("wlCite", text)) {
      let end = m.index + m[0].length;
      if (overlaps(claimed, m.index, end)) continue;
      let value = squash(m[0]);
      const p = matchAt("pinAfter", text, end);         // ", at *12"
      if (p) { value += "@" + p[1]; end = p.index + p[0].length; }
      claim(m.index, end, "cite", value);
    }
    for (const m of matchAll("earlyFedCite", text)) {
      if (overlaps(claimed, m.index, m.index + m[0].length)) continue;
      claim(m.index, m.index + m[0].length, "cite", `${m[1]} ${canonicalReporter(m[2])} ${m[3]}`);
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
      if (overlaps(claimed, s, e)) continue;
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
    const amendments = new Set();
    for (const m of matchAll("const", text)) {
      const parts = constParts(m[0]);
      const [kind, n, sec] = parts || ["pmbl", 0, ""];
      // A prose "First Amendment" is linked the first time only; a formal
      // citation every time.
      const prose = !squash(m[0]).toLowerCase().includes("const");
      if (kind === "amend" && prose && !sec && amendments.has(n)) continue;
      if (kind === "amend") amendments.add(n);
      found.push([m.index, m.index + m[0].length, "const", `${kind}:${n}:${sec}`]);
    }

    found.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
    const out = [];
    let pos = 0;
    for (const [start, end, kind, value] of found) {
      if (start < pos) continue;
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
    if ((host === "tile.loc.gov" || host === "loc.gov") && (m = /usrep(\d{3})(\d{3,4})/.exec(path))) {
      return caseAction(m[1], "U.S.", m[2]);
    }
    return null;
  }

  root.GetCasesCitations = {
    detect, mightHaveCitations, actionForUrl, browserUrl, label, category,
    // For the tests.
    _internal: { caseMatchText, validCaseReporter, reporterKey, canonicalReporter, constParts, pyQuote, splitSpec },
  };
})(globalThis);
