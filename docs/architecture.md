# Architecture notes

## Why there is no credential-login source

The obvious design — store the Walmart password, sign in on a schedule, scrape
the reviewer page — is missing on purpose. The reasoning is in the README; this
is the engineering summary.

| | Credential login | Email source | Browser extension |
|---|---|---|---|
| Walmart credentials stored | yes | none | none |
| Requires disabling 2FA | yes | no | no |
| Contacts Walmart servers | yes, from a datacenter | never | only pages you opened |
| Survives bot detection | no | n/a | n/a — real browser, real session |
| Breaks when markup changes | yes | degrades | degrades |

The email and extension paths reach the same outcome without the failure modes,
so the credential path has no upside left to justify its cost.

## Sources are pluggable

`sources/base.py` defines a one-method protocol:

```python
class ItemSource(Protocol):
    name: str
    def fetch(self) -> Iterable[Item]: ...
```

Sources do not deduplicate *items*; `pipeline.process` does that against
`SeenStore`. Returning the same items on every poll is expected and correct.
Adding a source means implementing `fetch()` and appending it in
`function_app.poll`.

Sources may still avoid redundant *transport*, which is a different concern.
`ImapSource` keeps a `uidvalidity:uid` high-water marker in the same table and
asks the server only for newer messages. Two details make that safe:

- `UID n:*` returns the newest message even when `n` is past the end of the
  mailbox, so the server's answer is always filtered against the marker rather
  than trusted.
- UIDs are only comparable within one `UIDVALIDITY` generation. The generation
  is stored alongside the marker, and a change resets to a dated backfill
  window instead of trusting a UID that now means something else.

The marker is *pending* until `commit_marker()`, and `function_app.poll` calls
that only when no delivery failed. Advancing it on a failed push would mean the
mail is never read again and the alert is simply lost.

## Price extraction

`sources/parsing.py` is the fiddliest part, because item titles and prices are
positional — there is no markup contract to rely on, and the templates change
without notice.

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

An item whose price cannot be located is still emitted with `value_usd=None`
rather than dropped; `Rule.alert_on_unknown_value` decides what happens next.

## Failure behaviour

Deliberate choices about what happens when something breaks:

- **Delivery failure releases the item's claim**, so the next run retries
  instead of silently swallowing an item you wanted. It also holds the IMAP
  marker back, so the source re-reads the mail that mentioned it.
- **Notifying requires winning an atomic claim.** The timer and the ingest
  endpoint can be holding the same item at the same moment; both will have read
  the store before either writes. `SeenStore.claim` is a Table Storage entity
  *create*, which the service rejects for an existing row key, so exactly one
  of them buzzes the phone.
- **Dedupe lookup failure fails closed** (treats the item as seen). A storage
  blip should not machine-gun your phone with repeats.
- **Non-matching items are still marked seen**, so loosening a rule later does
  not replay every old listing at once.
- **A misconfigured notifier degrades to `NullNotifier`** and logs, rather than
  crashing the polling run.
- **A failing source is caught per-source**, so email trouble does not take the
  ingest endpoint down with it.

## Identity and dedupe

`Item.fingerprint()` prefers the numeric Walmart item id from a `/ip/<id>` URL,
so a retitled listing does not re-alert. Without a URL it falls back to a hash
of the normalised title. Ids live in Table Storage under a single partition —
point lookups by `RowKey`, which is the cheapest access pattern Table Storage
has.

## The extension's refresh loop

The reload schedule lives in `background.js`, not in the content script. A
content script dies with its page, so a reload landing on a sign-in redirect or
a bot-check interstitial would end the loop permanently and silently -- exactly
the state the refresh exists to escape. A `chrome.alarms` alarm in the service
worker keeps firing regardless of what the tab currently shows, and asks the
content script whether the user is mid-interaction before reloading. A tab with
no content script answering is reloaded anyway.

Change detection also lives in the background worker. In the content script it
was re-initialised on every reload, so each refresh re-POSTed the whole page.

The worker posts to a different origin than the pages it reads, so the manifest
needs `https://*.azurewebsites.net/*` in `host_permissions`. Without it the
fetch is subject to CORS, Azure Functions answers no preflight, and every relay
fails silently. A custom domain on the Function App means editing that entry.

## Latency

The 2-minute timer is the default because it is comfortably inside the free
grant and fast enough for a queue that refreshes irregularly. The floor for a
timer trigger is about one minute. For genuine push latency, forward mail to
`/api/ingest` — that path is bounded by your mail provider, not by the poll.
