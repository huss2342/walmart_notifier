#!/usr/bin/env bash
# One-shot provision + deploy. Re-running is safe; Bicep is declarative.
set -euo pipefail

RG="${RG:-walmart-notifier-rg}"
LOCATION="${LOCATION:-eastus}"
PREFIX="${PREFIX:-wmrev}"
SEED_MODE="${SEED_MODE:-false}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for tool in az func; do
  command -v "$tool" >/dev/null || { echo "error: '$tool' not found on PATH" >&2; exit 1; }
done

: "${NTFY_TOPIC:?set NTFY_TOPIC to a long random string, e.g. \$(openssl rand -hex 16)}"

echo "==> Resource group $RG ($LOCATION)"
az group create --name "$RG" --location "$LOCATION" --output none

echo "==> Provisioning infrastructure (seedMode=$SEED_MODE)"
# Named so the outputs can be read back with az itself. Reading them through a
# JSON parser would mean depending on a `python3` that plenty of machines --
# Windows/Git Bash in particular -- simply do not have.
DEPLOYMENT="${PREFIX}-deploy"
az deployment group create \
  --resource-group "$RG" \
  --name "$DEPLOYMENT" \
  --template-file "$ROOT/infra/main.bicep" \
  --parameters \
      namePrefix="$PREFIX" \
      notifyProvider="${NOTIFY_PROVIDER:-ntfy}" \
      pollSchedule="${POLL_SCHEDULE:-0 */2 * * * *}" \
      ntfyTopic="$NTFY_TOPIC" \
      imapHost="${IMAP_HOST:-}" \
      imapUser="${IMAP_USER:-}" \
      imapPassword="${IMAP_PASSWORD:-}" \
      ingestToken="${INGEST_TOKEN:-}" \
      notifySecret="${NOTIFY_SECRET:-}" \
      seedMode="$SEED_MODE" \
  --output none

output() {
  az deployment group show --resource-group "$RG" --name "$DEPLOYMENT" \
    --query "properties.outputs.$1.value" --output tsv
}

APP_NAME=$(output functionAppName)
HEALTH=$(output healthUrl)
INGEST=$(output ingestUrl)

# The Key Vault role assignment is created alongside the app, so the app can
# come up before it can resolve @Microsoft.KeyVault(...) settings. A restart
# after provisioning re-reads them.
echo "==> Restarting $APP_NAME to pick up Key Vault references"
az functionapp restart --name "$APP_NAME" --resource-group "$RG" --output none

echo "==> Publishing function code to $APP_NAME"
( cd "$ROOT/src" && func azure functionapp publish "$APP_NAME" --python --build remote )

# The ingest route is auth_level=FUNCTION, so the URL is useless without its
# key. Key listing only works once the runtime has indexed the new code, which
# lags the publish by a few seconds.
echo "==> Fetching the ingest function key"
INGEST_KEY=""
for attempt in 1 2 3 4 5 6; do
  INGEST_KEY=$(az functionapp function keys list \
    --resource-group "$RG" --name "$APP_NAME" --function-name ingest \
    --query default --output tsv 2>/dev/null || true)
  [ -n "$INGEST_KEY" ] && break
  sleep 10
done
if [ -z "$INGEST_KEY" ]; then
  # Host keys work on any function in the app and are a fine fallback.
  INGEST_KEY=$(az functionapp keys list --resource-group "$RG" --name "$APP_NAME" \
    --query 'functionKeys.default' --output tsv 2>/dev/null || true)
fi

echo
echo "Deployed."
echo "  health: $HEALTH"
if [ -n "$INGEST_KEY" ]; then
  echo "  ingest: $INGEST?code=$INGEST_KEY"
  echo "          ^ paste this whole URL into the extension options page"
else
  echo "  ingest: $INGEST"
  echo "  warning: could not read the function key. The extension needs it. Run:" >&2
  echo "    az functionapp function keys list -g $RG -n $APP_NAME --function-name ingest --query default -o tsv" >&2
fi
echo "  ntfy:   subscribe to your topic in the ntfy app"
echo
if [ "$SEED_MODE" = "true" ]; then
  echo "SEED_MODE is on: items are recorded as seen and nothing is sent."
  echo "Let one poll cycle run, then redeploy with SEED_MODE=false to start alerting."
else
  echo "Note: on a fresh dedupe table the first run alerts on everything it finds."
  echo "To avoid that, deploy once with SEED_MODE=true, then again with false."
fi
echo
echo "Verify with:  curl -s $HEALTH"
