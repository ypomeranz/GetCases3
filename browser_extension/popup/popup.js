// The toolbar popup: whether GetCases is running, and the extension's settings.

const DEFAULTS = { enabled: true, disabledSites: [], interceptLinks: true, pdfViewer: true, port: 21983 };
const $ = (id) => document.getElementById(id);

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
  if (host) {
    $("siteRow").hidden = false;
    $("host").textContent = host;
    $("site").checked = !settings.disabledSites.includes(host);
    $("site").addEventListener("change", async () => {
      const { disabledSites } = await chrome.storage.sync.get({ disabledSites: [] });
      const next = disabledSites.filter((h) => h !== host);
      if (!$("site").checked) next.push(host);
      await chrome.storage.sync.set({ disabledSites: next });
    });
  }

  // This page.
  if (tab && !url.startsWith(viewer) && isPdfUrl(url)) {
    $("pdf").hidden = false;
    $("pdf").addEventListener("click", async () => {
      await chrome.runtime.sendMessage({ type: "openPdf", url });
      window.close();
    });
  }
  if (tab && host) {
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
