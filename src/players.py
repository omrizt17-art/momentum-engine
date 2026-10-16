"""Player layer: name tags, pass quality, ball speed and player-focus moments, updated one event at a time.

Covers these items of the brief:
  - Player identification: every event gets a name-tag overlay, with a badge when a threshold is reached
  - Pass quality: pass distance (metres), pass accuracy per player, and a pass difficulty rating (0 to 100)
  - Speed metrics: ball speed for passes and shots (km/h), from the `ball_speed_kmh` column of the event feed
  - Player-focused mode: a viewer follows one player and gets moments about them through the match

Distances are in metres (the pitch is about 105 x 68 m). Player running speed and distance covered are NOT here: the
event feed has no player tracking, only where each event happened.

    tracker = PlayerTracker(focus="Oscar Fitzroy")
    overlay, focus_moments = tracker.update(event)     # overlay: a timed name-tag dict, always; focus_moments: usually empty
"""

import math

# thresholds that trigger a badge (predefined, as in the brief)
LONG_PASS_M = 35.0  # a long ball
HARD_PASS = 70  # difficulty rating from which a completed pass earns a "hard pass" badge
FAST_BALL_KMH = 75.0  # a very hard-hit pass
POWER_SHOT_KMH = 105.0  # a very hard-hit shot
MILESTONES = (25, 50, 75, 100)  # completed passes
FOCUS_COOLDOWN_S = 60  # at most one non-goal focus moment per minute

BADGE_LABEL = {"long_pass": "long pass", "hard_pass": "hard pass", "fast_ball": "fast ball", "power_shot": "power shot",
               "goal": "goal", "big_chance": "big chance", "milestone": "passing milestone", "shot": "shot"}


def _num(v):
    """A number, or None if the value is missing or NaN."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def metres(x0, y0, x1, y1):
    """Straight-line distance in metres between two pitch points (pitch coordinates run 0 to 100)."""
    return math.hypot((x1 - x0) * 1.05, (y1 - y0) * 0.68)


def pass_distance(ev):
    return round(metres(ev["x"], ev["y"], ev["end_x"], ev["end_y"]), 1)


def pass_difficulty(ev):
    """Difficulty rating 0 to 100 from what the feed shows: length, direction, where it ends, and pressure.

    length up to 40 points (50 m or more = full), forward progress up to 20, ends in the final third 15 (and starts
    outside it: 10 more), made under pressure 20. A rough guide, not a fitted model.
    """
    d = pass_distance(ev)
    score = 40.0 * min(d, 50.0) / 50.0
    score += 20.0 * max(0.0, min((ev["end_x"] - ev["x"]) / 40.0, 1.0))
    if ev["end_x"] >= 66:
        score += 15.0 + (10.0 if ev["x"] < 66 else 0.0)
    if ev.get("under_pressure"):
        score += 20.0
    return int(round(min(score, 100.0)))


def difficulty_label(score):
    return "hard" if score >= HARD_PASS else "medium" if score >= 40 else "easy"


class PlayerTracker:
    def __init__(self, focus=None):
        self.focus = focus  # name of the followed player (matched without case, partial names work)
        self.players = {}  # player_id -> running stats
        self.last_focus_t = -1e9

    # ----- main entry -----

    def update(self, ev):
        """Feed one event. Returns (overlay, focus_moments)."""
        p = self._player(ev["player_id"], ev["player_name"], ev["position"], ev["team"])
        et, oc = ev["event_type"], ev["outcome"]
        metrics, badges = {}, []
        speed = _num(ev.get("ball_speed_kmh"))
        p["involvement"] += 1

        if et == "pass":
            dist, diff = pass_distance(ev), pass_difficulty(ev)
            metrics = {"distance_m": dist, "difficulty": diff, "difficulty_label": difficulty_label(diff)}
            if speed is not None:
                metrics["ball_speed_kmh"] = speed
            p["passes"] += 1
            p["dist_sum"] += dist
            p["diff_sum"] += diff
            p["longest"] = max(p["longest"], dist)
            if speed is not None:
                p["top_speed"] = max(p["top_speed"], speed)
            if oc == "complete":
                p["complete"] += 1
                if ev.get("receiver_id"):
                    self._player(ev["receiver_id"], ev.get("receiver_name") or ev["receiver_id"], None, ev["team"])["received"] += 1
                if diff >= HARD_PASS:
                    badges.append("hard_pass")
                if p["complete"] in MILESTONES:
                    badges.append("milestone")
            if dist >= LONG_PASS_M:
                badges.append("long_pass")
            if speed is not None and speed >= FAST_BALL_KMH:
                badges.append("fast_ball")
        elif et == "shot":
            xg = _num(ev.get("xg")) or 0.0
            p["shots"] += 1
            p["xg"] += xg
            metrics = {"xg": round(xg, 2)}
            if speed is not None:
                metrics["ball_speed_kmh"] = speed
                p["top_speed"] = max(p["top_speed"], speed)
            if oc == "goal":
                p["goals"] += 1
                badges.append("goal")
            elif xg >= 0.3:
                badges.append("big_chance")
            if speed is not None and speed >= POWER_SHOT_KMH:
                badges.append("power_shot")
        elif et == "tackle" and oc == "won":
            p["tackles_won"] += 1
        elif et == "interception":
            p["interceptions"] += 1
        elif et == "pressure":
            p["pressures"] += 1

        overlay = self._overlay(ev, metrics, badges)
        return overlay, self._focus_moments(ev, p, et, badges, metrics)

    # ----- readings -----

    def stats(self, player_id):
        """Derived stats of one player: accuracy, average difficulty and so on."""
        p = self.players.get(player_id)
        if not p:
            return None
        n = max(p["passes"], 1)
        return {**p, "accuracy_pct": round(100.0 * p["complete"] / n), "avg_pass_m": round(p["dist_sum"] / n, 1),
                "avg_difficulty": round(p["diff_sum"] / n), "xg": round(p["xg"], 2), "longest": round(p["longest"], 1)}

    def find(self, name):
        """The player_id whose name matches (case-insensitive, partial names work), or None."""
        if not name:
            return None
        q = name.strip().lower()
        for pid, p in self.players.items():
            if q and q in p["name"].lower():
                return pid
        return None

    def top(self, n=5, team=None):
        """Most involved players, as derived stats."""
        rows = [self.stats(pid) for pid, p in self.players.items() if team is None or p["team"] == team]
        return sorted(rows, key=lambda r: -r["involvement"])[:n]

    # ----- internals -----

    def _player(self, pid, name, position, team):
        p = self.players.get(pid)
        if p is None:
            p = self.players[pid] = {"player_id": pid, "name": name, "position": position, "team": team, "involvement": 0, "passes": 0,
                                     "complete": 0, "received": 0, "dist_sum": 0.0, "diff_sum": 0, "longest": 0.0, "top_speed": 0.0,
                                     "shots": 0, "goals": 0, "xg": 0.0, "tackles_won": 0, "interceptions": 0, "pressures": 0}
        if position and not p["position"]:
            p["position"] = position
        return p

    def _overlay(self, ev, metrics, badges):
        """A timed, machine-readable name tag: what an overlay renderer would draw next to the player."""
        et, oc = ev["event_type"], ev["outcome"]
        bits = [f"{ev['player_name']} ({ev['position']})", f"{et} {oc}".replace("applied", "").strip()]
        if "distance_m" in metrics:
            bits.append(f"{metrics['distance_m']:.0f} m")
        if "ball_speed_kmh" in metrics:
            bits.append(f"{metrics['ball_speed_kmh']:.0f} km/h")
        if "difficulty_label" in metrics:
            bits.append(f"{metrics['difficulty_label']} ({metrics['difficulty']})")
        return {"t": float(ev["timestamp_s"]), "minute": int(float(ev["timestamp_s"]) // 60), "match_id": ev["match_id"],
                "player_id": ev["player_id"], "name": ev["player_name"], "position": ev["position"], "team": ev["team"],
                "team_name": ev["team_name"], "event": et, "outcome": oc, "metrics": metrics, "badges": badges,
                "text": " · ".join(bits)}

    def _focus_moments(self, ev, p, et, badges, metrics):
        if not self.focus or self.focus.strip().lower() not in p["name"].lower():
            return []
        t = float(ev["timestamp_s"])
        if "goal" in badges:
            return []  # goals already have their own key moment
        kinds = list(badges)
        if et == "shot" and "big_chance" not in kinds:
            kinds.append("shot")
        if not kinds or t - self.last_focus_t < FOCUS_COOLDOWN_S:
            return []
        self.last_focus_t = t
        kind = kinds[0]
        s = self.stats(p["player_id"])
        facts = {"player": p["name"], "kind": kind, "badges": kinds, "metrics": metrics,
                 "stats": {k: s[k] for k in ("passes", "complete", "accuracy_pct", "shots", "goals", "xg", "longest", "top_speed")}}
        return [{"match_id": ev["match_id"], "elapsed_minute": int(t // 60), "timestamp_s": round(t, 1), "type": "player_focus",
                 "team": ev["team"], "team_name": ev["team_name"], "facts": facts,
                 "drivers": [f"{p['name']}: {BADGE_LABEL[kind]}" + (f" ({metrics['distance_m']:.0f} m)" if "distance_m" in metrics else "")]}]
