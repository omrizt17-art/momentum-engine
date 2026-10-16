"""Full evaluation of the momentum and entropy metrics against the hidden truth.

Prints every measure (r, AUC, accuracy, recall, lag, breakdowns) as tables in the terminal.
Uses the current settings from analyze_data.py, so both scripts always agree.

Usage:
  python src/evaluate.py --data data                  # evaluate the matches in the data/ folder (demo set)
  python src/evaluate.py --matches 50 --seed 123      # simulate fresh matches in memory and evaluate those
"""

import argparse  # read command-line options
import sys  # to import the other scripts
from pathlib import Path  # file paths

import numpy as np  # math
import pandas as pd  # tables

sys.path.insert(0, str(Path(__file__).parent))  # let us import files from src/
from analyze_data import MOM_WINDOW, PASS_W, MOM_THRESHOLD, N_PASSES, Q, tsallis_max  # current settings (single source of truth)
from generate_matches import simulate_match  # the simulator, for fresh matches
from tune_metrics import momentum_table, entropy_series, detection_lag, auc, entropy_q  # shared metric helpers


# ---------- loading ----------

def load_folder(folder):  # read matches written by generate_matches.py
    d = Path(folder)  # folder path
    ev = pd.read_csv(d / "events.csv")  # events
    truth = pd.read_csv(d / "truth_regimes.csv")  # hidden regime and chaos per minute
    styles = pd.read_csv(d / "truth_team_styles.csv")  # hidden passing style per team
    return ev, truth, styles, f"folder '{folder}', {ev.match_id.nunique()} matches"  # tables + a label


def load_simulated(n, seed):  # simulate fresh matches in memory
    rng = np.random.default_rng(seed)  # random generator
    ev, truth, styles = [], [], []  # collectors
    for m in range(1, n + 1):  # each match
        e, reg, _, _, _, sty = simulate_match(m, rng)  # events, hidden regime, hidden styles
        ev += e; truth += reg; styles += sty  # collect
    return pd.DataFrame(ev), pd.DataFrame(truth), pd.DataFrame(styles), f"{n} simulated matches, seed {seed}"  # tables + label


# ---------- helpers ----------

def run_lengths(values):  # how many minutes each value has lasted so far (1, 2, 3, ... resets on change)
    out, count, prev = [], 0, None  # outputs and counters
    for v in values:  # walk through the minutes
        count = count + 1 if v == prev else 1  # same as before: keep counting; changed: restart at 1
        prev = v  # remember the current value
        out.append(count)  # store
    return out  # list of run lengths


def verdict_r(r):  # label for a correlation
    a = abs(r)  # strength regardless of sign
    return "strong" if a >= 0.5 else "moderate" if a >= 0.3 else "weak" if a >= 0.1 else "none"  # rough guide


def verdict_auc(a):  # label for an AUC
    return "excellent" if a >= 0.9 else "good" if a >= 0.8 else "fair" if a >= 0.7 else "poor" if a >= 0.55 else "no signal / wrong way"  # rough guide


def table(title, rows):  # print a titled table
    print(f"\n{title}")  # title line
    print("-" * len(title))  # underline
    print(pd.DataFrame(rows).to_string(index=False))  # the table


# ---------- evaluation ----------

def evaluate(ev, truth, styles):  # compute and print everything
    ev = ev.copy()  # don't modify the caller's table
    ev["elapsed_minute"] = (ev.timestamp_s // 60).astype(int)  # join key with the truth

    # 1. Momentum vs hidden regime
    m = momentum_table(ev, MOM_WINDOW, PASS_W).merge(truth, on=["match_id", "elapsed_minute"])  # momentum next to the truth
    m["pred"] = np.select([m.momentum > MOM_THRESHOLD, m.momentum < -MOM_THRESHOLD], [1, -1], 0)  # threshold guess
    r = m.momentum.corr(m.regime)  # correlation
    auc_home = auc(m.momentum.values, (m.regime == 1).astype(int).values)  # separates home-dominant minutes
    auc_away = auc(-m.momentum.values, (m.regime == -1).astype(int).values)  # separates away-dominant minutes (sign flipped)
    acc = (m.pred == m.regime).mean()  # overall accuracy
    base = m.regime.value_counts(normalize=True).max()  # always guessing the most common state
    lag, within3 = detection_lag(m)  # speed of catching switches
    table("1. MOMENTUM vs hidden regime", [  # print
        {"measure": "r", "value": f"{r:.2f}", "verdict": verdict_r(r)},  # correlation
        {"measure": "AUC home dominating", "value": f"{auc_home:.2f}", "verdict": verdict_auc(auc_home)},  # AUC home
        {"measure": "AUC away dominating", "value": f"{auc_away:.2f}", "verdict": verdict_auc(auc_away)},  # AUC away
        {"measure": "accuracy", "value": f"{acc:.0%}", "verdict": f"baseline {base:.0%}"},  # accuracy vs baseline
        {"measure": "recall balanced", "value": f"{(m.pred[m.regime == 0] == 0).mean():.0%}", "verdict": ""},  # per-state accuracy
        {"measure": "recall home dominating", "value": f"{(m.pred[m.regime == 1] == 1).mean():.0%}", "verdict": ""},  # per-state accuracy
        {"measure": "recall away dominating", "value": f"{(m.pred[m.regime == -1] == -1).mean():.0%}", "verdict": ""},  # per-state accuracy
        {"measure": "median lag (min)", "value": f"{lag:.0f}", "verdict": ""},  # typical delay
        {"measure": "caught within 3 min", "value": f"{within3:.0%}", "verdict": ""},  # share caught fast
    ])

    cm = pd.crosstab(m.regime.map({-1: "away", 0: "balanced", 1: "home"}), m.pred.map({-1: "away", 0: "balanced", 1: "home"}), normalize="index")  # confusion matrix
    print("\n1a. Momentum confusion matrix (rows = truth, columns = guess, share of row)")  # title
    print((cm * 100).round(0).astype(int).astype(str).add("%").to_string())  # as percentages

    # 2. Rolling entropy vs hidden chaos
    s = entropy_series(ev, windows=(N_PASSES,), qs=(Q,))  # rolling entropy, current settings
    s["H"] = s.H / tsallis_max(N_PASSES, Q)  # scale to 0-1
    pm = s.sort_values(["match_id", "team", "pass_no"]).groupby(["match_id", "team", "elapsed_minute"]).last().reset_index()  # one value per team-minute
    pm = pm.merge(truth, on=["match_id", "elapsed_minute"])  # next to the truth
    pm["chaotic"] = np.where(pm.team == "home", pm.home_chaotic, pm.away_chaotic)  # this team's hidden style
    pm["dom"] = np.where(pm.team == "home", pm.regime, -pm.regime)  # this team's hidden dominance
    r_e = pm.H.corr(pm.chaotic)  # correlation
    a_e = auc(pm.H.values, pm.chaotic.values)  # AUC
    table("2. ROLLING ENTROPY vs hidden chaotic play", [  # print
        {"measure": "r", "value": f"{r_e:.2f}", "verdict": verdict_r(r_e)},  # correlation
        {"measure": "AUC", "value": f"{a_e:.2f}", "verdict": verdict_auc(a_e)},  # AUC
    ])

    t = truth.sort_values(["match_id", "elapsed_minute"]).copy()  # truth in time order
    for side in ("home", "away"):  # each team
        t[f"{side}_run"] = t.groupby("match_id")[f"{side}_chaotic"].transform(lambda x: pd.Series(run_lengths(x.values), index=x.index))  # minutes in current style
    pm = pm.merge(t[["match_id", "elapsed_minute", "home_run", "away_run"]], on=["match_id", "elapsed_minute"])  # add to entropy table
    pm["run"] = np.where(pm.team == "home", pm.home_run, pm.away_run)  # this team's run length
    rows = []  # breakdown rows
    for label, lo, hi in (("first 4 min", 1, 4), ("4-8 min", 4, 8), ("8+ min", 8, 10_000)):  # time since style switched
        g = pm[(pm.run >= lo) & (pm.run < hi)]  # minutes in that range
        a = auc(g.H.values, g.chaotic.values)  # AUC there
        rows.append({"breakdown": "time since style switched", "group": label, "AUC": f"{a:.2f}", "verdict": verdict_auc(a)})  # store
    pm = pm.merge(styles[["match_id", "team", "style_alpha"]], on=["match_id", "team"])  # add hidden passing style
    for label, lo, hi in (("concentrated (alpha<1)", 0, 1), ("medium (alpha 1-2)", 1, 2), ("already even (alpha>2)", 2, 99)):  # team style groups
        g = pm[(pm.style_alpha >= lo) & (pm.style_alpha < hi)]  # teams in that group
        if g.chaotic.nunique() == 2:  # need both chaotic and structured minutes to compute AUC
            a = auc(g.H.values, g.chaotic.values)  # AUC there
            rows.append({"breakdown": "team's normal style", "group": label, "AUC": f"{a:.2f}", "verdict": verdict_auc(a)})  # store
    table("2a. Entropy AUC broken down", rows)  # print

    stable = pm[pm.run >= 8]  # minutes well inside a spell
    table("2b. Entropy signal vs noise (stable spells, 0-1 scale)", [  # print
        {"measure": "gap: chaotic avg - structured avg", "value": f"{stable[stable.chaotic == 1].H.mean() - stable[stable.chaotic == 0].H.mean():.2f}"},  # signal
        {"measure": "spread within structured (sd)", "value": f"{stable[stable.chaotic == 0].H.std():.2f}"},  # noise
        {"measure": "spread within chaotic (sd)", "value": f"{stable[stable.chaotic == 1].H.std():.2f}"},  # noise
    ])

    # 3. Whole-match entropy vs hidden passing style
    done = ev[(ev.event_type == "pass") & (ev.outcome == "complete")]  # completed passes
    rows = []  # one row per team per match
    for (mid, team), g in done.groupby(["match_id", "team"]):  # each team
        c = g.groupby(["player_id", "receiver_id"]).size().values.astype(float)  # pair counts over the whole match
        alpha = styles[(styles.match_id == mid) & (styles.team == team)].style_alpha.iloc[0]  # hidden style
        rows.append((entropy_q(c, Q), alpha))  # store
    w = np.array(rows)  # as an array
    r_w = np.corrcoef(w[:, 0], w[:, 1])[0, 1]  # correlation
    table("3. WHOLE-MATCH ENTROPY vs hidden passing style", [  # print
        {"measure": "r", "value": f"{r_w:.2f}", "verdict": verdict_r(r_w), "n teams": len(w)},  # result and sample size
    ])

    # 4. Overlap between the two signals
    r_o = pm.H.corr(pm.dom)  # entropy vs this team's dominance
    table("4. OVERLAP: entropy vs dominance (ideal = 0)", [  # print
        {"measure": "r", "value": f"{r_o:.2f}", "verdict": "small overlap" if abs(r_o) < 0.3 else "large overlap"},  # result
    ])


def main():  # command-line entry point
    ap = argparse.ArgumentParser(description="Evaluate momentum and entropy against the hidden truth")  # parser
    ap.add_argument("--data", help="folder made by generate_matches.py (e.g. data)")  # option 1: read a folder
    ap.add_argument("--matches", type=int, default=50, help="number of matches to simulate if --data is not given")  # option 2: simulate
    ap.add_argument("--seed", type=int, default=123, help="seed for simulated matches")  # seed
    args = ap.parse_args()  # read options
    pd.set_option("display.width", 200)  # wide printing

    ev, truth, styles, label = load_folder(args.data) if args.data else load_simulated(args.matches, args.seed)  # get the data
    print(f"EVALUATION: {label}")  # header
    print(f"Settings: momentum window {MOM_WINDOW} min, pass weight {PASS_W}, threshold ±{MOM_THRESHOLD}; entropy last {N_PASSES} passes, q = {Q}")  # settings used
    evaluate(ev, truth, styles)  # run everything


if __name__ == "__main__":  # run only when executed directly
    main()  # start
