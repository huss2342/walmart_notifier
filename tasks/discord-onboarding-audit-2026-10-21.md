# Reviewer Alerts onboarding review

Reviewed October 21, 2026. Scope: live owner view, Whop Starter preview,
notification defaults, and existing member instructions. No paid checkout or
ordinary-member lifecycle test was performed.

The setup is quiet by default and has tier-based access. It is not yet a guided
notification preference wizard or a verified end-to-end customer onboarding flow.

## Flow and findings

1. **Whop plan -> Discord connection: partially verified.** The native Discord
   app shows a Connect Account entry point. Starter preview shows one Discord app;
   Admin sees both product-gated instances. Actual account linking, joining and
   Starter/Pro role assignment need a regular member test. Both owner entries
   remain named Discord; an optional name edit failed and was canceled.
2. **Discord arrival -> notification guide: improved and verified.** The existing
   welcome now starts with choosing alerts and links to Notification guide and
   Plans and access. The guide button successfully opens #how-it-works. Live API
   readback confirms saved text and buttons, without product mentions or access
   changes. Screenshot evidence: ../data/discord_bridge/onboarding-06-tier-choices-after.jpg.
3. **Choose feeds -> receive alerts: manual setup, not fully verified.** Pro can
   turn on category or value channels individually. Starter has a single combined
   feed for positive values below $40, delayed five minutes; no finer filters.
   Free has a single history feed delayed 24 hours from first detection. The
   server defaults to mentions-only and product messages contain no pings.
   Discord/device settings control actual push delivery. Choosing multiple feeds
   can notify twice for the same item; category/value feeds do not form an AND
   filter. A custom tier-aware picker has not been built.

## Changes applied

- Updated #welcome and #how-it-works from tasks/discord-onboarding-draft.md.
- Added the two welcome URL buttons; kept all mentions disabled.
- Verified the live privacy audit with zero findings.
- No personal notification settings, security grants, billing plans, product
  visibility or checkout availability were changed.
- API receipt: ../data/discord_bridge/onboarding-notification-guide.json.
- Browser screenshot reviewed after save: ../data/discord_bridge/onboarding-06-tier-choices-after.jpg.
- Earlier fresh evidence: onboarding-02-starter-preview.jpg shows Starter's single
  entry; onboarding-03-welcome-before.jpg shows the previous welcome. Screenshots
  use the actual narrow in-app viewport; they do not prove mobile usability.

## Before accepting paying members

| Task | State / owner |
| --- | --- |
| Seller identity/business verification and payout method | Owner completes sensitive verification in Whop |
| Free/Starter/Pro joining, upgrade/downgrade, cancellation/expiry, failed payment and refund access | Unverified; test with ordinary member accounts |
| Selected-channel notifications on desktop and a phone | Unverified; confirm no unwanted broad alerts |
| Walmart review buttons from a second eligible account | Unverified; alert does not guarantee access or stock |
| Reliable collector hosting, queue/backlog monitoring and restart recovery | Local collector works; ongoing uptime plan remains necessary |
| Program rules and commercial use of this paid offering | Unresolved before sales |
| Small beta, then product publication | Products remain hidden and waitlist-only |

## Source checks

- [Discord notification settings](https://support.discord.com/hc/en-us/articles/215253258-Notifications-Settings-101): per-channel overrides and mobile push settings.
- [Discord Community Onboarding](https://support.discord.com/hc/en-us/articles/11074987197975-Community-Onboarding-FAQ): native channel/role personalization has Community/default-channel requirements; it was not enabled for this private layout.
- [Whop Discord integration](https://whop.com/blog/link-whop-to-discord/): membership-based Discord access and roles.
- [Whop payouts](https://whop.com/blog/getting-paid-on-whop/): payout destinations and verification/compliance checks.
