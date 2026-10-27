import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const context = vm.createContext({});
for (const name of ['sweep-modes.js', 'runtime-policy.js']) {
  vm.runInContext(readFileSync(new URL(`../extension/${name}`, import.meta.url), 'utf8'), context);
}
const timing = context;

test('completed-cycle jitter is stable, varies by cycle and never shortens configured floors', () => {
  const settings = { refreshMinutes: 5, lastSweepStartedAt: 100_000, lastSweepDone: 200_000 };
  const first = timing.sweepRestartAt(settings, 210_000);
  assert.ok(first > 400_000 && first <= 430_000);
  assert.equal(timing.sweepRestartAt(settings, 220_000), first);
  assert.notEqual(timing.sweepRestartAt({ ...settings,
    lastSweepStartedAt: 110_000, lastSweepDone: 210_000 }, 220_000) - 410_000,
  first - 400_000);
  assert.equal(timing.sweepRestartAt({ ...settings, refreshMinutes: 0 }, 210_000), 0);
});

test('cleared verification immediately restores saved scan and page timings', () => {
  const now = Date.now();
  for (const count of [1, 3, 9]) {
    const settings = { refreshMinutes: 5, pageDelaySeconds: 15,
      botCheck: { count, lastAt: now - 1_000, pausedUntil: 0 } };
    assert.equal(timing.effectiveRefreshMinutes(settings, now), 5);
    assert.equal(timing.effectivePageSeconds(settings,
      { mode: 'none', total: 19 }, now), 15);
  }
});

test('recent verification does not defer the next complete scan by hours', () => {
  const now = Date.now();
  const restart = timing.sweepRestartAt({ refreshMinutes: 5,
    lastSweepStartedAt: now - 4 * 60_000, lastSweepDone: now,
    botCheck: { count: 9, lastAt: now - 60_000, pausedUntil: 0 } }, now);
  assert.ok(restart >= now + 60_000 && restart <= now + 90_000);
});

test('disabled restarts and configured page spacing remain respected', () => {
  assert.equal(timing.effectiveRefreshMinutes({ refreshMinutes: 0 }), 0);
  assert.equal(timing.effectivePageSeconds({ refreshMinutes: 5, pageDelaySeconds: 30 },
    { mode: 'none', total: 19 }), 30);
  assert.equal(timing.effectivePageSeconds({ refreshMinutes: 10, pageDelaySeconds: 15 },
    { mode: 'none', total: 19 }), 450 / 19);
});

test('page loading and ingest time count toward navigation spacing', () => {
  const now = 100_000;
  const result = timing.pageNavigationDelay(
    { refreshMinutes: 5, pageDelaySeconds: 15 }, { mode: 'none', total: 19 },
    now - 8_000, now, 0.5
  );
  assert.equal(result.intervalMs, 15_000);
  assert.equal(result.elapsedMs, 8_000);
  assert.equal(result.delayMs, 7_000);
});

test('a slow page can navigate immediately after processing finishes', () => {
  const now = 100_000;
  const result = timing.pageNavigationDelay(
    { refreshMinutes: 5, pageDelaySeconds: 15 }, { mode: 'none', total: 19 },
    now - 25_000, now, 0.5
  );
  assert.equal(result.intervalMs, 15_000);
  assert.equal(result.elapsedMs, 25_000);
  assert.equal(result.delayMs, 0);
});

test('negative jitter never lowers the configured navigation interval', () => {
  const settings = { refreshMinutes: 5, pageDelaySeconds: 30 };
  const result = timing.pageNavigationDelay(settings, { mode: 'none', total: 19 },
    90_000, 100_000, 0);
  assert.equal(result.intervalMs, 30_000);
  assert.equal(result.elapsedMs, 10_000);
  assert.equal(result.delayMs, 20_000);
});

test('navigation spacing retains sweep pacing and positive jitter', () => {
  const settings = { refreshMinutes: 10, pageDelaySeconds: 15 };
  const result = timing.pageNavigationDelay(settings, { mode: 'none', total: 19 },
    90_000, 100_000, 1);
  assert.equal(result.intervalMs, 450_000 / 19 * 1.25);
  assert.equal(result.elapsedMs, 10_000);
  assert.equal(result.delayMs, result.intervalMs - 10_000);
});

test('absent, invalid, or future navigation clocks never shorten the wait', () => {
  const settings = { refreshMinutes: 5, pageDelaySeconds: 15 };
  for (const lastNavigationAt of [undefined, null, 0, -1, NaN, Infinity,
    -Infinity, '90000', 100_001]) {
    const result = timing.pageNavigationDelay(settings, null, lastNavigationAt, 100_000, 0.5);
    assert.equal(result.intervalMs, 15_000);
    assert.equal(result.elapsedMs, 0);
    assert.equal(result.delayMs, 15_000);
  }
  for (const now of [NaN, Infinity, -Infinity, null, '100000']) {
    const result = timing.pageNavigationDelay(settings, null, 90_000, now, 0.5);
    assert.equal(result.elapsedMs, 0);
    assert.equal(result.delayMs, 15_000);
  }
});

test('each interval uses one default random sample or the supplied sample value', () => {
  const sampleContext = vm.createContext({});
  vm.runInContext(readFileSync(new URL('../extension/runtime-policy.js', import.meta.url), 'utf8') +
    '\nglobalThis.samples = 0; Math.random = () => { samples += 1; return 0.5; };', sampleContext);
  const settings = { refreshMinutes: 5, pageDelaySeconds: 15 };
  assert.equal(sampleContext.pageNavigationDelay(settings, null, 90_000, 100_000, 1).intervalMs, 18_750);
  assert.equal(sampleContext.samples, 0);
  assert.equal(sampleContext.pageNavigationDelay(settings, null, 90_000, 100_000).intervalMs, 15_000);
  assert.equal(sampleContext.samples, 1);
});
