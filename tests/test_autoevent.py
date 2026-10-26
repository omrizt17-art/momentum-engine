"""Tests for the auto-eventing prototype (synthetic tracking in, pass events and speed metrics out). Run:  python -m pytest tests -q"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from autoevent import detect_passes, evaluate, make_tracking  # noqa: E402
from generate_matches import simulate_match  # noqa: E402


@pytest.fixture(scope="module")
def events():
    return simulate_match(1, np.random.default_rng(7))[0]


def test_detector_recovers_most_passes_with_good_metrics(events):
    r = evaluate(events, detect_passes(make_tracking(events)))
    assert r["recall"] >= 0.8 and r["precision"] >= 0.85
    assert r["outcome_accuracy"] >= 0.99 and r["receiver_accuracy"] >= 0.99
    assert r["distance_mae_m"] < 1.0 and r["speed_mae_kmh"] < 4.0


def test_more_tracking_noise_means_larger_errors(events):
    low = evaluate(events, detect_passes(make_tracking(events, noise_m=0.2)))
    high = evaluate(events, detect_passes(make_tracking(events, noise_m=1.5)))
    assert high["distance_mae_m"] > low["distance_mae_m"] and high["speed_mae_kmh"] > low["speed_mae_kmh"]


def test_tracking_is_deterministic_for_a_seed(events):
    a, b = make_tracking(events[:200], seed=1), make_tracking(events[:200], seed=1)
    assert a == b and a != make_tracking(events[:200], seed=2)
