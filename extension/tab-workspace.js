// Group only explicitly registered scanner tabs. A short, optional activation
// slot changes the selected tab inside its existing foreground window; this
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

  function create(chrome, { config, paused, authorize }) {
    let registered = new Map();
    let groups = {};
    let activityUntil = 0;
    let epoch = 0;
    let lease = null;
    let expected = null;
    let focusInFlight = false;
    let lastNow = 0;
    let loaded = null;
    let syncTail = Promise.resolve();
    let saveTail = Promise.resolve();
    const waiting = new Map();
    const now = () => (lastNow = Math.max(lastNow, Number(Date.now()) || 0));
    const denied = (reason, enabled = true) => ({ ok: true, granted: false, enabled,
      ...(enabled ? { retryAfterMs: RETRY_MS } : {}), reason });

    function safeTab(tab, grouping = false) {
      if (!Number.isInteger(tab?.id) || !Number.isInteger(tab.windowId)) return false;
      return [tab.url, tab.pendingUrl].filter(Boolean).length > 0 &&
        [tab.url, tab.pendingUrl].filter(Boolean).every(raw => {
          try {
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
          activityUntil = Math.max(activityUntil, Math.min(now() + ACTIVITY_MS, Number(stored.activityUntil) || 0));
        } catch { /* Grouping and rotation remain optional when storage is unavailable. */ }
      })();
      await loaded;
    }

    function persist() {
      const result = saveTail.then(async () => {
        try { await chrome.storage?.session?.set({ [STORAGE_KEY]: { version: 1, groups, activityUntil } }); }
        catch { /* A failed optional cache never stops scanning. */ }
      });
      saveTail = result.catch(() => undefined);
      return result;
    }

    function revoke() {
      epoch += 1;
      lease = null;
      expected = null;
      waiting.clear();
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

    async function separateRetired(groupId, windowId) {
      if ((await config()).groupScannerTabs === false) return;
      const current = await groupContents(groupId, windowId, TITLE, COLOR);
      if (!current) return;
      const retired = [];
      for (const member of current.members) {
        if (registered.has(member.id)) continue;
        const tab = await chrome.tabs.get(member.id);
        if (tab.groupId === groupId && tab.windowId === windowId && safeTab(tab, true)) retired.push(tab);
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
      const latest = await chrome.tabGroups.get(archiveId);
      const members = await chrome.tabs.query({ groupId: archiveId });
      if (latest.shared === true || latest.windowId !== windowId ||
          members.some(tab => registered.has(tab.id) || !safeTab(tab, true))) return;
      if ((await config()).groupScannerTabs === false) return;
      await chrome.tabGroups.update(archiveId, { title: OLD_TITLE, color: 'grey',
        collapsed: !members.some(tab => tab.active) });
    }

    async function syncUnlocked(tabIds) {
      await load();
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
          grouped += live.length;
          await separateRetired(groupId, windowId);
        } catch { failures += 1; }
      }
      await persist();
      return { ok: true, groupingEnabled: true, grouped, failures };
    }

    function sync(tabIds) {
      const result = syncTail.then(() => syncUnlocked(tabIds), () => syncUnlocked(tabIds));
      syncTail = result.catch(() => undefined);
      return result.catch(() => ({ ok: true, groupingEnabled: false, grouped: 0, reason: 'workspace-unavailable' }));
    }

    function expired(expiresAt) {
      return expiresAt !== null && (!Number.isFinite(expiresAt) || expiresAt <= now());
    }

    function invalidRequest(token, expiresAt) {
      if (token !== epoch) return denied('revoked');
      if (expired(expiresAt)) return denied('expired');
      return null;
    }

    async function guard(token, tabId, generation, expiresAt, rotationRequired = true) {
      let invalid = invalidRequest(token, expiresAt);
      if (invalid) return invalid;
      const settings = await config();
      invalid = invalidRequest(token, expiresAt);
      if (invalid) return invalid;
      if (rotationRequired && settings.rotateScannerTabs === false) return denied('disabled', false);
      const isPaused = await paused();
      invalid = invalidRequest(token, expiresAt);
      if (invalid) return invalid;
      if (isPaused) return denied('paused');
      if (activityUntil > now()) return denied('user-active');
      if (!registered.has(tabId)) return denied('not-authorized');
      const isAuthorized = await authorize(tabId, generation);
      invalid = invalidRequest(token, expiresAt);
      if (invalid) return invalid;
      if (!isAuthorized) return denied('not-authorized');
      const stillPaused = await paused();
      invalid = invalidRequest(token, expiresAt);
      if (invalid) return invalid;
      if (stillPaused) return denied('paused');
      return null;
    }

    async function contextFor(tabId) {
      const tab = await chrome.tabs.get(tabId);
      if (!safeTab(tab) || registered.get(tabId)?.windowId !== tab.windowId) return { error: 'not-scanner' };
      if (chrome.windows?.get) {
        const window = await chrome.windows.get(tab.windowId);
        if (window?.focused === false || window?.state === 'minimized') return { error: 'window-unfocused' };
      }
      const [selected] = await chrome.tabs.query({ windowId: tab.windowId, active: true });
      if (!selected || !registered.has(selected.id) || !safeTab(selected)) return { error: 'manual-tab-active' };
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
      focusInFlight = true;
      const token = epoch;
      try {
        await load();
        if (!chrome.tabs?.get || !chrome.tabs?.query || !chrome.tabs?.update || typeof authorize !== 'function') {
          return denied('activation-unavailable', false);
        }
        let rejection = await guard(token, tabId, generation, expiresAt);
        if (rejection) return rejection;
        let context = await contextFor(tabId);
        rejection = await guard(token, tabId, generation, expiresAt);
        if (rejection) return rejection;
        if (context.error) return denied(context.error);
        for (const [id, pending] of waiting) {
          // Activation expires quickly, but the FIFO ticket must outlive a
          // competing scroll lease. Only a fresh guarded request can activate;
          // retaining priority does not grant an expired request permission.
          if (!registered.has(id) || pending.at + QUEUE_TTL_MS < now()) waiting.delete(id);
        }
        if (lease && lease.until > now()) {
          if (lease.tabId === tabId && lease.generation === generation) {
            if (context.selected.id !== tabId) {
              lease = null;
              return denied('selection-changed');
            }
            return { ok: true, granted: true, enabled: true, until: lease.until };
          }
          enqueue(tabId, generation, expiresAt);
          return denied('busy');
        }
        lease = null;
        enqueue(tabId, generation, expiresAt);
        if (waiting.keys().next().value !== tabId) return denied('waiting-turn');
        // Recheck the live selected tab and window immediately before changing
        // selection. The root revokes the epoch on real user interference.
        context = await contextFor(tabId);
        rejection = await guard(token, tabId, generation, expiresAt);
        if (rejection) return rejection;
        if (context.error) return denied(context.error);
        if (context.selected.id !== tabId) {
          expected = { tabId, epoch: token, until: now() + slotMs };
          await chrome.tabs.update(tabId, { active: true });
        }
        rejection = await guard(token, tabId, generation, expiresAt);
        if (rejection) return rejection;
        context = await contextFor(tabId);
        rejection = await guard(token, tabId, generation, expiresAt);
        if (rejection) return rejection;
        if (context.error || context.selected.id !== tabId) return denied(context.error || 'selection-changed');
        waiting.delete(tabId);
        lease = { tabId, generation, until: now() + slotMs };
        return { ok: true, granted: true, enabled: true, until: lease.until };
      } catch {
        expected = null;
        return denied('activation-unavailable', false);
      } finally { focusInFlight = false; }
    }

    // Read-only authority for a native facet click. Never selects a tab or
    // extends its lease; a real user action revokes this check immediately.
    async function allowsInput(tabId, generation) {
      const token = epoch;
      try {
        await load();
        if (await guard(token, tabId, generation, null, false)) return false;
        const context = await contextFor(tabId);
        if (context.error || context.selected.id !== tabId) return false;
        const settings = await config();
        if (settings.rotateScannerTabs !== false &&
            (!lease || lease.tabId !== tabId || lease.generation !== generation || lease.until - now() < 1_500)) return false;
        return !await guard(token, tabId, generation, null, false);
      } catch { return false; }
    }

    function isExpectedActivation(tabId) {
      const matches = expected?.tabId === tabId && expected.epoch === epoch && expected.until >= now();
      if (matches) expected = null;
      return matches;
    }

    return { sync, request, revoke, pause: revoke, noteActivity, isExpectedActivation, allowsInput };
  }

  return { create };
})();
