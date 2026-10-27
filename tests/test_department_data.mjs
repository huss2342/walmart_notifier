import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const home = 'https://www.walmart.com/reviews/claim-product';
const source = fs.readFileSync(new URL('../extension/department-data.js', import.meta.url), 'utf8');
// Reduced from the actual public filter module; account data and store params
// are excluded. IDs and names are observed values, not generated guesses.
const captured = JSON.parse(fs.readFileSync(new URL('./fixtures/reviewer-departments.json', import.meta.url), 'utf8'));
const context = vm.createContext({ URL, URLSearchParams });
vm.runInContext(source, context);
const api = context.ReviewerDepartmentData;
const values = data => data.props.pageProps.initialData.contentLayout.modules[0].configs.allSortAndFilterFacets[0].values;
function fixture() {
  const data = structuredClone(captured);
  data.props.pageProps.initialSearchQueryVariables.page = 1;
  return { data, doc: { querySelector: () => ({ textContent: JSON.stringify(data) }) } };
}

test('actual catalogue data supplies every top-level name and exact destination without clicking or recursing', () => {
  const f = fixture();
  values(f.data)[0].values = [{ id: '1085632_99', name: 'A child', type: 'cat_id' }];
  const targets = api.catalogue(f.doc, home);
  assert.equal(targets.length, values(f.data).length);
  assert.ok(targets.length >= 30);
  assert.equal(targets.find(t => t.label === 'Home').url, `${home}?catId=4044`);
  assert.equal(targets.find(t => t.label === 'Clothing, Shoes & Accessories').url, `${home}?catId=5438`);
  assert.equal(targets.some(t => t.label === 'A child'), false);
  for (const target of targets) assert.equal(api.destination(target.url, target.parentUrl), target.url);
});

for (const suffix of ['?q=Pet', '?queryNups=Pet', '?catId=4044', '?facet=brand:Acme',
  '?unknown=filter', '?page=0', '?page=101', '#filter']) {
  test(`catalogue discovery refuses a filtered or invalid parent ${suffix}`, () => {
    assert.equal(api.catalogue(fixture().doc, home + suffix), null);
  });
}

const corruptions = {
  'stale query': f => { f.props.pageProps.initialSearchQueryVariables.query = 'Pet'; },
  'stale category': f => { f.props.pageProps.initialSearchQueryVariables.catId = '4044'; },
  'stale page': f => { f.props.pageProps.initialSearchQueryVariables.page = 2; },
  'missing module': f => { f.props.pageProps.initialData.contentLayout.modules = []; },
  'duplicate module': f => { f.props.pageProps.initialData.contentLayout.modules.push(structuredClone(f.props.pageProps.initialData.contentLayout.modules[0])); },
  'duplicate ID': f => { values(f).push(structuredClone(values(f)[0])); },
  'duplicate name': f => { values(f)[1].name = values(f)[0].name.toUpperCase(); },
  'hierarchical ID': f => { values(f)[0].id = '4044_99'; },
  'mismatched public parameter': f => { values(f)[0].url = 'cat_id=4044'; },
  'selected category': f => { values(f)[0].isSelected = true; },
  'missing value': f => { values(f)[0].name = ''; }
};
for (const [name, change] of Object.entries(corruptions)) {
  test(`catalogue drift fails explicitly rather than returning partial destinations: ${name}`, () => {
    const f = fixture(); change(f.data);
    assert.equal(api.catalogue(f.doc, home), null);
  });
}

test('malformed or missing page data is contained', () => {
  for (const raw of ['', 'not json', '{}']) assert.equal(api.catalogue({ querySelector: () => ({ textContent: raw }) }, home), null);
  assert.equal(api.catalogue({ querySelector: () => null }, home), null);
});

test('destinations are restricted to one top-level reviewer category without search, brands or external routes', () => {
  assert.equal(api.destination(`${home}?catId=4044`, home), `${home}?catId=4044`);
  for (const url of [`${home}?catId=4044&q=Pet`, `${home}?catId=4044&facet=brand:Acme`,
    `${home}?catId=4044&catId=5438`, `${home}?catId=4044_99`, `${home}?catId=0`,
    `${home}?catId=4044#x`, 'https://evil.test/reviews/claim-product?catId=4044',
    'https://www.walmart.com/blocked?catId=4044']) assert.equal(api.destination(url, home), null, url);
});

test('fresh-result authorization requires the exact applied chip in one current filter section', () => {
  const button = label => ({ getAttribute: key => key === 'aria-label' ? label : null });
  let buttons = [button('Filter by Home applied, activate to change , filter applied')];
  const root = { getAttribute: () => null, querySelectorAll: () => buttons };
  let roots = [root];
  const doc = { querySelectorAll: () => roots };
  assert.equal(api.applied(doc, 'Home'), true);
  assert.equal(api.applied(doc, 'Clothing, Shoes & Accessories'), false);
  assert.equal(api.applied(doc, 'Home Improvement'), false);
  buttons.push(button('Filter by Home applied, activate to change , filter applied'));
  assert.equal(api.applied(doc, 'Home'), false);
  buttons.pop(); roots = [root, root];
  assert.equal(api.applied(doc, 'Home'), false);
  roots = [root]; root.hidden = true;
  assert.equal(api.applied(doc, 'Home'), false);
});

test('both real extension contexts load the department contract', () => {
  const manifest = JSON.parse(fs.readFileSync(new URL('../extension/manifest.json', import.meta.url), 'utf8'));
  assert.ok(manifest.content_scripts.some(script => script.js.includes('department-data.js') && script.js.includes('content.js')));
  assert.match(fs.readFileSync(new URL('../extension/background.js', import.meta.url), 'utf8'), /importScripts\([^\n]*'department-data.js'/);
});

test('a shortened applied name is authorized by the fresh category ID, and another department cannot bind', () => {
  let category = { catId: '5438', name: 'Clothing' };
  const button = { getAttribute: key => key === 'aria-label' ? 'Filter by Clothing applied, activate to change , filter applied' : null };
  const root = { getAttribute: () => null, querySelectorAll: () => [button] };
  const doc = { querySelectorAll: () => [root], querySelector: () => ({ textContent: JSON.stringify({ props: {
    pageProps: { initialData: { searchResult: { catInfo: category } } }
  } }) }) };
  assert.equal(api.applied(doc, 'Clothing, Shoes & Accessories', `${home}?catId=5438`), true);
  assert.equal(api.applied(doc, 'Clothing, Shoes & Accessories', `${home}?catId=4044`), false);
  category = { catId: '4044', name: 'Home' };
  assert.equal(api.applied(doc, 'Clothing, Shoes & Accessories', `${home}?catId=5438`), false);
  category = null;
  assert.equal(api.applied(doc, 'Clothing, Shoes & Accessories', `${home}?catId=5438`), false);
});
