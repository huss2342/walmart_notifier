importScripts('engine.js', 'chrome-driver.js');

// This companion is disabled until the owner enables it in its own options.
let activeAttempt = null;
const local = chrome.storage.local;
async function enabled() {
  return (await local.get(['enabled'])).enabled === true;
}
async function attempt(tabId, key) {
  if (activeAttempt || !await enabled()) return;
  // Reserve the global lane synchronously before the next asynchronous read.
  if (activeAttempt) return;
  activeAttempt = { tabId, key };
  let driver;
  try {
    const before = await chrome.tabs.get(tabId);
    if (!VerificationChrome.allowedUrl(before?.url) || before.status !== 'complete') return;
    const { budget } = await local.get(['budget']);
    const reserved = VerificationEngine.reserve(budget, key, Date.now());
    if (!reserved.allowed) return;
    // A failed write prevents all input. Never replay an ambiguous attempt on restart.
    await local.set({ budget: reserved.state, lastResult: { at: Date.now(), outcome: 'starting' } });
    driver = await VerificationChrome.open(chrome, tabId, enabled);
    const result = await VerificationEngine.run(driver);
    driver = null; // The engine owns release/detach on every path.
    await local.set({ lastResult: { ...result, at: Date.now() } });
  } catch {
    if (driver) { try { await driver.detach(); } catch { /* Best-effort cleanup. */ } }
    await local.set({ lastResult: { at: Date.now(), outcome: 'unavailable' } }).catch(() => undefined);
  } finally {
    activeAttempt = null;
  }
}
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message?.type !== 'verification-observed') return undefined;
  if (!Number.isInteger(sender?.tab?.id) || sender.frameId !== 0 ||
      !VerificationChrome.allowedUrl(sender.url) || !VerificationChrome.allowedUrl(sender.tab.url) ||
      sender.url !== sender.tab.url || typeof sender.documentId !== 'string' ||
      !/^[a-zA-Z0-9_-]{1,128}$/.test(sender.documentId) ||
      !Number.isSafeInteger(message.episode) || message.episode < 1 || message.episode > 10000) {
    reply({ ok: false }); return undefined;
  }
  attempt(sender.tab.id, `${sender.documentId}:${message.episode}`).then(
    () => reply({ ok: true }), () => reply({ ok: false }));
  return true;
});
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage().catch(() => undefined));
