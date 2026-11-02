// Non-secret scanner preferences and bounded local control/status transport.
// The bootstrap endpoint/token stay in Chrome; no browser scripting RPC exists.
const SCANNER_FAILURE_CODES = new Set(['missing-document', 'unsupported-kind', 'ambiguous-filter-section',
  'missing-filter-section', 'ambiguous-control', 'missing-control', 'missing-panel', 'ambiguous-expansion',
  'missing-expansion', 'expansion-stalled', 'expansion-overflow', 'option-overflow', 'missing-selection',
  'ambiguous-selection', 'already-selected', 'cancelled', 'facet-error',
  'document-http-error', 'render-timeout', 'render-opportunity-timeout', 'navigation-timeout',
  'navigation-scope-changed']);

const SCANNER_RUNTIME_PHASES = new Set(['passive', 'starting', 'collecting', 'ingesting', 'ingest-retry',
  'relay-retry', 'complete', 'empty-timeout', 'extension-reloaded', 'manual-browsing', 'navigating',
  'recovering', 'transient-end-retry', 'waiting-content', 'waiting-navigation', 'waiting-selection',
  'waiting-page-change', 'waiting-target-change', 'waiting-user', 'waiting-verification',
  'waiting-focus', 'reading-page', 'waiting-traffic']);
const SCANNER_FOCUS_REASONS = new Set(['none', 'busy', 'waiting-turn', 'manual-tab-active',
  'window-unfocused', 'user-active', 'not-authorized', 'expired', 'paused', 'disabled',
  'unavailable', 'short-lease', 'hidden', 'cancelled', 'revoked', 'selection-changed', 'not-scanner',
  'activation-unavailable']);

function scannerRuntimeProbe(probe) {
  const number = value => Number.isSafeInteger(value) && value >= 0 && value <= 1e15 ? value : 0;
  return { phase: SCANNER_RUNTIME_PHASES.has(probe?.phase) ? probe.phase : 'unknown',
    reachable: probe?.ready === true, active: probe?.active === true, busy: probe?.busy === true,
    visibility: ['visible', 'hidden'].includes(probe?.visibility) ? probe.visibility : 'unknown',
    focusReason: SCANNER_FOCUS_REASONS.has(probe?.focusReason) ? probe.focusReason : 'unknown',
    lastActivityAt: number(probe?.lastActivityAt), nextActionAt: number(probe?.nextActionAt) };
}

function scannerLaneCoverage(lane) {
  // Older coordinator snapshots have cursor counts, not verified completions.
  // Preserve absence rather than fabricating measured coverage or timestamps.
  return Object.fromEntries(['attemptedTargets', 'unresolvedTargets', 'emptyTargets', 'startedAt', 'completedAt']
    .filter(key => lane && Object.hasOwn(lane, key) && Number.isSafeInteger(lane[key]) &&
      lane[key] >= 0 && lane[key] <= 1e15)
    .map(key => [key, lane[key]]));
}

function scannerFailureCode(message) {
  if (!message) return 'none';
  if (typeof message !== 'string') return 'unexpected-error';
  const match = message.match(/(?:^|Reviewer filter: |Filter selection failed: )([a-z-]+)$/);
  if (SCANNER_FAILURE_CODES.has(match?.[1])) return match[1];
  if (/Filter controls are unavailable|ReviewerFacetControls is not defined/.test(message)) return 'controls-unavailable';
  if (/Could not save discovered filters/.test(message)) return 'discovery-save-failed';
  if (/did not respond|stopped responding|not reachable/i.test(message)) return 'content-unresponsive';
  if (/stopped progressing/i.test(message)) return 'progress-timeout';
  if (/Selected filter did not produce a fresh results page/.test(message)) return 'selection-render-timeout';
  if (/selected filter document did not render/.test(message)) return 'selection-document-timeout';
  if (/Could not authorize selected filter|selected filter could not be authorized/.test(message)) return 'selection-bind-failed';
  if (/filter changed without a confirmed fresh results page/.test(message)) return 'selection-unconfirmed';
  return 'unexpected-error';
}

function validatedScannerSettings(value) {
  return ReviewerScannerSettings.validate(value);
}

function validatedScannerCommands(value) {
  const keys = ['schemaVersion', 'restartHomeId', 'restartAdditionalId'];
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.schemaVersion !== 1 ||
      Object.keys(value).length !== keys.length || !keys.every(key => Object.hasOwn(value, key)) ||
      keys.slice(1).some(key => value[key] !== null &&
        (typeof value[key] !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(value[key])))) {
    throw new Error('Invalid scanner commands.');
  }
  return Object.fromEntries(keys.map(key => [key, value[key]]));
}

var ReviewerScannerControl = (() => {
  function create(chrome, { config, checkedEndpoint, snapshot, onCommand, paused }) {
    const local = chrome.storage.local;
    const queues = new Map();
    const serialize = (key, task) => {
      const result = (queues.get(key) || Promise.resolve()).then(task, task);
      queues.set(key, result.catch(() => undefined));
      return result;
    };

    async function request(path, method = 'GET', payload = undefined) {
      const { endpoint, token } = await config();
      const url = ReviewerLocalClient.route(checkedEndpoint(endpoint), path);
      const headers = token ? { 'X-Ingest-Token': token } : {};
      if (payload !== undefined) headers['Content-Type'] = 'application/json';
      const response = await ReviewerLocalClient.json(url, { method, headers, cache: 'no-store',
        ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) }, { timeoutMs: 5_000 });
      if (!response.ok) throw new Error('Scanner backend unavailable.');
      return response.body;
    }

    function syncSettings() {
      return serialize('settings', async () => {
        try {
          return await ReviewerLocalClient.settingsTransaction(async () => {
            const body = await request('/scanner-settings');
            if (!body || !Object.hasOwn(body, 'settings')) throw new Error('Missing scanner settings.');
            if (body.settings !== null) {
              const { schemaVersion, ...settings } = ReviewerScannerSettings.project(
                validatedScannerSettings(body.settings));
              const current = await config();
              const changed = Object.fromEntries(Object.entries(settings).filter(([key, value]) =>
                JSON.stringify(value) !== JSON.stringify(current[key])));
              if (Object.keys(changed).length) await local.set(changed);
            }
            await local.set({ scannerSettingsSyncError: '', scannerSettingsSyncedAt: Date.now() });
            return true;
          });
        } catch {
          await local.set({ scannerSettingsSyncError:
            'Could not load scanner-settings.json; keeping the last usable scanner settings.' }).catch(() => undefined);
          return false;
        }
      });
    }

    function syncCommands() {
      return serialize('commands', async () => {
        try {
          const body = await request('/scanner-commands');
          if (!body || !Object.hasOwn(body, 'commands')) throw new Error('Missing scanner commands.');
          if (body.commands !== null) {
            const commands = validatedScannerCommands(body.commands);
            for (const [field, name] of [['restartHomeId', 'restartHome'], ['restartAdditionalId', 'restartAdditional']]) {
              const id = commands[field];
              if (!id || await paused()) continue; // Never clear verification to run a restart.
              const stored = await local.get(['scannerCommandReceipts', 'scannerCommandIds']);
              const receipts = { ...(stored.scannerCommandReceipts || {}) };
              const previous = receipts[name];
              const seen = Array.isArray(stored.scannerCommandIds) ? stored.scannerCommandIds.slice(-128) : [];
              const actionId = `${name}:${id}`;
              // Older receipts used bare IDs. Retain their conservative replay
              // guard while allowing new requests to address both named scans.
              if ((previous?.id === id && previous.outcome !== 'paused') || seen.includes(actionId) || seen.includes(id)) {
                if (previous?.id === id && previous.outcome === 'started') {
                  receipts[name] = { id, outcome: 'interrupted', at: Date.now() };
                  await local.set({ scannerCommandReceipts: receipts });
                }
                continue;
              }
              receipts[name] = { id, outcome: 'started', at: Date.now() };
              // Reserve before any restart. Ambiguous interruptions never replay it.
              await local.set({ scannerCommandReceipts: receipts,
                scannerCommandIds: [...seen, actionId].slice(-128) });
              let result;
              try { result = await onCommand(name); } catch { result = { ok: false }; }
              const latest = (await local.get(['scannerCommandReceipts'])).scannerCommandReceipts || {};
              const outcome = result?.paused ? 'paused' : result?.ok === false ? 'failed' : 'complete';
              await local.set({ scannerCommandReceipts: { ...latest,
                [name]: { id, outcome, at: Date.now() } } });
              if (outcome === 'paused') {
                await local.set({ scannerCommandIds: seen });
              }
            }
          }
          await local.set({ scannerCommandsSyncError: '' });
          return true;
        } catch {
          await local.set({ scannerCommandsSyncError: 'Could not synchronize scanner commands.' }).catch(() => undefined);
          return false;
        }
      });
    }

    function syncStatus() {
      return serialize('status', async () => {
        try {
          await request('/scanner-status', 'POST', await snapshot());
          await local.set({ scannerStatusSyncError: '', scannerStatusSyncedAt: Date.now() });
          return true;
        } catch {
          await local.set({ scannerStatusSyncError: 'Could not mirror scanner status.' }).catch(() => undefined);
          return false;
        }
      });
    }
    return { syncSettings, syncCommands, syncStatus };
  }
  return { create };
})();
