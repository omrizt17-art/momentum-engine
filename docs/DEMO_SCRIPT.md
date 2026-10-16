# Demo video script (target 1:50, hard limit 2:00)

Written for: the person recording the demo video. Judges stop watching at 2:00, so the best moment goes early.
No third-party logos or copyrighted music. Show the real app on screen.

## Before recording
- `streamlit run src/app.py`, browser at 100% zoom, window about 1400 px wide.
- Match number 7 (it has a goal, big chances and shifts early). Speed 90 (a match in about 70 seconds).
- Do one dry run. Check the sidebar says which narrator is active ("Azure OpenAI" or "templates only").
- Have the README results table open in a second tab.

## Shots
| Time | On screen | Say |
| --- | --- | --- |
| 0:00-0:10 | Title: "Momentum & Chaos Engine" | "Every app tells you who has momentum. We tell you what kind: control or chaos." |
| 0:10-0:40 | Press Start in **Casual fan** mode. Cards appear. | "A match streams in live. The engine finds key moments and tells them in plain words, no jargon, no numbers." |
| 0:40-1:05 | Switch to **Analyst**. Open "Why it matters" on a momentum shift. Show the chaos chart. | "Same match, analyst view. Each moment shows the reasons: high regains, shots, and whether the team's passing is more or less predictable than the opponent's." |
| 1:05-1:25 | Change language to Spanish (or Hebrew). Type a favourite club. | "The viewer picks language, club and player. Cards are re-told at once." |
| 1:25-1:40 | Full-time recap card. | "At the final whistle, a recap with the dominant spells." |
| 1:40-1:55 | README results table, then the test run output. | "Under the hood: the engine is graded against a hidden ground truth on fresh matches. A verifier checks every sentence against the facts, so the AI cannot invent a number. Built on Azure OpenAI and Event Hubs." |

Only say "built on Azure OpenAI / Event Hubs" if you tested them for real. Otherwise say "designed for".

## After recording
Upload to YouTube or Vimeo as public, paste the URL into the project page, and into `docs/PITCH.md`.
