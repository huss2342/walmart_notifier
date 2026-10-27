# Focus and settled reading on every scanner page

October 26, 2026. Source version **2.24.1**.

## Accepted outcome

Keep the home, top-level department and keyword lanes. Before leaving each page,
obtain its visible focused turn, smoothly scroll through the results, collect
late-rendered items, and keep the current page when reading is interrupted.
Retain input/verification yielding, checkpoints, page spacing and failure
isolation. This slice does not change verification input or claim lower bot-check
odds. Owner specifically reports department scrolling and tab switching failing.

## Root cause and implementation

Old reading paths treated denied/manual/background focus and helper timeouts or
errors as successful reading. Navigation also explicitly permitted background
actions after manual-tab/window-focus denial. These paths existed in2.23.13 and
the pending2.24.0 source. New automatic rotation holds these cases; only a grant
with visible actual window focus permits reading/navigation. Read/navigation
requests allow three seconds for worker response; stale grants are rejected.
Explicit rotation-off remains the legacy background opt-out.

An interrupted pass resumes with startAtTop:false. The helper proves ready,
unchanged bottom geometry and item signature for1.2seconds. Unavailable or
no-progress reading has three attempts; cumulative successful scrolling work
is bounded to120seconds per page. User/window contention does not consume that
budget. Existing target recovery isolates failures. Live observer intake remains
available while waiting; repeated same-page retries submit only new/unacknowledged
IDs, while the first intake on each new page includes all stock observations.

Completed proof binds generation/URL, card signature, document height, viewport
height and document readiness. Late-card acknowledgement and navigation recheck
it. Current scroll position is deliberately excluded: the scanner moves upward
to reveal a pager above the true bottom. Including scrollY caused an endless
read/prepare loop, demonstrated RED then GREEN with a real animated pager fixture.
The existing native-transition source-change reconciliation retains its own
fresh-card guard during retry polling.

Parent-owned files: extension/content.js, background.js, manifest.json,
options.html; tests/test_extension_runtime.mjs, fixture-stubs.js,
navigation_focus_fixture.html; README.md, docs/architecture.md and task records.
Helper assignment owned extension/page-scroll.js and tests/test_page_scroll.mjs.
Final read-only review reported no remaining concrete blockers after the pager
proof fix. Unrelated pre-existing repository changes preserved; no commit/push.

## Verified

- All652 extension tests pass, zero failure/skip, using the full CI command.
- RED/GREEN coverage: denied/hidden/no-response focus cannot acknowledge pages;
  incomplete department scrolling resumes; actual window focus is required;
  same-page intake reuse preserves new cards; late cards or height changes
  invalidate reading; missing helper fails within bounds; mapping-based department
  navigation holds focus denial then resumes; owned upward pager positioning
  preserves completed reading.
- Production helper regressions cover bottom settlement, content/geometry
  changes, reduced motion, cancellation, timeout and callback cleanup.
- Both local in-app browser fixtures pass with zero warning/error console logs.
  They execute production content/scroll code against real DOM. Chrome focus
  responses/window focus are explicitly simulated, so this does not prove live
  Chrome activation. The no-op retry fixture accelerates its clock/wait; VM
  tests independently verify the configured minimum page interval.
- Owned local test tabs closed and loopback fixture server stopped after saving
  receipts. No production tab/window/rotation settings changed in this slice.
- Ruff, all extension/helper JavaScript syntax and scoped git whitespace pass.
  No Python/backend source changed this slice. Plan A's prior1739 Python passes
  and one platform skip are separate, not a new run for this slice.

Receipts: data/focus-reading-extension-tests.txt,
data/focus-reading-browser-results.json,
data/focus-reading-browser-proof.png. Initial focused failure/pass logs:
data/focus-reading-initial.txt and data/focus-reading-green.txt. Additional RED
pager proof failure and GREEN results are retained in tool history.

## Live activation verified

Owner confirmed reload October26. Both scanner-status.json and
browser-diagnostics.json report2.24.1. Initial diagnostic mirror lagged scanner
status briefly, then updated normally with metrics; deployed backend supports
results-page-ready and metric validation. No new backend deployment was needed.
Production scanner window1530560111 was brought forward under existing owner
authorization; no layout, tab grouping or scanner preferences were changed.
Window1530560494 remained minimized. Existing scanner and old tabs remain open.

After the real-input grace expired, automatic rotation was observed selecting
all three blue-group owned tabs. Arts Crafts & Sewing1334134 advanced through
pages15,16,17,18 and completed. Observed settled positions on16/17 matched their
document bottoms. The lane then returned to the unsearched root, selected actual
top-level Office Supplies1229749, verified its applied chip, and advanced to2.
Office Supplies was selected/active during observed downward reading at
scrollY3997.78, document height6313 and viewport1348; subsequent page2 observation
was at4772.22 with height6504. No manual scroll, pager or department click was
sent during this validation.

Home advanced5→8. Keywords advanced Home Improvement5→10, completed that target,
and moved to Hotel. New results-page-ready events preceded native transitions in
the retained trace. Department pages increased437→455 and completed targets25→26;
the historical failure count1 did not increase. Keyword failures remained0.
The mirrored metric snapshot recorded9 successful transitions,0 new challenge
episodes,0 verification downtime and0 URL fallbacks. This short clear window
does not establish a reduction in challenge frequency. Walmart emitted React
418/423 errors in its own framework; no extension runtime errors were observed.

Live receipts: data/focus-reading-live-20261026.json (safe status/metric/events),
data/focus-reading-live-office-20261026.json (tab/layout observations), and
data/focus-reading-live-office-20261026.png (actual page screenshot).
Source2.24.1 includes2.24.0 Plan A; both are now activated. No further reload or
Options changes are required. A manually selected non-scanner tab or another
foreground app intentionally holds scanning; selecting a scanner foreground
permits turns. No guarantee that Walmart exposes every eligible item is asserted.
