# Automated membership referral discounts

October 30, 2026, America/Chicago. Owner explicitly authorized implementation
after asking whether the previously placed coming-soon copy actually worked.
Accepted terms: friend gets 30% off their first monthly payment; referrer gets
30% off their next monthly payment. Cash commissions stay zero. Repeated
successful referrals queue successive discounted months without stacking one
bill or introducing a member cap. $30 becomes $21 for one payment; future
configured $99 plans use the same percentage.

## Implementation

- The existing bot guide's stable **Get my referral link** component opens a
  private reply. Guild, channel, authenticated human and bot-authored message
  boundaries are verified. No extra slash permission or public HTTP endpoint.
- Whop's hosted checkout receives the personal `promoCode` parameter. Native
  friend coupons are 30%, one payment, first-time purchasers only, without a
  cash affiliate. Discord-only/manual testers can generate links too.
- Provider-backed `external_account=discord_ID` membership lookup binds billing
  identity. Whop Bot remains the sole paid-role manager.
- A separate account/guild-bound SQLite ledger preserves profiles, historical
  coupon aliases, first-paid-purchase uniqueness, reward queues, submission
  phase, credential fingerprints and invoice confirmations across restarts.
- Applying a reward uses a private random one-use existing-membership coupon.
  Exact intent replay is bounded by Whop's documented 24-hour idempotency
  window and the same credential/version. Unknown older outcomes are held for
  reconciliation. Preparing a coupon alone cannot strand an unknown apply.
- Existing promotions and missing/canceled subscriptions retain pending rewards.
  Native discounted renewal amounts supply actual next-invoice quotes. Catalog
  prices are labeled standard plan prices. No automatic retroactive charges.
- A future plan-scope change creates a new personal coupon alias, retaining
  attribution from old links. Creating the unnamed future live tier, its
  checkout selection and premium-specific access remain its later launch work.

## Configuration and current activation status

**Live and verified for member entry, native coupon settings and checkout.**
Image `1d293c53b9f7` activated healthy October 30 at 23:21 CDT, over exact
`2f76ba56305b`. Nine source files were overlaid, preserving the latest request
fixes, scanner settings, mounts, security, command and all environment values
except the newly enabled referral flag.

Final full Python suite: **2,473 passed, one existing platform skip**. Final
focused referral/request/membership checks: **246 passed**. Ruff, whitespace,
independent correctness/security reviews and a network-disabled Linux candidate
smoke passed. Two concrete review findings (native invoice quotes and bounded
private-button work) were reproduced and fixed before deployment.

Private config: `data/discord_bridge/referral-config.json`. Credential path:
`data/discord_bridge/whop-referrals.env`, never committed or printed. Feature
flag: `DISCORD_REFERRALS_ENABLED`. Readiness is visible in existing authenticated
health as `referrals`; errors do not disable product feeds.

The exact current API scopes are `member:basic:read`, `member:manage`,
`payment:basic:read`, `promo_code:basic:read`, `promo_code:create`. Whop also lets
`member:manage` cancel memberships; the fixed-route adapter exposes no
cancellation, charge, refund or payout operation. The owner explicitly approved
the five-permission persistent key at action time. It was created through Chrome
and stored directly in the private file, never printed or copied to the clipboard.
Windows ACL permits only the owner and SYSTEM. Backend read access was verified.

Native read calls for memberships, payments and coupons succeeded. A genuine
owner referral identity produced a native 30% / once / first-purchase coupon,
without cash affiliate. Chrome checkout confirmed **$21 today, then $30/month**;
no payment was submitted. Clicking the real guide button returned the same
personal link in an owner-only ephemeral reply. Live health confirms referral
polling, the existing Gateway and personal-request sender ready, no error or
exception. Referral SQLite integrity and foreign keys pass; one owner profile,
zero actual paid-referral rewards. No fabricated customer, purchase or claim.

Nine existing messages now have the working button and active copy; IDs, pins,
existing links and mentions were preserved. Six channel descriptions and Whop
copy no longer say coming soon. Independent Discord readback and a fresh Whop
editor reload confirmed saved values. No public posts, announcements or DMs were
sent; the only new interaction was the private owner-button test.

**Provider evidence still pending:** actual referrer invoice application,
renewal, cancellation timing and consumed-coupon detachment await genuine paid
events or a suitable provider test facility. They are enabled/handled in code
and covered with provider fixtures, not falsely reported as live charged tests.
An unresolved or conflicting discount remains queued/reconcilable; it is not
silently discarded or blindly reapplied after the idempotency window. No test
charge/refund or unapproved access change was made.

Private deployment snapshot, rollback baseline, source hashes and offline smoke
are under `data/referral-automation-20261030/`. Copy-only originals remain under
`data/referral-copy-20261030/`.

Final workspace check found a concurrent monitoring-only edit in
`src/notifiers/discord.py` after the referral snapshot. It preserves referral
wiring and was left intact; it is not included in this deployed referral image.
The other eight deployed feature files still match the frozen reviewed sources.
Do not replace that concurrent work with the deployed snapshot. Any later
monitoring rollout must start from the live referral image or retain its modules
and enabled configuration.

## Primary references

- [Current Whop membership lookup](https://docs.whop.com/api-reference/beta/memberships/list-memberships.md)
- [Apply membership promo](https://docs.whop.com/api-reference/beta/memberships/apply-promo-code-to-membership.md)
- [Current promo creation](https://docs.whop.com/api-reference/beta/promo-codes/create-promo-code.md)
- [POST idempotency contract](https://docs.whop.com/developer/api/idempotency.md)
- [Hosted checkout and percentage semantics](https://whop.com/blog/add-promo-codes/)
- [Official current Python SDK](https://github.com/whopio/whopsdk-python/tree/564bda1e655630e14ed7e0094c91b20947f4d8b7)
