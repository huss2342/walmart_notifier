// Group only explicitly registered scanner tabs. A short, optional activation
// slot changes the selected tab inside its existing Chrome window; this
// module never focuses a window, moves tabs between windows or supplies input.
const ReviewerTabWorkspace = (() => {
  const STORAGE_KEY = 'reviewerTabWorkspace';
  const TITLE = 'Reviewer scans';
  const COLOR = 'blue';
  const OLD_TITLE = 'Old reviewer tabs — safe to close';
  const SLOT_MS = 3000;
  const RETRY_MS = 5000;
  const ACTIVITY_MS = 30_000;
  const QUEUE_TTL_MS = 15_000;
  let instances = 0;

  function create(chrome, { config, paused, authorize, isManaged, requestTimeoutMs = 5000,
    cleanupTimeoutMs = 1000 }) {
    let registered = new Map();
    let groups = {};
    let owned = {};
    let syncVersion = 0;
    let requestedIds = new Set();
    const removing = new Set();
    let activityUntil = 0;
    let epoch = 0;
    let acquisitionEpoch = 0;
    let lease = null;
    let leaseTimer = null;
    let expected = null;
    let focusInFlight = false;
    let lastNow = 0;
    let loaded = null;
    let syncTail = Promise.resolve();
    let saveTail = Promise.resolve();
    const instanceNonce = `${++instances}-${globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)}`;
    let leaseSequence = 0;
    const waiting = new Map();
    const now = () => (lastNow = Math.max(lastNow, Number(Date.now()) || 0));
    const denied = (reason, enabled = true) => ({ ok: true, granted: false, enabled,
      ...(enabled ? { retryAfterMs: RETRY_MS } : {}), reason });

    function safeTab(tab, grouping = false) {
      if (!Number.isInteger(tab?.id) || !Number.isInteger(tab.windowId)) return false;
      return [tab.url, tab.pendingUrl].filter(Boolean).length > 0 &&
        [tab.url, tab.pendingUrl].filter(Boolean).every(raw => {
          try {
            if (grouping && raw === chrome.runtime?.getURL?.('scanner-wait.html')) return true;
            const url = new URL(raw);
            return url.protocol === 'https:' && url.hostname === 'www.walmart.com' &&
              !url.username && !url.password && !url.port &&
              (/^\/reviews\/claim-product\/?$/.test(url.pathname) ||
                (grouping && /^\/blocked(?:\/|$)/i.test(url.pathname)));
          } catch { return false; }
        });
    }

    async function load() {
      if (!loaded) loaded = (async () => {
        try {
          const stored = (await chrome.storage?.session?.get([STORAGE_KEY]))?.[STORAGE_KEY];
          if (stored?.version !== 1) return;
          groups = stored.groups && typeof stored.groups === 'object' ? stored.groups : {};
          owned = stored.owned && typeof stored.owned === 'object' ? stored.owned : {};
          activityUntil = Math.max(activityUntil, Math.min(now() + ACTIVITY_MS, Number(stored.activityUntil) || 0));
        } catch { /* Grouping and rotation remain optional when storage is unavailable. */ }
      })();
      await loaded;
    }

    function persist() {
      const result = saveTail.then(async () => {
        try { await chrome.storage?.session?.set({ [STORAGE_KEY]: { version: 1, groups, owned, activityUntil } }); }
        catch { /* A failed optional cache never stops scanning. */ }
      });
      saveTail = result.catch(() => undefined);
      return result;
    }

    function notifySelectionRevoked(previous) {
      if (!Number.isInteger(previous?.tabId)) return;
      try {
        Promise.resolve(chrome.tabs.sendMessage?.(previous.tabId, {
          type: 'reviewer:selection-revoked', generation: previous.generation,
          leaseUntil: previous.until, leaseId: previous.leaseId
        })).catch(() => undefined);
      } catch { /* A closed document cannot receive a revocation. */ }
    }

    function revoke() {
      const previous = lease;
      epoch += 1;
      lease = null;
      clearTimeout(leaseTimer);
      leaseTimer = null;
      expected = null;
      waiting.clear();
      notifySelectionRevoked(previous);
      return { ok: true };
    }

    // OS focus changes cancel acquisitions that relied on a background window,
    // while an already selected scanner keeps its valid reading/navigation turn.
    function cancelPendingSelection() {
      acquisitionEpoch += 1;
      const established = lease && lease.until > now() && expected?.tabId === lease.tabId &&
        expected.generation === lease.generation && expected.epoch === epoch;
      if (!established) expected = null;
      return { ok: true };
    }

    async function noteActivity(tabId) {
      if (!registered.has(tabId)) return { ok: false };
      revoke();
      // Set before awaiting storage so an in-flight request cannot acquire a
      // new slot between trusted user input and its durable idle checkpoint.
      activityUntil = now() + ACTIVITY_MS;
      await load();
      await persist();
      return { ok: true, until: activityUntil };
    }

    async function groupContents(id, windowId, title, color) {
      try {
        const group = await chrome.tabGroups.get(id);
        if (group.windowId !== windowId || group.title !== title || group.color !== color ||
            group.shared === true) return null;
        const members = await chrome.tabs.query({ groupId: id });
        if (!members.length || members.some(tab => tab.windowId !== windowId || !safeTab(tab, true))) return null;
        return { group, members };
      } catch { return null; }
    }

    async function validGroup(record, windowId, tabs) {
      if (!record || !Number.isInteger(record.id) || record.windowId !== windowId ||
          !Array.isArray(record.tabIds)) return null;
      const found = await groupContents(record.id, windowId, TITLE, COLOR);
      if (found && found.members.every(tab => record.tabIds.includes(tab.id))) {
        // A live previously registered member anchors the saved identity. A
        // reused numeric group ID with unrelated tabs is never adopted.
        if (tabs.some(tab => record.tabIds.includes(tab.id) && tab.groupId === record.id)) return found.group;
      }
      return null;
    }

    async function recoverGroup(windowId, tabs) {
      // Chrome clears storage.session on an extension reload. Recover only
      // through an already registered live tab, never by a matching title alone.
      for (const id of new Set(tabs.map(tab => tab.groupId).filter(id => Number.isInteger(id) && id >= 0))) {
        const found = await groupContents(id, windowId, TITLE, COLOR);
        if (found && found.members.some(tab => registered.has(tab.id))) return found.group;
      }
      return null;
    }

    function matchesOwner(tab, owner, groupId, windowId) {
      return owner && owner.groupId === groupId && owner.windowId === windowId &&
        tab?.groupId === groupId && tab.windowId === windowId && tab.url === owner.url;
    }

    async function closeRetired(tabId, groupId, windowId, title, color, version, pass) {
      // Group names and reviewer URLs are not ownership evidence. The tab must
      // have been explicitly registered before, and the coordinator must now
      // confirm that it is no longer in use. Old archives without that evidence
      // remain available for the owner to close themselves.
      const owner = owned[tabId];
      if (!pass.active || pass.attempted.has(tabId) || !owner || typeof isManaged !== 'function' ||
          !chrome.tabs?.remove || removing.has(tabId)) return;
      pass.attempted.add(tabId);
      const token = epoch;
      const attempt = { active: true };
      let timer;
      const work = (async () => {
        if (registered.has(tabId) || requestedIds.has(tabId) || await isManaged(tabId) !== false) return;
        if (await paused() || (await config()).groupScannerTabs === false) return;
        const tab = await chrome.tabs.get(tabId);
        const group = await chrome.tabGroups.get(groupId);
        if (await paused()) return;
        if (!attempt.active || version !== syncVersion || token !== epoch || activityUntil > now() ||
            registered.has(tabId) || requestedIds.has(tabId) || group.shared === true ||
            group.windowId !== windowId || group.title !== title || group.color !== color ||
            !matchesOwner(tab, owner, groupId, windowId) || !safeTab(tab) || tab.pendingUrl ||
            tab.active || tab.pinned || tab.status === 'loading') return;
        removing.add(tabId);
        try {
          await chrome.tabs.remove(tabId);
          if (owned[tabId] === owner) delete owned[tabId];
        } finally { removing.delete(tabId); }
      })().catch(() => undefined);
      try {
        await Promise.race([work, new Promise(resolve => {
          timer = setTimeout(() => { attempt.active = false; pass.active = false; resolve(); },
            Math.max(1, Math.min(1000, Number(cleanupTimeoutMs) || 1000)));
        })]);
      } finally { attempt.active = false; clearTimeout(timer); }
    }

    async function separateRetired(groupId, windowId, version, pass) {
      if ((await config()).groupScannerTabs === false) return;
      const current = await groupContents(groupId, windowId, TITLE, COLOR);
      if (!current) return;
      const retired = [];
      for (const member of current.members) {
        if (registered.has(member.id)) continue;
        await closeRetired(member.id, groupId, windowId, TITLE, COLOR, version, pass);
        // Never move a tab while a previously issued removal is still pending.
        if (removing.has(member.id) || registered.has(member.id) || requestedIds.has(member.id)) continue;
        const tab = await chrome.tabs.get(member.id).catch(() => null);
        if (tab?.groupId === groupId && tab.windowId === windowId && safeTab(tab, true) && !tab.pinned) retired.push(tab);
      }
      if (!retired.length) return;
      let archive;
      for (const candidate of await chrome.tabGroups.query({ windowId })) {
        const found = await groupContents(candidate.id, windowId, OLD_TITLE, 'grey');
        if (found && found.members.every(tab => !registered.has(tab.id))) { archive = found.group; break; }
      }
      if ((await chrome.tabGroups.get(groupId)).shared === true ||
          (archive && (await chrome.tabGroups.get(archive.id)).shared === true)) return;
      if ((await config()).groupScannerTabs === false) return;
      const archiveId = await chrome.tabs.group({ tabIds: retired.map(tab => tab.id),
        ...(archive ? { groupId: archive.id } : { createProperties: { windowId } }) });
      for (const tab of retired) {
        if (matchesOwner(tab, owned[tab.id], groupId, windowId)) owned[tab.id] = { ...owned[tab.id], groupId: archiveId };
      }
      const latest = await chrome.tabGroups.get(archiveId);
      const members = await chrome.tabs.query({ groupId: archiveId });
      if (latest.shared === true || latest.windowId !== windowId ||
          members.some(tab => registered.has(tab.id) || !safeTab(tab, true))) return;
      if ((await config()).groupScannerTabs === false) return;
      await chrome.tabGroups.update(archiveId, { title: OLD_TITLE, color: 'grey',
        collapsed: !members.some(tab => tab.active) });
    }

    async function syncUnlocked(tabIds, version) {
      await load();
      const cleanupPass = { active: true, attempted: new Set() };
      const ids = [...new Set(Array.isArray(tabIds) ? tabIds.filter(Number.isInteger) : [])];
      const tabs = [];
      for (const id of ids) {
        try {
          const tab = await chrome.tabs.get(id);
          if (safeTab(tab, true)) tabs.push(tab);
        } catch { /* A closed or inaccessible tab is not a scanner workspace member. */ }
      }
      registered = new Map(tabs.map(tab => [tab.id, { windowId: tab.windowId }]));
      for (const id of waiting.keys()) if (!registered.has(id)) waiting.delete(id);
      if (lease && !registered.has(lease.tabId)) revoke();
      const settings = await config();
      if (settings.groupScannerTabs === false) return { ok: true, groupingEnabled: false, grouped: 0 };
      if (!chrome.tabs?.group || !chrome.tabs?.query || !chrome.tabGroups?.get || !chrome.tabGroups?.update) {
        return { ok: true, groupingEnabled: false, grouped: 0, reason: 'grouping-unavailable' };
      }
      let grouped = 0;
      let failures = 0;
      const byWindow = new Map();
      for (const tab of tabs) {
        if (!byWindow.has(tab.windowId)) byWindow.set(tab.windowId, []);
        byWindow.get(tab.windowId).push(tab);
      }
      for (const [windowId, candidates] of byWindow) {
        try {
          // The primary/interrupted tab is first in the caller's list. New
          // workers may register before primary election after a reload; their
          // provisional cached group must not pull that surviving anchor out
          // of its old group and strand the retired members there.
          let group = await recoverGroup(windowId, candidates) ||
            await validGroup(groups[windowId], windowId, candidates);
          const live = [];
          for (const tab of candidates) {
            const current = await chrome.tabs.get(tab.id);
            if (!safeTab(current, true) || current.windowId !== windowId) continue;
            if (Number.isInteger(current.groupId) && current.groupId >= 0) {
              // Shared groups are collaborative user workspaces. Never pull a
              // scanner out of one or change its metadata, even when a saved
              // private scanner group has since become shared.
              try {
                if ((await chrome.tabGroups.get(current.groupId)).shared === true) continue;
              } catch { continue; }
            }
            live.push(current);
          }
          if (!live.length || (await config()).groupScannerTabs === false) continue;
          let groupId = group?.id;
          if (group && (await chrome.tabGroups.get(groupId)).shared === true) continue;
          if (groupId === undefined || live.some(tab => tab.groupId !== groupId)) {
            groupId = await chrome.tabs.group({ tabIds: live.map(tab => tab.id),
              ...(group ? { groupId } : { createProperties: { windowId } }) });
          }
          group = await chrome.tabGroups.get(groupId);
          if (group.windowId !== windowId || group.shared === true) continue;
          if ((await config()).groupScannerTabs === false) continue;
          await chrome.tabGroups.update(groupId, { title: TITLE, color: COLOR });
          groups[windowId] = { id: groupId, windowId, tabIds: live.map(tab => tab.id) };
          for (const tab of live) owned[tab.id] = { windowId, groupId, url: tab.url };
          grouped += live.length;
          await separateRetired(groupId, windowId, version, cleanupPass);
        } catch { failures += 1; }
      }
      // Retry known archives retained during verification or after a Chrome
      // error, including windows that no longer contain a live scanner. Only
      // explicit ownership records qualify; a matching group title never does.
      if (typeof isManaged === 'function') for (const [rawId, owner] of Object.entries(owned)) {
        const id = Number(rawId);
        if (!cleanupPass.active) break;
        if (!Number.isInteger(id) || registered.has(id) || cleanupPass.attempted.has(id)) continue;
        try {
          const group = await chrome.tabGroups.get(owner.groupId);
          if ((group.title === TITLE && group.color === COLOR) ||
              (group.title === OLD_TITLE && group.color === 'grey')) {
            await closeRetired(id, owner.groupId, owner.windowId, group.title, group.color, version, cleanupPass);
          }
        } catch { /* A removed or inaccessible group cannot authorize cleanup. */ }
      }
      await persist();
      return { ok: true, groupingEnabled: true, grouped, failures };
    }

    function sync(tabIds) {
      const version = ++syncVersion;
      // Publish new live IDs immediately, before a preceding async cleanup can
      // use an older registration snapshot to close a newly reused scanner.
      requestedIds = new Set(Array.isArray(tabIds) ? tabIds.filter(Number.isInteger) : []);
      const result = syncTail.then(() => syncUnlocked(tabIds, version), () => syncUnlocked(tabIds, version));
      syncTail = result.catch(() => undefined);
      return result.catch(() => ({ ok: true, groupingEnabled: false, grouped: 0, reason: 'workspace-unavailable' }));
    }

    function expired(expiresAt) {
      return expiresAt !== null && (!Number.isFinite(expiresAt) || expiresAt <= now());
    }

    function invalidRequest(token, expiresAt, acquisition = null) {
      if (token !== epoch || (acquisition !== null && acquisition !== acquisitionEpoch)) return denied('revoked');
      if (expired(expiresAt)) return denied('expired');
      return null;
    }

    async function guard(token, tabId, generation, expiresAt, rotationRequired = true, acquisition = null) {
      let invalid = invalidRequest(token, expiresAt, acquisition);
      if (invalid) return invalid;
      const settings = await config();
      invalid = invalidRequest(token, expiresAt, acquisition);
      if (invalid) return invalid;
      if (rotationRequired && settings.rotateScannerTabs === false) return denied('disabled', false);
      const isPaused = await paused();
      invalid = invalidRequest(token, expiresAt, acquisition);
      if (invalid) return invalid;
      if (isPaused) return denied('paused');
      if (activityUntil > now()) return denied('user-active');
      if (!registered.has(tabId)) return denied('not-authorized');
      const isAuthorized = await authorize(tabId, generation);
      invalid = invalidRequest(token, expiresAt, acquisition);
      if (invalid) return invalid;
      if (!isAuthorized) return denied('not-authorized');
      const stillPaused = await paused();
      invalid = invalidRequest(token, expiresAt, acquisition);
      if (invalid) return invalid;
      if (stillPaused) return denied('paused');
      return null;
    }

    async function contextFor(tabId) {
      const tab = await chrome.tabs.get(tabId);
      if (!safeTab(tab) || registered.get(tabId)?.windowId !== tab.windowId) return { error: 'not-scanner' };
      // A focused Chrome window preserves manual browsing. While the window
      // is behind another app or minimized, collection can select its own tab
      // without restoring or focusing the OS window.
      const window = chrome.windows?.get ? await chrome.windows.get(tab.windowId) : null;
      const background = window?.focused === false || window?.state === 'minimized';
      const [selected] = await chrome.tabs.query({ windowId: tab.windowId, active: true });
      if (!selected || (!background && (!registered.has(selected.id) || !safeTab(selected)))) {
        return { error: 'manual-tab-active' };
      }
      return { tab, selected };
    }

    function enqueue(tabId, generation, expiresAt) {
      const existing = waiting.get(tabId);
      if (!existing || existing.generation !== generation) {
        waiting.set(tabId, { generation, at: now(), expiresAt });
      } else {
        // A third scanner may wait through two full leases. Refresh liveness
        // on its fresh request while retaining its Map insertion/FIFO order.
        existing.at = now();
      }
    }

    // A wakeup is advisory: the receiver must submit a fresh request, with all
    // normal ownership/input guards. One hint per FIFO ticket and lease avoids
    // a retry storm when a document is absent or its timer is delayed.
    function hintWaitingHead() {
      if (focusInFlight || (lease && lease.until > now()) || activityUntil > now()) return;
      for (const [id, ticket] of waiting) {
        if (!registered.has(id) || ticket.at + QUEUE_TTL_MS < now()) waiting.delete(id);
      }
      const [tabId, ticket] = waiting.entries().next().value || [];
      if (!ticket || ticket.hintedSequence === leaseSequence) return;
      ticket.hintedSequence = leaseSequence;
      const token = epoch;
      const sequence = leaseSequence;
      const current = () => token === epoch && sequence === leaseSequence &&
        waiting.get(tabId) === ticket && waiting.keys().next().value === tabId &&
        registered.has(tabId) && ticket.at + QUEUE_TTL_MS >= now() &&
        !focusInFlight && (!lease || lease.until <= now());
      (async () => {
        const rejection = await guard(token, tabId, ticket.generation, null);
        if (!current()) return;
        if (rejection) {
          if (rejection.reason === 'not-authorized') {
            waiting.delete(tabId);
            hintWaitingHead();
          }
          return;
        }
        let context = await contextFor(tabId);
        if (!current() || context.error) return;
        if (await guard(token, tabId, ticket.generation, null) || !current()) return;
        context = await contextFor(tabId);
        if (!current() || context.error || activityUntil > now()) return;
        // Never await a missing receiver or let delivery failure own the queue.
        Promise.resolve(chrome.tabs.sendMessage?.(tabId, {
          type: 'reviewer:focus-available', generation: ticket.generation
        })).catch(() => undefined);
      })().catch(() => undefined);
    }

    function expireLease(previous) {
      clearTimeout(leaseTimer);
      leaseTimer = setTimeout(() => {
        if (lease !== previous) return;
        if (previous.until > now()) { expireLease(previous); return; }
        lease = null;
        leaseTimer = null;
        notifySelectionRevoked(previous);
        hintWaitingHead();
      }, Math.max(1, previous.until - now()));
      leaseTimer?.unref?.();
    }

    async function request(tabId, generation, expiresAt = null, slotMs = SLOT_MS) {
      // A full results-page animation needs more than a short pager reveal.
      // Keep the lease finite; repeated requests never extend a live lease.
      slotMs = Number.isFinite(slotMs) ? Math.max(SLOT_MS, Math.min(10_000, slotMs)) : SLOT_MS;
      if (expired(expiresAt)) return denied('expired');
      // Contenders never sleep behind another tab's slot or browser API call.
      if (focusInFlight) {
        if (registered.has(tabId)) enqueue(tabId, generation, expiresAt);
        return denied('busy');
      }
      // A contender that must yield to the FIFO head cannot hold acquisition
      // while checking eligibility: that would make the head return busy and
      // the contender return waiting-turn on every simultaneous retry.
      const head = waiting.keys().next().value;
      const returningOwner = lease && lease.until > now() &&
        lease.tabId === tabId && lease.generation === generation;
      const queuedBehind = head !== undefined && head !== tabId && !returningOwner;
      const flight = { mutationPending: false };
      if (!queuedBehind) focusInFlight = flight;
      const token = epoch;
      const acquisition = acquisitionEpoch;
      const requestGuard = () => guard(token, tabId, generation, expiresAt, true, acquisition);
      const work = (async () => {
      try {
        await load();
        if (!chrome.tabs?.get || !chrome.tabs?.query || !chrome.tabs?.update || typeof authorize !== 'function') {
          return denied('activation-unavailable', false);
        }
        let rejection = await requestGuard();
        if (rejection) return rejection;
        let context = await contextFor(tabId);
        rejection = await requestGuard();
        if (rejection) return rejection;
        if (context.error) return denied(context.error);
        for (const [id, pending] of waiting) {
          // Activation expires quickly, but the FIFO ticket must outlive a
          // competing scroll lease. Only a fresh guarded request can activate;
          // retaining priority does not grant an expired request permission.
          if (!registered.has(id) || pending.at + QUEUE_TTL_MS < now()) waiting.delete(id);
        }
        // Eligibility checks and stale-ticket cleanup still run to completion,
        // but an unowned request never mutates a lease or selects a tab. Its
        // timeout/finally also cannot clear the head's newer acquisition.
        if (queuedBehind) {
          enqueue(tabId, generation, expiresAt);
          return denied(lease && lease.until > now() ? 'busy' : 'waiting-turn');
        }
        if (lease && lease.until > now()) {
          if (lease.tabId === tabId && lease.generation === generation) {
            if (context.selected.id !== tabId) {
              notifySelectionRevoked(lease);
              lease = null;
              return denied('selection-changed');
            }
            return { ok: true, granted: true, enabled: true, until: lease.until, leaseId: lease.leaseId };
          }
          enqueue(tabId, generation, expiresAt);
          return denied('busy');
        }
        notifySelectionRevoked(lease);
        lease = null;
        enqueue(tabId, generation, expiresAt);
        if (waiting.keys().next().value !== tabId) return denied('waiting-turn');
        // Recheck the live selected tab and window immediately before changing
        // selection. The root revokes the epoch on real user interference.
        context = await contextFor(tabId);
        rejection = await requestGuard();
        if (rejection) return rejection;
        if (context.error) return denied(context.error);
        // Authorization can yield while the owner returns to a manual tab in
        // the now-foreground window. Resample that eligibility, then make the
        // focus-event acquisition check synchronously before dispatching input.
        context = await contextFor(tabId);
        rejection = invalidRequest(token, expiresAt, acquisition);
        if (rejection) return rejection;
        if (context.error) return denied(context.error);
        if (context.selected.id !== tabId) {
          expected = { tabId, generation, epoch: token, until: now() + slotMs };
          flight.mutationPending = true;
          try { await chrome.tabs.update(tabId, { active: true }); }
          finally { flight.mutationPending = false; }
        }
        rejection = await requestGuard();
        if (rejection) return rejection;
        context = await contextFor(tabId);
        rejection = await requestGuard();
        if (rejection) return rejection;
        if (context.error || context.selected.id !== tabId) return denied(context.error || 'selection-changed');
        waiting.delete(tabId);
        lease = { tabId, generation, until: now() + slotMs, leaseId: `${instanceNonce}:${++leaseSequence}` };
        expireLease(lease);
        return { ok: true, granted: true, enabled: true, until: lease.until, leaseId: lease.leaseId };
      } catch {
        if (focusInFlight === flight) expected = null;
        return denied('activation-unavailable', false);
      } finally { if (focusInFlight === flight) focusInFlight = false; }
      })();
      let timer;
      const duration = Math.max(1, Math.min(5000, Number(requestTimeoutMs) || 5000,
        expiresAt === null ? 5000 : expiresAt - now()));
      try {
        return await Promise.race([work, new Promise(resolve => {
          timer = setTimeout(() => {
            if (focusInFlight === flight) {
              // Revoke authority before unlocking. A late browser callback
              // must not activate a tab or clear a newer request's lock.
              revoke();
              // A command already submitted to Chrome cannot be cancelled by
              // a JS timeout. Keep it serialized until Chrome acknowledges it.
              if (!flight.mutationPending) focusInFlight = false;
            }
            resolve(denied('expired'));
          }, duration);
        })]);
      } finally { clearTimeout(timer); }
    }

    // Hand back only the exact turn that finished. Keep contenders and pending
    // activation acknowledgements intact; neither release nor a stale finally
    // callback may revoke a replacement turn or move another tab to the front.
    async function release(tabId, generation, leaseId) {
      const previous = lease;
      const matches = () => previous && lease === previous &&
        previous.tabId === tabId && previous.generation === generation &&
        typeof leaseId === 'string' && previous.leaseId === leaseId &&
        Number.isFinite(previous.until) && previous.until > now();
      if (!matches()) return { ok: true, released: false };
      const token = epoch;
      try {
        await load();
        if (await guard(token, tabId, generation, null, false) || !matches()) {
          return { ok: true, released: false };
        }
        lease = null;
        clearTimeout(leaseTimer);
        leaseTimer = null;
        notifySelectionRevoked(previous);
        hintWaitingHead();
        return { ok: true, released: true };
      } catch { return { ok: true, released: false }; }
    }

    // Read-only authority for ordinary collector work. OS window foreground is
    // irrelevant to selected-tab ownership; these checks never select a tab,
    // renew a lease, or authorize the separate verification input transports.
    async function selectedAuthority(tabId, generation, minimumLeaseMs) {
      const token = epoch;
      try {
        await load();
        if (await guard(token, tabId, generation, null, false)) return false;
        let context = await contextFor(tabId);
        if (context.error || context.selected.id !== tabId) return false;
        const settings = await config();
        const validLease = () => settings.rotateScannerTabs === false ||
          (lease?.tabId === tabId && lease.generation === generation &&
            lease.until > now() && lease.until - now() >= minimumLeaseMs);
        if (!validLease() || await guard(token, tabId, generation, null, false)) return false;
        context = await contextFor(tabId);
        return !invalidRequest(token, null) && !context.error &&
          context.selected.id === tabId && validLease();
      } catch { return false; }
    }
    const allowsInput = (tabId, generation) => selectedAuthority(tabId, generation, 1_500);
    const allowsSelection = (tabId, generation) => selectedAuthority(tabId, generation, 0);

    function isExpectedActivation(tabId) {
      const matches = expected?.tabId === tabId && expected.epoch === epoch && expected.until >= now();
      if (matches) expected = null;
      return matches;
    }

    return { sync, request, release, revoke, pause: revoke, cancelPendingSelection, noteActivity,
      isExpectedActivation, allowsInput, allowsSelection };
  }

  return { create };
})();
