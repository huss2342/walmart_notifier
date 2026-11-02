// Reads the signed-in reviewer page. Every matching tab loads this script, but
// only the background-selected primary is allowed to observe, relay, or page.

const ITEM_LINK = 'a[href*="/ip/"]';
const DEFAULT_PATH_PATTERN = '^/reviews/claim-product';
const VALUE_RE = /Valued\s*at\s*\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i;
const VALUE_GLOBAL_RE = /Valued\s*at/gi;
const FREE_OFFER_RE = /(?:^|[\r\n])\s*Free\s*(?:\(\s*)?Valued\s*at\b/i;
const OUT_OF_STOCK_RE = /\bout of stock\b/i;
const CLAIMS_RE = /Free items remaining\s*(\d+)\s*item/i;
const BADGE_RE =
  /^(new arrival|reduced price|best seller|popular pick|clearance|rollback|deal|new)\b/i;
const TRAILING_PRICE_RE = /\s*\$\s*[0-9][0-9,]*(?:\.[0-9]{1,2})?(?:\s*was\s*\$\s*[0-9][0-9,]*(?:\.[0-9]{1,2})?)?\s*$/i;
const EXCLUDE_PATH = /\/(orders?|purchase-history|track|returns)\b/i;
const CARD_MAX_DEPTH = 8;

const INTERACTION_GRACE_MS = 30_000;
const ROLE_POLL_MS = 30_000;
const EMPTY_PAGE_TIMEOUT_MS = 30_000;
const NATIVE_START_TIMEOUT_MS = 5_000;
const NATIVE_NAVIGATION_BACKOFF_MS = 60_000;
const RESULTS_DOCUMENT_ID = Date.now();
let lastReadyResultsPage = null;
let terminalPagerObservation = null;
let focusWait = null;
let pendingFocusRetry = null;
const EMPTY_POLL_MS = 2_000;
const END_OF_RESULTS_RE = /no search results/i;
const UNPROVEN_PAGE_LIMIT = 100;
const MAX_PAGE_NUMBER = 100_000;
const EMPTY_END_CONFIRMATIONS = 3;
const EMPTY_END_CONFIRM_MS = 2_000;
const INGEST_RETRY_DELAYS_MS = [2_000, 5_000, 15_000, 30_000, 60_000];

let lastInteraction = 0;
let programmaticScroll = false;
let programmaticScrollUntil = 0;
let lastSharedInteraction = 0;
let lastFacetFocusReason = 'none';
let expectedFacetInput = null;
const interactionListeners = [];
for (const evt of ['pointerdown', 'click', 'keydown', 'wheel', 'touchstart', 'scroll']) {
  const listener = (event) => {
    if (event.isTrusted === false) return;
    const expected = expectedFacetInput;
    if (expected && Date.now() < expected.until && event.button === 0) {
      const labelTarget = expected.label.contains(event.target);
      if (evt === 'pointerdown' && !expected.seen.has(evt) && expected.label.isConnected && labelTarget) {
        expected.seen.add(evt);
        expected.pointer = { id: event.pointerId, x: event.clientX, y: event.clientY };
        return;
      }
      const pointer = expected.pointer;
      const paired = pointer && Number.isInteger(pointer.id) && pointer.id > 0 && event.pointerId === pointer.id &&
        Number.isFinite(pointer.x) && Number.isFinite(pointer.y) &&
        Math.abs(event.clientX - pointer.x) <= 2 && Math.abs(event.clientY - pointer.y) <= 2;
      // Closing the pressed popover can retarget click to its former ancestor.
      // Accept only the same pointer/point after our exact-label pointerdown.
      // A second real pointerdown, any other input or expiry cancels this arm.
      if (evt === 'click' && expected.seen.has('pointerdown') && (labelTarget || paired)) {
        expectedFacetInput = null;
        return;
      }
    }
    if (evt === 'scroll' &&
        (programmaticScroll || Date.now() < programmaticScrollUntil ||
          (navigationPending && ['waiting-page-change', 'waiting-target-change'].includes(phase)))) return;
    if (navigationPending && phase === 'waiting-target-change' &&
        Date.now() - lastInteraction >= INTERACTION_GRACE_MS) {
      console.debug('Reviewer Item Relay: facet interrupted by input ' + JSON.stringify({
        event: evt, expected: Boolean(expected), connected: Boolean(expected?.label.isConnected),
        labelTarget: Boolean(expected?.label.contains(event.target)),
        pointerMatch: Boolean(expected?.pointer && expected.pointer.id === event.pointerId),
        pointMatch: Boolean(expected?.pointer && Number.isFinite(expected.pointer.x) &&
          Math.abs(event.clientX - expected.pointer.x) <= 2 && Math.abs(event.clientY - expected.pointer.y) <= 2),
        button: Number.isInteger(event.button) ? event.button : null,
        detail: Number.isInteger(event.detail) ? event.detail : null,
        pointerId: Number.isInteger(event.pointerId) ? Math.min(100, Math.max(-1, event.pointerId)) : null,
        primarySeen: expected?.seen.has('pointerdown') === true,
        target: ['INPUT', 'LABEL', 'SPAN', 'DIV', 'BODY', 'SECTION', 'MAIN'].includes(event.target?.tagName)
          ? event.target.tagName.toLowerCase() : 'other'
      }));
    }
    // The site's own page click can restore scroll after our animation ends.
    // During that transition, wheel/touch/pointer/key input still yields, but
    // an unaffiliated scroll event must not unlock old cards or pause the scan.
    cancelPageControlScroll();
    cancelFocusPreparation();
    cancelResultsScroll();
    expectedFacetInput = null;
    lastInteraction = Date.now();
    if (Number.isInteger(role.tabId) && Date.now() - lastSharedInteraction >= 1_000) {
      lastSharedInteraction = Date.now();
      sendToWorker({ type: 'reviewer:user-active', generation: role.generation }).catch(() => {});
    }
  };
  document.addEventListener(evt, listener, { passive: true, capture: true });
  interactionListeners.push([evt, listener]);
}

const documentSession = ReviewerDocumentSession.create({ href: () => location.href });
const role = documentSession.role;
let phase = 'passive';
let lastActivityAt = 0;
let relayTimer = null;
let actionTimer = null;
let observer = null;
let relaying = false;
let emptySince = 0;
let ingestRetryAttempt = 0;
let roleRequestInFlight = false;
let verificationObserved = false;
let verificationReported = false;
let trafficWaitStartedAt = null;
let trafficWaitTimer = null;
let trafficNavigation = null;
let trafficDocumentRecovered = false;
let navigationRecoveryRevision = 0;
const TRAFFIC_POLL_MS = 10_000;
let sharedVerificationPaused = false;
let sharedVerificationRevision = 0;
let nextActionAt = 0;
let navigationPending = false;
let navigationTarget = null;
let nativeNavigationPausedUntil = 0;
let nativeNavigationFailures = {};
let currentSweepMode = 'none';
let currentPaginationFallback = false;
let contextInvalidated = false;
let rolePollTimer = null;
let roleRetryTimer = null;
let verificationTimer = null;
let verificationObserver = null;
let pagerPauseTimer = null;
let pagerPauseResolve = null;
let pagerFocusTimer = null;
let pagerFocusEpoch = 0;
const focusRequests = new Set();
let lastSelectionUntil = 0;
let lastSelectionLeaseId = null;
const revokedSelectionLeases = new Set();
const onScrollVisibilityChange = () => {
  if (document.visibilityState === 'visible') return;
  const controller = documentSession.reading.controller;
  if (!controller) return;
  // An occluded window can hide its selected tab. Check tab ownership instead
  // of mistaking that for a user selecting a different tab.
  documentSession.request(() => sendToWorker({ type: 'reviewer:selection-check',
    generation: role.generation }), 3_000).then(reply => {
    if (documentSession.reading.controller === controller && reply?.selected !== true) controller.abort();
  }).catch(() => {
    if (documentSession.reading.controller === controller) controller.abort();
  });
};
document.addEventListener('visibilitychange', onScrollVisibilityChange, true);
interactionListeners.push(['visibilitychange', onScrollVisibilityChange]);
const reviewerOrigin = new URL(location.href).origin;
const initialDocumentHref = location.href;
let initialDocumentDataCurrent = true;
let reviewerPathPattern = DEFAULT_PATH_PATTERN;
// Full loads have a browser navigation clock; native page changes update this
// same anchor. Loading, settling and local delivery all count toward spacing.
let lastNavigationAt = typeof performance !== 'undefined' &&
  Number.isFinite(performance.timeOrigin) ? performance.timeOrigin : 0;
let confirmedEmptyBoundary = null;
const EMPTY_BOUNDARY_TTL_MS = 10 * 60_000;
const endConfirmationsInDocument = new Map();
const attemptedAuxiliarySelections = new Set();

// The observer consumes a small explicit interface; it no longer reaches into
// content-script globals or owns a second competing intake lock.
const visibleObservation = ReviewerLiveObserver.create({ session: documentSession,
  canObserve: canObserveVisible,
  canRun: () => !checkVerification() && isCurrent() && keepMonitoringScope(),
  ready: async generation => await isReviewerPage() && isCurrent(generation),
  current: isCurrent, collect: excluded => collect(excluded, true),
  send: message => sendDocumentMessage({ ...message, intakePhase: 'live' }), acknowledgeRole: applyAckRole,
  diagnostic, page: currentPage, acquire: beginDocumentRelay, release: endDocumentRelay });
const acknowledgedVisibleIds = documentSession.visible.acknowledgedIds;
function rememberAcknowledged(items) { visibleObservation.remember(items); }
function cancelVisibleObservation() { visibleObservation.cancel(); }
function resetVisibleObservations() { visibleObservation.reset(); }
function scheduleVisibleObservation(delayMs) { visibleObservation.schedule(delayMs); }
function observeVisibleUpdate() { return visibleObservation.observe(); }
function canObserveVisible() {
  // An unbound facet still displays its parent's cards.
  return !role.auxiliary?.selection &&
    (phase === 'complete' || phase === 'waiting-navigation') && !navigationPending;
}
function beginDocumentRelay() {
  if (relaying) return null;
  const operation = documentSession.begin();
  if (operation) relaying = true;
  return operation;
}
function endDocumentRelay(operation) {
  if (documentSession.finish(operation)) relaying = false;
}
function revokeDocumentWork() { documentSession.revoke(); relaying = false; }

function stopInvalidatedContext() {
  if (contextInvalidated) return;
  contextInvalidated = true;
  role.active = false;
  documentSession.dispose();
  relaying = false;
  cancelPageWork();
  stopObserver();
  verificationObserver?.disconnect();
  clearTimeout(verificationTimer);
  clearTimeout(roleRetryTimer);
  clearInterval(rolePollTimer);
  clearTimeout(pagerPauseTimer);
  const finishPagerPause = pagerPauseResolve;
  pagerPauseTimer = pagerPauseResolve = null;
  finishPagerPause?.();
  verificationTimer = roleRetryTimer = rolePollTimer = null;
  for (const [event, listener] of interactionListeners) {
    document.removeEventListener(event, listener, true);
  }
  interactionListeners.length = 0;
  try { chrome.runtime.onMessage.removeListener(onReviewerMessage); } catch { /* Context is already gone. */ }
  try { chrome.storage.onChanged?.removeListener(onVerificationStorageChange); } catch { /* Context is already gone. */ }
  phase = 'extension-reloaded';
}

function extensionAvailable() {
  if (contextInvalidated) return false;
  try { if (chrome.runtime?.id) return true; } catch { /* Reloaded extension. */ }
  stopInvalidatedContext();
  return false;
}

async function extensionCall(invoke) {
  if (!extensionAvailable()) return undefined;
  try {
    const value = await invoke();
    return extensionAvailable() ? value : undefined;
  } catch (error) {
    // Chrome can throw synchronously before returning a Promise, including in
    // diagnostic/observer callbacks. Other transport/storage errors retain
    // their normal retry or failure behavior.
    if (/extension context invalidated/i.test(error?.message || '') || !extensionAvailable()) {
      stopInvalidatedContext();
      return undefined;
    }
    throw error;
  }
}

const sendToWorker = message => extensionCall(() => chrome.runtime.sendMessage(message));
const sendDocumentMessage = message => documentSession.request(() => sendToWorker({ ...message,
  documentId: RESULTS_DOCUMENT_ID }),
  message.type === 'reviewer:page'
    ? 30_000 + Math.max(1, Math.ceil((message.items?.length || 0) / 200)) * 15_000
    : 15_000);
const readLocal = async keys => await extensionCall(() => chrome.storage.local.get(keys)) || {};
const writeLocal = values => extensionCall(() => chrome.storage.local.set(values));
const removeLocal = keys => extensionCall(() => chrome.storage.local.remove(keys));

function diagnostic(event, details = {}) {
  sendToWorker({ type: 'reviewer:diagnostic', event,
    details: { ...details, page: currentPage(), generation: role.generation, documentId: RESULTS_DOCUMENT_ID,
      lane: role.auxiliary?.lane || 'home',
      ...Object.fromEntries(['round', 'targetIndex', 'attempt'].filter(k => Number.isSafeInteger(role.auxiliary?.[k]))
        .map(k => [k, role.auxiliary[k]])) } }).catch(() => {});
}

// Local diagnostic evidence only. Never export text, paths, resource addresses,
// account details or request/response bodies, and never make a network request.
let pageStateContext = '';
let pageStateEmptyContext = null;
let auxiliaryEmptyObservation = null;
let auxiliaryReadinessWaitSince = 0;
let auxiliaryReadinessWaitKey = '';
let auxiliaryRenderWait = null;
const pageStateHistory = new Map();
const PAGE_STATE_STAGES = new Set(['empty-observed', 'empty-confirmation', 'fallback',
  'unusable', 'zero-page-complete', 'recovered']);

function pageStateEvidence(sweep = null) {
  const count = (value, maximum = 10_000) => Math.min(maximum, Math.max(0,
    Number.isFinite(value) ? Math.floor(value) : 0));
  const visible = node => {
    if (!node || node.hidden || node.getAttribute?.('aria-hidden') === 'true') return false;
    const style = getComputedStyle(node);
    return style.display !== 'none' && !['hidden', 'collapse'].includes(style.visibility) &&
      (!node.getClientRects || node.getClientRects().length > 0);
  };
  const main = document.querySelector('main, [role="main"], #maincontent') || document.body;
  const text = main?.innerText || '';
  const all = selector => Array.from(document.querySelectorAll(selector)).slice(0, 10_000);
  const mode = sweep ? sweepRecordMode(sweep) : currentSweepMode;
  const queries = sweepQueries(mode);
  const query = String(sweep?.query || new URL(location.href).searchParams.get('queryNups') ||
    new URL(location.href).searchParams.get('q') || '').toLocaleLowerCase('en-US');
  const navigation = typeof performance !== 'undefined' && performance?.getEntriesByType
    ? performance.getEntriesByType('navigation')[0] : null;
  const resources = typeof performance !== 'undefined' && performance?.getEntriesByType
    ? performance.getEntriesByType('resource').slice(-1000) : [];
  const responseStatus = Number(navigation?.responseStatus);
  return {
    readyState: ['loading', 'interactive', 'complete'].includes(document.readyState) ? document.readyState : 'unknown',
    visibility: ['visible', 'hidden', 'prerender'].includes(document.visibilityState) ? document.visibilityState : 'unknown',
    route: onReviewerRoute() ? 'reviewer' : /\/(?:login|signin|auth)(?:\/|$)/i.test(location.pathname) ? 'auth' : 'other',
    reviewerShell: all('h1').some(node => visible(node) && /^recognized reviewer$/i.test(node.textContent?.trim() || '')),
    resultsLabel: visible(document.querySelector('#claim-product-result-label')),
    noResults: END_OF_RESULTS_RE.test(text),
    loading: all('[aria-busy="true"], [role="progressbar"], [data-testid="loading"], [data-testid="spinner"]').some(visible),
    error: /something went wrong|trouble loading|temporarily unavailable|service unavailable|technical difficulties/i.test(text),
    signIn: all('input[type="password"]').some(visible),
    productLinks: count(all(ITEM_LINK).length),
    offerLines: count((text.match(VALUE_GLOBAL_RE) || []).length),
    pagerPresent: Boolean(pageControlRoot()),
    renderedPage: count(renderedPaginationPage() || 0, MAX_PAGE_NUMBER),
    navigationType: ['navigate', 'reload', 'back_forward'].includes(navigation?.type) ? navigation.type : 'unknown',
    responseStatus: Number.isInteger(responseStatus) && responseStatus >= 100 && responseStatus <= 599 ? responseStatus : 0,
    failedRequests: count(resources.filter(entry => {
      if (!['fetch', 'xmlhttprequest'].includes(entry.initiatorType) || !(entry.responseStatus >= 400 && entry.responseStatus <= 599)) return false;
      try { return new URL(entry.name, location.href).origin === location.origin; } catch { return false; }
    }).length),
    readingFailures: count(documentSession.reading.failures),
    readingWorkMs: count(documentSession.reading.workMs, 1e15),
    queryIndex: queries.findIndex(value => value.toLocaleLowerCase('en-US') === query) + 1,
    queryCount: count(queries.length)
  };
}

function recordPageState(stage, details = {}) {
  try {
    if (!isCurrent() || !PAGE_STATE_STAGES.has(stage)) return;
    const context = `${role.generation}:${location.href}`;
    if (context !== pageStateContext) {
      pageStateContext = context;
      pageStateHistory.clear();
    }
    if (stage === 'empty-observed' || stage === 'empty-confirmation') pageStateEmptyContext = context;
    const now = Date.now();
    const previous = pageStateHistory.get(stage);
    // At most one event per stage per ten seconds, including changing spinners.
    if (previous && now >= previous.at && now - previous.at < 10_000) return;
    const state = pageStateEvidence(details.sweep);
    const attempt = Number.isSafeInteger(details.attempt) && details.attempt >= 0 ? details.attempt : 0;
    const signature = JSON.stringify({ state, attempt });
    if (previous?.signature === signature && now >= previous.at && now - previous.at < 60_000) return;
    pageStateHistory.set(stage, { at: now, signature });
    if (stage === 'recovered') pageStateEmptyContext = null;
    const sweep = details.sweep;
    diagnostic('page-state', { stage, state, lane: role.auxiliary?.lane || 'home',
      documentId: RESULTS_DOCUMENT_ID, attempt,
      elapsedMs: emptySince ? Math.max(0, now - emptySince) : 0,
      items: Number.isSafeInteger(details.items) && details.items >= 0 ? details.items : 0,
      pages: (Number(sweep?.completedPages) || 0) + new Set(sweep?.visited || []).size,
      mode: sweep ? sweepRecordMode(sweep) : currentSweepMode });
  } catch { /* Diagnostic failure must never change intake, navigation or recovery. */ }
}

// A challenge applies to the shared session, including passive tabs. Cached
// cards underneath it must never count as proof that verification is complete.
function checkVerification() {
  if (!extensionAvailable()) return true;
  const reason = verificationReason();
  verificationObserved = Boolean(reason);
  if (!reason) {
    verificationReported = false;
    return false;
  }
  revokeDocumentWork();
  confirmedEmptyBoundary = null;
  cancelPageWork();
  stopObserver();
  phase = 'waiting-verification';
  if (!verificationReported) {
    verificationReported = true;
    sendToWorker({ type: 'reviewer:verification', reason }).then(reply => {
      if (!reply?.ok) verificationReported = false;
    }).catch(() => { verificationReported = false; });
  }
  return true;
}

function highTrafficPresent() {
  if (!onReviewerRoute() || !document.body) return false;
  const text = (document.body.innerText || '').replace(/\s+/g, ' ').trim();
  if (!/high traffic is slowing things down a bit\b/i.test(text) ||
      !/we['’]ll load this page when it['’]s ready\b/i.test(text)) return false;
  const heading = [...document.querySelectorAll('h1, h2, [role="heading"]')].some(node => {
    if (!/^hold tight for a moment$/i.test((node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim())) return false;
    const style = getComputedStyle(node);
    return !node.hidden && style.display !== 'none' && !['hidden', 'collapse'].includes(style.visibility) &&
      (!node.getClientRects || node.getClientRects().length > 0);
  });
  return heading || /^hold tight for a moment\b/i.test(text);
}

// This is a server-requested wait, separate from verification and ordinary
// missing results. Check only the DOM; the fallback timer makes no requests.
function waitForTraffic() {
  if (!extensionAvailable()) return true;
  if (!role.active || sharedVerificationPaused || verificationObserved) return false;
  if (highTrafficPresent()) {
    if (trafficWaitStartedAt === null) {
      const pending = navigationPending && trafficNavigation?.generation === role.generation
        ? { continuation: trafficNavigation, target: navigationTarget } : null;
      revokeDocumentWork();
      cancelPageWork();
      stopObserver();
      if (pending) {
        trafficNavigation = pending.continuation;
        navigationPending = true;
        navigationTarget = pending.target;
      }
      trafficWaitStartedAt = Date.now();
      diagnostic('traffic-wait', { documentId: RESULTS_DOCUMENT_ID });
    }
    phase = 'waiting-traffic';
    lastActivityAt = Date.now();
    nextActionAt = Date.now() + TRAFFIC_POLL_MS;
    if (!trafficWaitTimer) trafficWaitTimer = setTimeout(() => {
      trafficWaitTimer = null;
      if (!checkVerification()) waitForTraffic();
    }, TRAFFIC_POLL_MS);
    return true;
  }
  if (trafficWaitStartedAt === null) return false;
  const durationMs = Math.max(0, Date.now() - trafficWaitStartedAt);
  clearTimeout(trafficWaitTimer);
  trafficWaitTimer = null;
  trafficWaitStartedAt = null;
  trafficDocumentRecovered = true;
  emptySince = 0;
  lastActivityAt = Date.now();
  nextActionAt = 0;
  diagnostic('traffic-resumed', { documentId: RESULTS_DOCUMENT_ID, durationMs });
  if (navigationPending && trafficNavigation?.generation === role.generation) {
    trafficNavigation.resume(durationMs);
  } else {
    phase = 'starting';
    startObserver();
    scheduleRelay(0, true);
  }
  return true;
}

function isCurrent(generation = role.generation) {
  return extensionAvailable() && role.active && !sharedVerificationPaused &&
    !verificationObserved && trafficWaitStartedAt === null && !highTrafficPresent() &&
    role.generation === generation;
}

function setSharedVerificationPause(paused) {
  sharedVerificationRevision += 1;
  sharedVerificationPaused = paused;
  if (!paused) return;
  revokeDocumentWork();
  cancelPageWork();
  stopObserver();
  phase = 'waiting-verification';
  nextActionAt = 0;
}

function onVerificationStorageChange(changes, area) {
  if (area !== 'local' || !changes.botCheck || !extensionAvailable()) return;
  // Missing/corrupt state is not proof that all checks were cleared.
  const until = changes.botCheck.newValue?.pausedUntil;
  if (typeof until !== 'number' || !Number.isFinite(until) || until < 0) return;
  setSharedVerificationPause(until > 0);
  if (!sharedVerificationPaused) requestRole();
}

function keepMonitoringScope() {
  const target = role.auxiliary?.targetUrl;
  const scoped = target ? auxiliaryScopeUrl(location.href) !== null &&
    auxiliaryScopeUrl(location.href) === auxiliaryScopeUrl(target) :
    scanScopeAllowed(location.href, currentSweepMode, currentPaginationFallback);
  if (onReviewerRoute() && scoped) return true;
  applyRole({ ...role, active: false });
  phase = 'manual-browsing';
  requestRole();
  return false;
}

function sweepingNow() {
  return isCurrent() && phase !== 'idle' && phase !== 'complete' && phase !== 'passive';
}

function onReviewerMessage(msg, _sender, sendResponse) {
  if (msg?.type === 'reviewer:selection-revoked') {
    if (isCurrent(msg.generation) && typeof msg.leaseId === 'string') {
      revokedSelectionLeases.add(msg.leaseId);
      // Requests expire within seconds; retain far more identities than can
      // be issued during that window, without growing for a long-lived page.
      if (revokedSelectionLeases.size > 128) revokedSelectionLeases.delete(revokedSelectionLeases.values().next().value);
    }
    const matchesLease = typeof msg.leaseId === 'string' ? msg.leaseId === lastSelectionLeaseId
      : !Number.isFinite(msg.leaseUntil) || msg.leaseUntil >= lastSelectionUntil;
    if (isCurrent(msg.generation) && matchesLease) {
      if (auxiliaryRenderWait?.grant?.granted) {
        auxiliaryRenderWait.grant.until = Math.min(auxiliaryRenderWait.grant.until, Date.now());
      }
      cancelResultsScroll();
      cancelFocusPreparation();
      cancelPageControlScroll();
    }
    sendResponse({ ok: true });
    return undefined;
  }
  if (!extensionAvailable()) return undefined;
  if (msg?.type === 'reviewer:pause') {
    setSharedVerificationPause(true);
    sendResponse({ ok: true });
    return undefined;
  }
  if (msg?.type === 'reviewer:focus-available') {
    if (msg.generation === role.generation && isCurrent() && !checkVerification() && keepMonitoringScope()) {
      const retry = pendingFocusRetry;
      if (retry && documentSession.current(retry.token) && ['waiting-focus', 'waiting-user'].includes(phase)) {
        clearTimeout(actionTimer);
        actionTimer = setTimeout(retry.attempt, 0);
        nextActionAt = Date.now();
      } else if (!actionTimer && phase === 'waiting-focus') {
        // Reading and initial rendering retry through relay(), not a queued
        // page action. Both paths must request fresh authority after this hint.
        scheduleRelay(0, true);
      }
    }
    sendResponse({ ok: true });
    return undefined;
  }
  if (msg?.type === 'reviewer:probe') {
    const botCheck = checkVerification();
    if (!botCheck) waitForTraffic();
    const highTraffic = highTrafficPresent();
    sendResponse({
      ready: true,
      botCheck,
      highTraffic,
      reviewerReady: !botCheck && !highTraffic && (collect().length > 0 || atEndOfResults()),
      active: role.active,
      generation: role.generation,
      documentId: RESULTS_DOCUMENT_ID,
      phase,
      busy: Date.now() - lastInteraction < INTERACTION_GRACE_MS,
      visibility: document.visibilityState,
      focusReason: lastFacetFocusReason,
      sweeping: sweepingNow(),
      lastActivityAt,
      nextActionAt
    });
    return undefined;
  }
  if (msg?.type === 'reviewer:set-role') {
    applyRole(msg);
    sendResponse({ ok: true });
    return undefined;
  }
  if (msg?.type === 'reviewer:restart-native') {
    handleNativeRestart(msg).then(sendResponse).catch(() => sendResponse({ ok: false }));
    return true;
  }
  if (msg?.type === 'reviewer:recover') {
    if (isCurrent(msg.generation) && !relaying) {
      if (phase !== 'waiting-user') {
        if (navigationPending) {
          const target = navigationTarget || location.href;
          cancelPageWork();
          queueWhenIdle(() => {
            navigationPending = true;
            navigationTarget = target;
            diagnostic('navigation-fallback', { reason: 'native-stalled' });
            if (isCurrent(msg.generation)) return navigateWithFocus(() => location.assign(target), msg.generation);
          }, 1_000, 'recovering');
        } else {
          clearTimeout(actionTimer);
          actionTimer = null;
          nextActionAt = 0;
          scheduleRelay(0, true);
        }
      }
      lastActivityAt = Date.now();
      sendResponse({ ok: true });
    } else {
      sendResponse({ ok: false });
    }
    return undefined;
  }
  // Compatibility with the previous background worker during an unpacked
  // extension reload.
  if (msg?.type === 'busy?') {
    sendResponse({
      busy: Date.now() - lastInteraction < INTERACTION_GRACE_MS,
      sweeping: sweepingNow()
    });
    return undefined;
  }
  return undefined;
}
if (extensionAvailable()) chrome.runtime.onMessage.addListener(onReviewerMessage);

/** Force the lazy pager into the DOM, then restore the user's scroll position. */
async function revealPager() {
  const generation = role.generation;
  if (!isCurrent(generation)) return;
  await requestPreparationFocus(generation, Date.now() + 1_000);
  if (!isCurrent(generation) || checkVerification() ||
      Date.now() - lastInteraction < INTERACTION_GRACE_MS) return;
  const from = window.scrollY;
  programmaticScroll = true;
  const pause = ms => new Promise(resolve => {
    pagerPauseResolve = resolve;
    pagerPauseTimer = setTimeout(() => {
      pagerPauseTimer = pagerPauseResolve = null;
      resolve();
    }, ms);
  });
  try {
    window.scrollTo(0, document.body.scrollHeight);
    await pause(400);
    if (!isCurrent(generation) || checkVerification() ||
        Date.now() - lastInteraction < INTERACTION_GRACE_MS) return;
    window.scrollTo(0, from);
    await pause(50);
  } finally {
    // Some browsers dispatch the final scroll event after scrollTo returns.
    programmaticScrollUntil = Date.now() + 250;
    programmaticScroll = false;
  }
}

function onReviewerRoute() {
  const url = new URL(location.href);
  if (url.origin !== reviewerOrigin || EXCLUDE_PATH.test(url.pathname)) return false;
  try {
    return new RegExp(reviewerPathPattern, 'i').test(url.pathname);
  } catch {
    return new RegExp(DEFAULT_PATH_PATTERN, 'i').test(url.pathname);
  }
}

async function isReviewerPage() {
  const { pathPattern = DEFAULT_PATH_PATTERN } =
    await readLocal(['pathPattern']);
  reviewerPathPattern = pathPattern;
  return onReviewerRoute();
}

function idFromHref(href) {
  const match = href.match(/\/ip\/(?:[^/?#]+\/)?(\d{6,})/);
  return match ? match[1] : null;
}

/** The nearest unambiguous product ancestor holding one retail value. */
function cardFor(anchor) {
  const ownId = idFromHref(anchor.getAttribute('href') || '');
  const normalized = text => text.replace(/\s+/g, ' ').trim().toLowerCase();
  let node = anchor.parentElement;
  for (let depth = 0; node && depth < CARD_MAX_DEPTH; depth += 1) {
    const ids = new Set([ownId]);
    const primaryIds = new Set();
    const primaryTitles = new Set();
    const links = [];
    for (const link of node.querySelectorAll?.(ITEM_LINK) || []) {
      const id = idFromHref(link.getAttribute('href') || '');
      if (!id) continue;
      ids.add(id);
      const label = (link.getAttribute('aria-label') || link.querySelector('img')?.getAttribute('alt') || '').trim();
      links.push({ id, label, anchor: link });
      const primary = label.match(/^view item for\s+(\S.*)$/i);
      if (primary) {
        primaryIds.add(id);
        primaryTitles.add(normalized(primary[1]));
      }
    }
    // One price alone may belong to a sibling whose neighbor has no value.
    // Keep the primary card's evidence only when every other ID is an explicit
    // variant of that primary. Variant links are never independent candidates.
    if (ids.size > 1) {
      if (!primaryIds.size && node.getAttribute?.('role') === 'group') {
        // Out-of-stock cards can omit View item. Only that unmarked group
        // needs this fallback; ordinary marked cards do no extra DOM work.
        // A swatch heading cannot establish a selected primary.
        const headingIds = new Set();
        const headingTitles = new Set();
        for (const { id, label, anchor: link } of links) {
          if (/^.+,\s*variant on\s+\S/i.test(label)) continue;
          const headings = [...(link.querySelectorAll?.('h3') || [])];
          const title = headings.length === 1 ? normalized(headings[0].innerText || headings[0].textContent || '') : '';
          if (title) { headingIds.add(id); headingTitles.add(title); }
        }
        if (headingIds.size === 1 && headingIds.has(ownId) && headingTitles.size === 1) {
          primaryIds.add(ownId);
          primaryTitles.add([...headingTitles][0]);
        }
      }
      if (primaryIds.size !== 1 || !primaryIds.has(ownId) || primaryTitles.size !== 1) return null;
      const [primaryTitle] = primaryTitles;
      for (const { id, label } of links) {
        if (id === ownId) continue;
        const variant = label.match(/^.+,\s*variant on\s+(\S.*)$/i);
        if (!variant || normalized(variant[1]) !== primaryTitle) return null;
      }
    }
    const text = node.innerText || '';
    const hits = text.match(VALUE_GLOBAL_RE);
    if (hits) return hits.length === 1 ? { node, text } : null;
    node = node.parentElement;
  }
  return null;
}

function cleanTitle(raw) {
  let title = (raw || '').replace(/\s+/g, ' ').trim();
  title = title.replace(BADGE_RE, '').trim();
  title = title.replace(/^view item(?:\s+for)?\b/i, '').trim();
  title = title.replace(TRAILING_PRICE_RE, '').trim();
  return title.slice(0, 250);
}

function titleFor(anchor, card) {
  const candidates = [
    anchor.querySelector('img')?.getAttribute('alt'),
    anchor.getAttribute('aria-label'),
    anchor.innerText,
    card?.text.split('\n').find((line) => line.length > 20 && !VALUE_RE.test(line))
  ];
  for (const candidate of candidates) {
    const title = cleanTitle(candidate);
    if (title.length > 3) return title;
  }
  return '';
}

function badgeFor(text) {
  const match = (text || '').trim().match(BADGE_RE);
  return match ? match[1].toLowerCase() : '';
}

/** Evidence only: the backend owns the shared Discord category taxonomy. */
function categoryFor(anchor, card) {
  const attributes = ['data-product-category', 'data-category', 'data-category-name',
    'data-product-department', 'data-department', 'data-department-name'];
  const selector = attributes.map(name => `[${name}]`).concat('[itemprop~="category"]').join(',');
  // Never ascend past the one-item boundary or use the page's search keyword.
  const nodes = [anchor, card?.node,
    ...(card?.node.querySelectorAll?.(selector) || [])];
  for (const node of nodes) {
    if (!node?.getAttribute) continue;
    const candidates = attributes.map(name => node.getAttribute(name));
    if ((node.getAttribute('itemprop') || '').split(/\s+/).includes('category')) {
      candidates.push(node.getAttribute('content'), node.textContent);
    }
    for (const raw of candidates) {
      const value = (raw || '').replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim();
      if (value && value.length <= 160 && !BADGE_RE.test(value)) return value;
    }
  }
  return '';
}

function imageFor(anchor, card) {
  const images = [anchor.querySelector('img'), ...(card?.node.querySelectorAll?.('img') || [])];
  for (const image of images) {
    if (!image) continue;
    for (const raw of [image.currentSrc, image.getAttribute('src')]) {
      if (typeof raw !== 'string' || raw.length > 2048) continue;
      try {
        const url = new URL(raw);
        if (url.protocol !== 'https:' || url.hostname !== 'i5.walmartimages.com' ||
            url.username || url.password || url.port || !/^\/(asr|seo)\//.test(url.pathname) ||
            !/\.(avif|jpe?g|png|webp)$/i.test(url.pathname)) continue;
        url.search = '';
        url.hash = '';
        return url.toString();
      } catch { /* Missing, relative, inline and malformed image URLs are omitted. */ }
    }
  }
  return '';
}

function claimsRemaining() {
  const match = (document.body.innerText || '').match(CLAIMS_RE);
  return match ? parseInt(match[1], 10) : null;
}

const CARD_EVIDENCE_SAMPLE_LIMIT = 12;
let lastCardExtractionSignature = '';

function recordCardExtraction(decisions, items) {
  const observations = [...decisions.values()];
  const priority = sample => sample.decision === 'accepted' && sample.valueCents === null ? 0
    : sample.outOfStock === true ? 1 : sample.decision === 'variant' ? 2
      : sample.decision === 'card-unavailable' ? 3 : sample.decision !== 'accepted' ? 4 : 5;
  const lane = ['departments', 'keywords', 'brands', 'priority'].includes(role.auxiliary?.lane)
    ? role.auxiliary.lane : 'home';
  const acceptedCount = observations.filter(sample => sample.decision === 'accepted').length;
  const details = { documentId: RESULTS_DOCUMENT_ID, lane, items,
    acceptedCount, rejectedCount: observations.length - acceptedCount,
    variantCount: observations.filter(sample => sample.decision === 'variant').length,
    outOfStockCount: observations.filter(sample => sample.outOfStock === true).length,
    missingValueCount: observations.filter(sample => sample.cardFound && sample.valueCents === null).length,
    samples: observations.sort((left, right) => priority(left) - priority(right))
      .slice(0, CARD_EVIDENCE_SAMPLE_LIMIT) };
  const signature = JSON.stringify({ ...details, page: currentPage(), generation: role.generation });
  if (signature === lastCardExtractionSignature) return;
  lastCardExtractionSignature = signature;
  // Only actual extraction paths opt in. Probe/readiness collections stay
  // silent, and diagnostics never contain titles, URLs, queries or account data.
  diagnostic('card-extraction', details);
}

function collect(excludedIds = null, withEvidence = false) {
  let claims;
  const params = new URLSearchParams(location.search);
  const query = params.get('queryNups') || params.get('q') || '';
  const byId = new Map();
  const decisions = withEvidence ? new Map() : null;
  const observe = (id, decision, card = null, free = null, outOfStock = null, value = null) => {
    if (!decisions || !/^\d{6,20}$/.test(id)) return;
    const cents = value === null ? null : Math.round(value * 100);
    decisions.set(id, { itemId: `ip-${id}`, decision, cardFound: Boolean(card), free, outOfStock,
      valueCents: Number.isSafeInteger(cents) && cents >= 0 && cents <= 1e15 ? cents : null });
  };

  for (const anchor of document.querySelectorAll(ITEM_LINK)) {
    const id = idFromHref(anchor.getAttribute('href') || '');
    if (!id || byId.has(id) || excludedIds?.has(`ip-${id}`)) continue;
    if (/^.+,\s*variant on\s+\S/i.test(anchor.getAttribute('aria-label') || anchor.querySelector('img')?.getAttribute('alt') || '')) {
      observe(id, 'variant');
      continue;
    }

    const card = cardFor(anchor);
    // A link without its own unambiguous card carries no offer or stock
    // evidence. In particular, rejected variant links must not default to an
    // available product just because their sibling's card was found earlier.
    if (!card) { observe(id, 'card-unavailable'); continue; }
    const free = FREE_OFFER_RE.test(card.text);
    const outOfStock = OUT_OF_STOCK_RE.test(card.text);
    const value = card.text.match(VALUE_RE);
    const valueUsd = value ? parseFloat(value[1].replace(/,/g, '')) : null;
    // Read the visible offer line, not a Free shipping label or hidden text.
    if (!free) { observe(id, 'not-free', card, free, outOfStock, valueUsd); continue; }
    const title = titleFor(anchor, card);
    if (!title) { observe(id, 'missing-title', card, free, outOfStock, valueUsd); continue; }

    if (claims === undefined) claims = claimsRemaining();
    observe(id, outOfStock ? 'out-of-stock' : 'accepted', card, free, outOfStock, valueUsd);
    byId.set(id, {
      item_id: `ip-${id}`,
      title,
      value_usd: valueUsd,
      url: new URL(anchor.getAttribute('href'), location.origin).toString().split('?')[0],
      category: categoryFor(anchor, card),
      badge: badgeFor(anchor.innerText),
      image_url: imageFor(anchor, card),
      available: !outOfStock,
      source: 'extension',
      claims_remaining: claims,
      query
    });
  }
  if (decisions) recordCardExtraction(decisions, byId.size);
  return [...byId.values()];
}

// --- pagination -------------------------------------------------------------

function currentPage() {
  const raw = parseInt(new URLSearchParams(location.search).get('page') || '1', 10);
  return Number.isFinite(raw) && raw > 0 ? raw : 1;
}

function pageUrl(page) {
  const url = new URL(location.href);
  if (page <= 1) url.searchParams.delete('page');
  else url.searchParams.set('page', String(page));
  return url.toString();
}

function searchUrl(query) {
  const url = new URL(pageUrl(1));
  url.searchParams.set('q', query);
  url.searchParams.set('queryNups', query);
  return url.toString();
}

function atEndOfResults() {
  return END_OF_RESULTS_RE.test(document.body.innerText || '');
}

function totalPages() {
  if (location.href !== initialDocumentHref) initialDocumentDataCurrent = false;
  const list = pageControlRoot();
  if (!list) return auxiliarySinglePageHint();
  // Inline controls can have innerText "123" even though they are pages 1, 2,
  // and 3. Read individual labels/leaves before falling back to plain text.
  const controlNumbers = [...(list.querySelectorAll?.('*') || [])].flatMap(node => {
    const control = /^(?:BUTTON|A)$/.test(node.tagName || '') || node.getAttribute?.('role') === 'button';
    if (!control && node.children?.length) return [];
    const aria = node.getAttribute?.('aria-label') || '';
    const named = aria.match(/^(?:go to\s+)?page\s+(\d+)$/i);
    const text = (node.innerText || node.textContent || '').trim();
    return named ? [Number(named[1])] : /^\d+$/.test(text) ? [Number(text)] : [];
  }).filter(number => Number.isSafeInteger(number) && number > 0 && number <= MAX_PAGE_NUMBER);
  const numbers = (controlNumbers.length ? controlNumbers : (list.innerText.match(/\d+/g) || []))
    .map(Number)
    .filter((number) => Number.isSafeInteger(number) && number > 0 && number <= MAX_PAGE_NUMBER);
  return numbers.length ? Math.max(...numbers) : auxiliarySinglePageHint();
}

function auxiliarySinglePageHint() {
  if (location.href !== initialDocumentHref) initialDocumentDataCurrent = false;
  if (!role.auxiliary || !initialDocumentDataCurrent) return null;
  try {
    const script = document.querySelector('#__NEXT_DATA__');
    if (!script?.textContent) return null;
    const data = JSON.parse(script.textContent).props?.pageProps;
    const variables = data?.initialSearchQueryVariables;
    const result = data?.initialData?.searchResult;
    const url = new URL(location.href);
    const query = url.searchParams.get('queryNups') || url.searchParams.get('q') || '';
    const category = value => !value || value === '0' ? '' : String(value);
    if (!variables || String(variables.query || '') !== query ||
        category(variables.catId) !== category(url.searchParams.get('catId')) ||
        String(variables.facet || '') !== (url.searchParams.get('facet') || '') ||
        Number(variables.page || 1) !== currentPage()) return null;
    return result?.paginationV2?.maxPage === 1 && result.hasMorePages === false ? 1 : null;
  } catch { return null; }
}

function sweepStorageKey() {
  return Number.isInteger(role.tabId) ? `sweep:${role.tabId}` : null;
}

function freshSweep(generation = role.generation) {
  return {
    generation,
    total: null,
    visited: [],
    endRetries: {},
    startedAt: Date.now(),
    lastProgressAt: 0
  };
}

async function readSweep(generation = role.generation) {
  const key = sweepStorageKey();
  if (!key) return freshSweep(generation);
  const stored = await readLocal([key, 'sweepMode']);
  const sweep = stored[key];
  if (sweep && sweep.generation === generation && Array.isArray(sweep.visited) &&
      (!role.auxiliary || sweep.targetKey === role.auxiliary.targetKey)) {
    currentSweepMode = sweepRecordMode(sweep);
    currentPaginationFallback = sweep.paginationFallback === true;
    return { ...freshSweep(generation), ...sweep };
  }
  const mode = role.auxiliary ? 'none' : normalizeSweepMode(stored.sweepMode);
  currentSweepMode = mode;
  currentPaginationFallback = false;
  return {
    ...freshSweep(generation),
    mode,
    ...(role.auxiliary ? { targetKey: role.auxiliary.targetKey } : {}),
    alphabet: mode === 'sweep',
    query: sweepQueries(mode)[0] || '',
    completedPages: 0,
    completedQueries: 0,
    completedLetters: 0
  };
}

function auxiliaryCanAct(generation) {
  return isCurrent(generation) && !checkVerification() && onReviewerRoute() &&
    Date.now() - lastInteraction >= INTERACTION_GRACE_MS;
}

function deferFacetControls(delayMs = 5_000) {
  phase = 'waiting-user';
  lastActivityAt = Date.now();
  scheduleRelay(Math.max(1_000, Math.min(30_000, delayMs)), true);
}

async function prepareFacetControls(generation) {
  const href = location.href;
  const epoch = pagerFocusEpoch;
  if (!auxiliaryCanAct(generation)) {
    lastFacetFocusReason = Date.now() - lastInteraction < INTERACTION_GRACE_MS ? 'user-active' : 'not-authorized';
    if (isCurrent(generation) && !verificationObserved) deferFacetControls();
    return null;
  }
  const grant = await requestPreparationFocus(generation, Date.now() + 1_000, 'facet-controls');
  if (!auxiliaryCanAct(generation) || location.href !== href) {
    lastFacetFocusReason = 'cancelled';
    if (isCurrent(generation) && !verificationObserved) deferFacetControls();
    return null;
  }
  // Worker grants confirm selected-tab ownership independently of OS focus.
  if (!grant || (grant.enabled !== false && !grant.granted) ||
      (grant.enabled === false && grant.reason !== 'disabled') ||
      (Number.isFinite(grant?.until) && grant.until - Date.now() < 8_000)) {
    lastFacetFocusReason = !grant ? 'unavailable' : !grant.granted && grant.reason !== 'disabled'
      ? grant.reason || 'unknown' : 'short-lease';
    deferFacetControls();
    return null;
  }
  lastFacetFocusReason = 'none';
  return () => auxiliaryCanAct(generation) && epoch === pagerFocusEpoch &&
    location.href === href &&
    (!Number.isFinite(grant?.until) || Date.now() < grant.until - 100);
}

async function discoverAuxiliaryTargets(generation) {
  const assignment = role.auxiliary;
  if (!assignment || assignment.lane !== 'departments' || assignment.selection) return true;
  const sweep = await readSweep(generation);
  if (!isCurrent(generation)) return false;
  if (sweep.discoveryDone) return true;
  if (assignment.targetKind === 'brand') return true;
  const settings = await readLocal(['auxiliaryBrands']);
  if (!assignment.discoveryOnly && settings.auxiliaryBrands !== true) return true;
  if (!auxiliaryCanAct(generation)) {
    phase = 'waiting-user';
    scheduleRelay(INTERACTION_GRACE_MS, true);
    return false;
  }
  if (assignment.discoveryOnly && typeof ReviewerDepartmentData !== 'undefined') {
    // The unsearched HTML already contains the complete root department facet.
    // Reading it needs no popover, animation frames, foreground tab or input.
    const candidates = ReviewerDepartmentData.catalogue(document, location.href);
    if (!candidates) {
      await reportUnusable('Filter controls are unavailable: catalogue department data did not validate', generation);
      return false;
    }
    const reply = await sendToWorker({ type: 'reviewer:aux-discovered', generation, candidates });
    applyAckRole(reply);
    if (!reply?.ok || !isCurrent(generation)) return false;
    return writeSweep({ ...sweep, discoveryDone: true, discoveredDepartments: candidates.length }, generation);
  }
  const canAct = await prepareFacetControls(generation);
  if (!canAct) return false;
  try {
    if (typeof ReviewerFacetControls?.discover !== 'function') throw new Error('Filter controls are unavailable');
    const kinds = [
      ...(assignment.discoveryOnly ? ['department'] : []),
      ...(settings.auxiliaryBrands === true ? ['brand'] : [])
    ];
    let discoveredDepartments = 0;
    for (const kind of kinds) {
      const candidates = await ReviewerFacetControls.discover({ document, kind,
        currentUrl: location.href, canAct,
        pause: ms => new Promise(resolve => setTimeout(resolve, ms)) });
      if (kind === 'department') discoveredDepartments += candidates.length;
      if (!isCurrent(generation)) return false;
      const reply = await sendToWorker({ type: 'reviewer:aux-discovered', generation, candidates });
      applyAckRole(reply);
      if (!reply?.ok || !isCurrent(generation)) throw new Error(reply?.error || 'Could not save discovered filters');
    }
    return await writeSweep({ ...sweep, discoveryDone: true, discoveredDepartments }, generation);
  } catch (error) {
    if (isCurrent(generation) && !checkVerification()) {
      if (error?.code === 'cancelled' || !canAct() ||
          Date.now() - lastInteraction < INTERACTION_GRACE_MS) {
        deferFacetControls();
      } else {
        await reportUnusable(`Department discovery failed: ${error?.message || String(error)}`, generation);
      }
    }
    return false;
  }
}

async function selectAuxiliaryTarget(generation) {
  const selection = role.auxiliary?.selection;
  if (!selection || !isCurrent(generation) || !keepMonitoringScope()) return;
  if (selection.url) {
    const target = typeof ReviewerDepartmentData !== 'undefined'
      ? ReviewerDepartmentData.destination(selection.url, selection.parentUrl) : null;
    if (!target || selection.kind !== 'department') {
      await reportUnusable('The selected filter could not be authorized', generation);
      return;
    }
    // Use the same focus-before-navigation and user/verification guards as
    // normal pagination. A full load cannot ingest the parent's stale cards.
    await navigateWithFocus(() => location.assign(target), generation);
    return;
  }
  const canAct = await prepareFacetControls(generation);
  if (!canAct) return;
  const previous = itemSignature();
  const startedAt = Date.now();
  attemptedAuxiliarySelections.add(`${generation}:${role.auxiliary.targetKey}`);
  navigationPending = true;
  phase = 'waiting-target-change';
  lastNavigationAt = startedAt;
  let trafficPausedMs = 0;
  let selectionInFlight = true;
  let selectionIssued = false;
  const poll = async () => {
    if (checkVerification() || waitForTraffic() || !isCurrent(generation)) return;
    if (lastInteraction > startedAt) {
      navigationPending = false;
      phase = 'waiting-user';
      scheduleRelay(INTERACTION_GRACE_MS, true);
      return;
    }
    const changedUrl = auxiliaryScopeUrl(location.href) !== auxiliaryScopeUrl(selection.parentUrl);
    const items = collect();
    if (changedUrl && ((items.length && itemSignature() !== previous) ||
        (!items.length && atEndOfResults()))) {
      const token = documentSession.capture();
      try {
        const reply = await sendToWorker({ type: 'reviewer:aux-selected', generation, url: location.href });
        if (!documentSession.current(token) || !isCurrent(generation)) return;
        applyAckRole(reply);
        if (!reply?.ok) throw new Error(reply?.error || 'Could not authorize selected filter');
        navigationPending = false;
        if (isCurrent(generation)) scheduleRelay(0, true);
      } catch (error) {
        if (!documentSession.current(token) || !isCurrent(generation)) return;
        navigationPending = false;
        if (isCurrent(generation)) await reportUnusable(error?.message || String(error), generation);
      }
      return;
    }
    if (Date.now() - startedAt - trafficPausedMs >= EMPTY_PAGE_TIMEOUT_MS) {
      navigationPending = false;
      await reportUnusable('Selected filter did not produce a fresh results page within 30 seconds', generation);
      return;
    }
    actionTimer = setTimeout(() => { actionTimer = null; poll().catch(() => scheduleRelay(5_000, true)); }, 500);
    nextActionAt = Date.now() + 500;
    lastActivityAt = Date.now();
  };
  const continuation = trafficNavigation = { generation, resume(durationMs) {
    trafficPausedMs += durationMs;
    phase = 'waiting-target-change';
    if (selectionInFlight) return;
    if (!selectionIssued) {
      attemptedAuxiliarySelections.delete(`${generation}:${role.auxiliary.targetKey}`);
      navigationPending = false;
      trafficNavigation = null;
      phase = 'starting';
      startObserver();
      scheduleRelay(0, true);
      return;
    }
    actionTimer = setTimeout(() => { actionTimer = null; poll().catch(() => scheduleRelay(5_000, true)); }, 0);
    nextActionAt = Date.now();
  } };
  try {
    const result = await ReviewerFacetControls.select({ document, selection,
      canAct,
      activate: async label => {
        if (!canAct()) return { ok: false, reason: 'cancelled' };
        // Expanded menus can scroll independently of the page. Reveal the
        // already matched label, then let the background find its live hitbox.
        programmaticScrollUntil = Date.now() + 300;
        label.scrollIntoView({ behavior: 'instant', block: 'nearest', inline: 'nearest' });
        await new Promise(resolve => setTimeout(resolve, 100));
        if (!canAct() || !label.isConnected) return { ok: false, reason: 'cancelled' };
        const expected = { label, seen: new Set(), until: Date.now() + 10_000 };
        expectedFacetInput = expected;
        let reply;
        try {
          selectionIssued = true;
          reply = await sendToWorker({ type: 'reviewer:facet-input', generation,
            selection: { kind: selection.kind, label: selection.label, parentUrl: selection.parentUrl } });
          return reply;
        } finally {
          if (expectedFacetInput === expected) {
            // Chrome's release response can precede renderer click delivery.
            // Retain only the one expected label click for a bounded second;
            // unrelated, repeated, or keyboard input still cancels immediately.
            if (reply?.ok === true) expected.until = Math.min(expected.until, Date.now() + 1_000);
            else expectedFacetInput = null;
          }
        }
      },
      pause: ms => new Promise(resolve => setTimeout(resolve, ms)) });
    selectionInFlight = false;
    if (result?.ok) selectionIssued = true;
    if (waitForTraffic() || !isCurrent(generation)) return;
    if (!result?.ok) throw new Error(result?.reason || 'Filter is no longer available');
  } catch (error) {
    selectionInFlight = false;
    if (waitForTraffic()) return;
    navigationPending = false;
    if (trafficNavigation === continuation) trafficNavigation = null;
    if (isCurrent(generation) && !checkVerification()) {
      if (error?.message === 'cancelled' || !canAct() ||
          Date.now() - lastInteraction < INTERACTION_GRACE_MS) deferFacetControls();
      else await reportUnusable(`Filter selection failed: ${error?.message || String(error)}`, generation);
    }
    return;
  }

  await poll();
}

function resetAuxiliaryRenderWait() {
  const previous = auxiliaryRenderWait;
  auxiliaryRenderWait = null;
  if (previous?.grant?.granted) {
    releasePreparationFocus(previous.grant, previous.generation, previous.at, 'page-navigation').catch(() => {});
  }
}

// Count actual selected-tab rendering opportunity, rather than time spent
// queued behind other scanners or yielding to input. A separate opportunity
// deadline still bounds a document that never obtains a usable turn.
async function waitForAuxiliaryRender(generation, reason) {
  if (!isCurrent(generation) || checkVerification()) return false;
  const now = Date.now();
  const key = `${generation}:${location.href}`;
  if (auxiliaryRenderWait?.key !== key) {
    resetAuxiliaryRenderWait();
    auxiliaryRenderWait = { key, generation, startedAt: now, at: now, workMs: 0, grant: null, visible: false };
  }
  const wait = auxiliaryRenderWait;
  const inputAt = lastInteraction > wait.at ? lastInteraction : now;
  const until = wait.grant?.granted ? wait.grant.until
    : wait.visible && document.visibilityState === 'visible' ? now : wait.at;
  wait.workMs += Math.max(0, Math.min(now, inputAt, until) - wait.at);
  wait.at = now;
  const idleIn = INTERACTION_GRACE_MS - (now - lastInteraction);
  if (idleIn > 0) {
    wait.startedAt = now + Math.max(0, idleIn);
    if (wait.grant?.granted) await releasePreparationFocus(wait.grant, generation, now, 'page-navigation');
    if (!isCurrent(generation) || auxiliaryRenderWait !== wait || checkVerification()) return false;
    wait.grant = null;
    wait.visible = false;
    phase = 'waiting-user';
    lastActivityAt = now;
    if (!verificationObserved) scheduleRelay(Math.max(1_000, idleIn), true);
    return false;
  }
  if (wait.workMs >= EMPTY_PAGE_TIMEOUT_MS || now - wait.startedAt >= 300_000) {
    const failureCode = wait.workMs >= EMPTY_PAGE_TIMEOUT_MS ? 'render-timeout' : 'render-opportunity-timeout';
    resetAuxiliaryRenderWait();
    await reportUnusable(reason, generation, false, { failureCode });
    return false;
  }
  if (!wait.grant?.granted || wait.grant.until <= now) {
    if (wait.grant?.granted) await releasePreparationFocus(wait.grant, generation, now, 'page-navigation');
    if (!isCurrent(generation) || auxiliaryRenderWait !== wait || checkVerification()) return false;
    wait.grant = null;
    phase = 'waiting-focus';
    const grant = await requestPreparationFocus(generation, now + 3_000, 'page-navigation');
    if (!isCurrent(generation) || auxiliaryRenderWait !== wait || checkVerification()) {
      await releasePreparationFocus(grant, generation, Date.now(), 'page-navigation');
      return false;
    }
    wait.at = Date.now();
    wait.grant = grant;
    wait.visible = grant?.enabled === false && grant.reason === 'disabled' && document.visibilityState === 'visible';
    if (Date.now() - lastInteraction < INTERACTION_GRACE_MS) return waitForAuxiliaryRender(generation, reason);
    lastFacetFocusReason = grant?.granted ? 'none' : grant?.reason || 'unavailable';
  }
  phase = wait.grant?.granted || wait.visible ? 'waiting-content' : 'waiting-focus';
  lastActivityAt = Date.now();
  scheduleRelay(wait.grant?.granted ? Math.max(100, Math.min(EMPTY_POLL_MS, wait.grant.until - Date.now()))
    : Math.max(1_000, Math.min(5_000, wait.grant?.retryAfterMs || EMPTY_POLL_MS)), true);
  return false;
}

function documentHttpFailure() {
  // Navigation timing describes the full document, not later SPA responses.
  if (location.href !== initialDocumentHref) return 0;
  try {
    const status = performance?.getEntriesByType?.('navigation')[0]?.responseStatus;
    return Number.isInteger(status) && status >= 400 && status <= 599 ? status : 0;
  } catch { return 0; }
}

async function ensureAuxiliaryTarget(generation) {
  const assignment = role.auxiliary;
  if (!assignment) return true;
  // A filter may perform a full document navigation instead of a client route
  // change. This fresh document has no old cards to unlock; the coordinator
  // still validates its pending selection before allowing any ingestion.
  if (assignment.selection && !navigationPending && onReviewerRoute() &&
      auxiliaryScopeUrl(location.href) !== auxiliaryScopeUrl(assignment.targetUrl)) {
    const filterMatches = !assignment.selection.url || (typeof ReviewerDepartmentData !== 'undefined' &&
      ReviewerDepartmentData.applied(document, assignment.selection.label, assignment.selection.url));
    if (attemptedAuxiliarySelections.has(`${generation}:${assignment.targetKey}`)) {
      await reportUnusable('The filter changed without a confirmed fresh results page', generation);
    } else if (filterMatches && (collect().length || atEndOfResults())) {
      const reply = await sendToWorker({ type: 'reviewer:aux-selected', generation, url: location.href });
      applyAckRole(reply);
      if (!reply?.ok && isCurrent(generation)) await reportUnusable('The selected filter could not be authorized', generation);
      if (reply?.ok && role.active) scheduleRelay(0, true);
    } else {
      recordPageState('empty-observed');
      if (filterMatches) return waitForAuxiliaryRender(generation,
        'The selected filter document did not render within its active rendering budget');
      if (!emptySince) emptySince = Date.now();
      if (Date.now() - emptySince >= EMPTY_PAGE_TIMEOUT_MS) {
        await reportUnusable('The selected filter could not be authorized', generation);
      } else {
        phase = 'waiting-content';
        scheduleRelay(EMPTY_POLL_MS, true);
      }
    }
    return false;
  }
  if (!keepMonitoringScope()) return false;
  const dataNavigation = typeof ReviewerDepartmentData !== 'undefined' &&
    (assignment.discoveryOnly || assignment.selection?.url);
  if (!dataNavigation && (assignment.selection || assignment.discoveryOnly || assignment.lane === 'departments')) {
    if (!collect().length && !atEndOfResults()) {
      recordPageState('empty-observed');
      return waitForAuxiliaryRender(generation,
        'Auxiliary target did not render within its active rendering budget');
    }
  }
  if (collect().length || atEndOfResults()) resetAuxiliaryRenderWait();
  if (assignment.selection) {
    const wait = await pageDelayMs();
    if (isCurrent(generation)) queueWhenIdle(() => {
      selectAuxiliaryTarget(generation).catch(error => reportUnusable(error?.message || String(error), generation));
    }, wait, 'waiting-selection');
    return false;
  }
  if (!(await discoverAuxiliaryTargets(generation))) return false;
  if (assignment.discoveryOnly) {
    if (await removeCurrentSweep(generation, 0) === false) return false;
    if (isCurrent(generation)) phase = 'complete';
    return false;
  }
  return true;
}

async function ensureSweepSearch(generation) {
  const sweep = await readSweep(generation);
  if (!isCurrent(generation)) return false;
  if (!sweepQueries(sweepRecordMode(sweep)).length) return keepMonitoringScope();
  const params = new URLSearchParams(location.search);
  if ((params.get('queryNups') || params.get('q')) === sweep.query &&
      (sweep.visited.length || currentPage() === 1)) return true;
  if (!(await writeSweep(sweep, generation))) return false;
  const wait = await pageDelayMs();
  if (isCurrent(generation)) {
    queueWhenIdle(() => navigateWithFocus(() => location.assign(searchUrl(sweep.query)), generation), wait);
  }
  return false;
}

async function writeSweep(sweep, generation = role.generation) {
  if (!isCurrent(generation)) return false;
  const token = documentSession.capture();
  const key = sweepStorageKey();
  if (!key) return false;
  await writeLocal({ [key]: { ...sweep, generation } });
  if (!isCurrent(generation) || !documentSession.current(token)) return false;
  // Persist the URL and acknowledged progress before a new document/tab can
  // replace this one. The worker validates the current primary lease.
  const checkpoint = await sendDocumentMessage({ type: 'reviewer:checkpoint', generation }).catch(() => null);
  if (!checkpoint?.ok) {
    if (isCurrent(generation) && documentSession.current(token)) {
      phase = 'relay-retry';
      lastActivityAt = Date.now();
      scheduleRelay(5_000, true);
    }
    return false;
  }
  return isCurrent(generation) && documentSession.current(token);
}

async function removeCurrentSweep(generation, pages) {
  if (!isCurrent(generation)) return;
  const key = sweepStorageKey();
  if (!key) return;
  const timingKeys = ['lastSweepDone', 'lastSweepStartedAt', 'lastSweepPages'];
  const stored = await readLocal([key, ...timingKeys]);
  if (!completionStillCurrent(stored[key])) return false;
  const auxiliary = role.auxiliary;
  const restore = async () => {
    await writeLocal({ [key]: stored[key], ...Object.fromEntries(timingKeys
      .filter(name => Object.hasOwn(stored, name)).map(name => [name, stored[name]])) });
    const absent = timingKeys.filter(name => !Object.hasOwn(stored, name));
    if (absent.length) await removeLocal(absent);
    if (isCurrent(generation)) scheduleRelay(0, true);
    return false;
  };
  // The auxiliary coordinator removes its checkpoint only after accepting
  // completion. A suspended worker must not erase acknowledged page coverage.
  if (!auxiliary && stored[key]?.generation === generation) await removeLocal(key);
  if (!isCurrent(generation)) return;
  if (!completionStillCurrent(stored[key], false)) return restore();
  if (!role.auxiliary) {
    await writeLocal({
      lastSweepDone: Date.now(),
      lastSweepStartedAt: stored[key]?.startedAt || Date.now(),
      lastSweepPages: pages
    });
    if (!isCurrent(generation)) return false;
    if (!completionStillCurrent(stored[key], false)) return restore();
  }
  const coverageLimited = Boolean(auxiliary && !auxiliary.discoveryOnly &&
    !hasTerminalCoverage(stored[key]));
  const reply = await sendDocumentMessage({ type: 'reviewer:sweep-complete', generation, pages,
    ...(auxiliary ? { coverageLimited, ...(stored[key]?.emptyResult === true ? { emptyResult: true } : {}) } : {}) }).catch(() => null);
  if (auxiliary) {
    applyAckRole(reply);
    if (!reply?.ok && isCurrent(generation)) {
      phase = 'relay-retry';
      scheduleRelay(5_000, true);
      return false;
    }
  }
  return true;
}

function nextUnvisited(sweep) {
  const seen = new Set((sweep.visited || []).filter((page) => Number.isInteger(page) && page > 0));
  const highest = seen.size ? Math.max(...seen) : 0;
  const terminal = terminalPage(sweep);
  const boundary = terminal ?? Math.max(highest, sweep.total || 0);
  for (let page = 1; page <= Math.min(boundary, MAX_PAGE_NUMBER); page += 1) {
    if (!seen.has(page)) return page;
  }
  if (terminal !== null) {
    // Gap recovery can outlive the original tail observation. Revisit that
    // frontier after filling the gaps so a growing catalogue is checked again.
    const proofPage = terminal + (sweep.terminalReason === 'confirmed-empty' ? 1 : 0);
    return sweep.lastObservedPage === proofPage ? null : proofPage;
  }
  if (boundary >= MAX_PAGE_NUMBER) return null;
  // Keep the old safety budget for blind URL probing. Recognized later pages
  // or a native Next let a real department continue beyond 100. Exhausting
  // this budget is unresolved coverage, never a successful completion.
  if (highest >= UNPROVEN_PAGE_LIMIT && !sweep.nextPageAvailable &&
      !(sweep.total > highest)) return null;
  return highest + 1;
}

function terminalPage(sweep) {
  return Number.isSafeInteger(sweep?.terminalPage) && sweep.terminalPage >= 0 &&
    sweep.terminalPage <= MAX_PAGE_NUMBER &&
    ['disabled-next', 'omitted-next', 'single-page', 'confirmed-empty'].includes(sweep.terminalReason)
    ? sweep.terminalPage : null;
}

function hasTerminalCoverage(sweep) {
  const last = terminalPage(sweep);
  if (last === null) return false;
  const seen = new Set(sweep.visited || []);
  for (let page = 1; page <= last; page += 1) if (!seen.has(page)) return false;
  return true;
}

function completionStillCurrent(sweep, retry = true) {
  if (!hasTerminalCoverage(sweep)) return true; // Unresolved/discovery reports have no success proof.
  const page = currentPage();
  const next = paginationNextState(page);
  const last = terminalPage(sweep);
  const current = sweep.emptyResult === true ? auxiliaryEmptyState(sweep, role.generation) !== null
    : sweep.terminalReason === 'confirmed-empty'
    ? page === last + 1 && next !== 'enabled' && atEndOfResults() && !collect().length
    : page === last && (totalPages() || 0) <= page &&
      (sweep.terminalReason === 'disabled-next' ? next === 'disabled'
        : sweep.terminalReason === 'omitted-next' ? next === 'omitted' && stableTerminalPager(last, role.generation)
        : next !== 'enabled' && auxiliarySinglePageHint() === 1);
  if (current) return true;
  // Controls/cards can hydrate while the durable checkpoint awaits its ACK.
  // Re-read this page rather than completing from a pre-ACK observation.
  phase = 'waiting-content';
  lastActivityAt = Date.now();
  if (retry) scheduleRelay(0, true);
  return false;
}

function cancelPageWork() {
  navigationRecoveryRevision += 1;
  clearTimeout(trafficWaitTimer);
  trafficWaitTimer = null;
  trafficWaitStartedAt = null;
  trafficNavigation = null;
  pendingFocusRetry = null;
  resetAuxiliaryRenderWait();
  cancelFocusPreparation();
  cancelPageControlScroll();
  cancelResultsScroll();
  cancelVisibleObservation();
  clearTimeout(relayTimer);
  clearTimeout(actionTimer);
  relayTimer = null;
  actionTimer = null;
  nextActionAt = 0;
  navigationPending = false;
  navigationTarget = null;
}

function scheduleRelay(delayMs = 0, replace = false) {
  // A completed sweep stays quiescent until the coordinator starts the next
  // one. Walmart's continuously mutating widgets must not wake an endless
  // duplicate sweep after progress has been cleared.
  if (!isCurrent() || actionTimer || phase === 'complete') return;
  if (relayTimer && !replace) return;
  const token = documentSession.capture();
  clearTimeout(relayTimer);
  relayTimer = setTimeout(() => {
    relayTimer = null;
    relay().catch((err) => {
      if (!documentSession.current(token) || !isCurrent()) return;
      console.warn('Reviewer Item Relay: relay failed', err);
      phase = 'relay-retry';
      lastActivityAt = Date.now();
      scheduleRelay(5_000, true);
    });
  }, Math.max(0, delayMs));
}

function queueWhenIdle(action, delayMs, waitingPhase = 'waiting-navigation', focusRetry = false) {
  if (!extensionAvailable()) return;
  clearTimeout(actionTimer);
  pendingFocusRetry = null;
  const generation = role.generation;
  phase = waitingPhase;
  lastActivityAt = Date.now();

  const attempt = () => {
    if (checkVerification()) return;
    if (!isCurrent(generation)) {
      actionTimer = null;
      return;
    }
    if (!keepMonitoringScope()) return;
    if (relaying || documentSession.visible.retries > 0) {
      // Do not leave late-rendered cards behind while their local delivery is
      // still in flight or awaiting a retry. This adds no Walmart requests.
      actionTimer = setTimeout(attempt, 1_000);
      nextActionAt = Date.now() + 1_000;
      return;
    }
    const idleIn = INTERACTION_GRACE_MS - (Date.now() - lastInteraction);
    if (idleIn > 0) {
      // Keep the pending action alive instead of dropping it and leaving a
      // persisted sweep record that blocks every later refresh.
      phase = 'waiting-user';
      lastActivityAt = Date.now();
      actionTimer = setTimeout(attempt, idleIn + 100);
      nextActionAt = Date.now() + idleIn + 100;
      return;
    }
    actionTimer = null;
    pendingFocusRetry = null;
    nextActionAt = 0;
    cancelPageControlScroll();
    phase = 'navigating';
    lastActivityAt = Date.now();
    return action();
  };
  actionTimer = setTimeout(attempt, Math.max(0, delayMs));
  nextActionAt = Date.now() + Math.max(0, delayMs);
  if (focusRetry) pendingFocusRetry = { token: documentSession.capture(), attempt };
}

async function pageDelayDetails() {
  const settings = await readLocal([
    'pageDelaySeconds', 'refreshMinutes', 'botCheck', 'nativeNavigationPausedUntil',
    'nativeNavigationFailures'
  ]);
  readNativePolicy(settings);
  return pageNavigationDelay(settings, await readSweep(), lastNavigationAt);
}

function readNativePolicy(settings) {
  nativeNavigationPausedUntil = Number.isFinite(settings.nativeNavigationPausedUntil)
    ? Math.min(settings.nativeNavigationPausedUntil, Date.now() + NATIVE_NAVIGATION_BACKOFF_MS) : 0;
  nativeNavigationFailures = Object.fromEntries(Object.entries(settings.nativeNavigationFailures || {})
    .filter(([key, value]) => key.length <= 4096 && Number.isFinite(value?.until) &&
      value.until > Date.now() - 86400_000 && Number.isInteger(value.failures) && value.failures > 0)
    .sort((a, b) => b[1].until - a[1].until).slice(0, 20)
    .map(([key, value]) => [key, { until: Math.min(value.until, Date.now() + NATIVE_NAVIGATION_BACKOFF_MS),
      failures: Math.min(5, value.failures) }]));
}

function nativeCoolingDown(target, control) {
  return nativeNavigationPausedUntil > Date.now() ||
    nativeNavigationFailures[nativeControlKey(target, control)]?.until > Date.now();
}

function cancelFocusPreparation() {
  pagerFocusEpoch += 1;
  clearTimeout(pagerFocusTimer);
  pagerFocusTimer = null;
  for (const cancel of focusRequests) cancel();
  focusRequests.clear();
}

async function requestPreparationFocus(generation, expiresAt, purpose) {
  let timer;
  let cancel;
  const startedAt = Date.now();
  const operation = purpose || 'pager';
  const waitKey = `${generation}:${location.href}:${operation}`;
  if (focusWait?.key !== waitKey) focusWait = { key: waitKey, at: startedAt };
  const waitingSince = focusWait.at;
  try {
    const reply = await Promise.race([
      sendToWorker({ type: 'reviewer:focus', generation, expiresAt, ...(purpose ? { purpose } : {}) }),
      new Promise(resolve => {
        cancel = () => { clearTimeout(timer); resolve(null); };
        focusRequests.add(cancel);
        const limit = ['page-scroll', 'page-navigation'].includes(purpose) ? 3_000 : 1_000;
        timer = setTimeout(cancel, Math.max(0, Math.min(limit, expiresAt - Date.now())));
      })
    ]);
    if (reply?.granted && revokedSelectionLeases.has(reply.leaseId)) {
      return { ok: true, enabled: true, granted: false, reason: 'revoked', retryAfterMs: 1_000 };
    }
    if (reply?.granted) {
      if (Number.isFinite(reply.until)) lastSelectionUntil = reply.until;
      lastSelectionLeaseId = typeof reply.leaseId === 'string' ? reply.leaseId : null;
    }
    diagnostic(reply?.granted ? 'focus-granted' : 'focus-denied', {
      operation, reason: reply?.granted ? undefined : reply?.reason || 'unavailable',
      durationMs: Date.now() - startedAt,
      elapsedMs: Math.max(0, Date.now() - waitingSince),
      delayMs: Math.max(0, (reply?.until || Date.now()) - Date.now()) });
    if (reply?.granted && focusWait?.key === waitKey) focusWait = null;
    return reply;
  } catch { return null; } // The caller keeps the current page until focus is available.
  finally { clearTimeout(timer); focusRequests.delete(cancel); }
}

async function releasePreparationFocus(grant, generation, startedAt, operation) {
  if (!grant?.granted || typeof grant.leaseId !== 'string') return;
  if (lastSelectionLeaseId === grant.leaseId) {
    lastSelectionLeaseId = null;
    lastSelectionUntil = 0;
  }
  let timer;
  try {
    const reply = await Promise.race([
      sendToWorker({ type: 'reviewer:focus-release', generation, leaseId: grant.leaseId }),
      new Promise(resolve => { timer = setTimeout(() => resolve(null), 1_000); })
    ]);
    diagnostic('focus-released', { operation, count: reply?.released ? 1 : 0,
      durationMs: Math.max(0, Date.now() - startedAt),
      delayMs: Math.max(0, (grant.until || Date.now()) - Date.now()) });
  } catch { /* The finite lease still expires if a replaced worker cannot reply. */ }
  finally { clearTimeout(timer); }
}

// Selected-tab grants remain valid when Chrome is behind another application.
// The worker revokes them on tab changes; OS focus and visibility are separate.
function scannerSelectionCurrent(grant) {
  return !Number.isFinite(grant?.until) || grant.until > Date.now();
}

async function navigateWithFocus(action, generation, sourceHref = location.href, onSourceChange = null) {
  const current = () => isCurrent(generation) && location.href === sourceHref &&
    !checkVerification() && keepMonitoringScope();
  const reconcile = () => {
    if (onSourceChange && isCurrent(generation) && location.href !== sourceHref &&
        !checkVerification() && keepMonitoringScope()) return onSourceChange();
  };
  if (!current()) return reconcile();
  const needsReading = () => {
    if (!onSourceChange && documentSession.reading.completed === `${generation}:${location.href}` &&
        !resultsReadingConfirmed(generation)) {
      navigationPending = false;
      phase = 'waiting-content';
      scheduleRelay(0, true);
      return true;
    }
    return false;
  };
  if (needsReading()) return;
  const epoch = pagerFocusEpoch;
  navigationPending = true;
  phase = 'waiting-focus';
  lastActivityAt = Date.now();
  const reply = await requestPreparationFocus(generation, Date.now() + 3_000, 'page-navigation');
  if (!current()) {
    if (onSourceChange && location.href !== sourceHref) return reconcile();
    if (isCurrent(generation)) {
      navigationPending = false;
      scheduleRelay(1_000, true);
    }
    return;
  }
  const idleIn = INTERACTION_GRACE_MS - (Date.now() - lastInteraction);
  if (needsReading()) return;
  if (epoch !== pagerFocusEpoch || idleIn > 0) {
    navigationPending = false;
    phase = 'waiting-user';
    scheduleRelay(Math.max(1_000, idleIn), true);
    return;
  }
  const staleGrant = reply?.granted && Number.isFinite(reply.until) && reply.until <= Date.now();
  if ((!reply || (reply.enabled !== false && !reply.granted) ||
        (reply.enabled === false && reply.reason !== 'disabled')) ||
      staleGrant) {
    lastFacetFocusReason = staleGrant ? 'expired' : reply?.reason || 'unavailable';
    navigationPending = false;
    const delayMs = reply?.granted ? 100 : Math.max(1_000, Math.min(5_000, reply?.retryAfterMs || 5_000));
    queueWhenIdle(() => navigateWithFocus(action, generation, sourceHref, onSourceChange), delayMs, 'waiting-user', true);
    return;
  }
  lastFacetFocusReason = 'none';
  phase = 'navigating';
  lastActivityAt = Date.now();
  return action();
}

function cancelResultsScroll() {
  documentSession.reading.controller?.abort();
  documentSession.reading.controller = null;
}

function resultsReadingStamp() {
  const heights = [document.scrollingElement?.scrollHeight, document.documentElement?.scrollHeight,
    document.body?.scrollHeight].filter(value => Number.isFinite(value) && value >= 0);
  // Reading proves a settled bottom. Later pager preparation can move upward
  // without changing that proof; newly rendered cards or geometry still void it.
  return JSON.stringify([itemSignature(), Math.max(0, ...heights), window.innerHeight || 0,
    document.readyState || '']);
}

function resultsReadingConfirmed(generation) {
  return documentSession.reading.completed === `${generation}:${location.href}` &&
    documentSession.reading.proof === resultsReadingStamp();
}

// Read each delivered results page before advancing, including the last page
// of a search. Automatic rotation must actually obtain a selected-tab turn;
// a denied turn or an unfinished animation never acknowledges page coverage.
async function scrollResultsPage(generation) {
  const href = location.href;
  const key = `${generation}:${href}`;
  if (resultsReadingConfirmed(generation)) return true;
  if (documentSession.reading.completed === key) documentSession.reading.completed = null;
  if (documentSession.reading.attemptKey !== key) {
    documentSession.reading.attemptKey = key;
    documentSession.reading.workMs = documentSession.reading.failures = 0;
  }
  const defer = (reason, delayMs = 1_000, waitingPhase = 'waiting-focus') => {
    lastFacetFocusReason = reason;
    phase = waitingPhase;
    lastActivityAt = Date.now();
    scheduleRelay(Math.max(100, Math.min(5_000, delayMs)), true);
    return false;
  };
  const unavailable = async () => {
    if (++documentSession.reading.failures >= 3) {
      await reportUnusable('Results reading could not complete after three attempts', generation);
      return false;
    }
    return defer('unavailable', 2_000, 'waiting-content');
  };
  if (typeof ReviewerPageScroll === 'undefined') return unavailable();
  const valid = () => isCurrent(generation) && !verificationObserved && location.href === href &&
    keepMonitoringScope() && Date.now() - lastInteraction >= INTERACTION_GRACE_MS;
  if (!valid() || checkVerification()) {
    if (isCurrent(generation) && !verificationObserved && location.href === href) {
      phase = 'waiting-user';
      scheduleRelay(Math.max(1_000, INTERACTION_GRACE_MS - (Date.now() - lastInteraction)), true);
    }
    return false;
  }
  const grant = await requestPreparationFocus(generation, Date.now() + 3_000, 'page-scroll');
  const grantedAt = Date.now();
  try {
    if (!valid() || checkVerification()) {
      if (isCurrent(generation) && !verificationObserved && location.href === href) {
        phase = 'waiting-user';
        scheduleRelay(Math.max(1_000, INTERACTION_GRACE_MS - (Date.now() - lastInteraction)), true);
      }
      return false;
    }
    const disabled = grant?.enabled === false && grant.reason === 'disabled';
    // Explicitly disabling rotation retains its documented background mode.
    if (disabled && document.visibilityState !== 'visible') {
      documentSession.reading.completed = key;
      documentSession.reading.proof = resultsReadingStamp();
      return true;
    }
    if (!grant || grant.reason === 'activation-unavailable') return unavailable();
    if ((!disabled && !grant?.granted) || !scannerSelectionCurrent(grant)) {
      return defer(grant?.until && grant.until <= Date.now() ? 'expired' : grant?.reason ||
        'unavailable',
        grant?.retryAfterMs || 1_000);
    }
    lastFacetFocusReason = 'none';
    cancelResultsScroll();
    const controller = new AbortController();
    documentSession.reading.controller = controller;
    const startAtTop = documentSession.reading.started !== key;
    programmaticScroll = true;
    phase = 'reading-page';
    try {
      const budgetMs = grant?.until ? Math.min(8_000, grant.until - Date.now() - 200) : 8_000;
      if (budgetMs < 100) {
        lastFacetFocusReason = 'short-lease';
        phase = 'waiting-user';
        scheduleRelay(1_000, true);
        return false;
      }
      documentSession.reading.started = key;
      const result = await ReviewerPageScroll.run({ window, document, signal: controller.signal,
        startAtTop, budgetMs, settleKey: itemSignature,
        canContinue: () => valid() && scannerSelectionCurrent(grant),
        onState() { lastActivityAt = Date.now(); } });
      if (!valid() || controller.signal.aborted || result.outcome === 'cancelled') {
        if (isCurrent(generation) && !verificationObserved && location.href === href) {
          phase = 'waiting-user';
          scheduleRelay(Math.max(1_000, INTERACTION_GRACE_MS - (Date.now() - lastInteraction)), true);
        }
        return false;
      }
      // Chrome can suspend frames and heavily delay timers in a background
      // document. Scheduler sleep is not evidence of an unusable results page.
      const schedulerDelayed = result.outcome === 'timeout' && result.durationMs > budgetMs + 1_000;
      if (schedulerDelayed) {
        documentSession.reading.failures = 0;
        return defer('none', 1_000, 'reading-page');
      }
      documentSession.reading.workMs += Math.max(0, Math.min(8_000, Number(result.durationMs) || 0));
      if (result.outcome !== 'complete') {
        if (documentSession.reading.workMs >= 120_000) {
          await reportUnusable('Results page did not settle within its bounded reading budget', generation);
          return false;
        }
        if (result.outcome !== 'timeout') return unavailable();
        documentSession.reading.failures = 0;
        return defer('none', 1_000, 'reading-page');
      }
      if (!scannerSelectionCurrent(grant)) {
        return defer('expired');
      }
      documentSession.reading.completed = key;
      documentSession.reading.proof = resultsReadingStamp();
      documentSession.reading.failures = 0;
      return true;
    } catch {
      // Already relayed items stay deduplicated; an animation error is not proof
      // that the rest of the page loaded. Recovery is bounded to this target.
      return valid() ? unavailable() : false;
    } finally {
      if (documentSession.reading.controller === controller) documentSession.reading.controller = null;
      programmaticScrollUntil = Date.now() + 250;
      programmaticScroll = false;
    }
  } finally {
    await releasePreparationFocus(grant, generation, grantedAt, 'page-scroll');
  }
}

async function preparePageNavigation(page, generation, waitMs) {
  cancelFocusPreparation();
  if (waitMs < 1_000 || !isCurrent(generation) || checkVerification() ||
      !keepMonitoringScope() || Date.now() - lastInteraction < INTERACTION_GRACE_MS) return;
  const target = pageUrl(page);
  const control = findPageControl(target, page);
  if (!control || nativeCoolingDown(target, control)) return;
  const epoch = pagerFocusEpoch;
  const deadline = Date.now() + waitMs;
  const prepare = async () => {
    if (epoch !== pagerFocusEpoch || !isCurrent(generation) || checkVerification() ||
        Date.now() > deadline - 1_000 || !keepMonitoringScope() ||
        Date.now() - lastInteraction < INTERACTION_GRACE_MS) return;
    const reply = await requestPreparationFocus(generation, Math.min(deadline - 900, Date.now() + 1_000));
    if (epoch !== pagerFocusEpoch || !isCurrent(generation) || checkVerification() ||
        !keepMonitoringScope() || Date.now() > deadline - 1_000 ||
        Date.now() - lastInteraction < INTERACTION_GRACE_MS) return;
    if ((reply?.enabled && !reply.granted) || !scannerSelectionCurrent(reply)) {
      const retry = reply?.granted ? 100 : Math.max(100, Number(reply?.retryAfterMs) || 1_000);
      if (Date.now() + retry <= deadline - 1_000) pagerFocusTimer = setTimeout(() => {
        pagerFocusTimer = null;
        prepare().catch(() => {});
      }, retry);
      return;
    }
    // The animation fits inside the already scheduled wait. Real input
    // cancels movement and still postpones the queued navigation.
    const previous = programmaticScrollUntil;
    programmaticScrollUntil = Date.now() + 1_000;
    if (!scrollPageControlIntoView(control, () => isCurrent(generation) &&
        keepMonitoringScope() && Date.now() < programmaticScrollUntil &&
        Date.now() - lastInteraction >= INTERACTION_GRACE_MS)) programmaticScrollUntil = previous;
  };
  await prepare();
}

async function handleNativeRestart(msg) {
  // A just-loaded final page can finish without requesting a next-page delay.
  // Read durable failures here too, before promising a native restart.
  const settings = await readLocal([
    'nativeNavigationPausedUntil', 'nativeNavigationFailures', 'sweepMode', 'pathPattern'
  ]);
  readNativePolicy(settings);
  currentSweepMode = role.auxiliary ? 'none' : normalizeSweepMode(settings.sweepMode);
  reviewerPathPattern = settings.pathPattern || DEFAULT_PATH_PATTERN;
  let accepted = false;
  try {
    const control = findPageControl(msg.target, 1);
    accepted = msg.active === true && msg.tabId === role.tabId &&
      msg.primaryTabId === role.tabId && msg.generation === role.generation &&
      !relaying && !navigationPending && !checkVerification() && currentPage() > 1 &&
      onReviewerRoute() && keepMonitoringScope() &&
      comparablePageUrl(msg.target, location.href) === comparablePageUrl(pageUrl(1), location.href) &&
      Boolean(control) && !nativeCoolingDown(msg.target, control);
  } catch { /* Unknown layouts or targets use the worker's full-load fallback. */ }
  if (accepted) {
    applyRole(msg);
    cancelPageWork();
    queueWhenIdle(() => navigateWithFocus(() => navigatePage(1, msg.generation, true), msg.generation), 0);
  }
  return { ok: accepted, navigation: accepted ? 'native' : null, generation: role.generation };
}

async function pageDelayMs() {
  return (await pageDelayDetails()).delayMs;
}

function itemSignature() {
  const ids = new Set();
  for (const anchor of document.querySelectorAll(ITEM_LINK)) {
    const id = idFromHref(anchor.getAttribute('href') || '');
    if (!id || ids.has(id)) continue;
    // Normal cards expose their title on the link/image. Walk ancestors only
    // for the rare title fallback; signatures need no prices, stock or images.
    if (!titleFor(anchor, null) && !titleFor(anchor, cardFor(anchor))) continue;
    ids.add(id);
  }
  return [...ids].map(id => `ip-${id}`).sort().join('|');
}

function navigatePage(page, generation, scanRestart = false, { retry = 0, bypassCooldown = false } = {}) {
  if (!isCurrent(generation) || checkVerification() || !keepMonitoringScope()) return;
  cancelVisibleObservation();
  const target = pageUrl(page);
  navigationTarget = target;
  const candidate = role.auxiliary?.urlPagination === true ? null : findPageControl(target, page);
  const nativeBackoff = candidate && nativeCoolingDown(target, candidate);
  if (nativeBackoff && !bypassCooldown) {
    // A cooldown is a wait before another bounded probe, not permission to
    // reload every subsequent page. Legacy ten-minute records get a probe
    // within one minute too; the probe never renews a record merely by reading it.
    const until = Math.max(nativeNavigationPausedUntil,
      nativeNavigationFailures[nativeControlKey(target, candidate)]?.until || 0);
    const delayMs = Math.max(1_000, Math.min(NATIVE_NAVIGATION_BACKOFF_MS, until - Date.now()));
    navigationPending = true;
    diagnostic('navigation-retry', { targetPage: page, reason: 'native-backoff', delayMs });
    queueWhenIdle(() => navigateWithFocus(() => navigatePage(page, generation, scanRestart,
      { retry, bypassCooldown: true }), generation), delayMs);
    return;
  }
  const control = candidate;
  if (!control) {
    const inspection = inspectPageControls(target, page);
    if (inspection) diagnostic('pager-inspection', { targetPage: page, ...inspection });
    diagnostic('navigation-fallback', { targetPage: page,
      reason: role.auxiliary?.urlPagination === true ? 'not-authorized' : 'missing-control' });
    if (scanRestart) diagnostic('scan-restart-load');
    if (!isCurrent(generation)) return;
    navigationPending = true;
    lastNavigationAt = Date.now();
    location.assign(target);
    return;
  }
  const previous = itemSignature();
  const startedAt = Date.now();
  let trafficPausedMs = 0;
  const controlKey = nativeControlKey(target, control);
  lastNavigationAt = startedAt;
  const startedUrl = comparablePageUrl(location.href, location.href);
  const fresh = () => {
    try {
      return comparablePageUrl(location.href, location.href) === comparablePageUrl(target, location.href) &&
        ((collect().length && itemSignature() !== previous) || atEndOfResults());
    } catch { return false; } // A detached/hydrating card cannot terminate the freshness watchdog.
  };
  let outcomeRecorded = false;
  const outcome = value => {
    if (outcomeRecorded) return;
    outcomeRecorded = true;
    diagnostic('navigation-outcome', { outcome: value, targetPage: page,
      documentId: RESULTS_DOCUMENT_ID, durationMs: Math.max(0, Date.now() - startedAt) });
  };
  const resumePoll = () => {
    navigationPending = true;
    phase = 'waiting-page-change';
    return poll();
  };
  const responseArrived = () => fresh() ||
    (comparablePageUrl(location.href, location.href) !== startedUrl && Date.now() - startedAt - trafficPausedMs < EMPTY_PAGE_TIMEOUT_MS);
  const stalled = async () => {
    const revision = navigationRecoveryRevision;
    if (!isCurrent(generation) || checkVerification() || !keepMonitoringScope()) return;
    if (lastInteraction > startedAt) {
      navigationPending = false;
      navigationTarget = null;
      phase = 'waiting-user';
      diagnostic('navigation-interrupted', { targetPage: page });
      outcome('interrupted');
      scheduleRelay(INTERACTION_GRACE_MS, true);
      return;
    }
    outcome('timeout');
    // Retry only a no-op. A route that already changed keeps its full render
    // allowance and must never be clicked twice over the previous page's cards.
    if (retry < 1 && comparablePageUrl(location.href, location.href) === startedUrl) {
      const delayMs = await pageDelayMs();
      if (revision !== navigationRecoveryRevision) return;
      if (!isCurrent(generation) || checkVerification() || !keepMonitoringScope()) return;
      diagnostic('navigation-retry', { targetPage: page, reason: 'native-stalled', delayMs,
        durationMs: Date.now() - startedAt });
      queueWhenIdle(() => {
        if (revision !== navigationRecoveryRevision) return;
        // A late first response can arrive during the paced wait. Let its
        // original freshness poll resolve it instead of clicking a second time.
        if (comparablePageUrl(location.href, location.href) !== startedUrl) return poll();
        return navigateWithFocus(() => {
          if (revision === navigationRecoveryRevision) return navigatePage(page, generation, scanRestart,
            { retry: 1, bypassCooldown: true });
        }, generation, location.href, () => {
          if (revision === navigationRecoveryRevision) return resumePoll();
        });
      }, delayMs);
      return;
    }
    const stored = await readLocal(['nativeNavigationFailures']);
    if (revision !== navigationRecoveryRevision) return;
    if (!isCurrent(generation) || checkVerification() || !keepMonitoringScope()) return;
    if (responseArrived()) return resumePoll();
    readNativePolicy({ nativeNavigationFailures: stored.nativeNavigationFailures });
    nativeNavigationFailures[controlKey] = { until: Date.now() + NATIVE_NAVIGATION_BACKOFF_MS,
      failures: Math.min(5, (nativeNavigationFailures[controlKey]?.failures || 0) + 1) };
    const bounded = Object.entries(nativeNavigationFailures).sort((a, b) => b[1].until - a[1].until).slice(0, 20);
    nativeNavigationFailures = Object.fromEntries(bounded);
    await writeLocal({ nativeNavigationFailures, nativeNavigationPausedUntil: 0 }).catch(() => {});
    if (revision !== navigationRecoveryRevision) return;
    if (!isCurrent(generation) || checkVerification() || !keepMonitoringScope()) return;
    if (responseArrived()) return resumePoll();
    if (lastInteraction > startedAt) {
      navigationPending = false;
      navigationTarget = null;
      phase = 'waiting-user';
      diagnostic('navigation-interrupted', { targetPage: page });
      outcome('interrupted');
      scheduleRelay(INTERACTION_GRACE_MS, true);
      return;
    }
    diagnostic('navigation-fallback', { targetPage: page, reason: 'native-stalled',
      durationMs: Date.now() - startedAt });
    if (scanRestart) diagnostic('scan-restart-load');
    if (!isCurrent(generation)) return;
    phase = 'navigating';
    await navigateWithFocus(() => {
      if (revision === navigationRecoveryRevision) location.assign(target);
    }, generation);
  };
  const recoverStalled = () => {
    const revision = navigationRecoveryRevision;
    return stalled().catch(async () => {
      if (revision !== navigationRecoveryRevision) return;
      if (!isCurrent(generation) || checkVerification()) return;
      navigationPending = false;
      navigationTarget = null;
      outcome('timeout');
      await reportUnusable('Native navigation could not recover within its bounded attempt', generation,
        false, { failureCode: 'navigation-timeout' });
    });
  };
  navigationPending = true;
  phase = 'waiting-page-change';
  diagnostic('navigation-native', { targetPage: page });
  if (!isCurrent(generation)) return;
  // Hold the relay lock across client-side URL changes: the old page's cards
  // may remain visible after the URL already says "page 2".
  const poll = () => {
    if (checkVerification() || waitForTraffic() || !isCurrent(generation)) return;
    if (lastInteraction > startedAt) {
      actionTimer = null;
      navigationPending = false;
      navigationTarget = null;
      phase = 'waiting-user';
      diagnostic('navigation-interrupted', { targetPage: page });
      outcome('interrupted');
      scheduleRelay(INTERACTION_GRACE_MS, true);
      return;
    }
    if (fresh()) {
      actionTimer = null;
      nextActionAt = 0;
      navigationPending = false;
      navigationTarget = null;
      phase = 'waiting-content';
      lastActivityAt = Date.now();
      diagnostic('navigation-ready', { targetPage: page, durationMs: Date.now() - startedAt });
      outcome('ready');
      if (scanRestart) diagnostic('scan-restart-native');
      if (!isCurrent(generation)) return;
      if (nativeNavigationFailures[controlKey]) {
        delete nativeNavigationFailures[controlKey];
        writeLocal({ nativeNavigationFailures }).catch(() => {});
      }
      scheduleRelay(1_000, true);
      return;
    }
    const elapsed = Date.now() - startedAt - trafficPausedMs;
    // Observed native pagination changes the URL before rendering new cards.
    // A no-op gets a short start window; a started navigation keeps the full
    // rendering allowance and can never acknowledge the previous page's cards.
    if (elapsed >= EMPTY_PAGE_TIMEOUT_MS ||
        (elapsed >= NATIVE_START_TIMEOUT_MS &&
          comparablePageUrl(location.href, location.href) === startedUrl)) {
      actionTimer = null;
      nextActionAt = 0;
      recoverStalled();
      return;
    }
    actionTimer = setTimeout(poll, 500);
    nextActionAt = Date.now() + 500;
  };
  trafficNavigation = { generation, resume(durationMs) {
    trafficPausedMs += durationMs;
    phase = 'waiting-page-change';
    actionTimer = setTimeout(poll, 0);
    nextActionAt = Date.now();
  } };
  actionTimer = setTimeout(poll, 500);
  nextActionAt = Date.now() + 500;
  try { control.click(); } catch {
    clearTimeout(actionTimer);
    actionTimer = null;
    nextActionAt = 0;
    recoverStalled();
  }
}

async function finishSweep(sweep, generation) {
  if (!completionStillCurrent(sweep)) return;
  const pages = (sweep.completedPages || 0) + new Set(sweep.visited || []).size;
  const mode = sweepRecordMode(sweep);
  const queries = sweepQueries(mode);
  const currentQuery = String(sweep.query || '').trim().toLocaleLowerCase('en-US');
  const currentIndex = queries.findIndex(query =>
    query.toLocaleLowerCase('en-US') === currentQuery);
  // An obsolete/unknown stored query is finished instead of wrapping to the
  // first query and scanning the same category pass again.
  const nextIndex = currentIndex < 0 ? queries.length : currentIndex + 1;
  if (queries.length && nextIndex < queries.length) {
    const restore = async () => {
      if (await writeSweep(sweep, generation)) scheduleRelay(0, true);
    };
    const updated = {
      ...sweep,
      mode,
      query: queries[nextIndex],
      completedQueries: nextIndex,
      completedLetters: mode === 'sweep' ? nextIndex : 0,
      completedPages: pages,
      total: null,
      terminalPage: undefined,
      terminalReason: undefined,
      nextPageAvailable: false,
      lastObservedPage: undefined,
      visited: [],
      endRetries: {},
      lastProgressAt: Date.now()
    };
    if (!(await writeSweep(updated, generation))) return;
    if (!completionStillCurrent(sweep, false)) {
      await restore();
      return;
    }
    const wait = await pageDelayMs();
    if (isCurrent(generation)) {
      queueWhenIdle(() => navigateWithFocus(async () => {
        if (!completionStillCurrent(sweep, false)) return restore();
        location.assign(searchUrl(updated.query));
      }, generation), wait);
    }
    return;
  }
  if (!(await writeSweep(sweep, generation))) return;
  if (!completionStillCurrent(sweep)) return;
  if (!pages && !role.auxiliary) recordPageState('zero-page-complete', { sweep });
  if (await removeCurrentSweep(generation, pages) === false) return;
  if (!isCurrent(generation)) return;
  if (!pages && !role.auxiliary) {
    // A sweep that relayed nothing used to end silently, so a page stuck on
    // the no-results panel produced no items, no error, and no clue -- the
    // relay was dead for eighteen hours and the status simply aged. Record it
    // so the options page and the staleness alert have something to show.
    await writeLocal({
      lastError:
        'A sweep finished without reading any pages: the reviewer page kept ' +
        'showing "no search results". If the catalogue is not actually empty, ' +
        'reload the tab.',
      lastErrorAt: Date.now()
    });
  }
  if (!isCurrent(generation)) return;
  phase = 'complete';
  lastActivityAt = Date.now();
  if (collect().length) scheduleVisibleObservation();
}

// Require stable, card-bound terminal evidence across separate reads. A changing
// pager or late cards resets this confirmation without another network request.
function stableTerminalPager(page, generation) {
  if (omittedNextTerminalPage() !== page) {
    terminalPagerObservation = null;
    return false;
  }
  const key = `${generation}:${location.href}:${page}:${itemSignature()}`;
  if (terminalPagerObservation?.key !== key || Date.now() < terminalPagerObservation.at) {
    terminalPagerObservation = { key, at: Date.now() };
    return false;
  }
  return Date.now() - terminalPagerObservation.at >= 1_000;
}

async function reconcileRenderedPagination(generation) {
  const rendered = omittedNextTerminalPage();
  const requested = currentPage();
  if (rendered === null || rendered === requested) return true;
  // A URL is not proof that Walmart served that page. Revisit the actual
  // terminal URL and fill interior gaps before permitting completion.
  if (!stableTerminalPager(rendered, generation)) {
    phase = 'waiting-content';
    scheduleRelay(1_000, true);
    return false;
  }
  const sweep = await readSweep(generation);
  if (!isCurrent(generation) || omittedNextTerminalPage() !== rendered) return false;
  const corrected = { ...sweep, total: rendered,
    visited: sweep.visited.filter(page => page <= rendered),
    terminalPage: undefined, terminalReason: undefined, lastObservedPage: undefined,
    lastPageFingerprint: undefined, nextPageAvailable: false };
  if (!(await writeSweep(corrected, generation))) return false;
  diagnostic('pager-clamped', { page: requested, targetPage: rendered });
  const timing = await pageDelayDetails();
  if (isCurrent(generation)) queueWhenIdle(() => navigateWithFocus(() => {
    if (omittedNextTerminalPage() !== rendered) { scheduleRelay(0, true); return; }
    return navigatePage(rendered, generation);
  }, generation), timing.delayMs);
  return false;
}

async function rejectRepeatedPage(generation, sweep = null) {
  sweep ||= await readSweep(generation);
  if (!isCurrent(generation)) return true;
  const page = currentPage();
  const fingerprint = itemSignature();
  if (fingerprint && Number.isInteger(sweep.lastObservedPage) && sweep.lastObservedPage !== page &&
      !sweep.visited.includes(page) && sweep.lastPageFingerprint === fingerprint) {
    diagnostic('page-repeat', { page, targetPage: sweep.lastObservedPage });
    await reportUnusable('Repeated results at a different page URL; coverage remains unresolved', generation);
    return true;
  }
  return false;
}

async function advanceDeliveredPage(generation) {
  if (!isCurrent(generation)) return;
  if (!(await reconcileRenderedPagination(generation))) return;
  const page = currentPage();
  let sweep = await readSweep(generation);
  if (!isCurrent(generation)) return;

  if (!keepMonitoringScope()) return;

  if (totalPages() === null &&
      Date.now() - lastInteraction >= INTERACTION_GRACE_MS) {
    await revealPager();
  }
  if (!isCurrent(generation)) return;

  // The pager can appear only after scrolling. Recheck the split before the
  // next navigation, and preserve its discovery checkpoint in this page save.
  if (role.auxiliary?.lane === 'departments') {
    if (!(await discoverAuxiliaryTargets(generation))) return;
    sweep = await readSweep(generation);
    if (!isCurrent(generation)) return;
  }

  const detected = totalPages();
  if (confirmedEmptyBoundary && page >= confirmedEmptyBoundary.emptyPage) {
    confirmedEmptyBoundary = null;
  }
  let total = Math.max(detected ?? 0, sweep.total ?? 0) || null;
  const nextState = paginationNextState(page);
  if (nextState === 'omitted' && !stableTerminalPager(page, generation)) {
    phase = 'waiting-content';
    scheduleRelay(1_000, true);
    return;
  }
  const fingerprint = itemSignature();
  if (await rejectRepeatedPage(generation, sweep)) return;
  let end = terminalPage(sweep);
  let terminalReason = sweep.terminalReason;
  if (end !== null && (page >= end || (detected || 0) > end)) {
    end = null;
    terminalReason = undefined;
  }
  if ((['disabled', 'omitted'].includes(nextState) && (detected || 0) <= page) ||
      (page === 1 && nextState !== 'enabled' && (detected || 0) <= 1 && auxiliarySinglePageHint() === 1)) {
    end = page;
    terminalReason = nextState === 'disabled' ? 'disabled-next' :
      nextState === 'omitted' ? 'omitted-next' : 'single-page';
    total = page;
  }
  const visited = [...new Set([...(sweep.visited || []), page])];
  const endRetries = { ...(sweep.endRetries || {}) };
  delete endRetries[page];
  endConfirmationsInDocument.delete(`${generation}:${sweep.query || ''}:${page}`);
  const updated = {
    ...sweep,
    generation,
    emptyResult: false,
    total,
    terminalPage: end ?? undefined,
    terminalReason,
    nextPageAvailable: nextState === 'enabled',
    lastObservedPage: page,
    lastPageFingerprint: fingerprint,
    visited,
    endRetries,
    lastProgressAt: Date.now()
  };
  await continueSweep(updated, generation);
}

async function continueSweep(sweep, generation) {
  if (!isCurrent(generation) || checkVerification()) return;
  const next = nextUnvisited(sweep);

  if (next === null) {
    if (!hasTerminalCoverage(sweep) && !role.auxiliary?.discoveryOnly) {
      // Preserve the checkpoint for the coordinator's durable retry ledger.
      // Home must likewise remain incomplete instead of recording success.
      diagnostic('coverage-unresolved', { page: currentPage(), pages: new Set(sweep.visited || []).size });
      if (!role.auxiliary) {
        if (await writeSweep(sweep, generation)) {
          await reportUnusable('Pagination end could not be verified; acknowledged pages retained', generation);
        }
        return;
      }
    }
    await finishSweep(sweep, generation);
    return;
  }
  if (!(await writeSweep(sweep, generation)) || !isCurrent(generation)) return;

  phase = 'waiting-navigation';
  lastActivityAt = Date.now();
  const timing = await pageDelayDetails();
  if (!isCurrent(generation)) return;
  diagnostic('pacing', { ...timing, targetPage: next });
  queueWhenIdle(() => navigateWithFocus(() => navigatePage(next, generation), generation), timing.delayMs);
  scheduleVisibleObservation();
  await preparePageNavigation(next, generation, timing.delayMs);
}

async function startPaginationFallback(generation, details = {}) {
  if (!isCurrent(generation) || checkVerification()) return false;
  if (role.auxiliary) return false;
  const sweep = await readSweep(generation);
  if (sweepRecordMode(sweep) !== 'none' || !isCurrent(generation) || !keepMonitoringScope()) return false;
  // Rendering can finish while storage/role checks are awaiting the worker.
  if (collect().length || (details.reason === 'empty-results' && !atEndOfResults())) {
    scheduleRelay(0, true);
    return true;
  }
  recordPageState('fallback', { sweep, attempt: details.attempt });
  const reply = await sendDocumentMessage({ type: 'reviewer:pagination-fallback', generation,
    page: currentPage(), totalPages: Math.max(totalPages() ?? 0, sweep.total ?? 0),
    ...details });
  applyAckRole(reply);
  if (!reply?.ok || !isCurrent(generation) || checkVerification()) return false;
  // The worker persists this round before authorizing its search URLs.
  emptySince = 0;
  confirmedEmptyBoundary = null;
  endConfirmationsInDocument.clear();
  await ensureSweepSearch(generation);
  return true;
}

async function handleEndPage(generation) {
  if (!isCurrent(generation) || checkVerification()) return;
  const token = documentSession.capture();
  const page = currentPage();
  const sweep = await readSweep(generation);
  if (!isCurrent(generation) || !documentSession.current(token) || checkVerification()) return;
  if (collect().length || !atEndOfResults()) {
    auxiliaryEmptyObservation = null;
    scheduleRelay(0, true);
    return;
  }

  if (await verifyAuxiliaryEmpty(sweep, generation)) return;
  if (!isCurrent(generation) || !documentSession.current(token) || checkVerification()) return;

  const detected = totalPages();
  const total = Math.max(detected ?? 0, sweep.total ?? 0) || null;
  const mode = sweepRecordMode(sweep);
  const smallerPager = detected !== null && detected < page;
  const boundaryConfirmed = paginationNextState(page) !== 'enabled' &&
    (total === null || page > total || (page === total && page > 1) || smallerPager);
  const fallbackCandidate = mode === 'none' && (page === 1 || !boundaryConfirmed);
  if (fallbackCandidate) {
    // The portal briefly shows no-results during hydration, including after a
    // reload. Give expected product pages the same rendering window as blanks
    // before consuming confirmations or changing the search.
    if (!emptySince) emptySince = Date.now();
    if (Date.now() - emptySince < EMPTY_PAGE_TIMEOUT_MS) {
      phase = 'waiting-content';
      lastActivityAt = Date.now();
      scheduleRelay(EMPTY_POLL_MS, true);
      return;
    }
  }
  const confirmationKey = `${generation}:${sweep.query || ''}:${page}`;
  const previous = Number(sweep.endRetries?.[page]) || 0;
  // A watchdog or widget mutation in this document must not substitute for
  // the fresh document required by the final confirmation.
  const awaitingReload = endConfirmationsInDocument.get(confirmationKey) === 2;
  const attempts = awaitingReload ? 2 : Math.min(EMPTY_END_CONFIRMATIONS, previous + 1);
  endConfirmationsInDocument.set(confirmationKey, attempts);
  const updated = {
    ...sweep,
    generation,
    total,
    endRetries: { ...(sweep.endRetries || {}), [page]: attempts }
  };
  if (!(await writeSweep(updated, generation)) || checkVerification()) return;
  // Checkpoint persistence can yield while hydration finally renders cards.
  // A confirmed empty tail must still be empty after that asynchronous boundary.
  if (!isCurrent(generation) || !keepMonitoringScope()) return;
  if (collect().length || !atEndOfResults()) {
    emptySince = 0;
    scheduleRelay(0, true);
    return;
  }

  const route = comparablePageUrl(pageUrl(1), location.href);
  if (confirmedEmptyBoundary && (confirmedEmptyBoundary.route !== route ||
      confirmedEmptyBoundary.mode !== mode ||
      Date.now() < confirmedEmptyBoundary.confirmedAt ||
      Date.now() - confirmedEmptyBoundary.confirmedAt >= EMPTY_BOUNDARY_TTL_MS ||
      (total ?? 0) > confirmedEmptyBoundary.advertisedTotal)) {
    confirmedEmptyBoundary = null;
  }
  const reuseBoundary = attempts >= 2 && confirmedEmptyBoundary?.emptyPage === page;

  recordPageState('empty-confirmation', { sweep: updated, attempt: attempts });

  // No-results briefly appears while cards load. Check the same document once
  // before spending a request, then reload once. These confirmations are short
  // and independent of normal page pacing and historical verification counts.
  if (attempts < EMPTY_END_CONFIRMATIONS && !reuseBoundary) {
    diagnostic('empty-retry', { attempt: attempts, delayMs: EMPTY_END_CONFIRM_MS });
    queueWhenIdle(
      () => {
        if (attempts === 1 || collect().length || !atEndOfResults()) scheduleRelay(0, true);
        else return navigateWithFocus(() => location.reload(), generation);
      },
      EMPTY_END_CONFIRM_MS,
      'transient-end-retry'
    );
    return;
  }

  if (fallbackCandidate && await startPaginationFallback(generation, {
    reason: 'empty-results', attempt: attempts, elapsedMs: Date.now() - emptySince
  })) return;
  if (!boundaryConfirmed || paginationNextState(page) === 'enabled') {
    // An unread interior page is a failed scan, not evidence that later pages
    // vanished. Restart from page one rather than reloading this page forever.
    await reportUnusable(
      `Reviewer page ${page} still showed no results after ${attempts} confirmations`,
      generation,
      true
    );
    return;
  }

  if (reuseBoundary) {
    diagnostic('empty-boundary-reused', { durationMs: Date.now() - confirmedEmptyBoundary.confirmedAt });
  } else {
    // This document has just completed the normal confirmation sequence. A
    // native restart can reuse that proof briefly, but must still visit this
    // page and see it empty twice. Fast reuse never extends the proof's age.
    confirmedEmptyBoundary = { route, mode, emptyPage: page,
      advertisedTotal: total ?? 0, confirmedAt: Date.now() };
  }

  // The catalogue can shrink while it is being scanned. Keep the confirmed
  // boundary rather than the old maximum, and fill any earlier unread pages
  // before reporting a complete scan. Even a smaller pager can be a window of
  // page numbers, so the current empty page bounds the confirmed range.
  await continueSweep({ ...updated, total: page - 1, terminalPage: page - 1,
    terminalReason: 'confirmed-empty', nextPageAvailable: false, lastObservedPage: page }, generation);
}

// An explicit no-results panel is useful coverage, but a blank/error document
// or a missing interior page is not. Prove an empty target locally across two
// visible reads; no reload or additional request is needed for this case.
function auxiliaryEmptyState(sweep, generation) {
  const target = role.auxiliary;
  if (!target || target.selection || target.discoveryOnly || !isCurrent(generation) ||
      currentPage() !== 1 || sweep.targetKey !== target.targetKey ||
      (sweep.visited || []).length || (sweep.total || 0) > 1 ||
      auxiliaryScopeUrl(location.href) !== auxiliaryScopeUrl(target.targetUrl) ||
      Date.now() - lastInteraction < INTERACTION_GRACE_MS || checkVerification()) return null;
  const results = document.querySelector('main, [role="main"], #maincontent');
  // Footer/help text and hidden panels cannot prove a result set is empty.
  if (!results || results.hidden || results.getAttribute?.('aria-hidden') === 'true' ||
      (results.getClientRects && !results.getClientRects().length) ||
      getComputedStyle(results).display === 'none' ||
      ['hidden', 'collapse'].includes(getComputedStyle(results).visibility) ||
      !END_OF_RESULTS_RE.test(results.innerText || '')) return null;
  const state = pageStateEvidence(sweep);
  if (state.readyState !== 'complete' || state.visibility !== 'visible' ||
      state.route !== 'reviewer' || !state.noResults || state.loading || state.error || state.signIn ||
      state.productLinks || state.offerLines || collect().length ||
      state.responseStatus >= 400 || state.failedRequests || paginationNextState(1) === 'enabled' ||
      (totalPages() || 0) > 1) return null;
  return `${generation}:${RESULTS_DOCUMENT_ID}:${target.targetKey}:${location.href}`;
}

async function verifyAuxiliaryEmpty(sweep, generation) {
  const target = role.auxiliary;
  const waitKey = `${generation}:${target?.targetKey || ''}:${location.href}`;
  if (waitKey !== auxiliaryReadinessWaitKey) {
    auxiliaryReadinessWaitKey = waitKey;
    auxiliaryReadinessWaitSince = 0;
  }
  if (!target || target.selection || target.discoveryOnly || currentPage() !== 1 ||
      (sweep.visited || []).length || (sweep.total || 0) > 1) {
    auxiliaryEmptyObservation = null;
    return false;
  }
  if (Date.now() - lastInteraction < INTERACTION_GRACE_MS) {
    auxiliaryEmptyObservation = null;
    auxiliaryReadinessWaitSince = 0;
    emptySince = 0;
    phase = 'waiting-user';
    scheduleRelay(Math.max(1_000, INTERACTION_GRACE_MS - (Date.now() - lastInteraction)), true);
    return true;
  }
  if (document.visibilityState !== 'visible') {
    auxiliaryEmptyObservation = null;
    auxiliaryReadinessWaitSince ||= Date.now();
    // Scheduler waits are not render failures. Still bound a lost/stalled
    // receiver rather than letting a wait label hide it indefinitely.
    if (Date.now() - auxiliaryReadinessWaitSince >= 120_000) return false;
    phase = 'waiting-focus';
    const grant = await requestPreparationFocus(generation, Date.now() + 3_000, 'page-navigation');
    if (!isCurrent(generation) || checkVerification()) return true;
    if (Date.now() - lastInteraction < INTERACTION_GRACE_MS) {
      auxiliaryReadinessWaitSince = 0;
      emptySince = 0;
      phase = 'waiting-user';
      scheduleRelay(Math.max(1_000, INTERACTION_GRACE_MS - (Date.now() - lastInteraction)), true);
      return true;
    }
    lastFacetFocusReason = grant?.granted ? 'none' : grant?.reason || 'unavailable';
    emptySince = 0;
    lastActivityAt = Date.now();
    // Leave the two-second local confirmation inside this three-second turn.
    // A denial retains the normal retry delay instead of polling rapidly.
    scheduleRelay(grant?.granted ? 100 : 1_000, true);
    return true;
  }
  auxiliaryReadinessWaitSince = 0;
  const key = auxiliaryEmptyState(sweep, generation);
  if (!key) { auxiliaryEmptyObservation = null; return false; }
  if (auxiliaryEmptyObservation?.key !== key || Date.now() < auxiliaryEmptyObservation.at ||
      Date.now() - auxiliaryEmptyObservation.at > 10_000) {
    auxiliaryEmptyObservation = { key, at: Date.now() };
  }
  if (Date.now() - auxiliaryEmptyObservation.at < EMPTY_END_CONFIRM_MS) {
    phase = 'waiting-content';
    lastActivityAt = Date.now();
    scheduleRelay(EMPTY_END_CONFIRM_MS, true);
    return true;
  }
  const empty = { ...sweep, generation, emptyResult: true, total: 0,
    terminalPage: 0, terminalReason: 'confirmed-empty', nextPageAvailable: false, lastObservedPage: 1 };
  if (!(await writeSweep(empty, generation)) || auxiliaryEmptyState(empty, generation) !== key) {
    auxiliaryEmptyObservation = null;
    if (isCurrent(generation)) scheduleRelay(0, true);
    return true;
  }
  recordPageState('empty-confirmation', { sweep: empty, attempt: 2 });
  await continueSweep(empty, generation);
  return true;
}

// --- role and relay lifecycle ------------------------------------------------

function startObserver() {
  if (!extensionAvailable() || observer) return;
  const target = document.body || document.documentElement;
  if (!target) return; // A role reply can arrive after its document was detached.
  observer = new MutationObserver(() => {
    if (canObserveVisible()) { scheduleVisibleObservation(); return; }
    // Schedule once on the first mutation; continuously changing widgets must
    // not postpone extraction forever by repeatedly resetting a debounce.
    if (!relaying && !actionTimer) scheduleRelay(1_000, false);
  });
  observer.observe(target, { childList: true, subtree: true });
}

function stopObserver() {
  observer?.disconnect();
  observer = null;
}

function applyRole(message) {
  if (!extensionAvailable()) return;
  if (Number.isInteger(message?.generation) && message.generation < role.generation) return;
  const next = {
    active: Boolean(message?.active),
    tabId: Number.isInteger(message?.tabId) ? message.tabId : role.tabId,
    primaryTabId: Number.isInteger(message?.primaryTabId) ? message.primaryTabId : null,
    generation: Number.isInteger(message?.generation) ? message.generation : role.generation,
    auxiliary: message?.auxiliary || null
  };
  const changed = next.active !== role.active || next.generation !== role.generation ||
    next.tabId !== role.tabId || JSON.stringify(next.auxiliary) !== JSON.stringify(role.auxiliary);
  Object.assign(role, next);
  if (sharedVerificationPaused) {
    cancelPageWork();
    stopObserver();
    phase = 'waiting-verification';
    return;
  }
  if (!changed) { waitForTraffic(); return; }

  revokeDocumentWork();
  cancelPageWork();
  resetVisibleObservations();
  endConfirmationsInDocument.clear();
  emptySince = 0;
  ingestRetryAttempt = 0;
  if (!role.active) {
    stopObserver();
    phase = verificationObserved ? 'waiting-verification' : 'passive';
    lastActivityAt = 0;
    return;
  }

  phase = 'starting';
  lastActivityAt = Date.now();
  if (waitForTraffic()) return;
  startObserver();
  scheduleRelay(0, true);
}

async function requestRole() {
  clearTimeout(roleRetryTimer);
  roleRetryTimer = null;
  if (checkVerification()) return;
  if (roleRequestInFlight) return;
  roleRequestInFlight = true;
  try {
    const reply = await sendToWorker({ type: 'reviewer:role' });
    if (reply) {
      applyRole(reply);
      if (isCurrent() && !observer) {
        phase = 'starting';
        startObserver();
        scheduleRelay(0, true);
      }
    }
  } catch {
    if (extensionAvailable() && !role.active) roleRetryTimer = setTimeout(requestRole, 2_000);
  } finally {
    roleRequestInFlight = false;
  }
}

function applyAckRole(ack) {
  if (!ack || !Number.isInteger(ack.generation)) return;
  if (ack.generation < role.generation) return;
  if (ack.generation !== role.generation || Boolean(ack.active) !== role.active ||
      ack.primaryTabId !== role.primaryTabId ||
      (ack.auxiliary && JSON.stringify(ack.auxiliary) !== JSON.stringify(role.auxiliary))) {
    applyRole(ack);
  }
}

async function reportUnusable(reason, generation, restartSweep = false, details = {}) {
  if (!isCurrent(generation)) return;
  recordPageState('unusable');
  phase = 'empty-timeout';
  lastActivityAt = Date.now();
  let reply;
  try {
    reply = await sendDocumentMessage({
      type: 'reviewer:unusable', generation, reason, documentId: RESULTS_DOCUMENT_ID,
      ...(['document-http-error', 'render-timeout', 'render-opportunity-timeout', 'navigation-timeout'].includes(details.failureCode)
        ? { failureCode: details.failureCode } : {}),
      ...(Number.isInteger(details.responseStatus) && details.responseStatus >= 400 && details.responseStatus <= 599
        ? { responseStatus: details.responseStatus } : {}),
      ...(restartSweep ? { restartSweep: true } : {})
    });
  } catch {
    scheduleRelay(15_000, true);
    return;
  }
  applyAckRole(reply);
  if (!isCurrent(reply?.generation ?? generation)) return;

  if (reply?.recover === 'restart') {
    queueWhenIdle(
      () => navigateWithFocus(() => location.assign(pageUrl(1)), generation),
      Math.max(1_000, Number(reply.retryAfterMs) || 5_000),
      'recovering'
    );
  } else if (reply?.recover === 'reload') {
    queueWhenIdle(
      () => navigateWithFocus(() => location.reload(), generation),
      Math.max(1_000, Number(reply.retryAfterMs) || 5_000),
      'recovering'
    );
  } else if (role.active) {
    scheduleRelay(15_000, true);
  }
}

function retryDelay(retryable) {
  if (!retryable) return INGEST_RETRY_DELAYS_MS.at(-1);
  const base = INGEST_RETRY_DELAYS_MS[
    Math.min(Math.max(0, ingestRetryAttempt - 1), INGEST_RETRY_DELAYS_MS.length - 1)
  ];
  return base * (0.8 + Math.random() * 0.4);
}

async function relay() {
  if (checkVerification()) return;
  if (waitForTraffic()) return;
  if (!isCurrent() || relaying || actionTimer || navigationPending) return;
  const operation = beginDocumentRelay();
  if (!operation) return;
  const generation = role.generation;
  const current = () => isCurrent(generation) && documentSession.current(operation);
  phase = 'collecting';
  lastActivityAt = Date.now();
  try {
    if (!(await isReviewerPage())) {
      await reportUnusable('The selected tab is no longer on the configured reviewer page', generation);
      return;
    }
    if (!current()) return;

    const responseStatus = documentHttpFailure();
    if (responseStatus && !(trafficDocumentRecovered && (collect().length > 0 || atEndOfResults()))) {
      if (Date.now() - lastInteraction < INTERACTION_GRACE_MS) {
        phase = 'waiting-user';
        lastActivityAt = Date.now();
        scheduleRelay(Math.max(1_000, INTERACTION_GRACE_MS - (Date.now() - lastInteraction)), true);
        return;
      }
      diagnostic('navigation-outcome', { outcome: 'http-error', responseStatus, documentId: RESULTS_DOCUMENT_ID });
      await reportUnusable('Reviewer document returned an unsuccessful HTTP response', generation, false,
        { failureCode: 'document-http-error', responseStatus });
      return;
    }
    if (!(await ensureAuxiliaryTarget(generation)) || !current()) return;
    if (!(await ensureSweepSearch(generation)) || !current()) return;
    if (!(await reconcileRenderedPagination(generation)) || !current()) return;

    const renderedItems = collect(null, true);
    if (renderedItems.length) {
      auxiliaryEmptyObservation = null;
      if (pageStateEmptyContext === `${generation}:${location.href}`) recordPageState('recovered', { items: renderedItems.length });
      const intakeKey = `${generation}:${location.href}`;
      const items = documentSession.reading.intakeKey === intakeKey
        ? renderedItems.filter(item => !acknowledgedVisibleIds.has(item.item_id)) : renderedItems;
      emptySince = 0;
      phase = 'ingesting';
      lastActivityAt = Date.now();
      if (items.length) {
        let ack;
        try {
          ack = await sendDocumentMessage({ type: 'reviewer:page', generation, page: currentPage(), items });
        } catch (err) {
          ack = { ok: false, retryable: true, error: err?.message || String(err) };
        }
        applyAckRole(ack);
        if (!current()) return;
        if (!ack?.ok) {
          ingestRetryAttempt += 1;
          phase = 'ingest-retry';
          diagnostic('ingest-retry', { attempt: ingestRetryAttempt, requestId: ack?.requestId, intakePhase: 'initial' });
          lastActivityAt = Date.now();
          scheduleRelay(retryDelay(ack?.retryable), true);
          return;
        }
        ingestRetryAttempt = 0;
        rememberAcknowledged(items);
        documentSession.reading.intakeKey = intakeKey;
      }
      if (!(await scrollResultsPage(generation))) return;
      if (!current() || checkVerification() || !keepMonitoringScope()) return;
      // Scrolling may render more cards. Commit these before leaving even the
      // final page, and retain the existing ingest retry/deduplication contract.
      const lateItems = collect(acknowledgedVisibleIds, true)
        .filter(item => !acknowledgedVisibleIds.has(item.item_id));
      if (lateItems.length) {
        let lateAck;
        try {
          lateAck = await sendDocumentMessage({ type: 'reviewer:page', generation,
            page: currentPage(), items: lateItems, intakePhase: 'late' });
        } catch { lateAck = { ok: false, retryable: true }; }
        applyAckRole(lateAck);
        if (!current()) return;
        if (!lateAck?.ok) {
          ingestRetryAttempt += 1;
          phase = 'ingest-retry';
          diagnostic('ingest-retry', { attempt: ingestRetryAttempt, requestId: lateAck?.requestId, intakePhase: 'late' });
          scheduleRelay(retryDelay(lateAck?.retryable), true);
          return;
        }
        rememberAcknowledged(lateItems);
      }
      if (!resultsReadingConfirmed(generation)) {
        phase = 'waiting-content';
        scheduleRelay(1_000, true);
        return;
      }
      if (await rejectRepeatedPage(generation)) return;
      if (lastReadyResultsPage !== currentPage()) {
        lastReadyResultsPage = currentPage();
        diagnostic('results-page-ready', { documentId: RESULTS_DOCUMENT_ID });
      }
      await advanceDeliveredPage(generation);
      return;
    }

    recordPageState('empty-observed');
    if (atEndOfResults()) {
      await handleEndPage(generation);
      return;
    }

    if (role.auxiliary) return waitForAuxiliaryRender(generation,
      'Auxiliary reviewer page did not render within its active rendering budget');
    if (!emptySince) emptySince = Date.now();
    if (Date.now() - emptySince >= EMPTY_PAGE_TIMEOUT_MS) {
      const elapsedMs = Date.now() - emptySince;
      emptySince = Date.now();
      if (await startPaginationFallback(generation, { reason: 'blank-timeout', elapsedMs })) return;
      await reportUnusable(
        `Reviewer page ${currentPage()} did not render any item cards within ` +
        `${EMPTY_PAGE_TIMEOUT_MS / 1000} seconds`,
        generation,
        true
      );
      return;
    }

    phase = 'waiting-content';
    lastActivityAt = Date.now();
    scheduleRelay(EMPTY_POLL_MS, true);
  } finally {
    endDocumentRelay(operation);
  }
}

if (extensionAvailable()) {
  chrome.storage.onChanged?.addListener(onVerificationStorageChange);
  // Snapshot outstanding pauses for fresh documents. A newer stop/storage
  // signal always wins over this asynchronous startup read.
  const initialPauseRevision = sharedVerificationRevision;
  readLocal(['botCheck']).then(saved => {
    if (extensionAvailable() && sharedVerificationRevision === initialPauseRevision &&
        saved.botCheck?.pausedUntil > 0) setSharedVerificationPause(true);
  }).catch(() => setSharedVerificationPause(true));
  verificationObserver = new MutationObserver(() => {
    if (!extensionAvailable() || verificationTimer) return;
    verificationTimer = setTimeout(() => {
      verificationTimer = null;
      if (!checkVerification() && !waitForTraffic() && phase === 'waiting-verification') requestRole();
    }, 100);
  });
  verificationObserver.observe(document.documentElement || document.body,
    { childList: true, subtree: true, characterData: true, attributes: true,
      attributeFilter: ['style', 'class', 'hidden', 'src', 'title'] });
  diagnostic('page-loaded');
  requestRole();
  if (extensionAvailable()) rolePollTimer = setInterval(requestRole, ROLE_POLL_MS);
}
