# Windows verification fallback — October 23, 2026

State: implementation and current-user host installation complete; Chrome
activation and live input/recovery acceptance pending. Owner explicitly requested
full implementation of the Windows mouse fallback. Native input may improve
recovery but cannot guarantee HUMAN acceptance or operation on a locked/noninteractive desktop.

## Current receipt

- Combined extension source is **2.19.0**, preserving the other chats' 2.18.0
  last-page/resume and category/pager changes. No commit, push or backend deployment.
- Installed and verified HKCU host `com.reviewer_item_relay.verification` points
  at this project's generated `data/native-verification/host.json` and locally
  compiled launcher. Exact extension origin is the existing `oidgbnehgnikmniolojjkmhlpifdoiil`.
  WhatIf install/remove previews and foreign-registration ownership checks pass.
- The installed launcher + actual Win32 adapter completed probe/close binary
  framing, rejected an unexpected origin, and sent **no mouse input**. The probe
  correctly reported focus-changed while Chrome was not foreground. Safe evidence:
  `data/native-verification-protocol-20261023.json`.
- Final declared extension suite: **344/344 pass**; native Python suite:
  **98 passed, 1 intentional off-platform-boundary skip** on Windows. Ruff,
  extension syntax, manifest/assets and scoped whitespace checks pass. Logs:
  `data/native-verification-js-20261023.txt` and
  `data/native-verification-python-20261023.txt`.
- Real Chrome local Options preview verified enable/disable and status with
  simulated permissions/storage and no console errors. Screenshot:
  `data/native-verification-options-20261023.jpg`. Fixture tab/server cleaned up.
- Independent reviews approved extension/bridge/controller/installer and
  Win32/watchdog. Required findings fixed with regressions: ongoing active-tab
  cancellation; no-press watchdog cancellation without unrelated mouseUp;
  parent-death recheck after release-gate acquisition. Added tests for slow arm
  TTL, exact owned input stamp and lost/late arm acknowledgement cleanup too.
- No real mouse movement, press, CAPTCHA solve or production extension reload
  was performed. Unit tests use fake injection; actual ABI, renderer geometry,
  cross-process mutex and unarmed watchdog startup/close checks are harmless.
  Native input delivery and HUMAN acceptance remain unverified end to end.
- Next action: owner reloads Reviewer Item Relay in chrome://extensions and
  confirms **2.19.0**, opens fresh Options, clicks **Enable Windows fallback**
  and grants optional Native Messaging/scripting. Automatic verification must
  also be enabled. The browser connector cannot control Chrome's internal pages,
  so this activation remains a manual handoff; do not use another browser channel
  or edit Chrome profile state to bypass it. Live CAPTCHA testing by the agent
  requires fresh action-time confirmation; user operation of their extension
  is separate. No further helper installation is needed.

## Accepted scope and boundaries

- Keep the existing Chrome verifier first. On a later budgeted attempt, use
  native input only after a matching safely completed Chrome failure: confirmed
  no-press move timeout/cleanup, or a completed unsuccessful hold with confirmed
  press, release and cleanup. Never replay an uncertain press or cleanup.
- Optional nativeMessaging + scripting permissions and explicit Windows fallback enable control.
  Native page inspection uses a fixed read-only function in Chrome's isolated world
  rather than attaching the failed debugger transport. Closed widget feedback
  stays unknown and uses the existing bounded hold; page readiness proves recovery.
  Existing enabled switches, attempt reservations, ten/check and ten/hour limits,
  10–20s pacing, shared-session pauses and readiness guards remain authoritative.
- Chrome launches an on-demand current-user helper; no HTTP listener, admin
  elevation, external service, scheduled task, cookie or credential access.
- A temporary inactive extension bridge tab in the challenged Chrome window owns
  the Native Messaging port. Chrome supplies its real --parent-window HWND;
  service-worker Native Messaging would supply zero and is deliberately avoided.
- The helper validates its launch origin against the installed host manifest.
  Only fixed probe/prepare/press/status/release/close operations exist. It binds
  to Chrome's supplied HWND, a unique visible renderer viewport and a validated
  CSS point/DPR mapping. No arbitrary window, keyboard, script or command API.
- Wait for five seconds of current interactive-session user idle before taking focus. Before
  input, the worker focuses and rechecks the actual reviewer tab/window and fresh
  document/target; the host independently checks foreground window, desktop,
  input state and geometry. User activity/focus/window changes abort a hold.
- Main-process polling plus a separate release watchdog enforce a 30-second
  maximum. Parent failure, pipe EOF and missed watchdog heartbeats release an
  armed button. One press per host connection, one system input lease at a time.
- Native results are input evidence only. Existing unchallenged catalogue
  readiness checks still determine recovery. No live CAPTCHA test without
  fresh action-time confirmation. Local dummy-button testing is in scope.

## Frozen Native Messaging protocol v1

Each frame is a four-byte native/little-endian uint32 length followed by UTF-8
JSON, at most 16 KiB. Strict request shape:
`{v:1,id:string,op:enum,payload:object}`. IDs are 1–80 ASCII letters/digits/colon/
hyphen/underscore. Unknown fields, non-finite values and extra payload fields fail.

- probe: payload {}. Returns input state plus ready/reason/idleMs.
- prepare: payload {viewport:{width,height,dpr},point:{x,y}}. Finite positive
  viewport sizes, DPR0.5–4, point inside viewport. Moves only, records a random
  preparation token with two-second expiry. Returns {token,...input state}.
- press: payload {token}. Rechecks input stamp/position/window/desktop/TTL and
  arms the independent watchdog before one mouseDown. Returns input state.
- status: payload {}. Returns input state; no side effects.
- release: payload {}. Releases only the connection's owned/suspected press;
  idempotent. Returns input state.
- close: payload {}. Release/cleanup and finish; also done on EOF/error.

Responses: `{v:1,id,ok:true,state:{...}}` or
`{v:1,id,ok:false,error:fixed-code,state:{...}}`. Safe state contains only
ready:boolean, reason:fixed-code|null, idleMs:number, phase:idle/prepared/holding/
released/aborted/closed, pressSent:boolean, pressConfirmed:boolean,
releaseConfirmed:boolean, holdMs:number, plus token on prepare. No raw errors,
page text, URLs, screenshots or product/account data. Logs retain these labels,
timestamps and durations only, bounded on disk.

## Frozen Python module interface

Shared constants/Target/DesktopError are in native_verification/contracts.py.
WindowsDesktop(parent_window:int):
probe()->dict(ready,reason,idleMs); prepare(viewport:dict,point:dict)->Target;
press(target:Target)->Target (updated input stamp);
abort_reason(target:Target)->str|None; release()->None; close()->None.
It owns the cross-process input mutex and fails closed on other platforms.

ReleaseWatchdog(): start()->None; arm()->None; pulse()->None; disarm()->None;
close()->None. start/arm handshake is bounded, pulse feeds while holding, disarm
is called only after confirmed release; close requests an armed release. Its
separate process performs only mouseUp, never mouseDown or movement.

NativeController(desktop,watchdog_factory=ReleaseWatchdog,clock=time.monotonic):
handle(op,payload)->dict safe state; close()->None. Optional manual tick() supports
deterministic tests; real polling thread checks at <=50ms and feeds watchdog at
<=250ms. Controller locks serialize protocol and polling. Strict framing/origin
validation belongs to host.py; installer records one exact allowed extension ID.

## Ownership and order

Root: contracts/spec, extension bridge/native adapter/controller selection,
Options, manifest/version, JS tests/fixture, integration and final handoff.
Worker desktop: win32.py, watchdog.py and their Python tests. No shared edits.
Worker host: controller.py, host.py, launcher/installer, host/controller/installer
tests and native README. No shared edits. Workers do not delegate or drive live UI.
Build host+desktop against the frozen interface, integrate extension, verify
behavioral failures and process/cleanup cases, review independently, install the
current-user host and perform bounded local dummy-input QA where available.
Chrome extension reload/permission activation is the final user handoff if its
internal pages remain unavailable to the browser connector.

Checks: run the four native Python test files (PowerShell enumeration in native README);
repository Ruff for changed Python; full declared extension Node suite plus new
native adapter tests; actual Win32 probe and local fixture smoke; installer
WhatIf/registration/uninstall ownership; extension syntax/manifest/assets.

Primary references: Chrome Native Messaging documentation; Microsoft SendInput,
EnumChildWindows, GetLastInputInfo and OpenInputDesktop documentation. No new
third-party runtime dependency is required.
