import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const backgroundSource = fs.readFileSync(
  new URL('../extension/background.js', import.meta.url), 'utf8'
);
const contentSource = fs.readFileSync(
  new URL('../extension/content.js', import.meta.url), 'utf8'
);
const optionsSource = fs.readFileSync(
  new URL('../extension/options.js', import.meta.url), 'utf8'
);
const optionsHtml = fs.readFileSync(new URL('../extension/options.html', import.meta.url), 'utf8');
const sweepModesSource = fs.readFileSync(
  new URL('../extension/sweep-modes.js', import.meta.url), 'utf8'
);
const policySource = fs.readFileSync(new URL('../extension/runtime-policy.js', import.meta.url), 'utf8');
const diagnosticsSource = fs.readFileSync(new URL('../extension/diagnostics.js', import.meta.url), 'utf8');
const auxiliaryScansSource = fs.readFileSync(new URL('../extension/auxiliary-scans.js', import.meta.url), 'utf8');
const tabWorkspaceSource = fs.readFileSync(new URL('../extension/tab-workspace.js', import.meta.url), 'utf8');
const facetInputSource = fs.readFileSync(new URL('../extension/facet-input.js', import.meta.url), 'utf8');
const departmentDataSource = fs.readFileSync(new URL('../extension/department-data.js', import.meta.url), 'utf8');
const scannerControlSource = fs.readFileSync(new URL('../extension/scanner-control.js', import.meta.url), 'utf8');
const controlsSource = fs.readFileSync(new URL('../extension/page-controls.js', import.meta.url), 'utf8');
const liveObserverSource = fs.readFileSync(new URL('../extension/live-observer.js', import.meta.url), 'utf8');
const relaySettingsSource = fs.readFileSync(new URL('../extension/relay-settings.js', import.meta.url), 'utf8');
const challengeEvidenceSource = fs.readFileSync(new URL('../extension/challenge-evidence.js', import.meta.url), 'utf8');
const verificationAutomationSource = ['verification-engine.js', 'verification-driver.js',
  'verification-automation.js'].map(name => fs.readFileSync(new URL(`../extension/${name}`, import.meta.url), 'utf8')).join('\n');

function event() {
  return { listeners: [], addListener(fn) { this.listeners.push(fn); },
    removeListener(fn) { this.listeners = this.listeners.filter(listener => listener !== fn); } };
}

function storageArea(initial = {}) {
  const data = { ...initial };
  return {
    data,
    async get(keys) {
      if (keys == null) return { ...data };
      if (typeof keys === 'string') return keys in data ? { [keys]: data[keys] } : {};
      if (Array.isArray(keys)) {
        return Object.fromEntries(
          keys.filter((key) => key in data).map((key) => [key, data[key]])
        );
      }
      return Object.fromEntries(
        Object.entries(keys).map(([key, fallback]) =>
          [key, data[key] === undefined ? fallback : data[key]])
      );
    },
    async set(values) { Object.assign(data, values); },
    async remove(keys) {
      for (const key of Array.isArray(keys) ? keys : [keys]) delete data[key];
    }
  };
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async text() { return JSON.stringify(body); }
  };
}

function loadBackground({
  tabs = [], probes = new Map(), summary = {}, fetchImpl = null, alarms = {},
  localArea = null, sessionArea = null, nativeRestart = null, automation = null,
  productionDefaults = false, scannerBackend = false
} = {}) {
  const local = localArea || storageArea();
  // Primary-only scenarios explicitly opt out of the additional default lanes.
  // Dedicated integration cases below exercise the production defaults.
  if (!productionDefaults) {
    local.data.auxiliaryDepartments ??= false;
    local.data.auxiliaryKeywords ??= false;
  }
  const session = sessionArea || storageArea();
  const sync = storageArea();
  const fetchCalls = [];
  const roleMessages = [];
  const pauseMessages = [];
  const restartMessages = [];
  const tabActions = [];
  const alarmCalls = [];
  const badgeCalls = [];
  const titleCalls = [];
  const alarmState = new Map(Object.entries(alarms));
  const chrome = {
    storage: { local, session, sync, onChanged: event() },
    runtime: { id: 'fixture', onMessage: event(), onStartup: event(), onInstalled: event(),
      getURL(path) { return `chrome-extension://fixture/${path}`; }, async openOptionsPage() {} },
    alarms: {
      onAlarm: event(),
      async get(name) { return alarmState.get(name); },
      async create(name, info) {
        alarmCalls.push({ name, info });
        alarmState.set(name, info);
      },
      async clear(name) { return alarmState.delete(name); }
    },
    action: {
      onClicked: event(),
      async setBadgeText(options) { badgeCalls.push({ ...options }); },
      async setTitle(options) { titleCalls.push({ ...options }); },
      async setBadgeBackgroundColor() {}
    },
    tabs: {
      onRemoved: event(),
      onUpdated: event(),
      onActivated: event(),
      async query() { return tabs; },
      async get(id) { return tabs.find((tab) => tab.id === id); },
      async create(options) {
        tabActions.push({ type: 'create', options });
        const tab = { id: 1000 + tabs.length, windowId: 1, index: tabs.length,
          status: 'loading', url: options.url };
        tabs.push(tab);
        return tab;
      },
      async update(id, options) { tabActions.push({ type: 'update', id, options }); },
      async reload(id, options) { tabActions.push({ type: 'reload', id, options }); },
      async sendMessage(id, message) {
        if (message.type === 'reviewer:challenge-probe') {
          const reply = probes.get(id);
          if (reply instanceof Error || reply == null) throw reply || new Error('no receiver');
          return typeof reply === 'function' ? reply() : reply;
        }
        if (message.type === 'reviewer:pause') {
          pauseMessages.push({ id, message });
          return { ok: true };
        }
        if (message.type === 'reviewer:probe') {
          const reply = probes.get(id);
          if (reply instanceof Error || reply == null) throw reply || new Error('no receiver');
          return typeof reply === 'function' ? reply() : reply;
        }
        if (message.type === 'reviewer:set-role') {
          roleMessages.push({ id, message });
          return { ok: true };
        }
        if (message.type === 'reviewer:restart-native') {
          restartMessages.push({ id, message });
          return nativeRestart?.(id, message);
        }
        return { ok: true };
      }
    }
  };
  const context = vm.createContext({
    chrome,
    URL,
    AbortController,
    setTimeout,
    clearTimeout,
    console,
    fetch: async (url, init) => {
      fetchCalls.push(init);
      if (fetchImpl) return fetchImpl(url, init, fetchCalls.length);
      return jsonResponse(summary);
    }
  });
  vm.runInContext(
    `${sweepModesSource}\n${policySource}\n${diagnosticsSource}\n${relaySettingsSource}\n${scannerControlSource}\n` +
    // Legacy collector cases isolate the existing server contract; explicit
    // backend-control cases below and the dedicated suite run the real bridge.
    (scannerBackend ? '' : 'ReviewerScannerControl.create = () => ({syncSettings: async () => true, syncCommands: async () => true, syncStatus: async () => true});\n') +
    `${challengeEvidenceSource}\n${verificationAutomationSource}\n${departmentDataSource}\n${auxiliaryScansSource}\n${tabWorkspaceSource}\n${facetInputSource}\n${backgroundSource.replace(/^importScripts\([^\n]+\);/m, '')}\n;globalThis.__test = {` +
      'selectPrimary, readCoordinator, handlePageReport, restartPrimary, post, checkedEndpoint,' +
      'isBotCheckUrl, handleBotCheck, pausedUntil, refreshTick, scheduleRefresh,' +
      'initialization, ensureAlarms, reportBrowserStatus, verificationAutomation, auxiliaryScans, tabWorkspace, scannerControl, scannerStatusSnapshot, syncRelaySettings: () => syncRelaySettings()};',
    context
  );
  if (automation) {
    chrome.permissions = { contains: async () => true };
    context.VerificationChrome.open = automation.open;
    context.VerificationEngine.run = automation.run;
  }
  vm.runInContext('globalThis.__test.syncBrowserDiagnostics = (...args) => syncBrowserDiagnostics(...args);', context);
  vm.runInContext('globalThis.__test.ensureReviewerTab = (...args) => ensureReviewerTab(...args);', context);
  vm.runInContext('globalThis.__test.setNow = value => { Date.now = () => value; };', context);
  return {
    api: context.__test, chrome, local, session, fetchCalls, roleMessages, tabActions, alarmCalls,
    badgeCalls, titleCalls, restartMessages, pauseMessages
  };
}

function workerMessage(bg, message, sender = { id: 'fixture', url: 'chrome-extension://fixture/options.html' }) {
  return new Promise((resolve, reject) => {
    const handled = bg.chrome.runtime.onMessage.listeners[0](message, sender, resolve);
    if (handled !== true) reject(new Error(`Unhandled message: ${message.type}`));
  });
}

test('production defaults start additional department and keyword lanes without Options setup', async () => {
  const bg = loadBackground({ productionDefaults: true });
  await bg.api.initialization;
  await bg.api.auxiliaryScans.tick();
  let status = await bg.api.auxiliaryScans.status();
  assert.deepEqual([...status.lanes.map(lane => lane.lane)].sort(), ['departments', 'keywords']);
  let departments = status.lanes.find(lane => lane.lane === 'departments');
  const keywords = status.lanes.find(lane => lane.lane === 'keywords');
  assert.equal(departments.status, 'waiting');
  assert.equal(departments.tabId, null);
  assert.ok(keywords.nextAt > departments.nextAt);
  bg.api.setNow(departments.nextAt);
  await bg.api.auxiliaryScans.tick();
  status = await bg.api.auxiliaryScans.status();
  departments = status.lanes.find(lane => lane.lane === 'departments');
  assert.equal(departments.status, 'scanning');
  assert.ok(Number.isInteger(departments.tabId));
  assert.equal(keywords.targetLabel, 'Furniture');
  assert.ok(['waiting', 'scanning'].includes(keywords.status));
  assert.ok(keywords.totalTargets >= 6);
});

test('native department input accepts only the current main-frame assignment and foreground authority', async () => {
  const home = 'https://www.walmart.com/reviews/claim-product';
  const tab = { id: 11, url: home, status: 'complete', windowId: 1, active: true };
  const bg = loadBackground({ tabs: [tab], productionDefaults: true });
  await bg.api.initialization;
  await bg.api.auxiliaryScans.tick();
  bg.api.setNow(bg.local.data.reviewerAuxiliaryScans.lanes.departments.nextAt);
  await bg.api.auxiliaryScans.tick();
  const lane = bg.local.data.reviewerAuxiliaryScans.lanes.departments;
  const id = lane.tabId;
  lane.targets.push({ key: 'Baby', kind: 'department', label: 'Baby', targetUrl: home,
    selection: { kind: 'department', label: 'Baby', parentUrl: home }, attempts: 0 });
  lane.cursor = 1; lane.status = 'scanning';
  const worker = await bg.chrome.tabs.get(id);
  worker.url = home; worker.status = 'complete'; worker.active = true; tab.active = false;
  bg.chrome.tabs.query = async query => [tab, worker].filter(t =>
    Object.entries(query || {}).every(([key, value]) => t[key] === value));
  bg.chrome.windows = { async get() { return { focused: true, state: 'normal' }; } };
  await bg.api.tabWorkspace.sync([11, id]);
  assert.equal((await bg.api.tabWorkspace.request(id, lane.generation, null, 10_000)).granted, true);
  const input = [];
  bg.chrome.debugger = { async attach() { input.push('attach'); }, async detach() { input.push('detach'); },
    async sendCommand(_target, method, params) {
      input.push(params.type || method);
      return method === 'Runtime.evaluate' ? { result: { value: { ok: true, x: 10, y: 20, clock: 1 } } } : {};
    } };
  const message = { type: 'reviewer:facet-input', generation: lane.generation, selection: lane.targets[1].selection };
  const sender = { id: 'fixture', frameId: 0, url: home, tab: worker };
  assert.equal((await workerMessage(bg, message, sender)).ok, true);
  assert.equal(input.filter(t => t === 'mousePressed').length, 1);
  const before = input.length;
  for (const change of [{ generation: lane.generation - 1 },
    { selection: { ...message.selection, label: 'Pet' } },
    { selection: { ...message.selection, parentUrl: `${home}?q=Pet` } }]) {
    assert.equal((await workerMessage(bg, { ...message, ...change }, sender)).ok, false);
  }
  for (const invalid of [{ ...sender, frameId: 1 }, { ...sender, url: 'https://www.walmart.com/blocked' }]) {
    let reply;
    bg.chrome.runtime.onMessage.listeners[0](message, invalid, value => { reply = value; });
    assert.equal(reply.ok, false);
  }
  await bg.api.tabWorkspace.noteActivity(id);
  assert.equal((await workerMessage(bg, message, sender)).ok, false);
  assert.equal(input.length, before);
});

test('uncertain facet input survives worker initialization and resets only on browser startup', async () => {
  const bg = loadBackground({ localArea: storageArea({ reviewerFacetInputUnreleased: true }) });
  await bg.api.initialization;
  assert.equal(bg.local.data.reviewerFacetInputUnreleased, true);
  await bg.chrome.runtime.onInstalled.listeners[0]();
  assert.equal(bg.local.data.reviewerFacetInputUnreleased, true);
  await bg.chrome.runtime.onStartup.listeners[0]();
  assert.equal(bg.local.data.reviewerFacetInputUnreleased, false);
});

const backendScannerSettings = () => ({ schemaVersion: 1,
  auxiliaryDepartments: true, auxiliaryKeywords: true, auxiliaryBrands: false,
  auxiliaryKeywordQueries: ['Furniture', 'Chair', 'Couch', 'Pet'],
  groupScannerTabs: true, rotateScannerTabs: true,
  verificationAssistEnabled: true, verificationNativeEnabled: false,
  pathPattern: '^/reviews/claim-product' });

test('real backend bridge overrides stale lane flags and exports ownership plus safe verification evidence', async () => {
  const tab = { id: 11, windowId: 1, active: false, frozen: true,
    status: 'complete', url: 'https://www.walmart.com/blocked?private=hidden' };
  const until = Date.now() + 60_000;
  const budget = { attempts: [10], checks: { '11:1': 10 } };
  const bg = loadBackground({ scannerBackend: true, tabs: [tab],
    localArea: storageArea({ token: 'never-export-token', auxiliaryDepartments: false,
      auxiliaryKeywords: false, verificationAssistBudget: budget,
      botCheck: { pausedUntil: until, count: 7, lastAt: 1, tabId: 11 },
      verificationAssistLastResult: { attempt: 10, outcome: 'input-error',
        errorStage: 'move', errorReason: 'timeout', pressSent: false, cleanupConfirmed: false,
        error: 'raw private failure' },
      scannerCommandReceipts: { restartHome: { outcome: 'complete', at: 1 } } }),
    fetchImpl(url) {
      const path = new URL(url).pathname;
      return jsonResponse(path === '/scanner-settings' ? { settings: backendScannerSettings() }
        : path === '/scanner-commands' ? { commands: null } : { ok: true });
    } });
  bg.chrome.windows = { async get() { return { focused: false }; } };
  await bg.api.initialization;
  assert.equal(bg.local.data.auxiliaryDepartments, true);
  assert.equal(bg.local.data.auxiliaryKeywords, true);
  await bg.api.auxiliaryScans.tick();
  const report = await bg.api.scannerStatusSnapshot();
  assert.deepEqual([...report.lanes.map(lane => [lane.lane, lane.enabled, lane.owned])],
    [['departments', true, false], ['keywords', true, false]]);
  assert.equal(report.verification.attempt, 10);
  assert.equal(report.verification.errorStage, 'move');
  assert.equal(report.verification.errorReason, 'timeout');
  assert.equal(report.verification.pressSent, false);
  assert.equal(report.verification.tabActive, false);
  assert.equal(report.verification.tabFrozen, true);
  assert.equal(report.verification.windowFocused, false);
  assert.equal(report.commands.restartHome, null);
  assert.equal(report.primary.phase, 'waiting-verification');
  assert.equal(await bg.api.scannerControl.syncStatus(), true);
  assert.deepEqual(bg.local.data.verificationAssistBudget, budget);
  const output = JSON.stringify(report);
  for (const secret of ['never-export-token', 'private=hidden', 'raw private failure', 'endpoint']) {
    assert.equal(output.includes(secret), false, secret);
  }
  if (process.env.SCANNER_CONTRACT_PATH) fs.writeFileSync(process.env.SCANNER_CONTRACT_PATH, output);
});

test('backend home restart defers through verification then executes once without resetting verifier budgets', async () => {
  const tab = { id: 11, windowId: 1, index: 0, status: 'complete',
    url: 'https://www.walmart.com/reviews/claim-product?page=8' };
  const until = Date.now() + 60_000;
  const budget = { attempts: [10], checks: { '11:1': 10 } };
  const bg = loadBackground({ scannerBackend: true, tabs: [tab],
    probes: new Map([[11, { ready: true, reviewerReady: true }]]),
    localArea: storageArea({ verificationAssistEnabled: false, verificationAssistBudget: budget,
      botCheck: { pausedUntil: until, count: 7, lastAt: 1, tabId: 11 } }),
    fetchImpl(url) {
      const path = new URL(url).pathname;
      return jsonResponse(path === '/scanner-settings' ? { settings: null }
        : path === '/scanner-commands' ? { commands: { schemaVersion: 1,
          restartHomeId: 'home-recovery-1', restartAdditionalId: null } } : { ok: true });
    } });
  await bg.api.initialization;
  await bg.api.scannerControl.syncCommands();
  assert.equal(bg.local.data.scannerCommandReceipts, undefined);
  assert.equal(bg.local.data.botCheck.pausedUntil, until);
  assert.equal(bg.tabActions.length, 0);
  // Simulate confirmed page recovery; a backend command cannot clear this itself.
  await bg.local.set({ botCheck: { pausedUntil: 0, count: 7, lastAt: 1 } });
  await bg.api.scannerControl.syncCommands();
  assert.equal(bg.local.data.scannerCommandReceipts.restartHome.outcome, 'complete');
  const actions = bg.tabActions.length;
  assert.ok(actions > 0);
  await bg.api.scannerControl.syncCommands();
  assert.equal(bg.tabActions.length, actions);
  assert.deepEqual(bg.local.data.verificationAssistBudget, budget);
});

for (const stage of ['selection', 'revocation']) test(`backend home restart preserves its checkpoint if verification arrives during lease ${stage}`, async () => {
  const tab = { id: 11, windowId: 1, index: 0, status: 'complete',
    url: 'https://www.walmart.com/reviews/claim-product?page=8' };
  const bg = loadBackground({ scannerBackend: true, tabs: [tab],
    probes: new Map([[11, { ready: true, reviewerReady: true }]]),
    fetchImpl(url) {
      const path = new URL(url).pathname;
      return jsonResponse(path === '/scanner-settings' ? { settings: null }
        : path === '/scanner-commands' ? { commands: { schemaVersion: 1,
          restartHomeId: 'pause-race-home', restartAdditionalId: null } } : { ok: true });
    } });
  await bg.api.initialization;
  const selected = await bg.api.selectPrimary();
  const sweep = { generation: selected.state.generation, mode: 'none', total: 17,
    visited: [1, 2, 3, 4, 5, 6, 7], lastProgressAt: Date.now() };
  await bg.local.set({ 'sweep:11': sweep, reviewerResume: { url: tab.url, tabId: 11,
    generation: selected.state.generation, sweepMode: 'none',
    pathPattern: '^/reviews/claim-product', sweep } });
  const send = bg.chrome.tabs.sendMessage;
  let inject = true;
  let pause;
  bg.chrome.tabs.sendMessage = async (id, message) => {
    if (inject && message.type === 'reviewer:set-role' && message.active === (stage === 'selection')) {
      inject = false;
      pause = bg.api.handleBotCheck(id, 'test-race');
      if (stage === 'revocation') await pause;
    }
    return send(id, message);
  };
  await bg.api.scannerControl.syncCommands();
  await pause;
  assert.ok(bg.local.data.botCheck.pausedUntil > Date.now());
  assert.deepEqual([...bg.local.data.reviewerResume.sweep.visited], [1, 2, 3, 4, 5, 6, 7]);
  assert.equal(bg.local.data.scannerCommandReceipts.restartHome.outcome, 'paused');
  assert.equal(bg.local.data.scannerCommandIds.includes('restartHome:pause-race-home'), false);
  assert.equal(bg.tabActions.length, 0);
});

function contentMessage(content, message) {
  return new Promise(resolve => content.chrome.runtime.onMessage.listeners[0](message, {}, resolve));
}

test('diagnostics retain a bounded history and export only allowlisted fields', async () => {
  const bg = loadBackground();
  await bg.local.set({ token: 'secret-token', endpoint: 'http://localhost:8787/ingest',
    refreshMinutes: 10, pageDelaySeconds: 15, sweepMode: 'none',
    reviewerDiagnostics: [{ at: Date.now() - 8 * 86400_000, event: 'challenge' }] });
  await Promise.all(Array.from({ length: 310 }, (_, index) => workerMessage(bg, {
    type: 'reviewer:diagnostic', event: 'navigation-native',
    details: { page: index % 17 + 1, token: 'secret-token', url: 'https://private.example',
      title: 'private item', reason: 'private raw error' }
  }, { tab: REVIEW_TAB })));
  const result = await workerMessage(bg, { type: 'reviewer:diagnostics' });
  assert.equal(result.ok, true);
  assert.equal(result.report.events.length, 300);
  assert.equal(result.report.schemaVersion, 1);
  const output = JSON.stringify(result.report);
  for (const privateText of ['secret-token', 'private.example', 'private item', 'private raw error', 'endpoint']) {
    assert.equal(output.includes(privateText), false);
  }
  assert.equal(result.report.settings.refreshMinutes, 10);
  assert.ok(result.report.events.every(entry => entry.tabId === 11));
});

test('diagnostic snapshots mirror to loopback only when changed and retry after failure', async () => {
  const paths = [];
  let fail = false;
  const bg = loadBackground({ fetchImpl(url) {
    paths.push(new URL(url).pathname);
    return jsonResponse({}, fail ? 503 : 200);
  } });
  await bg.api.initialization;
  await bg.local.set({ token: 'local-auth-secret', refreshMinutes: 10 });
  assert.equal(await bg.api.syncBrowserDiagnostics(), true);
  assert.equal(await bg.api.syncBrowserDiagnostics(), true);
  assert.deepEqual(paths, ['/browser-diagnostics']);
  assert.equal(JSON.stringify(JSON.parse(bg.fetchCalls[0].body)).includes('local-auth-secret'), false);
  await workerMessage(bg, { type: 'reviewer:diagnostic', event: 'page-loaded' }, { tab: REVIEW_TAB });
  fail = true;
  assert.equal(await bg.api.syncBrowserDiagnostics(), false);
  assert.match(bg.local.data.diagnosticsSyncError, /503/);
  fail = false;
  assert.equal(await bg.api.syncBrowserDiagnostics(), true);
  assert.equal(paths.length, 3);
  assert.equal(bg.local.data.diagnosticsSyncError, '');
});

test('diagnostic mirroring never sends a report or token to a non-loopback destination', async () => {
  const bg = loadBackground();
  await bg.local.set({ endpoint: 'https://example.org/ingest', token: 'local-auth-secret' });
  assert.equal(await bg.api.syncBrowserDiagnostics(), false);
  assert.equal(bg.fetchCalls.length, 0);
});

test('unchanged diagnostic mirror suppression survives service-worker recreation', async () => {
  const first = loadBackground();
  await first.api.initialization;
  await first.api.syncBrowserDiagnostics();
  const second = loadBackground({ localArea: first.local, sessionArea: first.session });
  await second.api.initialization;
  assert.equal(await second.api.syncBrowserDiagnostics(), true);
  assert.equal(second.fetchCalls.length, 0);
});

test('JSON scan settings refresh the browser cache and preserve explicit refresh zero', async () => {
  let current = { schemaVersion: 1, refreshMinutes: 10, pageDelaySeconds: 15, sweepMode: 'none' };
  const bg = loadBackground({ fetchImpl() { return jsonResponse({ settings: current }); } });
  await bg.api.initialization;
  assert.equal(await bg.api.syncRelaySettings(), true);
  assert.equal(bg.local.data.refreshMinutes, 10);
  current = { ...current, refreshMinutes: 0, pageDelaySeconds: 25, sweepMode: 'high-value' };
  assert.equal(await bg.api.syncRelaySettings(), true);
  assert.equal(bg.local.data.refreshMinutes, 0);
  assert.equal(bg.local.data.pageDelaySeconds, 25);
  assert.equal(bg.local.data.sweepMode, 'high-value');
});

test('a missing reviewer tab is opened once even when recovery checks overlap', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  const results = await Promise.all([bg.api.ensureReviewerTab(), bg.api.ensureReviewerTab()]);
  assert.ok(results.some(Boolean));
  assert.equal(bg.tabActions.length, 1);
  assert.equal(bg.tabActions[0].type, 'create');
  assert.equal(bg.tabActions[0].options.url, 'https://www.walmart.com/reviews/claim-product');
  assert.equal(bg.tabActions[0].options.active, false);
  assert.equal(await bg.api.ensureReviewerTab(), false);
});

test('automatic tab opening respects disabled refresh, verification and blocked tabs', async () => {
  for (const setup of ['disabled', 'paused', 'blocked']) {
    const bg = loadBackground({ tabs: setup === 'blocked'
      ? [{ ...REVIEW_TAB, url: 'https://www.walmart.com/blocked' }] : [] });
    await bg.api.initialization;
    if (setup === 'disabled') await bg.local.set({ refreshMinutes: 0 });
    if (setup === 'paused') await bg.local.set({ botCheck: { pausedUntil: Date.now() + 60_000 } });
    assert.equal(await bg.api.ensureReviewerTab(), false);
    assert.equal(bg.tabActions.length, 0);
  }
});

test('an existing loading reviewer tab prevents duplicate recovery tabs', async () => {
  const bg = loadBackground({ tabs: [{ ...REVIEW_TAB, status: 'loading' }] });
  await bg.api.initialization;
  assert.equal(await bg.api.ensureReviewerTab(), false);
  assert.equal(bg.tabActions.length, 0);
});

test('a pending reviewer navigation or verification redirect prevents duplicate recovery tabs', async () => {
  for (const pendingUrl of ['https://www.walmart.com/reviews/claim-product',
    'https://www.walmart.com/blocked']) {
    const bg = loadBackground({ tabs: [{ ...REVIEW_TAB, url: '', status: 'loading', pendingUrl }] });
    await bg.api.initialization;
    assert.equal(await bg.api.ensureReviewerTab(), false);
    assert.equal(bg.tabActions.length, 0);
  }
});

test('failed tab creation backs off across worker recreation', async () => {
  const first = loadBackground();
  await first.api.initialization;
  first.chrome.tabs.create = async () => { throw new Error('no window'); };
  assert.equal(await first.api.ensureReviewerTab(), false);
  const second = loadBackground({ localArea: first.local, sessionArea: first.session });
  await second.api.initialization;
  assert.equal(await second.api.ensureReviewerTab(), false);
  assert.equal(second.tabActions.length, 0);
});

test('tab recovery opens a window when Chrome has only a background worker', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  bg.chrome.tabs.create = async () => { throw new Error('no current window'); };
  bg.chrome.windows = { async create(options) {
    bg.tabActions.push({ type: 'window-create', options });
    return { tabs: [{ id: 101, url: options.url }] };
  } };
  assert.equal(await bg.api.ensureReviewerTab(), true);
  assert.equal(bg.tabActions.length, 1);
  assert.equal(bg.tabActions[0].options.focused, false);
});

test('installation opens the missing page without immediately reloading it again', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  await bg.chrome.runtime.onInstalled.listeners[0]();
  assert.deepEqual(bg.tabActions.map(action => action.type), ['create']);
});

test('extension installation retries updated native pagination without clearing verification', async () => {
  const bg = loadBackground({ tabs: [REVIEW_TAB] });
  await bg.api.initialization;
  const verification = { count: 4, lastAt: Date.now(), pausedUntil: Date.now() + 3_600_000,
    challengedTabIds: [REVIEW_TAB.id] };
  await bg.local.set({ nativeNavigationPausedUntil: Date.now() + 3_600_000, botCheck: verification });
  await bg.chrome.runtime.onInstalled.listeners[0]();
  assert.equal(bg.local.data.nativeNavigationPausedUntil, undefined);
  assert.deepEqual(bg.local.data.botCheck, verification);
  assert.equal(bg.tabActions.length, 0);
});

test('ordinary browser startup preserves native pagination cooldown', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  const until = Date.now() + 3_600_000;
  await bg.local.set({ nativeNavigationPausedUntil: until });
  await bg.chrome.runtime.onStartup.listeners[0]();
  assert.equal(bg.local.data.nativeNavigationPausedUntil, until);
});

test('browser startup synchronizes disabled JSON settings before reopening a tab', async () => {
  const bg = loadBackground({ fetchImpl() { return jsonResponse({ settings: {
    schemaVersion: 1, refreshMinutes: 0, pageDelaySeconds: 15, sweepMode: 'none'
  } }); } });
  await bg.api.initialization;
  await bg.local.set({ refreshMinutes: 10 });
  await bg.chrome.runtime.onStartup.listeners[0]();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(bg.local.data.refreshMinutes, 0);
  assert.equal(bg.tabActions.length, 0);
});

test('closing the last reviewer tab triggers replacement through the removal event', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  await bg.session.set({ reviewerCoordinator: { tabId: 11, generation: 9, selectedAt: 1 } });
  bg.chrome.tabs.onRemoved.listeners[0](11, { isWindowClosing: false });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(bg.tabActions.filter(action => action.type === 'create').length, 1);
});

test('tab replacement remembers a newly committed page before it delivers any items', async () => {
  const tab = { ...REVIEW_TAB };
  const tabs = [tab];
  const bg = loadBackground({ tabs, probes: new Map([[tab.id, { ready: true }]]) });
  await bg.api.initialization;
  await bg.api.selectPrimary();
  tab.url = 'https://www.walmart.com/reviews/claim-product?page=7&affinityOverride=default';
  await bg.chrome.tabs.onUpdated.listeners[0](tab.id, { url: tab.url, status: 'complete' }, tab);
  tabs.splice(0);
  await bg.chrome.tabs.onRemoved.listeners[0](tab.id, { isWindowClosing: false });
  assert.equal(bg.tabActions.at(-1)?.options.url, tab.url);
});

async function savedReviewerRound({ mode = 'none', fallback = false } = {}) {
  const query = mode === 'sweep' ? 'm' : mode === 'high-value' || fallback ? 'Kitchen' : '';
  const tab = { ...REVIEW_TAB, url: `https://www.walmart.com/reviews/claim-product?page=7${query ? `&q=${query}` : ''}` };
  const tabs = [tab];
  const probes = new Map([[tab.id, { ready: true }]]);
  const bg = loadBackground({ tabs, probes, summary: { seen: 1 } });
  await bg.api.initialization;
  await bg.local.set({ sweepMode: mode });
  if (fallback) await bg.session.set({ reviewerCoordinator: {
    tabId: tab.id, generation: 9, selectedAt: Date.now(), paginationFallback: true
  } });
  const { state } = await bg.api.selectPrimary();
  await bg.local.set({ [`sweep:${tab.id}`]: {
    generation: state.generation, mode: fallback ? 'high-value' : mode,
    paginationFallback: fallback, query, total: 10, visited: [1, 2, 3, 4, 5, 6],
    endRetries: {}, startedAt: 123, completedPages: 20, completedQueries: 12
  } });
  const content = loadContent({ localArea: bg.local });
  Object.assign(content.api.role, { active: true, tabId: tab.id, generation: state.generation });
  content.location.href = tab.url;
  content.location.search = new URL(tab.url).search;
  content.pager.innerText = '1 2 10';
  content.chrome.runtime.sendMessage = message => workerMessage(bg, message, { tab: { ...tab } });
  await content.api.advanceDeliveredPage(state.generation);
  return { bg, tabs, tab, probes, content, state };
}

test('closing a tab transfers acknowledged scan progress to its replacement', async () => {
  const { bg, tabs, tab, probes } = await savedReviewerRound();
  tabs.splice(0);
  await bg.chrome.tabs.onRemoved.listeners[0](tab.id, { isWindowClosing: false });
  const replacement = tabs[0];
  assert.equal(replacement?.url, tab.url);
  replacement.status = 'complete';
  probes.set(replacement.id, { ready: true });
  const selected = await bg.api.selectPrimary();
  const content = loadContent({ localArea: bg.local });
  Object.assign(content.api.role, { active: true, tabId: replacement.id, generation: selected.state.generation });
  const progress = await content.api.readSweep();
  assert.deepEqual([...progress.visited], [1, 2, 3, 4, 5, 6, 7]);
  assert.equal(content.api.nextUnvisited(progress), 8);
  assert.equal(progress.startedAt, 123);
  assert.equal(bg.local.data[`sweep:${tab.id}`], undefined);
});

test('browser restart restores the page, search and progress with fresh session storage', async () => {
  for (const options of [{ mode: 'none' }, { mode: 'sweep' }, { mode: 'high-value' }, { fallback: true }]) {
    const { bg: first, tab } = await savedReviewerRound(options);
    const probes = new Map();
    const tabs = [];
    const bg = loadBackground({ localArea: first.local, tabs, probes });
    await bg.api.initialization;
    await bg.chrome.runtime.onStartup.listeners[0]();
    assert.equal(tabs[0]?.url, tab.url);
    tabs[0].status = 'complete';
    probes.set(tabs[0].id, { ready: true });
    const selected = await bg.api.selectPrimary();
    const content = loadContent({ localArea: bg.local });
    Object.assign(content.api.role, { active: true, tabId: tabs[0].id, generation: selected.state.generation });
    content.location.href = tab.url;
    content.location.search = new URL(tab.url).search;
    const progress = await content.api.readSweep();
    assert.equal(progress.query, options.mode === 'sweep' ? 'm' : options.mode === 'high-value' || options.fallback ? 'Kitchen' : '');
    assert.deepEqual([...progress.visited], [1, 2, 3, 4, 5, 6, 7]);
    assert.equal(progress.completedQueries, 12);
    assert.equal(selected.state.paginationFallback === true, options.fallback === true);
    assert.equal(await bg.api.ensureReviewerTab(), false);
  }
});

test('passive and stale documents cannot overwrite the saved reviewer page', async () => {
  const { bg, tab, state } = await savedReviewerRound();
  for (const [id, generation] of [[tab.id + 1, state.generation], [tab.id, state.generation - 1]]) {
    const reply = await workerMessage(bg, { type: 'reviewer:checkpoint', generation },
      { tab: { ...tab, id, url: 'https://www.walmart.com/reviews/claim-product?page=99' } });
    assert.equal(reply.ok, false);
  }
  const restarted = loadBackground({ localArea: bg.local });
  await restarted.api.initialization;
  await restarted.api.ensureReviewerTab();
  assert.equal(restarted.tabActions.at(-1).options.url, tab.url);
});

test('changed scan settings and unrelated browsing cannot become recovery destinations', async () => {
  for (const changed of ['mode', 'path', 'manual-search', 'external']) {
    const { bg: first, tabs, tab } = await savedReviewerRound();
    if (changed === 'mode') await first.local.set({ sweepMode: 'sweep' });
    if (changed === 'path') await first.local.set({ pathPattern: '^/reviews/claim-product$' });
    if (changed === 'manual-search' || changed === 'external') {
      tab.url = changed === 'external' ? 'https://example.com/' : 'https://www.walmart.com/reviews/claim-product?q=manual';
      await first.chrome.tabs.onUpdated.listeners[0](tab.id, { url: tab.url }, tab);
      // Ignore the safe replacement created by navigating away.
      tabs.splice(0);
    }
    const bg = loadBackground({ localArea: first.local });
    await bg.api.initialization;
    await bg.api.ensureReviewerTab();
    assert.equal(bg.tabActions.at(-1).options.url, changed === 'mode' || changed === 'path'
      ? 'https://www.walmart.com/reviews/claim-product' : 'https://www.walmart.com/reviews/claim-product?page=7');
  }
});

test('extension reload repairs the same page and scan instead of restarting at page one', async () => {
  const { bg: first, tab } = await savedReviewerRound({ mode: 'sweep' });
  const probes = new Map(); // Previously loaded content scripts are invalidated.
  const bg = loadBackground({ localArea: first.local, tabs: [tab], probes });
  await bg.api.initialization;
  await bg.chrome.runtime.onInstalled.listeners[0]();
  assert.deepEqual(bg.tabActions.map(action => action.type), ['reload']);
  assert.equal(bg.tabActions[0].id, tab.id);
  probes.set(tab.id, { ready: true });
  const selected = await bg.api.selectPrimary();
  const progress = bg.local.data[`sweep:${tab.id}`];
  assert.equal(progress.generation, selected.state.generation);
  assert.equal(progress.query, 'm');
  assert.deepEqual([...progress.visited], [1, 2, 3, 4, 5, 6, 7]);
});

test('first upgrade preserves the existing page and legacy tab progress', async () => {
  const tab = { ...REVIEW_TAB, url: 'https://www.walmart.com/reviews/claim-product?page=7' };
  const bg = loadBackground({ tabs: [tab], localArea: storageArea({ 'sweep:11': {
    generation: 37, mode: 'none', total: 10, visited: [1, 2, 3, 4, 5, 6], startedAt: 123
  } }) });
  await bg.api.initialization;
  await bg.chrome.runtime.onInstalled.listeners[0]();
  assert.deepEqual(bg.tabActions.map(action => action.type), ['reload']);
  assert.deepEqual([...bg.local.data['sweep:11'].visited], [1, 2, 3, 4, 5, 6]);
  assert.equal(bg.local.data.reviewerResume.url, tab.url);
});

test('completed and explicitly restarted sweeps do not resurrect saved progress', async () => {
  for (const action of ['complete', 'restart']) {
    const { bg, tab, state } = await savedReviewerRound();
    if (action === 'complete') {
      await bg.local.remove(`sweep:${tab.id}`);
      await workerMessage(bg, { type: 'reviewer:sweep-complete', generation: state.generation, pages: 10 }, { tab });
    } else {
      assert.equal((await bg.api.restartPrimary()).ok, true);
    }
    const restarted = loadBackground({ localArea: bg.local });
    await restarted.api.initialization;
    await restarted.api.ensureReviewerTab();
    assert.equal(restarted.tabActions[0].options.url, action === 'complete'
      ? tab.url : 'https://www.walmart.com/reviews/claim-product');
    assert.ok(!bg.local.data.reviewerResume?.sweep);
  }
});

test('closing a browser window retains the checkpoint without opening an immediate replacement', async () => {
  const { bg, tabs, tab } = await savedReviewerRound();
  tabs.splice(0);
  await bg.chrome.tabs.onRemoved.listeners[0](tab.id, { isWindowClosing: true });
  assert.equal(bg.tabActions.length, 0);
  const restarted = loadBackground({ localArea: bg.local });
  await restarted.api.initialization;
  await restarted.api.ensureReviewerTab();
  assert.equal(restarted.tabActions[0].options.url, tab.url);
});

test('invalid saved URLs cannot redirect automatic tab recovery', async () => {
  for (const url of ['https://example.com/reviews/claim-product?page=7',
    'https://www.walmart.com/orders?page=7', 'javascript:alert(1)',
    'https://user:password@www.walmart.com/reviews/claim-product',
    'https://www.walmart.com/reviews/claim-product?q=manual']) {
    const bg = loadBackground({ localArea: storageArea({ reviewerResume: {
      url, sweepMode: 'none', pathPattern: '^/reviews/claim-product', sweep: null
    } }) });
    await bg.api.initialization;
    await bg.api.ensureReviewerTab();
    assert.equal(bg.tabActions[0].options.url, 'https://www.walmart.com/reviews/claim-product');
  }
});

test('a catalogue tab opened by the browser supervisor resumes the saved URL', async () => {
  const { bg: first, tab } = await savedReviewerRound({ mode: 'sweep' });
  const home = { ...REVIEW_TAB, id: 77, url: 'https://www.walmart.com/reviews/claim-product' };
  const bg = loadBackground({ localArea: first.local, tabs: [home], probes: new Map([[77, { ready: true }]]) });
  await bg.api.initialization;
  bg.chrome.tabs.update = async (id, options) => {
    bg.tabActions.push({ type: 'update', id, options });
    home.pendingUrl = options.url;
    home.status = 'loading';
  };
  await bg.chrome.runtime.onStartup.listeners[0]();
  assert.equal(bg.tabActions[0]?.options.url, tab.url);
  assert.equal((await bg.api.readCoordinator()).tabId, null, 'Old homepage document must remain passive');
  home.url = tab.url;
  delete home.pendingUrl;
  home.status = 'complete';
  const selected = await bg.api.selectPrimary();
  assert.equal(bg.local.data['sweep:77'].generation, selected.state.generation);
  assert.deepEqual([...bg.local.data['sweep:77'].visited], [1, 2, 3, 4, 5, 6, 7]);
});

test('a passive catalogue tab takes over at the closed primary page', async () => {
  const { bg, tabs, tab, probes } = await savedReviewerRound();
  const backup = { ...REVIEW_TAB, id: 22, index: 2, url: 'https://www.walmart.com/reviews/claim-product' };
  tabs.splice(0, 1, backup);
  probes.set(22, { ready: true });
  bg.chrome.tabs.update = async (id, options) => {
    bg.tabActions.push({ type: 'update', id, options });
    backup.pendingUrl = options.url;
    backup.status = 'loading';
  };
  await bg.chrome.tabs.onRemoved.listeners[0](tab.id, { isWindowClosing: false });
  assert.deepEqual(bg.tabActions.map(action => action.type), ['update']);
  assert.equal(bg.tabActions[0].id, backup.id);
  assert.equal(bg.tabActions[0].options.url, tab.url);
  assert.equal((await bg.api.readCoordinator()).tabId, null);
});

test('ordinary pending page navigation retains the current scan lease and checkpoint', async () => {
  const { bg, tab, state } = await savedReviewerRound();
  tab.pendingUrl = tab.url.replace('page=7', 'page=8');
  tab.status = 'loading';
  const selected = await bg.api.selectPrimary();
  assert.equal(selected.state.tabId, tab.id);
  assert.equal(selected.state.generation, state.generation);
  assert.deepEqual([...bg.local.data[`sweep:${tab.id}`].visited], [1, 2, 3, 4, 5, 6, 7]);
  assert.equal(bg.local.data.reviewerResume.url, tab.url);
});

test('reusing a recovery tab rechecks disabled monitoring and manual navigation', async () => {
  for (const change of ['disabled', 'manual']) {
    const { bg: first } = await savedReviewerRound();
    const home = { ...REVIEW_TAB, id: 77, url: 'https://www.walmart.com/reviews/claim-product' };
    const bg = loadBackground({ localArea: first.local, tabs: [home] });
    await bg.api.initialization;
    let changed = false;
    bg.chrome.tabs.query = async () => {
      const snapshot = [{ ...home }];
      if (!changed) {
        changed = true;
        if (change === 'disabled') await bg.local.set({ refreshMinutes: 0 });
        else home.url += '?q=manual';
      }
      return snapshot;
    };
    await bg.api.ensureReviewerTab();
    assert.equal(bg.tabActions.some(action => action.type === 'update' && action.id === home.id), false);
    if (change === 'disabled') assert.equal(bg.tabActions.length, 0);
  }
});

test('upgrading after a mode change cannot adopt the previous mode scan', async () => {
  const { bg: first, tab } = await savedReviewerRound({ mode: 'sweep' });
  await first.local.set({ sweepMode: 'high-value' });
  const bg = loadBackground({ localArea: first.local, tabs: [tab] });
  await bg.api.initialization;
  await bg.chrome.runtime.onInstalled.listeners[0]();
  assert.equal(bg.local.data[`sweep:${tab.id}`], undefined);
  assert.ok(!bg.local.data.reviewerResume?.sweep);
});

test('an unconfirmed checkpoint retries before allowing pagination to continue', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.local.data['sweep:1'] = { generation: 9, mode: 'none', total: 4, visited: [1], endRetries: {} };
  content.pager.innerText = '1 2 3 4';
  content.api.setCollector(() => [{ item_id: 'checkpoint-retry-item' }]);
  let saved = false;
  content.chrome.runtime.sendMessage = async message => message.type === 'reviewer:checkpoint'
    ? { ok: saved } : message.type === 'reviewer:focus' ? { ok: true, enabled: false, reason: 'disabled' }
      : { ok: true, active: true, tabId: 1, primaryTabId: 1, generation: 9 };
  await content.api.relay();
  assert.equal(content.api.getActionTimer(), null, 'Navigation must await a durable checkpoint');
  assert.ok([...content.timerCallbacks.values()].some(timer => timer.ms === 5_000), 'A failed checkpoint needs a retry');
  saved = true;
  await content.api.relay();
  assert.ok(content.api.getActionTimer());
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1, 2]);
});

test('missing or invalid JSON settings retain the last browser settings and secrets', async () => {
  let current = null;
  const bg = loadBackground({ fetchImpl() { return jsonResponse({ settings: current }); } });
  await bg.api.initialization;
  await bg.local.set({ refreshMinutes: 7, pageDelaySeconds: 20, token: 'private-token' });
  assert.equal(await bg.api.syncRelaySettings(), true);
  current = { schemaVersion: 1, refreshMinutes: 0, pageDelaySeconds: 15,
    sweepMode: 'none', token: 'malicious-token' };
  assert.equal(await bg.api.syncRelaySettings(), false);
  assert.equal(bg.local.data.refreshMinutes, 7);
  assert.equal(bg.local.data.pageDelaySeconds, 20);
  assert.equal(bg.local.data.token, 'private-token');
});

test('Options loads authoritative JSON settings including disabled refreshing', async () => {
  const ui = loadOptions({ initial: { refreshMinutes: 30 }, scanSettingsBody: {
    schemaVersion: 1, refreshMinutes: 0, pageDelaySeconds: 20, sweepMode: 'none'
  } });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(ui.elements.refreshMinutes.value, 0);
  assert.equal(ui.local.data.refreshMinutes, 0);
  assert.equal(ui.local.data.pageDelaySeconds, 20);
});

test('a failed JSON save remains visibly unsuccessful and retains browser settings', async () => {
  const ui = loadOptions({ initial: { refreshMinutes: 30, pageDelaySeconds: 15 }, scanSaveStatus: 503 });
  await new Promise(resolve => setTimeout(resolve, 0));
  ui.elements.refreshMinutes.value = '10';
  await ui.elements.save.listeners.get('click')();
  assert.equal(ui.local.data.refreshMinutes, 30);
  assert.match(ui.elements.saved.textContent, /Not saved/);
});

test('verification messages from reviewer tabs pause once and unrelated senders are refused', async () => {
  const bg = pausedBackground();
  assert.equal((await workerMessage(bg, { type: 'reviewer:verification' },
    { tab: { id: 99, url: 'https://example.org' } })).ok, false);
  const first = await workerMessage(bg, { type: 'reviewer:verification', reason: 'text' }, { tab: REVIEW_TAB });
  assert.equal(first.count, 1);
  const repeat = await workerMessage(bg, { type: 'reviewer:verification', reason: 'text' }, { tab: REVIEW_TAB });
  assert.equal(repeat.duplicate, true);
  const report = (await workerMessage(bg, { type: 'reviewer:diagnostics' })).report;
  assert.equal(report.events.filter(entry => entry.event === 'challenge').length, 1);
});

function loadOptions({
  initial = {}, sweepStatus = { ok: true, tabCount: 1 },
  scanSettingsBody = null, scanSaveStatus = 200, healthBody = null, debuggerPermission = true,
  retryResponse = { ok: true }
} = {}) {
  const optionsFetches = [];
  const optionsMessages = [];
  class FakeNode {
    constructor() {
      this.children = [];
      this._text = '';
      this.value = '';
      this.checked = false;
      this.disabled = false;
      this.className = '';
      this.listeners = new Map();
    }

    get textContent() {
      return this._text + this.children.map((child) => child.textContent || '').join(' ');
    }

    set textContent(value) {
      this._text = String(value ?? '');
      this.children = [];
    }

    appendChild(child) {
      this.children.push(child);
      return child;
    }

    replaceChildren(...children) {
      this._text = '';
      this.children = children;
    }

    addEventListener(type, listener) {
      this.listeners.set(type, listener);
    }
  }

  // Use the actual markup's IDs so a dangling script binding fails this VM.
  const ids = [...optionsHtml.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  const elements = Object.fromEntries(ids.map((id) => [id, new FakeNode()]));
  const document = {
    getElementById(id) { return elements[id]; },
    createElement() { return new FakeNode(); },
    createDocumentFragment() { return new FakeNode(); },
    createTextNode(text) {
      const node = new FakeNode();
      node.textContent = text;
      return node;
    }
  };
  const local = storageArea({
    endpoint: 'http://127.0.0.1:8787/ingest',
    ...initial
  });
  const chrome = {
    storage: { local },
    permissions: { contains: async () => debuggerPermission },
    runtime: {
      async sendMessage(message) {
        optionsMessages.push(message);
        if (message.type === 'reviewer:verification-retry') return retryResponse;
        if (message.type === 'reviewer:status') return sweepStatus;
        return { ok: true };
      }
    }
  };
  const response = (body, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    async text() { return JSON.stringify(body); },
    async json() { return body; }
  });
  const context = vm.createContext({
    chrome,
    document,
    URL,
    AbortController,
    setTimeout,
    clearTimeout,
    setInterval() {},
    console,
    fetch: async (url, init = {}) => {
      const path = new URL(url).pathname;
      optionsFetches.push({ path, ...init });
      if (path === '/relay-settings') {
        return response({ settings: init.method === 'PUT' ? JSON.parse(init.body) : scanSettingsBody },
          init.method === 'PUT' ? scanSaveStatus : 200);
      }
      if (path === '/scanner-settings') {
        return response({ settings: init.method === 'PUT' ? JSON.parse(init.body) : null },
          init.method === 'PUT' ? scanSaveStatus : 200);
      }
      return response(healthBody || {
        status: 'ok', notifier: 'TelegramNotifier', notifier_configured: true,
        seen_items: 3439, observed_values: { max_value_usd: 149.5 }
      });
    }
  });
  vm.runInContext(
    `${sweepModesSource}\n${policySource}\n${relaySettingsSource}\n${scannerControlSource}\n${optionsSource}\n;globalThis.__test = {` +
      'completedSweepPages, activeSweep, renderStatus};',
    context
  );
  return { api: context.__test, elements, local, optionsFetches, optionsMessages };
}

test('coordinator deterministically picks the leftmost reachable tab, then stays sticky', async () => {
  const tabs = [
    { id: 22, windowId: 1, index: 4, status: 'complete', url: 'https://www.walmart.com/reviews/claim-product' },
    { id: 11, windowId: 1, index: 2, status: 'complete', url: 'https://www.walmart.com/reviews/claim-product' }
  ];
  const probes = new Map([
    [11, { ready: true, active: false, generation: 0 }],
    [22, { ready: true, active: false, generation: 0 }]
  ]);
  const { api } = loadBackground({ tabs, probes });

  assert.equal((await api.selectPrimary()).state.tabId, 11);
  // Moving the backup earlier does not steal a healthy primary mid-sweep.
  tabs[0].index = 0;
  assert.equal((await api.selectPrimary()).state.tabId, 11);

  probes.set(11, new Error('content script gone'));
  assert.equal((await api.selectPrimary()).state.tabId, 22);
});

test('full-catalogue monitoring promotes an unfiltered tab over a sticky manual search', async () => {
  const cat = { ...REVIEW_TAB, url: REVIEW_TAB.url.split('?')[0] + '?q=&queryNups=Cat' };
  const plain = { ...REVIEW_TAB, id: 22, index: 2 };
  const bg = loadBackground({ tabs: [cat, plain], probes: new Map([
    [11, { ready: true, active: true, generation: 8 }],
    [22, { ready: true, active: false, generation: 8 }]
  ]) });
  bg.session.data.reviewerCoordinator = { tabId: 11, generation: 8, selectedAt: 1 };
  assert.equal((await bg.api.selectPrimary()).state.tabId, 22);
  assert.equal(bg.tabActions.length, 0, 'Manual search must not be rewritten');
  assert.equal(bg.roleMessages.find(entry => entry.id === 11).message.active, false);
});

test('Walmart default affinity pagination remains full-catalogue monitoring', async () => {
  const tab = { ...REVIEW_TAB,
    url: 'https://www.walmart.com/reviews/claim-product?q=&page=2&affinityOverride=default' };
  const bg = loadBackground({ tabs: [tab], probes: new Map([[11, { ready: true }]]) });
  assert.equal((await bg.api.selectPrimary()).state.tabId, 11);
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  let ran = false;
  content.api.queueWhenIdle(() => { ran = true; }, 15_000);
  content.location.href = tab.url;
  content.location.search = new URL(tab.url).search;
  await runNavigation(content);
  assert.equal(ran, true);
  assert.equal(content.api.role.active, true);
});

test('only manual filtered tabs cause one separate catalogue tab to open', async () => {
  for (const query of ['?queryNups=Cat', '?q=Cat', '?queryNups=&queryNups=Cat',
    '?facet=brand:custom', '?affinityOverride=store_led',
    '?affinityOverride=default&affinityOverride=store_led']) {
    const tab = { ...REVIEW_TAB, url: REVIEW_TAB.url.split('?')[0] + query };
    const bg = loadBackground({ tabs: [tab], probes: new Map([[11, { ready: true }]]) });
    await bg.api.initialization;
    assert.equal((await bg.api.selectPrimary()).state.tabId, null, query);
    assert.equal(await bg.api.ensureReviewerTab(), true, query);
    assert.equal(await bg.api.ensureReviewerTab(), false, query);
    assert.equal(bg.tabActions.length, 1, query);
    assert.equal(bg.tabActions[0].type, 'create', query);
    assert.equal(bg.tabActions[0].options.active, false, query);
    assert.equal(tab.url, REVIEW_TAB.url.split('?')[0] + query);
    await bg.api.restartPrimary('scheduled');
    assert.equal(bg.tabActions.some(action => action.id === 11), false);
  }
});

test('filtered regular-mode senders cannot ingest before a selection refresh', async () => {
  const tab = { ...REVIEW_TAB, url: REVIEW_TAB.url.split('?')[0] + '?queryNups=Cat' };
  const bg = loadBackground({ tabs: [tab] });
  bg.session.data.reviewerCoordinator = { tabId: 11, generation: 8, selectedAt: 1 };
  const reply = await bg.api.handlePageReport({ generation: 8, page: 1,
    items: [{ item_id: 'manual-search' }] }, { tab });
  assert.equal(reply.ok, false);
  assert.equal(reply.active, false);
  assert.equal(bg.fetchCalls.length, 0);
});

test('refresh repair never reloads a manual search when no catalogue tab is reachable', async () => {
  const tab = { ...REVIEW_TAB, url: 'https://www.walmart.com/reviews/claim-product?queryNups=Cat' };
  const bg = loadBackground({ tabs: [tab], probes: new Map([[11, { ready: true }]]) });
  await bg.api.initialization;
  await bg.api.refreshTick();
  assert.equal(bg.tabActions.length, 1);
  assert.equal(bg.tabActions[0].type, 'create');
  assert.equal(bg.tabActions[0].options.url, 'https://www.walmart.com/reviews/claim-product');
});

test('a queued page action stops when the user opens a manual search', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  let navigated = false;
  content.api.queueWhenIdle(() => { navigated = true; }, 15_000);
  content.location.href = 'https://www.walmart.com/reviews/claim-product?queryNups=Cat';
  content.location.search = '?queryNups=Cat';
  await runNavigation(content);
  assert.equal(navigated, false);
  assert.equal(content.api.role.active, false);
});

test('refresh repair revalidates manual browsing after reserving recovery', async () => {
  for (const pending of [false, true]) {
    const tab = { ...REVIEW_TAB };
    const bg = loadBackground({ tabs: [tab] });
    await bg.api.initialization;
    const originalSet = bg.session.set.bind(bg.session);
    bg.session.set = async values => {
      if (values['recovery:11']) {
        tab[pending ? 'pendingUrl' : 'url'] =
          'https://www.walmart.com/reviews/claim-product?queryNups=Cat';
      }
      return originalSet(values);
    };
    await bg.api.refreshTick();
    assert.equal(bg.tabActions.some(action => action.id === 11), false,
      `Repair must not reload changed ${pending ? 'pending' : 'committed'} scope`);
  }
});

test('queued page actions release the lease after leaving the reviewer route or origin', async () => {
  for (const target of ['https://www.walmart.com/orders',
    'https://www.walmart.com/account', 'https://example.org/reviews/claim-product']) {
    const content = loadContent();
    Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
    let navigated = false;
    content.api.queueWhenIdle(() => { navigated = true; }, 15_000);
    Object.assign(content.location, { href: target, pathname: new URL(target).pathname,
      origin: new URL(target).origin, search: '' });
    await runNavigation(content);
    assert.equal(navigated, false, target);
    assert.equal(content.api.role.active, false, target);
  }
});

test('a primary stuck loading past its lease fails over to a reachable backup', async () => {
  const tabs = [
    { id: 11, windowId: 1, index: 1, status: 'loading', url: 'https://www.walmart.com/reviews/claim-product?page=2' },
    { id: 22, windowId: 1, index: 2, status: 'complete', url: 'https://www.walmart.com/reviews/claim-product' }
  ];
  const probes = new Map([
    [11, new Error('hung load')],
    [22, { ready: true, active: false, generation: 4 }]
  ]);
  const { api, session } = loadBackground({ tabs, probes });
  session.data.reviewerCoordinator = { tabId: 11, generation: 4, selectedAt: 1 };

  const selected = await api.selectPrimary();

  assert.equal(selected.state.tabId, 22);
  assert.equal(selected.state.generation, 5);
});

test('only the primary sender can post and loopback is not mislabelled as local', async () => {
  const summary = {
    seen: 1, new: 1, duplicates: 0, filtered: 0, matched: 1,
    notified: 1, failed: 0, seeded: 0, value_known: 2,
    value_unknown: 0, min_value_usd: 10, max_value_usd: 20
  };
  const { api, session, local, fetchCalls } = loadBackground({ summary });
  session.data.reviewerCoordinator = { tabId: 11, generation: 7, selectedAt: 1 };

  const rejected = await api.handlePageReport(
    { generation: 7, page: 1, items: [{ item_id: 'x' }] },
    { tab: { id: 22 } }
  );
  assert.equal(rejected.ok, false);
  assert.equal(fetchCalls.length, 0);

  const accepted = await api.handlePageReport(
    { generation: 7, page: 1, items: [{ item_id: 'x' }] },
    { tab: { id: 11 } }
  );
  assert.equal(accepted.ok, true);
  assert.equal(fetchCalls.length, 1);
  assert.equal('targetAddressSpace' in fetchCalls[0], false);
  assert.equal(local.data.lastDuplicates, 0);
  assert.equal(local.data.lastValueKnown, 2);
  assert.equal(local.data.lastMinValue, 10);
});

test('neither extension fetch path overrides Chrome loopback classification', () => {
  assert.doesNotMatch(backgroundSource, /targetAddressSpace\s*:/);
  assert.doesNotMatch(optionsSource, /targetAddressSpace\s*:/);
});

test('Options labels relay counters as the latest page result', async () => {
  const now = Date.now();
  const { api, elements } = loadOptions({
    initial: {
      lastRelay: now,
      lastPage: 20,
      lastSeen: 80,
      lastNew: 21,
      lastDuplicates: 59,
      lastFiltered: 21,
      lastMatched: 0,
      lastNotified: 0,
      lastFailed: 0,
      lastPending: 0,
      lastValueKnown: 80,
      lastValueUnknown: 0,
      lastMinValue: 4.89,
      lastMaxValue: 33.99
    },
    sweepStatus: {
      ok: true,
      generation: 7,
      tabCount: 1,
      sweep: {
        generation: 7,
        total: 23,
        visited: Array.from({ length: 19 }, (_, index) => index + 1),
        startedAt: now - 60_000,
        lastProgressAt: now - 1_000
      }
    }
  });

  await api.renderStatus();

  const text = elements.status.textContent;
  assert.match(text, /Latest page result \(relayed just now\)/);
  assert.match(text, /0 alerted on this page/);
  assert.match(text, /21 new items on this page did not match the filters/);
  assert.match(text, /Sweep in progress: 20 of 23 pages done/);
});

test('Options distinguishes queued Discord work from delivery without personal filter controls', async () => {
  const { api, elements } = loadOptions({
    initial: { lastRelay: Date.now(), lastSeen: 8, lastNew: 0, lastDuplicates: 8,
      lastQueued: 2, lastNotified: 0, lastFiltered: 99 },
    healthBody: { notifier: 'DiscordNotifier', notifier_configured: true, discord: {
      live_sending: true, personal_filters_applied: false,
      jobs: { pending: 12, leased: 1, sent: 40, failed: 2, expired: 3, skipped: 4,
        'private-path': 'do-not-display' },
      last_delivery: { sent: 3, retried: 1, failed: 0, blocked: true,
        error: 'do-not-display' }
    } }
  });
  await api.renderStatus();
  const text = elements.status.textContent;
  assert.match(text, /2 observations safely queued for Discord/);
  assert.match(text, /0 delivered during intake/);
  assert.match(text, /12 pending, 1 processing/);
  assert.match(text, /40 sent, 2 failed, 3 expired, 4 skipped/);
  assert.match(text, /Last delivery pass: 3 sent, 1 retried, 0 failed/);
  assert.match(text, /Discord delivery is blocked/);
  assert.doesNotMatch(text, /nothing new to alert|did not match the filters|do-not-display|private-path/);
  assert.match(optionsHtml, /automatically routed by category and value/i);
  for (const id of ['minValue', 'maxValue', 'keywords', 'excludeKeywords',
    'matchMode', 'alertUnknown', 'saveRules', 'resetRules']) assert.equal(elements[id], undefined, id);
});

test('Options reports Discord sending off without claiming queued work has been sent', async () => {
  const { api, elements } = loadOptions({
    initial: { lastRelay: Date.now(), lastSeen: 2, lastNew: 0, lastQueued: 0 },
    healthBody: { notifier: 'DiscordNotifier', notifier_configured: true,
      discord: { live_sending: false, personal_filters_applied: false, jobs: { pending: 7 }, last_delivery: null } }
  });
  await api.renderStatus();
  const text = elements.status.textContent;
  assert.match(text, /Discord sending is off/);
  assert.match(text, /No additional Discord work queued from this page/);
  assert.doesNotMatch(text, /nothing new to alert|Last delivery pass:/);
});

test('Options retains legacy provider status without restoring personal filter controls', async () => {
  const { api, elements } = loadOptions({
    initial: { lastRelay: Date.now(), lastSeen: 2, lastNew: 0 }
  });
  await api.renderStatus();
  assert.equal(elements.saveRules, undefined);
  assert.equal(elements.minValue, undefined);
  assert.match(elements.status.textContent, /nothing new to alert/);
  assert.doesNotMatch(elements.status.textContent, /Discord delivery queue/);
});

test('Options only adds a just-acknowledged page to its current sweep', async () => {
  const now = Date.now();
  const base = {
    generation: 4,
    total: 23,
    visited: Array.from({ length: 19 }, (_, index) => index + 1),
    startedAt: now - 60_000,
    lastProgressAt: now - 1_000
  };
  const { api } = loadOptions({
    sweepStatus: {
      ok: true,
      generation: 5,
      tabCount: 1,
      sweep: { ...base, generation: 4 }
    }
  });

  assert.equal(api.completedSweepPages(base, {
    lastPage: 20, lastRelay: now, lastFailed: 0, lastPending: 0
  }), 20);
  assert.equal(api.completedSweepPages(base, {
    lastPage: 20, lastRelay: base.startedAt - 1, lastFailed: 0, lastPending: 0
  }), 19);
  assert.equal(api.completedSweepPages(base, {
    lastPage: 20, lastRelay: now, lastFailed: 1, lastPending: 0
  }), 19);
  assert.equal(api.completedSweepPages(base, {
    lastPage: 21, lastRelay: now, lastFailed: 0, lastPending: 0
  }), 19);
  assert.equal((await api.activeSweep()).sweep, null);
});

test('the endpoint must be the exact loopback ingest route', () => {
  const { api } = loadBackground();

  assert.equal(api.checkedEndpoint('http://127.0.0.1:8787/ingest'),
    'http://127.0.0.1:8787/ingest');
  assert.throws(() => api.checkedEndpoint('http://127.0.0.1:8787/wrong'), /exactly.*ingest/i);
  assert.throws(() => api.checkedEndpoint('https://127.0.0.1:8787/ingest'), /must use http/i);
});

test('delivery failures are a retryable negative acknowledgement', async () => {
  const { api, session, local } = loadBackground({
    summary: { seen: 1, new: 1, matched: 1, notified: 0, failed: 1 }
  });
  session.data.reviewerCoordinator = { tabId: 11, generation: 3, selectedAt: 1 };

  const reply = await api.handlePageReport(
    { generation: 3, page: 4, items: [{ item_id: 'retry-me' }] },
    { tab: { id: 11 } }
  );
  assert.equal(reply.ok, false);
  assert.equal(reply.retryable, true);
  assert.match(reply.error, /page will retry/i);
  assert.equal(local.data.lastFailed, 1);
  assert.equal(local.data.lastPage, 4);
});

test('a partial successful acknowledgement never advances the page', async () => {
  const { api, session } = loadBackground({
    summary: { seen: 0, new: 0, matched: 0, notified: 0, failed: 0, pending: 0 }
  });
  session.data.reviewerCoordinator = { tabId: 11, generation: 3, selectedAt: 1 };

  const reply = await api.handlePageReport(
    { generation: 3, page: 6, items: [{ item_id: 'not-acknowledged' }] },
    { tab: { id: 11 } }
  );
  assert.equal(reply.ok, false);
  assert.equal(reply.retryable, true);
  assert.match(reply.error, /acknowledged 0 of 1 items/i);
});

test('large pages are posted sequentially in 200-item batches with one aggregate status', async () => {
  const items = Array.from({ length: 401 }, (_, index) => ({ item_id: `item-${index}` }));
  const summaries = [
    {
      seen: 200, new: 100, duplicates: 100, filtered: 40, matched: 60,
      notified: 3, failed: 0, pending: 0, seeded: 1,
      value_known: 199, value_unknown: 1, min_value_usd: 10, max_value_usd: 50
    },
    {
      seen: 200, new: 20, duplicates: 180, filtered: 15, matched: 5,
      notified: 2, failed: 0, pending: 0, seeded: 0,
      value_known: 198, value_unknown: 2, min_value_usd: 2, max_value_usd: 70
    },
    {
      seen: 1, new: 1, filtered: 1, matched: 0, notified: 0, failed: 0,
      pending: 0, seeded: 0, value_known: 1, value_unknown: 0,
      min_value_usd: 7, max_value_usd: 7
    }
  ];
  let activeRequests = 0;
  let maxActiveRequests = 0;
  const { api, session, local, fetchCalls } = loadBackground({
    fetchImpl(_url, _init, callNumber) {
      activeRequests += 1;
      maxActiveRequests = Math.max(maxActiveRequests, activeRequests);
      const response = jsonResponse(summaries[callNumber - 1]);
      return {
        ...response,
        async text() {
          activeRequests -= 1;
          return response.text();
        }
      };
    }
  });
  session.data.reviewerCoordinator = { tabId: 11, generation: 4, selectedAt: 1 };

  const reply = await api.handlePageReport(
    { generation: 4, page: 8, items },
    { tab: { id: 11 } }
  );

  const bodies = fetchCalls.map((call) => JSON.parse(call.body).items);
  assert.deepEqual(bodies.map((batch) => batch.length), [200, 200, 1]);
  assert.equal(bodies[0][0].item_id, 'item-0');
  assert.equal(bodies[1][0].item_id, 'item-200');
  assert.equal(bodies[2][0].item_id, 'item-400');
  assert.equal(maxActiveRequests, 1);
  assert.equal(reply.ok, true);
  assert.deepEqual(
    {
      seen: reply.summary.seen,
      new: reply.summary.new,
      duplicates: reply.summary.duplicates,
      filtered: reply.summary.filtered,
      matched: reply.summary.matched,
      notified: reply.summary.notified,
      seeded: reply.summary.seeded,
      valueKnown: reply.summary.value_known,
      valueUnknown: reply.summary.value_unknown,
      min: reply.summary.min_value_usd,
      max: reply.summary.max_value_usd
    },
    {
      seen: 401, new: 121, duplicates: 280, filtered: 56, matched: 65,
      notified: 5, seeded: 1, valueKnown: 398, valueUnknown: 3, min: 2, max: 70
    }
  );
  assert.equal(local.data.lastSeen, 401);
  assert.equal(local.data.lastNew, 121);
  assert.equal(local.data.lastDuplicates, 280);
  assert.equal(local.data.lastNotified, 5);
  assert.equal(local.data.lastMinValue, 2);
  assert.equal(local.data.lastMaxValue, 70);
  assert.equal(local.data.lastPage, 8);
});

test('Discord queued observations aggregate across batches without being counted as delivered', async () => {
  const items = Array.from({ length: 401 }, (_, index) => ({ item_id: `queue-${index}` }));
  const summaries = [
    { seen: 200, new: 0, duplicates: 200, queued: 3, notified: 0 },
    { seen: 200, new: 200, duplicates: 0, queued: 200, notified: 0 },
    { seen: 1, new: 0, duplicates: 1, notified: 0 }
  ];
  const { api, local } = loadBackground({ fetchImpl(_url, _init, callNumber) {
    return jsonResponse(summaries[callNumber - 1]);
  } });
  const reply = await api.post(items, 7);
  assert.equal(reply.ok, true);
  assert.equal(reply.summary.queued, 203);
  assert.equal(reply.summary.notified, 0);
  assert.equal(local.data.lastQueued, 203);
  assert.equal(local.data.lastNotified, 0);
});

test('a later batch failure retries the whole page so completed batches dedupe safely', async () => {
  const items = Array.from({ length: 401 }, (_, index) => ({ item_id: `retry-${index}` }));
  let calls = 0;
  const { api, local, fetchCalls } = loadBackground({
    fetchImpl(_url, init) {
      calls += 1;
      const count = JSON.parse(init.body).items.length;
      if (calls === 2) return jsonResponse({ error: 'temporary outage' }, 503);
      return jsonResponse({
        seen: count,
        new: calls === 1 || calls >= 4 ? count : 0,
        duplicates: calls === 3 ? count : 0,
        failed: 0,
        pending: 0
      });
    }
  });

  const failed = await api.post(items, 9);
  assert.equal(failed.ok, false);
  assert.equal(failed.retryable, true);
  assert.match(failed.error, /batch 2 of 3.*HTTP 503/i);
  assert.deepEqual(fetchCalls.map((call) => JSON.parse(call.body).items.length), [200, 200]);
  assert.equal(local.data.lastRelay, undefined);

  const retried = await api.post(items, 9);
  assert.equal(retried.ok, true);
  assert.deepEqual(
    fetchCalls.map((call) => JSON.parse(call.body).items.length),
    [200, 200, 200, 200, 1]
  );
  assert.equal(JSON.parse(fetchCalls[0].body).items[0].item_id, 'retry-0');
  assert.equal(JSON.parse(fetchCalls[2].body).items[0].item_id, 'retry-0');
  assert.equal(retried.summary.seen, 401);
  assert.equal(local.data.ingestConsecutiveFailures, 0);
});

test('delivery failures and pending sends aggregate across all batches before retry', async () => {
  const items = Array.from({ length: 201 }, (_, index) => ({ item_id: `pending-${index}` }));
  const summaries = [
    { seen: 200, new: 1, duplicates: 199, matched: 1, failed: 1, pending: 0 },
    { seen: 1, new: 1, duplicates: 0, matched: 1, failed: 0, pending: 1 }
  ];
  const { api, session, local, fetchCalls } = loadBackground({
    fetchImpl(_url, _init, callNumber) {
      return jsonResponse(summaries[callNumber - 1]);
    }
  });
  session.data.reviewerCoordinator = { tabId: 11, generation: 2, selectedAt: 1 };

  const reply = await api.handlePageReport(
    { generation: 2, page: 3, items },
    { tab: { id: 11 } }
  );

  assert.equal(fetchCalls.length, 2);
  assert.equal(reply.ok, false);
  assert.equal(reply.retryable, true);
  assert.equal(reply.summary.failed, 1);
  assert.equal(reply.summary.pending, 1);
  assert.match(reply.error, /1 failed delivery.*1 delivery attempt is still pending/i);
  assert.equal(local.data.lastSeen, 201);
  assert.equal(local.data.lastFailed, 1);
  assert.equal(local.data.lastPending, 1);
});

test('restart revokes the old document lease before navigating to page one', async () => {
  const tabs = [{
    id: 11, windowId: 1, index: 0, status: 'complete',
    url: 'https://www.walmart.com/reviews/claim-product?page=7'
  }];
  const probes = new Map([[11, { ready: true, active: true, generation: 8 }]]);
  const { api, session, roleMessages, tabActions } = loadBackground({ tabs, probes });
  session.data.reviewerCoordinator = { tabId: 11, generation: 8, selectedAt: 1 };

  const reply = await api.restartPrimary('test');
  assert.equal(reply.ok, true);
  assert.equal(roleMessages[0].message.active, false);
  assert.equal(roleMessages[0].message.generation, 9);
  assert.equal(tabActions[0].type, 'update');
  assert.doesNotMatch(tabActions[0].options.url, /[?&]page=/);
});

test('scheduled restarts use an acknowledged native return after revoking the old lease', async () => {
  const tab = { id: 11, windowId: 1, index: 0, status: 'complete',
    url: 'https://www.walmart.com/reviews/claim-product?page=17' };
  const bg = loadBackground({ tabs: [tab], probes: new Map([[11,
    { ready: true, active: true, generation: 8 }]]), nativeRestart(_id, message) {
    assert.equal(bg.roleMessages.at(-1).message.active, false);
    assert.equal(bg.local.data['sweep:11'], undefined);
    return { ok: true, navigation: 'native', generation: message.generation };
  } });
  bg.session.data.reviewerCoordinator = { tabId: 11, generation: 8, selectedAt: 1 };
  bg.local.data['sweep:11'] = { generation: 8, visited: [1, 2], total: 17 };
  const reply = await bg.api.restartPrimary('scheduled');
  assert.equal(reply.ok, true);
  assert.equal(bg.restartMessages.length, 1);
  assert.equal(bg.restartMessages[0].message.generation, 9);
  assert.equal(bg.restartMessages[0].message.target, 'https://www.walmart.com/reviews/claim-product');
  assert.equal(bg.tabActions.length, 0);
});

test('unacknowledged native restart falls back to a full page-one load', async () => {
  for (const ack of [undefined, { ok: true }, { ok: true, navigation: 'native', generation: 1 }]) {
    const bg = loadBackground({ tabs: [{ id: 11, windowId: 1, index: 0, status: 'complete',
      url: 'https://www.walmart.com/reviews/claim-product?page=17' }],
    probes: new Map([[11, { ready: true, active: true, generation: 8 }]]),
    nativeRestart: () => ack });
    bg.session.data.reviewerCoordinator = { tabId: 11, generation: 8, selectedAt: 1 };
    assert.equal((await bg.api.restartPrimary('scheduled')).ok, true);
    assert.equal(bg.tabActions[0].type, 'update');
    assert.doesNotMatch(bg.tabActions[0].options.url, /[?&]page=/);
  }
});

test('a declined native restart cannot pull the user back from another search', async () => {
  const tab = { ...REVIEW_TAB, url: REVIEW_TAB.url.split('?')[0] + '?page=17' };
  const bg = loadBackground({ tabs: [tab], probes: new Map([[11,
    { ready: true, active: true, generation: 8 }]]), nativeRestart() {
    tab.url = REVIEW_TAB.url.split('?')[0] + '?q=changed-by-user';
    return { ok: false };
  } });
  bg.session.data.reviewerCoordinator = { tabId: 11, generation: 8, selectedAt: 1 };
  assert.equal((await bg.api.restartPrimary('scheduled')).ok, false);
  assert.equal(bg.tabActions.length, 0);
});

test('restart fallback respects a user navigation that has not committed yet', async () => {
  const tab = { ...REVIEW_TAB, url: REVIEW_TAB.url.split('?')[0] + '?page=17' };
  const bg = loadBackground({ tabs: [tab], probes: new Map([[11,
    { ready: true, active: true, generation: 8 }]]), nativeRestart() {
    tab.pendingUrl = 'https://www.walmart.com/orders';
    return { ok: false };
  } });
  bg.session.data.reviewerCoordinator = { tabId: 11, generation: 8, selectedAt: 1 };
  assert.equal((await bg.api.restartPrimary('scheduled')).ok, false);
  assert.equal(bg.tabActions.length, 0);
});

test('a challenge arriving during restart diagnostics prevents the fallback load', async () => {
  const bg = loadBackground({ tabs: [{ ...REVIEW_TAB, url: REVIEW_TAB.url.split('?')[0] + '?page=17' }],
    probes: new Map([[11, { ready: true, active: true, generation: 8 }]]) });
  await bg.api.initialization;
  bg.session.data.reviewerCoordinator = { tabId: 11, generation: 8, selectedAt: 1 };
  let release;
  let entered;
  const started = new Promise(resolve => { entered = resolve; });
  const originalSet = bg.local.set.bind(bg.local);
  bg.local.set = async values => {
    if (values.reviewerDiagnostics?.at(-1)?.event === 'scan-restart-load') {
      entered();
      await new Promise(resolve => { release = resolve; });
    }
    await originalSet(values);
  };
  const restart = bg.api.restartPrimary('scheduled');
  await started;
  const challenge = bg.api.handleBotCheck(11, 'text');
  for (let turn = 0; turn < 50 && (await bg.api.readCoordinator()).tabId !== null; turn += 1) {
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.equal((await bg.api.readCoordinator()).tabId, null);
  release();
  assert.equal((await restart).ok, false);
  await challenge;
  assert.equal(bg.tabActions.length, 0);
  assert.equal(bg.roleMessages.at(-1).message.active, false);
});

test('an overlapping pending delivery also keeps the page open for retry', async () => {
  const { api, session, local } = loadBackground({
    summary: { seen: 1, new: 1, matched: 1, notified: 0, failed: 0, pending: 1 }
  });
  session.data.reviewerCoordinator = { tabId: 11, generation: 4, selectedAt: 1 };

  const reply = await api.handlePageReport(
    { generation: 4, page: 5, items: [{ item_id: 'in-flight' }] },
    { tab: { id: 11 } }
  );
  assert.equal(reply.ok, false);
  assert.equal(reply.retryable, true);
  assert.match(reply.error, /still pending.*page will retry/i);
  assert.equal(local.data.lastPending, 1);
});

test('auxiliary content keeps exact assigned facet scope and rejects manual filters', async () => {
  const content = loadContent();
  content.location.href = 'https://www.walmart.com/reviews/claim-product?catId=4044&page=2';
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'departments', targetKey: 'Home',
      targetUrl: 'https://www.walmart.com/reviews/claim-product?catId=4044' } });
  assert.equal(content.api.keepMonitoringScope(), true);
  content.location.href += '&q=chair';
  assert.equal(content.api.keepMonitoringScope(), false);
});

test('auxiliary content uses its own mode even when the primary is doing alphabet searches', async () => {
  const content = loadContent();
  content.local.data.sweepMode = 'sweep';
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'keywords', targetKey: 'Chair',
      targetUrl: 'https://www.walmart.com/reviews/claim-product?q=Chair' } });
  const sweep = await content.api.readSweep();
  assert.equal(sweep.mode, 'none');
  assert.equal(sweep.targetKey, 'Chair');
});

test('auxiliary completion cannot overwrite homepage timing or counts', async () => {
  const content = loadContent();
  Object.assign(content.local.data, { lastSweepDone: 123, lastSweepStartedAt: 100, lastSweepPages: 16 });
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'keywords', targetKey: 'Chair', targetUrl: content.location.href } });
  content.local.data['sweep:7'] = { generation: 90, targetKey: 'Chair', visited: [1, 2] };
  await content.api.removeCurrentSweep(90, 2);
  assert.equal(content.local.data.lastSweepDone, 123);
  assert.equal(content.local.data.lastSweepStartedAt, 100);
  assert.equal(content.local.data.lastSweepPages, 16);
  assert.ok(content.sentMessages.some(m => m.type === 'reviewer:sweep-complete' && m.pages === 2));
});

test('auxiliary completion reports the site page cap even with a legacy child-discovery checkpoint', async () => {
  for (const [lane, kind, children, limited] of [
    ['keywords', 'keyword', 0, true], ['departments', 'brand', 0, true],
    ['departments', 'department', 0, true], ['departments', 'department', 2, true]
  ]) {
    const content = loadContent();
    content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
      auxiliary: { lane, targetKind: kind, targetKey: 'capped', targetUrl: content.location.href } });
    content.local.data['sweep:7'] = { generation: 90, targetKey: 'capped', total: 25,
      discoveredDepartments: children, visited: [1, 2] };
    await content.api.removeCurrentSweep(90, 25);
    const completion = content.sentMessages.find(m => m.type === 'reviewer:sweep-complete');
    assert.equal(completion.coverageLimited, limited, `${lane}/${kind}`);
  }
});

test('auxiliary completion acknowledgement failure remains retryable', async () => {
  const content = loadContent();
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'keywords', targetKey: 'Chair', targetUrl: content.location.href } });
  const checkpoint = { generation: 90, targetKey: 'Chair', mode: 'none', visited: [1, 2, 3],
    total: 25, discoveredDepartments: 2 };
  content.local.data['sweep:7'] = checkpoint;
  content.chrome.runtime.sendMessage = async () => { throw new Error('Worker suspended'); };
  assert.equal(await content.api.removeCurrentSweep(90, 2), false);
  assert.equal(content.api.getPhase(), 'relay-retry');
  assert.ok([...content.timerCallbacks.values()].some(timer => timer.ms === 5000));
  assert.deepEqual(content.local.data['sweep:7'], checkpoint);
  content.chrome.runtime.sendMessage = async () => ({ ok: true });
  assert.equal(await content.api.removeCurrentSweep(90, 25), true);
});

test('auxiliary top-level department discovery runs once for the catalogue root', async () => {
  let calls = 0;
  const content = loadContent({ facetControls: { async discover() {
    calls += 1;
    return [{ kind: 'department', label: 'Home', parentUrl: content.location.href }];
  } } });
  permitFacetFocus(content);
  content.location.href = 'https://www.walmart.com/reviews/claim-product';
  content.pager.innerText = '1 2 3 25';
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'departments', targetKey: 'root', targetUrl: content.location.href, discoveryOnly: true } });
  assert.equal(await content.api.discoverAuxiliaryTargets(90), true);
  assert.equal(await content.api.discoverAuxiliaryTargets(90), true);
  assert.equal(calls, 1);
  assert.ok(content.sentMessages.some(m => m.type === 'reviewer:aux-discovered' && m.candidates[0].label === 'Home'));
});

test('auxiliary discovery failure is reported to its target instead of crashing relay', async () => {
  const content = loadContent({ facetControls: { async discover() { throw new Error('renamed menu'); } } });
  permitFacetFocus(content);
  content.location.href = 'https://www.walmart.com/reviews/claim-product';
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'departments', targetKey: 'root', targetUrl: content.location.href, discoveryOnly: true } });
  assert.equal(await content.api.discoverAuxiliaryTargets(90), false);
  assert.ok(content.sentMessages.some(m => m.type === 'reviewer:unusable' && /renamed menu/.test(m.reason)));
});

test('auxiliary role switches cancel old work when a new target shares the generation', () => {
  const content = loadContent();
  const role = { active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'keywords', targetKey: 'Chair', targetUrl: content.location.href } };
  content.api.applyRole(role);
  content.api.queueWhenIdle(() => assert.fail('old target navigated'), 1000);
  content.api.applyRole({ ...role, auxiliary: { ...role.auxiliary, targetKey: 'Couch' } });
  assert.equal(content.api.getActionTimer(), null);
});

test('auxiliary delayed acknowledgements cannot revive a revoked generation', () => {
  const content = loadContent();
  content.api.applyRole({ active: false, tabId: 7, primaryTabId: 7, generation: 91,
    auxiliary: { lane: 'keywords', targetKey: 'Chair', targetUrl: content.location.href } });
  content.api.applyAckRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90 });
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90 });
  assert.equal(content.api.role.active, false);
  assert.equal(content.api.role.generation, 91);
});

test('a capped department scans its pages without discovering subdepartments, including durable pager totals', async () => {
  let discoveries = 0;
  const content = loadContent({ facetControls: { async discover() { discoveries++; return []; } } });
  permitFacetFocus(content);
  content.document.querySelector = () => null;
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'departments', targetKey: 'Home', targetUrl: content.location.href } });
  content.local.data['sweep:7'] = { generation: 90, mode: 'none', targetKey: 'Home', total: 25, visited: [1] };
  assert.equal(await content.api.discoverAuxiliaryTargets(90), true);
  assert.equal(discoveries, 0);
  assert.equal(content.local.data['sweep:7'].discoveryDone, undefined);
  assert.deepEqual([...content.local.data['sweep:7'].visited], [1]);
});

test('auxiliary fresh document can bind a pending filter before ingesting its results', async () => {
  const content = loadContent();
  const targetUrl = 'https://www.walmart.com/reviews/claim-product';
  content.location.href = `${targetUrl}?catId=4044`;
  content.api.setCollector(() => [{ item_id: 'new-document-result' }]);
  const auxiliary = { lane: 'departments', targetKey: 'Home', targetUrl,
    selection: { kind: 'department', label: 'Home', parentUrl: targetUrl } };
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90, auxiliary });
  content.chrome.runtime.sendMessage = async message => {
    content.sentMessages.push(message);
    return { ok: true, active: true, tabId: 7, primaryTabId: 7, generation: 90,
      auxiliary: { ...auxiliary, targetUrl: content.location.href, selection: null } };
  };
  assert.equal(await content.api.ensureAuxiliaryTarget(90), false);
  assert.equal(content.api.role.auxiliary.selection, null);
  assert.ok(content.sentMessages.some(m => m.type === 'reviewer:aux-selected'));
  assert.equal(content.sentMessages.some(m => m.type === 'reviewer:page'), false);
});

test('document-backed department discovery works in a hidden tab without dropdown or native input', async () => {
  const home = 'https://www.walmart.com/reviews/claim-product';
  const data = JSON.parse(fs.readFileSync(new URL('./fixtures/reviewer-departments.json', import.meta.url), 'utf8'));
  data.props.pageProps.initialSearchQueryVariables.page = 1;
  const content = loadContent({ initialUrl: home, departmentData: true,
    facetControls: { discover() { assert.fail('No dropdown discovery required'); } } });
  content.api.setNow(100_000);
  content.document.querySelector = () => ({ textContent: JSON.stringify(data) });
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'departments', targetKey: 'root', targetUrl: home, discoveryOnly: true } });
  assert.equal(await content.api.discoverAuxiliaryTargets(90), true);
  const candidates = content.sentMessages.find(m => m.type === 'reviewer:aux-discovered').candidates;
  assert.ok(candidates.length >= 30);
  assert.equal(candidates.find(c => c.label === 'Clothing, Shoes & Accessories').url, `${home}?catId=5438`);
  assert.equal(content.sentMessages.some(m => ['reviewer:focus', 'reviewer:facet-input'].includes(m.type)), false);
});

test('missing catalogue metadata fails only that discovery instead of silently completing an empty list', async () => {
  const content = loadContent({ departmentData: true });
  content.api.setNow(100_000);
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'departments', targetKey: 'root', targetUrl: content.location.href, discoveryOnly: true } });
  assert.equal(await content.api.discoverAuxiliaryTargets(90), false);
  assert.ok(content.sentMessages.some(m => m.type === 'reviewer:unusable' && /catalogue department data/.test(m.reason)));
  assert.equal(content.sentMessages.some(m => m.type === 'reviewer:aux-discovered'), false);
});

test('a blank catalogue gallery cannot block valid department discovery or claim capped product coverage', async () => {
  const home = 'https://www.walmart.com/reviews/claim-product';
  const data = JSON.parse(fs.readFileSync(new URL('./fixtures/reviewer-departments.json', import.meta.url), 'utf8'));
  data.props.pageProps.initialSearchQueryVariables.page = 1;
  const content = loadContent({ initialUrl: home, departmentData: true });
  content.api.setNow(100_000);
  content.body.innerText = '';
  content.api.setCollector(() => []);
  content.pager.innerText = '1 2 25';
  const query = content.document.querySelector;
  content.document.querySelector = selector => selector === '#__NEXT_DATA__'
    ? { textContent: JSON.stringify(data) } : query(selector);
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'departments', targetKey: 'root', targetUrl: home, discoveryOnly: true } });
  await content.api.ensureAuxiliaryTarget(90);
  assert.ok(content.sentMessages.some(m => m.type === 'reviewer:aux-discovered'));
  const done = content.sentMessages.find(m => m.type === 'reviewer:sweep-complete');
  assert.equal(done.pages, 0);
  assert.equal(done.coverageLimited, false);
  assert.equal(content.sentMessages.some(m => m.type === 'reviewer:page'), false);
});

test('a mapped department requires its foreground turn before full navigation without filter input', async () => {
  const home = 'https://www.walmart.com/reviews/claim-product';
  for (const background of [false, true]) {
    const content = loadContent({ initialUrl: home, departmentData: true,
      facetControls: { select() { assert.fail('No simulated filter click required'); } } });
    content.api.setNow(100_000);
    content.document.visibilityState = background ? 'hidden' : 'visible';
    const target = `${home}?catId=5438`;
    let destination;
    content.location.assign = url => { destination = url; };
    content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
      auxiliary: { lane: 'departments', targetKey: 'Clothing', targetUrl: home,
        selection: { kind: 'department', label: 'Clothing, Shoes & Accessories', parentUrl: home, url: target } } });
    const send = content.chrome.runtime.sendMessage;
    content.chrome.runtime.sendMessage = async message => {
      if (message.type !== 'reviewer:focus') return send(message);
      content.sentMessages.push(message);
      assert.equal(destination, undefined, 'focus is requested before category navigation');
      return background ? { enabled: true, granted: false, reason: 'window-unfocused' }
        : { enabled: true, granted: true, until: 110_000 };
    };
    await content.api.selectAuxiliaryTarget(90);
    assert.equal(destination, background ? undefined : target);
    if (background) {
      content.document.visibilityState = 'visible';
      content.chrome.runtime.sendMessage = async message => message.type === 'reviewer:focus'
        ? { enabled: true, granted: true, until: 110_000 } : send(message);
      await runNavigation(content);
      assert.equal(destination, target);
    }
    assert.equal(content.sentMessages.some(m => m.type === 'reviewer:facet-input'), false);
    assert.equal(content.sentMessages.some(m => m.type === 'reviewer:page'), false);
  }
});

for (const mode of ['correct', 'wrong-id', 'wrong-chip', 'missing-data']) {
test(`fresh mapped results require matching category identity and applied chip: ${mode}`, async () => {
  const home = 'https://www.walmart.com/reviews/claim-product';
  const target = `${home}?catId=5438`;
  const content = loadContent({ initialUrl: target, departmentData: true });
  content.api.setNow(100_000);
  content.api.setCollector(() => [{ item_id: 'fresh-result' }]);
  let category = mode === 'wrong-id' ? { catId: '4044', name: 'Home' } : { catId: '5438', name: 'Clothing' };
  if (mode === 'missing-data') category = null;
  const chip = mode === 'wrong-chip' ? 'Home' : category?.name || 'Clothing';
  const button = { getAttribute: key => key === 'aria-label' ? `Filter by ${chip} applied, activate to change , filter applied` : null };
  const root = { getAttribute: () => null, querySelectorAll: () => [button] };
  content.document.querySelectorAll = selector => selector.includes('Sort and Filter') ? [root] : [];
  content.document.querySelector = selector => selector === '#__NEXT_DATA__' ? { textContent: JSON.stringify({
    props: { pageProps: { initialData: { searchResult: { catInfo: category } } } }
  }) } : null;
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'departments', targetKey: 'Clothing', targetUrl: home,
      selection: { kind: 'department', label: 'Clothing, Shoes & Accessories', parentUrl: home, url: target } } });
  await content.api.ensureAuxiliaryTarget(90);
  assert.equal(content.sentMessages.some(m => m.type === 'reviewer:aux-selected'), mode === 'correct');
  if (mode !== 'correct') {
    content.api.setNow(131_000);
    await content.api.ensureAuxiliaryTarget(90);
    assert.ok(content.sentMessages.some(m => m.type === 'reviewer:unusable' && /could not be authorized/.test(m.reason)));
  }
  assert.equal(content.sentMessages.some(m => m.type === 'reviewer:page'), false);
});
}

test('auxiliary interrupted in-document selection never binds old cards on a later relay', async () => {
  const content = loadContent({ facetControls: { async select() {
    content.location.href += '&catId=4044';
    return { ok: true };
  } } });
  permitFacetFocus(content);
  content.api.setNow(100_000);
  content.api.setCollector(() => [{ item_id: 'old-parent-card' }]);
  const targetUrl = content.location.href;
  content.api.applyRole({ active: true, tabId: 7, primaryTabId: 7, generation: 90,
    auxiliary: { lane: 'departments', targetKey: 'Home', targetUrl,
      selection: { kind: 'department', label: 'Home', parentUrl: targetUrl } } });
  await content.api.selectAuxiliaryTarget(90);
  content.api.cancelPageWork();
  await content.api.ensureAuxiliaryTarget(90);
  assert.equal(content.sentMessages.some(m => m.type === 'reviewer:aux-selected'), false);
  assert.ok(content.sentMessages.some(m => m.type === 'reviewer:unusable'));
});

test('native facet clicks ignore only their expected label events and preserve real input cancellation', async () => {
  const home = 'https://www.walmart.com/reviews/claim-product';
  for (const interrupt of [false, true]) {
    const target = {}, other = {};
    let scrolls = 0;
    const label = { isConnected: true, contains: el => el === target,
      scrollIntoView() { scrolls++; } };
    const content = loadContent({ initialUrl: home, facetControls: {
      select: async ({ activate }) => activate(label)
    } });
    permitFacetFocus(content);
    content.api.setCollector(() => [{ item_id: 'parent' }]);
    const selection = { kind: 'department', label: 'Baby', parentUrl: home };
    content.api.applyRole({ active: true, tabId: 1, primaryTabId: 1, generation: 90,
      auxiliary: { lane: 'departments', targetKey: 'Baby', targetUrl: home, selection } });
    const send = content.chrome.runtime.sendMessage;
    content.chrome.runtime.sendMessage = async message => {
      if (message.type !== 'reviewer:facet-input') return send(message);
      content.sentMessages.push(message);
      for (const event of ['pointerdown', 'click']) content.listeners.get(event)({ isTrusted: true, button: 0, target });
      if (interrupt) {
        content.api.setNow(100_101);
        content.listeners.get('keydown')({ isTrusted: true, target });
        content.listeners.get('pointerdown')({ isTrusted: true, target: other });
        return { ok: false, reason: 'cancelled' };
      }
      content.location.href = `${home}?catId=5427`;
      content.api.setCollector(() => [{ item_id: 'baby-result' }]);
      return { ok: true };
    };
    const selecting = content.api.selectAuxiliaryTarget(90);
    await new Promise(resolve => setImmediate(resolve));
    const reveal = [...content.timerCallbacks.values()].find(timer => timer.ms === 100);
    assert.ok(reveal); reveal.callback();
    await selecting;
    assert.equal(scrolls, 1);
    assert.ok(content.sentMessages.some(m => m.type === 'reviewer:facet-input'));
    assert.equal(content.sentMessages.some(m => m.type === 'reviewer:user-active'), interrupt);
    assert.equal(content.sentMessages.some(m => m.type === 'reviewer:aux-selected'), !interrupt);
    if (interrupt) assert.equal(content.api.getPhase(), 'waiting-user');
  }
});

test('native facet release acknowledgement can arrive before its click without cancelling selection', async () => {
  const home = 'https://www.walmart.com/reviews/claim-product';
  const target = {};
  const label = { isConnected: true, contains: node => node === target, scrollIntoView() {} };
  const content = loadContent({ initialUrl: home, facetControls: { select: async ({ activate }) => activate(label) } });
  permitFacetFocus(content);
  let cards = [{ item_id: 'parent' }];
  content.api.setCollector(() => cards);
  const selection = { kind: 'department', label: 'Pets', parentUrl: home };
  content.api.applyRole({ active: true, tabId: 1, primaryTabId: 1, generation: 90,
    auxiliary: { lane: 'departments', targetKey: 'Pets', targetUrl: home, selection } });
  const send = content.chrome.runtime.sendMessage;
  content.chrome.runtime.sendMessage = async message => {
    if (message.type !== 'reviewer:facet-input') return send(message);
    content.sentMessages.push(message);
    content.listeners.get('pointerdown')({ isTrusted: true, button: 0, target });
    return { ok: true }; // Chrome can acknowledge input before renderer delivery.
  };
  const selecting = content.api.selectAuxiliaryTarget(90);
  await new Promise(resolve => setImmediate(resolve));
  [...content.timerCallbacks.values()].find(timer => timer.ms === 100).callback();
  await selecting;
  content.api.setNow(100_100);
  content.location.href = `${home}?catId=5440`;
  cards = [{ item_id: 'fresh-pets' }];
  content.listeners.get('click')({ isTrusted: true, button: 0, target });
  assert.equal(content.sentMessages.some(m => m.type === 'reviewer:user-active'), false);
  await content.timerCallbacks.get(content.api.getActionTimer()).callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(content.sentMessages.some(m => m.type === 'reviewer:aux-selected'));
});

for (const variant of ['paired', 'different-pointer', 'moved-point', 'another-press']) {
test(`a closing facet popover accepts only its paired click: ${variant}`, async () => {
  const home = 'https://www.walmart.com/reviews/claim-product';
  const target = {}, ancestor = {};
  const label = { isConnected: true, contains: node => node === target, scrollIntoView() {} };
  const content = loadContent({ initialUrl: home, facetControls: { select: async ({ activate }) => activate(label) } });
  permitFacetFocus(content);
  let cards = [{ item_id: 'parent' }];
  content.api.setCollector(() => cards);
  const selection = { kind: 'department', label: 'Seasonal', parentUrl: home };
  content.api.applyRole({ active: true, tabId: 1, primaryTabId: 1, generation: 90,
    auxiliary: { lane: 'departments', targetKey: 'Seasonal', targetUrl: home, selection } });
  const send = content.chrome.runtime.sendMessage;
  const mouse = { isTrusted: true, button: 0, pointerId: 1, clientX: 120, clientY: 180 };
  content.chrome.runtime.sendMessage = async message => {
    if (message.type !== 'reviewer:facet-input') return send(message);
    content.listeners.get('pointerdown')({ ...mouse, target });
    return { ok: true };
  };
  const selecting = content.api.selectAuxiliaryTarget(90);
  await new Promise(resolve => setImmediate(resolve));
  [...content.timerCallbacks.values()].find(timer => timer.ms === 100).callback();
  await selecting;
  content.api.setNow(100_100);
  content.location.href = `${home}?catId=1085632`;
  cards = [{ item_id: 'fresh-seasonal' }];
  label.isConnected = false; // React removed the pressed popover before click.
  if (variant === 'another-press') content.listeners.get('pointerdown')({ ...mouse, target: ancestor });
  content.listeners.get('click')({ ...mouse, target: ancestor,
    ...(variant === 'different-pointer' ? { pointerId: 2 } : {}),
    ...(variant === 'moved-point' ? { clientX: 140 } : {}) });
  const paired = variant === 'paired';
  assert.equal(content.sentMessages.some(m => m.type === 'reviewer:user-active'), !paired);
  await content.timerCallbacks.get(content.api.getActionTimer()).callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(content.sentMessages.some(m => m.type === 'reviewer:aux-selected'), paired);
  content.api.setNow(100_200);
  content.listeners.get('pointerdown')({ ...mouse, target: ancestor });
  assert.ok(content.sentMessages.some(m => m.type === 'reviewer:user-active'));
});
}

test('adjacent numeric pager controls cannot concatenate into fictitious page totals', () => {
  const content = loadContent();
  content.pager.innerText = '12';
  content.pager.querySelectorAll = () => [1, 2].map(number => ({ tagName: 'BUTTON', children: [],
    innerText: String(number), getAttribute: name => name === 'aria-label' ? `Go to Page ${number}` : null }));
  assert.equal(content.api.totalPages(), 2);
});

test('one-page auxiliary hints require matching fresh document scope and metadata', () => {
  const initialUrl = 'https://www.walmart.com/reviews/claim-product?q=Furniture';
  const content = loadContent({ initialUrl });
  content.api.role.auxiliary = { lane: 'keywords', targetKey: 'Furniture', targetUrl: initialUrl };
  const data = { props: { pageProps: { initialSearchQueryVariables: {
    query: 'Furniture', page: 1, catId: '', facet: '' }, initialData: {
    searchResult: { paginationV2: { maxPage: 1 }, hasMorePages: false } } } } };
  content.document.querySelector = selector => selector === '#__NEXT_DATA__' ? { textContent: JSON.stringify(data) } : null;
  assert.equal(content.api.totalPages(), 1);
  content.location.href = `${initialUrl}&page=2`;
  content.location.search = '?q=Furniture&page=2';
  assert.equal(content.api.totalPages(), null, 'SPA navigation cannot reuse initial metadata');
  content.location.href = initialUrl;
  content.location.search = '?q=Furniture';
  assert.equal(content.api.totalPages(), null, 'Returning by SPA cannot revive initial metadata');
  const fresh = loadContent({ initialUrl });
  fresh.api.role.auxiliary = content.api.role.auxiliary;
  fresh.document.querySelector = content.document.querySelector;
  data.props.pageProps.initialSearchQueryVariables.query = 'Chair';
  assert.equal(fresh.api.totalPages(), null, 'Mismatched query metadata cannot finish a search');
});

function loadContent({ pageAck = { ok: false, retryable: true }, localArea = null,
  runtimeId = 'reviewer-fixture-extension', facetControls = null,
  pageScroll = { run: async () => ({ outcome: 'complete' }) },
  departmentData = false,
  initialUrl = 'https://www.walmart.com/reviews/claim-product?page=2' } = {}) {
  const local = localArea || storageArea();
  const sentMessages = [];
  const timerCallbacks = new Map();
  const intervals = new Map();
  let timerId = 0;
  const fakeSetTimeout = (callback, ms = 0) => {
    const id = ++timerId;
    timerCallbacks.set(id, { callback, ms });
    return id;
  };
  const fakeClearTimeout = (id) => timerCallbacks.delete(id);
  const body = { innerText: 'No search results', scrollHeight: 100 };
  const mutationObservers = [];
  const pager = { innerText: '1 2 3 24' };
  const listeners = new Map();
  const viewport = { scrollY: 0, innerHeight: 800, innerWidth: 1000, scrollTo() {},
    matchMedia() { return { matches: false }; } };
  const document = {
    body,
    visibilityState: 'hidden',
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener(type, listener) { if (listeners.get(type) === listener) listeners.delete(type); },
    querySelectorAll() { return []; },
    querySelector(selector) {
      return selector.includes('page-number') ? { closest: () => pager } : null;
    }
  };
  class MutationObserver {
    constructor(callback) { this.callback = callback; mutationObservers.push(this); }
    observe(target, options) {
      assert.ok(target, 'MutationObserver requires a live document node');
      this.target = target;
      this.options = options;
    }
    disconnect() { this.disconnected = true; }
  }
  const location = {
    href: initialUrl,
    pathname: new URL(initialUrl).pathname,
    search: new URL(initialUrl).search,
    origin: new URL(initialUrl).origin,
    assign() {},
    reload() {}
  };
  const chrome = {
    storage: { local, onChanged: event() },
    runtime: {
      id: runtimeId,
      onMessage: event(),
      async sendMessage(message) {
        sentMessages.push(message);
        if (message.type === 'reviewer:role') {
          // The test assigns a role explicitly after evaluation.
          return new Promise(() => {});
        }
        if (message.type === 'reviewer:page') return pageAck;
        // Legacy unit scenarios explicitly opt out of automatic tab rotation.
        // Dedicated reading/focus cases below supply real grants and denials.
        if (message.type === 'reviewer:focus') return { ok: true, enabled: false, reason: 'disabled' };
        return { ok: true };
      }
    }
  };
  const context = vm.createContext({
    chrome,
    document,
    window: viewport,
    location,
    URL,
    URLSearchParams,
    AbortController,
    ReviewerPageScroll: pageScroll || undefined,
    ReviewerFacetControls: facetControls,
    getComputedStyle() { return { display: 'block', visibility: 'visible' }; },
    MutationObserver,
    setTimeout: fakeSetTimeout,
    clearTimeout: fakeClearTimeout,
    setInterval(callback, ms) { const id = ++timerId; intervals.set(id, { callback, ms }); return id; },
    clearInterval(id) { intervals.delete(id); },
    console
  });
  vm.runInContext(
    `${sweepModesSource}\n${policySource}\n${controlsSource}\n${liveObserverSource}\n${departmentData ? departmentDataSource : ''}\n${contentSource}\n;globalThis.__test = {role, nextUnvisited, handleEndPage, ` +
      'queueWhenIdle, relay, scheduleRelay, pageDelayMs, preparePageNavigation, advanceDeliveredPage, readSweep, findPageControl, inspectPageControls, navigatePage, collect, ' +
      'navigateWithFocus: (...args) => navigateWithFocus(...args), ' +
      'diagnostic, checkVerification, requestRole, revealPager, applyRole, keepMonitoringScope, ' +
      'ensureAuxiliaryTarget, discoverAuxiliaryTargets, removeCurrentSweep, applyAckRole, selectAuxiliaryTarget, cancelPageWork, totalPages, ' +
      'scrollResultsPage: (...args) => scrollResultsPage(...args), cancelResultsScroll: () => cancelResultsScroll(), ' +
      'scrollPageControlIntoView: (...args) => scrollPageControlIntoView(...args), ' +
      'observeVisibleUpdate: (...args) => observeVisibleUpdate(...args), ' +
      'rememberAcknowledged: (...args) => rememberAcknowledged(...args), ' +
      'scheduleVisibleObservation: (...args) => scheduleVisibleObservation(...args), ' +
      'setCollector(value) { collect = value; }, ' +
      'setLastInteraction(value) { lastInteraction = value; }, ' +
      'setRoleRequestInFlight(value) { roleRequestInFlight = value; }, ' +
      'setNow(value) { Date.now = () => value; }, ' +
      'setRandom(value) { Math.random = () => value; }, ' +
      'setNavigationAt(value) { lastNavigationAt = value; }, ' +
      'setPhase(value) { phase = value; }, getPhase() { return phase; }, ' +
      'getActionTimer() { return actionTimer; }, getNow() { return Date.now(); }};',
    context
  );
  return { api: context.__test, local, timerCallbacks, location, pager, document, chrome, body, sentMessages,
    mutationObservers, viewport, listeners, intervals };
}

function permitFacetFocus(content) {
  content.api.setNow(100_000);
  content.document.visibilityState = 'visible';
  const send = content.chrome.runtime.sendMessage;
  content.chrome.runtime.sendMessage = async message => {
    if (message.type === 'reviewer:focus' && message.purpose === 'facet-controls') {
      content.sentMessages.push(message);
      return { ok: true, enabled: true, granted: true, until: 110_000 };
    }
    return send(message);
  };
}

test('a synchronous invalidated-context error stops stale timers, observers and navigation', async () => {
  const content = loadContent();
  await contentMessage(content, { type: 'reviewer:set-role', active: true,
    tabId: 1, primaryTabId: 1, generation: 9 });
  const progress = { generation: 9, mode: 'none', total: 24, visited: [1] };
  content.local.data['sweep:1'] = progress;
  let navigated = false;
  content.location.assign = () => { navigated = true; };
  content.api.queueWhenIdle(() => { navigated = true; }, 1000);
  const pending = content.timerCallbacks.get(content.api.getActionTimer()).callback;
  content.chrome.runtime.sendMessage = () => { throw new Error('Extension context invalidated.'); };

  assert.doesNotThrow(() => content.api.diagnostic('page-loaded'));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(content.api.getPhase(), 'extension-reloaded');
  assert.equal(content.api.role.active, false);
  assert.equal(content.timerCallbacks.size, 0);
  assert.equal(content.intervals.size, 0);
  assert.equal(content.listeners.size, 0);
  assert.ok(content.mutationObservers.every(observer => observer.disconnected));
  pending();
  for (const observer of content.mutationObservers) observer.callback();
  assert.equal(navigated, false);
  assert.equal(content.timerCallbacks.size, 0);
  assert.deepEqual(content.local.data['sweep:1'], progress);
});

test('a missing runtime id cancels a queued page action before it can navigate', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  let navigated = false;
  content.api.queueWhenIdle(() => { navigated = true; }, 1000);
  content.chrome.runtime.id = undefined;
  await runNavigation(content);
  assert.equal(navigated, false);
  assert.equal(content.api.getPhase(), 'extension-reloaded');
  assert.equal(content.intervals.size, 0);
});

test('a rejected invalidated verification report tears down the old content script', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.body.innerText = 'Robot or human? Press & Hold';
  content.chrome.runtime.sendMessage = async () => { throw new Error('Extension context invalidated.'); };
  content.api.checkVerification();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(content.api.getPhase(), 'extension-reloaded');
  assert.equal(content.api.role.active, false);
  assert.equal(content.timerCallbacks.size, 0);
});

test('invalidation during storage or an in-flight acknowledgement cannot advance progress', async () => {
  const storage = loadContent();
  Object.assign(storage.api.role, { active: true, tabId: 1, generation: 9 });
  storage.local.get = () => { throw new Error('Extension context invalidated.'); };
  await assert.doesNotReject(storage.api.relay());
  assert.equal(storage.api.getPhase(), 'extension-reloaded');

  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 9 });
  content.api.setCollector(() => [{ item_id: 'unacknowledged' }]);
  const progress = { generation: 9, mode: 'none', total: 24, visited: [1] };
  content.local.data['sweep:1'] = progress;
  let acknowledge;
  let entered;
  const started = new Promise(resolve => { entered = resolve; });
  content.chrome.runtime.sendMessage = message => {
    if (message.type !== 'reviewer:page') return Promise.resolve({ ok: true });
    entered();
    return new Promise(resolve => { acknowledge = resolve; });
  };
  const delivery = content.api.relay();
  await started;
  content.chrome.runtime.id = undefined;
  acknowledge({ ok: true });
  await delivery;
  assert.equal(content.api.getPhase(), 'extension-reloaded');
  assert.deepEqual(content.local.data['sweep:1'], progress);
  assert.equal(content.timerCallbacks.size, 0);
});

test('a temporarily missing worker still retries without invalidating a healthy context', async () => {
  const content = loadContent();
  content.api.setRoleRequestInFlight(false);
  content.chrome.runtime.sendMessage = () => { throw new Error('Receiving end does not exist.'); };
  await content.api.requestRole();
  assert.notEqual(content.api.getPhase(), 'extension-reloaded');
  assert.equal(content.intervals.size, 1);
  assert.ok([...content.timerCallbacks.values()].some(timer => timer.ms === 2000));
});

test('a document with no extension context never starts observers or polling', () => {
  const content = loadContent({ runtimeId: null });
  assert.equal(content.api.getPhase(), 'extension-reloaded');
  assert.equal(content.sentMessages.length, 0);
  assert.equal(content.timerCallbacks.size, 0);
  assert.equal(content.intervals.size, 0);
  assert.equal(content.listeners.size, 0);
  assert.equal(content.chrome.runtime.onMessage.listeners.length, 0);
});

test('invalidation cancels a pending pager reveal without leaving a timer or scrolling again', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  let scrolls = 0;
  content.viewport.scrollTo = () => { scrolls++; };
  const revealing = content.api.revealPager();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(scrolls, 1);
  content.chrome.runtime.id = undefined;
  content.api.diagnostic('page-loaded');
  assert.equal(content.timerCallbacks.size, 0);
  await revealing;
  assert.equal(scrolls, 1);
});

test('ordinary storage failures remain visible instead of being treated as context invalidation', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.local.get = async () => { throw new Error('Temporary storage failure'); };
  await assert.rejects(content.api.relay(), /Temporary storage failure/);
  assert.equal(content.api.role.active, true);
  assert.notEqual(content.api.getPhase(), 'extension-reloaded');
});

test('invalidation detected by navigation diagnostics prevents both a page load and a native click', () => {
  for (const native of [false, true]) {
    const content = loadContent();
    Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
    let navigations = 0;
    if (native) addNativePageControl(content, () => { navigations++; });
    content.location.assign = () => { navigations++; };
    content.chrome.runtime.sendMessage = () => { throw new Error('Extension context invalidated.'); };
    content.api.navigatePage(2, 9);
    assert.equal(navigations, 0, native ? 'native' : 'full load');
    assert.equal(content.timerCallbacks.size, 0);
    assert.equal(content.api.getPhase(), 'extension-reloaded');
  }
});

function activateAlphabet(content, query = 'a', page = 1) {
  Object.assign(content.api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 9 });
  content.local.data.sweepMode = true;
  content.location.search = `?q=${query}&queryNups=&page=${page}`;
  content.location.href = `https://www.walmart.com/reviews/claim-product${content.location.search}`;
  content.pager.innerText = '1 2 3';
}

async function runNavigation(content) {
  const id = content.api.getActionTimer();
  const timer = content.timerCallbacks.get(id);
  assert.ok(timer, 'Expected a pending navigation action');
  content.timerCallbacks.delete(id);
  await timer.callback();
}

async function exhaustNoopRetry(content) {
  const timer = content.timerCallbacks.get(content.api.getActionTimer());
  assert.ok(timer, 'The first no-op must schedule its paced retry');
  content.api.setNow(content.api.getNow() + timer.ms + 1);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  content.api.setNow(content.api.getNow() + 6_000);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
}

test('a no-op pager retries once after normal spacing before using a full load', async () => {
  const content = loadContent();
  let clicks = 0, loads = 0;
  content.api.setNow(100_000);
  addNativePageControl(content, () => { clicks++; });
  content.location.assign = () => { loads++; };
  content.api.navigatePage(2, 9);
  content.api.setNow(105_500);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(loads, 0);
  const retry = content.timerCallbacks.get(content.api.getActionTimer());
  assert.ok(retry.ms >= 9_500, 'Retry must retain the15-second navigation floor');
  content.api.setNow(120_000);
  await runNavigation(content);
  assert.equal(clicks, 2);
  content.api.setNow(126_000);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(loads, 1, 'The second no-op must retain finite recovery');
});

test('a late first response during retry focus keeps stale cards locked until fresh results', async () => {
  const content = loadContent();
  content.api.setNow(100_000);
  content.body.innerText = '';
  let cards = [{ item_id: 'old' }];
  content.api.setCollector(() => cards);
  let clicks = 0, grant;
  addNativePageControl(content, () => { clicks++; });
  content.api.navigatePage(2, 9);
  content.api.setNow(106_000); await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  const send = content.chrome.runtime.sendMessage;
  content.chrome.runtime.sendMessage = message => message.type === 'reviewer:focus'
    ? new Promise(resolve => { grant = resolve; }) : send(message);
  content.api.setNow(120_000);
  const pending = runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  content.location.href = 'https://www.walmart.com/reviews/claim-product?page=2';
  content.location.search = '?page=2';
  grant({ ok: true, enabled: false }); await pending;
  assert.equal(clicks, 1);
  assert.equal(content.api.getPhase(), 'waiting-page-change');
  assert.ok(content.api.getActionTimer());
  cards = [{ item_id: 'fresh' }]; await runNavigation(content);
  assert.equal(content.api.getPhase(), 'waiting-content');
});

test('a late native completion during exhausted fallback persistence avoids a full load', async () => {
  const content = loadContent();
  content.api.setNow(100_000);
  content.body.innerText = '';
  let cards = [{ item_id: 'old' }], loads = 0;
  content.api.setCollector(() => cards);
  addNativePageControl(content, () => {});
  content.location.assign = () => { loads++; };
  content.api.navigatePage(2, 9);
  content.api.setNow(106_000); await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  content.api.setNow(120_000); await runNavigation(content);
  let finish;
  const set = content.local.set;
  content.local.set = values => values.nativeNavigationFailures
    ? new Promise(resolve => { finish = () => set(values).then(resolve); }) : set(values);
  content.api.setNow(126_000); await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  content.location.href = 'https://www.walmart.com/reviews/claim-product?page=2';
  content.location.search = '?page=2'; cards = [{ item_id: 'fresh' }];
  finish(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(loads, 0);
  assert.equal(content.api.getPhase(), 'waiting-content');
});

test('a cooled-down pager waits for one probe instead of amplifying full loads', async () => {
  const content = loadContent();
  let clicks = 0, loads = 0;
  content.api.setNow(100_000);
  addNativePageControl(content, () => { clicks++; });
  // Obtain the real key through one exhausted navigation rather than duplicating
  // the control-key implementation in this regression.
  content.api.navigatePage(2, 9);
  content.api.setNow(106_000); await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  content.api.setNow(121_000); await runNavigation(content);
  content.api.setNow(127_000); await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  const next = loadContent({ localArea: content.local });
  next.api.setNow(130_000);
  addNativePageControl(next, () => { clicks++; });
  next.location.assign = () => { loads++; };
  await next.api.pageDelayMs();
  next.api.navigatePage(2, 9);
  assert.equal(loads, 0);
  assert.ok(next.timerCallbacks.get(next.api.getActionTimer()).ms <= 60_000);
  next.api.setNow(191_000); await runNavigation(next);
  assert.equal(clicks, 3);
});

function loadFinalLetterContent(options = {}) {
  const content = loadContent(options);
  activateAlphabet(content, 'z', 2);
  content.pager.innerText = '1 2 3 24';
  content.local.data['sweep:1'] = { generation: 9, mode: 'sweep', query: 'z', visited: [1] };
  return content;
}

async function fallbackRound() {
  const tab = { ...REVIEW_TAB, id: 1,
    url: 'https://www.walmart.com/reviews/claim-product?page=2' };
  const probes = new Map([[1, { ready: true, active: true }]]);
  const bg = loadBackground({ tabs: [tab], probes, summary: { seen: 1 } });
  await bg.api.initialization;
  await bg.local.set({ sweepMode: 'none', rotateScannerTabs: false });
  const selected = await bg.api.selectPrimary();
  const generation = selected.state.generation;
  probes.set(1, { ready: true, active: true, generation });
  const content = loadContent({ localArea: bg.local });
  Object.assign(content.api.role, { active: true, tabId: 1, primaryTabId: 1, generation });
  content.chrome.runtime.sendMessage = message => {
    content.sentMessages.push(message);
    return workerMessage(bg, message, { tab: { ...tab } });
  };
  content.location.assign = url => {
    tab.url = url;
    content.location.href = url;
    content.location.search = new URL(url).search;
  };
  return { bg, content, tab, probes, generation };
}

async function waitForRegularRender(content) {
  content.api.setNow(1_000_000);
  await content.api.relay();
  content.api.setNow(1_031_000);
  await content.api.relay();
}

async function startConfirmedFallback({ bg, content, generation }) {
  bg.local.data['sweep:1'] = { generation, mode: 'none', total: 24,
    visited: [1], endRetries: { 2: 2 } };
  await waitForRegularRender(content);
}

test('regular pagination ignores a transient no-results screen on page six', async () => {
  const { bg, content, tab, generation } = await fallbackRound();
  tab.url = 'https://www.walmart.com/reviews/claim-product?page=6';
  content.location.href = tab.url;
  content.location.search = '?page=6';
  content.pager.innerText = '1 2 3 18';
  bg.local.data['sweep:1'] = { generation, mode: 'none', total: 18,
    visited: [1, 2, 3, 4, 5], endRetries: {} };

  content.api.setNow(1_000_000);
  bg.api.setNow(1_000_000);
  await content.api.relay();
  assert.equal(bg.local.data['sweep:1'].mode, 'none');
  assert.equal(bg.local.data['sweep:1'].endRetries[6], undefined);
  assert.equal(content.sentMessages.some(msg => msg.type === 'reviewer:pagination-fallback'), false);
  content.api.setNow(1_002_000);
  bg.api.setNow(1_002_000);
  content.body.innerText = 'Free (Valued at $25)';
  content.api.setCollector(() => [{ item_id: 'recovered-page-six' }]);
  await content.api.relay();
  assert.deepEqual([...bg.local.data['sweep:1'].visited], [1, 2, 3, 4, 5, 6]);
  assert.equal(bg.local.data['sweep:1'].mode, 'none');
  assert.equal(bg.local.data['sweep:1'].endRetries[6], undefined);
  assert.equal(content.sentMessages.some(msg => msg.type === 'reviewer:pagination-fallback'), false);
});

test('a recovered regular page cancels its queued empty-results reload', async () => {
  const { bg, content, generation } = await fallbackRound();
  bg.local.data['sweep:1'] = { generation, mode: 'none', total: 24,
    visited: [1], endRetries: { 2: 1 } };
  let reloads = 0;
  content.location.reload = () => { reloads += 1; };
  await waitForRegularRender(content);
  assert.equal(content.api.getPhase(), 'transient-end-retry');
  content.body.innerText = 'Free (Valued at $25)';
  content.api.setCollector(() => [{ item_id: 'returned-before-reload' }]);
  await runNavigation(content);
  assert.equal(reloads, 0);
  assert.equal(content.sentMessages.some(msg => msg.type === 'reviewer:pagination-fallback'), false);
});

test('a confirmed empty regular interior page records why it starts the category fallback', async () => {
  const { bg, content, generation } = await fallbackRound();
  bg.local.data['sweep:1'] = { generation, mode: 'none', total: 24,
    visited: [1], endRetries: { 2: 2 } };
  await waitForRegularRender(content);
  assert.equal(bg.local.data['sweep:1'].mode, 'high-value');
  const report = (await workerMessage(bg, { type: 'reviewer:diagnostics' })).report;
  const fallback = report.events.find(entry => entry.event === 'sweep-start' &&
    entry.mode === 'high-value');
  assert.equal(fallback.reason, 'empty-results');
  assert.equal(fallback.page, 2);
  assert.equal(fallback.totalPages, 24);
  assert.equal(fallback.attempt, 3);
});

test('a confirmed empty regular catalogue tail finishes without a category fallback', async () => {
  for (const total of [null, 18, 19]) {
    const { bg, content, tab, generation } = await fallbackRound();
    tab.url = 'https://www.walmart.com/reviews/claim-product?page=19';
    content.location.href = tab.url;
    content.location.search = '?page=19';
    content.pager.innerText = total === null ? '' : `1 2 ${total}`;
    bg.local.data['sweep:1'] = { generation, mode: 'none', total,
      visited: Array.from({ length: 18 }, (_, index) => index + 1), endRetries: { 19: 2 } };
    await content.api.relay();
    assert.equal(content.api.getPhase(), 'complete');
    assert.equal(bg.local.data.lastSweepPages, 18);
    assert.equal(content.sentMessages.some(msg => msg.type === 'reviewer:pagination-fallback'), false);
  }
});

test('a confirmed unusable regular page scans each category once then retries pagination next round', async () => {
  const { bg, content, tab, probes, generation } = await fallbackRound();
  content.document.visibilityState = 'visible';
  bg.local.data['sweep:1'] = { generation, mode: 'none', total: 24,
    visited: [1], endRetries: { 2: 2 }, startedAt: 123 };
  await waitForRegularRender(content);
  assert.equal(bg.local.data['sweep:1'].mode, 'high-value');
  assert.equal(bg.local.data['sweep:1'].query, 'Accessories');
  assert.equal(bg.local.data.sweepMode, 'none');
  assert.equal(content.sentMessages.some(msg => msg.type === 'reviewer:unusable'), false);
  // Focus expiry is compared across worker and document, as in one browser.
  bg.api.setNow(1_031_000);
  await runNavigation(content);
  assert.equal(new URL(tab.url).searchParams.get('q'), 'Accessories');
  assert.equal(new URL(tab.url).searchParams.get('page'), null);
  assert.equal(await bg.api.ensureReviewerTab(), false);
  assert.equal((await bg.api.selectPrimary()).state.generation, generation);

  // Recreate the worker and document while the temporary round is in progress.
  const resumedBg = loadBackground({ tabs: [tab], probes, localArea: bg.local,
    sessionArea: bg.session, summary: { seen: 1 } });
  await resumedBg.api.initialization;
  const resumed = loadContent({ localArea: bg.local });
  resumed.document.visibilityState = 'visible';
  Object.assign(resumed.api.role, content.api.role);
  resumed.location.href = tab.url;
  resumed.location.search = new URL(tab.url).search;
  resumed.location.assign = content.location.assign;
  resumed.chrome.runtime.sendMessage = message => workerMessage(resumedBg, message, { tab: { ...tab } });
  resumed.pager.innerText = '1';
  resumed.api.setCollector(() => [{ item_id: 'fallback-item' }]);
  const categories = [...vm.runInNewContext(`${sweepModesSource}; sweepQueries('high-value')`)];
  assert.equal(new Set(categories.map(query => query.toLowerCase())).size, categories.length);
  for (const requested of ['Furniture', 'Clothes', 'Chairs', 'Kitchen', 'Pet', 'Cat', 'Dog']) {
    assert.ok(categories.includes(requested));
  }
  const searched = [];
  for (const category of categories) {
    resumed.api.cancelPageWork(); // The next search is a new browser document.
    resumed.location.href = tab.url;
    resumed.location.search = new URL(tab.url).search;
    assert.equal((await resumed.api.readSweep(generation)).query, category);
    searched.push(category);
    await resumed.api.relay();
    if (category !== categories.at(-1)) await runNavigation(resumed);
  }
  assert.deepEqual(searched, categories);
  assert.equal(resumed.api.getPhase(), 'complete');
  assert.equal(bg.local.data['sweep:1'], undefined);
  assert.equal(bg.local.data.lastSweepPages, categories.length + 1);
  assert.equal((await resumedBg.api.selectPrimary()).state.tabId, 1);
  assert.equal(await resumedBg.api.ensureReviewerTab(), false);
  assert.equal(bg.local.data.sweepMode, 'none');

  const restarted = await resumedBg.api.restartPrimary('scheduled');
  assert.equal(restarted.ok, true);
  const target = new URL(resumedBg.tabActions.at(-1).options.url);
  assert.equal(target.searchParams.get('q'), null);
  assert.equal(target.searchParams.get('queryNups'), null);
  assert.equal(target.searchParams.get('page'), null);
  Object.assign(resumed.api.role, restarted);
  const next = await resumed.api.readSweep(restarted.generation);
  assert.equal(next.mode, 'none');
  assert.equal(next.query, '');
});

test('first-page, last-page and unknown-length no-results never immediately activate fallback', async () => {
  for (const [page, total] of [[1, null], [2, 2], [2, null]]) {
    const { bg, content, tab, generation } = await fallbackRound();
    tab.url = `https://www.walmart.com/reviews/claim-product?page=${page}`;
    content.location.href = tab.url;
    content.location.search = new URL(tab.url).search;
    content.pager.innerText = total === null ? '' : '1 2';
    bg.local.data['sweep:1'] = { generation, mode: 'none', total, visited: [], endRetries: {} };
    await content.api.relay();
    assert.equal(bg.local.data['sweep:1'].mode, 'none');
    assert.equal(content.sentMessages.some(msg => msg.type === 'reviewer:pagination-fallback'), false);
    assert.equal(bg.local.data.lastSweepDone, undefined);
  }
});

test('blank-card timeout uses the category fallback and verification cancels its navigation', async () => {
  const { bg, content, tab, generation } = await fallbackRound();
  content.body.innerText = 'Loading items';
  content.api.setNow(1_000_000);
  await content.api.relay();
  content.api.setNow(1_031_000);
  await content.api.relay();
  assert.equal(bg.local.data['sweep:1'].mode, 'high-value');
  assert.equal(bg.local.data.sweepMode, 'none');
  const request = content.sentMessages.find(message => message.type === 'reviewer:pagination-fallback');
  assert.equal(request.reason, 'blank-timeout');
  assert.equal(request.elapsedMs, 31_000);
  content.body.innerText = 'Robot or human? Press & Hold';
  await runNavigation(content);
  assert.equal(content.api.getPhase(), 'waiting-verification');
  assert.equal(new URL(tab.url).searchParams.get('q'), null);
  assert.equal(bg.local.data['sweep:1'].query, 'Accessories');
});

test('pagination fallback rejects stale leases, passive tabs, filtered pages, pauses and explicit search modes', async () => {
  for (const invalidation of ['stale', 'passive', 'filtered', 'paused', 'sweep', 'high-value']) {
    const { bg, tab, generation } = await fallbackRound();
    const progress = { generation, mode: 'none', total: 24, visited: [1] };
    bg.local.data['sweep:1'] = progress;
    if (invalidation === 'filtered') tab.url += '&q=manual';
    if (invalidation === 'paused') await bg.local.set({ botCheck: { pausedUntil: Date.now() + 60_000 } });
    if (['sweep', 'high-value'].includes(invalidation)) await bg.local.set({ sweepMode: invalidation });
    const response = await workerMessage(bg, { type: 'reviewer:pagination-fallback',
      generation: invalidation === 'stale' ? generation - 1 : generation },
    { tab: { ...tab, id: invalidation === 'passive' ? 2 : 1 } });
    assert.equal(response.ok, false, invalidation);
    assert.deepEqual(bg.local.data['sweep:1'], progress, invalidation);
  }
});

test('fallback authorization is idempotent and releases manual searches and pending filters', async () => {
  for (const navigation of ['q=manual', 'q=Accessories&sort=price', 'q=Accessories&queryNups=123', 'pending']) {
    const { bg, content, tab, generation } = await fallbackRound();
    await startConfirmedFallback({ bg, content, generation });
    await runNavigation(content);
    const progress = { ...bg.local.data['sweep:1'], query: 'Appliance', visited: [1] };
    bg.local.data['sweep:1'] = progress;
    const again = await workerMessage(bg, { type: 'reviewer:pagination-fallback', generation }, { tab });
    assert.equal(again.ok, true);
    assert.deepEqual(bg.local.data['sweep:1'], progress);
    if (navigation === 'pending') tab.pendingUrl = `${tab.url}&sort=price`;
    else tab.url = `https://www.walmart.com/reviews/claim-product?${navigation}`;
    const report = await bg.api.handlePageReport({ generation, page: 1,
      items: [{ item_id: 'manual-filter' }] }, { tab });
    assert.equal(report.ok, false, navigation);
    assert.equal(report.active, false, navigation);
    assert.equal(bg.fetchCalls.some(call => new URL(call.url).pathname === '/ingest'), false);
  }
});

test('failed category recovery retains the temporary mode and its completed queries', async () => {
  const { bg, content, tab, generation } = await fallbackRound();
  await startConfirmedFallback({ bg, content, generation });
  await runNavigation(content);
  const progress = { ...bg.local.data['sweep:1'], query: 'Appliance', total: 24, visited: [1],
    endRetries: { 2: 2 }, completedPages: 3, completedLetters: 0, completedQueries: 1 };
  bg.local.data['sweep:1'] = progress;
  const result = await workerMessage(bg, { type: 'reviewer:unusable', generation,
    restartSweep: true }, { tab });
  assert.equal(result.recover, 'restart');
  const recovered = await content.api.readSweep(generation);
  assert.equal(recovered.mode, 'high-value');
  assert.equal(recovered.paginationFallback, true);
  assert.equal(recovered.query, 'Appliance');
  assert.equal(recovered.completedQueries, 1);
  assert.equal(recovered.total, null);
  assert.equal(recovered.visited.length, 0);
  assert.equal(bg.local.data.sweepMode, 'none');
});

test('Chrome URL and loading events preserve the fallback lease across search navigation', async () => {
  const { bg, content, tab, generation } = await fallbackRound();
  await startConfirmedFallback({ bg, content, generation });
  await runNavigation(content);
  bg.chrome.tabs.onUpdated.listeners[0](1, { url: tab.url, status: 'loading' }, tab);
  // Drain the asynchronous Chrome listener, including selection and lease writes.
  for (let turn = 0; turn < 30; turn += 1) await new Promise(resolve => setImmediate(resolve));
  const state = await bg.api.readCoordinator();
  assert.equal(state.tabId, 1);
  assert.equal(state.generation, generation);
  assert.equal(state.paginationFallback, true);
  assert.equal(bg.tabActions.length, 0);
  assert.equal(bg.local.data['sweep:1'].query, 'Accessories');
});

function addNativePageControl(content, onClick) {
  content.body.innerText = 'Free (Valued at $25)';
  const control = {
    getAttribute(name) {
      if (name === 'href') return '?q=&page=2';
      return null;
    },
    getClientRects() { return [1]; },
    click: onClick
  };
  content.pager.querySelectorAll = () => [control];
  content.location.href = 'https://www.walmart.com/reviews/claim-product?q=';
  content.location.search = '?q=';
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.pager.innerText = '1 2 3';
}

test('page pacing counts navigation, rendering and delivery toward the minimum interval', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.api.setNow(100_000);
  content.api.setNavigationAt(94_000);
  content.api.setRandom(0.5);
  await content.local.set({ refreshMinutes: 5, pageDelaySeconds: 15 });
  await content.api.advanceDeliveredPage(9);
  assert.equal(content.timerCallbacks.get(content.api.getActionTimer()).ms, 9_000);
  const pacing = content.sentMessages.find(msg => msg.event === 'pacing').details;
  assert.equal(pacing.intervalMs, 15_000);
  assert.equal(pacing.elapsedMs, 6_000);
});

test('a seventeen-page scan keeps coverage with fifteen-second navigation starts', async () => {
  const content = loadContent({ pageAck: { ok: true } });
  const startedAt = 1_000_000;
  let clock = startedAt;
  let renderedPage = 1;
  const starts = [startedAt];
  content.location.href = 'https://www.walmart.com/reviews/claim-product';
  content.location.search = '';
  content.body.innerText = 'Free (Valued at $25)';
  content.pager.innerText = '1 2 17';
  Object.assign(content.api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 9 });
  content.api.setRandom(0.5);
  content.api.setNavigationAt(startedAt);
  content.api.setCollector(() => [{ item_id: `page-${renderedPage}` }]);
  content.local.data.refreshMinutes = 5;
  content.local.data.pageDelaySeconds = 15;
  content.local.data['sweep:1'] = { generation: 9, mode: 'none', total: 17,
    visited: [], endRetries: {}, startedAt };
  content.pager.querySelectorAll = () => Array.from({ length: 17 }, (_, index) => ({
    getAttribute(name) { return name === 'href' ? `?page=${index + 1}` : null; },
    getClientRects() { return [1]; },
    click() {
      starts.push(clock);
      content.location.search = `?page=${index + 1}`;
      content.location.href = 'https://www.walmart.com/reviews/claim-product' + content.location.search;
    }
  }));
  for (let page = 1; page <= 17; page += 1) {
    clock = starts.at(-1) + 2_500;
    content.api.setNow(clock);
    renderedPage = page;
    if (page > 1) {
      await runNavigation(content); // Changed cards unlock the native navigation.
      assert.equal(content.api.getPhase(), 'waiting-content');
    }
    clock += 1_000; // Normal settling/processing before acknowledged delivery.
    content.api.setNow(clock);
    // Execute extraction directly; the browser timer is covered separately.
    content.timerCallbacks.clear();
    await content.api.relay();
    if (page === 17) break;
    const wait = content.timerCallbacks.get(content.api.getActionTimer()).ms;
    assert.equal(wait, 11_500);
    clock += wait;
    content.api.setNow(clock);
    await runNavigation(content);
  }
  assert.deepEqual(content.sentMessages.filter(msg => msg.type === 'reviewer:page').map(msg => msg.page),
    Array.from({ length: 17 }, (_, index) => index + 1));
  assert.equal(starts.length, 17);
  assert.ok(starts.slice(1).every((start, index) => start - starts[index] === 15_000));
  assert.equal(content.local.data.lastSweepPages, 17);
  assert.equal(content.local.data.lastSweepDone - content.local.data.lastSweepStartedAt, 243_500);
});

test('native scan restart waits for fresh page-one cards and rejects stale leases', async () => {
  const content = loadContent({ pageAck: { ok: true } });
  content.body.innerText = 'Free (Valued at $25)';
  let cards = [{ item_id: 'last-page' }];
  let clicks = 0;
  content.api.setCollector(() => cards);
  const control = { getAttribute(name) { return name === 'href' ? '?page=1' : null; },
    getClientRects() { return [1]; }, click() {
      clicks += 1;
      content.location.href = 'https://www.walmart.com/reviews/claim-product';
      content.location.search = '';
    } };
  content.pager.querySelectorAll = () => [control];
  Object.assign(content.api.role, { active: false, tabId: 1, primaryTabId: 1, generation: 10 });
  const request = { type: 'reviewer:restart-native', active: true, tabId: 1,
    primaryTabId: 1, generation: 10, target: 'https://www.walmart.com/reviews/claim-product' };
  let reply = await contentMessage(content, { ...request, generation: 9 });
  assert.equal(reply.ok, false);
  reply = await contentMessage(content, request);
  assert.equal(reply.navigation, 'native');
  await runNavigation(content);
  assert.equal(clicks, 1);
  await runNavigation(content);
  assert.equal(content.api.getPhase(), 'waiting-page-change');
  assert.equal(content.sentMessages.some(msg => msg.type === 'reviewer:page'), false);
  cards = [{ item_id: 'fresh-first-page' }];
  await runNavigation(content);
  assert.equal(content.api.getPhase(), 'waiting-content');
  const relayTimer = [...content.timerCallbacks.values()].find(timer => timer.ms === 1_000);
  assert.ok(relayTimer);
  await relayTimer.callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(content.sentMessages.find(msg => msg.type === 'reviewer:page').page, 1);
});

test('verification removal requests a role immediately after the worker demotes the tab', async () => {
  const content = loadContent();
  const listener = content.chrome.runtime.onMessage.listeners[0];
  Object.assign(content.api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 9 });
  content.body.innerText = 'Robot or human? Press & Hold';
  listener({ type: 'reviewer:probe' }, {}, () => {});
  listener({ type: 'reviewer:set-role', active: false, tabId: 1,
    primaryTabId: null, generation: 10 }, {}, () => {});
  assert.equal(content.api.getPhase(), 'waiting-verification');
  // Finish the harness's startup role request before observing the clear.
  content.chrome.runtime.sendMessage = async message => {
    content.sentMessages.push(message);
    return { active: false, tabId: 1, generation: 10 };
  };
  content.api.setRoleRequestInFlight(false);
  const before = content.sentMessages.filter(msg => msg.type === 'reviewer:role').length;
  content.body.innerText = 'Free (Valued at $25)';
  content.mutationObservers[0].callback([{ type: 'characterData' }]);
  const timer = [...content.timerCallbacks.entries()].find(([, timer]) => timer.ms === 100);
  assert.ok(timer);
  content.timerCallbacks.delete(timer[0]);
  timer[1].callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(content.sentMessages.filter(msg => msg.type === 'reviewer:role').length, before + 1);
});

test('verification observation includes text changes and iframe source/title changes', () => {
  const { mutationObservers } = loadContent();
  const observed = mutationObservers[0].options;
  assert.equal(observed.characterData, true);
  assert.ok(observed.attributeFilter.includes('src'));
  assert.ok(observed.attributeFilter.includes('title'));
});

test('a slow verification alert cannot block resumption once reviewer content is usable', async () => {
  let release;
  let alertStarted;
  const started = new Promise(resolve => { alertStarted = resolve; });
  const tabs = [{ ...REVIEW_TAB }];
  const probes = new Map([[11, { ready: true, reviewerReady: false, botCheck: true }]]);
  const bg = loadBackground({ tabs, probes, fetchImpl(url) {
    if (new URL(url).pathname === '/bot-check') {
      alertStarted();
      return new Promise(resolve => { release = () => resolve(jsonResponse({})); });
    }
    return jsonResponse({});
  } });
  await bg.api.initialization;
  const detection = bg.api.handleBotCheck(11, 'text');
  await started;
  probes.set(11, { ready: true, reviewerReady: true, botCheck: false });
  let timeout;
  try {
    const selected = await Promise.race([bg.api.selectPrimary(), new Promise(resolve => {
      timeout = setTimeout(() => resolve(null), 100);
    })]);
    assert.equal(selected?.state.tabId, 11);
    assert.equal(await bg.api.pausedUntil(), 0);
  } finally {
    clearTimeout(timeout);
    release();
    await detection;
  }
});

test('older verification alert responses cannot overwrite a newer outage status', async () => {
  for (const oldStatus of [200, 503]) {
    let release;
    let started;
    const firstStarted = new Promise(resolve => { started = resolve; });
    let alerts = 0;
    const probes = new Map([[11, { ready: true, reviewerReady: false, botCheck: true }]]);
    const latestStatus = oldStatus === 200 ? 503 : 200;
    const bg = loadBackground({ tabs: [{ ...REVIEW_TAB }], probes, fetchImpl(url) {
      if (new URL(url).pathname !== '/bot-check') return jsonResponse({});
      if (++alerts === 1) {
        started();
        return new Promise(resolve => { release = () => resolve(jsonResponse({}, oldStatus)); });
      }
      return jsonResponse({}, latestStatus);
    } });
    await bg.api.initialization;
    const first = bg.api.handleBotCheck(11, 'text');
    await firstStarted;
    probes.set(11, { ready: true, reviewerReady: true, botCheck: false });
    await bg.api.selectPrimary();
    probes.set(11, { ready: true, reviewerReady: false, botCheck: true });
    await bg.api.handleBotCheck(11, 'text');
    release();
    await first;
    assert.equal(bg.local.data.botCheck.count, 2);
    assert.equal(bg.local.data.lastVerificationAlert.status, latestStatus === 200 ? 'sent' : 'failed');
    assert.equal(Boolean(bg.local.data.lastVerificationAlertError), latestStatus !== 200);
  }
});

test('native restart rejects missing controls, changed filters and visible challenges', async () => {
  for (const state of ['missing', 'filter', 'challenge', 'page-one']) {
    const content = loadContent();
    Object.assign(content.api.role, { active: false, tabId: 1, primaryTabId: 1, generation: 10 });
    if (state === 'challenge') content.body.innerText = 'Robot or human? Press & Hold';
    if (state === 'page-one') {
      content.location.href = 'https://www.walmart.com/reviews/claim-product';
      content.location.search = '';
    }
    const reply = await contentMessage(content, { type: 'reviewer:restart-native',
      active: true, tabId: 1, primaryTabId: 1, generation: 10,
      target: `https://www.walmart.com/reviews/claim-product${state === 'filter' ? '?q=other' : ''}`
    });
    assert.equal(reply.ok, false, state);
    assert.equal(content.api.role.active, false, state);
  }
});

test('native pagination waits for changed cards before relaying the new page', async () => {
  const content = loadContent({ pageAck: { ok: true } });
  let clicks = 0;
  let assigns = 0;
  let cards = [{ item_id: 'old' }];
  content.api.setCollector(() => cards);
  content.location.assign = () => { assigns += 1; };
  addNativePageControl(content, () => {
    clicks += 1;
    content.location.href = 'https://www.walmart.com/reviews/claim-product?q=&page=2';
    content.location.search = '?q=&page=2';
  });
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  assert.equal(clicks, 1);
  assert.equal(assigns, 0);
  await runNavigation(content);
  assert.equal(content.api.getPhase(), 'waiting-page-change');
  await content.api.relay();
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1]);
  cards = [{ item_id: 'new' }];
  await runNavigation(content);
  assert.equal(content.api.getPhase(), 'waiting-content');
  assert.equal(assigns, 0);
});

test('pagination links that omit empty search parameters still use the native control', async () => {
  const content = loadContent();
  let clicks = 0;
  addNativePageControl(content, () => { clicks += 1; });
  content.location.href = 'https://www.walmart.com/reviews/claim-product?q=&queryNups=';
  content.location.search = '?q=&queryNups=';
  content.pager.querySelectorAll = () => [{
    getAttribute(name) { return name === 'href' ? '?page=2' : null; },
    getClientRects() { return [1]; },
    click() { clicks += 1; }
  }];
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  assert.equal(clicks, 1);
});

test('an exact visible page link works without recognized pagination markup', async () => {
  const content = loadContent();
  content.location.href = 'https://www.walmart.com/reviews/claim-product?q=custom';
  content.document.querySelector = () => null;
  const control = {
    getAttribute(name) { return name === 'href' ? '?page=2&q=custom' : null; },
    getClientRects() { return [1]; }
  };
  content.document.querySelectorAll = selector => selector === 'a[href]' ? [control] : [];
  assert.equal(content.api.findPageControl('?q=custom&page=2', 2), control);
});

test('a page link outside recognized pagination cannot discard an active search', async () => {
  const content = loadContent();
  content.location.href = 'https://www.walmart.com/reviews/claim-product?q=custom';
  content.document.querySelector = () => null;
  content.document.querySelectorAll = selector => selector === 'a[href]' ? [{
    getAttribute(name) { return name === 'href' ? '?page=2' : null; },
    getClientRects() { return [1]; }
  }] : [];
  assert.equal(content.api.findPageControl('?q=custom&page=2', 2), null);
});

test('a numbered pager anchor without href is a native page control', () => {
  const content = loadContent();
  const control = {
    textContent: '2',
    getAttribute() { return null; },
    getClientRects() { return [1]; }
  };
  content.pager.querySelectorAll = selector => selector.split(',').some(part => part.trim() === 'a')
    ? [control] : [];
  assert.equal(content.api.findPageControl('?page=2', 2), control);
});

test('adjacent pagination prefers the visible native Next Page control over placeholder numbers', () => {
  const content = loadContent();
  content.location.href = 'https://www.walmart.com/reviews/claim-product?page=2';
  const current = { textContent: '2' };
  const numbered = { textContent: '3', getClientRects: () => [1],
    getAttribute: name => name === 'href' ? '#' : name === 'aria-label' ? 'Go to Page 3' : null };
  const next = { textContent: '', getClientRects: () => [1],
    getAttribute: name => name === 'href' ? '#' : name === 'aria-label' ? 'Next Page' : null };
  content.pager.querySelector = () => current;
  content.pager.querySelectorAll = () => [numbered, next];
  assert.equal(content.api.findPageControl('?page=3', 3), next);
  assert.equal(content.api.findPageControl('?page=1', 1), null);
  current.textContent = '1';
  assert.equal(content.api.findPageControl('?page=3', 3), numbered,
    'A pager still showing the old page must not authorize Next');
});

test('native Next controls cannot discard filters, jump gaps, or use disabled controls', () => {
  const content = loadContent();
  content.location.href = 'https://www.walmart.com/reviews/claim-product?q=custom&page=2';
  const next = { textContent: '', getClientRects: () => [1],
    getAttribute: name => name === 'href' ? '#' : name === 'aria-label' ? 'Next Page' : null };
  content.pager.querySelector = () => ({ textContent: '2' });
  content.pager.querySelectorAll = () => [next];
  assert.equal(content.api.findPageControl('?q=custom&page=3', 3), next);
  assert.equal(content.api.findPageControl('?page=3', 3), null);
  assert.equal(content.api.findPageControl('?q=custom&page=5', 5), null);
  next.disabled = true;
  assert.equal(content.api.findPageControl('?q=custom&page=3', 3), null);
});

function addCountedCards(content, count = 72) {
  const reads = { cards: 0, claims: 0 };
  const anchors = Array.from({ length: count }, (_, index) => ({
    parentElement: { get innerText() { reads.cards += 1; return 'Product\nFree (Valued at $25)'; } },
    querySelector() { return null; },
    getAttribute(name) {
      if (name === 'href') return `/ip/${10000000 + index}`;
      if (name === 'aria-label') return `Product number ${index}`;
      return null;
    },
    innerText: `Product number ${index}`
  }));
  Object.defineProperty(content.body, 'innerText', {
    get() { reads.claims += 1; return 'Free items remaining 3 items'; }, configurable: true
  });
  content.document.querySelectorAll = selector => selector === 'a[href*="/ip/"]' ? anchors : [];
  return { reads, ids: anchors.map((_, index) => `ip-${10000000 + index}`) };
}

test('live collection skips acknowledged card parsing and full collection retains every item', () => {
  const content = loadContent();
  const { reads, ids } = addCountedCards(content);
  const known = new Set(ids.slice(0, 71));
  const fresh = content.api.collect(known);
  assert.equal(fresh.length, 1);
  assert.equal(fresh[0].item_id, ids[71]);
  assert.equal(fresh[0].claims_remaining, 3);
  assert.equal(reads.cards, 1);
  reads.cards = 0;
  reads.claims = 0;
  assert.equal(content.api.collect(new Set(ids)).length, 0);
  assert.equal(reads.cards, 0);
  assert.equal(reads.claims, 0);
  assert.equal(content.api.collect().length, 72);
  assert.equal(reads.cards, 72);
});

test('live observation only parses fresh cards and failed deliveries remain retryable', async () => {
  const content = loadContent();
  const { reads, ids } = addCountedCards(content);
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.api.setPhase('complete');
  content.api.rememberAcknowledged(ids.slice(0, 71).map(item_id => ({ item_id })));
  let accepted = false;
  const sent = [];
  content.chrome.runtime.sendMessage = async message => {
    if (message.type === 'reviewer:page') {
      sent.push(message.items.map(item => item.item_id));
      return { ok: accepted, retryable: true };
    }
    return { ok: true };
  };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    reads.cards = 0;
    await content.api.observeVisibleUpdate();
    assert.equal(reads.cards, 1);
    assert.equal(sent.at(-1).join(','), ids[71]);
  }
  accepted = true;
  await content.api.observeVisibleUpdate();
  reads.cards = 0;
  await content.api.observeVisibleUpdate();
  assert.equal(reads.cards, 0);
  assert.equal(sent.length, 3);
});

test('numbered pager anchors with inert placeholder destinations use their click handlers', () => {
  for (const href of ['#', ' ', 'javascript:void(0)', 'javascript: void ( 0 );', 'javascript:void 0']) {
    const content = loadContent();
    content.location.href = 'https://www.walmart.com/reviews/claim-product';
    const control = { textContent: '2',
      getAttribute(name) { return name === 'href' ? href : null; },
      getClientRects() { return [1]; } };
    content.pager.querySelectorAll = () => [control];
    assert.equal(content.api.findPageControl('?page=2', 2), control, href);
  }
});

test('label matching never authorizes an unrelated or active script destination', () => {
  for (const href of ['?page=2', '#account', 'javascript:alert(1)', 'https://example.com/?page=2']) {
    const content = loadContent();
    content.location.href = 'https://www.walmart.com/reviews/claim-product?q=custom';
    const control = { textContent: '2',
      getAttribute(name) { return name === 'href' ? href : null; },
      getClientRects() { return [1]; } };
    content.pager.querySelectorAll = () => [control];
    assert.equal(content.api.findPageControl('?page=2&q=custom', 2), null, href);
  }
});

test('a placeholder anchor outside a recognized pager is never clicked by its number', () => {
  const content = loadContent();
  content.document.querySelector = () => null;
  const control = { textContent: '2',
    getAttribute(name) { return name === 'href' ? '#' : null; },
    getClientRects() { return [1]; } };
  content.document.querySelectorAll = () => [control];
  assert.equal(content.api.findPageControl('?page=2', 2), null);
});

test('pager inspection classifies href shapes without exporting their contents', () => {
  const content = loadContent();
  const elements = ['#', 'javascript:void(0)', '?page=2&q=private', '?pageNumber=2'].map(href => ({
    tagName: 'A', textContent: '2',
    getAttribute(name) { return name === 'href' ? href : null; },
    getClientRects() { return [1]; }
  }));
  content.pager.querySelectorAll = () => elements;
  const counts = content.api.inspectPageControls('?page=2', 2);
  assert.equal(counts.pagerPlaceholderHrefCount, 2);
  assert.equal(counts.pagerTargetPlaceholderCount, 2);
  assert.equal(counts.pagerPageHrefCount, 1);
  assert.equal(counts.pagerOtherHrefCount, 1);
  assert.equal(JSON.stringify(counts).includes('private'), false);
});

test('a site-named page-number marker is a native control inside its pager', () => {
  const content = loadContent();
  const control = {
    textContent: '2',
    getAttribute() { return null; },
    getClientRects() { return [1]; }
  };
  content.pager.querySelectorAll = selector => selector.includes('[data-automation-id="page-number"]')
    ? [control] : [];
  assert.equal(content.api.findPageControl('?page=2', 2), control);
});

test('a pager marker wrapper does not steal the click from its numbered anchor', () => {
  const content = loadContent();
  const anchor = { textContent: '2', getAttribute() { return null; },
    getClientRects() { return [1]; } };
  const wrapper = { ...anchor };
  content.pager.querySelectorAll = selector => selector.includes('[data-automation-id="page-number"]')
    ? [wrapper] : [anchor];
  assert.equal(content.api.findPageControl('?page=2', 2), anchor);
});

test('pager inspection is bounded, counts control shapes, and never returns raw page data', () => {
  const content = loadContent();
  content.document.querySelector = () => null;
  content.location.href = 'https://www.walmart.com/reviews/claim-product?q=private-search';
  const candidates = Array.from({ length: 1500 }, () => ({
    getAttribute(name) { return name === 'aria-label' ? 'Next page' : null; },
    textContent: 'private item title'
  }));
  content.document.querySelectorAll = () => candidates;
  const inspection = content.api.inspectPageControls('?page=2&q=private-search', 2);
  assert.equal(inspection.nextLabelButtonCount, 1000);
  assert.equal(inspection.recognizedPagerCount, 0);
  assert.equal(/private|href|https|label/i.test(JSON.stringify(Object.values(inspection))), false);
  assert.equal(content.api.inspectPageControls('?page=3&q=private-search', 3), null);
});

test('pager subtree inspection caps numeric structure counts without exporting labels', () => {
  const content = loadContent();
  content.pager.querySelectorAll = selector => selector === '*' ? Array.from({ length: 300 }, () => ({
    tagName: 'SPAN', textContent: '2',
    getAttribute(name) { return name === 'aria-label' ? 'private account label' : null; },
    getClientRects() { return []; }
  })) : [];
  const counts = content.api.inspectPageControls('?page=2', 2);
  assert.equal(counts.pagerElementCount, 200);
  assert.equal(counts.pagerTargetTextCount, 200);
  assert.equal(counts.pagerNumericTextCount, 200);
  assert.equal(counts.pagerHiddenCount, 200);
  assert.equal(JSON.stringify(counts).includes('private'), false);
});

test('a native pagination click that does nothing has one paced retry and exactly one URL fallback', async () => {
  const content = loadContent();
  let clicks = 0;
  let target;
  addNativePageControl(content, () => { clicks += 1; });
  content.location.assign = value => { target = value; };
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  // Replaying the watchdog with an advanced clock represents a stalled click.
  content.api.setNow(Date.now() + 31_000);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(target, undefined);
  await exhaustNoopRetry(content);
  assert.equal(clicks, 2);
  assert.equal(new URL(target).searchParams.get('page'), '2');
  assert.equal(content.timerCallbacks.size, 0);
});

test('a native click that never starts schedules its retry after five seconds instead of thirty', async () => {
  const content = loadContent();
  let assigned = 0;
  addNativePageControl(content, () => {});
  content.location.assign = () => { assigned += 1; };
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  content.api.setNow(Date.now() + 4_000);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(assigned, 0);
  content.api.setNow(Date.now() + 5_500);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(assigned, 0);
  await exhaustNoopRetry(content);
  assert.equal(assigned, 1);
  assert.equal(content.timerCallbacks.size, 0);
});

test('native navigation that started keeps the rendering allowance and rejects stale cards', async () => {
  const content = loadContent();
  let assigned = 0;
  let cards = [{ item_id: 'old-page' }];
  content.api.setCollector(() => cards);
  addNativePageControl(content, () => {
    content.location.href = 'https://www.walmart.com/reviews/claim-product?q=&page=2';
    content.location.search = '?q=&page=2';
  });
  content.location.assign = () => { assigned += 1; };
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  content.api.setNow(Date.now() + 6_000);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(assigned, 0);
  assert.equal(content.api.getPhase(), 'waiting-page-change');
  cards = [{ item_id: 'new-page' }];
  await runNavigation(content);
  assert.equal(content.api.getPhase(), 'waiting-content');
});

test('a placeholder control changing only the fragment still retries then falls back finitely', async () => {
  const content = loadContent();
  let assigned = 0;
  addNativePageControl(content, () => { content.location.href += '#'; });
  content.location.assign = () => { assigned += 1; };
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  content.api.setNow(Date.now() + 5_500);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(assigned, 0);
  await exhaustNoopRetry(content);
  assert.equal(assigned, 1);
});

test('a stalled native control backs off across page loads and retries after cooldown', async () => {
  const first = loadContent();
  addNativePageControl(first, () => {});
  await first.api.advanceDeliveredPage(9);
  await runNavigation(first);
  first.api.setNow(Date.now() + 31_000);
  await runNavigation(first);
  await new Promise(resolve => setImmediate(resolve));
  await exhaustNoopRetry(first);
  assert.ok(Object.values(first.local.data.nativeNavigationFailures).some(value => value.until > Date.now()));
  const next = loadContent();
  await next.local.set(first.local.data);
  let clicks = 0;
  let assigned = 0;
  addNativePageControl(next, () => { clicks += 1; });
  next.location.assign = () => { assigned += 1; };
  await next.api.advanceDeliveredPage(9);
  await runNavigation(next);
  assert.equal(clicks, 0);
  assert.equal(assigned, 0);
  next.api.setNow(Math.max(...Object.values(first.local.data.nativeNavigationFailures).map(v => v.until)) + 1);
  await runNavigation(next);
  assert.equal(clicks, 1);
});

test('a fresh final document reads the persisted native failure before acknowledging a restart', async () => {
  const first = loadContent();
  addNativePageControl(first, () => {});
  await first.api.advanceDeliveredPage(9);
  await runNavigation(first);
  first.api.setNow(Date.now() + 6_000);
  await runNavigation(first);
  await new Promise(resolve => setImmediate(resolve));
  await exhaustNoopRetry(first);
  const next = loadContent();
  await next.local.set(first.local.data);
  Object.assign(next.api.role, { active: false, tabId: 1, primaryTabId: 1, generation: 10 });
  let clicks = 0;
  next.pager.querySelectorAll = () => [{ getAttribute(name) {
    return name === 'href' ? '?page=1' : null;
  }, getClientRects() { return [1]; }, click() { clicks += 1; } }];
  const reply = await contentMessage(next, { type: 'reviewer:restart-native', active: true,
    tabId: 1, primaryTabId: 1, generation: 10,
    target: 'https://www.walmart.com/reviews/claim-product' });
  assert.equal(reply.ok, false);
  assert.equal(clicks, 0);
});

test('failed native capability does not disable a different control shape', async () => {
  const content = loadContent();
  addNativePageControl(content, () => {});
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  content.api.setNow(Date.now() + 6_000);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  await exhaustNoopRetry(content);
  let clicks = 0;
  content.pager.querySelectorAll = () => [{
    getAttribute(name) { return name === 'href' ? '#' : name === 'aria-label' ? 'Go to Page 2' : null; },
    getClientRects() { return [1]; }, click() { clicks += 1; }
  }];
  await content.api.pageDelayMs();
  content.api.navigatePage(2, 9);
  assert.equal(clicks, 1);
});

test('an expired failed native capability retains the normal startup allowance', async () => {
  const content = loadContent();
  let assigns = 0;
  addNativePageControl(content, () => {});
  content.location.assign = () => { assigns += 1; };
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  content.api.setNow(Date.now() + 6_000);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  await exhaustNoopRetry(content);
  assert.equal(assigns, 1);
  const until = Object.values(content.local.data.nativeNavigationFailures)[0].until;
  content.api.setNow(until + 1);
  await content.api.pageDelayMs();
  content.api.navigatePage(2, 9);
  content.api.setNow(until + 1_100);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(assigns, 1, 'A retry must not give up before normal site startup can finish');
  content.api.setNow(until + 5_100);
  await runNavigation(content);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(assigns, 1);
  await exhaustNoopRetry(content);
  assert.equal(assigns, 2);
});

test('one stalled numbered page does not suppress scrolling or native paging to other pages', async () => {
  const first = loadContent();
  const firstPager = addOffscreenPager(first);
  firstPager.control.getAttribute = name => name === 'href' ? '#' :
    name === 'aria-label' ? 'Go to Page 2' : null;
  await first.api.advanceDeliveredPage(9);
  first.api.setNow(109_000);
  await runNavigation(first);
  first.api.setNow(115_000);
  await runNavigation(first);
  await new Promise(resolve => setImmediate(resolve));
  await exhaustNoopRetry(first);
  assert.ok(Object.values(first.local.data.nativeNavigationFailures).some(value => value.until > 115_000));

  for (const targetPage of [2, 3]) {
    const next = loadContent();
    const pager = addOffscreenPager(next);
    await next.local.set(first.local.data);
    next.api.setNow(120_000);
    next.api.setNavigationAt(114_000);
    next.location.href = `https://www.walmart.com/reviews/claim-product?page=${targetPage - 1}`;
    next.location.search = `?page=${targetPage - 1}`;
    pager.control.getAttribute = name => name === 'href' ? '#' :
      name === 'aria-label' ? `Go to Page ${targetPage}` : null;
    let loads = 0;
    next.location.assign = () => { loads += 1; };
    await next.api.pageDelayMs();
    await next.api.preparePageNavigation(targetPage, 9, 9_000);
    assert.equal(pager.scrolls(), targetPage === 2 ? 0 : 1,
      'Only the failed destination should skip preparatory scrolling');
    next.api.navigatePage(targetPage, 9);
    assert.equal(pager.clicks(), targetPage === 2 ? 0 : 1);
    assert.equal(loads, 0, 'A cooled-down destination waits without another full load');
  }
});

test('legacy unscoped numbered cooldowns cannot suppress an upgraded pager', async () => {
  const content = loadContent();
  const pager = addOffscreenPager(content);
  pager.control.getAttribute = name => name === 'href' ? '#' :
    name === 'aria-label' ? 'Go to Page 2' : null;
  content.local.data.nativeNavigationFailures = {
    'https://www.walmart.com/reviews/claim-product|number': { until: 600_000, failures: 5 }
  };
  let loads = 0;
  content.location.assign = () => { loads += 1; };
  await content.api.advanceDeliveredPage(9);
  assert.equal(pager.scrolls(), 1);
  content.api.setNow(109_000);
  await runNavigation(content);
  assert.equal(pager.clicks(), 1);
  assert.equal(loads, 0);
});

test('site scroll restoration during pagination preserves the fresh-card navigation guard', async () => {
  const content = loadContent();
  let cards = [{ item_id: 'previous-page' }];
  let loads = 0;
  content.api.setCollector(() => cards);
  content.api.setNow(100_000);
  addNativePageControl(content, () => {
    content.location.href = 'https://www.walmart.com/reviews/claim-product?page=2';
    content.location.search = '?page=2';
  });
  content.location.assign = () => { loads += 1; };
  content.api.navigatePage(2, 9);
  content.api.setNow(101_500);
  content.listeners.get('scroll')({ isTrusted: true });
  await runNavigation(content);
  assert.equal(content.api.getPhase(), 'waiting-page-change',
    'The site scrolling after its own page click must not trigger a thirty-second input pause');
  cards = [{ item_id: 'next-page' }];
  await runNavigation(content);
  assert.equal(content.api.getPhase(), 'waiting-content');
  assert.equal(loads, 0);
});

test('direct user input during native paging still yields before reading changed cards', async () => {
  for (const event of ['wheel', 'touchstart', 'pointerdown', 'keydown', 'click']) {
    const content = loadContent();
    content.api.setNow(100_000);
    addNativePageControl(content, () => {});
    content.api.navigatePage(2, 9);
    content.api.setNow(100_100);
    content.listeners.get(event)({ isTrusted: true });
    await runNavigation(content);
    assert.equal(content.api.getPhase(), 'waiting-user', event);
  }
});

test('verification or user activity during a native fallback storage write cancels navigation', async () => {
  for (const blocker of ['verification', 'interaction']) {
    const content = loadContent();
    let finishWrite;
    let assigned = false;
    addNativePageControl(content, () => {});
    content.location.assign = () => { assigned = true; };
    await content.api.advanceDeliveredPage(9);
    await runNavigation(content);
    const originalSet = content.local.set.bind(content.local);
    content.local.set = values => 'nativeNavigationPausedUntil' in values
      ? new Promise(resolve => { finishWrite = () => originalSet(values).then(resolve); }) : originalSet(values);
    content.api.setNow(Date.now() + 31_000);
    await runNavigation(content);
    await new Promise(resolve => setImmediate(resolve));
    // The first no-op now waits for a retry; intercept the exhausted attempt's
    // durable write so the original cancellation boundary stays exercised.
    const retry = content.timerCallbacks.get(content.api.getActionTimer());
    content.api.setNow(content.api.getNow() + retry.ms + 1);
    await runNavigation(content);
    content.api.setNow(content.api.getNow() + 6_000);
    await runNavigation(content);
    await new Promise(resolve => setImmediate(resolve));
    if (blocker === 'verification') content.body.innerText = 'Robot or human? Press & Hold';
    else content.api.setLastInteraction(Date.now() + 32_000);
    finishWrite();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(assigned, false);
    assert.equal(content.api.getPhase(), blocker === 'verification' ? 'waiting-verification' : 'waiting-user');
  }
});

test('a challenge appearing during native navigation cancels its fallback', async () => {
  const content = loadContent();
  let assigned = false;
  addNativePageControl(content, () => {});
  content.location.assign = () => { assigned = true; };
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  content.body.innerText = 'Robot or human? Press & Hold';
  await runNavigation(content);
  assert.equal(assigned, false);
  assert.equal(content.api.getPhase(), 'waiting-verification');
});

test('watchdog recovery during native navigation reloads its target without ingesting stale cards', async () => {
  const content = loadContent();
  let target;
  addNativePageControl(content, () => {
    content.location.href = 'https://www.walmart.com/reviews/claim-product?q=&page=2';
    content.location.search = '?q=&page=2';
  });
  content.location.assign = value => { target = value; };
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  content.chrome.runtime.onMessage.listeners[0](
    { type: 'reviewer:recover', generation: 9 }, {}, () => {});
  await runNavigation(content);
  assert.equal(new URL(target).searchParams.get('page'), '2');
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1]);
});

test('alphabet mode starts at a page one before reading an arbitrary search', async () => {
  const content = loadContent();
  activateAlphabet(content, '123', 3);
  let collected = 0;
  let target;
  content.api.setCollector(() => { collected += 1; return []; });
  content.location.assign = url => { target = new URL(url); };
  await content.api.relay();
  await runNavigation(content);
  assert.equal(collected, 0);
  assert.equal(target.searchParams.get('q'), 'a');
  assert.equal(target.searchParams.get('page'), null);
  assert.equal(target.searchParams.get('queryNups'), '');
});

test('high value starts at Accessories before collecting an arbitrary search', async () => {
  const content = loadContent();
  activateAlphabet(content, '123', 3);
  content.local.data.sweepMode = 'high-value';
  let collected = 0;
  let target;
  content.api.setCollector(() => { collected += 1; return []; });
  content.location.assign = url => { target = new URL(url); };
  await content.api.relay();
  await runNavigation(content);
  assert.equal(collected, 0);
  assert.equal(target.searchParams.get('q'), 'Accessories');
  assert.equal(target.searchParams.get('page'), null);
  assert.equal(target.searchParams.get('queryNups'), '');
});

test('regular monitoring releases a manual search without collecting or navigating it', async () => {
  const content = loadContent();
  activateAlphabet(content, 'custom search');
  content.local.data.sweepMode = 'none';
  let assigned = false;
  content.location.assign = () => { assigned = true; };
  await content.api.relay();
  assert.equal(assigned, false);
  assert.equal(content.api.role.active, false);
  assert.equal(content.sentMessages.some(msg => msg.type === 'reviewer:page'), false);
  assert.equal(content.location.search, '?q=custom search&queryNups=&page=1');
});

test('alphabet mode visits all pages then persists the next letter before navigating', async () => {
  const content = loadContent();
  activateAlphabet(content, 'a', 2);
  content.local.data['sweep:1'] = {
    generation: 9, alphabet: true, query: 'a', total: 3, visited: [1],
    completedPages: 0, completedLetters: 0
  };
  let target;
  content.location.assign = url => { target = new URL(url); };
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  assert.equal(target.searchParams.get('q'), 'a');
  assert.equal(target.searchParams.get('page'), '3');
  content.location.href = target.toString();
  content.location.search = target.search;
  await content.api.advanceDeliveredPage(9);
  assert.equal(content.local.data['sweep:1'].query, 'b');
  assert.equal(content.local.data['sweep:1'].visited.length, 0);
  assert.equal(content.local.data['sweep:1'].total, null);
  assert.equal(content.local.data['sweep:1'].completedPages, 3);
  assert.equal(content.local.data.lastSweepDone, undefined);
  await runNavigation(content);
  assert.equal(target.searchParams.get('q'), 'b');
  assert.equal(target.searchParams.get('page'), null);
  // A new document resumes the persisted letter, rather than starting at a.
  const resumed = loadContent();
  activateAlphabet(resumed, 'b');
  resumed.local.data['sweep:1'] = content.local.data['sweep:1'];
  assert.equal((await resumed.api.readSweep(9)).query, 'b');
});

test('confirmed empty letters advance through only a-z and finish after z', async () => {
  const content = loadContent();
  activateAlphabet(content);
  content.pager.innerText = '';
  let target;
  content.location.assign = url => { target = new URL(url); };
  for (const letter of 'abcdefghijklmnopqrstuvwxyz') {
    const sweep = await content.api.readSweep(9);
    assert.equal(sweep.query, letter);
    sweep.completedPages = 2;
    sweep.endRetries = { 1: 2 };
    content.local.data['sweep:1'] = sweep;
    await content.api.handleEndPage(9);
    if (letter !== 'z') {
      assert.equal(content.api.getPhase(), 'waiting-navigation');
      await runNavigation(content);
      assert.match(target.searchParams.get('q'), /^[a-z]$/);
      content.location.href = target.toString();
      content.location.search = target.search;
    }
  }
  assert.equal(content.api.getPhase(), 'complete');
  assert.equal(content.local.data['sweep:1'], undefined);
  assert.equal(content.local.data.lastSweepPages, 2);
  assert.ok(content.local.data.lastSweepDone > 0);
  assert.equal(content.timerCallbacks.size, 0);
});

test('restarting alphabet mode resets the search and numbered-product filter', async () => {
  const bg = loadBackground({ tabs: [{ ...REVIEW_TAB, url:
    'https://www.walmart.com/reviews/claim-product?q=f&queryNups=123&page=3' }] });
  await bg.api.initialization;
  await bg.local.set({ sweepMode: true });
  const result = await bg.api.restartPrimary('manual');
  assert.equal(result.ok, true);
  const target = new URL(bg.tabActions.at(-1).options.url);
  assert.equal(target.searchParams.get('q'), 'a');
  assert.equal(target.searchParams.get('queryNups'), '');
  assert.equal(target.searchParams.get('page'), null);
});

test('sweep mode setting loads and saves in extension options', async () => {
  const options = loadOptions({ initial: { sweepMode: true } });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(options.elements.sweepMode.value, 'sweep');
  options.elements.sweepMode.value = 'none';
  await options.elements.save.listeners.get('click')();
  assert.equal(options.local.data.sweepMode, 'none');
});

test('high value mode searches every keyword in order and finishes after Wood', async () => {
  const keywords = ['Accessories', 'Appliance', 'Bag', 'Baking', 'Bamboo', 'Bath',
    'Bathroom', 'Bedding', 'Bedroom', 'Bulk', 'Car', 'Cat', 'Chairs', 'Cleaning',
    'Clothes', 'Cooking', 'Crafts', 'Dining', 'Dish', 'Dog', 'Electronics', 'Exercise', 'Floral', 'Fold', 'Frame',
    'Furniture', 'Gaming', 'Gifts', 'Glass', 'Gold Decor', 'Home', 'Home Decor',
    'Home Improvement', 'Hotel', 'Jewelry', 'Kitchen', 'LED', 'Machine', 'Maker',
    'Mesh', 'Office/School Supplies', 'Organization', 'Pantry', 'Personal Care',
    'Pet', 'Playpen', 'Repair', 'Shade', 'Shelves', 'Shower', 'Stainless', 'Steel',
    'Storage', 'Supplies', 'Tech', 'Tools', 'Travel', 'Utensils', 'Vanity',
    'Vintage', 'Wireless', 'Wood'];
  let content = loadContent();
  activateAlphabet(content);
  content.local.data.sweepMode = 'high-value';
  for (const [index, keyword] of keywords.entries()) {
    content.location.search = `?q=${encodeURIComponent(keyword)}&page=3`;
    content.location.href = `https://www.walmart.com/reviews/claim-product${content.location.search}`;
    const sweep = await content.api.readSweep(9);
    assert.equal(sweep.query, keyword);
    content.local.data['sweep:1'] = { ...sweep, total: 3, visited: [1, 2] };
    await content.api.advanceDeliveredPage(9);
    if (index < keywords.length - 1) {
      let target;
      content.location.assign = url => { target = new URL(url); };
      await runNavigation(content);
      assert.equal(target.searchParams.get('q'), keywords[index + 1]);
      assert.equal(target.searchParams.get('page'), null);
      const persisted = content.local.data['sweep:1'];
      assert.equal(persisted.completedPages, (index + 1) * 3);
      // Resume from a fresh document after every keyword transition.
      content = loadContent();
      activateAlphabet(content);
      content.local.data.sweepMode = 'high-value';
      content.local.data['sweep:1'] = persisted;
    }
  }
  assert.equal(content.api.getPhase(), 'complete');
  assert.equal(content.local.data['sweep:1'], undefined);
  assert.equal(content.local.data.lastSweepPages, keywords.length * 3);
});

test('restarting each search mode chooses the correct first query', async () => {
  for (const [mode, query] of [['none', ''], ['sweep', 'a'], ['high-value', 'Accessories']]) {
    const bg = loadBackground({ tabs: [{ ...REVIEW_TAB, url:
      mode === 'none' ? 'https://www.walmart.com/reviews/claim-product?q=&page=3' :
        'https://www.walmart.com/reviews/claim-product?q=existing&queryNups=123&page=3' }] });
    await bg.api.initialization;
    await bg.local.set({ sweepMode: mode });
    assert.equal((await bg.api.restartPrimary('manual')).ok, true);
    const target = new URL(bg.tabActions.at(-1).options.url);
    assert.equal(target.searchParams.get('q'), mode === 'none' ? null : query);
    assert.equal(target.searchParams.get('page'), null);
    assert.equal(target.searchParams.get('queryNups'), mode === 'none' ? null : '');
  }
});

test('all search modes load and save in options including legacy disabled mode', async () => {
  for (const mode of [false, 'none', 'sweep', 'high-value']) {
    const options = loadOptions({ initial: { sweepMode: mode } });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(options.elements.sweepMode.value, mode === false ? 'none' : mode);
    options.elements.sweepMode.value = 'high-value';
    await options.elements.save.listeners.get('click')();
    assert.equal(options.local.data.sweepMode, 'high-value');
  }
});

test('high value status shows keyword progress instead of alphabet progress', async () => {
  const options = loadOptions({
    initial: { lastRelay: Date.now(), lastPage: 2, lastCollected: 10 },
    sweepStatus: { ok: true, tabCount: 1, sweep: {
      mode: 'high-value', query: 'Gold Decor', completedPages: 80,
      visited: [1, 2], total: 3
    }}
  });
  await new Promise(resolve => setImmediate(resolve));
  await options.api.renderStatus();
  assert.match(options.elements.status.textContent,
    /High Value mode: search Gold Decor \(30 of 62 keywords\)/);
  assert.match(options.elements.status.textContent, /80 pages read for earlier keywords/);
});

// Search-mode empty results retain the existing bounded confirmation/recovery.
test('transient mid-search no-results stays on the same page for retry', async () => {
  const { api, local, timerCallbacks } = loadFinalLetterContent();
  Object.assign(api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 9 });
  local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: 24, visited: [1], endRetries: {}, startedAt: 1
  };

  await api.handleEndPage(9);
  assert.deepEqual(local.data['sweep:1'].visited, [1]);
  assert.equal(local.data['sweep:1'].endRetries[2], 1);
  assert.equal(api.getPhase(), 'transient-end-retry');
  assert.equal(timerCallbacks.size, 1);
});

test('an empty page cannot end an unknown-length catalogue on its first render', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.pager.innerText = '';
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: null, visited: [1], endRetries: {}, startedAt: 1
  };
  await content.api.handleEndPage(9);
  assert.equal(content.api.getPhase(), 'transient-end-retry');
  assert.equal(content.local.data['sweep:1'].endRetries[2], 1);
  assert.equal(content.local.data.lastSweepDone, undefined);
});

test('an unread last known page with no results retries instead of truncating coverage', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.pager.innerText = '1 2';
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: 2, visited: [1], endRetries: {}, startedAt: 1
  };
  await content.api.handleEndPage(9);
  assert.equal(content.api.getPhase(), 'transient-end-retry');
  assert.equal(content.local.data['sweep:1'].endRetries[2], 1);
  assert.equal(content.local.data.lastSweepDone, undefined);
});

test('an unknown-length catalogue finishes only after repeated empty-end confirmation', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.pager.innerText = '';
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: null, visited: [1], endRetries: { 2: 2 }, startedAt: 1
  };
  await content.api.handleEndPage(9);
  assert.equal(content.api.getPhase(), 'complete');
  assert.equal(content.local.data.lastSweepPages, 1);
});

test('empty-end confirmation rereads in two seconds and reloads only once before completing a stale tail', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.pager.innerText = '1 2';
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: 2, visited: [1], endRetries: {}, startedAt: 1
  };
  let reloads = 0;
  content.location.reload = () => { reloads += 1; };

  await content.api.handleEndPage(9);
  assert.equal(content.timerCallbacks.get(content.api.getActionTimer()).ms, 2_000);
  await runNavigation(content);
  assert.equal(reloads, 0, 'First confirmation should reuse the loaded document');
  const [relayId, relayTimer] = [...content.timerCallbacks.entries()][0];
  content.timerCallbacks.delete(relayId);
  relayTimer.callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(content.local.data['sweep:1'].endRetries[2], 2);
  assert.equal(content.timerCallbacks.get(content.api.getActionTimer()).ms, 2_000);
  await runNavigation(content);
  assert.equal(reloads, 1);

  const reloaded = loadFinalLetterContent();
  Object.assign(reloaded.api.role, { active: true, tabId: 1, generation: 9 });
  reloaded.pager.innerText = '1 2';
  await reloaded.local.set(content.local.data);
  await reloaded.api.handleEndPage(9);
  assert.equal(reloaded.api.getPhase(), 'complete');
  assert.equal(reloaded.local.data.lastSweepPages, 1);
  assert.equal(reloaded.local.data['sweep:1'], undefined);
  assert.equal(reloaded.sentMessages.some(message => message.type === 'reviewer:unusable'), false);
});

test('cards appearing during the first empty confirmation are delivered without reloading', async () => {
  const content = loadFinalLetterContent({ pageAck: { ok: true } });
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.pager.innerText = '1 2';
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: 2, visited: [1], endRetries: {}, startedAt: 1
  };
  let reloads = 0;
  content.location.reload = () => { reloads += 1; };
  await content.api.handleEndPage(9);
  content.api.setCollector(() => [{ item_id: 'late-rendered' }]);
  await runNavigation(content);
  const [relayId, relayTimer] = [...content.timerCallbacks.entries()][0];
  content.timerCallbacks.delete(relayId);
  relayTimer.callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(reloads, 0);
  assert.equal(content.api.getPhase(), 'complete');
  assert.equal(content.local.data.lastSweepPages, 2);
  assert.equal(content.sentMessages.some(message => message.type === 'reviewer:page' &&
    message.items[0].item_id === 'late-rendered'), true);
});

test('a confirmed empty nineteenth page closes an eighteen-page scan despite a stale pager', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.location.href = 'https://www.walmart.com/reviews/claim-product?page=19';
  content.location.search = '?page=19';
  content.pager.innerText = '1 2 18 19';
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: 19, visited: Array.from({ length: 18 }, (_, index) => index + 1),
    endRetries: { 19: 2 }, startedAt: 1
  };
  await content.api.handleEndPage(9);
  assert.equal(content.api.getPhase(), 'complete');
  assert.equal(content.local.data.lastSweepPages, 18);
  assert.equal(content.local.data['sweep:1'], undefined);
});

test('a smaller pager confirmed after reload closes a catalogue whose stored total shrank', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.location.href = 'https://www.walmart.com/reviews/claim-product?page=19';
  content.location.search = '?page=19';
  content.pager.innerText = '1 2 17 18';
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: 24, visited: Array.from({ length: 18 }, (_, index) => index + 1),
    endRetries: { 19: 2 }, startedAt: 1
  };
  await content.api.handleEndPage(9);
  assert.equal(content.api.getPhase(), 'complete');
  assert.equal(content.local.data.lastSweepPages, 18);
});

test('a confirmed empty tail fills unread interior pages before reporting completion', async () => {
  for (const knownTotal of [null, 3]) {
    const content = loadFinalLetterContent();
    Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
    content.location.href = 'https://www.walmart.com/reviews/claim-product?page=3';
    content.location.search = '?page=3';
    content.pager.innerText = knownTotal === null ? '' : '1 2 3';
    content.local.data['sweep:1'] = {
      generation: 9, mode: 'sweep', query: 'z', total: knownTotal, visited: [1], endRetries: { 3: 2 }, startedAt: 1
    };
    let target;
    content.location.assign = url => { target = new URL(url); };
    await content.api.handleEndPage(9);
    assert.equal(content.local.data.lastSweepDone, undefined);
    assert.equal(content.local.data['sweep:1'].total, 2);
    await runNavigation(content);
    assert.equal(target.searchParams.get('page'), '2');
  }
});

test('a smaller pager cannot skip unread pages before the confirmed empty boundary', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.location.href = 'https://www.walmart.com/reviews/claim-product?page=5';
  content.location.search = '?page=5';
  content.pager.innerText = '1 2 3';
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: 24, visited: [1, 2, 3], endRetries: { 5: 2 }, startedAt: 1
  };
  let target;
  content.location.assign = url => { target = new URL(url); };
  await content.api.handleEndPage(9);
  assert.equal(content.local.data.lastSweepDone, undefined);
  assert.equal(content.local.data['sweep:1'].total, 4);
  await runNavigation(content);
  assert.equal(target.searchParams.get('page'), '4');
});

test('unknown totals visit an unread earlier page before expanding the scan', () => {
  const content = loadFinalLetterContent();
  assert.equal(content.api.nextUnvisited({ total: null, visited: [2, 3] }), 1);
});

test('a persistently empty interior page fails the sweep with an explicit restart request', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: 24, visited: [1], endRetries: { 2: 2 }, startedAt: 1
  };
  await content.api.handleEndPage(9);
  const failed = content.sentMessages.find(message => message.type === 'reviewer:unusable');
  assert.equal(failed?.restartSweep, true);
  assert.equal(content.local.data.lastSweepDone, undefined);
  assert.deepEqual(content.local.data['sweep:1'].visited, [1]);
  assert.equal(content.local.data['sweep:1'].endRetries[2], 3);
  assert.equal(content.sentMessages.some(message => message.type === 'reviewer:sweep-complete'), false);
});

test('repeated checks in one document cannot replace the final fresh-page confirmation', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.pager.innerText = '1 2';
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: 2, visited: [1], endRetries: {}, startedAt: 1
  };
  for (let index = 0; index < 5; index += 1) await content.api.handleEndPage(9);
  assert.equal(content.local.data['sweep:1'].endRetries[2], 2);
  assert.equal(content.local.data.lastSweepDone, undefined);
  assert.equal(content.api.getPhase(), 'transient-end-retry');
});

async function confirmAndRestartEmptyBoundary(content) {
  Object.assign(content.api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 9 });
  content.api.setNow(1_000_000);
  content.pager.innerText = '1 2';
  content.local.data['sweep:1'] = { generation: 9, mode: 'sweep', query: 'z', total: 2,
    visited: [1], endRetries: { 2: 2 }, startedAt: 1 };
  await content.api.handleEndPage(9); // Fresh-document final confirmation.
  assert.equal(content.api.getPhase(), 'complete');
  content.chrome.runtime.onMessage.listeners[0]({ type: 'reviewer:set-role', active: true,
    tabId: 1, primaryTabId: 1, generation: 10 }, {}, () => {});
  content.timerCallbacks.clear();
  content.local.data['sweep:1'] = { generation: 10, mode: 'sweep', query: 'z', total: 2,
    visited: [1], endRetries: {}, startedAt: 1_100_000 };
  content.api.setNow(1_100_000);
}

test('a previously confirmed empty boundary is revisited and confirmed locally without another reload', async () => {
  const content = loadFinalLetterContent();
  await confirmAndRestartEmptyBoundary(content);
  await content.api.handleEndPage(10);
  assert.equal(content.api.getPhase(), 'transient-end-retry');
  assert.equal(content.timerCallbacks.get(content.api.getActionTimer()).ms, 2_000);
  content.api.setNow(1_102_000);
  await content.api.handleEndPage(10);
  assert.equal(content.api.getPhase(), 'complete');
  assert.equal(content.local.data.lastSweepPages, 1);
  assert.equal(content.sentMessages.filter(msg => msg.event === 'empty-boundary-reused').length, 1);
});

test('boundary reuse cannot hide advertised growth, a changed search, expiry or a challenge', async () => {
  for (const invalidation of ['growth', 'search', 'mode', 'expiry', 'challenge']) {
    const content = loadFinalLetterContent();
    await confirmAndRestartEmptyBoundary(content);
    if (invalidation === 'growth') content.pager.innerText = '1 2 3';
    if (invalidation === 'search') {
      content.location.search = '?q=other&page=2';
      content.location.href = 'https://www.walmart.com/reviews/claim-product?q=other&page=2';
    }
    if (invalidation === 'mode') content.local.data['sweep:1'].mode = 'high-value';
    if (invalidation === 'expiry') content.api.setNow(1_600_001);
    if (invalidation === 'challenge') {
      content.body.innerText = 'Robot or human? Press & Hold';
      content.chrome.runtime.onMessage.listeners[0]({ type: 'reviewer:probe' }, {}, () => {});
      content.body.innerText = 'No search results';
    }
    await content.api.handleEndPage(10);
    await content.api.handleEndPage(10);
    assert.notEqual(content.api.getPhase(), 'complete', invalidation);
    assert.equal(content.local.data.lastSweepStartedAt, 1, invalidation);
  }
});

test('fast boundary reuse never extends the original proof expiry', async () => {
  const content = loadFinalLetterContent();
  await confirmAndRestartEmptyBoundary(content);
  await content.api.handleEndPage(10);
  content.api.setNow(1_102_000);
  await content.api.handleEndPage(10);
  assert.equal(content.api.getPhase(), 'complete');
  content.chrome.runtime.onMessage.listeners[0]({ type: 'reviewer:set-role', active: true,
    tabId: 1, primaryTabId: 1, generation: 11 }, {}, () => {});
  content.timerCallbacks.clear();
  content.local.data['sweep:1'] = { generation: 11, mode: 'sweep', query: 'z', total: 2,
    visited: [1], endRetries: {}, startedAt: 1_600_000 };
  content.api.setNow(1_600_001);
  await content.api.handleEndPage(11);
  await content.api.handleEndPage(11);
  assert.equal(content.api.getPhase(), 'transient-end-retry');
  assert.equal(content.local.data['sweep:1'].endRetries[2], 2);
});

test('verification during empty-page confirmation cancels its reload and preserves progress', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: 2, visited: [1], endRetries: { 2: 1 }, startedAt: 1
  };
  let reloads = 0;
  content.location.reload = () => { reloads += 1; };
  await content.api.handleEndPage(9);
  content.body.innerText = 'Robot or human? Press & Hold';
  await runNavigation(content);
  assert.equal(reloads, 0);
  assert.equal(content.api.getPhase(), 'waiting-verification');
  assert.equal(content.local.data.lastSweepDone, undefined);
  assert.deepEqual(content.local.data['sweep:1'].visited, [1]);
});

test('a thirty-second blank-card timeout in search mode restarts the failed scan instead of reloading forever', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.body.innerText = 'Loading items';
  await content.api.relay();
  content.api.setNow(Date.now() + 31_000);
  await content.api.relay();
  const failed = content.sentMessages.find(message => message.type === 'reviewer:unusable');
  assert.equal(failed?.restartSweep, true);
  assert.equal(content.local.data.lastSweepDone, undefined);
});

test('a failed-scan recovery navigates to page one after its bounded delay', async () => {
  const content = loadFinalLetterContent();
  Object.assign(content.api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 9 });
  content.local.data['sweep:1'] = {
    generation: 9, mode: 'sweep', query: 'z', total: 24, visited: [1], endRetries: { 2: 2 }, startedAt: 1
  };
  content.chrome.runtime.sendMessage = async message => message.type === 'reviewer:unusable'
    ? { ok: false, recover: 'restart', retryAfterMs: 15_000, active: true,
      tabId: 1, primaryTabId: 1, generation: 9 } : message.type === 'reviewer:focus'
      ? { ok: true, enabled: false, reason: 'disabled' } : { ok: true };
  let target;
  let reloads = 0;
  content.location.assign = url => { target = new URL(url); };
  content.location.reload = () => { reloads += 1; };
  await content.api.handleEndPage(9);
  assert.equal(content.api.getPhase(), 'recovering');
  assert.equal(content.timerCallbacks.get(content.api.getActionTimer()).ms, 15_000);
  await runNavigation(content);
  assert.equal(target.pathname, '/reviews/claim-product');
  assert.equal(target.searchParams.get('page'), null);
  assert.equal(reloads, 0);
});

test('a negative ingest acknowledgement never marks the page visited', async () => {
  const { api, local, timerCallbacks } = loadContent({
    pageAck: { ok: false, retryable: true, active: true, primaryTabId: 1, generation: 6 }
  });
  Object.assign(api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 6 });
  api.setCollector(() => [{ item_id: 'not-delivered-yet' }]);

  await api.relay();
  assert.equal(local.data['sweep:1'], undefined);
  assert.equal(api.getPhase(), 'ingest-retry');
  assert.equal(timerCallbacks.size, 1);
});

test('pending navigation survives user activity and runs once the page is idle', () => {
  const { api, timerCallbacks } = loadContent();
  Object.assign(api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 2 });
  api.setLastInteraction(Date.now());
  let ran = 0;
  api.queueWhenIdle(() => { ran += 1; }, 0);

  const first = [...timerCallbacks.entries()][0];
  timerCallbacks.delete(first[0]);
  first[1].callback();
  assert.equal(ran, 0);
  assert.equal(api.getPhase(), 'waiting-user');

  api.setLastInteraction(0);
  const second = [...timerCallbacks.entries()][0];
  timerCallbacks.delete(second[0]);
  second[1].callback();
  assert.equal(ran, 1);
});

test('page mutations cannot wake a completed sweep', () => {
  const { api, timerCallbacks } = loadContent();
  Object.assign(api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 2 });
  api.setPhase('complete');

  api.scheduleRelay(0, false);

  assert.equal(timerCallbacks.size, 0);
  assert.equal(api.getPhase(), 'complete');
});

test('new cards on a completed page relay promptly without starting another sweep', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.api.setPhase('complete');
  const old = { item_id: 'already-acknowledged' };
  const fresh = { item_id: 'new-live-card' };
  content.api.setCollector(() => [old, fresh]);
  content.api.rememberAcknowledged([old]);
  const sent = [];
  content.chrome.runtime.sendMessage = async message => {
    if (message.type === 'reviewer:page') sent.push(message);
    return { ok: true };
  };
  await content.api.observeVisibleUpdate();
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0].items.map(item => item.item_id), ['new-live-card']);
  assert.equal(content.api.getPhase(), 'complete');
  assert.equal(content.local.data['sweep:1'], undefined);
  await content.api.observeVisibleUpdate();
  assert.equal(sent.length, 1);
});

test('cards rendered while waiting for the next page relay without changing navigation', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  const old = { item_id: 'acknowledged' };
  const fresh = { item_id: 'late-card' };
  content.api.rememberAcknowledged([old]);
  content.api.setCollector(() => [old, fresh]);
  let navigated = 0;
  content.api.queueWhenIdle(() => { navigated += 1; }, 60_000);
  const queued = [...content.timerCallbacks.entries()];
  const sent = [];
  content.chrome.runtime.sendMessage = async message => {
    if (message.type === 'reviewer:page') sent.push(message);
    return { ok: true };
  };
  await content.api.observeVisibleUpdate();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].items[0].item_id, 'late-card');
  assert.equal(content.api.getPhase(), 'waiting-navigation');
  assert.deepEqual([...content.timerCallbacks.entries()], queued);
  await runNavigation(content);
  assert.equal(navigated, 1);
  await content.api.observeVisibleUpdate();
  assert.equal(sent.length, 1); // No extraction during the navigation itself.
});

test('a failed late-card relay defers navigation until the local retry succeeds', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.api.setCollector(() => [{ item_id: 'late-card' }]);
  let navigated = 0;
  let attempts = 0;
  content.api.queueWhenIdle(() => { navigated += 1; }, 60_000);
  const [queuedId, queuedTimer] = [...content.timerCallbacks.entries()][0];
  content.chrome.runtime.sendMessage = async message => message.type === 'reviewer:page'
    ? { ok: ++attempts > 1, retryable: true } : { ok: true };
  await content.api.observeVisibleUpdate();
  content.timerCallbacks.delete(queuedId);
  queuedTimer.callback();
  assert.equal(navigated, 0);
  await content.api.observeVisibleUpdate();
  await runNavigation(content);
  assert.equal(navigated, 1);
});

test('live-card failures are retried and verification prevents live relays', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.api.setPhase('complete');
  content.api.setCollector(() => [{ item_id: 'new-live-card' }]);
  let calls = 0;
  content.chrome.runtime.sendMessage = async message => {
    if (message.type === 'reviewer:page') return { ok: ++calls > 1, retryable: true };
    return { ok: true };
  };
  await content.api.observeVisibleUpdate();
  assert.equal(calls, 1);
  assert.ok(content.timerCallbacks.size > 0);
  await content.api.observeVisibleUpdate();
  assert.equal(calls, 2);
  content.body.innerText = 'Robot or human? Press & Hold';
  await content.api.observeVisibleUpdate();
  assert.equal(calls, 2);
  assert.equal(content.api.getPhase(), 'waiting-verification');
});

test('mutations during a failed live relay cannot shorten its retry backoff', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  content.api.setPhase('complete');
  content.api.setCollector(() => [{ item_id: 'new-live-card' }]);
  let fail;
  let entered;
  const deliveryStarted = new Promise(resolve => { entered = resolve; });
  content.chrome.runtime.sendMessage = message => {
    if (message.type !== 'reviewer:page') return Promise.resolve({ ok: true });
    return new Promise(resolve => { fail = resolve; entered(); });
  };
  const attempt = content.api.observeVisibleUpdate();
  await deliveryStarted;
  content.api.scheduleVisibleObservation(1_000);
  fail({ ok: false, retryable: true });
  await attempt;
  const timers = [...content.timerCallbacks.values()];
  assert.equal(timers.length, 1);
  assert.equal(timers[0].ms, 2_000);
});


// --- Walmart bot check -------------------------------------------------------

const REVIEW_TAB = {
  id: 11, windowId: 1, index: 0, status: 'complete',
  url: 'https://www.walmart.com/reviews/claim-product?q=&page=20'
};

test('toolbar captures the active challenge before opening Options', async () => {
  const tab = { ...REVIEW_TAB, url: 'https://www.walmart.com/blocked' };
  const bg = loadBackground({ tabs: [tab] });
  await bg.api.initialization;
  await bg.local.set({ botCheck: { pausedUntil: Date.now() + 60_000,
    count: 1, lastAt: Date.now(), tabId: 11, challengedTabIds: [11], knownTabIds: [11] } });
  const actions = [];
  bg.chrome.tabs.captureVisibleTab = async () => {
    actions.push('capture');
    return 'data:image/jpeg;base64,/9j/AAAA';
  };
  bg.chrome.downloads = { async download(options) {
    actions.push(options.filename.endsWith('.jpg') ? 'image' : 'report');
    return actions.length;
  } };
  bg.chrome.runtime.openOptionsPage = async () => actions.push('options');
  await bg.chrome.action.onClicked.listeners[0](tab);
  assert.deepEqual(actions, ['capture', 'image', 'report', 'options']);
});

test('toolbar opens Options when screenshot permission fails, without changing verification', async () => {
  const tab = { ...REVIEW_TAB, url: 'https://www.walmart.com/blocked' };
  const bg = loadBackground({ tabs: [tab] });
  await bg.api.initialization;
  const until = Date.now() + 60_000;
  await bg.local.set({ botCheck: { pausedUntil: until,
    count: 1, lastAt: Date.now(), tabId: 11, challengedTabIds: [11], knownTabIds: [11] } });
  bg.chrome.tabs.captureVisibleTab = async () => { throw new Error('permission unavailable'); };
  let opened = false;
  bg.chrome.runtime.openOptionsPage = async () => { opened = true; };
  await bg.chrome.action.onClicked.listeners[0](tab);
  assert.equal(opened, true);
  assert.equal(bg.local.data.botCheck.pausedUntil, until);
  assert.equal(bg.local.data.lastVerificationCapture.status, 'unavailable');
});

function pausedBackground() {
  const probes = new Map([[11, { ready: true, active: true, generation: 0 }]]);
  return loadBackground({ tabs: [REVIEW_TAB], probes, summary: { ok: true } });
}

test('verification has a persistent toolbar warning until usable reviewer content resumes', async () => {
  const bg = loadBackground({ tabs: [{ ...REVIEW_TAB }],
    probes: new Map([[11, { ready: true, reviewerReady: true }]]) });
  await bg.api.initialization;
  await bg.api.handleBotCheck(11, 'text');
  assert.equal(bg.badgeCalls.at(-1)?.text, '!');
  assert.match(bg.titleCalls.at(-1)?.title || '', /verification.*needs attention/i);
  bg.chrome.alarms.onAlarm.listeners[0]({ name: 'clearBadge' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(bg.badgeCalls.at(-1)?.text, '!');
  await bg.api.selectPrimary();
  assert.equal(bg.badgeCalls.at(-1)?.text, '');
  assert.equal(bg.titleCalls.at(-1)?.title, 'Reviewer Item Relay');
});

test('an outstanding verification restores its toolbar warning after worker recreation', async () => {
  const local = storageArea({ botCheck: { pausedUntil: Date.now() + 60_000,
    count: 1, lastAt: Date.now(), tabId: 11, challengedTabIds: [11], knownTabIds: [11] } });
  const bg = loadBackground({ localArea: local });
  await bg.api.initialization;
  assert.equal(bg.badgeCalls.at(-1)?.text, '!');
});

test('a late item delivery cannot overwrite the verification toolbar warning', async () => {
  const bg = loadBackground({ summary: { seen: 1, new: 1, notified: 1 } });
  await bg.api.initialization;
  let releaseStatus;
  let statusEntered;
  const entered = new Promise(resolve => { statusEntered = resolve; });
  const originalSet = bg.local.set.bind(bg.local);
  bg.local.set = async values => {
    if (values.lastRelay !== undefined) {
      statusEntered();
      await new Promise(resolve => { releaseStatus = resolve; });
    }
    return originalSet(values);
  };
  const delivery = bg.api.post([{ item_id: 'late' }], 1);
  await entered;
  await bg.api.handleBotCheck(11, 'text');
  assert.equal(bg.badgeCalls.at(-1)?.text, '!');
  releaseStatus();
  await delivery;
  assert.equal(bg.badgeCalls.at(-1)?.text, '!');
});

test('a failed notifier alert is recorded without undoing verification detection', async () => {
  const bg = loadBackground({ fetchImpl: () => jsonResponse({}, 503) });
  await bg.api.initialization;
  await bg.api.handleBotCheck(11, 'text');
  assert.match(bg.local.data.lastVerificationAlertError || '', /not confirmed/i);
  assert.equal(bg.local.data.lastVerificationAlert?.status, 'failed');
  assert.ok(bg.local.data.botCheck.pausedUntil > Date.now());
});

test('an accepted notifier alert clears an earlier verification alert error', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  await bg.local.set({ lastVerificationAlertError: 'previous failed alert' });
  await bg.api.handleBotCheck(11, 'text');
  assert.equal(bg.local.data.lastVerificationAlertError, '');
  assert.equal(bg.local.data.lastVerificationAlert?.status, 'sent');
});

test('Options shows an unconfirmed verification alert separately from capture errors', async () => {
  const ui = loadOptions({ initial: { lastVerificationAlertError:
    'Verification was detected, but the notifier alert was not confirmed.' } });
  await ui.api.renderStatus();
  assert.match(ui.elements.status.textContent, /notifier alert was not confirmed/);
});

test('Relay Options enables built-in verification independently of scan saves', async () => {
  const ui = loadOptions({ initial: { token: 'keep-local-secret', refreshMinutes: 5 } });
  await ui.elements.enableVerificationAssist.listeners.get('click')();
  assert.equal(ui.local.data.verificationAssistEnabled, true);
  assert.match(ui.elements.verificationAssistStatus.textContent, /^Enabled\./);
  assert.equal(ui.local.data.refreshMinutes, 5);
  assert.equal(ui.local.data.token, 'keep-local-secret');
  const saves = ui.optionsFetches.filter(call => call.method === 'PUT');
  assert.deepEqual(saves.map(call => call.path), ['/scanner-settings']);
  assert.equal(JSON.parse(saves[0].body).verificationAssistEnabled, true);
  assert.equal(saves[0].body.includes('keep-local-secret'), false);
  await ui.elements.disableVerificationAssist.listeners.get('click')();
  assert.equal(ui.local.data.verificationAssistEnabled, false);
  assert.equal(ui.elements.verificationAssistStatus.textContent, 'Disabled.');
});

test('Relay Options reports missing debugger permission before enabling automatic input', async () => {
  const ui = loadOptions({ debuggerPermission: false });
  await ui.elements.enableVerificationAssist.listeners.get('click')();
  assert.notEqual(ui.local.data.verificationAssistEnabled, true);
  assert.match(ui.elements.verificationAssistStatus.textContent, /Reload Reviewer Item Relay/);
});

test('Relay Options distinguishes unconfirmed input from a held button and exposes explicit retry', async () => {
  const ui = loadOptions({ initial: { verificationAssistEnabled: true,
    verificationAssistStatus: { code: 'input-error' },
    verificationAssistLastResult: { outcome: 'input-error', attempt: 2, key: '11:1000',
      holdMs: 0, pressSent: false, pressConfirmed: false, errorStage: 'move', errorReason: 'timeout' },
    verificationAssistRetry: { key: '11:1000', attempt: 2, terminal: true, nextAt: 0 } } });
  await ui.api.renderStatus();
  const text = ui.elements.verificationAssistStatus.textContent;
  assert.match(text, /No press was sent/);
  assert.match(text, /moving.*pointer/i);
  assert.match(text, /timed out/i);
  assert.doesNotMatch(text, /Hold:/);
  assert.equal(ui.elements.retryVerificationAssist.hidden, false);
  const before = JSON.stringify(ui.local.data);
  await ui.elements.retryVerificationAssist.listeners.get('click')();
  assert.equal(ui.optionsMessages.filter(msg => msg.type === 'reviewer:verification-retry').length, 1);
  assert.equal(JSON.stringify(ui.local.data), before, 'Options must not reset reservations directly');
});

test('only the extension Options context can request a manual verification retry', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  let calls = 0;
  bg.api.verificationAutomation.retryCurrentCheck = async () => { calls += 1; return { ok: true }; };
  assert.equal(await workerMessage(bg, { type: 'reviewer:verification-retry' },
    { tab: REVIEW_TAB }).then(r => r.ok), false);
  assert.equal(calls, 0);
  assert.equal((await workerMessage(bg, { type: 'reviewer:verification-retry' },
    { url: 'chrome-extension://different/options.html' })).ok, false);
  assert.equal((await workerMessage(bg, { type: 'reviewer:verification-retry' },
    { id: 'different', url: 'chrome-extension://fixture/options.html' })).ok, false);
  assert.equal((await workerMessage(bg, { type: 'reviewer:verification-retry' },
    { id: 'fixture', url: 'chrome-extension://fixture/options.html',
      tab: { id: 432, url: 'chrome-extension://fixture/options.html' } })).ok, true);
  assert.equal(calls, 1);
});

test('a rejected manual retry leaves its button usable for a later explicit click', async () => {
  const ui = loadOptions({ retryResponse: { ok: false, reason: 'busy' }, initial: {
    verificationAssistEnabled: true, verificationAssistStatus: { code: 'input-error' },
    verificationAssistLastResult: { outcome: 'input-error', attempt: 2, key: '11:1000' },
    verificationAssistRetry: { key: '11:1000', attempt: 2, terminal: true } } });
  await ui.api.renderStatus();
  await ui.elements.retryVerificationAssist.listeners.get('click')();
  assert.equal(ui.elements.retryVerificationAssist.disabled, false);
  assert.equal(ui.local.data.verificationAssistRetry.terminal, true);
});

test('Relay Options explains retry count, next attempt and the release reason', async () => {
  const ui = loadOptions({ initial: { verificationAssistEnabled: true,
    verificationAssistStatus: { code: 'retry-pending' },
    verificationAssistLastResult: { outcome: 'needs-attention', attempt: 2,
      holdMs: 18000, releaseReason: 'stalled' },
    verificationAssistRetry: { nextAt: Date.now() + 15000 } } });
  await new Promise(resolve => setImmediate(resolve));
  const status = ui.elements.verificationAssistStatus.textContent;
  assert.match(status, /another attempt is scheduled/);
  assert.match(status, /Attempt 2 of 10/);
  assert.match(status, /Next attempt in about/);
  assert.match(status, /Progress stopped/);
  assert.match(status, /18\.00 seconds/);
});

test('worker startup attempts an existing verification pause through the built-in controller', async () => {
  const probes = new Map([[11, { ready: true, reviewerReady: false, botCheck: true }]]);
  let attempts = 0;
  const bg = loadBackground({ tabs: [{ ...REVIEW_TAB }], probes,
    localArea: storageArea({ verificationAssistEnabled: true, botCheck: {
      pausedUntil: Date.now() + 3600000, lastAt: Date.now() - 10000, count: 1,
      tabId: 11, challengedTabIds: [11], knownTabIds: [11]
    } }),
    automation: { open: async () => ({}), run: async () => {
      attempts++;
      probes.set(11, { ready: true, reviewerReady: true, botCheck: false });
      return { outcome: 'verified', holdMs: 2250, releaseReason: 'feedback' };
    } }
  });
  await bg.api.initialization;
  await bg.api.verificationAutomation.runPending();
  assert.equal(attempts, 1);
  assert.equal(bg.local.data.verificationAssistLastResult.outcome, 'verified');
  assert.equal(await bg.api.pausedUntil(), 0);
  assert.equal((await bg.api.readCoordinator()).tabId, 11);
});

test('enabling built-in verification checks an already visible pause without another page message', async () => {
  let attempts = 0;
  const bg = loadBackground({ tabs: [{ ...REVIEW_TAB }],
    localArea: storageArea({ botCheck: {
      pausedUntil: Date.now() + 3600000, lastAt: Date.now() - 10000, count: 1,
      tabId: 11, challengedTabIds: [11], knownTabIds: [11]
    } }), automation: { open: async () => ({}), run: async () => {
      attempts++; return { outcome: 'needs-attention', holdMs: 15000, releaseReason: 'deadline' };
    } }
  });
  await bg.api.initialization;
  assert.equal(attempts, 0);
  await bg.local.set({ verificationAssistEnabled: true });
  bg.chrome.storage.onChanged.listeners[0]({ verificationAssistEnabled: { newValue: true } }, 'local');
  await bg.api.verificationAutomation.runPending();
  assert.equal(attempts, 1);
  assert.equal(bg.local.data.verificationAssistStatus.code, 'retry-pending');
  assert.ok(await bg.api.pausedUntil());
});

test('redirect capture retries after loading completes without duplicating the challenge or alert', async () => {
  const tab = { ...REVIEW_TAB, active: true, status: 'loading', url: 'https://www.walmart.com/blocked' };
  const bg = loadBackground({ tabs: [tab] });
  await bg.api.initialization;
  let captures = 0;
  const downloads = [];
  bg.chrome.tabs.captureVisibleTab = async () => { captures += 1; return 'data:image/jpeg;base64,/9j/AAAA'; };
  bg.chrome.downloads = { async download(options) { downloads.push(options); return downloads.length; } };
  await bg.api.handleBotCheck(11);
  assert.equal(captures, 0);
  tab.status = 'complete';
  bg.chrome.tabs.onUpdated.listeners[0](11, { status: 'complete' }, tab);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(captures, 1);
  assert.equal(downloads.length, 2);
  assert.equal(bg.local.data.lastVerificationCapture.status, 'saved');
  assert.equal(bg.local.data.botCheck.count, 1);
  assert.equal(bg.fetchCalls.filter(init => init.body?.includes('paused_minutes')).length, 1);
  await bg.api.handleBotCheck(11);
  assert.equal(captures, 1);
});

test('only the Walmart /blocked page counts as a bot check', ({ assert: _ }) => {
  const { api } = pausedBackground();
  assert.equal(api.isBotCheckUrl(
    'https://www.walmart.com/blocked?url=L3Jldmlld3M&uuid=x'), true);
  assert.equal(api.isBotCheckUrl('https://www.walmart.com/blocked'), true);
  assert.equal(api.isBotCheckUrl('https://www.walmart.com/reviews/claim-product'), false);
  assert.equal(api.isBotCheckUrl('https://www.walmart.com/blocked-items'), false);
  assert.equal(api.isBotCheckUrl('https://evil.example/blocked'), false);
  assert.equal(api.isBotCheckUrl('not a url'), false);
});

test('a bot check pauses for an hour, stands tabs down and alerts once', async () => {
  const bg = pausedBackground();
  await bg.api.selectPrimary();
  await bg.local.set({ 'sweep:11': { total: 24, visited: [1, 2, 3] } });

  const before = Date.now();
  const result = await bg.api.handleBotCheck(11);
  assert.equal(result.minutes, 60);
  assert.ok(result.pausedUntil >= before + 60 * 60_000);

  // No tab may sweep, and the half-finished walk is discarded so solving the
  // check does not resume it at page 20.
  assert.equal((await bg.api.readCoordinator()).tabId, null);
  assert.equal(bg.local.data['sweep:11'], undefined);
  assert.ok(bg.roleMessages.some((m) => m.id === 11 && m.message.active === false));

  // The next alarm fires when the pause ends, not on the refresh interval.
  // Compared field by field: objects built inside the vm context have a
  // different Object prototype, which deepStrictEqual treats as unequal.
  const alarm = bg.alarmCalls.at(-1);
  assert.equal(alarm.name, 'refresh');
  assert.equal(alarm.info.when, result.pausedUntil);

  // One alert to the notifier.
  const alerts = bg.fetchCalls.filter((init) => init.body?.includes('paused_minutes'));
  assert.equal(alerts.length, 1);
  assert.equal(JSON.parse(alerts[0].body).paused_minutes, 60);

  // The same block fires several onUpdated events; they count once.
  const again = await bg.api.handleBotCheck(11);
  assert.equal(again.duplicate, true);
  assert.equal(bg.fetchCalls.filter((i) => i.body?.includes('paused_minutes')).length, 1);
});

test('repeat checks within a day double the pause, capped at a day', async () => {
  const bg = pausedBackground();
  const now = Date.now();
  // A previous check 10 minutes ago whose pause has already been cleared.
  await bg.local.set({ botCheck: { pausedUntil: 0, count: 1, lastAt: now - 10 * 60_000 } });
  assert.equal((await bg.api.handleBotCheck(11)).minutes, 120);

  await bg.local.set({ botCheck: { pausedUntil: 0, count: 9, lastAt: now - 10 * 60_000 } });
  assert.equal((await bg.api.handleBotCheck(11)).minutes, 24 * 60);

  // More than a day since the last one: start over at an hour.
  await bg.local.set({ botCheck: { pausedUntil: 0, count: 5, lastAt: now - 25 * 3600_000 } });
  assert.equal((await bg.api.handleBotCheck(11)).minutes, 60);
});

test('while paused nothing is selected, restarted or reloaded', async () => {
  const bg = pausedBackground();
  await bg.local.set({
    refreshMinutes: 3,
    botCheck: { pausedUntil: Date.now() + 3600_000, count: 1, lastAt: Date.now() }
  });

  assert.equal((await bg.api.selectPrimary()).tab, null);
  const scheduled = await bg.api.restartPrimary('scheduled');
  assert.equal(scheduled.paused, true);
  await bg.api.refreshTick();
  assert.deepEqual(bg.tabActions, []);

  // Changing the refresh interval cannot pull the next alarm inside the pause.
  await bg.api.scheduleRefresh();
  assert.ok(bg.alarmCalls.at(-1).info.when > Date.now());
});

test('Restart sweep cannot override an unresolved challenge', async () => {
  const bg = pausedBackground();
  await bg.local.set({
    botCheck: { pausedUntil: Date.now() + 3600_000, count: 2, lastAt: Date.now(),
      tabId: 11, challengedTabIds: [11], knownTabIds: [11] }
  });

  const result = await bg.api.restartPrimary('manual');
  assert.equal(result.paused, true);
  assert.ok(await bg.api.pausedUntil());
  assert.equal(bg.tabActions.length, 0);
  assert.equal(bg.local.data.botCheck.count, 2);
});

test('an unresolved challenge stays paused after cooldown expiry and worker recreation', async () => {
  const tab = { ...REVIEW_TAB, url: 'https://www.walmart.com/blocked' };
  const now = Date.now();
  const local = storageArea({ botCheck: { pausedUntil: now - 60_000, count: 3,
    lastAt: now - 3600_000, tabId: 11, challengedTabIds: [11], knownTabIds: [11] } });
  const bg = loadBackground({ tabs: [tab], localArea: local });
  await bg.api.initialization;
  assert.ok(await bg.api.pausedUntil() > now);
  await bg.api.refreshTick();
  assert.equal((await bg.api.selectPrimary()).state.tabId, null);
  assert.equal((await bg.api.restartPrimary('manual')).paused, true);
  assert.deepEqual(bg.tabActions, []);
  assert.equal(bg.badgeCalls.at(-1).text, '!');
  const repeat = await bg.api.handleBotCheck(11);
  assert.equal(repeat.duplicate, true);
  assert.equal(local.data.botCheck.count, 3);
});

test('all challenged tabs must recover even after cooldown expiry', async () => {
  const tabs = [{ ...REVIEW_TAB }, { ...REVIEW_TAB, id: 22, index: 1 }];
  const probes = new Map([[11, { ready: true, reviewerReady: true }],
    [22, { ready: true, reviewerReady: false, botCheck: true }]]);
  const bg = loadBackground({ tabs, probes, localArea: storageArea({ botCheck: {
    pausedUntil: Date.now() - 60_000, lastAt: Date.now() - 3600_000, count: 2,
    challengedTabIds: [11, 22], knownTabIds: [11, 22], tabId: 11 } }) });
  await bg.api.initialization;
  assert.equal((await bg.api.selectPrimary()).state.tabId, null);
  probes.set(22, { ready: true, reviewerReady: true });
  assert.notEqual((await bg.api.selectPrimary()).state.tabId, null);
  assert.equal(await bg.api.pausedUntil(), 0);
  assert.equal(bg.local.data.botCheck.count, 2);
});

test('a challenge on an ordinary Walmart page pauses the session until its own probe clears', async () => {
  const other = { ...REVIEW_TAB, id: 22, url: 'https://www.walmart.com/ip/example/123' };
  const tabs = [{ ...REVIEW_TAB }, other];
  const probes = new Map([[11, { ready: true, reviewerReady: true }], [22, { challenge: true }]]);
  const bg = loadBackground({ tabs, probes });
  await bg.api.initialization;
  const first = await workerMessage(bg, { type: 'reviewer:verification', reason: 'text' }, { tab: other });
  assert.equal(first.ok, true);
  assert.equal((await bg.api.selectPrimary()).state.tabId, null);
  probes.set(22, new Error('no receiver'));
  assert.equal((await bg.api.selectPrimary()).state.tabId, null);
  probes.set(22, { challenge: false });
  assert.equal((await bg.api.selectPrimary()).state.tabId, 11);
  assert.equal(await bg.api.pausedUntil(), 0);
});

test('recovery discovers another blocked tab before its content message arrives', async () => {
  const tabs = [{ ...REVIEW_TAB }, { ...REVIEW_TAB, id: 22, url: 'https://www.walmart.com/blocked' }];
  const probes = new Map([[11, { ready: true, reviewerReady: true }]]);
  const bg = loadBackground({ tabs, probes, localArea: storageArea({ botCheck: {
    pausedUntil: Date.now() + 60_000, lastAt: Date.now(), count: 1,
    tabId: 11, challengedTabIds: [11], knownTabIds: [11] } }) });
  await bg.api.initialization;
  assert.equal((await bg.api.selectPrimary()).state.tabId, null);
  assert.ok(bg.local.data.botCheck.challengedTabIds.includes(22));
  tabs[1].url = REVIEW_TAB.url;
  probes.set(22, { ready: true, reviewerReady: true });
  assert.notEqual((await bg.api.selectPrimary()).state.tabId, null);
  assert.equal(await bg.api.pausedUntil(), 0);
  assert.equal(bg.local.data.botCheck.count, 1);
});

test('detection stops every Walmart tab before pause persistence or coordinator work completes', async () => {
  const bg = loadBackground({ tabs: [{ ...REVIEW_TAB },
    { ...REVIEW_TAB, id: 22, url: 'https://www.walmart.com/reviews/claim-product?q=Pet' },
    { ...REVIEW_TAB, id: 33, url: 'https://example.org' }] });
  await bg.api.initialization;
  let releaseRead;
  const blocked = new Promise(resolve => { releaseRead = resolve; });
  const originalGet = bg.local.get;
  bg.local.get = async keys => {
    if (Array.isArray(keys) && keys.includes('botCheck')) await blocked;
    return originalGet(keys);
  };
  const detection = bg.api.handleBotCheck(11);
  await new Promise(resolve => setImmediate(resolve));
  const stopped = [...bg.pauseMessages.map(message => message.id)].sort((a, b) => a - b);
  const pauseWhileStorageBlocked = await bg.api.pausedUntil();
  releaseRead();
  await detection;
  assert.deepEqual(stopped, [11, 22]);
  assert.ok(pauseWhileStorageBlocked > Date.now());
});

for (const signal of ['message', 'storage']) test(`shared ${signal} pause cancels navigation and rejects late active roles`, async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  let navigations = 0;
  content.api.queueWhenIdle(() => { navigations++; }, 1000);
  const pending = content.timerCallbacks.get(content.api.getActionTimer()).callback;
  if (signal === 'message') await contentMessage(content, { type: 'reviewer:pause' });
  else for (const listener of content.chrome.storage.onChanged.listeners) {
    listener({ botCheck: { newValue: { pausedUntil: Date.now() + 3600_000 } } }, 'local');
  }
  await contentMessage(content, { type: 'reviewer:set-role', active: true,
    tabId: 1, primaryTabId: 1, generation: 10 });
  pending();
  await content.api.relay();
  assert.equal(navigations, 0);
  assert.equal(content.api.getActionTimer(), null);
  assert.equal(content.api.getPhase(), 'waiting-verification');
  assert.equal(content.sentMessages.some(message => message.type === 'reviewer:page'), false);
});

test('a fresh scanner document honors a stored unresolved pause even when its deadline is past', async () => {
  const content = loadContent({ localArea: storageArea({ botCheck: { pausedUntil: Date.now() - 60_000 } }) });
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  await new Promise(resolve => setImmediate(resolve));
  await content.api.relay();
  assert.equal(content.api.getPhase(), 'waiting-verification');
  assert.equal(content.sentMessages.some(message => message.type === 'reviewer:page'), false);
});

test('confirmed shared recovery requests a fresh role and restarts scanning without a restart click', async () => {
  const content = loadContent();
  await contentMessage(content, { type: 'reviewer:pause' });
  content.api.setRoleRequestInFlight(false);
  content.chrome.runtime.sendMessage = async message => {
    content.sentMessages.push(message);
    return { active: true, tabId: 1, primaryTabId: 1, generation: 10 };
  };
  for (const listener of content.chrome.storage.onChanged.listeners) {
    listener({ botCheck: { newValue: { pausedUntil: 0 } } }, 'local');
  }
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(content.api.role.active, true);
  assert.equal(content.api.getPhase(), 'starting');
  assert.ok([...content.timerCallbacks.values()].some(timer => timer.ms === 0));
});

test('another detection during recovery probing cannot clear the pause or promote a scanner', async () => {
  let entered;
  let finishProbe;
  const probed = new Promise(resolve => { entered = resolve; });
  const deferred = new Promise(resolve => { finishProbe = resolve; });
  const tabs = [{ ...REVIEW_TAB }, { ...REVIEW_TAB, id: 22 }];
  const probes = new Map([[11, { ready: true, reviewerReady: false }],
    [22, { ready: true, reviewerReady: false }]]);
  const bg = loadBackground({ tabs, probes, localArea: storageArea({ botCheck: {
    pausedUntil: Date.now() + 60_000, count: 1, lastAt: Date.now(),
    tabId: 11, challengedTabIds: [11], knownTabIds: [11, 22] } }) });
  await bg.api.initialization;
  probes.set(11, () => { entered(); return deferred; });
  const selecting = bg.api.selectPrimary();
  await probed;
  const detection = bg.api.handleBotCheck(22);
  finishProbe({ ready: true, reviewerReady: true });
  await detection;
  assert.equal((await selecting).state.tabId, null);
  assert.ok(await bg.api.pausedUntil());
  assert.ok(bg.local.data.botCheck.challengedTabIds.includes(22));
});

test('verification resumes automatically only once the challenged reviewer page is usable', async () => {
  const tabs = [{ ...REVIEW_TAB }];
  const probes = new Map([[11, { ready: true, reviewerReady: false }]]);
  const bg = loadBackground({ tabs, probes });
  await bg.local.set({ botCheck: {
    pausedUntil: Date.now() + 3600_000, count: 2, lastAt: Date.now(),
    tabId: 11, knownTabIds: [11]
  }, lastError: 'Walmart showed a bot check' });
  assert.equal((await bg.api.selectPrimary()).state.tabId, null);
  probes.set(11, { ready: true, reviewerReady: true, active: false });
  assert.equal((await bg.api.selectPrimary()).state.tabId, 11);
  assert.equal(await bg.api.pausedUntil(), 0);
  assert.equal(bg.local.data.botCheck.count, 2);
  assert.ok(bg.local.data.botCheck.verifiedAt > 0);
  assert.equal(bg.local.data.lastError, '');
  assert.equal(bg.tabActions.length, 0);
});

test('clearing verification removes old completion timing before a fresh scan can start', async () => {
  const bg = loadBackground({ tabs: [{ ...REVIEW_TAB }],
    probes: new Map([[11, { ready: true, reviewerReady: true }]]) });
  await bg.api.initialization;
  const now = Date.now();
  await bg.local.set({ refreshMinutes: 5,
    lastSweepStartedAt: now + 60 * 60_000, lastSweepDone: now + 90 * 60_000,
    lastSweepPages: 19,
    botCheck: { pausedUntil: now + 120 * 60_000, count: 5, lastAt: now,
      tabId: 11, challengedTabIds: [11], knownTabIds: [11] }
  });
  const selected = await bg.api.selectPrimary();
  assert.equal(selected.state.tabId, 11);
  assert.equal(await bg.api.pausedUntil(), 0);
  assert.equal(bg.local.data.lastSweepStartedAt, undefined);
  assert.equal(bg.local.data.lastSweepDone, undefined);
  assert.equal(bg.local.data.lastSweepPages, undefined);
  assert.equal(bg.local.data.refreshMinutes, 5);
  const alarm = bg.alarmCalls.filter(call => call.name === 'refresh').at(-1);
  assert.ok(alarm.info.delayInMinutes >= 4 && alarm.info.delayInMinutes <= 6);
  assert.equal(alarm.info.when, undefined);
});

test('an existing passive fallback cannot cancel verification, but a reopened reviewer tab can', async () => {
  const tabs = [{ ...REVIEW_TAB, id: 22 }];
  const probes = new Map([[22, { ready: true, reviewerReady: true }]]);
  const bg = loadBackground({ tabs, probes });
  await bg.local.set({ botCheck: {
    pausedUntil: Date.now() + 3600_000, count: 1, lastAt: Date.now(),
    tabId: 11, knownTabIds: [11, 22]
  } });
  assert.equal((await bg.api.selectPrimary()).state.tabId, null);
  tabs.push({ ...REVIEW_TAB, id: 33 });
  probes.set(33, { ready: true, reviewerReady: true });
  assert.notEqual((await bg.api.selectPrimary()).state.tabId, null);
  assert.equal(await bg.api.pausedUntil(), 0);
});

test('a new usable tab cannot clear an outstanding challenge in another open tab', async () => {
  const tabs = [{ ...REVIEW_TAB }, { ...REVIEW_TAB, id: 22, index: 1 }];
  const probes = new Map([
    [11, { ready: true, reviewerReady: false, botCheck: true }],
    [22, { ready: true, reviewerReady: true }]
  ]);
  const bg = loadBackground({ tabs, probes });
  await bg.api.initialization;
  await bg.local.set({ botCheck: {
    pausedUntil: Date.now() + 3600_000, count: 8, lastAt: Date.now(),
    tabId: 11, challengedTabIds: [11], knownTabIds: [11]
  } });
  assert.equal((await bg.api.selectPrimary()).state.tabId, null);
  assert.ok(await bg.api.pausedUntil());
  assert.equal(bg.local.data.botCheck.count, 8);
  assert.equal(bg.fetchCalls.length, 0);
  // Closing the old challenge allows a genuinely usable reopened page to resume.
  tabs.splice(0, 1);
  assert.equal((await bg.api.selectPrimary()).state.tabId, 22);
  assert.equal(await bg.api.pausedUntil(), 0);
  assert.equal(bg.local.data.botCheck.count, 8);
});

test('a blocked redirect without a content-script reply keeps verification paused', async () => {
  const tabs = [{ ...REVIEW_TAB, url: 'https://www.walmart.com/blocked' },
    { ...REVIEW_TAB, id: 22, index: 1 }];
  const bg = loadBackground({ tabs, probes: new Map([
    [22, { ready: true, reviewerReady: true }]
  ]) });
  await bg.api.initialization;
  await bg.local.set({ botCheck: {
    pausedUntil: Date.now() + 3600_000, count: 8, lastAt: Date.now(),
    tabId: 11, challengedTabIds: [11], knownTabIds: [11]
  } });
  assert.equal((await bg.api.selectPrimary()).state.tabId, null);
  assert.ok(await bg.api.pausedUntil());
  assert.equal(bg.local.data.botCheck.count, 8);
  assert.equal(bg.fetchCalls.length, 0);
});

test('upgrading a legacy pause does not mistake existing fallbacks for reopened tabs', async () => {
  const tabs = [{ ...REVIEW_TAB, id: 22 }];
  const probes = new Map([[22, { ready: true, reviewerReady: true }]]);
  const bg = loadBackground({ tabs, probes });
  await bg.api.initialization;
  await bg.local.set({ botCheck: {
    pausedUntil: Date.now() + 3600_000, count: 1, lastAt: Date.now(), tabId: 11
  } });
  assert.equal((await bg.api.selectPrimary()).state.tabId, null);
  assert.ok(await bg.api.pausedUntil());
  assert.ok(bg.local.data.botCheck.knownTabIds.includes(22));
  tabs.push({ ...REVIEW_TAB, id: 33 });
  probes.set(33, { ready: true, reviewerReady: true });
  assert.notEqual((await bg.api.selectPrimary()).state.tabId, null);
});

test('solving a second challenged tab resumes without duplicating the outage alert', async () => {
  const tabs = [{ ...REVIEW_TAB, id: 22 }];
  const probes = new Map([[22, { ready: true, reviewerReady: true }]]);
  const bg = loadBackground({ tabs, probes });
  await bg.api.initialization;
  await bg.local.set({ botCheck: {
    pausedUntil: Date.now() + 3600_000, count: 1, lastAt: Date.now(),
    tabId: 11, knownTabIds: [11, 22]
  } });
  assert.equal((await bg.api.handleBotCheck(22)).duplicate, true);
  assert.equal((await bg.api.selectPrimary()).state.tabId, 22);
  assert.equal(await bg.api.pausedUntil(), 0);
  assert.equal(bg.fetchCalls.length, 0);
});

test('a challenge arriving during a tab probe cannot promote or authorize that tab', async () => {
  let finishProbe;
  let entered;
  const probed = new Promise(resolve => { entered = resolve; });
  const deferred = new Promise(resolve => { finishProbe = resolve; });
  const bg = loadBackground({ tabs: [{ ...REVIEW_TAB, id: 22 }],
    probes: new Map([[22, () => { entered(); return deferred; }]]) });
  await bg.api.initialization;
  const selection = bg.api.selectPrimary();
  await probed;
  await bg.api.handleBotCheck(11);
  finishProbe({ ready: true, active: false });
  const selected = await selection;
  assert.equal(selected.state.tabId, null);
  const state = await bg.api.readCoordinator();
  const report = await bg.api.handlePageReport(
    { generation: state.generation, page: 1, items: [{ item_id: 'paused' }] },
    { tab: { id: 22 } }
  );
  assert.equal(report.ok, false);
  assert.equal(bg.fetchCalls.filter(call => JSON.parse(call.body).items).length, 0);
});

test('a challenge cancels queued reports and later batches while an issued request may finish', async () => {
  let finishDelivery;
  let startedDelivery;
  const started = new Promise(resolve => { startedDelivery = resolve; });
  const delivery = new Promise(resolve => { finishDelivery = resolve; });
  const bg = loadBackground({ fetchImpl(url, init) {
    if (new URL(url).pathname !== '/ingest') return jsonResponse({ ok: true });
    startedDelivery();
    return delivery;
  } });
  await bg.api.initialization;
  bg.session.data.reviewerCoordinator = { tabId: 11, generation: 1, selectedAt: 1 };
  const report = (items) => bg.api.handlePageReport(
    { generation: 1, page: 1, items }, { tab: { id: 11 } }
  );
  const first = report(Array.from({ length: 401 }, (_, index) => ({ item_id: `batch-${index}` })));
  await started;
  const queued = report([{ item_id: 'queued' }]);
  // Let its pre-queue authorization complete before the challenge begins.
  await new Promise(resolve => setImmediate(resolve));
  await bg.api.handleBotCheck(11);
  finishDelivery(jsonResponse({ seen: 200, new: 0, duplicates: 200, failed: 0, pending: 0 }));
  const results = await Promise.all([first, queued]);
  assert.ok(results.every(result => result.ok === false));
  assert.equal(bg.fetchCalls.filter(call => JSON.parse(call.body).items).length, 1);
  assert.match(bg.local.data.lastError, /bot check/);
});

test('repeated events on the unresolved challenge do not escalate the same outage', async () => {
  const bg = pausedBackground();
  await bg.local.set({ botCheck: {
    pausedUntil: Date.now() + 3600_000, count: 1,
    lastAt: Date.now() - 10 * 60_000, tabId: 11
  } });
  const result = await bg.api.handleBotCheck(11);
  assert.equal(result.duplicate, true);
  assert.equal(bg.local.data.botCheck.count, 1);
  assert.equal(bg.fetchCalls.length, 0);
});

test('new installs refresh every half hour while an explicit zero stays disabled', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  await bg.api.scheduleRefresh();
  const refresh = bg.alarmCalls.filter((alarm) => alarm.name === 'refresh').at(-1);
  assert.ok(refresh.info.delayInMinutes >= 24 && refresh.info.delayInMinutes <= 36);
  await bg.local.set({ refreshMinutes: 0 });
  const before = bg.alarmCalls.length;
  await bg.api.scheduleRefresh();
  assert.equal(bg.alarmCalls.length, before);
});

test('worker startup recreates missing alarms while preserving an existing refresh', async () => {
  const existing = { when: Date.now() + 10 * 60_000 };
  const bg = loadBackground({ alarms: { refresh: existing } });
  await bg.api.initialization;
  assert.equal(bg.alarmCalls.filter((a) => a.name === 'refresh').length, 0);
  assert.equal(bg.alarmCalls.find((a) => a.name === 'relayStatus').info.periodInMinutes, 1);
});

test('browser heartbeat describes disabled refreshing without relaying any items', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  await bg.local.set({ refreshMinutes: 0, lastSweepPages: 24, lastSweepDone: Date.now() });
  await bg.api.reportBrowserStatus();
  const report = JSON.parse(bg.fetchCalls.at(-1).body);
  assert.equal(report.phase, 'unreachable');
  assert.equal(report.refresh_minutes, 0);
  assert.equal(report.tab_count, 0);
  assert.equal(report.last_sweep_pages, 24);
  assert.equal('items' in report, false);
});

test('browser heartbeat retains page counts across all 26 letters', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  await bg.local.set({ lastSweepPages: 520, lastSweepDone: Date.now() });
  await bg.api.reportBrowserStatus();
  assert.equal(JSON.parse(bg.fetchCalls.at(-1).body).last_sweep_pages, 520);
});

test('browser heartbeat retains page counts across all 62 high value keywords', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  await bg.local.set({ lastSweepPages: 6200, lastSweepDone: Date.now() });
  await bg.api.reportBrowserStatus();
  assert.equal(JSON.parse(bg.fetchCalls.at(-1).body).last_sweep_pages, 6200);
});

test('page pacing keeps the saved delay after repeated verification challenges', async () => {
  const content = loadContent();
  const initial = await content.api.pageDelayMs();
  assert.ok(initial >= 11_250 && initial <= 18_750);
  await content.local.set({ pageDelaySeconds: 5,
    botCheck: { count: 3, lastAt: Date.now() } });
  const afterCheck = await content.api.pageDelayMs();
  assert.ok(afterCheck >= 3_750 && afterCheck <= 6_250);
});

test('negative page jitter never shortens the saved minimum', async () => {
  const content = loadContent();
  content.api.setRandom(0);
  for (const seconds of [5, 15, 60]) {
    await content.local.set({ pageDelaySeconds: seconds, refreshMinutes: 0 });
    assert.equal(await content.api.pageDelayMs(), seconds * 1_000);
  }
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  await content.local.set({ refreshMinutes: 10, pageDelaySeconds: 15,
    'sweep:1': { generation: 9, mode: 'none', total: 17, visited: [1] } });
  const spread = await content.api.pageDelayMs();
  assert.ok(spread >= 15_000 && spread < 600_000 * 0.75 / 17);
});

test('regular mode spreads seventeen pages across the saved ten-minute interval', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  await content.local.set({ refreshMinutes: 10, pageDelaySeconds: 15,
    'sweep:1': { generation: 9, mode: 'none', total: 17, visited: [1] } });
  const delay = await content.api.pageDelayMs();
  assert.ok(delay >= 19_800 && delay <= 33_100, `unexpected delay ${delay}`);
});

test('the first verification challenge leaves saved page spacing intact', async () => {
  const content = loadContent();
  await content.local.set({ refreshMinutes: 0, pageDelaySeconds: 15,
    botCheck: { count: 1, lastAt: Date.now() } });
  const delay = await content.api.pageDelayMs();
  assert.ok(delay >= 11_250 && delay <= 18_750);
});

test('an inline verification overlay cancels pending navigation before it can reload', async () => {
  const content = loadContent();
  Object.assign(content.api.role, { active: true, tabId: 1, generation: 9 });
  const messages = [];
  content.chrome.runtime.sendMessage = async message => { messages.push(message); return { ok: true }; };
  let navigated = 0;
  content.api.queueWhenIdle(() => { navigated += 1; }, 15_000);
  content.body.innerText = "Robot or human? Activate and hold the button to confirm that you're human. PRESS & HOLD";
  await runNavigation(content);
  await Promise.resolve();
  assert.equal(navigated, 0);
  assert.equal(content.api.getPhase(), 'waiting-verification');
  assert.equal(messages.filter(message => message.type === 'reviewer:verification').length, 1);
  await content.api.relay();
  assert.equal(navigated, 0);
});

test('probe never calls cached reviewer cards usable underneath a verification overlay', () => {
  const content = loadContent();
  content.api.setCollector(() => [{ item_id: 'cached' }]);
  content.body.innerText = 'Robot or human? Press & Hold';
  let probe;
  content.chrome.runtime.onMessage.listeners[0]({ type: 'reviewer:probe' }, {}, value => { probe = value; });
  assert.equal(probe.botCheck, true);
  assert.equal(probe.reviewerReady, false);
});

test('completed long sweeps receive a break before the next scheduled restart', async () => {
  const bg = pausedBackground();
  const now = Date.now();
  await bg.local.set({ refreshMinutes: 10, lastSweepStartedAt: now - 20 * 60_000,
    lastSweepDone: now });
  await bg.api.scheduleRefresh(true);
  const alarm = bg.alarmCalls.filter(value => value.name === 'refresh').at(-1);
  assert.ok(alarm.info.when >= now + 2 * 60_000);
});

test('completion pacing uses the saved refresh interval after verification clears', async () => {
  const bg = pausedBackground();
  await bg.api.initialization;
  const now = Date.now();
  await bg.local.set({ refreshMinutes: 10, lastSweepStartedAt: now - 5 * 60_000,
    lastSweepDone: now, botCheck: { count: 1, lastAt: now, pausedUntil: 0 } });
  await bg.api.scheduleRefresh();
  const alarm = bg.alarmCalls.filter(value => value.name === 'refresh').at(-1);
  assert.ok(alarm.info.when >= now + 5 * 60_000);
  assert.ok(alarm.info.when <= now + 6 * 60_000);
  assert.equal(bg.local.data.refreshMinutes, 10);
  await bg.local.set({ refreshMinutes: 0 });
  const count = bg.alarmCalls.length;
  await bg.api.scheduleRefresh();
  assert.equal(bg.alarmCalls.length, count);
});

test('a refresh alarm queued before disabling auto-refresh cannot restart or repair a tab', async () => {
  const bg = pausedBackground();
  await bg.local.set({ refreshMinutes: 0 });
  await bg.api.refreshTick();
  assert.equal(bg.tabActions.length, 0);
});

test('unusable-page recovery backs off and successful relays reset the retry budget', async () => {
  const bg = loadBackground({ tabs: [REVIEW_TAB],
    probes: new Map([[11, { ready: true, active: true, generation: 0 }]]), summary: { seen: 1 } });
  const selected = await bg.api.selectPrimary();
  const sender = { tab: REVIEW_TAB };
  const message = { type: 'reviewer:unusable', generation: selected.state.generation };
  const first = await workerMessage(bg, message, sender);
  const second = await workerMessage(bg, message, sender);
  assert.equal(first.retryAfterMs, 15_000);
  assert.equal(second.retryAfterMs, 30_000);
  for (let index = 0; index < 8; index += 1) await workerMessage(bg, message, sender);
  assert.equal((await workerMessage(bg, message, sender)).retryAfterMs, 300_000);
  const delivered = await bg.api.handlePageReport({ generation: selected.state.generation, page: 1,
    items: [{ item_id: 'recovery-reset' }] }, sender);
  assert.equal(delivered.ok, true);
  assert.equal((await workerMessage(bg, message, sender)).retryAfterMs, 15_000);
});

test('an authorized failed scan clears its impossible progress and requests a delayed page-one restart', async () => {
  const bg = loadBackground({ tabs: [REVIEW_TAB],
    probes: new Map([[11, { ready: true, active: true, generation: 0 }]]) });
  await bg.api.initialization;
  const selected = await bg.api.selectPrimary();
  await bg.local.set({ 'sweep:11': { generation: selected.state.generation,
    total: 24, visited: [1], endRetries: { 2: 3 } },
    lastSweepStartedAt: 100, lastSweepDone: 200, lastSweepPages: 24 });
  const result = await workerMessage(bg, { type: 'reviewer:unusable',
    generation: selected.state.generation, reason: 'Interior page stayed empty', restartSweep: true
  }, { tab: REVIEW_TAB });
  assert.equal(result.recover, 'restart');
  assert.equal(result.retryAfterMs, 15_000);
  assert.equal(result.generation, selected.state.generation);
  assert.equal(result.active, true);
  assert.equal(bg.local.data['sweep:11'], undefined);
  assert.equal(bg.local.data.lastSweepDone, undefined);
  assert.equal(bg.local.data.lastSweepStartedAt, undefined);
  assert.equal(bg.local.data.lastSweepPages, undefined);
  assert.equal(bg.tabActions.length, 0, 'Content must respect user activity before navigating');
});

test('an unauthorized failed-scan restart cannot discard the primary sweep', async () => {
  const bg = loadBackground({ tabs: [REVIEW_TAB],
    probes: new Map([[11, { ready: true, active: true, generation: 0 }]]) });
  await bg.api.initialization;
  const selected = await bg.api.selectPrimary();
  const progress = { generation: selected.state.generation, total: 24, visited: [1] };
  await bg.local.set({ 'sweep:11': progress });
  const result = await workerMessage(bg, { type: 'reviewer:unusable',
    generation: selected.state.generation - 1, restartSweep: true
  }, { tab: REVIEW_TAB });
  assert.equal(result.recover, undefined);
  assert.deepEqual(bg.local.data['sweep:11'], progress);
  assert.equal(bg.session.data['recovery:11'], undefined);
});

test('a delayed loading event cannot restore a coordinator lease revoked by verification', async () => {
  const bg = pausedBackground();
  await bg.api.initialization;
  await bg.api.selectPrimary();
  const originalGet = bg.local.get.bind(bg.local);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let delayed = false;
  bg.local.get = async keys => {
    if (!delayed && Array.isArray(keys) && keys.includes('pathPattern')) {
      delayed = true;
      await gate;
    }
    return originalGet(keys);
  };
  bg.chrome.tabs.onUpdated.listeners[0](11, { status: 'loading' }, REVIEW_TAB);
  await Promise.resolve();
  await bg.api.handleBotCheck(11);
  const revoked = await bg.api.readCoordinator();
  release();
  await new Promise(resolve => setTimeout(resolve, 10));
  const final = await bg.api.readCoordinator();
  assert.equal(final.tabId, null);
  assert.ok(final.generation >= revoked.generation);
});

test('Options has no personal alert filtering or historical rescan controls', () => {
  for (const id of ['minValue', 'maxValue', 'keywords', 'excludeKeywords', 'alertUnknown',
    'matchMode', 'saveRules', 'resetRules', 'priority', 'rescan', 'rescanHistory']) {
    assert.doesNotMatch(optionsHtml, new RegExp(`id="${id}"`), id);
  }
  assert.doesNotMatch(optionsSource, /rulesFetch|rulesUrl|formToRules|loadRules|activeRule/);
});

test('Options startup never requests personal rule or historical rescan endpoints', async () => {
  const ui = loadOptions();
  await new Promise(resolve => setTimeout(resolve, 0));
  await ui.api.renderStatus();
  assert.ok(ui.optionsFetches.some(request => request.path === '/relay-settings'));
  assert.ok(ui.optionsFetches.some(request => request.path === '/health'));
  assert.equal(ui.optionsFetches.some(request => /rules|rescan/.test(request.path)), false);
});

test('Options saves scanning settings and keeps connection credentials out of that payload', async () => {
  const ui = loadOptions({ initial: { token: 'local-secret', refreshMinutes: 30 } });
  await new Promise(resolve => setTimeout(resolve, 0));
  ui.elements.sweepMode.value = 'high-value';
  ui.elements.pageDelaySeconds.value = '25';
  ui.elements.refreshMinutes.value = '0';
  await ui.elements.save.listeners.get('click')();
  const request = ui.optionsFetches.find(entry => entry.method === 'PUT' && entry.path === '/relay-settings');
  const scanner = ui.optionsFetches.find(entry => entry.method === 'PUT' && entry.path === '/scanner-settings');
  assert.ok(scanner);
  assert.equal(scanner.body.includes('local-secret'), false);
  assert.deepEqual(JSON.parse(request.body), {
    schemaVersion: 1, sweepMode: 'high-value', pageDelaySeconds: 25, refreshMinutes: 0
  });
  assert.equal(request.body.includes('local-secret'), false);
  assert.equal(ui.local.data.token, 'local-secret');
  assert.equal(ui.local.data.refreshMinutes, 0);
});

test('Options keeps advanced connection collapsed and script bindings match markup', async () => {
  assert.match(optionsHtml, /<details[^>]*>\s*<summary>Advanced connection<\/summary>/);
  const advanced = optionsHtml.match(/<details[^>]*>[\s\S]*?<\/details>/)[0];
  assert.doesNotMatch(advanced.split('>')[0], /\bopen\b/);
  for (const id of ['endpoint', 'token', 'pathPattern']) assert.match(advanced, new RegExp(`id="${id}"`));
  const { api, elements } = loadOptions();
  await api.renderStatus();
  for (const id of ['save', 'resetSweep', 'checkConnection', 'testNotification', 'exportDiagnostics']) {
    assert.equal(typeof elements[id].listeners.get('click'), 'function', id);
  }
  assert.ok(elements.status.textContent.length > 0);
});

test('site pagination accepts neutral affinity defaults while preserving real filters', () => {
  const content = loadContent();
  addNativePageControl(content, () => {});
  let href = '?page=2&affinityOverride=default';
  const control = { getAttribute(name) { return name === 'href' ? href : null; },
    getClientRects() { return [1]; } };
  content.pager.querySelectorAll = () => [control];
  assert.equal(content.api.findPageControl('?page=2', 2), control);
  href = '?page=2&affinityOverride=default&affinityOverride=default';
  assert.equal(content.api.findPageControl('?page=2', 2), control);
  for (const modifier of ['affinityOverride=store_led',
    'affinityOverride=default&affinityOverride=store_led', 'queryNups=Cat', 'q=Cat']) {
    href = `?page=2&${modifier}`;
    assert.equal(content.api.findPageControl('?page=2', 2), null, modifier);
  }
});

test('site pagination adding default affinity waits for fresh cards without a repair reload', async () => {
  const content = loadContent();
  let cards = [{ item_id: 'old' }];
  let reloads = 0;
  content.api.setCollector(() => cards);
  addNativePageControl(content, () => {
    content.location.href = 'https://www.walmart.com/reviews/claim-product?page=2&affinityOverride=default';
    content.location.search = '?page=2&affinityOverride=default';
  });
  content.location.assign = () => { reloads += 1; };
  await content.api.advanceDeliveredPage(9);
  await runNavigation(content);
  await runNavigation(content);
  assert.equal(content.api.getPhase(), 'waiting-page-change', 'Old cards must remain locked');
  cards = [{ item_id: 'fresh' }];
  await runNavigation(content);
  assert.equal(content.api.getPhase(), 'waiting-content');
  assert.equal(reloads, 0);
});

function addOffscreenPager(content) {
  let scrolls = 0;
  let clicks = 0;
  addNativePageControl(content, () => { clicks += 1; });
  const control = content.pager.querySelectorAll()[0];
  control.getBoundingClientRect = () => ({ top: 1800, bottom: 1830, left: 10, right: 90 });
  control.scrollIntoView = options => {
    scrolls += 1;
    assert.equal(options.block, 'center');
    content.listeners.get('scroll')({ isTrusted: true });
  };
  content.document.visibilityState = 'visible';
  content.pager.innerText = '1 2 19';
  content.api.setNow(100_000);
  content.api.setNavigationAt(94_000);
  content.api.setRandom(0.5);
  content.local.data.refreshMinutes = 5;
  content.local.data.pageDelaySeconds = 15;
  return { control, scrolls: () => scrolls, clicks: () => clicks };
}

function animateOffscreenPager(content) {
  const pager = addOffscreenPager(content);
  const frames = new Map();
  const positions = [];
  let frameId = 0;
  let elapsed = 0;
  content.document.scrollingElement = { scrollHeight: 5_000 };
  content.viewport.performance = { now: () => elapsed };
  content.viewport.requestAnimationFrame = callback => {
    frames.set(++frameId, callback);
    return frameId;
  };
  content.viewport.cancelAnimationFrame = id => frames.delete(id);
  content.viewport.scrollTo = options => {
    assert.equal(options.behavior, 'instant', 'The animation owns easing, including on smooth-scroll pages');
    content.viewport.scrollY = options.top;
    positions.push(options.top);
    content.listeners.get('scroll')?.({ isTrusted: true });
  };
  pager.control.isConnected = true;
  pager.control.getBoundingClientRect = () => ({ top: 1800 - content.viewport.scrollY,
    bottom: 1830 - content.viewport.scrollY, left: 10, right: 90 });
  return { ...pager, frames, positions,
    tick(at) {
      elapsed = at;
      content.api.setNow(100_000 + at);
      const callbacks = [...frames.values()];
      frames.clear();
      for (const callback of callbacks) callback(at);
    },
    finish() {
      while (frames.size && elapsed < 1_000) this.tick(elapsed + 20);
      return elapsed;
    }
  };
}

test('pager motion varies its bounded duration and landing point inside the existing navigation wait', async () => {
  const samples = [];
  for (const random of [0, 0.9]) {
    const content = loadContent();
    const pager = animateOffscreenPager(content);
    content.api.setRandom(random);
    await content.api.advanceDeliveredPage(9);
    const deadline = content.timerCallbacks.get(content.api.getActionTimer()).ms;
    assert.equal(pager.frames.size, 1, 'Animation should run independently of the page action timer');
    const duration = pager.finish();
    assert.ok(duration >= 600 && duration <= 920);
    assert.equal(pager.frames.size, 0);
    assert.ok(pager.positions.length > 20, 'The scroll should ease across multiple frames');
    if (random === 0.9) assert.equal(Math.max(...pager.positions), pager.positions.at(-1),
      'A settling correction should not happen on every scroll');
    const rect = pager.control.getBoundingClientRect();
    assert.ok((rect.top + rect.bottom) / 2 >= 800 * 0.45 - 1);
    assert.ok((rect.top + rect.bottom) / 2 <= 800 * 0.60 + 1);
    assert.equal(content.timerCallbacks.get(content.api.getActionTimer()).ms, deadline);
    content.api.setNow(109_000);
    await runNavigation(content);
    assert.equal(pager.clicks(), 1);
    samples.push({ duration, top: content.viewport.scrollY });
  }
  assert.notEqual(samples[0].duration, samples[1].duration);
  assert.notEqual(samples[0].top, samples[1].top);
});

test('pager motion occasionally settles slightly upward and clamps to document edges', async () => {
  const content = loadContent();
  const pager = animateOffscreenPager(content);
  content.api.setRandom(0);
  await content.api.preparePageNavigation(2, 9, 1_000);
  assert.equal(pager.frames.size, 1);
  pager.finish();
  const peak = Math.max(...pager.positions);
  const settled = pager.positions.at(-1);
  assert.ok(peak > settled, 'A small final correction should sometimes move upward');
  assert.ok(peak - settled <= 18);

  content.viewport.scrollY = 4_000;
  pager.positions.length = 0;
  content.api.setRandom(0.9);
  await content.api.preparePageNavigation(2, 9, 1_000);
  assert.equal(pager.frames.size, 1);
  // Start the second animation at a fresh clock origin.
  for (const at of [700, 900, 1_200, 1_490]) pager.tick(at);
  assert.equal(pager.frames.size, 0);
  assert.ok(pager.positions.every(top => top >= 0 && top <= 4_200));
  const rect = pager.control.getBoundingClientRect();
  assert.ok(rect.top >= 0 && rect.bottom <= 800, 'An above-screen pager must also become visible');

  content.viewport.scrollY = 0;
  content.document.scrollingElement.scrollHeight = 1_900;
  pager.positions.length = 0;
  await content.api.preparePageNavigation(2, 9, 1_000);
  assert.equal(pager.frames.size, 1);
  for (const at of [1_900, 2_200, 2_400]) pager.tick(at);
  assert.equal(pager.frames.size, 0);
  assert.equal(content.viewport.scrollY, 1_100);
  assert.ok(pager.positions.every(top => top >= 0 && top <= 1_100));
});

test('real input immediately cancels pager animation and still postpones navigation', async () => {
  for (const type of ['wheel', 'touchstart', 'pointerdown', 'keydown']) {
    const content = loadContent();
    const pager = animateOffscreenPager(content);
    await content.api.advanceDeliveredPage(9);
    assert.equal(pager.frames.size, 1, type);
    pager.tick(100);
    const before = pager.positions.length;
    content.listeners.get(type)({ isTrusted: true });
    assert.equal(pager.frames.size, 0, type);
    pager.tick(200);
    assert.equal(pager.positions.length, before, type);
    content.api.setNow(109_000);
    await runNavigation(content);
    assert.equal(pager.clicks(), 0, type);
    assert.equal(content.api.getPhase(), 'waiting-user', type);
  }
});

test('pager animation stops for hidden, replaced, invalidated and challenged pages or an expired wait', async () => {
  for (const scenario of ['hidden', 'detached', 'invalidated', 'challenge', 'expired', 'reduced-motion']) {
    const content = loadContent();
    const pager = animateOffscreenPager(content);
    await content.api.advanceDeliveredPage(9);
    assert.equal(pager.frames.size, 1, scenario);
    pager.tick(100);
    const before = pager.positions.length;
    if (scenario === 'hidden') content.document.visibilityState = 'hidden';
    if (scenario === 'detached') pager.control.isConnected = false;
    if (scenario === 'invalidated') {
      content.chrome.runtime.id = undefined;
      content.api.diagnostic('page-loaded');
    }
    if (scenario === 'challenge') {
      content.body.innerText = 'Robot or human? Press & Hold';
      content.api.checkVerification();
    }
    if (scenario === 'reduced-motion') content.viewport.matchMedia = () => ({ matches: true });
    pager.tick(scenario === 'expired' ? 1_100 : 200);
    assert.equal(pager.frames.size, 0, scenario);
    assert.equal(pager.positions.length, before, scenario);
  }
});

test('a failed animation frame cannot interrupt the scheduled scan', async () => {
  const content = loadContent();
  const pager = animateOffscreenPager(content);
  await content.api.advanceDeliveredPage(9);
  assert.equal(pager.frames.size, 1);
  content.viewport.scrollTo = () => { throw new Error('layout replaced'); };
  assert.doesNotThrow(() => pager.tick(100));
  assert.equal(pager.frames.size, 0);
  content.api.setNow(109_000);
  await runNavigation(content);
  assert.equal(pager.clicks(), 1);
});

test('offscreen pager scrolls during the existing wait without delaying navigation or delivery', async () => {
  const content = loadContent();
  const pager = addOffscreenPager(content);
  await content.api.advanceDeliveredPage(9);
  assert.equal(pager.scrolls(), 1);
  assert.equal(pager.clicks(), 0);
  assert.equal(content.timerCallbacks.get(content.api.getActionTimer()).ms, 9_000);
  content.api.setNow(109_000);
  await runNavigation(content);
  assert.equal(pager.clicks(), 1, 'The programmatic scroll must not look like new user activity');
});

test('pager preparation avoids hidden tabs, recent input, cooldowns and short waits', async () => {
  for (const scenario of ['hidden', 'recent-input', 'cooldown', 'short-wait', 'in-view', 'challenge']) {
    const content = loadContent();
    const pager = addOffscreenPager(content);
    if (scenario === 'hidden') content.document.visibilityState = 'hidden';
    if (scenario === 'recent-input') content.api.setLastInteraction(99_000);
    if (scenario === 'cooldown') content.local.data.nativeNavigationPausedUntil = 200_000;
    if (scenario === 'short-wait') content.api.setNavigationAt(85_000);
    if (scenario === 'in-view') pager.control.getBoundingClientRect = () =>
      ({ top: 100, bottom: 130, left: 10, right: 90 });
    if (scenario === 'challenge') content.body.innerText = 'Robot or human? Press & Hold';
    await content.api.advanceDeliveredPage(9);
    assert.equal(pager.scrolls(), 0, scenario);
    assert.equal(pager.clicks(), 0, scenario);
  }
});

test('real wheel input still postpones pagination during the programmatic scroll grace period', async () => {
  const content = loadContent();
  const pager = addOffscreenPager(content);
  await content.api.advanceDeliveredPage(9);
  content.api.setNow(100_500);
  content.listeners.get('wheel')?.({ isTrusted: true });
  content.api.setNow(109_000);
  await runNavigation(content);
  assert.equal(pager.clicks(), 0);
  assert.equal(content.api.getPhase(), 'waiting-user');
});

test('pager scrolling respects reduced motion and a failed scroll cannot cancel the scan', async () => {
  const content = loadContent();
  const pager = addOffscreenPager(content);
  content.viewport.matchMedia = () => ({ matches: true });
  pager.control.scrollIntoView = options => { assert.equal(options.behavior, 'instant'); };
  assert.equal(content.api.scrollPageControlIntoView(pager.control), true);
  pager.control.scrollIntoView = () => { throw new Error('detached control'); };
  await content.api.advanceDeliveredPage(9);
  assert.equal(content.timerCallbacks.get(content.api.getActionTimer()).ms, 9_000);
});

async function releaseAuxiliaryDelay(bg) {
  await bg.api.auxiliaryScans.tick();
  const pending = (await bg.api.auxiliaryScans.status()).lanes
    .filter(lane => lane.status === 'waiting' && lane.nextAt > 0)
    .sort((a, b) => a.nextAt - b.nextAt)[0];
  if (pending) {
    bg.api.setNow(pending.nextAt);
    await bg.api.auxiliaryScans.tick();
  }
}

async function auxiliaryBackgroundFixture({ department = false, primary = true, automation = null } = {}) {
  const home = 'https://www.walmart.com/reviews/claim-product';
  const tabs = primary ? [{ id: 11, windowId: 1, index: 4, status: 'complete', url: home }] : [];
  const probes = new Map(tabs.map(tab => [tab.id, { ready: true, reviewerReady: true }]));
  const bg = loadBackground({ tabs, probes, automation, summary: { seen: 1, new: 1, duplicates: 0,
    filtered: 0, matched: 1, notified: 0, queued: 1, failed: 0, pending: 0 } });
  await bg.api.initialization;
  await bg.local.set(department ? { auxiliaryDepartments: true } : {
    auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet']
  });
  await releaseAuxiliaryDelay(bg);
  const lane = (await workerMessage(bg, { type: 'reviewer:aux-status' })).lanes[0];
  const tab = tabs.find(entry => entry.id === lane.tabId);
  const navigation = bg.tabActions.findLast(action => action.type === 'update' && action.id === tab.id);
  // The browser-boundary fixture records updates; simulate Chrome committing
  // the document so the real sender/current-URL authorization runs unchanged.
  Object.assign(tab, { url: navigation.options.url, status: 'complete', index: 0 });
  probes.set(tab.id, { ready: true, reviewerReady: true, active: true,
    phase: 'waiting-navigation', sweeping: true, generation: lane.generation,
    lastActivityAt: Date.now(), nextActionAt: Date.now() + 15_000 });
  return { bg, tab, tabs, lane, probes, home };
}

test('actual auxiliary failure is mirrored as a safe diagnostic code', async () => {
  const { bg, tab, lane } = await auxiliaryBackgroundFixture({ department: true });
  await workerMessage(bg, { type: 'reviewer:unusable', generation: lane.generation,
    reason: 'Department discovery failed: Reviewer filter: missing-panel' }, { tab });
  const report = await bg.api.scannerStatusSnapshot();
  const department = report.lanes.find(lane => lane.lane === 'departments');
  assert.equal(department.lastErrorCode, 'target-error');
  assert.equal(department.lastFailureCode, 'missing-panel');
  assert.equal(JSON.stringify(report).includes('Department discovery failed:'), false);
});

test('owned auxiliary runtime probes expose bounded phase and focus waits without raw details', async () => {
  const { bg, tab, lane, probes } = await auxiliaryBackgroundFixture({ department: true });
  probes.set(tab.id, { ready: true, generation: lane.generation, active: true,
    phase: 'waiting-selection', busy: false, visibility: 'visible', focusReason: 'short-lease',
    lastActivityAt: 100_000, nextActionAt: 105_000, token: 'must-not-leak' });
  let runtime = (await bg.api.scannerStatusSnapshot()).lanes.find(l => l.lane === 'departments').runtime;
  assert.equal(runtime.phase, 'waiting-selection');
  assert.equal(runtime.focusReason, 'short-lease');
  assert.equal(runtime.reachable, true);
  assert.equal(runtime.active, true);
  assert.equal(runtime.nextActionAt, 105_000);
  assert.equal(JSON.stringify(runtime).includes('must-not-leak'), false);
  probes.get(tab.id).focusReason = 'private error';
  runtime = (await bg.api.scannerStatusSnapshot()).lanes.find(l => l.lane === 'departments').runtime;
  assert.equal(runtime.focusReason, 'unknown');
  probes.get(tab.id).generation -= 1;
  runtime = (await bg.api.scannerStatusSnapshot()).lanes.find(l => l.lane === 'departments').runtime;
  assert.equal(runtime.reachable, false, 'An old document cannot report the current assignment runtime');
});

test('managed auxiliary tabs cannot become primary or lose their checkpoint during home selection', async () => {
  const { bg, tab, lane } = await auxiliaryBackgroundFixture({ department: true });
  await bg.local.set({ [`sweep:${tab.id}`]: { generation: lane.generation, mode: 'none',
    visited: [1, 2], total: 4 } });
  const selected = await bg.api.selectPrimary();
  assert.equal(selected.state.tabId, 11);
  assert.equal(bg.local.data[`sweep:${tab.id}`].visited.length, 2);
  const role = await workerMessage(bg, { type: 'reviewer:role' }, { tab });
  assert.equal(role.active, true);
  assert.equal(role.primaryTabId, tab.id);
  assert.equal(role.auxiliary.lane, 'departments');
});

test('an auxiliary tab does not prevent opening the separate homepage scanner', async () => {
  const { bg, tab } = await auxiliaryBackgroundFixture({ department: true, primary: false });
  const opened = await bg.api.ensureReviewerTab();
  assert.equal(opened, true);
  assert.equal(bg.tabActions.filter(action => action.type === 'create').length, 2);
  assert.equal(bg.tabActions.findLast(action => action.type === 'create').options.url,
    'https://www.walmart.com/reviews/claim-product');
  assert.equal(bg.tabActions.filter(action => action.type === 'update' && action.id === tab.id).length, 1);
});

test('auxiliary ingest uses its own lease and stale generations cannot advance or affect home', async () => {
  const { bg, tab, lane } = await auxiliaryBackgroundFixture();
  const primary = await bg.api.selectPrimary();
  const accepted = await workerMessage(bg, { type: 'reviewer:page', generation: lane.generation,
    items: [{ item_id: 'ip-12345678' }], page: 1 }, { tab });
  assert.equal(accepted.ok, true);
  assert.equal(accepted.primaryTabId, tab.id);
  const rejected = await workerMessage(bg, { type: 'reviewer:page', generation: lane.generation - 1,
    items: [{ item_id: 'ip-12345678' }], page: 1 }, { tab });
  assert.equal(rejected.ok, false);
  const unchanged = await bg.api.readCoordinator();
  assert.equal(unchanged.tabId, primary.state.tabId);
  assert.equal(unchanged.generation, primary.state.generation);
});

test('a shared challenge preserves auxiliary progress and its recovered tab can clear verification', async () => {
  const { bg, tab, lane, probes, home } = await auxiliaryBackgroundFixture();
  await bg.api.selectPrimary();
  await bg.local.set({ [`sweep:${tab.id}`]: { generation: lane.generation, mode: 'none',
    visited: [1, 2], total: 4 } });
  await workerMessage(bg, { type: 'reviewer:checkpoint', generation: lane.generation }, { tab });
  tab.url = 'https://www.walmart.com/blocked';
  await bg.api.handleBotCheck(tab.id);
  assert.ok(await bg.api.pausedUntil());
  assert.equal(bg.local.data[`sweep:${tab.id}`], undefined);
  assert.equal((await workerMessage(bg, { type: 'reviewer:role' }, { tab })).active, false);
  tab.url = `${home}?q=Pet`;
  probes.set(tab.id, { ready: true, reviewerReady: true });
  await bg.api.selectPrimary();
  assert.equal(await bg.api.pausedUntil(), 0);
  await releaseAuxiliaryDelay(bg);
  const restored = bg.local.data[`sweep:${tab.id}`];
  assert.deepEqual([...restored.visited], [1, 2]);
  assert.ok(restored.generation > lane.generation);
});

test('verification retains primary acknowledged progress and restores it after returning on another page', async () => {
  const home = 'https://www.walmart.com/reviews/claim-product';
  const tab = { ...REVIEW_TAB, url: `${home}?page=3` };
  const probes = new Map([[tab.id, { ready: true, reviewerReady: true }]]);
  const bg = loadBackground({ tabs: [tab], probes });
  await bg.api.initialization;
  const before = (await bg.api.selectPrimary()).state;
  await bg.local.set({ [`sweep:${tab.id}`]: { generation: before.generation, mode: 'none',
    visited: [1, 2, 3], total: 8, lastProgressAt: Date.now() } });
  assert.equal((await workerMessage(bg, { type: 'reviewer:checkpoint',
    generation: before.generation }, { tab })).ok, true);
  tab.url = 'https://www.walmart.com/blocked';
  await bg.api.handleBotCheck(tab.id);
  assert.deepEqual([...bg.local.data.reviewerResume.sweep.visited], [1, 2, 3]);
  assert.equal(bg.local.data[`sweep:${tab.id}`], undefined);
  tab.url = `${home}?page=4`;
  const after = (await bg.api.selectPrimary()).state;
  assert.equal(await bg.api.pausedUntil(), 0);
  assert.ok(after.generation > before.generation);
  assert.deepEqual([...bg.local.data[`sweep:${tab.id}`].visited], [1, 2, 3]);
});

for (const department of [true, false]) {
  test(`${department ? 'department' : 'keyword'} verification shares one controller and resumes its checkpoint`,
    { timeout: 5_000 }, async () => {
      let f;
      let started;
      let release;
      const running = new Promise(resolve => { started = resolve; });
      const completed = new Promise(resolve => { release = resolve; });
      let attempts = 0;
      f = await auxiliaryBackgroundFixture({ department, automation: {
        open: async (_api, id) => { assert.equal(id, f.tab.id); return {}; },
        run: async () => { attempts++; started(); await completed;
          return { outcome: 'verified', holdMs: 2250, releaseReason: 'feedback' }; }
      } });
      const { bg, tab, lane, probes } = f;
      await bg.api.selectPrimary();
      await bg.local.set({ verificationAssistEnabled: true,
        [`sweep:${tab.id}`]: { generation: lane.generation, mode: 'none', visited: [1, 2], total: 4 } });
      await workerMessage(bg, { type: 'reviewer:checkpoint', generation: lane.generation }, { tab });
      const target = tab.url;
      tab.url = 'https://www.walmart.com/blocked';
      await bg.api.handleBotCheck(tab.id);
      await running;
      await bg.api.handleBotCheck(tab.id);
      const inFlight = bg.api.verificationAutomation.runPending({ recheck: true });
      assert.equal((await bg.api.readCoordinator()).tabId, null);
      assert.equal((await workerMessage(bg, { type: 'reviewer:role' }, { tab })).active, false);
      assert.equal((await workerMessage(bg, { type: 'reviewer:page', generation: lane.generation,
        page: 3, items: [{ item_id: 'stale-cards' }] }, { tab })).ok, false);
      tab.url = target;
      probes.set(tab.id, { ready: true, reviewerReady: true });
      release();
      await inFlight;
      assert.equal(attempts, 1);
      assert.equal(await bg.api.pausedUntil(), 0);
      assert.equal(bg.local.data.botCheck.count, 1);
      await releaseAuxiliaryDelay(bg);
      assert.deepEqual([...bg.local.data[`sweep:${tab.id}`].visited], [1, 2]);
      assert.ok(bg.local.data[`sweep:${tab.id}`].generation > lane.generation);
    });
}

test('stale primary progress resets after verification instead of skipping a day-old catalogue', async () => {
  const tab = { ...REVIEW_TAB };
  const bg = loadBackground({ tabs: [tab], probes: new Map([[tab.id,
    { ready: true, reviewerReady: true }]]) });
  await bg.api.initialization;
  const before = (await bg.api.selectPrimary()).state;
  await bg.local.set({ [`sweep:${tab.id}`]: { generation: before.generation, mode: 'none',
    visited: [1, 2], total: 4, lastProgressAt: Date.now() - 86400_001 } });
  await workerMessage(bg, { type: 'reviewer:checkpoint', generation: before.generation }, { tab });
  await bg.api.handleBotCheck(tab.id);
  const after = await bg.api.selectPrimary();
  assert.ok(after.state.generation > before.generation);
  assert.equal(bg.local.data[`sweep:${tab.id}`], undefined);
  assert.equal(bg.local.data.reviewerResume.sweep, null);
});

test('only current scanner documents can request focus and verification revokes that permission', async () => {
  const f = await auxiliaryBackgroundFixture();
  const { bg, tabs, tab, lane } = f;
  await bg.api.selectPrimary();
  tabs[0].active = true;
  bg.chrome.tabs.query = async query => tabs.filter(entry =>
    (!query?.active || entry.active) && (query?.windowId === undefined || entry.windowId === query.windowId));
  const update = bg.chrome.tabs.update;
  bg.chrome.tabs.update = async (id, options) => {
    await update(id, options);
    if (options.active) for (const entry of tabs) entry.active = entry.id === id;
    return tabs.find(entry => entry.id === id);
  };
  const request = generation => workerMessage(bg, { type: 'reviewer:focus', generation }, { tab });
  assert.equal((await request(lane.generation - 1)).granted, false);
  assert.equal((await workerMessage(bg, { type: 'reviewer:focus', generation: lane.generation })).granted, false);
  assert.equal((await request(lane.generation)).granted, true);
  assert.equal(tab.active, true);
  await workerMessage(bg, { type: 'reviewer:user-active', generation: lane.generation }, { tab });
  const busy = await request(lane.generation);
  assert.equal(busy.granted, false);
  assert.equal(busy.reason, 'user-active');
  await bg.api.handleBotCheck(tab.id);
  assert.equal((await request(lane.generation)).granted, false);
});

test('hidden scanner gains focus before its existing scroll without moving the navigation deadline', async () => {
  const content = loadContent();
  const pager = animateOffscreenPager(content);
  content.document.visibilityState = 'hidden';
  const send = content.chrome.runtime.sendMessage;
  let requests = 0;
  content.chrome.runtime.sendMessage = async message => {
    if (message.type === 'reviewer:focus') {
      requests++;
      content.document.visibilityState = 'visible';
      return { ok: true, enabled: true, granted: true };
    }
    return send(message);
  };
  await content.api.advanceDeliveredPage(9);
  assert.equal(requests, 1);
  assert.equal(pager.frames.size, 1);
  const deadline = content.timerCallbacks.get(content.api.getActionTimer()).ms;
  pager.finish();
  assert.equal(content.timerCallbacks.get(content.api.getActionTimer()).ms, deadline);
  assert.ok(pager.positions.length > 20);
});

test('busy focus retries remain inside the original wait and real input cancels them', async () => {
  const content = loadContent();
  const pager = animateOffscreenPager(content);
  const send = content.chrome.runtime.sendMessage;
  let requests = 0;
  let activity = 0;
  content.chrome.runtime.sendMessage = async message => {
    if (message.type === 'reviewer:focus') { requests++; return {
      ok: true, enabled: true, granted: false, retryAfterMs: 5_000 }; }
    if (message.type === 'reviewer:user-active') activity++;
    return send(message);
  };
  await content.api.advanceDeliveredPage(9);
  const timer = content.api.getActionTimer();
  const deadline = content.timerCallbacks.get(timer).ms;
  assert.equal(pager.frames.size, 0);
  assert.ok([...content.timerCallbacks.values()].some(value => value.ms === 5_000));
  content.listeners.get('wheel')({ isTrusted: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(activity, 1);
  assert.equal(requests, 1);
  assert.equal(content.timerCallbacks.get(timer).ms, deadline);
  assert.equal([...content.timerCallbacks.values()].some(value => value.ms === 5_000), false);
});

test('completed scanner tabs still report trusted user input', async () => {
  const content = loadContent();
  animateOffscreenPager(content);
  content.api.role.active = false;
  content.listeners.get('wheel')({ isTrusted: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(content.sentMessages.filter(message => message.type === 'reviewer:user-active').length, 1);
});

test('a completed auxiliary tab renews the shared user pause while other scans remain active', async () => {
  const f = await auxiliaryBackgroundFixture();
  const { bg, tabs, tab, lane } = f;
  const state = (await bg.api.selectPrimary()).state;
  tabs[0].active = true;
  bg.chrome.tabs.query = async query => tabs.filter(entry =>
    (!query?.active || entry.active) && (query?.windowId === undefined || entry.windowId === query.windowId));
  bg.chrome.tabs.update = async (id, options) => {
    if (options.active) for (const entry of tabs) entry.active = entry.id === id;
    return tabs.find(entry => entry.id === id);
  };
  const clock = Date.now();
  bg.api.setNow(clock);
  await workerMessage(bg, { type: 'reviewer:focus', generation: lane.generation }, { tab });
  await workerMessage(bg, { type: 'reviewer:sweep-complete', generation: lane.generation, pages: 1 }, { tab });
  const completed = (await bg.api.auxiliaryScans.status()).lanes[0];
  assert.equal(completed.status, 'complete');
  bg.api.setNow(clock + 29_000);
  assert.equal((await workerMessage(bg, { type: 'reviewer:user-active',
    generation: completed.generation }, { tab })).ok, true);
  bg.api.setNow(clock + 31_000);
  const request = await workerMessage(bg, { type: 'reviewer:focus', generation: state.generation }, { tab: tabs[0] });
  assert.equal(request.granted, false);
  assert.equal(request.reason, 'user-active');
});

test('new user input during lazy pager discovery prevents its return scroll', async () => {
  const content = loadContent();
  animateOffscreenPager(content);
  const scrolls = [];
  content.viewport.scrollTo = (...args) => scrolls.push(args);
  const revealing = content.api.revealPager();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(scrolls.length, 1);
  content.api.setNow(100_100);
  content.listeners.get('wheel')({ isTrusted: true });
  [...content.timerCallbacks.values()].find(timer => timer.ms === 400).callback();
  await new Promise(resolve => setImmediate(resolve));
  const final = [...content.timerCallbacks.values()].find(timer => timer.ms === 50);
  if (final) final.callback();
  await revealing;
  assert.equal(scrolls.length, 1);
});

test('trusted input revokes peer activation before optional grouping can block', async () => {
  const { bg, tabs, tab, lane } = await auxiliaryBackgroundFixture();
  const state = (await bg.api.selectPrimary()).state;
  tabs[0].active = true;
  bg.chrome.tabs.query = async query => tabs.filter(entry =>
    (!query?.active || entry.active) && (query?.windowId === undefined || entry.windowId === query.windowId));
  bg.chrome.tabs.update = async (id, options) => {
    if (options.active) for (const entry of tabs) entry.active = entry.id === id;
    return tabs.find(entry => entry.id === id);
  };
  const clock = Date.now();
  bg.api.setNow(clock);
  await workerMessage(bg, { type: 'reviewer:focus', generation: lane.generation }, { tab });
  bg.api.setNow(clock + 4_000);
  let release;
  const groupPending = new Promise(resolve => { release = resolve; });
  bg.chrome.tabs.group = async () => { await groupPending; return 77; };
  bg.chrome.tabGroups = { get: async () => ({ id: 77, windowId: 1 }), update: async () => {} };
  const activity = workerMessage(bg, { type: 'reviewer:user-active', generation: lane.generation }, { tab });
  await new Promise(resolve => setImmediate(resolve));
  const request = await bg.api.tabWorkspace.request(tabs[0].id, state.generation);
  release();
  assert.equal((await activity).ok, true);
  assert.equal(request.granted, false);
  assert.equal(request.reason, 'user-active');
  assert.equal(tab.active, true);
});

test('an exact extension Options document can read status and restart auxiliary scans when it has tab metadata', async () => {
  const bg = loadBackground();
  await bg.api.initialization;
  const sender = { id: 'fixture', url: 'chrome-extension://fixture/options.html',
    tab: { id: 99, url: 'chrome-extension://fixture/options.html' } };
  for (const type of ['reviewer:aux-status', 'reviewer:aux-restart', 'reviewer:diagnostics']) {
    assert.equal((await workerMessage(bg, { type }, sender)).ok, true, type);
    assert.equal((await workerMessage(bg, { type }, { id: 'fixture',
      url: REVIEW_TAB.url, tab: REVIEW_TAB })).ok, false, type);
  }
});

function resultsScrollFixture({ lane = 'home', run, finalPage = false, grant = null } = {}) {
  const target = lane === 'keywords' ? 'https://www.walmart.com/reviews/claim-product?q=Chair'
    : lane === 'departments' ? 'https://www.walmart.com/reviews/claim-product?catId=4044'
      : 'https://www.walmart.com/reviews/claim-product';
  const initialUrl = `${target}${target.includes('?') ? '&' : '?'}page=2`;
  const pageScroll = { run: run || (async () => ({ outcome: 'complete' })) };
  const content = loadContent({ pageAck: { ok: true }, pageScroll, initialUrl });
  content.api.setNow(100_000);
  content.api.setNavigationAt(90_000);
  content.api.setRandom(0.5);
  content.document.visibilityState = 'visible';
  content.body.innerText = 'Rendered reviewer results';
  content.body.scrollHeight = 8000;
  content.pager.innerText = finalPage ? '1 2' : '1 2 3';
  const auxiliary = lane === 'home' ? null : { lane, targetKind: lane === 'keywords' ? 'keyword' : 'department',
    targetKey: lane === 'keywords' ? 'Chair' : 'Home', targetUrl: target };
  Object.assign(content.api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 9,
    auxiliary });
  content.local.data.pageDelaySeconds = 15;
  content.local.data.refreshMinutes = 5;
  content.local.data['sweep:1'] = { generation: 9, mode: 'none', total: finalPage ? 2 : 3,
    visited: [1], ...(auxiliary ? { targetKey: auxiliary.targetKey, discoveryDone: true } : {}) };
  let items = [{ item_id: 'ip-initial-card' }];
  content.api.setCollector(() => items);
  const send = content.chrome.runtime.sendMessage;
  content.chrome.runtime.sendMessage = async message => {
    if (message.type === 'reviewer:focus') {
      content.sentMessages.push(message);
      return grant || { ok: true, enabled: true, granted: true, until: 110_000 };
    }
    return send(message);
  };
  return { ...content, setItems(value) { items = value; } };
}

test('results reading scroll follows acknowledged home, keyword and department pages before navigation', async () => {
  for (const lane of ['home', 'keywords', 'departments']) {
    let calls = 0;
    const content = resultsScrollFixture({ lane, run: async options => {
      calls++;
      assert.ok(content.sentMessages.some(message => message.type === 'reviewer:page'), lane);
      assert.equal(content.local.data['sweep:1'].visited.includes(2), false, lane);
      assert.equal(options.startAtTop, true, lane);
      assert.equal(options.signal.aborted, false, lane);
      assert.equal(options.canContinue(), true, lane);
      assert.ok(options.budgetMs > 0 && options.budgetMs <= 8000, lane);
      return { outcome: 'complete' };
    } });
    await content.api.relay();
    assert.equal(calls, 1, lane);
    assert.ok(content.sentMessages.some(message => message.type === 'reviewer:focus' &&
      message.purpose === 'page-scroll'), lane);
    assert.deepEqual([...content.local.data['sweep:1'].visited], [1, 2], lane);
    assert.ok(content.api.getActionTimer(), lane);
  }
});

test('results scroll holds the current page when focus or visible reading is unavailable', async () => {
  for (const scenario of ['manual', 'unfocused', 'unavailable', 'no-response', 'hidden']) {
    let calls = 0;
    const grants = {
      manual: { enabled: true, granted: false, reason: 'manual-tab-active' },
      unfocused: { enabled: true, granted: false, reason: 'window-unfocused' },
      unavailable: { enabled: false, granted: false, reason: 'activation-unavailable' },
      'no-response': null,
      hidden: { enabled: true, granted: true, until: 110_000 }
    };
    const content = resultsScrollFixture({ run: async () => { calls++; return { outcome: 'complete' }; },
      grant: grants[scenario] });
    if (scenario === 'hidden') content.document.visibilityState = 'hidden';
    if (scenario === 'no-response') {
      const send = content.chrome.runtime.sendMessage;
      content.chrome.runtime.sendMessage = message => message.type === 'reviewer:focus' ? Promise.resolve(null) : send(message);
    }
    await content.api.relay();
    assert.equal(calls, 0, scenario);
    assert.deepEqual([...content.local.data['sweep:1'].visited], [1], scenario);
    assert.equal(content.api.getActionTimer(), null, scenario);
    assert.equal(content.sentMessages.some(message => message.type === 'reviewer:sweep-complete'), false, scenario);
  }
});

test('an unfinished department scroll resumes below its previous position and cannot advance prematurely', async () => {
  const starts = [];
  const content = resultsScrollFixture({ lane: 'departments', run: async options => {
    starts.push(options.startAtTop);
    assert.equal(typeof options.settleKey, 'function');
    return starts.length === 1 ? { outcome: 'timeout', distance: 4200, durationMs: 7500 }
      : { outcome: 'complete', distance: 1000, durationMs: 2000 };
  } });
  await content.api.relay();
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1]);
  assert.equal(content.api.getActionTimer(), null);
  await content.api.relay();
  assert.deepEqual(starts, [true, false]);
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1, 2]);
});

test('focus waits reuse acknowledged intake and send only newly rendered cards', async () => {
  const content = resultsScrollFixture({ lane: 'departments', grant:
    { enabled: true, granted: false, reason: 'window-unfocused' } });
  await content.api.relay(); await content.api.relay();
  assert.equal(content.sentMessages.filter(message => message.type === 'reviewer:page').length, 1);
  content.setItems([{ item_id: 'ip-initial-card' }, { item_id: 'ip-arrived-while-waiting' }]);
  await content.api.relay();
  const pages = content.sentMessages.filter(message => message.type === 'reviewer:page');
  assert.deepEqual(pages.map(message => message.items.map(item => item.item_id)),
    [['ip-initial-card'], ['ip-arrived-while-waiting']]);
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1]);
});

test('cards arriving during late-item intake invalidate the completed department reading pass', async () => {
  let calls = 0, resolveLate, entered;
  const waiting = new Promise(resolve => { entered = resolve; });
  const content = resultsScrollFixture({ lane: 'departments', run: async () => {
    calls++;
    if (calls === 1) content.setItems([{ item_id: 'ip-initial-card' }, { item_id: 'ip-lazy-card' }]);
    return { outcome: 'complete' };
  } });
  const send = content.chrome.runtime.sendMessage;
  content.chrome.runtime.sendMessage = message => {
    if (message.type === 'reviewer:page' && message.items.length === 1 && message.items[0].item_id === 'ip-lazy-card') {
      entered(); return new Promise(resolve => { resolveLate = resolve; });
    }
    return send(message);
  };
  const pending = content.api.relay(); await waiting;
  content.setItems([{ item_id: 'ip-initial-card' }, { item_id: 'ip-lazy-card' }, { item_id: 'ip-later-card' }]);
  content.body.scrollHeight += 1000;
  resolveLate({ ok: true }); await pending;
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1]);
  assert.equal(content.api.getActionTimer(), null);
  await content.api.relay();
  assert.equal(calls, 2);
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1, 2]);
});

test('automatic focus denial cannot change a page behind a different selected tab', async () => {
  for (const reason of ['manual-tab-active', 'window-unfocused', 'activation-unavailable']) {
    const content = navigationFocusFixture();
    let navigated = false;
    content.document.visibilityState = 'visible';
    content.chrome.runtime.sendMessage = async () => ({ ok: true, enabled: true, granted: false, reason });
    await content.api.navigateWithFocus(() => { navigated = true; }, 9);
    assert.equal(navigated, false, reason);
    assert.ok(content.api.getActionTimer(), reason);
  }
});

test('a visible scanner still waits for actual window focus before reading', async () => {
  let focused = false, calls = 0;
  const content = resultsScrollFixture({ lane: 'departments', run: async () => {
    calls++; return { outcome: 'complete' };
  } });
  content.document.hasFocus = () => focused;
  await content.api.relay();
  assert.equal(calls, 0);
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1]);
  focused = true; await content.api.relay();
  assert.equal(calls, 1);
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1, 2]);
});

test('a department page that extends during the navigation wait is read again before leaving', async () => {
  let calls = 0, loads = 0;
  const content = resultsScrollFixture({ lane: 'departments', run: async () => {
    calls++; return { outcome: 'complete' };
  } });
  content.location.assign = () => { loads++; };
  await content.api.relay();
  content.setItems([{ item_id: 'ip-initial-card' }, { item_id: 'ip-appended-before-navigation' }]);
  content.body.scrollHeight += 500;
  content.api.setNow(106_000);
  await runNavigation(content);
  assert.equal(loads, 0);
  assert.equal(content.api.getActionTimer(), null);
  await content.api.relay();
  assert.equal(calls, 2);
  assert.ok(content.api.getActionTimer());
});

test('revealing a pager above the true bottom preserves completed page reading', async () => {
  let reads = 0, navigations = 0;
  const content = loadContent({ pageScroll: { run: async () => {
    reads++; return { outcome: 'complete' };
  } } });
  const pager = animateOffscreenPager(content);
  content.viewport.scrollY = 4200;
  content.document.hasFocus = () => true;
  const send = content.chrome.runtime.sendMessage;
  content.chrome.runtime.sendMessage = message => message.type === 'reviewer:focus'
    ? Promise.resolve({ ok: true, enabled: true, granted: true, until: 110_000 }) : send(message);
  assert.equal(await content.api.scrollResultsPage(9), true);
  await content.api.preparePageNavigation(2, 9, 9000);
  pager.finish();
  assert.ok(content.viewport.scrollY < 4200, 'The pager requires an upward preparation scroll');
  const rect = pager.control.getBoundingClientRect();
  assert.ok(rect.top >= 0 && rect.bottom <= 800);
  await content.api.navigateWithFocus(() => { navigations++; }, 9);
  assert.equal(navigations, 1, 'Owned pager positioning must not restart the completed reading pass');
  assert.equal(reads, 1);
});

test('results scroll contention defers the page with a bounded retry rather than advancing without its slot', async () => {
  for (const reason of ['busy', 'waiting-turn']) {
    let calls = 0;
    const content = resultsScrollFixture({ run: async () => { calls++; return { outcome: 'complete' }; },
      grant: { enabled: true, granted: false, reason, retryAfterMs: 5000 } });
    await content.api.relay();
    assert.equal(calls, 0);
    assert.deepEqual([...content.local.data['sweep:1'].visited], [1]);
    assert.equal(content.api.getActionTimer(), null);
    assert.ok([...content.timerCallbacks.values()].some(timer => timer.ms === 5000));
  }
});

test('a short first reading lease defers without consuming the start-at-top pass', async () => {
  const starts = [];
  const content = resultsScrollFixture({ run: async options => {
    starts.push(options.startAtTop);
    return { outcome: 'complete' };
  } });
  let requests = 0;
  const send = content.chrome.runtime.sendMessage;
  content.chrome.runtime.sendMessage = async message => {
    if (message.type === 'reviewer:focus' && message.purpose === 'page-scroll') {
      content.sentMessages.push(message);
      return { ok: true, enabled: true, granted: true, until: ++requests === 1 ? 100_250 : 110_000 };
    }
    return send(message);
  };
  await content.api.relay();
  assert.deepEqual(starts, []);
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1]);
  assert.equal(content.api.getActionTimer(), null);
  assert.ok([...content.timerCallbacks.values()].some(timer => timer.ms === 1000));
  await content.api.relay();
  assert.deepEqual(starts, [true]);
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1, 2]);
  assert.ok(content.api.getActionTimer());
});

test('results scroll commits newly lazy-loaded cards before acknowledging the final search page', async () => {
  let scrolled = 0;
  let lateArrived;
  let releaseLate;
  const latePending = new Promise(resolve => { lateArrived = resolve; });
  const content = resultsScrollFixture({ lane: 'keywords', finalPage: true, run: async () => {
    scrolled++;
    content.setItems([{ item_id: 'ip-initial-card' }, { item_id: 'ip-lazy-card' }]);
    return { outcome: 'complete' };
  } });
  const send = content.chrome.runtime.sendMessage;
  content.chrome.runtime.sendMessage = async message => {
    if (message.type === 'reviewer:page' && message.items.some(item => item.item_id === 'ip-lazy-card')) {
      content.sentMessages.push(message);
      lateArrived();
      return new Promise(resolve => { releaseLate = resolve; });
    }
    return send(message);
  };
  const relay = content.api.relay();
  await latePending;
  assert.equal(content.sentMessages.some(message => message.type === 'reviewer:sweep-complete'), false);
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1]);
  assert.equal(content.api.getActionTimer(), null);
  releaseLate({ ok: true });
  await relay;
  assert.equal(scrolled, 1);
  assert.deepEqual(content.sentMessages.filter(message => message.type === 'reviewer:page')
    .map(message => message.items.map(item => item.item_id)), [['ip-initial-card'], ['ip-lazy-card']]);
  assert.ok(content.sentMessages.some(message => message.type === 'reviewer:sweep-complete' && message.pages === 2));
});

test('results scroll late-card acknowledgement failure prevents final completion and retry does not repeat the pass', async () => {
  let calls = 0;
  let failLate = true;
  const content = resultsScrollFixture({ lane: 'keywords', finalPage: true, run: async () => {
    calls++;
    content.setItems([{ item_id: 'ip-initial-card' }, { item_id: 'ip-lazy-card' }]);
    return { outcome: 'complete' };
  } });
  const send = content.chrome.runtime.sendMessage;
  content.chrome.runtime.sendMessage = async message => {
    if (message.type === 'reviewer:page' && message.items.length === 1 &&
        message.items[0].item_id === 'ip-lazy-card' && failLate) {
      content.sentMessages.push(message);
      return { ok: false, retryable: true };
    }
    return send(message);
  };
  await content.api.relay();
  assert.equal(content.api.getPhase(), 'ingest-retry');
  assert.deepEqual([...content.local.data['sweep:1'].visited], [1]);
  assert.equal(content.api.getActionTimer(), null);
  assert.equal(content.sentMessages.some(message => message.type === 'reviewer:sweep-complete'), false);
  failLate = false;
  await content.api.relay();
  assert.equal(calls, 1);
  assert.ok(content.sentMessages.some(message => message.type === 'reviewer:sweep-complete'));
});

test('results scroll cancellation by trusted input, lease revocation, verification, visibility or scope change never advances', async () => {
  for (const scenario of ['input', 'role', 'verification', 'visibility', 'scope']) {
    let entered;
    let options;
    let finish;
    const running = new Promise(resolve => { entered = resolve; });
    const content = resultsScrollFixture({ run: async value => {
      options = value;
      entered();
      return new Promise(resolve => {
        finish = resolve;
        value.signal.addEventListener('abort', () => resolve({ outcome: 'cancelled' }), { once: true });
      });
    } });
    const relay = content.api.relay();
    await running;
    if (scenario === 'input') content.listeners.get('wheel')({ isTrusted: true });
    if (scenario === 'role') content.api.applyRole({ active: false, tabId: 1, generation: 10 });
    if (scenario === 'verification') {
      content.body.innerText = 'Robot or human? Press & Hold';
      content.api.checkVerification();
    }
    if (scenario === 'visibility') {
      content.document.visibilityState = 'hidden';
      content.listeners.get('visibilitychange')({ isTrusted: true });
    }
    if (scenario === 'scope') {
      content.location.href += '&q=manual';
      assert.equal(options.canContinue(), false);
      // The real helper stops on a false canContinue; this controlled helper
      // resolves the same cancellation without inventing a DOM route event.
      finish({ outcome: 'cancelled' });
    }
    await relay;
    if (scenario !== 'scope') assert.equal(options.signal.aborted, true, scenario);
    assert.deepEqual([...content.local.data['sweep:1'].visited], [1], scenario);
    assert.equal(content.api.getActionTimer(), null, scenario);
    assert.equal(content.sentMessages.some(message => message.type === 'reviewer:sweep-complete'), false, scenario);
  }
});

test('results scroll errors preserve acknowledged items but cannot acknowledge unread page coverage', async () => {
  for (const scenario of ['throw', 'unavailable']) {
    const content = resultsScrollFixture({ run: async () => {
      if (scenario === 'throw') throw new Error('Animation API vanished');
      return { outcome: 'unavailable' };
    } });
    await content.api.relay();
    assert.deepEqual([...content.local.data['sweep:1'].visited], [1], scenario);
    assert.equal(content.api.getActionTimer(), null, scenario);
    await content.api.relay(); await content.api.relay();
    assert.ok(content.sentMessages.some(message => message.type === 'reviewer:unusable'), scenario);
    assert.equal(content.sentMessages.some(message => message.type === 'reviewer:sweep-complete'), false, scenario);
  }
});

test('a late active role safely handles a detached document and uses its root before a body exists', () => {
  for (const root of [null, { nodeName: 'HTML' }]) {
    const content = loadContent();
    content.api.applyRole({ active: false, tabId: 1, generation: 8 });
    const observers = content.mutationObservers.length;
    content.document.body = null;
    content.document.documentElement = root;
    assert.doesNotThrow(() => content.api.applyRole({ active: true, tabId: 1,
      primaryTabId: 1, generation: 9 }));
    assert.equal(content.mutationObservers.length, observers + (root ? 1 : 0));
    if (root) assert.equal(content.mutationObservers.at(-1).target, root);
  }
});

function pendingDepartmentSelectionFixture() {
  const parentUrl = 'https://www.walmart.com/reviews/claim-product';
  const selectedUrl = `${parentUrl}?catId=91083`;
  let binding = false;
  let items = [{ item_id: 'parent-card-must-not-ingest' }];
  let entered;
  let finish;
  const selectionEntered = new Promise(resolve => { entered = resolve; });
  const selected = new Promise(resolve => { finish = resolve; });
  const content = loadContent({ initialUrl: parentUrl, facetControls: { async select() {
    entered();
    await selected;
    content.location.href = selectedUrl;
    content.location.search = '?catId=91083';
    items = [{ item_id: 'auto-tires-selected-card' }];
    return { ok: true };
  } } });
  content.api.setNow(100_000);
  content.api.setNavigationAt(100_000);
  content.api.setRandom(0.5);
  content.api.setCollector(() => items);
  content.local.data.pageDelaySeconds = 15;
  content.local.data.refreshMinutes = 0;
  content.pager.innerText = '1';
  const auxiliary = { lane: 'departments', targetKind: 'department', targetKey: 'Auto & Tires',
    targetUrl: parentUrl, selection: { kind: 'department', label: 'Auto & Tires', parentUrl } };
  const activeRole = { active: true, tabId: 1, primaryTabId: 1, generation: 90, auxiliary };
  content.chrome.runtime.sendMessage = async message => {
    content.sentMessages.push(message);
    if (message.type === 'reviewer:aux-selected') {
      assert.equal(message.url, selectedUrl);
      binding = true;
      return { ok: true, ...activeRole, auxiliary: { ...auxiliary, targetUrl: selectedUrl, selection: null } };
    }
    if (message.type === 'reviewer:page') {
      // Mirror the manager's real rejection of any parent cards reported while
      // a facet assignment remains unbound. Such an ACK revokes queued work.
      return { ok: binding, ...activeRole, active: binding,
        auxiliary: binding ? { ...auxiliary, targetUrl: selectedUrl, selection: null } : auxiliary };
    }
    return { ok: true };
  };
  content.api.applyRole(activeRole);
  permitFacetFocus(content);
  return { ...content, selectionEntered, finishSelection: () => finish() };
}

test('department mutations preserve the paced selection and ingest only after its results are bound', async () => {
  const content = pendingDepartmentSelectionFixture();
  await content.api.relay();
  const pending = content.api.getActionTimer();
  assert.equal(content.timerCallbacks.get(pending).ms, 15_000);
  content.mutationObservers.at(-1).callback();
  await content.api.observeVisibleUpdate();
  assert.equal(content.sentMessages.some(message => message.type === 'reviewer:page'), false);
  assert.equal(content.api.role.active, true);
  assert.equal(content.api.getActionTimer(), pending, 'A parent mutation must not cancel the selection delay');

  await runNavigation(content);
  await content.selectionEntered;
  assert.equal(content.api.getPhase(), 'waiting-target-change');
  content.mutationObservers.at(-1).callback();
  await content.api.observeVisibleUpdate();
  assert.equal(content.sentMessages.some(message => message.type === 'reviewer:page'), false);
  assert.equal(content.api.role.active, true);
  content.finishSelection();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(content.api.role.auxiliary.selection, null);
  await content.api.relay();
  const messages = content.sentMessages;
  const boundAt = messages.findIndex(message => message.type === 'reviewer:aux-selected');
  const ingestedAt = messages.findIndex(message => message.type === 'reviewer:page');
  assert.ok(boundAt >= 0 && ingestedAt > boundAt);
  assert.deepEqual(messages.filter(message => message.type === 'reviewer:page')
    .flatMap(message => message.items.map(item => item.item_id)), ['auto-tires-selected-card']);
});

test('an unbound department cannot observe parent cards even if its phase says waiting-navigation', async () => {
  const content = pendingDepartmentSelectionFixture();
  await content.api.relay();
  const pending = content.api.getActionTimer();
  content.api.setPhase('waiting-navigation');
  content.mutationObservers.at(-1).callback();
  await content.api.observeVisibleUpdate();
  assert.equal(content.sentMessages.some(message => message.type === 'reviewer:page'), false);
  assert.equal(content.api.role.active, true);
  assert.equal(content.api.getActionTimer(), pending);
});

function facetInteractionFixture(operation, helper = null) {
  const parentUrl = 'https://www.walmart.com/reviews/claim-product';
  const selectedUrl = `${parentUrl}?catId=91083`;
  let grant = { ok: true, enabled: true, granted: true, until: 110_000 };
  let items = [{ item_id: 'parent-result' }];
  const calls = [];
  const perform = async args => {
    calls.push(args);
    if (helper) return helper(args, content);
    if (content.document.visibilityState !== 'visible') {
      throw Object.assign(new Error('Reviewer filter: missing-panel'), { code: 'missing-panel' });
    }
    assert.equal(args.canAct(), true);
    if (operation === 'discover') return [{ kind: 'department', label: 'Auto & Tires', parentUrl }];
    content.location.href = selectedUrl;
    content.location.search = '?catId=91083';
    items = [{ item_id: 'selected-auto-result' }];
    return { ok: true };
  };
  const content = loadContent({ initialUrl: parentUrl,
    facetControls: { discover: perform, select: perform } });
  content.api.setNow(100_000);
  content.api.setCollector(() => items);
  const auxiliary = operation === 'discover'
    ? { lane: 'departments', targetKey: 'root', targetKind: 'root', targetUrl: parentUrl, discoveryOnly: true }
    : { lane: 'departments', targetKey: 'Auto & Tires', targetKind: 'department', targetUrl: parentUrl,
      selection: { kind: 'department', label: 'Auto & Tires', parentUrl } };
  const activeRole = { active: true, tabId: 1, primaryTabId: 1, generation: 90, auxiliary };
  content.chrome.runtime.sendMessage = async message => {
    content.sentMessages.push(message);
    if (message.type === 'reviewer:focus') return grant;
    if (message.type === 'reviewer:aux-selected') return { ok: true, ...activeRole,
      auxiliary: { ...auxiliary, targetUrl: selectedUrl, selection: null } };
    return { ok: true };
  };
  content.api.applyRole(activeRole);
  return { ...content, calls, invoke: () => operation === 'discover'
    ? content.api.discoverAuxiliaryTargets(90) : content.api.selectAuxiliaryTarget(90),
  setGrant(value) { grant = value; } };
}

test('hidden facet panels wait for focus and visibility without spending a department attempt', async () => {
  for (const operation of ['discover', 'select']) {
    const content = facetInteractionFixture(operation);
    await content.invoke();
    assert.equal(content.calls.length, 0, operation);
    assert.equal(content.sentMessages.some(message => message.type === 'reviewer:unusable'), false, operation);
    assert.equal(content.api.role.active, true, operation);
    assert.equal(content.api.getPhase(), 'waiting-user', operation);
    assert.ok([...content.timerCallbacks.values()].some(timer => timer.ms === 5000), operation);
    content.document.visibilityState = 'visible';
    await content.invoke();
    assert.equal(content.calls.length, 1, operation);
    assert.ok(content.sentMessages.some(message => message.type === 'reviewer:focus' &&
      message.purpose === 'facet-controls'), operation);
    assert.ok(content.sentMessages.some(message => message.type ===
      (operation === 'discover' ? 'reviewer:aux-discovered' : 'reviewer:aux-selected')), operation);
  }
});

test('denied facet focus retains its department assignment while another lane keeps scanning', async () => {
  for (const operation of ['discover', 'select']) {
    for (const reason of ['busy', 'waiting-turn', 'manual-tab-active', 'window-unfocused']) {
      const content = facetInteractionFixture(operation);
      content.document.visibilityState = 'visible';
      content.setGrant({ ok: true, enabled: true, granted: false, reason, retryAfterMs: 5000 });
      await content.invoke();
      assert.equal(content.calls.length, 0, `${operation}: ${reason}`);
      assert.equal(content.api.role.active, true, reason);
      assert.equal(content.api.role.generation, 90, reason);
      assert.equal(content.sentMessages.some(message => message.type === 'reviewer:unusable'), false, reason);
      assert.ok([...content.timerCallbacks.values()].some(timer => timer.ms === 5000), reason);
      if (operation === 'select') assert.equal(content.api.role.auxiliary.selection.label, 'Auto & Tires');
    }
  }
  const peer = resultsScrollFixture({ lane: 'keywords' });
  await peer.api.relay();
  assert.deepEqual([...peer.local.data['sweep:1'].visited], [1, 2]);
  assert.ok(peer.api.getActionTimer());
});

test('facet controls defer a short existing focus lease and use the next full lease', async () => {
  for (const operation of ['discover', 'select']) {
    const content = facetInteractionFixture(operation);
    content.document.visibilityState = 'visible';
    content.setGrant({ ok: true, enabled: true, granted: true, until: 107_999 });
    await content.invoke();
    assert.equal(content.calls.length, 0, operation);
    assert.equal(content.sentMessages.some(message => message.type === 'reviewer:unusable'), false, operation);
    content.setGrant({ ok: true, enabled: true, granted: true, until: 110_000 });
    await content.invoke();
    assert.equal(content.calls.length, 1, operation);
  }
});

test('facet probe reports hidden, denied, short and successful focus states without free text', async () => {
  const content = facetInteractionFixture('select');
  await content.invoke();
  assert.equal((await contentMessage(content, { type: 'reviewer:probe' })).focusReason, 'hidden');
  content.document.visibilityState = 'visible';
  content.setGrant({ ok: true, enabled: true, granted: false, reason: 'busy' });
  await content.invoke();
  assert.equal((await contentMessage(content, { type: 'reviewer:probe' })).focusReason, 'busy');
  content.setGrant({ ok: true, enabled: true, granted: true, until: 107_000 });
  await content.invoke();
  assert.equal((await contentMessage(content, { type: 'reviewer:probe' })).focusReason, 'short-lease');
  content.setGrant({ ok: true, enabled: true, granted: true, until: 110_000 });
  await content.invoke();
  assert.equal((await contentMessage(content, { type: 'reviewer:probe' })).focusReason, 'none');
});

test('facet visibility or lease loss during panel mounting yields without failing its target', async () => {
  for (const operation of ['discover', 'select']) {
    for (const interruption of ['visibility', 'lease']) {
      let entered;
      let release;
      const started = new Promise(resolve => { entered = resolve; });
      const mounting = new Promise(resolve => { release = resolve; });
      const content = facetInteractionFixture(operation, async args => {
        assert.equal(args.canAct(), true);
        entered();
        await mounting;
        assert.equal(args.canAct(), false);
        if (operation === 'select') return { ok: false, reason: 'cancelled' };
        throw Object.assign(new Error('Reviewer filter: cancelled'), { code: 'cancelled' });
      });
      content.document.visibilityState = 'visible';
      const pending = content.invoke();
      await started;
      if (interruption === 'visibility') {
        content.document.visibilityState = 'hidden';
        content.listeners.get('visibilitychange')({ isTrusted: true });
      } else content.api.setNow(110_001);
      assert.equal(content.calls[0].canAct(), false, `${operation}: ${interruption} revokes the panel guard`);
      release();
      await pending;
      assert.equal(content.sentMessages.some(message => message.type === 'reviewer:unusable'), false,
        `${operation}: ${interruption}`);
      assert.equal(content.sentMessages.some(message => message.type === 'reviewer:aux-selected' ||
        message.type === 'reviewer:aux-discovered'), false);
      assert.equal(content.api.role.active, true);
      assert.equal(content.api.getPhase(), 'waiting-user');
      assert.ok([...content.timerCallbacks.values()].some(timer => timer.ms === 5000));
    }
  }
});

function navigationFocusFixture() {
  const content = loadContent();
  content.api.setNow(100_000);
  Object.assign(content.api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 9 });
  return content;
}

test('navigation requests its own focus turn immediately before changing the page', async () => {
  const content = navigationFocusFixture();
  const order = [];
  let grant;
  content.chrome.runtime.sendMessage = message => {
    assert.equal(message.type, 'reviewer:focus');
    assert.equal(message.purpose, 'page-navigation');
    order.push('focus-request');
    return new Promise(resolve => { grant = resolve; });
  };
  const pending = content.api.navigateWithFocus(() => order.push('navigate'), 9);
  assert.deepEqual(order, ['focus-request']);
  assert.equal(content.api.getPhase(), 'waiting-focus');
  content.document.visibilityState = 'visible';
  grant({ ok: true, enabled: true, granted: true, until: 103_000 });
  await pending;
  assert.deepEqual(order, ['focus-request', 'navigate']);
});

test('queued native and fallback page changes both await the navigation focus response', async () => {
  for (const native of [true, false]) {
    const content = navigationFocusFixture();
    let changes = 0;
    let release;
    if (native) addNativePageControl(content, () => changes++);
    content.location.assign = () => changes++;
    content.chrome.runtime.sendMessage = message => {
      if (message.type === 'reviewer:focus' && message.purpose === 'page-navigation') {
        return new Promise(resolve => { release = resolve; });
      }
      return Promise.resolve({ ok: true });
    };
    await content.api.advanceDeliveredPage(9);
    const pending = runNavigation(content);
    assert.equal(changes, 0, native ? 'native' : 'fallback');
    assert.equal(typeof release, 'function');
    content.document.visibilityState = 'visible';
    release({ ok: true, enabled: true, granted: true, until: 103_000 });
    await pending;
    assert.equal(changes, 1, native ? 'native' : 'fallback');
  }
});

test('navigation waits its turn instead of changing a page behind another scanner', async () => {
  const content = navigationFocusFixture();
  let calls = 0;
  let navigations = 0;
  content.chrome.runtime.sendMessage = async () => {
    calls++;
    if (calls === 1) return { ok: true, enabled: true, granted: false, reason: 'busy', retryAfterMs: 5000 };
    content.document.visibilityState = 'visible';
    return { ok: true, enabled: true, granted: true, until: 103_000 };
  };
  await content.api.navigateWithFocus(() => navigations++, 9);
  assert.equal(navigations, 0);
  assert.equal(content.api.getPhase(), 'waiting-user');
  const retry = content.timerCallbacks.get(content.api.getActionTimer());
  assert.equal(retry.ms, 5000);
  await retry.callback();
  assert.equal(navigations, 1);
  assert.equal(calls, 2);
});

test('foreground page selection arriving before visibility cannot navigate prematurely', async () => {
  const content = navigationFocusFixture();
  let navigations = 0;
  content.chrome.runtime.sendMessage = async () => ({ ok: true, enabled: true, granted: true, until: 103_000 });
  await content.api.navigateWithFocus(() => navigations++, 9);
  assert.equal(navigations, 0);
  content.document.visibilityState = 'visible';
  await content.timerCallbacks.get(content.api.getActionTimer()).callback();
  assert.equal(navigations, 1);
});

test('a focus lease that expired during browser suspension cannot authorize page navigation', async () => {
  const content = navigationFocusFixture();
  let changes = 0;
  content.document.visibilityState = 'visible';
  content.chrome.runtime.sendMessage = async () => ({ ok: true, enabled: true, granted: true, until: 99_999 });
  await content.api.navigateWithFocus(() => changes++, 9);
  assert.equal(changes, 0);
  assert.equal(content.timerCallbacks.get(content.api.getActionTimer()).ms, 100);
});

test('focus before navigation waits for unavailable automatic focus and retains explicit rotation opt-out', async () => {
  for (const reply of [null, { enabled: false, granted: false, reason: 'disabled' },
    { enabled: true, granted: false, reason: 'window-unfocused' },
    { enabled: true, granted: false, reason: 'manual-tab-active' }]) {
    const content = navigationFocusFixture();
    let navigations = 0;
    content.chrome.runtime.sendMessage = async () => reply;
    await content.api.navigateWithFocus(() => navigations++, 9);
    assert.equal(navigations, reply?.reason === 'disabled' ? 1 : 0);
  }
});

test('late navigation focus acknowledgements cannot override user input, a changed role, scope or verification', async () => {
  for (const scenario of ['input', 'role', 'scope', 'verification']) {
    const content = navigationFocusFixture();
    let release;
    let navigations = 0;
    content.chrome.runtime.sendMessage = message => message.type === 'reviewer:focus'
      ? new Promise(resolve => { release = resolve; }) : Promise.resolve({ ok: true });
    const pending = content.api.navigateWithFocus(() => navigations++, 9);
    if (scenario === 'input') content.listeners.get('wheel')({ isTrusted: true });
    if (scenario === 'role') content.api.applyRole({ active: false, tabId: 1, generation: 10 });
    if (scenario === 'scope') content.location.href += '&q=manual';
    if (scenario === 'verification') {
      content.body.innerText = 'Robot or human? Press & Hold';
      content.api.checkVerification();
    }
    content.document.visibilityState = 'visible';
    release({ ok: true, enabled: true, granted: true, until: 103_000 });
    await pending;
    assert.equal(navigations, 0, scenario);
  }
});
