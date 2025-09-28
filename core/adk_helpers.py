# core/adk_helpers.py
from __future__ import annotations
from typing import Optional, List, Tuple
import asyncio, inspect

# ADK + GenAI (ADK path)
try:
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    _ADK_OK = True
except Exception:
    _ADK_OK = False


def _ensure_session_sync(session_service: "InMemorySessionService",
                         app_name: str, user_id: str, session_id: str) -> None:
    """
    Create the ADK session, awaiting if the API is async on this version.
    Safe to call from synchronous code.
    """
    try:
        res = session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id)
        if inspect.isawaitable(res):
            # No loop in Streamlit main thread: use asyncio.run; else, make a temporary loop.
            try:
                asyncio.run(res)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(res)
                finally:
                    loop.close()
    except Exception:
        # If it already exists or the API signature changed, ignore; Runner will error if truly missing.
        pass


def run_llm_agent_text(agent, prompt: str,
                       app_name: str = "hurri_aid",
                       user_id: str = "ui_user",
                       session_id: str = "sess_text") -> Optional[str]:
    """
    Minimal: run an LLM agent and return the final text (or None).
    """
    text, _events, _err = run_llm_agent_text_debug(
        agent, prompt, app_name=app_name, user_id=user_id, session_id=session_id
    )
    return text


def run_llm_agent_text_debug(agent, prompt: str,
                             app_name: str = "hurri_aid",
                             user_id: str = "ui_user",
                             session_id: str = "sess_text_dbg") -> Tuple[Optional[str], List[str], Optional[str]]:
    """
    Debug-friendly: returns (final_text, event_summaries, error_str).
    Summaries look like: "final=True content=True preview='...'"
    """
    if not _ADK_OK:
        return None, [], "ADK_NOT_AVAILABLE"

    try:
        session_service = InMemorySessionService()
        # IMPORTANT: ensure the session exists for the exact session_id we will use with Runner.run
        _ensure_session_sync(session_service, app_name=app_name, user_id=user_id, session_id=session_id)

        runner = Runner(agent=agent, app_name=app_name, session_service=session_service)

        content = types.Content(role="user", parts=[types.Part(text=prompt)])
        events = runner.run(user_id=user_id, session_id=session_id, new_message=content)

        final_text: Optional[str] = None
        summaries: List[str] = []
        saw_any = False

        for ev in events:
            saw_any = True
            try:
                is_final = bool(ev.is_final_response())
            except Exception:
                is_final = False

            has_content = False
            preview = ""
            try:
                if getattr(ev, "content", None) and getattr(ev.content, "parts", None):
                    has_content = True
                    for p in ev.content.parts:
                        txt = getattr(p, "text", None)
                        if isinstance(txt, str) and txt:
                            if is_final and final_text is None:
                                final_text = txt.strip()
                            if not preview:
                                preview = txt[:120].replace("\n", " ")
                            break
            except Exception:
                pass

            summaries.append(f"final={is_final} content={has_content} preview='{preview}'")

        if not saw_any and final_text is None:
            return None, summaries, "NO_EVENTS"

        return final_text, summaries, None

    except Exception as e:
        return None, [], f"{type(e).__name__}: {e}"
