// The extension's settings, and the sites it leaves alone: shared by the
// service worker, the content script and the toolbar popup.
//
// A classic script, so all three can load it: it sets
// globalThis.GetCasesSettings.

(function (root) {
  "use strict";

  /** The legal research services, left alone unless the reader says
   *  otherwise: they link their own citations, and their pages are
   *  applications the extension's links would only get in the way of. */
  const RESEARCH_SITES = [
    "westlaw.com",
    "lexis.com",
    "lexisnexis.com",
    "bloomberglaw.com",
    "vlex.com",
    "fastcase.com",
  ];

  const DEFAULTS = {
    enabled: true,          // link citations on web pages
    disabledSites: RESEARCH_SITES,  // …except on these sites
    interceptLinks: true,   // a page's own links to legal sources open in GetCases
    pdfViewer: true,        // open PDFs in the extension's viewer
    port: 21983,
  };

  /** *host* as the list keeps it: lower case, without "www." or a final dot. */
  const bare = (host) => String(host || "").toLowerCase().replace(/\.$/, "").replace(/^www\./, "");

  /** Whether the site *site* (an entry of the list) covers *host*: the site
   *  itself, any subdomain of it, and the same reached through a library's
   *  proxy, which serves "next.westlaw.com" as
   *  "next-westlaw-com.proxy.example.edu" (or, in older ones,
   *  "next.westlaw.com.proxy.example.edu"). */
  function covers(site, host) {
    if (!site || !host) return false;
    if (host === site || host.endsWith("." + site)) return true;
    const dashed = site.replace(/\./g, "-");
    const first = host.split(".")[0];
    return first === dashed || first.endsWith("-" + dashed) ||
      host.startsWith(site + ".") || host.includes("." + site + ".");
  }

  /** The entries of *sites* that cover *host*. */
  function sitesCovering(host, sites) {
    const h = bare(host);
    return (sites || []).filter((site) => covers(site, h));
  }

  /** Whether the extension leaves *host* alone. */
  function siteExcluded(host, sites) {
    return sitesCovering(host, sites).length > 0;
  }

  /** What the reader typed (a domain, or an address copied from the
   *  browser) as an entry of the list, or "" when it names no site. */
  function normalizeSite(input) {
    let s = String(input || "").trim().replace(/^\*\./, "");
    if (!s) return "";
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = "http://" + s;
    let host;
    try {
      host = new URL(s).hostname;     // lower case, and punycode
    } catch (e) {
      return "";
    }
    host = bare(host);
    if (!/^[a-z0-9.-]+$/.test(host) || !/[a-z0-9]\.[a-z0-9]/.test(host)) return "";
    return host;
  }

  root.GetCasesSettings = {
    DEFAULTS, RESEARCH_SITES, siteExcluded, sitesCovering, normalizeSite,
  };
})(globalThis);
