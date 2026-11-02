// Observe new cards rendered after an acknowledgement, including during the
// existing wait before the next page and after a scan completes. This path
// sends only local relays: it never navigates, refreshes, or restarts a sweep.
var ReviewerLiveObserver = (() => {
  function create({ session, canObserve, canRun, ready, current, collect, send,
    acknowledgeRole, diagnostic, page, acquire, release,
    setTimer = setTimeout, clearTimer = clearTimeout }) {
    const state = session.visible;
    const ids = state.acknowledgedIds;
    function remember(items) {
      for (const item of items) ids.add(item.item_id);
      // Only a document-local optimization; server dedupe remains authoritative.
      while (ids.size > 5_000) ids.delete(ids.values().next().value);
    }
    function cancel() { clearTimer(state.timer); state.timer = null; }
    function reset() { cancel(); ids.clear(); state.retries = 0; }
    function schedule(delayMs = 1_000) {
      if (!session.role.active || !canObserve() || state.timer || !canRun()) return;
      const token = session.capture();
      state.timer = setTimer(() => {
        state.timer = null;
        if (!session.current(token)) return;
        observe().catch(() => { if (session.current(token)) schedule(15_000); });
      }, delayMs);
    }
    async function observe() {
      cancel();
      if (!canRun() || !session.role.active || !canObserve()) return;
      const operation = acquire();
      if (!operation) { schedule(2_000); return; }
      const generation = session.role.generation;
      try {
        if (!(await ready(generation)) || !canObserve()) return;
        const items = collect(ids).filter(item => !ids.has(item.item_id));
        if (!items.length) { state.retries = 0; return; }
        const startedAt = Date.now();
        let ack;
        try { ack = await send({ type: 'reviewer:page', generation, page: page(), items }); }
        catch { ack = { ok: false, retryable: true }; }
        acknowledgeRole(ack);
        if (!current(generation) || !session.current(operation)) return;
        if (ack?.ok) {
          remember(items);
          state.retries = 0;
          diagnostic('live-update', { items: items.length, durationMs: Date.now() - startedAt });
        } else {
          state.retries += 1;
          const delayMs = Math.min(60_000, 2_000 * 2 ** Math.min(5, state.retries - 1));
          diagnostic('ingest-retry', { attempt: state.retries, delayMs });
          cancel();
          schedule(ack?.retryable === false ? 60_000 : delayMs);
        }
      } finally { release(operation); }
    }
    return Object.freeze({ remember, cancel, reset, schedule, observe });
  }
  return Object.freeze({ create });
})();
