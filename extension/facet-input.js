// Browser input for ordinary department labels only. Coordinates and scripts
// are never accepted from a caller; a fixed DOM reader finds the current label.
var ReviewerFacetInput = (() => {
  const BLOCKED = 'reviewerFacetInputUnreleased';
  function scope(raw) {
    try {
      const u = new URL(raw);
      return u.protocol === 'https:' && u.hostname === 'www.walmart.com' &&
        !u.username && !u.password && !u.port && !u.hash &&
        /^\/reviews\/claim-product\/?$/.test(u.pathname) &&
        !['q', 'queryNups'].some(key => u.searchParams.getAll(key).some(v => v.trim()));
    } catch { return false; }
  }

  function inspect(wanted, parent) {
    const url = new URL(location.href);
    if (url.protocol !== 'https:' || url.hostname !== 'www.walmart.com' || url.port ||
        !/^\/reviews\/claim-product\/?$/.test(url.pathname) || document.visibilityState !== 'visible' ||
        ['q', 'queryNups'].some(key => url.searchParams.getAll(key).some(v => v.trim()))) return { ok: false };
    function normalized(raw) {
      const u = new URL(raw);
      for (const [key, value] of [...u.searchParams]) {
        if (!value.trim() || key === 'page' || (key === 'affinityOverride' && value === 'default')) u.searchParams.delete(key);
      }
      u.searchParams.sort(); return u.toString();
    }
    if (normalized(url) !== normalized(parent)) return { ok: false };
    function visible(el) {
      if (!el?.isConnected || !el.getClientRects().length) return false;
      for (let p = el; p; p = p.parentElement) {
        const s = getComputedStyle(p);
        if (p.hidden || p.getAttribute('aria-hidden') === 'true' || s.display === 'none' ||
            ['hidden', 'collapse'].includes(s.visibility)) return false;
      }
      return true;
    }
    if (/robot or human\?|press\s*(?:&|and)\s*hold/i.test(document.body?.innerText || '') ||
        [...document.querySelectorAll('#px-captcha,#px-captcha-wrapper,iframe[src*="captcha"],iframe[title*="human challenge" i]')].some(visible)) return { ok: false };
    const roots = [...document.querySelectorAll('[aria-label="Sort and Filter section"]')].filter(visible);
    if (roots.length !== 1) return { ok: false };
    const radios = [...roots[0].querySelectorAll('input[type="radio"]')]
      .filter(input => input.value === wanted && /^Departments?$/i.test(input.name) && !input.checked && !input.disabled);
    if (radios.length !== 1) return { ok: false };
    const radio = radios[0], label = radio.closest('label');
    if (!label || label.control !== radio || !visible(label) ||
        !label.closest('[data-dca-ctx="filterPopover"]') || !roots[0].contains(label) ||
        label.querySelectorAll('input').length !== 1) return { ok: false };
    const r = label.getBoundingClientRect();
    const x = r.x + r.width / 2, y = r.y + r.height / 2;
    if (!(r.width > 0 && r.height > 0 && x >= 0 && y >= 0 && x < innerWidth && y < innerHeight)) return { ok: false };
    const hit = document.elementFromPoint(x, y);
    return hit && label.contains(hit) ? { ok: true, x, y, clock: performance.timeOrigin } : { ok: false };
  }

  function create(api, { authorize }) {
    let inFlight = false;
    async function click(tabId, generation, selection) {
      const reject = () => ({ ok: false, reason: 'facet-error' });
      if (inFlight || !Number.isInteger(tabId) || !Number.isInteger(generation) ||
          selection?.kind !== 'department' || typeof selection.label !== 'string' ||
          !selection.label.trim() || selection.label.length > 300 || !scope(selection.parentUrl)) return reject();
      inFlight = true;
      const target = { tabId };
      let owned = false, abandoned = false, attaching = false, pressed = false, pressConfirmed = false, released = false, point;
      let watchdog, releasePromise, reserved = false;
      let cancelled = false;
      const bounded = (promise, ms = 1800) => {
        let timer;
        return Promise.race([promise, new Promise((_, fail) => {
          timer = setTimeout(() => fail(new Error('facet-command-timeout')), ms);
        })]).finally(() => clearTimeout(timer));
      };
      const guard = async () => { if (!await bounded(authorize(tabId, generation, selection))) throw new Error('cancelled'); };
      const command = (method, params) => bounded(api.debugger.sendCommand(target, method, params));
      const read = async () => {
        const result = await command('Runtime.evaluate', { expression: `(${inspect.toString()})(${JSON.stringify(selection.label)},${JSON.stringify(selection.parentUrl)})`,
          returnByValue: true, timeout: 1000 });
        const value = result.result?.value;
        if (result.exceptionDetails || value?.ok !== true || !Number.isFinite(value.x) ||
            !Number.isFinite(value.y) || !Number.isFinite(value.clock)) throw new Error('target-unavailable');
        return value;
      };
      const release = () => {
        if (!pressed) return Promise.resolve();
        if (!releasePromise) releasePromise = command('Input.dispatchMouseEvent', {
          type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount: 1
        }).then(async () => {
          released = true;
          if (pressConfirmed) await bounded(api.storage.local.set({ [BLOCKED]: false }));
        });
        return releasePromise;
      };
      let ok = false;
      try {
        if ((await bounded(api.storage.local.get([BLOCKED])))[BLOCKED]) return reject();
        await guard();
        attaching = true;
        await bounded(Promise.resolve().then(() => api.debugger.attach(target, '1.3')).then(async () => {
          if (abandoned) { await bounded(api.debugger.detach(target)); return; }
          owned = true;
        }).finally(() => { attaching = false; if (abandoned) inFlight = false; }));
        await guard();
        point = await read();
        await guard();
        await command('Input.dispatchMouseEvent', { type: 'mouseMoved', x: point.x, y: point.y, button: 'none', buttons: 0 });
        const fresh = await read();
        if (fresh.clock !== point.clock || Math.abs(fresh.x - point.x) > 3 ||
            Math.abs(fresh.y - point.y) > 3) throw new Error('target-changed');
        await guard();
        await bounded(api.storage.local.set({ [BLOCKED]: true }));
        reserved = true;
        await guard();
        pressed = true;
        watchdog = setTimeout(() => { release().catch(() => {}); }, 1000);
        await command('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y,
          button: 'left', buttons: 1, clickCount: 1 });
        pressConfirmed = true;
        await release();
        // A watchdog may have released while the press response was pending.
        if (released) await bounded(api.storage.local.set({ [BLOCKED]: false }));
        ok = true;
      } catch (error) {
        cancelled = error?.message === 'cancelled';
        // Ordinary filter failure cannot stop other lanes.
      }
      finally {
        abandoned = true;
        clearTimeout(watchdog);
        try { await release(); } catch { /* An unconfirmed release keeps the persistent input block. */ }
        if (reserved && !pressed) {
          try { await bounded(api.storage.local.set({ [BLOCKED]: false })); } catch { /* No input sent. */ }
        }
        if (owned) { try { await bounded(api.debugger.detach(target)); } catch { ok = false; } }
        inFlight = attaching;
      }
      return ok && released ? { ok: true } : cancelled ? { ok: false, reason: 'cancelled' } : reject();
    }
    return { click };
  }
  return { create, inspect, scope };
})();
