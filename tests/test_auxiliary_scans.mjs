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

test('explicit traffic waiting preserves retries and checkpoints while the assigned receiver answers', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Dog'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const original = await f.message(lane.tabId, 'reviewer:role');
  await f.local.set({ [`sweep:${lane.tabId}`]: { generation: lane.generation,
    targetKey: original.auxiliary.targetKey, mode: 'none', visited: [1], total: 10 } });
  await f.message(lane.tabId, 'reviewer:checkpoint', { generation: lane.generation });
  f.probes.set(lane.tabId, { ready: true, active: true, highTraffic: true,
    generation: lane.generation, phase: 'waiting-traffic', lastActivityAt: f.now(), nextActionAt: 0 });
  for (const elapsed of [121_000, 301_000, 900_000]) {
    f.advance(elapsed);
    await f.api.tick();
    const saved = f.local.data.reviewerAuxiliaryScans.lanes.keywords;
    assert.equal(saved.status, 'scanning');
    assert.equal(saved.targets[0].attempts, 0);
    assert.deepEqual(saved.checkpoint.visited, [1]);
  }
  f.probes.set(lane.tabId, { ready: false });
  f.advance(121_000);
  await f.api.tick();
  assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 1,
    'A missing receiver must still recover after explicit traffic evidence is lost');
});

test('traffic on a changed committed or pending scope cannot keep an obsolete assignment alive', async () => {
  for (const pending of [false, true]) for (const suffix of ['?q=Cat&queryNups=Cat', '?q=Dog&queryNups=Dog&unknown=private']) {
    const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Dog'] } });
    await f.api.tick();
    const lane = (await f.api.status()).lanes[0];
    const tab = f.tabs.get(lane.tabId);
    tab[pending ? 'pendingUrl' : 'url'] = `${home}${suffix}`;
    f.advance(121_000);
    f.probes.set(lane.tabId, { ready: true, active: true, highTraffic: true,
      generation: lane.generation, phase: 'waiting-traffic', lastActivityAt: f.now() });
    await f.api.tick();
    assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 1);
    assert.equal(f.roles.some(message => message.active === false), true);
  }
});

test('traffic waiting accepts the exact authorized destination of a pending department selection', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true } });
  await f.api.tick();
  let lane = (await f.api.status()).lanes[0];
  let role = await f.message(lane.tabId, 'reviewer:role');
  await f.message(lane.tabId, 'reviewer:aux-discovered', { generation: role.generation,
    candidates: [{ kind: 'department', label: 'Home', parentUrl: home, url: `${home}?catId=4044` }] });
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 0 });
  f.advance();
  await f.api.tick();
  role = await f.message(lane.tabId, 'reviewer:role');
  lane = (await f.api.status()).lanes[0];
  assert.equal(role.auxiliary.selection.url, `${home}?catId=4044`);
  f.tabs.get(lane.tabId).pendingUrl = role.auxiliary.selection.url;
  f.probes.set(lane.tabId, { ready: true, active: true, highTraffic: true,
    generation: lane.generation, phase: 'waiting-traffic', lastActivityAt: f.now() });
  f.advance(900_000);
  await f.api.tick();
  const saved = f.local.data.reviewerAuxiliaryScans.lanes.departments;
  assert.equal(saved.status, 'scanning');
  assert.equal(saved.targets[saved.cursor].attempts, 0);
});

for (const phase of ['waiting-focus', 'waiting-content', 'waiting-selection']) {
  test(`responding ${phase} receives bounded opportunity without consuming retries`, async () => {
    const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
    await f.api.tick();
    const lane = (await f.api.status()).lanes[0];
    f.advance(121_000);
    f.probes.set(lane.tabId, { ready: true, active: true, generation: lane.generation,
      documentId: 99_000, phase, busy: phase !== 'waiting-focus', lastActivityAt: 100_000, nextActionAt: 0 });
    await f.api.tick();
    assert.equal((await f.api.status()).lanes[0].status, 'scanning');
    assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 0);
    // Once real input ends, a stalled focus waiter still has a finite bound.
    f.probes.set(lane.tabId, { ready: true, active: true, generation: lane.generation,
      phase: 'waiting-focus', busy: false, lastActivityAt: f.now(), nextActionAt: f.now() + 15_000 });
    f.advance(301_000);
    await f.api.tick();
    assert.equal((await f.api.status()).lanes[0].status, 'waiting', 'No progress still has a finite recovery bound');
    assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 1);
  });
}

test('fresh focus heartbeats cannot renew the coordinator useful-progress bound', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  for (let tick = 0; tick < 11; tick += 1) {
    f.advance(30_000);
    f.probes.set(lane.tabId, { ready: true, active: true, generation: lane.generation,
      phase: 'waiting-focus', busy: false, lastActivityAt: f.now(), nextActionAt: f.now() + 5_000 });
    await f.api.tick();
  }
  assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 1);
});

for (const focusReason of ['expired', 'revoked', 'activation-unavailable']) {
  test(`scheduler denial ${focusReason} is bounded despite the waiting-user phase label`, async () => {
    const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
    await f.api.tick();
    const lane = (await f.api.status()).lanes[0];
    for (let tick = 0; tick < 11; tick += 1) {
      f.advance(30_000);
      f.probes.set(lane.tabId, { ready: true, active: true, generation: lane.generation,
        phase: 'waiting-user', busy: false, focusReason, lastActivityAt: f.now(), nextActionAt: f.now() + 5_000 });
      await f.api.tick();
    }
    assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 1);
  });
}

for (const focusReason of ['manual-tab-active', 'user-active']) {
  test(`actual ${focusReason} guard suspends scheduler budget in waiting-focus`, async () => {
    const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
    await f.api.tick();
    const lane = (await f.api.status()).lanes[0];
    for (let tick = 0; tick < 12; tick += 1) {
      f.advance(30_000);
      f.probes.set(lane.tabId, { ready: true, active: true, generation: lane.generation,
        phase: 'waiting-focus', busy: false, focusReason, lastActivityAt: f.now(), nextActionAt: 0 });
      await f.api.tick();
    }
    assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 0);
    f.probes.set(lane.tabId, { ready: true, active: true, generation: lane.generation,
      phase: 'waiting-focus', busy: false, focusReason: 'busy', lastActivityAt: f.now(), nextActionAt: 0 });
    f.advance(30_000);
    await f.api.tick();
    assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 0,
      'Guarded time cannot spend the resumed opportunity');
    f.advance(300_000);
    await f.api.tick();
    assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 1);
  });
}

test('a stale generation cannot obtain the responding-wait grace', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  f.advance(121_000);
  f.probes.set(lane.tabId, { ready: true, active: true, generation: lane.generation - 1,
    phase: 'waiting-focus', lastActivityAt: f.now(), nextActionAt: f.now() + 15_000 });
  await f.api.tick();
  assert.equal((await f.api.status()).lanes[0].status, 'waiting');
});

test('retry/skip diagnostics retain the failing identity and explicit transport code', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  const events = [];
  f.setDiagnostic((event, detail) => events.push({ event, ...detail }));
  await f.api.tick();
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const lane = (await f.api.status()).lanes[0];
    const reply = await f.message(lane.tabId, 'reviewer:unusable', { generation: lane.generation,
      reason: 'Private page body must not be logged', failureCode: 'document-http-error',
      documentId: 90_000, responseStatus: 520 });
    const receipt = events.findLast(e => e.event === (attempt === 3 ? 'auxiliary-target-skipped' : 'auxiliary-target-retry'));
    assert.equal(receipt.tabId, lane.tabId);
    assert.equal(receipt.generation, lane.generation, 'Identity must precede advance/generation replacement');
    assert.equal(receipt.round, 1);
    assert.equal(receipt.targetIndex, 1);
    assert.equal(receipt.attempt, attempt);
    assert.equal(receipt.failureCode, 'document-http-error');
    assert.equal(receipt.responseStatus, 520);
    assert.equal(receipt.documentId, 90_000);
    if (attempt < 3) assert.equal(reply.retryAfterMs, 30_000 * attempt);
    f.advance(reply.retryAfterMs || 30_000);
    if (attempt < 3) await f.api.tick();
  }
  assert.equal((await f.api.status()).lanes[0].unresolvedTargets, 1);
});

test('auxiliary intake attributes first discoveries to an opaque target identity', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] },
    intakeSummary: { new: 1, duplicates: 1, matched: 1, notified: 0 } });
  const events = [];
  f.setDiagnostic((event, detail) => events.push({ event, ...detail }));
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  await f.message(lane.tabId, 'reviewer:page', { generation: lane.generation,
    page: 1, items: [{ item_id: 'a' }, { item_id: 'b' }] });
  const receipt = events.find(e => e.event === 'auxiliary-page-delivered');
  assert.equal(receipt.round, 1);
  assert.equal(receipt.targetIndex, 1);
  assert.equal(receipt.new, 1);
  assert.equal(receipt.duplicates, 1);
  assert.equal(receipt.items, 2);
  assert.ok(Number.isSafeInteger(receipt.durationMs));
  assert.equal(receipt.target, undefined, 'No search text in the attribution receipt');
});

test('observed affinity drift remains unauthorized and recovers the first missing bound page', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Dog'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const original = await f.message(lane.tabId, 'reviewer:role');
  await f.local.set({ [`sweep:${lane.tabId}`]: { generation: lane.generation,
    targetKey: original.auxiliary.targetKey, mode: 'none', visited: [1, 2, 3, 4, 5], total: 10 } });
  await f.message(lane.tabId, 'reviewer:checkpoint', { generation: lane.generation });
  f.tabs.get(lane.tabId).url = `${original.auxiliary.targetUrl}&page=6&affinityOverride=store_led`;
  assert.equal((await f.message(lane.tabId, 'reviewer:role')).active, false);
  f.advance(121_000);
  f.probes.set(lane.tabId, { ready: true, active: false, busy: true, generation: lane.generation });
  await f.api.tick();
  assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 0,
    'Actual input must defer recovery even in a managed tab');
  f.probes.set(lane.tabId, { ready: true, active: false, busy: false, generation: lane.generation, documentId: 90_000 });
  await f.api.tick();
  assert.equal((await f.api.status()).lanes[0].status, 'waiting');
  f.advance(30_000);
  await f.api.tick();
  const resumed = await f.message(lane.tabId, 'reviewer:role');
  assert.equal(f.tabs.get(lane.tabId).url, `${original.auxiliary.targetUrl}&page=6`);
  assert.equal(resumed.auxiliary.urlPagination, true);
  assert.equal(resumed.active, true);
  assert.deepEqual(f.local.data[`sweep:${lane.tabId}`].visited, [1, 2, 3, 4, 5]);
});

for (const suffix of ['&q=Cat', '&facet=brand:custom', '&affinityOverride=store_led', '&affinityOverride=unknown']) {
  test(`affinity recovery does not broaden a changed or ambiguous scope: ${suffix}`, async () => {
    const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Dog'] } });
    await f.api.tick();
    const lane = (await f.api.status()).lanes[0];
    const role = await f.message(lane.tabId, 'reviewer:role');
    f.tabs.get(lane.tabId).url = `${role.auxiliary.targetUrl}&page=6&affinityOverride=store_led${suffix}`;
    f.probes.set(lane.tabId, { ready: true, active: false, busy: false, generation: lane.generation });
    await f.api.tick();
    assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].urlPagination, undefined);
    assert.equal((await f.message(lane.tabId, 'reviewer:role')).active, false);
  });
}

test('all enabled lanes reserve owned tabs immediately while reviewer loads stay staggered', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true, auxiliaryKeywords: true,
    auxiliaryKeywordQueries: ['Pet'] }, initialStartDelay: () => 30_000 });
  await f.api.tick();
  assert.equal(f.actions.filter(action => action.type === 'create').length, 2,
    'Both configured scanner slots must exist before either delayed scan starts');
  assert.equal(f.actions.filter(action => action.type === 'update').length, 0,
    'Reserving a slot must not spend the initial navigation delay');
  assert.equal((await f.api.managedTabIds()).length, 2);
  await f.api.tick();
  assert.equal(f.actions.filter(action => action.type === 'create').length, 2,
    'Repeated startup ticks must reuse the reserved slots');
  f.advance(30_000);
  await f.api.tick();
  assert.equal(f.actions.filter(action => action.type === 'update').length, 1,
    'Initial reviewer page loads remain paced');
});

test('verified empty completion is durable coverage and separately counted from failed targets', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Empty'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const role = await f.message(lane.tabId, 'reviewer:role');
  f.local.data[`sweep:${lane.tabId}`] = { generation: role.generation,
    targetKey: role.auxiliary.targetKey, mode: 'none', visited: [], total: 0,
    terminalPage: 0, terminalReason: 'confirmed-empty', lastObservedPage: 1, emptyResult: true };
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation,
    pages: 0, coverageLimited: false, emptyResult: true });
  const completed = (await f.api.status()).lanes[0];
  assert.equal(completed.status, 'complete');
  assert.equal(completed.completedTargets, 1);
  assert.equal(completed.emptyTargets, 1);
  assert.equal(completed.unresolvedTargets, 0);
  assert.equal(completed.failures, 0);
  assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].result, 'empty');
});

for (const invalid of ['missing', 'generation', 'target', 'visited', 'terminal']) {
  test(`unverified empty completion cannot clear coverage: ${invalid}`, async () => {
    const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Empty'] } });
    await f.api.tick();
    const lane = (await f.api.status()).lanes[0];
    const role = await f.message(lane.tabId, 'reviewer:role');
    const receipt = { generation: role.generation, targetKey: role.auxiliary.targetKey,
      visited: [], total: 0, terminalPage: 0, terminalReason: 'confirmed-empty', lastObservedPage: 1, emptyResult: true };
    if (invalid === 'generation') receipt.generation -= 1;
    if (invalid === 'target') receipt.targetKey = 'keyword:other';
    if (invalid === 'visited') receipt.visited = [1];
    if (invalid === 'terminal') receipt.terminalPage = 2;
    if (invalid !== 'missing') f.local.data[`sweep:${lane.tabId}`] = receipt;
    const reply = await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation,
      pages: 0, coverageLimited: false, emptyResult: true });
    assert.equal(reply.ok, false);
    const current = (await f.api.status()).lanes[0];
    assert.equal(current.completedTargets, 0);
    assert.equal(current.emptyTargets, 0);
  });
}

for (const status of ['complete', 'partial']) {
  test(`a ${status} lane restores its parked slot before the next scheduled round`, async () => {
    const settings = { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] };
    const first = fixture({ settings });
    await first.api.tick();
    const lane = (await first.api.status()).lanes[0];
    const role = await first.message(lane.tabId, 'reviewer:role');
    await first.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 1 });
    first.local.data.reviewerAuxiliaryScans.lanes.keywords.status = status;
    const previous = structuredClone(first.local.data.reviewerAuxiliaryScans.lanes.keywords);
    const restored = fixture({ settings, local: first.local });
    await restored.api.tick();
    const current = (await restored.api.status()).lanes[0];
    assert.equal(restored.actions.filter(action => action.type === 'create').length, 1);
    assert.equal(restored.actions.filter(action => action.type === 'update').length, 0,
      'Restoring the parked slot must not start its future round early');
    assert.equal(current.status, status);
    assert.equal(current.nextAt, previous.nextAt);
    assert.equal(current.round, previous.round);
    assert.equal((await restored.api.managedTabIds()).length, 1);
  });
}

test('optional lanes reserve exactly two owned tabs and never adopt a manual tab', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true, auxiliaryKeywords: true,
    auxiliaryKeywordQueries: ['Chair', 'chair', 'Pet'] },
  tabs: [{ id: 7, url: `${home}?catId=4044`, status: 'complete' }] });
  await f.api.tick();
  assert.equal(f.actions.filter(action => action.type === 'create').length, 2,
    'Both enabled lanes reserve their own tab immediately');
  assert.equal(f.actions.filter(action => action.type === 'update').length, 1,
    'The second reviewer load starts after the first lane navigation interval');
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
  assert.equal(role.auxiliary.targetUrl, `${home}?q=Chair&queryNups=Chair`);
  assert.equal(role.auxiliary.selection, null);
});

test('keyword targets use native reviewer query parameters and retain literal suffixes', async () => {
  for (const query of ['Large Appliance', 'Large Appliance*', 'Kitchen Appliance&']) {
    const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: [query] } });
    await f.api.tick();
    const lane = (await f.api.status()).lanes[0];
    const role = await f.message(lane.tabId, 'reviewer:role');
    const target = new URL(role.auxiliary.targetUrl);
    assert.equal(target.searchParams.get('q'), query);
    assert.equal(target.searchParams.get('queryNups'), query,
      'The native inline reviewer search submits the actual term through queryNups');
    assert.deepEqual([...target.searchParams.keys()], ['q', 'queryNups']);
    assert.equal((await f.message(lane.tabId, 'reviewer:page', { generation: role.generation,
      page: 1, items: [{ item_id: 'confirmed-target' }] })).ok, true);
    target.searchParams.delete('queryNups');
    f.tabs.get(lane.tabId).url = target.toString();
    assert.equal((await f.message(lane.tabId, 'reviewer:page', { generation: role.generation,
      page: 1, items: [{ item_id: 'old-query-route' }] })).ok, false,
      'A stale q-only document must not publish for the native search lease');
  }
});

test('a stored q-only keyword checkpoint finishes under its original lease before native restart', async () => {
  const settings = { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] };
  const first = fixture({ settings });
  await first.api.tick();
  const lane = (await first.api.status()).lanes[0];
  const role = await first.message(lane.tabId, 'reviewer:role');
  const legacy = new URL(home);
  legacy.searchParams.set('q', 'Pet');
  first.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].targetUrl = legacy.toString();
  first.tabs.get(lane.tabId).url = `${legacy}&page=3`;
  await first.local.set({ [`sweep:${lane.tabId}`]: { generation: role.generation, mode: 'none',
    targetKey: role.auxiliary.targetKey, visited: [1, 2], total: 4 } });
  assert.equal((await first.message(lane.tabId, 'reviewer:checkpoint', {
    generation: role.generation })).ok, true);

  const restored = fixture({ settings, local: first.local, session: first.session,
    tabs: [...first.tabs.values()] });
  await restored.api.tick();
  const current = await restored.message(lane.tabId, 'reviewer:role');
  assert.equal(current.auxiliary.targetUrl, legacy.toString());
  assert.equal(restored.tabs.get(lane.tabId).url, `${legacy}&page=3`);
  assert.equal((await restored.message(lane.tabId, 'reviewer:page', { generation: current.generation,
    page: 3, items: [{ item_id: 'legacy-checkpoint' }] })).ok, true);

  await restored.message(lane.tabId, 'reviewer:sweep-complete', { generation: current.generation, pages: 4 });
  const completed = (await restored.api.status()).lanes[0];
  restored.advance(completed.nextAt - restored.now());
  await restored.api.tick();
  const next = await restored.message(lane.tabId, 'reviewer:role');
  assert.equal(new URL(next.auxiliary.targetUrl).searchParams.get('queryNups'), 'Pet');
  assert.deepEqual(restored.local.data.reviewerAuxiliaryScans.lanes.keywords.checkpoint, null);
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
  assert.equal(f.actions.filter(action => action.type === 'create').length, 2);
  assert.equal(f.actions.filter(action => action.type === 'update').length, 0);
  const deadlines = Object.fromEntries((await f.api.status()).lanes.map(lane => [lane.lane, lane.nextAt]));
  assert.deepEqual(deadlines, { departments: f.now() + 20_000, keywords: f.now() + 60_000 });
  f.advance(19_999);
  await f.api.tick();
  assert.equal(f.actions.filter(action => action.type === 'create').length, 2);
  assert.equal(f.actions.filter(action => action.type === 'update').length, 0);
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
  assert.equal((await f.api.status()).lanes[0].totalTargets, 1,
    'The discovery control is not a covered department');
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
  assert.equal(next.auxiliary.targetUrl, `${home}?q=Pet&queryNups=Pet`);
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

test('a failed restored keyword keeps its checkpoint through bounded retries and deferral', async () => {
  const first = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet', 'Chair'] } });
  await first.api.tick();
  const lane = (await first.api.status()).lanes[0];
  const role = await first.message(lane.tabId, 'reviewer:role');
  const key = `sweep:${lane.tabId}`;
  const visited = [1, 2, 3, 4, 5, 6];
  first.tabs.get(lane.tabId).url = `${home}?q=Pet&queryNups=Pet&page=7`;
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
  assert.equal(second.tabs.get(lane.tabId).url, `${home}?q=Pet&queryNups=Pet&page=7`);
  assert.deepEqual(second.local.data[key].visited, visited);
  assert.equal(second.local.data[key].generation, current.generation);
  assert.equal(second.local.data[key].discoveredDepartments, 2);

  const retryAgain = await second.message(lane.tabId, 'reviewer:unusable', { generation: current.generation,
    reason: 'The resumed page is still unusable', restartSweep: true });
  assert.equal(retryAgain.active, false);
  assert.ok(retryAgain.generation > current.generation);
  assert.deepEqual(second.local.data[key].visited, visited);
  assert.deepEqual(second.local.data.reviewerAuxiliaryScans.lanes.keywords.checkpoint.visited, visited);
  second.advance();
  await second.api.tick();
  current = await second.message(lane.tabId, 'reviewer:role');
  assert.equal(second.tabs.get(lane.tabId).url, `${home}?q=Pet&queryNups=Pet&page=7`);
  assert.equal(current.active, true);

  await second.message(lane.tabId, 'reviewer:unusable', { generation: current.generation,
    reason: 'The outstanding page remains unusable', restartSweep: true });
  second.advance();
  await second.api.tick();
  const next = await second.message(lane.tabId, 'reviewer:role');
  assert.equal(next.auxiliary.targetUrl, `${home}?q=Chair&queryNups=Chair`);
  assert.equal((await second.api.status()).lanes[0].failures, 1);
  assert.equal((await second.api.status()).lanes[0].unresolvedTargets, 1);
  assert.deepEqual(second.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].checkpoint.visited, visited);
  assert.equal((await second.message(lane.tabId, 'reviewer:page', { generation: role.generation,
    page: 7, items: [{ item_id: 'stale-restored-page' }] })).ok, false);
});

test('ordinary watchdog failures continue resuming the outstanding page', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const role = await f.message(lane.tabId, 'reviewer:role');
  f.tabs.get(lane.tabId).url = `${home}?q=Pet&queryNups=Pet&page=4`;
  const stale = { ready: true, reviewerReady: false, active: true,
    phase: 'waiting-content', lastActivityAt: 0, nextActionAt: 0 };
  f.probes.set(lane.tabId, stale);
  f.advance(120_001);
  await f.api.tick();
  f.advance();
  await f.api.tick();
  assert.equal(f.tabs.get(lane.tabId).url, `${home}?q=Pet&queryNups=Pet&page=4`);
  let current = await f.message(lane.tabId, 'reviewer:role');
  assert.ok(current.generation > role.generation);
  f.advance(120_001);
  await f.api.tick();
  f.advance();
  await f.api.tick();
  current = await f.message(lane.tabId, 'reviewer:role');
  assert.equal(current.active, true);
  assert.equal(f.tabs.get(lane.tabId).url, `${home}?q=Pet&queryNups=Pet&page=4`);
});

test('browser session loss preserves the recovery stage and checkpoint in a newly owned tab', async () => {
  const first = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await first.api.tick();
  const lane = (await first.api.status()).lanes[0];
  const role = await first.message(lane.tabId, 'reviewer:role');
  first.tabs.get(lane.tabId).url = `${home}?q=Pet&queryNups=Pet&page=3`;
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
  assert.equal(second.tabs.get(resumed.tabId).url, `${home}?q=Pet&queryNups=Pet&page=3`);
  assert.deepEqual(second.local.data[`sweep:${resumed.tabId}`].visited, [1, 2]);
  assert.equal(second.local.data[`sweep:${resumed.tabId}`].generation, current.generation);
  assert.equal(second.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].attempts, 1);

  await second.message(resumed.tabId, 'reviewer:unusable', { generation: current.generation,
    reason: 'The resumed page is still unusable' });
  second.advance();
  await second.api.tick();
  current = await second.message(resumed.tabId, 'reviewer:role');
  assert.equal(current.active, true);
  assert.equal(second.tabs.get(resumed.tabId).url, `${home}?q=Pet&queryNups=Pet&page=3`);
  assert.deepEqual(second.local.data[`sweep:${resumed.tabId}`].visited, [1, 2]);
});

test('restored checkpoints retain acknowledged pages regardless of age without spending failure budget', async () => {
  for (const lastProgressAt of [100_000, 130_000, 0, undefined]) {
    const first = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
    await first.api.tick();
    const lane = (await first.api.status()).lanes[0];
    const role = await first.message(lane.tabId, 'reviewer:role');
    first.advance();
    first.tabs.get(lane.tabId).url = `${home}?q=Pet&queryNups=Pet&page=7`;
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
      `${home}?q=Pet&queryNups=Pet&page=7`, String(lastProgressAt));
    const state = second.local.data.reviewerAuxiliaryScans.lanes.keywords;
    assert.equal(state.targets[0].attempts, 0, 'Expiry does not consume the ordinary recovery budget');
    assert.equal(resumed.failures, 0);
    assert.deepEqual(state.checkpoint.visited, [1, 2, 3, 4, 5, 6]);
    assert.deepEqual(second.local.data[`sweep:${resumed.tabId}`].visited, [1, 2, 3, 4, 5, 6]);
  }
});

test('browser session loss creates a fresh owned tab without adopting a reused tab ID', async () => {
  const first = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await first.api.tick();
  const previous = (await first.api.status()).lanes[0];
  const second = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] },
    local: first.local, tabs: [{ id: previous.tabId, url: `${home}?q=Manual`, status: 'complete' }] });
  await second.api.tick();
  assert.equal(second.actions.filter(action => action.type === 'create').length, 1,
    'The scanner reserves its replacement slot before the persisted navigation deadline');
  assert.equal(second.actions.filter(action => action.type === 'update').length, 0,
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
  assert.equal(status.totalTargets, 1999, 'The bounded queue also contains its discovery entry');
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

test('appending five searches to an active hundred-target queue preserves every old receipt and lease', async () => {
  const queries = Array.from({ length: 100 }, (_, index) => `Item ${index}`);
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: queries } });
  await f.api.tick();
  const state = f.local.data.reviewerAuxiliaryScans;
  const lane = state.lanes.keywords;
  lane.cursor = 10;
  lane.pages = 41;
  for (const target of lane.targets.slice(0, 10)) {
    Object.assign(target, { coverageStatus: 'complete', completedAt: f.now(), attemptedAt: f.now(), pages: 3 });
  }
  Object.assign(lane.targets[10], { coverageStatus: 'unresolved', attemptedAt: f.now(),
    checkpoint: { generation: lane.generation, targetKey: lane.targets[10].key, mode: 'none', visited: [1, 2, 5], total: 150 } });
  Object.assign(lane.targets[20], { coverageStatus: 'unresolved', checkpoint: {
    generation: lane.generation-1, targetKey: lane.targets[20].key, mode: 'none', visited: [1, 2], total: 11 } });
  lane.checkpoint = structuredClone(lane.targets[10].checkpoint);
  lane.checkpointUrl = `${lane.targets[10].targetUrl}&page=3`;
  f.tabs.get(lane.tabId).url = lane.checkpointUrl;
  await f.local.set({ [`sweep:${lane.tabId}`]: lane.checkpoint });
  const before = structuredClone(lane);
  const actions = f.actions.filter(action => ['create', 'update', 'reload'].includes(action.type)).length;
  const roles = f.roles.length;
  f.configure({ auxiliaryKeywordQueries: [...queries, 'Alloy', 'Red Light', 'Gingerbread', 'Football', 'Mirror'] });
  await f.api.tick();
  const updated = f.local.data.reviewerAuxiliaryScans.lanes.keywords;
  assert.equal(updated.targets.length, 105);
  assert.deepEqual(updated.targets.slice(0, 100), before.targets);
  for (const key of ['generation', 'cursor', 'pages', 'round', 'status', 'startedAt', 'lastActivityAt', 'checkpoint', 'checkpointUrl']) {
    assert.deepEqual(updated[key], before[key], key);
  }
  assert.deepEqual(updated.targets.slice(100).map(target => target.label),
    ['Alloy', 'Red Light', 'Gingerbread', 'Football', 'Mirror']);
  assert.deepEqual(f.local.data[`sweep:${lane.tabId}`], before.checkpoint);
  assert.equal(f.actions.filter(action => ['create', 'update', 'reload'].includes(action.type)).length, actions);
  assert.equal(f.roles.length, roles, 'An unchanged live target needs no revocation/reset');
});

test('appended keyword targets avoid duplicate keys already present in unresolved carried work', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = f.local.data.reviewerAuxiliaryScans.lanes.keywords;
  lane.targets.push({ key: 'keyword:mirror', kind: 'keyword', label: 'Mirror',
    targetUrl: `${home}?q=Mirror&queryNups=Mirror`, selection: null, attempts: 1,
    coverageStatus: 'unresolved', checkpoint: { visited: [1], targetKey: 'keyword:mirror' } });
  const prior = structuredClone(lane.targets[1]);
  f.configure({ auxiliaryKeywordQueries: ['Pet', 'Mirror', 'mirror', 'Alloy'] });
  await f.api.tick();
  const updated = f.local.data.reviewerAuxiliaryScans.lanes.keywords;
  assert.deepEqual(updated.targets.map(target => target.key), ['keyword:pet', 'keyword:mirror', 'keyword:alloy']);
  assert.deepEqual(updated.targets[1], prior);
});

for (const completeStatus of ['complete', 'partial']) {
  test(`appending to a ${completeStatus} keyword round continues at its existing cursor with normal pacing`, async () => {
    const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
    await f.api.tick();
    const lane = (await f.api.status()).lanes[0];
    await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: lane.generation,
      pages: 12, coverageLimited: completeStatus === 'partial' });
    const before = structuredClone(f.local.data.reviewerAuxiliaryScans.lanes.keywords);
    f.configure({ auxiliaryKeywordQueries: ['Pet', 'Mirror'] });
    await f.api.tick();
    const continued = f.local.data.reviewerAuxiliaryScans.lanes.keywords;
    assert.equal(continued.generation, before.generation);
    assert.equal(continued.cursor, before.cursor);
    assert.equal(continued.pages, before.pages);
    assert.deepEqual(continued.targets[0], before.targets[0]);
    assert.equal(continued.status, 'waiting');
    assert.equal(continued.completedAt, 0);
    assert.equal(continued.nextAt, f.now() + 15_000);
    f.advance(15_000); await f.api.tick();
    assert.equal(new URL(f.tabs.get(lane.tabId).url).searchParams.get('q'), 'Mirror');
  });
}

test('editing, removing or reordering old keyword prefixes still resets their query scope', async () => {
  for (const queries of [['Pet', 'Dog', 'Alloy'], ['Cat', 'Pet', 'Mirror'], ['Pet'], ['Pet', 'Alloy', 'Cat']]) {
    const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet', 'Cat'] } });
    await f.api.tick();
    const before = structuredClone(f.local.data.reviewerAuxiliaryScans.lanes.keywords);
    f.configure({ auxiliaryKeywordQueries: queries }); await f.api.tick();
    const after = f.local.data.reviewerAuxiliaryScans.lanes.keywords;
    assert.ok(after.generation > before.generation);
    assert.equal(after.cursor, 0);
    assert.deepEqual(after.targets.map(target => target.label), queries);
  }
});

test('an in-flight page acknowledgement cannot regain authority after an appended queue advances targets', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  let observed;
  const atDiagnostic = new Promise(resolve => { observed = resolve; });
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  f.setDiagnostic(async event => { if (event === 'auxiliary-page-delivered') { observed(); await blocked; } });
  const pending = f.message(lane.tabId, 'reviewer:page', { generation: lane.generation,
    documentId: 9000, page: 1, items: [{ item_id: 'ip-123456789' }] });
  await atDiagnostic;
  f.configure({ auxiliaryKeywordQueries: ['Pet', 'Mirror'] }); await f.api.tick();
  assert.equal(f.local.data.reviewerAuxiliaryScans.lanes.keywords.generation, lane.generation);
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: lane.generation, pages: 1 });
  release();
  const reply = await pending;
  assert.equal(reply.ok, false);
  assert.equal(reply.active, false);
  assert.equal(reply.reason, 'stale-generation');
  assert.equal(reply.auxiliary.targetKey, 'keyword:mirror');
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
  assert.equal(status.totalTargets, 1);
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
  assert.match(lane.lastError, /Terminal page coverage could not be verified/i);
  assert.equal(lane.completedTargets, 1);
  assert.equal(lane.attemptedTargets, 2);
  assert.equal(lane.unresolvedTargets, 1);
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

test('failed deep work survives retries, healthy targets, and a browser round rollover', async () => {
  const settings = { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Chair', 'Pet'] };
  const first = fixture({ settings });
  await first.api.tick();
  const initial = (await first.api.status()).lanes[0];
  let role = await first.message(initial.tabId, 'reviewer:role');
  const oldGeneration = role.generation;
  const visited = Array.from({ length: 125 }, (_, i) => i + 1);
  first.tabs.get(initial.tabId).url = `${home}?q=Chair&queryNups=Chair&page=126`;
  await first.local.set({ [`sweep:${initial.tabId}`]: { generation: role.generation,
    targetKey: role.auxiliary.targetKey, mode: 'none', visited, total: 150 } });
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await first.message(initial.tabId, 'reviewer:unusable', { generation: role.generation,
      reason: 'Page 126 temporarily unavailable' });
    first.advance();
    await first.api.tick();
    role = await first.message(initial.tabId, 'reviewer:role');
    if (attempt < 2) {
      assert.deepEqual(first.local.data[`sweep:${initial.tabId}`].visited, visited);
      assert.equal(first.tabs.get(initial.tabId).url, `${home}?q=Chair&queryNups=Chair&page=126`);
    }
  }
  assert.equal(role.auxiliary.targetUrl, `${home}?q=Pet&queryNups=Pet`);
  await first.message(initial.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 2 });
  const partial = (await first.api.status()).lanes[0];
  assert.equal(partial.status, 'partial');
  assert.equal(partial.completedTargets, 1);
  assert.equal(partial.attemptedTargets, 2);
  assert.equal(partial.unresolvedTargets, 1);
  assert.equal(partial.pages, 127, 'Durable acknowledged pages on failed work remain represented');

  const restored = fixture({ settings, local: first.local });
  restored.advance(86_400_001);
  await restored.api.tick();
  const resumed = (await restored.api.status()).lanes[0];
  role = await restored.message(resumed.tabId, 'reviewer:role');
  assert.equal(role.auxiliary.targetKey, 'keyword:chair');
  assert.equal(restored.tabs.get(resumed.tabId).url, `${home}?q=Chair&queryNups=Chair&page=126`);
  assert.deepEqual(restored.local.data[`sweep:${resumed.tabId}`].visited, visited);
  assert.equal(resumed.unresolvedTargets, 1);
  assert.equal(resumed.completedTargets, 0, 'Success counts belong to the new round');
  assert.equal(resumed.attemptedTargets, 1);
  assert.equal((await restored.message(resumed.tabId, 'reviewer:sweep-complete', {
    generation: oldGeneration, pages: 150 })).ok, false);
  await restored.message(resumed.tabId, 'reviewer:sweep-complete', {
    generation: role.generation, pages: 150 });
  let current = (await restored.api.status()).lanes[0];
  assert.equal(current.completedTargets, 1);
  assert.equal(current.unresolvedTargets, 0);
  assert.equal(current.pages, 150, 'Valid coverage beyond page 100 is not truncated');
  restored.advance(); await restored.api.tick();
  role = await restored.message(resumed.tabId, 'reviewer:role');
  await restored.message(resumed.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 2 });
  current = (await restored.api.status()).lanes[0];
  assert.equal(current.status, 'complete');
  assert.equal(current.completedTargets, 2);
  assert.equal(current.attemptedTargets, 2);
  assert.equal(current.unresolvedTargets, 0);
});

test('limited coverage keeps its checkpoint and retries even when scheduled refresh is disabled', async () => {
  const f = fixture({ settings: { refreshMinutes: 0, auxiliaryKeywords: true,
    auxiliaryKeywordQueries: ['Chair'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  let role = await f.message(lane.tabId, 'reviewer:role');
  f.tabs.get(lane.tabId).url = `${home}?q=Chair&queryNups=Chair&page=101`;
  const visited = Array.from({ length: 100 }, (_, i) => i + 1);
  await f.local.set({ [`sweep:${lane.tabId}`]: { generation: role.generation,
    targetKey: role.auxiliary.targetKey, mode: 'none', visited, total: null } });
  await f.message(lane.tabId, 'reviewer:sweep-complete', {
    generation: role.generation, pages: 100, coverageLimited: true });
  const partial = (await f.api.status()).lanes[0];
  assert.equal(partial.completedTargets, 0);
  assert.equal(partial.attemptedTargets, 1);
  assert.equal(partial.unresolvedTargets, 1);
  assert.equal(partial.coverageLimitedTargets, 1);
  assert.ok(partial.nextAt > f.now());
  f.advance(partial.nextAt - f.now());
  await f.api.tick();
  role = await f.message(lane.tabId, 'reviewer:role');
  assert.equal(role.active, true);
  assert.deepEqual(f.local.data[`sweep:${lane.tabId}`].visited, visited);
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 101 });
  const complete = (await f.api.status()).lanes[0];
  assert.equal(complete.status, 'complete');
  assert.equal(complete.completedTargets, 1);
  assert.equal(complete.unresolvedTargets, 0);
  assert.equal(complete.coverageLimitedTargets, 0);
  assert.equal(complete.pages, 101);
  assert.equal(complete.nextAt, 0);
});

test('department discovery and duplicate destinations are not successful covered departments', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  let role = await f.message(lane.tabId, 'reviewer:role');
  await f.message(lane.tabId, 'reviewer:aux-discovered', { generation: role.generation,
    candidates: ['Home', 'Household'].map(label => ({ kind: 'department', label,
      parentUrl: home, url: `${home}?catId=4044` })) });
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 0 });
  assert.equal((await f.api.status()).lanes[0].completedTargets, 0);
  f.advance(); await f.api.tick();
  role = await f.message(lane.tabId, 'reviewer:role');
  f.tabs.get(lane.tabId).url = `${home}?catId=4044`;
  await f.message(lane.tabId, 'reviewer:aux-selected', { generation: role.generation,
    url: `${home}?catId=4044` });
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 3 });
  f.advance(); await f.api.tick();
  role = await f.message(lane.tabId, 'reviewer:role');
  f.tabs.get(lane.tabId).url = `${home}?catId=4044`;
  await f.message(lane.tabId, 'reviewer:aux-selected', { generation: role.generation,
    url: `${home}?catId=4044` });
  const complete = (await f.api.status()).lanes[0];
  assert.equal(complete.status, 'complete');
  assert.equal(complete.completedTargets, 1);
  assert.equal(complete.attemptedTargets, 1);
  assert.equal(complete.totalTargets, 1);
});

test('an explicit restart retains unfinished receipts while starting a new round', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  const role = await f.message(lane.tabId, 'reviewer:role');
  f.tabs.get(lane.tabId).url = `${home}?q=Pet&queryNups=Pet&page=8`;
  await f.local.set({ [`sweep:${lane.tabId}`]: { generation: role.generation,
    targetKey: role.auxiliary.targetKey, mode: 'none', visited: [1, 2, 3, 4, 5, 6, 7], total: 20 } });
  await f.api.restart();
  f.advance(); await f.api.tick();
  const current = (await f.api.status()).lanes[0];
  assert.equal(current.round, lane.round + 1);
  assert.equal(current.unresolvedTargets, 1);
  assert.deepEqual(f.local.data[`sweep:${lane.tabId}`].visited, [1, 2, 3, 4, 5, 6, 7]);
  assert.equal(f.tabs.get(lane.tabId).url, `${home}?q=Pet&queryNups=Pet&page=8`);
});

for (const interruption of ['verification pause', 'browser restart']) {
  test(`carried checkpoint survives ${interruption} before its paced target starts`, async () => {
    const settings = { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] };
    const first = fixture({ settings });
    await first.api.tick();
    const lane = (await first.api.status()).lanes[0];
    const role = await first.message(lane.tabId, 'reviewer:role');
    const visited = [1, 2, 3, 4, 5, 6, 7];
    first.tabs.get(lane.tabId).url = `${home}?q=Pet&queryNups=Pet&page=8`;
    await first.local.set({ [`sweep:${lane.tabId}`]: { generation: role.generation,
      targetKey: role.auxiliary.targetKey, mode: 'none', visited, total: 20 } });
    await first.api.restart();
    assert.equal((await first.api.status()).lanes[0].status, 'waiting');
    assert.equal(first.local.data[`sweep:${lane.tabId}`], undefined);
    assert.deepEqual(first.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].checkpoint.visited, visited);
    let restored = first;
    if (interruption === 'verification pause') {
      first.setPaused(200_000);
      await first.api.pause();
      assert.deepEqual(first.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].checkpoint.visited, visited);
      first.setPaused(0);
    } else {
      restored = fixture({ settings, local: first.local });
      await restored.api.tick();
      assert.deepEqual(restored.local.data.reviewerAuxiliaryScans.lanes.keywords.targets[0].checkpoint.visited, visited);
    }
    restored.advance(); await restored.api.tick();
    const current = (await restored.api.status()).lanes[0];
    const currentRole = await restored.message(current.tabId, 'reviewer:role');
    assert.equal(currentRole.active, true);
    assert.deepEqual(restored.local.data[`sweep:${current.tabId}`].visited, visited);
    assert.equal(restored.tabs.get(current.tabId).url, `${home}?q=Pet&queryNups=Pet&page=8`);
  });
}

test('a lost tab retains locally committed page receipts even before its checkpoint message arrives', async () => {
  const settings = { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] };
  const first = fixture({ settings });
  await first.api.tick();
  const lane = (await first.api.status()).lanes[0];
  const role = await first.message(lane.tabId, 'reviewer:role');
  await first.local.set({ [`sweep:${lane.tabId}`]: { generation: role.generation,
    targetKey: role.auxiliary.targetKey, mode: 'none', visited: [1, 2, 3], total: 10 } });
  const restored = fixture({ settings, local: first.local });
  restored.advance();
  await restored.api.tick();
  const current = (await restored.api.status()).lanes[0];
  assert.deepEqual(restored.local.data[`sweep:${current.tabId}`].visited, [1, 2, 3]);
  assert.equal(restored.local.data[`sweep:${current.tabId}`].generation, current.generation);
});

test('legacy advanced targets with missing bounded failure history remain unresolved', async () => {
  const settings = { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] };
  const f = fixture({ settings });
  await f.api.tick();
  const stored = f.local.data.reviewerAuxiliaryScans.lanes.keywords;
  delete stored.targets[0].coverageStatus;
  delete stored.targets[0].attemptedAt;
  stored.cursor = 1;
  stored.failures = 51;
  stored.failedTargets = [];
  stored.status = 'partial';
  stored.completedAt = f.now();
  const status = (await f.api.status()).lanes[0];
  assert.equal(status.completedTargets, 0);
  assert.equal(status.attemptedTargets, 1);
  assert.equal(status.unresolvedTargets, 1);
});

test('an apparently clean legacy complete round is reverified before it counts as success', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Pet'] } });
  await f.api.tick();
  const stored = f.local.data.reviewerAuxiliaryScans.lanes.keywords;
  delete stored.targets[0].coverageStatus;
  stored.cursor = 1;
  stored.failures = 0;
  stored.status = 'complete';
  stored.completedAt = f.now();
  const status = (await f.api.status()).lanes[0];
  assert.equal(status.status, 'partial');
  assert.equal(status.completedTargets, 0);
  assert.equal(status.unresolvedTargets, 1);
  assert.match(status.lastError, /terminal coverage verification/i);
});

test('checkpoint recovery resumes the first missing acknowledged page and retains the exact scope', async () => {
  const f = fixture({ settings: { auxiliaryKeywords: true, auxiliaryKeywordQueries: ['Large Appliance&'] } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  let role = await f.message(lane.tabId, 'reviewer:role');
  f.tabs.get(lane.tabId).url = `${role.auxiliary.targetUrl}&page=6`;
  await f.local.set({ [`sweep:${lane.tabId}`]: { generation: role.generation,
    targetKey: role.auxiliary.targetKey, mode: 'none', visited: [1, 2, 4, 5], total: 6 } });
  await f.message(lane.tabId, 'reviewer:unusable', { generation: role.generation, reason: 'Missing page three' });
  f.advance(); await f.api.tick();
  role = await f.message(lane.tabId, 'reviewer:role');
  const resumed = new URL(f.tabs.get(lane.tabId).url);
  assert.equal(resumed.searchParams.get('page'), '3');
  assert.equal(resumed.searchParams.get('q'), 'Large Appliance&');
  assert.equal(resumed.searchParams.get('queryNups'), 'Large Appliance&');
  assert.deepEqual(f.local.data[`sweep:${lane.tabId}`].visited, [1, 2, 4, 5]);
  assert.equal(f.local.data[`sweep:${lane.tabId}`].generation, role.generation);
});

test('an alias of an unresolved department does not falsely satisfy its coverage', async () => {
  const f = fixture({ settings: { auxiliaryDepartments: true } });
  await f.api.tick();
  const lane = (await f.api.status()).lanes[0];
  let role = await f.message(lane.tabId, 'reviewer:role');
  await f.message(lane.tabId, 'reviewer:aux-discovered', { generation: role.generation,
    candidates: ['Home', 'Household'].map(label => ({ kind: 'department', label,
      parentUrl: home, url: `${home}?catId=4044` })) });
  await f.message(lane.tabId, 'reviewer:sweep-complete', { generation: role.generation, pages: 0 });
  f.advance(); await f.api.tick();
  role = await f.message(lane.tabId, 'reviewer:role');
  f.tabs.get(lane.tabId).url = `${home}?catId=4044`;
  await f.message(lane.tabId, 'reviewer:aux-selected', { generation: role.generation,
    url: `${home}?catId=4044` });
  await f.local.set({ [`sweep:${lane.tabId}`]: { generation: role.generation,
    targetKey: role.auxiliary.targetKey, mode: 'none', visited: [1, 2], total: null } });
  await f.message(lane.tabId, 'reviewer:sweep-complete', {
    generation: role.generation, pages: 2, coverageLimited: true });
  f.advance(); await f.api.tick();
  role = await f.message(lane.tabId, 'reviewer:role');
  f.tabs.get(lane.tabId).url = `${home}?catId=4044`;
  const duplicate = await f.message(lane.tabId, 'reviewer:aux-selected', {
    generation: role.generation, url: `${home}?catId=4044` });
  assert.equal(duplicate.duplicate, true);
  const status = (await f.api.status()).lanes[0];
  assert.equal(status.status, 'partial');
  assert.equal(status.totalTargets, 1);
  assert.equal(status.completedTargets, 0);
  assert.equal(status.unresolvedTargets, 1);
  assert.deepEqual(f.local.data.reviewerAuxiliaryScans.lanes.departments.targets[1].checkpoint.visited, [1, 2]);
});
