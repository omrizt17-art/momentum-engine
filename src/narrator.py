"""Narrator: turns a key-moment JSON (from metrics.py) into one or two sentences.

Two agents with different jobs, plus a safety net:
  Writer    an Azure OpenAI model writes the sentence from the moment's facts, in the viewer's mode and language.
  Verifier  plain code checks the sentence against the facts: no invented numbers, no numbers at all in casual mode.
            If it fails, the Writer gets the reason and one more try.
  Fallback  if the model is missing, slow, errors out, or fails verification twice, a template sentence is used,
            so the demo never stalls. Templates exist for English, Hebrew and Spanish.

Setup (optional; without it the templates are used): copy .env.example to .env and fill in
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT  (and AZURE_OPENAI_API_VERSION if needed).

Usage:
  python src/narrator.py --demo                       # narrate a few sample moments, casual and analyst, en/he/es
  python src/narrator.py --moments moments.jsonl --mode analyst --lang he
"""

import argparse  # read command-line options
import json  # moments are JSON
import os  # environment variables
import re  # find numbers in text
import sys  # to find sibling modules
from pathlib import Path  # file paths

sys.path.insert(0, str(Path(__file__).parent))  # let us import files from src/

LANGUAGES = {"en": "English", "he": "Hebrew", "es": "Spanish", "fr": "French", "de": "German", "ar": "Arabic", "pt": "Portuguese"}  # name shown to the model
TEMPLATE_LANGS = ("en", "he", "es")  # languages with a built-in fallback template
LLM_TIMEOUT_S = 8.0  # a slow model must not stall the broadcast

# ---------- fallback templates ----------
# {team} is the club name, {minute} the match minute, {n} / {m} counts, {mom} the momentum, {name} a player.
# Casual templates have no numbers (except the minute); analyst templates add the numbers and the reason.

T = {
    "en": {
        "goal": {"casual": "GOAL! {team} score, and the place erupts.", "analyst": "{minute}': goal for {team}, scored by {player} (xG {xg:.2f}). Score {home}-{away}."},
        "big_chance": {"casual": "What a chance for {team}! So close.", "analyst": "{minute}': big chance for {team}, {player} (xG {xg:.2f})."},
        "momentum_shift": {"casual": "{team} are taking over the match now.", "analyst": "{minute}': momentum swings to {team} ({mom:+.1f}). {reasons}."},
        "style_change": {"casual": "{team} have changed how they play: {style_casual}.", "analyst": "{minute}': {team} now show {state_name} ({reasons})."},
        "style_casual": {"controlled_dominance": "calm and in control", "chaotic_pressure": "wild and frantic, but still on top", "being_read": "predictable, and the opponent is reading them", "scrambling": "scrambling for ideas", "balanced": "evenly matched"},
        "state_name": {"controlled_dominance": "controlled dominance", "chaotic_pressure": "chaotic pressure", "being_read": "being read", "scrambling": "scrambling", "balanced": "a balanced phase"},
        "reason": {"high_regain": "{n} high regains to {m}", "shot": "{n} shots to {m}", "ft_pass": "{n} final-third passes to {m}", "pressure": "{n} pressing actions to {m}",
                   "less_predictable": "less predictable passing", "more_predictable": "more predictable passing"},
        "and": " and ",
        "player_focus": {"casual": "{player} is involved again: {kind_casual}.", "analyst": "{minute}': {player}, {kind_label}{metrics}."},
        "kind_casual": {"shot": "he has a go at goal", "long_pass": "a lovely long ball", "hard_pass": "a difficult pass pulled off", "fast_ball": "a rocket of a pass", "power_shot": "a thunderous strike", "milestone": "another calm passing display", "big_chance": "a big chance"},
        "kind_label": {"shot": "shot", "long_pass": "long pass", "hard_pass": "hard pass", "fast_ball": "fast ball", "power_shot": "power shot", "milestone": "passing milestone", "big_chance": "big chance"},
    },
    "he": {
        "goal": {"casual": "גול! {team} מבקיעה והקהל משתגע.", "analyst": "דקה {minute}: שער ל{team}, מבקיע: {player} (xG {xg:.2f}). תוצאה {home}-{away}."},
        "big_chance": {"casual": "איזו הזדמנות ל{team}! ממש קרוב.", "analyst": "דקה {minute}: הזדמנות גדולה ל{team}, {player} (xG {xg:.2f})."},
        "momentum_shift": {"casual": "{team} משתלטת עכשיו על המשחק.", "analyst": "דקה {minute}: המומנטום עובר ל{team} ({mom:+.1f}). {reasons}."},
        "style_change": {"casual": "{team} שינתה את סגנון המשחק: {style_casual}.", "analyst": "דקה {minute}: {team} במצב של {state_name} ({reasons})."},
        "style_casual": {"controlled_dominance": "רגועה ושולטת", "chaotic_pressure": "פראית ולחוצה, אבל עדיין מעל", "being_read": "צפויה, והיריבה קוראת אותה", "scrambling": "מחפשת פתרונות", "balanced": "שוויון בין הקבוצות"},
        "state_name": {"controlled_dominance": "שליטה מבוקרת", "chaotic_pressure": "לחץ כאוטי", "being_read": "נקראת על ידי היריבה", "scrambling": "בלבול", "balanced": "שלב מאוזן"},
        "reason": {"high_regain": "{n} חטיפות גבוהות מול {m}", "shot": "{n} בעיטות מול {m}", "ft_pass": "{n} מסירות לשליש האחרון מול {m}", "pressure": "{n} לחצים מול {m}",
                   "less_predictable": "מסירות פחות צפויות", "more_predictable": "מסירות צפויות יותר"},
        "and": " ו",
        "player_focus": {"casual": "{player} שוב במרכז העניינים: {kind_casual}.", "analyst": "דקה {minute}: {player}, {kind_label}{metrics}."},
        "kind_casual": {"shot": "בועט לשער", "long_pass": "כדור ארוך ויפה", "hard_pass": "מסירה קשה שהצליחה", "fast_ball": "מסירה כמו טיל", "power_shot": "בעיטה רועמת", "milestone": "עוד משחק מסירות רגוע", "big_chance": "הזדמנות גדולה"},
        "kind_label": {"shot": "בעיטה", "long_pass": "מסירה ארוכה", "hard_pass": "מסירה קשה", "fast_ball": "כדור מהיר", "power_shot": "בעיטה חזקה", "milestone": "ציון דרך במסירות", "big_chance": "הזדמנות גדולה"},
    },
    "es": {
        "goal": {"casual": "¡GOL! {team} marca y el estadio estalla.", "analyst": "Min. {minute}: gol de {team}, anota {player} (xG {xg:.2f}). Marcador {home}-{away}."},
        "big_chance": {"casual": "¡Qué ocasión para {team}! Por poco.", "analyst": "Min. {minute}: gran ocasión para {team}, {player} (xG {xg:.2f})."},
        "momentum_shift": {"casual": "{team} se hace dueño del partido.", "analyst": "Min. {minute}: el impulso pasa a {team} ({mom:+.1f}). {reasons}."},
        "style_change": {"casual": "{team} ha cambiado su forma de jugar: {style_casual}.", "analyst": "Min. {minute}: {team} muestra {state_name} ({reasons})."},
        "style_casual": {"controlled_dominance": "con calma y control", "chaotic_pressure": "frenético, pero mandando", "being_read": "previsible, y el rival lo lee", "scrambling": "buscando ideas", "balanced": "partido igualado"},
        "state_name": {"controlled_dominance": "dominio controlado", "chaotic_pressure": "presión caótica", "being_read": "juego leído por el rival", "scrambling": "desconcierto", "balanced": "una fase equilibrada"},
        "reason": {"high_regain": "{n} recuperaciones altas contra {m}", "shot": "{n} tiros contra {m}", "ft_pass": "{n} pases al último tercio contra {m}", "pressure": "{n} presiones contra {m}",
                   "less_predictable": "pases menos previsibles", "more_predictable": "pases más previsibles"},
        "and": " y ",
        "player_focus": {"casual": "{player} otra vez protagonista: {kind_casual}.", "analyst": "Min. {minute}: {player}, {kind_label}{metrics}."},
        "kind_casual": {"shot": "prueba suerte", "long_pass": "un bonito pase largo", "hard_pass": "un pase difícil que sale bien", "fast_ball": "un pase como un cohete", "power_shot": "un disparo potente", "milestone": "otra tarde tranquila de pases", "big_chance": "una gran ocasión"},
        "kind_label": {"shot": "disparo", "long_pass": "pase largo", "hard_pass": "pase difícil", "fast_ball": "pase muy rápido", "power_shot": "disparo potente", "milestone": "hito de pases", "big_chance": "gran ocasión"},
    },
}


def _reasons(moment, lang):  # reasons from the structured facts, in the template language
    t, f = T[lang], moment.get("facts") or {}
    parts = [t["reason"][k].format(n=a, m=b) for k, (a, b) in (f.get("counts") or {}).items() if a - b >= 2][:2]  # clear advantages only
    if f.get("passing"):  # passing style
        parts.append(t["reason"][f["passing"]])
    if len(parts) > 2:  # "a, b and c"
        return ", ".join(parts[:-1]) + t["and"] + parts[-1]
    return t["and"].join(parts) if parts else ""


def template_text(moment, mode, lang):  # a simple, always-available sentence
    lang = lang if lang in T else "en"  # unknown language: English
    t = T[lang]
    kind = moment["type"]
    state = moment.get("state") or "balanced"
    facts = moment.get("facts") or {}
    kind_key = facts.get("kind", "")
    pm = facts.get("metrics") or {}
    metrics = "".join([f", {pm['distance_m']:.0f} m" if "distance_m" in pm else "", f", {pm['ball_speed_kmh']:.0f} km/h" if "ball_speed_kmh" in pm else "",
                       f", difficulty {pm['difficulty']}" if "difficulty" in pm and lang == "en" else f", {pm['difficulty']}" if "difficulty" in pm else ""])
    text = t[kind][mode].format(kind_casual=t["kind_casual"].get(kind_key, ""), kind_label=t["kind_label"].get(kind_key, ""), metrics=metrics if mode == "analyst" else "",
        
        team=moment.get("team_name") or moment["team"], minute=moment["elapsed_minute"], mom=moment["momentum"],
        home=moment["score"]["home"], away=moment["score"]["away"], player=facts.get("player", ""), xg=facts.get("xg", 0.0), reasons=_reasons(moment, lang),
        style_casual=t["style_casual"].get(state, ""), state_name=t["state_name"].get(state, state))
    return re.sub(r"\s+\.", ".", text).replace(" ()", "").replace("..", ".").replace(". .", ".")  # tidy empty reason slots


# ---------- verifier ----------

NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _flat_numbers(o):  # every number inside nested facts, in the forms a sentence may use
    if isinstance(o, bool):
        return
    if isinstance(o, (int, float)):
        x = abs(float(o))
        yield from {str(int(x)), f"{x:.1f}", f"{x:.2f}", str(int(round(x))), str(int(x + 0.5))}
    elif isinstance(o, dict):
        for v in o.values():
            yield from _flat_numbers(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _flat_numbers(v)


def allowed_numbers(moment):  # every number a truthful sentence may contain
    nums = {str(moment["elapsed_minute"]), str(moment["score"]["home"]), str(moment["score"]["away"])}
    for x in (moment["momentum"], moment.get("chaos_diff", 0)):
        nums |= {f"{abs(x):.1f}", f"{abs(x):.2f}", str(int(abs(x)))}
    for pair in ((moment.get("facts") or {}).get("counts") or {}).values():
        nums |= {str(v) for v in pair}
    nums |= set(_flat_numbers(moment.get("facts")))
    nums.add(str((moment.get("facts") or {}).get("window_min", 5)))
    if "xg" in (moment.get("facts") or {}):
        nums.add(f"{moment['facts']['xg']:.2f}")
    for d in moment.get("drivers", []):
        nums |= {n.replace(",", ".") for n in NUM.findall(d)}
    return nums


def verify(text, moment, mode):  # returns None if the text is fine, otherwise the reason it is not
    if not text or len(text) > 400:
        return "empty or too long"
    found = [n.replace(",", ".") for n in NUM.findall(text)]
    if mode == "casual":  # casual text may mention the minute and nothing else numeric
        bad = [n for n in found if n != str(moment["elapsed_minute"])]
        return f"casual text must not contain numbers (found {bad})" if bad else None
    ok = allowed_numbers(moment)
    bad = [n for n in found if n not in ok and n.lstrip("0") not in ok]
    return f"numbers not in the facts: {bad}" if bad else None


# ---------- writer (LLM) ----------

def make_client():  # Azure OpenAI client from environment variables, or None if not configured
    try:
        from dotenv import load_dotenv  # optional: read a .env file
        load_dotenv()
    except Exception:
        pass
    key, endpoint, deployment = (os.getenv(k) for k in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"))
    if not (key and endpoint and deployment):
        return None
    try:
        from openai import AzureOpenAI
        return AzureOpenAI(api_key=key, azure_endpoint=endpoint, api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"), timeout=LLM_TIMEOUT_S, max_retries=0)
    except Exception:
        return None


STYLE = {
    "casual": "Write for a casual fan: warm, emotional, plain words, at most two short sentences. Use NO numbers at all (the minute may appear). Never use jargon such as entropy or momentum index.",
    "analyst": "Write for a data-hungry analyst: precise, at most two sentences. Include the key numbers and the reason the moment matters. Use only numbers that appear in the facts.",
}
EXPLAIN = (
    "Meaning of the facts: momentum is home minus away threat in points per minute (positive = home on top). 'passing' says whether the team's passing is "
    "more or less predictable than the opponent's (control vs chaos). state is one of: controlled_dominance (on top and predictable), chaotic_pressure "
    "(on top but unpredictable), being_read (under pressure and predictable), scrambling (under pressure and unpredictable). counts are [team, opponent] over the last minutes.")


def build_prompt(moment, mode, lang, fav_club=None, fav_player=None):  # system and user messages for the Writer
    system = (f"You are a live football commentator. Write in {LANGUAGES.get(lang, lang)}. {STYLE[mode]} Use ONLY the facts given: do not invent players, "
              f"numbers or events. Explain why the moment matters, not just that it happened. {EXPLAIN} Reply with the sentence(s) only.")
    if fav_club:
        system += f" The viewer supports {fav_club}: narrate from that club's point of view."
    if fav_player:
        system += f" The viewer follows {fav_player}: mention that player only if the facts name them."
    return system, "Facts:\n" + json.dumps({k: moment.get(k) for k in ("type", "elapsed_minute", "team_name", "state", "momentum", "drivers", "facts", "score")}, ensure_ascii=False)


class Narrator:
    def __init__(self, mode="casual", language="en", fav_club=None, fav_player=None, client="auto"):
        self.mode, self.language, self.fav_club, self.fav_player = mode, language, fav_club, fav_player
        self.client = make_client() if client == "auto" else client  # pass client=None to force templates

    def narrate(self, moment):  # returns {"text", "source", "language", "mode", "note"}
        base = {"mode": self.mode, "language": self.language, "note": ""}
        if self.client is not None:
            system, user = build_prompt(moment, self.mode, self.language, self.fav_club, self.fav_player)
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            for attempt in range(2):  # the Writer gets one retry with the Verifier's reason
                try:
                    r = self.client.chat.completions.create(model=os.getenv("AZURE_OPENAI_DEPLOYMENT"), messages=messages, temperature=0.7, max_tokens=120)
                    text = (r.choices[0].message.content or "").strip()
                except Exception as e:  # network, quota, timeout: use the template
                    base["note"] = f"LLM error: {type(e).__name__}"
                    break
                problem = verify(text, moment, self.mode)
                if problem is None:
                    return {**base, "text": text, "source": "llm"}
                base["note"] = f"verifier: {problem}"
                messages += [{"role": "assistant", "content": text}, {"role": "user", "content": f"Rejected: {problem}. Rewrite it using only the facts."}]
        lang = self.language if self.language in TEMPLATE_LANGS else "en"
        if lang != self.language:
            base["note"] += f" (no template for '{self.language}', used English)"
        return {**base, "text": template_text(moment, self.mode, lang), "source": "template", "language": lang}


# ---------- command line ----------

DEMO = [
    {"match_id": 1, "elapsed_minute": 63, "timestamp_s": 3800.0, "type": "momentum_shift", "team": "away", "team_name": "Redcliffe Town", "momentum": -8.4,
     "state": "chaotic_pressure", "chaos_diff": 0.31, "drivers": ["6 high regains vs 1 in 5 min", "less predictable passing than the opponent"],
     "facts": {"window_min": 5, "counts": {"high_regain": [6, 1], "shot": [2, 0], "ft_pass": [14, 6], "pressure": [9, 5]}, "passing": "less_predictable"}, "score": {"home": 1, "away": 1}},
    {"match_id": 1, "elapsed_minute": 24, "timestamp_s": 1470.0, "type": "goal", "team": "away", "team_name": "Redcliffe Town", "momentum": -5.1, "state": None, "chaos_diff": 0.0,
     "drivers": ["Oscar Fitzroy scores (xG 0.29)"], "facts": {"player": "Oscar Fitzroy", "xg": 0.29}, "score": {"home": 0, "away": 1}},
    {"match_id": 1, "elapsed_minute": 42, "timestamp_s": 2547.0, "type": "style_change", "team": "home", "team_name": "Northbridge FC", "momentum": 6.2, "state": "controlled_dominance",
     "chaos_diff": -0.4, "drivers": ["more predictable passing than the opponent"],
     "facts": {"window_min": 5, "counts": {"high_regain": [3, 3], "shot": [1, 0], "ft_pass": [9, 8], "pressure": [4, 4]}, "passing": "more_predictable"}, "score": {"home": 0, "away": 1}},
]


def main():  # command-line entry point
    ap = argparse.ArgumentParser(description="Narrate key moments")  # parser
    ap.add_argument("--demo", action="store_true", help="narrate three sample moments in every mode and language")  # built-in sample
    ap.add_argument("--moments", help="JSON-lines file written by streamer.py --jsonl")  # input file
    ap.add_argument("--mode", default="casual", choices=["casual", "analyst"])  # viewer mode
    ap.add_argument("--lang", default="en", help="language code, e.g. en, he, es, fr")  # language
    ap.add_argument("--club", default=None, help="favourite club")  # personalisation
    ap.add_argument("--player", default=None, help="favourite player")  # personalisation
    args = ap.parse_args()  # read options
    client = make_client()  # may be None
    print("Writer:", "Azure OpenAI" if client else "not configured, using templates")  # which path will run
    if args.demo:
        for lang in TEMPLATE_LANGS:
            for mode in ("casual", "analyst"):
                n = Narrator(mode, lang, client=client)
                print(f"\n--- {lang} / {mode} ---")
                for m in DEMO:
                    r = n.narrate(m)
                    print(f"[{r['source']}] {r['text']}" + (f"   ({r['note']})" if r["note"] else ""))
        return
    n = Narrator(args.mode, args.lang, args.club, args.player, client=client)
    for line in open(args.moments, encoding="utf-8"):
        m = json.loads(line)
        r = n.narrate(m)
        print(f"[{r['source']}] {m['elapsed_minute']}'  {r['text']}")


if __name__ == "__main__":  # run only when executed directly
    main()  # start
