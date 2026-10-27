# Discord member security review — October 22, 2026

The owner corrected the earlier all-read-only requirement: #general and
#feature-requests are the two community chat areas. Alert feeds and #updates
stay read-only; owner announcements and bot delivery remain possible.

## Verified controls

- @everyone, Starter and Pro have zero guild base permissions. Category rules
  grant reader access by audience and Send Messages only for COMMUNITY.
- Members lack Attach Files, Embed Links, thread creation/replies, app commands,
  public external-app responses, polls, voice messages, management, invites,
  slowmode bypass and mass-mention permission. Additional ordinary roles may
  grant reader access only and must not be mentionable.
- COMMUNITY has only the two chats. #free-24h-alerts was restored to START HERE
  before the community Send permission was granted. Both chats retain 10-second
  slowmode; missing, malformed or shorter slowmode now fails the runtime audit.
- Exact channel ACL and role audits reject unexpected permissions/overwrites
  or untracked channels. The collector fails closed before delivery on drift.
- Collector bot has only View/Send/Embed/History in managed channels and no
  guild management grants. Verified bound Whop bot retains only the previously
  authorized access-management scope. No new app/credential/bot was installed.
- Server profile is private; Discovery/public preview/widget are off. Default
  notifications are mentions-only. Moderator 2FA and admin-only member pruning
  are enabled, Medium verification is selected, and sensitive media filtering
  applies to all members. Discord notes verification can be bypassed by an
  assigned role when server onboarding is not enabled; it is not an anti-abuse
  guarantee for paid members.

## Native AutoMod

Owner UI saved and enabled:

1. **Text-only community: block links and custom media**: block-message response,
   five regex patterns, no allowlist, no role/channel exemptions. The custom
   notice asks members to describe issues or requests in plain text.
2. **Block Mention Spam**: five unique mentions per message, block-message only.
3. **Block Suspected Spam Content**: block-message only.

No alert-forwarding channel or automatic timeout was enabled. Ordinary Walmart
links are blocked too; there is no substring allowlist that could permit a
malicious URL in a message also containing Walmart. Bot messages remain exempt.

Saved patterns (case-insensitive matching is supplied by Discord):

```text
(?:[a-z][a-z0-9+.-]{1,20}://|www[.。．])\S+
(?:[a-z0-9_\x{80}-\x{10ffff}-]+[.。．])+[a-z\x{80}-\x{10ffff}]{2,}
\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?:[/?#]\S*)?
<a?:[a-z0-9_]{2,32}:\d{15,22}>
<@&\d{15,22}>
```

Discord accepted these patterns; an earlier broad Unicode-class expression was
replaced after Discord rejected its compiled size. These patterns cover common
schemes, bare domains, IPv4 links, GIF-service URLs, custom emoji and role pings.
They can reject domain-looking text and do not defeat every obfuscation.

## Evidence and limits

- Read-only live receipt: `data/discord_bridge/community-security-verified-20261022.json`.
  All 37 resources across Free/Starter/Pro/both combinations passed; actual
  audit returns no findings and guild `mfa_level=1`.
- Native proof: `data/discord_bridge/automod-enabled-20261022.png` shows all three
  enabled rules and five regex patterns. Native Safety Setup separately verified
  the controls listed above.
- Saved channel guidance/slowmode proof:
  `data/discord_bridge/community-slowmode-20261022.png`. Both live topics now
  describe slowmode and the upload/GIF/link restrictions without claiming that
  all possible visual content is disabled.
- Final validation: 978 Python tests pass, Ruff and scoped formatting pass, and
  final live audit passes. A timing-dependent burst test now synchronizes its
  first two independent routes with a bounded barrier instead of relying on a
  10ms fake delay. Deliberately serialized dispatch fails; production delivery
  was not changed. Earlier Windows filesystem/socket failures were transient.
- Nineteen link/custom-media/role-mention and ordinary-text examples pass using
  ripgrep's Rust regex engine; receipt:
  `data/discord_bridge/automod-pattern-checks-20261022.json`. This is an offline
  check, not a substitute for a non-owner Discord client walkthrough.
- Final reviewed runtime image deployed into the existing notifier container.
  Docker health is healthy, port binding remains 127.0.0.1:8787, and authenticated
  health reports live configured with no privacy block, overdue/throttled/failed
  work or monitoring write failures. All 6,672 pending jobs at readback were
  future Free releases. Historical expiry totals are preserved, not replayed.
- Scoped independent review approved after rejecting mentionable ordinary roles.
  Offline permission checks establish permission boundaries, not content-level
  proof that every Discord client/media behavior is blocked.
- Discord-crafted stickers can remain usable even without external-sticker
  permission. Standard emoji, avatars, profiles and personal DMs also remain.
  Do not promise absolutely image-free chat. Preventing uploads is enforceable;
  blocking every visual element while allowing normal chat is not.
- Owner bypasses channel permissions. Admin/Manage Server accounts bypass
  AutoMod; no ordinary member has those privileges. Native AutoMod rules need
  owner review because reading them requires Manage Guild, which the runtime
  bot deliberately lacks. Audit reports that limitation explicitly.
- Actual non-owner member joining, ordinary client link/sticker checks, mobile
  notification choices and Whop upgrade/cancel/refund/access expiry still need
  the prelaunch walkthrough. No public invite or paid launch was performed.

Official references: [AutoMod FAQ](https://support.discord.com/hc/en-us/articles/4421269296535-AutoMod-FAQ),
[AutoMod API](https://docs.discord.com/developers/resources/auto-moderation),
[Discord sticker behavior](https://discord.com/blog/unleash-your-creativity-with-stickers-on-discord),
[role mention behavior](https://support.discord.com/hc/en-us/articles/360039176412-Role-Mention-Permissions-Suppression).
