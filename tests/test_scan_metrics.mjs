import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../extension/diagnostics.js', import.meta.url), 'utf8');
const START = 1_793_000_000_000;

function worker({ data = {}, at = START, failStorage = false,
  check = { count: 0, lastAt: 0, pausedUntil: 0 }, beforeGet = async () => {} } = {}) {
  let now = at;
  const storage = structuredClone(data);
  const warnings = [];
  class Clock extends Date { static now() { return now; } }
  const context = vm.createContext({
    Date: Clock, console: { warn: value => warnings.push(value) },
    config: async () => ({ refreshMinutes: 5, pageDelaySeconds: 15, sweepMode: 'none' }),
    readBotCheck: async () => check,
    normalizeSweepMode: value => value,
    chrome: {
      runtime: { getManifest: () => ({ version: '2.23.14' }) },
      storage: { local: {
        get: async keys => {
          if (failStorage) throw new Error('private storage failure');
          await beforeGet(keys);
          return Object.fromEntries(keys.map(key => [key, structuredClone(storage[key])]));
        },
        set: async values => {
          if (failStorage) throw new Error('private storage failure');
          Object.assign(storage, structuredClone(values));
        }
      } }
    }
  });
  vm.runInContext(`${source}\nthis.api = { recordDiagnostic, diagnosticReport };`, context);
  return {
    record: (event, details) => context.api.recordDiagnostic(event, details),
    report: async () => structuredClone(await context.api.diagnosticReport()),
    advance: ms => { now += ms; }, storage, warnings
  };
}

test('successful transitions exclude duplicate card delivery and each tab initial page', async () => {
  const w = worker();
  await w.record('results-page-ready', { tabId: 1, documentId: START, page: 1 });
  await w.record('results-page-ready', { tabId: 1, documentId: START, page: 1 });
  await w.record('page-delivered', { tabId: 1, page: 1, items: 48 });
  await w.record('results-page-ready', { tabId: 2, documentId: START, page: 4 });
  await w.record('results-page-ready', { tabId: 1, documentId: START, page: 2 });
  w.advance(1);
  await w.record('results-page-ready', { tabId: 1, documentId: START + 1, page: 2 });
  await w.record('results-page-ready', { tabId: 1, documentId: START, page: 3 });
  const { metrics } = await w.report();
  assert.equal(metrics.initialPages, 2);
  assert.equal(metrics.successfulTransitions, 2, 'SPA and full document each count, stale document does not');
  assert.equal(metrics.episodesPer100Transitions, 0);
});

test('worker restart retains page identity, counters and open verification downtime', async () => {
  const first = worker();
  await first.record('results-page-ready', { tabId: 1, documentId: START, page: 1 });
  await first.record('challenge', { tabId: 1 });
  first.advance(1000);
  await first.record('challenge', { tabId: 2 });
  assert.equal((await first.report()).metrics.verificationDowntimeMs, 1000);
  const second = worker({ data: first.storage, at: START + 3000 });
  await second.record('results-page-ready', { tabId: 1, documentId: START, page: 1 });
  await second.record('challenge', { tabId: 2 });
  let metrics = (await second.report()).metrics;
  assert.equal(metrics.initialPages, 1);
  assert.equal(metrics.successfulTransitions, 0);
  assert.equal(metrics.challengeEpisodes, 1);
  assert.equal(metrics.verificationDowntimeMs, 3000);
  assert.equal(metrics.completedVerificationDowntimeMs, 0);
  assert.equal(metrics.verificationActiveSince, START);
  assert.equal(metrics.episodesPer100Transitions, null);
  await second.record('verification-resumed');
  second.advance(500);
  await second.record('verification-resumed');
  metrics = (await second.report()).metrics;
  assert.equal(metrics.verificationDowntimeMs, 3000);
  assert.equal(metrics.completedVerificationDowntimeMs, 3000);
  assert.equal(metrics.verificationActiveSince, 0);
  await second.record('challenge');
  second.advance(500);
  await second.record('results-page-ready', { tabId: 1, documentId: START, page: 2 });
  metrics = (await second.report()).metrics;
  assert.equal(metrics.challengeEpisodes, 2);
  assert.equal(metrics.verificationDowntimeMs, 3500);
  assert.equal(metrics.episodesPer100Transitions, 200);
});

test('new metrics never reconstruct an incomplete baseline from retained history', async () => {
  const w = worker({ data: { reviewerDiagnostics: [
    { at: START - 1000, event: 'challenge' },
    { at: START - 500, event: 'navigation-fallback', reason: 'native-backoff' }
  ] } });
  const { metrics, events } = await w.report();
  assert.equal(metrics.measurementStartedAt, START);
  assert.equal(metrics.challengeEpisodes, 0);
  assert.equal(metrics.urlFallbacks, 0);
  assert.equal(metrics.verificationDowntimeMs, 0);
  assert.equal(events.length, 2);
});

test('upgrade during an open check counts one observed episode and measures only the new window', async () => {
  const check = { count: 17, lastAt: START - 60_000, pausedUntil: START - 1000 };
  const w = worker({ check });
  // A page-load diagnostic may establish the baseline before the first export.
  await w.record('page-loaded', { tabId: 1 });
  w.advance(1000);
  let metrics = (await w.report()).metrics;
  assert.equal(metrics.measurementStartedAt, START);
  assert.equal(metrics.challengeEpisodes, 1, 'never imports the historical17 count');
  assert.equal(metrics.verificationActiveSince, START);
  assert.equal(metrics.verificationDowntimeMs, 1000);
  await w.record('challenge', { tabId: 2 });
  w.advance(500);
  metrics = (await w.report()).metrics;
  assert.equal(metrics.challengeEpisodes, 1);
  assert.equal(metrics.verificationDowntimeMs, 1500);
  await w.record('verification-resumed');
  metrics = (await w.report()).metrics;
  assert.equal(metrics.challengeEpisodes, 1);
  assert.equal(metrics.verificationActiveSince, 0, 'a stale check snapshot cannot reopen an observed recovery');
});

test('an event queued behind slow report initialization retains the measurement boundary', async () => {
  let releaseRead;
  let enteredRead;
  const gate = new Promise(resolve => { releaseRead = resolve; });
  const entered = new Promise(resolve => { enteredRead = resolve; });
  let block = true;
  const w = worker({
    check: { count: 17, lastAt: START - 60_000, pausedUntil: START + 60_000 },
    beforeGet: async keys => {
      if (block && keys.includes('reviewerScanMetrics')) {
        block = false;
        enteredRead();
        await gate;
      }
    }
  });
  const firstReport = w.report();
  await entered;
  const pendingEvent = w.record('results-page-ready', { tabId: 1, documentId: START, page: 1 });
  w.advance(1000);
  releaseRead();
  const initial = (await firstReport).metrics;
  await pendingEvent;
  w.advance(500);
  let metrics = (await w.report()).metrics;
  assert.equal(metrics.measurementStartedAt, initial.measurementStartedAt);
  assert.equal(metrics.measurementStartedAt, START + 1000);
  assert.equal(metrics.initialPages, 1);
  assert.equal(metrics.challengeEpisodes, 1);
  assert.equal(metrics.verificationActiveSince, START + 1000);
  assert.equal(metrics.verificationDowntimeMs, 500);
  await w.record('verification-resumed');
  metrics = (await w.report()).metrics;
  assert.equal(metrics.completedVerificationDowntimeMs, 500);
  assert.equal(metrics.verificationDowntimeMs, 500);
  assert.equal(metrics.verificationActiveSince, 0);
});

test('rate counters distinguish attempts, stalled retries and full-load fallback reasons', async () => {
  const w = worker();
  await Promise.all([
    w.record('navigation-native'), w.record('navigation-native'),
    w.record('navigation-retry', { reason: 'native-stalled', durationMs: 1000, targetPage: 2 }),
    w.record('navigation-fallback', { reason: 'native-backoff' }),
    w.record('navigation-fallback', { reason: 'native-stalled' }),
    w.record('navigation-fallback', { reason: 'missing-control' })
  ]);
  const { metrics, events } = await w.report();
  assert.equal(metrics.nativeAttempts, 2);
  assert.equal(metrics.nativeRetries, 1);
  assert.equal(metrics.urlFallbacks, 3);
  assert.equal(metrics.nativeBackoffFallbacks, 1);
  assert.equal(metrics.nativeStalledFallbacks, 1);
  assert.equal(events.find(event => event.event === 'navigation-retry').durationMs, 1000);
});

test('durable counters survive history truncation and never export page identities or strings', async () => {
  const w = worker();
  await w.record('results-page-ready', { tabId: 9, documentId: START, page: 1,
    url: 'private-url', title: 'private-title', token: 'private-token' });
  for (let index = 0; index < 320; index++) await w.record('navigation-native');
  const snapshot = await w.report();
  assert.equal(snapshot.events.length, 300);
  assert.equal(snapshot.metrics.nativeAttempts, 320);
  assert.equal(snapshot.metrics.initialPages, 1);
  assert.equal('tabs' in snapshot.metrics, false);
  assert.equal('documentId' in snapshot.metrics, false);
  for (const privateText of ['private-url', 'private-title', 'private-token']) {
    assert.equal(JSON.stringify(w.storage).includes(privateText), false);
    assert.equal(JSON.stringify(snapshot).includes(privateText), false);
  }
});

test('tab identities remain bounded and malformed metric storage starts a fresh baseline', async () => {
  const w = worker({ data: { reviewerScanMetrics: { measurementStartedAt: -1,
    token: 'private-token', tabs: [] } } });
  for (let tabId = 1; tabId <= 70; tabId++) {
    await w.record('results-page-ready', { tabId, documentId: START, page: 1 });
  }
  const metrics = (await w.report()).metrics;
  assert.equal(metrics.initialPages, 70);
  assert.ok(w.storage.reviewerScanMetrics.tabs.length <= 64);
  assert.equal(metrics.measurementStartedAt, START);
  assert.equal(JSON.stringify(w.storage.reviewerScanMetrics).includes('private-token'), false);
});

test('diagnostic storage failures remain nonfatal and never disclose the failure text', async () => {
  const w = worker({ failStorage: true });
  await assert.doesNotReject(w.record('results-page-ready', { tabId: 1, documentId: START, page: 1 }));
  assert.equal(w.warnings.length, 1);
  assert.equal(w.warnings[0].includes('private'), false);
});
