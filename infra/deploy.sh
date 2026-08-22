#!/usr/bin/env bash
# One-shot provision + deploy. Re-running is safe; Bicep is declarative.
set -euo pipefail

RG="${RG:-walmart-notifier-rg}"
LOCATION="${LOCATION:-eastus}"
PREFIX="${PREFIX:-wmrev}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for tool in az func; do
  command -v "$tool" >/dev/null || { echo "error: '$tool' not found on PATH" >&2; exit 1; }
done

: "${NTFY_TOPIC:?set NTFY_TOPIC to a long random string, e.g. \$(openssl rand -hex 16)}"

echo "==> Resource group $RG ($LOCATION)"
az group create --name "$RG" --location "$LOCATION" --output none

echo "==> Provisioning infrastructure"
OUTPUTS=$(az deployment group create \
  --resource-group "$RG" \
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
  --query properties.outputs --output json)

APP_NAME=$(echo "$OUTPUTS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["functionAppName"]["value"])')
HEALTH=$(echo "$OUTPUTS"   | python3 -c 'import json,sys; print(json.load(sys.stdin)["healthUrl"]["value"])')
INGEST=$(echo "$OUTPUTS"   | python3 -c 'import json,sys; print(json.load(sys.stdin)["ingestUrl"]["value"])')

# The Key Vault role assignment is created alongside the app, so the app can
# come up before it can resolve @Microsoft.KeyVault(...) settings. A restart
# after provisioning re-reads them.
echo "==> Restarting $APP_NAME to pick up Key Vault references"
az functionapp restart --name "$APP_NAME" --resource-group "$RG" --output none

echo "==> Publishing function code to $APP_NAME"
( cd "$ROOT/src" && func azure functionapp publish "$APP_NAME" --python --build remote )

echo
echo "Deployed."
echo "  health: $HEALTH"
echo "  ingest: $INGEST"
echo "  ntfy:   subscribe to '$NTFY_TOPIC' in the ntfy app"
echo
echo "Verify with:  curl -s $HEALTH | python3 -m json.tool"
