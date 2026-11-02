import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const context = vm.createContext({ console });
for (const file of ['scanner-control.js', 'diagnostics.js']) {
  vm.runInContext(fs.readFileSync(new URL(`../extension/${file}`, import.meta.url), 'utf8'), context);
}
const clean = (event, details) => structuredClone(context.cleanDiagnostic(event, details, 100_000));

test('recovery records retain exact typed identity and strip private query/error text', () => {
  const input = { lane: 'departments', tabId: 7, generation: 9, documentId: 90_000,
    round: 4, targetIndex: 3, attempt: 2, failureCode: 'document-http-error', responseStatus: 520,
    target: 'Private keyword', url: 'https://private.invalid', error: 'Private body' };
  for (const event of ['auxiliary-target-retry', 'auxiliary-target-skipped']) {
    const r = clean(event, input);
    for (const key of ['tabId', 'generation', 'documentId', 'round', 'targetIndex', 'attempt', 'failureCode', 'responseStatus']) {
      assert.equal(r[key], input[key], key);
    }
    assert.equal(r.target, undefined);
    assert.equal(r.url, undefined);
    assert.equal(r.error, undefined);
  }
});

test('navigation outcome and HTTP status are strictly allowlisted', () => {
  const input = { targetPage: 4, documentId: 90_000, outcome: 'timeout', responseStatus: 520 };
  const r = clean('navigation-outcome', input);
  assert.equal(r.outcome, 'timeout');
  assert.equal(r.responseStatus, 520);
  assert.equal(clean('navigation-outcome', { ...input, outcome: 'private exception' }), null);
  assert.equal(clean('navigation-outcome', { ...input, responseStatus: 1000 }), null);
});

test('explicit render errors no longer collapse to unexpected-error', () => {
  for (const code of ['render-timeout', 'render-opportunity-timeout', 'navigation-timeout', 'document-http-error',
    'navigation-scope-changed']) {
    assert.equal(context.scannerFailureCode(code), code);
  }
});
