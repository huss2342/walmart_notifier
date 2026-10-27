# Active handoff: private Discord and Whop setup

Updated October 26, 2026 (America/Chicago).

## Discord delivery restored after category renames (October 26)

Owner reported pro-all silent since08:05 and confirmed they renamed headings.
Read-only queue evidence confirms last pre-outage send08:05:53 Central. Runtime
audit found only starter/pro-price category-name mismatches; actual permissions
passed. Source layout now matches owner's STARTER - 5 MIN DELAY and PRO - BY
VALUE. Runtime audit permits cosmetic renames of mapped category headings only;
IDs/types/parents/exact ACLs/unknown resources/channel names stay enforced, and
provisioning stays strict. Seven new regression cases cover the incident and
security boundaries. Independent review approved with no required findings.
Thin image replacing layout.py/provision.py deployed healthy; environment,
mounts/protections/networks/user/command preserved; no Discord resources changed.
Direct audit now ok with no findings; Discord API confirms a new bot-authored
five-card pro-all message at19:05:10 Central. First recovery19:04:35; ten pro-all
items confirmed across two messages, pending pro-all0 at saved snapshot. Paid
TTL unchanged;6703 pro-all destinations expired during the outage and were not
requeued. Final full Python suite **1746 passed/one platform skip/zero failures**;
Ruff/byte compilation/server startup checks pass. Health confirms Pro/Starter
pending0; delayed Free backlog is draining under learned rate limits. See
[discord-delivery-category-renames-20261026.md](discord-delivery-category-renames-20261026.md).

## Focused page reading (October 26)

Owner reports tab selection and department scrolling still skip pages. Source
**2.24.1** fixes confirmed background-navigation and incomplete-reading paths:
automatic rotation requires granted visible window focus before reading and
navigation; denied turns retain the page. Smooth variable scrolling resumes
across bounded turns until the ready bottom and item signature settle. New cards
are acknowledged and invalidate completed reading before navigation. Same-page
waits reuse intake acknowledgements; each new page still sends all observations.
Fresh review caught an owned-pager-scroll reread loop, fixed with a RED/GREEN
real-animation regression. Explicit rotation-off background mode is preserved.
All **652 extension tests** pass, zero failures/skips; Ruff, JS syntax and scoped
whitespace pass. Both local browser fixtures pass with zero console warnings or
errors (Chrome grants simulated; production DOM/scroll code). No backend change
or deployment in this slice. Plan A backend remains deployed and healthy.
Owner reloaded **2.24.1**; live scanner status and diagnostic mirrors confirm it.
Live foreground rotation observed all three owned tabs. Departments completed
Arts Crafts & Sewing through page18, then selected Office Supplies and advanced
to page2 with smooth downward movement observed. Home advanced5→8; keywords
completed Home Improvement through page10 and moved to Hotel. Results-ready
events precede native page transitions. No increase in the department's historical
failure count1. Metrics reached9 successful transitions,0 new challenge episodes,
0 URL fallbacks in the saved observation window; this does not establish reduced
challenge odds. No further source/reload/settings change needed for this slice.
Supersedes the earlier Plan A2.24.0 reload request. Details and receipts:
[focus-reading-20261026.md](focus-reading-20261026.md).

## Scan load reduction — Plan A (October26)

Owner authorized all four levers. Source2.24.0 now adds one paced no-op retry,
60-second cooldown waiting/probe rather than repeated full loads, stable additive
cycle jitter, initial one/two-interval offsets and persisted auxiliary start spacing.
Scrolling/focus/page coverage/verification input remain intact. Durable metrics
separate from300-event history measure transitions, challenge episodes and open/
completed downtime. Strict backward-compatible backend telemetry adds no raw URLs,
products or secrets. Fresh review found and fixed late native-response races in
retry focus and fallback persistence, plus a metric queue initialization race.
All639 extension tests pass; real-browser Plan A focus/retry/jitter fixture passes.
Full Python suite:1739 pass/one platform skip; Ruff/allJS syntax/whitespace/server
startup pass. Both real-browser fixtures pass with zero warnings/errors, including
existing varied scrolling/shared-pause regressions. Thin backend telemetry image
is deployed healthy; environment/mounts/protections/networks/user/command preserved.
Rollback reviewer-notifier:before-plan-a-20261026. Local test tabs/server closed.
Owner reload is now superseded by2.24.1 above; last mirror remains2.23.13.
No settings/Options changes needed. Reduced flag frequency is unproven
pending activation and a comparable observation window; do not promise prevention.
Details: [scan-load-plan-a-20261026.md](scan-load-plan-a-20261026.md).

## Applied department hierarchy (October 26)

User screenshot led to live dropdown validation: root Show More reveals 35
departments; Home's menu changes to a selected Home parent and 19 children.
The helper rejected `name="Home"` as another facet, stopping before sibling
Show More (4 children only); header-only leaves falsely raised missing-panel.
Source **2.23.6** recognizes checked named parents only inside the exact
Departments header group/filterPopover. Two RED regressions and real-browser
captured-markup RED4/GREEN19 prove the fix; simulated leaves return empty
success. All **562 extension tests**, Ruff, syntax and focused self-review pass.
Owner activated 2.23.6 and selected a scanner. Home/keyword scans advance but
department skipped Home with a new unexpected-error (failures13→14, pages0).
Opening its dropdown once showed automatic Show More expansion, not successful
selection. Broader department pagination remains unverified/failing.
Source **2.23.7** adds bounded current-generation lane runtime/focus-wait metadata
and specific selection timeout/bind failure codes, with no raw errors or URLs.
Backend compatibility update is deployed/healthy; configuration preserved.
Live2.23.7 shows menus open/expand, followed by **selection-render-timeout**.
Source **2.23.8** activates the radio's verified containing label rather than
the hidden input. Official React Aria code supports the observed cancellation
mode; captured-markup browser regression RED no selection/GREEN one Furniture
selection passes, with two unit RED cases. All **567 extension tests**, Ruff,
syntax and focused self-review pass; backend1649 passes/one platform skip.
Owner requests TOP-LEVEL departments only, every available page, no children.
Source **2.23.13** replaces normal department clicks with destinations read from
the public __NEXT_DATA__ SearchSortFilterModule cat_id facet on the unsearched
root.35–36 current top-level name/ID records were observed; IDs are not guessed.
Exact URL scope and fresh server category ID/applied chip are required before
binding; this fixes the observed wrong-category association (Home pages labeled
Clothing in2.23.12). The actual Clothing5438 destination is live-verified with
matching chip/server identity; research tab closed. Normal department discovery
and full navigation now work through the existing background/focus guards,
without popover/native input. Legacy/native/brand tests retained; all **620
extension tests** pass, with syntax/Ruff/whitespace. No backend/auth changes.
Owner activated **2.23.13**; automatic live binding/pagination is now verified.
Department0668 scanned all25 available Home4044 pages, recorded pages25 and
coverageLimitedTargets1 with failures0, then automatically loaded Clothing5438
and advanced through pages2 and3 with the Clothing applied chip intact. Root
discovery enqueued33 current departments (34 targets including discovery).
Primary0603 completed its17-page catalogue while keywords0669 advanced through
Personal Care, Playpen and Repair to Shade; keyword failures stayed at the
pre-existing2. Live scrolling moved Home page13 to scrollY5118.889. One blue
Reviewer scans group80647340 contains exactly these three current scanner tabs.
No dropdown clicks, resets, reloads or settings edits were used for this check.
The main Chrome window was focused once for scrolling; no other window changed.
Rotation remains true; no temporary settings/window cleanup remains. Entire
33-department/65-keyword rounds were not awaited; the tested transition works.
Safe live receipts: automatic-22313-home-progress.json and
automatic-22313-handoff.json; screenshot automatic-home-22313.png. Completion
counter proof distinguishes the cap from the sanitized lastFailureCode, which
currently also maps coverage text to unexpected-error despite failures0.
No further owner reload, ready sequence or Options read-back is needed.
Receipt: [department-hierarchy-20261026.md](department-hierarchy-20261026.md).

## Current and older scanner groups (October 26)

Owner reloaded/refreshed **2.23.5**; fresh safe status confirms activation and
Chrome confirms reuse of the same blue **Reviewer scans** group (2083254464).
Home1530560551 and owned department1530560571 are there. Retired0569/0570
automatically moved to existing collapsed gray **Old reviewer tabs — safe to
close** group824976180, now ten older tabs. Exactly one blue current group;
no manual cleanup needed after this reload. No tabs closed. Keyword is between
rounds (enabled, partial, owned=false), so its retired tab is correctly gray.
This verifies grouping/reload behavior, not broader department scan health.
The fix prefers the first registered scanner's existing validated group over a
provisional cached group. New regression failed before the fix. All **558
extension tests**, Ruff, syntax and focused self-review pass. Grouping task
complete; no Options inspection needed.
Receipt: [scanner-groups-20261026.md](scanner-groups-20261026.md).

## Focus immediately before page changes (October 26)

Combined source **2.23.3** also renews the scanner's focus turn immediately
before page navigation, including full-load fallback, search transitions,
restart and recovery. Contending scanners defer; real input, stale roles/URLs,
verification and expired grants cancel old actions. Foreground rotation yields
to unrelated tabs/apps, preserving the existing background fallback. Department
controls retain their dedicated foreground guard. All **552 extension tests**,
Ruff and syntax checks pass. The real-browser fixture confirms ordered focus,
busy retry and stale-action rejection with simulated Chrome grants. Self-review
completed. Receipt: [navigation-focus-20261026.md](navigation-focus-20261026.md).
Owner reloaded/refreshed: safe status confirms 2.23.3 and rotation enabled.
Live browser currently has an unrelated tab selected, so rotation yields as
designed. Live Walmart transition order remains distinct from the passing local
browser proof; select a scanner in the blue group to permit foreground rotation.

## Global verification pause (October 26)

Implemented in **2.23.3**, retained in current combined source **2.23.4**: an immediate shared stop signal and worker
authorization gate for every scanner when any recognized Walmart check is
detected, including on ordinary Walmart pages. The durable pause no longer
expires into scanning; every tracked check must clear before automatic recovery.
Restart sweep cannot override an unresolved check. Primary and auxiliary
checkpoints survive; late roles and pending navigation cannot revive scanning.
All **557 extension tests** pass; the latest concurrent tab-workspace edit also
passes its **30 tests** separately. Local real-browser pause/watcher fixtures
pass against the integrated navigation source without warnings/errors. Full
verification and activation receipt:
[global-verification-pause-20261026.md](global-verification-pause-20261026.md).
Existing verification-assistant settings/input and concurrent department/focus
edits are preserved. Automatic challenge solving was not added or improved.
Safe live status confirms 2.23.3 is active, scanning with no current pause; a
live challenge/recovery cycle is unverified. Reload current combined 2.23.4 and
refresh all Walmart tabs to activate the newer group fixes. This supersedes the
pending 2.23.2 reload below. No live CAPTCHA input occurred.

## Page reading and parallel scan follow-up (October 26)

Owner activated **2.22.2**, confirmed through fresh local status. Home and
keyword scans advance; department discovery still reports `missing-panel`.
Current source **2.23.2** adds a smooth, varied downward pass on each delivered
results page, safe focus rotation and cancellation, and a bounded filter-panel
readiness fix. Owner activated 2.23.0: discovery now finds 35 departments, but a
further parent-card observer race cancels pending selection. The 2.23.1 fix and
two RED/GREEN regressions isolate pending selection from live updates. Owner
activated 2.23.1; remaining `missing-panel` failures led to foreground focus before
facet actions and strict recovery from detached filter roots in 2.23.2. All
536 extension tests, Ruff/syntax and independent review pass. Owner was asked to
reload 2.23.2 and leave Chrome in front for one minute to validate live behavior.
Validation/activation are tracked in
[page-scroll-20261026.md](page-scroll-20261026.md). Source awaits checks/reload;
do not claim live department coverage is verified or ask for Options read-backs.

## Local scanner settings and status (October 25)

Latest source is **2.22.2**, superseding the pending 2.22.1 reload. Real captured
Walmart filter markup reproduced a silent coverage bug: Show More becomes
Show Less before new options render, and the old helper returned 4/34 departments.
The helper now waits for actual option-count growth and reports stalled expansion
if none arrives. Unit RED/GREEN and real-browser delayed-render RED4/GREEN34
pass, with no missing labels or console errors. Independent review and all
**487 extension tests** pass; current backend's **1,641 Python tests** still pass
with one platform skip. Source 2.22.2 includes both this fix and safe failure
codes. The owner was asked to reload 2.22.2 and refresh; do not request 2.22.1.
Live department pagination remains unverified until the new source activates.

Current follow-up: owner cleared verification. Refreshing stale reviewer pages
reconnected their content scripts and cleared the shared pause through normal
readiness probes. Home and keyword scans advance in the automatic group; fresh
owned keyword lane resumed Pet and advanced to Appliance with 25 pages. Department
discovery failed again on a fresh owned tab (round4, three retries, zero pages).
Exact reason was hidden by the generic export. The 2.22.1 preparation added a strictly
allowlisted `lastFailureCode`; backend support is deployed and healthy, retaining
all settings/data/access. At that stage **485 extension** and **1,641 Python** tests passed
(one platform skip), independent review approved. Owner was asked to reload
2.22.1; the new 2.22.2 request above supersedes it. No Options
read-back or speculative selector/visibility changes. No agent CAPTCHA input.

Relay **2.22.0** is active; the owner confirmed refresh and the live report
confirms the new version and synchronized settings.
The tested backend is deployed and healthy, preserving environment, data,
loopback binding and container protections. `data/scanner-settings.json` now
enables departments, all 65 supplied keyword terms, grouping and rotation;
brands stay off. Existing automatic-verification enabled/native disabled
preferences are preserved. Direct status is updating in
`data/scanner-status.json`; do not ask the owner to inspect Options. Live scans
initially remained paused by home and inline verification checks. The department lane's
retained round is partial (one discovery target skipped after retries, zero
pages); keyword progress is 5/65 at Pet. The owner has been asked to clear the
visible check; the follow-up above records recovery and the remaining discovery issue.

All **483 extension tests** and **1,607 Python tests** pass (one platform skip),
along with lint/syntax/startup, browser Options preview and independent review.
New safe local APIs pass live GET/PUT/access checks. Restart IDs are durable and
verification-safe; known unsuitable Chrome input state waits without spending
attempts. The Options sender/status bug is fixed. No CAPTCHA input or budget
reset was performed. Full receipt and remaining live check:
[`scanner-control-20261025.md`](scanner-control-20261025.md).

## Scanner tab grouping, rotation and resume (October 25)

Historical activation: Relay **2.21.0** was implemented and the owner confirmed reload/refresh. Live
diagnostics confirm version 2.21.0, pause cleared and home scanning through page
eight. A blue automatic **Reviewer scans** group exists with home and keyword
tabs. Its unavailable department status led to the 2.22 backend migration above;
the text read-back request is superseded. Keep live validation open using the
backend status file after reload.

Grouping/rotation default on. Focus slots preserve existing waits, yield to
manual tabs/input and verification, and never focus an OS window. Primary and
auxiliary scans preserve compatible checkpoints across verification; bounded
auxiliary recovery resumes, resets, then skips. All **451 extension tests** pass,
along with real-browser 31-page concurrent coverage, visible scrolling, Options
save, syntax/lint and independent review. No live CAPTCHA input was performed.
Full evidence and the exact remaining check:
[`scanner-workspace-20261025.md`](scanner-workspace-20261025.md).

## Concurrent department and keyword coverage (October 25)

Relay **2.20.0** keeps the primary home scan and adds two independently leased,
durable department/keyword scans. Both are **enabled by default** at the owner's
request; brands stay off. Live research confirmed 35 root departments and Home's
3,932 advertised items behind a 25-page cap. Capped departments discover children;
unpartitionable caps and failed targets remain visible as partial coverage.

All **412 extension tests** and **1,478 Python tests** pass (one platform skip),
along with syntax, lint, source startup and independent review. Real Chrome and
IAB integration passes 31 expected pages, isolated failures, dedupe, checkpoint
recovery and shared pause; visible IAB scrolling also passes. **Live activation
was confirmed on 2.20.0 and then 2.21.0**; the newer entry above tracks remaining
live coverage validation. No manual Options setup is required. Existing workspace
work and live backend are preserved.
Full evidence, safeguards and activation: [`parallel-scans-20261025.md`](parallel-scans-20261025.md).

## Membership count removed from greetings (October 25)

Owner requested removal of the public **Paid members** count and the
**Unique members with Starter or Pro access • latest complete scan** footer.
Removed both from Starter, Pro and upgrade notification cards, and edited the
existing Pro greeting in place; Discord GET readback confirms both are gone
and the congratulations, activation text and member mention are preserved.
The running bot uses the updated template; the replacement is healthy with
its environment, data mount and security settings preserved. Internal health
counts remain available. All 1,478 Python tests pass with one platform skip;
lint, formatting and isolated packaged payload/startup checks pass. Evidence
and rollback: `data/discord_bridge/membership-count-removal-20261025/`.
Rollback image: `reviewer-notifier:before-membership-count-removal-20261025`.
This supersedes the public-count presentation recorded on October 23.

## Evening Discord audit false positive repaired (October 25)

The evening check found delivery blocked again since 12:47 PM while Relay
2.19.1 kept scanning normally. The cause was a neutral extra member overwrite
on `welcome` (allow and deny both zero), which exact comparison wrongly treated
as drift. Canonical comparison now ignores validated inert entries, retaining
strict target/bitfield/duplicate checks and rejection of real permission changes.

All **1,478 Python tests pass, one platform skip**, including 24 new regressions;
lint, packaged startup/comparison checks, read-only live audit and independent
review pass. Only the audited source file was layered over the running image;
the backend was replaced at 7:21 PM with data/env/access preserved. Actual Pro
messages resumed and were verified at 7:22:14 PM. No Chrome reload is needed.
The 7:23 PM follow-up confirms empty Pro/Starter due queues, zero failed jobs
and 159 delivered items; Free history continues catching up.
Receipt, evidence, live health and rollback:
[`discord-neutral-permission-recovery-20261025.md`](discord-neutral-permission-recovery-20261025.md).

## Discord feed recovery and scrolling repair (October 25)

Verified the concurrent Pro-request repair restored the full privacy audit and
actual Discord feed sends. The Pro and Starter due queues are empty; retained
Free history is draining under provider limits, with no failed jobs. An actual
Pro message was retrieved from Discord, and request health is now ready with a
connected Gateway. No privacy gate or duplicate guard was bypassed.

Relay **2.19.1** fixes overly broad numbered-page cooldowns, premature one-second
retries and scroll-only interruptions during native navigation. All **348
extension tests** pass after integration, alongside lint/syntax checks and the
visible real-browser scrolling/card-guard fixture. Independent review approved
the production delta and browser test. **Live activation verified after the
owner reloaded Relay 2.19.1 and refreshed Walmart.** The fresh report at 7:36:57 AM
Central shows pages 4–7 delivered, three native transitions without fallback or
false input interruption, and two new items. Those items were confirmed in the
actual Pro message at 7:37:04 AM; Pro/Starter due queues remain empty. Full evidence:
[`discord-delivery-recovery-20261025.md`](discord-delivery-recovery-20261025.md).

## Pro request command repair (October 25)

Fixed the generic command failure after a normal Discord reconnect: RESUMED now
restores readiness through the same identity/shutdown checks as READY. Commands,
controls, autocomplete and error replies enforce the exact request channel and
stay silent elsewhere. The missing Pro channel overwrite was restored; the full
privacy audit passes, with ordinary member posting still disabled. Saved Discord
command overrides deny everyone/all channels, allow Pro/item-requests only.

Independent review approves; **1,454 Python tests pass, one platform skip**, lint
and formatting pass. Isolated Linux reconnect/DM smoke checks pass. The narrow
repair image is deployed; live request health is ready, Gateway connected and
the DM sender running. Detailed receipt and live checks are in
[`discord-personal-requests-20261024.md`](discord-personal-requests-20261024.md).
Owner/admin ordinary chat bypass remains a Discord limitation. No role grant,
new bot privileges, synthetic requests, other-member DMs, commit or push.

## Pro personal item requests (October 24)

Owner approved adding Pro-only item requests after the investigation and chose
DM-only notifications. Source now includes `/request add/list/edit/delete/test-dm`,
private request controls, ten keyword/exact-product watches per member, durable
transactional matching and a DM outbox. Fresh Pro/ownership checks, canceled-work
recovery, duplicate protection and blocked-DM guidance are implemented. The new
request channel is optional during rollout and governed by the existing audit;
the outbound Gateway needs no public endpoint or message-content intent.

Independent review approves the code. All 1,424 Python tests pass (one intentional
platform skip), along with lint, scoped formatting, source startup and isolated
Linux Docker checks. The deployed image layers over the prior running service
and preserves pending server/diagnostic changes from other chats. Windows setup
state saves now retry transient replacement locks while retaining atomic writes.

Activated after owner sign-in/MFA. The audited Pro-only `#item-requests` channel,
guild command, pinned clickable guide and compact welcome benefit are live.
Health confirms a connected Gateway, running DM sender and request readiness;
existing feed delivery continues. Real `/request list` as owner without Pro
returns a private Pro-only refusal. No role was granted, no synthetic watches
were created and no other member received a test DM. The first positive real
Pro request/matched-item DM remains unobserved. The Chrome session
`🔔 Discord requests` is left on the guide. Local credentials, prices, billing
and data are preserved, with image/SQLite/env/state backups. No commit or push.

Exact source, checks, image tags, baseline and authorized activation steps:
[`discord-personal-requests-20261024.md`](discord-personal-requests-20261024.md).

## Windows verification fallback (October 23)

Relay **2.19.0** source now includes an optional Windows mouse fallback after a
matching safely completed Chrome failure. The current-user Native Messaging
host is installed and its actual launcher/Win32 probe-close protocol passed;
no real mouse input or CAPTCHA solve was sent. All **344 extension tests** and
**98 native Python tests** pass (one intentional platform-boundary skip), lint
and syntax pass, independent reviews approved. User-idle, exact window/target/DPR,
tab/focus cancellation, one-press leases, and an independent release watchdog
preserve existing retry budgets. Other chats' pending resume/pager changes remain.

Activation pending: owner reloads Relay **2.19.0**, opens fresh Options and enables
**Windows fallback**, granting optional Native Messaging/scripting. Automatic
verification must also be enabled. Chrome internal UI is unavailable to the
browser connector. Live input and HUMAN acceptance remain unverified; no 100%
unattended guarantee. Receipt: `tasks/native-verification-20261023.md`.

Earlier version activation notes below are historical; the combined pending
extension version is now **2.19.1**. No backend deployment, commit or push here.

## Category detection active (October 23)

Improved clothing vocabulary and pet-carrier-pocket collisions, with explicit
pet apparel/recovery handling and stronger product-purpose phrases for common
Other misses. Replaying 15,404 stored titles moves 906 Other items and five Pets
variants into Clothing; Other falls from 5,016 to 3,541 (rule comparison, includes
variants). All eleven existing Discord type channels were verified live; no new
category/channel is required. All 1,224 Python tests, lint, startup and packaged
projection/routing checks pass; independent review approved.

Activated only `discord_bridge/classification.py` as a layer over the running
image, preserving other chats' pending activation work. Notifier is healthy,
Discord sending enabled, and the live classifier matches tested source. New
observations use the fixes; historical messages/free snapshots were not rewritten.
No extension reload is needed for this classifier. Full receipt and rollback
image details: `tasks/category-detection-20261023.md`.

## Last-page and scan recovery (October 23)

Owner requested recovery at the last monitored page after a closed tab. Relay
2.18.0 now saves a durable `reviewerResume` checkpoint containing the committed
URL and acknowledged sweep progress. Replacement tabs, eligible passive backups,
Chrome restarts with empty session storage, and a supervisor-opened catalogue
homepage resume that checkpoint. Alphabet/category position and temporary
category fallback survive too. Extension reload repairs the same URL and adopts
legacy per-tab progress on the first upgrade. Manual restarts still start fresh;
verification, disabled-refresh, manual-browsing and duplicate-open guards remain.

All 331 current combined extension tests, JavaScript syntax, manifest parsing
and scoped whitespace checks pass. Isolated real Chromium tab closure and process
restart preserve page 7 and acknowledged pages 1–7; fixture network/profile only.
Self-reviewed; existing/concurrent extension and verifier edits preserved. Source
is ready for the owner to reload Reviewer Item Relay **2.18.0** once; no live
extension reload, Walmart input, backend deployment, commit or push here.
Receipt: `tasks/tab-resume-20261023.md`.

## Verification move-timeout retry follow-up (October 23)

Owner supplied live 2.17.2 Options: no press sent, mouse-move command timed out,
attempt 1/10. Latest mirrored challenge at 1:20:31 PM Central resumed at 1:21:35 after owner
manual recovery; backend snapshots omit the solver's last-result details.
Source now retries only a timed-out move with explicit no-press and confirmed
cleanup, using existing delays and all attempt budgets. Ambiguous/legacy errors
stay terminal. Five focused cases and all 328 extension tests pass; independent
review approved. Included in pending combined 2.18.0 source, with the resume-work
chat owning that version/release. No live input, native helper, permission change,
extension activation or backend deployment. Actual input reliability remains
unverified. Receipt: `tasks/verification-automation.md` (current follow-up).

## Variable pager scrolling (October 23)

The owner requested slower, slightly variable scrolling and some upward motion.
Combined Relay 2.17.2 now eases an offscreen pager into view over 600–900 ms,
varies the landing point within 45–60% of viewport height, and sometimes makes
a bounded 6–18 px settling correction. Fits the existing wait; no changed page
deadlines or item-delivery timing. Real input, verification, visibility/context
loss and navigation stop the animation; reduced motion is respected. Nine
focused tests, all 311 combined extension tests and the real local browser
fixture pass; independent review approved. Challenge-rate benefit is unmeasured.
The category-fallback chat has finished; its changes below are preserved in the
same source. Reload Relay 2.17.2 and refresh the reviewer tab once to activate.
No live browser input, extension reload or backend deployment from this slice.
Receipt: `tasks/browsing-behavior-20261023.md` (current variable-motion follow-up).

## Premature pagination fallback fix (October 23)

Relay 2.17.2 source fixes the fallback reported by the owner on a populated
681-item catalogue. Mirrored 2.17.1 diagnostics show page 6 loading at
12:42:54.845 PM Central and a high-value fallback starting just 460 ms later.
The regular-mode no-results branch bypassed the existing confirmation checks.
Expected product pages now get a 30-second render allowance and the bounded
reread/reload confirmations before category fallback. Recovered cards cancel
unnecessary reloads, and confirmed empty catalogue tails finish normally.
Fallbacks log `empty-results` or `blank-timeout`, the page/advertised total,
elapsed wait and confirmation count. The category list and heartbeat limits
cover all 62 unique terms, including Clothes, Chairs and Dog.

Four new regressions failed on the original trigger and pass with the fix.
All 311 combined extension tests, relevant Python server/diagnostic tests, lint,
JavaScript syntax, server import and the real local browser fixture pass.
Extension suite results are in `data/fallback-trigger-extension-tests-20261023.log`; original
safe event evidence is in `data/fallback-trigger-evidence-20261023.json`.
Self-reviewed; concurrent pager animation edits were preserved.

Activation remains pending: reload Reviewer Item Relay 2.17.2, refresh the
reviewer tab once and restart the sweep to return from the current category
round. Restart/rebuild the local notifier from the updated source so diagnostic
mirroring accepts the new trigger metadata and the 6,200-page maximum.
No production reload, deployment or live verification input was performed here.

## Verification input-error follow-up (October 23)

Combined Relay 2.17.1 source is ready. Active 2.17.0 stopped at attempt 2 after
uncertain input/cleanup; the old 2.01-second value did not prove a press occurred.
Fixed bounded input acknowledgement timing and misleading hold accounting;
Options now reports the failure stage and offers an explicit retry preserving
all attempt limits. The existing live terminal record was left intact. Includes
the concurrent content-script reload cleanup below. All 302 extension tests,
syntax/manifest checks and local Options preview pass; independent review
approved. Owner must reload 2.17.1, refresh the reviewer page once, reopen Options
and use **Retry current check**. No live input, activation or backend rebuild.
Receipt: `tasks/verification-automation.md` (current input-error follow-up).

## Content-script reload cleanup (October 23)

Fixed stale page scripts throwing `Extension context invalidated` after a reload.
Old observers/listeners/timers/navigation stop, late ACKs cannot advance progress,
and ordinary delivery/storage retries remain intact. Nine lifecycle regressions,
221 content/runtime/collection/timing tests and a real local browser fixture pass.
The affected already-invalid Walmart page still needs one refresh. The
bot-detection chat owns combined 2.17.1/versioning while separately fixing
verification-input diagnostics. Receipt: `tasks/context-invalidation-20261023.md`.

## Combined extension 2.17.0 verification retries (October 23)

Owner requested up to ten verification attempts with about 10–20 seconds between
completed failures and more adaptive hold timing. Implemented and locally
verified in 2.17.0, preserving the concurrent pagination/scrolling slice. Counts
and deadlines persist; completed old failures can continue; uncertain input or
cleanup stops. Holds use readable progress/completion and a 30-second maximum.
Owner extension reload is still required; no live CAPTCHA input was performed.
Current acceptance, tests and activation receipt: `tasks/verification-automation.md`.

## Pagination behavior research (October 23)

The bot-detection chat finished its scoped source changes (initially 2.16.2,
included in the other chat's combined 2.17.0 source): neutral
`affinityOverride=default` destination normalization, one purposeful offscreen
pager scroll during the existing wait, and wheel/touch input guards. Six new
regressions and the real browser fixture pass; independent review approved.
Implementation edits by that chat are finished. Preserve these changes when
finishing the concurrent verification-retry work. The owner selected letting
that other chat finish before combined activation; no reload/deployment was
performed here. The combined suite passed 282 tests after the retry work landed;
the other chat's subsequent 2.17.0 stable-version receipt passes 283 tests.
That chat delivered its final release handoff; source is ready for the owner's
extension reload. Neither chat reloaded it or sent live verification input.
Research, ownership and evidence:
[`browsing-behavior-20261023.md`](browsing-behavior-20261023.md).

## Operational log retention (October 23)

- Owner selected a 60-day rolling history target with a disk-space backstop.
  Delivery/backend/recovery logs now rotate daily or by segment size, gzip old
  segments, and trim oldest first at 60 days or their space/count limits.
  Caps are 32/16/2 MiB respectively; Docker's separate recent console cache
  stays at 30 MiB. Heavy traffic can shorten retained history. Browser diagnostic
  snapshots remain capped at seven days/300 events. Durable delivery/dedupe and
  membership databases, manual exports and database backups are outside cleanup.
- Activated in the existing notifier container and restarted only this project's
  recovery task. Live health confirms Discord sending still enabled, 60-day
  retention, compressed archives, a 32-MiB delivery cap and zero dropped events.
  New host log lock and replacement helper process verified. Source comparison
  before build showed only the logging implementation differed from the live
  image; unrelated workspace edits were preserved.
- Verification: full Python regression suite passed; final focused retention
  and monitoring checks passed (27 tests), repository-wide Ruff passed, server
  help/startup passed, Docker build/health passed, and isolated compression and
  60-day expiry passed inside the running Linux container. Windows tests cover
  restart, age/space/count bounds, short writes, failed compression, locked
  originals, thread/process contention and backend logging integration.
- Viewing/retention details: [delivery operations](discord-delivery-monitoring.md#watch-the-logs).

## Authorized scope and accepted policy

Owner requested independent Discord preparation while app/extension development
continues elsewhere. Accepted prices are exactly $5 Starter / $20 Pro. Current
policy is Starter positive values below $40, delayed 300 seconds from bridge
intake; Pro all observed values with no intentional delay. No third tier is
approved. A 20-member Pro pilot is a recommendation, not an active cap.
Owner explicitly selected every item for free members, delayed 24 hours from
first detection, rather than selected daily recaps. Free alerts are historical.
On October 23, owner accepted additional Free queue delay to prioritize Pro and
expensive items. Paid messages now pack up to ten due items, with no fill timer;
Free remains capped at five and waits while unexpired paid work is due. Exact
Starter/Free release clocks and full privacy audits remain. Current implementation,
verification and activation receipts are in `tasks/scan-freshness-20261022.md`
and `tasks/discord-burst-delivery.md`; earlier five-item records below are historical.
The October 23 follow-up is deployed: frozen retry lookups now use a covering
batch index and new Discord bodies have a conservative 10 KiB ceiling. The
owner-approved recovery confirmed 49 destinations sent; 90 had reached their
original expiry and were not revived. Latest health has zero due/failed Discord
destinations. Extension 2.15.1 is active but waiting on a Walmart challenge; the
approved operator-helper attempt did not clear it. Details/receipts are in the
scan-freshness handoff above.
Preserve all unrelated/concurrent collector, extension and deployment edits.

Owner subsequently authorized wiring the extension/backend to Discord instead
of Telegram and removing the extension's old personal-filter options. This
supersedes the earlier preparation-only boundary below. The running local
`reviewer-item-notifier` container now selects `NOTIFY_PROVIDER=discord` with
`DISCORD_LIVE=true`; Telegram credentials remain preserved but unused.

## Current collector integration (October 21)

### Membership celebrations (October 23)

- Owner requested colorful congratulations in #greeting when someone buys a
  membership, with a visible subscriber count. Implemented a separate, opt-in
  membership worker: teal Starter cards, gold Pro cards and Pro upgrade cards,
  with exactly one allowed user mention. Counts are unique human paid-role
  holders, with dual-role members counted once as Pro. Discord access is the
  source; this does not verify billing receipts, and manual grants also qualify.
- Complete paginated member scans quietly baseline existing members; durable
  snapshots/outbox survive restarts. Renewals and downgrades stay quiet. Same-tier
  role churn has a 24h cooldown. Count/removal changes require a complete scan;
  partial failures never reset counts. One 1,000-member page per 30s cycle shares
  transport and yields to paid item delivery. Ambiguous sends reconcile or await
  review, without blind repeated pings. No payment or customer data is read.
- Owner signed in to the existing Developer Portal; Server Members Intent saved
  for Reviewer Alerts Bridge. Live member-list read succeeds with 1 unique paid
  human (Pro, holding both paid roles), and the privacy audit passes. No guild
  permissions, prices or role assignments changed.
- Files: discord_bridge/membership.py, welcome.py (shared collection/payload
  hooks), api.py (validated member pagination), src/notifiers/discord.py,
  notifier.example.env, discord_bridge/README.md and focused tests.
- Verified: all 1,103 Python tests pass, repository Ruff, scoped formatting,
  startup help and diff whitespace checks pass. Self-review covers count
  deduplication, quiet baseline, retries, priority, privacy and persistence.
  Docker source comparison found only this feature's files differed from live.
- Deployed with DISCORD_MEMBERSHIP_ENABLED=true. Container healthy and still
  bound to 127.0.0.1:8787; membership readiness true, paid_members=1, no pending,
  failed or review announcements. Quiet baseline emitted no historical posts.
  Isolated Linux outbox/card smoke test also passes using only /tmp and a fake
  provider; no synthetic records entered live ledgers. One startup transport
  cooldown cleared on the next scheduled cycle. The next real activation still
  needs to demonstrate the provider-to-public-card path; no purchase or role
  change was made to simulate one. Receipt:
  data/discord_bridge/membership-celebrations-20261023.json. Rollback image retained
  as reviewer-notifier:before-membership-20261023. No changes committed/pushed.

### Owner member walkthrough and launch outreach (October 23)

- Owner reports passing ordinary-member privacy, notification, other-account
  Walmart links, delivery, and billing navigation tests. Access removal was force
  tested by terminating the membership; this verifies forced removal, not a
  naturally elapsed paid period or refund event. Earlier generic pending-test
  paragraphs below predate this owner report.
- Owner now wants an interest/feedback post, without beta branding. No Reddit or
  Facebook post/message has been sent. Current r/RecognizedReviewer rule 1 says
  advertising and posts about extensions/apps may be removed and result in a ban;
  prepare a moderator-permission message and draft launch copy before publishing.
- Complete local Git history has one root, d9ff42bc105ccfac75a7ed3f4eef17b3d0058bcc,
  dated August 22, 2026 (Add Walmart reviewer item notifier on Azure Functions).
  This grounds project age of about two months, not continuous reliable service
  uptime or the date of the later shared Discord launch. Owner reports roughly
  $4,400 overall RR item value and repeated finds around $100; these are personal
  results, not a measured attribution/guarantee for subscribers.

### Paid-member onboarding repair (October 23)

- Fixed highest-tier display after a real member retained Starter through its paid
  period while purchasing Pro. Both roles were correctly assigned; Starter was
  incorrectly above Pro. Owner UI now orders Pro above Starter, below both managed
  bot roles, and enables separate member-list grouping for both paid tiers. Role
  IDs, permissions, colors, mentionability, and member assignments are unchanged.
  UI confirms notHuss appears gold under Pro with Pro first in profile role pills.
  Live role snapshot and existing security audit pass; receipts/screenshots are in
  ignored data/discord_bridge/tier-display-*-20261023.json and
  pro-member-display-20261023.png. Preserve this role ordering during future setup.
  Purchase order does not control display. Whop still owns access expiry; no billing
  or cancellation actions were performed as part of this cosmetic fix.

- Real Starter purchase succeeded. The initial no-Discord-connected failure is
  resolved: the test customer connected Discord, canceled Starter renewal while
  retaining its paid period, and purchased Pro. Live Discord readback confirms both
  tier roles assigned without a manual role grant; highest-tier display was fixed
  above. Actual channel access should still be checked from the ordinary member
  client. End-of-period role removal, refunds and failed-payment behavior remain
  unverified; a canceled renewal alone does not prove expiry handling.
- Added Activate Discord access, Manage subscription (generic Orders route), and
  Payment methods buttons to the existing bot-owned welcome/how-to-use posts.
  Welcome remains pinned; exact button/no-mention readback and screenshot saved in
  ignored data/discord_bridge/member-buttons-20261023.png. No manual role grants.
- Saved and reopened both product settings to verify After checkout redirects
  to their corresponding gated Discord experiences. Native setValue did not persist
  the initial URLs; Playwright fill plus form save did. Final reopened values pass.
  Prices, app gating and billing terms unchanged. A real post-payment redirect and
  member billing/cancellation/access lifecycle still need testing on the phone.
- Generic payment-methods and Orders pages verified via Whop UI. Legacy membership
  billing-manage route returned a Whop error; never publish member-specific or
  signed update-payment links. Customer PII/license keys stay out of public cards.

### Clear subscriptions and separate greetings (October 22, latest)

- Supersedes earlier hidden/waitlist-only and bloated-welcome copy below. Owner
  explicitly approved enabling both existing monthly Whop plans. Starter is $5,
  Pro $20; both are Visible / Buy now / Unlimited stock, with no trial or initial
  fee. Not listed on Discover. No beta/member-cap/referral program was added.
- #welcome now has one tier/payment card with direct checkout buttons. The
  hosted payment forms were opened and verified at $5/month and $20/month;
  no purchase, payment-data entry or acceptance of buyer terms was performed.
  Direct URLs: https://whop.com/checkout/plan_k2rVgU36MH7pE and
  https://whop.com/checkout/plan_Ta8OgmuWRX49n. The product page's disabled Get
  access button is bypassed. No session/account parameters are in shared links.
- Existing #how-it-works was renamed #how-to-use and moved to START HERE with
  synced read-only permissions. Its stable internal key and history are retained.
  New #greeting ID 1562885299758047312 holds automatic personal greetings;
  instructions/pricing stay out of the greeting stream.
- Greeting worker uses private native join events in #audit (system flags14,
  joins enabled/sticker replies disabled), exact single-user allowed_mentions,
  durable activation cursor/pagination/outbox, 24h rejoin cooldown and bounded
  retention. It shares transport limits and defers to due paid work. Ambiguous
  sends reconcile or await manual review without blind repeated pings.
- Server default remains mentions-only. Discord owners cannot force another
  member's personal channel mute or override their push/Highlights preferences.
  Live private-source event emission and actual new-member ping still need a
  real account walkthrough. Historical members are intentionally not greeted.
- Starter teal, Pro gold; Founder purple for owner and hoisted blue Bots group
  for both bots. Cosmetic and paid roles have zero base permissions and are
  unmentionable. Bot avatar matches the existing server logo.
- Live ACL audit passes: 38 resources x4 member combinations, zero violations.
  Independent greeting review closed activation/pagination gaps. Billing role
  lifecycle, other-account links, mobile notifications and delayed releases
  remain human launch checks. See tasks/discord-onboarding-draft.md for copy.
- Final live readback confirms owner Founder assignment, both Bots assignments,
  welcome pinned, system flags14 and mentions-only. No new privilege grants.
- Deployment exposed a measured health bottleneck: full delivery_metrics on the
  Docker bind mount took 3.465–3.702s and failed the unchanged 3s HTTP health
  budget. Repeated welcome priority checks also called those full analytics.
  Added a dedicated indexed `has_due_paid` predicate preserving due/leased/retry
  semantics, plus three additive covering/partial metrics indexes. No cache,
  skipped checks, timeout increase, ledger reset or schema-version change.
  Same-container isolated snapshot metrics became 0.052–0.057s. A bounded
  10-record write sample measured median 79→109ms per single-item transaction;
  this small fsync sample is illustrative, not a throughput guarantee.
- Reviewed deployment now returns full /health in 0.190s and Docker reports
  healthy, still 127.0.0.1:8787 only. Historical/queued work is preserved; live
  bursts can still encounter Discord cooldowns and delivery latency. Greeting
  polling intentionally defers while due paid jobs exist. Do not promise instant
  delivery or a completed member walkthrough from these checks.
- Final current-tree Python suite passes after the performance fix; Ruff check,
  scoped format check and diff whitespace check pass. Additive-index and paid
  priority semantics received independent review. No changes committed/pushed.
  Receipt: data/discord_bridge/onboarding-layout-verified-20261022.json;
  screenshot: data/discord_bridge/welcome-subscribe-pinned-20261022.png.
- Owner requested shorter #how-to-use copy. Edited the existing bot post from
  1,723 to 682 characters: pick a feed, enable/stop notifications, open an item.
  Replaced the long access/claiming paragraph with a short own-account/fallback
  instruction; plans and help link to #welcome/#general. Readback and live UI
  verified, no user/role/everyone pings. Source copy updated in onboarding draft.
- Owner requested a pinned membership promotion in the free feed and a channel
  icon accent. Added one gold upgrade card with the existing $5/$20 direct
  checkout buttons and Compare plans. Display names now 🆓-free-24h-alerts and
  💳-welcome; stable IDs/route keys and the exact ACLs remain the same. Owner UI
  handled names/pinning, bot retained minimal posting permissions. Updated and
  deployed display manifest; focused provisioning tests pass. Source copy and
  receipts in data/discord_bridge/free-membership-card-20261022.json.

### Community chat restored and security reviewed (October 22)

- Owner clarified that #general and #feature-requests should remain chat areas.
  This supersedes the all-read-only policy below. COMMUNITY now grants only
  Send Messages plus reader access to ordinary members; both chats retain
  10-second slowmode. All feeds, updates and operations remain read-only.
- Live audit found #free-24h-alerts under COMMUNITY. Moved it back to START HERE
  before opening chat, so the free feed remains read-only. No history deleted.
- Native owner-managed AutoMod now blocks ordinary member URLs, custom emoji
  markup and role mentions; mention spam (>5 unique mentions) and suspected
  spam blocking are enabled. No member/paid-role exemptions or forwarded
  flagged-content alerts were added. Bot links and thumbnails remain usable.
- Existing zero member-role bases and exact ACL audits prevent member uploads,
  embeds, thread/app/poll/invite/management actions. Audit now requires >=10s
  chat slowmode and rejects mentionable ordinary/interest roles. Independent
  scoped review approved after closing the mentionable-role gap.
- Native safety setup confirms moderator 2FA on, Medium verification, filtering
  sensitive media from all members and private server profile. Live permission
  readback: 37 resources x 4 member combinations, zero violations; audit passes.
  Receipt: data/discord_bridge/community-security-verified-20261022.json.
- Discord-crafted stickers, standard emoji, profiles and personal DMs cannot
  be prohibited through these channel upload permissions. Do not promise
  absolute image-free chat. AutoMod is not a guarantee against obfuscated text;
  runtime bot intentionally lacks Manage Guild and cannot inspect rule drift.
  Native AutoMod proof: data/discord_bridge/automod-enabled-20261022.png.
- Membership join/mobile/upgrade/cancel/refund/expiry tests remain required
  before paid launch. Seller setup stays with the owner. Referrals remain
  disabled/deferred; no new bot privileges or billing integration added.
- See tasks/discord-security-review-20261022.md for controls and evidence.
- Final verification: all 978 Python tests pass, Ruff and scoped formatting pass,
  final live audit passes, and rebuilt service is healthy on 127.0.0.1 only.
  No due/throttled/failed work at health readback; pending 6,672 jobs were future
  24-hour Free releases. Monitoring reports no write errors/dropped events.
  One existing concurrency test was made deterministic with a bounded two-route
  barrier; serialized dispatch was proven to fail and production dispatch was
  unchanged. Earlier transient Windows filesystem/socket failures did not recur
  in the final full run. Nineteen Rust-regex examples pass for native patterns.

### All member channels read-only; referral decision (October 22)

- Owner required member text, images/media and links blocked everywhere in the
  server. COMMUNITY's prior writing exception was removed in layout.py; its
  category Send Messages allow was changed to deny through the owner's Discord
  UI. Synced #general and #feature-requests inherited it. All other managed
  categories/channels already used the read-only member policy. No channel,
  history or role was deleted and no new bot management grant was introduced.
- Existing zero @everyone/Starter/Pro bases, reader-only additional roles,
  exact channel ACL comparison and strict role audit block uploads, thread
  replies/creation, apps, polls and reactions for normal members. Bot retains
  View/Send/Embed/Read History, preserving product thumbnails without Attach.
  The server owner bypass is unavoidable. This policy covers server channels,
  not personal DMs. Walmart-only text filtering is unnecessary while all member
  posting is disabled; ordinary members cannot post Walmart links either.
- Live permission readback verifies all 37 managed resources for Free, Starter,
  Pro and combined paid roles: zero prohibited actions in every audience.
  Receipt: data/discord_bridge/read-only-verified-20261022.json. One inventory
  GET was throttled; the read-only retry obeyed Retry-After and succeeded. No
  alert resend or permission bypass was used. Viewed saved screenshot:
  data/discord_bridge/member-posting-denied-20261022.png.
- 959 Python tests pass, including 57 permission tests. One initial focused run
  hit transient Windows os.replace WinError5 in test state persistence; rerun
  and full suite pass unchanged. Ruff/scoped formatting/whitespace checks pass.
  Independent scoped security review has no required findings. Actual new
  privacy audit passes; rebuilt/replaced existing Docker service is healthy,
  loopback-only, live-enabled and reports no privacy block or log-write errors.
- Referral rewards are NOT implemented/active. Owner accepted a one-time 30-day
  Pro reward per referrer after four qualifying distinct new paid customers,
  14-day qualification hold and refunded purchases excluded. Owner then asked
  whether to defer the idea. Recommendation: leave it disabled through beta and
  revisit after paid use/renewals; no API credential, reward product/role, cash
  affiliate or automatic reward grant has been created.
- Official current Whop docs support checkout-metadata attribution and payment
  polling. Paid Pro can be extended; recipient-bound 30-day reward invites are
  experimental/account-gated. Enabling automation still needs a scoped company
  API connection, secure reconciliation and real overlapping-access/expiry
  tests. Discord invite counts alone do not prove qualifying purchases. See
  tasks/discord-capacity-plan.md for the accepted terms and current boundary.

### Value priority and persistent operational logs (October 21)

- Owner requested expensive Pro work before cheap items/other tiers and a check
  for current throttles or unsent alerts. Priority slots now choose Pro, then
  descending observed value, with freshness/age and Pro all-items tie breaks.
  Full Pro bundles reserve four priority positions and one oldest same-channel
  catch-up position. Fair-route service, immutable attempted bundles, cooldowns,
  original tier clocks and expiry cohorts remain. Near-expiry work can still
  expire under overload; catch-up is not an every-item delivery guarantee.
- Private rotating JSONL dispatch/intake/error events and periodic queue summaries
  live at data/discord_bridge/delivery.jsonl (5 MiB +five backups). Health adds
  Pro/Starter/free future/due/retry/throttle/lease counts, terminal reasons and
  recent confirmed post-release p50/p95/max latency, plus log-write health.
  Fields are allowlisted; no product page/provider bodies or credentials.
  Log failure cannot trigger another provider write. Runbook/live-view commands:
  tasks/discord-delivery-monitoring.md. No new external monitoring service,
  public message or scheduled Codex automation was created.
- Baseline live check: zero active cooldowns, zero due work, zero failed jobs.
  All 2367 pending records were future free releases. 2100 old destination jobs
  across 1371 items were expired, plus 64 routing skips. These historical
  outcomes predate this follow-up and were preserved. Receipt:
  data/discord_bridge/priority-status-before.json.
- Rerun of the offline 500+50 model preserves 75.221% fewer paid posts and
  5.614x faster initial Pro drain. Value priority changes fresh latency: normal
  p95 22.4s/worst47.4s; fault/restart p95 96.5s/worst121.9s. This supersedes the
  earlier fresh-latency figures below. All invariant assertions pass; model
  assumptions remain explicit. Receipt: data/discord_bridge/priority-benchmark.json.
- Verified: all 957 Python tests pass, repository Ruff passes, nine scoped files
  pass formatting, server --help starts and whitespace checks pass. Independent
  final review has no required findings. Actual read-only Discord privacy audit
  passes. Same local image rebuilt/replaced; container healthy, loopback-only
  8787 preserved, live sending and new metrics/logging confirmed. Schema stays 4.
  Receipt: data/discord_bridge/priority-status-after.json. Log-write errors and
  dropped events are both zero. Two actual idle queue summaries were observed
  63.152s apart; receipt: data/discord_bridge/priority-monitoring-verified.json.
  No forced test alerts or public messages sent.

### Burst sender upgraded and deployed (October 21)

- Owner requested a significant delivery improvement before advertising instant
  paid alerts. Live sender now groups up to five ready items per channel, with
  photos, review-linked titles and numbered review/product buttons. Singles do
  not wait for batching. Four channels can send concurrently; a route throttle
  does not stop healthy channels. Reusable HTTPS connections, shared request
  pacing, live Discord headers and persistent cooldowns govern traffic.
- Intake wakes the worker immediately. Fresh paid work has priority plus a fair
  paid-route slot and bounded due-free service. Starter stays below $40 at 300s;
  free stays every captured item at 86400s. Existing expiry/privacy rules remain.
- Schema 4 adds immutable dispatch envelopes and atomic batch settlement.
  Uncertain old single sends keep their nonce; invalidated batches never revive
  under a new envelope. Full privacy audit runs before every bounded wave, with
  cancellation checks and total HTTP request deadlines. Long ambiguous failures
  still cannot guarantee exactly-once delivery beyond Discord's nonce window.
- 934 Python tests pass; repository Ruff and scoped formatting pass. Independent
  queue/dispatcher review has no remaining required findings. Transport tests
  cover deadline cancellation and request reuse. Actual read-only privacy audit
  passes through the new transport.
- Existing local Docker service rebuilt/replaced and healthy on loopback 8787;
  authenticated health confirms schema 4, live Discord, no block/failure and no
  currently due work. The pre-upgrade queue already had zero due paid jobs:
  do not attribute clearing its earlier backlog to this deployment. Its 2100
  expired jobs were preserved, not replayed. Consistent pre-migration backup:
  data/discord_bridge/collector-pre-burst-1792617562.sqlite3.
- One clearly labeled five-item format preview was sent only to private
  #collector-health. Readback confirms five photos, five cards and ten buttons;
  reviewed screenshot: data/discord_bridge/burst-preview-20261021.jpg. No load
  test flooded live feeds. Details/results: tasks/discord-burst-delivery.md.
- Offline actual-dispatcher model (500 initial +50 arriving items) cut paid posts
  1925 to 477 (75.221%) and initial Pro drain 640.6s to 114.1s (5.614x). Fresh
  destination p95 was 3.6s, or 7.3s with injected route/global throttles and an
  accept-before-ack restart; that fault run's worst was 51.1s. All 2480 intended
  destinations sent with no early release, expiry violation, duplicate item or
  expired revival. Receipt: data/discord_bridge/burst-benchmark.json. HTTP limits,
  latency and nonce cache are assumptions; disposable fsync is off. These are
  scheduling-model results, not observed live throughput or a delivery promise.
- This supersedes the earlier unresolved sender-capacity note below. Real live
  burst latency, ordinary member/mobile delivery, billing lifecycle and a second
  Walmart account still require pilot verification before a paid delivery SLA.

### Simplified member welcome and manual announcements (October 21)

- Owner requested less public onboarding detail and an easy place to post issues,
  resolutions and general updates. START HERE now has only #welcome, #updates,
  and #free-24h-alerts. #how-it-works and #plans-and-help were moved into PRIVATE
  OPERATIONS with synced operator-only permissions, preserving all messages.
- Existing #feed-status was renamed #updates; its internal key/ID stay stable.
  Everyone can read, regular members cannot post, and the server owner can type
  announcements directly. No new role, bot setup grant or public exposure added.
- Existing welcome post now has one compact text comparison chart of the three
  accepted plans, just notification/claiming basics, and Updates / Ask for help
  buttons. The chart was shortened to fit the actual narrow in-app view. Old
  links to detailed member guides were removed. The Updates shortcut was clicked
  and verified to open #updates, where the owner's composer is available.
- Existing status post now gives a brief getting-ready notice about possible
  alert delays, with no queue/developer details. Paid memberships remain closed.
  Manual issue/follow-up/resolved/general templates are in
  tasks/discord-updates-templates.md; these templates have not been posted.
- Regression reproduced the old five-channel public layout before the change;
  110 affected layout/privacy/CLI/runtime/collector tests pass. Repository Ruff
  and scoped formatting pass. Fresh live privacy audit has zero findings and
  verifies guide privacy for all normal paid/free audiences. Same container was
  rebuilt/replaced; health shows delivery resumed, 20 sent in its checked batch,
  zero failures/retries and no privacy block. Existing queue/timing retained.
- Copy/readback receipt: simple-onboarding-copy.json. Fresh viewed screenshot:
  simple-tier-chart-20261021.jpg. The prior onboarding audit describes the earlier
  layout and is historical; manual notification settings and untested real-member
  billing lifecycle limitations still apply. The later burst upgrade and its
  remaining live-pilot limits are recorded above.

### Compact product photos and two-button alerts (October 21)

- Owner requested product photos, removal of Reviewer catalog, and an honest
  rate-limit check. Backend projection now preserves an optional public Walmart
  product-photo URL, validates exact HTTPS CDN host/asr-or-seo image path and
  raster extension, strips query/fragment data and omits unsafe/missing evidence
  without blocking the item. Discord receives a compact embed thumbnail in the
  existing message; no backend download or extra upload/send is added.
- Future alerts have only Open review item and View product. Existing sent
  alerts were not mass-edited. The current live notification guide was updated
  to remove the obsolete catalog fallback. Receipt: thumbnail-guide-update.json.
- Original free snapshots preserve their original photo across later item
  updates and restart. Old queue payloads without an image still render safely;
  no database migration, timing policy or rate-limit behavior was changed.
- Focused regressions first reproduced missing thumbnail and extra button;
  focused 52 passed. Full Python suite: 883 passed in 81.15s. Repository Ruff,
  scoped formatting and whitespace checks pass. Self-review found no required
  correctness/security/performance issue in this bounded change.
- Rebuilt and replaced the same local Docker service. Healthy readback preserves
  loopback-only 8787 and the existing bound data/queue. Extension did not change
  and needs no reload. The live queue has received 80 image-bearing observations.
- One clearly labeled private #collector-health format preview was sent using
  a real same-product image read from the owner's Walmart page. Discord API
  readback verifies its thumbnail and exactly two buttons; browser screenshot
  visually verifies the product photo. Receipt: thumbnail-preview-verified.json;
  screenshot: thumbnail-preview-20261021.jpg. No private account/quota fields sent.
- Rate limits are real and handled with Discord headers/Retry-After and durable
  retries. Subscriber count does not multiply shared-channel sends. The live
  bulk sweep exceeds current delivery throughput: one sample measured 317 posts
  in five minutes, about 3,700 paid destination jobs due. A later checkpoint had
  3,294 due paid jobs and 572 expired jobs (zero failed). Paid jobs expire after
  30 minutes by current policy. Do not promise instant or complete burst delivery;
  burst handling remains a concrete pre-launch problem, not solved by photos.
  Current checkpoint: thumbnail-deployed-health.json. No production stress test,
  queue purge, backdating, rate-limit bypass or silent policy change was made.

### Member onboarding review (October 21)

- Audited the live server again: privacy audit passes with no findings and
  server notifications default to mentions-only. Product posts disallow mentions.
- Whop Starter preview shows only its own Discord app entry. Owner/Admin sees
  both tier app instances. Actual new-member joining and billing lifecycle remain
  untested; preview is not proof of real role assignment/removal.
- Updated the existing #welcome and #how-it-works posts with tier-specific
  notification guidance. Welcome has Notification guide and Plans and access
  buttons; API readback verified both posts and the guide button was clicked
  successfully in the browser. Receipt: onboarding-notification-guide.json.
- Pro can opt into category OR value channels using Discord's channel notification
  settings. Overlapping feeds can produce duplicate alerts; selections do not
  combine into an AND filter. Starter remains one combined under-$40 feed, with
  no category/value filters inside it. Free remains one 24-hour historical feed.
- This is documented manual setup, not a guided preference wizard. No new member
  notification roles, interaction handler, personal DMs or permission grants were
  added. Mobile push and personal notification settings still need device tests.
- Whop's optional app-name edit returned a generic save error; it was canceled.
  Both app entries retain the name Discord, with the existing tier gating intact.
- Before selling: owner completes seller verification and payout method; test
  ordinary Free/Starter/Pro accounts and membership access transitions, real
  mobile notifications and second-account Walmart links; settle ongoing hosting
  and backlog monitoring; verify program rules for the paid offering; run a small
  beta before publishing the currently hidden, waitlist-only products.
- Detailed findings and fresh screenshots: tasks/discord-onboarding-audit-2026-10-21.md.

- Extension 2.10.0 is loaded: owner confirmed reloading it and refreshing Walmart
  after the final card-boundary fix. Personal price/keyword/rule-save controls
  and related API traffic were removed. Scan mode, timing, scanner restart,
  status, private test alert and diagnostics remain; connection settings are
  collapsed under Advanced connection.
- Existing `/ingest` on loopback 8787 now routes Discord observations before
  personal filters and SeenStore dedupe. It acknowledges durable queue intake
  (`queued`) separately from background delivery (`notified` stays zero during
  intake). No old seen.json history was imported.
- New adapter: src/notifiers/discord.py. Queue is the separate, persistent
  data/discord_bridge/collector.sqlite3; existing channel mapping/token are reused.
  Native and Docker paths use the same package. No new port, token, permission
  grant, dependency, public invite or billing product was created.
- Product classifier: discord_bridge/classification.py, title evidence first,
  recognized card-local department fallback, query-only tagging forbidden.
  Color/style prefixes on variant titles cannot decide the product category.
  Unrecognized products go to `other`. The vocabulary was checked against a
  bounded live public-title sample; classification is heuristic, not guaranteed.
- Extension sends badge separately, card-local category hints, available boolean
  and sanitized optional image evidence. Public posts include optional product
  thumbnails; quota, raw data and search strings are omitted. Unavailable observations go only to the
  delayed free archive. Paid items use one category and one known-value band.
- Card extraction rejects neighboring-product ancestors. A primary can share
  evidence with its positively identified variant links, but variant IDs do
  not borrow the primary's price/stock. Mixed unmarked or wrong-title neighbors
  are rejected. Unknown values remain unknown rather than copying a neighbor.
- Queue schema 3 adds persistent seed suppression; previous delay clocks,
  message IDs and free snapshots survive migrations/restarts. SEED_MODE disables
  sending, historical rescan publication is refused, and diagnostics use only
  private #collector-health. Existing privacy audit and 429 handling are retained.
- Actual Docker build and replacement passed. Final live health: configured
  DiscordNotifier, sending true, no privacy block or failed jobs, 1,160
  destination messages sent at the saved checkpoint. Backend diagnostics confirm
  extension version 2.10.0 and a fresh relay three seconds earlier. Receipt:
  final-collector-health.json. Pending includes initial catalog catch-up and
  future Starter/free jobs. Initial bulk scans may add queue delay.
- Real API readback verified the bot author, matching product/value content and
  three Walmart buttons in Pro-all, category and value channels for one captured
  item. Receipt: collector-live-verification.json. These links still require a
  second eligible Walmart account check; no auto-claim.
- Existing #feed-status post now says collector connected and memberships closed;
  readback receipt feed-status-live.json. Free topic's obsolete setup-only notice
  was removed through owner UI. Whop products remain hidden/waitlist-only.
- Checks: full Python suite 847 passed in 83.37s; after 15 final classifier cases,
  affected classifier/collector/free/runtime/provision checks 208 passed in 15.45s.
  Combined extension suites 126 passed, no skips. Repository Ruff and scoped
  formatting/whitespace checks pass; server --help starts, final Docker health
  confirms the deployed classifier fixes. Earlier full integration suite 821
  passed before the first vocabulary expansion.
- Independent review initially reproduced two cross-card leaks. Both were fixed
  with red/green regressions; final review APPROVE, no required findings. Optional
  improvement: check shutdown between destinations in long delivery batches.
- Browser QA: offline HTML settings preview rendered the simplified controls,
  queue status and working connection check. Browser policy blocks chrome:// and
  chrome-extension://, so owner performed the real reloads. Do not bypass that
  block with alternate browser APIs or command-line automation.
- Proof screenshots: discord-live-feed-20261021.jpg (real product feed) and
  collector-connected-status-20261021.jpg (updated setup status). The temporary
  offline preview tab was closed and its owned loopback 8876 server was stopped.
- No commits, branch changes or pushes. All workers stopped after owned edits.

## Verified Discord setup

- Dedicated Reviewer Alerts guild: 1562530389866717295.
- Owner requested improving the attached storefront/stars image and applying it
  as the server logo. Generated assets/branding/reviewer-alerts-server-icon-v1.png
  with built-in imagegen; exact prompt is in assets/branding/README.md. Uploaded
  via the owner's Server Profile, accepted the centered crop and saved. The new
  icon is visibly present in both the server sidebar and window header; proof:
  data/discord_bridge/icon-applied-20261021.jpg. No bot permissions changed.
- Owner-approved Reviewer Alerts Bridge app/bot: 1562532620502437978, #9030.
- Owner accepted the server Community Guidelines and Developer Terms/Policy,
  personally completed human verification, reset the bot token and saved it in
  ignored data/discord_bridge/local.env. Token verified against official API;
  never printed. File ACL still grants only owner and SYSTEM, no inheritance.
- Owner approved deletion of the four empty defaults. All four were deleted
  and verified. No existing community was modified.
- Owner approved the initial three management permissions and then the four
  normal View/Send/Embed/Read History permissions required by Discord creation.
- Actual dry run matched two paid roles, seven categories and 30 text channels.
  Applied exact private ACLs and saved ID-only data/discord_bridge/server.json.
- Cleared ALL guild-role permissions from the bot after creation. It retains
  the four runtime permissions only through managed channel member overwrites.
- Actual runtime privacy audit: ok true, zero findings. No Administrator role.
  Private Profile on, Medium verification, all-member sensitive-media filtering,
  moderator 2FA, restricted member pruning, mentions-only notifications;
  discovery/public preview absent and widget disabled.
- Public-info/community means server members; paid feeds deny @everyone View.
  Starter/Pro bases are zero. Pro can also view Starter. Private operations
  remains owner/bot only. Owner always bypasses channel permissions.
- Posted four setup/onboarding messages in welcome, how-it-works, plans-and-help,
  feed-status; they originally said paid access and live alerts were inactive.
  The collector status was subsequently updated as recorded above.
  Content source: tasks/discord-onboarding-draft.md. Receipt IDs are ignored in
  data/discord_bridge/onboarding-posts.json.
- Sent ONE clearly labeled historical example only to private operations #audit.
  Read it back: correct bot author, embed, three Walmart buttons. No paid feed
  inventory announcements, public invites or live collector connection.
  Receipt: data/discord_bridge/smoke-delivery.json.
- The earlier setup had no sender running. The collector now runs the integrated
  Discord sender as authorized and verified above.
- Owner-requested live renames completed: six value feeds now use 💲0-10 through
  💲200-plus; all eleven category names omit type-. Discord stripped ASCII $, so
  the supported 💲 symbol was used. Former #yesterday-recap is #free-24h-alerts,
  with an accurate historical/24-hour topic and setup status. Stable route keys,
  IDs, categories and ACLs are unchanged. Display names now have their own manifest
  field, so routing does not depend on presentation text.
- Actual readback: all 30 text-channel names match the manifest, privacy audit
  ok true/no findings. Receipt: audit-channel-names-free24h.json. Screenshot:
  free24h-and-channel-names.jpg (final combined names and tier-rules view).
- Updated the bot's existing #plans-and-help setup message to explain free
  24-hour history and exact Starter filters; verified its own author and saved
  content by readback. No new promotional post or live listing was sent.
  Receipt: onboarding-free24h-update.json.

## Current Whop state

October 22 update: owner completed the Reviewer Alerts individual verification.
Fresh business Verifications readback shows the existing individual identity
and **Payouts Active**. Whop Card and Bank deposits remain Inactive as separate
capabilities; do not interpret either as proof that payouts are disabled.
Personal payouts were already Active. Bank destination setup is still being
walked through with the owner; no bank details or automatic transfers were
submitted by the agent. Owner chose to leave optional PayPal checkout off.

Owner personally created and signed into a NEW Whop account. Created the
Reviewer Alerts business workspace: biz_EIvLkhcxjNoQdD.
Dashboard: https://whop.com/dashboard/biz_EIvLkhcxjNoQdD/
Dismissed optional paid Economic Intelligence offers; never activated them.

Saved Reviewer Alerts Starter: prod_7TpoQmp6bHB8c.
The form verified recurring USD $5 per month, Add a waitlist checked, Hide from
store page checked on its price, and Visible on your store page off on the
product. Product table verifies Hidden / Not listed on Discover / zero users.
Default 30% affiliates, fake 20% discount display, member-count display and
included Whop Chat were disabled before saving. No shipping address collection,
free trial, upfront fee, split pay or cancellation discount. Accurate setup-only
copy states below $40/five-minute delay/eligible reviewers/no guaranteed claims.

Saved Reviewer Alerts Pro: prod_GsPF7my0RjJtU at recurring USD $20/month.
Same hidden product + hidden waitlist-only price and disabled promotional/app
defaults. Product table verifies both Hidden / Not listed on Discover / zero users.
Reopened Pro editor and verified $20 per month 0 Waitlist Hidden persisted.
Mappings are in ignored data/discord_bridge/whop-connection.json; no secrets there.
Owner approved the official Discord app's additional 3% fee for Starter/Pro.
Installed publisher Whop's app_sWtSR8DZsEGryg as experience exp_oHhrnaXBiSEMyn,
initially product-gated to both. Do not publish a checkout or accept other addons.
Owner then explicitly approved verified Whop Bot 815703008075579422 with only
Manage Roles, Create Invite and View Channels, plus displayed profile/email,
member-info and command scopes. Owner personally completed the MFA challenge.
Actual Discord API confirms a real bot with managed role 1562546005201911971,
permissions exactly 268436481. Saved hierarchy is Bridge > Whop > Starter > Pro.
Bound the bot's real ID locally; actual privacy audit ok true, no findings.
Receipts: whop-membership-bound.json and audit-whop-verified.json.
Audit policy now permits optional Create Invite/View only on the bound membership
bot; other roles and the alert bot still cannot have invite permission. No
Administrator or moderation permission was granted. No live alert sender started.

The embedded app cannot be automated reliably, but its official standalone
frame panel works AFTER authorization. Refetch roles worked below the Bridge
bot and above paid roles; no additional elevation was needed.
Separate experiences are now saved and verified:
- exp_oHhrnaXBiSEMyn: product-gated ONLY to Starter; includes ONLY Starter role.
- exp_tWRoVrlmCpMiX6: product-gated ONLY to Pro; includes ONLY Pro role.
Both Settings panels verify Reviewer Alerts, Remove Role cancellation action,
no event-log channel and no past-due replacement role selected. The second app
reused the existing server connection; no second OAuth grant was needed.
An initial automatic review rejection of removing Pro from the shared app was
resolved safely: completed the separate Pro instance first, then used the labeled
Pro checkbox to restrict the original instance. Both plans retain integration.
Screenshots: whop-starter-product-gate.jpg, whop-starter-role-mapping.jpg,
whop-pro-role-mapping.jpg, whop-starter-settings.jpg, whop-pro-settings.jpg,
whop-role-order-verified.jpg. Configuration is verified; lifecycle timing is not.
Final product-table readback still shows BOTH Hidden / Not listed on Discover,
zero active users and $0 revenue. Screenshot: whop-connected-hidden-products.jpg.
Complete fees, seller/KYC/payout setup, real membership lifecycles and
source/offering eligibility remain unresolved. No sales enabled.

## Implementation fixes and verification

Reusable discord_bridge package with a collector adapter; no new dependencies.
The optional standalone intake remains 127.0.0.1:8788 with a private Bearer key
and rejects browser Origin; integrated operation uses the existing 8787 listener.
Separate durable
SQLite destination jobs, retry/expiry/lease rules, 300-second Starter delay,
Walmart allowlisted sampling/product links, safe projections and default-off live gate.
Automatic product classification and collector routing are implemented above.
Small product thumbnails are implemented above; personal DMs remain later work.
Free 24-hour delivery is implemented in the independent queue: every public
observation gets one immutable historical snapshot, including unavailable/zero/
unknown-value items. It cannot be released early or leak later title/value
updates, and does not disappear when a later observation becomes unavailable.
Due paid jobs take priority over free catch-up. Free jobs can catch up for seven
days after their release; paid expiry remains 30 minutes from original intake.
Queue schema 1 upgrades additively to 2 and preserves paid jobs/clocks. A legacy
item first captured by the new free feed waits a fresh full day, avoiding
backdated snapshots exposing recent data. No old source ledger was imported.

Live setup exposed two compatibility gaps, now fixed and reviewed:
- Discord permits only overwrite bits held by the bot; Manage Roles overwrites
  require Administrator. Lean View/Send/Embed rules replace ungrantable blanket
  action denials. Strict zero member bases, exact ACLs and role audit preserve
  privacy. Full setup bitfield is 268520496; management subset is 268435504.
  Provisioning checks the post-@everyone-reset bot role grants BEFORE mutations.
- API rate limits now separate endpoint/method/major resource, learn Discord
  bucket IDs, honor exact retry delays and preserve a separate global gate.
  Client never automatically retries writes. Setup orchestration retried only
  definitive 429 responses with bounded waits; no ambiguous-create retry.
- Rate-limit follow-up: reproduced that repeated 429 waits exhausted the worker's
  ten-failure budget. Fixed in core.py/service.py: 429 deferrals release the lease
  without consuming a delivery-failure attempt, including after restart. Retry
  delays start after the response; permanent errors and stale expiry are retained.
  Added three regressions for repeated throttles/restart, bounded real connection
  failures and rate-limited expiry. The first failed before the fix as expected.
  No live traffic or throttling experiment was sent to Discord.

Checks on current checkout:
- Latest full Python suite: 734 passed in 76.86s, command:
  .venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp=data/qa-free-alerts-full-20261021 --tb=short --maxfail=3
  Escalation was only the established Windows test-directory ACL workaround.
  An earlier focused run hit WinError 10053 in the preexisting oversized-loopback
  request test; its focused rerun and the complete suite then passed unchanged.
  Whop invite regression first reproduced rejection of the approved grant; all
  bridge-focused checks then passed after allowing Create Invite/View only on
  the bound membership bot. Spoofed/unbound and collector invite grants fail.
- Free-feed regressions first reproduced all seven missing behaviors before
  implementation. Eight free-specific scenarios now pass, including exact delay,
  immutable snapshot/restart/duplicate intake, all values/unavailability, separate
  retention, paid-first priority and schema-1 migration without backdating.
  All bridge checks and repository Ruff/format/whitespace checks pass. Focused
  self-review retained the existing privacy gate and all paid timing/ACL rules.
- Offline burst simulation: 500 synthetic items, 2,000 destination jobs delivered,
  55 injected 429 responses, store reopened mid-run, no duplicate destination
  sends and zero live Discord requests. Receipt: rate-limit-simulation.json.
  This does not measure actual Discord throughput or eligibility/availability.
- Added regressions first reproduced 3 permission failures; focused 50 passed
  after fix, and explicit rate-limit/resume coverage was included in full suite.
- API worker added 16 tests; 26 API/caller checks passed; edits stopped.
- Independent security reviewer: no required P1/P2 findings; 35 audience/role
  combinations checked. Both workers stopped; ownership complete.
- Repository Ruff and whitespace checks pass. Earlier checkpoint extension
  runtime suite passed 83 tests; no extension edits were made for this slice.
- Actual audited API setup and real private delivery verified as above.

## Files and next steps

Primary guide: discord_bridge/README.md. Draft products: discord_bridge/whop-products.json.
Research: tasks/plan.md, tasks/research/item-history-summary.json (70,399 IDs).
Capacity proposal: tasks/discord-capacity-plan.md, including routing-volume
estimates and rate-limit handling. About 1,318 IDs/day in the captured trailing
week implies roughly 4,000-5,300 paid channel posts/day, plus about 1,318 free
historical posts a day with the newly accepted 24-hour policy.
Member count does not multiply channel sends; DMs would. Never replay the full
history when connecting the collector. Keep $5/$20 promise unchanged.
Referral feasibility follow-up: added a PROPOSED four-new-paying-customers =>
30-day Pro reward policy there, with a 14-day qualification hold, one reward per
referrer initially and reward users counting toward any future Pro cap. These
are recommendations, not accepted live terms. Whop docs confirm tracking links
and cash commissions, not a built-in threshold/free-month toggle. No affiliate
program, coupon, free membership or new recurring billing was enabled.

Ignored local receipts: provision-preview.json, provision-applied.json,
audit-verified.json, bootstrap.json, smoke-delivery.json, onboarding-posts.json.
Final Discord screenshot: data/discord_bridge/private-server-ready.jpg.
Whop product table screenshot: data/discord_bridge/whop-hidden-products.jpg.
Final connected/hidden readback: data/discord_bridge/whop-connected-hidden-products.jpg.
Never commit or print local.env, ingest key or bot token.

Use audit after removing setup grants; provision requires all seven again to
access widget settings. Do not silently regrant setup privileges. Separate real
free/Starter/Pro accounts and cross-account Walmart links remain to be tested
before any customer launch. Native Whop role-sync must pass actual activation,
expiry, failed-payment, upgrade/downgrade and refund tests.

No commits, branch changes, pushes or paid charges. Owner-authorized local
collector changes and container replacement are recorded above.
Discord browser tab 2 is retained as deliverable; Whop tab 6 is retained at the
Products dashboard. Temporary standalone app-control tab 7 was closed after
configuration. Reopen only an observed official frame/admin URL when needed.
Developer Portal token tab was closed by the owner; do not
reopen or capture a revealed token. Original brief preview on localhost:8895
remains an earlier user-facing artifact, not an alert sender.
