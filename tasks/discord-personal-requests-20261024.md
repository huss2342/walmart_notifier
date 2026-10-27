# Pro personal item requests: implementation and activation handoff

Investigated October 24, 2026 (America/Chicago).

## Status and confirmed direction

The owner authorized implementation after reviewing the investigation below.
Source implementation is deployed and active. Independent review approves the
feature; full regression verification and live checks are recorded below.
The owner signed in and the new private channel, command, pinned guide and
welcome benefit are live. The existing service runs the tested image with
`DISCORD_REQUESTS_ENABLED=true`. No new bot management privileges were granted.

The owner wants Pro members to request items, receive an acceptance confirmation,
and manage their own requests. The owner explicitly chose **DM-only item alerts**.
There must be no channel fallback or public mention when a DM cannot be delivered.
The implementation uses the defaults described below: ten watches per member,
keyword or exact product matching, persistent watches and one successful alert
per member/product. Prices and billing remain unchanged.

## October 25 repair: reconnect and channel restrictions

Owner reported generic `/request` failures and required requests only in
`#item-requests`, with ordinary chat disabled there. Live health reproduced a
running Gateway with `connected=false` and no last error. The SDK emitted
DISCONNECT → RESUMED after a dropped connection; the listener only restored
readiness on READY. A separate live read showed the channel's Pro overwrite
missing before this repair, preventing Pro visibility and failing privacy audit.

- `request_commands.py` now handles READY and RESUMED through one identity and
  shutdown guard. A transient disconnect gets private reconnect/retry guidance.
  Every command, error, view, modal and reply checks the exact guild/channel.
  Wrong-channel invocations acknowledge privately then erase that response;
  autocomplete returns no choices. No request content or status reply remains
  elsewhere, including owner/admin invocations.
- Restored the exact Pro View/History/Use Application Commands overwrite. Regular
  member posting stays denied. The full read-only privacy audit passes again.
- Saved `/request` command overrides with the owner UI: deny `@everyone`, allow
  Pro; deny All Channels (`guild_id - 1`), allow only the bound request channel.
  API readback confirms all four overrides. The existing pinned guide now says
  commands are only for this channel and ordinary member chat is disabled.
  Whop's integration has no installed slash commands; its membership grants were
  not changed. Discord owners/admins inherently bypass ordinary channel ACLs.
- RED regressions reproduced the missing RESUMED handler and wrong-channel
  replies. Independent reviewer approves all required boundaries. Final checks:
  **1,454 passed, one intentional platform skip** in 131.14s, with repository
  Ruff, scoped formatting and whitespace checks passing. Focused command suite:
  59 passed, including real SDK event dispatch with fake network/provider.
- Candidate Linux image passed network-disabled READY/DISCONNECT/RESUMED and
  wrong-channel checks, plus the earlier DM/outbox/restart/SDK parity smoke.
  It copies only `request_commands.py` over the prior live image and preserves
  the live server/diagnostic hashes. No database migration or role grant.
- Deployed as `reviewer-notifier:local` via Compose. Current image:
  `sha256:619ecd60b2f67ac9878303667103a186ae07168e2adea5a26571ed07988bab90`.
  Rollback: `reviewer-notifier:before-request-repair-20261025`. Source hash:
  `fdc014ed43784d9ff17d5d8cbcee1bba49c927a66af7f9f401d65f01802570bf`.
- Live health confirms ready=true, connected Gateway, running DM worker and no
  request errors. Existing feed delivery continues. Actual owner `/request list`
  in the request channel returns a private Pro-only refusal (owner lacks Pro).
  Actual owner invocation in `#general` removes its acknowledgement and leaves
  no reply. No synthetic watches or other-member test DMs were created; a real
  positive Pro request/matched-item DM remains unobserved.
- Ignored receipts: `data/discord_bridge/request-repair-20261025.json`,
  `request-repair-baseline-20261025.json`, `request-repair-build-20261025/`, and
  `request-channel-restrictions-20261025.jpg`. No commit, push, billing change,
  credential change or extra bot management privilege.

## Implementation receipt

- New source: `discord_bridge/request_store.py`, `request_delivery.py`,
  `request_commands.py`, `request_runtime.py`, and `request_setup.py`.
- Integrated with both the collector and standalone bridge; matching is in the
  existing `JobStore.record` transaction. The shared SQLite database and REST
  client preserve the existing feed and tier-release behavior.
- `/request` has add/list/edit/delete/test-dm, private owner-bound controls,
  autocomplete, fresh Pro checks, DM setup/recovery and visible failure status.
- Durable leases and committed send snapshots cover overlapping requests,
  concurrent edits/deletes, fresh reobservations and restarts. Sent or uncertain
  deliveries never blindly re-arm. Definitely unsent outcomes may re-arm only
  on a new eligible live observation. Only Discord's explicit Unknown Member
  error cancels departed-member work; unverifiable guild access defers safely.
- The optional channel is managed and audited when bound. Existing installations
  without it keep their prior policy. Runtime is disabled by default with
  `DISCORD_REQUESTS_ENABLED=false`; listener startup never registers commands.
- Added dependency: `discord.py>=2.6,<3`, verified with 2.7.1 on Windows and Linux.
  No public endpoint or message-content intent is used.
- Source documentation: `discord_bridge/README.md`; environment example updated.

## Verification and resources

- 102 focused feature checks pass across the five new test files, including
  genuine SQLite restarts, loopback collector integration and mocked Discord
  boundaries. Independent related feed/provisioning/transport checks pass.
- Repository Ruff and scoped formatting checks pass; server `--help` imports
  successfully. The initial full-suite run exposed a compatibility regression
  for an existing synthetic historical timestamp. The request hook now returns
  before request-specific validation when no watches are eligible; the affected
  priority/store/delivery checks pass. A repeated Windows atomic-replace lock in
  setup persistence now receives a bounded retry, preserving atomic writes and
  propagating permanent failures; two lock regressions and independent review
  pass. Final repository suite: **1,424 passed, 1 intentional platform skip**
  in 133.47 seconds. Log: `data/discord_bridge/pro-requests-python-release-20261024.log`.
- Independent review: APPROVE, all required findings resolved, including unsent
  re-arm, seeded-ID live reobservation, atomic render snapshot, failure visibility,
  DM channel-type validation and specific membership error codes.
- Docker candidate `reviewer-notifier:pro-requests-candidate-20261024` builds and
  passes an isolated, network-disabled Linux DM/outbox/restart/SDK payload test.
  Its actual server starts and serves health with sending/requests disabled.
  These are fake-provider tests, not live product DMs or real user interactions.
- Image is a narrow layer over the current running image, preserving pending
  `src/server.py` and `src/browser_diagnostics.py` work. Their in-image hashes
  match the original running container. Deployed that image as
  `reviewer-notifier:local`; production is healthy with live sending still on.
- Rollback image retained: `reviewer-notifier:before-pro-requests-20261024`.
  Baseline receipt and build context are in ignored
  `data/discord_bridge/pro-requests-live-baseline-20261024.json` and
  `data/discord_bridge/pro-requests-build-20261024/`. No credentials entered them.
- Owned temporary Docker check containers have stopped and were removed. The
  original SQLite backup completed and passed `PRAGMA quick_check`; the second
  redundant backup helper was stopped. No commits, branch changes, pushes,
  member role grants or live synthetic item requests.

## Completed activation and live evidence

The owner completed Discord sign-in and MFA. The Chrome session
`🔔 Discord requests` is left on the live private request guide. Existing local
bot credentials and all other runtime settings were preserved.

- Channel `1563632592232783962` is under the existing Pro category, with exactly
  audited Pro-only view/history/application-command access and the bot's previous
  posting rights. No ordinary member posting or public fallback was added.
- Guild command `1563633366450970685` contains add/list/edit/delete/test-dm.
  Registration encountered a definitive 429, then completed after respecting
  the transport cooldown; no bulk overwrite was used.
- Image `sha256:8a39cc8177a91282519de5b8f5c41aa6ca196b1cce44019131542ccf323070a7`
  is running. The local recovery supervisor restarted the service using the
  selected image/settings after the controlled stop; final Compose up was
  idempotent. Live health reports requests enabled/ready, Gateway connected,
  sender running, and no personal delivery failures or review work. Existing
  paid/free delivery continues.
- Ran `/request list` through the actual Discord client as the owner without a
  Pro role. The bot returned the Pro-only refusal with **Only you can see this**.
  This verifies the real interaction and access-check path without granting a
  role or using another member's identity. The ephemeral test was dismissed.
- Posted and owner-pinned guide `1563633778122039407` with clickable command
  links. Updated the existing bot-owned welcome message `1562538276806459574`
  with one compact Pro requests field; pricing and billing instructions remain.
  Readback verifies both the welcome benefit and guide pin.
- No synthetic watches entered the live database and no other member received
  a test DM. The first actual Pro request and matched-item DM remain unobserved;
  local real-SQLite/fake-provider tests cover the positive and blocked-DM flows.
- Receipt: `data/discord_bridge/pro-requests-activation-20261024.json`; screenshot:
  `data/discord_bridge/pro-requests-live-20261024.jpg`.

The following setup sequence records what was applied and how to repeat setup
on another installation; it is no longer pending for this server.

1. With the owner session, create exactly `#item-requests` under the saved
   `pro-firehose` category, using the payload from
   `.venv/Scripts/python.exe -m discord_bridge.request_setup plan`. Allow Pro
   View/History/Use Application Commands, deny ordinary-member posting and
   application commands, and keep the bot's existing posting permissions.
   Do not grant the bot new management privileges.
2. Bind that explicitly selected ID with `request_setup bind --channel ID`
   followed by the same command with `--apply`. A full privacy audit must pass.
3. Preview then apply `request_setup register`; this upserts only `/request` and
   does not bulk-overwrite other commands. Existing source authorization covers
   this setup; no new billing or privilege decision is pending.
4. Enable `DISCORD_REQUESTS_ENABLED=true` in the existing private runtime env,
   tag the candidate as `reviewer-notifier:local`, and replace only this service
   using `docker compose up -d --no-build notifier`. Preserve the current env and
   loopback port. Verify connected Gateway and running sender/readiness in health.
5. Verify real Pro add/list/edit/delete/test-dm and loss-of-access behavior with
   the owner's authorized test account. Do not message another member or create
   synthetic requests on their behalf. This server's live access gate and command
   availability are verified; its guide/welcome posts are now live. Positive Pro
   DM acceptance awaits a voluntary real request.

The following investigation records the original architecture and design basis;
its line references describe the pre-implementation source.

## What exists today

- `src/notifiers/discord.py:39` projects collector observations into public product
  fields. `DiscordNotifier.ingest` at line 146 durably accepts observations.
- `discord_bridge/core.py:91` normalizes titles, Walmart item IDs, product links,
  value, category, images and availability. These support local matching without
  changing what the browser scans or fetching user-supplied URLs.
- `discord_bridge/core.py:350` updates observations even for previously seen
  products. Matching must consider fresh reobservations, not just brand-new IDs.
- `discord_bridge/core.py:273` owns SQLite item/delivery storage with restart and
  duplicate protection. The current delivery destinations are channels.
- `discord_bridge/api.py:129` provides the shared REST transport and rate handling.
- `discord_bridge/membership.py:21` already uses the saved paid-role IDs; Pro wins
  when a member holds both Starter and Pro. Discord roles establish access;
  Whop continues to own billing and role expiry.
- `discord_bridge/layout.py:246` and `discord_bridge/provision.py:308` enforce exact
  channel ACLs and managed-resource inventories. A new channel or permission must
  be added to this policy, not created outside it: drift can block all delivery.
- Current paid roles have zero guild permissions. `USE_APPLICATION_COMMANDS` is
  defined in the layout but is not granted by the current channel policy.
- The runtime uses REST and background workers. There is no Gateway listener,
  interaction handler, personal request ledger or DM delivery workflow.
- Welcome/how-to-use posts already exist. Their feature copy should change only
  when the new capability is working and verified.

## Live verification

The current bot credentials and saved server mapping were used without printing
secrets. The existing transport's short cooldowns were respected during reads.

- Existing server privacy audit: **passed**, no findings.
- Application identity matches the saved collector bot: **yes**.
- Application interactions endpoint: **unset**.
- Global application commands: **none**.
- Server application commands: **none**.
- Existing managed request-related channel: `feature-requests`, which is public
  community feedback, not a Pro product-request channel.

No DM was sent and no command was registered. These reads verify the starting
configuration, not the proposed request workflow.

## Member experience

Add `#item-requests` in a Pro-only area with a short pinned guide. Allow Pro
members to use application commands there; ordinary message posting need not be
enabled. Use slash commands for guided inputs and private confirmations.

| Action | Proposed interface | Result |
| --- | --- | --- |
| Add | `/request add item:air fryer` | Private "Request accepted. I'll DM you when a matching item is detected." |
| Exact product | Same command with a Walmart product URL or item ID | Watch that product ID rather than similar titles |
| Manage | `/request list` | Private list of the member's requests, status, and Edit/Delete buttons |
| Edit | Button or `/request edit` | Choose one of their own requests and update it |
| Delete | Button or `/request delete` | Confirm deletion and cancel unsent matches for that request |
| Check delivery | `/request test-dm` | User-initiated test and private instructions if DMs are blocked |

Every interaction reply containing request data should be ephemeral (visible only
to the requesting user). The later product notification is a real DM from the
existing bot, with the matching request, product title, value when known, image
when available, observation time and the existing Walmart links.

Perform a DM check during initial setup. If Discord rejects it, explain how to
enable DMs and show the request as needing attention, rather than promising an
active alert. A later delivery failure should appear in request status; never
publish the item or member's request as a fallback.

Suggested welcome benefit after launch:

> **Pro includes personal item requests.** Tell the bot what you're looking for
> in #item-requests and receive a private DM when a match is detected. View,
> edit or delete your requests anytime. Server DMs must be enabled.

## Matching and lifecycle defaults

- Support keyword watches and exact Walmart item IDs. Start with predictable,
  case-insensitive word matching, handling punctuation and word boundaries.
  Clearly explain that keyword matching is broader than an exact product watch;
  a request such as "monitor" can also match an accessory mentioning that word.
- Match only available items freshly observed after a watch is created. Do not
  replay the historical ledger or seed/import observations as new matches.
- Initial limit: 10 active requests per Pro member, with bounded input
  lengths and no user regex, arbitrary URL fetching or extra browser searches.
- Watch behavior: remain active until deleted, alert once per distinct
  Walmart item ID. If several of one person's requests match one item, send one
  DM naming the matching requests. Relisted/restocked copies of the same ID would
  not alert again in this first version; a restock policy would be separate work.
- Recheck current guild membership and the exact saved Pro role before accepting
  changes and immediately before sending item data. Do not infer Pro from role
  names, Discord colors or an old membership celebration snapshot. If verification
  fails, defer; confirmed loss of access stops item DMs.
- Bind all reads, edits, deletes, button actions and autocomplete to the
  authenticated Discord user and guild. Never accept another person's user ID as
  the request owner. Store only the identifiers and request/delivery fields needed.

## Implementation approach used

1. Add a small Gateway command listener for the existing application. This keeps
   the collector's HTTP endpoint on loopback and avoids adding public hosting.
   Use a maintained Python Discord SDK for connection/reconnect handling; keep
   existing channel delivery and collector behavior intact. Slash interactions
   do not require reading arbitrary message text.
2. Add durable request storage and a personal-notification outbox. Match and
   enqueue transactionally with accepted observations, or use a durable intake
   cursor; a best-effort callback after successful intake can lose matches on a
   crash. Keep duplicate keys, request revisions, cancelation and expiry explicit.
3. Deliver through the shared rate-aware REST transport. Persist stable send
   identity and reconcile ambiguous sends rather than blindly repeating DMs.
   Membership checks, DM creation and sends all consume requests; personal fanout
   needs a bounded share of delivery capacity and aggregate health counters.
4. Extend the managed layout/audit for the Pro request channel and narrowly
   scoped `Use Application Commands`. Enforce Pro access in the command handler
   as well as Discord's UI; channel visibility alone is insufficient.
5. Add explicit command registration/setup and disabled-by-default runtime flags.
   Prepare the source, tests and setup preview before any live activation. Update
   welcome/how-to-use copy after verified activation.

Verification should cover Pro/Starter/Free separation, cross-user ownership,
blocked DMs with no fallback, role removal, keyword/exact-ID matching, unavailable
and historical items, fresh reobservations, restart durability, duplicate intake,
edit/delete while queued, concurrent claims, rate limits and ambiguous sends.
Then verify the complete flow using an ordinary Pro account and the current
privacy audit before advertising the feature. Current implementation checks
are recorded in the receipt above.

## Official Discord contracts checked

- [Interactions overview](https://docs.discord.com/developers/interactions/overview):
  Gateway and public HTTP are alternative ways to receive interactions.
- [Receiving/responding](https://docs.discord.com/developers/interactions/receiving-and-responding):
  acknowledge within three seconds, defer slower work, and use ephemeral replies
  for private request management. Interaction tokens expire; later item alerts
  must use normal bot DMs, not saved interaction tokens.
- [Application commands](https://docs.discord.com/developers/interactions/application-commands):
  register guild-scoped commands for this server. Role/channel command permission
  updates need authorized user configuration; the bot token alone cannot set
  those overrides. `default_member_permissions` takes permission bits, not a Pro
  role ID. Keep runtime role checks authoritative.
- [Create DM](https://docs.discord.com/developers/resources/user#create-dm):
  user-initiated requests provide the opt-in; opening many DMs can be rate limited.
- [DM delivery help](https://support.discord.com/hc/en-us/articles/360060145013-Why-isn-t-my-DM-going-through):
  members' privacy settings or blocking can prevent delivery.
