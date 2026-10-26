"""Check the Azure settings in .env in one command, and say what to fix.

  python src/check_azure.py                 # Azure OpenAI: settings, one tiny model call, one full story through the agents
  python src/check_azure.py --eventhubs     # also check the Event Hubs connection (no data is sent)

It prints OK or FAIL for each part. A FAIL says the likely cause. Nothing is written anywhere.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OPENAI_VARS = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")
HUB_VARS = ("EVENTHUB_CONNECTION_STRING", "EVENTHUB_NAME")

HINTS = {  # error class name -> what to do
    "AuthenticationError": "The key is wrong, or it belongs to another resource. Copy key1 again from the portal.",
    "PermissionDeniedError": "The key has no access to this resource. Check the resource and the deployment are in the same Azure resource.",
    "NotFoundError": "AZURE_OPENAI_DEPLOYMENT must be the deployment NAME (for example narrator), not the model name.",
    "RateLimitError": "Quota too small or too many calls. Wait a minute or raise the capacity of the deployment.",
    "APIConnectionError": "Cannot reach the endpoint. Check AZURE_OPENAI_ENDPOINT: https://<resource>.openai.azure.com/ with the trailing slash.",
    "APITimeoutError": "The call took too long. Try again; check the region and your network.",
    "BadRequestError": "The request was refused. Check AZURE_OPENAI_API_VERSION (try 2024-10-21) and that the deployment is a chat model.",
}


def missing(names, env=os.environ):
    """The names that are not set (or are empty)."""
    return [n for n in names if not env.get(n)]


def hint_for(exc):
    """A plain-language next step for a model error."""
    return HINTS.get(type(exc).__name__, "Unexpected error: read the message above and check the resource in the Azure portal.")


def check_openai(client, deployment, max_tokens=16):
    """One tiny model call. Returns (ok, message)."""
    try:
        r = client.chat.completions.create(model=deployment, max_tokens=max_tokens, messages=[{"role": "user", "content": "Reply with the single word OK."}])
        text = (r.choices[0].message.content or "").strip()
        return (True, f"model answered: {text!r}") if text else (False, "the model returned an empty answer")
    except Exception as e:  # noqa: BLE001 - we want every kind of failure reported
        return False, f"{type(e).__name__}: {str(e)[:160]}  ->  {hint_for(e)}"


def check_story(client):
    """One real story through the agents. Returns (ok, message, trace)."""
    from agents import Newsroom
    from narrator import DEMO
    r = Newsroom(client).process(DEMO[0], {"mode": "analyst", "language": "en"})
    trace = "  ->  ".join(f"{t['agent']} {t['status']}" for t in r["trace"])
    return (r["source"] == "llm"), r["text"], trace


def check_eventhubs(env=os.environ):
    """Open the Event Hubs connection and read the hub's properties. Sends nothing. Returns (ok, message)."""
    try:
        from azure.eventhub import EventHubProducerClient
        p = EventHubProducerClient.from_connection_string(env["EVENTHUB_CONNECTION_STRING"], eventhub_name=env["EVENTHUB_NAME"])
        with p:
            props = p.get_eventhub_properties()
        return True, f"hub '{props['eventhub_name']}' reachable, {len(props['partition_ids'])} partition(s)"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:160]}  ->  Check the connection string and hub name (EVENTHUB_NAME must match the hub you created)."


def main():
    ap = argparse.ArgumentParser(description="Check the Azure settings")
    ap.add_argument("--eventhubs", action="store_true", help="also check Event Hubs")
    args = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    failed = False
    print("1. Azure OpenAI settings")
    gone = missing(OPENAI_VARS)
    if gone:
        print(f"   FAIL  missing in .env: {', '.join(gone)}  (copy .env.example to .env and fill it in; see docs/AZURE.md)")
        failed = True
    else:
        print("   OK    endpoint, key and deployment name are set")
        from narrator import make_client
        client = make_client()
        print("2. One model call")
        ok, msg = check_openai(client, os.environ["AZURE_OPENAI_DEPLOYMENT"])
        print(f"   {'OK  ' if ok else 'FAIL'}  {msg}")
        failed |= not ok
        if ok:
            print("3. One story through the agents (Director, Analyst, Writer, Verifier)")
            ok, text, trace = check_story(client)
            print(f"   {'OK  ' if ok else 'FAIL'}  {text}")
            print(f"         {trace}")
            if not ok:
                print("         The story fell back to a template: read the trace above to see which agent failed.")
            failed |= not ok
    if args.eventhubs:
        print("4. Event Hubs")
        gone = missing(HUB_VARS)
        if gone:
            print(f"   FAIL  missing in .env: {', '.join(gone)}")
            failed = True
        else:
            ok, msg = check_eventhubs()
            print(f"   {'OK  ' if ok else 'FAIL'}  {msg}")
            failed |= not ok
    print("\nRESULT: " + ("something needs fixing (see FAIL lines)" if failed else "all checks passed. You can now write 'tested with Azure' in the pitch, with today's date."))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
