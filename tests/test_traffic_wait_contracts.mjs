import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const context = vm.createContext({ console });
for (const file of ['scanner-control.js', 'diagnostics.js']) {
  vm.runInContext(fs.readFileSync(new URL(`../extension/${file}`, import.meta.url), 'utf8'), context);
}

for (const event of ['traffic-wait', 'traffic-resumed']) {
  test(`${event} records bounded timing without page text or account data`, () => {
    const input = { lane: 'keywords', tabId: 7, generation: 9, documentId: 90_000,
      durationMs: 30_000, message: 'Hold tight: private page text', url: 'https://private.invalid',
      highTraffic: true, botCheck: false };
    const result = structuredClone(context.cleanDiagnostic(event, input, 100_000));
    assert.deepEqual(result, { at: 100_000, event, lane: 'keywords', tabId: 7,
      generation: 9, documentId: 90_000, durationMs: 30_000 });
  });
}

test('traffic waiting survives the runtime status sanitizer without probe-only fields', () => {
  const result = structuredClone(context.scannerRuntimeProbe({ phase: 'waiting-traffic', ready: true,
    active: true, busy: false, visibility: 'visible', focusReason: 'none',
    lastActivityAt: 100_000, nextActionAt: 110_000, highTraffic: true,
    text: 'private content', botCheck: false }));
  assert.deepEqual(result, { phase: 'waiting-traffic', reachable: true, active: true, busy: false,
    visibility: 'visible', focusReason: 'none', lastActivityAt: 100_000, nextActionAt: 110_000 });
});

test('older status phases and unrelated diagnostic events retain their wire shapes', () => {
  assert.equal(context.scannerRuntimeProbe({ phase: 'waiting-content' }).phase, 'waiting-content');
  assert.equal(context.cleanDiagnostic('challenge', { reason: 'text' }, 100_000).reason, 'text');
  assert.equal(context.cleanDiagnostic('private unknown event', {}, 100_000), null);
});
