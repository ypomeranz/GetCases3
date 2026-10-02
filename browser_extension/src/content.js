// GetCases in a web page: link the citations on it, and send a click on one
// to GetCases (when it is running) or to the web page the app would open.
//
// While GetCases runs, the page's text goes to the app (through the service
// worker, the only part of the extension allowed to reach it) and the app's
// own detector says what to link.  Without it, citations.js reads the page.
// Links a page already has to legal sources (a Justia case, a section on
// Cornell or uscode.house.gov) open in GetCases too while it is running, and
// go where they always went when it is not.

(() => {
  "use strict";
  if (globalThis.__getcasesContent) return;
  globalThis.__getcasesContent = true;

  const C = globalThis.GetCasesCitations;
  const LINK = "getcases-cite";
  const MAX_TEXT = 2000000;
  // Never read inside these: code, form fields, media, and whatever the
  // reader is typing into.
  const SKIP = new Set(["script", "style", "noscript", "textarea", "input", "select",
    "option", "button", "template", "svg", "math", "canvas", "iframe", "object",
    "embed", "video", "audio", "head", "title", "code", "kbd", "samp"]);
  // Elements that start a line of their own: text on either side of one is
  // never one citation.
  const BLOCK = new Set(["address", "article", "aside", "blockquote", "center", "dd",
    "details", "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
    "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody",
    "td", "tfoot", "th", "thead", "tr", "ul", "caption"]);

  const S = globalThis.GetCasesSettings;
  let settings = {
    enabled: S.DEFAULTS.enabled,
    disabledSites: S.DEFAULTS.disabledSites,
    interceptLinks: S.DEFAULTS.interceptLinks,
  };
  let mode = "web";                 // how the page was read: "app" or "web"
  let appRunning = false;
  let statusAt = 0;
  let nextId = 1;
  const links = new Map();          // our link's id → its action
  const anchorActions = new WeakMap();  // a page's own <a> → the action it cites
  let scanned = new WeakSet();      // text nodes already read
  // Provisions of the Constitution linked on the page: each the first time
  // only — in the page, not in each part of it read as it loads.
  let constLinked = new Set();
  let observer = null;
  let pending = new Set();
  let timer = 0;

  // -------------------------------------------------------------------------
  // Talking to the service worker
  // -------------------------------------------------------------------------

  function send(message) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(message, (reply) => {
          resolve(chrome.runtime.lastError ? null : reply);
        });
      } catch (e) {
        resolve(null);              // the extension was reloaded
      }
    });
  }

  async function refreshStatus(force = false) {
    if (!force && Date.now() - statusAt < 5000) return appRunning;
    statusAt = Date.now();
    const reply = await send({ type: "status", force });
    appRunning = !!(reply && reply.running);
    return appRunning;
  }

  /** Whether this site is one the reader has the extension leave alone
   *  (by default, the research services: Westlaw, Lexis, …). */
  function siteDisabled() {
    return S.siteExcluded(location.hostname, settings.disabledSites);
  }

  // -------------------------------------------------------------------------
  // Reading the page
  // -------------------------------------------------------------------------

  /** The text under *root*, with where each text node sits in it. */
  function collect(root, wantItalic) {
    const segs = [];
    const italic = [];
    const styles = new Map();
    let text = "";

    const isItalic = (el) => {
      if (!el) return false;
      let v = styles.get(el);
      if (v === undefined) {
        const fs = getComputedStyle(el).fontStyle;
        v = fs === "italic" || fs.startsWith("oblique");
        styles.set(el, v);
      }
      return v;
    };
    const newline = () => { if (text && !text.endsWith("\n")) text += "\n"; };

    const walk = (node) => {
      for (let child = node.firstChild; child; child = child.nextSibling) {
        if (text.length > MAX_TEXT) return;
        if (child.nodeType === Node.TEXT_NODE) {
          const data = child.data;
          if (!data) continue;
          if (scanned.has(child)) { newline(); continue; }
          scanned.add(child);
          const start = text.length;
          segs.push({ node: child, start, data });
          text += data;
          if (wantItalic && isItalic(child.parentElement)) {
            const last = italic[italic.length - 1];
            if (last && last[1] === start) last[1] = text.length;
            else italic.push([start, text.length]);
          }
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          const tag = child.localName;
          if (SKIP.has(tag) || child.isContentEditable || child.classList.contains(LINK) ||
              child.hasAttribute("data-getcases-skip")) {
            newline();
            continue;
          }
          if (tag === "br") { text += "\n"; continue; }
          const block = BLOCK.has(tag);
          if (block) newline();
          walk(child);
          if (block) newline();
        }
      }
    };
    walk(root);
    return { text, segs, italic };
  }

  /** The first segment ending after *offset*. */
  function segAt(segs, offset) {
    let lo = 0, hi = segs.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (segs[mid].start + segs[mid].data.length <= offset) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  function tooltip(link) {
    return `GetCases: ${link.label || ""}`.trim();
  }

  /** Put *found* (detect()'s links over *col*'s text) on the page. */
  function apply(col, found) {
    const pieces = [];
    for (const link of found) {
      const id = nextId++;
      const action = {
        kind: link.kind, value: link.value, url: link.url, label: link.label,
        category: link.category, text: col.text.slice(link.start, link.end).replace(/\s+/g, " ").trim(),
      };
      links.set(id, action);
      for (let i = segAt(col.segs, link.start); i < col.segs.length && col.segs[i].start < link.end; i++) {
        const seg = col.segs[i];
        const s = Math.max(link.start, seg.start) - seg.start;
        const e = Math.min(link.end, seg.start + seg.data.length) - seg.start;
        if (e <= s) continue;
        const piece = seg.data.slice(s, e);
        const anchor = seg.node.parentElement && seg.node.parentElement.closest("a[href]");
        if (anchor) {
          // The page links this already.  Its link opens in GetCases while
          // the app is running, if the citation itself is in it — not just
          // the case's name, which may well link to an article about it.
          if (/\d/.test(piece) && !anchorActions.has(anchor)) {
            anchorActions.set(anchor, action);
            anchor.classList.add("getcases-known");
            if (!anchor.title) anchor.title = tooltip(action);
          }
          continue;
        }
        if (!piece.trim()) continue;
        pieces.push({ seg, s, e, id, action });
      }
    }
    // Right to left, so that splitting a text node leaves the offsets of
    // the pieces before it where they were.
    for (let k = pieces.length - 1; k >= 0; k--) wrap(pieces[k]);
    // Our own changes are recorded with any the page made meanwhile: keep
    // the page's (see queue()).
    if (observer) queue(observer.takeRecords());
    return pieces.length;
  }

  function wrap({ seg, s, e, id, action }) {
    const node = seg.node;
    if (!node.isConnected || !node.parentNode || node.data.length < e ||
        node.data.slice(s, e) !== seg.data.slice(s, e)) {
      return;                       // the page changed it meanwhile
    }
    // The text split off has been read with the rest.
    let target = node;
    if (e < target.data.length) scanned.add(target.splitText(e));
    if (s > 0) {
      target = target.splitText(s);
      scanned.add(target);
    }
    const a = document.createElement("a");
    a.className = `${LINK} getcases-${action.category || "case"}`;
    if (action.url) a.href = action.url;
    a.dataset.getcasesId = String(id);
    a.title = tooltip(action);
    target.parentNode.insertBefore(a, target);
    a.appendChild(target);
  }

  // What the app reads that the extension does not: short forms, "Id.",
  // dockets, WL numbers, state codes, legislative history.  A page with none
  // of these, and no citation the extension can read itself, is not worth
  // the app's time.
  const APP_MARKERS = /§|\bv\.\s|\bIn re\b|\bId\.|\bNo\.\s?\d|\bWL\b|\bLEXIS\b|\bCode\b|\bStat\.|\bCong\.|\bRep\.\s?No\./;

  async function scan(root) {
    if (!root || !root.isConnected) return;
    const col = collect(root, mode === "app");
    if (!col.text.trim() || !C.mightHaveCitations(col.text)) return;
    const local = C.detect(col.text);
    let found = null;
    if (mode === "app" && (local.length || APP_MARKERS.test(col.text))) {
      const reply = await send({ type: "detect", text: col.text, italic: col.italic });
      if (reply && Array.isArray(reply.links)) found = reply.links;
    }
    if (!found) found = local;
    found = found.filter((l) => l.kind !== "const" ||
      (!constLinked.has(l.value) && !!constLinked.add(l.value)));
    if (found.length) apply(col, found);
  }

  function unlinkAll() {
    for (const a of document.querySelectorAll("a." + LINK)) a.replaceWith(...a.childNodes);
    for (const a of document.querySelectorAll("a.getcases-known")) a.classList.remove("getcases-known");
    links.clear();
    scanned = new WeakSet();
    constLinked = new Set();
    if (observer) observer.takeRecords();       // our changes, not the page's
  }

  async function scanPage() {
    if (!document.body) return;
    // Watch first: what the page adds while it is being read (the app can
    // take a second or two) is read after it.
    observe();
    mode = (await refreshStatus(true)) ? "app" : "web";
    await scan(document.body);
  }

  async function rescan() {
    unlinkAll();
    await scanPage();
  }

  // -------------------------------------------------------------------------
  // Pages that keep changing
  // -------------------------------------------------------------------------

  function observe() {
    if (observer || !document.body) return;
    observer = new MutationObserver(queue);
    observer.observe(document.body, { childList: true, subtree: true });
  }

  /** Note what the page added, to read shortly: never our own links, nor
   *  text already read. */
  function queue(records) {
    for (const r of records) {
      for (const n of r.addedNodes) {
        if (n.nodeType === Node.ELEMENT_NODE) {
          if (!n.classList.contains(LINK)) pending.add(n);
        } else if (n.nodeType === Node.TEXT_NODE && !scanned.has(n) && n.parentElement &&
                   !n.parentElement.closest("a." + LINK)) {
          pending.add(n.parentElement);
        }
      }
    }
    if (pending.size && !timer) timer = setTimeout(flush, 600);
  }

  async function flush() {
    timer = 0;
    const roots = [...pending].filter((n) => n.isConnected);
    pending = new Set();
    // Only the outermost of what changed.
    const tops = roots.filter((n) => !roots.some((o) => o !== n && o.contains(n)));
    for (const root of tops.slice(0, 200)) await scan(root);
  }

  // -------------------------------------------------------------------------
  // Clicks
  // -------------------------------------------------------------------------

  let toastHost = null;

  function toast(message) {
    if (!toastHost) {
      toastHost = document.createElement("div");
      toastHost.setAttribute("data-getcases-skip", "");
      toastHost.style.cssText = "all:initial;position:fixed;z-index:2147483647;right:16px;bottom:16px;";
      const shadow = toastHost.attachShadow({ mode: "open" });
      shadow.innerHTML = `<style>
        .t{font:13px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif;color:#fff;background:#1f2937;
           border-radius:8px;padding:9px 13px;box-shadow:0 6px 20px rgba(0,0,0,.25);max-width:340px;
           opacity:0;transform:translateY(6px);transition:opacity .15s,transform .15s}
        .t.on{opacity:1;transform:none}</style><div class="t" role="status"></div>`;
      (document.body || document.documentElement).appendChild(toastHost);
    }
    const box = toastHost.shadowRoot.querySelector(".t");
    box.textContent = message;
    box.classList.add("on");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => box.classList.remove("on"), 2600);
  }

  async function follow(action, from, anchor) {
    const reply = await send({
      type: "open", from,
      link: { kind: action.kind, value: action.value, text: action.text, url: action.url },
      href: anchor ? anchor.href : "",
      newTab: anchor ? anchor.target === "_blank" : false,
    });
    if (reply && reply.opened === "app") {
      appRunning = true;
      statusAt = Date.now();
      toast(`Opening ${action.label || action.text} in GetCases…`);
      if (mode === "web") setTimeout(rescan, 1500);   // read the page as the app does
    } else if (reply && reply.opened === "web") {
      appRunning = false;
      statusAt = Date.now();
    }
  }

  function modified(ev) {
    return ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey;
  }

  /** A click asking for the link in a tab of its own, behind this one:
   *  Ctrl/Cmd-click, or the middle button. */
  function behindClick(ev) {
    if (ev.shiftKey || ev.altKey) return false;
    return ev.button === 1 || (ev.button === 0 && (ev.ctrlKey || ev.metaKey));
  }

  /** Our link to a case, for a click asking for the web page: the scan the
   *  service worker finds first (see background.js caseUrl), not merely
   *  the address the link carries — which, short of looking, can only be
   *  Google Scholar for a lower court's case. */
  function onWebClick(ev) {
    if (ev.defaultPrevented || !behindClick(ev) || !settings.enabled) return;
    const el = ev.target instanceof Element ? ev.target : ev.target && ev.target.parentElement;
    const ours = el && el.closest("a." + LINK);
    const action = ours && links.get(+ours.dataset.getcasesId);
    if (!action || action.kind !== "cite") return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    send({
      type: "open", from: "ours", web: true, behind: true,
      link: { kind: action.kind, value: action.value, text: action.text, url: action.url },
    });
  }

  function onClick(ev) {
    if (behindClick(ev)) { onWebClick(ev); return; }
    if (ev.defaultPrevented || modified(ev) || !settings.enabled) return;
    const el = ev.target instanceof Element ? ev.target : ev.target && ev.target.parentElement;
    if (!el) return;
    const ours = el.closest("a." + LINK);
    if (ours) {
      const action = links.get(+ours.dataset.getcasesId);
      if (!action) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      follow(action, "ours", null);
      return;
    }
    if (!settings.interceptLinks || !appRunning) return;
    const anchor = el.closest("a[href]");
    if (!anchor) return;
    const action = anchorAction(anchor);
    if (!action) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    follow(action, "anchor", anchor);
  }

  function anchorAction(anchor) {
    if (anchorActions.has(anchor)) return anchorActions.get(anchor);
    const found = C.actionForUrl(anchor.href);
    if (!found) return null;
    const action = Object.assign({ category: C.category(found.kind), text: found.label }, found);
    anchorActions.set(anchor, action);
    return action;
  }

  // Before a click on a page's own legal link, make sure whether GetCases is
  // running is known: the click itself cannot wait to ask.
  function onPointerOver(ev) {
    if (!settings.interceptLinks) return;
    const el = ev.target instanceof Element ? ev.target : null;
    const anchor = el && el.closest("a[href]");
    if (anchor && !anchor.classList.contains(LINK) && anchorAction(anchor)) refreshStatus();
  }

  // -------------------------------------------------------------------------
  // Start
  // -------------------------------------------------------------------------

  async function start() {
    const stored = await new Promise((resolve) => {
      try {
        chrome.storage.sync.get(settings, (v) => resolve(chrome.runtime.lastError ? settings : v));
      } catch (e) {
        resolve(settings);
      }
    });
    settings = Object.assign({}, settings, stored);
    if (!settings.enabled || siteDisabled()) return;
    if (!listening) {
      listening = true;
      window.addEventListener("click", onClick, true);
      window.addEventListener("auxclick", onWebClick, true);   // the middle button
      document.addEventListener("pointerover", onPointerOver, true);
      document.addEventListener("visibilitychange", async () => {
        if (document.visibilityState !== "visible" || !settings.enabled || siteDisabled()) return;
        // GetCases started since the page was read: read it again, the app's way.
        if ((await refreshStatus(true)) && mode === "web") rescan();
      });
    }
    await scanPage();
  }
  let listening = false;

  chrome.runtime.onMessage.addListener((message, _sender, reply) => {
    if (message && message.type === "rescan") {
      if (!settings.enabled || siteDisabled()) {
        reply({ links: 0 });              // a site left alone stays so
        return false;
      }
      rescan().then(() => reply({ links: links.size }));
      return true;
    }
    if (message && message.type === "count") {
      reply({ links: links.size, mode });
    }
    return false;
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "sync") return;
    for (const [k, v] of Object.entries(changes)) settings[k] = v.newValue;
    if (!settings.enabled || siteDisabled()) {
      unlinkAll();
      if (observer) { observer.disconnect(); observer = null; }
    } else if ("enabled" in changes || "disabledSites" in changes) {
      start();
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
