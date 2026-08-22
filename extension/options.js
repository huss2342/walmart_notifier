const endpointEl = document.getElementById('endpoint');
const tokenEl = document.getElementById('token');
const statusEl = document.getElementById('status');

chrome.storage.sync.get(['endpoint', 'token']).then(({ endpoint = '', token = '' }) => {
  endpointEl.value = endpoint;
  tokenEl.value = token;
});

document.getElementById('save').addEventListener('click', async () => {
  await chrome.storage.sync.set({
    endpoint: endpointEl.value.trim(),
    token: tokenEl.value.trim()
  });
  statusEl.textContent = 'Saved';
  setTimeout(() => { statusEl.textContent = ''; }, 2000);
});
