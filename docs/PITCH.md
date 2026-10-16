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
through chaos? Our live engine reads match events as they happen, finds the moments that matter, explains why in plain
words for a casual fan or with numbers for an analyst, in the viewer's language, and writes the full-time recap. Every
sentence is checked against the facts, so the AI never invents a number.

## What we built

A pipeline with the five stages from the brief:

1. **Ingest.** A streamer plays synthetic match events one at a time, at a chosen speed. A local queue works out of the
   box; an Azure Event Hubs transport is included.
2. **Interpret.** An incremental engine keeps, per team: momentum (time-decayed threat points with hysteresis), pass
   entropy against the team's own baseline, and a chaos index. Together they give four match states: controlled
   dominance, chaotic pressure, being read, scrambling.
3. **Explain.** Each key moment (goal, big chance, momentum shift, change of style) carries the reasons behind it, for
   example "21 final-third passes to 2" and "more predictable passing than the opponent".
4. **Render.** A Streamlit dashboard shows score, minute, match state, live momentum and insight cards. Key moments are
   timed, machine-readable JSON, so an overlay renderer could use them.
5. **Personalize.** Casual fan and analyst are two different experiences (layout and text). The viewer picks language,
   favourite club and favourite player.

**Data innovation.** The synthetic generator hides a ground truth (who dominates, who plays chaotically, hot streaks).
The engine never sees it. We grade the engine against it on fresh matches.

## How the AI is used (honest status)

- **Writer** (Azure OpenAI): writes one or two sentences per key moment, and the full-time recap, in the viewer's mode and language.
- **Verifier** (code): rejects text with invented numbers (casual text may have no numbers). The Writer gets the reason and one retry.
- **Fallback**: if the model is missing, slow or fails twice, a template sentence is used (English, Hebrew, Spanish), so a live broadcast never stalls.
- The engine itself is classical analytics, not an LLM. That is deliberate: it is fast, testable and explainable.

## Microsoft and Azure technologies

| Technology | Use | Status |
| --- | --- | --- |
| Azure OpenAI (Microsoft Foundry deployment) | Writer for stories and recaps | Code done and tested with a fake client. [Confirm: tested with a real deployment on DATE] |
| Azure Event Hubs | Production transport between streamer and engine | Code done and tested with fakes. [Confirm: tested against a real namespace] |
| [Azure Container Apps or other hosting] | [Hosting the dashboard] | [Not done: delete this row or do it] |

## Results (synthetic data, fresh seeds, mean over 4 seeds x 20 matches)

- Momentum tracks the hidden dominant team: correlation 0.81; 76% of minutes classified correctly; median detection lag 2 minutes.
- Chaos index separates chaotic from structured play: AUC 0.83 (0.5 = no signal). Its link to dominance is about 0.
- Key moments: 66% precision, 81% recall against real momentum switches.
- 37 automated tests, including: the engine never reads the hidden truth, and never looks at future events.

## Limits we state openly

The weights were fitted on our own generator, so the numbers show the method works, not that it is right for real
football. The Azure parts are tested with fakes unless marked above. Player identification overlays, ball and shot
speed, and video auto-eventing are not covered.

## Problem and impact

Studios and streaming platforms have event data but not the story. Viewers want different depths: a casual fan wants
"they have taken over", an analyst wants "why". One engine can serve both, in any market's language, with an audit
trail that stops the AI inventing facts. That audit trail is what makes it usable in a broadcast.
