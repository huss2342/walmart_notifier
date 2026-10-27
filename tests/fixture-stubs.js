// Local-browser fixtures only. Never connects to Walmart or the notifier.
const fixtureMessages = [];
const fixtureListeners = [];
const fixtureStorageListeners = [];
const fixtureStorage = {
  refreshMinutes: 10, pageDelaySeconds: 15, sweepMode: 'none',
  endpoint: 'http://127.0.0.1:8787/ingest', token: 'fixture-secret-not-for-export'
};
window.chrome = {
  permissions: { async contains() { return true; }, async request() { return true; } },
  storage: { onChanged: {
    addListener(listener) { fixtureStorageListeners.push(listener); },
    removeListener(listener) {
      const index = fixtureStorageListeners.indexOf(listener);
      if (index >= 0) fixtureStorageListeners.splice(index, 1);
    }
  }, local: {
    async get(keys) {
      if (!keys) return { ...fixtureStorage };
      return Object.fromEntries(keys.filter(key => key in fixtureStorage).map(key => [key, fixtureStorage[key]]));
    },
    async set(values) { Object.assign(fixtureStorage, values); },
    async remove(keys) { for (const key of Array.isArray(keys) ? keys : [keys]) delete fixtureStorage[key]; }
  } },
  runtime: {
    id: 'reviewer-fixture-extension',
    getManifest() { return { version: '2.10.0' }; },
    onMessage: { addListener(listener) { fixtureListeners.push(listener); },
      removeListener(listener) {
        const index = fixtureListeners.indexOf(listener);
        if (index >= 0) fixtureListeners.splice(index, 1);
      } },
    async sendMessage(message) {
      fixtureMessages.push(message);
      if (message.type === 'reviewer:focus') return { ok: true, enabled: false, reason: 'disabled' };
      if (message.type === 'reviewer:verification-retry') {
        fixtureStorage.verificationAssistRetry.terminal = false;
        fixtureStorage.verificationAssistStatus = { code: 'retry-pending' };
        return { ok: true };
      }
      if (message.type === 'reviewer:role') return { active: false, tabId: 1, generation: 9 };
      if (message.type === 'reviewer:aux-status') return { ok: true, lanes: [
        { lane: 'departments', status: 'partial', completedTargets: 4, totalTargets: 4,
          pages: 27, failures: 1, coverageLimitedTargets: 1,
          lastError: 'A department changed; other targets continued.' },
        { lane: 'keywords', status: 'complete', completedTargets: 2, totalTargets: 2,
          pages: 4, failures: 0 }
      ] };
      if (message.type === 'reviewer:pagination-fallback') {
        fixtureStorage['sweep:1'] = { generation: message.generation, mode: 'high-value',
          paginationFallback: true, query: 'Accessories', total: null, visited: [], endRetries: {},
          completedPages: 1, completedQueries: 0, completedLetters: 0, startedAt: Date.now() };
      }
      if (message.type === 'reviewer:status') return {
        ok: true, generation: 9, tabCount: 1,
        sweep: { mode: 'none', generation: 9, total: 17, visited: [1], startedAt: Date.now() }
      };
      if (message.type === 'reviewer:diagnostics') return { ok: true, report: {
        schemaVersion: 1, extensionVersion: '2.10.0', exportedAt: Date.now(),
        settings: { refreshMinutes: 10, pageDelaySeconds: 15, sweepMode: 'none' }, events: []
      } };
      return { ok: true, active: true, tabId: 1, primaryTabId: 1, generation: 9 };
    }
  }
};
if (location.pathname === '/options.html') {
  if (new URLSearchParams(location.search).has('verificationInputErrorFixture')) {
    Object.assign(fixtureStorage, { verificationAssistEnabled: true,
      verificationAssistStatus: { code: 'input-error' },
      verificationAssistLastResult: { outcome: 'input-error', attempt: 2, key: '11:1000',
        holdMs: 0, pressSent: false, pressConfirmed: false, errorStage: 'move', errorReason: 'timeout' },
      verificationAssistRetry: { key: '11:1000', attempt: 2, nextAt: 0, terminal: true } });
  }
  if (new URLSearchParams(location.search).has('verificationRetryFixture')) {
    Object.assign(fixtureStorage, { verificationAssistEnabled: true,
      verificationAssistStatus: { code: 'retry-pending' },
      verificationAssistLastResult: { outcome: 'needs-attention', attempt: 2,
        holdMs: 18000, releaseReason: 'stalled' },
      verificationAssistRetry: { nextAt: Date.now() + 15000 } });
  }
  if (new URLSearchParams(location.search).has('verificationAlertFixture')) {
    fixtureStorage.lastVerificationAlertError =
      'Verification was detected, but the notifier alert was not confirmed. Check the local notifier connection.';
  }
  Object.assign(fixtureStorage, {
    lastRelay: Date.now(), lastSeen: 2, lastNew: 2, lastQueued: 2,
    lastNotified: 0, lastValueKnown: 2, lastValueUnknown: 0,
    lastMinValue: 12.99, lastMaxValue: 99.99
  });
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (url, init = {}) => {
    if (String(url).endsWith('/scanner-settings')) {
      const settings = init.method === 'PUT' ? JSON.parse(init.body) : null;
      return { ok: true, async json() { return { settings }; } };
    }
    if (String(url).endsWith('/relay-settings')) {
      const settings = init.method === 'PUT' ? JSON.parse(init.body) :
        { schemaVersion: 1, refreshMinutes: 10, pageDelaySeconds: 15, sweepMode: 'none' };
      return { ok: true, async json() { return { settings }; } };
    }
    const body = {
      notifier_configured: true, notifier: 'DiscordNotifier (offline preview)', seen_items: 123,
      discord: {
        live_sending: true, personal_filters_applied: false,
        jobs: { sent: 6, pending: 3 },
        last_delivery: { sent: 3, retried: 0, failed: 0, skipped: 0, blocked: false }
      }
    };
    return { ok: true, async json() { return body; }, async text() { return JSON.stringify(body); } };
  };
  // Verify the generated JSON without writing an actual user download.
  HTMLAnchorElement.prototype.click = async function () {
    const report = await (await nativeFetch(this.href)).text();
    const marker = document.createElement('p');
    marker.id = 'fixture-export-result';
    marker.textContent = JSON.parse(report).schemaVersion === 1 && !report.includes('fixture-secret')
      ? 'PASS: diagnostic JSON export generated without credentials.' : 'FAIL: diagnostic JSON export';
    document.body.appendChild(marker);
  };
}
