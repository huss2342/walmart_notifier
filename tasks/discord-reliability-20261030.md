# Discord delivery reliability and housekeeping

October 30, 2026, America/Chicago. Owner authorized auditing intake, delivery,
errors and serious lag, fixing demonstrated problems immediately, and related
quality-of-life/housekeeping. Complete: selective repair activated and verified.

## Live audit

- All **1,096 currently available, unseeded products first captured in the prior
  24 hours** have a confirmed `pro-all` send. This establishes delivery for captured
  products; it does not establish exhaustive Walmart discovery or mobile push.
- Read-only Discord GETs passed for the latest stored message on all **19 active
  feed routes**, including correct product footer IDs, review/product links and
  no user mentions. Three existing personal-alert messages also matched their
  ledger products and personal format. No synthetic customer messages were sent.
- Last-hour Pro delivery: 72 destination items, p50 1.983s, p95 5.165s,
  max 5.346s. Free: 52 items, p95 3.720s, max 4.431s **after** scheduled release.
  Free's 24-hour delay remains intentional. No active paid backlog, failed channel
  jobs, stale due work, or dropped monitoring events at checked snapshots.
- Historical 24-hour Pro maximum 1,202.256s belongs to a later category route
  for a product already sent to Pro/original category in about two seconds.
  `due` uses original first detection even when a changed observation adds a route;
  the old schema does not record route creation time, so this does not prove a
  20-minute sender stall. No queue summaries retained a backlog over 30 seconds.
- All eleven expired price/category destinations for newly captured products had
  confirmed original Pro sends. Changed route eligibility does not reset the
  established first-seen expiry or justify replay. Historical SQLite/provider
  errors and throttles are retained; subsequent sends and current polls confirm
  recovery. The earlier SQLite recovery repair predates this slice.
- Database `PRAGMA quick_check` passed. Existing one blocked-DM recipient, one
  uncertain personal send held for review and one historical expired personal
  alert remain intact; no blind replay or member preference change.
- Scanner 2.26.9 is active and all three lanes progressed during observation.
  Auxiliary rounds still retain unresolved render/scope targets; this delivery
  audit does not mark those separate scanner coverage gaps fixed.

## Repairs

`discord_bridge/request_delivery.py`: reproduced a definitively rejected cached
DM message POST returning 404 while leaving the member `ready`. Reobservations
could repeatedly rearm a futile send to the same missing destination. Such a
rejection now records `destination_missing` and marks `needs_test`, requiring the
existing explicit `/request test-dm` recovery. Successful testing restores fresh
delivery. Ambiguous/accepted sends remain held, and member/audit errors retain
their existing bounded retry behavior. Nine new regression cases cover this.

`src/notifiers/discord.py`: `/health` now exposes `discord.worker` liveness,
completed poll time/status, current cycle duration, last confirmed send, sanitized
failure class/time and recovery time. It distinguishes healthy idle polling from
a stopped/stalled sender without an invented stale cutoff. No lock spans delivery
I/O, and callback errors cannot erase a confirmed send. Five new regressions
cover idle, send/error/recovery, stopped state, long cycle and callback failure.

README documents the health and recovery behavior. Delivery policy, routing,
filters, expiry, nonces, privacy checks, saved watches and Free scheduling remain.

## Verification and activation

- Red: three destination-recovery failures and five missing worker-health cases.
- Green: 214 affected request checks, 98 worker regressions, 146 referral checks.
- Full checkout: **2,487 passed, one existing platform skip**, 208.22s.
- Exact rebased candidate: **363 passed**, 44.63s. Imports explicitly pinned to
  candidate modules. Repository Ruff, scoped formatting/startup/whitespace pass.
- Independent read-only code and release review: **APPROVE**, no required findings.
  Optional smoke timing race removed with bounded poll-completion wait.
- Actual built Linux image, network disabled: real background channel sender,
  durable SQLite queues, missing-DM pause, explicit retest and fresh alert pass
  with fake provider transport. No production error injection or test DMs.

Activated **23:28:59 CDT**, image **ab7e6c0ef42a**, only the two modules over exact
concurrent referral release **1d293c53b9f7**. Initial build guard caught that the
base had changed; candidate was rebased, retested and rebuilt before activation.
Full host configuration, environment hash, mounts, networks, launch configuration,
scanner and relay settings compare unchanged. All **12 watches are identical by
hash**. Referral worker remains configured/running without errors.

Post-activation: container healthy; channel sender running/poll `ok`, confirmed
natural send, no worker error; personal Gateway connected/ready and sender polling
`ok`; zero due/failed channel jobs, zero due personal alerts, zero dropped logs.
Observed recent Pro max 2.183s and Free max 1.556s after scheduled release. Health
responded in 0.583s. Existing messages naturally sent after restart were read back
from Discord. No historical jobs were replayed or queues cleared.

Evidence: `data/discord-reliability-20261030/` contains read-only audit scripts and
redacted results, before/after sources and selective patches, candidate manifest,
full/pinned test logs, offline smoke, configuration-preservation and activation
receipts, saved-watch hashes, and post-activation provider checks. First exact
candidate run caught a patch-file line-ending mismatch; corrected before release.
Rollback: `reviewer-notifier:before-discord-reliability-20261030` (exact referral
base; removes these two repairs only). Drift-checked helper: `deploy.py`.

All assigned workers/tests finished. Offline smoke containers auto-removed.
No production resources remain from testing; normal notifier service is running.
