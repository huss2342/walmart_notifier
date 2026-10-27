import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// A small DOM fixture implements the browser APIs used by the helper, with real
// parent/child containment and dynamic controls instead of selector return stubs.
class Element {
  constructor(tag, attrs = {}, children = []) {
    this.tagName = tag.toUpperCase();
    this.attrs = { ...attrs };
    this.children = [];
    this.parentElement = null;
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = attrs.value || '';
    this.textContent = attrs.text || '';
    this.clicks = 0;
    for (const child of children) this.append(child);
  }
  append(child) { child.parentElement = this; this.children.push(child); return child; }
  remove() {
    this.parentElement.children = this.parentElement.children.filter(child => child !== this);
    this.parentElement = null;
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  get id() { return this.attrs.id || ''; }
  get name() { return this.attrs.name || ''; }
  get type() { return this.attrs.type || ''; }
  get isConnected() { return this.tagName === '#DOCUMENT' || Boolean(this.parentElement?.isConnected); }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  getClientRects() {
    for (let node = this; node; node = node.parentElement) if (node.hidden) return [];
    return [{}];
  }
  matches(selector) {
    return selector.split(',').some(part => {
      part = part.trim();
      const tag = part.match(/^[a-zA-Z][\w-]*/)?.[0];
      if (tag && tag.toUpperCase() !== this.tagName) return false;
      for (const match of part.matchAll(/\[([^\s=\]]+)(?:\s*=\s*["']?([^\]"']*)["']?)?\]/g)) {
        if (match[2] === undefined ? this.getAttribute(match[1]) === null :
          this.getAttribute(match[1]) !== match[2]) return false;
      }
      return Boolean(tag || part.startsWith('['));
    });
  }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [
      ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)
    ]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node;
    return null;
  }
  click() { this.clicks++; this.onClick?.(); }
}

function fixture({ kind = 'department', groups = [['Home', 'Furniture', 'Decor'], ['Pet', 'Kitchen & Dining']],
  checked = 'Home', renamed = false, unrelated = true, delayed = false } = {}) {
  const document = new Element('#document');
  const root = document.append(new Element('section', { 'aria-label': 'Sort and Filter section' }));
  const wrap = root.append(new Element('div', renamed ? {} :
    { 'data-testid': kind === 'brand' ? 'filter_chip_brand' : 'filter_chip_cat_id' }));
  const facetName = kind === 'brand' ? 'Brand' : 'Departments';
  const button = wrap.append(new Element('button', {
    'aria-label': `Filter by ${renamed ? 'Something new' : facetName} not applied, activate to change`,
    'aria-expanded': 'false', text: facetName
  }));
  // Walmart renders the dropdown elsewhere inside the filter section, without
  // an aria-controls link from the chip's button to its panel.
  const panel = root.append(new Element('div', { id: 'observed-facet-panel' }));
  panel.hidden = true;
  if (kind === 'brand') {
    panel.setAttribute('data-dca-ctx', 'filterPopover');
    panel.append(new Element('input', { type: 'search', 'aria-label': 'Find a brand' }));
  }
  const radioGroup = panel.append(new Element('div', kind === 'brand' ?
    { role: 'group' } : { role: 'radiogroup', 'aria-label': facetName }));
  const radios = [];
  let group = 0;
  let pending = null;
  const addOptions = values => values.forEach(value => {
    const owner = kind === 'brand' ? radioGroup.append(new Element('div',
      { 'data-facet-option': `brand--${value}` })).append(new Element('label')) : radioGroup;
    const radio = owner.append(new Element('input', { type: kind === 'brand' ? 'checkbox' : 'radio',
      name: kind === 'brand' ? value : value === checked ? 'All departments' : facetName, value,
      'aria-label': `${value}, changing this filter will reload the page` }));
    radio.checked = value === checked;
    radio.onClick = () => { radio.checked = true; };
    radios.push(radio);
  });
  addOptions(groups[0]);
  const more = groups.length > 1 ? panel.append(new Element('button', { text: 'Show More' })) : null;
  more && (more.onClick = () => {
    const apply = () => { addOptions(groups[++group]); if (group === groups.length - 1) more.remove(); };
    if (delayed) pending = apply; else apply();
  });
  button.onClick = () => {
    panel.hidden = !panel.hidden;
    button.setAttribute('aria-expanded', String(!panel.hidden));
  };
  let outsideRadio;
  if (unrelated) {
    const header = document.append(new Element('header'));
    const outside = header.append(new Element('button', { text: 'Departments',
      'aria-label': 'Departments', 'aria-expanded': 'false' }));
    outsideRadio = header.append(new Element('input', { type: 'radio', name: 'Departments', value: 'Wrong menu' }));
    outside.onClick = () => { throw Error('Header department menu must never be opened'); };
  }
  document.defaultView = { getComputedStyle: () => ({ display: 'block', visibility: 'visible' }) };
  const pause = async () => { if (pending) { const action = pending; pending = null; action(); } };
  return { document, root, wrap, button, panel, more, radios, outsideRadio, pause };
}

let helper;
try {
  const context = vm.createContext({ URL, setTimeout, clearTimeout });
  vm.runInContext(readFileSync(new URL('../extension/facet-controls.js', import.meta.url), 'utf8'), context);
  helper = context.ReviewerFacetControls;
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}

const discover = (page, extra = {}) => helper.discover({ document: page.document,
  currentUrl: 'https://www.walmart.com/reviews/claim-product?sort=best&page=7#results',
  canAct: () => true, pause: page.pause, ...extra });
const select = (page, selection, extra = {}) => helper.select({ document: page.document,
  selection, canAct: () => true, pause: page.pause, ...extra });
const labels = result => Array.from(result, item => item.label);

// Captured Walmart hierarchy: the selected category has its own radio group
// and name, while the child group and Show More are siblings beside it.
function appliedDepartmentFixture(groups) {
  const page = fixture({ groups, checked: 'Home' });
  page.panel.setAttribute('data-dca-ctx', 'filterPopover');
  const children = page.radios[0].parentElement;
  children.setAttribute('aria-label', 'Home');
  children.setAttribute('data-testid', 'cat_id_radiogroup');
  const selected = page.radios[0];
  selected.remove();
  selected.setAttribute('name', 'Home');
  page.panel.append(new Element('div', { role: 'radiogroup', 'aria-label': 'Departments' })).append(selected);
  page.button.setAttribute('aria-label', 'Filter by Home applied, activate to change , filter applied');
  return page;
}

test('an applied department header cannot hide the sibling Show More from child discovery', async () => {
  const page = appliedDepartmentFixture([['Home', 'Kitchen & Dining', 'Decor', 'Furniture', 'Appliances'],
    ['Holiday Decor', 'Bedding', 'Bath']]);
  assert.deepEqual(labels(await discover(page)),
    ['Kitchen & Dining', 'Decor', 'Furniture', 'Appliances', 'Holiday Decor', 'Bedding', 'Bath']);
  assert.equal(page.more.clicks, 1);
  assert.equal(page.radios.some(input => input.clicks > 0), false);
});

test('a leaf department with only its named selected radio is an empty child list, not a missing panel', async () => {
  const page = appliedDepartmentFixture([['Home']]);
  assert.deepEqual(labels(await discover(page)), []);
  assert.equal(page.panel.hidden, true);
  assert.equal(page.radios[0].clicks, 0);
});

test('selecting an additional child of an applied department expands its sibling control', async () => {
  const page = appliedDepartmentFixture([['Home', 'Furniture'], ['Bedding']]);
  const result = await select(page, { kind: 'department', label: 'Bedding' });
  assert.equal(result.ok, true);
  assert.equal(page.more.clicks, 1);
  assert.equal(page.radios.find(input => input.value === 'Bedding').clicks, 1);
  assert.equal(page.radios[0].clicks, 0);
});

test('selected category names are recognized only in the rendered department header group', async () => {
  const page = appliedDepartmentFixture([['Home', 'Furniture'], ['Bedding']]);
  const header = page.radios[0].parentElement;
  header.setAttribute('aria-label', 'Unrelated choices');
  assert.deepEqual(labels(await discover(page)), ['Furniture']);
  assert.equal(page.more.clicks, 0, 'Do not expand through a different radio group');
});

test('discovery expands every Show More batch, omits current root and closes its menu', async () => {
  const page = fixture({ groups: [['Home', 'Furniture'], ['Decor'], ['Pet', 'Appliances']], delayed: true });
  const result = await discover(page);
  assert.deepEqual(labels(result), ['Furniture', 'Decor', 'Pet', 'Appliances']);
  assert.ok(result.every(item => item.kind === 'department' &&
    item.parentUrl === 'https://www.walmart.com/reviews/claim-product?sort=best'));
  assert.equal(page.panel.hidden, true);
  assert.equal(page.radios.every(radio => radio.clicks === 0), true);
  assert.equal(page.outsideRadio.clicks, 0);
});

test('Show More becoming Show Less cannot finish discovery before delayed options render', async () => {
  const page = fixture({ groups: [['Home', 'Furniture'], ['Pet', 'Appliances']] });
  const expand = page.more.onClick;
  let pending = false;
  let waits = 0;
  page.more.onClick = () => { page.more.textContent = 'Show Less'; pending = true; };
  const result = await discover(page, { pause: async () => {
    if (pending && ++waits === 3) { pending = false; expand(); }
  } });
  assert.deepEqual(labels(result), ['Furniture', 'Pet', 'Appliances']);
  assert.equal(page.more.clicks, 1);
});

test('a disappearing Show More control without new options reports an incomplete expansion', async () => {
  const page = fixture();
  page.more.onClick = () => page.more.remove();
  await assert.rejects(discover(page), error => error.code === 'expansion-stalled');
});

test('discovery keeps an already-open menu open and deduplicates exact labels', async () => {
  const page = fixture({ groups: [['Home', 'Furniture', 'Furniture', 'All departments', '  ']] });
  page.button.click();
  const result = await discover(page);
  assert.deepEqual(labels(result), ['Furniture']);
  assert.equal(page.panel.hidden, false);
});

test('discovery waits for an already-expanded department chip to mount its own panel', async () => {
  const page = fixture({ groups: [['Home', 'Furniture'], ['Pet']] });
  page.panel.remove();
  page.button.setAttribute('aria-expanded', 'true');
  let waits = 0;
  const result = await discover(page, { pause: async () => {
    if (++waits === 3) {
      page.panel.hidden = false;
      page.root.append(page.panel);
    }
  } });
  assert.deepEqual(labels(result), ['Furniture', 'Pet']);
  assert.equal(page.button.clicks, 0, 'an opening chip must not be toggled closed');
  assert.equal(page.panel.hidden, false, 'the pre-existing menu remains open');
  assert.equal(page.outsideRadio.clicks, 0);
});

test('selection waits for an already-expanded chip before choosing its exact department', async () => {
  const page = fixture({ groups: [['Home', 'Furniture']] });
  page.panel.remove();
  page.button.setAttribute('aria-expanded', 'true');
  let waits = 0;
  const result = await select(page, { kind: 'department', label: 'Furniture' }, {
    pause: async () => {
      if (++waits === 3) {
        page.panel.hidden = false;
        page.root.append(page.panel);
      }
    }
  });
  assert.equal(result.ok, true);
  assert.equal(page.radios.find(radio => radio.value === 'Furniture').checked, true);
  assert.equal(page.radios.reduce((sum, radio) => sum + radio.clicks, 0), 1);
  assert.equal(page.button.clicks, 0);
  assert.equal(page.outsideRadio.clicks, 0);
});

test('a slowly mounting department panel is opened once and receives a bounded readiness wait', async () => {
  const page = fixture({ groups: [['Home', 'Furniture']] });
  page.panel.remove();
  let opening = false;
  let waited = 0;
  page.button.onClick = () => {
    const expanded = page.button.getAttribute('aria-expanded') === 'true';
    page.button.setAttribute('aria-expanded', String(!expanded));
    opening = !expanded;
    if (expanded) page.panel.hidden = true;
  };
  const result = await discover(page, { pause: async milliseconds => {
    if (opening && (waited += milliseconds) >= 2160) {
      page.panel.hidden = false;
      page.root.append(page.panel);
      opening = false;
    }
  } });
  assert.deepEqual(labels(result), ['Furniture']);
  assert.equal(page.button.clicks, 2, 'one opening click and one owned cleanup click');
  assert.equal(page.panel.hidden, true);
  assert.ok(waited >= 2160 && waited < 8000);
});

test('an expanded chip with no panel times out without adopting the header department menu', async () => {
  const page = fixture();
  page.panel.remove();
  page.button.setAttribute('aria-expanded', 'true');
  let waited = 0;
  await assert.rejects(discover(page, { pause: async milliseconds => { waited += milliseconds; } }),
    error => error.code === 'missing-panel');
  assert.ok(waited > 0 && waited < 8000, 'panel readiness is bounded and actually awaited');
  assert.equal(page.button.clicks, 0);
  assert.equal(page.outsideRadio.clicks, 0);
});

test('cancellation while an expanded chip is awaiting its panel prevents all cleanup clicks', async () => {
  const page = fixture();
  page.panel.remove();
  page.button.setAttribute('aria-expanded', 'true');
  let active = true;
  await assert.rejects(discover(page, {
    canAct: async () => active,
    pause: async () => { active = false; }
  }), error => error.code === 'cancelled');
  assert.equal(page.button.clicks, 0);
  assert.equal(page.more.clicks, 0);
  assert.equal(page.outsideRadio.clicks, 0);
});

test('discovery and selection reacquire a replaced filter section during opening', async () => {
  for (const mode of ['discover', 'select']) {
    const page = fixture({ groups: [['Home', 'Furniture']] });
    const replacement = fixture({ groups: [['Home', 'Furniture']] });
    let replaced = false;
    const options = { pause: async () => {
      if (replaced) return;
      replaced = true;
      page.root.remove();
      replacement.root.remove();
      replacement.panel.hidden = false;
      replacement.button.setAttribute('aria-expanded', 'true');
      page.document.append(replacement.root);
    } };
    const result = mode === 'discover' ? await discover(page, options) :
      await select(page, { kind: 'department', label: 'Furniture' }, options);
    if (mode === 'discover') assert.deepEqual(labels(result), ['Furniture']);
    else assert.equal(result.ok, true);
    assert.equal(page.button.clicks, 1, mode);
    assert.equal(replacement.button.clicks, 0, 'replacement chip is never owned for cleanup');
    assert.equal(replacement.panel.hidden, false);
    assert.equal(replacement.radios.find(input => input.value === 'Furniture').clicks,
      mode === 'select' ? 1 : 0);
  }
});

test('discovery and selection reacquire a replaced section after Show More', async () => {
  for (const mode of ['discover', 'select']) {
    const page = fixture({ groups: [['Home', 'Furniture'], ['Pet']] });
    const replacement = fixture({ groups: [['Home', 'Furniture', 'Pet']] });
    page.more.onClick = () => {
      page.root.remove();
      replacement.root.remove();
      replacement.panel.hidden = false;
      replacement.button.setAttribute('aria-expanded', 'true');
      page.document.append(replacement.root);
    };
    const result = mode === 'discover' ? await discover(page) :
      await select(page, { kind: 'department', label: 'Pet' });
    if (mode === 'discover') assert.deepEqual(labels(result), ['Furniture', 'Pet']);
    else assert.equal(result.ok, true);
    assert.equal(page.more.clicks, 1);
    assert.equal(replacement.button.clicks, 0);
    assert.equal(replacement.radios.find(input => input.value === 'Pet').clicks,
      mode === 'select' ? 1 : 0);
  }
});

test('replacing only the chip never transfers cleanup ownership to its new button', async () => {
  const page = fixture({ groups: [['Home', 'Furniture']] });
  const replacement = fixture({ groups: [['Home', 'Furniture']] });
  let replaced = false;
  const result = await discover(page, { pause: async () => {
    if (replaced) return;
    replaced = true;
    page.button.remove();
    replacement.button.remove();
    replacement.button.setAttribute('aria-expanded', 'true');
    page.wrap.append(replacement.button);
  } });
  assert.deepEqual(labels(result), ['Furniture']);
  assert.equal(replacement.button.clicks, 0);
  assert.equal(page.panel.hidden, false);
});

test('a replaced root cannot widen discovery into ambiguous filter sections', async () => {
  const page = fixture();
  const replacements = [fixture(), fixture()];
  await assert.rejects(discover(page, { pause: async () => {
    if (!page.root.isConnected) return;
    page.root.remove();
    for (const replacement of replacements) {
      replacement.root.remove();
      replacement.panel.hidden = false;
      page.document.append(replacement.root);
    }
  } }), error => error.code === 'ambiguous-filter-section');
  assert.equal(replacements.reduce((count, value) => count + value.button.clicks, 0), 0);
});

test('cancellation during root replacement prevents reacquisition and all later clicks', async () => {
  const page = fixture();
  const replacement = fixture();
  let active = true;
  await assert.rejects(discover(page, { canAct: () => active, pause: async () => {
    active = false;
    page.root.remove();
    replacement.root.remove();
    replacement.panel.hidden = false;
    page.document.append(replacement.root);
  } }), error => error.code === 'cancelled');
  assert.equal(page.button.clicks, 1);
  assert.equal(replacement.button.clicks, 0);
  assert.equal(replacement.more.clicks, 0);
  assert.equal(replacement.radios.reduce((sum, input) => sum + input.clicks, 0), 0);
});

test('an applied category chip still discovers its children when the button says Home', async () => {
  const page = fixture({ groups: [['Home', 'Kitchen & Dining', 'Furniture']] });
  page.button.setAttribute('aria-label', 'Filter by Home applied, activate to change , filter applied');
  page.button.textContent = 'Home';
  assert.deepEqual(labels(await discover(page)), ['Kitchen & Dining', 'Furniture']);
});

test('candidate labels preserve exact rendered input values', async () => {
  const page = fixture({ groups: [['Home', ' Furniture ', '  ']] });
  assert.deepEqual(labels(await discover(page)), [' Furniture ']);
  assert.equal((await select(page, { kind: 'department', label: ' Furniture ' })).ok, true);
});

test('missing and renamed controls fail explicitly without opening another department menu', async () => {
  const renamed = fixture({ renamed: true });
  await assert.rejects(discover(renamed), error => error.code === 'missing-control');
  assert.equal(renamed.button.clicks, 0);
  const missing = fixture();
  missing.root.remove();
  await assert.rejects(discover(missing), error => error.code === 'missing-filter-section');
});

test('the exact accessible name remains usable if the chip wrapper disappears', async () => {
  const page = fixture();
  delete page.wrap.attrs['data-testid'];
  assert.deepEqual(labels(await discover(page)), ['Furniture', 'Decor', 'Pet', 'Kitchen & Dining']);
});

test('two matching facet controls fail as ambiguous without opening either', async () => {
  const page = fixture();
  page.root.append(new Element('button', {
    'aria-label': 'Filter by Departments not applied, activate to change', text: 'Departments'
  }));
  delete page.wrap.attrs['data-testid'];
  await assert.rejects(discover(page), error => error.code === 'ambiguous-control');
  assert.equal(page.button.clicks, 0);
});

test('selection expands hidden options and clicks only the exact matching input', async () => {
  const page = fixture();
  const result = await select(page, { kind: 'department', label: 'Kitchen & Dining' });
  assert.equal(result.ok, true);
  assert.equal(page.radios.find(radio => radio.value === 'Kitchen & Dining').checked, true);
  assert.equal(page.radios.reduce((sum, radio) => sum + radio.clicks, 0), 1);
  assert.equal(page.outsideRadio.clicks, 0);
});

test('department selection activates the associated label when input clicks do not apply the filter', async () => {
  const page = fixture({ groups: [['Home', 'Furniture']] });
  const input = page.radios.find(radio => radio.value === 'Furniture');
  const group = input.parentElement;
  input.remove();
  input.setAttribute('id', 'furniture-option');
  const label = group.append(new Element('label', { for: input.id }));
  label.append(input);
  let selected = '';
  // The rendered label owns selection, while direct input activation is a no-op.
  input.onClick = () => {};
  label.onClick = () => { input.checked = true; selected = input.value; };
  const result = await select(page, { kind: 'department', label: 'Furniture' });
  assert.equal(result.ok, true);
  assert.equal(selected, 'Furniture', 'A successful dispatch must reach the label selection handler');
  assert.equal(input.checked, true);
  assert.equal(label.clicks, 1);
  assert.equal(input.clicks, 0);
});

test('a department label associated with another input is never activated', async () => {
  const page = fixture({ groups: [['Home', 'Furniture']] });
  const input = page.radios.find(radio => radio.value === 'Furniture');
  const group = input.parentElement;
  input.remove();
  input.setAttribute('id', 'furniture-option');
  const label = group.append(new Element('label', { for: 'another-option' }));
  label.append(input);
  const result = await select(page, { kind: 'department', label: 'Furniture' });
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'ambiguous-selection');
  assert.equal(label.clicks, 0);
  assert.equal(input.clicks, 0);
});

test('missing or ambiguous selections fail without selecting any input', async () => {
  for (const groups of [[['Home', 'Furniture']], [['Home', 'Furniture', 'Furniture']]]) {
    const page = fixture({ groups });
    const result = await select(page, { kind: 'department', label: groups[0].length === 2 ? 'Missing' : 'Furniture' });
    assert.equal(result.ok, false);
    assert.match(result.reason, /missing-selection|ambiguous-selection/);
    assert.equal(page.radios.reduce((sum, radio) => sum + radio.clicks, 0), 0);
    assert.equal(page.panel.hidden, true);
  }
});

test('the native activation callback receives only the matched department label and failures stay unselected', async () => {
  for (const ok of [true, false]) {
    const page = fixture({ groups: [['Home', 'Furniture']] });
    const input = page.radios.find(radio => radio.value === 'Furniture');
    const group = input.parentElement;
    input.remove();
    const owner = group.append(new Element('label'));
    owner.append(input);
    let activations = 0;
    const result = await select(page, { kind: 'department', label: 'Furniture' }, {
      activate: async label => { assert.equal(label, owner); activations++; return { ok }; }
    });
    assert.equal(result.ok, ok);
    assert.equal(activations, 1);
    assert.equal(owner.clicks, 0);
    assert.equal(input.clicks, 0);
  }
});

test('brand discovery and selection use the Brand control and checkbox options', async () => {
  const page = fixture({ kind: 'brand', checked: '', groups: [['Acme', 'Widget Co.'], ['Brand #3']] });
  assert.deepEqual(labels(await discover(page, { kind: 'brand' })), ['Acme', 'Widget Co.', 'Brand #3']);
  assert.equal((await select(page, { kind: 'brand', label: 'Widget Co.' })).ok, true);
  assert.equal(page.radios.find(radio => radio.value === 'Widget Co.').checked, true);
});

test('applied Brand count accessible name locates the real brand checkbox popover', async () => {
  const page = fixture({ kind: 'brand', checked: 'NIDA', groups: [['NIDA', 'Acme']] });
  delete page.wrap.attrs['data-testid'];
  page.button.setAttribute('aria-label', 'Filter by Brand (1) applied, activate to change , filter applied');
  page.button.textContent = 'Brand (1)';
  assert.deepEqual(labels(await discover(page, { kind: 'brand' })), ['Acme']);
  assert.equal((await select(page, { kind: 'brand', label: 'Acme' })).ok, true);
});

test('brand options require the observed brand facet wrapper, not arbitrary checkboxes', async () => {
  const page = fixture({ kind: 'brand', checked: '', groups: [['NIDA', 'Acme']] });
  const arbitrary = page.panel.append(new Element('input', {
    type: 'checkbox', name: 'Brand', value: 'Wrong checkbox'
  }));
  assert.deepEqual(labels(await discover(page, { kind: 'brand' })), ['NIDA', 'Acme']);
  const result = await select(page, { kind: 'brand', label: 'Wrong checkbox' });
  assert.equal(result.reason, 'missing-selection');
  assert.equal(arbitrary.clicks, 0);
});

test('cancellation before interaction never opens a menu', async () => {
  const page = fixture();
  await assert.rejects(discover(page, { canAct: () => false }), error => error.code === 'cancelled');
  assert.equal((await select(page, { kind: 'department', label: 'Furniture' },
    { canAct: () => false })).reason, 'cancelled');
  assert.equal(page.button.clicks, 0);
});

test('cancellation while awaiting menu render prevents subsequent clicks', async () => {
  const page = fixture();
  let active = true;
  await assert.rejects(discover(page, { canAct: () => active, pause: async () => { active = false; } }),
    error => error.code === 'cancelled');
  assert.equal(page.button.clicks, 1);
  assert.equal(page.more.clicks, 0);
});

test('async cancellation guards are honored immediately before choosing a facet', async () => {
  const page = fixture({ groups: [['Home', 'Furniture']] });
  let active = true;
  const result = await select(page, { kind: 'department', label: 'Furniture' }, {
    canAct: async () => active,
    pause: async () => { active = false; }
  });
  assert.equal(result.reason, 'cancelled');
  assert.equal(page.radios.reduce((sum, radio) => sum + radio.clicks, 0), 0);
});

test('a Show More that never changes the options fails rather than silently truncating', async () => {
  const page = fixture();
  page.more.onClick = () => {};
  await assert.rejects(discover(page), error => error.code === 'expansion-stalled');
  assert.equal(page.more.clicks, 1);
  assert.equal(page.panel.hidden, true);
});

test('option count safety cap is reported rather than returning partial results', async () => {
  const page = fixture({ groups: [Array.from({ length: 2002 }, (_, index) => `Department ${index}`)] });
  await assert.rejects(discover(page), error => error.code === 'option-overflow');
  assert.equal(page.panel.hidden, true);
});

test('excessive expanding batches report incomplete coverage', async () => {
  const page = fixture({ groups: Array.from({ length: 27 }, (_, index) => [`Department ${index}`]) });
  await assert.rejects(discover(page), error => error.code === 'expansion-overflow');
  assert.equal(page.panel.hidden, true);
});
