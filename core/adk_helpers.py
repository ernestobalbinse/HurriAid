# core/adk_helpers.py
from __future__ import annotations
import threading
from typing import Optional, Tuple, List, Any

# ADK / GenAI (assumed present for this project)
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.events import Event
from google.genai import types as genai_types

# One shared, thread-safe in-memory session service for the whole app.
_SESSION_LOCK = threading.Lock()
_SESSION: Optional[InMemorySessionService] = None

def _get_session_service() -> InMemorySessionService:
    """Return a singleton InMemorySessionService instance."""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = InMemorySessionService()
    return _SESSION

def ensure_session(app_name: str, user_id: str, session_id: str) -> None:
    """
    Make sure a session exists for (app_name, user_id, session_id).
    We rely on ADK's synchronous helpers and fail fast if they're missing.
    """
    ss = _get_session_service()
    try:
        sess = ss.get_session_sync(app_name=app_name, user_id=user_id, session_id=session_id)
    except AttributeError as e:
        raise RuntimeError("ADK requires get_session_sync; update ADK to a recent version.") from e

    if not sess:
        try:
            ss.create_session_sync(app_name=app_name, user_id=user_id, session_id=session_id)
        except AttributeError as e:
            raise RuntimeError("ADK requires create_session_sync; update ADK to a recent version.") from e

def run_llm_agent_text_debug(
    agent: Any,
    prompt: str,
    app_name: str,
    user_id: str,
    session_id: str,
) -> Tuple[Optional[str], List[Event], Optional[str]]:
    """
    Run a single LlmAgent with a user text prompt.

    Returns:
      final_text: model's top-level text (first part) or None if unavailable
      events:     full event stream (useful for debugging/telemetry)
      err:        error string if something went wrong, else None
    """
    ensure_session(app_name, user_id, session_id)
    ss = _get_session_service()
    runner = Runner(agent=agent, app_name=app_name, session_service=ss)

    content = genai_types.Content(role="user", parts=[genai_types.Part(text=prompt)])
    events: List[Event] = []
    final_text: Optional[str] = None
    err: Optional[str] = None

    try:
        for ev in runner.run(user_id=user_id, session_id=session_id, new_message=content):
            events.append(ev)
            if ev.is_final_response() and ev.content:
                parts = ev.content.parts or []
                if parts:
                    # Take the first text part; callers needing more can inspect events.
                    text = getattr(parts[0], "text", None)
                    if isinstance(text, str):
                        final_text = text
    except Exception as e:
        err = f"{type(e).__name__}: {e}"

    return final_text, events, err
