# Architecture notes

## Discord collector route

`NOTIFY_PROVIDER=discord` sends fresh `/ingest` observations directly through
`src/notifiers/discord.py` into `discord_bridge.core.JobStore`. This branch runs
before personal rules and `SeenStore` deduplication, so older $79/keyword filters
cannot suppress Starter inventory. Local `SeenStore` observation statistics are
still recorded; its old ledger is never replayed into Discord.

The extension supplies product title, observed retail value, canonical item link,
explicit card-local category hints and availability. Promotional badges remain
separate. A deterministic title classifier picks one existing category; recognized
department hints provide fallback and unrelated search keywords cannot tag a
whole page. Unknown categories remain `other`. Only allowlisted public fields
enter the Discord store. Owner quotas, raw data and personal search strings do not.

The existing HTTP response reports durable acceptance, not completed posts. A
single background worker uses the existing audited, rate-limit-aware sender.
Independent destination jobs retain Pro/Starter/free timing, completed delivery
IDs and original free snapshots across process replacement. Diagnostics post
only to private operations. `/health` separates intake from delivery status.
Both the native launcher and Docker image use the same implementation; the
image build includes source packages only, with secrets/state mounted at runtime.

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
by window, position, and tab id, leases the first reachable ordinary tab as the
active home reviewer, and tells other ordinary eligible tabs to remain passive. The
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

Relay 2.18.0 also stores `reviewerResume` in local storage: the primary's last
committed eligible URL, scan settings and acknowledged sweep record. Content
scripts await a worker-authorized checkpoint before scheduling navigation. Tab
URL updates preserve a newly reached page even before item delivery. Closing
the primary retains that checkpoint, then reopens its URL or reuses an eligible
passive tab. Selection adopts matching saved progress under a fresh generation
and tab key before broadcasting the new lease. Pending navigation keeps the
current lease, while a reused tab's old document stays passive until the saved
URL commits.

The checkpoint survives empty session storage on browser restart, including
temporary category fallback. A supervisor-opened catalogue homepage returns to
the saved URL. Extension reloads repair the same page and migrate existing
per-tab progress on the first upgrade. Manual restarts clear the checkpoint;
changed mode/path settings invalidate incompatible state. Starting in 2.21.0,
verification preserves acknowledged progress in the durable checkpoint while
revoking all active leases. After every open challenged tab is usable, compatible
progress can restore under a fresh generation even when verification returned to
another page of the monitored scope. Known progress older than 24 hours resets.
Completed scans clear its sweep progress while retaining their last page.
Existing verification, disabled-refresh and duplicate-open guards still apply.

The one-primary rule prevents ordinary reviewer tabs from sharing progress or
duplicating a catalogue walk. Starting in 2.20.0, `auxiliary-scans.js` can also
own exactly two explicit lanes (departments and keywords), separate from this
primary. Their tab IDs are reserved in session storage before a reviewer URL is
loaded; their queues, generations, committed sweep checkpoints and schedules
live in `reviewerAuxiliaryScans` in local storage. Primary selection/cleanup never
adopts these tabs or deletes their progress, and browser restarts never adopt an
unrelated tab merely because Chrome reused its ID.

`tab-workspace.js` groups only coordinator/auxiliary-owned IDs per existing
window, using session group identities validated against current group members.
Its optional focus slots select tabs before pager preparation and navigation;
reading slots last up to ten seconds. They never focus an OS window.
Current-generation authorization, shared pause,
request expiry, live selection/window state and a persisted 30-second input
grace gate grants. Content cancellation and scrolling keep their existing
navigation deadline. Completed owned tabs still renew the shared user grace.
Auxiliary page recovery retains the first interrupted checkpoint, resets only
that target on the second failure, then skips on the third.

With rotation enabled, content requires a granted, visible, focused window for
reading and navigation. Denied turns hold the page; actual user/window contention
does not spend a target's retry budget. Missing reading support or unavailable
activation is bounded to three attempts. Explicitly disabled rotation retains
the legacy background mode. Each first reading starts at the top; incomplete
passes resume from their previous position, with a cumulative 120-second work
budget. The scrolling helper requires ready content plus unchanged geometry and
item signature at the bottom for 1.2 seconds. Coverage requires completed reading
and acknowledged base/late intake. Same-page reading retries send only new IDs;
the first intake on each new page still sends all observations. The completed
proof includes item signature, document height, viewport height and readiness,
and is checked again before navigation. Current scroll position is excluded so
owned pager preparation above the true bottom cannot trigger an endless reread.

Managed role replies carry an exact assigned target and optional pending facet
selection. `facet-controls.js` expands only the reviewer's current filter region,
discovers live input values and selects exact controls. Expansion requires
actual option-count growth after Show More; a Show Less label alone
does not establish that asynchronous options have rendered. Stalled expansion
fails explicitly rather than marking truncated discovery complete. In-document
selection must change URL and cards; a genuinely fresh document can bind a pending selected
URL through the manager. Ingestion is forbidden until that binding. Lower
generation acknowledgements cannot revive revoked work. Content uses the same
collector, pager, scroll, pacing, user-input and acknowledged-delivery machinery
as the primary, but cannot overwrite its completion timing or start its fallback.

Department discovery runs only at the unsearched catalogue root. Every available
page of each top-level department is scanned; subdepartments are not enqueued.
The queue fingerprint invalidates legacy recursive queues on upgrade. Optional
brands and configurable clean keyword URLs add other
entry points. Unknown controls and failed selections have bounded retries, then
only that target is skipped. The site's 25-page ceiling and queue overflow produce partial
round status. Each lane has its own alarm-supervised recovery and refresh clock;
verification is the deliberate shared-session exception and revokes all lanes.

Normal department discovery reads only the public SearchSortFilterModule cat_id
facet embedded in the root HTML's __NEXT_DATA__. It checks the unsearched page
variables and every immediate name/ID pair, with no recursion or endpoint calls.
The coordinator independently restricts each supplied destination to one numeric
top-level catId on the reviewer route and allows only that assigned category to
bind. A full category load uses the existing focus/navigation guards; the fresh
server category ID and rendered applied chip must agree before ingestion.
This also handles shortened chip names, such as Clothing. Invalid metadata
fails explicitly; independent lanes retain their own progress and retry budgets.

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

During ordinary populated-page readings the total only grows. The pager renders
progressively, so a read caught mid-render on page 19 saw `1 ... 18 19`,
reported 19, and the sweep declared itself complete five pages early. Taking
the maximum of every populated-page reading makes a partial render harmless.
A repeatedly confirmed empty tail may shrink the boundary, because catalogue
items can disappear while a scan is running. Missing earlier pages are still read.

In regular mode, an empty results panel immediately starts a temporary a–z
round; a 30-second blank-card timeout takes the same path. The worker authorizes
this fallback only for the current primary/generation and persists its search
progress without changing the saved mode. Its lease accepts only single-letter
searches and neutral pagination parameters. Completed-letter progress survives
failed-letter recovery. The next scheduled round clears the fallback and search
parameters and tries regular pagination again, including after worker recreation.

Within search modes, the end-of-results panel is only believed when the page
count is unknown or the current page is at/past it. Walmart shows that panel
transiently on a slow or failed load, and trusting it mid-search truncates coverage.

An unknown total is explicitly *not* treated as a finished sweep. It once was,
and since the pager renders late that ended every sweep after a single page.
The fallback is to walk forward from the highest page seen and let the
end-of-results panel stop the sweep; once any relay reads the pager, the total
is persisted and the set-based walk takes over.

The advance guard is a plain boolean set synchronously. The previous version
checked a timer handle before an `await` on storage, so two observer-driven
calls both passed the check and both queued a navigation.

The minimum step delay (default 15s, jittered) is a setting because it is the dial that
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
exclusions and normal priority, since a veto has to apply to either half. The
Options priority selector is removed; existing JSON priority fields remain
compatible with notification providers.

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
stop, and the extension pauses every scanner. The optional verification assistant
is configured separately; completing an attempt cannot itself clear this pause.

Detection covers the background worker's `tabs.onUpdated` redirect check and
a separate content-script observer on reviewer and blocked pages. A passive
watcher also covers every other `https://www.walmart.com/*` page from document
start without loading the scanner there. The observers report visible HUMAN markers or verification
text pairs. Every navigation/reload rechecks the DOM; hidden placeholders are
ignored. Probes mark cards underneath an overlay as unusable. Any challenged
Walmart tab pauses the session, including when its URL does not change.

Before this, the block silently stalled the sweep. Worse, solving the check
redirects back to the reviewer page mid-catalogue. The coordinator would
promote that tab and resume the walk at full speed, straight into another
check. Now a check:

- closes worker authorization as soon as detection is received and broadcasts
  a stop signal to all Walmart tabs, before queued storage/coordinator work.
  Scanner documents cancel navigation, scrolling and extraction timers, ignore
  late active roles, and also observe the durable pause in local storage;
- revokes primary and auxiliary leases while retaining acknowledged checkpoints
  for recovery under fresh generations;
- pauses until usable markup confirms recovery. Time alone never clears the
  pause, including across worker restarts. Historical cooldown deadlines remain
  for diagnostics and alerts; once elapsed they schedule another readiness
  check in one minute. Events throughout one unresolved pause count once;
- makes `scheduleRefresh`, `refreshTick` (including its repair reload), and every
  `restartPrimary` check the pause. Passive probes may clear it when a
  challenged or newly opened reviewer tab reports usable markup. Every still-open
  challenged tab must also be usable, closed, or navigated away; `/blocked` tabs
  keep the pause even without a content-script response. This prevents one
  unresolved outage from being resumed and immediately counted again. Pre-existing
  cached fallback tabs cannot clear it, including during upgrades from a legacy
  pause that did not record fallback IDs. Recovery also looks for other open
  checks before clearing anything. Challenged non-reviewer Walmart pages must
  answer a fresh passive probe with no challenge before a usable catalogue can
  resume scanning;
- posts `/bot-check` to the notifier, which sends a fixed-text urgent alert.
  The text is fixed server-side so the route cannot be used to push arbitrary
  content. Only the clamped pause length is read from the body.

**Restart sweep** checks recovery and refuses to navigate while any tracked
challenge is unresolved. Cleared checks resume automatically through the normal
readiness probes; no restart click is required. A request or navigation already
issued to the browser can finish, but no subsequent scanning work is authorized.

Minimum page delays default to 15 seconds; jitter cannot shorten the saved
minimum. Regular mode allocates
75% of the effective refresh target across the known page count, with a
90-second baseline cap. Spacing is measured between navigation starts: the
document's `performance.timeOrigin` anchors full loads, and each native click
updates the anchor. Loading, settling and acknowledged delivery reduce the
remaining wait, never the chosen interval. Invalid/future clocks receive the
full interval. Diagnostics retain interval, elapsed and remaining milliseconds.
Completion
persists the scan start and finish times; the next scan starts no earlier than
the target from its start and a 20% rest after its finish (rest clamped to
1–5 minutes). Explicit zero refresh stays off, including queued-alarm races.
Unresolved verification pauses navigation. Cleared verification immediately
restores the saved timings; challenge count and age no longer inflate page
spacing or refresh intervals. Pre-challenge completion timestamps are cleared
on confirmed resume so they cannot defer the fresh pass. Missing alarms are recreated when the
service worker starts, following the [Chrome alarm persistence guidance](https://developer.chrome.com/docs/extensions/reference/api/alarms#persistence).

Pagination uses a visible, enabled link with the exact destination anywhere in
the document, then prefers the recognized pager's placeholder Next control for
an adjacent target if its current-page marker agrees with the URL, then tries
numbered placeholder controls. Empty `q` and `queryNups` fields are equivalent
to missing fields; nonempty filters must match exactly. Missing controls and
stalled clicks fall back to a document navigation. A client-side transition
keeps the relay locked until the destination URL and changed cards (or
end-of-results) agree. A click that leaves the normalized URL unchanged gets a
five-second start window followed by one retry after normal page spacing; an
actual transition retains the 30-second render
allowance. Fragment-only changes are ignored. User interaction, lease revocation and verification
cancel it. Coordinator tab events use the same mutation queue as challenges and
restarts. Unusable-page retries back off to five minutes and reset on delivery.
Native failures are bounded, durable entries keyed by normalized search route
and control capability (destination link, next, previous or number). A failed
capability waits at most60seconds before a bounded native probe, instead of
repeating URL loads during cooldown. Exhausted no-op retries and missing controls
retain finite URL recovery; probes retain the five-second startup window.
A late response during retry focus or fallback persistence re-enters the original
URL/card freshness poll. New documents load this policy
before accepting a scheduled native restart. Updating clears the native history;
browser/worker restarts preserve it. Verification state is retained in both cases.

Completed-cycle jitter is additive0–10%, derived once from the completed-cycle
timestamp and lane salt, so repeated scheduling computes the same deadline.
Primary rest floors and configured minimum page intervals still apply. Production
initial auxiliary delays are supplied by the worker: one page interval for the
first lane and two for keywords when departments are enabled. The coordinator
persists a shared lastTargetStartedAt reservation before target navigation; a
second ready lane waits for its slot across restarts and verification recovery.

Diagnostics persist reviewerScanMetrics independently of the bounded event trace.
After successful base/late ingestion and the reading pass, content emits one
results-page-ready event per document/page. Numeric documentId/page identities
deduplicate64 recent tabs; each tab's first observation is initialPages, later
changed identities increment successfulTransitions. Challenge/resume events open
and close shared downtime. Reports include current open downtime and a nullable
episodes-per100-transitions ratio. Measurement starts fresh, never reconstructed
from truncated history; an existing verification at upgrade is one partial episode
measured from that boundary. Both event updates and report initialization share
the diagnostic serialization queue. The strict backend accepts optional schema1
metrics while retaining compatibility with older reports and snapshots.

Regular monitoring accepts only unfiltered reviewer routes: page numbers and
empty query values are allowed; nonempty search/facet modifiers release the lease.
Explicit search modes keep their own intended scope. Selection, sender authority,
queued/native/live actions and repair reloads validate this boundary. A catalogue
tab is opened in the background if only manual filtered tabs exist. Repair I/O
rechecks committed/pending URLs and the coordinator generation under its lock.

Scheduled restarts revoke the old generation and clear progress before offering
`reviewer:restart-native` with the new lease and exact page-one destination.
The content script accepts only a matching generation/tab, unchanged search,
visible page-one control, and no verification or in-flight relay/navigation.
It cancels the role's initial extraction and queues normal native navigation,
which keeps old cards locked until the destination and changed content agree.
Only that confirmed arrival records a successful native restart.
Missing controls, older listeners and invalid acknowledgements use the existing
full navigation/reload repair; the worker rechecks its lease/pause before that
fallback. Manual restart retains the full-load repair path.

A completed empty-tail confirmation retains document-local proof for ten minutes,
scoped to the exact normalized search route and mode. Every subsequent pass still
visits the boundary page; two empty observations can reuse that proof without a
reload. Reuse never refreshes its age. New cards at/beyond the boundary, advertised
growth, another search/mode, clock reversal, expiry or verification discard it.
New documents always perform normal fresh confirmation. Earlier unread pages
still need successful delivery before completion.

`diagnostics.js` serializes a local 300-event ring with seven-day retention.
Verification attention uses a serialized toolbar writer shared with delivery
badges. It reads pause state inside the queue, keeps a red `!` and accessible
tooltip while paused, restores them when the worker starts, and clears them on
confirmed resume/manual reset. Late deliveries and the normal badge-clear alarm
cannot overwrite the warning. Toolbar failures do not alter coordinator state.
Challenge detection and its notifier alert count once per outage; repeated
detections may retry screenshot capture after a loading document completes,
using the evidence helper's existing active-tab checks and download deduplication.
The notifier alert checks HTTP success and records safe local sent/failed status;
unconfirmed results appear in Options. Ambiguous alerts are not retried.
Evidence and alerts retain their Chrome event lifetime but run outside the
serialized pause/resume queue, so a slow local request cannot hold recovery.
Inactive role demotion preserves the waiting-verification state. The permanent
observer detects text-node changes and iframe source/title changes as well as
child/style/class/hidden changes, requesting a new role immediately when the
challenge disappears; usable-page probing remains the worker's resume proof.
Missing-control fallbacks also capture one `pager-inspection` per document:
at most 1,000 candidates classified into numeric counts of pager roots, exact
links, visible exact links, query mismatches, and button shapes. Labels, markup,
URLs and attributes are discarded before reporting. This supports diagnosis of
site markup changes without exporting the page.
Allowlisted event names, enum reasons and numeric fields exclude raw URLs,
product text, credentials and server errors. Tab ID and generation correlate
events. Options exports a versioned JSON schema with only safe settings and
challenge timing; content-script senders cannot request the export. Retention
is enforced on writes and export. This is evidence of extension activity, not
an explanation of Walmart's private scoring.

`live-observer.js` observes an acknowledged page during its existing navigation
delay and after sweep completion, without reopening its sweep. It
posts only newly rendered, not-yet-acknowledged cards to the local ingest route;
its document-local cache is capped at 5,000 IDs and cleared on role changes.
Failed relays retain the items for bounded retries and defer queued navigation;
successful relays release that hold. Phase checks prevent extraction after
navigation starts, and navigation cancels pending observation timers. Verification and role
revocation cancel the pending observer, and no live-observation path navigates
or reloads Walmart. This does not provide cross-page push discovery.

Safe diagnostic snapshots are mirrored on the heartbeat to
`POST /browser-diagnostics`; unchanged snapshots are suppressed for five
minutes using worker-lifetime session storage. Transport errors and old-server
404s are shown in Options and retried without changing scan state. Reports
include ingest duration and authoritative new/duplicate/matched/notified
counts per page, plus native-navigation ready duration. These measure local
observation/delivery and navigation, not the unknown time an item first dropped.

The HTTP route uses the existing loopback/token/origin boundary, requires JSON,
and rejects declared bodies above 128 KiB. `BrowserDiagnosticsStore` validates
the full schema, rejects unknown/private fields, keeps at most 300 recent
events, and fsyncs/replaces a sibling temporary snapshot before acknowledging.
Storage failures return 500 and leave prior state intact. `GET
/browser-diagnostics` uses the existing token check. Neither route updates item
relay timestamps or notification state. Telemetry accepts legacy saved page
delays of 1–4 seconds; actual runtime pacing still clamps to its five-second
minimum. Diagnostic mirroring does not rewrite scan preferences.

Empty boundaries require repeated confirmation even after earlier pages were
delivered: recheck after two seconds, then one reload after another two seconds.
A confirmed empty tail establishes the new boundary despite a stale maximum.
Unread earlier pages are filled before completion. Confirmed empty interior
pages request a fresh pass, clearing their old progress before returning a
bounded recovery delay and a page-1 navigation. They cannot loop forever on the
same persisted empty-page attempt counter. The coordinator rechecks its lease
and verification pause under its mutation lock before clearing progress.

`RelaySettingsStore` owns the separate editable `data/relay-settings.json` with
exact keys `schemaVersion:1`, `refreshMinutes` (integer 0–120), `pageDelaySeconds`
(integer 5–60), and `sweepMode` (`none`, `sweep`, `high-value`). GET/PUT
`/relay-settings` use the existing token boundary; PUT also requires a trusted
origin, JSON, and a maximum 16 KiB declared body. Saves fsync and atomically
replace a sibling file; GET reads disk fresh to support manual editing. A
missing file returns `settings:null`, preserving existing browser preferences.
Invalid files or I/O errors yield generic errors rather than replacing working
client settings. Endpoint, token and path are excluded from this schema.

`relay-settings.js` validates the same contract in the worker and Options. The
worker refreshes its Chrome cache during the one-minute heartbeat and extension
installation; Options reads and saves the same server file. A failed PUT is
shown as unsaved and leaves the cache intact. Mode/query already stored in an
active sweep remain unchanged until the next sweep; timing edits apply to
subsequent scheduling. Explicit refresh zero remains valid throughout.

`scanner_control.py` adds separate atomic, bounded JSON stores for
`scanner-settings.json`, `scanner-commands.json`, and `scanner-status.json`.
The first two expose GET/PUT; status exposes GET/POST. All use the existing
token and trusted-origin boundary, strict JSON schemas and generic errors.
Settings and commands accept at most 16 KiB, status at most 64 KiB. Reads consult
disk directly. Missing settings preserve Chrome's cache; malformed/outage
responses cannot partially replace it. These requests never reset the item
watchdog or imply a product delivery.

`scanner-control.js` synchronizes only explicit preferences and two named restart
requests. Credentials stay in Chrome. Each restart reserves its action/ID in
durable storage before execution; interrupted reservations are reported without
replay. Shared verification defers requests, including an in-lock auxiliary
restart check before deleting progress. Commands cannot clear verification or
change input budgets. The worker emits an allowlisted snapshot of both lanes,
ownership, effective settings, primary phase, safe verification result codes and
nullable activation/freezing/window-focus metadata. Server receipt time governs
120-second staleness. It exposes no arbitrary scripting or browser RPC.

Lane snapshots may include `lastFailureCode`, a fixed allowlisted code derived
from known facet/watchdog failures. Unknown messages become `unexpected-error`;
raw error text never leaves the browser. Older snapshots remain valid. This
distinguishes discovery/selection failures from content silence without an
Options read-back.

Chrome verification attempts defer before reservation when a known tab/window
state is unsuitable for input, and recheck after debugger attachment. These
deferrals retain attempt budgets and use a 60-second retry. Native fallback
keeps its separate permission, cleanup, idle and focus checks; no preflight
or backend command expands the ten-attempt limits.

A separate one-minute Chrome alarm posts a strictly validated `/relay-status`
heartbeat to the loopback server. It records sweep phase, reviewer tab count,
refresh settings, verification cooldown, and completed sweep information without
item URLs or credentials. Heartbeats never update the successful-item-relay
timestamp. `/status` can therefore distinguish a disconnected browser, disabled
refresh, manual interaction, verification, filtered items and already-seen items.
The last successful item-notification timestamp is persisted separately; tests
and diagnostic alerts do not overwrite it. A silence warning retries until it
is delivered, then stays quiet for that outage. Its default threshold is two hours.

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

Telegram flood-control responses arm a per-destination monotonic cooldown from
the documented `parameters.retry_after` integer. Invalid/missing hints use
30 seconds; valid hints are bounded to 1–86,400 seconds. Short lock sections
protect a maximum of 32 active deadlines and reap expired entries; network I/O
never holds that lock. Overlapping responses retain the longest deadline.
Suppressed sends return false rather than acknowledge delivery, so existing
pipeline claims are released and the browser retries the page. Cooldowns are
in memory and destination-specific; bot-wide throttling and already in-flight
requests can still fail. Provider documentation:
<https://core.telegram.org/bots/api#responseparameters>.

Version 2.9 adds recovery at two boundaries. The MV3 worker opens the fixed
reviewer home page when no eligible or challenged tab exists, coalesces
concurrent checks, and persists a two-minute attempt cooldown in session
storage. It runs on startup, installation, tab closure and the existing
one-minute status heartbeat. A loading/discarded tab counts as present.
Whole-window closure is left to the host supervisor. Automatic refresh `0`
and verification pauses suppress tab creation.

`src/browser_recovery.py` runs on Windows outside Docker, under the interactive
user. The login task invokes `.venv/Scripts/pythonw.exe` directly. A real OS
file lock prevents overlapping instances; atomic state persists outage,
cooldown, launch-limit and verification information. The helper reads the
fixed loopback `/health` endpoint, using `runtime.browser.age_seconds` rather
than item age. Missing heartbeat for three minutes permits one Chrome launch
per outage while Chrome is running, with a ten-minute cooldown and a three-per-hour
cap. Auto-profile selection matches the unpacked extension path in Chrome's
profile preferences. Failure to match suppresses the launch.
The last real heartbeat time survives notifier/helper restarts, so clearing
in-memory server status does not itself open a duplicate Chrome tab. The
remaining stale window is respected while the backend recovers.

Only the fixed reviewer URL, installed Chrome/Docker Desktop paths, and bounded
`docker info` / `docker compose up -d --no-build` calls are used. Health requests
have no redirects or proxy forwarding. `data/browser-recovery.json` is strictly
validated and hot-read; invalid configuration suppresses actions. Safe fixed
decision labels go to a log targeting 60 days, rotating daily or at 256 KiB and
compressing archives, with a 2-MiB total disk backstop that trims oldest first.
Backend Python logs use the same policy with 2-MiB segments and a 16-MiB cap;
Discord delivery JSONL uses 5-MiB segments and a 32-MiB cap. Writers use thread
and OS file locks, and failed compression leaves the original intact. Docker's
30-MiB console cache and the seven-day/300-event browser diagnostic snapshot
remain separate. Cleanup never modifies delivery or dedupe state. The helper
can restore the local backend during a known browser pause so fresh heartbeats
can clear that pause; it does not interact with verification controls.

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
the page advances. Thirty minutes is the refresh default. See the
Terms of Use discussion in the README before lowering it: the interval is the
main dial controlling how much this looks like a person and how much it looks
like a bot.
