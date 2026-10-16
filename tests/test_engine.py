"""Tests for the live engine and the streamer. Run:  python -m pytest tests -q

They simulate one short match in memory, so no data/ folder is needed.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from generate_matches import simulate_match  # noqa: E402
from metrics import Engine  # noqa: E402
from streamer import stream_events  # noqa: E402

REQUIRED = {"match_id", "elapsed_minute", "timestamp_s", "type", "team", "team_name", "momentum", "state",
            "chaos_diff", "drivers", "score"}


@pytest.fixture(scope="module")
def events():
    ev, *_ = simulate_match(1, np.random.default_rng(7))
    return ev


def run(events):
    eng = Engine()
    moments = []
    for e in events:
        moments += eng.update(e)
    return eng, moments


def test_engine_and_streamer_never_read_truth():
    for name in ("metrics.py", "streamer.py"):
        text = (SRC / name).read_text(encoding="utf-8")
        assert "truth_" not in text.replace("truth_*.csv", ""), f"{name} mentions a truth file"


def test_same_events_give_same_moments(events):
    assert run(events)[1] == run(events)[1]


def test_no_lookahead(events):
    """The state after N events must not depend on the events that come after them."""
    cut = len(events) // 2
    full, part = Engine(), Engine()
    for i, e in enumerate(events):
        full.update(e)
        if i < cut:
            part.update(e)
        if i == cut - 1:
            snap_full_then = full.snapshot()
    assert part.snapshot() == snap_full_then


def test_moments_have_the_contract_fields(events):
    _, moments = run(events)
    assert moments, "a full match should produce at least one key moment"
    for m in moments:
        assert REQUIRED <= set(m)
        assert m["type"] in {"goal", "momentum_shift", "style_change", "big_chance"}
        assert m["team"] in {"home", "away"}
        assert isinstance(m["drivers"], list) and m["drivers"]


def test_goals_match_the_final_score(events):
    eng, moments = run(events)
    goals = [m for m in moments if m["type"] == "goal"]
    assert len(goals) == eng.score["home"] + eng.score["away"]


def test_no_shift_moments_in_the_first_three_minutes(events):
    _, moments = run(events)
    assert all(m["timestamp_s"] >= 180 for m in moments if m["type"] in ("momentum_shift", "style_change"))


def test_cooldown_between_shift_and_style_moments(events):
    _, moments = run(events)
    t = [m["timestamp_s"] for m in moments if m["type"] in ("momentum_shift", "style_change")]
    assert all(b - a >= 120 for a, b in zip(t, t[1:]))


def test_state_is_balanced_before_warmup(events):
    eng = Engine()
    for e in events[:30]:
        eng.update(e)
    s = eng.snapshot()
    assert s["home"]["chaos"] == 0 and s["away"]["chaos"] == 0


def test_streamer_keeps_order_and_pace(events):
    now = [0.0]  # a fake clock that only moves when the streamer sleeps

    def sleep(s):
        now[0] += s

    out = list(stream_events(events[:200], speed=60, sleep=sleep, clock=lambda: now[0]))
    assert [e["event_id"] for e in out] == [e["event_id"] for e in events[:200]]
    expected = (events[199]["timestamp_s"] - events[0]["timestamp_s"]) / 60
    assert now[0] == pytest.approx(expected, abs=1e-6)  # the replay took exactly stream time / speed


def test_streamer_speed_zero_does_not_wait(events):
    sleeps = []
    list(stream_events(events[:100], speed=0, sleep=sleeps.append))
    assert sleeps == []
