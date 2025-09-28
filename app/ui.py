# ---- Standard library ----
import sys, os, hashlib
from datetime import datetime
from urllib.parse import urlencode

# ---- Third-party ----
import streamlit as st
import streamlit.components.v1 as components
st.set_page_config(page_title="HurriAid", layout="wide", initial_sidebar_state="expanded")
import pydeck as pdk

# --- repo root on sys.path so 'core', 'agents', 'tools' resolve when run from app/ ---
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

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

# --- Namespace used for all widget/session keys ---
APP_NS = "v8"

# --- Runtime sanity check (AI Studio only) ---
if not os.getenv("GOOGLE_API_KEY"):
    st.error(
        "AI Studio key missing. Set GOOGLE_API_KEY or create a .env with GOOGLE_API_KEY=YOUR_KEY.\n\n"
        "Windows PowerShell example:\n"
        '$env:GOOGLE_GENAI_USE_VERTEXAI = "FALSE"\n'
        '$env:GOOGLE_API_KEY = "<YOUR_KEY>"\n'
    )
    st.stop()

# --- Apply any pending ZIP change BEFORE the text_input widget is created ---
_pending_key = f"{APP_NS}_pending_zip"
zip_key = f"{APP_NS}_zip"
if _pending_key in st.session_state:
    st.session_state[zip_key] = st.session_state[_pending_key]
    del st.session_state[_pending_key]

# --- Global style tweaks + remove form box for Rumor Check ---
st.markdown("""
<style>
section.main > div { padding-top: 0.5rem !important; }
.main .block-container { padding-top: 0.5rem !important; }
.block-container { padding-top: 0.5rem !important; }
/* Remove the card/border/shadow around the AI Rumor Check form */
form[data-testid="stForm"],
section[data-testid="stForm"],
div[data-testid="stForm"]{
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  padding: 0 !important;
  margin: 0 !important;
}
form[data-testid="stForm"] > div,
section[data-testid="stForm"] > div,
div[data-testid="stForm"] > div{
  padding: 0 !important;
  margin: 0 !important;
}
</style>
""", unsafe_allow_html=True)

# --- Keep page position (stop jumping to top on rerun) ---
components.html("""
<script>
(function(){
  const KEY = 'v8_scrollY';
  function save(){ try { sessionStorage.setItem(KEY, String(window.scrollY)); } catch (e) {} }
  function load(){
    try {
      const y = parseFloat(sessionStorage.getItem(KEY) || '0');
      if (!isNaN(y)) { window.scrollTo(0, y); }
    } catch (e) {}
  }
  window.addEventListener('load', () => {
    load();
    setTimeout(load, 120);
    setTimeout(load, 400);
    setTimeout(load, 800);
  });
  ['click','wheel','touchstart','keydown','scroll'].forEach(ev =>
    window.addEventListener(ev, save, { passive: true, capture: true })
  );
  document.addEventListener('submit', save, true);
  window.addEventListener('beforeunload', save);
})();
</script>
""", height=0)

# --- Title placeholder (persists across quick reruns so it doesn't "disappear") ---
_title_box = st.container()

# ---------------- Sidebar ----------------
zip_code = st.sidebar.text_input("Enter ZIP code", value=st.session_state.get(zip_key, "33101"), key=zip_key)
update_now = st.sidebar.button("Update Now", key=f"{APP_NS}_update")

st.sidebar.markdown("---")
st.sidebar.subheader("Settings")
show_map = st.sidebar.toggle("Show Map", value=True, key=f"{APP_NS}_show_map")
show_verifier = st.sidebar.toggle("Show Verifier", value=True, key=f"{APP_NS}_show_verifier")
always_run = st.sidebar.toggle("Force run on every rerun", value=False, key=f"{APP_NS}_always_run")

# ---------------- Coordinator (ADK mandatory) ----------------
if "coordinator" not in st.session_state:
    try:
        # IMPORTANT: point to <repo-root>/data (not app/data)
        st.session_state.coordinator = Coordinator(data_dir=os.path.join(ROOT, "data"))
        st.session_state.adk_error = None
    except ADKNotAvailable as e:
        st.session_state.coordinator = None
        st.session_state.adk_error = str(e)

coord = st.session_state.coordinator
if st.session_state.get("adk_error"):
    st.error("Google ADK is required: " + st.session_state["adk_error"])
    st.stop()

# ---------------- Persisted history ----------------
if "persisted_history" not in st.session_state:
    try:
        st.session_state.persisted_history = load_history()
    except Exception:
        st.session_state.persisted_history = []

# ---------------- Advisory file path / change detection ----------------
def _advisory_path():
    return os.path.join(ROOT, "data", "sample_advisory.json")

def _advisory_mtime():
    try:
        return os.path.getmtime(_advisory_path())
    except Exception:
        return None

def _advisory_sig():
    path = _advisory_path()
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None

prev_mtime = st.session_state.get(f"{APP_NS}_adv_mtime")
curr_mtime = _advisory_mtime()
prev_sig = st.session_state.get(f"{APP_NS}_adv_sig")
curr_sig = _advisory_sig()

file_changed = ((curr_mtime is not None and curr_mtime != prev_mtime) or
                (curr_sig is not None and curr_sig != prev_sig))

# update stored trackers
if curr_mtime is not None:
    st.session_state[f"{APP_NS}_adv_mtime"] = curr_mtime
if curr_sig is not None:
    st.session_state[f"{APP_NS}_adv_sig"] = curr_sig

# ---------------- Run triggers (manual + file change + zip change + optional force) ----------------
zip_changed = (st.session_state.get("last_zip") != zip_code)
should_run = True if always_run else (("last_result" not in st.session_state) or update_now or zip_changed or file_changed)

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
    analysis = (result.get("analysis") or {})
    plan = result.get("plan") or {}
    hist.append({
        "time": st.session_state.last_run,
        "zip": zip_code,
        "risk": analysis.get("risk", "—"),
        "eta": plan.get("eta_min", "—"),
        "llm": "Gemini",
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

if errors.get("adk"):
    st.error("Google ADK is required: " + errors["adk"])
    st.stop()

# ---------------- Header (persistent) ----------------
with _title_box:
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

chips.append(badge("LLM: Gemini", "green"))
st.markdown(" ".join(chips), unsafe_allow_html=True)

# ---------- GRID LAYOUT ----------
col_left, col_mid, col_map = st.columns([0.9, 1.1, 1.6], gap="large")

with col_left:
    st.subheader("Risk")
    active = bool(advisory.get("active", True))
    if not active:
        st.info("No active hurricane. Watcher is paused for this ZIP.")
        st.stop()
    if analysis:
        if analysis.get("risk") == "ERROR":
            st.error(analysis.get("reason", "Unknown ZIP — cannot assess risk."))
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
                where = "Inside" if dist_km <= float(radius_km) else "Outside"
                bullets.append(f"- **Advisory area:** {where} (radius ≈ {float(radius_km):.1f} km)")
            st.markdown("\n".join(bullets))

            # AI explainer (validated upstream)
            why = result.get("analysis_explainer") or result.get("risk_explainer")
            if isinstance(why, str) and why.strip():
                st.markdown(f"**Why:** {why}")
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
            layers = []
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
            st.pydeck_chart(pdk.Deck(
                map_style=None,
                initial_view_state=view_state,
                layers=layers,
                parameters={"cull": True}
            ))

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

st.markdown("")  # spacer

# ========== AI Rumor Check (FORM-BASED, ATOMIC SUBMIT) ==========
if show_verifier:
    st.subheader("AI Rumor Check")
    if analysis.get("risk") == "ERROR":
        st.info("Verifier is disabled because the ZIP is invalid/unknown.")
    else:
        APP_FORM_KEY     = f"{APP_NS}_llm_form"
        LLM_TEXT_KEY     = f"{APP_NS}_llm_text"
        LLM_PENDING_CLR  = f"{APP_NS}_llm_text_pending_clear"
        LLM_RESULT_KEY   = f"{APP_NS}_llm_result"
        LLM_LAST_QUERY   = f"{APP_NS}_llm_last_query"

        llm_cache = st.session_state.setdefault("llm_rumor_cache", {})

        # Clear requested last run? do it BEFORE rendering widget
        if st.session_state.get(LLM_PENDING_CLR):
            st.session_state[LLM_TEXT_KEY] = ""
            st.session_state.pop(LLM_PENDING_CLR, None)

        st.caption("Enter statements or rumors to verify with the LLM (one per line).")

        with st.form(APP_FORM_KEY, clear_on_submit=False):
            llm_text = st.text_area(
                "Rumor(s) to verify",
                value=st.session_state.get(LLM_TEXT_KEY, ""),
                key=LLM_TEXT_KEY,
                help="Examples: 'drink seawater' (False), 'drink water' (True), 'taping windows' (Misleading).",
            )
            colA, colB, _ = st.columns([1, 1, 6])
            with colA:
                submit_check = st.form_submit_button("Check with LLM")
            with colB:
                submit_clear = st.form_submit_button("Clear")

        # Handle Clear
        if submit_clear:
            st.session_state.pop(LLM_RESULT_KEY, None)
            st.session_state.pop(LLM_LAST_QUERY, None)
            st.session_state[LLM_PENDING_CLR] = True
            st.rerun()

        # Normalize input
        items = [line.strip() for line in (llm_text or "").splitlines() if line.strip()]
        key_joined = "\n".join(items)

        # Handle Check
        if submit_check:
            if not items:
                st.session_state.pop(LLM_RESULT_KEY, None)
                st.session_state[LLM_LAST_QUERY] = ""
            else:
                if key_joined in llm_cache:
                    res = llm_cache[key_joined]
                else:
                    res = verify_items_with_llm(items)
                    llm_cache[key_joined] = res
                st.session_state[LLM_RESULT_KEY] = res
                st.session_state[LLM_LAST_QUERY] = key_joined

        # Render result
        llm_live = st.session_state.get(LLM_RESULT_KEY)
        if not items and not llm_live:
            st.info("Type something and click **Check with LLM**.")
        elif not llm_live:
            st.info("Click **Check with LLM** to verify.")
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
                    st.error("API key invalid/restricted. Set GOOGLE_API_KEY for local dev.")
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
    if advisory:
        st.json(advisory)
        dbg = result.get("debug") or {}
        ui_path = os.path.join(ROOT, "data", "sample_advisory.json")
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
        f"Analyzer: {_fmt_ms(timings.get('watcher_ms_analyze'))} ms" + (f" | ERROR: {errors['analyzer']}" if 'analyzer' in errors else ""),
        f"Planner:  {_fmt_ms(timings.get('planner_ms'))} ms"  + (f" | ERROR: {errors['planner']}" if 'planner' in errors else ""),
        f"Parallel: {_fmt_ms(timings.get('parallel_ms'))} ms",
        f"Total:    {_fmt_ms(timings.get('total_ms'))} ms (ran at {st.session_state.get('last_run', '—')})",
    ]
    if 'explainer_ms' in timings:
        status_lines.insert(1, f"Explainer: {_fmt_ms(timings.get('explainer_ms'))} ms")
    st.code("\n".join(status_lines), language="text")

with st.expander("Advisory (details)", expanded=False):
    if advisory:
        st.json(advisory)

        # NEW: show exactly what the watcher parsed from JSON
        advisory_raw = result.get("advisory_raw")
        if advisory_raw is not None:
            st.caption("Raw advisory as parsed:")
            st.json(advisory_raw)

        # NEW: show exact on-disk file contents (first 1000 chars)
        ui_path = os.path.join(ROOT, "data", "sample_advisory.json")
        try:
            with open(ui_path, "r", encoding="utf-8") as f:
                raw_text_preview = f.read(1000)
            st.caption("On-disk file preview (first 1000 chars):")
            st.code(raw_text_preview, language="json")
        except Exception as e:
            st.caption(f"Could not read file preview: {e}")

        # Existing debug lines…
        dbg = result.get("debug") or {}
        wpath = dbg.get("advisory_path")
        wsha  = dbg.get("advisory_sha256")
        st.caption(f"UI file:  {ui_path}")
        if wpath:
            st.caption(f"Watcher file: {wpath}")
        if wsha:
            st.caption(f"Watcher content hash (sha256, first 12): {wsha[:12]}")

        # NEW: show radius source + raw value
        src = dbg.get("advisory_radius_source")
        raw_val = dbg.get("advisory_radius_raw_value")
        if src:
            st.caption(f"radius_km source: {src} (raw value: {raw_val})")

        # Keep your mtime/hash/freshness lines…
        path = ui_path
        st.caption(f"File: {path}")
        try:
            st.caption(f"Last modified: {datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception:
            pass
        sig = st.session_state.get(f"{APP_NS}_adv_sig")
        if sig:
            st.caption(f"Content hash (sha256, first 12): {sig[:12]}")

        # Show the exact prompt the AI used (you already have this)
        if dbg.get("explainer_prompt"):
            st.caption("Explainer prompt (exact):")
            st.code(dbg["explainer_prompt"], language="text")
    else:
        st.caption("No advisory data.")
