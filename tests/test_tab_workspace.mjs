import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const home = 'https://www.walmart.com/reviews/claim-product';
const file = new URL('../extension/tab-workspace.js', import.meta.url);

function storage() {
  const data = {};
  return { data, async get(keys) {
    return Object.fromEntries(keys.filter(key => Object.hasOwn(data, key)).map(key => [key, structuredClone(data[key])]));
  }, async set(value) { Object.assign(data, structuredClone(value)); } };
}

function fixture({ groupScannerTabs = true, rotateScannerTabs = true } = {}) {
  let now = 100_000;
  let paused = false;
  let settings = { groupScannerTabs, rotateScannerTabs };
  let nextGroup = 10;
  let beforeUpdate = async () => {};
  let beforeGet = async () => {};
  let beforeAuthorize = async () => {};
  const actions = [];
  const tabs = new Map([
    [1, { id: 1, windowId: 7, url: home, active: true, groupId: -1 }],
    [2, { id: 2, windowId: 7, url: `${home}?q=Pet`, active: false, groupId: -1 }],
    [3, { id: 3, windowId: 8, url: `${home}?catId=4044`, active: true, groupId: -1 }],
    [9, { id: 9, windowId: 7, url: 'https://example.com/', active: false, groupId: -1 }]
  ]);
  const windows = new Map([[7, { id: 7, focused: true, state: 'normal' }],
    [8, { id: 8, focused: false, state: 'normal' }]]);
  const groups = new Map();
  const generations = new Map([[1, 1], [2, 1], [3, 1]]);
  const session = storage();
  const chrome = { storage: { session }, tabs: {
    async get(id) {
      await beforeGet(id);
      if (!tabs.has(id)) throw new Error('No tab');
      return { ...tabs.get(id) };
    }, async query(query) {
      return [...tabs.values()].filter(tab => Object.entries(query).every(([key, value]) => tab[key] === value))
        .map(tab => ({ ...tab }));
    }, async update(id, options) {
      await beforeUpdate(id, options);
      actions.push({ type: 'tab-update', id, options: structuredClone(options) });
      const tab = tabs.get(id);
      if (options.active) for (const candidate of tabs.values()) {
        if (candidate.windowId === tab.windowId) candidate.active = candidate.id === id;
      }
      return { ...tab };
    }, async group(options) {
      actions.push({ type: 'group', options: structuredClone(options) });
      const id = options.groupId ?? nextGroup++;
      if (!groups.has(id)) groups.set(id, { id, windowId: options.createProperties.windowId, title: '', color: 'grey' });
      for (const tabId of options.tabIds) {
        assert.equal(tabs.get(tabId).windowId, groups.get(id).windowId, 'Grouping must never move a tab across windows');
        tabs.get(tabId).groupId = id;
      }
      // Chrome removes a group as soon as its last tab leaves it.
      for (const oldId of groups.keys()) {
        if (![...tabs.values()].some(tab => tab.groupId === oldId)) groups.delete(oldId);
      }
      return id;
    }
  }, windows: {
    async get(id) { return { ...windows.get(id) }; },
    async update() { assert.fail('The workspace must never focus or change a window'); }
  }, tabGroups: {
    async get(id) { if (!groups.has(id)) throw new Error('No group'); return { ...groups.get(id) }; },
    async query(query) { return [...groups.values()].filter(group => group.windowId === query.windowId); },
    async update(id, value) { actions.push({ type: 'group-update', id, value }); Object.assign(groups.get(id), value); }
  } };
  class Clock extends Date { static now() { return now; } }
  const context = vm.createContext({ URL, Date: Clock });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
  const factory = vm.runInContext('ReviewerTabWorkspace', context);
  const dependencies = { config: async () => settings, paused: async () => paused,
    authorize: async (id, generation) => {
      await beforeAuthorize(id, generation);
      return generations.get(id) === generation;
    } };
  const api = factory.create(chrome, dependencies);
  return { api, chrome, actions, tabs, windows, groups, session, generations,
    recreate() { return factory.create(chrome, dependencies); },
    configure(value) { settings = { ...settings, ...value }; },
    advance(ms = 3001) { now += ms; }, setPaused(value) { paused = value; },
    beforeUpdate(callback) { beforeUpdate = callback; },
    beforeGet(callback) { beforeGet = callback; },
    beforeAuthorize(callback) { beforeAuthorize = callback; },
    activate(id) { for (const tab of tabs.values()) if (tab.windowId === tabs.get(id).windowId) tab.active = tab.id === id; } };
}

test('scanner tabs form one named blue group per existing window without touching unrelated tabs', async () => {
  const f = fixture();
  const result = await f.api.sync([1, 2, 3, 9, 12345]);
  assert.equal(result.ok, true);
  assert.equal(f.groups.size, 2);
  assert.equal(f.tabs.get(1).groupId, f.tabs.get(2).groupId);
  assert.notEqual(f.tabs.get(1).groupId, f.tabs.get(3).groupId);
  assert.equal(f.tabs.get(9).groupId, -1);
  for (const group of f.groups.values()) {
    assert.equal(group.title, 'Reviewer scans');
    assert.equal(group.color, 'blue');
  }
  assert.equal(f.actions.some(action => action.type === 'tab-update'), false);
});

test('native input authority is read-only, current, foreground and cancelled by user activity', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  assert.equal(await f.api.allowsInput(1, 1), false, 'a rotation lease is required');
  assert.equal((await f.api.request(1, 1, null, 10_000)).granted, true);
  const actions = f.actions.length;
  assert.equal(await f.api.allowsInput(1, 1), true);
  assert.equal(await f.api.allowsInput(1, 0), false);
  assert.equal(await f.api.allowsInput(2, 1), false);
  f.windows.get(7).focused = false;
  assert.equal(await f.api.allowsInput(1, 1), false);
  f.windows.get(7).focused = true;
  assert.equal(f.actions.length, actions, 'authority checks never select tabs');
  await f.api.noteActivity(1);
  assert.equal(await f.api.allowsInput(1, 1), false);
});

test('native input with rotation disabled still yields to real activity and never focuses tabs', async () => {
  const f = fixture({ rotateScannerTabs: false });
  await f.api.sync([1, 2]);
  const actions = f.actions.length;
  assert.equal(await f.api.allowsInput(1, 1), true);
  assert.equal(await f.api.allowsInput(2, 1), false);
  f.beforeAuthorize(() => f.api.revoke());
  assert.equal(await f.api.allowsInput(1, 1), false);
  f.beforeAuthorize(async () => {});
  await f.api.noteActivity(1);
  assert.equal(await f.api.allowsInput(1, 1), false);
  assert.equal(f.actions.length, actions);
});

test('session-owned groups are reused after service-worker recreation but reused group IDs are rejected', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  const original = f.tabs.get(1).groupId;
  await f.recreate().sync([1, 2]);
  assert.equal(f.groups.size, 1);
  // The saved ID now refers to a manual group containing an unrelated tab.
  f.tabs.get(1).groupId = -1;
  f.tabs.get(2).groupId = -1;
  f.tabs.get(9).groupId = original;
  Object.assign(f.groups.get(original), { title: 'Personal', color: 'red' });
  await f.recreate().sync([1, 2]);
  assert.notEqual(f.tabs.get(1).groupId, original);
  assert.equal(f.tabs.get(9).groupId, original);
  assert.equal(f.groups.get(original).title, 'Personal');
});

test('an extension reload reuses the group anchored by a live scanner and separates retired workers', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  const original = f.tabs.get(1).groupId;
  for (const key of Object.keys(f.session.data)) delete f.session.data[key];
  f.tabs.set(4, { id: 4, windowId: 7, url: `${home}?q=Chair`, active: false, groupId: -1 });
  const restarted = f.recreate();
  await restarted.sync([1, 4]);
  assert.equal(f.tabs.get(1).groupId, original, 'The reload must not create a second active group');
  assert.equal(f.tabs.get(4).groupId, original);
  const retired = f.groups.get(f.tabs.get(2).groupId);
  assert.equal(retired.title, 'Old reviewer tabs — safe to close');
  assert.equal(retired.color, 'grey');
  assert.equal(retired.collapsed, true);
  assert.notEqual(retired.id, original);
  assert.equal(f.actions.some(action => action.type === 'tab-update'), false);
  assert.equal((await restarted.request(2, 1)).granted, false,
    'Recovering organization cannot authorize an older scanner');
});

test('workers registering before the home scanner cannot strand its previous group', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  const original = f.tabs.get(1).groupId;
  for (const key of Object.keys(f.session.data)) delete f.session.data[key];
  f.tabs.set(4, { id: 4, windowId: 7, url: `${home}?q=Chair`, active: false, groupId: -1 });
  const restarted = f.recreate();
  // A new worker can ask for its role before primary election finishes.
  await restarted.sync([4]);
  await restarted.sync([1, 4]);
  assert.equal(f.tabs.get(1).groupId, original, 'Prefer the first live scanner anchor over the provisional worker group');
  assert.equal(f.tabs.get(4).groupId, original);
  assert.equal(f.groups.get(f.tabs.get(2).groupId).title, 'Old reviewer tabs — safe to close');
  assert.equal([...f.groups.values()].filter(g => g.title === 'Reviewer scans').length, 1);
  assert.equal(f.groups.size, 2, 'The empty provisional group dissolves and only the current/archive pair remains');
  assert.equal((await restarted.request(2, 1)).granted, false);
});

test('replacing workers keeps exactly one current group and reuses one archive group', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  const currentId = f.tabs.get(1).groupId;
  for (const id of [4, 5]) f.tabs.set(id,
    { id, windowId: 7, url: `${home}?q=Query${id}`, active: false, groupId: -1 });
  await f.api.sync([1, 4]);
  const oldId = f.tabs.get(2).groupId;
  assert.notEqual(oldId, currentId);
  await f.api.sync([1, 5]);
  await f.api.sync([1, 5]);
  assert.equal(f.tabs.get(1).groupId, currentId);
  assert.equal(f.tabs.get(5).groupId, currentId);
  assert.equal(f.tabs.get(4).groupId, oldId);
  assert.equal(f.groups.size, 2);
  assert.deepEqual([...f.tabs.values()].filter(t => t.groupId === currentId).map(t => t.id), [1, 5]);
});

test('recovery never adopts a shared group or a same-named group containing unrelated pages', async () => {
  for (const scenario of ['shared', 'unrelated']) {
    const f = fixture();
    const groupId = 20;
    f.groups.set(groupId, { id: groupId, windowId: 7, title: 'Reviewer scans', color: 'blue',
      shared: scenario === 'shared' });
    f.tabs.get(1).groupId = groupId;
    if (scenario === 'unrelated') f.tabs.get(9).groupId = groupId;
    await f.api.sync([1, 2]);
    assert.equal(f.groups.get(groupId).title, 'Reviewer scans', scenario);
    assert.equal(f.tabs.get(9).groupId, scenario === 'unrelated' ? groupId : -1, scenario);
    if (scenario === 'unrelated') assert.notEqual(f.tabs.get(1).groupId, groupId);
    else assert.equal(f.tabs.get(1).groupId, groupId);
  }
});

test('archiving an older selected reviewer preserves its tab and keeps its group visible', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  f.activate(2);
  f.tabs.set(4, { id: 4, windowId: 7, url: `${home}?q=Chair`, active: false, groupId: -1 });
  await f.api.sync([1, 4]);
  assert.equal(f.tabs.get(2).active, true);
  assert.equal(f.groups.get(f.tabs.get(2).groupId).title, 'Old reviewer tabs — safe to close');
  assert.equal(f.groups.get(f.tabs.get(2).groupId).collapsed, false);
  assert.equal(f.actions.some(action => action.type === 'tab-update'), false);
});

test('an archive containing an unrelated page or shared tabs is never reused', async () => {
  for (const scenario of ['shared', 'unrelated']) {
    const f = fixture();
    f.groups.set(30, { id: 30, windowId: 7, title: 'Old reviewer tabs — safe to close',
      color: 'grey', shared: scenario === 'shared' });
    f.tabs.get(9).groupId = 30;
    if (scenario === 'shared') f.tabs.get(9).url = home;
    await f.api.sync([1, 2]);
    f.tabs.set(4, { id: 4, windowId: 7, url: `${home}?q=Chair`, active: false, groupId: -1 });
    await f.api.sync([1, 4]);
    assert.equal(f.tabs.get(9).groupId, 30, scenario);
    assert.notEqual(f.tabs.get(2).groupId, 30, scenario);
    assert.equal(f.groups.get(30).title, 'Old reviewer tabs — safe to close');
  }
});

test('disabled grouping still registers owned scanner tabs and missing grouping APIs are harmless', async () => {
  const f = fixture({ groupScannerTabs: false });
  await f.api.sync([1, 2]);
  assert.equal(f.groups.size, 0);
  assert.equal((await f.api.request(2, 1)).granted, true);
  const g = fixture();
  delete g.chrome.tabGroups;
  delete g.chrome.tabs.group;
  assert.equal((await g.api.sync([1, 2])).ok, true);
  assert.equal((await g.api.request(2, 1)).granted, true);
});

test('a valid request selects only its scanner tab without focusing or changing any window', async () => {
  const f = fixture();
  await f.api.sync([1, 2, 3]);
  const granted = await f.api.request(2, 1);
  assert.equal(granted.ok, true);
  assert.equal(granted.granted, true);
  assert.equal(granted.enabled, true);
  assert.deepEqual(f.actions.filter(action => action.type === 'tab-update'),
    [{ type: 'tab-update', id: 2, options: { active: true } }]);
  assert.equal(f.tabs.get(9).active, false);
});

test('manual unrelated selected tabs, minimized windows and unfocused windows never lose their selection', async () => {
  for (const scenario of ['manual', 'minimized', 'unfocused']) {
    const f = fixture();
    await f.api.sync([1, 2]);
    if (scenario === 'manual') f.activate(9);
    if (scenario === 'minimized') f.windows.get(7).state = 'minimized';
    if (scenario === 'unfocused') f.windows.get(7).focused = false;
    const answer = await f.api.request(2, 1);
    assert.equal(answer.granted, false, scenario);
    assert.equal(answer.retryAfterMs, 5000, scenario);
    assert.equal(f.actions.some(action => action.type === 'tab-update'), false, scenario);
  }
});

test('unknown IDs, stale generations, challenge routes and pending unrelated navigation are rejected', async () => {
  for (const scenario of ['unknown', 'stale', 'challenge', 'pending', 'reused-tab']) {
    const f = fixture();
    await f.api.sync([1, 2]);
    if (scenario === 'challenge') f.tabs.get(2).url = 'https://www.walmart.com/blocked';
    if (scenario === 'pending') f.tabs.get(2).pendingUrl = 'https://example.com/';
    if (scenario === 'reused-tab') f.tabs.set(2, { id: 2, windowId: 8, url: home, active: true });
    const answer = await f.api.request(scenario === 'unknown' ? 3 : 2, scenario === 'stale' ? 0 : 1);
    assert.equal(answer.granted, false, scenario);
    assert.equal(f.actions.some(action => action.type === 'tab-update'), false, scenario);
  }
});

test('disabled rotation and unavailable focus APIs return a background fallback', async () => {
  const f = fixture({ rotateScannerTabs: false });
  await f.api.sync([1, 2]);
  assert.equal((await f.api.request(2, 1)).enabled, false);
  const g = fixture();
  await g.api.sync([1, 2]);
  delete g.chrome.tabs.query;
  assert.equal((await g.api.request(2, 1)).enabled, false);
});

test('focus slots expire without timers, bound same-owner renewal and fairly rotate contending scanners', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  assert.equal((await f.api.request(1, 1)).granted, true);
  assert.equal((await f.api.request(2, 1)).granted, false);
  f.advance(2000);
  assert.equal((await f.api.request(1, 1)).granted, true);
  f.advance(1100);
  assert.equal((await f.api.request(1, 1)).granted, false, 'A waiting scanner owns the next slot');
  assert.equal((await f.api.request(2, 1)).granted, true);
});

test('shared pauses and explicit revocations invalidate in-flight and existing focus grants', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  f.beforeUpdate(async () => { f.api.revoke(); });
  assert.equal((await f.api.request(2, 1)).granted, false);
  f.beforeUpdate(async () => {});
  f.api.revoke();
  f.setPaused(true);
  assert.equal((await f.api.request(1, 1)).granted, false);
  f.setPaused(false);
  assert.equal((await f.api.request(1, 1)).granted, true);
  f.api.pause();
  assert.equal((await f.api.request(2, 1)).granted, true);
});

test('API errors stay contained and a rejected activation does not poison later requests', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  f.beforeUpdate(async () => { throw new Error('Tab vanished'); });
  const failed = await f.api.request(2, 1);
  assert.equal(failed.granted, false);
  f.beforeUpdate(async () => {});
  f.advance(6000);
  assert.equal((await f.api.request(2, 1)).granted, true);
});

test('trusted scanner activity suspends all rotations for thirty seconds across worker recreation', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  assert.equal((await f.api.noteActivity(1)).ok, true);
  assert.equal((await f.api.request(2, 1)).granted, false);
  const restored = f.recreate();
  await restored.sync([1, 2]);
  assert.equal((await restored.request(2, 1)).granted, false);
  f.advance(29_999);
  assert.equal((await restored.request(2, 1)).granted, false);
  f.advance(2);
  assert.equal((await restored.request(2, 1)).granted, true);
});

test('a stale authorization or pause arriving during activation never produces a granted response', async () => {
  for (const scenario of ['generation', 'paused', 'disabled']) {
    const f = fixture();
    await f.api.sync([1, 2]);
    f.beforeUpdate(async () => {
      if (scenario === 'generation') f.generations.set(2, 2);
      if (scenario === 'paused') f.setPaused(true);
      if (scenario === 'disabled') f.configure({ rotateScannerTabs: false });
    });
    assert.equal((await f.api.request(2, 1)).granted, false, scenario);
  }
});

test('contenders receive an immediate retry while an activation is still in flight', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  let release;
  let started;
  const reachedUpdate = new Promise(resolve => { started = resolve; });
  f.beforeUpdate(async () => {
    started();
    await new Promise(resolve => { release = resolve; });
  });
  const first = f.api.request(2, 1);
  await reachedUpdate;
  const waiting = await f.api.request(1, 1);
  assert.equal(waiting.granted, false);
  assert.equal(waiting.retryAfterMs, 5000);
  release();
  assert.equal((await first).granted, true);
});

test('expected activation tracking identifies the scheduler transition and expires after revocation', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  f.beforeUpdate(async id => { assert.equal(id, 2); });
  await f.api.request(2, 1);
  assert.equal(f.api.isExpectedActivation(2), true);
  assert.equal(f.api.isExpectedActivation(2), false, 'A manual return to the same tab is a new activation');
  assert.equal(f.api.isExpectedActivation(1), false);
  f.api.revoke();
  assert.equal(f.api.isExpectedActivation(2), false);
});

test('owned challenge tabs remain grouped but can never acquire a scrolling activation slot', async () => {
  const f = fixture();
  f.tabs.get(2).url = 'https://www.walmart.com/blocked';
  await f.api.sync([1, 2]);
  assert.equal(f.tabs.get(1).groupId, f.tabs.get(2).groupId);
  assert.ok(f.tabs.get(2).groupId >= 0);
  assert.equal((await f.api.request(2, 1)).granted, false);
  assert.equal(f.actions.some(action => action.type === 'tab-update'), false);
});

test('an existing owner cannot claim a still-valid slot after another scanner was manually selected', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  assert.equal((await f.api.request(1, 1)).granted, true);
  f.activate(2);
  assert.equal((await f.api.request(1, 1)).granted, false);
  assert.equal(f.tabs.get(2).active, true);
});

test('grouping organizes owned challenge tabs during a verification pause without activating them', async () => {
  const f = fixture();
  f.setPaused(true);
  f.tabs.get(2).url = 'https://www.walmart.com/blocked';
  await f.api.sync([1, 2]);
  assert.ok(f.tabs.get(1).groupId >= 0);
  assert.equal(f.tabs.get(1).groupId, f.tabs.get(2).groupId);
  assert.equal((await f.api.request(1, 1)).granted, false);
  assert.equal(f.actions.some(action => action.type === 'tab-update'), false);
});

test('the optional trailing slash reviewer route can be grouped and activated', async () => {
  const f = fixture();
  f.tabs.get(2).url = `${home}/?q=Pet`;
  await f.api.sync([1, 2]);
  assert.equal(f.tabs.get(1).groupId, f.tabs.get(2).groupId);
  assert.equal((await f.api.request(2, 1)).granted, true);
});

test('scanner tabs in shared groups remain untouched, including a cached group that becomes shared', async () => {
  for (const cached of [false, true]) {
    const f = fixture();
    if (cached) await f.api.sync([1, 2]);
    const id = cached ? f.tabs.get(1).groupId : 20;
    if (!cached) {
      f.groups.set(id, { id, windowId: 7, title: 'Shared work', color: 'red' });
      f.tabs.get(1).groupId = id;
      f.tabs.get(2).groupId = id;
    }
    f.groups.get(id).shared = true;
    const count = f.actions.length;
    await f.api.sync([1, 2]);
    assert.equal(f.actions.length, count, 'Shared group membership and metadata must not change');
    assert.equal(f.tabs.get(1).groupId, id);
    assert.equal(f.tabs.get(2).groupId, id);
    assert.equal(f.groups.size, 1);
  }
});

test('expired or invalid preparation deadlines never activate a tab', async () => {
  for (const deadline of [100_000, 99_999, NaN, Infinity, '101000']) {
    const f = fixture();
    await f.api.sync([1, 2]);
    assert.equal((await f.api.request(2, 1, deadline)).granted, false, String(deadline));
    assert.equal(f.actions.some(action => action.type === 'tab-update'), false);
  }
});

test('a preparation deadline that expires during authorization or context reads prevents activation', async () => {
  for (const scenario of ['authorize', 'context']) {
    const f = fixture();
    await f.api.sync([1, 2]);
    if (scenario === 'authorize') f.beforeAuthorize(async () => { f.advance(1001); });
    else f.beforeGet(async () => { f.advance(1001); });
    assert.equal((await f.api.request(2, 1, 101_000)).granted, false, scenario);
    assert.equal(f.actions.some(action => action.type === 'tab-update'), false, scenario);
  }
});

test('page reading can hold a ten-second focus slot while the default remains three seconds', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  const reading = await f.api.request(1, 1, 101_000, 10_000);
  assert.equal(reading.granted, true);
  assert.equal(reading.until, 110_000);
  f.advance(4000);
  assert.equal((await f.api.request(2, 1)).granted, false);
  f.advance(6001);
  const next = await f.api.request(2, 1);
  assert.equal(next.granted, true);
  assert.equal(next.until, 113_001);
});

test('requested scrolling slots remain bounded and same-owner requests cannot renew an active lease', async () => {
  for (const duration of [10_001, 10**12, Infinity, NaN, '10000', -1]) {
    const f = fixture();
    await f.api.sync([1, 2]);
    const slot = await f.api.request(1, 1, 101_000, duration);
    assert.equal(slot.granted, true, String(duration));
    assert.ok(slot.until >= 103_000 && slot.until <= 110_000, String(duration));
  }
  const f = fixture();
  await f.api.sync([1, 2]);
  const initial = await f.api.request(1, 1);
  f.advance(1000);
  const repeated = await f.api.request(1, 1, 102_000, 10_000);
  assert.equal(repeated.granted, true);
  assert.equal(repeated.until, initial.until);
});

test('one-second request deadlines preserve FIFO turns across three rounds of ten-second reading leases', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  let now = 100_000;
  const advance = ms => { now += ms; f.advance(ms); };
  const request = id => f.api.request(id, 1, now + 1000, 10_000);
  assert.equal((await request(1)).granted, true);
  let owner = 1;
  for (let round = 0; round < 3; round++) {
    for (const contender of [2, 1]) {
      const originalDeadline = now + 1000;
      assert.equal((await request(contender)).reason, 'busy', `round ${round}, contender ${contender}`);
      advance(5000);
      const updates = f.actions.filter(action => action.type === 'tab-update').length;
      const expired = await f.api.request(contender, 1, originalDeadline, 10_000);
      assert.equal(expired.granted, false);
      assert.equal(expired.reason, 'expired');
      assert.equal(f.actions.filter(action => action.type === 'tab-update').length, updates,
        'An expired request cannot activate even when its FIFO ticket survives');
      assert.equal((await request(contender)).reason, 'busy');
      advance(5001);
      const repeatedOwner = await request(owner);
      assert.equal(repeatedOwner.granted, false, `round ${round}: the previous owner must yield`);
      assert.equal(repeatedOwner.reason, 'waiting-turn');
      const next = await request(contender);
      assert.equal(next.granted, true, `round ${round}, contender ${contender} receives its turn`);
      assert.equal(next.until, now + 10_000);
      owner = contender;
    }
  }
});

test('three reading tabs retain FIFO priority through repeated five-second retries', async () => {
  const f = fixture();
  f.tabs.get(3).windowId = 7;
  f.tabs.get(3).active = false;
  await f.api.sync([1, 2, 3]);
  let now = 100_000;
  const request = id => f.api.request(id, 1, now + 1000, 10_000);
  assert.equal((await request(1)).granted, true);
  const turns = [1];
  // Each tab retries on the content scheduler's 5-second cadence. The third
  // ticket must retain its place for two reading slots while fresh requests
  // keep it alive beyond the queue's original 15-second ticket lifetime.
  for (let step = 1; step <= 18; step++) {
    now += 5050;
    f.advance(5050);
    for (const id of [1, 2, 3]) await request(id);
    if (step % 2 === 0) {
      const selected = [...f.tabs.values()].find(tab => tab.windowId === 7 && tab.active).id;
      const expected = turns.at(-1) % 3 + 1;
      assert.equal(selected, expected, `reading turn ${turns.length}`);
      turns.push(selected);
    }
  }
  assert.deepEqual(turns, [1, 2, 3, 1, 2, 3, 1, 2, 3, 1]);
});
