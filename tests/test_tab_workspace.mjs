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

function fixture({ groupScannerTabs = true, rotateScannerTabs = true, requestTimeoutMs, cleanup = false, fakeTimers = false } = {}) {
  let now = 100_000;
  let paused = false;
  let settings = { groupScannerTabs, rotateScannerTabs };
  let nextGroup = 10;
  let beforeUpdate = async () => {};
  let beforeGet = async () => {};
  let beforeAuthorize = async () => {};
  let beforeManaged = async () => false;
  const actions = [];
  const selectionMessages = [];
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
    async sendMessage(id, message) { selectionMessages.push({ id, message: structuredClone(message) }); },
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
    }, async remove(id) {
      actions.push({ type: 'tab-remove', id });
      tabs.delete(id);
      for (const groupId of groups.keys()) {
        if (![...tabs.values()].some(tab => tab.groupId === groupId)) groups.delete(groupId);
      }
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
  let timerSequence = 0;
  const timers = new Map();
  const clockSetTimeout = fakeTimers ? (callback, ms) => {
    const id = ++timerSequence;
    timers.set(id, { callback, at: now + ms });
    return id;
  } : setTimeout;
  const clockClearTimeout = fakeTimers ? id => timers.delete(id) : clearTimeout;
  const context = vm.createContext({ URL, Date: Clock, setTimeout: clockSetTimeout, clearTimeout: clockClearTimeout });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
  const factory = vm.runInContext('ReviewerTabWorkspace', context);
  const dependencies = { config: async () => settings, paused: async () => paused, requestTimeoutMs,
    ...(cleanup ? { isManaged: id => beforeManaged(id), cleanupTimeoutMs: 20 } : {}),
    authorize: async (id, generation) => {
      await beforeAuthorize(id, generation);
      return generations.get(id) === generation;
    } };
  const api = factory.create(chrome, dependencies);
  return { api, chrome, actions, selectionMessages, tabs, windows, groups, session, generations,
    recreate() { return factory.create(chrome, dependencies); },
    configure(value) { settings = { ...settings, ...value }; },
    advance(ms = 3001) { now += ms; }, setPaused(value) { paused = value; },
    now() { return now; },
    async runTimers() {
      for (const [id, timer] of [...timers]) if (timer.at <= now) {
        timers.delete(id);
        timer.callback();
      }
      // Drain the asynchronous guard checks without sleeping real time.
      for (let i = 0; i < 100; i++) await Promise.resolve();
    },
    beforeUpdate(callback) { beforeUpdate = callback; },
    beforeGet(callback) { beforeGet = callback; },
    beforeAuthorize(callback) { beforeAuthorize = callback; },
    beforeManaged(callback) { beforeManaged = callback; },
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

test('reserved scanner waiting pages join their workspace but never acquire a page-reading turn', async () => {
  const f = fixture();
  const waitingUrl = 'chrome-extension://relay/scanner-wait.html';
  f.chrome.runtime = { getURL: name => `chrome-extension://relay/${name}` };
  f.tabs.get(2).url = waitingUrl;
  await f.api.sync([1, 2]);
  assert.equal(f.tabs.get(1).groupId, f.tabs.get(2).groupId);
  const response = await f.api.request(2, 1);
  assert.equal(response.granted, false);
  assert.equal(f.actions.some(action => action.type === 'tab-update'), false);
});

test('facet input authority is read-only, current, selected and cancelled by user activity', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  assert.equal(await f.api.allowsInput(1, 1), false, 'a rotation lease is required');
  assert.equal((await f.api.request(1, 1, null, 10_000)).granted, true);
  const actions = f.actions.length;
  assert.equal(await f.api.allowsInput(1, 1), true);
  assert.equal(await f.api.allowsInput(1, 0), false);
  assert.equal(await f.api.allowsInput(2, 1), false);
  f.windows.get(7).focused = false;
  assert.equal(await f.api.allowsInput(1, 1), true);
  f.windows.get(7).focused = true;
  assert.equal(f.actions.length, actions, 'authority checks never select tabs');
  await f.api.noteActivity(1);
  assert.equal(await f.api.allowsInput(1, 1), false);
});

test('facet input with rotation disabled still yields to real activity and never focuses tabs', async () => {
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

test('retired registered scanners close automatically while live and manual reviewer tabs survive', async () => {
  const f = fixture({ cleanup: true });
  await f.api.sync([1, 2]);
  const groupId = f.tabs.get(1).groupId;
  f.tabs.set(4, { id: 4, windowId: 7, url: `${home}?q=Chair`, active: false, groupId: -1 });
  f.tabs.set(5, { id: 5, windowId: 7, url: home, active: false, groupId });
  f.tabs.set(6, { id: 6, windowId: 7, url: home, active: false, groupId: -1 });
  f.tabs.set(7, { id: 7, windowId: 7, url: 'https://www.walmart.com/ip/123', active: false, groupId: -1 });
  await f.api.sync([1, 4]);
  assert.equal(f.tabs.has(2), false, 'An unchanged retired scanner should no longer accumulate in Chrome');
  for (const id of [1, 4, 5, 6, 7, 9]) assert.equal(f.tabs.has(id), true, `Preserve tab ${id}`);
  assert.deepEqual(f.actions.filter(action => action.type === 'tab-remove'), [{ type: 'tab-remove', id: 2 }]);
});

test('saved explicit ownership survives worker recreation but matching gray groups alone never authorize closure', async () => {
  const f = fixture({ cleanup: true });
  await f.api.sync([1, 2]);
  f.groups.set(30, { id: 30, windowId: 7, title: 'Old reviewer tabs — safe to close', color: 'grey' });
  f.tabs.set(5, { id: 5, windowId: 7, url: home, active: false, groupId: 30 });
  await f.recreate().sync([1]);
  assert.equal(f.tabs.has(2), false);
  assert.equal(f.tabs.has(5), true, 'An archive title is not ownership evidence');
});

test('retired owned scanners close even when their window no longer contains a live scanner', async () => {
  const f = fixture({ cleanup: true });
  f.tabs.get(3).active = false;
  f.tabs.set(8, { id: 8, windowId: 8, url: 'https://example.org/', active: true, groupId: -1 });
  await f.api.sync([1, 2, 3]);
  await f.api.sync([1, 2]);
  assert.equal(f.tabs.has(3), false);
  assert.equal(f.tabs.get(8).active, true);
});

test('an indeterminate ownership answer never authorizes automatic tab removal', async () => {
  const f = fixture({ cleanup: true });
  await f.api.sync([1, 2]);
  f.beforeManaged(async () => undefined);
  await f.api.sync([1]);
  assert.equal(f.tabs.has(2), true);
});

test('retired pinned, selected, challenged, navigating and changed-identity tabs stay open', async () => {
  for (const scenario of ['pinned', 'selected', 'challenge', 'pending', 'url', 'window', 'shared']) {
    const f = fixture({ cleanup: true });
    await f.api.sync([1, 2]);
    if (scenario === 'pinned') f.tabs.get(2).pinned = true;
    if (scenario === 'selected') f.activate(2);
    if (scenario === 'challenge') f.tabs.get(2).url = 'https://www.walmart.com/blocked';
    if (scenario === 'pending') f.tabs.get(2).pendingUrl = `${home}?q=User`;
    if (scenario === 'url') f.tabs.get(2).url = `${home}?q=User`;
    if (scenario === 'window') f.tabs.get(2).windowId = 8;
    if (scenario === 'shared') f.groups.get(f.tabs.get(2).groupId).shared = true;
    await f.api.sync([1]);
    assert.equal(f.tabs.has(2), true, scenario);
  }
});

test('verification pauses defer retired closure until a later successful workspace sync', async () => {
  const f = fixture({ cleanup: true });
  await f.api.sync([1, 2]);
  f.setPaused(true);
  await f.api.sync([1]);
  assert.equal(f.tabs.has(2), true);
  f.setPaused(false);
  await f.api.sync([1]);
  assert.equal(f.tabs.has(2), false, 'A known archived scanner can close once verification is complete');
});

test('fresh scanner ownership and state changes during retirement prevent stale tab removal', async () => {
  for (const scenario of ['managed', 'paused', 'activity', 'navigation', 'pinned', 'shared', 'sync']) {
    const f = fixture({ cleanup: true });
    await f.api.sync([1, 2]);
    let next;
    f.beforeManaged(async id => {
      assert.equal(id, 2);
      if (scenario === 'paused') f.setPaused(true);
      if (scenario === 'activity') await f.api.noteActivity(1);
      if (scenario === 'navigation') f.tabs.get(2).url = `${home}?q=User`;
      if (scenario === 'pinned') f.tabs.get(2).pinned = true;
      if (scenario === 'shared') f.groups.get(f.tabs.get(2).groupId).shared = true;
      if (scenario === 'sync') next = f.api.sync([1, 2]);
      return scenario === 'managed';
    });
    await f.api.sync([1]);
    if (next) await next;
    assert.equal(f.tabs.has(2), true, scenario);
  }
});

test('failed or unavailable tab removal falls back to one archive without repeated attempts in the same sync', async () => {
  for (const scenario of ['missing', 'reject']) {
    const f = fixture({ cleanup: true });
    await f.api.sync([1, 2]);
    let attempts = 0;
    if (scenario === 'missing') delete f.chrome.tabs.remove;
    else f.chrome.tabs.remove = async () => { attempts += 1; throw new Error('Chrome refused'); };
    assert.equal((await f.api.sync([1])).ok, true);
    assert.equal(f.tabs.has(2), true);
    assert.equal(f.groups.get(f.tabs.get(2).groupId).title, 'Old reviewer tabs — safe to close');
    assert.equal(attempts, scenario === 'reject' ? 1 : 0);
  }
});

test('verification or shared-group changes during the final tab read still preserve the retired tab', async () => {
  for (const scenario of ['paused', 'shared']) {
    const f = fixture({ cleanup: true });
    await f.api.sync([1, 2]);
    f.beforeManaged(async () => {
      f.beforeGet(async id => {
        if (id !== 2) return;
        if (scenario === 'paused') f.setPaused(true);
        else f.groups.get(f.tabs.get(2).groupId).shared = true;
      });
      return false;
    });
    await f.api.sync([1]);
    assert.equal(f.tabs.has(2), true, scenario);
  }
});

test('a stalled ownership check times out and its late reply cannot close a tab', async () => {
  const f = fixture({ cleanup: true });
  await f.api.sync([1, 2]);
  let release;
  f.beforeManaged(() => new Promise(resolve => { release = resolve; }));
  const result = await Promise.race([f.api.sync([1]),
    new Promise(resolve => setTimeout(() => resolve({ ok: false }), 150))]);
  assert.equal(result.ok, true);
  assert.equal(f.tabs.has(2), true);
  release(false);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.tabs.has(2), true);
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

test('an unrelated selected tab retains its selection while Chrome is focused', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  f.activate(9);
  const answer = await f.api.request(2, 1);
  assert.equal(answer.granted, false);
  assert.equal(answer.reason, 'manual-tab-active');
  assert.equal(answer.retryAfterMs, 5000);
  assert.equal(f.actions.some(action => action.type === 'tab-update'), false);
});

test('background and minimized Chrome windows select scanner tabs without changing window focus or state', async () => {
  for (const state of ['normal', 'minimized']) {
    const f = fixture();
    await f.api.sync([1, 2]);
    Object.assign(f.windows.get(7), { focused: false, state });
    const before = structuredClone(f.windows.get(7));
    const response = await f.api.request(2, 1);
    assert.equal(response.granted, true, state);
    assert.equal(f.tabs.get(2).active, true, state);
    assert.equal(f.tabs.get(1).active, false, state);
    assert.deepEqual(f.windows.get(7), before, 'Tab selection must not restore or focus the OS window');
    assert.deepEqual(f.actions.filter(action => action.type === 'tab-update'),
      [{ type: 'tab-update', id: 2, options: { active: true } }]);
    assert.equal(await f.api.allowsInput(2, 1), true, state);
  }
});

test('background collection can select its scanner over an unrelated tab without focusing Chrome', async () => {
  for (const state of ['normal', 'minimized']) {
    const f = fixture();
    await f.api.sync([1, 2]);
    f.activate(9);
    Object.assign(f.windows.get(7), { focused: false, state });
    const response = await f.api.request(2, 1);
    assert.equal(response.granted, true, state);
    assert.equal(f.tabs.get(2).active, true, state);
    assert.equal(f.tabs.get(9).active, false, state);
    assert.deepEqual(f.windows.get(7), { id: 7, focused: false, state });
    assert.deepEqual(f.actions.filter(action => action.type === 'tab-update'),
      [{ type: 'tab-update', id: 2, options: { active: true } }]);
  }
});

test('returning Chrome to the foreground during authorization preserves the selected manual tab', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  f.activate(9);
  f.windows.get(7).focused = false;
  let authorizations = 0;
  f.beforeAuthorize(async () => {
    if (++authorizations === 3) f.windows.get(7).focused = true;
  });
  const answer = await f.api.request(2, 1);
  assert.equal(answer.granted, false);
  assert.equal(f.tabs.get(9).active, true);
  assert.equal(f.actions.some(action => action.type === 'tab-update'), false);
});

test('an OS focus event cancels pending selection without revoking an established scanner lease', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  await f.api.request(1, 1);
  let release;
  f.beforeAuthorize(() => new Promise(resolve => { release = resolve; }));
  const pending = f.api.request(2, 1);
  await new Promise(resolve => setImmediate(resolve));
  f.api.cancelPendingSelection();
  f.beforeAuthorize(async () => {});
  release();
  assert.equal((await pending).granted, false);
  assert.equal(await f.api.allowsSelection(1, 1), true);
  assert.equal(f.selectionMessages.length, 0, 'An OS focus change must not revoke a current scanner turn');
  assert.equal(f.actions.some(action => action.type === 'tab-update'), false);
});

test('focus changes after the last window read cancel acquisition before any tab activation', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  f.activate(9);
  f.windows.get(7).focused = false;
  const query = f.chrome.tabs.query;
  let reads = 0;
  f.chrome.tabs.query = async options => {
    if (options.active === true && ++reads === 3) {
      f.windows.get(7).focused = true;
      f.api.cancelPendingSelection();
    }
    return query(options);
  };
  assert.equal((await f.api.request(2, 1)).granted, false);
  assert.equal(f.tabs.get(9).active, true);
  assert.equal(f.actions.some(action => action.type === 'tab-update'), false);
});

test('OS focus changes preserve a granted scanner turn and its expected activation event', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  await f.api.request(2, 1);
  f.api.cancelPendingSelection();
  assert.equal(await f.api.allowsSelection(2, 1), true);
  assert.equal(f.api.isExpectedActivation(2), true);
  assert.equal(f.selectionMessages.length, 0);
});

test('same-clock scanner leases have unique identities so delayed revocations cannot name a replacement', async () => {
  const f = fixture();
  await f.api.sync([1]);
  const first = await f.api.request(1, 1);
  f.api.revoke();
  const second = await f.api.request(1, 1);
  assert.equal(first.until, second.until, 'Reproduce the same-millisecond deadline collision');
  assert.equal(typeof first.leaseId, 'string');
  assert.notEqual(first.leaseId, second.leaseId);
  assert.equal(f.selectionMessages[0].message.leaseId, first.leaseId);
  assert.notEqual(f.selectionMessages[0].message.leaseId, second.leaseId);
  assert.equal((await f.api.request(1, 1)).leaseId, second.leaseId,
    'Reusing an existing turn must preserve its identity');
});

test('a revoked long turn cannot identify a newer short turn by its larger deadline', async () => {
  const f = fixture();
  await f.api.sync([1]);
  const previous = await f.api.request(1, 1, null, 10_000);
  f.advance(1_000);
  f.api.revoke();
  const current = await f.api.request(1, 1);
  assert.ok(previous.until > current.until);
  assert.equal(f.selectionMessages[0].message.leaseId, previous.leaseId);
  assert.notEqual(f.selectionMessages[0].message.leaseId, current.leaseId);
  assert.equal(await f.api.allowsSelection(1, 1), true);
});

test('selection authority is read-only and distinguishes backgrounding from actual tab deselection', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  assert.equal(await f.api.allowsSelection(1, 1), false);
  assert.equal((await f.api.request(2, 1)).granted, true);
  const actions = f.actions.length;
  Object.assign(f.windows.get(7), { focused: false, state: 'minimized' });
  assert.equal(await f.api.allowsSelection(2, 1), true);
  assert.equal(await f.api.allowsSelection(2, 0), false);
  assert.equal(await f.api.allowsSelection(1, 1), false);
  f.activate(1);
  assert.equal(await f.api.allowsSelection(2, 1), false);
  assert.equal(f.actions.length, actions, 'An authority query must never select tabs');
});

test('selection authority rejects expired, revoked, paused and user-interrupted leases', async () => {
  for (const scenario of ['expired', 'revoked', 'paused', 'activity', 'scope']) {
    const f = fixture();
    await f.api.sync([1, 2]);
    await f.api.request(1, 1);
    if (scenario === 'expired') f.advance();
    if (scenario === 'revoked') f.api.revoke();
    if (scenario === 'paused') f.setPaused(true);
    if (scenario === 'activity') await f.api.noteActivity(1);
    if (scenario === 'scope') f.tabs.get(1).url = 'https://example.com/';
    assert.equal(await f.api.allowsSelection(1, 1), false, scenario);
  }
});

test('selection checks retain an unexpired lease while facet input keeps its release margin', async () => {
  const f = fixture();
  await f.api.sync([1]);
  await f.api.request(1, 1);
  f.advance(1501);
  assert.equal(await f.api.allowsInput(1, 1), false);
  assert.equal(await f.api.allowsSelection(1, 1), true);
  f.advance(1499);
  assert.equal(await f.api.allowsSelection(1, 1), false);
  assert.equal(f.selectionMessages.length, 0);
});

test('rotation-disabled selection authority requires the actual selected owned tab without creating a lease', async () => {
  const f = fixture({ rotateScannerTabs: false });
  await f.api.sync([1, 2]);
  f.windows.get(7).focused = false;
  const actions = f.actions.length;
  assert.equal(await f.api.allowsSelection(1, 1), true);
  assert.equal(await f.api.allowsSelection(2, 1), false);
  await f.api.noteActivity(1);
  assert.equal(await f.api.allowsSelection(1, 1), false);
  assert.equal(f.actions.length, actions);
});

test('revocation during a selection-authority check cannot return stale permission', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  await f.api.request(1, 1);
  f.beforeAuthorize(async () => { f.api.revoke(); });
  assert.equal(await f.api.allowsSelection(1, 1), false);
});

test('revoking selection notifies only its old holder with its generation and lease deadline', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  const granted = await f.api.request(1, 1);
  f.api.revoke();
  assert.deepEqual(f.selectionMessages, [{ id: 1, message: {
    type: 'reviewer:selection-revoked', generation: 1, leaseUntil: granted.until, leaseId: granted.leaseId
  } }]);
  f.api.revoke();
  assert.equal(f.selectionMessages.length, 1, 'No holder means no stale duplicate notification');
  assert.equal((await f.api.request(2, 0)).granted, false);
  f.api.revoke();
  assert.equal(f.selectionMessages.length, 1, 'Unauthorized requests cannot become notification targets');
});

test('replacing an expired lease notifies its old holder and selection checks never send messages', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  const previous = await f.api.request(1, 1);
  f.advance();
  assert.equal(await f.api.allowsSelection(1, 1), false);
  assert.equal(f.selectionMessages.length, 0, 'Read-only checks cannot revoke or notify');
  assert.equal((await f.api.request(2, 1)).granted, true);
  assert.deepEqual(f.selectionMessages, [{ id: 1, message: {
    type: 'reviewer:selection-revoked', generation: 1, leaseUntil: previous.until, leaseId: previous.leaseId
  } }]);
  f.chrome.tabs.sendMessage = async () => { throw new Error('No content receiver'); };
  assert.doesNotThrow(() => f.api.revoke());
  await Promise.resolve();
});

test('background scanner selection retains FIFO turns and trusted user activity deferral', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  f.windows.get(7).focused = false;
  assert.equal((await f.api.request(1, 1)).granted, true);
  assert.equal((await f.api.request(2, 1)).reason, 'busy');
  f.advance();
  assert.equal((await f.api.request(1, 1)).reason, 'waiting-turn');
  assert.equal((await f.api.request(2, 1)).granted, true);
  await f.api.noteActivity(2);
  assert.equal((await f.api.request(1, 1)).reason, 'user-active');
  assert.equal(f.windows.get(7).focused, false);
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

test('a stalled focus check expires and cannot block or release a later scanner request', async () => {
  const f = fixture({ requestTimeoutMs: 20 });
  await f.api.sync([1, 2]);
  let release;
  f.beforeAuthorize(() => new Promise(resolve => { release = resolve; }));
  const pending = f.api.request(2, 1);
  const result = await Promise.race([pending,
    new Promise(resolve => setTimeout(() => resolve({ reason: 'test-deadline' }), 100))]);
  assert.equal(result.granted, false);
  assert.equal(result.reason, 'expired');
  f.beforeAuthorize(async () => {});
  assert.equal((await f.api.request(1, 1)).granted, true,
    'Another scanner can acquire focus after the stalled request expires');
  release();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.actions.some(action => action.type === 'tab-update' && action.id === 2), false,
    'Late authorization must never revive the expired request');
});

test('a late expired focus failure cannot erase the next activation acknowledgement', async () => {
  const f = fixture({ requestTimeoutMs: 25 });
  await f.api.sync([1, 2]);
  let rejectOld;
  f.beforeAuthorize(() => new Promise((_resolve, reject) => { rejectOld = reject; }));
  assert.equal((await f.api.request(2, 1)).reason, 'expired');
  f.beforeAuthorize(async () => {});
  let releaseUpdate;
  let updateStarted;
  const reachedUpdate = new Promise(resolve => { updateStarted = resolve; });
  f.beforeUpdate(() => {
    updateStarted();
    return new Promise(resolve => { releaseUpdate = resolve; });
  });
  const next = f.api.request(2, 1);
  await reachedUpdate;
  rejectOld(new Error('Old browser callback failed'));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.api.isExpectedActivation(2), true,
    'The current activation remains recognized as scanner-owned');
  releaseUpdate();
  assert.equal((await next).granted, true);
});

test('an already-issued tab activation stays serialized after its response deadline', async () => {
  const f = fixture({ requestTimeoutMs: 20 });
  await f.api.sync([1, 2]);
  let releaseUpdate;
  f.beforeUpdate(() => new Promise(resolve => { releaseUpdate = resolve; }));
  assert.equal((await f.api.request(2, 1)).reason, 'expired');
  assert.equal((await f.api.request(1, 1)).reason, 'busy',
    'A timed-out Chrome mutation may still select its target and cannot overlap another grant');
  f.beforeUpdate(async () => {});
  releaseUpdate();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal((await f.api.request(1, 1)).granted, true);
  assert.equal(f.tabs.get(1).active, true);
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

test('four reading tabs retain shared FIFO turns while retrying beyond the ticket lifetime', async () => {
  const f = fixture();
  f.tabs.get(3).windowId = 7;
  f.tabs.get(3).active = false;
  f.tabs.set(4, { id: 4, windowId: 7, url: `${home}?q=Furniture&queryNups=Furniture`,
    active: false, groupId: -1 });
  f.generations.set(4, 1);
  await f.api.sync([1, 2, 3, 4]);
  const request = id => f.api.request(id, 1, f.now() + 1000, 10_000);
  assert.equal((await request(1)).granted, true);
  const turns = [1];
  for (let step = 1; step <= 24; step++) {
    f.advance(5050);
    for (const id of [1, 2, 3, 4]) await request(id);
    if (step % 2 === 0) {
      const selected = [...f.tabs.values()].find(tab => tab.windowId === 7 && tab.active).id;
      assert.equal(selected, turns.at(-1) % 4 + 1, `reading turn ${turns.length}`);
      turns.push(selected);
    }
  }
  assert.deepEqual(turns, [1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 4, 1]);
});

test('finished reading releases its unused lease immediately while preserving FIFO contenders', async () => {
  const f = fixture();
  f.tabs.get(3).windowId = 7;
  f.tabs.get(3).active = false;
  await f.api.sync([1, 2, 3]);
  const first = await f.api.request(1, 1, null, 10_000);
  assert.equal((await f.api.request(2, 1)).reason, 'busy');
  assert.equal((await f.api.request(3, 1)).reason, 'busy');
  f.advance(2000);
  const before = f.actions.length;
  assert.equal((await f.api.release(1, 1, first.leaseId)).released, true);
  assert.equal(f.actions.length, before, 'Releasing never selects a tab or focuses a window');
  assert.equal(await f.api.allowsSelection(1, 1), false, 'The old turn no longer grants input authority');
  assert.equal((await f.api.request(1, 1)).reason, 'waiting-turn', 'The old holder cannot jump the queue');
  assert.equal((await f.api.request(3, 1)).reason, 'waiting-turn');
  const next = await f.api.request(2, 1, null, 10_000);
  assert.equal(next.granted, true, 'The next reader starts eight seconds before the original deadline');
  assert.equal(next.until, 112_000);
  assert.equal(f.selectionMessages.filter(entry => entry.message.type === 'reviewer:selection-revoked').at(-1).message.leaseId, first.leaseId);
});

test('lease release ignores wrong identities, old generations and repeated completions', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  const first = await f.api.request(1, 1, null, 10_000);
  for (const [tabId, generation, leaseId] of [
    [2, 1, first.leaseId], [1, 0, first.leaseId], [1, 1, 'stale'], [1, 1, null]
  ]) {
    assert.equal((await f.api.release(tabId, generation, leaseId)).released, false);
    assert.equal(await f.api.allowsSelection(1, 1), true);
  }
  assert.equal((await f.api.release(1, 1, first.leaseId)).released, true);
  assert.equal((await f.api.release(1, 1, first.leaseId)).released, false);
  const next = await f.api.request(1, 1);
  assert.equal((await f.api.release(1, 1, first.leaseId)).released, false);
  assert.notEqual(next.leaseId, first.leaseId);
  assert.equal(await f.api.allowsSelection(1, 1), true);
});

test('lease release never erases a replacement granted during an authorization await', async () => {
  const f = fixture();
  await f.api.sync([1, 2]);
  const first = await f.api.request(1, 1);
  let replacement;
  f.beforeAuthorize(async () => {
    f.beforeAuthorize(async () => {});
    f.api.revoke();
    replacement = await f.api.request(2, 1);
  });
  assert.equal((await f.api.release(1, 1, first.leaseId)).released, false);
  assert.equal(replacement.granted, true);
  assert.equal(await f.api.allowsSelection(2, 1), true);
});

test('released turns retain protection against expired leases, pauses and real activity', async () => {
  for (const interrupt of ['expired', 'paused', 'activity', 'generation']) {
    const f = fixture();
    await f.api.sync([1, 2]);
    const first = await f.api.request(1, 1);
    if (interrupt === 'expired') f.advance(3001);
    if (interrupt === 'paused') f.setPaused(true);
    if (interrupt === 'activity') await f.api.noteActivity(1);
    if (interrupt === 'generation') f.generations.set(1, 2);
    assert.equal((await f.api.release(1, 1, first.leaseId)).released, false, interrupt);
    const next = await f.api.request(2, 1);
    if (interrupt === 'paused' || interrupt === 'activity') assert.equal(next.granted, false, interrupt);
  }
});

async function competingReaders(options) {
  const f = fixture(options);
  f.tabs.get(3).windowId = 7;
  f.tabs.get(3).active = false;
  await f.api.sync([1, 2, 3]);
  return f;
}

const contentionOrders = [[1, 2, 3], [1, 3, 2], [2, 1, 3],
  [2, 3, 1], [3, 1, 2], [3, 2, 1]];

for (const order of contentionOrders) {
  test(`simultaneous scanner retries make bounded progress in arrival order ${order}`, async () => {
    const f = await competingReaders();
    const leases = [new Set(), new Set(), new Set()];
    const lastService = [0, 0, 0];
    for (let burst = 1; burst <= 200; burst++) {
      f.advance(6000);
      const deadline = 100_000 + burst * 6000 + 3000;
      const replies = await Promise.all(order.map(id =>
        f.api.request(id, 1, deadline, id === 1 ? 10_000 : 3000)));
      for (const [index, reply] of replies.entries()) {
        if (!reply.granted) continue;
        const id = order[index];
        leases[id - 1].add(reply.leaseId);
        assert.ok((burst - lastService[id - 1]) * 6000 <= 36_000,
          `Scanner ${id} must not starve behind simultaneous contenders`);
        lastService[id - 1] = burst;
      }
    }
    for (const [index, granted] of leases.entries()) {
      assert.ok(granted.size >= 30, `Scanner ${index + 1} must receive repeated distinct leases`);
      assert.ok((200 - lastService[index]) * 6000 <= 36_000,
        `Scanner ${index + 1} must still be making progress at the end`);
    }
  });
}

test('changing simultaneous arrival orders cannot starve any scanner', async () => {
  const f = await competingReaders();
  const granted = [0, 0, 0];
  let seed = 314159;
  for (let burst = 1; burst <= 200; burst++) {
    f.advance(6000);
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const order = contentionOrders[seed % contentionOrders.length];
    const replies = await Promise.all(order.map(id =>
      f.api.request(id, 1, 100_000 + burst * 6000 + 3000, id === 1 ? 10_000 : 3000)));
    for (const [index, reply] of replies.entries()) if (reply.granted) granted[order[index] - 1]++;
  }
  granted.forEach((count, index) => assert.ok(count >= 30, `Scanner ${index + 1} retains service`));
});

test('an unowned waiting request cannot revoke a head lease on timeout or late completion', async () => {
  const f = await competingReaders({ requestTimeoutMs: 30 });
  await Promise.all([2, 1, 3].map(id => f.api.request(id, 1, 103_000)));
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  f.beforeAuthorize(async id => { if (id === 2) await blocked; });
  try {
    const nonHead = f.api.request(2, 1, 103_000);
    const head = await f.api.request(1, 1, 103_000, 10_000);
    const reply = await nonHead;
    assert.equal(head.granted, true, 'The queued head must acquire while the non-head checks eligibility');
    assert.equal(reply.reason, 'expired');
    assert.equal(await f.api.allowsSelection(1, 1), true, 'Non-owner timeout preserves the head lease');
    release();
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(await f.api.allowsSelection(1, 1), true, 'Late non-owner completion preserves the head lease');
  } finally { release(); }
});

test('invalid or removed queue heads cannot hold healthy scanners indefinitely', async () => {
  for (const condition of ['unauthorized', 'expired', 'removed']) {
    const f = await competingReaders();
    await Promise.all([2, 1, 3].map(id => f.api.request(id, 1, 103_000)));
    if (condition === 'unauthorized') f.generations.set(1, 2);
    if (condition === 'removed') {
      f.activate(2);
      f.tabs.delete(1);
      await f.api.sync([2, 3]);
    }
    let grantedAt = null;
    for (let burst = 1; burst <= 5; burst++) {
      f.advance(6000);
      const now = 100_000 + burst * 6000;
      const ids = condition === 'removed' ? [2, 3] : [2, 1, 3];
      const replies = await Promise.all(ids.map(id => f.api.request(id, 1,
        condition === 'expired' && id === 1 ? now : now + 3000)));
      if (replies.some((reply, index) => ids[index] !== 1 && reply.granted)) { grantedAt = burst * 6000; break; }
    }
    assert.ok(grantedAt !== null && grantedAt <= 30_000, condition);
  }
});

test('simultaneous contenders retain pause, manual browsing, activity and generation guards', async () => {
  for (const condition of ['paused', 'manual-tab', 'user-active', 'expired', 'stale-generation']) {
    const f = await competingReaders();
    await Promise.all([2, 1, 3].map(id => f.api.request(id, 1, 103_000)));
    if (condition === 'paused') f.setPaused(true);
    if (condition === 'manual-tab') f.activate(9);
    if (condition === 'user-active') await f.api.noteActivity(1);
    if (condition === 'stale-generation') for (const id of [1, 2, 3]) f.generations.set(id, 2);
    const before = f.actions.filter(action => action.type === 'tab-update').length;
    const replies = await Promise.all([2, 1, 3].map(id =>
      f.api.request(id, 1, condition === 'expired' ? 100_000 : 103_000)));
    assert.ok(replies.every(reply => !reply.granted), condition);
    assert.equal(f.actions.filter(action => action.type === 'tab-update').length, before, condition);
  }
});


function focusHints(f) {
  return f.selectionMessages.filter(entry => entry.message.type === 'reviewer:focus-available');
}

test('releasing a turn hints only the FIFO head without granting or selecting it', async () => {
  const f = await competingReaders({ fakeTimers: true });
  const first = await f.api.request(1, 1, null, 10_000);
  await f.api.request(2, 1, f.now() + 1000);
  await f.api.request(3, 1, f.now() + 1000);
  f.advance(2000);
  const actions = f.actions.length;
  assert.equal((await f.api.release(1, 1, first.leaseId)).released, true);
  await f.runTimers();
  assert.deepEqual(focusHints(f), [{ id: 2, message: { type: 'reviewer:focus-available', generation: 1 } }]);
  assert.equal(f.actions.length, actions, 'a hint never selects the waiting tab');
  assert.equal(await f.api.allowsSelection(2, 1), false, 'an expired request is not granted by a wakeup');
  assert.equal((await f.api.request(2, 1, f.now() - 1)).reason, 'expired');
  assert.equal((await f.api.request(2, 1, f.now() + 1000)).granted, true, 'the receiver needs fresh authority');
  assert.equal((await f.api.release(1, 1, first.leaseId)).released, false);
  await f.runTimers();
  assert.equal(focusHints(f).length, 1, 'stale releases cannot wake another contender');
});

test('lease expiry wakes the next scanner once and revocation cancels old expiry callbacks', async () => {
  const f = await competingReaders({ fakeTimers: true });
  const first = await f.api.request(1, 1);
  await f.api.request(2, 1, f.now() + 1000);
  f.advance(3000);
  await f.runTimers();
  assert.deepEqual(focusHints(f).map(entry => entry.id), [2]);
  assert.equal(f.selectionMessages.filter(entry => entry.message.leaseId === first.leaseId).length, 1);
  await f.runTimers();
  assert.equal(focusHints(f).length, 1);
  const second = await f.api.request(2, 1);
  await f.api.request(3, 1);
  f.api.revoke();
  f.advance(5000);
  await f.runTimers();
  assert.equal(focusHints(f).length, 1, 'revoked queues cannot emit a delayed wakeup');
  assert.equal(await f.api.allowsSelection(2, 1), false);
  assert.notEqual(first.leaseId, second.leaseId);
});

test('wakeup checks pause, rotation, input, manual browsing and current generation afresh', async () => {
  for (const condition of ['paused', 'rotation', 'activity', 'manual-tab', 'generation', 'removed', 'stale-ticket']) {
    const f = await competingReaders({ fakeTimers: true });
    await f.api.request(1, 1);
    await f.api.request(2, 1);
    if (condition === 'paused') f.setPaused(true);
    if (condition === 'rotation') f.configure({ rotateScannerTabs: false });
    if (condition === 'activity') await f.api.noteActivity(1);
    if (condition === 'manual-tab') f.activate(9);
    if (condition === 'generation') f.generations.set(2, 2);
    if (condition === 'removed') f.tabs.delete(2);
    f.advance(condition === 'stale-ticket' ? 16000 : 3000);
    await f.runTimers();
    assert.equal(focusHints(f).length, 0, condition);
  }
});

test('a delayed hint cannot escape revocation or notify an old head after a replacement grant', async () => {
  const f = await competingReaders({ fakeTimers: true });
  const first = await f.api.request(1, 1, null, 10_000);
  await f.api.request(2, 1);
  let resume;
  const held = new Promise(resolve => { resume = resolve; });
  f.beforeAuthorize(async id => { if (id === 2) await held; });
  await f.api.release(1, 1, first.leaseId);
  await f.runTimers();
  f.api.revoke();
  f.beforeAuthorize(async () => {});
  const replacement = await f.api.request(3, 1);
  resume();
  await f.runTimers();
  assert.equal(replacement.granted, true);
  assert.equal(focusHints(f).length, 0);
  assert.equal(await f.api.allowsSelection(3, 1), true);
});

test('missing receivers retain bounded polling fallback without rejection or notification storms', async () => {
  const f = await competingReaders({ fakeTimers: true });
  const first = await f.api.request(1, 1);
  const reply = await f.api.request(2, 1);
  assert.equal(reply.retryAfterMs, 5000);
  let failedHints = 0;
  f.chrome.tabs.sendMessage = async (id, message) => {
    if (message.type === 'reviewer:focus-available') failedHints++;
    throw new Error('No receiver');
  };
  await f.api.release(1, 1, first.leaseId);
  await f.runTimers();
  f.advance(5000);
  await f.runTimers();
  assert.equal(failedHints, 1);
  assert.equal((await f.api.request(2, 1)).granted, true);
});

async function handoffSimulation(useHints) {
  const f = await competingReaders({ fakeTimers: true });
  const pending = new Map([[1, f.now()], [2, f.now()], [3, f.now()]]);
  const turns = [];
  let holder = null;
  let messageIndex = 0;
  let previousRelease = null;
  const idle = [];
  for (let step = 0; step < 2000 && turns.length < 18; step++) {
    if (holder && f.now() >= holder.done) {
      await f.api.release(holder.id, 1, holder.leaseId);
      previousRelease = f.now();
      pending.set(holder.id, f.now());
      holder = null;
    }
    await f.runTimers();
    const hints = focusHints(f);
    for (const hint of hints.slice(messageIndex)) if (useHints && pending.has(hint.id)) pending.set(hint.id, f.now());
    messageIndex = hints.length;
    for (const [id, due] of [...pending]) if (due <= f.now()) {
      const reply = await f.api.request(id, 1, f.now() + 1000, 10_000);
      if (reply.granted) {
        if (previousRelease !== null) idle.push(f.now() - previousRelease);
        holder = { id, done: f.now() + 3000, leaseId: reply.leaseId };
        pending.delete(id);
        turns.push(id);
      } else pending.set(id, f.now() + reply.retryAfterMs);
    }
    f.advance(100);
  }
  return { turns, idle, elapsedMs: f.now() - 100_000 };
}

test('advisory handoff reduces idle time for the same eighteen FIFO turns and three scanners', async t => {
  const polling = await handoffSimulation(false);
  const notified = await handoffSimulation(true);
  assert.equal(polling.turns.length, 18);
  assert.deepEqual(notified.turns, polling.turns, 'same work and FIFO coverage');
  for (const id of [1, 2, 3]) assert.equal(notified.turns.filter(turn => turn === id).length, 6);
  assert.ok(notified.elapsedMs < polling.elapsedMs, JSON.stringify({ polling, notified }));
  assert.ok(notified.idle.reduce((a, b) => a + b, 0) < polling.idle.reduce((a, b) => a + b, 0));
  t.diagnostic(JSON.stringify({ pollingMs: polling.elapsedMs, notifiedMs: notified.elapsedMs,
    pollingIdleMs: polling.idle.reduce((a, b) => a + b, 0),
    notifiedIdleMs: notified.idle.reduce((a, b) => a + b, 0), turns: notified.turns }));
});
