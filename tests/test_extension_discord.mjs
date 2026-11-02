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

function card(id, { title = 'Useful reviewer product', badge = '', category = '',
  outOfStock = false, metadata = [], image = null, value = 25 } = {}) {
  const anchor = new Element('a', { href: `/ip/Useful-product/${id}?private=removed`,
    'aria-label': `${badge} ${title}` }, `${badge}\n${title}`.trim(), image ? [image] : []);
  const text = `${badge}\n${title}\nFree (Valued at $${value})${outOfStock ? '\nOut of stock' : ''}`;
  return new Element('section', category ? { 'data-category': category } : {},
    text, [anchor, ...metadata]);
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
  assert.equal(Object.hasOwn(item, 'eligibility_url'), false);
});

function evidenceFrom(context) {
  return context.messages.filter(message => message.type === 'reviewer:diagnostic' &&
    message.event === 'card-extraction').map(message => JSON.parse(JSON.stringify(message.details)));
}

test('actual extraction logs bounded card decisions without changing emitted items or exposing page data', () => {
  const known = card('10000001', { title: 'Private title must not enter diagnostics' });
  const unknown = card('10000002');
  unknown.innerText = 'Unknown price product\nFree (Valued at —)';
  const stock = card('10000003', { outOfStock: true });
  const variant = new Element('a', { href: '/ip/10000004',
    'aria-label': 'Red, variant on Another title' }, 'Red');
  const unavailable = new Element('a', { href: '/ip/10000005' }, 'Product without a safe card');
  const retail = card('10000006');
  retail.innerText = 'Retail product\n$25 (Valued at $25)';
  const context = contentFrom([known, unknown, stock, variant, unavailable, retail], { query: 'private@example.test' });
  vm.runInContext(`role.generation = 42; role.auxiliary = {lane:'keywords'};
    globalThis.original = collect(); globalThis.instrumented = collect(null, true);`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.instrumented)), JSON.parse(JSON.stringify(context.original)));
  const [event] = evidenceFrom(context);
  assert.equal(event.items, 3);
  assert.equal(event.acceptedCount, 2);
  assert.equal(event.rejectedCount, 4);
  assert.equal(event.variantCount, 1);
  assert.equal(event.outOfStockCount, 1);
  assert.equal(event.missingValueCount, 1);
  assert.equal(event.lane, 'keywords');
  assert.equal(event.generation, 42);
  assert.equal(event.page, 1);
  assert.equal(Number.isSafeInteger(event.documentId), true);
  const byId = Object.fromEntries(event.samples.map(sample => [sample.itemId, sample]));
  assert.deepEqual(byId['ip-10000002'], { itemId: 'ip-10000002', decision: 'accepted',
    cardFound: true, free: true, outOfStock: false, valueCents: null });
  assert.equal(byId['ip-10000003'].decision, 'out-of-stock');
  assert.equal(byId['ip-10000003'].outOfStock, true);
  assert.deepEqual(byId['ip-10000004'], { itemId: 'ip-10000004', decision: 'variant',
    cardFound: false, free: null, outOfStock: null, valueCents: null });
  assert.equal(byId['ip-10000005'].decision, 'card-unavailable');
  assert.equal(byId['ip-10000006'].decision, 'not-free');
  assert.equal(byId['ip-10000001'].valueCents, 2500);
  const wire = JSON.stringify(event);
  for (const secret of ['Private title', 'private@example.test', 'Free items remaining', 'https:', 'Useful-product']) {
    assert.equal(wire.includes(secret), false, secret);
  }
});

test('probe collection is silent and exact repeated extraction evidence is deduplicated by context', () => {
  const context = contentFrom([card('10000001')]);
  vm.runInContext('collect(); collect(); collect(new Set());', context);
  assert.equal(evidenceFrom(context).length, 0);
  vm.runInContext('collect(null, true); collect(null, true);', context);
  assert.equal(evidenceFrom(context).length, 1);
  vm.runInContext('role.generation += 1; collect(null, true);', context);
  assert.equal(evidenceFrom(context).length, 2);
  vm.runInContext(`location.search='?page=2'; collect(null, true);`, context);
  assert.equal(evidenceFrom(context).length, 3);
  assert.equal(evidenceFrom(context).at(-1).page, 2);
});

test('extraction evidence samples prioritize price and rejection incidents and never exceed twelve', () => {
  const cards = Array.from({ length: 60 }, (_, index) => card(String(10000000 + index)));
  const unknown = card('20000001');
  unknown.innerText = 'Unknown value product\nFree (Valued at —)';
  const stock = card('20000002', { outOfStock: true });
  const variants = Array.from({ length: 5 }, (_, index) => new Element('a', {
    href: `/ip/${20000003 + index}`, 'aria-label': `Color ${index}, variant on Main product`
  }, `Color ${index}`));
  const context = contentFrom([...cards, unknown, stock, ...variants]);
  vm.runInContext('collect(null, true);', context);
  const [event] = evidenceFrom(context);
  assert.equal(event.samples.length, 12);
  assert.equal(event.acceptedCount, 61);
  assert.equal(event.rejectedCount, 6);
  for (const id of ['ip-20000001', 'ip-20000002', ...variants.map((_, index) => `ip-${20000003 + index}`)]) {
    assert.equal(event.samples.some(sample => sample.itemId === id), true, id);
  }
});

test('a duplicated variant link counts one decision and excluded IDs are not reconsidered', () => {
  const variant = () => new Element('a', { href: '/ip/10000002',
    'aria-label': 'Red, variant on Main product' }, 'Red');
  const context = contentFrom([card('10000001'), variant(), variant()]);
  vm.runInContext(`collect(new Set(['ip-10000001']), true);`, context);
  const [event] = evidenceFrom(context);
  assert.equal(event.items, 0);
  assert.equal(event.variantCount, 1);
  assert.equal(event.rejectedCount, 1);
  assert.equal(event.samples.length, 1);
  assert.equal(event.samples[0].itemId, 'ip-10000002');
});

test('card collection requires an explicit Free offer next to its retail valuation', () => {
  for (const offer of ['$25 (Valued at $25)', 'Valued at $25',
    'Free shipping (Valued at $25)', 'Free shipping\n(Valued at $25)',
    'Free delivery\nValued at $25', 'Buy one get one free\n(Valued at $25)',
    'Tax-free\nValued at $25']) {
    const product = card('10000001');
    product.innerText = `Useful reviewer product\n${offer}`;
    assert.deepEqual(collectFrom([product]), [], offer);
  }
});

test('visible Free offers retain valuation evidence across parentheses and line breaks', () => {
  for (const offer of ['Free (Valued at $49.99)', 'Free\n(Valued at $49.99)',
    'Free\nValued at\n$49.99', 'FREE (\nValued at $49.99\n)']) {
    const product = card('10000001');
    product.innerText = `Useful reviewer product\n${offer}`;
    const [item] = collectFrom([product]);
    assert.equal(item?.value_usd, 49.99, offer);
    assert.equal(item.available, true);
  }
});

test('Free cards with an unreadable retail amount remain available with an unknown value', () => {
  const product = card('10000001');
  product.innerText = 'Useful reviewer product\nFree (Valued at —)';
  const [item] = collectFrom([product]);
  assert.equal(item.value_usd, null);
  assert.equal(item.available, true);
  product.innerText = 'Useful reviewer product\nFree';
  assert.deepEqual(collectFrom([product]), [], 'An isolated Free word has no card-local valuation boundary');
});

test('hidden Free text cannot replace the visible card offer', () => {
  const product = card('10000001');
  product.innerText = 'Useful reviewer product\n$25 (Valued at $25)';
  product.textContent = `${product.innerText}\nFree (Valued at $25)`;
  assert.deepEqual(collectFrom([product]), []);
});

test('page message deadlines cover local ingest batches without product-page request time', () => {
  const context = contentFrom([]);
  context.deadlines = [];
  vm.runInContext(`documentSession.request = (_invoke, timeout) => deadlines.push(timeout);
    for (const length of [0, 1, 200, 201, 400, 401]) {
      sendDocumentMessage({ type: 'reviewer:page', items: Array(length).fill({}) });
    }
    sendDocumentMessage({ type: 'reviewer:checkpoint' });`, context);
  assert.deepEqual(Array.from(context.deadlines), [45_000, 45_000, 45_000, 60_000, 60_000, 75_000, 15_000]);
});

test('ingest retries retain their original backoff without product-check retry hints', () => {
  const context = contentFrom([]);
  vm.runInContext(`Math.random = () => 0.5;
    ingestRetryAttempt = 1;
    globalThis.firstRetry = retryDelay(true, 1000);
    ingestRetryAttempt = 4;
    globalThis.laterRetry = retryDelay(true, 1000);
    globalThis.nonretryable = retryDelay(false, 1000);`, context);
  assert.equal(context.firstRetry, 2_000);
  assert.equal(context.laterRetry, 30_000);
  assert.equal(context.nonretryable, 60_000);
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

test('a card without its own retail evidence is omitted rather than borrowing from its neighbor', () => {
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

test('an ambiguous card with unmarked different IDs emits no product candidates', () => {
  const product = card('10000001', { title: 'Blue headphones', value: 49, category: 'Electronics' });
  const variant = new Element('a', { href: '/ip/10000002', 'aria-label': 'Red headphones' }, 'Red headphones');
  variant.parentElement = product;
  product.children.push(variant);
  const items = collectFrom([product]);
  assert.deepEqual(items, []);
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
    [['ip-10000001', 49, 'Electronics']]);
});

test('a variant swatch appearing before its primary link never becomes a product', () => {
  const primary = new Element('a', { href: '/ip/10000001',
    'aria-label': 'View item for Blue headphones' }, 'View item');
  const swatch = new Element('a', { href: '/ip/10000002',
    'aria-label': 'Red, variant on Blue headphones' }, 'Red');
  const product = new Element('section', { 'data-category': 'Electronics' },
    'Blue headphones\nFree (Valued at $49)\nOut of stock', [swatch, primary]);
  assert.deepEqual(collectFrom([product]).map(item =>
    [item.item_id, item.title, item.value_usd, item.available]),
  [['ip-10000001', 'Blue headphones', 49, false]]);
});

test('an isolated variant link cannot acquire eligibility from a retail-value wrapper', () => {
  const swatch = new Element('a', { href: '/ip/10000002',
    'aria-label': 'Red, variant on Blue headphones' }, 'Red');
  const wrapper = new Element('section', {}, 'Free (Valued at $49)', [swatch]);
  assert.deepEqual(collectFrom([wrapper]), []);
});

test('an unrelated product link outside an actual card is not emitted with default availability', () => {
  const unrelated = new Element('a', { href: '/ip/10000003' }, 'Other products you might like');
  assert.deepEqual(collectFrom([unrelated]), []);
});

test('multiple retail values make a product wrapper ambiguous', () => {
  const product = card('10000001');
  product.innerText += '\nAnother item: Free (Valued at $59)';
  assert.deepEqual(collectFrom([product]), []);
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
    assert.deepEqual(collectFrom([product]), [], label);
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
    [['ip-10000002', 89, 'Furniture', false]]);
});

test('out-of-stock cards retain false availability for notifier filtering', () => {
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

function capturedElement(value) {
  assert.ok(Array.isArray(value.children), 'Captured DOM must not be truncated');
  return new Element(value.tag, value.attributes, value.text, value.children.map(capturedElement));
}

test('captured Sofa card retains the selected Free primary and excludes image-alt variants', () => {
  const capture = JSON.parse(fs.readFileSync(
    new URL('./fixtures/reviewer-sofa-card-20261028.json', import.meta.url), 'utf8'));
  const items = collectFrom(capture.cards.map(capturedElement));
  const primary = items.find(item => item.item_id === 'ip-20761609702');
  assert.ok(primary, 'The selected visible Free primary must survive its image-labeled swatches');
  assert.equal(primary.available, true);
  assert.equal(primary.value_usd, 35.99);
  for (const id of ['20786920734', '20844210988', '20827667279', '19385610574', '19341061402']) {
    assert.equal(items.some(item => item.item_id === `ip-${id}`), false,
      'Variant links must not inherit the selected card\'s Free evidence');
  }
});

for (const outOfStock of [false, true]) {
  test(`image-alt swatches before a primary retain its availability: outOfStock=${outOfStock}`, () => {
    const title = 'Blue headphones';
    const swatch = new Element('a', { href: '/ip/10000002' }, '', [
      new Element('img', { alt: `Red, variant on ${title}` })
    ]);
    const primary = new Element('a', { href: '/ip/10000001',
      'aria-label': `View item for ${title}` }, 'View item');
    const product = new Element('section', {},
      `${title}\nFree (Valued at $49)${outOfStock ? '\nOut of stock' : ''}`, [swatch, primary]);
    const items = collectFrom([product]);
    assert.deepEqual(items.map(item => [item.item_id, item.available]),
      [['ip-10000001', !outOfStock]]);
  });
}

test('an isolated image-alt variant never becomes an independent Free item', () => {
  const swatch = new Element('a', { href: '/ip/10000002' }, '', [
    new Element('img', { alt: 'Red, variant on Blue headphones' })
  ]);
  const wrapper = new Element('section', {}, 'Free (Valued at $49)', [swatch]);
  assert.deepEqual(collectFrom([wrapper]), []);
});

for (const label of ['', 'Red, variant on Different headphones']) {
  test(`missing or mismatched image-alt variant evidence remains rejected: ${label || '(missing)'}`, () => {
    const primary = new Element('a', { href: '/ip/10000001',
      'aria-label': 'View item for Blue headphones' }, 'View item');
    const swatch = new Element('a', { href: '/ip/10000002' }, '', [
      new Element('img', { alt: label })
    ]);
    const product = new Element('section', {}, 'Blue headphones\nFree (Valued at $49)', [primary, swatch]);
    assert.deepEqual(collectFrom([product]), []);
  });
}
