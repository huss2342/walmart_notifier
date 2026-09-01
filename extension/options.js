// Everything lives in chrome.storage.local. Nothing here needs syncing: the
// endpoint points at this machine, and the token is a local shared secret.

const DEFAULT_PATH_PATTERN = '^/reviews/claim-product';
const DEFAULT_ENDPOINT = 'http://127.0.0.1:8787/ingest';

const endpointEl = document.getElementById('endpoint');
const tokenEl = document.getElementById('token');
const pathEl = document.getElementById('pathPattern');
const pathErrorEl = document.getElementById('pathError');
const refreshEl = document.getElementById('refreshMinutes');
const savedEl = document.getElementById('saved');
const statusEl = document.getElementById('status');

chrome.storage.local
  .get(['endpoint', 'token', 'refreshMinutes', 'pathPattern'])
  .then(({ endpoint = DEFAULT_ENDPOINT, token = '', refreshMinutes = 0,
           pathPattern = DEFAULT_PATH_PATTERN }) => {
    endpointEl.value = endpoint;
    tokenEl.value = token;
    refreshEl.value = refreshMinutes;
    pathEl.value = pathPattern;
  });

document.getElementById('save').addEventListener('click', async () => {
  const pattern = pathEl.value.trim() || DEFAULT_PATH_PATTERN;
  try {
    new RegExp(pattern);
  } catch (err) {
    pathErrorEl.textContent = `Not a valid regular expression: ${err.message}`;
    return;
  }
  pathErrorEl.textContent = '';

  await chrome.storage.local.set({
    endpoint: endpointEl.value.trim() || DEFAULT_ENDPOINT,
    token: tokenEl.value.trim(),
    pathPattern: pattern,
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

function line(cls, strong, rest = '') {
  const span = document.createElement('span');
  span.className = cls;
  span.textContent = strong;
  const div = document.createElement('div');
  div.appendChild(span);
  if (rest) div.appendChild(document.createTextNode(` ${rest}`));
  return div;
}

async function renderStatus() {
  const { endpoint = DEFAULT_ENDPOINT, lastRelay, lastSeen = 0, lastNotified = 0,
          lastError = '', lastErrorAt } = await chrome.storage.local.get(
    ['endpoint', 'lastRelay', 'lastSeen', 'lastNotified', 'lastError', 'lastErrorAt']
  );

  // Built as nodes rather than an HTML string: lastError can contain a server
  // response, and that must never be parsed as markup.
  const frag = document.createDocumentFragment();
  if (!endpoint) {
    frag.appendChild(line('bad', 'No endpoint configured.', 'Set it above.'));
  } else if (!lastRelay) {
    frag.appendChild(line(
      'warn', 'Nothing relayed yet.',
      'Is python src/server.py running? Open your reviewer page in a tab — status updates once it sends.'
    ));
  } else {
    const stale = Date.now() - lastRelay > 60 * 60 * 1000;
    frag.appendChild(line(
      stale ? 'warn' : 'ok',
      `Last relayed ${ago(lastRelay)}`,
      `— ${lastSeen} item${lastSeen === 1 ? '' : 's'} read, ${lastNotified} alerted.`
    ));
    if (stale) {
      frag.appendChild(line('hint', 'Over an hour ago.', 'Is the reviewer tab still open?'));
    }
  }
  if (lastError) {
    frag.appendChild(line(
      'bad', `Last error${lastErrorAt ? ` (${ago(lastErrorAt)})` : ''}:`, lastError
    ));
  }
  statusEl.replaceChildren(frag);
}

renderStatus();
setInterval(renderStatus, 30000);
