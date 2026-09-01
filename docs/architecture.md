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

Out-of-stock items are dropped: a notification for something that cannot be
claimed is pure noise. `Free items remaining: N` is read once per page and
attached to each item, because zero claims left decides whether an alert is
actionable.

`src/sources/parsing.py` is a separate, server-side extractor for raw HTML
posted to `/ingest`. It is the fallback path, kept because it is well tested and
costs nothing; the structured JSON the extension sends does not go through it.

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

The reload schedule lives in `background.js`, not in the content script. A
content script dies with its page, so a reload landing on a sign-in redirect or
a bot-check interstitial would end the loop permanently and silently — exactly
the state the refresh exists to escape. A `chrome.alarms` alarm in the service
worker keeps firing regardless of what the tab currently shows, and asks the
content script whether the user is mid-interaction before reloading. A tab with
no content script answering is reloaded anyway.

Change detection also lives in the background worker. In the content script it
was reinitialised on every reload, so each refresh re-POSTed the whole page.

The worker posts to a different origin than the pages it reads, so the manifest
needs `http://127.0.0.1/*` in `host_permissions`. Without it the fetch is
subject to CORS and every relay fails silently. The server answers preflight for
`chrome-extension://` origins anyway — belt and braces, because this exact
mistake shipped once already.

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
nothing, and it buys edits that apply without a restart.

## Identity and dedupe

`Item.fingerprint()` prefers the numeric Walmart item id from a `/ip/<id>` URL,
so a retitled listing does not re-alert. Without a URL it falls back to a hash
of the normalised title.

State is `data/seen.json`. Writes go to a temp file and are then renamed over
the target, so a crash mid-write leaves the previous good file rather than a
truncated one — losing that file means re-alerting on everything. The file is
trimmed to the newest 20,000 entries; the portal shows around 971 items today,
so that is generous headroom.

`SeenStore.claim` is check-and-insert under one lock. The server handles each
request on its own thread and several tabs can relay the same item at the same
moment, so a separate read-then-write would let one item buzz twice.

## Failure behaviour

Deliberate choices about what happens when something breaks:

- **Delivery failure releases the item's claim**, so the next relay retries
  instead of silently swallowing an item you wanted.
- **Non-matching items are still marked seen**, so loosening a rule later does
  not replay every old listing at once.
- **A misconfigured notifier degrades to `NullNotifier`** and logs, rather than
  crashing the server.
- **An unparseable payload yields zero items, not an error.** The relay posting
  something odd must never take the notifier down.
- **A corrupt state file starts empty and is rewritten.** The cost is one round
  of duplicate alerts; the alternative is a server that will not start.

## Latency

Bounded by the extension's refresh interval, not by anything server-side — the
POST is handled in milliseconds. Three minutes is the suggested default. See the
Terms of Use discussion in the README before lowering it: the interval is the
main dial controlling how much this looks like a person and how much it looks
like a bot.
