import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Exercise the actual message boundary without starting browser alarms, intake
// or any verification transport. Workspace authority has its own runtime suite.
const source = fs.readFileSync(new URL('../extension/background.js', import.meta.url), 'utf8');
const start = source.indexOf("  if (msg?.type === 'reviewer:focus-release') {");
const end = source.indexOf("  if (msg?.type === 'reviewer:user-active') {", start);

function fixture() {
  const calls = [];
  const context = vm.createContext({
    tabWorkspace: { async release(...args) { calls.push(args); return { ok: true, released: true }; } },
    respondAsync(promise, respond) { return promise.then(respond); }
  });
  assert.ok(start >= 0 && end > start, 'The live background must handle a completed focus turn');
  const handle = vm.runInContext(`(msg, sender, sendResponse) => {${source.slice(start, end)}}`, context);
  return { calls, async send(message, sender) {
    let reply;
    await handle(message, sender, value => { reply = value; });
    return reply;
  } };
}

test('focus release uses the browser sender identity rather than caller-supplied tab IDs', async () => {
  const f = fixture();
  const reply = await f.send({ type: 'reviewer:focus-release', generation: 7, leaseId: 'turn-1', tabId: 999 },
    { frameId: 0, tab: { id: 42 } });
  assert.equal(reply.released, true);
  assert.deepEqual(f.calls, [[42, 7, 'turn-1']]);
});

test('subframes and malformed completions cannot release a scanner turn', async () => {
  const f = fixture();
  const valid = { type: 'reviewer:focus-release', generation: 7, leaseId: 'turn-1' };
  const cases = [
    [valid, { frameId: 1, tab: { id: 42 } }],
    [valid, { frameId: 0 }],
    [valid, { frameId: 0, tab: { id: '42' } }],
    [{ ...valid, generation: '7' }, { frameId: 0, tab: { id: 42 } }],
    [{ ...valid, leaseId: null }, { frameId: 0, tab: { id: 42 } }]
  ];
  for (const [message, sender] of cases) {
    const reply = await f.send(message, sender);
    assert.equal(reply.ok, false);
    assert.equal(reply.released, false);
  }
  assert.equal(f.calls.length, 0);
});
