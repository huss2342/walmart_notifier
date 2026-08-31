# Walmart reviewer item notifier

Instant phone alerts when new items appear in the Walmart Recognized Reviewer
("Spark Reviewer") program, filtered by keyword and minimum retail value.
Runs on Azure for well under $1/month.

## Does it work?

Yes. To be precise about which parts are which:

**The alerting engine is built, tested and working.** Given an item, it applies
your rules (`value >= $100`, keywords, exclusions), skips anything it has
already alerted on, and pushes to your phone. 102 tests cover it. That part is
done.

**It needs something feeding it items.** There are two feeds, and they cover
different situations:

| | Reads the actual portal | Runs when your computer is off | Needs |
|---|---|---|---|
| **Browser tab** | yes | no | a tab left open, signed in |
| **Email** | no — reads Walmart's mail to you | yes, 24/7 | Walmart to email you about drops |

**Check this first:** search your inbox for past Walmart/Bazaarvoice reviewer
mail. If they email you when items drop, turn on the email source and you get
genuine 24/7 alerting with nothing running on your machine. If they don't, the
browser tab is your real source and alerting runs whenever your computer is
awake. I could not confirm either way from public documentation — your inbox is
the authority.

Nothing stops you running both. They feed the same endpoint and the same dedupe
store, so an item arriving twice still only buzzes once.

### The leave-a-tab-open mode

This is the mode to use, and the extension does it. Set **auto-refresh** in the
extension options to e.g. 3 minutes and leave the reviewer page open in a tab.
It reloads itself, reads what appeared, and relays anything new. A reload is
deferred while you are clicking or scrolling, so it won't interrupt you
mid-claim, and the interval is jittered rather than metronomic.

It's your own browser, your own residential IP, your own session, and a real
browser fingerprint — which is why this works where a datacenter bot doesn't.
The honest caveat is that it is still automated retrieval on a schedule, so the
ToS clause quoted below is broad enough to reach it. It is much lower exposure
than a headless bot, not zero exposure. Your account, your call.

Its limitation is the obvious one: a sleeping laptop relays nothing. If you want
coverage while your machine is off and Walmart doesn't email you, there is no
clean way to get it — that gap is real, and closing it is exactly what would
require the credential bot below.

## Read this before you deploy

Your idea is technically doable, and most of it is built here. But the specific
approach you floated — storing your Walmart password in Key Vault and turning
off two-factor authentication so a script can sign in — is the one part I did
not build, for three reasons that are worth your time.

**1. Automated access to walmart.com is a Terms of Use violation, and the
penalty lands on the thing you're trying to protect.** Walmart's
[Terms of Use](https://www.walmart.com/help/article/walmart-com-terms-of-use/3b75080af40340d6bbd596f116fae5a0)
prohibit using "any robot, spider, site search/retrieval application or other
manual or automatic device to retrieve, index, 'scrape,' 'data mine' or
otherwise gather any Materials," and state that your account "may be restricted
or terminated for any reason, at our sole discretion." Recognized Reviewer
status is invite-only and revocable. A bot that gets you flagged costs you the
membership — the exact asset the whole project exists to exploit.

**2. It would not work reliably anyway.** Walmart runs PerimeterX/HUMAN and
Akamai bot management, which fingerprint the browser before the page renders.
A headless-Chromium login loop from an Azure datacenter IP, firing on a perfect
two-minute cadence, is close to a worst-case signature for that scoring. While
researching this project I hit a CAPTCHA fetching a *public* Walmart help page
from a datacenter. Getting past that needs residential proxies and fingerprint
spoofing — expensive, fragile, and squarely the kind of evasion that turns a
gray-area ToS issue into a deliberate one.

**3. Disabling 2FA is the costliest step and it buys you nothing.** Your Walmart
account holds saved payment methods and addresses. Turning off 2FA reduces it to
a single password that now also lives in a cloud service — and every path in
this repo works with 2FA left on. There is no version of this project that gets
better because you disabled it.

So this repo gets you the same outcome — instant, filtered, 24/7 alerts — from
sources you already have legitimate access to. **You still claim items by hand,
in your own browser.** There is deliberately no auto-claim: that is where a
notifier stops being a notifier.

You are the account holder and this is your call to make. If you deploy the
email path only, you never touch Walmart's servers at all.

## How it works

```
  ┌─ email source ──────┐
  │ your own mailbox    │  IMAP, your app password, 2FA stays on
  │ (Walmart emails)    │  polls every 2 min
  └──────────┬──────────┘
             │
  ┌─ browser extension ─┐        ┌──────────────────┐      ┌───────────┐
  │ the reviewer page   │──POST─▶│  Azure Function  │─────▶│ your phone│
  │ you already have    │        │  filter + dedupe │      │ ntfy /    │
  │ open and signed in  │        └────────┬─────────┘      │ Pushover /│
  └─────────────────────┘                 │                │ Telegram  │
                                  ┌───────▼────────┐       └───────────┘
                                  │ Table Storage  │
                                  │ (seen items)   │
                                  └────────────────┘
```

Two sources, both things you already have access to:

- **Email (`src/sources/imap_source.py`)** — reads Walmart/Bazaarvoice mail from
  your own mailbox over IMAP. No Walmart credentials, no scraping, 2FA stays on.
  Use a Gmail/Outlook **app password**: scoped to this one app and revocable
  without touching your main account. This is the recommended default.
  Fetching is incremental -- it remembers the highest message UID it has read
  and asks only for newer ones. Re-reading the last 25 messages every two
  minutes would be roughly a gigabyte a day against Gmail's IMAP bandwidth cap,
  which gets the mailbox throttled within days. `IMAP_BACKFILL_DAYS` (default
  `1`) bounds how far back the very first run looks.
- **Browser extension (`extension/`)** — a small Chrome extension that reads the
  reviewer page *you* have open in *your* signed-in browser and POSTs what it
  sees to your endpoint. With auto-refresh on it reloads that tab on a jittered
  interval, so leaving the tab open is the whole operating procedure. No stored
  credentials, no headless browser, no datacenter IP, nothing to evade. See
  [The leave-a-tab-open mode](#the-leave-a-tab-open-mode) for the trade-off.

For true push latency instead of a 2-minute poll, forward Walmart mail to the
`/api/ingest` endpoint via SendGrid Inbound Parse or Cloudflare Email Workers —
same endpoint the extension uses.

## Filters

Items in this program are **free**, so "minimum price" means minimum *retail
value* — "only wake me for the expensive stuff." Rules live in `src/rules.json`
or the `RULES_JSON` app setting, are evaluated top-down, and the first match
sets the notification priority.

```json
{
  "rules": [
    {
      "name": "big-ticket",
      "min_value_usd": 100.0,
      "priority": "high",
      "exclude_keywords": ["gift card", "protection plan"]
    },
    {
      "name": "watched-keywords",
      "keywords": ["laptop", "headphones", "air fryer"],
      "min_value_usd": 40.0,
      "priority": "normal"
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `keywords` | Match if **any** appear in title/category/URL (whole-word) |
| `match_all_keywords` | Require every keyword instead |
| `exclude_keywords` | Veto — always wins over `keywords` |
| `min_value_usd` / `max_value_usd` | Retail-value bounds |
| `categories` | Restrict to matching categories |
| `alert_on_unknown_value` | Default `true`: alert when the value can't be parsed |
| `priority` | `low`, `normal`, `high`, `urgent` (`urgent` bypasses DND on Pushover) |

`alert_on_unknown_value` defaults to alerting because email digests often omit
prices, and a missed $200 item costs more than a spurious buzz. Set it to
`false` if you would rather have silence than false positives.

## Setup

```bash
# 1. Pick a push channel. ntfy is free and needs no account:
#    install the ntfy app, subscribe to a long random topic name.
export NTFY_TOPIC="$(openssl rand -hex 16)"

# 2. Gmail app password (leave 2FA ON — app passwords require it):
#    https://myaccount.google.com/apppasswords
export IMAP_HOST=imap.gmail.com
export IMAP_USER=you@gmail.com
export IMAP_PASSWORD='xxxx xxxx xxxx xxxx'

# 3. Deploy. The first pass records what already exists without alerting --
#    without it, every item already on the page fires at once.
export INGEST_TOKEN="$(openssl rand -hex 24)"
SEED_MODE=true ./infra/deploy.sh

# 4. Verify, then deploy again for real
curl -s "https://<app>.azurewebsites.net/api/health" | python3 -m json.tool
./infra/deploy.sh
```

`deploy.sh` prints the ingest URL **with its `?code=` function key appended** --
the endpoint is useless without it. Copy that whole line.

Then set up the browser tab mode:

1. `chrome://extensions` → Developer mode → **Load unpacked** → pick `extension/`
2. Open its **options** page, paste the full ingest URL (including `?code=`)
   and your `INGEST_TOKEN`
3. Set **reviewer page path** to a regex matching your portal's URL path. The
   default `^/(reviewer|reviews)` is a guess -- open the portal and check.
   Order and returns pages are excluded no matter what you set, so the relay
   never reports your own past purchases as new items.
4. Set **auto-refresh** to 3 minutes (0 disables it; Chrome will not schedule
   an alarm below one minute)
5. Open your reviewer page in a tab and leave it there

Settings are stored in this browser only, never in Chrome sync: the endpoint
URL carries the function key.

The options page shows a **Status** panel — last relay time, items read, alerts
sent, and any error. If it says "Nothing relayed yet" after you've loaded the
reviewer page, the endpoint or token is wrong. Silent failure is the main risk
with a background relay, so check that panel occasionally.

## Cost

| | |
|---|---|
| Functions (Consumption) | **$0** — 21,600 runs/month at 2-min cadence, against a 1M free grant |
| Storage account + Table | ~$0.10–$0.50 |
| Key Vault | ~$0.01 (references resolve at app start, not per run) |
| App Insights | $0 under the 5 GB/month free grant |
| ntfy / Telegram | $0 (Pushover is $5 once) |

**Typically under $1/month.** See `docs/cost.md`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r src/requirements.txt pytest
.venv/bin/python -m pytest        # 102 tests
.venv/bin/pip install ruff && .venv/bin/ruff check .
```

Tests use in-memory dedupe and a fake notifier — no Azure and no network.

## Layout

| Path | |
|---|---|
| `src/function_app.py` | Timer, ingest and health triggers |
| `src/filters.py` | Rule engine |
| `src/pipeline.py` | source → filter → dedupe → notify |
| `src/sources/parsing.py` | Item/price extraction from mail and markup |
| `src/notifiers/` | ntfy, Pushover, Telegram |
| `infra/main.bicep` | Function App, Storage, Key Vault, RBAC |
| `src/state.py` | Dedupe claims and the IMAP high-water marker |
| `extension/` | MV3 browser companion |
| `tests/` | 102 tests; in-memory dedupe, fake notifier, no network |
| `.github/workflows/ci.yml` | pytest, ruff, `az bicep build`, `node --check` |
| `docs/` | Architecture and cost notes |
