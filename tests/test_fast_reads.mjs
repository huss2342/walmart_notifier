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
  for (const url of [`${HOME}?sort=new&min_price=79`, `${HOME}?sort=price_high&min_price=50`,
    `${HOME}?q=sofa&queryNups=sofa&sort=price_high&min_price=79`]) {
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
// advances time between runs the way the worker's timer chain would.
function harness({ respond = () => ({ ok: true, status: 200, items: [] }), searches = [], departments = [],
  onBlocked = async () => {}, paused = async () => false, send = null, mustSend = undefined } = {}) {
  let now = 1_000_000;
  const clock = class extends Date { static now() { return now; } };
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
    ...(mustSend ? { mustSend } : {}),
    setTimer: () => 1, clearTimer: () => {}, random: () => 0.5
  });
  return { api, store, sent, logs, asked, FastReads: context.FastReads,
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
  if (parsed.searchParams.get('min_price') === '79') return `value80:${page}`;
  return `${parsed.searchParams.get('sort') === 'price_high' ? 'value' : 'newest'}:${page}`;
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
  await h.steps(20); // the $80+ list's first reads go first
  const reply = await pending;
  assert.equal(reply.ok, true);
  const at = h.asked.findIndex(row => row.url.includes('research=1'));
  assert.ok(at >= 4 && at <= 20, `served right after the due $80+ pages: ${at}`);
  assert.ok(gaps(h.asked, 'value:1').every(gap => gap <= 12_500), 'never delays value page 1');
  assert.equal(h.sent.some(row => row.lane === 'research'), false, 'never ingested');
  assert.deepEqual(h.logs.filter(row => row.lane === 'research').map(row => [row.outcome, row.items, row.totalPages]),
    [['items', 1, 7]]);
  assert.equal(await h.api.research('https://www.walmart.com/ip/123'), null);
});

test('price_high page 1 is read every 10 s and every other $80+ page every 20 s', async () => {
  const h = harness({ respond: valueReply([120, 95, 85, 70, 55, 45, 35]) });
  await h.steps(120);
  assert.deepEqual(h.asked.slice(0, 4).map(row => row.what), ['value:1', 'value:2', 'value:3', 'value:4']);
  const page1 = gaps(h.asked, 'value:1');
  assert.ok(page1.length >= 20);
  assert.ok(page1.every(gap => gap >= 10_000 && gap <= 15_000), String(page1));
  for (const page of [2, 3]) {
    const fast = gaps(h.asked, `value:${page}`).slice(1); // after the start-up burst of first reads
    assert.ok(fast.length >= 10 && fast.every(gap => gap >= 20_000 && gap <= 25_000), `page ${page}: ${fast}`);
  }
  // Page 3 is all $80+, so page 4 is checked at the fast cadence too, in case a
  // new listing spills an $80+ item onto it; pages after that wait for the pass.
  assert.ok(gaps(h.asked, 'value:4').slice(1).every(gap => gap <= 25_000), 'boundary page checked fast');
  assert.ok(gaps(h.asked, 'value:5').every(gap => gap >= 240_000), 'page 5 waits for the pass');
  assert.ok(h.asked.some(row => row.what === 'value:8'), 'the first page without a $40+ item ends the pass');
  assert.equal(h.asked.some(row => row.what === 'value:9'), false);
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
    assert.ok(gaps(h.asked, `value:${page}`).slice(1).every(gap => gap <= 25_000), `page ${page} stays in the fast zone`);
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
  const start = deep.indexOf(3);
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
test('sort=new page 1 is read every 5 minutes and later new pages rarely', async () => {
  const h = harness({ respond: valueReply([120, 95, 85, 70]) });
  await h.steps(480); // 20 minutes
  const page1 = gaps(h.asked, 'newest:1');
  assert.ok(page1.length >= 3 && page1.every(gap => gap >= 300_000 && gap <= 310_000), String(page1));
  assert.ok(h.asked.filter(row => row.what === 'newest:2').length <= 2);
  assert.ok(h.asked.filter(row => row.what === 'newest:4').length <= 2);
});

test('read starts stay about 1.6 seconds apart with jitter', async () => {
  const h = harness({ respond: valueReply([120]) });
  await h.api.run();
  await h.api.run();
  h.advance(1_000);
  await h.api.run();
  assert.equal(h.asked.length, 1);
  h.advance(600);
  await h.api.run();
  assert.equal(h.asked.length, 2, '1.4 s plus the 0.2 s jitter this harness draws');
  await h.steps(60, 500);
  const starts = h.asked.map(row => row.at);
  assert.ok(starts.slice(1).every((at, index) => at - starts[index] >= 1_600));
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

test('member searches outrank priority searches; empty ones wait thirty minutes', async () => {
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
  assert.ok(sofa.length >= 8 && sofa.every(gap => gap >= 240_000 && gap <= 245_000), `searches with products: 4 min ${sofa}`);
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
  await h.steps(20);
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
  assert.ok(lateGaps('value:2').every(gap => gap <= 26_000), 'other $80+ pages');
  assert.ok(lateGaps('value80:6').every(gap => gap <= 50_000), 'pages of the $80+ list');
  const newest = lateGaps('newest:1');
  assert.ok(newest.length >= 6 && newest.every(gap => gap >= 300_000 && gap <= 390_000), `newest every ~5 min: ${newest}`);
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
