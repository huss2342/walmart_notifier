# High-value keyword coverage — October 27, 2026

Owner requested adding the discussed expensive-product searches and investigating
items seen in r/RecognizedReviewer but missed by the notifier, particularly a
smartwatch. Changes were made in the existing dirty checkout, preserving unrelated
work. No backend source change, restart, Discord mutation or historical replay.

## Implemented and active

The explicit saved list in ignored `data/scanner-settings.json` grew from65 to100.
All original terms and other settings were preserved. The35 additions run first:

Smartwatch; Smart Watch; Fitness Tracker; Smart Ring; Laptop; Tablet; Smartphone;
Television; OLED TV; Camera; Drone; Gaming Console; VR Headset; Graphics Card;
Robot Vacuum; Espresso Machine; Refrigerator; Washer; Dryer; Dishwasher;
Air Conditioner; Generator; Lawn Mower; E-Bike; Electric Scooter; Mattress;
Stroller; Car Seat; Vacuum; Monitor; Projector; Pressure Washer; Power Tool Set;
Luggage; Desk.

Saved with the existing validating atomic `ScannerSettingsStore`, after creating
`data/high-value-scanner-settings-before-20261027.json`; read-back matched. The
extension fetched the change through its existing settings sync. At23:37:44UTC
(18:37:44 Central), live2.25.1 reported100 targets, Smartwatch completed, Smart Watch
running, one page processed and no target failures. A later sanitized receipt is
in `data/high-value-live-20261027.json`. Refresh remains5minutes and requested page
delay15seconds. No new scanner tabs/concurrency were added.

Source defaults now match these100 terms in `extension/sweep-modes.js`. Explicit
custom lists still replace defaults. The legacy62-term High Value sweep is
unchanged; this request concerns the additional keyword tab.

## Implemented; activation requires extension reload

Source version2.25.2 corrects `effectivePageSeconds` in `runtime-policy.js`.
Auxiliary sweeps have `targetKey` and mode `none`, so the old shared policy spread
75% of the homepage refresh interval across every individual keyword/department.
For the actual5-minute refresh and a two-page keyword, the old nominal interval
was90seconds despite the15-second setting. The fix uses the requested15-second
baseline for additional scans, preserving jitter, delay minimums, elapsed loading
time, homepage pacing, full page reading, verification and focus behavior.

The available browser tool rejects claiming `chrome://extensions/`, so it cannot
reload the extension. Owner must reload Reviewer Item Relay once there and refresh
the scanner tabs to activate2.25.2. No backend restart is needed. Saved keyword
additions are already active independently of the reload.

## Evidence about missed watches

Read-only queries of `data/discord_bridge/collector.sqlite3` found:

- Oukitel BT103 (`21203910894`) and its white variant were first collected
  October26 at14:05:20Central. Pro-all and electronics jobs expired as stale at
  14:35:20 without `sent_at`, during the previously documented Discord outage.
  The delayed recap was sent October27. V3 (`21140008317`) had the same immediate
  delivery failure. These are proven missed alerts despite successful collection.
- Haulvean smartwatch (`16077503485`, $59.99) and two other watch listings
  (`19032852533`, `19029654136`) already existed in the collector. The live
  Smartwatch results displayed those three as out of stock on October27. Current
  availability does not establish their availability at an earlier Reddit post.
- Pro-all sent26 items in the hour ending around18:32Central, with a latest send
  at18:32:03. The historical outage therefore does not establish a current outage.

The specific watch the owner meant has not been identified; an optional request
for its Reddit or Walmart link remains unanswered. Do not infer all missed items
share one cause, clear dedupe or replay expired jobs.

## Research and remaining coverage limits

[Categories](https://www.reddit.com/r/RecognizedReviewer/comments/1sm37ao/categories/)
contains firsthand reports that broad and specific terms can return different
sets, including landscaping/landscape and gardening/garden. This supports
retaining existing broad searches alongside the new product terms.

[How do you search?](https://www.reddit.com/r/RecognizedReviewer/comments/1wy9idi/how_do_you_search/)
reports finding a shoe rack under "hall tree" and items accessible through shared
links that do not appear even under their full title. Comments also describe
price/new sorting and punctuation-based search experiments. These are community
reports, not verified Walmart search contracts; no guessed URL parameters or
punctuation behavior was added to production.

The code audit found serial keywords, two retries before skipping an unusable
target, and full-round refresh scheduling. Empty searches incur hydration and
confirmation waits. Adding terms extends the round;5-minute refresh does not
mean every term is checked every5minutes. Existing Walmart capped result sets
are marked partial, and top-level department scanning cannot expose every hidden
listing. The owner previously requested top-level departments only, which remains
in effect. No per-page extraction truncation was found: all unique rendered IDs
are collected and sent in batches of200.

## Verification and review

- New regressions first reproduced90s instead of15s on auxiliary targets and
  missing wearable defaults; homepage control passed.
- `node --test --test-isolation=none tests/test_high_value_search.mjs tests/test_auxiliary_scans.mjs tests/test_auxiliary_options.mjs tests/test_scanner_settings.mjs`:
  67passed.
- `node --test --test-isolation=none tests/test_*.mjs`:738passed, zero failures/skips.
- After the version-only update, integrated verification:29passed.
- `node --check` on changed runtime scripts, native protocol generation check
  and scoped `git diff --check` passed.
- Independent read-only reviewer approved the scoped patch without required
  findings; reviewer did not independently rerun tests or inspect live settings.
- Normal Node test isolation hit sandbox `spawn EPERM`; running the same tests
  with supported `--test-isolation=none` succeeded. No check was suppressed.

Read-only explorer and reviewer finished; no owned servers or new browser tabs
remain. The existing keyword tab was inspected without interacting with claims.
Next action: owner extension reload, then verify mirrored version2.25.2 and the
new pacing on a target with multiple pages.
