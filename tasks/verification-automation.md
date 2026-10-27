# Automatic reviewer verification — October 23, 2026

## Current follow-up: confirmed pre-press move timeout

Live diagnostics confirm Relay **2.17.2**, challenge count 9, the latest challenge
at **1:20:31.130 PM Central** and resumption at **1:21:35.095 PM**. The owner reports
manual recovery. The backend snapshot records challenge/resume but does not
include `verificationAssistLastResult`; it cannot identify the input failure on
its own. Safe episode evidence was preserved in ignored
`data/verification-last-episode-20261023-1324.json`.

The owner supplied the current Options result: enabled/watching, last attempt
`input-error`, **No press was sent. Failed while moving the pointer. Chrome
command timed out. Attempt 1 of 10.** The current adapter awaits
`Input.dispatchMouseEvent(mouseMoved)` before issuing mousePressed. Its five-second
acknowledgement deadline expired; the stored `pressSent=false` confirms the code
never reached mousePressed. This establishes the failing boundary, not the
underlying reason Chrome failed to acknowledge the move. Variable hold duration
and server acceptance of an actual press were not tested by this failed attempt.

**Implemented and locally verified:** the engine now records `cleanupConfirmed`
only after driver cleanup returns without errors. The integrated controller
retries exactly `input-error` + stage `move` + reason `timeout` +
`pressSent===false` + `cleanupConfirmed===true` + no cleanup error. Existing
10–20-second delays, ten starts/hour and ten attempts/check remain; every
reservation stays spent. Unknown old records, uncertain presses, failed cleanup
and other input failures remain terminal. The owner-facing hint describes this
exception. No timeout was lengthened and no native mouse code was added.

Five focused cases demonstrate the change (four failed before implementation),
including automatic recovery on the next attempt, restart/cap preservation,
ambiguous-case rejection and failed detach. All **328 combined extension tests**,
JavaScript syntax and scoped whitespace checks pass. Independent review approved
and reran **50/50** engine/controller cases. Receipts:
`data/prepress-retry-red-20261023.txt`, `data/prepress-retry-focused-20261023.txt`
and `data/prepress-retry-suite-20261023.txt`. No live CAPTCHA input or new transport
success is claimed. The pending combined source is **2.18.0**, versioned by the
concurrent resume-work chat; its changes were preserved. Chrome still needs the
combined reload. Old uncertain result records are not silently rearmed.

**Native fallback remains proposed:** the running notifier is a Linux Docker
container (`reviewer-notifier:local`); the existing Windows recovery process only
opens/restarts missing browser/services and never clicks verification. A real
Windows mouse path needs an interactive desktop helper, a verified Chrome window
and target/DPI mapping, physical-input cancellation and an independent release
watchdog. It could avoid the failing Chrome input-command path, but it is not a
guarantee of HUMAN acceptance and cannot be supplied by changing hold timing.
No native host/permission/registry change was installed or activated here.

Primary references: [Chrome debugger transport and detach behavior](https://developer.chrome.com/docs/extensions/reference/api/debugger),
[Chrome native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging),
[Windows SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
and [HUMAN interaction/challenge indicators](https://docs.humansecurity.com/applications/bot-characteristics).

## Previous input-error follow-up: Relay 2.17.1

The owner supplied the live Options status: enabled, `input-error`, hold 2.01
seconds, attempt 2 of 10. The notifier mirror confirmed active Relay 2.17.0 and
an outstanding verification pause. This explains the stopped retries: uncertain
input/cleanup is terminal even with attempts remaining. The old two-second
command deadline is a likely cause, but the old record lacks a failure stage,
so the exact command that failed is not established.

Two reproducible source defects are fixed in 2.17.1: input acknowledgements used
the same short deadline as DOM reads, and preparation time could be reported as
a hold even when no press was sent. Input commands now have a bounded five-second
acknowledgement window; DOM reads retain two seconds. Press acknowledgement
latency counts toward the hold limit, and the independent release watchdog stays.
Results record whether a press was sent/confirmed and release confirmed, plus
fixed failure-stage/reason labels without raw browser errors. No-press preparation
failures report zero hold. Old uncertain durations are labelled input duration.

Options adds **Retry current check** for a matching terminal input-error result.
Only an explicit message from this extension's own Options page can rearm it.
It preserves all existing check/hourly reservations and serializes with ordinary
automation; disabled, changed, busy or exhausted checks are refused. The current
live uncertain result was not reset and no live input was sent. The release
includes the concurrent content-script invalidation fix, with its receipt in
`context-invalidation-20261023.md`.

Verification: transport/controller and Options regressions failed before their
fixes, then passed. The final combined extension suite passes **302/302**, with
zero failures/skips; all extension JavaScript syntax, 2.17.1 manifest and scoped
whitespace checks pass. Independent review approved the final sender guard,
bounded cleanup, timing and retained budgets. Receipts are in ignored
`data/input-transport-suite-20261023.txt`, `data/input-transport-red-20261023.txt`
and `data/input-review-{red,green}-20261023.txt`.

The actual Options UI passed a local mock preview: no-press/error-stage display,
explicit retry transitioning to retry-pending, and no console warnings/errors.
Screenshot: `data/input-error-preview-20261023.png`. This does not validate actual
Chrome input or Walmart acceptance. Browser security blocked the internal
extension page, so the owner's pasted status was used; no access workaround.

Activation: reload the existing Relay to **2.17.1**, refresh the reviewer page
once to reconnect its content script, and reopen Options. Release any held mouse
button, then click **Retry current check** once. Automatic verification remains
enabled if previously enabled; counts remain intact. No backend rebuild or second
extension is needed. Neither activation nor live recovery is claimed here.

## Previous retry update: Relay 2.17.0, owner accepted October 23

Owner reported the active helper had held for 15.01 seconds without confirming
recovery and requested adaptive timing and retries. Owner explicitly revised the
limit to **up to 10 attempts, waiting about 10–20 seconds**. This supersedes the
historical one-attempt/three-per-hour acceptance below. Current source is 2.17.0,
including the separate pagination/scrolling work documented in
`browsing-behavior-20261023.md`. That chat agreed this chat owns the combined
manifest and final stable regression check; its navigation edits are preserved.

- Retry only completed `needs-attention` results after confirmed release/cleanup.
  Persist the reservation and terminal-in-progress marker before input; interrupted
  or uncertain input/cleanup never replays automatically.
- Per tab/outage: at most ten attempts, random 10–20-second wait after an
  unsuccessful completed attempt; retain a global ten-starts/hour budget.
  Short timer plus an alarm, startup and heartbeat restore persisted deadlines.
  Chrome suspension may delay an attempt; no tight timing guarantee is claimed.
- Release on changed completion feedback. Advancing numeric semantic progress
  may extend a hold; four seconds without further progress releases it. Hard
  maximum/watchdog: 30 seconds. Unreadable-feedback fallback starts at 15 seconds
  and increases by three seconds per retry to a maximum of 30 seconds.
- A matching completed old failure can continue as attempt two without erasing
  its original budget. Do not infer one tab's result belongs to another tab.
- Options shows count, release reason and next retry; disabling and verification
  recovery cancel eligibility. Scanning resumes only through existing readiness
  checks. No permission or backend/provider change.

Implementation is complete and locally verified. No live CAPTCHA input or
extension reload from this chat. Browser policy
blocked direct access to the internal Options page; no workaround was used. The
owner supplied its status text. Local preview uses only simulated Chrome/notifier
state. Live-solving reliability remains unverified.

Primary scheduling references: [Chrome alarms](https://developer.chrome.com/docs/extensions/reference/api/alarms)
and [service-worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle).

Verification: the original retry/adaptive regressions failed before the fix.
Final combined extension suite passes **283/283**, zero failures/skips; an added
pause-clear eligibility regression then passes with the full focused integrated
suite (**15/15**). Final JavaScript syntax, 2.17.0 manifests and whitespace checks
pass. The first combined run exposed a fake-clock test mixing real wall time;
advancing the fixture's own clock fixed it without changing production timing.
Self-review also closed cross-tab legacy-result attribution and pause-clear
eligibility gaps. No unresolved task-related findings remain.

Local Chrome Options preview passed enable/disable and the attempt/release/retry
display with zero console warnings/errors. Screenshot:
`data/verification-retry-preview-20261023.png`. Chrome screenshot clipping timed
out twice; the original successful full preview is retained. The preview is not
live-input/Walmart-acceptance evidence. Final test receipt:
`data/verification-retry-tests-20261023.log`. Repository Ruff found one concurrent,
unrelated E501 in `discord_bridge/membership.py:168`; it was left to that work's
owner. No Python source changed in this slice.

Activation: owner must reload the existing Relay to **2.17.0**; already-enabled
verification stays enabled and eligible legacy failures can resume. Existing
settings and budgets remain. No backend rebuild is needed. A live agent-operated
CAPTCHA test still needs separate action-time confirmation. The user authorized
messaging the pagination chat; its slice is included and its owner was informed
of the combined version and file ownership. No commit/push or live solve claimed.

## Historical initial integration

Owner request: integrate future automatic CAPTCHA handling into this program.
The preceding approved 15-second CUA trial cleared one modal. That is evidence
of one successful attempt, not a universal hold duration or reliable solver.

Current implementation: automatic verification is built into the regular
**Reviewer Item Relay 2.16.0**, at the owner's explicit follow-up request to combine
the extensions. The original separate companion was a design choice, not a
Chrome requirement. Chrome requires `debugger` as a required permission; it is now
declared by Relay. Its enable control, status and last outcome live in Relay
Options. No hosted service, desktop mouse control or additional account is needed.
The permission is broad; source restricts attachment/input to the exact Walmart
reviewer and blocked routes. The owner must accept it when reloading Relay.

Acceptance:

- Detect a visible supported challenge in the signed-in profile automatically.
- Press once; poll scoped widget feedback and release on changed readable
  completion/release feedback. If it is unavailable, release at 15 seconds.
- Always attempt release and detach on errors, disabling, movement, navigation,
  and deadlines. Never detach a debugger session this helper did not acquire.
- Require an unchallenged reviewer catalogue or explicit empty result for one
  second before reporting recovery. Existing collector pause checks remain.
- Persist attempt reservation before input: no repeat for the same episode
  across service-worker restarts, at most three starts/hour and 60 seconds
  between starts, one tab at a time. Ambiguous outcomes require attention.
- Do not retain page bodies, widget text, screenshots, URLs or credentials.
- Preserve collector source, delivery queues, scan settings and existing startup.

Canonical solver source: `extension/verification-engine.js`,
`extension/verification-driver.js`, `extension/verification-automation.js`.
Integration: Relay manifest, background and Options; dedicated and existing Node
tests, local fixtures, focused CI entries and README. The old `verification_helper`
runtime files are frozen for the previously installed companion; its README
marks it superseded. Do not run both controllers together. No Discord/backend
development streams or service deployments are part of this slice.

Verification: deterministic variable-duration and failure-injection tests,
Chrome-adapter contract tests, extension syntax/manifest validation, existing
extension regressions, and a live companion test only after permission acceptance.
Status: combined source implemented and locally verified; live activation still
requires the owner's Relay reload, permission acceptance and enable action.

## Follow-up diagnosis and integrated behavior

The owner installed/enabled the old companion and pasted its status:
`Enabled. Last attempt: No attempts recorded.` Its setting was enabled, but no
input attempt had been reserved. The agent's read-only Walmart observation found
a complete `/blocked` document with a visible `#px-captcha` measuring about
272 × 102 CSS pixels, matching its detector. The exact missing detector/message
cause was not proven. The browser security policy blocked reading the companion's
`chrome-extension:` settings page; no alternate method was used to read its state.

Relay 2.16.0 starts from its own stored verification pause. Worker startup,
enabling, pause changes and the existing heartbeat all trigger a check, without
requiring another detector event. A failing regression exposed an enable event
being dropped while the initial disabled check was pending. A coalesced recheck
now preserves that event; the regression passes. The collector still owns pause
clearing and tab readiness. A recovered catalogue with unconfirmed collector
resumption has a separate status instead of a misleading input-error message.

The latest inspected notifier mirror still reported live **2.15.1** with an
outstanding pause. Merely editing source does not activate the combined version.
Built-in verification defaults off and does not inherit the old companion's
separate storage switch or budget. Existing Relay settings/data remain intact.

## Combined version verification

- **259 Node tests pass**, zero failures/skips, including eight integrated
  controller/manifest cases and four new real-worker/Options regressions. The
  original variable-duration, transport failure, cleanup and restart tests now
  exercise the canonical engine and driver under `extension/`.
- A read-only preflight attachment failure does not consume a check's one input
  reservation. It retries control after a persisted one-minute cooldown. The
  reservation is still durable before any engine input; ambiguous input is never
  automatically replayed. Attachment failure/retry and failed reservation cleanup
  have focused tests.
- Local Chrome preview of the actual Relay Options passed enable and disable
  interaction with simulated storage/permissions/notifier responses; no warning
  or error console entries were observed. Screenshot:
  `data/verification-integrated-options-20261023.jpg`. This is UI evidence, not
  installation, permission-grant or live-solving evidence. The preview tab and
  owned port-8879 server were closed.
- All extension JS syntax checks, Relay 2.16.0 manifest/asset checks, fixture
  Python Ruff and scoped whitespace checks pass. Final suite output is in
  ignored `data/verification-integrated-tests-20261023.txt`.
- Integration received self-review of permission gating, enable-event races,
  durable input limits, pre-input control retry, same-outage cancellation,
  normal collector recovery guards and private status fields. No independent
  review or live solve is claimed. No backend service or provider was redeployed.

Primary references:
- https://developer.chrome.com/docs/extensions/reference/api/debugger
- https://developer.chrome.com/docs/extensions/reference/api/permissions
- https://chromedevtools.github.io/devtools-protocol/tot/Input/
- https://chromedevtools.github.io/devtools-protocol/tot/DOM/

Limit: a bounded fallback is still timed input. Readable adaptive feedback and
reliable live unattended solving are unverified until tested against this widget.

## Earlier companion verification (historical)

- Initial nine behavioral tests failed before the engine existed; implementation
  passed them. All **21 focused tests** and **247 total extension tests** pass in
  the final run, with zero failures/skips. The focused suite adds transport,
  detector and controller checks. Actual Chrome debugger input remains untested.
- A Chrome loopback fixture passed six real DOM/timer scenarios, with zero warning
  or error console entries: closed-shadow target, obscured/off-screen rejection,
  hidden cached content, feedback release plus stable recovery, and blocked-route
  rejection. Its driver is local test code, not the actual Chrome debugger input
  transport. Screenshot: ignored `data/verification-assistant-qa-20261023.jpg`.
- JavaScript syntax, companion manifest/declared assets, fixture Python Ruff and
  scoped Git whitespace checks pass. The full extension regression command is
  recorded in `.github/workflows/ci.yml`; final output is retained in ignored
  `data/verification-assistant-tests-20261023.txt`.
- Review was a self-review, covering scope/route boundaries, same-document and
  movement checks, cleanup, independent watchdog, unknown-feedback fallback,
  monotonic hold timing, durable limits, private status and debugger ownership.
  Added cases cover ambiguous press failure, late attachment cleanup, worker
  restart dedupe and brief widget replacement. No independent review is claimed.
- That earlier slice changed no collector/backend source, existing scan settings, provider configuration,
  running service, live input, or user mouse state was changed by this slice.
  The previously recovered Walmart tab was observed usable during development.
- The owned loopback QA server and fixture tab were closed after validation.
  A later optional compact screenshot timed out; the earlier saved full screenshot
  is retained. No application test failed and no browser input was retried.

The Chrome connector rejected claiming its internal extension-management tab
(`chrome://extensions`). This is a UI capability limit, not a Walmart denial.
The available native-app surface is disabled, so the companion cannot be loaded
through the available UI controls. No Chrome preference files were edited and no
alternate debugger session was opened to work around the connector restriction.

## Current activation handoff

1. Disable the old **Reviewer Verification Assistant** companion in Chrome.
2. Reload the existing **Reviewer Item Relay** and accept the new debugging
   permission. Confirm it shows **2.16.0**, then refresh the reviewer tab once.
3. Open regular Relay Options → **Enable automatic verification**. The status
   below that control describes permission, loading, attempt limits and outcomes.

No second extension should be loaded. Once active, the application detects checks
and performs bounded attempts independently of Codex. Actual Chrome input,
readable feedback on Walmart's widget and live unattended reliability remain
unverified. A further agent-operated live CAPTCHA test requires action-time
confirmation under the browser-control rules; prior approval was already used.

The owner explicitly permits native mouse movement/control if later testing
shows it is needed. No native mouse implementation was added in this slice.
