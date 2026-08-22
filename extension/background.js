// Forwards collected items to the Azure Function ingest endpoint.

async function config() {
  const { endpoint = '', token = '' } = await chrome.storage.sync.get(['endpoint', 'token']);
  return { endpoint, token };
}

async function post(items) {
  const { endpoint, token } = await config();
  if (!endpoint) {
    console.warn('Reviewer Item Relay: no endpoint configured; open the options page.');
    return;
  }
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['X-Ingest-Token'] = token;

  try {
    const resp = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify({ items })
    });
    if (!resp.ok) {
      console.error('Reviewer Item Relay: ingest returned', resp.status);
      await chrome.storage.local.set({
        lastError: `ingest returned HTTP ${resp.status}`,
        lastErrorAt: Date.now()
      });
      return;
    }
    const summary = await resp.json();
    // Recorded so the options page can answer "is this actually running?"
    // without digging through logs -- silent failure is the main risk with a
    // background relay.
    await chrome.storage.local.set({
      lastRelay: Date.now(),
      lastSeen: summary.seen ?? items.length,
      lastNotified: summary.notified ?? 0,
      lastError: ''
    });
    if (summary.notified) {
      chrome.action.setBadgeText({ text: String(summary.notified) });
      chrome.action.setBadgeBackgroundColor({ color: '#0071dc' });
    }
  } catch (err) {
    console.error('Reviewer Item Relay: ingest failed', err);
    await chrome.storage.local.set({ lastError: String(err), lastErrorAt: Date.now() });
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === 'items' && Array.isArray(msg.items) && msg.items.length) {
    post(msg.items);
  }
});
