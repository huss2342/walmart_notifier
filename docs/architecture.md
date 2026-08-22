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

Sources do not deduplicate; `pipeline.process` does that against `SeenStore`.
Returning the same items on every poll is expected and correct. Adding a source
means implementing `fetch()` and appending it in `function_app.poll`.

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

- **Delivery failure leaves the item unmarked**, so the next run retries instead
  of silently swallowing an item you wanted.
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

## Latency

The 2-minute timer is the default because it is comfortably inside the free
grant and fast enough for a queue that refreshes irregularly. The floor for a
timer trigger is about one minute. For genuine push latency, forward mail to
`/api/ingest` — that path is bounded by your mail provider, not by the poll.
