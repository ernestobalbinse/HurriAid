import streamlit as st
from datetime import datetime
from urllib.parse import urlencode
from streamlit_autorefresh import st_autorefresh


from agents.coordinator import Coordinator

st.set_page_config(page_title="HurriAid", layout="wide")


# --- Sidebar ---
st.sidebar.title("HurriAid")
zip_code = st.sidebar.text_input("Enter ZIP code", value="33101", key="zip_input")
offline_mode = st.sidebar.toggle("Offline Mode", value=True, key="offline_toggle")
update_now = st.sidebar.button("Update Now")


st.sidebar.caption(
"Tip: Offline uses local JSON and parallel agents."
)

# Auto‑refresh controls
autorefresh_on = st.sidebar.toggle("Auto Refresh", value=False, help="Continuously re‑run to simulate a loop.", key="auto_refresh_toggle")
interval_sec = st.sidebar.slider("Refresh every (seconds)", 5, 60, 15, key="auto_refresh_interval")


if autorefresh_on:
    try:
        from streamlit_autorefresh import st_autorefresh # pip install streamlit-autorefresh
        st_autorefresh(interval=interval_sec * 1000, key="hurri_loop")
    except Exception:
        st.warning("Auto Refresh requires 'streamlit-autorefresh'. Run: pip install streamlit-autorefresh")

# --- Coordinator bootstrap ---
if "coordinator" not in st.session_state:
    st.session_state.coordinator = Coordinator(data_dir="data", max_workers=3)
coord = st.session_state.coordinator

# Track last inputs to detect changes (so typing a new ZIP triggers a run)
last_zip = st.session_state.get("last_zip")
last_offline = st.session_state.get("last_offline")
zip_changed = (last_zip != zip_code)
offline_changed = (last_offline != offline_mode)

# --- Header / Status ---
st.title("HurriAid")
st.write(f"Last opened: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# --- Run agents (on load, on Update, on AutoRefresh, or if ZIP/Offline changed) ---
should_run = (
("last_result" not in st.session_state)
or update_now
or autorefresh_on
or zip_changed
or offline_changed
)

if should_run:
    st.session_state.last_result = coord.run_once(zip_code)
st.session_state.last_run = datetime.now().strftime('%H:%M:%S')
st.session_state.last_zip = zip_code
st.session_state.last_offline = offline_mode


result = st.session_state.get("last_result", {})
advisory = result.get("advisory", {})
analysis = result.get("analysis", {})
plan = result.get("plan")
timings = result.get("timings_ms", {})
errors = result.get("errors", {})

# --- Status Metrics ---
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Advisory", advisory.get("category", "—"))
with col2:
    st.metric("Location Risk", analysis.get("risk", "—") if analysis else "—")
with col3:
    st.metric("Nearest Shelter", plan.get("name") if plan else "None open")
with col4:
    st.metric("ETA (min)", plan.get("eta_min") if plan else "—")

# --- Panels ---
st.subheader("Advisory")
if advisory:
    st.json(advisory)
else:
    st.info("Advisory data unavailable.")

st.subheader("Shelters")
if plan:
    st.success(f"Nearest open shelter: {plan['name']} ({plan['distance_km']:.1f} km, {plan['eta_min']} min)")
    # Google Maps driving link
    params = {
        "api": 1,
        "destination": f"{plan['lat']},{plan['lon']}"
    }
    maps_url = "https://www.google.com/maps/dir/?" + urlencode(params)
    st.markdown(f"[Open route in Google Maps]({maps_url})")
else:
    st.info("No open shelters found.")

st.subheader("Risk")
if analysis:
    if "distance_km" in analysis:
        st.write(
            f"ZIP **{zip_code}** risk: **{analysis['risk']}** — "
            f"distance to advisory center: {analysis['distance_km']:.1f} km."
        )
    st.caption(analysis.get("reason", ""))
else:
    st.info("Risk analysis unavailable.")

st.subheader("Route")
if plan:
    st.write("Planner computed an ETA using a constant driving speed (demo assumption).")
else:
    st.info("Planner could not find an open shelter.")

st.subheader("Checklist")
st.write(
    "- Water (3 days)"
    "\n- Non-perishable food (3 days)"
    "\n- Medications (7 days)"
    "\n- Flashlight & batteries (1 set)"
    "\n- First aid kit (1)"
    "\n- Important documents in a waterproof bag"
)


st.subheader("Agent Status")
status_lines = [
    f"Watcher: {timings.get('watcher_ms', '—')} ms" + (f" | ERROR: {errors['watcher']}" if 'watcher' in errors else ""),
    f"Analyzer: {timings.get('analyzer_ms', '—')} ms" + (f" | ERROR: {errors['analyzer']}" if 'analyzer' in errors else ""),
    f"Planner: {timings.get('planner_ms', '—')} ms" + (f" | ERROR: {errors['planner']}" if 'planner' in errors else ""),
    f"Total: {timings.get('total_ms', '—')} ms (ran at {st.session_state.get('last_run', '—')})"
]
st.code("\n".join(status_lines), language="text")
