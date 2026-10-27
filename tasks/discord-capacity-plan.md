# Launch capacity and future priority access

Updated October 21, 2026. Recommendation for review, not a new paid offering.
The accepted configuration remains $5 Starter / $20 Pro. No third tier,
membership cap, waitlist or checkout is live.

Some members may be willing to pay more for earlier alerts. That is a plausible
hypothesis, not evidence of demand. Increasing the audience for a scarce listing
can reduce the usefulness of an alert to each member. Charging more changes
delivery order; it does not create inventory. Buyers also compete with reviewers
outside this Discord, and eligibility can differ between Walmart accounts.

## Recommended first launch

- Keep Starter at $5/month: positive values below $40, five-minute delay.
- Keep Pro at $20/month: all observed values, no intentional delay.
- Open at most 20 founding Pro memberships, with a waitlist for additional demand.
  Twenty is a cautious experiment, not a calculated inventory capacity.
- Run a 14-day pilot, then review the first monthly renewals before a large
  expansion. Admit another small cohort only when usefulness remains acceptable.
- Free members receive every captured item after 24 hours, as historical alerts.

Whop documents a **Stock** setting to limit subscriptions and **Add waitlist**
for accepting members before they subscribe. Configure and test those controls
in the seller account; verify whether stock is restored on cancellation and
whether pending purchases can exceed the intended active-member cap. Do not
assume a cumulative-sales limit is an active-membership limit.
[Official Whop subscription setup](https://whop.com/blog/start-a-subscription-business/).

## Evidence to collect

Use an optional private feedback form that asks whether a member attempted a
claim and whether the item was available, unavailable or ineligible. Capture
the item ID and approximate alert age; do not request Walmart credentials,
order details or screenshots of account pages. Report the response rate and
self-selection bias alongside results. An alert button click is not proof of a
successful claim.

Review successful-use reports, unavailable reports by alert age and price band,
collector uptime, delivery latency, cancellation reasons and actual renewals.
The current history has no stock quantities or claim outcomes, so it cannot
predict how many paying members the feed can support. The captured 70,399 IDs
include only 18 at $200+, including variants; a $99 high-value offer is unproven.
[Captured history](research/item-history-summary.json).

## If demand grows

The first option is to retain immediate Pro delivery and raise the price for
new members, with explicit treatment of founding members. This preserves the
initial benefit while testing a higher price without an extra delivery cohort.

A separate priority tier is another option. An illustrative, unvalidated design
would give Priority immediate delivery, Pro a clearly advertised short delay
(for example 60 seconds), and Starter five minutes. Test whether that short delay
still leaves useful opportunities before selling it. Test an actual price with
eligible interested buyers; $49 or $99 is not validated by this research.
Cap the immediate cohort too, since moving 100 people into Priority recreates
the same competition.

Choose that arrangement before selling, or obtain clear agreement to any change
from existing subscribers. Do not silently slow an existing $20 immediate plan
to manufacture an upgrade. If nobody pays yet, the owner can still choose a
different launch promise deliberately.

## Implementation boundary

### Proposed referral reward (not enabled)

October 22 update: owner accepted ONE free 30-day Pro reward per referrer for
four distinct new paid customers after a 14-day qualification hold, excluding
refunds, then asked whether the concept is worth implementing. Recommendation
is to defer it through the first beta. Terms are accepted; implementation and
activation are not complete. No referral capability should be advertised yet.

Owner asked whether four subscribers to either tier could earn one free Pro
month. This is feasible; no referral offer, affiliate commission or reward grant
was activated by this feasibility review.

Recommended pilot: four distinct new customers who buy Starter or Pro earn
30 days of Pro access. Count each buyer once across both products; exclude
renewals, trials, complimentary purchases, self-referrals and refunded/disputed
payments. Use a clearly disclosed 14-day qualification wait after successful
payment (a proposed business rule, not a guarantee against later disputes).
Begin with one reward per referrer; repeatable rewards require a separate choice.
Reward members count toward any future Pro capacity limit. Four Starter sales
bring $20 gross before fees, so measure renewals and capacity impact before
making the reward recurring.

Whop documents purchase-attributed tracking links and commission-based affiliate
links; the reviewed docs do not establish a native four-referral/free-month
threshold. Keep existing cash affiliates disabled unless separately chosen.
An initial manual approval process can validate attributed first payments; later
automation needs verified payment/refund events, one credit per buyer, replay-safe
reward records and expiry reconciliation. Do not rely on Discord invite counts.
Free reward access must expire without starting a paid renewal. Verify how to
credit existing Pro members and handle overlapping paid/reward access through
Whop before promising that behavior; an expiring role alone can conflict with
Whop's paid-role lifecycle. No implementation or live entitlement changes yet.

Sources: [Whop affiliate program](https://docs.whop.com/manage-your-business/growth-marketing/affiliate-program),
[tracking links](https://docs.whop.com/manage-your-business/growth-marketing/tracking-links),
[expiration-based plans](https://docs.whop.com/api-reference/plans/create-plan).

Current official API research (October 22): native affiliate aggregates do not
prove four distinct qualifying buyers/refunds; do not enable cash commissions
just to count referrals. The supported automation design is business-scoped
checkout configurations with opaque referral metadata, polling authoritative
payments/refunds/disputes, a unique-buyer and unique-referrer reward ledger,
fresh verification before granting, and reconciliation of ambiguous writes.
No public webhook listener is necessary for polling. Existing paid Pro can use
membership extension; free/Starter reward invitations are experimental and need
account enablement. Reward expiry must never remove overlapping paid access.

Exact current scopes: payment:basic:read, payment:dispute:read,
member:basic:read, checkout_configuration:create and
checkout_configuration:basic:read for reconciliation/attribution; member:manage
for extending Pro; membership:create for recipient-bound reward invitations.
Optional reward variant creation needs plan:create. Do not grant payment
charging/refunding, payouts, transfers or Admin. No such key is currently set up.
Sandbox payment tests cannot establish real Discord app role expiry behavior.

Sources: [current checkout API](https://docs.whop.com/api-reference/beta/checkout-configurations/create-a-checkout-configuration),
[payment schema](https://docs.whop.com/api-reference/beta/payments/payment),
[membership extension](https://docs.whop.com/api-reference/beta/memberships/extend-membership),
[experimental recipient invitation](https://docs.whop.com/api-reference/beta/memberships/invite-to-a-membership),
[current affiliates guide](https://docs.whop.com/developer/guides/affiliates),
[API scopes](https://docs.whop.com/developer/guides/auth-scoping),
[sandbox limitations](https://docs.whop.com/developer/guides/sandbox).

### Message volume and Discord rate limits

Channel membership does not multiply bot sends: one channel post serves all
members allowed to read it. Personal DMs would add a send for each recipient and
remain deferred. The current routing creates two to four destinations per item:
Pro all-items, its category, its price band when known and positive, and Starter
after five minutes when the value is below $40. There is also one free historical
destination per item after 24 hours, including zero/unknown/unavailable records.
It does not post an item in every
category or every price channel.

The captured trailing week has 9,227 IDs, about 1,318 per day. Sending each to
three or four destinations could produce roughly 4,000-5,300 daily posts across
paid destinations. The accepted free feed adds about 1,318 historical posts per
day, so an illustrative combined range is roughly 5,300-6,600 posts/day.
This is a planning estimate, not measured live Discord traffic; IDs
include variants and arrival bursts are not uniform. The full 70,399-ID history
must not be replayed into live feeds on first collector connection. Establish a
baseline and explicitly validate fresh-event handling at that integration step.

Discord currently documents a global limit of 50 bot API requests per second,
plus separate route/resource limits. It instructs clients to use response
headers and retry delays rather than hard-code presumed channel limits.
[Official Discord rate-limit documentation](https://docs.discord.com/developers/topics/rate-limits).

The October 21 burst upgrade replaces the serial sender with up to four
independent channels in flight and up to five ready items per message. Full
batches use 80% fewer message posts, so the one-item-per-post figures above are
unbatched planning estimates. Singles send without waiting for a batch to fill.
Connection reuse, shared request reservations, learned Discord buckets and
durable route/global cooldowns govern sending. One throttled channel no longer
blocks healthy channels, and fresh intake wakes the worker immediately.

The full privacy audit still runs before each bounded wave. Exact dispatch
bodies and nonces survive restart, with atomic settlement. Throttle waits do
not consume the actual delivery-failure budget. Starter's five-minute delay,
free history's 24-hour delay and seven-day catch-up window, and the 30-minute
paid expiry remain unchanged. Fair scheduling gives overdue paid routes and
due free history bounded service. Sent/expired jobs are not replayed.
See [implementation and verification](discord-burst-delivery.md).

The offline comparison exercises the actual dispatcher with simulated HTTP;
its quotas and latency are assumptions. Before promising instant paid alerts,
use a small private live pilot to measure queue age, all-items delivery latency,
rate-limit waits, failures and expirations during real bursts. A passing model
does not establish live throughput or zero delivery latency. Retain all-items
as an optional feed; encourage category/price subscriptions and avoid mass
pings. Variant grouping, configurable notification filters and DMs remain
proposed later work.

The bridge already persists each destination's delayed job, but its current
policy implements only Starter at 300 seconds and Pro at zero. A third tier
would require a reviewed policy/schema change, separate private channels and
roles, job migration rules, membership tests and explicit product descriptions.
No Priority role or delay is implemented by this note. Whop controls membership;
the bridge controls message release time. Discord cannot make an already-visible
message appear at a later time to another role in the same channel.
