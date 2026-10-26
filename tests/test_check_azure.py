"""Tests for the Azure check script (fake model, no network). Run:  python -m pytest tests -q"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from check_azure import check_openai, check_story, hint_for, missing  # noqa: E402


class Fake:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=a))])


class AuthenticationError(Exception):  # same class name as the SDK's, so the hint lookup is exercised
    pass


class NotFoundError(Exception):
    pass


def test_missing_lists_only_unset_or_empty_names():
    assert missing(("A", "B", "C"), {"A": "x", "B": ""}) == ["B", "C"]
    assert missing(("A",), {"A": "x"}) == []


def test_hints_name_the_likely_cause():
    assert "key" in hint_for(AuthenticationError("401")).lower()
    assert "deployment" in hint_for(NotFoundError("404")).lower()
    assert "Unexpected" in hint_for(ValueError("?"))


def test_check_openai_reports_success_and_failures():
    ok, msg = check_openai(Fake("OK"), "narrator")
    assert ok and "OK" in msg
    ok, msg = check_openai(Fake(AuthenticationError("bad key")), "narrator")
    assert not ok and "AuthenticationError" in msg and "key" in msg.lower()
    ok, msg = check_openai(Fake(""), "narrator")
    assert not ok and "empty" in msg


def test_check_story_runs_the_agents_and_returns_the_trace():
    client = Fake("Redcliffe are winning the duels, 6 high regains to 1.", "Momentum swings to Redcliffe Town at 63' after 6 high regains to 1.")
    ok, text, trace = check_story(client)
    assert ok and text.startswith("Momentum swings") and trace.startswith("Director ok  ->  Analyst ok")


def test_check_story_fails_when_the_model_errors():
    ok, text, trace = check_story(Fake(TimeoutError("slow")))
    assert not ok and "Fallback" in trace
