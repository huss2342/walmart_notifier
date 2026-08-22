const endpointEl = document.getElementById('endpoint');
const tokenEl = document.getElementById('token');
const refreshEl = document.getElementById('refreshMinutes');
const savedEl = document.getElementById('saved');
const statusEl = document.getElementById('status');

chrome.storage.sync.get(['endpoint', 'token', 'refreshMinutes']).then(
  ({ endpoint = '', token = '', refreshMinutes = 0 }) => {
    endpointEl.value = endpoint;
    tokenEl.value = token;
    refreshEl.value = refreshMinutes;
  }
);

document.getElementById('save').addEventListener('click', async () => {
  await chrome.storage.sync.set({
    endpoint: endpointEl.value.trim(),
    token: tokenEl.value.trim(),
    refreshMinutes: Math.max(0, parseInt(refreshEl.value, 10) || 0)
  });
  savedEl.textContent = 'Saved';
  setTimeout(() => { savedEl.textContent = ''; }, 2000);
});

function ago(ts) {
  const mins = Math.floor((Date.now() - ts) / 60000);
  if (mins < 1) return 'just now';
  if (mins === 1) return '1 minute ago';
  if (mins < 60) return `${mins} minutes ago`;
  const hrs = Math.floor(mins / 60);
  return hrs === 1 ? '1 hour ago' : `${hrs} hours ago`;
}

async function renderStatus() {
  const { endpoint = '' } = await chrome.storage.sync.get(['endpoint']);
  const { lastRelay, lastSeen = 0, lastNotified = 0, lastError = '', lastErrorAt } =
    await chrome.storage.local.get(
      ['lastRelay', 'lastSeen', 'lastNotified', 'lastError', 'lastErrorAt']
    );

  const lines = [];
  if (!endpoint) {
    lines.push('<span class="bad">No endpoint configured.</span> Paste your ingest URL above.');
  } else if (!lastRelay) {
    lines.push(
      '<span class="warn">Nothing relayed yet.</span> ' +
      'Open your reviewer page in a tab — status updates once it sends.'
    );
  } else {
    const stale = Date.now() - lastRelay > 60 * 60 * 1000;
    lines.push(
      `<span class="${stale ? 'warn' : 'ok'}">Last relayed ${ago(lastRelay)}</span> — ` +
      `${lastSeen} item${lastSeen === 1 ? '' : 's'} read, ${lastNotified} alerted.`
    );
    if (stale) {
      lines.push('<span class="hint">Over an hour ago. Is the reviewer tab still open?</span>');
    }
  }
  if (lastError) {
    lines.push(`<span class="bad">Last error${lastErrorAt ? ` (${ago(lastErrorAt)})` : ''}:</span> ${lastError}`);
  }
  statusEl.innerHTML = lines.join('<br>');
}

renderStatus();
setInterval(renderStatus, 30000);
