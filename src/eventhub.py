"""Azure Event Hubs transport: publish match events to a hub, and read them back as a live feed.

This replaces the local queue when you want the production path: streamer -> Event Hubs -> engine.
Nothing else changes: the engine and LiveSession only see an iterator of event dicts.

Setup: pip install azure-eventhub, then set in .env
  EVENTHUB_CONNECTION_STRING   (a namespace or hub connection string with Send and Listen rights)
  EVENTHUB_NAME                (the event hub, e.g. "match-events")

Order: every event of a match uses the match id as partition key, so one match stays in order inside one partition.
The publisher ends a match with a control message {"event_type": "end_of_match"}.

Usage:
  python src/streamer.py --data data --match 1 --speed 30 --publish eventhubs      # producer side
  python src/run_live.py --source eventhubs --match 1 --speed 0                    # consumer side (events are already paced)

Not yet tested against a real Event Hubs namespace: the tests use fake producer and event objects.
"""

import json  # events travel as JSON
import math  # NaN check
import os  # settings
import queue  # hand events from the receiver thread to the reader
import threading  # receiver thread

END = {"event_type": "end_of_match"}  # control message that closes a match


def _env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    return os.getenv("EVENTHUB_CONNECTION_STRING"), os.getenv("EVENTHUB_NAME")


def configured():  # True if both settings exist
    conn, hub = _env()
    return bool(conn and hub)


def to_json(ev):  # one event as JSON text, with NaN and numpy numbers made plain
    clean = {}
    for k, v in ev.items():
        if hasattr(v, "item"):  # numpy scalar
            v = v.item()
        clean[k] = None if isinstance(v, float) and math.isnan(v) else v
    return json.dumps(clean)


class EventHubPublisher:
    """publish(event) sends one event; close() sends the end-of-match message and closes the connection."""

    def __init__(self, producer=None):
        if producer is None:
            from azure.eventhub import EventHubProducerClient  # imported here so the package is optional
            conn, hub = _env()
            if not (conn and hub):
                raise RuntimeError("Set EVENTHUB_CONNECTION_STRING and EVENTHUB_NAME (see .env.example)")
            producer = EventHubProducerClient.from_connection_string(conn, eventhub_name=hub)
        self.producer = producer
        self.match_id = None

    def _send(self, payload, key):
        from azure.eventhub import EventData  # imported here so tests can run without the package
        batch = self.producer.create_batch(partition_key=str(key))
        batch.add(EventData(payload))
        self.producer.send_batch(batch)

    def publish(self, ev):
        self.match_id = ev["match_id"]
        self._send(to_json(ev), ev["match_id"])

    def close(self):
        if self.match_id is not None:
            self._send(json.dumps({**END, "match_id": self.match_id}), self.match_id)
        self.producer.close()


class EventHubFeed:
    """Reads one match from the hub. Iterate over it to get event dicts, in order, until the end-of-match message."""

    def __init__(self, match_id, consumer=None, consumer_group="$Default", idle_timeout_s=120.0):
        if consumer is None:
            from azure.eventhub import EventHubConsumerClient
            conn, hub = _env()
            if not (conn and hub):
                raise RuntimeError("Set EVENTHUB_CONNECTION_STRING and EVENTHUB_NAME (see .env.example)")
            consumer = EventHubConsumerClient.from_connection_string(conn, consumer_group=consumer_group, eventhub_name=hub)
        self.match_id, self.consumer, self.idle_timeout_s = match_id, consumer, idle_timeout_s
        self.q = queue.Queue()
        self.thread = threading.Thread(target=self._receive, daemon=True)
        self.thread.start()

    def _on_event(self, partition_context, event):  # called by the SDK for each message
        if event is None:
            return
        ev = json.loads(event.body_as_str())
        if ev.get("match_id") == self.match_id:  # the hub may carry other matches
            self.q.put(ev)

    def _receive(self):
        try:
            self.consumer.receive(on_event=self._on_event, starting_position="-1")  # from the start of the stream
        except Exception as e:  # tell the reader instead of failing silently
            self.q.put({"event_type": "error", "message": f"{type(e).__name__}: {e}"})

    def __iter__(self):
        while True:
            try:
                ev = self.q.get(timeout=self.idle_timeout_s)
            except queue.Empty:
                break  # nothing arrived for a long time: stop
            if ev["event_type"] == "end_of_match":
                break
            if ev["event_type"] == "error":
                raise RuntimeError(ev["message"])
            yield ev
        try:
            self.consumer.close()
        except Exception:
            pass
