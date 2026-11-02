# Measured scanner efficiency and coverage plan — October 28, 2026

Implementation follow-through: the owner subsequently approved the first fixes.
See [scanner-coverage-release-20261028.md](scanner-coverage-release-20261028.md)
for verified source2.26.4, backend activation and the pending Chrome reload.
The observations and proposal below describe the earlier baseline; they do not
establish a measured speedup of the new release.

Status: measured current operation and tested a collector candidate in an isolated
replay. This is a plan, not an installed scanner update. Every department page
remains mandatory. No scanner settings, production source, service deployment,
verification helper or page pacing changed during this investigation.

## What the current measurements establish

Archived diagnostic events were frozen after eleven local-report samples at
30-second intervals, **12:15:50–12:20:50 CDT**. The main throughput window below is
**11:50:15–12:20:15 CDT**, before the later manual card reproduction. Live extension
version was **2.26.2**, three owned scanner tabs, brands off, 100 keyword targets,
37 department-lane targets (including discovery), page delay 15 seconds and refresh
5 minutes. No active verification pause or new challenge count was observed.

| Lane | Distinct generation/page reads in 30 minutes | Reads/minute |
| --- | ---: | ---: |
| Departments | 60 | 2.00 |
| Home | 53 | 1.77 |
| Keywords | 12 | 0.40 |
| Combined | 125 | 4.17 |

These are acknowledged/read-ready page observations, not proof that all pages or
departments were exhausted. They must not be labeled unique products or complete
department coverage. At 60 minutes the corresponding total was 241 reads, or
4.02/minute, so the older 4.79/minute sample is not a reliable present capacity.

Successful department native page transitions: median **2.05 seconds** (n=58),
versus **19.31 seconds**
between consecutive page-ready events (n=57 in the final window). The separate
navigation-ready → page-ready median was **10.07 seconds**. This interval includes
rendering, required scrolling/settling and shared-focus waits; it is not all waste.
Main ingest batches took median **0.13 seconds** (n=53). Backend intake therefore
does not explain the tens-of-seconds browser intervals in this sample; auxiliary
ingest latency is not separately logged in the installed version.

Three recent 25-page department traversals took **13m16s, 9m07s and 11m09s** from
page-one-ready to page-25-ready. These spans exclude initial setup and final
completion overhead and do not prove page 25 was terminal. Recent home completion
records for 16–17 pages took **11m13s, 6m25s and 8m08s**. No verified full-department
round duration can be claimed from these page groups or the current cursor.

The five-minute sample advanced the keyword cursor **48 → 51**, while failures
rose **3 → 4**. Couch remained the current target at every sample from 12:17:50
through 12:20:20, with generation changes indicating recovery; the 12:20:50 sample
had advanced to Sofa with the extra failure. This is roughly three minutes spent
before skipping Couch, not a successful three-minute product search. At 12:20:50,
the keyword round begun around 11:00 was still only 51/100 targets advanced.
Linear extrapolation would ignore unequal query sizes and failures; do not turn
that into a promised full-round ETA.

Five of the eleven department samples reported busy/waiting-turn focus reasons.
The home status also collapses some focus waits into waiting-user. These coarse
samples establish contention, not how many seconds were avoidable or whether a
human was interacting. Current logs do not measure lease occupancy well enough to
prove early release or another worker would improve throughput.

## A reproduced coverage and retry bug, rather than a tab-count hypothesis

Read the existing keyword tab without navigating it. On Sofa it displayed a Free
**$35.99** primary Toddler Couch, item **20761609702**. The product had one explicit
"View item for …" primary link. Its alternate-color links had empty aria-labels;
the explicit "…, variant on …" text was instead on their child images' alt text.

cardFor() reads only each link's aria-label when validating sibling variant IDs.
It consequently rejects the legitimate primary together with its swatches. The
early variant exclusion in collect() also checks only aria-label, leaving an
isolated image-labeled variant able to become a candidate in a synthetic wrapper.
These are two directions of the same missing image-alt evidence.

A single later manual Sofa results page reproduced the markup. Saved only public
card text, product paths, aria-labels, image alt text and element ancestry; no
account, quota, page HTML, hidden application state or product-page requests. The
temporary manual tab was closed. The sparse DOM is sofa-card-fixture.json.

Replay used the complete current content-script source and the existing extraction
suite's DOM harness. The proposed candidate changes **only in-memory test copies**:
read an image alt fallback when link aria-label is empty, both for card sibling
validation and early variant rejection. The exact marked primary, one-card value,
visible Free and availability rules remain required.

| Test mode | Existing extraction tests | New cases | Total |
| --- | --- | --- | --- |
| Current source | 28 pass | 2 pass / 4 fail | 30 pass / 4 fail |
| In-memory candidate | 28 pass | 6 pass | **34 pass / 0 fail** |

The live-card replay changes the selected primary from **missing → retained**;
all listed swatch IDs remain excluded. Other cases retain available=false for
out-of-stock cards, reject mismatched/missing variant evidence and reject isolated image-alt
variants. This is proven for the captured case and fixtures, not an accuracy claim
for all cards or a measured production speed percentage.

Why it matters for efficiency: the relay currently interprets no recognized cards
as waiting for content. A primary rejected this way can spend the 30-second render
window and bounded recovery/reload budget before being skipped. Recognizing the
actual selected card removes that false-empty cause. The Couch retry observation
and the later Sofa markup are separate pieces of evidence; this investigation
does not establish that every recorded failed keyword had this cause.

## Other reproduced coverage failures

coverage-probe.mjs executes the actual totalPages()/nextUnvisited() functions with
controlled pager nodes. Current behavior:

| Fixture | Current outcome | Required outcome |
| --- | --- | --- |
| Windowed numeric labels 1–5, enabled Next, pages 1–5 visited | Finished, no limitation flag | Continue or explicitly unresolved |
| Numeric labels through 25, enabled Next, 1–25 visited | Finished, limitation flag | Continue while Next exposes more pages |
| No recognized total, 100 populated pages visited | Finished, no limitation flag | Unresolved safety stop; never verified complete |

These tests reproduce code paths; no claim is made that these exact pager states
occurred in a particular live department during the measurement. The saved
35-page pager additionally disproves a universal 25-page Walmart cap. Existing
scrolling, late-card ingestion and acknowledgement safeguards are retained.

## Evidence-backed implementation order

1. **Fix selected-card versus image-alt variant recognition first.** This has a
   captured real-page failure and a passing minimal candidate. Promote the six
   new cases into the maintained suite, run the complete extension regressions,
   and verify the same primary/swatches in the browser after activation. Keep
   stock checks and card-only operation; add no individual-product requests.
2. **Enforce terminal evidence and durable outstanding coverage.** A numeric
   pager maximum alone is not terminal proof. Follow usable ordinary Next/page
   controls, reconcile growing page windows, and retain inaccessible pages as
   unresolved. A safety ceiling must report partial, not complete. Failed target
   progress must survive round refreshes. Show successful/attempted/unresolved
   departments separately. Test longer than 25/100 pages, failed ACKs, late cards,
   changing pagers, interruptions and ownership recovery against the exact full
   page/eligible-ID union. Do not just remove the bound and risk an endless loop.
3. **Measure corrected throughput before a scheduling change.** Activate the
   already prepared bounded diagnostics, retain round/target timestamps and
   per-scope page/novelty evidence, and distinguish focus contention from user
   activity. Time complete verified department rounds. Compare matched work and
   settings, not only a short period containing different-sized departments.
   Recovering previously missed cards or deep pages can add legitimate work and
   lengthen a round. That is restored coverage, not evidence of a slowdown on
   identical work. Do not compare an old partial round with a new complete one.
4. **Then test a specific bottleneck, one at a time.** Profile actual focus-lease
   use/release and target-start deadline overshoot. Preserve the 15-second pacing,
   scrolling and all protection checks. Keep a change only if identical full-page
   work completes faster without new failures or unacceptable home/keyword delay.
   No percentage saving is currently proven for either mechanism.
5. **Keep three tabs, 100 keywords and brands off for now.** A fourth worker and
   fewer keywords have no controlled throughput/yield evidence. If a later
   experiment supports a fourth tab, it should claim whole departments from a
   shared queue with no duplicate ownership. Brands need demonstrated additional
   or materially earlier useful finds, or net time savings, with measured extra
   requests and preserved mandatory full department traversal. The previous one-
   brand trial added no item, so it does not justify enabling brands routinely.

This order has proven defect reproductions and a tested first candidate. It does
not claim an unperformed live A/B trial or a universal fastest configuration.

## Reproduction and limits

Evidence is ignored under data/scanner-measurement-20261028:
capture_live.py, initial/final reports and event archives, live-samples.jsonl,
analyze.py, timing-report.json, sofa-card-fixture.json, generate_card_benchmark.py,
card-benchmark.mjs, baseline-card-tests.log, candidate-card-tests.log,
coverage-probe.mjs and coverage-probe.json. Run the benchmark with
`node data/scanner-measurement-20261028/card-benchmark.mjs baseline` (four expected
failures) or `candidate` (34 passing tests). The production files are not patched
by either mode. The measurement sampler has finished; no owned process remains.
