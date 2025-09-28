# agents/ai_planner.py
from __future__ import annotations

"""
AI Planner Agent: picks the nearest OPEN shelter and estimates ETA.
Exports:
    build_planner_agent() -> LlmAgent
"""

from google.adk.agents.llm_agent import LlmAgent
from google.genai import types

__all__ = ["build_planner_agent"]


# Tool for precise distance calculations (called by the LLM)
def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (km) between (lat1,lon1) and (lat2,lon2)."""
    import math
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def build_planner_agent() -> LlmAgent:
    """
    INPUT (as a plain-text prompt you construct upstream):
      zip_lat=<float> zip_lon=<float>
      shelters_json=<compact JSON array/list of shelters>

    Each shelter item typically has: {"name": "...", "lat": <float>, "lon": <float>, "open": true|false}
    or {"status": "open"|"closed"|...}.

    OUTPUT (single-line JSON only):
      {"name":"...", "lat":<float>, "lon":<float>, "distance_km":<float>, "eta_min":<int>}

    If there is NO open shelter:
      {"name":"", "lat":0, "lon":0, "distance_km":-1, "eta_min":-1}
    """
    instruction = r"""
You are a planner that selects the nearest OPEN shelter.

FACTS will be provided as:
zip_lat=<float> zip_lon=<float>
shelters_json=<JSON array>

RULES:
- A shelter is OPEN if ("open": true) OR (status == "open", case-insensitive).
- Use the tool distance_km(lat1,lon1,lat2,lon2) for all distance calculations.
- Choose the nearest OPEN shelter to (zip_lat, zip_lon).
- Estimate ETA minutes with average speed 40 km/h:
  eta_min = round( (distance_km / 40) * 60 )
- Return ONLY a single-line JSON object with keys: name, lat, lon, distance_km, eta_min.
- No extra text before or after JSON.

If no open shelter exists, return exactly:
{"name":"", "lat":0, "lon":0, "distance_km":-1, "eta_min":-1}
"""

    return LlmAgent(
        model="gemini-2.0-flash",
        name="ai_planner_agent",
        instruction=instruction,
        include_contents="none",
        tools=[distance_km],
        generate_content_config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=256,
        ),
    )
