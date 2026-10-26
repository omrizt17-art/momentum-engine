#!/usr/bin/env bash
# One-command Azure set-up for the Momentum & Chaos Engine.
#
#   az login                               # once, in the same terminal
#   bash deploy/azure_setup.sh             # Azure OpenAI only (needed)
#   bash deploy/azure_setup.sh --eventhubs # also Event Hubs
#   bash deploy/azure_setup.sh --webapp    # also the dashboard on Azure App Service (works on student subscriptions)
#   bash deploy/azure_setup.sh --app       # or on Azure Container Apps (needs ACR Tasks: BLOCKED on Azure for Students)
#
# It writes your keys to .env (git-ignored) and never prints them. It is safe to run again: existing resources are reused.
# Run it from the repo folder, in Git Bash, WSL or Azure Cloud Shell (bash).
#
# Tested on 2026-10-26 on an Azure for Students subscription: Azure OpenAI (gpt-4.1-mini, francecentral) and the App Service dashboard
# (steps run by hand, same commands as --webapp). Event Hubs is NOT tested. Container Apps (--app) FAILED on that subscription (ACR Tasks blocked). If a step fails, the message says which one;
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
WANT_HUB=0; WANT_APP=0; WANT_WEB=0
WEBAPP="${WEBAPP:-momentum-engine-$SUFFIX}"
for a in "$@"; do case "$a" in --eventhubs) WANT_HUB=1 ;; --app) WANT_APP=1 ;; --webapp) WANT_WEB=1 ;; *) echo "unknown option: $a"; exit 2 ;; esac; done

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

if [ "$WANT_HUB" = 1 ]; then
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
  for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry; do az provider register --namespace "$ns" --wait -o none; done
  az containerapp up --name "$APP" --resource-group "$RG" --location "$LOCATION" --source . --ingress external --target-port 8501
  ENVVARS=("AZURE_OPENAI_ENDPOINT=$ENDPOINT" "AZURE_OPENAI_DEPLOYMENT=$DEPLOYMENT" "AZURE_OPENAI_API_VERSION=$API_VERSION" "AZURE_OPENAI_API_KEY=secretref:openai-key")
  SECRETS=("openai-key=$KEY")
  if [ -n "${CONN:-}" ]; then SECRETS+=("eventhub-conn=$CONN"); ENVVARS+=("EVENTHUB_CONNECTION_STRING=secretref:eventhub-conn" "EVENTHUB_NAME=$HUB"); fi  # only with --eventhubs
  az containerapp secret set --name "$APP" --resource-group "$RG" --secrets "${SECRETS[@]}" -o none
  az containerapp update --name "$APP" --resource-group "$RG" --min-replicas 1 --max-replicas 1 --set-env-vars "${ENVVARS[@]}" -o none
  echo "App URL: https://$(az containerapp show --name "$APP" --resource-group "$RG" --query properties.configuration.ingress.fqdn -o tsv)"
fi

if [ "$WANT_WEB" = 1 ]; then
  step "App Service $WEBAPP (code is zipped and built by Azure; no Docker and no ACR Tasks; takes about 10 minutes)"
  az provider register --namespace Microsoft.Web --wait -o none
  az appservice plan show --name plan-momentum --resource-group "$RG" -o none 2>/dev/null \
    || az appservice plan create --name plan-momentum --resource-group "$RG" --location "$LOCATION" --is-linux --sku B1 -o none
  az webapp show --name "$WEBAPP" --resource-group "$RG" -o none 2>/dev/null \
    || az webapp create --name "$WEBAPP" --resource-group "$RG" --plan plan-momentum --runtime "PYTHON:3.12" -o none
  az webapp config set --name "$WEBAPP" --resource-group "$RG" --web-sockets-enabled true --always-on true \
    --startup-file "python -m streamlit run src/app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false" -o none
  az webapp config appsettings set --name "$WEBAPP" --resource-group "$RG" -o none --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true WEBSITES_PORT=8000 \
    "AZURE_OPENAI_ENDPOINT=$ENDPOINT" "AZURE_OPENAI_API_KEY=$KEY" "AZURE_OPENAI_DEPLOYMENT=$DEPLOYMENT" "AZURE_OPENAI_API_VERSION=$API_VERSION"
  ZIP="${TMPDIR:-/tmp}/momentum_app.zip"; rm -f "$ZIP"
  python - "$ZIP" <<'PYZIP'
import os, sys, zipfile
z = zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED)
z.write("requirements.txt")  # only the app: no .env, no data, no tests
for root, dirs, files in os.walk("src"):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for f in files:
        if f.endswith(".py"):
            z.write(os.path.join(root, f))
z.close()
PYZIP
  az webapp deploy --name "$WEBAPP" --resource-group "$RG" --src-path "$ZIP" --type zip --timeout 900
  echo "App URL: https://$(az webapp show --name "$WEBAPP" --resource-group "$RG" --query defaultHostName -o tsv)"
fi

printf '\nDone. Now test it:\n  python src/check_azure.py%s\n' "$([ "$WANT_HUB" = 1 ] && echo ' --eventhubs')"
