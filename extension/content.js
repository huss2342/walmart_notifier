// Runs inside the page the user has already opened and signed into. It reads
// what is on screen and hands it to the background worker -- it does not log
// in, does not fetch anything on its own, and does nothing on pages the user
// has not navigated to.

const ITEM_LINK = 'a[href*="/ip/"]';
const PRICE_RE = /\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/;
const REVIEW_PATH = /\/(reviews|account|reviewer)/i;

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

let lastPayload = '';

function relay() {
  if (!REVIEW_PATH.test(location.pathname)) return;
  const items = collect();
  if (!items.length) return;

  // Cheap change detection so idling on a page does not re-POST every few
  // seconds; the server dedupes too, this just saves the round trip.
  const fingerprint = items.map((i) => i.item_id).sort().join(',');
  if (fingerprint === lastPayload) return;
  lastPayload = fingerprint;

  chrome.runtime.sendMessage({ type: 'items', items });
}

const observer = new MutationObserver(() => {
  clearTimeout(window.__relayTimer);
  window.__relayTimer = setTimeout(relay, 1500);
});
observer.observe(document.body, { childList: true, subtree: true });
relay();
