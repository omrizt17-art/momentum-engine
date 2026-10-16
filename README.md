# Momentum & Chaos Engine

**Every app tells you who has momentum. We tell you what kind.**

A live engine for the Premier League × Microsoft hackathon. It reads synthetic football events as they happen and
tells viewers two things, minute by minute:

1. **Momentum:** which team is on top right now, and when that flipped.
2. **What kind of momentum:** is the team dominating through *control* (organized, low-entropy passing) or through
   *chaos* (unpredictable, high-entropy passing)?

Key moments are turned into short AI-written insights, written differently for a casual fan and a data-hungry analyst.

---

## Contents

- [Status](#status)
- [Submission checklist](#submission-checklist)
- [Quick start](#quick-start)
- [Project structure](#project-structure)
- [The data](#the-data)
- [The hidden truth: what it is and how it is defined](#the-hidden-truth-what-it-is-and-how-it-is-defined)
- [The metrics](#the-metrics)
- [Results so far](#results-so-far)
- [Narration: a team of agents](#narration-a-team-of-agents-srcagentspy-srcnarratorpy-srcrecappy)
- [Azure set-up guide](docs/AZURE.md)
- [How it maps to the hackathon brief](#how-it-maps-to-the-hackathon-brief)
- [Roadmap](#roadmap)
- [Working together](#working-together)
- [Known limitations](#known-limitations)
- [References](#references)

---

## Status

| Component | File | Owner | Status |
| --- | --- | --- | --- |
| Synthetic match generator | `src/generate_matches.py` | A | ✅ Done |
| Offline analysis + validation | `src/analyze_data.py` | A | ✅ Done |
| Metric tuning and full offline evaluation | `src/tune_metrics.py`, `src/evaluate.py` | A | ✅ Done |
| Live event streamer | `src/streamer.py` | A | ✅ Done (local queue; optional Azure Event Hubs publisher) |
| Incremental metrics engine (momentum, entropy, chaos index, states, key moments) | `src/metrics.py` | A | ✅ v1 done |
| Engine validation (mean ± sd over seeds) | `src/validate_engine.py` | A | ✅ Done |
| Live session (streamer → engine → narrator in a background thread) | `src/live.py` | A + B | ✅ Done |
| AI narration: Writer (Azure OpenAI) + Verifier + template fallback; casual / analyst; en, he, es templates | `src/narrator.py` | B | ✅ Done. Templates and verifier tested; the live Azure call is not tested (no key yet) |
| Full-time recap (same Writer / Verifier / fallback pattern) | `src/recap.py` | B | ✅ Done (same caveat) |
| Terminal demo (backup if the dashboard fails) | `src/run_live.py` | B | ✅ Done |
| Multi-agent newsroom: Director, Analyst, Writer, Verifier, Fallback, circuit breaker, trace | `src/agents.py` | B | ✅ Done, tested with a scripted fake model |
| Player layer: name tags, pass distance / accuracy / difficulty, ball speed, badges, player focus | `src/players.py` | A | ✅ Done |
| Auto-eventing prototype on synthetic tracking | `src/autoevent.py` | A | ✅ Done (circular by construction, see above) |
| Dashboard (Streamlit) | `src/app.py` | B | ✅ v2 done (player tags, player focus, agent trace), tested headless; look and feel not yet reviewed in a browser |
| Azure Event Hubs transport (publisher and live feed) | `src/eventhub.py` | A | ✅ Done, tested with fake producer and consumer. Not tested against a real namespace |
| Tests | `tests/` | A + B | ✅ 65 tests: no truth leak, no lookahead, determinism, streamer pace, verifier, fallback, recap, Event Hubs (fakes), dashboard (headless), agents, players, auto-eventing |

---

## Submission checklist

Hackathon: Inside the Game (Microsoft x Premier League). Submissions close **27 October 2026**.

| Item | Status |
| --- | --- |
| Public GitHub repository | ⬜ Currently private: make it public before submitting |
| Pitch / description | ✅ Draft in [docs/PITCH.md](docs/PITCH.md): fill in the [BRACKETS] |
| Demo video under 2 minutes, public URL | ⬜ Script in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) |
| Azure set-up: OpenAI, Event Hubs, Container Apps | ⬜ Step-by-step in [docs/AZURE.md](docs/AZURE.md). Nothing tested against a real subscription yet |
| Real Azure OpenAI run (key, endpoint, deployment in `.env`) | ⬜ Not tested yet: only fakes and templates |
| Real Azure Event Hubs run | ⬜ Optional, not tested yet |
| Dashboard reviewed in a browser | ⬜ Tested headless only |
| Everything in English (code, docs, UI) | ✅ The only non-English text is the optional Hebrew and Spanish template languages (brief item vi) |

---

## Quick start

You need **Python 3.10+**. In VS Code, install the **Python** extension, then **File → Open Folder…** and open
this repo.

```bash
# 1. Create a virtual environment and install the libraries (once)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Generate 10 synthetic matches into data/
python src/generate_matches.py --matches 10 --seed 42 --out data

# 3. Analyse them: summary table + plots into figures/
python src/analyze_data.py --data data --match 1 --out figures

# 4. Replay match 1 as a live stream through the engine (30x speed; use --speed 0 for no waiting)
python src/streamer.py --data data --match 1 --speed 30 --jsonl moments.jsonl

# 5. Grade the engine against the hidden truth on fresh matches (a few minutes)
python src/validate_engine.py --seeds 4 --matches 20 --save results/engine_validation.csv

# 6. Live terminal demo with stories (no data/ folder needed with --simulate)
python src/run_live.py --simulate --seed 7 --speed 60 --mode analyst --lang he

# 7. The dashboard
streamlit run src/app.py

# 8. Tests
python -m pytest tests -q

# 9. Player layer and auto-eventing prototype
python src/autoevent.py --seed 7

# 10. Optional: the production path through Azure Event Hubs (needs the two EVENTHUB_ settings in .env)
python src/streamer.py --data data --match 1 --speed 30 --publish eventhubs   # terminal 1: publisher
python src/run_live.py --source eventhubs --match 1 --speed 0                 # terminal 2: engine and stories
```

**Narrator and Azure OpenAI.** Without settings, stories use built-in templates (English, Hebrew, Spanish) and everything
works. To use the model, copy `.env.example` to `.env` and fill in the endpoint, key and deployment name. The model's
sentence is checked against the facts (no invented numbers); if it fails twice, errors out or is slow (8 s), the template
is used.

Options:

| Script | Option | Default | Meaning |
| --- | --- | --- | --- |
| `generate_matches.py` | `--matches` | 10 | How many matches to simulate |
| | `--seed` | 42 | Random seed. Same seed = identical data, so we can both reproduce results |
| | `--out` | `data` | Output folder |
| `streamer.py` | `--data` | `data` | Folder made by the generator (only `events.csv` is read) |
| | `--match` | 1 | Which match to replay |
| | `--speed` | 30 | 30 = 30x real time, 0 = no waiting |
| | `--jsonl` | none | Also write key moments to this file, one JSON object per line |
| `run_live.py` | `--simulate` / `--seed` | off / 7 | Simulate a match in memory instead of reading `--data` |
| | `--speed` | 60 | 60 = one match minute per second, 0 = no waiting |
| | `--mode` / `--lang` | casual / en | Casual fan or analyst; language code |
| | `--club` / `--player` | none | Favourite club and player |
| `narrator.py` | `--demo` | off | Narrate sample moments in every mode and template language |
| `validate_engine.py` | `--seeds` / `--matches` | 5 / 20 | Independent batches, and matches per batch |
| | `--save` | none | Write per-seed scores and the summary to a CSV, so two people can compare runs |
| | `--first-seed` | 1000 | Keep away from the tuning seed (123) |
| `analyze_data.py` | `--data` | `data` | Folder made by the generator |
| | `--match` | 1 | Which match to plot in detail |
| | `--out` | `figures` | Output folder |

`data/` and `figures/` are not committed to git: anyone can recreate them in a few seconds with the commands above.

---

## Project structure

```
momentum-engine/
├── src/
│   ├── generate_matches.py   # simulates matches, writes data/
│   ├── analyze_data.py       # offline metrics, plots and validation, writes figures/
│   ├── tune_metrics.py       # grid search and weight fit against the hidden truth (fresh seed)
│   ├── evaluate.py           # full offline evaluation tables (offline metrics)
│   ├── metrics.py            # the live engine: momentum, entropy, chaos index, states, key moments
│   ├── streamer.py           # replays events.csv as a paced live feed
│   ├── live.py               # LiveSession: streamer -> engine -> narrator in a background thread
│   ├── eventhub.py           # Azure Event Hubs publisher and live feed
│   ├── agents.py             # the newsroom: Director, Analyst, Writer, Verifier, Fallback, circuit breaker
│   ├── players.py            # name tags, pass quality, ball speed, badges, player focus
│   ├── autoevent.py          # auto-eventing prototype on synthetic ball tracking
│   ├── narrator.py           # templates, number whitelist and verifier used by the agents
│   ├── recap.py              # full-time recap, same pattern
│   ├── run_live.py           # terminal demo
│   ├── app.py                # Streamlit dashboard
│   └── validate_engine.py    # grades the live engine against truth, mean ± sd over seeds
├── docs/                     # PITCH.md, DEMO_SCRIPT.md, AZURE.md (set-up guide)
├── Dockerfile                # dashboard image for Azure Container Apps
├── tests/                    # pytest: engine, streamer, narrator, verifier, fallback, recap, Event Hubs, dashboard
├── results/                  # saved validation numbers (engine_validation.csv)
├── .env.example              # Azure OpenAI settings (copy to .env, never commit .env)
├── docs/img/                 # example figures used in this README
├── notebooks/                # exploration only; finished code moves to src/
├── requirements.txt
└── README.md
```

---

## The data

The hackathon requires **synthetic** data, so we generate our own. Each match is simulated **event by event**: the
ball moves from player to player, gets pressed, tackled, intercepted and shot, with realistic timing. Players and
clubs are invented.

### Typical match

| Measure | Value per match (approx.) |
| --- | --- |
| Events | ~2,200 |
| Passes | ~1,400 (77% completed) |
| Shots | ~28 |
| Goals | ~3.4 |
| Length | ~97 minutes including stoppage time |

### Files written to `data/`

| File | One row per | Key columns | Can the engine read it? |
| --- | --- | --- | --- |
| `events.csv` | event | see below | ✅ Yes, this is the live feed |
| `players.csv` | player per match | `player_id`, `player_name`, `position`, `team` | ✅ Yes |
| `matches.csv` | match | clubs, final score | ✅ Yes |
| `truth_regimes.csv` | minute per match | `regime`, `label`, `home_chaotic`, `away_chaotic` | ❌ **Validation only** |
| `truth_hot_streaks.csv` | team per match | hot player, `start_minute`, `end_minute` | ❌ **Validation only** |
| `truth_team_styles.csv` | team per match | `style_alpha` | ❌ **Validation only** |

### `events.csv` columns

| Column | Meaning |
| --- | --- |
| `match_id`, `event_id` | Identifiers. `event_id` is the order events happen in |
| `period` | 1 = first half, 2 = second half |
| `minute`, `second` | Match clock (second half starts at 45:00) |
| `timestamp_s` | **Stream time:** seconds since kickoff, continuous, half-time skipped. Use this for live streaming and to join with the truth files |
| `team`, `team_name` | `home` / `away` and the club name |
| `player_id`, `player_name`, `position` | Who performed the event (GK / DEF / MID / FWD) |
| `event_type` | `pass`, `pressure`, `tackle`, `interception`, `shot` |
| `outcome` | pass: `complete` / `incomplete` · tackle: `won` / `lost` · interception: `won` · pressure: `applied` · shot: `goal` / `saved` / `off_target` / `blocked` |
| `x`, `y` | Where it happened, **in the acting team's attacking direction**: x = 0 own goal line, x = 100 opponent goal line; y = 0–100 across the pitch |
| `end_x`, `end_y` | Where a pass was aimed (passes only) |
| `receiver_id`, `receiver_name` | Intended receiver (passes only). This is what the entropy is built from |
| `xg` | Expected-goals value of a shot (shots only) |
| `under_pressure` | Whether the pass was made under a pressing action |
| `ball_speed_kmh` | Measured ball speed in km/h (passes and shots only; synthetic) |
| `home_score`, `away_score` | Score after this event |

The event logic in brief: each step, the team in possession either **shoots** (only in the final quarter of the
pitch), gets **tackled** (about 10% of steps), or **passes**, sometimes under **pressure**. A failed pass becomes an
**interception** for the other team. Who receives a pass depends on position (forwards get the ball more in the
final third) and on each team's passing habits.

---

## The hidden truth: what it is and how it is defined

### Why we need it

With real match data, nobody can tell you who *really* had momentum: that is exactly the thing we are trying to
measure. Synthetic data lets us do something real data can't: **we decide the answer in advance, hide it, and check
whether our engine finds it.**

So the generator has hidden "dials" that change how teams play. They are never written into `events.csv`; they only
**change the probabilities** of what happens. The engine sees only the events, exactly as it would with a real feed.
The dials are saved separately in the `truth_*.csv` files, and we use them only to grade the engine.

> **Rule:** the engine (`streamer.py`, `metrics.py`, `narrator.py`, `app.py`) must never read a `truth_*.csv` file.
> Only validation code may.

There are four hidden layers.

### 1. Momentum regime: who is dominating (`truth_regimes.csv` → `regime`, `label`)

**What it is:** a single match-level state with three values:

| `regime` | `label` | Meaning |
| --- | --- | --- |
| +1 | `home_dominant` | Home team is on top |
| 0 | `balanced` | Neither team is on top |
| −1 | `away_dominant` | Away team is on top |

**How it changes:**
- Every match starts `balanced`.
- Every minute of stream time, there is a **7% chance** it switches, to one of the other two states at random.
  On average a state lasts about 14 minutes.
- After a goal, there is a **40% chance** momentum swings to the team that just conceded (the "response" after
  going behind).
- In practice: about 8 switches per match, and roughly a third of minutes in each state.

**What it does to play:** `regime` is stored from the **home** team's point of view. Before every action, the
code converts it to the point of view of **the team on the ball**:

```python
d = regime if side == "home" else -regime   # +1 = team on the ball is dominant, -1 = dominated, 0 = balanced
```

| Situation | `regime` | `d` when home has the ball | `d` when away has the ball |
| --- | --- | --- | --- |
| Home dominating | +1 | +1 | −1 |
| Balanced | 0 | 0 | 0 |
| Away dominating | −1 | −1 | +1 |

So `d` changes whenever possession changes **and** whenever momentum switches. This lets each formula be written
once for both teams. `d` then shifts five probabilities (effects on the defending team, like tackles, use the same
`d` of the team on the ball):

| Effect | Formula in the code | Dominant (d = +1) | Balanced (d = 0) | Dominated (d = −1) |
| --- | --- | --- | --- | --- |
| Pass completion | base 0.85 + 0.05·d | 0.90 | 0.85 | 0.80 |
| How far forward passes go (mean, pitch %) | 5 + 4·d | 9 | 5 | 1 |
| Chance of being pressed before a pass | 0.22 − 0.08·d | 14% | 22% | 30% |
| Chance the opponent's tackle succeeds | 0.45 − 0.10·d | 35% | 45% | 55% |
| Base shot chance per action in the final quarter | 0.024 + 0.010·d (+ more closer to goal) | 3.4% | 2.4% | 1.4% |

These are **starting values**. Pressure, passes into the final third and fatigue all lower completion further, so
in the generated data (seed 42) completion is about **78% dominant / 77% balanced / 72% dominated**. The
dominant–balanced gap is small because dominant teams play more passes into the final third, which are harder.

Checked in the data: when home dominates, home completes 77.3% of passes vs away's 72.6%; when away dominates, it
flips to 78.5% for away vs 71.3% for home. The advantage follows the dominant team, not the home side.

None of these is dramatic on its own. Together, a dominant team progresses further, keeps the ball longer, wins it
back higher and shoots more, which is what momentum looks like on the pitch.

### 2. Play style: structured vs chaotic (`truth_regimes.csv` → `home_chaotic`, `away_chaotic`)

**What it is:** a separate switch **per team**: 0 = structured, 1 = chaotic.

**How it changes:**
- Both teams start structured.
- Every minute, each team independently has a **5% chance** to flip (about 20 minutes per spell on average).
- It is **independent of momentum**. That is deliberate: it means all four quadrants of our match-state map can
  occur (controlled dominance, chaotic pressure, being read, scrambling), so we can test that the engine tells
  them apart.
- In practice each team is chaotic about half the time.

**What it does to play when chaotic:**

| Effect | Structured | Chaotic |
| --- | --- | --- |
| Choice of receiver | Follows the team's passing habits (`habit` = 1.0) | Habits flattened (`habit` = 0.2): the ball goes to a wider spread of teammates |
| Spread of pass length (forward / sideways) | 9 / 15 | 13 / 20 |
| Pass completion | — | 4 points lower |

The first row is what entropy should pick up: when a team's passes are spread more evenly across passer–receiver
pairs, their passing is less predictable, so its entropy rises.

### 3. Hot streak: a player "on a roll" (`truth_hot_streaks.csv`)

**What it is:** one hidden hot streak per team per match.

**How it is defined:**
- One **outfield** player is chosen at random.
- The streak starts at a random time between **minute 10 and 75** and lasts **8–15 minutes**.

**What it does during the streak:**
- Teammates pass to this player **twice as often** (more involvement).
- Their own passes are **8 points more likely** to be completed.
- Their shots have **20% higher xG** (better chances).

This is the ground truth for the stretch-goal "player on a roll" feature.

### 4. Team passing style (`truth_team_styles.csv` → `style_alpha`)

**What it is:** each team's fixed passing habits for the whole match.

**How it is defined:** each player gets a share of the team's passing preferences, drawn from a Dirichlet
distribution with concentration `style_alpha`, which is drawn between **0.3 and 3.0**:
- **Low alpha** (e.g. 0.4): passes concentrate on a few key players (a playmaker-centric team) → **lower entropy**.
- **High alpha** (e.g. 2.8): passes spread evenly across the team → **higher entropy**.

This mirrors the between-team differences that Neuman et al. (2018) measured across a Premier League season.

### Other built-in effects (not in the truth files)

These make the data realistic but are not things we try to detect:
- **Fatigue:** after minute 55, pass completion drops by 0.15 points per minute (about 6 points by minute 95).
- **Position:** forwards receive more passes in the final third; defenders and goalkeepers more in their own third.
- **Final-third difficulty:** passes into the final third are 8 points less likely to be completed.
- **Shot quality:** xG falls with distance to goal (about 0.22 from 11 m, 0.06 from 20 m).

### Time base: important when joining files

The truth is recorded per **elapsed minute of stream time** (`timestamp_s // 60`), which runs continuously through
both halves with half-time skipped. It is **not** the `minute` column (the match clock, which restarts at 45:00).
Always join on:

```python
events["elapsed_minute"] = (events.timestamp_s // 60).astype(int)
```

---

## The metrics

These are implemented in `src/analyze_data.py` and will move into `src/metrics.py` for the live engine.

### Momentum

Each event earns "threat points" for the team that made it:

| Event | Points |
| --- | --- |
| Completed pass ending in the final third (`end_x` ≥ 66) | 1 |
| Ball won (tackle or interception) in the opponent's half (`x` ≥ 50) | 2 |
| Shot | 3 |

Momentum = (home points − away points) per minute, averaged over the **last 5 minutes**. Positive = home on top,
negative = away on top.

### Pass entropy

For each team, take its **last 40 completed passes**, count how often each passer→receiver pair occurs, and compute
the **Tsallis entropy** with q = 0.2 (the same measure as Neuman et al., 2018):

```
S_q = (1 − Σ pᵢ^q) / (q − 1)
```

where pᵢ is the share of passes going through pair i. It is scaled to 0–1 by dividing by the maximum possible value
(every pass a different pair). Low = predictable, concentrated passing; high = spread out, harder to predict.

Relative entropy (team minus opponent) is the planned "control vs chaos" axis.

### Live engine (`src/metrics.py`)

The engine reads **one event at a time** and keeps a small, fixed amount of state. It never reads a `truth_*.csv`.
The offline versions above stay in `analyze_data.py` for the plots; the live versions differ as follows.

**Momentum (live).** Threat points are *time-decayed* (half-life 1.5 minutes) instead of a hard window, so one event
updates the value in O(1). The weights come from a least-squares fit of the hidden regime (`tune_metrics.py`, seed 123):
completed pass into the final third 1.1, high regain 0.6, shot 3.0, pressing action 2.2. The fit found no use for a
penalty on lost passes (weight 0). Dominance uses **two thresholds** (hysteresis): a team becomes dominant above 6 points
per minute and stays dominant until momentum falls below 3. A `momentum_shift` moment is published only after the new
state has **held for 60 seconds**, and there is a **2-minute cooldown** between `momentum_shift` / `style_change` moments.
Together these stop the narrator being flooded with false "momentum shifted" moments.

**Pass entropy (live).** Same Tsallis entropy (q = 0.2) of passer-to-receiver pairs, but over an **exponential-decay
window** (a pass loses half its weight after 25 of the team's completed passes) instead of "the last 40 passes", so there
is no jump when an old pass leaves the window. The value is also read **against the team's own average in this match**
(`entropy_rel`), which removes the fixed difference between teams (`style_alpha`) and keeps only change within the match.
The baseline starts after 40 completed passes (about 10 minutes), so there is no chaos reading before that.

**Chaos index.** The mean of three z-scores, each against the team's own match baseline: pass entropy, spread of pass
length (`end_x - x`) and sideways spread (`end_y - y`). Higher = less predictable play. Completion rate is **not** in the
index: it also falls when a team is dominated (72% vs 78%), so it would mix up "chaotic" with "being pushed back". Spread
of pass length and direction are plausible chaos signals in real football too, not only in this generator.
Even without completion, the index was still linked to dominance (r = -0.16: a pushed-back team's passes clip at the
pitch edge, so their length spread shrinks). The engine removes this with one number: `chaos + 0.2 × (team momentum / 10)`.
On fresh matches the link drops to 0.01 and the chaos AUC does not change.

**Match state (live).** The four-cell table below, computed for each team. "More chaotic than the opponent" uses a dead
band (0.25) and can change at most once every 3 minutes, so the label is stable.

**Key moments.** Four types, as JSON for the narrator:
- `goal`
- `big_chance`: a missed shot with xG of 0.3 or more (at most one per minute)
- `momentum_shift`: a team enters the dominant state and holds it for 60 seconds (never in the first 3 minutes)
- `style_change`: the dominant team's quadrant changes (control and chaos swap)

Each one carries `drivers` (plain-language reasons from the last 5 minutes) and `facts` (the same reasons as numbers,
for templates and for checking the model's text). This replaces the draft in "Working together":

```json
{"match_id": 1, "elapsed_minute": 42, "timestamp_s": 2547.3, "type": "momentum_shift",
 "team": "away", "team_name": "Redcliffe Town", "momentum": -6.0, "state": "controlled_dominance",
 "chaos_diff": -0.27, "drivers": ["21 completed passes into the final third vs 2 in 5 min",
 "5 high regains vs 3 in 5 min", "more predictable passing than the opponent"],
 "facts": {"window_min": 5, "counts": {"high_regain": [5, 3], "shot": [0, 0], "ft_pass": [21, 2], "pressure": [8, 6]},
           "passing": "more_predictable"},
 "score": {"home": 0, "away": 1}}
```

`Engine.snapshot()` returns the current momentum, state, entropy, `entropy_rel`, chaos index and quadrant of each team,
for the dashboard.

### Match state

Combining dominance and chaos gives four states, per team:

| | Lower entropy than opponent | Higher entropy than opponent |
| --- | --- | --- |
| **Winning momentum** | Controlled dominance | Chaotic pressure |
| **Losing momentum** | Being read | Scrambling |

### Players, pass quality and ball speed (`src/players.py`)

A second layer, per player, updated one event at a time. It covers the "real-time match intelligence" items of the brief:

| Brief item | What we do |
| --- | --- |
| Player identification | Every event gets a timed, machine-readable **name tag** (`overlay`): player, position, team, event, metrics and badges. The dashboard shows them as "Player tags" |
| Pass distance, accuracy, difficulty | Distance in metres from the pitch coordinates; accuracy per player; a **difficulty rating 0 to 100** from pass length (up to 40), forward progress (20), ending in the final third (15, plus 10 if it starts outside), and pressure (20) |
| Ball speed and shot speed | The `ball_speed_kmh` column of the feed (new in `events.csv`: passes and shots) |
| Indicators when thresholds are reached | Badges: **long pass** (35 m or more), **hard pass** (completed, difficulty 70 or more), **fast ball** (75 km/h or more), **power shot** (105 km/h or more), **goal**, **big chance** (xG 0.3 or more), **passing milestone** (25th, 50th, 75th, 100th completed pass) |
| Player-focused mode | The viewer names a player. Moments about that player (`player_focus`: shot, long pass, hard pass, fast ball, power shot, milestone) appear as cards, with a stats panel: passes, accuracy, shots, longest pass, top ball speed. At most one per minute; goals keep their own `goal` moment |

Not here: running speed and distance covered by a player. The feed has no player tracking, only where each event
happened, and we did not invent those numbers. The speed and distance indicators are about the ball and the passes.

The ball speeds are synthetic: they come from a separate random stream in the generator (passes: faster when longer;
shots: faster from range), so adding them did not change any other column. All earlier validation numbers still hold.

### Auto-eventing prototype (`src/autoevent.py`)

The brief mentions a model that runs over a video feed to produce pass and speed metrics automatically. We have no video, so
this is a **prototype of that stage on a synthetic ball-tracking feed**: `make_tracking` builds a noisy 10 Hz ball track
(position, and who holds the ball) from a synthetic match; `detect_passes` finds every flight of the ball from the track alone and
computes distance, ball speed and outcome. Run `python src/autoevent.py --seed 7`. On seed 7 with 0.35 m of tracking noise:
87% of passes found, 92% of detections are real passes, outcome and receiver 100% right, distance error 0.4 m, ball-speed
error 2.6 km/h (6.2 km/h with 1 m of noise). Limits: the track is built from the events, so detection is circular; shots are
not detected; real video would need a vision model that outputs the same `{t, x, y, holder}` frames.

### Narration: a team of agents (`src/agents.py`, `src/narrator.py`, `src/recap.py`)

Each key moment goes through a small newsroom. Every agent has one job; they share one state (the **Blackboard**), hand work
on, and recover from failures. Every step is recorded in a **trace** that analyst mode shows under each card.

| Agent | Kind | Job |
| --- | --- | --- |
| **Director** | code | Decides how much the moment matters *for this viewer*: base priority by type, +0.2 if it is about the followed club, +0.3 if it names the followed player. Sets the story length (2 sentences if priority is 0.7 or more, else 1) and the angle |
| **Analyst** | model | Writes the tactical "why it matters" note from the facts only. Its note is checked like any text; a rule-based note is used if the model fails or invents a number |
| **Writer** | model | Writes the story in the viewer's mode (casual: warm, no numbers; analyst: numbers and the reason), language, club and player, from the plan and the note |
| **Verifier** | code | Checks the draft: casual text has no numbers except the minute; analyst numbers must be in the facts; the text must use the language's own letters (Hebrew, Arabic); the followed player may be named only if the facts name them; length. On failure it sends the draft back with the reason (one retry) |
| **Fallback** | code | A template sentence (English, Hebrew, Spanish) when the model is missing, slow, failing or rejected twice. Other languages fall back to English and say so |

Failure recovery: a model call that errors or takes longer than 8 seconds counts as a failure; after **3 failures in a
row a circuit breaker stops calling the model for 30 seconds** (the dashboard says so), then tries again. An agent that
raises an exception is recorded as an error and the next agent carries on. The full-time recap has its own short
pipeline (Segmenter → Writer+Verifier → Fallback) built from the score, shots, xG and dominant spells.

Why several agents: the creative part (Writer) is never the part that decides what is true (Verifier), the Director makes the
same moment come out differently for different viewers, and every step can fail without stopping a live broadcast. A single
prompt cannot give that guarantee.

The model never blocks the stream: a card appears at once with a template sentence, and a worker thread upgrades it when the
agents finish (`live.py`). Changing mode, language, club or player re-tells the existing cards and the recap.

This is our own lightweight orchestration, **not** Microsoft Agent Framework or A2A. The Blackboard and the one-method agent
interface map directly onto such a framework (each agent an executor, the Newsroom the workflow), but we have not done that port.

---

## Results so far

Seed 42, 10 matches:

| Check | Result | Verdict |
| --- | --- | --- |
| Momentum vs hidden regime (correlation) | r = 0.66 | ✅ Clearly detectable |
| Momentum, simple ±2 threshold, regime correctly classified | 59% of minutes (chance = 33%) | ✅ Works, threshold not yet tuned |
| Rolling entropy vs hidden chaotic play | r = 0.32 | ⚠️ Detectable but noisy |
| Whole-match entropy vs hidden passing style | r = 0.31 | ⚠️ Weak |

Numbers vary with the seed (another run gave 0.77 for momentum and 0.41 for entropy), so compare methods over several
seeds.

**Live engine, graded on fresh matches** (`validate_engine.py`, 4 seeds x 20 matches, seeds 2000-2003, never used for
tuning; mean ± sd across seeds):

| Check | Result | Note |
| --- | --- | --- |
| Momentum vs hidden regime (correlation) | 0.81 ± 0.02 | |
| Dominance state (with hysteresis) correct | 76% ± 2 of minutes | Most-common-state baseline about 42% |
| Real switches caught within 3 minutes | 79% ± 2 (median lag 2 min) | Switches into a dominant state that lasts 5+ minutes |
| `momentum_shift` moments that match a real switch | 66% ± 3 precision, 81% ± 4 recall | About 6.3 shifts per match (hold 60 s, cooldown 2 min; was 58% / 85% / 7.4 without them) |
| Momentum predicts shot difference in the next 3 min | r = 0.48 ± 0.03 | A naive "shots in the last 3 min" gives 0.35 |
| Chaos: decay-window entropy, raw | AUC 0.63 ± 0.04 | Hard 40-pass window on the same minutes: 0.71 |
| Chaos: decay-window entropy vs own baseline | AUC 0.71 ± 0.04 | Same as the old raw window |
| Chaos: composite chaos index | **AUC 0.83 ± 0.01** | 0.5 = no signal, 1.0 = perfect |
| Chaos index vs hidden dominance | r = 0.01 ± 0.03 | Confound check: should be 0. Was -0.16 before the adjustment |

**Offline version vs live engine**, same four batches (`evaluate.py` for the offline numbers: 3-minute window, pass
weight 0.5, threshold 5, last 40 passes):

| Measure | Offline (`analyze_data.py` settings) | Live engine (`metrics.py`) |
| --- | --- | --- |
| Momentum r | 0.75 ± 0.04 | **0.81 ± 0.02** |
| Dominance accuracy | 72% ± 2 | **76% ± 2** |
| Caught within 3 min | **91% ± 2** | 79% ± 2 |
| Median lag | 2 min | 2 min |
| Chaos AUC | 0.71 ± 0.04 (all minutes) | **0.83 ± 0.01** (after the 10-minute warm-up) |

Two caveats on this comparison. The offline state has no hysteresis, so it reacts faster but flips more often; its
`momentum_shift` precision was not measured. The live chaos AUC leaves out the first 10 minutes, while the offline AUC
includes them (on the same post-warm-up minutes the old entropy scores 0.71, the same).

How to read this honestly:
- **The decay window alone did not help.** Raw, it is worse than the hard 40-pass window (0.63 vs 0.71), because it is
  more exposed to the fixed style difference between teams. Reading it against the team's own baseline brings it back
  to the old level. **The real gain comes from the composite chaos index** (0.71 to 0.83), which uses extra signals.
- Precision of `momentum_shift` is still the weakest number. The thresholds (6 and 3), the 60-second hold and the
  cooldown were chosen on seed 1000 as a compromise: shorter holds catch switches sooner but raise false moments;
  a 120-second hold reaches 83% precision but recall drops to 71%.
- Momentum weights were fitted on the generator, so the 0.81 is partly "we recovered what we put in" (see Known
  limitations). The shot-prediction row is the less circular check, but the generator also links regime to shots.

**Offline analysis (first version).** Momentum tracks the hidden regime. Background shading = the hidden dominant team; the black line = our momentum.
Bottom panel: each team's rolling pass entropy; strips = minutes the team was secretly playing chaotically.

![Match 1 timeline](docs/img/timeline_match1.png)

**Validation across all 10 matches:**

![Validation](docs/img/validation.png)

**Passing network** (the matrix the entropy is computed from):

![Passing network](docs/img/network_match1_home.png)

**What to improve next:** run the engine on a second generator with different effects, to check it is not tuned to this
one; test Azure OpenAI and Event Hubs against real resources; tune the key-moment hold time per audience (a studio
may prefer fewer, surer moments).

---

## How it maps to the hackathon brief

| Brief stage | Our component |
| --- | --- |
| (a) Ingest events as they happen | `streamer.py` plays events one at a time at a chosen speed, into a local queue or Azure Event Hubs (`eventhub.py`) |
| (b) Interpret into stats and patterns | `metrics.py`: momentum, entropy, chaos index, match state, big chances |
| (c) Explain why a moment matters | Key moments with `drivers` and `facts`: "control vs chaos", pressing, regains, shots |
| (d) Render on screen | `app.py` Streamlit dashboard; key moments are timed, machine-readable JSON that an overlay renderer could use |
| (e) Personalize | Casual vs analyst experience (different layout and text), favourite club, favourite player, language |
| (iii) Narrative generation | `narrator.py` stories and `recap.py` full-time recap |
| (iv) Explainability | "Why it matters" panel and agent trace in analyst mode; Analyst note; Verifier that blocks invented numbers |
| (ii) Player identification | `players.py` name tags (timed overlays) with threshold badges |
| (ii) Pass quality | Distance (m), accuracy per player, difficulty rating 0 to 100 |
| (ii) Speed metrics | Ball speed and shot speed (`ball_speed_kmh`), power-shot and fast-ball badges |
| (ii) Auto-eventing | `autoevent.py` prototype on synthetic ball tracking (no real video) |
| (e) Player-focused mode | Follow a player: `player_focus` cards and a stats panel |
| (v) Data innovation | This generator, with hidden ground truth for validation |
| (vi) Multi-language | Model writes in any supported language; template fallback in en, he, es |

Not covered: player running speed and distance (the feed has no player tracking), and auto-eventing from real video (prototype on synthetic tracking only).

---

## Roadmap

| Days | Goal | Person A: data and metrics | Person B: AI and app |
| --- | --- | --- | --- |
| 1–2 | Data | ✅ Generator, ✅ analysis | Repo, Azure, OpenAI access |
| 3–5 | Core engine | Streamer, incremental metrics, key moments | Persona prompts, tested on sample moments |
| 6–8 | Integration | Stream → engine | Streamlit dashboard with live cards |
| 9–10 | Personalization | Full-time recap | Mode, club, language options |
| 11–12 | One stretch goal | Validation plot or player on a roll | |
| 13–14 | Freeze | Bug fixes | Demo video, slides |

---

## Working together

- **Branches:** one branch per feature (`feature/streamer`, `feature/narrator`), merged into `main` via pull requests.
- **Ownership:** Person A owns the streamer and metrics; Person B owns narration and the app. Mostly separate files,
  so merges stay clean.
- **The contract between us:** agree first on the key-moment JSON that `metrics.py` hands to `narrator.py`.
  Draft:

  ```json
  {
    "match_id": 1,
    "elapsed_minute": 63,
    "type": "momentum_shift",
    "team": "away",
    "team_name": "Redcliffe Town",
    "momentum": -8.4,
    "state": "chaotic_pressure",
    "drivers": ["4 high regains in 5 min", "entropy up 0.12 vs opponent"],
    "score": {"home": 1, "away": 1}
  }
  ```
  The final version (more fields: `timestamp_s`, `chaos_diff`, `facts`; four types) is in "Live engine" above.
- **Secrets:** Azure keys go in a `.env` file, which is git-ignored. Never commit keys.
- **Notebooks:** fine for exploring in `notebooks/`; once something works, move it into `src/`.

---

## Known limitations

- **Circularity:** the validation only shows that we recover what we built into the generator. It proves the
  pipeline works, not that real football behaves this way. (The hackathon requires synthetic data for the project, so
  we have not tried real event data.)
- **Entropy evidence is season-level:** Neuman et al. linked entropy to final league position over a season.
  Nobody has shown it works minute to minute; we present it as "inspired by", not proven.
- **Short windows are noisy:** 40 passes is a small sample, and q = 0.2 amplifies rare passes.
- **Fitted weights:** the momentum weights come from a least-squares fit on this generator. They show the method works,
  not that these weights suit real football.
- **Chaos and dominance are separated by a fitted correction** (beta = 0.2, tuned on one seed). The raw link was
  r = -0.16; it is now about 0, but the correction is another number fitted on this generator.
- **Key-moment precision is 66%:** some `momentum_shift` moments are early or brief. The hold time and thresholds trade
  speed for precision.
- **The Azure OpenAI path has not run against the real service yet.** It is tested with a fake client (accepted answer,
  one retry, two rejections, error and timeout), and the templates are tested directly.
- **The dashboard was tested headless** (no errors in casual and analyst mode, language switch). Layout and look were
  not reviewed in a browser.
- **Event Hubs is tested with fakes only.** The JSON round trip, partition key (one match per partition) and
  end-of-match message are tested; a real namespace has not been used.
- **Not covered from the brief:** player running speed and distance covered (no player tracking in the feed), and
  auto-eventing from real video. The auto-eventing prototype runs on a synthetic track built from the events, so its
  detection score is circular; the useful part is how well speed and distance survive noise.
- **The agents are our own orchestration**, not Microsoft Agent Framework or A2A, and the model-using agents (Analyst,
  Writer) are tested with a scripted fake model only.
- **Ball speeds are synthetic** (a separate random stream in the generator), so they say nothing about real football.
- **Templates only for en, he, es.** Other languages need the model.
- **Simplified football:** no set pieces, fouls, cards, substitutions or offsides yet.

---

## References

- Neuman, Y., Israeli, N., Vilenchik, D., & Cohen, Y. (2018). The adaptive behavior of a soccer team: An entropy-based
  analysis. *Entropy*, 20(10), 758. https://doi.org/10.3390/e20100758
- Zacks, J. M., Speer, N. K., Swallow, K. M., Braver, T. S., & Reynolds, J. R. (2007). Event perception: A mind–brain
  perspective. *Psychological Bulletin*, 133(2), 273–293.
- Gilovich, T., Vallone, R., & Tversky, A. (1985). The hot hand in basketball. *Cognitive Psychology*, 17(3), 295–314.
- Miller, J. B., & Sanjurjo, A. (2018). Surprised by the gambler's and hot hand fallacies? A truth in small numbers.
  *Econometrica*, 86(6), 2019–2047.
