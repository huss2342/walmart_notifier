// Pure state machine; browser input and clocks are supplied by the Chrome adapter.
var VerificationEngine = (() => {
  const POLL_MS = 250;
  const HOLD_MS = 15000;
  const MAX_HOLD_MS = 30000;
  const PROGRESS_STALL_MS = 4000;
  const SETTLE_MS = 20000;
  const HOUR_MS = 3600000;
  const keyOK = key => typeof key === 'string' && /^[a-zA-Z0-9:._-]{1,200}$/.test(key);
  function reserve(value, key, now, { maxAttempts = 3, minSpacingMs = 60000 } = {}) {
    if (!Number.isInteger(maxAttempts) || maxAttempts < 1 || maxAttempts > 10 ||
        !Number.isFinite(minSpacingMs) || minSpacingMs < 10000) return { allowed: false, reason: 'invalid-budget' };
    if (!keyOK(key) || !Number.isFinite(now) || now < 0) return { allowed: false };
    const state = value == null ? { attempts: [], seen: [] } : value;
    if (!state || Object.keys(state).sort().join() !== 'attempts,seen' ||
        !Array.isArray(state.attempts) || state.attempts.length > maxAttempts ||
        !Array.isArray(state.seen) || state.seen.length > 128 || !state.seen.every(keyOK) ||
        !state.attempts.every(a => a && Object.keys(a).sort().join() === 'at,key' &&
          keyOK(a.key) && Number.isFinite(a.at) && a.at >= 0 && a.at <= now)) {
      return { allowed: false, reason: 'invalid-budget' };
    }
    if (state.seen.includes(key)) return { allowed: false, reason: 'already-attempted' };
    const attempts = state.attempts.filter(a => now - a.at < HOUR_MS);
    if (attempts.length >= maxAttempts || attempts.some(a => now - a.at < minSpacingMs)) {
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
    let progress = null;
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
          Number.isFinite(Number(number)) && Number.isFinite(min) && Number.isFinite(max) && max > min) {
        if (Number(number) >= max) release ||= { kind: 'release', ...ref };
        else if (Number(number) >= min) {
          progress ||= { kind: 'progress', progress: (Number(number) - min) / (max - min), ...ref };
        }
      }
      for (const children of [node.children || [], node.shadowRoots || []]) {
        for (const child of children) {
          if (remaining <= 0) break;
          visit(child, ref);
        }
      }
    }
    visit(root, null);
    return failed || release || progress || { kind: 'unknown' };
  }

  async function run(driver, { fallbackHoldMs = HOLD_MS } = {}) {
    const fallbackMs = Number.isFinite(fallbackHoldMs)
      ? Math.min(MAX_HOLD_MS, Math.max(HOLD_MS, fallbackHoldMs)) : HOLD_MS;
    const result = { outcome: 'unsupported', holdMs: 0, releaseReason: 'none', progressObserved: false,
      cleanupConfirmed: false };
    let downAttempted = false;
    let released = false;
    let started;
    let stage = 'inspect';
    const recordError = (error, fallback, cleanup = false) => {
      const known = ['inspect', 'move', 'press', 'release', 'detach'];
      const failedAt = known.includes(error?.verificationStage) ? error.verificationStage : fallback;
      result.outcome = 'input-error';
      result.errorStage ||= failedAt;
      result.errorReason ||= ['timeout', 'detached'].includes(error?.verificationReason)
        ? error.verificationReason : 'command-failed';
      if (cleanup) result.cleanupErrorStage = failedAt;
    };
    try {
      const before = await driver.page();
      if (!await driver.enabled() || !before.eligible || !before.challenge ||
          !Number.isFinite(before.target?.x) || !Number.isFinite(before.target?.y)) return result;
      const baseline = await driver.feedback();
      let releaseChanged = baseline !== 'release';
      let highestProgress = baseline?.kind === 'progress' && Number.isFinite(baseline.progress)
        ? baseline.progress : 0;
      let lastProgressAt = null;
      // Recheck after feedback inspection, before any input.
      const fresh = await driver.page();
      if (!await driver.enabled() || !sameTarget(before, fresh)) return result;
      downAttempted = true;
      stage = 'press';
      started = driver.now();
      await driver.down(before.target);
      // A slow acknowledgement is already part of the requested hold; do not
      // start another full interval after Chrome acknowledges the press.
      started = driver.inputState?.().pressStartedAt ?? started;
      stage = 'inspect';
      result.outcome = 'needs-attention';
      result.releaseReason = 'deadline';
      while (driver.now() - started < MAX_HOLD_MS) {
        await driver.sleep(Math.min(POLL_MS, MAX_HOLD_MS - (driver.now() - started)));
        const page = await driver.page();
        if (!await driver.enabled() || !sameTarget(before, page)) {
          result.releaseReason = !page.challenge && page.eligible ? 'page-changed' : 'cancelled';
          if (result.releaseReason === 'cancelled') result.outcome = 'cancelled';
          break;
        }
        const hint = await driver.feedback();
        const signal = hint?.kind || hint;
        if (signal === 'progress' && Number.isFinite(hint.progress) && hint.progress <= 1 &&
            hint.progress > highestProgress) {
          highestProgress = hint.progress;
          lastProgressAt = driver.now();
          result.progressObserved = true;
        }
        if (signal !== 'release') releaseChanged = true;
        if (signal === 'failed' || (signal === 'release' && releaseChanged)) {
          result.releaseReason = signal === 'failed' ? 'failed' : 'feedback';
          break;
        }
        if (lastProgressAt !== null && driver.now() - lastProgressAt >= PROGRESS_STALL_MS) {
          result.releaseReason = 'stalled'; break;
        }
        if (lastProgressAt === null && driver.now() - started >= fallbackMs) break;
        if (driver.now() - started >= MAX_HOLD_MS) result.releaseReason = 'max-duration';
      }
      result.holdMs = driver.now() - started;
      stage = 'release';
      await driver.up();
      released = true;
      stage = 'inspect';
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
    } catch (error) {
      recordError(error, stage);
      if (started != null) result.holdMs = Math.max(0, driver.now() - started);
    } finally {
      if (downAttempted && !released) {
        try { await driver.up(); } catch (error) { recordError(error, 'release', true); }
      }
      try {
        await driver.detach();
        result.cleanupConfirmed = !result.cleanupErrorStage;
      } catch (error) { recordError(error, 'detach', true); }
      const input = driver.inputState?.();
      if (input) {
        result.pressSent = input.pressSent === true;
        result.pressConfirmed = input.pressConfirmed === true;
        result.releaseConfirmed = input.releaseConfirmed === true;
        result.holdMs = input.pressSent && Number.isFinite(input.pressStartedAt)
          ? Math.max(0, (input.releaseStartedAt ?? driver.now()) - input.pressStartedAt) : 0;
        if (input.transport === 'native') {
          result.transport = 'native';
          if (Number.isFinite(input.nativeHoldMs)) result.holdMs = input.nativeHoldMs;
          if (input.nativeReason) result.nativeReason = input.nativeReason;
        }
      }
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
