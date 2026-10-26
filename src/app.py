"""Dashboard: a match plays live, with momentum, match state and story cards. Run:

  streamlit run src/app.py

Casual mode shows plain-language stories and a simple "who is on top" view. Analyst mode adds the numbers, the
reasons behind each moment and the chaos index. A match is simulated in memory, or read from a data/ folder.
The engine and the model calls run in a background thread (live.py); this page only draws what it finds there.
"""

import sys  # to find sibling modules
from pathlib import Path  # file paths

import altair as alt  # charts with goal markers
import pandas as pd  # tables for the charts
import streamlit as st  # the dashboard

sys.path.insert(0, str(Path(__file__).parent))  # let us import files from src/
from live import LiveSession  # the live path
from eventhub import EventHubFeed, configured as eventhub_configured, roundtrip_feed  # optional Azure Event Hubs source
from narrator import LANGUAGES, Narrator, make_client  # stories
from run_live import simulated_events  # one simulated match
from streamer import load_events  # events from a data folder

st.set_page_config(page_title="Momentum & Chaos Engine", page_icon="⚽", layout="wide")

STATE_LABEL = {  # plain-language match state, per team
    "controlled_dominance": ("Controlled dominance", "green"), "chaotic_pressure": ("Chaotic pressure", "orange"),
    "being_read": ("Being read", "red"), "scrambling": ("Scrambling", "violet"), "balanced": ("Balanced", "gray"),
}
ICON = {"goal": "⚽", "big_chance": "🎯", "momentum_shift": "🔥", "style_change": "🧠", "player_focus": "⭐"}
TITLE = {"goal": "Goal", "big_chance": "Big chance", "momentum_shift": "Momentum shift", "style_change": "Change of style", "player_focus": "Your player"}
BADGE = {"long_pass": "🎯 long pass", "hard_pass": "🧩 hard pass", "fast_ball": "💨 fast ball", "power_shot": "🚀 power shot", "goal": "⚽ goal",
         "big_chance": "🎯 big chance", "milestone": "🏅 milestone"}
HOME_C, AWAY_C = "#3d9df3", "#f5b301"  # one colour per team everywhere: blue = home, yellow = away
DOT = {"home": ":blue[\u25cf]", "away": ":yellow[\u25cf]"}
STATUS = {"ok": "✓", "skipped": "–", "rejected": "✗", "error": "!"}


@st.cache_resource
def get_client():  # one Azure OpenAI client for the whole app, or None
    return make_client()


@st.cache_data
def sim_events(seed):  # a simulated match, cached per seed
    return simulated_events(seed)


def pick_lists(source, seed, folder, match_id):  # teams and players of the chosen match, for the favourite pickers
    try:
        if source in ("Simulated match", HUB):
            events = sim_events(int(seed))
        elif source == "data/ folder":
            events = load_events(folder, int(match_id))
        else:
            return [], []  # a live feed: names are not known before it starts
    except Exception:
        return [], []
    teams = sorted({e["team_name"] for e in events if e.get("team_name")})
    players = sorted({e["player_name"] for e in events if e.get("player_name")})
    return teams, players


# ---------- sidebar ----------

with st.sidebar:
    st.header("Match")
    HUB = "Simulated match via Azure Event Hubs"
    sources = ["Simulated match", "data/ folder"] + ([HUB] if eventhub_configured() else [])
    source = st.radio("Source", sources, horizontal=True)
    if source in ("Simulated match", HUB):
        seed = st.number_input("Match number (seed)", 1, 9999, 7)
        if source == HUB:
            st.caption("The match is sent into Azure Event Hubs and the dashboard reads it back from there.")
    else:
        folder = st.text_input("Data folder", "data")
        match_id = st.number_input("Match id", 1, 999, 1)
    speed = st.number_input("Speed (x real time)", 1, 120, 30, key="speed", help="Higher = faster. 30 = a match in about 3 minutes, 120 = under a minute")
    st.header("Viewer")
    mode_label = st.radio("Mode", ["Casual fan", "Analyst"], horizontal=True)
    mode = "casual" if mode_label == "Casual fan" else "analyst"
    teams, players = pick_lists(source, locals().get("seed"), locals().get("folder"), locals().get("match_id"))
    fav_club = st.selectbox("Favourite club", [None] + teams, key="fav_club", format_func=lambda t: "(none)" if t is None else t)
    fav_player = st.selectbox("Favourite player", [None] + players, key="fav_player", format_func=lambda t: "(none)" if t is None else t)
    c1, c2 = st.columns(2)
    start = c1.button("▶ Start", width="stretch", type="primary")
    stop = c2.button("■ Stop", width="stretch")
    client = get_client()
    st.caption("Stories: " + ("Azure OpenAI, with template fallback" if client else "templates only (set AZURE_OPENAI_* in .env to use the model)"))
    language = st.selectbox("Language", list(LANGUAGES), format_func=lambda c: f"{LANGUAGES[c]} ({c})", key="language")  # last in the sidebar

if start:  # a new match
    if st.session_state.get("session"):
        st.session_state.session.stop()
    try:
        if source == HUB:
            events, play_speed = roundtrip_feed(sim_events(int(seed)), float(speed)), 0.0  # paced when published; the feed is already live
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


def goals_of(v):  # every goal so far: minute, side, scorer, score after it
    return [{"minute": c["moment"]["elapsed_minute"], "side": c["moment"]["team"], "team": c["moment"]["team_name"],
             "player": (c["moment"].get("facts") or {}).get("player", ""), "score": f"{c['moment']['score']['home']}-{c['moment']['score']['away']}"}
            for c in v["cards"] if c["moment"]["type"] == "goal"]


def value_at(hist, minute, key):  # the history value at (or just before) a minute
    rows = [h for h in hist if h["minute"] <= minute]
    return rows[-1][key] if rows else 0.0


def goal_layer(goals, ykey, hist, home, away):  # dashed line + ball marker at each goal, in the scoring team's colour
    if not goals:
        return None
    g = pd.DataFrame([{**x, "y": value_at(hist, x["minute"], ykey(x["side"])),
                       "label": f"{x['minute']}' {x['player'] or 'Goal'} ({x['team']}) {x['score']}"} for x in goals])
    col = alt.Color("team:N", scale=alt.Scale(domain=[home, away], range=[HOME_C, AWAY_C]), legend=None)
    tip = ["label"]
    x = alt.X("minute:Q")
    rule = alt.Chart(g).mark_rule(strokeDash=[5, 3], strokeWidth=2.5, opacity=0.9).encode(x=x, color=col, tooltip=tip)
    pts = alt.Chart(g).mark_point(shape="circle", size=230, filled=True, stroke="white", strokeWidth=2).encode(x=x, y="y:Q", color=col, tooltip=tip)
    ball = alt.Chart(g).mark_text(text="\u26bd", dy=-17, fontSize=17).encode(x=x, y="y:Q", tooltip=tip)
    return rule + pts + ball


def momentum_chart(df, goals, hist, home, away):
    lim = max(10, int(df["momentum"].abs().max()) + 2)
    x = alt.X("minute:Q", scale=alt.Scale(domain=[0, 95]), title="Minute")
    y = alt.Y("v:Q", scale=alt.Scale(domain=[-lim, lim]), title="Momentum")
    base = alt.Chart(df).encode(x=x)
    up = base.transform_calculate(v="max(datum.momentum, 0)").mark_area(opacity=0.6, color=HOME_C, interpolate="monotone").encode(y=y)
    dn = base.transform_calculate(v="min(datum.momentum, 0)").mark_area(opacity=0.6, color=AWAY_C, interpolate="monotone").encode(y=y)
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="gray", opacity=0.6).encode(y="y:Q")
    notes = pd.DataFrame({"x": [1, 1], "y": [lim - 1.2, -lim + 1.2], "t": [f"▲ {home} on top", f"▼ {away} on top"], "c": [HOME_C, AWAY_C]})
    labels = alt.Chart(notes).mark_text(align="left", fontSize=12, fontWeight="bold").encode(x="x:Q", y="y:Q", text="t:N", color=alt.Color("c:N", scale=None))
    layers = [up, dn, zero, labels, goal_layer(goals, lambda s: "momentum", hist, home, away)]
    return alt.layer(*[l for l in layers if l is not None]).properties(height=240)


def chaos_chart(df, goals, hist, home, away):
    long = pd.concat([df[["minute", "home_chaos"]].rename(columns={"home_chaos": "chaos"}).assign(team=home),
                      df[["minute", "away_chaos"]].rename(columns={"away_chaos": "chaos"}).assign(team=away)])
    col = alt.Color("team:N", scale=alt.Scale(domain=[home, away], range=[HOME_C, AWAY_C]), legend=alt.Legend(orient="top", title=None))
    lines = alt.Chart(long).mark_line(strokeWidth=2.5, interpolate="monotone").encode(
        x=alt.X("minute:Q", scale=alt.Scale(domain=[0, 95]), title="Minute"), y=alt.Y("chaos:Q", title="Chaos index (higher = less predictable)"), color=col)
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="gray", opacity=0.6).encode(y="y:Q")
    layers = [lines, zero, goal_layer(goals, lambda s: f"{s}_chaos", hist, home, away)]
    return alt.layer(*[l for l in layers if l is not None]).properties(height=220)


def tug_of_war(momentum, home, away):  # one bar: who has the upper hand right now
    share = min(95, max(5, 50 + 5 * momentum))
    st.markdown(f"<div style='display:flex;height:14px;border-radius:7px;overflow:hidden;margin:2px 0 4px'>"
                f"<div style='width:{share:.0f}%;background:{HOME_C}'></div><div style='width:{100 - share:.0f}%;background:{AWAY_C}'></div></div>"
                f"<div style='display:flex;justify-content:space-between;font-size:.8rem;opacity:.8'><span>{home}</span><span>{away}</span></div>",
                unsafe_allow_html=True)


def render():
    v = session.view()
    snap, names, hist = v["snapshot"], v["names"], v["history"]
    home, away = names.get("home", "Home"), names.get("away", "Away")
    if v["error"]:
        st.error(v["error"])

    a, b, c = st.columns([2, 1, 2])
    a.metric(f"{DOT['home']} {home}", snap["score"]["home"])
    b.metric("Minute", f"{snap['elapsed_minute']}'")
    c.metric(f"{DOT['away']} {away}", snap["score"]["away"])
    tug_of_war(snap["momentum"], home, away)
    goals = goals_of(v)
    if goals:
        st.markdown("  \n".join(f"\u26bd `{g['minute']}'` {DOT[g['side']]} **{g['player'] or g['team']}** ({g['team']}) \u00b7 {g['score']}" for g in goals))

    on_top = {1: f"{home} on top", -1: f"{away} on top", 0: "Evenly balanced"}[snap["regime"]]
    left, right = st.columns([3, 2])
    with left:
        st.subheader(on_top + (f"  ({snap['momentum']:+.1f})" if mode == "analyst" else ""))
        st.markdown(f"{home}: {team_tag(v, 'home')}  ·  {away}: {team_tag(v, 'away')}")
        if hist:
            df = pd.DataFrame(hist).set_index("minute")
            st.caption(f"Momentum: {DOT['home']} above zero = {home}, {DOT['away']} below zero = {away}. \u26bd and the dashed line mark a goal, in the scorer's colour.")
            st.altair_chart(momentum_chart(df.reset_index(), goals, hist, home, away), width="stretch")
            if mode == "analyst":
                st.caption("Chaos index per team (higher = less predictable passing, vs the team's own average). Goals are marked on the scorer's line.")
                st.altair_chart(chaos_chart(df.reset_index(), goals, hist, home, away), width="stretch")
        fp = v["focus_player"]
        if fp:  # player-focused mode: the followed player's numbers
            st.subheader(f"Following {fp['name']} ({fp['position']})")
            f1, f2, f3, f4 = st.columns(4)
            f1.metric("Passes", f"{fp['complete']}/{fp['passes']}", f"{fp['accuracy_pct']}% accurate", delta_color="off")
            f2.metric("Shots", fp["shots"], f"{fp['goals']} goal(s)" if fp["goals"] else None, delta_color="off")
            f3.metric("Longest pass", f"{fp['longest']:.0f} m")
            f4.metric("Top ball speed", f"{fp['top_speed']:.0f} km/h")
        elif session.players.focus:
            st.caption(f"Waiting for {session.players.focus} to appear in the match…")
        st.subheader("Player tags")
        tags = v["ticker"][-8:][::-1] if mode == "analyst" else v["notable"][-5:][::-1]
        if not tags:
            st.caption("Name tags will appear here.")
        for o in tags:
            badges = "  ".join(BADGE.get(b, b) for b in o["badges"])
            st.markdown(f"`{o['minute']}'` **{o['name']}** ({o['position']}) · {o['text'].split(' · ', 1)[1] if mode == 'analyst' else ''} {badges}")
        if mode == "analyst":
            st.caption("Most involved players")
            rows = [{"team": names.get(t, t), "player": r["name"], "pos": r["position"], "events": r["involvement"], "pass acc %": r["accuracy_pct"],
                     "avg difficulty": r["avg_difficulty"], "top speed km/h": round(r["top_speed"])} for t in ("home", "away") for r in v["top_players"][t][:3]]
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            t = v["totals"]
            st.dataframe(pd.DataFrame({home: [t["home"]["shots"], round(t["home"]["xg"], 2), t["home"]["complete"], t["home"]["passes"]],
                                       away: [t["away"]["shots"], round(t["away"]["xg"], 2), t["away"]["complete"], t["away"]["passes"]]},
                                      index=["Shots", "xG", "Passes completed", "Passes attempted"]), width="stretch")
    with right:
        st.subheader("Insights")
        if v["breaker_open"]:
            st.warning("The model is failing, so stories use templates for a moment (circuit breaker open).")
        if v["finished"] and v["recap"]:
            with st.container(border=True):
                st.markdown("**Full time**")
                st.write(v["recap"]["text"])
                if mode == "analyst":
                    st.caption("Agents: " + "  →  ".join(f"{t['agent']} {STATUS.get(t['status'], '?')}" for t in v["recap"]["trace"]))
        elif v["finished"]:
            st.caption("Writing the recap…")
        if not v["cards"]:
            st.caption("Key moments will appear here.")
        for card in reversed(v["cards"]):
            m = card["moment"]
            with st.container(border=True):
                st.markdown(f"{ICON[m['type']]} {DOT.get(m['team'], '')} **{m['elapsed_minute']}'  {TITLE[m['type']]}**")
                st.write(card["text"])
                if mode == "analyst":
                    with st.expander("Why it matters"):
                        for d in m["drivers"]:
                            st.write("• " + d)
                        st.caption(f"momentum {m['momentum']:+.1f} · chaos difference {m['chaos_diff']:+.2f} · written by "
                                   + ("Azure OpenAI" if card["source"] == "llm" else "template") + (f" · {card['note']}" if card["note"] else ""))
                        if card.get("analysis"):
                            st.caption(f"Analyst note: {card['analysis']}")
                        if card.get("trace"):
                            st.caption("Agents: " + "  →  ".join(f"{t['agent']} {STATUS.get(t['status'], '?')}" for t in card["trace"])
                                       + (f"  ·  priority {card['plan']['priority']}" if card.get("plan") else ""))


running = bool(session.thread and session.thread.is_alive())
st.fragment(run_every=1 if running else None)(render)()
