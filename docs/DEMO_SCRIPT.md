# Demo video script (target 1:55, hard limit 2:00)

Written for: the person recording the demo video. Judges stop watching at 2:00, so the best moment goes early.
No third-party logos or copyrighted music. Show the real app on screen.

## Before recording
- `streamlit run src/app.py`, browser at 100% zoom, window about 1400 px wide.
- Match number 7 (it has a goal, big chances and shifts early). Speed 90 (a match in about 70 seconds).
- Do one dry run. Check the sidebar says which narrator is active ("Azure OpenAI" or "templates only"). The "Agents:" line shows
  `Analyst ✓ Writer ✓` only with a real model; with templates it shows `–` (skipped), which is fine but less impressive.
- Pick the favourite player in the dry run so you know the name to type.
- Have the README results table open in a second tab.

## Shots
| Time | On screen | Say |
| --- | --- | --- |
| 0:00-0:08 | Title: "Momentum & Chaos Engine" | "Every app tells you who has momentum. We tell you what kind: control or chaos." |
| 0:08-0:30 | Press Start in **Casual fan** mode. Cards appear. | "A match streams in live. The engine finds key moments and tells them in plain words, no jargon, no numbers." |
| 0:30-0:55 | Switch to **Analyst**. Open "Why it matters" on a momentum shift and point at the line "Agents: Director, Analyst, Writer, Verifier". Show the chaos chart. | "Same match, analyst view. A team of agents wrote this: the Analyst explains why it matters, the Writer tells it, and a Verifier checks every number against the facts." |
| 0:55-1:15 | Type a favourite player (copy a name from "Player tags" or the "Most involved players" table). Player card and stats panel appear. | "Follow one player. Name tags with badges for long passes and power shots, a difficulty rating for every pass, ball speed, and cards about your player." |
| 1:15-1:30 | Change language to Spanish (or Hebrew). Type a favourite club. | "Language, club and player: every card is re-told at once." |
| 1:30-1:40 | Full-time recap card. | "At the final whistle, a recap with the dominant spells." |
| 1:40-1:55 | README results table, then the test run output. | "Under the hood: the engine is graded against a hidden ground truth on fresh matches, and 65 tests check the agents recover when the model fails. Built for Azure OpenAI and Event Hubs." |

Only say "built on Azure OpenAI / Event Hubs" if you tested them for real. Otherwise say "designed for".

## After recording
Upload to YouTube or Vimeo as public, paste the URL into the project page, and into `docs/PITCH.md`.
