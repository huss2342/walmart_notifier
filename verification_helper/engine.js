// Pure state machine; browser input and clocks are supplied by the Chrome adapter.
var VerificationEngine = (() => {
  const POLL_MS = 250;
  const HOLD_MS = 15000;
  const SETTLE_MS = 20000;
  const HOUR_MS = 3600000;
  const keyOK = key => typeof key === 'string' && /^[a-zA-Z0-9:._-]{1,200}$/.test(key);
  function reserve(value, key, now) {
    if (!keyOK(key) || !Number.isFinite(now) || now < 0) return { allowed: false };
    const state = value == null ? { attempts: [], seen: [] } : value;
    if (!state || Object.keys(state).sort().join() !== 'attempts,seen' ||
        !Array.isArray(state.attempts) || state.attempts.length > 3 ||
        !Array.isArray(state.seen) || state.seen.length > 128 || !state.seen.every(keyOK) ||
        !state.attempts.every(a => a && Object.keys(a).sort().join() === 'at,key' &&
          keyOK(a.key) && Number.isFinite(a.at) && a.at >= 0 && a.at <= now)) {
      return { allowed: false, reason: 'invalid-budget' };
    }
    if (state.seen.includes(key)) return { allowed: false, reason: 'already-attempted' };
    const attempts = state.attempts.filter(a => now - a.at < HOUR_MS);
    if (attempts.length >= 3 || attempts.some(a => now - a.at < 60000)) {
      return { allowed: false, reason: 'cooldown' };
    }
    return { allowed: true, state: { attempts: [...attempts, { key, at: now }],
      seen: [...state.seen, key].slice(-128) } };
  }

  // Read only the widget subtree, never scripts/styles or surrounding account data.
  // Feedback is a release hint, never evidence that the server accepted a solve.
  function feedback(root) {
    let remaining = 250;
    let release = null;
    let failed = null;
    function visit(node, parentRef) {
      if (!node || --remaining < 0 || /^(SCRIPT|STYLE|NOSCRIPT)$/i.test(node.nodeName)) return;
      const attrs = {};
      for (let i = 0; i < (node.attributes?.length || 0); i += 2) {
        attrs[node.attributes[i]] = node.attributes[i + 1];
      }
      if (attrs['aria-hidden'] === 'true' || Object.hasOwn(attrs, 'hidden') ||
          /display\s*:\s*none|visibility\s*:\s*hidden/i.test(attrs.style || '')) return;
      const ref = node.nodeName === '#text' ? parentRef :
        { nodeId: node.nodeId || null, backendNodeId: node.backendNodeId || null };
      const label = [node.nodeName === '#text' ? node.nodeValue : '', attrs['aria-label'],
        attrs['aria-valuetext']].filter(Boolean).join(' ').slice(0, 512);
      if (/try again|too soon|verification failed|please retry/i.test(label)) failed = { kind: 'failed', ...ref };
      else if (/\brelease\b|\blet go\b|\bverified\b|\bcompleted\b|\bsuccess\b/i.test(label)) {
        release ||= { kind: 'release', ...ref };
      }
      const number = attrs['aria-valuenow'];
      const max = Number(attrs['aria-valuemax'] ?? 100);
      const min = Number(attrs['aria-valuemin'] ?? 0);
      if (attrs.role === 'progressbar' && typeof number === 'string' && number.trim() &&
          Number.isFinite(Number(number)) && Number.isFinite(max) && max > min && Number(number) >= max) {
        release ||= { kind: 'release', ...ref };
      }
      for (const children of [node.children || [], node.shadowRoots || []]) {
        for (const child of children) {
          if (remaining <= 0) break;
          visit(child, ref);
        }
      }
    }
    visit(root, null);
    return failed || release || { kind: 'unknown' };
  }

  async function run(driver) {
    const result = { outcome: 'unsupported', holdMs: 0, releaseReason: 'none' };
    let downAttempted = false;
    let released = false;
    let started;
    try {
      const before = await driver.page();
      if (!await driver.enabled() || !before.eligible || !before.challenge ||
          !Number.isFinite(before.target?.x) || !Number.isFinite(before.target?.y)) return result;
      const baseline = await driver.feedback();
      let releaseChanged = baseline !== 'release';
      // Recheck after feedback inspection, before any input.
      const fresh = await driver.page();
      if (!await driver.enabled() || !sameTarget(before, fresh)) return result;
      downAttempted = true;
      started = driver.now();
      await driver.down(before.target);
      result.outcome = 'needs-attention';
      result.releaseReason = 'deadline';
      while (driver.now() - started < HOLD_MS) {
        await driver.sleep(Math.min(POLL_MS, HOLD_MS - (driver.now() - started)));
        const page = await driver.page();
        if (!await driver.enabled() || !sameTarget(before, page)) {
          result.releaseReason = !page.challenge && page.eligible ? 'page-changed' : 'cancelled';
          if (result.releaseReason === 'cancelled') result.outcome = 'cancelled';
          break;
        }
        const signal = await driver.feedback();
        if (signal !== 'release') releaseChanged = true;
        if (signal === 'failed' || (signal === 'release' && releaseChanged)) {
          result.releaseReason = signal === 'failed' ? 'failed' : 'feedback';
          break;
        }
      }
      result.holdMs = driver.now() - started;
      await driver.up();
      released = true;
      if (['cancelled', 'failed'].includes(result.releaseReason)) return result;
      const settling = driver.now();
      let readySince = null;
      while (driver.now() - settling <= SETTLE_MS) {
        if (!await driver.enabled()) { result.outcome = 'cancelled'; break; }
        const page = await driver.page();
        if (!page.eligible) { result.outcome = 'cancelled'; break; }
        if (page.ready && !page.challenge) {
          readySince ??= driver.now();
          if (driver.now() - readySince >= 1000) { result.outcome = 'verified'; break; }
        } else readySince = null;
        await driver.sleep(POLL_MS);
      }
    } catch {
      result.outcome = 'input-error';
      if (started != null) result.holdMs = Math.max(0, driver.now() - started);
    } finally {
      if (downAttempted && !released) {
        try { await driver.up(); } catch { result.outcome = 'input-error'; }
      }
      try { await driver.detach(); } catch { result.outcome = 'input-error'; }
    }
    return result;
  }
  function sameTarget(before, next) {
    return next.eligible && next.challenge && next.clock === before.clock &&
      Number.isFinite(next.target?.x) && Number.isFinite(next.target?.y) &&
      Math.abs(next.target.x - before.target.x) <= 8 && Math.abs(next.target.y - before.target.y) <= 8;
  }
  return { run, reserve, feedback };
})();
