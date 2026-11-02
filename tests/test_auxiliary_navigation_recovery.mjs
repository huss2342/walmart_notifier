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
