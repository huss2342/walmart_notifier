# Coverage and performance audit — October 30, 2026

The highest-value work is scanner reliability and selected-tab scheduling.
The collector is generally much faster than the browser pipeline. Current
coverage still has unresolved targets, and two keyword targets were skipped
after the latest extension became live. Fixing those failures can improve both
coverage and the amount of useful work completed per hour.

This was a read-only operational investigation. No application source, settings,
scanner state, deployment, notifications, browser navigation or claims changed.
Analysis artifacts and this report are the only new files.

## Evidence and scope

Frozen trailing window: **October 29, 15:39:13 through October 30, 15:39:13 CDT**.
Read 93,528 browser events from 20 current/archived files and 17,937 delivery
events from three files. No malformed lines or exact duplicate rows were found
in the files inspected. Events outside the window were excluded. Snapshots are
point-in-time observations and do not reconstruct historical status.

Live telemetry first identifies extension **2.26.7 at 14:11:31 CDT October 30**.
The previous handoff's browser-activation-pending note is therefore stale.
The 24-hour sample mixes 2.26.6 and 2.26.7. Their work mix, user activity and
operational conditions differ; this is not a controlled before/after benchmark.

Coverage means verified scanner coverage, not test coverage. Page counts below
are distinct `(tab, generation, requested page)` ready receipts. They establish
acknowledged page-reading progress, not distinct catalogue content, product
recall, complete departments, or availability of every item to every member.

## Where time goes

| Measure | Home | Departments | Keywords |
| --- | ---: | ---: | ---: |
| Ready-page receipts, 24h | 2,145 | 2,157 | 1,292 |
| Ready receipts/minute, 24h | 1.49 | 1.50 | 0.90 |
| Consecutive-page ready interval, median | 30.44s | 30.76s | 34.37s |
| Consecutive-page ready interval, p95 | 69.53s | 68.54s | 96.17s |
| Successful native navigation, median | 2.03s | 2.03s | 2.34s |
| Native-load-to-ready interval, median | 16.69s | 17.39s | 18.15s |
| Wait reported at a focus grant, median | 8.66s | 11.38s | 12.11s |
| Released scroll-turn duration, median | 3.10s | 3.09s | 3.16s |

These are separate distributions with different sample populations. Do not add
their medians into a fabricated waterfall. Load-to-ready includes scrolling,
settling and scheduler waits. Focus waits are elapsed caller waits, not measured
idle CPU or a promise that all of that time is removable.

Home ingest acknowledgement: **0.184s median, 0.641s p95, n=2,228**.
Auxiliary ingest duration is not separately logged. Native-navigation samples
describe successful transitions; silent failed transitions are excluded.

All lanes combined produced **5,594 ready receipts, 3.885/minute** over the day.
The latest hour produced **271, 4.517/minute**; the latest half-hour produced
**137, 4.567/minute**. These windows contain different targets and are not
attributable speedups from 2.26.7.

The eight completed home sweeps on 2.26.7 covered 16–17 pages and took
**6m26s–10m22s**. A five-minute refresh setting does not mean every product
or department is rechecked within five minutes. Full department-round freshness
cannot be determined from a home sweep or cursor position.

## Priority 1: repair remaining coverage failures

There were **79 target-skip events**: 32 department `unexpected-error`,
17 keyword `unexpected-error`, and 30 keyword `content-unresponsive`.
These are skip events, not 79 distinct products, target identities, or retry
attempts. Current skip records omit tab/generation/round/target identity.

At the captured status, departments had **12/33 targets verified complete,
20 attempted, seven unresolved**; keywords had **69/100 complete, 78 attempted,
eight unresolved**. The current target is in progress. Unresolved totals can
include carried history; they are not a newly introduced failure count.

Two keyword skips occurred on 2.26.7 at **14:58:04 and 15:11:38 CDT**.
The immediately preceding keyword sequences both acknowledged 40 items on
page 3, completed the reading pass, obtained navigation focus, and emitted a
native page-3→4 action. No further events from that tab appeared before the
skip roughly two minutes later. The temporal context is strong, but skip
identity is not logged, so it is not an exact target join. These cases do not
establish empty results or prove the receiver was responsive. The cause of
the missing navigation/document continuation is not yet known.

Current-source offline probes also reproduce two existing timeout defects:

- A blank bound department times out after 31 seconds with **zero focus
  requests**, including when trusted input occurred **1ms before timeout**.
- A fresh coordinator probe with an old progress timestamp consumes a retry
  while reporting `waiting-focus`, or `busy` in `waiting-content` /
  `waiting-selection`. `waiting-user` and a future action deadline defer it.
  A responding receiver can therefore be classified as unresponsive; this
  mechanism does not establish the cause of the two silent live cases.

Recommended implementation: account for active rendering/selection opportunity
separately from user, verification and scheduler waiting; provide one bounded
guarded rendering turn for a blank bound document. Distinguish fresh receiver
liveness, useful progress, navigation in flight and actual missing receivers.
Retain a finite wall-clock recovery limit so a stuck receiver cannot wait forever.

Add worker-side navigation outcome/timeout records with numeric target page,
tab, generation, document, attempt and round. Capture whether the route changed,
the freshness poll ran, the receiver answered, or the content document vanished.
Update failure allowlists on both sides; avoid raw query/account/error text.
Then reproduce the particular navigation failure before changing its recovery.

Acceptance: input/verification/focus deferrals do not consume active render
budget; real dead documents still recover finitely; failed interior pages remain
unresolved; completed checkpoints survive; isolated legitimate emptiness retains
the existing positive visible-proof requirement.

Source: `extension/content.js:1055`, `:1430`, `:1707`;
`extension/auxiliary-scans.js:598`. Probes are saved with this audit.

## Priority 2: reduce focus handoff latency while preserving page-reading proof

The starvation repair is already live; early scroll-lease release is also already
implemented. Do not propose either as a new optimization.

The remaining source uses a **fixed five-second retry hint** for focus denials
(`tab-workspace.js:10`, `:37`). Current callers may wait another polling interval
after the previous scanner releases its turn. On 2.26.7, median elapsed wait at
grant remains **11.59s home, 11.47s departments, 12.12s keywords**, while released
scroll turns usually last about three seconds. These figures identify a useful
scheduling experiment; they do not measure avoidable time directly.

Recommended experiment: wake the eligible FIFO head when a lease is released,
with finite expiry, fresh authorization and a bounded timer fallback. Measure
actual queue occupancy, lease identity, grant/release/expiry and next-head wakeup
before and after. Preserve normal page pacing, manual browsing, trusted input,
verification pauses, settled-bottom proof and late-card intake. Do not add tabs
before measuring this shared selected-tab resource.

Compare equal work and time budgets: useful ready receipts, target completions,
wait p50/p95, failure rate and late-card capture. No numeric speedup is established
yet. Do not reduce scrolling or settling just to make a timing counter smaller.

Chrome documents that hidden-page timers can be delayed, and recommends events
instead of polling where possible. That supports testing event-driven handoff;
it does not prove throttling caused any particular stall in these logs.
[Chrome timer guidance](https://developer.chrome.com/blog/timer-throttling-in-chrome-88).
Extension alarms can also be delayed and do not wake a sleeping device, so they
are recovery tools rather than precise subsecond handoff clocks.
[Chrome alarms reference](https://developer.chrome.com/docs/extensions/reference/api/alarms).

## Priority 3: distinguish transport errors from empty/loading pages

The new page-state telemetry recorded **25 HTTP-520 snapshots across 12 distinct
department documents**, all requesting page 101, all on 2.26.6. Twelve snapshots
were at the unusable boundary. These are documented navigation HTTP failures,
not proof of genuine empty results or merely slow hydration. Other recorded
page-state navigation responses were 200. Zero observed fetch/XHR failures does
not contradict a failed document navigation, and does not prove all requests
succeeded.

Several episodes retried the page-101 checkpoint three times. It could be a
retained older checkpoint; the audit does not establish a valid page 101 or its
original provenance. No ready-page receipt exceeded page 25 in this window, and
the earlier diagnostic-sample repetition pattern was not found. The previous
over-pagination incident should not be described as currently reproduced.

Recommended implementation: classify an observable failed document response
explicitly, retain unresolved coverage, and use bounded transport recovery with
backoff. Reconcile a retained checkpoint with fresh rendered pagination evidence
when available. Do not reinterpret HTTP errors as terminal proof, erase interior
gaps, or install a global 25-page cap. This cannot repair the upstream response,
but can avoid misleading timeout labels and repeated ineffective work.

## Priority 4: measure which scans add coverage before changing the target mix

The collector received **224,065 item observations: 1,393 new and 222,672
duplicates (99.38%)**. Repeated observations are expected from overlapping lanes,
availability updates and rescans. This ratio does not establish 99% wasted work.

Current telemetry cannot reliably attribute all first discoveries to a lane and
target, or estimate recall against the entire eligible catalogue. Add lane plus
opaque/numeric target identity to intake acknowledgements, with first-seen,
rediscovered, availability/value-change and capture-latency counts. Retain target
completion and oldest-unresolved age. Use privacy-preserving identifiers rather
than publishing saved search strings.

Then test coverage-preserving prioritization by marginal new-item yield and
age since verified completion. Keep every required department and the configured
keyword set unless a measured coverage decision authorizes a change. Do not
drop apparently duplicate cards before checking meaningful updates or late cards.

## Delivery and backend: generally fast, with a tail worth tracking

Timed Pro channel dispatches: **2.17s median, 6.00s p95, n=2,808** from due time
to send. Each sample is one dispatch's maximum member-job delay, not one unique
product or Walmart-listing-to-alert latency. Four other legacy-format dispatch
records lack timing and are excluded. Timed free dispatches: 1.72s median,
3.66s p95, n=561, after their intentional historical-release deadline.

The worst Pro dispatch was **20m02s** at **10:44:15 October 30**. Its retained
job history does not explain that delay. Five other dispatches exceeded 30s
during recovery from the October 29 privacy-audit block. The 62 privacy-block events ran
16:28:25–16:34:30 and align with the separately documented retired-role repair;
that is a historical interruption, not a current reason to weaken privacy checks.

The latest hour's Pro median/p95/max were **2.37s / 5.07s / 6.33s**, n=41.
Current health has zero due channel jobs, zero failed channel jobs, and a running
personal sender. Future-dated free jobs are expected. One personal job is in
manual review; that is not an overdue-channel backlog and should not be blindly
resent. Over the day there were 158 audit throttles, three route throttles and
89 dispatch retry events; these counts are not unique affected items.

Recommended secondary work: investigate the isolated 20-minute job through
enqueue/due/retry/claim/settlement history and add sustained overdue-age and
sender-liveness monitoring. The previously proposed sender recovery exception
boundary remains relevant in source, but this audit does not establish a new
live sender crash. Backend micro-optimization or a database journal migration
has less measured value than browser reliability and focus scheduling here.

## Reproduction and limitations

Local ignored evidence: `data/performance-review-20261030/` contains frozen
browser/delivery JSONL gzip files, source-file hashes, initial scanner/diagnostic
snapshots, health/resource snapshots, focused failure contexts, probe outputs
and `analysis.json`. Live SQLite files were never opened directly by this audit.

Reproduce the frozen analysis without recapturing production:

```powershell
.venv/Scripts/python.exe data/performance-review-20261030/analyze.py --frozen --quiet
.venv/Scripts/python.exe data/performance-review-20261030/check_evidence.py
```

Frozen replay reproduced the identical analysis-file hash. Independent totals
reconcile intake observations with new+duplicate counts and verify the page,
skip, HTTP-document and timed-dispatch denominators. Existing isolated readiness
and coordinator probes were rerun against current source; no app tests were
modified and no full suite was needed for a read-only audit.

This is an observational software audit, not a CPU profile or a complete
catalogue-recall measurement. Sparse/rejection-biased card samples cannot measure
extraction precision or prove equality of full product sets. Current version
telemetry establishes activation but not byte-for-byte identity of every loaded
script. Improvements above are proposed work; no measured gain is claimed before
implementation and a comparable post-change observation.
