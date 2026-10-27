// Pure timing policy shared by the worker, reviewer page, and Options.
function effectiveRefreshMinutes(settings) {
  const minutes = Number(settings.refreshMinutes);
  // The coordinator pauses while verification is unresolved. Historical
  // challenges never change the user's timing after the page is usable again.
  return minutes > 0 ? minutes : 0;
}

function effectivePageSeconds(settings, sweep) {
  let seconds = Math.min(60, Math.max(5, Number(settings.pageDelaySeconds) || 15));
  const minutes = effectiveRefreshMinutes(settings);
  // Allocate about 75% of the regular-mode interval to browsing. Never skip
  // pages or shorten a user's delay to try to meet the target.
  if (sweep && sweepRecordMode(sweep) === 'none' && minutes > 0 && sweep.total > 0) {
    seconds = Math.max(seconds, Math.min(90, minutes * 60 * 0.75 / sweep.total));
  }
  return seconds;
}

function pageNavigationDelay(settings, sweep, lastNavigationAt, now = Date.now(), random = Math.random()) {
  const minimumMs = effectivePageSeconds(settings, null) * 1000;
  const intervalMs = Math.max(minimumMs,
    effectivePageSeconds(settings, sweep) * 1000 * (1 + (random * 2 - 1) * 0.25));
  // Loading and delivery already consume time between navigation starts. Only
  // a valid timestamp from this clock may reduce the remaining wait.
  const elapsedMs = Number.isFinite(now) && Number.isFinite(lastNavigationAt) &&
    lastNavigationAt > 0 && lastNavigationAt <= now ? now - lastNavigationAt : 0;
  return { delayMs: Math.max(0, intervalMs - elapsedMs), intervalMs, elapsedMs };
}

// Stable per completed cycle: heartbeat/worker restarts cannot keep resampling
// and postponing a deadline. This small deterministic sample needs no secrets.
function cycleJitterMs(intervalMs, cycleAt, salt = 0) {
  if (!(Number.isFinite(cycleAt) && cycleAt > 0 && Number.isFinite(intervalMs) && intervalMs > 0)) return 0;
  const seed = (Math.trunc(cycleAt) + salt * 7919) % 2147483647;
  const sample = (seed * 48271 % 2147483647) / 2147483647;
  return Math.round(intervalMs * 0.1 * sample);
}

function sweepRestartAt(settings, now = Date.now()) {
  const minutes = effectiveRefreshMinutes(settings, now);
  const done = Number(settings.lastSweepDone);
  const started = Number(settings.lastSweepStartedAt);
  if (!(minutes > 0 && done > 0 && started > 0)) return 0;
  // A scan that overruns its target still gets a real break (1–5 minutes).
  const rest = Math.min(5 * 60_000, Math.max(60_000, minutes * 60_000 * 0.2));
  const interval = minutes * 60_000;
  return Math.max(now, started + interval + cycleJitterMs(interval, done),
    done + rest + cycleJitterMs(rest, done));
}
