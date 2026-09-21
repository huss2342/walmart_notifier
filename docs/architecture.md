# Architecture notes

## Why this runs locally and not in the cloud

An earlier version of this ran on Azure Functions with Table Storage and Key
Vault. That was the wrong shape for the problem.

The reviewer portal can only be read from a signed-in browser session, so a
machine here has to be awake and running Chrome for anything to be read at all.
Once that is true, a cloud function is not buying availability — it is buying a
second place for the same work to happen, plus a deployment step, plus a bill.
The dedupe table was the only thing that genuinely lived there, and it is a few
hundred kilobytes of JSON.

What the move actually removed: a Bicep template, a deploy script, a function
key, a Key Vault round trip, a CORS failure mode, and the monthly cost. What it
gave up: alerting while this machine is off — which was never real anyway, since
the browser tab has to be open for items to be read.

The notifier normally runs in a small Docker container on that same machine.
Docker is only a process supervisor and visibility layer here: Chrome and the
signed-in reviewer tab remain on the host, while `data/seen.json` and
`data/rules.json` are bind-mounted from the repository so replacing the
container cannot erase them. The host publishes the service only at
`127.0.0.1:8787`, so changing Wi-Fi networks does not affect the extension and
does not expose the listener to that network.

## Why there is no credential-login source

The obvious design — store the Walmart password, sign in on a schedule, scrape
the reviewer page — is missing on purpose.

| | Credential login | Browser extension |
|---|---|---|
| Walmart credentials stored | yes | none |
| Requires disabling 2FA | yes | no |
| Contacts Walmart servers | yes, from a datacenter | only pages you opened |
| Survives bot detection | no | n/a — real browser, real session |
| Breaks when markup changes | yes | degrades |

Walmart runs PerimeterX/HUMAN and Akamai bot management, which fingerprint the
browser before the page renders. A headless-Chromium login loop from a
datacenter IP on a perfect cadence is close to a worst-case signature. Getting
past that needs residential proxies and fingerprint spoofing — expensive,
fragile, and squarely the kind of evasion that turns a gray-area ToS issue into
a deliberate one.

Disabling 2FA is the costliest step and buys nothing: the account holds saved
payment methods and addresses, and every path here works with 2FA left on.

## Reading the page

The extension's content script is the primary extractor, and the page fights it
in three specific ways.

**Two prices per card.** A clearance item renders `$5.99 Was $6.99` inside the
card's own link text and `Free (Valued at $5.99)` below it. Only the second is
the retail value the rules mean. Reading the nearest dollar amount gives the
sale price — wrong for exactly the discounted items most worth alerting on.

**The grid is as close as the card.** Walking up the DOM looking for a price
finds the enclosing grid soon after it finds the card, and the grid contains
every item's value. So the walk stops at the first ancestor containing
*exactly one* `Valued at` — more than one means it has climbed too far, and
the item yields no value rather than a neighbour's.

That boundary test deliberately does not require the dollar sign. Some listings
render `Free(Valued at )` with no figure at all; keying on `$` made those cards
invisible to the walk, so it climbed into the grid, found many, and dropped the
item outright. Matching the phrase instead surfaces them with a null value and
lets `alert_on_unknown_value` decide.

**Titles carry noise.** The card link's text is
`Clearance <title> $5.99 Was $6.99`, and the `aria-label` often matches. The
image `alt` is cleanest, so it is tried first, then the badge prefix and any
trailing price are stripped from whatever is used.

A card the walk cannot resolve is **not** dropped. It is emitted with a null
value so `alert_on_unknown_value` decides. Dropping it was the original
behaviour and it cost a real $79.99 item: fourteen of fifteen cards on that
page were captured, the fifteenth vanished, and nothing anywhere recorded that
it had happened. A spurious buzz is much cheaper than a silent miss.

Out-of-stock items are dropped: a notification for something that cannot be
claimed is pure noise. `Free items remaining: N` is read once per page and
attached to each item, because zero claims left decides whether an alert is
actionable.

`src/sources/parsing.py` remains a separate extractor for raw HTML used by
non-HTTP and legacy callers. The HTTP `/ingest` contract accepts only UTF-8
`application/json` and validates the complete item array before processing it.
Malformed, oversized, or partly invalid batches return an error; they never
degrade to an empty successful relay that lets the browser advance past data
the server did not understand.

## Price association in the markup fallback

Two failure modes it is built to avoid, both covered by regression tests in
`tests/test_parsing.py::TestPriceAssociation`:

1. **Prices bleeding across items.** A naive "look within N characters of the
   link" window straddles two cards and hands item N the price of item N-1.
   Fixed by treating item links as segment boundaries: an item's text runs from
   its own link to the next item's link, and the lookbehind is clamped to the
   preceding link.

2. **Assuming which side the price is on.** Some templates put the price after
   the title link, others before it. Choosing per-item by proximity fails on
   table layouts, where the previous item's price and the next item's price are
   *both* immediately adjacent to the link. Orientation is a property of the
   template, so it is decided once per document — whichever side yields prices
   for more items wins — and applied uniformly, with the other side as a
   per-item fallback.

## The extension's refresh loop

The service worker is a coordinator, not merely a timer. It sorts eligible tabs
by window, position, and tab id, leases the first reachable tab as the sole
active reviewer, and tells every other eligible tab to remain passive. The
lease includes a generation number. Reports from a former document or a tab
that lost the lease are rejected even if they finish after a reload or failover.
The current active tab stays sticky while it remains reachable, so opening or
moving another tab does not interrupt a sweep.

The reload schedule lives in `background.js`, not in the content script. A
content script dies with its page, so a reload landing on a sign-in redirect or
a bot-check interstitial could otherwise end the loop permanently and silently.
A one-shot `chrome.alarms` alarm is always re-armed. It probes the active tab,
defers while a sweep or user interaction is in progress, repairs stale sweeps,
reloads an eligible tab whose content script disappeared, and selects a passive
fallback if the active tab is no longer usable.

Relaying a page is transactional from the browser's point of view. The content
script does not add the page to `visited[]` until the service worker receives a
successful server summary under the same lease generation. A network error, an
HTTP error, a failed notification, or another request still delivering an item
is a negative acknowledgement. The same page stays open and retries after
jittered delays of 2, 5, 15, 30, then at most 60 seconds.

The worker posts to a different origin than the pages it reads, so the manifest
permits only `http://127.0.0.1/*` and `http://localhost/*`. Fetches explicitly
use those literal loopback hosts and do not set `targetAddressSpace`: current
LNA calls that address space `loopback`, while older PNA-era Chrome called it
`local`, so letting Chrome classify the literal is correct across both naming
schemes. Current Chrome authorizes extension-origin requests through the host
permissions; the visible **Check connection** action also verifies the server
directly and can surface a Local Network Access prompt on affected builds or
managed policies. The server answers extension CORS and older Private Network
Access preflights for compatibility.

## Pagination

A sweep visits every page once. Progress is an explicit `{total, visited[]}`
record in `chrome.storage.local`, keyed to the **active tab**
(`sweep:<tabId>`), not an inference from the current URL. When the lease moves,
inactive progress keys are removed so a fallback cannot resume another tab's
stale navigation.

The one-active-tab rule matters: several independent reviewer tabs previously
stomped shared progress and walked the same catalogue in parallel. A user may
still leave several reviewer tabs open, but only the deterministic primary
sweeps; the Options page identifies the others as passive fallbacks.

Deriving the next page as "current + 1" failed in practice: Walmart rewrites
the query string between loads (`page` appears before `affinityOverride` on one
render and after it on another), and the walk was seen jumping 1 -> 7 -> 6 ->
10. The explicit set also survives the content script dying, a reload landing
somewhere unexpected, and the user clicking a page link mid-sweep -- the next
relay just resumes at the lowest page not yet seen.

`total` comes from the pager: the highest number in the `<ul>` holding
`[data-automation-id="page-number"]`. It is scraped from the whole list rather
than the page-number anchors because the last page renders as a plain `<div>`,
not a link.

The pager is below the fold and lazily rendered, so a relay firing before it
exists reads nothing. The walk scrolls to the foot of the page and back to
force it in, restoring the scroll position so a visible tab does not jump. That
scroll is flagged as programmatic -- the interaction detector listens for
`scroll`, and counting our own would defer the next navigation by the full
30-second grace period every time.

The total only ever grows, across every reading in a sweep. The pager renders
progressively, so a read caught mid-render on page 19 saw `1 ... 18 19`,
reported 19, and the sweep declared itself complete five pages early. Taking
the maximum of every reading makes a partial render harmless.

The end-of-results panel is only believed when the page count is unknown or the
current page is at/past it. Walmart shows that panel transiently on a slow or
failed load, and trusting it mid-catalogue truncates the sweep the same way.

An unknown total is explicitly *not* treated as a finished sweep. It once was,
and since the pager renders late that ended every sweep after a single page.
The fallback is to walk forward from the highest page seen and let the
end-of-results panel stop the sweep; once any relay reads the pager, the total
is persisted and the set-based walk takes over.

The advance guard is a plain boolean set synchronously. The previous version
checked a timer handle before an `await` on storage, so two observer-driven
calls both passed the check and both queued a navigation.

The step delay (default 5s, jittered) is a setting because it is the dial that
decides how hard this hits the site. The walk defers while the user is
mid-interaction, and the refresh alarm clears the visited set to start a fresh
pass -- but skips tabs whose sweep is still running.

## Expressing "either condition"

A rule ANDs its own clauses; `first_match` ORs across rules. So "at least $80
**or** a vanity/mirror title" is two rules, not one.

The options form wrote exactly one rule, so it could only ever produce AND --
and it did so silently. A user asking for either condition got the stricter
reading with nothing in the UI to reveal it, and the filter simply never fired.

The form now has a match mode. "All" writes the single `my-filters` rule as
before. "Any" writes `my-filters-value` and `my-filters-keywords`, splitting
the value bounds from the keywords so each can match alone. Both carry the
exclusions and the priority, since a veto has to apply to either half.

The two names are also how the form recognises a set it wrote itself. Without
that, reloading would see two rules and warn that saving will collapse a
hand-built multi-rule configuration. A side left blank contributes no rule at
all rather than an empty always-true one, which would alert on the entire
catalogue.

## Where the filters live

The extension's options page edits filters, but it does not hold them. It reads
and writes them over `GET`/`PUT`/`DELETE /rules` on the notifier.

Filtering in the extension was the obvious alternative and is worse. The
notifier applies rules on every relay regardless of which tab (or `curl`) sent
the items, so browser-side rules would be a second filter that only some
traffic passes through. Two places to look when something did not alert is the
failure mode worth designing out.

Saves go to `data/rules.json` rather than `src/rules.json`. The repo's defaults
stay pristine, "Reset to defaults" is a file delete, and a user who has never
opened the options page gets the shipped rules. `RULES_JSON` in the environment
still outranks both, so a one-off override needs no file at all -- and the
options page says so in a banner rather than letting someone edit a form that
silently does nothing.

Rules are re-read per request. At one relay every few minutes that cost is
nothing, and it buys edits that apply without a restart. They are evaluated
only for newly observed items. Matching, filtered, and seed-mode items are all
recorded, so changing a rule affects future arrivals and does not replay an old
backlog. The state file is therefore a handled-item ledger, not an alert log.

The HTTP server is threaded, so rule-file reads, saves, and resets share one
process-wide lock. A save validates the whole rule set before an atomic replace,
and a response obtains the rules and source from the same locked snapshot.

## Identity and dedupe

`Item.fingerprint()` prefers the numeric Walmart item id from a `/ip/<id>` URL,
so a retitled listing does not re-alert. Without a URL it falls back to a hash
of the normalised title.

Persisted state is `data/seen.json`. Writes go to a temp file and are then
renamed over the target, so a crash mid-write leaves the previous good file
rather than a truncated one — losing that file means re-alerting on everything.
The file is trimmed to the newest 20,000 entries; the portal shows around 971
items today, so that is generous headroom.

`SeenStore.claim` reserves a matching item under one lock, but that pending
claim lives only in memory. It becomes a persisted record only after the
notifier reports success. An overlapping HTTP request sees `pending`, does not
run changed filters over the in-flight item, and returns a retry signal to the
browser. This closes the race between a timed-out request and its retry without
turning a crash during delivery into a permanently missed alert.

There is an unavoidable ambiguity if the process dies after the provider
accepts a notification but before `commit()`: the next run may send a duplicate.
The design intentionally prefers that possible duplicate to silently losing an
alert. Once committed, matching and non-matching items deduplicate identically.

## Walmart bot checks

Walmart answers suspected automation by redirecting to `/blocked`, a
"Robot or human? Press & Hold" page. That is the site asking this traffic to
stop, and the extension treats it that way. It does not try to get past the
check: the user solves it by hand.

Detection is in the background worker's `tabs.onUpdated`. That page is outside
the content script's `/reviews/*` match, so the content script never runs there
and could not report it. Any Walmart tab landing on it pauses everything,
because the check applies to the browser session, not to one tab.

Before this, the block silently stalled the sweep. Worse, solving the check
redirects back to the reviewer page mid-catalogue. The coordinator would
promote that tab and resume the walk at full speed, straight into another
check. Now a check:

- stands every tab down (the coordinator primary goes to null and a new
  generation starts), and discards all sweep progress;
- pauses until a backoff expires: 1 hour, doubling on each repeat within
  24 hours, capped at 24 hours. Several `onUpdated` events from one block
  count once;
- moves the refresh alarm to the end of the pause. `scheduleRefresh`,
  `refreshTick` (including its repair reload), and non-manual
  `restartPrimary` all check the pause, so nothing can reload a tab early;
- posts `/bot-check` to the notifier, which sends a fixed-text urgent alert.
  The text is fixed server-side so the route cannot be used to push arbitrary
  content. Only the clamped pause length is read from the body.

**Restart sweep** is the manual override: the user saying they solved the
check. It clears the pause but keeps the count, so an immediate repeat still
backs off longer.

## How the seen store is written

Two properties are in tension: an item must not alert twice, and the file must
not be rewritten constantly.

The original design saved the whole JSON file inside every `mark_seen` and
`commit`. At 20,000 records that is a 4.6 MB rewrite per item, so one 38-item
page wrote about 175 MB and a 21-page sweep several GB. Retention was a bare
row cap of 20,000, which quietly made the real window whatever the churn rate
implied: measured at 3,000-10,000 new ids a day, that was about three days, so
an item still listed in the portal could be trimmed and alert again as if new.

Now:

- **Retention is stated in days** (`RETENTION_DAYS`, default 60). `MAX_ENTRIES`
  survives only as a backstop against unbounded growth. Records with no
  timestamp predate value tracking and are treated as expired rather than
  immortal.
- **New records are appended**, one JSON line each, to a `.log` beside the
  snapshot. A write costs what is new rather than what is stored.
- **The snapshot is rewritten only on compaction**: when the log is both at
  least `MIN_COMPACT_LINES` and at least as long as the live set, and at
  shutdown. Comparing against *twice* the live set never fires, because with
  append-only inserts the log and the store grow together.
- **Appends are batched** by a background thread and forced once per ingest.
  A hard kill loses at most a second of records, whose only consequence is
  that those items may alert once more -- the same trade already made for
  in-flight claims.

Ordering matters in two places. Compaction deletes the log only after the
snapshot has been replaced, so a failed snapshot write cannot lose the records
the log still holds. A failed append puts its records back on the queue rather
than dropping them, since a lost record means a duplicate alert later.

Loading replays the log over the snapshot, and does so even when no snapshot
exists yet -- until the first compaction the log is the only copy. A torn final
line, the expected cost of appending without fsync, is skipped without
discarding the lines before it.

## Failure behaviour

Deliberate choices about what happens when something breaks:

- **Delivery failure releases the in-memory claim**, and both failed and
  still-pending counts are negative acknowledgements. The active browser tab
  keeps the same page open and retries instead of silently swallowing an item.
- **Non-matching items are still marked seen**, so loosening a rule later does
  not replay every old listing at once.
- **A misconfigured notifier degrades to `NullNotifier`** and logs, rather than
  crashing the server.
- **Malformed HTTP JSON is rejected with a 400**, including a mixed batch with
  one invalid item. The extension receives a negative acknowledgement and does
  not mark that page visited. The tolerant markup parser remains isolated from
  this HTTP contract.
- **`POST /test-notification` bypasses filters and state** so connection and
  provider delivery can be diagnosed independently of catalog extraction.
- **ntfy quota errors open circuit breakers.** Public `ntfy.sh` currently
  allows 250 messages per day; code `42908` suppresses further publish attempts
  until midnight UTC. Email-only failures pause email forwarding and retry the
  same alert push-only. On `ntfy.sh`, setting `NTFY_EMAIL` without a verified
  account token starts directly in push-only mode with a visible diagnostic.
- **Network exposure requires an explicit secret.** A native launch binds to
  `127.0.0.1`; a non-loopback `BIND_HOST` is refused unless `INGEST_TOKEN` is
  non-empty. The one narrow exception is the supplied container configuration:
  a bridged container listens on its internal wildcard interface while Compose
  publishes it only on the host's `127.0.0.1`. The explicit
  `CONTAINER_LOOPBACK_ONLY` assertion permits only wildcard addresses, never a
  LAN address or hostname. Browser-origin mutations are accepted only from
  extension origins.
- **A corrupt state file starts empty and is rewritten.** The cost is one round
  of duplicate alerts; the alternative is a server that will not start.

## Latency

Discovery is bounded by the extension's refresh interval and the time required
to walk the catalogue. Server-side filtering and state checks are local, while
provider delivery can take up to its network timeout and is acknowledged before
the page advances. Three minutes is the suggested refresh default. See the
Terms of Use discussion in the README before lowering it: the interval is the
main dial controlling how much this looks like a person and how much it looks
like a bot.
