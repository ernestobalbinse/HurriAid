# core/llm_gemini.py
from __future__ import annotations
import os
import google.generativeai as genai

class GeminiNotConfigured(RuntimeError):
    pass

def get_gemini(model: str = "gemini-1.5-pro"):
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise GeminiNotConfigured("GOOGLE_API_KEY is not set in the environment.")
    try:
        genai.configure(api_key=api_key)
        return genai.GenerativeModel(model)
    except Exception as e:
        raise GeminiNotConfigured(f"Failed to configure Gemini: {e}")

def generate_text(prompt: str, model: str = "gemini-1.5-pro") -> str:
    """Simple text generation; returns the model's text or raises GeminiNotConfigured."""
    g = get_gemini(model)
    resp = g.generate_content(prompt)
    # Handle both list/parts and .text convenience
    if hasattr(resp, "text") and resp.text:
        return resp.text
    # Fall back to raw structure if needed
    return str(resp)
