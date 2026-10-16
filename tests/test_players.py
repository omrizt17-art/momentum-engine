"""Tests for the player layer: name tags, pass quality, ball speed, badges and player focus. Run:  python -m pytest tests -q"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from generate_matches import simulate_match  # noqa: E402
from players import FAST_BALL_KMH, HARD_PASS, LONG_PASS_M, PlayerTracker, difficulty_label, pass_difficulty, pass_distance  # noqa: E402


def pass_event(**kw):
    ev = {"match_id": 1, "event_id": 1, "timestamp_s": 100.0, "team": "home", "team_name": "A", "player_id": "H05", "player_name": "Kai Venn", "position": "MID",
          "event_type": "pass", "outcome": "complete", "x": 40.0, "y": 50.0, "end_x": 55.0, "end_y": 50.0, "receiver_id": "H08", "receiver_name": "Oscar Hale",
          "xg": None, "under_pressure": False, "ball_speed_kmh": 50.0, "home_score": 0, "away_score": 0}
    ev.update(kw)
    return ev


@pytest.fixture(scope="module")
def events():
    return simulate_match(1, np.random.default_rng(7))[0]


def test_generated_events_carry_plausible_ball_speed(events):
    passes = [e["ball_speed_kmh"] for e in events if e["event_type"] == "pass"]
    shots = [e["ball_speed_kmh"] for e in events if e["event_type"] == "shot"]
    assert all(20 <= v <= 115 for v in passes) and all(55 <= v <= 130 for v in shots)
    assert np.mean(shots) > np.mean(passes)  # shots are hit harder than passes
    assert all(e["ball_speed_kmh"] is None for e in events if e["event_type"] in ("tackle", "pressure", "interception"))


def test_distance_is_in_metres():
    assert pass_distance(pass_event(x=0, y=50, end_x=100, end_y=50)) == pytest.approx(105.0, abs=0.1)  # the length of the pitch
    assert pass_distance(pass_event(x=50, y=0, end_x=50, end_y=100)) == pytest.approx(68.0, abs=0.1)  # the width


def test_difficulty_grows_with_length_pressure_and_final_third():
    short = pass_difficulty(pass_event(x=40, end_x=45))
    long = pass_difficulty(pass_event(x=40, end_x=80))
    pressed = pass_difficulty(pass_event(x=40, end_x=45, under_pressure=True))
    into_box = pass_difficulty(pass_event(x=50, end_x=70))
    assert long > short and pressed > short and into_box > short
    assert 0 <= short <= 100 and 0 <= pass_difficulty(pass_event(x=0, end_x=100, under_pressure=True)) <= 100
    assert difficulty_label(HARD_PASS) == "hard" and difficulty_label(10) == "easy"


def test_badges_fire_at_their_thresholds():
    t = PlayerTracker()
    o, _ = t.update(pass_event(x=10, end_x=10 + (LONG_PASS_M / 1.05) + 2))  # a bit over 35 m
    assert "long_pass" in o["badges"]
    o, _ = t.update(pass_event(x=10, end_x=10 + (LONG_PASS_M / 1.05) - 2))  # a bit under
    assert "long_pass" not in o["badges"]
    o, _ = t.update(pass_event(ball_speed_kmh=FAST_BALL_KMH))
    assert "fast_ball" in o["badges"]
    o, _ = t.update(pass_event(ball_speed_kmh=FAST_BALL_KMH - 1))
    assert "fast_ball" not in o["badges"]


def test_power_shot_goal_and_big_chance_badges():
    t = PlayerTracker()
    shot = pass_event(event_type="shot", outcome="goal", x=85, y=50, end_x=None, end_y=None, xg=0.4, ball_speed_kmh=108.0)
    o, _ = t.update(shot)
    assert set(o["badges"]) == {"goal", "power_shot"}
    o, _ = t.update(dict(shot, outcome="saved"))
    assert "big_chance" in o["badges"] and "goal" not in o["badges"]


def test_milestone_at_the_25th_completed_pass():
    t = PlayerTracker()
    for i in range(25):
        o, _ = t.update(pass_event(event_id=i))
    assert "milestone" in o["badges"]


def test_overlay_is_a_timed_machine_readable_name_tag():
    o, _ = PlayerTracker().update(pass_event())
    assert {"t", "minute", "player_id", "name", "position", "team", "team_name", "event", "metrics", "badges", "text"} <= set(o)
    assert o["name"] == "Kai Venn" and o["minute"] == 1 and "Kai Venn (MID)" in o["text"]


def test_stats_accuracy_and_find():
    t = PlayerTracker()
    t.update(pass_event(event_id=1))
    t.update(pass_event(event_id=2, outcome="incomplete"))
    s = t.stats("H05")
    assert s["passes"] == 2 and s["accuracy_pct"] == 50
    assert t.find("venn") == "H05" and t.find("nobody") is None
    assert t.stats("H08")["received"] == 1  # the receiver of the completed pass


def test_focus_moments_only_for_the_followed_player_with_cooldown():
    t = PlayerTracker(focus="venn")
    long_pass = dict(x=10, end_x=60)
    _, m = t.update(pass_event(event_id=1, timestamp_s=100.0, **long_pass))
    assert len(m) == 1 and m[0]["type"] == "player_focus" and m[0]["facts"]["player"] == "Kai Venn"
    _, m = t.update(pass_event(event_id=2, timestamp_s=130.0, **long_pass))
    assert m == []  # inside the 60 s cooldown
    _, m = t.update(pass_event(event_id=3, timestamp_s=170.0, **long_pass))
    assert len(m) == 1
    _, m = t.update(pass_event(event_id=4, timestamp_s=400.0, player_id="H02", player_name="Someone Else", **long_pass))
    assert m == []  # not the followed player


def test_goals_are_not_repeated_as_focus_moments():
    t = PlayerTracker(focus="venn")
    _, m = t.update(pass_event(event_type="shot", outcome="goal", x=85, y=50, end_x=None, end_y=None, xg=0.2, ball_speed_kmh=90.0))
    assert m == []  # the goal already has its own key moment


def test_a_full_match_gives_every_event_a_tag(events):
    t = PlayerTracker()
    tags = [t.update(e)[0] for e in events]
    assert len(tags) == len(events) and any(tg["badges"] for tg in tags)
    assert len(t.players) >= 20  # both squads appear
    top = t.top(3)
    assert top[0]["involvement"] >= top[2]["involvement"]
