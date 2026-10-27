import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const sources = ['sweep-modes.js', 'runtime-policy.js', 'page-controls.js',
  'live-observer.js', 'content.js'].map(name => fs.readFileSync(
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

function card(id, { title = 'Useful reviewer product', badge = '', category = '',
  outOfStock = false, metadata = [], image = null, value = 25 } = {}) {
  const anchor = new Element('a', { href: `/ip/Useful-product/${id}?private=removed`,
    'aria-label': `${badge} ${title}` }, `${badge}\n${title}`.trim(), image ? [image] : []);
  const text = `${badge}\n${title}\nFree (Valued at $${value})${outOfStock ? '\nOut of stock' : ''}`;
  return new Element('section', category ? { 'data-category': category } : {},
    text, [anchor, ...metadata]);
}

function collectFrom(cards, { query = '' } = {}) {
  const body = new Element('body', {}, `Free items remaining 3 items\n${cards.map(c => c.innerText).join('\n')}`, cards);
  const context = vm.createContext({
    document: { body, addEventListener() {},
      querySelectorAll: selector => selector === 'a[href*="/ip/"]' ? body.querySelectorAll(selector) : [],
      querySelector: () => null },
    chrome: { storage: { local: { async get() { return {}; } } }, runtime: {
      id: 'reviewer-fixture-extension',
      onMessage: { addListener() {} }, async sendMessage(message) {
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
  vm.runInContext(`${sources}\n;globalThis.collected = collect();`, context);
  return JSON.parse(JSON.stringify(context.collected));
}

test('collection preserves explicit product department and separates promotional badges', () => {
  const [item] = collectFrom([card('10000001', { category: '  Home > Furniture  ',
    badge: 'New arrival', title: 'Wooden bedside table' })]);
  assert.equal(item.category, 'Home > Furniture');
  assert.equal(item.badge, 'new arrival');
  assert.equal(item.title, 'Wooden bedside table');
  assert.equal(item.available, true);
  assert.equal(item.value_usd, 25);
  assert.equal(item.claims_remaining, 3);
  assert.equal(item.source, 'extension');
  assert.equal(item.url, 'https://www.walmart.com/ip/Useful-product/10000001');
});

test('a search keyword or promotional badge is not a product category', () => {
  const [item] = collectFrom([card('10000001', { badge: 'Rollback' })], { query: 'furniture' });
  assert.equal(item.category, '');
  assert.equal(item.badge, 'rollback');
  assert.equal(item.query, 'furniture');
});

test('schema category metadata stays with its own card', () => {
  const items = collectFrom([
    card('10000001', { metadata: [new Element('meta', { itemprop: 'category', content: 'Electronics' })] }),
    card('10000002', { metadata: [new Element('span', { 'data-department': 'Pets' }, 'Pets')] })
  ]);
  assert.deepEqual(items.map(item => item.category), ['Electronics', 'Pets']);
});

test('collection does not inherit category or stock text from a multi-card ancestor', () => {
  const first = card('10000001');
  const second = card('10000002', { outOfStock: true, category: 'Furniture' });
  const results = new Element('div', { 'data-category': 'Unrelated department' },
    `${first.innerText}\n${second.innerText}`, [first, second]);
  const items = collectFrom([results]);
  assert.equal(items[0].category, '');
  assert.equal(items[0].available, true);
  assert.equal(items[1].category, 'Furniture');
  assert.equal(items[1].available, false);
});

test('an unknown-value card cannot borrow a neighboring product value, category, image or stock', () => {
  const first = card('10000001', { title: 'Wireless headphones' });
  first.innerText = 'Wireless headphones';
  const second = card('10000002', { title: 'Wooden cabinet', value: 89,
    outOfStock: true, category: 'Furniture', metadata: [new Element('img', {
      src: 'https://i5.walmartimages.com/asr/cabinet.jpeg'
    })] });
  const results = new Element('div', {}, `${first.innerText}\n${second.innerText}`, [first, second]);
  const items = collectFrom([results]);
  assert.deepEqual(items.map(item => [item.item_id, item.value_usd, item.category,
    item.image_url, item.available]), [
    ['ip-10000001', null, '', '', true],
    ['ip-10000002', 89, 'Furniture', 'https://i5.walmartimages.com/asr/cabinet.jpeg', false]
  ]);
});

test('multiple links to the same product retain their single-card evidence', () => {
  const product = card('10000001', { value: 49, category: 'Electronics', outOfStock: true });
  const secondLink = new Element('a', { href: '/ip/10000001' }, 'View item');
  secondLink.parentElement = product;
  product.children.push(secondLink);
  const items = collectFrom([product]);
  assert.deepEqual(items.map(item => [item.item_id, item.value_usd, item.category, item.available]),
    [['ip-10000001', 49, 'Electronics', false]]);
});

test('different variant IDs keep their own anchors but do not assume shared card evidence', () => {
  const product = card('10000001', { title: 'Blue headphones', value: 49, category: 'Electronics' });
  const variant = new Element('a', { href: '/ip/10000002', 'aria-label': 'Red headphones' }, 'Red headphones');
  variant.parentElement = product;
  product.children.push(variant);
  const items = collectFrom([product]);
  assert.deepEqual(items.map(item => [item.item_id, item.title, item.value_usd, item.category]),
    [['ip-10000001', 'Blue headphones', null, ''], ['ip-10000002', 'Red headphones', null, '']]);
});

test('a single marked primary product retains its card evidence alongside other variant IDs', () => {
  const product = card('10000001', { title: 'Blue headphones', value: 49, category: 'Electronics' });
  for (const link of [
    new Element('a', { href: '/ip/10000001', 'aria-label': 'View item for Blue headphones' }, 'View item'),
    new Element('a', { href: '/ip/10000002', 'aria-label': 'Red, variant on Blue headphones' }, 'Red')
  ]) {
    link.parentElement = product;
    product.children.push(link);
  }
  const items = collectFrom([product]);
  assert.deepEqual(items.map(item => [item.item_id, item.value_usd, item.category]),
    [['ip-10000001', 49, 'Electronics'], ['ip-10000002', null, '']]);
});

test('one primary action cannot authorize borrowing evidence from an unmarked neighboring product', () => {
  const first = card('10000001', { title: 'Wireless headphones' });
  first.innerText = 'Wireless headphones';
  const action = new Element('a', { href: '/ip/10000001',
    'aria-label': 'View item for Wireless headphones' }, 'View item');
  action.parentElement = first;
  first.children.push(action);
  const second = card('10000002', { title: 'Wooden cabinet', value: 89, category: 'Furniture', outOfStock: true,
    metadata: [new Element('img', { src: 'https://i5.walmartimages.com/asr/cabinet.jpeg' })] });
  const results = new Element('div', {}, `${first.innerText}\n${second.innerText}`, [first, second]);
  const items = collectFrom([results]);
  assert.deepEqual(items.map(item => [item.item_id, item.value_usd, item.category,
    item.image_url, item.available]), [
    ['ip-10000001', null, '', '', true],
    ['ip-10000002', 89, 'Furniture', 'https://i5.walmartimages.com/asr/cabinet.jpeg', false]
  ]);
});

test('variant labels must identify the exact marked primary title on every other product link', () => {
  for (const label of ['Red, variant on a different product', 'Red, variant on Blue headphones extra']) {
    const product = card('10000001', { title: 'Blue headphones', value: 49, category: 'Electronics' });
    for (const link of [
      new Element('a', { href: '/ip/10000001', 'aria-label': 'View item for Blue headphones' }, 'View item'),
      new Element('a', { href: '/ip/10000002', 'aria-label': label }, 'Red')
    ]) {
      link.parentElement = product;
      product.children.push(link);
    }
    assert.equal(collectFrom([product])[0].value_usd, null, label);
  }
});

test('multiple marked primary products cannot make a mixed wrapper into a single card', () => {
  const first = card('10000001', { title: 'Wireless headphones' });
  first.innerText = 'Wireless headphones';
  const second = card('10000002', { title: 'Wooden cabinet', value: 89, category: 'Furniture', outOfStock: true });
  for (const [product, id, title] of [[first, '10000001', 'Wireless headphones'],
    [second, '10000002', 'Wooden cabinet']]) {
    const action = new Element('a', { href: `/ip/${id}`, 'aria-label': `View item for ${title}` }, 'View item');
    action.parentElement = product;
    product.children.push(action);
  }
  const results = new Element('div', {}, `${first.innerText}\n${second.innerText}`, [first, second]);
  const items = collectFrom([results]);
  assert.deepEqual(items.map(item => [item.item_id, item.value_usd, item.category, item.available]),
    [['ip-10000001', null, '', true], ['ip-10000002', 89, 'Furniture', false]]);
});

test('out-of-stock cards remain observations for delayed historical delivery', () => {
  const items = collectFrom([card('10000001', { outOfStock: true }), card('10000002')]);
  assert.deepEqual(items.map(item => [item.item_id, item.available]),
    [['ip-10000001', false], ['ip-10000002', true]]);
});

test('collection includes a card-local Walmart CDN product image without tracking parameters', () => {
  const image = new Element('img', { alt: 'Useful reviewer product',
    src: 'https://i5.walmartimages.com/asr/product-image.jpeg?width=300&tracking=removed#part' });
  const [item] = collectFrom([card('10000001', { image }), card('10000002')]);
  assert.equal(item.image_url, 'https://i5.walmartimages.com/asr/product-image.jpeg');
  assert.equal(collectFrom([card('10000002')])[0].image_url, '');
});

test('collection never exports inline images, arbitrary hosts or non-product CDN paths', () => {
  for (const src of ['data:image/png;base64,private', 'https://tracker.example/image.png',
    'http://i5.walmartimages.com/asr/image.jpeg', 'https://i5.walmartimages.com/dfw/tracker.gif',
    'https://i5.walmartimages.com.evil.example/asr/image.jpeg']) {
    const [item] = collectFrom([card('10000001', { image: new Element('img', { src }) })]);
    assert.equal(item.image_url, '', src);
  }
});
