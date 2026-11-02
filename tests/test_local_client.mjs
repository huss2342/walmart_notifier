import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import { reviewerLocks } from './fixtures/reviewer-locks.mjs';

const source = readFileSync(new URL('../extension/local-client.js', import.meta.url), 'utf8');
function fixture(fetchImpl, locks = reviewerLocks()) {
  let next = 0;
  const timers = new Map();
  const calls = [];
  const context = vm.createContext({ URL, AbortController, TextDecoder,
    navigator: { locks },
    setTimeout(callback, ms) { const id = ++next; timers.set(id, { callback, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch: async (url, init) => { calls.push({ url, init }); return fetchImpl(url, init); } });
  vm.runInContext(source, context);
  return { client: context.ReviewerLocalClient, timers, calls };
}

test('loopback JSON deadline includes a response body stalled after successful headers', async () => {
  const f = fixture(() => ({ ok: true, status: 200, text: () => new Promise(() => {}) }));
  const pending = f.client.json('http://127.0.0.1:8787/health', {}, { timeoutMs: 20 });
  const failure = assert.rejects(pending, error => error.name === 'AbortError');
  await Promise.resolve();
  assert.equal(f.timers.size, 1, 'Headers must not clear the body deadline');
  const [id, timer] = [...f.timers][0];
  f.timers.delete(id); timer.callback();
  await failure;
  assert.equal(f.calls[0].init.signal.aborted, true);
  assert.equal(f.timers.size, 0);
});

test('an unanswered fetch is bounded and a completed JSON request clears its deadline', async () => {
  const stalled = fixture(() => new Promise(() => {}));
  const pending = stalled.client.json('http://localhost:8787/health');
  const failure = assert.rejects(pending, error => error.name === 'AbortError');
  [...stalled.timers.values()][0].callback();
  await failure;
  const good = fixture(() => ({ ok: true, status: 200, text: async () => '{"ok":true}' }));
  assert.equal((await good.client.json('http://localhost:8787/health')).body.ok, true);
  assert.equal(good.timers.size, 0);
  assert.equal(good.calls[0].init.redirect, 'error');
});

test('streamed JSON size limits cancel the reader before consuming an unbounded body', async () => {
  let reads = 0;
  let cancelled = false;
  const f = fixture(() => ({ ok: true, status: 200, body: { getReader() { return {
    async read() { reads++; return { done: false, value: new Uint8Array(8) }; },
    async cancel() { cancelled = true; }
  }; } } }));
  await assert.rejects(f.client.json('http://localhost:8787/health', {}, { maxBytes: 10 }), /too large/);
  assert.equal(reads, 2);
  assert.equal(cancelled, true);
  assert.equal(f.timers.size, 0);
});

test('invalid JSON and malformed endpoints fail without sending credentials elsewhere', async () => {
  const f = fixture(() => ({ ok: true, status: 200, text: async () => '<html>not JSON</html>' }));
  for (const endpoint of ['https://outside.example/ingest', 'http://127.0.0.1:8787/other',
    'http://user:password@localhost:8787/ingest', 'http://localhost:8787/ingest?token=secret']) {
    assert.throws(() => f.client.route(endpoint, '/scanner-settings'));
  }
  assert.throws(() => f.client.route('http://localhost:8787/ingest', 'http://outside.example/settings'));
  await assert.rejects(f.client.json('https://outside.example/health', { headers: { 'X-Ingest-Token': 'private' } }));
  assert.equal(f.calls.length, 0);
  await assert.rejects(f.client.json('http://localhost:8787/health'), /invalid JSON/);
  assert.equal(f.timers.size, 0);
});

test('independent extension contexts share one settings transaction lock', async () => {
  const locks = reviewerLocks();
  const first = fixture(() => {}, locks);
  const second = fixture(() => {}, locks);
  const events = [];
  let release;
  const pending = first.client.settingsTransaction(async () => {
    events.push('first');
    await new Promise(resolve => { release = resolve; });
    events.push('first-complete');
  });
  await new Promise(resolve => setImmediate(resolve));
  const following = second.client.settingsTransaction(() => { events.push('second'); });
  await Promise.resolve();
  assert.deepEqual(events, ['first']);
  release();
  await Promise.all([pending, following]);
  assert.deepEqual(events, ['first', 'first-complete', 'second']);
  assert.equal(first.timers.size, 0);
  assert.equal(second.timers.size, 0);
});

test('unavailable cross-context locking rejects the mutation instead of silently downgrading', async () => {
  const f = fixture(() => {}, null);
  let ran = false;
  await assert.rejects(f.client.settingsTransaction(() => { ran = true; }), /coordination is unavailable/);
  assert.equal(ran, false);
  assert.equal(f.timers.size, 0);
});
