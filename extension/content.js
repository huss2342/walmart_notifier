// Reads the signed-in reviewer page. Every matching tab loads this script, but
// only the background-selected primary is allowed to observe, relay, or page.

const ITEM_LINK = 'a[href*="/ip/"]';
const DEFAULT_PATH_PATTERN = '^/reviews/claim-product';
const VALUE_RE = /Valued\s*at\s*\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i;
const VALUE_GLOBAL_RE = /Valued\s*at/gi;
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
const EMPTY_POLL_MS = 2_000;
const END_OF_RESULTS_RE = /no search results/i;
const HARD_PAGE_CAP = 100;
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

const role = {
  active: false,
  tabId: null,
  primaryTabId: null,
  generation: 0,
  auxiliary: null
};
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
let resultsScrollController = null;
let resultsScrollStarted = null;
let resultsScrollCompleted = null;
let resultsScrollProof = null;
let resultsIntakeKey = null;
let resultsScrollAttemptKey = null;
let resultsScrollWorkMs = 0;
let resultsScrollFailures = 0;
const onScrollVisibilityChange = () => {
  if (document.visibilityState !== 'visible') cancelResultsScroll();
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

function stopInvalidatedContext() {
  if (contextInvalidated) return;
  contextInvalidated = true;
  role.active = false;
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
const readLocal = async keys => await extensionCall(() => chrome.storage.local.get(keys)) || {};
const writeLocal = values => extensionCall(() => chrome.storage.local.set(values));
const removeLocal = keys => extensionCall(() => chrome.storage.local.remove(keys));

function diagnostic(event, details = {}) {
  sendToWorker({ type: 'reviewer:diagnostic', event,
    details: { ...details, page: currentPage(), generation: role.generation } }).catch(() => {});
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

function isCurrent(generation = role.generation) {
  return extensionAvailable() && role.active && !sharedVerificationPaused &&
    !verificationObserved && role.generation === generation;
}

function setSharedVerificationPause(paused) {
  sharedVerificationRevision += 1;
  sharedVerificationPaused = paused;
  if (!paused) return;
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
  if (!extensionAvailable()) return undefined;
  if (msg?.type === 'reviewer:pause') {
    setSharedVerificationPause(true);
    sendResponse({ ok: true });
    return undefined;
  }
  if (msg?.type === 'reviewer:probe') {
    const botCheck = checkVerification();
    sendResponse({
      ready: true,
      botCheck,
      reviewerReady: !botCheck && (collect().length > 0 || atEndOfResults()),
      active: role.active,
      generation: role.generation,
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
      const label = (link.getAttribute('aria-label') || '').trim();
      links.push({ id, label });
      const primary = label.match(/^view item for\s+(\S.*)$/i);
      if (primary) {
        primaryIds.add(id);
        primaryTitles.add(normalized(primary[1]));
      }
    }
    // One price alone may belong to a sibling whose neighbor has no value.
    // Accept variants only through Walmart's observed explicit relationship,
    // never because another product happens to lack a primary action.
    if (ids.size > 1) {
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
  title = title.replace(/^view item\b/i, '').trim();
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

function collect(excludedIds = null) {
  let claims;
  const query = new URLSearchParams(location.search).get('q') || '';
  const byId = new Map();

  for (const anchor of document.querySelectorAll(ITEM_LINK)) {
    const id = idFromHref(anchor.getAttribute('href') || '');
    if (!id || byId.has(id) || excludedIds?.has(`ip-${id}`)) continue;

    const card = cardFor(anchor);
    const title = titleFor(anchor, card);
    if (!title) continue;

    if (claims === undefined) claims = claimsRemaining();
    const value = card ? card.text.match(VALUE_RE) : null;
    byId.set(id, {
      item_id: `ip-${id}`,
      title,
      value_usd: value ? parseFloat(value[1].replace(/,/g, '')) : null,
      url: new URL(anchor.getAttribute('href'), location.origin).toString().split('?')[0],
      category: categoryFor(anchor, card),
      badge: badgeFor(anchor.innerText),
      image_url: imageFor(anchor, card),
      available: !(card && OUT_OF_STOCK_RE.test(card.text)),
      source: 'extension',
      claims_remaining: claims,
      query
    });
  }
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
  url.searchParams.set('queryNups', '');
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
  }).filter(number => number > 0 && number <= HARD_PAGE_CAP);
  const numbers = (controlNumbers.length ? controlNumbers : (list.innerText.match(/\d+/g) || []))
    .map(Number)
    .filter((number) => number > 0 && number <= HARD_PAGE_CAP);
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
    const query = url.searchParams.get('q') || url.searchParams.get('queryNups') || '';
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
  // Hidden documents can suspend the frames needed to mount their popovers.
  // Wait for a reading turn instead of exhausting otherwise valid targets.
  if ((grant?.enabled && !grant.granted) || document.visibilityState !== 'visible' ||
      (Number.isFinite(grant?.until) && grant.until - Date.now() < 8_000)) {
    lastFacetFocusReason = grant?.enabled && !grant.granted ? grant.reason || 'unknown'
      : document.visibilityState !== 'visible' ? 'hidden' : 'short-lease';
    deferFacetControls();
    return null;
  }
  lastFacetFocusReason = 'none';
  return () => auxiliaryCanAct(generation) && document.visibilityState === 'visible' &&
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
      if (error?.code === 'cancelled' || document.visibilityState !== 'visible' ||
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
    if (!isCurrent(generation)) return;
    if (!result?.ok) throw new Error(result?.reason || 'Filter is no longer available');
  } catch (error) {
    navigationPending = false;
    if (isCurrent(generation) && !checkVerification()) {
      if (error?.message === 'cancelled' || document.visibilityState !== 'visible' ||
          Date.now() - lastInteraction < INTERACTION_GRACE_MS) deferFacetControls();
      else await reportUnusable(`Filter selection failed: ${error?.message || String(error)}`, generation);
    }
    return;
  }
  const poll = async () => {
    if (!isCurrent(generation) || checkVerification()) return;
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
      try {
        const reply = await sendToWorker({ type: 'reviewer:aux-selected', generation, url: location.href });
        applyAckRole(reply);
        if (!reply?.ok) throw new Error(reply?.error || 'Could not authorize selected filter');
        navigationPending = false;
        if (isCurrent(generation)) scheduleRelay(0, true);
      } catch (error) {
        navigationPending = false;
        if (isCurrent(generation)) await reportUnusable(error?.message || String(error), generation);
      }
      return;
    }
    if (Date.now() - startedAt >= EMPTY_PAGE_TIMEOUT_MS) {
      navigationPending = false;
      await reportUnusable('Selected filter did not produce a fresh results page within 30 seconds', generation);
      return;
    }
    actionTimer = setTimeout(() => { actionTimer = null; poll().catch(() => scheduleRelay(5_000, true)); }, 500);
    nextActionAt = Date.now() + 500;
    lastActivityAt = Date.now();
  };
  await poll();
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
      if (!emptySince) emptySince = Date.now();
      if (Date.now() - emptySince >= EMPTY_PAGE_TIMEOUT_MS) {
        await reportUnusable(!filterMatches
          ? 'The selected filter could not be authorized'
          : 'The selected filter document did not render within 30 seconds', generation);
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
      if (!emptySince) emptySince = Date.now();
      if (Date.now() - emptySince >= EMPTY_PAGE_TIMEOUT_MS) {
        await reportUnusable('Auxiliary target did not render within 30 seconds', generation);
      } else {
        phase = 'waiting-content';
        scheduleRelay(EMPTY_POLL_MS, true);
      }
      return false;
    }
  }
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
  if (params.get('q') === sweep.query && !params.get('queryNups') &&
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
  const key = sweepStorageKey();
  if (!key) return false;
  await writeLocal({ [key]: { ...sweep, generation } });
  if (!isCurrent(generation)) return false;
  // Persist the URL and acknowledged progress before a new document/tab can
  // replace this one. The worker validates the current primary lease.
  const checkpoint = await sendToWorker({ type: 'reviewer:checkpoint', generation });
  if (!checkpoint?.ok) {
    if (isCurrent(generation)) {
      phase = 'relay-retry';
      lastActivityAt = Date.now();
      scheduleRelay(5_000, true);
    }
    return false;
  }
  return isCurrent(generation);
}

async function removeCurrentSweep(generation, pages) {
  if (!isCurrent(generation)) return;
  const key = sweepStorageKey();
  if (!key) return;
  const stored = await readLocal([key]);
  const auxiliary = role.auxiliary;
  // The auxiliary coordinator removes its checkpoint only after accepting
  // completion. A suspended worker must not erase acknowledged page coverage.
  if (!auxiliary && stored[key]?.generation === generation) await removeLocal(key);
  if (!isCurrent(generation)) return;
  if (!role.auxiliary) {
    await writeLocal({
      lastSweepDone: Date.now(),
      lastSweepStartedAt: stored[key]?.startedAt || Date.now(),
      lastSweepPages: pages
    });
  }
  const capped = Math.max(totalPages() || 0, stored[key]?.total || 0) >= 25;
  const coverageLimited = Boolean(auxiliary && !auxiliary.discoveryOnly && capped);
  const reply = await sendToWorker({ type: 'reviewer:sweep-complete', generation, pages,
    ...(auxiliary ? { coverageLimited } : {}) }).catch(() => null);
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
  if (sweep.total === null || sweep.total === undefined) {
    const highest = seen.size ? Math.max(...seen) : 0;
    for (let page = 1; page <= Math.min(highest, HARD_PAGE_CAP); page += 1) {
      if (!seen.has(page)) return page;
    }
    return highest >= HARD_PAGE_CAP ? null : highest + 1;
  }
  for (let page = 1; page <= sweep.total; page += 1) {
    if (!seen.has(page)) return page;
  }
  return null;
}

function cancelPageWork() {
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
  clearTimeout(relayTimer);
  relayTimer = setTimeout(() => {
    relayTimer = null;
    relay().catch((err) => {
      console.warn('Reviewer Item Relay: relay failed', err);
      phase = 'relay-retry';
      lastActivityAt = Date.now();
      scheduleRelay(5_000, true);
    });
  }, Math.max(0, delayMs));
}

function queueWhenIdle(action, delayMs, waitingPhase = 'waiting-navigation') {
  if (!extensionAvailable()) return;
  clearTimeout(actionTimer);
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
    if (relaying || visibleUpdateRetries > 0) {
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
    nextActionAt = 0;
    cancelPageControlScroll();
    phase = 'navigating';
    lastActivityAt = Date.now();
    return action();
  };
  actionTimer = setTimeout(attempt, Math.max(0, delayMs));
  nextActionAt = Date.now() + Math.max(0, delayMs);
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
  try {
    return await Promise.race([
      sendToWorker({ type: 'reviewer:focus', generation, expiresAt, ...(purpose ? { purpose } : {}) }),
      new Promise(resolve => {
        cancel = () => { clearTimeout(timer); resolve(null); };
        focusRequests.add(cancel);
        const limit = ['page-scroll', 'page-navigation'].includes(purpose) ? 3_000 : 1_000;
        timer = setTimeout(cancel, Math.max(0, Math.min(limit, expiresAt - Date.now())));
      })
    ]);
  } catch { return null; } // The caller keeps the current page until focus is available.
  finally { clearTimeout(timer); focusRequests.delete(cancel); }
}

// Renew selection at the actual navigation boundary. The earlier pager/reading
// slot may have expired while another scanner was using its turn.
function visibleScannerFocus(requireWindow = true) {
  return document.visibilityState === 'visible' && (!requireWindow ||
    typeof document.hasFocus !== 'function' || document.hasFocus());
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
    if (!onSourceChange && resultsScrollCompleted === `${generation}:${location.href}` &&
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
      staleGrant || (reply?.granted && !visibleScannerFocus())) {
    lastFacetFocusReason = staleGrant ? 'expired' : reply?.reason ||
      (reply?.granted ? document.visibilityState !== 'visible' ? 'hidden' : 'window-unfocused' : 'unavailable');
    navigationPending = false;
    const delayMs = reply?.granted ? 100 : Math.max(1_000, Math.min(5_000, reply?.retryAfterMs || 5_000));
    queueWhenIdle(() => navigateWithFocus(action, generation, sourceHref, onSourceChange), delayMs, 'waiting-user');
    return;
  }
  lastFacetFocusReason = 'none';
  phase = 'navigating';
  lastActivityAt = Date.now();
  return action();
}

function cancelResultsScroll() {
  resultsScrollController?.abort();
  resultsScrollController = null;
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
  return resultsScrollCompleted === `${generation}:${location.href}` &&
    resultsScrollProof === resultsReadingStamp();
}

// Read each delivered results page before advancing, including the last page
// of a search. Automatic rotation must actually obtain a visible reading turn;
// a denied turn or an unfinished animation never acknowledges page coverage.
async function scrollResultsPage(generation) {
  const href = location.href;
  const key = `${generation}:${href}`;
  if (resultsReadingConfirmed(generation)) return true;
  if (resultsScrollCompleted === key) resultsScrollCompleted = null;
  if (resultsScrollAttemptKey !== key) {
    resultsScrollAttemptKey = key;
    resultsScrollWorkMs = resultsScrollFailures = 0;
  }
  const defer = (reason, delayMs = 1_000, waitingPhase = 'waiting-focus') => {
    lastFacetFocusReason = reason;
    phase = waitingPhase;
    lastActivityAt = Date.now();
    scheduleRelay(Math.max(100, Math.min(5_000, delayMs)), true);
    return false;
  };
  const unavailable = async () => {
    if (++resultsScrollFailures >= 3) {
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
    resultsScrollCompleted = key;
    resultsScrollProof = resultsReadingStamp();
    return true;
  }
  if (!grant || grant.reason === 'activation-unavailable') return unavailable();
  if ((!disabled && !grant?.granted) || !visibleScannerFocus(!disabled) ||
      (grant?.until && grant.until <= Date.now())) {
    return defer(grant?.until && grant.until <= Date.now() ? 'expired' : grant?.reason ||
      (grant?.granted ? document.visibilityState !== 'visible' ? 'hidden' : 'window-unfocused' : 'unavailable'),
      grant?.retryAfterMs || 1_000);
  }
  lastFacetFocusReason = 'none';
  cancelResultsScroll();
  const controller = new AbortController();
  resultsScrollController = controller;
  const startAtTop = resultsScrollStarted !== key;
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
    resultsScrollStarted = key;
    const result = await ReviewerPageScroll.run({ window, document, signal: controller.signal,
      startAtTop, budgetMs, settleKey: itemSignature,
      canContinue: () => valid() && visibleScannerFocus(!disabled),
      onState() { lastActivityAt = Date.now(); } });
    if (!valid() || controller.signal.aborted || result.outcome === 'cancelled') {
      if (isCurrent(generation) && !verificationObserved && location.href === href) {
        phase = 'waiting-user';
        scheduleRelay(Math.max(1_000, INTERACTION_GRACE_MS - (Date.now() - lastInteraction)), true);
      }
      return false;
    }
    resultsScrollWorkMs += Math.max(0, Math.min(8_000, Number(result.durationMs) || 0));
    if (result.outcome !== 'complete') {
      if (resultsScrollWorkMs >= 120_000) {
        await reportUnusable('Results page did not settle within its bounded reading budget', generation);
        return false;
      }
      if (result.outcome !== 'timeout' || !(result.distance > 0)) return unavailable();
      resultsScrollFailures = 0;
      return defer('none', 1_000, 'reading-page');
    }
    if (!visibleScannerFocus(!disabled) || (grant?.until && grant.until <= Date.now())) {
      return defer(grant?.until && grant.until <= Date.now() ? 'expired'
        : document.visibilityState !== 'visible' ? 'hidden' : 'window-unfocused');
    }
    resultsScrollCompleted = key;
    resultsScrollProof = resultsReadingStamp();
    resultsScrollFailures = 0;
    return true;
  } catch {
    // Already relayed items stay deduplicated; an animation error is not proof
    // that the rest of the page loaded. Recovery is bounded to this target.
    return valid() ? unavailable() : false;
  } finally {
    if (resultsScrollController === controller) resultsScrollController = null;
    programmaticScrollUntil = Date.now() + 250;
    programmaticScroll = false;
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
    if ((reply?.enabled && !reply.granted) ||
        (reply?.granted && document.visibilityState !== 'visible')) {
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
  return collect().map(item => item.item_id).sort().join('|');
}

function navigatePage(page, generation, scanRestart = false, { retry = 0, bypassCooldown = false } = {}) {
  if (!isCurrent(generation) || checkVerification() || !keepMonitoringScope()) return;
  cancelVisibleObservation();
  const target = pageUrl(page);
  navigationTarget = target;
  const candidate = findPageControl(target, page);
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
      reason: 'missing-control' });
    if (scanRestart) diagnostic('scan-restart-load');
    if (!isCurrent(generation)) return;
    navigationPending = true;
    lastNavigationAt = Date.now();
    location.assign(target);
    return;
  }
  const previous = itemSignature();
  const startedAt = Date.now();
  const controlKey = nativeControlKey(target, control);
  lastNavigationAt = startedAt;
  const startedUrl = comparablePageUrl(location.href, location.href);
  const fresh = () => comparablePageUrl(location.href, location.href) === comparablePageUrl(target, location.href) &&
    ((collect().length && itemSignature() !== previous) || atEndOfResults());
  const resumePoll = () => {
    navigationPending = true;
    phase = 'waiting-page-change';
    return poll();
  };
  const responseArrived = () => fresh() ||
    (comparablePageUrl(location.href, location.href) !== startedUrl && Date.now() - startedAt < EMPTY_PAGE_TIMEOUT_MS);
  const stalled = async () => {
    if (!isCurrent(generation) || checkVerification() || !keepMonitoringScope()) return;
    if (lastInteraction > startedAt) {
      navigationPending = false;
      navigationTarget = null;
      phase = 'waiting-user';
      diagnostic('navigation-interrupted', { targetPage: page });
      scheduleRelay(INTERACTION_GRACE_MS, true);
      return;
    }
    // Retry only a no-op. A route that already changed keeps its full render
    // allowance and must never be clicked twice over the previous page's cards.
    if (retry < 1 && comparablePageUrl(location.href, location.href) === startedUrl) {
      const delayMs = await pageDelayMs();
      if (!isCurrent(generation) || checkVerification() || !keepMonitoringScope()) return;
      diagnostic('navigation-retry', { targetPage: page, reason: 'native-stalled', delayMs,
        durationMs: Date.now() - startedAt });
      queueWhenIdle(() => {
        // A late first response can arrive during the paced wait. Let its
        // original freshness poll resolve it instead of clicking a second time.
        if (comparablePageUrl(location.href, location.href) !== startedUrl) return poll();
        return navigateWithFocus(() => navigatePage(page, generation, scanRestart,
          { retry: 1, bypassCooldown: true }), generation, location.href, resumePoll);
      }, delayMs);
      return;
    }
    const stored = await readLocal(['nativeNavigationFailures']);
    if (!isCurrent(generation) || checkVerification() || !keepMonitoringScope()) return;
    if (responseArrived()) return resumePoll();
    readNativePolicy({ nativeNavigationFailures: stored.nativeNavigationFailures });
    nativeNavigationFailures[controlKey] = { until: Date.now() + NATIVE_NAVIGATION_BACKOFF_MS,
      failures: Math.min(5, (nativeNavigationFailures[controlKey]?.failures || 0) + 1) };
    const bounded = Object.entries(nativeNavigationFailures).sort((a, b) => b[1].until - a[1].until).slice(0, 20);
    nativeNavigationFailures = Object.fromEntries(bounded);
    await writeLocal({ nativeNavigationFailures, nativeNavigationPausedUntil: 0 }).catch(() => {});
    if (!isCurrent(generation) || checkVerification() || !keepMonitoringScope()) return;
    if (responseArrived()) return resumePoll();
    if (lastInteraction > startedAt) {
      navigationPending = false;
      navigationTarget = null;
      phase = 'waiting-user';
      diagnostic('navigation-interrupted', { targetPage: page });
      scheduleRelay(INTERACTION_GRACE_MS, true);
      return;
    }
    diagnostic('navigation-fallback', { targetPage: page, reason: 'native-stalled',
      durationMs: Date.now() - startedAt, items: collect().length });
    if (scanRestart) diagnostic('scan-restart-load');
    if (!isCurrent(generation)) return;
    phase = 'navigating';
    await navigateWithFocus(() => location.assign(target), generation);
  };
  navigationPending = true;
  phase = 'waiting-page-change';
  diagnostic('navigation-native', { targetPage: page });
  if (!isCurrent(generation)) return;
  // Hold the relay lock across client-side URL changes: the old page's cards
  // may remain visible after the URL already says "page 2".
  const poll = () => {
    if (!isCurrent(generation) || checkVerification()) return;
    if (lastInteraction > startedAt) {
      actionTimer = null;
      navigationPending = false;
      navigationTarget = null;
      phase = 'waiting-user';
      diagnostic('navigation-interrupted', { targetPage: page });
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
      if (scanRestart) diagnostic('scan-restart-native');
      if (!isCurrent(generation)) return;
      if (nativeNavigationFailures[controlKey]) {
        delete nativeNavigationFailures[controlKey];
        writeLocal({ nativeNavigationFailures }).catch(() => {});
      }
      scheduleRelay(1_000, true);
      return;
    }
    const elapsed = Date.now() - startedAt;
    // Observed native pagination changes the URL before rendering new cards.
    // A no-op gets a short start window; a started navigation keeps the full
    // rendering allowance and can never acknowledge the previous page's cards.
    if (elapsed >= EMPTY_PAGE_TIMEOUT_MS ||
        (elapsed >= NATIVE_START_TIMEOUT_MS &&
          comparablePageUrl(location.href, location.href) === startedUrl)) {
      actionTimer = null;
      nextActionAt = 0;
      stalled();
      return;
    }
    actionTimer = setTimeout(poll, 500);
    nextActionAt = Date.now() + 500;
  };
  actionTimer = setTimeout(poll, 500);
  nextActionAt = Date.now() + 500;
  try { control.click(); } catch {
    clearTimeout(actionTimer);
    actionTimer = null;
    nextActionAt = 0;
    stalled();
  }
}

async function finishSweep(sweep, generation) {
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
    const updated = {
      ...sweep,
      mode,
      query: queries[nextIndex],
      completedQueries: nextIndex,
      completedLetters: mode === 'sweep' ? nextIndex : 0,
      completedPages: pages,
      total: null,
      visited: [],
      endRetries: {},
      lastProgressAt: Date.now()
    };
    if (!(await writeSweep(updated, generation))) return;
    const wait = await pageDelayMs();
    if (isCurrent(generation)) {
      queueWhenIdle(() => navigateWithFocus(() => location.assign(searchUrl(updated.query)), generation), wait);
    }
    return;
  }
  if (!(await writeSweep(sweep, generation))) return;
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

async function advanceDeliveredPage(generation) {
  if (!isCurrent(generation)) return;
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
  const total = Math.max(detected ?? 0, sweep.total ?? 0) || null;
  const visited = [...new Set([...(sweep.visited || []), page])];
  const endRetries = { ...(sweep.endRetries || {}) };
  delete endRetries[page];
  endConfirmationsInDocument.delete(`${generation}:${sweep.query || ''}:${page}`);
  const updated = {
    ...sweep,
    generation,
    total,
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
  const reply = await sendToWorker({ type: 'reviewer:pagination-fallback', generation,
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
  const page = currentPage();
  const sweep = await readSweep(generation);
  if (!isCurrent(generation) || checkVerification()) return;
  if (collect().length || !atEndOfResults()) {
    scheduleRelay(0, true);
    return;
  }

  const detected = totalPages();
  const total = Math.max(detected ?? 0, sweep.total ?? 0) || null;
  const mode = sweepRecordMode(sweep);
  const smallerPager = detected !== null && detected < page;
  const boundaryConfirmed = total === null || page > total ||
    (page === total && page > 1) || smallerPager;
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

  const route = comparablePageUrl(pageUrl(1), location.href);
  if (confirmedEmptyBoundary && (confirmedEmptyBoundary.route !== route ||
      confirmedEmptyBoundary.mode !== mode ||
      Date.now() < confirmedEmptyBoundary.confirmedAt ||
      Date.now() - confirmedEmptyBoundary.confirmedAt >= EMPTY_BOUNDARY_TTL_MS ||
      (total ?? 0) > confirmedEmptyBoundary.advertisedTotal)) {
    confirmedEmptyBoundary = null;
  }
  const reuseBoundary = attempts >= 2 && confirmedEmptyBoundary?.emptyPage === page;

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
  if (!boundaryConfirmed) {
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
  await continueSweep({ ...updated, total: page - 1 }, generation);
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
  if (!changed) return;

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

async function reportUnusable(reason, generation, restartSweep = false) {
  if (!isCurrent(generation)) return;
  phase = 'empty-timeout';
  lastActivityAt = Date.now();
  let reply;
  try {
    reply = await sendToWorker({
      type: 'reviewer:unusable', generation, reason,
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
  if (!isCurrent() || relaying || actionTimer || navigationPending) return;
  const generation = role.generation;
  relaying = true;
  phase = 'collecting';
  lastActivityAt = Date.now();
  try {
    if (!(await isReviewerPage())) {
      await reportUnusable('The selected tab is no longer on the configured reviewer page', generation);
      return;
    }
    if (!isCurrent(generation)) return;

    if (!(await ensureAuxiliaryTarget(generation))) return;
    if (!(await ensureSweepSearch(generation))) return;

    const renderedItems = collect();
    if (renderedItems.length) {
      const intakeKey = `${generation}:${location.href}`;
      const items = resultsIntakeKey === intakeKey
        ? renderedItems.filter(item => !acknowledgedVisibleIds.has(item.item_id)) : renderedItems;
      emptySince = 0;
      phase = 'ingesting';
      lastActivityAt = Date.now();
      if (items.length) {
        let ack;
        try {
          ack = await sendToWorker({ type: 'reviewer:page', generation, page: currentPage(), items });
        } catch (err) {
          ack = { ok: false, retryable: true, error: err?.message || String(err) };
        }
        applyAckRole(ack);
        if (!isCurrent(generation)) return;
        if (!ack?.ok) {
          ingestRetryAttempt += 1;
          phase = 'ingest-retry';
          diagnostic('ingest-retry', { attempt: ingestRetryAttempt });
          lastActivityAt = Date.now();
          scheduleRelay(retryDelay(ack?.retryable), true);
          return;
        }
        ingestRetryAttempt = 0;
        rememberAcknowledged(items);
        resultsIntakeKey = intakeKey;
      }
      if (!(await scrollResultsPage(generation))) return;
      if (!isCurrent(generation) || checkVerification() || !keepMonitoringScope()) return;
      // Scrolling may render more cards. Commit these before leaving even the
      // final page, and retain the existing ingest retry/deduplication contract.
      const lateItems = collect(acknowledgedVisibleIds)
        .filter(item => !acknowledgedVisibleIds.has(item.item_id));
      if (lateItems.length) {
        let lateAck;
        try {
          lateAck = await sendToWorker({ type: 'reviewer:page', generation,
            page: currentPage(), items: lateItems });
        } catch { lateAck = { ok: false, retryable: true }; }
        applyAckRole(lateAck);
        if (!isCurrent(generation)) return;
        if (!lateAck?.ok) {
          ingestRetryAttempt += 1;
          phase = 'ingest-retry';
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
      if (lastReadyResultsPage !== currentPage()) {
        lastReadyResultsPage = currentPage();
        diagnostic('results-page-ready', { documentId: RESULTS_DOCUMENT_ID });
      }
      await advanceDeliveredPage(generation);
      return;
    }

    if (atEndOfResults()) {
      await handleEndPage(generation);
      return;
    }

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
    relaying = false;
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
      if (!checkVerification() && phase === 'waiting-verification') requestRole();
    }, 100);
  });
  verificationObserver.observe(document.documentElement || document.body,
    { childList: true, subtree: true, characterData: true, attributes: true,
      attributeFilter: ['style', 'class', 'hidden', 'src', 'title'] });
  diagnostic('page-loaded');
  requestRole();
  if (extensionAvailable()) rolePollTimer = setInterval(requestRole, ROLE_POLL_MS);
}
