# Reduce avoidable scan loads — Plan A

Accepted October26: implement the owner's four levers without reducing page
coverage, disabling scrolling/focus or changing verification input. Lower flag
frequency is a hypothesis to measure, not a promised outcome.

Current bounded baseline2.23.13:300 events spanning1793050671396–1793051258479;
42 URL fallbacks (37 native-backoff,4 native-stalled,1 missing-control),34 native
attempts,30 navigation-ready. This confirms cooldown reload amplification remains.
Saved15-second page spacing already has variation; ordinary completed-cycle and
auxiliary restart deadlines are fixed. Initial keyword offset exists, but repeated
auxiliary starts can coincide.

Implementation/acceptance:
- A no-op control gets one paced retry; a started route retains its full render
  timeout and is never clicked twice while its results are pending. Cooldown
  becomes60seconds. During cooldown, wait for a bounded probe instead of repeatedly
  generating full loads. Exhausted/missing controls retain finite URL recovery.
- Stable0–10% additive cycle jitter; configured page spacing/rest floors and
  refresh0 stay respected. Repeated heartbeat/worker scheduling cannot move a
  cycle's deadline indefinitely.
- Persist an auxiliary start reservation so initial/next-target/recovered/restart
  navigations are separated by at least the saved page interval.
- Persist cumulative counters separately from300-event history. First observed
  page per tab is initial; subsequent changed document/page acknowledgements are
  successful transitions. Count shared challenge episodes and open/closed downtime.
  Old history is not backfilled; report the measurement boundary and denominator.

Ownership: manager content/runtime-policy/auxiliary/background wiring, related
tests, manifest/CI/docs/task records; plan_a_metrics worker exclusively diagnostics.js,
browser_diagnostics.py, test_browser_diagnostics.py and new test_scan_metrics.mjs.
Shared metric contract is recorded in worker assignment; no user options required.
No commits/pushes or unrelated dirty files. Verification: meaningful RED/GREEN,
declared full suites, actual browser fixture; deploy backward-compatible backend
before activating the new extension; live comparison requires owner reload and a
sufficient observation window. Do not claim fewer challenges from a short clear run.

## Implemented and verified source2.24.0

All four levers implemented. Normal page jitter already existed and remains;
completed-cycle jitter and durable auxiliary-start reservation close the gaps.
The production worker supplies first-start offsets of one saved interval for
departments and two for keywords (one when departments disabled). Standalone
coordinator consumers retain their existing initial-now behavior unless supplying
the initialStartDelay dependency. Existing queues are not reset on this upgrade.

Review found two late-response races (focus-await and fallback-persistence) and a
metric report-initialization/queued-event race. Each has RED/GREEN regressions.
Native retry stays locked to the original freshness predicate if its first
response arrives late; a changed route is never clicked twice while rendering.
Noop attempts still have5seconds each and changed routes30seconds; retry is
spaced normally. Cooldown waits at most60seconds before probing, including legacy
ten-minute records. Missing/exhausted controls retain bounded URL recovery.

Metrics contract is optional schema1 report.metrics: measurementStartedAt,
initialPages, successfulTransitions, challengeEpisodes, nativeAttempts, urlFallbacks,
nativeBackoffFallbacks, nativeStalledFallbacks, nativeRetries,
completedVerificationDowntimeMs, verificationDowntimeMs, verificationActiveSince,
episodesPer100Transitions. Zero denominator is null. A current outage at initial
measurement is one partial episode; elapsed time before that boundary is excluded.
Dedup retains64 recent numeric tab/document/page identities; no raw URLs/products
or credentials. Report/event writes share one serialization queue, and metric
timestamps clamp to the established baseline without rewriting trace timestamps.

Validation on final source:
-639 extension tests pass, zero fail/skip; full CI list now includes scan metrics.
-1739 Python tests pass, one platform skip (quiet progress receipt1740 cases).
-Ruff, all extension/helper JavaScript syntax, scoped whitespace and server --help
 startup gate pass. Tests were adapted to waits/deadlines without deleting their
 checkpoint/generation/ownership assertions. No source tests were skipped.
-Real in-app browser: production source+real DOM controls recovers from a no-op
 with exactly one retry and no document load; focus ordering/busy resume/stale
 focus and stable jitter pass. Accelerated no-op clock/wait is explicitly labeled;
 VM regressions verify real configured15-second spacing. Existing full browser
 regression also passes varied scrolling, shared verification pause, stale-role
 rejection, category fallback, reload cleanup, scope and live ingestion.
-Both browser fixtures have zero warning/error logs. Test tabs closed and owned
 loopback fixture server stopped. Production Chrome/scanner settings untouched.

Receipts: data/plan-a-extension-tests.txt, plan-a-python-tests.txt,
plan-a-browser-results.json, plan-a-browser-proof.png; focused RED/GREEN logs in
data/plan-a-focused-initial.txt and plan-a-focused-green.txt. Some subsequent RED
race cases were emitted directly to tool history, followed by passing regressions.

Backend: thin image reviewer-notifier:plan-a-20261026 (also local) copies only
browser_diagnostics.py over the prior live image. Deployed with compose, healthy.
Before/after comparison preserved environment hash, mounts, protections, network
names, user and command. Live container imports new event support. Deployment
context/receipts in data/plan-a-20261026/deployment. Rollback image
reviewer-notifier:before-plan-a-20261026; retag it local and recreate notifier with
the same compose configuration if required. No delivery/credential changes.

Implementation and local verification complete. Owner activated2.24.1, which
includes Plan A. Live status/diagnostic mirrors and report.metrics confirm the
upgrade; all three lanes advance. See focus-reading-20261026.md and its live
receipts. Initial metric snapshot:9 successful transitions,0 URL fallbacks,0 new
challenge episodes. No duplicate reload/Options request. A comparable workload
window is still required to evaluate challenge frequency and fallback reduction.
Do not infer lower challenge odds from a short clear run. No automatic follow-up
or new recurring automation was requested or created.
