"""Dashboard: a match plays live, with momentum, match state and story cards. Run:

  streamlit run src/app.py

Casual mode shows plain-language stories and a simple "who is on top" view. Analyst mode adds the numbers, the
reasons behind each moment and the chaos index. A match is simulated in memory, or read from a data/ folder.
The engine and the model calls run in a background thread (live.py); this page only draws what it finds there.
"""

import sys  # to find sibling modules
from pathlib import Path  # file paths

import pandas as pd  # tables for the charts
import streamlit as st  # the dashboard

sys.path.insert(0, str(Path(__file__).parent))  # let us import files from src/
from live import LiveSession  # the live path
from eventhub import EventHubFeed, configured as eventhub_configured  # optional Azure Event Hubs source
from narrator import LANGUAGES, Narrator, make_client  # stories
from run_live import simulated_events  # one simulated match
from streamer import load_events  # events from a data folder

st.set_page_config(page_title="Momentum & Chaos Engine", page_icon="⚽", layout="wide")

STATE_LABEL = {  # plain-language match state, per team
    "controlled_dominance": ("Controlled dominance", "green"), "chaotic_pressure": ("Chaotic pressure", "orange"),
    "being_read": ("Being read", "red"), "scrambling": ("Scrambling", "violet"), "balanced": ("Balanced", "gray"),
}
ICON = {"goal": "⚽", "big_chance": "🎯", "momentum_shift": "🔥", "style_change": "🧠"}
TITLE = {"goal": "Goal", "big_chance": "Big chance", "momentum_shift": "Momentum shift", "style_change": "Change of style"}


@st.cache_resource
def get_client():  # one Azure OpenAI client for the whole app, or None
    return make_client()


@st.cache_data
def sim_events(seed):  # a simulated match, cached per seed
    return simulated_events(seed)


# ---------- sidebar ----------

with st.sidebar:
    st.header("Match")
    sources = ["Simulated match", "data/ folder"] + (["Azure Event Hubs"] if eventhub_configured() else [])
    source = st.radio("Source", sources, horizontal=True)
    if source == "Simulated match":
        seed = st.number_input("Match number (seed)", 1, 9999, 7)
    elif source == "Azure Event Hubs":
        hub_match = st.number_input("Match id", 1, 999, 1)
        st.caption("Reads a match that `streamer.py --publish eventhubs` is sending. Speed is set by the publisher.")
    else:
        folder = st.text_input("Data folder", "data")
        match_id = st.number_input("Match id", 1, 999, 1)
    speed = st.slider("Speed (x real time)", 1, 120, 30, help="30 = a match in about 3 minutes")
    st.header("Viewer")
    mode_label = st.radio("Mode", ["Casual fan", "Analyst"], horizontal=True)
    mode = "casual" if mode_label == "Casual fan" else "analyst"
    language = st.selectbox("Language", list(LANGUAGES), format_func=lambda c: f"{LANGUAGES[c]} ({c})")
    fav_club = st.text_input("Favourite club", "", placeholder="e.g. Redcliffe Town") or None
    fav_player = st.text_input("Favourite player", "", placeholder="e.g. Oscar Fitzroy") or None
    c1, c2 = st.columns(2)
    start = c1.button("▶ Start", use_container_width=True, type="primary")
    stop = c2.button("■ Stop", use_container_width=True)
    client = get_client()
    st.caption("Stories: " + ("Azure OpenAI, with template fallback" if client else "templates only (set AZURE_OPENAI_* in .env to use the model)"))

if start:  # a new match
    if st.session_state.get("session"):
        st.session_state.session.stop()
    try:
        if source == "Azure Event Hubs":
            events, play_speed = EventHubFeed(int(hub_match)), 0.0  # already paced by the publisher
        else:
            events, play_speed = (sim_events(int(seed)) if source == "Simulated match" else load_events(folder, int(match_id))), float(speed)
        st.session_state.session = LiveSession(events, play_speed, Narrator(mode, language, fav_club, fav_player, client=client)).start()
    except Exception as e:
        st.error(f"Could not load the match: {e}")
if stop and st.session_state.get("session"):
    st.session_state.session.stop()

session = st.session_state.get("session")
if session:  # viewer settings apply to the running match straight away
    session.set_prefs(mode, language, fav_club, fav_player)

st.title("Momentum & Chaos Engine")
st.caption("Not just who has momentum, but what kind: control or chaos.")

if not session:
    st.info("Choose a match in the sidebar and press **Start**.")
    st.stop()


# ---------- live view ----------

def team_tag(v, team):  # coloured state label for one team
    label, colour = STATE_LABEL[v["snapshot"][team]["state"]]
    return f":{colour}-badge[{label}]"


def render():
    v = session.view()
    snap, names, hist = v["snapshot"], v["names"], v["history"]
    home, away = names.get("home", "Home"), names.get("away", "Away")
    if v["error"]:
        st.error(v["error"])

    a, b, c = st.columns([2, 1, 2])
    a.metric(home, snap["score"]["home"])
    b.metric("Minute", f"{snap['elapsed_minute']}'")
    c.metric(away, snap["score"]["away"])

    on_top = {1: f"{home} on top", -1: f"{away} on top", 0: "Evenly balanced"}[snap["regime"]]
    left, right = st.columns([3, 2])
    with left:
        st.subheader(on_top + (f"  ({snap['momentum']:+.1f})" if mode == "analyst" else ""))
        st.markdown(f"{home}: {team_tag(v, 'home')}  ·  {away}: {team_tag(v, 'away')}")
        if hist:
            df = pd.DataFrame(hist).set_index("minute")
            st.caption(f"Momentum: above zero = {home}, below zero = {away}")
            st.line_chart(df[["momentum"]], height=220)
            if mode == "analyst":
                st.caption("Chaos index per team (higher = less predictable passing, vs the team's own average)")
                st.line_chart(df[["home_chaos", "away_chaos"]].rename(columns={"home_chaos": home, "away_chaos": away}), height=200)
        if mode == "analyst":
            t = v["totals"]
            st.dataframe(pd.DataFrame({home: [t["home"]["shots"], round(t["home"]["xg"], 2), t["home"]["complete"], t["home"]["passes"]],
                                       away: [t["away"]["shots"], round(t["away"]["xg"], 2), t["away"]["complete"], t["away"]["passes"]]},
                                      index=["Shots", "xG", "Passes completed", "Passes attempted"]), use_container_width=True)
    with right:
        st.subheader("Insights")
        if v["finished"] and v["recap"]:
            with st.container(border=True):
                st.markdown("**Full time**")
                st.write(v["recap"]["text"])
        elif v["finished"]:
            st.caption("Writing the recap…")
        if not v["cards"]:
            st.caption("Key moments will appear here.")
        for card in reversed(v["cards"]):
            m = card["moment"]
            with st.container(border=True):
                st.markdown(f"{ICON[m['type']]} **{m['elapsed_minute']}'  {TITLE[m['type']]}**")
                st.write(card["text"])
                if mode == "analyst":
                    with st.expander("Why it matters"):
                        for d in m["drivers"]:
                            st.write("• " + d)
                        st.caption(f"momentum {m['momentum']:+.1f} · chaos difference {m['chaos_diff']:+.2f} · written by "
                                   + ("Azure OpenAI" if card["source"] == "llm" else "template") + (f" · {card['note']}" if card["note"] else ""))


running = bool(session.thread and session.thread.is_alive())
st.fragment(run_every=1 if running else None)(render)()
