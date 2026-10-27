# Server onboarding copy

Updated October 23, 2026. Existing messages were edited in the dedicated Reviewer Alerts server
after its bot permissions and privacy audit passed. Receipt IDs are in ignored
`data/discord_bridge/onboarding-posts.json`.
This latest copy supersedes earlier hidden/waitlist setup language. The owner
approved real paid signups; successful purchase/access lifecycle tests remain pending.
Visible channel accents are now `💳-welcome` and `🆓-free-24h-alerts`; references
below use their short labels. Live posts link by channel ID and render the current name.

## Free-feed pinned membership card

**⭐ Want earlier alerts?**

Gold embed, title: **Choose the membership that fits you**.

Free: every item, delayed 24 hours.
Starter · $5/month: $0.01–$39.99 items, all categories, after five minutes.
Pro · $20/month: all values, no added delay, category and value feeds.

Footer: USD, billed monthly. Manage or cancel in Whop. Delays start at detection;
delivery times vary. Availability and claims are not guaranteed.

Buttons: Starter · $5/month and Pro · $20/month use the same direct checkout
URLs as #welcome; Compare plans links to #welcome. One pinned post, no pings.

## #welcome

**Choose your membership**

The bot displays this compact comparison inside a native Discord embed:

**Subscribe to Reviewer Alerts**

```text
Plan      /month   Alert delay
Free          $0    24 hours
Starter       $5   5 minutes
Pro          $20    No added*
```

**Starter:** $0.01–$39.99 items, all categories.
**Pro:** All values; category and value feeds.
**Free:** Every item after 24 hours; no payment needed.

**How to subscribe**
Choose a plan below. After payment, open **Activate Discord access** → your plan’s
Discord app → **Connect Discord / Claim Access**. Sign in with the email used at
checkout. Payment alone does not identify the recipient Discord account.

**Changing tiers?**
Starter and Pro are separate subscriptions. Before buying the other plan, cancel
your current plan’s renewal using **Manage subscription** to avoid paying for both.
The new plan is billed separately.

Footer: USD, billed monthly. Manage or cancel in Whop. *Delays start at
detection; delivery times vary. Availability and claims are not guaranteed.

Buttons: **Subscribe Starter · $5/month** → https://whop.com/checkout/plan_k2rVgU36MH7pE;
**Subscribe Pro · $20/month** → https://whop.com/checkout/plan_Ta8OgmuWRX49n;
Second row: **Activate Discord access** → https://whop.com/reviewer-alerts/;
**Manage subscription** → https://whop.com/@me/settings/orders;
**Payment methods** → https://whop.com/billing/manage/payment-methods/.
The same three member-navigation buttons appear on #how-to-use. These are generic
per-signed-in-user links, with no private customer identifiers. The welcome card
remains pinned; readback confirmed the buttons and no mentions.

Both products now have a saved **After checkout** redirect to their respective
product-gated Discord experience (Starter exp_oHhrnaXBiSEMyn, Pro exp_tWRoVrlmCpMiX6).
Reopening each product confirmed the stored URL. Actual post-payment redirect,
member cancellation controls and Discord role delivery still require the member
walkthrough. No checkout setting requiring Discord before payment was found in
the inspected product/pricing options. Do not claim the connection step is gone.

## #greeting

Personal member greetings only; ordinary members cannot post. Exact joining-user
mention allowlist, no roles/everyone/reply pings. The durable worker links #welcome
and #how-to-use. Server default is mentions-only; personal notification overrides
cannot be forced by the owner. Real join/push walkthrough remains pending.

## #how-to-use (stable internal key: how-it-works; public read-only)

**Set up your alerts**

**1 · Pick a feed**
Free: #free-24h-alerts
Starter: #starter-items
Pro: choose category OR value channels, or #pro-all for everything.
Start with one feed to avoid duplicate alerts.

**2 · Turn on notifications**
Right-click a feed (desktop) or long-press it (phone) → **Notification Settings
→ All Messages**. Keep the server on **Only @mentions**.
To stop a feed's alerts, choose **Nothing**.

**3 · Open an item**
Click **Open review item** using your own eligible Walmart account. If needed,
try **View product**. Availability varies.

Need help? Ask in #general. Plans and subscriptions: #welcome.

## #plans-and-help (private operations reference)

Planned access options (paid plans billed monthly in USD):

- Free — every captured item in #free-24h-alerts, released after 24 hours.
  These are historical listings; they may no longer be available.
- Starter — $5: items with a positive observed value below $40, released after
  a five-minute delay.
- Pro — $20: all observed values, with no intentional delivery delay; browse
  price and category feeds.

Starter covers all categories and has no keyword restriction. Values of $40 or
more, zero values, unknown values and items marked unavailable do not enter
Starter. Delay starts when our feed first detects the item, not when Walmart
first lists it or another reviewer finishes claiming it.

Both monthly plans are now visible, Buy now, with unlimited stock. Checkout
explains renewal and cancellation before subscription. Paid access does not
guarantee inventory or successful claims. No third tier or referral program is active.

## #updates (stable internal key: feed-status)

**Updates & announcements**
Service notices, improvements and resolved issues will be posted here.

Compare plans and subscribe in #welcome. For setup and help, visit #how-to-use.
