# Elite mobile: lead-agent development and simultaneous launch plan

Updated November 1, 2026 (America/Chicago).

**Status:** implementation started in the separate private project
`C:\Users\Navi\Documents\repos\reviewer_alerts_elite`. The first Android/backend
demo increment and matching uncompiled Swift source are implemented; production
identity, collector/device jobs, Walmart claiming and payments remain open.
Current evidence/resumption record: `reviewer_alerts_elite/docs/ACTIVE.md`.
Product baseline: [Elite product specification](elite-mobile-product-spec-20261101.md).
The user authorizes Codex to lead engineering and delegate bounded work. Android
and iOS source develop in parallel. Android is tested continuously; iOS builds
and runtime tests wait for the owner's Android-experience approval. Both apps
must be available on the first public Elite launch day.
This record is additive: the existing incomplete referral/scanner plans and
`ACTIVE.md` remain owned by their current work. Do not replace those records.

**Budget revision:** the owner rejected the $164/month dedicated Mac. The current
recommendation is on-demand macOS CI using Codemagic's individual M2 allowance;
no dedicated rental, paid overage or device-cloud subscription is selected.
Parallel source development and simultaneous public availability remain
requirements. The former dedicated-host proposal is superseded.

**Latest accepted testing cadence:** Android is the continuous feedback platform.
No iOS build is triggered merely to keep pace with Android changes. After the
owner approves the demonstrated Android experience, run batched iOS compilation,
tests and fixes, followed by signed release validation. Never treat Android
approval or source review as proof of iOS runtime behavior.

**Codemagic account read-back:** signed-in Chrome at `codemagic.io/apps` and its
Billing page shows Personal Account, **0 / 500** free macOS minutes used,
`Enable subscription`, and no billing transactions. Applications is empty.
GitHub setup reaches `Next: Install App`; no integration was installed, repository
authorized, credential created, application added or build started. The wizard
was dismissed. Paid subscription remains unenabled.

## Accepted product direction

- Elite is $99/month. Whop remains the subscription/access authority; connect
  Discord to the same verified customer identity.
- An installed phone app receives member-specific jobs from the backend. It
  retains the customer's Walmart session on that device and uses their selected
  preferred address. The backend never receives Walmart passwords or cookies.
- Explicit Claiming Mode keeps the app foreground and prevents automatic display
  sleep. A plugged-in spare phone/tablet is supported. Leaving the app, locking
  the phone or losing connectivity changes readiness; push brings users back.
- Owner-approved product types (Couch, Recliner, Dishware, Pots and Pans, etc.)
  and minimum listed retail values replace arbitrary member search phrases.
  Members can suggest categories; they cannot silently create new ones.
- Optional standing success-fee offers determine attempt priority. Show active
  request counts with their scope; keep other customers' identities and offers
  private initially. Preserve a no-additional-fee matching request option.
- A successful claim means Walmart accepted the exact item order and displayed
  a definitive order-confirmation state. Shipment, arrival and later fulfillment
  are outside this event. No fee is earned merely by clicking submit.
- Existing $30 Pro immediacy, free 24-hour history, unlimited signups, existing
  member watches and retired offers remain unchanged.

## Implementation baseline

The linked product specification and this plan define the implementation target.
Existing behavior is distinguished from new work below. Freeze the machine-readable
contract fixtures before workers implement against them. Routine implementation
choices remain lead-owned; changes to pricing, fee trigger or public launch
requirements return to the owner as concrete product changes.

| Module | Responsibility | Depends on |
| --- | --- | --- |
| mobile-contracts | Versioned identity, request, job and confirmation schemas; shared fixtures | None |
| elite-identity | Whop login/access, Discord binding, device registration/revocation | mobile-contracts |
| elite-discovery | Export fresh validated observations; narrow product taxonomy | mobile-contracts |
| elite-jobs | Readiness, ranking, budgets, dispatch, reconciliation, audit | elite-identity, elite-discovery |
| android-client | Kotlin/Compose UI, WebView session, Claiming Mode, claim state machine | mobile-contracts |
| ios-client | Swift/SwiftUI UI, WKWebView session, Claiming Mode, claim state machine | mobile-contracts |
| elite-settlement | Confirmation records and separately scoped success-fee payments | elite-identity, elite-jobs |
| mobile-release | Signed artifacts, real-device evidence, dual-store launch | All above |

Both clients consume one API and identical scenario fixtures. Use integer money
amounts in minor units. Distinguish listed retail value, allowed Walmart checkout
total (initially zero), offered service fee and reserved monthly budget.

## Execution environment I can operate

### Windows controller and Android host

Keep this chat and the canonical source on Windows. Codex can edit source, run
PowerShell, invoke SSH/SCP, build Android, drive ADB/UI tests, inspect screenshots
and retrieve remote test artifacts. No additional AI subscription is required by
this design; a hosted Mac is an execution machine for the same engineering work.

Read-only/tool inventory established during this planning turn:

- Git, authenticated GitHub CLI, OpenSSH, SCP, Python, Node and Java are present.
- Java is Temurin 21.0.9; Android SDK platforms 31, 34, 35 and 36 are installed.
- Android Emulator 36.3.10; `Pixel_9a` and `Pixel_Desktop` AVDs exist.
- `Pixel_9a` uses an API-36 Google Play x86_64 image. A read-only, headless probe
  booted successfully; ADB navigation, UI assertions and screenshots worked.
  This proves emulator control, not an Elite app build or claim flow.
- No Android/iOS mobile project exists in this repository yet.
- Baseline HEAD is `a146712`; approximately 200 paths were already dirty/untracked.
  Preserve that work. Do not build/deploy a dirty whole-repository snapshot.
- No applicable repository/ancestor `AGENTS.md` was found in the inspected paths.

### On-demand macOS CI: current low-cost recommendation

Use **Codemagic individual/personal, macOS M2**, with 500 free macOS build minutes
per month and no fixed rental charge. Published additional M2 usage is $0.095 per
minute if billing is enabled: 100 extra minutes cost $9.50, 300 cost $28.50.
The allowance is for personal accounts, not Teams. Keep paid billing disabled
initially; no new monthly spend limit or overage is authorized by this plan.
Free minutes are a bounded allowance, not a promise to finish the app for free.

Native Swift/SwiftUI and Kotlin remain the stack. Source develops in parallel;
Android/backend checks run locally throughout development. Codemagic remains
idle until the Android-approval gate. After approval, the agent dispatches a CI
workflow through its API and retrieves artifacts. Batch compilation, unit tests,
XCUITest and screenshots into the first iOS validation job where practical.
Fix discovered iOS defects, rerun affected checks, then prepare the signed release.

Use **manual-only/API-requested triggers** with automatic push/PR schedules off;
do not create automatic GitHub macOS jobs as a fallback. Batch changes, cache
dependencies and cancel superseded builds. Initial workflow timeout is 20 minutes;
increase deliberately only when useful work demonstrably needs more time.
Track minutes per validation pass. Keep iOS jobs gated until explicit owner
Android approval. Temporary SSH/VNC debugging is available on Codemagic, but its
documentation requires enabling it in the UI for each debug build. Do not claim
a permanent SSH host or UI-free debug-session provisioning. Routine CI execution
uses the API and does not require interactive remote access.

After Android approval, CI acceptance requires that Codex, from Windows, can:

1. Dispatch a workflow and query its installed Xcode/runtime versions.
2. Build the actual Elite app and boot a selected simulator inside the job;
   avoid spending a separate build on a disposable sample app.
3. Run a UI test that taps a control and asserts the changed screen.
4. Retrieve a screenshot and inspect the actual image locally.
5. Download `.xcresult`/logs, diagnose a failure and rerun the corrected revision.

Use a **private mobile source repository** when implementation begins. The
existing notifier remote was read back as PUBLIC during this budget revision;
do not push proprietary mobile/claiming code into it. This local planning record
has not been committed or pushed. A private GitHub repo's included Actions
allowance can provide an alternative macOS runner after quota is checked; do not
make source public to obtain free CI. Account sign-in and allowance are now
verified in Chrome; repository integration, API access and workflows are not set up.

### Physical devices without making the owner a test operator

Start with the local Android emulator for routine automated coverage. iOS Simulator
coverage begins after Android approval. Signed real-device checks remain required
before release.
Use existing/borrowed physical devices via Play testing/TestFlight for the
lowest additional cost. No paid device-cloud subscription is required to start.

If fully agent-operated physical-device coverage is required, evaluate an
on-demand device-cloud API using Appium and downloadable evidence. BrowserStack
App Automate is a candidate; App Live is manual access. No device-cloud account
is connected and no recurring subscription is selected. Physical-device access
and any associated expense remain separate from free simulator CI minutes.

The device-cloud onboarding ticket must demonstrate native controls AND the app's
WebView with a signed build. Public device-cloud re-signing can alter entitlements;
APNs, Keychain persistence, associated domains and app-integrity evidence must
be tested in their actual signing configuration. A simulated push is not proof
of APNs delivery. Do not buy Enterprise distribution to bypass normal publishing.

Use an owner-accessible real iPhone/Android via TestFlight/Play testing for final
signed-build and long-duration Claiming Mode checks. If all real-device actions
must be agent-operated, arrange an API-controlled physical device with appropriate
signing and a persistent test session; select that provision only after its
acceptance smoke. A consumer phone on the owner's desk is not automatically
remotely controllable from this Windows session.

Owner-only inputs: provider purchase/account acceptance, Apple/Google enrollment,
identity verification, 2FA when required, test-account authorization, and release
approval. Codex handles commands, development, test execution and diagnosis after
access is connected. Never ask the owner to transfer private keys through chat.

## Product runtime and confirmation contract

1. Collector exports fresh normalized observations to a separately authenticated
   HTTPS mobile service through an outbound durable outbox. The existing
   loopback server stays private; the Mac build host is not production hosting.
2. Backend matches approved product type and minimum retail value, checks active
   Elite access and current device readiness, and atomically reserves budget.
3. Rank matching offers descending; proposed tie-breaker is oldest qualifying
   request, followed by a stable ID. Snapshot the offered fee at allocation.
4. Issue one member/device/item/variant-bound job with a short expiry. Use a
   foreground WebSocket for speed; push is a notification/reconnect fallback.
5. In the app-owned browser, check item, preferred address and zero checkout
   total before submission. Unexpected charges/addresses, verification requests
   or changed page structure stop submission and request attention.
6. Detect a definitive confirmation for that job's exact item. Prefer structured
   page/DOM state and order ID over screenshot OCR. Associate the confirmation
   with the submission event; old pages cannot confirm new jobs.
7. Persist the confirmation locally, retry its report safely, and settle one fee
   against the backend job. A redacted screenshot is optional diagnostic evidence;
   do not upload full address, payment details, cookies or the entire browser.
8. Retire the successful one-item request; consider the next eligible member
   if claiming remains available. Observed availability is not inventory count.

Offline before dispatch means skip, without a success fee. Loss of connection
after possible submission means `unknown`, not failure: block duplicate
submission of that request until reconciled. State model proposal:
`queued -> offered -> acknowledged -> submitting -> confirmed|failed|unknown`.
Payment status is a separate state machine; `confirmed` never implies `paid`.

Billing proposal: explicit customer consent to their offered fee, a monthly cap,
server-side budget reservation, and a saved provider payment method. Charge after
confirmed success. If collection fails, keep the claim confirmed, show unpaid
fee/action needed, and pause additional paid attempts. Do not revoke a Walmart
order or silently charge a losing member. Do not create a stored-money wallet.
Authorization holds are not a prerequisite of the first implementation.
Paid fees, confirmed unpaid fees and open reservations consume the snapshotted
UTC-calendar-month cap without double counting or freeing budget upon payment.

App-reported confirmation is practical operational evidence, with residual
client-tampering risk. Add platform integrity checks, replay protection and audit
correlation; do not describe screenshots or client signatures as independent
Walmart confirmation. The supported Walmart login/claim route must be established
on both platforms; do not bypass identity, consent or anti-bot gates.

## Existing code to reuse, not overstate

- `discord_bridge/observations.py:normalize_item`: public product/price fields.
  Its review URL is constructed/unverified, and observed `available` does not
  establish eligibility for a customer account.
- `discord_bridge/core.py:JobStore.record`: durable intake/deduplication boundary.
- `classification.py:classify_product`: broad department evidence; add a separate
  narrow product-type taxonomy rather than changing current feed semantics.
- `request_store.py`: minimum-value matching and uncertain-send/outbox patterns.
- `whop_client.py`: fixed-origin/versioned provider validation and identity
  patterns. The existing adapter explicitly does not charge/refund/cancel; put
  success-fee operations behind a new narrowly scoped adapter.
- Referral membership selection optimizes next-bill discount eligibility; do not
  reuse it as Elite paid-through entitlement logic. A canceled-at-period-end
  subscription can retain access until paid-through expiry.
- `request_commands.py` and `request_delivery.py` currently check Pro specifically.
  Whop-native access and app session middleware are new work; a Discord role by
  itself is not a verified Elite payment.
- Existing `service.py` is loopback and not a public mobile API. Add a customer
  HTTPS service with device authentication, authorization and bounded endpoints.

## Parallel source development and work ownership

Use these paths in the new private mobile project: `mobile/android/`,
`mobile/ios/`, `mobile/contracts/`, `mobile/test-fixtures/`, `mobile/tools/`,
`elite_service/`, `tests/elite/`. This existing notifier repository keeps only
the additive planning records and a later narrowly reviewed collector adapter.
Use one frozen API/schema revision and backend-served test fixtures before
admitting platform workers. Existing live files change only at explicit
integration boundaries, after compatibility checks.

Root/lead owns contracts, planning, integration, test evidence and release.
With the current four-slot harness: one Android worker owns Android source/tests;
one iOS worker owns iOS source/tests; one backend worker owns the new service/tests.
They do not edit shared contracts/lockfiles/trackers. When integration is ready,
drain workers and use a fresh read-only reviewer/test engineer. The owner does not
dispatch workers or integrate their results. Use isolated feature work when
implementation starts; preserve the current uncommitted baseline explicitly.

## Ordered development checklist

Every implementation ticket below is unstarted. Split each implementation assignment
into a bounded slice of approximately five files or fewer; do not give a worker
an entire module plus its shared dependencies in one assignment.

| ID | Outcome and acceptance | Verification | Depends on |
| --- | --- | --- | --- |
| E00 | Prepare private project and manual-only CI configuration without starting an iOS build | Private remote read-back; scoped integration; disabled automatic triggers and paid billing | Development setup |
| E01A | Android app build/test/screenshot harness runs locally | Gradle + ADB/UI test evidence; existing environment probe already passed | E00 |
| E02 | Freeze module boundary/API revision and identical happy/failure fixtures | Schema tests; both teams review consumed revision | None |
| E03A | Android shell shows requests and Claiming Mode against fixture server | Gradle build + instrumented UI tests + screenshots | E01A, E02 |
| E03I | Write matching iOS shell against the same fixtures | Source/API parity review; explicitly uncompiled until E10I | E00, E02 |
| E04 | Real authenticated user/device has correct Elite access; revoke/expiry works | Provider-backed access checks plus negative/replay tests | E02 |
| E05A | Android login/address/$0 claim state machine recognizes exact confirmation | Emulator/fixture end-to-end for G1; signed real-device/authorized claim evidence at E12 | E03A; E04 for real-account verification |
| E05I | Write iOS login/address/$0 claim state machine and matching test sources | State/API review now; runtime and real-account verification deferred to E10I | E03I; E04 for real-account verification |
| E06 | Approved categories and suggestions work without unrestricted member filters | Golden product sample; ambiguous/unknown-price negative cases | E02 |
| E06D | Fresh validated observations export durably without seed/history replay or blocking Pro delivery | Export restart, duplicate intake, offline sink and Pro-latency regression tests | E02 |
| E07 | Highest eligible offer gets one job; offline skip, ties, caps and unknown outcomes survive restart | Concurrent allocation, reconnect, replay and crash tests | E04, E06, E06D |
| E08 | Each confirmed job has one fee settlement; failure remains unpaid; losers never charged | Backend/provider-supported tests and Android fixtures now; iOS path verified at E10I | E05A, E07 |
| E09A | Android readiness, keep-awake, reconnect and notification fallback work | Emulator/instrumented lifecycle tests for G1; prolonged signed physical-device run at E12 | E03A, E07 |
| E09I | Write iOS readiness/keep-awake/reconnect/push sources and tests | Source review now; actual lifecycle/signing/APNs evidence at E10I/E12 | E03I, E07 |
| E10A | Polished Android feature set is ready for owner review | Android tests, screenshots and recorded walkthrough of actual flows | E04, E05A, E06, E06D, E07, E08, E09A |
| G1 | Owner approves Android experience as the shared product target | Explicit owner approval linked to demonstrated emulator/fixture revision; lead cannot self-approve; live-device proof is E12 | E10A |
| E10I | Validate/fix the drafted iOS app against approved Android behavior | Batched actual-target build, unit/UI tests and screenshot review; fixes remain required | G1, E03I, E05I, E09I |
| E11 | Existing notifier/Pro and new mobile flows pass combined regression | Existing repo CI + both native suites + available real-device results | E10I |
| E11D | Staging/public HTTPS mobile API deploys with scoped authentication, durable storage, health and rollback | Both physical platforms connect outside the owner's network; restart/revoke/export smoke | E07, E08, E10I for paired physical-platform verification |
| E12 | Both signed release candidates install and run; store accounts/listings/billing ready | TestFlight + Play testing; privacy/account deletion; review evidence | E11, E11D |
| E13 | Both public store downloads are verified before Elite public activation | Store install smoke on both; backend activation receipt and rollback | Both store approvals |

Earliest useful milestone is **Android receiving and completing a fake job with
screenshots and passing tests, alongside matching iOS source and test scenarios**.
The Swift app is deliberately uncompiled at this stage. Develop both source trees
and the backend concurrently after E02. Android changes drive daily feedback;
freeze the accepted experience at G1, validate iOS at E10I, then verify signed
builds on both platforms. Public Elite activation waits for both store installs.

Necessary iOS build groups, rather than a build on every source change:

1. After G1, compile and run simulator/unit/UI validation in a combined job.
2. Rebuild to fix actual compilation/runtime/OS-specific failures, with affected
   tests selected; do not waive failed checks to meet a build-count target.
3. Produce and verify the signed TestFlight/App Store candidate. Reuse that
   candidate for release if unchanged; signing or release defects require fixes.

No fixed total build count is promised. Avoiding redundant jobs is the cost goal;
working iOS behavior and the shared public launch remain release requirements.

## Commands and evidence

Existing commands (not executed as product regression for this documentation-only
change): `.venv\Scripts\python.exe -m pytest -q`,
`.venv\Scripts\python.exe -m ruff check .`,
`.venv\Scripts\python.exe src/server.py --help`,
`node tools/generate-native-protocol.mjs --check`, and repository JS tests.

Proposed commands after scaffolding, **not currently runnable app checks**:

```powershell
# Windows; an emulator must already be selected and booted.
.\mobile\android\gradlew.bat -p mobile/android testDebugUnitTest lintDebug assembleDebug
.\mobile\android\gradlew.bat -p mobile/android connectedDebugAndroidTest
```

```bash
# macOS CI job; choose an installed simulator's actual UDID.
xcodebuild test -project mobile/ios/Elite.xcodeproj -scheme Elite \
  -destination "platform=iOS Simulator,id=$ELITE_SIMULATOR_UDID" \
  -resultBundlePath artifacts/ios/Elite.xcresult
xcrun simctl io "$ELITE_SIMULATOR_UDID" screenshot artifacts/ios/claiming-mode.png
```

Persist each run's revision, platform/runtime, command, test outcome, screenshots,
bounded redacted logs and skip reason. Compare both platforms against one matrix:
expired session, wrong address, nonzero total, sold out, duplicate job, duplicate
report, interrupted submission, restart, concurrent devices, budget exhaustion,
membership expiry, cancellation at period end, payment failure and notification.
Fixture pass, live claim acceptance and physical hardware proof remain distinct.

## Account setup, spending and release

The user rejected the $164/month rental. Start macOS execution with the personal
CI allowance and paid overage disabled. Review measured minutes before proposing
any paid usage. Device cloud, public backend hosting and developer accounts are
separate costs; the free CI allowance does not cover those. Apple Developer
enrollment remains $99/year for public distribution. No subscription, device
purchase or paid CI configuration is activated by this plan revision.

Apple enrollment/signing/2FA and Google identity verification remain owner account
actions. Store billing for the digital Elite subscription and success fees must
be resolved before production billing is advertised. Apple manual release and
Google's applicable publishing controls coordinate launch; first-publication
constraints and store propagation prevent promising identical release seconds.
New Google personal accounts need the applicable closed-test period; run it in
parallel with engineering and iOS TestFlight. Enable paid Elite publicly only
after both public app installs pass the release smoke.

No production notifier restart, membership role edit, live claim, customer charge,
cloud purchase, commit, push or publication is authorized merely by completing
this plan. Subsequent implementation follows the user's continuing instructions.

## Sources checked November 1, 2026

- [Codemagic personal allowance and per-minute pricing](https://docs.codemagic.io/billing/pricing/)
- [Codemagic native iOS builds](https://docs.codemagic.io/yaml-quick-start/building-a-native-ios-app/)
- [Codemagic builds API](https://docs.codemagic.io/rest-api/builds/)
- [Codemagic temporary SSH/VNC debugging](https://docs.codemagic.io/troubleshooting/accessing-builder-machine-via-ssh/)
- [GitHub Actions alternative runner pricing](https://docs.github.com/en/billing/reference/actions-runner-pricing)
- [Apple Developer membership](https://developer.apple.com/programs/whats-included/)
- [Apple command-line build, simulator and result tools](https://developer.apple.com/documentation/xcode/xcode-command-line-tool-reference)
- [Android keep-screen-on API](https://developer.android.com/develop/background-work/background-tasks/awake/screen-on)
- [Apple idle timer](https://developer.apple.com/documentation/uikit/uiapplication/isidletimerdisabled)
- [BrowserStack real-device automation](https://www.browserstack.com/app-automate)
- [BrowserStack iOS re-signing constraints](https://www.browserstack.com/docs/app-automate/appium/advanced-features/disable-resigning-of-apps)
- [Apple TestFlight](https://developer.apple.com/testflight/)
- [Apple manual release](https://developer.apple.com/help/app-store-connect/manage-your-apps-availability/select-an-app-store-version-release-option)
- [Google publishing controls and first-launch limitation](https://support.google.com/googleplay/android-developer/answer/9859654)
- [Google new-personal-account testing](https://support.google.com/googleplay/android-developer/answer/14151465)

## Environment verification and resumption

Android runtime probe: **passed** on the existing API-36 / Android-16 Pixel_9a
AVD, using a task-owned read-only/headless instance on port 5564. ADB reported
`sys.boot_completed=1`; opened Settings, parsed the actual UI tree, tapped the
observed Display & touch control, asserted Brightness level on the resulting
screen, and captured/retrieved screenshots. The Settings image was visually
inspected. This required no user-operated emulator interaction. The owned
instance was stopped after collection; no existing AVD configuration was edited.
Evidence is local/ignored under `data/elite-mobile-environment-20261101/`.

Independent read-only review previously corrected the discovery-export ticket,
real-account identity dependencies and named HTTPS deployment checkpoint.
Final review also separated emulator-based owner acceptance from signed-device
release proof, gated paired physical checks behind iOS validation, and corrected
monthly caps to retain paid fees as consumed budget.
No app build or iOS execution has been performed. Codemagic Personal sign-in,
unused 500-minute allowance and subscription-disabled state are now verified in
Chrome. Repository integration and API access are not configured; the GitHub
setup requires installing the Codemagic app. Limit its access to the new private
project when setup is authorized. Physical-device access remains a later dependency.

Next execution step: prepare the private mobile project, freeze E02's schemas/
fixtures, and begin the Android/backend running shell and matching iOS source.
Configure manual-only CI without starting a build. After G1 owner approval of
the Android revision, perform the first actual-target iOS validation at E10I.
Both platform claim flows and settlement must be verified before public activation.
