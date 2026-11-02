import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import observation from '../extension/verification-observation.js';

function page({ path = '/reviews/claim-product', text = '', marker = false,
  opacity = '1', hiddenParent = false, product = false, mainProduct = product } = {}) {
  const bounds = { x: 100, y: 100, width: 300, height: 100 };
  const parent = {};
  const widget = { parentElement: hiddenParent ? parent : null,
    getClientRects: () => [bounds], getBoundingClientRect: () => bounds,
    contains: target => target === widget, querySelectorAll: () => [] };
  const item = { getClientRects: () => [bounds], getBoundingClientRect: () => bounds };
  const context = vm.createContext({
    location: { protocol: 'https:', hostname: 'www.walmart.com', port: '', pathname: path },
    document: {
      body: { innerText: text }, documentElement: { clientWidth: 800, clientHeight: 600 },
      getElementById: () => null, elementFromPoint: () => widget,
      querySelectorAll(selector) {
        if (selector.includes('#px-captcha')) return marker ? [widget] : [];
        if (selector.startsWith('main a')) return mainProduct ? [item] : [];
        if (selector.startsWith('a[href')) return product ? [item] : [];
        return [];
      }
    },
    getComputedStyle: element => ({ display: element === parent ? 'none' : 'block',
      visibility: 'visible', opacity: element === widget ? opacity : '1' }),
    performance: { timeOrigin: 123 }, innerWidth: 800, innerHeight: 600, devicePixelRatio: 1,
  });
  const source = observation.observePage.toString();
  return policy => JSON.parse(JSON.stringify(vm.runInContext(`(${source})(${JSON.stringify(policy)})`, context)));
}

test('one self-contained callback preserves text-only detection differences', () => {
  const read = page({ text: 'Robot or human? Press & Hold' });
  assert.equal(read('passive').reason, 'text');
  assert.equal(read('operator').challengeVisible, true);
  assert.equal(read('driver').challenge, false);
});

test('existing opacity and ancestor visibility policies remain distinct', () => {
  const transparent = page({ marker: true, opacity: '0' });
  assert.equal(transparent('passive').reason, 'dom-marker');
  assert.equal(transparent('operator').challengeVisible, false);
  assert.equal(transparent('driver').challenge, false);
  const ancestor = page({ marker: true, hiddenParent: true });
  assert.equal(ancestor('passive').reason, 'dom-marker');
  assert.equal(ancestor('operator').challengeVisible, true);
  assert.equal(ancestor('driver').challenge, false);
});

test('route scope and catalogue scope preserve their existing consumer policies', () => {
  const suffix = page({ path: '/reviews/claim-product/extra', product: true });
  assert.equal(suffix('driver').eligible, false);
  assert.equal(suffix('operator').eligible, true);
  const outsideMain = page({ product: true, mainProduct: false });
  assert.equal(outsideMain('driver').ready, false);
  assert.equal(outsideMain('operator').reviewerReady, true);
  assert.equal(page({ path: '/blocked/follow-up' })('passive').reason, 'redirect');
});

test('driver observation preserves existing target and safe metadata without page text', () => {
  const read = page({ marker: true, text: 'private account text' });
  assert.deepEqual(read('driver'), {
    eligible: true, challenge: true, clock: 123, ready: false,
    target: { x: 280, y: 150 }, viewport: { width: 800, height: 600, dpr: 1 },
  });
  assert.equal(JSON.stringify(read('driver')).includes('private'), false);
});
