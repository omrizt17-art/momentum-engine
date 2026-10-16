"""Tests for the Newsroom agents: Director, Analyst, Writer, Verifier, Fallback, circuit breaker and trace. No network.

Run:  python -m pytest tests -q
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agents import CircuitBreaker, Newsroom  # noqa: E402
from generate_matches import simulate_match  # noqa: E402
from live import LiveSession  # noqa: E402
from narrator import DEMO, Narrator  # noqa: E402

SHIFT, GOAL, STYLE = DEMO
CASUAL = {"mode": "casual", "language": "en"}
ANALYST = {"mode": "analyst", "language": "en"}


class Scripted:
    """A fake model: answers in order (an Exception is raised instead) and remembers every system prompt."""

    def __init__(self, *answers):
        self.answers, self.systems = list(answers), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.systems.append(kw["messages"][0]["content"])
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=a))])


def agents_run(trace):
    return [(t["agent"], t["status"]) for t in trace]


def wait_for_recap(session):
    for _ in range(300):
        v = session.view()
        if v["recap"]:
            return v
        time.sleep(0.1)
    return session.view()


def test_happy_path_uses_every_agent_in_order():
    client = Scripted("Redcliffe are winning the key duels, with 6 high regains to 1.", "Momentum swings to Redcliffe Town at 63' (-8.4) after 6 high regains to 1.")
    r = Newsroom(client).process(SHIFT, ANALYST)
    assert r["source"] == "llm"
    assert agents_run(r["trace"]) == [("Director", "ok"), ("Analyst", "ok"), ("Writer", "ok"), ("Verifier", "ok")]
    assert "tactical analyst" in client.systems[0] and "commentator" in client.systems[1]  # two different roles, two different prompts
    assert r["analysis"].startswith("Redcliffe are winning")


def test_verifier_sends_the_draft_back_and_the_writer_retries():
    client = Scripted("note", "Redcliffe have 17 shots.", "Redcliffe Town take over at 63'.")
    r = Newsroom(client).process(SHIFT, ANALYST)
    assert r["source"] == "llm"
    assert agents_run(r["trace"])[-4:] == [("Writer", "ok"), ("Verifier", "rejected"), ("Writer", "ok"), ("Verifier", "ok")]


def test_two_rejections_end_in_the_fallback_with_the_reason():
    client = Scripted("note", "17 shots!", "23 shots!")
    r = Newsroom(client).process(SHIFT, ANALYST)
    assert r["source"] == "template" and agents_run(r["trace"])[-1] == ("Fallback", "ok")
    assert "not in the facts" in r["note"]


def test_analyst_note_with_an_invented_number_is_replaced_by_the_rule_note():
    client = Scripted("They had 41 shots.", "Redcliffe Town take over at 63'.")
    r = Newsroom(client).process(SHIFT, ANALYST)
    assert ("Analyst", "rejected") in agents_run(r["trace"]) and "41" not in r["analysis"]


def test_no_model_means_template_and_skipped_agents():
    r = Newsroom(None).process(SHIFT, CASUAL)
    assert r["source"] == "template"
    assert agents_run(r["trace"]) == [("Director", "ok"), ("Analyst", "skipped"), ("Writer", "skipped"), ("Fallback", "ok")]


def test_model_error_falls_back_and_is_traced():
    r = Newsroom(Scripted(TimeoutError("slow"))).process(GOAL, CASUAL)
    assert r["source"] == "template" and ("Analyst", "error") in agents_run(r["trace"])


def test_verifier_checks_the_language_script():
    client = Scripted("note", "Redcliffe take over.", "Redcliffe take over again.")
    r = Newsroom(client).process(SHIFT, {"mode": "casual", "language": "he"})
    assert r["source"] == "template" and "Hebrew" in r["note"]


def test_verifier_blocks_an_invented_mention_of_the_followed_player():
    client = Scripted("note", "Oscar Fitzroy is everywhere.", "Oscar Fitzroy is again everywhere.")
    r = Newsroom(client).process(SHIFT, {**CASUAL, "fav_player": "Oscar Fitzroy"})  # the player is not in this moment's facts
    assert r["source"] == "template" and "followed player" in r["note"]


def test_director_raises_priority_for_the_followed_club_and_player():
    nr = Newsroom(None)
    base = nr.process(STYLE, CASUAL)["plan"]
    club = nr.process(STYLE, {**CASUAL, "fav_club": "Northbridge FC"})["plan"]
    goal = nr.process(GOAL, {**CASUAL, "fav_player": "Oscar Fitzroy"})["plan"]
    assert club["priority"] > base["priority"] and club["angle"].startswith("club:")
    assert goal["priority"] == 1.0 and goal["angle"].startswith("player:") and goal["sentences"] == 2
    assert base["sentences"] == 1


def test_circuit_breaker_opens_after_three_failures_and_recovers():
    now = [0.0]
    cb = CircuitBreaker(3, 30.0, clock=lambda: now[0])
    for _ in range(3):
        assert cb.allow()
        cb.record(False)
    assert cb.is_open and not cb.allow()
    now[0] = 31.0
    assert cb.allow()  # half-open: one call goes through
    cb.record(True)
    assert not cb.is_open


def test_newsroom_stops_calling_a_failing_model():
    client = Scripted(*[TimeoutError("x")] * 20)
    nr = Newsroom(client)
    for _ in range(6):
        nr.process(SHIFT, CASUAL)
    assert nr.breaker.is_open
    calls = len(client.systems)
    r = nr.process(SHIFT, CASUAL)
    assert len(client.systems) == calls and r["source"] == "template"  # no new call while the circuit is open
    assert any("circuit open" in t["note"] for t in r["trace"])


def test_recap_has_its_own_trace():
    events = simulate_match(1, np.random.default_rng(3))[0]
    s = LiveSession(events, speed=0, narrator=Narrator("analyst", "en", client=None)).start()
    v = wait_for_recap(s)
    assert [t["agent"] for t in v["recap"]["trace"]] == ["Segmenter", "Writer+Verifier", "Fallback"]


def test_live_cards_carry_the_agent_trace_and_player_focus_cards_appear():
    events = simulate_match(1, np.random.default_rng(11))[0]
    star = max({e["player_name"] for e in events}, key=lambda n: sum(e["player_name"] == n and e["event_type"] == "pass" for e in events))
    s = LiveSession(events, speed=0, narrator=Narrator("analyst", "en", fav_player=star, client=None)).start()
    v = wait_for_recap(s)
    assert all(c["trace"] for c in v["cards"])
    assert any(c["moment"]["type"] == "player_focus" for c in v["cards"])
    assert v["focus_player"]["name"] == star and v["ticker"] and v["top_players"]["home"]
