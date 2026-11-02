import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = ['sweep-modes.js', 'runtime-policy.js', 'scanner-settings.js']
  .map(name => fs.readFileSync(new URL(`../extension/${name}`, import.meta.url), 'utf8')).join('\n');
const api = vm.runInNewContext(`${source}; ({ auxiliaryQueries, priorityQueries: (...args) => priorityQueries(...args), sweepQueries, pageNavigationDelay, scanScopeAllowed,
  validate: ReviewerScannerSettings.project })`, { URL });

test('priority searches have a short furniture-first queue independently of the hundred-keyword round', () => {
  const expected = ['Furniture', 'Sofa', 'Couch', 'Sectional', 'Recliner', 'Mattress', 'Bed', 'Desk', 'Dining Table', 'Dresser'];
  assert.deepEqual(Array.from(api.priorityQueries()), expected);
  assert.deepEqual(Array.from(api.priorityQueries(null)), expected);
  assert.deepEqual(Array.from(api.priorityQueries(['  Mattress ', 'SOFA', 'mattress', 'Dining   Table'])),
    ['Mattress', 'SOFA', 'Dining Table']);
  assert.deepEqual(Array.from(api.priorityQueries(' Mattress\n\nSOFA\nMattress\nDining Table\n')),
    ['Mattress', 'SOFA', 'Dining Table']);
  assert.deepEqual(Array.from(api.priorityQueries([])), []);
  assert.deepEqual(Array.from(api.priorityQueries('')), []);
  assert.equal(api.auxiliaryQueries().length, 100);
});

test('priority searches enforce their independent list and text bounds', () => {
  assert.equal(api.priorityQueries(Array.from({ length: 24 }, (_, i) => `Item ${i}`)).length, 24);
  assert.deepEqual(Array.from(api.priorityQueries(['\u{1f6cf}'.repeat(80)])), ['\u{1f6cf}'.repeat(80)]);
  for (const value of [{}, [7], [''], ['a'.repeat(81)], ['Bed\nDesk'], ['\tSofa'], ['Bed\u0085'], ['\ud800'],
    Array.from({ length: 25 }, (_, i) => `Item ${i}`)]) {
    assert.throws(() => api.priorityQueries(value));
  }
});

test('custom general keyword lists can append coverage up to 128 without changing the hundred defaults', () => {
  assert.equal(api.auxiliaryQueries().length, 100);
  assert.equal(api.auxiliaryQueries(Array.from({ length: 128 }, (_, i) => `Item ${i}`)).length, 128);
  assert.throws(() => api.auxiliaryQueries(Array.from({ length: 129 }, (_, i) => `Item ${i}`)), /128/);
});

test('native fallback searches retain exact scope and reject duplicated query parameters', () => {
  const base = 'https://www.walmart.com/reviews/claim-product';
  assert.equal(api.scanScopeAllowed(`${base}?q=Accessories&queryNups=Accessories`, 'none', true), true);
  for (const params of ['q=Accessories&queryNups=Accessories&queryNups=Cat',
    'q=Accessories&q=Cat&queryNups=Accessories', 'q=Accessories&queryNups=Cat']) {
    assert.equal(api.scanScopeAllowed(`${base}?${params}`, 'none', true), false);
  }
});

test('keyword and department pagination use the requested page delay across refresh intervals', () => {
  for (const refreshMinutes of [5, 30, 120]) {
    for (const total of [2, 25, 100]) {
      for (const targetKey of ['keyword:smartwatch', 'department:3944']) {
        const timing = api.pageNavigationDelay({ refreshMinutes, pageDelaySeconds: 15 },
          { mode: 'none', targetKey, total }, 100_000, 110_000, 0.5);
        assert.equal(timing.intervalMs, 15_000, `${targetKey}/${refreshMinutes}/${total}`);
        assert.equal(timing.delayMs, 5_000);
      }
    }
  }
});

test('auxiliary pagination retains delay minimums, jitter and time already spent loading', () => {
  const sweep = { mode: 'none', targetKey: 'keyword:smartwatch', total: 2 };
  const settings = { refreshMinutes: 30, pageDelaySeconds: 20 };
  assert.equal(api.pageNavigationDelay(settings, sweep, 100_000, 100_000, 0).intervalMs, 20_000);
  const upper = api.pageNavigationDelay(settings, sweep, 100_000, 110_000, 1);
  assert.equal(upper.intervalMs, 25_000);
  assert.equal(upper.delayMs, 15_000);
  assert.equal(api.pageNavigationDelay(settings, sweep, 100_000, 130_000, 1).delayMs, 0);
});

test('homepage browsing still allocates its refresh interval', () => {
  const settings = { refreshMinutes: 30, pageDelaySeconds: 15 };
  assert.equal(api.pageNavigationDelay(settings, { mode: 'none', total: 2 }, 0, 0, 0.5).intervalMs, 90_000);
  assert.equal(api.pageNavigationDelay(settings, { mode: 'high-value', total: 2 }, 0, 0, 0.5).intervalMs, 15_000);
});

test('default additional searches cover wearables within the shared settings limit', () => {
  const queries = Array.from(api.auxiliaryQueries());
  assert.ok(queries.includes('Smartwatch'));
  assert.ok(queries.includes('Smart Watch'));
  assert.ok(queries.includes('Fitness Tracker'));
  assert.ok(queries.includes('Espresso Machine'));
  assert.ok(queries.includes('Mattress'));
  assert.ok(queries.length <= 100);
  assert.equal(new Set(queries.map(query => query.toLowerCase())).size, queries.length);
  assert.equal(api.validate({ auxiliaryKeywordQueries: queries }).auxiliaryKeywordQueries.length, queries.length);
  assert.deepEqual(Array.from(api.auxiliaryQueries(['Custom item'])), ['Custom item']);
});

test('additional searches prioritize observed category wording and bounded wildcard trials', () => {
  const queries = Array.from(api.auxiliaryQueries());
  for (const query of ['Large Appliance', 'Kitchen Appliance', 'Cordless', 'Ride On',
    'Sports Watch', 'AMOLED', 'Large Appliance*', 'Kitchen Appliance*',
    'Large Appliance&', 'Kitchen Appliance&']) {
    assert.ok(queries.includes(query), `Missing additional coverage for ${query}`);
  }
  assert.equal(queries.length, 100, 'The curated round must fit the settings limit');
  const legacy = Array.from(api.sweepQueries('high-value'));
  for (const query of ['Bamboo', 'Mesh', 'Vintage', 'Floral', 'Fold', 'Gold Decor', 'Hotel',
    'Stainless', 'Steel', 'Glass']) {
    assert.equal(queries.includes(query), false, `${query} should make room in additional coverage`);
    assert.ok(legacy.includes(query), `Legacy High Value must retain ${query}`);
  }
  assert.deepEqual(Array.from(api.auxiliaryQueries(['Personal seed'])), ['Personal seed'],
    'Saved custom lists must not acquire default terms');
});

test('appliance discovery promotes existing broad terms while preserving the same hundred searches', () => {
  const previousDefaults = [
    'Large Appliance', 'Kitchen Appliance', 'Cordless', 'Ride On', 'Sports Watch', 'AMOLED',
    'Large Appliance*', 'Kitchen Appliance*', 'Large Appliance&', 'Kitchen Appliance&',
    'Smartwatch', 'Smart Watch', 'Fitness Tracker', 'Smart Ring', 'Laptop', 'Tablet',
    'Smartphone', 'Television', 'OLED TV', 'Camera', 'Drone', 'Gaming Console', 'VR Headset',
    'Graphics Card', 'Robot Vacuum', 'Espresso Machine', 'Refrigerator', 'Washer', 'Dryer',
    'Dishwasher', 'Air Conditioner', 'Generator', 'Lawn Mower', 'E-Bike', 'Electric Scooter',
    'Mattress', 'Stroller', 'Car Seat', 'Vacuum', 'Monitor', 'Projector', 'Pressure Washer',
    'Power Tool Set', 'Luggage', 'Desk', 'Furniture', 'Chair', 'Chairs', 'Couch', 'Sofa', 'Pet',
    'Accessories', 'Appliance', 'Bag', 'Baking', 'Bath', 'Bathroom', 'Bedding', 'Bedroom',
    'Bulk', 'Car', 'Cat', 'Cleaning', 'Clothes', 'Cooking', 'Crafts', 'Dining', 'Dish', 'Dog',
    'Electronics', 'Exercise', 'Frame', 'Gaming', 'Gifts', 'Home', 'Home Decor',
    'Home Improvement', 'Jewelry', 'Kitchen', 'LED', 'Machine', 'Maker',
    'Office/School Supplies', 'Organization', 'Pantry', 'Personal Care', 'Playpen', 'Repair',
    'Shade', 'Shelves', 'Shower', 'Storage', 'Supplies', 'Tech', 'Tools', 'Travel',
    'Utensils', 'Vanity', 'Wireless', 'Wood'
  ];
  assert.equal(previousDefaults.indexOf('Maker'), 81);
  assert.equal(previousDefaults.indexOf('Appliance'), 52);
  const queries = Array.from(api.auxiliaryQueries());
  assert.deepEqual(queries.slice(0, 12), [
    'Large Appliance', 'Kitchen Appliance', 'Maker', 'Appliance', 'Cordless',
    'Large Appliance&', 'Kitchen Appliance&', 'Ride On', 'Sports Watch', 'AMOLED',
    'Large Appliance*', 'Kitchen Appliance*'
  ]);
  assert.equal(queries.indexOf('Maker'), 2);
  assert.equal(queries.indexOf('Appliance'), 3);
  assert.equal(queries.length, 100);
  assert.equal(new Set(queries.map(query => query.toLowerCase())).size, 100);
  assert.deepEqual([...queries].sort(), [...previousDefaults].sort());
});

test('discovery priority does not reorder custom searches or alter the legacy High Value mode', () => {
  const custom = ['Dryer', 'Maker', 'Kitchen Appliance&', 'Appliance', 'Smart Watch'];
  assert.deepEqual(Array.from(api.auxiliaryQueries(custom)), custom);
  assert.deepEqual(Array.from(api.auxiliaryQueries(custom.join('\n'))), custom);
  assert.deepEqual(Array.from(api.auxiliaryQueries([])), []);
  assert.deepEqual(Array.from(api.sweepQueries('high-value')), [
    'Accessories', 'Appliance', 'Bag', 'Baking', 'Bamboo', 'Bath', 'Bathroom',
    'Bedding', 'Bedroom', 'Bulk', 'Car', 'Cat', 'Chairs', 'Cleaning', 'Clothes',
    'Cooking', 'Crafts', 'Dining', 'Dish', 'Dog', 'Electronics', 'Exercise',
    'Floral', 'Fold', 'Frame', 'Furniture', 'Gaming', 'Gifts', 'Glass',
    'Gold Decor', 'Home', 'Home Decor', 'Home Improvement', 'Hotel', 'Jewelry',
    'Kitchen', 'LED', 'Machine', 'Maker',
    'Mesh', 'Office/School Supplies', 'Organization', 'Pantry', 'Personal Care',
    'Pet', 'Playpen', 'Repair', 'Shade', 'Shelves', 'Shower', 'Stainless', 'Steel',
    'Storage', 'Supplies', 'Tech', 'Tools', 'Travel', 'Utensils', 'Vanity',
    'Vintage', 'Wireless', 'Wood'
  ]);
});
