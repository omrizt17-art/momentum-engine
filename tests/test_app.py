"""Headless test of the Streamlit dashboard (no browser). Run:  python -m pytest tests -q"""

import time
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "src" / "app.py")


def test_idle_page_asks_for_a_match():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("Start" in i.value for i in at.info)


@pytest.mark.parametrize("mode", ["Casual fan", "Analyst"])
def test_a_match_plays_in_both_modes(mode):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.slider[0].set_value(120)
    at.radio[1].set_value(mode)
    at.button[0].click().run()
    time.sleep(3)
    at.run()
    assert not at.exception
    assert [m.label for m in at.metric][1] == "Minute"
    at.button[1].click().run()  # Stop
    assert not at.exception


def test_language_switch_does_not_break_the_page():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.slider[0].set_value(120)
    at.button[0].click().run()
    time.sleep(2)
    at.selectbox[0].set_value("he").run()
    assert not at.exception
    at.button[1].click().run()


def test_player_focus_and_name_tags_render_in_analyst_mode():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.slider[0].set_value(120)
    at.radio[1].set_value("Analyst")
    at.text_input[1].set_value("Nobody Atall")  # favourite player that is not in the match
    at.button[0].click().run()
    time.sleep(3)
    at.run()
    assert not at.exception
    assert any("Player tags" in s.value for s in at.subheader)
    assert any("Waiting for Nobody Atall" in c.value for c in at.caption)
    at.button[1].click().run()
