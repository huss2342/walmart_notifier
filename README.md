# Walmart reviewer item notifier

Phone alerts when new items appear in the Walmart Recognized Reviewer
("Spark Reviewer") program, filtered by keyword and minimum retail value.

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
   verify that the displayed version is **2.2.1**.
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

### 5. Open the portal and leave it

Open <https://www.walmart.com/reviews/claim-product?q=> in one tab and leave it
there. One tab is recommended. If several eligible reviewer tabs are open, the
extension deterministically selects the first reachable one as active and keeps
the others passive. A passive tab takes over only if the active one becomes
unusable; separate query tabs are therefore fallbacks, not independent monitors.

Every sweep walks page 1 to the last page (detected by the portal's "no search
results" panel), pausing a configurable few seconds between each. Set **Delay
between pages** and **Auto-refresh** in the Options page. The active tab records
a page as visited only after the server acknowledges all of its items. A failed
or still-running delivery keeps that page open and retries with capped, jittered
backoff. Stale or empty sweeps are recovered without allowing a passive tab to
race the active one.

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
notifier process, and Telegram connection are online, and shows uptime, the last
successful reviewer-page relay, the latest ingest-batch counts, the current
value filter, and the number of recorded items. Those counts are not a whole-
sweep total. The command uses outbound Telegram long polling;
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
edits minimum/maximum value, required keywords, excluded keywords and priority.
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

## If it goes quiet

No alerts can mean "nothing matched your filter" or "the browser stopped
feeding it", and those look identical from the notifier's side. The notifier
therefore watches the age of the last successful relay and sends one alert when
it exceeds `STALE_RELAY_HOURS` (default 6). It re-arms when relays resume, so
one outage produces one alert.

Almost always it is the browser: the reviewer tab was closed, navigated away,
or is sitting on a bot check. Reopen
<https://www.walmart.com/reviews/claim-product> and the sweep picks up again.

To check by hand:

```powershell
curl.exe -s http://127.0.0.1:8787/health
```

`runtime.last_successful_relay.age_seconds` is the number that matters.

## If Walmart shows "Robot or human?"

That page (`walmart.com/blocked`) means Walmart has flagged the traffic as
automated. The extension notices it and:

- stops sweeping and discards the half-finished sweep,
- sends an urgent alert to your phone,
- pauses for 1 hour, doubling on each repeat within 24 hours (max 24 hours).

**Solve the Press & Hold by hand.** The extension never tries to get past it.
To resume before the pause ends, click **Restart sweep**.

The main thing that triggers it is page loads per hour, and that's mostly set
by **Auto-refresh**. A full sweep is ~24 page loads. Refreshing every 3 minutes
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
