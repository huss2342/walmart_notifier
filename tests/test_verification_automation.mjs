import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

function source(name) {
  const canonical = { engine: 'verification-engine', 'chrome-driver': 'verification-driver' };
  const path = canonical[name] ? `../extension/${canonical[name]}.js` : `../verification_helper/${name}.js`;
  return fs.readFileSync(new URL(path, import.meta.url), 'utf8');
}
function loadEngine() {
  const context = vm.createContext({ console });
  vm.runInContext(source('engine'), context);
  return context.VerificationEngine;
}
function fixture({ releaseAt = 3000, clearAt = 3500, failAt = Infinity,
  throwAt = Infinity, disableAt = Infinity, navigateAt = Infinity, moveAt = Infinity } = {}) {
  let clock = 0;
  let released = false;
  const calls = [];
  const page = () => ({ eligible: clock < navigateAt, challenge: !released || clock < clearAt,
    ready: released && clock >= clearAt, clock: 123, target: { x: clock >= moveAt ? 900 : 400, y: 300 } });
  return { calls, now: () => clock, driver: {
    now: () => clock,
    sleep: async ms => { clock += ms; },
    enabled: async () => clock < disableAt,
    page: async () => { if (clock >= throwAt) throw new Error('private-provider-error'); return page(); },
    feedback: async () => clock >= failAt ? 'failed' : clock >= releaseAt ? 'release' : 'unknown',
    down: async () => calls.push('down'),
    up: async () => { calls.push('up'); released = true; },
    detach: async () => calls.push('detach')
  } };
}

test('adaptive release follows differing feedback times and confirms stable content', async () => {
  for (const releaseAt of [1250, 4250, 9750]) {
    const f = fixture({ releaseAt, clearAt: releaseAt + 750 });
    const r = await loadEngine().run(f.driver);
    assert.equal(r.outcome, 'verified');
    assert.equal(r.releaseReason, 'feedback');
    assert.equal(r.holdMs, releaseAt);
    assert.ok(f.now() >= releaseAt + 1750);
    assert.deepEqual(f.calls, ['down', 'up', 'detach']);
  }
});
test('unreadable feedback uses a bounded fallback and remains unverified if still challenged', async () => {
  const f = fixture({ releaseAt: Infinity, clearAt: Infinity });
  const r = await loadEngine().run(f.driver);
  assert.equal(r.holdMs, 15000);
  assert.equal(r.releaseReason, 'deadline');
  assert.equal(r.outcome, 'needs-attention');
  assert.deepEqual(f.calls, ['down', 'up', 'detach']);
});

test('advancing readable progress can extend the hold beyond fifteen seconds', async () => {
  const f = fixture({ clearAt: 19000 });
  f.driver.feedback = async () => f.now() >= 18000 ? 'release' :
    { kind: 'progress', progress: f.now() / 18000 };
  const result = await loadEngine().run(f.driver);
  assert.equal(result.holdMs, 18000);
  assert.equal(result.releaseReason, 'feedback');
  assert.equal(result.outcome, 'verified');
  assert.equal(result.progressObserved, true);
});

test('stalled progress releases and continuously advancing progress has a thirty-second maximum', async () => {
  for (const stalled of [true, false]) {
    const f = fixture({ clearAt: Infinity });
    f.driver.feedback = async () => ({ kind: 'progress',
      progress: (stalled ? Math.min(f.now(), 2000) : f.now()) / 60000 });
    const result = await loadEngine().run(f.driver);
    assert.equal(result.holdMs, stalled ? 6000 : 30000);
    assert.equal(result.releaseReason, stalled ? 'stalled' : 'max-duration');
    assert.equal(result.outcome, 'needs-attention');
    assert.deepEqual(f.calls, ['down', 'up', 'detach']);
  }
});

test('partial semantic widget progress is preserved for adaptive timing', () => {
  const hint = loadEngine().feedback({ nodeName: 'DIV', nodeId: 11,
    attributes: ['role', 'progressbar', 'aria-valuenow', '25', 'aria-valuemax', '100'] });
  assert.equal(hint.kind, 'progress');
  assert.equal(hint.progress, 0.25);
});

test('retry fallback timing accepts a longer bounded hold without requiring readable feedback', async () => {
  for (const [requested, expected] of [[18000, 18000], [999999, 30000], [-1, 15000], [NaN, 15000]]) {
    const f = fixture({ releaseAt: Infinity, clearAt: Infinity });
    const result = await loadEngine().run(f.driver, { fallbackHoldMs: requested });
    assert.equal(result.holdMs, expected);
    assert.equal(result.outcome, 'needs-attention');
    assert.deepEqual(f.calls, ['down', 'up', 'detach']);
  }
});
test('disable, navigation, moved target and driver errors release without another press', async () => {
  for (const opts of [{ disableAt: 1000 }, { navigateAt: 1000 }, { moveAt: 1000 }, { throwAt: 1000 }]) {
    const f = fixture(opts);
    const r = await loadEngine().run(f.driver);
    assert.notEqual(r.outcome, 'verified');
    assert.deepEqual(f.calls, ['down', 'up', 'detach']);
  }
});
test('explicit failure feedback releases early and never retries', async () => {
  const f = fixture({ failAt: 1000, clearAt: Infinity });
  const r = await loadEngine().run(f.driver);
  assert.equal(r.outcome, 'needs-attention');
  assert.equal(r.holdMs, 1000);
  assert.equal(r.releaseReason, 'failed');
  assert.deepEqual(f.calls, ['down', 'up', 'detach']);
});
test('no supported target receives no press and a cleanup failure cannot turn into success', async () => {
  const f = fixture();
  f.driver.page = async () => ({ eligible: true, challenge: false, ready: true });
  assert.equal((await loadEngine().run(f.driver)).outcome, 'unsupported');
  assert.deepEqual(f.calls, ['detach']);
  const g = fixture();
  g.driver.up = async () => { g.calls.push('up'); throw new Error('secret'); };
  assert.equal((await loadEngine().run(g.driver)).outcome, 'input-error');
  assert.equal(g.calls.filter(x => x === 'down').length, 1);
  assert.equal(g.calls.at(-1), 'detach');
});
test('static release instructions do not cause immediate adaptive release', async () => {
  const f = fixture({ releaseAt: 0, clearAt: 15500 });
  const r = await loadEngine().run(f.driver);
  assert.equal(r.holdMs, 15000);
  assert.equal(r.releaseReason, 'deadline');
});
test('transient catalogue readiness followed by another challenge is never verified', async () => {
  const f = fixture({ releaseAt: 1000 });
  const original = f.driver.page;
  f.driver.page = async () => {
    const p = await original();
    if (f.now() >= 4000) { p.challenge = true; p.ready = false; }
    return p;
  };
  assert.equal((await loadEngine().run(f.driver)).outcome, 'needs-attention');
});

test('budget survives restarts, bounds hourly attempts and rejects malformed durable state', () => {
  const e = loadEngine();
  const now = 10_000_000;
  const first = e.reserve(null, 'doc-a:1', now);
  assert.equal(first.allowed, true);
  assert.equal(e.reserve(JSON.parse(JSON.stringify(first.state)), 'doc-a:1', now + 61000).allowed, false);
  assert.equal(e.reserve(first.state, 'doc-b:1', now + 59000).allowed, false);
  const second = e.reserve(first.state, 'doc-b:1', now + 60000);
  const third = e.reserve(second.state, 'doc-c:1', now + 120000);
  assert.equal(e.reserve(third.state, 'doc-d:1', now + 180000).allowed, false);
  assert.equal(e.reserve(third.state, 'doc-d:1', now + 3_600_001).allowed, true);
  for (const bad of [{}, { attempts: 'invalid' }, { attempts: [{ key: 'doc', at: NaN }] }]) {
    assert.equal(e.reserve(bad, 'doc-e:1', now).allowed, false);
  }
});

test('widget feedback ignores script text and identifies explicit progress completion', () => {
  const e = loadEngine();
  assert.equal(e.feedback({ nodeName: 'DIV', children: [
    { nodeName: 'SCRIPT', children: [{ nodeName: '#text', nodeValue: 'release success' }] }
  ] }).kind, 'unknown');
  const finished = e.feedback({ nodeName: 'DIV', nodeId: 11,
    attributes: ['role', 'progressbar', 'aria-valuenow', '100', 'aria-valuemax', '100'] });
  assert.equal(finished.kind, 'release');
  assert.equal(finished.nodeId, 11);
  assert.equal(e.feedback({ nodeName: 'DIV', nodeId: 12, children: [
    { nodeName: '#text', nodeValue: 'Please try again' }
  ] }).kind, 'failed');
});

function event() {
  const listeners = [];
  return { listeners, addListener: fn => listeners.push(fn),
    removeListener(fn) { const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1); } };
}
function chromeFixture() {
  const state = { tab: { id: 11, status: 'complete', url: 'https://www.walmart.com/reviews/claim-product' },
    page: { eligible: true, challenge: true, ready: false, clock: 123, target: { x: 400, y: 300 } },
    calls: [], attachError: false, downError: false, invisibleHint: false, failBudget: false,
    local: {}, timers: new Map(), nextTimer: 0, backendOnly: false, progressNow: 100 };
  const api = {
    tabs: { get: async () => ({ ...state.tab }), onUpdated: event() },
    debugger: {
      onDetach: event(),
      async attach(target, version) {
        state.calls.push(['attach', { ...target }, version]);
        if (state.attachError) throw new Error('another debugger is attached');
      },
      async detach(target) { state.calls.push(['detach', { ...target }]); },
      async sendCommand(target, method, params = {}) {
        state.calls.push([method, { ...params }]);
        assert.deepEqual({ ...target }, { tabId: 11 });
        if (method === 'Runtime.evaluate') return { result: { value: { ...state.page } } };
        if (method === 'DOM.getDocument') return { root: { nodeId: 1 } };
        if (method === 'DOM.querySelector') return { nodeId: 2 };
        if (method === 'DOM.describeNode') return { node: { nodeId: state.backendOnly ? 0 : 3,
          backendNodeId: 42, nodeName: 'DIV',
          attributes: ['role', 'progressbar', 'aria-valuenow', String(state.progressNow)] } };
        if (method === 'DOM.pushNodesByBackendIdsToFrontend') return { nodeIds: [3] };
        if (method === 'DOM.getBoxModel') return { model: { width: 100, height: 30 } };
        if (method === 'CSS.getComputedStyleForNode') return { computedStyle: [
          { name: 'display', value: state.invisibleHint ? 'none' : 'block' },
          { name: 'visibility', value: 'visible' }, { name: 'opacity', value: '1' }
        ] };
        if (method === 'Input.dispatchMouseEvent' && params.type === 'mousePressed' && state.downError) {
          throw new Error('private-input-failure');
        }
        return {};
      }
    },
    storage: { local: {
      async get(keys) { return Object.fromEntries(keys.filter(k => k in state.local).map(k => [k, state.local[k]])); },
      async set(values) {
        if (values.budget && state.failBudget) throw new Error('disk failure');
        Object.assign(state.local, JSON.parse(JSON.stringify(values)));
      }
    } },
    runtime: { onMessage: event(), openOptionsPage: async () => {} },
    action: { onClicked: event() }
  };
  const context = vm.createContext({ console, URL, chrome: api, performance,
    setTimeout(fn, ms) { const id = ++state.nextTimer; state.timers.set(id, { fn, ms }); return id; },
    clearTimeout(id) { state.timers.delete(id); }
  });
  vm.runInContext(source('engine') + '\n' + source('chrome-driver'), context);
  return { api, state, context, adapter: context.VerificationChrome };
}

test('Chrome adapter limits routes and never attaches to an unrelated or loading tab', async () => {
  const f = chromeFixture();
  for (const raw of ['https://www.walmart.com/reviews/claim-product', 'https://www.walmart.com/blocked?x=1']) {
    assert.equal(f.adapter.allowedUrl(raw), true);
  }
  for (const raw of ['http://www.walmart.com/blocked', 'https://www.walmart.com/orders',
    'https://evil.example/blocked', 'https://user@www.walmart.com/blocked',
    'https://www.walmart.com:9443/blocked', 'https://www.walmart.com/reviews/claim-product/subroute']) {
    f.state.tab.url = raw;
    await assert.rejects(f.adapter.open(f.api, 11, async () => true));
  }
  f.state.tab.url = 'https://www.walmart.com/blocked';
  f.state.tab.status = 'loading';
  await assert.rejects(f.adapter.open(f.api, 11, async () => true));
  assert.equal(f.state.calls.length, 0);
});

test('another debugger is never detached after attachment refusal', async () => {
  const f = chromeFixture();
  f.state.attachError = true;
  await assert.rejects(f.adapter.open(f.api, 11, async () => true));
  assert.deepEqual(f.state.calls.map(c => c[0]), ['attach']);
  assert.equal(f.api.tabs.onUpdated.listeners.length, 0);
  assert.equal(f.api.debugger.onDetach.listeners.length, 0);
});

test('slow Chrome move acknowledgement has a ten-second window; presses keep five', async () => {
  const f = chromeFixture();
  const send = f.api.debugger.sendCommand;
  let acknowledge;
  f.api.debugger.sendCommand = (target, method, params) =>
    method === 'Input.dispatchMouseEvent' && params.type === 'mouseMoved'
      ? new Promise(resolve => { acknowledge = resolve; }) : send(target, method, params);
  const driver = await f.adapter.open(f.api, 11, async () => true);
  const preparing = driver.down({ x: 400, y: 300 });
  await new Promise(resolve => setImmediate(resolve));
  assert.ok([...f.state.timers.values()].some(timer => timer.ms === 10000));
  assert.equal([...f.state.timers.values()].some(timer => timer.ms === 2000), false);
  acknowledge({});
  await preparing;
  await driver.up();
  await driver.detach();
});

test('pointer preparation timeout reports no press rather than a two-second hold', async () => {
  const f = chromeFixture();
  let clock = 0;
  f.context.performance = { now: () => clock };
  const send = f.api.debugger.sendCommand;
  f.api.debugger.sendCommand = (target, method, params) =>
    method === 'Input.dispatchMouseEvent' && params.type === 'mouseMoved'
      ? new Promise(() => {}) : send(target, method, params);
  const driver = await f.adapter.open(f.api, 11, async () => true);
  const running = f.context.VerificationEngine.run(driver);
  await new Promise(resolve => setImmediate(resolve));
  const timer = [...f.state.timers.values()].find(t => t.ms === 10000 || t.ms === 5000 || t.ms === 2000);
  assert.ok(timer);
  clock = timer.ms + 1;
  timer.fn();
  const result = await running;
  assert.equal(result.outcome, 'input-error');
  assert.equal(result.errorStage, 'move');
  assert.equal(result.errorReason, 'timeout');
  assert.equal(result.holdMs, 0);
  assert.equal(result.pressSent, false);
  assert.equal(result.pressConfirmed, false);
  assert.equal(result.cleanupConfirmed, true);
  assert.equal(f.state.calls.some(c => c[0] === 'Input.dispatchMouseEvent' &&
    c[1].type === 'mousePressed'), false);
});

test('a failed detach never confirms cleanup even when no press was sent', async () => {
  const f = chromeFixture();
  const send = f.api.debugger.sendCommand;
  f.api.debugger.sendCommand = (target, method, params) =>
    method === 'Input.dispatchMouseEvent' && params.type === 'mouseMoved'
      ? Promise.reject(new Error('move failed')) : send(target, method, params);
  f.api.debugger.detach = async () => { throw new Error('detach failed'); };
  const driver = await f.adapter.open(f.api, 11, async () => true);
  const result = await f.context.VerificationEngine.run(driver);
  assert.equal(result.pressSent, false);
  assert.equal(result.cleanupConfirmed, false);
  assert.equal(result.cleanupErrorStage, 'detach');
});

test('press acknowledgement failure records uncertainty and still confirms cleanup when possible', async () => {
  const f = chromeFixture();
  f.state.downError = true;
  const driver = await f.adapter.open(f.api, 11, async () => true);
  const result = await f.context.VerificationEngine.run(driver);
  assert.equal(result.errorStage, 'press');
  assert.equal(result.pressSent, true);
  assert.equal(result.pressConfirmed, false);
  assert.equal(result.releaseConfirmed, true);
  assert.equal(result.outcome, 'input-error');
});

test('press acknowledgement latency counts toward the hold limit', async () => {
  const f = fixture({ releaseAt: Infinity, clearAt: Infinity });
  let releasedAt;
  f.driver.down = async () => { f.calls.push('down'); await f.driver.sleep(3000); };
  const up = f.driver.up;
  f.driver.up = async () => { releasedAt = f.now(); await up(); };
  f.driver.inputState = () => ({ pressSent: true, pressConfirmed: true,
    pressStartedAt: 0, releaseStartedAt: releasedAt, releaseConfirmed: releasedAt != null });
  const result = await loadEngine().run(f.driver);
  assert.equal(releasedAt, 15000);
  assert.equal(result.holdMs, 15000);
});

test('Chrome transport presses/releases the exact tab and has an independent release watchdog', async () => {
  const f = chromeFixture();
  const d = await f.adapter.open(f.api, 11, async () => true);
  await d.down({ x: 400, y: 300 });
  const watchdog = [...f.state.timers.values()].find(t => t.ms === 30000);
  assert.ok(watchdog);
  watchdog.fn();
  await new Promise(resolve => setImmediate(resolve));
  await d.up();
  await d.detach();
  const input = f.state.calls.filter(c => c[0] === 'Input.dispatchMouseEvent').map(c => c[1]);
  assert.deepEqual(input.map(c => c.type), ['mouseMoved', 'mousePressed', 'mouseReleased']);
  assert.ok(input.every(c => c.x === 400 && c.y === 300));
  assert.equal(f.state.calls.at(-1)[0], 'detach');
  assert.equal(f.state.timers.size, 0);
});

test('navigation and target replacement immediately before pressing cancel input', async () => {
  for (const mode of ['navigation', 'target', 'disabled']) {
    const f = chromeFixture();
    const d = await f.adapter.open(f.api, 11, async () => mode !== 'disabled');
    if (mode === 'navigation') f.api.tabs.onUpdated.listeners[0](11, { status: 'loading' }, f.state.tab);
    if (mode === 'target') f.state.page.target.x = 800;
    await assert.rejects(d.down({ x: 400, y: 300 }));
    await d.detach();
    assert.equal(f.state.calls.some(c => c[0] === 'Input.dispatchMouseEvent'), false);
  }
});

test('readable shadow-tree hints must have visible layout before they affect release', async () => {
  const f = chromeFixture();
  const d = await f.adapter.open(f.api, 11, async () => true);
  assert.equal(await d.feedback(), 'release');
  f.state.progressNow = 25;
  const progress = await d.feedback();
  assert.equal(progress.kind, 'progress');
  assert.equal(progress.progress, 0.25);
  f.state.progressNow = 100;
  f.state.backendOnly = true;
  assert.equal(await d.feedback(), 'release');
  assert.ok(f.state.calls.some(c => c[0] === 'DOM.pushNodesByBackendIdsToFrontend'));
  f.state.invisibleHint = true;
  assert.equal(await d.feedback(), 'unknown');
  await d.detach();
  const methods = new Set(f.state.calls.map(c => c[0]));
  for (const forbidden of ['Network.getCookies', 'Runtime.callFunctionOn', 'DOM.setAttributeValue']) {
    assert.equal(methods.has(forbidden), false);
  }
});

function loadController() {
  const f = chromeFixture();
  const runFixture = fixture();
  let opened = 0;
  f.context.VerificationChrome.open = async () => { opened++; return runFixture.driver; };
  vm.runInContext(source('background').replace(/^importScripts\([^\n]+\);/, ''), f.context);
  f.dispatch = (message = { type: 'verification-observed', episode: 1 }, sender = {}) => new Promise(resolve => {
    f.api.runtime.onMessage.listeners[0](message, {
      tab: { ...f.state.tab }, frameId: 0, documentId: 'chrome-document-1', url: f.state.tab.url, ...sender
    }, resolve);
  });
  f.opened = () => opened;
  f.runFixture = runFixture;
  return f;
}
test('companion is inert until enabled and refuses forged frame/origin/document messages', async () => {
  const f = loadController();
  await f.dispatch();
  assert.equal(f.opened(), 0);
  f.state.local.enabled = true;
  for (const sender of [{ frameId: 1 }, { url: 'https://evil.example/blocked' }, { documentId: '../secret' }]) {
    assert.equal((await f.dispatch(undefined, sender)).ok, false);
  }
  assert.equal(f.opened(), 0);
});
test('durable reservation precedes input and duplicate detections cannot start another hold', async () => {
  const f = loadController();
  f.state.local.enabled = true;
  const beforeDown = f.runFixture.driver.down;
  f.runFixture.driver.down = async point => {
    assert.ok(f.state.local.budget.seen.includes('chrome-document-1:1'));
    await beforeDown(point);
  };
  await Promise.all([f.dispatch(), f.dispatch()]);
  await f.dispatch();
  assert.equal(f.opened(), 1);
  assert.equal(f.state.local.lastResult.outcome, 'verified');
  assert.doesNotMatch(JSON.stringify(f.state.local), /walmart|private|cookie|credential|target|document-1.*url/i);
});
test('a failed reservation write blocks all browser input', async () => {
  const f = loadController();
  f.state.local.enabled = true;
  f.state.failBudget = true;
  await f.dispatch();
  assert.equal(f.opened(), 0);
  assert.equal(f.state.local.lastResult.outcome, 'unavailable');
});

test('a controller restart preserves the completed episode reservation', async () => {
  const first = loadController();
  first.state.local.enabled = true;
  await first.dispatch();
  const restarted = loadController();
  restarted.state.local = JSON.parse(JSON.stringify(first.state.local));
  await restarted.dispatch();
  assert.equal(restarted.opened(), 0);
});

test('ambiguous press failure still attempts release and detaches its own session', async () => {
  const f = chromeFixture();
  f.state.downError = true;
  const d = await f.adapter.open(f.api, 11, async () => true);
  const r = await f.context.VerificationEngine.run(d);
  assert.equal(r.outcome, 'input-error');
  assert.deepEqual(f.state.calls.filter(c => c[0] === 'Input.dispatchMouseEvent').map(c => c[1].type),
    ['mouseMoved', 'mousePressed', 'mouseReleased']);
  assert.equal(f.state.calls.at(-1)[0], 'detach');
  assert.equal(f.state.timers.size, 0);
});

test('late attachment after timeout cleans up without issuing any input', async () => {
  const f = chromeFixture();
  let finish;
  f.api.debugger.attach = () => new Promise(resolve => { finish = resolve; });
  const opening = f.adapter.open(f.api, 11, async () => true);
  await new Promise(resolve => setImmediate(resolve));
  [...f.state.timers.values()].find(t => t.ms === 5000).fn();
  await assert.rejects(opening);
  finish();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(f.state.calls.map(c => c[0]), ['detach']);
  assert.equal(f.api.tabs.onUpdated.listeners.length, 0);
});

test('detector coalesces widget replacement and emits a new episode only after stable absence', async () => {
  let clock = 100000;
  let visible = true;
  let tick;
  const messages = [];
  const marker = { getClientRects: () => visible ? [1] : [] };
  const context = vm.createContext({
    Date: { now: () => clock }, setInterval: fn => { tick = fn; },
    document: { querySelectorAll: () => [marker] },
    getComputedStyle: () => ({ display: 'block', visibility: 'visible', opacity: '1' }),
    chrome: { runtime: { sendMessage: async message => { messages.push({ ...message }); } } }
  });
  vm.runInContext(source('detector'), context);
  async function advance(ms) { clock += ms; tick(); await Promise.resolve(); await Promise.resolve(); }
  await advance(2000);
  assert.equal(messages.at(-1).episode, 1);
  visible = false; await advance(2000);
  visible = true; await advance(1000); await advance(2000);
  assert.equal(messages.at(-1).episode, 1);
  visible = false; await advance(2000); await advance(6000);
  visible = true; await advance(2000); await advance(2000);
  assert.equal(messages.at(-1).episode, 2);
});
