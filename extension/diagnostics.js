// Worker-owned, serialized, bounded local history. Only these fields may leave
// the extension in an export; never log raw messages, URLs, DOM text, or errors.
const DIAGNOSTIC_EVENTS = new Set([
  'page-loaded', 'page-delivered', 'navigation-native', 'navigation-fallback',
  'navigation-interrupted', 'pacing', 'challenge', 'verification-resumed',
  'sweep-start', 'sweep-complete', 'ingest-retry', 'empty-retry', 'recovery-reload',
  'live-update', 'navigation-ready', 'pager-inspection', 'tab-opened',
  'scan-restart-native', 'scan-restart-load', 'empty-boundary-reused',
  'results-page-ready', 'navigation-retry'
]);
const DIAGNOSTIC_REASONS = new Set([
  'redirect', 'dom-marker', 'text', 'probe', 'missing-control', 'native-stalled', 'native-backoff',
  'unusable', 'unreachable', 'scheduled', 'manual', 'installed', 'failover',
  'empty-results', 'blank-timeout'
]);
const DIAGNOSTIC_NUMBERS = [
  'tabId', 'generation', 'documentId', 'page', 'targetPage', 'totalPages', 'delayMs', 'count', 'pages',
  'durationMs', 'intervalMs', 'elapsedMs', 'attempt', 'items', 'new', 'duplicates', 'matched', 'notified', 'failed', 'pending',
  'recognizedPagerCount', 'exactHrefCount', 'visibleExactHrefCount', 'queryMismatchCount',
  'targetLabelButtonCount', 'nextLabelButtonCount', 'scopedButtonCount', 'roleButtonCount',
  'pagerElementCount', 'pagerAnchorCount', 'pagerButtonCount', 'pagerInputCount',
  'pagerNumericTextCount', 'pagerTargetTextCount', 'pagerTargetAriaCount',
  'pagerHiddenCount', 'pagerMarkerCount', 'pagerPlaceholderHrefCount',
  'pagerTargetPlaceholderCount', 'pagerPageHrefCount', 'pagerOtherHrefCount'
];
let diagnosticTail = Promise.resolve();

// Cumulative counters are independent of the seven-day/300-event trace. The
// first observation starts a new measurement; retained history is never a
// complete enough baseline to reconstruct a rate. Only numeric page identities
// are retained, with a bounded recent-tab set for service-worker restarts.
const SCAN_METRIC_KEY = 'reviewerScanMetrics';
const SCAN_METRIC_MAX = 1e12;
const SCAN_METRIC_COUNTERS = [
  'initialPages', 'successfulTransitions', 'challengeEpisodes', 'nativeAttempts',
  'urlFallbacks', 'nativeBackoffFallbacks', 'nativeStalledFallbacks', 'nativeRetries'
];
const SCAN_METRIC_STATE_NUMBERS = [
  'measurementStartedAt', 'completedVerificationDowntimeMs', 'verificationActiveSince'
];

function scanMetricInteger(value, maximum = 1e15) {
  return Number.isSafeInteger(value) && value >= 0 && value <= maximum;
}

function scanMetricState(value, now) {
  const fresh = () => ({ schemaVersion: 1, measurementStartedAt: now,
    ...Object.fromEntries(SCAN_METRIC_COUNTERS.map(field => [field, 0])),
    completedVerificationDowntimeMs: 0, verificationActiveSince: 0,
    verificationBaselineObserved: false, tabs: [] });
  if (!value || value.schemaVersion !== 1 ||
      !SCAN_METRIC_COUNTERS.every(field => scanMetricInteger(value[field], SCAN_METRIC_MAX)) ||
      !SCAN_METRIC_STATE_NUMBERS.every(field => scanMetricInteger(value[field])) ||
      typeof value.verificationBaselineObserved !== 'boolean' ||
      value.measurementStartedAt > now || value.verificationActiveSince > now ||
      (value.verificationActiveSince > 0 && value.verificationActiveSince < value.measurementStartedAt) ||
      value.completedVerificationDowntimeMs > now - value.measurementStartedAt ||
      value.nativeBackoffFallbacks + value.nativeStalledFallbacks > value.urlFallbacks ||
      !Array.isArray(value.tabs) || value.tabs.length > 64 ||
      !value.tabs.every(tab => tab && ['tabId', 'documentId', 'page', 'lastAt']
        .every(field => scanMetricInteger(tab[field])) && tab.documentId > 0 && tab.page > 0) ||
      new Set(value.tabs.map(tab => tab.tabId)).size !== value.tabs.length) return fresh();
  // Copy only the fixed internal schema, even if storage was edited externally.
  return { schemaVersion: 1,
    verificationBaselineObserved: value.verificationBaselineObserved,
    ...Object.fromEntries([...SCAN_METRIC_COUNTERS, ...SCAN_METRIC_STATE_NUMBERS]
      .map(field => [field, value[field]])),
    tabs: value.tabs.map(tab => Object.fromEntries(['tabId', 'documentId', 'page', 'lastAt']
      .map(field => [field, tab[field]]))) };
}

function updateScanMetrics(state, entry) {
  const increment = field => { state[field] = Math.min(SCAN_METRIC_MAX, state[field] + 1); };
  if (entry.event === 'results-page-ready' &&
      ['tabId', 'documentId', 'page'].every(field => scanMetricInteger(entry[field])) &&
      entry.documentId > 0 && entry.page > 0 && entry.documentId <= entry.at) {
    const previous = state.tabs.find(tab => tab.tabId === entry.tabId);
    // A late message from a replaced document cannot establish another page.
    if (previous && entry.documentId < previous.documentId) return;
    if (!previous) increment('initialPages');
    else if (previous.documentId !== entry.documentId || previous.page !== entry.page) {
      increment('successfulTransitions');
    }
    state.tabs = state.tabs.filter(tab => tab.tabId !== entry.tabId);
    state.tabs.push({ tabId: entry.tabId, documentId: entry.documentId,
      page: entry.page, lastAt: entry.at });
    state.tabs = state.tabs.slice(-64);
  } else if (entry.event === 'challenge' && !state.verificationActiveSince) {
    increment('challengeEpisodes');
    state.verificationActiveSince = entry.at;
    state.verificationBaselineObserved = true;
  } else if (entry.event === 'verification-resumed') {
    if (state.verificationActiveSince) {
      state.completedVerificationDowntimeMs = Math.min(1e15,
        state.completedVerificationDowntimeMs + Math.max(0, entry.at - state.verificationActiveSince));
      state.verificationActiveSince = 0;
    }
    state.verificationBaselineObserved = true;
  } else if (entry.event === 'navigation-native') increment('nativeAttempts');
  else if (entry.event === 'navigation-retry') increment('nativeRetries');
  else if (entry.event === 'navigation-fallback') {
    increment('urlFallbacks');
    if (entry.reason === 'native-backoff') increment('nativeBackoffFallbacks');
    if (entry.reason === 'native-stalled') increment('nativeStalledFallbacks');
  }
}

function scanMetricReport(state, now) {
  return {
    ...Object.fromEntries([...SCAN_METRIC_STATE_NUMBERS, ...SCAN_METRIC_COUNTERS]
      .map(field => [field, state[field]])),
    verificationDowntimeMs: Math.min(1e15, state.completedVerificationDowntimeMs +
      (state.verificationActiveSince ? Math.max(0, now - state.verificationActiveSince) : 0)),
    episodesPer100Transitions: state.successfulTransitions > 0
      ? state.challengeEpisodes * 100 / state.successfulTransitions : null
  };
}

function cleanDiagnostic(event, details, at = Date.now()) {
  if (!DIAGNOSTIC_EVENTS.has(event)) return null;
  const entry = { at, event };
  for (const field of DIAGNOSTIC_NUMBERS) {
    if (Number.isFinite(details?.[field]) && details[field] >= 0 && details[field] <= 1e15) {
      entry[field] = Math.round(details[field]);
    }
  }
  if (DIAGNOSTIC_REASONS.has(details?.reason)) entry.reason = details.reason;
  if (['none', 'sweep', 'high-value'].includes(details?.mode)) entry.mode = details.mode;
  return entry;
}

function diagnosticHistory(value) {
  if (!Array.isArray(value)) return [];
  const cutoff = Date.now() - 7 * 86400_000;
  return value.filter(entry => entry && Number.isFinite(entry.at) &&
    entry.at >= cutoff && entry.at <= Date.now())
    .map(entry => cleanDiagnostic(entry.event, entry, entry.at)).filter(Boolean).slice(-300);
}

function recordDiagnostic(event, details = {}) {
  const entry = cleanDiagnostic(event, details);
  if (!entry) return Promise.resolve();
  const run = diagnosticTail.then(async () => {
    const { reviewerDiagnostics, [SCAN_METRIC_KEY]: storedMetrics } =
      await chrome.storage.local.get(['reviewerDiagnostics', SCAN_METRIC_KEY]);
    const history = diagnosticHistory(reviewerDiagnostics);
    history.push(entry);
    // An earlier queued export may initialize the baseline while this event is
    // waiting on storage. Validate against processing time, then count the
    // event inside that established window without rewriting its trace time.
    const metrics = scanMetricState(storedMetrics, Date.now());
    updateScanMetrics(metrics, { ...entry, at: Math.max(metrics.measurementStartedAt, entry.at) });
    await chrome.storage.local.set({ reviewerDiagnostics: history.slice(-300), [SCAN_METRIC_KEY]: metrics });
  });
  diagnosticTail = run.catch(() => {
    // Logging must not interrupt the scanner or disclose a storage error body.
    console.warn('Reviewer Item Relay: diagnostic storage unavailable');
  });
  return diagnosticTail;
}

async function diagnosticReport() {
  const settings = await config();
  const check = await readBotCheck();
  // Serialize initialization with event writes so an export cannot overwrite a
  // newly counted transition or a challenge arriving during storage reads.
  const snapshot = diagnosticTail.then(async () => {
    const { reviewerDiagnostics, [SCAN_METRIC_KEY]: storedMetrics } =
      await chrome.storage.local.get(['reviewerDiagnostics', SCAN_METRIC_KEY]);
    const now = Date.now();
    const metrics = scanMetricState(storedMetrics, now);
    if (!metrics.verificationBaselineObserved) {
      // Upgrading during an existing outage observes one partial episode. Never
      // import its historical count or downtime from before this measurement.
      if (Number.isFinite(check.pausedUntil) && check.pausedUntil > 0) {
        metrics.challengeEpisodes += 1;
        metrics.verificationActiveSince = Math.min(now, Math.max(metrics.measurementStartedAt,
          scanMetricInteger(check.lastAt) ? check.lastAt : now));
      }
      metrics.verificationBaselineObserved = true;
    }
    await chrome.storage.local.set({ [SCAN_METRIC_KEY]: metrics });
    return { now, events: diagnosticHistory(reviewerDiagnostics), metrics: scanMetricReport(metrics, now) };
  });
  diagnosticTail = snapshot.catch(() => undefined);
  const { now, events, metrics } = await snapshot;
  return {
    schemaVersion: 1,
    extensionVersion: chrome.runtime.getManifest?.().version || 'unknown',
    exportedAt: now,
    settings: {
      refreshMinutes: Number(settings.refreshMinutes) || 0,
      pageDelaySeconds: Number(settings.pageDelaySeconds) || 15,
      sweepMode: normalizeSweepMode(settings.sweepMode)
    },
    verification: { count: check.count, lastAt: check.lastAt, pausedUntil: check.pausedUntil },
    events,
    metrics
  };
}

let diagnosticSyncTail = Promise.resolve();
const DIAGNOSTIC_MIRROR_KEY = 'reviewerDiagnosticMirror';

// Mirror safe snapshots to the already configured local notifier. This never
// requests anything from Walmart. The heartbeat retries failed writes; a
// five-minute refresh also repairs a server restart even when events are quiet.
function syncBrowserDiagnostics() {
  const run = diagnosticSyncTail.then(async () => {
    let failure = 'Local diagnostic sync failed; retrying on the next heartbeat.';
    let timer;
    try {
      const { endpoint, token } = await config();
      const destination = new URL('/browser-diagnostics', checkedEndpoint(endpoint)).toString();
      const report = await diagnosticReport();
      const signature = destination + JSON.stringify({ ...report, exportedAt: 0 });
      const cached = (await chrome.storage.session.get([DIAGNOSTIC_MIRROR_KEY]))[DIAGNOSTIC_MIRROR_KEY];
      if (signature === cached?.signature && Date.now() - cached.at < 300_000) {
        return true;
      }
      const headers = { 'Content-Type': 'application/json' };
      if (token) headers['X-Ingest-Token'] = token;
      const controller = new AbortController();
      timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
      const response = await fetch(destination, { method: 'POST', headers,
        body: JSON.stringify(report), signal: controller.signal });
      if (!response.ok) {
        failure = response.status === 404
          ? 'Rebuild the local notifier to enable diagnostic mirroring (HTTP 404).'
          : `Local diagnostic sync returned HTTP ${response.status}; retrying next heartbeat.`;
        throw new Error('diagnostic sync rejected');
      }
      await chrome.storage.local.set({ diagnosticsSyncedAt: Date.now(), diagnosticsSyncError: '' });
      await chrome.storage.session.set({ [DIAGNOSTIC_MIRROR_KEY]: { signature, at: Date.now() } });
      return true;
    } catch {
      await chrome.storage.local.set({ diagnosticsSyncError: failure });
      return false;
    } finally {
      clearTimeout(timer);
    }
  });
  diagnosticSyncTail = run.catch(() => false);
  return diagnosticSyncTail;
}
