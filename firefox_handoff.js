// Runs only in GetCases' dedicated ordinary Firefox profile. It does not
// solve checks: it shows the tab when the app says the reader is needed.
(async () => {
  const win = await browser.windows.create({
    url: "about:blank", type: "popup", state: "minimized", focused: false,
  });
  const tab = win.tabs && win.tabs[0];
  if (!tab) throw new Error("Firefox did not create the handoff tab");
  const started = await fetch(HANDOFF.endpoint, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event: "started", state: win.state }),
  });
  if (!started.ok) throw new Error("Firefox handoff could not connect to GetCases");
  await browser.tabs.update(tab.id, { url: HANDOFF.url });
  let shown = false;
  const deadline = Date.now() + 300000;
  while (Date.now() < deadline) {
    try {
      const current = await browser.tabs.get(tab.id);
      if (current.status === "complete") {
        const [page] = await browser.tabs.executeScript(tab.id, {
          code: "({url: location.href, html: document.documentElement.outerHTML})",
        });
        const cookies = (await browser.cookies.getAll({ domain: "google.com" })).map(c => ({
          name: c.name, value: c.value, domain: c.domain, path: c.path,
          secure: c.secure, httpOnly: c.httpOnly,
          ...(c.session ? {} : { expiry: Math.floor(c.expirationDate) }),
        }));
        const response = await fetch(HANDOFF.endpoint, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...page, cookies }),
        });
        if (!response.ok) throw new Error("Page handoff unavailable");
        const reply = await response.json();
        if (reply.close) {
          // This is GetCases' dedicated window, not the reader's browser.
          await browser.windows.remove(tab.windowId);
          return;
        }
        if (reply.show && !shown) {
          shown = true;
          await browser.windows.update(tab.windowId, { state: "normal", focused: true });
          await browser.tabs.update(tab.id, { active: true });
        }
      }
    } catch (error) {
      // A closed tab ends the handoff. A transient navigation is tried again.
      try { await browser.tabs.get(tab.id); } catch (_) { return; }
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  // A bridge failure must not leave the reader's only recovery window hidden.
  if (!shown) await browser.windows.update(tab.windowId, { state: "normal", focused: true });
})().catch(() => {});
