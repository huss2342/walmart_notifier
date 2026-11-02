import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { reviewerLocks } from './fixtures/reviewer-locks.mjs';

const html = readFileSync(new URL('../extension/options.html', import.meta.url), 'utf8');
const source = ['sweep-modes.js', 'runtime-policy.js', 'local-client.js', 'scanner-settings.js',
  'options.js']
  .map(name => readFileSync(new URL(`../extension/${name}`, import.meta.url), 'utf8')).join('\n');

// Match the existing options fixture: real scripts and markup IDs, with only
// Chrome storage/runtime and the local notifier replaced by in-memory fakes.
async function loadOptions({ initial = {}, auxiliaryResponse = { ok: true, lanes: [] },
  auxiliaryError = false, restartResponse = { ok: true }, saveStatus = 200,
  scannerSettingsBody = null, scannerLoadStatus = 200, scannerSaveStatus = 200,
  debuggerPermission = true, nativePermission = true, shared = null, locks = null, fetchImpl = null } = {}) {
  class Node {
    constructor() {
      this.children = []; this._text = ''; this.value = ''; this.checked = false;
      this.disabled = false; this.listeners = new Map();
    }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
    set textContent(value) { this._text = String(value ?? ''); this.children = []; }
    set innerHTML(_value) { throw new Error('Status must not parse HTML.'); }
    appendChild(child) { this.children.push(child); return child; }
    replaceChildren(...children) { this._text = ''; this.children = children; }
    addEventListener(type, listener) { this.listeners.set(type, listener); }
    focus() { this.focused = true; }
  }
  const elements = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)]
    .map(match => [match[1], new Node()]));
  const document = { getElementById: id => elements[id], createElement: () => new Node(),
    createDocumentFragment: () => new Node(), createTextNode(text) {
      const node = new Node(); node.textContent = text; return node;
    } };
  const data = shared?.data || { endpoint: 'http://127.0.0.1:8787/ingest', ...initial };
  const fetches = []; const messages = []; const writes = []; const permissionRequests = [];
  const backend = shared || { scannerSettings: scannerSettingsBody, relaySettings: null };
  const timers = new Map();
  let nextTimer = 0;
  const chrome = { permissions: {
    async contains() { return debuggerPermission; },
    async request(value) { permissionRequests.push(value); return nativePermission; }
  }, storage: { local: {
    async get(keys) { return Object.fromEntries(keys.filter(key => key in data).map(key => [key, data[key]])); },
    async set(value) {
      const copy = JSON.parse(JSON.stringify(value)); writes.push(copy); Object.assign(data, copy);
    }
  } }, runtime: { async sendMessage(message) {
    messages.push(message);
    if (message.type === 'reviewer:aux-status') {
      if (auxiliaryError) throw new Error('Background is unavailable.');
      return auxiliaryResponse;
    }
    if (message.type === 'reviewer:aux-restart') return restartResponse;
    return { ok: true, tabCount: 1 };
  } } };
  const context = vm.createContext({ document, chrome, URL, AbortController, console,
    navigator: { locks: locks || shared?.locks || reviewerLocks() },
    setTimeout(callback, ms) { const id = ++nextTimer; timers.set(id, { callback, ms }); return id; },
    clearTimeout(id) { timers.delete(id); }, setInterval() {},
    async fetch(url, init = {}) {
      const path = new URL(url).pathname;
      fetches.push({ path, ...init });
      const override = fetchImpl?.(url, init);
      if (override !== undefined) return override;
      const put = init.method === 'PUT';
      if (path === '/scanner-settings') {
        const status = put ? scannerSaveStatus : scannerLoadStatus;
        if (put && status === 200) backend.scannerSettings = JSON.parse(init.body);
        const body = JSON.parse(JSON.stringify({ settings: backend.scannerSettings }));
        return { ok: status === 200, status, async json() { return body; } };
      }
      const status = put ? saveStatus : 200;
      if (path === '/relay-settings' && put && status === 200) backend.relaySettings = JSON.parse(init.body);
      const body = path === '/relay-settings'
        ? { settings: backend.relaySettings }
        : { notifier: 'DiscordNotifier', notifier_configured: true };
      return { ok: status === 200, status, async json() { return body; } };
    }
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  return { elements, data, fetches, messages, writes, permissionRequests, backend, timers, async click(id) {
    assert.ok(elements[id], `${id} is present in options markup`);
    const listener = elements[id].listeners.get('click');
    assert.equal(typeof listener, 'function', `${id} has a click handler`);
    await listener();
    await new Promise(resolve => setImmediate(resolve));
  } };
}


function scannerSettings(overrides = {}) {
  return { schemaVersion: 1, auxiliaryDepartments: true, auxiliaryKeywords: true,
    auxiliaryBrands: false, auxiliaryKeywordQueries: null, groupScannerTabs: true,
    rotateScannerTabs: true, verificationAssistEnabled: false, verificationNativeEnabled: false,
    pathPattern: '^/reviews/claim-product', ...overrides };
}

test('priority controls load independent settings and save only deliberate edits', async () => {
  const ui = await loadOptions({ scannerSettingsBody: scannerSettings({
    auxiliaryPriority: true, auxiliaryPriorityQueries: ['Sofa', 'Desk'],
    auxiliaryPriorityRefreshMinutes: 2 }) });
  assert.equal(ui.elements.auxiliaryPriority.checked, true);
  assert.equal(ui.elements.auxiliaryPriorityQueries.value, 'Sofa\nDesk');
  assert.equal(Number(ui.elements.auxiliaryPriorityRefreshMinutes.value), 2);
  ui.elements.auxiliaryPriorityQueries.value = 'Sofa\nMattress\nsofa';
  ui.elements.auxiliaryPriorityRefreshMinutes.value = '1';
  await ui.click('save');
  assert.deepEqual(ui.backend.scannerSettings.auxiliaryPriorityQueries, ['Sofa', 'Mattress']);
  assert.equal(ui.backend.scannerSettings.auxiliaryPriorityRefreshMinutes, 1);
  assert.equal(ui.backend.scannerSettings.auxiliaryDepartments, true);
  assert.equal(ui.backend.scannerSettings.auxiliaryKeywords, true);
});

test('priority form rejects empty enabled queries and invalid refresh without any PUT', async () => {
  const ui = await loadOptions({});
  ui.elements.auxiliaryPriority.checked = true;
  ui.elements.auxiliaryPriorityQueries.value = '';
  await ui.click('save');
  assert.match(ui.elements.auxiliaryPriorityError.textContent, /at least one/i);
  assert.equal(ui.fetches.filter(call => call.method === 'PUT').length, 0);
  ui.elements.auxiliaryPriorityQueries.value = 'Desk';
  ui.elements.auxiliaryPriorityRefreshMinutes.value = '0';
  await ui.click('save');
  assert.match(ui.elements.auxiliaryPriorityError.textContent, /1.*120/);
  assert.equal(ui.fetches.filter(call => call.method === 'PUT').length, 0);
});

test('priority scan has its own visible coverage status', async () => {
  const ui = await loadOptions({ auxiliaryResponse: { ok: true, lanes: [{
    lane: 'priority', status: 'scanning', targetLabel: 'Sofa', completedTargets: 2,
    attemptedTargets: 3, unresolvedTargets: 1, totalTargets: 10, pages: 8, failures: 1
  }] } });
  assert.match(ui.elements.auxiliaryStatus.textContent, /Priority scan/);
  assert.match(ui.elements.auxiliaryStatus.textContent, /2 of 10 targets verified complete/);
  assert.match(ui.elements.auxiliaryStatus.textContent, /1 unresolved/);
});

for (const [code, guidance] of [
  ['waiting-foreground', /Select the verification tab.*Chrome window forward/],
  ['waiting-tab-ready', /finish loading and unfreeze/]
]) {
  test(`verification ${code} explains readiness without claiming an attempt was spent`, async () => {
    const ui = await loadOptions({ initial: { verificationAssistEnabled: true,
      verificationAssistStatus: { code } } });
    assert.match(ui.elements.verificationAssistStatus.textContent, guidance);
    assert.match(ui.elements.verificationAssistStatus.textContent, /No new attempt has been spent/);
  });
}

test('backend scanner settings are authoritative without importing secrets or prompting permissions', async () => {
  const desired = scannerSettings({ auxiliaryDepartments: false, auxiliaryKeywords: false,
    auxiliaryKeywordQueries: [], groupScannerTabs: false, rotateScannerTabs: false,
    verificationAssistEnabled: true, verificationNativeEnabled: true, pathPattern: '^/reviews/custom' });
  const ui = await loadOptions({ initial: { token: 'private-token', auxiliaryDepartments: true },
    scannerSettingsBody: desired });
  assert.equal(ui.elements.auxiliaryDepartments.checked, false);
  assert.equal(ui.elements.auxiliaryKeywords.checked, false);
  assert.equal(ui.elements.auxiliaryKeywordQueries.value, '');
  assert.equal(ui.elements.groupScannerTabs.checked, false);
  assert.equal(ui.elements.pathPattern.value, '^/reviews/custom');
  assert.equal(ui.data.verificationAssistEnabled, true);
  assert.equal(ui.data.verificationNativeEnabled, true);
  assert.equal(ui.data.token, 'private-token');
  assert.equal(ui.permissionRequests.length, 0);
  assert.match(ui.elements.verificationAssistStatus.textContent, /^Enabled/);
});

test('invalid backend scanner settings retain browser preferences and show a load failure', async () => {
  const ui = await loadOptions({ initial: { auxiliaryDepartments: false, token: 'private-token' },
    scannerSettingsBody: { ...scannerSettings(), token: 'untrusted-secret' } });
  assert.equal(ui.elements.auxiliaryDepartments.checked, false);
  assert.equal(ui.data.token, 'private-token');
  assert.match(ui.elements.saved.textContent, /scanner-settings\.json/);
});

test('authoritative legacy settings clear stale priority preferences in both UI and cache', async () => {
  const ui = await loadOptions({ initial: { auxiliaryPriority: true,
    auxiliaryPriorityQueries: ['Desk'], auxiliaryPriorityRefreshMinutes: 2 },
    scannerSettingsBody: scannerSettings() });
  assert.equal(ui.elements.auxiliaryPriority.checked, false);
  assert.equal(ui.data.auxiliaryPriority, false);
  assert.equal(ui.data.auxiliaryPriorityQueries, null);
  assert.equal(ui.data.auxiliaryPriorityRefreshMinutes, 1);
  assert.equal('auxiliaryPriority' in ui.backend.scannerSettings, false);
});

test('full settings save writes the nonsecret scanner contract before caching acknowledged values', async () => {
  const ui = await loadOptions({ initial: { token: 'private-token', verificationAssistEnabled: true,
    verificationNativeEnabled: true } });
  ui.elements.auxiliaryKeywordQueries.value = ' Pet\nChair\nPET ';
  await ui.click('save');
  const request = ui.fetches.find(call => call.path === '/scanner-settings' && call.method === 'PUT');
  assert.ok(request, 'Scanner settings must be persisted in the backend.');
  assert.deepEqual(JSON.parse(request.body), scannerSettings({ auxiliaryKeywordQueries: ['Pet', 'Chair'],
    verificationAssistEnabled: true, verificationNativeEnabled: true,
    auxiliaryPriority: false, auxiliaryPriorityQueries: null, auxiliaryPriorityRefreshMinutes: 1 }));
  assert.equal(request.headers['X-Ingest-Token'], 'private-token');
  assert.doesNotMatch(request.body, /private-token|endpoint/);
  assert.deepEqual(ui.data.auxiliaryKeywordQueries, ['Pet', 'Chair']);
});

test('scanner save failure never falls back to browser-only preferences', async () => {
  const ui = await loadOptions({ initial: { auxiliaryDepartments: false }, scannerSaveStatus: 503 });
  ui.elements.auxiliaryDepartments.checked = true;
  await ui.click('save');
  assert.equal(ui.data.auxiliaryDepartments, false);
  assert.match(ui.elements.saved.textContent, /Not saved|Not fully saved/);
  assert.equal(ui.fetches.some(call => call.path === '/relay-settings' && call.method === 'PUT'), false);
});

test('verification toggles persist through scanner settings and retain state when the backend rejects them', async () => {
  const ui = await loadOptions({ initial: { auxiliaryDepartments: false, token: 'private-token' } });
  ui.elements.auxiliaryDepartments.checked = true; // Unsaved form edits must not leak into this toggle.
  await ui.click('enableVerificationAssist');
  assert.equal(ui.data.verificationAssistEnabled, true);
  const request = ui.fetches.find(call => call.path === '/scanner-settings' && call.method === 'PUT');
  assert.ok(request);
  assert.equal(JSON.parse(request.body).auxiliaryDepartments, false);
  assert.equal(JSON.parse(request.body).verificationAssistEnabled, true);
  const failed = await loadOptions({ scannerSaveStatus: 503 });
  await failed.click('enableVerificationAssist');
  assert.notEqual(failed.data.verificationAssistEnabled, true);
  assert.match(failed.elements.verificationAssistStatus.textContent, /Could not save/);
});

test('native verification saves its desired state only after explicit permission and backend acknowledgement', async () => {
  const ui = await loadOptions();
  await ui.click('enableNativeVerification');
  assert.equal(ui.permissionRequests.length, 1);
  assert.equal(ui.data.verificationNativeEnabled, true);
  assert.equal(ui.fetches.some(call => call.path === '/scanner-settings' && call.method === 'PUT'), true);
  const denied = await loadOptions({ nativePermission: false });
  await denied.click('enableNativeVerification');
  assert.equal(denied.fetches.some(call => call.method === 'PUT'), false);
  assert.notEqual(denied.data.verificationNativeEnabled, true);
  const missingDebugger = await loadOptions({ debuggerPermission: false });
  await missingDebugger.click('enableVerificationAssist');
  assert.equal(missingDebugger.fetches.some(call => call.method === 'PUT'), false);
});

test('simultaneous verification toggles preserve both acknowledged settings', async () => {
  const ui = await loadOptions({ scannerSettingsBody: scannerSettings() });
  await Promise.all([ui.click('enableVerificationAssist'), ui.click('enableNativeVerification')]);
  assert.equal(ui.backend.scannerSettings.verificationAssistEnabled, true);
  assert.equal(ui.backend.scannerSettings.verificationNativeEnabled, true);
  assert.equal(ui.data.verificationAssistEnabled, true);
  assert.equal(ui.data.verificationNativeEnabled, true);
});

test('two Options pages merge independent toggles and preserve unrelated form changes', async () => {
  const shared = { scannerSettings: scannerSettings(), relaySettings: {
    schemaVersion: 1, refreshMinutes: 30, pageDelaySeconds: 15, sweepMode: 'none' },
    data: { endpoint: 'http://127.0.0.1:8787/ingest' }, locks: reviewerLocks() };
  const first = await loadOptions({ shared });
  const second = await loadOptions({ shared });
  await Promise.all([first.click('enableVerificationAssist'), second.click('enableNativeVerification')]);
  assert.equal(shared.scannerSettings.verificationAssistEnabled, true);
  assert.equal(shared.scannerSettings.verificationNativeEnabled, true);
  first.elements.auxiliaryDepartments.checked = false;
  second.elements.rotateScannerTabs.checked = false;
  first.elements.pageDelaySeconds.value = 25;
  second.elements.refreshMinutes.value = 60;
  await Promise.all([first.click('save'), second.click('save')]);
  assert.equal(shared.scannerSettings.auxiliaryDepartments, false);
  assert.equal(shared.scannerSettings.rotateScannerTabs, false);
  assert.equal(shared.scannerSettings.verificationAssistEnabled, true);
  assert.equal(shared.scannerSettings.verificationNativeEnabled, true);
  assert.equal(shared.relaySettings.pageDelaySeconds, 25);
  assert.equal(shared.relaySettings.refreshMinutes, 60);
});

test('an untouched stale form cannot overwrite a newer toggle or keyword update', async () => {
  const shared = { scannerSettings: scannerSettings(), relaySettings: null,
    data: { endpoint: 'http://127.0.0.1:8787/ingest' }, locks: reviewerLocks() };
  const stale = await loadOptions({ shared });
  const newer = await loadOptions({ shared });
  newer.elements.auxiliaryKeywordQueries.value = 'Couch\nPet';
  await newer.click('save');
  await Promise.all([newer.click('enableVerificationAssist'), stale.click('save')]);
  assert.equal(shared.scannerSettings.verificationAssistEnabled, true);
  assert.deepEqual(shared.scannerSettings.auxiliaryKeywordQueries, ['Couch', 'Pet']);
  assert.equal(stale.elements.auxiliaryKeywordQueries.value, 'Couch\nPet');
});

test('stale Options pages use the latest saved connection unless the user edits it', async () => {
  const shared = { scannerSettings: scannerSettings(), relaySettings: null,
    data: { endpoint: 'http://127.0.0.1:8787/ingest', token: 'old-local-token' }, locks: reviewerLocks() };
  const first = await loadOptions({ shared });
  const stale = await loadOptions({ shared });
  first.elements.token.value = 'new-local-token';
  await first.click('save');
  await stale.click('enableVerificationAssist');
  await stale.click('save');
  assert.equal(shared.data.token, 'new-local-token');
  const updates = stale.fetches.filter(call => call.method === 'PUT');
  assert.ok(updates.every(call => call.headers['X-Ingest-Token'] === 'new-local-token'));
  assert.equal(stale.elements.token.value, 'new-local-token');
  stale.elements.token.value = 'explicit-replacement';
  await stale.click('save');
  assert.equal(shared.data.token, 'explicit-replacement');
});

test('unavailable cross-page locking leaves settings unchanged and reports a failed save', async () => {
  const ui = await loadOptions({ locks: {}, initial: { auxiliaryDepartments: true } });
  ui.elements.auxiliaryDepartments.checked = false;
  await ui.click('save');
  assert.equal(ui.data.auxiliaryDepartments, true);
  assert.equal(ui.fetches.some(call => call.method === 'PUT'), false);
  assert.match(ui.elements.saved.textContent, /coordination is unavailable/);
});

test('a headers-ready but stalled health body times out and releases the connection button', async () => {
  let stalled = false;
  const ui = await loadOptions({ fetchImpl(url) {
    if (stalled && new URL(url).pathname === '/health') {
      return { ok: true, status: 200, text: () => new Promise(() => {}) };
    }
  } });
  stalled = true;
  const pending = ui.click('checkConnection');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.elements.checkConnection.disabled, true);
  const deadline = [...ui.timers.entries()].find(([, timer]) => timer.ms === 15_000);
  assert.ok(deadline);
  ui.timers.delete(deadline[0]); deadline[1].callback();
  await pending;
  assert.equal(ui.elements.checkConnection.disabled, false);
  assert.match(ui.elements.endpointError.textContent, /timed out/);
});

test('department and keyword coverage default on while brands stay off and primary settings survive', async () => {
  const ui = await loadOptions({ initial: { sweepMode: 'high-value', pageDelaySeconds: 25,
    refreshMinutes: 0, token: 'private-token' } });
  assert.equal(ui.elements.auxiliaryDepartments.checked, true);
  assert.equal(ui.elements.auxiliaryKeywords.checked, true);
  assert.equal(ui.elements.auxiliaryBrands.checked, false);
  for (const term of ['Furniture', 'Chair', 'Chairs', 'Couch', 'Sofa', 'Pet', 'Wood']) {
    assert.ok(ui.elements.auxiliaryKeywordQueries.value.split('\n').includes(term), term);
  }
  assert.equal(ui.elements.sweepMode.value, 'high-value');
  assert.equal(ui.elements.pageDelaySeconds.value, 25);
  assert.equal(ui.elements.refreshMinutes.value, 0);
  assert.equal(ui.data.token, 'private-token');
  assert.equal(ui.writes.some(write => Object.keys(write).some(key => key.startsWith('auxiliary'))), false);
});

test('an explicit choice to disable additional scans survives the enabled defaults', async () => {
  const ui = await loadOptions({ initial: { auxiliaryDepartments: false, auxiliaryKeywords: false } });
  assert.equal(ui.elements.auxiliaryDepartments.checked, false);
  assert.equal(ui.elements.auxiliaryKeywords.checked, false);
});

test('scanner tab grouping and rotation default on without writing preferences on load', async () => {
  const ui = await loadOptions();
  assert.equal(ui.elements.groupScannerTabs?.checked, true);
  assert.equal(ui.elements.rotateScannerTabs?.checked, true);
  assert.equal(ui.writes.some(write => 'groupScannerTabs' in write || 'rotateScannerTabs' in write), false);
});

test('scanner tab choices preserve explicit false and keep the primary server contract unchanged', async () => {
  const ui = await loadOptions({ initial: { groupScannerTabs: false, rotateScannerTabs: false } });
  assert.equal(ui.elements.groupScannerTabs?.checked, false);
  assert.equal(ui.elements.rotateScannerTabs?.checked, false);
  await ui.click('save');
  assert.equal(ui.data.groupScannerTabs, false);
  assert.equal(ui.data.rotateScannerTabs, false);
  ui.elements.groupScannerTabs.checked = true;
  ui.elements.rotateScannerTabs.checked = true;
  await ui.click('save');
  assert.equal(ui.data.groupScannerTabs, true);
  assert.equal(ui.data.rotateScannerTabs, true);
  for (const request of ui.fetches.filter(call => call.path === '/relay-settings' && call.method === 'PUT')) {
    assert.deepEqual(Object.keys(JSON.parse(request.body)).sort(),
      ['pageDelaySeconds', 'refreshMinutes', 'schemaVersion', 'sweepMode']);
  }
});

test('failed settings save retains previous grouping and rotation choices', async () => {
  const ui = await loadOptions({ initial: { groupScannerTabs: true, rotateScannerTabs: true }, saveStatus: 503 });
  ui.elements.groupScannerTabs.checked = false;
  ui.elements.rotateScannerTabs.checked = false;
  await ui.click('save');
  assert.equal(ui.data.groupScannerTabs, true);
  assert.equal(ui.data.rotateScannerTabs, true);
  assert.match(ui.elements.saved.textContent, /Not saved/);
});

test('saved auxiliary settings reload and custom queries save deduplicated through the backend', async () => {
  const ui = await loadOptions({ initial: { auxiliaryDepartments: true, auxiliaryKeywords: true,
    auxiliaryBrands: true, auxiliaryKeywordQueries: ['Sofa', 'Pet'], token: 'private-token' } });
  assert.equal(ui.elements.auxiliaryDepartments.checked, true);
  assert.equal(ui.elements.auxiliaryKeywords.checked, true);
  assert.equal(ui.elements.auxiliaryBrands.checked, true);
  assert.equal(ui.elements.auxiliaryKeywordQueries.value, 'Sofa\nPet');
  ui.elements.auxiliaryKeywordQueries.value = '  Furniture \nchair\nFURNITURE\n \nPet\nCHAIR\n';
  await ui.click('save');
  assert.deepEqual(ui.data.auxiliaryKeywordQueries, ['Furniture', 'chair', 'Pet']);
  assert.equal(ui.data.auxiliaryDepartments, true);
  assert.equal(ui.data.auxiliaryKeywords, true);
  assert.equal(ui.data.auxiliaryBrands, true);
  const body = JSON.parse(ui.fetches.find(call => call.path === '/relay-settings' && call.method === 'PUT').body);
  assert.deepEqual(Object.keys(body).sort(), ['pageDelaySeconds', 'refreshMinutes', 'schemaVersion', 'sweepMode']);
  assert.equal(ui.data.token, 'private-token');
  assert.match(ui.elements.saved.textContent, /Saved/);
});

test('an enabled keyword worker requires a nonempty query and failed save preserves configuration', async () => {
  const ui = await loadOptions({ initial: { auxiliaryKeywords: false, auxiliaryKeywordQueries: ['Pet'] } });
  ui.elements.auxiliaryKeywords.checked = true;
  ui.elements.auxiliaryKeywordQueries.value = '\n  \n';
  await ui.click('save');
  assert.equal(ui.data.auxiliaryKeywords, false);
  assert.deepEqual(ui.data.auxiliaryKeywordQueries, ['Pet']);
  assert.match(ui.elements.auxiliaryKeywordError.textContent, /add at least one/i);
  assert.equal(ui.fetches.some(call => call.method === 'PUT'), false);
  assert.equal(ui.elements.auxiliaryKeywordQueries.focused, true);
});

test('explicitly empty disabled query lists reload and save without restoring presets', async () => {
  const ui = await loadOptions({ initial: { auxiliaryKeywordQueries: [], auxiliaryKeywords: false } });
  assert.equal(ui.elements.auxiliaryKeywordQueries.value, '');
  await ui.click('save');
  assert.deepEqual(ui.data.auxiliaryKeywordQueries, []);
  assert.equal(ui.data.auxiliaryKeywords, false);
});

test('keyword limits are shown beside the input without submitting or losing stored queries', async () => {
  for (const value of ['x'.repeat(81), Array.from({ length: 129 }, (_, index) => `Item ${index}`).join('\n')]) {
    const ui = await loadOptions({ initial: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
    ui.elements.auxiliaryKeywordQueries.value = value;
    await ui.click('save');
    assert.deepEqual(ui.data.auxiliaryKeywordQueries, ['Pet']);
    assert.match(ui.elements.auxiliaryKeywordError.textContent, /80 characters|128 distinct/);
    assert.equal(ui.fetches.some(call => call.method === 'PUT'), false);
  }
});

test('invalid stored keywords do not stop primary settings from loading', async () => {
  const ui = await loadOptions({ initial: { auxiliaryKeywordQueries: {}, sweepMode: 'high-value' } });
  assert.equal(ui.elements.sweepMode.value, 'high-value');
  assert.match(ui.elements.auxiliaryKeywordError.textContent, /keyword|search term/i);
  assert.ok(ui.fetches.some(call => call.path === '/relay-settings'));
});

test('failed relay-settings save does not partially enable auxiliary scans', async () => {
  const ui = await loadOptions({ initial: { auxiliaryDepartments: false }, saveStatus: 503 });
  ui.elements.auxiliaryDepartments.checked = true;
  await ui.click('save');
  assert.equal(ui.data.auxiliaryDepartments, false);
  assert.match(ui.elements.saved.textContent, /Not saved/);
});

test('brand scanning is only available with departments and keywords remain editable while off', async () => {
  const ui = await loadOptions({ initial: { auxiliaryDepartments: false, auxiliaryKeywords: false } });
  assert.equal(ui.elements.auxiliaryBrands.disabled, true);
  assert.equal(ui.elements.auxiliaryKeywordQueries.disabled, false);
  ui.elements.auxiliaryDepartments.checked = true;
  ui.elements.auxiliaryDepartments.listeners.get('change')();
  assert.equal(ui.elements.auxiliaryBrands.disabled, false);
  ui.elements.auxiliaryBrands.checked = true;
  ui.elements.auxiliaryDepartments.checked = false;
  ui.elements.auxiliaryDepartments.listeners.get('change')();
  await ui.click('save');
  assert.equal(ui.data.auxiliaryBrands, false);
});

test('auxiliary worker progress and labels render as text without parsing server markup', async () => {
  const ui = await loadOptions({ auxiliaryResponse: { ok: true, lanes: [
    { lane: 'departments', tabId: 11, targetLabel: '<img src=x onerror=alert(1)>',
      status: 'scanning', completedTargets: 3, totalTargets: 12, pages: 9, failures: 1 },
    { lane: 'keywords', tabId: 12, targetLabel: 'Pet', status: 'paused',
      completedTargets: 2, totalTargets: 5, pages: 6, failures: 0 }
  ] } });
  const text = ui.elements.auxiliaryStatus.textContent;
  assert.match(text, /Department scan/);
  assert.match(text, /<img src=x onerror=alert\(1\)>/);
  assert.match(text, /3 of 12/);
  assert.match(text, /9 pages/);
  assert.match(text, /1 fail/);
  assert.match(text, /Keyword scan/);
  assert.match(text, /Pet/);
  assert.match(text, /paused/i);
  assert.match(text, /3 of 12 targets advanced; completion unverified/);
  assert.doesNotMatch(text, /3 of 12 targets complete/);
});

test('auxiliary progress separates verified completions, attempted targets and unresolved coverage', async () => {
  const ui = await loadOptions({ auxiliaryResponse: { ok: true, lanes: [
    { lane: 'departments', status: 'partial', completedTargets: 3, attemptedTargets: 5,
      unresolvedTargets: 2, totalTargets: 12, pages: 110, failures: 2, coverageLimitedTargets: 2 }
  ] } });
  const text = ui.elements.auxiliaryStatus.textContent;
  assert.match(text, /3 of 12 targets verified complete; 5 attempted; 2 unresolved/);
  assert.match(text, /2 targets have no confirmed final page; coverage remains unresolved/);
  assert.doesNotMatch(text, /page limit|25|further filters|site cap/i);
});

test('legacy or invalid progress counters never imply verified completion', async () => {
  for (const extra of [{}, { attemptedTargets: 5 }, { attemptedTargets: 5, unresolvedTargets: '2' },
    { attemptedTargets: -1, unresolvedTargets: 2 }]) {
    const ui = await loadOptions({ auxiliaryResponse: { ok: true, lanes: [
      { lane: 'departments', status: 'complete', completedTargets: 3, totalTargets: 12, ...extra }
    ] } });
    const text = ui.elements.auxiliaryStatus.textContent;
    assert.match(text, /completion unverified/);
    assert.doesNotMatch(text, /targets (?:verified )?complete|Round complete/);
  }
});

test('verified empty targets are reported separately from unresolved coverage', async () => {
  const lane = { lane: 'keywords', status: 'scanning', completedTargets: 4,
    attemptedTargets: 6, unresolvedTargets: 1, totalTargets: 10, emptyTargets: 3 };
  const ui = await loadOptions({ auxiliaryResponse: { ok: true, lanes: [lane] } });
  assert.match(ui.elements.auxiliaryStatus.textContent, /3 verified empty/);
  assert.match(ui.elements.auxiliaryStatus.textContent, /1 unresolved/);
  for (const emptyTargets of [undefined, -1, '3', true, 5]) {
    const old = await loadOptions({ auxiliaryResponse: { ok: true, lanes: [{ ...lane, emptyTargets }] } });
    assert.doesNotMatch(old.elements.auxiliaryStatus.textContent, /verified empty/);
  }
});

test('old or unavailable background leaves the settings usable and shows a recovery message', async () => {
  for (const options of [{ auxiliaryResponse: { ok: true } }, { auxiliaryResponse: null },
    { auxiliaryError: true }]) {
    const ui = await loadOptions(options);
    assert.match(ui.elements.auxiliaryStatus.textContent, /unavailable|reload/i);
    await ui.click('save');
    assert.match(ui.elements.saved.textContent, /Saved/);
  }
});

test('partial rounds and missing per-lane fields are visible without breaking rendering', async () => {
  const ui = await loadOptions({ auxiliaryResponse: { ok: true, lanes: [null,
    { lane: 'departments', status: 'partial', failures: 2, truncatedTargets: 3,
      lastError: '<script>site changed</script>' },
    { lane: 'keywords' }
  ] } });
  assert.match(ui.elements.auxiliaryStatus.textContent, /Round incomplete/);
  assert.match(ui.elements.auxiliaryStatus.textContent, /3 targets? .*limit/);
  assert.match(ui.elements.auxiliaryStatus.textContent, /<script>site changed<\/script>/);
  assert.match(ui.elements.auxiliaryStatus.textContent, /Keyword scan/);
});

test('restart additional scans uses the separate coordinator endpoint and handles failures', async () => {
  const ui = await loadOptions();
  await ui.click('restartAuxiliary');
  assert.equal(ui.messages.some(message => message.type === 'reviewer:aux-restart'), true);
  assert.equal(ui.messages.some(message => message.type === 'reviewer:restart'), false);
  assert.match(ui.elements.auxiliaryActionStatus.textContent, /restart/i);
  const failed = await loadOptions({ restartResponse: { ok: false, error: 'No reviewer tab.' } });
  await failed.click('restartAuxiliary');
  assert.match(failed.elements.auxiliaryActionStatus.textContent, /Could not restart/);
  assert.equal(failed.elements.restartAuxiliary.disabled, false);
});
