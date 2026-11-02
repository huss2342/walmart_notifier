# Log storage, category-value analysis and manual social checks — November 1, 2026

The owner explicitly rejected a social scraping/checker feature: the request is
for a manual review of the supplied Reddit/Facebook posts in signed-in Chrome.
Separately authorized: increase the existing 5-MiB log size to 10 MiB and analyze
which categories contain expensive products. A fourth high-value tab is an
owner-proposed next design, not an enabled runtime feature.

## Log increase — verified live

DeliveryMonitor and shared RollingLog defaults changed from **5-MiB active /
32-MiB total** to **10-MiB active / 64-MiB total**. Daily rotation, 60-day age
target, compressed history, backup bounds and pruning/failure algorithms are
unchanged. Explicit browser/backend/operations/native/recovery limits remain
unchanged. The larger total cap actually increases archive headroom rather than
merely allowing a larger active segment.

Two regressions failed first; 34 focused tests passed, independently repeated.
Actual rollover retained 9.25 MiB in the active file, then archived its complete
contents when the next append crossed 10 MiB. Full Python: **2,603 passed, one
existing platform skip**. Ruff/whitespace passed. The actual candidate passed
network-disabled HTTP → queue → fake Discord send → request tracing and logging
failure-isolation smoke. JavaScript sources were unchanged.

Only two reviewed modules were overlaid on exact live image **007848259ff4**;
no dirty-checkout rebuild. Image **5fd8a91dba3c** activated healthy at
**2026-11-01 15:00:01 UTC / 09:00:01 CST**. Environment, full host configuration,
mounts, network names, command/user/security and scanner/relay settings preserved.
All 12 personal watches unchanged by before/after hash. Live health confirms
10/64-MiB limits, zero dropped logs, zero due jobs and zero failed channel jobs.
Rollback: `reviewer-notifier:before-log-budgets-20261101`.
Release/test/source receipts: ignored `data/high-value-coverage-20261101/`.

## Expensive-category findings

Read-only, transaction-consistent export at **14:51:34 UTC / 08:51:34 CST**:
43,457 distinct retained product IDs, observations beginning October 21. Latest
observed value is available on 25,826; 17,631 have no value. No duplicate IDs,
currency violations or invalid nonmissing values. There are no first-Pro payload
snapshots in this dataset, so this is **latest recorded value**, not maximum-ever
or value-at-discovery. All seed flags are false and cannot identify an initial
inventory backfill. Counts describe captured products, not the whole catalogue,
product publication times, demand, claim speed or resale value.

Rolling seven-day cohort (168 hours ending at export): 20,718 product IDs first
captured in that window; 14,765 have a recorded value, including 112 zero values.
There are 111 at $100+, 21 at $200+ and five at $500+. Furniture has 447 IDs,
282 priced, with **28 at $100+, eight at $200+, four at $500+**. Electronics has
11 at $100+ and two at $200+. Unclassified “other” remains visible: 38 at $100+
and six at $200+; it is a mixed group, not a single navigable department.
Furniture is the strongest named category for prioritization in this sample.

Recent 72-hour check also supports Furniture: 15 at $100+, five at $200+, three
at $500+; these are 15 of 48, five of ten and three of three across categories.
These small high-end counts do not establish future yield or popularity.

Stored categories are imperfect. Other includes trees, a grill and exercise
equipment, plus a patio table and shower seat. Two expensive artificial trees
are stored under sports/office/travel; current title classification would place
them in home/decor. Original category assignments were retained in the tables,
not silently corrected. Current search priorities, price missingness and latest-
value look-ahead can bias this sample. No per-query airtime data establishes an
optimal scheduling allocation.

The exact export, calculations, CSV, JSON and one-off analytical notes are in
`data/high-value-coverage-20261101/`. Independent recalculation reconciled all
11 seven-day categories, row totals and each price threshold.

## Manual signed-in Chrome checks

Read the live New-sorted r/RecognizedReviewer feed and selected posts directly
in the owner's existing Chrome session, including screenshots and posted times.
No social API, scraper, recurring monitor, new Discord channel or checker was
built. The original Reddit tab was preserved. Seven explicitly linked examples:

| Product | Recorded finding at 14:55 UTC |
| --- | --- |
| Gingerbread house, `3098271578` | Absent from retained collector and legacy history; posted minutes before the check |
| Harley mirrors, `20557108440` | Absent from retained history |
| Harley rotor, `20239318698` | Captured October 24; Pro job sent |
| Glow football, `18827452277` | Absent from retained history |
| White dining chairs, `20396768486` | Captured about 10 minutes before the post; Pro sent |
| Coffee scale, `20324358931` | Captured October 23; Pro sent |
| Black dining chairs, `20387452348` | Captured about 12m56s after the post; Pro sent |

Absence is an unmatched lead, not proof it was accessible to the owner's reviewer
account at the same time. Parent/variant and timing differences remain possible.
The garage shelving screenshot ($72.66) matched the retained title of
`21134201594`; this is a title match without a source item ID. The newest
Facebook sample discussed lifelike dolls and broader toy searches; no private
participant names or conversations were copied into the analysis. Manual
results/permalinks are in `reddit-spot-check.json` in the ignored audit folder.

## Fourth-tab design — proposed, not enabled

A small Furniture-heavy repeat queue is supported by the observed high-value
concentration and the dining-chair capture delay. A separate lane avoids waiting
behind the entire 100-keyword round. Retain the existing home, department and
keyword targets and deep page coverage. Candidate terms should come from actual
furniture titles; seasonal/outdoor terms can be secondary.

The existing “High Value” mode is a keyword preset, not a validated price/sort
filter. No reviewer price/sort control contract currently exists. A fourth lane
needs explicit enablement, independent targets/checkpoints, backend status and
diagnostic support, and planned-wait accounting across traffic/user/verification
pauses. Simply adding a tab/lane name would duplicate existing keyword behavior.

All scanners share one focus/reading lease. Four equally busy lanes would reduce
each original lane's theoretical share from 1/3 to 1/4 (about 33% longer cycles
under saturation); this is a capacity scenario, not measured production impact.
The priority lane needs a deliberate airtime policy and before/after freshness
measurements, with no preemption of active reading and no removal of broad
coverage. No fourth tab or scanner setting was added during this analysis.
