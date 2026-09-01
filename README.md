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
That server applies your rules, skips anything it has already alerted on, and
pushes to your phone. Nothing leaves your computer except the notification.

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

The tuning knob is the refresh interval. Three minutes is a person checking
often; twenty seconds is not. Your account, your call.

## Setup

Windows, Python 3.11+, Chrome. Commands below are for **PowerShell** — the
paths use backslashes, so they will not work in Git Bash as written (and
forward-slash paths will not work in `cmd.exe`).

### 1. Install

```powershell
python -m venv .venv
```

```powershell
.venv\Scripts\pip install -r src\requirements.txt
```

The server itself is standard library only — `requests` is just for the push
notifiers.

### 2. Pick a push channel

ntfy is free and needs no account. Install the **ntfy** app on your phone, then
generate a topic name:

```powershell
python -c "import secrets; print(secrets.token_hex(16))"
```

Subscribe to that exact topic in the app. **The topic name is the password** —
anyone who knows it can read your alerts, so keep it long and random.

Copy `notifier.example.env` to `notifier.env` and paste it in as `NTFY_TOPIC`.
If you would rather get email than a push, set `NTFY_EMAIL` too — ntfy will send
both.

### 3. Seed the dedupe file

The portal shows about 30 items per page. On a fresh install every one of them
looks new, so the first relay would fire 30 notifications at once. Run once in
seed mode to record what is already there:

```powershell
.\run.ps1 -Seed
```

Leave it running for step 4, then Ctrl-C and start it normally:

```powershell
.\run.ps1
```

### 4. Install the extension

1. `chrome://extensions` → Developer mode → **Load unpacked** → pick `extension/`
2. Open its **Options**. The defaults are already correct for a local server —
   endpoint `http://127.0.0.1:8787/ingest`, path `^/reviews/claim-product`.
3. Set **auto-refresh** to `3` minutes (`0` disables it)
4. Save

### 5. Open the portal and leave it

Open <https://www.walmart.com/reviews/claim-product?q=> in a tab and leave it
there. Add more tabs with `?q=headphones`, `?q=air+fryer`, and so on — each one
relays independently, and the same item arriving twice still only buzzes once.

Back in the extension's Options, the **Status** panel should show
"Last relayed just now — N items read". If it says "Nothing relayed yet", the
server is not running or the endpoint is wrong.

## Verifying it works

```powershell
curl.exe -s http://127.0.0.1:8787/health
```

Shows the loaded rules, whether a push channel is configured, and how many items
are in the dedupe file.

To prove the whole chain end to end, post a fake item — your phone should buzz:

```powershell
curl.exe -X POST http://127.0.0.1:8787/ingest -H "Content-Type: application/json" -d '{\"items\":[{\"item_id\":\"test-001\",\"title\":\"Smoke test\",\"value_usd\":99.0}]}'
```

Change `test-001` each time — dedupe means the same id only ever fires once.

## Filters

Items in this program are **free**, so "minimum price" means minimum *retail
value* — "only wake me for the good stuff". Real listings run roughly **$5–$30**,
so keep thresholds low; a $100 floor is silence forever.

Rules live in `src/rules.json`, are evaluated top-down, and the first match sets
the notification priority. They are re-read on every relay, so edits take effect
within a refresh cycle — no restart.

| Field | Meaning |
|---|---|
| `keywords` | Match if **any** appear in title/badge/URL (whole-word) |
| `match_all_keywords` | Require every keyword instead |
| `exclude_keywords` | Veto — always wins over `keywords` |
| `min_value_usd` / `max_value_usd` | Retail-value bounds |
| `categories` | Restrict to a badge: `clearance`, `rollback`, `new`, `reduced price` |
| `alert_on_unknown_value` | Default `true`: alert when the value can't be parsed |
| `priority` | `low`, `normal`, `high`, `urgent` (`urgent` bypasses DND on Pushover) |

Not sure what to filter on yet? Put a rule with no clauses at the bottom and
`"priority": "low"` — it matches everything, so you can watch for a day and see
what actually drops. `src/rules.json` ships one commented out.

## Keeping it running

The server has to be up whenever Chrome is relaying. Simplest is a terminal
window left open. To start it automatically at logon:

```powershell
schtasks /create /tn "Reviewer Notifier" /tr "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\Users\Navi\Documents\repos\walmart_notifier\run.ps1" /sc onlogon
```

A sleeping machine relays nothing. That gap is real and there is no clean way to
close it without a credential bot, which this project deliberately does not do.

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

```powershell
.venv\Scripts\pip install pytest ruff
```

```powershell
.venv\Scripts\python -m pytest        # 115 tests
```

Tests use an in-memory store and a fake notifier — no network, and the HTTP
tests bind a real server to an ephemeral port.

## Layout

| Path | |
|---|---|
| `src/server.py` | Local HTTP server: `/ingest`, `/health` |
| `src/pipeline.py` | source → filter → dedupe → notify |
| `src/filters.py` | Rule engine |
| `src/state.py` | Dedupe file (`data/seen.json`), atomic writes |
| `src/sources/parsing.py` | Item/price extraction from raw markup (fallback path) |
| `src/notifiers/` | ntfy, Pushover, Telegram |
| `extension/` | MV3 browser companion — reads the page, drives the refresh |
| `run.ps1` / `notifier.example.env` | Launcher and settings template |
| `tests/` | 115 tests |
| `docs/architecture.md` | Design notes and failure behaviour |
