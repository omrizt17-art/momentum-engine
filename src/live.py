"""LiveSession: one match played live through streamer -> engine -> narrator, in a background thread.

Both the terminal demo (run_live.py) and the dashboard (app.py) use it, so there is one live path to test.
It reads events only (never truth). The model call never blocks the stream: a card appears at once with a template
sentence, and a worker thread upgrades it when the model answers.

    s = LiveSession(events, speed=30, narrator=Narrator(...))
    s.start()
    ...  s.view()  # a thread-safe copy of everything the screen needs
    s.stop()
"""

import sys  # to find sibling modules
import threading  # background threads
from collections import deque  # recent items for the screen
from concurrent.futures import ThreadPoolExecutor  # model calls in the background
from pathlib import Path  # file paths

sys.path.insert(0, str(Path(__file__).parent))  # let us import files from src/
from agents import Newsroom  # the team of agents that writes the stories
from metrics import Engine  # the live engine
from narrator import Narrator, template_text  # fallback sentences and viewer settings
from players import PlayerTracker  # name tags, pass quality, ball speed, player focus
from streamer import stream_events  # paced replay

MIN_EPISODE = 3  # a dominant spell shorter than this many minutes is not an "episode"


def episodes(history):  # dominant spells from the per-minute history: [(start_minute, end_minute, 'home'|'away')]
    out, start, cur = [], None, 0
    for h in history:  # one entry per minute
        if h["regime"] != cur:  # the state changed
            if cur != 0 and h["minute"] - start >= MIN_EPISODE:
                out.append((start, h["minute"], "home" if cur == 1 else "away"))
            start, cur = h["minute"], h["regime"]
    if cur != 0 and history and history[-1]["minute"] + 1 - start >= MIN_EPISODE:
        out.append((start, history[-1]["minute"] + 1, "home" if cur == 1 else "away"))
    return out


class LiveSession:
    def __init__(self, events, speed=30.0, narrator=None, engine=None):
        self.events = events
        self.speed = speed
        self.narrator = narrator or Narrator(client=None)
        self.engine = engine or Engine()
        if isinstance(events, (list, tuple)):
            self.prefill_names(events)
        self.newsroom = Newsroom(client=self.narrator.client)  # Director, Analyst, Writer, Verifier, Fallback
        self.players = PlayerTracker(focus=self.narrator.fav_player)
        self.ticker = deque(maxlen=14)  # latest name tags (every event)
        self.notable = deque(maxlen=30)  # name tags that reached a threshold (long pass, hard pass, fast ball, power shot, ...)
        self.lock = threading.RLock()  # guards everything below
        self.stop_flag = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.thread = None
        self.snapshot = self.engine.snapshot()
        self.history = []  # one dict per elapsed minute: minute, momentum, regime, entropy and chaos of both teams, score
        self.cards = []  # key moments with their story text, newest last
        self.totals = {t: {"shots": 0, "xg": 0.0, "passes": 0, "complete": 0} for t in ("home", "away")}
        self.finished = False
        self.error = None
        self.recap = None

    # ----- control -----

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.stop_flag.set()

    def set_prefs(self, mode=None, language=None, fav_club=None, fav_player=None):
        """Change how the story is told. Existing cards are re-told in the new mode / language."""
        with self.lock:
            changed = False
            for name, val in (("mode", mode), ("language", language), ("fav_club", fav_club), ("fav_player", fav_player)):
                if val is not None and getattr(self.narrator, name) != val:
                    setattr(self.narrator, name, val)
                    changed = True
            if fav_player is not None:
                self.players.focus = fav_player or None
            cards = list(self.cards) if changed else []
            redo_recap = changed and self.finished
        for c in cards:
            self.pool.submit(self._tell, c)
        if redo_recap:
            self.pool.submit(self._make_recap)

    def view(self):
        """A copy of the current state for the screen (safe to read from another thread)."""
        with self.lock:
            return {"snapshot": dict(self.snapshot), "history": list(self.history), "cards": [dict(c) for c in self.cards],
                    "totals": {t: dict(v) for t, v in self.totals.items()}, "finished": self.finished, "error": self.error,
                    "recap": self.recap, "names": dict(self.engine.names), "episodes": episodes(self.history),
                    "ticker": list(self.ticker), "notable": list(self.notable), "breaker_open": self.newsroom.breaker.is_open,
                    "top_players": {t: self.players.top(5, t) for t in ("home", "away")}, "focus_player": self._focus_stats()}

    # ----- internals -----

    def prefill_names(self, events):  # team names are fixed match information: show them from the first second, not after the first event
        for e in events:
            if e.get("team_name"):
                self.engine.names.setdefault(e["team"], e["team_name"])
            if len(self.engine.names) == 2:
                break

    def _profile(self):
        n = self.narrator
        return {"mode": n.mode, "language": n.language, "fav_club": n.fav_club, "fav_player": n.fav_player}

    def _focus_stats(self):
        pid = self.players.find(self.players.focus)
        return self.players.stats(pid) if pid else None

    def _tell(self, card):  # narrate one card with the Newsroom (runs in the worker pool)
        with self.lock:
            profile = self._profile()
        r = self.newsroom.process(card["moment"], profile)
        with self.lock:
            if (r["mode"], r["language"]) == (self.narrator.mode, self.narrator.language) or r["source"] == "template":  # ignore answers for an old setting
                card.update(text=r["text"], source=r["source"], note=r["note"], pending=False, trace=r["trace"], plan=r["plan"], analysis=r["analysis"])

    def _run(self):
        try:
            last_minute = None
            for ev in stream_events(self.events, self.speed):
                if self.stop_flag.is_set():
                    break
                moments = self.engine.update(ev)
                overlay, focus = self.players.update(ev)
                moments = moments + [self.engine.moment("player_focus", f["team"], f["timestamp_s"], f["drivers"], f["facts"]) for f in focus]
                self._count(ev)
                minute = int(ev["timestamp_s"] // 60)
                with self.lock:
                    self.snapshot = self.engine.snapshot()
                    if minute != last_minute:  # one history point per minute, the state when the minute begins
                        s = self.snapshot
                        self.history.append({"minute": minute, "momentum": s["momentum"], "regime": s["regime"], "home_chaos": s["home"]["chaos"],
                                             "away_chaos": s["away"]["chaos"], "home_entropy": s["home"]["entropy"], "away_entropy": s["away"]["entropy"],
                                             "score_home": s["score"]["home"], "score_away": s["score"]["away"]})
                        last_minute = minute
                    self.ticker.append(overlay)
                    if overlay["badges"]:
                        self.notable.append(overlay)
                    for m in moments:
                        card = {"id": len(self.cards), "moment": m, "pending": True, "source": "template", "note": "", "trace": [], "plan": None, "analysis": None,
                                "text": template_text(m, self.narrator.mode, self.narrator.language if self.narrator.language in ("en", "he", "es") else "en")}
                        self.cards.append(card)
                        self.pool.submit(self._tell, card)
            if not self.stop_flag.is_set():
                self._finish()
        except Exception as e:  # show the problem on screen instead of dying silently
            with self.lock:
                self.error = f"{type(e).__name__}: {e}"

    def _count(self, ev):
        t = self.totals[ev["team"]]
        if ev["event_type"] == "shot":
            t["shots"] += 1
            t["xg"] += float(ev["xg"])
        elif ev["event_type"] == "pass":
            t["passes"] += 1
            t["complete"] += ev["outcome"] == "complete"

    def _finish(self):
        with self.lock:
            self.finished = True
        self.pool.submit(self._make_recap)

    def _make_recap(self):
        v = self.view()
        r = self.newsroom.recap(v, self._profile())
        with self.lock:
            self.recap = r
