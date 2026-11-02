// The token may only accompany literal loopback requests. The deadline covers
// headers AND bounded JSON body consumption, including a server that stalls.
var ReviewerLocalClient = (() => {
  function loopback(raw) {
    const url = new URL(raw);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) ||
        url.username || url.password || url.hash) {
      throw new Error('The notifier endpoint must use http://127.0.0.1 or http://localhost without credentials or a fragment.');
    }
    return url;
  }
  function endpoint(raw) {
    const url = loopback(raw);
    if (url.pathname.replace(/\/+$/, '') !== '/ingest' || url.search) {
      throw new Error('The endpoint must end exactly in /ingest, with no credentials or query.');
    }
    return url.toString();
  }
  function route(raw, path) {
    const base = new URL(endpoint(raw));
    const url = new URL(path, base.origin);
    if (url.origin !== base.origin) throw new Error('Notifier routes must stay on the configured loopback origin.');
    return loopback(url).toString();
  }
  async function bodyText(response, maxBytes) {
    if (!response.body?.getReader) {
      // Small in-memory response adapters can expose json() only.
      const text = response.text ? await response.text() : JSON.stringify(await response.json());
      if (text.length > maxBytes) throw new Error('Notifier response too large.');
      return text;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let size = 0;
    let text = '';
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) return text + decoder.decode();
        size += value.byteLength;
        if (size > maxBytes) throw new Error('Notifier response too large.');
        text += decoder.decode(value, { stream: true });
      }
    } finally { reader.cancel().catch(() => undefined); }
  }
  async function json(raw, init = {}, { timeoutMs = 15_000, maxBytes = 64 * 1024 } = {}) {
    const url = loopback(raw).toString();
    const controller = new AbortController();
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => {
        const error = new Error('Notifier request timed out.');
        error.name = 'AbortError';
        controller.abort();
        reject(error);
      }, timeoutMs);
    });
    try {
      return await Promise.race([timeout, (async () => {
        const response = await fetch(url, { ...init, redirect: 'error', signal: controller.signal });
        const text = await bodyText(response, maxBytes);
        let body;
        try { body = JSON.parse(text); }
        catch {
          const error = new Error(`Notifier returned invalid JSON (HTTP ${response.status}).`);
          error.status = response.status;
          throw error;
        }
        return { ok: response.ok, status: response.status, body };
      })()]);
    } finally { clearTimeout(timer); }
  }

  // Web Locks coordinate every same-origin extension page and service worker.
  // https://www.w3.org/TR/web-locks/ — no unsafe per-page fallback for mutations.
  function settingsTransaction(task) {
    if (typeof globalThis.navigator?.locks?.request !== 'function') {
      return Promise.reject(new Error('Cross-page settings coordination is unavailable. Reload the extension and try again.'));
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30_000);
    return Promise.resolve().then(() => navigator.locks.request('reviewer-notifier-settings',
      { mode: 'exclusive', signal: controller.signal },
      () => { clearTimeout(timer); return task(); })).finally(() => clearTimeout(timer));
  }
  return Object.freeze({ endpoint, route, json, settingsTransaction });
})();
