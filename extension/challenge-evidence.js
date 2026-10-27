// Best-effort local evidence for a visible, outstanding verification. Chrome's
// activeTab grant normally comes from clicking the extension icon; declaring
// activeTab alone does not permit unattended screenshots. Never activate tabs
// or export page HTML, cookies, URLs, raw errors, or pixels into diagnostics.
const VERIFICATION_CAPTURE_KEY = 'reviewerVerificationEvidence';
const VERIFICATION_CAPTURE_MAX_LENGTH = 3 * 1024 * 1024;
const VERIFICATION_CAPTURE_UNAVAILABLE =
  'Verification screenshot unavailable. With the verification tab selected, click the extension icon to retry.';
const VERIFICATION_CAPTURE_CHANGED =
  'Verification screenshot skipped because the active tab or verification changed.';
let verificationCaptureTail = Promise.resolve();
const verificationCapturesInFlight = new Map();
let verificationCapturedLastAt = 0;

async function verificationCaptureStatus(status, error = '') {
  try {
    await chrome.storage.local.set({
      lastVerificationCapture: { at: Date.now(), status },
      lastVerificationCaptureError: error
    });
  } catch {
    // Evidence failures must never interfere with scanning or verification.
  }
}

async function visibleVerificationTab(tabId, expected = null) {
  const check = await readBotCheck();
  if (!(check.pausedUntil > 0) || !(check.lastAt > 0) ||
      !check.challengedTabIds?.includes(tabId) ||
      (expected && check.lastAt !== expected.lastAt)) return null;
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (active?.id !== tabId || active.status !== 'complete' ||
      !Number.isInteger(active.windowId)) return null;
  const tab = await chrome.tabs.get(tabId);
  if (!tab || tab.url !== active.url || tab.windowId !== active.windowId ||
      (tab.pendingUrl && tab.pendingUrl !== tab.url) ||
      (expected && (tab.url !== expected.url || tab.windowId !== expected.windowId))) return null;
  const url = new URL(tab.url);
  if (url.protocol !== 'https:' || url.hostname !== 'www.walmart.com' ||
      url.username || url.password) return null;
  if (!/^\/blocked(\/|$)/i.test(url.pathname)) {
    if (!eligibleUrl(tab.url, await pathRegex()) ||
        (await probeOnce(tabId))?.botCheck !== true) return null;
  }
  // A probe can yield while the user switches tabs or navigates. Check again
  // immediately before handing control back to captureVisibleTab/download.
  const [latest] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (latest?.id !== tabId || latest.url !== tab.url ||
      latest.windowId !== tab.windowId || latest.status !== 'complete' ||
      (latest.pendingUrl && latest.pendingUrl !== latest.url)) return null;
  const latestCheck = await readBotCheck();
  if (!(latestCheck.pausedUntil > 0) || latestCheck.lastAt !== check.lastAt ||
      !latestCheck.challengedTabIds?.includes(tabId)) return null;
  return { tabId, windowId: tab.windowId, url: tab.url, lastAt: check.lastAt };
}

async function captureVerificationEvidenceUnlocked(tabId) {
  try {
    const before = Number.isInteger(tabId) ? await visibleVerificationTab(tabId) : null;
    if (!before) {
      await verificationCaptureStatus('unavailable', VERIFICATION_CAPTURE_CHANGED);
      return { ok: false, error: VERIFICATION_CAPTURE_CHANGED };
    }
    const cached = (await chrome.storage.session.get([VERIFICATION_CAPTURE_KEY]))[VERIFICATION_CAPTURE_KEY];
    if (verificationCapturedLastAt === before.lastAt || cached?.lastAt === before.lastAt) {
      return { ok: true, captured: false };
    }
    // Revalidate after the storage read, before requesting any pixels.
    if (!await visibleVerificationTab(tabId, before)) {
      await verificationCaptureStatus('unavailable', VERIFICATION_CAPTURE_CHANGED);
      return { ok: false, error: VERIFICATION_CAPTURE_CHANGED };
    }
    const image = await chrome.tabs.captureVisibleTab(before.windowId, {
      format: 'jpeg', quality: 70
    });
    if (typeof image !== 'string' || image.length > VERIFICATION_CAPTURE_MAX_LENGTH ||
        !/^data:image\/jpeg;base64,[A-Za-z0-9+/]+={0,2}$/.test(image)) {
      await verificationCaptureStatus('unavailable', VERIFICATION_CAPTURE_UNAVAILABLE);
      return { ok: false, error: VERIFICATION_CAPTURE_UNAVAILABLE };
    }
    // captureVisibleTab targets the window's active tab, not a tab ID. Discard
    // its result if navigation, focus, or verification changed during capture.
    if (!await visibleVerificationTab(tabId, before)) {
      await verificationCaptureStatus('unavailable', VERIFICATION_CAPTURE_CHANGED);
      return { ok: false, error: VERIFICATION_CAPTURE_CHANGED };
    }
    const base = `ReviewerItemRelay/verification-${Date.now()}`;
    await chrome.downloads.download({
      url: image, filename: `${base}.jpg`, saveAs: false, conflictAction: 'uniquify'
    });
    verificationCapturedLastAt = before.lastAt;
    try {
      await chrome.storage.session.set({ [VERIFICATION_CAPTURE_KEY]: { lastAt: before.lastAt } });
    } catch {
      // In-memory deduplication still prevents repeats in this worker.
    }
    let reportError = '';
    try {
      // Reuse the existing field allowlist, never storage.get(null) or a tab
      // payload. The report contains no image, credentials, or page contents.
      const report = JSON.stringify(await diagnosticReport(), null, 2);
      await chrome.downloads.download({
        url: `data:application/json;charset=utf-8,${encodeURIComponent(report)}`,
        filename: `${base}.json`, saveAs: false, conflictAction: 'uniquify'
      });
    } catch {
      reportError = 'Verification screenshot saved, but the diagnostic report could not be downloaded.';
    }
    await verificationCaptureStatus('saved', reportError);
    return { ok: true, captured: true };
  } catch {
    await verificationCaptureStatus('unavailable', VERIFICATION_CAPTURE_UNAVAILABLE);
    return { ok: false, error: VERIFICATION_CAPTURE_UNAVAILABLE };
  }
}

// Coalesce duplicate detections and serialize all captures. Failed attempts
// remain retryable after the user grants activeTab by clicking the toolbar.
function captureVerificationEvidence(tabId) {
  if (verificationCapturesInFlight.has(tabId)) return verificationCapturesInFlight.get(tabId);
  const run = verificationCaptureTail.then(() => captureVerificationEvidenceUnlocked(tabId));
  verificationCaptureTail = run.catch(() => undefined);
  verificationCapturesInFlight.set(tabId, run);
  const clear = () => verificationCapturesInFlight.delete(tabId);
  run.then(clear, clear);
  return run;
}
