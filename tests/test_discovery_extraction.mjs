import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const sources = ['sweep-modes.js', 'runtime-policy.js', 'verification-observation.js', 'page-controls.js',
  'document-session.js', 'live-observer.js', 'content.js'].map(name => fs.readFileSync(
  new URL(`../extension/${name}`, import.meta.url), 'utf8')).join('\n');

// Small card DOM, preserving real descendant and ancestor boundaries. The VM
// runs the complete content script; no extraction helpers are replaced.
class Element {
  constructor(tag, attributes = {}, text = '', children = []) {
    this.tagName = tag.toUpperCase();
    this.attributes = attributes;
    this.innerText = text;
    this.textContent = text;
    this.children = children;
    for (const child of children) child.parentElement = this;
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  matches(selector) {
    const match = selector.trim().match(/^(\w+)?(?:\[([\w-]+)([*~]?=)?"?([^\]"]*)"?\])?$/);
    if (!match) throw new Error(`Unsupported fixture selector: ${selector}`);
    const [, tag, attribute, operator, value] = match;
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    if (!attribute) return true;
    const actual = this.getAttribute(attribute);
    if (actual === null) return false;
    if (!operator) return true;
    if (operator === '*=') return actual.includes(value);
    if (operator === '~=') return actual.split(/\s+/).includes(value);
    return actual === value;
  }
  querySelectorAll(selector) {
    const parts = selector.split(',');
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
    return selector === '*' ? descendants :
      descendants.filter(child => parts.some(part => child.matches(part)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
}

function contentFrom(cards, { query = '' } = {}) {
  const body = new Element('body', {}, `Free items remaining 3 items\n${cards.map(c => c.innerText).join('\n')}`, cards);
  const messages = [];
  const context = vm.createContext({
    document: { body, addEventListener() {},
      querySelectorAll: selector => selector === 'a[href*="/ip/"]' ? body.querySelectorAll(selector) : [],
      querySelector: () => null },
    chrome: { storage: { local: { async get() { return {}; } } }, runtime: {
      id: 'reviewer-fixture-extension',
      onMessage: { addListener() {} }, async sendMessage(message) {
        messages.push(message);
        if (message.type === 'reviewer:role') return new Promise(() => {});
        return { ok: true };
      }
    } },
    location: { origin: 'https://www.walmart.com', pathname: '/reviews/claim-product',
      search: `?q=${encodeURIComponent(query)}`, href: 'https://www.walmart.com/reviews/claim-product' },
    window: {}, URL, URLSearchParams,
    MutationObserver: class { observe() {} disconnect() {} },
    setTimeout() {}, clearTimeout() {}, setInterval() {}, console
  });
  context.messages = messages;
  vm.runInContext(sources, context);
  return context;
}

function collectFrom(cards, options) {
  const context = contentFrom(cards, options);
  vm.runInContext('globalThis.collected = collect();', context);
  return JSON.parse(JSON.stringify(context.collected));
}


function headingCard(id, title = 'Selected card',
  { outOfStock = false, valueLine = '(Valued at $49)', variants = [] } = {}) {
  const heading = new Element('h3', {}, title);
  const primary = new Element('a', { href: `/ip/Selected/${id}` }, title,
    [new Element('span', {}, title, [heading])]);
  const text = `${title}\nFree\n${valueLine}${outOfStock ? '\nOut of stock' : ''}`;
  return new Element('div', { role: 'group' }, text, [primary, ...variants]);
}
function swatch(id, title = 'Selected card') {
  return new Element('a', { href: `/ip/Variant/${id}` }, '',
    [new Element('img', { alt: `Red, variant on ${title}` })]);
}
function captured(node) {
  return new Element(node.tag, node.attributes, node.text, (node.children || []).map(captured));
}
function fixtureCard() {
  const document = JSON.parse(fs.readFileSync(
    new URL('./fixtures/reviewer-sofa-card-20261028.json', import.meta.url), 'utf8'));
  return document.cards.find(card => JSON.stringify(card).includes('19698913226'));
}

test('captured out-of-stock heading primary retains its own evidence without a View item action', () => {
  const items = collectFrom([captured(fixtureCard())]);
  assert.deepEqual(items.map(i => [i.item_id, i.available, i.value_usd]), [['ip-19698913226', false, 29.89]]);
  assert.equal(items.some(i => ['ip-19385610574', 'ip-19341061402'].includes(i.item_id)), false);
});

test('an available selected heading primary can coexist with explicitly bound variants', () => {
  const product = headingCard('12345678', 'Selected card', { variants: [swatch('12345679')] });
  assert.deepEqual(collectFrom([product]).map(i => [i.item_id, i.available, i.value_usd]),
    [['ip-12345678', true, 49]]);
});

test('the captured heading layout preserves available versus unavailable local stock evidence', () => {
  const raw = structuredClone(fixtureCard());
  function available(node) {
    node.text = node.text.replace(/\nOut of stock/g, '');
    for (const child of node.children || []) available(child);
  }
  available(raw);
  const items = collectFrom([captured(raw)]);
  assert.deepEqual(items.map(i => [i.item_id, i.available]), [['ip-19698913226', true]]);
});

test('heading fallback cannot borrow price or stock from an adjacent different primary', () => {
  const unavailable = headingCard('12345678', 'Unpriced card',
    { valueLine: '', variants: [swatch('12345679', 'Unpriced card')] });
  const priced = headingCard('12345680', 'Priced card', { outOfStock: true });
  const group = new Element('div', {}, unavailable.innerText + '\n' + priced.innerText, [unavailable, priced]);
  assert.deepEqual(collectFrom([group]).map(i => [i.item_id, i.available, i.value_usd]),
    [['ip-12345680', false, 49]]);
});

test('an unmarked neighboring link cannot acquire a heading primary\'s offer', () => {
  const product = headingCard('12345678', 'Selected card',
    { variants: [new Element('a', { href: '/ip/12345679' }, 'Other card')] });
  assert.deepEqual(collectFrom([product]), []);
});

test('variant-only links stay rejected even if they contain a heading', () => {
  const variant = swatch('12345679');
  variant.children.push(new Element('h3', {}, 'Selected card'));
  const product = new Element('div', { role: 'group' }, 'Selected card\nFree\n(Valued at $49)', [variant]);
  assert.deepEqual(collectFrom([product]), []);
});

test('mismatched variant titles cannot be authorized by a different heading primary', () => {
  const product = headingCard('12345678', 'Selected card', { variants: [swatch('12345679', 'A different card')] });
  assert.deepEqual(collectFrom([product]), []);
});

test('two different heading primaries cannot make one shared valuation unambiguous', () => {
  const first = headingCard('12345678', 'First card', { valueLine: '' });
  const second = headingCard('12345679', 'Second card');
  const group = new Element('div', {}, first.innerText + '\n' + second.innerText,
    [first.children[0], second.children[0]]);
  assert.deepEqual(collectFrom([group]), []);
});

test('a missing valuation amount remains unknown while a missing retail-offer line stays unverified', () => {
  const unknown = headingCard('12345678', 'Unknown amount', { valueLine: '(Valued at )' });
  assert.deepEqual(collectFrom([unknown]).map(i => [i.item_id, i.value_usd]), [['ip-12345678', null]]);
  const noLine = headingCard('12345679', 'No offer line', { valueLine: '' });
  assert.deepEqual(collectFrom([noLine]), []);
});

test('the priority scanner uses exactly the same stock and variant admission and safe card telemetry', () => {
  const context = contentFrom([headingCard('12345678', 'Selected card',
    { outOfStock: true, variants: [swatch('12345679')] })]);
  vm.runInContext("role.auxiliary = { lane: 'priority' }; this.result = collect(null, true);", context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.result)).map(i => [i.item_id, i.available]),
    [['ip-12345678', false]]);
  const event = context.messages.find(m => m.event === 'card-extraction');
  assert.equal(event.details.lane, 'priority');
  assert.equal(event.details.outOfStockCount, 1);
});

test('heading fallback is restricted to the captured semantic product group', () => {
  const product = headingCard('12345678', 'Selected card', { variants: [swatch('12345679')] });
  product.attributes = {};
  assert.deepEqual(collectFrom([product]), []);
});

test('multiple or empty headings do not provide a fallback primary identity', () => {
  for (const multiple of [false, true]) {
    const product = headingCard('12345678', 'Selected card', { variants: [swatch('12345679')] });
    const titleLink = product.children[0];
    if (multiple) titleLink.children.push(new Element('h3', {}, 'Conflicting second heading'));
    else titleLink.children[0].children[0].innerText = titleLink.children[0].children[0].textContent = '';
    assert.deepEqual(collectFrom([product]), []);
  }
});
