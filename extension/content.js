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
const EMPTY_POLL_MS = 2_000;
const PAGE_JITTER = 0.25;
const DEFAULT_PAGE_DELAY_S = 5;
const END_OF_RESULTS_RE = /no search results/i;
const HARD_PAGE_CAP = 100;
const TRANSIENT_END_MAX_RELOADS = 3;
const INGEST_RETRY_DELAYS_MS = [2_000, 5_000, 15_000, 30_000, 60_000];

let lastInteraction = 0;
let programmaticScroll = false;
let programmaticScrollUntil = 0;
for (const evt of ['click', 'keydown', 'scroll']) {
  document.addEventListener(evt, () => {
    if (evt === 'scroll' &&
        (programmaticScroll || Date.now() < programmaticScrollUntil)) return;
    lastInteraction = Date.now();
  }, { passive: true, capture: true });
}

const role = {
  active: false,
  tabId: null,
  primaryTabId: null,
  generation: 0
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

function isCurrent(generation = role.generation) {
  return role.active && role.generation === generation;
}

function sweepingNow() {
  return role.active && phase !== 'idle' && phase !== 'complete' && phase !== 'passive';
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === 'reviewer:probe') {
    sendResponse({
      ready: true,
      active: role.active,
      generation: role.generation,
      phase,
      busy: Date.now() - lastInteraction < INTERACTION_GRACE_MS,
      sweeping: sweepingNow(),
      lastActivityAt
    });
    return undefined;
  }
  if (msg?.type === 'reviewer:set-role') {
    applyRole(msg);
    sendResponse({ ok: true });
    return undefined;
  }
  if (msg?.type === 'reviewer:recover') {
    if (msg.generation === role.generation && role.active && !relaying) {
      if (phase !== 'waiting-user') {
        clearTimeout(actionTimer);
        actionTimer = null;
        scheduleRelay(0, true);
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
});

/** Force the lazy pager into the DOM, then restore the user's scroll position. */
async function revealPager() {
  const from = window.scrollY;
  programmaticScroll = true;
  try {
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise((done) => setTimeout(done, 400));
    window.scrollTo(0, from);
    await new Promise((done) => setTimeout(done, 50));
  } finally {
    // Some browsers dispatch the final scroll event after scrollTo returns.
    programmaticScrollUntil = Date.now() + 250;
    programmaticScroll = false;
  }
}

async function isReviewerPage() {
  if (EXCLUDE_PATH.test(location.pathname)) return false;
  const { pathPattern = DEFAULT_PATH_PATTERN } =
    await chrome.storage.local.get(['pathPattern']);
  try {
    return new RegExp(pathPattern, 'i').test(location.pathname);
  } catch {
    return new RegExp(DEFAULT_PATH_PATTERN, 'i').test(location.pathname);
  }
}

function idFromHref(href) {
  const match = href.match(/\/ip\/(?:[^/?#]+\/)?(\d{6,})/);
  return match ? match[1] : null;
}

/** The nearest ancestor holding exactly one item's worth of card text. */
function cardFor(anchor) {
  let node = anchor.parentElement;
  for (let depth = 0; node && depth < CARD_MAX_DEPTH; depth += 1) {
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

function claimsRemaining() {
  const match = (document.body.innerText || '').match(CLAIMS_RE);
  return match ? parseInt(match[1], 10) : null;
}

function collect() {
  const claims = claimsRemaining();
  const query = new URLSearchParams(location.search).get('q') || '';
  const byId = new Map();

  for (const anchor of document.querySelectorAll(ITEM_LINK)) {
    const id = idFromHref(anchor.getAttribute('href') || '');
    if (!id || byId.has(id)) continue;

    const card = cardFor(anchor);
    if (card && OUT_OF_STOCK_RE.test(card.text)) continue;
    const title = titleFor(anchor, card);
    if (!title) continue;

    const value = card ? card.text.match(VALUE_RE) : null;
    byId.set(id, {
      item_id: `ip-${id}`,
      title,
      value_usd: value ? parseFloat(value[1].replace(/,/g, '')) : null,
      url: new URL(anchor.getAttribute('href'), location.origin).toString().split('?')[0],
      category: badgeFor(anchor.innerText),
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

function atEndOfResults() {
  return END_OF_RESULTS_RE.test(document.body.innerText || '');
}

function totalPages() {
  const list = document.querySelector('[data-automation-id="page-number"]')?.closest('ul');
  if (!list) return null;
  const numbers = (list.innerText.match(/\d+/g) || [])
    .map(Number)
    .filter((number) => number > 0 && number <= HARD_PAGE_CAP);
  return numbers.length ? Math.max(...numbers) : null;
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
  const stored = await chrome.storage.local.get([key]);
  const sweep = stored[key];
  if (sweep && sweep.generation === generation && Array.isArray(sweep.visited)) {
    return { ...freshSweep(generation), ...sweep };
  }
  return freshSweep(generation);
}

async function writeSweep(sweep, generation = role.generation) {
  if (!isCurrent(generation)) return false;
  const key = sweepStorageKey();
  if (!key) return false;
  await chrome.storage.local.set({ [key]: { ...sweep, generation } });
  return isCurrent(generation);
}

async function removeCurrentSweep(generation, pages) {
  if (!isCurrent(generation)) return;
  const key = sweepStorageKey();
  if (!key) return;
  const stored = await chrome.storage.local.get([key]);
  if (stored[key]?.generation === generation) await chrome.storage.local.remove(key);
  if (!isCurrent(generation)) return;
  await chrome.storage.local.set({
    lastSweepDone: Date.now(),
    lastSweepPages: pages
  });
}

function nextUnvisited(sweep) {
  const seen = new Set((sweep.visited || []).filter((page) => Number.isInteger(page) && page > 0));
  if (!sweep.total) {
    const highest = seen.size ? Math.max(...seen) : 0;
    return highest >= HARD_PAGE_CAP ? null : highest + 1;
  }
  for (let page = 1; page <= sweep.total; page += 1) {
    if (!seen.has(page)) return page;
  }
  return null;
}

function cancelPageWork() {
  clearTimeout(relayTimer);
  clearTimeout(actionTimer);
  relayTimer = null;
  actionTimer = null;
}

function scheduleRelay(delayMs = 0, replace = false) {
  // A completed sweep stays quiescent until the coordinator starts the next
  // one. Walmart's continuously mutating widgets must not wake an endless
  // duplicate sweep after progress has been cleared.
  if (!role.active || actionTimer || phase === 'complete') return;
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
  clearTimeout(actionTimer);
  const generation = role.generation;
  phase = waitingPhase;
  lastActivityAt = Date.now();

  const attempt = () => {
    if (!isCurrent(generation)) {
      actionTimer = null;
      return;
    }
    const idleIn = INTERACTION_GRACE_MS - (Date.now() - lastInteraction);
    if (idleIn > 0) {
      // Keep the pending action alive instead of dropping it and leaving a
      // persisted sweep record that blocks every later refresh.
      phase = 'waiting-user';
      lastActivityAt = Date.now();
      actionTimer = setTimeout(attempt, idleIn + 100);
      return;
    }
    actionTimer = null;
    phase = 'navigating';
    lastActivityAt = Date.now();
    action();
  };
  actionTimer = setTimeout(attempt, Math.max(0, delayMs));
}

async function pageDelayMs() {
  const { pageDelaySeconds = DEFAULT_PAGE_DELAY_S } =
    await chrome.storage.local.get(['pageDelaySeconds']);
  const base = Math.max(1, parseInt(pageDelaySeconds, 10) || DEFAULT_PAGE_DELAY_S) * 1000;
  return base * (1 + (Math.random() * 2 - 1) * PAGE_JITTER);
}

async function finishSweep(sweep, generation) {
  await removeCurrentSweep(generation, new Set(sweep.visited || []).size);
  if (!isCurrent(generation)) return;
  phase = 'complete';
  lastActivityAt = Date.now();
}

async function advanceDeliveredPage(generation) {
  if (!isCurrent(generation)) return;
  const page = currentPage();
  const sweep = await readSweep(generation);
  if (!isCurrent(generation)) return;

  if (totalPages() === null &&
      Date.now() - lastInteraction >= INTERACTION_GRACE_MS) {
    await revealPager();
  }
  if (!isCurrent(generation)) return;

  const detected = totalPages();
  const total = Math.max(detected ?? 0, sweep.total ?? 0) || null;
  const visited = [...new Set([...(sweep.visited || []), page])];
  const endRetries = { ...(sweep.endRetries || {}) };
  delete endRetries[page];
  const updated = {
    ...sweep,
    generation,
    total,
    visited,
    endRetries,
    lastProgressAt: Date.now()
  };
  const next = nextUnvisited(updated);

  if (next === null) {
    await finishSweep(updated, generation);
    return;
  }
  if (!(await writeSweep(updated, generation)) || !isCurrent(generation)) return;

  phase = 'waiting-navigation';
  lastActivityAt = Date.now();
  const wait = await pageDelayMs();
  if (!isCurrent(generation)) return;
  queueWhenIdle(() => location.assign(pageUrl(next)), wait);
}

async function handleEndPage(generation) {
  if (!isCurrent(generation)) return;
  const page = currentPage();
  const sweep = await readSweep(generation);
  if (!isCurrent(generation)) return;

  const detected = totalPages();
  const total = Math.max(detected ?? 0, sweep.total ?? 0) || null;
  const noConfirmedPages = !(sweep.visited || []).length;
  const inconsistent = Boolean(total && page < total);

  // Walmart briefly renders the no-results panel on slow/failed loads. Confirm
  // it instead of either truncating the sweep or returning with a stuck guard.
  if (inconsistent || noConfirmedPages) {
    const attempts = ((sweep.endRetries || {})[page] || 0) + 1;
    const updated = {
      ...sweep,
      generation,
      total,
      endRetries: { ...(sweep.endRetries || {}), [page]: attempts }
    };
    await writeSweep(updated, generation);
    if (!isCurrent(generation)) return;

    if (attempts >= TRANSIENT_END_MAX_RELOADS) {
      if (!inconsistent) {
        // A genuinely empty catalogue has no delivered page to mark. Three
        // identical renders are enough confirmation to finish quietly.
        await finishSweep(sweep, generation);
        return;
      }
      await reportUnusable(
        `Reviewer page ${page} still showed no results after ${attempts} reloads`,
        generation
      );
      return;
    }
    phase = 'transient-end-retry';
    const wait = await pageDelayMs();
    if (!isCurrent(generation)) return;
    queueWhenIdle(() => location.reload(), wait, 'transient-end-retry');
    return;
  }

  await finishSweep({ ...sweep, total }, generation);
}

// --- role and relay lifecycle ------------------------------------------------

function startObserver() {
  if (observer) return;
  observer = new MutationObserver(() => {
    // Schedule once on the first mutation; continuously changing widgets must
    // not postpone extraction forever by repeatedly resetting a debounce.
    if (!relaying && !actionTimer) scheduleRelay(1_000, false);
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

function stopObserver() {
  observer?.disconnect();
  observer = null;
}

function applyRole(message) {
  const next = {
    active: Boolean(message?.active),
    tabId: Number.isInteger(message?.tabId) ? message.tabId : role.tabId,
    primaryTabId: Number.isInteger(message?.primaryTabId) ? message.primaryTabId : null,
    generation: Number.isInteger(message?.generation) ? message.generation : role.generation
  };
  const changed = next.active !== role.active || next.generation !== role.generation ||
    next.tabId !== role.tabId;
  Object.assign(role, next);
  if (!changed) return;

  cancelPageWork();
  emptySince = 0;
  ingestRetryAttempt = 0;
  if (!role.active) {
    stopObserver();
    phase = 'passive';
    lastActivityAt = 0;
    return;
  }

  phase = 'starting';
  lastActivityAt = Date.now();
  startObserver();
  scheduleRelay(0, true);
}

async function requestRole() {
  if (roleRequestInFlight) return;
  roleRequestInFlight = true;
  try {
    const reply = await chrome.runtime.sendMessage({ type: 'reviewer:role' });
    if (reply) applyRole(reply);
  } catch {
    if (!role.active) setTimeout(requestRole, 2_000);
  } finally {
    roleRequestInFlight = false;
  }
}

function applyAckRole(ack) {
  if (!ack || !Number.isInteger(ack.generation)) return;
  if (ack.generation !== role.generation || Boolean(ack.active) !== role.active ||
      ack.primaryTabId !== role.primaryTabId) {
    applyRole(ack);
  }
}

async function reportUnusable(reason, generation) {
  if (!isCurrent(generation)) return;
  phase = 'empty-timeout';
  lastActivityAt = Date.now();
  let reply;
  try {
    reply = await chrome.runtime.sendMessage({
      type: 'reviewer:unusable', generation, reason
    });
  } catch {
    scheduleRelay(15_000, true);
    return;
  }
  applyAckRole(reply);
  if (!isCurrent(reply?.generation ?? generation)) return;

  if (reply?.recover === 'reload') {
    queueWhenIdle(
      () => location.reload(),
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
  if (!role.active || relaying || actionTimer) return;
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

    const items = collect();
    if (items.length) {
      emptySince = 0;
      phase = 'ingesting';
      lastActivityAt = Date.now();
      let ack;
      try {
        ack = await chrome.runtime.sendMessage({
          type: 'reviewer:page',
          generation,
          page: currentPage(),
          items
        });
      } catch (err) {
        ack = { ok: false, retryable: true, error: err?.message || String(err) };
      }

      applyAckRole(ack);
      if (!isCurrent(generation)) return;
      if (!ack?.ok) {
        ingestRetryAttempt += 1;
        phase = 'ingest-retry';
        lastActivityAt = Date.now();
        scheduleRelay(retryDelay(ack?.retryable), true);
        return;
      }

      ingestRetryAttempt = 0;
      await advanceDeliveredPage(generation);
      return;
    }

    if (atEndOfResults()) {
      emptySince = 0;
      await handleEndPage(generation);
      return;
    }

    if (!emptySince) emptySince = Date.now();
    if (Date.now() - emptySince >= EMPTY_PAGE_TIMEOUT_MS) {
      emptySince = Date.now();
      await reportUnusable(
        `Reviewer page ${currentPage()} did not render any item cards within ` +
        `${EMPTY_PAGE_TIMEOUT_MS / 1000} seconds`,
        generation
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

requestRole();
setInterval(requestRole, ROLE_POLL_MS);
