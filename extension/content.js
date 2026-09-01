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
const VALUE_GLOBAL_RE = /Valued\s*at\s*\$/gi;
const OUT_OF_STOCK_RE = /\bout of stock\b/i;
const CLAIMS_RE = /Free items remaining\s*(\d+)\s*item/i;

// Merchandising badges rendered inside the card link, ahead of the title.
const BADGE_RE = /^(clearance|rollback|reduced price|new|best seller|popular pick)\b/i;
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

// Never yank the page out from under someone mid-claim.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === 'busy?') {
    sendResponse({ busy: Date.now() - lastInteraction < INTERACTION_GRACE_MS });
  }
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

    const card = cardFor(anchor);
    // No card means no stated retail value, which means no way to tell a $5
    // sample from a $200 one. Alerting on it would defeat the whole filter.
    if (!card) continue;
    // Claiming an out-of-stock item is not possible, so waking someone for one
    // is pure noise.
    if (OUT_OF_STOCK_RE.test(card.text)) continue;

    const title = titleFor(anchor, card);
    if (!title) continue;

    const value = card.text.match(VALUE_RE);
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
  if (!items.length) return;
  // The background worker dedupes across reloads and the server dedupes again.
  chrome.runtime.sendMessage({ type: 'items', items });
}

let relayTimer;
const observer = new MutationObserver(() => {
  clearTimeout(relayTimer);
  relayTimer = setTimeout(relay, 1500);
});
observer.observe(document.body, { childList: true, subtree: true });
relay();
