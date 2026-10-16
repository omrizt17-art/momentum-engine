"""Tune the momentum and entropy metrics against the hidden truth.

Simulates a fresh set of matches (different seed from the demo data, so we don't tune on what we look at),
then tries every combination of settings and prints which ones recover the hidden states best.

Usage:  python src/tune_metrics.py --matches 50 --seed 123
"""

import argparse  # read command-line options
import sys  # to find the generator module
from pathlib import Path  # file paths

import numpy as np  # math
import pandas as pd  # tables

sys.path.insert(0, str(Path(__file__).parent))  # let us import files from src/
from generate_matches import simulate_match  # the match simulator


# ---------- data ----------

def make_matches(n, seed):  # simulate n matches in memory
    rng = np.random.default_rng(seed)  # random generator
    ev, truth = [], []  # collectors
    for m in range(1, n + 1):  # each match
        e, reg, _, _, _, _ = simulate_match(m, rng)  # events and hidden regime log (other outputs not needed)
        ev += e  # keep events
        truth += reg  # keep hidden states
    ev = pd.DataFrame(ev)  # events table
    ev["elapsed_minute"] = (ev.timestamp_s // 60).astype(int)  # join key with the truth
    return ev, pd.DataFrame(truth)  # both tables


# ---------- momentum ----------

def momentum_table(ev, window, pass_w, regain_w=2.0, shot_w=3.0):  # rolling momentum per match-minute
    ft = (ev.event_type == "pass") & (ev.outcome == "complete") & (ev.end_x >= 66)  # completed pass into the final third
    hw = ev.event_type.isin(["interception", "tackle"]) & (ev.outcome == "won") & (ev.x >= 50)  # ball won in opponent's half
    sh = ev.event_type == "shot"  # shot
    pts = (ft * pass_w + hw * regain_w + sh * shot_w) * np.where(ev.team == "home", 1, -1)  # signed threat points
    raw = pts.groupby([ev.match_id, ev.elapsed_minute]).sum()  # net points per minute
    out = []  # collector
    for mid, s in raw.groupby(level=0):  # each match
        s = s.droplevel(0)  # index = minute
        s = s.reindex(range(s.index.max() + 1), fill_value=0)  # include quiet minutes
        r = s.rolling(window, min_periods=1).mean()  # average over the last `window` minutes
        out.append(pd.DataFrame({"match_id": mid, "elapsed_minute": r.index, "momentum": r.values}))  # keep
    return pd.concat(out)  # one row per match-minute


def detection_lag(df, cap=10):  # minutes from a real switch into a dominant state until the rule agrees
    lags = []  # collector
    for _, g in df.groupby("match_id"):  # each match
        reg, pred = g.regime.values, g.pred.values  # truth and guess, minute by minute
        for i in range(1, len(reg)):  # walk through the minutes
            if reg[i] != reg[i - 1] and reg[i] != 0 and (reg[i:i + 5] == reg[i]).all():  # switch into a dominant state lasting 5+ min
                hit = np.where(pred[i:i + cap] == reg[i])[0]  # minutes where the guess matches
                lags.append(hit[0] if len(hit) else cap)  # first match, or `cap` if never
    lags = np.array(lags)  # as an array
    return np.median(lags), (lags <= 3).mean()  # typical lag, share caught within 3 minutes


def tune_momentum(ev, truth):  # try every momentum setting
    rows = []  # results
    for window in (3, 5, 8):  # minutes averaged
        for pass_w in (1.0, 0.5, 0.25):  # weight of a final-third pass
            m = momentum_table(ev, window, pass_w).merge(truth, on=["match_id", "elapsed_minute"])  # next to the truth
            r = m.momentum.corr(m.regime)  # correlation with the hidden regime
            for thr in (1, 2, 3, 4, 5, 6):  # threshold for calling a team dominant
                m["pred"] = np.select([m.momentum > thr, m.momentum < -thr], [1, -1], 0)  # the guess
                acc = (m.pred == m.regime).mean()  # overall accuracy
                bal = (m.pred[m.regime == 0] == 0).mean()  # balanced minutes correctly called balanced
                dom = (m.pred[m.regime != 0] == m.regime[m.regime != 0]).mean()  # dominant minutes correctly called
                lag, fast = detection_lag(m)  # how quickly switches are caught
                rows.append({"window": window, "pass_w": pass_w, "threshold": thr, "r": r, "accuracy": acc,  # settings and scores
                             "balanced_ok": bal, "dominant_ok": dom, "median_lag_min": lag, "caught_within_3min": fast})  # more scores
    return pd.DataFrame(rows).sort_values("accuracy", ascending=False)  # best first


# ---------- entropy ----------

def entropy_q(counts, q):  # entropy of pair counts for a given q
    p = counts[counts > 0] / counts.sum()  # shares of each pair
    if q == 1.0:  # q = 1 is Shannon entropy (the Tsallis formula divides by zero there)
        return -np.sum(p * np.log(p))  # Shannon formula
    return (1 - np.sum(p ** q)) / (q - 1)  # Tsallis formula


def entropy_series(ev, windows, qs):  # rolling entropy for every team, window and q, one value per completed pass
    rows = []  # results
    passes = ev[(ev.event_type == "pass") & (ev.outcome == "complete")]  # completed passes only
    for (mid, team), g in passes.groupby(["match_id", "team"]):  # each team in each match
        ids = {p: i for i, p in enumerate(sorted(set(g.player_id) | set(g.receiver_id)))}  # player -> number
        pair = np.array([ids[a] * 11 + ids[b] for a, b in zip(g.player_id, g.receiver_id)])  # each pass as a pair number
        mins = g.elapsed_minute.values  # minute of each pass
        for n in windows:  # each window size
            counts = np.zeros(121)  # how often each pair appears in the window
            for i, k in enumerate(pair):  # walk through the passes
                counts[k] += 1  # add the new pass
                if i >= n:  # window is over-full
                    counts[pair[i - n]] -= 1  # drop the oldest pass
                if i >= n - 1:  # window is full
                    for q in qs:  # each q
                        rows.append((mid, team, n, q, i, mins[i], entropy_q(counts, q)))  # store the value
    return pd.DataFrame(rows, columns=["match_id", "team", "window", "q", "pass_no", "elapsed_minute", "H"])  # as a table


def auc(score, label):  # probability a random chaotic minute scores higher than a random structured one
    ranks = pd.Series(score).rank().values  # rank all values
    pos = label == 1  # chaotic minutes
    n1, n0 = pos.sum(), (~pos).sum()  # how many of each
    return (ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)  # Mann-Whitney formula (0.5 = no signal, 1 = perfect)


def tune_entropy(ev, truth):  # try every entropy setting
    s = entropy_series(ev, windows=(40, 60, 80), qs=(0.2, 1.0, 2.0))  # all raw values
    key = ["match_id", "team", "window", "q"]  # one series per team and setting
    s = s.sort_values(key + ["pass_no"])  # in time order
    grp = s.groupby(key).H  # per series
    s["smooth"] = grp.transform(lambda x: x.rolling(10, min_periods=1).mean())  # average of the last 10 values
    s["baseline"] = s.H - grp.transform(lambda x: x.expanding().mean())  # value minus the team's own average so far
    s["baseline_smooth"] = s.smooth - grp.transform(lambda x: x.expanding().mean())  # both together
    per_min = s.groupby(key + ["elapsed_minute"]).last().reset_index()  # value at the end of each minute
    per_min = per_min.merge(truth, on=["match_id", "elapsed_minute"])  # next to the truth
    per_min["chaotic"] = np.where(per_min.team == "home", per_min.home_chaotic, per_min.away_chaotic)  # this team's hidden style
    per_min["d"] = np.where(per_min.team == "home", per_min.regime, -per_min.regime)  # this team's hidden dominance
    rows = []  # results
    for (n, q), g in per_min.groupby(["window", "q"]):  # each setting
        for variant in ("H", "smooth", "baseline", "baseline_smooth"):  # each way of reading the value
            rows.append({"window": n, "q": q, "variant": variant.replace("H", "raw"),  # settings
                         "r_chaos": g[variant].corr(g.chaotic),  # how well it tracks hidden chaos
                         "auc_chaos": auc(g[variant].values, g.chaotic.values),  # same, as a ranking score
                         "r_momentum": g[variant].corr(g.d)})  # confound check: should be near 0
    return pd.DataFrame(rows).sort_values("auc_chaos", ascending=False)  # best first


# ---------- run ----------

def main():  # command-line entry point
    ap = argparse.ArgumentParser(description="Tune momentum and entropy settings")  # parser
    ap.add_argument("--matches", type=int, default=50, help="number of matches to simulate")  # sample size
    ap.add_argument("--seed", type=int, default=123, help="seed (keep different from the demo data)")  # seed
    args = ap.parse_args()  # read options

    ev, truth = make_matches(args.matches, args.seed)  # fresh data
    pd.set_option("display.width", 200)  # wide printing
    print(f"{args.matches} matches, {len(ev)} events\n")  # sample size

    mom = tune_momentum(ev, truth)  # momentum grid
    print("MOMENTUM: top 10 settings by accuracy")  # header
    print(mom.head(10).round(3).to_string(index=False))  # best settings
    cur = mom[(mom.window == 5) & (mom.pass_w == 1.0) & (mom.threshold == 2)]  # the current settings
    print("\nCurrent settings (window 5, pass_w 1.0, threshold 2):")  # header
    print(cur.round(3).to_string(index=False))  # for comparison

    ent = tune_entropy(ev, truth)  # entropy grid
    print("\nENTROPY: all settings, best first (auc 0.5 = no signal, 1.0 = perfect; r_momentum should be near 0)")  # header
    print(ent.round(3).to_string(index=False))  # all results


if __name__ == "__main__":  # run only when executed directly
    main()  # start
