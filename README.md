# HurriAid

A small, AI‑first hurricane readiness demo built with Streamlit. It reads a live‑like advisory file, classifies risk with Gemini, plans a route to the nearest open shelter, and generates a short, risk‑aware prep checklist. It also includes a Rumor Check tool that verifies hurricane claims.

> **Design note:** This project assumes AI is always available. Offline/fallback flows are intentionally removed; failures surface clearly so you can fix configuration rather than silently degrade features.

---

## Quick start

```bash
# 1) Python 3.10+ recommended
python -V

# 2) Create & activate a virtual env (example: venv)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3) Install dependencies
pip install --upgrade pip
pip install streamlit pydeck pgeocode python-dotenv google-genai google-adk

# 4) Configure environment (AI Studio API key)
# Create a .env file in the repo root with:
# GOOGLE_GENAI_USE_VERTEXAI=FALSE
# GOOGLE_API_KEY=YOUR_API_KEY
# ADK_MODEL_ID=gemini-2.0-flash

# 5) Prepare data files (see samples below)
mkdir -p data
# Put data/sample_advisory.json and data/shelters.json in the data/ folder

# 6) Run the app
streamlit run app/ui.py
```

Open the URL Streamlit prints (usually http://localhost:8501). Use the sidebar to enter a ZIP (e.g., `33101`).

---

## Data files

HurriAid reads two JSON files from `data/`:

### 1) `data/sample_advisory.json`
Minimal example:

```json
{
  "center": { "lat": 25.77, "lon": -80.19 },
  "radius_km": 120,
  "category": "CAT1",
  "issued_at": "2025-09-28T12:30:00Z",
  "active": true
}
```

Fields:
- `center.lat`, `center.lon`: storm center (degrees)
- `radius_km`: advisory radius in km
- `category`: `TS`, `CAT1` … `CAT5`
- `issued_at`: ISO‑8601 time used for “freshness” badges
- `active`: `true` keeps the watcher running; `false` pauses risk work

### 2) `data/shelters.json`
Array form (preferred):

```json
[
  { "name": "Civic Center", "lat": 25.79, "lon": -80.22, "open": true },
  { "name": "North High School", "lat": 25.90, "lon": -80.28, "open": false }
]
```

or wrapped form:

```json
{ "shelters": [ { "name": "Civic Center", "lat": 25.79, "lon": -80.22, "open": true } ] }
```

Fields:
- `name` (string), `lat` (float), `lon` (float)
- `open` (bool) or `status` set to `"open"`

> The UI auto‑detects file changes using mtime and SHA‑256. Edit and save—no server restart needed.

---

## Environment & configuration

Create a `.env` in the repo root (loaded by the app). Common variables:

```
# Required to call Gemini (AI Studio API key)
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GOOGLE_API_KEY=your_api_key_here

# Model selection for ADK agents
ADK_MODEL_ID=gemini-2.0-flash
ADK_USER_ID=local_user

# Rumor checker tuning (optional)
HURRIAID_MODEL=gemini-2.0-flash
HURRIAID_LLM_TIMEOUT=25
HURRIAID_LLM_RETRIES=3
```

> **Tip:** If you run into API‑key errors, confirm the key is an **AI Studio** key (not Vertex) and that environment variables are visible to the Streamlit process.

---

## How it works (architecture)

**Request flow** for a ZIP:

1. **Watcher (`agents/watcher.py`)**
   - Loads `sample_advisory.json`
   - Resolves ZIP → `(lat, lon)` via `pgeocode`
   - Computes distance to storm center
   - Calls Gemini (via ADK `LlmAgent`) to classify risk **and** return a one‑sentence “why”
   - Produces a `state` object with advisory, analysis, explainer, and timing

2. **Parallel pipeline (`agents/parallel_pipeline.py`)**
   - **Checklist (`agents/ai_checklist.py`)**: prompts Gemini to output a short, risk‑sized JSON checklist; items are cleaned/deduped
   - **Planner (`agents/ai_planner.py`)**: loads open shelters, picks nearest by Haversine, estimates ETA based on category

3. **Coordinator (`agents/coordinator.py`)**
   - Orchestrates 1) watcher then 2) parallel work, merges outputs, and totals timings

4. **UI (`app/ui.py`)**
   - Streamlit layout: Risk summary, Checklist, Map (advisory circle + points), Route card, and **Gemini Rumor Check** form
   - Auto‑refreshes when the data files change or the ZIP changes

**Rumor Check (`agents/verifier_llm.py`)**
- Takes multiline input, calls Gemini directly with `google-genai`, and returns structured verdicts: `TRUE|FALSE|MISLEADING|CAUTION`
- Notes are human‑readable and de‑shouted, with an overall roll‑up

---

## Modules (overview)

- `app/ui.py` – Streamlit app shell and interactive UI
- `agents/coordinator.py` – Orchestrates watcher → parallel pipeline
- `agents/watcher.py` – Advisory load, ZIP resolve, distance compute, AI risk + “why”
- `agents/parallel_pipeline.py` – Runs checklist + route planner
- `agents/ai_checklist.py` – Risk‑aware, size‑bounded JSON checklist
- `agents/ai_planner.py` – Nearest open shelter + ETA
- `agents/ai_risk.py`, `agents/ai_explainer.py` – Alternative/specialized ADK agents
- `agents/ai_communicator.py` – Example of output_schema‑validated checklist agent
- `agents/verifier_llm.py` – Rumor checker using `google-genai`
- `core/adk_helpers.py` – ADK session/runner helpers
- `core/parallel_exec.py` – Lightweight threaded executor (utility)
- `core/shelters.py` – Robust shelter file reader + status helpers
- `core/ui_helpers.py` – Badges & “freshness” utility
- `core/utils.py` – History load/append utilities
- `tools/zip_resolver.py` – ZIP → `(lat, lon)` helpers
- `tools/geo.py` – Haversine + advisory circle polygon

---

## Usage notes (what you’ll see)

- **Risk card**: ZIP, risk level, distance, and whether you’re inside the advisory radius, plus a one‑sentence “Why”.
- **Checklist**: 0–12 concise items sized to risk; no duplicates or fluff.
- **Map**: Advisory circle, your ZIP point, nearest open shelter.
- **Route**: Click‑through to Google Maps with the shelter destination.
- **Rumor Check**: Paste multiple lines (one rumor per line) and click **Check with Gemini**.

Sidebar toggles:
- *Show Map* – render/hide map
- *Show Verifier* – render/hide the rumor checker form

---

## Troubleshooting

- **“AI Studio key missing” banner at top**
  - Add `GOOGLE_API_KEY` in `.env` and ensure Streamlit inherits your environment.
- **ADK import errors** (`google.adk`)
  - Install or update `google-adk`. If your environment uses a different package name, install that provider’s ADK and keep the same imports.
- **ZIP marked invalid / unknown**
  - `pgeocode` must be installed. Some ZIPs may be missing from local datasets; try another.
- **Shelters not found**
  - Ensure `data/shelters.json` exists and is valid JSON. Use the examples above.
- **Model timeouts or overload**
  - The UI will show a friendly warning. You can tune `HURRIAID_LLM_TIMEOUT` and `HURRIAID_LLM_RETRIES` for the rumor checker.
- **No map or route**
  - If the ZIP is invalid, those panels are hidden. Fix the ZIP first.

---

## Development tips

- The UI shows a compact **Agent Status** panel with per‑phase timings.
- The watcher returns debug blobs (prompts, attempts) under the **Storm Details / Agent Status** expanders.
- The UI history keeps your last 12 runs in memory; see `core/utils.py` for persisted history helpers.
- Edit `data/sample_advisory.json` to simulate movement (center/radius/category) and staleness (`issued_at`).

---

## Safety & scope

HurriAid is a demo and not a substitute for official guidance. Always follow local emergency managers and the National Hurricane Center. The app intentionally avoids medical or legal advice and limits notes to short, plain‑language tips.

---

## License

Choose a license appropriate for your use (e.g., MIT, Apache‑2.0, or proprietary). Add a `LICENSE` file at the repo root.

