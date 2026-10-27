import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { runApprovedVerification } from '../tools/verification-assist.mjs';

function browserFixture({ clearAfterMs = 4_000, navigated = false, supported = true,
  observationMs = 500 } = {}) {
  let clock = 0;
  let reads = 0;
  const clicks = [];
  const tab = {
    playwright: { async evaluate() {
      if (++reads === 1) return supported ? { eligible: true,
        target: { x: 850, y: 570 }, layout: 'modal', challengeVisible: true } : { eligible: false };
      return { eligible: !navigated, challengeVisible: clock < clearAfterMs,
        layout: clock < clearAfterMs ? 'modal' : 'none', reviewerReady: clock >= clearAfterMs,
        productLinks: 78, emptyResults: false };
    } },
    async click(point, options) { clicks.push({ point, options }); },
    async getAXState() { clock += observationMs; return ''; }
  };
  return { tab, clicks, now: () => clock };
}

test('waits for delayed success after one hold instead of making another attempt', async () => {
  const fixture = browserFixture();
  const result = await runApprovedVerification(fixture.tab, { approved: true, now: fixture.now });
  assert.equal(result.outcome, 'cleared-with-catalogue');
  assert.equal(result.attempts, 1);
  assert.equal(fixture.clicks.length, 1);
  assert.ok(result.settleMs >= 4_000);
  assert.equal(fixture.clicks[0].options.durationMs, 8_000);
});

test('fast observations still wait for delayed success and retain bounded history', async () => {
  const fixture = browserFixture({ observationMs: 10 });
  const result = await runApprovedVerification(fixture.tab, { approved: true, now: fixture.now });
  assert.equal(result.outcome, 'cleared-with-catalogue');
  assert.ok(result.settleMs >= 4_000);
  assert.ok(result.observations.length <= 120);
  assert.equal(fixture.clicks.length, 1);
});

function domFixture({ offscreen = false, covered = false, blockedAfter = false,
  fullPage = false, hiddenDialogAfter = false, replacementAfterMs = null,
  contentAfterMs = 0, unrelatedDialog = false, hiddenProducts = false } = {}) {
  let clock = 0;
  let clicked = false;
  const rect = { x: offscreen ? 2000 : 600, y: 400, width: 350, height: 102 };
  const container = {
    getBoundingClientRect: () => rect, getClientRects: () => [rect],
    contains: target => target === container
  };
  const frame = { parentElement: container };
  const dialog = {
    hidden: () => clicked && hiddenDialogAfter,
    getBoundingClientRect: () => rect, getClientRects: () => [rect],
    querySelector: selector => selector.includes('h2') ?
      { textContent: 'Robot or human?' } : frame,
    querySelectorAll: selector => selector === 'iframe' ? [frame] : [],
    contains: target => target === container,
    innerText: 'Robot or human? PRESS & HOLD'
  };
  const marker = { ...container, querySelectorAll: () => [],
    contains: target => target === marker, innerText: 'PRESS & HOLD' };
  const other = { ...dialog, querySelector: () => ({ textContent: 'Shopping options' }),
    querySelectorAll: () => [], innerText: 'Shopping options' };
  const product = { getClientRects: () => hiddenProducts ? [] : [rect],
    getBoundingClientRect: () => rect };
  const kind = () => !clicked ? (fullPage ? 'full' : 'modal') :
    replacementAfterMs !== null && clock >= replacementAfterMs ? 'full' :
    hiddenDialogAfter ? 'hidden' : 'none';
  const queryAll = selector => {
    if (selector.includes('a[href')) return clicked && clock >= contentAfterMs ? Array(78).fill(product) : [];
    if (selector === '[role="dialog"]') {
      const current = ['modal', 'hidden'].includes(kind()) ? [dialog] : [];
      return unrelatedDialog ? [other, ...current] : current;
    }
    if (selector.includes('#px-captcha')) return kind() === 'full' ? [marker] : [];
    return [];
  };
  const context = vm.createContext({
    location: { protocol: 'https:', hostname: 'www.walmart.com',
      pathname: fullPage ? '/blocked' : '/reviews/claim-product' },
    document: {
      body: { get innerText() {
        return ['modal', 'full'].includes(kind()) ? 'Robot or human? PRESS & HOLD' : 'Reviewer items';
      } },
      documentElement: { clientWidth: 1600, clientHeight: 1000 },
      querySelector: selector => queryAll(selector)[0] || null,
      querySelectorAll: queryAll, getElementById: () => null,
      elementFromPoint: () => covered ? {} : kind() === 'full' ? marker : container
    },
    getComputedStyle: element => ({ display: element.hidden?.() ? 'none' : 'block',
      visibility: 'visible', opacity: '1' })
  });
  return {
    now: () => clock,
    clicked: () => clicked,
    tab: {
      playwright: { evaluate: async fn => vm.runInContext(`(${fn.toString()})()`, context) },
      async click() {
        clicked = true;
        context.location.pathname = blockedAfter ? '/blocked' : '/reviews/claim-product';
      },
      async getAXState() { clock += 500; }
    }
  };
}

test('the real DOM locator rejects off-screen and covered challenge controls', async () => {
  for (const condition of [{ offscreen: true }, { covered: true }]) {
    const fixture = domFixture(condition);
    const result = await runApprovedVerification(fixture.tab, { approved: true, now: fixture.now });
    assert.equal(result.outcome, 'no-supported-visible-challenge');
    assert.equal(fixture.clicked(), false);
  }
});

test('a blocked redirect without a dialog remains pending', async () => {
  const fixture = domFixture({ blockedAfter: true });
  const result = await runApprovedVerification(fixture.tab,
    { approved: true, now: fixture.now, settleTimeoutMs: 2_000 });
  assert.equal(result.outcome, 'verification-pending');
  assert.equal(fixture.clicked(), true);
});

test('the observed full-page challenge layout supports one targeted attempt', async () => {
  const fixture = domFixture({ fullPage: true });
  const result = await runApprovedVerification(fixture.tab, { approved: true, now: fixture.now });
  assert.equal(fixture.clicked(), true);
  assert.equal(result.outcome, 'cleared-with-catalogue');
});

test('a hidden old dialog does not prevent confirmation of recovered content', async () => {
  const fixture = domFixture({ hiddenDialogAfter: true });
  const result = await runApprovedVerification(fixture.tab, { approved: true, now: fixture.now });
  assert.equal(result.outcome, 'cleared-with-catalogue');
});

test('an unrelated first dialog does not hide the supported challenge', async () => {
  const fixture = domFixture({ unrelatedDialog: true });
  const result = await runApprovedVerification(fixture.tab, { approved: true, now: fixture.now });
  assert.equal(fixture.clicked(), true);
  assert.equal(result.outcome, 'cleared-with-catalogue');
});

test('a replacement full-page challenge cannot be mistaken for cached catalogue success', async () => {
  const fixture = domFixture({ replacementAfterMs: 0 });
  const result = await runApprovedVerification(fixture.tab,
    { approved: true, now: fixture.now, settleTimeoutMs: 2_000 });
  assert.notEqual(result.outcome, 'cleared-with-catalogue');
  assert.equal(result.verified, false);
});

test('settling waits for delayed usable content instead of returning on a missing dialog', async () => {
  const fixture = domFixture({ contentAfterMs: 3_000 });
  const result = await runApprovedVerification(fixture.tab, { approved: true, now: fixture.now });
  assert.equal(result.outcome, 'cleared-with-catalogue');
  assert.ok(result.settleMs >= 3_000);
  assert.equal(result.verified, true);
});

test('a transient clear before a replacement challenge does not count as verified recovery', async () => {
  const fixture = domFixture({ replacementAfterMs: 1_000 });
  const result = await runApprovedVerification(fixture.tab,
    { approved: true, now: fixture.now, settleTimeoutMs: 2_000 });
  assert.notEqual(result.outcome, 'cleared-with-catalogue');
  assert.equal(result.verified, false);
});

test('hidden cached product anchors cannot confirm usable reviewer content', async () => {
  const fixture = domFixture({ hiddenProducts: true });
  const result = await runApprovedVerification(fixture.tab,
    { approved: true, now: fixture.now, settleTimeoutMs: 2_000 });
  assert.equal(result.verified, false);
  assert.equal(result.outcome, 'cleared-waiting-content');
  assert.ok(result.settleMs >= 2_000);
});

test('a slow or unsuccessful challenge stops as pending without a blind retry', async () => {
  const fixture = browserFixture({ clearAfterMs: Infinity });
  const result = await runApprovedVerification(fixture.tab,
    { approved: true, now: fixture.now, settleTimeoutMs: 3_000 });
  assert.equal(result.outcome, 'verification-pending');
  assert.equal(fixture.clicks.length, 1);
  assert.ok(result.settleMs >= 3_000);
});

test('requires approval for this attempt before interacting with the browser', async () => {
  const fixture = browserFixture();
  await assert.rejects(runApprovedVerification(fixture.tab), /current challenge/);
  assert.equal(fixture.clicks.length, 0);
});

test('an unsupported page never receives input', async () => {
  const fixture = browserFixture({ supported: false });
  const result = await runApprovedVerification(fixture.tab, { approved: true, now: fixture.now });
  assert.equal(result.outcome, 'no-supported-visible-challenge');
  assert.equal(fixture.clicks.length, 0);
});

test('navigation away during an attempt is not reported as challenge success', async () => {
  const fixture = browserFixture({ navigated: true, clearAfterMs: 0 });
  const result = await runApprovedVerification(fixture.tab, { approved: true, now: fixture.now });
  assert.equal(result.outcome, 'navigation-changed');
  assert.equal(fixture.clicks.length, 1);
});

test('rejects unbounded timing options before any input', async () => {
  for (const settings of [{ holdMs: Infinity }, { holdMs: -1 }, { settleTimeoutMs: 0 }]) {
    const fixture = browserFixture();
    await assert.rejects(runApprovedVerification(fixture.tab,
      { approved: true, now: fixture.now, ...settings }), /timing/);
    assert.equal(fixture.clicks.length, 0);
  }
});
