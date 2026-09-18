// Owns the one reviewer tab that is allowed to relay, posts its items to the
// local notifier, and restarts its sweep on a Chrome alarm.

const DEFAULTS = {
  endpoint: 'http://127.0.0.1:8787/ingest',
  token: '',
  refreshMinutes: 0,
  pageDelaySeconds: 5,
  pathPattern: '^/reviews/claim-product'
};

const REFRESH_ALARM = 'refresh';
const BADGE_ALARM = 'clearBadge';
const COORDINATOR_KEY = 'reviewerCoordinator';
const SWEEP_PREFIX = 'sweep:';
const EXCLUDE_PATH = /\/(orders?|purchase-history|track|returns)\b/i;
const JITTER = 0.2;
const MIN_DELAY_MIN = 1;
// Chrome 120+ permits a 30-second alarm. It may still run later than requested.
const BUSY_RETRY_MIN = 0.5;
const PROBE_TIMEOUT_MS = 2_000;
const PROBE_LOADING_RETRY_MS = 500;
const LOADING_LEASE_MS = 45_000;
const FETCH_TIMEOUT_MS = 12_000;
const STALE_SWEEP_MS = 2 * 60_000;
// Keep this in sync with sources.webhook_source.MAX_ITEMS. The strict ingest
// contract rejects larger arrays instead of silently truncating them.
const MAX_INGEST_ITEMS = 200;

// Walmart answers suspected automation by redirecting to /blocked, a "Robot or
// human? Press & Hold" page. That page is Walmart asking this traffic to stop,
// so the extension stops: no sweeps, no reloads, no retries until a backoff
// expires or the user explicitly restarts. Nothing here tries to get past the
// check -- the user solves it by hand.
const BOT_CHECK_KEY = 'botCheck';
const BOT_CHECK_PATH = /^\/blocked(\/|$)/i;
const BOT_BACKOFF_BASE_MIN = 60;
const BOT_BACKOFF_MAX_MIN = 24 * 60;
// Checks further apart than this are treated as unrelated; the backoff resets.
const BOT_COUNT_RESET_MS = 24 * 60 * 60_000;
// One block produces several onUpdated events; count them as a single check.
const BOT_DEBOUNCE_MS = 5 * 60_000;

async function config() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

const delay = (ms) => new Promise((done) => setTimeout(done, ms));

async function withTimeout(promise, ms, message) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(message)), ms);
      })
    ]);
  } finally {
    clearTimeout(timer);
  }
}

// --- bot-check pause ------------------------------------------------------

function isBotCheckUrl(raw) {
  try {
    const url = new URL(raw);
    return url.hostname === 'www.walmart.com' && BOT_CHECK_PATH.test(url.pathname);
  } catch {
    return false;
  }
}

async function readBotCheck() {
  const { [BOT_CHECK_KEY]: value } = await chrome.storage.local.get([BOT_CHECK_KEY]);
  return {
    pausedUntil: Number(value?.pausedUntil) || 0,
    count: Number.isInteger(value?.count) ? value.count : 0,
    lastAt: Number(value?.lastAt) || 0
  };
}

/** Epoch ms the pause ends, or 0 when automation may run. */
async function pausedUntil() {
  const { pausedUntil: until } = await readBotCheck();
  return until > Date.now() ? until : 0;
}

async function clearBotPause() {
  const current = await readBotCheck();
  // Keep count/lastAt so a check shortly after a manual resume still backs off
  // longer than the first one did.
  await chrome.storage.local.set({ [BOT_CHECK_KEY]: { ...current, pausedUntil: 0 } });
}

async function notifyBotCheck(minutes) {
  try {
    const { endpoint: raw, token } = await config();
    const url = new URL('/bot-check', checkedEndpoint(raw)).toString();
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-Ingest-Token'] = token;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    try {
      await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({ paused_minutes: minutes }),
        signal: controller.signal
      });
    } finally {
      clearTimeout(timer);
    }
  } catch (err) {
    // The pause itself is what matters; the phone alert is a courtesy.
    console.warn('Reviewer Item Relay: could not send the bot-check alert', err);
  }
}

let botCheckTail = Promise.resolve();
function handleBotCheck(tabId) {
  const run = botCheckTail.then(() => handleBotCheckUnlocked(tabId),
                                () => handleBotCheckUnlocked(tabId));
  botCheckTail = run.catch(() => undefined);
  return run;
}

async function handleBotCheckUnlocked(tabId) {
  const now = Date.now();
  const previous = await readBotCheck();
  if (previous.pausedUntil > now && now - previous.lastAt < BOT_DEBOUNCE_MS) {
    return { ok: true, duplicate: true, pausedUntil: previous.pausedUntil };
  }

  const count = now - previous.lastAt > BOT_COUNT_RESET_MS ? 1 : previous.count + 1;
  const minutes = Math.min(BOT_BACKOFF_BASE_MIN * 2 ** (count - 1), BOT_BACKOFF_MAX_MIN);
  const until = now + minutes * 60_000;
  await chrome.storage.local.set({
    [BOT_CHECK_KEY]: { pausedUntil: until, count, lastAt: now, tabId },
    lastError:
      `Walmart showed a bot check (${count} in the last 24h). Solve it by hand in ` +
      `the tab. Automation is paused for ${minutes} min; Restart sweep resumes early.`,
    lastErrorAt: now
  });

  // Stand every tab down and discard sweep progress. Without this, solving the
  // check redirects back to the reviewer page mid-catalogue, that tab is
  // promoted, and the walk resumes at full speed -- straight into another check.
  const state = await readCoordinator();
  const stood = await writeCoordinator({
    tabId: null, generation: state.generation + 1, selectedAt: 0
  });
  const stored = await chrome.storage.local.get(null);
  const sweepKeys = Object.keys(stored)
    .filter((key) => key === 'sweep' || key.startsWith(SWEEP_PREFIX));
  if (sweepKeys.length) await chrome.storage.local.remove(sweepKeys);
  await broadcastRoles(await reviewerTabs(), stood);

  await chrome.alarms.create(REFRESH_ALARM, { when: until });
  await notifyBotCheck(minutes);
  return { ok: true, count, minutes, pausedUntil: until };
}

// --- ingest -----------------------------------------------------------------

function checkedEndpoint(raw) {
  let endpoint;
  try {
    endpoint = new URL(raw);
  } catch {
    throw new Error(`The ingest endpoint is not a valid URL: ${raw || '(blank)'}`);
  }
  const loopback = endpoint.hostname === '127.0.0.1' || endpoint.hostname === 'localhost';
  if (endpoint.protocol !== 'http:' || !loopback) {
    throw new Error(
      'The ingest endpoint must use http://127.0.0.1 or http://localhost; ' +
      'the extension is intentionally permitted to send only to this machine.'
    );
  }
  if (endpoint.username || endpoint.password ||
      endpoint.pathname.replace(/\/+$/, '') !== '/ingest' ||
      endpoint.search || endpoint.hash) {
    throw new Error(
      'The ingest endpoint must end exactly in /ingest and must not contain ' +
      'credentials, a query string, or a fragment.'
    );
  }
  return endpoint.toString();
}

async function recordIngestFailure(message) {
  const { ingestConsecutiveFailures = 0 } =
    await chrome.storage.local.get(['ingestConsecutiveFailures']);
  const now = Date.now();
  await chrome.storage.local.set({
    lastAttempt: now,
    lastError: message,
    lastErrorAt: now,
    ingestConsecutiveFailures: ingestConsecutiveFailures + 1
  });
}

function networkFailureMessage(endpoint, err) {
  if (err?.name === 'AbortError') {
    return `The notifier at ${endpoint} did not respond within ${FETCH_TIMEOUT_MS / 1000} seconds. ` +
      'Keep the reviewer-item-notifier container running in Docker Desktop ' +
      '(or leave run.ps1 running); ' +
      'this page will retry automatically.';
  }
  return `Could not reach the notifier at ${endpoint}. Start the reviewer-item-notifier container in ` +
    'Docker Desktop (or run run.ps1), open the extension ' +
    'Options, click Check connection, and allow local-network access if Chrome asks. ' +
    'This page will retry automatically. ' +
    `(${err?.message || String(err)})`;
}

function batchLabel(index, count) {
  return count > 1 ? `Batch ${index + 1} of ${count}: ` : '';
}

async function postBatch(endpoint, headers, items, index, count) {
  const label = batchLabel(index, count);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  let resp;
  let text;
  try {
    resp = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify({ items }),
      // The validator permits only literal loopback hosts. Chrome therefore
      // knows the destination address space without an annotation. Explicitly
      // labelling 127.0.0.1 as `local` is wrong under current LNA naming and
      // Chrome rejects it because the resolved address space is `loopback`.
      signal: controller.signal
    });
    text = await resp.text();
  } catch (err) {
    const message = `${label}${networkFailureMessage(endpoint, err)}`;
    console.error('Reviewer Item Relay: ingest failed', err);
    await recordIngestFailure(message);
    return { ok: false, retryable: true, error: message };
  } finally {
    clearTimeout(timeout);
  }

  let summary = null;
  try {
    summary = text ? JSON.parse(text) : null;
  } catch {
    // Report a concise error below instead of persisting a long HTML response.
  }

  if (!resp.ok) {
    const detail = summary?.error || text.slice(0, 200) || `HTTP ${resp.status}`;
    const hint = resp.status === 403
      ? ' Check that the extension token matches INGEST_TOKEN.'
      : '';
    const message = `${label}Ingest returned HTTP ${resp.status}: ${detail}.${hint}`;
    console.error('Reviewer Item Relay:', message);
    await recordIngestFailure(message);
    return {
      ok: false,
      retryable: resp.status === 408 || resp.status === 429 || resp.status >= 500,
      error: message
    };
  }

  if (!summary || typeof summary !== 'object' || Array.isArray(summary)) {
    const message =
      `${label}The notifier returned a successful response that was not valid JSON.`;
    await recordIngestFailure(message);
    return { ok: false, retryable: true, error: message };
  }
  if (!Number.isInteger(summary.seen) || summary.seen !== items.length) {
    const message =
      `${label}The notifier acknowledged ${summary.seen ?? 'an unknown number of'} of ` +
      `${items.length} items; this page will retry instead of advancing.`;
    await recordIngestFailure(message);
    return { ok: false, retryable: true, error: message };
  }
  return { ok: true, summary };
}

const SUMMARY_COUNTS = [
  'new', 'filtered', 'matched', 'notified', 'failed', 'pending', 'seeded',
  'value_known', 'value_unknown'
];

function numericSummaryValue(summary, key, fallback = 0) {
  if (summary[key] === null || summary[key] === undefined) return fallback;
  const value = Number(summary[key]);
  return Number.isFinite(value) ? value : fallback;
}

function mergeSummary(total, next) {
  const fresh = numericSummaryValue(next, 'new');
  total.seen += next.seen;
  for (const key of SUMMARY_COUNTS) total[key] += numericSummaryValue(next, key);
  total.duplicates += numericSummaryValue(
    next, 'duplicates', Math.max(0, next.seen - fresh)
  );

  for (const [key, choose] of [
    ['min_value_usd', Math.min],
    ['max_value_usd', Math.max]
  ]) {
    if (next[key] === null || next[key] === undefined) continue;
    const value = Number(next[key]);
    if (!Number.isFinite(value)) continue;
    total[key] = total[key] === null ? value : choose(total[key], value);
  }
  return total;
}

function emptySummary() {
  const summary = {
    seen: 0,
    duplicates: 0,
    min_value_usd: null,
    max_value_usd: null
  };
  for (const key of SUMMARY_COUNTS) summary[key] = 0;
  return summary;
}

async function post(items, page) {
  const { endpoint: rawEndpoint, token } = await config();
  let endpoint;
  try {
    endpoint = checkedEndpoint(rawEndpoint);
  } catch (err) {
    const message = err.message || String(err);
    await recordIngestFailure(message);
    return { ok: false, retryable: false, error: message };
  }

  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['X-Ingest-Token'] = token;

  const summary = emptySummary();
  const batchCount = Math.max(1, Math.ceil(items.length / MAX_INGEST_ITEMS));
  for (let index = 0; index < batchCount; index += 1) {
    const start = index * MAX_INGEST_ITEMS;
    const batch = items.slice(start, start + MAX_INGEST_ITEMS);
    // Deliberately wait for each acknowledgement before sending the next
    // batch. If a later request fails, the content script retries the complete
    // page; successful earlier batches are harmless duplicates on that retry.
    const result = await postBatch(endpoint, headers, batch, index, batchCount);
    if (!result.ok) return result;
    mergeSummary(summary, result.summary);
  }

  const now = Date.now();
  await chrome.storage.local.set({
    lastAttempt: now,
    lastRelay: now,
    lastSeen: summary.seen,
    lastNew: summary.new ?? 0,
    lastDuplicates: summary.duplicates ?? Math.max(0, summary.seen - (summary.new ?? 0)),
    lastFiltered: summary.filtered ?? 0,
    lastMatched: summary.matched ?? 0,
    lastNotified: summary.notified ?? 0,
    lastFailed: summary.failed ?? 0,
    lastPending: summary.pending ?? 0,
    lastSeeded: summary.seeded ?? 0,
    lastValueKnown: summary.value_known ?? 0,
    lastValueUnknown: summary.value_unknown ?? 0,
    lastMinValue: summary.min_value_usd ?? null,
    lastMaxValue: summary.max_value_usd ?? null,
    lastPage: page ?? 1,
    lastError: '',
    ingestConsecutiveFailures: 0
  });

  let deliveryError = null;
  const pending = Number(summary.pending) || 0;
  const failed = Number(summary.failed) || 0;
  if (failed || pending) {
    const parts = [];
    if (failed) parts.push(`${failed} failed ${failed === 1 ? 'delivery' : 'deliveries'}`);
    if (pending) parts.push(`${pending} delivery attempt${pending === 1 ? ' is' : 's are'} still pending`);
    deliveryError = `${parts.join(' and ')}; this page will retry.`;
    await chrome.storage.local.set({
      lastError: deliveryError,
      lastErrorAt: Date.now()
    });
  }

  if (summary.notified) {
    try {
      await chrome.action.setBadgeText({ text: String(summary.notified) });
      await chrome.action.setBadgeBackgroundColor({ color: '#0071dc' });
      await chrome.alarms.create(BADGE_ALARM, { delayInMinutes: 10 });
    } catch (err) {
      console.warn('Reviewer Item Relay: could not update the badge', err);
    }
  }
  if (deliveryError) {
    return { ok: false, retryable: true, error: deliveryError, summary };
  }
  return { ok: true, summary };
}

// Mutation-heavy pages can produce another report before an earlier response
// reaches the content script. Serializing makes status writes deterministic.
let ingestTail = Promise.resolve();
function enqueuePost(items, page) {
  const result = ingestTail.then(() => post(items, page), () => post(items, page));
  ingestTail = result.catch(() => undefined);
  return result;
}

// --- reviewer-tab coordinator -----------------------------------------------

async function readCoordinator() {
  const stored = await chrome.storage.session.get([COORDINATOR_KEY]);
  const value = stored[COORDINATOR_KEY];
  if (!value || typeof value !== 'object') {
    return { tabId: null, generation: 0, selectedAt: 0 };
  }
  return {
    tabId: Number.isInteger(value.tabId) ? value.tabId : null,
    generation: Number.isInteger(value.generation) ? value.generation : 0,
    selectedAt: Number(value.selectedAt) || 0
  };
}

async function writeCoordinator(value) {
  await chrome.storage.session.set({ [COORDINATOR_KEY]: value });
  return value;
}

async function pathRegex() {
  const { pathPattern } = await config();
  try {
    return new RegExp(pathPattern, 'i');
  } catch {
    return new RegExp(DEFAULTS.pathPattern, 'i');
  }
}

function eligibleUrl(rawUrl, re) {
  try {
    const url = new URL(rawUrl);
    return url.protocol === 'https:' && url.hostname === 'www.walmart.com' &&
      !EXCLUDE_PATH.test(url.pathname) && re.test(url.pathname);
  } catch {
    return false;
  }
}

async function reviewerTabs() {
  const [tabs, re] = await Promise.all([
    chrome.tabs.query({ url: 'https://www.walmart.com/reviews/*' }),
    pathRegex()
  ]);
  return tabs
    .filter((tab) => Number.isInteger(tab.id) && eligibleUrl(tab.url, re))
    // Chrome does not promise a useful query order. Window/tab position makes
    // "the first tab" stable and easy to reason about.
    .sort((a, b) =>
      (a.windowId - b.windowId) || (a.index - b.index) || (a.id - b.id));
}

async function probeOnce(tabId) {
  try {
    const reply = await withTimeout(
      chrome.tabs.sendMessage(tabId, { type: 'reviewer:probe' }),
      PROBE_TIMEOUT_MS,
      'reviewer tab did not answer'
    );
    return reply?.ready ? reply : null;
  } catch {
    return null;
  }
}

async function probeTab(tab) {
  let reply = await probeOnce(tab.id);
  if (!reply) {
    // Do not let a secondary win merely because its content script happened to
    // initialize a few milliseconds before the preferred tab's script. Chrome
    // can report `complete` just before a document-idle script registers.
    await delay(PROBE_LOADING_RETRY_MS);
    reply = await probeOnce(tab.id);
  }
  return reply;
}

function roleFor(tabId, state) {
  return {
    active: tabId === state.tabId,
    tabId,
    primaryTabId: state.tabId,
    generation: state.generation
  };
}

async function sendRole(tab, state) {
  try {
    await chrome.tabs.sendMessage(tab.id, {
      type: 'reviewer:set-role',
      ...roleFor(tab.id, state)
    });
  } catch {
    // A loading/discarded tab will ask for its role when its script starts.
  }
}

async function broadcastRoles(tabs, state) {
  await Promise.allSettled(tabs.map((tab) => sendRole(tab, state)));
}

let selectionTail = Promise.resolve();

function selectPrimary(options = {}) {
  const run = selectionTail.then(
    () => selectPrimaryUnlocked(options),
    () => selectPrimaryUnlocked(options)
  );
  selectionTail = run.catch(() => undefined);
  return run;
}

async function selectPrimaryUnlocked({ excludeTabId = null, keepCurrentOnNoMatch = false } = {}) {
  const [tabs, current, paused] =
    await Promise.all([reviewerTabs(), readCoordinator(), pausedUntil()]);
  const currentTab = tabs.find((tab) => tab.id === current.tabId) || null;
  const candidates = [];
  // While paused no tab may be primary, so every content script stays passive.
  if (!paused) {
    if (currentTab && currentTab.id !== excludeTabId) candidates.push(currentTab);
    for (const tab of tabs) {
      if (tab.id !== currentTab?.id && tab.id !== excludeTabId) candidates.push(tab);
    }
  }
  if (paused) keepCurrentOnNoMatch = false;

  let chosen = null;
  let probe = null;
  for (const tab of candidates) {
    const reply = await probeTab(tab);
    if (reply) {
      chosen = tab;
      probe = reply;
      break;
    }
    if (tab.id === currentTab?.id && tab.status === 'loading' &&
        Date.now() - current.selectedAt < LOADING_LEASE_MS) {
      // Page-to-page navigation temporarily destroys the primary content
      // script. Keep its lease while Chrome is loading it instead of promoting
      // a backup in the middle of every sweep. A genuinely hung load loses the
      // lease after a bounded grace period so a reachable fallback can take it.
      chosen = tab;
      break;
    }
  }

  if (!chosen && keepCurrentOnNoMatch && currentTab) chosen = currentTab;

  let state = current;
  const nextId = chosen?.id ?? null;
  const changed = nextId !== current.tabId;
  if (changed) {
    state = await writeCoordinator({
      tabId: nextId,
      generation: current.generation + 1,
      selectedAt: nextId == null ? 0 : Date.now()
    });
    const stored = await chrome.storage.local.get(null);
    const inactiveSweepKeys = Object.keys(stored).filter((key) =>
      (key === 'sweep' || key.startsWith(SWEEP_PREFIX)) &&
      key !== `${SWEEP_PREFIX}${nextId}`
    );
    if (inactiveSweepKeys.length) await chrome.storage.local.remove(inactiveSweepKeys);
    await broadcastRoles(tabs, state);
    if (current.tabId != null && nextId != null) {
      await chrome.storage.local.set({
        lastTabFailover: Date.now(),
        lastTabFailoverFrom: current.tabId,
        lastTabFailoverTo: nextId
      });
    }
  } else if (chosen && (!probe?.active || probe.generation !== state.generation)) {
    await sendRole(chosen, state);
  }

  return { tabs, tab: chosen, probe, state, changed };
}

async function authorizedRole(sender, generation) {
  const tabId = sender?.tab?.id;
  const state = await readCoordinator();
  return {
    tabId,
    state,
    authorized: Number.isInteger(tabId) && tabId === state.tabId &&
      (generation === undefined || generation === state.generation)
  };
}

async function handleRoleRequest(sender) {
  const tabId = sender?.tab?.id;
  if (!Number.isInteger(tabId)) {
    return { ok: false, error: 'Role requests must come from a reviewer tab.' };
  }
  const selected = await selectPrimary();
  return { ok: true, ...roleFor(tabId, selected.state) };
}

async function handlePageReport(msg, sender) {
  const before = await authorizedRole(sender, msg.generation);
  if (!before.authorized) {
    return {
      ok: false,
      reason: before.tabId === before.state.tabId ? 'stale-generation' : 'not-primary',
      ...roleFor(before.tabId, before.state)
    };
  }
  if (!Array.isArray(msg.items) || !msg.items.length) {
    return {
      ok: false,
      retryable: false,
      error: 'No items were supplied.',
      ...roleFor(before.tabId, before.state)
    };
  }

  const result = await enqueuePost(msg.items, msg.page);
  const after = await authorizedRole(sender, msg.generation);
  return {
    ...result,
    ...roleFor(after.tabId, after.state),
    // A request can finish after a restart/failover. The server safely dedupes
    // it, but the old content script must not mark its page visited.
    ok: Boolean(result.ok && after.authorized),
    reason: after.authorized ? result.reason : 'stale-generation'
  };
}

async function navigateToPageOne(tabId) {
  const tab = await chrome.tabs.get(tabId);
  const url = new URL(tab.url);
  if (url.searchParams.has('page')) {
    url.searchParams.delete('page');
    await chrome.tabs.update(tabId, { url: url.toString() });
  } else {
    await chrome.tabs.reload(tabId, { bypassCache: false });
  }
}

async function restartPrimary(reason = 'manual') {
  if (await pausedUntil()) {
    if (reason !== 'manual') {
      return { ok: false, paused: true, error: 'Paused after a Walmart bot check.' };
    }
    // An explicit Restart sweep is the user saying they solved the check.
    await clearBotPause();
  }
  const selected = await selectPrimary();
  // A manual restart is also the repair path after an unpacked-extension
  // reload, when the tab exists but has no listening content script yet.
  const tab = selected.tab || selected.tabs[0] || null;
  if (!tab) {
    return { ok: false, error: 'No reachable reviewer tab is open.', ...roleFor(null, selected.state) };
  }

  const state = await writeCoordinator({
    tabId: tab.id,
    generation: selected.state.generation + 1,
    selectedAt: Date.now()
  });
  // Revoke the old document's lease before clearing progress. Otherwise its
  // pending navigation can race the requested trip back to page 1.
  try {
    await chrome.tabs.sendMessage(tab.id, {
      type: 'reviewer:set-role',
      ...roleFor(tab.id, state),
      active: false
    });
  } catch {
    // No listener is exactly the state this repair path is meant to fix.
  }
  await Promise.allSettled(
    selected.tabs.filter((candidate) => candidate.id !== tab.id)
      .map((candidate) => sendRole(candidate, state))
  );
  await chrome.storage.local.remove([
    `${SWEEP_PREFIX}${tab.id}`,
    'sweep',
    'lastSweepPages',
    'lastSweepDone'
  ]);

  try {
    await navigateToPageOne(tab.id);
    return { ok: true, reason, ...roleFor(tab.id, state) };
  } catch (err) {
    const message = `Could not restart reviewer tab ${tab.id}: ${err.message || err}`;
    await chrome.storage.local.set({ lastError: message, lastErrorAt: Date.now() });
    await sendRole(tab, state);
    return { ok: false, error: message, ...roleFor(tab.id, state) };
  }
}

async function handleUnusable(msg, sender) {
  const before = await authorizedRole(sender, msg.generation);
  if (!before.authorized) return { ok: false, ...roleFor(before.tabId, before.state) };

  const reason = String(msg.reason || 'Reviewer page did not become usable').slice(0, 300);
  await chrome.storage.local.set({
    lastError: `${reason}. Trying another reviewer tab if one is available.`,
    lastErrorAt: Date.now()
  });

  const selected = await selectPrimary({
    excludeTabId: before.tabId,
    keepCurrentOnNoMatch: true
  });
  if (selected.tab && selected.tab.id !== before.tabId) {
    const restarted = await restartPrimary('failover');
    return {
      ok: false,
      recover: 'failed-over',
      failedOverTo: restarted.primaryTabId,
      ...roleFor(before.tabId, await readCoordinator())
    };
  }
  return {
    ok: false,
    recover: 'reload',
    retryAfterMs: 5_000,
    ...roleFor(before.tabId, before.state)
  };
}

async function coordinatorStatus() {
  const selected = await selectPrimary();
  let sweep = null;
  if (selected.state.tabId != null) {
    const key = `${SWEEP_PREFIX}${selected.state.tabId}`;
    const stored = await chrome.storage.local.get([key]);
    sweep = stored[key] || null;
  }
  return {
    ok: true,
    primaryTabId: selected.state.tabId,
    generation: selected.state.generation,
    reachable: Boolean(selected.probe),
    tabCount: selected.tabs.length,
    tabs: selected.tabs.map((tab) => ({
      id: tab.id,
      windowId: tab.windowId,
      index: tab.index,
      active: tab.id === selected.state.tabId
    })),
    sweep,
    pausedUntil: await pausedUntil(),
    botCheck: await readBotCheck()
  };
}

function respondAsync(task, sendResponse) {
  Promise.resolve(task)
    .then((value) => sendResponse(value))
    .catch(async (err) => {
      const message = err?.message || String(err);
      console.error('Reviewer Item Relay: message handler failed', err);
      try {
        await chrome.storage.local.set({ lastError: message, lastErrorAt: Date.now() });
      } finally {
        sendResponse({ ok: false, error: message });
      }
    });
  return true;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === 'reviewer:role') {
    return respondAsync(handleRoleRequest(sender), sendResponse);
  }
  if (msg?.type === 'reviewer:page') {
    return respondAsync(handlePageReport(msg, sender), sendResponse);
  }
  if (msg?.type === 'reviewer:unusable') {
    return respondAsync(handleUnusable(msg, sender), sendResponse);
  }
  if (msg?.type === 'reviewer:status') {
    return respondAsync(coordinatorStatus(), sendResponse);
  }
  if (msg?.type === 'reviewer:restart') {
    return respondAsync(restartPrimary('manual'), sendResponse);
  }

  // Compatibility with an already-loaded 2.0.0 content script while the
  // unpacked extension is being reloaded. It is still restricted to the primary.
  if (msg?.type === 'items') {
    return respondAsync(
      readCoordinator().then((state) =>
        handlePageReport({ ...msg, generation: state.generation }, sender)),
      sendResponse
    );
  }
  if (msg?.type === 'whoami') {
    sendResponse({ tabId: sender?.tab?.id ?? null });
    return undefined;
  }
  return undefined;
});

// --- self-refresh -----------------------------------------------------------

async function scheduleRefresh(busy = false) {
  const until = await pausedUntil();
  if (until) {
    // Fire once when the pause ends, whatever the refresh interval says.
    await chrome.alarms.create(REFRESH_ALARM, { when: until });
    return;
  }
  const { refreshMinutes } = await config();
  if (!refreshMinutes || refreshMinutes <= 0) {
    await chrome.alarms.clear(REFRESH_ALARM);
    return;
  }
  const jittered = refreshMinutes * (1 + (Math.random() * 2 - 1) * JITTER);
  const delayMinutes = busy ? BUSY_RETRY_MIN : Math.max(MIN_DELAY_MIN, jittered);
  await chrome.alarms.create(REFRESH_ALARM, { delayInMinutes: delayMinutes });
}

let refreshInFlight = false;
async function refreshTick() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    if (await pausedUntil()) {
      // No reloads, not even the repair reload below, while Walmart has asked
      // this traffic to stop.
      await scheduleRefresh();
      return;
    }
    const selected = await selectPrimary();
    if (!selected.tab) {
      // If no content script answered, repair the deterministic first candidate.
      if (selected.tabs[0]) {
        try {
          await chrome.tabs.reload(selected.tabs[0].id, { bypassCache: false });
        } catch (err) {
          console.warn('Reviewer Item Relay: could not reload reviewer tab', err);
        }
      }
      await scheduleRefresh(true);
      return;
    }

    const state = selected.probe || await probeTab(selected.tab);
    if (!state) {
      await scheduleRefresh(true);
      return;
    }
    if (!state.active || state.generation !== selected.state.generation) {
      await sendRole(selected.tab, selected.state);
      await scheduleRefresh(true);
      return;
    }

    if (state.sweeping) {
      const stale = state.lastActivityAt && Date.now() - state.lastActivityAt > STALE_SWEEP_MS;
      if (stale && state.phase !== 'ingest-retry' && state.phase !== 'waiting-user') {
        try {
          await chrome.tabs.sendMessage(selected.tab.id, {
            type: 'reviewer:recover', generation: selected.state.generation
          });
        } catch {
          // The next alarm will probe/fail over if the tab remains unreachable.
        }
      }
      await scheduleRefresh(true);
      return;
    }
    if (state.busy) {
      await scheduleRefresh(true);
      return;
    }

    const restarted = await restartPrimary('scheduled');
    await scheduleRefresh(!restarted.ok);
  } finally {
    refreshInFlight = false;
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === REFRESH_ALARM) {
    refreshTick().catch((err) => {
      console.error('Reviewer Item Relay: refresh failed', err);
      // Named alarms are one-shot. Always arm a recovery attempt after an
      // unexpected coordinator/API error so auto-refresh cannot silently die.
      scheduleRefresh(true).catch((retryErr) =>
        console.error('Reviewer Item Relay: could not reschedule refresh', retryErr));
    });
  }
  if (alarm.name === BADGE_ALARM) {
    chrome.action.setBadgeText({ text: '' }).catch(() => undefined);
  }
});

chrome.runtime.onStartup.addListener(() => {
  scheduleRefresh().catch(console.error);
  selectPrimary().catch(console.error);
});

chrome.runtime.onInstalled.addListener(async () => {
  await migrateFromSync();
  await scheduleRefresh();
  // Reloading an unpacked extension invalidates content scripts already in
  // open tabs. restartPrimary deliberately falls back to the first eligible
  // tab and reloads it even when no content script can answer yet.
  await restartPrimary('installed');
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if ('refreshMinutes' in changes) scheduleRefresh().catch(console.error);
  if ('pathPattern' in changes) selectPrimary().catch(console.error);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.local.remove(`${SWEEP_PREFIX}${tabId}`).catch(() => undefined);
  readCoordinator().then(async (state) => {
    if (state.tabId !== tabId) return;
    await writeCoordinator({ tabId: null, generation: state.generation + 1, selectedAt: 0 });
    await selectPrimary();
  }).catch(console.error);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!changeInfo.url && !changeInfo.status) return;
  // Any Walmart tab landing on the bot check pauses everything: the check is
  // about this browser session, not about one tab.
  if (isBotCheckUrl(changeInfo.url || tab?.url)) {
    handleBotCheck(tabId).catch(console.error);
    return;
  }
  Promise.all([readCoordinator(), pathRegex()]).then(async ([state, re]) => {
    if (state.tabId !== tabId) return;
    if (changeInfo.url && !eligibleUrl(tab.url, re)) {
      await writeCoordinator({ tabId: null, generation: state.generation + 1, selectedAt: 0 });
      await selectPrimary();
      return;
    }
    if (changeInfo.status === 'loading') {
      // selectedAt doubles as the start of the current navigation lease. It
      // lets selectPrimary distinguish an ordinary page transition from a tab
      // that has been stuck loading long enough to fail over safely.
      await writeCoordinator({ ...state, selectedAt: Date.now() });
    }
  }).catch(console.error);
});

// 1.0.x stored settings in sync storage. Only the refresh interval is safe to
// migrate; an old endpoint may refer to the retired cloud deployment.
async function migrateFromSync() {
  const synced = await chrome.storage.sync.get(['endpoint', 'token', 'refreshMinutes']);
  await chrome.storage.sync.remove(['endpoint', 'token', 'refreshMinutes']);
  if (!Object.keys(synced).length) return;
  const existing = await chrome.storage.local.get(['refreshMinutes']);
  if (synced.refreshMinutes && !existing.refreshMinutes) {
    await chrome.storage.local.set({ refreshMinutes: synced.refreshMinutes });
  }
}
