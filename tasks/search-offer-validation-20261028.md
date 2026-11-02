# Search coverage and offer confirmation — October 28, 2026

**Current instruction supersedes product-page verification:** after reviewing its
traffic impact, the owner explicitly directed restoration of card-based checks.
Source2.26.2 removes the individual-product loader and uses visible Free/OOS card
evidence. Variant/ambiguous-link fixes, keyword routing, existing verifier and
notification burst guard remain. All767 current JS tests pass. See the latest
[ACTIVE.md](ACTIVE.md) section;2.26.0/2.26.1 details below are historical.

**Superseded activation target:2.26.1.** Live2.26.0 exposed a bot-check alert loop:
blocked background product GETs were assigned to clean scanner tabs, whose recovery
probes cleared the fake episode and enabled another. The repair removes that
attribution, adds a separate persisted5minute product-request circuit, and reserves
manual alert attempts with a15minute ceiling. Genuine visible challenges retain
their existing scan pause. All797JS tests pass and two independent reviews approve.
One further extension reload/tab refresh remains; no backend restart is needed.
See the latest section in [ACTIVE.md](ACTIVE.md) for the repair evidence.

Owner requested investigation of missed Reddit finds, comparison of a supplied
public Chrome extension, stronger variant/stock confirmation and old-tab cleanup.
Changes preserve the existing dirty checkout. No backend deployment, claim,
purchase, historical replay or external message was performed.

## Findings

- The supplied ZIP is RR Wheel2.2.18. Static reads through `.NET ZipFile`, without
  executing or installing it, found three query forms: base, base& and base*.
  ZIP `app-discovery.js:14–20,88` and `discovery.js:326–357` send the encoded term
  in `queryNups`. Its stock check reads catalogue text; it does not confirm
  selected variants through product pages. REA tools/CLI were unavailable; this
  is ordinary static source evidence, not REA execution evidence.
- In signed-in Chrome, Large Appliance initially returned3 direct results.
  Entering Large Appliance* through Walmart's actual inline field produced
  `q=large+appliance&queryNups=large+appliance*&page=1` and5 direct results.
  The ampersand form produced an encoded literal `%26` and6 direct results.
  These counts concern the direct result group; recommendation counts differ.
  This establishes a useful difference in that session, not universal wildcard
  semantics or guaranteed recovery of sold-out products.
- [Dryer discussion](https://www.reddit.com/r/RecognizedReviewer/comments/1xhljyx/electric_dryer/)
  confirms reports of finding it through Large Appliance after Dryer failed.
  The owner-supplied expanded comments also suggest Kitchen Appliance, Maker
  and Cordless. The [watch](https://www.reddit.com/r/RecognizedReviewer/comments/1xgorhv/smartwatch/)
  and [ride-on](https://www.reddit.com/r/RecognizedReviewer/comments/1xhmhnz/ride_on/)
  discussions had no visible comments at inspection. A bounded recent-post view
  supplied no stronger additional search pattern; no exhaustive subreddit audit.
- `cardFor` already rejected ambiguous variant relationships, but `collect`
  emitted those links anyway with null value and `available:true`. This was
  reproduced and fixed. Missing card evidence no longer authorizes an item.
- The Soonaway e-bike21279955832 currently showed normal$649.99, Buy now and
  Add to cart on its sampling page; no Claim item. A current LHUKSGF heater
  provided a positive comparison with selected ID20450158955, IN_STOCK, Free
  price and Claim item. Its parent `primaryUsItemId` differs; that parent cannot
  establish which selected variant is free. Nonzero JSON currentPrice is retail
  value even on a free offer, so it cannot substitute for the claim-price check.
- Read-only collector investigation found Ride On21064716574 at07:05:50Central,
  with pro-all/type-other delivery roughly2seconds later. Missing retail value
  prevented a price-band route. iGPSPORT VeRun20187407238 was collected earlier
  and sent; neither its historical availability flag nor the e-bike's proves
  actual earlier eligibility. No exact Domars/Ktaxon dryer title match was found.

## Implemented in source2.26.0

- Fresh auxiliary and primary sweep URLs populate both `q` and `queryNups`.
  Effective query reads prefer `queryNups`. Legacy q-only auxiliary leases keep
  their exact scope until completion; subsequent rounds get native routing.
  Fallback scans reject duplicate or contradictory query parameters.
- The100-term auxiliary defaults and saved list prioritize Large Appliance,
  Kitchen Appliance, Cordless, Ride On, Sports Watch, AMOLED and four literal
  appliance suffix trials. Replaced only Bamboo, Mesh, Vintage, Floral, Fold,
  Gold Decor, Hotel, Stainless, Steel and Glass. Specific item terms and legacy
  High Value presets remain. Custom-list semantics remain unchanged.
- Every candidate passes the shared primary/auxiliary/live-observer admission
  gate before ingest. Catalogue out-of-stock candidates are rejected immediately.
  Signed-in sampling-page GETs require the exact selected `usItemId`, consistent
  IN_STOCK statuses, main Free price and enabled main Claim item. Normal retail
  offers, free shipping and unrelated recommendation/hidden/inert markup fail.
- At most two requests run concurrently and six fresh checks run per report.
  Positive evidence caches5minutes, negative evidence2minutes, bounded2000.
  Partial acknowledgements prevent checked IDs consuming future check budgets;
  backend delivery failures/pending jobs withhold those acknowledgements.
  Unreadable pages retry twice, then soft-defer2minutes without persistent
  dedupe/history ingestion. Blocked/inactive outcomes cannot spend that retry
  budget, and recognizable challenges enter the existing shared pause.
- Future positively owned retired scanner tabs close after fresh coordinator,
  auxiliary, pause and tab-identity checks. Unknown, selected, pinned, shared,
  challenged and manually changed tabs remain protected. Five existing gray
  archived tabs were explicitly closed; all three live scanner tabs remained.
  Chrome's Reopen closed tab can restore closed tabs.

## Verification and activation

- Regression failures were demonstrated before extraction, admission, tab,
  native-query, actual Free-markup and inert/raw-text fixes.
- Final current-version full extension suite:790passed, zero failures/skips.
  Command: `node --test --test-isolation=none tests/test_*.mjs`.
  Evidence: ignored `data/qa-search-all-final-20261028.txt`. The in-process option
  avoids this sandbox's process-spawn EPERM without skipping tests.
- Eligibility suite24cases includes real observed Free-price markup, exact-ID,
  stock, button state, timeout, cache, redirects, pause and inert/raw-text cases.
  The actual primary-message integration uses the real verifier with simulated
  product HTTP responses. It does not establish live background GET success.
- Existing real-browser extension fixture passed with zero console warnings or
  errors, covering reading/pagination, live observation and shared pauses.
  Independent reviews approved admission and eligibility/search scopes after
  concrete findings were repaired. Ruff, JavaScript syntax, native-protocol and
  scoped whitespace checks passed. Backend Python source was unchanged.
- Saved scanner settings were backed up to ignored
  `data/scanner-settings-before-search-validation-20261028.json`, validated and
  read back with all non-keyword fields intact. Fresh live2.25.1 mirror confirms
  all100 terms, first10 matching, Large Appliance scanning, keyword failures0.

**Activation remains:** owner must reload Reviewer Item Relay in Chrome's
Extensions page and refresh scanner tabs to run2.26.0. The available browser
controls cannot reload extensions. No backend restart is required. Live
background product validation and automatic retired-tab cleanup remain unverified
until that reload; the old five-tab cleanup is already complete.

The product check reads returned server HTML without executing downloaded code.
Client-only offers or future markup changes may be skipped. Product verification
adds traffic and latency; cache/pacing limits do not guarantee fewer challenges
or availability when an alert is opened. Existing historical records were not
deleted or replayed. Research/test tabs closed and owned fixture server stopped
after verification; no task-owned temporary resources remain.
