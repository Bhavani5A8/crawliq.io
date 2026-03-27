"""
ollama_adapter.py — Run local LLM via Ollama for SEO content generation.

Completely free, no API key, no rate limits.
Runs on your own machine.

Setup:
  1. Install Ollama: https://ollama.com/download
  2. Pull a model:   ollama pull llama3.2   (4GB, good quality)
                  or ollama pull mistral    (4GB, fast)
                  or ollama pull phi3       (2GB, lightweight)
  3. Ollama starts automatically — it serves on http://localhost:11434

Then set USE_OLLAMA=true in environment.
"""

import os, json, re, logging
import urllib.request

logger   = logging.getLogger(__name__)
OLLAMA   = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OL_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")   # change to any installed model


def _ollama_available() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def generate_with_ollama(prompt: str) -> str:
    """
    Send prompt to local Ollama instance, return text response.
    Falls back to empty string on any failure.
    """
    if not _ollama_available():
        logger.warning("Ollama not running — start with: ollama serve")
        return ""

    payload = json.dumps({
        "model":  OL_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.15,
            "num_predict": 800,
        }
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data.get("response", "")
    except Exception as exc:
        logger.error("Ollama call failed: %s", exc)
        return ""
