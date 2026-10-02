// The toolbar popup: whether GetCases is running, and the extension's settings.

const S = globalThis.GetCasesSettings;
const DEFAULTS = S.DEFAULTS;
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// The sites left alone
// ---------------------------------------------------------------------------

async function getSites() {
  const { disabledSites } = await chrome.storage.sync.get({ disabledSites: DEFAULTS.disabledSites });
  return Array.isArray(disabledSites) ? disabledSites : [];
}

function setSites(sites) {
  return chrome.storage.sync.set({ disabledSites: sites });
}

function siteError(message) {
  $("siteError").textContent = message || "";
  $("siteError").hidden = !message;
}

/** Show the list, and whether *host* (the tab's site, or "") is on it. */
function showSites(sites, host) {
  const list = $("siteList");
  list.replaceChildren();
  for (const site of [...sites].sort()) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = site;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove";
    remove.textContent = "×";
    remove.title = `Link citations on ${site} again`;
    remove.setAttribute("aria-label", `Remove ${site}`);
    remove.addEventListener("click", async () => {
      await setSites((await getSites()).filter((s) => s !== site));
    });
    item.append(name, remove);
    list.append(item);
  }
  if (!sites.length) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "None: citations are linked on every site.";
    list.append(item);
  }
  $("siteCount").textContent = `(${sites.length})`;
  $("restoreSites").hidden = S.RESEARCH_SITES.every((s) => sites.includes(s));
  if (host) {
    const covering = S.sitesCovering(host, sites);
    $("site").checked = !covering.length;
    const others = covering.filter((s) => s !== host);
    $("siteNote").hidden = !others.length;
    $("siteNote").textContent = others.length ? ` (left alone as ${others.join(", ")})` : "";
  }
}

function setUpSites(host) {
  $("addSite").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const site = S.normalizeSite($("newSite").value);
    if (!site) {
      siteError("That isn't a site's address: try a domain, like example.com.");
      return;
    }
    const sites = await getSites();
    const covering = S.sitesCovering(site, sites);
    if (covering.length) {
      siteError(covering.includes(site)
        ? `${site} is in the list already.`
        : `${site} is left alone already, as part of ${covering.join(", ")}.`);
      return;
    }
    await setSites([...sites, site]);
    $("newSite").value = "";
    siteError("");
  });
  $("newSite").addEventListener("input", () => siteError(""));
  $("restoreSites").addEventListener("click", async () => {
    const sites = await getSites();
    await setSites([...sites, ...S.RESEARCH_SITES.filter((s) => !sites.includes(s))]);
  });
  if (host) {
    $("siteRow").hidden = false;
    $("host").textContent = host;
    $("site").addEventListener("change", async () => {
      const sites = await getSites();
      // Back on: off the list, whichever of its entries covered this site.
      const covering = S.sitesCovering(host, sites);
      await setSites($("site").checked ? sites.filter((s) => !covering.includes(s)) : [...sites, host]);
    });
  }
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "sync" && changes.disabledSites) {
      showSites(changes.disabledSites.newValue || [], host);
    }
  });
}

function isPdfUrl(url) {
  try {
    return /\.pdf$/i.test(new URL(url).pathname);
  } catch (e) {
    return false;
  }
}

async function main() {
  const settings = await chrome.storage.sync.get(DEFAULTS);
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = (tab && tab.url) || "";
  const viewer = chrome.runtime.getURL("pdf/viewer.html");
  let host = "";
  try {
    const u = new URL(url);
    if (/^https?:$/.test(u.protocol)) host = u.hostname.replace(/^www\./, "");
  } catch (e) { /* not a web page */ }

  // Settings.
  for (const key of ["enabled", "interceptLinks", "pdfViewer"]) {
    $(key).checked = !!settings[key];
    $(key).addEventListener("change", () => chrome.storage.sync.set({ [key]: $(key).checked }));
  }
  $("port").value = settings.port;
  $("port").addEventListener("change", () => {
    const port = parseInt($("port").value, 10);
    if (port >= 1024 && port <= 65535) chrome.storage.sync.set({ port }).then(showStatus);
  });
  setUpSites(host);
  showSites(Array.isArray(settings.disabledSites) ? settings.disabledSites : [], host);

  // This page.
  if (tab && !url.startsWith(viewer) && isPdfUrl(url)) {
    $("pdf").hidden = false;
    $("pdf").addEventListener("click", async () => {
      await chrome.runtime.sendMessage({ type: "openPdf", url });
      window.close();
    });
  }
  const leftAlone = !settings.enabled ||
    S.siteExcluded(host, Array.isArray(settings.disabledSites) ? settings.disabledSites : []);
  if (tab && host && !leftAlone) {
    chrome.tabs.sendMessage(tab.id, { type: "count" }, { frameId: 0 }, (reply) => {
      if (chrome.runtime.lastError || !reply) return;
      $("count").hidden = false;
      $("count").textContent = reply.links === 1
        ? "1 citation linked on this page."
        : `${reply.links} citations linked on this page.`;
      $("rescan").hidden = false;
    });
    $("rescan").addEventListener("click", () => {
      $("rescan").disabled = true;
      chrome.tabs.sendMessage(tab.id, { type: "rescan" }, { frameId: 0 }, (reply) => {
        $("rescan").disabled = false;
        if (!chrome.runtime.lastError && reply) {
          $("count").textContent = `${reply.links} citations linked on this page.`;
        }
      });
    });
  }
  await showStatus();
}

async function showStatus() {
  const reply = await chrome.runtime.sendMessage({ type: "status", force: true });
  const running = !!(reply && reply.running);
  $("status").classList.toggle("running", running);
  $("status").textContent = running
    ? "GetCases is running: citations open in the app."
    : "GetCases isn't running: citations open on the web.";
}

main();
