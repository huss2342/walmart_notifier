# Discord delivery and scanner timing audit — October 31, 2026

Owner authorized checking Discord delivery, errors and significant lag, immediate
repairs, and process/timing improvements while preserving or improving deep search.
Delivery checks are complete. Extension source **2.26.10** contains a verified
recovery repair; live Chrome activation remains pending. Backend unchanged.

## Delivery evidence

Read-only live audit around **10:48–10:54 CDT**; product and latency windows cover
the preceding 24 hours. Existing provider messages were fetched, with no test
posts, DMs, queue replay, migration or production error injection.

- All **428 currently available, unseeded products first captured in the preceding
  day** had confirmed `pro-all` delivery. This verifies captured eligible products,
  not complete Walmart discovery or each member's mobile push settings.
- Existing messages on **all 19 active feed routes** matched their stored product
  IDs and contained no user mentions. Three existing personal-alert messages also
  matched their products and personal format.
- Pro delivery after first capture: **1,316 destination items**, median **2.012s**,
  p95 **5.231s**, max **11.002s**. Free after its scheduled 24-hour release:
  **1,360 items**, median **1.604s**, p95 **4.573s**, max **25.175s**. These are
  destination-item samples; products may appear on several routes.
- No active Pro backlog, failed channel jobs or pending personal alerts. No
  retained 24-hour queue summary reported oldest due work over 30 seconds. All
  84 channel dispatches retried in that window subsequently reached `sent`.
  Seven additional retry IDs absent from the channel dispatch table were excluded
  from that channel reconciliation; personal events share this log stream.
- Prior-day transient SQLite/provider errors and rate limits exist in history;
  the last-hour error check was empty and current workers poll successfully.
  `PRAGMA quick_check` returned `ok`; monitoring dropped zero events.
- The existing blocked-DM recipient, one expired personal alert and one uncertain
  personal send held for review remain intact. An uncertain provider outcome
  does not establish a missed message or permit a blind duplicate.
- Current main sender, personal sender/Gateway and referral worker are healthy.
  Health responses took **0.428–0.630s**. One resource snapshot measured **0.53%
  CPU, 241 MiB RAM**; this is not a sustained CPU/Chrome performance benchmark.
  All 12 saved personal watches retain their pre-audit hash.

## Scanner evidence and repair

Frozen window: **October 30 22:48:06–October 31 10:47:39 CDT**. All **73,944**
retained browser events identify **2.26.9**; none were malformed. Ready-page
receipts: home **1,724**, departments **1,705**, keywords **1,313**. First discoveries
recorded at intake were home **27**, departments **99**, keywords **89**. Auxiliary
search continues to add coverage; preserve every discovered department, all 100
configured keywords and all result pages. Brands remain at the owner's off setting.

The coordinator could inspect an old, rejected document while the correctly
scoped replacement was still loading, then spend a second recovery retry.
Eleven observed duplicate retries occurred **61–750ms after canonical restart**,
**1.45–2.75s before** the subsequent page-loaded receipt. Ten retained the exact
old document ID. Each introduced another 15-second scheduled wait: **165 seconds
of aggregate target deferral**, not a measured production speedup.

`extension/auxiliary-scans.js` now defers this repeated recovery only while the
tab is loading a pending URL in its assigned scope, within the existing
**120-second coordinator deadline**. Probes cannot renew that deadline. Old cards
remain unauthorized; saved pages, finite recovery, input/verification guards,
normal pacing, scan concurrency and all search targets remain intact.
[Chrome's Tabs API](https://developer.chrome.com/docs/extensions/reference/api/tabs)
defines `url` as the committed URL and `pendingUrl` as the destination before
commit. The change uses that distinction without treating the old page as read.

Nine regression cases cover successful/persistent pending navigation, altered or
missing pending scopes, rejected old-card authority, checkpoint continuity and
input/verification guards. Two meaningful cases failed before the eight-line fix.

## Remaining coverage limits

The window contains **27 render-timeout skips** (19 department, eight keyword)
and **four HTTP 520 skips** (three department, one keyword). These targets remain
unresolved and eligible for later recovery; they are not certified complete.
No skip was demonstrated to be caused by exhaustion of the duplicate-retry race.

An out-of-stock-only keyword result contains ambiguous primary/variant links and
no View item anchor. It matches the existing October 28 fixture; safe card
exclusion can lead to a render timeout. No available-product alert miss was
proved. Relaxing that parser without unambiguous evidence risks attributing a
sibling's price or stock to the wrong product, so its extraction rules remain.
HTTP 520 and ambiguous result evidence need further source observations; the
repair above does not claim to eliminate provider failures or prove full recall.

## Verification and activation

- Focused delivery regression suite: **51 passed** using the project Python env.
- Independent read-only review: **APPROVE**, no required findings; independently
  reran the **75** coordinator tests and inspected finite recovery/scope guards.
- Full JavaScript suite: **928 passed**, normal Node process isolation.
- Repository Ruff, all extension/helper JavaScript syntax, native protocol,
  manifest JSON, server startup/import and whitespace checks passed.
- Full Python regression suite: **2,487 passed, one existing platform skip**
  (2,488 collected); normal approved execution in the project's virtual env.

Windows sandbox initially denied pytest access to temporary directories and Node
child-process isolation. Normal approved execution ran the assertions unchanged;
those harness errors are separate from application failures. No check was waived.

Reload **Reviewer Item Relay** in `chrome://extensions` to **2.26.10**, then refresh
the three scanner tabs. Keep saved sweeps, checkpoints, settings and history; no
Restart sweep is required. Browser control was unavailable during this audit,
so the running **2.26.9** has not been reported as upgraded. No backend restart
or deployment is needed for this unchanged status contract. Production throughput
and retry-rate improvement require a post-reload observation window.

Evidence: `data/reliability-audit-20261031/`, including sanitized ledger/provider
checks, live health and watch hashes, focused/full test logs, before snapshots,
and the scanner's exact before/after patch and correlated race sequences.
Rollback: restore the eight-line coordinator change from its saved before
snapshot and the manifest to 2.26.9, then reload; no state migration exists.
Existing unrelated checkout changes were preserved. No commit/push was made.
