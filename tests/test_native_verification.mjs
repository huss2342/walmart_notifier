import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

function event() {
  const listeners = new Set();
  return { addListener: f => listeners.add(f), removeListener: f => listeners.delete(f),
    fire: (...a) => { for (const f of [...listeners]) f(...a); } };
}
function fixture() {
  let now = 10000;
  let enabled = true;
  let page = { eligible: true, challenge: true, clock: 1, target: { x: 120, y: 80 },
    viewport: { width: 800, height: 600, dpr: 1 } };
  const calls = [];
  const state = { ready: true, reason: null, idleMs: 6000, phase: 'idle',
    pressSent: false, pressConfirmed: false, releaseConfirmed: false, holdMs: 0 };
  const api = {
    permissions: { contains: async () => true },
    tabs: { get: async () => ({ id: 11, windowId: 4, active: true,
      url: 'https://www.walmart.com/blocked', status: 'complete' }),
    update: async () => { calls.push('activate'); }, onUpdated: event(), onActivated: event(), onRemoved: event() },
    windows: { update: async () => { calls.push('focus'); }, get: async () => ({ focused: true }), onFocusChanged: event() },
    scripting: { executeScript: async () => [{ frameId: 0, documentId: 'doc1', result: page }] },
    storage: { local: { get: async () => ({ verificationNativeEnabled: enabled }) } }
  };
  let failure;
  const channel = {
    async rpc(op) {
      calls.push(op);
      if (failure?.op === op) throw failure.error;
      if (op === 'prepare') { state.phase = 'prepared'; return { ...state, token: 'test-token' }; }
      if (op === 'press') Object.assign(state, { phase: 'holding', pressSent: true, pressConfirmed: true });
      if (op === 'release') Object.assign(state, { phase: 'released', releaseConfirmed: true, holdMs: 2100 });
      return { ...state };
    },
    async close() { calls.push('close'); }
  };
  const ctx = vm.createContext({ URL, console, setTimeout, clearTimeout,
    crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' }, performance: { now: () => now },
    VerificationChrome: { allowedUrl: raw => raw === 'https://www.walmart.com/blocked', readPage() {} } });
  vm.runInContext(fs.readFileSync(new URL('../extension/verification-native.js', import.meta.url), 'utf8'), ctx);
  return { api, calls, state, native: ctx.VerificationNative,
    open: () => ctx.VerificationNative.open(api, 11, async () => enabled, { connect: async () => channel }),
    setEnabled: value => { enabled = value; }, setPage: value => { page = value; },
    fail: (op, error = new Error('disconnected')) => { failure = { op, error }; },
    advance: value => { now += value; } };
}

test('native adapter waits for idle before focus and uses its own input path', async () => {
  const f = fixture();
  const d = await f.open();
  assert.deepEqual(f.calls, ['probe', 'activate', 'focus', 'probe']);
  await d.down({ x: 120, y: 80 });
  assert.equal(d.inputState().pressConfirmed, true);
  await d.up();
  await d.detach();
  assert.equal(d.inputState().releaseConfirmed, true);
  assert.equal(d.inputState().transport, 'native');
  assert.equal(d.inputState().nativeHoldMs, 2100);
  assert.deepEqual(f.calls.filter(x => ['prepare', 'press', 'release'].includes(x)),
    ['prepare', 'press', 'release']);
  assert.equal(f.calls.at(-1), 'close');
});

test('busy user prevents native focus takeover and closes the helper', async () => {
  const f = fixture(); f.state.idleMs = 200;
  await assert.rejects(f.open());
  assert.deepEqual(f.calls, ['probe', 'close']);
});

test('changed target, document, disabled control or active tab prevents native press', async () => {
  for (const change of ['target', 'document', 'disabled', 'active']) {
    const f = fixture(); const d = await f.open();
    await d.page();
    if (change === 'target') f.setPage({ eligible: true, challenge: true, clock: 1,
      target: { x: 500, y: 80 }, viewport: { width: 800, height: 600, dpr: 1 } });
    if (change === 'document') f.api.scripting.executeScript = async () => [{ frameId: 0,
      documentId: 'doc2', result: { eligible: true, challenge: true, clock: 2,
        target: { x: 120, y: 80 }, viewport: { width: 800, height: 600, dpr: 1 } } }];
    if (change === 'disabled') f.setEnabled(false);
    if (change === 'active') f.api.tabs.get = async () => ({ id: 11, windowId: 4, active: false,
      url: 'https://www.walmart.com/blocked', status: 'complete' });
    await assert.rejects(d.down({ x: 120, y: 80 }), undefined, change);
    await d.detach();
    assert.equal(f.calls.includes('press'), false, change);
  }
});

test('lost press acknowledgement is uncertain and cleanup still requests release', async () => {
  const f = fixture(); const d = await f.open();
  f.fail('press');
  await assert.rejects(d.down({ x: 120, y: 80 }));
  assert.equal(d.inputState().pressSent, true);
  assert.equal(d.inputState().pressConfirmed, false);
  await d.detach();
  assert.equal(f.calls.includes('release'), true);
  assert.equal(f.calls.at(-1), 'close');
});

test('host activity abort cancels the engine and preserves actual host duration', async () => {
  const f = fixture(); const d = await f.open(); await d.down({ x: 120, y: 80 });
  Object.assign(f.state, { phase: 'aborted', reason: 'user-input', releaseConfirmed: true, holdMs: 750 });
  assert.equal(await d.enabled(), false);
  assert.equal(d.inputState().nativeReason, 'user-input');
  assert.equal(d.inputState().nativeHoldMs, 750);
  await d.detach();
});

test('native hold releases on tab switch, window focus loss, navigation and removal', async () => {
  for (const change of ['activated', 'focus', 'navigation', 'removed', 'poll-tab', 'poll-window']) {
    const f = fixture(); const d = await f.open(); await d.down({ x: 120, y: 80 });
    if (change === 'activated') f.api.tabs.onActivated.fire({ tabId: 12, windowId: 4 });
    if (change === 'focus') f.api.windows.onFocusChanged.fire(5);
    if (change === 'navigation') f.api.tabs.onUpdated.fire(11, { status: 'loading' });
    if (change === 'removed') f.api.tabs.onRemoved.fire(11);
    if (change === 'poll-tab') f.api.tabs.get = async () => ({ id: 11, windowId: 4,
      status: 'complete', active: false, url: 'https://www.walmart.com/blocked' });
    if (change === 'poll-window') f.api.windows.get = async () => ({ focused: false });
    assert.equal(await d.enabled(), false, change);
    await d.detach();
    assert.equal(d.inputState().releaseConfirmed, true, change);
    assert.equal(f.calls.filter(op => op === 'release').length, 1, change);
  }
});

test('fallback selection requires matching explicit safe input evidence', () => {
  const n = fixture().native;
  const noPress = { outcome: 'input-error', errorStage: 'move', errorReason: 'timeout',
    pressSent: false, cleanupConfirmed: true };
  assert.equal(n.safeFallback(noPress), true);
  assert.equal(n.safeFallback({ ...noPress, cleanupConfirmed: undefined }), false);
  assert.equal(n.safeFallback({ ...noPress, pressSent: true }), false);
  assert.equal(n.safeFallback({ ...noPress, cleanupErrorStage: 'detach' }), false);
  const held = { outcome: 'needs-attention', releaseReason: 'deadline', holdMs: 15000,
    pressSent: true, pressConfirmed: true, releaseConfirmed: true, cleanupConfirmed: true };
  assert.equal(n.safeFallback(held), true);
  assert.equal(n.safeFallback({ ...held, releaseConfirmed: false }), false);
  assert.equal(n.safeFallback({ ...held, outcome: 'cancelled' }), false);
});

function bridgeFixture({ forged = false, malformed = false, failResponse = false } = {}) {
  const f = fixture();
  const onConnect = event();
  const sent = [], removed = [];
  const state = { ...f.state };
  let port;
  Object.assign(f.api, {
    runtime: { id: 'owner', onConnect, getURL: path => `chrome-extension://owner/${path}` }
  });
  f.api.tabs.create = async spec => {
    const makePort = bad => ({ name: `reviewer-native:${new URL(spec.url).searchParams.get('session')}`,
      sender: { id: bad ? 'forged' : 'owner', url: spec.url, tab: { id: 90, windowId: 4 } },
      onMessage: event(), onDisconnect: event(),
      disconnect() { sent.push(bad ? 'forged-disconnect' : 'disconnect'); this.onDisconnect.fire(); },
      postMessage(message) {
        sent.push(message);
        queueMicrotask(() => {
          if (message.type === 'start') this.onMessage.fire({ type: 'ready' });
          else this.onMessage.fire({ response: { v: 1, id: message.request.id,
            ok: !failResponse, error: failResponse ? 'user-busy' : undefined,
            state: malformed ? { phase: 'holding' } : state } });
        });
      } });
    if (forged) queueMicrotask(() => onConnect.fire(makePort(true)));
    port = makePort(false);
    // Return the tab before a document can connect. Both event orders are used
    // in production, so this deliberately exercises the candidate-before-tab race.
    queueMicrotask(() => onConnect.fire(port));
    return { id: 90, windowId: 4 };
  };
  f.api.tabs.remove = async id => removed.push(id);
  return { ...f, sent, removed, port: () => port };
}

test('private bridge binds only its exact document and cleans up its owned tab', async () => {
  const f = bridgeFixture({ forged: true });
  const channel = await f.native.connectBridge(f.api, 4);
  const state = await channel.rpc('probe');
  assert.equal(state.phase, 'idle');
  await channel.close();
  assert.ok(f.sent.includes('forged-disconnect'));
  assert.deepEqual(f.removed, [90]);
});

test('bridge rejects malformed native replies and keeps safe error-state evidence', async () => {
  for (const kind of ['malformed', 'failResponse']) {
    const f = bridgeFixture({ [kind]: true });
    const channel = await f.native.connectBridge(f.api, 4);
    await assert.rejects(channel.rpc('press', { token: 'a'.repeat(48) }, 'press'), error => {
      assert.equal(error.verificationStage, 'press');
      if (kind === 'failResponse') {
        assert.equal(error.nativeReason, 'user-busy');
        assert.equal(error.nativeState.pressSent, false);
      }
      return true;
    });
    await channel.close();
  }
});

test('disconnected bridge refuses further RPC and still removes only its own tab', async () => {
  const f = bridgeFixture();
  const channel = await f.native.connectBridge(f.api, 4);
  f.port().onDisconnect.fire();
  await assert.rejects(channel.rpc('press', {}, 'press'));
  await channel.close();
  assert.deepEqual(f.removed, [90]);
});
