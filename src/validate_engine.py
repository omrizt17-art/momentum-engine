"""Grade the live engine (metrics.py) against the hidden truth, on freshly simulated matches.

Only this file (and tune_metrics.py) may read truth. The engine itself sees events only.

For each seed it simulates a batch of matches, streams every match through Engine, and compares the engine's
per-minute output with the hidden states. Results are printed as mean +- sd across seeds.

Usage:  python src/validate_engine.py --seeds 5 --matches 20
"""

import argparse  # read command-line options
import sys  # to find sibling modules
from pathlib import Path  # file paths

import numpy as np  # math
import pandas as pd  # tables

sys.path.insert(0, str(Path(__file__).parent))  # let us import files from src/
from generate_matches import simulate_match  # the match simulator
from metrics import Engine  # the engine under test
from tune_metrics import auc, entropy_series  # AUC helper and the old hard-window entropy, for comparison


def run_batch(n, seed, **engine_kwargs):  # simulate n matches and stream them through the engine
    rng = np.random.default_rng(seed)  # random generator
    ev, truth, mins, moments = [], [], [], []  # collectors
    for m in range(1, n + 1):  # each match
        e, reg, _, _, _, _ = simulate_match(m, rng)  # events and hidden regime log
        ev += e  # keep events
        truth += reg  # keep hidden states
        eng = Engine(**engine_kwargs)  # fresh engine per match
        last = None  # minute of the previous event
        for row in e:  # stream the events in order
            moments += eng.update(row)  # feed one event
            minute = int(row["timestamp_s"] // 60)  # elapsed minute of this event
            if last is not None and minute != last:  # a minute just ended: record the state it ended with
                mins.append((m, last, eng._prev))  # the snapshot taken before this event
            eng._prev = eng.snapshot()  # state after this event
            last = minute  # remember the minute
        mins.append((m, last, eng._prev))  # last minute of the match
    rows = []  # one row per match-minute and team
    for m, minute, s in mins:  # flatten the snapshots
        for team in ("home", "away"):  # both teams
            sign = 1 if team == "home" else -1  # home view of momentum
            rows.append({"match_id": m, "elapsed_minute": minute, "team": team, "momentum": s["momentum"] * sign, "regime_pred": s["regime"] * sign,
                         "entropy": s[team]["entropy"], "entropy_rel": s[team]["entropy_rel"], "chaos": s[team]["chaos"]})
    return pd.DataFrame(ev), pd.DataFrame(truth), pd.DataFrame(rows), pd.DataFrame(moments)  # all tables


def lag_stats(df, cap=10):  # how fast does the hysteresis state catch a real switch into dominance?
    lags = []  # collector
    for _, g in df.groupby("match_id"):  # each match
        reg, pred = g.regime.values, g.regime_pred.values  # truth and guess (home view), minute by minute
        for i in range(1, len(reg)):  # walk through the minutes
            if reg[i] != reg[i - 1] and reg[i] != 0 and (reg[i:i + 5] == reg[i]).all():  # real switch into a dominant state lasting 5+ min
                hit = np.where(pred[i:i + cap] == reg[i])[0]  # minutes where the guess matches
                lags.append(hit[0] if len(hit) else cap)  # first match, or cap if never
    lags = np.array(lags)  # as an array
    return float(np.median(lags)), float((lags <= 3).mean())  # median lag, share caught within 3 minutes


def moment_stats(moments, truth, window=5):  # do momentum_shift moments line up with real switches?
    sh = moments[moments.type == "momentum_shift"] if len(moments) else moments  # shift moments only
    good, total, caught, real = 0, 0, 0, 0  # counters
    for mid, t in truth.groupby("match_id"):  # each match
        reg = t.sort_values("elapsed_minute").regime.values  # hidden regime per minute
        mm = sh[sh.match_id == mid] if len(sh) else sh  # this match's shift moments
        for _, r in mm.iterrows():  # precision: a shift toward X is "right" if X was dominant at some point in the last `window` minutes
            want = 1 if r.team == "home" else -1  # regime that should hold
            lo = max(0, r.elapsed_minute - window)  # window start
            total += 1  # one moment judged
            good += int((reg[lo:r.elapsed_minute + 1] == want).any())  # right if truth agreed
        for i in range(1, len(reg)):  # recall: real switches into dominance lasting 5+ minutes
            if reg[i] != reg[i - 1] and reg[i] != 0 and (reg[i:i + 5] == reg[i]).all():  # such a switch
                real += 1  # one real switch
                want = "home" if reg[i] == 1 else "away"  # who became dominant
                caught += int(len(mm) > 0 and ((mm.team == want) & (mm.elapsed_minute >= i) & (mm.elapsed_minute <= i + window)).any())  # a moment within `window` minutes
    return good / max(total, 1), caught / max(real, 1), total / truth.match_id.nunique()  # precision, recall, shifts per match


def predictive(ev, mins_home, horizon=3):  # does momentum at minute t predict the shot difference in the next minutes?
    sh = ev[ev.event_type == "shot"].copy()  # shots
    sh["elapsed_minute"] = (sh.timestamp_s // 60).astype(int)  # minute of each shot
    sh["s"] = np.where(sh.team == "home", 1, -1)  # home view
    per = sh.groupby(["match_id", "elapsed_minute"]).s.sum()  # net shots per minute
    res = []  # collector
    for mid, g in mins_home.groupby("match_id"):  # each match
        p = per[mid] if mid in per.index.get_level_values(0) else pd.Series(dtype=float)  # this match's shot series
        p = p.reindex(range(int(g.elapsed_minute.max()) + 1 + horizon), fill_value=0)  # include quiet minutes
        past = p.rolling(horizon, min_periods=1).sum()  # shot difference in the last `horizon` minutes
        fut = p[::-1].rolling(horizon, min_periods=1).sum()[::-1].shift(-1)  # shot difference in the next `horizon` minutes
        t = g.elapsed_minute.values  # minutes
        res.append(pd.DataFrame({"mom": g.momentum.values, "past": past.reindex(t).values, "fut": fut.reindex(t).values}))  # one row per minute
    r = pd.concat(res).dropna()  # all matches
    return r.mom.corr(r.fut), r.past.corr(r.fut)  # momentum vs naive "recent shots" baseline


def one_seed(n, seed, **kw):  # all scores for one batch
    ev, truth, mins, moments = run_batch(n, seed, **kw)  # simulate and stream
    ev["elapsed_minute"] = (ev.timestamp_s // 60).astype(int)  # join key
    t = truth.copy()  # truth with a home-view regime
    m = mins.merge(t, on=["match_id", "elapsed_minute"])  # engine output next to the truth
    m["d"] = np.where(m.team == "home", m.regime, -m.regime)  # this team's hidden dominance
    m["chaotic"] = np.where(m.team == "home", m.home_chaotic, m.away_chaotic)  # this team's hidden style
    home = m[m.team == "home"].copy()  # one row per match-minute, home view
    home["regime_pred"] = home.regime_pred  # hysteresis state, home view
    out = {}  # scores
    out["momentum_r"] = home.momentum.corr(home.regime)  # continuous momentum vs hidden regime
    out["state_acc"] = (home.regime_pred == home.regime).mean()  # hysteresis state vs hidden regime
    out["lag_median_min"], out["caught_3min"] = lag_stats(home)  # speed of detection
    out["shift_precision"], out["shift_recall"], out["shifts_per_match"] = moment_stats(moments, truth)  # key moments
    out["pred_mom_r"], out["pred_recent_shots_r"] = predictive(ev, home)  # predictive validity
    warm = m[m.entropy > 0]  # minutes after the warm-up (engine outputs are 0 before it)
    out["auc_entropy"] = auc(warm.entropy.values, warm.chaotic.values)  # decay-window entropy
    out["auc_entropy_rel"] = auc(warm.entropy_rel.values, warm.chaotic.values)  # entropy vs own baseline
    out["auc_chaos_index"] = auc(warm.chaos.values, warm.chaotic.values)  # composite chaos index
    out["r_chaos_vs_dominance"] = warm.chaos.corr(warm.d)  # confound check: should stay near 0
    old = entropy_series(ev, windows=(40,), qs=(0.2,))  # the old hard 40-pass window
    old = old.groupby(["match_id", "team", "elapsed_minute"]).H.last().reset_index().merge(truth, on=["match_id", "elapsed_minute"])  # per minute
    old["chaotic"] = np.where(old.team == "home", old.home_chaotic, old.away_chaotic)  # hidden style
    cmp = warm.merge(old[["match_id", "team", "elapsed_minute", "H"]], on=["match_id", "team", "elapsed_minute"])  # same minutes as the engine rows, so the comparison is fair
    out["auc_old_entropy"] = auc(cmp.H.values, cmp.chaotic.values)  # old method for comparison
    out["auc_entropy_same_rows"] = auc(cmp.entropy.values, cmp.chaotic.values)  # new raw entropy on exactly those rows
    return out  # scores


def main():  # command-line entry point
    ap = argparse.ArgumentParser(description="Validate the engine against hidden truth")  # parser
    ap.add_argument("--seeds", type=int, default=5, help="number of independent batches")  # how many batches
    ap.add_argument("--matches", type=int, default=20, help="matches per batch")  # batch size
    ap.add_argument("--first-seed", type=int, default=1000, help="first seed (keep away from the tuning seed 123)")  # seed offset
    ap.add_argument("--thr-in", type=float, default=None, help="momentum threshold to enter a dominant state")  # engine override
    ap.add_argument("--thr-out", type=float, default=None, help="momentum threshold to leave it")  # engine override
    ap.add_argument("--halflife", type=float, default=None, help="momentum half-life in minutes")  # engine override
    args = ap.parse_args()  # read options
    kw = {k: v for k, v in (("thr_in", args.thr_in), ("thr_out", args.thr_out), ("halflife_min", args.halflife)) if v is not None}  # only what was set
    rows = [one_seed(args.matches, args.first_seed + i, **kw) for i in range(args.seeds)]  # one batch per seed
    df = pd.DataFrame(rows)  # scores per seed
    pd.set_option("display.width", 200)  # wide printing
    print(f"{args.seeds} seeds x {args.matches} matches (seeds {args.first_seed}..{args.first_seed + args.seeds - 1})\n")  # header
    print(pd.DataFrame({"mean": df.mean(), "sd": df.std()}).round(3).to_string())  # summary


if __name__ == "__main__":  # run only when executed directly
    main()  # start
