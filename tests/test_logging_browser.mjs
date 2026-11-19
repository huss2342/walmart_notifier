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
  let now = Date.now();
  class Clock extends Date { static now() { return now; } }
  const context = vm.createContext({ URL, Date: Clock, Promise, AbortController, TextEncoder, crypto: webcrypto, setTimeout, clearTimeout,
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
  vm.runInContext(source + '\nthis.api = {recordDiagnostic, diagnosticReport, syncBrowserDiagnostics, cleanDiagnostic,' +
    'diagnosticTargetId: (...a) => diagnosticTargetId(...a), recordScannerRuntime: (...a) => recordScannerRuntime(...a),' +
    'recordScannerRecovery: (...a) => recordScannerRecovery(...a)};', context);
  return { api: context.api, local, session, requests, context, setNow(value) { now = value; } };
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

const scannerState = () => ({ phase: 'waiting-focus', focusReason: 'waiting-turn', visibility: 'hidden',
  reachable: true, active: true, busy: false, highTraffic: false, paused: false, enabled: true,
  progressAt: 90_000, lastActivityAt: 99_000, nextActionAt: 105_000 });

test('scanner runtime and recovery retain only fixed labels, timestamps and stable hashed target identity', async () => {
  const f = fixture(); f.setNow(100_000);
  const key = 'keyword:Water Filter';
  const targetId = await f.api.diagnosticTargetId(key);
  assert.match(targetId, /^[0-9a-f]{32}$/);
  assert.equal(targetId, await f.api.diagnosticTargetId(key));
  assert.equal(targetId, '4098ca189c4e094477345c14831a9496');
  const state = scannerState();
  assert.deepEqual(clone(f.api.cleanDiagnostic('scanner-state', { lane: 'home', state, targetId,
    targetKey: 'private-query', url: 'private-url' })), { at: 100_000, event: 'scanner-state', lane: 'home', state, targetId });
  assert.equal(f.api.cleanDiagnostic('scanner-state', { lane: 'home', state: { ...state, phase: 'private-error' } }), null);
  assert.equal(f.api.cleanDiagnostic('scanner-state', { state }), null);
  await f.api.recordScannerRecovery('home', 'progress-timeout', 'deferred', { probe: {
    ready: true, active: true, busy: false, highTraffic: false, phase: 'waiting-focus',
    focusReason: 'waiting-turn', visibility: 'hidden', lastActivityAt: 99_000, nextActionAt: 105_000,
    private: 'never-record' }, progressAt: 90_000, targetKey: key, tabId: 7, generation: 2, url: 'never-record' });
  const r = await f.api.diagnosticReport();
  assert.equal(r.events[0].event, 'scanner-recovery');
  assert.equal(r.events[0].outcome, 'deferred');
  assert.equal(r.events[0].targetId, targetId);
  assert.deepEqual(clone(r.events[0].state), state);
  assert.equal(JSON.stringify(r).includes('never-record'), false);
  assert.equal(JSON.stringify(r).includes(key), false);
});

test('unchanged runtime timestamps cannot defeat the persisted one-minute heartbeat gate', async () => {
  const f = fixture(); f.setNow(100_000);
  const context = { probe: { ready: true, active: true, phase: 'waiting-focus', focusReason: 'waiting-turn',
    visibility: 'hidden', lastActivityAt: 99_000, nextActionAt: 105_000 }, progressAt: 90_000,
  tabId: 7, generation: 2, page: 3, targetKey: 'keyword:water filter' };
  const first = await f.api.recordScannerRuntime('keywords', context);
  assert.equal(first.recorded, true);
  f.setNow(130_000); context.probe.lastActivityAt = 130_000; context.probe.nextActionAt = 135_000;
  assert.equal((await f.api.recordScannerRuntime('keywords', context)).suppressed, true);
  const restarted = fixture({ local: f.local, session: f.session }); restarted.setNow(159_999);
  assert.equal((await restarted.api.recordScannerRuntime('keywords', context)).suppressed, true);
  restarted.setNow(160_000);
  assert.equal((await restarted.api.recordScannerRuntime('keywords', context)).recorded, true);
  const r = await restarted.api.diagnosticReport();
  assert.equal(r.events.length, 2);
  assert.equal(r.events[1].state.lastActivityAt, 130_000);
  restarted.setNow(160_001); context.progressAt = 160_001;
  assert.equal((await restarted.api.recordScannerRuntime('keywords', context)).recorded, true,
    'Acknowledged useful progress is a state transition');
});

test('concurrent summaries coalesce and failed writes do not suppress the next usable observation', async () => {
  const f = fixture(); f.setNow(100_000);
  const context = { probe: { ready: true, phase: 'collecting' }, generation: 4, progressAt: 90_000 };
  const results = await Promise.all(Array.from({ length: 10 }, () => f.api.recordScannerRuntime('home', context)));
  assert.equal(results.filter(result => result.recorded).length, 1);
  f.local.fail = true;
  assert.equal((await f.api.recordScannerRecovery('home', 'stale-document', 'unavailable', context)).recorded, false);
  context.probe.phase = 'waiting-focus';
  assert.equal((await f.api.recordScannerRuntime('home', context)).recorded, false);
  f.local.fail = false;
  assert.equal((await f.api.recordScannerRuntime('home', context)).recorded, true);
  f.session.fail = true;
  assert.equal((await f.api.recordScannerRuntime('home', context)).recorded, false);
  f.session.fail = false;
  f.setNow(160_000);
  assert.equal((await f.api.recordScannerRuntime('home', context)).recorded, true);
});

test('invalid scanner recovery reasons and malformed state numbers never reach history', async () => {
  const f = fixture();
  for (const change of [{ busy: 1 }, { progressAt: 1.5 }, { nextActionAt: Infinity }, { visibility: 'private' }]) {
    assert.equal(f.api.cleanDiagnostic('scanner-state', { lane: 'home', state: { ...scannerState(), ...change } }), null);
  }
  for (const change of [{ reason: 'private-error', outcome: 'accepted' }, { reason: 'progress-timeout', outcome: 'private' }]) {
    assert.equal(f.api.cleanDiagnostic('scanner-recovery', { lane: 'home', state: scannerState(), ...change }), null);
  }
  assert.equal(await f.api.diagnosticTargetId(null), null);
  assert.equal(await f.api.diagnosticTargetId(''), null);
  f.context.crypto = {};
  assert.equal(await f.api.diagnosticTargetId('another target'), null);
});

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

test('direct reads keep their lane, page totals and fast intake phase, nothing private', () => {
  const f = fixture();
  for (const lane of ['newest', 'value', 'requests', 'priority', 'departments']) {
    const e = f.api.cleanDiagnostic('fast-read', { lane, outcome: 'items', page: 2, items: 40, new: 1,
      totalPages: 25, targetIndex: 7, count: 1840, durationMs: 900, query: 'private search',
      url: 'https://www.walmart.com/reviews/claim-product?catId=4044' }, 123);
    assert.ok(e, lane);
    assert.deepEqual([e.lane, e.totalPages, e.targetIndex, e.count], [lane, 25, 7, 1840]);
    assert.equal(e.query, undefined);
    assert.equal(e.url, undefined);
  }
  assert.equal(f.api.cleanDiagnostic('fast-read', { lane: 'home', outcome: 'items' }), null);
  assert.equal(f.api.cleanDiagnostic('ingest-stage', { stage: 'queued', outcome: 'waiting',
    intakePhase: 'fast', lane: 'value' }, 1).intakePhase, 'fast');
  assert.equal(f.api.cleanDiagnostic('ingest-stage', { stage: 'queued', outcome: 'waiting',
    intakePhase: 'burst', lane: 'value' }, 1).intakePhase, undefined);
});

test('restock-watch keeps the evidence behind a "Back in stock": ids, fixed labels and timings only', () => {
  const f = fixture();
  const e = f.api.cleanDiagnostic('restock-watch', { itemId: 'ip-20755219869', stage: 'returned',
    stockState: 'in-stock', stockSource: 'data', signalV2: 'in', signalStatus: 'out', signalOos: false,
    variant: true, conflict: true, valueCents: 19999, awayMs: 240000, sinceOutMs: 60000, outReads: 2,
    lane: 'value80', page: 2, requestId: 'a'.repeat(32), title: 'private title', url: 'https://private.example',
    signalOther: 'IN_STOCK' }, 123);
  assert.ok(e);
  assert.deepEqual([e.itemId, e.stage, e.stockState, e.stockSource, e.signalV2, e.signalStatus, e.signalOos,
    e.variant, e.conflict, e.valueCents, e.awayMs, e.sinceOutMs, e.outReads, e.lane, e.page],
  ['ip-20755219869', 'returned', 'in-stock', 'data', 'in', 'out', false, true, true, 19999, 240000, 60000, 2,
    'value80', 2]);
  assert.equal(e.requestId, 'a'.repeat(32));
  for (const field of ['title', 'url', 'signalOther']) assert.equal(e[field], undefined, field);
  // Unknown labels are dropped, never copied through.
  const odd = f.api.cleanDiagnostic('restock-watch', { itemId: 'ip-20755219869', stage: 'followup',
    stockState: 'absent', signalV2: 'IN_STOCK', signalStatus: 'maybe', signalOos: 'no', stockSource: 'page' }, 1);
  assert.deepEqual([odd.signalV2, odd.signalStatus, odd.signalOos, odd.stockSource], [undefined, undefined, undefined, undefined]);
  for (const bad of [{ itemId: 'ip-1' }, { itemId: '20755219869' }, { stage: 'armed' }, { stockState: 'gone' }]) {
    assert.equal(f.api.cleanDiagnostic('restock-watch', { itemId: 'ip-20755219869', stage: 'returned',
      stockState: 'in-stock', ...bad }, 1), null, JSON.stringify(bad));
  }
});

test('direct reads log their stock mix, size, CDN age and product count', () => {
  const f = fixture();
  const e = f.api.cleanDiagnostic('fast-read', { lane: 'value80', outcome: 'items', page: 2, items: 41,
    stockIn: 31, stockOut: 10, stockOther: 0, bytes: 1203425, ageSeconds: 37, products: 41 }, 123);
  assert.deepEqual([e.stockIn, e.stockOut, e.stockOther, e.bytes, e.ageSeconds, e.products], [31, 10, 0, 1203425, 37, 41]);
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
    restockWatch: { mark: async items => items },
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

test('content diagnostics include internal target identity for hashing at the recorder boundary', async () => {
  const text = fs.readFileSync(new URL('../extension/content.js', import.meta.url), 'utf8');
  const fn = text.slice(text.indexOf('function diagnostic('), text.indexOf('// Local diagnostic evidence'));
  const messages = [];
  const context = vm.createContext({ sendToWorker: async value => messages.push(value), RESULTS_DOCUMENT_ID: 123,
    currentPage: () => 4, role: { generation: 7, auxiliary: { lane: 'keywords', targetKey: 'keyword:water filter',
      round: 8, targetIndex: 9, attempt: 2 } } });
  vm.runInContext(fn + "\ndiagnostic('navigation-native',{targetPage:5});", context);
  assert.deepEqual(clone(messages[0].details), { targetPage: 5, page: 4, generation: 7, documentId: 123,
    lane: 'keywords', targetKey: 'keyword:water filter', round: 8, targetIndex: 9, attempt: 2 });
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

test('the stock evidence reaches the restock watch with its lane and request, and never the backend', async () => {
  const f = ingestFixture();
  const seen = [];
  f.context.restockWatch = { mark: async (items, context) => { seen.push(context); return items; } };
  const result = await f.api.enqueuePost([{ item_id: 'ip-20755219869', value_usd: 199.99, available: true,
    stock: { source: 'data', v2: 'in', status: 'in', oos: false, variant: true } }], 2, async () => true,
  { lane: 'value80', intakePhase: 'fast' });
  assert.equal(result.ok, true);
  assert.deepEqual([seen[0].lane, seen[0].page, seen[0].requestId], ['value80', 2, result.requestId]);
  const sent = JSON.parse(f.calls[0].body).items[0];
  assert.equal(sent.item_id, 'ip-20755219869');
  assert.equal('stock' in sent, false, 'evidence is for the local log only');
});

test('a held restock is not sent until confirmed, and the fast reader is told how many were held', async () => {
  const f = ingestFixture();
  f.context.restockWatch = { mark: async items => items.map(entry => entry.item_id === 'ip-20689613492'
    ? { ...entry, restockHold: true } : entry) };
  const result = await f.api.enqueuePost([
    { item_id: 'ip-20689613492', value_usd: 269.99, available: true, stock: { source: 'data', v2: 'in' } },
    { item_id: 'ip-1234567', value_usd: 99, available: true },
    { item_id: 'ip-7654321', value_usd: 99, available: false }], 1, async () => true, { lane: 'value' });
  assert.equal(result.ok, true);
  assert.equal(result.held, 1);
  const sent = JSON.parse(f.calls[0].body).items;
  assert.deepEqual(sent.map(entry => entry.item_id), ['ip-1234567'], 'held and out-of-stock offers stay local');
  assert.ok(sent.every(entry => !('restockHold' in entry) && !('stock' in entry)));
  // A page whose only offer is held sends nothing at all, and still reports the hold.
  const lone = ingestFixture();
  lone.context.restockWatch = { mark: async items => items.map(entry => ({ ...entry, restockHold: true })) };
  const only = await lone.api.enqueuePost([{ item_id: 'ip-20689613492', value_usd: 269.99, available: true }], 1,
    async () => true, { lane: 'value' });
  assert.deepEqual([only.ok, only.held, lone.calls.length], [true, 1, 0]);
});
