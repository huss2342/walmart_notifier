# Owner-issued Pro trials

November 2, 2026, America/Chicago. Status: **live and verified**. Owner controls
with multi-member selection and the one-time 24-hour welcome offer are active. Whop billing, existing paid
memberships and scanner settings are unchanged.

## Accepted outcome

The owner wants to stop keeping a manual list of people and Pro expiration
dates. Their later clarification supersedes the initial owner-grants-only choice:
grants activate **directly in Discord, without a Whop signup**. Also provide a
limited-time automatic trial offer for future new members, with an owner on/off
control and adjustable duration. Delete saved item requests when a member no
longer has either paid Pro or valid trial access. Preserve paid memberships.

The owner asked for research to choose between four and eight hours. Recommended
initial duration: **24 hours** to allow a full day of alert exposure, especially
for late-night joins; eight hours is the shorter alternative. This is a judgment
for this service, not a measured conversion optimum. The control supports
1–168 hours, so the offer can be changed without a release. Recurly recommends
matching duration to experiencing value; ChartMogul's SaaS research provides
context but does not compare four/eight/24-hour Discord trials:
https://recurly.com/blog/how-to-fix-the-free-trial-problem/
https://chartmogul.com/reports/saas-go-to-market-report/

## Discord workflow

Use an owner-only `#trial-management` channel in private operations. Check the
actual server owner's Discord ID on each command; channel visibility alone
must not authorize grants. Responses and member records stay private.

- `/trial grant duration:7d`: select up to 25 members, review and Confirm.
- `/trial extend duration:7d` and `/trial revoke`: select a group to change.
- `/trial grant member:@person duration:7d`: start a chosen person's trial directly.
- `/trial grant member:@person until:...`: preserve an existing promised expiry.
  Accept an explicit date/time with timezone and show the resulting local time.
- `/trial list`: show active grants, exact expiration and time remaining.
- `/trial extend member:@person duration:7d`: add time without shortening access.
- `/trial revoke member:@person`: end complimentary access early.
- `/trial welcome enabled:true hours:24`: start the new-member offer.
- `/trial welcome enabled:false`: stop new automatic grants. Existing trials
  retain their expiration; cleanup keeps running.
- `/trial welcome`: privately inspect the offer's current status.

Persist Discord user IDs rather than mutable display names, expiry timestamps,
granting owner and grant/revoke history. Commands must be idempotent across
duplicate interaction delivery. Reconcile expiry after restart and retry
temporary provider failures without losing the recorded intent. Show pending
activation/removal honestly when Discord has not confirmed it.

The owner explicitly approved activation and requested concise, quiet trial
communication. Trial grants send no automatic DMs or public pings. Private grant
and extension confirmations include an optional short message to copy to the
recipient. A short owner guide is saved in the new private channel.

## Access and migration

Use a separate **Pro Trial** role that grants the
same feeds and personal requests. Remove only that role at expiry; never remove
Whop's paid **Pro** role. Verify request creation and actual DM delivery both
recognize current trial eligibility, including the stored expiration. The
bot now has the approved Manage Roles permission at runtime. Temporary setup
permissions have all been removed.

Whop's active $30/month checkout and billing remain unchanged. The trial bot
must sit strictly **above Pro Trial and below paid Pro**. The full privacy audit
admits Manage Roles only when the dedicated trial configuration is bound.
This restricts the bot from assigning or removing the paid Pro role.

The welcome offer starts disabled in a new ledger. Enabling persists the current
cutoff. Pre-existing members are not retroactively granted trials. Member IDs
are durably recorded so leaving/rejoining or toggling the offer cannot restart
an automatic trial. Automatic grants last from the member's recorded join time.

Existing manually assigned **Pro** roles need an explicit migration: identify
the member and promised expiration, establish temporary access, then remove
only a confirmed manual role assignment. Discord's Pro role alone cannot tell
whether someone is paying. Do not infer existing dates or remove paid access.

## Verification before activation

Use the existing Python/SQLite and Discord fake-API test patterns. Cover owner
authorization, wrong channel/server, duplicate commands, exact expiry,
extension, revocation, paid/trial overlap, API failures and restart recovery.
Audit all affected channel permissions and verify both personal-request
authorization paths. Use no real member grants or outgoing messages to test
without an explicitly chosen live-test recipient.

Run focused trial tests, the existing Discord regression tests, repository
Ruff, server startup and the full Python suite. No scanner/browser behavior is
part of this feature. Do not claim live activation from local fake-API tests.

## Evidence and next step

Inspected `discord_bridge/membership.py`, `request_commands.py`,
`request_delivery.py`, `request_runtime.py`, `layout.py`, `provision.py`,
`referral_runtime.py`, `src/notifiers/discord.py` and the Discord README. Current
membership polling celebrates role changes; it does not expire owner grants.
The prior privacy audit allowed runtime role management only to the bound Whop
bot; the reviewed optional trial configuration now admits the trial manager too.

Whop documents free pricing options with auto-expiring access:
https://whop.com/blog/create-a-whop/
Discord documents guild-scoped commands and permissions:
https://docs.discord.com/developers/interactions/application-commands

Implementation: `discord_bridge/trials.py`, `trial_commands.py`, `trial_setup.py`
and the existing request runtime, authorization, sender, privacy audit and saved
request store. No new dependencies, public endpoint or bot token is introduced.

## Final verification and activation handoff

- Final full Python suite: **2,738 passed, one existing platform skip**, 231.63s,
  `.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp
  C:/Users/Navi/AppData/Local/Temp/trial-full-final-20261102`.
- Final focused trial/integration/sender suite: **104 passed**. Independent
  reviewer separately ran **119** focused/request regression checks and approved
  after all three reproduced findings were fixed: stale expiry across network
  calls, ambiguous member-404 deletion and concurrent extension/watch deletion.
- Repository Ruff, whitespace and actual packaged server startup/help passed.
- Original ten-module candidate **92b291ad36e1** layered over exact
  **b5a19642db21**. Quiet copy was added in **9cf5e81ac947** after 36 focused
  tests. Final **72832f6c0f56** adds sanitized interaction-error classification.
  All ten live module hashes match the verified workspace; unrelated source
  changes were excluded. Final affected regression suite: **234 passed**,
  including real SDK interaction parsing/dispatch and SQLite persistence.
- Actual candidate, network disabled and no production mounts: real Linux
  SQLite welcome grant, owner grant, paid/trial overlap, disable, restart expiry,
  saved-request deletion and five-command SDK smoke passed. Receipt script and
  overlay/base snapshots are under ignored `data/trial-preparation-20261102/`.

## Live activation and remaining limits

The owner signed into Chrome and explicitly approved the concrete permissions
and activation. Created **Pro Trial** role `1566898622258221178` with zero base
permissions, nonmentionable; created owner-only **#trial-management** channel
`1566900700267675822` in private operations. Updated 22 Pro category/channel ACLs
and registered only `/trial`, command `1566901915256881288`. `/request` and its
existing permission overrides were preserved.

Actual hierarchy: trial **1**, collector integration **2**, collector's Bots
group **3**, paid Pro **4**. The collector integration now holds exactly
**268435456 (Manage Roles)**. Temporary Manage Channels, message and application
command bits used to copy Discord's ACLs were removed. No Administrator,
Manage Server, invite, moderation or webhook permission was granted.

The exact container's environment values, host configuration, mounts, networks,
command/user/security and scanner/relay settings were verified preserved. Final
image is **72832f6c0f56**, healthy. Normal and personal workers, Gateway and
referrals are ready, with no current worker errors. Existing **13 saved
requests are byte-for-byte equal by row hash** to the consistent SQLite backup;
all **seven paid members** retain Pro. The existing ambiguous-send review job
was not replayed.

The real owner `/trial welcome` command replied privately. The owner enabled
the 24-hour offer through the real UI at **13:58:23 CST**, November 2. Durable
cutoff: `1793649503.8121774`. A natural new member subsequently joined and
received the trial role: one recorded welcome grant, confirmed by role readback,
with expiry exactly **join timestamp + 86,400 seconds**. Existing member IDs
were recorded without retroactive grants. Final privacy audit: `ok:true`, no
findings. No synthetic member or test purchase was used.

The first live status request returned a private transient-unavailable reply;
its exact cause was not proven. Subsequent full real-UI status and enable
commands succeeded. Added redacted exception class/HTTP-status telemetry and
three actual SDK dispatch regressions to improve diagnosis; provider and local
transport limits retain their existing bounded behavior. Startup contention
receipts recovered on subsequent polls without deleting requests.

Receipts, consistent queue backup, original state/channel snapshot, exact
deployment scripts, final health and audit are in ignored
`data/trial-preparation-20261102/`. Operational rollback is
`reviewer-notifier:before-trial-interaction-diagnostics-20261102` (**9cf5e81ac947**),
which includes the complete trial manager and supports the current state and
ledger. Preserve current trial bindings/ledger; the pre-feature image cannot
manage active expirations.

Chrome **3**, tab **1530561957**, displays the private channel and owner guide.
All implementation/review workers have completed; no test containers remain.
No work remains for activation. Actual 24-hour expiration has not elapsed yet;
restart/expiry and paid-overlap behavior have offline SQLite/API coverage.
Existing manual Pro promises still need the owner's actual member IDs and
promised dates before migration; no existing Pro assignment was inferred or
removed.

## Owner-only command visibility

The owner explicitly requires `/trial` options to be visible only to them.
Read-only live verification confirms `default_member_permissions:"0"`, no trial
or application-wide command permission overrides, and **no Administrator roles**.
Ordinary members cannot see `/trial` in the command picker. The private channel
allows only the bot member, with everyone denied view/commands; the owner has
implicit server access. Every handler additionally checks the fresh owner ID.
Receipt: `data/trial-preparation-20261102/visibility-check.json`.

Discord's documented Administrator exception bypasses command permissions if an
Administrator role is introduced later. The bot's owner-ID check still rejects
those users. Do not claim the platform can hide commands from administrators:
https://docs.discord.com/developers/docs/interactions/slash-commands#permissions

## Multi-member controls follow-up (November 2; live and verified)

Accepted: owner requests selecting multiple members instead of entering one name
per grant. Implemented a private native member picker (1–25 people), review and
Confirm/Cancel for grant, extend and revoke. Omitting optional `member` opens the
picker; explicit single-member use remains compatible. Fresh owner and exact
channel checks apply to every component. Selection itself changes no access;
confirm uses durable per-member operation IDs and gives honest partial results.
No automatic DMs/pings or permission expansion. Existing welcome/expiry rules
remain in force. Exclusive UI/tests ownership: trial_commands worker; command,
gateway, SDK tests and integration/release ownership: root.

Verified:

- Full Python suite **2,775 passed / one existing platform skip**, Ruff and startup
  passed. Independent review approved after **73 affected tests**. UI tests use
  native SDK component resolution and real SQLite for batch grants. Confirm
  snapshots selection before ownership I/O; cancel/timeout during that wait and
  failed review updates prevent unconfirmed changes. Double confirmations and
  pending/partial results are covered. Expected rejections do not emit worker
  errors. Confirmed grants retain a short optional copyable recipient note.
- Exact packaged image passed an offline Linux native SDK/SQLite batch smoke,
  with two grants after confirmation, no role write on selection, paid-role
  preservation, restart expiry and saved-request deletion.
- Activated **14:34:07 CST** as image **794727e3ce50** over exact **72832f6c0f56**;
  only `trial_ui.py`, `trial_commands.py`, `request_commands.py` were overlaid.
  Gateway, trials, personal alerts and referrals are healthy. Runtime, settings,
  all 13 saved request rows and both pre-existing trial records are unchanged.
  The 24h welcome cutoff/settings and seven paid memberships are preserved.
- Real Chrome opened `/trial grant duration:7d` without `member`, selected two
  people, displayed **Selected (2)** plus enabled Confirm and **Only you can see
  this**, then Cancel displayed **No access changes were made**. Confirm was
  deliberately not executed against live members. The owner guide was edited in
  place; no extra user-facing notice or DM was sent. The private channel is left
  open. Updated command retains its ID, permission `0`, no overrides and no
  Administrator roles. Other commands and permissions are unchanged.

Concurrent edit: another workflow renamed the free recap text channel to
`🆓24h-delayed-alerts` in Discord and local `layout.py` during verification. The
only live/source layout difference is that display name. This release preserves
that work without shipping an unrelated module. The full server audit therefore
reports **one name mismatch**, with channel type independently verified as 0;
all access/privacy checks pass. All 11 deployed module hashes match the pinned
released base plus the three verified picker files. Do not describe the full
server audit as clean until the separate label change is deployed.

Resolved in the owner-authorized consolidated main release at 14:49 CST:
image `3e1b184f50a0` includes the accepted label, all 56 packaged sources match
the checkout, and the full server audit passes with no findings. Existing trial
records, saved requests and settings are preserved. See `tasks/ACTIVE.md` and
private receipts in `data/main-release-20261102/` for the current release.

Private receipts, source snapshots and the small trial ledger backup are in
`data/trial-multi-select-20261102/` (`activated.json`, `registration.json`,
`verified.json`, `smoke.json`). Full trial-manager rollback tag:
`reviewer-notifier:before-trial-multi-select-20261102` -> **72832f6c0f56**. If rolling
back, restore single-member command metadata to match its old runtime. No commit,
push or unrelated working-tree cleanup was performed. Both workers completed.
