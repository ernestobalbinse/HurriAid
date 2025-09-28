# core/adk_helpers.py
from __future__ import annotations
import threading
from typing import Optional, Tuple, List, Any

# ADK / GenAI
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.events import Event
from google.genai import types as genai_types

# ---------- singletons ----------
_SESSION_LOCK = threading.Lock()
_SESSION: Optional[InMemorySessionService] = None

def _get_session_service() -> InMemorySessionService:
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = InMemorySessionService()
    return _SESSION

def ensure_session(app_name: str, user_id: str, session_id: str) -> None:
    ss = _get_session_service()
    # ADK's InMemorySessionService methods are async; use its sync helpers if present:
    # Fallback: tiny compatibility wrapper
    try:
        sess = ss.get_session_sync(app_name=app_name, user_id=user_id, session_id=session_id)
    except AttributeError:
        # Provide sync behavior using internal store if necessary
        try:
            # available in newer versions
            sess = ss.get_session(app_name=app_name, user_id=user_id, session_id=session_id)  # type: ignore
        except Exception:
            sess = None
    if not sess:
        try:
            ss.create_session_sync(app_name=app_name, user_id=user_id, session_id=session_id)
        except AttributeError:
            # fallback to async (best-effort)
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(ss.create_session(app_name=app_name, user_id=user_id, session_id=session_id))  # type: ignore
            finally:
                loop.close()

def run_llm_agent_text_debug(
    agent,
    prompt: str,
    app_name: str,
    user_id: str,
    session_id: str,
) -> Tuple[Optional[str], List[Event], Optional[str]]:
    """
    Run a single LlmAgent with a text prompt. Returns (final_text, events, error).
    The agent should have its .instruction set already.
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
                if parts and getattr(parts[0], "text", None):
                    final_text = parts[0].text
    except Exception as e:
        err = f"{type(e).__name__}: {e}"

    return final_text, events, err
