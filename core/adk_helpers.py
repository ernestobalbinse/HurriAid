# core/adk_helpers.py
from __future__ import annotations
from typing import Optional, List, Tuple

# Try ADK + GenAI imports once and cache a flag
try:
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    _ADK_OK = True
except Exception:
    _ADK_OK = False


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
        session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id)
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
