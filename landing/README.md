# Reviewer Alerts landing page and Reddit headline test

Live website: https://reviewer-alerts.pages.dev/

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
