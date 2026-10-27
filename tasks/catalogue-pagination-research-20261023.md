# Catalogue pagination research — October 23, 2026

## Authorized larger-page test — 1:20 PM Central

The owner subsequently authorized testing 500/1000 in a separate browser tab.
One isolated page navigation was made to:

`https://www.walmart.com/reviews/claim-product?ps=1000&limit=1000&page=1`

This is an HTML/page request, **not a captured internal catalogue API call**.
The previous `limit: 40` finding came from embedded query variables, not a
recorded Network request. The exact API request remains unavailable through the
current browser tools.

Observed result:

- URL retained `ps=1000` and `limit=1000`.
- `initialSearchQueryVariables.ps` became 1000.
- Its `limit`, nested search limit and nested fitment limit remained **40**.
- The response held **20 Product entries plus one AdPlaceholder**, with 20 unique
  product IDs and 20 image URLs. Advertised inventory was **681** at this time.
- Pagination metadata reported size **20** and **35 pages**; the visible pager
  independently showed page 35 as the end. Inventory changes since the earlier
  701-item observation are not attributed to the test.

The URL override did not produce a larger batch. This is consistent with the
page ignoring/clamping/falling back from an unsupported page size; it does not
prove that the actual underlying API cannot accept a larger batch. No 500 test,
parallel requests, request replay or parameter-guessing loop was performed.

The first data read found a challenge marker, which alone is not a visible
challenge. Follow-up DOM/screenshot inspection found no marker or challenge UI.
No agent CAPTCHA interaction occurred. Numeric/schema evidence and the visible
35-page result are saved in ignored `data/catalogue-batch-limit-test-20261023.json`
and `data/catalogue-batch-limit-test-20261023.png`. The owned test tab was closed;
the existing monitoring tab was not operated.

Next evidence needed: one normal page transition's Fetch/XHR request URL,
operation name and pagination variables from Chrome Network, plus returned
product count/page metadata. Request headers, cookies and account/session
identifiers are unnecessary. A real API batch-size test should use that captured
contract rather than assuming the page URL maps directly to it.

## Scope and conclusion

Owner requested research into eliminating pagination, client/server data loading,
images in responses and parallel page requests. Read-only live DOM inspection and
primary-source web research were performed. No collector behavior, scan settings,
permissions or running services changed; no experimental API requests, parallel
requests, manual pagination or verification input were sent.

**Strong evidence: server-paginated data behind client-side navigation.** The
initial page embeds a limited search result and explicit page/limit variables.
Client-side route transitions do not mean the complete catalogue was preloaded.
The exact CSR request/response contract and server maximum batch size remain
unverified because the available browser connector exposes DOM and observed
asset metadata, but not raw Network request/response bodies. Prior Resource
Timing inspection was also unavailable; no alternate browser/debugger/profile
access was used. A public page-bundle web read was unavailable, so no shipped-code
analysis is claimed.

## Observed live evidence

The existing signed-in monitoring tab was usable and continued its ordinary scan.
Its DOM `script#__NEXT_DATA__` contained:

| Field | Observed value |
| --- | --- |
| `props.pageProps.initialSearchQueryVariables.page` | 1 |
| `props.pageProps.initialSearchQueryVariables.limit` | 40 |
| `props.pageProps.initialSearchQueryVariables.ps` | 40 |
| `initialData.searchResult.itemStacks[0].meta.totalItemCount` | 701 |
| `initialData.searchResult.count` | 40 |
| `initialData.searchResult.paginationV2.maxPage` | 18 |
| `initialData.searchResult.paginationV2.pageProperties.ps` | `"40"` |
| `initialData.searchResult.itemStacks[0].items.length` | 40 |
| Actual product entries with IDs/names/price info/image URL | 39 |
| Other result entry type | `AdPlaceholder` |

The initial script still described page 1 while ordinary monitoring moved through
URL pages 9, 12 and 16. Therefore reading this script repeatedly would return a
stale initial batch during SPA pagination. Future structured-data collection must
capture each fresh page result or use a validated supported data transport.

Product objects already include IDs, names, price metadata and
`imageInfo.thumbnailUrl` / `imageInfo.allImages` fields. Image URLs do not mean
all image bytes, or images for all 701 products, have been downloaded. The exact
reviewer “Valued at” mapping needs comparison against the corresponding cards;
generic price metadata alone is not that validation.

Sanitized numeric/schema evidence is retained in ignored
`data/catalogue-pagination-research-20261023.json`; no account/session/cookie
values, headers, raw page body or complete product records were exported.
Its main-link uniqueness counts include recommendations/other main content and
must not be treated as reviewer catalogue batch counts.

## Options and next experiment

1. **Check supported batch size first.** A limit/page-size parameter exists;
   this does not prove larger values are accepted. Capture one ordinary page
   request, identify its actual page/limit fields and freshness rules, then
   validate one modest larger batch. Check returned count, total, next-page
   metadata, unique IDs and the final partial batch. For illustration only,
   701 items require about 18 batches at 40, eight at 100 or four at 200 if the
   server supports those sizes. Do not assume a URL parameter changes the API
   limit or that the server has an all-items option.
2. **Use fresh structured page data where feasible.** It may remove card/image
   rendering dependencies and retain image URLs immediately. It does not itself
   eliminate server pagination or the collector's saved pacing. Preserve account
   eligibility, correct review value, dedupe, empty-page readiness and challenge
   handling before replacing DOM collection.
3. **Evaluate concurrency only after the actual contract is known.** Server
   pagination does not require physical/manual clicks; an appropriate data
   integration can request pages directly. Sending all 18 pages at once creates
   a burst even when total requests per scan are unchanged. HUMAN documents
   unusual request volume/flow indicators and configurable request/page limits.
   Walmart's thresholds are private. Any later concurrency experiment should be
   bounded, compared against sequential traffic, and stop on challenges, 429s
   or access denials. No concurrency value is established as reliable here.

The published Walmart Reviews API found in this research is for Marketplace
sellers and the Review Accelerator Program. It is not evidence of a public API
for this signed-in Recognized Reviewer claim catalogue.

## Primary references

- [Next.js client-side navigation](https://nextjs.org/learn/pages-router/navigate-between-pages-client-side)
- [Next.js server data and client navigation](https://nextjs.org/docs/pages/building-your-application/data-fetching/get-server-side-props)
- [Chrome Network recording and response inspection](https://developer.chrome.com/docs/devtools/network/reference)
- [HUMAN bot characteristics](https://docs.humansecurity.com/applications/bot-characteristics)
- [HUMAN rate and volume policy settings](https://docs.humansecurity.com/applications/bot-defender-policy-settings)
- [Walmart Reviews API scope](https://developer.walmart.com/global-marketplace/docs/reviews-api-overview)
