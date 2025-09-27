# app/ui.py — cleaned & unified
import streamlit as st
from datetime import datetime
from urllib.parse import urlencode

import pydeck as pdk
from tools.geo import circle_polygon

from core.utils import load_history
from core.ui_helpers import badge, compute_freshness
from agents.coordinator import Coordinator

st.set_page_config(page_title="HurriAid", layout="wide")

# ---------------- Sidebar (single block, unique keys) ----------------
APP_NS = "v8"  # namespace for widget keys

zip_code = st.sidebar.text_input(
    "Enter ZIP code",
    value="33101",
    key=f"{APP_NS}_zip",
)

update_now = st.sidebar.button(
    "Update Now",
    key=f"{APP_NS}_update",
)

use_adk_enabled = st.sidebar.toggle(
    "Use Google ADK",
    value=True,
    help="Turn off to force local thread fallback even if ADK is installed.",
    key=f"{APP_NS}_adk",
)

autorefresh_on = st.sidebar.toggle(
    "Auto Refresh",
    value=False,
    help="Continuously re-run to simulate a loop.",
    key=f"{APP_NS}_autorefresh",
)
interval_sec = st.sidebar.slider(
    "Refresh every (seconds)",
    5, 60, 15,
    key=f"{APP_NS}_autorefresh_interval",
)
if autorefresh_on:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=interval_sec * 1000, key=f"{APP_NS}_loop")
    except Exception:
        st.warning("Auto Refresh requires 'streamlit-autorefresh'. Run: pip install streamlit-autorefresh")

# ---------------- Coordinator bootstrap (recreate when toggle flips) ----------------
if ("coordinator" not in st.session_state) or (st.session_state.get("use_adk_enabled") != use_adk_enabled):
    st.session_state.coordinator = Coordinator(data_dir="data", adk_enabled=use_adk_enabled)
    st.session_state.use_adk_enabled = use_adk_enabled
coord = st.session_state.coordinator

# Persisted history (load once)
if "persisted_history" not in st.session_state:
    try:
        st.session_state.persisted_history = load_history()
    except Exception:
        st.session_state.persisted_history = []

# ---------------- Run triggers ----------------
zip_changed = (st.session_state.get("last_zip") != zip_code)
should_run = ("last_result" not in st.session_state) or update_now or autorefresh_on or zip_changed

if should_run:
    result = coord.run_once(zip_code)
    st.session_state.last_result = result
    st.session_state.last_zip = zip_code
    st.session_state.last_run = datetime.now().strftime("%H:%M:%S")

    # Append to session history
    hist = st.session_state.get("history", [])
    hist.append({
        "time": st.session_state.last_run,
        "zip": zip_code,
        "risk": (result.get("analysis") or {}).get("risk", "—"),
        "eta": (result.get("plan") or {}).get("eta_min", "—"),
        "adk": "ON" if use_adk_enabled else "OFF",
    })
    st.session_state.history = hist[-12:]  # keep last 12

# ---------------- Unpack result ----------------
result = st.session_state.get("last_result", {})
advisory = result.get("advisory", {})
analysis = result.get("analysis", {})
plan = result.get("plan")
checklist = result.get("checklist", [])
verify = result.get("verify", {})
timings = result.get("timings_ms", {})
errors = result.get("errors", {})
zip_point = result.get("zip_point")

# ---------------- Header ----------------
st.title("HurriAid")
st.write(f"Last opened: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ---------------- Status chips (use analysis/advisory safely) ----------------
chips = []
risk = (analysis or {}).get("risk", "—")
if risk == "HIGH":
    chips.append(badge("RISK: HIGH", "red"))
elif risk == "MEDIUM":
    chips.append(badge("RISK: MEDIUM", "amber"))
elif risk == "LOW":
    chips.append(badge("RISK: LOW", "green"))
elif risk == "ERROR":
    chips.append(badge("RISK: ERROR", "red"))
else:
    chips.append(badge("RISK: —", "gray"))

issued_at = (advisory or {}).get("issued_at", "")
fresh_status, fresh_detail = compute_freshness(issued_at)
if fresh_status == "FRESH":
    chips.append(badge(f"FRESHNESS: {fresh_detail}", "green"))
elif fresh_status == "STALE":
    chips.append(badge(f"FRESHNESS: {fresh_detail}", "amber"))
else:
    chips.append(badge("FRESHNESS: unknown", "gray"))

mode_label = "ADK ON" if st.session_state.get("use_adk_enabled", True) else "ADK OFF"
chips.append(badge(mode_label, "green" if st.session_state.get("use_adk_enabled", True) else "gray"))

st.markdown(" ".join(chips), unsafe_allow_html=True)

# ---------------- Agent error banners ----------------
if errors:
    if errors.get("watcher"):
        st.error(f"Watcher error: {errors['watcher']}")
    if errors.get("analyzer"):
        st.error(f"Analyzer error: {errors['analyzer']}")
    if errors.get("planner"):
        st.error(f"Planner error: {errors['planner']}")
    if errors.get("adk"):
        st.warning(f"ADK fallback used: {errors['adk']}")

# ---------------- Panels ----------------
st.subheader("Advisory")
if advisory:
    st.json(advisory)
    if issued_at:
        st.caption(f"Issued at: {issued_at} ({fresh_detail})")
else:
    st.info("Advisory data unavailable.")

# Risk
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

# Route
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

# Map
st.subheader("Map")
if analysis.get("risk") == "ERROR":
    st.info("Map is hidden because the ZIP is invalid/unknown.")
else:
    layers = []
    # Advisory circle
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
    if zip_point:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=[{"position": [zip_point["lon"], zip_point["lat"]], "label": "ZIP"}],
                get_position="position",
                get_radius=200,
                radius_min_pixels=4,
                get_fill_color=[0, 122, 255, 200],
                pickable=True,
            )
        )
    # Nearest shelter
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

    # View: center on ZIP if available, else advisory center
    view_lat = (zip_point or advisory.get("center") or {"lat": 25.77})["lat"]
    view_lon = (zip_point or advisory.get("center") or {"lon": -80.19})["lon"]
    view_state = pdk.ViewState(latitude=view_lat, longitude=view_lon, zoom=9, pitch=0)
    st.pydeck_chart(pdk.Deck(map_style=None, initial_view_state=view_state, layers=layers))

# Checklist
st.subheader("Checklist (Risk-aware)")
if analysis.get("risk") == "ERROR":
    st.info("Checklist is hidden because the ZIP is invalid/unknown.")
elif checklist:
    st.markdown("\n".join(f"- {it}" for it in checklist))
else:
    st.markdown(
        "- Water (3 days)\n"
        "- Non-perishable food\n"
        "- Medications\n"
        "- Flashlight & batteries\n"
        "- First aid kit\n"
        "- Important documents in a waterproof bag"
    )

# Verifier
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

# Agent Status
st.subheader("Agent Status")
status_lines = [
    f"Watcher: {timings.get('watcher_ms', '—')} ms" + (f" | ERROR: {errors['watcher']}" if 'watcher' in errors else ""),
    f"Analyzer: {timings.get('analyzer_ms', '—')} ms" + (f" | ERROR: {errors['analyzer']}" if 'analyzer' in errors else ""),
    f"Planner: {timings.get('planner_ms', '—')} ms" + (f" | ERROR: {errors['planner']}" if 'planner' in errors else ""),
    f"Parallel: {timings.get('parallel_ms', '—')} ms",
    f"Total: {timings.get('total_ms', '—')} ms (ran at {st.session_state.get('last_run', '—')})",
]
st.code("\n".join(status_lines), language="text")

# History
st.subheader("History")
hist = st.session_state.get("history", [])
if hist:
    st.table(hist)
else:
    st.caption("No session runs yet.")
