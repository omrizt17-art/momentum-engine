#!/usr/bin/env bash
# One-command Azure set-up for the Momentum & Chaos Engine.
#
#   az login                               # once, in the same terminal
#   bash deploy/azure_setup.sh             # Azure OpenAI only (needed)
#   bash deploy/azure_setup.sh --eventhubs # also Event Hubs
#   bash deploy/azure_setup.sh --app       # also the dashboard on Azure Container Apps
#
# It writes your keys to .env (git-ignored) and never prints them. It is safe to run again: existing resources are reused.
# Run it from the repo folder, in Git Bash, WSL or Azure Cloud Shell (bash).
#
# Tested on 2026-10-26 on an Azure for Students subscription (Azure OpenAI part: gpt-4.1-mini in francecentral). The Event Hubs and
# Container Apps parts are NOT tested yet. If a step fails, the message says which one;
# docs/AZURE.md has the same steps by hand and a troubleshooting table.

set -euo pipefail

LOCATION="${LOCATION:-francecentral}"   # one region for everything. Azure for Students only allows a few (francecentral, austriaeast, switzerlandnorth, uaenorth, israelcentral)
RG="${RG:-rg-momentum}"
SUFFIX="${SUFFIX:-$(printf '%s' "$(az account show --query id -o tsv)" | tr -d '-' | cut -c1-6)}"  # stable per subscription, so a second run reuses the names
OPENAI="${OPENAI:-momentum-openai-$SUFFIX}"
DEPLOYMENT="${DEPLOYMENT:-narrator}"
HUBNS="${HUBNS:-momentum-ns-$SUFFIX}"
HUB="${HUB:-match-events}"
APP="${APP:-momentum-engine}"
API_VERSION="${API_VERSION:-2024-10-21}"
WANT_HUB=0; WANT_APP=0
for a in "$@"; do case "$a" in --eventhubs) WANT_HUB=1 ;; --app) WANT_APP=1 ;; *) echo "unknown option: $a"; exit 2 ;; esac; done

step() { printf '\n== %s\n' "$*"; }
setenv() {  # setenv NAME VALUE : write or replace a line in .env without printing the value
  touch .env
  grep -v "^$1=" .env > .env.tmp || true
  printf '%s=%s\n' "$1" "$2" >> .env.tmp
  mv .env.tmp .env
}

step "Subscription"
az account show --query "{name:name, id:id}" -o tsv || { echo "Not signed in or no subscription. Run: az login"; exit 1; }

step "Registering the providers (one time per subscription, can take a minute)"
az provider register --namespace Microsoft.CognitiveServices --wait -o none

step "Resource group $RG ($LOCATION)"
az group create --name "$RG" --location "$LOCATION" -o none

step "Azure OpenAI resource $OPENAI"
if ! az cognitiveservices account show --name "$OPENAI" --resource-group "$RG" -o none 2>/dev/null; then
  az cognitiveservices account create --name "$OPENAI" --resource-group "$RG" --location "$LOCATION" \
    --kind OpenAI --sku S0 --custom-domain "$OPENAI" --yes -o none
fi

step "Model deployment '$DEPLOYMENT'"
if ! az cognitiveservices account deployment show --name "$OPENAI" --resource-group "$RG" --deployment-name "$DEPLOYMENT" -o none 2>/dev/null; then
  MODEL="${MODEL:-}"; VERSION="${VERSION:-}"
  if [ -z "$MODEL" ]; then  # gpt-4.1-mini if offered (works with the code as it is), else the first small chat model
    read -r MODEL VERSION < <(az cognitiveservices account list-models --name "$OPENAI" --resource-group "$RG" \
      --query "[?name=='gpt-4.1-mini' && capabilities.chatCompletion=='true'].[name,version]" -o tsv | head -1) || true
  fi
  if [ -z "${MODEL:-}" ]; then
    read -r MODEL VERSION < <(az cognitiveservices account list-models --name "$OPENAI" --resource-group "$RG" \
      --query "[?contains(name,'mini') && capabilities.chatCompletion=='true'].[name,version]" -o tsv | head -1) || true
  fi
  [ -n "${MODEL:-}" ] || { echo "No small chat model found in $LOCATION. Re-run with LOCATION=<another region> or MODEL=<name> VERSION=<version>."; exit 1; }
  echo "model: $MODEL $VERSION"
  az cognitiveservices account deployment create --name "$OPENAI" --resource-group "$RG" --deployment-name "$DEPLOYMENT" \
    --model-name "$MODEL" --model-version "$VERSION" --model-format OpenAI --sku-name GlobalStandard --sku-capacity "${CAPACITY:-50}" -o none \
  || az cognitiveservices account deployment create --name "$OPENAI" --resource-group "$RG" --deployment-name "$DEPLOYMENT" \
    --model-name "$MODEL" --model-version "$VERSION" --model-format OpenAI --sku-name Standard --sku-capacity "${CAPACITY:-50}" -o none
fi

step "Writing .env"
ENDPOINT="$(az cognitiveservices account show --name "$OPENAI" --resource-group "$RG" --query properties.endpoint -o tsv)"
KEY="$(az cognitiveservices account keys list --name "$OPENAI" --resource-group "$RG" --query key1 -o tsv)"
setenv AZURE_OPENAI_ENDPOINT "$ENDPOINT"
setenv AZURE_OPENAI_API_KEY "$KEY"
setenv AZURE_OPENAI_DEPLOYMENT "$DEPLOYMENT"
setenv AZURE_OPENAI_API_VERSION "$API_VERSION"
echo "AZURE_OPENAI_* written to .env"

if [ "$WANT_HUB" = 1 ] || [ "$WANT_APP" = 1 ]; then
  step "Event Hubs $HUBNS / $HUB"
  az eventhubs namespace show --name "$HUBNS" --resource-group "$RG" -o none 2>/dev/null \
    || az eventhubs namespace create --name "$HUBNS" --resource-group "$RG" --location "$LOCATION" --sku Basic -o none
  az eventhubs eventhub show --name "$HUB" --namespace-name "$HUBNS" --resource-group "$RG" -o none 2>/dev/null \
    || az eventhubs eventhub create --name "$HUB" --namespace-name "$HUBNS" --resource-group "$RG" --partition-count 2 -o none
  CONN="$(az eventhubs namespace authorization-rule keys list --namespace-name "$HUBNS" --resource-group "$RG" --name RootManageSharedAccessKey --query primaryConnectionString -o tsv)"
  setenv EVENTHUB_CONNECTION_STRING "$CONN"
  setenv EVENTHUB_NAME "$HUB"
  echo "EVENTHUB_* written to .env"
fi

if [ "$WANT_APP" = 1 ]; then
  step "Container Apps $APP (builds in Azure, no local Docker needed; takes a few minutes)"
  az extension add --name containerapp --upgrade -o none
  az provider register --namespace Microsoft.App -o none
  az provider register --namespace Microsoft.OperationalInsights -o none
  az containerapp up --name "$APP" --resource-group "$RG" --location "$LOCATION" --source . --ingress external --target-port 8501
  az containerapp secret set --name "$APP" --resource-group "$RG" --secrets "openai-key=$KEY" "eventhub-conn=${CONN:-none}" -o none
  az containerapp update --name "$APP" --resource-group "$RG" --min-replicas 1 --max-replicas 1 -o none \
    --set-env-vars "AZURE_OPENAI_ENDPOINT=$ENDPOINT" "AZURE_OPENAI_DEPLOYMENT=$DEPLOYMENT" "AZURE_OPENAI_API_VERSION=$API_VERSION" \
                   "AZURE_OPENAI_API_KEY=secretref:openai-key" "EVENTHUB_CONNECTION_STRING=secretref:eventhub-conn" "EVENTHUB_NAME=$HUB"
  echo "App URL: https://$(az containerapp show --name "$APP" --resource-group "$RG" --query properties.configuration.ingress.fqdn -o tsv)"
fi

printf '\nDone. Now test it:\n  python src/check_azure.py%s\n' "$([ "$WANT_HUB" = 1 ] && echo ' --eventhubs')"
