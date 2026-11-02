# Scanner efficiency proposal — revised October 28, 2026

Implementation follow-through: the owner subsequently approved the first fixes.
See [scanner-coverage-release-20261028.md](scanner-coverage-release-20261028.md)
for verified source2.26.4, backend activation and the pending Chrome reload.
The observations and proposal below describe the earlier baseline; they do not
establish a measured speedup of the new release.

The owner clarified the hard requirement: **scan every page of every department**.
Brands are optional and require evidence of saved time or improved discovery.
This revision supersedes the earlier recommendation to start with five-page
department turns. It is a review and proposed design; no scanner implementation,
live settings, tabs, external requests or deployment changes are part of this
revision. Earlier keyword ordering and stock/verifier decisions still apply.

**Current measurement addendum:** a subsequent live observation and controlled
collector replay found a concrete primary-card rejection bug involving image-alt
variant labels. The tested candidate restores the selected Free card while
excluding swatches. Read
[scanner-efficiency-evidence-20261028.md](scanner-efficiency-evidence-20261028.md)
for the fresh 11:50–12:20 CDT timing window, 34-case baseline/candidate replay,
coverage probes and the revised implementation order. That evidence takes
precedence over the earlier performance sample below. No candidate is installed.

## Non-negotiable coverage contract

- Every discovered department belongs to the mandatory work queue. Process every
  page and its late-rendered cards; neither brand searches nor early-page checks
  can replace the department scan.
- A department is complete only when its page receipts and terminal-page evidence
  establish completion. A failed, skipped, capped or interrupted target remains
  unresolved work. Advancing a queue cursor is not completion evidence.
- Save acknowledged progress before navigation/replacement, and resume after
  pauses, worker loss or restarts. Retry with backoff while other departments
  progress; never silently erase outstanding work or declare a complete round
  while mandatory departments/pages remain unresolved.
- The site can change inventory during a scan. All-pages traversal is a coverage
  requirement, not a promise of one simultaneous snapshot of every product. If
  Walmart hides results or makes pages inaccessible, report that limitation
  explicitly instead of claiming exhaustive coverage.

## Earlier measurements

The fixed 10:25:45.970–10:48:05.970 CDT baseline contains 107 distinct results-page
ready events in 22m20s: 37 home, 43 department and 27 keyword. Total 4.79 pages/minute;
the keyword share was 1.21 pages/minute. This is a measured sample, not a guaranteed
capacity or forecast for every workload.

After the keyword priority reset, Large Appliance read 6 pages. Document load to
last page-ready was 169.578 seconds; five native loads consumed 10.312 seconds total,
median 2.122 seconds. Rendering, reading, focus turns, pauses and ingestion dominate
the remainder. One measured department took 649 seconds to read 25 pages. Thirteen
measured department scopes triggered the code's 25-page warning. This is not proof
of a Walmart page cap: a saved run in catalogue-batch-limit-test-20261023.json
advertised 35 pages. Do not use 25 as an endpoint or an automatic brand trigger.

All tabs share a single selected-tab lease, even across windows. Reading/facets
request 10 seconds and navigation 3 seconds; current leases lack normal early release.
FIFO grants prevent independent simultaneous reading. Existing cards may be
ingested before selected reading, so an additional productive scope can still
improve discovery even when completed-page throughput barely improves. A fourth
tab is therefore a testable possibility, not a guaranteed doubling or guaranteed
regression. Actual exclusive-lease occupancy is not yet measured well enough to
establish the remaining capacity.

The 100 keywords are distinct. Similar names and suffix forms are not proven
redundant under Walmart search. At the original measurement, live 2.26.2 lacked
the pending card/auxiliary diagnostic events, and collector data dropped query
provenance. Those measurements cannot rank unique eligible finds per query or
establish an optimal term count. These are historical measurements, not a fresh
live-browser benchmark performed during this revision.

## Recommended direction

1. **Fix the reproduced primary-card rejection and verify full traversal.**
   Image-only variant descriptions currently discard a valid named Free primary;
   a controlled candidate passes the captured case and all 34 extraction checks.
   Then correct terminal evidence and outstanding-work accounting. Establish a
   baseline complete-round time only after proving that all required pages are
   represented. A faster partial round is not an improvement.
2. **Use whole-department scanning as the default baseline.** Finish department A
   from first to final page, then department B, retaining durable progress after
   interruptions. Remove the five-page-rotation recommendation as the first speed
   change. Chunking is a separate fairness/freshness option, not a demonstrated
   full-round speed improvement.
3. **Keep the 100 keyword searches and their applied priority order.** Measure
   their unique finds and service use before changing depth, cadence or membership
   of the pool. Do not let keyword priority restart unfinished department work.
4. **Keep three scanner tabs initially.** Profile actual reading-lease occupancy,
   waits, rendering, acknowledgement and target-start costs. If there is usable
   spare capacity, trial a fourth owned tab as a second department worker. Both
   department workers claim whole departments from one durable, deduplicated
   queue; do not divide by a fixed number of departments, since their sizes differ.
   Keep the change only if complete-round time improves with identical page
   coverage, stable operation and acceptable home/keyword service.
5. **Keep brands off unless a bounded experiment earns their cost.** The previous
   native BDQ trial returned one already-known butter churn and no incremental
   eligible ID; it provides no benefit evidence. Brands must not replace mandatory
   departments, starve unfinished departments or reset department progress.
   A 25-page warning alone does not justify enabling them.

The primary objective is time to finish the entire verified department round.
Time to first visit, time to first useful find and revisit intervals are separate
metrics. Gains from taking service away from home or keywords must be reported
as a tradeoff, not as additional capacity.

## Evidence required before enabling brands

Use a small explicit brand sample after establishing the full base scan. Record
the exact eligible IDs, scope and timestamps from both sources, the extra page
requests and time, and whether a brand find later appears in the same base round.
An item seen sooner through a brand is earlier discovery, not automatically an
item unavailable through departments. Changing stock/account eligibility can
confound non-contemporaneous comparisons.

Brands pass only if they demonstrably save net scan time, repeatedly find additional
eligible items, or find useful items materially earlier, while preserving full
department traversal and bounded service for all mandatory work. Measure any
increase in round/revisit time and
weigh it against the discovery benefit; it cannot be presented as a free speedup.
A smaller filtered result set does not itself save time when the complete
department scan is still mandatory:
the filtered requests are extra work. Any claim of saved time must identify and
measure what waiting or overhead it actually removes. Until then, keep brands off.

## Offline fairness model

An illustrative queue has departments A–F with 25, 25, 2, 1, 8, 4 pages. One serialized
page-service stream produces:

| Schedule | First visit to A/B/C/D/E/F | Activations | Total pages |
| --- | --- | --- | --- |
| Full-target serial | 1/26/51/53/54/62 | 6 | 65 |
| Five-page turns | 1/6/11/13/14/19 | 15 | 65 |
| Three-page turns | 1/4/7/9/10/13 | 25 | 65 |

All variants assert the same complete 65-page union with no repeated pages. All
finish at page-service slot 65 in this zero-overhead, single-worker model. Five-page
turns add nine target activations. Smaller turns expose later departments earlier
but do not reduce total work; real switching overhead can make completion slower.
The model excludes switching, loading, pauses, failures and changing inventory.
It demonstrates fairness only and does not justify changing the default schedule.
Reproduction: data/scanner-optimization-20261028/scheduling_model.py.

## Safe implementation boundary

Current checkpoints are lane-level. Before a multi-worker experiment, add a
durable department work ledger with page receipts, next unread position, owner,
generation and distinct pending/scanning/complete/unresolved status. Claim each
department once, and recover ownership after worker loss without losing pages.
Keep page completion after initial ingest, scrolling, late-card ingest, reading
and durable acknowledgement. A page-report acknowledgement alone is too early.
Preserve shared challenge/user pauses, pacing and stock admission protections.

Audit current pagination before accepting the baseline: content.js has a separate
100-page safety ceiling; totalPages() uses visible pager labels, and completion
uses the remembered total. The 25-page flag is only a heuristic. Verify actual
terminal controls and later pagination growth; reconcile any local ceiling with
the all-pages contract. Never remove a safety bound blindly or reinterpret a cap
as complete coverage. Failed/capped attempts must remain distinct from completed
departments in both the work ledger and status UI.

### Current code gaps found by this review

These are source-path findings, not evidence that every live department round
missed products:

- The 25-page heuristic does not terminate scanning. A saved 35-page pager proves
  the earlier universal-25-cap assumption was unsupported.
- totalPages() treats the maximum recognized visible pager label (up to 100) as
  a total. nextUnvisited() finishes once that remembered range is visited; that
  path does not separately prove an enabled Next control is exhausted.
- With no recognized total, the local 100-page ceiling ends traversal. The
  coverage-limited flag checks detected/stored total, not visited-page count.
  Consequently an unknown-total sweep can reach 100 populated pages and report
  complete without either terminal evidence or a limited-coverage flag. This
  violates the proposed all-pages completion contract and must be repaired before
  accepting any optimized implementation.
- The coordinator's completedTargets field is its advanced-target cursor. It
  includes discovery, duplicate bindings and failed/skipped targets, not just
  successfully exhausted departments. Recognized failures/caps make the final
  lane status partial, but that does not turn the cursor into a success count.
  Preserve a durable unresolved-work ledger and show separate successful,
  attempted and unresolved counts.
- Failed-target advancement clears its checkpoint, and the next round starts a
  fresh lane. Those departments are retried in later rounds, but their unfinished
  deep work is not retained as a durable pending range across round refreshes.
  Retain that outstanding coverage explicitly rather than relying on a fresh
  page-one retry to imply the previous round eventually completed.

Another department worker requires owned-worker/shard support across coordinator,
options, status, backend schemas and tests. Current code assumes exactly the
department and keyword auxiliary lanes; backend rejects a third auxiliary lane.
Opening an ordinary fourth reviewer tab does not create another scanner.

Acceptance must compare exact page and eligible-ID unions with complete serial
fixture scans, including departments longer than 25 and 100 pages, changing pager
windows, late cards, missing pages, failed acknowledgement and partial failures.
Exercise shared pause, worker/browser restart, duplicate/stale messages, retries
and worker loss. Required result: no lost pages or falsely completed departments.

For a four-worker trial, use matched A–B–A runs with the same targets, full depth,
settings, aggregate request budget and safeguards. Measure complete department
round wall/active time, deepest-page completion, unresolved work, home/keyword
round and revisit times, lease occupancy, failures and challenges. First visits
and unique eligible items/minute are secondary. Improvement must exceed ordinary
run-to-run variation; otherwise retain the simpler whole-department baseline.

This revision changes the proposal and handoff only; the illustrative model was
rerun successfully. It does not implement a new scheduler, remove a page limit,
enable brands or add a scanner worker.
