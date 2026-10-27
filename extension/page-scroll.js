// A bounded page-reading pass. Focus, scan ownership and navigation belong to
// the caller; this helper only moves the page while its current lease is valid.
var ReviewerPageScroll = (() => {
  async function run(options = {}) {
    const win = options.window || globalThis.window;
    const doc = options.document || win?.document;
    const now = options.now || (() => win?.performance?.now?.() ?? Date.now());
    const startedAt = now();
    const budget = options.budgetMs === undefined ? 8000 : options.budgetMs;
    const requestedSettle = options.settleMs === undefined ? 1200 : options.settleMs;
    const setTimer = options.setTimer || win?.setTimeout?.bind(win) || globalThis.setTimeout;
    const clearTimer = options.clearTimer || win?.clearTimeout?.bind(win) || globalThis.clearTimeout;
    const requestFrame = options.requestFrame || win?.requestAnimationFrame?.bind(win) ||
      (callback => setTimer(callback, 16));
    const cancelFrame = options.cancelFrame || win?.cancelAnimationFrame?.bind(win) || clearTimer;
    const canContinue = options.canContinue || (() => true);
    const pending = new Set();
    const inputTypes = ['wheel', 'touchstart', 'pointerdown', 'keydown'];
    const controller = new AbortController();
    const elapsed = () => {
      const value = now() - startedAt;
      return Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0;
    };
    let steps = 0;
    let distance = 0;
    let outcome = null;
    let deadlineTimer;
    let settle;
    const stopped = new Promise(resolve => { settle = resolve; });
    const report = () => ({ outcome, steps, distance: Math.round(distance), durationMs: elapsed() });
    const state = phase => {
      try { options.onState?.({ phase, steps, distance: Math.round(distance), elapsedMs: elapsed(),
        ...(outcome ? { outcome } : {}) }); } catch { /* Diagnostics cannot own scrolling. */ }
    };
    function stop(reason) {
      if (outcome) return;
      outcome = reason;
      clearTimer?.(deadlineTimer);
      controller.abort();
      for (const cancel of [...pending]) cancel();
      for (const type of inputTypes) win?.removeEventListener?.(type, input, true);
      options.signal?.removeEventListener('abort', abort);
      state('finished');
      settle();
    }
    const abort = () => stop('cancelled');
    const input = event => { if (event.isTrusted === true) abort(); };
    const finite = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
    function geometry() {
      const viewport = win?.innerHeight || doc?.documentElement?.clientHeight;
      const heights = [doc?.scrollingElement?.scrollHeight, doc?.documentElement?.scrollHeight,
        doc?.body?.scrollHeight].filter(value => finite(value) && value > 0);
      const position = win?.scrollY ?? doc?.scrollingElement?.scrollTop ?? 0;
      if (!finite(viewport) || !viewport || !heights.length || !finite(position)) return null;
      return { viewport, position, bottom: Math.max(0, Math.max(...heights) - viewport) };
    }
    if (!win || typeof win.scrollTo !== 'function' || !doc || !geometry() ||
        !Number.isFinite(startedAt) || !finite(budget) || typeof setTimer !== 'function' ||
        typeof clearTimer !== 'function' || !finite(requestedSettle) ||
        (options.settleKey !== undefined && typeof options.settleKey !== 'function')) {
      stop('unavailable');
      return report();
    }
    const settleMs = Math.min(requestedSettle, 5000);
    const deadline = startedAt + Math.min(budget, 30000);
    const random = () => {
      const value = (options.random || Math.random)();
      return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0.5;
    };
    async function guard() {
      if (outcome) return false;
      if (now() >= deadline) { stop('timeout'); return false; }
      let permitted;
      try { permitted = await canContinue(); } catch { permitted = false; }
      if (outcome) return false;
      if (!permitted || options.signal?.aborted) { stop('cancelled'); return false; }
      if (now() >= deadline) { stop('timeout'); return false; }
      return true;
    }
    function wait(frame, ms = 0) {
      if (outcome) return Promise.resolve(false);
      return new Promise(resolve => {
        let id;
        const cancel = () => {
          (frame ? cancelFrame : clearTimer)(id);
          pending.delete(cancel);
          resolve(false);
        };
        const done = () => { pending.delete(cancel); resolve(true); };
        id = frame ? requestFrame(done) : setTimer(done, ms);
        pending.add(cancel);
      });
    }
    async function pause(ms) {
      state('paused');
      if (options.pause) {
        // An injected pause must honor this signal; normal pauses use tracked timers.
        await Promise.race([options.pause(ms, controller.signal), stopped]);
      } else await wait(false, ms);
      return guard();
    }
    function write(top) {
      const before = geometry()?.position;
      win.scrollTo({ top, left: Number.isFinite(win.scrollX) ? win.scrollX : 0, behavior: 'instant' });
      const after = geometry()?.position;
      if (finite(before) && finite(after)) distance += Math.max(0, after - before);
    }
    async function scroll() {
      if (!await guard()) return;
      if (options.startAtTop === true && geometry().position > 0) write(0);
      let stable = null;
      let reduced = false;
      try { reduced = win.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true; }
      catch { /* A missing media-query API still permits bounded scrolling. */ }
      while (await guard()) {
        const current = geometry();
        if (!current) { stop('unavailable'); return; }
        if (current.position >= current.bottom - 1) {
          // Keep the ready bottom and caller's card signature unchanged for a
          // full interval so delayed rendering extends this pass rather than
          // being mistaken for a completed page.
          if (doc.readyState === 'loading') stable = null;
          else {
            let key;
            try { key = options.settleKey?.(); }
            catch { stop('unavailable'); return; }
            if (!stable || stable.position !== current.position || stable.bottom !== current.bottom ||
                stable.viewport !== current.viewport || !Object.is(stable.key, key)) {
              stable = { ...current, key, since: now() };
            }
            if (now() - stable.since >= settleMs) { stop('complete'); return; }
          }
          if (!await pause(180 + random() * 100)) return;
          continue;
        }
        stable = null;
        const target = Math.min(current.bottom, current.position + current.viewport * (0.72 + random() * 0.4));
        const duration = 180 + random() * 180;
        state('scrolling');
        if (reduced) {
          if (!await guard()) return;
          write(target);
        } else {
          const begun = now();
          let progress = 0;
          while (progress < 1) {
            if (!await wait(true) || !await guard()) return;
            const latest = geometry();
            if (!latest) { stop('unavailable'); return; }
            progress = Math.min(1, Math.max(0, (now() - begun) / duration));
            const ease = progress * progress * (3 - 2 * progress);
            const next = Math.min(latest.bottom, current.position + (target - current.position) * ease);
            if (next > latest.position) write(next);
          }
        }
        steps += 1;
        if (!await pause(50 + random() * 110)) return;
      }
    }
    for (const type of inputTypes) win.addEventListener?.(type, input, { capture: true, passive: true });
    options.signal?.addEventListener('abort', abort, { once: true });
    if (options.signal?.aborted) stop('cancelled');
    else {
      deadlineTimer = setTimer(() => stop('timeout'), Math.max(0, deadline - now()));
      scroll().catch(() => stop('unavailable'));
    }
    await stopped;
    return report();
  }
  return Object.freeze({ run });
})();
