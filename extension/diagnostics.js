// Worker-owned, serialized, bounded local history. Only these fields may leave
// the extension in an export; never log raw messages, URLs, DOM text, or errors.
// Card evidence contains only public item IDs and fixed, typed observations.
const DIAGNOSTIC_EVENTS = new Set([
  'page-loaded', 'page-delivered', 'navigation-native', 'navigation-fallback',
  'navigation-interrupted', 'pacing', 'challenge', 'verification-resumed', 'traffic-wait', 'traffic-resumed',
  'sweep-start', 'sweep-complete', 'ingest-retry', 'empty-retry', 'recovery-reload',
  'live-update', 'navigation-ready', 'pager-inspection', 'tab-opened',
  'scan-restart-native', 'scan-restart-load', 'empty-boundary-reused',
  'results-page-ready', 'navigation-retry', 'card-extraction', 'coverage-unresolved',
  'auxiliary-target-start', 'auxiliary-page-delivered', 'auxiliary-target-skipped', 'auxiliary-target-retry', 'auxiliary-error',
  'navigation-outcome', 'ingest-stage', 'auxiliary-target-complete', 'browser-error', 'sync-state', 'verification-state',
  'focus-granted', 'focus-denied', 'focus-released', 'pager-clamped', 'page-repeat', 'page-state'
]);
const DIAGNOSTIC_REASONS = new Set([
  'redirect', 'dom-marker', 'text', 'probe', 'missing-control', 'native-stalled', 'native-backoff',
  'unusable', 'unreachable', 'scheduled', 'manual', 'installed', 'failover',
  'empty-results', 'blank-timeout', 'waiting-turn', 'busy', 'user-active', 'expired',
  'unavailable', 'disabled', 'paused', 'revoked', 'not-authorized', 'manual-tab-active',
  'activation-unavailable', 'not-scanner', 'selection-changed'
]);
const DIAGNOSTIC_NUMBERS = [
  'tabId', 'generation', 'documentId', 'page', 'targetPage', 'totalPages', 'delayMs', 'count', 'pages',
  'round', 'targetIndex', 'responseStatus', 'queueMs', 'batchIndex', 'batchCount', 'resultAt', 'episodeAt',
  'durationMs', 'intervalMs', 'elapsedMs', 'attempt', 'items', 'new', 'duplicates', 'matched', 'notified', 'failed', 'pending',
  'recognizedPagerCount', 'exactHrefCount', 'visibleExactHrefCount', 'queryMismatchCount',
  'targetLabelButtonCount', 'nextLabelButtonCount', 'scopedButtonCount', 'roleButtonCount',
  'pagerElementCount', 'pagerAnchorCount', 'pagerButtonCount', 'pagerInputCount',
  'pagerNumericTextCount', 'pagerTargetTextCount', 'pagerTargetAriaCount',
  'pagerHiddenCount', 'pagerMarkerCount', 'pagerPlaceholderHrefCount',
  'pagerTargetPlaceholderCount', 'pagerPageHrefCount', 'pagerOtherHrefCount',
  'acceptedCount', 'rejectedCount', 'variantCount', 'outOfStockCount', 'missingValueCount'
];
const DIAGNOSTIC_LANES = new Set(['home', 'departments', 'keywords', 'brands', 'priority']);
const CARD_DECISIONS = new Set([
  'accepted', 'out-of-stock', 'variant', 'card-unavailable', 'not-free', 'missing-title'
]);
const CARD_COUNTS = ['acceptedCount', 'rejectedCount', 'variantCount', 'outOfStockCount', 'missingValueCount'];
const CLASSIFIED_FAILURES = new Set(['controls-unavailable', 'discovery-save-failed',
  'content-unresponsive', 'progress-timeout', 'selection-render-timeout',
  'selection-document-timeout', 'selection-bind-failed', 'selection-unconfirmed']);
const PAGE_STATE_STAGES = new Set(['empty-observed', 'empty-confirmation', 'fallback',
  'unusable', 'zero-page-complete', 'recovered']);
const PAGE_STATE_LABELS = {
  readyState: ['loading', 'interactive', 'complete', 'unknown'],
  visibility: ['visible', 'hidden', 'prerender', 'unknown'],
  route: ['reviewer', 'auth', 'other'],
  navigationType: ['navigate', 'reload', 'back_forward', 'unknown']
};
const PAGE_STATE_FLAGS = ['reviewerShell', 'resultsLabel', 'noResults', 'loading',
  'error', 'signIn', 'pagerPresent'];
const PAGE_STATE_COUNTS = { productLinks: 10000, offerLines: 10000, renderedPage: 100000,
  failedRequests: 10000, readingFailures: 10000, readingWorkMs: 1e15,
  queryIndex: 10000, queryCount: 10000 };

function cleanPageState(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const clean = {};
  for (const [field, choices] of Object.entries(PAGE_STATE_LABELS)) {
    if (!Object.hasOwn(value, field) || !choices.includes(value[field])) return null;
    clean[field] = value[field];
  }
  for (const field of PAGE_STATE_FLAGS) {
    if (!Object.hasOwn(value, field) || typeof value[field] !== 'boolean') return null;
    clean[field] = value[field];
  }
  for (const [field, maximum] of Object.entries(PAGE_STATE_COUNTS)) {
    if (!Object.hasOwn(value, field) || !Number.isSafeInteger(value[field]) ||
        value[field] < 0 || value[field] > maximum) return null;
    clean[field] = value[field];
  }
  if (!Object.hasOwn(value, 'responseStatus') || !Number.isSafeInteger(value.responseStatus) ||
      value.responseStatus !== 0 && (value.responseStatus < 100 || value.responseStatus > 599)) return null;
  clean.responseStatus = value.responseStatus;
  return clean;
}

function cleanCardSample(value) {
  if (!value || typeof value.itemId !== 'string' || !/^ip-\d{6,20}$/.test(value.itemId) ||
      !CARD_DECISIONS.has(value.decision) ||
      typeof value.cardFound !== 'boolean' ||
      ![value.free, value.outOfStock].every(flag => flag === null || typeof flag === 'boolean') ||
      !(value.valueCents === null || Number.isSafeInteger(value.valueCents) &&
        value.valueCents >= 0 && value.valueCents <= 1e15)) return null;
  return { itemId: value.itemId, decision: value.decision, cardFound: value.cardFound,
    free: value.free, outOfStock: value.outOfStock, valueCents: value.valueCents };
}
let diagnosticTail = Promise.resolve();
const DIAGNOSTIC_LOG_KEY = 'reviewerDiagnosticLogging';
const DIAGNOSTIC_HEX = /^[0-9a-f]{32}$/;
const DIAGNOSTIC_SYNC_ERRORS = ['none', 'timeout', 'network', 'http', 'storage', 'report'];
const DIAGNOSTIC_COUNTERS = ['lastSequence', 'recorded', 'rejected', 'storageErrors',
  'ringOverwrites', 'unacknowledgedOverwrites', 'lastRecordedAt', 'lastSyncAt', 'syncAttempts',
  'syncFailures', 'consecutiveSyncFailures', 'lastSyncStatus', 'acknowledgedSequence'];
let diagnosticLogging = null;
let diagnosticRejected = 0;
let diagnosticStorageErrors = 0;
let diagnosticPending = 0;
const DIAGNOSTIC_VERIFICATION_OUTCOMES = ['starting', 'unsupported', 'needs-attention', 'input-error', 'cancelled', 'verified'];
const DIAGNOSTIC_VERIFICATION_CODES = ['watching', 'permission-required', 'retrying-control', 'waiting-page',
  'invalid-budget', 'attempts-exhausted', 'already-attempted', 'waiting-tab-ready', 'waiting-foreground',
  'cooldown', 'unavailable', 'native-permission-required', 'native-connecting', 'connecting', 'retry-pending',
  'collector-pending', 'native-waiting-idle', 'native-unavailable', ...DIAGNOSTIC_VERIFICATION_OUTCOMES];
const DIAGNOSTIC_VERIFICATION_LABELS = {
  statusCode: DIAGNOSTIC_VERIFICATION_CODES,
  errorStage: ['inspect', 'move', 'press', 'release', 'detach'],
  errorReason: ['timeout', 'detached', 'command-failed'],
  releaseReason: ['none', 'deadline', 'page-changed', 'cancelled', 'failed', 'feedback', 'stalled', 'max-duration'],
  transport: ['native', 'debugger']
};

// Correlation only, never an authorization secret. Chrome supplies crypto; the
// numeric fallback also keeps isolated, older local harnesses operational.
function diagnosticIdentifier() {
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    return [...crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2, '0')).join('');
  }
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
}

function diagnosticLoggingState(stored) {
  if (!diagnosticLogging) {
    const valid = stored && DIAGNOSTIC_HEX.test(stored.streamId) &&
      DIAGNOSTIC_COUNTERS.every(k => Number.isSafeInteger(stored[k]) && stored[k] >= 0 && stored[k] <= 1e15) &&
      DIAGNOSTIC_SYNC_ERRORS.includes(stored.lastSyncError) &&
      stored.recorded === stored.lastSequence && stored.acknowledgedSequence <= stored.lastSequence;
    diagnosticLogging = valid ? { streamId: stored.streamId, lastSyncError: stored.lastSyncError,
      ...Object.fromEntries(DIAGNOSTIC_COUNTERS.map(k => [k, stored[k]])) } : {
      streamId: diagnosticIdentifier(), lastSyncError: 'none',
      ...Object.fromEntries(DIAGNOSTIC_COUNTERS.map(k => [k, 0])) };
  }
  diagnosticLogging.rejected += diagnosticRejected;
  diagnosticLogging.storageErrors += diagnosticStorageErrors;
  diagnosticRejected = diagnosticStorageErrors = 0;
  return diagnosticLogging;
}

function updateDiagnosticLogging(update) {
  const run = diagnosticTail.then(async () => {
    const stored = await chrome.storage.local.get([DIAGNOSTIC_LOG_KEY]);
    const state = diagnosticLoggingState(stored[DIAGNOSTIC_LOG_KEY]);
    update(state);
    await chrome.storage.local.set({ [DIAGNOSTIC_LOG_KEY]: state });
  });
  diagnosticTail = run.catch(() => { diagnosticStorageErrors += 1; });
  return diagnosticTail;
}

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
  for (const field of ['requestId', 'streamId']) {
    if (typeof details?.[field] === 'string' && DIAGNOSTIC_HEX.test(details[field])) entry[field] = details[field];
  }
  if (entry.streamId && Number.isSafeInteger(details?.eventSeq) && details.eventSeq > 0 && details.eventSeq <= 1e15) {
    entry.eventSeq = details.eventSeq;
  } else delete entry.streamId;
  if (['initial', 'late', 'live'].includes(details?.intakePhase)) entry.intakePhase = details.intakePhase;
  if (event === 'ingest-stage') {
    if (!['queued', 'batch', 'complete'].includes(details?.stage) ||
        !['waiting', 'accepted', 'accepted-stale', 'rejected', 'retry', 'timeout', 'network',
          'invalid-response', 'cancelled'].includes(details?.outcome)) return null;
    entry.stage = details.stage;
    entry.outcome = details.outcome;
  }
  if (event === 'auxiliary-target-complete') {
    if (!['items', 'empty', 'unresolved', 'duplicate'].includes(details?.outcome)) return null;
    entry.outcome = details.outcome;
  }
  if (event === 'verification-state') {
    if (!['status', ...DIAGNOSTIC_VERIFICATION_OUTCOMES].includes(details?.outcome)) return null;
    entry.outcome = details.outcome;
    for (const [field, choices] of Object.entries(DIAGNOSTIC_VERIFICATION_LABELS)) {
      if (choices.includes(details?.[field])) entry[field] = details[field];
    }
    if (details.outcome === 'status' && !entry.statusCode) return null;
    if (typeof details.cleanupConfirmed === 'boolean') entry.cleanupConfirmed = details.cleanupConfirmed;
  }
  if (['browser-error', 'sync-state'].includes(event)) {
    if (!['message', 'heartbeat', 'initialization', 'refresh', 'auxiliary', 'diagnostics',
      'settings', 'commands', 'status', 'coordinator', 'verification'].includes(details?.component) ||
        !['error', 'recovered'].includes(details?.outcome)) return null;
    entry.component = details.component;
    entry.outcome = details.outcome;
  }
  if (event === 'navigation-outcome') {
    if (!['ready', 'interrupted', 'timeout', 'http-error'].includes(details?.outcome)) return null;
    entry.outcome = details.outcome;
  }
  if (details?.responseStatus !== undefined && (!Number.isInteger(details.responseStatus) ||
      details.responseStatus !== 0 && (details.responseStatus < 100 || details.responseStatus > 599))) return null;
  if (event.startsWith('focus-')) {
    if (!['pager', 'facet-controls', 'page-scroll', 'page-navigation'].includes(details?.operation)) return null;
    entry.operation = details.operation;
  }
  for (const field of DIAGNOSTIC_NUMBERS) {
    if (event !== 'card-extraction' && CARD_COUNTS.includes(field)) continue;
    if (Number.isFinite(details?.[field]) && details[field] >= 0 && details[field] <= 1e15) {
      entry[field] = Math.round(details[field]);
    }
  }
  if (DIAGNOSTIC_REASONS.has(details?.reason)) entry.reason = details.reason;
  if (['none', 'sweep', 'high-value'].includes(details?.mode)) entry.mode = details.mode;
  if (DIAGNOSTIC_LANES.has(details?.lane)) entry.lane = details.lane;
  if (event.startsWith('auxiliary-') && !entry.lane) return null;
  if (event === 'page-state') {
    if (!entry.lane || !PAGE_STATE_STAGES.has(details?.stage)) return null;
    const state = cleanPageState(details?.state);
    if (!state) return null;
    entry.stage = details.stage;
    entry.state = state;
  }
  if (['auxiliary-error', 'auxiliary-target-skipped', 'auxiliary-target-retry'].includes(event)) {
    // The classifier maps arbitrary error text to fixed labels. Loading older
    // persisted events must preserve that label without retaining the message.
    if (typeof scannerFailureCode === 'function') {
      const code = scannerFailureCode(details?.reason);
      const stored = details?.failureCode;
      entry.failureCode = typeof stored === 'string' && stored !== 'none' &&
        (scannerFailureCode(stored) === stored || CLASSIFIED_FAILURES.has(stored))
        ? stored : code !== 'none' ? code : 'unexpected-error';
    } else entry.failureCode = 'unexpected-error';
  }
  if (event === 'card-extraction') {
    if (!entry.lane || !CARD_COUNTS.every(field => Number.isSafeInteger(details?.[field]) &&
        details[field] >= 0 && details[field] <= 1e15)) return null;
    entry.samples = Array.isArray(details?.samples)
      ? details.samples.slice(0, 12).map(cleanCardSample).filter(Boolean) : [];
  }
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
  if (!entry || diagnosticPending >= 512) {
    diagnosticRejected += 1;
    return Promise.resolve({ recorded: false });
  }
  diagnosticPending += 1;
  const run = diagnosticTail.then(async () => {
    const { reviewerDiagnostics, [SCAN_METRIC_KEY]: storedMetrics, [DIAGNOSTIC_LOG_KEY]: storedLog } =
      await chrome.storage.local.get(['reviewerDiagnostics', SCAN_METRIC_KEY, DIAGNOSTIC_LOG_KEY]);
    const history = diagnosticHistory(reviewerDiagnostics);
    const state = diagnosticLoggingState(storedLog);
    const evicted = history.length >= 300 ? history[0] : null;
    const next = { ...state, lastSequence: state.lastSequence + 1, recorded: state.recorded + 1,
      lastRecordedAt: Date.now(), ringOverwrites: state.ringOverwrites + Number(Boolean(evicted)),
      unacknowledgedOverwrites: state.unacknowledgedOverwrites + Number(Boolean(evicted &&
        evicted.streamId === state.streamId && evicted.eventSeq > state.acknowledgedSequence)) };
    history.push({ ...entry, streamId: next.streamId, eventSeq: next.lastSequence });
    // An earlier queued export may initialize the baseline while this event is
    // waiting on storage. Validate against processing time, then count the
    // event inside that established window without rewriting its trace time.
    const metrics = scanMetricState(storedMetrics, Date.now());
    updateScanMetrics(metrics, { ...entry, at: Math.max(metrics.measurementStartedAt, entry.at) });
    await chrome.storage.local.set({ reviewerDiagnostics: history.slice(-300), [SCAN_METRIC_KEY]: metrics,
      [DIAGNOSTIC_LOG_KEY]: next });
    diagnosticLogging = next;
    return { recorded: true };
  });
  diagnosticTail = run.catch(() => {
    diagnosticStorageErrors += 1;
    // Logging must not interrupt the scanner or disclose a storage error body.
    console.warn('Reviewer Item Relay: diagnostic storage unavailable');
    return { recorded: false };
  }).finally(() => { diagnosticPending -= 1; });
  return diagnosticTail;
}

async function diagnosticReport() {
  const settings = await config();
  const check = await readBotCheck();
  // Serialize initialization with event writes so an export cannot overwrite a
  // newly counted transition or a challenge arriving during storage reads.
  const snapshot = diagnosticTail.then(async () => {
    const { reviewerDiagnostics, [SCAN_METRIC_KEY]: storedMetrics, [DIAGNOSTIC_LOG_KEY]: storedLog } =
      await chrome.storage.local.get(['reviewerDiagnostics', SCAN_METRIC_KEY, DIAGNOSTIC_LOG_KEY]);
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
    const logging = diagnosticLoggingState(storedLog);
    await chrome.storage.local.set({ [SCAN_METRIC_KEY]: metrics, [DIAGNOSTIC_LOG_KEY]: logging });
    return { now, events: diagnosticHistory(reviewerDiagnostics), metrics: scanMetricReport(metrics, now),
      logging: { ...logging, pending: diagnosticPending } };
  });
  diagnosticTail = snapshot.catch(() => { diagnosticStorageErrors += 1; });
  const { now, events, metrics, logging } = await snapshot;
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
    metrics, logging
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
    let errorCode = 'report';
    let responseStatus = 0;
    try {
      const { endpoint, token } = await config();
      const destination = new URL('/browser-diagnostics', checkedEndpoint(endpoint)).toString();
      const report = await diagnosticReport();
      // Receipt/transport bookkeeping changes after a successful mirror. It
      // must not defeat suppression of an otherwise identical event snapshot.
      const signature = destination + JSON.stringify({ ...report, exportedAt: 0,
        logging: Object.fromEntries(['streamId', 'lastSequence', 'recorded', 'rejected', 'storageErrors',
          'ringOverwrites', 'unacknowledgedOverwrites'].map(k => [k, report.logging[k]])) });
      const cached = (await chrome.storage.session.get([DIAGNOSTIC_MIRROR_KEY]))[DIAGNOSTIC_MIRROR_KEY];
      if (signature === cached?.signature && Date.now() - cached.at < 300_000) {
        return true;
      }
      await updateDiagnosticLogging(state => { state.syncAttempts += 1; });
      const headers = { 'Content-Type': 'application/json' };
      if (token) headers['X-Ingest-Token'] = token;
      const controller = new AbortController();
      timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
      errorCode = 'network';
      const response = await fetch(destination, { method: 'POST', headers,
        body: JSON.stringify(report), signal: controller.signal });
      responseStatus = Number.isInteger(response.status) ? response.status : 0;
      if (!response.ok) {
        errorCode = 'http';
        failure = response.status === 404
          ? 'Rebuild the local notifier to enable diagnostic mirroring (HTTP 404).'
          : `Local diagnostic sync returned HTTP ${response.status}; retrying next heartbeat.`;
        throw new Error('diagnostic sync rejected');
      }
      errorCode = 'report';
      const body = typeof response.json === 'function' ? await response.json() : JSON.parse(await response.text());
      const receipt = body?.receipt;
      if (receipt != null && (typeof receipt !== 'object' || Array.isArray(receipt) ||
          Object.keys(receipt).length !== 3 || receipt.streamId !== report.logging.streamId ||
          !Number.isSafeInteger(receipt.lastSequence) || receipt.lastSequence < 0 ||
          receipt.lastSequence > report.logging.lastSequence ||
          !Number.isSafeInteger(receipt.gapEvents) || receipt.gapEvents < 0 || receipt.gapEvents > 1e15)) {
        throw new Error('invalid diagnostic receipt');
      }
      errorCode = 'storage';
      await chrome.storage.local.set({ diagnosticsSyncedAt: Date.now(), diagnosticsSyncError: '' });
      if (!receipt || receipt.lastSequence === report.logging.lastSequence) {
        await chrome.storage.session.set({ [DIAGNOSTIC_MIRROR_KEY]: { signature, at: Date.now() } });
      }
      await updateDiagnosticLogging(state => {
        state.lastSyncAt = Date.now(); state.consecutiveSyncFailures = 0;
        state.lastSyncStatus = responseStatus; state.lastSyncError = 'none';
        if (receipt) state.acknowledgedSequence = Math.max(state.acknowledgedSequence, receipt.lastSequence);
      });
      return true;
    } catch (error) {
      if (error?.name === 'AbortError') errorCode = 'timeout';
      await updateDiagnosticLogging(state => {
        state.syncFailures += 1; state.consecutiveSyncFailures += 1;
        state.lastSyncStatus = responseStatus; state.lastSyncError = errorCode;
      });
      await chrome.storage.local.set({ diagnosticsSyncError: failure }).catch(() => undefined);
      return false;
    } finally {
      clearTimeout(timer);
    }
  });
  diagnosticSyncTail = run.catch(() => false);
  return diagnosticSyncTail;
}
