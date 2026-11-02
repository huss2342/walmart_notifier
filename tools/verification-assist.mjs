// One approved attempt through an already connected CUA browser tab.
// This is an operator helper, not an unattended extension or a standalone CLI.
// The caller must obtain confirmation for the current challenge before passing
// approved:true. No approval, pixels, credentials, or tab contents are persisted.

import observation from '../extension/verification-observation.js';

// The default operator policy preserves this exported, serializable callback.
export const readVerificationState = observation.observePage;

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
