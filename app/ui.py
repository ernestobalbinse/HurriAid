import streamlit as st
from datetime import datetime

st.set_page_config(page_title="HurriAid", layout="wide")

# --- Sidebar ---
st.sidebar.title("HurriAid")
zip_code = st.sidebar.text_input("Enter ZIP code", value="33101")
offline_mode = st.sidebar.toggle("Offline Mode", value=True)
update_now = st.sidebar.button("Update Now")

st.sidebar.caption(
    "Tip: This starter is offline-only.\n"
    "We'll plug in real agents and data next."
)

# --- Header / Status ---
st.title("HurriAid")
st.write(f"Last opened: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if update_now:
    st.toast("Update requested — agents will run here in the next step.")

# --- Status Metrics ---
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Advisory", "—")
with col2:
    st.metric("Location Risk", "LOW")  # placeholder
with col3:
    st.metric("Nearest Shelter", "—")
with col4:
    st.metric("ETA (min)", "—")

# --- Panels (placeholders for now) ---
st.subheader("Advisory")
st.info("Using sample_advisory.json (to be added) — not wired yet.")

st.subheader("Shelters")
st.info("Using shelters.json (to be added) — not wired yet.")

st.subheader("Risk")
st.write(f"ZIP: **{zip_code}** | Offline: **{offline_mode}**")
st.success("Risk heuristic placeholder: showing **LOW** for starter.")

st.subheader("Route")
st.info("Planner will compute nearest open shelter and ETA here.")

st.subheader("Checklist")
st.write(
    "- Water (3 days)\n"
    "- Non-perishable food\n"
    "- Medications\n"
    "- Flashlight & batteries\n"
    "- First aid kit\n"
    "- Important documents in a waterproof bag"
)

st.subheader("Agent Status")
st.code(
    "Watcher: idle\n"
    "Analyzer: idle\n"
    "Planner: idle\n"
    "Communicator: idle\n"
    "(We'll show real timings later.)",
    language="text",
)
