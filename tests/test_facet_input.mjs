import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = () => fs.readFileSync(new URL('../extension/facet-input.js', import.meta.url), 'utf8');
function fixture({ fastTimers = false } = {}) {
  const calls = [];
  const data = {};
  let allowed = true;
  let fail = '';
  let moved = false;
  let reads = 0;
  const context = vm.createContext({ URL, setTimeout: fastTimers ? (fn, ms) => setTimeout(fn, Math.min(ms, 10)) : setTimeout,
    clearTimeout, Date });
  vm.runInContext(source(), context);
  const api = {
    storage: { local: { async get() { return { ...data }; }, async set(v) { Object.assign(data, v); } } },
    debugger: {
      async attach() { calls.push('attach'); }, async detach() { calls.push('detach'); },
      async sendCommand(_target, method, params) {
        calls.push(method === 'Input.dispatchMouseEvent' ? params.type : method);
        if (method === 'Runtime.evaluate') return { result: { value: { ok: true, clock: 123,
          x: moved && reads++ ? 180 : 100, y: 150 } } };
        if (params.type === fail) throw new Error('private transport failure');
        if (params.type === 'mousePressed' && fail === 'revoke-on-press') allowed = false;
        return {};
      }
    }
  };
  const input = context.ReviewerFacetInput.create(api, { authorize: async () => allowed });
  const selection = { kind: 'department', label: 'Baby', parentUrl: 'https://www.walmart.com/reviews/claim-product' };
  return { input, selection, calls, data, api, factory: context.ReviewerFacetInput,
    permit(value) { allowed = value; }, fail(value) { fail = value; }, move() { moved = true; } };
}

test('normal facet input clicks once with a confirmed release and detaches its own session', async () => {
  const f = fixture();
  const result = await f.input.click(11, 7, f.selection);
  assert.equal(result.ok, true);
  assert.equal(f.calls.filter(c => c === 'mousePressed').length, 1);
  assert.equal(f.calls.filter(c => c === 'mouseReleased').length, 1);
  assert.equal(f.calls.at(-1), 'detach');
});

test('revoked scanner authority and invalid or searched scopes never attach for input', async () => {
  const f = fixture();
  f.permit(false);
  assert.equal((await f.input.click(11, 7, f.selection)).reason, 'cancelled');
  f.permit(true);
  for (const parentUrl of ['https://www.walmart.com/blocked', 'https://example.com/reviews/claim-product',
    `${f.selection.parentUrl}?q=Pet`, `${f.selection.parentUrl}?catId=4044&q=Pet`]) {
    assert.equal((await f.input.click(11, 7, { ...f.selection, parentUrl })).ok, false);
  }
  assert.equal(f.calls.length, 0);
});

test('a moving or replaced target cancels before press and a transport failure stays isolated', async () => {
  const f = fixture(); f.move();
  assert.equal((await f.input.click(11, 7, f.selection)).ok, false);
  assert.equal(f.calls.includes('mousePressed'), false);
  assert.equal(f.calls.at(-1), 'detach');
  const g = fixture(); g.fail('mousePressed');
  assert.equal((await g.input.click(11, 7, g.selection)).ok, false);
  assert.equal(g.calls.includes('mouseReleased'), true);
  assert.equal(g.calls.at(-1), 'detach');
  // A failed press acknowledgement could be a late command. Even a release
  // acknowledgement cannot authorize another press after that uncertainty.
  const attempts = g.calls.length;
  g.fail('');
  assert.equal((await g.input.click(11, 7, g.selection)).ok, false);
  assert.equal(g.calls.length, attempts);
});

test('concurrent callers and lost authority after attachment cannot send a press', async () => {
  const f = fixture();
  let resume;
  f.api.debugger.attach = () => new Promise(resolve => { resume = resolve; });
  const first = f.input.click(11, 7, f.selection);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal((await f.input.click(11, 7, f.selection)).ok, false);
  f.permit(false);
  resume();
  assert.equal((await first).ok, false);
  assert.equal(f.calls.includes('mousePressed'), false);
  assert.equal(f.calls.at(-1), 'detach');
});

test('a timed-out late attachment is cleaned up without input or a competing attachment', async () => {
  const f = fixture({ fastTimers: true });
  let attach;
  f.api.debugger.attach = () => new Promise(resolve => { attach = resolve; });
  assert.equal((await f.input.click(11, 7, f.selection)).ok, false);
  assert.equal((await f.input.click(11, 7, f.selection)).ok, false);
  attach();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(f.calls, ['detach']);
});

function readerFixture() {
  const f = fixture();
  let challenge = false, occluded = false, duplicates = false;
  const node = () => ({ isConnected: true, hidden: false, parentElement: null,
    getClientRects: () => [{}], getAttribute: () => null });
  const root = node(), panel = node(), label = node(), radio = node();
  radio.value = 'Baby'; radio.name = 'Departments'; radio.checked = false; radio.disabled = false;
  radio.closest = selector => selector === 'label' ? label : null;
  label.control = radio; label.parentElement = panel; panel.parentElement = root;
  label.closest = () => panel;
  label.querySelectorAll = () => [radio];
  label.getBoundingClientRect = () => ({ x: 10, y: 100, width: 200, height: 40 });
  label.contains = target => target === label || target === radio;
  root.contains = target => target === label;
  root.querySelectorAll = () => duplicates ? [radio, radio] : [radio];
  const doc = { visibilityState: 'visible',
    querySelectorAll: selector => selector.startsWith('#px-') ? challenge ? [node()] : [] : [root],
    elementFromPoint: () => occluded ? root : radio };
  const location = { href: f.selection.parentUrl };
  const context = vm.createContext({ URL, document: doc, location, innerWidth: 800, innerHeight: 600,
    performance: { timeOrigin: 123 }, getComputedStyle: () => ({ display: 'block', visibility: 'visible' }) });
  vm.runInContext(source(), context);
  return { radio, label, doc, location,
    inspect: () => context.ReviewerFacetInput.inspect('Baby', f.selection.parentUrl),
    challenge() { challenge = true; }, occlude() { occluded = true; }, duplicate() { duplicates = true; } };
}

test('fixed DOM reader requires the exact visible associated option in an unsearched catalogue', () => {
  const f = readerFixture();
  assert.equal(f.inspect().ok, true);
  f.location.href += '?q=Pet';
  assert.equal(f.inspect().ok, false);
  f.location.href = 'https://www.walmart.com/blocked';
  assert.equal(f.inspect().ok, false);
  for (const mutate of [f => f.challenge(), f => f.occlude(), f => f.duplicate(),
    f => { f.label.control = {}; }, f => { f.radio.checked = true; },
    f => { f.doc.visibilityState = 'hidden'; }, f => { f.label.isConnected = false; },
    f => { f.location.href += '?catId=4044'; }]) {
    const g = readerFixture(); mutate(g); assert.equal(g.inspect().ok, false);
  }
});

test('authority loss after a press still releases, and an uncertain release prevents further presses', async () => {
  const f = fixture(); f.fail('revoke-on-press');
  await f.input.click(11, 7, f.selection);
  assert.equal(f.calls.includes('mouseReleased'), true);
  const g = fixture(); g.fail('mouseReleased');
  assert.equal((await g.input.click(11, 7, g.selection)).ok, false);
  const attached = g.calls.filter(c => c === 'attach').length;
  const fresh = g.factory.create(g.api, { authorize: async () => true });
  assert.equal((await fresh.click(11, 7, g.selection)).ok, false);
  assert.equal((await fresh.click(11, 7, g.selection)).ok, false);
  assert.equal(g.calls.filter(c => c === 'attach').length, attached);
});
