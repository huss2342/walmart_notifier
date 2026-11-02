// Explicitly owned browsing lanes complement the unchanged homepage scan.
// The content script still owns rendering, scrolling, pagination and ingestion
// acknowledgements; this coordinator only leases targets and isolates failures.
const ReviewerAuxiliaryScans = (() => {
  const STATE_KEY = 'reviewerAuxiliaryScans';
  const TABS_KEY = 'reviewerAuxiliaryTabs';
  const ALARM = 'auxiliaryScan';
  const LANES = Object.freeze(['departments', 'keywords', 'priority']);
  const enabled = (name, settings) => name === 'departments' ? settings.auxiliaryDepartments === true
    : name === 'keywords' ? settings.auxiliaryKeywords === true
      : name === 'priority' && settings.auxiliaryPriority === true;
  const MAX_TARGETS = 2000;
  const MAX_FAILURES = 50;
  const STALE_MS = 120_000;
  const WAIT_OPPORTUNITY_MS = 300_000;
  const CONTENT_FAILURES = new Set(['document-http-error', 'render-timeout',
    'render-opportunity-timeout', 'navigation-timeout', 'navigation-scope-changed']);
  const RETRIES = [5_000, 15_000];

  function create(chrome, dependencies) {
    const { home, config, paused, post } = dependencies;
    const diagnostic = dependencies.diagnostic || (async () => {});
    let tail = Promise.resolve();
    const mutate = task => {
      const result = tail.then(task, task);
      tail = result.catch(() => undefined);
      return result;
    };
    const clone = value => value == null ? null : JSON.parse(JSON.stringify(value));
    const spacing = settings => Math.max(5, Math.min(60, Number(settings.pageDelaySeconds) || 15)) * 1000;
    const restartAt = (lane, settings) => {
      const refresh = Number(settings.refreshMinutes);
      if (!(lane.completedAt > 0)) return 0;
      // Disabling periodic refresh must not abandon unfinished coverage. The
      // ordinary target spacing remains the floor for a deferred recovery.
      if (!(refresh > 0)) return lane.targets.some(target => target.coverageStatus === 'unresolved')
        ? lane.completedAt + Math.max(spacing(settings), RETRIES.at(-1)) : 0;
      const requested = lane.lane === 'priority'
        ? Math.max(1, Math.min(120, Number(settings.auxiliaryPriorityRefreshMinutes) || 1)) : refresh;
      const interval = Math.max(60_000, requested * 60_000);
      return lane.completedAt + interval + cycleJitterMs(interval, lane.completedAt,
        lane.lane === 'priority' ? 3 : lane.lane === 'keywords' ? 2 : 1);
    };

    function cleanUrl(raw) {
      try {
        const url = new URL(raw);
        const base = new URL(home);
        if (url.origin !== base.origin || url.pathname !== base.pathname ||
            url.username || url.password || url.hash) return null;
        for (const [key, value] of [...url.searchParams]) {
          if (!value.trim() || (key === 'affinityOverride' && value === 'default')) url.searchParams.delete(key);
          else if (key === 'page') {
            if (!/^\d+$/.test(value) || Number(value) < 1) return null;
            url.searchParams.delete(key);
          }
        }
        url.searchParams.sort();
        return url.toString();
      } catch { return null; }
    }

    function selectionUrl(raw, parent) {
      const normalized = cleanUrl(raw);
      const original = cleanUrl(parent);
      if (!normalized || !original || normalized === original) return false;
      const next = new URL(normalized);
      const before = new URL(original);
      const keys = new Set([...next.searchParams.keys(), ...before.searchParams.keys()]);
      let changed = false;
      for (const key of keys) {
        const oldValues = before.searchParams.getAll(key);
        const newValues = next.searchParams.getAll(key);
        if (JSON.stringify(oldValues) === JSON.stringify(newValues)) continue;
        // These are the public reviewer facet parameters. Search terms and
        // unknown future parameters cannot silently broaden a worker's lease.
        if (!['catId', 'facet'].includes(key) || newValues.length > 1) return false;
        if (key === 'catId' && newValues.some(value => !/^\d+(?:_\d+)*$/.test(value))) return false;
        if (key === 'facet' && newValues.some(value => value.length > 1000 || /[\u0000-\u001f]/.test(value))) return false;
        changed = true;
      }
      return changed;
    }

    function affinityScopeDrift(raw, target) {
      if (!target || target.selection || !cleanUrl(raw)) return false;
      const observed = new URL(raw);
      const values = observed.searchParams.getAll('affinityOverride');
      if (values.length !== 1 || values[0] !== 'store_led') return false;
      observed.searchParams.delete('affinityOverride');
      // This proves only which bound URL to recover. The modified response
      // remains unauthorized and cannot supply any coverage receipt.
      return cleanUrl(observed.toString()) === cleanUrl(target.targetUrl);
    }

    function queries(name, settings) {
      return name === 'priority' ? priorityQueries(settings.auxiliaryPriorityQueries)
        : auxiliaryQueries(settings.auxiliaryKeywordQueries ?? undefined);
    }

    function fingerprint(name, settings) {
      return JSON.stringify(name === 'departments'
        ? ['top-level-department-destinations-v2', settings.auxiliaryBrands === true] : queries(name, settings));
    }

    function initialTargets(name, settings) {
      if (name === 'departments') return [{ key: 'departments:discovery', kind: 'discovery',
        label: 'Discover departments', targetUrl: home, selection: null, discoveryOnly: true, attempts: 0 }];
      return queries(name, settings).map(query => {
        const url = new URL(home);
        url.searchParams.set('q', query);
        url.searchParams.set('queryNups', query);
        return { key: `${name === 'priority' ? 'priority' : 'keyword'}:${query.toLocaleLowerCase('en-US')}`, kind: 'keyword', label: query,
          targetUrl: url.toString(), selection: null, attempts: 0 };
      });
    }

    function appendKeywordTargets(name, lane, settings) {
      if (name !== 'keywords' || !Array.isArray(lane.targets)) return false;
      let previous;
      try {
        previous = JSON.parse(lane.fingerprint);
        // Only fingerprints produced from an actual normalized query list
        // prove that old work is still the exact prefix of this configuration.
        if (!Array.isArray(previous) ||
            JSON.stringify(auxiliaryQueries(previous)) !== JSON.stringify(previous)) return false;
      } catch { return false; }
      const next = queries(name, settings);
      if (next.length <= previous.length || previous.some((query, index) => next[index] !== query)) return false;
      const known = new Set(lane.targets.map(target => target.key));
      for (const target of initialTargets(name, settings).slice(previous.length)) {
        if (known.has(target.key)) continue;
        known.add(target.key);
        lane.targets.push({ coverageStatus: 'pending', attemptedAt: 0, ...target });
      }
      lane.fingerprint = JSON.stringify(next);
      if (['complete', 'partial'].includes(lane.status) && lane.cursor < lane.targets.length) {
        lane.status = 'waiting';
        lane.completedAt = 0;
        lane.nextAt = Date.now() + spacing(settings);
      }
      // The current target and generation did not change. Keep its active
      // document, outstanding acknowledgements and durable receipts intact.
      return true;
    }

    async function read() {
      const stored = (await chrome.storage.local.get([STATE_KEY]))[STATE_KEY];
      const state = stored?.version === 1 && stored.lanes && typeof stored.lanes === 'object'
        ? clone(stored) : { version: 1, lanes: {} };
      // Existing v1 queues remain resumable, but their cursor did not prove a
      // terminal page. Revisit advanced targets before calling them verified;
      // old bounded failure history cannot reconstruct every skipped target.
      for (const lane of Object.values(state.lanes)) {
        let unverified = false;
        for (const [index, target] of (lane.targets || []).entries()) {
          if (!target.coverageStatus) {
            const failed = (lane.failedTargets || []).some(entry => entry.target === target.label);
            target.coverageStatus = target.coverageLimited || failed || index < lane.cursor
              ? 'unresolved' : 'pending';
            target.attemptedAt = index < lane.cursor || (index === lane.cursor && lane.status === 'scanning')
              ? Number(lane.startedAt) || 1 : 0;
            unverified ||= target.coverageStatus === 'unresolved';
          }
        }
        if (unverified && lane.status === 'complete') {
          lane.status = 'partial';
          lane.completedAt ||= Date.now();
          lane.lastError = 'Previous target advances need terminal coverage verification.';
        }
      }
      return state;
    }

    async function save(state) { await chrome.storage.local.set({ [STATE_KEY]: state }); }
    async function owned() {
      const value = (await chrome.storage.session.get([TABS_KEY]))[TABS_KEY];
      return value && typeof value === 'object' ? value : {};
    }
    async function managedTabIds() {
      return Object.values(await owned()).filter(Number.isInteger);
    }
    async function ownsTab(tabId) {
      return Number.isInteger(tabId) && (await managedTabIds()).includes(tabId);
    }

    function freshLane(name, previous, settings) {
      const initial = initialTargets(name, settings);
      const compatible = previous?.fingerprint === fingerprint(name, settings);
      const carried = compatible ? (previous.targets || [])
        .filter(target => target.coverageStatus === 'unresolved' && !target.duplicateOf)
        .map(target => ({ ...clone(target), attempts: 0, attemptedAt: 0, completedAt: 0 })) : [];
      const keys = new Set(carried.map(target => target.key));
      const targets = [...carried, ...initial.filter(target => !keys.has(target.key))]
        .map(target => ({ coverageStatus: 'pending', attemptedAt: 0, ...target }));
      return { lane: name, tabId: previous?.tabId ?? null,
        generation: Math.max(0, Number(previous?.generation) || 0) + 1,
        fingerprint: fingerprint(name, settings), targets, cursor: 0,
        status: 'ready', nextAt: 0, startedAt: Date.now(), completedAt: 0, pages: 0,
        failures: 0, failedTargets: [], truncatedTargets: 0,
        coverageLimitedTargets: carried.filter(target => target.coverageLimited).length, lastError: '',
        lastActivityAt: Date.now(), checkpoint: null, round: (Number(previous?.round) || 0) + 1 };
    }

    function currentTarget(lane) { return lane?.targets?.[lane.cursor] || null; }
    function role(lane, active = true) {
      const target = currentTarget(lane);
      return { ok: true, active: Boolean(active && target && lane.status === 'scanning'),
        tabId: lane.tabId, primaryTabId: lane.tabId, generation: lane.generation,
        auxiliary: { lane: lane.lane, mode: 'none', targetKey: target?.key || '',
          round: lane.round || 0, targetIndex: (lane.cursor || 0) + 1, attempt: (target?.attempts || 0) + 1,
          targetKind: target?.kind === 'discovery' ? 'root' : target?.kind || '',
          targetUrl: target?.targetUrl || home, selection: clone(target?.selection),
          discoveryOnly: target?.discoveryOnly === true,
          ...(target?.urlPagination === true ? { urlPagination: true } : {}) } };
    }

    async function sendRole(lane, active = true) {
      if (!Number.isInteger(lane.tabId)) return;
      try { await chrome.tabs.sendMessage(lane.tabId, { type: 'reviewer:set-role', ...role(lane, active) }); }
      catch { /* The next content document requests its own role. */ }
    }

    async function authorized(sender, generation, { selection = false, passive = false } = {}) {
      const tabId = sender?.tab?.id;
      const owner = Object.entries(await owned()).find(([, id]) => id === tabId);
      if (!owner) return null;
      const state = await read();
      const lane = state.lanes[owner[0]];
      if (!lane || lane.tabId !== tabId) return { state, lane: { lane: owner[0], tabId, generation: 0 }, allowed: false };
      const settings = await config();
      const target = currentTarget(lane);
      let tab;
      try { tab = await chrome.tabs.get(tabId); } catch { /* Closed senders have no authority. */ }
      const permitted = raw => {
        const url = cleanUrl(raw);
        if (!url || !target) return false;
        return url === cleanUrl(target.targetUrl) ||
          (selection && target.selection && (target.selection.url
            ? url === cleanUrl(target.selection.url) : selectionUrl(url, target.selection.parentUrl)));
      };
      const scoped = tab && [sender?.tab?.url, tab.url, tab.pendingUrl].filter(Boolean).every(permitted);
      const allowed = Boolean(enabled(lane.lane, settings) && !await paused() && target &&
        (passive || lane.status === 'scanning') && scoped &&
        (generation === undefined || generation === lane.generation) &&
        (selection || !target.selection));
      return { state, lane, target, tab, allowed };
    }

    async function retainCheckpoint(lane) {
      const target = currentTarget(lane);
      const belongs = checkpoint => Array.isArray(checkpoint?.visited) &&
        (!checkpoint.targetKey || checkpoint.targetKey === target?.key);
      // A queued recovery may not have repopulated the lane's working copy yet.
      // Pause/recreation must hydrate its durable target receipt, not replace it
      // with an empty working slot during the target-start pacing interval.
      if (!target?.selection && !lane.checkpoint && belongs(target?.checkpoint)) {
        lane.checkpoint = clone(target.checkpoint);
        lane.checkpointUrl = target.checkpointUrl || null;
      }
      if (!Number.isInteger(lane.tabId)) return;
      const key = `sweep:${lane.tabId}`;
      const sweep = (await chrome.storage.local.get([key]))[key];
      if (sweep?.generation === lane.generation && belongs(sweep)) {
        lane.checkpoint = clone(sweep);
      }
      try {
        const tab = await chrome.tabs.get(lane.tabId);
        if (!currentTarget(lane)?.selection && cleanUrl(tab.url) === cleanUrl(currentTarget(lane)?.targetUrl)) {
          lane.checkpointUrl = tab.url;
        }
      } catch { /* The durable checkpoint remains valid after a tab closes. */ }
      if (!target?.selection && lane.checkpoint?.visited?.length) {
        const visited = new Set(lane.checkpoint.visited.filter(page => Number.isSafeInteger(page) && page > 0));
        let next = 1;
        while (visited.has(next)) next += 1;
        const destination = new URL(target.targetUrl);
        if (next > 1) destination.searchParams.set('page', next);
        lane.checkpointUrl = destination.toString();
      }
      if (target && belongs(lane.checkpoint)) {
        target.checkpoint = clone(lane.checkpoint);
        target.checkpointUrl = lane.checkpointUrl || null;
      }
    }

    async function clearSweep(lane) {
      if (Number.isInteger(lane.tabId)) await chrome.storage.local.remove(`sweep:${lane.tabId}`);
      lane.checkpoint = null;
      lane.checkpointUrl = null;
    }

    async function advance(state, lane, settings, { failed = false, limited = false,
      duplicateOf = '', reason = '', pages = 0, emptyResult = false, documentId = 0 } = {}) {
      const target = currentTarget(lane);
      const completionContext = { lane: lane.lane, tabId: lane.tabId, generation: lane.generation,
        round: lane.round, targetIndex: lane.cursor + 1,
        attempt: (target?.attempts || 0) + (failed ? 0 : 1), documentId };
      if (failed || limited) await retainCheckpoint(lane);
      const count = Number(pages);
      const retainedPages = failed || limited ? new Set((target?.checkpoint?.visited || [])
        .filter(page => Number.isSafeInteger(page) && page > 0)).size : 0;
      const pageCount = Math.max(Number.isSafeInteger(count) && count >= 0 ? count : 0, retainedPages);
      if (target) {
        target.coverageStatus = duplicateOf ? 'duplicate' : failed || limited ? 'unresolved' : 'complete';
        target.result = !failed && !limited && !duplicateOf ? emptyResult ? 'empty' : 'items' : null;
        target.duplicateOf = duplicateOf || null;
        target.coverageLimited = limited;
        target.lastError = String(reason).slice(0, 300);
        target.pages = Math.max(Number(target.pages) || 0, pageCount);
        if (!failed && !limited) {
          target.completedAt = Date.now();
          target.checkpoint = null;
          target.checkpointUrl = null;
        }
      }
      if (failed) {
        lane.failures += 1;
        lane.lastError = String(reason).slice(0, 300);
        lane.failedTargets = [...(lane.failedTargets || []), { target: target?.label || '',
          at: Date.now(), reason: lane.lastError }].slice(-MAX_FAILURES);
      }
      // Coverage receipts represented by this round, including retained pages
      // on resumed/failed work; this is not a count of new browser requests.
      lane.pages += pageCount;
      lane.coverageLimitedTargets = lane.targets.filter(entry => !entry.duplicateOf && entry.coverageLimited).length;
      lane.cursor += 1;
      lane.generation += 1;
      lane.lastActivityAt = Date.now();
      await clearSweep(lane);
      if (lane.cursor >= lane.targets.length) {
        // Existing status contract: completedAt ends an attempt round. Only
        // status=complete proves that round has no outstanding coverage.
        lane.completedAt = Date.now();
        lane.status = lane.truncatedTargets || lane.targets.some(entry => entry.coverageStatus === 'unresolved')
          ? 'partial' : 'complete';
        lane.nextAt = restartAt(lane, settings);
      } else {
        lane.status = 'waiting';
        lane.nextAt = Date.now() + spacing(settings);
      }
      await save(state);
      await sendRole(lane, false);
      await diagnostic('auxiliary-target-complete', { ...completionContext, pages: pageCount,
        outcome: duplicateOf ? 'duplicate' : failed || limited ? 'unresolved' : emptyResult ? 'empty' : 'items' });
    }

    async function fail(state, lane, settings, reason, details = {}) {
      const target = currentTarget(lane);
      if (!target) return role(lane, false);
      target.attempts = (Number(target.attempts) || 0) + 1;
      target.coverageStatus = 'unresolved';
      lane.lastError = CONTENT_FAILURES.has(details.failureCode) ? details.failureCode :
        String(reason || 'The reviewer target did not become usable').slice(0, 300);
      lane.lastActivityAt = Date.now();
      // Capture the failing lease before advance() replaces its generation.
      const context = { lane: lane.lane, tabId: lane.tabId, generation: lane.generation,
        round: lane.round, targetIndex: lane.cursor + 1, attempt: target.attempts,
        reason: lane.lastError, ...(CONTENT_FAILURES.has(details.failureCode) ? { failureCode: details.failureCode } : {}) };
      if (Number.isSafeInteger(details.documentId) && details.documentId > 0) context.documentId = details.documentId;
      if (Number.isInteger(details.responseStatus) && details.responseStatus >= 100 && details.responseStatus <= 599) {
        context.responseStatus = details.responseStatus;
      }
      if (target.attempts > RETRIES.length) {
        await advance(state, lane, settings, { failed: true, reason: lane.lastError,
          documentId: details.documentId || 0 });
        await diagnostic('auxiliary-target-skipped', context);
        return { ...role(lane, false), recover: 'skipped' };
      }
      if (target.selection) {
        // An unbound SPA navigation may have changed the URL without ever
        // rendering its cards. Reloading that destination repeats an
        // unconfirmed selection; retry the actual control from its parent.
        await clearSweep(lane);
      } else {
        // A failed document does not invalidate acknowledged earlier pages.
        // Retain the same outstanding range through every bounded recovery;
        // after that budget it waits behind healthy targets for the next round.
        await retainCheckpoint(lane);
      }
      lane.generation += 1;
      lane.status = 'waiting';
      // A known failed document is not a rendering opportunity. Back off those
      // requests while retaining every acknowledged page and the same retry cap.
      const retryMs = details.failureCode === 'document-http-error'
        ? Math.max(RETRIES[target.attempts - 1], 30_000 * target.attempts) : RETRIES[target.attempts - 1];
      lane.nextAt = Date.now() + retryMs;
      await save(state);
      await sendRole(lane, false);
      await diagnostic('auxiliary-target-retry', { ...context, delayMs: retryMs });
      return { ...role(lane, false), ok: false, recover: 'waiting', retryAfterMs: retryMs };
    }

    async function page(message, sender) {
      const before = await authorized(sender, message.generation);
      if (!before) return null;
      if (!before.allowed || !Array.isArray(message.items) || !message.items.length) {
        return { ...role(before.lane, false), ok: false, reason: 'inactive-or-unbound-target' };
      }
      const startedAt = Date.now();
      const context = { lane: before.lane.lane, tabId: before.lane.tabId, generation: message.generation,
        documentId: message.documentId, round: before.lane.round, targetIndex: before.lane.cursor + 1,
        intakePhase: ['late', 'live'].includes(message.intakePhase) ? message.intakePhase : 'initial' };
      const result = await post(message.items, message.page,
        async () => (await authorized(sender, message.generation))?.allowed === true, context);
      const after = await authorized(sender, message.generation);
      if (result.ok && after?.allowed) {
        await mutate(async () => {
          const latest = await authorized(sender, message.generation);
          if (!latest?.allowed) return;
          latest.lane.lastActivityAt = Date.now();
          await save(latest.state);
        });
        await diagnostic('auxiliary-page-delivered', { ...context, requestId: result.requestId,
          page: message.page, items: message.items.length, durationMs: Date.now() - startedAt,
          ...Object.fromEntries(['new', 'duplicates', 'matched', 'notified', 'failed', 'pending']
            .filter(key => Number.isSafeInteger(result.summary?.[key]) && result.summary[key] >= 0)
            .map(key => [key, result.summary[key]])) });
      }
      // Diagnostics and checkpoint writes can yield to a challenge, disable or
      // target change. Return the current lease, never the one captured before
      // those awaits, so a late acknowledgement cannot revive an old document.
      const latest = await authorized(sender, message.generation);
      if (result.ok && !latest?.allowed) await diagnostic('ingest-stage', { ...context,
        requestId: result.requestId, page: message.page, items: message.items.length,
        stage: 'complete', outcome: 'accepted-stale', durationMs: Date.now() - startedAt });
      return { ...result, ...role(latest?.lane || before.lane, latest?.allowed === true),
        ok: Boolean(result.ok && latest?.allowed), reason: latest?.allowed ? result.reason : 'stale-generation' };
    }

    async function handle(message, sender) {
      if (!await ownsTab(sender?.tab?.id)) return null;
      if (message.type === 'reviewer:page') return page(message, sender);
      return mutate(async () => {
        const selecting = ['reviewer:role', 'reviewer:aux-selected', 'reviewer:unusable'].includes(message.type);
        const auth = await authorized(sender, message.generation, { selection: selecting });
        if (!auth) return null;
        const { state, lane, target } = auth;
        if (message.type === 'reviewer:role') return role(lane, auth.allowed);
        if (!auth.allowed) return { ...role(lane, false), ok: false, reason: 'inactive-or-stale-generation' };
        const settings = await config();
        if (await paused()) return { ...role(lane, false), ok: false, reason: 'verification' };
        lane.lastActivityAt = Date.now();
        if (message.type === 'reviewer:checkpoint') {
          await retainCheckpoint(lane);
          await save(state);
          return role(lane);
        }
        if (message.type === 'reviewer:aux-discovered') {
          if (lane.lane !== 'departments' || !Array.isArray(message.candidates)) return { ...role(lane), ok: false };
          const known = new Set(lane.targets.map(entry => entry.key));
          let added = 0;
          let truncated = 0;
          for (const candidate of message.candidates.slice(0, MAX_TARGETS + 1)) {
            if (!['department', 'brand'].includes(candidate?.kind) ||
                (candidate.kind === 'brand' && settings.auxiliaryBrands !== true) ||
                typeof candidate.label !== 'string') continue;
            const rawLabel = candidate.label;
            const label = rawLabel.trim().replace(/\s+/g, ' ');
            const parentUrl = cleanUrl(candidate.parentUrl);
            const departmentUrl = candidate.kind === 'department'
              ? ReviewerDepartmentData.destination(candidate.url, parentUrl) : null;
            if (!label || rawLabel.length > 300 || label.length > 150 ||
                parentUrl !== cleanUrl(target.targetUrl) ||
                (candidate.kind === 'department' &&
                  (target.kind !== 'discovery' || parentUrl !== cleanUrl(home) || !departmentUrl))) continue;
            const key = `${candidate.kind}:${parentUrl}:${label.toLocaleLowerCase('en-US')}`;
            if (known.has(key)) continue;
            known.add(key);
            if (lane.targets.length >= MAX_TARGETS) { truncated += 1; continue; }
            lane.targets.push({ key, kind: candidate.kind, label, targetUrl: parentUrl,
              coverageStatus: 'pending', attemptedAt: 0,
              selection: { kind: candidate.kind, label: rawLabel, parentUrl,
                ...(departmentUrl ? { url: departmentUrl } : {}) }, attempts: 0 });
            added += 1;
          }
          truncated += Math.max(0, message.candidates.length - (MAX_TARGETS + 1));
          lane.truncatedTargets += truncated;
          if (truncated) lane.lastError = `Discovery exceeded ${MAX_TARGETS} targets; this round is partial.`;
          await save(state);
          return { ...role(lane), added, truncated };
        }
        if (message.type === 'reviewer:aux-selected') {
          if (!target.selection || !selectionUrl(message.url, target.selection.parentUrl) ||
              (target.selection.url && cleanUrl(message.url) !== cleanUrl(target.selection.url)) ||
              cleanUrl(message.url) !== cleanUrl(auth.tab.url)) return { ...role(lane), ok: false };
          const url = cleanUrl(message.url);
          const duplicate = lane.targets.find(entry => entry !== target && !entry.duplicateOf && entry.boundUrl === url);
          if (duplicate) {
            await advance(state, lane, settings, { duplicateOf: duplicate.key });
            return { ...role(lane, false), duplicate: true };
          }
          target.targetUrl = url;
          target.boundUrl = url;
          target.selection = null;
          await clearSweep(lane);
          await save(state);
          return role(lane);
        }
        if (message.type === 'reviewer:unusable') return fail(state, lane, settings, message.reason, message);
        if (message.type === 'reviewer:sweep-complete') {
          if (message.emptyResult === true) {
            const receipt = (await chrome.storage.local.get([`sweep:${lane.tabId}`]))[`sweep:${lane.tabId}`];
            if (target.discoveryOnly || target.selection || message.pages !== 0 || message.coverageLimited === true ||
                receipt?.generation !== lane.generation || receipt?.targetKey !== target.key ||
                receipt?.emptyResult !== true || !Array.isArray(receipt.visited) || receipt.visited.length ||
                receipt.total !== 0 || receipt.terminalPage !== 0 || receipt.terminalReason !== 'confirmed-empty' ||
                receipt.lastObservedPage !== 1 || new URL(auth.tab.url).searchParams.get('page') > 1) {
              return { ...role(lane), ok: false, reason: 'unverified-empty-result' };
            }
            const latest = await authorized(sender, message.generation);
            if (!latest?.allowed) return { ...role(lane, false), ok: false, reason: 'stale-empty-result' };
          }
          const limited = message.coverageLimited === true;
          const reason = limited ? 'Terminal page coverage could not be verified; unfinished pages remain queued.' : '';
          if (limited) lane.lastError = reason;
          await advance(state, lane, settings, { pages: message.pages, limited, reason,
            emptyResult: message.emptyResult === true, documentId: message.documentId || 0 });
          return role(lane, false);
        }
        return { ...role(lane), ok: false, reason: 'unsupported-auxiliary-message' };
      });
    }

    async function reserveTab(state, lane) {
      if (await paused() || !enabled(lane.lane, await config())) return null;
      const ownership = await owned();
      let tab;
      if (ownership[lane.lane] === lane.tabId) {
        try { tab = await chrome.tabs.get(lane.tabId); } catch { /* Recreate only our missing tab. */ }
      }
      if (!tab) {
        // Creating a blank first guarantees no reviewer content can request a
        // primary lease before its explicit auxiliary ownership is durable.
        if (await paused()) return;
        // Content commits receipts before its checkpoint message. A browser or
        // worker loss in that gap must not delete the last acknowledged pages.
        await retainCheckpoint(lane);
        const waitingUrl = chrome.runtime?.getURL?.('scanner-wait.html') || 'about:blank';
        tab = await chrome.tabs.create({ url: waitingUrl, active: false });
        const oldTabId = lane.tabId;
        lane.tabId = tab.id;
        lane.generation += 1;
        if (lane.status === 'scanning') lane.status = 'waiting';
        ownership[lane.lane] = tab.id;
        await chrome.storage.session.set({ [TABS_KEY]: ownership });
        if (Number.isInteger(oldTabId) && oldTabId !== tab.id) await chrome.storage.local.remove(`sweep:${oldTabId}`);
        await save(state);
      }
      return tab;
    }

    async function startTarget(state, lane) {
      if (await paused()) return;
      const settings = await config();
      const lastStart = Number(state.lastTargetStartedAt);
      const earliest = Number.isFinite(lastStart) && lastStart > 0 && lastStart <= Date.now()
        ? lastStart + spacing(settings) : 0;
      if (earliest > Date.now()) {
        lane.status = 'waiting';
        lane.nextAt = earliest;
        await save(state);
        return;
      }
      if (!await reserveTab(state, lane)) return;
      const target = currentTarget(lane);
      if (!target) return;
      lane.status = 'scanning';
      lane.nextAt = 0;
      lane.lastActivityAt = Date.now();
      target.attemptedAt ||= Date.now();
      if (!lane.checkpoint && target.checkpoint) {
        lane.checkpoint = clone(target.checkpoint);
        lane.checkpointUrl = target.checkpointUrl || null;
      }
      if (lane.checkpoint) {
        lane.checkpoint.generation = lane.generation;
        await chrome.storage.local.set({ [`sweep:${lane.tabId}`]: lane.checkpoint });
      }
      await save(state);
      const destination = lane.checkpointUrl && cleanUrl(lane.checkpointUrl) === cleanUrl(target.targetUrl)
        ? lane.checkpointUrl : target.targetUrl;
      const currentSettings = await config();
      const verificationPause = await paused();
      if (verificationPause || !enabled(lane.lane, currentSettings)) {
        lane.status = verificationPause ? 'paused' : 'disabled';
        lane.generation += 1;
        await save(state);
        await sendRole(lane, false);
        return;
      }
      // Reserve before navigation and persist across worker restarts. One
      // recovered/completed lane cannot start alongside another in the same tick.
      state.lastTargetStartedAt = Date.now();
      state.lastTargetStartedLane = lane.lane;
      await save(state);
      await chrome.tabs.update(lane.tabId, { url: destination, active: false });
      await diagnostic('auxiliary-target-start', { lane: lane.lane, tabId: lane.tabId,
        generation: lane.generation, round: lane.round, targetIndex: lane.cursor + 1,
        attempt: (target.attempts || 0) + 1 });
    }

    async function tickLane(name, settings) {
      const state = await read();
      let lane = state.lanes[name];
      if (!enabled(name, settings)) {
        if (lane && lane.status !== 'disabled') {
          await retainCheckpoint(lane);
          lane.status = 'disabled';
          lane.generation += 1;
          await save(state);
          await sendRole(lane, false);
        }
        return;
      }
      const expectedFingerprint = fingerprint(name, settings);
      if (lane && lane.fingerprint !== expectedFingerprint && appendKeywordTargets(name, lane, settings)) {
        await save(state);
      }
      if (!lane || lane.fingerprint !== expectedFingerprint) {
        const previous = lane;
        lane = state.lanes[name] = freshLane(name, previous, settings);
        if (!previous) {
          const suppliedDelay = Number(dependencies.initialStartDelay?.(settings, name));
          const fallbackDelay = name === 'priority'
            ? spacing(settings) * (1 + Number(enabled('departments', settings)) + Number(enabled('keywords', settings)))
            : name === 'keywords' && settings.auxiliaryDepartments === true ? spacing(settings) : 0;
          const initialDelay = Math.max(0, Number.isFinite(suppliedDelay) ? suppliedDelay : 0,
            fallbackDelay);
          if (initialDelay > 0) {
            lane.status = 'waiting';
            lane.nextAt = Date.now() + initialDelay;
          }
        }
        if (previous) await clearSweep(lane);
        await save(state);
        await sendRole(lane, false);
      }
      if (lane.status === 'disabled' && lane.cursor >= lane.targets.length) {
        lane = state.lanes[name] = freshLane(name, lane, settings);
        await save(state);
      }
      if (await paused()) {
        if (!['paused', 'complete', 'partial'].includes(lane.status)) {
          await retainCheckpoint(lane);
          lane.status = 'paused';
          lane.generation += 1;
          await save(state);
          await sendRole(lane, false);
        }
        return;
      }
      // Reserve every configured slot together; only reviewer loads are
      // staggered. Finished lanes keep their future next-round clock intact.
      if (lane.targets.length && !await reserveTab(state, lane)) return;
      if (['complete', 'partial'].includes(lane.status)) {
        const nextAt = restartAt(lane, settings);
        if (lane.nextAt !== nextAt) {
          lane.nextAt = nextAt;
          await save(state);
        }
        if (!nextAt || nextAt > Date.now()) return;
        lane = state.lanes[name] = freshLane(name, lane, settings);
        await save(state);
      }
      if (!currentTarget(lane)) {
        lane.status = 'partial';
        lane.lastError = 'No valid keyword targets are configured.';
        lane.nextAt = 0;
        await save(state);
        return;
      }
      if (lane.nextAt > Date.now()) return;
      const ownership = await owned();
      const belongs = ownership[name] === lane.tabId;
      if (lane.status === 'scanning' && belongs) {
        let tab;
        try { tab = await chrome.tabs.get(lane.tabId); } catch { /* Missing managed tab is recreated below. */ }
        if (tab) {
          if (cleanUrl(tab.url) === null && tab.url !== 'about:blank' && tab.status !== 'loading') {
            await fail(state, lane, settings, 'Managed tab left the reviewer route');
            return;
          }
          let probe;
          try { probe = await dependencies.probe?.(lane.tabId); } catch { /* A lost content script gets bounded recovery. */ }
          if (probe?.botCheck) {
            // The shared pause queues this manager's revocation. Do not await
            // it while holding the manager mutation lock.
            Promise.resolve(dependencies.onBotCheck?.(lane.tabId)).catch(() => undefined);
            return;
          }
          if (probe?.ready === true && probe.generation === lane.generation &&
              probe.active === false && lane.checkpoint?.visited?.length &&
              affinityScopeDrift(tab.url, currentTarget(lane))) {
            if (probe.busy === true) return;
            // Chrome can expose the rejected old document while the bound
            // recovery load is pending. Do not spend another retry on it.
            // Only the coordinator's start time bounds this loading grace;
            // repeated probes cannot keep a permanently stuck load alive.
            const pendingScope = cleanUrl(tab.pendingUrl);
            if (tab.status === 'loading' && pendingScope &&
                pendingScope === cleanUrl(currentTarget(lane).targetUrl) &&
                Date.now() - (Number(lane.lastActivityAt) || 0) < STALE_MS) return;
            // A native pager can silently add store_led. Keep that scope
            // rejected, then resume the outstanding bound page with the normal
            // paced URL path for this target; do not repeat that broken pager.
            currentTarget(lane).urlPagination = true;
            await fail(state, lane, settings, 'navigation-scope-changed', {
              failureCode: 'navigation-scope-changed', documentId: probe.documentId });
            return;
          }
          let matches = probe?.ready === true && probe.active === true &&
            (probe.generation === undefined || probe.generation === lane.generation);
          // A live assigned document explicitly waiting for Walmart traffic is
          // reachable, but cannot supply results yet. A missing/obsolete probe
          // still falls through to the ordinary finite recovery below.
          if (matches && probe.generation === lane.generation && probe.highTraffic === true) {
            const auth = await authorized({ tab }, lane.generation, { selection: true });
            if (auth?.allowed) return;
            await sendRole(lane, false);
            matches = false;
          }
          // busy is the content script's trusted-input grace, not a claim of
          // useful progress. Suspend the wait bound during that real guard.
          if (matches && (probe.busy === true ||
              ['manual-tab-active', 'user-active'].includes(probe.focusReason))) {
            lane.waitResumedAt = Date.now();
            await save(state);
            return;
          }
          const waiting = matches && ['waiting-focus', 'waiting-user'].includes(probe.phase);
          if (waiting) {
            // Only coordinator-observed work (intake/checkpoints/target start)
            // renews this bound. Content heartbeat timestamps cannot renew it.
            const progressAt = Math.max(Number(lane.lastActivityAt) || 0, Number(lane.waitResumedAt) || 0);
            if (Date.now() - progressAt < WAIT_OPPORTUNITY_MS) return;
            await fail(state, lane, settings, 'Managed reviewer page stopped progressing', probe);
            return;
          }
          const activeAt = Math.max(Number(lane.lastActivityAt) || 0,
            matches ? Number(probe?.lastActivityAt) || 0 : 0);
          if (Date.now() - activeAt < STALE_MS) return;
          if (matches && (probe.nextActionAt > Date.now() ||
              ['ingest-retry', 'waiting-user'].includes(probe.phase))) return;
          await fail(state, lane, settings, 'Managed reviewer page stopped responding', probe);
          return;
        }
      }
      if (['paused', 'disabled'].includes(lane.status)) lane.generation += 1;
      await startTarget(state, lane);
    }

    async function ensureAlarm() {
      const settings = await config();
      if (LANES.some(name => enabled(name, settings))) {
        if (!await chrome.alarms.get(ALARM)) await chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
      } else await chrome.alarms.clear(ALARM);
    }

    async function tick() {
      await ensureAlarm();
      // A fast queue must not repeatedly win the global load-spacing slot.
      // Persist the last reservation so restarts retain the same fair order;
      // lanes that are not due simply yield without preempting existing work.
      const previous = LANES.indexOf((await read()).lastTargetStartedLane);
      const order = [...LANES.slice(previous + 1), ...LANES.slice(0, previous + 1)];
      for (const name of order) {
        await mutate(async () => {
          const settings = await config();
          try { await tickLane(name, settings); }
          catch (error) {
            const state = await read();
            const lane = state.lanes[name] ||= { lane: name, tabId: null, generation: 0,
              targets: [], cursor: 0, pages: 0, failures: 0, failedTargets: [], truncatedTargets: 0 };
            if (enabled(name, settings)) {
              await retainCheckpoint(lane);
              lane.status = 'waiting';
              lane.nextAt = Date.now() + 30_000;
              lane.lastError = `Could not load this target: ${String(error?.message || error).slice(0, 250)}`;
              lane.generation = (Number(lane.generation) || 0) + 1;
              await save(state);
              await sendRole(lane, false);
            }
            await diagnostic('auxiliary-error', { lane: name, reason: String(error?.message || error).slice(0, 300) });
          }
        });
      }
    }

    async function pause() {
      return mutate(async () => {
        const state = await read();
        for (const lane of Object.values(state.lanes)) {
          if (['disabled', 'paused', 'complete', 'partial'].includes(lane.status)) continue;
          await retainCheckpoint(lane);
          lane.generation += 1;
          lane.status = 'paused';
          lane.nextAt = 0;
        }
        await save(state);
        await Promise.allSettled(Object.values(state.lanes).map(lane => sendRole(lane, false)));
      });
    }

    async function removed(tabId) {
      return mutate(async () => {
        const ownership = await owned();
        const name = Object.keys(ownership).find(key => ownership[key] === tabId);
        if (!name) return false;
        delete ownership[name];
        await chrome.storage.session.set({ [TABS_KEY]: ownership });
        const state = await read();
        const lane = state.lanes[name];
        if (lane?.tabId === tabId) {
          await retainCheckpoint(lane);
          lane.tabId = null;
          lane.generation += 1;
          if (!['disabled', 'paused', 'complete', 'partial'].includes(lane.status)) lane.status = 'waiting';
          lane.nextAt = Date.now() + 30_000;
          await save(state);
        }
        await chrome.storage.local.remove(`sweep:${tabId}`);
        return true;
      });
    }

    async function status() {
      const state = await read();
      return { ok: true, lanes: LANES.filter(name => state.lanes[name]).map(name => {
        const lane = state.lanes[name];
        const targets = lane.targets.filter(target => target.kind !== 'discovery' && !target.duplicateOf);
        return { lane: name, tabId: lane.tabId, generation: lane.generation,
          targetLabel: currentTarget(lane)?.label || '', status: lane.status,
          completedTargets: targets.filter(target => target.coverageStatus === 'complete').length,
          emptyTargets: targets.filter(target => target.coverageStatus === 'complete' && target.result === 'empty').length,
          attemptedTargets: targets.filter(target => target.attemptedAt > 0).length,
          unresolvedTargets: targets.filter(target => target.coverageStatus === 'unresolved').length,
          totalTargets: targets.length, pages: lane.pages,
          failures: lane.failures, failedTargets: clone(lane.failedTargets), nextAt: lane.nextAt,
          truncatedTargets: lane.truncatedTargets, coverageLimitedTargets: lane.coverageLimitedTargets || 0,
          lastError: lane.lastError,
          startedAt: lane.startedAt, completedAt: lane.completedAt, round: lane.round };
      }) };
    }

    async function restart(name = null) {
      const result = await mutate(async () => {
        const state = await read();
        const settings = await config();
        // Recheck under the same lock as checkpoint deletion. A pause can
        // arrive while a caller waits for this mutation or its storage reads.
        if (await paused()) return { ok: false, paused: true };
        for (const laneName of LANES) {
          if ((name && name !== laneName) || !enabled(laneName, settings)) continue;
          const previous = state.lanes[laneName];
          if (previous) {
            await retainCheckpoint(previous);
            const target = currentTarget(previous);
            if (target && target.coverageStatus !== 'complete' && target.attemptedAt > 0) {
              target.coverageStatus = 'unresolved';
            }
          }
          const lane = state.lanes[laneName] = freshLane(laneName, previous, settings);
          await clearSweep(lane);
          await sendRole(lane, false);
        }
        await save(state);
        return { ok: true };
      });
      if (result.paused) return result;
      await tick();
      return status();
    }

    return { handle, tick, pause, removed, status, restart, ensureAlarm, ownsTab, managedTabIds,
      alarmName: ALARM };
  }

  return { create, lanes: LANES, enabled };
})();
