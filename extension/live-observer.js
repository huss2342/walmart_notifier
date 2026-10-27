// Observe new cards rendered after an acknowledgement, including during the
// existing wait before the next page and after a scan completes. This path
// sends only local relays: it never navigates, refreshes, or restarts a sweep.
const acknowledgedVisibleIds = new Set();
let visibleUpdateTimer = null;
let visibleUpdateRetries = 0;

function rememberAcknowledged(items) {
  for (const item of items) acknowledgedVisibleIds.add(item.item_id);
  // Only a document-local optimization; server dedupe remains authoritative.
  while (acknowledgedVisibleIds.size > 5_000) {
    acknowledgedVisibleIds.delete(acknowledgedVisibleIds.values().next().value);
  }
}

function cancelVisibleObservation() {
  clearTimeout(visibleUpdateTimer);
  visibleUpdateTimer = null;
}

function resetVisibleObservations() {
  cancelVisibleObservation();
  acknowledgedVisibleIds.clear();
  visibleUpdateRetries = 0;
}

function canObserveVisible() {
  // A pending facet selection is still showing its parent's cards, which the
  // coordinator must reject until the new result scope has been confirmed.
  return !role.auxiliary?.selection &&
    (phase === 'complete' || phase === 'waiting-navigation') && !navigationPending;
}

function scheduleVisibleObservation(delayMs = 1_000) {
  if (!role.active || !canObserveVisible() || visibleUpdateTimer || verificationObserved) return;
  visibleUpdateTimer = setTimeout(() => {
    visibleUpdateTimer = null;
    observeVisibleUpdate().catch(() => scheduleVisibleObservation(15_000));
  }, delayMs);
}

async function observeVisibleUpdate() {
  cancelVisibleObservation();
  if (checkVerification() || !role.active || !canObserveVisible() || !keepMonitoringScope()) return;
  if (relaying) { scheduleVisibleObservation(2_000); return; }
  const generation = role.generation;
  relaying = true;
  try {
    if (!(await isReviewerPage()) || !isCurrent(generation) || !canObserveVisible()) return;
    const items = collect(acknowledgedVisibleIds)
      .filter(item => !acknowledgedVisibleIds.has(item.item_id));
    if (!items.length) { visibleUpdateRetries = 0; return; }
    const startedAt = Date.now();
    let ack;
    try {
      ack = await sendToWorker({ type: 'reviewer:page', generation,
        page: currentPage(), items });
    } catch { ack = { ok: false, retryable: true }; }
    applyAckRole(ack);
    if (!isCurrent(generation)) return;
    if (ack?.ok) {
      rememberAcknowledged(items);
      visibleUpdateRetries = 0;
      diagnostic('live-update', { items: items.length, durationMs: Date.now() - startedAt });
    } else {
      visibleUpdateRetries += 1;
      const delayMs = Math.min(60_000, 2_000 * 2 ** Math.min(5, visibleUpdateRetries - 1));
      diagnostic('ingest-retry', { attempt: visibleUpdateRetries, delayMs });
      cancelVisibleObservation();
      scheduleVisibleObservation(ack?.retryable === false ? 60_000 : delayMs);
    }
  } finally {
    relaying = false;
  }
}
