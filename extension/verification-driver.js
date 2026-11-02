// Chrome debugger transport: https://developer.chrome.com/docs/extensions/reference/api/debugger
// Only fixed read-only DOM expressions and tab-local Input commands are sent.
var VerificationChrome = (() => {
  // Acknowledgement bounds. A pointer move can be acknowledged later than a
  // press when the page's pointer handlers are busy under load, so the move
  // gets its own wider bound. Press/release and the 30-second release watchdog
  // are unchanged.
  const INPUT_ACK_MS = 5000;
  const MOVE_ACK_MS = 10000;
  function allowedUrl(raw) {
    try {
      const u = new URL(raw);
      return u.protocol === 'https:' && u.hostname === 'www.walmart.com' &&
        !u.username && !u.password && !u.port &&
        (/^\/reviews\/claim-product\/?$/.test(u.pathname) || /^\/blocked\/?$/.test(u.pathname));
    } catch { return false; }
  }

  // Compatible local wrapper. Page-world calls serialize observePage itself so
  // they do not depend on extension globals. No account text or URL is returned.
  function readPage(doc = document, current = location) {
    return ReviewerVerificationObservation.observePage('driver', doc, current);
  }

  async function open(api, tabId, enabled) {
    const tab = await api.tabs.get(tabId);
    if (!Number.isInteger(tabId) || !allowedUrl(tab?.url) || tab.status !== 'complete' ||
        (tab.pendingUrl && tab.pendingUrl !== tab.url)) throw new Error('unsupported');
    const target = { tabId };
    let owned = false;
    let stopped = false;
    let inputAllowed = true;
    let watchdog = null;
    let point = null;
    let releasePromise = null;
    const input = { pressSent: false, pressConfirmed: false, releaseConfirmed: false,
      pressStartedAt: null, releaseStartedAt: null };
    const changed = (id, info, latest) => {
      if (id !== tabId) return;
      if (info.status === 'loading' || info.url) inputAllowed = false;
      if (info.url && !allowedUrl(info.url || latest?.url)) stopped = true;
    };
    const detached = source => { if (source.tabId === tabId) { stopped = true; owned = false; } };
    api.tabs.onUpdated.addListener(changed);
    api.debugger.onDetach.addListener(detached);
    const unlisten = () => {
      api.tabs.onUpdated.removeListener(changed);
      api.debugger.onDetach.removeListener(detached);
    };
    const failure = (stage, reason) => Object.assign(new Error('Verification control failed'),
      { verificationStage: stage, verificationReason: reason });
    function bounded(promise, ms = 2000, stage = 'inspect') {
      let timer;
      return Promise.race([Promise.resolve(promise).catch(() => { throw failure(stage, 'command-failed'); }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(failure(stage, 'timeout')), ms); })
      ]).finally(() => clearTimeout(timer));
    }
    const command = (method, params = {}) => {
      const inputStage = { mouseMoved: 'move', mousePressed: 'press', mouseReleased: 'release' };
      const stage = method === 'Input.dispatchMouseEvent' ? inputStage[params.type] : 'inspect';
      if (!owned) return Promise.reject(failure(stage, 'detached'));
      // Input can take longer than a DOM read to acknowledge. Keep it bounded
      // and retain the independent 30-second release watchdog.
      const limit = method === 'Input.dispatchMouseEvent'
        ? (params.type === 'mouseMoved' ? MOVE_ACK_MS : INPUT_ACK_MS) : 2000;
      return bounded(api.debugger.sendCommand(target, method, params), limit, stage);
    };
    try {
      // If attachment times out but later succeeds, clean up only our own late session.
      let abandoned = false;
      const attaching = api.debugger.attach(target, '1.3').then(async () => {
        if (abandoned) { await api.debugger.detach(target); return; }
        owned = true;
      });
      try { await bounded(attaching, 5000); }
      catch { abandoned = true; throw new Error('attach-unavailable'); }
      if (stopped || !inputAllowed) throw new Error('navigation-changed');
      await command('DOM.enable');
      await command('CSS.enable');
    } catch {
      if (owned) { try { await bounded(api.debugger.detach(target)); } catch { /* Already detached. */ } }
      unlisten();
      throw new Error('attach-unavailable');
    }
    async function page() {
      if (stopped || !allowedUrl((await api.tabs.get(tabId))?.url)) return { eligible: false };
      const r = await command('Runtime.evaluate', {
        expression: `(${ReviewerVerificationObservation.observePage.toString()})('driver')`,
        returnByValue: true, timeout: 1500
      });
      if (r.exceptionDetails || !r.result?.value) throw new Error('page-unavailable');
      return r.result.value;
    }
    async function up() {
      clearTimeout(watchdog);
      if (!point) return;
      if (!releasePromise) {
        input.releaseStartedAt ??= performance.now();
        releasePromise = command('Input.dispatchMouseEvent', {
          type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount: 1
        }).then(() => { input.releaseConfirmed = true; })
          .catch(error => { releasePromise = null; throw error; });
      }
      return releasePromise;
    }
    return {
      now: () => performance.now(),
      inputState: () => ({ ...input }),
      sleep: ms => new Promise(resolve => setTimeout(resolve, ms)),
      enabled: async () => !stopped && await enabled(), page,
      async feedback() {
        // Closed shadow trees may expose semantic progress through the DOM domain.
        // Unknown/inaccessible feedback falls back to the bounded hold, never guessed success.
        try {
          const { root } = await command('DOM.getDocument', { depth: 0 });
          const { nodeId } = await command('DOM.querySelector', {
            nodeId: root.nodeId, selector: '#px-captcha'
          });
          if (!nodeId) return 'unknown';
          const { node } = await command('DOM.describeNode', { nodeId, depth: 8, pierce: true });
          const hint = VerificationEngine.feedback(node);
          if (hint.kind === 'unknown') return 'unknown';
          let visibleNodeId = hint.nodeId;
          if (!visibleNodeId && hint.backendNodeId) {
            const pushed = await command('DOM.pushNodesByBackendIdsToFrontend', {
              backendNodeIds: [hint.backendNodeId]
            });
            visibleNodeId = pushed.nodeIds?.[0];
          }
          if (!visibleNodeId) return 'unknown';
          const { model } = await command('DOM.getBoxModel', { nodeId: visibleNodeId });
          const { computedStyle } = await command('CSS.getComputedStyleForNode', { nodeId: visibleNodeId });
          const styles = Object.fromEntries(computedStyle.map(s => [s.name, s.value]));
          if (!(model?.width > 0 && model.height > 0 && styles.display !== 'none' &&
              !['hidden', 'collapse'].includes(styles.visibility) && styles.opacity !== '0')) return 'unknown';
          return hint.kind === 'progress' ? { kind: 'progress', progress: hint.progress } : hint.kind;
        } catch { return 'unknown'; }
      },
      async down(targetPoint) {
        const latest = await page();
        if (!inputAllowed || !await enabled() || !latest.challenge || !latest.eligible ||
            !latest.target || Math.abs(latest.target.x - targetPoint.x) > 8 ||
            Math.abs(latest.target.y - targetPoint.y) > 8) throw new Error('input-cancelled');
        await command('Input.dispatchMouseEvent', {
          type: 'mouseMoved', x: targetPoint.x, y: targetPoint.y, button: 'none', buttons: 0
        });
        const hovered = await page();
        if (!inputAllowed || !await enabled() || !hovered.eligible || !hovered.challenge ||
            hovered.clock !== latest.clock || !hovered.target ||
            Math.abs(hovered.target.x - targetPoint.x) > 8 ||
            Math.abs(hovered.target.y - targetPoint.y) > 8) throw new Error('input-cancelled');
        point = targetPoint;
        // A separate watchdog releases even while a feedback/page command stalls.
        watchdog = setTimeout(() => { up().catch(() => undefined); }, 30000);
        input.pressStartedAt = performance.now();
        input.pressSent = true;
        await command('Input.dispatchMouseEvent', {
          type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 1
        });
        input.pressConfirmed = true;
      }, up,
      async detach() {
        clearTimeout(watchdog);
        try {
          if (owned && point) await up();
        } finally {
          try { if (owned) await bounded(api.debugger.detach(target), 5000, 'detach'); }
          finally { owned = false; unlisten(); }
        }
      }
    };
  }
  return { open, readPage, observePage: ReviewerVerificationObservation.observePage, allowedUrl };
})();
