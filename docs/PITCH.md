# Pitch and project description

Written for: hackathon judges (Microsoft and the Premier League), and for the submission form.
Items in [BRACKETS] must be filled in by the team before submitting.

**Challenge:** The Synthetic Match Insights Engine for Premier League Studio
**Project name:** Momentum & Chaos Engine
**Team:** [names]
**Repository (must be public):** [URL]
**Demo video (under 2 minutes, public URL):** [URL]

## Elevator pitch (about 60 words)

Every football app tells you who has momentum. We tell you *what kind*: is a team on top through calm control, or
through chaos? Our live engine reads match events as they happen and finds the moments that matter. A team of agents
(Director, Analyst, Writer, Verifier) tells each one for the viewer in front of the screen: plain words for a casual fan,
numbers for an analyst, the viewer's language, club and player. A code Verifier checks every sentence against the facts,
so the AI never invents a number, and a fallback means the broadcast never stalls.

## What we built

A pipeline with the five stages from the brief:

1. **Ingest.** A streamer plays synthetic match events one at a time, at a chosen speed. A local queue works out of the
   box; an Azure Event Hubs transport is included.
2. **Interpret.** An incremental engine keeps, per team: momentum (time-decayed threat points with hysteresis), pass
   entropy against the team's own baseline, and a chaos index. Together they give four match states: controlled
   dominance, chaotic pressure, being read, scrambling.
3. **Explain.** Each key moment (goal, big chance, momentum shift, change of style) carries the reasons behind it, for
   example "21 final-third passes to 2" and "more predictable passing than the opponent".
4. **Render.** A Streamlit dashboard shows score, minute, match state, a live momentum chart and a chaos chart with every goal
   marked in the scorer's colour (so you can see whether the trend led to the goal), and insight cards. Key moments are
   timed, machine-readable JSON, so an overlay renderer could use them.
5. **Personalize.** Casual fan and analyst are two different experiences (layout and text). The viewer picks language,
   favourite club and favourite player, and can follow one player through the match.

**Player intelligence.** Every event becomes a timed name tag with badges when a threshold is reached (long pass, hard pass,
fast ball, power shot, big chance, passing milestone). Passes get a distance in metres and a difficulty rating 0 to 100; players
get accuracy, longest pass and top ball speed. An auto-eventing prototype recovers passes and ball speed from a synthetic
ball-tracking feed (recall 87%, speed error 2.6 km/h) to show the "model over a feed" stage; there is no real video.

**Data innovation.** The synthetic generator hides a ground truth (who dominates, who plays chaotically, hot streaks).
The engine never sees it. We grade the engine against it on fresh matches.

## How the AI is used (honest status)

A small newsroom of agents with one job each, a shared state (the Blackboard), handoffs, retries and a visible trace:

- **Director** (code): decides how much a moment matters for this viewer (followed club and player) and how long the story is.
- **Analyst** (Azure OpenAI): writes the tactical "why it matters" note from the facts only.
- **Writer** (Azure OpenAI): writes the story in the viewer's mode and language.
- **Verifier** (code): rejects invented numbers, the wrong language, and mentions of a player the facts do not name; sends the draft back once.
- **Fallback**: if the model is missing, slow or fails twice, a template sentence is used (English, Hebrew, Spanish).
- **Circuit breaker**: after three model failures in a row the model is skipped for 30 seconds, and the screen says so.
- The engine itself is classical analytics, not an LLM. That is deliberate: it is fast, testable and explainable.

This is our own lightweight orchestration, not Microsoft Agent Framework or A2A. The model-using agents are tested with a
scripted fake model; [confirm: run with a real Azure OpenAI deployment on DATE].

## Microsoft and Azure technologies

| Technology | Use | Status |
| --- | --- | --- |
| Microsoft Foundry (Azure AI Foundry resource and project `momentum-project`, France Central) with Azure OpenAI `gpt-4.1-mini` | Analyst and Writer agents: stories and recaps | **Tested live on 2026-10-26**: `check_azure.py` passes on the Foundry endpoint; a full simulated match gave 10 of 10 model-written cards and a recap, in English and Hebrew. The model is deployed on the Foundry resource; the agents call it through the Azure OpenAI API. We do not use Foundry Agent Service. [Confirm on the public URL that cards say "written by Azure OpenAI" before you claim it there] |
| Azure Event Hubs (Basic, France Central) | Transport between the match streamer and the engine: the public dashboard source "Simulated match via Azure Event Hubs" sends the match into the hub and reads it back | Tested 2026-10-26: 2152 of 2152 events back in the same order. [Confirm it works on the public URL before you claim it there] |
| Azure App Service (Linux, Python 3.12) | Hosts the dashboard: https://momentum-engine-52e742.azurewebsites.net/ | Deployed 2026-10-26. [Confirm it still opens before submitting] |

## Results (synthetic data, fresh seeds, mean over 4 seeds x 20 matches)

- Momentum tracks the hidden dominant team: correlation 0.81; 76% of minutes classified correctly; median detection lag 2 minutes.
- Chaos index separates chaotic from structured play: AUC 0.83 (0.5 = no signal). Its link to dominance is about 0.
- Key moments: 66% precision, 81% recall against real momentum switches.
- 65 automated tests, including: the engine never reads the hidden truth, never looks at future events, and the agents recover from model errors, rejections and an open circuit.

## Limits we state openly

The weights were fitted on our own generator, so the numbers show the method works, not that it is right for real
football. The Azure parts are tested with fakes unless marked above. Player running speed and distance (no player
tracking in the feed) and auto-eventing from real video are not covered; the auto-eventing prototype uses synthetic tracking.
Set-up steps for Azure are in `docs/AZURE.md`.

## Problem and impact

Studios and streaming platforms have event data but not the story. Viewers want different depths: a casual fan wants
"they have taken over", an analyst wants "why". One engine can serve both, in any market's language, with an audit
trail that stops the AI inventing facts. That audit trail is what makes it usable in a broadcast.
