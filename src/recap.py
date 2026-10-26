"""Full-time recap: a short match summary built from the score, team totals, goals and dominant spells ("episodes").

Same pattern as narrator.py: an Azure OpenAI Writer, a Verifier that checks numbers against the facts, and a template
fallback (English, Hebrew, Spanish) so a recap always appears.
"""

import json  # facts as JSON for the model
import os  # deployment name
import re  # find numbers
import sys  # to find sibling modules
from pathlib import Path  # file paths

sys.path.insert(0, str(Path(__file__).parent))  # let us import files from src/
from narrator import LANGUAGES, LLM_TIMEOUT_S, NUM, TEMPLATE_LANGS  # shared helpers


def recap_facts(view):  # the facts the recap may use, as plain data
    snap, names, tot = view["snapshot"], view["names"], view["totals"]
    goals = [{"minute": c["moment"]["elapsed_minute"], "team": c["moment"]["team_name"], "player": (c["moment"].get("facts") or {}).get("player"),
              "score": f"{c['moment']['score']['home']}-{c['moment']['score']['away']}"} for c in view["cards"] if c["moment"]["type"] == "goal"]
    eps = [{"from": a, "to": b, "team": names.get(t)} for a, b, t in view["episodes"]]
    return {"home": names.get("home"), "away": names.get("away"), "score": snap["score"], "goals": goals, "episodes": eps,
            "shots": {t: tot[t]["shots"] for t in tot}, "xg": {t: round(tot[t]["xg"], 1) for t in tot},
            "pass_completion_pct": {t: round(100 * tot[t]["complete"] / max(tot[t]["passes"], 1)) for t in tot},
            "key_moments": sum(1 for c in view["cards"] if c["moment"]["type"] in ("momentum_shift", "style_change"))}


def allowed(facts):  # every number the recap may contain
    nums = {str(facts["score"]["home"]), str(facts["score"]["away"])}
    for g in facts["goals"]:
        nums |= {str(g["minute"])} | set(NUM.findall(g["score"]))
    for e in facts["episodes"]:
        nums |= {str(e["from"]), str(e["to"]), str(e["to"] - e["from"])}
    for key in ("shots", "xg", "pass_completion_pct"):
        for v in facts[key].values():
            nums |= {str(v), f"{v:.1f}" if isinstance(v, float) else str(v)}
    nums.add(str(facts["key_moments"]))
    return nums


def verify(text, facts, mode):
    if not text or len(text) > 900:
        return "empty or too long"
    found = [n.replace(",", ".") for n in NUM.findall(text)]
    ok = allowed(facts)
    if mode == "casual":  # casual recap: the score and goal minutes only
        ok = {str(facts["score"]["home"]), str(facts["score"]["away"])} | {str(g["minute"]) for g in facts["goals"]}
    bad = [n for n in found if n not in ok]
    return f"numbers not in the facts: {bad}" if bad else None


R = {
    "en": {"head": "Full time: {home} {sh}-{sa} {away}.", "goal": "{player} scored for {team} on {minute}'.", "goal_anon": "{team} scored on {minute}'.",
           "ep": "{team} were on top from {a}' to {b}'.", "stats": "Shots {h}-{a}, expected goals {xh}-{xa}.", "none": "No goals.", "casual_stats": ""},
    "he": {"head": "סיום: {home} {sh}-{sa} {away}.", "goal": "{player} כבש עבור {team} בדקה {minute}.", "goal_anon": "{team} כבשה בדקה {minute}.",
           "ep": "{team} שלטה בין דקה {a} לדקה {b}.", "stats": "בעיטות {h}-{a}, שערים צפויים {xh}-{xa}.", "none": "ללא שערים.", "casual_stats": ""},
    "es": {"head": "Final: {home} {sh}-{sa} {away}.", "goal": "{player} marcó para {team} en el minuto {minute}.", "goal_anon": "{team} marcó en el minuto {minute}.",
           "ep": "{team} dominó del minuto {a} al {b}.", "stats": "Tiros {h}-{a}, goles esperados {xh}-{xa}.", "none": "Sin goles.", "casual_stats": ""},
}


def template_recap(facts, mode, lang):
    lang = lang if lang in R else "en"
    t = R[lang]
    parts = [t["head"].format(home=facts["home"], away=facts["away"], sh=facts["score"]["home"], sa=facts["score"]["away"])]
    for g in facts["goals"][:6]:
        parts.append((t["goal"] if g["player"] else t["goal_anon"]).format(player=g["player"], team=g["team"], minute=g["minute"]))
    if not facts["goals"]:
        parts.append(t["none"])
    if mode == "analyst":  # numbers only for the analyst
        for e in facts["episodes"][:4]:
            parts.append(t["ep"].format(team=e["team"], a=e["from"], b=e["to"]))
        parts.append(t["stats"].format(h=facts["shots"]["home"], a=facts["shots"]["away"], xh=facts["xg"]["home"], xa=facts["xg"]["away"]))
    return " ".join(parts)


def build_recap(view, narrator):  # returns {"text", "source", "language", "mode", "note"}
    facts = recap_facts(view)
    mode, lang = narrator.mode, narrator.language
    out = {"mode": mode, "language": lang, "note": ""}
    if narrator.client is not None:
        style = ("casual fan: warm and simple, 3 to 4 sentences, only the final score and goal minutes as numbers" if mode == "casual"
                 else "data-hungry analyst: 4 to 6 sentences with the numbers, the dominant spells and what decided the match")
        system = (f"You are a football broadcaster writing the full-time recap in {LANGUAGES.get(lang, lang)} for a {style}. Use ONLY the facts given; do not invent "
                  "players, events or numbers. Explain what shaped the match, not just the result. Reply with the recap only.")
        if narrator.fav_club:
            system += f" The viewer supports {narrator.fav_club}: tell it from that club's point of view."
        shown = {k: v for k, v in facts.items() if k != "key_moments"}  # a bare count of moments invites invented drama
        system += (" Say nothing about defending, goalkeepers, tactics or quality of play unless the numbers show it (shots, xG, pass completion). "
                   "Name a player only as the scorer of a listed goal.")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": "Facts:\n" + json.dumps(shown, ensure_ascii=False)}]
        for _ in range(2):
            try:
                r = narrator.client.chat.completions.create(model=os.getenv("AZURE_OPENAI_DEPLOYMENT"), messages=messages, temperature=0.7, max_tokens=400)
                text = (r.choices[0].message.content or "").strip()
            except Exception as e:
                out["note"] = f"LLM error: {type(e).__name__}"
                break
            problem = verify(text, facts, mode)
            if problem is None:
                return {**out, "text": text, "source": "llm"}
            out["note"] = f"verifier: {problem}"
            messages += [{"role": "assistant", "content": text}, {"role": "user", "content": f"Rejected: {problem}. Rewrite it using only the facts."}]
    used = lang if lang in TEMPLATE_LANGS else "en"
    return {**out, "text": template_recap(facts, mode, used), "source": "template", "language": used}
