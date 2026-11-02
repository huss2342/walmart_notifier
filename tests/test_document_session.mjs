import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../extension/document-session.js', import.meta.url), 'utf8');

function fixture() {
  const timers = new Map();
  let next = 0;
  let href = 'https://www.walmart.com/reviews/claim-product?page=10';
  const context = vm.createContext({});
  vm.runInContext(source, context);
  const session = context.ReviewerDocumentSession.create({ href: () => href,
    setTimer(callback, ms) { const id = ++next; timers.set(id, { callback, ms }); return id; },
    clearTimer(id) { timers.delete(id); } });
  Object.assign(session.role, { active: true, tabId: 1, generation: 9 });
  return { session, timers, navigate(value) { href = value; } };
}

test('revoked intake never blocks or releases an operation from the next generation', () => {
  const { session } = fixture();
  const previous = session.begin();
  assert.equal(session.begin(), null);
  session.revoke();
  session.role.generation = 10;
  const current = session.begin();
  assert.ok(current);
  assert.equal(session.finish(previous), false);
  assert.equal(session.begin(), null);
  assert.equal(session.finish(current), true);
  assert.ok(session.begin());
});

test('an unanswered request rejects at its deadline and clears its tracked timer', async () => {
  const { session, timers } = fixture();
  const pending = session.request(() => new Promise(() => {}), 15_000);
  const failure = assert.rejects(pending, /did not reply/);
  const [id, timer] = [...timers][0];
  assert.equal(timer.ms, 15_000);
  timers.delete(id);
  timer.callback();
  await failure;
  assert.equal(timers.size, 0);
});

test('revocation rejects old requests immediately and ignores their late results', async () => {
  const { session, timers } = fixture();
  let release;
  const pending = session.request(() => new Promise(resolve => { release = resolve; }), 15_000);
  const failure = assert.rejects(pending, /cancelled/);
  session.revoke();
  await failure;
  release({ ok: true });
  await Promise.resolve();
  assert.equal(timers.size, 0);
});

test('a changed document scope cannot accept an old response even with the same generation', async () => {
  const { session, navigate } = fixture();
  let release;
  const pending = session.request(() => new Promise(resolve => { release = resolve; }), 15_000);
  const failure = assert.rejects(pending, /cancelled/);
  navigate('https://www.walmart.com/reviews/claim-product?page=11');
  release({ ok: true });
  await failure;
});

test('disposal cancels pending work and refuses new document operations', async () => {
  const { session, timers } = fixture();
  const pending = session.request(() => new Promise(() => {}), 15_000);
  const failure = assert.rejects(pending, /cancelled/);
  session.dispose();
  await failure;
  assert.equal(timers.size, 0);
  assert.equal(session.begin(), null);
  await assert.rejects(session.request(() => ({ ok: true }), 15_000), /cancelled/);
});
