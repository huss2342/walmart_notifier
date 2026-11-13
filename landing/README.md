# Reviewer Alerts landing page and Reddit headline test

Live website: https://reviewer-alerts.pages.dev/

## Plan and design contract

1. Lead with the owner's reported posting-to-alert time of approximately six
   seconds and the free Pro trial on joining Discord. No credit card is needed.
2. Keep one past high-value example, the actual bot avatar, two join buttons,
   and one collapsed trial/pricing section. Remove the walkthrough, feature
   list, duplicate pitch, and extra example cards.
3. Run short speed-versus-trial headlines against the same image, audience,
   landing page, and CTA. Attribute joins using separate Discord invites.
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

## Headline test and attribution

| Variant | Headline | Characters | Discord invite |
| --- | --- | --- | --- |
| A | $100+ Walmart RR alerts in ~6 seconds | 37 | `5nUpFYyUj` |
| B | Walmart RR alerts. Free Pro trial. | 34 | `CwFh53XD` |

Both invite counters were zero when inspected, and both allow unlimited uses.
A expires November 28; B expires November 19. Two unused invites created while
preparing the test (`MMwDEJsVt` and `cpsEX3v3`) were removed; the existing
permanent invitation was preserved.

Both ad destinations use `utm_source=reddit`, `utm_medium=paid_social`,
`utm_campaign=rr_headline_20261112`, and `utm_content=a` or `b`. Only the
headline and attribution label differ. The same website and offer serve both.
Primary image pixels were checked and match between the two existing creatives.

On November 19 at 23:00 UTC, campaign routing falls back to the existing
permanent invite so later visits still work. Unknown parameters and visitors
without JavaScript retain that permanent link.

Read invite-use totals in Discord **Server Settings → Invites** and divide each
ad's spend by its invite-use increase. Counts are joins through those links,
not paid subscriptions or guaranteed unique new users. Shared links and repeat
joins can affect attribution. Delivery in one ad group may be uneven; this is a
directional comparison, not a guaranteed randomized 50/50 experiment. With this
small remaining budget, do not declare a conclusive winner from sparse results.

## Verification and release

- Visible copy reduced from 282 to 84 words (approximately 70%).
- Checked 320, 375 with touch emulation, 768, 1024, and 1440 pixel widths.
- No horizontal overflow; avatar and product image load; both join buttons work.
- Keyboard access and console checked; no console errors.
- Three route tests pass: distinct invites, hostile/unknown input, expiry fallback.
- Fixed allowlisted URLs only; no arbitrary redirects, cookies, visitor storage,
  network beacons, third-party pixels, or new application dependencies.
- Production returns 200 with CSP, referrer policy, and working self-hosted avatar.
- Static release: https://ff22fa03.reviewer-alerts.pages.dev/ (source `73be61a`).
- Reddit saved one replacement campaign, one ad group, two ads, and two posts.
- Campaign `2633996234531549277` enabled; $10.98 Lifetime, Lowest cost,
  November 12 at 7 PM through November 14 at 6 PM CST verified in View Setup.
- Ad group `2633996234524189314` enabled; campaign schedule inherited,
  United States, sole community RecognizedReviewer, automated targeting False,
  and Feed + Conversations verified from saved settings.
- A: ad `2633996234526107767`, post `t3_1xutfnq`.
- B: ad `2633996234526712340`, post `t3_1xutfnr`.
- Both ads enabled with correct distinct destination URLs. Both Admin Approval
  statuses Pending and delivery statuses Processing at final read-back.
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
