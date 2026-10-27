import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = ['page-controls.js', 'verification-watch.js'].map(name =>
  fs.readFileSync(new URL(`../extension/${name}`, import.meta.url), 'utf8')).join('\n');

function watch(pathname = '/ip/example/123', text = 'Robot or human? Press & Hold', responses = []) {
  const messages = [];
  const listeners = [];
  const timers = [];
  const body = { innerText: text };
  let observer;
  const context = vm.createContext({
    location: { pathname }, document: { body, querySelectorAll: () => [] },
    chrome: { runtime: { id: 'fixture', onMessage: { addListener(fn) { listeners.push(fn); } },
      async sendMessage(message) {
        messages.push(message);
        const response = responses.shift();
        if (response instanceof Error) throw response;
        return response ?? { ok: true };
      } } },
    MutationObserver: class {
      constructor(callback) { this.callback = callback; observer = this; }
      observe() {} disconnect() { this.disconnected = true; }
    },
    getComputedStyle: () => ({ display: 'none', visibility: 'hidden' }),
    setTimeout(fn) { timers.push(fn); return timers.length; }
  });
  vm.runInContext(source, context);
  return { messages, listeners, timers, body, context, get observer() { return observer; } };
}

const settle = () => new Promise(resolve => setImmediate(resolve));

test('ordinary Walmart pages report each visible challenge episode once without scanning', async () => {
  const page = watch();
  await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(page.messages)), [{ type: 'reviewer:verification', reason: 'text' }]);
  page.observer.callback();
  page.timers.shift()();
  await settle();
  assert.equal(page.messages.length, 1);
  page.body.innerText = 'Product details';
  page.observer.callback();
  page.timers.shift()();
  page.body.innerText = 'Robot or human? Press & Hold';
  page.observer.callback();
  page.timers.shift()();
  await settle();
  assert.equal(page.messages.length, 2);
  assert.ok(page.messages.every(message => message.type === 'reviewer:verification'));
});

test('passive recovery probes inspect the current DOM and keep hidden placeholders clear', async () => {
  const page = watch('/cart', 'Cart');
  vm.runInContext('document.querySelectorAll = () => [{ getClientRects: () => [] }]', page.context);
  let reply;
  const probe = () => page.listeners[0]({ type: 'reviewer:challenge-probe' }, {}, value => { reply = value; });
  probe();
  assert.equal(reply.challenge, false);
  page.body.innerText = 'Robot or human? Press & Hold';
  probe();
  assert.equal(reply.challenge, true);
  page.body.innerText = 'Cart';
  probe();
  assert.equal(reply.challenge, false);
  assert.equal(page.messages.length, 0);
});

test('reviewer and blocked routes keep their existing detector without a duplicate observer', () => {
  for (const route of ['/reviews/claim-product', '/blocked']) {
    const page = watch(route);
    assert.equal(page.observer, undefined);
    assert.equal(page.messages.length, 0);
  }
});

test('an unacknowledged report retries a stable challenge without another DOM mutation', async () => {
  const page = watch('/cart', 'Robot or human? Press & Hold', [new Error('worker unavailable')]);
  await settle();
  assert.equal(page.messages.length, 1);
  assert.equal(page.timers.length, 1);
  page.timers.shift()();
  await settle();
  assert.equal(page.messages.length, 2);
  assert.equal(page.timers.length, 0);
});

test('manifest loads passive controls before scanner code and covers all Walmart pages', () => {
  const manifest = JSON.parse(fs.readFileSync(new URL('../extension/manifest.json', import.meta.url), 'utf8'));
  const watcher = manifest.content_scripts.find(script => script.js.includes('verification-watch.js'));
  assert.ok(watcher.matches.includes('https://www.walmart.com/*'));
  assert.equal(watcher.run_at, 'document_start');
  assert.deepEqual(watcher.js, ['page-controls.js', 'verification-watch.js']);
  assert.equal(manifest.content_scripts.filter(script => script.js.includes('page-controls.js')).length, 1);
});
