import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const context = vm.createContext({});
vm.runInContext(readFileSync(new URL('../extension/scanner-settings.js', import.meta.url), 'utf8'), context);
const settings = context.ReviewerScannerSettings;

test('pure scanner defaults preserve installed behavior without importing secrets', () => {
  const defaults = settings.project({ token: 'private', endpoint: 'http://localhost:8787/ingest' });
  assert.equal(defaults.auxiliaryDepartments, true);
  assert.equal(defaults.auxiliaryKeywords, true);
  assert.equal(defaults.auxiliaryBrands, false);
  assert.equal(defaults.verificationAssistEnabled, false);
  assert.equal(defaults.auxiliaryKeywordQueries, null);
  assert.equal('token' in defaults, false);
  assert.equal('endpoint' in defaults, false);
  assert.equal(settings.relay.defaults.refreshMinutes, 30);
  assert.equal(settings.relay.defaults.pageDelaySeconds, 15);
});

test('explicit false and empty queries survive projection and unchanged fields produce no edits', () => {
  const baseline = settings.project({ auxiliaryDepartments: false, auxiliaryKeywordQueries: [] });
  assert.equal(baseline.auxiliaryDepartments, false);
  assert.equal(baseline.auxiliaryKeywordQueries.length, 0);
  assert.deepEqual(Object.keys(settings.changes(baseline, baseline)), []);
  const edits = settings.changes({ ...baseline, rotateScannerTabs: false }, baseline);
  assert.deepEqual(Object.keys(edits), ['rotateScannerTabs']);
});

test('canonical schemas reject secret fields, broken routes and malformed timing', () => {
  assert.throws(() => settings.validate({ ...settings.defaults, token: 'private' }));
  assert.throws(() => settings.project({ pathPattern: '[' }));
  assert.throws(() => settings.project({ auxiliaryKeywordQueries: ['bad\nquery'] }));
  assert.throws(() => settings.relay.validate({ ...settings.relay.defaults, refreshMinutes: -1 }));
  assert.throws(() => settings.relay.validate({ ...settings.relay.defaults, endpoint: 'private' }));
});

test('priority settings are opt-in and legacy documents retain their exact shape', () => {
  const projected = settings.project({});
  assert.equal(projected.auxiliaryPriority, false);
  assert.equal(projected.auxiliaryPriorityQueries, null);
  assert.equal(projected.auxiliaryPriorityRefreshMinutes, 1);
  const legacy = { ...settings.defaults };
  for (const key of ['auxiliaryPriority', 'auxiliaryPriorityQueries', 'auxiliaryPriorityRefreshMinutes']) delete legacy[key];
  assert.deepEqual(JSON.parse(JSON.stringify(settings.validate(legacy))), legacy);
});

test('priority settings accept bounded custom queries and independent timing', () => {
  const result = settings.project({ auxiliaryPriority: true,
    auxiliaryPriorityQueries: [' Sofa ', 'Dining Table'], auxiliaryPriorityRefreshMinutes: 2 });
  assert.equal(result.auxiliaryPriority, true);
  assert.deepEqual(Array.from(result.auxiliaryPriorityQueries), ['Sofa', 'Dining Table']);
  assert.equal(result.auxiliaryPriorityRefreshMinutes, 2);
  assert.equal(settings.project({ auxiliaryPriorityQueries: [] }).auxiliaryPriorityQueries.length, 0);
});

test('priority boundary rejects invalid values without changing legacy fields', () => {
  const bad = [
    { auxiliaryPriority: 'yes' }, { auxiliaryPriorityRefreshMinutes: 0 },
    { auxiliaryPriorityRefreshMinutes: 121 }, { auxiliaryPriorityRefreshMinutes: 1.5 },
    { auxiliaryPriorityRefreshMinutes: true }, { auxiliaryPriorityQueries: 'Sofa' },
    { auxiliaryPriorityQueries: [''] }, { auxiliaryPriorityQueries: ['Sofa', ' sofa '] },
    { auxiliaryPriorityQueries: ['bad\nquery'] },
    { auxiliaryPriorityQueries: Array.from({ length: 25 }, (_, i) => `Term ${i}`) },
    { auxiliaryPriorityQueries: ['x'.repeat(81)] }
  ];
  for (const fields of bad) assert.throws(() => settings.project(fields), JSON.stringify(fields));
});

test('priority query Unicode trimming and identity match the backend contract', () => {
  assert.deepEqual(Array.from(settings.project({ auxiliaryPriorityQueries: ['\ufeff \ufeffSofa\ufeff \ufeff', 'Straße', 'STRASSE'] })
    .auxiliaryPriorityQueries), ['Sofa', 'Straße', 'STRASSE']);
  assert.throws(() => settings.project({ auxiliaryPriorityQueries: ['Sofa', '\ufeffSofa'] }));
  assert.throws(() => settings.project({ auxiliaryPriorityQueries: ['\u0085Sofa'] }));
  assert.equal(settings.project({ auxiliaryPriorityQueries: ['🪑'.repeat(80)] }).auxiliaryPriorityQueries.length, 1);
  assert.throws(() => settings.project({ auxiliaryPriorityQueries: ['🪑'.repeat(81)] }));
});

test('general keyword capacity expands without changing the default list', () => {
  assert.equal(settings.project({ auxiliaryKeywordQueries: Array.from({ length: 128 }, (_, i) => `Term ${i}`) })
    .auxiliaryKeywordQueries.length, 128);
  assert.throws(() => settings.project({ auxiliaryKeywordQueries: Array.from({ length: 129 }, (_, i) => `Term ${i}`) }));
  assert.equal(settings.project({}).auxiliaryKeywordQueries, null);
});
