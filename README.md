# Walmart reviewer item notifier

Instant phone alerts when new items appear in the Walmart Recognized Reviewer
("Spark Reviewer") program, filtered by keyword and minimum retail value.
Runs on Azure for well under $1/month.

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
- **Browser extension (`extension/`)** — a small Chrome extension that reads the
  reviewer page *you* have open in *your* signed-in browser and POSTs what it
  sees to your endpoint. No stored credentials, no headless browser, no
  datacenter IP, nothing to evade — it only ever sees a page you navigated to
  yourself. Note this is still automated reading of page content, which the ToS
  language above is broad enough to cover; it is materially less exposed than a
  datacenter bot, but it is not zero. Read the clause and decide for yourself.

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

# 3. Deploy
export INGEST_TOKEN="$(openssl rand -hex 24)"
./infra/deploy.sh

# 4. Verify
curl -s "https://<app>.azurewebsites.net/api/health" | python3 -m json.tool
```

Then load `extension/` via `chrome://extensions` → Developer mode → Load
unpacked, and paste the `ingestUrl` and `INGEST_TOKEN` into its options page.

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
.venv/bin/python -m pytest        # 69 tests
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
| `extension/` | MV3 browser companion |
| `docs/` | Architecture and cost notes |
