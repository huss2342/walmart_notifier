# Local scanner control and status — October 25, 2026

## Current: 2.22.2 delayed expansion fix, live activation pending

The actual department popup contained 4 initial departments and Show More;
manually expanding it revealed 34 departments (35 radio inputs including All).
Captured only the nonsecret filter section into
`data/live-department-filter-20261025.json` and
`data/live-department-expanded-20261025.json`. No application/private state,
credentials or Walmart endpoints were used for the reproduction.

A local browser fixture replaced the captured section's children after a delay.
Normal immediate expansion found all 34. A deterministic case changing Show More
to Show Less immediately, then rendering new options after 540 ms, reproduced
the old helper returning **4 of 34 with ok:true**. This demonstrates a code bug;
it does not prove all prior live failures had the same cause.

`facet-controls.js` now requires more actual options after an expansion click.
Button disappearance/renaming alone cannot prove full coverage. If no growth
arrives within the existing bounded wait, it reports expansion-stalled. Removed
unused signature helper. Two meaningful unit regressions were RED then GREEN.
All 20 facet tests pass, including delayed/multiple-batch/brand/cancellation.
Fresh reviewer independently checked cancellation during the wait and approved.

Real browser GREEN reads **34 of 34, no missing labels, one Show More click**;
console warnings/errors are empty. Evidence:
`data/facet-repro-20261025/browser-red.json`, `browser-green.json`,
`browser-green.png`, actual markup and the isolated fixture source. Full final
extension suite: **487 passed** (`data/facet-expansion-extension-20261025.txt`).
Backend models have not changed since **1,641 passed, one platform skip**;
lint, syntax and independent review pass.
The isolated IAB test tab is closed. Worker stopped its owned server and verified
port 8791 has no listener. Captures and reproduction source remain saved.

Source **2.22.2** includes the expansion fix and optional failure-code telemetry.
Backend telemetry support is already healthy/deployed as recorded below; no
second backend rollout was needed for the JS fix. User was asked to reload
2.22.2 and refresh Walmart; this supersedes the 2.22.1 request. Live status still
reported 2.22.0 before the request. Next: confirm new version, inspect exact safe
failure code if any, then verify real department targets and pagination. Overall
scraping task remains open; home/keyword recovery was already observed.

## Latest follow-up: 2.22.1 diagnosis

Owner manually cleared the home check and requested that the agent handle future
checks. Browser CAPTCHA policy still requires action-time confirmation; do not
treat future preapproval as that confirmation. The agent sent no CAPTCHA input.
The configured automatic verifier independently reported a canceled attempt with
confirmed cleanup. Existing preferences and limits were preserved.

After legitimate clearance, old unowned tabs still had inline verification/stale
content scripts. Root refreshed the home, old department, old keyword and old
Pet pages once each at their existing URLs; no challenge input or forced pause
reset. Normal fresh probes cleared the shared pause. Home resumed its compatible
checkpoint, encountered a confirmed empty page16 and used its existing category
fallback. Keyword lane resumed Pet through page 10, then advanced through Accessories
to Appliance, reaching 25 pages. Fresh owned lanes 1530560542/0543 and home 1530560453
were confirmed in the automatic Reviewer scans group. Manual popup research on
the old passive catalogue was closed afterward; user tabs remain open.

Fresh department round 4 exhausted its three discovery retries: totalTargets 1,
completed 1, pages 0, failures 1, partial. This is a real remaining coverage problem,
not a successful scan. An independent coupled content/background/manager VM
confirms accepted discovery, checkpoint and queue advancement work; no integration
bug or selector defect was established. Do not weaken aria-hidden/challenge guards.

Source **2.22.1** adds optional lane `lastFailureCode`, derived only from fixed
known facet/watchdog codes. Raw error messages remain browser-local. The server
accepts only the strict enum and continues accepting older reports unchanged.
New actual-background and parser regressions pass, as does the real JS-produced
snapshot against the Python validator. Full suites: **485 extension tests,
1,641 Python tests passed, one existing platform skip**. Lint, syntax, packaged
startup and independent review pass. Exact watchdog wording was reproduced RED,
fixed and independently verified.

Only `src/scanner_control.py` was layered over the actual current image. Backend
is healthy; environment/mount/access/security comparisons match, settings retain
all 65 terms and existing verification preferences; current 2.22 reports remain fresh.
Current image: `reviewer-notifier:scanner-failure-code-20261025`, also tagged local,
sha256:26765b8b5f5b2a672097fc9a128806b96979125bea49ac572d005d645ec2c530.
Rollback: `reviewer-notifier:before-scanner-failure-code-20261025`.
Evidence: `data/scanner-failure-code-*-20261025.txt`,
`data/scanner-status-code-contract-20261025.json` and
`data/scanner-failure-code-20261025/build/`.

Owner was asked to reload **2.22.1** and refresh Walmart. Once that is confirmed,
read `data/scanner-status.json` directly for the retained exact failure code,
repair the demonstrated cause, and verify real discovery/department pagination.
Do not call the overall scraping task complete yet. No commit/push or owned
test server/tab remains. Older sections below preserve 2.22 baseline history.

## Authorized outcome and current state

The owner asked to stop requiring Chrome Options read-backs and move routine
settings/status somewhere local tools can read and edit. Preserve concurrent
home/department/keyword coverage, scrolling, tab grouping, durable recovery,
shared verification pause and existing input limits. This is a declarative
backend interface, not a proxy to protected Chrome pages or arbitrary scripting.

Relay **2.22.0** is implemented, backend deployed and healthy, and extension live
activation confirmed by its actual status report after the owner's refresh. Department/keyword/group/rotation
remain enabled by default, brands off.
The supplied keyword list matches the 65 defaults. Preserve the owner's existing
automatic-verification enabled/native-fallback disabled preferences when seeding
the backend file. No CAPTCHA input or budget reset is authorized by this record.

## Implementation

- `src/scanner_control.py` and `src/server.py`: strict, atomic local settings,
  commands and safe status APIs/files, existing token/origin/size guards retained.
- `extension/scanner-control.js` and background integration: heartbeat sync,
  last-usable cache retention, explicit status fields and durable action/ID
  restart receipts; verification defers restarts without spending their IDs.
- Options uses the same backend preferences. Fixed its background sender check:
  an exact own-Options sender is valid even when Chrome includes tab metadata.
- Added in-lock pause check before auxiliary restart erases progress. An ID
  shared by home/additional actions now runs each named action once.
- Chrome verifier defers known inactive/frozen/unfocused targets before attempt
  reservation, rechecks after attachment, reports waiting state and retries
  after 60 seconds. Native safety checks and ten-attempt limits stay intact.
- CI includes the new bridge suite; examples, README and architecture updated.

Chrome endpoint/token stay browser-local. Status excludes credentials, product
details, full URLs and raw errors. Restart commands cannot clear verification,
change budgets, solve checks or issue arbitrary browser operations.

## Verification and remaining work

Full Python: **1,607 passed, one existing platform skip**. Actual JS-produced
snapshot validates unchanged against Python models, including both lanes,
10/10 attempts and nullable tab/window metadata. Focused RED→GREEN regressions
cover the Options sender bug, same-ID actions, auxiliary pause race and verifier
preflight/attachment race. Lint, syntax and source startup pass. The Options
browser preview saves successfully with no console errors (simulated backend).
Final extension regression: **483/483 passed**. Independent review approved the
backend, bridge, Options integration, verifier preflight and actual snapshot.
Review reproduced and closed same-ID collision, auxiliary reset pause race,
primary selection/revocation pause races. All preserve checkpoints and leave
deferred command IDs unspent. The test-only Options preview tab/server are closed.

Source baseline is the owner's existing extensive working tree; unrelated
Discord/backend work is preserved. No commit or push. Backend deployment layered
only `src/server.py` and new `src/scanner_control.py` over the actual live image;
Compose environment, mounts, loopback binding and protections compare equal.
API smoke roundtripped the real settings file and idle commands and verified
trusted-origin rejection. The service's optional token policy is retained
exactly; token-required cases are covered by the HTTP tests.

Live settings contain all 65 terms explicitly, department/keyword/group/rotation
true, brands false, automatic verifier true/native fallback false. No restart ID
was issued. `data/scanner-status.json` now has fresh 2.22 reports with settings
synchronized and all 65 terms present. See live evidence below.

Candidate/current image: `reviewer-notifier:scanner-control-20261025`, also tagged
`reviewer-notifier:local`. Rollback image:
`reviewer-notifier:before-scanner-control-20261025`. Retag rollback as `local` and
run `docker compose up -d --no-build notifier` to restore backend source only.
Keep the new settings files; the previous server ignores them. Deployment,
configuration comparison and live API receipts live in
`data/scanner-control-20261025/` and contain no credentials.

Owner refreshed; the live report confirms **2.22.0**. Do not request another
reload or Options read-back without an actual new source change. A fresh report
is saved in `data/scanner-control-live-after-20261025.json`. It confirms home
checkpoint15/17, shared verification pause, department partial round3 (one
discovery target skipped, zero pages), keyword5/65 at Pet and currently absent
session ownership after reload. The pause blocks reacquisition; owned:false is
not proof of disabled settings. Both scan preferences are true.

Live Chrome shows the home tab1530560453 at Robot or human and an inline visible
challenge on prior department tab1530560532, with #px-captcha masking the filter
section through aria-hidden on its page ancestor. Do not weaken filter visibility
or verification guards to interact behind it. User was asked to clear the home
check; further inline checks may need clearing/reloading after legitimate session
recovery. No CAPTCHA input performed. Challenge screenshot timed out; no retry or
lower-level workaround was attempted.

Department partial/failure1 means three root discovery retries were exhausted;
a successful empty discovery would show complete/failures0. Current generic
target-error hides the exact retained reason, and diagnostic whitelist drops
auxiliary events. After legitimate recovery, watch a fresh owned discovery tab
for actual department targets before diagnosing a selector defect. Existing
live root/radio names and containment match the helper. Verifier last result is
attempt10/10, move timeout, no press, cleanup confirmed; do not reset budgets or
assume its key is the current target solely from the snapshot.

Evidence: `data/scanner-control-*-20261025.*`,
`data/scanner-status-contract-20261025.json`, and
`data/scanner-status-contract-validation-20261025.json`.
