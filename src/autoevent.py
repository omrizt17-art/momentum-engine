"""Auto-eventing prototype: turn a ball-tracking feed into pass events and speed metrics, instead of tagging them by hand.

The brief mentions "running a model over an incoming video feed to generate the pass and speed metrics automatically".
We have no video. This module shows the stage and measures it, on a SYNTHETIC tracking feed:

  make_tracking(events)   builds a noisy ball track (10 frames a second) from a synthetic match: ball position and who
                          holds it. This stands in for the output of a video or tracking model.
  detect_passes(frames)   the detector: finds every spell where the ball is flying, takes the holder before and after,
                          and computes pass distance, ball speed and outcome (same team = complete) from the track alone.
  evaluate(...)           compares what the detector found with the real synthetic events.

Honest limits: the track is built from the events, so the test is circular for detection; what it does show is how well
the speed and distance can be recovered through noise and the 10 Hz sampling. Shots are not detected. To use real
video, replace make_tracking by a model that outputs the same frame dicts: {t, x, y, holder} (x, y in metres).

Usage:  python src/autoevent.py --seed 7
"""

import argparse
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

HZ = 10  # frames per second
MIN_FLIGHT_FRAMES = 2  # shorter bursts are noise


def _abs_m(team, x, y):
    """Pitch metres in a fixed frame (home attacks towards x = 105 m). Events are logged in the acting team's own direction."""
    if team != "home":
        x, y = 100 - x, 100 - y
    return x * 1.05, y * 0.68


def make_tracking(events, noise_m=0.35, hz=HZ, seed=0):
    """A synthetic ball track from events: list of {t, x, y, holder}. holder is None while the ball is in the air."""
    rng = np.random.default_rng(seed)
    frames = []
    times = [float(e["timestamp_s"]) for e in events]
    pass_times = {t for t, e in zip(times, events) if e["event_type"] == "pass"}
    for i, e in enumerate(events):
        t0 = times[i]
        if e["event_type"] != "pass" and t0 in pass_times:
            continue  # a press or interception logged at the same instant as a pass: the pass frames already cover it
        t1 = next((t for t in times[i + 1:] if t > t0), t0 + 3.0)  # the next instant something else happens
        nxt = None  # who holds the ball once a pass has landed: the receiver, or the opponent who wins it
        if e["event_type"] == "pass":
            if e["outcome"] == "complete":
                nxt = e["receiver_id"]
            else:
                nxt = next((g["player_id"] for g in events[i + 1:i + 4] if g["event_type"] == "interception"), None)
        sx, sy = _abs_m(e["team"], e["x"], e["y"])
        flight, ex, ey = 0.0, sx, sy
        if e["event_type"] == "pass" and e.get("ball_speed_kmh"):
            ex, ey = _abs_m(e["team"], e["end_x"], e["end_y"])
            flight = math.hypot(ex - sx, ey - sy) / (float(e["ball_speed_kmh"]) / 3.6)
            flight = min(flight, max(t1 - t0 - 0.1, 0.1))
        tau = t0
        while tau < t1 - 1e-9:
            if flight and tau - t0 < flight:  # in the air
                f = (tau - t0) / flight
                x, y, holder = sx + f * (ex - sx), sy + f * (ey - sy), None
            elif flight:  # landed: held by whoever acts next (the receiver, or the opponent who wins it)
                x, y, holder = ex, ey, nxt
            else:  # a duel, a shot, a press: the ball stays with the actor
                x, y, holder = sx, sy, e["player_id"]
            frames.append({"t": round(tau, 3), "x": x + rng.normal(0, noise_m), "y": y + rng.normal(0, noise_m), "holder": holder})
            tau += 1.0 / hz
    return frames


def detect_passes(frames, hz=HZ):
    """Find passes in a track. Returns a list of {t, passer_id, receiver_id, outcome, distance_m, ball_speed_kmh}."""
    out, i, n = [], 0, len(frames)
    last_holder, last_holder_i = None, None
    while i < n:
        f = frames[i]
        if f["holder"] is not None:
            last_holder, last_holder_i = f["holder"], i
        if f["holder"] is None and last_holder is not None:  # the ball just left a player: a flight starts
            j = i
            while j < n and frames[j]["holder"] is None:
                j += 1
            if j - i >= MIN_FLIGHT_FRAMES and j < n and last_holder_i is not None:
                a, b = frames[last_holder_i], frames[j]  # last frame held, first frame held again
                dist = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
                secs = (j - i - 0.5) / hz  # frames in the air, minus half a frame for the sampling
                receiver = frames[j]["holder"]
                out.append({"t": frames[i]["t"], "passer_id": last_holder, "receiver_id": receiver,
                            "outcome": "complete" if receiver[0] == last_holder[0] else "incomplete",
                            "distance_m": round(dist, 1), "ball_speed_kmh": round(dist / secs * 3.6, 1)})
            i = j
            continue
        i += 1
    return out


def evaluate(events, detected, tol_s=1.0):
    """Compare detections with the real passes: recall, precision, outcome and receiver accuracy, distance and speed error."""
    truth = [e for e in events if e["event_type"] == "pass" and e.get("ball_speed_kmh")]
    used, matched = set(), []
    for d in detected:
        for k, e in enumerate(truth):
            if k not in used and e["player_id"] == d["passer_id"] and abs(float(e["timestamp_s"]) - d["t"]) <= tol_s:
                used.add(k)
                matched.append((e, d))
                break
    from players import pass_distance  # the same metre conversion as the player layer
    dist_err = [abs(pass_distance(e) - d["distance_m"]) for e, d in matched]
    speed_err = [abs(float(e["ball_speed_kmh"]) - d["ball_speed_kmh"]) for e, d in matched]
    return {"passes_in_match": len(truth), "detected": len(detected), "matched": len(matched),
            "recall": len(matched) / max(len(truth), 1), "precision": len(matched) / max(len(detected), 1),
            "outcome_accuracy": float(np.mean([e["outcome"] == d["outcome"] for e, d in matched])) if matched else 0.0,
            "receiver_accuracy": float(np.mean([e["receiver_id"] == d["receiver_id"] or e["outcome"] == "incomplete" for e, d in matched])) if matched else 0.0,
            "distance_mae_m": float(np.mean(dist_err)) if dist_err else float("nan"),
            "speed_mae_kmh": float(np.mean(speed_err)) if speed_err else float("nan")}


def main():
    ap = argparse.ArgumentParser(description="Auto-eventing prototype on a synthetic tracking feed")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--noise", type=float, default=0.35, help="tracking noise in metres")
    args = ap.parse_args()
    from generate_matches import simulate_match
    events = simulate_match(1, np.random.default_rng(args.seed))[0]
    frames = make_tracking(events, noise_m=args.noise)
    res = evaluate(events, detect_passes(frames))
    print(f"{len(events)} events -> {len(frames)} tracking frames ({HZ} Hz, noise {args.noise} m)")
    for k, v in res.items():
        print(f"  {k:<18} {v:.3f}" if isinstance(v, float) else f"  {k:<18} {v}")


if __name__ == "__main__":
    main()
