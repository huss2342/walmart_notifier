// Explicitly owned browsing lanes complement the unchanged homepage scan.
// The content script still owns rendering, scrolling, pagination and ingestion
// acknowledgements; this coordinator only leases targets and isolates failures.
const ReviewerAuxiliaryScans = (() => {
  const STATE_KEY = 'reviewerAuxiliaryScans';
  const TABS_KEY = 'reviewerAuxiliaryTabs';
  const ALARM = 'auxiliaryScan';
  // One descriptor per lane; add a lane here. Object order drives fair
  // rotation and start delays. keyPrefix and salt are persisted/observable
  // (target keys, refresh jitter), so never change them for an existing lane.
  // Query lanes supply queries/normalize; append is the rule that lets a
  // longer list extend a saved queue: 'prefix' (same order) or 'superset'.
  const LANE_SPECS = Object.freeze({
    departments: Object.freeze({ salt: 1, enabled: settings => settings.auxiliaryDepartments === true }),
    keywords: Object.freeze({ salt: 2, keyPrefix: 'keyword', append: 'prefix',
      enabled: settings => settings.auxiliaryKeywords === true,
      queries: settings => auxiliaryQueries(settings.auxiliaryKeywordQueries ?? undefined),
      normalize: value => auxiliaryQueries(value) }),
    priority: Object.freeze({ salt: 3, keyPrefix: 'priority', append: 'prefix',
      enabled: settings => settings.auxiliaryPriority === true,
      refreshMinutes: settings => Math.max(1, Math.min(120, Number(settings.auxiliaryPriorityRefreshMinutes) || 1)),
      queries: settings => priorityQueries(settings.auxiliaryPriorityQueries),
      normalize: value => priorityQueries(value) }),
    requests: Object.freeze({ salt: 4, keyPrefix: 'request', append: 'superset', refreshMinutes: () => 1,
      enabled: settings => memberQueries(settings).length > 0,
      queries: settings => memberQueries(settings),
      normalize: value => requestQueries(value) })
  });
  const LANES = Object.freeze(Object.keys(LANE_SPECS));
  const enabled = (name, settings) => LANE_SPECS[name]?.enabled(settings) === true;

  function requestQueries(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    return value.filter(query => {
      if (typeof query !== 'string' || !query.trim() || /[\p{C}]/u.test(query) || /:\/\//.test(query)) return false;
      const key = query.trim().toLocaleLowerCase('en-US');
      if (seen.has(key)) return false;
      seen.add(key); return true;
    }).map(query => query.trim());
  }
  // Searches the priority tab already runs are skipped here: its results carry
  // the same query, so the backend still matches them to member requests.
  function memberQueries(settings) {
    const queries = requestQueries(settings.requestSearchQueries);
    if (settings.auxiliaryPriority !== true) return queries;
    let priority;
    try { priority = priorityQueries(settings.auxiliaryPriorityQueries); } catch { return queries; }
    const key = query => query.toLocaleLowerCase('en-US').replace(/\s+/g, ' ');
    const covered = new Set(priority.map(key));
    return queries.filter(query => !covered.has(key(query)));
  }
  const MAX_TARGETS = 2000;
  const MAX_FAILURES = 50;
  const STALE_MS = 120_000;
  const WAIT_OPPORTUNITY_MS = 300_000;
  const CONTENT_FAILURES = new Set(['document-http-error', 'render-timeout',
    'render-opportunity-timeout', 'navigation-timeout', 'navigation-scope-changed', 'interior-empty', 'card-unreadable']);
  const RETRIES = [5_000, 15_000];

  function create(chrome, dependencies) {
    const { home, config, paused, post } = dependencies;
    const diagnosticSink = dependencies.diagnostic || (async () => {});
    const targetIdentity = async targetKey => {
      try {
        return typeof diagnosticTargetId === 'function' ? await diagnosticTargetId(targetKey) : null;
      } catch { return null; }
    };
    const diagnostic = async (event, details = {}) => {
      const { targetKey, ...safe } = details;
      const targetId = await targetIdentity(targetKey);
      return diagnosticSink(event, { ...safe, ...(targetId ? { targetId } : {}) });
    };
    const recoveryDiagnostic = (lane, reason, outcome, probe, decision = 'none') => {
      if (typeof recordScannerRecovery !== 'function') return;
      try {
        Promise.resolve(recordScannerRecovery(lane.lane, reason, outcome, {
          probe, progressAt: lane.lastActivityAt || 0, enabled: true, paused: false, decision,
          tabId: lane.tabId, generation: lane.generation, round: lane.round,
          targetIndex: lane.cursor + 1, targetKey: currentTarget(lane)?.key,
          documentId: probe?.documentId || 0
        })).catch(() => {});
      } catch { /* Logging never controls recovery. */ }
    };
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
      const spec = LANE_SPECS[lane.lane];
      const requested = spec?.refreshMinutes ? spec.refreshMinutes(settings) : refresh;
      const interval = Math.max(60_000, requested * 60_000);
      return lane.completedAt + interval + cycleJitterMs(interval, lane.completedAt, spec?.salt || 1);
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
      return (LANE_SPECS[name]?.queries || LANE_SPECS.keywords.queries)(settings);
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
        return { key: `${LANE_SPECS[name]?.keyPrefix || 'keyword'}:${query.toLocaleLowerCase('en-US')}`, kind: 'keyword', label: query,
          targetUrl: url.toString(), selection: null, attempts: 0 };
      });
    }

    function appendQueryTargets(name, lane, settings) {
      const spec = LANE_SPECS[name];
      if (!spec?.append || !Array.isArray(lane.targets)) return false;
      let previous;
      try {
        previous = JSON.parse(lane.fingerprint);
        // Only fingerprints produced from an actual normalized query list
        // prove that old work is still the exact prefix of this configuration.
        if (!Array.isArray(previous) ||
            JSON.stringify(spec.normalize(previous)) !== JSON.stringify(previous)) return false;
      } catch { return false; }
      const next = queries(name, settings);
      if (next.length <= previous.length || (spec.append === 'superset'
        ? previous.some(query => !next.includes(query))
        : previous.some((query, index) => next[index] !== query))) return false;
      const known = new Set(lane.targets.map(target => target.key));
      for (const target of initialTargets(name, settings)) {
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
        // chrome.storage already returns a fresh copy; no second deep clone.
        ? stored : { version: 1, lanes: {} };
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
        .map(target => ({ ...clone(target), attempts: 0, attemptedAt: 0, completedAt: 0, deliveredPages: [] })) : [];
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

    async function revalidateInteriorBoundary(lane, details) {
      const target = currentTarget(lane);
      if (details.revalidateBoundary !== true || details.restartSweep !== true ||
          target?.selection || target?.discoveryOnly || !target) return false;
      let tab;
      let probe;
      let page;
      try {
        tab = await chrome.tabs.get(lane.tabId);
        page = Number(new URL(tab.url).searchParams.get('page') || 1);
        probe = await dependencies.probe?.(lane.tabId);
      } catch { recoveryDiagnostic(lane, 'interior-empty', 'deferred', probe, 'receiver-unavailable'); return false; }
      if (probe?.ready !== true || probe.active !== true || probe.generation !== lane.generation ||
          probe.busy === true || probe.botCheck === true || probe.highTraffic === true) {
        const decision = probe?.busy ? 'user-input' : probe?.botCheck ? 'verification'
          : probe?.highTraffic ? 'traffic' : 'receiver-unavailable';
        recoveryDiagnostic(lane, 'interior-empty', 'deferred', probe, decision);
        return false;
      }
      const receipt = (await chrome.storage.local.get([`sweep:${lane.tabId}`]))[`sweep:${lane.tabId}`];
      if (!Number.isSafeInteger(page) || page <= 1 || receipt?.generation !== lane.generation ||
          receipt?.targetKey !== target.key || !Array.isArray(receipt.visited) || receipt.visited.includes(page) ||
          !Number.isSafeInteger(receipt.total) || receipt.total <= page ||
          !Number.isSafeInteger(receipt.endRetries?.[page]) || receipt.endRetries[page] < 3) {
        recoveryDiagnostic(lane, 'interior-empty', 'deferred', probe, receipt ? 'boundary-unproven' : 'receipt-missing');
        return false;
      }
      // The page, receiver and saved proof can yield to a scope/pause change.
      // Validate again before replacing the working receipt with a fresh search.
      const auth = await authorized({ tab }, lane.generation);
      if (!auth?.allowed || auth.target?.key !== target.key) {
        recoveryDiagnostic(lane, 'interior-empty', 'deferred', probe, 'scope-changed');
        return false;
      }
      const checkpoint = clone(receipt);
      delete checkpoint.boundaryRevalidation;
      target.boundaryRevalidation = { at: Date.now(), checkpoint, checkpointUrl: tab.url };
      target.checkpoint = null;
      target.checkpointUrl = null;
      await clearSweep(lane);
      recoveryDiagnostic(lane, 'interior-empty', 'accepted', probe);
      return true;
    }

    async function advance(state, lane, settings, { failed = false, limited = false,
      duplicateOf = '', reason = '', pages = 0, emptyResult = false, documentId = 0 } = {}) {
      const target = currentTarget(lane);
      const completionContext = { lane: lane.lane, tabId: lane.tabId, generation: lane.generation,
        round: lane.round, targetIndex: lane.cursor + 1, targetKey: target?.key,
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

    // A blank page or an unbindable department usually means a target with
    // nothing left in it. One direct read of its first page settles that instead
    // of several full reloads; no answer keeps the ordinary retries.
    function emptyProbeUrl(target) {
      try {
        if (target?.kind === 'keyword') {
          const url = new URL(target.targetUrl);
          if (!url.searchParams.get('q')) return null;
          url.searchParams.delete('page');
          url.searchParams.set('sort', 'new');
          return url.toString();
        }
        if (target?.kind !== 'department') return null;
        const catId = new URL(target.selection?.url || target.targetUrl).searchParams.get('catId');
        if (!/^\d{1,16}(?:_\d{1,16})*$/.test(catId || '')) return null;
        const url = new URL(home);
        url.searchParams.set('catId', catId);
        return url.toString();
      } catch { return null; }
    }

    // Runs before the coordinator lock, so other lanes never wait on the read.
    // Only this coordinator supplies the result; a content message cannot.
    async function probeUnusable(message, sender) {
      if (typeof dependencies.fastProbe !== 'function') return null;
      const bindFailure = /could not authorize selected filter|selected filter could not be authorized/i
        .test(String(message.reason || ''));
      if (message.failureCode !== 'render-timeout' && !bindFailure) return null;
      try {
        const auth = await authorized(sender, message.generation, { selection: true });
        const url = auth?.allowed ? emptyProbeUrl(auth.target) : null;
        if (!url) return null;
        const reply = await dependencies.fastProbe(url);
        return reply?.ok === true && Number.isSafeInteger(reply.products) && reply.products >= 0
          ? { key: auth.target.key, products: reply.products,
            totalPages: Number.isSafeInteger(reply.totalPages) && reply.totalPages > 0 ? reply.totalPages : 0 }
          : null;
      } catch { return null; }
    }

    // A resumed page that no longer exists renders blank on every retry. Archive
    // the stale progress, as boundary revalidation does, and read the target again
    // from its first page, which re-learns the real page count.
    async function restartFromFirstPage(lane, target) {
      const checkpoint = clone(target.checkpoint || lane.checkpoint);
      if (checkpoint) delete checkpoint.boundaryRevalidation;
      target.boundaryRevalidation = { at: Date.now(), checkpoint,
        checkpointUrl: target.checkpointUrl || lane.checkpointUrl || null };
      target.checkpoint = null;
      target.checkpointUrl = null;
      await clearSweep(lane);
    }

    function resumePage(lane, target) {
      const visited = new Set(((lane.checkpoint || target.checkpoint)?.visited || [])
        .filter(page => Number.isSafeInteger(page) && page > 0));
      let next = 1;
      while (visited.has(next)) next += 1;
      return next;
    }

    async function fail(state, lane, settings, reason, details = {}) {
      const target = currentTarget(lane);
      if (!target) return role(lane, false);
      const probe = details.emptyProbe?.key === target.key ? details.emptyProbe : null;
      if (probe?.products === 0) {
        // The server's first page holds no products at all: an empty target,
        // settled with one direct read instead of repeated full reloads.
        await advance(state, lane, settings, { emptyResult: true, documentId: details.documentId || 0 });
        return { ...role(lane, false), recover: 'empty' };
      }
      // Every page the server reports for this target was read and delivered
      // this round, so the blank page after them is past its end, not a failure.
      // (Some department pagers never show an end marker the page can prove.)
      const delivered = new Set(target.deliveredPages || []);
      if (probe?.totalPages > 0 && !target.selection &&
          Array.from({ length: probe.totalPages }, (_, index) => index + 1).every(page => delivered.has(page))) {
        await advance(state, lane, settings, { pages: probe.totalPages, documentId: details.documentId || 0 });
        return { ...role(lane, false), recover: 'complete' };
      }
      target.attempts = (Number(target.attempts) || 0) + 1;
      target.coverageStatus = 'unresolved';
      lane.lastError = CONTENT_FAILURES.has(details.failureCode) ? details.failureCode :
        String(reason || 'The reviewer target did not become usable').slice(0, 300);
      lane.lastActivityAt = Date.now();
      // Capture the failing lease before advance() replaces its generation.
      const context = { lane: lane.lane, tabId: lane.tabId, generation: lane.generation,
        round: lane.round, targetIndex: lane.cursor + 1, targetKey: target.key, attempt: target.attempts,
        reason: lane.lastError, ...(CONTENT_FAILURES.has(details.failureCode) ? { failureCode: details.failureCode } : {}) };
      if (Number.isSafeInteger(details.documentId) && details.documentId > 0) context.documentId = details.documentId;
      if (Number.isInteger(details.responseStatus) && details.responseStatus >= 100 && details.responseStatus <= 599) {
        context.responseStatus = details.responseStatus;
      }
      const revalidatedBoundary = await revalidateInteriorBoundary(lane, details);
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
      } else if (!revalidatedBoundary) {
        // A failed document does not invalidate acknowledged earlier pages.
        // Retain the same outstanding range through every bounded recovery;
        // after that budget it waits behind healthy targets for the next round.
        await retainCheckpoint(lane);
        // Except a resume page past the target's current end (it shrank), or a
        // blank resume page on the last retry: read from page one instead.
        const next = resumePage(lane, target);
        if (next > 1 && ((probe && next > Math.max(1, probe.totalPages)) ||
            (details.failureCode === 'render-timeout' && target.attempts === RETRIES.length))) {
          await restartFromFirstPage(lane, target);
        }
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
      const targetId = await targetIdentity(before.target?.key);
      const context = { lane: before.lane.lane, tabId: before.lane.tabId, generation: message.generation,
        ...(targetId ? { targetId } : {}),
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
          if (latest.target && Number.isSafeInteger(message.page) && message.page > 0 && message.page <= 100) {
            latest.target.deliveredPages = [...new Set([...(latest.target.deliveredPages || []), message.page])];
          }
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
      const emptyProbe = message.type === 'reviewer:unusable' ? await probeUnusable(message, sender) : null;
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
        if (message.type === 'reviewer:unusable') {
          return fail(state, lane, settings, message.reason, { ...message, emptyProbe });
        }
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
        const windowId = await dependencies.windowId?.();
        if (await paused() || !enabled(lane.lane, await config())) return null;
        tab = await chrome.tabs.create({ url: waitingUrl, active: false,
          ...(Number.isInteger(windowId) ? { windowId } : {}) });
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
      // A member search a direct read found empty minutes ago would load the
      // same empty page: record it as empty without a page load and move on.
      const provenEmpty = () => {
        const next = currentTarget(lane);
        return lane.lane === 'requests' && next?.kind === 'keyword' && !next.checkpoint && !lane.checkpoint &&
          dependencies.recentlyEmpty?.(next.label) === true;
      };
      while (provenEmpty()) {
        currentTarget(lane).attemptedAt ||= Date.now();
        await advance(state, lane, settings, { emptyResult: true });
        if (lane.cursor >= lane.targets.length) return;
      }
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
        generation: lane.generation, round: lane.round, targetIndex: lane.cursor + 1, targetKey: target.key,
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
      if (lane && lane.fingerprint !== expectedFingerprint && appendQueryTargets(name, lane, settings)) {
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
          // A document can request its role before its navigation commits and
          // receive a valid passive reply. Rebind at the completion tick rather
          // than waiting for its next poll, but never treat that as progress or
          // renew the finite unresponsive-document deadline.
          if (probe?.ready === true && probe.active === false && probe.phase === 'passive' &&
              probe.generation === lane.generation && probe.busy !== true && probe.highTraffic !== true &&
              Date.now() - (Number(lane.lastActivityAt) || 0) < STALE_MS) {
            const auth = await authorized({ tab }, lane.generation, { selection: true });
            if (auth?.allowed) {
              await sendRole(auth.lane);
              recoveryDiagnostic(auth.lane, 'passive-rebind', 'requested', probe);
              return;
            }
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
          // focusReason is a cached denial, not current input evidence. The
          // content reader still requests fresh workspace authority to act.
          if (matches && probe.busy === true) {
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
      // Poll even with no current watches so the first Discord request starts
      // automatically. No reviewer tab is allocated until work exists.
      if (!await chrome.alarms.get(ALARM)) await chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
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
      try { await scheduleWake(); } catch { /* The alarm remains the fallback. */ }
    }

    // The 30s alarm alone left the 15s start slot idle ~40% of the time
    // (measured p50 gap 24s). Wake exactly when the next waiting lane is due.
    let wakeTimer = null;
    async function scheduleWake() {
      if (typeof setTimeout !== 'function') return;
      const settings = await config();
      const state = await read();
      const due = Object.values(state.lanes || {})
        // Only future slots: a lane already due but unable to start (focus,
        // pause) is retried by the alarm, never by a tight timer loop.
        .filter(lane => enabled(lane.lane, settings) && lane.status === 'waiting' && lane.nextAt > Date.now())
        .map(lane => lane.nextAt);
      if (wakeTimer !== null) clearTimeout(wakeTimer);
      wakeTimer = null;
      if (!due.length) return;
      const wait = Math.min(...due) - Date.now() + 50;
      if (wait >= 30_000) return; // The alarm covers longer waits.
      wakeTimer = setTimeout(() => {
        wakeTimer = null;
        tick().catch(() => {});
      }, wait);
      wakeTimer?.unref?.();
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
          targetLabel: currentTarget(lane)?.label || '', targetKey: currentTarget(lane)?.key || '',
          lastProgressAt: lane.lastActivityAt || 0, status: lane.status,
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
