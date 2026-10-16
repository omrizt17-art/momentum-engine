"""Analyse the synthetic match data produced by generate_matches.py.

Outputs (in the figures/ folder):
  summary.csv                  one row per match per team: score, shots, xG, passes, completion, entropy
  timeline_match<N>.png        momentum and pass entropy over one match, against the hidden ground truth
  validation.png               how well momentum and entropy recover the hidden states, across all matches
  network_match<N>_<team>.png  who passes to whom (the matrix the entropy is computed on)

Usage:  python analyze_data.py --data data --match 1
"""

import argparse  # read command-line options
from pathlib import Path  # build file paths safely

import matplotlib  # plotting library
matplotlib.use("Agg")  # draw to files, no window needed
import matplotlib.pyplot as plt  # plotting interface
import numpy as np  # math
import pandas as pd  # tables

HOME, AWAY = "#2a78d6", "#eb6834"  # team colours (blue = home, orange = away)
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"  # text, secondary text and gridline colours
Q = 0.2  # Tsallis entropy index used by Neuman et al. (2018): amplifies rare passes
N_PASSES = 40  # rolling entropy window: each team's last 40 completed passes
MOM_WINDOW = 3  # rolling momentum window in minutes (tuned: 3 beats 5, catches switches ~2 min faster)
PASS_W = 0.5  # points for a completed final-third pass (tuned: 1.0 overweighted long possession spells)
MOM_THRESHOLD = 5  # momentum above +5 / below -5 = a team is dominating (tuned: 2 was too low)

plt.rcParams.update({  # one consistent, quiet style for every figure
    "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,  # small text, light frame
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlecolor": INK,  # secondary-coloured ticks
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,  # light gridlines
    "axes.spines.top": False, "axes.spines.right": False,  # no box around plots
    "figure.dpi": 130, "savefig.bbox": "tight",  # sharp output, no wasted margin
})


# ---------- metrics (these functions will be reused by the live engine) ----------

def tsallis(counts, q=Q):  # Tsallis entropy of a set of pass counts
    p = np.asarray(counts, dtype=float)  # counts as an array
    p = p[p > 0] / p.sum()  # keep non-zero pairs and turn counts into probabilities
    return (1 - np.sum(p ** q)) / (q - 1)  # Tsallis formula: S_q = (1 - sum p^q) / (q - 1)


def tsallis_max(n_cells, q=Q):  # entropy of a perfectly uniform distribution over n_cells
    return (1 - n_cells ** (1 - q)) / (q - 1)  # used to scale entropy to 0..1


MAX_H = tsallis_max(11 * 10)  # whole match: 11 passers x 10 possible receivers = 110 possible pass pairs
MAX_H_WINDOW = tsallis_max(N_PASSES)  # rolling window: 40 passes can fill at most 40 different pairs


def momentum_per_minute(ev):  # rolling momentum for one match, one value per elapsed minute
    ft_pass = (ev.event_type == "pass") & (ev.outcome == "complete") & (ev.end_x >= 66)  # completed pass into the final third
    high_win = ev.event_type.isin(["interception", "tackle"]) & (ev.outcome == "won") & (ev.x >= 50)  # ball won in the opponent's half
    shot = ev.event_type == "shot"  # any shot
    points = ft_pass * PASS_W + high_win * 2 + shot * 3  # weight each threat signal
    sign = np.where(ev.team == "home", 1, -1)  # home threat counts up, away threat counts down
    minute = (ev.timestamp_s // 60).astype(int)  # elapsed minute of each event
    raw = pd.Series(points * sign).groupby(minute).sum()  # net threat per minute
    raw = raw.reindex(range(minute.max() + 1), fill_value=0)  # include minutes with no threat events
    return raw.rolling(MOM_WINDOW, min_periods=1).mean()  # smooth over the last MOM_WINDOW minutes


def rolling_entropy(ev, team):  # normalised pass entropy of one team, updated every completed pass
    passes = ev[(ev.team == team) & (ev.event_type == "pass") & (ev.outcome == "complete")]  # that team's completed passes
    pairs = list(zip(passes.player_id, passes.receiver_id))  # (passer, receiver) for each pass
    times, values = [], []  # outputs
    for i in range(N_PASSES, len(pairs) + 1):  # slide a 40-pass window along the match
        counts = pd.Series(pairs[i - N_PASSES:i]).value_counts().values  # how often each pair occurs in the window
        times.append(passes.timestamp_s.iloc[i - 1] / 60)  # time of the window's last pass, in minutes
        values.append(tsallis(counts) / MAX_H_WINDOW)  # entropy scaled to 0..1 (1 = every pass went to a different pair)
    return pd.Series(values, index=times)  # entropy indexed by elapsed minute


# ---------- 1. summary table ----------

def summarise(ev, matches, styles):  # one row per match per team (styles = hidden truth, for comparison only)
    rows = []  # collector
    for (mid, team), g in ev.groupby(["match_id", "team"]):  # every team in every match
        passes = g[g.event_type == "pass"]  # all passes
        done = passes[passes.outcome == "complete"]  # completed passes
        shots = g[g.event_type == "shot"]  # shots
        pairs = done.groupby(["player_id", "receiver_id"]).size().values  # whole-match pass matrix
        m = matches[matches.match_id == mid].iloc[0]  # match info
        rows.append({  # the summary row
            "match_id": mid, "team": team, "club": g.team_name.iloc[0],  # who
            "goals": m[f"{team}_score"], "shots": len(shots), "xg": round(shots.xg.sum(), 2),  # attacking output
            "passes": len(passes), "completion_%": round(100 * len(done) / len(passes), 1),  # passing volume and accuracy
            "high_regains": int(((g.event_type.isin(["interception", "tackle"])) & (g.outcome == "won") & (g.x >= 50)).sum()),  # pressing success
            "entropy_0to1": round(tsallis(pairs) / MAX_H, 3),  # whole-match pass entropy, normalised
            "style_alpha_hidden": styles[(styles.match_id == mid) & (styles.team == team)].style_alpha.iloc[0],  # hidden passing style (low = concentrated)
        })
    return pd.DataFrame(rows)  # as a table


# ---------- 2. one-match timeline ----------

def shade(ax, minutes, values, colours, alpha=0.12):  # shade background spans where a hidden state holds
    start = minutes[0]  # beginning of the current span
    for i in range(1, len(minutes) + 1):  # walk through the minutes
        if i == len(minutes) or values[i] != values[i - 1]:  # the state changes (or the match ends)
            c = colours.get(values[i - 1])  # colour for that state (None = no shading)
            if c:  # only shade states that have a colour
                ax.axvspan(start, minutes[i - 1] + 1, color=c, alpha=alpha, lw=0)  # draw the span
            if i < len(minutes):  # start the next span
                start = minutes[i]  # new span begins here


def plot_timeline(ev, truth, mid, out):  # momentum and entropy over one match vs hidden truth
    e = ev[ev.match_id == mid]  # this match's events
    t = truth[truth.match_id == mid].sort_values("elapsed_minute")  # this match's hidden states
    home, away = e[e.team == "home"].team_name.iloc[0], e[e.team == "away"].team_name.iloc[0]  # club names
    mins = t.elapsed_minute.values  # minute axis for the shading
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True, gridspec_kw={"height_ratios": [1.2, 1]})  # two stacked panels

    mom = momentum_per_minute(e)  # rolling momentum
    shade(a1, mins, t.regime.values, {1: HOME, -1: AWAY})  # hidden regime as background colour
    a1.plot(mom.index, mom.values, color=INK, lw=2)  # momentum line
    a1.axhline(0, color=MUTED, lw=1)  # zero = balanced
    for _, g in e[(e.event_type == "shot") & (e.outcome == "goal")].iterrows():  # every goal
        y = mom.max() * 1.05 if g.team == "home" else mom.min() * 1.05  # place the marker near the top (home) or bottom (away)
        a1.scatter(g.timestamp_s / 60, y, s=60, color=HOME if g.team == "home" else AWAY, zorder=3, edgecolor="white", lw=1.5)  # goal marker
        a1.annotate(g.player_name.split()[-1], (g.timestamp_s / 60, y), xytext=(6, -3), textcoords="offset points", fontsize=8, color=MUTED)  # scorer's surname
    a1.set_ylabel("Momentum (home − away)")  # y-axis meaning
    a1.set_title(f"Match {mid}: {home} {e.home_score.iloc[-1]}–{e.away_score.iloc[-1]} {away}  ·  shading = hidden dominant team", loc="left", fontsize=11)  # headline

    for team, col in (("home", HOME), ("away", AWAY)):  # one entropy line per team
        h = rolling_entropy(e, team)  # rolling entropy
        a2.plot(h.index, h.values, color=col, lw=2, label=f"{home if team == 'home' else away}")  # entropy line
        chaotic = t[f"{team}_chaotic"].values  # hidden chaotic minutes for this team
        y0 = 0.05 if team == "home" else 0.0  # strip position at the bottom of the panel
        for m, c in zip(mins, chaotic):  # mark chaotic minutes as a thin strip
            if c:  # only when the team was chaotic
                a2.add_patch(plt.Rectangle((m, y0), 1, 0.04, transform=a2.get_xaxis_transform(), color=col, alpha=0.6, lw=0))  # strip block
    a2.set_ylabel(f"Pass entropy (last {N_PASSES} passes, 0–1)")  # y-axis meaning
    a2.set_xlabel("Elapsed minute")  # x-axis meaning
    a2.legend(loc="lower right", bbox_to_anchor=(1, 1), frameon=False, ncol=2, fontsize=9)  # which line is which team, above the panel so it never covers a line
    a2.set_title("Coloured strips at the bottom = minutes the team was secretly playing chaotically", loc="left", fontsize=9, color=MUTED)  # explain the strips
    fig.savefig(out / f"timeline_match{mid}.png")  # save
    plt.close(fig)  # free memory


# ---------- 3. validation across all matches ----------

def validate(ev, truth, out):  # do the metrics recover the hidden states?
    mom_rows, ent_rows = [], []  # collectors
    for mid, e in ev.groupby("match_id"):  # each match
        mom = momentum_per_minute(e)  # rolling momentum
        mom_rows.append(pd.DataFrame({"match_id": mid, "elapsed_minute": mom.index, "momentum": mom.values}))  # keep per minute
        for team in ("home", "away"):  # each team
            h = rolling_entropy(e, team)  # rolling entropy
            ent_rows.append(pd.DataFrame({"match_id": mid, "team": team, "elapsed_minute": np.floor(h.index).astype(int), "entropy": h.values}))  # keep per pass
    m = pd.concat(mom_rows).merge(truth, on=["match_id", "elapsed_minute"])  # momentum next to hidden regime
    en = pd.concat(ent_rows).merge(truth, on=["match_id", "elapsed_minute"])  # entropy next to hidden style
    en["chaotic"] = np.where(en.team == "home", en.home_chaotic, en.away_chaotic)  # the hidden style of that team
    en = en.groupby(["match_id", "team", "elapsed_minute"], as_index=False).agg(entropy=("entropy", "mean"), chaotic=("chaotic", "first"))  # one value per team-minute

    r_mom = m.momentum.corr(m.regime)  # correlation momentum vs hidden regime
    r_ent = en.entropy.corr(en.chaotic)  # correlation entropy vs hidden chaos
    m["predicted"] = np.select([m.momentum > MOM_THRESHOLD, m.momentum < -MOM_THRESHOLD], [1, -1], 0)  # threshold classifier (tuned with tune_metrics.py)
    acc = (m.predicted == m.regime).mean()  # share of minutes where it matches the truth

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))  # two panels side by side
    groups = [(-1, "Away dominant", AWAY), (0, "Balanced", MUTED), (1, "Home dominant", HOME)]  # regime order and colours
    a1.boxplot([m.momentum[m.regime == r] for r, _, _ in groups], widths=0.5, showfliers=False,  # momentum distribution per hidden regime
               medianprops={"color": INK, "lw": 2}, boxprops={"color": MUTED}, whiskerprops={"color": MUTED}, capprops={"color": MUTED})  # quiet boxes, bold median
    a1.set_xticks([1, 2, 3], [lbl for _, lbl, _ in groups])  # regime names on the x-axis
    a1.set_ylabel("Momentum (home − away)")  # y-axis meaning
    a1.set_title(f"Momentum vs hidden regime  ·  r = {r_mom:.2f}, threshold accuracy {acc:.0%}", loc="left", fontsize=10)  # result

    a2.boxplot([en.entropy[en.chaotic == 0], en.entropy[en.chaotic == 1]], widths=0.5, showfliers=False,  # entropy per hidden style
               medianprops={"color": INK, "lw": 2}, boxprops={"color": MUTED}, whiskerprops={"color": MUTED}, capprops={"color": MUTED})  # same style
    a2.set_xticks([1, 2], ["Structured", "Chaotic"])  # style names
    a2.set_ylabel("Pass entropy (0–1)")  # y-axis meaning
    a2.set_title(f"Entropy vs hidden play style  ·  r = {r_ent:.2f}", loc="left", fontsize=10)  # result
    fig.savefig(out / "validation.png")  # save
    plt.close(fig)  # free memory
    return r_mom, acc, r_ent  # numbers for the console


# ---------- 4. passing network ----------

def plot_network(ev, players, mid, team, out):  # who passes to whom, as a heatmap
    e = ev[(ev.match_id == mid) & (ev.team == team) & (ev.event_type == "pass") & (ev.outcome == "complete")]  # completed passes
    squad = players[(players.match_id == mid) & (players.team == team)].sort_values("player_id")  # squad in shirt order
    ids = squad.player_id.tolist()  # player ids
    labels = [f"{n.split()[-1]} ({p})" for n, p in zip(squad.player_name, squad.position)]  # "Surname (POS)"
    mat = pd.crosstab(e.player_id, e.receiver_id).reindex(index=ids, columns=ids, fill_value=0)  # 11 x 11 pass matrix
    fig, ax = plt.subplots(figsize=(7.5, 6.5))  # square-ish figure
    im = ax.imshow(mat.values, cmap="Blues")  # single-hue sequential colour: darker = more passes
    ax.set_xticks(range(len(ids)), labels, rotation=60, ha="right", fontsize=8)  # receivers along the bottom
    ax.set_yticks(range(len(ids)), labels, fontsize=8)  # passers down the side
    ax.set_xlabel("Receiver")  # axis meaning
    ax.set_ylabel("Passer")  # axis meaning
    ax.grid(False)  # no gridlines on a heatmap
    fig.colorbar(im, ax=ax, shrink=0.8, label="Completed passes")  # colour scale
    ax.set_title(f"Match {mid} · {e.team_name.iloc[0]} passing network · entropy {tsallis(mat.values.ravel()) / MAX_H:.2f}", loc="left", fontsize=10)  # headline
    fig.savefig(out / f"network_match{mid}_{team}.png")  # save
    plt.close(fig)  # free memory


# ---------- run everything ----------

def main():  # command-line entry point
    ap = argparse.ArgumentParser(description="Analyse synthetic match data")  # argument parser
    ap.add_argument("--data", default="data", help="folder made by generate_matches.py")  # input folder
    ap.add_argument("--match", type=int, default=1, help="match to plot in detail")  # match for the timeline and network
    ap.add_argument("--out", default="figures", help="output folder")  # output folder
    args = ap.parse_args()  # read the options

    d, out = Path(args.data), Path(args.out)  # paths
    out.mkdir(parents=True, exist_ok=True)  # create the output folder
    ev = pd.read_csv(d / "events.csv")  # event stream
    matches = pd.read_csv(d / "matches.csv")  # match info
    players = pd.read_csv(d / "players.csv")  # squads
    truth = pd.read_csv(d / "truth_regimes.csv")  # hidden states (validation only)
    styles = pd.read_csv(d / "truth_team_styles.csv")  # hidden passing styles (validation only)

    summary = summarise(ev, matches, styles)  # per team per match
    summary.to_csv(out / "summary.csv", index=False)  # save it
    print(summary.drop(columns=["team"]).to_string(index=False))  # show it

    plot_timeline(ev, truth, args.match, out)  # one match in detail
    for team in ("home", "away"):  # both teams' passing networks
        plot_network(ev, players, args.match, team, out)  # heatmap
    r_mom, acc, r_ent = validate(ev, truth, out)  # check against the hidden truth

    print(f"\nMomentum vs hidden regime: r = {r_mom:.2f}, threshold accuracy = {acc:.0%}")  # headline result 1
    print(f"Entropy vs hidden chaos:   r = {r_ent:.2f}")  # headline result 2
    print(f"Whole-match entropy vs hidden style alpha: r = {summary.entropy_0to1.corr(summary.style_alpha_hidden):.2f}")  # does style show in entropy
    print(f"\nFigures saved in {out}/")  # where to look


if __name__ == "__main__":  # run only when executed directly
    main()  # start
