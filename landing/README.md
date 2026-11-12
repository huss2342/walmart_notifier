# Reviewer Alerts landing page and Reddit headline test

## Fix plan — November 12, 2026

1. Recover the existing static Pages website into `public/` and preserve its
   product examples, legal pages, Discord destination, and Cloudflare project.
2. Restore the actual bot avatar. Make Discord joining the clear primary action,
   show Free's 24-hour delay and optional Pro's $30/month price before joining,
   and add a second join action after the examples. Keep past examples clearly
   labeled; do not present their timestamps as proof of Walmart publication speed.
3. Test two short headlines in the existing campaign, with the same creative,
   audience, landing page, placements, and bidding. Preserve the campaign's $60
   lifetime total, including money already spent; shorten the remaining schedule
   to five days. Verify r/RecognizedReviewer is the only community and expansion
   is off. Prefer lowest-cost bidding.
4. Attribute each ad through a different Discord invite, selected by an allowlisted
   `utm_content` parameter. Count actual invite uses separately from ad clicks.
   No pixel, cookies, or visitor database is needed for this small test.
5. Verify mobile/desktop rendering, images, keyboard access, and invite selection;
   deploy to the existing `reviewer-alerts` Pages project; re-read saved ad settings.

## Acceptance criteria

- $60 remains the existing campaign's lifetime total, never a new $60 allowance.
- No new campaign, expanded audience, invented testimonial, guaranteed claim,
  synthetic notification screenshot, or verified five-second claim.
- Two concise headline variants differ only in copy and attribution label.
- Free joining, delayed Free examples, and optional paid current alerts are clear.
- All invite buttons work without JavaScript; unrecognized parameters retain the
  original invite and cannot become arbitrary external destinations.

## Baseline

Dashboard inspected November 12 with reporting through November 11:
$38.68 spent, 1,108 impressions, 118 clicks, $0.33 CPC, 10.650% CTR.
Campaign `2624276685116702694` already has a $60 lifetime budget and November 30
end date. Reporting is delayed. Remaining reported budget is $21.32 before any
later/unreported spend. Existing editable screen includes a draft duplicate ad.

Source was recovered from the public deployment because no local landing-page
checkout was found. Existing unrelated backend, extension, and mobile edits are
outside this change. `public/` is the complete static deploy directory.

## Checks and deployment

```powershell
node --test landing/tests/*.test.mjs
npx wrangler pages deploy landing/public --project-name reviewer-alerts --branch main
```

Attribution links (both unlimited uses, both at zero uses when inspected):

| Variant | Headline | Invite | Expiry |
| --- | --- | --- | --- |
| A | $100+ Walmart RR alerts on Discord | `5nUpFYyUj` | November 28, 2026 |
| B | Stop refreshing. Get Walmart RR alerts. | `CwFh53XD` | November 19, 2026 |

Each destination uses `utm_source=reddit`, `utm_medium=paid_social`,
`utm_campaign=rr_headline_20261112`, and `utm_content=a` or `b`. The same page and
offer serve both ads. On November 19 at 23:00 UTC, the site stops choosing the
temporary campaign invites and falls back to the existing permanent invitation,
so future clicks keep working. JavaScript-disabled visitors also retain that
permanent link; their joins cannot be attributed to a variant.

Read invite-use totals in Discord **Server Settings → Invites**; compare uses
with per-ad spend for this revision. Invite counts do not identify paid members,
and shared links or repeat joins can affect attribution. Do not label them as
paid conversions or guaranteed unique new users.

Routing security: URL parameters are untrusted. Only exact `a`/`b` values select
fixed Discord URLs; no arbitrary redirect, HTML injection, cookies, storage,
network beacon, or third-party pixel is used. Tests cover hostile parameters,
distinct destinations, and expiry fallback. The static site has no dependencies
or package-manager lockfile to audit.

Use per-ad spend from the start of this revision and Discord invite-use deltas
for cost per join. Creative delivery may be uneven: two ads in one ad group are
a directional comparison, not a guaranteed randomized 50/50 experiment. Five
days is an observation window, not a promise of statistical significance. If
joins are sparse, retain uncertainty and use CTR/CPC only as secondary signals.

## Status

Implementation and verification in progress. Saved campaign state and deployment
URL will be recorded here after read-back.
