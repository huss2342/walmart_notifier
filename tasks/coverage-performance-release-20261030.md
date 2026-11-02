# Coverage and performance fixes — October 30, 2026

## Accepted scope

Owner authorized fixing the findings in the October 30 performance audit, with
coverage and performance as the controlling requirements. Department priority
changes were permitted only if they improve those outcomes. Every configured
target, acknowledged page, unresolved range, late-card intake, input guard and
verification guard remains required. Normal navigation/refresh pacing is retained.

Baseline and task evidence: `data/coverage-performance-fix-20261030/`. The checkout
already contained substantial unrelated work. Three disjoint coding assignments
covered content recovery, focus scheduling and delivery recovery; root owned
coordinator/contracts, integration, testing, docs and the scanner backend rollout.
No commits, resets or branch changes were made.

## Delivered behavior

- Blank bound targets request guarded rendering opportunities. The active render
  budget excludes denied focus, revoked turns and actual input pauses; a separate
  finite opportunity limit prevents indefinite waiting. Genuine empty results
  still require stable, visible, bound first-page evidence.
- Coordinator liveness is separated from useful progress. Responding scheduler
  waits receive a bounded opportunity; fresh heartbeats and future action timers
  cannot renew it forever. Actual input and manual browsing still defer work.
  Missing/stale-generation receivers retain finite recovery.
- Native freshness-extraction errors no longer kill the navigation poll with its
  lock latched. Recovery persistence errors produce bounded classified failures.
  Current document/generation checks and stale-card exclusion remain mandatory.
- Observed document HTTP errors receive an explicit failure code and paced
  transport recovery (30/60-second retry waits). Acknowledged pages are retained;
  failed interior pages remain unresolved. No hard page cap or fabricated terminal
  result was added.
- Exact release and lease expiry send an advisory hint to the FIFO head. The
  receiver wakes only the matching pending focus retry, or its waiting render/read
  relay, then requests fresh authority. Hints cannot select a tab, bypass normal
  page pacing, or authorize input/navigation. Existing polling remains a fallback.
- Retry/skip records retain tab, generation, round, numeric target index, attempt,
  document identity and observable HTTP status. Department/keyword intake records
  include duration and new/duplicate counts. Search strings are not exported.
- Sender errors are classified before recovery persistence. Secondary failures
  preserve uncertain delivery state; a bounded cancellable loop backoff prevents
  a dead sender or tight retry loop. Health distinguishes successful storage
  polling, historical failures, DM success and unavailable queue statistics.
  Dispatch claims retain original due/retry deadlines for latency diagnosis.

The existing carried-unresolved-first recovery ordering remains. No departments
or keywords were removed or deprioritized based on guesses. New attribution can
support a later measured priority change without reducing required coverage.

## Important audit clarification

The same item behind the apparent 20-minute dispatch outlier had already reached
Pro, its original category and its value route in 2.2–2.4 seconds. The later home
category dispatch inherited the original first-seen due time. This is evidence
of a later route becoming eligible, not a 20-minute delay in the initial alert.
Exact route enqueue timing was not retained, so no due-clock behavior was changed.
Evidence is in `delivery-tail-evidence.json` under the task evidence directory.

## Verification

- Final complete JavaScript suite: **913 passed**, no skips, in 19.36 seconds.
  Used `node --test --experimental-test-isolation=none --test-timeout=15000 tests/test_*.mjs`.
  Sandbox process spawning prevented default isolation; execution without child
  isolation worked. Independent focused checks also ran successfully.
- Complete Python suite: **2,236 passed, one existing platform skip**, 196.17s.
  Used the repository virtual environment, no cache provider and a task-specific
  temporary directory. Windows sandbox temporary-directory ACL errors required
  the established normal-permission test execution; those errors were recorded
  separately and were not treated as source failures.
- Real Chromium local replay: **21 checks passed**, no runtime warnings/errors.
  Exercises real DOM empty evidence, hydration changes, hint freshness, fresh
  authority, ordinary pacing and a rendering opportunity. Chrome storage/runtime
  and HTTP status are fixtures; this is not a live Walmart speed measurement.
- Equal-work scheduling replay: 18 identical FIFO turns took **75.1s polling
  versus 51.1s with hints**. Handoff idle time was **24s versus zero**. This isolates
  scheduling; it is not a measured 32% improvement in production page throughput.
- Repository Ruff, JS syntax, generated native protocol and server startup pass.
- Maintained regressions reproduced the important failures before repair.
  Independent review caught two liveness-renewal loopholes; both were repaired
  with refreshed-heartbeat/denial regressions. Final independent review approved
  the scanner changes, including 142 coordinator/workspace and five hint checks.

## Deployment and activation

The other active solar-alert investigation had explicit human authorization to
coordinate and combine overlapping sender updates. Its deployment preserved the
new minimum-value/bulk-request features and activated the combined sender fix on
image `5418be51ee3b`. Its receipt is
`data/request-notifications-20261030/activated.json`.

The scanner backend overlays only `src/scanner_control.py` and
`src/browser_diagnostics.py` on that exact image. Offline/no-network validation
passed. Image **0c9b5a503cce** activated healthy at **17:42:23 CDT**. Environment,
host configuration, mounts, networks, saved settings and launch configuration
were verified preserved. Postdeployment in-memory schema and real health checks
passed: zero due/failed channel jobs, personal sender running, zero dropped
monitor events. At the initial backend deployment the live browser report still identified 2.26.7 and
manual-browsing focus guards; no live scanner speed gain is claimed. Rollback tag:
`reviewer-notifier:before-coverage-performance-20261030`.

The initial extension source version was **2.26.8**. At that handoff activation was pending:
Chrome's browser security policy blocks access to `chrome://extensions`, so the
agent cannot reload it. Reload Reviewer Item Relay, confirm 2.26.8, then refresh
the existing scanner tabs. Do not restart sweeps or clear saved unresolved work.
After activation, verify new-version reports, matched retry/intake receipts and
useful progress across all three lanes before claiming live performance gains.

All coding/review workers have finished. The isolated replay tab/server will be
closed after evidence capture. The source baseline, source hashes, task diffs,
red/green logs, full checks, deployment receipt and health checks are retained
under `data/coverage-performance-fix-20261030/`.


## Live activation follow-up and scope recovery

Owner confirmed reloading **2.26.8** and refreshing the scanners. At approximately
18:01 CDT, fresh scanner and diagnostic reports identified 2.26.8, all three
receivers became reachable, and intake resumed. Retained new-version receipts
show 28 home, 26 department and three keyword ready pages in the observation.
These are observational receipts across generations, not distinct catalogue
coverage or a controlled speed comparison.

Live inspection exposed a remaining pre-existing keyword failure: native Next
changed `q=Dog&queryNups=Dog&page=5` to page 6 with
`affinityOverride=store_led`. The result had rendered cards, but the strict assigned
scope correctly rejected it and made the receiver passive. The coordinator then
classified the wait as unresponsive. No modified-scope cards were accepted.

Source **2.26.9** adds narrow recovery for this observed modifier drift. It requires
an owned tab, matching generation, an inactive responding receiver, an acknowledged
checkpoint, a bound target and exactly the single known modifier difference.
Actual input and verification defer recovery. Recovery retains the first missing
page and original target; it marks only that target for paced canonical URL
pagination so the same native scope change is not repeated on every page.
Other queries, filters, duplicate and unknown modifiers remain unauthorized.
The scope guard was not weakened and all prior scope-rejection tests remain.

Six new checks cover checkpoint recovery, fresh input after the stale deadline,
changed/ambiguous scopes and the scoped URL fallback. Original failures were
reproduced before the repair. Independent review caught a stale inactive/input
case; the strengthened regression failed before its correction and now passes.
Final independent review: APPROVE, seven focused checks passed.

Final complete JavaScript suite: **919 passed**, no skips, 19.77 seconds.
Affected Python contract/scanner suite: **614 passed**, 2.32 seconds. The preceding
complete Python baseline remains **2,236 passed, one existing platform skip**;
the only subsequent production Python change adds one fixed failure-code enum.
Repository Ruff, syntax, native protocol and startup checks pass.

Compatible backend image **ccf65816deaf** activated healthy at **18:11:06 CDT** over
exact **0c9b5a503cce**. Only `src/scanner_control.py` changed. All runtime config,
settings, mounts, networks and environment were verified preserved. Live health
and in-memory acceptance of the new failure code pass: zero due/failed channel
jobs, personal sender running, zero dropped monitor events. Rollback tag:
`reviewer-notifier:before-affinity-recovery-20261030`.

**Current activation step:** owner must reload Reviewer Item Relay to **2.26.9**
and refresh scanner tabs, preserving sweeps and unresolved history. Chrome policy
still blocks agent access to the extension controls. A follow-up reload request
has been sent. Verify the new version and actual affected-target progress before
calling the keyword recovery live-verified. No new customer messages or synthetic
telemetry were sent.

Follow-up evidence: `affinity-{red,green,input-red,js-full-final,python}.txt`,
`affinity-recovery-before/`, `activated-affinity.json`, `activation-events-2268.json`
and `live-activation-summary.json` in the existing evidence directory.
