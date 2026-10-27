// Owns the one reviewer tab that is allowed to relay, posts its items to the
// local notifier, and restarts its sweep on a Chrome alarm.

importScripts('sweep-modes.js', 'runtime-policy.js', 'diagnostics.js', 'relay-settings.js', 'scanner-control.js', 'challenge-evidence.js', 'verification-engine.js', 'verification-driver.js', 'verification-native.js', 'verification-automation.js', 'department-data.js', 'auxiliary-scans.js', 'tab-workspace.js', 'facet-input.js');

const DEFAULTS = {
  endpoint: 'http://127.0.0.1:8787/ingest',
  token: '',
  refreshMinutes: 30,
  pageDelaySeconds: 15,
  sweepMode: 'none',
  auxiliaryDepartments: true,
  auxiliaryKeywords: true,
  auxiliaryBrands: false,
  auxiliaryKeywordQueries: null,
  groupScannerTabs: true,
  rotateScannerTabs: true,
  verificationAssistEnabled: false,
  verificationNativeEnabled: false,
  pathPattern: '^/reviews/claim-product'
};

const REFRESH_ALARM = 'refresh';
const BADGE_ALARM = 'clearBadge';
const STATUS_ALARM = 'relayStatus';
const REVIEWER_HOME = 'https://www.walmart.com/reviews/claim-product';
const AUTO_OPEN_KEY = 'reviewerAutoOpen';
const AUTO_OPEN_COOLDOWN_MS = 120_000;
const COORDINATOR_KEY = 'reviewerCoordinator';
const RESUME_KEY = 'reviewerResume';
const SWEEP_PREFIX = 'sweep:';
const EXCLUDE_PATH = /\/(orders?|purchase-history|track|returns)\b/i;
const JITTER = 0.2;
const MIN_DELAY_MIN = 1;
// Chrome 120+ permits a 30-second alarm. It may still run later than requested.
const BUSY_RETRY_MIN = 0.5;
const PROBE_TIMEOUT_MS = 2_000;
const PROBE_LOADING_RETRY_MS = 500;
const LOADING_LEASE_MS = 45_000;
const FETCH_TIMEOUT_MS = 12_000;
const STALE_SWEEP_MS = 2 * 60_000;
// Keep this in sync with sources.webhook_source.MAX_ITEMS. The strict ingest
// contract rejects larger arrays instead of silently truncating them.
const MAX_INGEST_ITEMS = 200;

// Walmart answers suspected automation by redirecting to /blocked, a "Robot or
// human? Press & Hold" page. That page is Walmart asking this traffic to stop,
// so scanning stops until a usable reviewer page confirms recovery. The optional
// built-in verifier may make a bounded attempt; its input alone cannot clear the
// shared-session pause or authorize cached product cards.
const BOT_CHECK_KEY = 'botCheck';
const BOT_CHECK_PATH = /^\/blocked(\/|$)/i;
const BOT_BACKOFF_BASE_MIN = 60;
const BOT_BACKOFF_MAX_MIN = 24 * 60;
// Checks further apart than this are treated as unrelated; the backoff resets.
const BOT_COUNT_RESET_MS = 24 * 60 * 60_000;
const VERIFICATION_RECHECK_MS = 60_000;
let pendingVerificationEvents = 0;
let verificationPauseFailed = false;

const verificationAutomation = ReviewerVerificationAutomation.create(chrome, {
  readCheck: readBotCheck,
  onRecovered: async () => {
    // Preserve normal shared-session readiness guards; a solver never clears a
    // pause merely because its input finished or one duplicate tab recovered.
    const selected = await selectPrimary();
    await syncBrowserDiagnostics();
    return selected.state.tabId != null && !(await pausedUntil());
  }
});
function triggerVerificationAutomation() {
  verificationAutomation.runPending({ recheck: true }).catch(() => undefined);
}

const auxiliaryScans = ReviewerAuxiliaryScans.create(chrome, {
  home: REVIEWER_HOME, config, paused: pausedUntil, post: enqueuePost,
  diagnostic: recordDiagnostic, probe: probeOnce, onBotCheck: handleBotCheck,
  initialStartDelay: (settings, name) => Math.max(5, Math.min(60, Number(settings.pageDelaySeconds) || 15)) * 1000 *
    (name === 'keywords' && settings.auxiliaryDepartments === true ? 2 : 1)
});

const tabWorkspace = ReviewerTabWorkspace.create(chrome, {
  config, paused: pausedUntil, authorize: authorizedScanner
});

const facetInput = ReviewerFacetInput.create(chrome, { authorize: authorizedFacetInput });

async function authorizedFacetInput(tabId, generation, selection) {
  if (!Number.isInteger(tabId) || !Number.isInteger(generation) ||
      selection?.kind !== 'department' || !ReviewerFacetInput.scope(selection.parentUrl) ||
      !await auxiliaryScans.ownsTab(tabId) || await pausedUntil()) return false;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab || tab.status !== 'complete' || tab.discarded || tab.frozen || tab.pendingUrl ||
      !ReviewerFacetInput.scope(tab.url)) return false;
  const assigned = await auxiliaryScans.handle({ type: 'reviewer:role' }, { tab });
  const expected = assigned?.auxiliary?.selection;
  return assigned?.active === true && assigned.generation === generation &&
    assigned.auxiliary.lane === 'departments' && expected?.kind === 'department' &&
    expected.label === selection.label && expected.parentUrl === selection.parentUrl &&
    await tabWorkspace.allowsInput(tabId, generation);
}

const scannerControl = ReviewerScannerControl.create(chrome, {
  config, checkedEndpoint, snapshot: scannerStatusSnapshot, paused: pausedUntil,
  onCommand: async name => {
    if (await pausedUntil()) return { ok: false, paused: true };
    if (name === 'restartHome') return restartPrimary('backend');
    if (name === 'restartAdditional') return auxiliaryScans.restart();
    return { ok: false };
  }
});

function scannerSettingsForConfig(settings) {
  return validatedScannerSettings({ schemaVersion: 1,
    auxiliaryDepartments: settings.auxiliaryDepartments === true,
    auxiliaryKeywords: settings.auxiliaryKeywords === true,
    auxiliaryBrands: settings.auxiliaryBrands === true,
    auxiliaryKeywordQueries: settings.auxiliaryKeywordQueries == null ? null
      : auxiliaryQueries(settings.auxiliaryKeywordQueries),
    groupScannerTabs: settings.groupScannerTabs !== false,
    rotateScannerTabs: settings.rotateScannerTabs !== false,
    verificationAssistEnabled: settings.verificationAssistEnabled === true,
    verificationNativeEnabled: settings.verificationNativeEnabled === true,
    pathPattern: settings.pathPattern || DEFAULTS.pathPattern });
}

async function scannerStatusSnapshot() {
  const number = value => Number.isSafeInteger(value) && value >= 0 && value <= 1e15 ? value : 0;
  const label = (value, size = 64) => typeof value === 'string' &&
    new RegExp(`^[a-z-]{0,${size}}$`).test(value) ? value : '';
  const errors = [];
  const [settings, state, saved, managed] = await Promise.all([
    config(), readCoordinator(), chrome.storage.local.get([
      'verificationAssistStatus', 'verificationAssistLastResult', 'verificationAssistRetry',
      'scannerSettingsSyncError', 'scannerSettingsSyncedAt', 'scannerCommandsSyncError',
      'scannerCommandReceipts'
    ]), auxiliaryScans.managedTabIds()
  ]);
  let scannerSettings;
  try { scannerSettings = scannerSettingsForConfig(settings); }
  catch { scannerSettings = scannerSettingsForConfig(DEFAULTS); errors.push({ component: 'settings', code: 'invalid-settings' }); }
  let lanes = [];
  try { lanes = (await auxiliaryScans.status()).lanes; }
  catch { errors.push({ component: 'departments', code: 'status-unavailable' },
    { component: 'keywords', code: 'status-unavailable' }); }
  const tabId = state.tabId ?? state.interruptedTabId ?? null;
  let tab;
  try { if (tabId != null) tab = await chrome.tabs.get(tabId); } catch { /* Closed tab is unreachable. */ }
  const probe = state.tabId == null ? null : await probeOnce(state.tabId);
  const check = await readBotCheck();
  const storedSweep = tabId == null ? {} : await chrome.storage.local.get([`sweep:${tabId}`]);
  const sweep = storedSweep[`sweep:${tabId}`] || (await readReviewerResume())?.sweep;
  const phase = await pausedUntil() ? 'waiting-verification'
    : !probe ? 'unreachable' : probe.phase === 'complete' ? 'complete'
      : ['waiting-user', 'ingest-retry', 'passive', 'starting'].includes(probe.phase) ? probe.phase
        : probe.sweeping ? 'scanning' : 'idle';
  let page = 0;
  try { page = number(Number(new URL(tab.url).searchParams.get('page') || 1)); } catch { /* Missing/invalid URL. */ }
  const previous = saved.verificationAssistLastResult || {};
  let verificationTab;
  let verificationWindow;
  try {
    if (check.tabId != null) verificationTab = await chrome.tabs.get(check.tabId);
    if (verificationTab?.windowId != null && chrome.windows?.get) {
      verificationWindow = await chrome.windows.get(verificationTab.windowId);
    }
  } catch { /* Closed or inaccessible verification target. */ }
  const boolean = value => typeof value === 'boolean' ? value : null;
  const receipts = saved.scannerCommandReceipts || {};
  const laneProbes = new Map(await Promise.all(lanes.filter(lane => managed.includes(lane.tabId))
    .map(async lane => {
      const value = await probeOnce(lane.tabId);
      return [lane.lane, value?.generation === lane.generation ? value : null];
    })));
  const receipt = name => {
    const value = receipts[name];
    return value && typeof value.id === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(value.id) &&
      ['started', 'complete', 'failed', 'interrupted', 'paused'].includes(value.outcome)
      ? { id: value.id, outcome: value.outcome, at: number(value.at) } : null;
  };
  if (saved.scannerSettingsSyncError) errors.push({ component: 'settings', code: 'settings-sync-failed' });
  if (saved.scannerCommandsSyncError) errors.push({ component: 'commands', code: 'commands-sync-failed' });
  return { schemaVersion: 1, extensionVersion: chrome.runtime.getManifest?.().version || 'unknown',
    exportedAt: Date.now(), settings: scannerSettings,
    primary: { tabId, generation: number(state.generation), phase, reachable: Boolean(probe?.ready),
      page, totalPages: Number.isSafeInteger(sweep?.total) && sweep.total >= 0 ? sweep.total : null,
      visitedPages: Array.isArray(sweep?.visited) ? sweep.visited.length : 0,
      lastActivityAt: number(probe?.lastActivityAt), nextActionAt: number(probe?.nextActionAt) },
    lanes: ['departments', 'keywords'].map(name => {
      const lane = lanes.find(entry => entry.lane === name);
      const enabled = name === 'departments' ? settings.auxiliaryDepartments === true : settings.auxiliaryKeywords === true;
      return { lane: name, enabled, owned: managed.includes(lane?.tabId),
        runtime: scannerRuntimeProbe(laneProbes.get(name)),
        tabId: Number.isInteger(lane?.tabId) ? lane.tabId : null,
        generation: number(lane?.generation), status: lane?.status || (enabled ? 'ready' : 'disabled'),
        targetLabel: typeof lane?.targetLabel === 'string' ? lane.targetLabel.slice(0, 150) : '',
        ...Object.fromEntries(['completedTargets', 'totalTargets', 'pages', 'failures', 'truncatedTargets',
          'coverageLimitedTargets', 'nextAt', 'round'].map(key => [key, number(lane?.[key])])),
        lastErrorCode: lane?.lastError ? 'target-error' : errors.some(error => error.component === name)
          ? 'status-unavailable' : 'none', lastFailureCode: scannerFailureCode(lane?.lastError) };
    }),
    verification: { pausedUntil: number(check.pausedUntil), count: number(check.count), lastAt: number(check.lastAt),
      assistEnabled: settings.verificationAssistEnabled === true, nativeEnabled: settings.verificationNativeEnabled === true,
      statusCode: label(saved.verificationAssistStatus?.code) || 'unknown',
      attempt: Math.min(10, number(previous.attempt)), attemptLimit: 10,
      lastOutcome: label(previous.outcome), errorStage: label(previous.errorStage, 32), errorReason: label(previous.errorReason),
      pressSent: typeof previous.pressSent === 'boolean' ? previous.pressSent : null,
      cleanupConfirmed: typeof previous.cleanupConfirmed === 'boolean' ? previous.cleanupConfirmed : null,
      tabActive: boolean(verificationTab?.active), tabFrozen: boolean(verificationTab?.frozen),
      windowFocused: boolean(verificationWindow?.focused),
      retryAt: number(saved.verificationAssistRetry?.nextAt) },
    sync: { settingsOk: !saved.scannerSettingsSyncError,
      settingsSyncedAt: number(saved.scannerSettingsSyncedAt), commandsOk: !saved.scannerCommandsSyncError },
    commands: { restartHome: receipt('restartHome'), restartAdditional: receipt('restartAdditional') }, errors };
}

async function authorizedScanner(tabId, generation) {
  if (!Number.isInteger(tabId) || !Number.isInteger(generation)) return false;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab) return false;
  if (await auxiliaryScans.ownsTab(tabId)) {
    const role = await auxiliaryScans.handle({ type: 'reviewer:role' }, { tab });
    return role?.active === true && role.generation === generation;
  }
  return (await authorizedRole({ tab }, generation)).authorized;
}

async function syncScannerWorkspace() {
  const state = await readCoordinator();
  await tabWorkspace.sync([state.tabId, state.interruptedTabId,
    ...await auxiliaryScans.managedTabIds()].filter(Number.isInteger));
}

async function config() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

const delay = (ms) => new Promise((done) => setTimeout(done, ms));

async function withTimeout(promise, ms, message) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(message)), ms);
      })
    ]);
  } finally {
    clearTimeout(timer);
  }
}

// --- bot-check pause ------------------------------------------------------

function isWalmartUrl(raw) {
  try {
    const url = new URL(raw);
    return url.protocol === 'https:' && url.hostname === 'www.walmart.com' &&
      !url.username && !url.password && !url.port;
  } catch {
    return false;
  }
}

function isBotCheckUrl(raw) {
  return isWalmartUrl(raw) && BOT_CHECK_PATH.test(new URL(raw).pathname);
}

async function readBotCheck() {
  const { [BOT_CHECK_KEY]: value } = await chrome.storage.local.get([BOT_CHECK_KEY]);
  return {
    pausedUntil: Number(value?.pausedUntil) || 0,
    count: Number.isInteger(value?.count) ? value.count : 0,
    lastAt: Number(value?.lastAt) || 0,
    tabId: Number.isInteger(value?.tabId) ? value.tabId : null,
    challengedTabIds: Array.isArray(value?.challengedTabIds)
      ? value.challengedTabIds.filter(Number.isInteger)
      : Number.isInteger(value?.tabId) ? [value.tabId] : [],
    knownTabIds: Array.isArray(value?.knownTabIds) ? value.knownTabIds : null,
    verifiedAt: Number(value?.verifiedAt) || 0
  };
}

/** Next pause recheck, or 0 only when scanning is allowed. */
async function pausedUntil() {
  if (pendingVerificationEvents || verificationPauseFailed) return Date.now() + VERIFICATION_RECHECK_MS;
  const { pausedUntil: until } = await readBotCheck();
  // Detection closes authorization before queued storage/coordinator mutations.
  // A cooldown is a recheck deadline, never evidence that verification passed.
  if (pendingVerificationEvents || verificationPauseFailed || until > 0) {
    return Math.max(until, Date.now() + VERIFICATION_RECHECK_MS);
  }
  return 0;
}

let toolbarUpdateTail = Promise.resolve();
let verificationBadgeShown = false;
function enqueueToolbarUpdate(task) {
  const run = toolbarUpdateTail.then(task).catch(() => {
    // A toolbar failure must never prevent the shared-session pause/resume.
    console.warn('Reviewer Item Relay: could not update toolbar status');
  });
  toolbarUpdateTail = run;
  return run;
}
async function writeVerificationBadge(active) {
  await chrome.action.setBadgeText({ text: active ? '!' : '' });
  await chrome.action.setTitle({ title: active
    ? 'Reviewer Item Relay: verification detected — needs attention'
    : 'Reviewer Item Relay' });
  if (active) await chrome.action.setBadgeBackgroundColor({ color: '#b91c1c' });
  verificationBadgeShown = active;
}
function syncVerificationBadge(clearWhenReady = false) {
  return enqueueToolbarUpdate(async () => {
    const active = (await pausedUntil()) > 0;
    if (active || clearWhenReady || verificationBadgeShown) await writeVerificationBadge(active);
  });
}
function showDeliveryBadge(count) {
  return enqueueToolbarUpdate(async () => {
    // A delivery can finish after its document's lease was revoked. Recheck
    // inside the shared toolbar queue so it cannot replace a challenge warning.
    if (await pausedUntil()) return writeVerificationBadge(true);
    await chrome.action.setBadgeText({ text: String(count) });
    await chrome.action.setTitle({ title: 'Reviewer Item Relay' });
    await chrome.action.setBadgeBackgroundColor({ color: '#0071dc' });
    verificationBadgeShown = false;
    await chrome.alarms.create(BADGE_ALARM, { delayInMinutes: 10 });
  });
}

// Probe only the challenged tab or a newly opened reviewer tab. An old passive
// fallback may still show cached items while the active tab awaits verification.
function resumeAfterVerification(tabs) {
  const run = botCheckTail.then(async () => {
    const current = await readBotCheck();
    if (current.pausedUntil <= 0 || pendingVerificationEvents || verificationPauseFailed) return false;
    if (current.knownTabIds === null) {
      // Older versions did not record fallbacks. Snapshot them before deciding
      // whether a tab was reopened, so upgrading cannot clear an unsolved check.
      current.knownTabIds = tabs.map((tab) => tab.id);
      await chrome.storage.local.set({ [BOT_CHECK_KEY]: current });
    }
    const replies = new Map();
    const re = await pathRegex();
    // Discover checks that have not yet delivered their observer message.
    // Do this before trusting any recovered tab or an old passive catalogue.
    const openWalmartTabs = (await chrome.tabs.query({ url: 'https://www.walmart.com/*' }))
      .filter(tab => isWalmartUrl(tab.url));
    const newlyChallenged = (await Promise.all(openWalmartTabs
      .filter(tab => !current.challengedTabIds.includes(tab.id))
      .map(async tab => {
        if (isBotCheckUrl(tab.url)) return tab.id;
        if (eligibleUrl(tab.url, re)) {
          const reply = await probeOnce(tab.id);
          replies.set(tab.id, reply);
          return reply?.botCheck === true ? tab.id : null;
        }
        try {
          const reply = await chrome.tabs.sendMessage(tab.id, { type: 'reviewer:challenge-probe' });
          return reply?.challenge === true ? tab.id : null;
        } catch { return null; }
      }))).filter(Number.isInteger);
    if (newlyChallenged.length) {
      await chrome.storage.local.set({ [BOT_CHECK_KEY]: { ...current,
        challengedTabIds: [...current.challengedTabIds, ...newlyChallenged] } });
      return false;
    }
    let recoveredOtherPage = false;
    for (const id of current.challengedTabIds) {
      let tab;
      try { tab = await chrome.tabs.get(id); } catch { continue; } // Closed tab.
      if (!tab) continue;
      if (isBotCheckUrl(tab.url)) return false;
      if (!eligibleUrl(tab.url, re)) {
        if (!isWalmartUrl(tab.url)) continue;
        // Non-reviewer Walmart pages have only the passive challenge watcher.
        // Require a fresh answer; an unreachable document is not a clear check.
        let reply;
        try { reply = await chrome.tabs.sendMessage(id, { type: 'reviewer:challenge-probe' }); }
        catch { return false; }
        if (tab.status !== 'complete' || reply?.challenge !== false) return false;
        recoveredOtherPage = true;
        continue;
      }
      if (tab.status !== 'complete') return false;
      const reply = await probeOnce(id);
      replies.set(id, reply);
      // A usable new tab does not clear the challenge still visible in an old
      // tab. Otherwise selection immediately counts that same outage again.
      if (reply?.reviewerReady !== true || reply?.botCheck) return false;
    }
    for (const tab of tabs) {
      if (tab.status !== 'complete' ||
          (!recoveredOtherPage && !current.challengedTabIds.includes(tab.id) &&
           current.knownTabIds.includes(tab.id))) continue;
      const reply = replies.has(tab.id) ? replies.get(tab.id) : await probeOnce(tab.id);
      if (reply?.reviewerReady !== true || reply?.botCheck) continue;
      if (pendingVerificationEvents || verificationPauseFailed) return false;
      await chrome.storage.local.set({
        [BOT_CHECK_KEY]: { ...current, pausedUntil: 0, verifiedAt: Date.now() },
        lastError: '', lastErrorAt: 0
      });
      await syncVerificationBadge(true);
      // A pre-challenge completion must not postpone this fresh scan.
      await chrome.storage.local.remove(['lastSweepStartedAt', 'lastSweepDone', 'lastSweepPages']);
      await recordDiagnostic('verification-resumed', { tabId: tab.id, count: current.count });
      await scheduleRefresh();
      return true;
    }
    return false;
  });
  botCheckTail = run.catch(() => undefined);
  return run;
}

function saveVerificationAlert(outage, sent) {
  return coordinatorMutation(async () => {
    const current = await readBotCheck();
    if (current.lastAt !== outage.lastAt || current.count !== outage.count) return;
    await chrome.storage.local.set({
      lastVerificationAlert: { at: Date.now(), status: sent ? 'sent' : 'failed' },
      lastVerificationAlertError: sent ? '' :
        'Verification was detected, but the notifier alert was not confirmed. Check the local notifier connection.'
    });
  });
}

async function notifyBotCheck(minutes, outage) {
  try {
    const { endpoint: raw, token } = await config();
    const url = new URL('/bot-check', checkedEndpoint(raw)).toString();
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-Ingest-Token'] = token;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({ paused_minutes: minutes }),
        signal: controller.signal
      });
      if (!response.ok) throw new Error('Verification alert was not accepted.');
      await saveVerificationAlert(outage, true);
    } finally {
      clearTimeout(timer);
    }
  } catch {
    // Do not retry an ambiguous alert: the notifier may already have sent it.
    await saveVerificationAlert(outage, false).catch(() => {});
    console.warn('Reviewer Item Relay: bot-check alert was not confirmed');
  }
}

let botCheckTail = Promise.resolve();
let coordinatorMutationTail = Promise.resolve();
function coordinatorMutation(task) {
  const run = coordinatorMutationTail.then(task, task);
  coordinatorMutationTail = run.catch(() => undefined);
  return run;
}
function handleBotCheck(tabId, reason = 'redirect') {
  pendingVerificationEvents += 1;
  tabWorkspace.pause();
  // Tell renderers to cancel timers/scrolling immediately, independently of
  // durable checkpoint work and the primary/auxiliary mutation queues.
  chrome.tabs.query({ url: 'https://www.walmart.com/*' }).then(tabs =>
    Promise.allSettled(tabs.filter(tab => {
      try { return new URL(tab.url).origin === 'https://www.walmart.com'; }
      catch { return false; }
    }).map(tab => chrome.tabs.sendMessage(tab.id, { type: 'reviewer:pause' })))
  ).catch(() => undefined);
  const run = botCheckTail.then(() => handleBotCheckUnlocked(tabId, reason),
                                () => handleBotCheckUnlocked(tabId, reason));
  botCheckTail = run.catch(() => undefined);
  // Keep the Chrome event/message alive for best-effort evidence and alerting,
  // but never hold the session mutation queue while waiting on either service.
  return run.then(result => {
    pendingVerificationEvents -= 1;
    verificationPauseFailed = false;
    return result;
  }, error => {
    pendingVerificationEvents -= 1;
    verificationPauseFailed = true;
    throw error;
  }).then(async result => {
    triggerVerificationAutomation();
    await captureVerificationEvidence(tabId);
    if (!result.duplicate) await notifyBotCheck(result.minutes, result);
    return result;
  });
}

async function handleBotCheckUnlocked(tabId, reason) {
  const result = await coordinatorMutation(() => pauseForBotCheck(tabId));
  await syncVerificationBadge();
  if (!result.duplicate) {
    await recordDiagnostic('challenge', { tabId, reason, count: result.count });
  }
  return result;
}

async function pauseForBotCheck(tabId) {
  tabWorkspace.pause();
  const now = Date.now();
  const previous = await readBotCheck();
  if (previous.pausedUntil > 0) {
    if (!previous.challengedTabIds.includes(tabId)) {
      await chrome.storage.local.set({ [BOT_CHECK_KEY]: {
        ...previous, challengedTabIds: [...previous.challengedTabIds, tabId]
      } });
    }
    await auxiliaryScans.pause();
    return { ok: true, duplicate: true, pausedUntil: previous.pausedUntil };
  }

  const count = now - previous.lastAt > BOT_COUNT_RESET_MS ? 1 : previous.count + 1;
  const minutes = Math.min(BOT_BACKOFF_BASE_MIN * 2 ** (count - 1), BOT_BACKOFF_MAX_MIN);
  const until = now + minutes * 60_000;
  const knownTabIds = (await reviewerTabs({ includeAuxiliary: true })).map((tab) => tab.id);
  await chrome.storage.local.set({
    [BOT_CHECK_KEY]: {
      pausedUntil: until, count, lastAt: now, tabId, knownTabIds,
      challengedTabIds: [tabId]
    },
    lastError:
      `Walmart showed a bot check (${count} in the last 24h). Solve it by hand, ` +
      `and close any other tabs still showing the check. Monitoring resumes ` +
      `automatically when the reviewer page is ready. ` +
      `Scanning stays paused until recovery is confirmed.`,
    lastErrorAt: now
  });

  await auxiliaryScans.pause();

  // Keep the last acknowledged checkpoint separately from revoked leases. A
  // redirect may already have replaced the primary URL, so use the last safe
  // worker-validated record rather than the challenge's return URL.
  const resume = await readReviewerResume();
  if (resume) await chrome.storage.local.set({ [RESUME_KEY]: { ...resume, interruptedAt: now } });

  // Revoke every lease while the check is unresolved. Once the page becomes
  // usable, restore progress under a fresh generation at the saved scan speed.
  const state = await readCoordinator();
  const stood = await writeCoordinator({
    tabId: null, generation: state.generation + 1, selectedAt: 0,
    interruptedTabId: state.tabId ?? state.interruptedTabId
  });
  const stored = await chrome.storage.local.get(null);
  const sweepKeys = Object.keys(stored)
    .filter((key) => key === 'sweep' || key.startsWith(SWEEP_PREFIX));
  if (sweepKeys.length) await chrome.storage.local.remove(sweepKeys);
  await broadcastRoles(await reviewerTabs(), stood);

  await chrome.alarms.create(REFRESH_ALARM, { when: until });
  return { ok: true, count, lastAt: now, minutes, pausedUntil: until };
}

// --- ingest -----------------------------------------------------------------

function checkedEndpoint(raw) {
  let endpoint;
  try {
    endpoint = new URL(raw);
  } catch {
    throw new Error(`The ingest endpoint is not a valid URL: ${raw || '(blank)'}`);
  }
  const loopback = endpoint.hostname === '127.0.0.1' || endpoint.hostname === 'localhost';
  if (endpoint.protocol !== 'http:' || !loopback) {
    throw new Error(
      'The ingest endpoint must use http://127.0.0.1 or http://localhost; ' +
      'the extension is intentionally permitted to send only to this machine.'
    );
  }
  if (endpoint.username || endpoint.password ||
      endpoint.pathname.replace(/\/+$/, '') !== '/ingest' ||
      endpoint.search || endpoint.hash) {
    throw new Error(
      'The ingest endpoint must end exactly in /ingest and must not contain ' +
      'credentials, a query string, or a fragment.'
    );
  }
  return endpoint.toString();
}

async function recordIngestFailure(message) {
  const { ingestConsecutiveFailures = 0 } =
    await chrome.storage.local.get(['ingestConsecutiveFailures']);
  const now = Date.now();
  await chrome.storage.local.set({
    lastAttempt: now,
    lastError: message,
    lastErrorAt: now,
    ingestConsecutiveFailures: ingestConsecutiveFailures + 1
  });
}

function networkFailureMessage(endpoint, err) {
  if (err?.name === 'AbortError') {
    return `The notifier at ${endpoint} did not respond within ${FETCH_TIMEOUT_MS / 1000} seconds. ` +
      'Keep the reviewer-item-notifier container running in Docker Desktop ' +
      '(or leave run.ps1 running); ' +
      'this page will retry automatically.';
  }
  return `Could not reach the notifier at ${endpoint}. Start the reviewer-item-notifier container in ` +
    'Docker Desktop (or run run.ps1), open the extension ' +
    'Options, click Check connection, and allow local-network access if Chrome asks. ' +
    'This page will retry automatically. ' +
    `(${err?.message || String(err)})`;
}

function batchLabel(index, count) {
  return count > 1 ? `Batch ${index + 1} of ${count}: ` : '';
}

async function postBatch(endpoint, headers, items, index, count) {
  const label = batchLabel(index, count);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  let resp;
  let text;
  try {
    resp = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify({ items }),
      // The validator permits only literal loopback hosts. Chrome therefore
      // knows the destination address space without an annotation. Explicitly
      // labelling 127.0.0.1 as `local` is wrong under current LNA naming and
      // Chrome rejects it because the resolved address space is `loopback`.
      signal: controller.signal
    });
    text = await resp.text();
  } catch (err) {
    const message = `${label}${networkFailureMessage(endpoint, err)}`;
    console.error('Reviewer Item Relay: ingest failed', err);
    await recordIngestFailure(message);
    return { ok: false, retryable: true, error: message };
  } finally {
    clearTimeout(timeout);
  }

  let summary = null;
  try {
    summary = text ? JSON.parse(text) : null;
  } catch {
    // Report a concise error below instead of persisting a long HTML response.
  }

  if (!resp.ok) {
    const detail = summary?.error || text.slice(0, 200) || `HTTP ${resp.status}`;
    const hint = resp.status === 403
      ? ' Check that the extension token matches INGEST_TOKEN.'
      : '';
    const message = `${label}Ingest returned HTTP ${resp.status}: ${detail}.${hint}`;
    console.error('Reviewer Item Relay:', message);
    await recordIngestFailure(message);
    return {
      ok: false,
      retryable: resp.status === 408 || resp.status === 429 || resp.status >= 500,
      error: message
    };
  }

  if (!summary || typeof summary !== 'object' || Array.isArray(summary)) {
    const message =
      `${label}The notifier returned a successful response that was not valid JSON.`;
    await recordIngestFailure(message);
    return { ok: false, retryable: true, error: message };
  }
  if (!Number.isInteger(summary.seen) || summary.seen !== items.length) {
    const message =
      `${label}The notifier acknowledged ${summary.seen ?? 'an unknown number of'} of ` +
      `${items.length} items; this page will retry instead of advancing.`;
    await recordIngestFailure(message);
    return { ok: false, retryable: true, error: message };
  }
  return { ok: true, summary };
}

const SUMMARY_COUNTS = [
  'new', 'filtered', 'matched', 'notified', 'queued', 'failed', 'pending', 'seeded',
  'value_known', 'value_unknown'
];

function numericSummaryValue(summary, key, fallback = 0) {
  if (summary[key] === null || summary[key] === undefined) return fallback;
  const value = Number(summary[key]);
  return Number.isFinite(value) ? value : fallback;
}

function mergeSummary(total, next) {
  const fresh = numericSummaryValue(next, 'new');
  total.seen += next.seen;
  for (const key of SUMMARY_COUNTS) total[key] += numericSummaryValue(next, key);
  total.duplicates += numericSummaryValue(
    next, 'duplicates', Math.max(0, next.seen - fresh)
  );

  for (const [key, choose] of [
    ['min_value_usd', Math.min],
    ['max_value_usd', Math.max]
  ]) {
    if (next[key] === null || next[key] === undefined) continue;
    const value = Number(next[key]);
    if (!Number.isFinite(value)) continue;
    total[key] = total[key] === null ? value : choose(total[key], value);
  }
  return total;
}

function emptySummary() {
  const summary = {
    seen: 0,
    duplicates: 0,
    min_value_usd: null,
    max_value_usd: null
  };
  for (const key of SUMMARY_COUNTS) summary[key] = 0;
  return summary;
}

async function post(items, page, canSend = async () => !(await pausedUntil())) {
  const { endpoint: rawEndpoint, token } = await config();
  let endpoint;
  try {
    endpoint = checkedEndpoint(rawEndpoint);
  } catch (err) {
    const message = err.message || String(err);
    await recordIngestFailure(message);
    return { ok: false, retryable: false, error: message };
  }

  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['X-Ingest-Token'] = token;

  const summary = emptySummary();
  const batchCount = Math.max(1, Math.ceil(items.length / MAX_INGEST_ITEMS));
  for (let index = 0; index < batchCount; index += 1) {
    if (!await canSend()) return { ok: false, retryable: false, reason: 'inactive' };
    const start = index * MAX_INGEST_ITEMS;
    const batch = items.slice(start, start + MAX_INGEST_ITEMS);
    // Deliberately wait for each acknowledgement before sending the next
    // batch. If a later request fails, the content script retries the complete
    // page; successful earlier batches are harmless duplicates on that retry.
    const result = await postBatch(endpoint, headers, batch, index, batchCount);
    if (!result.ok) return result;
    mergeSummary(summary, result.summary);
  }

  if (!await canSend()) return { ok: false, retryable: false, reason: 'inactive' };

  const now = Date.now();
  await chrome.storage.local.set({
    lastAttempt: now,
    lastRelay: now,
    lastSeen: summary.seen,
    lastNew: summary.new ?? 0,
    lastDuplicates: summary.duplicates ?? Math.max(0, summary.seen - (summary.new ?? 0)),
    lastFiltered: summary.filtered ?? 0,
    lastMatched: summary.matched ?? 0,
    lastNotified: summary.notified ?? 0,
    lastQueued: summary.queued ?? 0,
    lastFailed: summary.failed ?? 0,
    lastPending: summary.pending ?? 0,
    lastSeeded: summary.seeded ?? 0,
    lastValueKnown: summary.value_known ?? 0,
    lastValueUnknown: summary.value_unknown ?? 0,
    lastMinValue: summary.min_value_usd ?? null,
    lastMaxValue: summary.max_value_usd ?? null,
    lastPage: page ?? 1,
    lastError: '',
    ingestConsecutiveFailures: 0
  });

  let deliveryError = null;
  const pending = Number(summary.pending) || 0;
  const failed = Number(summary.failed) || 0;
  if (failed || pending) {
    const parts = [];
    if (failed) parts.push(`${failed} failed ${failed === 1 ? 'delivery' : 'deliveries'}`);
    if (pending) parts.push(`${pending} delivery attempt${pending === 1 ? ' is' : 's are'} still pending`);
    deliveryError = `${parts.join(' and ')}; this page will retry.`;
    await chrome.storage.local.set({
      lastError: deliveryError,
      lastErrorAt: Date.now()
    });
  }

  if (summary.notified) {
    await showDeliveryBadge(summary.notified);
  }
  if (deliveryError) {
    return { ok: false, retryable: true, error: deliveryError, summary };
  }
  return { ok: true, summary };
}

// Mutation-heavy pages can produce another report before an earlier response
// reaches the content script. Serializing makes status writes deterministic.
let ingestTail = Promise.resolve();
function enqueuePost(items, page, canSend) {
  const result = ingestTail.then(
    () => post(items, page, canSend), () => post(items, page, canSend)
  );
  ingestTail = result.catch(() => undefined);
  return result;
}

// --- reviewer-tab coordinator -----------------------------------------------

async function readCoordinator() {
  const stored = await chrome.storage.session.get([COORDINATOR_KEY]);
  const value = stored[COORDINATOR_KEY];
  if (!value || typeof value !== 'object') {
    return { tabId: null, generation: 0, selectedAt: 0 };
  }
  return {
    tabId: Number.isInteger(value.tabId) ? value.tabId : null,
    generation: Number.isInteger(value.generation) ? value.generation : 0,
    selectedAt: Number(value.selectedAt) || 0,
    ...(Number.isInteger(value.interruptedTabId) ? { interruptedTabId: value.interruptedTabId } : {}),
    paginationFallback: value.paginationFallback === true
  };
}

async function writeCoordinator(value) {
  await chrome.storage.session.set({ [COORDINATOR_KEY]: value });
  return value;
}

async function pathRegex() {
  const { pathPattern } = await config();
  try {
    return new RegExp(pathPattern, 'i');
  } catch {
    return new RegExp(DEFAULTS.pathPattern, 'i');
  }
}

function eligibleUrl(rawUrl, re) {
  try {
    const url = new URL(rawUrl);
    return url.protocol === 'https:' && url.hostname === 'www.walmart.com' &&
      !EXCLUDE_PATH.test(url.pathname) && re.test(url.pathname);
  } catch {
    return false;
  }
}

function monitoringTab(tab, settings, state = {}, resume = null) {
  let re;
  try { re = new RegExp(settings.pathPattern || DEFAULTS.pathPattern, 'i'); }
  catch { re = new RegExp(DEFAULTS.pathPattern, 'i'); }
  const fallback = ((state.paginationFallback && state.tabId === tab.id) ||
    (state.tabId == null && resume?.url === tab.url && resume.sweep?.paginationFallback)) &&
    normalizeSweepMode(settings.sweepMode) === 'none';
  return [tab.url, tab.pendingUrl].filter(Boolean).every(url =>
    eligibleUrl(url, re) && scanScopeAllowed(url, settings.sweepMode, fallback));
}

function resumeSweepForSettings(sweep, settings) {
  if (!Array.isArray(sweep?.visited)) return null;
  if (Number.isFinite(sweep.lastProgressAt) && sweep.lastProgressAt > 0 &&
      Date.now() - sweep.lastProgressAt > 86400_000) return null;
  const mode = normalizeSweepMode(settings.sweepMode);
  const sweepMode = sweepRecordMode(sweep);
  return sweepMode === mode || (mode === 'none' && sweepMode === 'high-value' &&
    sweep.paginationFallback === true) ? sweep : null;
}

// Unlike the session lease and tab IDs, this checkpoint survives Chrome exits.
// Only the primary can write it, within the configured monitoring scope.
async function readReviewerResume(settings = null) {
  settings ||= await config();
  const value = (await chrome.storage.local.get([RESUME_KEY]))[RESUME_KEY];
  let re;
  try { re = new RegExp(settings.pathPattern, 'i'); }
  catch { re = new RegExp(DEFAULTS.pathPattern, 'i'); }
  if (!value || value.sweepMode !== normalizeSweepMode(settings.sweepMode) ||
      value.pathPattern !== settings.pathPattern || !eligibleUrl(value.url, re)) return null;
  const url = new URL(value.url);
  const sweep = resumeSweepForSettings(value.sweep, settings);
  if (url.username || url.password || url.port ||
      !scanScopeAllowed(value.url, settings.sweepMode, sweep?.paginationFallback === true)) return null;
  return { ...value, sweep };
}

// Call under coordinatorMutation, before broadcasting a lease or navigating.
async function saveReviewerResume(tab, state) {
  const settings = await config();
  if (!tab || state.tabId !== tab.id || await pausedUntil() ||
      (tab.pendingUrl && tab.pendingUrl !== tab.url) ||
      !monitoringTab(tab, settings, state)) return false;
  const url = new URL(tab.url);
  if (url.username || url.password || url.port) return false;
  const key = `${SWEEP_PREFIX}${tab.id}`;
  const stored = await chrome.storage.local.get([key, RESUME_KEY]);
  const sweep = resumeSweepForSettings(stored[key], settings);
  const value = { url: tab.url, tabId: tab.id, generation: state.generation,
    sweepMode: normalizeSweepMode(settings.sweepMode), pathPattern: settings.pathPattern,
    sweep: sweep?.generation === state.generation && Array.isArray(sweep.visited) ? sweep : null };
  if (JSON.stringify(stored[RESUME_KEY]) !== JSON.stringify(value)) {
    await chrome.storage.local.set({ [RESUME_KEY]: value });
  }
  return true;
}

function resumeExistingReviewerTab() {
  return coordinatorMutation(async () => {
    const settings = await config();
    if (!(Number(settings.refreshMinutes) > 0) || await pausedUntil()) return false;
    const [state, resume, tabs] = await Promise.all([
      readCoordinator(), readReviewerResume(settings), chrome.tabs.query({})
    ]);
    if (state.tabId != null || !resume || tabs.some(tab =>
      [tab.url, tab.pendingUrl].some(url => url === resume.url || isBotCheckUrl(url)))) return false;
    const tab = tabs.find(tab => tab.url && (!tab.pendingUrl || tab.pendingUrl === tab.url) &&
      monitoringTab(tab, settings));
    if (!tab) return false;
    const [live, latest, currentSettings, paused] = await Promise.all([
      chrome.tabs.get(tab.id), readCoordinator(), config(), pausedUntil()
    ]);
    if (paused || !(Number(currentSettings.refreshMinutes) > 0) || latest.tabId != null ||
        live?.url !== tab.url || (live.pendingUrl && live.pendingUrl !== tab.url) ||
        (await readReviewerResume(currentSettings))?.url !== resume.url) return false;
    // The host supervisor may already have opened the catalogue home. Reuse
    // that monitoring tab; leave manually filtered tabs to the user.
    await chrome.tabs.update(tab.id, { url: resume.url });
    return true;
  });
}

function handleCheckpoint(msg, sender) {
  if (sender?.tab) {
    return auxiliaryScans.ownsTab(sender.tab.id).then(managed => managed
      ? auxiliaryScans.handle({ ...msg, type: 'reviewer:checkpoint' }, sender)
      : handlePrimaryCheckpoint(msg, sender));
  }
  return handlePrimaryCheckpoint(msg, sender);
}

function handlePrimaryCheckpoint(msg, sender) {
  return coordinatorMutation(async () => {
    const role = await authorizedRole(sender, msg.generation);
    if (!role.authorized) return { ok: false };
    const tab = await chrome.tabs.get(role.tabId);
    return { ok: await saveReviewerResume(tab, role.state) };
  });
}

async function reviewerTabs({ includeAuxiliary = false } = {}) {
  const [tabs, re, managed] = await Promise.all([
    chrome.tabs.query({ url: 'https://www.walmart.com/reviews/*' }),
    pathRegex(), auxiliaryScans.managedTabIds()
  ]);
  return tabs
    .filter((tab) => Number.isInteger(tab.id) && eligibleUrl(tab.url, re) &&
      (includeAuxiliary || !managed.includes(tab.id)))
    // Chrome does not promise a useful query order. Window/tab position makes
    // "the first tab" stable and easy to reason about.
    .sort((a, b) =>
      (a.windowId - b.windowId) || (a.index - b.index) || (a.id - b.id));
}

let autoOpenInFlight = null;
function ensureReviewerTab() {
  if (autoOpenInFlight) return autoOpenInFlight;
  autoOpenInFlight = (async () => {
    if (!(Number((await config()).refreshMinutes) > 0) || await pausedUntil()) return false;
    const re = await pathRegex();
    if (!eligibleUrl(REVIEWER_HOME, re)) return false;
    const resume = await readReviewerResume();
    if (resume && await resumeExistingReviewerTab()) return true;
    const hasReviewerOrChallenge = async () => {
      // A restored tab can have an empty committed URL while pendingUrl is
      // already the reviewer page. Host permissions expose only matching URLs.
      const tabs = await chrome.tabs.query({});
      const settings = await config();
      const [state, currentResume] = await Promise.all([readCoordinator(), readReviewerResume(settings)]);
      const managed = await auxiliaryScans.managedTabIds();
      return tabs.some(tab => !managed.includes(tab.id) && [tab.url, tab.pendingUrl].some(url =>
        (url && monitoringTab({ id: tab.id, url }, settings, state, currentResume)) || isBotCheckUrl(url)));
    };
    if (await hasReviewerOrChallenge()) return false;
    const stored = (await chrome.storage.session.get([AUTO_OPEN_KEY]))[AUTO_OPEN_KEY];
    if (Number(stored?.lastAttemptAt) > Date.now() - AUTO_OPEN_COOLDOWN_MS) return false;
    await chrome.storage.session.set({ [AUTO_OPEN_KEY]: { lastAttemptAt: Date.now() } });
    // A restored browser window or challenge can arrive during the storage read.
    if (await pausedUntil() || !(Number((await config()).refreshMinutes) > 0) ||
        await hasReviewerOrChallenge()) return false;
    const target = (await readReviewerResume())?.url || REVIEWER_HOME;
    if (!eligibleUrl(target, await pathRegex())) return false;
    if (await pausedUntil()) return false;
    try {
      let opened;
      try {
        opened = await chrome.tabs.create({ url: target, active: false });
      } catch {
        // Chrome can keep a background worker alive without an open window.
        if (await pausedUntil()) return false;
        const window = await chrome.windows.create({ url: target, focused: false });
        opened = window.tabs?.[0];
      }
      await recordDiagnostic('tab-opened', { tabId: opened?.id });
      return true;
    } catch {
      await chrome.storage.local.set({
        lastError: 'Could not reopen the reviewer page. Open it manually in Chrome.',
        lastErrorAt: Date.now()
      });
      return false;
    }
  })().finally(() => { autoOpenInFlight = null; });
  return autoOpenInFlight;
}

async function probeOnce(tabId) {
  try {
    const reply = await withTimeout(
      chrome.tabs.sendMessage(tabId, { type: 'reviewer:probe' }),
      PROBE_TIMEOUT_MS,
      'reviewer tab did not answer'
    );
    return reply?.ready ? reply : null;
  } catch {
    return null;
  }
}

async function probeTab(tab) {
  let reply = await probeOnce(tab.id);
  if (!reply) {
    // Do not let a secondary win merely because its content script happened to
    // initialize a few milliseconds before the preferred tab's script. Chrome
    // can report `complete` just before a document-idle script registers.
    await delay(PROBE_LOADING_RETRY_MS);
    reply = await probeOnce(tab.id);
  }
  return reply;
}

function roleFor(tabId, state) {
  return {
    active: tabId === state.tabId,
    tabId,
    primaryTabId: state.tabId,
    generation: state.generation
  };
}

async function sendRole(tab, state) {
  try {
    await chrome.tabs.sendMessage(tab.id, {
      type: 'reviewer:set-role',
      ...roleFor(tab.id, state)
    });
  } catch {
    // A loading/discarded tab will ask for its role when its script starts.
  }
}

async function broadcastRoles(tabs, state) {
  await Promise.allSettled(tabs.map((tab) => sendRole(tab, state)));
}

let selectionTail = Promise.resolve();

function selectPrimary(options = {}) {
  const run = selectionTail.then(
    () => selectPrimaryUnlocked(options),
    () => selectPrimaryUnlocked(options)
  );
  selectionTail = run.catch(() => undefined);
  return run;
}

async function selectPrimaryUnlocked({ excludeTabId = null, keepCurrentOnNoMatch = false,
    requireReviewerReady = false } = {}) {
  const tabs = await reviewerTabs();
  await resumeAfterVerification(await reviewerTabs({ includeAuxiliary: true }));
  const [current, paused] = await Promise.all([readCoordinator(), pausedUntil()]);
  const currentTab = tabs.find((tab) => tab.id === current.tabId) || null;
  const settings = await config();
  const resume = await readReviewerResume(settings);
  const monitoring = tabs.filter(tab => monitoringTab(tab, settings, current, resume));
  if (current.tabId == null && resume) {
    monitoring.sort((a, b) => Number(b.url === resume.url) - Number(a.url === resume.url) ||
      Number(b.id === resume.tabId) - Number(a.id === resume.tabId));
  }
  const candidates = [];
  // While paused no tab may be primary, so every content script stays passive.
  if (!paused) {
    if (currentTab && monitoring.includes(currentTab) && currentTab.id !== excludeTabId) candidates.push(currentTab);
    for (const tab of monitoring) {
      if (tab.id !== currentTab?.id && tab.id !== excludeTabId) candidates.push(tab);
    }
  }
  if (paused) keepCurrentOnNoMatch = false;

  let chosen = null;
  let probe = null;
  for (const tab of candidates) {
    // A reused recovery tab can still contain its old homepage script until
    // the saved URL commits. That document must not claim the restored scan.
    if (tab.pendingUrl && tab.pendingUrl !== tab.url) {
      if (tab.id === current.tabId && Date.now() - current.selectedAt < LOADING_LEASE_MS) {
        chosen = tab;
        break;
      }
      continue;
    }
    const reply = await probeTab(tab);
    if (reply?.botCheck) {
      await handleBotCheck(tab.id, 'probe');
      break;
    }
    if (reply && (!requireReviewerReady || reply.reviewerReady)) {
      chosen = tab;
      probe = reply;
      break;
    }
    if (tab.id === currentTab?.id && tab.status === 'loading' &&
        Date.now() - current.selectedAt < LOADING_LEASE_MS) {
      // Page-to-page navigation temporarily destroys the primary content
      // script. Keep its lease while Chrome is loading it instead of promoting
      // a backup in the middle of every sweep. A genuinely hung load loses the
      // lease after a bounded grace period so a reachable fallback can take it.
      chosen = tab;
      break;
    }
  }

  if (!chosen && keepCurrentOnNoMatch && currentTab && monitoring.includes(currentTab)) chosen = currentTab;

  return coordinatorMutation(() => commitSelection(tabs, current, chosen, probe));
}

async function commitSelection(tabs, current, chosen, probe) {
  // Probing yields to tab events. A challenge or restart may have revoked this
  // selection's generation in the meantime; never overwrite that newer state.
  const latest = await readCoordinator();
  if (await pausedUntil() || latest.generation !== current.generation ||
      latest.tabId !== current.tabId) {
    return { tabs, tab: null, probe: null, state: latest, changed: false };
  }

  let state = current;
  const nextId = chosen?.id ?? null;
  const changed = nextId !== current.tabId;
  if (changed) {
    const resume = await readReviewerResume();
    const restore = (chosen?.url === resume?.url || (resume?.interruptedAt && chosen &&
      monitoringTab(chosen, await config(), current, resume))) && resume?.sweep;
    state = await writeCoordinator({
      tabId: nextId,
      generation: current.generation + 1,
      selectedAt: nextId == null ? 0 : Date.now(),
      paginationFallback: restore?.paginationFallback === true
    });
    if (restore && Array.isArray(restore.visited)) {
      await chrome.storage.local.set({ [`${SWEEP_PREFIX}${nextId}`]: {
        ...restore, generation: state.generation
      } });
    }
    const stored = await chrome.storage.local.get(null);
    const managedSweepKeys = new Set((await auxiliaryScans.managedTabIds()).map(id => `${SWEEP_PREFIX}${id}`));
    const inactiveSweepKeys = Object.keys(stored).filter((key) =>
      (key === 'sweep' || key.startsWith(SWEEP_PREFIX)) &&
      key !== `${SWEEP_PREFIX}${nextId}` && !managedSweepKeys.has(key)
    );
    if (inactiveSweepKeys.length) await chrome.storage.local.remove(inactiveSweepKeys);
    if (chosen) await saveReviewerResume(chosen, state);
    await broadcastRoles(tabs, state);
    if (current.tabId != null && nextId != null) {
      await chrome.storage.local.set({
        lastTabFailover: Date.now(),
        lastTabFailoverFrom: current.tabId,
        lastTabFailoverTo: nextId
      });
    }
  } else if (chosen && (!probe?.active || probe.generation !== state.generation)) {
    await saveReviewerResume(chosen, state);
    await sendRole(chosen, state);
  } else if (chosen) {
    await saveReviewerResume(chosen, state);
  }

  return { tabs, tab: chosen, probe, state, changed };
}

async function authorizedRole(sender, generation) {
  const tabId = sender?.tab?.id;
  const [state, paused] = await Promise.all([readCoordinator(), pausedUntil()]);
  let currentTab;
  if (Number.isInteger(tabId)) {
    try { currentTab = await chrome.tabs.get(tabId); } catch { /* A closed sender cannot renew a lease. */ }
  }
  const settings = await config();
  const scopeAllowed = [sender?.tab, currentTab].filter(tab => tab?.url)
    .every(tab => monitoringTab(tab, settings, state));
  return {
    tabId,
    state,
    scopeAllowed,
    authorized: !paused && scopeAllowed && Number.isInteger(tabId) && tabId === state.tabId &&
      (generation === undefined || generation === state.generation)
  };
}

async function handleRoleRequest(sender) {
  const tabId = sender?.tab?.id;
  if (!Number.isInteger(tabId)) {
    return { ok: false, error: 'Role requests must come from a reviewer tab.' };
  }
  if (await auxiliaryScans.ownsTab(tabId)) {
    const role = await auxiliaryScans.handle({ type: 'reviewer:role' }, sender);
    await syncScannerWorkspace();
    return role;
  }
  const selected = await selectPrimary();
  if (!selected.tab) await ensureReviewerTab();
  await syncScannerWorkspace();
  return { ok: true, ...roleFor(tabId, selected.state) };
}

async function handlePageReport(msg, sender) {
  if (await auxiliaryScans.ownsTab(sender?.tab?.id)) return auxiliaryScans.handle({ ...msg, type: 'reviewer:page' }, sender);
  const before = await authorizedRole(sender, msg.generation);
  if (!before.authorized) {
    return {
      ok: false,
      reason: before.tabId === before.state.tabId ? 'stale-generation' : 'not-primary',
      ...roleFor(before.tabId, before.state),
      active: before.scopeAllowed && before.tabId === before.state.tabId
    };
  }
  if (!Array.isArray(msg.items) || !msg.items.length) {
    return {
      ok: false,
      retryable: false,
      error: 'No items were supplied.',
      ...roleFor(before.tabId, before.state)
    };
  }

  const startedAt = Date.now();
  const result = await enqueuePost(msg.items, msg.page,
    async () => (await authorizedRole(sender, msg.generation)).authorized);
  const after = await authorizedRole(sender, msg.generation);
  if (result.ok && after.authorized) {
    await chrome.storage.session.remove(`recovery:${after.tabId}`);
    await recordDiagnostic('page-delivered', { tabId: after.tabId, generation: msg.generation,
      page: msg.page, items: msg.items.length, durationMs: Date.now() - startedAt,
      new: result.summary?.new || 0, duplicates: result.summary?.duplicates || 0,
      matched: result.summary?.matched || 0, notified: result.summary?.notified || 0,
      failed: result.summary?.failed || 0, pending: result.summary?.pending || 0 });
  }
  return {
    ...result,
    ...roleFor(after.tabId, after.state),
    // A request can finish after a restart/failover. The server safely dedupes
    // it, but the old content script must not mark its page visited.
    ok: Boolean(result.ok && after.authorized),
    reason: after.authorized ? result.reason : 'stale-generation'
  };
}

async function navigateToPageOne(tabId, state, native = false) {
  const tab = await chrome.tabs.get(tabId);
  const originalUrl = tab.url;
  if (await pausedUntil() || !eligibleUrl(tab.url, await pathRegex())) {
    throw new Error('The reviewer tab is waiting for verification or has navigated away.');
  }
  const url = new URL(tab.url);
  const { sweepMode } = await config();
  const queries = sweepQueries(sweepMode);
  url.searchParams.delete('page');
  if (queries.length) {
    url.searchParams.set('q', queries[0]);
    url.searchParams.set('queryNups', '');
  } else {
    url.searchParams.delete('q');
    url.searchParams.delete('queryNups');
  }
  if (native && url.toString() !== tab.url) {
    try {
      const reply = await chrome.tabs.sendMessage(tabId, {
        type: 'reviewer:restart-native', ...roleFor(tabId, state), target: url.toString()
      });
      if (reply?.ok === true && reply.navigation === 'native' &&
          reply.generation === state.generation) {
        // The page reports native success only after URL/content confirmation.
        return;
      }
    } catch { /* An old/missing content script still gets the repair load below. */ }
  }
  await recordDiagnostic('scan-restart-load', { tabId, generation: state.generation });
  // Logging/messaging yield to challenges, other restarts and manual browsing.
  // Revalidate and perform the fallback under the same lock as lease mutation.
  await coordinatorMutation(async () => {
    const [latest, currentTab, paused] = await Promise.all([
      readCoordinator(), chrome.tabs.get(tabId), pausedUntil()
    ]);
    if (paused || latest.tabId !== tabId || latest.generation !== state.generation ||
        currentTab?.url !== originalUrl ||
        (currentTab.pendingUrl && currentTab.pendingUrl !== originalUrl)) {
      throw new Error('Monitoring changed before the reviewer tab could restart.');
    }
    if (url.toString() !== tab.url) {
      await chrome.tabs.update(tabId, { url: url.toString() });
    } else {
      await chrome.tabs.reload(tabId, { bypassCache: false });
    }
  });
}

function repairReviewerTab() {
  return coordinatorMutation(async () => {
    const settings = await config();
    if (!(Number(settings.refreshMinutes) > 0) || await pausedUntil()) return false;
    const [tabs, current, resume] = await Promise.all([
      reviewerTabs(), readCoordinator(), readReviewerResume(settings)
    ]);
    const stored = await chrome.storage.local.get(tabs.map(tab => `${SWEEP_PREFIX}${tab.id}`));
    const tab = tabs.find(tab => tab.url === resume?.url) || tabs.find(tab =>
      monitoringTab(tab, settings, { tabId: tab.id,
        paginationFallback: stored[`${SWEEP_PREFIX}${tab.id}`]?.paginationFallback === true }));
    if (!tab) return false;
    const live = await chrome.tabs.get(tab.id);
    if (live?.url !== tab.url || (live.pendingUrl && live.pendingUrl !== tab.url)) return false;
    // Adopt the old per-tab record on the first upgrade, before a durable
    // checkpoint exists. Later reloads prefer the saved checkpoint.
    const sweep = resumeSweepForSettings(tab.url === resume?.url
      ? resume.sweep : stored[`${SWEEP_PREFIX}${tab.id}`], settings);
    const state = await writeCoordinator({ tabId: tab.id,
      generation: Math.max(current.generation, Number(sweep?.generation) || 0) + 1,
      selectedAt: Date.now(), paginationFallback: sweep?.paginationFallback === true });
    const key = `${SWEEP_PREFIX}${tab.id}`;
    if (Array.isArray(sweep?.visited)) {
      await chrome.storage.local.set({ [key]: { ...sweep, generation: state.generation } });
    } else {
      await chrome.storage.local.remove(key);
    }
    await saveReviewerResume(live, state);
    await broadcastRoles(tabs, { ...state, tabId: null });
    // An unpacked-extension reload invalidates old content scripts. Repair the
    // current URL so the new script can resume, without a page-one restart.
    const [latestTab, latestSettings] = await Promise.all([chrome.tabs.get(tab.id), config()]);
    if (await pausedUntil() || !(Number(latestSettings.refreshMinutes) > 0) ||
        latestTab?.url !== tab.url || (latestTab.pendingUrl && latestTab.pendingUrl !== tab.url) ||
        !monitoringTab(latestTab, latestSettings, state)) return false;
    await chrome.tabs.reload(tab.id, { bypassCache: false });
    return true;
  });
}

async function restartPrimary(reason = 'manual') {
  if (await pausedUntil()) {
    await resumeAfterVerification(await reviewerTabs({ includeAuxiliary: true }));
    if (await pausedUntil()) {
      return { ok: false, paused: true, error: 'Paused until all Walmart checks are cleared.' };
    }
  }
  const selected = await selectPrimary();
  // A manual restart is also the repair path after an unpacked-extension
  // reload, when the tab exists but has no listening content script yet.
  const settings = await config();
  const tab = selected.tab || selected.tabs.find(candidate => monitoringTab(candidate, settings, selected.state)) || null;
  if (!tab) {
    await ensureReviewerTab();
    return { ok: false, error: 'No reachable reviewer tab is open.', ...roleFor(null, selected.state) };
  }

  const state = await coordinatorMutation(async () => {
    if (await pausedUntil()) return { paused: true };
    const latest = await readCoordinator();
    if (latest.generation !== selected.state.generation) return null;
    return writeCoordinator({
      tabId: tab.id,
      generation: latest.generation + 1,
      selectedAt: Date.now()
    });
  });
  if (state?.paused) return { ok: false, paused: true, error: 'Paused after a Walmart bot check.' };
  if (!state) return { ok: false, error: 'Monitoring changed while restarting. Try again.' };
  // Revoke the old document's lease before clearing progress. Otherwise its
  // pending navigation can race the requested trip back to page 1.
  try {
    await chrome.tabs.sendMessage(tab.id, {
      type: 'reviewer:set-role',
      ...roleFor(tab.id, state),
      active: false
    });
  } catch {
    // No listener is exactly the state this repair path is meant to fix.
  }
  await Promise.allSettled(
    selected.tabs.filter((candidate) => candidate.id !== tab.id)
      .map((candidate) => sendRole(candidate, state))
  );
  const reset = await coordinatorMutation(async () => {
    if (await pausedUntil()) return { ok: false, paused: true };
    const latest = await readCoordinator();
    if (latest.tabId !== tab.id || latest.generation !== state.generation) return { ok: false };
    // Messaging above yields to verification. Reset progress only while this
    // lease still owns the coordinator, under the same lock as shared pause.
    await chrome.storage.local.remove([
      `${SWEEP_PREFIX}${tab.id}`, 'sweep', 'lastSweepPages',
      'lastSweepDone', 'lastSweepStartedAt', RESUME_KEY
    ]);
    return { ok: true };
  });
  if (!reset.ok) return { ...reset, error: 'Monitoring changed before its progress could restart.' };

  try {
    await recordDiagnostic('sweep-start', { tabId: tab.id, generation: state.generation,
      reason, mode: normalizeSweepMode((await config()).sweepMode) });
    await navigateToPageOne(tab.id, state, reason === 'scheduled');
    return { ok: true, reason, ...roleFor(tab.id, state) };
  } catch (err) {
    const message = `Could not restart reviewer tab ${tab.id}: ${err.message || err}`;
    await chrome.storage.local.set({ lastError: message, lastErrorAt: Date.now() });
    const latest = await readCoordinator();
    await sendRole(tab, latest);
    return { ok: false, error: message, ...roleFor(tab.id, latest) };
  }
}

async function handlePaginationFallback(msg, sender) {
  const before = await authorizedRole(sender, msg.generation);
  if (!before.authorized) return { ok: false, ...roleFor(before.tabId, before.state) };
  return coordinatorMutation(async () => {
    const latest = await readCoordinator();
    const settings = await config();
    const tab = await chrome.tabs.get(before.tabId);
    if (latest.tabId !== before.tabId || latest.generation !== msg.generation ||
        await pausedUntil() || normalizeSweepMode(settings.sweepMode) !== 'none' ||
        !monitoringTab(tab, settings, latest)) {
      return { ok: false, ...roleFor(before.tabId, latest) };
    }
    // Idempotent retries preserve the current category. Older fallback state
    // used the alphabet mode, so migrate it once to the focused category pass.
    const key = `${SWEEP_PREFIX}${before.tabId}`;
    const stored = (await chrome.storage.local.get([key]))[key];
    const previous = stored?.generation === latest.generation ? stored : null;
    const allowedQueries = sweepQueries('high-value');
    const previousQuery = String(previous?.query || '').trim().toLocaleLowerCase('en-US');
    const queryIsCurrent = allowedQueries.some(query =>
      query.toLocaleLowerCase('en-US') === previousQuery);
    if (!latest.paginationFallback || sweepRecordMode(previous || {}) !== 'high-value' ||
        !queryIsCurrent) {
      await chrome.storage.local.set({ [key]: {
        generation: latest.generation, mode: 'high-value', alphabet: false,
        paginationFallback: true, query: allowedQueries[0],
        total: null, visited: [], endRetries: {},
        startedAt: previous?.startedAt || Date.now(), lastProgressAt: Date.now(),
        completedPages: new Set(previous?.visited || []).size,
        completedQueries: 0, completedLetters: 0
      } });
      await writeCoordinator({ ...latest, paginationFallback: true });
      await recordDiagnostic('sweep-start', { tabId: before.tabId,
        generation: latest.generation, mode: 'high-value',
        reason: ['empty-results', 'blank-timeout'].includes(msg.reason) ? msg.reason : 'unusable',
        page: msg.page, totalPages: msg.totalPages, attempt: msg.attempt, elapsedMs: msg.elapsedMs });
    }
    return { ok: true, ...roleFor(before.tabId, latest) };
  });
}

async function handleUnusable(msg, sender) {
  if (await auxiliaryScans.ownsTab(sender?.tab?.id)) return auxiliaryScans.handle({ ...msg, type: 'reviewer:unusable' }, sender);
  const before = await authorizedRole(sender, msg.generation);
  if (!before.authorized) return { ok: false, ...roleFor(before.tabId, before.state) };

  const reason = String(msg.reason || 'Reviewer page did not become usable').slice(0, 300);
  await chrome.storage.local.set({
    lastError: `${reason}. Trying another reviewer tab if one is available.`,
    lastErrorAt: Date.now()
  });

  const selected = await selectPrimary({
    excludeTabId: before.tabId,
    keepCurrentOnNoMatch: true,
    requireReviewerReady: true
  });
  if (selected.tab && selected.tab.id !== before.tabId) {
    const restarted = await restartPrimary('failover');
    return {
      ok: false,
      recover: 'failed-over',
      failedOverTo: restarted.primaryTabId,
      ...roleFor(before.tabId, await readCoordinator())
    };
  }
  if (msg.restartSweep === true) {
    // A confirmed unreadable interior page needs a fresh pass, not another
    // reload with the same persisted empty-page attempts and impossible total.
    return coordinatorMutation(async () => {
      const latest = await readCoordinator();
      if (latest.tabId !== before.tabId || latest.generation !== msg.generation ||
          await pausedUntil()) {
        return { ok: false, ...roleFor(before.tabId, latest) };
      }
      const key = `${SWEEP_PREFIX}${before.tabId}`;
      const sweep = (await chrome.storage.local.get([key]))[key];
      if (latest.paginationFallback && sweep?.generation === latest.generation &&
          sweep.paginationFallback === true) {
        // Recover this letter without losing the automatic round or repeating
        // earlier letters. The regular saved mode is still used next round.
        await chrome.storage.local.set({ [key]: { ...sweep,
          total: null, visited: [], endRetries: {}, lastProgressAt: Date.now() } });
      } else {
        await chrome.storage.local.remove(key);
      }
      await chrome.storage.local.remove([
        'lastSweepStartedAt', 'lastSweepDone', 'lastSweepPages'
      ]);
      return { ok: false, recover: 'restart', retryAfterMs: await reserveRecovery(before.tabId),
        ...roleFor(before.tabId, latest) };
    });
  }
  return {
    ok: false,
    recover: 'reload',
    retryAfterMs: await reserveRecovery(before.tabId),
    ...roleFor(before.tabId, before.state)
  };
}

async function reserveRecovery(tabId) {
  const key = `recovery:${tabId}`;
  const stored = (await chrome.storage.session.get([key]))[key];
  const attempts = Math.min(6, (Number(stored?.attempts) || 0) + 1);
  const delayMs = Math.min(300_000, 15_000 * 2 ** (attempts - 1));
  await chrome.storage.session.set({ [key]: { attempts, nextAt: Date.now() + delayMs } });
  await recordDiagnostic('recovery-reload', { tabId, attempt: attempts, delayMs });
  return delayMs;
}

async function coordinatorStatus() {
  const selected = await selectPrimary();
  let sweep = null;
  if (selected.state.tabId != null) {
    const key = `${SWEEP_PREFIX}${selected.state.tabId}`;
    const stored = await chrome.storage.local.get([key]);
    sweep = stored[key] || null;
  }
  return {
    ok: true,
    primaryTabId: selected.state.tabId,
    generation: selected.state.generation,
    reachable: Boolean(selected.probe),
    tabCount: selected.tabs.length,
    tabs: selected.tabs.map((tab) => ({
      id: tab.id,
      windowId: tab.windowId,
      index: tab.index,
      active: tab.id === selected.state.tabId
    })),
    sweep,
    pausedUntil: await pausedUntil(),
    botCheck: await readBotCheck()
  };
}

function respondAsync(task, sendResponse) {
  Promise.resolve(task)
    .then((value) => sendResponse(value))
    .catch(async (err) => {
      const message = err?.message || String(err);
      console.error('Reviewer Item Relay: message handler failed', err);
      try {
        await chrome.storage.local.set({ lastError: message, lastErrorAt: Date.now() });
      } finally {
        sendResponse({ ok: false, error: message });
      }
    });
  return true;
}

function optionsSender(sender) {
  return sender?.id === chrome.runtime.id && sender?.url === chrome.runtime.getURL('options.html');
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === 'reviewer:facet-input') {
    // A content script can request only its assigned ordinary department.
    // Browser-supplied sender identity, fresh role and focus are rechecked at
    // each input boundary. No caller script, coordinates or arbitrary tab ID.
    if (sender?.frameId !== 0 || !ReviewerFacetInput.scope(sender?.url) ||
        !Number.isInteger(sender?.tab?.id)) {
      sendResponse({ ok: false, reason: 'facet-error' }); return undefined;
    }
    return respondAsync(facetInput.click(sender.tab.id, msg.generation, msg.selection), sendResponse);
  }
  if (msg?.type === 'reviewer:focus') {
    return respondAsync((async () => {
      if (!await authorizedScanner(sender?.tab?.id, msg.generation)) {
        return { ok: false, granted: false, enabled: true };
      }
      await syncScannerWorkspace();
      const responseWindow = ['page-scroll', 'page-navigation'].includes(msg.purpose) ? 3_000 : 1_000;
      const expiresAt = Number.isFinite(msg.expiresAt) ? Math.min(msg.expiresAt, Date.now() + responseWindow)
        : Date.now() + responseWindow;
      return tabWorkspace.request(sender.tab.id, msg.generation, expiresAt,
        ['page-scroll', 'facet-controls'].includes(msg.purpose) ? 10_000 : 3_000);
    })(), sendResponse);
  }
  if (msg?.type === 'reviewer:user-active') {
    const tabId = sender?.tab?.id;
    if (!Number.isInteger(tabId) || !Number.isInteger(msg.generation)) {
      sendResponse({ ok: false }); return undefined;
    }
    // Chrome supplies the sender ID; the workspace admits only registered
    // scanner IDs. Revoke synchronously, before storage or optional group RPCs.
    // Completed scanner tabs remain entitled to protect real user activity.
    return respondAsync(tabWorkspace.noteActivity(tabId), sendResponse);
  }
  if (msg?.type === 'reviewer:aux-status') {
    if (!optionsSender(sender)) { sendResponse({ ok: false }); return undefined; }
    return respondAsync(auxiliaryScans.status(), sendResponse);
  }
  if (msg?.type === 'reviewer:aux-restart') {
    if (!optionsSender(sender)) {
      sendResponse({ ok: false }); return undefined;
    }
    return respondAsync(auxiliaryScans.restart(msg.lane), sendResponse);
  }
  if (msg?.type === 'reviewer:aux-discovered' || msg?.type === 'reviewer:aux-selected') {
    return respondAsync(auxiliaryScans.handle(msg, sender).then(reply => reply || { ok: false }), sendResponse);
  }
  if (msg?.type === 'reviewer:verification-retry') {
    if (sender?.id !== chrome.runtime.id || sender?.url !== chrome.runtime.getURL('options.html')) {
      sendResponse({ ok: false }); return undefined;
    }
    return respondAsync(verificationAutomation.retryCurrentCheck().then(result => {
      if (result.ok) triggerVerificationAutomation();
      return result;
    }), sendResponse);
  }
  if (msg?.type === 'reviewer:diagnostics') {
    // Exports are initiated from Options, never from a Walmart content script.
    if (!optionsSender(sender)) { sendResponse({ ok: false }); return undefined; }
    return respondAsync(diagnosticReport().then(report => ({ ok: true, report })), sendResponse);
  }
  if (msg?.type === 'reviewer:diagnostic') {
    return respondAsync((async () => {
      if (!Number.isInteger(sender?.tab?.id) ||
          !(eligibleUrl(sender.tab.url, await pathRegex()) || isBotCheckUrl(sender.tab.url))) {
        return { ok: false };
      }
      await recordDiagnostic(msg.event, { ...msg.details, tabId: sender.tab.id });
      return { ok: true };
    })(), sendResponse);
  }
  if (msg?.type === 'reviewer:verification') {
    if (!Number.isInteger(sender?.tab?.id) || !isWalmartUrl(sender.tab.url)) {
      sendResponse({ ok: false }); return undefined;
    }
    return respondAsync(handleBotCheck(sender.tab.id, msg.reason), sendResponse);
  }
  if (msg?.type === 'reviewer:sweep-complete') {
    return respondAsync((async () => {
      if (await auxiliaryScans.ownsTab(sender?.tab?.id)) return auxiliaryScans.handle(msg, sender);
      if (!(await authorizedRole(sender, msg.generation)).authorized) return { ok: false };
      await handleCheckpoint(msg, sender);
      const { lastSweepStartedAt, lastSweepDone } = await chrome.storage.local.get(
        ['lastSweepStartedAt', 'lastSweepDone']);
      await recordDiagnostic('sweep-complete', { tabId: sender.tab.id, generation: msg.generation,
        pages: msg.pages, durationMs: Math.max(0, lastSweepDone - lastSweepStartedAt) });
      await scheduleRefresh();
      return { ok: true };
    })(), sendResponse);
  }
  if (msg?.type === 'reviewer:role') {
    return respondAsync(handleRoleRequest(sender), sendResponse);
  }
  if (msg?.type === 'reviewer:checkpoint') {
    return respondAsync(handleCheckpoint(msg, sender), sendResponse);
  }
  if (msg?.type === 'reviewer:page') {
    return respondAsync(handlePageReport(msg, sender), sendResponse);
  }
  if (msg?.type === 'reviewer:unusable') {
    return respondAsync(handleUnusable(msg, sender), sendResponse);
  }
  if (msg?.type === 'reviewer:pagination-fallback') {
    return respondAsync(handlePaginationFallback(msg, sender), sendResponse);
  }
  if (msg?.type === 'reviewer:status') {
    return respondAsync(coordinatorStatus(), sendResponse);
  }
  if (msg?.type === 'reviewer:restart') {
    return respondAsync(restartPrimary('manual'), sendResponse);
  }

  // Compatibility with an already-loaded 2.0.0 content script while the
  // unpacked extension is being reloaded. It is still restricted to the primary.
  if (msg?.type === 'items') {
    return respondAsync(
      readCoordinator().then((state) =>
        handlePageReport({ ...msg, generation: state.generation }, sender)),
      sendResponse
    );
  }
  if (msg?.type === 'whoami') {
    sendResponse({ tabId: sender?.tab?.id ?? null });
    return undefined;
  }
  return undefined;
});

// --- self-refresh -----------------------------------------------------------

async function scheduleRefresh(busy = false) {
  const until = await pausedUntil();
  if (until) {
    // Recheck readiness at the deadline; elapsed time cannot clear a challenge.
    await chrome.alarms.create(REFRESH_ALARM, { when: until });
    return;
  }
  const { refreshMinutes } = await config();
  if (!refreshMinutes || refreshMinutes <= 0) {
    await chrome.alarms.clear(REFRESH_ALARM);
    return;
  }
  const check = await readBotCheck();
  const restartAt = sweepRestartAt({ refreshMinutes, botCheck: check,
    ...await chrome.storage.local.get(['lastSweepStartedAt', 'lastSweepDone']) });
  if (restartAt) {
    await chrome.alarms.create(REFRESH_ALARM, { when: restartAt });
    return;
  }
  const effectiveMinutes = effectiveRefreshMinutes({ refreshMinutes, botCheck: check });
  const jittered = effectiveMinutes * (1 + (Math.random() * 2 - 1) * JITTER);
  const delayMinutes = busy ? BUSY_RETRY_MIN : Math.max(MIN_DELAY_MIN, jittered);
  await chrome.alarms.create(REFRESH_ALARM, { delayInMinutes: delayMinutes });
}

let refreshInFlight = false;
async function refreshTick() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    await resumeAfterVerification(await reviewerTabs({ includeAuxiliary: true }));
    if (await pausedUntil()) {
      // No reloads, not even the repair reload below, while Walmart has asked
      // this traffic to stop.
      await scheduleRefresh();
      return;
    }
    if (!(Number((await config()).refreshMinutes) > 0)) {
      await scheduleRefresh();
      return;
    }
    const selected = await selectPrimary();
    if (await pausedUntil()) {
      await scheduleRefresh();
      return;
    }
    if (!selected.tab) {
      // If no content script answered, repair the deterministic first candidate.
      const settings = await config();
      const candidate = selected.tabs.find(tab => monitoringTab(tab, settings, selected.state));
      if (candidate) {
        try {
          const tabId = candidate.id;
          const recovery = (await chrome.storage.session.get([`recovery:${tabId}`]))[`recovery:${tabId}`];
          if (!(recovery?.nextAt > Date.now())) {
            const originalUrl = candidate.url;
            await reserveRecovery(tabId);
            // Recovery reservation yields to manual browsing and lease changes.
            await coordinatorMutation(async () => {
              const [latest, currentTab, paused, currentSettings] = await Promise.all([
                readCoordinator(), chrome.tabs.get(tabId), pausedUntil(), config()
              ]);
              if (paused || latest.generation !== selected.state.generation ||
                  (latest.tabId != null && latest.tabId !== tabId) ||
                  currentTab?.url !== originalUrl ||
                  (currentTab.pendingUrl && currentTab.pendingUrl !== originalUrl) ||
                  !monitoringTab(currentTab, currentSettings, latest)) return;
              await chrome.tabs.reload(tabId, { bypassCache: false });
            });
          }
        } catch (err) {
          console.warn('Reviewer Item Relay: could not reload reviewer tab', err);
        }
      } else {
        await ensureReviewerTab();
      }
      await scheduleRefresh(true);
      return;
    }

    const state = selected.probe || await probeTab(selected.tab);
    if (!state) {
      await scheduleRefresh(true);
      return;
    }
    if (!state.active || state.generation !== selected.state.generation) {
      await sendRole(selected.tab, selected.state);
      await scheduleRefresh(true);
      return;
    }

    if (state.sweeping) {
      const stale = state.lastActivityAt && Date.now() - state.lastActivityAt > STALE_SWEEP_MS;
      if (stale && !(state.nextActionAt > Date.now()) &&
          state.phase !== 'ingest-retry' && state.phase !== 'waiting-user') {
        try {
          await chrome.tabs.sendMessage(selected.tab.id, {
            type: 'reviewer:recover', generation: selected.state.generation
          });
        } catch {
          // The next alarm will probe/fail over if the tab remains unreachable.
        }
      }
      await scheduleRefresh(true);
      return;
    }
    if (state.busy) {
      await scheduleRefresh(true);
      return;
    }

    const restartAt = sweepRestartAt({ ...(await config()), botCheck: await readBotCheck(),
      ...await chrome.storage.local.get(['lastSweepStartedAt', 'lastSweepDone']) });
    if (restartAt > Date.now()) {
      await chrome.alarms.create(REFRESH_ALARM, { when: restartAt });
      return;
    }
    const restarted = await restartPrimary('scheduled');
    await scheduleRefresh(!restarted.ok);
  } finally {
    refreshInFlight = false;
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === auxiliaryScans.alarmName) {
    scannerControl.syncSettings().then(() => auxiliaryScans.tick()).then(() => scannerControl.syncCommands())
      .then(syncScannerWorkspace).then(() => scannerControl.syncStatus())
      .catch(error => console.warn('Reviewer Item Relay: auxiliary scan tick failed', error));
  }
  if (alarm.name === 'verificationRetry') triggerVerificationAutomation();
  if (alarm.name === REFRESH_ALARM) {
    refreshTick().catch((err) => {
      console.error('Reviewer Item Relay: refresh failed', err);
      // Named alarms are one-shot. Always arm a recovery attempt after an
      // unexpected coordinator/API error so auto-refresh cannot silently die.
      scheduleRefresh(true).catch((retryErr) =>
        console.error('Reviewer Item Relay: could not reschedule refresh', retryErr));
    });
  }
  if (alarm.name === BADGE_ALARM) {
    syncVerificationBadge(true);
  }
  if (alarm.name === STATUS_ALARM) {
    reportBrowserStatus().catch((err) =>
      console.warn('Reviewer Item Relay: could not report browser status', err));
  }
});

async function ensureAlarms() {
  await auxiliaryScans.ensureAlarm();
  if (!await chrome.alarms.get(STATUS_ALARM)) {
    await chrome.alarms.create(STATUS_ALARM, { periodInMinutes: 1 });
  }
  if (!await chrome.alarms.get(REFRESH_ALARM)) await scheduleRefresh();
}

async function reportBrowserStatus() {
  await scannerControl.syncSettings();
  triggerVerificationAutomation();
  await syncRelaySettings();
  await auxiliaryScans.tick();
  await ensureReviewerTab();
  await syncBrowserDiagnostics();
  const status = await coordinatorStatus();
  await syncScannerWorkspace();
  await scannerControl.syncCommands();
  await scannerControl.syncStatus();
  const { endpoint, token, refreshMinutes } = await config();
  const progress = await chrome.storage.local.get(['lastSweepPages', 'lastSweepDone']);
  const probe = status.primaryTabId == null ? null : await probeOnce(status.primaryTabId);
  const phase = status.pausedUntil ? 'waiting-verification'
    : !status.reachable ? 'unreachable'
      : probe?.phase === 'waiting-user' ? 'waiting-user'
        : probe?.phase === 'ingest-retry' ? 'ingest-retry'
          : probe?.phase === 'complete' ? 'complete'
            : probe?.sweeping ? 'sweeping' : 'ready';
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['X-Ingest-Token'] = token;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(new URL('/relay-status', checkedEndpoint(endpoint)).toString(), {
      method: 'POST', headers, signal: controller.signal,
      body: JSON.stringify({
        phase, tab_count: Math.min(100, status.tabCount), reachable: status.reachable,
        refresh_minutes: Number(refreshMinutes), paused_until: status.pausedUntil,
        last_sweep_pages: Math.min(sweepQueries('high-value').length * 100,
          Number(progress.lastSweepPages) || 0),
        last_sweep_at: Number(progress.lastSweepDone) || 0
      })
    });
    if (!response.ok) throw new Error(`Browser status returned HTTP ${response.status}`);
  } finally {
    clearTimeout(timeout);
  }
}

// Chrome may lose alarms on an update/restart. Recreate missing alarms whenever
// this service worker starts, without postponing an existing scheduled refresh.
const initialization = ensureAlarms().then(() => syncVerificationBadge()).then(async () => {
  await scannerControl.syncSettings();
  // Stored pauses survive upgrades/restarts. Start even if an old document has
  // not emitted a new content-script event since the extension was enabled.
  triggerVerificationAutomation();
  auxiliaryScans.tick().then(syncScannerWorkspace).catch(console.error);
}).catch(console.error);

chrome.action.onClicked.addListener(async (tab) => {
  try {
    if (Number.isInteger(tab?.id)) {
      if (isBotCheckUrl(tab.url)) await handleBotCheck(tab.id);
      const check = await readBotCheck();
      if (check.pausedUntil > 0 && check.challengedTabIds.includes(tab.id)) {
        // The click supplies activeTab permission. Capture before opening
        // Options so that the verification page is still the visible tab.
        await captureVerificationEvidence(tab.id);
      }
    }
    await chrome.runtime.openOptionsPage();
  } catch {
    console.warn('Reviewer Item Relay: could not open verification diagnostics');
  }
});

chrome.runtime.onStartup.addListener(async () => {
  try {
    // Only a true browser restart resets uncertain held input. Extension
    // reloads and worker restarts must preserve that conservative block.
    await chrome.storage.local.set({ reviewerFacetInputUnreleased: false });
    await syncRelaySettings();
    await scannerControl.syncSettings();
    await ensureAlarms();
    await scheduleRefresh();
    await ensureReviewerTab();
    await selectPrimary();
    await auxiliaryScans.tick();
    await syncScannerWorkspace();
  } catch (err) {
    console.error('Reviewer Item Relay: could not restore monitoring', err);
  }
});

chrome.runtime.onInstalled.addListener(async () => {
  // Updated pager handling deserves a fresh attempt. Ordinary worker/browser
  // restarts retain the cooldown; outstanding verification stays paused.
  await chrome.storage.local.remove(['nativeNavigationPausedUntil', 'nativeNavigationFailures']);
  await migrateFromSync();
  await syncRelaySettings();
  await scannerControl.syncSettings();
  await ensureAlarms();
  await scheduleRefresh();
  if (!await ensureReviewerTab()) await repairReviewerTab();
  await auxiliaryScans.tick();
  await syncScannerWorkspace();
  await scannerControl.syncCommands();
  await scannerControl.syncStatus();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if ('verificationAssistEnabled' in changes || 'verificationNativeEnabled' in changes ||
      'botCheck' in changes) triggerVerificationAutomation();
  if ('refreshMinutes' in changes) {
    scheduleRefresh().catch(console.error);
    auxiliaryScans.tick().catch(console.error);
  }
  if ('pathPattern' in changes || 'sweepMode' in changes) {
    selectPrimary().then(() => ensureReviewerTab()).catch(console.error);
  }
  if (['auxiliaryDepartments', 'auxiliaryKeywords', 'auxiliaryBrands', 'auxiliaryKeywordQueries']
      .some(key => key in changes)) auxiliaryScans.tick().catch(console.error);
  if ('groupScannerTabs' in changes) syncScannerWorkspace().catch(console.error);
  if ('rotateScannerTabs' in changes) tabWorkspace.revoke();
});

chrome.tabs.onActivated?.addListener(({ tabId }) => {
  if (!tabWorkspace.isExpectedActivation(tabId)) {
    tabWorkspace.revoke();
    tabWorkspace.noteActivity(tabId).catch(() => undefined);
  }
});
chrome.windows?.onFocusChanged?.addListener(() => tabWorkspace.revoke());

chrome.tabs.onRemoved.addListener((tabId, removeInfo) => {
  chrome.storage.session.remove(`recovery:${tabId}`).catch(() => undefined);
  return auxiliaryScans.ownsTab(tabId).then(async managed => {
    if (managed) {
      await auxiliaryScans.removed(tabId);
      if (!removeInfo?.isWindowClosing) await auxiliaryScans.tick();
      return;
    }
    return coordinatorMutation(async () => {
      const state = await readCoordinator();
      if (state.tabId !== tabId) {
        await chrome.storage.local.remove(`${SWEEP_PREFIX}${tabId}`);
        return false;
      }
      const resume = await readReviewerResume();
      if (resume?.tabId === tabId && resume.generation === state.generation) {
        await saveReviewerResume({ id: tabId, url: resume.url }, state);
      }
      await chrome.storage.local.remove(`${SWEEP_PREFIX}${tabId}`);
      await writeCoordinator({ tabId: null, generation: state.generation + 1, selectedAt: 0 });
      return true;
    }).then(async changed => {
      if (!removeInfo?.isWindowClosing) await ensureReviewerTab();
      if (changed) await selectPrimary();
    });
  }).catch(console.error);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!changeInfo.url && !changeInfo.status) return;
  // Any Walmart tab landing on the bot check pauses everything: the check is
  // about this browser session, not about one tab.
  if (isBotCheckUrl(changeInfo.url || tab?.url)) {
    handleBotCheck(tabId).catch(console.error);
    return;
  }
  return auxiliaryScans.ownsTab(tabId).then(async managed => {
    if (managed) {
      if (changeInfo.status === 'complete') {
        await resumeAfterVerification(await reviewerTabs({ includeAuxiliary: true }));
        await auxiliaryScans.tick();
      }
      return;
    }
    return pathRegex().then(async (re) => {
      if ((changeInfo.status === 'complete' || changeInfo.url) && eligibleUrl(tab?.url, re)) {
        await selectPrimary();
      }
      const changed = await coordinatorMutation(async () => {
        const state = await readCoordinator();
        if (state.tabId !== tabId || await pausedUntil()) return false;
        if (changeInfo.url && (!eligibleUrl(tab.url, re) || !monitoringTab(tab, await config(), state))) {
          await writeCoordinator({ tabId: null, generation: state.generation + 1, selectedAt: 0 });
          return true;
        }
        if (changeInfo.status === 'loading') {
          // Read and update the current lease under the same lock as challenges.
          await writeCoordinator({ ...state, selectedAt: Date.now() });
        }
        return false;
      });
      if (changed) await selectPrimary();
      if (changeInfo.url) await ensureReviewerTab();
    });
  }).catch(console.error);
});

// 1.0.x stored settings in sync storage. Only the refresh interval is safe to
// migrate; an old endpoint may refer to the retired cloud deployment.
async function migrateFromSync() {
  const synced = await chrome.storage.sync.get(['endpoint', 'token', 'refreshMinutes']);
  await chrome.storage.sync.remove(['endpoint', 'token', 'refreshMinutes']);
  if (!Object.keys(synced).length) return;
  const existing = await chrome.storage.local.get(['refreshMinutes']);
  if (synced.refreshMinutes && !existing.refreshMinutes) {
    await chrome.storage.local.set({ refreshMinutes: synced.refreshMinutes });
  }
}
