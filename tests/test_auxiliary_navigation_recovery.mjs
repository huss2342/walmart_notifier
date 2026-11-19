import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const home = 'https://www.walmart.com/reviews/claim-product';
const file = new URL('../extension/auxiliary-scans.js', import.meta.url);

function storage(initial = {}) {
  const data = structuredClone(initial);
  return { data, async get(keys) {
    if (keys == null) return structuredClone(data);
    return Object.fromEntries((Array.isArray(keys) ? keys : [keys])
      .filter(key => Object.hasOwn(data, key)).map(key => [key, structuredClone(data[key])]));
  }, async set(values) { Object.assign(data, structuredClone(values)); },
  async remove(keys) { for (const key of Array.isArray(keys) ? keys : [keys]) delete data[key]; } };
}

function fixture({ settings: initial = {}, local = storage(), session = storage(), tabs: initialTabs = [],
  initialStartDelay, intakeSummary = {} } = {}) {
  let now = 100_000;
  let paused = 0;
  let settings = { refreshMinutes: 5, pageDelaySeconds: 15, ...initial };
  let onDiagnostic = async () => {};
  let onConfigRead = async () => {};
  const tabs = new Map(initialTabs.map(tab => [tab.id, { ...tab }]));
  const actions = [];
  const posts = [];
  const roles = [];
  const probes = new Map();
  let nextId = Math.max(99, ...tabs.keys()) + 1;
  const chrome = { storage: { local, session }, alarms: {
    async get() { return null; }, async create(name, options) { actions.push({ type: 'alarm', name, options }); },
    async clear() {}
  }, tabs: {
    async create(options) {
      const tab = { id: nextId++, url: options.url, status: 'complete' };
      tabs.set(tab.id, tab);
      actions.push({ type: 'create', ...options });
      return { ...tab };
    }, async update(id, options) {
      assert.ok(await api.ownsTab(id), 'Ownership must be reserved before loading a reviewer document');
      Object.assign(tabs.get(id), options);
      actions.push({ type: 'update', id, ...options });
      return { ...tabs.get(id) };
    }, async get(id) {
      if (!tabs.has(id)) throw new Error('No tab');
      return { ...tabs.get(id) };
    }, async reload(id) { actions.push({ type: 'reload', id }); },
    async sendMessage(id, message) { roles.push({ id, ...message }); return { ok: true }; }
  } };
  class Clock extends Date { static now() { return now; } }
  const context = vm.createContext({ URL, URLSearchParams, Date: Clock, console });
  vm.runInContext(fs.readFileSync(new URL('../extension/sweep-modes.js', import.meta.url), 'utf8') +
    '\n' + fs.readFileSync(new URL('../extension/runtime-policy.js', import.meta.url), 'utf8') +
    '\n' + fs.readFileSync(new URL('../extension/department-data.js', import.meta.url), 'utf8') +
    '\n' + fs.readFileSync(file, 'utf8'), context);
  const api = vm.runInContext('ReviewerAuxiliaryScans', context).create(chrome, {
    home, config: async () => { await onConfigRead(); return settings; }, paused: async () => paused,
    initialStartDelay,
    diagnostic: (...args) => onDiagnostic(...args), probe: async id => probes.get(id) || { ready: true, reviewerReady: true,
      phase: 'waiting-navigation', sweeping: true, active: true, lastActivityAt: now, nextActionAt: now + 15_000 },
    post: async (items, page, canSend) => {
      if (!await canSend()) return { ok: false };
      posts.push({ items, page });
      return { ok: true, summary: { seen: items.length, ...intakeSummary } };
    }
  });
  const sender = id => ({ tab: { ...tabs.get(id) } });
  const message = (id, type, values = {}) => api.handle({ type, ...values }, sender(id));
  return { api, chrome, local, session, tabs, actions, posts, roles, probes, sender, message,
    advance(ms = 30_000) { now += ms; }, now() { return now; }, setPaused(value) { paused = value; },
    setDiagnostic(callback) { onDiagnostic = callback; },
    setConfigRead(callback) { onConfigRead = callback; },
    configure(value) { settings = { ...settings, ...value }; } };
}

async function passiveAssignment() {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Water Filter'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const canonical = f.tabs.get(lane.tabId).url;
  Object.assign(f.tabs.get(lane.tabId), { url: `${home}?q=Old&queryNups=Old`,
    pendingUrl: canonical, status: 'loading' });
  assert.equal((await f.message(lane.tabId, 'reviewer:role')).active, false);
  f.probes.set(lane.tabId, { ready: true, active: false, phase: 'passive',
    generation: lane.generation, busy: false });
  return { f, lane, canonical };
}

test('a committed assigned document is rebound immediately after its earlier passive role', async () => {
  const { f, lane, canonical } = await passiveAssignment();
  Object.assign(f.tabs.get(lane.tabId), { url: `${canonical}&page=2`, pendingUrl: undefined, status: 'complete' });
  const before = structuredClone(f.local.data.reviewerAuxiliaryScans.lanes.keywords);
  f.roles.length = 0;
  await f.api.tick();
  assert.equal(f.roles.length, 1);
  assert.equal(f.roles[0].active, true);
  assert.equal(f.roles[0].generation, lane.generation);
  assert.equal(f.roles[0].auxiliary.targetKey, 'keyword:water filter');
  assert.deepEqual(f.local.data.reviewerAuxiliaryScans.lanes.keywords, before,
    'Rebinding is not page progress and cannot renew deadlines or spend retries');
});

for (const guard of ['old-document', 'wrong-query', 'wrong-pending-scope', 'stale-generation',
  'unknown-generation', 'missing-receiver', 'busy', 'user-active', 'high-traffic', 'bot-check', 'paused', 'disabled']) {
  test(`passive rebind preserves the ${guard} guard`, async () => {
    const { f, lane, canonical } = await passiveAssignment();
    const tab = f.tabs.get(lane.tabId);
    Object.assign(tab, { url: canonical, pendingUrl: undefined, status: 'complete' });
    const probe = f.probes.get(lane.tabId);
    if (guard === 'old-document') Object.assign(tab, { url: `${home}?q=Old&queryNups=Old`, pendingUrl: canonical });
    if (guard === 'wrong-query') tab.url = `${home}?q=Cat&queryNups=Cat`;
    if (guard === 'wrong-pending-scope') tab.pendingUrl = `${home}?q=Cat&queryNups=Cat`;
    if (guard === 'stale-generation') probe.generation -= 1;
    if (guard === 'unknown-generation') delete probe.generation;
    if (guard === 'missing-receiver') f.probes.set(lane.tabId, { ready: false });
    if (guard === 'busy') probe.busy = true;
    if (guard === 'user-active') { probe.focusReason = 'user-active'; probe.busy = true; }
    if (guard === 'high-traffic') probe.highTraffic = true;
    if (guard === 'bot-check') probe.botCheck = true;
    if (guard === 'paused') f.setPaused(1);
    if (guard === 'disabled') f.configure({ auxiliaryKeywords: false });
    f.roles.length = 0;
    await f.api.tick();
    assert.equal(f.roles.filter(role => role.active).length, 0);
  });
}

test('repeated passive rebinds retain the finite coordinator recovery deadline', async () => {
  const { f, lane, canonical } = await passiveAssignment();
  Object.assign(f.tabs.get(lane.tabId), { url: canonical, pendingUrl: undefined, status: 'complete' });
  const target = () => f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0];
  const startedAt = f.local.data.reviewerAuxiliaryScans.lanes.keywords.lastActivityAt;
  for (const elapsed of [1000, 29000, 30000, 30000, 29999]) {
    f.advance(elapsed);
    await f.api.tick();
    assert.equal(target().attempts, 0);
    assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.lastActivityAt, startedAt);
  }
  assert.equal(f.roles.filter(role => role.active).length, 5);
  f.advance(1);
  await f.api.tick();
  assert.equal(target().attempts, 1, 'An unresponsive role application still receives bounded recovery');
  assert.equal((await f.api.status()).lanes[0].status, 'waiting');
});

async function confirmedInterior() {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Kitchen Appliance'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const assigned = await f.message(lane.tabId, 'reviewer:role');
  const canonical = assigned.auxiliary.targetUrl;
  f.tabs.get(lane.tabId).url = `${canonical}&page=10`;
  const receipt = { generation: lane.generation, targetKey: assigned.auxiliary.targetKey, mode: 'none',
    visited: [1, 2, 3, 4, 5, 6, 7, 8, 9], total: 25, endRetries: { 10: 3 }, lastObservedPage: 10 };
  const key = `sweep:${lane.tabId}`;
  await f.local.set({ [key]: receipt });
  await f.message(lane.tabId, 'reviewer:checkpoint', { generation: lane.generation });
  f.probes.set(lane.tabId, { ready: true, active: true, generation: lane.generation, busy: false });
  return { f, lane, canonical, receipt, key,
    target: () => f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0],
    fail: (values = {}) => f.message(lane.tabId, 'reviewer:unusable', { generation: lane.generation,
      reason: 'Reviewer page 10 still showed no results after 3 confirmations',
      restartSweep: true, revalidateBoundary: true, ...values }) };
}

test('a repeatedly confirmed empty interior page archives its receipt and revalidates the search from page one', async () => {
  const { f, lane, canonical, receipt, key, target, fail } = await confirmedInterior();
  const oldGeneration = lane.generation;
  const reply = await fail();
  assert.equal(reply.recover, 'waiting');
  assert.equal(target().coverageStatus, 'unresolved');
  assert.equal(target().attempts, 1);
  assert.deepEqual(target().boundaryRevalidation, { at: f.now(), checkpoint: receipt, checkpointUrl: `${canonical}&page=10` });
  assert.equal(target().checkpoint, null);
  assert.equal(target().checkpointUrl, null);
  assert.equal(f.local.data[key], undefined);
  assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.checkpoint, null);
  assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.checkpointUrl, null);

  f.advance(15_000); await f.api.tick();
  const current = await f.message(lane.tabId, 'reviewer:role');
  assert.ok(current.generation > oldGeneration);
  assert.equal(f.tabs.get(lane.tabId).url, canonical);
  assert.equal(f.local.data[key], undefined, 'The reader must derive a fresh total instead of inheriting 25 pages');
  for (const page of [1, 2]) {
    f.tabs.get(lane.tabId).url = `${canonical}&page=${page}`;
    assert.equal((await f.message(lane.tabId, 'reviewer:page', { generation: current.generation,
      page, items: [{ item_id: `current-catalogue-${page}` }] })).ok, true);
    await f.local.set({ [key]: { generation: current.generation, targetKey: current.auxiliary.targetKey,
      mode: 'none', total: 2, visited: page === 1 ? [1] : [1, 2], endRetries: {} } });
    await f.message(lane.tabId, 'reviewer:checkpoint', { generation: current.generation });
  }
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: current.generation, pages: 2 });
  assert.equal((await f.api.status()).lanes[0].status, 'complete');
  assert.equal(target().coverageStatus, 'complete');
  assert.equal(target().pages, 2, 'Only the fresh completed search proves current coverage');
  assert.equal(f.posts.length, 2);
  assert.deepEqual(target().boundaryRevalidation.checkpoint.visited, receipt.visited);
});

for (const guard of ['no-flag', 'no-restart', 'two-confirmations', 'visited-page', 'no-known-total',
  'not-interior', 'wrong-receipt-generation', 'wrong-receipt-target', 'stale-message', 'wrong-scope',
  'pending-wrong-scope', 'input', 'user-active', 'traffic', 'paused']) {
  test(`interior boundary revalidation rejects ${guard} without discarding acknowledged pages`, async () => {
    const { f, lane, canonical, key, target, fail } = await confirmedInterior();
    const receipt = f.local.data[key];
    const detail = {};
    if (guard === 'no-flag') detail.revalidateBoundary = false;
    if (guard === 'no-restart') detail.restartSweep = false;
    if (guard === 'two-confirmations') receipt.endRetries[10] = 2;
    if (guard === 'visited-page') receipt.visited.push(10);
    if (guard === 'no-known-total') receipt.total = null;
    if (guard === 'not-interior') receipt.total = 10;
    if (guard === 'wrong-receipt-generation') receipt.generation -= 1;
    if (guard === 'wrong-receipt-target') receipt.targetKey = 'keyword:golf';
    if (guard === 'stale-message') detail.generation = lane.generation - 1;
    if (guard === 'wrong-scope') f.tabs.get(lane.tabId).url = `${home}?q=Golf&queryNups=Golf&page=10`;
    if (guard === 'pending-wrong-scope') f.tabs.get(lane.tabId).pendingUrl = `${home}?q=Golf&queryNups=Golf`;
    if (guard === 'input') f.probes.get(lane.tabId).busy = true;
    if (guard === 'user-active') Object.assign(f.probes.get(lane.tabId), { focusReason: 'user-active', busy: true });
    if (guard === 'traffic') f.probes.get(lane.tabId).highTraffic = true;
    if (guard === 'paused') f.setPaused(1);
    await fail(detail);
    assert.equal(target().boundaryRevalidation, undefined);
    assert.deepEqual(target().checkpoint.visited.slice(0, 9), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
    assert.ok(target().checkpointUrl?.startsWith(canonical));
  });
}

for (const focusReason of ['manual-tab-active', 'user-active']) {
  test(`cached ${focusReason} does not block a proven interior boundary refresh`, async () => {
    const { f, lane, receipt, target, fail } = await confirmedInterior();
    Object.assign(f.probes.get(lane.tabId), { focusReason, busy: false });
    await fail();
    assert.deepEqual(target().boundaryRevalidation?.checkpoint, receipt);
    assert.equal(target().checkpoint, null);
  });

  test(`cached ${focusReason} does not block an authorized passive rebind`, async () => {
    const { f, lane, canonical } = await passiveAssignment();
    Object.assign(f.tabs.get(lane.tabId), { url: canonical, pendingUrl: undefined, status: 'complete' });
    Object.assign(f.probes.get(lane.tabId), { focusReason, busy: false });
    f.roles.length = 0;
    await f.api.tick();
    assert.equal(f.roles.filter(role => role.active).length, 1);
  });
}

test('boundary revalidation clears an impossible active receipt before the final retry cap defers it', async () => {
  const { f, lane, canonical, receipt, key, target, fail } = await confirmedInterior();
  target().attempts = 2;
  assert.equal((await fail()).recover, 'skipped');
  assert.equal((await f.api.status()).lanes[0].status, 'partial');
  assert.equal(target().coverageStatus, 'unresolved');
  assert.equal(target().attempts, 3);
  assert.equal(target().checkpoint, null);
  assert.equal(target().checkpointUrl, null);
  assert.deepEqual(target().boundaryRevalidation.checkpoint, receipt);
  assert.equal(f.local.data[key], undefined);
  const deferred = (await f.api.status()).lanes[0];
  f.advance(deferred.nextAt - f.now()); await f.api.tick();
  assert.equal(f.tabs.get(lane.tabId).url, canonical);
  assert.equal(target().checkpoint, null);
  assert.equal(target().attempts, 0);
  assert.deepEqual(target().boundaryRevalidation.checkpoint.visited, receipt.visited);
});

test('a later boundary revalidation replaces its bounded archive without nesting earlier receipts', async () => {
  const { f, lane, canonical, key, target, fail } = await confirmedInterior();
  await fail();
  const firstArchive = structuredClone(target().boundaryRevalidation);
  f.advance(15_000); await f.api.tick();
  const current = await f.message(lane.tabId, 'reviewer:role');
  f.tabs.get(lane.tabId).url = `${canonical}&page=3`;
  const receipt = { generation: current.generation, targetKey: current.auxiliary.targetKey,
    mode: 'none', total: 4, visited: [1, 2], endRetries: { 3: 3 } };
  await f.local.set({ [key]: receipt });
  f.probes.set(lane.tabId, { ready: true, active: true, generation: current.generation, busy: false });
  await f.message(lane.tabId, 'reviewer:unusable', { generation: current.generation,
    reason: 'A later interior page was confirmed empty', restartSweep: true, revalidateBoundary: true });
  assert.equal(target().attempts, 2);
  assert.deepEqual(target().boundaryRevalidation.checkpoint, receipt);
  assert.notDeepEqual(target().boundaryRevalidation, firstArchive);
  assert.equal(target().boundaryRevalidation.checkpoint.boundaryRevalidation, undefined);
  assert.deepEqual(Object.keys(target().boundaryRevalidation).sort(), ['at', 'checkpoint', 'checkpointUrl']);
});



async function pendingCanonicalRecovery() {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Dog'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const original = await f.message(lane.tabId, 'reviewer:role');
  await f.local.set({ [`sweep:${lane.tabId}`]: { generation: lane.generation,
    targetKey: original.auxiliary.targetKey, mode: 'none', visited: [1], total: 10 } });
  await f.message(lane.tabId, 'reviewer:checkpoint', { generation: lane.generation });
  const drift = `${original.auxiliary.targetUrl}&page=2&affinityOverride=store_led`;
  f.tabs.get(lane.tabId).url = drift;
  f.probes.set(lane.tabId, { ready: true, active: false, busy: false, generation: lane.generation, documentId: 90000 });
  await f.api.tick();
  assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 1);
  f.advance(30000);
  await f.api.tick();
  const resumed = (await f.api.status()).lanes[0];
  const canonical = f.tabs.get(lane.tabId).url;
  Object.assign(f.tabs.get(lane.tabId), { url: drift, pendingUrl: canonical, status: 'loading' });
  f.probes.set(lane.tabId, { ready: true, active: false, busy: false, generation: resumed.generation, documentId: 90000 });
  return { f, lane: resumed, canonical, target: () => f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0] };
}

test('pending canonical navigation does not spend recovery retries on its old document', async () => {
  const { f, lane, canonical, target } = await pendingCanonicalRecovery();
  for (const elapsed of [72, 1000, 29000, 60000]) {
    f.advance(elapsed);
    await f.api.tick();
    assert.equal(target().attempts, 1, 'Only the completed drift spends a retry before pending navigation commits');
    assert.equal((await f.api.status()).lanes[0].status, 'scanning');
    assert.deepEqual(f.local.data[`sweep:${lane.tabId}`].visited, [1]);
    assert.equal((await f.message(lane.tabId, 'reviewer:role')).active, false,
      'The old drifted response remains unauthorized while loading');
  }
  Object.assign(f.tabs.get(lane.tabId), { url: canonical, pendingUrl: undefined, status: 'complete' });
  assert.equal((await f.message(lane.tabId, 'reviewer:role')).active, true);
  assert.equal((await f.message(lane.tabId, 'reviewer:role')).auxiliary.urlPagination, true);
  assert.deepEqual(f.local.data[`sweep:${lane.tabId}`].visited, [1]);
});

test('a pending canonical load has one finite start-based recovery deadline', async () => {
  const { f, target } = await pendingCanonicalRecovery();
  for (let tick = 0; tick < 4; tick += 1) {
    f.advance(29000);
    await f.api.tick();
    assert.equal(target().attempts, 1, 'Repeated loading probes cannot renew the deadline');
  }
  f.advance(4001);
  await f.api.tick();
  assert.equal(target().attempts, 2, 'A permanently loading tab must still recover finitely');
  assert.equal((await f.api.status()).lanes[0].status, 'waiting');
});

for (const suffix of ['&q=Cat', '&facet=brand:custom', '&affinityOverride=store_led', '&unknown=private']) {
  test(`a changed pending scope cannot defer drift recovery: ${suffix}`, async () => {
    const { f, lane, canonical, target } = await pendingCanonicalRecovery();
    f.tabs.get(lane.tabId).pendingUrl = canonical + suffix;
    await f.api.tick();
    assert.equal(target().attempts, 2);
  });
}

test('a completed drift document does not obtain a loading grace', async () => {
  const { f, lane, target } = await pendingCanonicalRecovery();
  f.tabs.get(lane.tabId).status = 'complete';
  await f.api.tick();
  assert.equal(target().attempts, 2);
});

test('a missing pending URL does not infer an authorized canonical load', async () => {
  const { f, lane, target } = await pendingCanonicalRecovery();
  delete f.tabs.get(lane.tabId).pendingUrl;
  await f.api.tick();
  assert.equal(target().attempts, 2);
});

test('input and verification guards still protect a stalled pending load', async () => {
  const { f, lane, target } = await pendingCanonicalRecovery();
  f.probes.set(lane.tabId, { ready: true, active: false, busy: true, generation: lane.generation });
  f.advance(121000);
  await f.api.tick();
  assert.equal(target().attempts, 1, 'Trusted input takes precedence over the recovery deadline');
  f.setPaused(1);
  await f.api.tick();
  assert.equal((await f.api.status()).lanes[0].status, 'paused');
  assert.equal(target().attempts, 1);
  assert.deepEqual(f.local.data[`sweep:${lane.tabId}`].visited, [1]);
});
