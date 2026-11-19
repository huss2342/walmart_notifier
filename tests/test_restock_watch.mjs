import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../extension/restock-watch.js', import.meta.url), 'utf8');

function watch(saved = {}, clock = { now: 1_000_000 }) {
  const store = { ...saved };
  const events = [];
  // A settable clock for the timing fields.
  const FakeDate = class extends Date { static now() { return clock.now; } };
  const context = vm.createContext({ Promise, Map, Object, Number, Array, Set, Math, Date: FakeDate });
  vm.runInContext(`${source}\nthis.Watch = ReviewerRestockWatch;`, context);
  const storage = { async get(key) { return { [key]: store[key] }; }, async set(values) { Object.assign(store, values); } };
  const diagnostic = (event, details) => events.push({ event, ...JSON.parse(JSON.stringify(details)) });
  return { api: context.Watch.create(storage, { diagnostic }), store, events, clock, Watch: context.Watch };
}

const offer = (id, value, available) => ({ item_id: `ip-${id}`, value_usd: value, available });
// A data read (Walmart's own stock fields). Not a variant listing unless asked:
// variants are steady-checked from their first hold (see the variant tests).
const data = (id, value, available, extra = {}) => ({ ...offer(id, value, available),
  stock: { source: 'data', v2: available ? 'in' : 'out', status: available ? 'in' : 'out',
    oos: !available, variant: false, ...extra } });
const variant = (id, value, available) => data(id, value, available, { variant: true });
const where = { lane: 'value80', page: 2, requestId: 'a'.repeat(32) };
const plain = value => JSON.parse(JSON.stringify(value));
const stages = events => events.map(e => [e.stage, e.stockState]);

test('a return is held on its first in-stock read and announced only when a later read agrees', async () => {
  const { api, store, clock, Watch } = watch();
  assert.deepEqual(plain(await api.mark([offer(1, 149.99, true)])), [offer(1, 149.99, true)], 'never out: untouched');
  await api.mark([offer(1, 149.99, false), offer(2, 20, false)]);
  assert.deepEqual(Object.keys(store.restockWatch), ['ip-1'], 'only $80+ offers are remembered');
  const [first] = await api.mark([offer(1, 149.99, true)]);
  assert.equal(first.restockHold, true, 'first in-stock read is held back');
  assert.equal(first.restocked, undefined);
  clock.now += Watch.CONFIRM_MS - 1;
  assert.equal((await api.mark([offer(1, 149.99, true)]))[0].restockHold, true, 'too soon to confirm');
  clock.now += 1;
  const [second] = await api.mark([offer(1, 149.99, true)]);
  assert.equal(second.restocked, true, 'confirmed CONFIRM_MS later');
  assert.equal(second.restockHold, undefined);
  assert.equal((await api.mark([offer(2, 20, true)]))[0].restocked, undefined, 'cheap offers never');
});

// Nov 12, 17:32 CST: ip-20689613492 ($269.99) had been out of stock for days.
// price_high page 1 showed it IN_STOCK for one read (every stock field agreeing,
// the rest of the page unchanged) and OUT_OF_STOCK 10.6 s later. It alerted.
test('the Wisairt blip: in stock for one read, out 10.6 s later, is never announced', async () => {
  const { api, events, store, clock } = watch();
  const page1 = { lane: 'value', page: 1 };
  await api.mark([data(20689613492, 269.99, false)], page1);
  clock.now += 10 * 60_000;
  const [held] = await api.mark([data(20689613492, 269.99, true)], page1);
  assert.equal(held.restockHold, true);
  clock.now += 10_600;
  const [out] = await api.mark([data(20689613492, 269.99, false)], page1);
  assert.equal(out.restocked, undefined);
  assert.deepEqual(stages(events), [['returned', 'in-stock'], ['rejected', 'out-of-stock']]);
  assert.equal(events[1].sinceReturnMs, 10_600);
  assert.deepEqual(Object.keys(store.restockWatch), ['ip-20689613492'], 'still watched for a real return');
  // A second blip later is held again, not confirmed by the first one.
  clock.now += 5 * 60_000;
  assert.equal((await api.mark([data(20689613492, 269.99, true)], page1))[0].restockHold, true);
});

// Nov 12, 17:03 CST: ip-20755219869 ($199.99) read IN_STOCK once on the
// min_price=79 list, OUT_OF_STOCK on the next read. It alerted.
test('the Mitonos blip on the $80+ list is rejected by the 10 s re-read', async () => {
  const { api, events, clock } = watch();
  await api.mark([data(20755219869, 199.99, false)], where);
  clock.now += 20 * 60_000;
  await api.mark([data(20755219869, 199.99, true)], where);
  clock.now += 10_000;
  assert.equal((await api.mark([data(20755219869, 199.99, false)], where))[0].restocked, undefined);
  assert.deepEqual(stages(events), [['returned', 'in-stock'], ['rejected', 'out-of-stock']]);
});

test('a hold that is never confirmed expires, and a later blip cannot confirm it', async () => {
  const { api, events, clock, Watch } = watch();
  await api.mark([data(1, 199.99, false)], where);
  await api.mark([data(1, 199.99, true)], where); // blip 1; its page is never re-sent as in stock
  clock.now += Watch.PENDING_MS + 1;
  const [blip2] = await api.mark([data(1, 199.99, true)], where);
  assert.equal(blip2.restockHold, true, 'a fresh hold, not a confirmation');
  assert.deepEqual(stages(events), [['returned', 'in-stock'], ['rejected', 'absent'], ['returned', 'in-stock']]);
});

test('a held offer missing from its own single-list page is rejected', async () => {
  const { api, events, clock } = watch();
  await api.mark([data(1, 199.99, false)], where);
  await api.mark([data(1, 199.99, true)], where);
  clock.now += 10_000;
  await api.mark([data(2, 150, true)], { lane: 'value', page: 2 }); // another lane: no verdict
  await api.mark([data(2, 150, true)], { lane: 'value80', page: 3 }); // another page: no verdict
  assert.equal(events.length, 1);
  await api.mark([data(2, 150, true)], where);
  assert.deepEqual(events.slice(1).map(e => [e.stage, e.stockState, e.valueCents, e.sinceReturnMs]),
    [['rejected', 'absent', 19999, 10_000]]);
});

test('awaiting() names the pages that must be re-sent while a return is held', async () => {
  const { api, clock } = watch();
  assert.equal(api.awaiting([offer(1, 199.99, true)]), false);
  await api.mark([offer(1, 199.99, false)], where);
  await api.mark([offer(1, 199.99, true)], where);
  assert.equal(api.awaiting([offer(1, 199.99, false)]), true, 'in or out of stock: its page must be sent');
  assert.equal(api.awaiting([offer(2, 199.99, true)]), false);
  clock.now += 10_000;
  await api.mark([offer(1, 199.99, true)], where);
  assert.equal(api.awaiting([offer(1, 199.99, true)]), false, 'confirmed: nothing awaited');
});

// Nov 19, 11:05 CST: a backend restart refused the upload that confirmed 28
// returns (HTTP 503). A confirmed return is now flagged until an upload carrying
// it is acknowledged, however long the backend is away (up to RETRY_MS).
test('a confirmed return stays flagged until an upload carrying it is acknowledged', async () => {
  const { api, clock, Watch } = watch({ restockWatch: { 'ip-1': 1 } });
  await api.mark([offer(1, 149.99, true)]);
  clock.now += 10_000;
  assert.equal((await api.mark([offer(1, 149.99, true)]))[0].restocked, true); // Upload refused here (503).
  clock.now += 6 * 60_000; // Longer than the old 5-minute flag.
  assert.equal((await api.mark([offer(1, 149.99, true)]))[0].restocked, true, 'the retry still says restocked');
  await api.ack(['ip-1']);
  assert.equal((await api.mark([offer(1, 149.99, true)]))[0].restocked, undefined, 'acknowledged: no longer');
  await api.mark([offer(1, 149.99, false)]);
  assert.equal((await api.mark([offer(1, 149.99, true)]))[0].restockHold, true, 'a new return is held again');
  // Unacknowledged, the flag ends after RETRY_MS: an older restock is no news.
  clock.now += 10_000;
  assert.equal((await api.mark([offer(1, 149.99, true)]))[0].restocked, true);
  clock.now += Watch.RETRY_MS;
  assert.equal((await api.mark([offer(1, 149.99, true)]))[0].restocked, undefined);
});

test('an unacknowledged return is re-sent on its own, survives a restart and stops when out again', async () => {
  const { api, store, clock, Watch } = watch({ restockWatch: { 'ip-1': 1, 'ip-2': 1 } });
  const page = { lane: 'value80', page: 2 };
  await api.mark([{ ...data(1, 149.99, true), title: 'Desk' }, data(2, 99, true)], page);
  clock.now += 10_000;
  const confirmed = await api.mark([{ ...data(1, 149.99, true), title: 'Desk' }, data(2, 99, true)], page);
  assert.deepEqual(confirmed.map(item => item.restocked), [true, true]); // Upload fails: worker stops.
  assert.deepEqual(plain(await api.retryable()), [], 'its own page upload goes first');
  clock.now += 15_000;
  // A new worker (extension reload) reads the same local storage.
  const restarted = watch(store, clock);
  const retry = plain(await restarted.api.retryable());
  assert.deepEqual(retry, [{ item_id: 'ip-1', value_usd: 149.99, available: true, title: 'Desk', restocked: true },
    { item_id: 'ip-2', value_usd: 99, available: true, restocked: true }], 'flagged, without stock evidence');
  assert.deepEqual(plain(await restarted.api.retryable()), [], 'at most once per 15 s');
  await restarted.api.mark([data(2, 99, false)], page); // Out again: a blip, never re-sent.
  await restarted.api.ack(['ip-1']);
  clock.now += 15_000;
  assert.deepEqual(plain(await restarted.api.retryable()), []);
  assert.deepEqual(Object.keys(restarted.store.restockReturns), ['ip-1']);
  assert.equal(restarted.store.restockReturns['ip-1'].acked, true);
  clock.now += Watch.RETRY_MS;
  assert.deepEqual(plain(await watch(restarted.store, clock).api.retryable()), []);
});

test('the watch survives the worker restarting', async () => {
  const { api, clock } = watch({ restockWatch: { 'ip-7': 1 } });
  assert.equal((await api.mark([offer(7, 99, true)]))[0].restockHold, true);
  clock.now += 10_000;
  assert.equal((await api.mark([offer(7, 99, true)]))[0].restocked, true);
});

test('return and confirmation log time away, stock signals and where each was read', async () => {
  const { api, events, clock } = watch();
  await api.mark([data(20755219869, 199.99, false)], where);
  assert.deepEqual(events, [], 'going out of stock is not logged');
  clock.now += 3 * 60_000;
  await api.mark([data(20755219869, 199.99, false)], where);
  clock.now += 60_000;
  await api.mark([data(20755219869, 199.99, true)], where);
  const signals = { stockSource: 'data', signalV2: 'in', signalStatus: 'in', variant: false, signalOos: false,
    conflict: false };
  assert.deepEqual(events, [{ event: 'restock-watch', itemId: 'ip-20755219869', valueCents: 19999,
    lane: 'value80', page: 2, requestId: 'a'.repeat(32), stage: 'returned', stockState: 'in-stock',
    awayMs: 4 * 60_000, sinceOutMs: 60_000, outReads: 2, ...signals }]);
  clock.now += 10_000;
  await api.mark([data(20755219869, 199.99, true)], { ...where, requestId: 'b'.repeat(32) });
  assert.deepEqual(events[1], { event: 'restock-watch', itemId: 'ip-20755219869', valueCents: 19999,
    lane: 'value80', page: 2, requestId: 'b'.repeat(32), stage: 'confirmed', stockState: 'in-stock',
    sinceReturnMs: 10_000, reads: 2, awayMs: 4 * 60_000 + 10_000, ...signals });
});

test('disagreeing Walmart stock fields on the return are flagged as a conflict', async () => {
  const { api, events } = watch({ restockWatch: { 'ip-7': 1 } });
  await api.mark([data(7, 99, true, { status: 'out', oos: true })], where);
  assert.equal(events[0].conflict, true);
  assert.deepEqual([events[0].signalV2, events[0].signalStatus, events[0].signalOos], ['in', 'out', true]);
});

test('a card read logs only the visible out-of-stock text, never data fields', async () => {
  const { api, events } = watch({ restockWatch: { 'ip-7': 1 } });
  await api.mark([{ ...offer(7, 99, true), stock: { source: 'card', oos: false } }], { lane: 'home', page: 3 });
  assert.equal(events[0].stockSource, 'card');
  assert.equal(events[0].signalOos, false);
  assert.equal('signalV2' in events[0], false);
  assert.equal('conflict' in events[0], false);
});

test('a confirmed restock is followed: steady reads, then a drop back out is logged', async () => {
  const { api, events, clock, store } = watch();
  await api.mark([data(1, 199.99, false)], where);
  await api.mark([data(1, 199.99, true)], where);
  clock.now += 10_000;
  await api.mark([data(1, 199.99, true)], where);
  for (let read = 0; read < 2; read += 1) {
    clock.now += 40_000;
    await api.mark([data(1, 199.99, true)], where);
  }
  clock.now += 40_000;
  await api.mark([data(1, 199.99, false)], where);
  assert.deepEqual(events.map(e => [e.stage, e.stockState, e.reads]),
    [['returned', 'in-stock', undefined], ['confirmed', 'in-stock', 2], ['followup', 'in-stock', 1],
      ['followup', 'in-stock', 2], ['followup', 'out-of-stock', 2]]);
  assert.deepEqual(Object.keys(store.restockWatch), ['ip-1'], 'watched again, so the next return can alert');
});

test('a confirmed offer missing from its own single-list page is logged as absent', async () => {
  const { api, events, clock } = watch();
  await api.mark([data(1, 199.99, false)], where);
  await api.mark([data(1, 199.99, true)], where);
  clock.now += 10_000;
  await api.mark([data(1, 199.99, true)], where);
  clock.now += 40_000;
  await api.mark([data(2, 150, true)], where);
  assert.deepEqual(events.slice(2).map(e => [e.stage, e.stockState, e.sinceReturnMs]),
    [['followup', 'absent', 40_000]]);
});

test('a diagnostic failure never stops intake or the restock flag', async () => {
  const clock = { now: 1 };
  const FakeDate = class extends Date { static now() { return clock.now; } };
  const context = vm.createContext({ Promise, Map, Object, Number, Array, Set, Math, Date: FakeDate });
  vm.runInContext(`${source}\nthis.Watch = ReviewerRestockWatch;`, context);
  const storage = { async get() { return { restockWatch: { 'ip-7': 1 } }; }, async set() {} };
  const api = context.Watch.create(storage, { diagnostic() { throw new Error('boom'); } });
  await api.mark([data(7, 99, true)], where);
  clock.now += 10_000;
  assert.equal((await api.mark([data(7, 99, true)], where))[0].restocked, true);
});

// Nov 19, 03:29 CST (2.32.3): ip-20755219869 ($199.99) was held on price_high
// page 1, "confirmed" 9.15 s later by an incidental read of the min_price=79 list
// (page 1), and price_high page 1's own re-read 1.3 s after that showed it out of
// stock. It alerted. The exact read sequence from the browser diagnostics:
test('the Nov 19 Mitonos leak: an incidental read of another lane never confirms a return', async () => {
  const { api, events, store, clock } = watch({ restockWatch: { 'ip-20755219869': 1 } });
  const page1 = { lane: 'value', page: 1 };
  const [first] = await api.mark([data(20755219869, 199.99, true)], page1); // 03:29:44.411
  assert.equal(first.restockHold, true);
  clock.now += 9_150; // 03:29:53.561, the min_price=79 list
  const [incidental] = await api.mark([data(20755219869, 199.99, true)], { lane: 'value80', page: 1 });
  assert.equal(incidental.restockHold, true, 'another lane only starts its own check');
  assert.equal(incidental.restocked, undefined);
  assert.equal(api.awaiting([offer(20755219869, 199.99, false)]), true, 'every page holding it is still sent');
  clock.now += 1_334; // 03:29:54.895, price_high page 1's own re-read
  const [out] = await api.mark([data(20755219869, 199.99, false)], page1);
  assert.equal(out.restocked, undefined);
  assert.deepEqual(stages(events), [['returned', 'in-stock'], ['rejected', 'out-of-stock']]);
  assert.equal(events[1].sinceReturnMs, 10_484);
  assert.equal(store.restockBlips['ip-20755219869'].length, 1, 'the blip is remembered');
});

test('another page\'s in-stock read starts that page\'s own check, which can confirm', async () => {
  const { api, events, clock } = watch({ restockWatch: { 'ip-1': 1 } });
  await api.mark([data(1, 199.99, true)], { lane: 'value', page: 1 });
  clock.now += 4_000;
  await api.mark([data(1, 199.99, true)], { lane: 'value80', page: 1 });
  clock.now += 6_000; // 10 s after the first read, 6 s after the value80 one
  assert.equal((await api.mark([data(1, 199.99, true)], { lane: 'value80', page: 1 }))[0].restockHold, true,
    'too soon for that page');
  clock.now += 3_000;
  const [confirmed] = await api.mark([data(1, 199.99, true)], { lane: 'value80', page: 1 });
  assert.equal(confirmed.restocked, true);
  assert.deepEqual(events.map(e => [e.stage, e.lane, e.page, e.sinceReturnMs, e.reads]),
    [['returned', 'value', 1, undefined, undefined], ['confirmed', 'value80', 1, 13_000, 3]]);
});

test('an out-of-stock read from any lane rejects a held return', async () => {
  const { api, events, clock } = watch({ restockWatch: { 'ip-1': 1 } });
  await api.mark([data(1, 199.99, true)], { lane: 'value', page: 1 });
  clock.now += 4_000;
  await api.mark([data(1, 199.99, false)], { lane: 'value80', page: 3 });
  clock.now += 6_000;
  const [again] = await api.mark([data(1, 199.99, true)], { lane: 'value', page: 1 });
  assert.equal(again.restockHold, true, 'a fresh hold, not a confirmation');
  assert.deepEqual(stages(events), [['returned', 'in-stock'], ['rejected', 'out-of-stock'], ['returned', 'in-stock']]);
});

test('searches and departments: the same lane and page number of another target never confirms', async () => {
  const { api, events, clock } = watch({ restockWatch: { 'ip-1': 1 } });
  await api.mark([data(1, 199.99, true)], { lane: 'requests', page: 1, targetIndex: 1 });
  clock.now += 10_000;
  assert.equal((await api.mark([data(1, 199.99, true)], { lane: 'requests', page: 1, targetIndex: 2 }))[0]
    .restockHold, true);
  assert.equal((await api.mark([data(1, 199.99, true)], { lane: 'departments', page: 1, targetId: 'c'.repeat(32) }))[0]
    .restockHold, true);
  clock.now += 1_000;
  assert.equal((await api.mark([data(1, 199.99, true)], { lane: 'requests', page: 1, targetIndex: 1 }))[0]
    .restocked, true);
  assert.deepEqual(stages(events), [['returned', 'in-stock'], ['confirmed', 'in-stock']]);
});

test('a held offer gone from one of its pages waits for the others; gone from all, it is rejected', async () => {
  const { api, events, clock } = watch({ restockWatch: { 'ip-1': 1 } });
  await api.mark([data(1, 199.99, true)], { lane: 'value', page: 1 });
  await api.mark([data(1, 199.99, true)], { lane: 'value80', page: 1 });
  clock.now += 10_000;
  await api.mark([data(2, 150, true)], { lane: 'value', page: 1 });
  assert.equal(events.length, 1, 'still on the min_price=79 list');
  await api.mark([data(2, 150, true)], { lane: 'value80', page: 1 });
  assert.deepEqual(stages(events), [['returned', 'in-stock'], ['rejected', 'absent']]);
});

// Three blips in a day: held then out (twice), and announced then out within 5 min.
async function flicker(api, clock, where1 = { lane: 'value', page: 1 }) {
  for (let blip = 0; blip < 2; blip += 1) {
    await api.mark([data(1, 199.99, true)], where1);
    clock.now += 10_000;
    await api.mark([data(1, 199.99, false)], where1);
    clock.now += 60 * 60_000;
  }
  await api.mark([data(1, 199.99, true)], where1);
  clock.now += 10_000;
  assert.equal((await api.mark([data(1, 199.99, true)], where1))[0].restocked, true, 'two blips: still 10 s');
  clock.now += 90_000;
  await api.mark([data(1, 199.99, false)], where1);
  clock.now += 60 * 60_000;
}

test('an offer that blipped three times in a day must stay in stock on one page for 60 s', async () => {
  const { api, events, store, clock, Watch } = watch({ restockWatch: { 'ip-1': 1 } });
  await flicker(api, clock);
  assert.equal(store.restockBlips['ip-1'].length, 3);
  events.length = 0;
  const page1 = { lane: 'value', page: 1 };
  await api.mark([data(1, 199.99, true)], page1);
  assert.equal(events[0].count, 3, 'the return logs its recent blips');
  for (let read = 1; read < 6; read += 1) {
    clock.now += 10_000;
    assert.equal((await api.mark([data(1, 199.99, true)], page1))[0].restockHold, true, `held at ${read * 10} s`);
  }
  clock.now += Watch.STEADY_MS - 50_000;
  assert.equal((await api.mark([data(1, 199.99, true)], page1))[0].restocked, true, 'confirmed at 60 s');
  assert.deepEqual(events.map(e => [e.stage, e.sinceReturnMs, e.reads, e.count]),
    [['returned', undefined, undefined, 3], ['confirmed', 60_000, 7, 3]]);
});

test('a steady check needs several reads and restarts after a gap of more than 30 s', async () => {
  const { api, events, clock } = watch({ restockWatch: { 'ip-1': 1 } });
  await flicker(api, clock);
  events.length = 0;
  const page4 = { lane: 'value80', page: 4 };
  await api.mark([data(1, 199.99, true)], page4);
  clock.now += 61_000; // One re-read after 61 s proves nothing about the minute between.
  assert.equal((await api.mark([data(1, 199.99, true)], page4))[0].restockHold, true);
  for (let read = 0; read < 4; read += 1) {
    clock.now += 12_000;
    assert.equal((await api.mark([data(1, 199.99, true)], page4))[0].restockHold, true);
  }
  clock.now += 12_000; // 60 s after the restart, its 6th read
  assert.equal((await api.mark([data(1, 199.99, true)], page4))[0].restocked, true);
  assert.deepEqual([events.at(-1).sinceReturnMs, events.at(-1).reads], [61_000 + 60_000, 6]);
});

test('a flickering offer out again during its steady minute is rejected and never announced', async () => {
  const { api, events, clock } = watch({ restockWatch: { 'ip-1': 1 } });
  await flicker(api, clock);
  events.length = 0;
  await api.mark([data(1, 199.99, true)], { lane: 'value', page: 1 });
  for (let read = 0; read < 4; read += 1) {
    clock.now += 10_000;
    await api.mark([data(1, 199.99, true)], { lane: 'value', page: 1 });
  }
  clock.now += 2_000;
  await api.mark([data(1, 199.99, false)], { lane: 'value80', page: 1 });
  assert.deepEqual(stages(events), [['returned', 'in-stock'], ['rejected', 'out-of-stock']]);
});

test('blips older than a day, or an announced return lasting 5+ minutes, do not count', async () => {
  const { api, events, store, clock, Watch } = watch({ restockWatch: { 'ip-1': 1 } });
  const page1 = { lane: 'value', page: 1 };
  for (let blip = 0; blip < 3; blip += 1) {
    await api.mark([data(1, 199.99, true)], page1);
    clock.now += 10_000;
    await api.mark([data(1, 199.99, false)], page1);
    clock.now += 60_000;
  }
  clock.now += 24 * 60 * 60_000;
  await api.mark([data(1, 199.99, true)], page1);
  clock.now += Watch.CONFIRM_MS;
  assert.equal((await api.mark([data(1, 199.99, true)], page1))[0].restocked, true, 'old blips: 9 s again');
  clock.now += Watch.BLIP_AFTER_CONFIRM_MS;
  await api.mark([data(1, 199.99, false)], page1);
  await api.mark([data(1, 199.99, true)], page1);
  clock.now += 10_000;
  await api.mark([data(1, 199.99, false)], page1); // A fresh blip saves the history.
  assert.deepEqual(Object.keys(store.restockBlips), ['ip-1']);
  assert.equal(store.restockBlips['ip-1'].length, 1, 'expired blips are dropped; the 5-minute return is no blip');
  assert.deepEqual(stages(events).slice(-5), [['returned', 'in-stock'], ['confirmed', 'in-stock'],
    ['followup', 'out-of-stock'], ['returned', 'in-stock'], ['rejected', 'out-of-stock']]);
});

test('the blip history survives the worker restarting', async () => {
  const now = 50 * 60 * 60_000;
  const { api, events, clock } = watch({ restockWatch: { 'ip-1': 1 },
    restockBlips: { 'ip-1': [now - 3 * 60 * 60_000, now - 2 * 60 * 60_000, now - 60 * 60_000], 'ip-2': ['x'] } },
  { now });
  await api.mark([data(1, 199.99, true)], { lane: 'value', page: 1 });
  clock.now += 10_000;
  assert.equal((await api.mark([data(1, 199.99, true)], { lane: 'value', page: 1 }))[0].restockHold, true);
  assert.equal(events[0].count, 3);
});

// Nov 17-19: 33 of the 34 blips were variant listings (71 variant returns, 33
// blips; 184 other returns, 1 blip). A variant is steady-checked from its first hold.
test('a variant offer must stay in stock on one page for 60 s from its first hold', async () => {
  const { api, events, clock, Watch } = watch({ restockWatch: { 'ip-1': 1 } });
  const page1 = { lane: 'value', page: 1 };
  await api.mark([variant(1, 199.99, true)], page1);
  clock.now += 10_000;
  assert.equal((await api.mark([variant(1, 199.99, true)], page1))[0].restockHold, true, 'no 10 s confirmation');
  for (let read = 2; read < 6; read += 1) {
    clock.now += 10_000;
    assert.equal((await api.mark([variant(1, 199.99, true)], page1))[0].restockHold, true, `held at ${read * 10} s`);
  }
  clock.now += Watch.STEADY_MS - 50_000;
  assert.equal((await api.mark([variant(1, 199.99, true)], page1))[0].restocked, true, 'confirmed at 60 s');
  assert.deepEqual(events.map(e => [e.stage, e.sinceReturnMs, e.reads, e.variant]),
    [['returned', undefined, undefined, true], ['confirmed', 60_000, 7, true]]);
});

test('a variant blip that the 10 s re-read would have confirmed is never announced', async () => {
  const { api, events, store, clock } = watch({ restockWatch: { 'ip-1': 1 } });
  const page1 = { lane: 'value', page: 1 };
  await api.mark([variant(1, 199.99, true)], page1);
  clock.now += 10_000;
  await api.mark([variant(1, 199.99, true)], page1);
  clock.now += 25_000; // Gone again 35 s after it came back.
  assert.equal((await api.mark([variant(1, 199.99, false)], page1))[0].restocked, undefined);
  assert.deepEqual(stages(events), [['returned', 'in-stock'], ['rejected', 'out-of-stock']]);
  assert.equal(store.restockBlips['ip-1'].length, 1);
});

test('a variant first seen on a card is steady-checked once a data read names it', async () => {
  const { api, clock } = watch({ restockWatch: { 'ip-1': 1 } });
  const card = { lane: 'home', page: 3 };
  await api.mark([{ ...offer(1, 199.99, true), stock: { source: 'card', oos: false } }], card);
  const page1 = { lane: 'value', page: 1 };
  await api.mark([variant(1, 199.99, true)], page1);
  clock.now += 10_000;
  assert.equal((await api.mark([variant(1, 199.99, true)], page1))[0].restockHold, true);
  clock.now += 10_000;
  assert.equal((await api.mark([{ ...offer(1, 199.99, true), stock: { source: 'card', oos: false } }], card))[0]
    .restockHold, true, 'the card page too now needs the steady minute');
});

test('the blip history and the watch survive an extension reload; very old watch entries are dropped', async () => {
  const now = 30 * 24 * 60 * 60_000;
  const old = now - 14 * 24 * 60 * 60_000 - 1;
  const { api, store, clock } = watch({ restockWatch: { 'ip-1': now - 60_000, 'ip-9': old } }, { now });
  await api.mark([data(1, 199.99, true)], { lane: 'value', page: 1 });
  clock.now += 10_000;
  await api.mark([data(1, 199.99, false)], { lane: 'value', page: 1 }); // A blip, saved.
  assert.equal(store.restockBlips['ip-1'].length, 1);
  // The reloaded worker reads the same local storage: the blip still counts.
  const reloaded = watch(store, clock);
  clock.now += 60_000;
  await reloaded.api.mark([data(1, 199.99, true)], { lane: 'value', page: 1 });
  assert.equal(reloaded.events[0].count, 1);
  assert.equal((await reloaded.api.mark([data(9, 99, true)], { lane: 'value', page: 1 }))[0].restockHold, undefined,
    'a forgotten offer is left to the backend rule');
});
