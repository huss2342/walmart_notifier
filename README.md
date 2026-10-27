# Walmart reviewer item notifier

Phone alerts when new items appear in the Walmart Recognized Reviewer
("Spark Reviewer") program, filtered by keyword and minimum retail value.

**Reviewer Alerts Discord mode:** the extension sends every captured product to
the local backend. It classifies the product and queues separate category,
observed-value and tier feeds before personal notification filters. The existing
server's private channels and role rules remain in force. See
[Discord operation](discord_bridge/README.md#collector-integration).

Runs entirely on your own machine. **No cloud account, no hosting bill, $0/month.**

## How it works

```
  ┌─ your browser ──────────┐         ┌─ this machine ─────┐      ┌───────────┐
  │ walmart.com/reviews/    │  POST   │  server.py         │      │ your phone│
  │   claim-product?q=...   │────────▶│  filter + dedupe   │─────▶│ ntfy /    │
  │ a tab you left open,    │ 127.0.0.1│                   │      │ Pushover /│
  │ signed in as you        │         │  data/seen.json    │      │ Telegram  │
  └─────────────────────────┘         └────────────────────┘      └───────────┘
```

A small Chrome extension reads the reviewer page **you** have open in **your**
signed-in browser and posts what it sees to a Python server on `127.0.0.1`.
That server applies your rules to newly observed items, records every item it
handles (including filtered ones), and pushes matches to your phone. Nothing
leaves your computer except the notification sent through your chosen provider.

With `NOTIFY_PROVIDER=discord`, the backend instead bypasses these personal
rules and uses its own durable per-channel queue. Starter receives positive
values below $40 after five minutes; Pro receives all values without an
intentional delay; free members receive historical snapshots after 24 hours.
It never imports old `seen.json` history into Discord. Repeated observations do
not reset timers or resend completed destinations. Unavailable items appear only
in the delayed free history. Unknown departments go to `other`.

There is deliberately **no auto-claim**. You still claim items by hand.

## Read this before you start

**Automated access to walmart.com is a Terms of Use violation.** Walmart's
[Terms of Use](https://www.walmart.com/help/article/walmart-com-terms-of-use/3b75080af40340d6bbd596f116fae5a0)
prohibit using "any robot, spider, site search/retrieval application or other
manual or automatic device to retrieve, index, 'scrape,' 'data mine' or
otherwise gather any Materials," and state that your account "may be restricted
or terminated for any reason, at our sole discretion." Recognized Reviewer
status is invite-only and revocable.

This design is about as low-exposure as such a thing gets — your own browser,
your own residential IP, your own session, a real browser fingerprint, no stored
credentials, no headless browser, and 2FA left on. But leaving a tab on a
self-refresh timer is still automated retrieval on a schedule, and the clause
above is broad enough to reach it. **Lower exposure than a bot, not zero.**

The tuning knob is the refresh interval. Each refresh is a full sweep of about
24 pages, so every 3 minutes is roughly 480 page loads an hour, far past
anything a person does, and it has triggered Walmart's bot check in practice.
Every 30 to 60 minutes is far gentler. Your account, your call.

## Setup

Windows, Docker Desktop, and Chrome. Docker is the recommended way to run the
notifier: it appears as **reviewer-item-notifier** in Docker Desktop, keeps its
own logs and health indicator there, and restarts automatically whenever Docker
Desktop starts. The Python/PowerShell launcher remains available as a fallback.

### 1. Pick a push channel

ntfy push delivery is free and needs no account. The public `ntfy.sh` service
currently allows [250 published messages per day](https://docs.ntfy.sh/publish/#limitations).
Install the **ntfy** app on your phone, then generate a topic name:

```powershell
[guid]::NewGuid().ToString('N')
```

Subscribe to that exact topic in the app. **The topic name is the password** —
anyone who knows it can read your alerts, so keep it long and random.

Create the local settings file, then paste the generated value in as
`NTFY_TOPIC`:

```powershell
Copy-Item notifier.example.env notifier.env
```

`notifier.env` is excluded from both Git and the Docker image. Compose reads it
only when the container starts.

Email forwarding has stricter requirements: `ntfy.sh` has
[disabled anonymous email](https://docs.ntfy.sh/publish/#e-mail-notifications),
so the destination address must belong to a verified ntfy account and
`NTFY_TOKEN` must contain that account's access token. Set `NTFY_EMAIL` as well
to request both push and email. If `NTFY_EMAIL` is set without a token, this
notifier deliberately stays in push-only mode and reports the reason on the
Options status panel.

### 2. Choose the first-run mode

The portal shows about 30 items per page. On a fresh install every one of them
looks new, so the first relay would fire many notifications at once. If
`data/seen.json` already exists, skip this step. For a genuinely fresh install,
set `SEED_MODE=true` in `notifier.env` now, before starting the container. Step
5 explains when to change it back.

### 3. Start the container

Run every `docker compose` command from the repository folder containing
`compose.yaml`:

```powershell
docker compose up -d --build
```

This creates and starts the service. The extension continues using
`http://127.0.0.1:8787`; Docker publishes that address on this computer only,
not to the local network. Check it at any time with:

```powershell
docker compose ps
docker compose logs --tail 50 notifier
```

Do not run `run.ps1` at the same time because both would need port 8787.

### 4. Install the extension

1. `chrome://extensions` → Developer mode → **Load unpacked** → pick
   `extension/`. If it was already installed, click **Reload** on its card and
   verify that the displayed version is **2.22.2**.
2. Open its **Options**. The defaults are already correct for a local server —
   endpoint `http://127.0.0.1:8787/ingest`, path `^/reviews/claim-product`.
3. With **reviewer-item-notifier** running, click **Check connection**. This
   verifies the local server directly; allow local-network access if your
   Chrome build or policy shows a permission prompt.
4. Click **Send test alert** and confirm that the labeled test reaches your
   phone. This bypasses filters and dedupe.
5. Set **auto-refresh** to `30` minutes (`0` disables it), then save. Each
   refresh is a full ~24-page sweep, so shorter intervals multiply load fast;
   see *If Walmart shows "Robot or human?"* below.

### 5. Open the portal

Open <https://www.walmart.com/reviews/claim-product?q=> in one tab and leave it
there. If several ordinary reviewer tabs are open, the
extension deterministically selects the first reachable one as active and keeps
the others passive. A passive tab takes over only if the active one becomes
unusable. Manually opened query/filter tabs stay under your control. For additional
independent monitors, enable the managed scans described below.
Version **2.18.0** saves the active reviewer's last committed URL, search/category,
and acknowledged scan progress. Closing its tab reopens that page in the
background (or reuses an eligible passive tab), retaining completed pages rather
than starting the scan over. The checkpoint survives a Chrome restart; an
extension reload refreshes the current page and preserves its progress too.
Recovery also checks on the one-minute heartbeat, with a two-minute retry
cooldown. A verification pause or auto-refresh setting of `0` prevents automatic
reopening. **Restart sweep** intentionally starts a fresh pass; changing search
mode or the monitored path invalidates an incompatible checkpoint.

Choose **Search mode** in extension settings, click **Save settings**,
then **Restart sweep** to apply:

- **None** reads the full catalogue using pagination. In **2.17.2**, an expected
  product page showing no search results gets 30 seconds to render, a second
  check, and one reload before a confirmed failure switches to category searches
  for that round. A blank page also gets 30 seconds. Recovered cards continue
  pagination; a confirmed empty catalogue tail finishes the scan. After `Wood`,
  the next scheduled round tries unfiltered pagination again; the saved mode
  stays None. Fallback diagnostics record the trigger, page, advertised page
  count, confirmations and observed wait. Update the local notifier for the
  expanded diagnostic fields.
- **Sweep (a–z)** searches `a`, `b`, …, `z`.
- **High Value** searches the 62 preset keywords in order, from `Accessories`
  through `Wood`, including phrases such as `Home Decor` and
  `Office/School Supplies`, plus `Furniture`, `Clothes`, `Chairs`, `Kitchen`,
  `Pet`, `Cat` and `Dog`. Each distinct query runs once per round. This selects search terms; alert filters still
  decide which products trigger notifications.

Sweep and High Value read all results pages for each search before advancing
and clear `queryNups`. Progress survives page loads; each refresh starts again
at the first letter or keyword. Products found under multiple searches remain
deduplicated by the notifier. Empty searches are retried before moving on.
The existing page delay and verification pauses also apply between searches.
Existing enabled Sweep settings remain enabled after upgrading.

**Additional coverage (2.20.0):** department and keyword scans run by default
alongside the normal home scan after reloading the extension. No checkbox setup
is required; brands remain off. Options can disable either scan or customize
keywords. The extension creates at most two additional background tabs;
do not create a tab per department. Their settings live in
`data/scanner-settings.json`; the existing `data/relay-settings.json` contract
stays unchanged.

The department tab reads Walmart's full top-level department list from the
unsearched catalogue and visits every available page in each department. It does
not walk subdepartments. Live investigation found 35 top-level departments; Home alone
reported 3,932 items while the unfiltered catalogue advertised only 631. These
counts change over time. Optional brand scans use the actual brand controls
within departments and can make rounds much longer. Leave brands off for the
initial rollout.

Department destinations come from the public sort-and-filter data embedded in
the unsearched reviewer HTML. The scanner visits those exact categories through
the normal focus-before-navigation path, then verifies the fresh category ID
and applied filter before accepting any items. A loading or incorrect department
cannot be counted as coverage for another target. Invalid catalogue metadata
fails the department round with bounded recovery; home and keyword scans continue.

The keyword tab searches editable terms independently. Defaults start with
Furniture, Chair, Chairs, Couch, Sofa and Pet, followed by the existing broad
terms. Each query reads all available pages before advancing. Search URLs start
clean, so a department or brand cannot accidentally narrow a keyword search.
Products discovered by multiple scans use the same notifier duplicate protection.

Each additional scan has its own durable queue, acknowledged progress, retry
budget and refresh clock. Closing its tab restores its committed progress. A
failed filter is retried and then skipped for that round without stopping the
home or other additional scan. Site changes, queue limits and the site's
observed 25-page ceiling appear as **partial** coverage in **Additional scan
status**. Use **Restart additional scans** to start fresh. There is no guarantee
that Walmart exposes every eligible item through these filters.

All modes retain normal page spacing, pager scrolling, fresh-card checks and
the pause for real user input. A verification challenge still pauses the shared
browser session. Additional tabs increase total browsing traffic; scrolling and
timing variation do not establish that challenges will be prevented.

**Scanner workspace (2.24.1):** grouping and tab rotation are enabled by default.
Owned home, department and keyword tabs join a blue **Reviewer scans** group in
their existing window. Each page gets a focused reading turn with a smooth
downward scroll, varying steps and timing, and a settled bottom before advancing.
Long pages resume scrolling in later turns from their previous position. The tab
is selected again before navigation. Rotation stays within a focused Chrome window and
yields to other selected tabs, verification, or recent real user input. It never
brings Chrome over another application. With rotation enabled, unavailable focus
holds the current page; selecting a scanner tab in the foreground lets rotation
resume. Explicitly disabling rotation retains background scanning without a
guaranteed visible reading pass. Grouping does not prevent closing a
tab, and tabs in other windows form separate groups.

Verification now retains the home scan's acknowledged progress as well as the
additional scans' checkpoints. Compatible progress resumes under fresh leases;
known checkpoints older than 24 hours start fresh. An additional target first
retries its saved page, resets to page one after a second failure, then skips
after a third. Verification does not spend that target's retry budget. Automatic
verification retains its existing enable setting, bounded retries and readiness
checks; neither tab focus nor refreshing guarantees that Walmart accepts a check.

Every sweep walks page 1 to the last page (detected by the portal's "no search
results" panel). **Delay between pages** is a minimum (default 15 seconds), with
varying timing. Regular mode spreads known pages across about 75% of the saved
refresh interval, up to a 90-second baseline per page. For example, 17 pages at
a 10-minute target use about 26 seconds between navigation starts. Loading,
settling and local delivery count toward that interval; they no longer add a
second full wait afterward. User activity and delivery retries can still delay
the next navigation.
**Auto-refresh** is a target between sweep starts; completed scans receive a
1–5 minute break even if a long scan runs past its target. Larger catalogues,
slow loading, user activity, and unresolved verification can extend the target.
The active tab records
a page as visited only after the server acknowledges its items and the reading
pass completes. Newly rendered cards are acknowledged too; changes to the item
set or page height before navigation trigger another reading pass. A failed
or still-running delivery keeps that page open and retries with capped, jittered
backoff. Stale or empty sweeps are recovered without allowing a passive tab to
race the active one.

Relay **2.19.1** limits a stalled numbered-page control's cooldown to that
destination, so other pages retain normal scrolling and native pagination.
Retries keep the normal five-second startup allowance. Site-driven scroll
restoration during a page change does not interrupt navigation or release its
old-card guard; direct wheel, touch, pointer and keyboard input still pauses it.

If this was a fresh install started with `SEED_MODE=true`, wait until the first
full sweep completes. Then set `SEED_MODE=false` in `notifier.env` and recreate
the container so it reads the changed environment:

```powershell
docker compose up -d --force-recreate
```

## Verifying it works

```powershell
curl.exe -s http://127.0.0.1:8787/health
```

Shows the loaded rules, whether a push channel is configured, and how many items
are in the recorded-item state file.

With Telegram configured, send `/status` to your bot from the same account
identified by `TELEGRAM_CHAT_ID`. The reply confirms that the computer,
notifier process, and Telegram connection are online. It shows browser heartbeat
and sweep state, verification pauses, disabled auto-refresh, alert destination,
last tracked successful item alert, and the latest ingest-batch counts. It also
explains when a page produced no alerts because its items were already recorded
or filtered out. Those counts are not a whole-sweep total. Use `/help` for the
command list, `/filters` for every active rule, and `/test` for a labeled test
sent to the actual alert destination, bypassing rules and dedupe. Commands work
in your private bot chat even when item alerts go to your group.
The command uses outbound Telegram long polling;
it does not expose a port on your router or accept commands from other users.

To share alerts, create a private Telegram group containing you, the other
person, and the bot. From your own account, send `/usehere` in that group. The
bot confirms the switch and sends each future alert once to the group, where
both members receive it. Only the account in `TELEGRAM_CHAT_ID` can switch the
destination or request `/status`. Send `/useprivate` in your original private
bot chat to move alerts back. The chosen group survives container restarts.

If `INGEST_TOKEN` is configured, include
`-H "X-Ingest-Token: your-token"` on health and rules requests too.

The quickest end-to-end check is **Send test alert** in the extension Options.
The equivalent HTTP request is:

```powershell
curl.exe -X POST http://127.0.0.1:8787/test-notification
```

If you configured `INGEST_TOKEN`, add
`-H "X-Ingest-Token: your-token"`. This route bypasses filters and state, so it
can be repeated without inventing a new item id.

## Filters

Items in this program are **free**, so "minimum price" means minimum *retail
value* — "only wake me for the good stuff". Real listings run roughly **$4–$45**,
so keep thresholds low; a $100 floor is silence forever.

**The easy way: the extension's Options page.** Its *Alert filters* section
edits minimum/maximum value, required keywords and excluded keywords.
The priority selector has been removed; saving filters uses normal alerts.
Existing file-based priority settings remain supported for compatibility.
Those save to the notifier over HTTP rather than being stored in the browser —
the notifier is the only thing that applies them, and a second copy in the
browser would be a second source of truth that silently disagrees.

Rules are re-read on every relay, so a save takes effect within a refresh cycle
with no restart. They apply only when an item is first observed. Matching,
non-matching, and seed-mode items are all recorded in `data/seen.json`; the file
is a record of handled items, not merely a history of alerts. Loosening a filter
does not replay older items that were previously filtered out.

**The full way: JSON.** The Options form edits one combined rule. For several
rules with different priorities, edit the file. Rules are evaluated top-down and
the first match sets the priority. Precedence:

| Layer | |
|---|---|
| `RULES_JSON` env var | Overrides everything; set in `notifier.env` |
| `data/rules.json` | Written by the Options page. Delete to revert. |
| `src/rules.json` | The defaults that ship with the repo |

Saving from the Options page never touches `src/rules.json`, so **Reset to
defaults** always has something clean to fall back to.

| Field | Meaning |
|---|---|
| `keywords` | Match if **any** appear in title/badge/URL (whole-word) |
| `match_all_keywords` | Require every keyword instead |
| `exclude_keywords` | Veto — always wins over `keywords` |
| `min_value_usd` / `max_value_usd` | Retail-value bounds |
| `categories` | Restrict to a badge: `clearance`, `rollback`, `new`, `reduced price` |
| `alert_on_unknown_value` | Default `true`: alert when the value can't be parsed |
| `priority` | `low`, `normal`, `high`, `urgent` (`urgent` bypasses DND on Pushover) |

Not sure what to filter on yet? In the Options page, clear the keyword box and
set minimum value to `0` — that alerts on everything, so you can watch for a day
and see what actually drops before narrowing it.

## Keeping it running

The server has to be up whenever Chrome is relaying. Compose sets
`restart: unless-stopped`, so **reviewer-item-notifier** comes back automatically
when Docker Desktop starts. There is no PowerShell window to leave open. Docker
Desktop shows whether it is healthy and has a **Logs** tab; the command-line
equivalents are:

```powershell
docker compose ps
docker compose logs -f notifier
docker compose restart notifier
```

`restart` is for restarting the same configuration. After changing
`notifier.env`, use `docker compose up -d --force-recreate` so the new settings
are loaded.

Use `docker compose stop` when you intentionally want it off, and
`docker compose start` to resume it. Docker Desktop, Chrome, the signed-in
reviewer tab, and the computer must still be running. A sleeping machine relays
nothing; moving the computer to a different internet network is fine.

The bind mount `./data:/app/data` means rebuilding or replacing the container
does not reset saved rules or dedupe history. Do not run multiple notifier
containers against that directory at once.

## Recovery after closing Chrome or restarting Windows

The extension can recover a missing tab while Chrome is running. For recovery
when Chrome or Docker Desktop has stopped, install the host supervisor once
from this repository, after creating the project `.venv`:

```powershell
.\install-startup.ps1 -WhatIf
.\install-startup.ps1
```

This starts a hidden helper now and at your Windows login, under your current
user without a stored password. It checks the extension heartbeat every 30
seconds and opens the reviewer page after three minutes without a heartbeat.
It selects the Chrome profile with this unpacked extension installed. If it
cannot identify that profile, it logs the problem and leaves Chrome alone.
When the local notifier is unavailable, it starts installed Docker Desktop and
runs `docker compose up -d --no-build` against the existing local image.
Build the image during initial setup; recovery does not download or rebuild it.
You must sign in to Windows for interactive Chrome recovery to run.

Edit `data/browser-recovery.json` to tune the helper; changes apply on its next
check. `browser-recovery.example.json` documents the defaults. Browser launches
are limited to once per outage while Chrome is running, at least ten minutes
apart and at most three per hour. Fresh heartbeats rearm recovery. Item age
does not trigger a launch, so quiet or deduplicated scans stay undisturbed.
Verification pauses and auto-refresh `0` suppress browser launches, including
across helper/notifier restarts. The local notifier can still be restored so
it can receive the heartbeat confirming that verification is finished.

Operational logs target **60 days**, rotating daily or when a segment fills and
compressing older segments. Space backstops remove the oldest history sooner
if needed: **32 MiB** for Discord delivery logs, **16 MiB** for backend logs and
**2 MiB** for recovery logs. Docker's separate recent console cache is capped
at **30 MiB**. See [log retention and viewing](tasks/discord-delivery-monitoring.md#watch-the-logs)
for paths and compressed archives. These limits cover logs; delivery databases,
backups and manually saved diagnostic exports are separate files.

Inspect `data/browser-recovery.log` for recovery logs. Preview the
current decision without launching anything using
`.\start-docker.ps1 -Once -DryRun`. Set `enabled` to `false` in the recovery
JSON to stop recovery actions, or run `.\install-startup.ps1 -Remove` to stop
the helper and remove its login task. Stop recovery before intentionally
shutting down the notifier. The helper never completes Press & Hold for you.

## Sharing alerts with a group

One signed-in reviewer browser can feed the notifier's selected Telegram group.
Adding group members does not add Walmart scans or one Telegram request per
member; each item alert is posted once to the group. The browser, this machine,
the local notifier and Telegram must all be available for fresh alerts. A
Telegram success response confirms provider acceptance, not each member's
device notification or reading the message.

If Telegram reports rate limiting, the notifier honors its `retry_after` delay
for that destination. Repeated sends during the cooldown return retryable
failure without more Telegram requests; unsent items stay eligible and the
extension keeps the page open until delivery succeeds. A group cooldown does
not intentionally block the owner's private chat. This reacts to provider
limits; it does not guarantee that every burst can be delivered immediately.

Full-catalogue discovery time and provider delivery time are separate. Saved
refresh settings are targets; page coverage, verification pauses and delivery
retries can extend them. Treat the service as monitored automation requiring
operator attention for verification. Before a wider rollout, verify complete
scan coverage, a real received alert, burst/failure handling and restart recovery.
The current tests and short live runs do not establish a discovery-time or
availability guarantee.

## If it goes quiet

No alerts can mean "nothing matched your filter" or "the browser stopped
feeding it", and those look identical from the notifier's side. The notifier
therefore watches the age of the last successful relay and sends one alert when
it exceeds `STALE_RELAY_HOURS` (default 2). A failed warning retries on the next
five-minute check; a delivered warning remains quiet until relays resume.

Check whether the browser is waiting for verification. Missing reviewer tabs
are reopened automatically while monitoring is enabled; the optional Windows
recovery helper also covers Chrome and Docker Desktop stopping. You can still
open <https://www.walmart.com/reviews/claim-product> manually to resume a scan.

To check by hand:

```powershell
curl.exe -s http://127.0.0.1:8787/health
```

Check `runtime.last_successful_relay.age_seconds`, `runtime.browser`, and
`runtime.last_notification`. The extension reports browser state once a minute;
heartbeats do not count as item relays or reset the silence warning. Old alert
timestamps are unknown until the first item delivery tracked by this version.
An explicit auto-refresh setting of `0` stays disabled and is shown as a warning.

## Editable scan settings

Version 2.8 saves scan settings in `data/relay-settings.json` on the notifier.
Options writes the same file. Manual edits are picked up on the extension's
one-minute heartbeat without restarting the notifier; search-mode changes take
effect on the next sweep. Keep the extension loaded and the browser awake.

```json
{
  "schemaVersion": 1,
  "refreshMinutes": 10,
  "pageDelaySeconds": 15,
  "sweepMode": "none"
}
```

Use `relay-settings.example.json` as a starting point for a new file. Refresh
accepts 0–120 minutes (0 disables automatic restarts), page delay accepts 5–60
seconds, and mode is `none`, `sweep`, or `high-value`. The example is a faster
baseline, not a discovery deadline: every results page must still be checked,
and loading or retries can extend the cycle. Cleared verification restores the
saved timings immediately, with no 24-hour speed penalty. There is no evidence
that items are posted on a fixed 30-minute schedule. Stored `seen_at` values
measure discovery, not the site's original publication time.

A missing file preserves an existing installation's Chrome settings. Invalid
edits or connection failures keep its last working cache and show a warning.
The endpoint and token remain in Chrome; do not put credentials
in the scan JSON. Alert filters remain in the separate `data/rules.json`.

Version **2.22.0** also uses `data/scanner-settings.json` for additional coverage,
tab grouping/rotation, verification preferences and the reviewer path pattern.
Use `scanner-settings.example.json` as a starting point. Set
`auxiliaryKeywordQueries` to a list of at most 100 terms (80 characters each),
or `null` for the 65 default terms beginning Furniture, Chair, Chairs, Couch,
Sofa and Pet. Options edits the same file. Backend changes replace the Chrome
cache on the next heartbeat; malformed files or outages retain usable settings.
Verification preferences do not grant Chrome permissions or reset attempt limits.

`data/scanner-status.json` contains the latest home, department, keyword and
verification status, effective preferences and restart receipts. It has no
credentials, product details or full URLs. `/scanner-status` adds a `stale` flag
after two minutes without a report; the file's `receivedAt` records freshness.
This allows local tools to inspect status without opening Chrome Options.

To request a fresh pass from local tools, save `data/scanner-commands.json` with
`schemaVersion: 1`, `restartHomeId`, and `restartAdditionalId`. Use a new short
identifier for each requested action; use `null` for no action. The same ID may
address both actions. Requests run once per action, wait during verification,
and preserve recorded items and verification limits. An interrupted reserved
request is reported and requires a new ID to retry. Settings, commands and status
also have local APIs under the existing token policy and trusted-origin checks;
no arbitrary browser commands are exposed.

## If Walmart shows "Robot or human?"

That page (`walmart.com/blocked`), or an inline verification overlay, means
Walmart is requesting verification. The extension notices it and:

- stops sweeping and retains acknowledged progress for compatible recovery,
- sends an urgent alert to your phone,
- pauses until a usable reviewer page confirms you finished verification, with
  a fallback cooldown of 1 hour that doubles on subsequent checks (max 24 hours).

**Solve the Press & Hold by hand.** The extension never tries to get past it.
Monitoring resumes automatically when the challenged tab shows reviewer items
again, or when you reopen a usable reviewer tab. Every other tab known to show
the challenge must be solved, closed, or navigated away first; otherwise the
same outage could immediately pause monitoring again. Cached passive tabs do
not clear a verification pause. **Restart sweep** remains a manual recovery option.
Repeated events from the same unresolved check count once. As soon as the check
is cleared, the saved page delay and scan target apply immediately. Challenge
history is retained for diagnosis and does not slow subsequent usable scans.
Hidden verification placeholders and cached cards underneath a visible challenge
cannot incorrectly resume monitoring.

Version 2.13 adds a persistent red **!** toolbar badge and a “verification
detected — needs attention” tooltip. The normal notification-badge timer and
late item deliveries cannot clear or overwrite it during the pause. The warning
restores after a worker restart and clears when usable reviewer content resumes.
Options also reports when the local notifier did not confirm the verification
alert. These are detection/attention features; automatic CAPTCHA solving is not
installed in the extension.

The extension tries the site's own pagination controls, including exact page
links when the pager wrapper changes. Version 2.12 also prefers the recognized
pager's **Next Page** control for an adjacent page when its current-page marker
agrees with the URL, with numbered placeholder controls as a fallback. Filters
must still match. A click that never changes the destination gets a five-second
start window before one paced site-control retry; a started transition gets up to
30 seconds to render fresh cards. Fragment-only changes do not extend that wait.
Client-side navigation waits for changed item cards so old content
is not recorded under a new page number. In2.24.0, two no-op attempts can fall
back to one ordinary URL load. A failed control then waits up to60seconds before
a bounded probe instead of causing repeated cooldown reloads. Other control
types can still work. This state survives page loads; every probe retains its
five-second startup allowance. Updating
the extension clears the native-control history; ordinary browser restarts retain it. Jitter never
reduces the saved minimum page delay.

Completed cycles add0–10% timing variation with a stable per-cycle deadline;
heartbeats cannot keep postponing the same restart. Initial department and
keyword starts are offset by one and two saved page intervals when both are
enabled. Auxiliary target starts, including recovery and repeated rounds, share
a persisted spacing reservation. These waits preserve every available page.

`data/browser-diagnostics.json` now includes `report.metrics`, cumulative since
`measurementStartedAt`. `successfulTransitions` counts a changed document/page
after each tab's first acknowledged results page; late-card repeats do not count.
`episodesPer100Transitions` is null until that denominator is positive.
`verificationDowntimeMs` includes an unresolved episode; completed downtime is
reported separately. Upgrading during verification observes one partial episode
and counts only downtime after measurement begins. Native attempts, retries and
URL fallback reasons are also counted. Counters survive worker restarts and the
300-event trace limit; up to64 recent tab identities support deduplication. Old
truncated history is not backfilled. Compare comparable workloads and elapsed
windows before drawing conclusions about challenge frequency.
Failed-page recovery uses increasing
delays capped at five minutes instead of continually reloading. No new browser
automation library or simulated mouse movement is required.

Version 2.17.0 recognizes Walmart's neutral `affinityOverride=default` on a
rendered destination, avoiding a false pagination stall and repair reload. It
also brings an offscreen pager control into view during the existing page wait
on a visible, idle tab. Version 2.17.2 varies the eased movement over 600–900 ms
and its landing point within 45–60% of the viewport, with an occasional small
6–18 px settling correction. This adds no navigation delay, respects reduced
motion and stops on real user input. Effects on verification frequency are
unmeasured. See [behavior research and activation status](tasks/browsing-behavior-20261023.md)
for evidence, limits and the combined-version handoff.

**Options → Export diagnostics** downloads a JSON report containing extension
version, scan settings, and up to 300 recent navigation, page-load, pacing,
retry and verification events (maximum age seven days). It contains no
credentials, cookies, product details, full URLs or raw error responses. History
is local and capped, so export soon after a prompt. The report can distinguish
page reloads from native pagination and show retries leading up to a challenge;
it cannot reveal Walmart's private reason for requesting verification.

Version 2.7 also mirrors that safe snapshot to the notifier on this computer.
Rebuild the notifier when upgrading from 2.6 (`docker compose up -d --build`),
then reload the extension and reviewer tab. The worker sends changed snapshots
on its one-minute heartbeat, resends quiet snapshots every five minutes, and
retries failures without stopping item relays. The mirror survives notifier
restarts in `data/browser-diagnostics.json` and is readable at
`http://127.0.0.1:8787/browser-diagnostics` (with the ingest token if configured).
It does not make extra Walmart requests or reset the stale-item warning.

After a page is acknowledged, newly rendered cards on that same page are
relayed after about one second during the existing wait before navigation and
after sweep completion, without restarting the sweep or reloading the site.
Previously acknowledged cards are skipped locally before title/value parsing.
Full scans and navigation checks still read every card. Failed sends retry with
backoff and hold pending navigation until delivery succeeds. This helps when
the site updates the open page; it cannot
discover unseen items on other pages. New items can appear on later pages,
so polling only page 1 cannot guarantee prompt discovery across the catalogue.

A single empty observation cannot finish a scan. An empty page is checked again
in the current document after two seconds, then reloaded once after another two
seconds. A confirmed empty tail closes the scan at the last populated boundary,
including when a stale pager still advertises that empty final page. Any unread
earlier pages are visited before completion. A confirmed empty interior page
reports a failed pass and restarts from page 1 after the bounded recovery delay.

Version 2.11 can save verification evidence in **Downloads/ReviewerItemRelay**:
a JPEG of the visible challenge and its safe diagnostic JSON. With the
verification tab selected, click the extension icon to grant Chrome's temporary
`activeTab` permission and capture before Options opens. Automatic capture is
attempted on detection when that grant is already available; unrelated tabs are
never activated or captured. If a redirect is detected while still loading,
completion/probe events retry capture; accepted images remain deduplicated.
One accepted screenshot download per unresolved
check prevents duplicates. Download settings can still prompt or interrupt saving.
Screenshots include the visible page; the JSON never includes cookies, credentials,
page HTML or screenshot pixels. FlareSolverr is not integrated: its
[official README](https://github.com/FlareSolverr/FlareSolverr#captcha-solvers)
reports nonworking CAPTCHA solvers and provides no proven support for this check.

`tools/verification-assist.mjs` provides a separate operator helper for an already
connected CUA browser tab. It requires confirmation for the current challenge,
locates and hit-tests the visible modal or full-page control, sends one bounded
hold, and waits up to 20 seconds for the delayed outcome without pressing again.
It ignores hidden/unrelated dialogs, checks visible challenge markers throughout
settling, and requires usable reviewer content without a challenge for at least
one second before returning `verified: true`. Hidden cached cards and temporary
prompt disappearance cannot confirm recovery. A replacement layout remains
pending without another hold; `challengeChanged` describes a layout change,
not a unique challenge identity. Browser errors are indeterminate and must not
trigger a blind retry. Its default eight-second hold succeeded in earlier
approved trials; [HUMAN documents variable hold timing](https://docs.humansecurity.com/applications/create-a-human-challenge),
so that duration is not a universal threshold. It is not loaded by the extension and
does not run unattended or provide a standalone CLI. The final targeting and
settling guards are regression-tested. The updated helper flow cleared one
approved live modal with its eight-second hold, then confirmed the visible
empty-results page after about four seconds of settling. The extension needed
one ordinary page refresh to restore its content-script connection afterward.
This is one successful trial, not an unattended reliability guarantee.

**Reviewer Item Relay 2.22.2 includes automatic verification in the regular
extension.** The former `verification_helper` companion is superseded; disable
it before enabling the built-in solver. Reload the existing unpacked Relay in
`chrome://extensions`, accept Chrome's new debugging permission, and confirm
version **2.22.2**. The upgrade repairs the current reviewer page. Then use
Relay Options → **Enable automatic verification**. No second installation is
needed. Existing scan settings, tokens and delivery state are preserved.
Automatic verification defaults to off and does not inherit the companion's
separate enabled switch.

The debugger permission is broad and cannot be requested as optional. Combining
the extensions is supported; the prior split was a design choice to isolate
that permission. Source restricts attachment/input to the exact Walmart reviewer
and blocked routes. Chrome may show a debugging banner, and another debugger
can prevent attachment. **Disable automatic verification** cancels a running
hold at its next check and stops future attempts.

The built-in verifier polls the scoped widget for readable completion and numeric
progress in accessible shadow trees. It releases on completion, stops after four
seconds without advancing progress, and caps a hold at 30 seconds with an
independent release watchdog. Without readable progress, its first hold is 15
seconds; retries increase this by three seconds each, up to 30 seconds.
It requires an unchallenged catalogue or explicit empty result for one second
before reporting recovery. An unsuccessful completed attempt schedules another
after about 10–20 seconds, up to ten attempts per tab/check and ten starts per
hour. Counts and retry deadlines survive worker restarts; short timers plus an
alarm/startup/heartbeat fallback recover pending retries. Chrome suspension can
delay them. Options shows the attempt number, release reason and next retry.
Reservations are persisted before input and tabs are serialized. Ambiguous input,
interruption or cleanup stops retries; all unconfirmed outcomes leave scanning
paused. A control-attachment failure that sends no input can retry after a
one-minute cooldown. Existing completed legacy failures can resume as attempt two
when their last result matches the last reservation; uncertain input stays spent.
The move-timeout follow-up also retries a timed-out pointer move when the driver
confirms no press was sent and cleanup completed. These retries keep the same
attempt limits and pacing. Unknown old failures or uncertain presses stay stopped.
Version 2.17.1 separates preparation time from the requested hold interval and
reports whether Chrome confirmed the press/release plus the failing input stage.
Input commands have a bounded five-second acknowledgement window. After an
input-error stop, Options offers **Retry current check**: release your mouse
button before clicking it. This explicit retry preserves all existing check and
hourly attempt limits; reloading alone does not rearm uncertain input.
The Chrome transport does not close tabs or save/send page bodies,
widget text, screenshots, cookies or credentials. Startup, enabling, pause changes and the
regular heartbeat all check the existing stored verification pause; a new
detector message is not needed to start. Solver status and its last outcome
appear in the regular Relay Options. See
[`tasks/verification-automation.md`](tasks/verification-automation.md)
for checks and current activation status. The verifier's actual Chrome input
transport, readable feedback on Walmart's widget, and live unattended reliability
still need validation; the preceding successful CUA trial does not prove them.

**Optional Windows mouse fallback (2.19.0):** run
`./install-native-verification.ps1` from this repository, reload Relay, then select
**Enable Windows fallback** in Options and grant its optional Native Messaging
and scripting permissions. The on-demand helper uses the project Python and a
locally compiled launcher; no admin access, network listener or download is used.
Installation and removal details are in [`native_verification/README.md`](native_verification/README.md).

Chrome input remains first. A matching timed-out move with explicit no-press and
confirmed cleanup, or a completed unsuccessful hold with confirmed press,
release and cleanup, permits the native transport on the next budgeted attempt.
Ambiguous input is never automatically replayed. The helper waits for five seconds
of current-session user idle before selecting the reviewer tab, maps its exact
viewport/DPR to Chrome's supplied Windows HWND, and holds the real mouse button.
User input, tab/window changes, navigation and a locked desktop cancel input.
A separate release-only process enforces parent-death/heartbeat and 30-second
limits; each connection can press once. A private temporary extension tab binds
the host to the correct Chrome window and closes after the attempt. Native page
reads use a fixed isolated DOM function rather than Chrome debugger commands;
inaccessible widget feedback uses the bounded hold, followed by catalogue
readiness checks. Existing attempt counts and hourly limits remain authoritative.
This requires an unlocked interactive Windows session and cannot guarantee
HUMAN acceptance. Automated unit tests use fake mouse input; live input and
end-to-end recovery must be distinguished from local protocol/safety evidence.

The following live 2.12 scan covered all 17 populated pages in 5 minutes 59
seconds with no new challenge or failed/pending deliveries. A stale empty page
18 closed at the populated boundary after about 5.8 seconds, including one
confirmation reload. The following scheduled scan hit another verification
redirect immediately; clearing the prior check did not prevent recurrence.
Five minutes remains a scan-start target, not a guarantee
that the whole catalogue is revisited within five minutes.

Version 2.14 uses native page-one navigation for scheduled scan restarts when
the current search has a matching control, with a full-load repair fallback.
It also reuses a confirmed empty boundary for up to ten minutes within the same
document and search: every pass still visits that page, and two empty observations
two seconds apart can avoid another confirmation reload. New cards, an increased
page count, a different search, expiry or a challenge require fresh proof.
Verification removal now requests immediate resumption after the worker pauses
the tab; screenshot and notifier work cannot hold the resume queue. This improves
detection/recovery and does not add unattended challenge-solving.
Diagnostics record the chosen restart path and the interval/elapsed/remaining
page wait. Version 2.15 reads persisted native failures before accepting a
scheduled restart and records native success only after the destination renders.
Regular monitoring selects an unfiltered catalogue tab; manual search/filter tabs
stay separate. If only filtered tabs exist, it opens one background catalogue tab.
Queued actions and repair reloads revalidate scope so manual browsing releases
the scan lease. Reload the unpacked extension and refresh reviewer tabs to activate
the new content scripts.

These changes preserve page coverage, filters and notifications. They reduce
unnecessary traffic and improve diagnosis; they do not guarantee elimination of
verification prompts. After upgrading, reload the extension and the reviewer
tabs so both the worker and page scripts run the same version.

If Chrome reports **Extension context invalidated** after an extension reload,
refresh the affected Walmart tab once. Its old page script has lost access to
the extension. Current content scripts detect this, stop their observers,
timers and queued navigation, and leave saved progress intact. Ordinary missing
worker, fetch and HTTP 503 errors keep their existing retry behavior.

Page-load frequency can contribute to verification checks, but the extension
cannot identify Walmart's private reason for any particular challenge.
**Auto-refresh** controls how often a full scan is attempted. A full sweep is
roughly one page load or native navigation per results page. For a 24-page
catalogue, refreshing every 3 minutes
works out to roughly 480 loads an hour. Every 30 minutes is about 48, and
every 60 minutes about 24. If checks keep coming back, raise the interval.
Repeated checks put the account at risk.

## What the extension actually reads

Per item card, from the page you already have open:

- the `/ip/<id>` link — the item id and its URL
- **`Free (Valued at $X)`** — the retail value the rules filter on. Not the
  `$5.99 Was $6.99` in the card's own link text: that is the sale price, and on
  a clearance item the two differ.
- the merchandising badge (`Clearance`, `New`, `Rollback`, `Reduced price`)
- `Out of stock` — such items are skipped, since you cannot claim them
- `Free items remaining: N` — included in the notification, because zero claims
  left is the difference between an alert worth acting on and one that is not

## Development

To run without Docker, install Python 3.11+ and use the original launcher:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r src\requirements.txt
.\run.ps1
```

Stop the Docker container first so port 8787 is available.

```powershell
.venv\Scripts\pip install pytest ruff
```

```powershell
.venv\Scripts\python -m pytest
node --test tests\test_extension_runtime.mjs
```

Tests use an in-memory store and a fake notifier — no network, and the HTTP
tests bind a real server to an ephemeral port.

## Layout

| Path | |
|---|---|
| `src/server.py` | Local HTTP server: `/ingest`, `/health`, `/rules`, `/test-notification` |
| `src/pipeline.py` | source → filter → dedupe → notify |
| `src/filters.py` | Rule engine |
| `src/state.py` | Recorded-item state (`data/seen.json`) plus in-memory delivery claims |
| `src/sources/parsing.py` | Item/price extraction from raw markup (fallback path) |
| `src/notifiers/` | ntfy, Pushover, Telegram |
| `extension/` | MV3 browser companion — reads the page, drives the refresh |
| `Dockerfile` / `compose.yaml` | Recommended notifier runtime |
| `run.ps1` / `notifier.example.env` | Python fallback and settings template |
| `tests/` | Python and extension runtime tests |
| `docs/architecture.md` | Design notes and failure behaviour |
