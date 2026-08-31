// Runs inside the page the user has already opened and signed into. It reads
// what is on screen and hands it to the background worker -- it does not log
// in, does not fetch anything on its own, and does nothing on pages the user
// has not navigated to.
//
// The reload schedule lives in background.js, not here: a content script dies
// with its page, so a reload landing on a sign-in redirect would silently end
// the loop. This script only reports whether the user is mid-interaction.

const ITEM_LINK = 'a[href*="/ip/"]';
const PRICE_RE = /\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/;
const DEFAULT_PATH_PATTERN = '^/(reviewer|reviews)';

// Order history and tracking pages are full of /ip/ links to things the user
// already bought. Relaying those would alert on their own past purchases as if
// they were new offers, so they are excluded whatever the pattern says.
const EXCLUDE_PATH = /\/(orders?|purchase-history|track|returns)\b/i;

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

function priceNear(anchor) {
  // Walk up a few levels looking for a dollar amount in the item's card.
  let node = anchor;
  for (let depth = 0; node && depth < 5; depth += 1) {
    const match = (node.innerText || '').match(PRICE_RE);
    if (match) return parseFloat(match[1].replace(/,/g, ''));
    node = node.parentElement;
  }
  return null;
}

function titleFor(anchor) {
  const explicit =
    anchor.getAttribute('aria-label') ||
    anchor.querySelector('img')?.getAttribute('alt') ||
    anchor.innerText;
  return (explicit || '').replace(/\s+/g, ' ').trim().slice(0, 200);
}

function collect() {
  const byId = new Map();
  for (const anchor of document.querySelectorAll(ITEM_LINK)) {
    const id = idFromHref(anchor.getAttribute('href') || '');
    if (!id || byId.has(id)) continue;
    const title = titleFor(anchor);
    if (!title) continue;
    byId.set(id, {
      item_id: `ip-${id}`,
      title,
      value_usd: priceNear(anchor),
      url: new URL(anchor.getAttribute('href'), location.origin).toString().split('?')[0],
      category: document.title.slice(0, 120),
      source: 'extension'
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
