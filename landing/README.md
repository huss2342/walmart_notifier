# Reviewer Alerts landing page and Reddit headline test

Live website: https://reviewer-alerts.pages.dev/

The page was redesigned on November 18, 2026; see "November 18 redesign" at
the end. The sections in between record the earlier design and the Reddit test.

## Plan and design contract

1. Lead with the owner's reported posting-to-alert time of approximately six
   seconds and the free Pro trial on joining Discord. No credit card is needed.
2. Keep one past high-value example, the actual bot avatar, two join buttons,
   and one collapsed trial/pricing section. Remove the walkthrough, feature
   list, duplicate pitch, and extra example cards.
3. Run short speed-versus-free-item headlines against the same image, audience,
   landing page, and CTA. Use clean URLs and the permanent Discord invitation.
4. Keep the approved $50 spending plan inside the owner's $60 ceiling, including
   previous spending. Verify all saved settings and the deployed website.

The six-second figure is supplied by the owner; this task did not independently
benchmark Walmart publication timestamps. The past alert example is illustrative
of the alert content, not proof of publication-to-delivery timing. No trial
duration, guaranteed claim, testimonial, or availability was invented.

## Campaign budget and schedule

The owner approved a $50 planning budget to leave 20% headroom under $60.
Final reporting read-back through November 12 showed $39.02 already spent,
1,161 impressions, 121 clicks, $0.32 CPC, and 10.422% CTR. Reporting is delayed.

The original campaign, `2624276685116702694`, was paused. Updating its historical
October 30 start / November 17 end schedule triggered Reddit's validation:
"Budget must be at least 90.00 for a lifetime spend campaign." The start date
cannot be edited on an existing campaign.

The separate campaign spend cap was also unsuitable: Reddit requires a minimum
of $100. No $100 cap was saved. Replacement: a new campaign with campaign
budget optimization on, a $10.98 lifetime budget, and a schedule of November 12
at 7 PM through November 14 at 6 PM, America/Chicago. The reported $39.02 plus
the new $10.98 budget totals the approved $50 plan. Allowing 20% overrun on the
new budget gives approximately $52.20 total, retaining headroom below $60.
Reporting is delayed, so the existing campaign remains paused. The shorter
schedule also meets Reddit's $5/day minimum without increasing the budget.

Keep US targeting, r/RecognizedReviewer as the only community, automated
targeting off, Feed and Conversations placements, Traffic objective, and
lowest-cost bidding. Do not change billing details or add an advertising pixel.

Budget reference: [Reddit Budgets and Bidding](https://business.reddithelp.com/articles/Knowledge/How-much-do-Reddit-Ads-cost).

## Current headlines and permanent invitation

| Variant | Headline | Characters | Discord invite |
| --- | --- | --- | --- |
| A | $100+ Walmart RR alerts in ~6 seconds | 37 | `VzwNcSgWBk` |
| B | Catch free $100+ Walmart RR items | 33 | `VzwNcSgWBk` |

Both ad destinations are now exactly `https://reviewer-alerts.pages.dev/`.
All Join buttons use `https://discord.gg/VzwNcSgWBk`. Discord's public invite
endpoint verified the correct Reviewer Alerts server and `expires_at: null` on
November 13. No new invitation was created for this revision.

The November 12 setup used campaign URL tags to select separate invitations
and compare aggregate joins. A used an existing zero-use invitation
`5nUpFYyUj`; B used the new invitation `CwFh53XD`. The owner requested clean
URLs and a permanent Join link on November 13, so this routing was removed.
Old tagged visits now keep the permanent invitation. The current page loads no
JavaScript; a comment-only `campaign.js` remains for previously cached HTML.

The original B headline, "Walmart RR alerts. Free Pro trial.", was replaced on
November 13 at 8:51 AM CST. B's name is now `RR B | Free items | 2026-11-13`.
The primary image and Learn More CTA are retained. The landing page now leads
its supporting copy with "Free couches, appliances, and $100+ finds."

Without separate invitations, Reddit can compare impressions, clicks, CTR, and
CPC, but this website cannot attribute Discord joins to a particular headline.
Delivery in one ad group may be uneven; this is a directional comparison, not a
guaranteed randomized 50/50 experiment. Do not declare a winner from sparse data.

## Performance snapshot before the November 13 edits

Reddit Ads Manager, November 12–13 reporting range, read before changing B:

| Original variant | Impressions | Clicks | Reported spend |
| --- | ---: | ---: | ---: |
| A: speed headline | 1 | 0 | $0.00 |
| B: free Pro trial headline | 1 | 0 | $0.00 |

Neither headline has evidence of better performance. Both were enabled and
Active; reporting is delayed. The edited ads keep their ad IDs, so distinguish
the pre-edit snapshot from future results using the 8:51 AM CST revision time.

## Verification and release

- Visible copy reduced from 282 to 83 words (approximately 70%).
- Checked 320, 375 with touch emulation, 768, 1024, and 1440 pixel widths.
- No horizontal overflow; avatar and product image load; both join buttons work.
- Keyboard access and console checked; no console errors.
- Four tests pass: both variants keep the permanent invite, current HTML uses
  permanent links without a routing script, hostile/unknown input, future visits.
- Fixed allowlisted URLs only; no arbitrary redirects, cookies, visitor storage,
  network beacons, third-party pixels, or new application dependencies.
- Production returns 200 with CSP, referrer policy, and working self-hosted avatar.
- Current static release: https://2c2d89f3.reviewer-alerts.pages.dev/ (source `04d549f`).
- November 13 production checks: old B tags at desktop width and old A tags at
  375-pixel touch width both keep the permanent invitation; no overflow, loaded
  avatar and product image, zero console errors or warnings, screenshot reviewed.
- Reddit saved one replacement campaign, one ad group, two ads, and two posts.
- Campaign `2633996234531549277` enabled; $10.98 Lifetime, Lowest cost,
  November 12 at 7 PM through November 14 at 6 PM CST verified in View Setup.
- Ad group `2633996234524189314` enabled; campaign schedule inherited,
  United States, sole community RecognizedReviewer, automated targeting False,
  and Feed + Conversations verified from saved settings.
- A: ad `2633996234526107767`, current post `t3_1xv9vd3`.
- B: ad `2633996234526712340`, current post `t3_1xv9vd1`.
- Both edited ads enabled, with the clean destination URL verified in View Setup.
  Both Admin Approval statuses Pending after the November 13 edits. The saved B
  headline and unchanged CTA/image were also checked in the Assets view.
- Original campaign `2624276685116702694` remains paused. Its old $60 budget and
  November 30 schedule were not changed because the attempted edit failed.

```powershell
node --test landing/tests/*.test.mjs
npx wrangler pages deploy landing/public --project-name reviewer-alerts --branch main
```

The static source was recovered from the existing public deployment. Deploy only
`landing/public`. Existing unrelated backend, extension, and mobile edits were
preserved. No source push or PR was performed.

Wrangler's newly created legacy cache directory shadowed the existing login.
Credential discovery was restored with a local junction from
`C:\Users\Navi\.wrangler\config` to the existing
`C:\Users\Navi\AppData\Roaming\xdg.config\.wrangler\config`.
No credential contents were copied or printed. Task-created diagnostics were
preserved in `C:\Users\Navi\AppData\Local\Temp\rr-wrangler-session-diagnostics-20261112`.

## November 18 redesign

The page has one job: a tap on the yellow Discord button.

- Concept: the hero is a phone lock screen at 4:08 AM on Friday, October 30,
  showing the couch alert. Headline (owner's wording, same day): "Stop scrolling
  Walmart RR. We'll alert you instantly." The owner prefers "instantly" to
  "~6 seconds" in the headline; the figure stays in the line below as proof.
  Buttons read "Get free alerts on Discord". The line under it ends "even at hours
  like this", which points at the clock.
- Color: the blue is the bot avatar's (`#0045c2`). Yellow (`#ffcc33`) is used
  only for Join buttons. Phones in dark mode get navy sections instead of white.
- Type: Barlow Condensed ExtraBold for the clock, headlines and prices,
  self-hosted in `public/assets/fonts/` with its SIL OFL license text. Body text
  and the notification use the visitor's system font.
- Proof: a ledger of the three past Pro alerts (October 29 and 30) with their
  alert times and retail values, taken from the earlier page in git history.
  The times are when the alerts were sent, not proof of the ~6 second figure,
  which is still the owner's.
- A docked Join bar shows whenever no other Join button is on screen. It uses
  CSS scroll-driven animations; browsers without them show the bar all the time.
- `<meta name="darkreader-lock">` stops the Dark Reader extension from
  repainting the palette. Without it, white sections turn black and the yellow
  button turns brown for anyone who runs that extension.
- Still no JavaScript and no inline styles; the CSP in `_headers` is unchanged.
- The landing test now accepts two or more Join links instead of exactly two.
- `terms.html` describes the 3-day Whop trial and the $15 founding price, and
  its "Updated" date is now November 18. `privacy.html` only gained the two
  color-scheme meta tags.
- Checked at 320, 360, 390, 820, 1440 and 2560 pixel widths, in light and dark
  mode. Not checked on a physical phone.
- Released to production at https://reviewer-alerts.pages.dev/ with the deploy
  command above plus `--commit-dirty=true`.

## November 24 update: you choose what pings you

Ad visitors assumed joining the Discord meant being pinged for every alert. A
section right under the hero, "You choose what pings you.", answers that. Every
claim was checked against the bot code:

- Price ranges: six value channels in `discord_bridge/layout.py`; the page shows
  two real ones (`💲200-plus` on, `💲0-10` off) as a sample.
- Categories: eleven category channels in `discord_bridge/policy.py`; sample
  `furniture` on, `pets` off.
- Item requests: `/request add item: couch $200+`. The `$N+` suffix and the
  `min_value` option are both real (`request_commands.py`, `request_store.py`);
  a match is sent by DM.
- Auto-claim: "Coming soon" and "Not live yet". Nothing else about it is claimed.
- Value channels, category channels and requests are Pro-only in the bot (Pro
  and an active Pro Trial), stated once: "Price, category and request alerts
  are Pro. Try Pro free for 3 days."
- "Nothing buzzes until you turn one on": the server's default notifications
  are mentions-only (`provision.py`) and alerts never mention anyone
  (`allowed_mentions` is empty in `messages.py`).

Same-day phone cleanup after owner feedback ("cluttered on phones"): the first
version listed all 17 channels as chips, tagged each feature Pro, repeated the
plan details in fine print and added a third Join button. The section is now one
intro line and four compact rows (mark, title, one line, a two-chip sample), with
no divider rules and no Join button of its own; the dock keeps its original
behaviour. At 390 px the section went from 1525 to 682 px (320 px: 1814 to 755)
and the page from 4500 to 3704 px. The Pro tier text is split into four short
lines, price first.

Also changed: the hero join note, the Discord FAQ answer, the Free/Pro tier
lines and the meta descriptions.

`tests/content.test.mjs` reads the bot source and fails if the shown channel
names, counts, command syntax, the Pro line or the coming-soon label drift from
the code, if the section regains a Join button, fine print or a long chip wall,
or if any page gains a script, inline style or `data:` URL. Checked at 320 and
390 px (iframe harness; the Chrome window cannot be narrowed on this machine)
and 2560 px, light and dark.
