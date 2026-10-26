"""Terminal demo: a match plays live, with key moments told by the narrator. Also the backup demo if the dashboard fails.

Usage:
  python src/run_live.py --data data --match 1 --speed 60 --mode analyst --lang he
  python src/run_live.py --simulate --seed 7 --speed 0          # no data/ folder needed: simulate a match in memory
  python src/run_live.py --source eventhubs --match 1 --speed 0 # read a match that streamer.py --publish eventhubs is sending
"""

import argparse  # read command-line options
import sys  # to find sibling modules
import time  # polling
from pathlib import Path  # file paths

sys.path.insert(0, str(Path(__file__).parent))  # let us import files from src/
from live import LiveSession  # the live path
from narrator import Narrator, make_client  # stories
from streamer import load_events  # events from a data folder


def simulated_events(seed, match_id=1):  # one match simulated in memory
    import numpy as np
    from generate_matches import simulate_match
    return simulate_match(match_id, np.random.default_rng(seed))[0]


def main():
    ap = argparse.ArgumentParser(description="Live terminal demo")
    ap.add_argument("--data", default="data", help="folder made by generate_matches.py")
    ap.add_argument("--match", type=int, default=1)
    ap.add_argument("--simulate", action="store_true", help="simulate a match in memory instead of reading --data")
    ap.add_argument("--source", choices=["data", "eventhubs"], default="data", help="where events come from (eventhubs: already paced by the publisher, use --speed 0)")
    ap.add_argument("--seed", type=int, default=7, help="seed for --simulate")
    ap.add_argument("--speed", type=float, default=60.0, help="60 = one minute per second, 0 = no waiting")
    ap.add_argument("--mode", default="casual", choices=["casual", "analyst"])
    ap.add_argument("--lang", default="en")
    ap.add_argument("--club", default=None)
    ap.add_argument("--player", default=None)
    args = ap.parse_args()

    if args.source == "eventhubs":
        from eventhub import EventHubFeed  # optional dependency
        events = EventHubFeed(args.match)  # an iterator of event dicts
    else:
        events = simulated_events(args.seed) if args.simulate else load_events(args.data, args.match)
    client = make_client()
    print("Narrator:", "Azure OpenAI (with template fallback)" if client else "templates only (no Azure OpenAI settings found)")
    s = LiveSession(events, args.speed, Narrator(args.mode, args.lang, args.club, args.player, client=client)).start()

    shown, last_minute = 0, -1
    while True:
        v = s.view()
        if v["error"]:
            print("ERROR:", v["error"])
            break
        snap = v["snapshot"]
        if snap["elapsed_minute"] != last_minute and snap["elapsed_minute"] % 5 == 0:
            last_minute = snap["elapsed_minute"]
            print(f"{last_minute:>3}'  momentum {snap['momentum']:+5.1f}  {snap['regime_name']:<14}  {snap['score']['home']}-{snap['score']['away']}")
        for c in v["cards"][shown:]:  # print each card once, after its model text had a moment to arrive
            if c["pending"] and not v["finished"]:
                break
            m = c["moment"]
            print(f"  >> {m['elapsed_minute']:>2}' [{m['type']}] {c['text']}")
            shown += 1
        if v["finished"] and v["recap"] and shown >= len(v["cards"]):
            print("\nFULL TIME\n" + v["recap"]["text"])
            break
        time.sleep(0.2)


if __name__ == "__main__":
    main()
