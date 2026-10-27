// Integrated controller. The collector supplies its existing verification pause;
// no second extension, extra detector, or page reload is needed to start a check.
var ReviewerVerificationAutomation = (() => {
  function create(api, { readCheck, onRecovered = async () => {} }) {
    const local = api.storage.local;
    let inFlight = null;
    let recheckRequested = false;
    let retryTimer = null;
    let manualRetrying = false;
    function retryable(result) {
      if (result?.outcome === 'needs-attention') {
        return ['deadline', 'failed', 'stalled', 'max-duration'].includes(result.releaseReason) &&
          Number.isFinite(result.holdMs) && result.holdMs > 0;
      }
      // A timed-out pointer move cannot leave a held button when no press was
      // sent and cleanup completed. Keep ambiguous/legacy input errors terminal.
      return result?.outcome === 'input-error' && result.errorStage === 'move' &&
        result.errorReason === 'timeout' && result.pressSent === false &&
        result.cleanupConfirmed === true && !result.cleanupErrorStage;
    }
    async function cancelScheduledRetry() {
      clearTimeout(retryTimer);
      retryTimer = null;
      await api.alarms?.clear('verificationRetry');
    }
    async function scheduleRetry(nextAt, code = 'retry-pending') {
      clearTimeout(retryTimer);
      retryTimer = setTimeout(() => {
        retryTimer = null;
        return runPending({ recheck: true }).catch(() => undefined);
      }, Math.max(0, nextAt - Date.now()));
      retryTimer?.unref?.();
      // The short timer supplies normal pacing; the alarm/startup/heartbeat
      // recover the persisted deadline if Chrome suspends the worker.
      await api.alarms?.create('verificationRetry', { when: nextAt });
      await status(code);
    }
    async function enabled() {
      return (await local.get(['verificationAssistEnabled'])).verificationAssistEnabled === true;
    }
    async function chromeWaitReason(tab) {
      if (!VerificationChrome.allowedUrl(tab?.url) || tab.status !== 'complete' ||
          (tab.pendingUrl && tab.pendingUrl !== tab.url)) return 'waiting-tab-ready';
      if (tab.frozen === true || tab.discarded === true) return 'waiting-tab-ready';
      if (tab.active === false) return 'waiting-foreground';
      // Older adapters may omit window/activation fields. Defer on known
      // unavailable state without requiring unsupported APIs or taking focus.
      if (Number.isInteger(tab.windowId) && typeof api.windows?.get === 'function') {
        try {
          if ((await api.windows.get(tab.windowId))?.focused === false) return 'waiting-foreground';
        } catch { return 'waiting-tab-ready'; }
      }
      return null;
    }
    // A background tab can acknowledge input late. Take the foreground
    // once instead of waiting for the user; defer on any failed step.
    async function ensureForeground(tab) {
      if (!tab || !Number.isInteger(tab.id)) return false;
      try {
        if (tab.active === false) await api.tabs.update(tab.id, { active: true });
        if (Number.isInteger(tab.windowId) && typeof api.windows?.get === 'function' &&
            (await api.windows.get(tab.windowId))?.focused === false)
          await api.windows.update(tab.windowId, { focused: true });
        return (await chromeWaitReason(await api.tabs.get(tab.id))) === null;
      } catch { return false; }
    }
    async function status(code) {
      const previous = (await local.get(['verificationAssistStatus'])).verificationAssistStatus;
      if (previous?.code !== code) await local.set({ verificationAssistStatus: { code, at: Date.now() } });
    }
    async function run() {
      let driver;
      let reservedInput = false;
      try {
        await cancelScheduledRetry();
        if (!await enabled()) return;
        const check = await readCheck();
        if (!(check.pausedUntil > 0) || !Number.isFinite(check.lastAt) || check.lastAt <= 0) {
          await status('watching'); return;
        }
        const permission = api.permissions?.contains
          ? await api.permissions.contains({ permissions: ['debugger'] }) : Boolean(api.debugger?.attach);
        if (!permission) { await status('permission-required'); return; }
        const retry = (await local.get(['verificationAssistRetryAt'])).verificationAssistRetryAt;
        if (Number.isFinite(retry) && retry > Date.now()) {
          await scheduleRetry(retry, 'retrying-control'); return;
        }
        const ids = [...new Set(check.challengedTabIds || [check.tabId])].filter(Number.isInteger);
        let skipped = 'waiting-page';
        let deferred = null;
        for (const id of ids) {
          const tab = await api.tabs.get(id).catch(() => null);
          if (!VerificationChrome.allowedUrl(tab?.url) || tab.status !== 'complete' ||
              (tab.pendingUrl && tab.pendingUrl !== tab.url)) continue;
          const current = await readCheck();
          if (!await enabled() || current.lastAt !== check.lastAt || !(current.pausedUntil > 0)) return;
          const stored = await local.get(['verificationAssistBudget', 'verificationAssistRetry',
            'verificationAssistLastResult', 'verificationNativeEnabled']);
          const key = `${id}:${check.lastAt}`;
          let previous = stored.verificationAssistRetry;
          if (previous?.key !== key) {
            previous = null;
            // Upgrade a completed old one-attempt failure, preserving the
            // original reservation. Interrupted/ambiguous attempts stay spent.
            const old = stored.verificationAssistBudget?.attempts?.find(a => a.key === key);
            const latest = stored.verificationAssistBudget?.attempts?.reduce(
              (best, item) => !best || item.at > best.at ? item : best, null);
            const last = stored.verificationAssistLastResult;
            if (old && latest?.key === key && !last?.key && retryable(last) &&
                Number.isFinite(last.at) && last.at >= old.at) {
              previous = { key, attempt: 1, nextAt: last.at + 15000, terminal: false };
              await local.set({ verificationAssistRetry: previous });
            }
          }
          if (previous) {
            if (!Number.isInteger(previous.attempt) || previous.attempt < 1 || previous.attempt > 10 ||
                !Number.isFinite(previous.nextAt) || previous.nextAt < 0 || typeof previous.terminal !== 'boolean') {
              await status('invalid-budget'); return;
            }
            if (previous.attempt >= 10) { skipped = 'attempts-exhausted'; continue; }
            if (previous.terminal) {
              const outcome = stored.verificationAssistLastResult?.outcome;
              if (outcome === 'verified') { skipped = 'already-attempted'; continue; }
              await status(outcome === 'starting' ? 'needs-attention' : outcome || 'needs-attention'); return;
            }
            if (previous.nextAt > Date.now()) {
              await scheduleRetry(previous.nextAt); return;
            }
          }
          const last = stored.verificationAssistLastResult;
          const native = stored.verificationNativeEnabled === true && previous?.key === key &&
            last?.key === key && last.attempt === previous.attempt &&
            typeof VerificationNative !== 'undefined' && VerificationNative.safeFallback(last);
          if (!native) {
            const waiting = await chromeWaitReason(tab);
            const foreground = waiting === 'waiting-foreground' && await ensureForeground(tab);
            if (waiting && !foreground) { deferred ||= waiting; continue; }
          }
          const attempt = (previous?.attempt || 0) + 1;
          const reservation = VerificationEngine.reserve(stored.verificationAssistBudget,
            attempt === 1 ? key : `${key}:${attempt}`, Date.now(),
            { maxAttempts: 10, minSpacingMs: 10000 });
          if (!reservation.allowed) {
            skipped = reservation.reason || 'unavailable';
            if (skipped === 'already-attempted') continue;
            if (skipped === 'cooldown') {
              const attempts = stored.verificationAssistBudget.attempts.filter(a => Date.now() - a.at < 3600000);
              const nextAt = Math.max(...attempts.map(a => a.at + 10000),
                attempts.length >= 10 ? Math.min(...attempts.map(a => a.at)) + 3600000 : 0);
              await scheduleRetry(nextAt, 'cooldown');
            } else await status(skipped);
            return;
          }
          // Attaching sends no mouse input. A busy/unavailable debugger may be
          // retried later without consuming an input attempt.
          if (native && !await api.permissions.contains(VerificationNative.PERMISSIONS)) {
            await status('native-permission-required'); return;
          }
          await status(native ? 'native-connecting' : 'connecting');
          const stillEnabled = async () => {
            const latest = await readCheck();
            return await enabled() && latest.lastAt === check.lastAt && latest.pausedUntil > 0;
          };
          driver = await (native ? VerificationNative : VerificationChrome).open(api, id, stillEnabled);
          if (!native) {
            // Attachment can yield to a tab switch or a frozen renderer. No
            // input has been sent, so close our session and keep its budget.
            const fresh = await api.tabs.get(id).catch(() => null);
            const waiting = await chromeWaitReason(fresh);
            const foreground = waiting === 'waiting-foreground' && await ensureForeground(fresh);
            if (waiting && !foreground) {
              await driver.detach();
              driver = null;
              deferred ||= waiting;
              continue;
            }
          }
          // Persist before any input; a failed write still detaches without a hold.
          await local.set({ verificationAssistBudget: reservation.state,
            verificationAssistRetryAt: 0,
            verificationAssistRetry: { key, attempt, nextAt: 0, terminal: true },
            verificationAssistLastResult: { at: Date.now(), key, attempt, outcome: 'starting' } });
          reservedInput = true;
          await status('starting');
          const result = await VerificationEngine.run(driver, {
            fallbackHoldMs: Math.min(30000, 15000 + (attempt - 1) * 3000)
          });
          driver = null; // The engine always owns cleanup after it starts.
          const nextAt = retryable(result) && attempt < 10
            ? Date.now() + 10000 + Math.floor(Math.random() * 10001) : 0;
          await local.set({ verificationAssistLastResult: { ...result, at: Date.now(), key, attempt },
            verificationAssistRetry: { key, attempt, nextAt, terminal: !nextAt } });
          if (nextAt) await scheduleRetry(nextAt);
          else await status(retryable(result) && attempt >= 10 ? 'attempts-exhausted' : result.outcome);
          if (result.outcome === 'verified') {
            try {
              if (await onRecovered(id, check.lastAt) === false) await status('collector-pending');
            } catch { await status('collector-pending'); }
          }
          return;
        }
        if (deferred) await scheduleRetry(Date.now() + 60000, deferred);
        else await status(skipped);
      } catch (error) {
        if (driver) { try { await driver.detach(); } catch { /* Best-effort release is in the adapter. */ } }
        if (!reservedInput) {
          const nextAt = Date.now() + 60000;
          await local.set({ verificationAssistRetryAt: nextAt }).catch(() => undefined);
          const code = error?.nativeReason === 'user-busy' ? 'native-waiting-idle' :
            error?.nativeReason ? 'native-unavailable' : 'unavailable';
          await scheduleRetry(nextAt, code).catch(() => undefined);
        } else await status('unavailable').catch(() => undefined);
      }
    }
    async function retryCurrentCheck() {
      if (inFlight || manualRetrying) return { ok: false, reason: 'busy' };
      manualRetrying = true;
      try {
        if (!await enabled()) return { ok: false, reason: 'disabled' };
        const check = await readCheck();
        const stored = await local.get(['verificationAssistRetry', 'verificationAssistLastResult']);
        const previous = stored.verificationAssistRetry;
        const last = stored.verificationAssistLastResult;
        const ids = check.challengedTabIds || [check.tabId];
        if (!(check.pausedUntil > 0) || !Number.isFinite(check.lastAt) ||
            !previous?.terminal || !Number.isInteger(previous.attempt) ||
            previous.attempt < 1 || previous.attempt >= 10 || last?.outcome !== 'input-error' ||
            last.key !== previous.key || last.attempt !== previous.attempt ||
            !ids.some(id => Number.isInteger(id) && previous.key === `${id}:${check.lastAt}`)) {
          return { ok: false, reason: 'not-retryable' };
        }
        const current = await readCheck();
        if (!await enabled() || current.lastAt !== check.lastAt || !(current.pausedUntil > 0)) {
          return { ok: false, reason: 'changed' };
        }
        // Only an explicit Options click can rearm an uncertain result. Keep
        // every original reservation and let run() enforce the hourly cap.
        await local.set({ verificationAssistRetry: { ...previous, nextAt: Date.now(), terminal: false } });
        return { ok: true };
      } finally {
        manualRetrying = false;
        if (recheckRequested) runPending({ recheck: true }).catch(() => undefined);
      }
    }
    function runPending({ recheck = false } = {}) {
      if (manualRetrying) { recheckRequested = true; return Promise.resolve(); }
      if (inFlight) {
        if (recheck) recheckRequested = true;
        return inFlight;
      }
      inFlight = (async () => {
        try {
          do {
            recheckRequested = false;
            await run();
          } while (recheckRequested);
        } finally { inFlight = null; }
      })();
      return inFlight;
    }
    return { runPending, retryCurrentCheck };
  }
  return { create };
})();
