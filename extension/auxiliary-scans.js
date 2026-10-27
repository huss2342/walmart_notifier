// Two explicitly owned browsing lanes complement the unchanged homepage scan.
// The content script still owns rendering, scrolling, pagination and ingestion
// acknowledgements; this coordinator only leases targets and isolates failures.
const ReviewerAuxiliaryScans = (() => {
  const STATE_KEY = 'reviewerAuxiliaryScans';
  const TABS_KEY = 'reviewerAuxiliaryTabs';
  const ALARM = 'auxiliaryScan';
  const LANES = ['departments', 'keywords'];
  const MAX_TARGETS = 2000;
  const MAX_FAILURES = 50;
  const CHECKPOINT_MAX_AGE_MS = 24 * 60 * 60_000;
  const STALE_MS = 120_000;
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
    const enabled = (name, settings) => name === 'departments'
      ? settings.auxiliaryDepartments === true : settings.auxiliaryKeywords === true;
    const spacing = settings => Math.max(5, Math.min(60, Number(settings.pageDelaySeconds) || 15)) * 1000;
    const restartAt = (lane, settings) => {
      const refresh = Number(settings.refreshMinutes);
      if (!(refresh > 0 && lane.completedAt > 0)) return 0;
      const interval = Math.max(60_000, refresh * 60_000);
      return lane.completedAt + interval + cycleJitterMs(interval, lane.completedAt,
        lane.lane === 'keywords' ? 2 : 1);
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

    function queries(settings) {
      return auxiliaryQueries(settings.auxiliaryKeywordQueries ?? undefined);
    }

    function fingerprint(name, settings) {
      return JSON.stringify(name === 'departments'
        ? ['top-level-department-destinations-v2', settings.auxiliaryBrands === true] : queries(settings));
    }

    function initialTargets(name, settings) {
      if (name === 'departments') return [{ key: 'departments:discovery', kind: 'discovery',
        label: 'Discover departments', targetUrl: home, selection: null, discoveryOnly: true, attempts: 0 }];
      return queries(settings).map(query => {
        const url = new URL(home);
        url.searchParams.set('q', query);
        return { key: `keyword:${query.toLocaleLowerCase('en-US')}`, kind: 'keyword', label: query,
          targetUrl: url.toString(), selection: null, attempts: 0 };
      });
    }

    async function read() {
      const stored = (await chrome.storage.local.get([STATE_KEY]))[STATE_KEY];
      return stored?.version === 1 && stored.lanes && typeof stored.lanes === 'object'
        ? clone(stored) : { version: 1, lanes: {} };
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
      return { lane: name, tabId: previous?.tabId ?? null,
        generation: Math.max(0, Number(previous?.generation) || 0) + 1,
        fingerprint: fingerprint(name, settings), targets: initialTargets(name, settings), cursor: 0,
        status: 'ready', nextAt: 0, startedAt: Date.now(), completedAt: 0, pages: 0,
        failures: 0, failedTargets: [], truncatedTargets: 0, coverageLimitedTargets: 0, lastError: '',
        lastActivityAt: Date.now(), checkpoint: null, round: (Number(previous?.round) || 0) + 1 };
    }

    function currentTarget(lane) { return lane?.targets?.[lane.cursor] || null; }
    function role(lane, active = true) {
      const target = currentTarget(lane);
      return { ok: true, active: Boolean(active && target && lane.status === 'scanning'),
        tabId: lane.tabId, primaryTabId: lane.tabId, generation: lane.generation,
        auxiliary: { lane: lane.lane, mode: 'none', targetKey: target?.key || '',
          targetKind: target?.kind === 'discovery' ? 'root' : target?.kind || '',
          targetUrl: target?.targetUrl || home, selection: clone(target?.selection),
          discoveryOnly: target?.discoveryOnly === true } };
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
      if (!Number.isInteger(lane.tabId)) return;
      const key = `sweep:${lane.tabId}`;
      const sweep = (await chrome.storage.local.get([key]))[key];
      if (sweep?.generation === lane.generation && Array.isArray(sweep.visited)) {
        lane.checkpoint = clone(sweep);
      }
      try {
        const tab = await chrome.tabs.get(lane.tabId);
        if (!currentTarget(lane)?.selection && cleanUrl(tab.url) === cleanUrl(currentTarget(lane)?.targetUrl)) {
          lane.checkpointUrl = tab.url;
        }
      } catch { /* The durable checkpoint remains valid after a tab closes. */ }
    }

    async function clearSweep(lane) {
      if (Number.isInteger(lane.tabId)) await chrome.storage.local.remove(`sweep:${lane.tabId}`);
      lane.checkpoint = null;
      lane.checkpointUrl = null;
    }

    async function advance(state, lane, settings, { failed = false, reason = '', pages = 0 } = {}) {
      const target = currentTarget(lane);
      if (failed) {
        lane.failures += 1;
        lane.lastError = String(reason).slice(0, 300);
        lane.failedTargets = [...(lane.failedTargets || []), { target: target?.label || '',
          at: Date.now(), reason: lane.lastError }].slice(-MAX_FAILURES);
      }
      lane.pages += Math.max(0, Math.min(100, Number(pages) || 0));
      lane.cursor += 1;
      lane.generation += 1;
      lane.lastActivityAt = Date.now();
      await clearSweep(lane);
      if (lane.cursor >= lane.targets.length) {
        lane.completedAt = Date.now();
        lane.status = lane.failures || lane.truncatedTargets || lane.coverageLimitedTargets ? 'partial' : 'complete';
        lane.nextAt = restartAt(lane, settings);
      } else {
        lane.status = 'waiting';
        lane.nextAt = Date.now() + spacing(settings);
      }
      await save(state);
      await sendRole(lane, false);
    }

    async function fail(state, lane, settings, reason) {
      const target = currentTarget(lane);
      if (!target) return role(lane, false);
      target.attempts = (Number(target.attempts) || 0) + 1;
      lane.lastError = String(reason || 'The reviewer target did not become usable').slice(0, 300);
      lane.lastActivityAt = Date.now();
      if (target.attempts > RETRIES.length) {
        await advance(state, lane, settings, { failed: true, reason: lane.lastError });
        await diagnostic('auxiliary-target-skipped', { lane: lane.lane, target: target.label, reason: lane.lastError });
        return { ...role(lane, false), recover: 'skipped' };
      }
      if (target.selection) {
        // An unbound SPA navigation may have changed the URL without ever
        // rendering its cards. Reloading that destination repeats an
        // unconfirmed selection; retry the actual control from its parent.
        await clearSweep(lane);
      } else if (target.attempts === 1) {
        // First recover the same scope/page with its acknowledged progress.
        // Persist before revoking the document so worker/browser restarts keep
        // both the resume destination and the bounded recovery stage.
        await retainCheckpoint(lane);
      } else {
        // A repeated unusable page gets exactly one fresh pass from this
        // target's page one. The next failure spends the existing skip budget.
        await clearSweep(lane);
      }
      lane.generation += 1;
      lane.status = 'waiting';
      lane.nextAt = Date.now() + RETRIES[target.attempts - 1];
      await save(state);
      await sendRole(lane, false);
      return { ...role(lane, false), ok: false, recover: 'waiting', retryAfterMs: RETRIES[target.attempts - 1] };
    }

    async function page(message, sender) {
      const before = await authorized(sender, message.generation);
      if (!before) return null;
      if (!before.allowed || !Array.isArray(message.items) || !message.items.length) {
        return { ...role(before.lane, false), ok: false, reason: 'inactive-or-unbound-target' };
      }
      const result = await post(message.items, message.page,
        async () => (await authorized(sender, message.generation))?.allowed === true);
      const after = await authorized(sender, message.generation);
      if (result.ok && after?.allowed) {
        await mutate(async () => {
          const latest = await authorized(sender, message.generation);
          if (!latest?.allowed) return;
          latest.lane.lastActivityAt = Date.now();
          await save(latest.state);
        });
        await diagnostic('auxiliary-page-delivered', { lane: before.lane.lane, tabId: before.lane.tabId,
          generation: message.generation, page: message.page, items: message.items.length });
      }
      // Diagnostics and checkpoint writes can yield to a challenge, disable or
      // target change. Return the current lease, never the one captured before
      // those awaits, so a late acknowledgement cannot revive an old document.
      const latest = await authorized(sender, message.generation);
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
          const duplicate = lane.targets.some(entry => entry !== target && entry.boundUrl === url);
          if (duplicate) {
            await advance(state, lane, settings);
            return { ...role(lane, false), duplicate: true };
          }
          target.targetUrl = url;
          target.boundUrl = url;
          target.selection = null;
          await clearSweep(lane);
          await save(state);
          return role(lane);
        }
        if (message.type === 'reviewer:unusable') return fail(state, lane, settings, message.reason);
        if (message.type === 'reviewer:sweep-complete') {
          if (message.coverageLimited === true && !target.coverageLimited) {
            target.coverageLimited = true;
            lane.coverageLimitedTargets = (Number(lane.coverageLimitedTargets) || 0) + 1;
            lane.lastError = 'A target reached the observed 25-page limit; all available pages were scanned, but Walmart may hide additional items.';
          }
          await advance(state, lane, settings, { pages: message.pages });
          return role(lane, false);
        }
        return { ...role(lane), ok: false, reason: 'unsupported-auxiliary-message' };
      });
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
      const ownership = await owned();
      let tab;
      if (ownership[lane.lane] === lane.tabId) {
        try { tab = await chrome.tabs.get(lane.tabId); } catch { /* Recreate only our missing tab. */ }
      }
      if (!tab) {
        // Creating a blank first guarantees no reviewer content can request a
        // primary lease before its explicit auxiliary ownership is durable.
        if (await paused()) return;
        tab = await chrome.tabs.create({ url: 'about:blank', active: false });
        const oldTabId = lane.tabId;
        lane.tabId = tab.id;
        lane.generation += 1;
        ownership[lane.lane] = tab.id;
        await chrome.storage.session.set({ [TABS_KEY]: ownership });
        if (Number.isInteger(oldTabId) && oldTabId !== tab.id) await chrome.storage.local.remove(`sweep:${oldTabId}`);
      }
      const target = currentTarget(lane);
      if (!target) return;
      lane.status = 'scanning';
      lane.nextAt = 0;
      lane.lastActivityAt = Date.now();
      const progressAt = Number(lane.checkpoint?.lastProgressAt);
      if (Number.isFinite(progressAt) && progressAt > 0 &&
          Date.now() - progressAt > CHECKPOINT_MAX_AGE_MS) {
        // Known stale coverage needs a fresh target pass. Legacy checkpoints
        // without a progress clock remain resumable; expiry is not a failure.
        await clearSweep(lane);
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
      await save(state);
      await chrome.tabs.update(lane.tabId, { url: destination, active: false });
      await diagnostic('auxiliary-target-start', { lane: lane.lane, tabId: lane.tabId,
        generation: lane.generation, target: target.label });
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
      if (!lane || lane.fingerprint !== fingerprint(name, settings)) {
        const previous = lane;
        lane = state.lanes[name] = freshLane(name, previous, settings);
        if (!previous) {
          const suppliedDelay = Number(dependencies.initialStartDelay?.(settings, name));
          const initialDelay = Math.max(0, Number.isFinite(suppliedDelay) ? suppliedDelay : 0,
            name === 'keywords' && settings.auxiliaryDepartments === true ? spacing(settings) : 0);
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
          const activeAt = Math.max(Number(lane.lastActivityAt) || 0, Number(probe?.lastActivityAt) || 0);
          if (Date.now() - activeAt < STALE_MS || probe?.nextActionAt > Date.now() ||
              ['ingest-retry', 'waiting-user'].includes(probe?.phase)) return;
          await fail(state, lane, settings, 'Managed reviewer page stopped responding');
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
      for (const name of LANES) {
        await mutate(async () => {
          const settings = await config();
          try { await tickLane(name, settings); }
          catch (error) {
            const state = await read();
            const lane = state.lanes[name] ||= { lane: name, tabId: null, generation: 0,
              targets: [], cursor: 0, pages: 0, failures: 0, failedTargets: [], truncatedTargets: 0 };
            if (enabled(name, settings)) {
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
        return { lane: name, tabId: lane.tabId, generation: lane.generation,
          targetLabel: currentTarget(lane)?.label || '', status: lane.status,
          completedTargets: lane.cursor, totalTargets: lane.targets.length, pages: lane.pages,
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
          const lane = state.lanes[laneName] = freshLane(laneName, state.lanes[laneName], settings);
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

  return { create };
})();
