// The extension's service worker: the one part of it that talks to GetCases.
//
// GetCases listens on this machine only (127.0.0.1, port 21983 unless the
// reader changed it), and only to requests carrying the X-GetCases header
// (see the app's browser_bridge.py).  Pages' content scripts cannot reach it
// themselves — a request from one is the page's, and Chrome would ask the
// reader for local-network access — so they ask here.
//
// Also here: the right-click "Look up in GetCases"; sending PDFs Chrome
// opens to the extension's own viewer, which links their citations;
// without GetCases, a case's scan: before a case opens on the web, the
// sites its scan would be at (the Library of Congress, GovInfo, the Caselaw
// Access Project) are asked whether they have it; and, with GetCases,
// CommonLII's CloudFlare check and Google Scholar's CAPTCHA passed here,
// handed to the app.

importScripts("settings.js", "patterns.js", "citations.js");

const { DEFAULTS, RESEARCH_SITES } = GetCasesSettings;

function getSettings() {
  return chrome.storage.sync.get(DEFAULTS);
}

/** The research services joined the sites left alone in version 1.2; a copy
 *  of the extension from before then, whose reader had already turned it
 *  off on a site of their own, gets them added once.  (One that never had
 *  a list of its own has them by default.) */
async function addResearchSites() {
  const { disabledSites, sitesVersion = 0 } =
    await chrome.storage.sync.get(["disabledSites", "sitesVersion"]);
  if (sitesVersion >= 1) return;
  const update = { sitesVersion: 1 };
  if (Array.isArray(disabledSites)) {
    update.disabledSites = [...disabledSites, ...RESEARCH_SITES.filter((s) => !disabledSites.includes(s))];
  }
  await chrome.storage.sync.set(update);
}

function hostOf(url) {
  try { return new URL(url).hostname; } catch (e) { return ""; }
}

// ---------------------------------------------------------------------------
// GetCases
// ---------------------------------------------------------------------------

let status = { at: 0, running: false };

async function appFetch(path, { body, timeout = 3000 } = {}) {
  const { port } = await getSettings();
  const init = {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-GetCases": "1" },
    signal: AbortSignal.timeout(timeout),
    cache: "no-store",
    credentials: "omit",
  };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(`http://127.0.0.1:${port}${path}`, init);
  if (!response.ok) throw new Error(`GetCases answered ${response.status}`);
  return response.json();
}

function showStatus(running) {
  chrome.action.setBadgeText({ text: running ? "on" : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#15803d" });
  chrome.action.setTitle({
    title: running
      ? "GetCases is running: citations open in the app"
      : "GetCases isn't running: citations open on the web",
  });
}

/** Whether GetCases is running, asking it at most every two seconds. */
async function appRunning(force = false) {
  if (!force && Date.now() - status.at < 2000) return status.running;
  let running = false;
  try {
    const reply = await appFetch("/status", { timeout: 800 });
    running = reply && reply.app === "GetCases";
  } catch (e) {
    running = false;
  }
  status = { at: Date.now(), running };
  showStatus(running);
  return running;
}

/** The app's reading of a page's text, or null. */
async function appDetect(text, italic) {
  if (!(await appRunning())) return null;
  try {
    const reply = await appFetch("/detect", { body: { text, italic }, timeout: 30000 });
    return Array.isArray(reply.links) ? reply.links : null;
  } catch (e) {
    status.at = 0;
    return null;
  }
}

/** Hand a citation to GetCases; whether it took it. */
async function appOpen(link) {
  try {
    const reply = await appFetch("/open", {
      body: { kind: link.kind || "", value: link.value || "", text: link.text || "" },
      timeout: 2500,
    });
    status = { at: Date.now(), running: true };
    showStatus(true);
    return !!(reply && reply.ok);
  } catch (e) {
    status = { at: Date.now(), running: false };
    showStatus(false);
    return false;
  }
}

/** Whether *url* is a page to open: never a script or a browser page. */
function webPage(url) {
  return /^(https?|file):/i.test(url || "");
}

/** Open *url* in a tab of its own, beside the one the reader is in (and
 *  behind it, unless *active*). */
function openTab(url, sender, active = true) {
  if (!webPage(url)) return Promise.resolve();
  const props = { url, active };
  if (sender && sender.tab) {
    props.index = sender.tab.index + 1;
    props.openerTabId = sender.tab.id;
    props.windowId = sender.tab.windowId;
  }
  return chrome.tabs.create(props);
}

// ---------------------------------------------------------------------------
// A case on the web: its scan where there is one
// ---------------------------------------------------------------------------

/** Whether *url* answers with a PDF: true or false, or null when that can't
 *  be told (no answer in time, no network).  GovInfo answers a scan it
 *  hasn't with a web page, not a 404, so the type is what tells. */
async function isPdf(url) {
  try {
    const response = await fetch(url, {
      method: "HEAD",
      redirect: "follow",
      credentials: "omit",
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) return false;
    return /\bpdf\b/i.test(response.headers.get("content-type") || "");
  } catch (e) {
    return null;
  }
}

/**
 * The page to open for the case citation *cite* ("201 F. 20@24"): the first
 * of its scans that is there — the U.S. Reports at the Library of Congress
 * or GovInfo, a lower court's at the Caselaw Access Project, which holds
 * only some cases of a reporter — and Google Scholar's case-law search when
 * none is.  The scans are asked about at once, and taken in order.
 */
async function caseUrl(cite) {
  const urls = GetCasesCitations.caseUrls(cite);
  const scholar = urls.pop();
  const answers = await Promise.all(urls.map(isPdf));
  // A scan that couldn't be asked about is opened all the same: the
  // browser says why, if it can't reach it either.
  const found = urls.find((_url, i) => answers[i] !== false);
  return found || scholar;
}

/** The web page for *link*, an action as detect() gives it. */
function linkUrl(link) {
  if (link.kind === "cite" && link.value) return caseUrl(link.value);
  return link.url || GetCasesCitations.browserUrl(link.kind, link.value, link.text);
}

/**
 * A citation clicked on a page: to GetCases if it is running (unless the
 * reader asked for the web page, with Ctrl/Cmd or the middle button);
 * otherwise our own link opens the web page the app would, and a page's own
 * link goes where it pointed.
 */
async function openLink(message, sender) {
  const link = message.link || {};
  if (!message.web && (await appOpen(link))) return { opened: "app" };
  if (message.from === "anchor" && webPage(message.href)) {
    // Where the link would have gone: its own tab, unless it asked for a
    // new one or sits in a frame (whose page this can't navigate).
    if (message.newTab || !sender.tab || sender.frameId) await openTab(message.href, sender);
    else await chrome.tabs.update(sender.tab.id, { url: message.href });
    return { opened: "web" };
  }
  const url = await linkUrl(link);
  if (url) await openTab(url, sender, !message.behind);
  return { opened: "web" };
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  const handle = async () => {
    switch (message && message.type) {
      case "status":
        return { running: await appRunning(!!message.force) };
      case "detect":
        return { links: await appDetect(message.text || "", message.italic || null) };
      case "open":
        return openLink(message, sender);
      case "openPdf":
        await openPdfViewer(message.url, sender.tab ? sender.tab.id : null);
        return { ok: true };
      case "pdfBypass":
        await bypassPdf(message.url);
        return { ok: true };
      default:
        return null;
    }
  };
  handle().then(reply, (e) => reply({ error: String(e) }));
  return true;
});

// ---------------------------------------------------------------------------
// Right-click
// ---------------------------------------------------------------------------

function setUpMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "getcases-lookup",
      title: "Look up “%s” in GetCases",
      contexts: ["selection"],
    });
    chrome.contextMenus.create({
      id: "getcases-pdf-link",
      title: "Open PDF with citation links",
      contexts: ["link"],
    });
  });
}

chrome.runtime.onInstalled.addListener(() => {
  setUpMenus();
  addResearchSites();
});
chrome.runtime.onStartup.addListener(setUpMenus);

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const sender = { tab };
  if (info.menuItemId === "getcases-lookup") {
    const text = (info.selectionText || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    if (await appOpen({ text })) return;
    // No GetCases: the page for the citation selected, or Google Scholar's
    // search of case law for the words (a case's name, say).
    const found = GetCasesCitations.detect(text);
    const url = found.length
      ? await linkUrl(found[0])
      : "https://scholar.google.com/scholar?hl=en&as_sdt=2006&q=" + encodeURIComponent(text);
    await openTab(url, sender);
  } else if (info.menuItemId === "getcases-pdf-link" && info.linkUrl) {
    await openTab(viewerUrl(info.linkUrl), sender);
  }
});

// ---------------------------------------------------------------------------
// PDFs
// ---------------------------------------------------------------------------

function viewerUrl(url) {
  return chrome.runtime.getURL("pdf/viewer.html") + "?file=" + encodeURIComponent(url);
}

async function openPdfViewer(url, tabId) {
  if (!url) return;
  if (tabId !== null && tabId !== undefined) await chrome.tabs.update(tabId, { url: viewerUrl(url) });
  else await chrome.tabs.create({ url: viewerUrl(url) });
}

/** Let *url* open in Chrome's own viewer once: the viewer's "Open in
 *  Chrome's viewer" button. */
async function bypassPdf(url) {
  const { pdfBypass = {} } = await chrome.storage.session.get("pdfBypass");
  pdfBypass[url] = Date.now() + 60000;
  await chrome.storage.session.set({ pdfBypass });
}

async function bypassed(url) {
  const { pdfBypass = {} } = await chrome.storage.session.get("pdfBypass");
  const until = pdfBypass[url];
  if (!until) return false;
  delete pdfBypass[url];
  await chrome.storage.session.set({ pdfBypass });
  return until > Date.now();
}

function header(headers, name) {
  const h = (headers || []).find((x) => x.name.toLowerCase() === name);
  return h ? h.value || "" : "";
}

/** Whether a response is a PDF Chrome would show in its viewer (not one
 *  it downloads): the test Mozilla's pdf.js extension uses. */
function isPdfResponse(details) {
  if (details.method !== "GET" || details.statusCode < 200 || details.statusCode >= 300) return false;
  const type = header(details.responseHeaders, "content-type").toLowerCase();
  const disposition = header(details.responseHeaders, "content-disposition");
  if (/^\s*attachment/i.test(disposition)) return false;
  if (/^application\/(x-)?pdf\b/.test(type)) return true;
  if (/^(application|binary)\/octet-stream\b/.test(type)) {
    let path = "";
    try { path = new URL(details.url).pathname; } catch (e) { /* not a URL */ }
    return /\.pdf$/i.test(path) || /filename\*?=[^;]*\.pdf\b/i.test(disposition);
  }
  return false;
}

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.tabId < 0 || !isPdfResponse(details)) return;
    (async () => {
      const { pdfViewer, disabledSites } = await getSettings();
      if (!pdfViewer || (await bypassed(details.url))) return;
      // A PDF from a site left alone, or opened from one, is left to
      // Chrome's viewer too.
      const excluded = (url) => GetCasesSettings.siteExcluded(hostOf(url), disabledSites);
      if (excluded(details.url) || (details.initiator && excluded(details.initiator))) return;
      await openPdfViewer(details.url, details.tabId);
    })();
  },
  { urls: ["http://*/*", "https://*/*"], types: ["main_frame"] },
  ["responseHeaders"],
);

// PDFs on this computer (file:// has no web requests to watch), when the
// reader has let the extension read files.
chrome.tabs.onUpdated.addListener(async (tabId, change) => {
  const url = change.url || "";
  if (!/^file:\/\/.*\.pdf$/i.test(url)) return;
  const { pdfViewer } = await getSettings();
  if (!pdfViewer || !(await chrome.extension.isAllowedFileSchemeAccess())) return;
  if (await bypassed(url)) return;
  await openPdfViewer(url, tabId);
});

// ---------------------------------------------------------------------------
// Checks for people passed here, for GetCases: CommonLII's, Google Scholar's
// ---------------------------------------------------------------------------
//
// GetCases shows the English Reports' scans from CommonLII, which CloudFlare
// lets through only once a person has passed its check in a browser, and
// opinions from Google Scholar, which Google sometimes puts a CAPTCHA in
// front of.  The app can't read Chrome's cookies, so when a page of either
// loads here past the check, its cookies go to GetCases, which fetches with
// them (see the app's eng_rep_pdf.py and scholar_extension.py).  The check
// holds them to the browser that passed: the headers that page went out
// with go too.  And when GetCases opened the page here itself, to have a
// CAPTCHA answered, it is waiting for the page: that goes to it as well,
// and the tab closes.  Only these sites' cookies, only to GetCases on this
// computer, only while it is running.

/** The sites whose cookies GetCases asks for. */
const CLEARANCE_SITES = ["commonlii.org", "scholar.google.com"];
const CLEARANCE_URLS = CLEARANCE_SITES.map((domain) => `*://*.${domain}/*`);
/** Of Google's cookies, the only ones read: Google's anti-abuse state (the
 *  CAPTCHA exemption among it), a returning browser's standing, Scholar's
 *  preferences and consent.  Never those that sign you in to Google. */
const GOOGLE_COOKIES = ["GOOGLE_ABUSE_EXEMPTION", "AEC", "NID", "__Secure-ENID", "GSP", "SOCS", "CONSENT"];
/** The headers that go with a clearance: who the browser said it was. */
const CLEARANCE_HEADERS = ["user-agent", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform", "accept-language"];

/** The headers each page of those sites went out with, by request, until
 *  its answer is in. */
const sentHeaders = new Map();

chrome.webRequest.onSendHeaders.addListener(
  (details) => {
    const kept = {};
    for (const h of details.requestHeaders || []) {
      const name = h.name.toLowerCase();
      if (CLEARANCE_HEADERS.includes(name) && h.value) kept[name] = h.value;
    }
    sentHeaders.set(details.requestId, kept);
    // A page whose answer never came leaves its entry; keep the last few.
    while (sentHeaders.size > 20) sentHeaders.delete(sentHeaders.keys().next().value);
  },
  { urls: CLEARANCE_URLS, types: ["main_frame"] },
  ["requestHeaders", "extraHeaders"],
);

chrome.webRequest.onCompleted.addListener(
  (details) => {
    const headers = sentHeaders.get(details.requestId);
    sentHeaders.delete(details.requestId);
    // A page let through: not CloudFlare's check ("cf-mitigated:
    // challenge"), nor a refusal.  (Google sends its CAPTCHA from elsewhere,
    // or as a page GetCases knows when it sees one.)
    if (details.statusCode !== 200 || header(details.responseHeaders, "cf-mitigated")) return;
    sendClearance(details, headers).catch(() => {});
  },
  { urls: CLEARANCE_URLS, types: ["main_frame"] },
  ["responseHeaders"],
);

/** Which of CLEARANCE_SITES *url* is on. */
function clearanceSite(url) {
  const host = hostOf(url);
  return CLEARANCE_SITES.find((d) => host === d || host.endsWith("." + d));
}

/** The browser, as the reader knows it: "Chrome", "Edge". */
function browserName() {
  const brands = (navigator.userAgentData && navigator.userAgentData.brands) || [];
  const own = brands.map((b) => b.brand).find((b) => b !== "Chromium" && !/not.?a.?brand/i.test(b));
  if (!own) return /\bEdg\//.test(navigator.userAgent) ? "Edge" : "Chrome";
  return own.replace(/^(Google|Microsoft)\s+/, "");
}

/** The headers the browser sends, as best this worker can say, for a page
 *  whose own were not caught (the worker had been stopped in between). */
function ownHeaders() {
  const out = { "user-agent": navigator.userAgent };
  const data = navigator.userAgentData;
  if (data) {
    out["sec-ch-ua"] = data.brands.map((b) => `"${b.brand}";v="${b.version}"`).join(", ");
    out["sec-ch-ua-mobile"] = data.mobile ? "?1" : "?0";
    out["sec-ch-ua-platform"] = `"${data.platform}"`;
  }
  return out;
}

/** The cookies a page of *url*'s site carries (of Google's, only
 *  GOOGLE_COOKIES).  CloudFlare's may be Partitioned, kept under the site
 *  itself as the top-level site; Chrome sends a top-level page both kinds. */
async function siteCookies(url) {
  const domain = clearanceSite(url);
  const topLevelSite = "https://" + domain.split(".").slice(-2).join(".");
  const jar = new Map();
  for (const cookie of await chrome.cookies.getAll({ url })) jar.set(cookie.name, cookie);
  try {
    const partitioned = await chrome.cookies.getAll({ url, partitionKey: { topLevelSite } });
    for (const cookie of partitioned) jar.set(cookie.name, cookie);
  } catch (e) {
    // A Chrome before 119 has no partitioned cookies to ask for.
  }
  let cookies = [...jar.values()];
  if (domain === "scholar.google.com") cookies = cookies.filter((c) => GOOGLE_COOKIES.includes(c.name));
  return cookies.map((c) => ({ name: c.name, value: c.value, expires: c.expirationDate || 0 }));
}

/** The page in tab *tabId* as it arrived, before anything was linked in it
 *  (content.js keeps it).  Asked a few times: the tab's content script may
 *  not be listening yet as the page finishes loading. */
async function arrivedPage(tabId) {
  for (let i = 0; i < 12; i++) {
    try {
      const reply = await chrome.tabs.sendMessage(tabId, { type: "pageHtml" }, { frameId: 0 });
      if (reply && reply.html) return reply.html;
    } catch (e) {
      // Not listening yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return "";
}

/** Hand GetCases the cookies a page of a site behind a check loaded with —
 *  and the page itself, if GetCases opened it here to have a CAPTCHA
 *  answered, closing its tab once GetCases has it. */
async function sendClearance(details, headers) {
  if (!(await appRunning())) return;
  const { url, tabId } = details;
  const cookies = await siteCookies(url);
  // CommonLII's cookies are no use to GetCases without CloudFlare's.
  if (clearanceSite(url) === "commonlii.org" && !cookies.some((c) => c.name === "cf_clearance")) return;
  const reply = await appFetch("/clearance", {
    body: {
      url,
      browser: browserName(),
      cookies,
      headers: headers && headers["user-agent"] ? headers : ownHeaders(),
    },
    timeout: 5000,
  });
  if (!reply || !reply.page || tabId < 0) return;
  const html = await arrivedPage(tabId);
  if (!html) return;
  const taken = await appFetch("/page", { body: { url, html }, timeout: 15000 });
  if (taken && taken.close) await chrome.tabs.remove(tabId);
}
