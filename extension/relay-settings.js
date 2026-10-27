// Scan configuration is owned by the notifier's editable JSON file. Chrome
// keeps a working cache so a local server outage cannot erase saved settings.
function validatedRelaySettings(value) {
  const keys = ['schemaVersion', 'refreshMinutes', 'pageDelaySeconds', 'sweepMode'];
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).length !== keys.length || !keys.every(key => Object.hasOwn(value, key)) ||
      value.schemaVersion !== 1 || !Number.isInteger(value.refreshMinutes) ||
      value.refreshMinutes < 0 || value.refreshMinutes > 120 ||
      !Number.isInteger(value.pageDelaySeconds) || value.pageDelaySeconds < 5 ||
      value.pageDelaySeconds > 60 || !['none', 'sweep', 'high-value'].includes(value.sweepMode)) {
    throw new Error('Invalid scan settings.');
  }
  return Object.fromEntries(keys.map(key => [key, value[key]]));
}

let relaySettingsTail = Promise.resolve();
function syncRelaySettings() {
  const run = relaySettingsTail.then(async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5_000);
    try {
      const { endpoint, token } = await config();
      const url = new URL('/relay-settings', checkedEndpoint(endpoint));
      const response = await fetch(url.toString(), {
        headers: token ? { 'X-Ingest-Token': token } : {}, signal: controller.signal, cache: 'no-store'
      });
      if (!response.ok) throw new Error('Scan settings unavailable.');
      const text = await response.text();
      if (text.length > 16 * 1024) throw new Error('Scan settings too large.');
      const body = JSON.parse(text);
      if (!body || !Object.hasOwn(body, 'settings')) throw new Error('Scan settings unavailable.');
      const { settings } = body;
      // Missing file: retain the existing installation's browser preferences.
      if (settings !== null) {
        const { schemaVersion, ...scan } = validatedRelaySettings(settings);
        const cached = await config();
        if (Object.keys(scan).some(key => cached[key] !== scan[key])) {
          await chrome.storage.local.set(scan);
        }
      }
      await chrome.storage.local.set({ scanSettingsSyncError: '', scanSettingsSyncedAt: Date.now() });
      return true;
    } catch {
      await chrome.storage.local.set({
        scanSettingsSyncError: 'Could not load relay-settings.json; using the last saved scan settings.'
      });
      return false;
    } finally {
      clearTimeout(timer);
    }
  });
  relaySettingsTail = run.catch(() => undefined);
  return run;
}
