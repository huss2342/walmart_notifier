# Applied department dropdown hierarchy — October 26, 2026

User supplied a screenshot and asked whether the dropdown had been misunderstood.
Investigation and a scoped bug repair are authorized. Preserve parallel scanning,
tab organization, saved keywords/settings and verification behavior.

## Observed Walmart behavior

Separate temporary Chrome research tab, using rendered controls only:

- Root menu: checked All departments, four initial options, Show More reveals
  35 selectable root departments. This agrees with the owner's screenshot.
- Selecting Home closes the menu and navigates to `?catId=4044`; the filter chip
  becomes Home. Result heading reports 1000+ versus the unfiltered page's 687.
- Reopening Home shows its checked parent input `name="Home" value="Home"`
  inside `role="radiogroup" aria-label="Departments"`. Its sibling child group
  has `data-testid="cat_id_radiogroup" aria-label="Home"`; children retain
  `name="Departments"`. Show More is outside both radio groups in their popover.
- Home starts with Kitchen & Dining, Decor, Furniture and Appliances. Show More
  expands to 19 children. Selecting Furniture navigates to `?catId=4044_103150`
  and the rendered count is 56. This confirms a hierarchy, one selected category
  at a time, and a changing chip label.

Only relevant filter markup was retained; no credentials/account DOM saved.
`data/department-hierarchy-20261026/home-filter.html`.

## Root cause and source 2.23.6

The helper recognized department inputs only by names Departments/All departments.
It rejected the applied parent Home as another facet. Panel traversal then stopped
inside the child radio group before reaching sibling Show More, silently returning
only four children. With only a named selected radio and no children, it returned
missing-panel instead of recognizing an empty child list.

The fix recognizes a checked radio whose name equals its rendered value, strictly
inside the Departments header radio group within the rendered filterPopover.
Existing ordinary department option names stay supported; checkbox and unrelated
header structures are not widened. Checked parent remains excluded from discovery.
This repairs hierarchy recognition rather than increasing retry timeouts.

Files: extension/facet-controls.js, tests/test_facet_controls.mjs; manifest and
version assertion bump to 2.23.6. No backend deployment or verifier edits.

## Evidence and scope

- Two meaningful unit regressions fail before repair: applied Home expansion
  returns 4 of 7 fixture children, and a header-only leaf throws missing-panel.
- Four added cases cover full expansion, leaf completion, hidden child selection,
  and rejecting the applied-name exception outside the exact department header.
- Browser fixture uses captured live Home markup and simulated delayed expansion.
  Baseline returns 4 of the 19 observed children; corrected helper returns all 19.
  Header-only simulated leaf: RED missing-panel, GREEN successful empty list.
  Green browser console has no warnings/errors.
- All **562 extension tests pass**, zero failures/skips; Ruff, helper syntax,
  manifest JSON and scoped diff whitespace pass.
- Focused self-review checked the captured sibling structure, option dedupe,
  exclusion of checked parents, strict header scope and brand isolation.

Receipts: data/department-hierarchy-20261026/browser-{red,green}.json,
browser-leaf-{red,green}.json, browser-green.png, extension-tests.txt.
Temporary browser tabs and loopback fixture server are cleaned up at handoff.

## Live 2.23.6 check and pending 2.23.7 diagnostics

Owner activated 2.23.6, confirmed by fresh safe status. First all scanners were
background tabs, so owner was asked to select a scanner and keep Chrome in front;
they replied ready. A further reload yielded owned department1530560592 and
keyword1530560593. Window was focused and a scanner selected; home progressed
from page18 into a new sweep and keywords progressed from Bath toward Bedding.

Department remained on its root parent while assigned Home. One manual native
dropdown-open was performed as a diagnostic; the scanner later expanded Show
More itself, but no successful selection/page coverage was established. Home
was skipped: failures rose13→14, cursor14→15, lastFailureCode changed from retained
missing-panel to unexpected-error. Later Sports & Outdoors, failures15, pages0.
Thus broader live department coverage is still failing; do not mark complete.
Receipts: live-2236-activation.json and live-2236-after-observation.json here in
the data receipt folder. A cropped screenshot is diagnostic only and does not
prove selection success. No CAPTCHA action occurred.

Source2.23.7 adds optional strictly allowlisted lane runtime (phase, reachable,
active, busy, visibility, focusReason, lastActivityAt, nextActionAt) from owned
current-generation document probes. Content records facet focus waits; stale
generation probes are discarded. Four fixed selection failure classifications
distinguish render/document timeout, binding failure and unconfirmed selection.
No raw error text, URLs, page content or credentials are exported. Existing
required schema is preserved; legacy reports remain valid. No scanner timing,
focus/verification policy or keyword/group settings changed in this follow-up.

The new positive probe and schema cases fail without instrumentation. Tests:
565 extension passes; full Python suite1649 passes/one platform skip, including
315 focused scanner/API passes. Ruff, three changed JS syntax checks, JSON and
scoped diff whitespace pass. Focused self-review checked exact allowlists,
current owned generation, stale rejection, bounded values, parallel isolated
probes, legacy compatibility, private data rejection and failure containment.

Backend validator is deployed and healthy by layering only scanner_control.py
on the actual live image. Old/new report validation and actual container startup
pass; before/after compare environment hash, mounts, ports/protections, networks,
user and command equal. Candidate/current image reviewer-notifier:lane-runtime-20261026
(also local). Rollback reviewer-notifier:before-lane-runtime-20261026; retag as local
and docker compose up -d --no-build notifier. Deployment receipts in
data/department-hierarchy-20261026/runtime-deployment/.

## 2.23.7 live diagnosis and source 2.23.8

Owner activated 2.23.7 and left a Walmart scanner selected. Runtime confirms
visible/reachable/current assignment, initially user-active then window-unfocused.
Chrome management independently confirmed the selected scanner's window was not
focused. The agent focused that existing window for the requested live check;
fresh management read-back confirmed focused=true. No tab was selected manually
and no browser window moved/resized.

The scanner opened and expanded the dropdown, yet remained at the parent URL
and later timed out with **selection-render-timeout**. Thus focus and menu
expansion were no longer the missing evidence. The hidden input click could
report successful dispatch without applying a selection. React Aria's official
radio implementation explains this failure mode: label press handles selection,
and label click prevents default native activation. This source is supporting
context, not inspection of Walmart's private component handlers:
https://raw.githubusercontent.com/adobe/react-spectrum/main/packages/react-aria/src/radio/useRadio.ts

2.23.8 prefers the matched department radio's containing label. Before activation
it checks that label contains exactly that one radio, an explicit for attribute
matches its ID, native label.control (when exposed) points to it, and the label
is visible/enabled inside the same discovered panel. Bare-radio fallback and
brand checkbox behavior are preserved. Caller still verifies a changed URL and
fresh cards before binding/ingesting; dispatch alone is never proof of navigation.

Two unit regressions fail prior helper: label-owned selection remains unchanged,
and a mismatched for label was not rejected. Current36 facet tests and **567 total
extension tests** pass. Ruff, helper syntax/JSON and whitespace pass. Focused
self-review checked label association, current panel boundaries, no duplicate
activation, unchanged cancellation and brand behavior. Backend unchanged since
the tested/healthy runtime rollout (1649 Python passes/one platform skip).

Real browser fixture uses captured Walmart Home markup plus a simulated label
handler that cancels native radio activation: RED dispatch=true/applied empty,
changes0/checked=false; GREEN applied Furniture, changes1/checked=true. Green
console has no warnings/errors. Receipts label-browser-{red,green}.json and
label-browser-green.png, extension-2238-tests.txt. Temporary fixture tabs closed
and loopback server8892 stopped.

Owner has already reloaded/refreshed 2.23.8 and replied done. Fresh safe report
confirms that version, current department1530560603 (Toys), keyword1530560604
(Cat, later Cleaning), initially loading/unreachable probes. Subsequent fresh
runtime confirms department reachable/current, hidden, waiting-user with
focusReason=manual-tab-active. Chrome management independently shows unrelated
RunPod tab1530560563 in group47707676 selected, no current scanner selected.
The focused window alone is insufficient: rotation intentionally yields to a
manual tab. The owner reports leaving Walmart selected; another browser task
may be changing selection, but that ownership is not established. Do not change
or close unrelated tabs/tasks.

Owner has been asked to keep a current blue-group scanner selected for a minute
and briefly pause other Chrome automation if applicable. Response pending.
Live selection/page progress must still be checked; no 2.23.8 new failures were
observed during the focus wait (counts remain16 failures/17 completed from before
activation). Do not request another reload. No Options read-backs needed.
Activation receipt: live-2238-activation.json in the data evidence folder.

## Full catalogue scope and browser input (source 2.23.9)

Owner correctly points out that a keyword search narrows the Departments menu.
Live worker0603 was verified on /reviews/claim-product with an empty inline
search field; the keyword lane0604 had its own q=Dog URL. Thus automatic
selection was failing on the full catalogue, not a search-filtered department
list. Native browser click of Baby changed this exact worker to ?catId=5427.
The existing lane subsequently delivered/scanned 18 pages and advanced to Pets;
keyword and home progress continued. Historical failures18 did not increase
during that successful target. Source2.23.8 label.click was insufficient live;
do not describe that source-only regression as a verified live fix.

Source2.23.9 adds ordinary department browser input using Chrome's existing
(debugger) permission. No manifest permissions, credentials, backend or verifier
behavior change. A fixed read-only reader finds only the current assigned,
unselected department radio's associated label in the visible rendered filter
popover. Search queries, wrong origin/routes, changed parent, challenge markers,
duplicate/occluded/offscreen targets are refused. Caller cannot supply script,
coordinates, target tab or input method. Main-frame sender, current owned lane,
generation and exact pending selection are rechecked before input. Read-only
workspace authority requires the selected scanner in its foreground window,
current lease when rotation is enabled, and no real-user activity/pause. It
never focuses a tab or extends a lease. Disabled rotation still respects input.

Content reveals the already matched label in its scroll container, then requests
one native click. Only one expected left pointerdown and click on that label
are excluded from its own input listener; key/wheel/other/repeated input still
cancels. URL and fresh-card binding remain required before ingestion. Revoked
input authority defers the target without spending an attempt. Input commands,
storage and authority waits are bounded; no native retry occurs within a call.
A release is always attempted after a possible press, even after authority loss,
with watchdog cleanup. Unknown press/release preserves a local block across
extension reload and worker recreation; only true Chrome startup clears it.
Late attach is detached before another attachment may start. Other lanes retain
their existing isolation and shared verification pause.

Validation includes live ordinary-click reproduction; unit coverage for fixed
reader scope/association/hit testing, stale/main-frame assignment, searched
catalogue rejection, read-only focus authority, user cancellation, confirmed and
uncertain cleanup, concurrency and late attachment. Browser input still needs
live validation after owner reload; no manual Options inspection is needed.
Official API sources consulted: Chrome debugger API reference and CDP Input
method reference. These document the mechanism, not Walmart compatibility.
https://developer.chrome.com/docs/extensions/reference/api/debugger
https://chromedevtools.github.io/devtools-protocol/tot/Input/

2.23.9 declared full extension suite: **580 passed**, zero fail/skip. Five
changed JS syntax checks, Ruff, manifest JSON and scoped whitespace passed.
Focused self-review checked fixed script/coordinate boundaries, root/search
scope, sender/role lease freshness, conservative unknown input cleanup,
interruption, late attach and failure isolation. No backend source changed;
prior deployed backend1649 passes/one platform skip remains the backend receipt.
Owner asked to reload2.23.9/refresh and leave a blue-group tab selected. Await
that activation before claiming live automatic selection is fixed.

## Top-level-only scope and delayed click (source 2.23.10)

Owner activated2.23.9. Fresh report confirms that version and current owned
worker0621. Chrome showed this worker selected but window unfocused; focused
that existing scanner window once and confirmed the management read-back.
Without any manual facet input, the worker selected Pets, changing the public
URL to ?catId=5440 and showing the applied Pets chip plus fresh pet cards (793
advertised items). Thus native selection is live-proven. However the content
flow later interrupted itself before binding; retained failure changed to
selection-unconfirmed and retry generation442. Do not call pagination fixed.

A delayed-click regression reproduces a concrete content race: Chrome release
acknowledgement may arrive before the renderer click event. Clearing the exact
expected-input arm immediately caused that own late click to be treated as
real-user input, cancelling the selection poll and later rejecting its cards.
RED receipt late-facet-click-red.txt. 2.23.10 keeps only the one matched left
click armed for at most one additional second after a successful input reply.
It consumes the arm on click; all unrelated, repeated, keyboard or expired
input still cancels. URL/card binding and interrupted-selection rejection remain.

Owner explicitly narrowed the requested scope: every page of every TOP-LEVEL
department, no subdepartment traversal. That supersedes the earlier cap-based
child discovery plan. Content now discovers departments only at the root;
the coordinator independently rejects department candidates from bound facets.
Optional brand discovery within departments remains available/off by default.
A queue fingerprint safely invalidates legacy recursive queues at upgrade,
reuses the owned department tab, and starts fresh root discovery. Home/keyword
settings and shared item deduplication remain unchanged. The 25-page cap still
reports its existing limited-coverage status without starting child scans.
Options, README and architecture wording reflect this accepted scope change.

All **583 extension tests** pass, zero fail/skip, including delayed click,
top-level-only discovery, child enqueue rejection, optional brands and legacy
queue migration. Two changed JS syntax checks, Ruff, manifest JSON and scoped
whitespace pass. Focused self-review checked finite arm lifetime/event count,
real-input cancellation, binding guards, queue invalidation and lane isolation.
Backend unchanged. Owner asked to reload2.23.10 and refresh; live activation
and automatic bind/pagination still pending. No Options read-back needed.

2.23.10 activation confirmed after owner reply done. New root-only department
round93/generation444 owns tab1530560628, zero failures, one initial discovery
target. Keyword tab1530560629 retained round5 progress155pages/32targets (later
157pages/33targets), home1530560603. Exactly one blue Reviewer scans group now
80647340; gray archive1520524624. Old numeric group IDs are no longer current.

Live root discovery is waiting-user/window-unfocused. Main scanner window
1530560111 was selected on628 but foreground switched to the other Chrome
window1530560494. Focusing the selected scanner window once confirmed true,
but it lost focus again within12seconds before discovery could get its slot.
Do not fight another browser task or change unrelated tabs/windows. Owner was
asked to pause other Chrome work for one minute, reply ready, then return to a
blue-group Walmart tab. No new source changes/reload needed. Still awaiting
live root discovery, automatic binding and page advance; tests remain583pass.

Owner clarified that window activation may be part of scanning, explicitly
authorized temporary disabling/restoring of that behavior, and authorized
controlling Chrome/window focus for validation. Rotation was true and has been
TEMPORARILY set false in data/scanner-settings.json. Fresh extension status
confirms false. Receipt temporary-focus-settings.json records restoreRequired.
Restore only rotateScannerTabs=true after the test; preserve all other settings.
Root discovery completed: 36 top-level departments + discovery target =37,
round93, generation445, targetSeasonal, zero failures. It then defers native
input while another Chrome window regains focus. Tab rotation false did NOT
prevent the other window gaining focus, so do not attribute this to rotation.

For the explicitly authorized isolated Chrome test, other window1530560494
was TEMPORARILY minimized (was maximized and focused), and main scanner window
1530560111 was focused on selected department0628. Read-back confirms main
focused=true / other minimized. RESTORE other window state=maximized after
the test (and restore focus if appropriate); no tabs moved/closed. Keep source
2.23.10; no new reload requested. Both temporary changes need cleanup before
ending the turn, even if validation fails.

## Removed-label click case (source 2.23.11)

The temporary test discovered all36 current top-level departments (37 including
root discovery), then automatically selected Seasonal at ?catId=1085632. Yet
its own native click was still counted as user input before binding. Keeping
the original label alive was a false assumption: a press can close/unmount the
popover before the final click, retargeting it to the former common ancestor.
The new regression is RED on2.23.10 (retargeted-facet-click-red.txt).

2.23.11 records the pointer ID and point only after the one expected trusted
left pointerdown inside the exact connected label. Its one click may use that
label or the same pointer/point (within2px), within the existing bounded arm,
even after the label is removed. It consumes the arm on click. Another press,
changed pointer, moved point, unrelated event or expiry still cancels. Those
positive and negative cases all pass, as does fresh-result binding. Console
debug for the first unmatched input during a facet transition contains only
finite event/boolean metadata, with no coordinates, DOM text, IDs or URLs.
This aids a live failure diagnosis without another status schema rollout.

All **587 extension tests** pass, zero fail/skip. Content syntax, Ruff, manifest
and scoped whitespace pass. Focused self-review checked strict initial label,
pointer pairing, finite lifetime/one event, all real-user cancellation paths,
unchanged generation/verification and fresh-card binding. Backend unchanged.

Temporary test settings have ALREADY been cleaned up: rotateScannerTabs=true
in both file and fresh extension status, other Chrome window1530560494 restored
to maximized/focused. Receipt temporary-focus-settings.json has restoreRequired
false. Owner requested to reload2.23.11/refresh; activation/live bind/pagination
pending. Owner already explicitly authorizes brief window isolation and
rotation disable/restore for this test; do not ask for another ready sequence.
Current2.23.10 department628, keyword629, primary603; obtain fresh IDs after
reload, and use the one current blue group (last80647340). Source retains
TOP-LEVEL-only scope. Do not claim live paging fixed merely from587 tests.

2.23.11 is active (department0632 / keywords0633). Automatic Seasonal selection
again succeeded but still self-interrupted; new console event was captured at
17:01:34.691 UTC, rendered only as 'Object' by the browser tool, so its fields
were unavailable. Round93 advanced with failures1/pages0 to Home generation449.
Do NOT claim this is fixed from587passing tests. Current diagnostic hypothesis
must be replaced by event evidence before another behavioral patch.

Source2.23.12 ONLY serializes that bounded console trace into the message,
including event type, expected-label booleans, pointer/point match, button/detail,
clamped pointer ID and allowlisted tag. No coordinates, DOM text/IDs/URLs or
behavior changes. Current587tests pass. Need activation to read the exact event.
Temporary isolation was cleaned up: rotateScannerTabs=true restored in file,
other window0494 maximized again (Chrome restored it to foreground on maximize).
Receipt temporary-focus-settings.json restoreRequired=false. No cleanup pending.

## Page-provided department destinations (source 2.23.13)

2.23.12 activation exposed an additional correctness issue: the worker was
paging ?catId=4044/Home while its queue target said Clothing, Shoes & Accessories.
No readable interruption trace survived that full-page transition. Binding any
changed catId is insufficient; correct category identity must be enforced.

Read-only inspection of the public __NEXT_DATA__ SearchSortFilterModule on the
unsearched root found all immediate cat_id facet records:36 initially,35 in a
later snapshot (counts/order change). Each has visible department name, numeric
ID and a matching public cat_id query parameter. This data is document-backed;
no private application objects, guessed IDs or undocumented requests were used.
The root variables query/catId/facet are empty, and match the current page.
The root list is not the narrowed facet list from a keyword/category search.

2.23.13 reads these immediate root records with department-data.js. It requires
unsearched reviewer scope and matching page variables, one department module,
bounded nonempty unique IDs/names and matching source parameters. It never
recurses into child values and copies no store/location or other source params.
Destinations contain only the observed numeric catId on the reviewer route.
Coordinator validates every destination independently and can bind only that
exact assigned category. The queue fingerprint resets legacy/mislabeled queues.

Normal department selection is now a guarded full navigation through the same
navigateWithFocus path as pagination. No simulated dropdown/native click is
needed; the root data can be read hidden or with a blank gallery. It still
observes page spacing, input cancellation and shared verification. Fresh-page
binding requires the server category ID and rendered applied chip to agree.
Clothing's root name is 'Clothing, Shoes & Accessories', but its applied/server
name is 'Clothing'; category ID5438 identifies it without a guessed label alias.
Invalid/stale data fails the department target with bounded recovery instead of
an empty complete round; other lanes remain independent. Existing legacy/native
and optional brand code/tests are retained, but are not the normal root-department
path. No backend, permissions, credentials or verification changes.

A temporary research tab navigated to the OBSERVED Clothing destination5438.
Live URL, applied Clothing chip, fresh products and server catInfo5438/Clothing
all agree. Screenshot clothing-destination-live.png. Research tab was closed.
Reduced fixture tests/fixtures/reviewer-departments.json includes only observed
names/IDs and cat_id; account/store data is excluded. Its count35 is dynamic.

All **620 extension tests** pass, zero fail/skip, including captured full-list
parsing, searches/children/duplicates/drift rejection, strict destinations,
hidden/blank root discovery, focus-before-full-navigation in foreground and
background, mismatched category/chip rejection and shortened chip names. Four
changed JS syntax checks, Ruff and scoped whitespace pass. Focused self-review
checked bounded DOM parsing, exact origin/query scope, full-page stale-card
isolation, generation/verification cancellation, independent failure and queue
migration. No tests were removed; the prior587 tests remain in the suite.

Temporary window/rotation changes from2.23.12 are ALREADY RESTORED: rotation=true,
other window1530560494 maximized. Owner asked to reload2.23.13/refresh; live
scanner automatic binding/page advancement still pending. It should no longer
need an isolated foreground window to discover/navigate departments. Do not ask
for another ready sequence or minimize other windows unless a new need appears.

## Live automatic verification completed (2.23.13)

Owner reloaded2.23.13. Fresh backend snapshots confirm that version and a new
department round94/generation459 on tab1530560668. Root discovery produced33
current top-level departments (totalTargets34 includes the discovery target).
With no manual selection/navigation, the worker loaded Home4044 and paged1–25.
The applied Home chip was observed on pages6–9; subsequent safe navigation
events retained generation459 through25. A normal page8 interruption recovered
without skipping the target. At completion the lane recorded pages25,
completedTargets2, coverageLimitedTargets1, failures0 and generation460.

The worker then returned to the root, waited its normal pacing, loaded5438,
rendered the exact Clothing applied chip and advanced automatically through
pages2 and3. This proves both complete target pagination and target handoff;
the full33-department round was not awaited. The UI's shortened Clothing name
is accepted through the fresh category ID contract, with no manual dropdown
click or guessed category ID used during this verification.

Parallel evidence: primary1530560603 completed its17-page catalogue, and keyword
1530560669 advanced from Personal Care through Playpen and Repair to Shade.
Its completedTargets increased47→50 and pages245→259; historical failures
remained2. The new department round had0 failures throughout this check.
Grouping stayed in one blue Reviewer scans80647340 with exactly the three owned
tabs. No duplicate scanner group was created by the reload.

The main scanner window1530560111 was focused once for the live scrolling check.
No other window was altered and no settings or checkpoints were changed.
Home page13's viewport was observed at scrollY5118.889 after being at0 on prior
pages, and backend phase reading-page was observed during subsequent pages.
Smooth animation is covered by the existing passing browser/unit receipts;
this observation separately proves the deployed scroll pass runs. Rotation
remains enabled. No new verification challenge occurred or was solved here.

Safe receipts in data/department-hierarchy-20261026:
- automatic-22313-home-progress.json: intermediate status/navigation history.
- automatic-22313-handoff.json: Home completion/cap counter and Clothing lease.
- automatic-home-22313.png: actual automatic Home results and applied chip.

The sanitizer currently maps the coverage-limit lastError into generic
lastFailureCode unexpected-error even though failedTargets is empty and
failures0. Do not misdiagnose this cosmetic status mapping as a new failed
selection; coverageLimitedTargets1 and recorded25 pages are the actual result.
All620 source tests remain passing; no production code changed during this
live verification. No further reload/Options/ready request is required.
