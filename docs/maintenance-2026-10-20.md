# Regular-mode navigation and verification resilience — October 20, 2026

## Scope and state

Implemented in extension version **2.6.0** after the owner authorized the
navigation, pacing, verification detection and diagnostic improvements. Page
coverage, search modes, alert filters, dedupe and notification delivery remain
available. Existing uncommitted work was preserved; no commit or deployment was
performed. The Python notifier did not require changes for this slice.

## Behavior

- Prefer enabled, visible numbered pagination controls with an exact link
  destination or page label. Fall back to document navigation when unavailable.
- Hold extraction during client-side navigation until the destination and
  changed cards agree. A stalled click gets one guarded fallback after 30
  seconds. Verification, user interaction and role changes cancel pending work.
- Observe inline verification even in passive reviewer tabs. Detect blocked
  redirects as before. Cached cards under an overlay cannot clear the pause.
- Spread regular-mode pages over roughly 75% of the effective refresh target,
  with a 90-second baseline cap and the configured delay as a floor. A 17-page,
  10-minute scan uses about 26 seconds between pages, with jitter, before load
  and delivery time. This is a target, not a completion deadline.
- Require a 20% rest after completion, clamped to 1–5 minutes. Preserve explicit
  auto-refresh zero, including alarms already queued when it is disabled.
- Immediately slow page spacing after the first challenge. Preserve the
  existing 24-hour adaptive 30/60/120-minute refresh targets without rewriting
  saved settings. Unusable-page recovery backs off to five minutes and resets
  after a successful relay. Failover requires usable reviewer markup.
- Serialize tab-event lease changes with challenges and restarts so delayed
  events cannot restore revoked generations. Watchdog recovery retains a
  pending navigation's destination instead of leaving extraction locked.
- Keep up to 300 allowlisted local diagnostic events, filtering entries older
  than seven days on writes and export. Export schema version 1 includes the
  extension version, safe settings, challenge timing and event history.
  Credentials, cookies, product details, raw URLs and error bodies are excluded.

## Verification

- `node --test --test-isolation=none tests/test_extension_runtime.mjs`:
  **67 passed**, including regressions demonstrated failing before fixes.
  The default Node runner could not spawn its isolated child in the sandbox;
  disabling test isolation changes the runner, not assertions or tested code.
- `.venv/Scripts/python.exe -m pytest --basetemp=data/qa-tests-20261020-unsandboxed -p no:cacheprovider --tb=short --maxfail=3`:
  **408 passed**. The initial sandbox run could not reopen Python 3.13's
  restrictive Windows temporary directories; the full suite passed outside
  that restriction with a separate temporary base.
- Ruff, syntax checks for all extension scripts, manifest JSON and referenced
  content-script files, server `--help` startup, and Git whitespace checks pass.
- Real in-app browser against loopback fixtures: native pagination, stale-card
  protection, challenge cancellation, cached-card readiness and hidden-marker
  handling pass. Options renders the effective 26-second spacing and generates
  credential-free diagnostic JSON. Both fixture pages reported no console
  errors or warnings. The fixture intercepts the download to inspect its JSON;
  it does not write a real user download or contact Walmart/the notifier.
- Independent read-only review approved after the lease-race and watchdog
  findings were fixed and covered by regressions.

To repeat browser QA, run `python tests/serve_extension_fixture.py`, then open
the two loopback URLs it prints. The server serves only allowlisted fixtures
and extension assets on port 8879. Stop it with Ctrl+C when finished.

## Activation and remaining evidence

Reload Reviewer Item Relay at `chrome://extensions`, confirm **2.6.0**, then
reload the reviewer tabs so worker and content scripts match. Existing saved
settings remain intact; a recent verification pause/backoff remains in effect.
No notifier container rebuild is needed for these extension changes.

The user's Chrome session was unavailable to the browser connector. Actual
Walmart pagination compatibility, long-running timing and changes in prompt
frequency therefore remain unverified. These changes do not guarantee removal
of verification prompts. If another occurs, export diagnostics promptly from
Options; the capped history is designed to preserve the activity leading up to it.
