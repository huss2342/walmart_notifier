import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// The page snapshot uploader (extension/page-snapshots.js, 2.32.7): bounded,
// batched, never a Walmart request; a backend without the route pauses it.
const source = fs.readFileSync(new URL('../extension/page-snapshots.js', import.meta.url), 'utf8');

function uploader(respond) {
  const context = vm.createContext({});
  vm.runInContext(`${source}\nthis.Snapshots = ReviewerPageSnapshots;`, context);
  let now = 1_000;
  const timers = [];
  const bodies = [];
  const api = context.Snapshots.create({
    upload: async body => {
      bodies.push(JSON.parse(JSON.stringify(body)));
      return respond(body, bodies.length);
    },
    setTimer: (fn, delay) => { timers.push({ fn, delay }); return timers.length; },
    clearTimer: () => {},
    now: () => now
  });
  return { api, bodies, timers, Snapshots: context.Snapshots, advance: ms => { now += ms; },
    async fire() { const timer = timers.shift(); timer?.fn(); await new Promise(resolve => setImmediate(resolve)); } };
}

const record = n => ({ kind: 'read', source: 'fast', at: n, lane: 'value80', page: 1, outcome: 'items', items: [] });

test('records are batched on a timer and sent once, in order', async () => {
  const u = uploader(() => ({ ok: true, status: 200 }));
  for (let n = 1; n <= 3; n += 1) u.api.add(record(n));
  assert.equal(u.bodies.length, 0, 'nothing is sent synchronously');
  assert.equal(u.timers.length, 1);
  assert.equal(u.timers[0].delay, u.Snapshots.FLUSH_MS);
  await u.fire();
  assert.deepEqual(u.bodies, [{ schemaVersion: 1, records: [record(1), record(2), record(3)] }]);
  assert.equal(await u.api.flush(), false, 'nothing left');
  assert.equal(u.api.status().sent, 3);
});

test('a full batch flushes at once; the buffer is bounded and the loss is reported', async () => {
  let status = 503;
  const u = uploader(() => ({ ok: status === 200, status }));
  for (let n = 1; n <= u.Snapshots.MAX_BUFFER + 25; n += 1) u.api.add(record(n));
  assert.deepEqual(u.timers.map(timer => timer.delay), [u.Snapshots.FLUSH_MS, 0], 'a full batch goes at once');
  await u.fire(); // the replaced 20 s timer (a no-op: it was cleared in the worker)
  await u.fire();
  assert.equal(u.api.status().buffered, u.Snapshots.MAX_BUFFER, 'kept, bounded, for the retry');
  status = 200;
  await u.api.flush();
  const last = u.bodies.at(-1);
  assert.equal(last.dropped, 25);
  assert.equal(last.records.length, u.Snapshots.MAX_BATCH);
  assert.equal(last.records[0].at, 26, 'the oldest were dropped');
  while (u.api.status().buffered) await u.api.flush();
  assert.equal(u.bodies.at(-1).dropped, undefined, 'a loss is reported once');
});

test('a backend without the route (HTTP 404) pauses the log for 10 minutes', async () => {
  const u = uploader(() => { const error = new Error('not json'); error.status = 404; throw error; });
  u.api.add(record(1));
  await u.fire();
  assert.equal(u.api.status().buffered, 0);
  u.api.add(record(2));
  assert.equal(u.api.status().buffered, 0, 'ignored while paused');
  u.advance(10 * 60_000);
  u.api.add(record(3));
  assert.equal(u.api.status().buffered, 1);
});

test('a refused batch (HTTP 400) is never resent; a network error keeps the records', async () => {
  let reply = () => { throw new Error('network'); };
  const u = uploader(() => reply());
  u.api.add(record(1));
  await u.fire();
  assert.equal(u.api.status().buffered, 1);
  assert.equal(u.timers.at(-1).delay, 60_000, 'retried a minute later');
  reply = () => ({ ok: false, status: 400 });
  await u.api.flush();
  assert.equal(u.api.status().buffered, 0);
  reply = () => ({ ok: true, status: 200 });
  u.api.add(record(2));
  await u.api.flush();
  assert.deepEqual(u.bodies.at(-1), { schemaVersion: 1, dropped: 1, records: [record(2)] });
});

test('the worker loads the uploader before the fast reads and posts only to the local notifier', () => {
  const background = fs.readFileSync(new URL('../extension/background.js', import.meta.url), 'utf8');
  assert.match(background, /importScripts\([^\n]*'page-snapshots\.js', 'fast-reads\.js'/);
  assert.match(background, /ReviewerLocalClient\.route\(checkedEndpoint\(endpoint\), '\/page-snapshots'\)/);
  assert.match(background, /snapshot: record => pageSnapshots\.add\(record\)/);
  assert.match(background, /post: postAuxiliaryPage/);
  assert.doesNotMatch(source, /fetch\(|walmart\.com/);
});
