import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

const source = fs.readFileSync(new URL('../extension/diagnostics.js', import.meta.url), 'utf8');
const clone = value => structuredClone(value);
function area(initial = {}) {
  const data = clone(initial);
  return { data, fail: false, async get(keys) {
    if (this.fail) throw new Error('private storage body');
    return Object.fromEntries(keys.filter(k => k in data).map(k => [k, clone(data[k])]));
  }, async set(values) { if (this.fail) throw new Error('private storage body'); Object.assign(data, clone(values)); } };
}
function fixture({ local = area(), session = area(), send } = {}) {
  const requests = [];
  const context = vm.createContext({ URL, Date, Promise, AbortController, crypto: webcrypto, setTimeout, clearTimeout,
    console: { warn() {} }, FETCH_TIMEOUT_MS: 500,
    chrome: { storage: { local, session }, runtime: { getManifest: () => ({ version: '2.26.11' }) } },
    config: async () => ({ endpoint: 'http://127.0.0.1:8787/ingest', token: 'private-token',
      refreshMinutes: 5, pageDelaySeconds: 15, sweepMode: 'none' }),
    readBotCheck: async () => ({ count: 0, lastAt: 0, pausedUntil: 0 }),
    normalizeSweepMode: x => x, checkedEndpoint: raw => raw,
    fetch: async (url, init) => {
      const report = JSON.parse(init.body); requests.push(report);
      const body = send ? await send(report) : { ok: true, receipt: { streamId: report.logging.streamId,
        lastSequence: report.logging.lastSequence, gapEvents: 0 } };
      return { ok: true, status: 200, async text() { return JSON.stringify(body); } };
    }
  });
  vm.runInContext(source + '\nthis.api = {recordDiagnostic, diagnosticReport, syncBrowserDiagnostics, cleanDiagnostic};', context);
  return { api: context.api, local, session, requests, context };
}

test('recorded events retain a persisted stream and increasing identity across worker restart', async () => {
  const first = fixture();
  await first.api.recordDiagnostic('page-loaded', { documentId: 10, lane: 'home' });
  const a = await first.api.diagnosticReport();
  assert.match(a.events[0].streamId, /^[0-9a-f]{32}$/);
  assert.equal(a.events[0].eventSeq, 1);
  const second = fixture({ local: first.local, session: first.session });
  await second.api.recordDiagnostic('page-loaded', { documentId: 11, lane: 'home' });
  const b = await second.api.diagnosticReport();
  assert.equal(b.events[1].streamId, a.events[0].streamId);
  assert.equal(b.events[1].eventSeq, 2);
  assert.equal(b.logging.recorded, 2);
});

test('storage failures report false, recover, and never count as a recorded sequence', async () => {
  const f = fixture();
  await f.api.diagnosticReport();
  f.local.fail = true;
  assert.equal((await f.api.recordDiagnostic('page-loaded')).recorded, false);
  f.local.fail = false;
  assert.equal((await f.api.recordDiagnostic('page-loaded')).recorded, true);
  const r = await f.api.diagnosticReport();
  assert.equal(r.logging.storageErrors, 1);
  assert.equal(r.logging.lastSequence, 1);
  assert.equal(r.logging.recorded, 1);
  assert.equal(r.events.length, 1);
  assert.equal(JSON.stringify(r).includes('private storage body'), false);
});

test('invalid events are counted and raw values never enter the report', async () => {
  const f = fixture();
  await f.api.recordDiagnostic('private error text', { url: 'private-url' });
  await f.api.recordDiagnostic('page-loaded', { requestId: 'invalid-private-id', url: 'private-url' });
  const r = await f.api.diagnosticReport();
  assert.equal(r.logging.rejected, 1);
  assert.equal(r.events.length, 1);
  assert.equal(r.events[0].requestId, undefined);
  assert.equal(JSON.stringify(r).includes('private'), false);
});

test('ring overwrites expose only unacknowledged loss and preserve the 300-event bound', async () => {
  const f = fixture();
  for (let i = 0; i < 305; i++) await f.api.recordDiagnostic('page-loaded', { page: i + 1 });
  const r = await f.api.diagnosticReport();
  assert.equal(r.events.length, 300);
  assert.equal(r.events[0].eventSeq, 6);
  assert.equal(r.logging.ringOverwrites, 5);
  assert.equal(r.logging.unacknowledgedOverwrites, 5);
  await f.api.syncBrowserDiagnostics();
  for (let i = 0; i < 5; i++) await f.api.recordDiagnostic('page-loaded');
  const acknowledged = await f.api.diagnosticReport();
  assert.equal(acknowledged.logging.ringOverwrites, 10);
  assert.equal(acknowledged.logging.unacknowledgedOverwrites, 5,
    'Evicting an archived event must not invent lost telemetry');
});

test('valid durable receipt updates acknowledgement without breaking unchanged mirror suppression', async () => {
  const f = fixture();
  await f.api.recordDiagnostic('page-loaded');
  assert.equal(await f.api.syncBrowserDiagnostics(), true);
  assert.equal(await f.api.syncBrowserDiagnostics(), true);
  assert.equal(f.requests.length, 1);
  const r = await f.api.diagnosticReport();
  assert.equal(r.logging.acknowledgedSequence, 1);
  assert.equal(r.logging.syncAttempts, 1);
  assert.equal(r.logging.syncFailures, 0);
});

for (const mutation of [r => ({ ...r, streamId: 'f'.repeat(32) }), r => ({ ...r, lastSequence: r.lastSequence + 1 }),
  r => ({ ...r, gapEvents: -1 })]) {
  test('an invalid or mismatched receipt cannot acknowledge diagnostic history', async () => {
    const f = fixture({ send: report => ({ ok: true, receipt: mutation({ streamId: report.logging.streamId,
      lastSequence: report.logging.lastSequence, gapEvents: 0 }) }) });
    await f.api.recordDiagnostic('page-loaded');
    assert.equal(await f.api.syncBrowserDiagnostics(), false);
    const r = await f.api.diagnosticReport();
    assert.equal(r.logging.acknowledgedSequence, 0);
    assert.equal(r.logging.syncFailures, 1);
    assert.equal(r.logging.lastSyncError, 'report');
  });
}

test('legacy backend success remains compatible without claiming archive acknowledgement', async () => {
  const f = fixture({ send: () => ({ ok: true }) });
  await f.api.recordDiagnostic('page-loaded');
  assert.equal(await f.api.syncBrowserDiagnostics(), true);
  assert.equal((await f.api.diagnosticReport()).logging.acknowledgedSequence, 0);
});

test('ingest telemetry keeps only fixed stages, outcomes and strict request IDs', () => {
  const f = fixture();
  const requestId = 'a'.repeat(32);
  const e = f.api.cleanDiagnostic('ingest-stage', { stage: 'complete', outcome: 'accepted-stale',
    intakePhase: 'late', requestId, queueMs: 42, documentId: 9, lane: 'keywords', round: 4,
    targetIndex: 5, batchCount: 2, url: 'private-url', message: 'private-error' }, 123);
  assert.ok(e);
  assert.equal(e.requestId, requestId);
  assert.equal(e.outcome, 'accepted-stale');
  assert.equal(e.queueMs, 42);
  assert.equal(e.url, undefined);
  assert.equal(e.message, undefined);
  assert.equal(f.api.cleanDiagnostic('ingest-stage', { stage: 'private', outcome: 'accepted' }), null);
});

const background = fs.readFileSync(new URL('../extension/background.js', import.meta.url), 'utf8');
function ingestFixture({ fetchImpl, authorization } = {}) {
  const events = [], calls = [];
  const context = vm.createContext({ URL, Date, Promise, AbortController, setTimeout, clearTimeout,
    console: { error() {} }, MAX_INGEST_ITEMS: 200, FETCH_TIMEOUT_MS: 500,
    DIAGNOSTIC_HEX: /^[0-9a-f]{32}$/,
    diagnosticIdentifier: () => webcrypto.randomUUID().replaceAll('-', ''),
    config: async () => ({ endpoint: 'http://127.0.0.1:8787/ingest', token: 'private-token' }),
    checkedEndpoint: raw => raw, pausedUntil: async () => 0,
    recordIngestFailure: async () => {}, networkFailureMessage: () => 'fixed failure',
    showDeliveryBadge: async () => {},
    chrome: { storage: { local: { async set() {} }, session: { async remove() {} } } },
    recordDiagnostic: async (event, details) => { events.push({ event, ...details }); return { recorded: true }; },
    fetch: async (url, init) => { calls.push({ url, ...init });
      if (fetchImpl) return fetchImpl(init, calls.length);
      return { ok: true, status: 200, async text() { return JSON.stringify({ seen: JSON.parse(init.body).items.length }); } };
    },
    auxiliaryScans: { async ownsTab() { return false; } },
    authorizedRole: authorization || (async () => ({ authorized: true, tabId: 1, state: { tabId: 1 } })),
    roleFor: () => ({ generation: 7, active: true })
  });
  const functions = background.slice(background.indexOf('function batchLabel('),
    background.indexOf('// --- reviewer-tab coordinator'));
  const page = background.slice(background.indexOf('async function handlePageReport('),
    background.indexOf('async function navigateToPageOne('));
  vm.runInContext(functions + '\n' + page + '\nthis.api = {enqueuePost, handlePageReport};', context);
  return { api: context.api, events, calls, context };
}
const products = count => Array.from({ length: count }, (_, i) => ({ item_id: `ip-${1000000 + i}`, available: true }));

test('one page attempt joins bounded batches to its request ID and numeric scan context', async () => {
  const f = ingestFixture();
  const result = await f.api.enqueuePost(products(401), 3, async () => true,
    { documentId: 900, lane: 'keywords', tabId: 1, generation: 7, round: 8, targetIndex: 9, intakePhase: 'late' });
  assert.equal(result.ok, true);
  assert.match(result.requestId, /^[0-9a-f]{32}$/);
  assert.equal(f.calls.length, 3);
  assert.ok(f.calls.every(c => c.headers['X-Request-ID'] === result.requestId));
  const batches = f.events.filter(e => e.event === 'ingest-stage' && e.stage === 'batch');
  assert.deepEqual(batches.map(e => e.items), [200, 200, 1]);
  assert.deepEqual(batches.map(e => e.batchIndex), [1, 2, 3]);
  assert.ok(batches.every(e => e.documentId === 900 && e.requestId === result.requestId &&
    e.targetIndex === 9 && e.round === 8 && e.intakePhase === 'late' && e.outcome === 'accepted'));
  assert.ok(batches.every(e => Number.isFinite(e.queueMs) && e.queueMs >= 0));
});

test('accepted HTTP work remains visible when its final caller authorization expires', async () => {
  let allowed = true;
  const f = ingestFixture({ fetchImpl(init) {
    allowed = false;
    return { ok: true, status: 200, async text() { return JSON.stringify({ seen: JSON.parse(init.body).items.length }); } };
  } });
  const r = await f.api.enqueuePost(products(1), 1, async () => allowed, { documentId: 9, lane: 'home' });
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'inactive');
  assert.ok(f.events.some(e => e.stage === 'batch' && e.outcome === 'accepted'));
  assert.ok(f.events.some(e => e.stage === 'complete' && e.outcome === 'accepted-stale' && e.requestId === r.requestId));
});

test('partial batch acceptance followed by transport failure remains a retry, not stale success', async () => {
  const f = ingestFixture({ fetchImpl(init, index) {
    if (index === 2) throw new Error('private failure text');
    return { ok: true, status: 200, async text() { return JSON.stringify({ seen: JSON.parse(init.body).items.length }); } };
  } });
  const r = await f.api.enqueuePost(products(201), 1, async () => true, { documentId: 9, lane: 'home' });
  assert.equal(r.ok, false);
  assert.ok(f.events.some(e => e.stage === 'batch' && e.outcome === 'network'));
  assert.ok(f.events.some(e => e.stage === 'complete' && e.outcome === 'retry'));
  assert.equal(f.events.some(e => e.outcome === 'accepted-stale'), false);
});

test('a successful post followed by a stale page acknowledgement never authorizes old progress', async () => {
  let checks = 0;
  const f = ingestFixture({ authorization: async () => ({ authorized: ++checks === 1,
    tabId: 1, state: { tabId: 1 }, scopeAllowed: false }) });
  vm.runInContext("enqueuePost = async () => ({ok:true,requestId:'a'.repeat(32),summary:{seen:1}})", f.context);
  const r = await f.api.handlePageReport({ generation: 7, documentId: 90, page: 1, items: products(1) }, { tab: { id: 1 } });
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'stale-generation');
  assert.equal(f.events.some(e => e.event === 'page-delivered'), false);
  assert.ok(f.events.some(e => e.outcome === 'accepted-stale' && e.documentId === 90 && e.requestId === 'a'.repeat(32)));
});

test('content diagnostics always include document, lane and opaque target context', async () => {
  const text = fs.readFileSync(new URL('../extension/content.js', import.meta.url), 'utf8');
  const fn = text.slice(text.indexOf('function diagnostic('), text.indexOf('// Local diagnostic evidence'));
  const messages = [];
  const context = vm.createContext({ sendToWorker: async value => messages.push(value), RESULTS_DOCUMENT_ID: 123,
    currentPage: () => 4, role: { generation: 7, auxiliary: { lane: 'keywords', round: 8, targetIndex: 9, attempt: 2 } } });
  vm.runInContext(fn + "\ndiagnostic('navigation-native',{targetPage:5});", context);
  assert.deepEqual(clone(messages[0].details), { targetPage: 5, page: 4, generation: 7, documentId: 123,
    lane: 'keywords', round: 8, targetIndex: 9, attempt: 2 });
});

test('partial durable acknowledgement leaves its unarchived tail eligible for retry', async () => {
  const f = fixture({ send: report => ({ ok: true, receipt: { streamId: report.logging.streamId,
    lastSequence: 0, gapEvents: 0 } }) });
  await f.api.recordDiagnostic('page-loaded');
  assert.equal(await f.api.syncBrowserDiagnostics(), true);
  assert.equal(await f.api.syncBrowserDiagnostics(), true);
  assert.equal(f.requests.length, 2);
  assert.equal((await f.api.diagnosticReport()).logging.acknowledgedSequence, 0);
});

test('verification phase tracing is change-only and retains safe result timing without raw controller state', async () => {
  const f = fixture();
  const body = background.slice(background.indexOf('function recordVerificationChanges('), background.indexOf('const auxiliaryScans'));
  const changes = [
    { verificationAssistStatus: { newValue: { code: 'watching', at: 1 } } },
    { verificationAssistStatus: { oldValue: { code: 'watching', at: 1 }, newValue: { code: 'watching', at: 2 } } },
    { verificationAssistLastResult: { newValue: { outcome: 'starting', at: 3, attempt: 2, key: '42:123456' } } },
    { verificationAssistLastResult: { oldValue: { outcome: 'starting', at: 3, attempt: 2, key: '42:123456' },
      newValue: { outcome: 'input-error', at: 4, attempt: 2, key: '42:123456', holdMs: 15000,
        errorStage: 'move', errorReason: 'timeout', cleanupConfirmed: true, account: 'private-account',
        target: { x: 123, y: 456 }, raw: 'private-page-text' } } }
  ];
  // The observer consumes the real sanitizer/recorder, independently of input.
  const context = f.context;
  vm.runInContext(body, context);
  for (const change of changes) context.recordVerificationChanges(change);
  const r = await f.api.diagnosticReport();
  assert.equal(r.events.length, 3, 'Repeated idle phase changes must not generate events');
  const result = r.events[2];
  assert.equal(result.outcome, 'input-error');
  assert.equal(result.durationMs, 15000);
  assert.equal(result.attempt, 2);
  assert.equal(result.tabId, 42);
  assert.equal(result.episodeAt, 123456);
  assert.equal(result.errorReason, 'timeout');
  assert.equal(result.cleanupConfirmed, true);
  assert.equal(JSON.stringify(r).includes('private-'), false);
  assert.equal('target' in result, false);
});

test('auxiliary startup attribution uses its existing assignment before the first content role reply', async () => {
  const recorded = [];
  const context = vm.createContext({ auxiliaryScans: { async ownsTab() { return true; },
    async handle() { return { generation: 9, auxiliary: { lane: 'keywords', round: 2,
      targetIndex: 4, attempt: 1, targetKey: 'private-query' } }; } },
    recordDiagnostic: async (event, details) => recorded.push({ event, ...details }) });
  vm.runInContext(background.slice(background.indexOf('async function recordContentDiagnostic('),
    background.indexOf('function respondAsync(')), context);
  await context.recordContentDiagnostic({ event: 'page-loaded', details: { generation: 0,
    lane: 'home', documentId: 123, page: 1 } }, { tab: { id: 42 } });
  assert.deepEqual(clone(recorded[0]), { event: 'page-loaded', generation: 9, lane: 'keywords',
    documentId: 123, page: 1, tabId: 42, round: 2, targetIndex: 4, attempt: 1 });
});
