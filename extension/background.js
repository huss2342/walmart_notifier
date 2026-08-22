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
      return;
    }
    const summary = await resp.json();
    if (summary.notified) {
      chrome.action.setBadgeText({ text: String(summary.notified) });
      chrome.action.setBadgeBackgroundColor({ color: '#0071dc' });
    }
  } catch (err) {
    console.error('Reviewer Item Relay: ingest failed', err);
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === 'items' && Array.isArray(msg.items) && msg.items.length) {
    post(msg.items);
  }
});
