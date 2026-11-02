import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { renderNativeProtocol } from '../tools/generate-native-protocol.mjs';

const spec = JSON.parse(fs.readFileSync(new URL('../native_verification/protocol.json', import.meta.url)));
const cases = JSON.parse(fs.readFileSync(new URL('./fixtures/native-protocol-cases.json', import.meta.url)));
const source = fs.readFileSync(new URL('../extension/verification-protocol.js', import.meta.url), 'utf8');
const context = vm.createContext({});
vm.runInContext(source, context);
const protocol = context.VerificationNativeProtocol;

test('browser native contract is generated exactly from the canonical specification', () => {
  assert.equal(source, renderNativeProtocol(spec));
  const { validState, ...browserSpec } = protocol;
  assert.deepEqual(JSON.parse(JSON.stringify(browserSpec)), spec);
  assert.equal(typeof validState, 'function');
  assert.equal(protocol.limits.viewportMax, 20000);
});

for (const sample of cases.state) {
  test(`shared native response fixture: ${sample.name}`, () => {
    const state = { ready: true, reason: null, idleMs: 6000, phase: 'idle',
      pressSent: false, pressConfirmed: false, releaseConfirmed: false, holdMs: 0,
      ...sample.change };
    assert.equal(Boolean(protocol.validState(state)), sample.valid);
  });
}
