# Discovery speed audit and improvement plan — November 12, 2026

Audience: an implementing agent (e.g. Sonnet 5.5) working in this repository.
Read the whole file before changing anything. The owner's constraints are hard:
**no feature removal, no performance regression, nothing risky** (in particular, no
increase in requests sent to Walmart). The goal is to find items sooner.

## 0. Implementation status — November 12, 2026 (owner approved all phases)

Owner direction: execute every phase; `price_high` reads for $80+ items are the
most valuable calls; the five stuck departments are probably empty and should be
checked without retry loops.

- **Backend (Phase 0) live.** `src/browser_diagnostics.py` accepts intake phase
  `fast` and fast-read lanes `priority`/`departments` (pure widening). Overlay image
  `reviewer-notifier:discovery-speed-20261112` (`dd409a0c54a0`) is
  `reviewer-notifier:local`; rollback tag `reviewer-notifier:before-discovery-speed-20261112`
  (`a0222d315948`). Mounts, ports, security options, command, networks and the env
  set were verified unchanged (env order only); health, `/ingest` and
  `/browser-diagnostics` return 200.
- **Extension 2.30.0 built, pending owner reload.**
  - `fast-reads.js`: rank scheduler (see `docs/architecture.md` "Direct reads"):
    $80+ `price_high` pages first (p1 15 s, p2 25 s, p3 40 s, p4+ 60 s), member
    searches 6 min (12 min while empty) together with `sort=new` p1 (30 s), then the
    `price_high` chain to $40 (≤12 pages, 4 min), priority searches 12 min and later
    search pages (members 12 min, priority 24 min), `sort=new` p2–4, and department
    pages in spare slots (page 1, then deepest first). A 3-hour production-scale
    simulation caught and fixed a starvation of `sort=new` p1 before release.
  - 2.30.1 (owner: "you may increase the budget"): read starts 1.8-2.2 s apart
    (~28-30/min, was 24) with jitter; 2.5 s for 24 h after any bot-check sign.
    Simulated at production scale: value p1 ~16 s, `sort=new` p1 ~32 s, members
    6/12 min, priority ~12 min, $40 chain ~4 min, departments ~4.4 reads/min (a
    full pass every ~110 min). Department reads log `count` (Walmart's item
    total) for the hidden-tail measurement.
  - 2.30.3 (owner: "highest value page asap, whatever is 80+ second fastest"):
    page 1 every 10 s, every other page holding an $80+ item every 20 s, read
    starts 1.6-2.0 s apart (~31/min). Fixed a 2.30.0-2.30.2 regression: the zone
    was judged by each page's last item, but Walmart ends every price_high page
    with a few cheaper items (live: page 1 ended at $64, page 2 ran $142-$91), so
    only page 1 stayed fast. Zones now use any item on the page.
  - 2.30.4: investigation of the price order (see below) and fixes. Live reads
    12:52-13:42 found every in-stock $80+ item on price_high pages 1-4 (10, 29,
    21 and 1 items) and none on pages 5-10. Over Nov 10-12, 13 of 93 $80+ first
    sightings were never seen by value reads: five flash items in stock under
    ~3 minutes, eight multi-variant products Walmart files by a much lower
    listing price (8/10-pack chairs, luggage sets, school supply boxes). Pages
    beyond the main block holding an $80+ item (in stock or awaiting restock)
    are now read every 20 s. Department fix: the round-34 trace showed the
    stuck departments are 2-page departments whose pager never proves its end;
    the tab re-read pages 1-2 then loaded a blank page 3 every round. A blank
    page after all server-reported pages were delivered this round now completes
    the target.
  - 2.30.2: the price_high pages under $80 are read as one top-down, back-to-back
    pass every 4 minutes (owner question: new listings shift items between pages).
    Downward shifts land on pages the pass has not read yet; a test reproduces the
    out-of-order reads the per-page schedule made after the $80 boundary moved.
  - `content.js`: department pages (`catId`, default order) are readable by direct
    reads with strict page-data checks; replies include `products`/`totalItems`. A
    blank auxiliary page whose fresh server data has no products reports at once.
  - `auxiliary-scans.js`: on a blank (`render-timeout`) or unbindable department,
    one direct read of page 1 decides: no products → recorded empty, no retries;
    resume page past the real end → read again from page 1 now; otherwise the
    ordinary retries (last one from page 1). The requests tab skips member searches
    a direct read found empty within 15 minutes.
  - `background.js`: `fast` intake phase, member/priority split, department list
    from the department tab's own discovery, coordinator probe hooks.
- **Not built, by design:** 4c (priority searches by price) — the global
  `price_high` chain already reaches every $80+ and $40+ item catalogue-wide; 4d
  (persisting scheduler times) — a worker restart now just re-reads targets in
  rank order under the same rate cap. 4a (hidden-tail sweeps) waits for the
  department `count` data the new reads now log.
- Tests: JavaScript 1137/1137, Python full suite, Ruff, syntax, protocol and
  manifest checks pass; new behaviour tests were shown to fail on the old code.
  Pre-change copies and hashes: `data/discovery-speed-20261112/before/`.

Live 2.30.0 (owner reloaded 12:52; first 6 minutes): 22.8 reads/min, value p1
every 15.9 s (max 17.6), `sort=new` p1 every 31 s, zero blocked reads or
challenges, backend 200 on every route, department direct reads 24/24 readable
(the default-order page check holds). The seven carried "stuck" departments are
not empty: direct reads report 50, 73, 62, 69, 1, 64 and 740 items (2, 2, 2, 2,
1, 2 and 19 pages). They had shrunk below their saved resume page, so the tab kept
asking for a page past the end and never re-read them. Department direct reads
found 15 new in-stock items in those first 6 minutes.

After the reload, verify with `.venv\Scripts\python.exe tools\lane_yield.py --hours 24`
against `data/discovery-speed-20261112/baseline.txt`: value p1 revisit ≈ 15 s,
zero `blocked`/challenges, ≤ 31 reads/min, no department targets stuck with zero
pages, and `fast:departments` / `fast:priority` rows present.

## 1. Evidence (read-only audit)

Window: Nov 10 02:36 → Nov 12 11:24 CST (57 h), extension 2.29.1/2.29.3. Sources:
`data/browser-diagnostic-events.jsonl*` joined to `data/discord_bridge/delivery.jsonl*`
(browser `ingest-stage.requestId` == collector `http_request_id`). A *first sighting* is
a `pro-all` job queued at intake. The extension only posts in-stock items
(`background.js` `post()` filters `available === false`), and Pro jobs exist only for
first-ever in-stock observations, so **first sighting == new Pro alert**.
Reproduce any number below with:

```powershell
.venv\Scripts\python.exe tools\lane_yield.py --hours 57
.venv\Scripts\python.exe tools\lane_yield.py --self-test
```

### Fast reads (`extension/fast-reads.js`, ~24 reads/min, one at a time)

| target | share of reads | revisit | first sightings (57 h) | ≥ $80 |
|---|---:|---:|---:|---:|
| `sort=new` p1 | 50 % | 5 s | 22 | 0 (all < $10) |
| `sort=new` p2–4 | 9 % | 75 s | 13 | 0 (all < $10) |
| `sort=price_high` p1–4 | 12 % | 75 s | 121 | **71** |
| searches (priority + member), `sort=new` p1–3 | 29 % | ~12 min per query | 112 | 22 |

- 98 % of `sort=new` p1 reads return an unchanged page.
- `price_high` p1–4 produced 70 % of all ≥ $80 first sightings in the whole system.
- High-value items are short-lived: of the ≥ $80 items first found by the value lane,
  ≥ 10 % were seen on a single read only (gone within ~2.5 min) and 15 % were gone
  within 5 min (proxy: value-lane observation gaps; an item can also drop out of
  p1–4 by re-ranking). A 75 s revisit misses or delays exactly these items.
- New Walmart listings are rare and cheap: `sort=new` finds are 91 % top-decile item
  IDs (newest products). Most other finds are **restocks of older listings** (only
  12–31 % top-decile IDs), which `sort=new` can never surface.

### Tab lanes

| lane | first sightings (57 h) | ≥ $40 | ≥ $80 | round / revisit |
|---|---:|---:|---:|---|
| departments | **973 (69 %)** | 73 | 6 | 175 min p50 (pacing-bound, 17.5 s/page, ~460 pages) |
| home | 124 | 5 | 0 | 5.5 min (8–9 pages) |
| requests | 50 | 9 | 2 | 25 min (52 targets) |

- Department yield rises with depth: pages 1–4 ≈ 5–8 finds/100 reads, pages 13–24
  mostly 13–22, **page 25 = 46 finds/100 reads** (83/179). Ten departments sit at the
  site's 25-page cap; only 29 % of the items the value lane sees are ever seen by the
  department lane (partly the cap, partly the stuck targets below).
- Average wait for a department-only find is roughly half a round: **~1.5 h**.

### Bug: five department targets are stuck every round

All 19 department rounds in the window: targets 1–4 resume a checkpoint one page
**past the department's current end** (p3 of a now-2-page department, p4 of a
3-page one). Walmart answers HTTP 200 with a blank page (0 product links, no pager,
no "no results" panel) → `page-state empty-observed` → `render-timeout` ×3 →
`unresolved` → carried to the front of the next round with the same checkpoint →
repeat. They delivered **0 pages in 57 h** and burned ~5.4 h of lane time (~9.5 %).
Target 5 fails `selection-bind-failed` at page 1 every round (0 pages).

Mechanism: `fail()` (`extension/auxiliary-scans.js:456`) calls `retainCheckpoint()`
(`:314`), which always resumes at the smallest unvisited page (`:337–344`).
`revalidateInteriorBoundary()` (`:357`) cannot fire: it needs `receipt.total > page`
and three confirmed empties, but a blank page is never a confirmed empty. The Nov 4
repair (`tasks/coverage-repair-20261104.md`) covered the confirmed-empty variant only.

### Requests (member watches)

- Personal-request DMs queued (first match) in 57 h: fast searches 52, tab requests 22,
  departments 14, value 11, home 2, newest 1.
- Fast searches cycle ~79 queries (27 priority + 52 member) through 29 % of reads, so
  each query p1 is re-read every ~12 min. 42 % of those reads return 0 items but keep
  the same cadence.
- The tab requests lane re-runs the same member searches: ~50 % of its targets are
  empty every round and the 15 s inter-target spacing dominates its 25-min round. Its
  share of DM first-matches (22–30 %) matches what pure timing luck predicts from the
  two revisit rates; it rarely finds anything the fast searches would not.

### Not bottlenecks

Zero bot challenges and zero fast-read `blocked` outcomes in 57 h. Ingest p50 75 ms,
queue wait p50 0 ms. First sighting → Pro channel send p50 0.55 s; DM send p50 0.73 s.
The delay is entirely in discovery.

## 2. Guardrails for every phase

- **No feature removal.** Every lane, sort, setting and query list stays. Only cadence,
  order and bug behaviour change.
- **No load increase.** Fast reads stay ≤ 24/min (Phase 2 makes this an explicit cap).
  Do not change `pageDelaySeconds`, refresh minutes, tab pacing, focus/lease logic,
  verification or bot-check handling.
- Keep fast-read safety: block → 5-min stop + `onBlocked`, tab fallback on `network`/
  `error`, unchanged-page suppression (`lastSent`/`RESEND_MS`), fail-closed URL scope
  (`fastReadUrl`) and `__NEXT_DATA__` sort/page/query validation in `fastRead`.
- TDD: write the failing test first, then the change. Never skip or weaken existing
  assertions. Where this plan deliberately changes behaviour, rewrite only the
  assertions that encode the old behaviour and name them in the commit message.
- Before any release, all of these pass:
  ```powershell
  node --test --experimental-test-isolation=none --test-timeout=15000 tests/test_*.mjs
  .venv\Scripts\python.exe -m pytest -q
  .venv\Scripts\python.exe -m ruff check .
  Get-ChildItem extension\*.js | ForEach-Object { node --check $_.FullName }
  node tools/generate-native-protocol.mjs --check
  ```
- The working tree contains unrelated uncommitted work. Do not revert, reformat or
  commit it. Touch only the files named in the phase. Copy each touched file to
  `data/discovery-speed-20261112/before/` first (`data/` is git-ignored).
- **Backend first.** The strict diagnostics validator rejects unknown labels, so any
  allowlist widening must be live before an extension that emits it. Deploy with the
  existing overlay pattern (`data/Dockerfile.priority-limit`,
  `data/request-searches-20261108/release.py`): tag `reviewer-notifier:local` as
  `reviewer-notifier:before-discovery-speed-<phase>`, overlay only the changed modules,
  `docker compose up -d --no-build`, confirm `/health` and HTTP 200 on
  `/browser-diagnostics` in `data/operations.jsonl`. Never rebuild from this checkout.
- **Extension activation needs the owner.** Bump `extension/manifest.json` `version`
  and the pinned assertion in `tests/test_integrated_verification.mjs:505`, then ask the
  owner to reload *Reviewer Item Relay* in `chrome://extensions` and refresh the scanner
  tabs. Do not restart sweeps or clear unresolved work.
- **Stop and roll back** a release if any of these appear: a `challenge` event or
  fast-read `blocked` outcome after the release, fast reads > 25/min, or first
  sightings/hour more than 20 % below the baseline over 24 h.

## 3. Phases

Recommended releases: **A = Phase 0 + Phase 1** (instrumentation + bug fix, low risk),
then 24–48 h of observation, then **B = Phase 2**. Phase 3+ need owner decisions.

### Phase 0 — Baseline and exact attribution (no scan behaviour change)

1. Save the baseline:
   `.venv\Scripts\python.exe tools\lane_yield.py --hours 48 > data\discovery-speed-20261112\baseline.txt`
2. Backend allowlists (deploy first, they are pure widenings):
   - `src/browser_diagnostics.py:297` intake phases: add `"fast"`.
   - `src/browser_diagnostics.py:341` fast-read lanes: add `"priority"`, `"departments"`.
   - `tools/log_audit.py:614` intake phases: add `"fast"`.
   - Tests in `tests/test_browser_diagnostics.py` (and `tests/test_log_audit.py` if it
     enumerates labels): new labels accepted, an unknown label still rejected.
3. Extension:
   - `extension/diagnostics.js:292` accept intake phase `'fast'`;
     `:321` fast-read lanes add `'priority'`, `'departments'`.
   - `extension/background.js:118` fast-read posts use `intakePhase: 'fast'`.
   - `extension/fast-reads.js` diagnostics: add `totalPages` (when `reply.totalPages`
     is an integer) and, for searches, `targetIndex` (1-based position in its list).
     Send and log priority searches with lane `'priority'`; member searches keep
     `'requests'`. Both fields are already in both numeric allowlists.
   - Tests: the sanitizer keeps the new fields/labels; a fast-read event with an
     unknown lane is still dropped.

Acceptance: suites green; after activation `lane_yield.py` lists `fast:priority`
separately and `/browser-diagnostics` POSTs stay HTTP 200.

### Phase 1 — Unstick department targets (bug fix)

Change in `fail()` (`extension/auxiliary-scans.js:456–500`): when scheduling the
**last** retry (`target.attempts === RETRIES.length`) for a target **without**
`selection`, with `details.failureCode` in `{'render-timeout', 'progress-timeout'}`, and
whose resume page is > 1, do not call `retainCheckpoint()`. Instead archive the
checkpoint exactly as `revalidateInteriorBoundary()` does
(`target.boundaryRevalidation = { at, checkpoint, checkpointUrl }`, then
`target.checkpoint = target.checkpointUrl = null`, `await clearSweep(lane)`), so the final
attempt starts a fresh pass at `target.targetUrl` (page 1) and re-learns the real page
count from the pager. Earlier retries keep resuming the checkpoint, so transient
failures deep in a large department keep their progress. A fresh pass re-reads every
page, so no coverage claim is weakened.

Tests (red first) in `tests/test_auxiliary_scans.mjs` or
`tests/test_auxiliary_navigation_recovery.mjs`:
- checkpoint visited `[1, 2]`, two `render-timeout` failures → the third start navigates
  to the page-1 target URL and `boundaryRevalidation` holds the old checkpoint;
- first `render-timeout` failure still resumes page 3 (unchanged);
- `selection` targets, `document-http-error` and confirmed interior-empty paths are
  unchanged; a later successful pass completes the target (`coverageStatus` complete).

Phase 1b — investigate target 5 (`selection-bind-failed`) without changing bind rules:
poll `data/scanner-status.json` (`report.lanes[departments].targetLabel`,
`lastFailureCode`) at the start of a department round to identify the label, then
compare it with the root facet name and the rendered chip (see "shortened chip names"
in `tasks/department-hierarchy-20261026.md`). Report the cause to the owner with a
proposed fix; do not loosen the server-category/chip agreement check without evidence.

Acceptance after one full department round (~3 h): `lane_yield.py` reports no targets
"attempted ≥3x with zero delivered pages" other than the bind case, department
unresolved count falls, round minutes fall ~5–10 %.

### Phase 2 — Re-budget fast reads at the same total rate

Files: `extension/fast-reads.js`, `extension/background.js` (`queries` callback,
lines 126–136), `tests/test_fast_reads.mjs`, manifest version.

Replace `PATTERN` / `CATALOGUE` / `valueNext` / `lastValue1` with a due-time scheduler:

- Every target has an interval. Each step reads the target with the highest overdue
  ratio `(now − lastReadAt) / interval` among targets with ratio ≥ 1 (never-read
  targets are due). Tie order: value p1, member searches, value p2+, priority
  searches, newest, follow-up pages. If nothing is due, read the value page with the
  oldest `lastReadAt` (Phase 3 replaces this filler).
- Explicit rate cap: `MIN_READ_SPACING_MS = 2500` between read **starts** (≈ 24/min),
  kept alongside `STEP_MS`, so faster responses can never raise load.
- Initial intervals, in one frozen object so they are easy to tune:

  | target | today | new |
  |---|---|---|
  | value p1 | 75 s | **20 s** |
  | value p2–p4 | 75 s | 60 s |
  | value p5+ (new) | never | 180 s each |
  | member search p1 | ~12 min | **5 min** (12 min if its last read had 0 items) |
  | priority search p1 | ~12 min | 10 min |
  | follow-ups p2..min(totalPages, 3) | next search slot | 10 min |
  | newest p1 | 5 s | 30 s |
  | newest p2 | 75 s | 5 min |
  | newest p3–p4 | 75 s | 15 min |

  Every productive target gets faster or stays equal; only `sort=new`, which found no
  item worth ≥ $10 in 57 h, slows down. Estimated demand ≈ 22 reads/min with 52 member
  and 27 priority searches, under the 24/min cap.
- Value chain: `VALUE_FLOOR` 80 → **40**, `VALUE_MAX_PAGES` 20 → **12**. After reading
  value page p: if its last item is ≥ floor and p < min(totalPages, VALUE_MAX_PAGES), the
  chain depth becomes at least p + 1; if the last item is < floor, depth = p. Value
  pages 1..depth are targets. (Today p4 already holds mostly $40–80 finds that the
  department lane otherwise finds ~1.5 h later.)
- `background.js` `queries` returns `{ members, priority }` with today's normalisation
  and dedupe; a query in both lists counts as a member search.
- Unchanged: `sort=new` for searches, block backoff, tab fallback, `lastSent`/
  `RESEND_MS`, scope checks, `paused()`.

Tests (red first, fake clock as in the existing overdue test):
- newest p1 never read more often than every 30 s; value p1 read at least every ~20 s;
- value chain extends while the last item is ≥ $40, stops below $40, never passes
  min(totalPages, 12);
- an empty member search is not re-read for 12 min, a productive one is due after
  5 min; members win ties against priority searches;
- follow-ups p2..p3 are scheduled after p1 reports `totalPages ≥ 2`;
- consecutive read starts are ≥ 2.5 s apart;
- existing safety tests (block backoff, tab fallback, unchanged-page suppression,
  bot-check hand-off) pass unchanged.
Rewrite only the assertions tied to the old slot pattern (PATTERN/CATALOGUE shape,
slot positions in the "price_high pages on…", "searches read sort=new…" and "overdue
price_high page 1…" tests) to the new contract.

Acceptance 24–48 h after activation, against `baseline.txt`:
- fast reads ≤ 25/min, zero `blocked`, zero `challenge`;
- revisit p50: value p1 ≤ 25 s, value p2–p4 ≤ 65 s, newest p1 ≈ 30 s;
- ≥ $80 first sightings/hour not below baseline;
- DMs first matched by fast searches/hour not below baseline;
- value pages ≥ 5 appear with $40–80 finds, and department ≥ $40 first sightings drop
  (the same items found earlier by the value chain).

Rollback: reload the saved 2.29.3/Release-A extension source.

### Phase 3 — Fast department sweep (owner decision: budget)

Purpose: cut the ~1.5 h average wait for the 69 % of finds that only the department
lane sees today, while the tab lane remains the full-coverage guarantee.

Budget options (combined with the 175-min tab round): (a) Phase 2 spare only
(~2 reads/min → extra pass every ~4 h, average wait ~1.5 h → ~50 min); (b) raise the
fast-read cap from 24 to 30/min after 48 h of clean Phase 2 (pass every ~1 h, average
wait → ~22 min); (c) lengthen priority/newest intervals further. Do not start without
the owner's choice; (b) is the only option that adds Walmart load.

- `content.js` `fastReadUrl` (`:504`): also accept `catId` matching `^\d+(?:_\d+)*$`
  with an optional page and **no** `q`, `queryNups` or `sort`. `fastRead` (`:565`): for
  such URLs require `String(variables.catId) === catId`, an empty query, and a default
  sort value. Confirm the default-sort value on one real department page first
  (expected `undefined`, `null`, `''` or `'best_match'`); any mismatch returns
  `ok: false`, so the read fails closed and ingests nothing.
- `fast-reads.js`: new dependency `departments()` → `[{ catId }]`, supplied by
  `background.js` reading the existing `reviewerAuxiliaryScans.lanes.departments.targets`
  (`kind === 'department'`, catId from `targetUrl`) read-only. Sweep (department, page)
  using `totalPages` from page 1, capped at 25. Lane `'departments'`, intake phase
  `'fast'`. On page-1 reads also log `count` = `itemStacks[0].meta.totalItemCount` when
  present: this measures how much of each department hides beyond the 25-page cap.
- After one uniform pass, order by measured finds/read (deep pages and capped
  departments' last page are richest today).
- Tests: URL accept/reject cases, variable mismatches, cursor across departments and
  pages, `totalPages` respected, block backoff stops the sweep, budget share respected.

Acceptance: `fast:departments` first sightings > 0 per hour, `tab:departments` first
sightings fall by a similar amount, total first sightings/hour ≥ baseline, no blocks
or challenges, reads/min within the chosen cap.

### Phase 4 — Optional, each needs the owner's go-ahead

- **4a Hidden tails.** If Phase 3 counts show departments far above 1,000 items, add
  per-capped-department `sort=price_high` (and possibly `price_low`) page sweeps via
  fast reads, or revisit the Oct 26 top-level-only decision (made when every page cost a
  rendered tab load with scrolling and a focus turn, ~17.5 s; a fast read is one request,
  ~2.5 s).
- **4b Requests tab lane.** Skip a member search for one round when a fast read proved
  it empty (0 items) within the last 10 min. An empty result set is sort-independent,
  so coverage is unchanged while the 25-min round roughly halves.
- **4c Priority searches by value.** A/B `sort=price_high` for the owner's priority
  list (its stated purpose is the high-value tail; `sort=new` shows only the newest
  120 results per search).
- **4d** Persist scheduler `lastReadAt` in `chrome.storage.session` so a worker restart
  does not reset cadence.

## 4. Rejected options

- Lower `pageDelaySeconds`, add tabs, parallel fetches or Walmart API/`_next/data`
  probing: all raise load or bot-check risk.
- Delete the `sort=new` lane or the tab requests lane: feature removal.
- Shorter department pacing to speed rounds: same as the first point.

## 5. Owner decisions

1. Approve slowing `sort=new` p1 from every 5 s to every 30 s (0 finds ≥ $10 in 57 h).
2. Release grouping: A then B (recommended) or one combined release.
3. Phase 3 budget option (a), (b) or (c).
4. Any Phase 4 item.
