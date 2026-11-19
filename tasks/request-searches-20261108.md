# Dedicated searches for Discord item requests

November 8, 2026, America/Chicago.

## Requested behavior

The owner requested complete dedicated searches for the two current request
users, without new restrictions. Existing request commands, permissions, trial
eligibility, per-member watch rules and private DM delivery are preserved. This
is active catalogue search, not auto-claim. No public messages, DMs or synthetic
live watches are sent during rollout.

## Implementation

- `RequestStore.search_queries()` produces one search per distinct AND watch or
  exact item ID. The first watch preserves natural keyword order. Member IDs,
  request IDs, minimum values and billing metadata are not exported to Chrome.
- `RequestRuntime.search_queries()` only exposes work from its audited ready
  service. Existing membership sweeps continue to remove ineligible watches;
  the DM sender still verifies current membership immediately before delivery.
- `GET /request-searches` uses the existing ingest-token and trusted-origin
  checks, returns `schemaVersion: 1` and `queries`, and forbids response caching.
  It is read-only and never resets relay freshness or sends an alert. Failure
  returns a sanitized 503; a disabled request service returns an empty list.
- Extension polling runs with the existing 30-second auxiliary alarm, including
  when no watches exist yet. Backend errors clear the current request list and
  pause only its lane. A later successful sync restores it. Searches do not
  overwrite the operator's scanner settings or manually configured keywords.
- `requests` is a separately owned browser lane, one additional managed tab when
  work exists. It shares full result pagination, checkpointing, global load
  spacing, foreground turns, traffic waits and verification pauses. Equivalent
  watches share search work; additions retain active progress regardless of
  their sort order. Removed scopes reject late-generation results.
- A completed request round waits one minute plus existing bounded jitter;
  broad lanes retain their intervals. The interval starts after all targets
  complete, so it is not a per-query one-minute latency promise. Global refresh
  zero stops repeated completed rounds as before.
- Discovered items use the same authenticated intake and transactional matcher,
  preserving original filters and DM deduplication. Matching still happens on
  broad observations too. No extra claim action or Walmart product-page fetch.
- Private scanner status, Options, browser telemetry and log audit recognize the
  new lane. Queries are not added to general scanner preferences or public logs.

## Verification

Feature tests failed first on missing store/runtime/HTTP/sync/lane behavior,
then passed. Additional tests cover deletion, eligibility removal, exact IDs,
shared queries, HTTP authorization/origin/privacy, sync failure/recovery,
four-lane status, automatic background startup, scoped intake, verification
pause, round scheduling, additions with deep checkpoints and obsolete results.

All **1,098 JavaScript tests pass**. Ruff, JavaScript syntax, generated native
protocol and backend CLI startup checks pass. The candidate image passes an
offline Linux smoke using temporary data, no network or production mounts:
dedicated queries, shared intake, personal-alert deduplication and deletion.
Full Python suite: **2,841 passed, one existing platform skip** in 184.46 seconds.
JavaScript suite: **1,098 passed, zero failures/skips**. Test output is saved in
`data/request-search-python-verified.txt` and `data/request-search-js-verified.txt`.

One stale existing priority test still asserted a 24-query cap while both current
schemas allow 32; its rejection case now tests 33. The manifest test follows
2.27.0. No validation was weakened or tests skipped for this feature.

## Deployment

Private receipts: `data/request-searches-20261108/`.
Exact baseline image: `2aa85aba1e71e031e7c1b8b4f41f5d23410baae1009575bc22318897b8a2a6b4`.
The candidate overlays only seven reviewed backend modules on that live image.
It preserves environment, mounts, security, command, network and scanner/relay/
Discord configuration hashes; rollback image remains available. Source comparisons
against live were reviewed after normalizing line endings. The workspace already
contained unrelated pending scanner/telemetry work; it was not reverted or
included in the backend overlay. No commit or push was made from this mixed tree.

Browser tooling is unavailable in this chat. Reloading extension 2.27.0 requires
the owner's Chrome UI. Until scanner status reports 2.27.0 and a `requests` lane,
the dedicated browser searches are not proven active. Existing broad scans and
personal matching remain usable with the compatible backend.

Backend activated November 8 at 11:50 CST. Image
`050a198f1e0fb01e2eff3eb20a495c1a7cc6605a1b61c95908c55a5ec307e4c7`
is healthy with all runtime preservation checks passing. Read-only live checks
confirmed 13 distinct searches from 13 saved watches across three request owners,
request runtime ready with no error, database quick-check clean, no-store
response and correct rejection of unauthenticated/untrusted-origin requests.
No live watch or item was fabricated. The latest scanner report still shows
**2.26.18** and no request lane; the owner was asked to reload Chrome's unpacked
extension. Dedicated live search/detection timing remains unverified until that
step. Source-module hashes and receipts are saved in `live.json`.

## Live browser activation confirmed

After the owner's reload, scanner status reports **2.27.0** with the dedicated
`requests` lane enabled, owned and reachable on tab `1530562750`, with all 13
searches queued. At **11:53:37 CST**, its first real result page delivered four
items to intake with HTTP 200: **one new product and three duplicates**, zero
intake failures. This confirms an independent requested search is discovering
and delivering real catalogue results through existing deduplication. It does
not claim a successful customer claim or prove a new personal DM was sent.

The reader subsequently reports `waiting-focus` / `user-active`, honoring the
existing foreground courtesy guard while the owner uses Chrome. That is a wait,
not a search/ingest error. Full-round duration and long-term latency remain to
be measured. Read-only activation evidence is in `browser-active.json`.
