const status = document.getElementById('status');
const enable = document.getElementById('enable');
const disable = document.getElementById('disable');
const labels = {
  starting: 'Attempt starting.', verified: 'Catalogue recovery confirmed.',
  'needs-attention': 'Attempt did not confirm recovery; check the reviewer tab.',
  cancelled: 'Attempt cancelled because the page or enabled state changed.',
  'input-error': 'Input or cleanup could not be confirmed; check the reviewer tab.',
  unsupported: 'This layout is unsupported; no hold was sent.',
  unavailable: 'Chrome control was unavailable; check other debuggers and the reviewer tab.'
};
async function render() {
  try {
    const value = await chrome.storage.local.get(['enabled', 'lastResult']);
    const on = value.enabled === true;
    status.textContent = `${on ? 'Enabled' : 'Disabled'}. Last attempt: ` +
      (labels[value.lastResult?.outcome] || 'No attempts recorded.') +
      (Number.isFinite(value.lastResult?.holdMs) ? ` Last hold: ${(value.lastResult.holdMs / 1000).toFixed(2)} seconds.` : '');
    enable.disabled = on;
    disable.disabled = !on;
  } catch { status.textContent = 'Could not read assistant status.'; }
}
async function setEnabled(value) {
  try { await chrome.storage.local.set({ enabled: value }); await render(); }
  catch { status.textContent = 'Could not save the enabled state.'; }
}
enable.addEventListener('click', () => setEnabled(true));
disable.addEventListener('click', () => setEnabled(false));
chrome.storage.onChanged.addListener(() => render());
render();
