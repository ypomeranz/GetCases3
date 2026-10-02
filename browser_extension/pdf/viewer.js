// The extension's PDF viewer: pdf.js shows the pages, and each citation in
// them — read by GetCases when it is running, by citations.js when it is
// not — gets a link laid over its text, which opens the way a citation on a
// web page does (see src/content.js).
//
// Opened as pdf/viewer.html?file=<the PDF's address>, by the service worker
// when Chrome opens a PDF, or from the toolbar popup or the right-click menu.

import "../vendor/pdfjs/pdf.min.mjs";

const pdfjsLib = globalThis.pdfjsLib;
const { EventBus, LinkTarget, PDFFindController, PDFLinkService, PDFViewer } =
  await import("../vendor/pdfjs/pdf_viewer.mjs");

const VENDOR = new URL("../vendor/pdfjs/", import.meta.url).href;
pdfjsLib.GlobalWorkerOptions.workerSrc = VENDOR + "pdf.worker.min.mjs";

const $ = (id) => document.getElementById(id);
const fileUrl = new URLSearchParams(location.search).get("file") || "";
const C = globalThis.GetCasesCitations;

const container = $("viewerContainer");
const eventBus = new EventBus();
const linkService = new PDFLinkService({ eventBus, externalLinkTarget: LinkTarget.BLANK });
const findController = new PDFFindController({ eventBus, linkService });
const viewer = new PDFViewer({
  container,
  viewer: $("viewer"),
  eventBus,
  linkService,
  findController,
});
linkService.setViewer(viewer);

let pdf = null;
let fileName = "document.pdf";
let linksShown = true;
// Page index → the citation links on it: [{start, end, link}] over the
// page's text as its text layer lays it out.
const pageLinks = new Map();
let pageTexts = [];

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

function showMessage(html) {
  $("message").innerHTML = html;
  $("message").hidden = false;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.classList.add("on");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => box.classList.remove("on"), 2600);
}

function send(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (reply) => resolve(chrome.runtime.lastError ? null : reply));
  });
}

// ---------------------------------------------------------------------------
// The toolbar
// ---------------------------------------------------------------------------

function setUpToolbar() {
  $("prev").addEventListener("click", () => viewer.previousPage());
  $("next").addEventListener("click", () => viewer.nextPage());
  $("pageNumber").addEventListener("change", () => {
    const n = parseInt($("pageNumber").value, 10);
    if (n >= 1 && pdf && n <= pdf.numPages) viewer.currentPageNumber = n;
    else $("pageNumber").value = viewer.currentPageNumber;
  });
  eventBus.on("pagechanging", (e) => { $("pageNumber").value = e.pageNumber; });

  $("zoomIn").addEventListener("click", () => viewer.increaseScale());
  $("zoomOut").addEventListener("click", () => viewer.decreaseScale());
  $("scale").addEventListener("change", () => {
    if ($("scale").value !== "custom") viewer.currentScaleValue = $("scale").value;
  });
  eventBus.on("scalechanging", (e) => {
    const select = $("scale");
    const preset = e.presetValue || String(e.scale);
    if ([...select.options].some((o) => o.value === preset && o.id !== "customScale")) {
      select.value = preset;
    } else {
      $("customScale").hidden = false;
      $("customScale").textContent = `${Math.round(e.scale * 100)}%`;
      select.value = "custom";
    }
  });

  let lastQuery = "";
  const find = (again, previous) => {
    const query = $("find").value;
    if (!query) { $("findResult").textContent = ""; return; }
    eventBus.dispatch("find", {
      source: window, type: again && query === lastQuery ? "again" : "", query,
      caseSensitive: false, entireWord: false, highlightAll: true,
      findPrevious: previous, matchDiacritics: false,
    });
    lastQuery = query;
  };
  $("find").addEventListener("input", () => find(false, false));
  $("find").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); find(true, e.shiftKey); }
    if (e.key === "Escape") { $("find").value = ""; find(false, false); container.focus(); }
  });
  eventBus.on("updatefindmatchescount", ({ matchesCount }) => {
    $("findResult").textContent = matchesCount.total ? `${matchesCount.current} of ${matchesCount.total}` : "";
  });
  eventBus.on("updatefindcontrolstate", ({ state, matchesCount }) => {
    if (state === 1) $("findResult").textContent = "Not found";
    else if (matchesCount && matchesCount.total) {
      $("findResult").textContent = `${matchesCount.current} of ${matchesCount.total}`;
    }
  });
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
      e.preventDefault();
      $("find").focus();
      $("find").select();
    }
  });

  $("toggleLinks").addEventListener("click", () => {
    linksShown = !linksShown;
    $("toggleLinks").setAttribute("aria-pressed", String(linksShown));
    document.body.classList.toggle("links-hidden", !linksShown);
  });
  $("download").addEventListener("click", async () => {
    if (!pdf) return;
    const data = await pdf.getData();
    const url = URL.createObjectURL(new Blob([data], { type: "application/pdf" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  });
  $("native").addEventListener("click", openInChrome);
}

async function openInChrome() {
  if (!fileUrl) return;
  await send({ type: "pdfBypass", url: fileUrl });
  location.href = fileUrl;
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

async function load() {
  if (!fileUrl) {
    $("title").textContent = "No PDF";
    showMessage("Open a PDF in Chrome, or right-click a link to one and choose " +
      "<b>Open PDF with citation links</b>.");
    return;
  }
  fileName = pdfjsLib.getPdfFilenameFromUrl(fileUrl) || fileName;
  $("title").textContent = fileName;
  document.title = `${fileName} · GetCases`;
  try {
    pdf = await pdfjsLib.getDocument({
      url: fileUrl,
      withCredentials: true,
      cMapUrl: VENDOR + "cmaps/",
      cMapPacked: true,
      standardFontDataUrl: VENDOR + "standard_fonts/",
      wasmUrl: VENDOR + "wasm/",
      iccUrl: VENDOR + "iccs/",
      isEvalSupported: false,
      enableXfa: false,
    }).promise;
  } catch (e) {
    $("title").textContent = fileName;
    const local = fileUrl.startsWith("file:");
    showMessage(
      `<p><b>This PDF couldn't be opened here.</b> ${escapeHtml(e && e.message || e)}</p>` +
      (local
        ? "<p>To read PDFs on this computer, turn on <b>Allow access to file URLs</b> for " +
          "GetCases Citation Links on the chrome://extensions page.</p>"
        : "") +
      `<p><a href="#" id="fallback">Open it in Chrome's viewer instead</a></p>`);
    $("fallback").addEventListener("click", (ev) => { ev.preventDefault(); openInChrome(); });
    return;
  }
  eventBus.on("pagesinit", () => {
    viewer.currentScaleValue = "auto";
    container.focus();
  });
  viewer.setDocument(pdf);
  linkService.setDocument(pdf, null);
  $("numPages").textContent = `of ${pdf.numPages}`;
  $("pageNumber").max = pdf.numPages;
  try {
    const { info } = await pdf.getMetadata();
    const title = info && typeof info.Title === "string" ? info.Title.trim() : "";
    if (title) {
      $("title").textContent = title;
      $("title").title = fileName;
      document.title = `${title} · GetCases`;
    }
  } catch (e) { /* no metadata */ }
  readCitations();
}

// ---------------------------------------------------------------------------
// Citations
// ---------------------------------------------------------------------------

/** A page's text as its text layer lays it out: each item's string, and a
 *  line break after the items that end a line. */
function pageText(items) {
  let s = "";
  for (const item of items) {
    if (item.str === undefined) continue;
    s += item.str;
    if (item.hasEOL) s += "\n";
  }
  return s;
}

async function readCitations() {
  const status = $("citeStatus");
  status.textContent = "Reading citations…";
  const starts = [];
  let text = "";
  pageTexts = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    if (pdf.numPages > 40 && i % 20 === 0) status.textContent = `Reading page ${i} of ${pdf.numPages}…`;
    let s = "";
    try {
      const page = await pdf.getPage(i);
      const content = await page.getTextContent({ includeMarkedContent: false, disableNormalization: true });
      s = pageText(content.items);
    } catch (e) { /* an unreadable page: no citations on it */ }
    starts.push(text.length);
    pageTexts.push(s);
    text += s + "\n\n";
  }
  if (!text.trim()) {
    status.textContent = "No text to read";
    $("toggleLinks").title = "This PDF has no text layer (a scan without OCR), so its citations can't be read.";
    return;
  }
  let reply = await send({ type: "detect", text });
  let found = reply && Array.isArray(reply.links) ? reply.links : null;
  const byApp = !!found;
  if (!found) found = C.detect(text);

  pageLinks.clear();
  let p = 0;
  for (const link of found) {
    while (p + 1 < starts.length && starts[p + 1] <= link.start) p++;
    for (let q = p; q < starts.length && link.end > starts[q]; q++) {
      const s = Math.max(link.start, starts[q]) - starts[q];
      const e = Math.min(link.end, starts[q] + pageTexts[q].length) - starts[q];
      if (e <= s) continue;
      if (!pageLinks.has(q)) pageLinks.set(q, []);
      pageLinks.get(q).push({ start: s, end: e, link, text: text.slice(link.start, link.end) });
    }
  }
  const n = found.length;
  status.textContent = n === 1 ? "1 citation" : `${n} citations`;
  $("toggleLinks").title = (byApp ? "Read by GetCases. " : "Read without GetCases (it isn't running). ") +
    "Click to show or hide the links.";
  for (let i = 0; i < pdf.numPages; i++) {
    const view = viewer.getPageView(i);
    if (view && view.textLayer && view.textLayer.div && view.textLayer.div.childElementCount) drawLinks(view);
  }
}

eventBus.on("textlayerrendered", (e) => {
  const view = viewer.getPageView(e.pageNumber - 1);
  if (view) drawLinks(view);
});

/** Lay the page's citation links over its text layer. */
function drawLinks(view) {
  const index = view.id - 1;
  const page = view.div;
  const layer = view.textLayer && view.textLayer.div;
  if (!page || !layer) return;
  const old = page.querySelector(":scope > .getcases-overlay");
  if (old) old.remove();
  const items = pageLinks.get(index);
  if (!items || !items.length) return;

  // Where each text node of the layer sits in the page's text.
  const nodes = [];
  let text = "";
  const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.nodeType === Node.TEXT_NODE) {
      nodes.push({ node: n, start: text.length });
      text += n.data;
    } else if (n.localName === "br") {
      text += "\n";
    }
  }
  const same = text === pageTexts[index];
  const locate = (offset) => {
    let lo = 0, hi = nodes.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (nodes[mid].start <= offset) lo = mid; else hi = mid - 1;
    }
    const hit = nodes[lo];
    return hit ? [hit.node, Math.min(offset - hit.start, hit.node.data.length)] : null;
  };

  const base = layer.getBoundingClientRect();
  if (!base.width || !base.height) return;
  const overlay = document.createElement("div");
  overlay.className = "getcases-overlay";
  for (const item of items) {
    let start = item.start;
    let end = item.end;
    if (!same) {
      // The layer's text differs from what was read (a font's text not yet
      // decoded, say): find the citation's words near where they should be.
      const want = pageTexts[index].slice(start, end);
      const at = text.indexOf(want, Math.max(0, start - 400));
      if (at < 0) continue;
      start = at;
      end = at + want.length;
    }
    const from = locate(start);
    const to = locate(end);
    if (!from || !to) continue;
    const range = document.createRange();
    try {
      range.setStart(from[0], from[1]);
      range.setEnd(to[0], to[1]);
    } catch (e) {
      continue;
    }
    const action = item.link;
    for (const r of range.getClientRects()) {
      if (r.width < 1 || r.height < 1) continue;
      const a = document.createElement("a");
      a.className = `getcases-pdf-link getcases-${action.category || "case"}`;
      if (action.url) a.href = action.url;
      a.title = `GetCases: ${action.label || item.text}`;
      a.style.left = `${((r.left - base.left) / base.width) * 100}%`;
      a.style.top = `${((r.top - base.top) / base.height) * 100}%`;
      a.style.width = `${(r.width / base.width) * 100}%`;
      a.style.height = `${(r.height / base.height) * 100}%`;
      a.addEventListener("click", (ev) => {
        if (behindClick(ev)) { followOnWeb(ev, action, item.text); return; }
        if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
        ev.preventDefault();
        follow(action, item.text);
      });
      a.addEventListener("auxclick", (ev) => followOnWeb(ev, action, item.text));
      overlay.appendChild(a);
    }
  }
  page.appendChild(overlay);
}

async function follow(action, text) {
  const reply = await send({
    type: "open", from: "ours",
    link: { kind: action.kind, value: action.value, url: action.url, text: text.replace(/\s+/g, " ").trim() },
  });
  if (reply && reply.opened === "app") toast(`Opening ${action.label || text} in GetCases…`);
}

/** A click asking for the link in a tab behind this one: Ctrl/Cmd-click,
 *  or the middle button. */
function behindClick(ev) {
  if (ev.shiftKey || ev.altKey) return false;
  return ev.button === 1 || (ev.button === 0 && (ev.ctrlKey || ev.metaKey));
}

/** Such a click on a case: its scan, found first, rather than the address
 *  the link carries (see src/content.js onWebClick). */
function followOnWeb(ev, action, text) {
  if (!behindClick(ev) || action.kind !== "cite") return;
  ev.preventDefault();
  send({
    type: "open", from: "ours", web: true, behind: true,
    link: { kind: action.kind, value: action.value, url: action.url, text: text.replace(/\s+/g, " ").trim() },
  });
}

setUpToolbar();
load();
