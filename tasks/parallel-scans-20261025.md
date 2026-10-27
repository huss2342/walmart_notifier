# Concurrent catalogue coverage — October 25, 2026

Historical 2.20 receipt. The owner subsequently activated 2.20 and 2.21; current
activation/status work is tracked in
[scanner-control-20261025.md](scanner-control-20261025.md) for 2.22.

State: source 2.20.0 implemented and locally verified; live activation awaiting
the owner's extension reload and Walmart refresh. Owner authorized live filter
research, preserving the home scan, concurrent department/keyword scanning,
independent recovery, and existing scrolling/pacing. No purchases or claims.

## Observed live evidence

- Unfiltered reviewer catalogue: 631 advertised items, 16 pages.
- Home (`catId=4044`): 1,000+ displayed items, 25 pages. A fresh rendered
  `__NEXT_DATA__` response reports aggregatedCount=3932 and maxPage=25.
- Home exposes Kitchen & Dining, Decor, Furniture, Appliances and 15 more
  subdepartments after Show More. Inputs expose department names, not IDs.
- SPA navigation updates the URL before cards/filter choices settle. Initial
  `__NEXT_DATA__` can remain stale after navigation; never use it as fresh cards.
- Expanded the actual root filter to enumerate 35 departments. Brand controls
  use `data-facet-option="brand--NIDA"` with checkbox name/value `NIDA`, not a
  generic Brand input name. Selecting it yields `facet=brand:NIDA` and 18 items.
- The owner cleared the research verification dialog manually. Research resumed
  without solver input. No purchases/claims or verification settings changed.
- Native search uses `queryNups=Furniture`; the extension's clean `q=Furniture`
  returns the same seven primary results plus 15 suggestions. A fresh document
  has count=22, maxPage=1, hasMorePages=false, and no DOM pager. Single-page hints
  are used only with matching initial document/query/category/facet/page data;
  observing any SPA URL change permanently invalidates that document's hint.

## Implementation contract

Retain the existing primary coordinator. Add at most two extension-created tabs:
departments and keywords. Each has durable target queue, lease/generation,
checkpoint, completion clock and failure history. Never adopt manual filtered
tabs. All use the same collector, acknowledged delivery, pager/scroll helpers,
user input grace, and server dedupe. A real challenge pauses the shared session.

Department discovery reads live controls, expands Show More and queues every
root department. Split departments reaching the observed 25-page cap through
their actual children. Traverse every page in each target. Optional brand
discovery remains off by default. Keywords run from clean catalogue URLs with
configurable terms including Furniture, Chair, Chairs, Couch, Sofa and Pet.

Selection uses the actual filter input and waits for both changed URL and fresh
cards/confirmed empty state. Missing/changed controls fail only their target;
bounded retries, watchdog and later rounds keep other work running. Queue limits
must surface incomplete coverage, never silently claim exhaustive results.

Auxiliary settings are local extension settings, separate from the existing
four-field backend scan-settings contract. After local verification the owner
explicitly requested department and keyword scans enabled by default; both now
start after reload without manual checkbox setup. Brands remain off. Explicit
later disable choices are preserved.

## Ownership

- Manager: content integration, shared query helpers, manifest/CI, integration
  regressions, browser fixtures, notes and final verification.
- scan_architecture: auxiliary manager/background integration and manager tests;
  existing runtime loader imports only.
- facet_controls: facet-controls.js and its tests.
- scan_options: options.html/options.js and separate UI tests.

## Verification and review

- Declared extension command plus the three new suites: **412/412 pass**, zero
  failures/skips. Receipt: `data/parallel-scans-extension-final-20261025.txt`.
- Full Python suite: **1478 pass, one platform skip**. Receipt:
  `data/parallel-scans-python-final-20261025.txt`. No backend implementation changed.
- All extension/companion JavaScript syntax, manifest JSON, repository Ruff and
  scoped whitespace checks pass. Server source `--help` starts successfully.
- Actual production manager/content/facet/pager/observer scripts in two real
  browser iframe documents passed **31 pages** (27 department, four keyword),
  three Show More expansions, two URL-before-card guards, one lazy pager reveal,
  30 duplicate observations, 31 checkpoints, manager recreation and simultaneous
  verification pause. A missing department was skipped after bounded retries;
  the keyword round completed. Home state remained unchanged. Ingestion/Chrome
  APIs and timing are simulated; this is browser integration, not live activation.
- Both IAB and a fresh Chrome run pass; the fresh Chrome console has no warnings
  or errors. Receipts: `data/parallel-scans-browser-20261025.json` and `.jpg`.
- Initial browser evidence exposed concatenated inline pager text (`12` instead
  of pages 1 and 2). Individual control/leaf parsing and regressions fix it.
- Existing visible IAB scrolling/card-guard fixture passes eased motion, upward
  settling, unchanged deadline, recovery, verification, live relay and reload
  cleanup with a clean console. Receipt: `data/parallel-scans-scroll-20261025.txt`.
  The first external Chrome animation run did not finish within its one-second
  budget in that window; the same unchanged motion code passed visibly in IAB.
- Options real-browser preview confirms both defaults checked, brands unchecked,
  successful save and partial/complete statuses with no console warnings/errors.
- Independent review approved after required repairs for unbound selection
  recovery, completed-round pause/reschedule, exact input values, stale lease
  acknowledgements, lazy cap discovery and completion checkpoint preservation.

Focused RED evidence is retained under `data/parallel-content-red-20261025.txt`
and `data/parallel-coverage-red-20261025.txt`; manager, facet and Options workers
also demonstrated failing cases before implementing their modules. Baseline
snapshot: `data/parallel-scans-before-20261025/`. Previously pending workspace
changes are preserved; no commit, push or backend deployment.

## Activation and remaining evidence

Owner requested both additional scans enabled by default instead of checkbox
setup. Reload Reviewer Item Relay **2.20.0** in Chrome's Extensions page, then
refresh Walmart. The browser connector cannot operate that internal page.
Brands remain off; later explicit disable settings remain respected. Observe
two extension-created tabs, department/filter progress, keyword progress and
normal home progress. Complete live rounds and long-term challenge rates remain
unmeasured; do not claim they passed from local fixture evidence.

The final read-only diagnostic receipt still reports live **2.19.1**, with a
verification pause. The newly visible blocked tab is not evidence of auxiliary
activation. Receipt: `data/parallel-scans-live-before-20261025.json`. The owner
said they will reload/refresh after defaults are enabled, so this is the exact
handoff boundary. A current verification check must be cleared manually before
normal shared-session monitoring can resume.

Workers have finished. Temporary fixture tabs and loopback servers belong to this
task and are closed after QA; user Walmart tabs and live notifier stay open.

Primary references:
- https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle
- https://developer.chrome.com/docs/extensions/reference/api/alarms
