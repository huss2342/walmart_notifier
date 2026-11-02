# Verified empty targets and scanner focus progress — October 30, 2026

## Accepted scope

The owner authorized distinguishing genuinely empty keyword/department results
from scan failures, and removing scheduler contention that causes long stalls.
The October 30 restart was manual and is outside this work.

Preserve full configured coverage, acknowledged checkpoints, manual browsing,
trusted input and verification guards. Verify a current, bound target with a
stable visible no-results state before counting it empty; blank, loading,
HTTP-failure and interior-gap pages must not silently become completed targets.
Do not change refresh/navigation pacing or add product-page requests.

## Implementation and evidence

Source implemented and verified; live browser activation remains pending.
Baseline files are preserved under
`data/scanner-reliability-20261030/before`; Git HEAD at start:
`a14671213161f87afbc743e7a029640f2d3b7820`. The working tree already contains
unrelated uncommitted work, which must remain intact.

Disjoint assignments: focus coordinator and maintained adversarial tests;
auxiliary content/coordinator and empty-results tests; root owns status-contract
integration, version/docs, integrated verification and release preparation.

The optional numeric `emptyTargets` status field reports positively verified
empty targets among completed targets. Older reports omit it rather than
inventing an empty-result count. Backend validation must be activated before
the new extension reports it.

Required evidence: failing reproductions, focused and full regression checks,
real-browser local replay, independent review, and clear distinction between
source verification and live browser activation.

## Delivered behavior

- First-page auxiliary no-results can complete as `emptyResult: true` after two
  stable, visible reads two seconds apart. The result must belong to the current
  bound target and document, with no product links/offers, visible loading,
  login/error indication, observable HTTP failure, later-page indication or
  acknowledged pages. Main-scoped visible text is required; hidden/footer text
  cannot prove emptiness. Positive proof expires after ten seconds and is
  rechecked after storage boundaries. Cards appearing during the check cancel it.
- The coordinator validates the durable target/generation-matched zero-page
  receipt before recording `result: empty`. `emptyTargets` counts these outcomes
  separately from unresolved coverage, in local status, mirrored status and Options.
- Hidden no-results requests guarded focus. A granted three-second turn schedules
  its first observation after 100 ms, allowing confirmation at 2.1 seconds; denial
  retries remain one second and hidden waiting is bounded. Input/verification
  still defer work. Broader blank-page render/probe accounting is not repaired
  by this slice, and genuine failures remain unresolved.
- A FIFO non-head request no longer owns the acquisition lock while yielding to
  the head. This fixes repeated `busy`/`waiting-turn` starvation while preserving
  finite leases, exact releases and browsing/input/verification guards. Maintained
  concurrent-order tests show bounded service at the fixture's polling cadence;
  they are not a universal live latency guarantee. Normal page pacing is unchanged.

## Verification and review

Initial empty-classification and focus-contention regressions failed against the
saved baseline before implementation. Final JavaScript suite: **877 passed**, no
skips. Python: **2,166 passed, one existing platform skip** (2,167 collected).
Repository Ruff, JavaScript syntax, native protocol generation, server startup
and scoped whitespace checks passed.

Real in-app Chromium localhost replay: **15 checks passed**, no console warnings
or errors. It exercises real rendered main/hidden/footer, loading, sign-in, error,
late-card states, stable timing and the persisted completion message. Chrome
storage/runtime are fixtures; it does not prove live Walmart activation.

Independent read-only reviewer approved the frozen source after **79 focus** and
**27 affected** checks. Review caught a first/second-read lease boundary; a failing
regression now proves the 2.1-second confirmation is inside its three-second turn.
All coding workers are finished. Evidence, task-only diff, hashes, tests and local
replay are under `data/scanner-reliability-20261030/`.

## Activation and resumption

Backend overlay changes only `src/scanner_control.py` over exact live image
`fdf363e44b6e`; no dirty-checkout rebuild. Image built and validated offline.
The first activation automatically rolled back because Compose represented unset
DNS fields as null instead of empty arrays. Comparison confirmed those three
fields were the only difference. The deployment check now canonicalizes only
unset DNS representations, retains all real overrides and compares everything
else exactly. Final image **6b060cb1d7c8** activated healthy at **14:10:17 CDT**.
Host configuration (with unset DNS representation normalized), mounts,
environment, settings, networks and launch configuration are preserved.
Live verification: successful relay age four seconds, zero due/failed Discord
items, personal sender running. Deployed validator accepted the new field in
memory without writing synthetic telemetry. Receipt: `activated.json`.

Source version **2.26.7** requires reloading Reviewer Item Relay in
`chrome://extensions`, then refreshing the scanner tabs. Do not restart sweeps,
reset settings or clear unresolved history solely for activation. Existing failed
targets become verified empty only when actually checked again. Confirm live
reports identify 2.26.7, new explicit empty receipts/status appear naturally,
and three-scanner useful progress continues before claiming live improvement.

Rollback image: `reviewer-notifier:before-scanner-reliability-20261030`.
The source baseline is in `before/`. Do not remove unrelated work when reverting.
