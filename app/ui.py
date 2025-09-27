# app/ui.py — Live Watcher + Demo Mode + AI Studio default (Vertex optional)
import os
import streamlit as st
from datetime import datetime
from urllib.parse import urlencode

# ----- Load .env first (so env vars are available to checks) -----
try:
    from dotenv import load_dotenv  # pip install python-dotenv
    load_dotenv()
except Exception:
    pass

# ---------------- Runtime mode & sanity checks ----------------
USE_VERTEX = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "FALSE").upper() == "TRUE"

if not USE_VERTEX:
    # AI Studio path — no GCP/billing needed
    if not os.getenv("GOOGLE_API_KEY"):
        st.error("AI Studio key missing. Set environment variable GOOGLE_API_KEY or create a .env with GOOGLE_API_KEY=YOUR_KEY.")
        st.stop()
else:
    # Vertex path — used only if you intentionally flip the switch to TRUE
    PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-east4")
    if not PROJECT:
        st.error("GOOGLE_CLOUD_PROJECT not set. For Vertex mode, set GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION and GOOGLE_APPLICATION_CREDENTIALS.")
        st.stop()
    try:
        from google.cloud import aiplatform
        aiplatform.init(project=PROJECT, location=LOCATION)
    except Exception as e:
        st.error(f"Google Cloud init failed: {e}")
        st.caption("Ensure billing is enabled, Vertex AI API is ON, and credentials/roles are configured.")
        st.stop()

# ---------------- Project imports ----------------
from core.parallel_exec import ADKNotAvailable
import pydeck as pdk
from tools.geo import circle_polygon
from core.utils import load_history
from core.ui_helpers import badge, compute_freshness
from agents.coordinator import Coordinator
from agents.verifier_llm import verify_items_with_llm  # interactive LLM tab uses this

st.set_page_config(page_title="HurriAid", layout="wide")
APP_NS = "v9"  # bump namespace to avoid stale widget state

# ───────────────────────── Sidebar ─────────────────────────
st.sidebar.subheader("Input")
zip_code = st.sidebar.text_input("Enter ZIP code", value="33101", key=f"{APP_NS}_zip")

# Manual update
update_now = st.sidebar.button("Update Now", key=f"{APP_NS}_update")

# Demo Mode (under Update Now)
st.sidebar.markdown("---")
st.sidebar.subheader("Demo Mode")
demo_mode = st.sidebar.toggle(
    "Enable Demo Mode",
    value=False,
    help="Cycles through demo ZIPs automatically.",
    key=f"{APP_NS}_demo_mode",
)
demo_interval = st.sidebar.slider(
    "Demo step (seconds)",
    5, 120, 12,
    key=f"{APP_NS}_demo_interval",
)
DEFAULT_DEMO_ZIPS = ["33101", "33012", "33301", "33401"]
demo_zips = DEFAULT_DEMO_ZIPS

# Live Watcher controls
st.sidebar.markdown("---")
st.sidebar.subheader("Live Watcher")
live_on = st.sidebar.toggle(
    "Enable Live Watcher",
    value=False,
    help="Poll advisories every N minutes and auto-run when risk changes.",
    key=f"{APP_NS}_live_on",
)
live_every_min = st.sidebar.slider(
    "Poll every (minutes)",
    1, 30, 5,
    key=f"{APP_NS}_live_every_min",
)

# View toggles
st.sidebar.markdown("---")
st.sidebar.subheader("View")
show_map = st.sidebar.toggle("Show Map", value=True, key=f"{APP_NS}_show_map")
show_verifier = st.sidebar.toggle("Show Verifier", value=True, key=f"{APP_NS}_show_verifier")
show_history = st.sidebar.toggle("Show History", value=True, key=f"{APP_NS}_show_history")

# ───────────────────── Demo & Live timers ─────────────────────
# Autorefresh for Demo Mode
if demo_mode:
    try:
        from streamlit_autorefresh import st_autorefresh
        count_demo = st_autorefresh(interval=demo_interval * 1000, key=f"{APP_NS}_demo_tick")
        # Keep an index in session
        if "demo_idx" not in st.session_state:
            st.session_state.demo_idx = 0
        if count_demo is not None:
            st.session_state.demo_idx = (st.session_state.demo_idx + 1) % len(demo_zips)
            zip_code = demo_zips[st.session_state.demo_idx]
            # Keep the textbox in sync with the demo ZIP
            st.session_state[f"{APP_NS}_zip"] = zip_code
    except Exception:
        st.warning("Demo Mode needs 'streamlit-autorefresh'. Run: pip install streamlit-autorefresh")

# Autorefresh for Live Watcher
if live_on:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=live_every_min * 60 * 1000, key=f"{APP_NS}_live_tick")
    except Exception:
        st.warning("Live Watcher needs 'streamlit-autorefresh'. Run: pip install streamlit-autorefresh")

# ───────────────────── Coordinator / ADK ─────────────────────
if "coordinator" not in st.session_state:
    try:
        st.session_state.coordinator = Coordinator(data_dir="data")
        st.session_state.adk_error = None
    except ADKNotAvailable as e:
        st.session_state.coordinator = None
        st.session_state.adk_error = str(e)
coord = st.session_state.coordinator

if st.session_state.get("adk_error"):
    st.error("Google ADK is required: " + st.session_state["adk_error"])
    st.stop()

# History bootstrap
if "persisted_history" not in st.session_state:
    try:
        st.session_state.persisted_history = load_history()
    except Exception:
        st.session_state.persisted_history = []
if "history" not in st.session_state:
    st.session_state["history"] = []
if "live_prev_risk" not in st.session_state:
    st.session_state["live_prev_risk"] = None
if "last_result" not in st.session_state:
    st.session_state["last_result"] = {}
if "last_zip" not in st.session_state:
    st.session_state["last_zip"] = zip_code

# ───────────── Run logic (Manual / Demo / Live) ─────────────
zip_changed = (st.session_state.get("last_zip") != zip_code)

def _record_history(result: dict, trigger: str):
    hist = st.session_state.get("history", [])
    hist.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "zip": zip_code,
        "risk": (result.get("analysis") or {}).get("risk", "—"),
        "eta": (result.get("plan") or {}).get("eta_min", "—"),
        "trigger": trigger,
    })
    st.session_state["history"] = hist[-12:]

if live_on:
    if coord is None:
        st.error("Coordinator not available (ADK error).")
        st.stop()

    prev = st.session_state.get("live_prev_risk")
    live_result = coord.run_if_risk_changed(zip_code, prev_risk=prev)

    # Update prev_risk each tick
    current_risk = (live_result.get("analysis") or {}).get("risk")
    st.session_state["live_prev_risk"] = current_risk
    st.session_state["last_zip"] = zip_code

    # If risk changed, persist full fan-out result
    if live_result.get("changed"):
        st.session_state["last_result"] = live_result
        st.session_state["last_run"] = datetime.now().strftime("%H:%M:%S")
        _record_history(live_result, trigger="risk-change")
        st.success(f"Risk changed → auto-ran agents (now: {current_risk}).")
    elif update_now or zip_changed or demo_mode:
        # Allow manual/zip-change/demo to render latest probe even if no change
        st.session_state["last_result"] = live_result
        st.session_state["last_run"] = datetime.now().strftime("%H:%M:%S")
        src = "manual" if update_now else ("zip-change" if zip_changed else "demo")
        _record_history(live_result, trigger=src)
else:
    # Classic one-shot mode
    should_run = ("last_result" not in st.session_state) or update_now or zip_changed or demo_mode
    if should_run:
        if coord is None:
            st.error("Coordinator not available (ADK error).")
            st.stop()
        result = coord.run_once(zip_code)
        st.session_state["last_result"] = result
        st.session_state["last_zip"] = zip_code
        st.session_state["last_run"] = datetime.now().strftime("%H:%M:%S")
        src = "manual" if update_now else ("zip-change" if zip_changed else ("demo" if demo_mode else "auto"))
        _record_history(result, trigger=src)

# ───────────────────── Unpack result ─────────────────────
result = st.session_state.get("last_result", {}) or {}
advisory = result.get("advisory", {}) or {}
analysis = result.get("analysis", {}) or {}
plan = result.get("plan")
checklist = result.get("checklist", []) or []
verify = result.get("verify", {}) or {}
timings = result.get("timings_ms", {}) or {}
errors = result.get("errors", {}) or {}
zip_point = result.get("zip_point")

if errors.get("adk"):
    st.error("Google ADK is required: " + errors["adk"])
    st.stop()

# ───────────────────── Header / Chips ─────────────────────
st.title("HurriAid")
st.write(f"Last opened: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

chips = []
risk_val = (analysis or {}).get("risk", "—")
if risk_val == "HIGH":
    chips.append(badge("RISK: HIGH", "red"))
elif risk_val == "MEDIUM":
    chips.append(badge("RISK: MEDIUM", "amber"))
elif risk_val == "LOW":
    chips.append(badge("RISK: LOW", "green"))
elif risk_val == "ERROR":
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

chips.append(badge("LLM: Google AI Studio" if not USE_VERTEX else "LLM: Vertex",
                   "green" if not USE_VERTEX else "amber"))
st.markdown(" ".join(chips), unsafe_allow_html=True)

# Errors
if errors.get("watcher"):  st.error(f"Watcher error: {errors['watcher']}")
if errors.get("analyzer"): st.error(f"Analyzer error: {errors['analyzer']}")
if errors.get("planner"):  st.error(f"Planner error: {errors['planner']}")

# ───────────────────── Panels ─────────────────────
# Advisory
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

# Map (guarded, self-clearing)
map_box = st.container()
if show_map:
    with map_box:
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
            view_lat = (zip_point or advisory.get("center") or {"lat": 25.77})["lat"]
            view_lon = (zip_point or advisory.get("center") or {"lon": -80.19})["lon"]
            view_state = pdk.ViewState(latitude=view_lat, longitude=view_lon, zoom=9, pitch=0)
            st.pydeck_chart(pdk.Deck(map_style=None, initial_view_state=view_state, layers=layers))
else:
    map_box.empty()

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

# Verifier (Rumor Check) — LLM only (no defaults shown until user runs)
verifier_box = st.container()
if show_verifier:
    with verifier_box:
        st.subheader("AI Rumor Check")
        if analysis.get("risk") == "ERROR":
            st.info("Verifier is disabled because the ZIP is invalid/unknown.")
        else:
            LLM_RESULT_KEY  = f"{APP_NS}_llm_result"
            LLM_NONCE_KEY   = f"{APP_NS}_llm_nonce"
            LLM_CLEARED_KEY = f"{APP_NS}_llm_cleared"

            if LLM_NONCE_KEY not in st.session_state:
                st.session_state[LLM_NONCE_KEY] = 0

            llm_default_value = ""
            if st.session_state.get(LLM_CLEARED_KEY):
                llm_default_value = ""
                st.session_state.pop(LLM_CLEARED_KEY, None)

            llm_text_key = f"{APP_NS}_llm_text_{st.session_state[LLM_NONCE_KEY]}"
            st.caption("Enter statements or rumors to verify with the LLM (one per line).")
            llm_text = st.text_area(
                "Enter rumor(s) to verify:",
                value=llm_default_value,
                key=llm_text_key,
                help="Examples: 'drink seawater' (False), 'drink water' (True), 'taping windows' (Misleading).",
            )

            c1, c2, _ = st.columns([1, 1, 6])
            with c1:
                run_llm_check = st.button("Check with LLM", key=f"{APP_NS}_llm_run_btn")
            with c2:
                clear_llm = st.button("Clear", key=f"{APP_NS}_llm_clear_btn")

            llm_cache = st.session_state.setdefault("llm_rumor_cache", {})

            if clear_llm:
                st.session_state.pop(LLM_RESULT_KEY, None)
                st.session_state[LLM_CLEARED_KEY] = True
                st.session_state[LLM_NONCE_KEY] += 1
                st.rerun()

            if run_llm_check:
                items = [line.strip() for line in llm_text.splitlines() if line.strip()]
                if not items:
                    st.info("Type at least one rumor to verify.")
                else:
                    key_joined = "\n".join(items)
                    if key_joined in llm_cache:
                        st.session_state[LLM_RESULT_KEY] = llm_cache[key_joined]
                    else:
                        res = verify_items_with_llm(items)
                        llm_cache[key_joined] = res
                        st.session_state[LLM_RESULT_KEY] = res

            # Only show results after the user runs a check
            llm_live = st.session_state.get(LLM_RESULT_KEY)

            if not isinstance(llm_live, dict) or not llm_live:
                st.info("Enter rumor text above and click **Check with LLM**.")
            else:
                # Friendly verdict labels + de-shout helper
                VERDICT_LABELS = {
                    "TRUE": "True", "FALSE": "False", "MISLEADING": "Misleading",
                    "CAUTION": "Caution", "CLEAR": "Clear", "ERROR": "Error", "SAFE": "Safe",
                }
                def de_shout(text: str) -> str:
                    if isinstance(text, str) and text.isupper():
                        # simple sentence-casing for ALL CAPS responses
                        return text.capitalize()
                    return text

                overall_raw = (llm_live.get("overall") or "CLEAR")
                overall = overall_raw.upper()
                matches = llm_live.get("matches", []) or []
                overall_display = VERDICT_LABELS.get(overall, overall_raw.title())

                if overall == "ERROR":
                    msg = (llm_live.get("error") or "")
                    umsg = msg.upper()
                    if any(k in umsg for k in ("API KEY NOT VALID", "API_KEY_INVALID")):
                        st.error("AI Studio API key is invalid or restricted. Set GOOGLE_API_KEY and remove restrictions for local dev.")
                    elif any(k in umsg for k in ("UNAVAILABLE", "OVERLOADED", "503", "TIMEOUT")):
                        st.warning("The model is busy. Please try again shortly.")
                    else:
                        st.error(msg or "LLM error.")
                elif (overall in ("CLEAR", "SAFE")) and not matches:
                    st.success("No rumor flags detected.")
                elif overall == "SAFE":
                    st.success(f"Verifier result: {overall_display}")
                    for m in matches:
                        note = de_shout(m.get("note", ""))
                        st.markdown(f"- **Rumor:** {m['pattern']} — {note}")
                elif overall == "FALSE":
                    st.error(f"Verifier result: {overall_display}")
                    for m in matches:
                        note = de_shout(m.get("note", ""))
                        st.markdown(f"- **Rumor:** {m['pattern']} — {note}")
                else:
                    st.warning(f"Verifier result: {overall_display}")
                    for m in matches:
                        note = de_shout(m.get("note", ""))
                        st.markdown(f"- **Rumor:** {m['pattern']} — {note}")
else:
    verifier_box.empty()

# ───────────────── Agent Status & History ─────────────────
st.subheader("Agent Status")
status_lines = [
    f"Watcher: {timings.get('watcher_ms', '—')} ms" + (f" | ERROR: {errors['watcher']}" if 'watcher' in errors else ""),
    f"Analyzer: {timings.get('analyzer_ms', '—')} ms" + (f" | ERROR: {errors['analyzer']}" if 'analyzer' in errors else ""),
    f"Planner: {timings.get('planner_ms', '—')} ms" + (f" | ERROR: {errors['planner']}" if 'planner' in errors else ""),
    f"Parallel: {timings.get('parallel_ms', '—')} ms",
    f"Total: {timings.get('total_ms', '—')} ms (ran at {st.session_state.get('last_run', '—')})",
]
st.code("\n".join(status_lines), language="text")

if show_history:
    st.subheader("History")
    hist = st.session_state.get("history", [])
    if hist:
        st.table(hist)
    else:
        st.caption("No session runs yet.")
