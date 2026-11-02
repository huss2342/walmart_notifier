# Discord category routing and notification defaults — October 28, 2026

## Scope

The owner requested significantly better Discord categorization and quiet Pro
notifications by default, with an exception for very high-value items. Chrome
Discord and Whop inspection was authorized. Clarifications about the high-value
threshold and broad channel organization were requested asynchronously; use the
existing $200+ feed provisionally. Preserve the existing eleven category routes,
membership permissions, personal-request DMs and normal feed delivery.

## Findings

- Discord guild default is already Only @mentions, matching provisioning/audit.
  Product messages have no allowed pings. Members can opt into selected feeds.
- The owner's personal server setting is All Messages. Lower-value feeds, all
  eleven type feeds and pro-all have individual mute overrides. The $80–200
  and $200+ feeds inherit All Messages. This explains the remaining routine
  Pro notifications on this account without a guild-default regression.
- Discord's message-level SUPPRESS_NOTIFICATIONS flag would also silence
  members who deliberately enabled a feed. Do not add it as a substitute for
  quiet defaults. An automatic role-ping feature would require a separate policy
  and permission change; no new pings or roles are part of this slice.
- Chrome's open Whop page showed the Personal home, not an existing business
  dashboard. No Whop billing, integration, access or notification changes made.
- Observed classifier misses include GPS sports watches, electric bikes losing
  to the incidental word tire, artificial Christmas trees, the ride-on train,
  and some decorative/bedding accessories losing to incidental furniture nouns.

## Notification action and approval

The concrete proposed personal change is #💲80-200 → Only @mentions, leaving
#💲200-plus enabled and preserving other existing preferences. Automatic approval
review rejected adding the $80–200 override because it interpreted the channel
as the wrong alert destination. No alternative mutation was attempted. The owner
then explicitly approved: "Yes, quiet $80–200 and keep $200+ alerts". Applied
Only @mentions directly in the $80–200 channel menu; its selected state was
verified and saved in notification-after.jpg. The $200+ channel menu separately
confirmed Use Category Default / All Messages and offered Mute Channel, proving
it remained unmuted. Other settings were preserved. The approval block is resolved.

The current setting is saved at
data/category-improvement-20261028/notification-before.jpg and notification-after.jpg.
Detailed opt-in
guidance was improved in tasks/discord-onboarding-draft.md; this remains a local
draft, not an edited live post. New members still need to enable All Messages
on their chosen high-value channel to receive those ordinary unmentioned posts.

## Implementation and verification status

The classifier now recognizes GPS/sports watches, whole electric bikes and
bicycle accessories, artificial Christmas trees, crib/mattress sheets, beach
towel clips and toy/ride-on trains. A bounded graphic-garment purpose guard keeps
depicted watch/bike/tree nouns from stealing clothing routes. Explicit pet
recipients still win. Storage/frame recipients and toy figures wearing garments
retain their routes. Generic figurine/sculpture additions were removed after
independent review exposed precedence collisions; those speculative rules were
not released.

Final verification: **794 Discord tests passed**, no failures or skips, including
all temporary-database integration cases. **58 new regression cases**; independent
review approved after fixes, with 283 classifier tests independently passing.
Ruff and whitespace checks pass. An isolated network-disabled release container
and the actual running container each passed ten classifier → public projection →
category-route cases and matched the tested source SHA-256:
fe329ffa7b9e671e87e11eeb3c6abd8ac739f741e5b76b69df271a6aba52c0c6.

Final offline audit against the exact deployed baseline compared **40,320**
stored titles and changed **34** outputs. All changed title samples were reviewed.
These are rule-output changes, not a labeled accuracy measurement; historical
variants and obsolete availability are included. Stored titles lack original
query/hint provenance, so those tie/fallback effects cannot be replayed. No
historical rows, queue envelopes or Discord messages were manually rewritten.

## Runtime activation

**Activated and healthy at 11:44 CDT** (16:44:09 UTC). Only classification.py was
overlaid on exact prior image cbba8caf18a8. Final image:
sha256:d6b0153011487aca5e9dcc6906734d8dffe22006737297d17d103e78e43dc851.
Container 88879e2693f1. Environment values, full host configuration, mounts, saved
scanner/relay settings, user, command and stop policy were verified unchanged.
Read-only live Discord audit returned ok:true, findings:[]. New observations use
the classifier without an extension reload.

Release baseline, receipts, logs, audit and screenshots are under ignored
data/category-improvement-20261028. Rollback tag:
reviewer-notifier:before-categories-20261028. An initial build tried interpreting
a bare image ID as a registry tag; it failed without changing the service. The
successful build used the verified local rollback tag for the same exact image.
No owned test processes or new browser tabs remain.
