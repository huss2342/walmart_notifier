// Runs inside the reviewer page the user already has open and signed into. It
// reads what is on screen and hands it to the background worker -- it does not
// log in, does not fetch anything on its own, and does nothing on pages the
// user has not navigated to.
//
// The reload schedule lives in background.js, not here: a content script dies
// with its page, so a reload landing on a sign-in redirect would silently end
// the loop. This script only reports whether the user is mid-interaction.

const ITEM_LINK = 'a[href*="/ip/"]';
const DEFAULT_PATH_PATTERN = '^/reviews/claim-product';

// The portal states retail value as `Free(Valued at $5.99)`. That is the number
// the rules care about. The price inside the card's own link text is the item's
// sale price, and for a clearance item it is followed by `Was $6.99` -- reading
// either of those instead gives the wrong figure for exactly the items most
// worth alerting on.
const VALUE_RE = /Valued\s*at\s*\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i;
// Deliberately does NOT require the dollar sign. Some listings render
// `Free(Valued at )` with no figure at all; keying the card boundary on `$`
// made those cards invisible, so the item was dropped outright instead of
// being surfaced with an unknown value for `alert_on_unknown_value` to judge.
const VALUE_GLOBAL_RE = /Valued\s*at/gi;
const OUT_OF_STOCK_RE = /\bout of stock\b/i;
const CLAIMS_RE = /Free items remaining\s*(\d+)\s*item/i;

// Merchandising badges rendered inside the card link, ahead of the title.
// Longest first: "new arrival" must be tried before "new", or the badge strips
// to "New" and leaves "arrival" glued to the front of the title.
const BADGE_RE =
  /^(new arrival|reduced price|best seller|popular pick|clearance|rollback|deal|new)\b/i;
const TRAILING_PRICE_RE = /\s*\$\s*[0-9][0-9,]*(?:\.[0-9]{1,2})?(?:\s*was\s*\$\s*[0-9][0-9,]*(?:\.[0-9]{1,2})?)?\s*$/i;

// Order and returns pages are full of /ip/ links to things the user already
// bought. Relaying those would alert on their own past purchases as if they
// were new offers.
const EXCLUDE_PATH = /\/(orders?|purchase-history|track|returns)\b/i;

// How far up from the link to look for the enclosing item card.
const CARD_MAX_DEPTH = 8;

const INTERACTION_GRACE_MS = 30_000;

let lastInteraction = 0;
for (const evt of ['click', 'keydown', 'scroll']) {
  document.addEventListener(evt, () => { lastInteraction = Date.now(); },
                            { passive: true, capture: true });
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== 'busy?') return undefined;
  // Never yank the page out from under someone mid-claim, and never restart a
  // sweep that is still walking. Chrome throttles timers in hidden tabs to
  // about one per minute, so a backgrounded sweep can outlast the refresh
  // interval; resetting it on every alarm would mean the later pages are never
  // reached at all.
  readSweep().then((sweep) => {
    sendResponse({
      busy: Date.now() - lastInteraction < INTERACTION_GRACE_MS,
      // A sweep with nothing visited yet is not running, and must not block
      // the refresh -- nextUnvisited() answers "page 1" for a fresh record.
      sweeping: (sweep.visited || []).length > 0 && nextUnvisited(sweep) !== null
    });
  });
  return true;   // response is asynchronous
});

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
  const m = href.match(/\/ip\/(?:[^/?#]+\/)?(\d{6,})/);
  return m ? m[1] : null;
}

/** The nearest ancestor holding exactly one item's worth of card text. */
function cardFor(anchor) {
  let node = anchor.parentElement;
  for (let depth = 0; node && depth < CARD_MAX_DEPTH; depth += 1) {
    const text = node.innerText || '';
    const hits = text.match(VALUE_GLOBAL_RE);
    if (hits) {
      // More than one means we have climbed past the card into the grid, where
      // the next item's value sits as close to this link as its own does.
      return hits.length === 1 ? { node, text } : null;
    }
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
  // The image alt is the cleanest source; the link text carries the badge
  // prefix and the sale price, and the aria-label often carries both.
  const candidates = [
    anchor.querySelector('img')?.getAttribute('alt'),
    anchor.getAttribute('aria-label'),
    anchor.innerText,
    card?.text.split('\n').find((line) => line.length > 20 && !VALUE_RE.test(line)),
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

    // A card whose layout defeats the walk used to be dropped outright, which
    // is the worst outcome: a $79.99 item vanished with nothing logged. Emit
    // it with an unknown value instead and let `alert_on_unknown_value`
    // decide -- a spurious buzz is far cheaper than a silent miss.
    const card = cardFor(anchor);
    // Claiming an out-of-stock item is not possible, so waking someone for one
    // is pure noise.
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

async function relay() {
  if (!(await isReviewerPage())) return;
  const items = collect();
  if (items.length) {
    // The background worker dedupes across reloads and the server dedupes again.
    chrome.runtime.sendMessage({ type: 'items', items, page: currentPage() });
  } else if (!atEndOfResults()) {
    // No cards and no end marker means the page is still rendering. Wait for
    // the next mutation rather than calling this the end of the catalogue.
    return;
  }
  scheduleNextPage();
}

// --- pagination -------------------------------------------------------------
// A sweep visits every page once. Which pages remain is tracked explicitly in
// storage rather than inferred from the current URL: Walmart rewrites the
// query string (parameter order differs between loads), so "whatever page I am
// on, plus one" drifts and the walk visibly jumped 1 -> 7 -> 6 -> 10.
//
// The explicit set also survives the content script dying, a reload landing
// somewhere unexpected, or the user clicking a page link mid-sweep -- the next
// relay simply resumes with the lowest page not yet seen.

const PAGE_JITTER = 0.25;
const DEFAULT_PAGE_DELAY_S = 5;
const SWEEP_KEY = 'sweep';
// The portal answers a past-the-end page with a "no search results" panel.
const END_OF_RESULTS_RE = /no search results/i;
// Backstop for the case where the page count cannot be read at all.
const HARD_PAGE_CAP = 100;

function currentPage() {
  const raw = parseInt(new URLSearchParams(location.search).get('page') || '1', 10);
  return Number.isFinite(raw) && raw > 0 ? raw : 1;
}

function pageUrl(page) {
  const url = new URL(location.href);
  if (page <= 1) {
    url.searchParams.delete('page');
  } else {
    url.searchParams.set('page', String(page));
  }
  return url.toString();
}

function atEndOfResults() {
  return END_OF_RESULTS_RE.test(document.body.innerText || '');
}

/** Highest page number in the pager, or null if the pager is not on the page.
 *
 * The last page sits in a plain <div> rather than an <a>, so the whole list is
 * scanned for numbers instead of just the page-number anchors.
 */
function totalPages() {
  const list = document.querySelector('[data-automation-id="page-number"]')?.closest('ul');
  if (!list) return null;
  const numbers = (list.innerText.match(/\d+/g) || [])
    .map(Number)
    .filter((n) => n > 0 && n <= HARD_PAGE_CAP);
  return numbers.length ? Math.max(...numbers) : null;
}

async function readSweep() {
  const { [SWEEP_KEY]: sweep } = await chrome.storage.local.get([SWEEP_KEY]);
  if (sweep && Array.isArray(sweep.visited)) return sweep;
  return { total: null, visited: [] };
}

/** Lowest page in 1..total not yet visited, or null when the sweep is done.
 *
 * An unknown total is NOT "done". The pager renders late, so a relay firing
 * before it exists reads null -- and treating that as completion ended every
 * sweep after a single page.
 */
function nextUnvisited(sweep) {
  const seen = new Set(sweep.visited || []);
  if (!sweep.total) {
    // Total still unknown: walk forward from the highest page seen so far and
    // rely on the end-of-results panel to stop.
    const highest = seen.size ? Math.max(...seen) : 0;
    return highest >= HARD_PAGE_CAP ? null : highest + 1;
  }
  for (let page = 1; page <= sweep.total; page += 1) {
    if (!seen.has(page)) return page;
  }
  return null;
}

// Set synchronously. The storage reads below are async, so a check that only
// consulted a timer handle would let two observer-driven calls through and
// queue two navigations.
let advancing = false;

async function scheduleNextPage() {
  if (advancing) return;
  advancing = true;
  try {
    const page = currentPage();
    const sweep = await readSweep();
    const ended = atEndOfResults();

    // A page that reports a total is authoritative; past the end there is no
    // pager at all, so fall back to what the sweep already knew.
    const total = totalPages() ?? sweep.total;
    const visited = ended
      ? sweep.visited                       // nothing real here to record
      : [...new Set([...sweep.visited, page])];

    const updated = { total, visited };
    // The end-of-results panel is definitive whatever the counters say.
    const next = ended ? null : nextUnvisited(updated);

    if (next === null) {
      // Sweep complete. Clear it so the next refresh starts a fresh pass.
      await chrome.storage.local.remove(SWEEP_KEY);
      await chrome.storage.local.set({ lastSweepDone: Date.now(), lastSweepPages: visited.length });
      return;
    }

    await chrome.storage.local.set({ [SWEEP_KEY]: updated });
    if (next === page) return;   // already here; wait for this page to render

    const { pageDelaySeconds = DEFAULT_PAGE_DELAY_S } =
      await chrome.storage.local.get(['pageDelaySeconds']);
    const base = Math.max(1, parseInt(pageDelaySeconds, 10) || DEFAULT_PAGE_DELAY_S) * 1000;
    const delay = base * (1 + (Math.random() * 2 - 1) * PAGE_JITTER);

    setTimeout(() => {
      // Same courtesy as the reload: never navigate out from under someone
      // mid-claim. Release the guard so a later relay can retry.
      if (Date.now() - lastInteraction < INTERACTION_GRACE_MS) {
        advancing = false;
        return;
      }
      location.assign(pageUrl(next));
    }, delay);
  } catch (err) {
    advancing = false;
    console.warn('Reviewer Item Relay: could not advance the sweep', err);
  }
}

let relayTimer;
const observer = new MutationObserver(() => {
  clearTimeout(relayTimer);
  relayTimer = setTimeout(relay, 1500);
});
observer.observe(document.body, { childList: true, subtree: true });
relay();
