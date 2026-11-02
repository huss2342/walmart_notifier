# Scanner performance follow-up — October 28, 2026

Read-only operational analysis requested by the owner. No production code,
settings, pacing, scanner ownership, verification helper or deployment changed.
Extension 2.26.4 first appears in the retained event archive at 13:28:19 CDT.

## Main finding: pagination probes repeat a rendered terminal page

At inspection the Electronics department URL requested page 85, then 86 and 88,
while Walmart's actual rendered pagination remained `1 … 22 23 24 25`.
The terminal 25 is a plain div. There is no `aria-current` marker and no Next
control. The page-85 accessibility snapshot and page-86 DOM both began with
the Avoben selfie light, HONGDAK GoPro case and Dutevolns zip ties.

Archived extraction evidence is stronger than the URL alone: requested pages
27–85 have the same accepted count (37) and the same complete twelve-entry
diagnostic sample. These are sampled card facts, not a full product-set equality
proof. A full forty-ID product fingerprint was captured at URL page 86;
subsequent full-set comparison timed out during automatic scanning. The repeated
sample and visible terminal pager strongly indicate server-side page clamping.

The actual current source explains why it continues:

- `extension/page-controls.js:35` requires an `aria-current` marker matching the
  requested URL page before inspecting Next. This live markup returns unknown.
- `extension/content.js:1838` accepts a disabled Next, a positively identified
  single-page result, or separately confirmed empty results as terminal proof.
  An omitted Next on this multi-page terminal layout supplies none of these.
- `extension/content.js:1107` continues blind URL probes until page 100 when no
  terminal proof exists. The safety stop preserves unresolved work; it does not
  establish successful completion.

A Node VM probe executing those exact source functions reproduces unknown Next
and advancement 25→26, 26→27, 85→86 and 99→100, then null at 100. Probe output:
`data/performance-review-20261028/pagination-probe.json`.

The retained generation 783 read page 1 at 13:39:49 and page 87 at 14:23:36.
Its 62 acknowledged URL pages above 25 span 13:52:25–14:23:36 (31m11s).
This is time spent beyond the rendered boundary, not a proven 31-minute saving:
some early boundary probes could be legitimate confirmation work. The current
page throughput must not be equated to distinct useful page coverage.

## Measurements

Thirty-minute event windows, distinct (tab, generation, requested-page) ready
receipts; earlier worker IDs are mapped from the previous captured analysis.

| Lane | 12:58:13–13:28:13, v2.26.2 | 13:54:07–14:24:07, v2.26.4 |
| --- | ---: | ---: |
| Home | 49 | 47 |
| Departments | 50 | 57 |
| Keywords | 33 | 17 |
| Combined receipts/min | 4.40 | 4.03 |

These are observational windows with different work, human activity and recovery
state; they are not a controlled speed comparison. All 57 department transitions
in the later window used URL fallback. The captured pager explains the missing
destination controls beyond its terminal page. The earlier midday baseline was
4.17 receipts/min; neither comparison establishes a useful throughput gain.

Later window median ready-to-ready intervals: home 26.35s, departments 26.84s,
keywords 42.14s. Median successful native home navigation is about 2.1s;
home intake median remains below 0.2s in the immediately preceding analysis
window. Recent completed 17-page home sweeps took 10m11s, 8m05s and 7m46s.
Five-minute refresh therefore does not imply a full catalogue visit every five
minutes.

Fresh local-file sampling ran for 180.03s, 37 observations at five-second
intervals. These are sampled status counts, not exact time attribution:

- Departments: 30/37 (81%) in focus/user wait phases, with `busy` or
  `waiting-turn` reasons. The other seven were navigating.
- Keywords: 31/37 (84%) in wait phases with those internal contention reasons;
  the other six were passive during a target transition.
- Home: 19/37 (51%) waiting-user. Home collapses some focus reasons into that
  label, so this does not establish human interaction.

Home advanced page 11→15. Keywords completed Electronics and moved to Exercise
(completed targets 5→6). Departments remained on Electronics, generation 783.
Thus the scanner is moving, but department URL advancement includes repeated
terminal content, and the three lanes compete for selected-tab turns.

At the analysis checkpoint departments reported 1 verified / 14 attempted /
12 unresolved targets; keywords 6 verified / 72 attempted / 65 unresolved.
The migration explicitly converts old advances without terminal proof to
unresolved. Those totals are not counts of newly introduced failures. Since
2.26.4 activation, telemetry recorded one department unexpected-error skip and
three keyword content-unresponsive skips. They require separate reproduction;
the current evidence does not establish that every skip has the pager cause.

## Backend and delivery

The live container was healthy, with no due/pending/leased Pro jobs, no route
cooldowns or global delivery wait. One resource snapshot showed 9.31% CPU and
152 MiB RAM. That snapshot is not a sustained host/browser resource profile.

The final event window contained 5,056 product observations: 45 new and 5,011
duplicates (99.1%). Overlapping scans and rescans naturally produce duplicates;
the aggregate alone does not prove inefficient work. The terminal-page evidence
does establish one specific waste source.

Ninety-four successful Pro dispatches in that window had median maximum-member
due-to-send delay 2.70s, p95 5.91s and max 7.98s. This measures delay after queue
acceptance, not time from Walmart listing to discovery, and each sample is a
dispatch rather than a unique product. There was no current delivery backlog.

One database I/O error appeared during the earlier inspection, which included
Windows-host reads of the live SQLite bind mount. It recovered on successive
container health checks. That overlap does not prove causation. This follow-up
avoids further host SQLite reads and uses container health plus JSON telemetry.

## Recommended next change

First repair terminal-pagination recognition against this captured markup:
recognize the rendered current page even without aria-current, handle an omitted
Next only with corroborating terminal evidence, and detect repeated rendered
page/product fingerprints when a requested URL is not honored. A contradictory
or clamped response must never count as a newly covered page. Preserve genuine
windowed pagers, departments longer than 25 or 100 pages, dynamic growth, late
cards, stock/variant checks and durable unresolved recovery. Do not install a
global 25-page cap.

Then instrument focus grants, hold durations and releases to measure avoidable
contention separately from necessary scrolling and actual user activity. Recheck
useful distinct page coverage and a full verified department round before
changing tab counts, keyword lists, page pacing or the verification helper.

Evidence and reproducible analyzer: `data/performance-review-20261028/` contains
initial/final archived events, initial status, 37 live samples, analysis.json,
analyze.py and the source-function probe output. All are local ignored receipts.
Browser inspection only read existing scanner documents; no manual navigation,
claim, test notification or external message was performed.
