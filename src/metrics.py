"""Incremental metrics engine: momentum, pass entropy, chaos index, match state and key moments.

The engine reads one event at a time, exactly as a live feed would deliver it, and keeps a small fixed amount of
state (no re-reading of past events). It never reads a truth_*.csv file.

    engine = Engine()
    for event in feed:                      # event = dict with the events.csv columns
        moments = engine.update(event)      # list of key-moment dicts (usually empty)
        state = engine.snapshot()           # current momentum, entropy, chaos, match state

Three ideas, all computed per team:

  1. Momentum: time-decayed threat points (final-third passes, high regains, shots, pressing, lost passes),
     home minus away, with a two-threshold ("hysteresis") rule so the state does not flicker.
  2. Pass entropy: Tsallis entropy of passer->receiver pairs, with an exponential-decay window instead of a
     hard "last 40 passes" window, then compared with the team's own average in this match.
  3. Chaos index: entropy plus completion rate, pass-length spread and sideways spread, each as a z-score
     against the team's own baseline. Higher = less predictable play.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

# ---------- settings (tuned in tune_metrics.py / checked in validate_engine.py) ----------

Q = 0.2  # Tsallis entropy index (Neuman et al., 2018)
N_PLAYERS = 11  # players per team
N_CELLS = N_PLAYERS * (N_PLAYERS - 1)  # 110 possible passer->receiver pairs

ENT_HALFLIFE = 25.0  # entropy window: a pass loses half its weight after 25 of the team's completed passes
ATT_HALFLIFE = 25.0  # same idea for completion rate and pass-spread, counted in attempted passes
WARMUP_PASSES = 40  # a team's baseline starts only after this many completed passes (about 10 minutes)
BASE_MIN_N = 20  # baseline needs this many samples before z-scores are trusted
CHAOS_SIGNS = {"entropy": 1, "len_sd": 1, "lat_sd": 1}  # features in the chaos index and their direction (completion rate is left out: it also drops when a team is dominated)
Z_CLIP = 3.0  # z-scores are clipped to +-3 so one odd value cannot dominate the index

MOM_HALFLIFE_MIN = 1.5  # threat points lose half their weight after 1.5 minutes
WEIGHTS = {  # threat points per event (least-squares fit, tune_metrics.py, seed 123; shot fixed at 3)
    "ft_pass": 1.1,  # completed pass ending in the final third (end_x >= 66)
    "high_regain": 0.6,  # tackle or interception won in the opponent's half (x >= 50)
    "shot": 3.0,  # any shot
    "pressure": 2.2,  # pressing action against the opponent
    "lost_pass": 0.0,  # penalty for an incomplete pass (the fit found no use for it)
}
THR_IN, THR_OUT = 6.0, 3.0  # momentum enters "dominant" above THR_IN, leaves it below THR_OUT (points per minute)
MOM_WARMUP_S = 180  # no key moments in the first 3 minutes: momentum has not built up yet
CHAOS_BAND = 0.25  # chaos difference needed before the "more chaotic" team changes
CHAOS_DWELL_S = 180  # and it can change at most once every 3 minutes (stops flicker)
DRIVER_WINDOW_S = 300  # key-moment explanations look at the last 5 minutes
SHIFT_HOLD_S = 60  # a new dominant state must hold this long before a momentum_shift moment is published
MOMENT_COOLDOWN_S = 120  # at most one momentum_shift / style_change moment in this many seconds
CHAOS_BETA = 0.2  # removes the link between chaos and dominance: chaos + beta * (team momentum / 10)
BIG_CHANCE_XG = 0.3  # a missed shot with at least this xG is a "big_chance" moment

OTHER = {"home": "away", "away": "home"}
REGIME = {"home": 1, "balanced": 0, "away": -1}
REGIME_NAME = {1: "home_dominant", 0: "balanced", -1: "away_dominant"}


# ---------- entropy helpers ----------

def tsallis(p, q=Q):
    """Tsallis entropy of a probability vector (zeros are ignored)."""
    p = np.asarray(p, dtype=float)
    p = p[p > 0]
    return float((1 - np.sum(p ** q)) / (q - 1))


def tsallis_max(n_cells, q=Q):
    """Entropy of a perfectly uniform distribution over n_cells: used to scale to 0..1."""
    return (1 - n_cells ** (1 - q)) / (q - 1)


MAX_H = tsallis_max(N_CELLS)


class Baseline:
    """Running mean and standard deviation (Welford's method): the team's own average in this match."""

    def __init__(self):
        self.n, self.mean, self._m2 = 0, 0.0, 0.0

    def add(self, x):
        self.n += 1
        d = x - self.mean
        self.mean += d / self.n
        self._m2 += d * (x - self.mean)

    @property
    def std(self):
        return math.sqrt(self._m2 / (self.n - 1)) if self.n > 1 else 0.0

    def z(self, x):
        if self.n < BASE_MIN_N:
            return 0.0
        return max(-Z_CLIP, min(Z_CLIP, (x - self.mean) / max(self.std, 1e-6)))


class TeamStyle:
    """Pass entropy and chaos features for one team, updated one pass at a time."""

    FEATURES = ("entropy", "completion", "len_sd", "lat_sd")

    def __init__(self):
        self.pairs = np.zeros((N_PLAYERS, N_PLAYERS))  # decayed count of each passer->receiver pair
        self.d_ent = 0.5 ** (1.0 / ENT_HALFLIFE)
        self.d_att = 0.5 ** (1.0 / ATT_HALFLIFE)
        self.n_complete = 0  # completed passes seen
        self.sw = self.sc = 0.0  # decayed attempts and completions
        self.sx = self.sxx = self.sy = self.syy = 0.0  # decayed sums of pass length and sideways movement
        self.value = {k: None for k in self.FEATURES}  # latest value of each feature
        self.base = {k: Baseline() for k in self.FEATURES}  # the team's own average of each feature
        self.entropy = self.entropy_rel = self.chaos = 0.0  # current outputs

    def add_pass(self, ev):
        """Update with one pass (complete or incomplete) by this team."""
        dx, dy = ev["end_x"] - ev["x"], ev["end_y"] - ev["y"]
        done = ev["outcome"] == "complete"
        a = self.d_att
        self.sw = self.sw * a + 1
        self.sc = self.sc * a + (1.0 if done else 0.0)
        self.sx, self.sxx = self.sx * a + dx, self.sxx * a + dx * dx
        self.sy, self.syy = self.sy * a + dy, self.syy * a + dy * dy
        if done:
            i, j = _pidx(ev["player_id"]), _pidx(ev["receiver_id"])
            self.pairs *= self.d_ent
            self.pairs[i, j] += 1
            self.n_complete += 1
        self._refresh()

    def _refresh(self):
        v = self.value
        if self.n_complete:
            v["entropy"] = tsallis(self.pairs / self.pairs.sum()) / MAX_H
        if self.sw >= 10:
            v["completion"] = self.sc / self.sw
            v["len_sd"] = math.sqrt(max(self.sxx / self.sw - (self.sx / self.sw) ** 2, 0.0))
            v["lat_sd"] = math.sqrt(max(self.syy / self.sw - (self.sy / self.sw) ** 2, 0.0))
        if self.n_complete < WARMUP_PASSES or any(x is None for x in v.values()):
            return  # too early: no baseline, no chaos reading (outputs stay at 0)
        for k in self.FEATURES:
            self.base[k].add(v[k])
        self.entropy = v["entropy"]
        self.entropy_rel = v["entropy"] - self.base["entropy"].mean  # entropy vs the team's own average
        z = [sg * self.base[k].z(v[k]) for k, sg in CHAOS_SIGNS.items()]  # z-score of each feature, signed so that up = more chaotic
        self.chaos = sum(z) / len(z)  # chaos index: mean z-score, higher = less predictable


def _pidx(player_id):
    return int(player_id[1:]) - 1  # "H07" -> 6


# ---------- engine ----------

class Engine:
    """Reads events in order and tracks momentum, entropy, chaos, match state and key moments."""

    def __init__(self, weights=None, thr_in=THR_IN, thr_out=THR_OUT, halflife_min=MOM_HALFLIFE_MIN,
                 shift_hold_s=None, cooldown_s=None, chaos_beta=None):
        self.w = dict(WEIGHTS if weights is None else weights)
        self.shift_hold_s = SHIFT_HOLD_S if shift_hold_s is None else shift_hold_s
        self.cooldown_s = MOMENT_COOLDOWN_S if cooldown_s is None else cooldown_s
        self.chaos_beta = CHAOS_BETA if chaos_beta is None else chaos_beta
        self.pending = None  # (team, since) for a dominant state that has not held long enough yet
        self.last_moment_t = -1e9  # stream time of the last momentum_shift / style_change
        self.last_chance_t = -1e9  # stream time of the last big_chance
        self.thr_in, self.thr_out = thr_in, thr_out
        self.tau_min = halflife_min / math.log(2)  # time constant of the decay, in minutes
        self.decay_per_s = math.log(2) / (halflife_min * 60)
        self.m = {"home": 0.0, "away": 0.0}  # decayed threat points per team
        self.t = 0.0  # stream time of the latest event
        self.regime = 0  # dominance state with hysteresis: +1 home, 0 balanced, -1 away
        self.style = {"home": TeamStyle(), "away": TeamStyle()}
        self.chaotic_since = 0.0  # stream time when chaotic_side last changed
        self.chaotic_side = None  # which team is currently the more chaotic one ("home" / "away" / None)
        self.score = {"home": 0, "away": 0}
        self.match_id = None
        self.names = {}
        self.recent = deque()  # (time, team, kind) of recent threat events, for key-moment explanations
        self.moments = []  # every key moment so far

    # ----- main entry -----

    def update(self, ev):
        """Feed one event. Returns the list of key moments it triggered (usually empty)."""
        t = float(ev["timestamp_s"])
        self._decay_to(t)
        self.match_id = ev["match_id"]
        self.names[ev["team"]] = ev["team_name"]
        self.score = {"home": int(ev["home_score"]), "away": int(ev["away_score"])}
        out = []

        for team, kind in self._threats(ev):
            self.m[team] += self.w[kind] if kind != "lost_pass" else -self.w[kind]
            self.recent.append((t, team, kind))
        if ev["event_type"] == "pass":
            self.style[ev["team"]].add_pass(ev)
        while self.recent and self.recent[0][0] < t - DRIVER_WINDOW_S:
            self.recent.popleft()

        if ev["event_type"] == "shot" and ev["outcome"] == "goal":
            out.append(self._moment("goal", ev["team"], t, drivers=[f"{ev['player_name']} scores (xG {ev['xg']:.2f})"],
                                    facts={"player": ev["player_name"], "xg": round(float(ev["xg"]), 2)}))
        elif ev["event_type"] == "shot" and ev["xg"] >= BIG_CHANCE_XG and t - self.last_chance_t >= max(self.cooldown_s, 60):
            self.last_chance_t = t
            out.append(self._moment("big_chance", ev["team"], t, drivers=[f"{ev['player_name']} took a shot: {ev['outcome'].replace('_', ' ')} (xG {ev['xg']:.2f})"],
                                    facts={"player": ev["player_name"], "xg": round(float(ev["xg"]), 2), "outcome": ev["outcome"]}))
        out += self._update_states(t)
        self.moments += out
        return out

    # ----- readings -----

    @property
    def momentum(self):
        """Home minus away threat, in points per minute (positive = home on top)."""
        return (self.m["home"] - self.m["away"]) / self.tau_min

    def chaos_of(self, team):
        """Chaos index of one team, with the link to dominance removed (beta = 0 means no adjustment)."""
        if self.style[team].n_complete < WARMUP_PASSES:
            return 0.0  # no reading yet
        sign = 1 if team == "home" else -1
        return self.style[team].chaos + self.chaos_beta * sign * self.momentum / 10.0

    def chaos_diff(self):
        """Home chaos index minus away chaos index (positive = home is the less predictable side)."""
        return self.chaos_of("home") - self.chaos_of("away")

    def team_state(self, team):
        """Match-state quadrant for one team (see README): dominance x (more or less chaotic than the opponent)."""
        if self.regime == 0 or self.chaotic_side is None:
            return "balanced"
        leading = (REGIME[team] == self.regime)
        chaotic = (self.chaotic_side == team)
        return {(True, False): "controlled_dominance", (True, True): "chaotic_pressure",
                (False, False): "being_read", (False, True): "scrambling"}[(leading, chaotic)]

    def snapshot(self):
        s = {"match_id": self.match_id, "elapsed_minute": int(self.t // 60), "momentum": round(self.momentum, 2),
             "regime": self.regime, "regime_name": REGIME_NAME[self.regime], "chaos_diff": round(self.chaos_diff(), 2),
             "score": dict(self.score)}
        for team in ("home", "away"):
            st = self.style[team]
            s[team] = {"entropy": round(st.entropy, 3), "entropy_rel": round(st.entropy_rel, 3),
                       "chaos": round(self.chaos_of(team), 2), "state": self.team_state(team)}
        return s

    # ----- internals -----

    def _decay_to(self, t):
        f = math.exp(-self.decay_per_s * max(0.0, t - self.t))
        self.m["home"] *= f
        self.m["away"] *= f
        self.t = max(self.t, t)

    @staticmethod
    def _threats(ev):
        """Threat signals in one event, as (team credited, kind)."""
        et, oc, team = ev["event_type"], ev["outcome"], ev["team"]
        if et == "pass":
            if oc == "complete":
                return [(team, "ft_pass")] if ev["end_x"] >= 66 else []
            return [(team, "lost_pass")]
        if et in ("tackle", "interception") and oc == "won" and ev["x"] >= 50:
            return [(team, "high_regain")]
        if et == "shot":
            return [(team, "shot")]
        if et == "pressure":
            return [(team, "pressure")]
        return []

    def _update_states(self, t):
        out = []
        mom = self.momentum
        old_regime = self.regime
        # dominance with hysteresis: enter at thr_in, leave at thr_out, jump to the other side only past its thr_in
        if mom >= self.thr_in:
            self.regime = 1
        elif mom <= -self.thr_in:
            self.regime = -1
        elif self.regime == 1 and mom < self.thr_out:
            self.regime = 0
        elif self.regime == -1 and mom > -self.thr_out:
            self.regime = 0
        # which team is the more chaotic one, with a dead band so it does not flip on small differences
        diff = self.chaos_diff()
        old_side = self.chaotic_side
        warm = all(s.n_complete >= WARMUP_PASSES for s in self.style.values())
        if warm:
            want = "home" if diff > CHAOS_BAND else "away" if diff < -CHAOS_BAND else None  # a clear winner of the comparison, or none
            if self.chaotic_side is None:  # first reading after warm-up
                self.chaotic_side, self.chaotic_since = ("home" if diff >= 0 else "away"), t
            elif want and want != self.chaotic_side and t - self.chaotic_since >= CHAOS_DWELL_S:  # clear change and not too soon
                self.chaotic_side, self.chaotic_since = want, t
        if self.regime != old_regime:  # a new dominance state: start (or cancel) the hold timer
            self.pending = ("home" if self.regime == 1 else "away", t) if self.regime != 0 else None
        if t < MOM_WARMUP_S:
            return out
        cool = t - self.last_moment_t >= self.cooldown_s
        if self.pending and cool and t - self.pending[1] >= self.shift_hold_s:
            team = self.pending[0]
            self.pending = None
            self.last_moment_t = t
            out.append(self._moment("momentum_shift", team, t, drivers=self._drivers(team)))
        elif self.regime != 0 and self.chaotic_side != old_side and old_side is not None and cool:
            team = "home" if self.regime == 1 else "away"
            self.last_moment_t = t
            out.append(self._moment("style_change", team, t, drivers=self._drivers(team, style_only=True)))
        return out

    def moment(self, kind, team, t, drivers, facts=None):
        """A key moment built from outside the engine (for example a player-focus moment), with the engine's current momentum and state."""
        return self._moment(kind, team, t, drivers, facts)

    def _moment(self, kind, team, t, drivers, facts=None):
        return {"match_id": self.match_id, "elapsed_minute": int(t // 60), "timestamp_s": round(t, 1), "type": kind,
                "team": team, "team_name": self.names.get(team), "momentum": round(self.momentum, 2),
                "state": self.team_state(team) if kind not in ("goal", "big_chance", "player_focus") else None, "chaos_diff": round(self.chaos_diff(), 2),
                "drivers": drivers, "facts": facts if facts is not None else self._facts(team),
                "score": dict(self.score)}

    def _facts(self, team):
        """The same reasons as _drivers, as numbers: {kind: [team count, opponent count]} over the last 5 minutes."""
        n = {(tm, k): 0 for tm in ("home", "away") for k in self.w}
        for _, tm, k in self.recent:
            n[(tm, k)] += 1
        counts = {k: [n[(team, k)], n[(OTHER[team], k)]] for k in ("high_regain", "shot", "ft_pass", "pressure")}
        passing = None if self.chaotic_side is None else ("less_predictable" if self.chaotic_side == team else "more_predictable")
        return {"window_min": DRIVER_WINDOW_S // 60, "counts": counts, "passing": passing}

    def _drivers(self, team, style_only=False):
        """Plain-language reasons: what the team did more than the opponent in the last 5 minutes."""
        n = {(tm, k): 0 for tm in ("home", "away") for k in self.w}
        for _, tm, k in self.recent:
            n[(tm, k)] += 1
        labels = {"high_regain": "high regains", "shot": "shots", "ft_pass": "completed passes into the final third",
                  "pressure": "pressing actions", "lost_pass": "lost passes"}
        gaps = sorted(((n[(team, k)] - n[(OTHER[team], k)], k) for k in ("high_regain", "shot", "ft_pass", "pressure")), reverse=True)
        reasons = [] if style_only else [f"{n[(team, k)]} {labels[k]} vs {n[(OTHER[team], k)]} in 5 min" for g, k in gaps[:2] if g > 0]
        if self.chaotic_side is not None:  # explain the style label with the same side the state uses
            reasons.append(f"{'less' if self.chaotic_side == team else 'more'} predictable passing than the opponent")
        return reasons
