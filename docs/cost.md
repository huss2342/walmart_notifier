# Cost breakdown

Figures are US East list prices and round generously upward. Check current
[Functions](https://azure.microsoft.com/pricing/details/functions/) and
[Storage](https://azure.microsoft.com/pricing/details/storage/tables/) pricing
before relying on them.

## Monthly total: typically under $1

| Resource | Usage at default settings | Cost |
|---|---|---|
| Functions (Y1 Consumption) | 21,600 executions, ~2s each | **$0** |
| Storage account | Runtime state + Table entities, well under 1 GB | $0.10–$0.50 |
| Table Storage transactions | ~50k point reads/writes | < $0.01 |
| Key Vault | ~1k secret reads | < $0.01 |
| Application Insights | Sampled, far under 5 GB | $0 |
| ntfy.sh / Telegram | unlimited | $0 |

The Consumption plan's monthly free grant is **1,000,000 executions** and
**400,000 GB-seconds**. A 2-minute timer uses 21,600 executions — about 2% of
the execution grant — and at 128 MB for ~2 seconds, roughly 5,400 GB-s, under
2% of the compute grant. Both stay free even at a 1-minute cadence.

The bill is therefore almost entirely the storage account, which the Functions
runtime requires regardless.

## Keeping it there

- **Don't move off Consumption.** A Premium plan (EP1) has an always-warm
  instance at roughly $150/month. Nothing here needs it.
- **Watch Application Insights** if you raise the log level. Ingestion past
  5 GB/month is ~$2.30/GB. `host.json` enables sampling; set
  `enableAppInsights: false` in Bicep to drop it entirely.
- **Key Vault references resolve at app start**, not per invocation, so secret
  reads stay in the noise.
- **Cold starts are fine here.** A notifier that takes 3 seconds to warm up is
  indistinguishable from one that doesn't, and warm-up plans cost real money.

## Pushover

$5 one-time per platform, not a subscription. Worth it over free ntfy if you
want emergency-priority alerts that bypass silent mode — set `"priority":
"urgent"` on a rule and Pushover will re-alert until you acknowledge.
