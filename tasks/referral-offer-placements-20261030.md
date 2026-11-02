# Referral offer placements

Historical copy-only stage. The later [live automation rollout](referral-automation-20261030.md)
supersedes the coming-soon status below; those same cards/descriptions now have
working referral entry and active instructions.

October 30, 2026, America/Chicago. The owner authorized placing the agreed offer
in multiple existing surfaces. This authorizes copy placement, not activation
of the still-unimplemented referral billing program.

Published copy: **Refer a friend · coming soon.** The friend gets 30% off their
first month; the referrer gets 30% off their next month when the friend
subscribes. Personal links are explicitly unavailable until referrals open.

Verified changes:

- Edited nine existing bot-owned messages: welcome card, how-to-use guide,
  pinned free-feed card, and six existing Pro activation cards. Preserved message
  IDs, existing buttons, pin state and existing mentions. Allowed mentions were
  disabled for edits; no new message, DM, announcement or ping was sent.
- Updated six channel descriptions: welcome, how-to-use, free-24h-alerts,
  pro-all, furniture and 200-plus. Each has one short referral reminder pointing
  to how-to-use. Corrected a stale Starter/$20 reference in the free description.
- Updated the Whop Pro product description through Chrome. Reopened the editor
  and verified the exact saved description. The active product remains $30/month.
- Synchronized the existing onboarding-copy document. The automatic activation
  renderer was not changed or redeployed by this task; the six existing cards
  were edited in place. Future automated wording belongs to the referral rollout.

Verification: independent Discord API readback confirmed all nine messages and
six topics, with no duplicated reminder text. Whop description was checked after
closing and reopening its editor. Whitespace checks passed. No implementation
tests were needed for existing-message/topic edits and documentation.

Private originals, exact edit helper, sanitized verification receipts and visual
proof: `data/referral-copy-20261030/`. Existing message/topic backups permit
removing just these additions later. Billing, commissions, coupons, membership
roles and notification preferences were not changed.

The [implementation plan](plan.md) and RA checklist remain open for actual
referral links, invoice discounts and future-tier integration.
