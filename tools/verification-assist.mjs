// One approved attempt through an already connected CUA browser tab.
// This is an operator helper, not an unattended extension or a standalone CLI.
// The caller must obtain confirmation for the current challenge before passing
// approved:true. No approval, pixels, credentials, or tab contents are persisted.

// Shared read-only DOM callback for targeting and outcome checks. Keep it
// self-contained: CUA evaluates the callback without this module's closures.
export function readVerificationState() {
  const reviewerRoute = /^\/reviews\/claim-product(?:\/|$)/.test(location.pathname);
  const blockedRedirect = /^\/blocked(?:\/|$)/.test(location.pathname);
  const eligible = location.protocol === 'https:' && location.hostname === 'www.walmart.com' &&
    (reviewerRoute || blockedRedirect);
  if (!eligible) return { eligible: false };
  const visible = element => {
    if (!element?.getClientRects().length) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' &&
      style.visibility !== 'hidden' && style.visibility !== 'collapse' && style.opacity !== '0';
  };
  const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(element =>
    visible(element) && /robot\s+or\s+human/i.test(
      element.querySelector('h1, h2, h3')?.textContent || ''));
  const markers = [...document.querySelectorAll('#px-captcha, #px-captcha-wrapper')].filter(visible);
  const frames = [...document.querySelectorAll(
    'iframe[src*="captcha.px-cloud.net"], iframe[src*="px-captcha"], iframe[title*="human challenge" i]'
  )].filter(visible);
  const pressText = /press\s*(?:&|and)\s*hold/i.test(document.body?.innerText || '') &&
    /(?:robot\s+or\s+human|(?:verify|confirm).{0,30}human)/i.test(document.body?.innerText || '');
  const challengeVisible = blockedRedirect || dialogs.length > 0 || markers.length > 0 ||
    frames.length > 0 || pressText;
  const root = dialogs[0] || markers[0];
  const layout = dialogs.length ? 'modal' : blockedRedirect ? 'blocked' :
    challengeVisible ? 'page' : 'none';
  const candidates = root ? [...new Set([
    ...[...root.querySelectorAll('iframe')].map(frame => frame.parentElement),
    ...markers.filter(marker => marker === root || root.contains(marker))
  ])] : [];
  const targets = new Map();
  for (const container of candidates) {
    if (!visible(container)) continue;
    const rect = container.getBoundingClientRect();
    // These bounds match the inspected modal and full-page hold containers.
    // Unknown layouts remain visible/pending instead of receiving guessed input.
    if (rect.width < 200 || rect.width > 700 || rect.height < 50 || rect.height > 250) continue;
    const target = { x: Math.round(rect.x + rect.width * 0.6),
      y: Math.round(rect.y + rect.height * 0.5) };
    if (target.x < 0 || target.y < 0 || target.x >= document.documentElement.clientWidth ||
        target.y >= document.documentElement.clientHeight) continue;
    const hit = document.elementFromPoint(target.x, target.y);
    if (!hit || (hit !== container && !container.contains(hit))) continue;
    targets.set(`${target.x}:${target.y}`, target);
  }
  const productLinks = [...document.querySelectorAll('a[href*="/ip/"]')].filter(visible).length;
  const emptyResults = visible(document.getElementById('search-item-stack-no-results-header'));
  return { eligible, reviewerRoute, blockedRedirect, challengeVisible, layout, productLinks,
    emptyResults, reviewerReady: reviewerRoute && !challengeVisible && (productLinks > 0 || emptyResults),
    target: targets.size === 1 ? [...targets.values()][0] : null };
}

export async function runApprovedVerification(tab, {
  approved = false, holdMs = 8_000, settleTimeoutMs = 20_000, now = Date.now
} = {}) {
  if (approved !== true) throw new Error('Approval for the current challenge is required.');
  if (!Number.isInteger(holdMs) || holdMs < 1_000 || holdMs > 15_000 ||
      !Number.isInteger(settleTimeoutMs) || settleTimeoutMs < 1_000 || settleTimeoutMs > 30_000) {
    throw new Error('Verification timing is outside its bounded range.');
  }
  const result = { startedAt: now(), requestedHoldMs: holdMs, attempts: 0,
    verified: false, observations: [] };
  const before = await tab.playwright.evaluate(readVerificationState);
  if (!before?.eligible || !Number.isFinite(before.target?.x) || !Number.isFinite(before.target?.y)) {
    return { ...result, outcome: 'no-supported-visible-challenge', finishedAt: now() };
  }
  // The approved hold is sent once. A timeout or slow response never sends a
  // second hold, and eight seconds remains an observed trial, not a threshold.
  result.target = before.target;
  result.initialLayout = before.layout;
  result.attempts = 1;
  await tab.click([before.target.x, before.target.y], { durationMs: holdMs });
  result.inputCompletedAt = now();
  await tab.getAXState({ emit: false });
  let readySince = null;
  for (;;) {
    const state = await tab.playwright.evaluate(readVerificationState);
    result.observations.push({ ...state, at: now() });
    if (result.observations.length > 120) result.observations.shift();
    if (!state.eligible) { result.outcome = 'navigation-changed'; break; }
    if (state.challengeVisible && state.layout !== before.layout) {
      result.challengeChanged = true;
      result.outcome = 'verification-pending';
      break;
    }
    if (state.reviewerReady) {
      readySince ??= now();
      if (now() - readySince >= 1_000) {
        result.verified = true;
        result.outcome = state.productLinks > 0 ? 'cleared-with-catalogue' : 'cleared-empty-page';
        break;
      }
    } else readySince = null;
    if (now() - result.inputCompletedAt >= settleTimeoutMs) {
      result.outcome = state.challengeVisible ? 'verification-pending' : 'cleared-waiting-content';
      break;
    }
    // A real browser state read supplies fresh evidence while the server
    // completes verification. No additional site request or fixed sleep is used.
    await tab.getAXState({ emit: false });
  }
  result.outcome ||= 'verification-pending';
  result.finishedAt = now();
  result.settleMs = result.finishedAt - result.inputCompletedAt;
  return result;
}
