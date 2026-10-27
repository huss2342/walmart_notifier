import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const root = new URL('../extension/', import.meta.url);
function load({ enabled = true, permission = true, paused = true, failWrite = false,
  failRecovery = false, attachFails = false, onOpen = null } = {}) {
  const data = { verificationAssistEnabled: enabled };
  const check = { lastAt: 1000, pausedUntil: paused ? Date.now() + 60000 : 0,
    tabId: 11, challengedTabIds: [11] };
  const calls = [];
  let clock = Date.now();
  const timers = new Map();
  const alarms = new Map();
  let timerId = 0;
  let driverResult = { outcome: 'verified', holdMs: 2250, releaseReason: 'feedback' };
  let failAttach = attachFails;
  let currentEligibility;
  const tab = { id: 11, status: 'complete', url: 'https://www.walmart.com/blocked' };
  const api = {
    storage: { local: {
      async get(keys) { return Object.fromEntries(keys.filter(k => k in data).map(k => [k, data[k]])); },
      async set(value) {
        if (failWrite && value.verificationAssistBudget) throw new Error('write failed');
        Object.assign(data, JSON.parse(JSON.stringify(value)));
      }
    } },
    tabs: { async get(id) { return { ...tab, id }; } },
    permissions: { async contains() { return permission; } },
    debugger: { attach() {} }
  };
  api.alarms = { async create(name, info) { alarms.set(name, info); },
    async clear(name) { alarms.delete(name); } };
  const context = vm.createContext({ console, URL, Date: { now: () => clock },
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    VerificationChrome: { allowedUrl: raw => raw === 'https://www.walmart.com/blocked',
      async open(_api, id, eligible) {
        calls.push(['open', id]);
        currentEligibility = eligible;
        if (failAttach) throw new Error('busy debugger');
        if (onOpen) await onOpen();
        return { detach: async () => calls.push(['detach']) };
      } }
  });
  context.VerificationNative = {
    PERMISSIONS: { permissions: ['nativeMessaging', 'scripting'] },
    safeFallback: last => last?.cleanupConfirmed === true && last.pressSent === false &&
      last.errorStage === 'move' && last.errorReason === 'timeout',
    async open(_api, id, eligible) {
      calls.push(['native-open', id]); currentEligibility = eligible;
      if (failAttach) throw Object.assign(new Error('helper missing'), { nativeReason: 'host-unavailable' });
      return { detach: async () => calls.push(['native-detach']) };
    }
  };
  vm.runInContext(fs.readFileSync(new URL('verification-engine.js', root), 'utf8'), context);
  context.VerificationEngine.run = async (_driver, policy) => { calls.push(['run', policy]); return driverResult; };
  vm.runInContext(fs.readFileSync(new URL('verification-automation.js', root), 'utf8'), context);
  const makeController = () => context.ReviewerVerificationAutomation.create(api, {
    readCheck: async () => ({ ...check, challengedTabIds: [...check.challengedTabIds] }),
    onRecovered: async () => { calls.push(['recover']); if (failRecovery) throw new Error('offline'); }
  });
  const controller = makeController();
  return { data, calls, check, controller, api, tab, makeController, timers, alarms,
    eligible: () => currentEligibility(),
    now: () => clock, advance(ms) { clock += ms; },
    setAttachFailure(value) { failAttach = value; },
    result(value) { driverResult = value; } };
}

test('Chrome preflight waits for known foreground and ready tab states without spending attempts', async () => {
  for (const reason of ['inactive', 'frozen', 'discarded', 'unfocused', 'window-unavailable']) {
    const f = load();
    Object.assign(f.tab, { active: true, windowId: 4 });
    let focused = reason !== 'unfocused';
    let failWindow = reason === 'window-unavailable';
    f.api.windows = { async get() {
      if (failWindow) throw new Error('Window unavailable');
      return { focused };
    } };
    if (reason === 'inactive') f.tab.active = false;
    if (reason === 'frozen') f.tab.frozen = true;
    if (reason === 'discarded') f.tab.discarded = true;
    await f.controller.runPending();
    assert.deepEqual(f.calls, [], reason);
    assert.equal(f.data.verificationAssistBudget, undefined, reason);
    assert.equal(f.data.verificationAssistRetry, undefined, reason);
    assert.equal(f.data.verificationAssistLastResult, undefined, reason);
    assert.equal(f.data.verificationAssistStatus.code,
      ['inactive', 'unfocused'].includes(reason) ? 'waiting-foreground' : 'waiting-tab-ready', reason);
    const timer = [...f.timers.values()][0];
    assert.equal(timer.ms, 60000);
    assert.ok(f.alarms.get('verificationRetry').when > f.now());

    Object.assign(f.tab, { active: true, frozen: false, discarded: false });
    focused = true;
    failWindow = false;
    f.advance(timer.ms);
    await timer.fn();
    assert.equal(f.calls.filter(call => call[0] === 'run').length, 1, reason);
    assert.equal(f.data.verificationAssistBudget.attempts.length, 1, reason);
  }
});

test('waiting for foreground preserves the existing retry result and spent reservation', async () => {
  const f = load();
  f.result({ outcome: 'input-error', holdMs: 0, errorStage: 'move', errorReason: 'timeout',
    pressSent: false, cleanupConfirmed: true });
  await f.controller.runPending();
  f.advance(f.data.verificationAssistRetry.nextAt - f.now());
  const saved = JSON.stringify({ budget: f.data.verificationAssistBudget,
    retry: f.data.verificationAssistRetry, last: f.data.verificationAssistLastResult });
  f.tab.active = false;
  await f.makeController().runPending();
  assert.equal(f.data.verificationAssistStatus.code, 'waiting-foreground');
  assert.equal(f.calls.filter(call => call[0] === 'run').length, 1);
  assert.equal(JSON.stringify({ budget: f.data.verificationAssistBudget,
    retry: f.data.verificationAssistRetry, last: f.data.verificationAssistLastResult }), saved);
  f.tab.active = true;
  f.result({ outcome: 'verified', holdMs: 2250, releaseReason: 'feedback' });
  await f.controller.runPending();
  assert.equal(f.data.verificationAssistLastResult.attempt, 2);
  assert.equal(f.data.verificationAssistBudget.attempts.length, 2);
});

test('foreground lost while attaching detaches before persisting a Chrome attempt', async () => {
  let interrupt = true;
  const f = load({ onOpen: async () => { if (interrupt) f.tab.active = false; } });
  f.tab.active = true;
  await f.controller.runPending();
  assert.deepEqual(f.calls.map(call => call[0]), ['open', 'detach']);
  assert.equal(f.data.verificationAssistBudget, undefined);
  assert.equal(f.data.verificationAssistRetry, undefined);
  assert.equal(f.data.verificationAssistLastResult, undefined);
  assert.equal(f.data.verificationAssistStatus.code, 'waiting-foreground');
  interrupt = false;
  f.tab.active = true;
  await f.controller.runPending();
  assert.equal(f.calls.filter(call => call[0] === 'run').length, 1);
  assert.equal(f.data.verificationAssistBudget.attempts.length, 1);
});

test('Chrome preflight takes the foreground before input when the tab or window lost focus', async () => {
  for (const reason of ['inactive', 'unfocused']) {
    const f = load();
    const updates = [];
    Object.assign(f.tab, { active: true, windowId: 4 });
    f.tab.active = reason !== 'inactive';
    f.api.tabs.update = async (id, props) => {
      updates.push(['tab', id, props]);
      Object.assign(f.tab, props);
      return { ...f.tab, id };
    };
    let focused = reason !== 'unfocused';
    f.api.windows = {
      async get() { return { focused }; },
      async update(id, props) { updates.push(['window', id, props]); focused = props.focused; return { focused }; }
    };
    await f.controller.runPending();
    assert.deepEqual(f.calls.map(call => call[0]), ['open', 'run', 'recover'], reason);
    assert.deepEqual(updates.map(([kind, id, props]) => [kind, id, { ...props }]),
      reason === 'inactive'
        ? [['tab', 11, { active: true }]]
        : [['window', 4, { focused: true }]], reason);
    assert.equal(f.data.verificationAssistBudget.attempts.length, 1, reason);
    assert.equal(f.data.verificationAssistLastResult.attempt, 1, reason);
  }
});

test('a failed foreground attempt defers the Chrome attempt without spending a reservation', async () => {
  const f = load();
  Object.assign(f.tab, { active: false, windowId: 4 });
  f.api.tabs.update = async () => { throw new Error('tabs update unavailable'); };
  f.api.windows = { async get() { return { focused: false }; },
    async update() { throw new Error('windows update unavailable'); } };
  await f.controller.runPending();
  assert.deepEqual(f.calls, []);
  assert.equal(f.data.verificationAssistBudget, undefined);
  assert.equal(f.data.verificationAssistRetry, undefined);
  assert.equal(f.data.verificationAssistLastResult, undefined);
  assert.equal(f.data.verificationAssistStatus.code, 'waiting-foreground');
  const timer = [...f.timers.values()][0];
  assert.equal(timer.ms, 60000);
  assert.ok(f.alarms.get('verificationRetry').when > f.now());
});

test('foreground lost while attaching is re-taken when focus succeeds instead of detaching', async () => {
  const f = load({ onOpen: async () => { f.tab.active = false; } });
  f.tab.active = true;
  f.api.tabs.update = async (id, props) => { Object.assign(f.tab, props); return { ...f.tab, id }; };
  await f.controller.runPending();
  assert.deepEqual(f.calls.map(call => call[0]), ['open', 'run', 'recover']);
  assert.equal(f.data.verificationAssistBudget.attempts.length, 1);
  assert.equal(f.data.verificationAssistLastResult.attempt, 1);
});

test('native fallback starts on the next reserved attempt only after matching safe evidence', async () => {
  const f = load(); f.data.verificationNativeEnabled = true;
  f.result({ outcome: 'input-error', errorStage: 'move', errorReason: 'timeout', pressSent: false,
    cleanupConfirmed: true, holdMs: 0 });
  await f.controller.runPending();
  assert.equal(f.calls.some(c => c[0] === 'native-open'), false);
  f.advance(f.data.verificationAssistRetry.nextAt - f.now());
  f.tab.active = false;
  f.tab.frozen = true;
  f.tab.windowId = 4;
  f.api.windows = { async get() { return { focused: false }; } };
  f.result({ outcome: 'verified', transport: 'native', holdMs: 18000 });
  await f.controller.runPending();
  assert.equal(f.calls.filter(c => c[0] === 'native-open').length, 1);
  assert.equal(f.data.verificationAssistLastResult.attempt, 2);
  assert.equal(f.data.verificationAssistBudget.attempts.length, 2);
  assert.equal(f.data.verificationAssistLastResult.transport, 'native');
});

test('native fallback never consumes a reservation when helper connection fails', async () => {
  const f = load(); f.data.verificationNativeEnabled = true;
  f.result({ outcome: 'input-error', errorStage: 'move', errorReason: 'timeout', pressSent: false,
    cleanupConfirmed: true, holdMs: 0 });
  await f.controller.runPending();
  f.advance(f.data.verificationAssistRetry.nextAt - f.now()); f.setAttachFailure(true);
  await f.controller.runPending();
  assert.equal(f.data.verificationAssistBudget.attempts.length, 1);
  assert.equal(f.data.verificationAssistStatus.code, 'native-unavailable');
});

test('native enable cannot replay ambiguous input or another episode result', async () => {
  for (const otherEpisode of [false, true]) {
    const f = load(); f.data.verificationNativeEnabled = true;
    f.result({ outcome: 'input-error', errorStage: 'move', errorReason: 'timeout', pressSent: false,
      cleanupConfirmed: true, holdMs: 0 });
    await f.controller.runPending();
    f.advance(f.data.verificationAssistRetry.nextAt - f.now());
    if (otherEpisode) f.data.verificationAssistLastResult.key = 'other';
    else {
      f.data.verificationAssistLastResult.pressSent = true;
      f.data.verificationAssistRetry.terminal = true;
    }
    await f.controller.runPending();
    assert.equal(f.calls.some(c => c[0] === 'native-open'), false);
  }
});

test('an enabled relay attempts an already paused tab without a new detector message', async () => {
  const f = load();
  await f.controller.runPending();
  assert.deepEqual(f.calls.map(call => call[0]), ['open', 'run', 'recover']);
  assert.equal(f.data.verificationAssistLastResult.outcome, 'verified');
  assert.ok(f.data.verificationAssistBudget.seen.length);
});

test('a completed failed attempt retries after 10-20 seconds and stops at ten across restarts', async () => {
  const f = load();
  f.result({ outcome: 'needs-attention', holdMs: 15000, releaseReason: 'deadline' });
  for (let attempt = 1; attempt <= 10; attempt++) {
    await f.makeController().runPending();
    assert.equal(f.calls.filter(c => c[0] === 'run').length, attempt);
    assert.equal(f.data.verificationAssistLastResult.attempt, attempt);
    assert.equal(f.calls.filter(c => c[0] === 'run').at(-1)[1].fallbackHoldMs,
      Math.min(30000, 15000 + (attempt - 1) * 3000));
    if (attempt < 10) {
      assert.equal(f.data.verificationAssistStatus.code, 'retry-pending');
      const delay = f.data.verificationAssistRetry.nextAt - f.now();
      assert.ok(delay >= 10000 && delay <= 20000);
      await f.controller.runPending();
      assert.equal(f.calls.filter(c => c[0] === 'run').length, attempt);
      f.advance(delay);
    }
  }
  f.advance(3600001);
  await f.makeController().runPending();
  assert.equal(f.calls.filter(c => c[0] === 'run').length, 10);
  assert.equal(f.data.verificationAssistStatus.code, 'attempts-exhausted');
  assert.ok(f.check.pausedUntil > 0);
});

test('only an explicit manual retry can rearm the current input error without resetting budgets', async () => {
  const f = load();
  f.result({ outcome: 'input-error', holdMs: 0, errorStage: 'move', pressSent: false });
  await f.controller.runPending();
  const original = JSON.stringify(f.data.verificationAssistBudget);
  await f.controller.runPending();
  assert.equal(f.calls.filter(c => c[0] === 'run').length, 1);
  const response = await f.controller.retryCurrentCheck();
  assert.equal(response.ok, true);
  assert.equal(JSON.stringify(f.data.verificationAssistBudget), original);
  assert.equal(f.data.verificationAssistRetry.attempt, 1);
  assert.equal(f.data.verificationAssistRetry.terminal, false);
  f.advance(10000);
  f.result({ outcome: 'verified', holdMs: 4000, releaseReason: 'feedback' });
  await f.controller.runPending();
  assert.equal(f.calls.filter(c => c[0] === 'run').length, 2);
  assert.equal(f.data.verificationAssistLastResult.attempt, 2);
  assert.equal(f.data.verificationAssistBudget.attempts.length, 2);
});

test('a timed-out move with confirmed cleanup retries without manual intervention or erased reservations', async () => {
  const f = load();
  f.result({ outcome: 'input-error', holdMs: 0, errorStage: 'move', errorReason: 'timeout',
    pressSent: false, cleanupConfirmed: true });
  await f.controller.runPending();
  assert.equal(f.data.verificationAssistStatus.code, 'retry-pending');
  assert.equal(f.data.verificationAssistRetry.terminal, false);
  const timer = [...f.timers.values()][0];
  assert.ok(timer.ms >= 10000 && timer.ms <= 20000);
  await f.controller.runPending();
  assert.equal(f.calls.filter(c => c[0] === 'run').length, 1);
  f.advance(timer.ms);
  f.result({ outcome: 'verified', holdMs: 4000, releaseReason: 'feedback' });
  await timer.fn();
  assert.equal(f.calls.filter(c => c[0] === 'run').length, 2);
  assert.equal(f.data.verificationAssistLastResult.attempt, 2);
  assert.equal(f.data.verificationAssistLastResult.outcome, 'verified');
  assert.equal(f.data.verificationAssistBudget.attempts.length, 2);
  assert.equal(f.data.verificationAssistBudget.seen.length, 2);
});

test('safe move retries retain the ten-attempt cap across restarts and never rearm exhausted checks', async () => {
  const f = load();
  f.result({ outcome: 'input-error', holdMs: 0, errorStage: 'move', errorReason: 'timeout',
    pressSent: false, cleanupConfirmed: true });
  for (let attempt = 1; attempt <= 10; attempt++) {
    await f.makeController().runPending();
    assert.equal(f.calls.filter(c => c[0] === 'run').length, attempt);
    assert.equal(f.data.verificationAssistBudget.attempts.length, attempt);
    assert.equal(f.data.verificationAssistLastResult.attempt, attempt);
    if (attempt < 10) f.advance(f.data.verificationAssistRetry.nextAt - f.now());
  }
  assert.equal(f.data.verificationAssistStatus.code, 'attempts-exhausted');
  const exhaustedBudget = JSON.stringify(f.data.verificationAssistBudget);
  f.tab.active = false;
  await f.makeController().runPending();
  assert.equal(f.data.verificationAssistStatus.code, 'attempts-exhausted');
  f.advance(3600001);
  f.tab.active = true;
  f.data.verificationNativeEnabled = true;
  await f.makeController().runPending();
  assert.equal(f.calls.filter(c => c[0] === 'run').length, 10);
  assert.equal(JSON.stringify(f.data.verificationAssistBudget), exhaustedBudget);
});

test('uncertain press, cleanup and unclassified move failures remain terminal', async () => {
  for (const change of [{ pressSent: true }, { pressSent: undefined },
    { cleanupConfirmed: false }, { cleanupConfirmed: undefined },
    { cleanupErrorStage: 'detach' }, { errorStage: 'press' }, { errorReason: 'command-failed' }]) {
    const f = load();
    f.result({ outcome: 'input-error', holdMs: 0, errorStage: 'move', errorReason: 'timeout',
      pressSent: false, cleanupConfirmed: true, ...change });
    await f.controller.runPending();
    f.advance(60000);
    await f.makeController().runPending();
    assert.equal(f.calls.filter(c => c[0] === 'run').length, 1, JSON.stringify(change));
    assert.equal(f.data.verificationAssistRetry.terminal, true);
    assert.equal(f.data.verificationAssistStatus.code, 'input-error');
  }
});

test('manual retry refuses disabled, expired, mismatched, exhausted and in-progress checks', async () => {
  for (const reason of ['disabled', 'cleared', 'mismatched', 'exhausted', 'in-progress']) {
    const f = load();
    f.result({ outcome: 'input-error', holdMs: 0 });
    await f.controller.runPending();
    if (reason === 'disabled') f.data.verificationAssistEnabled = false;
    if (reason === 'cleared') f.check.pausedUntil = 0;
    if (reason === 'mismatched') f.check.lastAt += 1;
    if (reason === 'exhausted') f.data.verificationAssistRetry.attempt = 10;
    if (reason === 'in-progress') f.data.verificationAssistLastResult.outcome = 'starting';
    const original = JSON.stringify(f.data.verificationAssistRetry);
    assert.equal((await f.controller.retryCurrentCheck()).ok, false, reason);
    assert.equal(JSON.stringify(f.data.verificationAssistRetry), original, reason);
  }
});

test('a saved short retry timer runs the next attempt without a new challenge event', async () => {
  const f = load();
  f.result({ outcome: 'needs-attention', holdMs: 15000, releaseReason: 'deadline' });
  await f.controller.runPending();
  const timer = [...f.timers.values()][0];
  assert.ok(timer && timer.ms >= 10000 && timer.ms <= 20000);
  assert.ok(f.alarms.has('verificationRetry'));
  f.advance(timer.ms);
  f.result({ outcome: 'verified', holdMs: 4000, releaseReason: 'feedback' });
  await timer.fn();
  assert.equal(f.calls.filter(c => c[0] === 'run').length, 2);
  assert.equal(f.data.verificationAssistLastResult.outcome, 'verified');
  assert.equal(f.alarms.size, 0);
});

test('disabled, cleared, interrupted and ambiguous attempts never restart input', async () => {
  for (const reason of ['disabled', 'cleared', 'starting', 'input-error', 'cancelled']) {
    const f = load();
    f.result({ outcome: ['starting', 'input-error', 'cancelled'].includes(reason) ? reason : 'needs-attention',
      holdMs: 15000, releaseReason: 'deadline' });
    await f.controller.runPending();
    if (reason === 'disabled') f.data.verificationAssistEnabled = false;
    if (reason === 'cleared') f.check.pausedUntil = 0;
    f.advance(60000);
    await f.makeController().runPending();
    assert.equal(f.calls.filter(c => c[0] === 'run').length, 1, reason);
  }
});

test('the current legacy failed attempt can resume once without resetting its existing budget', async () => {
  const f = load();
  f.data.verificationAssistBudget = { attempts: [{ key: '11:1000', at: f.now() - 60000 }], seen: ['11:1000'] };
  f.data.verificationAssistLastResult = { at: f.now() - 40000, outcome: 'needs-attention',
    holdMs: 15010, releaseReason: 'deadline' };
  await f.controller.runPending();
  assert.equal(f.calls.filter(c => c[0] === 'run').length, 1);
  assert.equal(f.data.verificationAssistLastResult.attempt, 2);
  assert.equal(f.data.verificationAssistBudget.attempts.length, 2);
});

test('corrupt retry counters stop and all ten hourly reservations remain enforced', async () => {
  const corrupt = load();
  corrupt.data.verificationAssistRetry = { key: '11:1000', attempt: -1, terminal: false, nextAt: 0 };
  await corrupt.controller.runPending();
  assert.equal(corrupt.calls.length, 0);
  assert.equal(corrupt.data.verificationAssistStatus.code, 'invalid-budget');

  const capped = load();
  const attempts = Array.from({ length: 10 }, (_, index) => ({ key: `old:${index}`, at: capped.now() - 60000 - index * 10000 }));
  capped.data.verificationAssistBudget = { attempts, seen: attempts.map(a => a.key) };
  await capped.controller.runPending();
  assert.equal(capped.calls.length, 0);
  assert.equal(capped.data.verificationAssistStatus.code, 'cooldown');
  assert.ok(capped.alarms.get('verificationRetry').when > capped.now());
});

test('legacy recovery never assigns another tab’s last failure to this check', async () => {
  const f = load();
  f.data.verificationAssistBudget = { attempts: [
    { key: '11:1000', at: f.now() - 60000 }, { key: '22:1000', at: f.now() - 30000 }
  ], seen: ['11:1000', '22:1000'] };
  f.data.verificationAssistLastResult = { at: f.now() - 10000, outcome: 'needs-attention',
    holdMs: 15000, releaseReason: 'deadline' };
  await f.controller.runPending();
  assert.equal(f.calls.length, 0);
  assert.equal(f.data.verificationAssistStatus.code, 'already-attempted');
});

test('clearing the collector pause cancels an attached driver’s input eligibility', async () => {
  const f = load();
  await f.controller.runPending();
  assert.equal(await f.eligible(), true);
  f.check.pausedUntil = 0;
  assert.equal(await f.eligible(), false);
});
test('a relay with assistance off, no pause or no debugger permission never sends input', async () => {
  for (const setting of [{ enabled: false }, { paused: false }, { permission: false }]) {
    const f = load(setting);
    await f.controller.runPending();
    assert.equal(f.calls.length, 0);
    if (setting.permission === false) assert.equal(f.data.verificationAssistStatus.code, 'permission-required');
  }
});
test('duplicate triggers share one attempt and reservation survives a recreated controller', async () => {
  const f = load();
  await Promise.all([f.controller.runPending(), f.controller.runPending()]);
  await f.makeController().runPending();
  assert.equal(f.calls.filter(c => c[0] === 'run').length, 1);
  assert.equal(f.data.verificationAssistStatus.code, 'already-attempted');
});
test('a failed reservation is visible and blocks browser input', async () => {
  const f = load({ failWrite: true });
  await f.controller.runPending();
  assert.equal(f.calls.some(c => c[0] === 'run'), false);
  assert.deepEqual(f.calls, [['open', 11], ['detach']]);
  assert.equal(f.data.verificationAssistStatus.code, 'unavailable');
});
test('unavailable Chrome control retries after cooldown without spending the input reservation', async () => {
  const f = load({ attachFails: true });
  await f.controller.runPending();
  assert.equal(f.data.verificationAssistBudget, undefined);
  assert.equal(f.data.verificationAssistStatus.code, 'unavailable');
  await f.controller.runPending();
  assert.equal(f.calls.filter(c => c[0] === 'open').length, 1);
  assert.equal(f.data.verificationAssistStatus.code, 'retrying-control');
  f.setAttachFailure(false);
  f.advance(60000);
  await f.controller.runPending();
  assert.equal(f.calls.filter(c => c[0] === 'run').length, 1);
  assert.equal(f.data.verificationAssistLastResult.outcome, 'verified');
});
test('unsuccessful attempts preserve the collector pause and do not run the recovery hook', async () => {
  const f = load();
  f.result({ outcome: 'needs-attention', holdMs: 15000, releaseReason: 'deadline' });
  await f.controller.runPending();
  assert.equal(f.data.verificationAssistStatus.code, 'retry-pending');
  assert.equal(f.calls.some(c => c[0] === 'recover'), false);
  assert.ok(f.check.pausedUntil > Date.now());
});
test('a collector recovery error is distinct from failed browser input', async () => {
  const f = load({ failRecovery: true });
  await f.controller.runPending();
  assert.equal(f.data.verificationAssistLastResult.outcome, 'verified');
  assert.equal(f.data.verificationAssistStatus.code, 'collector-pending');
});
test('integrated options and manifest expose one relay control and include the solver modules', () => {
  const manifest = JSON.parse(fs.readFileSync(new URL('manifest.json', root), 'utf8'));
  const html = fs.readFileSync(new URL('options.html', root), 'utf8');
  const background = fs.readFileSync(new URL('background.js', root), 'utf8');
  assert.equal(manifest.name, 'Reviewer Item Relay');
  assert.equal(manifest.version, '2.24.1');
  assert.ok(manifest.permissions.includes('debugger'));
  for (const file of ['verification-engine.js', 'verification-driver.js', 'verification-automation.js']) {
    assert.ok(fs.existsSync(new URL(file, root)));
    assert.ok(background.includes(`'${file}'`));
  }
  assert.match(html, /id="enableVerificationAssist"/);
  assert.match(html, /id="disableVerificationAssist"/);
  assert.match(html, /id="verificationAssistStatus"/);
});
