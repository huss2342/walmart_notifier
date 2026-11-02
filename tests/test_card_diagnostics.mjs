import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../extension/diagnostics.js', import.meta.url), 'utf8');
const controls = fs.readFileSync(new URL('../extension/scanner-control.js', import.meta.url), 'utf8');
function cleaner() {
  const context = vm.createContext({ Date, Promise });
  vm.runInContext(`${source}\n${controls}\nthis.clean = cleanDiagnostic;`, context);
  return (...args) => JSON.parse(JSON.stringify(context.clean(...args)));
}
const sample = { itemId: 'ip-21064716574', decision: 'accepted', cardFound: true,
  free: true, outOfStock: false, valueCents: null };
const evidence = { lane: 'keywords', documentId: 1234, page: 11, generation: 2,
  items: 1, acceptedCount: 1, rejectedCount: 0, variantCount: 0,
  outOfStockCount: 0, missingValueCount: 1, samples: [sample] };

test('priority lane evidence survives cleaning without page or query data', () => {
  const clean = cleaner();
  const result = clean('card-extraction', { ...evidence, lane: 'priority',
    query: 'private-query', url: 'private-url', token: 'private-token' }, 99);
  assert.deepEqual(result, { at: 99, event: 'card-extraction', ...evidence, lane: 'priority' });
  assert.deepEqual(clean(result.event, result, result.at), result);
  assert.deepEqual(clean('auxiliary-target-start', { lane: 'priority', round: 2,
    targetIndex: 3, query: 'private-query' }, 99),
  { at: 99, event: 'auxiliary-target-start', lane: 'priority', round: 2, targetIndex: 3 });
});

test('focus and pagination diagnostics retain only bounded performance context', () => {
  const clean = cleaner();
  for (const event of ['focus-granted', 'focus-denied', 'focus-released', 'pager-clamped', 'page-repeat']) {
    const result = clean(event, { generation: 2, page: 85, targetPage: 25,
      durationMs: 203, elapsedMs: 5000, operation: 'page-scroll', reason: 'waiting-turn',
      url: 'https://private.example', leaseId: 'private-identity' }, 99);
    assert.ok(result, event);
    assert.equal(result.url, undefined);
    assert.equal(result.leaseId, undefined);
    assert.equal(result.durationMs, 203);
    if (event.startsWith('focus-')) {
      assert.equal(result.operation, 'page-scroll');
      assert.equal(result.reason, 'waiting-turn');
      assert.equal(clean(event, { operation: 'private-operation' }), null);
    }
  }
});

test('card evidence survives sanitization without raw page or account data', () => {
  const clean = cleaner();
  const result = clean('card-extraction', { ...evidence, token: 'secret', query: 'private-query',
    url: 'https://private.example', samples: [{ ...sample, title: 'private-title', html: '<secret>' }] }, 99);
  assert.deepEqual(result, { at: 99, event: 'card-extraction', documentId: 1234, page: 11,
    generation: 2, items: 1, acceptedCount: 1, rejectedCount: 0, variantCount: 0,
    outOfStockCount: 0, missingValueCount: 1, lane: 'keywords', samples: [sample] });
});

test('card evidence rejects invalid labels and numbers and bounds samples', () => {
  const clean = cleaner();
  assert.equal(clean('card-extraction', { ...evidence, lane: 'private-lane' }), null);
  assert.equal(clean('card-extraction', { ...evidence, acceptedCount: true }), null);
  const samples = [
    { ...sample, itemId: 'private-id' }, { ...sample, free: 'true' },
    { ...sample, decision: 'private-message' }, { ...sample, valueCents: 1.5 },
    { ...sample, valueCents: -1 }, { ...sample, outOfStock: undefined },
    ...Array.from({ length: 20 }, () => sample)
  ];
  const result = clean('card-extraction', { ...evidence, samples });
  assert.equal(result.samples.length, 6);
  assert.ok(result.samples.every(value => value.itemId === sample.itemId));
});

test('auxiliary lane events retain useful fixed context and drop arbitrary errors', () => {
  const clean = cleaner();
  for (const event of ['auxiliary-target-start', 'auxiliary-target-skipped',
    'auxiliary-page-delivered', 'auxiliary-error']) {
    assert.deepEqual(clean(event, { lane: 'departments', tabId: 42, generation: 7,
      reason: 'secret-error', target: 'private-target', items: 20 }, 99),
    { at: 99, event, tabId: 42, generation: 7, items: 20, lane: 'departments',
      ...(['auxiliary-error', 'auxiliary-target-skipped'].includes(event)
        ? { failureCode: 'unexpected-error' } : {}) });
    assert.equal(clean(event, { lane: 'private-lane' }), null);
  }
});

test('auxiliary failures retain classified causes across history sanitization', () => {
  const clean = cleaner();
  const first = clean('auxiliary-error', { lane: 'keywords',
    reason: 'Reviewer filter: missing-control, token=private' }, 99);
  assert.equal(first.failureCode, 'unexpected-error');
  const event = clean('auxiliary-target-skipped', { lane: 'departments',
    reason: 'Selected filter did not produce a fresh results page' }, 99);
  assert.equal(event.failureCode, 'selection-render-timeout');
  assert.deepEqual(clean(event.event, event, event.at), event);
});

test('malformed history cannot produce a report the backend rejects', () => {
  const clean = cleaner();
  assert.deepEqual(clean('card-extraction', { ...evidence,
    samples: [{ ...sample, itemId: ['ip-21064716574'] }] }).samples, []);
  const event = clean('page-delivered', { page: 2, items: 1, acceptedCount: 1,
    samples: [sample] }, 99);
  assert.deepEqual(event, { at: 99, event: 'page-delivered', page: 2, items: 1 });
});

test('unresolved coverage retains numeric page evidence without claiming sweep completion', () => {
  const clean = cleaner();
  assert.deepEqual(clean('coverage-unresolved', { lane: 'departments', page: 100, pages: 100,
    query: 'private-query', url: 'private-url' }, 99),
    { at: 99, event: 'coverage-unresolved', page: 100, pages: 100, lane: 'departments' });
});

const pageState = {
  readyState: 'complete', visibility: 'hidden', route: 'reviewer',
  reviewerShell: true, resultsLabel: false, noResults: false, loading: true,
  error: false, signIn: false, productLinks: 0, offerLines: 0, pagerPresent: false,
  renderedPage: 0, navigationType: 'reload', responseStatus: 200, failedRequests: 2,
  readingFailures: 0, readingWorkMs: 0, queryIndex: 3, queryCount: 100
};

test('page-state sanitizes fixed context and survives repeated history cleaning', () => {
  const clean = cleaner();
  for (const stage of ['empty-observed', 'empty-confirmation', 'fallback', 'unusable',
    'zero-page-complete', 'recovered']) {
    const details = { lane: 'home', stage, state: { ...pageState, url: 'private-url',
      html: '<private>', account: 'private-account' }, documentId: 123, generation: 4,
      page: 1, attempt: 2, items: 0, pages: 0, elapsedMs: 30000, mode: 'none',
      query: 'private-query', reason: 'private-error' };
    const expected = { at: 99, event: 'page-state', lane: 'home', stage, state: pageState,
      documentId: 123, generation: 4, page: 1, attempt: 2, items: 0, pages: 0,
      elapsedMs: 30000, mode: 'none' };
    const first = clean('page-state', details, 99);
    assert.deepEqual(first, expected);
    assert.deepEqual(clean(first.event, first, first.at), expected);
    assert.ok(!JSON.stringify(first).includes('private'));
  }
});

test('page-state drops invalid typed states instead of leaking or inventing facts', () => {
  const clean = cleaner();
  const details = { lane: 'keywords', stage: 'empty-observed', state: pageState };
  for (const key of Object.keys(pageState)) {
    const missing = { ...pageState };
    delete missing[key];
    assert.equal(clean('page-state', { ...details, state: missing }), null, key);
    for (const value of [null, [], {}, 'private-value']) {
      assert.equal(clean('page-state', { ...details, state: { ...pageState, [key]: value } }),
        null, `${key}: ${JSON.stringify(value)}`);
    }
  }
  for (const [key, maximum] of [['productLinks', 10000], ['offerLines', 10000],
    ['renderedPage', 100000], ['failedRequests', 10000], ['readingFailures', 10000],
    ['readingWorkMs', 1e15], ['queryIndex', 10000], ['queryCount', 10000]]) {
    for (const value of [true, -1, 0.5, NaN, Infinity, maximum + 1]) {
      assert.equal(clean('page-state', { ...details, state: { ...pageState, [key]: value } }), null);
    }
    assert.equal(clean('page-state', { ...details, state: { ...pageState, [key]: maximum } })
      .state[key], maximum);
  }
  for (const responseStatus of [true, -1, 1, 99, 600, 200.5, NaN, Infinity]) {
    assert.equal(clean('page-state', { ...details, state: { ...pageState, responseStatus } }), null);
  }
  for (const responseStatus of [0, 100, 599]) {
    assert.equal(clean('page-state', { ...details, state: { ...pageState, responseStatus } })
      .state.responseStatus, responseStatus);
  }
  for (const state of [null, [], true, 'private']) {
    assert.equal(clean('page-state', { ...details, state }), null);
  }
  assert.equal(clean('page-state', { ...details, lane: 'private-lane' }), null);
  assert.equal(clean('page-state', { ...details, stage: 'private-stage' }), null);
  assert.equal(clean('page-state', { state: pageState, stage: 'unusable' }), null);
  assert.equal(clean('page-state', { state: pageState, lane: 'home' }), null);
});

test('page-state fields cannot attach to older diagnostic events', () => {
  const clean = cleaner();
  assert.deepEqual(clean('page-loaded', { lane: 'home', stage: 'unusable', state: pageState }, 99),
    { at: 99, event: 'page-loaded', lane: 'home' });
});
