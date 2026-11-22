# General Pro media and forwarding - November 21, 2026

General permits links, embeds, uploads and media forwards for **Pro** and
**Pro Trial**. Free members retain text-only chat. All 18 Pro alert feeds remain
read-only for every member role. The owner explicitly selected this policy
while investigating failed member forwards; it supersedes the earlier request
to keep all member uploads blocked.

## Cause and repair

The first embed-only change did not solve member forwarding. Discord's native
audit log recorded three General messages from the affected Pro member blocked
by an AutoMod keyword rule at 6:42-6:44 PM Central. The owner bypasses AutoMod,
so a successful owner forward was insufficient evidence for member behavior.

The native rule **Text-only community: block links and custom media** retains
its five regexes, block-message response and empty word allowlist. Its role
exceptions are now exactly Pro and Pro Trial; no Free or channel exception was
added. Native UI read-back after saving confirmed those two roles. The mention
spam, suspected spam and item-request command rules remain unchanged. The role
exception applies to the text-only rule wherever it runs; ordinary typed Pro
links can also pass that rule in other channels where they may type.

General's @everyone overwrite now allows `2147552256` and denies `49152`
(Embed Links and Attach Files). Its Pro and Pro Trial overwrites each allow
`49152`, deny `0`. The bot's member overwrite remains allow `84992`, deny `0`.
No role base or other channel ACL changed. General's topic describes the policy.

The canonical layout and strict audit use the same Pro-only channel exception.
New-install provisioning temporarily needs Attach Files to create those
role overwrites; runtime bot permissions still exclude Attach Files. Live ACLs
were saved through the owner's native UI without granting runtime privileges.

## Verification

Three updated permission regressions failed before implementation. The first
focused run exposed the temporary setup grant requirement; after fixing it,
all 135 provisioning, trial integration and request setup tests passed. The
complete Discord regression suite then passed **1,401 tests** in 109 seconds.
Changed-file lint and whitespace checks pass. The earlier full suite had only
three old trial-fixture failures, corrected before this follow-up.

Six live role combinations, including Free plus either Pro entitlement, pass:
General media allowed only with Pro or Pro Trial; all Pro feeds read-only.
Only General's ACL changed. The deployed source hashes match the repository,
the deployed runtime audit passes, Docker is healthy, delivery runs without an
error, and the request Gateway is connected. Actual member retry is pending;
no public test message was sent by this task.

Evidence: `data/pro-media-20261121/`, plus the AutoMod rejection screenshot in
`data/forwarding-20261121/automod-member-rejections.png`. The first attempt's
historical receipts remain in `data/forwarding-20261121/`.

Rollback must restore channel ACLs and the matching image together. Baseline
`reviewer-notifier:before-pro-media-20261121` contains the earlier embed-only
policy; original ACLs are in `data/pro-media-20261121/channels-before.json`.
Remove the two AutoMod role exceptions to restore its original text-only rule.
No database schema, queue history or membership assignment changed.
