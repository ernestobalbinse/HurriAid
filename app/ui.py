# ---- Standard library ----
import os
from datetime import datetime
from urllib.parse import urlencode

# ---- Third-party ----
import streamlit as st
st.set_page_config(page_title="HurriAid", layout="wide", initial_sidebar_state="expanded")
import pydeck as pdk
from streamlit_autorefresh import st_autorefresh
import pandas as pd

# ---- Project modules ----
from core.parallel_exec import ADKNotAvailable
from core.ui_helpers import badge, compute_freshness
from core.utils import load_history
from tools.geo import circle_polygon
from agents.coordinator import Coordinator
from agents.verifier_llm import verify_items_with_llm

# Load .env early so env vars are available
try:
    from dotenv import load_dotenv  # pip install python-dotenv
    load_dotenv()
except Exception:
    pass

# --- Runtime sanity check (AI Studio only) ---
if not os.getenv("GOOGLE_API_KEY"):
    st.error(
        "AI Studio key missing. Set GOOGLE_API_KEY or create a .env with GOOGLE_API_KEY=YOUR_KEY.\n\n"
        "Windows PowerShell example:\n"
        '$env:GOOGLE_GENAI_USE_VERTEXAI = "FALSE"\n'
        '$env:GOOGLE_API_KEY = "<YOUR_KEY>"\n'
    )
    st.stop()

# --- Layout tightening CSS (reduce top padding) ---
st.markdown("""
<style>
.block-container { padding-top: 0.6rem; }
header[data-testid="stHeader"] { height: 40px; }
header[data-testid="stHeader"] > div { height: 40px; }
h2, h3 { margin-top: .6rem; margin-bottom: .4rem; }
</style>
""", unsafe_allow_html=True)

# ---------------- Sidebar ----------------
APP_NS = "v8"  # namespace for widget keys

zip_code = st.sidebar.text_input("Enter ZIP code", value="33101", key=f"{APP_NS}_zip")
update_now = st.sidebar.button("Update Now", key=f"{APP_NS}_update")

# Live Watcher (always on) above Demo Mode
st.sidebar.markdown("---")
st.sidebar.subheader("Live Watcher")
watch_interval = st.sidebar.slider(
    "Check storm data every (seconds)", 10, 300, 60, key=f"{APP_NS}_watch_interval"
)
# Use counters so we only run when the counter increments (not every render)
watch_count = st_autorefresh(interval=watch_interval * 1000, key=f"{APP_NS}_watch_loop")
prev_watch_count = st.session_state.get(f"{APP_NS}_prev_watch_count")
watch_tick = (watch_count is not None) and (prev_watch_count is not None) and (watch_count != prev_watch_count)
st.session_state[f"{APP_NS}_prev_watch_count"] = watch_count

# Demo Mode
st.sidebar.markdown("---")
st.sidebar.subheader("Demo Mode")
demo_mode = st.sidebar.toggle(
    "Demo Mode", value=False, help="Cycles ZIPs automatically", key=f"{APP_NS}_demo_mode"
)
demo_interval = st.sidebar.slider(
    "Demo step (seconds)", 5, 120, 12, key=f"{APP_NS}_demo_interval"
)
DEFAULT_DEMO_ZIPS = ["33101", "33012", "33301", "33401"]
demo_zips = DEFAULT_DEMO_ZIPS

demo_count = None
demo_tick = False
if "demo_idx" not in st.session_state:
    st.session_state.demo_idx = 0

if demo_mode:
    demo_count = st_autorefresh(interval=demo_interval * 1000, key=f"{APP_NS}_demo_loop")
    prev_demo_count = st.session_state.get(f"{APP_NS}_prev_demo_count")
    demo_tick = (demo_count is not None) and (prev_demo_count is not None) and (demo_count != prev_demo_count)
    st.session_state[f"{APP_NS}_prev_demo_count"] = demo_count

    if demo_tick:
        st.session_state.demo_idx = (st.session_state.demo_idx + 1) % len(demo_zips)
        zip_code = demo_zips[st.session_state.demo_idx]
        st.session_state[f"{APP_NS}_zip"] = zip_code  # keep input in sync

# Settings
st.sidebar.markdown("---")
st.sidebar.subheader("Settings")
show_map = st.sidebar.toggle("Show Map", value=True, key=f"{APP_NS}_show_map")
show_verifier = st.sidebar.toggle("Show Verifier", value=True, key=f"{APP_NS}_show_verifier")

# ---------------- Coordinator (ADK mandatory) ----------------
if "coordinator" not in st.session_state:
    try:
        st.session_state.coordinator = Coordinator(data_dir="data")
        st.session_state.adk_error = None
    except ADKNotAvailable as e:
        st.session_state.coordinator = None
        st.session_state.adk_error = str(e)

coord = st.session_state.coordinator

# If ADK broke during init, show blocking banner and stop
if st.session_state.get("adk_error"):
    st.error("Google ADK is required: " + st.session_state["adk_error"])
    st.stop()

# ---------------- Persisted history ----------------
if "persisted_history" not in st.session_state:
    try:
        st.session_state.persisted_history = load_history()
    except Exception:
        st.session_state.persisted_history = []

# ---------------- Run triggers (only when something really changed) ----------------
zip_changed = (st.session_state.get("last_zip") != zip_code)
first_render = ("last_result" not in st.session_state)

# Avoid double-run on the very first render if autorefresh counters are also initialized
if first_render:
    watch_tick = False
    demo_tick = False

should_run = first_render or update_now or zip_changed or watch_tick or demo_tick

if should_run:
    if coord is None:
        st.error("Coordinator not available (ADK error).")
        st.stop()

    result = coord.run_once(zip_code)
    st.session_state.last_result = result
    st.session_state.last_zip = zip_code
    st.session_state.last_run = datetime.now().strftime("%H:%M:%S")

    # History row
    hist = st.session_state.get("history", [])
    adk_ok = not st.session_state.get("adk_error")
    hist.append({
        "time": st.session_state.last_run,
        "zip": zip_code,
        "risk": (result.get("analysis") or {}).get("risk", "—"),
        "eta": (result.get("plan") or {}).get("eta_min", "—"),
        "adk": "ON" if adk_ok else "ERROR",
    })
    st.session_state["history"] = hist[-12:]

# ---------------- Unpack result ----------------
result = st.session_state.get("last_result", {}) or {}
advisory = result.get("advisory", {}) or {}
analysis = result.get("analysis", {}) or {}
plan = result.get("plan")
checklist = result.get("checklist", []) or []
timings = result.get("timings_ms", {}) or {}
errors = result.get("errors", {}) or {}
zip_point = result.get("zip_point")

# If ADK exploded during run, block the page (mandatory ADK policy)
if errors.get("adk"):
    st.error("Google ADK is required: " + errors["adk"])
    st.stop()

# ---------------- Header ----------------
st.markdown("<h1 style='margin:0'>HurriAid</h1>", unsafe_allow_html=True)
st.caption(f"Last opened: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Chips row
chips = []
risk_val = (analysis or {}).get("risk", "—")
if risk_val == "HIGH":
    chips.append(badge("RISK: HIGH", "red"))
elif risk_val == "MEDIUM":
    chips.append(badge("RISK: MEDIUM", "amber"))
elif risk_val == "LOW":
    chips.append(badge("RISK: LOW", "green"))
elif risk_val == "SAFE":
    chips.append(badge("RISK: SAFE", "green"))
elif risk_val == "ERROR":
    chips.append(badge("RISK: ERROR", "red"))
else:
    chips.append(badge("RISK: —", "gray"))

issued_at = (advisory or {}).get("issued_at", "")
fresh_status, fresh_detail = compute_freshness(issued_at)
label = "Last update"
if fresh_status == "FRESH":
    chips.append(badge(f"{label}: {fresh_detail}", "green"))
elif fresh_status == "STALE":
    chips.append(badge(f"{label}: {fresh_detail}", "amber"))
else:
    chips.append(badge(f"{label}: unknown", "gray"))

st.markdown(" ".join(chips), unsafe_allow_html=True)

# ---------- GRID LAYOUT ----------
# Row 1: Risk (left) | Checklist (middle) | Map (right)
col_left, col_mid, col_map = st.columns([0.9, 1.1, 1.6], gap="large")

with col_left:
    st.subheader("Risk")
    if analysis:
        if analysis.get("risk") == "ERROR":
            st.error(analysis.get("reason", "Unknown ZIP — cannot assess risk."))
            st.stop()
        else:
            risk_txt = analysis.get("risk", "—")
            dist_km = analysis.get("distance_km")
            radius_km = (advisory or {}).get("radius_km")
            bullets = [
                f"- **ZIP:** `{zip_code}`",
                f"- **Risk:** **{risk_txt}**",
            ]
            if isinstance(dist_km, (int, float)):
                bullets.append(f"- **Distance to storm center:** {dist_km:.1f} km")
            if isinstance(dist_km, (int, float)) and isinstance(radius_km, (int, float)):
                where = "Inside" if float(dist_km) <= float(radius_km) else "Outside"
                bullets.append(f"- **Storm area:** {where} (radius ≈ {float(radius_km):.1f} km)")
            st.markdown("\n".join(bullets))
    else:
        st.info("Risk analysis unavailable.")

with col_mid:
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

with col_map:
    if show_map:
        st.subheader("Map")
        if analysis.get("risk") == "ERROR":
            st.info("Map is hidden because the ZIP is invalid/unknown.")
        else:
            # --- Build layers (fast) ---
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

            # --- Make map cheaper on each loop: cache deck by a signature ---
            map_sig = (
                advisory.get("issued_at"),
                (advisory.get("center") or {}).get("lat") if advisory else None,
                (advisory.get("center") or {}).get("lon") if advisory else None,
                advisory.get("radius_km"),
                (zip_point or {}).get("lat") if zip_point else None,
                (zip_point or {}).get("lon") if zip_point else None,
                (plan or {}).get("lat") if plan else None,
                (plan or {}).get("lon") if plan else None,
            )

            if st.session_state.get("last_map_sig") != map_sig:
                deck = pdk.Deck(map_style=None, initial_view_state=view_state, layers=layers)
                st.session_state["last_map_chart"] = deck
                st.session_state["last_map_sig"] = map_sig

            if st.session_state.get("last_map_chart"):
                st.pydeck_chart(st.session_state["last_map_chart"])

# Row 2: Route on left (spans left + mid)
left_span, _ = st.columns([2.0, 1.6], gap="large")
with left_span:
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

# ---------- Below the grid ----------
st.markdown("")

# AI Rumor Check — full width (on-demand only; no background LLM)
if show_verifier:
    st.subheader("AI Rumor Check")
    if analysis.get("risk") == "ERROR":
        st.info("Verifier is disabled because the ZIP is invalid/unknown.")
    else:
        LLM_RESULT_KEY  = f"{APP_NS}_llm_result"
        LLM_NONCE_KEY   = f"{APP_NS}_llm_nonce"
        LLM_CLEARED_KEY = f"{APP_NS}_llm_cleared"

        if LLM_NONCE_KEY not in st.session_state:
            st.session_state[LLM_NONCE_KEY] = 0
        if st.session_state.get(LLM_CLEARED_KEY):
            st.session_state.pop(LLM_CLEARED_KEY, None)

        llm_text_key = f"{APP_NS}_llm_text_{st.session_state[LLM_NONCE_KEY]}"
        st.caption("Enter statements or rumors to verify with the LLM (one per line).")
        llm_text = st.text_area(
            "Rumor(s) to verify",
            value="",
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

        llm_live = st.session_state.get(LLM_RESULT_KEY)
        if not isinstance(llm_live, dict) or not llm_live:
            st.info("Enter rumor text above and click **Check with LLM**.")
        else:
            VERDICT_LABELS = {
                "TRUE": "True", "FALSE": "False", "MISLEADING": "Misleading",
                "CAUTION": "Caution", "CLEAR": "Clear", "ERROR": "Error", "SAFE": "Safe",
            }
            def de_shout(text: str) -> str:
                if isinstance(text, str) and text.isupper():
                    return text.capitalize()
                return text

            overall_raw = (llm_live.get("overall") or "CLEAR")
            overall = overall_raw.upper()
            matches = llm_live.get("matches", [])

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
            else:
                box = st.success if overall == "SAFE" else (st.error if overall == "FALSE" else st.warning)
                box(f"Verifier result: {overall_display}")
                for m in matches:
                    note = de_shout(m.get("note",""))
                    st.markdown(f"- **Rumor:** {m['pattern']} — {note}")

# Collapsibles
with st.expander("Advisory (details)", expanded=False):
    issued_at = (advisory or {}).get("issued_at", "")
    fresh_status, fresh_detail = compute_freshness(issued_at)
    if advisory:
        st.json(advisory)
        if issued_at:
            st.caption(f"Issued at: {issued_at} — Last update: {fresh_detail}")
    else:
        st.caption("No advisory data.")

# --- Timing formatter (2 decimals) ---
def _fmt_ms(v):
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "—"

with st.expander("Agent Status", expanded=False):
    status_lines = [
        f"Watcher: {_fmt_ms(timings.get('watcher_ms'))} ms" + (f" | ERROR: {errors['watcher']}" if 'watcher' in errors else ""),
        f"Analyzer: {_fmt_ms(timings.get('analyzer_ms'))} ms" + (f" | ERROR: {errors['analyzer']}" if 'analyzer' in errors else ""),
        f"Planner:  {_fmt_ms(timings.get('planner_ms'))} ms"  + (f" | ERROR: {errors['planner']}" if 'planner' in errors else ""),
        f"Parallel: {_fmt_ms(timings.get('parallel_ms'))} ms",
        f"Total:    {_fmt_ms(timings.get('total_ms'))} ms (ran at {st.session_state.get('last_run', '—')})",
    ]
    st.code("\n".join(status_lines), language="text")

# --- History (collapsible) ---
with st.expander("History", expanded=False):
    raw_hist = st.session_state.get("history", [])
    if raw_hist:
        display_rows = []
        for r in raw_hist:
            display_rows.append({
                "time": r.get("time", "—"),
                "zip": r.get("zip", "—"),
                "risk": r.get("risk", "—"),
                "eta": "—" if r.get("eta") in (None, "—") else str(r.get("eta")),  # left-align as text
                "adk": r.get("adk", "—"),
            })
        df = pd.DataFrame(display_rows, columns=["time", "zip", "risk", "eta", "adk"])
        st.dataframe(df, hide_index=True, use_container_width=True)

        c1, c2 = st.columns([1, 6])
        with c1:
            if st.button("Clear history", key=f"{APP_NS}_clear_history"):
                st.session_state["history"] = []
                st.success("History cleared.")
                st.rerun()
        with c2:
            st.caption(f"{len(raw_hist)} run(s) in this session.")
    else:
        st.caption("No session runs yet.")
