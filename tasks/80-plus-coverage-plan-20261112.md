# $80+ coverage: analysis and plan (2026-11-12)

Goal: nearly every $80+ reviewer offer reaches the owner within seconds of
becoming claimable. Constraints carried over: no feature removed, highest-value
page 1 stays the fastest read (10 s) and the other $80+ pages second (20 s),
never fetch product pages, backend before extension, owner approves any rise
in Walmart load.

Written for execution by Sonnet 5.5. Phase 0 decides between designs; do not
start Phase 2 or later before its results are in this file.

## 1. What counts

An event is an $80+ offer becoming claimable:

- first arrival: a new listing, an existing product joining the program, or
  first stock;
- restock: back in stock after being out.

Metrics (add to `tools/lane_yield.py`, Phase 1):

- M1: share of $80+ first sightings made by a fast lane (value pages, newest
  page 1). Target 98%.
- M2: in-stock $80+ offers seen in the last 6 h only outside fast lanes, with
  names. Target 0 to 1.
- M3: restocks per day (method in F7) and how many were alerted.

## 2. Baseline (measured Nov 10 to 12; 2.30.4 live since 13:51)

| Fact | Value |
|---|---|
| $80+ first sightings | ~39/day (92 in 57 h) |
| In-stock $80+ offers on value pages 1-12 now | 59 of 64 (92%) |
| Value page cadence (2.30.4) | p1 every 11 s, p2-4 every 22 s, stray p10 every 22 s |
| Total direct reads | 31.5/min, 0 blocks |
| $80+ offers (in stock or not) on value pages | p1 18, p2 29, p3 22, p4 1, p10 1 (out of stock) |
| $80+ restocks, evidenced | ~12/day, none re-sent |
| Catalogue | 16,141 items in 13 department targets |
| Items beyond the 25-page department cap | 9,086 (56%), in 6 departments |

## 3. Findings

F1. The highest-value list chooses page members by one price and displays them
by another. All 37 recent posts of pages 1-9 are in strict valued-at order
within the page, yet pages overlap: page 3 starts at $96 after page 2 ended its
run at $90.89, page 4 starts at $87.56 after page 3's $79.99. Page 1 holds 30
offers, only 18 of them $80+; its last eight ($63.97-$79: five Weatherwood
stain colours, two ostomy belts) are products listed high but offered as a
cheaper variant. Most likely key: the product's listing (primary variant)
price. The mirror case, products listed cheap but offered as an $80+ variant,
sits deep in the list. Phase 0 E1 confirms the field.

F2. Deep $80+ offers now (in stock in the last 6 h, never on value pages 1-12):
two faux fur coats $159.99, Keyo grill $129.99, school supply boxes $119.99 (in
stock 3+ h), StupidFrog pressure washer $94.99. 5 of 64 (8%).

F3. Nov 10-12: 29 $80+ first sightings were never posted by a value read. 13
are traceable: 9 found by member or priority keyword searches, 4 by department
reads on department pages 17-21 (so present in unsearched lists, just deep).
Values up to $449.97 (ride-on tractor), $424.99 mattress, $233.99 sofa bed.
The other 16 predate lane labels (Nov 10 before 05:00).

F4. `sort=new` follows product creation order: of 82 distinct items ever on
newest page 1 since Nov 10, 81 have product IDs of 21 billion or more. An
existing product that joins the program or gets stock never reaches it, and
most $80+ arrivals are of that kind (only ~33 distinct items/day pass through
newest page 1, at any value).

F5. Most catalogue items are in stock (value pages 2-4: 29/32, 32/34, 34/36
offers in stock; page 1: 17/30), so an "in stock only" filter would barely
shorten any list.

F6. Department reads cannot reach 56% of the catalogue (cap 25 pages = 1,000
items per target) and run on leftover budget (~2-4 reads/min, a ~180-page round
takes about an hour). That is why deep offers surface hours late.

F7. Restocks: about 12/day $80+ offers return to pages 1-4 after 15+ min away
while their own page was posted without them. 19 of 29 returned in 2-minute
bursts (Walmart flipping batches), and most stayed hours afterwards. The
backend makes one job per item per route ever (`ON CONFLICT DO NOTHING`, 30-min
job life), so restocks are never sent.

F8. Shipping vs pickup: the owner switched Walmart to Shipping about 14:05.
Through 14:06 nothing changed in value pages (offer and $80+ counts) or
department totals (watch running). The 4 deep offers found by department reads
were in unsearched lists, so at least those were not hidden by pickup mode.
Phase 0 E6 records which page variables carry the setting.

F9. Earlier "out of stock filtered before ingest" holds: `background.js`
drops `available === false` before posting, so `first_seen` is the first
in-stock sighting and job expiry is correct.

## 4. Retrieval options considered

| Option | Verdict |
|---|---|
| `sort=price_high` (current) | Holds 92% of in-stock $80+ offers on pages 1-4 (+ stray pages). Keep as the fast core. |
| `sort=new` | Creation order (F4). Catches new listings only. Keep for those. |
| `sort=price_low` | Unknown: unpriced offers may sort first. E3. |
| Default order (no sort) | Unknown; E6 records it. |
| `min_price=80` filter | Decisive if it filters on the offer value or any variant: then a 2-3 page list holds every $80+ offer. E5. |
| In-stock filter | Little use (F5). |
| Larger page size (`ps`, `limit`) | Would make a full sweep 2.5-10x cheaper. E2. |
| Search by item ID | Per-item watch for known deep offers, 1 read each. E4. |
| Page JSON/GraphQL call behind page 2 | Smaller replies, maybe larger pages, but a new traffic pattern. Only if E2 fails and the owner agrees. E7. |
| Product pages | Never (standing rule). |

## 5. Phase 0: one minute of reads decides the design

Preferred: owner connects Claude in Chrome so the agent can run and iterate.
Fallback: owner pastes `data/discovery-speed-20261112/retrieval-check.js` into
DevTools Console on a signed-in walmart.com tab. It reads at most 18 list
pages 3 s apart, stops at any bot check, and saves
`Downloads/walmart-sort-check-v2.json`.

| Test | Read | If yes | If no |
|---|---|---|---|
| E1 sort key | price_high p1-3, all price fields | Confirms F1, names the field | Re-examine F1 |
| E2 page size | `ps=100`, then `ps=400` or `limit=100` | Sweep uses the largest honoured size | Sweep at 40/page |
| E3 unpriced first | `sort=price_low` p1 | Add price_low p1 as a fast lane | Drop |
| E4 lookups | `q=<id>` / title for the 4 deep offers | ID watch possible (Phase 3.3); their price fields explain F2 | Title watch or none |
| E5 price filter | `min_price=80` with price_high and price_low | Phase 5: direct $80+ list at 10-20 s | Filter usable only for windows (Phase 2.1) |
| E6 setting | variables, default page | Intent guard (Phase 1.2), maybe a URL override | Guard by totals only |

Record results in section 9 before continuing.

## 6. Plan

Phase 1: measure and guard (no extra Walmart load)

1. `tools/lane_yield.py`: an "$80+ coverage" section computing M1-M3 with the
   methods used here (first-finder lane per item; offers seen only outside
   fast lanes; restocks = gaps over 15 min where the item's own page, or a
   neighbour, was posted without it at least 4 times). Self-test.
2. Intent guard (after E6): `fastRead` returns the setting variable;
   `fast-reads.js` logs it when it changes; popup and diagnostics flag
   anything other than Shipping. Allowlist new labels in
   `src/browser_diagnostics.py` (deploy first) and `extension/diagnostics.js`.
3. Value reads also log the in-stock count per page (2.30.4 logs `count` of
   $80+ offers, in stock or not).

Phase 2: every item reachable, swept on a clock (the backstop)

1. Reach: split each department target over 1,000 items so every item sits
   on a readable page: price windows (`min_price`/`max_price` on the listing
   price) if E5 shows they work with `catId`, otherwise sub-category catIds
   from the page facets. `fastReadUrl` must accept the window parameters.
2. Sweep lane replacing the department filler: round-robin over all pages of
   all windows at a fixed rate R, page 1 of each window first (fresh totals),
   pages that held $80+ offers in the last 7 days twice as often. Uses the
   largest page size E2 allows.
3. Budget, staged with owner approval (D2): ceiling 31.5 -> 36 -> 40
   reads/min, one day each, automatic fallback through the existing block
   backoff and 24 h safe mode. The rise goes to the sweep (R about 8-12/min).

Sweep period for 16,141 items: 40 per page ~404 reads (~40 min at R 10);
100 per page ~162 (~16 min); 200+ per page ~81 (~8 min).

Phase 3: $80+ registry and hot pages (fast for known deep offers)

1. Keep in extension storage every $80+ offer seen by any read, in stock or
   not: item, value, list and page where seen, last seen in and out of stock.
   Cap ~2,000 entries.
2. Hot pages: any page holding a deep $80+ offer, in stock or awaiting
   restock, is read every 20-60 s (generalise 2.30.4's stray pages from
   value pages 5-12 to every list; cap and budget-aware).
3. If E4 shows ID search works: per-item watch for deep $80+ offers awaiting
   restock every 1-3 min, at most ~10 items.

Phase 4: restock alerts (owner decision D1)

1. Extension marks a posted item `restocked: true` when a read after its last
   in-stock sighting showed it out of stock. That is evidence of an actual
   restock, not a gap in reading.
2. Backend: for value of $80 or more, a restocked sighting after 15+ min away
   queues a new Pro job with key `v1:{item}:{route}:r{n}` and a "Back in
   stock" line, at most one per item per 6 h. Personal DMs only if the owner
   wants. Expect ~12/day, in bursts.

Phase 5: direct list (only if E3 or E5 succeed)

Add the list as a rank-0 lane at 10-20 s, ahead of everything but value page
1. Then trim Phase 2's rate and Phase 3's hot pages to what M2 still shows.

## 7. Owner decisions

- D1 Restock alerts: yes/no; Pro channel and/or DMs; 15 min away, 6 h cooldown.
- D2 Budget: staged rise to 40 reads/min for the sweep.
- D3 Phase 0: connect Claude in Chrome (preferred) or paste the snippet.

## 8. Expected outcome

| Event | Per day | Now | After |
|---|---|---|---|
| $80+ first arrival on value pages | ~34 | within 10-20 s | same |
| $80+ first arrival listed deep | ~5 | minutes to hours, some never | within the sweep period (8-40 min by E2); 10-20 s if E3/E5 succeed |
| Restock of a known $80+ offer | ~12 | never sent | within 10-20 s (value pages) or the hot-page cadence (deep), if D1 |
| Deep offer in stock under 3 min | rare | missed | only a direct list (E3/E5) catches these |

Overall share of $80+ events alerted within a minute: about 70% today
(first arrivals only, restocks never sent), about 94% with Phase 4, 97-99%
with Phases 2-3, near 100% if Phase 5 applies.

## 9. Phase 0 results (research reads, 2.31.0, Nov 12 14:42)

Run through the extension's own reader (`data/research-results.jsonl`, 36 reads).

- E5: `min_price=80` is the answer. 262 items, 7 pages; 71 of the 72 $80+
  offers seen in any list, both hidden ones included (school supply boxes
  $119.99 on its page 6, a $111.99 fiberglass kit on its page 2). It is not a
  valued-at filter (sorted low to high, its page 1 is $7-$30 offers): it keeps
  listings with a version at $80 or more. Missing: one ordinary listing (PG air
  filter $87.56) that `price_high` page 4 carries.
- Hidden $80+ offers are VARIANT listings (181 of 231 in the min80 list vs 48
  of 133 in `price_high` pages 1-4).
- E1: no price field in the page data explains the `price_high` order; page
  tails are mostly out-of-stock offers.
- E2: `ps=100`/`400` return 16 items; `limit` ignored. No bigger pages.
- E3: `sort=price_low` pages 1, 2, 25 hold $0.79-$8 offers. Useless for $80+.
- E6: `affinityOverride=default` (what the Shipping setting adds to the owner's
  own URLs) changes nothing in any list; `best_seller` equals the default order.
- E4: ID searches find a listed item (school boxes); the coats, grill and
  pressure washer had left the program.

Follow-up (owner's idea): `min_price=79` holds all 72 $80+ offers, the $87.56
air filter included, in 287 items / 8 pages (+1 page); 70 -> 9 pages, 60 -> 12.

Decision (2.32.0): keep `price_high` page 1 at 10 s (now ranked ahead of
everything) and pages 2-4 at 20 s; add the `min_price=79` list as lane
`value80` (pages holding an $80+ offer 40 s, others 3 min); drop the stray
pages; spacing 1.4 s (about 37 reads/min, from 31.5). Restock alerts shipped:
$80+, away 15+ min, once an hour per item, "Back in stock".
