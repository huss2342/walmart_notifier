import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const backgroundSource = fs.readFileSync(
  new URL('../extension/background.js', import.meta.url), 'utf8'
);
const contentSource = fs.readFileSync(
  new URL('../extension/content.js', import.meta.url), 'utf8'
);
const optionsSource = fs.readFileSync(
  new URL('../extension/options.js', import.meta.url), 'utf8'
);

function event() {
  return { listeners: [], addListener(fn) { this.listeners.push(fn); } };
}

function storageArea(initial = {}) {
  const data = { ...initial };
  return {
    data,
    async get(keys) {
      if (keys == null) return { ...data };
      if (typeof keys === 'string') return keys in data ? { [keys]: data[keys] } : {};
      if (Array.isArray(keys)) {
        return Object.fromEntries(
          keys.filter((key) => key in data).map((key) => [key, data[key]])
        );
      }
      return Object.fromEntries(
        Object.entries(keys).map(([key, fallback]) =>
          [key, data[key] === undefined ? fallback : data[key]])
      );
    },
    async set(values) { Object.assign(data, values); },
    async remove(keys) {
      for (const key of Array.isArray(keys) ? keys : [keys]) delete data[key];
    }
  };
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async text() { return JSON.stringify(body); }
  };
}

function loadBackground({
  tabs = [], probes = new Map(), summary = {}, fetchImpl = null
} = {}) {
  const local = storageArea();
  const session = storageArea();
  const sync = storageArea();
  const fetchCalls = [];
  const roleMessages = [];
  const tabActions = [];
  const alarmCalls = [];
  const chrome = {
    storage: { local, session, sync, onChanged: event() },
    runtime: { onMessage: event(), onStartup: event(), onInstalled: event() },
    alarms: {
      onAlarm: event(),
      async create(name, info) { alarmCalls.push({ name, info }); },
      async clear() { return true; }
    },
    action: {
      async setBadgeText() {},
      async setBadgeBackgroundColor() {}
    },
    tabs: {
      onRemoved: event(),
      onUpdated: event(),
      async query() { return tabs; },
      async get(id) { return tabs.find((tab) => tab.id === id); },
      async update(id, options) { tabActions.push({ type: 'update', id, options }); },
      async reload(id, options) { tabActions.push({ type: 'reload', id, options }); },
      async sendMessage(id, message) {
        if (message.type === 'reviewer:probe') {
          const reply = probes.get(id);
          if (reply instanceof Error || reply == null) throw reply || new Error('no receiver');
          return typeof reply === 'function' ? reply() : reply;
        }
        if (message.type === 'reviewer:set-role') {
          roleMessages.push({ id, message });
          return { ok: true };
        }
        return { ok: true };
      }
    }
  };
  const context = vm.createContext({
    chrome,
    URL,
    AbortController,
    setTimeout,
    clearTimeout,
    console,
    fetch: async (url, init) => {
      fetchCalls.push(init);
      if (fetchImpl) return fetchImpl(url, init, fetchCalls.length);
      return jsonResponse(summary);
    }
  });
  vm.runInContext(
    `${backgroundSource}\n;globalThis.__test = {` +
      'selectPrimary, readCoordinator, handlePageReport, restartPrimary, post, checkedEndpoint,' +
      'isBotCheckUrl, handleBotCheck, pausedUntil, refreshTick, scheduleRefresh};',
    context
  );
  return {
    api: context.__test, chrome, local, session, fetchCalls, roleMessages, tabActions, alarmCalls
  };
}

function loadOptions({
  initial = {}, sweepStatus = { ok: true, tabCount: 1 }, rulesBody = null
} = {}) {
  const optionsFetches = [];
  class FakeNode {
    constructor() {
      this.children = [];
      this._text = '';
      this.value = '';
      this.checked = false;
      this.disabled = false;
      this.className = '';
      this.listeners = new Map();
    }

    get textContent() {
      return this._text + this.children.map((child) => child.textContent || '').join(' ');
    }

    set textContent(value) {
      this._text = String(value ?? '');
      this.children = [];
    }

    appendChild(child) {
      this.children.push(child);
      return child;
    }

    replaceChildren(...children) {
      this._text = '';
      this.children = children;
    }

    addEventListener(type, listener) {
      this.listeners.set(type, listener);
    }
  }

  const ids = [
    'endpoint', 'endpointError', 'token', 'pathPattern', 'pathError',
    'refreshMinutes', 'pageDelaySeconds', 'saved', 'status', 'minValue',
    'maxValue', 'keywords', 'excludeKeywords', 'priority', 'alertUnknown',
    'rulesBanner', 'rulesSaved', 'rulesError', 'checkConnection',
    'testNotification', 'testResult', 'save', 'saveRules', 'resetRules',
    'resetSweep', 'matchMode'
  ];
  const elements = Object.fromEntries(ids.map((id) => [id, new FakeNode()]));
  const document = {
    getElementById(id) { return elements[id]; },
    createElement() { return new FakeNode(); },
    createDocumentFragment() { return new FakeNode(); },
    createTextNode(text) {
      const node = new FakeNode();
      node.textContent = text;
      return node;
    }
  };
  const local = storageArea({
    endpoint: 'http://127.0.0.1:8787/ingest',
    ...initial
  });
  const chrome = {
    storage: { local },
    runtime: {
      async sendMessage(message) {
        if (message.type === 'reviewer:status') return sweepStatus;
        return { ok: true };
      }
    }
  };
  const response = (body, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    async text() { return JSON.stringify(body); },
    async json() { return body; }
  });
  const context = vm.createContext({
    chrome,
    document,
    URL,
    AbortController,
    setTimeout,
    clearTimeout,
    setInterval() {},
    console,
    fetch: async (url, init = {}) => {
      const path = new URL(url).pathname;
      optionsFetches.push({ path, ...init });
      if (path === '/rules') {
        if (init.method === 'PUT') {
          return response({ source: 'saved', rules: JSON.parse(init.body).rules });
        }
        return response(rulesBody || {
          source: 'saved',
          rules: [{
            name: 'my-filters', keywords: [], exclude_keywords: [],
            min_value_usd: 49, priority: 'high', alert_on_unknown_value: false
          }]
        });
      }
      return response({
        status: 'ok', notifier: 'TelegramNotifier', notifier_configured: true,
        seen_items: 3439, observed_values: { max_value_usd: 149.5 }
      });
    }
  });
  vm.runInContext(
    `${optionsSource}\n;globalThis.__test = {` +
      'completedSweepPages, activeSweep, renderStatus, formToRules, loadRules};',
    context
  );
  return { api: context.__test, elements, local, optionsFetches };
}

test('coordinator deterministically picks the leftmost reachable tab, then stays sticky', async () => {
  const tabs = [
    { id: 22, windowId: 1, index: 4, status: 'complete', url: 'https://www.walmart.com/reviews/claim-product' },
    { id: 11, windowId: 1, index: 2, status: 'complete', url: 'https://www.walmart.com/reviews/claim-product' }
  ];
  const probes = new Map([
    [11, { ready: true, active: false, generation: 0 }],
    [22, { ready: true, active: false, generation: 0 }]
  ]);
  const { api } = loadBackground({ tabs, probes });

  assert.equal((await api.selectPrimary()).state.tabId, 11);
  // Moving the backup earlier does not steal a healthy primary mid-sweep.
  tabs[0].index = 0;
  assert.equal((await api.selectPrimary()).state.tabId, 11);

  probes.set(11, new Error('content script gone'));
  assert.equal((await api.selectPrimary()).state.tabId, 22);
});

test('a primary stuck loading past its lease fails over to a reachable backup', async () => {
  const tabs = [
    { id: 11, windowId: 1, index: 1, status: 'loading', url: 'https://www.walmart.com/reviews/claim-product?page=2' },
    { id: 22, windowId: 1, index: 2, status: 'complete', url: 'https://www.walmart.com/reviews/claim-product' }
  ];
  const probes = new Map([
    [11, new Error('hung load')],
    [22, { ready: true, active: false, generation: 4 }]
  ]);
  const { api, session } = loadBackground({ tabs, probes });
  session.data.reviewerCoordinator = { tabId: 11, generation: 4, selectedAt: 1 };

  const selected = await api.selectPrimary();

  assert.equal(selected.state.tabId, 22);
  assert.equal(selected.state.generation, 5);
});

test('only the primary sender can post and loopback is not mislabelled as local', async () => {
  const summary = {
    seen: 1, new: 1, duplicates: 0, filtered: 0, matched: 1,
    notified: 1, failed: 0, seeded: 0, value_known: 2,
    value_unknown: 0, min_value_usd: 10, max_value_usd: 20
  };
  const { api, session, local, fetchCalls } = loadBackground({ summary });
  session.data.reviewerCoordinator = { tabId: 11, generation: 7, selectedAt: 1 };

  const rejected = await api.handlePageReport(
    { generation: 7, page: 1, items: [{ item_id: 'x' }] },
    { tab: { id: 22 } }
  );
  assert.equal(rejected.ok, false);
  assert.equal(fetchCalls.length, 0);

  const accepted = await api.handlePageReport(
    { generation: 7, page: 1, items: [{ item_id: 'x' }] },
    { tab: { id: 11 } }
  );
  assert.equal(accepted.ok, true);
  assert.equal(fetchCalls.length, 1);
  assert.equal('targetAddressSpace' in fetchCalls[0], false);
  assert.equal(local.data.lastDuplicates, 0);
  assert.equal(local.data.lastValueKnown, 2);
  assert.equal(local.data.lastMinValue, 10);
});

test('neither extension fetch path overrides Chrome loopback classification', () => {
  assert.doesNotMatch(backgroundSource, /targetAddressSpace\s*:/);
  assert.doesNotMatch(optionsSource, /targetAddressSpace\s*:/);
});

test('Options labels relay counters as the latest page result', async () => {
  const now = Date.now();
  const { api, elements } = loadOptions({
    initial: {
      lastRelay: now,
      lastPage: 20,
      lastSeen: 80,
      lastNew: 21,
      lastDuplicates: 59,
      lastFiltered: 21,
      lastMatched: 0,
      lastNotified: 0,
      lastFailed: 0,
      lastPending: 0,
      lastValueKnown: 80,
      lastValueUnknown: 0,
      lastMinValue: 4.89,
      lastMaxValue: 33.99
    },
    sweepStatus: {
      ok: true,
      generation: 7,
      tabCount: 1,
      sweep: {
        generation: 7,
        total: 23,
        visited: Array.from({ length: 19 }, (_, index) => index + 1),
        startedAt: now - 60_000,
        lastProgressAt: now - 1_000
      }
    }
  });

  await api.renderStatus();

  const text = elements.status.textContent;
  assert.match(text, /Latest page result \(relayed just now\)/);
  assert.match(text, /0 alerted on this page/);
  assert.match(text, /21 new items on this page did not match the filters/);
  assert.match(text, /Sweep in progress: 20 of 23 pages done/);
});

test('Options only adds a just-acknowledged page to its current sweep', async () => {
  const now = Date.now();
  const base = {
    generation: 4,
    total: 23,
    visited: Array.from({ length: 19 }, (_, index) => index + 1),
    startedAt: now - 60_000,
    lastProgressAt: now - 1_000
  };
  const { api } = loadOptions({
    sweepStatus: {
      ok: true,
      generation: 5,
      tabCount: 1,
      sweep: { ...base, generation: 4 }
    }
  });

  assert.equal(api.completedSweepPages(base, {
    lastPage: 20, lastRelay: now, lastFailed: 0, lastPending: 0
  }), 20);
  assert.equal(api.completedSweepPages(base, {
    lastPage: 20, lastRelay: base.startedAt - 1, lastFailed: 0, lastPending: 0
  }), 19);
  assert.equal(api.completedSweepPages(base, {
    lastPage: 20, lastRelay: now, lastFailed: 1, lastPending: 0
  }), 19);
  assert.equal(api.completedSweepPages(base, {
    lastPage: 21, lastRelay: now, lastFailed: 0, lastPending: 0
  }), 19);
  assert.equal((await api.activeSweep()).sweep, null);
});

test('the endpoint must be the exact loopback ingest route', () => {
  const { api } = loadBackground();

  assert.equal(api.checkedEndpoint('http://127.0.0.1:8787/ingest'),
    'http://127.0.0.1:8787/ingest');
  assert.throws(() => api.checkedEndpoint('http://127.0.0.1:8787/wrong'), /exactly.*ingest/i);
  assert.throws(() => api.checkedEndpoint('https://127.0.0.1:8787/ingest'), /must use http/i);
});

test('delivery failures are a retryable negative acknowledgement', async () => {
  const { api, session, local } = loadBackground({
    summary: { seen: 1, new: 1, matched: 1, notified: 0, failed: 1 }
  });
  session.data.reviewerCoordinator = { tabId: 11, generation: 3, selectedAt: 1 };

  const reply = await api.handlePageReport(
    { generation: 3, page: 4, items: [{ item_id: 'retry-me' }] },
    { tab: { id: 11 } }
  );
  assert.equal(reply.ok, false);
  assert.equal(reply.retryable, true);
  assert.match(reply.error, /page will retry/i);
  assert.equal(local.data.lastFailed, 1);
  assert.equal(local.data.lastPage, 4);
});

test('a partial successful acknowledgement never advances the page', async () => {
  const { api, session } = loadBackground({
    summary: { seen: 0, new: 0, matched: 0, notified: 0, failed: 0, pending: 0 }
  });
  session.data.reviewerCoordinator = { tabId: 11, generation: 3, selectedAt: 1 };

  const reply = await api.handlePageReport(
    { generation: 3, page: 6, items: [{ item_id: 'not-acknowledged' }] },
    { tab: { id: 11 } }
  );
  assert.equal(reply.ok, false);
  assert.equal(reply.retryable, true);
  assert.match(reply.error, /acknowledged 0 of 1 items/i);
});

test('large pages are posted sequentially in 200-item batches with one aggregate status', async () => {
  const items = Array.from({ length: 401 }, (_, index) => ({ item_id: `item-${index}` }));
  const summaries = [
    {
      seen: 200, new: 100, duplicates: 100, filtered: 40, matched: 60,
      notified: 3, failed: 0, pending: 0, seeded: 1,
      value_known: 199, value_unknown: 1, min_value_usd: 10, max_value_usd: 50
    },
    {
      seen: 200, new: 20, duplicates: 180, filtered: 15, matched: 5,
      notified: 2, failed: 0, pending: 0, seeded: 0,
      value_known: 198, value_unknown: 2, min_value_usd: 2, max_value_usd: 70
    },
    {
      seen: 1, new: 1, filtered: 1, matched: 0, notified: 0, failed: 0,
      pending: 0, seeded: 0, value_known: 1, value_unknown: 0,
      min_value_usd: 7, max_value_usd: 7
    }
  ];
  let activeRequests = 0;
  let maxActiveRequests = 0;
  const { api, session, local, fetchCalls } = loadBackground({
    fetchImpl(_url, _init, callNumber) {
      activeRequests += 1;
      maxActiveRequests = Math.max(maxActiveRequests, activeRequests);
      const response = jsonResponse(summaries[callNumber - 1]);
      return {
        ...response,
        async text() {
          activeRequests -= 1;
          return response.text();
        }
      };
    }
  });
  session.data.reviewerCoordinator = { tabId: 11, generation: 4, selectedAt: 1 };

  const reply = await api.handlePageReport(
    { generation: 4, page: 8, items },
    { tab: { id: 11 } }
  );

  const bodies = fetchCalls.map((call) => JSON.parse(call.body).items);
  assert.deepEqual(bodies.map((batch) => batch.length), [200, 200, 1]);
  assert.equal(bodies[0][0].item_id, 'item-0');
  assert.equal(bodies[1][0].item_id, 'item-200');
  assert.equal(bodies[2][0].item_id, 'item-400');
  assert.equal(maxActiveRequests, 1);
  assert.equal(reply.ok, true);
  assert.deepEqual(
    {
      seen: reply.summary.seen,
      new: reply.summary.new,
      duplicates: reply.summary.duplicates,
      filtered: reply.summary.filtered,
      matched: reply.summary.matched,
      notified: reply.summary.notified,
      seeded: reply.summary.seeded,
      valueKnown: reply.summary.value_known,
      valueUnknown: reply.summary.value_unknown,
      min: reply.summary.min_value_usd,
      max: reply.summary.max_value_usd
    },
    {
      seen: 401, new: 121, duplicates: 280, filtered: 56, matched: 65,
      notified: 5, seeded: 1, valueKnown: 398, valueUnknown: 3, min: 2, max: 70
    }
  );
  assert.equal(local.data.lastSeen, 401);
  assert.equal(local.data.lastNew, 121);
  assert.equal(local.data.lastDuplicates, 280);
  assert.equal(local.data.lastNotified, 5);
  assert.equal(local.data.lastMinValue, 2);
  assert.equal(local.data.lastMaxValue, 70);
  assert.equal(local.data.lastPage, 8);
});

test('a later batch failure retries the whole page so completed batches dedupe safely', async () => {
  const items = Array.from({ length: 401 }, (_, index) => ({ item_id: `retry-${index}` }));
  let calls = 0;
  const { api, local, fetchCalls } = loadBackground({
    fetchImpl(_url, init) {
      calls += 1;
      const count = JSON.parse(init.body).items.length;
      if (calls === 2) return jsonResponse({ error: 'temporary outage' }, 503);
      return jsonResponse({
        seen: count,
        new: calls === 1 || calls >= 4 ? count : 0,
        duplicates: calls === 3 ? count : 0,
        failed: 0,
        pending: 0
      });
    }
  });

  const failed = await api.post(items, 9);
  assert.equal(failed.ok, false);
  assert.equal(failed.retryable, true);
  assert.match(failed.error, /batch 2 of 3.*HTTP 503/i);
  assert.deepEqual(fetchCalls.map((call) => JSON.parse(call.body).items.length), [200, 200]);
  assert.equal(local.data.lastRelay, undefined);

  const retried = await api.post(items, 9);
  assert.equal(retried.ok, true);
  assert.deepEqual(
    fetchCalls.map((call) => JSON.parse(call.body).items.length),
    [200, 200, 200, 200, 1]
  );
  assert.equal(JSON.parse(fetchCalls[0].body).items[0].item_id, 'retry-0');
  assert.equal(JSON.parse(fetchCalls[2].body).items[0].item_id, 'retry-0');
  assert.equal(retried.summary.seen, 401);
  assert.equal(local.data.ingestConsecutiveFailures, 0);
});

test('delivery failures and pending sends aggregate across all batches before retry', async () => {
  const items = Array.from({ length: 201 }, (_, index) => ({ item_id: `pending-${index}` }));
  const summaries = [
    { seen: 200, new: 1, duplicates: 199, matched: 1, failed: 1, pending: 0 },
    { seen: 1, new: 1, duplicates: 0, matched: 1, failed: 0, pending: 1 }
  ];
  const { api, session, local, fetchCalls } = loadBackground({
    fetchImpl(_url, _init, callNumber) {
      return jsonResponse(summaries[callNumber - 1]);
    }
  });
  session.data.reviewerCoordinator = { tabId: 11, generation: 2, selectedAt: 1 };

  const reply = await api.handlePageReport(
    { generation: 2, page: 3, items },
    { tab: { id: 11 } }
  );

  assert.equal(fetchCalls.length, 2);
  assert.equal(reply.ok, false);
  assert.equal(reply.retryable, true);
  assert.equal(reply.summary.failed, 1);
  assert.equal(reply.summary.pending, 1);
  assert.match(reply.error, /1 failed delivery.*1 delivery attempt is still pending/i);
  assert.equal(local.data.lastSeen, 201);
  assert.equal(local.data.lastFailed, 1);
  assert.equal(local.data.lastPending, 1);
});

test('restart revokes the old document lease before navigating to page one', async () => {
  const tabs = [{
    id: 11, windowId: 1, index: 0, status: 'complete',
    url: 'https://www.walmart.com/reviews/claim-product?page=7'
  }];
  const probes = new Map([[11, { ready: true, active: true, generation: 8 }]]);
  const { api, session, roleMessages, tabActions } = loadBackground({ tabs, probes });
  session.data.reviewerCoordinator = { tabId: 11, generation: 8, selectedAt: 1 };

  const reply = await api.restartPrimary('test');
  assert.equal(reply.ok, true);
  assert.equal(roleMessages[0].message.active, false);
  assert.equal(roleMessages[0].message.generation, 9);
  assert.equal(tabActions[0].type, 'update');
  assert.doesNotMatch(tabActions[0].options.url, /[?&]page=/);
});

test('an overlapping pending delivery also keeps the page open for retry', async () => {
  const { api, session, local } = loadBackground({
    summary: { seen: 1, new: 1, matched: 1, notified: 0, failed: 0, pending: 1 }
  });
  session.data.reviewerCoordinator = { tabId: 11, generation: 4, selectedAt: 1 };

  const reply = await api.handlePageReport(
    { generation: 4, page: 5, items: [{ item_id: 'in-flight' }] },
    { tab: { id: 11 } }
  );
  assert.equal(reply.ok, false);
  assert.equal(reply.retryable, true);
  assert.match(reply.error, /still pending.*page will retry/i);
  assert.equal(local.data.lastPending, 1);
});

function loadContent({ pageAck = { ok: false, retryable: true } } = {}) {
  const local = storageArea();
  const timerCallbacks = new Map();
  let timerId = 0;
  const fakeSetTimeout = (callback, ms = 0) => {
    const id = ++timerId;
    timerCallbacks.set(id, { callback, ms });
    return id;
  };
  const fakeClearTimeout = (id) => timerCallbacks.delete(id);
  const body = { innerText: 'No search results', scrollHeight: 100 };
  const pager = { innerText: '1 2 3 24' };
  const document = {
    body,
    addEventListener() {},
    querySelectorAll() { return []; },
    querySelector(selector) {
      return selector.includes('page-number') ? { closest: () => pager } : null;
    }
  };
  class MutationObserver {
    observe() {}
    disconnect() {}
  }
  const location = {
    href: 'https://www.walmart.com/reviews/claim-product?page=2',
    pathname: '/reviews/claim-product',
    search: '?page=2',
    origin: 'https://www.walmart.com',
    assign() {},
    reload() {}
  };
  const chrome = {
    storage: { local },
    runtime: {
      onMessage: event(),
      async sendMessage(message) {
        if (message.type === 'reviewer:role') {
          // The test assigns a role explicitly after evaluation.
          return new Promise(() => {});
        }
        if (message.type === 'reviewer:page') return pageAck;
        return { ok: true };
      }
    }
  };
  const context = vm.createContext({
    chrome,
    document,
    window: { scrollY: 0, scrollTo() {} },
    location,
    URL,
    URLSearchParams,
    MutationObserver,
    setTimeout: fakeSetTimeout,
    clearTimeout: fakeClearTimeout,
    setInterval() {},
    console
  });
  vm.runInContext(
    `${contentSource}\n;globalThis.__test = {role, nextUnvisited, handleEndPage, ` +
      'queueWhenIdle, relay, scheduleRelay, setCollector(value) { collect = value; }, ' +
      'setLastInteraction(value) { lastInteraction = value; }, ' +
      'setPhase(value) { phase = value; }, getPhase() { return phase; }};',
    context
  );
  return { api: context.__test, local, timerCallbacks };
}

test('transient mid-catalogue no-results stays on the same page for retry', async () => {
  const { api, local, timerCallbacks } = loadContent();
  Object.assign(api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 9 });
  local.data['sweep:1'] = {
    generation: 9, total: 24, visited: [1], endRetries: {}, startedAt: 1
  };

  await api.handleEndPage(9);
  assert.deepEqual(local.data['sweep:1'].visited, [1]);
  assert.equal(local.data['sweep:1'].endRetries[2], 1);
  assert.equal(api.getPhase(), 'transient-end-retry');
  assert.equal(timerCallbacks.size, 1);
});

test('a negative ingest acknowledgement never marks the page visited', async () => {
  const { api, local, timerCallbacks } = loadContent({
    pageAck: { ok: false, retryable: true, active: true, primaryTabId: 1, generation: 6 }
  });
  Object.assign(api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 6 });
  api.setCollector(() => [{ item_id: 'not-delivered-yet' }]);

  await api.relay();
  assert.equal(local.data['sweep:1'], undefined);
  assert.equal(api.getPhase(), 'ingest-retry');
  assert.equal(timerCallbacks.size, 1);
});

test('pending navigation survives user activity and runs once the page is idle', () => {
  const { api, timerCallbacks } = loadContent();
  Object.assign(api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 2 });
  api.setLastInteraction(Date.now());
  let ran = 0;
  api.queueWhenIdle(() => { ran += 1; }, 0);

  const first = [...timerCallbacks.entries()][0];
  timerCallbacks.delete(first[0]);
  first[1].callback();
  assert.equal(ran, 0);
  assert.equal(api.getPhase(), 'waiting-user');

  api.setLastInteraction(0);
  const second = [...timerCallbacks.entries()][0];
  timerCallbacks.delete(second[0]);
  second[1].callback();
  assert.equal(ran, 1);
});

test('page mutations cannot wake a completed sweep', () => {
  const { api, timerCallbacks } = loadContent();
  Object.assign(api.role, { active: true, tabId: 1, primaryTabId: 1, generation: 2 });
  api.setPhase('complete');

  api.scheduleRelay(0, false);

  assert.equal(timerCallbacks.size, 0);
  assert.equal(api.getPhase(), 'complete');
});


// --- Walmart bot check -------------------------------------------------------

const REVIEW_TAB = {
  id: 11, windowId: 1, index: 0, status: 'complete',
  url: 'https://www.walmart.com/reviews/claim-product?q=&page=20'
};

function pausedBackground() {
  const probes = new Map([[11, { ready: true, active: true, generation: 0 }]]);
  return loadBackground({ tabs: [REVIEW_TAB], probes, summary: { ok: true } });
}

test('only the Walmart /blocked page counts as a bot check', ({ assert: _ }) => {
  const { api } = pausedBackground();
  assert.equal(api.isBotCheckUrl(
    'https://www.walmart.com/blocked?url=L3Jldmlld3M&uuid=x'), true);
  assert.equal(api.isBotCheckUrl('https://www.walmart.com/blocked'), true);
  assert.equal(api.isBotCheckUrl('https://www.walmart.com/reviews/claim-product'), false);
  assert.equal(api.isBotCheckUrl('https://www.walmart.com/blocked-items'), false);
  assert.equal(api.isBotCheckUrl('https://evil.example/blocked'), false);
  assert.equal(api.isBotCheckUrl('not a url'), false);
});

test('a bot check pauses for an hour, stands tabs down and alerts once', async () => {
  const bg = pausedBackground();
  await bg.api.selectPrimary();
  await bg.local.set({ 'sweep:11': { total: 24, visited: [1, 2, 3] } });

  const before = Date.now();
  const result = await bg.api.handleBotCheck(11);
  assert.equal(result.minutes, 60);
  assert.ok(result.pausedUntil >= before + 60 * 60_000);

  // No tab may sweep, and the half-finished walk is discarded so solving the
  // check does not resume it at page 20.
  assert.equal((await bg.api.readCoordinator()).tabId, null);
  assert.equal(bg.local.data['sweep:11'], undefined);
  assert.ok(bg.roleMessages.some((m) => m.id === 11 && m.message.active === false));

  // The next alarm fires when the pause ends, not on the refresh interval.
  // Compared field by field: objects built inside the vm context have a
  // different Object prototype, which deepStrictEqual treats as unequal.
  const alarm = bg.alarmCalls.at(-1);
  assert.equal(alarm.name, 'refresh');
  assert.equal(alarm.info.when, result.pausedUntil);

  // One alert to the notifier.
  const alerts = bg.fetchCalls.filter((init) => init.body?.includes('paused_minutes'));
  assert.equal(alerts.length, 1);
  assert.equal(JSON.parse(alerts[0].body).paused_minutes, 60);

  // The same block fires several onUpdated events; they count once.
  const again = await bg.api.handleBotCheck(11);
  assert.equal(again.duplicate, true);
  assert.equal(bg.fetchCalls.filter((i) => i.body?.includes('paused_minutes')).length, 1);
});

test('repeat checks within a day double the pause, capped at a day', async () => {
  const bg = pausedBackground();
  const now = Date.now();
  // A previous check 10 minutes ago whose pause has already been cleared.
  await bg.local.set({ botCheck: { pausedUntil: 0, count: 1, lastAt: now - 10 * 60_000 } });
  assert.equal((await bg.api.handleBotCheck(11)).minutes, 120);

  await bg.local.set({ botCheck: { pausedUntil: 0, count: 9, lastAt: now - 10 * 60_000 } });
  assert.equal((await bg.api.handleBotCheck(11)).minutes, 24 * 60);

  // More than a day since the last one: start over at an hour.
  await bg.local.set({ botCheck: { pausedUntil: 0, count: 5, lastAt: now - 25 * 3600_000 } });
  assert.equal((await bg.api.handleBotCheck(11)).minutes, 60);
});

test('while paused nothing is selected, restarted or reloaded', async () => {
  const bg = pausedBackground();
  await bg.local.set({
    refreshMinutes: 3,
    botCheck: { pausedUntil: Date.now() + 3600_000, count: 1, lastAt: Date.now() }
  });

  assert.equal((await bg.api.selectPrimary()).tab, null);
  const scheduled = await bg.api.restartPrimary('scheduled');
  assert.equal(scheduled.paused, true);
  await bg.api.refreshTick();
  assert.deepEqual(bg.tabActions, []);

  // Changing the refresh interval cannot pull the next alarm inside the pause.
  await bg.api.scheduleRefresh();
  assert.ok(bg.alarmCalls.at(-1).info.when > Date.now());
});

test('Restart sweep is the user saying the check is solved', async () => {
  const bg = pausedBackground();
  await bg.local.set({
    botCheck: { pausedUntil: Date.now() + 3600_000, count: 2, lastAt: Date.now() }
  });

  const result = await bg.api.restartPrimary('manual');
  assert.equal(result.ok, true);
  assert.equal(await bg.api.pausedUntil(), 0);
  assert.equal(bg.tabActions.at(-1).type, 'update');
  // The count survives, so an immediate repeat still backs off longer.
  assert.equal(bg.local.data.botCheck.count, 2);
});


// --- filter modes ------------------------------------------------------------

function fillFilterForm(elements, { mode, min = '', keywords = '', exclude = '' }) {
  elements.matchMode.value = mode;
  elements.minValue.value = min;
  elements.keywords.value = keywords;
  elements.excludeKeywords.value = exclude;
  elements.priority.value = 'high';
}

test('"any" writes two rules, because the engine ORs across rules only', () => {
  // A single rule ANDs its clauses, so "$80 or a vanity/mirror title" cannot
  // be expressed as one rule -- which silently made the filter stricter.
  const { api, elements } = loadOptions();
  fillFilterForm(elements, {
    mode: 'any', min: '80', keywords: 'vanity, mirror', exclude: 'covers'
  });

  const rules = api.formToRules();
  assert.equal(rules.length, 2);
  // Joined rather than deep-compared: arrays built inside the vm context have
  // a different Array prototype, which deepStrictEqual treats as unequal.
  assert.equal(rules.map((r) => r.name).join(','),
    'my-filters-value,my-filters-keywords');
  assert.equal(rules[0].min_value_usd, 80);
  assert.equal(rules[0].keywords.join(','), '');
  assert.equal(rules[1].keywords.join(','), 'vanity,mirror');
  // The veto applies to both halves, or an excluded item slips through one.
  assert.equal(rules[0].exclude_keywords.join(','), 'covers');
  assert.equal(rules[1].exclude_keywords.join(','), 'covers');
});

test('"all" still writes the single combined rule', () => {
  const { api, elements } = loadOptions();
  fillFilterForm(elements, { mode: 'all', min: '80', keywords: 'vanity' });

  const rules = api.formToRules();
  assert.equal(rules.length, 1);
  assert.equal(rules[0].name, 'my-filters');
  assert.equal(rules[0].min_value_usd, 80);
  assert.equal(rules[0].keywords.join(','), 'vanity');
});

test('an empty side of "any" contributes no rule at all', () => {
  // An empty rule matches everything, which would alert on the catalogue.
  const { api, elements } = loadOptions();
  fillFilterForm(elements, { mode: 'any', min: '', keywords: 'mirror' });
  assert.equal(api.formToRules().map((r) => r.name).join(','), 'my-filters-keywords');

  fillFilterForm(elements, { mode: 'any', min: '25', keywords: '' });
  assert.equal(api.formToRules().map((r) => r.name).join(','), 'my-filters-value');

  fillFilterForm(elements, { mode: 'any', min: '', keywords: '' });
  assert.equal(api.formToRules().length, 0);
});

test('a saved "any" pair reloads as "any" and is not flagged as hand-built', async () => {
  const { api, elements } = loadOptions({
    rulesBody: {
      source: 'user',
      rules: [
        { name: 'my-filters-value', keywords: [], exclude_keywords: ['covers'],
          min_value_usd: 80, priority: 'high' },
        { name: 'my-filters-keywords', keywords: ['vanity', 'mirror'],
          exclude_keywords: ['covers'], priority: 'high' }
      ]
    }
  });

  await api.loadRules();

  assert.equal(elements.matchMode.value, 'any');
  assert.equal(elements.minValue.value, 80);
  assert.equal(elements.keywords.value, 'vanity, mirror');
  assert.equal(elements.excludeKeywords.value, 'covers');
  // The multi-rule warning is for configurations this form did not write.
  assert.equal(elements.rulesBanner.textContent, '');
});
