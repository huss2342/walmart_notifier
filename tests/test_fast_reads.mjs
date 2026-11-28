import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const read = name => fs.readFileSync(new URL(`../extension/${name}`, import.meta.url), 'utf8');
const contentSources = ['sweep-modes.js', 'runtime-policy.js', 'verification-observation.js', 'page-controls.js',
  'document-session.js', 'live-observer.js', 'content.js'].map(read).join('\n');

function product(id, { name = `Product ${id}`, price = '149.99', free = true, stock = 'IN_STOCK' } = {}) {
  return {
    __typename: 'Product', usItemId: String(id), name, canonicalUrl: `/ip/Slug-${id}/${id}?athbdg=1`,
    imageInfo: { thumbnailUrl: `https://i5.walmartimages.com/seo/Item-${id}.jpeg?odnHeight=180` },
    availabilityStatusV2: { value: stock },
    priceInfo: { priceDetails: { priceLines: [{ lineType: free ? 'FREE_PRICE' : 'PRICE',
      values: [{ key: 'PRICE', value: price }] }] } }
  };
}

function page({ sort = 'new', pageNumber = 1, items = [] } = {}) {
  const data = { props: { pageProps: {
    initialSearchQueryVariables: { query: '', sort, page: pageNumber },
    initialData: { searchResult: { itemStacks: [{ items: [{ __typename: 'AdPlaceholder' }, ...items] }] } }
  } } };
  return `<html><script id="__NEXT_DATA__" type="application/json">${JSON.stringify(data)}</script></html>`;
}

function reader(respond) {
  const context = vm.createContext({
    document: { body: { innerText: '' }, addEventListener() {}, querySelectorAll: () => [], querySelector: () => null },
    chrome: { storage: { local: { async get() { return {}; } } }, runtime: {
      id: 'fixture', onMessage: { addListener() {} },
      async sendMessage(message) { return message.type === 'reviewer:role' ? new Promise(() => {}) : { ok: true }; } } },
    location: { origin: 'https://www.walmart.com', pathname: '/reviews/claim-product', search: '',
      href: 'https://www.walmart.com/reviews/claim-product' },
    window: {}, URL, URLSearchParams, MutationObserver: class { observe() {} disconnect() {} },
    setTimeout() {}, clearTimeout() {}, setInterval() {}, console,
    fetch: async url => respond(url)
  });
  vm.runInContext(contentSources, context);
  const run = name => url => vm.runInContext(`${name}(${JSON.stringify(url)})`, context)
    .then(value => JSON.parse(JSON.stringify(value)));
  return Object.assign(run('fastRead'), { context: run('researchRead') });
}

const ok = (html, url = 'https://www.walmart.com/reviews/claim-product?sort=new') =>
  ({ status: 200, url, text: async () => html });

test('a research read summarises every price field of a reviewer list page and nothing else', async () => {
  const fastRead = reader(() => ok(page({ items: [{ ...product(21399965272, { name: 'Faux Fur Coat', price: '159.99' }),
    price: 39.99, variantCount: 5, variantCriteria: [{ name: 'Size' }],
    priceInfo: { linePrice: '$39.99', minPrice: 29.99, priceRangeString: '$29.99 - $159.99',
      currentPrice: { price: 39.99, priceString: '$39.99' },
      priceDetails: { priceLines: [{ lineType: 'FREE_PRICE', values: [{ key: 'PRICE', value: '159.99' }] }] } } }] })));
  const context = vm.createContext({});
  const research = url => fastRead.context(url);
  const result = await research('https://www.walmart.com/reviews/claim-product?sort=price_low&min_price=80&ps=100');
  assert.equal(result.ok, true);
  assert.equal(result.variables.sort, 'new');
  const coat = result.products.find(row => row.usItemId === '21399965272');
  assert.equal(coat.price, 39.99);
  assert.equal(coat.variantCount, 5);
  assert.deepEqual(coat.variants, ['Size']);
  assert.equal(coat.prices.linePrice, '$39.99');
  assert.equal(coat.prices['currentPrice.price'], 39.99);
  assert.equal(coat.prices.priceRangeString, '$29.99 - $159.99');
  assert.deepEqual(coat.lines, [['FREE_PRICE', 'PRICE=159.99']]);
  assert.equal(coat.stock, 'IN_STOCK');
  assert.equal(result.products[0].__typename, 'AdPlaceholder');
  for (const url of ['https://www.walmart.com/ip/21399965272', 'https://evil.example/reviews/claim-product',
    'https://www.walmart.com/reviews/claim-product?bad-key=1']) {
    assert.equal((await research(url)).reason, 'scope', url);
  }
  void context;
});

test('the $80+ list read must be exactly sort=price_high with min_price=79, as Walmart applied it', async () => {
  const listPage = (minPrice, sort = 'price_high') => {
    const data = { props: { pageProps: { initialSearchQueryVariables: { query: '', sort, page: 2, min_price: minPrice },
      initialData: { searchResult: { itemStacks: [{ items: [product(21399965272, { price: '159.99' })] }] } } } } };
    return `<html><script id="__NEXT_DATA__" type="application/json">${JSON.stringify(data)}</script></html>`;
  };
  let html = listPage('79');
  const fastRead = reader(() => ok(html));
  const list = `${HOME}?sort=price_high&min_price=79&page=2`;
  assert.equal((await fastRead(list)).ok, true);
  html = listPage('');
  assert.equal((await fastRead(list)).ok, false, 'a page served without the filter proves nothing');
  // Freshness probes (2.32.7) also read the same list newest first.
  html = listPage('79', 'new');
  assert.equal((await fastRead(`${HOME}?sort=new&min_price=79&page=2`)).ok, true);
  assert.equal((await fastRead(list)).ok, false, 'a price_high read served newest first proves nothing');
  for (const url of [`${HOME}?sort=price_high&min_price=50`, `${HOME}?sort=new&min_price=80`,
    `${HOME}?min_price=79`, `${HOME}?q=sofa&queryNups=sofa&sort=price_high&min_price=79`,
    `${HOME}?q=sofa&queryNups=sofa&sort=new&min_price=79`]) {
    assert.equal((await fastRead(url)).reason, 'scope', url);
  }
});

test('fast read maps free catalogue products exactly like card items', async () => {
  const fastRead = reader(() => ok(page({ items: [
    product(21155107170, { name: 'Electric Scooter', price: '349.99' }),
    product(1234567, { free: false }),
    product(7654321, { stock: 'OUT_OF_STOCK', price: '20.00' })] })));
  const result = await fastRead('https://www.walmart.com/reviews/claim-product?sort=new');
  assert.equal(result.ok, true);
  assert.deepEqual(result.items, [
    { item_id: 'ip-21155107170', title: 'Electric Scooter', value_usd: 349.99,
      url: 'https://www.walmart.com/ip/Slug-21155107170/21155107170', category: '', badge: '',
      image_url: 'https://i5.walmartimages.com/seo/Item-21155107170.jpeg', available: true,
      source: 'extension', claims_remaining: null, query: '',
      stock: { source: 'data', v2: 'in', status: 'none', oos: null, variant: false } },
    { item_id: 'ip-7654321', title: 'Product 7654321', value_usd: 20,
      url: 'https://www.walmart.com/ip/Slug-7654321/7654321', category: '', badge: '',
      image_url: 'https://i5.walmartimages.com/seo/Item-7654321.jpeg', available: false,
      source: 'extension', claims_remaining: null, query: '',
      stock: { source: 'data', v2: 'out', status: 'none', oos: null, variant: false } }]);
  // The read itself reports the page's stock mix and size, for telling a one-item
  // flicker from a page that changed or came back stale.
  assert.deepEqual({ ...result.stock }, { in: 1, out: 1, other: 0 });
  assert.equal(result.bytes > 0, true);
  assert.equal(result.age, undefined);
});

test('a fast read lists every product on its page for the snapshot log, in page order', async () => {
  const fastRead = reader(() => ok(page({ sort: 'price_high', items: [
    { ...product(21155107170, { price: '1,149.99' }), category: { categoryPathId: '0:4044:103150' }, startDate: null },
    product(1234567, { free: false }),
    { ...product(7654321, { stock: 'OUT_OF_STOCK', price: '20.00' }), category: { categoryPathId: '0:1' },
      releaseDate: '2026-11-18', availabilityTime: 'soon<script>' },
    { ...product(7654322, { stock: 'PREORDER', price: '99.00' }),
      category: { path: [{ url: '/cp/home/4044' }, { url: '/browse/home/furniture/4044_103150?povid=x' }] } }] })));
  const result = await fastRead('https://www.walmart.com/reviews/claim-product?sort=price_high');
  assert.equal(result.ok, true);
  // [usItemId, position among products, free price in cents, stock, extra]: the
  // category only for $80+ offers, date-like fields only with a plain value.
  assert.deepEqual(result.snapshot, [
    ['21155107170', 1, 114_999, 'i', { c: '0:4044:103150' }],
    ['1234567', 2, null, 'i'],
    ['7654321', 3, 2_000, 'o', { d: { releaseDate: '2026-11-18' } }],
    ['7654322', 4, 9_900, 'x', { c: '4044_103150' }]]);
  assert.equal(result.items.length, 3, 'the delivered items are unchanged');
});

test('a read reports its stock mix, every Walmart stock field and the CDN age', async () => {
  const html = page({ items: [
    product(21155107170, { price: '149.99' }),
    { ...product(21155107171, { price: '159.99', stock: 'OUT_OF_STOCK' }), availabilityStatus: 'OUT_OF_STOCK',
      isOutOfStock: true, type: 'VARIANT' },
    { ...product(21155107172, { price: '169.99' }), availabilityStatus: 'OUT_OF_STOCK', isOutOfStock: false },
    { ...product(21155107173, { price: '179.99' }), availabilityStatusV2: { value: 'PREORDER' } }] });
  const fastRead = reader(() => ({ ...ok(html), headers: new Map([['age', '37']]) }));
  const result = await fastRead('https://www.walmart.com/reviews/claim-product?sort=new');
  assert.deepEqual({ ...result.stock }, { in: 2, out: 1, other: 1 });
  assert.equal(result.age, 37);
  const byId = Object.fromEntries(result.items.map(item => [item.item_id, JSON.parse(JSON.stringify(item.stock))]));
  assert.deepEqual(byId['ip-21155107171'], { source: 'data', v2: 'out', status: 'out', oos: true, variant: true });
  assert.deepEqual(byId['ip-21155107172'], { source: 'data', v2: 'in', status: 'out', oos: false, variant: false });
  assert.equal(byId['ip-21155107173'].v2, 'other');
});

test('fast read searches keep the query on each item and report the page count', async () => {
  const html = page({ items: [product(21155107170, { name: 'Electric Scooter' })] })
    .replace('"query":""', '"query":"scooter"').replace('"searchResult":{', '"searchResult":{"paginationV2":{"maxPage":2},');
  const fastRead = reader(() => ok(html));
  const result = await fastRead('https://www.walmart.com/reviews/claim-product?q=scooter&queryNups=scooter&sort=new');
  assert.equal(result.ok, true);
  assert.equal(result.totalPages, 2);
  assert.equal(result.items[0].query, 'scooter');
  const mismatch = reader(() => ok(page()));
  assert.equal((await mismatch('https://www.walmart.com/reviews/claim-product?q=scooter&queryNups=scooter&sort=new')).ok, false);
  const scope = reader(() => { throw new Error('must not fetch'); });
  assert.equal((await scope('https://www.walmart.com/reviews/claim-product?q=a&queryNups=b&sort=new')).reason, 'scope');
});

test('fast read refuses unproven or out-of-scope pages and reports bot checks', async () => {
  const unsorted = reader(() => ok(page({ sort: 'best_match' })));
  assert.equal((await unsorted('https://www.walmart.com/reviews/claim-product?sort=new')).ok, false);
  const wrongPage = reader(() => ok(page({ pageNumber: 1 })));
  assert.equal((await wrongPage('https://www.walmart.com/reviews/claim-product?sort=new&page=2')).ok, false);
  const scope = reader(() => { throw new Error('must not fetch'); });
  for (const url of ['https://www.walmart.com/reviews/claim-product?sort=new&q=x&queryNups=y',
    'https://www.walmart.com/ip/123', 'https://example.com/reviews/claim-product?sort=new']) {
    assert.equal((await scope(url)).reason, 'scope');
  }
  const redirected = reader(() => ({ status: 200, url: 'https://www.walmart.com/blocked?url=x', text: async () => '' }));
  assert.equal((await redirected('https://www.walmart.com/reviews/claim-product?sort=new')).blocked, true);
  const captcha = reader(() => ok('<div id="px-captcha">Press &amp; Hold</div>'));
  assert.equal((await captcha('https://www.walmart.com/reviews/claim-product?sort=new')).blocked, true);
  const limited = reader(() => ({ status: 429, url: 'https://www.walmart.com/reviews/claim-product?sort=new',
    text: async () => '' }));
  assert.equal((await limited('https://www.walmart.com/reviews/claim-product?sort=new')).blocked, true);
});

const HOME = 'https://www.walmart.com/reviews/claim-product';

function departmentPage({ catId = '4044', pageNumber = 1, items = [], sort, maxPage = 3, total = 120 } = {}) {
  const data = { props: { pageProps: {
    initialSearchQueryVariables: { query: '', catId, facet: '', page: pageNumber, ...(sort ? { sort } : {}) },
    initialData: { searchResult: { paginationV2: { maxPage },
      itemStacks: [{ meta: { totalItemCount: total }, items: [{ __typename: 'AdPlaceholder' }, ...items] }] } }
  } } };
  return `<html><script id="__NEXT_DATA__" type="application/json">${JSON.stringify(data)}</script></html>`;
}

test('fast read reads one department in its default order and reports its size', async () => {
  const fastRead = reader(() => ok(departmentPage({ pageNumber: 2, maxPage: 25, total: 1840,
    items: [product(21155107170, { name: 'Electric Scooter', price: '349.99' }), product(1234567, { free: false })] })));
  const result = await fastRead(`${HOME}?catId=4044&page=2`);
  assert.equal(result.ok, true);
  assert.equal(result.totalPages, 25);
  assert.equal(result.totalItems, 1840);
  assert.equal(result.products, 2, 'every product counts toward emptiness, free offer or not');
  assert.deepEqual(result.items.map(row => row.item_id), ['ip-21155107170']);
  const empty = reader(() => ok(departmentPage({ maxPage: 0, total: 0 })));
  const none = await empty(`${HOME}?catId=4044`);
  assert.equal(none.ok, true);
  assert.equal(none.products, 0);
});

test('fast read refuses department reads it cannot prove', async () => {
  const scope = reader(() => { throw new Error('must not fetch'); });
  for (const url of [`${HOME}?catId=4044&sort=new`, `${HOME}?catId=4044&q=x&queryNups=x`,
    `${HOME}?catId=40x4`, `${HOME}?catId=4044&catId=4171`, `${HOME}?catId=`]) {
    assert.equal((await scope(url)).reason, 'scope', url);
  }
  const otherDepartment = reader(() => ok(departmentPage({ catId: '4171' })));
  assert.equal((await otherDepartment(`${HOME}?catId=4044`)).ok, false);
  const sorted = reader(() => ok(departmentPage({ sort: 'price_high' })));
  assert.equal((await sorted(`${HOME}?catId=4044`)).ok, false);
  const wrongPage = reader(() => ok(departmentPage({ pageNumber: 1 })));
  assert.equal((await wrongPage(`${HOME}?catId=4044&page=3`)).ok, false);
});

function scheduler(replies, searches = [], clock = Date, onBlocked = async () => {}) {
  const context = vm.createContext({ URL, Date: clock, Promise, Map, Number, Array, Math, String });
  vm.runInContext(`${read('fast-reads.js')}\nthis.FastReads = ReviewerFastReads;`, context);
  const store = {};
  const sent = [];
  const logs = [];
  const asked = [];
  const chrome = {
    storage: { local: { async get(key) { return { [key]: store[key] }; }, async set(values) { Object.assign(store, values); } } },
    alarms: { async get() { return null; }, async create() {} },
    tabs: { async sendMessage(tabId, message) {
      asked.push([tabId, message.url]);
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return reply;
    } }
  };
  const api = context.FastReads.create(chrome, {
    tabIds: async () => [1, 2],
    send: async (items, page, lane) => { sent.push({ items, page, lane }); return { ok: true, summary: { new: items.length } }; },
    paused: async () => false,
    diagnostic: details => logs.push(details),
    queries: async () => searches, onBlocked,
    setTimer: () => 1, clearTimer: () => {}
  });
  return { api, store, sent, logs, asked, FastReads: context.FastReads };
}

const item = id => ({ item_id: `ip-${id}`, available: true, value_usd: 10 });

// A clock-driven harness: every reply comes from respond(url), and steps()
// advances time between runs the way the worker's timer chain would. Hot
// searches are off unless a test passes hotSeeds (null: the built-in seeds,
// pinned and excluded searches too), so the older cadence tests keep proving
// the schedule they were written for.
function harness({ respond = () => ({ ok: true, status: 200, items: [] }), searches = [], departments = [],
  onBlocked = async () => {}, paused = async () => false, send = null, mustSend = undefined, hotSeeds = [],
  hotPinned = hotSeeds === null ? null : [], hotExcluded = hotSeeds === null ? null : [],
  store = {}, start = 1_000_000 } = {}) {
  let now = start;
  const clock = class extends Date { static now() { return now; } };
  const context = vm.createContext({ URL, Date: clock, Promise, Map, Number, Array, Math, String });
  vm.runInContext(`${read('fast-reads.js')}\nthis.FastReads = ReviewerFastReads;`, context);
  const sent = [];
  const logs = [];
  const asked = [];
  const delays = [];
  const records = []; // The page snapshot log (2.32.7): every read and the freshness probes.
  const chrome = {
    storage: { local: { async get(key) { return { [key]: store[key] }; }, async set(values) { Object.assign(store, values); } } },
    alarms: { async get() { return null; }, async create() {} },
    tabs: { async sendMessage(tabId, message) {
      asked.push({ tabId, url: message.url, at: now, what: describe(message.url) });
      return respond(message.url, tabId);
    } }
  };
  const api = context.FastReads.create(chrome, {
    tabIds: async () => [1],
    send: async (items, page, lane, targetIndex) => {
      sent.push({ items, page, lane, at: now, ...(targetIndex !== undefined ? { targetIndex } : {}) });
      return send ? send(items, page, lane) : { ok: true, summary: { new: items.length } }; },
    paused, diagnostic: details => logs.push(details),
    queries: async () => searches, departments: async () => departments, onBlocked,
    ...(mustSend ? { mustSend } : {}), ...(hotSeeds ? { hotSeeds } : {}),
    ...(hotPinned ? { hotPinned } : {}), ...(hotExcluded ? { hotExcluded } : {}),
    setTimer: (_fn, delay) => { delays.push(delay); return 1; }, clearTimer: () => {}, random: () => 0.5,
    snapshot: record => records.push(JSON.parse(JSON.stringify(record)))
  });
  return { api, store, sent, logs, asked, delays, records, FastReads: context.FastReads,
    advance(ms) { now += ms; },
    now() { return now; },
    async steps(count, ms = 2_500) {
      for (let index = 0; index < count; index += 1) { await api.run(); now += ms; }
    } };
}

function describe(url) {
  const parsed = new URL(url);
  const page = Number(parsed.searchParams.get('page') || 1);
  if (parsed.searchParams.get('catId')) return `department:${parsed.searchParams.get('catId')}:${page}`;
  if (parsed.searchParams.get('q')) return `search:${parsed.searchParams.get('q')}:${page}`;
  const high = parsed.searchParams.get('sort') === 'price_high';
  // The min_price=79 list: price order (value80) or newest first (new80, Nov 23).
  if (parsed.searchParams.get('min_price') === '79') return `${high ? 'value80' : 'new80'}:${page}`;
  return `${high ? 'value' : 'newest'}:${page}`;
}

const gaps = (asked, what) => {
  const times = asked.filter(row => row.what === what).map(row => row.at);
  return times.slice(1).map((at, index) => at - times[index]);
};

// price_high page p holds items at lasts[p - 1] + 5 and lasts[p - 1] dollars.
const valueReply = (lasts, totalPages = 25) => url => {
  const what = describe(url);
  if (!what.startsWith('value:')) return { ok: true, status: 200, items: [item(9_000_000)], products: 1 };
  const page = Number(what.split(':')[1]);
  const last = lasts[page - 1] ?? 5;
  return { ok: true, status: 200, totalPages, products: 40,
    items: [{ ...item(1_000_000 + page), value_usd: last + 5 }, { ...item(2_000_000 + page), value_usd: last }] };
};

test('research reads wait only for due $80+ pages, are never ingested and log as their own lane', async () => {
  const h = harness({ respond: (url, tabId) => url.includes('research=1')
    ? { ok: true, status: 200, products: [{ usItemId: '1' }], pagination: { maxPage: 7 } }
    : valueReply([120, 95, 85, 70, 55, 45, 35])(url, tabId) });
  await h.steps(4); // value pages 1-4 first
  const pending = h.api.research('https://www.walmart.com/reviews/claim-product?sort=price_low&research=1');
  await h.steps(24); // the $80+ lists' first reads go first
  const reply = await pending;
  assert.equal(reply.ok, true);
  const at = h.asked.findIndex(row => row.url.includes('research=1'));
  assert.ok(at >= 4 && at <= 24, `served right after the due $80+ pages: ${at}`);
  assert.ok(gaps(h.asked, 'value:1').every(gap => gap <= 12_500), 'never delays value page 1');
  assert.equal(h.sent.some(row => row.lane === 'research'), false, 'never ingested');
  assert.deepEqual(h.logs.filter(row => row.lane === 'research').map(row => [row.outcome, row.items, row.totalPages]),
    [['items', 1, 7]]);
  assert.equal(await h.api.research('https://www.walmart.com/ip/123'), null);
});

test('price_high page 1 is read every 10 s, every other $80+ page every 30 s, the page after them every 2 min', async () => {
  const h = harness({ respond: valueReply([120, 95, 85, 70, 55, 45, 35]) });
  await h.steps(360);
  assert.deepEqual(h.asked.slice(0, 4).map(row => row.what), ['value:1', 'value:2', 'value:3', 'value:4']);
  const page1 = gaps(h.asked, 'value:1');
  assert.ok(page1.length >= 20);
  assert.ok(page1.every(gap => gap >= 10_000 && gap <= 15_000), String(page1));
  for (const page of [2, 3]) {
    const fast = gaps(h.asked, `value:${page}`).slice(1); // after the start-up burst of first reads
    assert.ok(fast.length >= 10 && fast.every(gap => gap >= 30_000 && gap <= 35_000), `page ${page}: ${fast}`);
  }
  // Page 3 is all $80+, so page 4 is checked too, in case a new listing spills
  // an $80+ item onto it: every 2 min while it holds none (30 s until Nov 26;
  // the min_price=79 list shows every $80+ offer at 40 s anyway). Pages after
  // that wait for the pass.
  const page4 = gaps(h.asked, 'value:4');
  assert.ok(page4.length >= 5 && page4.every(gap => gap >= 120_000 && gap <= 125_000), `boundary page: ${page4}`);
  assert.equal(h.FastReads.INTERVALS.valueSpill, 120_000);
  assert.ok(gaps(h.asked, 'value:5').every(gap => gap >= 240_000), 'page 5 waits for the pass');
  assert.ok(h.asked.some(row => row.what === 'value:8'), 'the first page without a $40+ item ends the pass');
  assert.equal(h.asked.some(row => row.what === 'value:9'), false);
});

test('the page after the $80+ block is read every 2 min while it holds none, every 30 s once it holds one', async () => {
  // Nov 26 (2.32.9): page 4 (max $79.99 in production) showed no new $80+ item
  // first in 147 h at 30 s; the min_price=79 list holds every $80+ offer at 40 s.
  const lasts = [120, 95, 85, 70, 55, 45, 35];
  const h = harness({ respond: url => valueReply(lasts)(url) });
  await h.steps(360); // 15 minutes: page 4 holds $75 and $70
  const switchAt = h.now();
  const before = gaps(h.asked, 'value:4');
  assert.ok(before.length >= 5 && before.every(gap => gap >= 120_000 && gap <= 125_000), `spill check: ${before}`);
  lasts[3] = 85; // a new listing puts $90 and $85 offers on page 4
  await h.steps(360);
  const after = h.asked.filter(row => row.at > switchAt);
  const firstHolding = after.find(row => row.what === 'value:4').at;
  const page4 = gaps(after.filter(row => row.at >= firstHolding), 'value:4');
  assert.ok(page4.length >= 8 && page4.every(gap => gap >= 30_000 && gap <= 35_000), `now an $80+ page: ${page4}`);
  // Page 4 is all $80+ now, so page 5 is the check after the block, every 2 min.
  const page5 = gaps(after, 'value:5').slice(1);
  // (Up to 135 s: it waits behind batches of due $80+ pages at times.)
  assert.ok(page5.length >= 2 && page5.every(gap => gap >= 120_000 && gap <= 135_000), `page 5: ${page5}`);
});

test('a page counts as $80+ by any item on it, not by the cheaper items Walmart puts last', async () => {
  // Live November 12 shape: page 1 ends at $64 although page 2 runs $142 to $91.
  const pages = [[206.66, 198.98, 150, 79, 69.59, 63.97], [142.48, 119, 99.99, 90.89, 69.59, 58.8],
    [96, 89.99, 85, 80, 79.99, 57.99], [87.56, 79.99, 75.98, 71.98, 59.99], [75, 66, 50], [48, 44, 39], [38, 30]];
  const h = harness({ respond: url => {
    const what = describe(url);
    if (!what.startsWith('value:')) return { ok: true, status: 200, items: [] };
    const page = Number(what.split(':')[1]);
    return { ok: true, status: 200, totalPages: 25, products: 40,
      items: (pages[page - 1] || [20]).map((price, index) => ({ ...item(3_000_000 + page * 100 + index), value_usd: price })) };
  } });
  await h.steps(120);
  for (const page of [2, 3, 4]) {
    assert.ok(gaps(h.asked, `value:${page}`).slice(1).every(gap => gap <= 35_000), `page ${page} stays in the fast zone`);
  }
  assert.ok(gaps(h.asked, 'value:5').every(gap => gap >= 240_000), 'page 5 holds no $80+ item');
  assert.ok(h.asked.some(row => row.what === 'value:7'));
  assert.equal(h.asked.some(row => row.what === 'value:8'), false, 'page 7 holds no $40+ item');
});

test('pages under $80 are read in one top-down pass, so a shifting list cannot hide an item', async () => {
  // A live price_high list: page p shows positions 40(p-1) .. 40p-1. Pages 1-4
  // can hold $80+ items; pages 5-9 run from $80 down to the $40 boundary.
  const listing = Array.from({ length: 600 }, (_, index) => ({ item_id: `ip-${7_000_000 + index}`,
    available: true, value_usd: index < 120 ? 300 - index : 80 - (index - 120) * 0.2 }));
  const passes = [];
  let inserted = 0;
  const h = harness({ respond: url => {
    const what = describe(url);
    if (!what.startsWith('value:')) return { ok: true, status: 200, items: [] };
    const page = Number(what.split(':')[1]);
    if (page >= 5) {
      if (page === 5) {
        // Every item already on pages 5+ and worth $40 or more must be seen in this pass.
        passes.push({ expected: listing.slice(160).filter(entry => entry.value_usd >= 40)
          .map(entry => entry.item_id), seen: new Set(), pages: [] });
      }
      // A new $79 listing arrives before each pass read, pushing everything below it down.
      listing.splice(165, 0, { item_id: `ip-${8_000_000 + inserted++}`, available: true, value_usd: 79 });
    }
    const items = listing.slice((page - 1) * 40, page * 40);
    if (page >= 5) {
      passes.at(-1).pages.push({ page, at: h.now() });
      items.forEach(entry => passes.at(-1).seen.add(entry.item_id));
    }
    return { ok: true, status: 200, totalPages: 25, products: items.length, items };
  } });
  await h.steps(200); // 500 s: two complete passes
  const complete = passes.slice(0, -1);
  assert.ok(complete.length >= 1);
  for (const pass of complete) {
    assert.deepEqual(pass.pages.map(row => row.page).slice(0, 5), [5, 6, 7, 8, 9], 'top-down, no gaps');
    // Back to back except for the $80+ pages, which always go first.
    assert.ok(pass.pages.at(-1).at - pass.pages[0].at <= pass.pages.length * 8_000, 'back to back');
    assert.ok(pass.expected.every(id => pass.seen.has(id)), 'shifted items are still seen in the same pass');
  }
});

test('a pass runs top-down from the $80 boundary even right after that boundary moves', async () => {
  let lasts = [120, 95, 85, 70, 60, 55, 50, 45, 35];
  const h = harness({ respond: url => valueReply(lasts)(url) });
  await h.steps(30); // pages 1-4 fast, a first pass over pages 5-9 done
  lasts = [120, 70, 60, 58, 55, 50, 45, 42, 35]; // page 2 now holds no $80+ item
  const before = h.asked.length;
  await h.steps(160); // about 6.5 minutes
  // Page 2 is now the fast zone's boundary check (page 1 is all $80+); the pass
  // must still run 3, 4, 5 ... in order even though pages 3-4 were just fast.
  const deep = h.asked.slice(before).filter(row => row.what.startsWith('value:') &&
    Number(row.what.split(':')[1]) >= 3).map(row => Number(row.what.split(':')[1]));
  // (A last fast read of page 3 can come before page 2's re-read moves the boundary.)
  const start = deep.findIndex((page, index) => page === 3 && deep[index + 1] === 4);
  assert.deepEqual(deep.slice(start, start + 8), [3, 4, 5, 6, 7, 8, 9, 10], String(deep));
});

// The min_price=79 list: page p holds the listed values; pages 5 and 7 hold none at $80+.
const value80Reply = (pages = { 1: [206, 79], 2: [142, 60], 3: [101, 50], 4: [96, 40], 5: [70, 30],
  6: [119.99, 20], 7: [32, 5] }, totalPages = 7) => url => {
  const what = describe(url);
  if (what.startsWith('value80:')) {
    const page = Number(what.split(':')[1]);
    return { ok: true, status: 200, totalPages, products: 40, items: (pages[page] || []).map((price, index) =>
      ({ ...item(5_000_000 + page * 10 + index), value_usd: price })) };
  }
  return valueReply([120, 95, 85, 70, 55, 45, 35])(url);
};

test('the min_price=79 list is read page by page: $80+ pages every 40 s, the rest every 3 min', async () => {
  const h = harness({ respond: value80Reply() });
  await h.steps(400, 1_600); // about 10.7 minutes
  for (const page of [1, 2, 3, 4, 6]) {
    const late = gaps(h.asked, `value80:${page}`).slice(2);
    assert.ok(late.length >= 8 && late.every(gap => gap >= 40_000 && gap <= 48_000), `page ${page}: ${late}`);
  }
  for (const page of [5, 7]) {
    assert.ok(gaps(h.asked, `value80:${page}`).slice(1).every(gap => gap >= 180_000), `page ${page} is idle`);
  }
  assert.equal(h.asked.some(row => row.what === 'value80:8'), false, 'the list ends at its last page');
  assert.equal(new URL(h.asked.find(row => row.what === 'value80:2').url).search,
    '?sort=price_high&min_price=79&page=2');
  assert.ok(gaps(h.asked, 'value:1').slice(4).every(gap => gap <= 12_000), 'price_high page 1 stays at 10 s');
  const logged = h.logs.find(row => row.lane === 'value80' && row.page === 6);
  assert.equal(logged.count, 1, 'each read logs how many $80+ offers its page holds');
  assert.ok(h.sent.some(row => row.lane === 'value80' && row.items.some(entry => entry.value_usd === 119.99)),
    'its offers are ingested like any other read');
});

// Nov 28: 3 of 265 new $80+ items turned up first at position 1 of the first or
// second page after the $80+ block, ~3 min late; those two pages are read at 40 s.
test('the two min_price=79 pages after the $80+ block are read every 40 s, the last page stays idle', async () => {
  const h = harness({ respond: value80Reply({ 1: [206, 79], 2: [142, 60], 3: [101, 50], 4: [96, 40], 5: [70, 30],
    6: [70, 20], 7: [32, 5] }) });
  await h.steps(400, 1_600); // about 10.7 minutes
  for (const page of [1, 2, 3, 4, 5, 6]) {
    const late = gaps(h.asked, `value80:${page}`).slice(2);
    assert.ok(late.length >= 8 && late.every(gap => gap >= 40_000 && gap <= 48_000), `page ${page}: ${late}`);
  }
  assert.ok(gaps(h.asked, 'value80:7').slice(1).every(gap => gap >= 180_000), 'the last page is idle');
});

test('a page that held back a restock is re-read 10 s later and re-sent even when unchanged', async () => {
  let holds = 1;
  let awaiting = false;
  const h = harness({ respond: value80Reply(),
    // The first send of page 2 holds a restock back; the watch then awaits it.
    send: (items, page, lane) => {
      if (lane === 'value80' && page === 2 && holds > 0) { holds -= 1; awaiting = true; return { ok: true, held: 1 }; }
      if (lane === 'value80' && page === 2) awaiting = false;
      return { ok: true, summary: { new: 0 } };
    },
    mustSend: items => awaiting && items.some(entry => entry.item_id === 'ip-5000020') });
  await h.steps(200, 1_600); // about 5.3 minutes
  const reads = h.asked.filter(row => row.what === 'value80:2').map(row => row.at);
  const sends = h.sent.filter(row => row.lane === 'value80' && row.page === 2).map(row => row.at);
  const heldAt = sends[0];
  const next = reads.find(at => at > heldAt);
  assert.ok(next - heldAt >= 10_000 && next - heldAt <= 12_000, `re-read ${next - heldAt} ms after the hold, not 40 s`);
  assert.equal(sends[1], next, 'the unchanged page is sent again so the hold is settled');
  assert.ok(sends.length <= 3, `then unchanged pages are not re-sent every read (${sends.length})`);
  const later = reads.filter(at => at > next);
  assert.ok(later.slice(1).every((at, index) => at - later[index] >= 40_000), 'and it returns to 40 s');
});

test('an unchanged page holding an awaited restock is sent on every read until it is settled', async () => {
  let awaiting = false;
  const h = harness({ respond: value80Reply(), mustSend: items => awaiting &&
    items.some(entry => entry.item_id === 'ip-5000020') });
  await h.steps(60, 1_600);
  const before = h.sent.filter(row => row.lane === 'value80' && row.page === 2).length;
  assert.equal(before, 1, 'unchanged reads are not re-sent');
  awaiting = true; // e.g. a tab read of another lane held this offer back
  await h.steps(60, 1_600);
  const during = h.sent.filter(row => row.lane === 'value80' && row.page === 2).length - before;
  const reads = h.asked.filter(row => row.what === 'value80:2' && row.at > h.now() - 96_000).length;
  assert.ok(during >= 2 && during === reads, `every read sent while awaited (${during}/${reads})`);
});

test('the min_price=79 list follows its page count and its offers as they move', async () => {
  let pages = { 1: [206], 2: [142], 3: [101] };
  let total = 3;
  const h = harness({ respond: url => value80Reply(pages, total)(url) });
  await h.steps(120, 1_600);
  assert.equal(h.asked.some(row => /^value80:[4-7]$/.test(row.what) && row.at > 1_000_000 + 30_000), false,
    'pages past the reported end are dropped');
  pages = { 1: [206], 2: [60], 3: [101], 4: [99] };
  total = 4;
  await h.steps(400, 1_600);
  const late = row => row.at > h.now() - 300_000;
  assert.ok(h.asked.filter(row => late(row) && row.what === 'value80:4').length >= 5, 'a new last page joins');
  assert.ok(h.asked.filter(row => late(row) && row.what === 'value80:2').length <= 2, 'a page without $80+ goes idle');
});

test('a lone $80+ offer far below the block no longer pulls the pages between into the fast zone', async () => {
  const layout = { 1: [150, 120], 2: [110, 90], 3: [75, 70], 4: [68, 64], 5: [62, 60], 6: [58, 55],
    7: [95, 50], 8: [48, 45], 9: [44, 41], 10: [39, 30] };
  const h = harness({ respond: url => {
    const what = describe(url);
    if (!what.startsWith('value:')) return { ok: true, status: 200, items: [] };
    const page = Number(what.split(':')[1]);
    return { ok: true, status: 200, totalPages: 25, products: 40,
      items: (layout[page] || [20, 10]).map((price, index) => ({ ...item(4_000_000 + page * 10 + index), value_usd: price })) };
  } });
  await h.steps(300, 1_600);
  for (const page of [5, 6, 7]) {
    assert.ok(gaps(h.asked, `value:${page}`).every(gap => gap >= 200_000), `page ${page} stays in the pass`);
  }
});

// Nov 19: sort=new page 1 found no $80+ item first in Nov 10-19; its reads
// went to searches with products (owner-approved, same read load).
test('sort=new page 1 is read every 30 minutes and later new pages rarely', async () => {
  // Every 5 min until Nov 26: in 147 h it showed no new $80+ item first.
  const h = harness({ respond: valueReply([120, 95, 85, 70]) });
  await h.steps(2_200); // ~92 minutes
  const page1 = gaps(h.asked, 'newest:1');
  // A rank-1 check: it waits behind a batch of due $80+ pages (here all eight
  // idle min_price=79 pages fall due together every 3 minutes).
  assert.ok(page1.length >= 2 && page1.every(gap => gap >= 1_800_000 && gap <= 1_830_000), String(page1));
  assert.deepEqual([...h.FastReads.INTERVALS.newest], [1_800_000, 1_800_000, 1_800_000, 1_800_000]);
  assert.ok(h.asked.filter(row => row.what === 'newest:2').length <= 4);
  assert.ok(h.asked.filter(row => row.what === 'newest:4').length <= 4);
});

test('read starts stay at least 0.5 seconds apart, plus jitter', async () => {
  const h = harness({ respond: valueReply([120]) });
  assert.equal(h.FastReads.MIN_SPACING_MS, 500);
  assert.equal(h.FastReads.SPACING_JITTER_MS, 400);
  await h.api.run();
  await h.api.run();
  h.advance(650);
  await h.api.run();
  assert.equal(h.asked.length, 1);
  h.advance(50);
  await h.api.run();
  assert.equal(h.asked.length, 2, '0.5 s plus the 0.2 s jitter this harness draws');
  await h.steps(40, 250);
  const starts = h.asked.map(row => row.at);
  assert.ok(starts.length >= 12);
  assert.ok(starts.slice(1).every((at, index) => at - starts[index] >= 700));
});

// Nov 19: a read also waits for the previous one to finish, plus STEP_MS;
// since Nov 23 not for its upload (reads took ~1 s, uploads ~0.2 s more).
test('after a slow read the next one waits only the 0.1 s pause, still one at a time', async () => {
  let h = null;
  h = harness({ respond: url => { h.advance(2_000); return valueReply([120])(url); } });
  assert.equal(h.FastReads.STEP_MS, 100);
  await h.api.run(); // a 2 s read: longer than the spacing
  assert.equal(h.delays.at(-1), 100, 'the next run is due 0.1 s after the read finished');
  h.advance(100);
  await h.api.run();
  assert.equal(h.asked.length, 2);
  assert.equal(h.asked[1].at - h.asked[0].at, 2_100);
  // A quick read leaves the spacing in charge: the next run waits for it.
  const quick = harness({ respond: valueReply([120]) });
  await quick.api.run();
  quick.advance(100);
  await quick.api.run();
  assert.equal(quick.asked.length, 1);
  assert.equal(quick.delays.at(-1), 600, 'the rest of the 0.7 s spacing');
});

test('after a bot-check sign, reads keep the old 2.5 s spacing for a day', async () => {
  let blocked = true;
  const h = harness({ respond: url => blocked ? { ok: false, blocked: true, status: 403 } : valueReply([120])(url) });
  await h.api.run();
  assert.equal(h.store.reviewerFastReads.slowUntil, 1_000_000 + 24 * 60 * 60_000);
  blocked = false;
  h.advance(5 * 60_000);
  await h.steps(120, 500); // one minute in half-second ticks
  let starts = h.asked.slice(1).map(row => row.at);
  assert.ok(starts.length >= 10);
  assert.ok(starts.slice(1).every((at, index) => at - starts[index] >= 2_500), 'slow for a day');
  assert.equal(h.store.reviewerFastReads.blocks, 0, 'a good read clears the block count');
  assert.equal(h.store.reviewerFastReads.slowUntil, 1_000_000 + 24 * 60 * 60_000, 'but not the slow day');
  h.advance(24 * 60 * 60_000);
  const before = h.asked.length;
  await h.steps(120, 500);
  starts = h.asked.slice(before).map(row => row.at);
  assert.ok(starts.slice(1).some((at, index) => at - starts[index] < 2_500), 'normal pace afterwards');
});

test('the price_high chain never passes the page count or twelve pages', async () => {
  for (const [totalPages, deepest] of [[6, 6], [25, 12]]) {
    const h = harness({ respond: valueReply(Array(30).fill(150), totalPages) });
    await h.steps(400);
    const pages = h.asked.filter(row => row.what.startsWith('value:')).map(row => Number(row.what.split(':')[1]));
    assert.equal(Math.max(...pages), deepest);
  }
});

test('a page with no $80+ item leaves the fast zone until $80+ items return', async () => {
  let lasts = [120, 95, 85, 70];
  const h = harness({ respond: url => valueReply(lasts)(url) });
  await h.steps(12);
  lasts = [60, 50, 45, 30];
  await h.steps(8); // page 1 is re-read and now holds no $80+ item
  const before = h.asked.length;
  await h.steps(120); // 5 minutes
  assert.ok(h.asked.slice(before).filter(row => row.what === 'value:2').length <= 2,
    'page 2 now waits with the $40 pages');
  lasts = [120, 95, 85, 70];
  await h.steps(40); // 100 s: page 1 brings page 2 back at once, no wait for the pass
  assert.ok(h.asked.slice(-30).filter(row => row.what === 'value:2').length >= 3, 'page 2 is fast again');
});

test('member and priority searches share rank 2: members every 4 min, priority every 12; empty ones wait 30', async () => {
  const h = harness({ searches: { members: ['sofa', 'lamp'], priority: ['desk', 'Sofa'] },
    respond: url => {
      const what = describe(url);
      if (what === 'search:lamp:1') return { ok: true, status: 200, items: [], products: 0, totalPages: 0 };
      if (what.startsWith('search:')) return { ok: true, status: 200, items: [item(3_000_000)], products: 1, totalPages: 1 };
      return valueReply([30])(url);
    } });
  await h.steps(960); // 40 minutes
  const searches = h.asked.filter(row => row.what.startsWith('search:')).map(row => row.what);
  assert.deepEqual(searches.slice(0, 3), ['search:sofa:1', 'search:lamp:1', 'search:desk:1']);
  assert.equal(searches.filter(what => what === 'search:Sofa:1').length, 0, 'a shared query runs once, as a member search');
  const sofa = gaps(h.asked, 'search:sofa:1');
  assert.ok(sofa.length >= 8 && sofa.every(gap => gap >= 240_000 && gap <= 250_000), `searches with products: 4 min ${sofa}`);
  const lamp = gaps(h.asked, 'search:lamp:1');
  assert.ok(lamp.length >= 1 && lamp.every(gap => gap >= 1_800_000 && gap <= 1_805_000), `empty: 30 min ${lamp}`);
  assert.ok(gaps(h.asked, 'search:desk:1').every(gap => gap >= 720_000 && gap <= 725_000), 'priority: 12 min');
  assert.deepEqual([...new Set(h.sent.filter(row => row.items[0]?.item_id === 'ip-3000000').map(row => row.lane))].sort(),
    ['priority', 'requests']);
  // Each search page is sent with its target index, so a restock held on one
  // search's page 1 is never confirmed by another search's page 1.
  assert.deepEqual([...new Set(h.sent.filter(row => row.items[0]?.item_id === 'ip-3000000')
    .map(row => `${row.lane}:${row.targetIndex}`))].sort(), ['priority:1', 'requests:1']);
  assert.ok(h.sent.filter(row => row.lane === 'value').every(row => !('targetIndex' in row)));
  assert.equal(h.api.recentlyEmpty('lamp'), true);
  assert.equal(h.api.recentlyEmpty(' LAMP '), true);
  assert.equal(h.api.recentlyEmpty('sofa'), false);
  // The proof outlasts the 30-minute gap to the next read, so the requests tab
  // keeps skipping the empty search instead of loading it in a tab.
  const lastLamp = h.asked.filter(row => row.what === 'search:lamp:1').at(-1).at;
  h.advance(lastLamp + 34 * 60_000 - h.now());
  assert.equal(h.api.recentlyEmpty('lamp'), true, 'still proven until just after its next read');
  h.advance(2 * 60_000);
  assert.equal(h.api.recentlyEmpty('lamp'), false, 'an empty proof expires');
});

test('later search pages follow page 1 when it reports more, at most page three', async () => {
  const h = harness({ searches: ['Scooter'], respond: url => {
    const what = describe(url);
    if (what.startsWith('search:')) return { ok: true, status: 200, items: [item(4_000_000)], products: 1, totalPages: 5 };
    return valueReply([30])(url);
  } });
  await h.steps(40);
  const reads = h.asked.filter(row => row.what.startsWith('search:')).map(row => row.what);
  assert.deepEqual(reads, ['search:Scooter:1', 'search:Scooter:2', 'search:Scooter:3']);
  assert.equal(new URL(h.asked.find(row => row.what === 'search:Scooter:1').url).searchParams.get('sort'), 'new');
});

test('department pages fill only spare slots: page 1 for the size, then deepest first', async () => {
  const h = harness({ departments: [{ catId: '4044', index: 1 }, { catId: '4171', index: 2 }],
    respond: url => {
      const what = describe(url);
      if (what.startsWith('department:4044')) return { ok: true, status: 200, items: [item(5_000_000)], products: 1, totalPages: 3, totalItems: 110 };
      if (what.startsWith('department:4171')) return { ok: true, status: 200, items: [item(6_000_000)], products: 1, totalPages: 1, totalItems: 12 };
      return valueReply([30])(url);
    } });
  await h.steps(240); // 10 minutes
  const departments = h.asked.filter(row => row.what.startsWith('department:')).map(row => row.what);
  assert.deepEqual(departments.slice(0, 8), ['department:4044:1', 'department:4044:3', 'department:4044:2',
    'department:4171:1', 'department:4044:1', 'department:4044:3', 'department:4044:2', 'department:4171:1']);
  assert.ok(gaps(h.asked, 'value:1').every(gap => gap <= 15_000), 'department reads never delay $80+ pages');
  assert.ok(h.sent.some(row => row.lane === 'departments'));
  const sized = h.logs.find(row => row.lane === 'departments' && row.page === 1);
  assert.deepEqual([sized.count, sized.totalPages, sized.targetIndex], [110, 3, 1]);
});

test('unreadable department pages pause department reads instead of spending every spare slot', async () => {
  const h = harness({ departments: [{ catId: '4044', index: 1 }], respond: url =>
    describe(url).startsWith('department:') ? { ok: false, status: 200 } : valueReply([30])(url) });
  await h.steps(240); // 10 minutes
  assert.equal(h.asked.filter(row => row.what.startsWith('department:')).length, 3);
  h.advance(30 * 60_000);
  await h.steps(40);
  assert.ok(h.asked.filter(row => row.what.startsWith('department:')).length > 3, 'department reads resume later');
});

test('fast reads send changed pages, skip unchanged ones and fall back to the next tab', async () => {
  const items = [{ ...item(1111111), value_usd: 120 }];
  const h = harness({ respond: url => describe(url) === 'value:1'
    ? { ok: true, status: 200, items, products: 1, totalPages: 1 } : { ok: true, status: 200, items: [] } });
  // Two tabs: the first read falls back to the second tab when the first is navigating.
  const twoTabs = scheduler([new Error('navigating'), { ok: true, status: 200, items }]);
  await twoTabs.api.run();
  assert.deepEqual(twoTabs.asked.map(([tab]) => tab), [1, 2]);
  assert.equal(twoTabs.sent.length, 1);
  await h.steps(10); // value page 1 read every 10 s with the same items
  assert.equal(h.asked.filter(row => row.what === 'value:1').length, 3);
  assert.equal(h.sent.filter(row => row.lane === 'value').length, 1, 'an unchanged page is not resent');
  assert.deepEqual(h.logs.filter(row => row.lane === 'value' && row.page === 1).map(row => row.new), [1, 0, 0]);
});

test('a read aborted by a navigating tab is retried in the next tab', async () => {
  const { api, sent, asked } = scheduler([{ ok: false, reason: 'network' }, { ok: true, status: 200, items: [item(5555555)] }]);
  await api.run();
  assert.deepEqual(asked.map(([tab]) => tab), [1, 2]);
  assert.equal(sent.length, 1);
});

test('a bot check stops fast reads for a back-off window while scanning tabs continue', async () => {
  const h = harness({ respond: () => ({ ok: false, blocked: true, status: 403 }) });
  await h.api.run();
  assert.equal(h.logs[0].outcome, 'blocked');
  assert.equal(h.store.reviewerFastReads.until, 1_000_000 + 5 * 60_000);
  await h.steps(100); // just over four minutes
  assert.equal(h.asked.length, 1, 'no further reads during back-off');
  assert.equal(h.sent.length, 0);
  h.advance(60_000);
  await h.steps(1);
  assert.equal(h.asked.length, 2, 'reads resume after the back-off');
});

test('a bot check hands its tab to the bot-check path once, even if that fails', async () => {
  const alerts = [];
  const { api, logs } = scheduler([{ ok: false, blocked: true, status: 403 }], [], Date,
    async (minutes, tabId) => { alerts.push([minutes, tabId]); throw new Error('notifier down'); });
  await api.run();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(alerts, [[5, 1]], 'hands the challenged tab to the bot-check path');
  assert.equal(logs[0].outcome, 'blocked');
});

test('a probe is read before the schedule, under the same rate, and never ingested', async () => {
  const h = harness({ respond: url => describe(url) === 'department:4044:1'
    ? { ok: true, status: 200, items: [], products: 0, totalPages: 0 } : valueReply([120])(url) });
  await h.api.run(); // value page 1
  const pending = h.api.probe(`${HOME}?catId=4044`);
  await h.api.run();
  assert.equal(h.asked.length, 1, 'the probe still waits for the 2.5 s spacing');
  h.advance(2_500);
  await h.api.run();
  const reply = await pending;
  assert.equal(h.asked[1].what, 'department:4044:1');
  assert.equal(reply.products, 0);
  assert.equal(h.sent.filter(row => row.lane === 'departments').length, 0);
  assert.equal(await h.api.probe('not a url'), null);
  assert.equal(await h.api.probe(`${HOME}?sort=new`), null, 'only department or search pages');
});

test('a probe resolves null while reads are paused or backing off', async () => {
  let pausedNow = true;
  const h = harness({ paused: async () => pausedNow, respond: valueReply([120]) });
  const pending = h.api.probe(`${HOME}?q=lamp&queryNups=lamp&sort=new`);
  await h.api.run();
  assert.equal(await pending, null);
  assert.equal(h.asked.length, 0);
  pausedNow = false;
  h.store.reviewerFastReads = { blocks: 1, until: 2_000_000 };
  const blocked = h.api.probe(`${HOME}?q=lamp&queryNups=lamp&sort=new`);
  await h.api.run();
  assert.equal(await blocked, null);
});

test('at production scale every rank keeps its cadence within the fixed read budget', async () => {
  const members = Array.from({ length: 52 }, (_, index) => `member ${index}`);
  const priority = Array.from({ length: 27 }, (_, index) => `priority ${index}`);
  const h = harness({ searches: { members, priority },
    departments: Array.from({ length: 31 }, (_, index) => ({ catId: String(1000 + index), index: index + 1 })),
    respond: url => {
      const what = describe(url);
      if (what.startsWith('search:member')) {
        return Number(what.split(' ')[1].split(':')[0]) % 2
          ? { ok: true, status: 200, items: [], products: 0, totalPages: 0 }
          : { ok: true, status: 200, items: [item(7_000_000)], products: 3, totalPages: 1 };
      }
      if (what.startsWith('search:priority')) return { ok: true, status: 200, items: [item(8_000_000)], products: 40, totalPages: 25 };
      if (what.startsWith('department:')) return { ok: true, status: 200, items: [item(9_000_001)], products: 40, totalPages: 25 };
      if (what.startsWith('value80:')) return value80Reply()(url);
      return valueReply([190, 120, 92, 70, 58, 50, 46, 42, 39])(url);
    } });
  await h.steps(1_688, 1_600); // 45 minutes at the full rate
  assert.ok(h.asked.length <= 45 * 38 + 1, `reads ${h.asked.length}`);
  const late = h.asked.filter(row => row.at >= 1_000_000 + 5 * 60_000); // after the first-read burst
  const lateGaps = what => gaps(late, what);
  assert.ok(lateGaps('value:1').every(gap => gap <= 14_000), 'value page 1');
  assert.ok(lateGaps('value:2').every(gap => gap <= 36_000), 'other $80+ pages');
  assert.ok(lateGaps('value80:6').every(gap => gap <= 50_000), 'pages of the $80+ list');
  const new80 = lateGaps('new80:1');
  assert.ok(new80.length >= 50 && new80.every(gap => gap >= 40_000 && gap <= 50_000), `new80: ${new80}`);
  const newest = gaps(h.asked, 'newest:1');
  assert.ok(newest.length >= 1 && newest.every(gap => gap >= 1_800_000 && gap <= 1_900_000),
    `newest every ~30 min: ${newest}`);
  // Priority searches share rank 2 with the member searches (Nov 26; at rank 3
  // they were never read once member searches took every spare read).
  const owner = priority.map(query => gaps(h.asked, `search:${query}:1`));
  assert.ok(owner.every(reads => reads.length >= 3 && reads.every(gap => gap >= 720_000 && gap <= 780_000)),
    `priority searches every ~12 min: ${owner}`);
  // Searches with products share every read the ranks above leave; with 26 of
  // them that is about every 4 minutes.
  const productive = members.filter((_, index) => index % 2 === 0).flatMap(query => lateGaps(`search:${query}:1`));
  const median = [...productive].sort((x, y) => x - y)[productive.length >> 1];
  assert.ok(median <= 4.5 * 60_000, `productive member searches, median ${median}`);
  assert.ok(productive.every(gap => gap <= 6.5 * 60_000), `productive member searches: ${productive}`);
  // Empty searches keep their 30-minute check, spread 10 s apart rather than in
  // one burst (the first round, all due at once, ends up to 26 x 10 s late).
  const empty = members.filter((_, index) => index % 2).flatMap(query => gaps(h.asked, `search:${query}:1`));
  assert.ok(empty.length >= 20 && empty.every(gap => gap >= 1_800_000 && gap <= 35 * 60_000), `empty: ${empty}`);
  const slow = h.asked.filter(row => row.at >= 1_000_000 + 25 * 60_000 &&
    /^search:member \d*[13579]:1$/.test(row.what)).map(row => row.at);
  assert.ok(slow.slice(1).every((at, index) => at - slow[index] >= 10_000), 'slow checks at least 10 s apart');
  assert.ok(h.asked.some(row => row.what === 'value:9'), 'the $40 chain is read');
});

// Hot searches (Nov 19): searches read at rank 0, chosen by how many new
// in-stock $80+ items their page-1 reads surface. Nov 23: 12 of them every 48 s
// (6 every 60 s before). Nov 26: 13, 'furniture' a seventh seed and pinned
// (always hot), 'bicycle' never hot.
const SEEDS = ['chair', 'ktaxon', 'sofa', 'tablet', 'trampoline', 'rack', 'furniture'];
// A full hot set of 13 for the tests that need one (only the first seven are built in).
const FULL = [...SEEDS, 'desk', 'bike', 'grill', 'tent', 'kayak', 'mower'];
const plain = value => JSON.parse(JSON.stringify(value));
const offer = (id, value, available = true) => ({ item_id: `ip-${id}`, available, value_usd: value });
const searchReply = items => ({ ok: true, status: 200, items, products: items.length, totalPages: 3 });
// Reference FNV-1a (32-bit) over UTF-8, independent of the extension's code.
const fnv = text => {
  let hash = 0x811c9dc5;
  for (const byte of Buffer.from(text, 'utf8')) hash = Number((BigInt((hash ^ byte) >>> 0) * 0x01000193n) & 0xffffffffn);
  return hash;
};

test('the hot seeds are read every 48 s, page 1 only, while the $80+ pages keep their cadence', async () => {
  const h = harness({ hotSeeds: null, respond: url => describe(url).startsWith('search:')
    ? searchReply([item(9_100_000)]) : value80Reply()(url) });
  assert.deepEqual([...h.FastReads.HOT_SEEDS], SEEDS);
  assert.equal(h.FastReads.HOT_SIZE, 13);
  assert.deepEqual([...h.FastReads.HOT_PINNED], ['furniture']);
  assert.deepEqual([...h.FastReads.HOT_EXCLUDED], ['bicycle']);
  assert.equal(h.FastReads.INTERVALS.hot, 48_000);
  await h.steps(700, 1_200); // 14 minutes at ~50 reads a minute (Nov 23)
  const late = h.asked.filter(row => row.at >= 1_000_000 + 2 * 60_000);
  for (const [index, seed] of SEEDS.entries()) {
    const reads = gaps(late, `search:${seed}:1`);
    // At most ~20% late when due reads bunch up: always within 60 s of the last read.
    const median = [...reads].sort((x, y) => x - y)[reads.length >> 1];
    assert.ok(reads.length >= 12 && median <= 50_000 && reads.every(gap => gap >= 48_000 && gap <= 58_000),
      `${seed}: ${reads}`);
    assert.equal(h.asked.some(row => row.what === `search:${seed}:2`), false, 'page 1 only');
    // Seeds in neither list read as priority searches numbered after the owner's (none here).
    const logged = h.logs.filter(row => row.searchHash === fnv(seed));
    assert.ok(logged.length >= 10 && logged.every(row => row.hot === true && row.lane === 'priority' &&
      row.targetIndex === index + 1), seed);
  }
  assert.equal(new URL(h.asked.find(row => row.what === 'search:chair:1').url).search,
    '?q=chair&queryNups=chair&sort=new');
  assert.ok(gaps(late, 'value:1').every(gap => gap <= 12_000), 'price_high page 1 stays first, at 10 s');
  for (const page of [2, 3]) {
    const reads = gaps(late, `value:${page}`);
    const median = [...reads].sort((x, y) => x - y)[reads.length >> 1];
    assert.ok(median <= 31_500 && reads.every(gap => gap <= 36_000), `value page ${page} stays at 30 s: ${reads}`);
  }
  assert.ok(gaps(late, 'value:4').every(gap => gap >= 120_000 && gap <= 126_000), 'the boundary page keeps its 2 min');
  // (Within 48 s: with seven seeds and the 48-a-minute budget a read is held a few seconds at most.)
  for (const page of [1, 2, 3, 4, 6]) {
    assert.ok(gaps(late, `value80:${page}`).every(gap => gap <= 48_000), `min_price=79 page ${page} stays at 40 s`);
  }
  // price_high page 1 is still the most frequent read.
  const count = what => late.filter(row => row.what === what).length;
  assert.ok(SEEDS.every(seed => count('value:1') > 4 * count(`search:${seed}:1`)));
});

test('a hot member search is the member target read sooner: never twice, page 2 on its own cadence', async () => {
  const h = harness({ hotSeeds: ['sofa'], searches: { members: ['lamp', 'Sofa'], priority: ['sofa', 'desk'] },
    respond: url => describe(url).startsWith('search:') ? searchReply([item(3_000_000)]) : valueReply([30])(url) });
  await h.steps(480); // 20 minutes
  const sofa = gaps(h.asked, 'search:Sofa:1');
  assert.ok(sofa.length >= 20 && sofa.every(gap => gap >= 48_000 && gap <= 52_500), `hot member: ${sofa}`);
  assert.equal(h.asked.some(row => row.what === 'search:sofa:1'), false, 'the seed and priority copies are not read');
  assert.ok(gaps(h.asked, 'search:Sofa:2').every(gap => gap >= 720_000), 'its page 2 keeps the 12-minute member cadence');
  assert.ok(gaps(h.asked, 'search:lamp:1').every(gap => gap >= 240_000 && gap <= 250_000), 'other members keep 4 min');
  const logged = h.logs.filter(row => row.searchHash === fnv('sofa') && row.page === 1);
  assert.ok(logged.length >= 20 && logged.every(row => row.lane === 'requests' && row.targetIndex === 2 && row.hot));
  assert.ok(h.sent.filter(row => row.lane === 'requests' && row.targetIndex === 2).length >= 1,
    'sent as the member search, so a held restock is confirmed by its own page');
});

test('scores count in-stock $80+ items new to a search, from its second page-1 read, and halve every 3 days', async () => {
  let current = [];
  const h = harness({ searches: ['lamp'], respond: url => describe(url) === 'search:lamp:1'
    ? searchReply(current) : valueReply([30])(url) });
  const lampReads = () => h.asked.filter(row => row.what === 'search:lamp:1').length;
  const nextRead = async items => {
    current = items;
    const before = lampReads();
    for (let step = 0; step < 400 && lampReads() === before; step += 1) await h.steps(1);
    assert.equal(lampReads(), before + 1);
  };
  const score = async () => (await h.api.hotSearches()).scores.lamp || 0;
  await nextRead([offer(1_000_001, 120), offer(1_000_002, 30)]);
  assert.equal(await score(), 0, 'the first read only sets the baseline');
  // Only 1_000_003 is new: 1_000_001 was seen, 1_000_004 is out of stock,
  // 1_000_005 is under $80 and a repeated item counts once.
  await nextRead([offer(1_000_001, 120), offer(1_000_003, 90), offer(1_000_004, 95, false), offer(1_000_005, 79.99),
    offer(1_000_003, 90)]);
  assert.ok(Math.abs(await score() - 1) < 0.001, String(await score()));
  await nextRead([offer(1_000_004, 95), offer(1_000_003, 90)]);
  assert.ok(Math.abs(await score() - 1) < 0.001, 'back in stock after being seen out of stock is not new');
  // A probe (the scan coordinator's emptiness check) never scores.
  current = [offer(1_000_009, 300)];
  const pending = h.api.probe(`${HOME}?q=lamp&queryNups=lamp&sort=new`);
  await h.steps(2);
  assert.equal((await pending).items.length, 1);
  assert.ok(Math.abs(await score() - 1) < 0.001, 'probes do not score');
  // Gone from the search for over 6 hours, an item counts again; one seen within them does not.
  await nextRead([offer(1_000_004, 95)]);
  h.advance(5 * 60 * 60_000);
  await nextRead([offer(1_000_004, 95)]);
  h.advance(60 * 60_000 + 60_000);
  await nextRead([offer(1_000_001, 120), offer(1_000_004, 95)]);
  const after = await score();
  assert.ok(after > 1.9 && after < 2.001, `one more, the old point slightly decayed: ${after}`);
  h.advance(72 * 60 * 60_000);
  assert.ok(Math.abs(await score() - after / 2) < 0.01, 'half-life 3 days');
  assert.equal(h.FastReads.HOT_HALF_LIFE_MS, 3 * 24 * 60 * 60_000);
});

test('a newcomer must beat the weakest hot search by 25% plus 0.5; ties keep the set, nothing flaps', async () => {
  let lamp = [offer(1_099_999, 20)]; // products, so it is read every 4 minutes
  const h = harness({ hotSeeds: FULL, searches: { members: ['lamp'] }, respond: url => {
    const what = describe(url);
    if (what === 'search:lamp:1') return searchReply(lamp);
    return what.startsWith('search:') ? searchReply([item(9_100_000)]) : valueReply([30])(url);
  } });
  const hotSet = async () => plain((await h.api.hotSearches()).hot);
  await h.steps(10, 1_200);
  assert.deepEqual(await hotSet(), FULL, 'the seeds start hot, at their prior of 2, and fill all 13 places');
  let id = 1_100_000;
  const fresh = count => Array.from({ length: count }, () => offer(id++, 150));
  const lampReads = () => h.asked.filter(row => row.what === 'search:lamp:1').length;
  const nextRead = async items => {
    lamp = [...lamp, ...items];
    const before = lampReads();
    for (let step = 0; step < 800 && lampReads() === before; step += 1) await h.steps(1, 1_200);
    assert.equal(lampReads(), before + 1);
  };
  await nextRead([]); // baseline
  await nextRead(fresh(2)); // 2 against the seeds' ~2: not enough
  assert.deepEqual(await hotSet(), FULL);
  await nextRead(fresh(2)); // ~4 > 2 x 1.25 + 0.5
  const swapped = [...FULL.slice(0, 12), 'lamp'];
  assert.deepEqual(await hotSet(), swapped, 'replaces the latest seed to join among the tied weakest');
  const swappedAt = h.now();
  await h.steps(500, 1_200); // 10 minutes
  const lampGaps = gaps(h.asked.filter(row => row.at > swappedAt + 70_000), 'search:lamp:1');
  assert.ok(lampGaps.length >= 9 && lampGaps.every(gap => gap <= 52_000), `now read every 48 s: ${lampGaps}`);
  assert.equal(h.asked.some(row => row.what === 'search:mower:1' && row.at > swappedAt), false, 'mower is out');
  // Everything decays alike, so weeks without a find change nothing.
  h.advance(20 * 24 * 60 * 60_000);
  await h.steps(5, 1_200);
  assert.deepEqual(await hotSet(), swapped);
});

test('the hot set holds 13: the pinned search, seven built-in seeds, then the best-scoring searches', async () => {
  const start = 5_000_000;
  const members = Array.from({ length: 10 }, (_, index) => `member ${index}`);
  // Members 0-7 scored (one new $80+ item or more); 8 and 9 below the 0.5 needed for a free place.
  const scores = members.map((key, index) => [key, index < 8 ? 1 + index / 10 : 0.4, start]);
  const store = { reviewerHotSearches: { v: 1, hot: ['chair'], scores, memory: [] } };
  const h = harness({ store, start, hotSeeds: null, searches: { members }, respond: valueReply([30]) });
  await h.steps(1);
  const hot = plain((await h.api.hotSearches()).hot);
  assert.equal(hot.length, 13);
  assert.deepEqual(hot.slice(0, 2), ['chair', 'furniture'],
    'members of the stored set keep their place; the pinned search takes one');
  // The other five seeds (prior 2) and the six best members fill the free places, best first.
  assert.deepEqual(hot.slice(2), ['ktaxon', 'sofa', 'tablet', 'trampoline', 'rack',
    'member 7', 'member 6', 'member 5', 'member 4', 'member 3', 'member 2']);
});

test('the swap margin is exact: weakest x 1.25 + 0.5 stays out, just above gets in; a free place needs 0.5', async () => {
  const start = 5_000_000;
  const full = FULL;
  for (const [challenger, expected] of [[3, false], [3.001, true]]) {
    const store = { reviewerHotSearches: { v: 1, hot: full,
      scores: [...full.map(key => [key, 2, start]), ['lamp', challenger, start]], memory: [] } };
    const h = harness({ store, start, hotSeeds: full, searches: { members: ['lamp'] }, respond: valueReply([30]) });
    await h.steps(1);
    const { hot } = await h.api.hotSearches();
    assert.equal(hot.includes('lamp'), expected, `challenger ${challenger}`);
    assert.equal(hot.length, 13);
  }
  for (const [score, expected] of [[0.49, false], [0.5, true]]) {
    const store = { reviewerHotSearches: { v: 1, hot: ['chair'], scores: [['chair', 2, start], ['lamp', score, start]],
      memory: [] } };
    const h = harness({ store, start, hotSeeds: ['chair'], searches: { members: ['lamp'] }, respond: valueReply([30]) });
    await h.steps(1);
    assert.equal((await h.api.hotSearches()).hot.includes('lamp'), expected, `free place, score ${score}`);
  }
});

test('scores, the hot set and each search\'s recent items survive a reload; storage stays bounded', async () => {
  let lamp = [offer(1_200_001, 150)];
  const respond = url => {
    const what = describe(url);
    if (what === 'search:lamp:1') return searchReply(lamp);
    return what.startsWith('search:') ? searchReply([item(9_100_000)]) : valueReply([30])(url);
  };
  const store = {};
  const first = harness({ store, hotSeeds: null, searches: { members: ['lamp'] }, respond });
  await first.steps(100); // the baseline read of lamp
  lamp = [...lamp, offer(1_200_002, 150), offer(1_200_003, 150), offer(1_200_004, 150), offer(1_200_005, 150)];
  await first.steps(100); // +4: lamp joins the set
  const saved = plain(await first.api.hotSearches());
  assert.ok(saved.hot.includes('lamp') && saved.scores.lamp > 3.9, JSON.stringify(saved));
  assert.equal(store.reviewerHotSearches.v, 1);
  assert.ok(store.reviewerHotSearches.memory.some(([key, items]) => key === 'lamp' && items.length === 5));
  // A reload: same storage, a new scheduler.
  const second = harness({ store, start: first.now() + 30_000, hotSeeds: null, searches: { members: ['lamp'] }, respond });
  const loaded = plain(await second.api.hotSearches());
  assert.deepEqual(loaded.hot, saved.hot);
  assert.ok(Math.abs(loaded.scores.lamp - saved.scores.lamp) < 0.01);
  lamp = [...lamp, offer(1_200_006, 150)];
  await second.steps(30); // lamp is hot: read within the first minute
  assert.ok(second.asked.some(row => row.what === 'search:lamp:1'));
  const reloaded = plain(await second.api.hotSearches()).scores.lamp;
  assert.ok(Math.abs(reloaded - loaded.scores.lamp - 1) < 0.01, `only the one new item counts: ${reloaded}`);
  // Bounded: 1000 stored scores load as at most 400; 50 remembered items as 16.
  const now = 9_000_000;
  const big = { reviewerHotSearches: { v: 1, hot: ['chair'],
    scores: Array.from({ length: 1000 }, (_, index) => [`old ${index}`, 1, now]),
    memory: [['lamp', Array.from({ length: 50 }, (_, index) => [`ip-${1_300_000 + index}`, now])]] } };
  const bounded = harness({ store: big, start: now, hotSeeds: ['chair'], searches: { members: ['lamp'] },
    respond: url => describe(url) === 'search:lamp:1' ? searchReply([offer(1_399_999, 150)]) : valueReply([30])(url) });
  await bounded.steps(40);
  const kept = big.reviewerHotSearches;
  assert.ok(kept.scores.length <= 400, `scores ${kept.scores.length}`);
  assert.ok(kept.memory.length >= 1 && kept.memory.every(([, items]) => items.length <= 16));
  // Malformed storage is ignored: the seeds start again.
  const broken = harness({ store: { reviewerHotSearches: { v: 1, hot: [5, null], scores: 'x', memory: [[1, 2]] } },
    hotSeeds: null, respond: valueReply([30]) });
  assert.deepEqual(plain((await broken.api.hotSearches()).hot), []);
  await broken.steps(1);
  assert.deepEqual(plain((await broken.api.hotSearches()).hot), ['furniture', ...SEEDS.slice(0, 6)],
    'the pinned search first, then the seeds by their prior');
});

test('a pinned search always holds a hot place; an excluded one never gets one and keeps its member cadence', async () => {
  // Nov 26 (2.32.9): 'furniture' pinned, 'bicycle' excluded (it showed the same
  // $80+ items as 'bike' every time).
  const start = 5_000_000;
  const members = ['Bicycle', 'bike', ...Array.from({ length: 14 }, (_, index) => `member ${index}`)];
  const stored = scores => ({ reviewerHotSearches: { v: 1, hot: ['bicycle', ...members.slice(2, 14)], scores,
    memory: [] } });
  const respond = url => describe(url).startsWith('search:') ? searchReply([item(3_000_000)]) : valueReply([30])(url);
  const options = { start, hotSeeds: [], hotPinned: [' Furniture '], hotExcluded: ['BICYCLE'], respond,
    searches: { members, priority: ['furniture'] } };
  // Every member scores far above the pinned search, which has no score at all.
  const even = members.map(key => [key.toLowerCase(), 10, start]);
  const h = harness({ ...options, store: stored(even) });
  await h.steps(1);
  let hot = plain((await h.api.hotSearches()).hot);
  assert.equal(hot.length, 13, JSON.stringify(hot));
  assert.ok(hot.includes('furniture'), 'pinned: in the set without any score');
  assert.equal(hot.includes('bicycle'), false, 'excluded: dropped from the stored set');
  assert.equal(hot.includes('bike'), false, 'a tie with the weakest does not get in');
  // A newcomer that clears the margin takes the weakest unpinned place, never the
  // pinned one; the excluded search stays out whatever it scores.
  const others = even.filter(([key]) => !['member 13', 'bicycle'].includes(key));
  const strong = harness({ ...options, store: stored([...others, ['member 13', 100, start], ['bicycle', 1_000, start]]) });
  await strong.steps(1);
  hot = plain((await strong.api.hotSearches()).hot);
  assert.equal(hot.length, 13);
  assert.ok(hot.includes('furniture') && hot.includes('member 13') && !hot.includes('bicycle'), JSON.stringify(hot));
  // Reads: the pinned search every 48 s as a hot priority search; the excluded one on the member cadence.
  await strong.steps(1_000, 1_200); // 20 minutes
  const late = strong.asked.filter(row => row.at >= start + 2 * 60_000);
  const furniture = gaps(late, 'search:furniture:1');
  assert.ok(furniture.length >= 15 && furniture.every(gap => gap >= 48_000 && gap <= 58_000), `pinned: ${furniture}`);
  const pinnedLogs = strong.logs.filter(row => row.searchHash === fnv('furniture') && row.page === 1);
  assert.ok(pinnedLogs.length >= 15 && pinnedLogs.every(row => row.hot === true && row.lane === 'priority'));
  const bicycle = gaps(strong.asked, 'search:Bicycle:1');
  assert.ok(bicycle.length >= 1 && bicycle.every(gap => gap >= 240_000), `excluded, member cadence: ${bicycle}`);
  assert.equal(strong.logs.some(row => row.searchHash === fnv('bicycle') && row.hot), false);
});

test('during the gentler day after a bot-check sign no search is hot; the hot set returns after it', async () => {
  let blocked = true;
  const h = harness({ hotSeeds: null, searches: { members: ['Sofa'] }, respond: url => {
    if (blocked) return { ok: false, blocked: true, status: 403 };
    return describe(url).startsWith('search:') ? searchReply([item(3_000_000)]) : valueReply([120, 95, 85, 70])(url);
  } });
  await h.api.run();
  blocked = false;
  h.advance(5 * 60_000);
  const from = h.now();
  const logsBefore = h.logs.length;
  await h.steps(600, 2_800); // 28 minutes at the gentler pace (2.7 s with this harness's jitter)
  const slow = h.asked.filter(row => row.at >= from);
  assert.equal(slow.some(row => ['chair', 'ktaxon', 'tablet', 'trampoline', 'rack', 'furniture']
    .some(seed => row.what === `search:${seed}:1`)), false, 'the seeds and the pinned search pause');
  const sofa = gaps(slow, 'search:Sofa:1');
  assert.ok(sofa.length >= 4 && sofa.every(gap => gap >= 240_000), `sofa back on the member cadence: ${sofa}`);
  assert.equal(h.logs.slice(logsBefore).some(row => row.hot), false);
  assert.ok(slow.slice(1).every((row, index) => row.at - slow[index].at >= 2_500), 'the old spacing, unchanged');
  h.advance(24 * 60 * 60_000);
  const back = h.now();
  await h.steps(200, 1_500);
  const resumed = gaps(h.asked.filter(row => row.at >= back), 'search:Sofa:1');
  assert.ok(resumed.length >= 3 && resumed.every(gap => gap <= 64_500), `hot again: ${resumed}`);
  assert.ok(h.asked.some(row => row.at >= back && row.what === 'search:chair:1'));
});

test('search reads log a hash of the normalised query, the hot flag, the $80+ count and the request id', async () => {
  assert.equal(fnv('a'), 0xe40c292c, 'reference FNV-1a');
  const h = harness({ hotSeeds: ['chair'], searches: { members: ['Desk  Lamp', 'café crème'] },
    send: () => ({ ok: true, summary: { new: 1 }, requestId: 'ab'.repeat(16) }),
    respond: url => describe(url).startsWith('search:')
      ? searchReply([offer(1_400_001, 150), offer(1_400_002, 20)]) : valueReply([120])(url) });
  // The $150 offer no list shows starts a freshness probe (2.32.7): a few extra reads first.
  await h.steps(30);
  const byHash = hash => h.logs.filter(row => row.searchHash === hash);
  assert.equal(h.FastReads.searchHash('desk lamp'), fnv('desk lamp'));
  assert.equal(h.FastReads.searchHash('café crème'), fnv('café crème'), 'UTF-8 bytes, like Python');
  const lamp = byHash(fnv('desk lamp'));
  assert.ok(lamp.length >= 1 && lamp.every(row => row.lane === 'requests' && !('hot' in row) && row.count === 1 &&
    row.requestId === 'ab'.repeat(16)), JSON.stringify(lamp));
  assert.ok(byHash(fnv('café crème')).length >= 1);
  const chair = byHash(fnv('chair'));
  assert.ok(chair.length >= 1 && chair.every(row => row.hot === true && row.lane === 'priority'));
  assert.ok(h.logs.filter(row => row.lane === 'value').every(row => !('searchHash' in row) && !('hot' in row)));
  const text = JSON.stringify(h.logs);
  for (const word of ['chair', 'Desk', 'Lamp', 'lamp', 'café']) assert.equal(text.includes(word), false, word);
});

test('at production scale with 13 hot searches, new80, priority searches at rank 2 and the budget, members keep their revisit', async () => {
  // 2.32.7 (6 hot searches every 60 s, other $80+ price_high pages every 20 s,
  // no new80) on this same workload at its ~41 reads a minute (a start every
  // 1.47 s): productive member searches every 251.4 s at the median, 286.7 s at
  // worst. 2.32.8 reads ~50 a minute (data/plan90-20261123/cadence_sim.mjs on
  // the Nov 19-23 read durations): a start every ~1.2 s. Here the hot searches
  // (13 since Nov 26) are extra searches, not members (the harder case), and
  // the 27 priority searches share rank 2 with the members (Nov 26); the price_high
  // page after the $80+ block (2 min) and sort=new (30 min) pay for them.
  const members = Array.from({ length: 52 }, (_, index) => `member ${index}`);
  const priority = Array.from({ length: 27 }, (_, index) => `priority ${index}`);
  const respond = url => {
    const what = describe(url);
    if (what.startsWith('search:member')) {
      return Number(what.split(' ')[1].split(':')[0]) % 2
        ? { ok: true, status: 200, items: [], products: 0, totalPages: 0 }
        : { ok: true, status: 200, items: [item(7_000_000)], products: 3, totalPages: 1 };
    }
    if (what.startsWith('search:priority')) return { ok: true, status: 200, items: [item(8_000_000)], products: 40, totalPages: 25 };
    if (what.startsWith('search:')) return searchReply([item(9_100_000)]);
    if (what.startsWith('department:')) return { ok: true, status: 200, items: [item(9_000_001)], products: 40, totalPages: 25 };
    if (what.startsWith('value80:')) return value80Reply()(url);
    return valueReply([190, 120, 92, 70, 58, 50, 46, 42, 39])(url);
  };
  const departments = Array.from({ length: 31 }, (_, index) => ({ catId: String(1000 + index), index: index + 1 }));
  const simulate = async (hotSeeds, step) => {
    const h = harness({ searches: { members, priority }, departments, respond, hotSeeds });
    await h.steps(Math.round(45 * 60_000 / step), step);
    const late = h.asked.filter(row => row.at >= 1_000_000 + 5 * 60_000);
    const productive = members.filter((_, index) => index % 2 === 0).flatMap(query => gaps(late, `search:${query}:1`));
    const sorted = [...productive].sort((x, y) => x - y);
    return { h, late, median: sorted[sorted.length >> 1], max: sorted.at(-1) };
  };
  const after = await simulate(FULL, 1_200);
  assert.ok(after.median <= 251_370 * 1.05, `members: ${after.median} vs 251370 in 2.32.7`);
  assert.ok(after.max <= Math.max(286_650 * 1.1, 6.5 * 60_000), `members worst: ${after.max} vs 286650 in 2.32.7`);
  const lateGaps = what => gaps(after.late, what);
  assert.ok(lateGaps('value:1').every(gap => gap <= 12_000), 'value page 1');
  assert.ok(lateGaps('value:2').every(gap => gap <= 36_000), 'other $80+ pages');
  assert.ok(lateGaps('value80:6').every(gap => gap <= 48_000), 'pages of the $80+ list');
  assert.ok(lateGaps('new80:1').every(gap => gap <= 48_000), 'new80 page 1');
  for (const seed of FULL) {
    const reads = lateGaps(`search:${seed}:1`);
    // Every hot read within 60 s of the last: an item new there is never later than that.
    assert.ok(reads.length >= 40 && reads.every(gap => gap <= 56_000), `${seed}: ${reads}`);
  }
  const newest = gaps(after.h.asked, 'newest:1');
  assert.ok(newest.length >= 1 && newest.every(gap => gap >= 1_800_000 && gap <= 1_900_000), `newest: ${newest}`);
  const owner = priority.map(query => gaps(after.h.asked, `search:${query}:1`));
  assert.ok(owner.every(reads => reads.length >= 2 && reads.every(gap => gap <= 20 * 60_000)), `priority: ${owner}`);
  const empty = members.filter((_, index) => index % 2).flatMap(query => gaps(after.h.asked, `search:${query}:1`));
  assert.ok(empty.length >= 20 && empty.every(gap => gap >= 1_800_000 && gap <= 35 * 60_000), `empty: ${empty}`);
});

// --- freshness probes (2.32.7) -------------------------------------------------
// The min_price=79 list has 3 pages ($300-$210, $200-$130, $125-$79); a member
// search "desk" shows NEW, a $150 offer no catalogue list shows yet.
const NEW = 21_500_000_001;
const shape = url => {
  const parsed = new URL(url);
  const page = Number(parsed.searchParams.get('page') || 1);
  if (parsed.searchParams.get('catId')) return `department:${parsed.searchParams.get('catId')}:${page}`;
  if (parsed.searchParams.get('q')) return `search:${parsed.searchParams.get('q')}:${page}`;
  const min = parsed.searchParams.get('min_price') === '79';
  const high = parsed.searchParams.get('sort') === 'price_high';
  return `${high ? (min ? 'value80' : 'value') : (min ? 'new80' : 'newest')}:${page}`;
};
const LIST80 = { 1: [300, 290, 280, 270, 260, 250, 240, 230, 220, 210],
  2: [200, 190, 180, 170, 160, 150, 145, 140, 135, 130], 3: [125, 120, 110, 100, 95, 90, 85, 80, 79.5, 79] };
const IDLE80 = { 1: LIST80[1], 2: LIST80[2], 3: [79.99, 79.9, 79.8, 79.7, 79.6, 79.5, 79.4, 79.3, 79.2, 79.1] };

function freshWorld({ appearAt = Infinity, appearPage = 2, price = 150, category = '0:4044:103150', available = true,
  list80 = LIST80, newest = [], searchItems = null, blockOn = '', page3BrokenUntil = 0 } = {}) {
  let now = () => 0;
  const respond = url => {
    const what = shape(url);
    const page = Number(what.split(':').at(-1));
    if (blockOn && what === blockOn) return { ok: false, blocked: true, status: 429 };
    if (what === 'value80:3' && now() < page3BrokenUntil) return { ok: false, status: 500 };
    if (what.startsWith('search:')) {
      const items = searchItems || [offer(NEW, price, available)];
      return { ...searchReply(items), snapshot: items.map((entry, index) => [entry.item_id.slice(3), index + 1,
        Math.round(entry.value_usd * 100), entry.available ? 'i' : 'o', ...(category ? [{ c: category }] : [])]) };
    }
    if (what.startsWith('value80:')) {
      const items = (list80[page] || []).map((value, index) => offer(5_100_000 + page * 100 + index, value));
      if (now() >= appearAt && page === appearPage) items.splice(5, 0, offer(NEW, price));
      return { ok: true, status: 200, totalPages: Object.keys(list80).length, products: items.length, items };
    }
    if (what.startsWith('value:')) return valueReply([210, 140, 85, 60, 45, 35])(url);
    if (what.startsWith('newest:') || what.startsWith('new80:')) {
      return { ok: true, status: 200, totalPages: 8, products: 2 + newest.length,
        items: [...newest, offer(5_900_001, 99), offer(5_900_002, 15)] };
    }
    if (what.startsWith('department:')) {
      return { ok: true, status: 200, totalPages: 25, products: 1, items: [offer(5_800_001, 30)] };
    }
    return { ok: true, status: 200, items: [] };
  };
  return { respond, bind(h) { now = () => h.now(); return h; } };
}

const DEPARTMENTS = [{ catId: '4044', index: 1 }, { catId: '4044_103150', index: 2 }, { catId: '1085666', index: 3 }];
const freshHarness = (world, options = {}) => world.bind(harness({ respond: world.respond,
  searches: { members: ['desk'] }, departments: DEPARTMENTS, ...options }));
const freshAsks = h => h.asked.map((row, index) => ({ ...row, index, what: shape(row.url) }));
const probeRecords = (h, event) => h.records.filter(row => row.kind === 'probe' && (!event || row.event === event));
const usedReads = (count, now = 1_000_000) => ({ reviewerFreshnessProbes: { v: 1,
  reads: Array.from({ length: count }, (_, index) => now - index * 600_000), starts: [], probed: [] } });

test('a search-only $80+ item starts a probe: the predicted $80+ page, both new lists and its department, at once', async () => {
  const h = freshHarness(freshWorld({ appearAt: 1_000_000 + 4 * 60_000 }));
  await h.steps(40);
  const [start] = probeRecords(h, 'start');
  assert.ok(start, JSON.stringify(probeRecords(h)));
  assert.equal(start.itemId, `ip-${NEW}`);
  assert.equal(start.valueCents, 15_000);
  assert.deepEqual([start.predictedPage, start.pages, start.plannedReads], [2, [2], 4]);
  assert.equal(start.department, '4044_103150', 'the longest known department on the category path');
  assert.equal(start.category, '0:4044:103150');
  assert.deepEqual(start.trigger, { lane: 'requests', page: 1, searchHash: fnv('desk'), position: 1 });
  const asks = freshAsks(h);
  const search = asks.find(row => row.what === 'search:desk:1');
  // Probe reads come straight after the search read, behind nothing but price_high page 1.
  const after = asks.slice(search.index + 1).filter(row => row.what !== 'value:1').slice(0, 4).map(row => row.what);
  assert.deepEqual(after, ['value80:2', 'newest:1', 'new80:1', 'department:4044_103150:1']);
  const extra = h.records.filter(row => row.kind === 'read' && row.probe === start.probe);
  assert.deepEqual(extra.map(row => row.lane), ['value80', 'newest', 'new80', 'departments']);
  assert.ok(extra.every(row => row.outcome === 'items' && row.items.length > 0));
  assert.deepEqual(extra.map(row => [row.sort ?? '', row.minPrice ?? 0, row.catId ?? '']),
    [['price_high', 79, ''], ['new', 0, ''], ['new', 79, ''], ['', 0, '4044_103150']]);
  // Measurement only: never ingested, logged as extra (fresh) reads. (Since
  // Nov 23 new80 page 1 is also a regular read every 40 s, ingested.)
  assert.equal(h.sent.some(row => row.lane === 'departments'), false);
  const probeNew80 = extra.find(row => row.lane === 'new80');
  assert.equal(h.sent.some(row => row.lane === 'new80' && row.at === probeNew80.at), false);
  assert.equal(h.logs.filter(row => row.fresh === true).length, 4);
  assert.ok(h.logs.filter(row => row.fresh === true).every(row => !('searchHash' in row)));
});

test('a probe ends when the item shows on the $80+ list, with its lag; normal 40 s reads cover the re-checks', async () => {
  const appearAt = 1_000_000 + 4 * 60_000;
  const h = freshHarness(freshWorld({ appearAt }));
  await h.steps(200); // 500 s
  const [start] = probeRecords(h, 'start');
  const [end] = probeRecords(h, 'end');
  assert.ok(end, JSON.stringify(probeRecords(h)));
  assert.equal(end.probe, start.probe);
  assert.equal(end.outcome, 'appeared');
  assert.equal(end.extraReads, 4, 'page 2 is read every 40 s anyway: no extra re-reads');
  assert.deepEqual([end.first.value80.page, end.first.value80.position], [2, 6]);
  assert.ok(end.first.value80.at >= appearAt && end.first.value80.at <= appearAt + 45_000, String(end.first.value80.at));
  assert.equal(end.lagMs, end.first.value80.at - start.startedAt);
  assert.ok(end.checks.some(check => check.shape === 'new80' && check.extra === true && check.found === false));
  assert.ok(end.checks.filter(check => check.shape === 'value80' && !check.extra).length >= 3, 'normal reads count');
  assert.equal(probeRecords(h, 'start').length, 1, 'one probe per item');
  const state = await h.api.freshness();
  assert.deepEqual([state.active.length, state.readsToday, state.startsThisHour], [0, 4, 1]);
});

test('an idle predicted page is re-read every 60 s until 20 minutes pass; then its cadence is back', async () => {
  // Page 3 holds no $80+ offer (normally read every 3 min); an $80 offer belongs there.
  const h = freshHarness(freshWorld({ price: 80, category: '', list80: IDLE80 }));
  await h.steps(700); // ~29 min
  const [start] = probeRecords(h, 'start');
  assert.deepEqual([start.predictedPage, start.pages, start.plannedReads], [3, [2, 3], 4], 'near the edge: page 2 too');
  const [end] = probeRecords(h, 'end');
  assert.equal(end.outcome, 'timeout');
  assert.ok(end.at - start.startedAt >= 20 * 60_000 && end.at - start.startedAt <= 20 * 60_000 + 5_000);
  const reads = freshAsks(h).filter(row => row.what === 'value80:3').map(row => row.at);
  const during = reads.filter(at => at > start.startedAt && at < end.at);
  const duringGaps = during.slice(1).map((at, index) => at - during[index]);
  assert.ok(duringGaps.length >= 15 && duringGaps.every(gap => gap <= 65_000), `re-checked every 60 s: ${duringGaps}`);
  const extra = h.records.filter(row => row.kind === 'read' && row.probe === start.probe);
  assert.equal(extra.length, end.extraReads);
  assert.ok(end.extraReads >= 12 && end.extraReads <= 24, String(end.extraReads));
  assert.ok(extra.filter(row => row.lane === 'value80' && row.page === 2).length <= 1, 'page 2 is read every 40 s anyway');
  const afterEnd = reads.filter(at => at > end.at + 60_000);
  assert.ok(afterEnd.length >= 1 && afterEnd.slice(1).every((at, index) => at - afterEnd[index] >= 170_000),
    'back to its 3 min cadence');
  assert.equal(h.store.reviewerFreshnessProbes.reads.length, end.extraReads, 'extra reads persisted');
});

test('no probe for items a list showed in the last 30 min, sold-out or under-$80 offers, or before the list is warm', async () => {
  const h1 = freshHarness(freshWorld({ newest: [offer(NEW, 150)] }));
  await h1.steps(120);
  assert.ok(freshAsks(h1).some(row => row.what === 'search:desk:1'));
  assert.equal(probeRecords(h1).length, 0, 'sort=new showed it');
  for (const world of [freshWorld({ available: false }), freshWorld({ price: 79.99 })]) {
    const h = freshHarness(world);
    await h.steps(120);
    assert.equal(probeRecords(h).length, 0);
    assert.equal(h.records.some(row => row.kind === 'read' && row.probe), false, 'no probe read');
  }
  // Until every page of the $80+ list has been read since the worker started,
  // the search alone proves nothing (page 3 fails for the first 5 minutes).
  const ready = 1_000_000 + 5 * 60_000;
  const cold = freshHarness(freshWorld({ page3BrokenUntil: ready, category: '' }));
  await cold.steps(110); // 275 s
  assert.ok(freshAsks(cold).filter(row => row.what === 'search:desk:1').length >= 1);
  assert.equal(probeRecords(cold).length, 0);
  await cold.steps(150);
  const [start] = probeRecords(cold, 'start');
  assert.ok(start && start.startedAt > ready, JSON.stringify(probeRecords(cold)));
});

test('caps: two probes at once, eight starts an hour, 100 extra reads a day, all persisted', async () => {
  const many = [offer(NEW, 150), offer(NEW + 1, 160), offer(NEW + 2, 170)];
  const h = freshHarness(freshWorld({ searchItems: many, category: '' }));
  await h.steps(40);
  assert.deepEqual(probeRecords(h, 'start').map(row => row.itemId), [`ip-${NEW}`, `ip-${NEW + 1}`]);
  assert.deepEqual(probeRecords(h, 'skipped').map(row => [row.itemId, row.reason]), [[`ip-${NEW + 2}`, 'active']]);
  const stored = JSON.parse(JSON.stringify(h.store.reviewerFreshnessProbes));
  assert.equal(stored.v, 1);
  assert.equal(stored.starts.length, 2);
  assert.deepEqual(stored.probed.map(row => row[0]).sort(), many.map(row => row.item_id).sort());

  const now = 1_000_000;
  const hourly = freshHarness(freshWorld({ category: '' }), { store: { reviewerFreshnessProbes: { v: 1, reads: [],
    starts: Array.from({ length: 8 }, (_, index) => now - index * 60_000), probed: [] } } });
  await hourly.steps(40);
  assert.deepEqual(probeRecords(hourly).map(row => [row.event, row.reason]), [['skipped', 'hourly']]);

  const daily = freshHarness(freshWorld({ category: '' }), { store: usedReads(98) });
  await daily.steps(40);
  assert.deepEqual(probeRecords(daily).map(row => [row.event, row.reason]), [['skipped', 'daily']]);
  assert.equal(daily.records.some(row => row.kind === 'read' && row.probe), false, 'no probe read');

  // 96 used: the 4 planned reads fit, then the cap stops the re-checks of an idle page.
  const late = freshHarness(freshWorld({ price: 80, category: '', list80: IDLE80 }), { store: usedReads(96) });
  await late.steps(700);
  const [end] = probeRecords(late, 'end');
  assert.equal(end.extraReads, 4);
  assert.ok(end.droppedReads >= 1, JSON.stringify(end));
  assert.equal(late.store.reviewerFreshnessProbes.reads.length, 100);
  assert.equal(late.records.filter(row => row.kind === 'read' && row.probe).length, 4);
  // 22 hours on most of the budget is free again, but the same item is not probed twice in a day.
  late.advance(22 * 60 * 60_000);
  await late.steps(60);
  assert.ok(freshAsks(late).some(row => row.at > 1_000_000 + 22 * 60 * 60_000 && row.what === 'search:desk:1'));
  assert.equal(probeRecords(late, 'start').length, 1);
  assert.equal(probeRecords(late, 'skipped').length, 0);
});

test('probe reads go through the limiter one at a time and price_high page 1 keeps its 10 s', async () => {
  const h = freshHarness(freshWorld({ searchItems: [offer(NEW, 150), offer(NEW + 1, 290)] }));
  await h.steps(4_000, 100); // 400 s in 100 ms ticks: only the limiter spaces the reads
  assert.equal(probeRecords(h, 'start').length, 2);
  const times = h.asked.map(row => row.at);
  const spacing = times.slice(1).map((at, index) => at - times[index]);
  assert.ok(spacing.every(gap => gap >= h.FastReads.MIN_SPACING_MS), `spacing: ${Math.min(...spacing)}`);
  assert.ok(gaps(freshAsks(h), 'value:1').every(gap => gap <= 12_500), 'price_high page 1 is never delayed');
  assert.ok(h.records.filter(row => row.kind === 'read' && row.probe).length >= 7);
});

test('never in the safe period after a bot-check sign; a block mid-probe ends it and stops its reads', async () => {
  const start = 1_000_000;
  const safe = freshHarness(freshWorld(), { store: { reviewerFastReads: { blocks: 0, until: 0,
    slowUntil: start + 24 * 60 * 60_000 } } });
  await safe.steps(200, 3_000);
  assert.ok(freshAsks(safe).some(row => row.what === 'search:desk:1'), 'the member search still runs');
  assert.equal(probeRecords(safe).length, 0);
  assert.equal(safe.records.some(row => row.probe), false);

  // The bot-check sign comes on a probe read: the item's department page 1
  // (department reads otherwise fill only spare slots, 4044 first).
  const blocked = freshHarness(freshWorld({ blockOn: 'department:4044_103150:1' }));
  await blocked.steps(30);
  const [end] = probeRecords(blocked, 'end');
  assert.equal(end.outcome, 'blocked');
  const sign = blocked.records.find(row => row.kind === 'read' && row.outcome === 'blocked');
  assert.deepEqual([sign.lane, sign.catId, Boolean(sign.probe)], ['departments', '4044_103150', true]);
  blocked.advance(6 * 60_000); // past the 5 min back-off, inside the day of gentler reads
  await blocked.steps(200, 3_000);
  assert.ok(freshAsks(blocked).some(row => row.at > sign.at + 6 * 60_000 && row.what === 'search:desk:1'));
  assert.equal(blocked.records.filter(row => row.kind === 'read' && row.probe && row.at > sign.at).length, 0);
  assert.equal(probeRecords(blocked, 'start').length, 1);
});

test('every read goes to the snapshot log with its shape and products, searches only as a hash', async () => {
  const h = freshHarness(freshWorld(), { searches: { members: ['Desk  Lamp'] } });
  await h.steps(30);
  const reads = h.records.filter(row => row.kind === 'read');
  assert.equal(reads.length, h.asked.length, 'one record per read');
  const v80 = reads.find(row => row.lane === 'value80' && row.page === 2);
  assert.deepEqual([v80.source, v80.sort, v80.minPrice, v80.outcome, v80.totalPages], ['fast', 'price_high', 79, 'items', 3]);
  assert.deepEqual(v80.items[0], ['5100200', 1, 20_000, 'i']);
  const search = reads.find(row => row.lane === 'requests');
  assert.equal(search.searchHash, fnv('desk lamp'));
  assert.deepEqual(search.items[0], [String(NEW), 1, 15_000, 'i', { c: '0:4044:103150' }]);
  const text = JSON.stringify(h.records);
  for (const word of ['Desk', 'Lamp', 'lamp', 'desk']) assert.equal(text.includes(word), false, word);
  assert.equal(h.FastReads.queryHash('  Desk   LAMP '), fnv('desk lamp'));
  assert.equal(h.FastReads.queryHash(''), null);
});

// --- Nov 23 (2.32.8): new80, uploads off the read path, the read budget ----------
const tick = () => new Promise(resolve => setImmediate(resolve));
const NEW80 = [offer(5_300_001, 150), offer(5_300_002, 99), offer(5_300_003, 20)];
const new80Reply = (items = NEW80) => url => describe(url) === 'new80:1'
  ? { ok: true, status: 200, totalPages: 8, products: 40, items } : value80Reply()(url);

test('new80: sort=new&min_price=79 page 1 is read every 40 s at rank 0 and ingested as its own page', async () => {
  const h = harness({ respond: new80Reply() });
  assert.equal(h.FastReads.INTERVALS.new80, 40_000);
  await h.steps(600, 1_200); // 12 minutes at ~50 reads a minute
  assert.equal(new URL(h.asked.find(row => row.what === 'new80:1').url).search, '?sort=new&min_price=79');
  assert.equal(h.asked.some(row => row.what.startsWith('new80:') && row.what !== 'new80:1'), false, 'page 1 only');
  const late = gaps(h.asked.filter(row => row.at >= 1_000_000 + 60_000), 'new80:1');
  assert.ok(late.length >= 14 && late.every(gap => gap >= 40_000 && gap <= 46_000), String(late));
  // Ingested like the other lists, so it can alert: page 1, lane new80 and no
  // target index, the page identity a held restock must be confirmed by.
  const sent = h.sent.filter(row => row.lane === 'new80');
  assert.ok(sent.length >= 1 && sent.every(row => row.page === 1 && !('targetIndex' in row)), JSON.stringify(sent));
  assert.deepEqual(plain(sent[0].items), NEW80);
  assert.ok(sent.length <= 4, 'an unchanged page is resent only every 5 minutes');
  assert.equal(h.logs.find(row => row.lane === 'new80').count, 2, 'logs how many $80+ offers it holds');
  assert.ok(gaps(h.asked, 'value:1').slice(4).every(gap => gap <= 12_000), 'price_high page 1 stays first, at 10 s');
});

test('a restock held on new80 page 1 is re-read 10 s later, first among the due $80+ pages', async () => {
  let holds = 1;
  let awaiting = false;
  const h = harness({ respond: new80Reply(),
    send: (items, page, lane) => {
      if (lane === 'new80' && holds > 0) { holds -= 1; awaiting = true; return { ok: true, held: 1 }; }
      if (lane === 'new80') awaiting = false;
      return { ok: true, summary: { new: 0 } };
    },
    mustSend: items => awaiting && items.some(entry => entry.item_id === 'ip-5300001') });
  await h.steps(300, 1_200);
  const reads = h.asked.filter(row => row.what === 'new80:1').map(row => row.at);
  const sends = h.sent.filter(row => row.lane === 'new80').map(row => row.at);
  const next = reads.find(at => at > sends[0]);
  assert.ok(next - sends[0] >= 10_000 && next - sends[0] <= 11_200, `re-read ${next - sends[0]} ms after the hold`);
  assert.equal(sends[1], next, 'the unchanged page is sent again so the hold is settled');
  const later = reads.filter(at => at > next);
  assert.ok(later.length >= 3 && later.slice(1).every((at, index) => at - later[index] >= 40_000), 'then 40 s again');
});

test('the next read starts while the last upload is unanswered; never two Walmart requests at once', async () => {
  const answers = [];
  let release = null;
  let inFlight = 0;
  let most = 0;
  let serial = 0;
  const h = harness({
    // Every price_high page 1 read shows a new offer, so every read of it is sent.
    respond: url => {
      inFlight += 1;
      most = Math.max(most, inFlight);
      const reply = describe(url) === 'value:1'
        ? { ok: true, status: 200, totalPages: 25, products: 40, items: [offer(6_000_000 + serial++, 150)] }
        : { ok: true, status: 200, items: [] };
      return new Promise(resolve => { release = () => { inFlight -= 1; resolve(reply); }; });
    },
    send: () => new Promise(resolve => answers.push(resolve)) });
  const first = h.api.run();
  await tick();
  assert.equal(h.asked.length, 1);
  await h.api.run(); // the timer and the alarm can both fire: a read is running, so nothing starts
  const probe = h.api.probe(`${HOME}?q=lamp&queryNups=lamp&sort=new`);
  await tick();
  assert.equal(h.asked.length, 1, 'the probe waits for the running read');
  h.advance(900);
  release();
  await first;
  assert.deepEqual([h.sent.length, answers.length], [1, 1], 'read 1 handed over, its upload unanswered');
  h.advance(100);
  const second = h.api.run(); // the probe, while upload 1 is still unanswered
  await tick();
  assert.equal(h.asked.length, 2);
  assert.equal(h.asked[1].at - h.asked[0].at, 1_000, 'no wait for the upload');
  release();
  await second;
  assert.equal((await probe).ok, true);
  for (let index = 0; index < 20; index += 1) {
    answers.splice(0).forEach(answer => answer({ ok: true, summary: { new: 1 } }));
    h.advance(1_000);
    release = null;
    const pending = h.api.run();
    await tick();
    release?.();
    await pending;
  }
  assert.ok(h.asked.length >= 15, String(h.asked.length));
  assert.equal(most, 1, 'one Walmart request in flight at any time');
});

test('three unanswered uploads hold the next read until the oldest is answered', async () => {
  const answers = [];
  let serial = 0;
  const h = harness({
    // Every read shows a new offer: every read is sent.
    respond: () => ({ ok: true, status: 200, totalPages: 25, products: 40, items: [offer(6_400_000 + serial++, 150)] }),
    send: () => new Promise(resolve => answers.push(resolve)) });
  assert.equal(h.FastReads.MAX_UPLOADS, 3);
  for (let index = 0; index < 3; index += 1) {
    await h.api.run();
    h.advance(1_200);
  }
  assert.deepEqual([h.asked.length, answers.length], [3, 3]);
  const blocked = h.api.run();
  await tick();
  assert.equal(h.asked.length, 3, 'a fourth read waits');
  h.advance(5_000);
  await tick();
  assert.equal(h.asked.length, 3, 'for as long as the oldest upload is unanswered');
  answers[0]({ ok: true, summary: { new: 1 } });
  await blocked;
  assert.deepEqual([h.asked.length, answers.length], [4, 4], 'then it starts');
});

test('uploads keep read order; while one is unanswered an unchanged page is sent again; answers act as before', async () => {
  const answers = [];
  const items = [offer(6_100_001, 150)];
  const list80 = [offer(6_200_002, 120)];
  const h = harness({
    respond: url => describe(url) === 'value:1' ? { ok: true, status: 200, totalPages: 25, products: 40, items }
      : describe(url) === 'value80:2' ? { ok: true, status: 200, totalPages: 8, products: 40, items: list80 }
        : { ok: true, status: 200, items: [] },
    send: (_items, page, lane) => new Promise(resolve => answers.push({ resolve, lane, page })) });
  const value1 = () => h.sent.filter(row => row.lane === 'value' && row.page === 1).length;
  const reads = what => h.asked.filter(row => row.what === what).length;
  await h.steps(1, 1_200); // value:1, sent
  assert.equal(value1(), 1);
  // Until value:1 is read again 10 s later, its upload stays unanswered.
  while (reads('value:1') < 2) await h.steps(1, 1_200);
  assert.equal(value1(), 2, 'unchanged, but sent: the first upload\'s holds are not known yet');
  assert.deepEqual(answers.slice(0, 2).map(row => `${row.lane}:${row.page}`), ['value:1', 'value80:2'],
    'handed to the uploader in read order');
  h.advance(300);
  answers.splice(0).forEach((row, index) => row.resolve({ ok: true, summary: { new: 0 },
    ...(index === 0 ? { requestId: 'b'.repeat(32) } : {}) }));
  await tick();
  // Each answer is logged when it comes: its request id and the time from the read's start.
  const logged = h.logs.find(row => row.requestId === 'b'.repeat(32));
  assert.deepEqual([logged.lane, logged.page, logged.outcome], ['value', 1, 'items']);
  assert.ok(logged.durationMs >= 300, String(logged.durationMs));
  // All answered: the next unchanged read of value:1 is skipped, as before.
  while (reads('value:1') < 3) await h.steps(1, 1_200);
  assert.equal(value1(), 2, 'unchanged and nothing unanswered: not sent');
  // Another page's unanswered upload may hold back a restock this page shows
  // too, so until it is answered no page counts as unchanged.
  list80[0] = offer(6_200_003, 125);
  while (!answers.length) await h.steps(1, 1_200);
  assert.deepEqual([answers[0].lane, answers[0].page], ['value80', 2]);
  let count = reads('value:1');
  while (reads('value:1') === count) await h.steps(1, 1_200);
  assert.equal(value1(), 3, 'unchanged value:1 is sent while the value80:2 upload is unanswered');
  answers.splice(0).forEach(row => row.resolve({ ok: true, summary: { new: 0 } }));
  await tick();
  count = reads('value:1');
  while (reads('value:1') === count) await h.steps(1, 1_200);
  assert.equal(value1(), 3, 'answered: skipped again');
  // A refused upload leaves the page unsent: its next read is sent again.
  items[0] = offer(6_100_009, 150);
  count = reads('value:1');
  while (reads('value:1') === count) await h.steps(1, 1_200);
  assert.equal(value1(), 4);
  answers.splice(0).forEach(row => row.resolve(row.lane === 'value' ? { ok: false, retryable: true }
    : { ok: true, summary: { new: 0 } }));
  await tick();
  count = reads('value:1');
  while (reads('value:1') === count) await h.steps(1, 1_200);
  assert.equal(value1(), 5, 'the refused page is sent again');
});

test('a held answer that comes after later reads still brings its page back 10 s after the answer', async () => {
  const answers = [];
  let fresh = false;
  const h = harness({
    respond: url => describe(url) === 'value80:2' && fresh
      ? { ok: true, status: 200, totalPages: 7, products: 40, items: [offer(6_300_001, 130), offer(5_000_020, 142)] }
      : value80Reply()(url),
    send: (_items, page, lane) => new Promise(resolve => answers.push({ resolve, lane, page })) });
  const settle = (keep = () => false) => {
    for (const row of answers.splice(0)) {
      if (keep(row)) answers.push(row);
      else row.resolve({ ok: true, summary: { new: 0 } });
    }
  };
  const isHeld = row => row.lane === 'value80' && row.page === 2;
  const reads = () => h.asked.filter(row => row.what === 'value80:2').map(row => row.at);
  for (let step = 0; step < 60; step += 1) {
    await h.steps(1, 1_200);
    settle();
  }
  fresh = true; // a new offer on value80:2: its next read is sent
  const before = reads().length;
  while (reads().length === before) {
    await h.steps(1, 1_200);
    settle(isHeld);
  }
  const heldAt = reads().at(-1);
  for (let step = 0; step < 2; step += 1) { // two more reads start before the answer comes
    await h.steps(1, 1_200);
    settle(isHeld);
  }
  assert.equal(answers.filter(isHeld).length, 1);
  answers.find(isHeld).resolve({ ok: true, held: 1 });
  const answeredAt = h.now();
  await tick();
  settle();
  while (reads().length === before + 1) {
    await h.steps(1, 1_200);
    settle();
  }
  const next = reads().at(-1);
  assert.ok(next - answeredAt >= 10_000 && next - answeredAt <= 11_200, `re-read ${next - answeredAt} ms after the answer`);
  assert.ok(next - heldAt < 20_000, `not at its usual 40 s (${next - heldAt} ms after the held read)`);
});

test('in the gentler day after a bot-check sign neither new80 nor any hot search is read; both return after it', async () => {
  const start = 1_000_000;
  const h = harness({ hotSeeds: null, start,
    respond: url => describe(url).startsWith('search:') ? searchReply([item(9_100_000)]) : new80Reply()(url),
    store: { reviewerFastReads: { blocks: 0, until: 0, slowUntil: start + 24 * 60 * 60_000 } } });
  await h.steps(400, 750); // 5 minutes
  const starts = h.asked.map(row => row.at);
  assert.ok(starts.length >= 90, String(starts.length));
  assert.ok(starts.slice(1).every((at, index) => at - starts[index] >= h.FastReads.SAFE_SPACING_MS), 'the old 2.5 s');
  assert.equal(h.asked.some(row => row.what.startsWith('new80:')), false, 'no new80 read');
  assert.equal(h.asked.some(row => row.what.startsWith('search:')), false, 'no hot search');
  assert.ok(gaps(h.asked, 'value:1').slice(2).every(gap => gap <= 13_500), 'price_high page 1 still first');
  assert.equal(h.sent.some(row => row.lane === 'new80'), false);
  h.advance(24 * 60 * 60_000);
  const after = h.asked.length;
  await h.steps(300, 1_200); // 6 minutes
  assert.ok(h.asked.slice(after).filter(row => row.what === 'new80:1').length >= 7, 'new80 is back');
  assert.ok(SEEDS.every(seed => h.asked.slice(after).some(row => row.what === `search:${seed}:1`)), 'hot searches too');
});

test('never more than 48 reads a minute, however fast Walmart answers', async () => {
  const h = harness({ departments: [{ catId: '4044', index: 1 }, { catId: '4171', index: 2 }],
    respond: url => describe(url).startsWith('department:')
      ? { ok: true, status: 200, items: [item(5_000_000)], products: 1, totalPages: 25, totalItems: 1_000 }
      : valueReply([120, 95, 85, 70])(url) });
  // 54 from Nov 23; 48 since Nov 26, so 2.32.9's shorter search reads cannot raise the load.
  assert.deepEqual([h.FastReads.RATE_PER_MINUTE, h.FastReads.RATE_BURST], [48, 10]);
  await h.steps(6_000, 100); // 10 minutes in 0.1 s ticks; every read takes no time; spare slots read departments
  const starts = h.asked.map(row => row.at);
  const within = (from, ms) => starts.filter(at => at >= from && at < from + ms).length;
  assert.ok(starts.length <= 10 + 480 + 1, `${starts.length} reads in 10 minutes`);
  assert.ok(starts.length >= 430, `the budget is used while something is due (${starts.length})`);
  assert.ok(starts.every(at => within(at, 60_000) <= 10 + 48), 'any minute: the budget plus the 10-read lead');
  // Once the lead is spent, the reads are paced evenly rather than in bursts.
  assert.ok(starts.filter(at => at >= 1_000_000 + 60_000).every(at => within(at, 60_000) <= 49), 'steady minutes');
  const spacing = starts.slice(1).map((at, index) => at - starts[index]);
  assert.ok(spacing.every(gap => gap >= h.FastReads.MIN_SPACING_MS), 'the spacing still holds');
  assert.ok(gaps(h.asked, 'value:1').slice(2).every(gap => gap <= 12_500), 'price_high page 1 keeps its 10 s');
});
