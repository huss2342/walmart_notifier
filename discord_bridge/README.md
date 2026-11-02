# Private Discord bridge

A private delivery package integrated with the local collector through
`src/notifiers/discord.py`. The owner authorized switching the existing Docker
notifier from Telegram to Discord. Live sending is **off by default in code**;
this workspace's local settings now explicitly select Discord and enable it.
See [the current handoff](../tasks/ACTIVE.md) for verification and pending owner
steps. On October 28 the owner selected Free and $30/month Pro with unlimited
signups. The new Pro checkout is Visible / Buy now / Unlimited, without a trial
or initial fee. Starter and the old $20 Pro pricing option are hidden and out of
stock; both old direct checkouts were verified unavailable. Existing test
purchases remain unchanged. Membership lifecycle was not retested by this change.

The free feed releases **every captured item after 24 hours** in
`#🆓24h-delayed-alerts`, marked as historical and available to all server members.
The paid plan is **Pro: $30/month** (all observed values, no intentional delay,
category/value feeds and personal item requests). Starter's legacy role and
channel IDs remain for audit/history compatibility, but it is no longer routed
or delivered. Startup skips any old pending/leased Starter jobs and frozen
dispatches with reason `route_retired`, preserving terminal history. Deleted
Starter roles/channels/categories no longer block the active-feed audit; any surviving
retired resources still undergo permission checks. The delay starts when this bridge first accepts the
item at the local backend. Arbitrary incoming timestamps are deliberately ignored;
the collector posts fresh page observations rather than importing its old ledger.

There is no member cap, waitlist or new admission restriction. Older capacity
proposals are superseded by the owner's October 28 instruction. Current pricing
and evidence are in [the rollout record](../tasks/free-pro-launch-20261028.md).

## Ready now

- A manifest of two paid roles, seven channel categories and all price/type feeds.
- One pinned #welcome payment card with the current $30 Pro checkout
  button, and read-only #updates for owner announcements. Member instructions
  live in public read-only #how-to-use (stable key `how-it-works`). Stable internal
  keys remain unchanged; owner posting templates are in
  [discord-updates-templates.md](../tasks/discord-updates-templates.md).
- Member chat only in #general and #feature-requests, with 10-second slowmode.
  Member uploads and embeds are unavailable; native owner-managed AutoMod blocks
  common URLs, custom-emoji markup and role pings. Built-in Discord stickers and
  standard emoji may remain usable. The minimal bot cannot read AutoMod rules;
  verify them in Server Settings after changes. See the
  [security review](../tasks/discord-security-review-20261022.md).
- Dry-run-first provisioning and a read-only privacy audit. Existing resources
  are never adopted by name or deleted. Partial setup saves IDs after each create.
- SQLite destination jobs and immutable dispatch envelopes, restart-safe delay,
  atomic batch settlement, per-route retries and stale expiry.
- Discord response-header throttling; 429 waits retain the delivery-failure
  budget. Live paid jobs expire after 30 minutes; free history can catch up for
  seven days after its scheduled release and yields to due paid alerts.
- Authenticated sidecar HTTP and direct Python intake through the existing collector.
- Local product-title classification, supplemented by card-specific department
  hints. Search queries can only break ties already supported by product evidence.
- Two Walmart buttons (Open review item and View product), optional small product
  thumbnails, and explicit unknown-value/category handling. Photos use only
  allowlisted public Walmart product-image URLs with query/fragment data removed;
  missing or rejected images never block the alert. The thumbnail is part of the
  existing message, with no separate upload, scrape or API send.
- Two draft Whop products in [whop-products.json](whop-products.json).

## Temporary Pro access

Members can run `/mytrial` in **#general** for a private reply with their own
trial status, local expiration date and time remaining. It has no member option
and never changes access or sends a DM. Separate Pro access is reported without
inventing a subscription expiration. The general channel alone explicitly allows
members to use application commands; `/trial` retains owner-only permissions.

Suggested announcement: **Check your free trial anytime: run `/mytrial` in
#general to see your status and expiration. Only you can see the reply.**

The optional trial feature grants Pro feeds and personal requests directly in
Discord, without a Whop account. **Pro Trial** is a separate role; expiry only
removes temporary access. Members retaining Whop's paid **Pro** keep their access
and saved requests. When neither entitlement remains, the service deletes saved
requests and cancels unsent personal alerts. An uncertain API response does not
prove a member lost access.

In the owner-only **#trial-management** channel:

```text
/trial grant duration:7d
/trial extend duration:7d
/trial revoke
/trial grant member:@person duration:7d
/trial grant member:@person until:2026-11-10T18:00:00-06:00
/trial extend member:@person duration:7d
/trial revoke member:@person
/trial list
/trial welcome enabled:true hours:24
/trial welcome enabled:false
/trial welcome
```

Only the current server owner can operate these commands; each action checks
fresh ownership and replies privately. Omit `member` to open a private selector
for up to 25 members. Choose people, review the selection and press **Confirm**;
selection alone changes no access. **Cancel** or expiry closes the controls
without changes. Each selected member receives an individual result, including
pending synchronization or a rejected change; existing active trials are not
overwritten by a grant. Use `extend` to add time. Controls expire after three
minutes of inactivity and must be reopened after a bot restart.

`duration` accepts hours or days. Supply
either `duration` or `until`; exact dates require a timezone. Trial timestamps
are displayed in the viewer's local Discord time. List results are paginated.
Grant and extension confirmations include a short optional message to copy to
the recipient. Trials send no automatic DMs, public announcements or extra
member pings.

The welcome offer starts **off**, with a recommended 24-hour duration. It supports
1–168 hours and applies only to future new members after enabling. One welcome
trial is permitted per recorded Discord ID; leaving/rejoining and turning the
offer off/on do not reset it. Turning it off stops new grants while existing
expirations and request cleanup keep running. Member-list polling requires the
existing **Server Members Intent**. Expiry is reconciled about every 15 seconds
while the service runs; restart catches up overdue removals. During a Discord
outage, channel access cannot be removed until its API recovers, while personal
request eligibility still checks the saved expiry.

Setup is explicit. Create an unprivileged, nonmentionable **Pro Trial** role,
place the collector bot **above Pro Trial and below paid Pro**, and add only
**Manage Roles** to its runtime permissions. The trial bot cannot manage paid
Pro at that hierarchy. Review the exact channel/ACL plan before applying it:

```powershell
.venv/Scripts/python.exe -m discord_bridge.trial_setup plan --role TRIAL_ROLE_ID
.venv/Scripts/python.exe -m discord_bridge.trial_setup bind --role TRIAL_ROLE_ID --channel TRIAL_CHANNEL_ID
# After preparing the exact role, channel and ACLs and reviewing the dry run:
.venv/Scripts/python.exe -m discord_bridge.trial_setup bind --role TRIAL_ROLE_ID --channel TRIAL_CHANNEL_ID --apply
.venv/Scripts/python.exe -m discord_bridge.trial_setup register
.venv/Scripts/python.exe -m discord_bridge.trial_setup register --apply
```

`plan` returns the owner-only channel creation payload and exact Pro-channel ACL
updates. `bind` audits the complete server before saving the two IDs locally;
it does not create resources or edit permissions. `register --apply` upserts only
`/trial`, preserving `/request` and its overrides. Restart the existing live
request runtime after binding. Its separate `trials.sqlite3` persists grant
intents, expiry, welcome settings and one-time eligibility alongside the existing
collector database. Keep one active runtime per trial ledger. No live setup is
performed by importing these modules.

For existing manual Pro promises, grant the recorded member their actual
remaining expiry first. Then remove only a confirmed **manual** Pro assignment;
the trial bot never removes Pro and cannot infer whether an existing Pro role
came from payment. See [the trial task](../tasks/owner-trials-20261102.md) for
scope, duration research and activation status.

## Referral discounts

`DISCORD_REFERRALS_ENABLED=true` enables the existing Gateway's **Get my referral
link** button and an independent outbound Whop polling worker. Private replies
work for Free/trial/Pro members; no new slash-command permission, member role,
public HTTP endpoint or paid hosting is needed. Whop Bot still controls paid
access. Each successful first paid referred subscription gives its buyer 30%
off that first payment and the referrer 30% off one next payment. Additional
rewards queue future discounted months; there are no cash commissions.

Private `data/discord_bridge/referral-config.json` binds guild/business, eligible
product IDs, monthly plan IDs/prices and primary checkout. The API credential
is read only from `whop-referrals.env` in that directory. Do not commit, print or
put this key in a link. Scopes and unavoidable `member:manage` breadth, current
verification and rollback are documented in
[the rollout record](../tasks/referral-automation-20261030.md).

`referrals.sqlite3` is separate from item/request queues. Credits and invoice
use are unique, native Discord-to-Whop identity is verified, and unknown writes
retain their exact intent/submission boundary across restart. The private
button uses bounded dedicated threads; provider deadlines, Retry-After and
credential-scoped idempotency are respected. Existing promotions, absent paid
memberships and unresolved applications keep rewards pending. Authenticated
health exposes readiness/error counts without customer details. A future
monthly tier can add stable product/plan IDs and prices without a public name
being used for billing identity; create that live tier and its access rules only
when its benefits are defined.

## Personal greetings

`DISCORD_WELCOME_ENABLED=true` enables the collector's low-priority greeting
worker. Default is off. It requires managed public read-only `greeting`, native
join messages routed to private `audit`, join notifications enabled and sticker
reply prompts disabled. Privacy and system-channel checks fail closed; no new
bot permissions or Gateway/member intent are needed. This workspace's native
source and permissions were verified and the worker enabled on October 22.

One explicit joining-user mention is allowed; role/everyone/reply pings are
disabled. A separate durable `welcome.sqlite3` stores activation cursor, bounded
pagination and outbox, skips historical/bot joins, and applies a 24-hour rejoin
cooldown. Ambiguous sends reconcile or require review instead of blind retries.
Terminal join data expires after seven days. It shares the existing Discord
quota/client and yields to due paid deliveries. Health and sanitized logs expose
readiness, pending/failed/review counts and provider errors.

Server notifications default to mentions-only. Owners cannot force another
member's personal channel mute/push/Highlights settings. A real join must still
prove Discord emits the native event into the private source and the custom
greeting mentions only that member; local tests cannot prove this provider edge.

## Membership celebrations

`DISCORD_MEMBERSHIP_ENABLED=true` adds colorful congratulations in `#greeting`
when a human first gains Starter or Pro access, and when Starter upgrades to Pro.
Starter cards are teal and Pro cards gold. Cards mention only that member and
contain the congratulations and membership activation text, with no public
member count or scan footer. Internal health still counts unique humans with
paid access; someone holding both roles counts once, as Pro. This measures
Discord access, not transactions, revenue or historical purchases. Whop remains
the access manager; manual paid-role assignments also look like activations.

Enable **Server Members Intent** on the existing bot's Developer Portal Bot page.
This is required by Discord's [List Guild Members API](https://docs.discord.com/developers/resources/guild#list-guild-members).
No Gateway connection or additional guild permissions are needed. The feature is
off by default and only runs with live Discord delivery. It shares the transport
and yields to due paid alerts. Polling is every 30 seconds, one page of up to
1,000 members per cycle; announcements can take longer during paid alert bursts.

The separate durable `membership.sqlite3` (override with
`DISCORD_MEMBERSHIP_PATH`) stages paginated scans and quietly baselines existing
paid members. Complete scans update the count and outbox together. Renewals,
unchanged roles and downgrades stay quiet; repeated activations of the same tier
have a 24-hour cooldown. Snapshot hashes persist while access is active, raw IDs
are kept during scanning/pending delivery, and terminal announcements expire after
seven days. Ambiguous sends reconcile without blind duplicate pings. Health exposes
`membership` readiness, `paid_members` and delivery counters, without member IDs.
If member access is denied, health reports `member_access` and celebrations stop.

## Burst delivery

The sender packs up to ten already-ready paid items (five for Free) for the same channel into one
message, without waiting for a batch to fill. Each card keeps its thumbnail;
clicking its title opens review context, and numbered review/product button rows
identify its actions, with two item pairs per row on larger paid batches. Single
items keep their original format. Ten-item batches reduce message requests by
up to 90% while staying within Discord's embed, UTF-16 text and component limits.
New messages also obey a conservative 10 KiB serialized-body ceiling and may
contain fewer cards. This is an application limit prompted by observed HTTP 413
responses, not a claimed Discord request limit. A fresh definitive rejection can
reduce that route's batch size and safely repackage jobs; possibly accepted or
unknown historical attempts retain their exact body/nonce for review or expiry.

Up to four channels send concurrently through reusable HTTPS connections. Shared
rate reservations, Discord bucket/global headers and Retry-After control pacing;
one throttled channel no longer stops healthy channels. Observed route/global
cooldowns survive restart. The worker wakes on intake and drains ready work
without the old two-second pause between batches.

Priority slots favor Pro and higher observed values. Full Pro bundles keep up
to nine high-value positions and reserve the final card for oldest same-channel
work in the same expiry cohort. Newly arrived expensive Pro items lead the next
eligible envelope. A persistent fair-route slot serves overdue/quieter paid feeds.
Free history waits while paid work is due, so a slow Free request cannot block
the next paid wave. Future tier-delayed jobs do not block Free. Batches do
not mix fresh items with backlog expiring within 60 seconds. Free's 24-hour
release and 30-minute paid expiry are unchanged; sent or expired jobs are
not replayed.

The exact message body, ordered members and nonce are saved before submission.
Retries keep that envelope; a changed/ineligible/seeded/expired member safely
invalidates its remaining batch rather than repackaging a possibly sent message.
Legacy uncertain single sends keep their existing nonce. Discord's nonce
deduplication lasts only a limited time, so prolonged ambiguous failures are not
an exactly-once delivery guarantee.

Known provider route/global cooldowns are checked before auditing and claiming.
Frozen-envelope retry lookups use a covering batch index so future Free jobs do
not make paid claims hold the SQLite writer lock for long scans.
Every bounded send wave still requires the full privacy audit. Shutdown is
checked between audit calls and before sending; the HTTP transport has a total
request deadline and never automatically resubmits a write. `/health` separates
queued item destinations from message count, audit/delivery timing, queue age,
throttles and recent confirmed delivery. Future tier-delayed work is normal.

See [burst-delivery verification](../tasks/discord-burst-delivery.md) and run
`python tests/benchmark_discord_burst.py` for the offline comparison. Its modeled
quotas and latency are test assumptions, not live Discord limits or a paid SLA.

The existing sender also writes rotating private JSONL delivery events and
one-minute queue summaries, including idle time. Health metrics distinguish
future tier releases, due/retry/throttled work, terminal history and recent
delivery latency. See [delivery operations](../tasks/discord-delivery-monitoring.md)
for log paths, live-view commands and interpretation. No extra monitor service
or public technical messages are required.

Free delivery uses immutable public snapshots, so later edits or unavailability
cannot leak recent paid-feed details or photos through an older free alert.
Personal request DMs are available through the optional feature below. Billing
continues to be managed by Whop.

## Pro personal item requests

Pro members can watch up to ten keyword queries or exact Walmart products in the
private `#item-requests` channel. Matching uses fresh collector observations;
it does not add browser searches or replay historical items. Keywords match all
entered words, ignoring case and punctuation, and can also match accessories.
Use a Walmart `/ip/` product link or item ID for an exact product watch.

- `/request add item:air fryer` saves a watch and privately confirms acceptance.
- `/request add item:recliner min_value:100` watches recliners valued at $100 or more.
  The minimum is optional, uses USD with up to two decimal places, and includes
  the exact boundary. Items with an unknown value cannot meet a minimum.
- `/request add item:recliner, chair, reclining chairs min_value:100` adds three
  separate watches with the same minimum. You can instead enter
  `recliner, chair, reclining chairs $100+` in the item field. Each comma-separated
  phrase matches independently; words within a phrase must all match.
  A batch saves together or fails without adding any requests. Repeated phrases
  within a batch count once. Later overlapping batches reuse saved phrases and
  add only the new ones. Existing filters stay unchanged; an explicitly different
  minimum for a reused phrase must be changed with Edit instead.
  The total limit remains ten watches, including bulk additions.
- `/request list` shows personal requests with a selector and Edit/Delete buttons.
- `/request edit` and `/request delete` offer autocomplete for the member's own
  requests. Edit one watch at a time. Omitting `min_value` keeps its existing
  minimum; use `min_value:0` to remove it, or a new value / `$100+` suffix to change
  it. The Edit dialog also accepts a minimum (blank keeps it, zero clears it).
  List controls expire after five minutes of inactivity and disable when possible.
  Expired controls or edit forms, including controls from before a bot restart,
  privately direct the member to run `/request list` again.
- `/request test-dm` checks delivery and resumes watches after DMs are enabled.
  It sends a test DM; it does not simulate a product match or reset product deduplication.

Existing watches survive updates; members can edit them without deleting their
list. Each comma-separated phrase counts as one of the ten watches, so
`ice maker, ice machine` counts as two. Shared words between different phrases
are allowed. A phrase may contain multiple required words (up to 150 characters);
the full add input is limited to 512 characters.

Request commands work only in the bound `#item-requests` channel. Pro members
have Send Messages there because Discord requires it for slash commands; this
also allows ordinary messages. All command replies are private. Item alerts are
**DM only**, with the product
image when available, observed value and Walmart links. There is no channel
fallback. The first request checks DMs; blocked delivery pauses alerts and shows
instructions. Watches remain active until deleted. A successfully sent product
is not repeated for the same member, even when several requests match it.
There is no total notification cap or twenty-minute watch timeout. Individual
unsent alerts expire after twenty minutes; new matching products remain eligible.
Confirmed loss of the Pro role stops delivery. New requests require current Pro
access, and every product DM checks the live role immediately before sending.

The request store and personal outbox share the collector's SQLite database so
matching is committed with observation intake. Seed/import events do not alert;
a later fresh live observation of an old item can match a new watch. Definitely
unsent canceled/failed/expired work can re-arm on a fresh observation; uncertain
sends require review rather than a blind repeated DM. Queued alerts expire after
20 minutes. The latest observed value must still meet the minimum immediately
before a queued alert is submitted. Existing watches have no minimum until edited.
Delivery uses a bounded share of the existing REST transport; it does
not guarantee item availability or a delivery deadline during bursts.

The feature is off by default. Install `src/requirements.txt`, prepare the
channel with the owner account, bind its exact ID, and register only this command:

```powershell
.venv/Scripts/python.exe -m discord_bridge.request_setup plan
.venv/Scripts/python.exe -m discord_bridge.request_setup bind --channel CHANNEL_ID
.venv/Scripts/python.exe -m discord_bridge.request_setup bind --channel CHANNEL_ID --apply
.venv/Scripts/python.exe -m discord_bridge.request_setup register
.venv/Scripts/python.exe -m discord_bridge.request_setup register --apply
```

For an existing installation, deploy the updated request modules and restart
the collector, then preview and apply `request_setup register` again to expose
the optional `min_value` slash option. Existing watches migrate automatically
without a minimum. Registration does not run automatically on startup.

The plan prints the exact parent and permission overwrites: only Pro gets View,
History, Use Application Commands and Send Messages; the bot retains its normal posting access.
Bind performs the full existing privacy audit before saving the explicitly
specified channel ID. Existing servers without this optional channel continue
to pass their normal audit. No new bot management privileges are required by the
runtime. Do not create an untracked channel and leave it outside the managed
mapping: the existing sender refuses privacy drift.

In Server Settings → Integrations → Reviewer Alerts Bridge → `/request`, set
role overrides to deny `@everyone` and allow Pro, then channel overrides to deny
All Channels and allow only `#item-requests`. These saved command permissions
require the owner account; the bot token cannot edit them. The handler also
silently acknowledges and removes interactions outside the exact bound channel,
including owner/admin invocations that bypass Discord's command permissions.
Owners and administrators bypass channel ACLs. Pro members can send ordinary
messages in the request channel as well as use commands.

Set `DISCORD_REQUESTS_ENABLED=true` alongside `DISCORD_LIVE=true` and restart the
collector (or standalone live bridge). The outbound Gateway listener requires no
public HTTP endpoint or message-content intent. Registration is explicit; startup
does not overwrite existing application commands. `/health` includes aggregate
request, DM queue, Gateway connection and readiness status without member IDs or
request text. Readiness recovers after both READY and RESUMED events; temporary
disconnects show private retry guidance within the request channel. Add the
benefit to the welcome guide only after activation is
verified. See [implementation receipt](../tasks/discord-personal-requests-20261024.md).
The sender does not verify Walmart stock or claim eligibility. No auto-claim.

## Collector integration

Keep the extension endpoint at `http://127.0.0.1:8787/ingest`. Discord credentials
stay only on the backend in ignored `data/discord_bridge/local.env`; never enter
the bot token in extension settings. The existing ingest token, when configured,
is unchanged. No second HTTP service or new listener is needed.

Select in ignored `notifier.env`:

```dotenv
NOTIFY_PROVIDER=discord
DISCORD_LIVE=true
```

Run `docker compose up -d --build notifier` after backend changes, or `run.ps1`
for native operation. The default durable queue is
`data/discord_bridge/collector.sqlite3`; the default channel mapping is the
existing `data/discord_bridge/server.json`. Optional `DISCORD_QUEUE_PATH` and
`DISCORD_STATE_PATH` override those paths. The standalone CLI uses its separate
`queue.sqlite3`, so earlier preview examples are not imported into live feeds.

Reload **Reviewer Item Relay** in Chrome's extension manager and refresh the
Walmart reviewer tab after extension changes. The extension settings contain
scan/timing, connection and diagnostics controls; Discord product filters live
on the backend. Explicit card-local department hints replace the old badge
mislabel. Badges, search queries and owner claim quotas are never published.
Unfamiliar products fall back to `other`; the English rule vocabulary can be
extended in `classification.py` without changing channel IDs.

The October 23 category update recognizes more everyday clothing, footwear and
accessories, including sweatshirts, sweatpants, rompers, vests, hats and sunglasses.
Human sweatshirts with a pet-carrier pocket stay in Clothing; explicit pet garments
and recovery suits go to Pets. Product-purpose phrases keep animal-themed ornaments,
cleaning/work gloves, bottle boots and plumbing tees in their relevant departments.
All eleven category routes retain their existing Discord channels. The title audit
and verification receipt are in [category-detection-20261023.md](../tasks/category-detection-20261023.md).

`/ingest` acknowledges durable queue acceptance using `queued`, while `notified`
remains zero for that intake request. A background sender delivers due jobs and
`/health` reports `discord.live_sending`, aggregate job states and its last
delivery result. `discord.worker` reports actual sender liveness, completed polls,
the current cycle's elapsed time, last confirmed send, sanitized failure class and
recovery time. An idle sender can be healthy without a recent matching product;
a running thread alone does not prove it is making progress. A rejected missing
personal-DM destination pauses that member's sends until `/request test-dm`
confirms a working destination. Uncertain sends remain held for review.
Privacy checks run before sending; failed checks retain jobs
without publishing. System tests, bot-check and silence warnings go only to
private `#collector-health`. Historical `/rescan` publication is refused in
Discord mode to avoid replaying old personal history.

Use a separate queue for synthetic tests. `DISCORD_LIVE=false` accepts observations
without posting; `SEED_MODE=true` also disables sending and durably suppresses
the seeded item IDs. Schema 3 adds persistent seed suppression while preserving
existing destination IDs, snapshots and delay clocks from schemas 1 and 2.

## Preview without credentials

Run from the repository root:

```powershell
.venv/Scripts/python.exe -m discord_bridge plan
.venv/Scripts/python.exe -m discord_bridge preview discord_bridge/example-items.json
```

Both commands are local only. The example is labeled synthetic and is not an
availability announcement. Input and output fields are described below.

## Connect the owner accounts

1. Sign in to Discord and create a dedicated private server. Provisioning expects
   an empty server, or its previously saved managed resources. Discord may create
   default channels; review those while empty before removing them yourself.
   Do not point this tool at an established community.
2. Enable Developer Mode in Discord and copy this server's ID. Keep owner 2FA on.
   Do not publish an invite, enable Discovery/public preview or enable the widget.
3. Create an owner-controlled application/bot in the official
   [Discord Developer Portal](https://discord.com/developers/applications).
   No privileged Gateway intents are needed: delivery uses REST.
4. Install the bot only in this server with View Channels, Send Messages, Embed
   Links and Read Message History, plus temporary Manage Server, Manage Channels
   and Manage Roles. **Do not give it Administrator.** The manifest prints the
   full setup bitfield, currently `268520496`; management alone is `268435504`.
   Discord requires the creating bot to hold every bit it allows or denies in
   a channel overwrite. Zero member role bases and the strict audit keep other
   member actions unavailable. [Discord channel creation](https://docs.discord.com/developers/resources/guild#create-guild-channel).
5. Initialize local settings once:

   ```powershell
   .venv/Scripts/python.exe -m discord_bridge init
   ```

   This creates `data/discord_bridge/local.env` with a random ingest key and a
   blank `DISCORD_BOT_TOKEN`. On Windows its ACL is restricted to your account
   and SYSTEM; on Unix it is created with mode 0600. Existing settings are
   preserved. Put the bot token in that local file with an editor. Never paste
   it into chat, commit it, or include it in screenshots.

6. Inspect the dry run, then apply to that exact server:

   ```powershell
   .venv/Scripts/python.exe -m discord_bridge provision --guild YOUR_SERVER_ID
   .venv/Scripts/python.exe -m discord_bridge provision --guild YOUR_SERVER_ID --apply
   ```

   The second command creates the reviewed roles/channels and hardens server
   settings. It saves ID-only state in `data/discord_bridge/server.json`. Creation
   is fail-closed: private ACLs are included in the create request. Run only one
   provisioning process. If a create succeeded remotely but its local ID save
   failed, the next run refuses the unknown resource; reconcile it rather than
   deleting or guessing. Setup is resumable for known saved resources.

7. Clear the alert bot's guild-role permissions after setup, then run:

   ```powershell
   .venv/Scripts/python.exe -m discord_bridge audit
   ```

   Use `audit` after removing setup rights; `provision` still requires its full
   temporary setup grant, including access to widget settings. Runtime permissions
   are View Channels, Send Messages, Embed Links and Read
   Message History, granted on managed channels. The audit must return `ok:true`
   before live delivery. It checks role privileges, exact channel ACLs, parents,
   unexpected resources and available server privacy settings. The owner always
   has access; authorized members can copy messages. These controls do not stop
   owner actions or promise exclusive inventory.

## Whop connection

Whop is the selected candidate for payment plus membership-role management;
there is no custom payment handler or customer billing database here.

The existing Pro product and Discord experience remain in use. Its active
$30/month checkout is recorded in [whop-products.json](whop-products.json).
Pro maps only to Pro. Do not reopen retired Starter or the old $20 checkout.
Legacy Starter bindings remain for compatibility; its feed is private operations.

Whop's documented integration supports paid-role assignment, cancellation actions
and past-due settings. Choose removal of the purchased role at effective access
expiry, retain the free community, and verify actual timing with test memberships.
Avoid “Remove all roles,” “Kick,” or “No action.” Leave membership event logging
off initially; this package does not grant Whop access to private operations.
See [Whop's official setup guide](https://whop.com/blog/link-whop-to-discord/).

Position the Whop integration above Starter/Pro. After checking its identity and
the permissions requested by the official connection, bind its exact bot ID:

```powershell
.venv/Scripts/python.exe -m discord_bridge trust-membership --bot VERIFIED_WHOP_BOT_ID
.venv/Scripts/python.exe -m discord_bridge audit
```

This is an explicit trust decision: a role manager can assign Pro to itself or
others. The bridge admits only the bound, real managed bot role, with a suitable
hierarchy and only Manage Roles, optional Create Invite and View Channels.
Discord requires Create Invite for automatic member joins. The owner explicitly
approved that addition for the verified Whop Bot; other roles cannot gain it
without failing the audit. It refuses Administrator,
Manage Server/Channels and other unrelated privileges. It changes no Discord
permissions while binding. The owner approved Whop's account/profile, email,
member-info and command scopes, with only the three stated server permissions.
Do not silently broaden the allowlist if its connection asks for more. The owner
must review that concrete request first.

Before selling, test subscription activation, renewal, cancellation, failed
payment, upgrade/downgrade and refund with real free/Starter/Pro test accounts.
Inspect channel links directly from a free account and re-run the audit after
changes. The official Whop Discord app adds **3% of payments** for each product
that includes it; the owner approved this for the hidden Starter and Pro products.
Whop's payment page lists base domestic card processing of 2.7% + $0.30 and
separate service fees. Confirm all enabled services before publishing; the
processing headline is not an all-in membership quote.
[Whop pricing](https://whop.com/network/pricing/).

Commercial source permission and offering eligibility from the original plan
remain unresolved. Account preparation and private tests do not establish them.
This work enables no subscription checkout, public advertising or live sales.

## Future collector connection

Start the independent bridge in preview/intake mode:

```powershell
.venv/Scripts/python.exe -m discord_bridge serve
```

It binds **127.0.0.1:8788 only**. `GET /health` and `POST /v1/observations`
require `Authorization: Bearer <DISCORD_BRIDGE_INGEST_KEY>`. POST also requires
`Content-Type: application/json`. Browser-origin requests are rejected and CORS
is not enabled. Connect a trusted backend adapter, not a public website or the
browser page. Never put the Discord bot token into the extension.

```json
{
  "items": [{
    "item_id": "ip-20449164214",
    "title": "Example audio card",
    "value_usd": 19.99,
    "category": "toys-baby-books",
    "url": "https://www.walmart.com/ip/20449164214",
    "available": true
  }]
}
```

- One item, a list, or `{ "items": [...] }` is accepted; maximum 500 items and
  512 KiB. The entire batch validates before any persistence.
- Accept numeric `value_usd` with whole cents, or integer `value_cents`, not both.
  Currency is USD. Unknown/null/zero values never unlock Starter or a price band.
- Known categories are printed in `plan`; absent or unknown ones route to Other.
  The backend can add correct tags later without changing the intake contract.
- Only the matching HTTPS `www.walmart.com/ip/...` product is accepted. Tracking
  is removed. Review links are constructed from the supplied sampling pattern,
  labeled unverified, and accompanied by a regular product/catalog fallback.
  A second eligible account must verify the sampling route before relying on it.
- Public projection drops claims remaining, private raw fields and queries.
- `202` means durable acceptance, not Discord success. Retry a failed HTTP request;
  one logical job per item/destination prevents duplicate ingest from replaying it.
- Later metadata enriches pending **paid** messages/new destinations without
  resetting original delay/expiry or replaying sent destinations. Explicit
  `available:false` suppresses pending paid sends; omission from a partial scan
  does not. Free snapshots remain unchanged and still release as history.
- Paid jobs expire 30 minutes after first intake. Free jobs first become eligible
  after 86,400 seconds, then have seven days to catch up. A legacy item first
  captured by the new free feed waits a fresh full day; recent metadata cannot
  use an old item timestamp to bypass the hold. Queue schema 1 upgrades to 2
  without losing existing jobs or resetting paid clocks. Failed/expired counts remain
  visible in authenticated health. Discord nonce dedupe only covers a recent
  window, so exactly-once delivery across every remote crash is not guaranteed.

For a local Python caller, use `validate_batch(payload)` then
`JobStore(path).record(items)`. Run one delivery service for the same queue.
Supply all public observations before the collector's personal notification
filter; forwarding only its high-value personal matches would omit Starter items.
Display names are separate from stable route keys and saved channel IDs.
For the existing Docker collector, `127.0.0.1` inside its container is not the
host bridge: choose a reviewed private backend transport when integrating it.
This package deliberately makes no existing Docker or collector changes.

When real account/role tests pass and the collector is ready:

```powershell
.venv/Scripts/python.exe -m discord_bridge serve --live
```

Only this explicit flag enables sending. The service refuses startup on a failed
audit and checks permissions again before release batches. Sender retries and
Discord rate limits do not change durable release times. Keep the machine awake;
this does not make the Walmart collector independently hosted.

Stop the service with Ctrl+C. Queue jobs and the original delay clock survive
restart. Do not queue the historical 70k-item ledger as a fresh live feed.

## Implementation boundaries

`policy.py` owns product categories, price bands, tier prices and release clocks.
Managed feed definitions and observation routing share these definitions.
`observations.py` validates the public observation contract; `messages.py` renders
Discord payloads. Existing imports from `core.py` remain supported.

The single-job `JobStore.claim/finish/retry` API adapts the canonical dispatch
engine. It respects channel/global cooldowns and records delivery metrics. A
single-job caller cannot claim an existing frozen multi-item envelope; that work
remains available to `claim_batch` without splitting or changing its nonce.

Personal watches use transactional exact-ID and Unicode token indexes. Existing
watches are indexed once during schema initialization; database triggers keep
add/edit/delete atomic with the index, including writes from a rollback image.
Fresh observations still create their private outbox entries atomically with
collector intake. No historical
observations are replayed by the migration.

`DeliveryOutcome` separates a transport-proven pre-submission failure from a
rejected or potentially accepted write. Personal alerts retry the former safely;
accepted or unknown writes stay protected from blind retries. Channel frozen
body, cancellation, release timing and time-limited nonce policies still apply.

## Verification

```powershell
.venv/Scripts/python.exe -m pytest tests/test_discord_bridge_free.py tests/test_discord_bridge_runtime.py tests/test_discord_bridge_service.py tests/test_discord_bridge_provision.py -p no:cacheprovider
.venv/Scripts/python.exe -m ruff check discord_bridge tests/test_discord_bridge*.py
```

The tests use actual SQLite restarts and loopback HTTP with a stateful fake
Discord API. They establish local behavior and modeled permissions, not live
Discord delivery, Whop checkout behavior or cross-account Walmart eligibility.
See [the handoff](../tasks/ACTIVE.md) for current full-suite results, the earlier
extension checkpoint and live Discord audit evidence. The original independent
security review covered the prepared private setup; later changes have their
own regression evidence there.
On this managed Windows host, pytest requires a fresh permitted `--basetemp`
outside the restricted sandbox; never reuse/delete another session's temp tree.
