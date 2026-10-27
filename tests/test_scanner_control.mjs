import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = fs.readFileSync(new URL('../extension/scanner-control.js', import.meta.url), 'utf8');
const settings = () => ({ schemaVersion: 1, auxiliaryDepartments: true, auxiliaryKeywords: true,
  auxiliaryBrands: false, auxiliaryKeywordQueries: ['Chair', 'Pet'], groupScannerTabs: true,
  rotateScannerTabs: true, verificationAssistEnabled: true, verificationNativeEnabled: false,
  pathPattern: '^/reviews/claim-product' });

test('selection failures retain actionable fixed codes without exporting raw messages', () => {
  const f = fixture();
  for (const [message, code] of [
    ['Selected filter did not produce a fresh results page within 30 seconds', 'selection-render-timeout'],
    ['The selected filter document did not render within 30 seconds', 'selection-document-timeout'],
    ['Could not authorize selected filter', 'selection-bind-failed'],
    ['The filter changed without a confirmed fresh results page', 'selection-unconfirmed'],
  ]) assert.equal(f.failureCode(message), code);
  assert.equal(f.failureCode('private raw error and token'), 'unexpected-error');
});
function fixture() {
  const data = { endpoint: 'http://127.0.0.1:8787/ingest', token: 'never-export-this-token',
    auxiliaryDepartments: false, verificationAssistBudget: { attempts: [1, 2, 3] } };
  const paths = [];
  const actions = [];
  let paused = false;
  let failReservation = false;
  let result = { ok: true };
  const bodies = new Map([['/scanner-settings', { settings: settings() }],
    ['/scanner-commands', { commands: null }], ['/scanner-status', { ok: true }]]);
  const local = { async get(keys) { return Object.fromEntries(keys.filter(key => key in data).map(key => [key, data[key]])); },
    async set(value) { if (failReservation && value.scannerCommandIds) throw new Error('disk unavailable');
      Object.assign(data, JSON.parse(JSON.stringify(value))); } };
  const context = vm.createContext({ URL, AbortController, setTimeout, clearTimeout, console,
    fetch: async (url, init) => {
      const path = new URL(url).pathname;
      paths.push({ path, ...init });
      const body = bodies.get(path);
      if (body instanceof Error) throw body;
      return { ok: true, async text() { return typeof body === 'string' ? body : JSON.stringify(body); } };
    } });
  vm.runInContext(source, context);
  const make = () => context.ReviewerScannerControl.create({ storage: { local } }, {
    config: async () => ({ ...data }),
    checkedEndpoint(raw) { const url = new URL(raw); if (url.protocol !== 'http:' ||
      !['127.0.0.1', 'localhost'].includes(url.hostname)) throw new Error('unsafe endpoint'); return raw; },
    paused: async () => paused,
    snapshot: async () => ({ schemaVersion: 1, verification: { pausedUntil: 0 } }),
    onCommand: async name => { actions.push(name); if (result instanceof Error) throw result; return result; }
  });
  return { data, paths, actions, bodies, make, bridge: make(), validate: context.validatedScannerSettings,
    failureCode: context.scannerFailureCode,
    pause(value) { paused = value; }, failReservation() { failReservation = true; },
    result(value) { result = value; }, command(id = 'request-1') { bodies.set('/scanner-commands', {
      commands: { schemaVersion: 1, restartHomeId: id, restartAdditionalId: null } }); } };
}

test('scanner failure export keeps only recognized codes and never raw errors', () => {
  const f = fixture();
  for (const code of ['missing-filter-section', 'missing-panel', 'expansion-stalled', 'cancelled']) {
    assert.equal(f.failureCode(`Department discovery failed: Reviewer filter: ${code}`), code);
  }
  assert.equal(f.failureCode('Filter selection failed: missing-selection'), 'missing-selection');
  assert.equal(f.failureCode('Department discovery failed: ReviewerFacetControls is not defined'), 'controls-unavailable');
  assert.equal(f.failureCode('Department discovery failed: Could not save discovered filters'), 'discovery-save-failed');
  assert.equal(f.failureCode('The reviewer tab did not respond'), 'content-unresponsive');
  assert.equal(f.failureCode('Managed reviewer page stopped responding'), 'content-unresponsive');
  assert.equal(f.failureCode('The reviewer page stopped progressing'), 'progress-timeout');
  assert.equal(f.failureCode('private account text https://private.example/path'), 'unexpected-error');
  assert.equal(f.failureCode(null), 'none');
});

test('scanner settings validate exact nonsecret fields, bounded queries and usable route syntax', () => {
  const f = fixture();
  for (const patch of [{ token: 'secret' }, { auxiliaryDepartments: 'true' },
    { auxiliaryKeywordQueries: ['x'.repeat(81)] }, { auxiliaryKeywordQueries: ['bad\nquery'] },
    { pathPattern: '[' }, { schemaVersion: 2 }]) {
    assert.throws(() => f.validate({ ...settings(), ...patch }), /Invalid scanner settings/);
  }
  assert.equal(f.validate({ ...settings(), auxiliaryKeywordQueries: null }).auxiliaryKeywordQueries, null);
  assert.equal(f.validate({ ...settings(), auxiliaryKeywordQueries: [] }).auxiliaryKeywordQueries.length, 0);
});

test('backend settings override stale browser flags without changing credentials or verifier budgets', async () => {
  const f = fixture();
  assert.equal(await f.bridge.syncSettings(), true);
  assert.equal(f.data.auxiliaryDepartments, true);
  assert.equal(f.data.verificationAssistEnabled, true);
  assert.equal(f.data.token, 'never-export-this-token');
  assert.deepEqual(f.data.verificationAssistBudget.attempts, [1, 2, 3]);
});

test('absent or invalid backend settings retain the complete working browser cache', async () => {
  for (const value of [null, { ...settings(), auxiliaryKeywordQueries: [7] }]) {
    const f = fixture(); f.bodies.set('/scanner-settings', { settings: value });
    assert.equal(await f.bridge.syncSettings(), value === null);
    assert.equal(f.data.auxiliaryDepartments, false);
    assert.equal(f.data.verificationAssistEnabled, undefined);
  }
});

test('bad transport and oversized responses stop only backend synchronization', async () => {
  for (const body of [new Error('unavailable'), ' '.repeat(70_000)]) {
    const f = fixture(); f.bodies.set('/scanner-settings', body);
    assert.equal(await f.bridge.syncSettings(), false);
    assert.equal(f.data.auxiliaryDepartments, false);
    assert.match(f.data.scannerSettingsSyncError, /last usable/);
  }
});

test('control requests refuse nonloopback destinations before disclosing the token', async () => {
  const f = fixture(); f.data.endpoint = 'https://outside.example/ingest';
  assert.equal(await f.bridge.syncSettings(), false);
  assert.equal(await f.bridge.syncCommands(), false);
  assert.equal(await f.bridge.syncStatus(), false);
  assert.equal(f.paths.length, 0);
});

test('restart IDs reserve once and remain idempotent across controller recreation', async () => {
  const f = fixture(); f.command();
  await Promise.all([f.bridge.syncCommands(), f.bridge.syncCommands()]);
  await f.make().syncCommands();
  assert.deepEqual(f.actions, ['restartHome']);
  assert.equal(f.data.scannerCommandReceipts.restartHome.outcome, 'complete');
});

test('a failed durable reservation prevents the restart', async () => {
  const f = fixture(); f.command(); f.failReservation();
  assert.equal(await f.bridge.syncCommands(), false);
  assert.equal(f.actions.length, 0);
});

test('the same request ID restarts each named scan once across controller recreation', async () => {
  const f = fixture();
  f.bodies.set('/scanner-commands', { commands: { schemaVersion: 1,
    restartHomeId: 'restart-both-1', restartAdditionalId: 'restart-both-1' } });
  await f.bridge.syncCommands();
  await f.make().syncCommands();
  assert.deepEqual(f.actions, ['restartHome', 'restartAdditional']);
  assert.equal(f.data.scannerCommandReceipts.restartAdditional.outcome, 'complete');
});

test('verification defers a restart without spending its ID or clearing the pause', async () => {
  const f = fixture(); f.command(); f.pause(true);
  await f.bridge.syncCommands();
  assert.equal(f.actions.length, 0);
  assert.equal(f.data.scannerCommandIds, undefined);
  f.pause(false); await f.bridge.syncCommands();
  assert.deepEqual(f.actions, ['restartHome']);
});

test('interrupted reserved restarts are reported and never blindly repeated', async () => {
  const f = fixture(); f.command();
  f.data.scannerCommandReceipts = { restartHome: { id: 'request-1', outcome: 'started', at: 10 } };
  f.data.scannerCommandIds = ['request-1'];
  await f.make().syncCommands();
  assert.equal(f.actions.length, 0);
  assert.equal(f.data.scannerCommandReceipts.restartHome.outcome, 'interrupted');
});

test('a pause racing the reserved command leaves it retryable after actual recovery', async () => {
  const f = fixture(); f.command(); f.result({ ok: false, paused: true });
  await f.bridge.syncCommands();
  assert.equal(f.data.scannerCommandReceipts.restartHome.outcome, 'paused');
  f.result({ ok: true }); await f.bridge.syncCommands();
  assert.equal(f.actions.length, 2);
  assert.equal(f.data.scannerCommandReceipts.restartHome.outcome, 'complete');
});

test('failed restart callbacks stay visible without crashing or replaying the command', async () => {
  const f = fixture(); f.command(); f.result(new Error('failed'));
  assert.equal(await f.bridge.syncCommands(), true);
  await f.bridge.syncCommands();
  assert.equal(f.actions.length, 1);
  assert.equal(f.data.scannerCommandReceipts.restartHome.outcome, 'failed');
});

test('unknown commands never invoke collector controls', async () => {
  const f = fixture(); f.bodies.set('/scanner-commands', { commands: {
    schemaVersion: 1, restartHomeId: 'ok', restartAdditionalId: null, execute: 'anything' } });
  assert.equal(await f.bridge.syncCommands(), false);
  assert.equal(f.actions.length, 0);
});

test('status mirroring sends only the explicit snapshot, with credentials confined to its header', async () => {
  const f = fixture(); assert.equal(await f.bridge.syncStatus(), true);
  const post = f.paths[0];
  assert.equal(post.path, '/scanner-status');
  assert.equal(post.headers['X-Ingest-Token'], f.data.token);
  assert.equal(post.body.includes(f.data.token), false);
  assert.equal(post.body.includes('endpoint'), false);
});

test('a status outage cannot block independently queued settings or commands', async () => {
  const f = fixture(); f.bodies.set('/scanner-status', new Error('offline')); f.command();
  assert.equal(await f.bridge.syncStatus(), false);
  assert.equal(await f.bridge.syncSettings(), true);
  assert.equal(await f.bridge.syncCommands(), true);
  assert.equal(f.actions.length, 1);
});
