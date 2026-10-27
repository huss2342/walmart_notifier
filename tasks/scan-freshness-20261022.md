# Scan freshness and recovery — October 22, 2026

## Authorized outcome

The owner requested removal of the prolonged post-verification speed penalty,
immediate return to saved timing after verification, a fix for empty final-page
loops, substantially faster scans, useful visual evidence on future challenges,
and investigation of FlareSolverr. Existing unrelated work remains untouched.

## Implemented and verified

- Source extension version **2.15.1** includes the removal of challenge-history inflation from
  page spacing and scan targets. Unresolved verification still cancels navigation.
  Confirmed resume clears old completion timestamps and immediately permits a
  fresh scan at the saved speed. Historical counts remain diagnostic data.
- Empty results are rechecked in the document after **2 seconds**, then reloaded
  once after another **2 seconds**. A fresh document confirms an empty tail,
  including the observed stale page 19 after populated pages 1–18. The confirmed
  boundary is current page minus one; earlier gaps are visited before completion.
  A smaller pager alone cannot skip unread earlier pages. Version 2.14 can reuse
  recent document-local proof after two empty observations; every pass still
  visits the boundary, and proof expires without renewal (details below).
- Confirmed empty interior pages and a 30-second blank-page timeout request a
  new pass. The background validates its lease under the coordinator lock,
  clears old progress and returns a bounded page-1 recovery. A failed pass does
  not produce a successful completion event.
- Native pagination now prefers the recognized pager's adjacent **Next Page**
  control when its current marker agrees with the URL. Exact destination links
  retain priority; nonempty filters, disabled controls and page gaps are guarded.
  A click with no normalized URL change falls back after a five-second start
  window; started transitions retain 30 seconds to render changed cards.
- Verification evidence captures only the active, recorded, still-challenged
  Walmart tab, checking before and after capture. JPEG and allowlisted diagnostic
  JSON downloads go to `Downloads/ReviewerItemRelay`; one accepted image download
  per outage prevents duplicates. Pixels, HTML, cookies and credentials are not
  added to the ordinary diagnostic history or mirror. The screenshot itself
  contains visible page contents. Chrome may require clicking the extension icon
  to grant `activeTab`; Options opens after the capture attempt. Download settings
  and interruption can affect final saving. No `all_urls` or debugger grant added.
- The running notifier's editable JSON is now **5 minutes / 15 seconds / none**,
  confirmed by authenticated GET `/relay-settings`. A five-minute target is not a
  full-catalogue discovery guarantee; site loading, retries and the one-minute
  minimum post-completion rest still contribute.

Previous 2.14 verification: **215 JavaScript tests passed** across runtime, extraction, timing,
evidence and the operator helper; all extension JavaScript syntax checks passed; **1,036 Python
tests passed** in the full suite; Ruff and whitespace checks passed. Windows
sandbox process spawning required Node's `--test-isolation=none`; pytest's fixture
directory permissions required the approved outside-sandbox local test run.
An independent read-only reviewer approved the material code with no required
findings in the implementation review. The local notifier image is updated and
healthy; 2.14 activation and a full live pass are now verified (October 23 audit
below), with native-navigation problems and delivery latency still observed.

Real browser fixture verified stale-card guards, live observation, first-empty
confirmation, confirmed empty-tail completion, verification cancellation, saving
5/15 timing and credential-free export. Both fixture tabs had no warnings/errors.
The updated browser fixture also passed a delegated native Next handler and its
stale-card guard. Its added page-3 controls initially contaminated the later
empty-tail scenario; cleaning up those fixture controls restored the intended
page-2 setup. The subsequent live 2.12 scan is recorded in the latest trial section below.

## Research and activation limits

FlareSolverr's [official README](https://github.com/FlareSolverr/FlareSolverr#captcha-solvers)
says its CAPTCHA solvers currently do not work. No dependable support for this
HUMAN Press & Hold challenge was demonstrated and no solver service was added.
HUMAN's [bypass-token documentation](https://docs.humansecurity.com/applications/create-a-challenge-bypass-token)
requires the site's operator settings; it does not supply a token for this
extension. This research does not establish that every other automated method
is impossible. No unattended solve is claimed.

The old **2.10.0** runtime remained active during development. Its repeated
empty-page recovery reached a real verification overlay at approximately 13:25
Central. The live page was inspected and captured through the browser tool.
The browser tool rejected navigation to Chrome's internal Extensions page and
explicitly disallowed workarounds, so a **manual extension reload was required**.
The owner subsequently reloaded 2.12; activation and a complete live scan are now
verified in the latest sections below.
A one-attempt browser verification test was offered with the tool's required
per-attempt user confirmation. The owner subsequently approved it; the outcome
is recorded below. No CAPTCHA interaction was made without that confirmation.

No notifier rebuild is needed for these extension-only behavioral changes.
No commit, push, public message, hosted publication or recurring automation was
performed. The owner completed the manual Reviewer Item Relay reload in Chrome Extensions.
New telemetry reports 2.12.0 and
5/15/none. Future live scan durations and prompt frequency should be evaluated
from that version's bounded local diagnostics.

Closeout: both local fixture tabs and the task-created blank Chrome tab were
closed; the port-8879 fixture server stopped; the task's pytest scratch directory
was removed after absolute-path verification. The owner's reviewer tab remains
open. Readback confirms that even the old runtime has now
picked up 5/15/none; its version remains 2.10.0 until the manual reload.

## Approved verification experiment — 13:33–13:36 Central

The owner explicitly approved one attempt on the current Press & Hold and asked
for as much useful diagnostic evidence as possible. The owner also noted that
the required hold timing may vary; this is a hypothesis, not measured variation
from one sample.

- The challenge appeared as a 400 × 265 CSS-pixel modal over the empty page 19,
  containing an iframe and a separate close button. Before, after, and restored
  catalogue screenshots were captured in this chat. No image files were exported
  by this one-off browser experiment; future extension evidence downloads still
  require activation of 2.11 and Chrome's capture grant.
- One browser-tool pointer hold requested **8,000 ms** at the observed challenge
  button. The tool call started at 13:33:49.680 and finished at 13:34:01.141 Central;
  that 11.461-second call duration includes tool overhead and is not the measured
  physical hold duration or the site's required threshold.
- The extension independently recorded `verification-resumed` at **13:34:04.102**.
  At 13:34:15.137, DOM and screenshot inspection found no challenge. No second
  attempt or challenge-solving service was used.
- The existing **Start a new search** action returned the empty tail to page 1.
  The catalogue then exposed 104 product links; live telemetry acknowledged
  **65 extracted items, two newly observed, zero failed or pending**. Product
  links are DOM anchors and do not equal the extracted unique-item count.
- Old 2.10 subsequently scheduled a 68.468-second page wait, demonstrating that
  its post-challenge speed inflation is still active until the pending reload.
- A bounded recent console sample was classified without exporting raw messages.
  No causal conclusion is drawn from unrelated page errors. Resource Timing was
  unavailable through the browser tool's read-only evaluator; no network trace,
  request/response bodies, cookies or credentials were captured.

Durable safe record: `data/verification-experiment-20261022-1333.json` (ignored
runtime data). It retains the local event history, settings, timestamps,
geometry, attempt count and independent resume/outcome evidence. This establishes
one successful browser-controlled attempt, not an always-working solver, a fixed
eight-second threshold, or prevention of future challenges.

Any future recovery design should use observable progress/success/failure and
bounded cancellation rather than fixed screen coordinates or an assumed hold
duration. The current browser action exposes a fixed-duration hold; it does not
provide a documented separate pointer-down/pointer-up stream for a feedback-based
hold. Such a controller has not been implemented or validated here. No deliberate
new challenge was triggered to manufacture more samples.

## Recurring challenge and scripted operator helper — 13:39–13:46 Central

The owner reported another challenge on populated page 3 and approved one
attempt with the instruction to automate and test it. Safe pre-attempt history
was preserved in `data/verification-recurrence-1792694345379.json`.

The scripted browser flow located the current iframe-parent control rectangle
instead of reusing the first trial's fixed coordinates, requested one 8-second
hold at its current target, and checked the result. Its first rapid observation
window ended at 13:45:55.579 with the prompt still visible. Independent extension
telemetry recorded `verification-resumed` at **13:45:56.573**, approximately
**3.644 seconds after the input call finished**. At 13:46:07.067, DOM and screenshot
checks confirmed no challenge and 123 product links. The notifier acknowledged
78 extracted items on page 3 with no failed or pending work. No second hold or
deliberate new challenge was used.

A reusable, operator-only workflow is saved in `tools/verification-assist.mjs`.
It needs an already connected CUA tab and current-check confirmation. It is
not part of the extension, an unattended daemon, or a standalone CLI. The saved
helper adds a 20-second elapsed-time settling deadline, bounded 120-entry
observation history, viewport/hit-test targeting checks, blocked-redirect
handling and no blind input retries. It still uses the observed 8-second trial
duration, not a feedback-controlled release. The initial live flow and the
final hardened helper are distinct versions; no new live challenge was triggered
to exercise the hardened version.

Three regressions reproduced early timeout with fast observations, off-screen
or covered targeting, and false success on `/blocked` before correction. All
**9 helper tests** now pass; the combined JavaScript suite is **171 passed**.
Ruff, helper syntax and whitespace checks pass. The independent reviewer
initially required the settling and hit-test fixes, then approved the corrected
helper with no required findings. Actual DOM hit-test support and viewport
dimensions were checked read-only in the connected browser. Two successful
approved holds are useful evidence, not proof of variable thresholds, permanent
prevention, or unattended reliability. Extension activation still requires the
manual 2.11 reload; the last live telemetry inspected remained 2.10.0.

## Native pagination and current activation — 13:56–14:08 Central

The owner approved one attempt on the then-current full-page `/blocked` check
with “Yes, clear it once.” One 8-second browser hold at the observed control
started at **13:56:04.763**, returned at **13:56:13.062**, and subsequently
restored page 5 with 138 product anchors. Safe record:
`data/verification-experiment-20261022-1356.json`. This consumed that approval;
no attempt was made on the later recurrence.

Live inspection found role-button anchors with `href="#"`, numbered
`Go to Page N` labels, an `aria-current="page"` marker, and a **Next Page**
control (`data-testid="NextPage"`). One normal browser click moved page 2 to 3.
After 559 ms the URL was already 3 while the pager and old cards still said 2;
a later observation showed pager 3 and changed cards. That later observation
includes tool/model delay, so it is not an exact render-time measurement.
Safe record: `data/pagination-investigation-20261022.json`.

Source **2.12.0** now prefers that adjacent Next control, preserves strict
filter/marker/disabled checks, and shortens only a click that never starts a
normalized URL transition to a five-second start window. Changed URLs still
get 30 seconds to hydrate fresh cards. Fragment-only links reproduced a timeout
bug in a regression before normalization fixed it. A delegated Next browser
fixture passed without acknowledging stale cards.

Two more regressions proved that negative jitter shortened the configured
minimum and an old cooldown survived installation. Jitter now respects the
saved minimum, and installation/reload clears only the native cooldown. Ordinary
startup retains it; unresolved verification is unchanged. All **179 combined
JavaScript tests**, extension syntax, Ruff and whitespace checks pass. The
independent reviewer approved the final changes with no required findings.

Latest mirror inspection now reports **2.11.0 / 5 minutes / 15 seconds / none**,
with another challenge at page 13 (fourth recorded check, lastAt
1792695674936). Page waits before that prompt were around the configured
15-second setting instead of the old history-inflated waits. No completed new
live scan was observed. No evidence download directory exists yet; automatic
screenshots therefore remain unproven and may need the extension-icon
`activeTab` grant. **2.12 activation, a completed live scan, and an actual
evidence download remain unverified.** The owner has been asked to perform the
manual reload/refresh that the browser tool cannot access.

Current closeout: the updated fixture tab is closed and its task-owned port-8879
server is stopped. The owner's reviewer tab is left open. No commit or push.

## Activation confirmed — 16:42 Central

The owner reported reloading 2.12 and refreshing the reviewer tab. The fresh
mirror exported at **16:42:35.908 Central** confirms **2.12.0**, refresh **5**,
page minimum **15**, mode **none**. The first read was a 2.11 snapshot from just
before the reload; the following heartbeat supplied the new version.

The live reviewer tab still displays the page-13 Press & Hold with no product
anchors. It is the existing fourth challenge, not evidence of a new challenge
triggered by 2.12. No attempt was made under a previous approval. A fresh
one-attempt confirmation was requested and remains pending. A completed scan,
live native pagination, and screenshot downloads cannot yet be measured.
The safe activation snapshot is `data/scan-activation-20261022.json`.

## Reliable recovery investigation — October 22 follow-up

Owner requested a consistent bypass. This outcome remains **unproven**. Current
official [HUMAN challenge documentation](https://docs.humansecurity.com/applications/create-a-human-challenge)
confirms variable hold duration; the [testing-token documentation](https://docs.humansecurity.com/applications/create-a-challenge-bypass-token)
requires access to the site's own HUMAN settings. No Walmart operator token is
available here. The [FlareSolverr README](https://github.com/FlareSolverr/FlareSolverr#captcha-solvers)
still reports nonworking CAPTCHA solvers. No solver service was installed.

Read-only live inspection confirms a visible modal hold control, but no exposed
progressbar/ARIA progress values. Its challenge iframe has no readable document.
The available CUA input API provides fixed-duration holds, not a separate
pointer-down/release stream. Therefore feedback-based release was not implemented
or represented as proven. No new CAPTCHA input was sent without current approval.

Seven actual-callback regressions reproduced reliability failures before fixes:
unsupported full-page controls, hidden or unrelated dialogs, full-page replacement
with cached cards, early return during delayed content, transient clear before
a replacement prompt, and hidden cached product anchors confirming false success.
The operator helper now uses shared visible DOM detection for targeting and
settling, supports the inspected modal/full-page containers, requires the HTTPS
reviewer route and visible products/empty state without a prompt for one second,
and returns an explicit `verified` boolean. Unknown layouts or replacement
challenges remain pending with no additional input. `challengeChanged` is only a
layout change indicator. Browser errors remain indeterminate, not permission to retry.

The updated detector was checked read-only on the real page-13 prompt: modal,
zero product anchors, `reviewerReady: false`, supported hit-tested target. The
latest hold/outcome flow still needs a freshly approved live trial. The prior
one-attempt question remains pending. No extension files changed in this follow-up;
no additional extension reload is needed for the separate operator helper.

Final verification: **16 helper tests / 186 combined JavaScript tests passed**;
helper syntax, Ruff and whitespace checks passed. Independent reviewer approved
the corrected hidden-anchor handling with no remaining required findings in the
reviewed scope. No new browser input, external installation, commit or push.

## Updated helper live trial and complete scan — 17:11–17:20 Central

Owner explicitly authorized: “test the updated helper once on the current
challenge.” The current functions from `tools/verification-assist.mjs` were
reproduced in the connected CUA session (not imported as a standalone CLI) and
run with `approved: true`. One native hold requested **8,000 ms** at the current
hit-tested target. No second CAPTCHA input was sent.

The flow began **17:11:58.786**, the input call completed **17:12:07.065**, the
first clear was observed **17:12:09.999**, and stable reviewer empty-results
content was confirmed **17:12:11.090**. Result: `verified: true`,
`cleared-empty-page`, **4,025 ms** settling and **26** bounded observations.
The helper continued waiting while the prompt was visible over the empty panel,
then required over one second of clear usable content before confirming.
Input-call duration includes tool overhead; the site's required threshold
was not measured.

Challenge clearing succeeded, but automatic extension resumption was initially
unconfirmed. The backend's browser heartbeat reported `reachable: false`,
`waiting-verification`. One ordinary reload of the cleared reviewer tab restored
its content-script connection; the page then returned to page 1 with products.
The new document recorded `page-loaded` in 2.12 telemetry. Independent
`verification-resumed` was recorded at **17:13:37.517**; the pause became zero.

The complete pass covered **every page 1–17**, with **0 failed/pending deliveries**
and **0 new challenges**. The extension recorded completion at **17:19:37.623**
with duration **359,399 ms (5 minutes 59.399 seconds)**. Sixteen native
transitions had median **2,346.5 ms**, range **1,037–4,429 ms**. One ordinary
URL fallback was expected for the page-12 to page-14 jump over already visited
page 13; no native-stall fallback occurred. The saved minimum remained 15 seconds.

The stale boundary led to empty page 18, exercising the real tail fix. Empty
rechecks occurred at **17:19:31.832** and **17:19:33.835**. One reload completed
at **17:19:37.280**, followed by `sweep-complete` for 17 populated pages at
**17:19:37.623**: **5,791 ms** from first empty observation to completion.
This replaces the prior repeated final-page loop in this observed case.
Safe scan report and its allowlisted events: `data/scan-trial-20261022-1713.json`.
Five minutes remains a scheduling target; this observed pass itself took six
minutes and the normal one-minute post-scan rest still applies.

Safe durable record: `data/verification-helper-trial-20261022-1712.json`.
Before/after screenshots were captured inline in this chat, with no image files
exported. This is one successful live sample of the hardened flow, not proof
of permanent prevention or unattended reliability. The current-check approval
has been consumed.

### Recurrence on the following scan — 17:20 Central

The next scheduled pass started **17:20:37.595**, about **6 minutes 59 seconds**
after the completed pass's recorded start. A new verification redirect was
detected at **17:20:38.391** (challenge event recorded at **17:20:38.497**),
bringing the diagnostic count to 5. The successful preceding pass had no new
challenge; this recurrence happened at the following scheduled full navigation.
This timing establishes correlation with that navigation, not Walmart's private
reason for challenging it.

No additional CAPTCHA input was sent. The one approved attempt is consumed,
and the current new check requires a separate confirmation before any attempt.
The fifth challenge leaves the extension paused; permanent or consistent
unattended bypass is **not established**. The empty-tail and native-pagination
performance fixes are live-verified, while recurring verification remains open.
Safe follow-up: `data/verification-recurrence-20261022-1720.json`.
The bounded scan watcher exited on successful completion; no test server or
watcher remains running. The owner's Walmart tab is left open.

## Detection visibility audit — October 22 evening

Owner reported that a challenge was not being automatically detected/solved.
Current 2.12 telemetry already records the fifth verification outage beginning
**17:20:38.391 Central**, with no subsequent recovery. The live tab is on the
full-page check. Duplicate prompts during one unresolved outage are intentionally
not counted again, so this does not establish a separate newly numbered outage.
Backend status agrees: `waiting-verification`, no reachable reviewer page.

Automatic solving is **not implemented** in the extension. The tested helper is
a separate operator flow; the connected browser's policy requires a fresh
confirmation for every CAPTCHA attempt. Prior one-attempt approval is consumed.
The owner has been asked whether to attempt the currently visible check once.
No CAPTCHA input or new live notification was sent during this audit.

Two detection-feedback gaps were reproduced and fixed in source **2.13.0**:
missing persistent toolbar attention and missed evidence when the redirect's
first detection occurred before document completion. A shared toolbar queue now
protects the red `!` and needs-attention tooltip from late item-delivery badges
and the normal clear timer, restores it after worker recreation, and clears it
on resume/reset. Duplicate detection retries best-effort evidence capture while
preserving one accepted JPEG and one alert per outage.

The notifier alert previously ignored HTTP failure. It now records safe local
sent/failed status and exposes an unconfirmed-alert warning in Options, without
blindly retrying an ambiguous external send. No new external service or browser
permission was added. Existing capture still needs Chrome's active-tab grant.

Seven regressions failed before fixes and now pass. Full combined JavaScript
suite: **193 passed**; extension syntax, Ruff and whitespace checks passed.
Independent reviewer approved the queue, evidence retry and alert-status fixes.
The Options warning was verified in the isolated localhost browser fixture.
Source 2.13 still needs the owner's manual extension reload: the browser tool
cannot access Chrome's Extensions page. Actual toolbar/capture activation is
not claimed. This slice does not fulfill unattended challenge-solving.

Closeout: the isolated Options fixture showed the new warning with no console
warnings/errors, then its tab and task-owned server were closed. No commit/push.
Runtime remains 2.12; source 2.13 activation and the fresh one-attempt answer
remain pending. The owner's challenged tab stays open.

## Approved full-page recovery — 18:20 Central

Owner answered “Yes, try once” to the current-check request. The unchanged
operator helper ran once on the full-page check using its live hit-tested
target. It requested one **8,000 ms** hold, starting **18:20:41.457**; the
input call completed **18:20:49.735**. The first clear was observed at
**18:20:55.324** with 18 visible product anchors, followed by 122 anchors at
**18:20:56.902**. The helper returned `verified: true`,
`cleared-with-catalogue`, **7,167 ms** settling and **32** observations.
This settling includes the navigation/rendering delay, not a measured change
in the required hold threshold.

The reviewer tab advanced to page 2 without another browser action or manual
refresh. Fresh telemetry confirms `verification-resumed` at **18:20:58.304**,
pause **0**, successful page-1/page-2 deliveries with **0 failed/pending**, and
a **2,255 ms** native transition to page 2. The next native transition toward
page 3 began normally. No second attempt was
sent; the current-check approval is consumed. Runtime was still **2.12.0**;
source **2.13.0** remains unactivated and still needs the owner's manual reload.
Safe evidence: `data/verification-helper-trial-20261022-1820.json`.

## Scan efficiency implementation — source 2.14

Owner authorized implementation after the efficiency assessment. Changes are
implemented and verified locally. Chrome activation and a full live timing trial
were subsequently verified in the October 23 audit below. At this implementation
handoff runtime was still 2.12. No further challenge attempt was made.

- Scheduled scans offer a native page-one restart after revoking the old lease
  and clearing progress. The content script requires a matching generation/tab,
  search, visible control and idle extraction/navigation state. It locks stale
  last-page cards until page one renders. Older listeners and missing/stalled
  controls retain full-load recovery. Manual restart retains its repair load.
- Page spacing now applies between navigation starts, retaining the existing
  configured minimum and jitter. Browser navigation start anchors full loads;
  native navigation updates that anchor. Rendering, settling and delivery reduce
  the remaining wait. The five-minute target and one-minute completion rest are
  unchanged. Logging records interval, elapsed and remaining milliseconds.
- A document-local, route/mode-bound empty-tail proof lasts ten minutes. Every
  pass still probes that page. Two empty observations may avoid another reload;
  cards, advertised growth, changed scope, verification, new document or expiry
  require fresh proof. Fast reuse never extends expiry, and unread earlier pages
  still need successful delivery.
- Verification demotion preserves the immediate-resume state. The permanent
  observer also watches text-node changes and iframe source/title changes.
  Optional screenshot/alert work no longer holds the pause/resume queue. Alert
  results are tied to their outage so late responses cannot overwrite a newer
  alert's status. This does not implement unattended challenge-solving.

Review reproduced and resolved fallback navigation racing a challenge during
logging, late alert responses crossing outages, and pending manual navigation
being overwritten. Final load authorization and I/O share the coordinator lock,
including generation, pause, committed URL and pending URL checks. The reviewer
approved the final fixes; no required findings remain.

Verification:

- Combined JavaScript suite: **215 passed**, no failures/skips. Meaningful new
  regressions failed before implementation. A controlled 17-page fake-clock test
  exercises the real content pipeline and confirms pages 1–17 are delivered with
  fifteen seconds between starts and no additive loading wait. Its 243.5-second
  duration is a fixture result, **not a measured Walmart performance claim**.
- Full Python suite: **1,036 passed** in 104.19 seconds with normal temp-directory
  access. The sandbox run could not create pytest temporary directories and was
  stopped; no failing tests/assertions were removed. Diagnostic persistence tests
  accept only the added fixed events/numeric fields and retain strict rejection.
- The isolated in-app browser fixture passed native restart, stale-card guards,
  pacing, immediate verification resume, live relay and empty-tail completion,
  with no warning/error console entries. Its tab and task-owned port-8879 server
  were closed after verification.
- Extension syntax, manifest parsing, Ruff, whitespace and server startup checks
  passed. No commit/push or settings change.

The local notifier was rebuilt/restarted with its existing data volume/settings.
It is healthy, configured, and its running image confirms the new diagnostic
allowlists. Chrome's internal Extensions page remains inaccessible to the browser
tool (no workaround attempted). Next step: owner reloads Reviewer Item Relay
**2.14.0** and refreshes reviewer tabs, then measure full cycles/challenge rate.

## October 23 read-only log audit — after owner's refresh

2.14 is active. The newly refreshed unfiltered primary (tab 1530560112,
generation 1) delivered every populated page 1–18, completing at
**08:31:28 Central** in **318,884 ms (5m 19s)**, with zero failed/pending local
deliveries. Empty page 19 took **6,648 ms** from first empty observation through
completion, with normal two short rechecks and one confirmation reload. Chosen
navigation intervals were about 15–18 seconds. No newer verification is recorded;
the last challenge remains October 22 at 17:20 Central, with no current pause.
Safe retained trial: `data/scan-trial-20261023-0826.json`.

The earlier retained history was a different primary, tab 1530560370, filtered
to `queryNups=Cat`, confirmed by the visible reviewer search. Its three-page
completions were not evidence of full catalogue coverage. Since the refresh,
the unfiltered tab is scanning normally. Primary selection currently permits
this filtered/manual browsing scope and deserves a separate protection.

Remaining issues observed (no code/settings changed during this audit):

- Native pagination still fails on the real site. The refreshed scan's page-2
  attempt stalled after 5,979 ms, and all 18 transitions including the empty tail
  used full-load fallback. Earlier retained scheduled native restarts also all
  fell back (17 stalls, roughly 5–57 seconds each). The handshake reads a
  document-local cooldown initialized to zero; a final full-load page can finish
  without calling pageDelayDetails, leaving the persisted cooldown unread before
  the next restart. This explains repeated native attempts despite backoff; the
  underlying site click failure is not yet explained by these logs.
- Current Discord health separated intentional future-tier delay from due work:
  one sample had 600 due Pro jobs and oldest due age around 155 seconds. Recent
  delivered Pro latency p50 was 14.2s and p95 64.9s. Large future Free/Starter
  totals are deliberately delayed, not evidence that all pending jobs are late.
  These are queue samples during catalogue catch-up, not a steady-state SLA.
  The 90-minute container sample had no ERROR lines and 44 handled
  `audit_throttled` warnings (provider throttling or locally enforced learned gates),
  which can add delivery delay; the count does not prove 44 outbound HTTP 429 responses.
  A later sample at about 08:33 had 269 due jobs, oldest due age 251s, and recent
  Pro latency p50 81.3s / p95 241.4s (733 samples). The queue was draining but
  those notifications were still delayed despite successful scraper ingestion.

No browser input, CAPTCHA attempt, restart, source edit or settings change was
performed during this audit. Read-only inspection left the user's tabs open.

## October 23 fixes — source 2.15.1

Owner authorized fixing all audit findings, specifically allowing Free history
to wait so Pro and expensive items receive priority. This supersedes the earlier
reserved-Free-slot policy when paid work is due. Free's 24-hour release and
Starter's 300-second delay remain unchanged.

- Regular scans now select unfiltered catalogue tabs. Nonempty manual queries,
  facets and restrictive affinity modifiers cannot masquerade as full coverage; if only
  manual searches exist, one separate background catalogue tab is opened.
  Explicit search modes retain their intended behavior. Sender authority,
  queued/native/live actions and repair reloads all validate scope. Manual SPA
  navigation off the owned origin/reviewer route releases the lease too.
  Live verification caught Walmart appending neutral `affinityOverride=default`
  during ordinary pagination; patch 2.15.1 allows exactly that value while still
  rejecting `store_led` and mixed duplicates. This also has red/green coverage.
- Native failures persist per normalized search route and control capability,
  bounded to 20 entries. They bypass that capability for ten minutes, then allow
  a one-second start trial. Other capabilities remain usable. A final document
  reads this policy before accepting a native restart, avoiding the repeated
  stale-document-zero problem. Native success is logged only on confirmed
  destination URL plus changed content. Real Walmart programmatic native clicks
  have not been proven reliable; correctly paced URL fallback remains required.
- Discord paid envelopes now carry up to ten already-due items, Free at most
  five. Numbered buttons fit five rows, and aggregate embed text uses Discord's
  UTF-16 limits. Full privacy audits still precede every bounded four-channel
  wave. Learned provider cooldowns are checked before doing avoidable audits.
  Expensive new Pro items lead eligible envelopes, with oldest-paid fairness.
  Free requests are deferred while paid work is due to avoid blocking a later
  same-channel paid batch. Exact persisted envelope bodies/nonces survive retry.
- Monitoring retains all ten member IDs/values with a fixed upper bound. No
  credentials or raw page contents were added to ordinary logs.

Regression evidence: both manual-navigation races were reproduced red, then
fixed green. The independent extension reviewer approved both repairs. The
combined JavaScript suite has **226 passing tests**. The final full Python suite
has **1,072 passing tests** (109.75s), followed by **16 passing monitoring tests**
after adding the explicit health reason for payload-review holds. The real
in-app fixture passes scope guards, native restart/paging, stale cards, pacing,
empty-tail completion and immediate verification resume, with zero console
warnings/errors. Extension syntax and manifest checks pass.

Final controlled offline receipt: `data/discord_bridge/paid-throughput-wire-20261023.json`.
With 500 initial items plus 50 arriving during paid backlog, paid envelope 5→10
reduced message posts 430→278, audit GETs 990→565, initial Pro drain 117.32→58.27s,
and new high-value Pro p95 8.66→2.76s. These are virtual provider timings, not a
production SLA; all-value fresh Pro p95 remained 26.57s in that model.

Fresh diagnostics confirm **2.15.1 active**. Existing 5-minute / 15-second / none
settings are preserved. Walmart displayed a new modal challenge during page 1→2
at 09:52 Central. Detection paused the session immediately (count 6); the owner
approved one current helper attempt. Its 8-second hold plus 20-second outcome
observation did **not** clear the challenge. Receipt:
`data/verification-attempt-20261023-0957.json`. No second attempt was made; no
unattended solver is installed. Full live 2.15.1 cycle verification remains
dependent on clearing this challenge.

### Live delivery findings and repaired causes

The initial larger-envelope deployment exposed two real failures that the fake
provider model did not cover. Some ten-card bodies (roughly 15–16 KiB JSON) were
rejected with HTTP 413 while a six-card body succeeded. The implementation now
uses one compact wire encoder and a conservative **10 KiB application ceiling**
when creating new envelopes. It preserves priority and oldest catch-up cards
while shrinking a new body. A fresh definitive 413 can archive an unambiguous
parent and requeue eligible unexpired members under a persisted smaller route
cap (at most five). Unknown prior attempts, network ambiguity and expired lease
recovery prevent splitting; those bodies/nonces remain frozen for review/expiry.
This avoids silently duplicating messages after a possibly successful send.
Only numeric provider codes and request/response sizes enter logs.

SQLite profiling on a copy of the live queue (13,388 items / 48,868 jobs) found
the correlated sibling-retry lookup selecting a broad ready index and scanning
thousands of future Free rows per candidate under `BEGIN IMMEDIATE`. The retry
covering index and explicit indexed lookup reduce measured copy claim time from
0.8877s to 0.0122s; the deterministic VM-instruction regression fails before the
fix and passes with immutable legacy envelopes and sibling deadlines preserved.
Actual Docker/Windows bind-mount writer contention produced `database is locked`,
so the online index attempt failed. A brief stop, consistent backup, offline index
creation and restart succeeded. Backups:
`data/discord_bridge/collector-before-retry-index-20261023.sqlite3` and
`data/discord_bridge/collector-before-paid-recovery-20261023.sqlite3`.

The reviewed payload/index build is deployed and healthy. Its first sampled
logs have zero OperationalError/ERROR entries; legacy oversized retries are
being held or reviewed and are not evidence of a clean paid backlog yet.
Read-only 413 review approved the final code and independently passed 79 focused
tests. The initial deployment had already failed ten destinations at its retry
limit; existing old envelopes require separate evidence-based reconciliation.
No expired/sent item may be revived, and uncertain sends may not be repackaged.
The task-owned browser fixture and port-8879 server are closed. No commit/push.

### Final live recovery and remaining verification dependency

Final image `49de8c1e3cc857046fd2cb22507ac8f4a32cc886ea0bd224417f2b042a452341`
is running and healthy. At **10:23 Central**, authenticated health reports **zero
due Discord destinations and zero failed destinations**. This is queue health,
not proof of fresh discovery latency while Walmart is paused.

The owner explicitly authorized retrying forty older uncertain destinations,
accepting possible duplicate delivery. The exact four archived parents were
reviewed against current member/body/clock/attempt hashes; proof and that approval
are stored transactionally in `dispatch_recovery_reviews`. Their existing frozen
retries received fresh 413 responses, safely reduced, and **all forty destinations
are confirmed sent**. Receipt: `data/discord_bridge/owner-approved-40-retry-20261023.json`.

The independent audit proved nine additional ten-item envelopes had complete,
contiguous definitive-413 histories, with all 73 provider outcomes hash-matched
to retained logs. A separate nine-item envelope passed the same safety checks.
Proof artifacts are `data/discord_bridge/rejection-review-20261023.json` and
`rejection-review-nine-item-20261023.json`; the GET-only history supplement found
no positive nonce matches and was not used as absence-based authorization.

Automatic approval review initially rejected the 90-job recovery because it
required explicit owner approval for those external Discord retries; the rejected
action changed no queue rows. The owner then approved the other 99 retries too,
with original expiry preserved. By execution, **ninety had expired**. They were
left retired. The remaining nine were atomically detached from their proven
rejected parent, preserving its payload/member/nonce archive and all job release/
expiry clocks; **all nine are confirmed sent**. Transactional evidence and receipt:
`data/discord_bridge/owner-approved-99-retry-20261023.json`. No expired/sent entry
was revived. Total approved recovery: **49 sent / 90 expired and untouched**.

All implementation/review/deployment work in the accepted scan and paid-priority
scope is complete. The remaining live full-scan measurement requires the owner
to clear Walmart's outstanding challenge; the one approved helper attempt failed.
Automatic detection/logging/resumption are installed, but unattended solving is
not. The browser tool requires action-time approval for each attempted CAPTCHA.
No further reload is needed: version 2.15.1 is already confirmed active.

Final workspace check found concurrent rolling-log work added after the earlier
passing repository lint run: four Ruff findings in `discord_bridge/rolling_logs.py`,
`tests/test_rolling_logs.py` and the new `src/server.py` import ordering. These
unrelated edits were preserved. All eleven delivery/queue/API/monitoring/test files
owned by this change still pass scoped Ruff. The verified running image above
predates those concurrent source changes; do not rebuild that unrelated work
without reconciling its owner and checks.

### October 23 follow-up: approved verification cleared; collector resumed

This supersedes the outstanding live-challenge status above. The owner explicitly
approved one attempt on the currently visible Walmart Press & Hold dialog through
the browser confirmation prompt in this chat. Chrome was accessible through CUA;
the earlier report of denied site access did not apply to this connection.

One 15-second hold was sent to the visible control. The control initially showed
a pending indicator; a later read confirmed the challenge was absent and 125
visible product links had loaded. This is one successful timed attempt, not proof
of the required duration, adaptive release, or future unattended reliability.
The currently exposed CUA input API provides timed clicks rather than separate
mouse-down/mouse-up controls; the inspected challenge container exposed no readable
progress control in its page DOM. No solver, debugger permission, desktop-input
service, or automatic retry loop was added to the program.

A second, redundant reviewer catalogue tab on page 20 still showed its old
challenge, keeping the existing all-challenged-tabs recovery guard paused. That
duplicate was closed through CUA; it received no solve attempt. The recovered
catalogue tab was left open. By **10:46 Central**, authenticated local health
confirmed `phase=sweeping`, one reachable tab, and `paused_until=0`. The latest
successful relay at 10:46:00 Central observed 91 items, including two new items,
with zero failed/pending relay items and two queued destinations. Queued work is
not confirmation of downstream message delivery. The disk diagnostic mirror was
still older than this fresh health response and must not override it.

Recovered-page screenshot: ignored `data/verification-cleared-20261023.jpg`.
Source/runtime behavior was unchanged; no extension reload, deployment, test
messages, or additional CAPTCHA input was performed. The owner's original request
for an integrated unattended verification solution remains unresolved. The live
browser helper continues to require confirmation for each attempted challenge.

### October 23: temporary alphabet fallback for broken catalogue pagination

Owner requested immediate character-search recovery when regular pagination
returns no items, followed by another pagination attempt after each a–z round.
Implemented in extension **2.16.1**: an explicit no-results panel switches to
`a` immediately; a blank card area uses the existing 30-second load timeout.
Navigation continues to respect saved spacing, user activity and verification.
Saved `sweepMode` stays `none`; the worker grants only the current primary and
generation access to the temporary single-letter searches. The fallback survives
document/worker reloads, Chrome URL/loading events and failed-letter recovery.
After `z`, the next scheduled restart removes `q`, `queryNups` and `page`, and
tries full-catalogue pagination again. Explicit Sweep/High Value modes retain
their existing empty-search confirmation and recovery.

Verification: regressions failed before implementation and pass after it;
all **266 Node extension tests** pass, the full Python suite passes, repository
Ruff and diff whitespace checks pass, and JavaScript syntax/manifests pass.
Final focused checks also pass after removing a redundant storage read.
The local Chrome fixture passes with no console warnings/errors, using simulated
extension messaging/storage and no Walmart/notifier requests. Self-review covered
lease authorization, manual filters, verification and completion/restart behavior.

Activation is pending a reload of Reviewer Item Relay in Chrome. No live extension
reload, backend rebuild, deployment, commit/push or CAPTCHA interaction was made
for this change. Existing unrelated workspace edits were preserved.
