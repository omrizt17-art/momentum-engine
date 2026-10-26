"""Newsroom: a small team of agents that turns a key moment into a story the viewer can trust.

Each agent has one job. They share one state (the Blackboard), hand work to the next, and recover from failures.

  Director   (code)  decides how much a moment matters for THIS viewer (club, player followed) and how long the story is
  Analyst    (LLM)   writes the tactical "why it matters" note from the facts only; a rule-based note if the model is out
  Writer     (LLM)   writes the story in the viewer's mode and language from the plan and the note
  Verifier   (code)  checks the draft against the facts: numbers, names, language, length; sends it back with the reason
  Fallback   (code)  a template sentence when the model is missing, slow, failing or rejected twice

Why several agents: the part that is creative (Writer) is never the part that decides what is true (Verifier), and
the model is optional at every step, so a live broadcast never stalls. A circuit breaker stops calling a failing model
for a while. Every step is recorded in a trace that the dashboard can show.

This is our own lightweight orchestration (no external agent framework). The Blackboard and the step interface map
directly onto frameworks such as Microsoft Agent Framework: each agent is one executor and the Newsroom is the workflow.
"""

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from narrator import LANGUAGES, TEMPLATE_LANGS, allowed_numbers, template_text, verify as verify_numbers  # noqa: E402
from recap import build_recap, recap_facts, template_recap, verify as verify_recap  # noqa: E402

BASE_PRIORITY = {"goal": 1.0, "momentum_shift": 0.7, "big_chance": 0.6, "player_focus": 0.5, "style_change": 0.4}
SCRIPT = {"he": r"[֐-׿]", "ar": r"[؀-ۿ]"}  # languages whose text must contain their own letters
STEP_TIMEOUT_S = 8.0  # a model call longer than this counts as a failure


class Blackboard(dict):
    """The shared state of one story. Agents read what earlier agents wrote and add their own part."""

    def __init__(self, moment, profile):
        super().__init__(moment=moment, profile=profile, plan=None, analysis=None, draft=None, verdict=None, final=None, trace=[])

    def log(self, agent, status, started, note=""):
        self["trace"].append({"agent": agent, "status": status, "ms": int((time.time() - started) * 1000), "note": note})


class CircuitBreaker:
    """After `threshold` model failures in a row, stop calling the model for `cooldown_s` seconds, then try once again."""

    def __init__(self, threshold=3, cooldown_s=30.0, clock=time.time):
        self.threshold, self.cooldown_s, self.clock = threshold, cooldown_s, clock
        self.failures, self.opened_at = 0, None
        self.lock = threading.Lock()

    def allow(self):
        with self.lock:
            if self.opened_at is None:
                return True
            if self.clock() - self.opened_at >= self.cooldown_s:  # half-open: let one call through
                self.opened_at = None
                self.failures = self.threshold - 1
                return True
            return False

    def record(self, ok):
        with self.lock:
            if ok:
                self.failures, self.opened_at = 0, None
            else:
                self.failures += 1
                if self.failures >= self.threshold:
                    self.opened_at = self.clock()

    @property
    def is_open(self):
        return self.opened_at is not None


def _flat(profile_text):
    return (profile_text or "").strip().lower()


# ---------- agents ----------

class Director:
    """Decides priority and length for this viewer. No model: it must be fast and predictable."""

    name = "Director"

    def run(self, board, newsroom):
        m, prof = board["moment"], board["profile"]
        pri = BASE_PRIORITY.get(m["type"], 0.4)
        blob = json.dumps({"facts": m.get("facts"), "drivers": m.get("drivers")}, ensure_ascii=False).lower()
        club, player = _flat(prof.get("fav_club")), _flat(prof.get("fav_player"))
        follow_club = bool(club) and club in (m.get("team_name") or "").lower()
        follow_player = bool(player) and player in blob
        pri = min(1.0, pri + (0.2 if follow_club else 0.0) + (0.3 if follow_player else 0.0))
        angle = f"player:{prof['fav_player']}" if follow_player else f"club:{prof['fav_club']}" if follow_club else "neutral"
        board["plan"] = {"priority": round(pri, 2), "sentences": 2 if pri >= 0.7 else 1, "angle": angle,
                         "supporter_view": bool(club) and not follow_club and bool(m.get("team_name"))}
        return "ok", f"priority {pri:.2f}, {board['plan']['sentences']} sentence(s), angle {angle}"


def rule_note(moment):
    """The Analyst's fallback: the engine's own reasons, in one line."""
    drivers = moment.get("drivers") or []
    if moment["type"] == "momentum_shift":
        return f"{moment.get('team_name')} are now on top: " + "; ".join(drivers[:3]) + "."
    if moment["type"] == "style_change":
        return f"{moment.get('team_name')} changed how they play: " + "; ".join(drivers[:2]) + "."
    return "; ".join(drivers[:2]) + ("." if drivers else "")


class Analyst:
    """Writes the tactical note. Uses the model when it can; the note is checked against the facts like any text."""

    name = "Analyst"
    SYSTEM = ("You are a football tactical analyst. From the facts only, say in at most two short sentences why this moment matters "
              "tactically, in English. Use only numbers that appear in the facts. No greetings, no opinions beyond the facts. "
              "Meaning: momentum is home minus away threat in points per minute; 'passing' says whether the team's passing is more or "
              "less predictable than the opponent's (control vs chaos).")

    def run(self, board, newsroom):
        m = board["moment"]
        if not newsroom.llm_ready():
            board["analysis"] = rule_note(m)
            return "skipped", "model unavailable: rule-based note"
        text, err = newsroom.ask(self.SYSTEM, "Facts:\n" + json.dumps(_facts_view(m), ensure_ascii=False), 120)
        if err:
            board["analysis"] = rule_note(m)
            return "error", f"{err}: rule-based note"
        if verify_numbers(text, m, "analyst"):  # an invented number in the note: do not pass it on
            board["analysis"] = rule_note(m)
            return "rejected", "note had numbers not in the facts: rule-based note"
        board["analysis"] = text
        return "ok", text[:80]


def _facts_view(m):
    return {k: m.get(k) for k in ("type", "elapsed_minute", "team_name", "state", "momentum", "drivers", "facts", "score")}


class Writer:
    """Writes the story for the viewer. The only agent that is allowed to be creative."""

    name = "Writer"
    STYLE = {
        "casual": "Write for a casual fan: warm, emotional, plain words. Use NO numbers at all (the minute may appear). Never use jargon such as entropy or momentum index.",
        "analyst": "Write for a data-hungry analyst: precise, with the key numbers and the reason the moment matters. Use only numbers that appear in the facts.",
    }

    def run(self, board, newsroom):
        m, prof, plan = board["moment"], board["profile"], board["plan"]
        if not newsroom.llm_ready():
            board["draft"] = None
            return "skipped", "model unavailable" + (" (circuit open)" if newsroom.breaker.is_open else "")
        system = (f"You are a live football commentator. Write in {LANGUAGES.get(prof['language'], prof['language'])}. {self.STYLE[prof['mode']]} "
                  f"Write exactly {plan['sentences']} sentence(s). Use ONLY the facts and the analyst note: do not invent players, numbers or events. "
                  "Explain why the moment matters, not just that it happened. Reply with the story only.")
        if plan["angle"].startswith("club:") or plan["supporter_view"]:
            system += f" The viewer supports {prof['fav_club']}: tell it from that club's point of view."
        if plan["angle"].startswith("player:"):
            system += f" The viewer follows {prof['fav_player']}: make that player the centre of the story."
        user = "Facts:\n" + json.dumps(_facts_view(m), ensure_ascii=False) + f"\nAnalyst note: {board['analysis']}"
        if board["verdict"] and not board["verdict"]["ok"] and board["draft"]:  # a retry: the Verifier's reason goes back to the Writer
            user += f"\nYour previous draft was rejected: {board['verdict']['problem']}. Rewrite it using only the facts.\nPrevious draft: {board['draft']}"
        text, err = newsroom.ask(system, user, 140)
        if err:
            board["draft"] = None
            return "error", err
        board["draft"] = text
        return "ok", text[:80]


class Verifier:
    """Plain code, so it cannot be talked round by a persuasive draft."""

    name = "Verifier"

    def run(self, board, newsroom):
        m, prof, plan, text = board["moment"], board["profile"], board["plan"], board["draft"]
        problem = verify_numbers(text, m, prof["mode"])
        if not problem and prof["language"] in SCRIPT and not re.search(SCRIPT[prof["language"]], text):
            problem = f"text is not written in {LANGUAGES[prof['language']]}"
        if not problem and prof.get("fav_player") and _flat(prof["fav_player"]) in text.lower():
            if _flat(prof["fav_player"]) not in json.dumps(_facts_view(m), ensure_ascii=False).lower():
                problem = "mentions the followed player, who is not in the facts"
        if not problem and len(re.findall(r"[.!?؟。]+", text)) > plan["sentences"] + 1:
            problem = f"too long: asked for {plan['sentences']} sentence(s)"
        board["verdict"] = {"ok": problem is None, "problem": problem}
        return ("ok", "all checks passed") if problem is None else ("rejected", problem)


class Fallback:
    name = "Fallback"

    def run(self, board, newsroom):
        prof = board["profile"]
        lang = prof["language"] if prof["language"] in TEMPLATE_LANGS else "en"
        board["final"] = {"text": template_text(board["moment"], prof["mode"], lang), "source": "template", "language": lang}
        return "ok", "template" + ("" if lang == prof["language"] else f" (no template for '{prof['language']}', used English)")


# ---------- orchestrator ----------

class Newsroom:
    def __init__(self, client=None, breaker=None, max_attempts=2):
        self.client = client
        self.breaker = breaker or CircuitBreaker()
        self.max_attempts = max_attempts
        self.director, self.analyst, self.writer, self.verifier, self.fallback = Director(), Analyst(), Writer(), Verifier(), Fallback()

    # ----- model access, shared by the LLM agents -----

    def llm_ready(self):
        return self.client is not None and self.breaker.allow()

    def ask(self, system, user, max_tokens):
        """One model call. Returns (text, None) or (None, error). Feeds the circuit breaker."""
        try:
            r = self.client.chat.completions.create(model=os.getenv("AZURE_OPENAI_DEPLOYMENT"), temperature=0.6, max_tokens=max_tokens,
                                                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], timeout=STEP_TIMEOUT_S)
            text = (r.choices[0].message.content or "").strip()
            self.breaker.record(bool(text))
            return (text, None) if text else (None, "empty answer")
        except Exception as e:
            self.breaker.record(False)
            return None, f"{type(e).__name__}"

    def _step(self, agent, board):
        t0 = time.time()
        try:
            status, note = agent.run(board, self)
        except Exception as e:  # an agent bug must never stop the broadcast
            status, note = "error", f"{type(e).__name__}: {e}"
        board.log(agent.name, status, t0, note)
        return status

    # ----- one moment -----

    def process(self, moment, profile):
        """Returns {"text", "source", "language", "mode", "note", "trace", "plan", "analysis"}."""
        profile = {"mode": "casual", "language": "en", "fav_club": None, "fav_player": None, **profile}
        board = Blackboard(moment, profile)
        self._step(self.director, board)
        self._step(self.analyst, board)
        for attempt in range(self.max_attempts):  # Writer and Verifier take turns
            if self._step(self.writer, board) != "ok":
                break
            self._step(self.verifier, board)
            if board["verdict"]["ok"]:
                board["final"] = {"text": board["draft"], "source": "llm", "language": profile["language"]}
                break
        if board["final"] is None:
            self._step(self.fallback, board)
        f = board["final"]
        note = "" if f["source"] == "llm" else next((t["note"] for t in reversed(board["trace"]) if t["agent"] in ("Verifier", "Writer") and t["status"] != "ok"), "")
        return {"text": f["text"], "source": f["source"], "language": f["language"], "mode": profile["mode"], "note": note,
                "trace": board["trace"], "plan": board["plan"], "analysis": board["analysis"]}

    # ----- full-time recap: Segmenter -> Writer -> Verifier -> Fallback -----

    def recap(self, view, profile):
        profile = {"mode": "casual", "language": "en", "fav_club": None, "fav_player": None, **profile}
        trace = []

        def log(agent, status, t0, note=""):
            trace.append({"agent": agent, "status": status, "ms": int((time.time() - t0) * 1000), "note": note})

        t0 = time.time()
        facts = recap_facts(view)  # Segmenter: score, totals, goals and dominant spells from the live state
        log("Segmenter", "ok", t0, f"{len(facts['episodes'])} dominant spell(s), {len(facts['goals'])} goal(s)")
        out = None
        shim = type("N", (), {"mode": profile["mode"], "language": profile["language"], "fav_club": profile["fav_club"], "client": None, "fav_player": None})()
        if self.llm_ready():
            t0 = time.time()
            shim.client = self.client
            res = build_recap(view, shim)  # Writer and Verifier with one retry, as in recap.py
            ok = res["source"] == "llm"
            self.breaker.record(ok or "LLM error" not in res["note"])
            log("Writer+Verifier", "ok" if ok else "rejected", t0, res["note"] or "accepted")
            if ok:
                out = res
        else:
            log("Writer+Verifier", "skipped", time.time(), "model unavailable")
        if out is None:
            t0 = time.time()
            lang = profile["language"] if profile["language"] in TEMPLATE_LANGS else "en"
            out = {"text": template_recap(facts, profile["mode"], lang), "source": "template", "language": lang, "mode": profile["mode"], "note": ""}
            log("Fallback", "ok", t0, "template")
        return {**out, "trace": trace}
