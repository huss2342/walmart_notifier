import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const home = 'https://www.walmart.com/reviews/claim-product';
const sources = ['sweep-modes.js', 'runtime-policy.js', 'department-data.js', 'auxiliary-scans.js']
  .map(name => fs.readFileSync(new URL(`../extension/${name}`, import.meta.url), 'utf8')).join('\n');

function storage(initial = {}) {
  const data = structuredClone(initial);
  return { data, async get(keys) {
    return Object.fromEntries((Array.isArray(keys) ? keys : [keys])
      .filter(key => Object.hasOwn(data, key)).map(key => [key, structuredClone(data[key])]));
  }, async set(value) { Object.assign(data, structuredClone(value)); },
  async remove(keys) { for (const key of Array.isArray(keys) ? keys : [keys]) delete data[key]; } };
}

function fixture({ settings: initial = {}, local = storage(), session = storage(), initialTabs = [],
  initialStartDelay } = {}) {
  let now = 100_000;
  let paused = false;
  let settings = { refreshMinutes: 30, pageDelaySeconds: 15, auxiliaryPriority: true,
    auxiliaryPriorityQueries: ['Furniture'], auxiliaryPriorityRefreshMinutes: 1, ...initial };
  const tabs = new Map(initialTabs.map(tab => [tab.id, { ...tab }]));
  const probes = new Map();
  const posts = [];
  const events = [];
  const updates = [];
  let nextId = Math.max(99, ...tabs.keys()) + 1;
  const chrome = { storage: { local, session }, runtime: { getURL: name => `chrome-extension://relay/${name}` },
    alarms: { async get() { return {}; }, async create() {}, async clear() {} }, tabs: {
      async create(options) {
        const tab = { id: nextId++, status: 'complete', ...options };
        tabs.set(tab.id, tab);
        return { ...tab };
      }, async get(id) { if (!tabs.has(id)) throw new Error('Closed tab'); return { ...tabs.get(id) }; },
      async update(id, options) { Object.assign(tabs.get(id), options); updates.push({ id, ...options }); },
      async sendMessage() { return { ok: true }; }
    } };
  class Clock extends Date { static now() { return now; } }
  const context = vm.createContext({ URL, URLSearchParams, Date: Clock });
  vm.runInContext(sources, context);
  const registry = vm.runInContext('ReviewerAuxiliaryScans', context);
  const api = registry.create(chrome, {
    home, config: async () => settings, paused: async () => paused, initialStartDelay,
    diagnostic: async (event, detail) => events.push({ event, ...structuredClone(detail) }),
    probe: async id => probes.get(id) || { ready: true, active: true, lastActivityAt: now },
    post: async (items, page, canSend, context) => {
      if (!await canSend()) return { ok: false };
      posts.push({ items, page, context: structuredClone(context) });
      return { ok: true, summary: { new: items.length } };
    }
  });
  return { api, registry, local, session, tabs, probes, posts, events, updates,
    message(id, type, detail = {}) { return api.handle({ type, ...detail }, { tab: { ...tabs.get(id) } }); },
    saved(name = 'priority') { return structuredClone(local.data.reviewerAuxiliaryScans?.lanes[name]); },
    advance(ms) { now += ms; }, now() { return now; }, setPaused(value) { paused = value; },
    configure(value) { settings = { ...settings, ...value }; },
    async startPriority() {
      await api.tick();
      const lane = (await api.status()).lanes.find(lane => lane.lane === 'priority');
      assert.ok(lane, 'Enabled priority lane must exist');
      if (lane.nextAt > now) { now = lane.nextAt; await api.tick(); }
      return (await api.status()).lanes.find(lane => lane.lane === 'priority');
    } };
}

test('shared lane registry is frozen and does not enable priority for legacy preferences', async () => {
  const f = fixture({ settings: { auxiliaryPriority: undefined } });
  assert.deepEqual(Array.from(f.registry.lanes || []), ['departments', 'keywords', 'priority']);
  assert.equal(Object.isFrozen(f.registry.lanes), true);
  assert.equal(f.registry.enabled('priority', {}), false);
  assert.equal(f.registry.enabled('priority', { auxiliaryPriority: true }), true);
  assert.equal(f.registry.enabled('unknown', { auxiliaryKeywords: true }), false);
  await f.api.tick();
  assert.equal(f.saved(), undefined);
  assert.equal(f.tabs.size, 0);
});

test('priority uses ten exact furniture search targets and the ordinary keyword execution mode', async () => {
  const f = fixture({ settings: { auxiliaryPriorityQueries: null } });
  const lane = await f.startPriority();
  const stored = f.saved();
  assert.deepEqual(stored.targets.map(target => target.label),
    ['Furniture', 'Sofa', 'Couch', 'Sectional', 'Recliner', 'Mattress', 'Bed', 'Desk', 'Dining Table', 'Dresser']);
  assert.equal(stored.targets[0].key, 'priority:furniture');
  assert.ok(stored.targets.every(target => target.kind === 'keyword' && target.selection === null));
  const assigned = await f.message(lane.tabId, 'reviewer:role');
  assert.equal(assigned.active, true);
  assert.equal(assigned.auxiliary.lane, 'priority');
  assert.equal(assigned.auxiliary.mode, 'none');
  for (const target of stored.targets) {
    const url = new URL(target.targetUrl);
    assert.equal(url.origin + url.pathname, home);
    assert.deepEqual([...url.searchParams.keys()], ['q', 'queryNups']);
    assert.equal(url.searchParams.get('q'), target.label);
    assert.equal(url.searchParams.get('queryNups'), target.label);
  }
});

test('priority first-load fallback follows the enabled existing lanes and reserves all tabs without loading together', async () => {
  for (const departments of [false, true]) for (const keywords of [false, true]) {
    const f = fixture({ settings: { auxiliaryDepartments: departments, auxiliaryKeywords: keywords,
      auxiliaryKeywordQueries: ['Pet'] } });
    await f.api.tick();
    assert.equal(f.saved().nextAt, f.now() + 15_000 * (1 + Number(departments) + Number(keywords)));
    assert.equal(f.tabs.size, 1 + Number(departments) + Number(keywords));
    assert.ok(f.updates.length <= 1);
  }
});

test('supplied priority initial delay is applied only once and cannot shorten the safe fallback', async () => {
  let calls = 0;
  const f = fixture({ initialStartDelay: () => { calls += 1; return 25_000; } });
  await f.api.tick();
  assert.equal(f.saved().nextAt, f.now() + 25_000);
  f.advance(10_000);
  await f.api.tick();
  assert.equal(calls, 1);
  assert.equal(f.saved().nextAt, 125_000);
  const short = fixture({ initialStartDelay: () => 1 });
  await short.api.tick();
  assert.equal(short.saved().nextAt, short.now() + 15_000);
});

test('priority configuration and its query edits preserve existing queues and fingerprints', async () => {
  const f = fixture({ settings: { auxiliaryPriority: false, auxiliaryDepartments: true,
    auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  f.advance(15_000); await f.api.tick();
  const keywords = f.saved('keywords');
  await f.local.set({ [`sweep:${keywords.tabId}`]: { generation: keywords.generation,
    targetKey: keywords.targets[0].key, mode: 'none', visited: [1, 2, 4], total: 150 } });
  await f.message(keywords.tabId, 'reviewer:checkpoint', { generation: keywords.generation });
  const before = { departments: f.saved('departments'), keywords: f.saved('keywords') };
  assert.equal(before.departments.fingerprint, JSON.stringify(['top-level-department-destinations-v2', false]));
  assert.equal(before.keywords.fingerprint, JSON.stringify(['Pet']));
  f.configure({ auxiliaryPriority: true }); await f.api.tick();
  assert.deepEqual(f.saved('departments'), before.departments);
  assert.deepEqual(f.saved('keywords'), before.keywords);
  const oldPriority = f.saved();
  f.configure({ auxiliaryPriorityQueries: ['Mattress', 'Desk'] }); await f.api.tick();
  assert.deepEqual(f.saved('departments'), before.departments);
  assert.deepEqual(f.saved('keywords'), before.keywords);
  assert.ok(f.saved().generation > oldPriority.generation);
  assert.deepEqual(f.saved().targets.map(target => target.key), ['priority:mattress', 'priority:desk']);
});

test('priority round interval is independent, persistent and never resets work or changes old lane pacing', async () => {
  const f = fixture();
  const started = await f.startPriority();
  const fingerprint = f.saved().fingerprint;
  f.configure({ auxiliaryPriorityRefreshMinutes: 2 }); await f.api.tick();
  assert.equal(f.saved().generation, started.generation);
  assert.equal(f.saved().fingerprint, fingerprint);
  await f.message(started.tabId, 'reviewer:sweep-complete', { generation: started.generation, pages: 12 });
  const complete = f.saved();
  assert.equal(complete.status, 'complete');
  assert.ok(complete.nextAt >= complete.completedAt + 120_000);
  assert.ok(complete.nextAt <= complete.completedAt + 132_000);
  f.configure({ auxiliaryPriorityRefreshMinutes: 1 }); await f.api.tick();
  assert.equal(f.saved().generation, complete.generation);
  assert.ok(f.saved().nextAt >= complete.completedAt + 60_000);
  assert.ok(f.saved().nextAt <= complete.completedAt + 66_000);
  const deadline = f.saved().nextAt;
  const restored = fixture({ local: f.local, session: f.session, initialTabs: [...f.tabs.values()] });
  await restored.api.tick();
  assert.equal(restored.saved().nextAt, deadline);
  restored.advance(deadline-restored.now()); await restored.api.tick();
  assert.equal(restored.saved().round, complete.round + 1);
  assert.equal(restored.saved().tabId, complete.tabId);
});

test('priority interval changes do not alter a completed general keyword round deadline', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  const priority = await f.startPriority();
  const keyword = f.saved('keywords');
  await f.message(keyword.tabId, 'reviewer:sweep-complete', { generation: keyword.generation, pages: 1 });
  await f.message(priority.tabId, 'reviewer:sweep-complete', { generation: priority.generation, pages: 1 });
  const before = f.saved('keywords');
  assert.ok(before.nextAt >= before.completedAt + 30*60_000);
  assert.ok(f.saved().nextAt < before.nextAt);
  f.configure({ auxiliaryPriorityRefreshMinutes: 3 }); await f.api.tick();
  assert.deepEqual(f.saved('keywords'), before);
  assert.ok(f.saved().nextAt >= f.saved().completedAt + 3*60_000);
});

test('global refresh zero disables completed priority rounds but still retries unresolved deep coverage', async () => {
  for (const limited of [false, true]) {
    const f = fixture({ settings: { refreshMinutes: 0 } });
    const lane = await f.startPriority();
    const assigned = await f.message(lane.tabId, 'reviewer:role');
    const visited = Array.from({ length: 131 }, (_, index) => index + 1);
    await f.local.set({ [`sweep:${lane.tabId}`]: { generation: lane.generation,
      targetKey: assigned.auxiliary.targetKey, mode: 'none', visited, total: 150 } });
    await f.message(lane.tabId, 'reviewer:checkpoint', { generation: lane.generation });
    await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: lane.generation,
      pages: 131, coverageLimited: limited });
    assert.equal(f.saved().status, limited ? 'partial' : 'complete');
    assert.equal(f.saved().nextAt > 0, limited);
    if (limited) {
      assert.deepEqual(f.saved().targets[0].checkpoint.visited, visited);
      f.advance(f.saved().nextAt-f.now()); await f.api.tick();
      const retry = f.saved();
      assert.deepEqual(retry.checkpoint.visited, visited);
      assert.equal(new URL(f.tabs.get(retry.tabId).url).searchParams.get('page'), '132');
    }
  }
});

test('priority shares traffic, scoped ingestion and verification checkpoint guards', async () => {
  const f = fixture();
  const lane = await f.startPriority();
  const assigned = await f.message(lane.tabId, 'reviewer:role');
  await f.local.set({ [`sweep:${lane.tabId}`]: { generation: lane.generation,
    targetKey: assigned.auxiliary.targetKey, mode: 'none', visited: [1, 2], total: 10 } });
  await f.message(lane.tabId, 'reviewer:checkpoint', { generation: lane.generation });
  f.probes.set(lane.tabId, { ready: true, active: true, generation: lane.generation,
    phase: 'waiting-traffic', highTraffic: true });
  f.advance(600_000); await f.api.tick();
  assert.equal(f.saved().targets[0].attempts, 0);
  assert.deepEqual(f.saved().checkpoint.visited, [1, 2]);
  const sent = await f.message(lane.tabId, 'reviewer:page', { generation: lane.generation,
    documentId: 1234, page: 3, items: [{ item_id: 'ip-123456789' }] });
  assert.equal(sent.ok, true);
  assert.equal(f.posts[0].context.lane, 'priority');
  assert.ok(f.events.some(event => event.event === 'auxiliary-page-delivered' && event.lane === 'priority'));
  f.setPaused(true); await f.api.pause();
  assert.equal(f.saved().status, 'paused');
  assert.deepEqual(f.saved().checkpoint.visited, [1, 2]);
  const rejected = await f.message(lane.tabId, 'reviewer:page', { generation: lane.generation,
    page: 4, items: [{ item_id: 'ip-123456789' }] });
  assert.equal(rejected.ok, false);
  assert.equal(f.posts.length, 1);
});

test('empty priority queue stays visible and cannot fabricate complete coverage or start loads', async () => {
  const f = fixture({ settings: { auxiliaryPriorityQueries: [] } });
  await f.api.tick();
  assert.equal(f.saved().status, 'partial');
  assert.equal(f.saved().targets.length, 0);
  assert.equal(f.updates.length, 0);
});

test('priority disable and tab recreation retain the first missing deep page under fresh authority', async () => {
  const f = fixture();
  const lane = await f.startPriority();
  const assigned = await f.message(lane.tabId, 'reviewer:role');
  const visited = [1, 2, 4, 120];
  await f.local.set({ [`sweep:${lane.tabId}`]: { generation: lane.generation,
    targetKey: assigned.auxiliary.targetKey, mode: 'none', visited, total: 150 } });
  await f.message(lane.tabId, 'reviewer:checkpoint', { generation: lane.generation });
  f.configure({ auxiliaryPriority: false }); await f.api.tick();
  assert.equal(f.saved().status, 'disabled');
  assert.deepEqual(f.saved().targets[0].checkpoint.visited, visited);
  f.tabs.delete(lane.tabId); await f.api.removed(lane.tabId);
  f.configure({ auxiliaryPriority: true });
  f.advance(30_000); await f.api.tick();
  const resumed = f.saved();
  assert.equal(resumed.status, 'scanning');
  assert.notEqual(resumed.tabId, lane.tabId);
  assert.ok(resumed.generation > lane.generation);
  assert.deepEqual(resumed.checkpoint.visited, visited);
  assert.equal(new URL(f.tabs.get(resumed.tabId).url).searchParams.get('page'), '3');
  assert.equal((await f.message(resumed.tabId, 'reviewer:page', { generation: lane.generation,
    page: 3, items: [{ item_id: 'ip-123456789' }] })).ok, false);
});

test('priority traffic evidence cannot preserve a wrong query scope or a lost receiver', async () => {
  for (const changedScope of [false, true]) {
    const f = fixture();
    const lane = await f.startPriority();
    if (changedScope) f.tabs.get(lane.tabId).url = `${home}?q=Different&queryNups=Different`;
    f.probes.set(lane.tabId, changedScope ? { ready: true, active: true,
      generation: lane.generation, phase: 'waiting-traffic', highTraffic: true } : { ready: false });
    f.advance(121_000); await f.api.tick();
    assert.equal(f.saved().targets[0].attempts, 1);
    assert.equal(f.saved().status, 'waiting');
  }
});

for (const departments of [false, true]) {
  test(`paced lane starts rotate fairly with ${departments ? 'three' : 'two'} fast auxiliary queues`, async () => {
    const f = fixture({ settings: { auxiliaryDepartments: departments, auxiliaryKeywords: true,
      auxiliaryKeywordQueries: Array.from({ length: 100 }, (_, index) => `Item ${index}`),
      auxiliaryPriorityQueries: null } });
    await f.api.tick();
    if (departments) {
      const lane = f.saved('departments');
      await f.message(lane.tabId, 'reviewer:aux-discovered', { generation: lane.generation,
        candidates: Array.from({ length: 20 }, (_, index) => ({ kind: 'department',
          label: `Department ${index}`, parentUrl: home, url: `${home}?catId=${index + 1}` })) });
    }
    const enabled = departments ? ['departments', 'keywords', 'priority'] : ['keywords', 'priority'];
    const starts = [];
    let observedUpdates = 0;
    for (let tick = 0; tick < 10; tick++) {
      const added = f.updates.slice(observedUpdates);
      assert.ok(added.length <= 1, 'One tick cannot bypass the shared target-load spacing');
      for (const update of added) starts.push(enabled.find(name => f.saved(name).tabId === update.id));
      observedUpdates = f.updates.length;
      for (const name of enabled) {
        const lane = f.saved(name);
        if (lane.status !== 'scanning') continue;
        const target = lane.targets[lane.cursor];
        if (target.selection) {
          f.tabs.get(lane.tabId).url = target.selection.url;
          const bound = await f.message(lane.tabId, 'reviewer:aux-selected', {
            generation: lane.generation, url: target.selection.url });
          assert.equal(bound.ok, true);
        }
        const complete = await f.message(lane.tabId, 'reviewer:sweep-complete', {
          generation: lane.generation, pages: target.discoveryOnly ? 0 : 1 });
        assert.equal(complete.ok, true);
      }
      f.advance(30_000); await f.api.tick();
    }
    assert.deepEqual(starts, Array.from({ length: starts.length }, (_, index) => enabled[index % enabled.length]));
    for (const name of enabled) {
      assert.ok(starts.filter(start => start === name).length >= 3, `${name} must receive repeated paced starts`);
    }
    assert.ok(f.saved().cursor >= 3, 'Priority coverage must progress before the hundred-keyword round ends');
  });
}

test('rotation of paced starts survives worker recreation before both lanes become due', async () => {
  const settings = { auxiliaryKeywords: true,
    auxiliaryKeywordQueries: Array.from({ length: 100 }, (_, index) => `Item ${index}`),
    auxiliaryPriorityQueries: null };
  const first = fixture({ settings });
  await first.api.tick();
  const keyword = first.saved('keywords');
  await first.message(keyword.tabId, 'reviewer:sweep-complete', { generation: keyword.generation, pages: 1 });
  const restored = fixture({ settings, local: first.local, session: first.session,
    initialTabs: [...first.tabs.values()] });
  restored.advance(30_000); await restored.api.tick();
  assert.equal(restored.saved().status, 'scanning');
  assert.equal(restored.saved('keywords').status, 'waiting');
  assert.equal(restored.saved('keywords').cursor, 1);
  assert.equal(restored.updates.length, 1);
  assert.equal(restored.updates[0].id, restored.saved().tabId);
});

test('missing, unknown and disabled previous start lanes retain safe initial scheduling', async () => {
  for (const previous of [undefined, 'unknown', 'priority']) {
    const local = storage({ reviewerAuxiliaryScans: { version: 1, lanes: {},
      ...(previous ? { lastTargetStartedLane: previous } : {}) } });
    const f = fixture({ local, settings: { auxiliaryKeywords: true,
      auxiliaryKeywordQueries: ['Pet'], auxiliaryPriority: false } });
    await f.api.tick();
    assert.equal(f.saved('keywords').status, 'scanning');
    assert.equal(f.updates.length, 1);
  }
});
