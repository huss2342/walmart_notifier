// Chrome debugger transport: https://developer.chrome.com/docs/extensions/reference/api/debugger
// Only fixed read-only DOM expressions and tab-local Input commands are sent.
var VerificationChrome = (() => {
  function allowedUrl(raw) {
    try {
      const u = new URL(raw);
      return u.protocol === 'https:' && u.hostname === 'www.walmart.com' &&
        !u.username && !u.password && !u.port &&
        (/^\/reviews\/claim-product\/?$/.test(u.pathname) || /^\/blocked\/?$/.test(u.pathname));
    } catch { return false; }
  }

  // Self-contained read-only page function. No account text or URL is returned.
  function readPage(doc = document, current = location) {
    const eligible = current.protocol === 'https:' && current.hostname === 'www.walmart.com' &&
      !current.port && (/^\/reviews\/claim-product\/?$/.test(current.pathname) ||
        /^\/blocked\/?$/.test(current.pathname));
    if (!eligible) return { eligible: false };
    function visible(el) {
      if (!el?.getClientRects().length) return false;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return false;
      for (let p = el, depth = 0; p && depth < 20; p = p.parentElement, depth++) {
        const style = getComputedStyle(p);
        if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) ||
            style.opacity === '0') return false;
      }
      return true;
    }
    const markers = [...doc.querySelectorAll('#px-captcha, #px-captcha-wrapper')].filter(visible);
    const heading = [...doc.querySelectorAll('[role="dialog"] h1,[role="dialog"] h2')]
      .some(e => visible(e) && /robot\s+or\s+human/i.test(e.textContent || ''));
    const frame = [...doc.querySelectorAll(
      'iframe[src*="captcha.px-cloud.net"],iframe[src*="px-captcha"],iframe[title*="human challenge" i]'
    )].some(visible);
    const blocked = /^\/blocked\/?$/.test(current.pathname);
    const challenge = blocked || heading || frame || markers.length > 0;
    const targets = new Map();
    for (const marker of markers) {
      const r = marker.getBoundingClientRect();
      if (r.width < 200 || r.width > 700 || r.height < 50 || r.height > 250) continue;
      const x = Math.round(r.x + r.width * 0.6);
      const y = Math.round(r.y + r.height * 0.5);
      if (x < 0 || y < 0 || x >= doc.documentElement.clientWidth ||
          y >= doc.documentElement.clientHeight) continue;
      const hit = doc.elementFromPoint(x, y);
      if (hit && (hit === marker || marker.contains(hit))) targets.set(`${x}:${y}`, { x, y });
    }
    const products = [...doc.querySelectorAll('main a[href*="/ip/"]')].some(visible);
    const empty = visible(doc.getElementById('search-item-stack-no-results-header'));
    return { eligible, challenge, clock: performance.timeOrigin,
      ready: !blocked && !challenge && (products || empty),
      target: targets.size === 1 ? [...targets.values()][0] : null };
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
    function bounded(promise, ms = 2000) {
      let timer;
      return Promise.race([promise,
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), ms); })
      ]).finally(() => clearTimeout(timer));
    }
    const command = (method, params = {}) => {
      if (!owned) return Promise.reject(new Error('detached'));
      return bounded(api.debugger.sendCommand(target, method, params));
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
        expression: `(${readPage.toString()})()`, returnByValue: true, timeout: 1500
      });
      if (r.exceptionDetails || !r.result?.value) throw new Error('page-unavailable');
      return r.result.value;
    }
    async function up() {
      clearTimeout(watchdog);
      if (!point) return;
      if (!releasePromise) releasePromise = command('Input.dispatchMouseEvent', {
        type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount: 1
      }).catch(error => { releasePromise = null; throw error; });
      return releasePromise;
    }
    return {
      now: () => performance.now(),
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
          return model?.width > 0 && model.height > 0 && styles.display !== 'none' &&
            !['hidden', 'collapse'].includes(styles.visibility) && styles.opacity !== '0'
            ? hint.kind : 'unknown';
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
        watchdog = setTimeout(() => { up().catch(() => undefined); }, 15000);
        await command('Input.dispatchMouseEvent', {
          type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 1
        });
      }, up,
      async detach() {
        clearTimeout(watchdog);
        try {
          if (owned && point) await up();
        } finally {
          try { if (owned) await bounded(api.debugger.detach(target)); }
          finally { owned = false; unlisten(); }
        }
      }
    };
  }
  return { open, readPage, allowedUrl };
})();
