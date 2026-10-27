// Optional Windows input transport. Page reads stay in Chrome's isolated world;
// only the narrowly scoped Native Messaging host can move/hold the OS pointer.
var VerificationNative = (() => {
  const PERMISSIONS = { permissions: ['nativeMessaging', 'scripting'] };
  const REASONS = new Set(['unsupported-platform', 'invalid-request', 'invalid-origin', 'invalid-window',
    'helper-busy', 'desktop-locked', 'user-busy', 'button-held', 'focus-changed', 'window-changed',
    'viewport-mismatch', 'target-obscured', 'target-expired', 'user-input', 'input-failed',
    'release-failed', 'watchdog-unavailable', 'already-used', 'not-prepared', 'host-unavailable',
    'max-duration', 'closed']);
  const failure = (stage, reason = 'host-unavailable') => Object.assign(new Error('Windows input unavailable'),
    { verificationStage: stage, verificationReason: 'command-failed',
      nativeReason: REASONS.has(reason) ? reason : 'host-unavailable' });
  function safeFallback(result) {
    if (!result || result.cleanupConfirmed !== true || result.cleanupErrorStage) return false;
    if (result.outcome === 'input-error') return result.errorStage === 'move' &&
      result.errorReason === 'timeout' && result.pressSent === false;
    return result.outcome === 'needs-attention' &&
      ['deadline', 'failed', 'stalled', 'max-duration'].includes(result.releaseReason) &&
      result.pressSent === true && result.pressConfirmed === true && result.releaseConfirmed === true &&
      Number.isFinite(result.holdMs) && result.holdMs > 0;
  }
  function validState(s) {
    return s && typeof s.ready === 'boolean' && (s.reason === null || REASONS.has(s.reason)) &&
      ['idle', 'prepared', 'holding', 'released', 'aborted', 'closed'].includes(s.phase) &&
      ['pressSent', 'pressConfirmed', 'releaseConfirmed'].every(k => typeof s[k] === 'boolean') &&
      Number.isFinite(s.idleMs) && s.idleMs >= 0 && Number.isFinite(s.holdMs) && s.holdMs >= 0 &&
      (s.token === undefined || (typeof s.token === 'string' && /^[a-zA-Z0-9_-]{8,128}$/.test(s.token)));
  }
  async function bounded(promise, ms, stage = 'inspect') {
    let timer;
    return Promise.race([Promise.resolve(promise), new Promise((_, reject) => {
      timer = setTimeout(() => reject(failure(stage)), ms);
    })]).finally(() => clearTimeout(timer));
  }
  async function connectBridge(api, windowId) {
    const token = crypto.randomUUID();
    const name = `reviewer-native:${token}`;
    const url = api.runtime.getURL(`native-bridge.html?session=${token}`);
    let tab, port, candidate, closed = false, abandoned = false, sequence = 0;
    const pending = new Map();
    let accept, rejectReady;
    const ready = new Promise((resolve, reject) => { accept = resolve; rejectReady = reject; });
    const disconnected = () => {
      closed = true;
      rejectReady(failure('inspect'));
      for (const p of pending.values()) p.reject(failure(p.stage));
      pending.clear();
    };
    function bind(incoming) {
      if (!tab || incoming.sender?.id !== api.runtime.id || incoming.sender?.tab?.id !== tab.id ||
          incoming.sender?.tab?.windowId !== windowId || incoming.sender?.url !== url) {
        incoming.disconnect(); return;
      }
      port = incoming;
      api.runtime.onConnect.removeListener(connected);
      port.onDisconnect.addListener(disconnected);
      port.onMessage.addListener(message => {
        if (message?.type === 'ready') { accept(); return; }
        const response = message?.response;
        const p = pending.get(response?.id);
        if (!p) return;
        pending.delete(response.id);
        if (response.v !== 1 || typeof response.ok !== 'boolean' || !validState(response.state)) {
          p.reject(failure(p.stage)); port.disconnect(); return;
        }
        if (!response.ok) {
          p.reject(Object.assign(failure(p.stage, response.error), { nativeState: response.state }));
        } else p.resolve(response.state);
      });
      port.postMessage({ type: 'start' });
    }
    function connected(incoming) {
      if (incoming.name !== name) return;
      if (incoming.sender?.id !== api.runtime.id || incoming.sender?.url !== url ||
          incoming.sender?.tab?.windowId !== windowId) { incoming.disconnect(); return; }
      if (abandoned || port || candidate) { incoming.disconnect(); return; }
      if (!tab) candidate = incoming; else bind(incoming);
    }
    api.runtime.onConnect.addListener(connected);
    async function close() {
      abandoned = true;
      api.runtime.onConnect.removeListener(connected);
      try { port?.disconnect(); } finally {
        candidate?.disconnect();
        disconnected();
        if (tab) await api.tabs.remove(tab.id);
      }
    }
    try {
      const creating = api.tabs.create({ windowId, active: false, url }).then(async created => {
        if (abandoned) { await api.tabs.remove(created.id); return; }
        tab = created;
        if (candidate) { const incoming = candidate; candidate = null; bind(incoming); }
      });
      await bounded(Promise.all([creating, ready]), 10000);
    } catch (error) { await close().catch(() => undefined); throw error; }
    return {
      async rpc(op, payload = {}, stage = 'inspect') {
        if (closed) throw failure(stage);
        const id = `n:${++sequence}`;
        try {
          return await bounded(new Promise((resolve, reject) => {
            pending.set(id, { resolve, reject, stage });
            try { port.postMessage({ type: 'request', request: { v: 1, id, op, payload } }); }
            catch { reject(failure(stage)); }
          }), 2500, stage);
        } finally { pending.delete(id); }
      }, close
    };
  }
  async function open(api, tabId, enabled, { connect = connectBridge } = {}) {
    if (!await api.permissions.contains(PERMISSIONS)) throw failure('inspect');
    const initial = await api.tabs.get(tabId);
    if (!VerificationChrome.allowedUrl(initial?.url) || initial.status !== 'complete' ||
        (initial.pendingUrl && initial.pendingUrl !== initial.url)) throw failure('inspect', 'invalid-window');
    const channel = await connect(api, initial.windowId);
    let inputAllowed = true, stopped = false, closed = false, documentId, baseline;
    let releasePromise;
    const input = { transport: 'native', pressSent: false, pressConfirmed: false, releaseConfirmed: false,
      pressStartedAt: null, releaseStartedAt: null };
    function update(state) {
      input.pressSent = state.pressSent;
      input.pressConfirmed = state.pressConfirmed;
      input.releaseConfirmed = state.releaseConfirmed;
      input.nativeHoldMs = state.holdMs;
      if (state.reason) input.nativeReason = state.reason;
      if (state.phase === 'aborted') stopped = true;
      return state;
    }
    async function rpc(op, payload = {}, stage = 'inspect') {
      try { return update(await channel.rpc(op, payload, stage)); }
      catch (error) {
        if (error.nativeState) update(error.nativeState);
        input.nativeReason = error.nativeReason || 'host-unavailable';
        throw error.verificationStage ? error : failure(stage);
      }
    }
    async function active() {
      const tab = await api.tabs.get(tabId);
      return tab.windowId === initial.windowId && tab.active === true && tab.status === 'complete' &&
        (!tab.pendingUrl || tab.pendingUrl === tab.url) && VerificationChrome.allowedUrl(tab.url) &&
        (await api.windows.get(initial.windowId)).focused === true;
    }
    async function permitted() {
      return !stopped && !closed && await enabled() &&
        (await api.storage.local.get(['verificationNativeEnabled'])).verificationNativeEnabled === true;
    }
    async function page() {
      const tab = await api.tabs.get(tabId);
      if (tab.windowId !== initial.windowId || !VerificationChrome.allowedUrl(tab.url) ||
          tab.status !== 'complete' || (tab.pendingUrl && tab.pendingUrl !== tab.url)) return { eligible: false };
      const reads = await bounded(api.scripting.executeScript({ target: { tabId },
        func: VerificationChrome.readPage, world: 'ISOLATED' }), 2000);
      if (reads.length !== 1 || reads[0].frameId !== 0 || !reads[0].documentId || !reads[0].result) {
        throw failure('inspect');
      }
      documentId ??= reads[0].documentId;
      const result = reads[0].result;
      if (reads[0].documentId !== documentId) inputAllowed = false;
      baseline ??= result;
      return result;
    }
    async function up() {
      if (!input.pressSent || input.releaseConfirmed) return;
      if (!releasePromise) {
        input.releaseStartedAt ??= performance.now();
        releasePromise = rpc('release', {}, 'release').catch(error => { releasePromise = null; throw error; });
      }
      return releasePromise;
    }
    const changed = (id, change) => {
      if (id === tabId && (change.url || change.status === 'loading')) {
        inputAllowed = false;
        cancelHolding();
      }
    };
    function cancelHolding() {
      if (input.pressSent && !input.releaseConfirmed) {
        stopped = true;
        up().catch(() => undefined);
      }
    }
    const activated = info => { if (info.windowId === initial.windowId && info.tabId !== tabId) cancelHolding(); };
    const removed = id => { if (id === tabId) { inputAllowed = false; cancelHolding(); } };
    const focused = id => { if (id !== initial.windowId) cancelHolding(); };
    api.tabs.onUpdated.addListener(changed);
    api.tabs.onActivated?.addListener(activated);
    api.tabs.onRemoved?.addListener(removed);
    api.windows.onFocusChanged?.addListener(focused);
    async function detach() {
      if (closed) return;
      try { await up(); }
      finally {
        closed = true;
        api.tabs.onUpdated.removeListener(changed);
        api.tabs.onActivated?.removeListener(activated);
        api.tabs.onRemoved?.removeListener(removed);
        api.windows.onFocusChanged?.removeListener(focused);
        await channel.close();
      }
    }
    try {
      const probe = await rpc('probe');
      if (!await permitted() || probe.idleMs < 5000 ||
          (!probe.ready && probe.reason !== 'focus-changed')) throw failure('inspect', probe.reason || 'user-busy');
      await api.tabs.update(tabId, { active: true });
      await api.windows.update(initial.windowId, { focused: true });
      if (!await permitted() || !await active() || !(await rpc('probe')).ready) throw failure('inspect');
    } catch (error) { await detach().catch(() => undefined); throw error; }
    return {
      now: () => performance.now(), sleep: ms => new Promise(resolve => setTimeout(resolve, ms)),
      inputState: () => ({ ...input }), page,
      async enabled() {
        if (input.pressSent && !input.releaseConfirmed) {
          if (!inputAllowed || !await active()) cancelHolding();
          await rpc('status');
        }
        return permitted();
      },
      // Closed widget internals are not available in the isolated DOM. The engine
      // uses its existing bounded hold and confirms recovery from the real page.
      feedback: async () => 'unknown',
      async down(point) {
        const fresh = await page();
        const matches = p => p.eligible && p.challenge && p.clock === baseline.clock && p.target &&
          Math.abs(p.target.x - point.x) <= 8 && Math.abs(p.target.y - point.y) <= 8;
        if (!inputAllowed || !matches(fresh) || !await permitted() || !await active()) throw failure('move');
        const prepared = await rpc('prepare', { viewport: fresh.viewport, point }, 'move');
        const hovered = await page();
        if (!inputAllowed || !matches(hovered) || !await permitted() || !await active()) throw failure('press');
        // A lost acknowledgement is uncertain: never treat this as no press.
        input.pressSent = true;
        input.pressStartedAt = performance.now();
        await rpc('press', { token: prepared.token }, 'press');
      }, up, detach
    };
  }
  return { open, connectBridge, safeFallback, PERMISSIONS };
})();
