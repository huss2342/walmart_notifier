import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

let helper;
try {
  const context = vm.createContext({ AbortController });
  vm.runInContext(fs.readFileSync(new URL('../extension/page-scroll.js', import.meta.url), 'utf8'), context);
  helper = context.ReviewerPageScroll;
} catch (error) { if (error.code !== 'ENOENT') throw error; }

function fixture({ height = 4800, viewport = 600, reduced = false, loading = false,
  freezeFrames = false } = {}) {
  let time = 0;
  let nextId = 0;
  const tasks = new Map();
  const listeners = new Map();
  const writes = [];
  const states = [];
  const document = { readyState: loading ? 'loading' : 'complete',
    documentElement: { scrollHeight: height, clientHeight: viewport }, body: { scrollHeight: height } };
  const window = { scrollY: 0, scrollX: 17, innerHeight: viewport,
    matchMedia: () => ({ matches: reduced }),
    scrollTo(options) {
      assert.equal(options.left, 17);
      assert.equal(options.behavior, 'instant');
      window.scrollY = options.top;
      writes.push({ at: time, top: options.top });
    },
    addEventListener(name, listener) { (listeners.get(name) || listeners.set(name, new Set()).get(name)).add(listener); },
    removeEventListener(name, listener) { listeners.get(name)?.delete(listener); } };
  const schedule = (callback, delay, kind) => {
    const id = ++nextId;
    tasks.set(id, { callback, at: time + delay, kind });
    return id;
  };
  let randomIndex = 0;
  const options = { window, document, canContinue: () => true, budgetMs: 8000,
    now: () => time, random: () => [0.1, 0.85, 0.35, 0.65, 0.2][randomIndex++ % 5],
    requestFrame: callback => schedule(callback, freezeFrames ? Infinity : 16, 'frame'),
    cancelFrame: id => tasks.delete(id),
    setTimer: (callback, delay) => schedule(callback, delay, 'timer'),
    clearTimer: id => tasks.delete(id), onState: state => states.push({ ...state }) };
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  async function advance(to) {
    await flush();
    while (true) {
      const next = [...tasks.entries()].filter(([, task]) => task.at <= to)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      const [id, task] = next;
      time = task.at;
      tasks.delete(id);
      task.callback(time);
      await flush();
    }
    time = to;
    await flush();
  }
  async function finish(promise) {
    let result;
    let error;
    promise.then(value => { result = value; }, value => { error = value; });
    for (let i = 0; i < 2000 && !result && !error; i++) {
      await flush();
      if (result || error) break;
      const next = Math.min(...[...tasks.values()].map(task => task.at));
      if (!Number.isFinite(next)) throw new Error('Scroll did not settle with bounded work.');
      await advance(next);
    }
    if (error) throw error;
    assert.ok(result, 'Scroll must settle.');
    return result;
  }
  return { options, window, document, writes, states, tasks, listeners, advance, finish,
    emit(name, event = { isTrusted: true }) { for (const listener of listeners.get(name) || []) listener(event); },
    grow(value) { document.documentElement.scrollHeight = document.body.scrollHeight = value; },
    activeListeners: () => [...listeners.values()].reduce((count, set) => count + set.size, 0) };
}

function clean(page) {
  assert.equal(page.tasks.size, 0, 'No frame or timer survives a settled run.');
  assert.equal(page.activeListeners(), 0, 'No temporary input listener survives a settled run.');
}

test('smooth downward scrolling covers the page in varied chunks and cleans up', async () => {
  const page = fixture();
  const result = await page.finish(helper.run(page.options));
  assert.equal(result.outcome, 'complete');
  assert.equal(page.window.scrollY, 4200);
  assert.equal(result.distance, 4200);
  assert.ok(result.steps >= 5);
  assert.ok(page.writes.length > result.steps * 3, 'Each smooth chunk has intermediate positions.');
  assert.ok(page.writes.every((write, index) => !index || write.top >= page.writes[index - 1].top));
  const stops = page.states.filter(state => state.phase === 'paused').map(state => state.distance);
  const chunks = stops.map((distance, index) => distance - (stops[index - 1] || 0)).filter(distance => distance > 0);
  assert.ok(new Set(chunks.map(distance => Math.round(distance))).size >= 3);
  const starts = page.states.filter(state => state.phase === 'scrolling');
  const durations = starts.map(start => page.states.find(state => state.phase === 'paused' &&
    state.elapsedMs > start.elapsedMs).elapsedMs - start.elapsedMs);
  assert.ok(new Set(durations).size >= 2, 'Chunks use different animation durations.');
  assert.ok(result.durationMs < 8000);
  clean(page);
});

test('reduced motion uses immediate downward chunks without animation frames', async () => {
  const page = fixture({ reduced: true });
  const result = await page.finish(helper.run(page.options));
  assert.equal(result.outcome, 'complete');
  assert.equal(page.window.scrollY, 4200);
  assert.equal(page.writes.length, result.steps);
  clean(page);
});

test('a tall catalogue reaches its pager within the default eight-second budget', async () => {
  const page = fixture({ height: 12000, viewport: 900 });
  const result = await page.finish(helper.run(page.options));
  assert.equal(result.outcome, 'complete');
  assert.equal(page.window.scrollY, 11100);
  assert.ok(result.durationMs < 8000);
  assert.ok(result.steps > 8);
  clean(page);
});

test('a lost role or generation stops before any later scroll write', async () => {
  const page = fixture();
  let current = true;
  const promise = helper.run({ ...page.options, canContinue: async () => current });
  await page.advance(90);
  assert.ok(page.writes.length > 0);
  current = false;
  const before = page.writes.length;
  const result = await page.finish(promise);
  assert.equal(result.outcome, 'cancelled');
  assert.equal(page.writes.length, before);
  clean(page);
});

test('trusted input immediately cancels an active animation while synthetic events do not', async () => {
  for (const type of ['wheel', 'touchstart', 'pointerdown', 'keydown']) {
    const page = fixture();
    const promise = helper.run(page.options);
    await page.advance(48);
    page.emit(type, { isTrusted: false });
    await page.advance(80);
    const before = page.writes.length;
    page.emit(type);
    const result = await page.finish(promise);
    assert.equal(result.outcome, 'cancelled', type);
    assert.equal(page.writes.length, before, type);
    clean(page);
  }
});

test('verification cancellation aborts even a suspended frame and clears its deadline', async () => {
  const page = fixture({ freezeFrames: true });
  const controller = new AbortController();
  const promise = helper.run({ ...page.options, signal: controller.signal });
  await page.advance(20);
  controller.abort();
  const result = await page.finish(promise);
  assert.equal(result.outcome, 'cancelled');
  assert.equal(page.writes.length, 0);
  clean(page);
});

test('the deadline bounds stalled frames, async guards, and an endlessly growing page', async () => {
  for (const mode of ['frame', 'guard', 'growth']) {
    const page = fixture({ freezeFrames: mode === 'frame', loading: mode === 'growth' });
    if (mode === 'growth') page.options.onState = state => {
      if (state.phase === 'paused') page.grow(page.document.body.scrollHeight + 500);
    };
    const result = await page.finish(helper.run({ ...page.options, budgetMs: 700,
      ...(mode === 'guard' ? { canContinue: () => new Promise(() => {}) } : {}) }));
    assert.equal(result.outcome, 'timeout', mode);
    assert.equal(result.durationMs, 700, mode);
    clean(page);
  }
});

test('bottom settling includes finite late page growth before declaring completion', async () => {
  const page = fixture({ height: 1200 });
  let grew = false;
  page.options.onState = state => {
    if (!grew && state.phase === 'paused' && page.window.scrollY >= 600) {
      grew = true;
      page.grow(2400);
    }
  };
  const result = await page.finish(helper.run(page.options));
  assert.equal(result.outcome, 'complete');
  assert.equal(page.window.scrollY, 1800);
  assert.equal(result.distance, 1800);
  clean(page);
});

test('the bottom remains observable while a delayed lazy-render batch arrives', async () => {
  const page = fixture({ height: 1200 });
  let scheduled = false;
  page.options.onState = state => {
    if (!scheduled && state.phase === 'paused' && page.window.scrollY >= 600) {
      scheduled = true;
      page.options.setTimer(() => page.grow(2400), 375);
    }
  };
  const result = await page.finish(helper.run(page.options));
  assert.equal(result.outcome, 'complete');
  assert.equal(page.window.scrollY, 1800);
  clean(page);
});

test('bottom settling includes height growth arriving after the former short sample window', async () => {
  const page = fixture({ height: 1200 });
  let scheduled = false;
  page.options.onState = state => {
    if (!scheduled && state.phase === 'paused' && page.window.scrollY >= 600) {
      scheduled = true;
      page.options.setTimer(() => page.grow(2400), 750);
    }
  };
  const result = await page.finish(helper.run(page.options));
  assert.equal(result.outcome, 'complete');
  assert.equal(page.window.scrollY, 1800);
  assert.equal(result.distance, 1800);
  const reachedBottom = page.writes.find(write => write.top === 1800).at;
  assert.ok(result.durationMs - reachedBottom >= 1200);
  clean(page);
});

test('a late card signature change restarts the full stable-bottom settle interval', async () => {
  const page = fixture({ height: 600 });
  let signature = 'first cards';
  page.options.setTimer(() => { signature = 'updated cards'; }, 750);
  const result = await page.finish(helper.run({ ...page.options, settleKey: () => signature }));
  assert.equal(result.outcome, 'complete');
  assert.ok(result.durationMs >= 1950, 'Changed cards must remain settled for another 1200 ms');
  assert.equal(page.writes.length, 0);
  clean(page);
});

test('a loading document must become ready and then settle before completing', async () => {
  const page = fixture({ height: 600, loading: true });
  page.options.setTimer(() => { page.document.readyState = 'interactive'; }, 750);
  const result = await page.finish(helper.run(page.options));
  assert.equal(result.outcome, 'complete');
  assert.ok(result.durationMs >= 1950, 'Loading time cannot count toward bottom stability');
  clean(page);
  const loading = fixture({ height: 600, loading: true });
  const timeout = await loading.finish(helper.run({ ...loading.options, budgetMs: 2500 }));
  assert.equal(timeout.outcome, 'timeout');
  assert.equal(timeout.durationMs, 2500);
  clean(loading);
});

test('a long page times out in place and a resumed pass reaches and settles its bottom', async () => {
  const page = fixture({ height: 18000 });
  const first = await page.finish(helper.run(page.options));
  assert.equal(first.outcome, 'timeout');
  assert.equal(first.durationMs, 8000);
  assert.ok(page.window.scrollY > 0 && page.window.scrollY < 17400);
  const resumedAt = page.window.scrollY;
  const before = page.writes.length;
  clean(page);
  const next = await page.finish(helper.run({ ...page.options, startAtTop: false }));
  assert.equal(next.outcome, 'complete');
  assert.equal(page.window.scrollY, 17400);
  assert.ok(page.writes.slice(before).every(write => write.top >= resumedAt));
  assert.equal(first.distance + next.distance, 17400);
  clean(page);
});

test('an unreadable settling signature fails closed and clears temporary work', async () => {
  const page = fixture({ height: 600 });
  const result = await page.finish(helper.run({ ...page.options,
    settleKey() { throw new Error('Unreadable cards'); } }));
  assert.equal(result.outcome, 'unavailable');
  clean(page);
});

test('settle duration is configurable and bounded while invalid settings fail closed', async () => {
  for (const [settleMs, minimum, maximum] of [[0, 0, 0], [250, 250, 550], [90_000, 5000, 5300]]) {
    const page = fixture({ height: 600 });
    const result = await page.finish(helper.run({ ...page.options, settleMs }));
    assert.equal(result.outcome, 'complete', String(settleMs));
    assert.ok(result.durationMs >= minimum && result.durationMs <= maximum, String(settleMs));
    clean(page);
  }
  for (const options of [{ settleMs: -1 }, { settleMs: NaN }, { settleMs: Infinity },
    { settleKey: 'not a callback' }]) {
    const page = fixture({ height: 600 });
    assert.equal((await page.finish(helper.run({ ...page.options, ...options }))).outcome, 'unavailable');
    assert.equal(page.writes.length, 0);
    clean(page);
  }
});

test('scrolling continues from the current position without an upward reset', async () => {
  const page = fixture({ height: 2400 });
  page.window.scrollY = 700;
  const result = await page.finish(helper.run(page.options));
  assert.equal(result.outcome, 'complete');
  assert.equal(result.distance, 1100);
  assert.ok(page.writes.every(write => write.top >= 700));
  clean(page);
});

test('an explicit fresh-page reset moves to the top once and still honors cancellation', async () => {
  const page = fixture({ height: 2400 });
  page.window.scrollY = 700;
  const result = await page.finish(helper.run({ ...page.options, startAtTop: true }));
  assert.equal(result.outcome, 'complete');
  assert.equal(page.writes[0].top, 0);
  assert.equal(page.writes.filter(write => write.top === 0).length, 1);
  assert.equal(result.distance, 1800);
  clean(page);
  const cancelled = fixture();
  cancelled.window.scrollY = 700;
  assert.equal((await cancelled.finish(helper.run({ ...cancelled.options,
    startAtTop: true, canContinue: () => false }))).outcome, 'cancelled');
  assert.equal(cancelled.writes.length, 0);
  clean(cancelled);
});

test('unusable geometry and throwing guards fail closed without lingering work', async () => {
  const invalid = fixture({ height: NaN, viewport: NaN });
  assert.equal((await invalid.finish(helper.run(invalid.options))).outcome, 'unavailable');
  assert.equal(invalid.writes.length, 0);
  clean(invalid);
  const guarded = fixture();
  assert.equal((await guarded.finish(helper.run({ ...guarded.options,
    canContinue: () => { throw new Error('private details'); } }))).outcome, 'cancelled');
  clean(guarded);
});

test('invalid clocks still return finite diagnostics without scheduling work', async () => {
  const page = fixture();
  const result = await page.finish(helper.run({ ...page.options, now: () => NaN }));
  assert.equal(result.outcome, 'unavailable');
  assert.ok(Number.isFinite(result.durationMs));
  assert.ok(Number.isFinite(result.distance));
  clean(page);
});
