// Scan settings and Discord delivery status; routing is managed by the backend.

const DEFAULT_PATH_PATTERN = '^/reviews/claim-product';
const DEFAULT_ENDPOINT = 'http://127.0.0.1:8787/ingest';
const OPTIONS_SCANNER_KEYS = ['auxiliaryDepartments', 'auxiliaryKeywords', 'auxiliaryBrands',
  'auxiliaryKeywordQueries', 'groupScannerTabs', 'rotateScannerTabs',
  'verificationAssistEnabled', 'verificationNativeEnabled', 'pathPattern'];
const $ = (id) => document.getElementById(id);

const endpointEl = $('endpoint');
const endpointErrorEl = $('endpointError');
const tokenEl = $('token');
const pathEl = $('pathPattern');
const pathErrorEl = $('pathError');
const refreshEl = $('refreshMinutes');
const pageDelayEl = $('pageDelaySeconds');
const sweepModeEl = $('sweepMode');
const auxiliaryDepartmentsEl = $('auxiliaryDepartments');
const auxiliaryKeywordsEl = $('auxiliaryKeywords');
const auxiliaryBrandsEl = $('auxiliaryBrands');
const auxiliaryQueriesEl = $('auxiliaryKeywordQueries');
const auxiliaryKeywordErrorEl = $('auxiliaryKeywordError');
const groupScannerTabsEl = $('groupScannerTabs');
const rotateScannerTabsEl = $('rotateScannerTabs');
const savedEl = $('saved');
const statusEl = $('status');

const checkConnectionEl = $('checkConnection');
const testNotificationEl = $('testNotification');
const testResultEl = $('testResult');

const verificationStateLabels = {
  watching: 'Watching for verification checks.',
  'waiting-page': 'Verification detected; waiting for its tab to finish loading.',
  'waiting-foreground': 'Select the verification tab and bring its Chrome window forward. No new attempt has been spent.',
  'waiting-tab-ready': 'Waiting for the verification tab to finish loading and unfreeze. No new attempt has been spent.',
  starting: 'Automatic attempt starting.',
  connecting: 'Connecting to the verification tab. No hold has started yet.',
  'retrying-control': 'Waiting up to one minute before retrying Chrome control. No hold has started.',
  verified: 'Unchallenged catalogue recovery confirmed.',
  'collector-pending': 'Catalogue recovered; collector resumption is not confirmed. Check other challenged tabs and the connection.',
  'needs-attention': 'The automatic attempt did not confirm recovery. Check the reviewer tab.',
  'retry-pending': 'Verification is still present; another attempt is scheduled.',
  'attempts-exhausted': 'All 10 attempts for this check were used. Manual attention is needed.',
  'already-attempted': 'This check was already attempted; it still needs attention.',
  cooldown: 'Waiting for the hourly verification limit or minimum spacing.',
  'invalid-budget': 'Stored attempt limits are invalid; no input will be sent.',
  cancelled: 'Attempt cancelled because the page or enabled state changed.',
  'input-error': 'Input or cleanup could not be confirmed. Check the reviewer tab.',
  unsupported: 'This challenge layout is unsupported; no hold was sent.',
  'permission-required': 'Reload Reviewer Item Relay and accept Chrome’s debugging permission.',
  unavailable: 'Chrome control was unavailable. Check other debuggers and disable the old companion.',
  'native-connecting': 'Connecting to the Windows fallback. No hold has started yet.',
  'native-permission-required': 'Enable Windows fallback to grant its optional Chrome permissions.',
  'native-waiting-idle': 'Windows fallback is waiting for five seconds without user input.',
  'native-unavailable': 'Windows fallback could not start safely. Check that the helper is installed and Windows is unlocked.'
};
async function renderVerificationControls() {
  const target = $('verificationAssistStatus');
  try {
    const value = await chrome.storage.local.get([
      'verificationAssistEnabled', 'verificationAssistStatus', 'verificationAssistLastResult',
      'verificationAssistRetry', 'verificationNativeEnabled'
    ]);
    const active = value.verificationAssistEnabled === true;
    const native = value.verificationNativeEnabled === true;
    $('enableNativeVerification').disabled = native;
    $('disableNativeVerification').disabled = !native;
    $('nativeVerificationStatus').textContent = native
      ? 'Enabled. Used after a safe Chrome failure; requires the installed Windows helper and an unlocked desktop.'
      : 'Disabled. Install the Windows helper before enabling this fallback.';
    $('enableVerificationAssist').disabled = active;
    $('disableVerificationAssist').disabled = !active;
    const result = value.verificationAssistLastResult;
    target.textContent = active ? 'Enabled. ' +
      (verificationStateLabels[value.verificationAssistStatus?.code] || 'Checking existing verification pauses…') : 'Disabled.';
    if (result) {
      target.textContent += ' Last attempt: ' +
        (verificationStateLabels[result.outcome] || 'Not yet completed.');
      if (result.pressSent === false) target.textContent += ' No press was sent.';
      else if (result.outcome === 'input-error' && result.pressConfirmed !== true) {
        target.textContent += ' The press was not confirmed by the input transport.';
      }
      if (Number.isFinite(result.holdMs) && result.pressSent !== false) {
        const label = result.outcome === 'input-error' && result.pressConfirmed !== true
          ? 'Input duration' : 'Hold';
        target.textContent += ` ${label}: ${(result.holdMs / 1000).toFixed(2)} seconds.`;
      }
      const stages = { inspect: 'reading the page', move: 'moving the pointer',
        press: 'pressing the button', release: 'releasing the button', detach: 'disconnecting input control' };
      const reasons = result.transport === 'native' ? {
        timeout: 'Windows helper request timed out', detached: 'Windows helper disconnected',
        'command-failed': 'Windows helper rejected input or disconnected'
      } : { timeout: 'Chrome command timed out', detached: 'Chrome control disconnected',
        'command-failed': 'Chrome command failed' };
      if (stages[result.errorStage]) target.textContent += ` Failed while ${stages[result.errorStage]}.`;
      if (reasons[result.errorReason]) target.textContent += ` ${reasons[result.errorReason]}.`;
      if (stages[result.cleanupErrorStage]) target.textContent +=
        ` Cleanup could not confirm ${stages[result.cleanupErrorStage]}.`;
      if (result.transport === 'native') {
        target.textContent += ' Input: Windows mouse.';
        const nativeReasons = { 'user-input': 'Stopped when you interacted with Windows.',
          'focus-changed': 'Stopped when window focus changed.', 'desktop-locked': 'Windows desktop was locked.',
          'window-changed': 'The Chrome window moved or changed.', 'viewport-mismatch': 'Window geometry did not match the page.',
          'host-unavailable': 'The Windows helper connection was unavailable.', 'release-failed': 'Button release was not confirmed.' };
        if (nativeReasons[result.nativeReason]) target.textContent += ` ${nativeReasons[result.nativeReason]}`;
      }
    }
    const retry = value.verificationAssistRetry;
    const canRetry = active && value.verificationAssistStatus?.code === 'input-error' &&
      result?.outcome === 'input-error' && retry?.terminal === true &&
      retry.key === result.key && retry.attempt === result.attempt &&
      Number.isInteger(retry.attempt) && retry.attempt > 0 && retry.attempt < 10;
    $('retryVerificationAssist').hidden = !canRetry;
    $('retryVerificationAssist').disabled = !canRetry;
    $('verificationRetryHint').hidden = !canRetry;
    if (Number.isInteger(result?.attempt)) target.textContent += ` Attempt ${result.attempt} of 10.`;
    const release = { feedback: 'Completion feedback', deadline: 'Hold time limit',
      stalled: 'Progress stopped', 'max-duration': '30-second maximum', failed: 'Failure feedback' };
    if (release[result?.releaseReason]) target.textContent += ` Released: ${release[result.releaseReason]}.`;
    if (active && value.verificationAssistRetry?.nextAt > Date.now()) {
      target.textContent += ` Next attempt in about ${Math.ceil((value.verificationAssistRetry.nextAt - Date.now()) / 1000)} seconds.`;
    }
  } catch { target.textContent = 'Could not read automatic verification status.'; }
}
async function setVerificationEnabled(active) {
  try {
    if (active && !(await chrome.permissions?.contains({ permissions: ['debugger'] }))) {
      $('verificationAssistStatus').textContent = verificationStateLabels['permission-required'];
      return;
    }
    await persistScannerToggle('verificationAssistEnabled', active);
    await renderVerificationControls();
  } catch { $('verificationAssistStatus').textContent = 'Could not save automatic verification settings.'; }
}
$('enableVerificationAssist').addEventListener('click', () => setVerificationEnabled(true));
$('disableVerificationAssist').addEventListener('click', () => setVerificationEnabled(false));
async function setNativeEnabled(active) {
  try {
    if (active && !await chrome.permissions.request({ permissions: ['nativeMessaging', 'scripting'] })) {
      $('nativeVerificationStatus').textContent = 'Chrome permission was not granted.';
      return;
    }
    await persistScannerToggle('verificationNativeEnabled', active);
    await renderVerificationControls();
  } catch { $('nativeVerificationStatus').textContent = 'Could not save Windows fallback settings.'; }
}
$('enableNativeVerification').addEventListener('click', () => setNativeEnabled(true));
$('disableNativeVerification').addEventListener('click', () => setNativeEnabled(false));
$('retryVerificationAssist').addEventListener('click', async () => {
  $('retryVerificationAssist').disabled = true;
  try {
    const response = await chrome.runtime.sendMessage({ type: 'reviewer:verification-retry' });
    if (!response?.ok) {
      $('retryVerificationAssist').disabled = false;
      $('verificationAssistStatus').textContent =
        'This check cannot be retried now. It may have changed, reached its limit, or already be running.';
      return;
    }
    await renderVerificationControls();
  } catch {
    $('retryVerificationAssist').disabled = false;
    $('verificationAssistStatus').textContent = 'Could not request a verification retry.';
  }
});
chrome.storage.onChanged?.addListener((changes, area) => {
  if (area === 'local' && ['verificationAssistEnabled', 'verificationAssistStatus',
    'verificationAssistLastResult', 'verificationAssistRetry',
    'verificationNativeEnabled'].some(key => key in changes)) renderVerificationControls();
});
renderVerificationControls();

let lastHealth = null;
let lastHealthError = '';

$('exportDiagnostics').addEventListener('click', async () => {
  const resultEl = $('diagnosticsResult');
  try {
    const result = await chrome.runtime.sendMessage({ type: 'reviewer:diagnostics' });
    if (!result?.ok || !result.report) throw new Error('Diagnostics are unavailable. Reload the extension and try again.');
    const blob = new Blob([JSON.stringify(result.report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `reviewer-diagnostics-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1_000);
    resultEl.textContent = 'Diagnostic export downloaded.';
  } catch (err) {
    resultEl.textContent = err.message;
  }
});

/** Resolve a server route while refusing to send the optional token off-device. */
function localUrl(path) {
  const base = new URL(endpointEl.value.trim() || DEFAULT_ENDPOINT);
  if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(base.hostname)) {
    throw new Error('Use an http://127.0.0.1 or http://localhost endpoint.');
  }
  if (base.username || base.password || base.pathname.replace(/\/+$/, '') !== '/ingest' ||
      base.search || base.hash) {
    throw new Error('The endpoint must end exactly in /ingest, with no credentials or query.');
  }
  return new URL(path, base.origin).toString();
}

function authHeaders() {
  const token = tokenEl.value.trim();
  return token ? { 'X-Ingest-Token': token } : {};
}

async function optionsScannerSettings(overrides = {}) {
  const value = await chrome.storage.local.get(OPTIONS_SCANNER_KEYS);
  return validatedScannerSettings({ schemaVersion: 1,
    auxiliaryDepartments: value.auxiliaryDepartments ?? true,
    auxiliaryKeywords: value.auxiliaryKeywords ?? true,
    auxiliaryBrands: value.auxiliaryBrands ?? false,
    auxiliaryKeywordQueries: value.auxiliaryKeywordQueries ?? null,
    groupScannerTabs: value.groupScannerTabs ?? true,
    rotateScannerTabs: value.rotateScannerTabs ?? true,
    verificationAssistEnabled: value.verificationAssistEnabled ?? false,
    verificationNativeEnabled: value.verificationNativeEnabled ?? false,
    pathPattern: value.pathPattern || DEFAULT_PATH_PATTERN,
    ...overrides });
}

async function readBackendScannerSettings() {
  const response = await localFetch(localUrl('/scanner-settings'), {
    headers: authHeaders(), cache: 'no-store'
  }, 5_000);
  if (!response.ok) throw new Error('Scanner settings unavailable.');
  const body = await response.json();
  if (!body || !Object.hasOwn(body, 'settings')) throw new Error('Scanner settings unavailable.');
  return body.settings === null ? null : validatedScannerSettings(body.settings);
}

async function putBackendScannerSettings(settings) {
  const response = await localFetch(localUrl('/scanner-settings'), {
    method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(validatedScannerSettings(settings))
  });
  if (!response.ok) throw new Error('Scanner settings save failed.');
  return validatedScannerSettings((await response.json()).settings);
}

async function persistScannerToggle(key, active) {
  // A separate verification button changes saved desired settings, without
  // accidentally applying unsaved scan controls from the form.
  const current = await readBackendScannerSettings() || await optionsScannerSettings();
  const { schemaVersion, ...saved } = await putBackendScannerSettings({ ...current, [key]: active });
  await chrome.storage.local.set(saved);
}

/**
 * Fetch from a visible extension page. This is an explicit connection check
 * and a place for affected Chrome builds or policies to show an LNA prompt.
 */
function localFetch(url, init = {}, timeoutMs = 15_000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, {
    ...init,
    // localUrl() guarantees a literal loopback destination, so Chrome can
    // classify it correctly across both PNA-era and current LNA builds.
    signal: controller.signal
  }).finally(() => clearTimeout(timeout));
}

function connectionError(err, url = DEFAULT_ENDPOINT) {
  const detail = err?.message || String(err);
  if (err?.status === 403 || /\bHTTP\s+403\b/i.test(detail)) {
    return `The notifier at ${url} rejected the API token (HTTP 403). The extension's Ingest token ` +
      `likely does not match INGEST_TOKEN; update it, save the connection settings, and try again.`;
  }
  if (err?.name === 'AbortError') {
    return `The notifier request at ${url} timed out. Check that the reviewer-item-notifier container ` +
      `is running in Docker Desktop (or run run.ps1), then try again.`;
  }
  if (/failed to fetch|networkerror|load failed/i.test(detail)) {
    return `Could not connect to ${url}. Start the reviewer-item-notifier container in Docker Desktop ` +
      `(or run run.ps1), then click Check connection ` +
      `and allow Chrome's local-network prompt if it appears. (${detail})`;
  }
  return `Notifier request failed at ${url}: ${detail}`;
}

// --- connection settings ----------------------------------------------------

chrome.storage.local
  .get(['endpoint', 'token', 'refreshMinutes', 'pageDelaySeconds', 'pathPattern', 'sweepMode',
    'auxiliaryDepartments', 'auxiliaryKeywords', 'auxiliaryBrands', 'auxiliaryKeywordQueries',
    'groupScannerTabs', 'rotateScannerTabs'])
  .then(async ({ endpoint = DEFAULT_ENDPOINT, token = '', refreshMinutes = 30,
           pageDelaySeconds = 15, pathPattern = DEFAULT_PATH_PATTERN, sweepMode = false,
           auxiliaryDepartments = true, auxiliaryKeywords = true, auxiliaryBrands = false,
           auxiliaryKeywordQueries, groupScannerTabs = true, rotateScannerTabs = true }) => {
    endpointEl.value = endpoint;
    tokenEl.value = token;
    refreshEl.value = refreshMinutes;
    pageDelayEl.value = pageDelaySeconds;
    sweepModeEl.value = normalizeSweepMode(sweepMode);
    pathEl.value = pathPattern;
    auxiliaryDepartmentsEl.checked = auxiliaryDepartments === true;
    auxiliaryKeywordsEl.checked = auxiliaryKeywords === true;
    auxiliaryBrandsEl.checked = auxiliaryBrands === true;
    groupScannerTabsEl.checked = groupScannerTabs !== false;
    rotateScannerTabsEl.checked = rotateScannerTabs !== false;
    updateAuxiliaryControls();
    try {
      auxiliaryQueriesEl.value = auxiliaryQueries(auxiliaryKeywordQueries ?? undefined).join('\n');
    } catch (err) {
      // A damaged optional setting must not prevent the primary form loading.
      auxiliaryQueriesEl.value = typeof auxiliaryKeywordQueries === 'string' ? auxiliaryKeywordQueries :
        Array.isArray(auxiliaryKeywordQueries) ? auxiliaryKeywordQueries.filter(value => typeof value === 'string').join('\n') : '';
      auxiliaryKeywordErrorEl.textContent = `Saved keywords need attention: ${err.message}`;
      auxiliaryQueriesEl.ariaInvalid = 'true';
    }
    try {
      const response = await localFetch(localUrl('/relay-settings'), {
        headers: authHeaders(), cache: 'no-store'
      }, 5_000);
      if (!response.ok) throw new Error('Scan settings unavailable.');
      const body = await response.json();
      if (body.settings != null) {
        const { schemaVersion, ...scan } = validatedRelaySettings(body.settings);
        refreshEl.value = scan.refreshMinutes;
        pageDelayEl.value = scan.pageDelaySeconds;
        sweepModeEl.value = scan.sweepMode;
        await chrome.storage.local.set(scan);
      }
    } catch {
      savedEl.textContent = 'Could not read relay-settings.json; showing the last saved scan settings.';
    }
    try {
      const scanner = await readBackendScannerSettings();
      if (scanner) {
        const { schemaVersion, ...cached } = scanner;
        await chrome.storage.local.set(cached);
        pathEl.value = scanner.pathPattern;
        auxiliaryDepartmentsEl.checked = scanner.auxiliaryDepartments;
        auxiliaryKeywordsEl.checked = scanner.auxiliaryKeywords;
        auxiliaryBrandsEl.checked = scanner.auxiliaryBrands;
        groupScannerTabsEl.checked = scanner.groupScannerTabs;
        rotateScannerTabsEl.checked = scanner.rotateScannerTabs;
        auxiliaryQueriesEl.value = auxiliaryQueries(scanner.auxiliaryKeywordQueries ?? undefined).join('\n');
        updateAuxiliaryControls();
        await renderVerificationControls();
      }
    } catch {
      savedEl.textContent += (savedEl.textContent ? ' ' : '') +
        'Could not read scanner-settings.json; showing the last saved scanner settings.';
    }
    renderStatus();
  });

$('save').addEventListener('click', async () => {
  try {
    localUrl('/ingest');
  } catch (err) {
    endpointErrorEl.textContent = err.message;
    return;
  }
  endpointErrorEl.textContent = '';

  const pattern = pathEl.value.trim() || DEFAULT_PATH_PATTERN;
  if (pattern.length > 200) {
    pathErrorEl.textContent = 'Use a reviewer page pattern of 200 characters or fewer.';
    return;
  }
  try {
    new RegExp(pattern);
  } catch (err) {
    pathErrorEl.textContent = `Not a valid regular expression: ${err.message}`;
    return;
  }
  pathErrorEl.textContent = '';

  let queries;
  try {
    queries = auxiliaryQueries(auxiliaryQueriesEl.value);
    if (auxiliaryKeywordsEl.checked && !queries.length) {
      throw new Error('Add at least one item name or keyword to enable keyword scanning.');
    }
  } catch (err) {
    auxiliaryKeywordErrorEl.textContent = err.message;
    auxiliaryQueriesEl.ariaInvalid = 'true';
    auxiliaryQueriesEl.focus?.();
    return;
  }
  auxiliaryKeywordErrorEl.textContent = '';
  auxiliaryQueriesEl.ariaInvalid = 'false';

  const scan = validatedRelaySettings({
    schemaVersion: 1,
    sweepMode: normalizeSweepMode(sweepModeEl.value),
    pageDelaySeconds: Math.min(60, Math.max(5, parseInt(pageDelayEl.value, 10) || 15)),
    refreshMinutes: Math.min(120, Math.max(0, parseInt(refreshEl.value, 10) || 0))
  });
  let scanner;
  try {
    scanner = await putBackendScannerSettings(await optionsScannerSettings({
      auxiliaryDepartments: auxiliaryDepartmentsEl.checked,
      auxiliaryKeywords: auxiliaryKeywordsEl.checked,
      auxiliaryBrands: auxiliaryDepartmentsEl.checked && auxiliaryBrandsEl.checked,
      auxiliaryKeywordQueries: queries,
      groupScannerTabs: groupScannerTabsEl.checked,
      rotateScannerTabs: rotateScannerTabsEl.checked,
      pathPattern: pattern
    }));
    const response = await localFetch(localUrl('/relay-settings'), {
      method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(scan)
    });
    if (!response.ok) throw new Error('Scan settings save failed.');
    validatedRelaySettings((await response.json()).settings);
  } catch {
    savedEl.textContent = 'Not saved: could not confirm both scanner-settings.json and relay-settings.json. ' +
      'Some backend values may have changed; reload before retrying.';
    return;
  }
  const { schemaVersion, ...cachedScan } = scan;
  const { schemaVersion: scannerVersion, ...cachedScanner } = scanner;
  await chrome.storage.local.set({
    endpoint: endpointEl.value.trim() || DEFAULT_ENDPOINT,
    token: tokenEl.value.trim(),
    ...cachedScan,
    ...cachedScanner
  });
  auxiliaryQueriesEl.value = queries.join('\n');
  flash(savedEl, 'Saved');
  renderStatus();
});

function updateAuxiliaryControls() {
  auxiliaryBrandsEl.disabled = !auxiliaryDepartmentsEl.checked;
  auxiliaryKeywordErrorEl.textContent = '';
  auxiliaryQueriesEl.ariaInvalid = 'false';
}
auxiliaryDepartmentsEl.addEventListener('change', updateAuxiliaryControls);
auxiliaryKeywordsEl.addEventListener('change', updateAuxiliaryControls);
auxiliaryQueriesEl.addEventListener('input', () => {
  auxiliaryKeywordErrorEl.textContent = '';
  auxiliaryQueriesEl.ariaInvalid = 'false';
});

$('restartAuxiliary').addEventListener('click', async () => {
  const button = $('restartAuxiliary');
  const resultEl = $('auxiliaryActionStatus');
  button.disabled = true;
  resultEl.textContent = '';
  try {
    const result = await chrome.runtime.sendMessage({ type: 'reviewer:aux-restart' });
    if (!result?.ok) throw new Error(result?.error || 'Reload the extension and check the reviewer tab.');
    resultEl.textContent = 'Additional scans restarted.';
  } catch (err) {
    resultEl.textContent = `Could not restart additional scans: ${err.message}`;
  } finally {
    button.disabled = false;
    await renderAuxiliaryStatus();
  }
});

$('resetSweep').addEventListener('click', async () => {
  try {
    // The background coordinator owns the active tab and generation. Asking it
    // to restart prevents passive fallback tabs from racing the new sweep.
    const result = await chrome.runtime.sendMessage({ type: 'reviewer:restart' });
    if (!result?.ok) {
      throw new Error(result?.error || 'No reachable reviewer tab is open.');
    }
    flash(savedEl, 'Sweep restarted');
  } catch (err) {
    flash(savedEl, `Could not restart the sweep: ${err.message}`);
  }
  renderStatus();
});

checkConnectionEl.addEventListener('click', async () => {
  checkConnectionEl.disabled = true;
  endpointErrorEl.textContent = '';
  try {
    const resp = await localFetch(localUrl('/health'), {
      cache: 'no-store', headers: authHeaders()
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    lastHealth = await resp.json();
    flash(savedEl, `Connected to ${lastHealth.notifier || 'notifier'}`);
  } catch (err) {
    endpointErrorEl.textContent = connectionError(err, endpointEl.value.trim());
  } finally {
    checkConnectionEl.disabled = false;
    renderStatus();
  }
});

testNotificationEl.addEventListener('click', async () => {
  testNotificationEl.disabled = true;
  testResultEl.textContent = '';
  testResultEl.className = '';
  const headers = {};
  const token = tokenEl.value.trim();
  if (token) headers['X-Ingest-Token'] = token;
  try {
    const resp = await localFetch(localUrl('/test-notification'), {
      method: 'POST', headers
    }, 25_000);
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok || !payload.ok) {
      throw new Error(payload.detail || payload.error || `HTTP ${resp.status}`);
    }
    if (payload.detail) {
      testResultEl.className = 'warn';
      testResultEl.textContent = `Test push sent. ${payload.detail}`;
    } else {
      testResultEl.className = 'saved';
      flash(testResultEl, 'Test alert sent');
    }
  } catch (err) {
    testResultEl.className = 'err';
    testResultEl.textContent = connectionError(err, endpointEl.value.trim() || DEFAULT_ENDPOINT);
  } finally {
    testNotificationEl.disabled = false;
    renderStatus();
  }
});

function flash(el, message) {
  el.textContent = message;
  setTimeout(() => { el.textContent = ''; }, 2500);
}

// --- status -----------------------------------------------------------------

function ago(ts) {
  const mins = Math.floor((Date.now() - ts) / 60000);
  if (mins < 1) return 'just now';
  if (mins === 1) return '1 minute ago';
  if (mins < 60) return `${mins} minutes ago`;
  const hrs = Math.floor(mins / 60);
  return hrs === 1 ? '1 hour ago' : `${hrs} hours ago`;
}

function line(cls, strong, rest = '') {
  const span = document.createElement('span');
  span.className = cls;
  span.textContent = strong;
  const div = document.createElement('div');
  div.appendChild(span);
  if (rest) div.appendChild(document.createTextNode(` ${rest}`));
  return div;
}

function safeCount(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

async function renderAuxiliaryStatus() {
  const target = $('auxiliaryStatus');
  try {
    const result = await chrome.runtime.sendMessage({ type: 'reviewer:aux-status' });
    if (!result?.ok || !Array.isArray(result.lanes)) throw new Error('Status unavailable.');
    const frag = document.createDocumentFragment();
    let rendered = 0;
    const names = { departments: 'Department scan', keywords: 'Keyword scan' };
    const labels = {
      disabled: 'Disabled', idle: 'Waiting to start', ready: 'Waiting to start', starting: 'Starting',
      scanning: 'Scanning', running: 'Scanning', discovering: 'Discovering filters',
      paused: 'Paused for verification', 'verification-paused': 'Paused for verification',
      waiting: 'Waiting', cooldown: 'Waiting for the next round',
      complete: 'Round complete', completed: 'Round complete', partial: 'Round incomplete',
      retrying: 'Waiting to retry', error: 'Needs attention', failed: 'Needs attention'
    };
    for (const lane of result.lanes) {
      if (!lane || !Object.hasOwn(names, lane.lane)) continue;
      const state = typeof lane.status === 'string' ? lane.status : 'idle';
      const description = Object.hasOwn(labels, state) ? labels[state] : state.slice(0, 80);
      const label = typeof lane.targetLabel === 'string' ? lane.targetLabel.slice(0, 200) : '';
      let detail = `${description}.`;
      if (label) detail += ` Current target: ${label}.`;
      detail += ` ${safeCount(lane.completedTargets)} of ${safeCount(lane.totalTargets)} targets complete; ` +
        `${safeCount(lane.pages)} pages read; ${safeCount(lane.failures)} failures.`;
      if (safeCount(lane.truncatedTargets)) {
        detail += ` ${safeCount(lane.truncatedTargets)} targets reached the coverage limit.`;
      }
      if (safeCount(lane.coverageLimitedTargets)) {
        detail += ` ${safeCount(lane.coverageLimitedTargets)} targets reached the page limit without further filters.`;
      }
      if (typeof lane.lastError === 'string' && lane.lastError) {
        detail += ` Last problem: ${lane.lastError.slice(0, 240)}`;
      }
      if (Number.isFinite(lane.nextAt) && lane.nextAt > Date.now()) {
        detail += ` Next action in about ${Math.ceil((lane.nextAt - Date.now()) / 60_000)} minutes.`;
      }
      const cls = ['error', 'failed', 'partial', 'paused', 'verification-paused'].includes(state) ? 'warn' : 'hint';
      frag.appendChild(line(cls, `${names[lane.lane]}:`, detail));
      rendered += 1;
    }
    if (!rendered) {
      frag.appendChild(line('hint', 'Additional scans are disabled or have not started.',
        'Enable coverage above and save settings to start.'));
    }
    target.replaceChildren(frag);
  } catch {
    target.textContent = 'Additional scan status is unavailable. Reload the extension if it was just updated.';
  }
}

/** Render only named aggregate fields; never expose queue payloads or errors. */
function appendDiscordStatus(frag, discord) {
  const live = discord.live_sending === true;
  frag.appendChild(line(live ? 'ok' : 'warn',
    live ? 'Discord background sending is on.' : 'Discord sending is off.',
    live ? 'Tier delays and Discord rate limits apply.' :
      'Observations can be queued, but no Discord messages are being sent.'));
  const jobs = discord.jobs || {};
  frag.appendChild(line('hint', 'Discord delivery queue (destination messages):',
    `${safeCount(jobs.pending)} pending, ${safeCount(jobs.leased)} processing; ` +
    `${safeCount(jobs.sent)} sent, ${safeCount(jobs.failed)} failed, ` +
    `${safeCount(jobs.expired)} expired, ${safeCount(jobs.skipped)} skipped.`));
  const delivery = discord.last_delivery;
  if (delivery && typeof delivery === 'object') {
    frag.appendChild(line(delivery.failed || delivery.blocked ? 'warn' : 'hint',
      `Last delivery pass: ${safeCount(delivery.sent)} sent, ` +
      `${safeCount(delivery.retried)} retried, ${safeCount(delivery.failed)} failed.`));
    if (delivery.blocked === true) {
      frag.appendChild(line('bad', 'Discord delivery is blocked.',
        'Check the backend logs and Discord permission audit. Queued work is retained.'));
    }
  }
}

/** Health and safe aggregate diagnostics, or null when the server is unreachable. */
async function serverHealth() {
  try {
    const resp = await localFetch(localUrl('/health'), { headers: authHeaders() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    lastHealth = await resp.json();
    lastHealthError = '';
    return lastHealth;
  } catch (err) {
    lastHealthError = connectionError(err, endpointEl.value.trim() || DEFAULT_ENDPOINT);
    return null;
  }
}

/** The coordinator-selected sweep and the number of eligible fallback tabs. */
async function activeSweep() {
  try {
    const status = await chrome.runtime.sendMessage({ type: 'reviewer:status' });
    if (!status?.ok) return { sweep: null, tabCount: 0, pausedUntil: 0 };
    // A tab failover can leave an old per-tab sweep record around until the
    // newly selected content script writes its first progress update. Never
    // combine that stale record with the latest page result.
    const sweep = status.sweep?.generation === status.generation
      ? status.sweep
      : null;
    return {
      sweep,
      tabCount: status.tabCount || 0,
      pausedUntil: Number(status.pausedUntil) || 0
    };
  } catch {
    return { sweep: null, tabCount: 0, pausedUntil: 0 };
  }
}

/**
 * Include a page whose ingest has completed but whose content script has not
 * yet persisted the matching `visited` update. That small hand-off window is
 * otherwise visible as "page 20" beside "19 pages done".
 */
function completedSweepPages(sweep, {
  lastPage, lastRelay, lastFailed = 0, lastPending = 0
}) {
  const total = Number(sweep?.total);
  const visited = new Set(
    (Array.isArray(sweep?.visited) ? sweep.visited : [])
      .filter((page) => Number.isInteger(page) && page > 0 &&
        (!Number.isInteger(total) || page <= total))
  );
  if (!Number.isInteger(total) || total < 1) return visited.size;

  const page = Number(lastPage);
  const relayAt = Number(lastRelay);
  const startedAt = Number(sweep?.startedAt);
  const progressedAt = Number(sweep?.lastProgressAt);
  let next = 1;
  while (next <= total && visited.has(next)) next += 1;

  const belongsToThisSweep = Number.isInteger(page) && page === next &&
    Number.isFinite(relayAt) && Number.isFinite(startedAt) && relayAt >= startedAt &&
    (!Number.isFinite(progressedAt) || relayAt >= progressedAt);
  const deliveryComplete = !Number(lastFailed) && !Number(lastPending);
  if (belongsToThisSweep && deliveryComplete) visited.add(page);
  return Math.min(visited.size, total);
}

async function renderStatus() {
  void renderAuxiliaryStatus();
  const { endpoint = DEFAULT_ENDPOINT, lastRelay, lastSeen = 0, lastNotified = 0, lastQueued = 0,
          lastNew = 0, lastDuplicates = Math.max(0, lastSeen - lastNew),
          lastFiltered = 0, lastMatched = 0, lastFailed = 0, lastPending = 0,
          lastSeeded = 0,
          lastValueKnown = 0, lastValueUnknown = 0, lastMinValue = null,
          lastMaxValue = null, lastPage = 1, lastError = '', lastErrorAt,
          lastSweepPages, lastSweepDone, refreshMinutes = 30,
          pageDelaySeconds = 15, botCheck, sweepMode = 'none', reviewerDiagnostics = [],
          diagnosticsSyncedAt = 0, diagnosticsSyncError = '', scanSettingsSyncError = '',
          lastVerificationCapture, lastVerificationCaptureError = '',
          lastVerificationAlertError = '' } = await chrome.storage.local.get(
    ['endpoint', 'lastRelay', 'lastSeen', 'lastNotified', 'lastQueued', 'lastPage',
     'lastNew', 'lastDuplicates', 'lastFiltered', 'lastMatched', 'lastFailed',
     'lastPending', 'lastSeeded', 'lastValueKnown', 'lastValueUnknown', 'lastMinValue',
     'lastMaxValue', 'lastError', 'lastErrorAt', 'lastSweepPages', 'lastSweepDone',
     'refreshMinutes', 'pageDelaySeconds', 'botCheck', 'sweepMode', 'reviewerDiagnostics',
     'diagnosticsSyncedAt', 'diagnosticsSyncError', 'scanSettingsSyncError',
     'lastVerificationCapture', 'lastVerificationCaptureError', 'lastVerificationAlertError']
  );
  const { sweep, tabCount, pausedUntil } = await activeSweep();
  const health = await serverHealth();
  const discord = health?.discord && typeof health.discord === 'object' ? health.discord : null;

  // Built as nodes rather than an HTML string: lastError can contain a server
  // response, and that must never be parsed as markup.
  const frag = document.createDocumentFragment();
  if (scanSettingsSyncError) {
    frag.appendChild(line('warn', 'Scan settings need attention:', scanSettingsSyncError));
  }
  if (!refreshMinutes) {
    frag.appendChild(line('warn', 'Auto-refresh is off.',
      'Only the current sweep runs. Set a refresh interval and save to keep monitoring.'));
  } else {
    frag.appendChild(line('hint', `Scan target: ${refreshMinutes} minutes between starts.`,
      'Every results page is checked; loading and retries can extend the cycle.'));
  }
  if (pageDelaySeconds < 5) {
    frag.appendChild(line('hint', 'Page delay uses a 5-second minimum.',
      'Save 15 seconds or more for gentler browsing.'));
  }
  const pacingSweep = sweep || { mode: normalizeSweepMode(sweepMode), total: lastSweepPages };
  const seconds = effectivePageSeconds({ pageDelaySeconds, refreshMinutes, botCheck }, pacingSweep);
  frag.appendChild(line('hint', `Page spacing: about ${Math.round(seconds)} seconds, with varying timing.`,
    'Saved page delay is a minimum; full page coverage is preserved.'));
  if (lastVerificationCapture?.status === 'saved') {
    frag.appendChild(line('hint', 'Verification screenshot sent to Downloads/ReviewerItemRelay.',
      'Chrome download settings determine when the file finishes saving.'));
  }
  if (lastVerificationCaptureError) {
    frag.appendChild(line('hint', 'Verification evidence:', lastVerificationCaptureError));
  }
  if (lastVerificationAlertError) {
    frag.appendChild(line('warn', 'Last verification alert:', lastVerificationAlertError));
  }
  const recentEvents = Array.isArray(reviewerDiagnostics) ? reviewerDiagnostics.filter(entry =>
    entry?.at > Date.now() - 3600_000) : [];
  const loads = recentEvents.filter(entry => entry.event === 'page-loaded').length;
  const checks = recentEvents.filter(entry => entry.event === 'challenge').length;
  frag.appendChild(line('hint', `Recent retained diagnostics: ${loads} page loads and ${checks} verification checks in the last hour.`,
    'History is capped at 300 events; export below for navigation and retry details.'));
  if (diagnosticsSyncedAt) {
    frag.appendChild(line('hint', `Diagnostics mirrored to the local notifier ${ago(diagnosticsSyncedAt)}.`));
  }
  if (diagnosticsSyncError) {
    frag.appendChild(line('warn', 'Diagnostic mirror needs attention:', diagnosticsSyncError));
  }
  const completed = Array.isArray(reviewerDiagnostics)
    ? [...reviewerDiagnostics].reverse().find(entry => entry?.event === 'sweep-complete') : null;
  if (completed && Number.isFinite(completed.durationMs)) {
    frag.appendChild(line('hint', `Last complete scan: ${Math.ceil(completed.durationMs / 60_000)} minutes for ${completed.pages || 0} pages.`,
      'New items can appear on any page; a shorter target does not guarantee that discovery time.'));
  }
  if (pausedUntil > 0) {
    frag.appendChild(line(
      'bad', 'All scanning paused for Walmart verification.',
      'Clear every open check. Monitoring resumes automatically when all ' +
      'challenged tabs are clear and the reviewer page is ready. Elapsed time and Restart sweep do not clear this pause.'
    ));
  }
  if (!endpoint) {
    frag.appendChild(line('bad', 'No endpoint configured.', 'Set it above.'));
  } else if (!lastRelay) {
    frag.appendChild(line(
      'warn', 'Nothing relayed yet.',
      'Is the reviewer-item-notifier container running? (run.ps1 also works.) Open your reviewer page ' +
      'in a tab — status updates once it sends.'
    ));
  } else {
    const stale = Date.now() - lastRelay > 60 * 60 * 1000;
    frag.appendChild(line(
      stale ? 'warn' : 'ok',
      `Latest page result (relayed ${ago(lastRelay)})`,
      `— page ${lastPage}, ${lastSeen} item${lastSeen === 1 ? '' : 's'}: ` +
      (discord
        ? `${lastNew} new, ${lastDuplicates} already recorded; ` +
          `${safeCount(lastQueued)} observation${lastQueued === 1 ? '' : 's'} safely queued for Discord, ` +
          `${safeCount(lastNotified)} delivered during intake. Delivery runs in the background.`
        : `${lastNew} new, ${lastDuplicates} already recorded, ${lastMatched} matched, ` +
          `${lastNotified} alerted on this page.`)
    ));
    if (lastFiltered && !discord) {
      frag.appendChild(line(
        'hint', `${lastFiltered} new item${lastFiltered === 1 ? '' : 's'} on this page ` +
          `did not match the filters.`
      ));
    }
    if (discord && lastSeen && !safeCount(lastQueued) && !lastFailed && !lastPending) {
      frag.appendChild(line('hint', 'No additional Discord work queued from this page.',
        'Previously queued alerts can still be waiting for their tier delay or a retry.'));
    } else if (!discord && lastSeen && lastNew === 0 && !lastFailed && !lastPending) {
      frag.appendChild(line('hint', 'No new items on this page.',
        'All were already recorded, so there was nothing new to alert on.'));
    }
    if (lastFailed) {
      frag.appendChild(line(
        'bad', `${lastFailed} matched alert${lastFailed === 1 ? '' : 's'} failed delivery.`,
        'The relay will keep this page open and retry.'
      ));
    }
    if (lastPending) {
      frag.appendChild(line(
        'warn', `${lastPending} delivery attempt${lastPending === 1 ? ' is' : 's are'} still running.`,
        'The relay will keep this page open until the result is known.'
      ));
    }
    if (lastSeeded) {
      frag.appendChild(line('warn', `${lastSeeded} item${lastSeeded === 1 ? '' : 's'} seeded; alerts were intentionally disabled.`));
    }
    if (lastValueKnown || lastValueUnknown) {
      let range = '';
      if (lastMinValue !== null && lastMaxValue !== null) {
        range = lastMinValue === lastMaxValue
          ? ` Values on this page: $${Number(lastMinValue).toFixed(2)}.`
          : ` Values on this page: $${Number(lastMinValue).toFixed(2)}–$${Number(lastMaxValue).toFixed(2)}.`;
      }
      frag.appendChild(line(
        'hint', `${lastValueKnown} value${lastValueKnown === 1 ? '' : 's'} parsed; ` +
          `${lastValueUnknown} unknown.${range}`
      ));
    }
    // Per-page counts read like total coverage; show sweep progress too.
    if (tabCount > 1) {
      frag.appendChild(line(
        'hint', `${tabCount} reviewer tabs open.`,
        'Only the first reachable tab is active; the others are passive fallbacks.'
      ));
    }
    if (sweep && sweepQueries(sweepRecordMode(sweep)).length) {
      const mode = sweepRecordMode(sweep);
      const queries = sweepQueries(mode);
      const unit = mode === 'sweep' ? 'letter' : 'keyword';
      const label = mode === 'sweep' ? 'Sweep mode' : 'High Value mode';
      frag.appendChild(line(
        'hint', `${label}: search ${sweep.query} (${queries.indexOf(sweep.query) + 1} of ${queries.length} ${unit}s).`,
        `${sweep.completedPages || 0} pages read for earlier ${unit}s; ` +
          `${sweep.visited.length}${sweep.total ? ` of ${sweep.total}` : ''} pages done for this ${unit}.`
      ));
    } else if (sweep && sweep.total) {
      const done = completedSweepPages(sweep, {
        lastPage, lastRelay, lastFailed, lastPending
      });
      frag.appendChild(line(
        'hint', `Sweep in progress: ${done} of ${sweep.total} pages done.`
      ));
    } else if (lastSweepPages) {
      frag.appendChild(line(
        'hint',
        `Last sweep covered ${lastSweepPages} pages` +
        (lastSweepDone ? `, finished ${ago(lastSweepDone)}.` : '.')
      ));
    }
    if (health?.seen_items !== undefined) {
      frag.appendChild(line('hint', `${health.seen_items} distinct items recorded so far.`));
    }
    if (stale) {
      frag.appendChild(line('hint', 'Over an hour ago.', 'Is the reviewer tab still open?'));
    }
  }
  if (health) {
    const provider = health.notifier || 'notifier';
    frag.appendChild(line(
      health.notifier_configured ? 'ok' : 'bad',
      health.notifier_configured ? `${provider} configured.` : 'No notification provider configured.'
    ));
    if (health.seed_mode) {
      frag.appendChild(line('bad', 'Seed mode is on.', 'Items are recorded but no alerts are sent.'));
    }
    if (health.notifier_diagnostic) {
      frag.appendChild(line('warn', 'Notification provider note:', health.notifier_diagnostic));
    }
    if (discord) appendDiscordStatus(frag, discord);
    if (health.telegram_commands?.delivery_chat === 'group') {
      frag.appendChild(line('hint', 'Telegram alerts go to your selected group.',
        'Use /useprivate in your private bot chat to switch back.'));
    }
  } else if (endpoint) {
    const tokenRejected = /rejected the API token \(HTTP 403\)/i.test(lastHealthError);
    frag.appendChild(line(
      'bad', tokenRejected ? 'API token mismatch.' : 'Notifier is unreachable.',
      lastHealthError ||
        'Start the reviewer-item-notifier container in Docker Desktop (or run run.ps1), then click ' +
        'Check connection and approve local-network access if Chrome asks.'
    ));
  }
  if (lastError) {
    frag.appendChild(line(
      'bad', `Last error${lastErrorAt ? ` (${ago(lastErrorAt)})` : ''}:`, lastError
    ));
  }
  statusEl.replaceChildren(frag);
}

renderStatus();
setInterval(renderStatus, 30000);
