import pydeck as pdk
from tools.geo import circle_polygon
import streamlit as st
from datetime import datetime
from urllib.parse import urlencode

from agents.coordinator import Coordinator
from core.utils import load_history

st.set_page_config(page_title="HurriAid", layout="wide")

# --- Sidebar ---
st.sidebar.title("HurriAid")
zip_code = st.sidebar.text_input("Enter ZIP code", value="33101", key="zip_input")
offline_mode = st.sidebar.toggle("Offline Mode", value=True, key="offline_toggle")
update_now = st.sidebar.button("Update Now")
use_adk_enabled = st.sidebar.toggle("Use Google ADK", value=True, help="Enable or Disable use of Google ADK")

# Optional auto‑refresh
autorefresh_on = st.sidebar.toggle("Auto Refresh", value=False, help="Continuously re‑run to simulate a loop.", key="auto_refresh_toggle")
interval_sec = st.sidebar.slider("Refresh every second", 5, 60, 15, key="auto_refresh_interval")
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

# Preload persisted history
if "persisted_history" not in st.session_state:
    st.session_state.persisted_history = load_history()

# Change detection
zip_changed = (st.session_state.get("last_zip") != zip_code)
mode_changed = (st.session_state.get("last_offline") != offline_mode)

# --- Header ---
st.title("HurriAid")
st.write(f"Last opened: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# --- Run ---
should_run = ("last_result" not in st.session_state) or update_now or autorefresh_on or zip_changed or mode_changed
if should_run:
    result = coord.run_once(zip_code)
    st.session_state.last_result = result
    st.session_state.last_zip = zip_code
    st.session_state.last_offline = offline_mode
    st.session_state.last_run = datetime.now().strftime('%H:%M:%S')
    # Append to session history table
    hist = st.session_state.get("history", [])
    hist.append({
        "time": st.session_state.last_run,
        "zip": zip_code,
        "risk": (result.get("analysis") or {}).get("risk", "—"),
        "eta": (result.get("plan") or {}).get("eta_min", "—"),
        "mode": "Offline" if offline_mode else "Online"
    })
    st.session_state.history = hist[-12:]

result = st.session_state.get("last_result", {})
advisory = result.get("advisory", {})
analysis = result.get("analysis", {})
plan = result.get("plan")
checklist = result.get("checklist", [])
verify = result.get("verify", {})
timings = result.get("timings_ms", {})
errors = result.get("errors", {})
zip_valid = result.get("zip_valid", True)
zip_message = result.get("zip_message", "")

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
    if analysis.get("risk") == "ERROR":
        st.error(analysis.get("reason", "Unknown ZIP — cannot assess risk."))
    else:
        if "distance_km" in analysis:
            st.write(
                f"ZIP **{zip_code}** risk: **{analysis['risk']}** — "
                f"distance to advisory center: {analysis['distance_km']:.1f} km."
            )
        st.caption(analysis.get("reason", ""))
else:
    st.info("Risk analysis unavailable.")


st.subheader("Route")
if analysis.get("risk") == "ERROR":
    st.info("Route is not available because the ZIP is invalid/unknown.")
elif plan:
    st.success(f"Nearest open shelter: {plan['name']} ({plan['distance_km']:.1f} km, {plan['eta_min']} min)")
    params = {"api": 1, "destination": f"{plan['lat']},{plan['lon']}"}
    maps_url = "https://www.google.com/maps/dir/?" + urlencode(params)
    st.markdown(f"[Open route in Google Maps]({maps_url})")
else:
    st.info("No open shelters found.")

# Map visualization
st.subheader("Map")
if analysis.get("risk") == "ERROR":
    st.info("Map is hidden because the ZIP is invalid/unknown.")
else:
    layers = []
    # Advisory circle as a filled polygon
    if advisory and advisory.get("center") and advisory.get("radius_km"):
        center = advisory["center"]
        poly = circle_polygon(center["lat"], center["lon"], float(advisory["radius_km"]))
        layers.append(
            pdk.Layer(
                "PolygonLayer",
                data=[{"polygon": poly, "name": "Advisory"}],
                get_polygon="polygon",
                get_fill_color=[255, 0, 0, 40],
                get_line_color=[200, 0, 0],
                line_width_min_pixels=1,
                stroked=True,
                filled=True,
                pickable=False,
            )
        )

    # ZIP centroid
    if result.get("zip_point"):
        zp = result["zip_point"]
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=[{"position": [zp["lon"], zp["lat"]], "label": "ZIP"}],
                get_position="position",
                get_radius=200,
                radius_min_pixels=4,
                get_fill_color=[0, 122, 255, 200],
                pickable=True,
            )
        )
    # Nearest open shelter
    if plan:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=[{"position": [plan["lon"], plan["lat"]], "label": plan["name"]}],
                get_position="position",
                get_radius=200,
                radius_min_pixels=5,
                get_fill_color=[0, 180, 0, 220],
                pickable=True,
            )
        )


    # View: center on zip if available, else advisory center
    view_lat = (result.get("zip_point") or advisory.get("center") or {"lat": 25.77}).get("lat")
    view_lon = (result.get("zip_point") or advisory.get("center") or {"lon": -80.19}).get("lon")


    view_state = pdk.ViewState(latitude=view_lat, longitude=view_lon, zoom=9, pitch=0)
    st.pydeck_chart(pdk.Deck(map_style=None, initial_view_state=view_state, layers=layers))

st.subheader("Checklist (Risk‑aware)")
if analysis.get("risk") == "ERROR":
    st.info("Route is not available because the ZIP is invalid/unknown.")
elif checklist:
    st.write("\n".join(f"- {it}" for it in checklist))
else:
    st.markdown(
        "- Water (3 days)\n"
        "- Non-perishable food\n"
        "- Medications\n"
        "- Flashlight & batteries\n"
        "- First aid kit\n"
        "- Important documents in a waterproof bag"
    )

st.subheader("Verifier (Rumor Check)")
if analysis.get("risk") == "ERROR":
    st.info("Verifier is disabled because the ZIP is invalid/unknown.")
else:
    overall = verify.get("overall", "CLEAR")
    matches = verify.get("matches", [])
    if overall == "CLEAR" and not matches:
        st.success("No rumor flags detected in the current checklist.")
    else:
        st.warning(f"Verifier result: {overall}")
        for m in matches:
            st.markdown(f"- **Pattern:** {m['pattern']} → {m['verdict']} — {m.get('note', '')}")

st.subheader("Agent Status")
status_lines = [
    f"Watcher: {timings.get('watcher_ms', '—')} ms" + (f" | ERROR: {errors['watcher']}" if 'watcher' in errors else ""),
    f"Analyzer: {timings.get('analyzer_ms', '—')} ms" + (f" | ERROR: {errors['analyzer']}" if 'analyzer' in errors else ""),
    f"Planner: {timings.get('planner_ms', '—')} ms" + (f" | ERROR: {errors['planner']}" if 'planner' in errors else ""),
    f"Parallel: {timings.get('parallel_ms', '—')} ms",
    f"Total: {timings.get('total_ms', '—')} ms (ran at {st.session_state.get('last_run', '—')})"
]
st.code("\n".join(status_lines), language="text")


st.subheader("History")
hist = st.session_state.get("history", [])
if hist:
    st.table(hist)
else:
    st.caption("No session runs yet.")