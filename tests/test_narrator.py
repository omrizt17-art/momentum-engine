"""Tests for the narrator, the verifier, the recap and the live session. No network: the model is a fake client.

Run:  python -m pytest tests -q
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from generate_matches import simulate_match  # noqa: E402
from live import LiveSession, episodes  # noqa: E402
from narrator import DEMO, Narrator, template_text, verify  # noqa: E402
from recap import build_recap, recap_facts  # noqa: E402

SHIFT, GOAL, STYLE = DEMO  # three sample moments


class FakeClient:
    """Stands in for AzureOpenAI: returns the next prepared answer, or raises it if it is an exception."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=a))])


def test_verifier_accepts_facts_and_rejects_inventions():
    assert verify("Momentum swings to Redcliffe Town at 63', driven by 6 high regains to 1 (-8.4).", SHIFT, "analyst") is None
    assert verify("Redcliffe have 17 shots.", SHIFT, "analyst") is not None  # 9 is not in the facts
    assert verify("Redcliffe take over after 4 big chances.", SHIFT, "casual") is not None  # casual: no numbers
    assert verify("Redcliffe take over just after the hour (63').", SHIFT, "casual") is None  # the minute is allowed


@pytest.mark.parametrize("lang", ["en", "he", "es"])
@pytest.mark.parametrize("mode", ["casual", "analyst"])
def test_templates_pass_their_own_verifier(lang, mode):
    for m in DEMO:
        text = template_text(m, mode, lang)
        assert text and "{" not in text
        if lang == "en":  # Hebrew and Spanish use the same digits, so the check holds for them too
            assert verify(text, m, mode) is None, text


def test_casual_and_analyst_texts_differ():
    assert template_text(SHIFT, "casual", "en") != template_text(SHIFT, "analyst", "en")
    assert not any(ch.isdigit() for ch in template_text(SHIFT, "casual", "en"))


def test_good_model_answer_is_used():
    n = Narrator("analyst", "en", client=FakeClient("Momentum swings to Redcliffe Town at 63' (-8.4) after 6 high regains to 1."))
    r = n.narrate(SHIFT)
    assert r["source"] == "llm"


def test_writer_gets_one_retry_after_a_rejection():
    client = FakeClient("Redcliffe have 17 shots.", "Redcliffe Town take over at 63'.")
    r = Narrator("analyst", "en", client=client).narrate(SHIFT)
    assert r["source"] == "llm" and client.calls == 2


def test_two_rejections_fall_back_to_the_template():
    client = FakeClient("17 shots!", "23 shots!")
    r = Narrator("analyst", "en", client=client).narrate(SHIFT)
    assert r["source"] == "template" and "verifier" in r["note"]


def test_model_error_falls_back_to_the_template():
    r = Narrator("casual", "he", client=FakeClient(TimeoutError("slow"))).narrate(GOAL)
    assert r["source"] == "template" and "TimeoutError" in r["note"]


def test_unknown_language_falls_back_to_english_template():
    r = Narrator("casual", "fr", client=None).narrate(GOAL)
    assert r["source"] == "template" and r["language"] == "en" and "fr" in r["note"]


def test_episodes_need_three_minutes():
    hist = [{"minute": i, "regime": r} for i, r in enumerate([0, 1, 1, 1, 1, 0, -1, -1, 0, 0])]
    assert episodes(hist) == [(1, 5, "home")]  # the 2-minute away spell is dropped


@pytest.fixture(scope="module")
def finished_session():
    events = simulate_match(1, np.random.default_rng(11))[0]
    s = LiveSession(events, speed=0, narrator=Narrator("analyst", "en", client=None)).start()
    for _ in range(300):
        v = s.view()
        if v["finished"] and v["recap"]:
            break
        time.sleep(0.1)
    return s


def test_live_session_finishes_with_cards_and_recap(finished_session):
    v = finished_session.view()
    assert v["error"] is None and v["finished"] and v["recap"]
    assert v["cards"] and all(not c["pending"] for c in v["cards"])
    assert len(v["history"]) > 80  # about one entry per minute


def test_recap_mentions_the_final_score(finished_session):
    v = finished_session.view()
    s = v["snapshot"]["score"]
    assert f"{s['home']}-{s['away']}" in v["recap"]["text"]
    assert recap_facts(v)["score"] == s


def test_set_prefs_retells_cards_and_recap(finished_session):
    finished_session.set_prefs(mode="casual", language="he")
    for _ in range(100):
        v = finished_session.view()
        if v["recap"]["language"] == "he" and all(c["text"] == template_text(c["moment"], "casual", "he") for c in v["cards"]):
            break
        time.sleep(0.1)
    assert v["recap"]["language"] == "he" and v["recap"]["mode"] == "casual"
    assert all(c["text"] == template_text(c["moment"], "casual", "he") for c in v["cards"])
