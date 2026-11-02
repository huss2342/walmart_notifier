// Scan configuration is owned by the notifier's editable JSON file. Chrome
// keeps a working cache so a local server outage cannot erase saved settings.
function validatedRelaySettings(value) {
  return ReviewerScannerSettings.relay.validate(value);
}

let relaySettingsTail = Promise.resolve();
function syncRelaySettings() {
  const run = relaySettingsTail.then(async () => {
    try {
      return await ReviewerLocalClient.settingsTransaction(async () => {
        const { endpoint, token } = await config();
        const url = ReviewerLocalClient.route(checkedEndpoint(endpoint), '/relay-settings');
        const response = await ReviewerLocalClient.json(url, {
          headers: token ? { 'X-Ingest-Token': token } : {}, cache: 'no-store'
        }, { timeoutMs: 5_000, maxBytes: 16 * 1024 });
        if (!response.ok) throw new Error('Scan settings unavailable.');
        const body = response.body;
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
      });
    } catch {
      await chrome.storage.local.set({
        scanSettingsSyncError: 'Could not load relay-settings.json; using the last saved scan settings.'
      });
      return false;
    }
  });
  relaySettingsTail = run.catch(() => undefined);
  return run;
}
