"""Tests for the Event Hubs transport with fake producer and consumer objects (no Azure account needed).

Run:  python -m pytest tests -q
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("azure.eventhub")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eventhub import EventHubFeed, EventHubPublisher, to_json  # noqa: E402
from generate_matches import simulate_match  # noqa: E402
from live import LiveSession  # noqa: E402
from metrics import Engine  # noqa: E402


class FakeBatch:
    def __init__(self, key):
        self.key, self.items = key, []

    def add(self, event_data):
        self.items.append(event_data.body_as_str())


class FakeProducer:
    def __init__(self):
        self.batches, self.closed = [], False

    def create_batch(self, partition_key=None):
        return FakeBatch(partition_key)

    def send_batch(self, batch):
        self.batches.append(batch)

    def close(self):
        self.closed = True


class FakeConsumer:
    """receive() hands over prepared messages, like the SDK's callback loop."""

    def __init__(self, messages):
        self.messages, self.closed = messages, False

    def receive(self, on_event, starting_position=None):
        for m in self.messages:
            on_event(None, SimpleNamespace(body_as_str=lambda m=m: m))

    def close(self):
        self.closed = True


@pytest.fixture(scope="module")
def events():
    return simulate_match(1, np.random.default_rng(5))[0][:600]


def test_to_json_makes_nan_and_numpy_plain():
    s = to_json({"a": float("nan"), "b": np.float64(1.5), "c": np.int64(3), "d": "x"})
    assert json.loads(s) == {"a": None, "b": 1.5, "c": 3, "d": "x"}


def test_publisher_keeps_a_match_on_one_partition_and_ends_it(events):
    prod = FakeProducer()
    pub = EventHubPublisher(prod)
    for e in events[:10]:
        pub.publish(e)
    pub.close()
    assert len(prod.batches) == 11  # ten events and the end-of-match message
    assert {b.key for b in prod.batches} == {"1"}
    assert json.loads(prod.batches[-1].items[0])["event_type"] == "end_of_match"
    assert prod.closed


def test_feed_returns_the_events_in_order_and_stops_at_the_end(events):
    msgs = [to_json(e) for e in events[:50]] + [json.dumps({"event_type": "end_of_match", "match_id": 1})]
    out = list(EventHubFeed(1, consumer=FakeConsumer(msgs)))
    assert [e["event_id"] for e in out] == [e["event_id"] for e in events[:50]]


def test_feed_ignores_other_matches(events):
    other = dict(events[0], match_id=2)
    msgs = [to_json(other), to_json(events[0]), json.dumps({"event_type": "end_of_match", "match_id": 1})]
    assert len(list(EventHubFeed(1, consumer=FakeConsumer(msgs)))) == 1


def test_engine_gives_the_same_moments_through_the_hub(events):
    """The transport must not change what the engine sees: JSON round trip in, same key moments out."""
    direct = Engine()
    via = Engine()
    m1, m2 = [], []
    for e in events:
        m1 += direct.update(e)
    msgs = [to_json(e) for e in events] + [json.dumps({"event_type": "end_of_match", "match_id": 1})]
    for e in EventHubFeed(1, consumer=FakeConsumer(msgs)):
        m2 += via.update(e)
    assert m1 == m2


def test_live_session_accepts_a_feed(events):
    msgs = [to_json(e) for e in events] + [json.dumps({"event_type": "end_of_match", "match_id": 1})]
    s = LiveSession(EventHubFeed(1, consumer=FakeConsumer(msgs)), speed=0).start()
    s.thread.join(timeout=30)
    assert s.view()["error"] is None and s.view()["finished"]
