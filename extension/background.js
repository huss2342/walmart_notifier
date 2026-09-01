// Forwards collected items to the notifier running on this machine, and owns
// the self-refresh schedule.
//
// The refresh lives here rather than in the content script because a content
// script dies with its page: one reload landing on a sign-in redirect or a
// bot-check interstitial would end the refresh loop permanently and silently.
// An alarm in the service worker keeps running regardless of what the tab is
// currently showing.

// The notifier runs on this machine, so the endpoint has a real default and
// most people never need to touch it.
const DEFAULTS = {
  endpoint: 'http://127.0.0.1:8787/ingest',
  token: '',
  refreshMinutes: 0,
  pageDelaySeconds: 5,
  pathPattern: '^/reviews/claim-product'
};

const REFRESH_ALARM = 'refresh';
const BADGE_ALARM = 'clearBadge';
const JITTER = 0.2;
// Chrome clamps alarms to a one-minute floor; asking for less just gets rounded.
const MIN_DELAY_MIN = 1;
// A tab the user is actively working in should not be yanked out from under
// them mid-claim, so retry soon instead of skipping the cycle entirely.
const BUSY_RETRY_MIN = 0.5;

async function config() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

// --- ingest -----------------------------------------------------------------

async function post(items) {
  const { endpoint, token } = await config();
  if (!endpoint) {
    console.warn('Reviewer Item Relay: no endpoint configured; open the options page.');
    return;
  }

  // Change detection lives here, not in the content script: the content script
  // is re-injected on every reload, so its in-memory fingerprint was always
  // empty and every refresh re-POSTed the whole page.
  const fingerprint = items.map((i) => i.item_id).sort().join(',');
  const { lastFingerprint = '' } = await chrome.storage.session.get(['lastFingerprint']);
  if (fingerprint === lastFingerprint) return;

  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['X-Ingest-Token'] = token;

  try {
    const resp = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify({ items })
    });
    if (!resp.ok) {
      console.error('Reviewer Item Relay: ingest returned', resp.status);
      await chrome.storage.local.set({
        lastError: `ingest returned HTTP ${resp.status}`,
        lastErrorAt: Date.now()
      });
      return;
    }
    const summary = await resp.json();
    await chrome.storage.session.set({ lastFingerprint: fingerprint });
    // Recorded so the options page can answer "is this actually running?"
    // without digging through logs -- silent failure is the main risk with a
    // background relay.
    await chrome.storage.local.set({
      lastRelay: Date.now(),
      lastSeen: summary.seen ?? items.length,
      lastNotified: summary.notified ?? 0,
      lastError: ''
    });
    if (summary.notified) {
      chrome.action.setBadgeText({ text: String(summary.notified) });
      chrome.action.setBadgeBackgroundColor({ color: '#0071dc' });
      chrome.alarms.create(BADGE_ALARM, { delayInMinutes: 10 });
    }
  } catch (err) {
    console.error('Reviewer Item Relay: ingest failed', err);
    await chrome.storage.local.set({ lastError: String(err), lastErrorAt: Date.now() });
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === 'items' && Array.isArray(msg.items) && msg.items.length) {
    post(msg.items);
  }
});

// --- self-refresh -----------------------------------------------------------

async function scheduleRefresh(busy = false) {
  const { refreshMinutes } = await config();
  if (!refreshMinutes || refreshMinutes <= 0) {
    await chrome.alarms.clear(REFRESH_ALARM);
    return;
  }
  // +/-20% jitter: staggered reloads beat a metronome, and it costs nothing.
  const jittered = refreshMinutes * (1 + (Math.random() * 2 - 1) * JITTER);
  const delay = busy ? BUSY_RETRY_MIN : Math.max(MIN_DELAY_MIN, jittered);
  chrome.alarms.create(REFRESH_ALARM, { delayInMinutes: delay });
}

async function reviewerTabs() {
  const tabs = await chrome.tabs.query({ url: 'https://www.walmart.com/reviews/*' });
  const { pathPattern } = await config();
  let re;
  try {
    re = new RegExp(pathPattern, 'i');
  } catch {
    re = new RegExp(DEFAULTS.pathPattern, 'i');
  }
  return tabs.filter((tab) => {
    try {
      return re.test(new URL(tab.url).pathname);
    } catch {
      return false;
    }
  });
}

async function isBusy(tabId) {
  try {
    const reply = await chrome.tabs.sendMessage(tabId, { type: 'busy?' });
    return Boolean(reply?.busy);
  } catch {
    // No content script listening (interstitial, error page, still loading).
    // Reload anyway -- that is exactly the state the refresh needs to escape.
    return false;
  }
}

async function refreshTick() {
  const tabs = await reviewerTabs();
  if (!tabs.length) {
    await scheduleRefresh();
    return;
  }
  let anyBusy = false;
  for (const tab of tabs) {
    if (await isBusy(tab.id)) {
      anyBusy = true;
      continue;
    }
    try {
      // Restart the sweep at page 1. Reloading whatever page the walk stopped
      // on would leave earlier pages unread every cycle.
      const url = new URL(tab.url);
      if (url.searchParams.has('page')) {
        url.searchParams.delete('page');
        await chrome.tabs.update(tab.id, { url: url.toString() });
      } else {
        await chrome.tabs.reload(tab.id, { bypassCache: false });
      }
    } catch (err) {
      console.warn('Reviewer Item Relay: could not reload tab', tab.id, err);
    }
  }
  await scheduleRefresh(anyBusy);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === REFRESH_ALARM) refreshTick();
  if (alarm.name === BADGE_ALARM) chrome.action.setBadgeText({ text: '' });
});

// Re-arm on every path that can start or change the schedule.
chrome.runtime.onStartup.addListener(() => scheduleRefresh());
chrome.runtime.onInstalled.addListener(async () => {
  await migrateFromSync();
  scheduleRefresh();
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && ('refreshMinutes' in changes || 'pathPattern' in changes)) {
    scheduleRefresh();
  }
});

// 1.0.x kept settings in chrome.storage.sync, which ships them to Google's
// servers and to every signed-in copy of Chrome. Nothing here needs syncing --
// the endpoint points at this machine.
async function migrateFromSync() {
  const synced = await chrome.storage.sync.get(['endpoint', 'token', 'refreshMinutes']);
  await chrome.storage.sync.remove(['endpoint', 'token', 'refreshMinutes']);
  if (!Object.keys(synced).length) return;
  // An endpoint carried over from the Azure build points at a function app that
  // no longer exists, so it is deliberately not migrated.
  const { refreshMinutes } = synced;
  const existing = await chrome.storage.local.get(['refreshMinutes']);
  if (refreshMinutes && !existing.refreshMinutes) {
    await chrome.storage.local.set({ refreshMinutes });
  }
}
