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
  initialStartDelay } = {}) {
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
      return { ok: true, summary: { seen: items.length } };
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

test('optional lanes reserve exactly two owned tabs and never adopt a manual tab', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true, auxiliaryKeywords: true,
    auxiliaryKeywordQueries: ['Chair', 'chair', 'Pet'] },
  tabs: [{ id: 7, url: `${home}?catId=4044`, status: 'complete' }] });
  await f.api.tick();
  assert.equal(f.actions.filter(action => action.type === 'create').length, 1,
    'The second lane starts after the first lane navigation interval');
  f.advance();
  await f.api.tick();
  assert.equal(f.actions.filter(action => action.type === 'create').length, 2);
  assert.equal(await f.api.ownsTab(7), false);
  assert.equal(await f.message(7, 'reviewer:role'), null);
  const { lanes } = await f.api.status();
  assert.equal(lanes.length, 2);
  const keywords = lanes.find(lane => lane.lane === 'keywords');
  assert.equal(keywords.totalTargets, 2);
  const role = await f.message(keywords.tabId, 'reviewer:role');
  assert.equal(role.primaryTabId, keywords.tabId);
  assert.equal(role.auxiliary.targetUrl, `${home}?q=Chair`);
  assert.equal(role.auxiliary.selection, null);
});

test('auxiliary target starts stay staggered after simultaneous recovery', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true, auxiliaryKeywords: true,
    auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  f.advance(); await f.api.tick();
  const state = f.local.data.reviewerAuxiliaryScans;
  for (const lane of Object.values(state.lanes)) {
    lane.status = 'waiting'; lane.nextAt = 0;
  }
  await f.local.set({ reviewerAuxiliaryScans: state });
  f.advance(60_000);
  const before = f.actions.filter(a => a.type === 'update').length;
  await f.api.tick();
  assert.equal(f.actions.filter(a => a.type === 'update').length - before, 1);
  const waiting = (await f.api.status()).lanes.find(lane => lane.status === 'waiting');
  assert.ok(waiting.nextAt > 0);
  await f.api.tick();
  assert.equal(f.actions.filter(a => a.type === 'update').length - before, 1);
  f.advance(15_000); await f.api.tick();
  assert.equal(f.actions.filter(a => a.type === 'update').length - before, 2);
});

test('supplied first-start offsets persist once and each fresh lane starts at its own deadline', async () => {
  const calls = [];
  const f = fixture({ settings: { pageDelaySeconds: 20, auxiliaryDepartments: true,
    auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] },
  initialStartDelay(settings, name) {
    calls.push(name);
    return settings.pageDelaySeconds * 1000 * (name === 'departments' ? 1 : 3);
  } });
  await f.api.tick();
  assert.equal(f.actions.filter(action => action.type === 'create').length, 0);
  const deadlines = Object.fromEntries((await f.api.status()).lanes.map(lane => [lane.lane, lane.nextAt]));
  assert.deepEqual(deadlines, { departments: f.now() + 20_000, keywords: f.now() + 60_000 });
  f.advance(19_999);
  await f.api.tick();
  assert.equal(f.actions.filter(action => action.type === 'create').length, 0);
  assert.deepEqual(Object.fromEntries((await f.api.status()).lanes.map(lane => [lane.lane, lane.nextAt])), deadlines);
  assert.deepEqual(calls, ['departments', 'keywords'], 'Heartbeat ticks must not recalculate first-start offsets');
  f.advance(1);
  await f.api.tick();
  let lanes = (await f.api.status()).lanes;
  assert.equal(lanes.find(lane => lane.lane === 'departments').status, 'scanning');
  assert.equal(lanes.find(lane => lane.lane === 'keywords').status, 'waiting');
  assert.equal(lanes.find(lane => lane.lane === 'keywords').nextAt, deadlines.keywords,
    'The standalone keyword offset must not replace a longer supplied deadline');
  assert.equal(f.actions.filter(action => action.type === 'update').length, 1);
  f.advance(39_999);
  await f.api.tick();
  assert.equal(f.actions.filter(action => action.type === 'update').length, 1);
  f.advance(1);
  await f.api.tick();
  lanes = (await f.api.status()).lanes;
  assert.ok(lanes.every(lane => lane.status === 'scanning'));
  assert.equal(f.actions.filter(action => action.type === 'update').length, 2);
  assert.deepEqual(calls, ['departments', 'keywords']);
});

test('first-start offsets are not reapplied after worker recreation or a queue migration', async () => {
  const settings = { auxiliaryDepartments: true };
  const first = fixture({ settings, initialStartDelay: () => 20_000 });
  await first.api.tick();
  const waiting = (await first.api.status()).lanes[0];
  const restored = fixture({ settings, local: first.local, session: first.session,
    initialStartDelay() { assert.fail('Only a newly created lane receives the initial offset'); } });
  restored.advance(10_000);
  await restored.api.tick();
  assert.equal((await restored.api.status()).lanes[0].nextAt, waiting.nextAt);
  restored.advance(10_000);
  await restored.api.tick();
  const started = (await restored.api.status()).lanes[0];
  assert.equal(started.status, 'scanning');
  const state = restored.local.data.reviewerAuxiliaryScans;
  state.lanes.departments.fingerprint = 'legacy-department-queue';
  await restored.local.set({ reviewerAuxiliaryScans: state });
  restored.advance(15_000);
  await restored.api.tick();
  const migrated = (await restored.api.status()).lanes[0];
  assert.equal(migrated.status, 'scanning');
  assert.equal(migrated.tabId, started.tabId);
  assert.ok(migrated.generation > started.generation);
  assert.equal(restored.actions.filter(action => action.type === 'update').length, 2);
});

test('restarting both lanes preserves the last navigation reservation and starts each lane once', async () => {
  const f = fixture({ settings: { pageDelaySeconds: 25, auxiliaryDepartments: true,
    auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  f.advance();
  await f.api.tick();
  const before = (await f.api.status()).lanes;
  const lastStart = f.local.data.reviewerAuxiliaryScans.lastTargetStartedAt;
  const updates = () => f.actions.filter(action => action.type === 'update').length;
  assert.equal(updates(), 2);
  await f.api.restart();
  assert.equal(updates(), 2, 'Restart cannot reload both targets immediately after a navigation');
  assert.equal(f.local.data.reviewerAuxiliaryScans.lastTargetStartedAt, lastStart);
  for (const lane of (await f.api.status()).lanes) {
    const previous = before.find(old => old.lane === lane.lane);
    assert.equal(lane.tabId, previous.tabId);
    assert.ok(lane.generation > previous.generation);
    assert.equal(lane.status, 'waiting');
    assert.equal(lane.nextAt, lastStart + 25_000);
    assert.equal((await f.message(lane.tabId, 'reviewer:role')).active, false);
    assert.equal((await f.message(lane.tabId, 'reviewer:page', {
      generation: previous.generation, page: 1, items: [{ item_id: 'stale-before-restart' }]
    })).ok, false);
  }
  f.advance(24_999);
  await f.api.tick();
  assert.equal(updates(), 2);
  f.advance(1);
  await f.api.tick();
  assert.equal(updates(), 3);
  let lanes = (await f.api.status()).lanes;
  assert.equal(lanes.find(lane => lane.lane === 'departments').status, 'scanning');
  assert.equal(lanes.find(lane => lane.lane === 'keywords').nextAt, f.now() + 25_000);
  f.advance(25_000);
  await f.api.tick();
  lanes = (await f.api.status()).lanes;
  assert.equal(updates(), 4);
  assert.ok(lanes.every(lane => lane.status === 'scanning'));
  assert.equal(f.actions.filter(action => action.type === 'create').length, 2);
  assert.equal(f.posts.length, 0);
});

test('a verification pause arriving inside restart preserves acknowledged checkpoints', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  await f.local.set({ [`sweep:${lane.tabId}`]: { generation: lane.generation,
    mode: 'none', visited: [1, 2], total: 4 } });
  await f.message(lane.tabId, 'reviewer:checkpoint', { generation: lane.generation });
  const before = structuredClone(f.local.data);
  const get = f.local.get;
  let inject = true;
  f.local.get = async keys => {
    const result = await get(keys);
    if (inject && keys.includes('reviewerAuxiliaryScans')) {
      inject = false;
      f.setPaused(200_000);
    }
    return result;
  };
  const result = await f.api.restart();
  assert.equal(result.ok, false);
  assert.equal(result.paused, true);
  assert.deepEqual(f.local.data, before);
});

test('a challenge during the final settings read cancels a pending auxiliary navigation', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  let navigationPrepared = false;
  const save = f.local.set;
  f.local.set = async values => {
    await save(values);
    if (values.reviewerAuxiliaryScans?.lanes?.keywords?.status === 'scanning') navigationPrepared = true;
  };
  f.setConfigRead(async () => { if (navigationPrepared) f.setPaused(200_000); });
  await f.api.tick();
  assert.equal(f.actions.filter(action => action.type === 'update').length, 0);
  assert.equal((await f.api.status()).lanes[0].status, 'paused');
});

test('pending department selection cannot ingest until a safe changed facet is bound', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true } });
  await f.api.tick();
  let lane = (await f.api.status()).lanes[0];
  let role = await f.message(lane.tabId, 'reviewer:role');
  const discovered = await f.message(lane.tabId, 'reviewer:aux-discovered', {
    generation: role.generation, candidates: [
      { kind: 'department', label: 'Home', parentUrl: home, url: `${home}?catId=4044` },
      { kind: 'department', label: 'HOME', parentUrl: home, url: `${home}?catId=4044` },
      { kind: 'department', label: 'Unsafe', parentUrl: 'https://example.com/' }
    ]
  });
  assert.equal(discovered.ok, true);
  assert.equal((await f.api.status()).lanes[0].totalTargets, 2);
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 0 });
  f.advance();
  await f.api.tick();
  role = await f.message(lane.tabId, 'reviewer:role');
  assert.equal(role.auxiliary.selection.label, 'Home');
  let ack = await f.message(lane.tabId, 'reviewer:page', { generation: role.generation,
    page: 1, items: [{ item_id: 'ip-12345678' }] });
  assert.equal(ack.ok, false);
  assert.equal(f.posts.length, 0);
  for (const url of [`${home}?catId=5438`, `${home}?q=changed`, `${home}?redirect=https://evil.test`, 'https://evil.test/reviews/claim-product', home]) {
    f.tabs.get(lane.tabId).url = url;
    ack = await f.message(lane.tabId, 'reviewer:aux-selected', { generation: role.generation, url });
    assert.equal(ack.ok, false, url);
  }
  f.tabs.get(lane.tabId).url = `${home}?catId=4044`;
  ack = await f.message(lane.tabId, 'reviewer:aux-selected', { generation: role.generation,
    url: `${home}?catId=4044` });
  assert.equal(ack.ok, true);
  assert.equal(ack.auxiliary.selection, null);
  f.tabs.get(lane.tabId).url = `${home}?catId=4044&page=2&affinityOverride=default`;
  ack = await f.message(lane.tabId, 'reviewer:page', { generation: role.generation,
    page: 2, items: [{ item_id: 'ip-12345678' }] });
  assert.equal(ack.ok, true);
  f.tabs.get(lane.tabId).url = `${home}?catId=9999&page=2`;
  ack = await f.message(lane.tabId, 'reviewer:page', { generation: role.generation,
    page: 2, items: [{ item_id: 'ip-12345678' }] });
  assert.equal(ack.ok, false);
  assert.equal(f.posts.length, 1);
});

test('bound departments cannot enqueue subdepartments, while optional brands stay in their department', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true, auxiliaryBrands: true } });
  await f.api.tick();
  let lane = (await f.api.status()).lanes[0];
  let role = await f.message(lane.tabId, 'reviewer:role');
  await f.message(lane.tabId, 'reviewer:aux-discovered', { generation: role.generation,
    candidates: [{ kind: 'department', label: 'Home', parentUrl: home, url: `${home}?catId=4044` }] });
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 0 });
  f.advance(); await f.api.tick();
  role = await f.message(lane.tabId, 'reviewer:role');
  const selected = `${home}?catId=4044`;
  f.tabs.get(lane.tabId).url = selected;
  await f.message(lane.tabId, 'reviewer:aux-selected', { generation: role.generation, url: selected });
  const result = await f.message(lane.tabId, 'reviewer:aux-discovered', { generation: role.generation,
    candidates: [{ kind: 'department', label: 'Furniture', parentUrl: selected },
      { kind: 'brand', label: 'Acme', parentUrl: selected }] });
  assert.equal(result.added, 1);
  const targets = f.local.data.reviewerAuxiliaryScans.lanes.departments.targets;
  assert.equal(targets.some(target => target.label === 'Furniture'), false);
  assert.equal(targets.some(target => target.label === 'Acme'), true);
});

test('legacy recursive department queues reset safely into top-level discovery after the scope update', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true } });
  await f.api.tick();
  const previous = f.local.data.reviewerAuxiliaryScans.lanes.departments;
  previous.fingerprint = JSON.stringify([false]);
  previous.targets.push({ key: 'old-child', kind: 'department', label: 'Furniture',
    targetUrl: `${home}?catId=4044`, selection: null });
  const generation = previous.generation;
  const tabId = previous.tabId;
  await f.api.tick();
  const current = f.local.data.reviewerAuxiliaryScans.lanes.departments;
  assert.ok(current.generation > generation);
  assert.equal(current.tabId, tabId);
  assert.equal(current.targets.length, 1);
  assert.equal(current.targets[0].kind, 'discovery');
  assert.equal(current.targets[0].targetUrl, home);
});

test('a repeatedly broken keyword skips that target while the department lane keeps its lease', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true, auxiliaryKeywords: true,
    auxiliaryKeywordQueries: ['Chair', 'Pet'] } });
  await f.api.tick();
  f.advance();
  await f.api.tick();
  const initial = (await f.api.status()).lanes;
  const departments = initial.find(lane => lane.lane === 'departments');
  const keywords = initial.find(lane => lane.lane === 'keywords');
  const homeRole = await f.message(departments.tabId, 'reviewer:role');
  const role = await f.message(keywords.tabId, 'reviewer:role');
  let currentRole = role;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await f.message(keywords.tabId, 'reviewer:unusable', { generation: currentRole.generation,
      reason: 'Layout changed', restartSweep: true });
    if (attempt < 2) {
      f.advance();
      await f.api.tick();
      currentRole = await f.message(keywords.tabId, 'reviewer:role');
    }
  }
  const unchanged = await f.message(departments.tabId, 'reviewer:role');
  assert.equal(unchanged.generation, homeRole.generation);
  assert.equal(unchanged.active, true);
  f.advance(120_000);
  await f.api.tick();
  const next = await f.message(keywords.tabId, 'reviewer:role');
  assert.equal(next.auxiliary.targetUrl, `${home}?q=Pet`);
  assert.ok(next.generation > role.generation);
  assert.equal((await f.api.status()).lanes.find(lane => lane.lane === 'keywords').failures, 1);
  assert.equal((await f.message(keywords.tabId, 'reviewer:page', { generation: role.generation,
    items: [{ item_id: 'stale' }], page: 1 })).ok, false);
});

test('a shared challenge revokes both generations and resume preserves acknowledged checkpoints', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const role = await f.message(lane.tabId, 'reviewer:role');
  await f.local.set({ [`sweep:${lane.tabId}`]: { generation: role.generation, mode: 'none', visited: [1, 2], total: 4 } });
  assert.equal((await f.message(lane.tabId, 'reviewer:checkpoint', { generation: role.generation })).ok, true);
  f.setPaused(500_000);
  await f.api.pause();
  assert.equal((await f.message(lane.tabId, 'reviewer:page', { generation: role.generation,
    items: [{ item_id: 'paused' }], page: 1 })).ok, false);
  f.setPaused(0);
  await f.api.tick();
  assert.equal((await f.api.status()).lanes[0].status, 'waiting');
  assert.deepEqual(f.local.data[`sweep:${lane.tabId}`].visited, [1, 2]);
  f.advance(15_000);
  await f.api.tick();
  const next = await f.message(lane.tabId, 'reviewer:role');
  assert.ok(next.generation > role.generation);
  assert.deepEqual(f.local.data[`sweep:${lane.tabId}`].visited, [1, 2]);
  assert.equal(f.local.data[`sweep:${lane.tabId}`].generation, next.generation);
  assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 0,
    'Verification does not spend the ordinary recovery budget');
});

test('a failed restored keyword resumes its checkpoint before one reset and a bounded skip', async () => {
  const first = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet', 'Chair'] } });
  await first.api.tick();
  const lane = (await first.api.status()).lanes[0];
  const role = await first.message(lane.tabId, 'reviewer:role');
  const key = `sweep:${lane.tabId}`;
  const visited = [1, 2, 3, 4, 5, 6];
  first.tabs.get(lane.tabId).url = `${home}?q=Pet&page=7`;
  await first.local.set({ [key]: { generation: role.generation, mode: 'none',
    targetKey: role.auxiliary.targetKey, visited, total: 25, discoveredDepartments: 2 } });
  await first.message(lane.tabId, 'reviewer:checkpoint', { generation: role.generation });
  const retry = await first.message(lane.tabId, 'reviewer:unusable', { generation: role.generation,
    reason: 'Restored page seven did not render', restartSweep: true });
  assert.equal(retry.active, false);
  assert.equal(retry.recover, 'waiting');
  assert.ok(retry.generation > role.generation);
  assert.deepEqual(first.local.data.reviewerAuxiliaryScans.lanes.keywords.checkpoint.visited, visited);

  // A recreated worker must retain the first recovery stage and saved URL.
  const second = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet', 'Chair'] },
    local: first.local, session: first.session, tabs: [...first.tabs.values()] });
  second.advance();
  await second.api.tick();
  let current = await second.message(lane.tabId, 'reviewer:role');
  assert.equal(second.tabs.get(lane.tabId).url, `${home}?q=Pet&page=7`);
  assert.deepEqual(second.local.data[key].visited, visited);
  assert.equal(second.local.data[key].generation, current.generation);
  assert.equal(second.local.data[key].discoveredDepartments, 2);

  const reset = await second.message(lane.tabId, 'reviewer:unusable', { generation: current.generation,
    reason: 'The resumed page is still unusable', restartSweep: true });
  assert.equal(reset.active, false);
  assert.ok(reset.generation > current.generation);
  assert.equal(second.local.data[key], undefined);
  assert.equal(second.local.data.reviewerAuxiliaryScans.lanes.keywords.checkpoint, null);
  second.advance();
  await second.api.tick();
  current = await second.message(lane.tabId, 'reviewer:role');
  assert.equal(second.tabs.get(lane.tabId).url, `${home}?q=Pet`);
  assert.equal(current.active, true);

  await second.message(lane.tabId, 'reviewer:unusable', { generation: current.generation,
    reason: 'Fresh page one remains unusable', restartSweep: true });
  second.advance();
  await second.api.tick();
  const next = await second.message(lane.tabId, 'reviewer:role');
  assert.equal(next.auxiliary.targetUrl, `${home}?q=Chair`);
  assert.equal((await second.api.status()).lanes[0].failures, 1);
  assert.equal((await second.message(lane.tabId, 'reviewer:page', { generation: role.generation,
    page: 7, items: [{ item_id: 'stale-restored-page' }] })).ok, false);
});

test('ordinary watchdog failures also resume the current page before a page-one reset', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const role = await f.message(lane.tabId, 'reviewer:role');
  f.tabs.get(lane.tabId).url = `${home}?q=Pet&page=4`;
  const stale = { ready: true, reviewerReady: false, active: true,
    phase: 'waiting-content', lastActivityAt: 0, nextActionAt: 0 };
  f.probes.set(lane.tabId, stale);
  f.advance(120_001);
  await f.api.tick();
  f.advance();
  await f.api.tick();
  assert.equal(f.tabs.get(lane.tabId).url, `${home}?q=Pet&page=4`);
  let current = await f.message(lane.tabId, 'reviewer:role');
  assert.ok(current.generation > role.generation);
  f.advance(120_001);
  await f.api.tick();
  f.advance();
  await f.api.tick();
  current = await f.message(lane.tabId, 'reviewer:role');
  assert.equal(current.active, true);
  assert.equal(f.tabs.get(lane.tabId).url, `${home}?q=Pet`);
});

test('browser session loss preserves the recovery stage and checkpoint in a newly owned tab', async () => {
  const first = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await first.api.tick();
  const lane = (await first.api.status()).lanes[0];
  const role = await first.message(lane.tabId, 'reviewer:role');
  first.tabs.get(lane.tabId).url = `${home}?q=Pet&page=3`;
  await first.local.set({ [`sweep:${lane.tabId}`]: { generation: role.generation, mode: 'none',
    targetKey: role.auxiliary.targetKey, visited: [1, 2], total: 4 } });
  await first.message(lane.tabId, 'reviewer:unusable', { generation: role.generation,
    reason: 'The current page stopped rendering' });

  const second = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] },
    local: first.local, tabs: [{ id: lane.tabId, url: `${home}?q=Manual`, status: 'complete' }] });
  second.advance();
  await second.api.tick();
  const resumed = (await second.api.status()).lanes[0];
  let current = await second.message(resumed.tabId, 'reviewer:role');
  assert.notEqual(resumed.tabId, lane.tabId);
  assert.equal(await second.api.ownsTab(lane.tabId), false);
  assert.equal(second.tabs.get(lane.tabId).url, `${home}?q=Manual`);
  assert.equal(second.tabs.get(resumed.tabId).url, `${home}?q=Pet&page=3`);
  assert.deepEqual(second.local.data[`sweep:${resumed.tabId}`].visited, [1, 2]);
  assert.equal(second.local.data[`sweep:${resumed.tabId}`].generation, current.generation);
  assert.equal(second.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 1);

  await second.message(resumed.tabId, 'reviewer:unusable', { generation: current.generation,
    reason: 'The resumed page is still unusable' });
  second.advance();
  await second.api.tick();
  current = await second.message(resumed.tabId, 'reviewer:role');
  assert.equal(current.active, true);
  assert.equal(second.tabs.get(resumed.tabId).url, `${home}?q=Pet`);
  assert.equal(second.local.data[`sweep:${resumed.tabId}`], undefined);
});

test('restored checkpoints older than a day restart the target without spending its failure budget', async () => {
  for (const [lastProgressAt, expired] of [[100_000, true], [130_000, false], [0, false], [undefined, false]]) {
    const first = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
    await first.api.tick();
    const lane = (await first.api.status()).lanes[0];
    const role = await first.message(lane.tabId, 'reviewer:role');
    first.advance();
    first.tabs.get(lane.tabId).url = `${home}?q=Pet&page=7`;
    await first.local.set({ [`sweep:${lane.tabId}`]: { generation: role.generation, mode: 'none',
      targetKey: role.auxiliary.targetKey, visited: [1, 2, 3, 4, 5, 6], total: 25,
      ...(lastProgressAt === undefined ? {} : { lastProgressAt }) } });
    await first.message(lane.tabId, 'reviewer:checkpoint', { generation: role.generation });

    const second = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] },
      local: first.local });
    second.advance(86_400_001);
    await second.api.tick();
    const resumed = (await second.api.status()).lanes[0];
    assert.equal(second.tabs.get(resumed.tabId).url,
      expired ? `${home}?q=Pet` : `${home}?q=Pet&page=7`, String(lastProgressAt));
    const state = second.local.data.reviewerAuxiliaryScans.lanes.keywords;
    assert.equal(state.targets[0].attempts, 0, 'Expiry does not consume the ordinary recovery budget');
    assert.equal(resumed.failures, 0);
    if (expired) {
      assert.equal(state.checkpoint, null);
      assert.equal(second.local.data[`sweep:${resumed.tabId}`], undefined);
    } else {
      assert.deepEqual(second.local.data[`sweep:${resumed.tabId}`].visited, [1, 2, 3, 4, 5, 6]);
    }
  }
});

test('browser session loss creates a fresh owned tab without adopting a reused tab ID', async () => {
  const first = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await first.api.tick();
  const previous = (await first.api.status()).lanes[0];
  const second = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] },
    local: first.local, tabs: [{ id: previous.tabId, url: `${home}?q=Manual`, status: 'complete' }] });
  await second.api.tick();
  assert.equal(second.actions.filter(action => action.type === 'create').length, 0,
    'The persisted start reservation survives losing session ownership');
  assert.equal(await second.api.ownsTab(previous.tabId), false);
  second.advance(15_000);
  await second.api.tick();
  assert.equal(second.actions.filter(action => action.type === 'create').length, 1);
  assert.equal(await second.api.ownsTab(previous.tabId), false);
  assert.equal(second.actions.some(action => action.type === 'update' && action.url.includes('Manual')), false);
});

test('disabled lanes become passive without disrupting or closing their tabs', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  f.configure({ auxiliaryKeywords: false });
  await f.api.tick();
  assert.equal((await f.message(lane.tabId, 'reviewer:role')).active, false);
  assert.equal((await f.api.status()).lanes[0].status, 'disabled');
  assert.equal(f.tabs.size, 1);
});

test('discovery queue overflow is visible and never reported as a complete round', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const role = await f.message(lane.tabId, 'reviewer:role');
  const ack = await f.message(lane.tabId, 'reviewer:aux-discovered', { generation: role.generation,
    candidates: Array.from({ length: 2100 }, (_, index) => ({ kind: 'department',
      label: `Department ${index}`, parentUrl: home, url: `${home}?catId=${index + 1}` })) });
  assert.equal(ack.ok, true);
  assert.ok(ack.truncated > 0);
  const status = (await f.api.status()).lanes[0];
  assert.equal(status.totalTargets, 2000);
  assert.ok(status.truncatedTargets > 0);
});

test('invalid keyword configuration remains visible without stopping department discovery', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true, auxiliaryKeywords: true,
    auxiliaryKeywordQueries: ['x'.repeat(81)] } });
  await f.api.tick();
  const { lanes } = await f.api.status();
  assert.equal(lanes.find(lane => lane.lane === 'departments').status, 'scanning');
  assert.match(lanes.find(lane => lane.lane === 'keywords').lastError, /80 characters/);
  assert.equal(f.actions.filter(action => action.type === 'create').length, 1);
});

test('an unbound facet that never renders retries from its parent with a fresh generation', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  let role = await f.message(lane.tabId, 'reviewer:role');
  await f.message(lane.tabId, 'reviewer:aux-discovered', { generation: role.generation,
    candidates: [{ kind: 'department', label: 'Home', parentUrl: home, url: `${home}?catId=4044` }] });
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 0 });
  f.advance();
  await f.api.tick();
  role = await f.message(lane.tabId, 'reviewer:role');
  f.tabs.get(lane.tabId).url = `${home}?catId=4044`;
  const ack = await f.message(lane.tabId, 'reviewer:unusable', { generation: role.generation,
    reason: 'The selected department never rendered', restartSweep: true });
  assert.equal(ack.active, false);
  assert.equal(ack.recover, 'waiting');
  assert.ok(ack.generation > role.generation);
  f.advance();
  await f.api.tick();
  assert.equal(f.tabs.get(lane.tabId).url, home);
  const retry = await f.message(lane.tabId, 'reviewer:role');
  assert.equal(retry.active, true);
  assert.equal(retry.auxiliary.selection.label, 'Home');
});

test('a completed lane retains its scheduled next round through a shared verification pause', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const role = await f.message(lane.tabId, 'reviewer:role');
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 2 });
  const completed = (await f.api.status()).lanes[0];
  assert.equal(completed.status, 'complete');
  f.setPaused(500_000);
  await f.api.pause();
  await f.api.tick();
  const waiting = (await f.api.status()).lanes[0];
  assert.equal(waiting.status, 'complete');
  assert.equal(waiting.nextAt, completed.nextAt);
  f.setPaused(0);
  assert.ok(completed.nextAt >= completed.completedAt + 300_000);
  assert.ok(completed.nextAt <= completed.completedAt + 330_000);
  f.advance(completed.nextAt - f.now() - 1);
  await f.api.tick();
  assert.equal((await f.api.status()).lanes[0].status, 'complete');
  f.advance(1);
  await f.api.tick();
  const resumed = (await f.api.status()).lanes[0];
  assert.equal(resumed.status, 'scanning');
  assert.equal(resumed.round, completed.round + 1);
});

test('facet selection preserves the exact control value while deduplicating normalized labels', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const role = await f.message(lane.tabId, 'reviewer:role');
  await f.message(lane.tabId, 'reviewer:aux-discovered', { generation: role.generation,
    candidates: [{ kind: 'department', label: '  Furniture  ', parentUrl: home, url: `${home}?catId=103150` },
      { kind: 'department', label: 'Furniture', parentUrl: home, url: `${home}?catId=103150` }] });
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 0 });
  f.advance();
  await f.api.tick();
  const selected = await f.message(lane.tabId, 'reviewer:role');
  assert.equal(selected.auxiliary.selection.label, '  Furniture  ');
  const status = (await f.api.status()).lanes[0];
  assert.equal(status.totalTargets, 2);
  assert.equal(status.targetLabel, 'Furniture');
});

test('a pause during delayed delivery diagnostics returns the revoked lease in its acknowledgement', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const role = await f.message(lane.tabId, 'reviewer:role');
  f.setDiagnostic(async event => {
    if (event === 'auxiliary-page-delivered') {
      f.setPaused(500_000);
      await f.api.pause();
    }
  });
  const ack = await f.message(lane.tabId, 'reviewer:page', { generation: role.generation,
    page: 1, items: [{ item_id: 'ip-12345678' }] });
  assert.equal(ack.ok, false);
  assert.equal(ack.active, false);
  assert.ok(ack.generation > role.generation);
});

test('site-capped targets remain marked limited and later targets continue normally', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Chair', 'Pet'] } });
  await f.api.tick();
  let lane = (await f.api.status()).lanes[0];
  let role = await f.message(lane.tabId, 'reviewer:role');
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation,
    pages: 25, coverageLimited: true });
  f.advance();
  await f.api.tick();
  lane = (await f.api.status()).lanes[0];
  assert.equal(lane.targetLabel, 'Pet');
  role = await f.message(lane.tabId, 'reviewer:role');
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 1 });
  lane = (await f.api.status()).lanes[0];
  assert.equal(lane.status, 'partial');
  assert.equal(lane.coverageLimitedTargets, 1);
  assert.equal(lane.failures, 0);
  assert.match(lane.lastError, /25-page limit; all available pages were scanned/i);
});

test('completed lanes reconcile disabled, increased and decreased refresh intervals', async () => {
  const f = fixture({ settings: { refreshMinutes: 0, auxiliaryKeywords: true,
    auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const role = await f.message(lane.tabId, 'reviewer:role');
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 1 });
  const completed = (await f.api.status()).lanes[0];
  assert.equal(completed.nextAt, 0);
  let deadline;
  for (const minutes of [5, 10, 2]) {
    f.configure({ refreshMinutes: minutes });
    await f.api.tick();
    deadline = (await f.api.status()).lanes[0].nextAt;
    assert.ok(deadline >= completed.completedAt + minutes * 60_000);
    assert.ok(deadline <= completed.completedAt + minutes * 66_000);
    await f.api.tick();
    assert.equal((await f.api.status()).lanes[0].nextAt, deadline,
      'Heartbeat ticks must not resample jitter and move the restart deadline');
  }
  f.advance(deadline - f.now());
  await f.api.tick();
  assert.equal((await f.api.status()).lanes[0].status, 'scanning');
});

test('a completed cycle keeps its jittered deadline across worker recreation without starvation', async () => {
  const settings = { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] };
  const first = fixture({ settings });
  await first.api.tick();
  const lane = (await first.api.status()).lanes[0];
  const role = await first.message(lane.tabId, 'reviewer:role');
  await first.message(lane.tabId, 'reviewer:sweep-complete', {
    generation: role.generation, pages: 2
  });
  const completed = (await first.api.status()).lanes[0];
  assert.ok(completed.nextAt > completed.completedAt + 300_000,
    'This cycle receives a positive offset instead of a fixed five-minute deadline');
  assert.ok(completed.nextAt <= completed.completedAt + 330_000);
  const restored = fixture({ settings, local: first.local, session: first.session,
    tabs: [...first.tabs.values()] });
  restored.advance(300_000);
  await restored.api.tick();
  assert.equal((await restored.api.status()).lanes[0].nextAt, completed.nextAt);
  assert.equal(restored.actions.filter(action => action.type === 'update').length, 0);
  restored.advance(completed.nextAt - restored.now());
  await restored.api.tick();
  const resumed = (await restored.api.status()).lanes[0];
  assert.equal(resumed.status, 'scanning');
  assert.equal(resumed.round, completed.round + 1);
  assert.equal(resumed.tabId, completed.tabId);
  assert.ok(resumed.generation > completed.generation);
  assert.equal(restored.actions.filter(action => action.type === 'update').length, 1);
  assert.equal(restored.actions.filter(action => action.type === 'create').length, 0);
});
