# core/adk_helpers.py
from __future__ import annotations

import asyncio
import threading
from typing import Any, List, Tuple, Optional

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# Global session service for the whole app
_SESSION = InMemorySessionService()

def _run_coro_blocking(coro):
    """
    Run an async coroutine from sync code, even if we're already inside a running loop.
    Uses a dedicated thread+event loop when necessary.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop: safe to asyncio.run
        return asyncio.run(coro)

    if loop.is_running():
        # We are already inside an event loop (e.g., Streamlit/ADK threads)
        result_holder: dict = {}
        exc_holder: dict = {}

        def _worker():
            try:
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                result_holder["value"] = new_loop.run_until_complete(coro)
                new_loop.close()
            except Exception as e:
                exc_holder["error"] = e

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join()
        if "error" in exc_holder:
            raise exc_holder["error"]
        return result_holder.get("value")
    else:
        # Edge case: have a loop object but it's not running
        return loop.run_until_complete(coro)

def _maybe_call(meth, **kwargs):
    """
    Call a session service method that might be sync or async; await when needed.
    """
    res = meth(**kwargs)
    if asyncio.iscoroutine(res):
        return _run_coro_blocking(res)
    return res

def ensure_session(app_name: str, user_id: str, session_id: str) -> None:
    """
    Ensure (app_name, user_id, session_id) exists in the InMemorySessionService,
    across ADK versions where get_session/create_session may be async.
    """
    # Try to fetch existing
    get_sess = getattr(_SESSION, "get_session", None)
    found = None
    if callable(get_sess):
        try:
            found = _maybe_call(get_sess, app_name=app_name, user_id=user_id, session_id=session_id)
        except Exception:
            found = None

    if found:
        return

    # Create if missing
    create = getattr(_SESSION, "create_session", None)
    if not callable(create):
        # Fallback: older builds may only expose create_session via other name; in practice it's present.
        raise RuntimeError("InMemorySessionService.create_session() not available.")
    _maybe_call(create, app_name=app_name, user_id=user_id, session_id=session_id)

def run_llm_agent_text_debug(
    agent: Any,
    prompt: str,
    app_name: str,
    user_id: str,
    session_id: str,
) -> Tuple[Optional[str], List[str], Optional[str]]:
    """
    Synchronously run an ADK LlmAgent and return (text, events, error).

    - Ensures session exists (handles async/sync APIs).
    - Iterates over ALL events; captures last seen text as fallback.
    - Retries ONCE if ADK throws 'Session not found' from the runner thread.
    """
    def _invoke() -> Tuple[Optional[str], List[str], Optional[str]]:
        ensure_session(app_name, user_id, session_id)
        runner = Runner(agent=agent, app_name=app_name, session_service=_SESSION)

        content = types.Content(role="user", parts=[types.Part(text=prompt)])
        try:
            events_iter = runner.run(user_id=user_id, session_id=session_id, new_message=content)
        except Exception as e:
            return None, [], f"GENAI_ERROR {type(e).__name__}: {e}"

        final_text: Optional[str] = None
        last_text: Optional[str] = None
        events_dump: List[str] = []
        try:
            for ev in events_iter:
                etype = getattr(ev, "type", ev.__class__.__name__)
                events_dump.append(str(etype))

                ev_content = getattr(ev, "content", None)
                if ev_content and getattr(ev_content, "parts", None):
                    for part in ev_content.parts:
                        t = getattr(part, "text", None)
                        if isinstance(t, str) and t.strip():
                            last_text = t

                if getattr(ev, "is_final_response", lambda: False)():
                    if ev_content and getattr(ev_content, "parts", None):
                        t = getattr(ev_content.parts[0], "text", None)
                        if isinstance(t, str) and t.strip():
                            final_text = t
        except ValueError as ve:
            # Common ADK thread error bubbles up here sometimes
            msg = str(ve)
            if "Session not found" in msg:
                return None, events_dump, "SESSION_MISSING"
            return None, events_dump, f"GENAI_ERROR ValueError: {msg}"
        except Exception as e:
            return None, events_dump, f"GENAI_ERROR {type(e).__name__}: {e}"

        if not final_text:
            final_text = last_text

        if not final_text or not final_text.strip():
            return None, events_dump, "NO_TEXT"

        return final_text, events_dump, None

    # First attempt
    text, events, err = _invoke()
    if err == "SESSION_MISSING":
        # Retry once after re-ensuring session
        ensure_session(app_name, user_id, session_id)
        text, events2, err2 = _invoke()
        # keep all event types we saw
        events = events + ["--- retry ---"] + events2
        return text, events, err2

    return text, events, err
