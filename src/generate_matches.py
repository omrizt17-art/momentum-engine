"""Synthetic football match generator with hidden momentum regimes.

Each match is simulated event by event (passes, pressures, tackles, interceptions, shots).
A hidden "who is dominating" regime switches during the match and shifts event probabilities,
so the momentum engine can later be validated against a known ground truth.

Coordinates: every event is in the acting team's attacking direction
(x = 0 own goal line, x = 100 opponent goal line; y = 0 to 100 across the pitch).
"""

import argparse  # read command-line options
from pathlib import Path  # build file paths safely

import numpy as np  # random numbers and math
import pandas as pd  # tables and CSV output

POSITIONS = ["GK", "DEF", "DEF", "DEF", "DEF", "MID", "MID", "MID", "FWD", "FWD", "FWD"]  # 4-3-3 line-up, one entry per player

RECEIVE_WEIGHTS = {  # how likely each role is to receive a pass, by the third the ball is in
    "def": {"GK": 0.05, "DEF": 0.45, "MID": 0.40, "FWD": 0.10},  # ball in own third: mostly defenders and midfielders
    "mid": {"GK": 0.00, "DEF": 0.20, "MID": 0.50, "FWD": 0.30},  # ball in middle third: midfield-heavy
    "att": {"GK": 0.00, "DEF": 0.05, "MID": 0.40, "FWD": 0.55},  # ball in final third: forwards get it most
}

DEFEND_WEIGHTS = {  # which role of the defending team makes a challenge, by the attacker's third
    "def": {"GK": 0.00, "DEF": 0.05, "MID": 0.35, "FWD": 0.60},  # attacker deep in own half: defending forwards press
    "mid": {"GK": 0.00, "DEF": 0.30, "MID": 0.55, "FWD": 0.15},  # midfield battle
    "att": {"GK": 0.00, "DEF": 0.60, "MID": 0.35, "FWD": 0.05},  # attacker near goal: defenders challenge
}

REGIME_LABELS = {-1: "away_dominant", 0: "balanced", 1: "home_dominant"}  # names for the hidden momentum state

TEAM_NAMES = ["Northbridge FC", "Harbour City", "Ashford Rovers", "Kingsmere United", "Redcliffe Town", "Westvale Athletic"]  # invented clubs
FIRST_NAMES = ["Arlo", "Tomas", "Kai", "Luca", "Noah", "Elias", "Mateo", "Jonah", "Felix", "Rafael", "Oscar", "Idris", "Marek", "Theo", "Sami", "Jude"]  # invented first names
LAST_NAMES = ["Venn", "Okafor", "Lindqvist", "Moreau", "Hale", "Brandt", "Costa", "Ferris", "Nakamura", "Quill", "Rossi", "Adeyemi", "Kerr", "Duval", "Sato", "Ward",  # invented surnames
              "Mbeki", "Larsen", "Ortega", "Petrov", "Achterberg", "Silva", "Kowalski", "Haddad", "Fitzroy", "Ishikawa", "Novak", "Calloway", "Delgado", "Eriksen"]  # 30 in total, enough for 22 unique players per match


def zone(x):  # which third of the pitch an x coordinate falls in
    if x < 33:  # first third
        return "def"  # own third
    if x < 66:  # second third
        return "mid"  # middle third
    return "att"  # final third


def make_team(name, side, rng, surnames):  # build one synthetic squad (surnames: 11 unique names reserved for this team)
    alpha = rng.uniform(0.3, 3.0)  # passing style: low alpha = favours a few players = lower entropy
    pref = rng.dirichlet([alpha] * len(POSITIONS))  # each player's share of the team's passing habits
    players = []  # will hold the 11 player dicts
    for i, pos in enumerate(POSITIONS):  # one loop per squad slot
        players.append({  # add a player
            "player_id": f"{side[0].upper()}{i + 1:02d}",  # e.g. H07 for home player 7
            "player_name": f"{rng.choice(FIRST_NAMES)} {surnames[i]}",  # random first name + a surname unique within the match
            "position": pos,  # GK / DEF / MID / FWD
            "pref": pref[i],  # habit weight from the Dirichlet draw
            "team": side,  # home or away
            "team_name": name,  # club name
        })
    return {"name": name, "side": side, "alpha": alpha, "players": players}  # the team dict


def pick(players, weights_by_role, rng, exclude=None, boost=None, habit=1.0):  # weighted random choice of a player
    cands = [p for p in players if p is not exclude]  # everyone except the excluded player (e.g. the passer)
    w = np.array([weights_by_role[p["position"]] * p["pref"] ** habit for p in cands])  # role weight times personal habit (habit < 1 flattens it = more chaotic)
    if boost is not None:  # a player on a hot streak gets involved more
        w = w * np.array([2.0 if p is boost else 1.0 for p in cands])  # double the hot player's weight
    w = w / w.sum()  # normalise to probabilities
    return cands[rng.choice(len(cands), p=w)]  # draw one player


def shot_xg(x, y):  # simple expected-goals model from shot location
    dx = (100 - x) * 1.05  # metres to the goal line (pitch is ~105 m long)
    dy = (y - 50) * 0.68  # metres from the centre line of the goal (pitch is ~68 m wide)
    dist = np.hypot(dx, dy)  # straight-line distance to goal centre
    return float(1 / (1 + np.exp(0.17 * dist - 0.6)))  # logistic decay: ~0.22 at 11 m, ~0.06 at 20 m


def metres(x0, y0, x1, y1):  # straight-line distance in metres between two pitch points (pitch ~105 x 68 m)
    return float(np.hypot((x1 - x0) * 1.05, (y1 - y0) * 0.68))


def simulate_match(match_id, rng):  # simulate one full match
    speed_rng = np.random.default_rng(int(rng.bit_generator.state["state"]["state"]) % (2 ** 32))  # separate stream for ball speeds: reads the main RNG state without using it, so every other column stays identical
    home_name, away_name = rng.choice(TEAM_NAMES, size=2, replace=False)  # two different clubs
    surnames = rng.choice(LAST_NAMES, size=22, replace=False)  # 22 different surnames so no two players in a match share one
    teams = {"home": make_team(home_name, "home", rng, surnames[:11]), "away": make_team(away_name, "away", rng, surnames[11:])}  # build both squads
    other = {"home": "away", "away": "home"}  # lookup for the opposing side

    hot = {}  # hidden hot streaks, one per team
    for side in teams:  # for home and away
        outfield = [p for p in teams[side]["players"] if p["position"] != "GK"]  # goalkeepers excluded
        start = rng.uniform(10, 75) * 60  # streak start, in seconds of elapsed time
        hot[side] = (outfield[rng.integers(len(outfield))], start, start + rng.uniform(8, 15) * 60)  # (player, start, end)

    events, regime_log = [], []  # outputs: event rows and the hidden regime per minute
    score = {"home": 0, "away": 0}  # running score
    regime = 0  # hidden state starts balanced
    chaos = {"home": 0, "away": 0}  # hidden play style per team: 0 = structured, 1 = chaotic
    next_check = 0.0  # elapsed second of the next regime update
    elapsed = 0.0  # continuous seconds since kickoff (used for live streaming)
    eid = 0  # event counter

    def shot_speed(x, y):  # shot speed in km/h: faster from range, with noise
        return round(float(np.clip(speed_rng.normal(84 + 0.5 * metres(x, y, 100, 50), 9), 55, 130)), 1)

    def add(side, player, etype, outcome, x, y, period, clock, **extra):  # append one event row
        nonlocal eid  # modify the counter defined above
        eid += 1  # next event id
        events.append({  # the event record
            "match_id": match_id, "event_id": eid, "period": period,  # identifiers
            "minute": int(clock // 60), "second": int(clock % 60), "timestamp_s": round(elapsed, 1),  # match clock and stream time
            "team": side, "team_name": teams[side]["name"],  # acting team
            "player_id": player["player_id"], "player_name": player["player_name"], "position": player["position"],  # acting player
            "event_type": etype, "outcome": outcome,  # what happened
            "x": round(x, 1), "y": round(y, 1),  # where it happened
            "end_x": extra.get("end_x"), "end_y": extra.get("end_y"),  # where the ball went (passes)
            "receiver_id": extra.get("receiver_id"), "receiver_name": extra.get("receiver_name"),  # intended receiver (passes)
            "xg": extra.get("xg"), "under_pressure": extra.get("under_pressure", False),  # shot quality and pressure flag
            "ball_speed_kmh": extra.get("ball_speed_kmh"),  # measured ball speed (passes and shots)
            "home_score": score["home"], "away_score": score["away"],  # score after this event
        })

    for period in (1, 2):  # two halves
        clock = 0.0 if period == 1 else 45 * 60.0  # match clock restarts at 45:00 for the second half
        end_clock = clock + 45 * 60 + rng.uniform(60, 300)  # half length including stoppage time
        side = "home" if period == 1 else "away"  # who kicks off
        holder = [p for p in teams[side]["players"] if p["position"] == "MID"][0]  # a midfielder takes the kickoff
        x, y = 50.0, 50.0  # ball on the centre spot

        while clock < end_clock:  # play until the half ends
            while elapsed >= next_check:  # update the hidden regime once per elapsed minute
                if rng.random() > 0.93:  # 7% chance per minute that the momentum regime switches
                    regime = int(rng.choice([r for r in (-1, 0, 1) if r != regime]))  # jump to a different state
                for s in chaos:  # each team's play style switches independently of momentum
                    if rng.random() < 0.05:  # 5% chance per minute
                        chaos[s] = 1 - chaos[s]  # structured <-> chaotic
                regime_log.append({"match_id": match_id, "elapsed_minute": int(next_check // 60), "regime": regime, "label": REGIME_LABELS[regime],  # record ground truth
                                   "home_chaotic": chaos["home"], "away_chaotic": chaos["away"]})  # hidden style of each team
                next_check += 60  # schedule the next check

            d = regime if side == "home" else -regime  # +1 if the team in possession is dominant, -1 if dominated
            minute = clock / 60  # current match minute
            fatigue = max(0.0, minute - 55) * 0.0015  # pass accuracy drops slowly after the 55th minute
            hp, hs, he = hot[side]  # hot-streak player of the team in possession
            is_hot = holder is hp and hs <= elapsed <= he  # is the ball carrier currently on a streak
            bonus = 0.08 if is_hot else 0.0  # hot players are more accurate
            boost = hp if hs <= elapsed <= he else None  # hot players also get the ball more
            defenders = teams[other[side]]["players"]  # the opposing squad
            wild = chaos[side]  # is the team in possession currently playing chaotically

            if x >= 75 and rng.random() < 0.024 + 0.010 * d + (x - 75) * 0.0015:  # in the final quarter: maybe shoot (tuned to ~25 shots per match)
                xg = min(0.95, shot_xg(x, y) * (1.2 if is_hot else 1.0))  # shot quality, better if hot
                if rng.random() < xg:  # the shot goes in
                    score[side] += 1  # update the score
                    add(side, holder, "shot", "goal", x, y, period, clock, xg=round(xg, 3), ball_speed_kmh=shot_speed(x, y))  # log the goal
                    if rng.random() < 0.4:  # sometimes the conceding team surges after a goal
                        regime = -1 if side == "home" else 1  # momentum flips to the conceding side
                    side = other[side]  # conceding team kicks off
                    holder = [p for p in teams[side]["players"] if p["position"] == "MID"][0]  # kickoff midfielder
                    x, y = 50.0, 50.0  # back to the centre spot
                    dt = rng.uniform(40, 70)  # celebration and restart time
                else:  # the shot does not go in
                    outcome = str(rng.choice(["saved", "off_target", "blocked"], p=[0.4, 0.4, 0.2]))  # what happened to it
                    add(side, holder, "shot", outcome, x, y, period, clock, xg=round(xg, 3), ball_speed_kmh=shot_speed(x, y))  # log the shot
                    side = other[side]  # opponent gets the ball
                    holder = [p for p in teams[side]["players"] if p["position"] == "GK"][0]  # their goalkeeper restarts
                    x, y = 6.0, 50.0  # goal kick position
                    dt = rng.uniform(8, 20)  # restart time
            elif rng.random() < 0.10:  # sometimes the opponent challenges the ball carrier directly
                tackler = pick(defenders, DEFEND_WEIGHTS[zone(x)], rng)  # who makes the tackle
                if rng.random() < 0.45 - 0.10 * d:  # tackle succeeds more often against a dominated team
                    add(other[side], tackler, "tackle", "won", 100 - x, 100 - y, period, clock)  # log it in the tackler's direction
                    side, holder, x, y = other[side], tackler, 100 - x, 100 - y  # possession switches at the same spot
                else:  # carrier beats the tackle
                    add(other[side], tackler, "tackle", "lost", 100 - x, 100 - y, period, clock)  # log the failed tackle
                dt = rng.uniform(1.5, 3.5)  # duels are quick
            else:  # the usual case: a pass, maybe under pressure
                pressed = rng.random() < 0.22 - 0.08 * d  # dominant teams get pressed less
                if pressed:  # log the pressing action
                    presser = pick(defenders, DEFEND_WEIGHTS[zone(x)], rng)  # who presses
                    add(other[side], presser, "pressure", "applied", 100 - x, 100 - y, period, clock)  # in presser's direction
                receiver = pick(teams[side]["players"], RECEIVE_WEIGHTS[zone(x)], rng, exclude=holder, boost=boost, habit=0.2 if wild else 1.0)  # intended target (chaotic = less habitual)
                ex = float(np.clip(x + rng.normal(5 + 4 * d, 13 if wild else 9), 2, 92))  # pass end x: dominant teams progress further, chaotic passes vary more
                ey = float(np.clip(y + rng.normal(0, 20 if wild else 15), 2, 98))  # pass end y: lateral spread, wider when chaotic
                p_ok = 0.85 + 0.05 * d - 0.12 * pressed - fatigue + bonus - (0.08 if zone(ex) == "att" else 0) - 0.04 * wild  # completion probability (chaotic play is riskier)
                ok = rng.random() < p_ok  # does the pass arrive
                add(side, holder, "pass", "complete" if ok else "incomplete", x, y, period, clock,  # log the pass
                    end_x=round(ex, 1), end_y=round(ey, 1), receiver_id=receiver["player_id"],  # where and to whom
                    receiver_name=receiver["player_name"], under_pressure=bool(pressed),  # receiver name and pressure flag
                    ball_speed_kmh=round(float(np.clip(speed_rng.normal(32 + 1.1 * metres(x, y, ex, ey), 5), 20, 115)), 1))  # ball speed: longer passes are hit harder
                if ok:  # pass completed
                    holder, x, y = receiver, ex, ey  # receiver now has the ball
                else:  # pass lost
                    interceptor = pick(defenders, DEFEND_WEIGHTS[zone(ex)], rng)  # who wins it
                    add(other[side], interceptor, "interception", "won", 100 - ex, 100 - ey, period, clock)  # log the regain
                    side, holder, x, y = other[side], interceptor, 100 - ex, 100 - ey  # possession switches
                dt = rng.uniform(2.0, 5.0)  # time until the next action

            clock += dt  # advance the match clock
            elapsed += dt  # advance continuous stream time (half-time is skipped so the stream runs continuously)

    hot_rows = [{"match_id": match_id, "team": s, "player_id": p["player_id"], "player_name": p["player_name"],  # hidden hot streaks
                 "start_minute": round(a / 60, 1), "end_minute": round(b / 60, 1)} for s, (p, a, b) in hot.items()]  # in elapsed minutes
    roster = [{"match_id": match_id, **{k: v for k, v in p.items() if k != "pref"}} for t in teams.values() for p in t["players"]]  # squads without hidden weights
    meta = {"match_id": match_id, "home": home_name, "away": away_name, "home_score": score["home"], "away_score": score["away"]}  # public match summary
    style_rows = [{"match_id": match_id, "team": s, "team_name": t["name"], "style_alpha": round(t["alpha"], 2)} for s, t in teams.items()]  # hidden passing style per team
    return events, regime_log, hot_rows, roster, meta, style_rows  # everything for this match


def main():  # command-line entry point
    ap = argparse.ArgumentParser(description="Generate synthetic football matches")  # argument parser
    ap.add_argument("--matches", type=int, default=10, help="number of matches")  # how many matches
    ap.add_argument("--seed", type=int, default=42, help="random seed for reproducibility")  # same seed = same data
    ap.add_argument("--out", default="data", help="output folder")  # where files go
    args = ap.parse_args()  # read the options

    rng = np.random.default_rng(args.seed)  # one random generator for everything
    out = Path(args.out)  # output folder path
    out.mkdir(parents=True, exist_ok=True)  # create it if needed

    all_ev, all_reg, all_hot, all_ros, all_meta, all_sty = [], [], [], [], [], []  # collectors across matches
    for m in range(1, args.matches + 1):  # simulate each match
        ev, reg, hot, ros, meta, sty = simulate_match(m, rng)  # one match
        all_ev += ev; all_reg += reg; all_hot += hot; all_ros += ros; all_meta.append(meta); all_sty += sty  # collect results

    pd.DataFrame(all_ev).to_csv(out / "events.csv", index=False)  # the event stream (what the engine sees)
    pd.DataFrame(all_ros).to_csv(out / "players.csv", index=False)  # squads
    pd.DataFrame(all_meta).to_csv(out / "matches.csv", index=False)  # one row per match with final score
    pd.DataFrame(all_reg).to_csv(out / "truth_regimes.csv", index=False)  # hidden momentum, for validation only
    pd.DataFrame(all_hot).to_csv(out / "truth_hot_streaks.csv", index=False)  # hidden streaks, for validation only
    pd.DataFrame(all_sty).to_csv(out / "truth_team_styles.csv", index=False)  # hidden passing styles, for validation only
    print(pd.DataFrame(all_meta)[["match_id", "home", "home_score", "away_score", "away"]].to_string(index=False))  # quick summary
    print(f"\n{len(all_ev)} events written to {out}/")  # total size


if __name__ == "__main__":  # run only when executed directly
    main()  # start
