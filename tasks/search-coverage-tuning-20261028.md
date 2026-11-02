# Search coverage tuning — October 28, 2026

The owner authorized investigating missed high-value finds, testing brands and
improving search coverage. Preserve the accepted card-only checks, original
verification helper,100 search terms,15-second pacing and three scanner lanes.

## What was already implemented

The supplied dryer discussion recommended Large Appliance and Kitchen Appliance,
with Maker and Cordless as other useful broad terms. Earlier source/saved-list
changes introduced these broad searches and a few literal star/ampersand trials.
Fresh generated searches populate both q and Walmart's native queryNups field.
Old persisted q-only targets could continue their previous round; the current
priority-list update starts a fresh keyword round with the native fields.

## Measured scan limits

Read-only saved diagnostics and live status established:

| Observation, October 28 CDT | Result |
| --- | --- |
| Main catalogue10:27:02–10:35:34 |17 pages in8m32s |
| Main catalogue10:37:08–10:43:43 |17 pages in6m36s |
| One department10:28:57–10:39:47 |25 pages in10m49s |
| Department scopes flagged at measurement |13 of26 completed |
| Auxiliary targets |37 departments and100 keywords |

Native page transitions had median duration about2seconds. Rendering, settling,
ingest, selected-tab turns and user activity also contribute to page intervals;
logs do not separate them well enough to justify a pacing/controller change.
The25-page flag means coverage may be limited; it does not establish that hidden
items exist. A five-minute refresh setting applies after a complete auxiliary
round, so it is not a five-minute revisit promise for each department or term.

Brand discovery currently runs within the department lane. It appends scoped
brands after base department targets, toward the2000-target cap, before the next
round can start. Enabling it also changes the department fingerprint and resets
that round. No evidence justifies turning on this entire expansion for speed.

## Bounded native Chrome trials

Used one separate manual reviewer tab. No product-page checks, claims, purchases,
CAPTCHA input, background scraping or saved brand-setting changes were made.

| Search/filter | Observed direct group and first-page findings |
| --- | --- |
| Large Appliance |2 direct matches, both OOS;205 suggested options reported |
| Dryer |1 direct match: a$16.99 herb-drying rack |
| Ktaxon |1 direct match: a$12 Halloween decoration |
| Large Appliance& |5 direct matches,3 available Free cards;137 suggestions reported |
| Native BDQ brand filter on the suffix search |1 known$69.99 butter churn, no incremental ID |
| Maker |10 direct matches; bounded DOM summary captured7 available Free cards |

The plain and ampersand first rendered card summaries each held37 unique IDs,
with34 shared. The ampersand-only first-page entries were butter churn20867213576
($69.99), fan20312315042($6.99) and fan20830471139($18.99). These may appear on later
pages or under other searches; whole-catalogue exclusivity is not established.
The suffix modifies results in this session; it is not a documented wildcard
contract. Native search retained stale q while updating queryNups, confirming
why the effective native field matters.

No exact pictured Ktaxon dryer or Domars watch title exists in the read-only
40,283-ID collector snapshot. Eight other Ktaxon IDs exist.80 dryer-titled records
are mostly parts/filters/hair/boot products, with no match to the pictured$299.99
dryer. Current tests cannot establish earlier stock or cross-account eligibility.
The stored collector projection does not retain per-query yield provenance.

## Applied improvement

Only the default/saved keyword priority changed. Maker moved from position82 to3
and Appliance from53 to4. First12 now:

1. Large Appliance
2. Kitchen Appliance
3. Maker
4. Appliance
5. Cordless
6. Large Appliance&
7. Kitchen Appliance&
8. Ride On
9. Sports Watch
10. AMOLED
11. Large Appliance*
12. Kitchen Appliance*

All100 existing terms remain. Custom lists retain their supplied order; the
legacy62-term High Value mode is unchanged. Atomic ScannerSettingsStore save
preserved every field except auxiliaryKeywordQueries; read-back matched.
Backup: data/search-priority-before-20261028T160018Z.json.

Live status confirmed the reordered100-term list, keyword round15 starting Large
Appliance, and q/queryNups both populated on page2. This settings change is active
under2.26.2 without another reload. Pending source2.26.3 defaults now agree; its
previously prepared logging feature still requires its own activation reload.

The priority regression failed before the change;43 focused tests and all778 JS
tests pass. Only extension/sweep-modes.js and tests/test_high_value_search.mjs
changed in source. No backend deployment, timers, verification/stock logic,
department schedule or broad brand toggle changed. Independent review approved;
manual trial tab closed. This improves first-pass ordering without shortening
the entire100-term round or guaranteeing arrivals ahead of other reviewers.
