// Connection settings live in chrome.storage.local. Alert filters do NOT --
// they live on the notifier, which is the only thing that actually applies
// them. Keeping a second copy in the browser would mean two sources of truth
// that silently disagree the moment either is edited elsewhere.

const DEFAULT_PATH_PATTERN = '^/reviews/claim-product';
const DEFAULT_ENDPOINT = 'http://127.0.0.1:8787/ingest';
const UI_RULE_NAME = 'my-filters';

const $ = (id) => document.getElementById(id);

const endpointEl = $('endpoint');
const endpointErrorEl = $('endpointError');
const tokenEl = $('token');
const pathEl = $('pathPattern');
const pathErrorEl = $('pathError');
const refreshEl = $('refreshMinutes');
const pageDelayEl = $('pageDelaySeconds');
const savedEl = $('saved');
const statusEl = $('status');

const minEl = $('minValue');
const maxEl = $('maxValue');
const keywordsEl = $('keywords');
const excludeEl = $('excludeKeywords');
const priorityEl = $('priority');
const alertUnknownEl = $('alertUnknown');
const rulesBannerEl = $('rulesBanner');
const rulesSavedEl = $('rulesSaved');
const rulesErrorEl = $('rulesError');
const checkConnectionEl = $('checkConnection');
const testNotificationEl = $('testNotification');
const testResultEl = $('testResult');

let activeRule = null;
let lastHealth = null;
let lastHealthError = '';

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
  .get(['endpoint', 'token', 'refreshMinutes', 'pageDelaySeconds', 'pathPattern'])
  .then(({ endpoint = DEFAULT_ENDPOINT, token = '', refreshMinutes = 0,
           pageDelaySeconds = 5, pathPattern = DEFAULT_PATH_PATTERN }) => {
    endpointEl.value = endpoint;
    tokenEl.value = token;
    refreshEl.value = refreshMinutes;
    pageDelayEl.value = pageDelaySeconds;
    pathEl.value = pathPattern;
    loadRules().finally(renderStatus);
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
  try {
    new RegExp(pattern);
  } catch (err) {
    pathErrorEl.textContent = `Not a valid regular expression: ${err.message}`;
    return;
  }
  pathErrorEl.textContent = '';

  await chrome.storage.local.set({
    endpoint: endpointEl.value.trim() || DEFAULT_ENDPOINT,
    token: tokenEl.value.trim(),
    pathPattern: pattern,
    pageDelaySeconds: Math.min(60, Math.max(1, parseInt(pageDelayEl.value, 10) || 5)),
    refreshMinutes: Math.min(120, Math.max(0, parseInt(refreshEl.value, 10) || 0))
  });
  flash(savedEl, 'Saved');
  renderStatus();
});

// --- alert filters ----------------------------------------------------------

/** The rules endpoint sits alongside whatever /ingest the user configured. */
function rulesUrl() {
  return localUrl('/rules');
}

async function rulesFetch(method, body) {
  const headers = {};
  const token = tokenEl.value.trim();
  if (token) headers['X-Ingest-Token'] = token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const resp = await localFetch(rulesUrl(), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const text = await resp.text();
  let parsed = null;
  try {
    parsed = JSON.parse(text);
  } catch { /* server sent plain text */ }
  if (!resp.ok) {
    const detail = parsed?.error || text;
    throw new Error(`HTTP ${resp.status}${detail ? `: ${detail}` : ''}`);
  }
  return parsed;
}

const csv = (list) => (list || []).join(', ');
const parseCsv = (value) =>
  (value || '').split(',').map((s) => s.trim()).filter(Boolean);

function fillForm(rule) {
  activeRule = rule || {};
  minEl.value = rule.min_value_usd ?? '';
  maxEl.value = rule.max_value_usd ?? '';
  keywordsEl.value = csv(rule.keywords);
  excludeEl.value = csv(rule.exclude_keywords);
  priorityEl.value = rule.priority || 'normal';
  alertUnknownEl.checked = rule.alert_on_unknown_value !== false;
}

function banner(text, tone = 'banner') {
  rulesBannerEl.replaceChildren();
  if (!text) return;
  const div = document.createElement('div');
  div.className = tone;
  div.textContent = text;
  rulesBannerEl.appendChild(div);
}

async function loadRules() {
  try {
    const { rules, source } = await rulesFetch('GET');
    fillForm(rules[0] || {});
    if (rules.length > 1) {
      // The form edits one rule. Saving would collapse a multi-rule setup, so
      // say that plainly instead of quietly discarding the rest.
      banner(
        `The notifier currently has ${rules.length} rules (${rules
          .map((r) => r.name)
          .join(', ')}) from the ${source} configuration. This form edits one ` +
        `combined rule — saving replaces all ${rules.length} with it. ` +
        `Edit src/rules.json directly if you want to keep them separate.`
      );
    } else if (source === 'env') {
      banner(
        'RULES_JSON is set in the environment, which overrides anything saved ' +
        'here. Remove it from notifier.env for this form to take effect.'
      );
    } else {
      banner('');
    }
    rulesErrorEl.textContent = '';
  } catch (err) {
    banner('');
    rulesErrorEl.textContent = connectionError(
      err, endpointEl.value.trim() || DEFAULT_ENDPOINT
    );
  }
}

function formToRule() {
  const rule = {
    name: UI_RULE_NAME,
    keywords: parseCsv(keywordsEl.value),
    exclude_keywords: parseCsv(excludeEl.value),
    priority: priorityEl.value,
    alert_on_unknown_value: alertUnknownEl.checked
  };
  const min = parseFloat(minEl.value);
  const max = parseFloat(maxEl.value);
  if (Number.isFinite(min)) rule.min_value_usd = min;
  if (Number.isFinite(max)) rule.max_value_usd = max;
  return rule;
}

$('saveRules').addEventListener('click', async () => {
  const rule = formToRule();
  if (rule.min_value_usd !== undefined && rule.max_value_usd !== undefined &&
      rule.min_value_usd > rule.max_value_usd) {
    rulesErrorEl.textContent = 'Minimum value is above the maximum — nothing would ever match.';
    return;
  }
  rulesErrorEl.textContent = '';
  try {
    const { rules } = await rulesFetch('PUT', { rules: [rule] });
    fillForm(rules[0]);
    banner('');
    flash(rulesSavedEl, 'Filters saved');
  } catch (err) {
    rulesErrorEl.textContent = `Could not save: ${err.message}`;
  }
});

$('resetRules').addEventListener('click', async () => {
  try {
    const { rules } = await rulesFetch('DELETE');
    fillForm(rules[0] || {});
    await loadRules();
    flash(rulesSavedEl, 'Reset to defaults');
  } catch (err) {
    rulesErrorEl.textContent = `Could not reset: ${err.message}`;
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
    await loadRules();
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
  const { endpoint = DEFAULT_ENDPOINT, lastRelay, lastSeen = 0, lastNotified = 0,
          lastNew = 0, lastDuplicates = Math.max(0, lastSeen - lastNew),
          lastFiltered = 0, lastMatched = 0, lastFailed = 0, lastPending = 0,
          lastSeeded = 0,
          lastValueKnown = 0, lastValueUnknown = 0, lastMinValue = null,
          lastMaxValue = null, lastPage = 1, lastError = '', lastErrorAt,
          lastSweepPages, lastSweepDone } = await chrome.storage.local.get(
    ['endpoint', 'lastRelay', 'lastSeen', 'lastNotified', 'lastPage',
     'lastNew', 'lastDuplicates', 'lastFiltered', 'lastMatched', 'lastFailed',
     'lastPending', 'lastSeeded', 'lastValueKnown', 'lastValueUnknown', 'lastMinValue',
     'lastMaxValue', 'lastError', 'lastErrorAt', 'lastSweepPages', 'lastSweepDone']
  );
  const { sweep, tabCount, pausedUntil } = await activeSweep();
  const health = await serverHealth();

  // Built as nodes rather than an HTML string: lastError can contain a server
  // response, and that must never be parsed as markup.
  const frag = document.createDocumentFragment();
  if (pausedUntil > Date.now()) {
    const minutes = Math.ceil((pausedUntil - Date.now()) / 60_000);
    frag.appendChild(line(
      'bad', `Paused after a Walmart bot check (${minutes} min left).`,
      'Solve the Press & Hold check by hand in the tab, then click Restart sweep ' +
      'to resume early. Consider a longer auto-refresh interval.'
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
      `${lastNew} new, ${lastDuplicates} already recorded, ${lastMatched} matched, ` +
      `${lastNotified} alerted on this page.`
    ));
    if (lastFiltered) {
      frag.appendChild(line(
        'hint', `${lastFiltered} new item${lastFiltered === 1 ? '' : 's'} on this page ` +
          `did not match the filters.`
      ));
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
    if (sweep && sweep.total) {
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
    const observedMax = health.observed_values?.max_value_usd;
    const floor = activeRule?.min_value_usd;
    if (Number.isFinite(floor) && Number.isFinite(observedMax) && floor > observedMax) {
      frag.appendChild(line(
        'warn', `Your $${Number(floor).toFixed(2)} minimum is above the highest recorded value ` +
          `($${Number(observedMax).toFixed(2)}).`,
        'No known-value item can match that minimum.'
      ));
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
