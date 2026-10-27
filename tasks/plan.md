# Discord reviewer alerts: research and implementation plan

Prepared October 21, 2026 (America/Chicago). Status: proposed design for review;
no Discord resources, subscriptions, messages, or production changes were made.
Update: the owner subsequently selected exactly **$5 / $20** and authorized
isolated Discord preparation while the collector continues changing. The current
implementation and account connection steps are in
[the bridge guide](../discord_bridge/README.md). Research prices below remain
the original proposal; they do not override the accepted $5 / $20 choice.
Execution checklist: [todo.md](todo.md). Research companion:
[design brief](discord-design-brief-2026-10-21.html),
[captured measurements](research/item-history-summary.json), and
[reproducible audit](research/analyze_item_history.py).

## Recommended launch

Start with a private pilot, then two paid plans if source permissions and
cross-account availability are resolved: **Starter $4.99/month** for items below
$40 with a five-minute delivery delay, and **Pro $19.99/month** for all detected
values immediately after detection. Free members get a next-day recap, explicitly
labeled historical. These prices and delays are experiments, not validated demand.

Do not launch a $99 plan based on access to expensive items. Only 18 observed IDs
were $200+, including variants. Sell relevance and time saved; measure successful
use and renewal before adding more tiers. A five-minute delay can be implemented,
but its practical effect depends on how quickly items disappear, which today's
logs cannot establish.

Assumptions: USD monthly subscriptions, existing invited Walmart reviewers as the
audience, manual claiming in each member's own account, one Discord server, the
current local browser as the initial discovery source, and Telegram retained
during the pilot. A paid membership does not confer Walmart program eligibility.
"Every item" means every eligible item the scanner actually observes, not a
guaranteed complete or real-time Walmart catalog.

## What the current project actually does

| Area | Current behavior and consequence |
|---|---|
| Collection | `extension/content.js` reads signed-in claim-product cards. It extracts Walmart ID, cleaned title, the card's **Valued at** amount, a link, query, and account-wide claims remaining. Cards marked out of stock are skipped. This is not all of Walmart retail. |
| Searches | `extension/sweep-modes.js` defines regular/current query, a–z, and 59 preset High Value terms. High Value changes the search sequence, not the actual value filter. User-defined arbitrary query lists are not currently exposed. |
| Coordination | Background/content modules coordinate one primary tab, page traversal, retries, heartbeats, verification pauses, and progress. More subscribers do not create more inventory or make one sweep discover listings sooner. |
| Category | `Item.category` and `Rule.categories` already exist, but `collect()` fills category from a promotional badge such as “new arrival.” There is no reliable department classifier. |
| Link | `collect()` calls `.split('?')[0]`, removing sampling context. Retaining the numeric ID preserves dedupe when this is fixed. |
| Image | The model has `image_url`, but the current collection payload does not populate it. Images require an extraction change if visible card images are suitable. |
| Ingestion | `src/sources/webhook_source.py` validates incoming batches. Extra query and claims fields reach `Item.raw`. `src/server.py` invokes `pipeline.process()` and acknowledges its summary. |
| Personal filtering | `data/rules.json` currently matches retail value >=$79 OR “mirror”/“vanity”; it does not announce every observed item. Bundled `src/rules.json` defaults differ from these active saved rules. |
| Dedupe | `src/pipeline.py` checks global seen/pending state before notifying. Filtered and seeded items are recorded too. Success commits; failure releases the claim. One Boolean provider result cannot represent many channels and delayed destinations. |
| Storage | `src/state.py` replays `data/seen.log` over `data/seen.json`. Item rows retain ID, title, value and handling timestamp; markers are separate. URL, image, taxonomy, search history and availability history are lost. Retention defaults to 60 days. |
| Delivery | `src/notifiers/__init__.py` selects one of ntfy, Pushover, Telegram. There is no Discord provider, entitlement system, durable delivery schedule or user preference service. |
| Commands/status | Telegram supports owner diagnostics and commands. `POST /rescan` can inspect historic matches and reconstruct ordinary `/ip/<id>` links, but does not verify current claimability. Do not use it to bulk replay the paid feed. |
| Hosting | Docker hosts the loopback relay; the Walmart browser remains on this computer. A cloud bot alone cannot keep collecting if this computer/browser is asleep, disconnected or verification-blocked. |

The working manifest is 2.7.0; October 20 documentation describes 2.6.0. The
working tree has extensive existing uncommitted edits. Implementation should
first preserve that baseline and reconcile the current files; this plan does not
assume the repository's HEAD represents the installed extension.

## Measured item history

Read `seen.json` and replay only `t=item` log events, keyed by Walmart ID, just
as the store loader does. Do not add marker events to the item count, mutate the
store, or sum snapshot and log counts without deduplication. The captured files
contain 58,108 snapshot IDs and 12,291 appended item events, with no overlapping
item IDs or malformed log lines: **70,399 distinct IDs** in the merged history.

Recorded period: September 17, 2026, 10:06:47 AM through October 21, 2026,
8:40:24 AM CDT (15:06:47Z through 13:40:24Z). This is the range of handling
timestamps, not listing publication dates or a complete operating window.

| Observed retail value | Distinct item IDs | Share |
|---|---:|---:|
| $0 to below $10 | 19,422 | 27.59% |
| $10 to below $20 | 32,486 | 46.15% |
| $20 to below $40 | 15,447 | 21.94% |
| $40 to below $80 | 2,624 | 3.73% |
| $80 to below $200 | 402 | 0.57% |
| $200+ | 18 | 0.026% |

Median $14.99; 95th percentile $39.99; 99th percentile $69.99; maximum $399.99.
All rows contain numeric values, but 540 say $0. Those zeros are a data-quality
flag, not proof of a $0 retail value. Bands above include them as stored; live
policy should send unverified zero values to value-unverified review.

29,133 IDs explicitly contain “variant on” in the title (41.38%). Families are
therefore fewer than IDs, and this phrase is not a complete family detector.
The $200+ sample includes multiple variants of Christmas trees and a shower
door, as well as a lift chair, dryer, collector board game and printer ink.
Do not market all high-value matches as premium electronics.

In the trailing seven days ending at the latest stored observation, 9,227 IDs
include 50 at >=$80, 19 at >=$100, and **one** at >=$200. The trailing 14-day
window contains 17,339 IDs and also one at >=$200. These are history slices,
not stock, quantities, successful claims, or predicted future supply. Gaps in
recorded days, retention, changes in scans and account-specific visibility limit
any arrival-rate or market-wide conclusions. Historical departments cannot be
measured reliably because they were not retained and titles can be truncated.

Recent container log entries confirm successful alerts (including an $81 golf
storage rack, $99.99 dog playpen, $104.99 solar post light and $189 printer
media), verification pauses up to 1,440 minutes, and Telegram SSL/time-out
failures. The plain `data/notifier.log` is an old September startup log; Docker
logs are the more relevant operational source. No inference is made about the
root cause of those delivery failures or current pause state.

## Capability map and build order

These are proposed module boundaries, not approved module specifications.

| Module ID | Responsibility | Depends on |
|---|---|---|
| item-catalog | Safe links, metadata, taxonomy, observations, item/family identity | Existing validated ingestion |
| alert-policy | Pure decisions about value, category, recipient eligibility and release time | item-catalog |
| delivery-outbox | Durable destination jobs, retries, idempotency and worker leases | alert-policy |
| discord-feed | Embeds, buttons, channel routing and operational status | delivery-outbox |
| membership | Subscription/role synchronization and entitlement reconciliation | Chosen payment platform; alert-policy contract |
| personal-alerts | Opt-in preferences and private notifications | membership, delivery-outbox, item-catalog |

Build order: evidence checks → item-catalog → one private discord-feed path with
delivery-outbox → alert-policy/channel groups → membership → personal-alerts.
The policy contract must exist before workers use it; billing can arrive after
the private feed is proved. Create scoped implementation specs from this map
when execution starts; the current deliverable is the reviewable overall plan.

## Discord structure and visibility

Use ordinary text channels for item feeds, with bot-only posting. Put discussion
and feature requests elsewhere. Forums are useful for discussions; tags do not
create per-post paid access, and threads inherit parent visibility. Discord's
documented permissions are channel/role based [S3, S4].

```text
START HERE                  @everyone
  #welcome-and-eligibility  #how-it-works  #feed-status  #plans-and-help
COMMUNITY                   @everyone
  #general  #feature-requests  #yesterday-recap
STARTER — 5 MIN DELAY        Starter only; below $40
  #items-under-40
PRO — LIVE BY VALUE         Pro only
  #under-10  #10-to-20  #20-to-40  #40-to-80  #80-to-200  #200-plus
PRO — LIVE BY TYPE          Pro only
  #furniture  #home-storage-decor  #kitchen-appliances  #electronics
  #tools-auto  #beauty-health  #clothing-accessories  #toys-baby-books
  #pets  #sports-office-travel  #other-items
PRO — OPTIONAL FIREHOSE     Pro only; muted by default
  #all-items
OPERATIONS                  owner/moderators only
  #collector-health  #delivery-errors  #classification-review  #audit
```

Post each item in its one price channel and primary department channel; keep
secondary tags in the embed. The optional firehose is a third copy, not a third
item. Members choose a preferred notification view and mute others. No
`@everyone`/`@here` for item drops; interest roles are opt-in notification
preferences and never grant paid access. Do not mass ping thousands of IDs.

Starter gets a distinct, delayed channel; placing Pro and Starter in the same
department channel would expose Pro timing and high values. A later middle
plan needs its own delayed destinations or private delivery. Adding a role
after five minutes cannot selectively reveal one message in a shared channel.

For every protected category: deny View Channel to @everyone, allow only the
appropriate paid role and staff, synchronize child channels, deny ordinary
members posting/webhook creation, and keep bot permissions minimal. Do not
give members Administrator or reusable invite control over protected resources.
Test actual free/Starter/Pro accounts, including combinations of interest roles,
direct message links, subscription expiry and accidental unsynced channels.
Forwarding/screenshots by authorized members remain possible; access control
does not guarantee secrecy or exclusive inventory.

## Tagging and search coverage

Classify **items**, recording the page query as evidence. A search for “Wood”
can return a cutting board, frame or furniture; “Gaming” can return stickers or
a mouse pad. a–z terms carry no useful category meaning. Do not assign a whole
page to the query's category, or treat a badge as taxonomy.

Classification order: explicit item override → actual visible department or
structured taxonomy, if present and verified → weighted title/phrase rules with
negative accessory rules → cautious query hint → `other-items` and review queue.
Store `primary_category`, `tags`, `category_source`, `confidence`, and
`classifier_version`. A classifier's confidence is a routing score until
validated; do not present it as an accuracy probability.

Keep “case only,” replacement parts, pet toys, chair covers, Christmas tree
ornaments and accessories from being classified as the expensive parent product.
Examples from the history already show why a plain word match fails: a “desk
organizer” is not a desk, a sofa pillowcase is not furniture, and an earbuds
case is not earbuds. A manual item override wins over all future queries.

Start with deterministic local rules. Add optional AI only for unresolved
items after measuring a need; it must not block delivery. Persist all observed
search terms and enrich a known item when a later query provides evidence.
Global seen-state must not prevent taxonomy or last-seen updates.

Keep the three current sweep modes for the first pilot. Later add a configurable
keyword list with optional hints and a coverage report; each new query increases
scan duration and can overlap existing results. Do not speed up or bypass
verification to support a premium promise. Additional category subscriptions
should configure filters; category coverage should improve for everyone once
added, rather than charging a customer to fix missing basic taxonomy.

## Cross-account Walmart links and alert content

The supplied review URL contains no obvious user identifier or login token:
`filters` decodes to `[{"intent":"sampling","values":["Sampling"]}]`, plus
`classType=REGULAR` and `from=/reviews/claim-product`. These are context
parameters, not credentials. That is evidence of a potentially portable route,
not proof that every invited account can claim the item. Walmart still decides
eligibility, inventory, account quota and checkout behavior [S2].

Store `product_url` and `review_url` separately. Keep a known-good card's sampling
context by constructing a new HTTPS Walmart URL with an allowlist of the three
parameters above; retain the actual numeric ID/variant. Use the observed path,
including its slug when available. Drop session/tracking parameters, fragments,
personal data and arbitrary hosts. Record whether the review URL was observed
or constructed; a constructed link remains unverified. Never forward cookies,
headers, tokens, the owner's invite, or a pre-filled checkout URL.

Use URL buttons: **Open review item**, **View product**, **Reviewer catalog**.
The catalog fallback is `https://www.walmart.com/reviews/claim-product`; ordinary
item fallback is `https://www.walmart.com/ip/<numeric-id>`. Product titles and
images link only to allowlisted destinations. Discord link buttons navigate
directly and do not emit click interactions to the bot [S5]; do not promise
automatic click attribution. Avoid a “Claim now” success implication.

Before publishing the review button, manually open real examples from an
independent invited reviewer account, the owner's account, signed-out browser,
and a non-invited account. Check desktop and mobile/Discord browser behavior,
an unavailable item, and variants. Stop before ordering: a page-opening test
does not require claiming a product. If claim context fails, publish the
ordinary product link plus catalog fallback and state that limitation.

Alert body: full item title; observed retail value (not tax value, cash value or
guaranteed savings); primary category/tags; item ID; first detected and last
observed timestamps; observation age; delay label; image if available; and
“Eligibility and availability depend on your Walmart account.”

The existing `claims_remaining` is the collecting account's allowance. Remove
it from all shared channels, DMs and public status. Product stock and recipient
quota must not be inferred from it. A stale observation must say last observed,
not “still available.”

## Delivery architecture

Keep the browser → loopback relay. Use Python's built-in SQLite for the new
catalog and outbox; do not replace the current seen files during the initial
pilot. Persist to the existing Docker data volume. One worker is sufficient at
pilot scale; use worker leases/transactions if later adding more processes.

Suggested tables:

- `items`: Walmart ID, full title, value in integer cents/currency, safe URLs,
  image, taxonomy/provenance, optional family ID, first/last observed, revision.
- `observations`: item ID, scan/page/query IDs and observed time; bounded
  retention, no browser credentials or account quota in public projections.
- `deliveries`: item/event revision, recipient/destination, release cohort,
  policy version, due/expiry times, status, attempt count, next retry,
  message ID and bounded redacted error. Unique logical delivery key.
- `preferences` and `entitlements`: Discord user ID, opt-in rules, tier,
  validity and payment event IDs; added only when their features ship.

Persist each validated observation batch and all planned delivery jobs in a
transaction. ACK durable acceptance; workers deliver asynchronously. Existing
Telegram filters stay personal and must not suppress Discord catalog ingestion.
Discord and Telegram success/failure need separate destination state; delivering
one must never mark all others delivered. During the first shadow/private slice,
preserve the old Telegram path; before paid launch, decouple its failures from
browser advancement through durable personal jobs and an explicit cutover.

SQLite commits do not provide exactly-once Discord delivery across a network.
Use a stable logical key and Discord nonce/enforce_nonce where supported, save
returned message IDs, and reconcile ambiguous sends. Discord only checks nonce
uniqueness over a short recent window [S6]. A crash after remote success but
before local commit can still duplicate a message; document this boundary.

Compute `release_at = first_detected_at + tier_delay` using UTC server time.
Restarting never restarts the delay clock. Repeated observations update metadata,
not release time. Persist policy version so later tier edits do not accidentally
release the backlog. Recheck entitlement at send time for DMs/private recipients.
Cancellation removes paid roles at paid-through expiry; refunds/revocations
follow the provider's authoritative effective time. Reconcile periodically to
repair missed events and test downgrade combinations.

Use per-route queues, global backpressure, timeouts and Discord rate-limit
headers/Retry-After [S7]. Never hardcode a supposedly universal message limit.
429 retries must not pause browser collection; revoked channels/403/404 go to a
visible failure queue, and one closed-DM recipient cannot block the channel feed.
Avoid unbounded in-memory sleeps for delayed delivery.

Proposed stale policy for the pilot: normal paid jobs expire after 30 minutes
from detection; fresher observation can support an explicitly labeled recovery
release. Before delayed sends, suppress positively observed unavailable items;
absence from a partial keyword scan is not proof of sold out. If no fresh stock
evidence exists, show observation age. The free recap intentionally includes
historical entries, limits to 10 representative families, and never claims they
remain available. Revisit the 30-minute cutoff after measuring useful lifetime.

Track every observed ID internally. Initially preserve separate variant links;
bundle only confidently identified families and retain access to every variant.
Apply tier/value rules **per variant before grouping**, so a mixed-price family
cannot reveal a premium-only variant in Starter. Grouping is presentation, not
destructive dedupe. Price/category updates edit existing messages; do not
reannounce or replay the initial 70k history as new drops.

## Membership and willingness to pay

| Plan | Test price/month | Value scope | Release after detection | Purpose |
|---|---:|---|---|---|
| Free | $0 | Up to 10 representative families in daily recap | Next day | Demonstrate usefulness; community and status |
| Starter | $4.99 | Verified positive value below $40 | 5 minutes | Low-cost filtered entry; one feed |
| Pro | $19.99 | All observed values; unverified values clearly labeled | No intentional delay | Category/value channels; relevant alerts |
| Possible middle plan, later | $9.99 | Below $200 | 2 minutes | Add only if demand justifies another cohort |
| $99 concierge, deferred | Untested | No invented inventory advantage | Same detection source | Only with demonstrated extra service and demand |

The value ceiling and delay are server policies, not Walmart restrictions. At
$40 exactly an item leaves Starter; $200 exactly belongs to $200+. Convert to
cents before applying thresholds, and keep unknown/zero values out of ceiling-
restricted plans until verified. Labels must match these half-open intervals.

Comparable evidence: Ultraviner's indexed official pricing lists $4.99 Basic,
$9.99 Turbo and $14.99 Ultimate; its current live page loads some price content
dynamically. Reconfirm checkout before using exact comparisons publicly [S8].
Its alerts/rules/Discord integration are relevant substitutes, but it serves
Amazon Vine and includes more tooling; it is not proof of Walmart demand.
SmartViner advertises a free beta and alert/filter features [S9]. Public Reddit
discussion contains both payment interest and price resistance [S10]; it is
self-selected anecdote. A targeted search did not establish a directly comparable
paid Walmart reviewer alert service with independently verified demand.

Inference: $5 is a plausible low-friction test; $20 needs proof of better relevance,
reliability and useful claims. $99 is far above the adjacent tools reviewed and
unsupported by this history. Public prices prove offers exist, not how many
people pay or what this audience will pay.

After permission/availability checks, invite 10–20 existing reviewers to a
14-day private pilot (recruitment is a future action). Ask what they currently
use, their last missed item, desired categories, manual search time and whether
five-minute-old alerts still work. Offer actual, cancelable paid beta plans;
interest surveys alone do not validate pricing. Have members voluntarily report
“opened,” “unavailable,” “claimed,” or “not eligible,” without Walmart account
details. These reports are feedback, not automatically verified claims.

Pilot decision thresholds (proposed operating gates, not measured outcomes):
zero permission leaks; >=99% durable jobs delivered or visibly failed/expired;
p95 Discord acceptance within 10 seconds of detection for unthrottled live jobs;
>=95% correct primary tags in a manually reviewed stratified 200-item sample;
10 actual paying beta users and at least 6 renewing into a second month before
scaling. Track time saved, unavailable rate by age, opt-outs, support time and
source uptime. A 14-day pilot cannot establish monthly retention: renewal is
measured after the first full billing cycle. Cap the initial cohort and expand
only while useful-claim feedback stays acceptable; do not sell “less competition”
or seats as exclusive inventory without evidence.

Personal alerts come after the feed: `/watch`, `/unwatch`, `/preferences`,
`/mute`, `/pause`, `/resume`; filter by category, keywords, minimum/maximum value
and exclusion terms. Require explicit DM opt-in, quiet hours with timezone,
match caps, recipient dedupe and an unsubscribe control. Pro can include three
rules during the beta; test an optional $4.99 pack for ten extra rules only if
support and retention evidence justify it. A DM must obey the user's tier and
release time; it cannot become a bypass. Adding a category and commissioning a
custom feature are separate from paying for delivery preferences.

## Billing, feasibility and launch conditions

Native Server Subscriptions are the quickest candidate **if eligible and if
Discord accepts this offering**: paid roles/channels are supported, with 1–3 paid
tiers; creator requirements include US identity/bank, age 18+, verified email,
2FA and account standing [S11]. Discord lists desktop/browser server fees of 6%
processing and a 10% platform fee; net proceeds must be confirmed in onboarding
because other transaction/mobile/tax/refund conditions apply [S12]. Do not model
revenue as gross subscription prices or automatically as “90% take-home.”

If native subscriptions do not fit, evaluate a provider with Discord role
delivery (for example Whop) and confirm its complete current fee schedule,
revocation behavior and permitted offering before selecting it. Whop's base
payments page lists 2.7% + $0.30 domestic card processing, with additional
services/fees; that is not a verified all-in Discord membership cost [S13].
Keep billing out of the custom relay initially. Native paid bot features such
as separately sold DM capabilities may trigger Discord Premium Apps purchase
support and price-parity requirements for supported offerings/regions [S14].
External checkout does not automatically avoid those requirements.

Two concrete source issues precede public sales:

1. Walmart's current Site Terms prohibit commercial use and automated collection
   without express prior written consent for the collection provision [S1]. The
   program incorporates those terms [S2]. Obtain a permitted source/permission
   for the proposed commercial feed before committing to this business. This
   report does not establish that permission. Existing account verification
   pauses make source continuity an observed issue too.
2. Discord's Monetization Policy restricts offerings whose primary purpose is
   third-party marketing/promotional benefit [S15]. Whether this reviewer utility
   qualifies needs confirmation; do not assume a list of Walmart links is approved
   monetized content or that another payment provider resolves this question.

These findings affect paid launch feasibility; they do not prevent completing
the design, offline replay, or reviewable local implementation plan. No legal
clearance, policy approval, demand validation or guaranteed claim access is
claimed. Do not imply Walmart affiliation, program admission, product allocation
or guaranteed savings. The product is independent notification/filtering service.

## Execution and verification

Follow the ordered, scoped tasks in [todo.md](todo.md). First build one private
end-to-end alert with a safe fallback link and durable delivery. Add taxonomy,
roles and delays only after that path works. Production channel provisioning,
billing enablement, public recruitment and mass sending are separate future
actions; this planning request performs none of them.

Use the existing Python/Node stack. Future checks from repository root:

```powershell
.venv/Scripts/python.exe -m pytest -q
node --test --test-isolation=none tests/test_extension_runtime.mjs
.venv/Scripts/python.exe -m ruff check .
Get-ChildItem extension -Filter *.js | ForEach-Object { node --check $_.FullName }
.venv/Scripts/python.exe src/server.py --help
```

Use a fresh permitted `--basetemp` and `-p no:cacheprovider` if the documented
Windows sandbox temporary-directory restriction recurs; do not delete active
runtime data or suppress failing assertions. Tests were not rerun for this
planning-only deliverable. The history calculation executed successfully with
total/band/threshold reconciliation and source hashes. Its notebook code ran
sequentially via a standard-library harness; native Jupyter rendering was not
available. The HTML brief is separately inspected in a browser.

## Sources checked October 21, 2026

- S1: [Walmart Site Terms](https://www.walmart.com/help/article/walmart.com-terms-of-use/3b75080af40340d6bbd596f116fae5a0): commercial use and automated collection provisions.
- S2: [Recognized Reviewer terms](https://www.walmart.com/reviews/terms-and-conditions): invitation, ordering, eligibility, account limits and incorporated terms.
- S3: [Discord permissions](https://docs.discord.com/developers/topics/permissions): role/channel visibility.
- S4: [Discord threads](https://github.com/discord/discord-api-docs/blob/main/developers/topics/threads.mdx): parent permission inheritance.
- S5: [Discord component reference](https://docs.discord.com/developers/components/reference): URL buttons and absence of click interactions.
- S6: [Discord messages](https://docs.discord.com/developers/resources/message): embeds, allowed mentions, nonce/enforce_nonce and limited dedupe window.
- S7: [Discord rate limits](https://docs.discord.com/developers/topics/rate-limits): response-driven backpressure.
- S8: [Ultraviner official pricing/product](https://www.ultraviner.com/): adjacent offer, indexed prices, current dynamic-page limitation.
- S9: [SmartViner official product](https://smartviner.com/): free-beta and alert/filter positioning; seller claims.
- S10: [VineHelper price discussion](https://www.reddit.com/r/VineHelper/comments/1o92mdx/new_connection_limits/): anecdotal price resistance, not representative demand.
- S11: [Discord server monetization setup](https://creator-support.discord.com/hc/en-us/articles/10423011974551-Server-Shop-For-Server-Owners-and-Admins): roles, tier limits and eligibility.
- S12: [Discord Monetization Terms](https://support.discord.com/hc/en-us/articles/5330075836311-Monetization-Terms): fee schedule and other conditions.
- S13: [Whop payments pricing](https://whop.com/network/pricing/): base processing and separate fee categories; not an all-in membership quote.
- S14: [Discord Premium Apps requirements](https://support-dev.discord.com/hc/en-us/articles/23810643331735-Premium-Apps-Required-Support-for-Monetizing-Apps): supported paid app offerings and external payment parity.
- S15: [Discord Monetization Policy](https://support.discord.com/hc/en-us/articles/10575066024983-Monetization-Policy): third-party promotional-purpose restriction.

Local authorities: current extension/Python source, active non-secret saved
rules, merged item snapshot/append log, and read-only Docker log tail. Chrome
was not exposed by the browser connector; no second invited Walmart account or
Discord/payment account was tested. User-supplied URLs establish the proposed
link pattern. No workplace CRM, messages, customer payment data or warehouse
sources were needed or consulted.
