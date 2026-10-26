"""Live event streamer: replays events.csv one event at a time, paced like a real match, and runs the engine.

It reads events.csv only. It never opens a truth_*.csv file, so the engine sees exactly what a live feed would show.

Usage:
  python src/streamer.py --data data --match 1 --speed 30            # watch match 1 at 30x speed
  python src/streamer.py --data data --match 1 --speed 0 --jsonl moments.jsonl   # as fast as possible, save key moments
  python src/streamer.py --data data --match 1 --speed 30 --publish eventhubs    # publish the paced feed to Azure Event Hubs (see eventhub.py)

From other code (e.g. the dashboard) use the local queue, which is the fallback for Azure Event Hubs:
  q = start_queue_feed(events, speed=30)   # a thread fills a queue.Queue with events, then a final None
  while (event := q.get()) is not None: ...
"""

import argparse  # read command-line options
import json  # write key moments as JSON lines
import queue  # local queue: the fallback for Azure Event Hubs
import sys  # to find sibling modules
import threading  # background feeder for the queue
import time  # pacing
from pathlib import Path  # file paths

import pandas as pd  # read the CSV

sys.path.insert(0, str(Path(__file__).parent))  # let us import files from src/
from metrics import Engine  # the live engine


def load_events(data_dir, match_id):  # one match's events as plain dicts, in event order
    df = pd.read_csv(Path(data_dir) / "events.csv")  # the live feed file (truth files are never opened here)
    df = df[df.match_id == match_id].sort_values("event_id")  # one match, in the order events happened
    return df.to_dict("records")  # list of dicts


def stream_events(events, speed=1.0, sleep=time.sleep, clock=time.monotonic):  # yield events, waiting so that stream time passes `speed` times faster than real time
    start = None  # stream time of the first event (events can be a list or a live iterator)
    t0 = clock()  # wall-clock reference
    for ev in events:  # each event
        if start is None:
            start = ev["timestamp_s"]
        if speed > 0:  # speed 0 = no waiting
            due = (ev["timestamp_s"] - start) / speed  # when this event is due, in wall-clock seconds
            wait = due - (clock() - t0)  # how long until then
            if wait > 0:  # not yet
                sleep(wait)  # wait
        yield ev  # hand the event over


def start_queue_feed(events, speed=1.0):  # local fallback for Event Hubs: a thread publishes events into a queue
    q = queue.Queue()  # the queue

    def feed():  # runs in the background thread
        for ev in stream_events(events, speed):  # paced events
            q.put(ev)  # publish
        q.put(None)  # end-of-match marker

    threading.Thread(target=feed, daemon=True).start()  # start publishing
    return q  # consumers read from here


def describe(m):  # one readable line for a key moment
    who = m["team_name"]  # team in the moment
    if m["type"] == "goal":  # goals
        return f"[{m['elapsed_minute']:>2}'] GOAL  {who}  {m['score']['home']}-{m['score']['away']}  {m['drivers'][0]}"
    return f"[{m['elapsed_minute']:>2}'] {m['type'].upper():<15} {who}  mom {m['momentum']:+.1f}  {m['state']}  | " + "; ".join(m["drivers"])


def main():  # command-line entry point
    ap = argparse.ArgumentParser(description="Replay a match as a live stream and run the engine")  # parser
    ap.add_argument("--data", default="data", help="folder made by generate_matches.py")  # input folder
    ap.add_argument("--match", type=int, default=1, help="match to replay")  # which match
    ap.add_argument("--speed", type=float, default=30.0, help="replay speed (30 = 30x real time, 0 = no waiting)")  # pacing
    ap.add_argument("--jsonl", default=None, help="also write key moments to this JSON-lines file")  # optional output
    ap.add_argument("--publish", choices=["eventhubs"], default=None, help="publish the paced events to Azure Event Hubs instead of running the engine here")  # transport
    args = ap.parse_args()  # read options

    events = load_events(args.data, args.match)  # the feed
    if args.publish == "eventhubs":  # producer side of the production path
        from eventhub import EventHubPublisher  # optional dependency, imported only here
        pub = EventHubPublisher()
        for ev in stream_events(events, args.speed):  # paced events
            pub.publish(ev)
        pub.close()
        print(f"Published {len(events)} events of match {args.match} to Event Hubs.")
        return
    engine = Engine()  # fresh engine
    out = open(args.jsonl, "w", encoding="utf-8") if args.jsonl else None  # optional file
    last_minute = -1  # for the once-a-minute status line
    for ev in stream_events(events, args.speed):  # live loop
        for m in engine.update(ev):  # feed the engine
            print(describe(m))  # show the key moment
            if out:  # save it
                out.write(json.dumps(m) + "\n")  # one JSON object per line
        minute = int(ev["timestamp_s"] // 60)  # elapsed minute
        if minute != last_minute and minute % 5 == 0:  # status every 5 minutes
            s = engine.snapshot()  # current state
            print(f"  {minute:>2}'  momentum {s['momentum']:+5.1f} ({s['regime_name']})  chaos home {s['home']['chaos']:+.2f} away {s['away']['chaos']:+.2f}  score {s['score']['home']}-{s['score']['away']}")
        last_minute = minute  # remember
    if out:  # close the file
        out.close()
    print(f"\nDone: {len(events)} events, {len(engine.moments)} key moments.")  # summary


if __name__ == "__main__":  # run only when executed directly
    main()  # start
