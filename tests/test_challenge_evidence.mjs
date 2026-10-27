import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const evidenceSource = fs.readFileSync(
  new URL('../extension/challenge-evidence.js', import.meta.url), 'utf8'
);
const diagnosticsSource = fs.readFileSync(
  new URL('../extension/diagnostics.js', import.meta.url), 'utf8'
);
const CHALLENGE_URL = 'https://www.walmart.com/blocked?challenge=private-query';
const REVIEW_URL = 'https://www.walmart.com/reviews/claim-product';
const JPEG = 'data:image/jpeg;base64,/9j/AAAA';

function storageArea(initial = {}) {
  const data = { ...initial };
  return {
    data,
    async get(keys) {
      return Object.fromEntries(keys.filter(key => key in data).map(key => [key, data[key]]));
    },
    async set(value) { Object.assign(data, value); }
  };
}

function loadEvidence({ local = null, session = null, url = CHALLENGE_URL } = {}) {
  local ||= storageArea({
    botCheck: { pausedUntil: Date.now() + 60_000, lastAt: Date.now() - 2000,
      challengedTabIds: [11], count: 1 },
    token: 'private-auth-token', endpoint: 'http://private-endpoint/ingest',
    reviewerDiagnostics: [{ event: 'challenge', at: Date.now(), tabId: 11,
      token: 'private-auth-token', url: 'https://private-url', title: 'private-product-title' }]
  });
  session ||= storageArea();
  const state = {
    tab: { id: 11, windowId: 4, active: true, status: 'complete', url },
    activeId: 11, probe: { ready: true, botCheck: true },
    captureCalls: [], downloads: [], captureImpl: null, downloadImpl: null,
    probeImpl: null, queryImpl: null
  };
  const chrome = {
    storage: { local, session },
    runtime: { getManifest: () => ({ version: 'test' }) },
    tabs: {
      async query(info) {
        assert.deepEqual({ ...info }, { active: true, currentWindow: true });
        if (state.queryImpl) return state.queryImpl();
        return state.activeId === state.tab.id ? [{ ...state.tab }] :
          [{ id: state.activeId, windowId: 4, active: true, url: 'https://unrelated.example' }];
      },
      async get(id) { return id === state.tab.id ? { ...state.tab } : null; },
      async captureVisibleTab(windowId, options) {
        state.captureCalls.push({ windowId, options: { ...options } });
        return state.captureImpl ? state.captureImpl() : JPEG;
      }
    },
    downloads: {
      async download(options) {
        if (state.downloadImpl) await state.downloadImpl(options);
        state.downloads.push({ ...options });
        return state.downloads.length;
      }
    }
  };
  const context = vm.createContext({
    chrome, URL, console,
    readBotCheck: async () => ({ ...local.data.botCheck }),
    pathRegex: async () => /^\/reviews\/claim-product/,
    eligibleUrl(raw, re) {
      const parsed = new URL(raw);
      return parsed.protocol === 'https:' && parsed.hostname === 'www.walmart.com' &&
        re.test(parsed.pathname);
    },
    probeOnce: async id => {
      assert.equal(id, 11);
      return state.probeImpl ? state.probeImpl() : state.probe;
    },
    config: async () => ({ ...local.data, refreshMinutes: 10, pageDelaySeconds: 15, sweepMode: 'none' }),
    normalizeSweepMode: value => value
  });
  vm.runInContext(`${diagnosticsSource}\n${evidenceSource}`, context);
  return { capture: context.captureVerificationEvidence, local, session, state };
}

test('an unrelated active tab is never captured or downloaded', async () => {
  const helper = loadEvidence();
  helper.state.activeId = 99;
  const result = await helper.capture(11);
  assert.equal(result.ok, false);
  assert.equal(helper.state.captureCalls.length, 0);
  assert.equal(helper.state.downloads.length, 0);
});

test('an unresolved visible challenge saves one JPEG and safe report per outage', async () => {
  const helper = loadEvidence();
  const first = await helper.capture(11);
  assert.equal(first.ok, true);
  assert.equal(first.captured, true);
  assert.equal(helper.state.captureCalls.length, 1);
  assert.equal(helper.state.captureCalls[0].windowId, 4);
  assert.equal(helper.state.captureCalls[0].options.format, 'jpeg');
  const [image, report] = helper.state.downloads;
  assert.match(image.filename, /^ReviewerItemRelay\/verification-\d+\.jpg$/);
  assert.equal(image.url, JPEG);
  assert.equal(image.saveAs, false);
  assert.equal(image.conflictAction, 'uniquify');
  assert.equal(report.filename, image.filename.replace(/\.jpg$/, '.json'));
  assert.equal(helper.local.data.lastVerificationCapture.status, 'saved');
  assert.equal(helper.local.data.lastVerificationCaptureError, '');
  const decoded = decodeURIComponent(report.url.split(',')[1]);
  const diagnosticReport = JSON.parse(decoded);
  assert.equal(diagnosticReport.schemaVersion, 1);
  for (const secret of ['private-auth-token', 'private-endpoint', 'private-url',
    'private-product-title', 'private-query', 'data:image']) {
    assert.equal(decoded.includes(secret), false);
    assert.equal(JSON.stringify(helper.local.data.lastVerificationCapture).includes(secret), false);
  }
  assert.deepEqual(JSON.parse(JSON.stringify(await helper.capture(11))), { ok: true, captured: false });
  assert.equal(helper.state.captureCalls.length, 1);
  const recreated = loadEvidence({ local: helper.local, session: helper.session });
  assert.equal((await recreated.capture(11)).captured, false);
  assert.equal(recreated.state.captureCalls.length, 0);
});

test('an expired but outstanding pause remains eligible; zero pause does not', async () => {
  const helper = loadEvidence();
  helper.local.data.botCheck.pausedUntil = Date.now() - 60_000;
  assert.equal((await helper.capture(11)).captured, true);
  const resumed = loadEvidence();
  resumed.local.data.botCheck.pausedUntil = 0;
  assert.equal((await resumed.capture(11)).ok, false);
  assert.equal(resumed.state.captureCalls.length, 0);
});

test('reviewer-path captures require a current positive verification probe', async () => {
  const helper = loadEvidence({ url: REVIEW_URL });
  helper.state.probe = { ready: true, reviewerReady: true, botCheck: false };
  assert.equal((await helper.capture(11)).ok, false);
  assert.equal(helper.state.captureCalls.length, 0);
  helper.state.probe = { ready: true, botCheck: true };
  assert.equal((await helper.capture(11)).captured, true);
});

for (const [description, mutate] of [
  ['navigation changes', helper => { helper.state.tab.url = REVIEW_URL; }],
  ['the active tab changes', helper => { helper.state.activeId = 99; }],
  ['the verification is solved', helper => { helper.local.data.botCheck.pausedUntil = 0; }],
  ['another outage replaces it', helper => { helper.local.data.botCheck.lastAt += 1; }]
]) {
  test(`a screenshot is discarded when ${description} during capture`, async () => {
    const helper = loadEvidence();
    helper.state.captureImpl = () => { mutate(helper); return JPEG; };
    const result = await helper.capture(11);
    assert.equal(result.ok, false);
    assert.equal(helper.state.downloads.length, 0);
    assert.equal(JSON.stringify(result).includes(JPEG), false);
  });
}

test('a reviewer verification solved without navigation discards the image', async () => {
  const helper = loadEvidence({ url: REVIEW_URL });
  helper.state.captureImpl = () => {
    helper.state.probe = { ready: true, botCheck: false, reviewerReady: true };
    return JPEG;
  };
  assert.equal((await helper.capture(11)).ok, false);
  assert.equal(helper.state.downloads.length, 0);
});

test('a pause cleared while the probe is running never captures', async () => {
  const helper = loadEvidence({ url: REVIEW_URL });
  helper.state.probeImpl = () => {
    helper.local.data.botCheck.pausedUntil = 0;
    return { ready: true, botCheck: true };
  };
  assert.equal((await helper.capture(11)).ok, false);
  assert.equal(helper.state.captureCalls.length, 0);
});

test('permission failure has a fixed error and retries on a later toolbar invocation', async () => {
  const helper = loadEvidence();
  helper.state.captureImpl = () => { throw new Error('private-auth-token <all_urls> permission'); };
  const failure = await helper.capture(11);
  assert.equal(failure.ok, false);
  assert.match(failure.error, /click the extension icon/i);
  assert.equal(failure.error.includes('private-auth-token'), false);
  assert.equal(helper.local.data.lastVerificationCapture.status, 'unavailable');
  helper.state.captureImpl = null;
  assert.equal((await helper.capture(11)).captured, true);
  assert.equal(helper.state.captureCalls.length, 2);
});

test('concurrent calls coalesce rather than capturing twice', async () => {
  const helper = loadEvidence();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  helper.state.captureImpl = () => gate;
  const first = helper.capture(11);
  const second = helper.capture(11);
  release(JPEG);
  const results = await Promise.all([first, second]);
  assert.ok(results.every(result => result.captured));
  assert.equal(helper.state.captureCalls.length, 1);
  assert.equal(helper.state.downloads.length, 2);
});

test('unrecorded tabs and non-HTTPS or unrelated URLs are never captured', async () => {
  for (const url of ['http://www.walmart.com/blocked', 'https://walmart.com/blocked',
    'https://www.walmart.com/orders', 'https://elsewhere.example/blocked']) {
    const helper = loadEvidence({ url });
    assert.equal((await helper.capture(11)).ok, false);
    assert.equal(helper.state.captureCalls.length, 0);
  }
  const helper = loadEvidence();
  helper.local.data.botCheck.challengedTabIds = [22];
  assert.equal((await helper.capture(11)).ok, false);
  assert.equal(helper.state.captureCalls.length, 0);
});

test('non-JPEG or oversized capture results are never downloaded', async () => {
  for (const dataUrl of ['data:image/png;base64,AAAA', 'data:image/jpeg;base64,invalid%payload',
    'data:image/jpeg;base64,' + 'A'.repeat(3 * 1024 * 1024 + 1)]) {
    const helper = loadEvidence();
    helper.state.captureImpl = () => dataUrl;
    assert.equal((await helper.capture(11)).ok, false);
    assert.equal(helper.state.downloads.length, 0);
  }
});

test('a failed download remains retryable and never leaks raw failure details', async () => {
  const helper = loadEvidence();
  helper.state.downloadImpl = () => { throw new Error('private-auth-token download error'); };
  const result = await helper.capture(11);
  assert.equal(result.ok, false);
  assert.equal(JSON.stringify(result).includes('private-auth-token'), false);
  helper.state.downloadImpl = null;
  assert.equal((await helper.capture(11)).captured, true);
});

test('a report download failure preserves and deduplicates the successful screenshot', async () => {
  const helper = loadEvidence();
  helper.state.downloadImpl = options => {
    if (options.filename.endsWith('.json')) throw new Error('private-auth-token');
  };
  assert.equal((await helper.capture(11)).captured, true);
  assert.equal(helper.state.downloads.length, 1);
  assert.equal(helper.local.data.lastVerificationCapture.status, 'saved');
  assert.match(helper.local.data.lastVerificationCaptureError, /diagnostic report/);
  assert.equal(helper.local.data.lastVerificationCaptureError.includes('private-auth-token'), false);
  assert.equal((await helper.capture(11)).captured, false);
  assert.equal(helper.state.captureCalls.length, 1);
});
