# Scanner coverage repair — October 28, 2026

## State and authorization

Owner approved implementation with “go for it” after the measured efficiency
review. Extension **2.26.4 is implemented and verified, awaiting Chrome reload**.
The two-module backend logging update is live and healthy. Last live report still
identifies extension **2.26.2**; no live speed improvement is claimed yet.

Keep three scanners, all 100 keywords, brands off, existing 15-second requested
pacing, card-only eligibility and the existing verification helper. No product
detail requests or new scheduling policy were introduced.

## Implemented behavior

- Card recognition reads image alt text when a link has no aria-label. The
  captured Free/$35.99 Sofa primary is recovered; its image-labelled swatches
  remain excluded. Explicit conflicting labels and stock protections stay intact.
- Numeric pager labels are a lower bound, rather than proof of a last page.
  Scans continue through moving pager windows and beyond 100 when ordinary pager
  evidence supports it. Completion requires a matching current-page disabled
  Next control consistent with numeric controls, validated single-page metadata,
  or the existing repeated/fresh-document empty-tail confirmation.
- Missing interior pages prevent completion. After gap recovery, the old frontier
  is revisited; a populated frontier requires fresh terminal evidence. Conflicting,
  hidden, stale or absent controls cannot certify completion.
- The existing blind-navigation safety budget is retained. A populated endpoint
  without terminal proof becomes unresolved and retains receipts, instead of
  reporting success. Recovery starts at the first unread page, including beyond
  100. There is also a defensive 100,000-page bound that remains unresolved if
  reached. These bounds do not substitute for verified all-page coverage.
- Auxiliary per-target receipts survive bounded retries, round rollover,
  explicit Restart, tab loss, browser/session recreation and verification pauses,
  including the paced interval before a target starts. Unresolved work survives
  the next round and runs ahead of fresh refresh work. Healthy targets still run
  after a failed target exhausts its normal bounded retry attempts.
- Discovery and duplicate aliases no longer inflate successful department counts.
  Legacy target advances are unverified and require rechecking. Status separates
  successful completions, attempts and unresolved targets. New fields are optional
  in backend validation, preserving compatibility with the installed older client.
- Round timestamps survive status export. `completedAt` means the attempt round
  ended; only `status: complete` establishes successful coverage. `pages` represents
  coverage receipts, including resumed receipts, not newly issued requests.
  Diagnostic `coverage-unresolved` is separate from a successful sweep.

Changing a configured scope still rebuilds that scope as before; unchanged-scope
recovery retains outstanding work. Another department worker, brand discovery,
keyword removal and five-page turns remain unimplemented proposals.

## Verification

Regression cases reproduced failures before implementation. Final checks:

- `node --test --test-isolation=none tests/test_*.mjs`: **818 passed**, no failures,
  cancellations or skips. Includes 34 extraction and 43 auxiliary cases.
- `.venv\Scripts\python -m pytest -q tests/test_scanner_control.py
  tests/test_browser_diagnostics.py tests/test_server.py -p no:cacheprovider
  --basetemp data/scanner-implementation-20261028/final-python-temp`:
  **606 passed**. Isolated Windows test directories required normal filesystem
  permissions; these checks exercised strict status validation and disk recovery.
- Real browser replay: **10 checks pass**, including the captured public Sofa
  DOM, variant exclusion, stock exclusion, visible/stale pager controls and
  completion policy. Run `.venv\Scripts\python tests/serve_extension_fixture.py`,
  then open `http://127.0.0.1:8879/coverage-fixture` to reproduce.
- Actual content-script VM replay traverses a 121-page department across fresh
  documents, preserving all 121 page receipts and the exact 242 initial/late-item
  union. Existing failed-acknowledgement and interrupted-reading cases pass.
- Independent review findings reproduced and fixed: contradictory disabled Next,
  stale saved terminal after gap recovery, Next hydration across checkpoint/storage
  awaits, paced query transitions and delayed navigation focus grants, and carried checkpoint overwritten by
  an empty working slot during paced pause/recovery. Final review has no required
  findings outstanding.
- Scoped Ruff and whitespace checks pass. Verification-related files,
  `runtime-policy.js` and `page-scroll.js` retain their pre-change hashes. The
  background change only exports the additional bounded coverage fields.

Browser QA is a local replay, not a claim about live Walmart throughput or stock.
An initial browser harness assertion used the wrong value field, and another
expected an ambiguous OOS card to be emitted. Those harness assertions were
corrected; the final browser run passes with production card safeguards intact.

## Backend activation

Only `src/scanner_control.py` and `src/browser_diagnostics.py` were overlaid on the
exact then-running image `cca7c831d651`. Both live baseline hashes matched the
saved source baseline before building, avoiding unrelated dirty-tree changes.

Activated **13:08:27 CDT**, healthy image
`sha256:f34a0192132d24440bdbc037e6b3a12b99d5718f93239c3bd13ca2c9381ba144`.
Environment key/value pairs, HostConfig, mounts, networks, saved scanner/relay
settings and launch configuration match the baseline. Live module hashes match
the tested source. Existing 2.26.2 status synchronization remains healthy with
100 keywords and brands off. No Discord routing, pricing, ACL or notification
preference changed in this release.

The first activation rolled back automatically because the preservation script
hashed environment-variable array order. Names and values matched the preflight
comparison. The check now hashes the exact name/value mapping in canonical order;
the successful retry retained every setting. Rollback image:
`reviewer-notifier:before-scanner-coverage-20261028`.

Private receipts, baseline snapshots, test logs, preservation hashes and browser
screenshot: `data/scanner-implementation-20261028/` (ignored). No credentials are
printed or included in this document.

## Activation and measurement next step

Reload Reviewer Item Relay in Chrome's extensions page, then refresh the three
reviewer documents. Browser-control tooling cannot reload the extension, so the
owner was asked to perform this final activation step. Confirm a fresh local
status report identifies 2.26.4 and includes attempted/unresolved fields before
attributing any subsequent measurements to this release.

Retain the prior measured 30-minute baseline: departments60/home53/keywords12
read-ready pages (4.17/min combined), department navigation median2.05s,
ready-page interval19.31s, and Couch roughly three minutes of false blank retries.
Those old completion counters are not a verified all-department-round baseline.

After activation, measure fresh read-ready events, target start/end times,
verified full department round duration, unresolved ranges and keyword/home
revisit times with unchanged settings. A faster first visit or a higher receipt
count alone does not prove better coverage or throughput. Only consider more
workers, brands or different scheduling after corrected operation is measured.
