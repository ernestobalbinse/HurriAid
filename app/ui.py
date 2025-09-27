# app/ui.py — Step 5: ADK on/off toggle + Communicator + History
import streamlit as st
from datetime import datetime
from urllib.parse import urlencode

from agents.coordinator import Coordinator

st.set_page_config(page_title="HurriAid", layout="wide")

# --- Sidebar ---
st.sidebar.title("HurriAid")
zip_code = st.sidebar.text_input("Enter ZIP code", value="33101", key="zip_input")
offline_mode = st.sidebar.toggle("Offline Mode", value=True, key="offline_toggle")
update_now = st.sidebar.button("Update Now")

# Explicit enable/disable for ADK (default: enabled)
use_adk_enabled = st.sidebar.toggle(
    "Use Google ADK",
    value=True,
    help="Enable or Disable use of Google ADK"
)

# Optional auto‑refresh
autorefresh_on = st.sidebar.toggle("Auto Refresh", value=False, help="Continuously re‑run to simulate a loop.", key="auto_refresh_toggle")
interval_sec = st.sidebar.slider("Refresh every (seconds)", 5, 60, 15, key="auto_refresh_interval")
if autorefresh_on:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=interval_sec * 1000, key="hurri_loop")
    except Exception:
        st.warning("Auto Refresh requires 'streamlit-autorefresh'. Run: pip install streamlit-autorefresh")

# --- Coordinator bootstrap ---
if ("coordinator" not in st.session_state) or (st.session_state.get("use_adk_enabled") != use_adk_enabled):
    st.session_state.coordinator = Coordinator(data_dir="data", use_adk_preferred=use_adk_enabled)
    st.session_state.use_adk_enabled = use_adk_enabled
coord = st.session_state.coordinator

# Change detection
zip_changed = (st.session_state.get("last_zip") != zip_code)

# --- Header ---
st.title("HurriAid")
st.write(f"Last opened: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# --- Run ---
should_run = ("last_result" not in st.session_state) or update_now or autorefresh_on or zip_changed
if should_run:
    result = coord.run_once(zip_code)
    st.session_state.last_result = result
    st.session_state.last_zip = zip_code
    st.session_state.last_run = datetime.now().strftime('%H:%M:%S')
    # Append to history
    hist = st.session_state.get("history", [])
    hist.append({
        "time": st.session_state.last_run,
        "zip": zip_code,
        "risk": (result.get("analysis") or {}).get("risk", "—"),
        "eta": (result.get("plan") or {}).get("eta_min", "—"),
    })
    st.session_state.history = hist[-12:]  # keep last 12 runs

result = st.session_state.get("last_result", {})
advisory = result.get("advisory", {})
analysis = result.get("analysis", {})
plan = result.get("plan")
checklist = result.get("checklist", [])
timings = result.get("timings_ms", {})
errors = result.get("errors", {})

# --- Metrics ---
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

st.subheader("Risk")
if analysis:
    if "distance_km" in analysis:
        st.write(
            f"ZIP **{zip_code}** risk: **{analysis['risk']}** — distance to advisory center: {analysis['distance_km']:.1f} km."
        )
    st.caption(analysis.get("reason", ""))
else:
    st.info("Risk analysis unavailable.")

st.subheader("Route")
if plan:
    st.success(f"Nearest open shelter: {plan['name']} ({plan['distance_km']:.1f} km, {plan['eta_min']} min)")
    params = {"api": 1, "destination": f"{plan['lat']},{plan['lon']}"}
    maps_url = "https://www.google.com/maps/dir/?" + urlencode(params)
    st.markdown(f"[Open route in Google Maps]({maps_url})")
else:
    st.info("No open shelters found.")

st.subheader("Checklist (Risk‑aware)")
if checklist:
    st.write("\n".join(f"- {it}" for it in checklist))
else:
    st.write("- Water (3 days)\n- Non-perishable food\n- Medications\n- Flashlight & batteries\n- First aid kit\n- Important documents in a waterproof bag")

st.subheader("Agent Status")
status_lines = [
    f"Watcher: {timings.get('watcher_ms', '—')} ms" + (f" | ERROR: {errors['watcher']}" if 'watcher' in errors else ""),
    f"Analyzer: {timings.get('analyzer_ms', '—')} ms" + (f" | ERROR: {errors['analyzer']}" if 'analyzer' in errors else ""),
    f"Planner: {timings.get('planner_ms', '—')} ms" + (f" | ERROR: {errors['planner']}" if 'planner' in errors else ""),
    f"Parallel: {timings.get('parallel_ms', '—')} ms",
    f"Total: {timings.get('total_ms', '—')} ms (ran at {st.session_state.get('last_run', '—')})"
]
st.code("\n".join(status_lines), language="text")

st.subheader("Run History")
hist = st.session_state.get("history", [])
if hist:
    st.table(hist)
else:
    st.caption("No runs yet.")
