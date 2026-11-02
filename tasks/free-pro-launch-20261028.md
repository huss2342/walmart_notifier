# Free and $30 Pro rollout

Updated October 28, 2026, America/Chicago. The owner authorized updating Whop
and existing Discord text/channel names. Free remains; Starter is retired; Pro
is $30 USD/month with unlimited signups. No member cap, waitlist, new admission
rule, trial configuration, referral change or outreach is part of this update.
This supersedes the earlier proposed capacity and complimentary-access gates.
Existing individual free-month promises are unchanged.

## Verified Whop state

- Pro product: prod_GsPF7my0RjJtU. New option plan_ADOMT0v8UqKSh is $30/month,
  Visible, Buy now, Unlimited, no trial and no initial fee.
- Checkout: https://whop.com/checkout/plan_ADOMT0v8UqKSh
- Whop refused editing the sold $20 option, so a new option was necessary.
  The same product and Discord app association remain in use.
- Old Starter plan_k2rVgU36MH7pE and Pro plan_Ta8OgmuWRX49n are hidden
  with zero stock. Both hosted direct URLs show Out of stock. Existing test
  purchases are unchanged.
- A separate attempt to hide the Starter product was rejected by automatic
  approval review, which interpreted it as hiding the entire Whop. That action
  was canceled. Product archival was also left unused because the UI says it
  cannot be undone. Retirement uses the verified closed pricing option.

## Discord changes

Four existing bot-owned posts were edited: welcome, how-to-use, private
plans-and-help, and the pinned free-feed card. The Starter buttons and tier
switching copy were removed; Pro buttons use the new $30 checkout. Existing
message IDs and pin states were preserved. No posts, announcements, greetings,
pings, pins or outreach were created.

Welcome and private plan topics match Free/$30 Pro. The old Starter category
is RETIRED FEED and its channel is retired-starter-feed, private to owner and
delivery bot. Existing bindings, queue compatibility and records remain; no
ledger reset, replay, Pro timing or Free 24-hour timing change was made.
Follow-up: the Starter destination is now removed from routing. Startup skips
old pending/leased jobs and frozen dispatches, preserving completed history.
The old channel/role IDs remain only for audit/history compatibility. The live
Starter channel and category were found deleted during final verification;
their absence no longer blocks Free/Pro delivery. Other missing active channels
still fail the audit, and surviving retired resources retain all ACL checks.

## Source and runtime

Canonical pricing/layout, Whop product reference and saved onboarding copy are
synchronized. The initial pricing release overlaid only layout.py and policy.py
on d6b015301148. The follow-up overlaid observations.py, core.py, policy.py and
delivery.py on that exact running image, then provision.py for deleted-resource
compatibility. Final image cca7c831d651 is healthy at 13:02 CDT. Receipts are in
data/starter-retirement-20261028 and data/starter-retirement-audit-20261028.
Both deployments preserved environment, host configuration, mounts,
scanner/relay settings and command.

Verification: 794 Discord cases passed in aggregate. The first suite run had
793 passes and one obsolete CLI price expectation; after updating that expectation
all three CLI cases passed. Live runtime audit returned ok:true, findings:[];
container healthy. Windows sandbox blocked pytest temp folders, so the existing
suite ran with approved elevated execution.

Starter-routing follow-up: a failing regression reproduced new Starter jobs and
resumed legacy deliveries. Final full Discord run: 813 passed. After discovering
the deleted Starter resources, three additional audit regressions were added;
all 130 focused provision/retirement cases passed. Ruff and whitespace checks
pass. Self-review checked exact deployed diffs and preserved source hashes.
An isolated, network-disabled migration of a real queue snapshot preserved all
non-Starter jobs/dispatches and terminal Starter history. Live startup skipped
9 old Starter jobs; final active Starter jobs/dispatches are both zero. Final
live audit ok:true, findings:[]; no test messages were sent. The production
queue was backed up; no ledger reset, replay or channel recreation occurred.

Private receipts and original message backup are under data/pricing-20261028
and data/discord_bridge/pricing-backup-20261028.json. Actual new payments, role
activation, cancellation and expiry were not exercised by this pricing change.
No free trial or automatic billing conversion was added.
