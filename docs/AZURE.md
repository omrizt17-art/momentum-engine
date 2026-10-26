# Azure guide: from zero to a running project

Written for: the team members who will set up Azure for the first time. All steps are in English and use the Azure
portal or Azure CLI. **Nothing here has been run against a real subscription yet** (the author had no Azure access),
so treat each command as a checked draft: if one fails, the troubleshooting list at the end covers the usual causes.

There are three Azure pieces. Do them in this order, and stop after step 2 if time is short.

| Step | Service | What it gives the project | Needed? |
| --- | --- | --- | --- |
| 1 | Azure OpenAI (in a Microsoft Foundry project) | The Analyst and Writer agents use a real model | **Yes**: this is the core "Microsoft AI platform" use |
| 2 | Azure Event Hubs | The live event feed goes through a cloud queue | Recommended (cloud-native category) |
| 3 | Azure Container Apps | The dashboard has a public URL | Recommended (judges can click it) |

Everything is optional for the code to run: without Azure the app uses templates. So you can never be blocked by Azure.

## Fast path (about 10 minutes once you have a subscription)

```bash
az login                                  # same terminal; choose the account that has the subscription
bash deploy/azure_setup.sh                # resource group, Azure OpenAI, a small model, and writes .env
python src/check_azure.py                 # OK or FAIL for each part, with the likely cause
```
Add `--eventhubs` to both commands for Event Hubs, and `--app` to `azure_setup.sh` for the public dashboard URL.
Run in Git Bash, WSL or Azure Cloud Shell. The script writes keys to `.env` and never prints them. It was **not run
against a real subscription** when it was written; if a step fails, the message names it, and the manual steps below
do the same thing. When `check_azure.py` says all checks passed, you may write "tested with Azure" in the pitch.

## 0. Before you start

1. **A subscription.** Sign in at https://portal.azure.com with your Microsoft account. Options for credit:
   - **Azure for Students** (about $100, no credit card), if your university is eligible: https://azure.microsoft.com/free/students
   - The free account ($200 for 30 days).
   - Check the hackathon page or Discord for credits or Azure access offered to participants.
2. **Azure CLI** (optional but faster): https://learn.microsoft.com/cli/azure/install-azure-cli, then `az login`.
   Or use **Azure Cloud Shell** (the `>_` icon in the portal, choose Bash): nothing to install.
3. Pick **one region** for everything and keep it. Use one where your model is available (check in step 1). Examples:
   `swedencentral`, `eastus2`, `westeurope`.

Names used below: resource group `rg-momentum`. Replace `<unique>` with something only you would pick (names are global).

```bash
az group create --name rg-momentum --location swedencentral
```

## 1. Azure OpenAI in Microsoft Foundry (the model)

### Portal route (easiest)
1. Open https://ai.azure.com (Microsoft Foundry) and sign in. Create a **project** (it creates the Azure resource for you).
   Use resource group `rg-momentum` and your chosen region.
2. In the project: **Models + endpoints** (or **Model catalog**) → pick a small, cheap **chat model** that your region offers
   (a "mini" GPT model is plenty: the stories are two sentences) → **Deploy**.
   - Give the deployment a simple name, for example `narrator`. **This name, not the model name, goes in `.env`.**
   - Deployment type "Global Standard" or "Standard"; a small capacity (for example 10K tokens per minute) is enough.
3. Open the deployment page and copy: **Endpoint** (looks like `https://<resource>.openai.azure.com/`) and **Key**.

### CLI route
```bash
# 1. the resource (the --custom-domain makes the endpoint https://<name>.openai.azure.com/)
az cognitiveservices account create --name momentum-openai-<unique> --resource-group rg-momentum \
  --location swedencentral --kind OpenAI --sku S0 --custom-domain momentum-openai-<unique>

# 2. see which models you can deploy in this region, then pick a small chat model
az cognitiveservices account list-models --name momentum-openai-<unique> --resource-group rg-momentum \
  --query "[?kind=='OpenAI'].{model:name, version:version}" -o table

# 3. deploy it (replace MODEL and VERSION with a row from the table above)
az cognitiveservices account deployment create --name momentum-openai-<unique> --resource-group rg-momentum \
  --deployment-name narrator --model-name MODEL --model-version VERSION --model-format OpenAI \
  --sku-name GlobalStandard --sku-capacity 10

# 4. the values for .env
az cognitiveservices account show --name momentum-openai-<unique> --resource-group rg-momentum --query properties.endpoint -o tsv
az cognitiveservices account keys list --name momentum-openai-<unique> --resource-group rg-momentum --query key1 -o tsv
```

### Connect the project
Copy `.env.example` to `.env` (never commit `.env`; it is git-ignored) and fill in:

```
AZURE_OPENAI_ENDPOINT=https://momentum-openai-<unique>.openai.azure.com/
AZURE_OPENAI_API_KEY=<key1>
AZURE_OPENAI_DEPLOYMENT=narrator
AZURE_OPENAI_API_VERSION=2024-10-21
```

### Test it
```bash
python src/narrator.py --demo
```
The first line must say `Writer: Azure OpenAI`, and sentences must start with `[llm]`. If they start with `[template]`,
read the note in brackets on that line: it names the error. Then:

```bash
python src/run_live.py --simulate --seed 7 --speed 60 --mode analyst
```
and in the dashboard (`streamlit run src/app.py`) open "Why it matters" on a card: the line "Agents:" should show
`Director ✓ → Analyst ✓ → Writer ✓ → Verifier ✓`. **Do this test and write the date in `docs/PITCH.md`**: it turns
"tested with fakes" into "tested for real".

Cost: a match makes about 15 to 25 cards, two short model calls each, so a few cents per match with a mini model.

## 2. Azure Event Hubs (the cloud queue)

```bash
az eventhubs namespace create --name momentum-ns-<unique> --resource-group rg-momentum --location swedencentral --sku Basic
az eventhubs eventhub create --name match-events --namespace-name momentum-ns-<unique> --resource-group rg-momentum --partition-count 2
az eventhubs namespace authorization-rule keys list --namespace-name momentum-ns-<unique> --resource-group rg-momentum \
  --name RootManageSharedAccessKey --query primaryConnectionString -o tsv
```
Add to `.env`:
```
EVENTHUB_CONNECTION_STRING=<the connection string from the last command>
EVENTHUB_NAME=match-events
```
Test with two terminals (the `Basic` tier has only the `$Default` consumer group, which the code uses):
```bash
# terminal 1: start the reader first (it waits for events)
python src/run_live.py --source eventhubs --match 1 --speed 0
# terminal 2: publish match 1 at 30x speed
python src/streamer.py --data data --match 1 --speed 30 --publish eventhubs
```
In the dashboard, the source "Azure Event Hubs" appears once both settings exist.

Notes: the reader starts from the beginning of the stream, and Basic keeps messages for 1 day, so old runs of the same match id
can be read again; use a different `--match` number for a fresh run. Cost: Basic is a few dollars a month; delete when done.

## 3. Azure Container Apps (a public URL for the dashboard)

The repo has a `Dockerfile`. `az containerapp up --source .` builds it in Azure, so you do **not** need Docker locally.

```bash
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights

# from the repo folder
az containerapp up --name momentum-engine --resource-group rg-momentum --location swedencentral \
  --source . --ingress external --target-port 8501

# keep the key out of the image: store it as a secret, then point the settings at it
az containerapp secret set --name momentum-engine --resource-group rg-momentum --secrets openai-key=<key1> eventhub-conn="<connection string>"
az containerapp update --name momentum-engine --resource-group rg-momentum \
  --min-replicas 1 --max-replicas 1 \
  --set-env-vars AZURE_OPENAI_ENDPOINT=<endpoint> AZURE_OPENAI_DEPLOYMENT=narrator AZURE_OPENAI_API_VERSION=2024-10-21 \
                 AZURE_OPENAI_API_KEY=secretref:openai-key EVENTHUB_CONNECTION_STRING=secretref:eventhub-conn EVENTHUB_NAME=match-events
```
- **Keep exactly one replica.** A running match lives in the memory of one process; two replicas would split viewers.
- The command prints the app URL (`https://momentum-engine.<random>.<region>.azurecontainerapps.io`). Open it, press Start.
- To update after code changes, run the `az containerapp up ... --source .` command again.
- Streamlit uses websockets; Container Apps supports them, so no extra setting is needed.

## 4. What to tell the judges (fill in `docs/PITCH.md`)

| If you did | Say |
| --- | --- |
| Step 1 | "The Analyst and Writer agents run on Azure OpenAI in Microsoft Foundry (deployment `narrator`)." |
| Step 2 | "Events travel through Azure Event Hubs, one partition per match, so order is kept." |
| Step 3 | "The dashboard runs on Azure Container Apps: <URL>." |
| Not done | Do not claim it. Say "designed for" instead. |

Microsoft Foundry also offers tracing and evaluation for agents; if you have time, a screenshot of a Foundry trace for one
story is a strong demo asset. Our own agent trace (shown in analyst mode) is the same idea inside the app.

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| `[template]` with note `LLM error: AuthenticationError` | Wrong key, or key from another resource. Copy `key1` again. |
| `NotFoundError` or "DeploymentNotFound" | `AZURE_OPENAI_DEPLOYMENT` must be the **deployment name** (`narrator`), not the model name. |
| `RateLimitError` (429) | Quota too small. Raise the capacity in the deployment, or wait a minute. The circuit breaker will already be using templates. |
| Endpoint rejected | Use the endpoint exactly as shown, with `https://` and the trailing `/`. Some Foundry projects show a `cognitiveservices.azure.com` endpoint: it also works. |
| Model not available in the region | Run the `list-models` command; create the resource in a region that has a chat model. |
| `az containerapp up` fails on a provider | Run the two `az provider register` commands, wait two minutes, retry. |
| Dashboard on the URL shows nothing after Start | Check there is one replica and look at logs: `az containerapp logs show --name momentum-engine --resource-group rg-momentum --follow` |
| Event Hubs reader waits forever | The publisher must use the same `--match` number and the same hub; start the reader before the publisher. |

## Clean up (to stop charges)

```bash
az group delete --name rg-momentum --yes --no-wait
```
Do this after the hackathon results, **not before**: judges may open the app URL.

## Security notes

Keys live in `.env` (git-ignored) or in Container Apps secrets, never in code. For production, prefer Microsoft Entra
ID (managed identity) over keys; the code uses keys today because it is simpler for a hackathon. Rotate any key that was
ever pasted into a chat, a screenshot or a commit.
